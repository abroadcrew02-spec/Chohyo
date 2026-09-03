# -*- coding: utf-8 -*-
"""全自動テストの一括実行と集計（03_test_requirements.md §2 の L1）。

  python scripts/run_all_tests.py [--coverage]

pytest（core/tests 全体）・GUI 純ロジック・gui の型検査（tsc --noEmit）・
cargo test（gui/src-tauri）を順に回し、最後に1行サマリを出す。リリース時は
この1行を Release ノートへ転記する。実 API は呼ばない（Replay 前提）。

--coverage を付けると pytest にカバレッジ計測を足す（既定 OFF・issue #65-5）。
計測は実行時間を伸ばすため、日常のゲートでは付けない。

node / cargo が無い環境は SKIP ではなく FAIL にする（issue #95）。SKIP を
合否に効かせないままだと、道具が入っていないだけの環境で Rust と GUI の
テストが1件も走らずに PASS が出る。導入方法はメッセージに添える。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

# python -m scripts.run_all_tests や PYTHONSAFEPATH=1 で実行すると、このスクリプト
# の置き場所（scripts/）が sys.path に自動で入らず兄弟モジュール import が壊れる
# ため明示しておく
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dist_stamp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]  # 他の scripts と同じ書き方に揃える
# 集計行だけを拾うための形。件数で始まり "in <秒>" で終わるものに限る
SUMMARY_LINE = re.compile(r"^\d+ (passed|failed|skipped|error)\b.*\bin \d")
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
# 「道具が無くて実行できなかった」を表す番兵。returncode の None（＝正常終了
# コードが取れなかった）と取り違えないよう専用の値にする（issue #95）
MISSING = object()
# カバレッジの出力先。workdir_build/ は .gitignore 済みで、生成物が追跡対象に
# ならない。pytest の cwd は core/ なので、--cov-report へは core/ からの相対で
# 渡す——Windows の絶対パスはドライブレターの `:` が
# `--cov-report=<種別>:<パス>` の区切りと紛らわしい
COVERAGE_JSON = ROOT / 'workdir_build' / 'coverage' / 'coverage.json'
COVERAGE_JSON_REL = '../workdir_build/coverage/coverage.json'


def run(name, cmd, cwd):
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=1800)
    dt = time.time() - t0
    out = (r.stdout or "") + (r.stderr or "")
    return name, r.returncode, dt, out


def read_coverage_percent():
    """pytest-cov が書いた JSON から全体の網羅率を読む（issue #65-5）。

    端末表示の表を数え直さず JSON を読むのは、-n auto（xdist）だと表の出方が
    ワーカー数で変わりうるため。JSON の totals は結合後の1組だけを持つ。
    pytest が落ちた場合はファイルが無いことがあるので None を返す。
    """
    try:
        with open(COVERAGE_JSON, encoding="utf-8") as f:
            return float(json.load(f)["totals"]["percent_covered"])
    except (OSError, ValueError, KeyError):
        return None


def _short(text, limit=80):
    """1行サマリへ載せるために詰める。"""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


def check_tsc():
    """gui の型検査（`tsc --noEmit`・issue #95）。

    集計行（"N passed in Xs"）を出さないので、pytest/cargo と同じループには
    乗せず core-dist と同様に個別で PASS/FAIL を返す。tsc は差分ではなく毎回
    プロジェクト全体を見るため件数の概念が無い。

    実体は gui/node_modules/.bin/tsc を直接呼ぶ。リポジトリ直下には
    package.json が無く、そこから `npx tsc` を呼ぶと npx が別パッケージを
    取りに行って "This is not the tsc command you are looking for" で終わる
    （実測 2026-09-03）。代替の npx には --no-install を付ける——ゲートの合否が
    ネットワークの有無で変わらないようにするため。

    戻り値: (status, detail, 秒)
    """
    gui = ROOT / "gui"
    if not (gui / "tsconfig.json").exists():
        return "FAIL", "gui/tsconfig.json が無い", 0.0
    # tsc は node で動く。node が無いまま tsc.cmd を起動すると、cmd.exe の
    # 「認識されていません」が CP932 のまま返って exit=1 としか読めない
    # （実測 2026-09-03）。先に node の有無で切って理由を名指しする
    if not shutil.which("node"):
        return "FAIL", "node 未導入。https://nodejs.org/ から Node.js 22 以上を入れる", 0.0
    local = gui / "node_modules" / ".bin" / ("tsc.cmd" if os.name == "nt" else "tsc")
    npx = shutil.which("npx")
    if local.exists():
        cmd = [str(local)]
    elif npx:
        cmd = [npx, "--no-install", "tsc"]
    else:
        return "FAIL", "tsc が無い。Node.js 22 以上を入れて cd gui && npm ci", 0.0
    _, code, dt, out = run("tsc", cmd + ["--noEmit", "-p", "."], gui)
    if code == 0:
        return "PASS", "型エラーなし", dt
    # tsc は "src/X.tsx(12,3): error TS2345: ..." を1件1行で出す。件数と先頭の
    # 1件をサマリへ、全文はその下へ出す
    errs = [l for l in out.splitlines() if ": error TS" in l]
    detail = f"{len(errs)} 件の型エラー: {errs[0]}" if errs else f"exit={code}"
    print("----- tsc 出力（失敗のため全文） -----")
    print(out)
    return "FAIL", detail, dt


def main(coverage_on=False):
    results = []

    # 1) pytest（GUI スモークは dev サーバー無しなら自動 skip）
    # --coverage のときだけ計測を足す。既定で付けないのは、計測が実行時間を
    # 伸ばすため（日常のゲートは速さが要る）。--ignore=tests/test_gui_smoke.py は
    # 付けない——dev サーバーが起動していれば実走させ、いなければ従来どおり skip
    # させる。計測のために対象を減らすと、計測した数字と日常のゲートが食い違う
    pytest_cmd = [str(PYTHON), "-X", "utf8", "-m", "pytest", "-q", "--tb=short",
                  "-rf", "-rs", "-n", "auto"]
    if coverage_on:
        # 古い結果を読んで「計測した」と誤解しないよう、先に消す
        COVERAGE_JSON.unlink(missing_ok=True)
        COVERAGE_JSON.parent.mkdir(parents=True, exist_ok=True)
        pytest_cmd += [
            "--cov=chouhyo_ocr",
            # skip-covered: 100% の行だけのファイルを一覧から外す。読むべき
            # 「足りていない行」だけが残る
            "--cov-report=term-missing:skip-covered",
            f"--cov-report=json:{COVERAGE_JSON_REL}",
        ]

    results.append(run(
        "pytest",
        # -rf: 失敗したテスト名を必ず末尾に出す（パイプで切り詰めても分かる）
        # -rs: skip の理由も出す。素材欠けで大量 skip したときに何が要るか分かる
        # -n auto: pytest-xdist で CPU 数ぶん並列（2026-09-01・ユーザー承認）。
        #   テストは tmp_path 隔離・共有素材（testdata/local/s2 等）は読み取りのみなので
        #   並列安全。集計行「N passed in Xs」の形式は xdist でも同じで、下の
        #   SUMMARY_LINE 集計はそのまま機能する
        pytest_cmd,
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
        why = ("node 未導入。https://nodejs.org/ から Node.js 22 以上を入れる"
               if not node else f"{gui_test.relative_to(ROOT)} が無い")
        results.append(("gui logic", MISSING, 0.0, why))

    # 2b) gui の型検査。gui-logic テストは esbuild で型注釈を剥がすだけで型を
    # 見ないため、ここを入れないと型エラーが配布物ビルドまで露見しない（#95）
    tsc_status, tsc_detail, tsc_dt = check_tsc()

    # 3) cargo test（サブコマンド白リスト・issue #7）
    if shutil.which("cargo"):
        results.append(run(
            "cargo test",
            ["cargo", "test", "--quiet"],
            ROOT / "gui" / "src-tauri"))
    else:
        results.append(("cargo test", MISSING, 0.0,
                        "cargo 未導入。https://rustup.rs/ の rustup で Rust を入れる"))

    # 4) core-dist（同梱 exe）の鮮度検査。2026-09-02: GUI が優先起動する同梱
    # exe が再ビルドされないまま core 側の変更を反映せず配布された事故があった
    # （同梱物は上の pytest/cargo では一切検査されない）。判定の形が pytest/
    # cargo と違う（ハッシュ比較で PASS/FAIL/SKIP を返す）ため、下の集計ループ
    # （"N passed" 拾い）には乗せず個別に扱う
    dist_status, dist_detail = dist_stamp.check_freshness(ROOT)

    fail = False
    lines = []
    for name, code, dt, out in results:
        if code is MISSING:
            # 道具が無いだけの環境を緑にしない。ここを SKIP のまま集計から
            # 外していたのが issue #95 の中身
            fail = True
            lines.append(f"{name}: FAIL ({out})")
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
        # testdata/local/ の検証素材（sample-1.png・s2/resp_*.json）に依存しており、
        # 素材を持たない別マシンでは黙って skip する（レビュー4巡目 M-5・#88）
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
                  "testdata/local/pages/sample-1.png と testdata/local/s2/resp_*.json を用意して再実行する")
        if code != 0:
            fail = True
            # 失敗したテスト名だけは必ずサマリへ載せる（全文はその後）
            names = [l for l in out.splitlines() if l.startswith("FAILED")]
            for n in names:
                lines.append(f"  ✗ {n}")
            print(f"----- {name} 出力（失敗のため全文） -----")
            print(out)
        lines.append(f"{name}: {status} ({counts}, {dt:.1f}s)")

    # 全文は check_tsc が失敗した時点で出しているので、ここでは合否だけ畳む
    if tsc_status == "FAIL":
        fail = True
    lines.append(f"tsc: {tsc_status} ({_short(tsc_detail)}, {tsc_dt:.1f}s)")

    if coverage_on:
        pct = read_coverage_percent()
        lines.append("coverage: " + (f"{pct:.1f}%" if pct is not None else
                                     "計測できず（coverage.json が無い）"))

    if dist_status == "FAIL":
        fail = True
        print("----- core-dist 鮮度検査（詳細） -----")
        print(dist_detail)
    lines.append(f"core-dist: {dist_status} ({_short(dist_detail)})")

    print()
    for l in lines:
        print(l)
    total_dt = sum(dt for _, _, dt, _ in results) + tsc_dt
    print(f"SUMMARY: {'FAIL' if fail else 'PASS'} / {' | '.join(lines)} "
          f"/ total {total_dt:.1f}s")
    return 1 if fail else 0


if __name__ == "__main__":
    # 引数はこの1つだけなので argparse は入れない
    sys.exit(main(coverage_on="--coverage" in sys.argv[1:]))
