"""issue #142: expand-page / match-templates / detect-frames の import 失敗が
無言で main() の汎用ハンドラへ抜けず、必ずコマンド固有のイベントを返す。

`cmd_expand_page` などは `PIL`/`format_check`/`align`/`template` を関数内
import している（cli.py の意図的な遅延 import 方針・冒頭のモジュール
docstring を参照——numpy/openpyxl 等の重い依存を使わないコマンドの起動を
遅くしないため、モジュール先頭への移動はしない）。この import が
PyInstaller の hidden import 漏れ等で失敗すると、以前は:

- `cmd_expand_page`: import が `except TemplateError:` 等の内側にあったため、
  import 失敗時に `TemplateError` という名前自体が未定義のままとなり、
  except 節の評価そのものが NameError を起こして関数を素通りした
- `cmd_match_templates`/`cmd_detect_frames`: import がどの try にも入って
  いなかったため、素の ImportError がそのまま関数を素通りした

いずれも main() の汎用 `except Exception` に落ち、対応する `*_page`/
`*_templates`/`*_frames` イベントを一つも出さないまま exit 1 していた
（GUI は結果待ちの表示のまま止まる）。

`builtins.__import__` を差し替えて "PIL" の import だけを失敗させ、
各コマンドが exit 0 ＋ ok:false のイベントを返すことを固定する。

差し替え前に `chouhyo_ocr.ingest`（内部で `from PIL import Image` を
モジュール先頭で行う）を含む依存を明示的に一度 import しておく——モジュール
本体の実行は初回 import 時の1回きりなので、それ以降にモジュールを再
import してもこの中の "PIL" import は再実行されない。先に warm-up しておか
ないと、まだ import されていない内部モジュールの初回 import 経由で
"PIL" の import が意図せず巻き込まれ、テストが検証したい箇所（各コマンドの
関数内 try）より前の場所で落ちてしまう。
"""
import builtins
import json

import pytest

# warm-up: PIL 経由の依存を先に本物のまま import し尽くしておく
import chouhyo_ocr.align  # noqa: F401
import chouhyo_ocr.format_check  # noqa: F401
import chouhyo_ocr.grid  # noqa: F401
import chouhyo_ocr.ingest  # noqa: F401
import chouhyo_ocr.template  # noqa: F401
from chouhyo_ocr import cli


def _cfg_path(tmp_path) -> "str":
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")
    return str(cfg_path)


def _events(capsys):
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


@pytest.fixture
def boom_pil_import(monkeypatch):
    """"PIL" の import だけを失敗させる（他モジュールの import は素通し）。"""
    real_import = builtins.__import__

    def _boom(name, *a, **kw):
        if name == "PIL":
            raise ImportError("simulated: PIL hidden import missing")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _boom)


def test_expand_page_import_failure_still_emits_event(tmp_path, capsys, boom_pil_import):
    """import 失敗でも expand_page イベントを1行返す（契約は変えない）。"""
    inp = tmp_path / "input"; inp.mkdir()
    src = inp / "page.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)  # 中身は問わない
    rc = cli.main(["--config", _cfg_path(tmp_path), "expand-page",
                   "--input", str(src), "--page", "1"])
    assert rc == 0

    ev = next(e for e in _events(capsys) if e["event"] == "expand_page")
    assert ev["ok"] is True          # 生画像で続行する契約は変えない
    assert ev["aligned"] is False
    assert ev["reason"] == "other"

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "expand_page_import_failed" in app_log


def test_match_templates_import_failure_still_emits_event(tmp_path, capsys, boom_pil_import):
    """import 失敗でも match_templates イベントを1行返す（error: internal）。"""
    inp = tmp_path / "input"; inp.mkdir()
    src = inp / "page.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    shipped = tmp_path / "shipped.json"
    shipped.write_text("{}", encoding="utf-8")
    rc = cli.main(["--config", _cfg_path(tmp_path), "match-templates",
                   "--input", str(src), "--page", "1", "--shipped", str(shipped)])
    assert rc == 0

    ev = next(e for e in _events(capsys) if e["event"] == "match_templates")
    assert ev == {"event": "match_templates", "ok": False, "error": "internal"}

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "match_templates_failed" in app_log


def test_detect_frames_import_failure_still_emits_event(tmp_path, capsys, boom_pil_import):
    """import 失敗でも detect_frames イベントを1行返す（error: internal）。"""
    inp = tmp_path / "input"; inp.mkdir()
    src = inp / "page.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 32)
    rc = cli.main(["--config", _cfg_path(tmp_path), "detect-frames",
                   "--input", str(src), "--page", "1"])
    assert rc == 0

    ev = next(e for e in _events(capsys) if e["event"] == "detect_frames")
    assert ev == {"event": "detect_frames", "ok": False, "error": "internal"}

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "detect_frames_failed" in app_log
