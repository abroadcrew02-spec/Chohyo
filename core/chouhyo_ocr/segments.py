"""ページ全体からの線分抽出（端点付き・設計 §4.1・FR-F47・FR-F15）。

`projection.py`（`line_positions`）は射影ピークの**中心座標**しか返さず、
端点を持たない。閉じた矩形を組むには端点が要るため、ここへ新しい原始関数
`detect_segments` を置く。`projection.py` は1行も参照・変更しない
（`estimate_shift` の検出条件を変えないため・NFR-F08）。

方式はランレングス＋隣接行マージ＋ギャップ橋渡し（S-1・08 §4.1.2）:

1. 各走査線（水平検出なら行、垂直検出なら列＝転置した行）で暗画素の
   連続ラン [x0, x1] を取る（`HOLE_MAX` 以下のかすれは同一ランへ橋渡し、
   `MIN_SEG_LEN` 未満のランは捨てる）
2. 隣接する走査線のランを重なり率 `OVERLAP_MIN` 以上で束ねる
   （束の厚みが `THICK_MAX` を超えたら塗り潰し面とみなし捨てる）
3. 束ごとに1本の線分（pos=重心・start/end=端点の最小最大）を作る
4. `COLLINEAR_TOL` 以内・x（y）の隙間 `GAP_BRIDGE` 以内の線分を橋渡しして
   1本に統合する（罫線が交差で切れる・かすれる分を吸収）

垂直の検出は `binary.T` を渡して同じ手順を通し、返り値の意味を
（pos=x・start/end=y）に読み替える。
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

BASE_DPI = 300  # 較正基準dpi（他モジュールの BASE_DPI と同じ意味）

# --- 閾値定数（設計 08 §4.1.4・すべて @300dpi の実測較正値）---------------
# 実効値は `round(定数 × dpi / BASE_DPI)` でスケールする（S-1・汎用化 A-3 と
# 同じ流儀）。ただし OVERLAP_MIN（重なり率）は無次元量——「絶対長」では
# ないため dpi でスケールしない（比率を dpi 倍すると意味が壊れる）。
# MAX_RAILS（レール数の上限）は矩形化専用の閾値のため `grid.py` 側に
# 定義を移した（レビュー LOW・2026-09-03: 線分抽出の閾値と矩形化の閾値の
# 所在を分ける）。08 §4.1.4 の表でこの2つだけ根拠欄が「—」（px 表記が無い）
# になっているのは変わらない。

MIN_SEG_LEN = 60
"""px@300dpi。線分として成立する最小の連続暗画素長。
formB の最小欄「受付日」は 300×80px で、短辺 80px がこれを上回る。
文字のストロークは 3〜10px 程度なので、60px 連続の暗画素が1行に並ぶのは
罫線・下線・塗り潰しに限られる。"""

HOLE_MAX = 4
"""px@300dpi。1本のラン内で許容するかすれ（欠け）の最大幅。
`projection.LINE_GAP`(6) より小さく取る——あちらは「別の線を同一視する」
量、こちらは「1本の中の欠け」なので同じ量にしない。"""

THICK_MAX = 12
"""px@300dpi。これを超える帯厚（束ねた走査線の本数）は罫線ではなく
塗り潰し（綴じ穴帯・黒ベタ）とみなして捨てる。"""

COLLINEAR_TOL = 2
"""px@300dpi。同一線とみなす pos（y または x）の差。`estimate_shift` の
端一致許容（±2px・align.py の hit()）と同じ流儀。"""

GAP_BRIDGE = 12
"""px@300dpi。交差切れの橋渡し距離。列間隙が 0 の現存テンプレートでも
隣の欄を巻き込まない大きさ。"""

MIN_RECT_SIZE = 20
"""px@300dpi。候補矩形の最小辺（grid.detect_frames の除外規則で使用）。"""

OVERLAP_MIN = 0.5
"""無次元。隣接する走査線のランを同じ線分へ束ねる重なり率の下限。"""


def scale_threshold(base: int, dpi: int) -> int:
    """px@300dpi 定数を dpi へスケールする。既定 dpi=BASE_DPI なら base のまま。"""
    return max(1, round(base * dpi / BASE_DPI))


@dataclass(frozen=True)
class Segment:
    """1本の線分（端点付き）。

    kind="h": pos=y（行位置）・start/end=x0/x1（左右端）。
    kind="v": pos=x（列位置）・start/end=y0/y1（上下端）。
    thickness は束ねた走査線の本数（診断・THICK_MAX 判定の記録用）。
    """
    kind: str  # "h" | "v"
    pos: float
    start: int
    end: int
    thickness: int


def _bridge_gaps(mask: "np.ndarray", hole_max: int) -> "np.ndarray":
    """1次元 bool 配列（True=インク）の、長さ <= hole_max の False ランを埋める。"""
    if hole_max <= 0 or mask.size == 0:
        return mask
    m = mask.astype(np.int8)
    if m[0] == 0 and m[-1] == 0 and m.sum() == 0:
        return mask
    diff = np.diff(m)
    starts = np.flatnonzero(diff == -1) + 1   # True->False の切替点（False ラン開始）
    ends = np.flatnonzero(diff == 1) + 1      # False->True の切替点（False ラン終了）
    if m[0] == 0:
        starts = np.concatenate(([0], starts))
    if m[-1] == 0:
        ends = np.concatenate((ends, [len(m)]))
    out = mask.copy()
    for s, e in zip(starts, ends):
        if e - s <= hole_max:
            out[s:e] = True
    return out


def _true_runs(mask: "np.ndarray", min_len: int) -> list[tuple[int, int]]:
    """埋め込み済み bool 配列から True ラン [start, end]（両端含む）を抽出する。

    長さ (end-start+1) >= min_len のものだけ返す。
    """
    if mask.size == 0 or not mask.any():
        return []
    m = mask.astype(np.int8)
    diff = np.diff(m)
    starts = np.flatnonzero(diff == 1) + 1
    ends = np.flatnonzero(diff == -1) + 1
    if m[0] == 1:
        starts = np.concatenate(([0], starts))
    if m[-1] == 1:
        ends = np.concatenate((ends, [len(m)]))
    return [(int(s), int(e - 1)) for s, e in zip(starts, ends) if (e - s) >= min_len]


class UnionFind:
    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb


def _overlap_ratio(a0: int, a1: int, b0: int, b1: int) -> float:
    inter = min(a1, b1) - max(a0, b0) + 1
    if inter <= 0:
        return 0.0
    return inter / min(a1 - a0 + 1, b1 - b0 + 1)


def _detect_axis(binary: "np.ndarray", dpi: int) -> list[tuple[float, int, int, int]]:
    """binary の行方向に線分を検出する（水平検出。垂直は呼び出し元が転置して渡す）。

    戻り値は (pos, start, end, thickness) のタプル配列（kind なし）。
    """
    min_len = scale_threshold(MIN_SEG_LEN, dpi)
    hole_max = scale_threshold(HOLE_MAX, dpi)
    thick_max = scale_threshold(THICK_MAX, dpi)
    collinear_tol = scale_threshold(COLLINEAR_TOL, dpi)
    gap_bridge = scale_threshold(GAP_BRIDGE, dpi)

    h = binary.shape[0]
    # --- 手順1: 各行の連続ラン抽出（かすれ橋渡し込み） ---
    row_runs: list[list[tuple[int, int]]] = []
    for y in range(h):
        row = binary[y]
        if not row.any():
            row_runs.append([])
            continue
        bridged = _bridge_gaps(row, hole_max)
        row_runs.append(_true_runs(bridged, min_len))

    # --- 手順2: 隣接行のランを束ねる（Union-Find） ---
    flat: list[tuple[int, int, int]] = []  # (y, x0, x1)
    row_base: list[int] = []
    idx = 0
    for y, runs in enumerate(row_runs):
        row_base.append(idx)
        for r in runs:
            flat.append((y, r[0], r[1]))
            idx += 1
    n = len(flat)
    if n == 0:
        return []
    uf = UnionFind(n)
    for y in range(h - 1):
        cur = row_runs[y]
        nxt = row_runs[y + 1]
        if not cur or not nxt:
            continue
        cur_base, nxt_base = row_base[y], row_base[y + 1]
        for i, (a0, a1) in enumerate(cur):
            for j, (b0, b1) in enumerate(nxt):
                if _overlap_ratio(a0, a1, b0, b1) >= OVERLAP_MIN:
                    uf.union(cur_base + i, nxt_base + j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    bundled: list[tuple[float, int, int, int]] = []
    for members in groups.values():
        ys = [flat[i][0] for i in members]
        thickness = len(set(ys))
        if thickness > thick_max:
            continue  # 塗り潰し面（罫線ではない）
        x0 = min(flat[i][1] for i in members)
        x1 = max(flat[i][2] for i in members)
        pos = round(sum(ys) / len(ys), 1)
        bundled.append((pos, x0, x1, thickness))

    # --- 手順4: 同一線の橋渡し（COLLINEAR_TOL・GAP_BRIDGE） ---
    bundled.sort(key=lambda s: s[0])
    merged: list[list] = []
    for pos, x0, x1, thick in bundled:
        placed = False
        for m in merged:
            if abs(m[0] - pos) > collinear_tol:
                continue
            if x0 > m[2]:
                gap = x0 - m[2] - 1
            elif m[1] > x1:
                gap = m[1] - x1 - 1
            else:
                gap = 0  # 重なっている／接している
            if gap <= gap_bridge:
                len_m = m[2] - m[1] + 1
                len_new = x1 - x0 + 1
                m[0] = round((m[0] * len_m + pos * len_new) / (len_m + len_new), 1)
                m[1] = min(m[1], x0)
                m[2] = max(m[2], x1)
                m[3] = max(m[3], thick)
                placed = True
                break
        if not placed:
            merged.append([pos, x0, x1, thick])

    return [(m[0], m[1], m[2], m[3]) for m in merged]


def detect_segments(binary: "np.ndarray", dpi: int = BASE_DPI
                    ) -> tuple[list[Segment], list[Segment]]:
    """二値画像（True=インク）から水平・垂直の線分を端点付きで抽出する（FR-F47）。

    `dpi` はこの画像の render_dpi（汎用化 A-3）。閾値は `scale_threshold` で dpi に
    合わせてスケールする。既定 dpi=BASE_DPI(300) のときは表の値そのもの。

    戻り値: (h_segments, v_segments)。h は pos=y・start/end=x0/x1、
    v は pos=x・start/end=y0/y1（転置して同じ処理を通し、意味を読み替える）。
    """
    h_raw = _detect_axis(binary, dpi)
    v_raw = _detect_axis(np.ascontiguousarray(binary.T), dpi)
    h_segments = [Segment("h", pos, x0, x1, thick) for pos, x0, x1, thick in h_raw]
    v_segments = [Segment("v", pos, x0, x1, thick) for pos, x0, x1, thick in v_raw]
    return h_segments, v_segments
