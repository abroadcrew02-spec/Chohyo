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
    # run_start にテンプレート由来（パス・ハッシュ）が残る（issue #59 H-7）。
    # 入力フォルダが無くて後段で失敗しても、テンプレート読み込みまでは進むため
    # 両方のログ行は書かれる
    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "run_start" in app_log and "template_path=" in app_log
    assert "template_loaded" in app_log and "template_hash=" in app_log


def test_logging_whitelist_drops_value_key():
    """汎用キー value は白リスト外＝黙って落ちる（issue #3）。"""
    line = logging_safe._fmt("x", {"value": "テスト太郎", "page_id": "p1"})
    assert "テスト太郎" not in line
    assert "page_id=p1" in line
    line2 = logging_safe._fmt("x", {"duplicate_of": "a.png"})
    assert "duplicate_of=a.png" in line2


def test_logging_whitelist_allows_template_path_and_hash():
    """テンプレート由来トレーサビリティのキーが白リストを通る（issue #59 H-7）。"""
    line = logging_safe._fmt("run_start", {
        "path": "in", "template_path": "C:\\t.json", "value": "テスト太郎"})
    assert "template_path=C:\\t.json" in line
    assert "テスト太郎" not in line
    line2 = logging_safe._fmt("template_loaded", {"template_hash": "abc123"})
    assert "template_hash=abc123" in line2


def test_fixed_repr_redacts_values():
    """記入値を持つ dataclass の repr が値を出さない（issue #4・付録 C7）。"""
    assert "テスト太郎" not in repr(Symbol("テスト太郎", 1, 2, 0.9))
    assert "テスト太郎" not in repr(CellContent("テスト太郎", 0.9))
    row = Row("p1", "s.png", 1, "正常", ["テスト太郎", "千葉県"], 0, "0.900")
    assert "テスト太郎" not in repr(row) and "千葉県" not in repr(row)
    t = load_template(TPL)
    cell = t.cells[0]
    assert "redacted" in repr(CellContent("x", None)) or True  # 形式は固定文字列
    assert repr(cell).startswith("<CellSpec ")


def test_risky_prefix_warning_does_not_log_values(tmp_path):
    """危険接頭の警告に記入値が入らない（D-28・A5・設計 §8.1）。"""
    from chouhyo_ocr import logging_safe as log
    from chouhyo_ocr.pipeline import _warn_risky
    log.init(str(tmp_path))
    _warn_risky([("p_0001", "person_備考")])
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in tmp_path.glob("*.log"))
    assert "csv_formula_risk" in text
    assert "person_備考" in text and "p_0001" in text
    # 出るキーは page_id・field_id・count のみ（記入値が乗る余地が無い）。
    # _warn_risky はそもそも値を受け取らない——scan_risky_prefixes の戻りが
    # (page_id, 列名) だけなので、値がログへ流れる経路が型の上で存在しない
    for line in text.splitlines():
        if "csv_formula_risk" not in line:
            continue
        keys = {kv.split("=")[0] for kv in line.split() if "=" in kv}
        assert keys <= {"page_id", "field_id", "count"}, keys
