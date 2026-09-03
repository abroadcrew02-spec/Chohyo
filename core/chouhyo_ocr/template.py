"""テンプレートの読み込み・検証・格子展開（設計 §4.2）。

読み込み時に JSON Schema（schema/template.schema.json・コアとエディタの共有正本）で
検証し、続けて v1 の受け入れ範囲を機械的に確認する。範囲外は黙って続行せず
TemplateError で拒否する（設計 §4.2「読めないなら止まる方が安い」）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from jsonschema import exceptions as jsonschema_exceptions
from jsonschema import validators as jsonschema_validators

from . import logging_safe as log
from .paths import template_schema_path

SCHEMA_VERSION = 1
V1_FACE_IDS = {"front", "back"}
# 選択肢マークが欄の矩形からはみ出せる上限（px）。値の根拠は load_template の
# 検証箇所（issue #48）のコメントに実測つきで書いてある
CHOICE_MARK_MARGIN_PX = 4

# 表を持たない面（単発欄だけの紙）で、欄の枠線を平行移動推定のアンカーに
# 使うための定数（issue #86・D-25 の別経路）。表のある面には一切効かない。
#
# ANCHOR_MIN_SPAN_PX: アンカーとして採る辺の最小の長さ（px）。射影の被覆率
#   条件（projection.H_COVERAGE=0.50 / V_COVERAGE=0.35）は分母が小さいほど
#   ノイズに弱く、短い辺だと文字の一画が「線」として拾われる。横線は幅、
#   縦線は高さでそれぞれ足切りする。
# ANCHOR_SEARCH_PX: 欄だけの面の平行移動探索の上限（px・両軸同値）。表の
#   ような周期構造が無いので _shift_limits の式（最小ピッチの半分）は使わず
#   固定値にする——欄1個の高さがそのまま「ピッチ」になり、小さい欄が1つ
#   あるだけで面全体の探索範囲が潰れるため。
# ANCHOR_WARN_MIN: これ未満のアンカー欄しかない面に W-5 警告を出す（拒否はしない）。
#
# ※要較正: いずれも 300dpi（BASE_DPI）想定の設計判断で、実際の「欄だけの紙」
# の検体での較正はしていない（本 issue の時点で検体 0 枚）。実検体が入った
# 時点で測り直す。dpi が違うテンプレートでは BASE_DPI 比でスケールして使う。
ANCHOR_MIN_SPAN_PX = 40
ANCHOR_SEARCH_PX = 24
ANCHOR_WARN_MIN = 3

# 吸着（issue #75 (f)・FR-F35）を信用する下限。期待横線がこの本数以下の表は
# need_y = max(2, ceil(n × SHIFT_MATCH_RATIO)) の下限 2 と拮抗する（n=4 で
# ceil(4×0.5)=2 と同値、n=5 で初めて ceil 側が上回る）ため、吸着の対象に
# しない。判定そのものは snap.is_small_table にあり、ここは保存時警告
# （W-7・FR-F48）と共有するための1つの正本。snap.py は template.py を
# import する側なので、定数はこちらに置く（逆向きは循環 import になる）
SNAP_EXCLUDED_H_LINES_MAX = 4

# px 単位の内部定数（mapping._LINE_GAP/_BUCKET・align.COARSE_DILATE/
# SHIFT_RUNNER_DIST・grid.ROW_INSET・projection.LINE_GAP・本モジュールの
# CHOICE_MARK_MARGIN_PX）が較正された基準 dpi（汎用化 A-3）。render_dpi は
# テンプレートで可変（schema/template.schema.json: 72〜1200）だが、これらの
# 定数は現行様式の 300dpi 実測に固定較正されている。dpi が違う様式では
# 黙って壊れる（行クラスタ崩れ→〓増・枠外率超過→様式不一致誤判定）ため、
# 各定数を使う関数はスケールしてから使う。実装上は大半が `dpi: int` 引数を
# 受け取り、その場で `dpi / BASE_DPI` を計算する形（呼び出し元は
# template.render_dpi を渡す）——Template インスタンスを直接持つ数少ない
# 呼び出し元（align.align_page）だけが Template.dpi_scale プロパティを
# そのまま使う（S-6b・2026-09-01 実態に合わせて訂正）。
# era の帯定数（era.BAND_PAD/BAND_PAD_IN）は意図的にスケール対象外
# （D-31 で向きまで実測較正済み・06_second_form_findings.md §7）。
# render_dpi != BASE_DPI での choice 欄は load_template が拒否する（Q-MA）。
BASE_DPI = 300


class TemplateError(ValueError):
    """テンプレートが受け入れ範囲外。メッセージに拒否理由を必ず含める。"""


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class ChoiceMark:
    value: str
    rect: Rect  # 面ローカル座標


@dataclass(frozen=True)
class CellSpec:
    """物理セル1つ。subfields を持つセルは出力列を複数持つ（分割は render 時・D-23）。"""
    field_id: str
    face_id: str
    rect: Rect  # 面ローカル座標
    kind: str  # "text" | "choice"
    choice_marks: tuple[ChoiceMark, ...] = ()
    subfields: tuple[str, ...] = ()
    normalize: str | None = None  # 値の正規化方式（"amount"）。列名には依存させない（§5.2）
    table_id: str | None = None  # 繰り返し欄のとき所属テーブル（空行判定・D-06）
    row_no: int | None = None    # 繰り返し欄のとき行連番（1起点・ブロック跨ぎ通し）
    # 参照先の枠（text の単独欄のみ・任意）。主の rect に文字が1つも来なかった
    # ときに限り、この枠の読取値を採用する（mapping.assign が合流させる）。
    # 主にインクがあるが読めない（〓）場合は参照しない——答えは主に書かれて
    # いるのに別の場所の値を出すと誤転記になるため（転記主義・2026-08-31）
    fallback_rect: Rect | None = None
    # 追加の領域（text の単独欄のみ・任意）。主の rect と**等価な**受け皿で、
    # どの領域に入った文字も同じ欄に集まり読み順に連結される（L字・コの字の
    # 欄を作る・2026-08-31）。fallback_rect と違い「主が空のとき」の条件は無い

    extra_rects: tuple[Rect, ...] = ()
    # 出力列に出すか（省略時 true・issue #66 D-34）。false でも読み取り・
    # 重なり検証・空行判定・field_id の一意性検証は従来どおり行う——この
    # 属性が変えるのは出力の可否だけで、幾何にも母集団にも触れない
    # （FR-1.2）。対象外判定は output_cells() の1関数に集約する（S-3設計）。
    output: bool = True
    # 表由来のセルが属するブロックの面内通し序数（issue #75 (f)・FR-F33）。
    # faces[].tables[].blocks[] を定義順に平坦化した 0 始まりで、同じ面の
    # Face.table_geoms／Face.table_zones の並びと一致する。**単発欄（fields
    # 由来）は None**——吸着で動かすのは表由来のセルだけ（08 §6 判断2）。
    # 採番は load_template の面ループが行う（_expand_table 単体では面内の
    # 通し序数を決められない。同じ面に table が複数あると 0 から数え直す）
    block_idx: int | None = None

    def all_rects(self) -> tuple[Rect, ...]:
        """欄を構成する全領域（主＋追加）。参照先は含まない。"""
        return (self.rect, *self.extra_rects)

    def output_columns(self) -> tuple[str, ...]:
        if self.subfields:
            return tuple(f"{self.field_id}_{sf}" for sf in self.subfields)
        return (self.field_id,)

    def __repr__(self) -> str:  # 座標・種別のみ（設計 §8.1・付録 C7 の方針に合わせ簡潔に）
        return f"<CellSpec {self.field_id}>"


@dataclass(frozen=True)
class TableZone:
    """1ブロックの占有域（面ローカル）。枠外 symbol の below_table 判定に使う（§6.4）。"""
    table_id: str
    x_min: int
    x_max: int
    bottom: int  # 最終行の下端
    # CellSpec.block_idx と同じ面内通し序数（issue #75 (f)）。枠外 symbol の
    # below_table 判定に使う占有域も吸着で動くため、dy を配る対応が要る
    block_idx: int = 0


@dataclass(frozen=True)
class TableGeom:
    """テーブルの罫線期待位置（面ローカル）。平行移動推定のアンカー（D-25）。"""
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    h_lines: tuple[int, ...]  # 期待横線の y
    v_lines: tuple[int, ...]  # 期待縦線の x
    # 行間隙 = row_pitch − row_height（issue #75 (f)・FR-F34 の許容幅）。
    # px をコードへ直書きせずテンプレートから導出するための持ち回り。
    # 欄アンカー（_field_geoms・issue #86）は行ピッチを持たないので 0 のまま
    # ——欄の枠線は吸着の対象にしない（08 §6 判断5-B）
    row_gap: int = 0


@dataclass(frozen=True)
class Face:
    face_id: str
    page_offset: int
    source_rect: Rect  # 入力ページ座標
    exclusions: tuple[Rect, ...] = ()
    table_zones: tuple[TableZone, ...] = ()
    table_geoms: tuple[TableGeom, ...] = ()
    # 平行移動の探索上限 (n_x, n_y)。テンプレから導出する——探索が行ピッチ／
    # 列間隔の半分を超えると1行（列）ズレた解が正解と同点になり、行ズレを
    # 「補正」してしまう（D-25: N は許容範囲でなくエイリアシング境界）
    shift_limits: tuple[int, int] = (0, 0)
    # 表を持たない面でだけ使う、欄の枠線由来のアンカー（issue #86）。
    # **不変条件 A: table_geoms が非空なら field_geoms は必ず空。** 表のある
    # 面の位置合わせ経路を1バイトも変えないための構造上の保証で、
    # align.estimate_shift はこの2つを排他の分岐で使い分ける
    field_geoms: tuple[TableGeom, ...] = ()


@dataclass(frozen=True)
class Template:
    template_id: str
    render_dpi: int
    image_size: tuple[int, int]  # (width, height)
    record_pages: int
    faces: tuple[Face, ...]
    cells: tuple[CellSpec, ...] = field(default=())
    # 除外領域×受け皿の重なり警告（U-09・H-6・2026-08-31）。拒否はしない——
    # 出荷テンプレートに意図的な重なりが実在するため（§7.1）。cli.py の verify が
    # そのまま warnings: [string] として GUI 側へ渡す契約（変更不可）
    warnings: tuple[str, ...] = field(default=())

    def face(self, face_id: str) -> Face:
        for f in self.faces:
            if f.face_id == face_id:
                return f
        raise KeyError(face_id)

    @property
    def dpi_scale(self) -> float:
        """render_dpi が基準 dpi（BASE_DPI=300）からどれだけ違うかの倍率。

        mapping._LINE_GAP/_BUCKET・align.COARSE_DILATE・grid.ROW_INSET など
        300dpi 較正の px 定数をスケールするのに使う（汎用化 A-3）。
        render_dpi==BASE_DPI のとき 1.0 ちょうど（300/300.0）になる——
        定数側の「scale==1.0 なら従来値と完全一致」という契約はこの正確性に依存する。
        """
        return self.render_dpi / BASE_DPI


def output_cells(template: Template) -> tuple[CellSpec, ...]:
    """出力列に出すセルだけを、定義順のまま返す（issue #66 D-34・S-3設計）。

    列を作る側（columns.derive_columns）と値を作る側（render_rows.build_row・
    build_failure_row）の3経路がすべてここを通ることで、対象外判定が1箇所に
    集約され、列と値の対応がズレない。個別に `if not cell.output` を
    散らさないための唯一の窓口——3経路以外からもこの関数越しに参照すること。

    重なり検証・空行判定・field_id の一意性検証・fallback_rect の受け皿・
    resolveOverlaps はこの関数を経由しない（FR-1.2）——それらは
    `template.cells`（全セル）をそのまま見る。対象外はあくまで
    「出力列から外れる」だけで、テンプレートの構造からは消えない。
    """
    return tuple(c for c in template.cells if c.output)


def _rect(d: dict) -> Rect:
    return Rect(d["x"], d["y"], d["w"], d["h"])


def _expand_table(face_id: str, t: dict, *, block_base: int = 0) -> list[CellSpec]:
    """テーブル定義から格子を展開する（設計 §4.2 展開規則）。

    blocks を定義順に走り、行の通し番号はブロックを跨いで連番。

    block_base はこの table の先頭ブロックの**面内**通し序数（issue #75 (f)）。
    同じ面に table が複数あると各 table のブロックは 0 から数え直すため、
    面内で一意な序数は呼び出し元（load_template）が渡す。
    """
    cells: list[CellSpec] = []
    row_no = 0
    pitch, height = t["row_pitch"], t["row_height"]
    for bi, blk in enumerate(t["blocks"]):
        ox, oy = blk["origin"]["x"], blk["origin"]["y"]
        for i in range(blk["rows"]):
            row_no += 1
            top = oy + pitch * i
            for c in t["columns"]:
                fid = f'{t["table_id"]}_{row_no:02d}_{c["name"]}'
                rect = Rect(ox + c["x_offset"], top, c["width"], height)
                marks: tuple[ChoiceMark, ...] = ()
                if c["kind"] == "choice":
                    marks = tuple(
                        ChoiceMark(
                            m["value"],
                            Rect(
                                ox + m["x_offset"],
                                top + m.get("y_offset", 0),
                                m["width"],
                                m.get("height", height - m.get("y_offset", 0)),
                            ),
                        )
                        for m in c["choice_marks"]
                    )
                cells.append(
                    CellSpec(
                        field_id=fid,
                        face_id=face_id,
                        rect=rect,
                        kind=c["kind"],
                        choice_marks=marks,
                        # choice の subfields は無視する（issue #26: 残すと
                        # output_columns() が複数列を返すのに build_row の choice
                        # 分岐は値1個で、行の値数が列数とズレる。normalize と同じく
                        # 読み込み時に落とし、エディタの隠れ値を無害化する）
                        subfields=(tuple(c.get("subfields", ()))
                                   if c["kind"] == "text" else ()),
                        # choice・subfields 付きでは正規化は無視する（スキーマの明文）。
                        # 読み込み時に落とすことで、描画の発火条件と validate_v1 の
                        # 件数母集団が一致する（エディタで隠れた値が残っても無害）
                        normalize=(c.get("normalize")
                                   if c["kind"] == "text" and not c.get("subfields")
                                   else None),
                        table_id=t["table_id"],
                        row_no=row_no,
                        # 表の列は列単位の属性（全行一括・FR-1.1）。行ごとに
                        # 違う値を持たせる経路は無い——1つの tableColumn
                        # 定義が対象外なら、展開後の全行が対象外になる
                        output=c.get("output", True),
                        block_idx=block_base + bi,
                    )
                )
    return cells


def _table_geoms(t: dict) -> list[TableGeom]:
    """罫線の期待位置（D-25）。横線は origin.y + i*row_pitch（行境界）、
    縦線は origin.x + 列境界。ブロックごとに1つ作る。"""
    right = max(c["x_offset"] + c["width"] for c in t["columns"])
    v_offsets = sorted({c["x_offset"] for c in t["columns"]} | {right})
    geoms = []
    for blk in t["blocks"]:
        ox, oy = blk["origin"]["x"], blk["origin"]["y"]
        h = [round(oy + t["row_pitch"] * i) for i in range(blk["rows"] + 1)]
        geoms.append(TableGeom(
            x_min=ox, x_max=ox + right,
            y_min=oy, y_max=h[-1],
            h_lines=tuple(h),
            v_lines=tuple(ox + v for v in v_offsets),
            # 吸着の許容幅（issue #75 FR-F34）。row_height <= row_pitch は
            # load_template が先に検証済みなので負にならない
            row_gap=t["row_pitch"] - t["row_height"],
        ))
    return geoms


def _shift_limits(geoms: list[TableGeom]) -> tuple[int, int]:
    """探索上限 (n_x, n_y)。最小ピッチ・最小列間隔の半分から余裕2pxを引く。"""
    pitches = []
    gaps = []
    for g in geoms:
        hs, vs = sorted(g.h_lines), sorted(g.v_lines)
        pitches += [b - a for a, b in zip(hs, hs[1:])]
        gaps += [b - a for a, b in zip(vs, vs[1:])]
    if not pitches or not gaps:
        return (0, 0)
    return (max(0, min(gaps) // 2 - 2), max(0, min(pitches) // 2 - 2))


def _field_geoms(face_cells: list[CellSpec], dpi: int) -> list[TableGeom]:
    """単発欄の枠線を平行移動推定のアンカーにする（issue #86・表の無い面専用）。

    1 欄 = 1 geom。欄の矩形の 4 辺すべてを期待線として使うが、軸ごとに辺の
    長さで足切りする——横線は横方向に広がりが無いと射影で線として立たない
    ので幅で、縦線は高さで測る（ANCHOR_MIN_SPAN_PX）。両軸とも足りない欄は
    geom を作らない。

    縦線も要るのは estimate_shift の構造上の必然で、期待縦線が 0 本だと
    need_x = max(2, ...) に対して一致本数 0 となり、その面は必ず few_lines で
    失敗する。

    母集団は呼び出し元が渡すセル（面の fields 由来・table_id is None）だけで、
    追加の領域（extra_rects）・参照先（fallback_rect）は含めない。これらは
    「複数矩形の合併で1つの欄」を作る受け皿であって、個々の矩形の辺が紙に
    印刷されている保証が無い。存在しない期待線を足すと一致率の分母だけが
    増えて few_lines を誘発する。
    """
    span = max(1, round(ANCHOR_MIN_SPAN_PX * dpi / BASE_DPI))
    geoms = []
    for c in face_cells:
        r = c.rect
        h_lines = (r.y, r.y + r.h) if r.w >= span else ()
        v_lines = (r.x, r.x + r.w) if r.h >= span else ()
        if not h_lines and not v_lines:
            continue
        geoms.append(TableGeom(
            x_min=r.x, x_max=r.x + r.w,
            y_min=r.y, y_max=r.y + r.h,
            h_lines=h_lines, v_lines=v_lines,
        ))
    return geoms


def _anchor_search_limits(dpi: int) -> tuple[int, int]:
    """欄だけの面の探索上限 (n_x, n_y)。両軸とも固定値（issue #86・決定 5）。"""
    n = max(0, round(ANCHOR_SEARCH_PX * dpi / BASE_DPI))
    return (n, n)


def _anchor_shortage_warnings(faces: list[Face]) -> list[str]:
    """W-5: 表を持たず、アンカーに使える欄が少ない面の警告（issue #86）。

    拒否はしない——アンカーが 1 本でも作れるなら原理的に位置合わせは成立する
    （need = max(2, ...) は 1 欄の 2 辺で満たせる）。W-1〜W-4 と同じく
    「見える化のみ」に揃える。

    2026-09-03（issue #75・T-5b 追補）: 「最外の欄の外側の辺が紙に要る」の
    注意はこの警告から外し、W-6 として**欄の個数に関わらず**出すようにした。
    欄が 3 個以上あっても最外の辺が紙に無ければ毎回 `edge_mismatch` で落ちる
    （`test_field_anchor.py::test_t5b_outer_edge_missing_fails`）——個数の
    不足とは独立した失敗要因なので、個数条件に相乗りさせない。
    """
    warnings: list[str] = []
    for f in faces:
        if f.table_geoms:
            continue
        n = len(f.field_geoms)
        if n < ANCHOR_WARN_MIN:
            warnings.append(
                f"[W-5] 面 '{f.face_id}' は表を持たず、位置合わせのアンカーに"
                f"使える欄が {n} 個しかない。罫線のかすれや記入のはみ出しで"
                "位置合わせに失敗しやすい（表を1つ置くか、枠のある欄を増やすと安定する）")
    return warnings


def _anchor_outer_edge_warnings(faces: list[Face]) -> list[str]:
    """W-6: 欄アンカー面には、欄数に関わらず最外の辺の注意を出す（issue #75・T-5b 追補）。

    `estimate_shift` は欄アンカー面で「面内の期待線の**最外 2 本**（両軸）が
    検出線に当たること」を要求する（`align.py` の非周期アンカー・issue #86）。
    この 4 本は欄の個数と無関係に必要で、紙に印刷されていなければ欄が何個
    あっても毎回「外形不一致」で位置合わせに失敗する。W-5（個数不足）の
    条件に相乗りさせると、欄を増やして W-5 を消した利用者が同じ失敗に
    はまり続ける。
    """
    warnings: list[str] = []
    for f in faces:
        if f.table_geoms or not f.field_geoms:
            continue
        warnings.append(
            f"[W-6] 面 '{f.face_id}' は表を持たず、欄の枠線を位置合わせの基準に使う。"
            "面のいちばん上・下・左・右にある欄は、外側の辺の枠線が紙に印刷されて"
            "いる欄にすること（この4本を照合に使うため、紙に無いと毎回「外形不一致」で"
            "位置合わせに失敗する）")
    return warnings


def _small_table_snap_warnings(faces: list[Face], raw: dict) -> list[str]:
    """W-7: 期待横線が少ない表は吸着対象外になる（issue #75 FR-F48・AC-F64）。

    吸着（(f)）は期待横線 `SNAP_EXCLUDED_H_LINES_MAX` 本以下の表を信用せず、
    **その表を含む面全体**を対象外にする（FR-F35）。行数はテンプレートに
    書いてあるので、実行を待たず保存時点で予見できる。拒否はしない
    ——吸着が効かないだけで読み取りは従来どおり動く（W-1〜W-6 と同じ
    「見える化のみ」）。

    表 ID を出すため raw（元 JSON）から table_id を読む。`Face.table_geoms` は
    table_id を持たないので突き合わせは定義順で行う——`_table_geoms` が blocks を
    定義順に平坦化するのと同じ順序。

    **表ごとに1件**（ブロックごとではない）。1つの表を複数ブロックに割っても、
    利用者が直す対象は「その表の行数」1箇所だから。
    """
    warnings: list[str] = []
    faces_by_id = {f.face_id: f for f in faces}
    for rf in raw.get("faces", []):
        face = faces_by_id.get(rf["face_id"])
        if face is None or not face.table_geoms:
            continue
        idx = 0
        for t in rf.get("tables", []):
            counts = []
            for _blk in t["blocks"]:
                counts.append(len(face.table_geoms[idx].h_lines))
                idx += 1
            fewest = min(counts)
            if fewest > SNAP_EXCLUDED_H_LINES_MAX:
                continue
            warnings.append(
                f"[W-7] 面 '{rf['face_id']}' の表 '{t['table_id']}' は"
                f"期待横線 {fewest} 本のため枠の自動合わせ（吸着）の対象外になり、"
                "同じ面の他の表も吸着しない。行数を増やすか、吸着を使わない運用に"
                "する（吸着は既定 OFF なので、設定を変えていなければ読み取りへの"
                "影響はない）")
    return warnings


def _table_zones(t: dict, *, block_base: int = 0) -> list[TableZone]:
    right = max(c["x_offset"] + c["width"] for c in t["columns"])
    zones = []
    for bi, blk in enumerate(t["blocks"]):
        ox, oy = blk["origin"]["x"], blk["origin"]["y"]
        zones.append(TableZone(
            table_id=t["table_id"],
            x_min=ox,
            x_max=ox + right,
            bottom=oy + t["row_pitch"] * (blk["rows"] - 1) + t["row_height"],
            block_idx=block_base + bi,
        ))
    return zones


def _rect_area(r: Rect) -> int:
    return r.w * r.h


def _overlap_area(ra: Rect, rb: Rect) -> int:
    ox = max(0, min(ra.x + ra.w, rb.x + rb.w) - max(ra.x, rb.x))
    oy = max(0, min(ra.y + ra.h, rb.y + rb.h) - max(ra.y, rb.y))
    return ox * oy


def hole_bbox(cell: CellSpec) -> Rect | None:
    """欄の受け皿群の外接矩形（BBox）。単一領域なら None（穴は空・U-07 §5.1）。

    実際の「穴」は BBox から受け皿の和集合を引いた領域だが、割付での判定は
    first-hit の順序（領域→参照先→穴）で保証するため、ここでは BBox の矩形
    だけを持てば十分（§5.1・§14 不変条件7）。

    mapping._bucket_cells と _hole_overlap_warnings（W-4・issue #66 第2弾）の
    両方が使う共有ロジック（2026-09-01 に mapping.py から移設・単一の正）。
    """
    rects = cell.all_rects()
    if len(rects) < 2:
        return None
    x0 = min(r.x for r in rects)
    y0 = min(r.y for r in rects)
    x1 = max(r.x + r.w for r in rects)
    y1 = max(r.y + r.h for r in rects)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def _exclusion_overlap_warnings(faces: list[Face], cells: list[CellSpec]) -> list[str]:
    """U-09（H-6）: 除外領域と受け皿の重なりを警告として集める（設計 §7）。

    拒否はしない——出荷テンプレートに意図的な重なりが実在する（印字の一部を
    欄の内側から除く構成）ため、拒否にすると出荷テンプレート自身が読めなく
    なる（§7.1）。W-1（情報）は「重なりがある」だけで発火し、W-2（強い警告）は
    ①受け皿が完全に覆われる ②参照先が被覆される ③参照先を持つ欄の主枠が
    被覆される、のいずれかで発火する。
    """
    warnings: list[str] = []
    exclusions_by_face = {f.face_id: f.exclusions for f in faces}
    # 欄名・面IDの代わりにログへ残す匿名識別子（Q-S1・FR-F50・2026-09-02）:
    # cell_idx は cells（load_template が Template.cells へそのまま渡す並び
    # と同一・呼び出し元 load_template を参照）内の0始まり序数、face_idx は
    # faces（Template.faces と同一の並び）内の0始まり序数。GUI 向けの
    # warnings（下の f-string）は欄名を含めたまま返す（stdout の JSON Lines
    # は秘匿対象外・§0.6）
    face_idx_by_id = {f.face_id: i for i, f in enumerate(faces)}
    for idx, c in enumerate(cells):
        exclusions = exclusions_by_face.get(c.face_id, ())
        if not exclusions:
            continue
        face_idx = face_idx_by_id[c.face_id]
        # issue #66 段2（トワ・ぼたん S-8）: 対象の欄が output: false なら、
        # W-1/W-2 の全文言に印を付ける。無改変テンプレート（全欄 output=True）
        # では tag は常に空文字列なので、既存の件数固定テストは影響を受けない
        tag = "（出力対象外）" if not c.output else ""
        receptors: list[tuple[str, Rect]] = [("欄", c.rect)]
        receptors += [("欄の追加領域", r) for r in c.extra_rects]
        if c.fallback_rect is not None:
            receptors.append(("参照先の枠", c.fallback_rect))
        for label, r in receptors:
            area = _rect_area(r)
            if area == 0:
                continue
            covered = min(sum(_overlap_area(r, e) for e in exclusions), area)
            if covered <= 0:
                continue
            ratio = covered / area
            warnings.append(
                f"[W-1] {c.field_id} の{label}が除外領域と重なっている"
                f"（被覆率 約{ratio * 100:.1f}%）{tag}")
            log.warn("exclusion_overlap_w1", cell_idx=idx, face_idx=face_idx)
            if covered >= area:
                warnings.append(
                    f"[W-2] {c.field_id} の{label}が除外領域に完全に覆われている"
                    f"（恒久的に空になる）{tag}")
                log.warn("exclusion_overlap_w2_full", cell_idx=idx, face_idx=face_idx)
            if label == "参照先の枠":
                warnings.append(
                    f"[W-2] {c.field_id} の参照先が除外領域と重なっている"
                    f"（参照先が機能しない可能性がある）{tag}")
                log.warn("exclusion_overlap_w2_fallback", cell_idx=idx, face_idx=face_idx)
            elif label == "欄" and c.fallback_rect is not None:
                warnings.append(
                    f"[W-2] {c.field_id} の主枠が除外領域と重なっている"
                    f"（『主が空』が構造的に成立しやすくなり、参照先が常時採用されうる）{tag}")
                log.warn("exclusion_overlap_w2_primary", cell_idx=idx, face_idx=face_idx)
    return warnings


# 受け皿間の死角検出（W-3・#61 L-4・2026-08-31）。出荷テンプレートの実測で、
# 隣接する2つの受け皿（欄の主/追加領域・参照先の枠）の間にわずかな隙間が
# 残っている箇所が見つかった（例: person_郵便番号1 の右端 x=648 と
# person_住所1 の左端 x=649 の間の1px列）。この隙間に文字が落ちると
# unassigned_other へ消え、どの欄の値にもならない。テンプレート座標そのものは
# 変えない（geometry_hash が変わり全ページ再送信になるため・データ修正は
# 管理者判断）——ここでは見える化のみ行う。拒否はしない（W-1/W-2 と同じ方針）。
#
# 誤検知の氾濫を避けるため:
# - 比較対象は受け皿どうしに限定し、除外領域は含めない。除外領域はページ端の
#   余白マスクなど「受け皿が存在しない領域」を大量に含み、比較すると無関係な
#   欄すべてに警告が出てしまう。除外領域と受け皿の重なりは既存の W-1/W-2 が
#   別途カバーする
# - 同じ欄自身の主/追加領域/参照先どうしは比較しない。L字・コの字の欄
#   （extra_rects）は物理的に離れた矩形を意図的に1つの欄として束ねる機能で、
#   その間に隙間があるのは design 上ふつう——ここを警告すると当のL字機能が
#   使いものにならなくなる
# - 「同じ y 帯で隣接する」ペアだけを見る。隣接の定義: 2つの受け皿の y 範囲が
#   重なり（同じ帯にある）、一方が他方の右にあり（x が重ならない）、かつ
#   その間（重なる y 帯の中）に割り込む第三の受け皿が無いこと。この定義に
#   より、離れた無関係な欄どうしを誤って「隣接」と判定することはない
#   （面全体の総当たりではなく、実際に隣り合うペアだけを警告する）
GAP_MIN_PX = 1  # 0px（接触）は死角ではない。1px 以上を隙間とみなす


def _adjacent_gap_warnings(faces: list[Face], cells: list[CellSpec]) -> list[str]:
    # issue #66 段2（FR-1.2・トワ・ぼたん S-8）: 隙間の当事者どちらかが
    # output: false なら「（出力対象外）」を付す（欄単位の属性なので field_id
    # で引く。参照先の枠・追加領域も同じ欄の output に従う）
    output_by_id = {c.field_id: c.output for c in cells}
    # 欄名・面IDの代わりにログへ残す匿名識別子（Q-S1・FR-F50・2026-09-02）。
    # 以前は face_id・field_a・field_b がいずれも白リスト外で、adjacent_gap_w3
    # はイベント名しか記録されていなかった（診断が機能していなかった実害・
    # 08_frame_detection_design.md §1.1）。今回 face_idx・cell_a・cell_b の
    # 匿名識別子を新設し、診断を実際に機能させる
    idx_by_field_id = {c.field_id: i for i, c in enumerate(cells)}
    face_idx_by_id = {f.face_id: i for i, f in enumerate(faces)}
    by_face: dict[str, list[tuple[str, str, Rect]]] = {}
    for c in cells:
        for r in c.all_rects():
            by_face.setdefault(c.face_id, []).append((c.field_id, "欄", r))
        if c.fallback_rect is not None:
            by_face.setdefault(c.face_id, []).append(
                (c.field_id, "参照先の枠", c.fallback_rect))

    warnings: list[str] = []
    for face_id, receptors in by_face.items():
        n = len(receptors)
        for i in range(n):
            id_a, label_a, ra = receptors[i]
            a_right = ra.x + ra.w
            for j in range(n):
                if i == j:
                    continue
                id_b, label_b, rb = receptors[j]
                if id_a == id_b:
                    continue  # 同じ欄の受け皿どうしは対象外（L字・コの字の設計上の隙間）
                if rb.x < a_right:
                    continue  # 右側にある受け皿だけを見る（左方向は逆側から検出される）
                band_lo, band_hi = max(ra.y, rb.y), min(ra.y + ra.h, rb.y + rb.h)
                if band_hi - band_lo <= 0:
                    continue  # 同じ y 帯でない
                gap = rb.x - a_right
                if gap < GAP_MIN_PX:
                    continue
                blocked = any(
                    rc.x < rb.x and rc.x + rc.w > a_right
                    and min(rc.y + rc.h, band_hi) - max(rc.y, band_lo) > 0
                    for k, (_id_c, _label_c, rc) in enumerate(receptors)
                    if k != i and k != j)
                if blocked:
                    continue  # 間に別の受け皿が挟まっている＝隣接ではない
                # 対象外欄を1つ作った場合に印が付く（AC-1.5）: どちらか一方が
                # output: false であれば付す（OR）。「片方は出力に残るので
                # まだ実害がある」ケースを黙って対象外扱いにしないため、
                # 両方が output: false のときだけ付す（AND）ではなくこちらを選ぶ
                tag = ("（出力対象外）"
                      if not output_by_id.get(id_a, True)
                      or not output_by_id.get(id_b, True) else "")
                warnings.append(
                    f"[W-3] {id_a}（{label_a}）と {id_b}（{label_b}）の間に"
                    f"{gap}px の隙間がある（面 '{face_id}'）。この隙間に書かれた"
                    f"文字はどの欄にも入らず読み取られない{tag}")
                log.warn("adjacent_gap_w3", face_idx=face_idx_by_id[face_id],
                         cell_a=idx_by_field_id[id_a],
                         cell_b=idx_by_field_id[id_b], gap_px=gap)
    return warnings


# 穴どうしの重なり検出（W-4・issue #66 第2弾・05 F-12・ぼたん Phase 2 レビュー B
# 経路B・2026-09-01）。mapping の空間インデックス（_bucket_cells）は
# 「領域→参照先→穴」の3層 first-hit で、層をまたぐ優先順位は配列順と無関係
# だが、**穴（extra_rects を持つ単発欄の切り抜き穴・hole_bbox）どうしの重なり
# だけは load_template の欄矩形の重なり拒否（issue #24）の母集団に入って
# おらず、配列順依存が残る**——2つの穴の BBox が重なるバケツに落ちた symbol
# は、_bucket_cells が cells の定義順で targets へ積んだ順（=first-hit）で
# どちらの穴に割り付くかが決まる。第2弾（列の並べ替え・配列順変更）を許すと、
# 並べ替えだけで割付結果が黙って変わりうる。拒否はしない——現行出荷テンプレは
# 非発火（実測: 穴どうしの y 帯が重ならない）であり、拒否にする実害の裏付けが
# 無い。切り抜き（extra_rects）の増加で将来発生しうる事象を、W-1/W-2/W-3 と
# 同じ「見える化のみ」方針で伝える。
def _hole_overlap_warnings(faces: list[Face], cells: list[CellSpec]) -> list[str]:
    # 欄名・面IDの代わりにログへ残す匿名識別子（Q-S1・FR-F50・2026-09-02。
    # 以前は face_id・field_a・field_b が白リスト外で、hole_overlap_w4 は
    # イベント名しか記録されていなかった実害を修正する・§1.1）
    idx_by_field_id = {c.field_id: i for i, c in enumerate(cells)}
    face_idx_by_id = {f.face_id: i for i, f in enumerate(faces)}
    warnings: list[str] = []
    by_face: dict[str, list[tuple[str, Rect]]] = {}
    for c in cells:
        bbox = hole_bbox(c)
        if bbox is not None:
            by_face.setdefault(c.face_id, []).append((c.field_id, bbox))
    for face_id, holes in by_face.items():
        n = len(holes)
        for i in range(n):
            id_a, ra = holes[i]
            for j in range(i + 1, n):
                id_b, rb = holes[j]
                if _overlap_area(ra, rb) <= 0:
                    continue
                warnings.append(
                    f"[W-4] {id_a} と {id_b} の穴（切り抜き）が重なっている"
                    f"（面 '{face_id}'）。重なった部分に落ちた文字の割付先は"
                    "テンプレートの配列順（定義順）で決まる——欄・列の並べ替えで"
                    "割付結果が変わりうる")
                log.warn("hole_overlap_w4", face_idx=face_idx_by_id[face_id],
                         cell_a=idx_by_field_id[id_a],
                         cell_b=idx_by_field_id[id_b])
    return warnings


# テンプレート JSON Schema のプロセス内キャッシュ（issue #72 (t)・M-3・
# 2026-09-02 マリン指摘）。match-templates が候補テンプレートごとに
# load_template を呼ぶため、毎回スキーマファイルを読み直すと（実測 83ms/件）
# NFR-F09（合計3.0秒）を圧迫する。キーはファイルパス＋mtime——スキーマ
# ファイル自体が更新されたら（開発中の編集等）取り直す。プロセス内で
# スキーマファイルは実質1つなので、キー不一致時は総入れ替えでよい
# 2026-09-03 追記: キャッシュするのはファイルの中身ではなく**バリデータ実体**
# にした。jsonschema.validate() は呼び出しのたびに check_schema()（メタスキーマ
# 検証）をやり直しており、これが支配項になっていた（実測: 出荷テンプレートを
# 7 回読む所要が 1377ms → 620ms・3 試行の最小値・2026-09-03）。バリデータを
# 作るときに 1 回だけ check_schema を通し、以降は iter_errors だけを呼ぶ。
# エラーの選び方（best_match）も送出する例外も jsonschema.validate と同じ手順を
# 踏むので、メッセージの形は変わらない。1 つのバリデータを複数のインスタンスへ
# 使い回すのは jsonschema が公式に案内している使い方（iter_errors は
# バリデータを変更しない）。
_validator_cache: dict[tuple[str, float], object] = {}


def _schema_validator():
    """テンプレート JSON Schema のバリデータ（プロセス内キャッシュ）。

    キーはファイルパス＋mtime——スキーマファイル自体が更新されたら
    （開発中の編集等）作り直す。プロセス内でスキーマファイルは実質1つなので、
    キー不一致時は総入れ替えでよい。
    """
    p = template_schema_path()
    mtime = p.stat().st_mtime
    key = (str(p), mtime)
    cached = _validator_cache.get(key)
    if cached is not None:
        return cached
    schema = json.loads(p.read_text(encoding="utf-8"))
    cls = jsonschema_validators.validator_for(schema)
    cls.check_schema(schema)   # スキーマ自体の妥当性は作るときに 1 回だけ見る
    validator = cls(schema)
    _validator_cache.clear()
    _validator_cache[key] = validator
    return validator


def load_template(path: str | Path) -> Template:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    e = jsonschema_exceptions.best_match(_schema_validator().iter_errors(raw))
    if e is not None:
        raise TemplateError(f"スキーマ検証エラー: {e.message}（場所: {'/'.join(map(str, e.absolute_path))}）") from e

    # --- v1 受け入れ範囲（設計 §4.2・D-20）。範囲外は拒否し黙って続行しない ---
    if raw["schema_version"] != SCHEMA_VERSION:
        raise TemplateError(f"未知の schema_version={raw['schema_version']}（受理は {SCHEMA_VERSION} のみ）")
    if raw["record"]["pages"] != 1:
        raise TemplateError(f"v1 は record.pages=1 のみ受理（指定: {raw['record']['pages']}）")

    faces: list[Face] = []
    cells: list[CellSpec] = []
    for f in raw["faces"]:
        fid = f["face_id"]
        if fid not in V1_FACE_IDS:
            raise TemplateError(f"v1 の face_id は front/back のみ受理（指定: {fid}）")
        if f["source"]["page_offset"] != 0:
            raise TemplateError(f"v1 は page_offset=0 のみ受理（face {fid}: {f['source']['page_offset']}）")
        zones: list[TableZone] = []
        geoms: list[TableGeom] = []
        for fld in f.get("fields", []):
            marks = tuple(
                ChoiceMark(m["value"], _rect(m["rect"])) for m in fld.get("choice_marks", [])
            )
            if fld.get("fallback_rect") is not None and fld["kind"] != "text":
                raise TemplateError(
                    f"参照先（fallback_rect）は文字欄（text）のみ指定できる"
                    f"（{fld['field_id']} は {fld['kind']}）。丸印は帯スコアで"
                    "判定するため、別枠への参照は定義できない")
            if fld.get("extra_rects") and fld["kind"] != "text":
                raise TemplateError(
                    f"追加の領域（extra_rects）は文字欄（text）のみ指定できる"
                    f"（{fld['field_id']} は {fld['kind']}）。丸印の帯スコアは"
                    "単一矩形が前提のため、複数領域にはできない")
            cells.append(
                CellSpec(
                    field_id=fld["field_id"],
                    face_id=fid,
                    rect=_rect(fld["rect"]),
                    kind=fld["kind"],
                    choice_marks=marks,
                    # 単発欄（fields）に subfields は存在しない——schema の field
                    # 定義（schema/template.schema.json）は additionalProperties:
                    # false で subfields プロパティを許可しておらず、スキーマ検証
                    # （load_template 冒頭）を通過した時点で fld に subfields は
                    # 絶対に無い。以前はここで fld.get("subfields") を読んでいたが
                    # 到達不能な分岐だった（#61 L-1）。table 列（_expand_table）の
                    # subfields は物理と出力の粒度が違うときの正規の装置で、これとは別物
                    subfields=(),
                    normalize=(fld.get("normalize")
                               if fld["kind"] == "text" else None),
                    fallback_rect=(_rect(fld["fallback_rect"])
                                   if fld.get("fallback_rect") is not None else None),
                    extra_rects=tuple(_rect(r)
                                      for r in fld.get("extra_rects", ())),
                    output=fld.get("output", True),
                )
            )
        for t in f.get("tables", []):
            if t["row_height"] > t["row_pitch"]:
                raise TemplateError(
                    f"row_height({t['row_height']}) > row_pitch({t['row_pitch']})：行が重なる（table {t['table_id']}）"
                )
            # ブロックの面内通し序数は、この時点の geoms の長さがそのまま
            # 先頭になる（issue #75 (f)・08 §6 判断1）。**採番の正本はこの
            # 3行の順序**——cells／zones が参照する序数と geoms の並びが
            # 一致することを、派生表ではなく代入順で保証する
            blk_base = len(geoms)
            cells.extend(_expand_table(fid, t, block_base=blk_base))
            zones.extend(_table_zones(t, block_base=blk_base))
            geoms.extend(_table_geoms(t))
        # 平行移動推定は罫線をアンカーにする。表を持たない面は、単発欄の枠線を
        # 代わりのアンカーにする（issue #86）——表のある面はここに入らず従来
        # どおり table_geoms だけを使う（不変条件 A）。どちらのアンカーも作れ
        # ない面は毎ページ静かに失敗するため、読み込み時に1回だけ大声で落とす
        fgeoms: list[TableGeom] = []
        if not geoms:
            fgeoms = _field_geoms(
                [c for c in cells if c.face_id == fid and c.table_id is None],
                raw["render_dpi"])
            # 期待線が片軸でも 0 本だと estimate_shift の need_x / need_y を
            # 構造上満たせず、その面は必ず few_lines で失敗する。読める見込みが
            # 無い設定なので受理しない（設計 §4.2「読めないなら止まる方が安い」）
            if not any(g.h_lines for g in fgeoms) or not any(g.v_lines for g in fgeoms):
                span = max(1, round(ANCHOR_MIN_SPAN_PX * raw["render_dpi"] / BASE_DPI))
                raise TemplateError(
                    f"face '{fid}' に位置合わせのアンカーが無い。表（tables）を"
                    f"1つ以上定義するか、幅・高さが {span}px 以上の欄を1つ以上置く"
                    "（欄の枠線を位置合わせの基準に使う）")
        faces.append(
            Face(
                face_id=fid,
                page_offset=f["source"]["page_offset"],
                source_rect=_rect(f["source"]["rect"]),
                exclusions=tuple(_rect(e["rect"]) for e in f.get("exclusions", [])),
                table_zones=tuple(zones),
                table_geoms=tuple(geoms),
                shift_limits=(_shift_limits(geoms) if geoms
                              else _anchor_search_limits(raw["render_dpi"])),
                field_geoms=tuple(fgeoms),
            )
        )

    if len({f.face_id for f in faces}) != len(faces):
        raise TemplateError("face_id が重複している")
    ids = [c.field_id for c in cells]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise TemplateError(f"field_id が重複している: {dup[:5]}")

    # Q-MA（らでん逆張り採用・2026-09-02: 警告ではなく拒否へ格上げ）。
    # era の帯定数（era.BAND_PAD/BAND_PAD_IN）は300dpiの実測較正値で、向きまで
    # 較正済み（D-31）。render_dpi != BASE_DPI へ機械的にスケールすると元号
    # 丸印の判定が狂う——警告に留めると気づかず出荷して誤判定が実害化しうる
    # ため、読み込み時点で拒否する（06_second_form_findings.md §7「意図的に
    # 除外」・再較正とセットでの対応が前提）
    if raw["render_dpi"] != BASE_DPI and any(c.kind == "choice" for c in cells):
        raise TemplateError(
            f"render_dpi={raw['render_dpi']} のテンプレートで選択式（choice）欄は"
            "使えない。元号丸印の帯定数（era.BAND_PAD/BAND_PAD_IN）は300dpiの"
            "実測較正値で、再較正なしに他のdpiへ適用すると判定が狂うため"
            "（docs/design/chouhyo-ocr/06_second_form_findings.md §7）")

    # 選択肢の値が重複していると era.decide の候補が1つに潰れ、共通フロア減算で
    # 自分自身のスコアが消えて**丸印があっても永久に未選択（〓）**になる
    # （issue #31・実測: decide({'昭': 0.3}) → 未選択）。スキーマの minItems:2 は
    # 重複を弾けないのでここで検証する
    for c in cells:
        vals = [m.value for m in c.choice_marks]
        if len(set(vals)) != len(vals):
            raise TemplateError(
                f"選択肢の値が重複している（{c.field_id}: {vals}）。"
                "同じ値が2つ以上あると、その欄は丸印があっても常に〓になる")

    # 欄・マークが面の範囲内にあるかを検証する（レビュー M-20）。範囲外の欄は
    # symbol が来ず永久に〓になり、原因表示も無い。era も面外を含む矩形で
    # area を過大評価してスコアが不当に下がる
    face_size = {f.face_id: (f.source_rect.w, f.source_rect.h) for f in faces}
    for c in cells:
        fw, fh = face_size[c.face_id]
        targets = [("欄", c.rect)] + [(f"選択肢 '{m.value}'", m.rect)
                                       for m in c.choice_marks]
        if c.fallback_rect is not None:
            targets.append(("参照先の枠", c.fallback_rect))
        for r in c.extra_rects:
            targets.append(("欄の追加領域", r))
        for label, r in targets:
            if r.x < 0 or r.y < 0 or r.x + r.w > fw or r.y + r.h > fh:
                raise TemplateError(
                    f"{label}が面 '{c.face_id}'（{fw}×{fh}）の外にはみ出している"
                    f"（{c.field_id}: x={r.x} y={r.y} w={r.w} h={r.h}）。"
                    "範囲外の欄は文字が来ず常に〓になる")

    # 選択肢マークは欄の矩形の内側にあること（issue #48）。編集画面で欄を移動・
    # リサイズしてもマークが元の座標に取り残されることがあり、その状態で保存すると
    # era.decide が欄と無関係な位置のインクで元号を決める——〓ではなく**誤った元号**を
    # 出力する唯一の経路になる。GUI 側の追従修正だけでは手書き JSON・過去に壊れた
    # 状態で保存されたテンプレート・別経路で作られたテンプレートを止められないため、
    # 読み込み時に落とす（run/render/remap はいずれも pipeline._load 経由でここを通る）。
    #
    # 許容 CHOICE_MARK_MARGIN_PX=4px の根拠（テンプレートを書き換えずに済ませるための実測）:
    # 出荷テンプレート templates/chouhyo-v1.json は厳密な内包を満たしていない。実測の
    # 最大はみ出しは右1px（family_*_生年月日_元号: 欄 x_offset 671+50=721 に対しマーク
    # 686+36=722）と下2px（person_生年月日_元号: 欄 y 135+148=283 に対しマーク「令」
    # 238+47=285）。印字文字に合わせて枠を引いたときの丸めで、読み取りへの実害はない。
    # 一方 #48 の取り残しは欄の移動量そのもの（編集画面のドラッグは十〜数十px）。
    # マークの最小寸法は32px・縦の並び間隔も32〜38px なので、4px は「隣のマークへ
    # 食い込む距離」の1/8以下に収まりつつ、実測の丸め2pxより上にある。
    # 境界: はみ出し0（欄の辺にちょうど接する）は内側として扱う。矩形を [x, x+w) の
    # 半開区間で見る流儀（上の面内判定・下の重なり判定と同じ）で、x+w はマークに
    # 含まれない列だから。
    #
    # この検査で拾えない範囲（限界の明示・2026-08-31 実測）: 見ているのは「マークが欄の
    # 中にあるか」だけなので、マークと欄の間に元々ある内側余白の分は、欄を動かして
    # マークを据え置いても検出できない。支配項は許容4pxではなく**その固有余白**で、
    # family_*_生年月日_元号 は欄 x=1060 に対しマーク x=1075（左に15px）、
    # person_生年月日_元号 は左に8px ある。欄だけを動かしたとき検出されない最大移動量は
    # family: 右19 / 左3 / 下6 / 上5px、person: 右12 / 左9 / 下10 / 上2px
    # （tests/test_template.py::test_choice_mark_blind_spot_envelope で固定）。
    # したがって許容を 4→0 に締めても窓は family 右 19→15px に縮むだけで塞がらず、
    # 出荷テンプレの丸め1〜2px が読めなくなる副作用の方が大きい。塞ぐなら許容値ではなく
    # 「マーク群と欄の各辺の距離が編集の前後で保たれているか」を見る別設計が要る（別issue）。
    # 現状これで許容しているのは、元号の当落を左右する縦方向の取りこぼしが最大10px＝
    # マーク高32〜47px・並び間隔32〜50px の1/3未満に収まっているため。
    # CHOICE_MARK_MARGIN_PX は BASE_DPI=300 較正の px 定数（汎用化 A-3・S-3）。
    # dpi/BASE_DPI の比でスケールしてから使う。300dpi（scale=1.0）のときは
    # round(4*1.0)=4 で従来と完全に同じ値になる
    margin_px = max(0, round(CHOICE_MARK_MARGIN_PX * (raw["render_dpi"] / BASE_DPI)))
    for c in cells:
        r = c.rect
        for m in c.choice_marks:
            q = m.rect
            over = max(r.x - q.x, r.y - q.y,
                       q.x + q.w - (r.x + r.w), q.y + q.h - (r.y + r.h))
            if over > margin_px:
                raise TemplateError(
                    f"選択肢の枠が欄からはみ出している: {c.field_id} のマーク"
                    f"『{m.value}』が欄の矩形の外にある"
                    f"（欄 x={r.x} y={r.y} w={r.w} h={r.h} / "
                    f"マーク x={q.x} y={q.y} w={q.w} h={q.h}・"
                    f"はみ出し {over}px・許容 {margin_px}px）。"
                    "テンプレート編集画面で欄を移動した際に選択肢の枠が"
                    "追従していない可能性がある")

    # 同一面のセル矩形の重なりを拒否する（issue #24）。mapping は定義順の
    # first-hit で解決するため、重なり帯へ落ちた symbol の行き先が
    # 「テンプレートの記述順」という見えない要素で決まる。実サンプルでは
    # 顕在化しなかったが、記入が欄をわずかにはみ出す実データで列ズレになる
    # 参照先も割付の受け皿（symbol の行き先）なので、重なり検査の母集団に
    # 含める。主と自分の参照先の重なりは「空のとき参照」の意味が壊れるため
    # 先に専用メッセージで拒否する
    def _overlap(ra: Rect, rb: Rect) -> bool:
        return (ra.x < rb.x + rb.w and rb.x < ra.x + ra.w
                and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h)

    for c in cells:
        if c.fallback_rect is None:
            continue
        # 参照先は「主（全領域）が空のときに読む別の場所」。どれかの領域と
        # 重なると、その部分の文字は領域側に入って「空」判定が成立しなくなる
        for r in c.all_rects():
            if _overlap(r, c.fallback_rect):
                raise TemplateError(
                    f"参照先の枠が主の枠と重なっている（{c.field_id}）。"
                    "参照先は「主が空のときに読む別の場所」なので、離れた位置に置く")

    by_face: dict[str, list] = {}
    for c in cells:
        # 同じ欄の領域どうしの重なりは無害（同じ受け皿）なので検査しない
        #（下のループが field_id 一致をスキップする）
        for r in c.all_rects():
            by_face.setdefault(c.face_id, []).append((c, r, "欄"))
        if c.fallback_rect is not None:
            by_face.setdefault(c.face_id, []).append((c, c.fallback_rect, "参照先の枠"))
    for fid, cs in by_face.items():
        for i, (a, ra, la) in enumerate(cs):
            for b, rb, lb in cs[i + 1:]:
                if a.field_id == b.field_id:
                    continue  # 主と自分の参照先は上で検査済み
                if _overlap(ra, rb):
                    # 主同士は従来の文言を保つ（#24 当時からのメッセージ）。
                    # 参照先が絡むときは「なぜダメか・どうすればよいか」まで言う
                    # ——重なった場所の文字は主の欄が常に取るため、重なった
                    # 参照先は設定しても1文字も受け取れない（ユーザー報告
                    # 2026-08-31: 住所の上に郵便番号の参照先を置いて詰まった）
                    if la == lb == "欄":
                        raise TemplateError(
                            f"欄の矩形が重なっている（{fid}: {a.field_id} と "
                            f"{b.field_id}）。重なり部分の文字がどちらへ入るかが"
                            "定義順で決まってしまうため、枠を分ける")
                    if la == lb == "参照先の枠":
                        # 両方が参照先どうしの重なり（#61 L-3）。以前は下の
                        # else 分岐へ落ち、実際には重なっていない相手の「欄」を
                        # 指す文言（「{b} の欄と重なっている」）を出していた——
                        # 案内どおり欄を動かしても直らない。実際に重なっている
                        # 対象（互いの参照先）を指す専用の文言にする
                        raise TemplateError(
                            f"{a.field_id} と {b.field_id} の参照先の枠どうしが"
                            f"重なっている（{fid}）。重なった場所の文字は定義順で"
                            "決まるどちらか一方の参照先にしか入らず、他方の参照先は"
                            "機能しない。参照先はどの欄・参照先とも重ならない場所に置く")
                    fb_owner, other = (a, b) if la == "参照先の枠" else (b, a)
                    raise TemplateError(
                        f"{fb_owner.field_id} の参照先の枠が {other.field_id} の欄と"
                        f"重なっている（{fid}）。重なった場所の文字は欄の側"
                        f"（{other.field_id}）に入り、参照先には届かないため、"
                        "この参照先は機能しない。参照先はどの欄とも重ならない"
                        "場所に置く。その記入位置が他の欄の中にしか無い場合は"
                        "参照先では表現できない——読取値は元の欄に入るので、"
                        "目視確認で移す運用にする")

    # 面の切り出し矩形が重なると、同じ記入が両面のセルへ割り付いて二重転記に
    # なる（issue #32）。エディタの「表裏の境界」の誤操作で作れてしまう
    for i, a in enumerate(faces):
        for b in faces[i + 1:]:
            ra, rb = a.source_rect, b.source_rect
            if (ra.x < rb.x + rb.w and rb.x < ra.x + ra.w
                    and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h):
                raise TemplateError(
                    f"面 '{a.face_id}' と '{b.face_id}' の切り出し範囲が重なっている。"
                    "同じ記入が両面へ二重に転記されるため、表裏の境界を見直す")

    return Template(
        template_id=raw["template_id"],
        render_dpi=raw["render_dpi"],
        image_size=(raw["image"]["width"], raw["image"]["height"]),
        record_pages=raw["record"]["pages"],
        faces=tuple(faces),
        cells=tuple(cells),
        warnings=tuple(_exclusion_overlap_warnings(faces, cells)
                       + _adjacent_gap_warnings(faces, cells)
                       + _hole_overlap_warnings(faces, cells)
                       + _anchor_shortage_warnings(faces)
                       + _anchor_outer_edge_warnings(faces)
                       + _small_table_snap_warnings(faces, raw)),
    )
