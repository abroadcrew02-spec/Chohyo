"""除外領域（マスク）の検知層（issue #55・#59 H-8・5巡目キャンペーン第1段）。

- verify の template チェックに exclusions / exclusions_by_face を追加する
  （#55: 保存経路に保全機構が無く除外領域が編集中に静かに消えても、検証が
  それを一切見ていなかった。ここでは件数を見える化するだけで、閾値判定や
  拒否は行わない——比較・拒否の判断は呼び出し側（編集画面）が持つ）
- expand-page に --no-mask を追加する（#59 H-8: 編集画面の下地が出荷テンプレの
  除外を焼いた画像しか持てず、除外枠の位置調整・取捨の判断材料が無かった）。
  既定（run の送信経路）は従来どおり白塗りすることを併せて確認する
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from chouhyo_ocr import cli
from chouhyo_ocr.align import align_page
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL_PATH = app_root() / "templates" / "chouhyo-v1.json"
PAGES = app_root() / "workdir" / "pages"


def _cfg(tmp_path: Path) -> Path:
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    return cfg_path


# ---------- T1: verify の template チェックに exclusions を出す（#55） ----------

def test_verify_reports_exclusion_counts_for_shipped_template(tmp_path, capsys):
    """出荷テンプレは front=7・back=2（issue #55 実測値と一致）。"""
    cfg_path = _cfg(tmp_path)
    cli.main(["--config", str(cfg_path), "verify", "--template", str(TPL_PATH)])
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    tpl_ev = next(e for e in events if e.get("check") == "template")
    assert tpl_ev["ok"] is True
    # 既存フィールドは変えない（契約はフィールド追加のみ）
    assert tpl_ev["columns"] > 0
    assert tpl_ev["cells"] > 0
    assert tpl_ev["amount_cells"] == 28
    # 追加フィールド
    assert tpl_ev["exclusions"] == 9
    assert tpl_ev["exclusions_by_face"] == {"front": 7, "back": 2}


def test_verify_exclusion_counts_reflect_a_degraded_template(tmp_path, capsys):
    """除外が編集で欠落したテンプレは、その欠落を件数の変化として見せる。

    issue #55 の実測（front 7→3・postal_label/tel_paren の4個が消失）を
    最小再現する。verify は拒否しない（見える化のみ）——編集画面側が
    読み込み時点との差分表示に使う想定（H-9 の設計方針）。
    """
    raw = json.loads(TPL_PATH.read_text(encoding="utf-8"))
    for face in raw["faces"]:
        if face["face_id"] == "front":
            face["exclusions"] = [
                e for e in face["exclusions"]
                if e["id"] not in ("postal_label_1", "postal_label_2",
                                   "tel_paren_l", "tel_paren_r")
            ]
    degraded = tmp_path / "degraded.json"
    degraded.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg_path = _cfg(tmp_path)
    cli.main(["--config", str(cfg_path), "verify", "--template", str(degraded)])
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    tpl_ev = next(e for e in events if e.get("check") == "template")
    assert tpl_ev["ok"] is True
    assert tpl_ev["exclusions_by_face"] == {"front": 3, "back": 2}
    assert tpl_ev["exclusions"] == 5


# ---------- T2/T3: align_page の mask 引数・expand-page --no-mask（#59 H-8） ----------

# templates/chouhyo-v1.json front.exclusions の postal_label_1（印字ラベル・実際に
# インクがある領域なので、白塗りの有無が画素差として確実に出る）
POSTAL_LABEL_1 = (415, 313, 205, 38)

pytestmark_sample = pytest.mark.skipif(
    not (PAGES / "sample-1.png").exists(), reason="サンプル画像が無い環境")


@pytestmark_sample
def test_align_page_mask_false_leaves_scan_pixels_true_whitens_them():
    """align_page(mask=False) は除外領域を白塗りしない。既定(mask=True)は従来どおり白塗り。"""
    template = load_template(TPL_PATH)
    front = template.face("front")
    x, y, w, h = POSTAL_LABEL_1
    fx, fy = front.source_rect.x, front.source_rect.y

    img = Image.open(PAGES / "sample-1.png")
    _faces_masked, composite_masked = align_page(img, template)            # 既定
    _faces_raw, composite_raw = align_page(img, template, mask=False)      # --no-mask 相当

    region_masked = np.asarray(composite_masked)[fy + y:fy + y + h, fx + x:fx + x + w]
    region_raw = np.asarray(composite_raw)[fy + y:fy + y + h, fx + x:fx + x + w]

    assert (region_masked == 255).all(), "既定 mask=True は除外領域を白塗りするはず"
    assert not (region_raw == 255).all(), "mask=False は走査画素を残すはず（印字ラベル領域）"
    assert not np.array_equal(region_masked, region_raw)


@pytestmark_sample
def test_align_page_default_call_still_masks(tmp_path):
    """run（pipeline.py）と同じ引数の並び（位置引数2つ）で呼んでも、従来どおり白塗りされる。

    run 側のマスク保証を壊していないことの回帰——mask 引数を増やしたことで
    既存の呼び出し（引数を渡さない）の既定値が変わっていないかを確認する。
    """
    template = load_template(TPL_PATH)
    front = template.face("front")
    x, y, w, h = POSTAL_LABEL_1
    fx, fy = front.source_rect.x, front.source_rect.y

    img = Image.open(PAGES / "sample-1.png")
    _faces, composite = align_page(img, template)  # pipeline.py:438 と同じ呼び方
    region = np.asarray(composite)[fy + y:fy + y + h, fx + x:fx + x + w]
    assert (region == 255).all()


@pytestmark_sample
def test_expand_page_no_mask_flag_reaches_align_page(tmp_path, capsys):
    """CLI の --no-mask が argparse → cmd_expand_page → align_page(mask=...) まで届く。

    出力ファイル名は決め打ち上書き（既存仕様）なので、2回目の --no-mask 呼び出しが
    1回目の出力を上書きする。1回目の画素を読んでおいてから比較する。
    """
    cfg_path = _cfg(tmp_path)
    src = str(PAGES / "sample-1.png")

    rc1 = cli.main(["--config", str(cfg_path), "expand-page",
                     "--input", src, "--template", str(TPL_PATH)])
    ev1 = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    assert rc1 == 0
    assert ev1["event"] == "expand_page" and ev1["ok"] is True and ev1["aligned"] is True
    masked_arr = np.asarray(Image.open(ev1["page_path"]).convert("RGB")).copy()

    rc2 = cli.main(["--config", str(cfg_path), "expand-page",
                     "--input", src, "--template", str(TPL_PATH), "--no-mask"])
    ev2 = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()][-1]
    assert rc2 == 0
    assert ev2["ok"] is True and ev2["aligned"] is True
    assert ev1["page_path"] == ev2["page_path"]  # 決め打ち名で同一パスを指す
    raw_arr = np.asarray(Image.open(ev2["page_path"]).convert("RGB"))

    front = load_template(TPL_PATH).face("front")
    x, y, w, h = POSTAL_LABEL_1
    fx, fy = front.source_rect.x, front.source_rect.y
    region_masked = masked_arr[fy + y:fy + y + h, fx + x:fx + x + w]
    region_raw = raw_arr[fy + y:fy + y + h, fx + x:fx + x + w]

    assert (region_masked == 255).all()
    assert not (region_raw == 255).all()
    assert not np.array_equal(region_masked, region_raw)
