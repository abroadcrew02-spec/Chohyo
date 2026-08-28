"""元号丸印判定（設計 §6.8）。

印字文字の外接矩形の外側〜セル境界の環状帯でインク画素比を測る。
scores だけを永続化し、5値への変換は render 時に毎回導出する
（丸印閾値は設定値・判定結果を焼き込まない）。
"""
from __future__ import annotations

import numpy as np

from .template import CellSpec

BAND_PAD = 8          # 帯の外側幅（px）※実物で調整
BAND_PAD_IN = 6       # 帯の内側幅（px・issue #23）※実物で調整
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

    帯は矩形の境界を**またぐ**（外 BAND_PAD・内 BAND_PAD_IN）。丸は印字文字へ
    きつく重ねて書かれることが多く、外側だけの帯ではインクが1画素も入らない
    ——実測で「目視では明瞭な丸なのにスコア 0.0000」が p0002 の家族欄3行で
    起きていた（issue #23）。内側6px を足すと、既存の閾値のまま実サンプル
    8箇所すべてが正解する（トップ値の最小 0.0658・1位2位差の最小 0.0647）。
    """
    H, W = binary_face.shape
    scores: dict[str, float] = {}
    for mark in cell.choice_marks:
        r = mark.rect
        y0, y1 = max(0, r.y), min(H, r.y + r.h)
        lx0, lx1 = max(0, r.x - BAND_PAD), min(W, r.x + BAND_PAD_IN)
        rx0, rx1 = max(0, r.x + r.w - BAND_PAD_IN), min(W, r.x + r.w + BAND_PAD)
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
    # 共通フロア（左右の縦罫線など全選択肢に一様に乗るインク）を差し引く。
    # 3候補以上なら最小値がフロアの近似になるが、**2候補では最小値が
    # 「選ばれなかった側」そのもの**で、引くと second が必ず 0 になり
    # 判定不能へ到達できなくなる（レビュー M-4・実測: 乱数20万件で0件）。
    # 2候補では下から2番目＝自分自身を引くことになるためフロアを引かない
    floor = min(scores.values()) if len(scores) >= 3 else 0.0
    ranked = sorted(((k, v - floor) for k, v in scores.items()), key=lambda kv: -kv[1])
    # ranked の要素は (選択肢, スコア)。以前は top_val が選択肢・top がスコアと
    # 名前が逆で、読む側が毎回 unpack を確かめる必要があった（レビュー LOW）
    top_choice, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score < era_threshold:
        return UNSELECTED          # 帳票の事実（丸が無い）
    if top_score - second_score < gap:
        return UNDECIDED           # ツールの能力限界（2候補が拮抗）
    return top_choice
