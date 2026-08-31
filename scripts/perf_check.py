# -*- coding: utf-8 -*-
"""性能 NFR の実測（要件 §6.1: 物理メモリ8GB で100枚連続を OOM・停止なく完走）。

保存済み Vision 応答の replay で N ページを実プロセスで処理し、
ピーク RSS・RSS の推移・所要時間・成果物サイズを計測する（API 課金ゼロ）。

計測は2部構成（issue #52 M-14）:
  1. PDF 展開（pdftoppm → PNG）— 1ページごとに必ず通る経路。**入力を .png に
     すると通らない**ため、replay 中心の計測では支配的コストを見落とす
  2. パイプライン全体 — replay で N ページを流し、RSS の推移を見る

実行:
  .venv/Scripts/python.exe scripts/perf_check.py                 # 両方
  .venv/Scripts/python.exe scripts/perf_check.py --only expand   # 展開のみ
  .venv/Scripts/python.exe scripts/perf_check.py --pages 250     # 枚数を変える
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "workdir_build" / "perf"
PAGE = ROOT / "workdir" / "pages" / "sample-1.png"
RESP = ROOT / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
TPL = ROOT / "templates" / "chouhyo-v1.json"


def measure_expand() -> int:
    """PDF 展開の実測（issue #50 の最適化を守るための計測・M-14 (a)）。

    サンプル PDF は .gitignore 済みで環境によっては無い。その場合は skip する
    （計測できないことを黙って PASS にしない）。
    """
    import tempfile

    sys.path.insert(0, str(ROOT / "core"))
    from chouhyo_ocr.ingest import expand

    pdfs = sorted(ROOT.joinpath("samples").glob("*.pdf")) if (ROOT / "samples").is_dir() else []
    if not pdfs:
        print("展開計測: SKIP（samples/ に PDF が無い。.gitignore 済みのため環境依存）")
        return 0
    src = pdfs[0]
    dpi = json.loads(TPL.read_text(encoding="utf-8")).get("render_dpi", 300) if TPL.exists() else 300

    out = Path(tempfile.mkdtemp(prefix="perf_expand_"))
    t0 = time.perf_counter()
    pages = expand(src, dpi, out)
    elapsed = time.perf_counter() - t0
    total_mb = sum(p.stat().st_size for p in pages) / (1024 * 1024)
    shutil.rmtree(out, ignore_errors=True)

    print(f"展開計測: {len(pages)}ページ {elapsed:.2f}s "
          f"({elapsed/len(pages):.2f}s/ページ) 出力 {total_mb:.2f}MB "
          f"({total_mb/len(pages):.2f}MB/ページ) @{dpi}dpi")
    return 0


def measure_pipeline(N: int) -> int:
    # 前提の成果物が無いと base = PAGE.read_bytes() が素の FileNotFoundError で
    # 落ち、purge 直後に「壊れた」と誤読される（レビュー M-19）。何を用意すれば
    # よいかを先に言う
    missing = [p for p in (PAGE, RESP) if not p.exists()]
    if missing:
        print("性能計測に必要な素材がありません:", flush=True)
        for p in missing:
            print(f"  - {p}", flush=True)
        print("先に replay 用の 1 ページ分（サンプル画像と保存済み応答）を"
              "用意してから実行する。purge 後は素材ごと消えている", flush=True)
        return 2
    if BASE.exists():
        shutil.rmtree(BASE)
    inp = BASE / "input"; inp.mkdir(parents=True)
    resp = BASE / "resp"; resp.mkdir()
    base = PAGE.read_bytes()
    for i in range(1, N + 1):
        # 二重取り込み検知に食われないよう内容をユニーク化（IEND 後の1バイト）
        (inp / f"perf{i:03d}.png").write_bytes(base + bytes([i % 250 + 1, i // 250]))
        shutil.copy(RESP, resp / f"perf{i:03d}_p0001.json")
    cfg = BASE / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(BASE / "out"),
        "workdir": str(BASE / "wd"),
        "log_dir": str(BASE / "logs"),
    }), encoding="utf-8")

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    t0 = time.time()
    # stdout は必ずファイルへ流す。PIPE を読まずに待つと 60 ページ規模で
    # パイプバッファが詰まり、コア側の print がブロックしてハングする（実測）
    log_out = open(BASE / "run.out", "wb")
    log_err = open(BASE / "run.err", "wb")
    proc = subprocess.Popen(
        [str(py), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(inp), "--replay", str(resp)],
        cwd=ROOT / "core", stdout=log_out, stderr=log_err)
    ps = psutil.Process(proc.pid)
    peak = 0
    samples: list[int] = []   # RSS の推移（リーク兆候の判定に使う・M-14 (b)）
    while proc.poll() is None:
        try:
            rss = ps.memory_info().rss
            for ch in ps.children(recursive=True):
                try:
                    rss += ch.memory_info().rss
                except psutil.Error:
                    pass
            peak = max(peak, rss)
            samples.append(rss)
        except psutil.Error:
            break
        time.sleep(0.25)
    proc.wait()
    log_out.close(); log_err.close()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print("NG: 終了コード", proc.returncode)
        print((BASE / "run.err").read_text("utf-8", "replace")[-800:])
        return 1
    # summary 行が無い（コアが途中で落ちた等）と next() が素の StopIteration で
    # 落ちる。何が起きたかを言ってから終わる（レビュー LOW）
    summary = next((json.loads(l) for l in
                    (BASE / "run.out").read_text("utf-8").splitlines()
                    if '"summary"' in l), None)
    if summary is None:
        print("NG: コアが summary を出していない。run.out / run.err を確認する")
        print((BASE / "run.err").read_text("utf-8", "replace")[-800:])
        return 1
    outs = sorted((BASE / "out").glob("*.xlsx"))
    if not outs:
        print("NG: 出力 xlsx が生成されていない")
        return 1
    xlsx = outs[-1]
    db = BASE / "wd" / "intermediate.sqlite"
    mb = 1024 * 1024
    print(f"pages={summary['pages']} rows={summary['rows']} "
          f"align_failed={summary['align_failed']}")
    print(f"elapsed={elapsed:.1f}s ({elapsed/N:.2f}s/枚)")
    print(f"peak_rss={peak/mb:.0f}MB  xlsx={xlsx.stat().st_size/mb:.1f}MB  "
          f"db={db.stat().st_size/mb:.1f}MB")

    # RSS の推移。先頭20%と末尾20%の平均を比べる。単調増加ならリークを疑う。
    # ピーク値だけでは「起動直後の一時ピーク」と「じわじわ増える漏れ」を
    # 区別できない（250ページで横ばいを実測済み・issue #52 M-14）
    leak_ok = True
    if len(samples) >= 10:
        k = max(1, len(samples) // 5)
        head = sum(samples[:k]) / k / mb
        tail = sum(samples[-k:]) / k / mb
        growth = (tail - head) / head * 100 if head else 0.0
        leak_ok = growth < 25.0
        print(f"rss_trend: head={head:.0f}MB tail={tail:.0f}MB "
              f"({growth:+.1f}%)  {'横ばい' if leak_ok else 'リークの疑い'}")
    else:
        print("rss_trend: サンプル不足（枚数を増やして再計測する）")

    ok = summary["rows"] == N and peak / mb < 2000 and leak_ok
    print("PASS: 8GB 環境に十分な余裕" if ok else "確認要")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="性能 NFR の実測")
    ap.add_argument("--pages", type=int, default=100, help="replay で流す枚数")
    ap.add_argument("--only", choices=["all", "expand", "pipeline"], default="all")
    a = ap.parse_args()
    rc = 0
    if a.only in ("all", "expand"):
        rc |= measure_expand()
    if a.only in ("all", "pipeline"):
        rc |= measure_pipeline(a.pages)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
