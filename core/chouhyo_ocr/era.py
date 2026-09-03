"""元号丸印判定（設計 §6.8）。

印字文字の外接矩形の外側〜セル境界の環状帯でインク画素比を測る。
scores だけを永続化し、5値への変換は render 時に毎回導出する
（丸印閾値は設定値・判定結果を焼き込まない）。
"""
from __future__ import annotations

from typing import Sequence

import numpy as np

from .template import CellSpec, Rect

BAND_PAD = 8          # 帯の外側幅（px）※実物で調整
BAND_PAD_IN = 6       # 帯の内側幅（px・issue #23）※実物で調整
DECIDE_GAP = 0.05     # 判定不能の閾: 1位と2位のスコア差がこれ未満なら不能 ※実物で調整

# 帯が欄の矩形の外へ出てよい幅（px・BASE_DPI=300 較正・issue #52 M-1）。
# 手書きの丸は罫線の枠をわずかに越えて描かれるため 0 にはできない: 出荷テンプレの
# person_生年月日_元号（欄 x=1758 w=75・右端 1833）では 平 の丸が右へ 3px はみ出て
# おり、slack=0 で切ると当該セルのトップ値が 0.0676→0.0477 と era_threshold=0.05 を
# 割り、判定が 平→未選択 へ反転する（2サンプル8箇所で 8/8→7/8・2026-09-03 実測）。
# 実測スイープ（slack=0..9）では 3〜8 が同じ最良値（min_top 0.0676・min_gap 0.0669）で
# 頭打ちになる。その平坦域から、既に較正済みの同種の許容値
# （template.CHOICE_MARK_MARGIN_PX＝マークが欄からはみ出してよい幅）と同じ 4 を採る
BAND_OUT_SLACK = 4

UNSELECTED = "未選択"
UNDECIDED = "判定不能"


def occluders_for(cells: Sequence[CellSpec], cell: CellSpec) -> tuple[Rect, ...]:
    """`cell` の帯から必ず除くべき矩形（同じ面の**他の欄**の受け皿）を集める。

    「他の欄が記入を受け取る場所」＝その欄の全領域（主＋追加）と参照先の枠。
    自分自身は含めない。load_template が同一面のセル矩形の重なりを拒否している
    （issue #24）ため、ここで集めた矩形は自欄の rect とは交わらない——帯のうち
    自欄の外へ出た部分だけがトリムされる。

    score_cell へ渡さなければ従来どおり（BAND_OUT_SLACK までのはみ出しを許す）。
    """
    out: list[Rect] = []
    for other in cells:
        if other.field_id == cell.field_id or other.face_id != cell.face_id:
            continue
        out.extend(other.all_rects())
        if other.fallback_rect is not None:
            out.append(other.fallback_rect)
    return tuple(out)


def _trim(x0: int, x1: int, y0: int, y1: int,
          occluders: Sequence[Rect]) -> tuple[int, int]:
    """水平区間 [x0,x1) から、y で重なる occluder に食われた端を削る。

    帯は横方向にしか伸びないので、削るのも x の両端だけ。occluder が区間の
    内側だけを塞ぐ（帯を2つに割る）形は、セル矩形の重なり禁止（issue #24）と
    「帯は自欄からはみ出した分しか外へ出ない」ことから起こらない。
    """
    for r in occluders:
        if min(y1, r.y + r.h) <= max(y0, r.y):
            continue                       # y が重ならない＝この帯には関係ない
        if r.x <= x0 < r.x + r.w:
            x0 = max(x0, r.x + r.w)
        if r.x < x1 <= r.x + r.w:
            x1 = min(x1, r.x)
    return x0, x1


def score_cell(binary_face: "np.ndarray", cell: CellSpec,
               occluders: Sequence[Rect] = ()) -> dict[str, float]:
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

    帯は面サイズだけでなく**欄の矩形でもクランプする**（issue #52 M-1）。
    面サイズだけで切ると外側 BAND_PAD 分が隣の欄へそのまま伸びる: 出荷
    テンプレート family_01_生年月日_元号（欄 x=1060 w=50）の右帯は
    x=[1105,1119) で、うち 9px が隣の手書き日付欄（x=1110 から）の内側に入る
    ——隣欄の左端寄りに書かれた数字が特定のマークの帯にだけ乗ると、丸の有無
    ではなく隣の記入内容で順位が動きうる。issue #24 が「重なり帯へ落ちた
    symbol の行き先が定義順で決まるのは危険」としてセル矩形の重なりを拒否して
    いるのと同じ原則を、丸印スコアの測定窓にも適用する。

    クランプの外周は「欄の矩形を BAND_OUT_SLACK だけ広げたもの」。0 にすると
    枠をわずかに越える手書きの丸そのものを削って判定が反転する（定数の
    コメント参照）。`occluders`（occluders_for が返す他欄の受け皿）を渡すと、
    はみ出しが許されるのは**どの欄にも属さない余白だけ**になり、隣欄への
    食い込みは 0px になる。省略時は余白と隣欄を区別しないため、上記の
    family 欄では 9px の食い込みが 4px まで縮む（完全には消えない）。
    """
    H, W = binary_face.shape
    # 帯の外周（面ローカル）。マークが欄からはみ出すことは load_template が
    # CHOICE_MARK_MARGIN_PX まででしか認めないため、これで帯が空になることはない
    cx0 = max(0, cell.rect.x - BAND_OUT_SLACK)
    cx1 = min(W, cell.rect.x + cell.rect.w + BAND_OUT_SLACK)
    cy0 = max(0, cell.rect.y - BAND_OUT_SLACK)
    cy1 = min(H, cell.rect.y + cell.rect.h + BAND_OUT_SLACK)
    scores: dict[str, float] = {}
    for mark in cell.choice_marks:
        r = mark.rect
        y0, y1 = max(cy0, r.y), min(cy1, r.y + r.h)
        spans = [
            _trim(max(cx0, r.x - BAND_PAD), min(cx1, r.x + BAND_PAD_IN),
                  y0, y1, occluders),
            _trim(max(cx0, r.x + r.w - BAND_PAD_IN), min(cx1, r.x + r.w + BAND_PAD),
                  y0, y1, occluders),
        ]
        # クランプ・トリムで区間が反転しうる。幅・高さを負のまま面積に使うと
        # area が過小・負になり、インク比が跳ねる
        area = max(0, y1 - y0) * sum(max(0, b - a) for a, b in spans)
        if area <= 0:
            scores[mark.value] = 0.0
            continue
        ink = sum(int(binary_face[y0:y1, a:b].sum()) for a, b in spans if b > a)
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
