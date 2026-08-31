"""PDF 展開の高速化が出力を変えていないことの回帰テスト（issue #50）。

`expand()` は pdftoppm の遅い PNG エンコーダを避け、ppm を stdout で受け取って
Pillow で PNG 化する。この最適化が成立する前提は **画素が現行の `-png` 出力と
完全に一致すること** で、一致する限り Vision へ渡る入力は同一であり OCR 結果も
変わらない。ここが崩れると「速いが読み取り結果が変わる」最悪の状態になるため、
不変条件としてテストで固定する。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chouhyo_ocr.ingest import IngestError, expand, pdftoppm_path  # noqa: E402


def _make_pdf(path: Path, pages: int, size=(200, 280)) -> None:
    """複数ページの PDF を作る（ページごとに濃度を変えて区別できるようにする）。"""
    frames = [Image.new("L", size, 255 - i * 7) for i in range(pages)]
    frames[0].save(path, save_all=True, append_images=frames[1:])


def _reference_png(pdf: Path, dpi: int, out: Path) -> list[Path]:
    """現行実装が使っていた `pdftoppm -png` を直接呼んで基準画像を作る。"""
    out.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(pdftoppm_path()), "-r", str(dpi), "-png", str(pdf), str(out / "ref")],
        capture_output=True, timeout=300)
    assert proc.returncode == 0, f"基準の展開に失敗した rc={proc.returncode}"
    hits = sorted(out.glob("ref-*.png"),
                  key=lambda p: int(p.stem.rsplit("-", 1)[1]))
    assert hits, "基準画像が生成されなかった"
    return hits


@pytest.mark.parametrize("pages", [1, 3])
def test_expand_is_pixel_identical_to_pdftoppm_png(tmp_path, pages):
    """展開結果が `pdftoppm -png` と全画素で一致する（issue #50 の安全性の根拠）。

    一致していれば Vision への入力が変わらないので、OCR 精度への影響は無い。
    """
    src = tmp_path / "doc.pdf"
    _make_pdf(src, pages)

    ref = _reference_png(src, 72, tmp_path / "ref")
    got = expand(src, dpi=72, out_dir=tmp_path / "pages")

    assert len(got) == len(ref) == pages

    for r, g in zip(ref, got):
        a = np.asarray(Image.open(r).convert("RGB"), dtype=np.int16)
        b = np.asarray(Image.open(g).convert("RGB"), dtype=np.int16)
        assert a.shape == b.shape, f"寸法が違う {a.shape} != {b.shape}"
        assert int(np.abs(a - b).max()) == 0, (
            f"{g.name} の画素が基準と一致しない。"
            "展開の最適化が読み取り結果を変えている")


def test_expand_leaves_no_intermediate_files(tmp_path):
    """中間の ppm を残さない（1ページ約 25MB でディスクを食い潰すため）。"""
    src = tmp_path / "doc.pdf"
    _make_pdf(src, 3)
    out = tmp_path / "pages"
    got = expand(src, dpi=72, out_dir=out)

    produced = sorted(p.name for p in out.iterdir())
    assert produced == sorted(p.name for p in got), (
        f"想定外のファイルが残っている: {produced}")
    assert all(p.suffix == ".png" for p in out.iterdir())


def test_expand_returns_pages_in_numeric_order(tmp_path):
    """ページは数値順で返る（ゼロ埋め幅が変わっても辞書順に崩れない・issue #20）。"""
    src = tmp_path / "doc.pdf"
    _make_pdf(src, 12)
    got = expand(src, dpi=36, out_dir=tmp_path / "pages")
    nos = [int(p.stem.rsplit("-", 1)[1]) for p in got]
    assert nos == list(range(1, 13))


def test_expand_page_selection_only_writes_that_page(tmp_path):
    """page=N は該当ページだけを書く（テンプレート編集の 1 ページ展開）。"""
    src = tmp_path / "doc.pdf"
    _make_pdf(src, 5)
    out = tmp_path / "pages"
    got = expand(src, dpi=36, out_dir=out, page=3)
    assert len(got) == 1
    assert int(got[0].stem.rsplit("-", 1)[1]) == 3
    assert sorted(p.name for p in out.iterdir()) == [got[0].name]


def test_expand_rejects_out_of_range_page(tmp_path):
    """存在しないページ番号は展開失敗として扱う（無言で空を返さない）。"""
    src = tmp_path / "doc.pdf"
    _make_pdf(src, 2)
    with pytest.raises(IngestError):
        expand(src, dpi=36, out_dir=tmp_path / "pages", page=99)


def test_expand_reports_broken_pdf_as_failure(tmp_path):
    """破損 PDF は IngestError になる（生の例外で run 全体を止めない）。"""
    src = tmp_path / "broken.pdf"
    src.write_bytes(b"%PDF-1.4 this is not a valid pdf")
    with pytest.raises(IngestError):
        expand(src, dpi=36, out_dir=tmp_path / "pages")
