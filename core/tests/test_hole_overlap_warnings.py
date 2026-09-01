"""穴（切り抜き）どうしの重なり警告 W-4 のテスト（issue #66 第2弾・段6）。

背景（05 の F-12・ぼたん Phase 2 レビュー B の経路B）: mapping の空間インデックス
（mapping._bucket_cells）は「領域→参照先→穴」の3層 first-hit で、層をまたぐ
優先順位は配列順と無関係だが、**穴（extra_rects を持つ単発欄の切り抜き穴・
template.hole_bbox）どうしの重なりだけは、load_template の欄矩形の重なり拒否
（issue #24）の母集団に入っておらず、配列順依存が残る**。第2弾で列の並べ替え
（配列順変更）を許すため、この残存する順序依存を W-4 警告で可視化する。

拒否ではなく警告——W-1/W-2/W-3 と同じ「見える化のみ」方針（現行出荷テンプレは
非発火。切り抜きの増加で将来発生しうる事象を止める理由が無い）。
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


def _w1(t):
    return [w for w in t.warnings if w.startswith("[W-1]")]


def _w3(t):
    return [w for w in t.warnings if w.startswith("[W-3]")]


def _w4(t):
    return [w for w in t.warnings if w.startswith("[W-4]")]


# ---------- 出荷テンプレの実測 ----------

def test_shipped_template_w4_is_zero_and_existing_counts_unchanged():
    """出荷テンプレは W-4=0（person_住所1/住所2 の穴は y 帯が重ならない・実測）。

    既存の W-1=3・W-3=12 の件数固定テストが本変更で崩れていないことも
    同時に確認する（W-4 追加が既存警告の母集団・件数に影響しないこと）。
    """
    t = load_template(TPL)
    assert _w4(t) == []
    assert len(_w1(t)) == 3
    assert len(_w3(t)) == 12


def test_load_template_still_succeeds_with_shipped_holes():
    """出荷テンプレの穴（person_住所1/住所2）があっても load_template は
    例外を出さない（W-4 は拒否ではなく警告）。"""
    load_template(TPL)  # 例外にならなければ良い


# ---------- 検出ロジック: 穴どうしが重なる合成テンプレ ----------

def _free_spot_fields(a_extra_first: bool = True):
    """他の欄と重ならない面右下の空き位置に、穴（extra_rects）が重なり合う
    2つの text 欄を作る（front 面）。

    field A: 主 (2000,1700,60,40) + 追加 (2300,1820,60,40)
             → 穴 BBox = (2000,1700,360,160)
    field B: 主 (2100,1780,60,40) + 追加 (2200,1740,60,40)
             → 穴 BBox = (2100,1740,160,80)
    実際の物理矩形（主・追加）どうしは一切重ならない（issue #24 の重なり拒否に
    引っかからない）が、穴の BBox どうしは x:[2100,2260]・y:[1740,1820] で
    重なる——real コードで実測済み（座標は script で衝突なしを確認した値）。
    """
    def field(fid, main, extra):
        return {
            "field_id": fid, "kind": "text",
            "rect": {"x": main[0], "y": main[1], "w": main[2], "h": main[3]},
            "extra_rects": [{"x": extra[0], "y": extra[1], "w": extra[2], "h": extra[3]}],
        }
    a = field("hole_test_a", (2000, 1700, 60, 40), (2300, 1820, 60, 40))
    b = field("hole_test_b", (2100, 1780, 60, 40), (2200, 1740, 60, 40))
    return (a, b) if a_extra_first else (b, a)


def test_overlapping_holes_trigger_w4_with_both_field_ids(tmp_path, raw):
    a, b = _free_spot_fields()
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))  # 拒否されない（物理矩形は重ならない）

    w4 = _w4(t)
    assert len(w4) == 1
    assert "hole_test_a" in w4[0] and "hole_test_b" in w4[0]
    assert "配列順" in w4[0]  # 並べ替えで割付が変わりうることを伝える文言


def test_overlapping_holes_warning_is_symmetric_in_field_order(tmp_path, raw):
    """テンプレート内の配列順（どちらを先に定義するか）に関わらず、同じ
    ペアには同じ1件の W-4 が出る（警告そのものが順序依存を可視化する道具で
    あって、警告の有無自体が順序に依存してはならない）。
    """
    a, b = _free_spot_fields(a_extra_first=False)
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))
    w4 = _w4(t)
    assert len(w4) == 1
    assert "hole_test_a" in w4[0] and "hole_test_b" in w4[0]


def test_non_overlapping_holes_do_not_trigger_w4(tmp_path, raw):
    """穴の BBox が重ならない2欄（y 帯を離す）では W-4 が出ない（誤検知しない）。"""
    def field(fid, main, extra):
        return {
            "field_id": fid, "kind": "text",
            "rect": {"x": main[0], "y": main[1], "w": main[2], "h": main[3]},
            "extra_rects": [{"x": extra[0], "y": extra[1], "w": extra[2], "h": extra[3]}],
        }
    a = field("hole_far_a", (2000, 1700, 60, 40), (2300, 1745, 60, 40))  # 穴 y:[1700,1785]
    b = field("hole_far_b", (2000, 1800, 60, 40), (2300, 1835, 60, 40))  # 穴 y:[1800,1875]
    raw["faces"][0]["fields"] += [a, b]
    t = load_template(write(tmp_path, raw))
    assert not any("hole_far_a" in w or "hole_far_b" in w for w in _w4(t))


def test_single_hole_field_alone_does_not_trigger_w4(tmp_path, raw):
    """穴を持つ欄が1つだけ（比較対象が無い）では W-4 は発火しない。"""
    a, _b = _free_spot_fields()
    raw["faces"][0]["fields"].append(a)
    t = load_template(write(tmp_path, raw))
    assert _w4(t) == []
