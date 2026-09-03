"""6巡目の LOW 群（#53 L-5 / L-9 / L-10 と #65-4 / #65-9）。

いずれも実害は小さいが、黙って間違った結果を出す側の穴なのでテストで固定する。
"""
import json
import shutil
import sys

import pytest

from chouhyo_ocr import cli
from chouhyo_ocr.config import ConfigError
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.store import Store, StoreError

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

needs_replay = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


def write_cfg(tmp_path) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "unclear_threshold": 0.4,
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")
    return str(p)


def run_cli(cfg, inp, resp) -> int:
    return cli.main(["--config", cfg, "run", "--input", str(inp),
                     "--template", str(TPL), "--replay", str(resp)])


# ---------- #53 L-5: drop_pages_of の後に採番集合を更新する ----------

@needs_replay
def test_replaced_file_keeps_its_page_id(tmp_path):
    """同名・別内容に差し替えても帳票 ID が `<stem>_<hash8>_p0001` へ逃げない。

    旧行は drop_pages_of で消えているのに `taken`（使用済み ID の集合）が
    更新されず、採番が衝突回避側へ倒れていた。
    """
    cfg = write_cfg(tmp_path)
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    assert run_cli(cfg, inp, resp) == 0
    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        assert store.all_page_ids() == {"a_p0001"}

    # 同じ名前で中身を変える（PIL は IEND の後ろを無視するので画像としては有効）
    (inp / "a.png").write_bytes(PAGE_PNG.read_bytes() + b"\x01")
    assert run_cli(cfg, inp, resp) == 0
    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        assert store.all_page_ids() == {"a_p0001"}


# ---------- #53 L-9: 終了コードの母集団は今回の入力だけ ----------

@needs_replay
def test_exit_code_ignores_done_rows_from_earlier_runs(tmp_path):
    """過去の done 行が残っていても、今回の入力が全滅すれば exit 1。"""
    cfg = write_cfg(tmp_path)
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    assert run_cli(cfg, inp, resp) == 0

    # 2回目は壊れた画像1枚だけ（a.png は入力から外す）
    (inp / "a.png").unlink()
    (inp / "b.png").write_bytes(b"not an image")
    assert run_cli(cfg, inp, resp) == 1

    # 過去の done 行は残っている（＝旧実装が exit 0 を返していた条件）
    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        states = {p["page_id"]: p["state"] for p in store.pages()}
    assert states["a_p0001"] == "done" and states["b_p0001"] == "failed"


@needs_replay
def test_send_limit_zero_still_exits_zero(tmp_path):
    """送信上限で見送っただけの実行は失敗ではない（分割送信は通常運用）。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "send_limit": 0,
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    assert run_cli(str(p), inp, resp) == 0


# ---------- #53 L-10: 空の keep で NOT IN () を組み立てない ----------

def test_upsert_cells_with_no_rows_clears_the_page(tmp_path):
    """rows が空でも SQL 構文エラーにならず、そのページの cell が全部消える。

    旧実装は `NOT IN ()` を発行して sqlite3 が落ち、pipeline の
    `except Exception` に捕まって「様式不一致」に化けていた。
    """
    db = tmp_path / "s.sqlite"
    with Store(db) as store:
        store.upsert_page("p1", "a.png", 1, "done")
        store.upsert_cells("p1", [("f1", "x", 0.9, "text", 0),
                                  ("f2", "y", 0.9, "text", 0)])
        assert set(store.cells("p1")) == {"f1", "f2"}
        store.upsert_cells("p1", [])          # 例外にならない
        assert store.cells("p1") == {}


# ---------- #65-9: 拡張列の UPDATE が空振りしたら黙って進まない ----------

def test_upsert_cell_extras_fails_loudly_when_no_row_matches(tmp_path):
    db = tmp_path / "s.sqlite"
    with Store(db) as store:
        store.upsert_page("p1", "a.png", 1, "done")
        store.upsert_cells("p1", [("f1", "x", 0.9, "text", 0)])
        # 呼び順どおり（upsert_cells の後）なら通る
        store.upsert_cell_extras("p1", [("f1", "0.900", "main")])
        assert store.cell_extras("p1")["f1"] == ("0.900", "main")
        # cell 行が無い field_id / page_id は明示エラー
        with pytest.raises(StoreError, match="拡張列"):
            store.upsert_cell_extras("p1", [("f_missing", "", "")])
        with pytest.raises(StoreError, match="拡張列"):
            store.upsert_cell_extras("p_missing", [("f1", "", "")])
        # 空リストは何もしない（例外にしない）
        store.upsert_cell_extras("p1", [])


# ---------- #65-4: 配布 exe を --template なしで直接叩いたとき ----------

class _Args:
    def __init__(self, config, template):
        self.config = config
        self.template = template


def _cfg_with_last_template(tmp_path, value) -> str:
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"workdir": str(tmp_path / "wd"),
                             "output_dir": str(tmp_path / "out"),
                             "log_dir": str(tmp_path / "logs"),
                             "last_template": value}), encoding="utf-8")
    return str(p)


def test_default_template_is_untouched_when_not_frozen(tmp_path, monkeypatch):
    """開発実行は従来どおり（config を読みに行かない）。"""
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    args = _Args(_cfg_with_last_template(tmp_path, "user:自社様式"),
                 cli._DefaultTemplate(TPL))
    cli._resolve_default_template(args)       # 例外にならない
    assert str(args.template) == str(TPL)


def test_frozen_default_template_is_refused_for_user_templates(tmp_path,
                                                               monkeypatch):
    """利用者テンプレートを使っていたなら、黙って出荷テンプレートで処理しない。

    保存先を知っているのは GUI（Rust）だけで、直叩きの core には渡らない
    ——別実体で読むと、編集したはずの欄が反映されない結果が「正常」として出る。
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args = _Args(_cfg_with_last_template(tmp_path, "user:自社様式"),
                 cli._DefaultTemplate(TPL))
    with pytest.raises(ConfigError, match="--template"):
        cli._resolve_default_template(args)


def test_frozen_default_template_is_allowed_for_the_shipped_template(
        tmp_path, monkeypatch):
    """出荷テンプレートなら GUI の注入先と同じ実体なので既定値のまま通す。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args = _Args(_cfg_with_last_template(tmp_path, "shipped"),
                 cli._DefaultTemplate(TPL))
    cli._resolve_default_template(args)
    assert str(args.template) == str(TPL)


def test_frozen_explicit_template_is_never_refused(tmp_path, monkeypatch):
    """明示指定は常に尊重する（印が付いていないので判定に入らない）。"""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args = _Args(_cfg_with_last_template(tmp_path, "user:自社様式"), str(TPL))
    cli._resolve_default_template(args)
    assert args.template == str(TPL)


def test_frozen_default_template_is_refused_when_the_file_is_missing(
        tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    args = _Args(_cfg_with_last_template(tmp_path, "shipped"),
                 cli._DefaultTemplate(tmp_path / "no" / "such.json"))
    with pytest.raises(ConfigError, match="見つかりません"):
        cli._resolve_default_template(args)
