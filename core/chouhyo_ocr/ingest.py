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
import re
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
    return _poppler_tool("pdftoppm.exe")


def _poppler_tool(name: str) -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "poppler" / name
    hits = sorted((app_root() / "vendor" / "poppler").glob(f"**/{name}"))
    if not hits:
        raise IngestError("POPPLER_NOT_FOUND")
    return hits[0]


def pdf_page_count(source: Path) -> int | None:
    """総ページ数（pdfinfo）。取得できなければ None（表示・範囲チェック用の補助）。"""
    try:
        proc = subprocess.run([str(_poppler_tool("pdfinfo.exe")), str(source)],
                              capture_output=True, timeout=60)
        for line in proc.stdout.decode("utf-8", errors="replace").splitlines():
            if line.lower().startswith("pages:"):
                return int(line.split(":", 1)[1].strip())
    except (IngestError, subprocess.TimeoutExpired, ValueError, OSError):
        pass
    return None


def list_inputs(input_dir: str | Path,
                skipped: list[str] | None = None) -> list[Path]:
    """入力の列挙。フォルダなら中の対応ファイル、単一ファイルならそれ1つ。

    実運用は「スキャンした PDF が数枚」のことがあり、フォルダ縛りだと
    利用者がファイルを選べない（2026-08-28 ユーザー指摘）。
    skipped を渡すと、対象外だったファイル名を追記する——呼び出し側が
    進捗イベントへ出すため（レビュー M-2: ログだけだと「total=0 の正常終了」
    にしか見えず、利用者は何が起きたか分からない）。
    """
    root = Path(input_dir)
    if root.is_file():
        if root.suffix.lower() in SUPPORTED:
            return [root]
        log.info("skip_unsupported", source_file=root.name)
        if skipped is not None:
            skipped.append(root.name)
        return []
    files = []
    for p in sorted(root.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() in SUPPORTED:
            files.append(p)
        else:
            log.info("skip_unsupported", source_file=p.name)
            if skipped is not None:
                skipped.append(p.name)
    return files


def page_id_for(source: Path, page_no: int, taken: set[str]) -> str:
    base = f"{source.stem}_p{page_no:04d}"
    if base not in taken:
        return base
    digest = hashlib.sha1(str(source.resolve()).encode("utf-8")).hexdigest()[:8]
    return f"{source.stem}_{digest}_p{page_no:04d}"


def expand(source: Path, dpi: int, out_dir: Path,
           page: int | None = None) -> list[Path]:
    """1入力ファイル → ページ画像のリスト（PDF は pdftoppm・画像はそのまま）。

    page=N なら該当ページのみ展開する（pdftoppm -f/-l）。テンプレート編集は
    位置合わせ用の1ページだけあればよく、全ページ展開（実測 約13秒/2頁）を
    1ページ分（約5秒）に抑える（2026-08-28 ユーザー要望）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".pdf":
        return [source]

    prefix = out_dir / source.stem
    # 展開前に同一 stem の残骸を必ず消す（issue #20）。out_dir は実行をまたいで
    # 永続するため、同名 PDF を差し替えて再実行すると旧展開分が混ざり、
    # **別の帳票のデータが新ファイル名で送信・出力される**（実測: 12頁→2頁の
    # 差し替えで14頁化。ゼロ埋め幅の差で順序も入り乱れる）
    stale_pat = re.compile(rf"{re.escape(source.stem)}-\d+")
    for old in out_dir.glob(f"{glob.escape(source.stem)}-*.png"):
        if stale_pat.fullmatch(old.stem):
            old.unlink()

    exe = pdftoppm_path()
    args = [str(exe), "-r", str(dpi), "-png"]
    if page is not None:
        args += ["-f", str(page), "-l", str(page)]
    try:
        proc = subprocess.run(
            args + [str(source), str(prefix)],
            capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise IngestError("PDF_EXPAND_TIMEOUT") from e
    if proc.returncode != 0:
        # stderr は記入値を含みうる経路ではないが、方針どおり固定コード化して捨てる
        raise IngestError("PDF_EXPAND_FAILED")
    # stem は glob エスケープ必須。scan[1].pdf（ブラウザの重複名）で [1] が
    # 文字クラス解釈され、展開成功なのに 0 件マッチ→展開失敗になる（issue #13）。
    # さらに <stem>-<数字>.png に厳密一致させる: a.pdf と a-1.pdf が同居すると
    # a-* が a-1-1.png まで拾い、ページ数と行数の対応が崩れる（レビュー N-13）
    # page 指定時は該当番号のみに絞る（過去の全ページ展開の残骸を拾わない）。
    # pdftoppm はゼロ埋めすることがあるため 0* を許す
    num = rf"0*{page}" if page is not None else r"\d+"
    pat = re.compile(rf"{re.escape(source.stem)}-(?P<no>{num})")
    # 辞書順ではなくページ番号の数値順（issue #20: ゼロ埋め幅が総ページ数で
    # 変わるため、文字列 sorted() は 1,10,11,...,2 の並びになりうる）
    hits = []
    for p in out_dir.glob(f"{glob.escape(source.stem)}-*.png"):
        m = pat.fullmatch(p.stem)
        if m:
            hits.append((int(m.group("no")), p))
    pages = [p for _no, p in sorted(hits)]
    if not pages:
        raise IngestError("PDF_EXPAND_EMPTY")
    return pages
