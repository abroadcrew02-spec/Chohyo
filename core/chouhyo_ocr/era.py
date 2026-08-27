"""元号丸印判定（設計 §6.8）。

印字文字の外接矩形の外側〜セル境界の環状帯でインク画素比を測る。
scores だけを永続化し、5値への変換は render 時に毎回導出する
（丸印閾値は設定値・判定結果を焼き込まない）。
"""
from __future__ import annotations

import numpy as np

from .template import CellSpec

BAND_PAD = 8          # 環状帯の外側幅（px）※実物で調整
DECIDE_GAP = 0.05     # 判定不能の閾: 1位と2位のスコア差がこれ未満なら不能 ※実物で調整

UNSELECTED = "未選択"
UNDECIDED = "判定不能"


def score_cell(binary_face: "np.ndarray", cell: CellSpec) -> dict[str, float]:
    """choice セル1つの各選択肢スコア（マークの左右帯のインク画素比）。

    帯は**左右のみ**。マークは縦積みで上下の帯は隣マークの印字・行罫線を
    含んでしまい、最上段（昭）だけ行罫線ぶんのバイアスが乗る（実データで
    確認・2026-08-27）。丸囲みの弧は自マークの左右に必ず出るため、左右帯
    だけで判別できる。左右の縦罫線は全選択肢に共通のフロアとして乗るので
    分離には効かず、decide 側はスコア差で判定する。
    """
    H, W = binary_face.shape
    scores: dict[str, float] = {}
    for mark in cell.choice_marks:
        r = mark.rect
        y0, y1 = max(0, r.y), min(H, r.y + r.h)
        lx0, lx1 = max(0, r.x - BAND_PAD), max(0, r.x)
        rx0, rx1 = min(W, r.x + r.w), min(W, r.x + r.w + BAND_PAD)
        area = (y1 - y0) * ((lx1 - lx0) + (rx1 - rx0))
        if area <= 0:
            scores[mark.value] = 0.0
            continue
        ink = int(binary_face[y0:y1, lx0:lx1].sum()) + int(binary_face[y0:y1, rx0:rx1].sum())
        scores[mark.value] = ink / area
    return scores


def decide(scores: dict[str, float], era_threshold: float,
           gap: float = DECIDE_GAP) -> str:
    """scores → 昭/平/令 or 未選択/判定不能（出力上はどちらも〓・意味は別）。

    拮抗の判定は比率でなく**絶対差**。左右の縦罫線による共通フロアが
    全スコアへ一様に乗るため、比率は フロア↑ で縮んで誤って不能へ倒れる。
    """
    if not scores:
        return UNSELECTED
    # 共通フロア（左右の縦罫線など全選択肢に一様に乗るインク）を差し引く
    floor = min(scores.values())
    ranked = sorted(((k, v - floor) for k, v in scores.items()), key=lambda kv: -kv[1])
    top_val, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if top < era_threshold:
        return UNSELECTED          # 帳票の事実（丸が無い）
    if top - second < gap:
        return UNDECIDED           # ツールの能力限界（2候補が拮抗）
    return top_val
