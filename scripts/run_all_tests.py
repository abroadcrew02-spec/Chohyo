# -*- coding: utf-8 -*-
"""全自動テストの一括実行と集計（03_test_requirements.md §2 の L1）。

  python scripts/run_all_tests.py

pytest（core/tests 全体）と cargo test（gui/src-tauri）を順に回し、
最後に1行サマリを出す。リリース時はこの1行を Release ノートへ転記する。
実 API は呼ばない（Replay 前提）。cargo が無い環境では Rust 側を SKIP 扱い。
"""
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # 他の scripts と同じ書き方に揃える
# 集計行だけを拾うための形。件数で始まり "in <秒>" で終わるものに限る
SUMMARY_LINE = re.compile(r"^\d+ (passed|failed|skipped|error)\b.*\bin \d")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def run(name, cmd, cwd):
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    return name, r.returncode, dt, out


def main():
    results = []

    # 1) pytest（GUI スモークは dev サーバー無しなら自動 skip）
    results.append(run(
        "pytest",
        # -rf: 失敗したテスト名を必ず末尾に出す（パイプで切り詰めても分かる）
        [str(PYTHON), "-X", "utf8", "-m", "pytest", "-q", "--tb=short", "-rf"],
        ROOT / "core"))

    # 2) cargo test（サブコマンド白リスト・issue #7）
    if shutil.which("cargo"):
        results.append(run(
            "cargo test",
            ["cargo", "test", "--quiet"],
            ROOT / "gui" / "src-tauri"))
    else:
        results.append(("cargo test", None, 0.0, "cargo 未導入のため SKIP"))

    fail = False
    lines = []
    for name, code, dt, out in results:
        if code is None:
            lines.append(f"{name}: SKIP")
            continue
        # "N passed" 等を種別ごとに合算（cargo はテストバイナリごとに行が出る）。
        # 出力全体を舐めると、失敗メッセージやテスト名に含まれる "3 passed" まで
        # 拾って件数が水増しされる（レビュー LOW）。集計行だけを母集団にする:
        #   pytest -q → "7 passed in 0.32s"（= の罫線は付かない・実測）
        #   pytest 通常 → "==== 5 passed, 1 failed in 2.0s ===="
        #   cargo      → "test result: ok. 2 passed; 0 failed; ..."
        agg = {}
        for line in out.splitlines():
            s = line.strip().strip("=").strip()
            if not (SUMMARY_LINE.match(s) or "test result:" in line):
                continue
            for n, k in re.findall(r"(\d+) (passed|failed|skipped|error)", line):
                agg[k] = agg.get(k, 0) + int(n)
        counts = ", ".join(f"{v} {k}" for k, v in agg.items() if v) or f"exit={code}"
        status = "PASS" if code == 0 else "FAIL"
        # 1件も実行していないのに PASS と出すと「通った」と誤読される。
        # 収集ゼロ・集計行を読めなかった場合は失敗として扱う
        if status == "PASS" and not agg.get("passed"):
            status = "FAIL"
            fail = True
            counts += "（実行された試験が0件）"
        if code != 0:
            fail = True
            # 失敗したテスト名だけは必ずサマリへ載せる（全文はその後）
            names = [l for l in out.splitlines() if l.startswith("FAILED")]
            for n in names:
                lines.append(f"  ✗ {n}")
            print(f"----- {name} 出力（失敗のため全文） -----")
            print(out)
        lines.append(f"{name}: {status} ({counts}, {dt:.1f}s)")

    print()
    for l in lines:
        print(l)
    total_dt = sum(dt for _, _, dt, _ in results)
    print(f"SUMMARY: {'FAIL' if fail else 'PASS'} / {' | '.join(lines)} "
          f"/ total {total_dt:.1f}s")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
