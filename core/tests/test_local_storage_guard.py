"""保存先の同期フォルダ検知（issue #8）の再レビューテスト。"""
import json
import subprocess

from chouhyo_ocr.paths import app_root, is_cloud_synced_path

PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"


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
