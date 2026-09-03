"""ブロック単位の枠吸着（issue #75 (f)・07 §5.5・08 §6）。

**純関数だけを置く。画像には一切触らない。** 入力は `align.estimate_shift` が
測った `ShiftEstimate.block_shifts`（追加の画像走査ゼロ・NFR-F07）と
テンプレートの幾何で、出力は「面ごとにどのブロックを何 px 動かすか」を表す
`FaceSnap` と、それを適用した `Template` の複製。

守っている不変条件（08 §6）:

1. **吸着 OFF では計算が1行も走らない。** `plan_face_snap` は `enabled` が
   False なら最初の分岐で戻る。バイト一致（AC-F45）はテストではなく構造で守る
2. **`apply_snap` はどの面も適用しないなら入力の `Template` を同一オブジェクト
   のまま返す。** 複製すらしない——OFF・未記録・fail-safe のすべてで下流が
   1バイトも変わらないことが `is` で確かめられる
3. **動かすのは表由来のセル（`block_idx is not None`）と、対応する
   `TableZone.bottom`・`TableGeom` の y だけ。** 単発欄・除外領域・面の
   切り出し矩形・欄アンカー（`field_geoms`）・探索上限は動かさない（判断2）
4. **x 座標は 1 つも動かさない**（FR-F34。x が no-op なのは仕様で、
   `tolerance_x()` は 0 ではなく文字列定数を返して「許容幅 0 による偶然の
   no-op」と区別できるようにする）
5. **部分適用を作らない。** 1ブロックでも条件を満たさなければ面全体を
   テンプレート座標へ戻す（FR-F42）
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from typing import Mapping

from .align import SHIFT_MATCH_RATIO, ShiftEstimate
from .template import (SNAP_EXCLUDED_H_LINES_MAX, Face, Rect, TableGeom,
                       Template)

# `transform["snap"]` の形式版。読み戻す側が知らない版なら「未記録」として
# 恒等に倒す（黙って別の意味で解釈しない）
SNAP_FORMAT_VERSION = 1

# x 軸の許容幅は「0」ではなく、**対象外であること**を型の違いで表す
# （FR-F34・AC-F33）。0 を返すと「許容幅 0 で常に fail-safe」という偶然の
# no-op と区別できず、実装漏れが緑のまま通る
X_AXIS_NOT_SUPPORTED = "x_not_supported"

# 面の理由コード（FaceSnap.reason）。applied=True のときは空文字列
REASON_DISABLED = "disabled"                 # 設定 OFF（吸着を試みてすらいない）
REASON_NO_ESTIMATE = "no_estimate"           # 位置合わせ失敗面・測定が無い
REASON_NO_BLOCKS = "no_blocks"               # 表を持たない面（issue #86）
REASON_NOT_RECORDED = "not_recorded"         # 中間データに吸着の記録が無い（復元経路）
REASON_EXCLUDED = "excluded_small_table"     # FR-F35（テンプレート定義由来）
REASON_FAILSAFE = "failsafe"                 # FR-F36（入力画像由来）
REASON_OVERLAP = "overlap_after_snap"        # FR-F36 第3条件（吸着後の新しい重なり）

# ブロックの理由コード（BlockSnap.reason）。健全なら空文字列
BLOCK_FEW_LINES = "few_lines"                # 一致本数 < need_y
BLOCK_OVER_TOLERANCE = "over_tolerance"      # |測った量| > 許容幅
BLOCK_SMALL_TABLE = "excluded_small_table"   # 期待横線が下限以下


@dataclass(frozen=True)
class BlockSnap:
    """1ブロックの吸着の測定と判定（記録専用の内訳）。"""
    block_idx: int
    measured_dy: int  # 測った量。**適用した量ではない**（面が戻れば適用は 0）
    matched: int      # そのブロック単独での一致本数
    need: int         # need_y = max(2, ceil(expected × SHIFT_MATCH_RATIO))
    expected: int     # 期待横線の本数
    allow: int        # 許容幅（row_pitch − row_height）
    reason: str = ""


@dataclass(frozen=True)
class FaceSnap:
    """1面の吸着の計画（＋復元結果）。

    `measured_dy`（測った量）と `dy_by_block()`（適用した量）を分けて持つ。
    fail-safe のとき前者は非ゼロ・後者は 0 になる——同じ値になる場合
    （applied=True）でも役割の違いが名前から読める（08 §6 判断3-B）。
    """
    applied: bool = False
    reason: str = REASON_DISABLED
    blocks: tuple[BlockSnap, ...] = ()

    def dy_by_block(self) -> tuple[int, ...]:
        """block_idx 順の**適用した** y 移動量。applied=False なら全 0。"""
        if not self.applied:
            return tuple(0 for _ in self.blocks)
        return tuple(b.measured_dy for b in self.blocks)


# ---------------------------------------------------------------------------
# 判定の材料（純関数・単体で検証できる粒度）
# ---------------------------------------------------------------------------

def tolerance_y(geom: TableGeom) -> int:
    """y 軸の許容幅（FR-F34）。行間隙 `row_pitch − row_height` をそのまま返す。

    実測値: `chouhyo-v1` family 8px／detail 4px、`formB-v1` visit 10px。
    px をコードへ直書きせず `TableGeom.row_gap` から取る。
    """
    return geom.row_gap


def tolerance_x() -> str:
    """x 軸は今回の吸着対象外（FR-F34）。**0 ではなく文字列定数**を返す。"""
    return X_AXIS_NOT_SUPPORTED


def need_y(expected_lines: int) -> int:
    """そのブロックで一致を要求する本数（FR-F35）。

    `max(2, ceil(n × SHIFT_MATCH_RATIO))`。`SHIFT_MATCH_RATIO` は
    `estimate_shift` と同じ定数を流用する——吸着のために新しい較正対象を
    増やさない。
    """
    return max(2, math.ceil(expected_lines * SHIFT_MATCH_RATIO))


def is_small_table(geom: TableGeom) -> bool:
    """期待横線が下限以下＝吸着を信用しない表か（FR-F35）。

    n=4 では `ceil(4×0.5)=2` が `need_y` の下限 2 と拮抗し、n=5 で初めて
    `ceil` 側が上回る。現存テンプレート（family 6・detail 15・formB visit 6）
    では発火しない。
    """
    return len(geom.h_lines) <= SNAP_EXCLUDED_H_LINES_MAX


# ---------------------------------------------------------------------------
# 面ごとの計画（08 §6 判断4-G の判定順）
# ---------------------------------------------------------------------------

def plan_face_snap(face: Face, est: "ShiftEstimate | None",
                   enabled: bool) -> FaceSnap:
    """1面の吸着を計画する。**この関数は重なり検査を含まない**——
    第3条件（FR-F36）は面をまたいだ幾何を要るので `reject_overlapping` が担う。

    判定順（この順序が FR-F35 と FR-F36 を別物として分ける）:

    1. `enabled` が False → `disabled`。**ここで即 return**（1バイトも計算しない）
    2. `est` が無い／失敗／`block_shifts` が空 → `no_estimate`
    3. 表を持たない面 → `no_blocks`（issue #86。位置合わせ失敗と区別する）
    4. どれか1ブロックでも期待横線が下限以下 → 面全体 `excluded_small_table`
    5. どれか1ブロックの一致本数 < `need_y` → 面全体 `failsafe`
    6. どれか1ブロックの |測った量| > 許容幅 → 面全体 `failsafe`
    7. 全ブロック健全 → `applied=True`
    """
    if not enabled:
        return FaceSnap(applied=False, reason=REASON_DISABLED, blocks=())
    if est is None or not est.ok or not est.block_shifts:
        return FaceSnap(applied=False, reason=REASON_NO_ESTIMATE, blocks=())
    if not face.table_geoms:
        return FaceSnap(applied=False, reason=REASON_NO_BLOCKS, blocks=())

    blocks: list[BlockSnap] = []
    excluded = False
    failsafe = False
    for bs in est.block_shifts:
        geom = face.table_geoms[bs.block_idx]
        allow = tolerance_y(geom)
        need = need_y(bs.expected)
        if is_small_table(geom):
            reason = BLOCK_SMALL_TABLE
            excluded = True
        elif bs.matched < need:
            reason = BLOCK_FEW_LINES
            failsafe = True
        elif abs(bs.dy) > allow:
            # 境界は**含む**（08 §6 判断4-C）。全行を +allow 動かすと行 i の
            # 下端は次の行の元の上端にちょうど接するだけで重ならない。
            # `>=` で弾くと救える帯（detail で 2〜4px）を 1px 削る
            reason = BLOCK_OVER_TOLERANCE
            failsafe = True
        else:
            reason = ""
        blocks.append(BlockSnap(block_idx=bs.block_idx, measured_dy=bs.dy,
                                matched=bs.matched, need=need,
                                expected=bs.expected, allow=allow,
                                reason=reason))
    if excluded:
        # 除外は fail-safe とは**別カウント**（FR-F41）。テンプレート定義由来
        # なので毎回同じ件数になり、直す先が違う
        return FaceSnap(applied=False, reason=REASON_EXCLUDED,
                        blocks=tuple(blocks))
    if failsafe:
        return FaceSnap(applied=False, reason=REASON_FAILSAFE,
                        blocks=tuple(blocks))
    return FaceSnap(applied=True, reason="", blocks=tuple(blocks))


# ---------------------------------------------------------------------------
# fail-safe の第3条件: 吸着後に新しい重なりが生じたら面全体を戻す（判断4-F）
# ---------------------------------------------------------------------------

def _overlap(ra: Rect, rb: Rect) -> bool:
    """`load_template` の重なり判定（issue #24）と同一の述語。"""
    return (ra.x < rb.x + rb.w and rb.x < ra.x + ra.w
            and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h)


def _face_entries(template: Template, face_id: str,
                  dy_by_block: tuple[int, ...]) -> list[tuple[str, Rect, Rect]]:
    """その面の受け皿を (field_id, 吸着前の矩形, 吸着後の矩形) で列挙する。

    母集団は `load_template` の重なり検査と揃える——セルの全領域
    （主＋追加）と参照先の枠。**除外領域は含めない**（W-1/W-2 が「拒否せず
    見える化」と決めた領域で、重なっても白塗りで〓になるだけ。値の取り違えは
    起きない）。
    """
    out: list[tuple[str, Rect, Rect]] = []
    for c in template.cells:
        if c.face_id != face_id:
            continue
        d = 0 if c.block_idx is None else dy_by_block[c.block_idx]
        for r in c.all_rects():
            out.append((c.field_id, r, Rect(r.x, r.y + d, r.w, r.h)))
        if c.fallback_rect is not None:
            r = c.fallback_rect
            out.append((c.field_id, r, Rect(r.x, r.y + d, r.w, r.h)))
    return out


def _has_new_overlap(template: Template, face_id: str,
                     dy_by_block: tuple[int, ...]) -> bool:
    """吸着後に**新しい**重なりが生じるか（吸着前に重なっていた対は数えない）。

    走査は「吸着後の矩形を y でソートし、y 帯が重なる間だけ前進、そのうち
    x 帯も重なる対だけを検査」する掃引。母集団 n は front 面 58・back 面 140
    の受け皿矩形（`chouhyo-v1`・2026-09-03 実測）で、比較は整数のみ。
    両面あわせて **0.712 ms/ページ**（200 回平均・同日実測）。

    既存の重なりを再検出して常時 fail-safe にしないため、判定は
    「吸着後に重なる」かつ「吸着前は重なっていない」の連言に限る。
    """
    entries = _face_entries(template, face_id, dy_by_block)
    entries.sort(key=lambda e: e[2].y)
    n = len(entries)
    for i in range(n):
        fid_a, pre_a, post_a = entries[i]
        a_bottom = post_a.y + post_a.h
        for j in range(i + 1, n):
            fid_b, pre_b, post_b = entries[j]
            if post_b.y >= a_bottom:
                break  # y ソート済み——これ以降は必ず離れている
            if fid_a == fid_b:
                continue  # 同じ欄の領域どうしは同じ受け皿（load_template と同じ扱い）
            if not (post_a.x < post_b.x + post_b.w
                    and post_b.x < post_a.x + post_a.w):
                continue  # x 帯が重ならない対は見ない
            if _overlap(pre_a, pre_b):
                continue  # 吸着前から重なっていた＝新しい重なりではない
            return True
    return False


def reject_overlapping(template: Template,
                       snaps: Mapping[str, FaceSnap]) -> dict[str, FaceSnap]:
    """吸着後に新しい重なりが生じる面を `overlap_after_snap` で戻す（判断4-F）。

    なぜ要るか（2026-09-03 実測）: 出荷テンプレート `chouhyo-v1` の front 面で、
    family 表の最終行下端（y=1464）と単発欄 `person_備考` の上端（y=1471）の
    余白は **7px** しかないのに、許容幅（`row_pitch − row_height`）は **8px**
    ある。許容幅を使い切ると 1px 重なり、`mapping._bucket_cells` の first-hit
    が定義順で行き先を決めるため **`status=正常` のまま値が入れ替わる**。
    `load_template` の重なり拒否（issue #24）はテンプレート座標で読み込み時に
    1回走るだけで、吸着後の座標は1度も検証されない。

    落とす単位は**面**（FR-F42）。クランプ（許容幅を余白まで縮めて適用する）は
    採らない——測った量が +8 なのに +7 だけ動かすのは「半分だけ正しい」状態で、
    D-25 が禁じた形そのもの。動かすか戻すかの2択にする。

    **計画時（新規整列）だけ実行する。** 復元経路は検査を通った適用済みの dy を
    読むので再検査しない。
    """
    out: dict[str, FaceSnap] = {}
    for face_id, fs in snaps.items():
        if fs.applied and _has_new_overlap(template, face_id, fs.dy_by_block()):
            out[face_id] = replace(fs, applied=False, reason=REASON_OVERLAP)
        else:
            out[face_id] = fs
    return out


# ---------------------------------------------------------------------------
# 適用（Template の複製）
# ---------------------------------------------------------------------------

def _shift_rect(r: Rect, dy: int) -> Rect:
    """y だけ動かす。**x は触らない**（FR-F34 の NG 事項）。"""
    return Rect(r.x, r.y + dy, r.w, r.h)


def apply_snap(template: Template,
               snap_by_face: Mapping[str, FaceSnap]) -> Template:
    """吸着後の姿の `Template` を返す（FR-F37 の5経路が共通で使う唯一の関数）。

    どの面も適用しないなら**入力をそのまま返す**（同一オブジェクト）。
    座標をコピーして持ち回らず、5経路すべてがこの1関数の出力を使うことで
    「同じ座標」を構造で担保する。

    動かすもの: 表由来のセル（`rect`・`extra_rects`・`fallback_rect`・
    `choice_marks`）／`TableZone.bottom`／`TableGeom` の y。
    動かさないもの: 単発欄・`exclusions`・`source_rect`・`shift_limits`・
    `field_geoms`（08 §6 判断2・5-B）。

    ⚠️ **`align_page`／`estimate_shift` にこの戻り値を渡さない。** アンカーが
    二重に動く。使ってよいのは位置合わせ後の割付・スコアリング・描画だけ。
    """
    # ブロック数が合わない記録は、このテンプレートを説明していない——
    # 適用せずテンプレート座標のままにする（fail-safe 側へ倒す）。remap は
    # template_hash を照合しない（check_template=False）ため、表のブロックを
    # 増減させたテンプレートで保存済みの dy を読む経路が実在する
    dy_map: dict[str, tuple[int, ...]] = {}
    for f in template.faces:
        s = snap_by_face.get(f.face_id)
        if s is None or not s.applied:
            continue
        dys = s.dy_by_block()
        if len(dys) != len(f.table_geoms):
            continue
        dy_map[f.face_id] = dys
    if not any(any(dys) for dys in dy_map.values()):
        # 適用面が無い、または全ブロックの適用量が 0（＝結果が同じ）
        return template

    cells = []
    for c in template.cells:
        dys = dy_map.get(c.face_id)
        if dys is None or c.block_idx is None:
            cells.append(c)          # 単発欄・非適用面はそのまま（判断2）
            continue
        d = dys[c.block_idx]
        if d == 0:
            cells.append(c)
            continue
        cells.append(replace(
            c,
            rect=_shift_rect(c.rect, d),
            extra_rects=tuple(_shift_rect(r, d) for r in c.extra_rects),
            fallback_rect=(None if c.fallback_rect is None
                           else _shift_rect(c.fallback_rect, d)),
            choice_marks=tuple(replace(m, rect=_shift_rect(m.rect, d))
                               for m in c.choice_marks),
        ))

    faces = []
    for f in template.faces:
        dys = dy_map.get(f.face_id)
        if dys is None or not any(dys):
            faces.append(f)
            continue
        faces.append(replace(
            f,
            table_zones=tuple(replace(z, bottom=z.bottom + dys[z.block_idx])
                              for z in f.table_zones),
            table_geoms=tuple(
                replace(g, y_min=g.y_min + dys[i], y_max=g.y_max + dys[i],
                        h_lines=tuple(y + dys[i] for y in g.h_lines))
                for i, g in enumerate(f.table_geoms)),
        ))
    return replace(template, cells=tuple(cells), faces=tuple(faces))


# ---------------------------------------------------------------------------
# 永続化の形（幾何は transform["snap"]・記録は snap_detail・判断3-B）
# ---------------------------------------------------------------------------

def to_transform_json(snap: FaceSnap) -> dict:
    """`alignment.transform["snap"]` に入れる**幾何だけ**の形。

    `dy` は block_idx 順の「**適用した**量」（applied=False なら全 0）。
    理由コード・一致本数はここに書かない——復元に要らないものを唯一の
    復元源へ混ぜると、読む側が判定に使いはじめる余地ができる。
    """
    return {"v": SNAP_FORMAT_VERSION, "applied": bool(snap.applied),
            "dy": list(snap.dy_by_block())}


def to_detail_json(snap: FaceSnap) -> str:
    """`alignment.snap_detail` に入れる**記録専用**の形（座標を作る側は読まない）。

    `dy` は入れない（`transform` と二重に持たない）。入るのは
    `measured_dy`＝測った量で、fail-safe のときに「いくら動かそうとして
    戻したのか」を事後に読むための値。
    """
    return json.dumps({
        "applied": bool(snap.applied),
        "reason": snap.reason,
        "blocks": [{"block_idx": b.block_idx, "measured_dy": b.measured_dy,
                    "matched": b.matched, "need": b.need,
                    "expected": b.expected, "allow": b.allow,
                    "reason": b.reason} for b in snap.blocks],
    }, ensure_ascii=False)


def snap_px_of(snap: FaceSnap) -> float:
    """`alignment.snap_px`（面の代表値）。未吸着は -1（未計測の印）。"""
    dys = snap.dy_by_block()
    if not snap.applied or not dys:
        return -1.0
    return float(max(abs(d) for d in dys))


def from_json(d: Mapping) -> FaceSnap:
    """`transform["snap"]` を `FaceSnap` へ戻す（復元経路）。

    戻るのは幾何（適用した dy）だけで、理由コード・一致本数は戻らない
    ——それらは `snap_detail` にあり、座標を作る側は読んではならない。
    知らない形式版・壊れた値は「未記録」として恒等（applied=False）に倒す。
    """
    if not isinstance(d, Mapping) or d.get("v") != SNAP_FORMAT_VERSION:
        return FaceSnap(applied=False, reason=REASON_NOT_RECORDED, blocks=())
    dys = d.get("dy")
    if not isinstance(dys, list) or not all(isinstance(v, int) for v in dys):
        return FaceSnap(applied=False, reason=REASON_NOT_RECORDED, blocks=())
    applied = bool(d.get("applied"))
    blocks = tuple(BlockSnap(block_idx=i, measured_dy=int(v), matched=0,
                             need=0, expected=0, allow=0)
                   for i, v in enumerate(dys))
    return FaceSnap(applied=applied,
                    reason="" if applied else REASON_NOT_RECORDED,
                    blocks=blocks)


def from_store_rows(
        rows: Mapping[str, tuple[dict, int]]) -> dict[str, FaceSnap]:
    """`store.snap_geometry()` の戻り値を `FaceSnap` の辞書にする。

    復元経路（`remap`・`debug-images`・`_restore_alignment`）はこの2行だけを
    書く——`transform["snap"]` を直接読むコードを増やさない（判断3-C）。
    """
    return {face_id: from_json(blob) for face_id, (blob, _enabled) in rows.items()}


# ---------------------------------------------------------------------------
# 件数（FR-F41・判断4-H）
# ---------------------------------------------------------------------------

def page_counter_key(snaps: Mapping[str, FaceSnap]) -> str:
    """1ページを `excluded` / `failsafe` / "" のどれに数えるか。

    **両方には数えない**（優先は excluded＝テンプレート定義由来で毎回同じ＝
    先に直すべき原因）。`disabled`・`no_estimate`・`no_blocks`・
    `not_recorded` はどちらにも数えない——吸着を試みてすらいないため。
    この規則により `failsafe件数 + excluded件数 <= 総ページ数` が成立する。
    """
    reasons = {s.reason for s in snaps.values()}
    if REASON_EXCLUDED in reasons:
        return "excluded"
    if REASON_FAILSAFE in reasons or REASON_OVERLAP in reasons:
        return "failsafe"
    return ""
