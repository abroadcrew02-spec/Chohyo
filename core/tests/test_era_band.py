"""元号スコアの測定帯が隣の欄へ食い込まないこと（issue #52 M-1）。

合成画像だけで完結する（実サンプル素材に依存しない）。較正そのもの
——実際の帳票で 8/8 正解すること——は test_era_calibration.py が見る。

背景: 帯は面サイズにしかクランプされておらず、出荷テンプレートの
family_01_生年月日_元号 では右帯 14px のうち 9px が隣の手書き日付欄の内側に
入っていた（実測・2026-08-31）。隣欄の左端に書かれた数字が特定のマークの帯に
だけ乗ると、丸の有無ではなく隣の記入内容で順位が動く。
"""
import numpy as np

from chouhyo_ocr import era
from chouhyo_ocr.template import CellSpec, ChoiceMark, Rect

# 出荷テンプレの family 欄と同じ形（欄 w=50・マーク w=36・右端がほぼ接する）を
# 300x300 の面へ縮尺なしで置いたもの
CELL_RECT = Rect(100, 100, 50, 105)          # 右端 150
MARK_RECT = Rect(115, 102, 36, 32)           # 右端 151
# 右帯は [MARK 右端 - BAND_PAD_IN, MARK 右端 + BAND_PAD) = [145, 159)
BAND_R0 = MARK_RECT.x + MARK_RECT.w - era.BAND_PAD_IN     # 145
BAND_R1 = MARK_RECT.x + MARK_RECT.w + era.BAND_PAD        # 159
SLACK_EDGE = CELL_RECT.x + CELL_RECT.w + era.BAND_OUT_SLACK   # 154
NEIGHBOR = Rect(152, 100, 50, 105)           # 余白 [150,152) の先に隣欄が始まる


def _cell(field_id="era"):
    return CellSpec(field_id, "f", CELL_RECT, "choice",
                    choice_marks=(ChoiceMark("昭", MARK_RECT),
                                  ChoiceMark("平", Rect(MARK_RECT.x,
                                                        MARK_RECT.y + 40,
                                                        MARK_RECT.w,
                                                        MARK_RECT.h))))


def _face_with_ink(x0, x1):
    """[x0,x1) の縦帯だけにインクがある面。"""
    face = np.zeros((300, 300), dtype=np.uint8)
    face[MARK_RECT.y:MARK_RECT.y + MARK_RECT.h, x0:x1] = 1
    return face


def test_ink_inside_the_cell_is_measured():
    """欄の内側（帯の本体）はこれまでどおり数える。"""
    scores = era.score_cell(_face_with_ink(BAND_R0, CELL_RECT.x + CELL_RECT.w),
                            _cell())
    assert scores["昭"] > 0


def test_ink_in_the_margin_around_the_cell_is_still_measured():
    """欄の外でも BAND_OUT_SLACK までは数える。

    手書きの丸は罫線の枠をわずかに越える。ここを 0 にすると、出荷テンプレの
    person_生年月日_元号 で丸が 3px はみ出ている実データが判定不能へ落ちる
    （2026-09-03 実測: トップ値 0.0676 → 0.0477・era_threshold=0.05 を割る）。
    """
    margin = _face_with_ink(CELL_RECT.x + CELL_RECT.w, SLACK_EDGE)
    assert era.score_cell(margin, _cell())["昭"] > 0


def test_ink_far_outside_the_cell_is_not_measured():
    """BAND_OUT_SLACK より外は、帯の幅が残っていても数えない。"""
    far = _face_with_ink(SLACK_EDGE, BAND_R1)
    assert era.score_cell(far, _cell())["昭"] == 0


def test_neighbouring_cell_is_excluded_when_occluders_are_given():
    """隣の欄の記入は、occluders を渡せば 1px も帯に入らない（M-1 の本題）。"""
    cells = [_cell(), CellSpec("となりの手書き欄", "f", NEIGHBOR, "text")]
    occ = era.occluders_for(cells, cells[0])
    ink_in_neighbour = _face_with_ink(NEIGHBOR.x, SLACK_EDGE)

    assert era.score_cell(ink_in_neighbour, cells[0])["昭"] > 0, \
        "前提が崩れている（occluders なしでは隣欄のインクを拾う）"
    assert era.score_cell(ink_in_neighbour, cells[0], occ)["昭"] == 0


def test_margin_outside_the_neighbour_survives_occluders():
    """隣欄に属さない余白は occluders を渡しても残す（過剰に削らない）。"""
    cells = [_cell(), CellSpec("となりの手書き欄", "f", NEIGHBOR, "text")]
    occ = era.occluders_for(cells, cells[0])
    margin = _face_with_ink(CELL_RECT.x + CELL_RECT.w, NEIGHBOR.x)
    assert era.score_cell(margin, cells[0], occ)["昭"] > 0


def test_occluders_for_collects_only_other_cells_of_the_same_face():
    """自分自身・別の面は除く。他欄の追加領域と参照先は含める。"""
    me = _cell()
    other = CellSpec("other", "f", Rect(200, 200, 20, 20), "text",
                     extra_rects=(Rect(230, 200, 20, 20),),
                     fallback_rect=Rect(260, 200, 20, 20))
    back = CellSpec("back", "back_face", Rect(0, 0, 20, 20), "text")
    occ = era.occluders_for([me, other, back], me)
    assert CELL_RECT not in occ
    assert Rect(0, 0, 20, 20) not in occ
    assert set(occ) == {Rect(200, 200, 20, 20), Rect(230, 200, 20, 20),
                        Rect(260, 200, 20, 20)}


def test_scores_stay_a_ratio_even_when_the_band_is_fully_trimmed():
    """帯が全部削れても 0 を返す（面積が負・0除算にならない）。"""
    # 欄の全周を覆う occluder を渡すと、左右の帯はどちらも幅 0 になりうる
    cells = [_cell(),
             CellSpec("left", "f", Rect(0, 100, CELL_RECT.x, 105), "text"),
             CellSpec("right", "f", Rect(CELL_RECT.x + CELL_RECT.w, 100,
                                         100, 105), "text")]
    occ = era.occluders_for(cells, cells[0])
    scores = era.score_cell(_face_with_ink(BAND_R0, BAND_R1), cells[0], occ)
    assert all(0.0 <= v <= 1.0 for v in scores.values())
