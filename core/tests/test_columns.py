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


def test_total_matches_shipped_template(template):
    """列数はテンプレート由来（固定数の拒否は 2026-08-31 に廃止）。

    現行 chouhyo-v1 は 220列 = 管理6＋本人14（郵便番号2列を含む）＋家族60＋明細140。
    ここで固定するのは「出荷テンプレートの現在値」であって v1 の上限ではない。
    """
    cols = validate_v1(template)
    assert len(cols) == 220


def test_segment_boundaries(template):
    cols = derive_columns(template)
    person = [c for c in cols if c.startswith("person_")]
    family = [c for c in cols if c.startswith("family_")]
    detail = [c for c in cols if c.startswith("detail_")]
    assert cols[:6] == list(META_COLUMNS)
    assert (len(person), len(family), len(detail)) == (14, 60, 140)
    # 境界: 管理 | 本人 | 家族 | 明細 の順で連続する（定義順＝列順）
    assert cols[6] == "person_電話番号"
    assert cols[6:6 + 14] == person
    assert cols[20] == "family_01_続柄"
    assert cols[20:80] == family
    assert cols[80] == "detail_01_来店年月日_年"
    assert cols[-1] == "detail_28_品目"
    # 郵便番号は対応する住所の左隣（レビュー4巡目後・住所ブロックから分離）
    assert cols[cols.index("person_住所1") - 1] == "person_郵便番号1"
    assert cols[cols.index("person_住所2") - 1] == "person_郵便番号2"


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
    assert excel_column_letter(218) == "HJ"   # 純関数の検証値（218固定の根拠ではない）。
    # render_out.py の COUNTIF 範囲は excel_column_letter(len(columns)) で導出しており、
    # 現行220列では Gn:HLn になる（218列だった旧版の名残りコメントを2026-08-31 訂正）


def test_validate_accepts_any_count(template):
    """列数の増減は拒否しない（決め打ち廃止・2026-08-31）。

    欄を足せば列が増えるのが正。検証は verify・編集画面が列数を表示して
    人が確認する方式に変えた。
    """
    import dataclasses
    smaller = dataclasses.replace(template, cells=template.cells[:-5])
    assert len(validate_v1(smaller)) == len(validate_v1(template)) - 5


def test_validate_rejects_duplicate_names(template):
    """列名の重複だけは拒否する（xlsx/csv の列対応が壊れる真の不変条件）。"""
    import dataclasses
    dup = dataclasses.replace(template,
                              cells=template.cells + (template.cells[0],))
    with pytest.raises(TemplateError, match="重複"):
        validate_v1(dup)
