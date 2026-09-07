"""render_rows.format_mismatch_ratio: D-15 の実効閾値スケーリング（issue #103）。

疎なテンプレート（表のない面）では印字ラベルの大半が枠外（other）に落ち、
固定閾値 0.55 だけでは正常ページまで様式不一致に倒れる（実測: 欄14だけの
面で other/total=0.531、合成ケースCで印字:記入比 1.25 のとき 0.556 が
閾値をまたぐ）。format_mismatch_ratio はテンプレートの「枠が覆う面積比」が
低いテンプレートに限って実効閾値を引き上げる。出荷テンプレートは面積比が
高いため常に従来どおりの閾値を返す（golden バイト一致の前提）。
"""
import pytest

from chouhyo_ocr import render_rows
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import CellSpec, Face, Rect, Template, load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


@pytest.fixture(scope="module")
def shipped_template():
    return load_template(TPL)


def _synthetic_template(face_w: int, face_h: int,
                        cell_boxes: list[tuple[int, int, int, int]]) -> Template:
    """1面だけの最小テンプレート（helpers_geom.py と同じ流儀の直接構築）。

    cell_boxes: [(x, y, w, h), ...]。field_id は連番で振る。
    """
    face = Face(face_id="front", page_offset=0,
               source_rect=Rect(0, 0, face_w, face_h))
    cells = tuple(
        CellSpec(field_id=f"f{i}", face_id="front", rect=Rect(x, y, w, h),
                kind="text")
        for i, (x, y, w, h) in enumerate(cell_boxes))
    return Template(template_id="synthetic", render_dpi=300,
                    image_size=(face_w, face_h), record_pages=1,
                    faces=(face,), cells=cells)


def test_shipped_template_ratio_is_unchanged(shipped_template):
    """出荷テンプレートは面積比が baseline を明確に上回る（front 0.576・
    back 0.726・2026-09 実測）ため、閾値は従来の FORMAT_MISMATCH_RATIO の
    まま——ここが崩れると既存出力が1バイトも変わらない前提（golden 一致）が
    破れる。
    """
    assert (render_rows.format_mismatch_ratio(shipped_template)
            == render_rows.FORMAT_MISMATCH_RATIO)


def test_sparse_template_raises_threshold_past_case_c_boundary():
    """issue #103 合成ケースC相当の疎テンプレート。

    欄20・面2000x3000（面積 6,000,000）に各80x60pxの欄だけを置く
    （被覆率 ≈0.016）——表を持たない「欄だけ」の面を模した極端な疎さ。
    ケースCの実測境界 other/total=0.556（印字:記入比 1.25）を様式不一致に
    倒さないことを確認する。
    """
    boxes = [(100 + i * 90, 100, 80, 60) for i in range(20)]
    tpl = _synthetic_template(2000, 3000, boxes)
    ratio = render_rows.format_mismatch_ratio(tpl)
    assert ratio > render_rows.FORMAT_MISMATCH_RATIO
    assert ratio >= 0.556  # ケースCの境界を様式不一致に倒さない
    assert ratio < 1.0


def test_ratio_at_baseline_coverage_is_unchanged():
    """被覆率がちょうど _COVERAGE_BASELINE のとき、閾値は引き上がらない
    （coverage >= baseline で従来閾値・#103 の設計どおり境界を含む側）。
    """
    # 面積 1,000,000。単一セル 700x500=350,000 → 被覆率 0.35 ちょうど
    tpl = _synthetic_template(1000, 1000, [(0, 0, 700, 500)])
    assert (render_rows.format_mismatch_ratio(tpl)
            == render_rows.FORMAT_MISMATCH_RATIO)


def test_ratio_just_below_baseline_coverage_is_raised():
    """被覆率が baseline をわずかに下回ると、閾値は厳密に引き上がる。"""
    # 700x499=349,300 → 被覆率 0.3493 (< 0.35)
    tpl = _synthetic_template(1000, 1000, [(0, 0, 700, 499)])
    assert (render_rows.format_mismatch_ratio(tpl)
            > render_rows.FORMAT_MISMATCH_RATIO)


def test_ratio_caps_below_one_for_near_zero_coverage():
    """被覆率がほぼ0でも、実効閾値は1.0未満の上限で頭打ちになる
    （明らかに崩れた応答まで様式不一致から救わないため）。
    """
    tpl = _synthetic_template(10000, 10000, [(0, 0, 1, 1)])
    ratio = render_rows.format_mismatch_ratio(tpl)
    assert render_rows.FORMAT_MISMATCH_RATIO < ratio < 1.0


def test_zero_area_face_falls_back_to_default_ratio():
    """面積0の面（構造上あり得ない防御分岐）は「密」扱いで従来閾値を維持する
    （実効閾値を不用意に緩めて全ページ様式不一致を素通りさせないため）。
    """
    face = Face(face_id="front", page_offset=0, source_rect=Rect(0, 0, 0, 0))
    tpl = Template(template_id="synthetic", render_dpi=300, image_size=(0, 0),
                   record_pages=1, faces=(face,), cells=())
    assert (render_rows.format_mismatch_ratio(tpl)
            == render_rows.FORMAT_MISMATCH_RATIO)
