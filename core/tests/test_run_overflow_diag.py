"""run() が diag_overflow の判定を診断カウンタとして出す（issue #63・段2）。

`diag_overflow`（#63 段1・commit 04dd37e）は中間データを後から数える
CLI 専用ツールだったが、ここでは PM 判断（〓化はしない・値は変えない）に
従い、run の summary イベントへ `overflow_partial_fill` として同じ判定を
可視化のみで出す。出力 xlsx/csv のバイト列が変わらないことを本ファイルで
固定する。

保存済み S2 応答と展開済みサンプル画像に依存する（.gitignore 配下・
このマシン限定）。無い環境では skip（test_e2e_replay.py と同じ流儀）。
"""
from pathlib import Path
import shutil

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr import pipeline as pipeline_mod
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・展開画像が無い環境")


def _prep(tmp_path: Path, name: str) -> tuple[Path, Path, Config]:
    root = tmp_path / name
    input_dir = root / "input"
    input_dir.mkdir(parents=True)
    shutil.copy(PAGE_PNG, input_dir / "sample-1.png")
    replay_dir = root / "responses"
    replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")
    cfg = Config(unclear_threshold=0.4,
                output_dir=str(root / "out"), workdir=str(root / "wd"),
                log_dir=str(root / "logs"))
    return input_dir, replay_dir, cfg


def _run_and_capture(input_dir: Path, replay_dir: Path, cfg: Config):
    """run() を実行し、(summary, xlsx_path, csv_path) を返す。

    run() 自身は Summary しか返さない——出力パスは summary イベント
    （progress コールバック）からしか取れない（test_e2e_replay.py と違い
    render() を呼び直さない。呼び直すと2回目の render_seconds 計測などが
    余計に走るため、run() が実際に書いた出力をそのまま検証する）。
    """
    events: list[dict] = []
    summary = run(input_dir, TPL, cfg, ReplayClient(replay_dir),
                  progress=events.append)
    ev = next(e for e in events if e["event"] == "summary")
    return summary, ev, Path(ev["xlsx"]), Path(ev["csv"])


def test_summary_event_always_carries_overflow_partial_fill_key(tmp_path):
    """summary イベントは overflow_partial_fill を常に出す（0件でも・
    risky_cells/fallback_used と同じ「非ゼロのときだけ」ではなく「常に出す」
    流儀——snap_failsafe_pages/snap_excluded_pages と揃える）。
    """
    input_dir, replay_dir, cfg = _prep(tmp_path, "a")
    summary, ev, _xlsx, _csv = _run_and_capture(input_dir, replay_dir, cfg)
    assert isinstance(summary.overflow_partial_fill, int)
    assert summary.overflow_partial_fill >= 0
    assert "overflow_partial_fill" in ev
    assert ev["overflow_partial_fill"] == summary.overflow_partial_fill


def test_overflow_partial_fill_scan_does_not_change_output_bytes(
        tmp_path, monkeypatch):
    """issue #63: diag_overflow の統合は可視化のみ——xlsx/csv のバイト列を
    変えない。

    診断対象欄（郵便番号系）を空にして走査を実質無効化した実行と比べ、
    出力が完全一致することで確認する。target_fields をテンプレートに
    依らず空リストへ差し替えるだけで、run() 内の overflow スキャン分岐
    （`if overflow_fields:`）が丸ごと素通りになる——中間データの書き込み・
    割付・render 経路には一切触れていないので、バイト一致は「配線した
    分岐が値の計算パスと無関係」であることの直接証拠になる。
    """
    input_dir1, replay_dir1, cfg1 = _prep(tmp_path, "with_scan")
    _s1, _ev1, xlsx1, csv1 = _run_and_capture(input_dir1, replay_dir1, cfg1)

    input_dir2, replay_dir2, cfg2 = _prep(tmp_path, "without_scan")
    monkeypatch.setattr(pipeline_mod.diag_overflow, "target_fields",
                        lambda template: [])
    _s2, _ev2, xlsx2, csv2 = _run_and_capture(input_dir2, replay_dir2, cfg2)

    assert xlsx1.read_bytes() == xlsx2.read_bytes()
    assert csv1.read_bytes() == csv2.read_bytes()
