"""render_rows.py: セル3状態・〓判定・D-01/D-23 適用・ステータス合成のテスト。"""
import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import (STATUS_ALIGN_FAILED, STATUS_INTERRUPTED,
                                     STATUS_OK, STATUS_OVERFLOW,
                                     UNCLEAR, build_failure_row, build_row,
                                     compose_status)
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"
CFG = Config(unclear_threshold=0.85, era_threshold=0.06)


@pytest.fixture(scope="module")
def template():
    return load_template(TPL)


def base_cells(template):
    """全セル未読取（空行なし）の cells 辞書。"""
    return {c.field_id: ("", None, c.kind, False) for c in template.cells}


def page(status="", below=0):
    return {"page_id": "p_0001", "source_file": "s.png", "page_no": 1,
            "status": status, "unassigned_below_table": below}


def col_value(template, row, colname):
    cols = [oc for c in template.cells for oc in c.output_columns()]
    return row.values[cols.index(colname)]


def test_unread_cell_is_unclear_not_empty(template):
    """記入行（空行でない）の未読取セルは空文字でなく〓（要件 §5.5）。"""
    row = build_row(template, page(), base_cells(template), {}, CFG)
    assert col_value(template, row, "person_備考") == UNCLEAR


def test_low_conf_becomes_unclear(template):
    cells = base_cells(template)
    cells["person_氏名"] = ("テスト太郎", 0.60, "text", False)
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "person_氏名") == UNCLEAR


def test_high_conf_passes(template):
    cells = base_cells(template)
    cells["person_氏名"] = ("テスト太郎", 0.95, "text", False)
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "person_氏名") == "テスト太郎"
    assert row.min_conf == "0.950"


def test_empty_row_yields_empty_strings(template):
    cells = base_cells(template)
    for c in template.cells:
        if c.table_id == "family" and c.row_no == 5:
            cells[c.field_id] = ("", None, c.kind, True)
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "family_05_続柄") == ""
    assert col_value(template, row, "family_05_生年月日_年") == ""
    assert col_value(template, row, "family_05_生年月日_元号") == ""


def test_subfields_split_and_failure(template):
    cells = base_cells(template)
    cells["family_01_生年月日"] = ("7.7.20", 0.95, "text", False)
    cells["family_02_生年月日"] = ("7.720", 0.95, "text", False)  # 2分割 → 全サブ列〓
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "family_01_生年月日_年") == "7"
    assert col_value(template, row, "family_01_生年月日_月") == "7"
    assert col_value(template, row, "family_01_生年月日_日") == "20"
    assert col_value(template, row, "family_02_生年月日_年") == UNCLEAR
    assert col_value(template, row, "family_02_生年月日_日") == UNCLEAR


def test_amount_normalized_to_int(template):
    cells = base_cells(template)
    cells["detail_01_金額"] = ("10.000", 0.95, "text", False)
    cells["detail_02_金額"] = ("/0.000", 0.95, "text", False)  # 誤読 → 〓
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "detail_01_金額") == 10000
    assert col_value(template, row, "detail_02_金額") == UNCLEAR


def test_control_chars_become_unclear(template):
    """xlsx に書けない制御文字入りの読取値は〓（issue #2・値の例外漏出防止）。"""
    cells = base_cells(template)
    cells["person_氏名"] = ("テスト太郎" + chr(1), 0.95, "text", False)
    cells["person_住所1"] = ("千葉県" + chr(0) + "千葉市", 0.99, "text", False)
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "person_氏名") == UNCLEAR
    assert col_value(template, row, "person_住所1") == UNCLEAR


def test_era_decision_paths(template):
    cells = base_cells(template)
    scores = {
        "person_生年月日_元号": {"昭": 0.01, "平": 0.30, "令": 0.02},   # 平が明確
        "family_01_生年月日_元号": {"昭": 0.01, "平": 0.02, "令": 0.02},  # 全部閾値未満=未選択
        "family_02_生年月日_元号": {"昭": 0.20, "平": 0.19, "令": 0.02},  # 拮抗=判定不能
    }
    row = build_row(template, page(), cells, scores, CFG)
    assert col_value(template, row, "person_生年月日_元号") == "平"
    assert col_value(template, row, "family_01_生年月日_元号") == UNCLEAR
    assert col_value(template, row, "family_02_生年月日_元号") == UNCLEAR


def test_choice_conf_excluded_from_min_conf(template):
    """選択式は最低信頼度の母集団に入らない（要件 §5.6）。"""
    cells = base_cells(template)
    cells["person_氏名"] = ("テスト太郎", 0.91, "text", False)
    scores = {"person_生年月日_元号": {"昭": 0.01, "平": 0.30, "令": 0.02}}
    row = build_row(template, page(), cells, scores, CFG)
    assert row.min_conf == "0.910"


def test_status_composition():
    assert compose_status("", 0, processed=True) == STATUS_OK
    assert compose_status("", 5, processed=True) == STATUS_OVERFLOW
    assert compose_status(STATUS_ALIGN_FAILED, 0, processed=False) == STATUS_ALIGN_FAILED


def test_failure_row_all_unclear(template):
    row = build_failure_row(template, page(status=STATUS_ALIGN_FAILED))
    assert len(row.values) == 212
    assert set(row.values) == {UNCLEAR}
    assert row.unclear_count == 212
    assert row.min_conf == ""


def test_min_conf_excludes_cells_that_became_unclear(template):
    """最低信頼度は出力された値のうちの最小（レビュー B-1）。

    分割失敗・金額正規化失敗で〓になったセルの信頼度を混ぜない——
    〓の優先確認に使う列なのに、出ていない値の信頼度が出ていた。
    """
    cells = base_cells(template)
    # 高信頼だが分割できない値（〓になる）と、低めだが出力される値
    cells["family_01_生年月日"] = ("分割できない値", 0.30, "text", False)
    cells["person_氏名"] = ("テスト太郎", 0.91, "text", False)
    row = build_row(template, page(), cells, {}, CFG)
    assert col_value(template, row, "family_01_生年月日_年") == UNCLEAR
    assert col_value(template, row, "person_氏名") == "テスト太郎"
    assert row.min_conf == "0.910", f"〓セルの信頼度が混ざっている: {row.min_conf}"


def test_unknown_status_is_not_reported_as_normal():
    """既知集合に無いステータスを「正常」へ倒さない（レビュー B-4 の fail-open）。

    中間データは版をまたいで残る。定数を1つ改名しただけで、旧 status を
    持つ既存ページが黙って正常になるのを防ぐ。
    """
    assert compose_status("旧版の未知ステータス", 0, processed=True) == "旧版の未知ステータス"
    assert compose_status("", 0, processed=True) == STATUS_OK
    assert compose_status(STATUS_OK, 0, processed=True) == STATUS_OK
    assert compose_status(STATUS_ALIGN_FAILED, 0, processed=False) == STATUS_ALIGN_FAILED


def test_unprocessed_row_never_claims_ok():
    """全〓の失敗行が「正常」を名乗らない（レビュー LOW の同値2分岐）。

    旧実装は processed の真偽にかかわらず STATUS_OK を返し、事故を防いでいたのは
    呼び出し側のガードだけだった。ガードが1つ外れれば、値が1つも無い行が
    「正常」として出荷される。
    """
    assert compose_status("", 0, processed=False) != STATUS_OK
    assert compose_status("", 0, processed=False) == STATUS_INTERRUPTED
    # 正常系は変わらない
    assert compose_status("", 0, processed=True) == STATUS_OK
