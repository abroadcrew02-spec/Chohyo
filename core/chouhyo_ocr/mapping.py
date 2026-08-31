"""symbol → セル割付・空行判定・枠外分類（設計 §6.4・§6.5）。

割付は word ではなく **symbol（1文字）単位**。Vision はセルを跨いで数字を
1 word に結合することがあり（S2 ドライランで日付と金額の混線を実測）、
文字ごとに物理位置で振り直せば結合の影響を受けない。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from . import logging_safe as log
from .template import CellSpec, Face


@dataclass(frozen=True)
class Symbol:
    """1文字。座標は文脈による（ページ座標 or 面ローカル）。x/y は中心点。"""
    text: str
    x: float
    y: float
    conf: float

    def __repr__(self) -> str:  # 記入値を repr へ出さない（設計 §8.1・付録 C7）
        return f"<{type(self).__name__} redacted>"



@dataclass(frozen=True)
class CellContent:
    """1物理セルへ割り付いた読取内容。"""
    text: str
    conf_min: float | None  # symbol が無ければ None

    def __repr__(self) -> str:  # 記入値を repr へ出さない（設計 §8.1・付録 C7）
        return f"<{type(self).__name__} redacted>"



@dataclass(frozen=True)
class MappingResult:
    cells: Mapping[str, CellContent]          # field_id → 内容（symbol 有りのセルのみ）
    empty_rows: frozenset[tuple[str, int]]    # (table_id, row_no)
    unassigned_below_table: int               # D-06 の入力
    unassigned_other: int                     # D-15 の入力


def symbols_from_response(resp: dict) -> list[Symbol]:
    """DOCUMENT_TEXT_DETECTION 応答（MessageToDict 形式）→ ページ座標の symbol 列。"""
    out: list[Symbol] = []
    dropped: list[int] = []
    fta = resp.get("fullTextAnnotation") or {}
    for page in fta.get("pages", []):
        for block in page.get("blocks", []):
            for para in block.get("paragraphs", []):
                for word in para.get("words", []):
                    for sym in word.get("symbols", []):
                        # 防御的パース（issue #40）: 応答の部分欠落・切り詰めで
                        # 1 symbol が壊れていても、応答全体を落として run を
                        # 止めない（止めると received のまま再送ループへ入る）。
                        # 捨てた件数は呼び出し側がログへ出す
                        vs = (sym.get("boundingBox") or {}).get("vertices") or []
                        text = sym.get("text")
                        if not vs or not isinstance(text, str):
                            dropped.append(1)
                            continue
                        xs = [v.get("x", 0) for v in vs]
                        ys = [v.get("y", 0) for v in vs]
                        conf = sym.get("confidence", 0.0)
                        if isinstance(conf, bool) or not isinstance(conf, (int, float)):
                            # 型不正の confidence は「信頼度なし」として扱う
                            # （render 側で〓へ倒れる。数値比較で落ちない・#39）
                            conf = 0.0
                        out.append(Symbol(
                            text=text,
                            x=(min(xs) + max(xs)) / 2,
                            y=(min(ys) + max(ys)) / 2,
                            conf=float(conf),
                        ))
    if dropped:
        log.warn("response_symbols_dropped", count=len(dropped))
    return out


def to_face_local(face: Face, symbols: Iterable[Symbol]) -> list[Symbol]:
    """ページ座標の symbol を面ローカルへ変換する（source.rect 内のみ・平行移動）。

    除外領域（要件 §5.2: 抽出対象外）内の symbol はここで捨てる。実送信では
    マスク済み画像を送るため応答に現れないが、マスク前の応答を replay した
    場合も同じ結果になるようにする。
    """
    r = face.source_rect
    out = []
    for s in symbols:
        if not (r.x <= s.x < r.x + r.w and r.y <= s.y < r.y + r.h):
            continue
        lx, ly = s.x - r.x, s.y - r.y
        if any(e.x <= lx < e.x + e.w and e.y <= ly < e.y + e.h
               for e in face.exclusions):
            continue
        out.append(Symbol(s.text, lx, ly, s.conf))
    return out


_LINE_GAP = 30.0  # 行の切れ目とみなす y ギャップ（px・300dpi）※実物で調整


def _cell_text(cell: CellSpec, syms: list[Symbol]) -> str:
    """y → x の順で連結（設計 §6.4）。

    行の分離は固定量子化でなく y ギャップのクラスタリングで行う。
    住所欄のように「郵便番号の行＋住所の行」を1セルで持つ場合、
    固定量子化では行の境界と量子境界がずれて文字順が混ざる（実データで確認）。
    """
    if not syms:
        return ""
    by_y = sorted(syms, key=lambda s: s.y)
    lines: list[list[Symbol]] = [[by_y[0]]]
    for s in by_y[1:]:
        if s.y - lines[-1][-1].y > _LINE_GAP:
            lines.append([s])
        else:
            lines[-1].append(s)
    out = []
    for line in lines:
        out.extend(sorted(line, key=lambda s: s.x))
    return "".join(s.text for s in out)


_BUCKET = 128  # グリッドの一辺（px）。セル高（実測 90〜148px）と同程度


# 参照先の受け皿を per_cell 上で区別する内部キーの接尾辞。field_id は
# スキーマ上 NUL を含まないため衝突しない
_FB = "\x00fallback"


def _bucket_cells(cells: Sequence[CellSpec]) -> dict:
    """セルをグリッドのバケツへ入れる（issue #17 の空間インデックス）。

    1セルが複数バケツにまたがる場合は全てへ入れる。バケツ内の順序は
    元の定義順を保つ（first-hit の契約を変えないため）。参照先の枠は
    独立した受け皿（key=field_id+_FB）として主の後ろに並べる——
    重なりは load_template が拒否するので順序が結果を変えることはない。
    """
    buckets: dict[tuple[int, int], list] = {}
    targets: list[tuple[str, "Rect"]] = []
    for c in cells:
        # 追加領域（L字などの構成片）は主と等価の受け皿。同じ key に集める
        for r in c.all_rects():
            targets.append((c.field_id, r))
    for c in cells:
        if c.fallback_rect is not None:
            targets.append((c.field_id + _FB, c.fallback_rect))
    for i, (key, r) in enumerate(targets):
        for bx in range(r.x // _BUCKET, (r.x + r.w) // _BUCKET + 1):
            for by in range(r.y // _BUCKET, (r.y + r.h) // _BUCKET + 1):
                buckets.setdefault((bx, by), []).append((i, key, r))
    return buckets


def _candidates(buckets: dict, x: float, y: float):
    """座標を含むバケツの受け皿（key, rect）を定義順で返す。"""
    got = buckets.get((int(x) // _BUCKET, int(y) // _BUCKET))
    if not got:
        return ()
    return [(k, r) for _i, k, r in got]


def assign(
    cells: Sequence[CellSpec],
    symbols_by_face: Mapping[str, Sequence[Symbol]],
    faces: Sequence[Face],
) -> MappingResult:
    """面ローカル symbol をセルへ割り付け、空行と枠外を分類する。"""
    per_cell: dict[str, list[Symbol]] = {}
    below = other = 0

    cells_by_face: dict[str, list[CellSpec]] = {}
    for c in cells:
        cells_by_face.setdefault(c.face_id, []).append(c)
    face_by_id = {f.face_id: f for f in faces}

    for face_id, syms in symbols_by_face.items():
        face_cells = cells_by_face.get(face_id, [])
        zones = face_by_id[face_id].table_zones if face_id in face_by_id else ()
        # 空間インデックス（issue #17）。全 symbol × 全セルの線形照合は
        # 記入密度 × 列数の掛け算で悪化する。セルをグリッドのバケツへ入れ、
        # symbol の座標から候補だけを見る。**定義順の first-hit は保つ**
        # ——重なりがあるテンプレートで結果が変わらないようにするため
        # （矩形の重なりは load_template が拒否するが、割付の契約は不変）
        buckets = _bucket_cells(face_cells)
        for s in syms:
            hit = None
            for key, r in _candidates(buckets, s.x, s.y):
                if r.x <= s.x < r.x + r.w and r.y <= s.y < r.y + r.h:
                    hit = key
                    break
            if hit is not None:
                per_cell.setdefault(hit, []).append(s)
            elif any(z.x_min <= s.x < z.x_max and s.y >= z.bottom for z in zones):
                below += 1  # テーブル最終行より下＝行数超過の候補（D-06）
            else:
                other += 1  # 印字ラベル等を含む枠外（D-15。ベースラインが高い点に注意）

    # 空行判定: 行内の text セルに symbol がひとつも無い行（choice の印字は数えない・§6.5）
    rows: dict[tuple[str, int], bool] = {}
    for c in cells:
        if c.table_id is None or c.kind != "text":
            continue
        key = (c.table_id, c.row_no)
        rows[key] = rows.get(key, False) or bool(per_cell.get(c.field_id))
    empty = frozenset(k for k, has in rows.items() if not has)

    # 参照先の合流（2026-08-31）: 主の枠が**完全に空**（symbol が1つも無い）
    # ときに限り、参照先の読取値を主の値として採用する。主にインクがあって
    # 読めない場合は〓のまま——答えは主に書かれているのに別の場所の値を出すと
    # 誤転記になる（転記主義）。主に値があるとき参照先の内容は捨てる
    # （枠外扱いにはしない。参照先として定義された領域の文字だから）
    for c in cells:
        if c.fallback_rect is None:
            continue
        fb = per_cell.pop(c.field_id + _FB, None)
        if fb and c.field_id not in per_cell:
            per_cell[c.field_id] = fb
            log.info("fallback_used", field_id=c.field_id)

    index = {c.field_id: c for c in cells}
    contents = {
        fid: CellContent(text=_cell_text(index[fid], syms),
                         conf_min=min(s.conf for s in syms))
        for fid, syms in per_cell.items()
    }
    return MappingResult(
        cells=contents,
        empty_rows=empty,
        unassigned_below_table=below,
        unassigned_other=other,
    )
