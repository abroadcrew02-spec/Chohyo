"""除外領域×受け皿の重なり警告（U-09・H-6・2026-08-31）。

拒否ではなく警告（W-1弱／W-2強）にする。出荷テンプレートには意図的な重なりが
実在するため、拒否にすると出荷テンプレート自身が読み込めなくなる（§7.1）。
"""
import copy
import json

import pytest

from chouhyo_ocr import cli
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


@pytest.fixture()
def raw():
    return json.loads(TPL.read_text(encoding="utf-8"))


def write(tmp_path, data):
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def _w1(t):
    return [w for w in t.warnings if w.startswith("[W-1]")]


def _w2(t):
    return [w for w in t.warnings if w.startswith("[W-2]")]


# ---------- T-22: 出荷テンプレの実測 ----------

def test_shipped_template_has_three_w1_and_no_w2():
    """出荷テンプレの意図的な重なりは3欄（電話番号・会社名屋号・所在地）で W-1、
    W-2 は0件（設計書 §1.2 の実測 18.5%・1.1%・1.1% と一致）。
    """
    t = load_template(TPL)
    w1 = _w1(t)
    w2 = _w2(t)
    assert len(w1) == 3
    assert len(w2) == 0
    fields = {"person_電話番号", "person_会社名屋号", "person_所在地"}
    assert fields <= {w.split()[1] for w in w1}
    assert any("18.5%" in w for w in w1 if "電話番号" in w)


def test_load_template_still_succeeds_with_overlaps():
    """重なりがあっても load_template は例外を出さない（拒否しない・§7.1）。"""
    load_template(TPL)  # 例外にならなければ良い


# ---------- T-23: W-2 の完全被覆 ----------

def test_w2_fires_when_receptor_fully_covered(tmp_path, raw):
    fld = next(f for f in raw["faces"][0]["fields"] if f["field_id"] == "person_氏名")
    r = fld["rect"]
    raw["faces"][0]["exclusions"].append({
        "id": "cover_all", "rect": {"x": r["x"], "y": r["y"], "w": r["w"], "h": r["h"]}})
    t = load_template(write(tmp_path, raw))  # 拒否されない
    w2 = _w2(t)
    assert any("person_氏名" in w and "完全に覆われている" in w for w in w2)


# ---------- T-24: W-2 の参照先被覆 ----------

def test_w2_fires_when_fallback_rect_is_covered(tmp_path, raw):
    fld = next(f for f in raw["faces"][0]["fields"]
               if f["field_id"] == "person_郵便番号1")
    fb = fld["fallback_rect"]
    raw["faces"][0]["exclusions"].append({
        "id": "cover_fallback",
        "rect": {"x": fb["x"], "y": fb["y"], "w": 10, "h": 10}})
    t = load_template(write(tmp_path, raw))
    w2 = _w2(t)
    assert any("person_郵便番号1" in w and "参照先が除外領域と重なっている" in w
               for w in w2)


def test_w2_fires_when_primary_of_fallback_field_is_covered(tmp_path, raw):
    """参照先を持つ欄の主枠が1pxでも被覆されると W-2（『主が空』が構造的に成立しやすくなる）。"""
    fld = next(f for f in raw["faces"][0]["fields"]
               if f["field_id"] == "person_郵便番号1")
    r = fld["rect"]
    raw["faces"][0]["exclusions"].append({
        "id": "cover_primary",
        "rect": {"x": r["x"], "y": r["y"], "w": 5, "h": 5}})
    t = load_template(write(tmp_path, raw))
    w2 = _w2(t)
    assert any("person_郵便番号1" in w and "主枠が除外領域と重なっている" in w for w in w2)


# ---------- cli verify の warnings 契約（GUI 側と合意済み・変更不可） ----------

def _cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    return p


def test_cli_verify_reports_warnings_as_string_list(tmp_path, capsys):
    cfg_path = _cfg(tmp_path)
    cli.main(["--config", str(cfg_path), "verify", "--template", str(TPL)])
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    tpl_ev = next(e for e in events if e.get("check") == "template")
    assert tpl_ev["ok"] is True
    assert isinstance(tpl_ev["warnings"], list)
    assert all(isinstance(w, str) for w in tpl_ev["warnings"])
    # W-1（除外×受け皿の重なり）3件 + W-3（受け皿間の死角・#61 L-4）12件。
    # 内訳は他のテスト（本ファイルの test_shipped_template_has_three_w1_and_no_w2・
    # test_template.py 側の W-3 個別テスト）が別途固定する
    assert len(tpl_ev["warnings"]) == 15


def test_cli_verify_warnings_empty_for_overlap_free_template(tmp_path, raw, capsys):
    """除外領域を全て外すと W-1/W-2（除外×受け皿の重なり）は出ない。

    W-3（受け皿間の隙間・#61 L-4）は除外領域に依存しない別カテゴリの警告
    なので、この操作の影響を受けず残る（全 warnings が空になるわけではない）。
    """
    for face in raw["faces"]:
        face["exclusions"] = []
    p = tmp_path / "clean.json"
    p.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    cfg_path = _cfg(tmp_path)
    cli.main(["--config", str(cfg_path), "verify", "--template", str(p)])
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    tpl_ev = next(e for e in events if e.get("check") == "template")
    assert not any(w.startswith("[W-1]") or w.startswith("[W-2]")
                   for w in tpl_ev["warnings"])


def test_w1_only_when_extra_rect_overlaps_without_fallback(tmp_path, raw):
    """extra_rects（追加領域）が除外と重なっても、主枠ではないので W-2 の
    「主枠被覆」条件は発火しない（W-1 のみ）。
    """
    fld = next(f for f in raw["faces"][0]["fields"] if f["field_id"] == "person_住所1")
    extra = fld["extra_rects"][0]
    raw["faces"][0]["exclusions"].append({
        "id": "cover_extra",
        "rect": {"x": extra["x"], "y": extra["y"], "w": 5, "h": 5}})
    t = load_template(write(tmp_path, raw))
    w1 = _w1(t)
    w2 = _w2(t)
    assert any("person_住所1" in w and "欄の追加領域" in w for w in w1)
    assert not any("person_住所1" in w for w in w2)
