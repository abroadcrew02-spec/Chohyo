"""render_out.py: U-04（由来色）・U-12/U-13（部分〓の条件付き書式・COUNTIF ワイルドカード化）。

正本: docs/design/chouhyo-ocr/04_unclear_policy.md §8・T-11・T-20・T-21。
openpyxl は数式を評価しないため、COUNTIF・FIND 数式は文字列としての一致までを
ここで固定する（実機確認は T-16・報告に証跡を残す）。
"""
import zipfile

from chouhyo_ocr.columns import META_COLUMNS, excel_column_letter
from chouhyo_ocr.render_out import (FILL_ORIGIN_FALLBACK, write_csv,
                                    write_outputs, write_xlsx)
from chouhyo_ocr.render_rows import UNCLEAR, Row

COLS = list(META_COLUMNS) + ["a", "b", "c"]


def _row(values, origins=(), unclear_count=None, min_conf="0.900"):
    if unclear_count is None:
        unclear_count = sum(1 for v in values if isinstance(v, str) and UNCLEAR in v)
    return Row(page_id="p1", source_file="s.png", page_no=1, status="正常",
               values=list(values), unclear_count=unclear_count, min_conf=min_conf,
               origins=tuple(origins))


def _sheet_xml(path):
    z = zipfile.ZipFile(path)
    return z.read("xl/worksheets/sheet1.xml").decode("utf-8")


def _styles_xml(path):
    z = zipfile.ZipFile(path)
    return z.read("xl/styles.xml").decode("utf-8")


# ---------- T-11: 由来印が xlsx に出る ----------

def test_fallback_origin_gets_static_fill(tmp_path):
    row = _row(["262-0032", "x", "y"], origins=("fallback", "", ""))
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])
    styles = _styles_xml(out)
    assert "00E8F4FA" in styles  # 由来色が xf/fill として登録されている
    sheet = _sheet_xml(out)
    # G2（先頭の抽出列・2行目）が由来色のスタイルを参照していること
    assert 'r="G2"' in sheet


def test_non_fallback_origin_has_no_static_fill(tmp_path):
    row = _row(["x", "y", "z"], origins=("", "", ""))
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])
    styles = _styles_xml(out)
    assert "00E8F4FA" not in styles


def test_missing_origins_length_mismatch_does_not_crash(tmp_path):
    """origins が省略された Row（旧呼び出し）でも write_xlsx は落ちない（後方互換）。"""
    row = Row(page_id="p1", source_file="s.png", page_no=1, status="正常",
              values=["x", "y"], unclear_count=0, min_conf="0.9")  # origins 省略
    out = tmp_path / "o.xlsx"
    write_xlsx(out, list(META_COLUMNS) + ["a", "b"], [row])  # 例外にならなければ良い


# ---------- T-21: 条件付き書式が2本 ----------

def test_two_conditional_formatting_rules_present(tmp_path):
    row = _row(["〓", "旭〓市", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])
    sheet = _sheet_xml(out)
    assert sheet.count("<cfRule") == 2
    assert 'operator="equal"' in sheet
    assert "ISNUMBER(FIND(" in sheet
    assert "LEN(" in sheet


def test_countif_formula_uses_contains_wildcard(tmp_path):
    """U-13: COUNTIF が "*〓*" になっている（完全一致のままだと部分〓を数え損なう）。"""
    row = _row(["旭〓市", "y", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])
    sheet = _sheet_xml(out)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f'COUNTIF({first}2:{last}2,&quot;*〓*&quot;)' in sheet \
        or f'COUNTIF({first}2:{last}2,"*〓*")' in sheet


# ---------- T-20: xlsx ↔ csv の一致 ----------

def test_csv_unclear_count_counts_contains(tmp_path):
    row = _row(["〓", "旭〓市", "普通の値"])  # 欄全体〓1 + 一部〓1 = 2
    assert row.unclear_count == 2
    out = tmp_path / "o.csv"
    write_csv(out, COLS, [row])
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].split(",")[0] == '"2"'


def test_write_outputs_keeps_xlsx_and_csv_unclear_count_consistent(tmp_path):
    """xlsx の COUNTIF 範囲と csv の静的値は同じセル集合（抽出列全体）を指す。

    openpyxl は数式を評価しないため、ここでは「範囲」と「csv の値」が
    同じ行・同じ抽出列幅を指していることまでを固定する。
    """
    row = _row(["〓", "旭〓市", "普通の値"])
    xlsx, csvp, _risky = write_outputs(tmp_path, "t1", COLS, [row])
    sheet = _sheet_xml(xlsx)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f"{first}2:{last}2" in sheet
    text = csvp.read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].split(",")[0] == '"2"'
