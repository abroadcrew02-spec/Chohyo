"""template.py: 読み込み・v1 受け入れ範囲・格子展開のテスト。

実テンプレート（templates/chouhyo-v1.json）を正として使い、拒否系は
その複製を壊して確かめる。
"""
import copy
import json
import re

import pytest

import chouhyo_ocr.template as template_mod
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import (CHOICE_MARK_MARGIN_PX, Rect, TemplateError,
                                  load_template)

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


# --- 選択肢マークが欄の内側にあること（issue #48） ---
# 編集画面で欄だけ動かしてマークが取り残されると、無関係な位置のインクで元号が
# 決まり〓ではなく誤った元号が出る。GUI 側の追従修正が効かない経路（手書き JSON・
# 既に壊れた状態で保存されたテンプレート）を止める最後の防衛線を検証する。

def choice_field(raw):
    """front の choice 欄（person_生年月日_元号）を返す。"""
    return next(f for f in raw["faces"][0]["fields"] if f["kind"] == "choice")


def test_reject_choice_mark_left_behind_when_field_moved(tmp_path, raw):
    """#48 の再現: 欄だけ上へ移動し、マークを元の座標に残す。

    移動先 y=20 は他の欄と重ならない位置（person_電話番号 は x>=1860・
    person_ふりがな は x<=1740）なので、重なり検証(#24)ではなくマーク検証が理由で
    落ちることをメッセージで確かめる。
    """
    fld = choice_field(raw)
    fld["rect"]["y"] = 20  # 欄は [20,168]・マークは [141,285] のまま
    with pytest.raises(TemplateError, match="選択肢の枠が欄からはみ出している"):
        load_template(write(tmp_path, raw))


def test_reject_choice_mark_fully_outside_field(tmp_path, raw):
    """マークが欄と一切重ならない位置にある場合も落ちる。"""
    fld = choice_field(raw)
    for m in fld["choice_marks"]:
        m["rect"]["y"] += 400  # 面内には収まるが欄からは完全に外れる
    with pytest.raises(TemplateError, match="選択肢の枠が欄からはみ出している"):
        load_template(write(tmp_path, raw))


def test_reject_choice_mark_partially_outside_field(tmp_path, raw):
    """一部だけ欄の外にはみ出すケースも落ちる（半分入っていれば良しとしない）。"""
    fld = choice_field(raw)
    mark = fld["choice_marks"][-1]  # 「令」: y=238 h=47・欄の下端は 283
    mark["rect"]["y"] = 258  # 下端 305 → 22px はみ出し（上端 258 は欄の内側）
    with pytest.raises(TemplateError, match="令"):
        load_template(write(tmp_path, raw))


def snap_marks_inside(raw):
    """全 choice マークを欄の内側へ寄せる（出荷テンプレの丸め1〜2px を消す）。

    許容 CHOICE_MARK_MARGIN_PX=0 で境界を試すための下ごしらえ。許容が効いた
    ままでは「はみ出し0」は自明に通ってしまい、境界をどちらへ倒したかを固定できない。
    """
    def clamp(lo, hi, v):
        return max(lo, min(hi, v))

    for face in raw["faces"]:
        for fld in face.get("fields", []):
            r = fld["rect"]
            for m in fld.get("choice_marks", []):
                q = m["rect"]
                q["w"], q["h"] = min(q["w"], r["w"]), min(q["h"], r["h"])
                q["x"] = clamp(r["x"], r["x"] + r["w"] - q["w"], q["x"])
                q["y"] = clamp(r["y"], r["y"] + r["h"] - q["h"], q["y"])
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                for m in c.get("choice_marks", []):
                    # 列マークの x_offset・y_offset はブロック原点／行上端からの
                    # 相対（template._expand_table）。欄は列と同じ原点なので
                    # 列の x_offset・width と row_height の内側へ寄せれば足りる
                    m["width"] = min(m["width"], c["width"])
                    m["x_offset"] = clamp(c["x_offset"],
                                          c["x_offset"] + c["width"] - m["width"],
                                          m["x_offset"])
                    y = m.get("y_offset", 0)
                    h = min(m.get("height", tb["row_height"] - y), tb["row_height"])
                    m["y_offset"] = clamp(0, tb["row_height"] - h, y)
                    m["height"] = h
    return raw


def test_choice_mark_flush_with_field_edge_is_accepted(tmp_path, raw, monkeypatch):
    """欄の辺にちょうど接するマークは内側扱い（矩形は [x, x+w) の半開区間）。

    許容を 0 に落として確かめる。許容 4px のままだと はみ出し0 は許容に吸われて
    通るだけで、判定を `over >= 許容` と書き間違えても気付けない（この形なら
    書き間違いで落ちる）。許容そのものの境界は test_choice_mark_margin_boundary。
    """
    monkeypatch.setattr(template_mod, "CHOICE_MARK_MARGIN_PX", 0)
    snap_marks_inside(raw)  # 出荷テンプレの丸め1〜2px を消し、厳密内包の状態にする
    fld = choice_field(raw)
    r, mark = fld["rect"], fld["choice_marks"][-1]
    mark["rect"]["x"] = r["x"] + r["w"] - mark["rect"]["w"]  # 右端ぴったり
    mark["rect"]["y"] = r["y"] + r["h"] - mark["rect"]["h"]  # 下端ぴったり
    t = load_template(write(tmp_path, raw))
    q = next(c for c in t.cells if c.field_id == fld["field_id"]).choice_marks[-1].rect
    assert q.x + q.w == r["x"] + r["w"]
    assert q.y + q.h == r["y"] + r["h"]


@pytest.mark.parametrize("over,rejected", [(CHOICE_MARK_MARGIN_PX, False),
                                           (CHOICE_MARK_MARGIN_PX + 1, True)])
def test_choice_mark_margin_boundary(tmp_path, raw, over, rejected):
    """許容 CHOICE_MARK_MARGIN_PX ちょうどは通し、1px 超えたら落とす。"""
    fld = choice_field(raw)
    r, mark = fld["rect"], fld["choice_marks"][-1]
    mark["rect"]["y"] = r["y"] + r["h"] + over - mark["rect"]["h"]
    if rejected:
        with pytest.raises(TemplateError, match="選択肢の枠が欄からはみ出している"):
            load_template(write(tmp_path, raw))
    else:
        load_template(write(tmp_path, raw))


def test_real_template_choice_mark_slop_is_2px():
    """出荷テンプレートのはみ出し実測値を固定する（許容 4px の根拠）。

    厳密な内包は満たしていない。最大は下2px（person_生年月日_元号 の「令」:
    欄の下端 135+148=283 に対しマーク 238+47=285）、次が右1px
    （family_*_生年月日_元号: 欄 1060+50=1110 に対しマーク 1075+36=1111）。
    印字文字に合わせて枠を引いたときの丸め。`<= 許容` ではなく実測値そのものを
    固定するのは、テンプレートの座標が動いて丸めが 3〜4px へ悪化したとき——
    つまり許容の余裕が食い潰されたときに、落ちて気付けるようにするため。
    """
    t = load_template(TPL)
    overs = [max(c.rect.x - m.rect.x, c.rect.y - m.rect.y,
                 m.rect.x + m.rect.w - (c.rect.x + c.rect.w),
                 m.rect.y + m.rect.h - (c.rect.y + c.rect.h))
             for c in t.cells for m in c.choice_marks]
    assert len(overs) == 33  # person 3 + family 10行 × 3
    assert max(overs) == 2
    assert max(overs) < CHOICE_MARK_MARGIN_PX  # 許容に余裕が残っていること


def test_choice_mark_blind_spot_envelope():
    """検出できない「欄だけ移動」量を実測で固定する（この検査の限界の明示）。

    見ているのは「マークが欄の中にあるか」だけなので、マークと欄の間に元々ある
    内側余白の分は、欄を動かしてマークを据え置いても検出できない。支配項は許容
    4px ではなくその固有余白（family は左に15px、person は左に8px）で、許容を
    0 に締めても family 右の窓は 19→15px に縮むだけ。template.py のコメントに
    書いた数値がテンプレート座標の変更で古くならないよう、ここで固定する。
    """
    t = load_template(TPL)
    env: dict[str, dict[str, int]] = {}
    for c in t.cells:
        if not c.choice_marks:
            continue
        key = re.sub(r"_\d\d_", "_*_", c.field_id)  # family_01_… → family_*_…
        r = c.rect
        for m in c.choice_marks:
            q = m.rect
            # 欄を右へ d 動かすと左側の余白 (r.x - q.x) が d だけ食われる。
            # 各方向とも「反対側の余白 + 許容」までは検出されない
            slack = {"右": q.x - r.x, "左": (r.x + r.w) - (q.x + q.w),
                     "下": q.y - r.y, "上": (r.y + r.h) - (q.y + q.h)}
            d = env.setdefault(key, {})
            for k, v in slack.items():
                d[k] = min(d.get(k, 10 ** 9), v + CHOICE_MARK_MARGIN_PX)
    assert env == {
        "person_生年月日_元号": {"右": 12, "左": 9, "下": 10, "上": 2},
        "family_*_生年月日_元号": {"右": 19, "左": 3, "下": 6, "上": 5},
    }


@pytest.mark.parametrize("dx,rejected", [(12, False), (13, True)])
def test_choice_mark_blind_spot_is_real_behaviour(tmp_path, raw, dx, rejected):
    """上の envelope が机上でなく load_template の実挙動であることを確かめる。

    person_生年月日_元号 を右へ 12px ずらしてもマークは欄に残り検出されない
    （x=1758+13=1771・右端 1846 で person_電話番号 の x>=1860 とは重ならないので、
    落ちるのはマーク検証が理由）。13px で初めて落ちる。
    """
    fld = choice_field(raw)
    fld["rect"]["x"] += dx
    if rejected:
        with pytest.raises(TemplateError, match="選択肢の枠が欄からはみ出している"):
            load_template(write(tmp_path, raw))
    else:
        load_template(write(tmp_path, raw))
