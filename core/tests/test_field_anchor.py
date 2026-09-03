# -*- coding: utf-8 -*-
"""表を持たない面（単発欄だけの紙）を位置合わせのアンカーにする（issue #86）。

対象: 決定 1〜8（欄アンカーは表の無い面だけで使う／欄矩形の 4 辺／期待位置の
窓だけを見る／探索上限は固定／ALGO_VERSION 据え置き／アンカー 0 本だけ拒否）。

回帰の骨格は**テストではなく構造**にある——`align.estimate_shift` は
`face.table_geoms` の有無で経路を二分し、表を持つ面は従来の
`_collect_table_lines`（既存ループの逐語移動）へ入る。ここではその構造が
保たれていること（不変条件 A・欄経路が呼ばれないこと）を固定する。

合成画像は罫線だけの 2 値配列を直接作る（`test_align_residual.py` の
`_draw_geoms` と同型）。`align_page` をフルパスで通す試験だけは
`align._otsu` を経由するため、中間調を持つ画像を使う（理由は `_draw_rects`
の gray_patch 引数のコメント）。実 API は呼ばない。
"""
from __future__ import annotations

import json
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr import align, format_check
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import (
    ANCHOR_MIN_SPAN_PX,
    ANCHOR_SEARCH_PX,
    ANCHOR_WARN_MIN,
    TemplateError,
    load_template,
)

TPL = app_root() / "templates" / "chouhyo-v1.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
FORMB_TPL = app_root() / "testdata" / "formB" / "formB-v1.json"
FORMB_PNG = app_root() / "testdata" / "formB" / "formB-1.png"

needs_real_data = pytest.mark.skipif(
    not (TPL.exists() and PAGE_PNG.exists()),
    reason="出荷テンプレート・展開画像が無い環境")
needs_formb = pytest.mark.skipif(
    not (FORMB_TPL.exists() and FORMB_PNG.exists()), reason="formB の素材が無い環境")


# ---------------------------------------------------------------------------
# 合成テンプレート・合成画像のヘルパー
# ---------------------------------------------------------------------------

W, H = 800, 1200
RW, RH = 200, 60
# 非周期に散らした 6 欄。等間隔に積むと 1 ピッチずれ解と拮抗する（R-2・
# test_t5b_* を参照）ので、基本動作の確認には使わない
RECTS = [(100, 100), (100, 220), (100, 400), (450, 100), (450, 300), (450, 600)]


def _tpl_obj(rects, rw=RW, rh=RH, dpi=300, extra=None, image=(W, H)):
    """欄だけ（tables を持たない）のテンプレート JSON を組み立てる。"""
    fields = []
    for i, (x, y) in enumerate(rects):
        fld = {"field_id": f"f{i}", "kind": "text",
               "rect": {"x": x, "y": y, "w": rw, "h": rh}}
        if extra and i in extra:
            fld.update(extra[i])
        fields.append(fld)
    return {
        "schema_version": 1, "template_id": "fieldonly", "render_dpi": dpi,
        "image": {"width": image[0], "height": image[1]}, "record": {"pages": 1},
        "faces": [{
            "face_id": "front",
            "source": {"page_offset": 0,
                       "rect": {"x": 0, "y": 0, "w": image[0], "h": image[1]}},
            "fields": fields,
        }],
    }


def _load(tmp_path, obj, name="t.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return load_template(p)


def _draw_rects(rects, rw=RW, rh=RH, dx=0, dy=0, size=(W, H), gray_patch=False):
    """欄の枠（4 辺・幅 1px）だけを描いたグレースケール画像。

    辺は期待座標にちょうど 1px で置く——太らせると `line_positions` が返す
    帯の中心が期待位置から半画素ずれ、シフト推定の値が読みにくくなる。

    gray_patch: 中間調のパッチを隅に 1 つ置く。`align._otsu` は輝度が 2 値
    しかない画像で閾値 0 を返し、呼び出し側の `gray < th` でインクが全消え
    する（`align._otsu` の docstring に実測つきの説明がある）。`align_page`
    をフルパスで通す試験だけがこの経路に触るので、そこでだけ使う。どの欄の
    検出窓にも入らない位置に置くため、検出線には影響しない。
    """
    img = Image.new("L", size, 255)
    d = ImageDraw.Draw(img)
    if gray_patch:
        d.rectangle((10, size[1] - 100, 60, size[1] - 50), fill=128)
    for (x, y) in rects:
        X, Y = x + dx, y + dy
        d.line((X, Y, X + rw, Y), fill=0, width=1)
        d.line((X, Y + rh, X + rw, Y + rh), fill=0, width=1)
        d.line((X, Y, X, Y + rh), fill=0, width=1)
        d.line((X + rw, Y, X + rw, Y + rh), fill=0, width=1)
    return img


def _binary(rects, **kw):
    return np.asarray(_draw_rects(rects, **kw)) < 128


# ---------------------------------------------------------------------------
# T-1 / T-2 / T-3 / T-6: 表のある面は欄アンカーの経路に触れない（不変条件 A）
# ---------------------------------------------------------------------------

def _assert_table_faces_have_no_field_geoms(template):
    for face in template.faces:
        assert face.table_geoms, f"face {face.face_id} に table_geoms が無い"
        assert face.field_geoms == (), (
            f"不変条件 A 違反: face {face.face_id} が table_geoms と field_geoms を"
            "両方持っている。表のある面は従来経路だけを通らねばならない")


@needs_real_data
def test_t1_shipped_template_faces_have_no_field_anchors():
    _assert_table_faces_have_no_field_geoms(load_template(TPL))


@needs_formb
def test_t1_formb_faces_have_no_field_anchors():
    _assert_table_faces_have_no_field_geoms(load_template(FORMB_TPL))


@needs_formb
def test_t2_table_face_never_enters_field_anchor_path(monkeypatch):
    """表のある面の位置合わせが欄アンカーの収集関数を 1 度も呼ばないこと。

    残差・dx/dy の数値を突き合わせる T-3 と違い、こちらは経路そのものを塞ぐ
    ——表のある面のコードが変わっていないことの直接の証拠になる。
    """
    def _boom(*a, **kw):
        raise AssertionError("表のある面で _collect_field_anchor_lines が呼ばれた")

    monkeypatch.setattr(align, "_collect_field_anchor_lines", _boom)
    template = load_template(FORMB_TPL)
    faces, _composite = align.align_page(Image.open(FORMB_PNG), template)
    assert len(faces) == len(template.faces)


@needs_real_data
def test_t3_shipped_template_estimate_is_unchanged():
    """出荷テンプレート × sample-1 の推定値を f935a79 時点の直値で固定する。

    証跡（PM 受入基準「表ありの既存テンプレートの残差が f935a79 時点と同一」）:
    - コマンド: `align._face_estimate` を front/back に実行して全フィールドを
      JSON 化するスクリプトを、変更前（f935a79 の内容）と変更後の両方で実行し
      `diff` を取った
    - 出力の要点: 差分なし。値は下の expected と完全一致
    - 実行日: 2026-09-03
    """
    expected = {
        "front": dict(dx=0, dy=0, matched=17, total=22, ok=True, reason="",
                      det_h=27, det_v=9, exp_h=6, exp_v=10, matched_uniq=11,
                      bh=False, bv=False, shift_limits=(23, 54),
                      h=(0, 1, 6, 0), v=(0, 18, 9, 1),
                      blocks=((0, 0, 1, 6, 0), (1, -2, 2, 6, 0))),
        "back": dict(dx=0, dy=0, matched=38, total=42, ok=True, reason="",
                     det_h=24, det_v=10, exp_h=15, exp_v=11, matched_uniq=22,
                     bh=False, bv=False, shift_limits=(50, 50),
                     h=(0, 1, 15, 0), v=(0, 11, 10, 1),
                     blocks=((0, 1, 2, 15, 0), (1, 0, 1, 15, 0))),
    }
    template = load_template(TPL)
    tw, th = template.image_size
    page = Image.open(PAGE_PNG).convert("RGB").resize((tw, th))
    pad = max((max(f.shift_limits) for f in template.faces), default=0)
    padded = Image.new("RGB", (tw + 2 * pad, th + 2 * pad), "white")
    padded.paste(page, (pad, pad))

    for face in template.faces:
        exp = expected[face.face_id]
        est, _big, angle = align._face_estimate(padded, face, template, pad)
        assert angle == 0.0
        assert face.shift_limits == exp["shift_limits"]
        got = dict(dx=est.dx, dy=est.dy, matched=est.matched, total=est.total,
                   ok=est.ok, reason=est.reason, det_h=est.det_h_count,
                   det_v=est.det_v_count, exp_h=est.exp_h_uniq,
                   exp_v=est.exp_v_uniq, matched_uniq=est.matched_uniq,
                   bh=est.at_boundary_h, bv=est.at_boundary_v)
        assert got == {k: exp[k] for k in got}, face.face_id
        r = est.residual
        assert (r.h.med, r.h.max, r.h.pairs, r.h.unpaired) == exp["h"]
        assert (r.v.med, r.v.max, r.v.pairs, r.v.unpaired) == exp["v"]
        assert tuple((b.block_idx, b.med, b.max, b.pairs, b.unpaired)
                     for b in r.blocks) == exp["blocks"]


def test_t6_algo_version_not_bumped():
    """欄アンカーの追加で ALGO_VERSION を上げていないこと（決定 6）。

    上げると既存の中間データが全て再利用を拒否され、run のやり直し＝API 再送信
    ＝再課金になる。今回挙動が変わりうるのは「表を持たない面」だけで、そんな
    テンプレートはこれまで load_template が拒否していた＝中間データとして存在
    し得ないため、旧データを新方式で誤って再利用する経路が無い。
    """
    assert align.ALGO_VERSION == "2"


# ---------------------------------------------------------------------------
# T-4 / T-5: 欄アンカーでの位置合わせ（基本動作・ずれの復元）
# ---------------------------------------------------------------------------

def test_t4_field_only_face_aligns_without_shift(tmp_path):
    template = _load(tmp_path, _tpl_obj(RECTS))
    face = template.faces[0]
    assert face.table_geoms == ()
    assert len(face.field_geoms) == len(RECTS)
    est = align.estimate_shift(_binary(RECTS), face, dpi=template.render_dpi)
    assert est.ok, est.reason
    assert (est.dx, est.dy) == (0, 0)
    assert est.matched == est.total == 24   # 6 欄 × 4 辺
    assert format_check.classify(est) == ("match", "")


@pytest.mark.parametrize("dx,dy", [(3, 3), (8, -8), (15, 15), (-20, 10), (-18, 18)])
def test_t5_field_only_face_recovers_shift(tmp_path, dx, dy):
    """ずれた入力から平行移動量を復元する（issue #86 の受入目安）。

    `_axis_shift` のスコアは期待線の ±1px を一致とみなし、同点は小さいシフトを
    優先する（既存仕様・本 issue で変更していない）。そのため復元値は入力の
    ずれから最大 1px 内側に寄る。ここは ±1 で固定する。
    """
    template = _load(tmp_path, _tpl_obj(RECTS))
    face = template.faces[0]
    est = align.estimate_shift(_binary(RECTS, dx=dx, dy=dy), face,
                               dpi=template.render_dpi)
    assert est.ok, est.reason
    assert abs(est.dx - dx) <= 1, f"dx: 入力 {dx} に対し推定 {est.dx}"
    assert abs(est.dy - dy) <= 1, f"dy: 入力 {dy} に対し推定 {est.dy}"
    assert est.matched == est.total == 24


@pytest.mark.parametrize("pitch", [80, 120, 200])
def test_t5b_equally_stacked_fields_reject_one_pitch_shift(tmp_path, pitch):
    """同寸の欄を等間隔に縦積みし、1 ピッチずらした入力を拒否できること（R-2）。

    歯止めは面内の期待線の最外 2 本を照合する外形検査（決定 7')。1 ピッチずれ解は
    探索上限（ANCHOR_SEARCH_PX=24px）の外にあるので次点として現れず、次点差で見る
    ambiguous は原理的に発火しない——だから一致率でも探索範囲でもなく、外形で捉える。

    reason まで踏み込んで固定する: ok=False だけだと few_lines で落ちても通って
    しまい、意図した経路と違うところで塞がっていても気づけない。

    足切りは軸ごとに辺の長さで効く（横線は幅・縦線は高さ・template.py の
    `_field_geoms`）。ここは幅 200・高さ 60 の欄なので両軸ともアンカーになる。
    """
    stack = [(200, 100 + pitch * i) for i in range(6)]
    template = _load(tmp_path, _tpl_obj(stack))
    face = template.faces[0]
    est = align.estimate_shift(_binary(stack, dy=pitch), face,
                               dpi=template.render_dpi)
    assert not est.ok, (
        f"1 ピッチ（{pitch}px）ずれた入力を ok=True で通した"
        f"（dy={est.dy} matched={est.matched}/{est.total}）")
    assert est.reason == "edge_mismatch", est.reason


def test_t5c_equally_stacked_fields_pass_without_shift(tmp_path):
    """縦積みの正常系（ずれ 0）は落ちない。

    最外照合（決定 7')が正常系を巻き込んでいないことの基準。1 ピッチずれを
    塞ぐ変更を入れる前にこの緑を確認してある——後から落ちたなら、それは穴を
    塞いだのではなく過剰拒否を作ったということ。
    """
    stack = [(200, 100 + 120 * i) for i in range(6)]
    template = _load(tmp_path, _tpl_obj(stack))
    est = align.estimate_shift(_binary(stack), template.faces[0],
                               dpi=template.render_dpi)
    assert est.ok, est.reason
    assert (est.dx, est.dy) == (0, 0)
    assert est.matched == est.total == 24


@pytest.mark.parametrize("dy", [3, 15, -15])
def test_t5d_equally_stacked_fields_recover_in_range_shift(tmp_path, dy):
    """探索範囲（ANCHOR_SEARCH_PX=24px）内のずれは、縦積みでも補正できる。

    最外照合は「補正しきれなかったずれ」だけを捉える。補正できたずれまで
    拒否していないことを固定する。
    """
    stack = [(200, 200 + 120 * i) for i in range(6)]
    template = _load(tmp_path, _tpl_obj(stack))
    est = align.estimate_shift(_binary(stack, dy=dy), template.faces[0],
                               dpi=template.render_dpi)
    assert est.ok, est.reason
    assert abs(est.dy - dy) <= 1, f"dy: 入力 {dy} に対し推定 {est.dy}"
    assert est.matched == est.total == 24


def test_t5e_missing_outermost_edge_fails_visibly(tmp_path):
    """最外の期待線が紙に無いテンプレートは、毎回 edge_mismatch で落ちる。

    外形照合の限界を「仕様」として固定する。最上段の欄の上辺が印刷されていない
    （下線だけの欄など）と `min(exp_h_set)` は永久に当たらず、ずれの無い正しい紙
    でも位置合わせに失敗する。方向は安全側——**無言の誤りではなく可視の失敗**に
    なることの証拠であって、この経路を将来ゆるめるなら意図的にここを踏む。

    利用者への案内は W-5 の警告文（template.py の `_anchor_shortage_warnings`）。
    """
    stack = [(200, 100 + 120 * i) for i in range(6)]
    template = _load(tmp_path, _tpl_obj(stack))
    binary = _binary(stack)
    binary[98:103, :] = False              # 最上段の欄の上辺（y=100）だけを消す
    est = align.estimate_shift(binary, template.faces[0], dpi=template.render_dpi)
    assert not est.ok
    assert est.reason == "edge_mismatch", est.reason


def test_t5f_equally_spaced_columns_reject_one_pitch_shift(tmp_path):
    """x 方向: 横に等間隔で並ぶ同寸の欄で、1 ピッチずれた入力を拒否できること。

    日付・金額の 1 文字ずつの記入枠のように、横並びにも周期構造は実在する。表の
    経路が縦線に外形照合を掛けないのは「列間隔は非周期」という前提に立つためで、
    欄アンカーではその前提が成り立たない（決定 7' の v 軸判断）。

    このテストは v 軸の照合を外すと落ちる。※要較正で v 軸を緩める判断をする日が
    来たら、必ずここで気づく。
    """
    pitch = 100
    row = [(150 + pitch * i, 300) for i in range(6)]
    template = _load(tmp_path, _tpl_obj(row, rw=60))
    est = align.estimate_shift(_binary(row, rw=60, dx=pitch), template.faces[0],
                               dpi=template.render_dpi)
    assert not est.ok, (
        f"x 方向に 1 ピッチ（{pitch}px）ずれた入力を ok=True で通した"
        f"（dx={est.dx} matched={est.matched}/{est.total}）")
    assert est.reason == "edge_mismatch", est.reason


# ---------------------------------------------------------------------------
# T-7 / T-8 / T-9 / T-13: load_template の受け入れ範囲（決定 8・決定 5）
# ---------------------------------------------------------------------------

def test_t7_face_without_any_anchor_is_rejected(tmp_path):
    """表も無く、アンカーに使える欄も無い面は読み込み時に拒否する。"""
    small = [(100, 100), (300, 100), (100, 300)]
    with pytest.raises(TemplateError) as ei:
        _load(tmp_path, _tpl_obj(small, rw=20, rh=20))
    msg = str(ei.value)
    assert "front" in msg                    # どの面かが分かる
    assert str(ANCHOR_MIN_SPAN_PX) in msg    # どうすれば直るかが分かる
    assert "tables" in msg


def test_t7_horizontal_only_anchors_are_rejected(tmp_path):
    """横線しか作れない面（縦に短い欄だけ）も拒否する。

    期待縦線が 0 本だと need_x = max(2, 0) = 2 に対し一致 0 本となり、その面は
    毎ページ必ず few_lines で失敗する。読める見込みが無い設定は受理しない。
    """
    wide_flat = [(100, 100), (100, 300), (100, 500)]
    with pytest.raises(TemplateError, match="アンカーが無い"):
        _load(tmp_path, _tpl_obj(wide_flat, rw=400, rh=20))


def test_t8_few_anchors_load_with_w5_warning(tmp_path):
    template = _load(tmp_path, _tpl_obj(RECTS[:2]))
    assert len(template.faces[0].field_geoms) == 2
    w5 = [w for w in template.warnings if w.startswith("[W-5]")]
    assert len(w5) == 1
    assert "front" in w5[0] and "2 個" in w5[0]


def test_t9_enough_anchors_load_without_warning(tmp_path):
    template = _load(tmp_path, _tpl_obj(RECTS))
    assert len(RECTS) >= ANCHOR_WARN_MIN
    assert [w for w in template.warnings if w.startswith("[W-5]")] == []
    assert template.faces[0].shift_limits == (ANCHOR_SEARCH_PX, ANCHOR_SEARCH_PX)


def test_t9_search_limits_scale_with_render_dpi(tmp_path):
    """探索上限は固定 px だが BASE_DPI 比でスケールする（汎用化 A-3）。"""
    big = [(x * 2, y * 2) for x, y in RECTS]
    template = _load(tmp_path, _tpl_obj(big, rw=RW * 2, rh=RH * 2, dpi=600,
                                        image=(W * 2, H * 2)))
    assert template.faces[0].shift_limits == (ANCHOR_SEARCH_PX * 2,
                                              ANCHOR_SEARCH_PX * 2)
    assert len(template.faces[0].field_geoms) == len(big)


def test_t9_min_span_scales_with_render_dpi(tmp_path):
    """300dpi では適格な寸法の欄が、600dpi では足切りされる（同じ px 値なので）。"""
    rects = [(100, 100), (400, 100), (100, 400)]
    at300 = _load(tmp_path, _tpl_obj(rects, rw=50, rh=50), "a.json")
    assert len(at300.faces[0].field_geoms) == 3
    with pytest.raises(TemplateError, match="アンカーが無い"):
        _load(tmp_path, _tpl_obj(rects, rw=50, rh=50, dpi=600,
                                 image=(W * 2, H * 2)), "b.json")


def test_t13_extra_and_fallback_rects_are_not_anchors(tmp_path):
    """追加の領域・参照先は期待線に足さない（決定 8 の母集団）。

    これらは「複数の矩形の合併で 1 つの欄」を作る受け皿で、個々の矩形の辺が
    紙に印刷されている保証が無い。足すと一致率の分母だけが増える。
    """
    extra = {0: {"extra_rects": [{"x": 100, "y": 700, "w": RW, "h": RH}],
                 "fallback_rect": {"x": 450, "y": 900, "w": RW, "h": RH}}}
    template = _load(tmp_path, _tpl_obj(RECTS, extra=extra))
    face = template.faces[0]
    assert len(face.field_geoms) == len(RECTS)   # 6 欄ぶんだけ（8 ではない）
    assert {(g.x_min, g.y_min) for g in face.field_geoms} == set(RECTS)


def test_output_false_fields_still_count_as_anchors(tmp_path):
    """出力対象外（output: false）の欄も幾何には残る（FR-1.2）。"""
    template = _load(tmp_path, _tpl_obj(
        RECTS, extra={i: {"output": False} for i in range(len(RECTS))}))
    assert len(template.faces[0].field_geoms) == len(RECTS)


# ---------------------------------------------------------------------------
# T-10 / T-11: 様式判定の倒れ方（決定 3 の代償を固定する）
# ---------------------------------------------------------------------------

def test_t10_blank_page_falls_to_undecidable(tmp_path):
    """白紙は「様式不一致」ではなく「判定不能（few_lines）」へ倒れる。

    期待位置の窓だけを見るので、材料不足のときに det が期待線と食い違う形で
    埋まらない——正しい紙を「線はあるのに合わない」と誤って不一致にしない。
    """
    template = _load(tmp_path, _tpl_obj(RECTS))
    est = align.estimate_shift(np.zeros((H, W), dtype=bool), template.faces[0],
                               dpi=template.render_dpi)
    assert not est.ok
    assert format_check.classify(est) == ("undecidable", "few_lines")


def test_t11_different_layout_does_not_match(tmp_path):
    """別配置の紙は少なくとも「一致」にはならない。

    実測（2026-09-03）: verdict は ('undecidable', 'few_lines')。決定 3 の代償の
    とおり `mismatch` には倒れない——欄だけの面は (t) の照合で「積極的に排除する」
    力を持たない（R-5・#71 へ記録）。最外の外形照合（決定 7'）を入れた後も同値
    ——材料不足の few_lines が先に返るため外形照合には到達しない（同日再測）。
    """
    template = _load(tmp_path, _tpl_obj(RECTS))
    other = [(x + 137, y + 271) for x, y in RECTS]   # 探索範囲(24px)の外へ動かす
    est = align.estimate_shift(_binary(other), template.faces[0],
                               dpi=template.render_dpi)
    verdict, reason = format_check.classify(est)
    assert verdict != "match"
    assert (verdict, reason) == ("undecidable", "few_lines")


# ---------------------------------------------------------------------------
# T-12: align_page のフルパス（pad・crop 込み）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("dx,dy", [(0, 0), (10, -12), (-15, 15), (20, 20)])
def test_t12_align_page_full_path(tmp_path, dx, dy):
    template = _load(tmp_path, _tpl_obj(RECTS))
    img = _draw_rects(RECTS, dx=dx, dy=dy, gray_patch=True).convert("RGB")
    faces, composite = align.align_page(img, template)
    assert composite.size == (W, H)
    assert len(faces) == 1
    assert abs(faces[0].dx - dx) <= 1
    assert abs(faces[0].dy - dy) <= 1


# ---------------------------------------------------------------------------
# R-4: 欄が多い面での check_page の実測（NFR-F09 の予算 3.0 秒）
# ---------------------------------------------------------------------------

def test_r4_many_fields_check_page_is_cheap(tmp_path):
    """欄 100 個の面で `format_check.check_page` が予算を圧迫しないこと。

    決定 3 の窓方式（期待位置の周辺だけを走査）を採った理由の裏取り。上限は
    環境差を見込んで緩く置いてある——性能の回帰検知が目的で、値の固定ではない。

    実測（2026-09-03・Windows 11 / .venv Python 3.13・前処理込みで 5 回計測）:
    最外照合を入れる前 334/351/357/373/397 ms、入れた後 321/310/370/370/397 ms
    （同日再測）。外形照合は min/max と集合参照だけで画像を触らないので、差は
    測定のばらつきの範囲。NFR-F09 の予算は候補テンプレート合計で 3.0 秒なので、
    欄だけの候補が数件混ざっても収まる。
    """
    pw, ph = 2490, 3510
    rects = [(150 + (i % 4) * 560, 120 + (i // 4) * 130) for i in range(100)]
    template = _load(tmp_path, _tpl_obj(rects, rw=420, rh=70, image=(pw, ph)))
    assert len(template.faces[0].field_geoms) == 100
    img = _draw_rects(rects, rw=420, rh=70, size=(pw, ph),
                      gray_patch=True).convert("RGB")

    format_check.check_page(img, template)          # ウォームアップ
    t0 = time.perf_counter()
    verdict = format_check.check_page(img, template)
    elapsed = time.perf_counter() - t0
    assert verdict.verdict == "match", (verdict.verdict, verdict.reason)
    assert elapsed < 3.0, f"欄 100 個の check_page が {elapsed:.2f} 秒かかった"
