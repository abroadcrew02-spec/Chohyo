"""issue #120: テスト専用の環境変数（`CHOUHYO_USAGE_DIR_FOR_TESTS`・
`CHOUHYO_CRED_DIR_FOR_TESTS`）が本番バイナリでも無条件に効いてしまう件。

`CHOUHYO_TEST_MODE=1` が明示に設定されていない限り、この2つの環境変数は
無視される（`api_budget.usage_path()`／`cred_store.store_dir()` は代わりに
`%LOCALAPPDATA%` を使う）。テストモードが有効なときは起動ログへ1行残すが、
差し替え先のパスそのものは出さない。

全テストの `conftest._isolate_local_app_state`（autouse）が
`CHOUHYO_TEST_MODE=1` を既定でセットするため、「ゲートが閉じている」側の
挙動を確認するテストだけは明示的に unset/別値へ上書きする。
"""
from pathlib import Path

from chouhyo_ocr import api_budget, cred_store, logging_safe


def test_usage_dir_override_ignored_without_test_mode(tmp_path, monkeypatch):
    """CHOUHYO_TEST_MODE が無い（既定値でない）と、
    CHOUHYO_USAGE_DIR_FOR_TESTS は無視され %LOCALAPPDATA% を使う。"""
    monkeypatch.delenv("CHOUHYO_TEST_MODE", raising=False)
    fake_appdata = tmp_path / "fake_localappdata"
    override_dir = tmp_path / "should_be_ignored"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(override_dir))

    p = api_budget.usage_path()
    assert str(fake_appdata) in str(p)
    assert str(override_dir) not in str(p)


def test_usage_dir_override_applied_with_test_mode(tmp_path, monkeypatch):
    """CHOUHYO_TEST_MODE=1 が設定されていれば従来どおり差し替えが効く。"""
    monkeypatch.setenv("CHOUHYO_TEST_MODE", "1")
    fake_appdata = tmp_path / "fake_localappdata"
    override_dir = tmp_path / "override"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(override_dir))

    p = api_budget.usage_path()
    assert str(override_dir) in str(p)


def test_usage_dir_override_rejects_non_1_value(tmp_path, monkeypatch):
    """`CHOUHYO_TEST_MODE` は文字列 \"1\" 以外（\"true\" 等）ではゲートを開けない
    ——契約をあいまいにしない（fail-closed）。"""
    monkeypatch.setenv("CHOUHYO_TEST_MODE", "true")
    fake_appdata = tmp_path / "fake_localappdata"
    override_dir = tmp_path / "should_be_ignored"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(override_dir))

    p = api_budget.usage_path()
    assert str(fake_appdata) in str(p)
    assert str(override_dir) not in str(p)


def test_usage_dir_override_logs_without_leaking_path(tmp_path, monkeypatch):
    """テストモードが有効なとき、差し替えを使った事実はログに残るが
    差し替え先のパス自体はログに出さない。"""
    monkeypatch.setenv("CHOUHYO_TEST_MODE", "1")
    override_dir = tmp_path / "override_secret_looking_path"
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(override_dir))
    logging_safe.init(str(tmp_path / "logs"))

    api_budget.usage_path()

    log_text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "usage_dir_test_override" in log_text
    assert str(override_dir) not in log_text


def test_cred_dir_override_ignored_without_test_mode(tmp_path, monkeypatch):
    """cred_store 側も同じゲート（issue #120）。"""
    monkeypatch.delenv("CHOUHYO_TEST_MODE", raising=False)
    fake_appdata = tmp_path / "fake_localappdata"
    override_dir = tmp_path / "should_be_ignored"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("CHOUHYO_CRED_DIR_FOR_TESTS", str(override_dir))

    d = cred_store.store_dir()
    assert str(fake_appdata) in str(d)
    assert str(override_dir) not in str(d)


def test_cred_dir_override_applied_with_test_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOUHYO_TEST_MODE", "1")
    fake_appdata = tmp_path / "fake_localappdata"
    override_dir = tmp_path / "override"
    monkeypatch.setenv("LOCALAPPDATA", str(fake_appdata))
    monkeypatch.setenv("CHOUHYO_CRED_DIR_FOR_TESTS", str(override_dir))

    d = cred_store.store_dir()
    assert str(override_dir) in str(d)


def test_cred_dir_override_logs_without_leaking_path(tmp_path, monkeypatch):
    monkeypatch.setenv("CHOUHYO_TEST_MODE", "1")
    override_dir = tmp_path / "override_secret_looking_path"
    monkeypatch.setenv("CHOUHYO_CRED_DIR_FOR_TESTS", str(override_dir))
    logging_safe.init(str(tmp_path / "logs"))

    cred_store.store_dir()

    log_text = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "cred_dir_test_override" in log_text
    assert str(override_dir) not in log_text


def test_usage_path_falls_back_to_home_when_no_localappdata(tmp_path, monkeypatch):
    """両方欠けても例外にならない（既存挙動の維持・回帰防止）。"""
    monkeypatch.delenv("CHOUHYO_TEST_MODE", raising=False)
    monkeypatch.delenv("CHOUHYO_USAGE_DIR_FOR_TESTS", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    p = api_budget.usage_path()
    assert p == tmp_path / ".chouhyo_ocr" / "ChouhyoOCR" / "api_usage.json"
