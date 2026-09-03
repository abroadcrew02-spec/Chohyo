"""segments.py: 線分抽出（端点付き）のテスト（AC-F55・issue #73 (b)）。

`detect_segments` は `projection.line_positions`（射影ピークの中心座標のみ）
とは別の新しい原始関数——端点を持つ線分を返す。`projection.py` を1行も
参照しないため、`estimate_shift` の検出条件（既存の位置合わせテスト）には
一切影響しない。その確認は本ファイルではなく、既存
`test_alignment_robustness.py`・`test_page_size_guard.py` の全緑で行う
（AC-F55 の後半）。
"""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr.segments import (
    GAP_BRIDGE,
    HOLE_MAX,
    MIN_SEG_LEN,
    Segment,
    detect_segments,
    scale_threshold,
)


def _rect_binary(w: int, h: int, rect: tuple[int, int, int, int],
                 width: int = 2) -> "np.ndarray":
    """白背景に矩形の罫線（4辺）だけを描いた二値配列（True=インク）を作る。"""
    img = Image.new("L", (w, h), 255)
    draw = ImageDraw.Draw(img)
    x, y, rw, rh = rect
    draw.rectangle((x, y, x + rw, y + rh), outline=0, width=width)
    return np.asarray(img) < 128


def test_detect_segments_finds_rectangle_edges():
    """既知の位置に矩形を描く → 4本の線分が端点付きで ±2px 一致する（AC-F55）。"""
    binary = _rect_binary(800, 600, (100, 100, 400, 200))
    h_segs, v_segs = detect_segments(binary, dpi=300)

    assert len(h_segs) == 2
    assert len(v_segs) == 2

    h_positions = sorted(s.pos for s in h_segs)
    assert abs(h_positions[0] - 100) <= 2
    assert abs(h_positions[1] - 300) <= 2
    for s in h_segs:
        assert s.kind == "h"
        assert abs(s.start - 100) <= 2
        assert abs(s.end - 500) <= 2

    v_positions = sorted(s.pos for s in v_segs)
    assert abs(v_positions[0] - 100) <= 2
    assert abs(v_positions[1] - 500) <= 2
    for s in v_segs:
        assert s.kind == "v"
        assert abs(s.start - 100) <= 2
        assert abs(s.end - 300) <= 2


def test_detect_segments_empty_image_returns_nothing():
    """罫線が無い画像 → 線分ゼロ（AC-F18 の土台）。"""
    binary = np.zeros((300, 300), dtype=bool)
    h_segs, v_segs = detect_segments(binary, dpi=300)
    assert h_segs == []
    assert v_segs == []


def test_detect_segments_short_run_is_dropped():
    """MIN_SEG_LEN 未満の連続暗画素は線分として拾わない。"""
    binary = np.zeros((200, 200), dtype=bool)
    short_len = MIN_SEG_LEN - 10
    binary[50, 10:10 + short_len] = True
    h_segs, v_segs = detect_segments(binary, dpi=300)
    assert h_segs == []


def test_detect_segments_bridges_small_hole():
    """HOLE_MAX 以下のかすれは同一ランへ橋渡しされる（1本の線分になる）。"""
    binary = np.zeros((200, 200), dtype=bool)
    binary[50, 10:80] = True
    binary[50, 80 + HOLE_MAX:160] = True  # 隙間 = HOLE_MAX（境界値）
    h_segs, _ = detect_segments(binary, dpi=300)
    assert len(h_segs) == 1
    assert h_segs[0].start == 10
    assert h_segs[0].end == 159


def test_detect_segments_does_not_bridge_large_hole():
    """GAP_BRIDGE を超える隙間は橋渡ししない（2本の別線分のまま）。

    HOLE_MAX（1本のラン内のかすれ許容）を超えても、GAP_BRIDGE（手順4・
    交差切れの橋渡し）以下ならまとめて1本になる——GAP_BRIDGE(12) は
    HOLE_MAX(4) より大きい値として意図的に設計されている（08 §4.1.4）。
    「橋渡しされない」を確認するには GAP_BRIDGE 自体を超える隙間が要る。
    """
    binary = np.zeros((200, 200), dtype=bool)
    binary[50, 10:80] = True
    binary[50, 80 + GAP_BRIDGE + 5:200] = True  # 隙間 > GAP_BRIDGE
    h_segs, _ = detect_segments(binary, dpi=300)
    assert len(h_segs) == 2


def test_detect_segments_thick_fill_is_excluded():
    """THICK_MAX を超える帯厚（塗り潰し面）は罫線として拾わない。"""
    binary = np.zeros((200, 200), dtype=bool)
    binary[50:90, 10:150] = True  # 厚さ40px（THICK_MAX=12を大きく超える）
    h_segs, v_segs = detect_segments(binary, dpi=300)
    assert h_segs == []
    assert v_segs == []


def test_detect_segments_bridges_gap_across_crossing():
    """交差切れ（GAP_BRIDGE 以内の隙間）を1本の線分へ橋渡しする。"""
    binary = np.zeros((300, 300), dtype=bool)
    binary[100, 10:150] = True
    binary[100, 150 + GAP_BRIDGE - 2:250] = True  # 隙間 < GAP_BRIDGE
    h_segs, _ = detect_segments(binary, dpi=300)
    assert len(h_segs) == 1
    assert h_segs[0].start == 10
    assert h_segs[0].end == 249


def test_scale_threshold_identity_at_base_dpi():
    """既定 dpi=300（BASE_DPI）では従来値そのまま（S-1 の契約）。"""
    assert scale_threshold(60, 300) == 60
    assert scale_threshold(4, 300) == 4


def test_scale_threshold_halves_at_half_dpi():
    """dpi が半分なら閾値も概ね半分にスケールする。"""
    assert scale_threshold(60, 150) == 30
    assert scale_threshold(12, 150) == 6


def test_detect_segments_dpi_scaling_detects_smaller_rect_at_lower_dpi():
    """低 dpi では閾値も縮むため、同じ相対サイズの矩形が同様に検出できる。"""
    binary = _rect_binary(400, 300, (50, 50, 200, 100))
    h_segs, v_segs = detect_segments(binary, dpi=150)
    assert len(h_segs) == 2
    assert len(v_segs) == 2


def test_segment_is_frozen_dataclass():
    """Segment は frozen（誤って書き換えられない値オブジェクト）。"""
    s = Segment("h", 10.0, 0, 100, 1)
    with pytest.raises(Exception):
        s.pos = 20.0  # type: ignore[misc]
