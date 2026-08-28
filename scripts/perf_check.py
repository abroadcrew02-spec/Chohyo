# -*- coding: utf-8 -*-
"""性能 NFR の実測（要件 §6.1: 物理メモリ8GB で100枚連続を OOM・停止なく完走）。

保存済み Vision 応答の replay で 100 ページを実プロセスで処理し、
ピーク RSS・所要時間・成果物サイズを計測する（API 課金ゼロ）。
実行: .venv/Scripts/python.exe scripts/perf_check.py
"""
import json
import shutil
import subprocess
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
N = 100
BASE = ROOT / "workdir_build" / "perf"
PAGE = ROOT / "workdir" / "pages" / "sample-1.png"
RESP = ROOT / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"


def main() -> int:
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
    while proc.poll() is None:
        try:
            rss = ps.memory_info().rss
            for ch in ps.children(recursive=True):
                try:
                    rss += ch.memory_info().rss
                except psutil.Error:
                    pass
            peak = max(peak, rss)
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
    ok = summary["rows"] == N and peak / mb < 2000
    print("PASS: 8GB 環境に十分な余裕" if ok else "確認要")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
