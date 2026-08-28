"""同一内容ファイルの二重取り込み検知（要件 §5.1 Could）。"""
import shutil

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


def test_same_content_different_name_is_skipped(tmp_path):
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    # 同じ中身のファイルを別名で2つ置く
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(PAGE_PNG, inp / "b.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    shutil.copy(RESP, resp / "b_p0001.json")
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                 workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))

    events = []
    summary = run(inp, TPL, cfg, ReplayClient(resp), progress=events.append)

    # 2つ目（同一内容・別名）は API へ送らないが、黙って消さず
    # 「スキップ（重複）」の全〓行として出す（D-27・§3.4 の行数保存を優先）
    assert summary.pages == 2
    assert summary.rows == 2
    skips = [e for e in events if e.get("event") == "skip_duplicate"]
    assert len(skips) == 1
    assert skips[0]["file"] == "b.png"
    assert skips[0]["same_as"] == "a.png"


def test_rerun_of_same_file_is_not_treated_as_duplicate(tmp_path):
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                 workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))

    run(inp, TPL, cfg, ReplayClient(resp))
    events = []
    summary = run(inp, TPL, cfg, ReplayClient(resp), progress=events.append)
    # 同名の再実行は「再開」であり二重投入ではない（スキップ扱いしない）
    assert summary.pages == 1
    assert not [e for e in events if e.get("event") == "skip_duplicate"]
