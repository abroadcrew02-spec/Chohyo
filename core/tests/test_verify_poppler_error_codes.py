"""issue #150 (5): verify の poppler チェック失敗に error キーが無く、
TimeoutExpired と「pdftoppm が無い」が同一イベントに潰れて区別できなかった。

固定コード3種（`timeout`／`not_found`／`failed`）を返すことを固定する。
"""
import json
import subprocess

import pytest

from chouhyo_ocr import cli
from chouhyo_ocr.paths import app_root

TPL = app_root() / "templates" / "chouhyo-v1.json"


def _cfg_path(tmp_path) -> str:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")
    return str(cfg_path)


def _poppler_event(capsys):
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines()
             if l.startswith("{")]
    return next(e for e in events if e.get("event") == "verify"
               and e.get("check") == "poppler")


def test_poppler_timeout_reports_timeout_error(tmp_path, capsys, monkeypatch):
    def _boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="pdftoppm.exe", timeout=30)

    monkeypatch.setattr(subprocess, "run", _boom)
    cli.main(["--config", _cfg_path(tmp_path), "verify", "--template", str(TPL)])

    ev = _poppler_event(capsys)
    assert ev["ok"] is False
    assert ev["error"] == "timeout"


def test_poppler_missing_binary_reports_not_found_error(tmp_path, capsys, monkeypatch):
    """pdftoppm_path() が指す実行ファイルが実在しない（未インストール等）と
    subprocess.run 自身が FileNotFoundError を出す——実際の OS 挙動で再現する。
    """
    from pathlib import Path

    from chouhyo_ocr import ingest

    monkeypatch.setattr(ingest, "pdftoppm_path",
                        lambda: Path(str(tmp_path / "no_such_pdftoppm.exe")))
    cli.main(["--config", _cfg_path(tmp_path), "verify", "--template", str(TPL)])

    ev = _poppler_event(capsys)
    assert ev["ok"] is False
    assert ev["error"] == "not_found"


def test_poppler_unexpected_error_reports_failed(tmp_path, capsys, monkeypatch):
    from chouhyo_ocr import ingest

    def _boom():
        raise RuntimeError("simulated: unexpected failure")

    monkeypatch.setattr(ingest, "pdftoppm_path", _boom)
    cli.main(["--config", _cfg_path(tmp_path), "verify", "--template", str(TPL)])

    ev = _poppler_event(capsys)
    assert ev["ok"] is False
    assert ev["error"] == "failed"


def test_poppler_success_has_no_error_key(tmp_path, capsys, monkeypatch):
    """成功時（returncode==0）は従来どおり error キーを持たない（契約は変えない）。"""
    from pathlib import Path

    from chouhyo_ocr import ingest

    class _FakeProc:
        returncode = 0

    monkeypatch.setattr(ingest, "pdftoppm_path", lambda: Path("dummy_pdftoppm.exe"))
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: _FakeProc())
    cli.main(["--config", _cfg_path(tmp_path), "verify", "--template", str(TPL)])

    ev = _poppler_event(capsys)
    assert ev["ok"] is True
    assert "error" not in ev
