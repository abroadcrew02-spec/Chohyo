"""ブロック単位の枠吸着 — 永続化・再利用ガード・5経路（issue #75 (f)・Unit B）。

対象 AC: AC-F30（吸着が効く）・AC-F34/F41（fail-safe と件数）・AC-F36（remap）・
AC-F37（ON→OFF で3経路が拒否）・AC-F38（既定 OFF の記録）・AC-F42（記録の中身）・
AC-F43（旧方式の拒否は維持）・AC-F43b（**OFF のまま使う既存 workdir は
拒否されず Vision 0 回**）・AC-F56（debug-images）・AC-F57（中断→再 run）・
AC-F58（stale_done_pages）。

素材（`testdata/local/pages/sample-1.png`・`testdata/local/s2/...json`）は .gitignore 配下の
L2 素材で、無い環境では skip する（07 §7.2「素材依存 AC の扱い」）。
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image

from chouhyo_ocr import snap
from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import (OperationRefused, remap, render, run)
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import load_template
from chouhyo_ocr.vision_client import ReplayClient
from helpers_geom import _block_rect, shift_block_y, shift_response_vertices

def _first_existing(*candidates: Path) -> Path:
    """L2 素材の置き場を解決する（2026-09-03 の移設に追随するため）。

    素材は `testdata/local/pages/`（従来）から `testdata/local/`（移設先）へ移動中。
    どちらに置かれていても拾い、両方無ければ最初の候補を返して skipif に
    倒す——移設の途中でこのファイルだけが赤くならないようにする。
    """
    for c in candidates:
        if c.exists():
            return c
    return candidates[0]


RESP = _first_existing(
    app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json",
    app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json")
PAGE_PNG = _first_existing(
    app_root() / "testdata" / "local" / "pages" / "sample-1.png",
    app_root() / "testdata" / "local" / "pages" / "sample-1.png")
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・展開画像が無い環境")


class CountingReplay(ReplayClient):
    """送信回数を数える（AC-F43b の「Vision 0 回」の実測手段）。"""

    def __init__(self, resp_dir):
        super().__init__(resp_dir)
        self.calls = 0

    def annotate(self, png, page_id):
        self.calls += 1
        return super().annotate(png, page_id)


def _cfg(tmp_path: Path, *, snap_blocks: bool, sub: str = "wd") -> Config:
    return Config(unclear_threshold=0.4, snap_blocks=snap_blocks,
                  output_dir=str(tmp_path / "out"),
                  workdir=str(tmp_path / sub), log_dir=str(tmp_path / "logs"))


def _materialize(tmp_path: Path, *, delta: int = 0, name: str = "a") -> tuple[Path, Path]:
    """入力画像と replay 応答を作る。delta>0 なら back/detail の block1 だけを
    y へ delta px 動かし、**同じ変換を保存済み応答の頂点にも適用**する
    （適用しないと差が逆向きに出て ON のほうが悪化する・07 AC-F30 の注意書き）。"""
    inp = tmp_path / f"input_{name}"; inp.mkdir(parents=True, exist_ok=True)
    resp_dir = tmp_path / f"resp_{name}"; resp_dir.mkdir(parents=True, exist_ok=True)
    if delta == 0:
        shutil.copy(PAGE_PNG, inp / f"{name}.png")
        shutil.copy(RESP, resp_dir / f"{name}_p0001.json")
        return inp, resp_dir
    tpl = load_template(TPL)
    with Image.open(PAGE_PNG) as im:
        shifted = shift_block_y(im.convert("RGB"), tpl, "back", "detail", 1, delta)
    shifted.save(inp / f"{name}.png")
    region = _block_rect(tpl, "back", "detail", 1)
    resp = shift_response_vertices(
        json.loads(RESP.read_text(encoding="utf-8")), region, delta)
    (resp_dir / f"{name}_p0001.json").write_text(
        json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    return inp, resp_dir


def _run(tmp_path: Path, cfg: Config, *, delta: int = 0, name: str = "a",
         events: list | None = None) -> str:
    inp, resp_dir = _materialize(tmp_path, delta=delta, name=name)
    run(inp, TPL, cfg, ReplayClient(resp_dir),
        (events.append if events is not None else (lambda e: None)))
    return f"{name}_p0001"


def _values(cfg: Config, page_id: str) -> dict[str, str]:
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        return {fid: v[0] for fid, v in store.cells(page_id).items()}


def _alignment_row(cfg: Config, page_id: str, face_id: str):
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        return store.con.execute(
            "SELECT snap_enabled, snap_px, snap_detail, transform FROM alignment "
            "WHERE page_id=? AND face_id=?", (page_id, face_id)).fetchone()


# ---------------------------------------------------------------------------
# AC-F30: 吸着が効いていること（★これが緑でない完了報告はしない・07 §7.2）
# ---------------------------------------------------------------------------

def test_ac_f30_snap_cancels_the_measured_block_error(tmp_path):
    """AC-F30 の刺激（sample-1 back/detail block1 のみ y +4px）で、**吸着が
    測ったズレをそのまま打ち消している**ことを実測で示す。

    δ=4 の根拠は #70-2 の実測掃引（成立窓 δ=2〜5・境界は δ=5 ok → δ=6
    ambiguous・`helpers_geom.py` の docstring）。issue #75 本文の「y +3px」は
    残差の値であって刺激ではない。

    **面のシフトと吸着量の合計が δ ちょうどになること**を固定する——ここが
    「幾何レベルで効いている」の意味で、1px でも足りなければ落ちる
    （2026-09-03 の推定量切り替え〔`_axis_shift` の argmax → `_axis_residual`
    の符号付き中央値〕で 1px 過小が消えたことの実測。08 §6 判断4-D）。

    ⚠️ **07 AC-F30 が期待する「OFF では block1 にラベル混入または列ズレが
    出る」は、この素材では再現しない**（2026-09-03 実測）。δ=2/3/4/5 の
    4通りすべてで、OFF・ON とも無変形時と**セル値が完全一致**した（差 0 欄）。
    理由は幾何: detail の行の高さは 100px で、記入は行の内側にあるため
    3〜4px 動かしても `mapping` のバケツ（欄の矩形）から出ない。**位置合わせが
    許容する刺激（δ≤5）は、出力を変えるには小さすぎる**——07 §3.5 が
    「救う価値がある帯は 2〜4px」と書いた狭さがそのまま出た形。
    出力レベルの差の再現は素材の追加が要る（実装報告で申し送り）。
    """
    off_cfg = _cfg(tmp_path, snap_blocks=False, sub="wd_off")
    off_id = _run(tmp_path, off_cfg, delta=4, name="off")
    on_cfg = _cfg(tmp_path, snap_blocks=True, sub="wd_on")
    on_id = _run(tmp_path, on_cfg, delta=4, name="on")

    # OFF: (c) の残差が「補正されずに残ったズレ」を測っている
    residual = json.loads(_alignment_row(off_cfg, off_id, "back")[3])
    with Store(Path(off_cfg.workdir) / "intermediate.sqlite") as store:
        detail = store.con.execute(
            "SELECT align_residual_detail FROM alignment "
            "WHERE page_id=? AND face_id='back'", (off_id,)).fetchone()[0]
    off_block_error = json.loads(detail)["blocks"][1]["med"]
    assert off_block_error == 3, off_block_error   # δ=4 − 面シフト 1

    # ON: 同じ量が吸着量として適用されている（＝ズレを打ち消す向き・大きさ）
    on_snap = json.loads(_alignment_row(on_cfg, on_id, "back")[3])["snap"]
    assert on_snap["applied"] is True
    assert on_snap["dy"][1] == off_block_error
    # 面のシフト（1px）＋ブロックの吸着（3px）＝刺激 δ=4 ちょうど。
    # 推定量が 1px 過小だった時期はこの合計が 3 にしかならなかった
    assert residual["dy"] + on_snap["dy"][1] == 4
    # block0（動かしていない側）にも小さな量が出る。実測 -1px（2026-09-03・
    # align_page 経由・傾き補正込み）——罫線の実際の位置は整数格子に乗って
    # いないので「動かしていない＝0」にはならない。値そのものではなく
    # 「許容幅（detail は 4px）の内側に収まっていること」を固定する
    assert abs(on_snap["dy"][0]) <= 1, on_snap["dy"]
    assert json.loads(_alignment_row(on_cfg, on_id, "back")[2])["blocks"][1][
        "measured_dy"] == off_block_error
    # OFF は1ブロックも動かない
    assert json.loads(_alignment_row(off_cfg, off_id, "back")[3])["snap"]["dy"] == []
    assert residual["dy"] == 1        # 面シフトは従来どおり効いている


def test_ac_f30_output_is_unchanged_by_the_stimulus_in_this_material(tmp_path):
    """上のテストの但し書きを**実測として固定**する（2026-09-03）。

    δ=4 では OFF も ON も無変形時とセル値が一致する。将来この材料で出力差が
    出るようになったら（素材の差し替え・欄定義の変更・割付の変更）ここが
    落ちて気づける——「AC-F30 が出力レベルで再現しない」という現在の事実を、
    コメントではなくテストで持つ。
    """
    base_cfg = _cfg(tmp_path, snap_blocks=False, sub="wd_base")
    baseline = _values(base_cfg, _run(tmp_path, base_cfg, delta=0, name="base"))
    off_cfg = _cfg(tmp_path, snap_blocks=False, sub="wd_off")
    off = _values(off_cfg, _run(tmp_path, off_cfg, delta=4, name="off"))
    on_cfg = _cfg(tmp_path, snap_blocks=True, sub="wd_on")
    on = _values(on_cfg, _run(tmp_path, on_cfg, delta=4, name="on"))
    assert off == baseline
    assert on == baseline


# ---------------------------------------------------------------------------
# AC-F38・AC-F42: 記録（既定 OFF／ON の内訳）
# ---------------------------------------------------------------------------

def test_ac_f38_default_is_off_and_recorded_as_disabled(tmp_path):
    """AC-F38: 設定省略時の既定が OFF。OFF の run は無効であることを記録に残す。"""
    assert Config().snap_blocks is False
    cfg = _cfg(tmp_path, snap_blocks=False)
    pid = _run(tmp_path, cfg)
    for face_id in ("front", "back"):
        snap_enabled, snap_px, detail, transform = _alignment_row(cfg, pid, face_id)
        assert snap_enabled == 0
        assert snap_px == -1.0
        d = json.loads(detail)
        assert (d["applied"], d["reason"], d["blocks"]) == (False, "disabled", [])
        assert json.loads(transform)["snap"] == {"v": 1, "applied": False, "dy": []}


def test_ac_f42_on_records_block_level_measurements(tmp_path):
    """AC-F42: ON の run はブロック単位の測定量・一致本数・fail-safe 有無を残す。
    `transform["snap"]["dy"]` の長さ＝ブロック数（添字が block_idx と揃う）。"""
    cfg = _cfg(tmp_path, snap_blocks=True)
    pid = _run(tmp_path, cfg)
    template = load_template(TPL)
    for face in template.faces:
        snap_enabled, snap_px, detail, transform = _alignment_row(cfg, pid, face.face_id)
        assert snap_enabled == 1
        d = json.loads(detail)
        assert len(d["blocks"]) == len(face.table_geoms)
        for b in d["blocks"]:
            assert set(b) == {"block_idx", "measured_dy", "matched", "need",
                              "expected", "allow", "reason"}
            assert b["expected"] == len(face.table_geoms[b["block_idx"]].h_lines)
            assert b["allow"] == face.table_geoms[b["block_idx"]].row_gap
        blob = json.loads(transform)["snap"]
        assert len(blob["dy"]) == len(face.table_geoms)
        if blob["applied"]:
            assert snap_px == float(max(abs(v) for v in blob["dy"]))


def test_snap_result_logged_without_names(tmp_path):
    """ログは面ごとに1イベント。`face_id`・欄名は出さず `face_idx` と件数のみ
    （Q-S1・§1.2 の白リスト。渡しても黙って落ちる罠を踏まない）。"""
    from chouhyo_ocr import cli

    inp, resp_dir = _materialize(tmp_path)
    log_dir = tmp_path / "logs"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"), "workdir": str(tmp_path / "wd"),
        "log_dir": str(log_dir), "snap_blocks": True}), encoding="utf-8")
    assert cli.main(["--config", str(cfg_path), "run", "--input", str(inp),
                     "--template", str(TPL), "--replay", str(resp_dir)]) == 0
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in log_dir.glob("*.log"))
    lines = [ln for ln in text.splitlines() if "snap_result face_idx=" in ln]
    assert len(lines) == 2, lines            # front / back
    for ln in lines:
        assert "snap_blocks=" in ln and "snap_dy_max=" in ln and "snap_reason=" in ln
        assert "face_id=" not in ln
        assert "detail" not in ln and "family" not in ln   # 表名・欄名を出さない


# ---------------------------------------------------------------------------
# AC-F41 / 4-H: 件数（2項目・二重計上なし）
# ---------------------------------------------------------------------------

def test_ac_f41_summary_always_carries_both_counters(tmp_path):
    """FR-F41: fail-safe と対象外を**別項目**で、0 でも常に出す。"""
    events: list = []
    cfg = _cfg(tmp_path, snap_blocks=False)
    _run(tmp_path, cfg, events=events)
    ev = next(e for e in events if e.get("event") == "summary")
    assert ev["snap_failsafe_pages"] == 0
    assert ev["snap_excluded_pages"] == 0
    assert ev["snap_failsafe_pages"] + ev["snap_excluded_pages"] <= ev["pages"]


def test_ac_f41_excluded_face_makes_the_whole_page_excluded(tmp_path):
    """AC-F35/F41: 期待横線 4 本以下の表を含む面は対象外として数え、
    fail-safe 件数には入れない（原因が違うので混ぜない）。"""
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    for blk in raw["faces"][0]["tables"][0]["blocks"]:
        blk["rows"] = 3
    raw["faces"][0]["fields"] = [f for f in raw["faces"][0]["fields"]
                                 if f["field_id"] != "person_備考"]
    small_tpl = tmp_path / "small.json"
    small_tpl.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    inp, resp_dir = _materialize(tmp_path)
    cfg = _cfg(tmp_path, snap_blocks=True)
    events: list = []
    run(inp, small_tpl, cfg, ReplayClient(resp_dir), events.append)
    ev = next(e for e in events if e.get("event") == "summary")
    assert ev["snap_excluded_pages"] == 1
    assert ev["snap_failsafe_pages"] == 0
    assert ev["snap_excluded_pages"] + ev["snap_failsafe_pages"] <= ev["pages"]
    detail = json.loads(_alignment_row(cfg, "a_p0001", "front")[2])
    assert detail["reason"] == "excluded_small_table"


# ---------------------------------------------------------------------------
# AC-F36 / AC-F56 / AC-F57: 5経路が同じ座標を復元する
# ---------------------------------------------------------------------------

def test_ac_f36_remap_reproduces_run_coordinates(tmp_path):
    """AC-F36: ON で run した後の remap（assign と era.score_cell の両方）が
    run と同じ座標で行われ、cell 値が1つも変わらない。"""
    cfg = _cfg(tmp_path, snap_blocks=True)
    pid = _run(tmp_path, cfg, delta=4)
    before = _values(cfg, pid)
    assert remap(TPL, cfg) == 1
    assert _values(cfg, pid) == before


def test_ac_f56_debug_images_draw_snapped_rects(tmp_path):
    """AC-F56: debug-images が描く枠は**吸着後**の座標。

    画素走査ではなく、描画に使う座標そのもの（`apply_snap` の戻り値）で
    確認する簡易版——`write_debug_images` は run・remap と同じ2行
    （`snap_geometry` → `apply_snap`）を通るので、そこが吸着後であれば
    描かれる rect も吸着後になる。画像が実際に書けることも同時に見る。
    """
    from chouhyo_ocr.debug_images import write_debug_images

    cfg = _cfg(tmp_path, snap_blocks=True)
    pid = _run(tmp_path, cfg, delta=4)
    template = load_template(TPL)
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        rows = store.snap_geometry(pid)
        t2 = snap.apply_snap(template, snap.from_store_rows(rows))
        made = write_debug_images(store, template, Path(cfg.workdir) / "aligned",
                                  tmp_path / "dbg", cfg, page_ids=[pid])
    assert made and made[0].exists()
    dy = rows["back"][0]["dy"]
    assert dy[1] != 0, "刺激が吸着されていない（この AC の前提が崩れている）"
    moved = {c.field_id: c.rect.y for c in t2.cells if c.block_idx == 1
             and c.face_id == "back"}
    orig = {c.field_id: c.rect.y for c in template.cells if c.block_idx == 1
            and c.face_id == "back"}
    assert all(moved[f] == orig[f] + dy[1] for f in moved)


def test_ac_f57_resume_restores_snapped_coordinates(tmp_path):
    """AC-F57: ON で run を中断（send_limit=0）し、aligned のまま再 run すると
    `_restore_alignment` が吸着後座標を復元する（再整列した回と同じ値）。"""
    inp, resp_dir = _materialize(tmp_path, delta=4)
    stopped = Config(unclear_threshold=0.4, snap_blocks=True,
                     send_limit=0, output_dir=str(tmp_path / "out"),
                     workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    run(inp, TPL, stopped, ReplayClient(resp_dir))
    with Store(Path(stopped.workdir) / "intermediate.sqlite") as store:
        assert store.page("a_p0001")["state"] == "aligned"
        first = store.snap_geometry("a_p0001")

    resumed = Config(unclear_threshold=0.4, snap_blocks=True,
                     output_dir=str(tmp_path / "out"),
                     workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    events: list = []
    run(inp, TPL, resumed, ReplayClient(resp_dir), events.append)
    with Store(Path(resumed.workdir) / "intermediate.sqlite") as store:
        assert store.snap_geometry("a_p0001") == first     # 座標が変わっていない
    ref = _cfg(tmp_path, snap_blocks=True, sub="wd_ref")
    ref_id = _run(tmp_path, ref, delta=4, name="ref")
    assert _values(resumed, "a_p0001") == _values(ref, ref_id)


def test_restore_is_refused_when_snap_setting_changed(tmp_path):
    """AC-F58 の前半: ON で aligned まで進めた後に OFF で再 run すると、
    `_restore_alignment` は ON の座標を流用せず再整列する。"""
    from chouhyo_ocr.align import ALGO_VERSION, template_hash
    from chouhyo_ocr.pipeline import _restore_alignment

    inp, resp_dir = _materialize(tmp_path, delta=4)
    cfg = Config(unclear_threshold=0.4, snap_blocks=True, send_limit=0,
                 output_dir=str(tmp_path / "out"), workdir=str(tmp_path / "wd"),
                 log_dir=str(tmp_path / "logs"))
    run(inp, TPL, cfg, ReplayClient(resp_dir))
    template = load_template(TPL)
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    from chouhyo_ocr.align import geometry_hash
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        args = (store, template, Path(cfg.workdir) / "aligned", "a_p0001",
                geometry_hash(raw), ALGO_VERSION, template_hash(raw))
        assert _restore_alignment(*args, snap_enabled=True) is not None
        assert _restore_alignment(*args, snap_enabled=False) is None


# ---------------------------------------------------------------------------
# AC-F37 / AC-F43 / AC-F43b / AC-F58: 再利用ガード
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["render", "remap", "debug-images"])
def test_ac_f37_all_three_paths_refuse_when_snap_flag_differs(tmp_path, path):
    """AC-F37: ON で run した中間データに OFF を設定して render／remap／
    debug-images → **3経路すべてが拒否**。既存3ハッシュは一致させたまま
    （テンプレートを変えない）で止まる＝4つ目のガードが効いた証拠。"""
    on = _cfg(tmp_path, snap_blocks=True)
    _run(tmp_path, on)
    off = _cfg(tmp_path, snap_blocks=False)   # 同じ workdir・設定だけ OFF
    with pytest.raises(OperationRefused, match="吸着"):
        if path == "render":
            render(TPL, off, timestamp="x")
        elif path == "remap":
            remap(TPL, off)
        else:
            from chouhyo_ocr.align import geometry_hash, template_hash
            from chouhyo_ocr.pipeline import check_reusable
            raw = json.loads(TPL.read_text(encoding="utf-8"))
            with Store(Path(off.workdir) / "intermediate.sqlite") as store:
                check_reusable(store, geometry_hash(raw), template_hash(raw),
                               check_template=True, snap_enabled=off.snap_blocks)
    if path == "render":
        assert not list((tmp_path / "out").glob("output_x.*"))   # 1バイトも書かない


def test_ac_f43b_existing_off_workdir_is_not_refused_and_sends_nothing(tmp_path):
    """AC-F43b（PM 確定判断 A13）: **既定 OFF で作った既存 workdir を OFF の
    まま使う**なら、render も remap も拒否されず Vision 送信は 0 回。

    `ALGO_VERSION` を上げていれば全件が陳腐化して再送（課金）を求められた
    ——上げずに吸着フラグで守る、という判断が守られていることの実証。
    """
    cfg = _cfg(tmp_path, snap_blocks=False)
    inp, resp_dir = _materialize(tmp_path)
    client = CountingReplay(resp_dir)
    run(inp, TPL, cfg, client)
    assert client.calls == 1                      # 初回の1枚だけ

    render(TPL, cfg, timestamp="ok")              # 拒否されない
    assert remap(TPL, cfg) == 1
    render(TPL, cfg, timestamp="ok2")
    client2 = CountingReplay(resp_dir)
    run(inp, TPL, cfg, client2)                   # 再 run も再送しない
    assert client2.calls == 0
    assert list((tmp_path / "out").glob("output_ok.*"))


def test_ac_f43_old_algo_version_is_still_refused(tmp_path):
    """AC-F43（維持）: `ALGO_VERSION` "2" 以外で作られた中間データは従来どおり
    拒否される。吸着フラグを足しても既存のガードは弱まっていない。"""
    cfg = _cfg(tmp_path, snap_blocks=False)
    _run(tmp_path, cfg)
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        store.con.execute("UPDATE alignment SET algo_version='1'")
        store.con.commit()
    with pytest.raises(OperationRefused, match="位置合わせ方式"):
        render(TPL, cfg, timestamp="y")


def test_ac_f58_stale_done_pages_detects_snap_flag_change(tmp_path):
    """AC-F58: ON で done にした後 OFF で run → `stale_done_pages` が検出し、
    オプトイン無しでは API を1回も叩かずに中止する（ON の done と OFF の
    新規が1出力に混ざらない）。"""
    on = _cfg(tmp_path, snap_blocks=True)
    inp, resp_dir = _materialize(tmp_path)
    run(inp, TPL, on, ReplayClient(resp_dir))

    off = _cfg(tmp_path, snap_blocks=False)
    with Store(Path(off.workdir) / "intermediate.sqlite") as store:
        from chouhyo_ocr.align import ALGO_VERSION, geometry_hash, template_hash
        raw = json.loads(TPL.read_text(encoding="utf-8"))
        assert store.stale_done_pages(geometry_hash(raw), template_hash(raw),
                                      ALGO_VERSION, 0) == ["a_p0001"]
        assert store.stale_done_pages(geometry_hash(raw), template_hash(raw),
                                      ALGO_VERSION, 1) == []
    client = CountingReplay(resp_dir)
    with pytest.raises(OperationRefused, match="ページ"):
        run(inp, TPL, off, client)
    assert client.calls == 0


# ---------------------------------------------------------------------------
# 旧 DB 互換（既存 workdir に列が足される）
# ---------------------------------------------------------------------------

def test_old_db_gains_snap_columns_with_off_defaults_and_still_reuses(tmp_path):
    """列を持たない古い DB を開くと3列が足され、既存行は 0／-1／'' になる。
    そのまま OFF で再 run すると再利用が成立し、送信は 0 回（AC-F43b の
    「既存 workdir」側の実体）。"""
    cfg = _cfg(tmp_path, snap_blocks=False)
    inp, resp_dir = _materialize(tmp_path)
    run(inp, TPL, cfg, ReplayClient(resp_dir))
    db = Path(cfg.workdir) / "intermediate.sqlite"
    import sqlite3
    con = sqlite3.connect(db)
    for col in ("snap_enabled", "snap_px", "snap_detail"):
        con.execute(f"ALTER TABLE alignment DROP COLUMN {col}")
    # 旧版が書いた transform は "snap" キーを持たない（列を消すだけでは
    # 再現しない）。本物の旧 workdir と同じ形にしてから開き直す
    for face_id, blob in con.execute(
            "SELECT face_id, transform FROM alignment").fetchall():
        t = json.loads(blob)
        t.pop("snap", None)
        con.execute("UPDATE alignment SET transform=? WHERE face_id=?",
                    (json.dumps(t), face_id))
    con.commit()
    con.close()

    with Store(db) as store:
        cols = {r[1] for r in store.con.execute("PRAGMA table_info(alignment)")}
        assert {"snap_enabled", "snap_px", "snap_detail"} <= cols
        assert store.con.execute(
            "SELECT snap_enabled, snap_px, snap_detail FROM alignment LIMIT 1"
        ).fetchone() == (0, -1.0, "")
        # 旧行は transform に "snap" を持たない → 恒等（apply_snap が同一
        # オブジェクトを返す）
        assert store.snap_geometry("a_p0001")["front"] == ({}, 0)

    events: list = []
    client = CountingReplay(resp_dir)
    run(inp, TPL, cfg, client, events.append)
    ev = next(e for e in events if e.get("event") == "summary")
    assert client.calls == 0
    assert ev["reused_pages"] == ev["pages"] == 1
