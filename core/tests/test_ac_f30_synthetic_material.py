"""AC-F30 の出力レベル実証 — 吸着 ON/OFF で出力に差が出る合成素材（issue #105・#75 の申し送り）。

## 背景

`test_snap_pipeline.py::test_ac_f30_output_is_unchanged_by_the_stimulus_in_this_material`
が実測で固定したとおり、実写素材（`testdata/local/pages/sample-1.png`）では
δ=2〜5 の全域で OFF/ON ともセル値が無変形時と完全一致し、07 AC-F30 が期待する
「OFF ではブロック位置ずれで値が乱れる」が再現しなかった。理由は幾何:
detail 表の行高は100px あり、位置合わせが信用できる刺激（δ≤5・
`align.SHIFT_GAP_MIN`/`SHIFT_RUNNER_DIST` が定める上限）では、実写の記入位置が
行バケツ（`mapping._bucket_cells` の点内包判定）の外へ出ない。

このファイルは実写を使わず、**行の寸法とバケツ境界への記入位置を完全に
指定できる合成素材**で AC-F30 を出力レベルで実証する。

## 素材の作り方（helpers_geom.py の流儀を踏襲しつつ、経路を変えている）

`helpers_geom.shift_block_y`/`shift_response_vertices` は「実写画像の画素を
物理的にずらし、`align.estimate_shift` に実際の線検出をやらせる」経路だが、
上記のとおり実写では行高に対してブロックのずれが小さすぎて出力に届かない。
このファイルは **`chouhyo_ocr.pipeline.align_page` を差し替える**経路を取る
（先例: `test_review4_pipeline.py::AlignCounter`・`test_page_size_guard.py` が
同じ関数を monkeypatch している）。align_page が返す `AlignedFace.estimate`
（`ShiftEstimate.block_shifts`）を直接組み立てることで、「位置合わせが測った
ブロックのずれ量」を任意の px に固定できる——線検出の閾値較正に依存せず、
`snap.plan_face_snap`/`apply_snap`→`mapping.assign` という**吸着が出力へ効く
経路そのもの**を実測する。

テンプレートも実物ではなく、行高 20px・行間隙 4px（`chouhyo-v1` detail と
同じ row_gap）の最小合成表（1面・1表・1ブロック・5行・1列）を使う。応答
（Vision 相当）は replay 用の合成 JSON——`symbols_from_response` が読む
`fullTextAnnotation.pages[].blocks[].paragraphs[].words[].symbols[]` の最小形。

刺激 D（ブロックの実際の印字ずれ・px）は「測った量」として
`BlockShift.dy` にそのまま渡し、**同じ D だけ記入 symbol の y も動かす**
（`_materialize` の delta と同じ考え方: 面のシフトと吸着量が一致することを
テストの前提にする）。行3（`t1_03_amt`）の記入だけを行の下端ぎりぎり
（`row_height-1`）に置き、他4行は行の中央（`row_height//2`）に置く——
中央の記入は D=4 動いても行の内側に収まったままなので、OFF でも正しく
拾える「アンカー」として働き、D-15（枠外率超過による様式不一致判定）を
誘発しない。狙った行だけが境界をまたぐ。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from chouhyo_ocr import pipeline as pipeline_mod
from chouhyo_ocr.align import AlignedFace, BlockShift, ShiftEstimate
from chouhyo_ocr.config import Config
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.store import Store
from chouhyo_ocr.vision_client import ReplayClient

# --- 合成テンプレートの幾何（chouhyo-v1 detail と同じ row_gap=4 を採用） ---
ROW_PITCH = 24
ROW_HEIGHT = 20                     # row_gap = ROW_PITCH - ROW_HEIGHT = 4
ROWS = 5                            # h_lines=6 本 > SNAP_EXCLUDED_H_LINES_MAX(4)
BLOCK_ORIGIN = (50, 50)
COL_WIDTH = 100
IMAGE_SIZE = 300                    # 面 = ページ全体（1面・source.rect = 画像全体）
TARGET_ROW_IDX = 2                  # 0起点。row_no=3 の行だけを境界へ寄せる


def _template_dict() -> dict:
    return {
        "schema_version": 1, "template_id": "ac-f30-synth", "render_dpi": 300,
        "image": {"width": IMAGE_SIZE, "height": IMAGE_SIZE},
        "record": {"pages": 1},
        "faces": [{
            "face_id": "front",
            "source": {"page_offset": 0,
                       "rect": {"x": 0, "y": 0, "w": IMAGE_SIZE, "h": IMAGE_SIZE}},
            "exclusions": [], "fields": [],
            "tables": [{
                "table_id": "t1", "row_pitch": ROW_PITCH, "row_height": ROW_HEIGHT,
                "blocks": [{"origin": {"x": BLOCK_ORIGIN[0], "y": BLOCK_ORIGIN[1]},
                           "rows": ROWS}],
                "columns": [{"name": "amt", "x_offset": 0, "width": COL_WIDTH,
                            "kind": "text"}],
            }],
        }],
    }


def _symbols_for(d: int) -> list[tuple[float, float, str]]:
    """行ごとの記入 symbol（x, y, text）。行3（idx=2）だけ行の下端-1px、
    残り4行は行の中央——中央は D=4 動いても行内に留まるアンカー役。"""
    out = []
    for i in range(ROWS):
        top = BLOCK_ORIGIN[1] + ROW_PITCH * i
        row_no = i + 1
        offset = (ROW_HEIGHT - 1) if i == TARGET_ROW_IDX else ROW_HEIGHT // 2
        y = top + d + offset
        x = BLOCK_ORIGIN[0] + COL_WIDTH / 2
        out.append((x, y, str(row_no)))
    return out


def _response_json(symbols: list[tuple[float, float, str]]) -> dict:
    def sym(x: float, y: float, text: str) -> dict:
        return {"text": text, "confidence": 0.95,
                "boundingBox": {"vertices": [
                    {"x": x - 3, "y": y - 3}, {"x": x + 3, "y": y - 3},
                    {"x": x + 3, "y": y + 3}, {"x": x - 3, "y": y + 3}]}}
    words = [{"symbols": [sym(x, y, t)]} for x, y, t in symbols]
    return {"fullTextAnnotation": {"pages": [{"blocks": [{"paragraphs": [{"words": words}]}]}]}}


def _fake_align_page(d: int, matched: int, expected: int):
    """`pipeline.align_page` の差し替え（先例: test_review4_pipeline.py・
    test_page_size_guard.py）。線検出を一切せず、`BlockShift.dy=d` を
    そのまま「測った量」として返す——刺激と測定値を一致させる（D-25 の
    契約と同じ考え方を、ここでは monkeypatch で明示的に固定する）。"""
    def fake(img, template):
        faces = []
        composite = Image.new("RGB", template.image_size, "white")
        for face in template.faces:
            r = face.source_rect
            face_img = Image.new("RGB", (r.w, r.h), "white")
            binary = np.zeros((r.h, r.w), dtype=bool)
            block_shifts = ((BlockShift(block_idx=0, dy=d, matched=matched,
                                        expected=expected),)
                            if face.table_geoms else ())
            est = ShiftEstimate(dx=0, dy=0, matched=matched, total=expected,
                                ok=True, reason="", block_shifts=block_shifts)
            faces.append(AlignedFace(face.face_id, face_img, binary, 0.0,
                                     dx=0, dy=0, shift_matched=matched, estimate=est))
            composite.paste(face_img, (r.x, r.y))
        return faces, composite
    return fake


def _cfg(tmp_path: Path, *, snap_blocks: bool, name: str) -> Config:
    return Config(unclear_threshold=0.4, snap_blocks=snap_blocks,
                  output_dir=str(tmp_path / f"out_{name}"),
                  workdir=str(tmp_path / f"wd_{name}"),
                  log_dir=str(tmp_path / f"logs_{name}"))


def _run_scenario(tmp_path: Path, monkeypatch, *, name: str, snap_blocks: bool,
                  d: int, matched: int = ROWS + 1, expected: int = ROWS + 1
                  ) -> tuple[dict[str, str], dict, tuple]:
    """1シナリオ実行し、(cells値, summary event, alignment行) を返す。

    matched/expected の既定 ROWS+1=6 は h_lines の本数（rows+1）——全期待横線が
    一致した想定（need_y = max(2, ceil(6*0.5))=3 を十分満たす）。
    """
    tpl_path = tmp_path / "tpl.json"
    if not tpl_path.exists():
        tpl_path.write_text(json.dumps(_template_dict(), ensure_ascii=False),
                            encoding="utf-8")

    inp = tmp_path / f"input_{name}"; inp.mkdir(parents=True, exist_ok=True)
    resp_dir = tmp_path / f"resp_{name}"; resp_dir.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (IMAGE_SIZE, IMAGE_SIZE), "white").save(inp / f"{name}.png")
    (resp_dir / f"{name}_p0001.json").write_text(
        json.dumps(_response_json(_symbols_for(d)), ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(pipeline_mod, "align_page",
                        _fake_align_page(d, matched, expected))

    cfg = _cfg(tmp_path, snap_blocks=snap_blocks, name=name)
    events: list = []
    run(inp, tpl_path, cfg, ReplayClient(resp_dir), events.append)

    page_id = f"{name}_p0001"
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        values = {fid: v[0] for fid, v in store.cells(page_id).items()}
        align_row = store.con.execute(
            "SELECT snap_enabled, snap_px, snap_detail FROM alignment "
            "WHERE page_id=? AND face_id='front'", (page_id,)).fetchone()
    summary = next(e for e in events if e.get("event") == "summary")
    return values, summary, align_row


# ---------------------------------------------------------------------------
# 本題: D=4（許容幅 row_gap=4 の境界内）— ON だけが行3を正しく拾う
# ---------------------------------------------------------------------------

def test_ac_f30_synthetic_material_shows_output_level_difference(tmp_path, monkeypatch):
    """AC-F30 の出力レベル再固定（issue #105）。

    実写 sample-1 では再現しなかった「OFF で値が乱れる／消える」効果を、
    合成素材（行高20px・行間隙4px・記入位置を境界ぎりぎりに固定）で実測する。

    D=4 は row_gap（許容幅）ちょうど——`snap.plan_face_snap` の `over_tolerance`
    判定は `>` なので境界は含まれ、吸着は適用される（07 §6 判断4-C・
    `test_snap_geometry.py` の境界テストと同じ扱い）。
    """
    off_values, off_summary, off_align = _run_scenario(
        tmp_path, monkeypatch, name="off", snap_blocks=False, d=4)
    on_values, on_summary, on_align = _run_scenario(
        tmp_path, monkeypatch, name="on", snap_blocks=True, d=4)

    # ★ここが AC-F30 の核心: 同じ刺激・同じ記入位置で、吸着 OFF/ON が
    # 出力セル値そのもの（Store 経由・render の母集団と同じ値）を変える
    assert off_values["t1_03_amt"] == ""     # OFF: 記入が行間隙へ落ち、消える
    assert on_values["t1_03_amt"] == "3"     # ON: 吸着が測った量を打ち消し、拾う

    # 境界（アンカー）行は OFF/ON どちらでも変わらない——差が出るのは
    # 境界へ寄せた行3だけであることの対照
    for row_no in (1, 2, 4, 5):
        fid = f"t1_0{row_no}_amt"
        assert off_values[fid] == on_values[fid] == str(row_no)

    # D-15（枠外率超過による様式不一致）を誘発していないことの確認
    # （アンカー行が「other」を吸わないよう設計した狙いどおりであることの実測）
    assert off_summary["format_mismatch"] == 0
    assert on_summary["format_mismatch"] == 0
    assert on_summary["snap_failsafe_pages"] == 0

    # 吸着の内訳（記録）も確認: OFF は未計測、ON は適用・許容幅ちょうど
    assert off_align == (0, -1.0, '{"applied": false, "reason": "disabled", "blocks": []}')
    on_detail = json.loads(on_align[2])
    assert on_detail["applied"] is True
    assert on_detail["blocks"][0]["measured_dy"] == 4
    assert on_detail["blocks"][0]["allow"] == 4


# ---------------------------------------------------------------------------
# 境界外: D=5（許容幅+1）— fail-safe に落ち、ON も OFF と同じ「消える」結果
# ---------------------------------------------------------------------------

def test_ac_f30_beyond_tolerance_snap_cannot_rescue_either(tmp_path, monkeypatch):
    """許容幅（row_gap=4）を1px超えると吸着は `over_tolerance` で面ごと
    fail-safe に落ち、ON も OFF と同じ結果（記入が消える）になる——
    「吸着が救える帯」の外側であることを出力レベルで固定する。
    """
    off_values, _, _ = _run_scenario(
        tmp_path, monkeypatch, name="off5", snap_blocks=False, d=5)
    on_values, on_summary, on_align = _run_scenario(
        tmp_path, monkeypatch, name="on5", snap_blocks=True, d=5)

    assert off_values["t1_03_amt"] == ""
    assert on_values["t1_03_amt"] == ""      # ON でも救えない
    assert on_summary["snap_failsafe_pages"] == 1
    on_detail = json.loads(on_align[2])
    assert on_detail["applied"] is False
    assert on_detail["blocks"][0]["reason"] == "over_tolerance"
