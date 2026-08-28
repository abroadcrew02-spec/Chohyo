"""ファイル取り込み・PDF 展開（設計 §6.1）。

- 対象拡張子: .pdf / .jpg / .jpeg / .png。それ以外はスキップしログへ
- 帳票ID: <入力ファイル名の stem>_p<4桁ページ番号>。
  ファイル名は帳票ID・ログ・出力・GUI に表示されるため、**入力ファイル名に
  氏名等の個人情報を含めない運用を前提とする**（コードでは強制できない。
  スキャン時は連番ファイル名を使うこと・issue #10）
- Poppler は sys.frozen で分岐した絶対パスで起動（§12-C1）。stderr は
  そのままログへ流さず固定エラーコード化する（§12-C8）
"""
from __future__ import annotations

import glob
import hashlib
import subprocess
import sys
from pathlib import Path

from . import logging_safe as log
from .paths import app_root

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png"}


class IngestError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def pdftoppm_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "poppler" / "pdftoppm.exe"
    hits = sorted((app_root() / "vendor" / "poppler").glob("**/pdftoppm.exe"))
    if not hits:
        raise IngestError("POPPLER_NOT_FOUND")
    return hits[0]


def list_inputs(input_dir: str | Path) -> list[Path]:
    files = []
    for p in sorted(Path(input_dir).iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() in SUPPORTED:
            files.append(p)
        else:
            log.info("skip_unsupported", source_file=p.name)
    return files


def page_id_for(source: Path, page_no: int, taken: set[str]) -> str:
    base = f"{source.stem}_p{page_no:04d}"
    if base not in taken:
        return base
    digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{source.stem}_{digest}_p{page_no:04d}"


def expand(source: Path, dpi: int, out_dir: Path) -> list[Path]:
    """1入力ファイル → ページ画像のリスト（PDF は pdftoppm・画像はそのまま）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".pdf":
        return [source]

    prefix = out_dir / source.stem
    exe = pdftoppm_path()
    try:
        proc = subprocess.run(
            [str(exe), "-r", str(dpi), "-png", str(source), str(prefix)],
            capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise IngestError("PDF_EXPAND_TIMEOUT") from e
    if proc.returncode != 0:
        # stderr は記入値を含みうる経路ではないが、方針どおり固定コード化して捨てる
        raise IngestError("PDF_EXPAND_FAILED")
    # stem は glob エスケープ必須。scan[1].pdf（ブラウザの重複名）で [1] が
    # 文字クラス解釈され、展開成功なのに 0 件マッチ→展開失敗になる（issue #13）
    pages = sorted(out_dir.glob(f"{glob.escape(source.stem)}-*.png"))
    if not pages:
        raise IngestError("PDF_EXPAND_EMPTY")
    return pages
