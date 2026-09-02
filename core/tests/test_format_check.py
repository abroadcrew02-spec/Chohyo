"""様式判定（3値化）の純関数テスト（issue #71 (a')・08 §2.3・§2.9）。

対応する受入基準（07 §8.1）:
- AC-F05: classify() が4理由コード＋成功＋size を対応表どおりに3値化する
- AC-F06: fold() が片面 mismatch でページ mismatch になる（優先順）
- AC-F15: check_page(img, template) がスコア（[0,1]）と理由を返し、
  面切りが関数の内側で行われる
"""
import json

import pytest
from PIL import Image

from chouhyo_ocr.align import FaceDiag, ShiftEstimate
from chouhyo_ocr.format_check import (
    FaceVerdict, check_page, classify, fold, from_diag, from_faces, score_of,
)
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"


def _est(ok=False, reason="", det_h=0, det_v=0, exp_h=0, exp_v=0,
        matched_uniq=0, at_h=False, at_v=False) -> ShiftEstimate:
    """テスト用の合成 ShiftEstimate（dx/dy/matched/total は判定に無関係な
    ダミー値・classify は ok/reason/det_*_count/exp_*_uniq/matched_uniq/
    at_boundary_* しか見ない）。"""
    return ShiftEstimate(dx=0, dy=0, matched=0, total=0, ok=ok, reason=reason,
                         det_h_count=det_h, det_v_count=det_v,
                         exp_h_uniq=exp_h, exp_v_uniq=exp_v,
                         matched_uniq=matched_uniq,
                         at_boundary_h=at_h, at_boundary_v=at_v)


# ---------- AC-F05: classify() の3値化 ----------

def test_classify_success_is_match():
    v, r = classify(_est(ok=True, reason=""))
    assert (v, r) == ("match", "")


def test_classify_boundary_is_undecidable():
    v, r = classify(_est(reason="boundary"))
    assert (v, r) == ("undecidable", "boundary")


def test_classify_edge_mismatch_is_undecidable():
    """★1（08 §2.3.4）: 07 v1.1 まで「不一致」だったが実測で「判定不能」へ
    訂正（上端罫線1本の白塗りで発火するため）。07 v1.2 の対応表と一致。
    """
    v, r = classify(_est(reason="edge_mismatch"))
    assert (v, r) == ("undecidable", "edge")


def test_classify_ambiguous_is_mismatch():
    v, r = classify(_est(reason="ambiguous"))
    assert (v, r) == ("mismatch", "ambiguous")


def test_classify_few_lines_sparse_h_is_undecidable():
    """★2（軸別）: h 軸の検出線が期待線の50%未満＝「線が取れていない」。"""
    v, r = classify(_est(reason="few_lines", det_h=3, exp_h=10,
                         det_v=10, exp_v=10))
    assert (v, r) == ("undecidable", "few_lines")


def test_classify_few_lines_sparse_v_is_undecidable():
    """★2: 軸別なので v 軸だけが乏しくても判定不能になる
    （合算だと h 軸の本数に埋もれて成立しない・08 §2.3.4）。"""
    v, r = classify(_est(reason="few_lines", det_h=10, exp_h=10,
                         det_v=2, exp_v=10))
    assert (v, r) == ("undecidable", "few_lines")


def test_classify_few_lines_sufficient_and_at_boundary_is_undecidable():
    """★3: 検出は十分だが探索境界に張り付いている＝大きくズレただけの
    正しい紙かもしれない→判定不能へ倒す（08 §2.3.4）。"""
    v, r = classify(_est(reason="few_lines", det_h=10, exp_h=10,
                         det_v=10, exp_v=10, at_h=True))
    assert (v, r) == ("undecidable", "boundary")


def test_classify_few_lines_sufficient_not_at_boundary_is_mismatch():
    """線は十分あるのに期待位置と合わない＝別の紙（不一致）。"""
    v, r = classify(_est(reason="few_lines", det_h=10, exp_h=10,
                         det_v=10, exp_v=10))
    assert (v, r) == ("mismatch", "lines")


def test_classify_unknown_reason_is_undecidable_safe_side():
    v, r = classify(_est(reason="something_new"))
    assert v == "undecidable" and r == "something_new"


def test_classify_few_lines_with_no_tables_on_either_axis_is_undecidable():
    """M-4（2026-09-02 マリン指摘）: 両軸とも期待線が0本（tables を持たない
    面）は「検出十分/乏しい」を判定する母数が無い。sparse_h/sparse_v は
    exp_*_uniq>0 の条件のためどちらも False になり、直さないと素通りして
    誤って「不一致」（mismatch, lines）に倒れてしまう。
    """
    v, r = classify(_est(reason="few_lines", det_h=0, det_v=0, exp_h=0, exp_v=0))
    assert (v, r) == ("undecidable", "few_lines")


# ---------- 不変条件2/3: スコアは判定に使わず、分母は重複排除 ----------

def test_score_of_uses_deduped_denominator_not_matched_or_total():
    """front が完全な紙のとき matched=22・重複排除分母16で1.375になる
    （定義域超過）ことを避けるため、score_of は matched_uniq/(exp_h_uniq+
    exp_v_uniq) を使う——matched（連結リスト基準）は分子にも使わない。
    """
    est = _est(ok=True, exp_h=6, exp_v=10, matched_uniq=16)
    assert score_of(est) == pytest.approx(1.0)
    # matched/total を変えてもスコアに影響しない（判定に使わないことの実証）
    est2 = ShiftEstimate(dx=0, dy=0, matched=999, total=999, ok=True, reason="",
                         det_h_count=0, det_v_count=0, exp_h_uniq=6, exp_v_uniq=10,
                         matched_uniq=16, at_boundary_h=False, at_boundary_v=False)
    assert score_of(est2) == score_of(est)


def test_score_of_is_minus_one_when_denominator_is_zero():
    assert score_of(_est(exp_h=0, exp_v=0)) == -1.0


# ---------- AC-F06: fold() の畳み込み（FR-F03） ----------

def test_fold_mismatch_beats_undecidable_and_match():
    faces = (
        FaceVerdict(0, "front", "match", "", 1.0, 20, 16),
        FaceVerdict(1, "back", "mismatch", "lines", 0.2, 20, 26),
    )
    pv = fold(faces)
    assert pv.verdict == "mismatch" and pv.reason == "lines"


def test_fold_undecidable_beats_match_when_no_mismatch():
    faces = (
        FaceVerdict(0, "front", "match", "", 1.0, 20, 16),
        FaceVerdict(1, "back", "undecidable", "boundary", -1.0, 5, 26),
    )
    pv = fold(faces)
    assert pv.verdict == "undecidable" and pv.reason == "boundary"


def test_fold_all_match_is_match():
    faces = (
        FaceVerdict(0, "front", "match", "", 1.0, 20, 16),
        FaceVerdict(1, "back", "match", "", 0.9, 24, 26),
    )
    pv = fold(faces)
    assert pv.verdict == "match" and pv.score == pytest.approx(0.9)


def test_fold_excludes_unmeasured_score_from_minimum():
    """M-4（2026-09-02 マリン指摘）: score=-1.0（未計測）の面は最小値の
    母集団から除く。混ぜると「算出できなかった」が実際の最悪スコアより
    小さい数値として勝ってしまい、欠測とワースト値の意味を取り違える。
    """
    faces = (
        FaceVerdict(0, "front", "match", "", 1.0, 20, 16),
        FaceVerdict(1, "back", "match", "", -1.0, 0, 0),  # 未計測（denom 0 等）
    )
    pv = fold(faces)
    assert pv.verdict == "match"
    assert pv.score == pytest.approx(1.0)  # -1.0 に引きずられない

    # 全面が未計測なら -1.0 のまま（フォールバック）
    all_unmeasured = (
        FaceVerdict(0, "front", "match", "", -1.0, 0, 0),
        FaceVerdict(1, "back", "match", "", -1.0, 0, 0),
    )
    assert fold(all_unmeasured).score == -1.0


def test_fold_skipped_faces_do_not_affect_verdict():
    """評価に到達しなかった面（skipped）は判定に影響しない（08 §2.3.5）。"""
    faces = (
        FaceVerdict(0, "front", "mismatch", "lines", 0.1, 20, 16),
        FaceVerdict(1, "back", "skipped", "", -1.0, 0, 0),
    )
    pv = fold(faces)
    assert pv.verdict == "mismatch"
    assert pv.faces == faces  # skipped も含めて全面分を保持する


# ---------- from_diag / from_faces ----------

def test_from_diag_marks_unevaluated_faces_as_skipped():
    diag = (
        FaceDiag(0, "front", _est(reason="few_lines", det_h=10, exp_h=10,
                                  det_v=10, exp_v=10)),
        FaceDiag(1, "back", None),  # 未評価（align_page が front で先に raise）
    )
    pv = from_diag(diag)
    assert pv.verdict == "mismatch"  # front（評価済み）が代表する
    assert pv.faces[0].verdict == "mismatch"
    assert pv.faces[1].verdict == "skipped"


def test_from_faces_builds_match_page_verdict():
    class _FakeAlignedFace:
        def __init__(self, face_id, estimate):
            self.face_id = face_id
            self.estimate = estimate

    faces = [
        _FakeAlignedFace("front", _est(ok=True, exp_h=6, exp_v=10, matched_uniq=16)),
        _FakeAlignedFace("back", _est(ok=True, exp_h=15, exp_v=11, matched_uniq=26)),
    ]
    pv = from_faces(faces)
    assert pv.verdict == "match" and pv.reason == ""
    assert [f.face_id for f in pv.faces] == ["front", "back"]


def test_from_faces_treats_none_estimate_as_skipped():
    """#45 の再利用復元（_restore_alignment）由来で estimate=None が混ざる
    呼び出し元にも安全に使える（skipped 扱い）。"""
    class _FakeAlignedFace:
        def __init__(self, face_id, estimate):
            self.face_id = face_id
            self.estimate = estimate

    faces = [_FakeAlignedFace("front", None)]
    pv = from_faces(faces)
    assert pv.faces[0].verdict == "skipped"


# ---------- AC-F15: check_page ----------

@pytest.mark.skipif(not PAGE_PNG.exists(), reason="サンプル画像が無い環境")
def test_check_page_returns_score_and_reason_for_matching_page():
    """実サンプル（テンプレートと一致）は match・スコア[0,1]・面切りは内側で
    行われる（faces タプルに front/back の両方が入る）。"""
    template = load_template(TPL)
    with Image.open(PAGE_PNG) as img:
        pv = check_page(img, template)
    assert pv.verdict == "match"
    assert 0.0 <= pv.score <= 1.0
    assert {f.face_id for f in pv.faces} == {"front", "back"}


def test_check_page_returns_mismatch_for_unrelated_form():
    """同寸別様式（formC）は不一致——check_page は例外を投げず PageVerdict を
    返す（align_page と違い早期打ち切りしない・全面を評価する）。
    """
    formc = app_root() / "testdata" / "formC" / "formC-1.png"
    if not formc.exists():
        pytest.skip("formC-1.png が無い環境（testdata/formC/make_formC.py で生成）")
    template = load_template(TPL)
    with Image.open(formc) as img:
        pv = check_page(img, template)
    assert pv.verdict == "mismatch"
    # align_page と異なり早期打ち切りしないため、両面とも skipped ではない
    assert all(f.verdict != "skipped" for f in pv.faces)


def test_check_page_size_mismatch_returns_mismatch_without_calling_estimate_shift():
    """寸法不一致は estimate_shift に到達しないため呼び出し側が PageVerdict を
    直接組む（size・07 §4.1）。"""
    template = load_template(TPL)
    tiny = Image.new("RGB", (200, 250), "white")  # テンプレと比が大きく異なる
    pv = check_page(tiny, template)
    assert pv.verdict == "mismatch" and pv.reason == "size"
    assert pv.faces == ()
