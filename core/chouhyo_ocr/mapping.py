"""symbol → セル割付・空行判定・枠外分類（設計 §6.4・§6.5）。

割付は word ではなく **symbol（1文字）単位**。Vision はセルを跨いで数字を
1 word に結合することがあり（S2 ドライランで日付と金額の混線を実測）、
文字ごとに物理位置で振り直せば結合の影響を受けない。

2026-08-31（5巡目 第2段・docs/design/chouhyo-ocr/04_unclear_policy.md）:
参照先（fallback_rect）の採否判定を U-02/U-03 の3分岐へ、複数領域の連結順を
U-06 の帯方式へ、切り抜きで開いた「穴」に落ちた文字の検知を U-07 へ、それぞれ
置き換えた。判断番号（U-xx）は上記設計書を参照。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from . import logging_safe as log
from .template import BASE_DPI, CellSpec, Face, Rect, hole_bbox

# 由来〓が確定した欄にだけ使うローカルの記号。render_rows.UNCLEAR と同じ文字だが
# あえて別定義にする——mapping は「事実だけを渡す」層という原則（設計 §14 不変条件3）
# の中で、穴に落ちた文字の検知（構造的に確定した事実）だけは例外的にこの層で
# 〓を確定させる（由来は render 側の閾値判断ではなく穴の有無という幾何的事実のため）。
# render_rows への依存を作らないよう独立した定数に留める。
_UNCLEAR = "〓"

NOISE_MAX = 1  # 罫線の掠れ・句点・隣欄からのはみ出しは1文字単位で入る（U-03）。
               # 2文字以上の連なりは記入とみなす。※実データでの較正は未実施


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
    """1物理セルへ割り付いた読取内容。

    char_confs は raw_text と同じ順序・同じ長さの文字単位信頼度（U-10・#62）。
    len(char_confs) != len(text) の場合や空タプルの場合、render 側は文字単位〓を
    適用せず欄全体〓へ倒す（設計 §14 不変条件2）。
    origin は値の由来（''=主／'fallback'=参照先採用／'conflict'=矛盾で〓・
    値は主のまま保存、U-04）。
    """
    text: str
    conf_min: float | None  # symbol が無ければ None
    char_confs: tuple[float, ...] = ()
    origin: str = ""

    def __repr__(self) -> str:  # 記入値を repr へ出さない（設計 §8.1・付録 C7）
        return f"<{type(self).__name__} redacted>"



@dataclass(frozen=True)
class MappingResult:
    cells: Mapping[str, CellContent]          # field_id → 内容（symbol 有りのセルのみ）
    empty_rows: frozenset[tuple[str, int]]    # (table_id, row_no)
    unassigned_below_table: int               # D-06 の入力
    unassigned_other: int                     # D-15 の入力
    # 2026-08-31 追加（U-04・U-07）。ページ単位の件数——run/remap 側が進捗イベント・
    # run サマリへ出す入力になる（設計 §10.3。呼び出し側の配線は本モジュールの範囲外）。
    fallback_used: int = 0        # 参照先を採用した欄の数（判定表 #9）
    fallback_discarded: int = 0   # 破棄した参照先 symbol の数（判定表 A）
    carve_hole: int = 0           # 欄の穴に落ちた symbol の数（判定表 #7）
    # issue #66 段2（FR-1.4・AC-1.10）: 上記3件のうち、発火元（穴の持ち主／
    # fallback_rect の持ち主／矛盾の主）の欄が output: false のものだけを別に
    # 数える。上記の総数（fallback_discarded・carve_hole）は output に関わらず
    # 従来どおり全欄を数え続ける——read/mapping 層は output の影響を受けない
    # という FR-1.1 の不変条件を壊さないため、これは総数の「内訳」であって
    # 「差し替え」ではない。conflict はこれまで総数を持たなかった（ログのみ）ため、
    # ここでは対象外欄由来の件数だけを追加する（総数の新設は本タスクの範囲外）
    fallback_discarded_excluded_field: int = 0
    carve_hole_excluded_field: int = 0
    conflict_excluded_field: int = 0


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


_LINE_GAP = 30.0  # 行の切れ目とみなす y ギャップ（px・300dpi=BASE_DPI 基準値）※実物で調整
_BAND_OVERLAP_RATIO = 0.5  # 領域を帯へ併合する y 重なりの下限（U-06・§4.2。無次元の比率のため dpi 非依存）


def _line_cluster(syms: list[Symbol], line_gap: float = _LINE_GAP) -> list[Symbol]:
    """1領域内の symbol を y でクラスタし、各行を x 順に並べる（旧 _cell_text の中核）。

    行の分離は固定量子化でなく y ギャップのクラスタリングで行う。line_gap は
    呼び出し元が Template.dpi_scale でスケール済みの値を渡す（汎用化 A-3。
    既定値は BASE_DPI=300 基準のまま・後方互換）。
    """
    if not syms:
        return []
    by_y = sorted(syms, key=lambda s: s.y)
    lines: list[list[Symbol]] = [[by_y[0]]]
    for s in by_y[1:]:
        if s.y - lines[-1][-1].y > line_gap:
            lines.append([s])
        else:
            lines[-1].append(s)
    out: list[Symbol] = []
    for line in lines:
        out.extend(sorted(line, key=lambda s: s.x))
    return out


def _symbols_to_text_and_confs(ordered: list[Symbol]) -> tuple[str, tuple[float, ...]]:
    """連結順が確定した symbol 列 → (text, char_confs)。

    char_confs は raw_text と同じ長さ（設計 §14 不変条件2）。1 symbol の text が
    2文字以上の場合は同じ conf をその文字数ぶん繰り返す（設計 §4.2）。
    """
    text = "".join(s.text for s in ordered)
    confs: list[float] = []
    for s in ordered:
        confs.extend([s.conf] * len(s.text))
    return text, tuple(confs)


def _connect_single(syms: list[Symbol], line_gap: float = _LINE_GAP) -> tuple[str, tuple[float, ...]]:
    """単一領域（参照先の枠など）の連結。"""
    return _symbols_to_text_and_confs(_line_cluster(syms, line_gap))


def _connect_regions(region_syms: list[list[Symbol]],
                     region_rects: Sequence[Rect],
                     line_gap: float = _LINE_GAP) -> tuple[str, tuple[float, ...]]:
    """帯 → 領域 → 行 → x の順で連結する（U-06・設計 §4.2）。

    領域を y レンジの重なりで「帯」へ推移的に併合し、帯を y_min 昇順、
    帯内の領域を x_min 昇順に並べる。空の領域（symbol が無い）は無視する。
    単一領域しか無い場合は _connect_single と同じ結果になる（回帰・T-05）。
    """
    active = [i for i in range(len(region_rects)) if region_syms[i]]
    if not active:
        return "", ()
    if len(active) == 1:
        return _connect_single(region_syms[active[0]], line_gap)

    parent = {i: i for i in active}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            ra, rb = region_rects[a], region_rects[b]
            y0, y1 = ra.y, ra.y + ra.h
            z0, z1 = rb.y, rb.y + rb.h
            overlap = min(y1, z1) - max(y0, z0)
            if overlap >= _BAND_OVERLAP_RATIO * min(ra.h, rb.h):
                union(a, b)

    groups: dict[int, list[int]] = {}
    for i in active:
        groups.setdefault(find(i), []).append(i)
    bands = list(groups.values())
    band_order = sorted(bands, key=lambda idxs: min(region_rects[i].y for i in idxs))

    out: list[Symbol] = []
    for band in band_order:
        for ridx in sorted(band, key=lambda i: region_rects[i].x):
            out.extend(_line_cluster(region_syms[ridx], line_gap))
    return _symbols_to_text_and_confs(out)


def fallback_decision(n_main: int, n_fb: int) -> str:
    """U-03 の3分岐。origin 文字列（''／'fallback'／'conflict'）を返す。

    n_main=n_fb=0（記入なし）は呼び出し側が別途扱う——ここで見るのは
    「参照先を採用するか／矛盾とみなすか」の判定のみ。
    """
    if n_main == 0 and n_fb >= 1:
        return "fallback"
    if 1 <= n_main <= NOISE_MAX and n_fb >= 2:
        return "conflict"
    return ""


_BUCKET = 128  # グリッドの一辺（px・300dpi=BASE_DPI 基準値）。セル高（実測 90〜148px）と同程度

# _bucket_cells が返す索引のキーは (field_id, tag) の組。tag は
# 領域インデックス（int・欄の領域）／_TAG_FALLBACK（参照先）／_TAG_HOLE（欄の穴）
_TAG_FALLBACK = "fb"
_TAG_HOLE = "hole"


def _bucket_for(dpi: int) -> int:
    """_BUCKET（px・BASE_DPI=300 較正）を dpi に合わせてスケールした整数バケツ幅。

    離散化パラメタ（グリッドの一辺）は px 整数が必要なので丸める。四捨五入
    （round）で最も近い px へ寄せ、極端な低 dpi でも 0 除算・無限バケツ化を
    避けるため下限 1 を保証する（丸め方針・汎用化 A-3）。schema 許容域
    （72〜1200・schema/template.schema.json）の dpi では実際にはこの下限に
    到達しない（72dpi でも _BUCKET*0.24 ≈ 31）——本関数を直接呼ぶ経路
    （テスト等・schema 検証を経ない呼び出し）向けの防御として残している。

    assign()・build_symbol_locator()・（旧）locate_symbol() の3箇所に散っていた
    同じ計算式をここへ1箇所化した（レビュー M-2）。
    """
    return max(1, round(_BUCKET * (dpi / BASE_DPI)))


@dataclass(frozen=True)
class SymbolLocator:
    """symbol の行き先索引（build_symbol_locator の戻り値・#60 M-6・A-3 M-2）。

    バケツ辞書とバケツ幅を1つにまとめて保持する。locate_symbol 側が別途 dpi を
    受け取ってバケツ幅を再計算する必要がなくなるため、索引を作った dpi と
    引く時の dpi が食い違うことが型として起こらない（旧設計は locator と
    dpi を別々に持ち回る必要があり、面をまたいだ取り違えが起こり得た）。
    """
    buckets: dict
    bucket: int


def _bucket_cells(cells: Sequence[CellSpec], bucket: int = _BUCKET) -> dict:
    """セルをグリッドのバケツへ入れる（issue #17 の空間インデックス）。

    1セルが複数バケツにまたがる場合は全てへ入れる。バケツ内の順序は
    「欄の領域（定義順）→ 参照先 → 穴」（設計 §14 不変条件7）——重なりは
    load_template が拒否するので、**穴どうしを除き**この順序が結果を
    変えることはない（参照先・穴は欄の領域とは異なる意味の受け皿のため、
    順序で優先度を表す）。穴どうしの重なりだけは拒否せず W-4 で警告のみ
    （issue #66 第2弾・05 F-12・template._hole_overlap_warnings）——その場合
    ここでの積み順（=cells の配列順）が first-hit を決める、残存する順序依存。

    bucket は呼び出し元が Template.dpi_scale でスケール済みの値を渡す
    （汎用化 A-3。既定値は BASE_DPI=300 基準のまま・後方互換）。0 除算を
    避けるため呼び出し元は 1 未満を渡さないこと（build_symbol_locator/
    assign が丸め時に下限 1 を保証する）。
    """
    buckets: dict[tuple[int, int], list] = {}
    targets: list[tuple[tuple[str, object], Rect]] = []
    for c in cells:
        for ridx, r in enumerate(c.all_rects()):
            targets.append(((c.field_id, ridx), r))
    for c in cells:
        if c.fallback_rect is not None:
            targets.append(((c.field_id, _TAG_FALLBACK), c.fallback_rect))
    for c in cells:
        bbox = hole_bbox(c)
        if bbox is not None:
            targets.append(((c.field_id, _TAG_HOLE), bbox))
    for i, (key, r) in enumerate(targets):
        for bx in range(r.x // bucket, (r.x + r.w) // bucket + 1):
            for by in range(r.y // bucket, (r.y + r.h) // bucket + 1):
                buckets.setdefault((bx, by), []).append((i, key, r))
    return buckets


def _candidates(buckets: dict, x: float, y: float, bucket: int = _BUCKET):
    """座標を含むバケツの受け皿（key, rect）を定義順で返す。"""
    got = buckets.get((int(x) // bucket, int(y) // bucket))
    if not got:
        return ()
    return [(k, r) for _i, k, r in got]


def _locate_hit(buckets: dict, x: float, y: float, bucket: int = _BUCKET):
    """(x, y) の first-hit を返す（(field_id, tag) または None）。"""
    for key, r in _candidates(buckets, x, y, bucket):
        if r.x <= x < r.x + r.w and r.y <= y < r.y + r.h:
            return key
    return None


def build_symbol_locator(cells: Sequence[CellSpec], dpi: int = BASE_DPI) -> SymbolLocator:
    """symbol の行き先を調べるための索引を作る（debug-images 等の再利用向け・#60 M-6）。

    戻り値は SymbolLocator（バケツ辞書＋バケツ幅）。座標系は面ごとに独立する
    ため、cells は同一 face_id のセルに絞って渡すこと。

    dpi はテンプレートの render_dpi（汎用化 A-3）。既定 BASE_DPI=300 のときは
    従来どおり _BUCKET をそのまま使う。locator がバケツ幅を自分で保持する
    ため、locate_symbol はこの locator だけで正しいバケツ幅を引ける——
    面をまたいで locator を使い回しても、各 locator は自分を作ったときの
    dpi 由来のバケツ幅のまま動く（dpi の取り違えが型として起こらない）。
    """
    bucket = _bucket_for(dpi)
    return SymbolLocator(buckets=_bucket_cells(cells, bucket), bucket=bucket)


def locate_symbol(locator: SymbolLocator, x: float, y: float) -> tuple[str | None, str | None]:
    """1つの symbol の行き先を返す（field_id, tag）。

    tag は "region"（欄の領域）／"fallback"（参照先）／"hole"（欄の穴）のいずれか。
    どの受け皿にも入らない場合は (None, None)。locator は build_symbol_locator
    が返すバケツ幅つきの索引なので、呼び出し側は dpi を意識しなくてよい。
    """
    hit = _locate_hit(locator.buckets, x, y, locator.bucket)
    if hit is None:
        return None, None
    fid, tag = hit
    if isinstance(tag, int):
        return fid, "region"
    if tag == _TAG_FALLBACK:
        return fid, "fallback"
    return fid, "hole"


def _hole_hit(buckets: dict, x: float, y: float, bucket: int = _BUCKET) -> str | None:
    """(x, y) がどこかの欄の穴に入っているかを調べる（破棄された参照先 symbol 用）。

    呼び出し元は「この座標は既にどこかの参照先に当たった」ことを知っている
    （破棄判定はその前提の上でしか呼ばれない）ので、参照先タグの候補は
    無視して調べ直す——素直に locate_symbol を呼ぶと、この symbol 自身が
    当たった参照先の受け皿が first-hit で再び返るだけになり、その参照先の
    枠と重なる別の欄の穴（例: 郵便番号の参照先 ≒ 住所欄の穴）へ絶対に
    到達できない。参照先どうし・参照先と欄の重なりは load_template が
    拒否しているため（issue #24）、参照先タグをスキップしても欄の領域を
    誤って穴と判定することはない。
    """
    for key, r in _candidates(buckets, x, y, bucket):
        fid, tag = key
        if tag == _TAG_FALLBACK:
            continue
        if r.x <= x < r.x + r.w and r.y <= y < r.y + r.h:
            return fid if tag == _TAG_HOLE else None
    return None


def assign(
    cells: Sequence[CellSpec],
    symbols_by_face: Mapping[str, Sequence[Symbol]],
    faces: Sequence[Face],
    dpi: int = BASE_DPI,
) -> MappingResult:
    """面ローカル symbol をセルへ割り付け、空行と枠外を分類する（U-02/U-03/U-06/U-07）。

    dpi はテンプレートの render_dpi（汎用化 A-3）。_LINE_GAP・_BUCKET は
    BASE_DPI=300 較正の px 定数なので、Template.dpi_scale 相当の比で
    スケールしてから使う。既定 dpi=BASE_DPI のときは scale=1.0 となり、
    従来の定数をそのまま使ったときと完全に同じ値になる（バイト一致契約）。
    """
    scale = dpi / BASE_DPI
    line_gap = _LINE_GAP * scale
    bucket = _bucket_for(dpi)

    cells_by_face: dict[str, list[CellSpec]] = {}
    for c in cells:
        cells_by_face.setdefault(c.face_id, []).append(c)
    face_by_id = {f.face_id: f for f in faces}

    # field_id → 領域インデックス → symbol 列（U-06 の連結に使う）
    region_syms: dict[str, dict[int, list[Symbol]]] = {}
    fb_syms: dict[str, list[Symbol]] = {}
    hole_hits: dict[str, int] = {}
    below = other = 0
    locators: dict[str, dict] = {}

    for face_id, syms in symbols_by_face.items():
        face_cells = cells_by_face.get(face_id, [])
        zones = face_by_id[face_id].table_zones if face_id in face_by_id else ()
        # 空間インデックス（issue #17）。全 symbol × 全セルの線形照合は
        # 記入密度 × 列数の掛け算で悪化する。セルをグリッドのバケツへ入れ、
        # symbol の座標から候補だけを見る。**定義順の first-hit は保つ**
        buckets = _bucket_cells(face_cells, bucket)
        locators[face_id] = buckets
        for s in syms:
            hit = _locate_hit(buckets, s.x, s.y, bucket)
            if hit is not None:
                fid, tag = hit
                if tag == _TAG_FALLBACK:
                    fb_syms.setdefault(fid, []).append(s)
                elif tag == _TAG_HOLE:
                    # 穴への直撃（U-07）: この時点で来る symbol は「他欄の破棄
                    # symbol の再割付」経路とは別に、元々この座標に来た記入。
                    # 枠外にはしない（穴は欄の内側であって枠外ではない・§5.2）
                    hole_hits[fid] = hole_hits.get(fid, 0) + 1
                else:
                    region_syms.setdefault(fid, {}).setdefault(tag, []).append(s)
            elif any(z.x_min <= s.x < z.x_max and s.y >= z.bottom for z in zones):
                below += 1  # テーブル最終行より下＝行数超過の候補（D-06）
            else:
                other += 1  # 印字ラベル等を含む枠外（D-15。ベースラインが高い点に注意）

    contents: dict[str, CellContent] = {}
    fallback_used = 0
    fallback_discarded = 0
    fallback_discarded_excluded_field = 0
    conflict_excluded_field = 0
    # issue #66 段2（FR-1.4）: 発火元の欄が output: false かどうかの参照用。
    # 対象外判定はここで output_cells() を経由せず CellSpec.output を直接見る
    # ——column.py 側の「output に出す/出さない」判定とは別で、こちらは
    # 「読み取り上の事実（どの欄で警告が起きたか）」を output で色分けするだけ
    output_by_id = {c.field_id: c.output for c in cells}

    for c in cells:
        all_rects = c.all_rects()
        rsyms = region_syms.get(c.field_id, {})
        n_main = sum(len(v) for v in rsyms.values())
        fsyms = fb_syms.get(c.field_id, []) if c.fallback_rect is not None else []
        n_fb = len(fsyms)

        if n_main == 0 and n_fb == 0:
            continue  # 記入なし（判定表 #3・既存）

        decision = fallback_decision(n_main, n_fb)
        if decision == "fallback":
            # U-02: 主（全領域）が完全に空のときだけ参照先を読む（判定表 #9）
            text, confs = _connect_single(fsyms, line_gap)
            contents[c.field_id] = CellContent(
                text=text, conf_min=(min(confs) if confs else None),
                char_confs=confs, origin="fallback")
            fallback_used += 1
            log.info("fallback_used", field_id=c.field_id)
            continue

        # 主を採用する（矛盾＝conflict の場合も値は主のまま・U-03）
        region_list = [rsyms.get(i, []) for i in range(len(all_rects))]
        text, confs = _connect_regions(region_list, all_rects, line_gap)
        contents[c.field_id] = CellContent(
            text=text, conf_min=(min(confs) if confs else None),
            char_confs=confs, origin=("conflict" if decision == "conflict" else ""))
        if decision == "conflict":
            log.warn("fallback_conflict", field_id=c.field_id)
            if not c.output:
                conflict_excluded_field += 1

        if n_fb >= 1:
            # U-02: 参照先は「主が空のときだけ有効な受け皿」。無効なとき参照先の
            # 文字は消えてはならず、必ず穴・枠外のどちらかに分類される
            # （設計 §14 不変条件6・判定表 A）
            fallback_discarded += n_fb
            log.warn("fallback_discarded", field_id=c.field_id, count=n_fb)
            if not c.output:
                # fallback_rect は c（この欄）が持つので、発火元の欄は c 自身
                fallback_discarded_excluded_field += n_fb
            loc = locators.get(c.face_id)
            for s in fsyms:
                hit_fid = _hole_hit(loc, s.x, s.y, bucket) if loc is not None else None
                if hit_fid is not None:
                    hole_hits[hit_fid] = hole_hits.get(hit_fid, 0) + 1
                else:
                    other += 1

    carve_hole = sum(hole_hits.values())
    # issue #66 段2（FR-1.4）: 穴の持ち主（fid）は _hole_hit / 直撃のどちらの
    # 経路でも hole_hits のキーになるため、ここを1回集計するだけで両経路を
    # 網羅できる（fid の output で対象外由来かを判定）
    carve_hole_excluded_field = sum(
        n for fid, n in hole_hits.items() if not output_by_id.get(fid, True))
    for fid, n in hole_hits.items():
        # U-07: 穴に落ちた文字が1つでもあれば、その欄のそれまでの内容を
        # 破棄して欄全体〓にする（判定表 #7）。空欄だった場合も新規に立てる——
        # 位置的に怪しい記入がある事実を無言で隠さない（設計原則）
        contents[fid] = CellContent(text=_UNCLEAR, conf_min=None,
                                    char_confs=(), origin="")
        log.warn("carve_hole", field_id=fid, count=n)

    # 空行判定: 行内の text セルに内容がひとつも無い行（choice の印字は数えない・§6.5）
    rows: dict[tuple[str, int], bool] = {}
    for c in cells:
        if c.table_id is None or c.kind != "text":
            continue
        key = (c.table_id, c.row_no)
        rows[key] = rows.get(key, False) or (c.field_id in contents)
    empty = frozenset(k for k, has in rows.items() if not has)

    return MappingResult(
        cells=contents,
        empty_rows=empty,
        unassigned_below_table=below,
        unassigned_other=other,
        fallback_used=fallback_used,
        fallback_discarded=fallback_discarded,
        carve_hole=carve_hole,
        fallback_discarded_excluded_field=fallback_discarded_excluded_field,
        carve_hole_excluded_field=carve_hole_excluded_field,
        conflict_excluded_field=conflict_excluded_field,
    )
