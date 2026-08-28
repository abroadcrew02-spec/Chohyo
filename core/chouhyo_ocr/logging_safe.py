"""ログ（設計 §8.1）。`import logging` はこのモジュールに限る（§12-C6）。

帳票の記入値は一切書かない。出力してよいのは 入力ファイル名・ページ番号・
帳票ID・項目ID・処理ステップ名・エラーコード・信頼度の数値・設定値・件数のみ。
許可キー以外は黙って落とす（型で守れない書き方への最後の網）。
"""
from __future__ import annotations

import logging
from pathlib import Path

_ALLOWED_KEYS = {
    "source_file", "page_no", "page_id", "field_id", "step", "error_code",
    "conf", "count", "duplicate_of", "path", "state", "status", "attempt",
}

_app: logging.Logger | None = None
_err: logging.Logger | None = None


def init(log_dir: str | Path) -> None:
    global _app, _err
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    _app = logging.getLogger("chouhyo.app")
    _app.setLevel(logging.INFO)
    _app.handlers.clear()
    h = logging.FileHandler(d / "app.log", encoding="utf-8")
    h.setFormatter(fmt)
    _app.addHandler(h)

    _err = logging.getLogger("chouhyo.error")
    _err.setLevel(logging.WARNING)
    _err.handlers.clear()
    h2 = logging.FileHandler(d / "error.log", encoding="utf-8")
    h2.setFormatter(fmt)
    _err.addHandler(h2)


def _fmt(event: str, fields: dict) -> str:
    safe = {k: v for k, v in fields.items() if k in _ALLOWED_KEYS}
    body = " ".join(f"{k}={v}" for k, v in sorted(safe.items()))
    return f"{event} {body}".rstrip()


def info(event: str, **fields) -> None:
    if _app:
        _app.info(_fmt(event, fields))


def error(event: str, **fields) -> None:
    if _err:
        _err.error(_fmt(event, fields))
    if _app:
        _app.error(_fmt(event, fields))


def error_trace(error_code: str, stack: str) -> None:
    """未捕捉例外のスタックを error.log へ残す（issue #2）。

    stack は traceback.format_tb の出力（ファイル/行/関数とソース行のみ）を
    想定する。例外メッセージ本文は帳票の値を含みうるため受け取らない。
    """
    if _err:
        _err.error(f"unhandled_exception error_code={error_code}\n{stack.rstrip()}")
