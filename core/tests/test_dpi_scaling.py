"""px 定数群の dpi 正規化のテスト（汎用化 A-3）。

render_dpi はテンプレートで可変（schema/template.schema.json:29-34・72〜1200）
だが、以下の px 定数は現行様式の 300dpi 実測に固定較正されたままだった:
- mapping._LINE_GAP（行クラスタの y ギャップ）
- mapping._BUCKET（空間インデックスのバケツ幅）
- align.COARSE_DILATE（粗マスクの膨張量）
- grid.ROW_INSET（行高の控え）

dpi が違う様式では例外にならず静かに壊れる（行クラスタ崩れ→〓増・
枠外率超過→様式不一致誤判定）。Template.dpi_scale（= render_dpi / BASE_DPI）
で各定数をスケールしてから使うことで対応する。

対象外（意図的に本テストの対象にしない）:
- mapping.NOISE_MAX・template.py の FORMAT_MISMATCH_RATIO・DECIDE_GAP 等の
  無次元量（比率・文字数・スコア比はそもそも dpi に依存しない）
- era.py の BAND_PAD/BAND_PAD_IN（px だが選択式の帯設計と絡むため今回は対象外）
"""
import json

import numpy as np
import pytest

from chouhyo_ocr.align import binarize_face
from chouhyo_ocr.grid import make_uniform
from chouhyo_ocr.mapping import Symbol, assign, build_symbol_locator, locate_symbol
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import (
    BASE_DPI, CellSpec, Face, Rect, Template, TemplateError, load_template,
)

TPL = app_root() / "templates" / "chouhyo-v1.json"


# ---------- Template.dpi_scale（scale 導出の1箇所） ----------

def test_dpi_scale_at_base_dpi_is_exactly_one():
    """render_dpi==BASE_DPI のとき scale はちょうど 1.0（300/300.0 の厳密割り算）。

    以降の全モジュールの「dpi 省略時・dpi=BASE_DPI 明示時は従来値と完全一致」
    という契約は、この 1.0 ちょうどという性質に依存する。
    """
    t = Template(template_id="t", render_dpi=300, image_size=(10, 10),
                record_pages=1, faces=())
    assert t.dpi_scale == 1.0


def test_dpi_scale_doubles_at_600dpi():
    t = Template(template_id="t", render_dpi=600, image_size=(10, 10),
                record_pages=1, faces=())
    assert t.dpi_scale == 2.0


def test_real_template_is_base_dpi():
    """出荷テンプレート（現行様式）は BASE_DPI そのもの——ここが変わらない限り
    dpi 正規化はこれまでの出力に影響しない（回帰の前提確認）。"""
    t = load_template(TPL)
    assert t.render_dpi == BASE_DPI
    assert t.dpi_scale == 1.0


# ---------- (a) render_dpi=300 で従来値と完全一致すること ----------

def test_assign_default_dpi_matches_base_dpi_explicit():
    """mapping.assign(): dpi 省略時と dpi=BASE_DPI 明示時が完全に同じ結果になる。

    _LINE_GAP=30px のクラスタリング挙動を使う。y差40px（>30px）は 300dpi
    基準では別行と判定され、行ごとに x 昇順で連結されるので "AB" になる
    （※このテキスト自体は記入値ではなく symbol の識別ラベル）。
    """
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 500, 500))
    cell = CellSpec("fid", "f", Rect(0, 0, 500, 200), "text")
    syms = [Symbol("B", 10, 40, 0.9), Symbol("A", 100, 0, 0.9)]

    r_default = assign([cell], {"f": syms}, [face])
    r_explicit = assign([cell], {"f": syms}, [face], dpi=BASE_DPI)

    assert r_default.cells["fid"].text == r_explicit.cells["fid"].text == "AB"


def test_bucket_scale_is_noop_at_base_dpi():
    """mapping._bucket_cells 経由の空間インデックス（issue #17 回帰）が
    dpi 省略時・dpi=BASE_DPI 明示時で同じ取りこぼしゼロを保つ。"""
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 2000, 2000))
    wide = CellSpec("wide", "f", Rect(10, 10, 900, 900), "text")  # 128px 格子を跨ぐ
    syms = [Symbol("x", x, y, 0.9)
            for x in (11, 127, 128, 500, 908) for y in (11, 128, 500, 908)]

    r_default = assign([wide], {"f": syms}, [face])
    r_explicit = assign([wide], {"f": syms}, [face], dpi=BASE_DPI)

    assert r_default.unassigned_other == r_explicit.unassigned_other == 0
    assert len(r_default.cells["wide"].text) == len(r_explicit.cells["wide"].text) == len(syms)


def test_row_inset_scale_is_noop_at_base_dpi():
    """grid.make_uniform(): dpi 省略時と dpi=BASE_DPI 明示時が同じ row_height。"""
    fit_default = make_uniform((0, 0, 1000, 700), rows=10, cols=2)
    fit_explicit = make_uniform((0, 0, 1000, 700), rows=10, cols=2, dpi=BASE_DPI)

    assert fit_default.row_height == fit_explicit.row_height == 66  # int(70.0) - ROW_INSET(4)


def test_binarize_face_dpi_default_matches_base_dpi_bytewise():
    """align.binarize_face(): dpi 省略時と dpi=BASE_DPI 明示時がバイト単位で一致する。

    テンプレート編集ツールが自分の render_dpi を渡し忘れても、300dpi のときは
    無音で従来どおりの結果になる（後方互換の安全側フォールバック）。
    """
    gray = np.zeros((300, 300), dtype=np.uint8)
    gray[50:100, 50:100] = 255  # コントラストを作る（Otsu が単一輝度で 128 固定に落ちないように）
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 300, 300),
                exclusions=(Rect(10, 10, 30, 30),))

    b_default = binarize_face(gray, face)
    b_explicit = binarize_face(gray, face, dpi=BASE_DPI)

    assert np.array_equal(b_default, b_explicit)


# ---------- (b) render_dpi=600 で px 定数が2倍相当に効くこと ----------

def test_line_gap_scales_with_dpi_changes_row_clustering():
    """mapping._LINE_GAP: 600dpi では 30px→60px にスケールし、行クラスタの
    結果が変わる（振る舞いテスト）。

    y差40px の2つの symbol は、300dpi 基準（LINE_GAP=30px）では別行
    （y 昇順で連結="AB"）、600dpi（LINE_GAP=60px）では同一行
    （x 昇順で連結="BA"）になる。
    """
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 500, 500))
    cell = CellSpec("fid", "f", Rect(0, 0, 500, 200), "text")
    syms = [Symbol("B", 10, 40, 0.9), Symbol("A", 100, 0, 0.9)]

    r300 = assign([cell], {"f": syms}, [face], dpi=300)
    r600 = assign([cell], {"f": syms}, [face], dpi=600)

    assert r300.cells["fid"].text == "AB"
    assert r600.cells["fid"].text == "BA"


def test_bucket_scales_with_dpi_still_finds_symbols_across_buckets():
    """mapping._BUCKET: 600dpi では 128px→256px にスケールしても、バケツ境界を
    またぐセルで取りこぼしが起きない（issue #17 の性質は dpi に依存しない）。
    """
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 2000, 2000))
    wide = CellSpec("wide", "f", Rect(10, 10, 900, 900), "text")  # 256px 格子を跨ぐ
    syms = [Symbol("x", x, y, 0.9)
            for x in (11, 255, 256, 500, 908) for y in (11, 256, 500, 908)]

    result = assign([wide], {"f": syms}, [face], dpi=600)

    assert result.unassigned_other == 0, "600dpi のバケツ境界で取りこぼした"
    assert len(result.cells["wide"].text) == len(syms)


# ---------- mapping.build_symbol_locator / locate_symbol（locator の自己記述化・M-2） ----------

def test_build_symbol_locator_at_600dpi_hits_scaled_bucket_boundary():
    """build_symbol_locator(dpi=600) で作った locator は 600dpi のバケツ幅
    （256px）でセルを索引するため、300dpi のバケツ境界（128px）をまたぐ座標
    でも locate_symbol が dpi を渡さずに正しく同じセルへ hit する。

    locator がバケツ幅を自分で持つ（SymbolLocator）ことで、dpi の取り違えが
    型として起こらないという M-2 の狙いを、実際の座標判定で確認する。
    """
    wide = CellSpec("wide", "f", Rect(10, 10, 900, 900), "text")  # 256px 格子を跨ぐ

    locator = build_symbol_locator([wide], dpi=600)

    for x, y in ((11, 11), (127, 128), (256, 500), (908, 908)):
        fid, tag = locate_symbol(locator, x, y)
        assert (fid, tag) == ("wide", "region"), f"({x}, {y}) が wide セルへ hit しなかった"


def test_symbol_locator_keeps_bucket_width():
    """SymbolLocator は自分を作った dpi 由来のバケツ幅を保持する（M-2）。

    600dpi では _BUCKET(128px) の2倍（256px）になる。locate_symbol が dpi
    引数を受け取らずに正しいバケツ幅を引けることの直接確認（M-1 のドキュメント
    訂正どおり、locator 自身がバケツ幅の情報を持つことの証跡）。
    """
    cell = CellSpec("fid", "f", Rect(0, 0, 500, 200), "text")

    locator_300 = build_symbol_locator([cell], dpi=300)
    locator_600 = build_symbol_locator([cell], dpi=600)

    assert locator_300.bucket == 128
    assert locator_600.bucket == 256


def test_row_inset_scales_with_dpi():
    """grid.make_uniform(): 600dpi では ROW_INSET が 4px→8px にスケールする。"""
    fit_300 = make_uniform((0, 0, 1000, 700), rows=10, cols=2, dpi=300)
    fit_600 = make_uniform((0, 0, 1000, 700), rows=10, cols=2, dpi=600)

    assert fit_300.row_height == 66  # int(70.0) - 4
    assert fit_600.row_height == 62  # int(70.0) - 8


def test_coarse_dilate_scales_with_dpi_changes_exclusion_mask():
    """align.COARSE_DILATE: 600dpi では 60px→120px にスケールし、除外領域の
    膨張マスクが広がることを二値化結果（観測可能な振る舞い）で確認する。

    除外矩形 (100,100,50,50) の外側 (220,220) に置いたインクは、300dpi の
    膨張量60px（マスク範囲 x/y: 40〜210）では覆われず検出されるが、600dpi の
    膨張量120px（マスク範囲 x/y: 0〜270）では覆われて除外される。

    背景はグラデーションにする——一様な2値（0/255 のみ）だと Otsu の
    between-class variance が th=0 で最大点に張り付き（ヒストグラムの
    山が2つしかないと 0〜254 のどこでも同点になり argmax が最小の 0 を
    返す）、意図した中間閾値によるインク判定を再現できないため。
    """
    size = 400
    gray = np.tile(np.linspace(0, 255, size), (size, 1)).astype(np.uint8)
    gray[210:230, 210:230] = 0  # 除外矩形の外・300dpi膨張の外・600dpi膨張の内
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, size, size),
                exclusions=(Rect(100, 100, 50, 50),))

    binary_300 = binarize_face(gray, face, dpi=300)
    binary_600 = binarize_face(gray, face, dpi=600)

    assert binary_300[220, 220]        # 300dpi: マスク外なのでインク検出
    assert not binary_600[220, 220]    # 600dpi: マスクに飲まれ除外


# ---------- (c) 不正 render_dpi はスキーマ検証で拒否される ----------

@pytest.fixture()
def raw_template():
    return json.loads(TPL.read_text(encoding="utf-8"))


def _write(tmp_path, data):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_render_dpi_below_minimum_rejected(tmp_path, raw_template):
    """schema/template.schema.json:32 の minimum=72 未満は拒否される。"""
    raw_template["render_dpi"] = 71
    with pytest.raises(TemplateError, match="スキーマ検証エラー"):
        load_template(_write(tmp_path, raw_template))


def test_render_dpi_above_maximum_rejected(tmp_path, raw_template):
    """schema/template.schema.json:33 の maximum=1200 超は拒否される。"""
    raw_template["render_dpi"] = 1201
    with pytest.raises(TemplateError, match="スキーマ検証エラー"):
        load_template(_write(tmp_path, raw_template))


def test_render_dpi_non_integer_rejected(tmp_path, raw_template):
    """schema/template.schema.json:31 の type=integer 以外は拒否される。"""
    raw_template["render_dpi"] = 300.5
    with pytest.raises(TemplateError, match="スキーマ検証エラー"):
        load_template(_write(tmp_path, raw_template))
