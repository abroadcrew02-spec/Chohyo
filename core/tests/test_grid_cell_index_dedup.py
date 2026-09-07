"""issue #124（調査）: `grid.cell_index_of` は原子セルの5要素タプル
`(y1, x1, y2, x2, cell_residual)` を dict のキーにして候補の添字を引く。
`_grid_atomic_cells` が完全に同一の矩形（同一残差）を2件返した場合、
辞書のキー衝突で添字が上書きされ、`cells` には入っているのに
`cell_indexes` から参照されないセルが生まれる（`surviving` のフィルタも
1件に潰れる）。

このファイルは `_grid_atomic_cells` が実際に重複タプルを返すかどうかを、
実素材（sample-1・formB-1・formC-1）と合成の密な格子で確認する。

`detect_frames()` の公開結果（`FrameCandidates.cells`）を通して確認する
——`cell_index_of` は `_grid_atomic_cells` の戻り値（重複があれば `kept` にも
そのまま残る）をキーにして `cells` を1件ずつ append するため、`cells` に
同一の (rect.x, rect.y, rect.w, rect.h, residual_px) の組が2件以上あれば、
それは `atomic_raw`（＝ `_grid_atomic_cells` の戻り値）に重複タプルが
あったことの直接の証拠になる（辞書の上書きは起きても append 自体は
止まらないため、症状は「cells 内の重複」として必ず表に出る）。
"""
from collections import Counter

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr.grid import detect_frames
from chouhyo_ocr.paths import app_root

FORMB_PNG = app_root() / "testdata" / "formB" / "formB-1.png"
FORMC_PNG = app_root() / "testdata" / "formC" / "formC-1.png"
SAMPLE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"

needs_sample = pytest.mark.skipif(not SAMPLE_PNG.exists(), reason="サンプル画像が無い環境")


def _binary(path) -> "np.ndarray":
    gray = np.asarray(Image.open(path).convert("L"))
    return gray < 128


def _dense_uniform_grid(rows: int, cols: int, cell: int = 40) -> "np.ndarray":
    """升をぎっしり並べた合成格子（密なほどレール数が増え、衝突が起きやすい）。"""
    w = cols * cell + 20
    h = rows * cell + 20
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    xs = [10 + i * cell for i in range(cols + 1)]
    ys = [10 + i * cell for i in range(rows + 1)]
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill=0, width=1)
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill=0, width=1)
    return np.asarray(img) < 128


def _assert_no_duplicate_cells(result, label: str) -> None:
    """`cells` 内に同一 (rect, residual_px) の組が無いことを確認する。

    重複が1件でもあれば、その重複ぶんだけ `cell_indexes` から参照されない
    セルが生まれているはず（issue #124 の症状）。
    """
    keys = [(c.rect.x, c.rect.y, c.rect.w, c.rect.h, c.residual_px) for c in result.cells]
    counts = Counter(keys)
    dupes = {k: n for k, n in counts.items() if n > 1}
    assert not dupes, (
        f"[{label}] cells 内に重複した (rect, residual_px) がある: {dupes}"
        f"（cell_index_of の辞書キー衝突で cell_indexes から参照されない"
        f"セルが生まれている可能性）")


@needs_sample
def test_no_duplicate_atomic_cells_sample1():
    result = detect_frames(_binary(SAMPLE_PNG), dpi=300)
    _assert_no_duplicate_cells(result, "sample-1")


def test_no_duplicate_atomic_cells_formb1():
    result = detect_frames(_binary(FORMB_PNG), dpi=300)
    _assert_no_duplicate_cells(result, "formB-1")


def test_no_duplicate_atomic_cells_formc1():
    result = detect_frames(_binary(FORMC_PNG), dpi=300)
    _assert_no_duplicate_cells(result, "formC-1")


def test_no_duplicate_atomic_cells_synthetic_dense_grid():
    """合成の密な格子（20 行 × 15 列 = 300 升）で衝突しないことを確認する。

    実素材よりレール数・原子セル数を意図的に増やし、辞書キー衝突が
    起きやすい条件を作る（罫線が密なテーブルほど衝突のリスクは上がる）。
    """
    result = detect_frames(_dense_uniform_grid(20, 15), dpi=300)
    assert len(result.cells) == 300  # 20 行 × 15 列（欠けがあれば重複の疑い）
    _assert_no_duplicate_cells(result, "synthetic-dense-20x15")


def test_cell_indexes_reference_only_existing_cells():
    """提案が指す cell_indexes は、常に result.cells の実在する添字だけを指す
    （重複があれば範囲外や意図しない添字を指す可能性があるための保険）。
    """
    result = detect_frames(_binary(FORMB_PNG), dpi=300)
    n = len(result.cells)
    for s in result.suggestions:
        assert all(0 <= i < n for i in s.cell_indexes)
        assert len(set(s.cell_indexes)) == len(s.cell_indexes)  # 重複添字も無い
