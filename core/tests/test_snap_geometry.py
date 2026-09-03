"""ブロック単位の枠吸着 — 幾何と純関数（issue #75 (f)・Unit A・08 §6）。

対象 AC: AC-F32（許容幅超は戻る）・AC-F33（許容幅の導出と x の対象外）・
AC-F35（信用する下限と面単位の除外）・AC-F64（保存時の警告）。
加えて 08 §6 の判断1〜4 が求める固定（T-B1〜B3・T-S1/S2・T-F1〜F9・T-P3）。

このファイルは**画像を1枚も開かない**。合成罫線画像を要る符号の検証だけ
`test_align_residual.py` と同じ `_two_block_face` 構成を借りる。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr import snap
from chouhyo_ocr.align import BlockShift, ShiftEstimate, estimate_shift
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.snap import (BLOCK_FEW_LINES, BLOCK_OVER_TOLERANCE,
                              BLOCK_SMALL_TABLE, REASON_DISABLED,
                              REASON_EXCLUDED, REASON_FAILSAFE,
                              REASON_NO_BLOCKS, REASON_NO_ESTIMATE,
                              REASON_OVERLAP, X_AXIS_NOT_SUPPORTED, BlockSnap,
                              FaceSnap, apply_snap, is_small_table, need_y,
                              plan_face_snap, reject_overlapping, tolerance_x,
                              tolerance_y)
from chouhyo_ocr.template import (CellSpec, Face, Rect, TableGeom, TableZone,
                                  Template, load_template)
from helpers_geom import shift_block_y

TPL = app_root() / "templates" / "chouhyo-v1.json"
FORMB = app_root() / "testdata" / "formB" / "formB-v1.json"


# ---------------------------------------------------------------------------
# 判断1: block_idx の採番（T-B1〜B3）— 5経路の座標が揃うことの土台
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", [TPL, FORMB])
def test_t_b1_block_idx_matches_table_geoms_order(path):
    """表由来 cell の block_idx 集合 == range(len(face.table_geoms))、
    table_zones[i].block_idx == i、単発欄は None。

    **これが「transform["snap"]["dy"] の添字＝cell の block_idx」の唯一の
    担保**（08 §6 判断1）。崩れると5経路すべての座標が静かにずれるので、
    他のどのテストより先に置く。
    """
    template = load_template(path)
    for face in template.faces:
        table_cells = [c for c in template.cells
                       if c.face_id == face.face_id and c.table_id is not None]
        assert {c.block_idx for c in table_cells} == set(range(len(face.table_geoms)))
        assert [z.block_idx for z in face.table_zones] == list(
            range(len(face.table_zones)))
        assert all(c.block_idx is None for c in template.cells
                   if c.face_id == face.face_id and c.table_id is None)


def test_t_b2_block_idx_is_continuous_across_multiple_tables(tmp_path):
    """同じ面に table が2つあると block_idx は 0,1,2,3 と通しになる
    （table ごとに 0 へ戻らない）。現存テンプレートは面あたり table 1個なので
    この経路は合成でしか通らない。"""
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    front = raw["faces"][0]
    t0 = front["tables"][0]
    t1 = json.loads(json.dumps(t0))
    t1["table_id"] = "family2"
    # 2つ目の表は1つ目の下（重ならない位置・面の高さ 1880 に収まる）へ置く
    for blk in t1["blocks"]:
        blk["origin"]["y"] = 1500
        blk["rows"] = 2
    t1["blocks"] = t1["blocks"][:2]
    front["tables"] = [t0, t1]
    front["fields"] = [f for f in front["fields"] if f["field_id"] != "person_備考"]
    p = tmp_path / "two_tables.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    template = load_template(p)
    face = template.face("front")
    assert len(face.table_geoms) == 4
    by_table: dict[str, set] = {}
    for c in template.cells:
        if c.table_id is None or c.face_id != "front":
            continue
        by_table.setdefault(c.table_id, set()).add(c.block_idx)
    assert by_table["family"] == {0, 1}
    assert by_table["family2"] == {2, 3}


def test_t_b3_positional_cellspec_still_works():
    """位置引数で組んだ CellSpec は block_idx が None（末尾・既定値つきの担保）。"""
    c = CellSpec("f", "front", Rect(0, 0, 10, 10), "text")
    assert c.block_idx is None
    assert TableZone("t", 0, 10, 20).block_idx == 0
    assert TableGeom(0, 10, 0, 20, (0, 10, 20), (0, 10)).row_gap == 0


# ---------------------------------------------------------------------------
# 判断4: 許容幅・下限・境界（T-F1〜F5・AC-F32/F33/F35）
# ---------------------------------------------------------------------------

def test_t_f1_tolerance_y_from_real_templates_and_x_is_not_supported():
    """AC-F33: y は family 8／detail 4／formB visit 10。x は「対象外」が返り、
    許容幅 0 による偶然の no-op と区別できる（型が違う）。"""
    front = load_template(TPL).face("front")
    back = load_template(TPL).face("back")
    visit = load_template(FORMB).faces[0]
    assert {tolerance_y(g) for g in front.table_geoms} == {8}
    assert {tolerance_y(g) for g in back.table_geoms} == {4}
    assert {tolerance_y(g) for g in visit.table_geoms} == {10}

    assert tolerance_x() == X_AXIS_NOT_SUPPORTED
    assert tolerance_x() != 0          # 値としても 0 と等しくない
    assert not isinstance(tolerance_x(), int)   # 型としても数値ではない


def test_t_f5_need_y_never_sits_on_the_floor_above_the_exclusion():
    """4-A/4-B: 除外（期待横線4本以下）を通った表では下限 2 が効くことは無い。"""
    assert need_y(4) == 2          # 下限 2 と拮抗（＝除外される側）
    for n in range(5, 40):
        assert need_y(n) > 2


def _geom(h_lines: tuple[int, ...], row_gap: int) -> TableGeom:
    return TableGeom(x_min=0, x_max=100, y_min=h_lines[0], y_max=h_lines[-1],
                     h_lines=h_lines, v_lines=(0, 50, 100), row_gap=row_gap)


def _face(geoms: tuple[TableGeom, ...]) -> Face:
    return Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 200, 900),
                table_geoms=geoms, shift_limits=(10, 50))


def _est(shifts: tuple[BlockShift, ...]) -> ShiftEstimate:
    return ShiftEstimate(dx=0, dy=0, matched=99, total=99, ok=True, reason="",
                         block_shifts=shifts)


@pytest.mark.parametrize("allow,dy,expect_applied", [
    (4, 4, True), (4, 5, False),      # detail の行間隙
    (8, 8, True), (8, 9, False),      # family の行間隙
])
def test_t_f2_tolerance_boundary_is_inclusive(allow, dy, expect_applied):
    """4-C: 許容幅**ちょうど**は通す。拒否は `abs(dy) > allow` から。

    全行を +allow 動かすと行 i の下端は次の行の元の上端にちょうど接するだけで
    重ならない（半開区間）。`>=` で弾くと救える帯（detail で 2〜4px）を 1px
    削る。**`>=` で書いた実装はこの単体テストでしか落ちない。**
    """
    h = tuple(60 * i for i in range(10))
    face = _face((_geom(h, allow),))
    fs = plan_face_snap(face, _est((BlockShift(0, dy, 10, 10),)), enabled=True)
    assert fs.applied is expect_applied
    if not expect_applied:
        assert fs.reason == REASON_FAILSAFE
        assert fs.blocks[0].reason == BLOCK_OVER_TOLERANCE
    assert fs.blocks[0].measured_dy == dy
    assert fs.blocks[0].allow == allow


def test_t_f3_over_tolerance_returns_to_template_coordinates():
    """AC-F32: detail（許容幅 4px）に y 10px の吸着要求 → 吸着せず戻る。"""
    h = tuple(104 * i for i in range(15))
    face = _face((_geom(h, 4),))
    fs = plan_face_snap(face, _est((BlockShift(0, 10, 15, 15),)), enabled=True)
    assert (fs.applied, fs.reason) == (False, REASON_FAILSAFE)
    assert fs.blocks[0].reason == BLOCK_OVER_TOLERANCE
    assert fs.dy_by_block() == (0,)       # 適用した量は 0（測った量は 10）
    assert fs.blocks[0].measured_dy == 10


@pytest.mark.parametrize("rows,excluded", [(3, True), (4, False)])
def test_t_f4_small_table_excludes_the_whole_face(rows, excluded):
    """AC-F35: 期待横線 4 本（3行表）は対象外。5 本（4行表）は試みる。
    除外は**面全体**へ効き、健全な隣のブロックも吸着しない。"""
    small = _geom(tuple(60 * i for i in range(rows + 1)), 4)
    healthy = _geom(tuple(60 * i for i in range(16)), 4)
    face = _face((small, healthy))
    est = _est((BlockShift(0, 1, rows + 1, rows + 1), BlockShift(1, 1, 15, 16)))
    fs = plan_face_snap(face, est, enabled=True)
    assert is_small_table(small) is excluded
    if excluded:
        assert (fs.applied, fs.reason) == (False, REASON_EXCLUDED)
        assert fs.blocks[0].reason == BLOCK_SMALL_TABLE
        assert fs.blocks[1].reason == ""      # 隣は健全だが面ごと戻る
        assert fs.dy_by_block() == (0, 0)
    else:
        assert fs.applied is True


def test_few_lines_puts_the_whole_face_into_failsafe():
    """FR-F36: 一致本数 < need_y のブロックがあれば面全体が fail-safe。"""
    g = _geom(tuple(60 * i for i in range(16)), 4)
    face = _face((g, g))
    est = _est((BlockShift(0, 1, 16, 16), BlockShift(1, 1, 3, 16)))
    fs = plan_face_snap(face, est, enabled=True)
    assert (fs.applied, fs.reason) == (False, REASON_FAILSAFE)
    assert fs.blocks[1].reason == BLOCK_FEW_LINES
    assert fs.blocks[1].need == need_y(16) == 8


def test_disabled_returns_immediately_without_touching_geometry():
    """4-G 手順1: OFF は最初の分岐で戻る（1バイトも計算しない・AC-F45 の構造担保）。"""
    face = _face((_geom(tuple(60 * i for i in range(16)), 4),))
    fs = plan_face_snap(face, _est((BlockShift(0, 99, 16, 16),)), enabled=False)
    assert (fs.applied, fs.reason, fs.blocks) == (False, REASON_DISABLED, ())


@pytest.mark.parametrize("est", [
    None,
    ShiftEstimate(0, 0, 0, 0, False, "few_lines"),     # 位置合わせ失敗
    ShiftEstimate(0, 0, 9, 9, True, "", block_shifts=()),   # 測定なし
])
def test_no_estimate_is_distinct_from_failsafe(est):
    """4-G 手順2: 測定が無い面は `no_estimate`。fail-safe とは別の理由コード
    ——「吸着を試みて戻した」と「試みてすらいない」を同じ語で言わない。"""
    face = _face((_geom(tuple(60 * i for i in range(16)), 4),))
    fs = plan_face_snap(face, est, enabled=True)
    assert (fs.applied, fs.reason) == (False, REASON_NO_ESTIMATE)


def test_t_f9_face_without_tables_is_no_blocks(tmp_path):
    """判断5-B: 表を持たない面（issue #86）は `no_blocks`。位置合わせ失敗と
    同じ理由コードにすると診断で区別できない。"""
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 100, 100),
                table_geoms=(), field_geoms=(_geom((0, 50), 0),),
                shift_limits=(24, 24))
    fs = plan_face_snap(face, _est((BlockShift(0, 1, 2, 2),)), enabled=True)
    assert (fs.applied, fs.reason) == (False, REASON_NO_BLOCKS)


# ---------------------------------------------------------------------------
# 判断4-D: 符号（T-F6）— ここを間違えると OFF では絶対に気づけない
# ---------------------------------------------------------------------------

_ROW_PITCH = 60
_COL_WIDTH = 80
_COLS = 4
_OY = 60
_SHARED_H = [_OY + _ROW_PITCH * i for i in range(15)]
_EXTRA0_H = [_SHARED_H[-1] + _ROW_PITCH * i for i in range(1, 6)]


def _make_geom(ox: int, h_offsets: list) -> TableGeom:
    return TableGeom(x_min=ox, x_max=ox + _COL_WIDTH * _COLS,
                     y_min=min(h_offsets), y_max=max(h_offsets),
                     h_lines=tuple(sorted(h_offsets)),
                     v_lines=tuple(ox + _COL_WIDTH * i for i in range(_COLS + 1)),
                     row_gap=8)


def _two_block_face() -> tuple[Face, tuple]:
    """test_align_residual.py と同じ非対称2ブロック構成（08 §5.10 R-1）。"""
    g0 = _make_geom(50, _SHARED_H + _EXTRA0_H)
    g1 = _make_geom(50 + _COL_WIDTH * _COLS, _SHARED_H)
    size = (50 + _COL_WIDTH * _COLS * 2 + 50, max(g0.y_max, g1.y_max) + 100)
    face = Face(face_id="test", page_offset=0, source_rect=Rect(0, 0, *size),
                table_geoms=(g0, g1),
                shift_limits=(_COL_WIDTH // 2 - 2, _ROW_PITCH // 2 - 2))
    return face, size


def _draw(size: tuple, geoms_with_offset: list) -> "np.ndarray":
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    for g, dy in geoms_with_offset:
        for y in g.h_lines:
            draw.line((g.x_min, y + dy, g.x_max, y + dy), fill=0, width=2)
        for x in g.v_lines:
            draw.line((x, g.y_min + dy, x, g.y_max + dy), fill=0, width=2)
    return np.asarray(img) < 128


def _page_and_template() -> tuple["Image.Image", Face, Template]:
    """`helpers_geom.shift_block_y` に渡せる合成ページとテンプレートを作る。

    L2 素材（sample-1）と**同じ刺激の与え方**にするための器——面の切り出し
    矩形がページ全体、表 't' が2ブロック（左は5行ぶん背が高く非対称）。
    """
    face, size = _two_block_face()
    g0, g1 = face.table_geoms
    img = Image.new("RGB", size, "white")
    dr = ImageDraw.Draw(img)
    for g in (g0, g1):
        for y in g.h_lines:
            dr.line((g.x_min, y, g.x_max, y), fill="black", width=2)
        for x in g.v_lines:
            dr.line((x, g.y_min, x, g.y_max), fill="black", width=2)
    zones = tuple(TableZone("t", g.x_min, g.x_max, g.y_max, block_idx=i)
                  for i, g in enumerate((g0, g1)))
    f2 = Face(face_id="front", page_offset=0, source_rect=Rect(0, 0, *size),
              table_zones=zones, table_geoms=(g0, g1),
              shift_limits=face.shift_limits)
    tpl = Template(template_id="t", render_dpi=300, image_size=size,
                   record_pages=1, faces=(f2,), cells=())
    return img, f2, tpl


@pytest.mark.parametrize("delta", [0, 2, 3, 4, 5])
def test_t_f6a_block_dy_is_the_residual_median_itself(delta):
    """恒等: `block_shifts[i].dy` は (c) の `residual.blocks[i].med` **そのもの**。

    2026-09-03 の architect 判断で、吸着量の推定量を `_axis_shift` の argmax から
    `_axis_residual` の符号付き中央値へ切り替えた（08 §6 判断4-D）。前者は
    ±1px の窓で数えるため真のずれ δ に対し `s ∈ {δ−1, δ, δ+1}` が同点になり、
    同点規則が絶対値の小さい側を採って**吸着量が系統的に 1px 過小**になっていた。

    ここは「2つの推定量を突き合わせる」テストではなく、**写しであることの固定**。
    (c) 側が正本で、(f) は同じ値を `BlockShift.dy` と `snap_detail.measured_dy`
    へ写す。`matched` だけは `_axis_shift` の score のままで、こちらは同点の
    どれを採っても本数が変わらないためバイアスを受けない。
    """
    face, size = _two_block_face()
    g0, g1 = face.table_geoms
    est = estimate_shift(_draw(size, [(g0, 0), (g1, delta)]), face, dpi=300)
    assert est.ok is True, est.reason
    assert [b.block_idx for b in est.block_shifts] == [0, 1]
    assert [b.dy for b in est.block_shifts] == [b.med for b in est.residual.blocks]
    # matched は _axis_shift の score（残差の pairs ではない——対応窓が
    # shift_limits と緩く、FR-F36 が要求する一致本数の厳しさと釣り合わない）
    assert [b.matched for b in est.block_shifts] == [20, 15]
    assert [b.expected for b in est.block_shifts] == [20, 15]


@pytest.mark.parametrize("delta", [-5, -4, -3, -2, 2, 3, 4, 5])
def test_t_f6b_block_dy_equals_the_applied_delta_exactly(delta):
    """符号と大きさ: `shift_block_y` で block1 だけを δ 動かすと、
    `block_shifts[1].dy` が **δ ちょうど**になる（block0 は 0 のまま）。

    刺激の与え方は L2 の AC-F30（sample-1 の back/detail block1）と同じ helper。
    **符号を逆に書いた実装はここで必ず落ちる**（吸着 OFF では絶対に気づけない
    故障）。直値が δ−sign(δ) だった時期は `_axis_shift` の ±1px 窓に由来する
    1px 過小で、推定量の切り替えで消えた（2026-09-03 実測・08 §6.7）。

    `matched` が期待線 15 本に対し 14 本になる δ があるのは helper の性質:
    `_block_rect` が返す矩形は半開区間 [y_min, y_max) なので、最下端の期待線
    （y_max）だけがブロックの移動に付いてこない。吸着量（中央値）には効かない。
    """
    img, face, tpl = _page_and_template()
    shifted = shift_block_y(img, tpl, "front", "t", 1, delta)
    binary = np.asarray(shifted.convert("L")) < 128
    est = estimate_shift(binary, face, dpi=300)
    assert est.ok is True, est.reason
    assert est.dy == 0                      # 面のシフトは動いていない側へ収束
    assert est.block_shifts[0].dy == 0
    assert est.block_shifts[1].dy == delta  # 1px 過小が無いこと
    assert est.block_shifts[1].matched >= 14


def test_block_shifts_absent_on_alignment_failure():
    """判定を通らなかった面には測定を作らない（08 §5.9-1 と同じ流儀）。"""
    face, size = _two_block_face()
    est = estimate_shift(np.zeros((size[1], size[0]), dtype=bool), face, dpi=300)
    assert est.ok is False
    assert est.block_shifts == ()


# ---------------------------------------------------------------------------
# 判断2: apply_snap が動かすもの／動かさないもの（T-S1・T-S2・T-P3）
# ---------------------------------------------------------------------------

def _all_applied(template: Template, dy: int) -> dict:
    return {f.face_id: FaceSnap(
        applied=True, reason="",
        blocks=tuple(BlockSnap(i, dy, 9, 3, 9, 8)
                     for i in range(len(f.table_geoms))))
        for f in template.faces}


def test_t_s1_apply_snap_moves_only_table_cells_in_y():
    """T-S1: 単発欄・除外領域・面の切り出し矩形は動かない。x は1つも動かない。

    x 不変はレビューの目視ではなくテストで固定する（FR-F34 の NG 事項）。
    """
    t = load_template(TPL)
    t2 = apply_snap(t, _all_applied(t, 3))
    before = {c.field_id: c for c in t.cells}
    for c2 in t2.cells:
        c = before[c2.field_id]
        # x は全矩形で不変
        assert [r.x for r in c2.all_rects()] == [r.x for r in c.all_rects()]
        assert [r.w for r in c2.all_rects()] == [r.w for r in c.all_rects()]
        assert [m.rect.x for m in c2.choice_marks] == [m.rect.x for m in c.choice_marks]
        if c.block_idx is None:
            assert c2 == c                       # 単発欄はそのまま（同値）
        else:
            assert c2.rect.y == c.rect.y + 3
            assert [m.rect.y for m in c2.choice_marks] == [
                m.rect.y + 3 for m in c.choice_marks]
    for f, f2 in zip(t.faces, t2.faces):
        assert f2.exclusions == f.exclusions
        assert f2.source_rect == f.source_rect
        assert f2.shift_limits == f.shift_limits
        assert f2.field_geoms == f.field_geoms
        assert [z2.bottom - z.bottom for z, z2 in zip(f.table_zones, f2.table_zones)] \
            == [3] * len(f.table_zones)
        for g, g2 in zip(f.table_geoms, f2.table_geoms):
            assert g2.h_lines == tuple(y + 3 for y in g.h_lines)
            assert (g2.x_min, g2.x_max, g2.v_lines) == (g.x_min, g.x_max, g.v_lines)


def test_t_s2_apply_snap_keeps_cell_order_and_ids():
    """T-S2: 並び順と field_id 列が入力と完全一致（列順が吸着で動かない）。"""
    t = load_template(TPL)
    t2 = apply_snap(t, _all_applied(t, 5))
    assert [c.field_id for c in t2.cells] == [c.field_id for c in t.cells]
    assert [c.output for c in t2.cells] == [c.output for c in t.cells]
    assert t2.image_size == t.image_size and t2.render_dpi == t.render_dpi


@pytest.mark.parametrize("snaps", [
    {},
    "all_false",
    "all_zero",
])
def test_t_p3_apply_snap_is_identity_when_nothing_applies(snaps):
    """T-P3: 未記録・全面 fail-safe・適用量ゼロでは**同一オブジェクト**を返す。

    AC-F45（OFF のバイト一致）をテストではなく設計で守るための性質
    ——複製すらしないので下流が1バイトも変わりようがない。
    """
    t = load_template(TPL)
    if snaps == "all_false":
        snaps = {f.face_id: FaceSnap(applied=False, reason=REASON_FAILSAFE,
                                     blocks=tuple(BlockSnap(i, 4, 9, 3, 9, 8)
                                                  for i in range(len(f.table_geoms))))
                 for f in t.faces}
    elif snaps == "all_zero":
        snaps = _all_applied(t, 0)
    assert apply_snap(t, snaps) is t


def test_apply_snap_ignores_records_with_wrong_block_count():
    """ブロック数が合わない記録は適用しない（remap は template_hash を照合
    しないため、表のブロックを増減したテンプレートで読む経路が実在する）。"""
    t = load_template(TPL)
    bogus = {f.face_id: FaceSnap(applied=True, reason="",
                                 blocks=(BlockSnap(0, 4, 9, 3, 9, 8),))
             for f in t.faces}
    assert apply_snap(t, bogus) is t


# ---------------------------------------------------------------------------
# 判断4-F: fail-safe の第3条件（吸着後の新しい重なり）— T-F7
# ---------------------------------------------------------------------------

def test_t_f7_front_family_snap_of_allowance_creates_overlap_with_person_biko():
    """AC-F36 第3条件（Go 条件①）: 出荷テンプレート chouhyo-v1 の front 面で
    family ブロックに dy=+8 を与えると面全体が戻り、`person_備考` と重ならない。

    実測（2026-09-03・`templates/chouhyo-v1.json` を読み、family の最終行下端と
    `person_備考` の rect を突き合わせ）:

        family block0/block1 最終行 y: 1359-1464（半開区間・origin.y=907・
            row_pitch=113・row_height=105・rows=5 → 907+113*4=1359, +105=1464）
        person_備考      y: 1471（x 400-2430・family block0 の x 389-1400 と
            x 帯が重なる）
        余白: 7px ／ 許容幅（row_pitch − row_height）: 8px
        → dy=+8 で最終行の下端が 1472 となり、person_備考 の上端 1471 と 1px 重なる

    重なると `mapping._bucket_cells` の first-hit が定義順で行き先を決めるため、
    **`status=正常` のまま値が入れ替わる**。許容幅の内側でも起こりうるので、
    「動かすか戻すか」の第3条件で塞ぐ（クランプはしない）。
    """
    t = load_template(TPL)
    front = t.face("front")
    last_bottom = max(c.rect.y + c.rect.h for c in t.cells
                      if c.face_id == "front" and c.table_id == "family")
    biko = next(c for c in t.cells if c.field_id == "person_備考")
    assert (last_bottom, biko.rect.y) == (1464, 1471)   # 余白 7px
    assert tolerance_y(front.table_geoms[0]) == 8       # 許容幅は余白より広い

    def _plan(dy: int) -> dict:
        return {"front": FaceSnap(applied=True, reason="",
                                  blocks=tuple(BlockSnap(i, dy, 6, 3, 6, 8)
                                               for i in range(2))),
                "back": FaceSnap(applied=False, reason=REASON_DISABLED)}

    out8 = reject_overlapping(t, _plan(8))
    assert (out8["front"].applied, out8["front"].reason) == (False, REASON_OVERLAP)
    # fail-safe 件数（対象外ではない）に計上される
    assert snap.page_counter_key(out8) == "failsafe"
    out7 = reject_overlapping(t, _plan(7))
    assert out7["front"].applied is True          # 余白ちょうどまでは通る
    assert snap.page_counter_key(out7) == ""      # 「常に戻る」実装では緑にならない
    assert out7["back"].reason == REASON_DISABLED  # 他面は触らない


def _hand_template(cells: tuple[CellSpec, ...]) -> Template:
    """重なりを持つテンプレートは load_template が拒否するので手で組む。"""
    geom = TableGeom(0, 100, 0, 300, (0, 100, 200, 300), (0, 100), row_gap=8)
    face = Face(face_id="f", page_offset=0, source_rect=Rect(0, 0, 400, 400),
                table_zones=(TableZone("t", 0, 100, 200, block_idx=0),),
                table_geoms=(geom,), shift_limits=(10, 20))
    return Template(template_id="hand", render_dpi=300, image_size=(400, 400),
                    record_pages=1, faces=(face,), cells=cells)


def _snap_of(dy: int) -> dict:
    return {"f": FaceSnap(applied=True, reason="",
                          blocks=(BlockSnap(0, dy, 4, 2, 4, 20),))}


def test_new_overlap_rejects_the_face():
    """吸着で**新しく**重なる対があれば戻す。"""
    t = _hand_template((
        CellSpec("static", "f", Rect(0, 0, 100, 100), "text"),
        CellSpec("row", "f", Rect(0, 110, 100, 90), "text",
                 table_id="t", row_no=1, block_idx=0),
    ))
    out = reject_overlapping(t, _snap_of(-15))   # 110→95 で static(0..100) と重なる
    assert (out["f"].applied, out["f"].reason) == (False, REASON_OVERLAP)


def test_pre_existing_overlap_does_not_trigger_failsafe():
    """Go 条件②: 「新しい重なり」＝吸着前に重なっていなかった対のみ。
    もともと重なっている対を再検出して常時 fail-safe にしない。"""
    t = _hand_template((
        CellSpec("already", "f", Rect(0, 150, 100, 100), "text"),   # 150..250
        CellSpec("row", "f", Rect(0, 110, 100, 90), "text",         # 110..200
                 table_id="t", row_no=1, block_idx=0),
    ))
    out = reject_overlapping(t, _snap_of(-15))   # 95..185 — 前も後も重なっている
    assert out["f"].applied is True


def test_overlap_check_ignores_exclusions():
    """除外領域との重なりは検査しない（W-1/W-2 が「拒否せず見える化」と
    決めた領域で、重なっても白塗りで〓になるだけ。値の取り違えは起きない）。"""
    t = _hand_template((
        CellSpec("row", "f", Rect(0, 110, 100, 90), "text",
                 table_id="t", row_no=1, block_idx=0),
    ))
    t = Template(template_id=t.template_id, render_dpi=t.render_dpi,
                 image_size=t.image_size, record_pages=t.record_pages,
                 faces=(Face(face_id="f", page_offset=0,
                             source_rect=Rect(0, 0, 400, 400),
                             exclusions=(Rect(0, 90, 100, 40),),
                             table_zones=t.faces[0].table_zones,
                             table_geoms=t.faces[0].table_geoms,
                             shift_limits=(10, 20)),),
                 cells=t.cells)
    assert reject_overlapping(t, _snap_of(-15))["f"].applied is True


def test_overlap_scan_stays_cheap_on_the_real_template():
    """4-F のコスト。x 帯が重なる対に絞り、y ソートで前進を打ち切る掃引。

    母集団は front 58／back 140 の受け皿矩形（`chouhyo-v1`）。**両面あわせて
    0.712 ms/ページ**（200 回平均・2026-09-03 実測）。計測値は環境で変わるので
    **上限は緩く**取り、桁が変わったときだけ落ちるようにする（NFR-F07 の
    +0.15 秒/枚＝150 ms に対して2桁の余裕があることの見張り）。
    """
    t = load_template(TPL)
    plan = _all_applied(t, 3)
    t0 = time.perf_counter()
    for _ in range(20):
        reject_overlapping(t, plan)
    per_page_ms = (time.perf_counter() - t0) / 20 * 1000
    assert per_page_ms < 15.0, f"{per_page_ms:.2f} ms/page"


# ---------------------------------------------------------------------------
# 永続化の形（判断3-B）と読み出し口の単一性（T-P2）
# ---------------------------------------------------------------------------

def test_transform_json_holds_geometry_only_and_detail_holds_no_dy():
    """判断3-B: `transform["snap"]` は幾何（適用した dy）だけ、`snap_detail` は
    記録だけ。**同じ値を2箇所に持たせない**——detail に `dy` は無く
    `measured_dy` がある。"""
    fs = FaceSnap(applied=False, reason=REASON_FAILSAFE,
                  blocks=(BlockSnap(0, 0, 15, 8, 15, 4),
                          BlockSnap(1, 3, 3, 8, 15, 4, BLOCK_FEW_LINES)))
    tj = snap.to_transform_json(fs)
    assert tj == {"v": 1, "applied": False, "dy": [0, 0]}
    dj = json.loads(snap.to_detail_json(fs))
    assert dj["reason"] == REASON_FAILSAFE
    assert [b["measured_dy"] for b in dj["blocks"]] == [0, 3]
    assert all("dy" not in b for b in dj["blocks"])
    assert snap.snap_px_of(fs) == -1.0

    ok = FaceSnap(applied=True, reason="",
                  blocks=(BlockSnap(0, 0, 15, 8, 15, 4),
                          BlockSnap(1, 3, 15, 8, 15, 4)))
    assert snap.to_transform_json(ok)["dy"] == [0, 3]
    assert snap.snap_px_of(ok) == 3.0


@pytest.mark.parametrize("blob", [
    {}, {"v": 99, "applied": True, "dy": [3]}, {"v": 1, "dy": "x"}, "not a dict",
])
def test_from_json_falls_back_to_identity_on_unknown_shapes(blob):
    """知らない形式版・壊れた値は「未記録」＝恒等に倒す（黙って別解釈しない）。"""
    fs = snap.from_json(blob)
    assert fs.applied is False
    assert fs.dy_by_block() == ()


def test_t_p2_transform_snap_key_appears_in_exactly_two_places():
    """判断3-C: `transform` の "snap" キーに触るコードは**読み1・書き1**だけ。

    読み出し口が2つに増えると5経路が別々の答えを出しうる（FR-F37 が潰したい
    故障そのもの）。08 §6 は `grep '\\["snap"\\]'` が1件であることを求めて
    いるが、素の grep はコメント・docstring 中の説明文まで数え、実装は
    `t.get("snap")` の形なので当たらない——**字句ではなくトークンで数える**
    （コメントは COMMENT トークンとして除外され、docstring は "snap" と
    一致しない）。書き側（pipeline.py の upsert）も1箇所であることを同時に
    固定する。
    """
    import io
    import tokenize

    root = Path(__file__).resolve().parents[1] / "chouhyo_ocr"
    hits: list[str] = []
    for p in sorted(root.glob("*.py")):
        src = p.read_text(encoding="utf-8")
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING and tok.string.strip("'\"") == "snap" \
                    and tok.string[0] in "'\"":
                hits.append(f"{p.name}:{tok.start[0]}")
    assert [h.split(":")[0] for h in hits] == ["pipeline.py", "store.py"], hits


def test_page_counter_never_counts_a_page_twice():
    """4-H: excluded を優先し、1ページを2つの件数へ二重計上しない。"""
    both = {"front": FaceSnap(False, REASON_EXCLUDED),
            "back": FaceSnap(False, REASON_FAILSAFE)}
    assert snap.page_counter_key(both) == "excluded"
    assert snap.page_counter_key({"a": FaceSnap(False, REASON_OVERLAP)}) == "failsafe"
    assert snap.page_counter_key({"a": FaceSnap(False, REASON_DISABLED)}) == ""
    assert snap.page_counter_key({"a": FaceSnap(False, REASON_NO_ESTIMATE)}) == ""
    assert snap.page_counter_key({"a": FaceSnap(True, "")}) == ""


# ---------------------------------------------------------------------------
# FR-F48・AC-F64: 保存時の警告（W-7）と、T-5b 追補の W-6
# ---------------------------------------------------------------------------

def test_ac_f64_small_table_warns_on_load(tmp_path):
    """AC-F64: 期待横線 4 本以下の表を持つテンプレートで警告が1件出る。
    現存テンプレート（family 6・detail 15・formB visit 6）では発火しない。"""
    assert [w for w in load_template(TPL).warnings if w.startswith("[W-7]")] == []
    assert [w for w in load_template(FORMB).warnings if w.startswith("[W-7]")] == []

    raw = json.loads(TPL.read_text(encoding="utf-8"))
    for blk in raw["faces"][0]["tables"][0]["blocks"]:
        blk["rows"] = 3            # 期待横線 4 本
    p = tmp_path / "small.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    w7 = [w for w in load_template(p).warnings if w.startswith("[W-7]")]
    assert len(w7) == 1, w7        # 表ごとに1件（ブロックごとではない）
    assert "family" in w7[0] and "4 本" in w7[0]


def test_w6_outer_edge_notice_fires_regardless_of_anchor_count(tmp_path):
    """T-5b 追補: 欄アンカー面では**欄数に関わらず**「最外の欄の外側の辺が
    紙に要る」注意を出す。欄を増やして W-5（個数不足）を消しても、最外の辺が
    紙に無ければ毎回 `edge_mismatch` で落ちる——独立した失敗要因なので
    個数条件に相乗りさせない。表のある面には出さない。"""
    assert [w for w in load_template(TPL).warnings if w.startswith("[W-6]")] == []

    raw = json.loads(TPL.read_text(encoding="utf-8"))
    front = raw["faces"][0]
    front.pop("tables", None)      # 表を消して欄アンカー面にする
    p = tmp_path / "fields_only.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    t = load_template(p)
    assert len(t.face("front").field_geoms) >= 3        # 欄は十分にある
    assert [w for w in t.warnings if w.startswith("[W-5]")] == []
    w6 = [w for w in t.warnings if w.startswith("[W-6]")]
    assert len(w6) == 1 and "front" in w6[0]
    assert "外形不一致" in w6[0]
