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

H_COVERAGE = 0.50   # 水平線: 行射影の被覆率下限
V_COVERAGE = 0.35   # 垂直線: 列射影の被覆率下限（かすれ・交差切れに寛容）
LINE_GAP = 6        # 同一線とみなす画素間隔
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


def _lines(profile: "np.ndarray", threshold: float) -> list[int]:
    idx = np.where(profile > threshold)[0]
    if len(idx) == 0:
        return []
    groups: list[list[int]] = [[int(idx[0])]]
    for i in idx[1:]:
        if i - groups[-1][-1] <= LINE_GAP:
            groups[-1].append(int(i))
        else:
            groups.append([int(i)])
    return [int(np.mean(g)) for g in groups]


def detect_ruled(gray: "np.ndarray", region: tuple[int, int, int, int]) -> GridFit | None:
    """罫線からテーブル定義を当てはめる。線が足りなければ None。"""
    x, y, w, h = region
    seg = gray[y:y + h, x:x + w] < 128

    h_lines = _lines(seg.sum(axis=1), w * H_COVERAGE)
    if len(h_lines) < 3:          # 行を1つ作るにも上下の線＋もう1本要る
        return None
    v_lines = _lines(seg[h_lines[0]:h_lines[-1], :].sum(axis=0),
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
    """外枠＋行数・列数の等分割。Q-03 に依存せず常に成立する退避先。"""
    x, y, w, h = region
    pitch = h / rows
    width = w // cols
    columns = [{"x_offset": i * width, "width": width} for i in range(cols)]
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
