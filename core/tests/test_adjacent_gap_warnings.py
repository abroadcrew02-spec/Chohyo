"""受け皿間の隙間（死角）警告 W-3 のテスト（#61 L-4・2026-08-31）。

拒否ではなく警告——テンプレート座標そのものは変えない（geometry_hash が
変わり全ページ再送信になるため。データ修正は管理者判断）。
"""
import json

import pytest

from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


@pytest.fixture()
def raw():
    return json.loads(TPL.read_text(encoding="utf-8"))


def write(tmp_path, data):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _w3(t):
    return [w for w in t.warnings if w.startswith("[W-3]")]


# ---------- 出荷テンプレの実測（issue #61 L-4 記載の死角） ----------

def test_shipped_template_flags_postal_to_address_gaps():
    """person_郵便番号1→住所1 の1px、郵便番号2→住所2 の4px が W-3 として出る。

    issue #61 L-4 の実測値そのもの（x=648 の1列・郵便番号2側は4列）。
    テンプレート座標は変えず、見える化だけを行う。
    """
    t = load_template(TPL)
    w3 = _w3(t)
    assert any("person_郵便番号1" in w and "person_住所1" in w and "1px" in w
               for w in w3)
    assert any("person_郵便番号2" in w and "person_住所2" in w and "4px" in w
               for w in w3)


def test_shipped_template_w3_count_is_stable():
    """出荷テンプレの W-3 件数を固定する（回帰検知）。

    件数が増減したら、テンプレート座標か検出ロジックのどちらかが変わった
    合図——中身（上のテストで固定した2件を含むか）と合わせて確認する。
    """
    t = load_template(TPL)
    assert len(_w3(t)) == 12


def test_load_template_still_succeeds_with_gaps():
    """隙間があっても load_template は例外を出さない（拒否しない）。"""
    load_template(TPL)  # 例外にならなければ良い


# ---------- 検出ロジックの境界 ----------

def _free_rects(raw):
    """他の欄と重ならない面右下の空き位置に、2つの text 欄を新設する下ごしらえ。"""
    front = raw["faces"][0]
    base = dict(front["fields"][0])
    a = dict(base, field_id="w3_test_a",
             rect={"x": 2000, "y": 1700, "w": 100, "h": 60})
    a.pop("fallback_rect", None)
    a.pop("choice_marks", None)
    a.pop("extra_rects", None)
    a["kind"] = "text"
    b = dict(a, field_id="w3_test_b")
    return a, b


def test_zero_gap_is_not_flagged(tmp_path, raw):
    """0px（接触）は死角ではないので W-3 は出ない。"""
    a, b = _free_rects(raw)
    b["rect"] = {"x": a["rect"]["x"] + a["rect"]["w"], "y": a["rect"]["y"],
                 "w": 100, "h": 60}  # a の右端に接する
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))
    w3 = _w3(t)
    assert not any("w3_test_a" in w and "w3_test_b" in w for w in w3)


def test_one_px_gap_is_flagged(tmp_path, raw):
    """1px の隙間は境界値として検出される（GAP_MIN_PX=1）。"""
    a, b = _free_rects(raw)
    b["rect"] = {"x": a["rect"]["x"] + a["rect"]["w"] + 1, "y": a["rect"]["y"],
                 "w": 100, "h": 60}
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))
    w3 = _w3(t)
    assert any("w3_test_a" in w and "w3_test_b" in w and "1px" in w for w in w3)


def test_gap_not_flagged_when_third_receptor_blocks(tmp_path, raw):
    """隙間の間に第三の受け皿があれば「隣接」ではないので警告しない。

    面全体の網羅的な総当たりを避け、真に隣り合うペアだけを見る設計の確認。
    """
    a, b = _free_rects(raw)
    b["rect"] = {"x": a["rect"]["x"] + a["rect"]["w"] + 20, "y": a["rect"]["y"],
                 "w": 100, "h": 60}
    c = dict(a, field_id="w3_test_c",
             rect={"x": a["rect"]["x"] + a["rect"]["w"] + 5,
                   "y": a["rect"]["y"], "w": 10, "h": 60})
    raw["faces"][0]["fields"] += [a, b, c]
    t = load_template(write(tmp_path, raw))
    w3 = _w3(t)
    assert not any("w3_test_a" in w and "w3_test_b" in w for w in w3)
    # a-c・c-b はそれぞれ正しく隣接として検出される
    assert any("w3_test_a" in w and "w3_test_c" in w for w in w3)
    assert any("w3_test_c" in w and "w3_test_b" in w for w in w3)


def test_gap_not_flagged_across_different_y_bands(tmp_path, raw):
    """y 帯が重ならなければ、x 方向に隙間があっても対象外（同じ行でない）。"""
    a, b = _free_rects(raw)
    b["rect"] = {"x": a["rect"]["x"] + a["rect"]["w"] + 20,
                 "y": a["rect"]["y"] + a["rect"]["h"] + 5,  # y が重ならない
                 "w": 100, "h": 60}
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))
    w3 = _w3(t)
    assert not any("w3_test_a" in w and "w3_test_b" in w for w in w3)


def test_same_field_own_regions_not_flagged(tmp_path, raw):
    """同じ欄の主枠と参照先の間に隙間があっても W-3 は出さない（L字設計への配慮）。"""
    a, _b = _free_rects(raw)
    a["fallback_rect"] = {"x": a["rect"]["x"] + a["rect"]["w"] + 10,
                          "y": a["rect"]["y"], "w": 50, "h": 60}
    raw["faces"][0]["fields"].append(a)
    t = load_template(write(tmp_path, raw))
    w3 = _w3(t)
    assert not any("w3_test_a" in w for w in w3)
