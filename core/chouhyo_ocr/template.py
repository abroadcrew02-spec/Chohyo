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
class Face:
    face_id: str
    page_offset: int
    source_rect: Rect  # 入力ページ座標
    exclusions: tuple[Rect, ...] = ()
    table_zones: tuple[TableZone, ...] = ()


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
        faces.append(
            Face(
                face_id=fid,
                page_offset=f["source"]["page_offset"],
                source_rect=_rect(f["source"]["rect"]),
                exclusions=tuple(_rect(e["rect"]) for e in f.get("exclusions", [])),
                table_zones=tuple(zones),
            )
        )

    if len({f.face_id for f in faces}) != len(faces):
        raise TemplateError("face_id が重複している")
    ids = [c.field_id for c in cells]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise TemplateError(f"field_id が重複している: {dup[:5]}")

    return Template(
        template_id=raw["template_id"],
        render_dpi=raw["render_dpi"],
        image_size=(raw["image"]["width"], raw["image"]["height"]),
        record_pages=raw["record"]["pages"],
        faces=tuple(faces),
        cells=tuple(cells),
    )
