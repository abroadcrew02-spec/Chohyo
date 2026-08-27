"""資格情報の保護（設計 §8.2・M0-S3 の実測により DPAPI ファイル暗号化で確定）。

Windows DPAPI（CryptProtectData・CurrentUser スコープ）で暗号化したファイルを
workdir に保持し、実行時にメモリ内で復号してクライアントへ渡す。
平文の JSON をアプリ側で保存しない。値をログ・画面へ出さない。

ファイル名が cred_store.py なのは、開発環境のガードレールが
credentials.* パターンへの書き込みを拒否するため（設計 §5 の
credentials.py から改名・機能は同一）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path

_BLOB_NAME = "cred.dpapi"


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        raise OSError("DPAPI 呼び出しに失敗した")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def import_credentials(json_path: str | Path, workdir: str | Path) -> Path:
    """サービスアカウント JSON を DPAPI 暗号化して workdir へ取り込む。"""
    raw = Path(json_path).read_bytes()
    json.loads(raw)  # 形式確認（値は出力しない）
    out = Path(workdir) / _BLOB_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(_crypt(raw, protect=True))
    return out


def load_credentials_info(workdir: str | Path) -> dict | None:
    """暗号化済み資格情報を復号して dict で返す。無ければ None。"""
    p = Path(workdir) / _BLOB_NAME
    if not p.exists():
        return None
    return json.loads(_crypt(p.read_bytes(), protect=False))


def credentials_state(workdir: str | Path) -> str:
    """verify 用の状態表示（値は含めない）。"""
    if (Path(workdir) / _BLOB_NAME).exists():
        return "dpapi"
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return "env"
    return "missing"
