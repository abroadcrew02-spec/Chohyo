"""ppm 展開 + Pillow での PNG 保存が、現行の pdftoppm -png を置き換えられるか測る（issue #50）。

s5 で分かったこと:
  - pdftoppm -ppm は -png の 10.5 倍速く、画素は現行 png と完全一致する（可逆）
  - ただし ppm は容量が 10.9 倍（25MB/頁）で、3,000 頁なら 75GB になり現実的でない

遅さの原因は poppler 内蔵の PNG エンコーダなので、そこだけ Pillow に置き換える:
  pdftoppm -ppm → Pillow で読む → compress_level=1 で PNG 保存 → ppm を捨てる

align.py が整列画像の保存で compress_level=1 を選んだのと同じ判断
（実測コメント: level 6 で 0.35s/枚 → level 1 で 0.22s/枚）。

この案が成立する条件は 2 つ。両方を実測で確かめる:
  1. 出来上がった PNG が現行 png と画素完全一致すること（精度影響ゼロ）
  2. 合計時間が現行より十分速いこと
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
DPI = 300
REPEAT = 3
LEVELS = [0, 1, 3, 6]


def run_pdftoppm(out: Path, flags: list[str]) -> float:
    t0 = time.perf_counter()
    p = subprocess.run([str(pdftoppm_path()), "-r", str(DPI), *flags,
                        str(PDF), str(out / "p")], capture_output=True, timeout=600)
    if p.returncode != 0:
        raise SystemExit(f"pdftoppm failed rc={p.returncode}")
    return time.perf_counter() - t0


def main() -> int:
    # 基準: 現行の -png
    base_dir = Path(tempfile.mkdtemp(prefix="s6_base_"))
    base_t = min(run_pdftoppm(Path(tempfile.mkdtemp(prefix="s6_b_")), ["-png"])
                 for _ in range(REPEAT - 1))
    base_t = min(base_t, run_pdftoppm(base_dir, ["-png"]))
    base_pages = sorted(base_dir.glob("*.png"))
    base_bytes = sum(p.stat().st_size for p in base_pages)
    print(f"現行 pdftoppm -png : {base_t:6.2f}s  {len(base_pages)}ページ  "
          f"{base_bytes/1_048_576:6.2f}MB")

    # ppm 展開の時間（Pillow 変換とは分けて測る）
    ppm_dir = Path(tempfile.mkdtemp(prefix="s6_ppm_"))
    ppm_t = min(run_pdftoppm(Path(tempfile.mkdtemp(prefix="s6_p_")), [])
                for _ in range(REPEAT - 1))
    ppm_t = min(ppm_t, run_pdftoppm(ppm_dir, []))
    ppm_pages = sorted(p for p in ppm_dir.iterdir() if p.is_file())
    print(f"pdftoppm -ppm      : {ppm_t:6.2f}s  {len(ppm_pages)}ページ")
    print()
    print("=== ppm → Pillow PNG（compress_level 別）===")
    print(f"{'level':>5}  {'変換':>7}  {'合計':>7}  {'速度比':>7}  {'容量':>9}  {'容量比':>7}  画素一致")

    for lv in LEVELS:
        times = []
        for _ in range(REPEAT):
            out = Path(tempfile.mkdtemp(prefix=f"s6_c{lv}_"))
            t0 = time.perf_counter()
            made = []
            for src in ppm_pages:
                img = Image.open(src)
                img.load()
                dst = out / (src.stem + ".png")
                img.save(dst, format="PNG", compress_level=lv)
                made.append(dst)
            times.append(time.perf_counter() - t0)
            last_out, last_made = out, made
        conv = min(times)
        total = ppm_t + conv
        nbytes = sum(p.stat().st_size for p in last_made)

        identical = len(last_made) == len(base_pages)
        maxd = 0
        if identical:
            for pa, pb in zip(base_pages, sorted(last_made)):
                a = np.asarray(Image.open(pa).convert("RGB"), dtype=np.int16)
                b = np.asarray(Image.open(pb).convert("RGB"), dtype=np.int16)
                if a.shape != b.shape:
                    identical = False
                    break
                d = int(np.abs(a - b).max())
                maxd = max(maxd, d)
                if d != 0:
                    identical = False
        mark = "完全一致" if identical else f"差分あり(最大{maxd})"
        print(f"{lv:>5}  {conv:6.2f}s  {total:6.2f}s  {base_t/total:6.2f}x  "
              f"{nbytes/1_048_576:8.2f}MB  {nbytes/base_bytes:6.2f}x  {mark}")

    print()
    print("判断: 画素完全一致なら Vision への入力が現行と同一 → OCR 結果も同一（精度影響ゼロ）。")
    print("      速度比と容量比だけで採否を決められる。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
