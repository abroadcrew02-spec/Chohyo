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
import io
import itertools
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

from . import logging_safe as log
from .paths import app_root

SUPPORTED = {".pdf", ".jpg", ".jpeg", ".png"}

# ページ画像の PNG 圧縮レベル（issue #50）。
# poppler 内蔵の PNG エンコーダが遅く、`pdftoppm -png` は同じ PDF・同じ解像度の
# ppm 出力に比べ約 7〜10 倍の時間を使う（実測 2026-08-31・2ページ 300dpi:
# -png 14.73s / -ppm 1.40s）。展開は 1 ページごとに必ず通る経路で、月 6,000 画像なら
# ここだけで概算 8 時間を使う。
#
# そこで ppm で受け取って Pillow で PNG 化する。ppm も PNG も可逆形式なので、
# **画素は現行の -png 出力と完全一致する**（全ページ・全画素で差分 0 を実測確認）。
# 入力が同一なら OCR 結果も同一で、精度への影響は無い（API 送信による検証は不要）。
#
# レベルの選定（実測・2490x3510・現行 -png を基準）:
#   level 0: 4.61x 速い / 容量 10.89x   level 1: 4.17x / 1.47x
#   level 3: 3.91x 速い / 容量  1.04x   level 6: 3.46x / 1.00x
# level 3 が「容量を現行水準に保ったまま最大の速度」。align.py が整列画像で
# level 1 を選んだのは中間データで容量を許容できるためで、ここは永続する
# ページ画像なので容量側に寄せる。
PNG_COMPRESS_LEVEL = 3


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


def _render_page_ppm(source: Path, dpi: int, page_no: int) -> bytes | None:
    """PDF の 1 ページを ppm（無圧縮）で受け取る。ページが無ければ None。

    出力プレフィックスを渡さないと pdftoppm は stdout へ書く。中間の ppm を
    ディスクに置かずに済む（ppm は約 25MB/頁で、置くとページ数ぶん積み上がる）。
    """
    exe = pdftoppm_path()
    args = [str(exe), "-r", str(dpi), "-f", str(page_no), "-l", str(page_no),
            str(source)]
    try:
        proc = subprocess.run(args, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise IngestError("PDF_EXPAND_TIMEOUT") from e
    if proc.returncode != 0:
        # stderr は記入値を含みうる経路ではないが、方針どおり固定コード化して捨てる
        raise IngestError("PDF_EXPAND_FAILED")
    return proc.stdout or None


def expand(source: Path, dpi: int, out_dir: Path,
           page: int | None = None) -> list[Path]:
    """1入力ファイル → ページ画像のリスト（PDF は pdftoppm・画像はそのまま）。

    page=N なら該当ページのみ展開する（pdftoppm -f/-l）。テンプレート編集は
    位置合わせ用の1ページだけあればよく、全ページ展開を1ページ分に抑える
    （2026-08-28 ユーザー要望）。

    ページ画像は ppm で受け取って Pillow で PNG 化する（issue #50・
    PNG_COMPRESS_LEVEL の項を参照）。1ページごとに pdftoppm を起動するため
    ページ数の多い PDF では起動コストが積むが、それを含めても現行より速い
    （実測 2026-08-31・2ページ 300dpi: 現行 -png 20.11s → 本方式 4.47s）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() != ".pdf":
        return [source]

    # 展開前に同一 stem の残骸を必ず消す（issue #20）。out_dir は実行をまたいで
    # 永続するため、同名 PDF を差し替えて再実行すると旧展開分が混ざり、
    # **別の帳票のデータが新ファイル名で送信・出力される**（実測: 12頁→2頁の
    # 差し替えで14頁化。ゼロ埋め幅の差で順序も入り乱れる）
    # page 指定時は該当番号だけを消す（レビュー M-4: 旧実装は全番号を消し、
    # expand(page=2) が page 1 の PNG まで巻き添えにしていた）。
    # 削除できない（ロック中など）ファイルは無視する——ここで落とすと
    # IngestError でなく生の例外が run 全体を止める
    stale_num = rf"0*{page}" if page is not None else r"\d+"
    stale_pat = re.compile(rf"{re.escape(source.stem)}-{stale_num}")
    # テンプレート編集画面（cli.cmd_expand_page）が作る位置合わせ済みの下地
    # 「<stem>-p{page:04d}-aligned.png」も同じ stem の残骸として扱う（#60 M-7）。
    # 固定名で上書きされるため同じページを開き直しても増えないが、別ページ
    # 番号を開くたびに新しい -aligned.png が増え、従来の <stem>-<数字> 完全一致
    # の対象外だったため purge するまで帳票原本の複製（個人情報）が滞留していた
    aligned_num = rf"p0*{page}" if page is not None else r"p\d+"
    aligned_pat = re.compile(rf"{re.escape(source.stem)}-{aligned_num}-aligned")
    for old in out_dir.glob(f"{glob.escape(source.stem)}-*.png"):
        if stale_pat.fullmatch(old.stem) or aligned_pat.fullmatch(old.stem):
            try:
                old.unlink()
            except OSError:
                log.info("stale_page_unlink_failed", source_file=old.name)

    total = pdf_page_count(source)
    # pdftoppm はゼロ埋め幅を総ページ数で決める。同じ名前になるよう合わせる
    # （既存の展開結果と混在しても stale 掃除・番号パースが従来どおり効く）
    width = len(str(total)) if total else 1
    if page is not None:
        if total is not None and not (1 <= page <= total):
            raise IngestError("PDF_EXPAND_EMPTY")
        numbers: list[int] | None = [page]
    elif total is not None:
        numbers = list(range(1, total + 1))
    else:
        # pdfinfo が総ページ数を返さない（破損 PDF など）。1 から順に試し、
        # 空応答で終端とみなす。破損していれば 1 ページ目で失敗する
        numbers = None

    pages: list[Path] = []
    for no in (numbers if numbers is not None else itertools.count(1)):
        data = _render_page_ppm(source, dpi, no)
        if not data:
            if numbers is None:
                break          # 総ページ数不明時の終端
            raise IngestError("PDF_EXPAND_FAILED")
        dst = out_dir / f"{source.stem}-{no:0{width}d}.png"
        try:
            with Image.open(io.BytesIO(data)) as img:
                img.load()
                img.save(dst, format="PNG", compress_level=PNG_COMPRESS_LEVEL)
        except Exception as e:  # noqa: BLE001
            # 変換失敗は展開失敗として扱う。例外メッセージは捨てる
            # （PIL の例外はファイルパスを含み、パスには入力ファイル名が乗る）
            raise IngestError("PDF_EXPAND_FAILED") from e
        pages.append(dst)

    if not pages:
        raise IngestError("PDF_EXPAND_EMPTY")
    return pages
