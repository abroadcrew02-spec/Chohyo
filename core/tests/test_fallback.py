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
    """参照先は出力列を増やさない（列数はテンプレート由来のまま不変）。"""
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


def test_fallback_overlap_message_names_both_fallbacks(tmp_path, raw):
    """両方が参照先どうしの重なりでは、実際に重なっている対象（参照先）を
    指す文言になる（#61 L-3）。

    従来は else 分岐へ落ち、実際には重なっていない相手側の「欄」を指す文言
    （『{b} の欄と重なっている』）を出していた。拒否自体は正しく効くが、
    案内どおり欄を動かしても直らない。
    """
    fields = [f for f in raw["faces"][0]["fields"] if f["kind"] == "text"]
    assert len(fields) >= 2, "検証には text 欄が2つ要る"
    a, b = fields[0], fields[1]
    spot = _free_spot(raw)
    a["fallback_rect"] = dict(spot)
    b["fallback_rect"] = {"x": spot["x"] + 2, "y": spot["y"] + 2,
                          "w": spot["w"], "h": spot["h"]}
    with pytest.raises(TemplateError, match="参照先の枠どうしが") as exc:
        load_template(write(tmp_path, raw))
    assert "の欄と重なっている" not in str(exc.value)


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


def test_fallback_symbols_discarded_count_as_unassigned(tpl_with_fallback):
    """参照先領域の文字は、主に値があって捨てられる場合は枠外に数える（T-08・2026-08-31改訂）。

    旧仕様（#54(a)）は採用しない参照先の中身が再代入されず静かに消えていた。
    U-02 では「消える」を許さず、必ず穴（無ければ枠外）へ回す。ここでは穴の
    無い自由スポットを使うため、破棄分は全て unassigned_other に乗る。
    """
    t, fid, fb = tpl_with_fallback
    primary = next(c.rect for c in t.cells if c.field_id == fid)
    pr = {"x": primary.x, "y": primary.y}
    base = _run(t, [_sym("主", pr), _sym("値", pr, dx=25)])
    with_fb = _run(t, [_sym("主", pr), _sym("値", pr, dx=25),
                       _sym("捨", fb), _sym("却", fb, dx=25)])
    assert with_fb.cells[fid].text == "主値"
    assert with_fb.unassigned_other == base.unassigned_other + 2
    assert with_fb.fallback_discarded == 2


# ---------- U-03: 主が空の3分岐（T-06〜T-10・2026-08-31） ----------

def test_fallback_used_counts_the_field(tpl_with_fallback):
    """T-06: 主が空・参照先2字 → fallback_used は「採用した欄の数」（symbol数ではない）。"""
    t, fid, fb = tpl_with_fallback
    res = _run(t, [_sym("参", fb), _sym("照", fb, dx=25)])
    assert res.cells[fid].text == "参照"
    assert res.cells[fid].origin == "fallback"
    assert res.fallback_used == 1


def test_conflict_when_main_noise_and_fallback_ambiguous(tpl_with_fallback):
    """T-07: 主1字（NOISE_MAX以内）・参照先2字以上 → 矛盾。主のまま保存し origin='conflict'。

    参照先を採らない理由: 主の1文字が本物の記入である可能性を排除できないため
    （U-03）。値そのものは書き換えない（転記主義）。
    """
    t, fid, fb = tpl_with_fallback
    primary = next(c.rect for c in t.cells if c.field_id == fid)
    pr = {"x": primary.x, "y": primary.y}
    res = _run(t, [_sym("ノ", pr), _sym("参", fb), _sym("照", fb, dx=25)])
    assert res.cells[fid].text == "ノ"
    assert res.cells[fid].origin == "conflict"


def test_conflict_does_not_trigger_with_single_fallback_char(tpl_with_fallback):
    """主1字・参照先1字（n_fb<2）は矛盾条件を満たさない→主を採用（それ以外の分岐）。"""
    t, fid, fb = tpl_with_fallback
    primary = next(c.rect for c in t.cells if c.field_id == fid)
    pr = {"x": primary.x, "y": primary.y}
    res = _run(t, [_sym("ノ", pr), _sym("参", fb)])
    assert res.cells[fid].text == "ノ"
    assert res.cells[fid].origin == ""
    assert res.fallback_discarded == 1


def test_postal_fallback_discard_carves_address_hole():
    """T-09: 実データ経路。person_郵便番号1 の主に記入があり参照先も記入がある場合、
    破棄された参照先の文字は person_住所1 の「穴」（追加領域と主の間の隙間）に落ち、
    住所欄が欄全体〓になる（判定表 #7・H-4 コア層）。
    """
    t = load_template(TPL)
    postal_main = next(c.rect for c in t.cells if c.field_id == "person_郵便番号1")
    # 参照先領域のうち、住所1 の穴（主649-2416x368-439 と追加1129-2416x310-368の
    # 隙間＝ x:649-1129 y:310-368）と重なる位置に置く
    hole_x, hole_y = 700, 320
    syms = [
        _sym("1", {"x": postal_main.x, "y": postal_main.y}),
        _sym("2", {"x": postal_main.x, "y": postal_main.y}, dx=30),
        Symbol(text="9", x=hole_x, y=hole_y, conf=0.95),
        Symbol(text="9", x=hole_x + 20, y=hole_y, conf=0.95),
    ]
    res = _run(t, syms)
    assert res.cells["person_郵便番号1"].text == "12"
    assert res.cells["person_住所1"].text == "〓"
    assert res.cells["person_住所1"].conf_min is None
    assert res.carve_hole == 2
    assert res.fallback_discarded == 2
    # 穴に落ちた文字は枠外（unassigned_other）に数えない（§5.2）
    assert res.unassigned_other == 0


def test_postal_fallback_adopted_does_not_carve_hole():
    """T-10: 正常ページ（主が空で参照先を採用する構成）では穴は発火しない。

    §1.3 の実測（郵便番号の参照先文字は必ず住所欄の穴とほぼ一致するが、
    採用時はそこへ「破棄」ルートを通らないので carve_hole は 0 のまま）と一致。
    """
    t = load_template(TPL)
    postal_fb = next(c.fallback_rect for c in t.cells if c.field_id == "person_郵便番号1")
    syms = [_sym("2", {"x": postal_fb.x, "y": postal_fb.y}, dx=d)
            for d in (5, 30, 55, 80)]
    res = _run(t, syms)
    assert res.cells["person_郵便番号1"].origin == "fallback"
    assert res.carve_hole == 0
    assert "person_住所1" not in res.cells


def test_no_fallback_field_unaffected(tmp_path, raw):
    """fallback を使わないテンプレートの割付は従来と同一（回帰）。"""
    t0 = load_template(TPL)
    fld = _first_text_field(raw)
    primary = fld["rect"]
    pr = {"x": primary["x"], "y": primary["y"]}
    res = _run(t0, [_sym("あ", pr)])
    assert res.cells[fld["field_id"]].text == "あ"
