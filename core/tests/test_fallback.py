"""参照先（fallback_rect）欄のテスト（2026-08-31 新設）。

仕様: 主の枠が**完全に空**（symbol が1つも来ない）ときに限り、参照先の枠の
読取値を採用する。主にインクがあって読めない場合は〓のまま（誤転記防止・
転記主義）。対象は単独の文字欄（kind=text）のみ。

テンプレート検証は実テンプレートの複製を壊して確かめる（test_template.py と
同じ流儀・実サンプル素材に依存しないため全環境で走る）。
"""
import copy
import json

import pytest

from chouhyo_ocr.mapping import Symbol, assign
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import TemplateError, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


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
    raise AssertionError("実テンプレートに text の単独欄が無い")


def _free_spot(raw):
    """他の欄と重ならない空き位置（実テンプレートの表面右下の余白）。"""
    return {"x": 2300, "y": 1700, "w": 150, "h": 60}


# ---------- テンプレート検証 ----------

def test_accepts_fallback_on_text_field(tmp_path, raw):
    fld = _first_text_field(raw)
    fld["fallback_rect"] = _free_spot(raw)
    t = load_template(write(tmp_path, raw))
    cell = next(c for c in t.cells if c.field_id == fld["field_id"])
    assert cell.fallback_rect is not None
    assert cell.fallback_rect.x == 2300


def test_fallback_does_not_change_output_columns(tmp_path, raw):
    """参照先は出力列を増やさない（218列の契約は不変）。"""
    before = load_template(TPL)
    n_before = sum(len(c.output_columns()) for c in before.cells)
    fld = _first_text_field(raw)
    fld["fallback_rect"] = _free_spot(raw)
    after = load_template(write(tmp_path, raw))
    assert sum(len(c.output_columns()) for c in after.cells) == n_before


def test_rejects_fallback_on_choice_field(tmp_path, raw):
    choice = next(f for f in raw["faces"][0]["fields"] if f["kind"] == "choice")
    choice["fallback_rect"] = _free_spot(raw)
    with pytest.raises(TemplateError, match="text"):
        load_template(write(tmp_path, raw))


def test_rejects_fallback_overlapping_own_primary(tmp_path, raw):
    fld = _first_text_field(raw)
    r = fld["rect"]
    fld["fallback_rect"] = {"x": r["x"] + 5, "y": r["y"] + 5, "w": 30, "h": 20}
    with pytest.raises(TemplateError, match="主の枠と重なっている"):
        load_template(write(tmp_path, raw))


def test_rejects_fallback_overlapping_other_cell(tmp_path, raw):
    fields = [f for f in raw["faces"][0]["fields"] if f["kind"] == "text"]
    assert len(fields) >= 2, "検証には text 欄が2つ要る"
    a, b = fields[0], fields[1]
    rb = b["rect"]
    a["fallback_rect"] = {"x": rb["x"] + 2, "y": rb["y"] + 2, "w": 20, "h": 20}
    with pytest.raises(TemplateError, match="重なっている"):
        load_template(write(tmp_path, raw))


def test_rejects_fallback_outside_face(tmp_path, raw):
    fld = _first_text_field(raw)
    face_h = raw["faces"][0]["source"]["rect"]["h"]
    fld["fallback_rect"] = {"x": 10, "y": face_h - 10, "w": 50, "h": 50}
    with pytest.raises(TemplateError, match="はみ出している"):
        load_template(write(tmp_path, raw))


# ---------- 割付の合流 ----------

@pytest.fixture()
def tpl_with_fallback(tmp_path, raw):
    fld = _first_text_field(raw)
    fld["fallback_rect"] = _free_spot(raw)
    return load_template(write(tmp_path, raw)), fld["field_id"], _free_spot(raw)


def _sym(text, rect, dx=5, conf=0.98):
    """rect 内の1文字（座標は面ローカル・中心点）。"""
    return Symbol(text=text, x=rect["x"] + dx, y=rect["y"] + 5, conf=conf)


def _run(t, syms_front):
    return assign(t.cells, {"front": syms_front, "back": []}, t.faces)


def test_primary_wins_when_filled(tpl_with_fallback):
    t, fid, fb = tpl_with_fallback
    primary = next(c.rect for c in t.cells if c.field_id == fid)
    pr = {"x": primary.x, "y": primary.y}
    res = _run(t, [_sym("主", pr), _sym("値", pr, dx=25),
                   _sym("参", fb), _sym("照", fb, dx=25)])
    assert res.cells[fid].text == "主値"


def test_fallback_used_when_primary_empty(tpl_with_fallback):
    t, fid, fb = tpl_with_fallback
    res = _run(t, [_sym("参", fb), _sym("照", fb, dx=25)])
    assert res.cells[fid].text == "参照"


def test_fallback_confidence_flows_to_cell(tpl_with_fallback):
    """参照先の低信頼はそのままセルの conf_min になる（〓判定が普通に効く）。"""
    t, fid, fb = tpl_with_fallback
    res = _run(t, [_sym("参", fb, conf=0.10)])
    assert res.cells[fid].conf_min == pytest.approx(0.10)


def test_both_empty_stays_empty(tpl_with_fallback):
    t, fid, _fb = tpl_with_fallback
    res = _run(t, [])
    assert fid not in res.cells


def test_fallback_symbols_not_counted_as_unassigned(tpl_with_fallback):
    """参照先領域の文字は（主に値があって捨てられる場合でも）枠外に数えない。"""
    t, fid, fb = tpl_with_fallback
    primary = next(c.rect for c in t.cells if c.field_id == fid)
    pr = {"x": primary.x, "y": primary.y}
    base = _run(t, [_sym("主", pr)])
    with_fb = _run(t, [_sym("主", pr), _sym("捨", fb)])
    assert with_fb.unassigned_other == base.unassigned_other
    assert with_fb.cells[fid].text == "主"


def test_no_fallback_field_unaffected(tmp_path, raw):
    """fallback を使わないテンプレートの割付は従来と同一（回帰）。"""
    t0 = load_template(TPL)
    fld = _first_text_field(raw)
    primary = fld["rect"]
    pr = {"x": primary["x"], "y": primary["y"]}
    res = _run(t0, [_sym("あ", pr)])
    assert res.cells[fld["field_id"]].text == "あ"
