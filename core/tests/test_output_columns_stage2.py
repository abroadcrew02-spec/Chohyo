"""出力列制御 MVP（issue #66）段2: 縫い目の手当ての受入基準を固定する。

要件の正本: docs/design/chouhyo-ocr/05_output_columns_requirements.md
FR-1.2（母集団表）・FR-1.4（対象外欄由来の警告可視化）・§7.2 の該当 AC。
設計判断: 02_design.md D-34。

このファイルで固定する4本の縫い目（付録 C）:
  1. W-1/W-2/W-3 警告文への「（出力対象外）」印（FR-1.2・AC-1.5）
  2. carve_hole・fallback_discarded・conflict の対象外欄由来カウンタを
     run サマリ・remap_summary の両方へ配線する（FR-1.4・AC-1.10）
  3. 母集団維持（空行判定・一意性検証・fallback_rect 受け皿）が対象外でも
     従来どおり働くこと（AC-1.4・AC-1.6・AC-1.7）・漏出防止/purge（AC-1.12）
  4. debug_images._field_origins と assign() の由来一致が対象外欄でも
     保たれること（F-13・AC-1.20 の debug_images 側）

段2 の範囲は core のみ（template.py・pipeline.py・mapping.py・
debug_images.py）。GUI（対象外行の一覧・チェックボックス等）は段3。
"""
import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chouhyo_ocr.columns import META_COLUMNS, derive_columns
from chouhyo_ocr.config import Config
from chouhyo_ocr.mapping import CellContent, Symbol, assign
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import remap, run
from chouhyo_ocr.render_rows import UNCLEAR, build_row
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import TemplateError, load_template
from chouhyo_ocr.vision_client import ReplayClient

TPL = app_root() / "templates" / "chouhyo-v1.json"
RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"

needs_replay = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


# ---------- 共通ヘルパ（core/tests に conftest.py が無いため各ファイルで複製） ----------

def _raw() -> dict:
    return json.loads(TPL.read_text(encoding="utf-8"))


def _write(tmp_path, data, name):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _disable_field(raw, field_id):
    fld = next(f for f in raw["faces"][0]["fields"] if f["field_id"] == field_id)
    fld["output"] = False
    return fld


def make_cfg(tmp_path) -> Config:
    return Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                  workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))


def _run_with_template(tmp_path, cfg, tpl_path, progress=None):
    """実応答（保存済み・課金ゼロ）で1ページ分の run を、指定テンプレートで行う。

    progress を渡すと run() の進捗イベントをそこへ流す（既定は捨てる）。
    """
    inp = tmp_path / "input"; inp.mkdir(parents=True)
    resp = tmp_path / "resp"; resp.mkdir(parents=True)
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    kwargs = {"progress": progress} if progress is not None else {}
    run(inp, tpl_path, cfg, ReplayClient(resp), **kwargs)


def page(status="", below=0):
    return {"page_id": "p_0001", "source_file": "s.png", "page_no": 1,
            "status": status, "unassigned_below_table": below}


CFG = Config(unclear_threshold=0.85, era_threshold=0.06)


def _sym(text, rect, dx=5, dy=5, conf=0.98):
    """rect（Rect または {"x","y"} dict）内の1文字（面ローカル・中心点）。

    test_fallback.py と同じ流儀。Rect はデータクラスなので getattr で両対応する。
    """
    x = rect.x if hasattr(rect, "x") else rect["x"]
    y = rect.y if hasattr(rect, "y") else rect["y"]
    return Symbol(text=text, x=x + dx, y=y + dy, conf=conf)


# ========== 1. W-1/W-2/W-3 警告への「（出力対象外）」印（FR-1.2・AC-1.5） ==========

def test_ac_1_5_w1_w3_counts_unchanged_and_w3_gets_marker_when_one_field_excluded(tmp_path):
    baseline = load_template(TPL)
    w1_base = [w for w in baseline.warnings if w.startswith("[W-1]")]
    w3_base = [w for w in baseline.warnings if w.startswith("[W-3]")]
    assert len(w1_base) == 3    # 既存の件数固定（test_exclusion_warnings.py と同じ実測）
    assert len(w3_base) == 12   # 既存の件数固定（test_adjacent_gap_warnings.py と同じ実測）
    assert not any("（出力対象外）" in w for w in baseline.warnings)  # 無改変では印なし

    raw = _raw()
    # person_郵便番号1 は W-3（郵便番号1→住所1 の1px 隙間・issue #61 L-4）の当事者
    _disable_field(raw, "person_郵便番号1")
    t = load_template(_write(tmp_path, raw, "ac15.json"))

    w1 = [w for w in t.warnings if w.startswith("[W-1]")]
    w3 = [w for w in t.warnings if w.startswith("[W-3]")]
    assert len(w1) == 3   # 件数不変（FR-1.2: 重なり検証の母集団は変わらない）
    assert len(w3) == 12  # 件数不変（FR-1.2: W-3 の母集団は変わらない）
    assert any("person_郵便番号1" in w and "person_住所1" in w
              and "（出力対象外）" in w for w in w3)
    # 無関係な W-3（郵便番号2→住所2）には印が付かない
    assert any("person_郵便番号2" in w and "（出力対象外）" not in w for w in w3)


def test_w1_w2_get_marker_when_the_field_is_excluded(tmp_path):
    """コーディネーター指示（トワ・ぼたん S-8）: W-1/W-2 にも同じ印を付ける
    （要件書 05 の FR-1.2 表は W-3 のみ明記だが、対象の欄が output:false なら
    W-1/W-2 も同様に読み手へ伝えるべきという指示に沿って拡張する）。
    """
    raw = _raw()
    _disable_field(raw, "person_電話番号")  # 出荷テンプレの W-1 発火欄の1つ
    t = load_template(_write(tmp_path, raw, "w1_marker.json"))
    w1 = [w for w in t.warnings if w.startswith("[W-1]") and "person_電話番号" in w]
    assert w1 and all("（出力対象外）" in w for w in w1)
    # 対象外にしていない欄には印が付かない（誤爆しない）
    other_w1 = [w for w in t.warnings if w.startswith("[W-1]") and "person_電話番号" not in w]
    assert other_w1 and all("（出力対象外）" not in w for w in other_w1)

    raw2 = _raw()
    fld = next(f for f in raw2["faces"][0]["fields"] if f["field_id"] == "person_氏名")
    fld["output"] = False
    r = fld["rect"]
    raw2["faces"][0]["exclusions"].append({
        "id": "cover_all_w2", "rect": {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}})
    t2 = load_template(_write(tmp_path, raw2, "w2_marker.json"))
    w2 = [w for w in t2.warnings if w.startswith("[W-2]") and "person_氏名" in w]
    assert w2 and all("（出力対象外）" in w for w in w2)


def test_w3_marker_fires_on_either_side_excluded_or_logic(tmp_path):
    """W-3 は隙間の当事者どちらかが output:false なら印が付く（OR）。

    判断理由: AND（両方対象外のときだけ）にすると、まだ出力に残る側の欄が
    この隙間で文字を取りこぼしている事実を「対象外だから無視してよい」と
    読み手に誤解させる。OR は「読み手が判断材料として知っておくべき」
    （FR-1.2 の趣旨）を最大化する側に倒した設計判断——印は「無視してよい」
    ではなく「関係する欄に対象外が含まれる」という付随情報として付ける。
    """
    # 片方だけ対象外（郵便番号2側）でも印が付く
    raw = _raw()
    _disable_field(raw, "person_郵便番号2")
    t = load_template(_write(tmp_path, raw, "w3_or_a.json"))
    w3 = [w for w in t.warnings if w.startswith("[W-3]")
         and "person_郵便番号2" in w and "person_住所2" in w]
    assert w3 and all("（出力対象外）" in w for w in w3)

    # 反対側（住所2）だけを対象外にしても同じ隙間に印が付く
    raw2 = _raw()
    _disable_field(raw2, "person_住所2")
    t2 = load_template(_write(tmp_path, raw2, "w3_or_b.json"))
    w3b = [w for w in t2.warnings if w.startswith("[W-3]")
          and "person_郵便番号2" in w and "person_住所2" in w]
    assert w3b and all("（出力対象外）" in w for w in w3b)


# ========== 2. 対象外欄由来カウンタ（FR-1.4・AC-1.10） ==========

def test_conflict_excluded_field_counts_only_when_field_output_false(tmp_path):
    """assign() レベル: conflict（U-03矛盾）が output:false 欄で起きたときだけ
    conflict_excluded_field が増える（読み取り自体は output に関わらず同じ）。
    """
    raw_true = _raw()
    fld_true = next(f for f in raw_true["faces"][0]["fields"] if f["kind"] == "text")
    fld_true["fallback_rect"] = {"x": 2300, "y": 1700, "w": 150, "h": 60}
    t_true = load_template(_write(tmp_path, raw_true, "conflict_on.json"))
    fid = fld_true["field_id"]
    primary = next(c.rect for c in t_true.cells if c.field_id == fid)
    fb = next(c.fallback_rect for c in t_true.cells if c.field_id == fid)
    syms = [_sym("ノ", primary), _sym("参", fb), _sym("照", fb, dx=25)]
    res_true = assign(t_true.cells, {"front": syms, "back": []}, t_true.faces)
    assert res_true.cells[fid].origin == "conflict"  # 読み取り結果は不変
    assert res_true.conflict_excluded_field == 0      # output=true なので0

    raw_false = _raw()
    fld_false = next(f for f in raw_false["faces"][0]["fields"] if f["kind"] == "text")
    fld_false["fallback_rect"] = {"x": 2300, "y": 1700, "w": 150, "h": 60}
    fld_false["output"] = False
    t_false = load_template(_write(tmp_path, raw_false, "conflict_off.json"))
    fid2 = fld_false["field_id"]
    primary2 = next(c.rect for c in t_false.cells if c.field_id == fid2)
    fb2 = next(c.fallback_rect for c in t_false.cells if c.field_id == fid2)
    syms2 = [_sym("ノ", primary2), _sym("参", fb2), _sym("照", fb2, dx=25)]
    res_false = assign(t_false.cells, {"front": syms2, "back": []}, t_false.faces)
    assert res_false.cells[fid2].origin == "conflict"       # 読み取りは同じ
    assert res_false.conflict_excluded_field == 1            # output=false なので1


def test_carve_hole_and_fallback_discarded_excluded_field_are_attributed_separately(tmp_path):
    """carve_hole と fallback_discarded は別々の欄に属する事実に、それぞれ
    正しく追従する（FR-1.4）。

    実データ経路（issue #61・postal→address の穴）: fallback_discarded は
    参照先を持つ欄（郵便番号1）自身に属し、carve_hole は穴の持ち主（住所1）に
    属する。郵便番号1だけを対象外にしても carve_hole_excluded_field は0のまま、
    住所1だけを対象外にすると carve_hole_excluded_field だけが増える。
    """
    hole_x, hole_y = 700, 320  # test_fallback.py::test_postal_fallback_discard_carves_address_hole と同じ穴座標

    raw_a = _raw()
    _disable_field(raw_a, "person_郵便番号1")
    t_a = load_template(_write(tmp_path, raw_a, "excl_postal.json"))
    postal_a = next(c.rect for c in t_a.cells if c.field_id == "person_郵便番号1")
    syms_a = [
        _sym("1", postal_a, dx=0), _sym("2", postal_a, dx=30),
        Symbol(text="9", x=hole_x, y=hole_y, conf=0.95),
        Symbol(text="9", x=hole_x + 20, y=hole_y, conf=0.95),
    ]
    res_a = assign(t_a.cells, {"front": syms_a, "back": []}, t_a.faces)
    assert res_a.carve_hole == 2
    assert res_a.fallback_discarded == 2
    assert res_a.fallback_discarded_excluded_field == 2  # 発火元＝郵便番号1（対象外）
    assert res_a.carve_hole_excluded_field == 0            # 穴の持ち主＝住所1（対象内のまま）

    raw_b = _raw()
    _disable_field(raw_b, "person_住所1")
    t_b = load_template(_write(tmp_path, raw_b, "excl_address.json"))
    postal_b = next(c.rect for c in t_b.cells if c.field_id == "person_郵便番号1")
    syms_b = [
        _sym("1", postal_b, dx=0), _sym("2", postal_b, dx=30),
        Symbol(text="9", x=hole_x, y=hole_y, conf=0.95),
        Symbol(text="9", x=hole_x + 20, y=hole_y, conf=0.95),
    ]
    res_b = assign(t_b.cells, {"front": syms_b, "back": []}, t_b.faces)
    assert res_b.carve_hole == 2
    assert res_b.carve_hole_excluded_field == 2            # 穴の持ち主＝住所1（対象外）
    assert res_b.fallback_discarded_excluded_field == 0     # 発火元＝郵便番号1（対象内のまま）


@needs_replay
def test_run_and_remap_both_report_excluded_field_counters(tmp_path, monkeypatch):
    """パイプライン配線: run の summary イベント・remap の remap_summary
    イベントの両方に *_excluded_field キーが出る（5巡目の轍——片方だけ配線して
    remap 経由で消える事故の再発防止）。

    assign() 自体の判定ロジックは上の2テストで固定済みなので、ここでは
    「MappingResult の値がどちらの経路でも欠落せず伝わるか」だけを見る。
    実データは carve_hole/fallback_discarded/conflict を自然発生させない
    （test_e2e_replay.py の remap 実測: fallback_discarded=0・carve_hole=0）
    ため、pipeline.assign を薄くラップして値を注入する。
    """
    from chouhyo_ocr import pipeline

    real_assign = pipeline.assign

    def fake_assign(cells, by_face, faces, **kwargs):
        # dpi 等の追加キーワード引数（汎用化 A-3）は実体へ透過させる。
        # 固定シグネチャで受けると pipeline 側の引数追加のたびに TypeError で
        # map_failed に倒れ、このテストが本来見たいカウンタ配線と無関係に落ちる
        result = real_assign(cells, by_face, faces, **kwargs)
        return dataclasses.replace(
            result,
            fallback_discarded_excluded_field=result.fallback_discarded_excluded_field + 3,
            carve_hole_excluded_field=result.carve_hole_excluded_field + 2,
            conflict_excluded_field=result.conflict_excluded_field + 1,
        )

    monkeypatch.setattr(pipeline, "assign", fake_assign)

    # run: 別 workdir で新規に1ページ処理させ、summary/page イベントを見る
    run_events: list[dict] = []
    cfg_run = make_cfg(tmp_path / "run")
    _run_with_template(tmp_path / "run", cfg_run, TPL, progress=run_events.append)

    summary_ev = next(e for e in run_events if e.get("event") == "summary")
    assert summary_ev["fallback_discarded_excluded_field"] == 3
    assert summary_ev["carve_hole_excluded_field"] == 2
    assert summary_ev["conflict_excluded_field"] == 1
    page_ev = next(e for e in run_events
                   if e.get("event") == "page" and e.get("status") == "done")
    assert page_ev["fallback_discarded_excluded_field"] == 3
    assert page_ev["carve_hole_excluded_field"] == 2
    assert page_ev["conflict_excluded_field"] == 1

    # remap: 別 workdir を用意し、まず1ページ done にしてから remap で
    # 再割付させる（remap_summary イベントを見る）。この run 呼び出しも
    # 同じパッチ下（assign は既に置き換え済み）だが、ここで見たいのは
    # remap 側のイベントなので run 側の中身は問わない
    cfg_remap = make_cfg(tmp_path / "remap")
    _run_with_template(tmp_path / "remap", cfg_remap, TPL)
    remap_events: list[dict] = []
    remap(TPL, cfg_remap, progress=remap_events.append)
    remap_ev = next(e for e in remap_events if e.get("event") == "remap_summary")
    assert remap_ev["fallback_discarded_excluded_field"] == 3
    assert remap_ev["carve_hole_excluded_field"] == 2
    assert remap_ev["conflict_excluded_field"] == 1


@needs_replay
def test_page_and_summary_events_omit_or_zero_excluded_field_keys_when_none_fire(tmp_path):
    """通常データ（対象外欄なし・carve_hole/fallback_discarded/conflict も
    発生しない）では、page イベントにキー自体が出ず（既存の「非ゼロのときだけ
    キーを足す」流儀）、summary/remap_summary には0の値付きで常に出る。
    """
    cfg = make_cfg(tmp_path)
    events: list[dict] = []
    _run_with_template(tmp_path, cfg, TPL, progress=events.append)

    page_ev = next(e for e in events if e.get("event") == "page" and e.get("status") == "done")
    assert "fallback_discarded_excluded_field" not in page_ev
    assert "carve_hole_excluded_field" not in page_ev
    assert "conflict_excluded_field" not in page_ev

    summary_ev = next(e for e in events if e.get("event") == "summary")
    assert summary_ev["fallback_discarded_excluded_field"] == 0
    assert summary_ev["carve_hole_excluded_field"] == 0
    assert summary_ev["conflict_excluded_field"] == 0

    remap_events: list[dict] = []
    remap(TPL, cfg, progress=remap_events.append)
    remap_ev = next(e for e in remap_events if e.get("event") == "remap_summary")
    assert remap_ev["fallback_discarded_excluded_field"] == 0
    assert remap_ev["carve_hole_excluded_field"] == 0
    assert remap_ev["conflict_excluded_field"] == 0


# ========== 3. 母集団維持・漏出防止（AC-1.4・AC-1.6・AC-1.7・AC-1.12） ==========

def test_ac_1_4_row_with_only_excluded_field_filled_is_not_empty_row(tmp_path):
    """対象外欄にのみ記入がある行は空行と判定されず、他の出力列は
    空文字でなく〓になる（FR-1.2: 空行判定の母集団は output と無関係）。
    """
    raw = _raw()
    _disable_field(raw, "person_電話番号")  # 単発欄側でも同じ理屈を軽く確認
    tbl = next(t for f in raw["faces"] for t in f.get("tables", []) if t["table_id"] == "family")
    next(c for c in tbl["columns"] if c["name"] == "続柄")["output"] = False
    t = load_template(_write(tmp_path, raw, "ac14.json"))

    zokugara = next(c.rect for c in t.cells if c.field_id == "family_05_続柄")
    syms = [_sym("長", zokugara)]
    result = assign(t.cells, {"front": syms, "back": []}, t.faces)
    assert ("family", 5) not in result.empty_rows  # 対象外欄の記入だけで空行を脱する

    cells_dict = {
        c.field_id: ("", None, c.kind, (c.table_id, c.row_no) in result.empty_rows)
        for c in t.cells}
    content = result.cells.get("family_05_続柄")
    cells_dict["family_05_続柄"] = (content.text, content.conf_min, "text", False)

    row = build_row(t, page(), cells_dict, {}, CFG)
    extract_cols = derive_columns(t)[len(META_COLUMNS):]
    assert "family_05_続柄" not in extract_cols  # 対象外なので列には出ない
    other_idx = extract_cols.index("family_05_氏名")
    assert row.values[other_idx] == UNCLEAR  # 空文字ではなく〓（AC-1.4 の核心）


def test_ac_1_6_duplicate_field_id_rejected_even_when_one_is_excluded(tmp_path):
    """対象外欄と同じ field_id を持つ別欄を追加すると、一意性検証が従来どおり
    拒否する（解除時に列名が衝突する状態を事前に作らせない・FR-1.2）。
    """
    raw = _raw()
    raw["faces"][0]["fields"][0]["output"] = False
    raw["faces"][0]["fields"][1]["field_id"] = raw["faces"][0]["fields"][0]["field_id"]
    with pytest.raises(TemplateError, match="重複"):
        load_template(_write(tmp_path, raw, "ac16.json"))


def test_ac_1_7_fallback_receptor_still_triggers_w2_when_field_is_excluded(tmp_path):
    """対象外欄が fallback_rect を持つ場合、参照先は受け皿として機能し続け、
    W-2 が従来どおり発火する（B-S1・FR-1.2）。
    """
    raw = _raw()
    fld = _disable_field(raw, "person_郵便番号1")
    fb = fld["fallback_rect"]
    raw["faces"][0]["exclusions"].append({
        "id": "cover_fallback_ac17",
        "rect": {"x": fb["x"], "y": fb["y"], "w": 10, "h": 10}})
    t = load_template(_write(tmp_path, raw, "ac17.json"))
    w2 = [w for w in t.warnings if w.startswith("[W-2]")]
    assert any("person_郵便番号1" in w and "参照先が除外領域と重なっている" in w for w in w2)


def test_ac_1_12_excluded_field_value_never_leaks_via_repr_or_row(tmp_path):
    """対象外欄の読取値も、他の欄と同じ漏出防止（repr 固定文字列・値そのものが
    Row.values に現れない）を受ける（NFR-02・test_leak_guards と同じ観点）。
    """
    raw = _raw()
    _disable_field(raw, "person_電話番号")
    t = load_template(_write(tmp_path, raw, "ac112_leak.json"))
    phone = next(c.rect for c in t.cells if c.field_id == "person_電話番号")
    secret = "09012345678"
    syms = [Symbol(text=ch, x=phone.x + 5 + i * 8, y=phone.y + 5, conf=0.95)
           for i, ch in enumerate(secret)]
    result = assign(t.cells, {"front": syms, "back": []}, t.faces)
    content = result.cells["person_電話番号"]
    assert content.text == secret          # 中間データには残る（Q-29・可逆性）
    assert secret not in repr(content)     # だが repr には出ない（漏出防止は不変）
    assert secret not in repr(CellContent(secret, 0.9))  # 型そのものの保護（field 非依存）

    cells_dict = {c.field_id: ("", None, c.kind, False) for c in t.cells}
    cells_dict["person_電話番号"] = (content.text, content.conf_min, "text", False)
    row = build_row(t, page(), cells_dict, {}, CFG)
    assert secret not in repr(row)
    assert secret not in row.values  # 対象外なので抽出列にすら現れない（stage1 の担保の再確認）


@needs_replay
def test_ac_1_12_purge_removes_excluded_field_intermediate_data(tmp_path):
    """対象外欄の読取値も、purge（workdir 削除）で他の欄と同様に消える
    （TR-G5 の資産を流用・対象外だからといって特別扱いの経路が無いことの確認）。
    """
    cfg = make_cfg(tmp_path)
    raw = _raw()
    _disable_field(raw, "person_電話番号")
    tpl_off = _write(tmp_path, raw, "purge_off.json")
    _run_with_template(tmp_path, cfg, tpl_off)

    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        pid = store.pages()[0]["page_id"]
        cells_before = store.cells(pid)
    finally:
        store.close()
    assert "person_電話番号" in cells_before  # 対象外欄の読取値も中間データに残る

    cfg_file = tmp_path / "cli_config.json"
    cfg_file.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(wd),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")
    r = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg_file), "purge", "--yes"],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=60)
    assert r.returncode == 0
    # workdir 自体は keep-list 方式（#83）では残るが、対象外欄の読取値を含む
    # intermediate.sqlite は他の中間データと同様に特別扱いなく消える
    assert wd.exists()
    assert not (wd / "intermediate.sqlite").exists()


# ========== 4. debug_images 側の由来一致（F-13・AC-1.20） ==========

@needs_replay
def test_ac_1_20_debug_images_field_origins_matches_assign_when_field_excluded(tmp_path):
    """output=false の欄でも debug_images._field_origins と assign() の由来が
    一致し続けること（F-13・#60 M-1③・output は読み取りに一切影響しないため
    理論上は無変更で一致するはず——それをここで実測固定する）。

    2026-09-03（#65-6）: 可視化は由来を再計算せず中間データの cell.origin を
    読むようになった。ここで固定したいのは「可視化と assign() の結論が一致する」
    ことなので、比較の対象はそのまま——ただし一致の理由が「同じ規則を2箇所で
    実装しているから」ではなく「同じ1つの値を見ているから」に変わった。
    """
    from chouhyo_ocr.debug_images import _field_origins

    cfg = make_cfg(tmp_path)
    raw = _raw()
    _disable_field(raw, "person_郵便番号1")
    tpl_off = _write(tmp_path, raw, "ac120_debug.json")
    _run_with_template(tmp_path, cfg, tpl_off)

    template = load_template(tpl_off)
    assert next(c.output for c in template.cells
               if c.field_id == "person_郵便番号1") is False

    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        pid = store.pages()[0]["page_id"]
        tokens = store.tokens(pid)
        origins_debug = _field_origins(store, pid)
    finally:
        store.close()

    by_face: dict[str, list[Symbol]] = {}
    for _seq, face, text, conf, x, y in tokens:
        by_face.setdefault(face, []).append(Symbol(text, x, y, conf))
    result = assign(template.cells, by_face, template.faces)

    assert origins_debug.get("person_郵便番号1") == "fallback"
    assert result.cells["person_郵便番号1"].origin == "fallback"
    assert origins_debug.get("person_郵便番号1") == result.cells["person_郵便番号1"].origin
