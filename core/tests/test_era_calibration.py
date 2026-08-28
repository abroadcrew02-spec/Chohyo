"""元号丸印判定の較正（issue #23）。

実サンプル2ページの丸印8箇所すべてを正解する。正解は切り出し画像の目視で
確定させたもの（2026-08-28）:
  sample-1: person=平 family=平/昭/昭
  sample-2: person=平 family=平/令/令

**設計書 v2.0 の「page2 の家族3行は丸なし＝未選択が正しい」は誤りだった。**
3行とも丸があり、旧実装はそれを取りこぼしていた（丸が印字文字へきつく
重ねて書かれ、矩形の外側だけの帯にインクが1画素も入らなかった）。
「5/5 正解」の実績は、誤った正解表に対する 5/8 だった。
"""
import pytest
from PIL import Image

from chouhyo_ocr import era
from chouhyo_ocr.align import align_page
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL_PATH = app_root() / "templates" / "chouhyo-v1.json"
PAGES = app_root() / "workdir" / "pages"

TRUTH = {
    "sample-1.png": {"person_生年月日_元号": "平",
                     "family_01_生年月日_元号": "平",
                     "family_02_生年月日_元号": "昭",
                     "family_03_生年月日_元号": "昭"},
    "sample-2.png": {"person_生年月日_元号": "平",
                     "family_01_生年月日_元号": "平",
                     "family_02_生年月日_元号": "令",
                     "family_03_生年月日_元号": "令"},
}

pytestmark = pytest.mark.skipif(
    not (PAGES / "sample-1.png").exists(), reason="サンプル画像が無い環境")


@pytest.mark.parametrize("png", sorted(TRUTH))
def test_all_marks_decided_correctly(png):
    """丸印の判定が目視の正解と全て一致する（〓・誤選択ともゼロ）。"""
    template = load_template(TPL_PATH)
    faces = {f.face_id: f.binary for f in align_page(Image.open(PAGES / png),
                                                     template)[0]}
    expected = TRUTH[png]
    got = {}
    for cell in template.cells:
        if cell.kind != "choice" or cell.field_id not in expected:
            continue
        scores = era.score_cell(faces[cell.face_id], cell)
        got[cell.field_id] = era.decide(scores, 0.05)
    assert got == expected


def test_margin_is_not_marginal():
    """閾値に対する余裕を確認する（実測: トップ 0.0658 / 差 0.0647 以上）。

    ぎりぎりで通っていると、実物データのわずかな差で崩れる。較正を変える
    ときはこの余裕が縮んでいないかを見る。
    """
    template = load_template(TPL_PATH)
    tops, gaps = [], []
    for png, expected in TRUTH.items():
        faces = {f.face_id: f.binary
                 for f in align_page(Image.open(PAGES / png), template)[0]}
        for cell in template.cells:
            if cell.kind != "choice" or cell.field_id not in expected:
                continue
            scores = era.score_cell(faces[cell.face_id], cell)
            floor = min(scores.values())
            ranked = sorted((v - floor for v in scores.values()), reverse=True)
            tops.append(ranked[0])
            gaps.append(ranked[0] - ranked[1])
    assert min(tops) > 0.05, f"トップ値が閾値に近すぎる: {min(tops):.4f}"
    assert min(gaps) > 0.05, f"1位2位差が gap に近すぎる: {min(gaps):.4f}"
