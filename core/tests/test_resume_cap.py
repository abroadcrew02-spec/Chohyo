"""再開規則・送信枚数上限のテスト（§8-6/7 の M3 残ギャップ）。

実 Ctrl+C の伝播（付録 C10）は実機確認項目として残る。ここでは
「中断でどんな状態が残っても、次の run が正しく拾う」ことを検証する。
"""
import shutil
import pathlib
import sqlite3

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.render_rows import STATUS_CAP, UNCLEAR
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


class CountingReplay(ReplayClient):
    def __init__(self, d):
        super().__init__(d)
        self.calls = 0

    def annotate(self, image_png, page_id):
        self.calls += 1
        return super().annotate(image_png, page_id)


@pytest.fixture()
def env(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "resp").mkdir()
    shutil.copy(PAGE_PNG, tmp_path / "input" / "sample-1.png")
    shutil.copy(RESP, tmp_path / "resp" / "sample-1_p0001.json")
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                 workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    return tmp_path, cfg


def test_send_cap_produces_unprocessed_row(env):
    tmp, cfg = env
    import dataclasses
    cfg0 = dataclasses.replace(cfg, send_limit=0)
    client = CountingReplay(tmp / "resp")
    summary = run(tmp / "input", TPL, cfg0, client)
    assert client.calls == 0                      # 上限が実効的に効く（要件 §6.2）
    assert summary.rows == 1                      # 入力ページ数＝出力行数は維持
    # 全〓＋ステータス
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    status, state = con.execute("SELECT status, state FROM page").fetchone()
    assert status == STATUS_CAP
    assert state != "done"


def test_capped_page_is_processed_on_next_run(env):
    tmp, cfg = env
    import dataclasses
    run(tmp / "input", TPL, dataclasses.replace(cfg, send_limit=0),
        CountingReplay(tmp / "resp"))
    client = CountingReplay(tmp / "resp")
    summary = run(tmp / "input", TPL, cfg, client)  # 上限を戻して再実行
    assert client.calls == 1
    assert summary.unclear_total < 212            # 正常処理された（全〓でない）
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    status, state = con.execute("SELECT status, state FROM page").fetchone()
    assert state == "done" and status == ""       # CAP ステータスが剥がれる


def test_done_page_is_not_resent(env):
    tmp, cfg = env
    run(tmp / "input", TPL, cfg, CountingReplay(tmp / "resp"))
    client = CountingReplay(tmp / "resp")
    run(tmp / "input", TPL, cfg, client)
    assert client.calls == 0                      # 処理済みは再送信しない（§8-7）


def test_interrupted_sending_page_is_resent_once(env):
    """state='sending' で応答が残っていない1枚は再送を許容する（D-05）。

    issue #92 で「応答は保存済みなのに state=sending」のページは復旧するように
    なったため、D-05 の再送が残るのは**応答が無い（または壊れている）**場合。
    ここでは応答ファイルごと消して、その経路を固定する。
    """
    tmp, cfg = env
    run(tmp / "input", TPL, cfg, CountingReplay(tmp / "resp"))
    for saved in (pathlib.Path(cfg.workdir) / "responses").iterdir():
        saved.unlink()                               # 応答が残っていない中断
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    con.execute("UPDATE page SET state='sending'")   # 送信中の中断を再現
    con.commit(); con.close()

    client = CountingReplay(tmp / "resp")
    run(tmp / "input", TPL, cfg, client)
    assert client.calls == 1                      # この1枚だけ再送
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    (state,) = con.execute("SELECT state FROM page").fetchone()
    assert state == "done"
    (attempt,) = con.execute("SELECT attempt FROM page").fetchone()
    assert attempt == 2                           # attempt が積み上がる


def test_interrupted_sending_page_with_saved_response_is_not_resent(env):
    """応答が保存済みなら state=sending でも再送しない（issue #92）。

    送信成功後は「応答を保存 → state を received」の2ステップで、間で落ちると
    課金済みの応答を持ったまま送信中の状態で残る。旧実装はこれを D-05 の
    「送信済みか判別できない」ケースとして一律に再送＝再課金していた。
    """
    tmp, cfg = env
    run(tmp / "input", TPL, cfg, CountingReplay(tmp / "resp"))
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    con.execute("UPDATE page SET state='sending'")
    con.commit(); con.close()

    client = CountingReplay(tmp / "resp")
    summary = run(tmp / "input", TPL, cfg, client)
    assert client.calls == 0, "課金済みの応答があるのに再送信した"
    assert summary.recovered_responses == 1
    con = sqlite3.connect(f"{cfg.workdir}/intermediate.sqlite")
    (state,) = con.execute("SELECT state FROM page").fetchone()
    assert state == "done"
