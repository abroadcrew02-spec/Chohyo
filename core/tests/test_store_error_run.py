"""issue #138: run 中の DB 障害（database is locked・StoreError）を
様式不一致と取り違えず、再送＝再課金も起こさないことの固定。

`pipeline._run_locked` は `_map_and_score` を呼んだ直後（＝Vision 応答を
`save_response` で保存し終えた後）に割付・丸印を行う。ここで sqlite3.Error /
store.StoreError が起きても、原因は中間データ側（DB のロック競合・整合性
検査違反）であってテンプレートや記入内容の問題ではない。以前は一律
`except Exception` に落ちて `様式不一致` を報告しつつ `state="failed"` に
していたため、次回 run の todo に入って Vision へ再送（＝再課金）されていた。

差し替え対象は `chouhyo_ocr.pipeline._map_and_score`（pipeline が同じ
モジュール内で定義している関数を直接呼ぶため、pipeline 側の名前を
差し替えれば効く）。test_render_status.py の `build_row` 差し替えと同じ流儀。
"""
import shutil
import sqlite3

import pytest

from chouhyo_ocr import logging_safe, pipeline as pipeline_mod, render_rows
from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.store import Store, StoreError
from chouhyo_ocr.vision_client import ReplayClient

TPL = app_root() / "templates" / "chouhyo-v1.json"
RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"

needs_replay = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()),
    reason="保存済み応答・サンプル画像が無い環境")


def _cfg(tmp_path) -> Config:
    return Config(unclear_threshold=0.4,
                  output_dir=str(tmp_path / "out"), workdir=str(tmp_path / "wd"),
                  log_dir=str(tmp_path / "logs"))


def _prepare(tmp_path):
    """入力フォルダと replay 素材を用意する（送信は 0 円）。"""
    inp = tmp_path / "input"; inp.mkdir()
    replay = tmp_path / "responses"; replay.mkdir()
    shutil.copy(PAGE_PNG, inp / "sample-1.png")
    shutil.copy(RESP, replay / "sample-1_p0001.json")
    logging_safe.init(str(tmp_path / "logs"))
    return inp, replay, _cfg(tmp_path)


def _pages(cfg):
    with Store(cfg.workdir + "/intermediate.sqlite") as store:
        return {r["page_id"]: dict(r) for r in store.pages()}


def _boom(exc):
    def _f(*a, **kw):
        raise exc
    return _f


@needs_replay
@pytest.mark.parametrize("exc", [
    pytest.param(sqlite3.OperationalError("database is locked"), id="sqlite3-locked"),
    pytest.param(StoreError("unexpected cell count"), id="store-error"),
])
def test_db_failure_does_not_become_format_mismatch_and_state_stays_resendable(
        tmp_path, monkeypatch, exc):
    """_map_and_score が sqlite3.Error / StoreError を投げても、
    status は様式不一致にならず、state は再送対象（failed）に落ちない。
    """
    inp, replay, cfg = _prepare(tmp_path)
    monkeypatch.setattr(pipeline_mod, "_map_and_score", _boom(exc))

    summary = run(inp, TPL, cfg, ReplayClient(replay))

    # 送信は今回の run で1回だけ起きている（DB 障害は送信後に起きるため）
    assert summary.api_calls == 1
    # 様式不一致としては数えない（issue #138 の核心）
    assert summary.format_mismatch == 0
    assert summary.processed_failed == 1

    pages = _pages(cfg)
    pid = next(iter(pages))
    page = pages[pid]
    assert page["status"] != render_rows.STATUS_FORMAT_MISMATCH
    assert page["status"] == render_rows.STATUS_INTERRUPTED
    assert page["status_reason"] == "store_error"
    # state が "failed" に落ちていない＝次回 run で「保存済み応答を再利用」
    # の経路（page["state"] == "received"）に乗る
    assert page["state"] != "failed"
    assert page["state"] == "received"


@needs_replay
def test_db_failure_record_also_fails_run_continues_to_remaining_pages(
        tmp_path, monkeypatch):
    """接続自体が死んでいる場合（`_map_and_score` だけでなく、それを受けての
    記録用 `store.set_status(..., reason="store_error")` 自体も同じ例外を
    投げる二重障害）でも、run はページループを抜けずに残りのページを
    処理し切る（issue #138 再検証・MEDIUM 指摘）。

    以前は `store.set_status` の呼び出しが例外ハンドラの中で無条件に行われ、
    `database is locked` のように接続そのものが詰まっているケースでは同じ
    例外が再送出されて run 全体が落ち、未処理のページが失われていた
    （StoreError＝整合性検査違反なら接続は健全なので `set_status` は成功して
    おり、この経路は通っていなかった）。
    """
    inp = tmp_path / "input"; inp.mkdir()
    replay = tmp_path / "responses"; replay.mkdir()
    # 同一バイト列だと「同一内容の二重投入」判定（issue #46・pipeline.py の
    # source.read_bytes() ハッシュ）に引っかかり、b/c が送信すらされない
    # skip_duplicate 行になってしまう。末尾に無害なゴミバイトを付けて中身を
    # ページごとに変える（PNG は IEND チャンクで読み終わるので、その後ろの
    # バイト列があっても PIL の Image.open は無視して読める）
    for name in ("a", "b", "c"):
        (inp / f"{name}.png").write_bytes(PAGE_PNG.read_bytes() + f"__{name}__".encode())
        shutil.copy(RESP, replay / f"{name}_p0001.json")
    logging_safe.init(str(tmp_path / "logs"))
    cfg = _cfg(tmp_path)

    # page_id は ingest.page_id_for が "<stem>_p0001" 形式で決定論的に作る
    # （tests/test_duplicate_source.py と同じ前提）。b だけを対象にすることで、
    # b より前（a）・後（c）のどちらのページも失われないことを確認する
    target_pid = "b_p0001"
    real_map_and_score = pipeline_mod._map_and_score

    def _map_and_score_boom(store, template, pid, resp, faces, *, snap_by_face):
        if pid == target_pid:
            raise sqlite3.OperationalError("database is locked")
        return real_map_and_score(store, template, pid, resp, faces,
                                  snap_by_face=snap_by_face)

    monkeypatch.setattr(pipeline_mod, "_map_and_score", _map_and_score_boom)

    real_set_status = Store.set_status

    def _set_status_boom(self, page_id, status, reason=""):
        if page_id == target_pid and status == render_rows.STATUS_INTERRUPTED:
            raise sqlite3.OperationalError("database is locked")
        return real_set_status(self, page_id, status, reason=reason)

    monkeypatch.setattr(Store, "set_status", _set_status_boom)

    summary = run(inp, TPL, cfg, ReplayClient(replay))

    # 3ページとも送信は起きている（DB 障害は送信後にしか起きないため）
    assert summary.api_calls == 3
    assert summary.format_mismatch == 0
    assert summary.processed_failed == 1  # 失敗は対象ページの1件のみ

    pages = _pages(cfg)
    # 記録用 UPDATE 自体も失敗したので status は初期値（空文字列）のまま
    assert pages[target_pid]["status"] == ""
    assert pages[target_pid]["state"] == "received"

    # 対象ページより前・後のページはどちらも正常に最後まで処理されている
    # （run がページループを抜けて丸ごと落ちていたら "done" にならない）
    assert pages["a_p0001"]["state"] == "done"
    assert pages["c_p0001"]["state"] == "done"


@needs_replay
def test_retry_after_db_failure_reuses_saved_response_without_resending(
        tmp_path, monkeypatch):
    """1回目の run で DB 障害に遭って state="received" のまま残ったページは、
    2回目の run（_map_and_score を正常に戻した状態）で保存済み応答を再利用し、
    Vision へは再送しない（api_calls が増えない＝再課金されない）。
    """
    inp, replay, cfg = _prepare(tmp_path)
    monkeypatch.setattr(pipeline_mod, "_map_and_score",
                        _boom(sqlite3.OperationalError("database is locked")))
    summary1 = run(inp, TPL, cfg, ReplayClient(replay))
    assert summary1.api_calls == 1

    monkeypatch.undo()  # _map_and_score を本来の実装へ戻す

    summary2 = run(inp, TPL, cfg, ReplayClient(replay))

    # 2回目は保存済み応答を再利用するだけで、Vision へは1回も送っていない
    assert summary2.api_calls == 0

    pages = _pages(cfg)
    pid = next(iter(pages))
    assert pages[pid]["state"] == "done"
