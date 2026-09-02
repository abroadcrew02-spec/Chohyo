"""L-S2: pdftoppm/pdfinfo へ渡すパス引数の先頭 `-` 対策（CWE-88）。

入力ファイル名が `-` から始まると、getopt 系の引数パーサ（pdftoppm/pdfinfo）
がオプションと誤認し、後続引数を巻き込んで意図しない動作をしうる。
`ingest._safe_path_arg` が相対パスの先頭に `./` を付けて中和することを、
(1) 関数単体 (2) 実際に subprocess.run へ渡る args リストの両方で固定する。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from chouhyo_ocr import ingest
from chouhyo_ocr.ingest import _safe_path_arg


def test_safe_path_arg_prefixes_leading_dash():
    assert _safe_path_arg(Path("-evil.pdf")) == "./-evil.pdf"


def test_safe_path_arg_leaves_normal_paths_untouched():
    assert _safe_path_arg(Path("normal.pdf")) == "normal.pdf"


def test_safe_path_arg_leaves_absolute_paths_untouched():
    # 絶対パス（ドライブレター）は "-" から始まらないため対象外
    p = Path("C:/tmp/-looks-like-a-flag.pdf")
    assert _safe_path_arg(p) == str(p)


def _fake_completed(stdout=b"", returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=b"")


def test_render_page_ppm_neutralizes_leading_dash_source(monkeypatch, tmp_path):
    """_render_page_ppm が subprocess.run へ渡す args の末尾（source）が中和される。"""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _fake_completed(stdout=b"P6\n1 1\n255\n\xff\xff\xff")

    monkeypatch.setattr(ingest, "pdftoppm_path", lambda: Path("dummy_pdftoppm.exe"))
    monkeypatch.setattr(subprocess, "run", fake_run)

    src = Path("-evil.pdf")
    ingest._render_page_ppm(src, dpi=300, page_no=1)

    assert captured["args"][-1] == "./-evil.pdf"
    assert not captured["args"][-1].startswith("-")


def test_pdf_page_count_neutralizes_leading_dash_source(monkeypatch):
    """pdf_page_count が subprocess.run へ渡す args の末尾（source）が中和される。"""
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        return _fake_completed(stdout=b"Pages: 3\n")

    monkeypatch.setattr(ingest, "_poppler_tool", lambda name: Path("dummy_pdfinfo.exe"))
    monkeypatch.setattr(subprocess, "run", fake_run)

    src = Path("-evil.pdf")
    n = ingest.pdf_page_count(src)

    assert captured["args"][-1] == "./-evil.pdf"
    assert n == 3
