"""文字単位〓（U-10〜U-13・#62・2026-08-31）。build_row の extras 引数を直接使う。

正本: docs/design/chouhyo-ocr/04_unclear_policy.md §2 判定表・§8・T-12〜T-21。
pipeline.py（run/remap の store 書き込み配線）は5巡目 第2段の担当範囲外のため、
ここでは render_rows.build_row の契約（extras の解釈）そのものを直接検証する。
"""
import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import UNCLEAR, build_row
from chouhyo_ocr.template import CellSpec, Rect, Template, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"
CFG_ON = Config(unclear_threshold=0.85, era_threshold=0.06, unclear_char_level=True)
CFG_OFF = Config(unclear_threshold=0.85, era_threshold=0.06, unclear_char_level=False)


@pytest.fixture(scope="module")
def template():
    return load_template(TPL)


def base_cells(template):
    return {c.field_id: ("", None, c.kind, False) for c in template.cells}


def page():
    return {"page_id": "p1", "source_file": "s.png", "page_no": 1,
            "status": "", "unassigned_below_table": 0}


def col_value(template, row, colname):
    cols = [oc for c in template.cells for oc in c.output_columns()]
    return row.values[cols.index(colname)]


def col_origin(template, row, colname):
    cols = [oc for c in template.cells for oc in c.output_columns()]
    return row.origins[cols.index(colname)]


# 単純 text 欄（subfields なし・normalize なし）
SIMPLE_FIELD = "person_氏名"


def test_partial_unclear_folds_only_low_conf_chars(template):
    """T-12: 旭〓市。全文字置換ではなく該当文字だけ〓・最低信頼度は残った文字の最小。"""
    cells = base_cells(template)
    cells[SIMPLE_FIELD] = ("旭川市", 0.31, "text", False)  # conf(=conf_min) < threshold
    extras = {SIMPLE_FIELD: ("0.97,0.31,0.96", "")}
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, SIMPLE_FIELD) == "旭〓市"
    assert row.min_conf == "0.960"


def test_all_chars_below_threshold_folds_to_single_mark(template):
    """T-13: 全文字が閾値未満 → "〓〓〓" ではなく1文字の "〓" へ畳む。"""
    cells = base_cells(template)
    cells[SIMPLE_FIELD] = ("旭川市", 0.29, "text", False)
    extras = {SIMPLE_FIELD: ("0.31,0.30,0.29", "")}
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, SIMPLE_FIELD) == UNCLEAR
    assert col_value(template, row, SIMPLE_FIELD) != "〓〓〓"


def test_feature_off_keeps_whole_cell_unclear(template):
    """T-14: 機能OFFなら同じ入力でも従来どおり欄全体〓。"""
    cells = base_cells(template)
    cells[SIMPLE_FIELD] = ("旭川市", 0.31, "text", False)
    extras = {SIMPLE_FIELD: ("0.97,0.31,0.96", "")}
    row = build_row(template, page(), cells, {}, CFG_OFF, extras=extras)
    assert col_value(template, row, SIMPLE_FIELD) == UNCLEAR


def test_missing_char_confs_falls_back_to_whole_cell(template):
    """T-15a: char_confs が空 → 安全側（欄全体〓）。"""
    cells = base_cells(template)
    cells[SIMPLE_FIELD] = ("旭川市", 0.31, "text", False)
    extras = {SIMPLE_FIELD: ("", "")}
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, SIMPLE_FIELD) == UNCLEAR


def test_mismatched_char_confs_length_falls_back_to_whole_cell(template):
    """T-15b: char_confs の長さが raw と不一致 → 安全側（欄全体〓）。"""
    cells = base_cells(template)
    cells[SIMPLE_FIELD] = ("旭川市", 0.31, "text", False)  # 3文字
    extras = {SIMPLE_FIELD: ("0.9,0.9", "")}  # 2個しかない
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, SIMPLE_FIELD) == UNCLEAR


def test_choice_unaffected_by_char_level_flag(template):
    """T-17: choice は機能 ON/OFF に関わらず従来挙動（候補照合であり文字列読み取りでない）。"""
    cells = base_cells(template)
    scores = {"person_生年月日_元号": {"昭": 0.01, "平": 0.02, "令": 0.02}}  # 未選択
    row_on = build_row(template, page(), cells, scores, CFG_ON)
    row_off = build_row(template, page(), cells, scores, CFG_OFF)
    assert col_value(template, row_on, "person_生年月日_元号") == UNCLEAR
    assert col_value(template, row_off, "person_生年月日_元号") == UNCLEAR


def test_amount_unaffected_by_char_level_flag(template):
    """T-18: normalize=amount は文字単位〓の対象外（Could・今回はやらない）。"""
    cells = base_cells(template)
    cells["detail_01_金額"] = ("100", 0.31, "text", False)
    extras = {"detail_01_金額": ("0.9,0.9,0.31", "")}
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, "detail_01_金額") == UNCLEAR


def test_subfields_unaffected_by_char_level_flag(template):
    """T-19: subfields を持つ欄は文字単位〓の対象外（split_composite が〓混じりを扱えない）。"""
    cells = base_cells(template)
    cells["family_01_生年月日"] = ("7.7.20", 0.31, "text", False)
    extras = {"family_01_生年月日": ("0.9,0.9,0.9,0.9,0.31,0.9", "")}
    row = build_row(template, page(), cells, {}, CFG_ON, extras=extras)
    assert col_value(template, row, "family_01_生年月日_年") == UNCLEAR
    assert col_value(template, row, "family_01_生年月日_月") == UNCLEAR
    assert col_value(template, row, "family_01_生年月日_日") == UNCLEAR


def test_unclear_count_counts_partial_cells_as_one():
    """U-13: 要確認セル数は「〓を含む」で数える。部分〓も欄全体〓も1件。

    実テンプレート（220列）は未読取セルが軒並み〓になり基準線が0でないため、
    ここでは数え上げのロジックだけを2セルの最小テンプレートで検証する。
    """
    cells = (
        CellSpec("f1", "front", Rect(0, 0, 10, 10), "text"),
        CellSpec("f2", "front", Rect(20, 0, 10, 10), "text"),
    )
    tiny = Template(template_id="t", render_dpi=300, image_size=(100, 100),
                    record_pages=1, faces=(), cells=cells)
    cell_data = {
        "f1": ("旭川市", 0.31, "text", False),      # 一部〓 → "旭〓市"
        "f2": ("アサヒカワシ", 0.20, "text", False),  # 全文字未満 → 欄全体〓
    }
    extras = {"f1": ("0.97,0.31,0.96", ""),
             "f2": ("0.10,0.10,0.10,0.10,0.10,0.10", "")}
    row = build_row(tiny, page(), cell_data, {}, CFG_ON, extras=extras)
    assert row.values == ["旭〓市", UNCLEAR]
    assert row.unclear_count == 2


# ---------- U-04: 由来印（origin）と conflict の強制〓 ----------

def test_fallback_origin_flows_to_row_when_value_clear(template):
    """由来印 'fallback' は値が〓でなければ Row.origins に残る（xlsx 由来色の入力）。"""
    cells = base_cells(template)
    cells["person_郵便番号1"] = ("262-0032", 0.95, "text", False)
    extras = {"person_郵便番号1": ("", "fallback")}
    row = build_row(template, page(), cells, {}, CFG_OFF, extras=extras)
    assert col_value(template, row, "person_郵便番号1") == "262-0032"
    assert col_origin(template, row, "person_郵便番号1") == "fallback"


def test_fallback_origin_suppressed_when_value_becomes_unclear(template):
    """値が〓になった列は由来印を持たない（〓の条件付き書式を優先・設計 §3 U-04）。"""
    cells = base_cells(template)
    cells["person_郵便番号1"] = ("262-0032", 0.10, "text", False)  # 閾値未満
    extras = {"person_郵便番号1": ("", "fallback")}
    row = build_row(template, page(), cells, {}, CFG_OFF, extras=extras)
    assert col_value(template, row, "person_郵便番号1") == UNCLEAR
    assert col_origin(template, row, "person_郵便番号1") == ""


def test_conflict_origin_forces_unclear_regardless_of_confidence(template):
    """T-07 の render 側: origin='conflict' は高信頼でも欄全体〓（判定表 #8）。"""
    cells = base_cells(template)
    cells["person_郵便番号1"] = ("ノ", 0.99, "text", False)  # 高信頼
    extras = {"person_郵便番号1": ("0.99", "conflict")}
    row = build_row(template, page(), cells, {}, CFG_OFF, extras=extras)
    assert col_value(template, row, "person_郵便番号1") == UNCLEAR
    assert col_origin(template, row, "person_郵便番号1") == ""


def test_no_extras_matches_legacy_behavior(template):
    """extras 省略（None）は由来印なし・文字単位〓なしの従来挙動と完全一致（回帰の要）。"""
    cells = base_cells(template)
    cells["person_氏名"] = ("テスト太郎", 0.95, "text", False)
    row_with_none = build_row(template, page(), cells, {}, CFG_ON)  # extras 省略
    row_with_empty = build_row(template, page(), cells, {}, CFG_ON, extras={})
    assert row_with_none.values == row_with_empty.values
    assert col_value(template, row_with_none, "person_氏名") == "テスト太郎"
    assert col_origin(template, row_with_none, "person_氏名") == ""
