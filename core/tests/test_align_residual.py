"""位置合わせ残差・吸着量の記録（issue #74 (c)・FR-F32・08 §5）のテスト。

対象 AC: AC-F29（残差の算出・記録・ログ）・NFR-F08（既存判定を変えない）。

合成罫線画像のヘルパー（`_make_geom`/`_draw_geoms`）は
`test_detect_frames.py` の `_draw_table` と同型（罫線だけの2値画像を作る）。
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr.align import _axis_residual, _nearest_signed_dist, estimate_shift
from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import Face, Rect, TableGeom, load_template
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

needs_real_data = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・展開画像が無い環境")


# ---------------------------------------------------------------------------
# 合成罫線画像のヘルパー
# ---------------------------------------------------------------------------

def _make_geom(ox: int, cols: int, h_offsets: list, col_width: int) -> TableGeom:
    """罫線期待位置を持つ TableGeom を組み立てる（面ローカル座標）。"""
    v_lines = tuple(ox + col_width * i for i in range(cols + 1))
    return TableGeom(x_min=ox, x_max=ox + col_width * cols,
                     y_min=min(h_offsets), y_max=max(h_offsets),
                     h_lines=tuple(sorted(h_offsets)), v_lines=v_lines)


def _draw_geoms(size: tuple, geoms_with_offset: list) -> "np.ndarray":
    """罫線だけの合成画像（True=インク）を作る。各 geom を dy だけ縦にずらして描く。"""
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    for g, dy in geoms_with_offset:
        for y in g.h_lines:
            draw.line((g.x_min, y + dy, g.x_max, y + dy), fill=0, width=2)
        for x in g.v_lines:
            draw.line((x, g.y_min + dy, x, g.y_max + dy), fill=0, width=2)
    return np.asarray(img) < 128


# 横並び2ブロック（sample-1 の back 面と同型: 2ブロックが h_lines（y範囲）を
# 共有する左右分割構成）。block0 だけ余分に5行を持たせて非対称にする——
# 完全対称だと dy=0（無変形解）と dy=delta（block1 が動いた分だけシフトした
# 解）のスコアが常に同点になり ambiguous に落ちて ok=True に到達しないこと
# を実測で確認済み（08 §5.10 R-1「食い違ったら値を合わせにいかず、原因を
# 報告する」——事前に決め打ちせず、この構成を実測して選んだ）。block0 の
# 余分行が「動いていない側」を一致本数で支配的にし、グローバル dy=0 で
# 安定して match する
_ROW_PITCH = 60
_COL_WIDTH = 80
_COLS = 4
_OY = 60
_SHARED_H = [_OY + _ROW_PITCH * i for i in range(15)]
_EXTRA0_H = [_SHARED_H[-1] + _ROW_PITCH * i for i in range(1, 6)]
_EXP_H_UNIQ = len(set(_SHARED_H) | set(_EXTRA0_H))  # 20（15 共有 + 5 余分）


def _two_block_face() -> tuple[Face, tuple]:
    g0 = _make_geom(50, _COLS, _SHARED_H + _EXTRA0_H, _COL_WIDTH)
    g1 = _make_geom(50 + _COL_WIDTH * _COLS, _COLS, _SHARED_H, _COL_WIDTH)
    n_x = max(0, _COL_WIDTH // 2 - 2)
    n_y = max(0, _ROW_PITCH // 2 - 2)
    size = (50 + _COL_WIDTH * _COLS * 2 + 50, max(g0.y_max, g1.y_max) + 100)
    face = Face(face_id="test", page_offset=0, source_rect=Rect(0, 0, *size),
               exclusions=(), table_zones=(), table_geoms=(g0, g1),
               shift_limits=(n_x, n_y))
    return face, size


# ---------------------------------------------------------------------------
# AC-F29（残差の算出）— 合成罫線画像
# ---------------------------------------------------------------------------

def test_residual_unshifted_is_zero_median_with_full_pairs():
    """無変形なら面の h 残差は med=0・pairs=期待線の本数・unpaired=0。"""
    face, size = _two_block_face()
    binary = _draw_geoms(size, [(g, 0) for g in face.table_geoms])
    est = estimate_shift(binary, face, dpi=300)
    assert est.ok is True, est.reason
    r = est.residual
    assert r is not None
    assert (r.h.med, r.h.max, r.h.pairs, r.h.unpaired) == (0, 0, _EXP_H_UNIQ, 0)


def test_residual_block_shift_is_isolated_per_block():
    """片ブロックだけ y へ 4px 動かすと、そのブロックの残差だけが動く。

    §6 の実測（sample-1・block_idx=1 が 3px・block_idx=0 が -1px）とは
    値が一致しない（08 §5.10 R-1・食い違いは実装報告で報告済み）——
    このテストは L1 合成画像で実測した値（block1=4px・block0=0px、
    グローバル dy=0 に収束する構成）をそのまま期待値に採用する。
    ブロック単位の残差計算が「動いていないブロックへ波及しない」ことの
    実証が目的で、§6 と数値そのものが一致することは目的にしない。
    """
    face, size = _two_block_face()
    g0, g1 = face.table_geoms
    delta = 4
    binary = _draw_geoms(size, [(g0, 0), (g1, delta)])
    est = estimate_shift(binary, face, dpi=300)
    assert est.ok is True, est.reason
    assert est.dy == 0  # グローバルシフトは動いていない block0 側に収束する
    r = est.residual
    assert r is not None
    assert len(r.blocks) == 2
    assert r.blocks[0].block_idx == 0 and r.blocks[1].block_idx == 1
    assert (r.blocks[0].med, r.blocks[0].max, r.blocks[0].unpaired) == (0, 0, 0)
    assert (r.blocks[1].med, r.blocks[1].max, r.blocks[1].unpaired) == (delta, delta, 0)
    # 面全体の残差にはブロックのズレが現れない（08 §5.1 (b)・合併集合から
    # ブロック残差を導出できない理由の実証）
    assert r.h.med == 0 and r.h.max == 0


def test_residual_is_none_on_alignment_failure():
    """罫線ゼロ（位置合わせ失敗）なら ok=False・residual は None（未計測）。"""
    face, size = _two_block_face()
    binary = np.zeros((size[1], size[0]), dtype=bool)
    est = estimate_shift(binary, face, dpi=300)
    assert est.ok is False
    assert est.residual is None


# ---------------------------------------------------------------------------
# 境界値 — `_axis_residual`／`_nearest_signed_dist`（08 §5.2 のアルゴリズム
# 単位を直接検証。`helpers_geom.py` が `_exclusion_mask`／`_otsu` を直接
# import して検証しているのと同じ流儀——align.py のモジュール内プライベート
# 関数は「クラスの内部実装」ではなく合成画像を介さず検証すべきアルゴリズム
# 単位として扱う）
# ---------------------------------------------------------------------------

def test_axis_residual_empty_expected_axis_yields_all_zero():
    """期待線が0本の軸: ループが回らず (med, max, pairs, unpaired) は全て0。
    例外にならないこと。"""
    r = _axis_residual(expected=[], detected_sorted=[10, 20, 30], shift=0, window=5)
    assert (r.med, r.max, r.pairs, r.unpaired) == (0, 0, 0, 0)


def test_axis_residual_empty_detected_axis_all_unpaired():
    """検出線が0本の軸: 全期待線が対応なし（unpaired）になり、pairs=0。"""
    r = _axis_residual(expected=[10, 20, 30], detected_sorted=[], shift=0, window=5)
    assert (r.med, r.max, r.pairs, r.unpaired) == (0, 0, 0, 3)


def test_nearest_signed_dist_empty_detected_returns_none():
    """`_nearest_signed_dist` 単体でも空集合は None（`_axis_residual` が
    unpaired に倒す一次情報）。"""
    assert _nearest_signed_dist([], 100) is None


@pytest.mark.parametrize("dist,expect_paired", [(5, True), (6, False)])
def test_axis_residual_window_boundary_is_inclusive(dist, expect_paired):
    """対応窓ちょうどの距離は「対応あり」。設計文言『距離が対応窓を超えたら
    対応なし』（08 §5.2）＝ `> window` で unpaired、`<= window` は対応あり。
    window=5 のとき距離5は対応あり・距離6は対応なし。"""
    window = 5
    r = _axis_residual(expected=[100], detected_sorted=[100 + dist], shift=0, window=window)
    if expect_paired:
        assert (r.pairs, r.unpaired) == (1, 0)
        assert r.med == dist
    else:
        assert (r.pairs, r.unpaired) == (0, 1)


def test_axis_residual_even_count_median_takes_smaller_side():
    """偶数個（4件）の中央値は小さい側を採る（08 §5.2）。
    距離 [1, 2, 3, 4] のとき中央の2値は 2 と 3 — 小さい側の 2 を med とする。
    期待線を離しておくことで、各期待線が個別の検出線と1対1対応する
    （近接させると別の期待線に最近傍を奪われ距離が意図どおりにならない）。"""
    expected = [0, 10, 20, 30]
    detected = [1, 12, 23, 34]  # 距離: 1, 2, 3, 4
    r = _axis_residual(expected=expected, detected_sorted=sorted(detected),
                       shift=0, window=10)
    assert r.pairs == 4
    assert r.med == 2  # (2,3) の小さい側


# ---------------------------------------------------------------------------
# AC-F29（記録・ログ）— 実データ（sample-1.png・保存済み S2 応答）
# ---------------------------------------------------------------------------

def _run_once(tmp_path: Path, stem: str = "case") -> tuple[Config, str, Path, Path]:
    """1ページ run（ReplayClient・課金ゼロ）して (Config, page_id, input_dir,
    replay_dir) を返す。"""
    input_dir = tmp_path / "input"; input_dir.mkdir()
    shutil.copy(PAGE_PNG, input_dir / f"{stem}.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / f"{stem}_p0001.json")
    cfg = Config(output_dir=str(tmp_path / "out"), workdir=str(tmp_path / "wd"),
                log_dir=str(tmp_path / "logs"))
    run(input_dir, TPL, cfg, ReplayClient(replay_dir))
    return cfg, f"{stem}_p0001", input_dir, replay_dir


@needs_real_data
def test_align_residual_recorded_in_db(tmp_path):
    """1ページ run 後、alignment に align_residual_px/detail が記録される。"""
    cfg, page_id, _input_dir, _replay_dir = _run_once(tmp_path)
    template = load_template(TPL)
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        for face in template.faces:
            row = store.con.execute(
                "SELECT align_residual_px, align_residual_detail FROM alignment "
                "WHERE page_id=? AND face_id=?", (page_id, face.face_id)).fetchone()
            assert row is not None, f"face_id={face.face_id} の alignment 行が無い"
            px, detail = row
            assert px >= 0
            d = json.loads(detail)
            assert set(d.keys()) == {"h", "v", "blocks"}
            assert len(d["blocks"]) == len(face.table_geoms)


@needs_real_data
def test_align_residual_logged_with_face_idx_not_face_id(tmp_path):
    """app.log に align_residual イベントが出て、face_idx はあり face_id は無い
    （`test_leak_guards.py` と同型・§1.5・Q-S1）。

    `log.info` は `logging_safe.init()`（cli._load_config_and_init_log が
    呼ぶ）を経由していないと何も書かない——`pipeline.run()` を直接呼ぶ
    他のテスト（DB 記録・失敗面・旧 DB 互換）はログを見ないため直接呼び
    出しで足りるが、ここだけは `cli.main([...])` 経由（CLI エントリ
    ポイント）で実行し、実際にファイルへ書かれることを確認する。
    """
    from chouhyo_ocr import cli

    input_dir = tmp_path / "input"; input_dir.mkdir()
    shutil.copy(PAGE_PNG, input_dir / "case.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "case_p0001.json")
    log_dir = tmp_path / "logs"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(log_dir),
    }), encoding="utf-8")
    rc = cli.main(["--config", str(cfg_path), "run",
                  "--input", str(input_dir), "--template", str(TPL),
                  "--replay", str(replay_dir)])
    assert rc == 0
    log_text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in log_dir.glob("*.log"))
    # ログ行は "<asctime> <levelname> <event> <k=v ...>"（フィールドはキーの
    # アルファベット順・logging_safe._fmt）。event 自体は先頭一致では拾えない
    # ため、事後に確定した最初のフィールド名（face_idx）まで含めて検索する
    align_lines = [ln for ln in log_text.splitlines()
                  if "align_residual face_idx=" in ln]
    assert align_lines, "align_residual イベントが app.log に出ていない"
    for ln in align_lines:
        assert "page_id=" in ln
        assert "face_idx=" in ln
        assert "face_id=" not in ln
        assert "res_h=" in ln and "res_v=" in ln
        assert "res_pairs=" in ln and "res_unpaired=" in ln


@needs_real_data
def test_align_residual_not_recorded_on_alignment_failure(tmp_path):
    """位置合わせ失敗（罫線ゼロ）のページは alignment に行が作られない
    （upsert_alignment は成功パスのみ呼ばれる・08 §5.2）。"""
    input_dir = tmp_path / "input"; input_dir.mkdir()
    img = Image.open(PAGE_PNG).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width, img.height), fill="white")  # 全消し＝線ゼロ
    img.save(input_dir / "erased.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "erased_p0001.json")
    cfg = Config(output_dir=str(tmp_path / "out"), workdir=str(tmp_path / "wd"),
                log_dir=str(tmp_path / "logs"))
    run(input_dir, TPL, cfg, ReplayClient(replay_dir))
    with Store(Path(cfg.workdir) / "intermediate.sqlite") as store:
        rows = store.con.execute(
            "SELECT * FROM alignment WHERE page_id=?", ("erased_p0001",)).fetchall()
        assert rows == []


# ---------------------------------------------------------------------------
# 旧 DB 互換
# ---------------------------------------------------------------------------

@needs_real_data
def test_old_db_gains_residual_columns_with_unmeasured_defaults_and_still_reuses(tmp_path):
    """追加列なしの alignment を持つ DB を開くと列が増え、既存行は -1/'' に
    なる。同じ DB で2回目の run が再整列に落ちず再利用に成功する（#45）。

    手順: 1回目の run で本物の DB を作る → その DB から新2列だけを
    DROP COLUMN で取り除いて「旧 DB」を模擬する → Store で開き直して列が
    復元され既定値になることを確認 → 同じ workdir で2回目の run を実行し、
    reused_pages が機能する（新列が再利用判定の材料に混ざらない・
    08 §5.9 不変条件7）ことを確認する。
    """
    cfg, page_id, input_dir, replay_dir = _run_once(tmp_path)
    db_path = Path(cfg.workdir) / "intermediate.sqlite"

    con = sqlite3.connect(db_path)
    con.execute("ALTER TABLE alignment DROP COLUMN align_residual_px")
    con.execute("ALTER TABLE alignment DROP COLUMN align_residual_detail")
    con.commit()
    cols_before = {r[1] for r in con.execute("PRAGMA table_info(alignment)")}
    assert "align_residual_px" not in cols_before
    con.close()

    with Store(db_path) as store:
        cols_after = {r[1] for r in store.con.execute("PRAGMA table_info(alignment)")}
        assert {"align_residual_px", "align_residual_detail"} <= cols_after
        row = store.con.execute(
            "SELECT align_residual_px, align_residual_detail FROM alignment "
            "WHERE page_id=?", (page_id,)).fetchone()
        assert row == (-1.0, "")

    events = []
    run(input_dir, TPL, cfg, ReplayClient(replay_dir), events.append)
    summary_ev = next(e for e in events if e.get("event") == "summary")
    assert summary_ev["reused_pages"] == summary_ev["pages"] == 1
    assert summary_ev["api_calls"] == 0
