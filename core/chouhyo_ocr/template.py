"""テンプレートの読み込み・検証・格子展開（設計 §4.2）。

読み込み時に JSON Schema（schema/template.schema.json・コアとエディタの共有正本）で
検証し、続けて v1 の受け入れ範囲を機械的に確認する。範囲外は黙って続行せず
TemplateError で拒否する（設計 §4.2「読めないなら止まる方が安い」）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import jsonschema

from .paths import template_schema_path

SCHEMA_VERSION = 1
V1_FACE_IDS = {"front", "back"}


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


@dataclass(frozen=True)
class TableGeom:
    """テーブルの罫線期待位置（面ローカル）。平行移動推定のアンカー（D-25）。"""
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    h_lines: tuple[int, ...]  # 期待横線の y
    v_lines: tuple[int, ...]  # 期待縦線の x


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


@dataclass(frozen=True)
class Template:
    template_id: str
    render_dpi: int
    image_size: tuple[int, int]  # (width, height)
    record_pages: int
    faces: tuple[Face, ...]
    cells: tuple[CellSpec, ...] = field(default=())

    def face(self, face_id: str) -> Face:
        for f in self.faces:
            if f.face_id == face_id:
                return f
        raise KeyError(face_id)


def _rect(d: dict) -> Rect:
    return Rect(d["x"], d["y"], d["w"], d["h"])


def _expand_table(face_id: str, t: dict) -> list[CellSpec]:
    """テーブル定義から格子を展開する（設計 §4.2 展開規則）。

    blocks を定義順に走り、行の通し番号はブロックを跨いで連番。
    """
    cells: list[CellSpec] = []
    row_no = 0
    pitch, height = t["row_pitch"], t["row_height"]
    for blk in t["blocks"]:
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


def _table_zones(t: dict) -> list[TableZone]:
    right = max(c["x_offset"] + c["width"] for c in t["columns"])
    zones = []
    for blk in t["blocks"]:
        ox, oy = blk["origin"]["x"], blk["origin"]["y"]
        zones.append(TableZone(
            table_id=t["table_id"],
            x_min=ox,
            x_max=ox + right,
            bottom=oy + t["row_pitch"] * (blk["rows"] - 1) + t["row_height"],
        ))
    return zones


def load_template(path: str | Path) -> Template:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))

    schema = json.loads(template_schema_path().read_text(encoding="utf-8"))
    try:
        jsonschema.validate(raw, schema)
    except jsonschema.ValidationError as e:
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
            cells.append(
                CellSpec(
                    field_id=fld["field_id"],
                    face_id=fid,
                    rect=_rect(fld["rect"]),
                    kind=fld["kind"],
                    choice_marks=marks,
                    subfields=(tuple(fld.get("subfields", ()))
                               if fld["kind"] == "text" else ()),
                    normalize=(fld.get("normalize")
                               if fld["kind"] == "text" and not fld.get("subfields")
                               else None),
                )
            )
        for t in f.get("tables", []):
            if t["row_height"] > t["row_pitch"]:
                raise TemplateError(
                    f"row_height({t['row_height']}) > row_pitch({t['row_pitch']})：行が重なる（table {t['table_id']}）"
                )
            cells.extend(_expand_table(fid, t))
            zones.extend(_table_zones(t))
            geoms.extend(_table_geoms(t))
        if not geoms:
            # 平行移動推定は罫線をアンカーにする。table の無い面はアンカー不能で
            # 毎ページ静かに失敗するため、読み込み時に1回だけ大声で落とす（D-25）
            raise TemplateError(
                f"face '{fid}' に tables が無い。位置合わせのアンカーとして"
                "各面に1つ以上のテーブル定義が必要（v1 の受け入れ範囲）")
        faces.append(
            Face(
                face_id=fid,
                page_offset=f["source"]["page_offset"],
                source_rect=_rect(f["source"]["rect"]),
                exclusions=tuple(_rect(e["rect"]) for e in f.get("exclusions", [])),
                table_zones=tuple(zones),
                table_geoms=tuple(geoms),
                shift_limits=_shift_limits(geoms),
            )
        )

    if len({f.face_id for f in faces}) != len(faces):
        raise TemplateError("face_id が重複している")
    ids = [c.field_id for c in cells]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise TemplateError(f"field_id が重複している: {dup[:5]}")

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

    # 同一面のセル矩形の重なりを拒否する（issue #24）。mapping は定義順の
    # first-hit で解決するため、重なり帯へ落ちた symbol の行き先が
    # 「テンプレートの記述順」という見えない要素で決まる。実サンプルでは
    # 顕在化しなかったが、記入が欄をわずかにはみ出す実データで列ズレになる
    by_face: dict[str, list] = {}
    for c in cells:
        by_face.setdefault(c.face_id, []).append(c)
    for fid, cs in by_face.items():
        for i, a in enumerate(cs):
            for b in cs[i + 1:]:
                ra, rb = a.rect, b.rect
                if (ra.x < rb.x + rb.w and rb.x < ra.x + ra.w
                        and ra.y < rb.y + rb.h and rb.y < ra.y + ra.h):
                    raise TemplateError(
                        f"欄の矩形が重なっている（{fid}: {a.field_id} と "
                        f"{b.field_id}）。重なり部分の文字がどちらへ入るかが"
                        "定義順で決まってしまうため、枠を分ける")

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
    )
