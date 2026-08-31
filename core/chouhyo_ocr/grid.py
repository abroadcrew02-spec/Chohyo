"""枠候補の生成（設計 §6.9・`detect-grid`）。

生成器は2つ。`RuledLineGrid`（罫線の射影検出）と `UniformGrid`（等分割）。
どちらも同じ GridFit を返し、テンプレート編集画面は生成器の違いを知らない。
検出の当てはめ残差（最大ずれ px）を返し、大きいときは利用者が等分割へ
切り替える判断材料にする。

罫線検出はテンプレート較正（2026-08-27）で実証した射影方式:
行方向・列方向の暗画素射影で被覆率の高い帯を線とみなす。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .pipeline_errors import OperationRefused
from .projection import H_COVERAGE, V_COVERAGE, line_positions

ROW_INSET = 4       # 行高 = ピッチ − 罫線ぶんの控え（候補値・編集画面で調整）


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


def detect_ruled(gray: "np.ndarray", region: tuple[int, int, int, int]) -> GridFit | None:
    """罫線からテーブル定義を当てはめる。線が足りなければ None。"""
    x, y, w, h = region
    seg = gray[y:y + h, x:x + w] < 128

    h_lines = line_positions(seg.sum(axis=1), w * H_COVERAGE)
    if len(h_lines) < 3:          # 行を1つ作るにも上下の線＋もう1本要る
        return None
    v_lines = line_positions(seg[h_lines[0]:h_lines[-1], :].sum(axis=0),
                             (h_lines[-1] - h_lines[0]) * V_COVERAGE)
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
        row_height=max(1, int(pitch) - ROW_INSET),
        columns=columns,
        residual_px=round(float(residual_h), 2),
    )


def make_uniform(region: tuple[int, int, int, int], rows: int, cols: int) -> GridFit:
    """外枠＋行数・列数の等分割。Q-03 に依存せず常に成立する退避先。

    行数・列数は編集画面の入力欄と CLI の --rows/--cols からそのまま渡る。
    0 や負値だと ZeroDivisionError／空の列定義になり、CLI の最上位ハンドラで
    「ERROR ZeroDivisionError: 処理を中止しました」に潰れて何を直せばよいか
    分からなくなる。業務的な拒否として明示的に断る（レビュー4巡目 LOW）。
    """
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
        row_height=max(1, int(pitch) - ROW_INSET),
        columns=columns,
        residual_px=0.0,
    )
