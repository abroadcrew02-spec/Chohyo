"""grid.py: 枠候補生成のテスト（§8-16/17 の土台）。

罫線検出は実サンプルの裏面（明細テーブル）で検証する——テンプレート較正時の
実測値（14行・ピッチ104・5列）を正解として使う。
"""
import numpy as np
import pytest
from PIL import Image

from chouhyo_ocr.grid import detect_ruled, make_uniform
from chouhyo_ocr.paths import app_root

PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"


def test_uniform_grid_arithmetic():
    fit = make_uniform((100, 200, 500, 700), rows=14, cols=5)
    assert fit.mode == "uniform"
    assert (fit.origin_x, fit.origin_y, fit.rows) == (100, 200, 14)
    assert fit.row_pitch == 50.0
    assert len(fit.columns) == 5
    assert fit.columns[0] == {"x_offset": 0, "width": 100}
    assert fit.columns[4]["x_offset"] == 400
    assert fit.residual_px == 0.0


def test_uniform_never_depends_on_image():
    """等分割生成は画像に依存しない（Q-03 非依存の退避先・§8-17）。"""
    fit = make_uniform((0, 0, 1000, 280), rows=4, cols=2)
    assert fit.rows == 4 and len(fit.columns) == 2


@pytest.mark.skipif(not PAGE_PNG.exists(), reason="展開画像が無い環境")
def test_ruled_detects_detail_table():
    gray = np.asarray(Image.open(PAGE_PNG).convert("L"))
    # 裏面・明細の左ブロック（較正実測: x70-1123, データ行 y1973-3430）
    fit = detect_ruled(gray, (60, 1950, 1080, 1500))
    assert fit is not None
    assert fit.mode == "ruled"
    assert fit.rows == 14                       # 実測どおり（要件 v3.10 の根拠）
    assert 100 < fit.row_pitch < 108            # 実測 104
    assert len(fit.columns) == 5                # 年|月|日|金額|品目
    assert fit.residual_px < 4.0                # 当てはめ残差（§6.9: 画面へ出す値）


@pytest.mark.skipif(not PAGE_PNG.exists(), reason="展開画像が無い環境")
def test_ruled_fails_gracefully_on_blank():
    blank = np.full((500, 500), 255, dtype=np.uint8)
    assert detect_ruled(blank, (0, 0, 500, 500)) is None  # 退避先へ誘導（§8-17）


def test_uniform_columns_cover_the_whole_region():
    """等分割の列が枠の右端まで届く（端数を捨てない・レビュー LOW）。

    旧実装は width = w // cols で、幅が列数で割り切れないと最終列が最大
    cols-1 px 手前で終わり、右端の文字を取りこぼしていた。
    """
    for w, cols in [(1000, 7), (999, 4), (101, 5), (7, 3), (500, 1)]:
        fit = make_uniform((0, 0, w, 300), rows=3, cols=cols)
        c = fit.columns
        assert len(c) == cols
        assert c[0]["x_offset"] == 0
        assert c[-1]["x_offset"] + c[-1]["width"] == w, f"w={w} cols={cols} で右端不一致"
        # 隙間も重なりも作らない
        for a, b in zip(c, c[1:]):
            assert a["x_offset"] + a["width"] == b["x_offset"]
        assert all(x["width"] >= 1 for x in c)
