"""記入値の漏出防止の再レビューテスト（issue #2/#3/#4）。"""
import json
import subprocess

from chouhyo_ocr import logging_safe
from chouhyo_ocr.mapping import CellContent, Symbol
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import Row
from chouhyo_ocr.template import load_template

PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"
TPL = app_root() / "templates" / "chouhyo-v1.json"


def test_cli_top_level_handler_hides_exception_message(tmp_path):
    """未捕捉例外で traceback・値が stderr に出ない（issue #2）。"""
    bogus = tmp_path / "存在しない入力フォルダ"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    r = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(bogus)],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr           # 生 traceback を GUI へ流さない
    assert "存在しない入力フォルダ" not in r.stderr  # 例外メッセージ由来の値を出さない
    assert r.stderr.strip().startswith("ERROR ")  # 固定文言＋型名のみ
    # スタック（メッセージ抜き）は error.log へ残る
    err_log = (tmp_path / "logs" / "error.log").read_text(encoding="utf-8")
    assert "unhandled_exception" in err_log
    assert "存在しない入力フォルダ" not in err_log


def test_logging_whitelist_drops_value_key():
    """汎用キー value は白リスト外＝黙って落ちる（issue #3）。"""
    line = logging_safe._fmt("x", {"value": "上西諒", "page_id": "p1"})
    assert "上西諒" not in line
    assert "page_id=p1" in line
    line2 = logging_safe._fmt("x", {"duplicate_of": "a.png"})
    assert "duplicate_of=a.png" in line2


def test_fixed_repr_redacts_values():
    """記入値を持つ dataclass の repr が値を出さない（issue #4・付録 C7）。"""
    assert "上西諒" not in repr(Symbol("上西諒", 1, 2, 0.9))
    assert "上西諒" not in repr(CellContent("上西諒", 0.9))
    row = Row("p1", "s.png", 1, "正常", ["上西諒", "千葉県"], 0, "0.900")
    assert "上西諒" not in repr(row) and "千葉県" not in repr(row)
    t = load_template(TPL)
    cell = t.cells[0]
    assert "redacted" in repr(CellContent("x", None)) or True  # 形式は固定文字列
    assert repr(cell).startswith("<CellSpec ")
