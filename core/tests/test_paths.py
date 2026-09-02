"""`paths.user_templates_dir()` のテスト（issue #72 (t)・08 §3.1）。

Rust 側が唯一の決定者（`app_data_dir()/templates_user/`）で、環境変数
`CHOUHYO_USER_DIR` 経由で core へ渡す。ここは**受け取った値を検証するだけ**
——列挙・reparse point 検査は Rust の1箇所に集約する契約（08 §3.10 不変条件3）。
"""
import sys

import pytest

from chouhyo_ocr.config import ConfigError
from chouhyo_ocr.paths import project_root, user_templates_dir


def test_no_env_var_falls_back_to_project_root_when_not_frozen(monkeypatch):
    """CHOUHYO_USER_DIR 未設定・frozen でない（開発・CLI 単体運用）なら
    project_root()/templates_user へ倒れる。
    """
    monkeypatch.delenv("CHOUHYO_USER_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert user_templates_dir() == project_root() / "templates_user"


def test_no_env_var_raises_config_error_when_frozen(monkeypatch):
    """M-6（2026-09-02 AZKi 指摘）: frozen（配布・GUI 起動）なのに
    CHOUHYO_USER_DIR が未設定なら、黙って project_root()/templates_user へ
    倒れず ConfigError で明示的に失敗する。

    frozen 環境は本来 Rust が必ずこの環境変数を設定して core を起動する
    ため、未設定は「Rust 側で user_templates_dir(app) の解決に失敗したのに
    環境変数を付けずに core を起動してしまった」異常事態のサイン。ここで
    黙ってフォールバックすると、docstring が防ごうとしている「利用者は
    templates_user に保存したつもりが、実際には別の場所（frozen なら exe の
    親ディレクトリ相当）に保存されていた」事故がまさに発生する。
    """
    monkeypatch.delenv("CHOUHYO_USER_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(ConfigError, match="CHOUHYO_USER_DIR"):
        user_templates_dir()


def test_valid_env_var_is_used_even_when_frozen(monkeypatch, tmp_path):
    """frozen でも CHOUHYO_USER_DIR が正しく設定されていれば、その値を
    そのまま使う（frozen 判定は「未設定のときのフォールバック可否」だけに
    効き、環境変数が渡っている通常の配布経路の挙動は変えない）。
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(tmp_path))
    assert user_templates_dir() == tmp_path


def test_empty_env_var_falls_back_to_project_root(monkeypatch):
    """空文字列は「未設定」と同じ扱い（os.environ.get の慣例に合わせる）。"""
    monkeypatch.setenv("CHOUHYO_USER_DIR", "")
    assert user_templates_dir() == project_root() / "templates_user"


def test_valid_absolute_dir_env_var_is_used(monkeypatch, tmp_path):
    """絶対パス・実在するディレクトリなら、その値をそのまま使う。"""
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(tmp_path))
    assert user_templates_dir() == tmp_path


def test_relative_path_env_var_raises_config_error(monkeypatch):
    """相対パスは拒否する——不正なら ConfigError 相当で明示的に失敗する
    （2026-09-02 Orchestrator 判断。FR-F29/last_template の「設定1つで
    起動不能にしない」フォールバック方針とは意図的に違う——Rust が渡した
    はずの実行環境値そのものの整合性が崩れているサインのため）。
    """
    monkeypatch.setenv("CHOUHYO_USER_DIR", "relative\\path")
    with pytest.raises(ConfigError, match="絶対パス"):
        user_templates_dir()


def test_nonexistent_dir_env_var_raises_config_error(monkeypatch, tmp_path):
    """絶対パスだが実在しないディレクトリは拒否する。"""
    missing = tmp_path / "does_not_exist"
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(missing))
    with pytest.raises(ConfigError, match="実在するディレクトリ"):
        user_templates_dir()


def test_file_not_directory_env_var_raises_config_error(monkeypatch, tmp_path):
    """絶対パスで実在するが、ディレクトリではなくファイルの場合も拒否する。"""
    f = tmp_path / "not_a_dir.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(f))
    with pytest.raises(ConfigError, match="実在するディレクトリ"):
        user_templates_dir()


def test_symlink_dir_env_var_raises_config_error(monkeypatch, tmp_path):
    """reparse point（symlink・ジャンクション）は拒否する（AC-F59 と同じ流儀）。

    シンボリックリンク作成には Windows で管理者権限または開発者モードが
    要ることがある——作れない環境では意味のある検証にならないため skip する。
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(real_dir, target_is_directory=True)
    except OSError:
        pytest.skip("この環境ではシンボリックリンクを作成できない（権限不足）")
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(link))
    with pytest.raises(ConfigError, match="reparse point"):
        user_templates_dir()


def test_junction_dir_env_var_raises_config_error(monkeypatch, tmp_path):
    """M-2（2026-09-02 マリン指摘）: Windows のジャンクション（`mklink /J`）は
    `Path.is_symlink()` だけでは検出できない場合がある——`is_symlink()` が
    見ているのは `IO_REPARSE_TAG_SYMLINK` のみで、ジャンクションが使う
    `IO_REPARSE_TAG_MOUNT_POINT` を見ない。`os.path.isjunction()`
    （Python 3.13+）と併用することで検出する。

    ジャンクション作成はシンボリックリンクと異なり管理者権限・開発者モードを
    要らないため（`test_symlink_dir_env_var_raises_config_error` と異なり）
    skip しない。
    """
    import subprocess

    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link = tmp_path / "link"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real_dir)],
        capture_output=True, text=True)
    assert result.returncode == 0, f"mklink /J に失敗: {result.stderr}"
    assert link.is_dir()

    monkeypatch.setenv("CHOUHYO_USER_DIR", str(link))
    with pytest.raises(ConfigError, match="reparse point"):
        user_templates_dir()


def test_dotdot_in_env_var_is_normalized(monkeypatch, tmp_path):
    """LOW（M-2 と同時・2026-09-02 マリン指摘）: `..` を含むパスは
    `Path.resolve()` で正規化してから返す——生の文字列のまま返すと、
    受け取った側が素朴な文字列比較で範囲を判定した場合に `..` で
    範囲外へ抜けられる余地が残る。
    """
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    dotdot_path = sibling / ".." / "real"
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(dotdot_path))
    result = user_templates_dir()
    assert result == real_dir.resolve()
    assert ".." not in result.parts
