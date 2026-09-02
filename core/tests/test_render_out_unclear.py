"""render_out.py: U-04（由来色）・U-12/U-13（部分〓の条件付き書式・COUNTIF ワイルドカード化）。

正本: docs/design/chouhyo-ocr/04_unclear_policy.md §8・T-11・T-20・T-21。
openpyxl は数式を評価しないため、COUNTIF・FIND 数式は文字列としての一致までを
ここで固定する（実機確認は T-16・報告に証跡を残す）。
"""
import zipfile

import pytest

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


# ---------- T-21: 条件付き書式（ON 時は2本・OFF 時は既存1本のまま） ----------
#
# QA 再判定（2026-08-31・T-16 ブロッカーの解消）: write_xlsx の「含む」判定
# （COUNTIF ワイルドカード・条件付き書式2本目）は unclear_char_level でゲート
# する。Excel 実機での COUNTIF("*〓*") 動作（T-16）が未検証のため、既定 OFF
# の経路には未検証の仮定を載せない。

def test_two_conditional_formatting_rules_present_when_on(tmp_path):
    row = _row(["〓", "旭〓市", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row], unclear_char_level=True)
    sheet = _sheet_xml(out)
    assert sheet.count("<cfRule") == 2
    assert 'operator="equal"' in sheet
    assert "ISNUMBER(FIND(" in sheet
    assert "LEN(" in sheet


def test_only_one_conditional_formatting_rule_when_off(tmp_path):
    """既定 OFF: 条件付き書式は機能追加前と同じ1本（完全一致）のまま。"""
    row = _row(["〓", "z", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])  # unclear_char_level 省略＝False
    sheet = _sheet_xml(out)
    assert sheet.count("<cfRule") == 1
    assert 'operator="equal"' in sheet
    assert "ISNUMBER(FIND(" not in sheet


def test_countif_formula_uses_contains_wildcard_when_on(tmp_path):
    """U-13: unclear_char_level=True のときだけ COUNTIF が "*〓*" になる
    （完全一致のままだと部分〓を数え損なう）。
    """
    row = _row(["旭〓市", "y", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row], unclear_char_level=True)
    sheet = _sheet_xml(out)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f'COUNTIF({first}2:{last}2,&quot;*〓*&quot;)' in sheet \
        or f'COUNTIF({first}2:{last}2,"*〓*")' in sheet


def test_countif_formula_is_exact_match_when_off(tmp_path):
    """QA 再判定: 既定 OFF では COUNTIF は機能追加前と同じ完全一致のまま
    （T-16 が実機確認されるまで、未検証のワイルドカードを既定経路に載せない）。
    """
    row = _row(["〓", "y", "z"])
    out = tmp_path / "o.xlsx"
    write_xlsx(out, COLS, [row])  # unclear_char_level 省略＝False
    sheet = _sheet_xml(out)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f'COUNTIF({first}2:{last}2,&quot;〓&quot;)' in sheet \
        or f'COUNTIF({first}2:{last}2,"〓")' in sheet
    assert "*〓*" not in sheet


# ---------- T-20: xlsx ↔ csv の一致（設計 §14 不変条件5・OFF/ON 両状態） ----------

def test_csv_unclear_count_counts_contains(tmp_path):
    row = _row(["〓", "旭〓市", "普通の値"])  # 欄全体〓1 + 一部〓1 = 2
    assert row.unclear_count == 2
    out = tmp_path / "o.csv"
    write_csv(out, COLS, [row])
    text = out.read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].split(",")[0] == '"2"'


def test_write_outputs_keeps_xlsx_and_csv_unclear_count_consistent_when_on(tmp_path):
    """設計 §14 不変条件5（ON 側）: xlsx の COUNTIF 範囲と csv の静的値は
    同じセル集合（抽出列全体）を指す。row.unclear_count は「含む」で数えた
    値（render_rows.build_row が cfg.unclear_char_level=True のとき作る値と
    同じ形）——write_outputs 側も unclear_char_level=True で揃える。

    openpyxl は数式を評価しないため、ここでは「範囲」と「csv の値」が
    同じ行・同じ抽出列幅を指していることまでを固定する（実機確認は T-16）。
    """
    row = _row(["〓", "旭〓市", "普通の値"])  # 含む判定で2
    xlsx, csvp, _risky = write_outputs(tmp_path, "t_on", COLS, [row],
                                       unclear_char_level=True)
    sheet = _sheet_xml(xlsx)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f"{first}2:{last}2" in sheet
    assert "*〓*" in sheet
    text = csvp.read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].split(",")[0] == '"2"'


def test_write_outputs_keeps_xlsx_and_csv_unclear_count_consistent_when_off(tmp_path):
    """設計 §14 不変条件5（OFF 側）: 完全一致のセルだけを数えた row.unclear_count
    と、OFF（既定）で書いた xlsx の COUNTIF（完全一致のまま）が同じ意味の
    セル集合を指す。文字単位〓が無効なら「〓」は必ず単独のセル値になるため、
    この行には部分〓の値を含めない（OFF 経路の実態と揃える）。
    """
    row = _row(["〓", "普通の値", "普通の値"])  # 完全一致でも含むでも1
    assert row.unclear_count == 1
    xlsx, csvp, _risky = write_outputs(tmp_path, "t_off", COLS, [row])
    sheet = _sheet_xml(xlsx)
    first = excel_column_letter(len(META_COLUMNS) + 1)
    last = excel_column_letter(len(COLS))
    assert f"{first}2:{last}2" in sheet
    assert "*〓*" not in sheet
    text = csvp.read_text(encoding="utf-8-sig")
    assert text.splitlines()[1].split(",")[0] == '"1"'


# ---------- Q-MH: write_xlsx/write_csv 単体の列数検査（write_outputs を経由
# しない直接呼び出しへの多重防御。write_outputs 側の検査は issue #27・
# test_review_fixes.py::test_write_outputs_rejects_row_length_mismatch） ----------

def test_write_xlsx_rejects_row_length_mismatch(tmp_path):
    row = _row(["x", "y"])  # COLS は抽出列3（a/b/c）に対し値2つ
    with pytest.raises(ValueError, match="値数"):
        write_xlsx(tmp_path / "o.xlsx", COLS, [row])


def test_write_csv_rejects_row_length_mismatch(tmp_path):
    row = _row(["x", "y", "z", "extra"])  # 抽出列3に対し値4つ
    with pytest.raises(ValueError, match="値数"):
        write_csv(tmp_path / "o.csv", COLS, [row])
