"""remap のロック取得と、1ページ分の更新の原子性（issue #93）。

remap だけが RunLock を取らずに共有 SQLite を書き換えていた。CLI の remap は
「ロック無しの remap → ロック有りの render」の2段だったため、間に別プロセスの
run が割り込む窓が常にあった。さらに store の書き込みはメソッドごとに commit
していたので、1ページ分の更新（cell / 拡張列 / era_score / template_hash /
unassigned）が5回に分断され、途中で落ちると「cell は新テンプレートの割付なのに
page.template_hash は旧」という自己矛盾が残りえた。
"""
import json
import os
import shutil
import subprocess
import time
from contextlib import contextmanager

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import (OperationRefused, remap, remap_and_render,
                                  render, run)
from chouhyo_ocr.runlock import RunLock, RunLockError
from chouhyo_ocr.store import Store
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"
PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")

# 別プロセスでロックを握り続けるヘルパ。ready を作って待機し、stop が現れたら
# 解放する（テスト側が窓の長さを決められるようにする）
_HOLDER = """\
import os, sys, time
from chouhyo_ocr.runlock import RunLock
workdir, ready, stop = sys.argv[1], sys.argv[2], sys.argv[3]
lock = RunLock(workdir)
lock.acquire()
open(ready, "w").close()
deadline = time.time() + 60          # テストが落ちても居座らせない
while not os.path.exists(stop) and time.time() < deadline:
    time.sleep(0.05)
lock.release()
"""


def make_cfg(tmp_path) -> Config:
    return Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                  workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))


def run_one_page(tmp_path, cfg):
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    return run(inp, TPL, cfg, ReplayClient(resp))


@contextmanager
def lock_held_by_another_process(tmp_path, workdir):
    script = tmp_path / "holder.py"
    script.write_text(_HOLDER, encoding="utf-8")
    ready = tmp_path / "holder.ready"
    stop = tmp_path / "holder.stop"
    proc = subprocess.Popen([str(PYTHON), str(script), str(workdir),
                             str(ready), str(stop)],
                            cwd=app_root() / "core")
    try:
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists(), "ロックを握るプロセスが起動しなかった"
        yield
    finally:
        stop.write_text("", encoding="utf-8")
        proc.wait(timeout=30)


def test_remap_is_refused_while_another_process_holds_the_lock(tmp_path):
    """別プロセスが実行中なら remap は拒否される（#93 の本体）。"""
    cfg = make_cfg(tmp_path)
    run_one_page(tmp_path, cfg)
    with lock_held_by_another_process(tmp_path, cfg.workdir):
        with pytest.raises(OperationRefused, match="実行中"):
            remap(TPL, cfg)
        with pytest.raises(OperationRefused, match="実行中"):
            remap_and_render(TPL, cfg)
        # 既存の2経路（run / render）と同じ断り方であることも固定する
        with pytest.raises(OperationRefused, match="実行中"):
            render(TPL, cfg)
    # 解放後は通る（ロックが残骸として残らない）
    assert remap(TPL, cfg) == 1


def test_remap_and_render_keep_the_lock_between_the_two_steps(tmp_path):
    """remap→render の間にロックを手放さない（#93 の窓）。

    remap の完了イベント（remap_summary）が出た時点＝render に入る直前で
    ロックを取り直せないことを確認する。単体の remap() は戻る前に解放する
    ——だから2回に分けて呼ぶ CLI 経路には窓があった。
    """
    cfg = make_cfg(tmp_path)
    run_one_page(tmp_path, cfg)

    checked = []

    def progress(event):
        if event.get("event") == "remap_summary":
            with pytest.raises(RunLockError):
                RunLock(cfg.workdir).acquire()
            checked.append(True)

    n, xlsx, csvp, rows = remap_and_render(TPL, cfg, progress=progress)
    assert checked == [True]
    assert n == 1 and xlsx.exists() and csvp.exists() and len(rows) == 1

    # 単体の remap は戻り値を返す時点で解放している（窓があった根拠）
    assert remap(TPL, cfg) == 1
    lock = RunLock(cfg.workdir)
    lock.acquire()          # 例外にならない＝解放済み
    lock.release()


def test_failed_remap_leaves_cells_and_template_hash_in_the_old_generation(
        tmp_path, monkeypatch):
    """1ページ分の更新の途中で落ちても、旧世代のまま揃っている（#93）。

    set_template_hash で落として、cell だけが新しくなる（＝次の render が
    「テンプレートが変わっている」と誤って拒否する）状態が残らないことを見る。
    """
    cfg = make_cfg(tmp_path)
    run_one_page(tmp_path, cfg)
    db = tmp_path / "wd" / "intermediate.sqlite"

    with Store(db) as store:
        pid = store.pages()[0]["page_id"]
        before_hash = store.page(pid)["template_hash"]
        field_id = sorted(store.cells(pid))[0]
        # 割付し直せば必ず上書きされる印を1欄に置く（ロールバックの目印）
        store.con.execute(
            "UPDATE cell SET raw_text='SENTINEL' WHERE page_id=? AND field_id=?",
            (pid, field_id))
        store.con.commit()
    assert before_hash

    def boom(self, page_id, template_hash):
        raise RuntimeError("injected")

    monkeypatch.setattr(Store, "set_template_hash", boom)
    with pytest.raises(RuntimeError, match="injected"):
        remap(TPL, cfg)

    with Store(db) as store:
        assert store.page(pid)["template_hash"] == before_hash
        assert store.cells(pid)[field_id][0] == "SENTINEL"


def test_transaction_commits_once_and_rolls_back_as_a_unit(tmp_path):
    """Store.transaction の基本契約（別接続から見えるのは commit 後だけ）。"""
    db = tmp_path / "t.sqlite"
    with Store(db) as writer, Store(db) as reader:
        with writer.transaction():
            writer.upsert_page("p1", "a.png", 1, "expanded")
            writer.upsert_cells("p1", [("f1", "x", 0.9, "text", 0)])
            assert reader.page("p1") is None      # まだ commit していない
        assert reader.page("p1") is not None
        assert reader.cells("p1")["f1"][0] == "x"

        with pytest.raises(RuntimeError):
            with writer.transaction():
                writer.upsert_cells("p1", [("f1", "y", 0.9, "text", 0)])
                writer.set_unassigned("p1", 3, 4)
                raise RuntimeError("boom")
        # どちらの更新も残っていない
        assert reader.cells("p1")["f1"][0] == "x"
        assert reader.page("p1")["unassigned_other"] == 0


def test_nested_transaction_commits_at_the_outermost_exit(tmp_path):
    db = tmp_path / "n.sqlite"
    with Store(db) as writer, Store(db) as reader:
        with writer.transaction():
            writer.upsert_page("p1", "a.png", 1, "expanded")
            with writer.transaction():
                writer.set_state("p1", "done")
            assert reader.page("p1") is None      # 内側では commit しない
        assert reader.page("p1")["state"] == "done"
