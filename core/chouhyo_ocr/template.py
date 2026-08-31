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

from . import logging_safe as log
from .paths import template_schema_path

SCHEMA_VERSION = 1
V1_FACE_IDS = {"front", "back"}
# 選択肢マークが欄の矩形からはみ出せる上限（px）。値の根拠は load_template の
# 検証箇所（issue #48）のコメントに実測つきで書いてある
CHOICE_MARK_MARGIN_PX = 4


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
    # 除外領域×受け皿の重なり警告（U-09・H-6・2026-08-31）。拒否はしない——
    # 出荷テンプレートに意図的な重なりが実在するため（§7.1）。cli.py の verify が
    # そのまま warnings: [string] として GUI 側へ渡す契約（変更不可）
    warnings: tuple[str, ...] = field(default=())

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


def _rect_area(r: Rect) -> int:
    return r.w * r.h


def _overlap_area(ra: Rect, rb: Rect) -> int:
    ox = max(0, min(ra.x + ra.w, rb.x + rb.w) - max(ra.x, rb.x))
    oy = max(0, min(ra.y + ra.h, rb.y + rb.h) - max(ra.y, rb.y))
    return ox * oy


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
    for c in cells:
        exclusions = exclusions_by_face.get(c.face_id, ())
        if not exclusions:
            continue
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
                f"（被覆率 約{ratio * 100:.1f}%）")
            log.warn("exclusion_overlap_w1", field_id=c.field_id)
            if covered >= area:
                warnings.append(
                    f"[W-2] {c.field_id} の{label}が除外領域に完全に覆われている"
                    "（恒久的に空になる）")
                log.warn("exclusion_overlap_w2_full", field_id=c.field_id)
            if label == "参照先の枠":
                warnings.append(
                    f"[W-2] {c.field_id} の参照先が除外領域と重なっている"
                    "（参照先が機能しない可能性がある）")
                log.warn("exclusion_overlap_w2_fallback", field_id=c.field_id)
            elif label == "欄" and c.fallback_rect is not None:
                warnings.append(
                    f"[W-2] {c.field_id} の主枠が除外領域と重なっている"
                    "（『主が空』が構造的に成立しやすくなり、参照先が常時採用されうる）")
                log.warn("exclusion_overlap_w2_primary", field_id=c.field_id)
    return warnings


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
                    subfields=(tuple(fld.get("subfields", ()))
                               if fld["kind"] == "text" else ()),
                    normalize=(fld.get("normalize")
                               if fld["kind"] == "text" and not fld.get("subfields")
                               else None),
                    fallback_rect=(_rect(fld["fallback_rect"])
                                   if fld.get("fallback_rect") is not None else None),
                    extra_rects=tuple(_rect(r)
                                      for r in fld.get("extra_rects", ())),
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
    for c in cells:
        r = c.rect
        for m in c.choice_marks:
            q = m.rect
            over = max(r.x - q.x, r.y - q.y,
                       q.x + q.w - (r.x + r.w), q.y + q.h - (r.y + r.h))
            if over > CHOICE_MARK_MARGIN_PX:
                raise TemplateError(
                    f"選択肢の枠が欄からはみ出している: {c.field_id} のマーク"
                    f"『{m.value}』が欄の矩形の外にある"
                    f"（欄 x={r.x} y={r.y} w={r.w} h={r.h} / "
                    f"マーク x={q.x} y={q.y} w={q.w} h={q.h}・"
                    f"はみ出し {over}px・許容 {CHOICE_MARK_MARGIN_PX}px）。"
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
        warnings=tuple(_exclusion_overlap_warnings(faces, cells)),
    )
