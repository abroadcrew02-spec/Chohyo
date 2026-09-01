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
        # -rs: skip の理由も出す。素材欠けで大量 skip したときに何が要るか分かる
        # -n auto: pytest-xdist で CPU 数ぶん並列（2026-09-01・ユーザー承認）。
        #   テストは tmp_path 隔離・共有素材（workdir/s2 等）は読み取りのみなので
        #   並列安全。集計行「N passed in Xs」の形式は xdist でも同じで、下の
        #   SUMMARY_LINE 集計はそのまま機能する
        [str(PYTHON), "-X", "utf8", "-m", "pytest", "-q", "--tb=short", "-rf", "-rs",
         "-n", "auto"],
        ROOT / "core"))

    # 2) GUI の純ロジック（座標追従・進捗イベントの文言）。
    # node と esbuild は既に依存にあるので追加導入は要らない。ここへ載せないと
    # 誰も走らせず腐る（レビュー4巡目で新設したテストの配線）
    node = shutil.which("node")
    gui_test = ROOT / "gui" / "tests" / "gui-logic.test.mjs"
    if node and gui_test.exists():
        results.append(run(
            "gui logic",
            [node, str(gui_test)],
            ROOT / "gui"))
    else:
        results.append(("gui logic", None,
                        0.0, "node 未導入のため SKIP"))

    # 3) cargo test（サブコマンド白リスト・issue #7）
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
        # skip が多いまま PASS と出すと、素材の無い環境の「116 passed, 70 skipped」が
        # そのままリリースノートへ転記される。約38%のテストが .gitignore 済みの
        # workdir/ 素材（sample-1.png・s2/resp_*.json）に依存しており、purge 後や
        # 別マシンでは黙って skip する（レビュー4巡目 M-5）
        skipped = agg.get("skipped", 0)
        passed = agg.get("passed", 0)
        if status == "PASS" and passed and skipped > passed * 0.1:
            status = "FAIL"
            fail = True
            counts += f"（skip が {skipped/(passed+skipped)*100:.0f}% と多い）"
            print(f"----- {name} の skip 理由 -----")
            for line in out.splitlines():
                if line.startswith("SKIPPED"):
                    print(line)
            print("素材が要るテストが skip されている。"
                  "workdir/pages/sample-1.png と workdir/s2/resp_*.json を用意して再実行する")
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
