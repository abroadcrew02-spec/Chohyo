"""前処理を候補間で共有しても判定が変わらないことの固定（issue #82・08 §3.3.4）。

`format_check.PageContext` は `check_page` の前処理（ページの正規化・探索余白
つきキャンバス・面の切り出し・グレースケール化・Otsu・傾き推定・二値化）を
幾何をキーに候補間で使い回す。ここで固定するのは:

1. **`align._face_estimate` と同一の `ShiftEstimate` を出す**（分離が純粋な
   関数分割であること・NFR-F08）。`align.py` 側の手順が変わればこのテストが
   落ちる——複製した手順が黙って古くなるのを防ぐための固定
2. **素材×テンプレートの全組み合わせで `PageVerdict` が分離前と一致する**
   （3値・スコア・理由コード・detected/expected・面ごとの内訳まで）。傾いた
   入力（回転して二値化をやり直す枝）も別途通す
3. 別ファイルとして読んだ同一幾何のテンプレート間で前処理が実際に使い回される
4. 幾何が候補ごとに違ってもキャッシュ上限で壊れない・寸法不一致は前処理に
   到達しない

素材は sample-1（実サンプル）・formB-1・formC-1（いずれもローカル生成物で
git には入らない）。無い環境では skip する（計測できないことを黙って PASS に
しない）。
"""
import pytest
from PIL import Image

from chouhyo_ocr import format_check
from chouhyo_ocr.align import _face_estimate, page_size_verdict
from chouhyo_ocr.format_check import (
    FaceVerdict, PageContext, PageVerdict, check_page, classify, fold, score_of,
)
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

ROOT = app_root()
MATERIALS = {
    "sample-1": ROOT / "workdir" / "pages" / "sample-1.png",
    "formB-1": ROOT / "testdata" / "formB" / "formB-1.png",
    "formC-1": ROOT / "testdata" / "formC" / "formC-1.png",
}
TEMPLATES = {
    "shipped": ROOT / "templates" / "chouhyo-v1.json",
    "formB": ROOT / "testdata" / "formB" / "formB-v1.json",
}
COMBOS = [(m, t) for m in MATERIALS for t in TEMPLATES]


def _unsplit_check(page_img, template) -> PageVerdict:
    """分離前の `check_page`（ページごとに前処理をやり直す実装）。

    `align._face_estimate` をそのまま呼ぶ——期待値を配列で書き下すのではなく
    align.py の現在の実装から導くことで、align 側が変わったときに
    `PageContext` の複製手順が取り残されたことを検出できる。
    """
    W, H = template.image_size
    if page_size_verdict(page_img.size, template) is not None:
        return PageVerdict("mismatch", "size", -1.0, ())
    page = page_img.convert("RGB").resize((W, H))
    pad = max((max(f.shift_limits) for f in template.faces), default=0)
    padded = Image.new("RGB", (W + 2 * pad, H + 2 * pad), "white")
    padded.paste(page, (pad, pad))
    face_verdicts = []
    for idx, face in enumerate(template.faces):
        est, _big, _angle = _face_estimate(padded, face, template, pad)
        v, r = classify(est)
        face_verdicts.append(FaceVerdict(
            idx, face.face_id, v, r, score_of(est),
            est.det_h_count + est.det_v_count,
            est.exp_h_uniq + est.exp_v_uniq))
    return fold(face_verdicts)


def _open(name):
    p = MATERIALS[name]
    if not p.exists():
        pytest.skip(f"{p.name} が無い環境（formC は make_formC.py で生成）")
    img = Image.open(p)
    img.load()
    return img


@pytest.mark.parametrize("material,tpl_name", COMBOS)
def test_shared_prep_matches_unsplit_implementation(material, tpl_name):
    """素材×テンプレートの全組み合わせで、分離後の判定が分離前と一致する。"""
    template = load_template(TEMPLATES[tpl_name])
    with _open(material) as img:
        expected = _unsplit_check(img, template)
        assert check_page(img, template) == expected      # 互換ラッパ経由
        ctx = PageContext(img)
        assert ctx.check(template) == expected            # 初回（キャッシュ充填）
        assert ctx.check(template) == expected            # 2回目（キャッシュ命中）


def test_shared_prep_matches_unsplit_implementation_on_skewed_input():
    """傾いた入力（回転して二値化をやり直す分岐）でも分離前と一致する。

    手持ちの素材はどれもデジタル生成で傾き 0——`_face_estimate` のうち
    「angle != 0 なら big を回してグレースケールと Otsu を取り直す」枝が
    実素材では通らない。`PageContext` が複製しているのはまさにこの手順なので、
    入力を傾けて枝を通した状態で一致を固定する。
    """
    from chouhyo_ocr import align

    template = load_template(TEMPLATES["shipped"])
    with _open("sample-1") as img:
        skewed = img.rotate(1.0, expand=False, fillcolor="white",
                            resample=Image.BICUBIC)

    seen = []
    real = align._deskew_angle

    def spy(binary):
        angle = real(binary)
        seen.append(angle)
        return angle

    align._deskew_angle = spy
    try:
        expected = _unsplit_check(skewed, template)
        assert any(a != 0.0 for a in seen), "傾き推定が 0 のままで回転の枝を通っていない"
        assert PageContext(skewed).check(template) == expected
    finally:
        align._deskew_angle = real


def test_shared_prep_is_reused_across_separately_loaded_templates(tmp_path):
    """別ファイルとして読み込んだ同一幾何のテンプレートでも前処理を使い回す。

    (t) の候補は「出荷テンプレートを写して一部だけ直した利用者テンプレート」が
    主なので、幾何が一致する候補間で前処理が共有されることが効果の本体になる。
    面の前処理（傾き推定）が呼ばれた回数で確認する。
    """
    import shutil

    src = TEMPLATES["shipped"]
    copy = tmp_path / "copy.json"
    shutil.copy(src, copy)
    templates = [load_template(src), load_template(copy)]

    calls = []
    real = format_check.PageContext._face_binary

    def counting(self, padded, face, template, pad):
        before = len(self._binary)
        out = real(self, padded, face, template, pad)
        calls.append(len(self._binary) != before)   # True = 新規計算
        return out

    with _open("sample-1") as img:
        ctx = PageContext(img)
        format_check.PageContext._face_binary = counting
        try:
            verdicts = [ctx.check(t) for t in templates]
        finally:
            format_check.PageContext._face_binary = real

    assert verdicts[0] == verdicts[1]
    # 2 面 x 2 テンプレート = 4 回呼ばれ、新規計算は最初の 2 回だけ
    assert calls == [True, True, False, False]


def test_page_context_is_bounded_and_still_correct():
    """幾何が候補ごとに違ってもキャッシュ上限で壊れない（正しさは保つ）。"""
    shipped = load_template(TEMPLATES["shipped"])
    formb = load_template(TEMPLATES["formB"])
    with _open("sample-1") as img:
        ctx = PageContext(img)
        # 幾何の違う 2 種を交互に照合しても、単発呼び出しと同じ結果になる
        for _ in range(3):
            assert ctx.check(shipped) == check_page(img, shipped)
            assert ctx.check(formb) == check_page(img, formb)
        assert len(ctx._padded) <= format_check._PADDED_CACHE_MAX
        assert len(ctx._prep) <= format_check._FACE_CACHE_MAX
        assert len(ctx._binary) <= format_check._FACE_CACHE_MAX


def test_size_mismatch_short_circuits_before_preprocessing():
    """寸法不一致は前処理に到達しない（候補ループで無駄な 30MB 確保をしない）。"""
    template = load_template(TEMPLATES["shipped"])
    tiny = Image.new("RGB", (200, 250), "white")
    ctx = PageContext(tiny)
    pv = ctx.check(template)
    assert (pv.verdict, pv.reason, pv.faces) == ("mismatch", "size", ())
    assert not ctx._padded and not ctx._prep and not ctx._binary
