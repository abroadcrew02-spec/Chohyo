"""実プロセスの強制終了→再開テスト（付録 C10 の自動化できる部分）。

実際の CLI プロセスを別プロセスとして起動し、処理の途中で kill する
（GUI の中断ボタン＝taskkill /T /F と同じ止まり方）。その後の run が
残りを拾い、入力ページ数＝出力行数が成立することを検証する。

※ 本物のコンソール Ctrl+C（CTRL_C_EVENT の伝播）は対話コンソールが
   必要なため、ここでは扱わない。強制終了は Ctrl+C より乱暴な停止で
   あり、これに耐えれば Ctrl+C にも耐える（WAL＋UPSERT の検証）。
"""
import json
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

from chouhyo_ocr.paths import app_root

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")

N_PAGES = 40  # 位置合わせが1枚 約0.2秒（2段探索後）→ kill を差し込む窓を作る


@pytest.fixture()
def env(tmp_path):
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    base = PAGE_PNG.read_bytes()
    for i in range(1, N_PAGES + 1):
        # 同一内容だと二重取り込み検知（要件 §5.1 Could）に食われるため
        # IEND 後に1バイト足して内容をユニーク化する（PIL は無視する）
        (inp / f"page{i:02d}.png").write_bytes(base + bytes([i]))
        shutil.copy(RESP, resp / f"page{i:02d}_p0001.json")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
        "unclear_threshold": 0.4,
    }), encoding="utf-8")
    return tmp_path, inp, resp, cfg


def run_cli(cfg, inp, resp, tmp):
    # stdout は必ずファイルへ。PIPE を読まずに待つとページ数次第で
    # パイプバッファが詰まりコア側の print がブロックする（perf 計測で実測）
    out = open(tmp / "run.out", "wb")
    err = open(tmp / "run.err", "wb")
    return subprocess.Popen(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(inp), "--replay", str(resp)],
        cwd=app_root() / "core", stdout=out, stderr=err)


def test_kill_midway_then_resume_completes(env):
    tmp, inp, resp, cfg = env

    # 1回目: 数ページ進んだところで強制終了（taskkill /T /F 相当）
    proc = run_cli(cfg, inp, resp, tmp)
    db = tmp / "wd" / "intermediate.sqlite"
    deadline = time.time() + 120  # フルスイート並走時の CPU 競合を考慮
    while time.time() < deadline:
        if db.exists():
            try:
                con = sqlite3.connect(db)
                done = con.execute(
                    "SELECT COUNT(*) FROM page WHERE state='done'").fetchone()[0]
                con.close()
                if done >= 3:
                    break
            except sqlite3.OperationalError:
                pass  # スキーマ作成中
        time.sleep(0.2)
    else:
        proc.kill()
        pytest.fail("120秒以内に3ページ完了へ達しない")

    subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                   capture_output=True)
    proc.wait(timeout=15)

    # kill 直後は AV スキャン等で disk I/O error になることがある（レビュー M-17 実測:
    # pytest 実行下で 5 回中 2 回。製品欠陥ではなく検証側の読み取りタイミング）→ リトライ
    states = None
    for attempt in range(5):
        try:
            con = sqlite3.connect(db)
            states = dict(
                con.execute("SELECT state, COUNT(*) FROM page GROUP BY state"))
            con.close()
            break
        except sqlite3.OperationalError:
            time.sleep(1.0)
    assert states is not None, "kill 後の DB 読み取りが5回とも失敗"
    assert states.get("done", 0) < N_PAGES, "kill 前に全部終わってしまった（窓が短い）"

    # 2回目: 続きから完走。処理済みは再送しない（§8-7）
    out = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(inp), "--replay", str(resp)],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=180)
    assert out.returncode == 0, out.stderr[-500:]
    summary = next(json.loads(l) for l in out.stdout.splitlines()
                   if '"summary"' in l)
    assert summary["pages"] == N_PAGES
    assert summary["rows"] == N_PAGES          # 入力ページ数＝出力行数（要件 §3.4）
    assert summary["api_calls"] < N_PAGES      # 1回目の完了分は再送されていない

    con = sqlite3.connect(db)
    done = con.execute("SELECT COUNT(*) FROM page WHERE state='done'").fetchone()[0]
    con.close()
    assert done == N_PAGES
