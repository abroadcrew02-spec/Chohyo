"""枠候補の生成（設計 §6.9・`detect-grid`／設計 08 §4・`detect-frames`）。

生成器は2つ。`RuledLineGrid`（罫線の射影検出）と `UniformGrid`（等分割）。
どちらも同じ GridFit を返し、テンプレート編集画面は生成器の違いを知らない。
検出の当てはめ残差（最大ずれ px）を返し、大きいときは利用者が等分割へ
切り替える判断材料にする。

罫線検出はテンプレート較正（2026-08-27）で実証した射影方式:
行方向・列方向の暗画素射影で被覆率の高い帯を線とみなす。

本モジュールにはもう1つの検出系がある。`detect_frames`（issue #73 (b)・
設計 08 §4）は**領域指定なし**（ページ全体）から表候補・欄候補を一括生成
する新しい系統で、`segments.detect_segments`（線分・端点付き）を土台に
レール化→矩形化を行う。`detect_ruled`／`make_uniform`（既存・`--region`
必須の系統）とは独立しており、両者は1行も共有しない——**既存2関数は本節の
追加によって一切変更されない**（設計 08 §4.9 不変条件2）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np

from . import segments as _segments
from .pipeline_errors import OperationRefused
from .projection import H_COVERAGE, LINE_GAP, V_COVERAGE, line_positions
from .segments import Segment, UnionFind
from .template import BASE_DPI, Rect, Template

ROW_INSET = 4       # 行高 = ピッチ − 罫線ぶんの控え（候補値・編集画面で調整・300dpi=BASE_DPI 基準値）


@dataclass(frozen=True)
class GridFit:
    """テーブル定義（§4.2 tables[]）への当てはめ結果。"""
    mode: str                 # "ruled" | "uniform"
    origin_x: int
    origin_y: int
    rows: int
    row_pitch: float
    row_height: int
    columns: list[dict]       # [{"x_offset": int, "width": int}]
    residual_px: float        # 検出線と等間隔当てはめの最大ずれ（uniform は 0）

    def to_json(self) -> dict:
        return asdict(self)


def detect_ruled(gray: "np.ndarray", region: tuple[int, int, int, int],
                 dpi: int = BASE_DPI) -> GridFit | None:
    """罫線からテーブル定義を当てはめる。線が足りなければ None。

    dpi はこの画像の render_dpi（汎用化 A-3）。ROW_INSET・projection.LINE_GAP は
    BASE_DPI=300 較正の px 定数なので、dpi/BASE_DPI の比でスケールしてから使う。
    既定 dpi=BASE_DPI のときは従来と完全に同じ値になる（S-1）。
    """
    row_inset = max(0, round(ROW_INSET * (dpi / BASE_DPI)))
    line_gap = max(0, round(LINE_GAP * (dpi / BASE_DPI)))
    x, y, w, h = region
    seg = gray[y:y + h, x:x + w] < 128

    h_lines = line_positions(seg.sum(axis=1), w * H_COVERAGE, gap=line_gap)
    if len(h_lines) < 3:          # 行を1つ作るにも上下の線＋もう1本要る
        return None
    v_lines = line_positions(seg[h_lines[0]:h_lines[-1], :].sum(axis=0),
                             (h_lines[-1] - h_lines[0]) * V_COVERAGE, gap=line_gap)
    if len(v_lines) < 2:
        return None

    rows = len(h_lines) - 1
    pitch = (h_lines[-1] - h_lines[0]) / rows
    fitted = [h_lines[0] + pitch * i for i in range(len(h_lines))]
    residual_h = max(abs(o - f) for o, f in zip(h_lines, fitted))

    columns = [{"x_offset": v_lines[i] - v_lines[0],
                "width": v_lines[i + 1] - v_lines[i]}
               for i in range(len(v_lines) - 1)]

    return GridFit(
        mode="ruled",
        origin_x=x + v_lines[0],
        origin_y=y + h_lines[0],
        rows=rows,
        row_pitch=round(pitch, 2),
        row_height=max(1, int(pitch) - row_inset),
        columns=columns,
        residual_px=round(float(residual_h), 2),
    )


def make_uniform(region: tuple[int, int, int, int], rows: int, cols: int,
                 dpi: int = BASE_DPI) -> GridFit:
    """外枠＋行数・列数の等分割。Q-03 に依存せず常に成立する退避先。

    行数・列数は編集画面の入力欄と CLI の --rows/--cols からそのまま渡る。
    0 や負値だと ZeroDivisionError／空の列定義になり、CLI の最上位ハンドラで
    「ERROR ZeroDivisionError: 処理を中止しました」に潰れて何を直せばよいか
    分からなくなる。業務的な拒否として明示的に断る（レビュー4巡目 LOW）。

    dpi はこの画像の render_dpi（汎用化 A-3）。ROW_INSET のスケールは
    detect_ruled と同じ（既定 dpi=BASE_DPI のときは従来と完全に同じ値）。
    """
    row_inset = max(0, round(ROW_INSET * (dpi / BASE_DPI)))
    x, y, w, h = region
    if rows < 1 or cols < 1:
        raise OperationRefused(
            f"行数・列数は 1 以上にする（指定: 行 {rows}・列 {cols}）",
            hint="表の行数と列数を入力し直す")
    if w < 1 or h < 1:
        raise OperationRefused(
            f"表の外枠の幅・高さは 1px 以上にする（指定: 幅 {w}・高さ {h}）",
            hint="外枠を描き直す")
    pitch = h / rows
    # 端数を捨てると最終列が枠から最大 cols-1 px 手前で終わり、右端の文字を
    # 取りこぼす。境界を実数で刻んでから整数へ丸め、幅を差で決める（レビュー LOW）
    edges = [round(w * i / cols) for i in range(cols + 1)]
    columns = [{"x_offset": edges[i], "width": max(1, edges[i + 1] - edges[i])}
               for i in range(cols)]
    return GridFit(
        mode="uniform",
        origin_x=x,
        origin_y=y,
        rows=rows,
        row_pitch=round(pitch, 2),
        row_height=max(1, int(pitch) - row_inset),
        columns=columns,
        residual_px=0.0,
    )


# ===========================================================================
# detect_frames（issue #73 (b)・設計 08 §4.2）
# ===========================================================================
# 矩形化専用の閾値。segments.py の8個（線分抽出）とは別の3個
# （08 §4.2.1 手順3・手順5 に登場・線分抽出の閾値表には含まれない）。
EDGE_COVER = 0.90
"""無次元。閉じた矩形と認める4辺それぞれの線分被覆率の下限。同じ基準を
「内部を貫くレール」の判定（原子セル）にも使う。"""

PITCH_TOL = 2
"""px@300dpi。表ブロックへ束ねるとき、連続する行のピッチを一定とみなす許容差。
`segments.COLLINEAR_TOL` と同じ値だが、意味が違う（あちらは線分の同一視、
こちらは行ピッチの揺らぎ）ため別定数として持つ。他の px 閾値と同じく
`segments.scale_threshold` で dpi スケールする（レビュー M-2・
2026-09-03: 以前は素の値のまま比較しており、高 dpi 申告で相対的に
厳しくなりすぎる不具合があった）。"""

MAX_RAILS = 200
"""無次元。矩形化でのレール数の打ち切りガード。線分抽出（segments.py）の
閾値ではなく矩形化専用のため、こちらに定義する（レビュー LOW・
2026-09-03: 以前は segments.py 側にあった）。"""


@dataclass(frozen=True)
class TableCandidate:
    """表候補（等ピッチ行 >= 2 の束・08 §4.2.1 手順5）。"""
    rect: Rect                # 外接矩形（GUI が候補を1つの矩形として描く用）
    origin_x: int
    origin_y: int
    rows: int
    row_pitch: float
    row_height: int
    columns: list[dict]       # [{"x_offset": int, "width": int}]
    residual_px: float        # max(等ピッチ当てはめの残差, 構成セルのレール散らばり)・#85 N-1
    face_id: str | None = None
    overlaps_existing: bool = False


@dataclass(frozen=True)
class FieldCandidate:
    """単発欄候補（表に吸収されなかった原子セル・08 §4.2.1 手順6）。"""
    rect: Rect
    residual_px: float
    face_id: str | None = None
    overlaps_existing: bool = False


@dataclass(frozen=True)
class FrameCandidates:
    """`detect_frames` の戻り値（08 §4.4 の JSON 契約に写す元データ）。"""
    tables: tuple[TableCandidate, ...]
    fields: tuple[FieldCandidate, ...]
    excluded: tuple[dict, ...]   # [{"reason": str, "count": int}]
    stats: dict                 # {"lines_h", "lines_v", "rects", "rails_h", "rails_v"}
    zero_reason: str | None     # None | "no_lines" | "no_rect" | "all_filtered" | "too_many_lines"


def _cluster_rails(segs: list["Segment"], tol: int) -> list[tuple[float, list["Segment"]]]:
    """pos が tol 以内で連続する線分を1つのレールへ束ねる（連鎖クラスタリング）。

    戻り値は pos 昇順の [(レール代表位置, そのレールに属する線分群), ...]。
    代表位置は**同一クラスタの全線分の pos の平均**で、線分どうしの区間
    （start/end）が重なるかは問わない——ページの別の場所にある短い線でも、
    pos が tol 以内なら同じレールへ入り、代表位置＝報告される矩形の辺を
    引っ張る（#85 LOW・レビュー実測）。この引っ張りは残差に表れる
    （原子セルの `cell_residual`。#85 N-1 で表候補の `residual_px` にも
    反映するようにした）ので、座標だけが黙ってずれることはない。
    """
    ordered = sorted(segs, key=lambda s: s.pos)
    clusters: list[list["Segment"]] = []
    for seg in ordered:
        if clusters and abs(seg.pos - clusters[-1][-1].pos) <= tol:
            clusters[-1].append(seg)
        else:
            clusters.append([seg])
    return [(round(sum(s.pos for s in c) / len(c), 1), c) for c in clusters]


def _coverage(segs: list["Segment"], lo: float, hi: float) -> float:
    """レールに属する線分群の [lo, hi] 区間に対する被覆率（重複区間は畳んで計算）。"""
    span = hi - lo
    if span <= 0:
        return 1.0
    intervals = sorted(
        (max(lo, s.start), min(hi, s.end)) for s in segs if s.end >= lo and s.start <= hi
    )
    total = 0.0
    cur_s: float | None = None
    cur_e: float = 0.0
    for s, e in intervals:
        if e <= s:
            continue
        if cur_s is None:
            cur_s, cur_e = s, e
        elif s <= cur_e:
            cur_e = max(cur_e, e)
        else:
            total += cur_e - cur_s
            cur_s, cur_e = s, e
    if cur_s is not None:
        total += cur_e - cur_s
    return total / span


def _grid_atomic_cells(h_rails: list[tuple[float, list["Segment"]]],
                       v_rails: list[tuple[float, list["Segment"]]],
                       edge_cover: float
                       ) -> tuple[list[tuple[float, float, float, float, float]], int, int, int]:
    """レールが作る基本グリッドセルを、閉じていない内壁を挟んで結合し、
    4辺すべてが閉じている矩形（原子セル）を列挙する。

    **08 §4.2.1 手順4（内部を貫くレールが無い閉じた矩形）の厳密な同値では
    ない——グリッドセル単位の連結成分で近似する**（レビュー M-1・
    2026-09-03: 以前は「自然に満たされる」と書いていたが誤りだった）。
    Union-Find で「閉じていない内壁」を挟んで隣接セルを結合する方式は
    ほとんどのケースで手順4と一致するが、**L字型（非矩形）の連結成分が
    できた場合はその成分を丸ごと候補から外す**——08 の手順は矩形ごとに
    独立して判定するため、L字を構成する2つの矩形をそれぞれ拾える経路が
    ありうるが、本実装は連結成分1つを単位にしか見ないためその経路を
    持たない。以前はこれを候補ゼロ・理由ゼロで黙って消していた
    （M-1 指摘）——非矩形として落ちた連結成分数を戻り値の2番目で返し、
    呼び出し元が `excluded` へ `reason:"non_rectangular"` として計上する。

    「4隅が交点」の明示チェックはしない——辺の被覆率が edge_cover(0.90) 以上
    あれば、その両端付近もほぼ確実に線を持つため、別立ての交点判定は不要。

    矩形ではあるが外周4辺のどれかが閉じていない（被覆率 edge_cover 未満）
    連結成分も、以前は理由ゼロで黙って落としていた（#85 N-2。sample-1 の
    実測で 157 成分中 11 個がこれに当たり、`stats.components` と `rects` の
    差として現れるだけで内訳が無かった）。落ちた数を戻り値の3番目で返し、
    呼び出し元が `excluded` へ `reason:"not_closed"` として計上する。

    実装はレール対の全組み合わせ（O(nh^2 * nv^2)）を試す素朴な方式ではなく、
    基本セル単位の Union-Find（O(nh*nv)）にした——罫線が密なテーブル
    （行数の多い formC 実測で 37×9 レール）では前者が数千万回規模の比較に
    膨らみ、性能検証中に 50 秒超を実測したための設計変更（NFR-F02）。

    戻り値: (原子セル配列, 非矩形として除外した連結成分の数,
    4辺が閉じずに除外した連結成分の数, 連結成分の総数)。4つの数は
    「総数 = 原子セル数 + 非矩形 + 閉じていない」で閉じる（成分の台帳・
    08 §4.2.3・#85 N-2）。
    原子セルは (y1, x1, y2, x2, residual_px)。`residual_px` は4辺それぞれの
    実測線分位置とレール代表位置とのずれの最大値（M-4）——レールは複数の
    線分束をクラスタリングした代表位置なので、束ねられた個々の線分の
    `pos` が代表位置からどれだけばらついているかを表す。
    """
    nh, nv = len(h_rails), len(v_rails)
    if nh < 2 or nv < 2:
        return [], 0, 0, 0
    n_rows, n_cols = nh - 1, nv - 1

    # 水平レール i の [v_rails[j], v_rails[j+1]] 区間に対する被覆（=そのセル
    # 列の上辺/下辺が閉じているか）。垂直レール j の [h_rails[i], h_rails[i+1]]
    # 区間に対する被覆（=そのセル行の左辺/右辺が閉じているか）
    h_closed = [[_coverage(h_rails[i][1], v_rails[j][0], v_rails[j + 1][0]) >= edge_cover
                for j in range(n_cols)] for i in range(nh)]
    v_closed = [[_coverage(v_rails[j][1], h_rails[i][0], h_rails[i + 1][0]) >= edge_cover
                for j in range(nv)] for i in range(n_rows)]

    uf = UnionFind(n_rows * n_cols)

    def idx(i: int, j: int) -> int:
        return i * n_cols + j

    for i in range(n_rows):
        for j in range(n_cols):
            if j + 1 < n_cols and not v_closed[i][j + 1]:  # 右壁が閉じていない
                uf.union(idx(i, j), idx(i, j + 1))
            if i + 1 < n_rows and not h_closed[i + 1][j]:  # 下壁が閉じていない
                uf.union(idx(i, j), idx(i + 1, j))

    groups: dict[int, list[tuple[int, int]]] = {}
    for i in range(n_rows):
        for j in range(n_cols):
            groups.setdefault(uf.find(idx(i, j)), []).append((i, j))

    atomic: list[tuple[float, float, float, float, float]] = []
    non_rectangular = 0
    not_closed = 0
    for members in groups.values():
        is_ = [m[0] for m in members]
        js_ = [m[1] for m in members]
        i_min, i_max, j_min, j_max = min(is_), max(is_), min(js_), max(js_)
        # 連結成分の面積が外接矩形の面積と一致しなければ矩形でない（L字型等）
        # ——Union-Find は隣接結合のみなので飛び地は生じず、面積一致は
        # 「穴・凹みが無い」ことの十分条件になる
        if len(members) != (i_max - i_min + 1) * (j_max - j_min + 1):
            non_rectangular += 1
            continue
        # 外周4辺の閉じ判定。1辺でも被覆率 edge_cover に満たなければ矩形と
        # 認めず、`not_closed` として数える（#85 N-2・黙って消さない）
        if not (all(h_closed[i_min][j] for j in range(j_min, j_max + 1))
                and all(h_closed[i_max + 1][j] for j in range(j_min, j_max + 1))
                and all(v_closed[i][j_min] for i in range(i_min, i_max + 1))
                and all(v_closed[i][j_max + 1] for i in range(i_min, i_max + 1))):
            not_closed += 1
            continue
        y1, y2 = h_rails[i_min][0], h_rails[i_max + 1][0]
        x1, x2 = v_rails[j_min][0], v_rails[j_max + 1][0]
        top_segs, bottom_segs = h_rails[i_min][1], h_rails[i_max + 1][1]
        left_segs, right_segs = v_rails[j_min][1], v_rails[j_max + 1][1]
        residual = max(
            max((abs(s.pos - y1) for s in top_segs), default=0.0),
            max((abs(s.pos - y2) for s in bottom_segs), default=0.0),
            max((abs(s.pos - x1) for s in left_segs), default=0.0),
            max((abs(s.pos - x2) for s in right_segs), default=0.0),
        )
        atomic.append((y1, x1, y2, x2, residual))
    return atomic, non_rectangular, not_closed, len(groups)


_STRADDLE_FACE = "__straddle__"  # _assign_face のセンチネル（正規の face_id と衝突しない内部専用値）


def _assign_face(rect: Rect, template: "Template") -> str | None:
    """候補矩形の中心が入る面の face_id。面をまたぐ場合はセンチネル `_STRADDLE_FACE`。

    どの面にも中心が収まらなければ None（テンプレートの面外＝通常は
    ページ外形として別途除外される想定）。
    """
    cx, cy = rect.x + rect.w / 2, rect.y + rect.h / 2
    for f in template.faces:
        r = f.source_rect
        if r.x <= cx < r.x + r.w and r.y <= cy < r.y + r.h:
            if (rect.x >= r.x and rect.y >= r.y
                    and rect.x + rect.w <= r.x + r.w and rect.y + rect.h <= r.y + r.h):
                return f.face_id
            return _STRADDLE_FACE
    return None


def _rects_overlap(a: Rect, b: Rect) -> bool:
    return (a.x < b.x + b.w and b.x < a.x + a.w
            and a.y < b.y + b.h and b.y < a.y + a.h)


def _overlaps_existing(rect: Rect, face_id: str, template: "Template") -> bool:
    """候補矩形（ページ座標）が face_id の既存セルのいずれかと重なるか。"""
    face = template.face(face_id)
    local = Rect(rect.x - face.source_rect.x, rect.y - face.source_rect.y, rect.w, rect.h)
    for c in template.cells:
        if c.face_id != face_id:
            continue
        for r in c.all_rects():
            if _rects_overlap(local, r):
                return True
    return False


def detect_frames(binary: "np.ndarray", dpi: int = BASE_DPI,
                  exclusions: "tuple[Rect, ...] | list[Rect]" = (),
                  existing: "Template | None" = None) -> FrameCandidates:
    """ページ全体（領域指定なし）から表候補・欄候補を一括生成する（issue #73 (b)）。

    手順（設計 08 §4.2.1）: 線分抽出 → レール化 → 交点 → 閉じた矩形
    （4辺被覆 >= EDGE_COVER）→ 原子セル（内部を貫くレールが無いもの・
    グリッドセル単位の連結成分で近似・M-1）→ 行内で x 方向に隙間がある
    箇所を別ブロックとして分割（H-2・FR-F16「ブロック単位」）→ 垂直レール
    署名が一致し等ピッチ（±PITCH_TOL）な行 >= 2 の束を表ブロック、残りを
    単発欄候補にする。

    `binary`: 二値画像（True=インク）。**テンプレート座標系のページ画像
    （位置合わせ後・テンプレートの `image_size` と一致する寸法）を渡す前提**
    （M-3）——寸法が合わない画像に対する挙動は呼び出し元（CLI）が
    `existing`/`exclusions` を渡す前に判定する責務で、本関数自体は寸法の
    整合性を検査しない。除外領域はまだ潰されていない前提——`exclusions`
    （ページ座標の Rect 列）をここで白潰し（False 化）してから線分抽出へ
    渡す（08 §4.1.5: `--template` 指定時のみ利用者が渡す）。
    `existing`: 渡されたテンプレート。各候補への face_id 割り当てと
    `overlaps_existing` の算出に使う。None なら face_id は全候補 None・
    `overlaps_existing` は常に False（08 §4.2.3）。
    座標は常にページ座標（08 §4.2.3・§4.9 不変条件3）。
    """
    work = binary.copy()
    for ex in exclusions:
        work[ex.y:ex.y + ex.h, ex.x:ex.x + ex.w] = False

    h_segs, v_segs = _segments.detect_segments(work, dpi)
    stats = {"lines_h": len(h_segs), "lines_v": len(v_segs), "rects": 0,
             "rails_h": 0, "rails_v": 0, "components": 0}
    if not h_segs and not v_segs:
        return FrameCandidates((), (), (), stats, "no_lines")

    tol = _segments.scale_threshold(_segments.COLLINEAR_TOL, dpi)
    pitch_tol = _segments.scale_threshold(PITCH_TOL, dpi)  # M-2: 他の px 閾値と同じく dpi スケールする
    h_rails = _cluster_rails(h_segs, tol)
    v_rails = _cluster_rails(v_segs, tol)
    stats["rails_h"] = len(h_rails)
    stats["rails_v"] = len(v_rails)
    if len(h_rails) > MAX_RAILS or len(v_rails) > MAX_RAILS:
        return FrameCandidates((), (), (), stats, "too_many_lines")

    atomic_raw, non_rectangular, not_closed, components = _grid_atomic_cells(
        h_rails, v_rails, EDGE_COVER)
    stats["rects"] = len(atomic_raw)
    stats["components"] = components
    # `excluded` には2つの台帳が混ざる（08 §4.2.3）。成分の台帳は
    # components = rects + non_rectangular + not_closed で閉じ、セルの台帳
    # （page_outline・too_small・straddles_face）は rects から引かれる。
    # 全 reason を単純に足して components と引き算しない
    excluded_counts: dict[str, int] = {}
    if non_rectangular:
        excluded_counts["non_rectangular"] = non_rectangular
    if not_closed:
        excluded_counts["not_closed"] = not_closed
    if not atomic_raw:
        excluded = tuple({"reason": k, "count": v} for k, v in excluded_counts.items())
        return FrameCandidates((), (), excluded, stats, "no_rect")

    page_h, page_w = binary.shape
    min_rect_size = _segments.scale_threshold(_segments.MIN_RECT_SIZE, dpi)  # LOW: ループ外で1回だけ計算
    kept: list[tuple[float, float, float, float, float]] = []
    for (y1, x1, y2, x2, cell_residual) in atomic_raw:
        w, h = x2 - x1, y2 - y1
        if w >= page_w * 0.9 and h >= page_h * 0.9:
            excluded_counts["page_outline"] = excluded_counts.get("page_outline", 0) + 1
            continue
        # `too_small` は死んだ分岐ではない——`segments.MIN_SEG_LEN` は「レールが
        # 立つか」の閾値で、原子セルの辺長とは独立に効く（長いレール2本が
        # 20px 未満の間隔で並べば小さい原子セルができる。sample-1 の実測で
        # 5件発火）。発火しないように見えてもデッドコードとして削らない（#85 LOW）
        if w < min_rect_size or h < min_rect_size:
            excluded_counts["too_small"] = excluded_counts.get("too_small", 0) + 1
            continue
        kept.append((y1, x1, y2, x2, cell_residual))

    if not kept:
        excluded = tuple({"reason": k, "count": v} for k, v in excluded_counts.items())
        return FrameCandidates((), (), excluded, stats, "all_filtered")

    # --- 表ブロックへの束ね（08 §4.2.1 手順5・H-2 で行の x 分割を追加） ---
    # 同じ (y1, y2) を持つ原子セルを集めたあと、x 方向の隙間（> tol）で
    # 分割する——水平罫線を共有する左右2ブロックが同じ行として1つに
    # 融合し、間の隙間が「幽霊列」として columns に混入するのを防ぐ
    # （H-2・マリン指摘）。左右ブロックはこれで別々の行セグメントになる
    rows_by_bounds: dict[tuple[float, float], list[tuple[float, float, float, float, float]]] = {}
    for r in kept:
        rows_by_bounds.setdefault((r[0], r[2]), []).append(r)

    row_infos = []
    for (y1, y2), cells in rows_by_bounds.items():
        cells_sorted = sorted(cells, key=lambda c: c[1])
        groups: list[list[tuple[float, float, float, float, float]]] = [[cells_sorted[0]]]
        for c in cells_sorted[1:]:
            prev_x2 = groups[-1][-1][3]
            if c[1] - prev_x2 > tol:
                groups.append([c])
            else:
                groups[-1].append(c)
        for g in groups:
            bounds = sorted({round(c[1], 1) for c in g} | {round(c[3], 1) for c in g})
            signature = tuple(bounds)
            row_infos.append({"y1": y1, "y2": y2, "signature": signature, "cells": g})
    # y1 を主キー、x 位置を副キーにする——同じ y1 でも複数ブロック
    # （分割後の row_info）を安定した順序で並べる
    row_infos.sort(key=lambda ri: (ri["y1"], ri["cells"][0][1]))

    def _same_signature(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
        if len(a) != len(b):
            return False
        return all(abs(x - y) <= tol for x, y in zip(a, b))

    # H-2: run を単一系列（runs[-1] とだけ比較）ではなく、x 範囲ごとに
    # 複数系列を並行して保持する——family/detail のように複数の表が
    # 縦方向へ入り乱れて出現しても、間に別系列の行が挟まった程度では
    # runが打ち切られない（以前は runs[-1] しか見ていないため、直前の
    # 行が別系列だと即座に新しい run を開始してしまっていた）
    runs: list[list[dict]] = []
    for ri in row_infos:
        placed = False
        for run in runs:
            last = run[-1]
            if ri["y1"] <= last["y1"]:
                continue  # 同一・逆行する y は別系列として扱う（安全側）
            if not _same_signature(last["signature"], ri["signature"]):
                continue
            if len(run) == 1:
                run.append(ri)
                placed = True
                break
            pitch0 = run[1]["y1"] - run[0]["y1"]
            pitch_new = ri["y1"] - last["y1"]
            if abs(pitch_new - pitch0) <= pitch_tol:
                run.append(ri)
                placed = True
                break
        if not placed:
            runs.append([ri])

    tables: list[TableCandidate] = []
    used_cells: set[tuple[float, float, float, float, float]] = set()
    for run in runs:
        if len(run) < 2:
            continue
        first = run[0]
        origin_x = min(c[1] for c in first["cells"])
        origin_y = first["y1"]
        pitches = [run[i + 1]["y1"] - run[i]["y1"] for i in range(len(run) - 1)]
        row_pitch = sum(pitches) / len(pitches)
        fitted = [origin_y + row_pitch * i for i in range(len(run))]
        pitch_residual = max(abs(ri["y1"] - f) for ri, f in zip(run, fitted))
        # #85 N-1: 等ピッチ当てはめの残差だけでは rows==2 の候補で判別力が
        # ゼロになる（2点は必ず直線に乗るので定義上つねに 0）。sample-1 の
        # 実測では表候補10件中8件が rows==2 で、ピッチが行高に対して極端な
        # 512／537 の候補まで 0.0 だった。原子セル側が測っているレールの
        # 散らばり（cell_residual）と合わせ、大きい方を候補の残差とする
        rail_residual = max((c[4] for ri in run for c in ri["cells"]), default=0.0)
        residual = max(pitch_residual, rail_residual)
        # セル高の中央値（08 §4.2.1 手順5）。既存 detect_ruled のように
        # ROW_INSET（罫線ぶんの控え）は引かない——ここでの高さは実測した
        # 罫線間の距離そのもので、`grid.ROW_INSET` は「等分割・領域内検出」
        # 側の経験則であり、本設計の閾値表（08 §4.1.4）には含まれていない。
        # 採否は GUI 側で候補を見た人が判断する
        heights = sorted(ri["y2"] - ri["y1"] for ri in run)
        row_height = heights[len(heights) // 2]
        bounds = list(first["signature"])
        columns = [{"x_offset": round(bounds[i] - origin_x), "width": round(bounds[i + 1] - bounds[i])}
                  for i in range(len(bounds) - 1)]
        last = run[-1]
        rect = Rect(round(origin_x), round(origin_y),
                   round(bounds[-1] - origin_x), round(last["y2"] - origin_y))
        table = TableCandidate(
            rect=rect, origin_x=round(origin_x), origin_y=round(origin_y),
            rows=len(run), row_pitch=round(row_pitch, 2), row_height=round(row_height),
            columns=columns, residual_px=round(residual, 2))
        if existing is not None:
            face_id = _assign_face(rect, existing)
            if face_id == _STRADDLE_FACE:
                excluded_counts["straddles_face"] = excluded_counts.get("straddles_face", 0) + 1
                for ri in run:
                    used_cells.update(ri["cells"])
                continue
            overlaps = _overlaps_existing(rect, face_id, existing) if face_id else False
            table = replace(table, face_id=face_id, overlaps_existing=overlaps)
        tables.append(table)
        for ri in run:
            used_cells.update(ri["cells"])

    fields: list[FieldCandidate] = []
    for r in kept:
        if r in used_cells:
            continue
        y1, x1, y2, x2, cell_residual = r
        rect = Rect(round(x1), round(y1), round(x2 - x1), round(y2 - y1))
        face_id: str | None = None
        overlaps = False
        if existing is not None:
            face_id = _assign_face(rect, existing)
            if face_id == _STRADDLE_FACE:
                excluded_counts["straddles_face"] = excluded_counts.get("straddles_face", 0) + 1
                continue
            overlaps = _overlaps_existing(rect, face_id, existing) if face_id else False
        # M-4: 欄候補の residual_px は原子セル判定時に測った実測値
        # （矩形4辺と実測線分位置のずれの最大値）を使う。0.0 固定はしない
        fields.append(FieldCandidate(rect=rect, residual_px=round(cell_residual, 2),
                                     face_id=face_id, overlaps_existing=overlaps))

    excluded = tuple({"reason": k, "count": v} for k, v in excluded_counts.items())
    if not tables and not fields:
        return FrameCandidates((), (), excluded, stats, "all_filtered")
    return FrameCandidates(tuple(tables), tuple(fields), excluded, stats, None)
