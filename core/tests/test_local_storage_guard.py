"""保存先の同期フォルダ検知（issue #8）の再レビューテスト。"""
import sys
from pathlib import Path
import json
import subprocess

import pytest

from chouhyo_ocr.paths import app_root, is_cloud_synced_path

PYTHON = Path(sys.executable)


def test_detects_cloud_sync_markers(tmp_path):
    assert is_cloud_synced_path(r"C:\Users\user\OneDrive\Desktop\wd")
    assert is_cloud_synced_path(r"C:\Users\user\Dropbox\data")
    assert is_cloud_synced_path(r"G:\My Drive") is False  # マーカー外は検知しない（限界の明示）
    assert is_cloud_synced_path(r"C:\Users\user\Google Drive\x")
    assert is_cloud_synced_path("\\\\fileserver\\share\\wd")  # UNC
    assert not is_cloud_synced_path(tmp_path)              # 通常のローカルは OK


def test_verify_flags_synced_workdir(tmp_path):
    onedrive_like = tmp_path / "OneDrive" / "wd"
    onedrive_like.mkdir(parents=True)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "workdir": str(onedrive_like),
        "output_dir": str(tmp_path / "out"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    r = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "verify"],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    events = [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    ls = next(e for e in events if e.get("check") == "local_storage")
    assert ls["ok"] is False
    assert "workdir" in ls["synced_dirs"]
    assert r.returncode == 1  # verify 全体も NG


# ---------- レビュー4巡目 M-12: 検知するクライアントを広げる ----------

@pytest.mark.parametrize("folder", [
    "OneDrive", "Dropbox", "Google Drive", "Box", "Box Sync",
    "Nextcloud", "ownCloud", "iCloudDrive", "Egnyte", "pCloud", "Seafile",
])
def test_detects_known_sync_clients(tmp_path, folder):
    """業務で使われる同期クライアントの既定フォルダを検知する。"""
    from chouhyo_ocr.paths import is_cloud_synced_path
    d = tmp_path / folder / "chouhyo" / "workdir"
    d.mkdir(parents=True)
    assert is_cloud_synced_path(d), f"{folder} を検知できていない"


@pytest.mark.parametrize("folder", [
    "dropbox_backup", "toolbox", "sandbox", "boxes", "work",
])
def test_does_not_flag_unrelated_folder_names(tmp_path, folder):
    """部分一致で無関係なフォルダを誤検知しない（成分の完全一致で見る）。"""
    from chouhyo_ocr.paths import is_cloud_synced_path
    d = tmp_path / folder / "workdir"
    d.mkdir(parents=True)
    assert not is_cloud_synced_path(d), f"{folder} を誤検知している"


def test_detects_company_suffixed_onedrive(tmp_path):
    """「OneDrive - 会社名」形式も検知する。"""
    from chouhyo_ocr.paths import is_cloud_synced_path
    d = tmp_path / "OneDrive - Contoso" / "workdir"
    d.mkdir(parents=True)
    assert is_cloud_synced_path(d)
