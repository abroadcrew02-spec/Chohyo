"""columns.py: 列導出のテスト（設計 §4.3 の検証3点を自動化）。"""
import pytest

from chouhyo_ocr.columns import (META_COLUMNS, derive_columns, excel_column_letter,
                                 extract_columns, validate_v1)
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import TemplateError, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


@pytest.fixture(scope="module")
def template():
    return load_template(TPL)


def test_total_is_220(template):
    cols = validate_v1(template)
    assert len(cols) == 220


def test_segment_boundaries(template):
    cols = derive_columns(template)
    person = [c for c in cols if c.startswith("person_")]
    family = [c for c in cols if c.startswith("family_")]
    detail = [c for c in cols if c.startswith("detail_")]
    assert cols[:6] == list(META_COLUMNS)
    assert (len(person), len(family), len(detail)) == (14, 60, 140)
    # 境界: 管理 | 本人 | 家族 | 明細 の順で連続する
    assert cols[6] == "person_電話番号"        # テンプレート定義順＝物理配置順
    assert cols[19] == "person_備考"
    assert cols[20] == "family_01_続柄"
    assert cols[79] == "family_10_生年月日_日"
    assert cols[80] == "detail_01_来店年月日_年"
    assert cols[219] == "detail_28_品目"


def test_no_duplicates(template):
    cols = derive_columns(template)
    assert len(set(cols)) == len(cols)


def test_subfields_expand_in_order(template):
    cols = derive_columns(template)
    i = cols.index("family_01_生年月日_年")
    assert cols[i:i + 3] == [
        "family_01_生年月日_年", "family_01_生年月日_月", "family_01_生年月日_日"]


def test_extract_columns_excludes_meta(template):
    ext = extract_columns(template)
    assert len(ext) == 214
    assert not set(META_COLUMNS) & set(ext)


def test_excel_column_letters():
    assert excel_column_letter(7) == "G"
    assert excel_column_letter(220) == "HL"   # COUNTIF 範囲 Gn:HLn の根拠


def test_validate_rejects_wrong_count(template):
    """行数を変えたテンプレートは 220 列検証で拒否される。"""
    import dataclasses
    smaller = dataclasses.replace(template, cells=template.cells[:-5])
    with pytest.raises(TemplateError, match="220"):
        validate_v1(smaller)
