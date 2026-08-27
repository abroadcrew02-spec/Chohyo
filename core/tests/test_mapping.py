"""mapping.py: 保存済み S2 応答（実サンプル・page1）での回帰テスト。

Vision API を呼ばない（workdir/s2/ の保存応答を使う）。応答ファイルは
.gitignore 配下のためこのマシンにしか無い——無い環境では skip する。

このテストの主眼は symbol 単位割付の実証:
word 単位のドライランでは「10 8 31 1000」が1word「108311000」に結合され
日セルへ落ちた（日付と金額の混線）。symbol 単位なら文字ごとに正しいセルへ入る。
"""
import json

import pytest

from chouhyo_ocr.mapping import assign, symbols_from_response, to_face_local
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(not RESP.exists(), reason="保存済み Vision 応答が無い環境")


@pytest.fixture(scope="module")
def result():
    template = load_template(TPL)
    page_syms = symbols_from_response(json.loads(RESP.read_text(encoding="utf-8")))
    by_face = {f.face_id: to_face_local(f, page_syms) for f in template.faces}
    return assign(template.cells, by_face, template.faces)


def text(result, fid):
    c = result.cells.get(fid)
    return c.text if c else ""


def test_person_fields(result):
    assert text(result, "person_郵便番号1") == "262-0032"
    # 紙には「071-8111」と書かれているが Vision は末尾の「11」を検出しない
    # （word='071-81' で終端・最後の1は conf 0.66 → 閾値 0.85 なら〓化され目検へ回る）。
    # このテストは第1層（配置の正しさ）の検証であり、第2層（読取内容の正しさ）は
    # 保証対象外（要件 §8）。期待値は Vision の実際の読取値とする。
    assert text(result, "person_郵便番号2") == "071-81"
    assert "千葉県千葉市" in text(result, "person_住所1")
    assert "北海道旭川市" in text(result, "person_住所2")
    assert "アブロード" in text(result, "person_会社名屋号")
    assert text(result, "person_ふりがな") == "じょうにしりょう"


def test_family_composite_date(result):
    """家族欄の生年月日はまとめ書きのまま1セルへ入る（分割は render 時・D-23）。"""
    assert text(result, "family_01_生年月日") == "7.7.20"
    assert text(result, "family_02_生年月日") == "1.3.31"
    assert text(result, "family_03_生年月日") == "3,8,30"


def test_symbol_assignment_fixes_word_merge(result):
    """word 単位で混線した明細4行目が、symbol 単位では正しいセルへ分かれる。"""
    assert text(result, "detail_04_来店年月日_年") == "10"
    assert text(result, "detail_04_来店年月日_月") == "8"
    assert text(result, "detail_04_来店年月日_日") == "31"
    assert text(result, "detail_04_金額") == "1000"
    assert text(result, "detail_04_品目") == "合格祈願"


def test_detail_rows(result):
    assert text(result, "detail_01_品目") == "家内安全"
    assert text(result, "detail_03_金額") == "100"
    assert text(result, "detail_05_品目") == "七五三"


def test_empty_rows_ignore_choice_prints(result):
    """choice の印字（昭平令）が読まれても、text 列が空なら行は空行。"""
    fam_empty = {r for t, r in result.empty_rows if t == "family"}
    det_empty = {r for t, r in result.empty_rows if t == "detail"}
    assert fam_empty == set(range(4, 11))      # 家族: 1〜3行のみ記入
    assert det_empty == set(range(6, 29))      # 明細: 1〜5行のみ記入
    assert result.unassigned_below_table == 0  # このサンプルに行数超過はない


def test_unassigned_labels_counted(result):
    """印字ラベル（TEL・氏名・郵便番号等）は枠外に落ち unassigned_other に載る。"""
    assert result.unassigned_other > 0
