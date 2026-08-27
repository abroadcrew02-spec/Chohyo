"""template.py: 読み込み・v1 受け入れ範囲・格子展開のテスト。

実テンプレート（templates/chouhyo-v1.json）を正として使い、拒否系は
その複製を壊して確かめる。
"""
import copy
import json

import pytest

from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import Rect, TemplateError, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


@pytest.fixture()
def raw():
    return json.loads(TPL.read_text(encoding="utf-8"))


def write(tmp_path, data):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_load_real_template():
    t = load_template(TPL)
    assert t.template_id == "chouhyo-v1"
    assert t.image_size == (2490, 3510)
    assert t.record_pages == 1
    assert {f.face_id for f in t.faces} == {"front", "back"}


def test_grid_expansion_rows_and_ids():
    t = load_template(TPL)
    fam = [c for c in t.cells if c.field_id.startswith("family_")]
    det = [c for c in t.cells if c.field_id.startswith("detail_")]
    # 家族: 10行 × 物理4列 / 明細: 28行 × 5列
    assert len(fam) == 40
    assert len(det) == 140
    # 行連番はブロックを跨いで連続（家族: 左01-05・右06-10 / 明細: 左01-14・右15-28）
    assert any(c.field_id == "family_06_続柄" for c in fam)
    assert any(c.field_id == "detail_15_品目" for c in det)
    assert not any(c.field_id.startswith("detail_29") for c in det)


def test_row_pitch_no_drift():
    """行の上端は origin.y + pitch*i の算術で決まる（ドリフトしない）。"""
    t = load_template(TPL)
    tops = [c.rect.y for c in t.cells
            if c.field_id.startswith("detail_") and c.field_id.endswith("_金額")]
    left = tops[:14]
    diffs = {b - a for a, b in zip(left, left[1:])}
    assert diffs == {104}


def test_subfields_output_columns():
    t = load_template(TPL)
    cell = next(c for c in t.cells if c.field_id == "family_01_生年月日")
    assert cell.subfields == ("年", "月", "日")
    assert cell.output_columns() == (
        "family_01_生年月日_年", "family_01_生年月日_月", "family_01_生年月日_日")


def test_choice_marks_vertical_stack():
    """家族欄の昭平令は行内に縦積み（y_offset が効いている）。"""
    t = load_template(TPL)
    cell = next(c for c in t.cells if c.field_id == "family_01_生年月日_元号")
    ys = [m.rect.y for m in cell.choice_marks]
    assert len(cell.choice_marks) == 3
    assert ys == sorted(ys) and len(set(ys)) == 3


def test_reject_unknown_schema_version(tmp_path, raw):
    raw["schema_version"] = 2
    with pytest.raises(TemplateError, match="schema_version"):
        load_template(write(tmp_path, raw))


def test_reject_multi_page_record(tmp_path, raw):
    raw["record"]["pages"] = 2
    with pytest.raises(TemplateError, match="record.pages"):
        load_template(write(tmp_path, raw))


def test_reject_unknown_face_id(tmp_path, raw):
    raw["faces"][0]["face_id"] = "left"
    with pytest.raises(TemplateError, match="face_id"):
        load_template(write(tmp_path, raw))


def test_reject_row_height_over_pitch(tmp_path, raw):
    t = raw["faces"][0]["tables"][0]
    t["row_height"] = t["row_pitch"] + 1
    with pytest.raises(TemplateError, match="row_height"):
        load_template(write(tmp_path, raw))


def test_reject_duplicate_field_id(tmp_path, raw):
    raw["faces"][0]["fields"][1]["field_id"] = raw["faces"][0]["fields"][0]["field_id"]
    with pytest.raises(TemplateError, match="重複"):
        load_template(write(tmp_path, raw))


def test_reject_schema_violation(tmp_path, raw):
    del raw["faces"][0]["source"]
    with pytest.raises(TemplateError, match="スキーマ検証エラー"):
        load_template(write(tmp_path, raw))


def test_face_local_rects_within_face(tmp_path):
    """全セル・全マークが面の寸法（source.rect の w×h）に収まる。"""
    t = load_template(TPL)
    for c in t.cells:
        f = t.face(c.face_id)
        w, h = f.source_rect.w, f.source_rect.h
        assert 0 <= c.rect.x and c.rect.x + c.rect.w <= w, c.field_id
        assert 0 <= c.rect.y and c.rect.y + c.rect.h <= h, c.field_id
        for m in c.choice_marks:
            assert 0 <= m.rect.x and m.rect.x + m.rect.w <= w, c.field_id
            assert 0 <= m.rect.y and m.rect.y + m.rect.h <= h, c.field_id
