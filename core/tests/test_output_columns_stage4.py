"""出力列制御 MVP（issue #66）段4 の core 側。

1. debug-images での対象外欄の見分け（FR-1.9 Should・ぼたん S-9）。
   debug-images は template.cells 全件を描く（対象外欄も読み取りは継続するので
   描かれ続けるのが正しい・record 側は変えない）。本段は「見分けが付く」ことだけを
   追加する: 枠・ラベルを専用色 COL_EXCLUDED で塗り分ける。GUI の canvas 表現
   （ハッチ）は模倣しない（debug 画像は開発者向けのため識別できれば十分）。
2. verify の template チェックへ `output_disabled_cells` を追加（FR-1.9・
   フブキ実測: RunScreen の差分計算 cells+6-columns は subfields 展開で破綻する
   ため、欄数はここで直接数えて渡す）。
"""
import json
import shutil

import pytest
from PIL import Image

from chouhyo_ocr import cli, debug_images
from chouhyo_ocr.config import Config
from chouhyo_ocr.debug_images import write_debug_images
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import load_template
from chouhyo_ocr.vision_client import ReplayClient

TPL = app_root() / "templates" / "chouhyo-v1.json"
RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"

needs_replay = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


# ---------- 呼び出し検証（実データ不要・常に実行） ----------

def test_col_excluded_is_a_new_color_distinct_from_all_existing_colors():
    """COL_EXCLUDED が新設され、既存の色定数のどれとも被らないこと。

    「枠線を別スタイル（対象外用の色1色を新設）」の直接検証。既存の色と
    衝突すると、対象外欄が既存の何らかの意味（文字欄・選択式・参照先など）
    と誤読される。
    """
    existing = {
        debug_images.COL_TEXT, debug_images.COL_CHOICE, debug_images.COL_FALLBACK,
        debug_images.COL_OK, debug_images.COL_LOW, debug_images.COL_STRAY,
        debug_images.COL_FALLBACK_OK, debug_images.COL_FALLBACK_DISCARD,
        debug_images.COL_HOLE,
    }
    assert isinstance(debug_images.COL_EXCLUDED, tuple) and len(debug_images.COL_EXCLUDED) == 3
    assert debug_images.COL_EXCLUDED not in existing


# ---------- 画素検証（実データ・実描画で確かめる） ----------

@needs_replay
def test_excluded_field_border_pixel_differs_from_normal_field(tmp_path):
    """output:false の欄の枠が COL_EXCLUDED で、通常欄は従来どおり COL_TEXT で
    描かれる（実際に生成した PNG の画素で確認）。

    person_電話番号 を対象外にし、person_氏名（対象外にしていない同じ text 欄）
    と枠色を比較する。矩形の左上角は width>=1 の outline で必ず塗られる位置
    （dr.rectangle(box, outline=color, width=3) の描画実装に依存しない安全な
    サンプル点）。
    """
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    fld = next(f for f in raw["faces"][0]["fields"] if f["field_id"] == "person_電話番号")
    fld["output"] = False
    tpl_off = tmp_path / "off.json"
    tpl_off.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    run(inp, tpl_off, cfg, ReplayClient(resp))

    template = load_template(tpl_off)
    excluded_cell = next(c for c in template.cells if c.field_id == "person_電話番号")
    normal_cell = next(c for c in template.cells if c.field_id == "person_氏名")
    assert excluded_cell.output is False
    assert normal_cell.output is True
    ox, oy = template.face("front").source_rect.x, template.face("front").source_rect.y

    from pathlib import Path
    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        out = tmp_path / "dbg"
        made = write_debug_images(store, template, wd / "aligned", out, cfg)
    finally:
        store.close()
    assert made
    img = Image.open(made[0]).convert("RGB")

    excluded_pixel = img.getpixel(
        (excluded_cell.rect.x + ox, excluded_cell.rect.y + oy))
    normal_pixel = img.getpixel((normal_cell.rect.x + ox, normal_cell.rect.y + oy))

    assert excluded_pixel == debug_images.COL_EXCLUDED
    assert normal_pixel == debug_images.COL_TEXT
    assert excluded_pixel != normal_pixel


@needs_replay
def test_write_debug_images_unaffected_for_unmodified_template(tmp_path):
    """無改変テンプレート（全欄 output=True）では、対象外欄の色が一切使われない
    （回帰: 段4 の変更が既存の見た目を変えていないことの確認）。
    """
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    run(inp, TPL, cfg, ReplayClient(resp))

    template = load_template(TPL)
    assert all(c.output for c in template.cells)  # 前提: 無改変では全欄 output=True
    ox, oy = template.face("front").source_rect.x, template.face("front").source_rect.y
    phone = next(c.rect for c in template.cells if c.field_id == "person_電話番号")

    from pathlib import Path
    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        out = tmp_path / "dbg_unmod"
        made = write_debug_images(store, template, wd / "aligned", out, cfg)
    finally:
        store.close()
    img = Image.open(made[0]).convert("RGB")
    assert img.getpixel((phone.x + ox, phone.y + oy)) == debug_images.COL_TEXT
    assert img.getpixel((phone.x + ox, phone.y + oy)) != debug_images.COL_EXCLUDED


# ========== 2. verify の output_disabled_cells（FR-1.9・フブキ実測） ==========

def _cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    return p


def _verify_template_event(tmp_path, capsys, template_path, name="cli_config.json"):
    cfg_path = _cfg(tmp_path)
    cli.main(["--config", str(cfg_path), "verify", "--template", str(template_path)])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return next(e for e in events if e.get("check") == "template")


def test_verify_output_disabled_cells_counts_physical_cells_not_output_columns(tmp_path, capsys):
    """output_disabled_cells は「欄数」（物理セル数）を数え、「列数」ではない。

    ①無改変テンプレートは0 ②単発欄を1つ対象外にすると1
    ③subfields（年/月/日=3出力列）を持つ表の列を対象外にしても、
    対象外にした「欄」自体は1つなので output_disabled_cells は1のまま
    （3ではない）——フブキが実測した GUI 側の破綻（cells+6-columns が
    subfields 展開で負値になる）を core 側の直接カウントで避ける。
    """
    ev0 = _verify_template_event(tmp_path, capsys, TPL)
    assert ev0["ok"] is True
    assert ev0["output_disabled_cells"] == 0
    # 既存フィールドは不変（契約はフィールド追加のみ）
    assert ev0["columns"] > 0 and ev0["cells"] > 0

    raw1 = json.loads(TPL.read_text(encoding="utf-8"))
    fld = next(f for f in raw1["faces"][0]["fields"] if f["field_id"] == "person_電話番号")
    fld["output"] = False
    tpl1 = tmp_path / "one_field_off.json"
    tpl1.write_text(json.dumps(raw1, ensure_ascii=False), encoding="utf-8")
    ev1 = _verify_template_event(tmp_path, capsys, tpl1)
    assert ev1["output_disabled_cells"] == 1

    raw2 = json.loads(TPL.read_text(encoding="utf-8"))
    fam = next(t for f in raw2["faces"] for t in f.get("tables", []) if t["table_id"] == "family")
    fam["blocks"] = [dict(fam["blocks"][0], rows=1)]  # この検証専用に1行へ縮める
    col = next(c for c in fam["columns"] if c["name"] == "生年月日")
    assert col.get("subfields") == ["年", "月", "日"]  # 前提: 3出力列に展開される欄
    col["output"] = False
    tpl2 = tmp_path / "subfields_field_off.json"
    tpl2.write_text(json.dumps(raw2, ensure_ascii=False), encoding="utf-8")
    ev2 = _verify_template_event(tmp_path, capsys, tpl2)
    assert ev2["output_disabled_cells"] == 1  # 列は3つ減るが、欄は1つ
