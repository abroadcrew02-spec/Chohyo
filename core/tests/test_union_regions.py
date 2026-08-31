"""欄の複数領域（extra_rects・L字欄）のテスト（2026-08-31 新設）。

仕様: 追加領域は主の rect と等価な受け皿。どの領域に入った文字も同じ欄に
集まり、読み順（座標順）で1つの値になる。出力列は増えない。
対象は文字欄（kind=text）のみ。参照先（fallback_rect）は「主＝全領域が空」
のときだけ読まれる。
"""
import json

import pytest

from chouhyo_ocr.mapping import Symbol, assign
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import TemplateError, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"

# 実テンプレートの表面下端の余白（person_備考 の下端 1661 より下・面高 1880 未満）
SPOT_A = {"x": 2300, "y": 1670, "w": 120, "h": 60}
SPOT_B = {"x": 2100, "y": 1670, "w": 120, "h": 60}
SPOT_C = {"x": 2300, "y": 1790, "w": 120, "h": 60}


@pytest.fixture()
def raw():
    return json.loads(TPL.read_text(encoding="utf-8"))


def write(tmp_path, data):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _first_text_field(raw):
    for fld in raw["faces"][0]["fields"]:
        if fld["kind"] == "text":
            return fld
    raise AssertionError("text の単独欄が無い")


def _sym(text, rect, dx=5, dy=5, conf=0.98):
    return Symbol(text=text, x=rect["x"] + dx, y=rect["y"] + dy, conf=conf)


def _run(t, syms_front):
    return assign(t.cells, {"front": syms_front, "back": []}, t.faces)


# ---------- テンプレート検証 ----------

def test_accepts_extra_rects_on_text_field(tmp_path, raw):
    fld = _first_text_field(raw)
    fld["extra_rects"] = [SPOT_A, SPOT_B]
    t = load_template(write(tmp_path, raw))
    cell = next(c for c in t.cells if c.field_id == fld["field_id"])
    assert len(cell.extra_rects) == 2
    assert len(cell.all_rects()) == 3


def test_extra_rects_do_not_change_columns(tmp_path, raw):
    before = load_template(TPL)
    n = sum(len(c.output_columns()) for c in before.cells)
    fld = _first_text_field(raw)
    fld["extra_rects"] = [SPOT_A]
    after = load_template(write(tmp_path, raw))
    assert sum(len(c.output_columns()) for c in after.cells) == n


def test_rejects_extra_rects_on_choice(tmp_path, raw):
    choice = next(f for f in raw["faces"][0]["fields"] if f["kind"] == "choice")
    choice["extra_rects"] = [SPOT_A]
    with pytest.raises(TemplateError, match="text"):
        load_template(write(tmp_path, raw))


def test_own_regions_may_overlap_each_other(tmp_path, raw):
    """同じ欄の領域どうしの重なりは無害（同じ受け皿）なので許す。"""
    fld = _first_text_field(raw)
    fld["extra_rects"] = [
        {**SPOT_A},
        {"x": SPOT_A["x"] + 20, "y": SPOT_A["y"] + 10, "w": 120, "h": 60},
    ]
    load_template(write(tmp_path, raw))  # 例外にならない


def test_rejects_extra_overlapping_other_cell(tmp_path, raw):
    fields = [f for f in raw["faces"][0]["fields"] if f["kind"] == "text"]
    a, b = fields[0], fields[1]
    rb = b["rect"]
    a["extra_rects"] = [{"x": rb["x"] + 2, "y": rb["y"] + 2, "w": 20, "h": 20}]
    with pytest.raises(TemplateError, match="重なっている"):
        load_template(write(tmp_path, raw))


def test_rejects_extra_outside_face(tmp_path, raw):
    fld = _first_text_field(raw)
    face_h = raw["faces"][0]["source"]["rect"]["h"]
    fld["extra_rects"] = [{"x": 10, "y": face_h - 10, "w": 50, "h": 50}]
    with pytest.raises(TemplateError, match="はみ出している"):
        load_template(write(tmp_path, raw))


def test_rejects_fallback_overlapping_own_extra(tmp_path, raw):
    """参照先は追加領域とも重なれない（「主＝全領域が空」の判定が壊れる）。"""
    fld = _first_text_field(raw)
    fld["extra_rects"] = [SPOT_A]
    fld["fallback_rect"] = {"x": SPOT_A["x"] + 10, "y": SPOT_A["y"] + 10,
                            "w": 40, "h": 30}
    with pytest.raises(TemplateError, match="主の枠と重なっている"):
        load_template(write(tmp_path, raw))


# ---------- 割付 ----------

@pytest.fixture()
def tpl_l_shape(tmp_path, raw):
    fld = _first_text_field(raw)
    fld["extra_rects"] = [SPOT_A]
    return load_template(write(tmp_path, raw)), fld["field_id"], dict(fld["rect"])


def test_chars_from_all_regions_join_in_reading_order(tpl_l_shape):
    """主と追加領域の文字が1つの欄に集まり、座標順に連結される。"""
    t, fid, primary = tpl_l_shape
    # SPOT_A（y=1600）は主（本人欄・上部）より下 → 読み順では後
    res = _run(t, [_sym("後", SPOT_A), _sym("前", primary)])
    assert res.cells[fid].text == "前後"


def test_extra_only_still_fills_the_cell(tpl_l_shape):
    t, fid, _primary = tpl_l_shape
    res = _run(t, [_sym("あ", SPOT_A)])
    assert res.cells[fid].text == "あ"


def test_conf_min_across_regions(tpl_l_shape):
    t, fid, primary = tpl_l_shape
    res = _run(t, [_sym("あ", primary, conf=0.9), _sym("い", SPOT_A, conf=0.2)])
    assert res.cells[fid].conf_min == pytest.approx(0.2)


def test_fallback_consulted_only_when_all_regions_empty(tmp_path, raw):
    fld = _first_text_field(raw)
    fld["extra_rects"] = [SPOT_A]
    fld["fallback_rect"] = SPOT_C
    t = load_template(write(tmp_path, raw))
    fid = fld["field_id"]
    # 追加領域に文字がある → 参照先は読まれない
    res = _run(t, [_sym("領", SPOT_A), _sym("参", SPOT_C)])
    assert res.cells[fid].text == "領"
    # 全領域が空 → 参照先を読む
    res2 = _run(t, [_sym("参", SPOT_C)])
    assert res2.cells[fid].text == "参"
