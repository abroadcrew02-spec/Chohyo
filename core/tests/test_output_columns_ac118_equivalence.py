"""出力列制御 MVP（issue #66）第1弾 QA 条件付きOK・切替条件②（core 側）。

AC-1.18: 「JSON 直接編集で `output: false` を書いたテンプレートの run 出力が、
画面経由で作ったものと一致する」の core 側の裏付け。

このテストは Python から GUI（Editor.tsx）を直接実行しない
（言語跨ぎの直接実行はしない）。GUI 側の直列化契約は
gui/tests/gui-logic.test.mjs の「AC-1.18 (a)」で別途固定している——
buildTemplate は無効化した欄にだけ明示的に `"output": false` を書き、
有効な欄は `output` キー自体を省略する（B-S4: 無関係な保存で
template_hash を動かさないため）。

本テストは core 側の役割を独立に固定する: GUI が書く形（省略のみ）と、
JSON を直接編集する運用者が書きうる形（有効な欄にも明示的に
`"output": true` を書く——スキーマ上は省略と同値のはず）の2種の
fixture を用意し、`load_template` → `derive_columns` が同一の列構成に
なることを確認する。GUI 側と core 側それぞれが独立に「同じ契約」を
守っていることを挟み撃ちで固定する。
"""
import copy
import json

from chouhyo_ocr.columns import derive_columns
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


def _raw() -> dict:
    return json.loads(TPL.read_text(encoding="utf-8"))


def _write(tmp_path, data, name):
    p = tmp_path / name
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_ac_1_18_gui_shaped_and_hand_written_output_forms_are_equivalent(tmp_path):
    raw = _raw()
    target_id = "person_会社名屋号"  # 任意の1つの単発 text 欄を対象外にする

    # フィクスチャ1: GUI が書く形（buildTemplate と同じ規則）。
    # 対象欄だけ output:false・他の欄・表の列は output キーを一切書かない
    gui_shaped = copy.deepcopy(raw)
    fld = next(f for f in gui_shaped["faces"][0]["fields"] if f["field_id"] == target_id)
    fld["output"] = False
    assert all("output" not in f for face in gui_shaped["faces"]
               for f in face.get("fields", []) if f["field_id"] != target_id), \
        "GUI 形フィクスチャの前提が崩れている（対象外以外に output を書いてしまった）"

    # フィクスチャ2: 手書きで書かれたと想定できる形。対象欄は同じく
    # output:false・それ以外の全欄・表の全列には明示的に output:true を
    # 追記する（省略と意味的に等価なはずの、より冗長な書き方）
    hand_written = copy.deepcopy(raw)
    for face in hand_written["faces"]:
        for f in face.get("fields", []):
            f["output"] = False if f["field_id"] == target_id else True
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                c["output"] = True

    t_gui = load_template(_write(tmp_path, gui_shaped, "gui_shaped.json"))
    t_hand = load_template(_write(tmp_path, hand_written, "hand_written.json"))

    cols_gui = derive_columns(t_gui)
    cols_hand = derive_columns(t_hand)

    assert cols_gui == cols_hand, (
        "output を省略する書き方（GUI 形）と明示的に true を書く書き方（手書き形）"
        "で列構成が食い違っている——AC-1.18 の前提（省略と明示 true は等価）が崩れている")
    assert target_id not in cols_gui, "対象外にした欄が列に残っている"
    # 対象外1欄ぶんだけ列が減っていること（他の欄には触れていない健全性チェック）
    assert len(cols_gui) == len(derive_columns(load_template(TPL))) - 1
