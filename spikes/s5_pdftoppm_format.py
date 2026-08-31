"""pdftoppm の出力形式（png / ppm / jpeg）の速度と画素一致を比較する（issue #50）。

issue #50 で「-png は -ppm/-jpeg の約7〜8倍遅い」と実測された。展開は 1 ページごとに
必ず通る経路なので、月 3,000〜6,000 画像なら概算 8 時間超をここだけで使う。

ただし本ツールの設計は「誤った値を出すコストのほうが大きい」であり、速度のために
OCR 精度のリスクを取る取引はできない。そこで API を 1 回も叩かずに精度影響を決着させる:

    -png と -ppm はどちらも可逆（ロスレス）ラスタライズなので、画素が一致すれば
    Vision へ渡る入力は同一になる。入力が同一なら OCR 結果も同一である。

つまり ppm については「画素一致」を示せば精度影響ゼロが証明できる。jpeg は非可逆なので
画素が一致せず、この方法では安全性を示せない（実送信での比較が要る＝課金が発生する）。

使い方:
    .venv\\Scripts\\python.exe spikes\\s5_pdftoppm_format.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "core"))

from chouhyo_ocr.ingest import pdftoppm_path  # noqa: E402

PDF = REPO / "samples" / "帳票抽出検証用2026-08-24.pdf"
DPI = 300  # templates/chouhyo-v1.json の render_dpi
REPEAT = 3  # 1回だけだとディスクキャッシュの影響を受ける

# (ラベル, pdftoppm のフラグ, 出力拡張子, 可逆か)
FORMATS = [
    ("png (現行)", ["-png"], ".png", True),
    ("ppm", [], ".ppm", True),
    ("jpeg", ["-jpeg"], ".jpg", False),
]


def expand(out_dir: Path, flags: list[str], label: str) -> tuple[float, list[Path], str]:
    """pdftoppm を実行し、経過秒数と生成ファイルを返す。"""
    exe = pdftoppm_path()
    prefix = out_dir / "p"
    args = [str(exe), "-r", str(DPI), *flags, str(PDF), str(prefix)]
    t0 = time.perf_counter()
    proc = subprocess.run(args, capture_output=True, timeout=600)
    elapsed = time.perf_counter() - t0
    if proc.returncode != 0:
        return elapsed, [], f"FAILED rc={proc.returncode}"
    files = sorted(p for p in out_dir.iterdir() if p.is_file())
    return elapsed, files, ""


def main() -> int:
    if not PDF.exists():
        print(f"サンプル PDF が無い: {PDF}")
        return 1

    print(f"PDF        : {PDF.name}")
    print(f"解像度     : {DPI} dpi")
    print(f"試行回数   : {REPEAT}（最良値を採用。ディスクキャッシュの影響を減らす）")
    print()

    results: dict[str, dict] = {}

    for label, flags, ext, lossless in FORMATS:
        times = []
        pages: list[Path] = []
        keep_dir = Path(tempfile.mkdtemp(prefix=f"s5_{ext.strip('.')}_"))
        for i in range(REPEAT):
            work = keep_dir if i == REPEAT - 1 else Path(tempfile.mkdtemp(prefix="s5_tmp_"))
            elapsed, files, err = expand(work, flags, label)
            if err:
                print(f"{label:12s}: {err}")
                break
            times.append(elapsed)
            if i == REPEAT - 1:
                pages = files
        if not times:
            continue
        total_bytes = sum(p.stat().st_size for p in pages)
        results[label] = {
            "best": min(times),
            "times": times,
            "pages": pages,
            "bytes": total_bytes,
            "lossless": lossless,
        }
        print(f"{label:12s}: 最良 {min(times):6.2f}s  "
              f"(全試行 {', '.join(f'{t:.2f}' for t in times)})  "
              f"{len(pages)}ページ  {total_bytes/1_048_576:6.2f}MB")

    if "png (現行)" not in results:
        print("\n現行形式の展開に失敗したため比較できない")
        return 1

    base = results["png (現行)"]
    print()
    print("=== 速度比（現行 png を 1.00 とする）===")
    for label, r in results.items():
        print(f"  {label:12s}: {base['best']/r['best']:5.2f}x 速い  "
              f"容量 {r['bytes']/base['bytes']:5.2f}x")

    print()
    print("=== 画素一致（現行 png との比較）===")
    print("可逆形式で画素が完全一致するなら、Vision へ渡る入力は同一 → OCR 結果も同一。")
    print("この場合、精度検証のための API 送信は不要（課金ゼロで安全性を示せる）。")
    print()

    verdict: dict[str, str] = {}
    for label, r in results.items():
        if label == "png (現行)":
            continue
        if len(r["pages"]) != len(base["pages"]):
            verdict[label] = f"ページ数が違う（{len(r['pages'])} vs {len(base['pages'])}）"
            print(f"  {label:12s}: {verdict[label]}")
            continue

        identical = True
        worst_maxdiff = 0
        worst_ratio = 0.0
        shape_mismatch = ""
        for pa, pb in zip(base["pages"], r["pages"]):
            a = np.asarray(Image.open(pa).convert("RGB"), dtype=np.int16)
            b = np.asarray(Image.open(pb).convert("RGB"), dtype=np.int16)
            if a.shape != b.shape:
                shape_mismatch = f"寸法が違う {a.shape} vs {b.shape}"
                identical = False
                break
            diff = np.abs(a - b)
            maxd = int(diff.max())
            if maxd != 0:
                identical = False
            worst_maxdiff = max(worst_maxdiff, maxd)
            worst_ratio = max(worst_ratio, float((diff.any(axis=2)).mean()))

        if shape_mismatch:
            verdict[label] = shape_mismatch
            print(f"  {label:12s}: {shape_mismatch}")
        elif identical:
            verdict[label] = "完全一致"
            print(f"  {label:12s}: 完全一致（全ページ・全画素で差分 0）"
                  f"{'  ← 可逆形式なので精度影響なし' if r['lossless'] else ''}")
        else:
            verdict[label] = f"差分あり（最大 {worst_maxdiff}/255・{worst_ratio*100:.2f}% の画素）"
            print(f"  {label:12s}: 差分あり  最大差 {worst_maxdiff}/255  "
                  f"差のある画素 {worst_ratio*100:.2f}%")

    print()
    print("=== 判断材料 ===")
    for label, r in results.items():
        if label == "png (現行)":
            continue
        v = verdict.get(label, "未比較")
        speed = base["best"] / r["best"]
        size = r["bytes"] / base["bytes"]
        if v == "完全一致" and r["lossless"]:
            print(f"  {label}: 切替は精度リスクなし。{speed:.1f}倍速・容量 {size:.1f}倍。"
                  f"ディスク使用量とのトレードオフのみで判断できる")
        elif r["lossless"]:
            print(f"  {label}: 可逆形式のはずが画素が一致しない（{v}）。原因を特定するまで切り替えない")
        else:
            print(f"  {label}: 非可逆形式で画素が一致しない（{v}）。"
                  f"精度影響を示すには実送信での比較が要る（課金が発生する）ため、"
                  f"この spike では安全性を判定できない")

    print()
    print(f"注: 生成物は一時ディレクトリに残している（手動確認用）。不要なら削除してよい。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
