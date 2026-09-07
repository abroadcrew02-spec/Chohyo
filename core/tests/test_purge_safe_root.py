"""issue #108: purge --preview／--yes の安全確認（応急 (a)）。

対象:
- `config.is_unsafe_workdir_root`（ドライブ直下・UNC・`.`/`..`・空・
  ユーザープロファイル直下の判定・ファイルシステムに触れない）
- `cli.cmd_purge` の `--preview`（削除しない・件数と絶対パスだけ出す）
- `cli.cmd_purge` の `--yes`（unsafe_root／other_items のいずれかがあれば
  削除せず rc=2 で拒否する）

いずれのテストも、拒否されるケースでは実ファイル削除が起きないことを
テストの前提にしている（`C:\\` や UNC のような実在パスに触れるテストは、
拒否が文字列判定だけで完結する＝ファイルシステムへ実アクセスしない
（`_unsafe_workdir_reason` が文字列判定を先に見る）ことに支えられている）。
"""
import json

import pytest

from chouhyo_ocr import cli, cred_store
from chouhyo_ocr.config import is_unsafe_workdir_root


# ========== config.is_unsafe_workdir_root（単体・ファイルシステム非依存） ==========

@pytest.mark.parametrize("raw,expected", [
    ("", "empty"),
    ("   ", "empty"),
    ("C:\\", "drive_root"),
    ("C:", "drive_root"),
    (".", "dot"),
    ("..", "dot"),
    ("..\\workdir", "dot"),
    ("workdir\\..\\..\\etc", "dot"),
    ("\\\\server\\share", "unc"),
    ("\\\\?\\UNC\\server\\share", "unc"),
])
def test_unsafe_reasons(raw, expected):
    assert is_unsafe_workdir_root(raw) == expected


@pytest.mark.parametrize("raw", [
    "workdir",
    ".\\workdir",
    "C:\\work\\wd",
    "\\\\?\\C:\\work\\wd",  # verbatim local はプレフィックスを剥がして通常判定
])
def test_safe_forms(raw):
    assert is_unsafe_workdir_root(raw) is None


def test_profile_root_rejected(monkeypatch, tmp_path):
    """%USERPROFILE% 直下そのもの・Documents/Desktop/Downloads は拒否する。"""
    profile = tmp_path / "Users" / "someone"
    monkeypatch.setenv("USERPROFILE", str(profile))
    assert is_unsafe_workdir_root(str(profile)) == "profile_root"
    for sub in ("Documents", "Desktop", "Downloads"):
        assert is_unsafe_workdir_root(str(profile / sub)) == "profile_root"


def test_profile_root_subfolder_is_not_rejected(monkeypatch, tmp_path):
    """プロファイル直下そのものではなく、その配下の専用フォルダは対象外
    （過検知でユーザーの正当な作業フォルダまで拒否しない）。"""
    profile = tmp_path / "Users" / "someone"
    monkeypatch.setenv("USERPROFILE", str(profile))
    assert is_unsafe_workdir_root(str(profile / "chouhyo_work")) is None
    assert is_unsafe_workdir_root(str(profile / "Documents" / "chouhyo")) is None


# ========== cli.cmd_purge --preview ==========

def _cfg_file(tmp_path, workdir, output_dir=None, **extra):
    cfg = tmp_path / "config.json"
    data = {"workdir": str(workdir),
            "output_dir": str(output_dir or (tmp_path / "out")),
            "log_dir": str(tmp_path / "logs")}
    data.update(extra)
    cfg.write_text(json.dumps(data), encoding="utf-8")
    return cfg


def _run(cfg, *args):
    return cli.main(["--config", str(cfg), "purge", *args])


def _events(capsys):
    out = capsys.readouterr().out
    return [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_preview_reports_counts_and_absolute_paths(tmp_path, capsys):
    """--preview は削除せず、tool_items／other_items／safe_root を出す。"""
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    (wd / "pages").mkdir()
    (wd / "note.txt").write_text("原本ではないが未知のファイル", encoding="utf-8")
    cred = wd / cred_store.blob_name()
    cred.write_bytes(b"dummy")

    cfg = _cfg_file(tmp_path, wd)
    assert _run(cfg, "--preview") == 0

    events = _events(capsys)
    ev = next(e for e in events if e["event"] == "purge_preview")
    assert ev["tool_items"] == 2          # intermediate.sqlite + pages/
    assert ev["other_items"] == 1         # note.txt（cred.dpapi はどちらにも数えない）
    assert ev["other_examples"] == ["note.txt"]
    assert ev["safe_root"] is True
    assert ev["unsafe_reason"] is None
    # wd はもともと絶対パス（tmp_path 由来）なので変換前後で一致する
    assert ev["path"] == str(wd)
    assert ev["output_dir"] == str(tmp_path / "out")

    # 何も消えていない
    assert (wd / "intermediate.sqlite").exists()
    assert (wd / "pages").exists()
    assert (wd / "note.txt").exists()
    assert cred.exists()


def test_preview_wins_over_yes(tmp_path, capsys):
    """--preview と --yes の同時指定は --preview が優先され、削除しない。"""
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    cfg = _cfg_file(tmp_path, wd)

    assert _run(cfg, "--preview", "--yes") == 0
    assert (wd / "intermediate.sqlite").exists()

    events = _events(capsys)
    assert any(e["event"] == "purge_preview" for e in events)
    assert not any(e["event"] == "purged" for e in events)


def test_preview_on_missing_workdir_is_zero(tmp_path, capsys):
    """workdir が無くても --preview は例外にならず 0/0 を返す。"""
    wd = tmp_path / "wd"  # 作らない
    cfg = _cfg_file(tmp_path, wd)
    assert _run(cfg, "--preview") == 0
    ev = next(e for e in _events(capsys) if e["event"] == "purge_preview")
    assert (ev["tool_items"], ev["other_items"]) == (0, 0)
    assert ev["safe_root"] is True


# ========== cli.cmd_purge --yes: other_items 拒否 ==========

def test_yes_refuses_when_other_items_present(tmp_path, capsys):
    """workdir 直下に未知のファイル（原本想定）が1件でもあれば削除しない。"""
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    original = wd / "大事な原本.pdf"
    original.write_text("original", encoding="utf-8")
    cfg = _cfg_file(tmp_path, wd)

    assert _run(cfg, "--yes") == 2

    # 何も消えない（原本はもちろん、中間データも）
    assert (wd / "intermediate.sqlite").exists()
    assert original.exists()

    ev = next(e for e in _events(capsys) if e["event"] == "purge_refused")
    assert ev["reason"] == "other_items"
    assert ev["other_items"] == 1
    assert ev["other_examples"] == ["大事な原本.pdf"]


def test_yes_proceeds_when_only_tool_items_present(tmp_path, capsys):
    """従来の正常削除: workdir 直下がすべて既知の中間データなら削除する。"""
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    (wd / "pages").mkdir()
    (wd / "pages" / "0001.png").write_text("x", encoding="utf-8")
    cfg = _cfg_file(tmp_path, wd)

    assert _run(cfg, "--yes") == 0
    assert not (wd / "intermediate.sqlite").exists()
    assert not (wd / "pages").exists()

    ev = next(e for e in _events(capsys) if e["event"] == "purged")
    assert ev["removed"] == 2
    # path は絶対パスで出る（issue #108）
    from pathlib import Path
    assert Path(ev["path"]).is_absolute()


# ========== cli.cmd_purge --yes: unsafe_root 拒否 ==========

def test_yes_refuses_dot_workdir(tmp_path, capsys):
    """workdir=\".\" は削除せず拒否する（起票の再現ケース）。"""
    cfg = _cfg_file(tmp_path, ".")
    assert _run(cfg, "--yes") == 2
    ev = next(e for e in _events(capsys) if e["event"] == "purge_refused")
    assert ev["reason"] == "unsafe_root" and ev["unsafe_reason"] == "dot"


def test_yes_refuses_drive_root_workdir(tmp_path, capsys):
    """workdir=\"C:\\\" は削除せず拒否する（起票の再現ケース）。"""
    cfg = _cfg_file(tmp_path, "C:\\")
    assert _run(cfg, "--yes") == 2
    ev = next(e for e in _events(capsys) if e["event"] == "purge_refused")
    assert ev["reason"] == "unsafe_root" and ev["unsafe_reason"] == "drive_root"


def test_yes_refuses_unc_workdir(tmp_path, capsys):
    """UNC パスは（到達可否に関わらず）文字列判定だけで拒否する
    （ファイルシステムへ実アクセスしないことがこのテストの安全性の前提）。
    """
    cfg = _cfg_file(tmp_path, "\\\\nonexistent-share-xyz\\folder")
    assert _run(cfg, "--yes") == 2
    ev = next(e for e in _events(capsys) if e["event"] == "purge_refused")
    assert ev["reason"] == "unsafe_root" and ev["unsafe_reason"] == "unc"


def test_yes_refuses_profile_root_workdir(tmp_path, capsys, monkeypatch):
    """workdir が %USERPROFILE% そのものだと拒否する（起票の同居シナリオ）。"""
    profile = tmp_path / "Users" / "someone"
    profile.mkdir(parents=True)
    monkeypatch.setenv("USERPROFILE", str(profile))
    cfg = _cfg_file(tmp_path, profile)
    assert _run(cfg, "--yes") == 2
    ev = next(e for e in _events(capsys) if e["event"] == "purge_refused")
    assert ev["reason"] == "unsafe_root" and ev["unsafe_reason"] == "profile_root"
