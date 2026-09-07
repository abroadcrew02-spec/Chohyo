"""issue #108: purge --preview／--yes の安全確認（応急 (a) → 許可リスト方式
PM決定 (b)・2026-09-07）。

対象:
- `config.is_unsafe_workdir_root`（ドライブ直下・UNC・`.`/`..`・空・
  ユーザープロファイル直下の判定・ファイルシステムに触れない）
- `cli.cmd_purge` の `--preview`（削除しない・件数と絶対パスだけ出す）
- `cli.cmd_purge` の `--yes`:
  - `unsafe_root`（ドライブ直下・UNC・`.`/`..`・ユーザープロファイル直下・
    reparse point）なら削除せず rc=2 で拒否する（これは変更なし）
  - workdir 直下のうち、このツールが作ったと分かるもの（許可リスト・
    `_is_tool_workdir_entry`）**だけ**を削除し、それ以外は種類を問わず
    残す。以前（応急 (a)）は「認識できないものが1件でもあれば purge
    全体を拒否」だったが、PM決定 (b) で「認識できるものだけ消し、
    残りは件数・例を報告する」方式へ変更した。rc は 0（`--include-output`
    の `output_kept` と同じ流儀）

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


# ========== cli.cmd_purge --yes: 許可リスト方式（PM決定 (b)・2026-09-07） ==========

def test_yes_keeps_unrecognized_items_and_still_deletes_known_ones(tmp_path, capsys):
    """workdir 直下に未知のファイル（原本想定）があっても purge 自体は拒否
    しない——認識できる中間データだけ消し、未知のものは残して報告する
    （PM決定 (b)。issue #108 の起票シナリオそのもの: victim/{cred.dpapi,
    mydocs/note.txt, 大事な原本.pdf, intermediate.sqlite}）。
    """
    wd = tmp_path / "victim"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    mydocs = wd / "mydocs"; mydocs.mkdir()
    (mydocs / "note.txt").write_text("x", encoding="utf-8")
    original = wd / "大事な原本.pdf"
    original.write_text("original", encoding="utf-8")
    cred = wd / cred_store.blob_name()
    cred.write_bytes(b"dummy")
    cfg = _cfg_file(tmp_path, wd)

    assert _run(cfg, "--yes") == 0

    # 中間データだけ消え、原本・利用者フォルダ・資格情報は残る
    assert not (wd / "intermediate.sqlite").exists()
    assert original.exists()
    assert mydocs.exists() and (mydocs / "note.txt").exists()
    assert cred.exists()

    ev = next(e for e in _events(capsys) if e["event"] == "purged")
    assert ev["cred_kept"] is True
    assert ev["removed"] == 1 and ev["failed"] == 0
    assert ev["kept"] == 2                      # mydocs + 大事な原本.pdf
    assert sorted(ev["kept_examples"]) == sorted(["mydocs", "大事な原本.pdf"])
    assert "purge_refused" not in {e["event"] for e in _events(capsys)}


def test_yes_proceeds_when_only_tool_items_present(tmp_path, capsys):
    """従来の正常削除: workdir 直下がすべて既知の中間データなら削除する。
    kept は 0 でも常にキーが出る。"""
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
    assert ev["kept"] == 0 and ev["kept_examples"] == []
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


# ========== "logs" フォルダの判定（issue #108 レビュー指摘・2026-09-07） ==========
#
# cfg.log_dir の既定は workdir の兄弟パスであり、既定運用では workdir 直下に
# "logs" は作られない。無条件で名前一致にすると、利用者が workdir 直下へ
# 自分の用途で "logs" フォルダを作った場合に誤って削除してしまう。

def test_logs_kept_when_log_dir_is_sibling_of_workdir(tmp_path, capsys):
    """既定構成（log_dir が workdir の兄弟パス）では、workdir 直下の
    "logs" フォルダはツール由来と判定せず、--yes でも残す。
    """
    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    user_logs = wd / "logs"; user_logs.mkdir()
    (user_logs / "my_notes.log").write_text("x", encoding="utf-8")
    # log_dir は workdir の兄弟（GUI 既定と同じ並び）——workdir 配下ではない
    cfg = _cfg_file(tmp_path, wd, log_dir=str(tmp_path / "logs"))

    assert _run(cfg, "--yes") == 0
    assert not (wd / "intermediate.sqlite").exists()
    assert user_logs.exists() and (user_logs / "my_notes.log").exists()

    ev = next(e for e in _events(capsys) if e["event"] == "purged")
    assert ev["kept"] == 1 and ev["kept_examples"] == ["logs"]


def test_logs_deleted_when_log_dir_is_inside_workdir(tmp_path):
    """cfg.log_dir が実際に <workdir>/logs を指しているときだけ、
    その "logs" フォルダをツール由来として削除する。

    `cli._scan_workdir_entries`/`_purge_workdir` を直接呼ぶ——`cli.main`
    経由（`--yes` 実行）だと、同一プロセス内で `_load_config_and_init_log`
    が `log_dir=<workdir>/logs` に対して `logging_safe.init()` を呼び、
    このテストプロセス自身が app.log/error.log を開いたまま削除を試みる
    ことになり、Windows のファイル共有ロックで `failed` になる
    （purge の判定ロジックとは無関係な、ログ初期化とファイル削除が同一
    プロセス・同一実行内で競合するテスト環境側の制約）。ここでは分類・
    削除の対象決定ロジックだけを直接確認する。
    """
    from chouhyo_ocr.config import Config

    wd = tmp_path / "wd"; wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    tool_logs = wd / "logs"; tool_logs.mkdir()
    (tool_logs / "app.log").write_text("x", encoding="utf-8")
    (tool_logs / "error.log").write_text("x", encoding="utf-8")
    cfg = Config(workdir=str(wd), output_dir=str(tmp_path / "out"),
                log_dir=str(wd / "logs"))

    tool_items, other_items, other_examples = cli._scan_workdir_entries(wd, cfg)
    assert tool_items == 2 and other_items == 0 and other_examples == []

    cred_kept, removed, failed = cli._purge_workdir(wd, cfg)
    # removed はファイル単位（intermediate.sqlite + logs/app.log + logs/error.log）
    assert failed == 0 and removed == 3
    assert not (wd / "intermediate.sqlite").exists()
    assert not tool_logs.exists()          # 中身が空になったのでフォルダごと消える


def test_logs_dir_with_unrecognized_file_is_partially_kept(tmp_path):
    """log_dir が <workdir>/logs でも、拡張子が .log でないファイルは
    直下1階層の判定で残り、フォルダ自体も空にならないので残る。
    """
    from chouhyo_ocr.config import Config

    wd = tmp_path / "wd"; wd.mkdir()
    tool_logs = wd / "logs"; tool_logs.mkdir()
    (tool_logs / "app.log").write_text("x", encoding="utf-8")
    (tool_logs / "memo.txt").write_text("x", encoding="utf-8")
    cfg = Config(workdir=str(wd), output_dir=str(tmp_path / "out"),
                log_dir=str(wd / "logs"))

    tool_items, other_items, other_examples = cli._scan_workdir_entries(wd, cfg)
    assert tool_items == 1 and other_items == 1
    assert other_examples == ["logs/memo.txt"]

    cred_kept, removed, failed = cli._purge_workdir(wd, cfg)
    assert failed == 0 and removed == 1
    assert not (tool_logs / "app.log").exists()
    assert (tool_logs / "memo.txt").exists()
    assert tool_logs.exists()              # 中身が残っているのでフォルダは残る


# ========== USERPROFILE 欠落時の警告（issue #108 レビュー指摘・2026-09-07） ==========

def test_userprofile_missing_logs_warning(tmp_path, monkeypatch):
    """%USERPROFILE% が未設定のとき、判定は False に倒れるが無音にはせず
    app.log へ1行残す（値＝環境変数の中身は無いので出しようがない）。
    """
    from chouhyo_ocr import logging_safe

    monkeypatch.delenv("USERPROFILE", raising=False)
    logging_safe.init(str(tmp_path / "logs"))

    assert is_unsafe_workdir_root(str(tmp_path / "wd")) is None  # 判定自体は通る

    log_text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "userprofile_missing" in log_text
