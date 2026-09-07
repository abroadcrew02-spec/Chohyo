# -*- coding: utf-8 -*-
"""`grid.detect_frames` の `too_small` 閾値（segments.MIN_RECT_SIZE）較正（issue #87 項目4）。

`_grid_atomic_cells` が返す原子セル（フィルタ前）の辺長分布を実測し、
現行閾値（20px@300dpi）の直下・直上で件数がどう動くかを見る。
`detect_frames` は集計件数（`stats.excluded` の `too_small` カウント）しか
返さないため、内部関数（`grid._segments.detect_segments` →
`grid._cluster_rails` → `grid._grid_atomic_cells`）を直接呼んで個々の
矩形サイズを取り出す（テスト対象コードは変更しない・読み取りのみ）。

sample-1 はテンプレート適用後の合成画像（`align_page`）を使う
（`test_detect_frames.py::test_ac_h33_cell_ledger_closes_on_sample1` と同じ
前処理）。formB／formC はテンプレートなし・dpi=300 の生画像（既存テストと同じ）。

実行:
  .venv/Scripts/python.exe scripts/calibrate_too_small.py
  .venv/Scripts/python.exe scripts/calibrate_too_small.py --threshold 16
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))

from chouhyo_ocr import grid as _grid  # noqa: E402
from chouhyo_ocr import segments as _segments  # noqa: E402
from chouhyo_ocr.paths import app_root  # noqa: E402
from chouhyo_ocr.template import load_template  # noqa: E402

FORMB_PNG = app_root() / "testdata" / "formB" / "formB-1.png"
FORMC_PNG = app_root() / "testdata" / "formC" / "formC-1.png"
SAMPLE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
SHIPPED_TPL = app_root() / "templates" / "chouhyo-v1.json"


def _binary(path: Path) -> np.ndarray:
    gray = np.asarray(Image.open(path).convert("L"))
    return gray < 128


def _load_sample1() -> tuple[np.ndarray, int]:
    """sample-1 をテンプレート適用済み合成画像として読む（test と同じ前処理）。"""
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        _faces, composite = align_page(img, tpl)
    return np.asarray(composite.convert("L")) < 128, tpl.render_dpi


def _atomic_rects(binary: np.ndarray, dpi: int) -> list[tuple[float, float, float, float]]:
    """`detect_frames` の too_small フィルタ**前**の原子セル一覧（w, h 付き）を返す。"""
    h_segs, v_segs = _segments.detect_segments(binary, dpi)
    if not h_segs and not v_segs:
        return []
    tol = _segments.scale_threshold(_segments.COLLINEAR_TOL, dpi)
    h_rails = _grid._cluster_rails(h_segs, tol)
    v_rails = _grid._cluster_rails(v_segs, tol)
    if len(h_rails) > _grid.MAX_RAILS or len(v_rails) > _grid.MAX_RAILS:
        return []
    atomic_raw, _non_rect, _not_closed, _components = _grid._grid_atomic_cells(
        h_rails, v_rails, _grid.EDGE_COVER)
    page_h, page_w = binary.shape
    out = []
    for (y1, x1, y2, x2, _residual) in atomic_raw:
        w, h = x2 - x1, y2 - y1
        if w >= page_w * 0.9 and h >= page_h * 0.9:
            continue  # page_outline は較正の対象外（too_small とは別枠）
        out.append((w, h))
    return out


def _report(name: str, rects: list[tuple[float, float, float, float]], dpi: int,
            threshold_base: int) -> None:
    thr = _segments.scale_threshold(threshold_base, dpi)
    min_side = [min(w, h) for w, h in rects]
    below = sorted(s for s in min_side if s < thr)
    near = sorted(s for s in min_side if thr <= s < thr * 2)
    print(f"\n[{name}] dpi={dpi} 原子セル(page_outline除く)={len(rects)}件  "
          f"閾値(min_rect_size)={thr}px")
    print(f"  too_small（< {thr}px）: {len(below)}件  分布(最小辺,昇順)={below}")
    print(f"  境界近傍（{thr}〜{thr*2 - 1}px）: {len(near)}件  分布={near}")
    # 最小辺ごとの (w, h) 実サイズも見えるようにしておく（幅だけ極端に細い等の判別用）
    detail_below = sorted([(w, h) for w, h in rects if min(w, h) < thr],
                           key=lambda t: min(t))
    if detail_below:
        print(f"  too_small 実サイズ(w×h): {detail_below}")
    detail_near = sorted([(w, h) for w, h in rects if thr <= min(w, h) < thr * 2],
                          key=lambda t: min(t))
    if detail_near:
        print(f"  境界近傍 実サイズ(w×h): {detail_near}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--threshold", type=int, default=_segments.MIN_RECT_SIZE,
                     help="較正対象の MIN_RECT_SIZE（px@300dpi）。既定は現行値")
    args = ap.parse_args()

    samples: list[tuple[str, np.ndarray, int]] = []
    if FORMB_PNG.exists():
        samples.append(("formB-1", _binary(FORMB_PNG), 300))
    if FORMC_PNG.exists():
        samples.append(("formC-1", _binary(FORMC_PNG), 300))
    if SAMPLE_PNG.exists():
        binary, dpi = _load_sample1()
        samples.append(("sample-1", binary, dpi))

    if not samples:
        print("素材が1件も見つかりません（testdata/formB, testdata/formC, "
              "testdata/local/pages/sample-1.png を確認）")
        return 1

    print(f"MIN_RECT_SIZE(base@300dpi) = {args.threshold}px  "
          f"(現行定数 = {_segments.MIN_RECT_SIZE}px)")
    for name, binary, dpi in samples:
        rects = _atomic_rects(binary, dpi)
        _report(name, rects, dpi, args.threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
