"""E2E: 入力フォルダ → run（Replay・課金ゼロ）→ .xlsx/.csv → 配置検証。

保存済み S2 応答と展開済みサンプル画像に依存する（.gitignore 配下・
このマシン限定）。無い環境では skip。

検証は要件 §8 第1層（配置の正しさ）と再現性（§6.2 バイト一致）。
"""
import json
import shutil

import pytest
from openpyxl import load_workbook

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import remap, render, run
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・展開画像が無い環境")


@pytest.fixture(scope="module")
def outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("e2e")
    input_dir = tmp / "input"
    input_dir.mkdir()
    shutil.copy(PAGE_PNG, input_dir / "sample-1.png")

    replay_dir = tmp / "responses"
    replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    # 配置（第1層）の検証が目的のため〓閾値を下げる。既定閾値での〓化は
    # test_default_threshold_masks_low_conf で別途検証する。
    cfg = Config(unclear_threshold=0.4,
                 output_dir=str(tmp / "out"), workdir=str(tmp / "wd"), log_dir=str(tmp / "logs"))
    summary = run(input_dir, TPL, cfg, ReplayClient(replay_dir))
    xlsx, csvp, rows = render(TPL, cfg, timestamp="t1")
    return {"cfg": cfg, "summary": summary, "xlsx": xlsx, "csv": csvp, "rows": rows}


def sheet(outputs):
    wb = load_workbook(outputs["xlsx"])
    ws = wb["output"]
    header = [c.value for c in ws[1]]
    data = [c.value for c in ws[2]]
    return header, data


def val(outputs, col):
    header, data = sheet(outputs)
    return data[header.index(col)]


def test_one_page_one_row(outputs):
    assert outputs["summary"].pages == 1
    assert len(outputs["rows"]) == 1
    assert val(outputs, "ステータス") == "正常"
    assert val(outputs, "帳票ID") == "sample-1_p0001"


def test_placement_person(outputs):
    # 郵便番号は独立列（2026-08-31）。主（印字ボックス）が空のサンプルなので
    # 参照先（住所行の先頭ゾーン）から拾われ、住所列には混ざらない
    assert val(outputs, "person_郵便番号1") == "262-0032"
    assert val(outputs, "person_住所1").startswith("千葉県千葉市")
    assert "北海道旭川市" in val(outputs, "person_住所2")


def test_placement_family_date_split(outputs):
    """複合セル「7.7.20」が render で 年/月/日 3列へ分かれる（D-23）。"""
    assert val(outputs, "family_01_生年月日_年") == "7"
    assert val(outputs, "family_01_生年月日_月") == "7"
    assert val(outputs, "family_01_生年月日_日") == "20"


def test_placement_detail_row4_no_cross_contamination(outputs):
    """word 結合で混線していた行が正しい列に入る（symbol 割付の E2E 実証）。"""
    assert val(outputs, "detail_04_来店年月日_年") == "10"
    assert val(outputs, "detail_04_来店年月日_月") == "8"
    assert val(outputs, "detail_04_来店年月日_日") == "31"
    assert val(outputs, "detail_04_金額") == 1000        # 数値型
    assert val(outputs, "detail_04_品目") == "合格祈願"


def test_amount_types(outputs):
    assert val(outputs, "detail_02_金額") == 100000
    assert isinstance(val(outputs, "detail_02_金額"), int)
    assert val(outputs, "detail_03_金額") == 100


def test_empty_rows_are_empty_strings(outputs):
    assert val(outputs, "family_05_続柄") in ("", None)   # 空行 → 空文字
    assert val(outputs, "detail_10_品目") in ("", None)


def test_countif_formula_and_csv_static_agree(outputs):
    header, data = sheet(outputs)
    # 範囲の終端はテンプレート由来の列数から導出する（決め打ち廃止・2026-08-31。
    # 現行 220列なら HL）。リテラルで固定すると列の増減のたびにここが割れる
    from chouhyo_ocr.columns import derive_columns, excel_column_letter
    from chouhyo_ocr.template import load_template
    last = excel_column_letter(len(derive_columns(load_template(TPL))))
    assert data[0] == f'=COUNTIF(G2:{last}2,"〓")'
    lines = outputs["csv"].read_text(encoding="utf-8-sig").splitlines()
    csv_row = next(csv_line for csv_line in lines[1:] if csv_line)
    static = int(csv_row.split('","')[0].strip('"'))
    unclear_in_xlsx = sum(1 for v in data[6:] if v == "〓")
    assert static == unclear_in_xlsx == outputs["rows"][0].unclear_count


def test_min_conf_present_and_format(outputs):
    v = val(outputs, "最低信頼度")
    assert v and len(v.split(".")[1]) == 3


def test_era_decisions_are_valid_values(outputs):
    for col in ("person_生年月日_元号", "family_01_生年月日_元号"):
        assert val(outputs, col) in ("昭", "平", "令", "〓")


def test_default_threshold_masks_low_conf(outputs):
    """既定閾値 0.85 では低信頼セル（住所1 conf 0.83）が〓化される（§8-3 実証）。"""
    import dataclasses
    cfg85 = dataclasses.replace(outputs["cfg"], unclear_threshold=0.85)
    xlsx, _csv, _rows = render(TPL, cfg85, timestamp="t85")
    wb = load_workbook(xlsx)
    ws = wb["output"]
    header = [c.value for c in ws[1]]
    data = [c.value for c in ws[2]]
    assert data[header.index("person_住所1")] == "〓"   # 低信頼 symbol を含むため
    assert data[header.index("detail_01_品目")] == "家内安全"  # 高信頼は残る


def test_render_determinism_bytes(outputs):
    """同一中間データ・同一設定 → xlsx/csv ともバイト一致（要件 §6.2）。"""
    cfg = outputs["cfg"]
    x1, c1, _ = render(TPL, cfg, timestamp="d1")
    x2, c2, _ = render(TPL, cfg, timestamp="d2")
    assert x1.read_bytes() == x2.read_bytes()
    assert c1.read_bytes() == c2.read_bytes()


def test_remap_is_idempotent_without_template_change(outputs):
    cfg = outputs["cfg"]
    before = render(TPL, cfg, timestamp="r1")[0].read_bytes()
    assert remap(TPL, cfg) == 1
    after = render(TPL, cfg, timestamp="r2")[0].read_bytes()
    assert before == after
