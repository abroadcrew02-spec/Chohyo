"""テンプレート編集の開き方に関する config.json の2キー（07 v1.6 FR-F52・FR-F55／AC-F84・2026-09-04）。

- `auto_detect_frames_on_open`（bool・既定 True）: 画像・PDF を開いたときに
  枠候補を自動生成するか。`snap_blocks`・`unclear_char_level` と同じく
  `config.json` 直接編集のみ（GUI 設定6項目には出さない）
- `last_applied_template`（str・既定 ""）: 編集タブで「このテンプレートを
  適用する」を選んだときだけ書く記憶。値は ""・"shipped"・"user:<表示名>"
  のみで、**絶対パスは保存しない**（07 §7.3）

どちらも既存の config.json（これらのキーを持たない）で壊れないこと、
GUI 側（`gui/src-tauri/src/lib.rs` の `validate_config_patch`）と core 側で
受理・拒否がズレないことを見る。名前の文字種そのものの検証は Rust 側が
唯一の正で、ここでは形（区分＋非空の名前・パスに見える値の拒否）を見る。

型違いの扱いは2キーで違う。`auto_detect_frames_on_open` は他の真偽値フラグと
同じく `ConfigError`、`last_applied_template` は `last_template` と同じく
**例外を投げず ""（記憶なし）へ倒す**（AC-F60・FR-F29「設定1行で起動不能に
しない」）——記憶の1行が壊れているだけで全コマンドが起動不能になるのを避ける。
"""
import json
import subprocess
import sys

import pytest

from chouhyo_ocr import cli
from chouhyo_ocr.config import Config, ConfigError, load_config, save_config
from chouhyo_ocr.paths import app_root

PYTHON = sys.executable
DEFAULT_TPL = app_root() / "templates" / "chouhyo-v1.json"


def _write(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


# --- auto_detect_frames_on_open ---

def test_auto_detect_default_is_true():
    """既定 ON——候補を出すだけでは既存の枠も読み取り結果も変わらない。"""
    assert Config().auto_detect_frames_on_open is True


def test_auto_detect_missing_key_keeps_default(tmp_path):
    """このキーを持たない既存の config.json でも既定で埋まる。"""
    cfg = load_config(_write(tmp_path, {"unclear_threshold": 0.9}))
    assert cfg.auto_detect_frames_on_open is True
    assert cfg.unclear_threshold == 0.9


def test_auto_detect_explicit_false_loads(tmp_path):
    cfg = load_config(_write(tmp_path, {"auto_detect_frames_on_open": False}))
    assert cfg.auto_detect_frames_on_open is False


@pytest.mark.parametrize("value", ["yes", "true", 1, 0, None, [], {}])
def test_auto_detect_rejects_non_bool(tmp_path, value):
    """0/1 や "yes" は bool ではない（既存 unclear_char_level と同じ方針）。

    truthy で通すと、typo した値が「設定は効いている」顔のまま既定と違う
    動きをする。
    """
    p = _write(tmp_path, {"auto_detect_frames_on_open": value})
    with pytest.raises(ConfigError, match="auto_detect_frames_on_open"):
        load_config(p)


# --- last_applied_template ---

def test_last_applied_default_is_empty():
    """既定は「記憶なし」。実行タブ用の last_template（既定 shipped）とは別。"""
    assert Config().last_applied_template == ""
    assert Config().last_template == "shipped"


def test_last_applied_missing_key_keeps_default(tmp_path):
    cfg = load_config(_write(tmp_path, {"last_template": "user:sample"}))
    assert cfg.last_applied_template == ""
    assert cfg.last_template == "user:sample"


@pytest.mark.parametrize("value", ["", "shipped", "user:sample",
                                   "user:サンプル 帳票", "user:form-B_2"])
def test_last_applied_accepts_allowed_forms(tmp_path, value):
    """Rust の validate_name_shape が通す名前（英数・かな漢字・- _ 空白）は
    すべて core も通す——書き込み側と読み込み側で判定がズレない。"""
    cfg = load_config(_write(tmp_path, {"last_applied_template": value}))
    assert cfg.last_applied_template == value


@pytest.mark.parametrize("value", [
    "user:",                    # 区分だけで名前が無い
    "shipped:sample",           # shipped に名前は付かない
    "sample",                   # 区分が無い
    "C:\\templates\\a.json",    # 絶対パス
    "user:C:\\templates\\a",    # 名前の位置に絶対パス
    "user:..",                  # 親ディレクトリ
    "user:../../etc",
    "user:a/b",                 # パス区切り
    "user:a\\b",
    "user:a:b",                 # ドライブ文字／代替データストリーム
    "user:a\nb",                # 制御文字
    5, True, None, ["user:a"], {"user": "a"},   # 型違い
])
def test_last_applied_invalid_falls_back_without_raising(tmp_path, value):
    """形・型が不正でも例外を投げず ""（記憶なし）へ倒す。

    last_template と同じ扱い（AC-F60・FR-F29「設定1行で起動不能にしない」・
    08 §3.10 不変条件6）。config.json は手編集でも GUI からも書けるので、
    記憶の1行が壊れただけで run／verify／render／remap が全部止まる状態を
    作らない。倒したことは理由コードに残る（例外を投げないこと自体が検証対象）。
    """
    p = _write(tmp_path, {"last_applied_template": value})
    cfg = load_config(p)
    assert cfg.last_applied_template == ""
    assert cfg.last_applied_template_fallback_reason == "invalid_format"


def test_last_applied_valid_value_leaves_no_fallback_reason(tmp_path):
    """理由コードは「今回の読み込みで倒したか」を示す一時情報。手編集で
    紛れ込んでも正規化して事実と揃える（last_template_fallback_reason と同じ）。"""
    cfg = load_config(_write(tmp_path, {
        "last_applied_template": "user:sample",
        "last_applied_template_fallback_reason": "invalid_format"}))
    assert cfg.last_applied_template == "user:sample"
    assert cfg.last_applied_template_fallback_reason == ""


def test_last_applied_fallback_logs_warning_without_value(tmp_path):
    """倒したことは警告に残るが、値そのものはログに出さない（Q-S1・FR-F50）。

    警告を出すのは cli._load_config_and_init_log（log.init の直後）で、
    load_config 単体では出ない——_validate の時点では logging_safe が未初期化
    （M-1）。本番の呼び出し順と揃えるためここも cli 経由で呼ぶ。
    """
    bad_value = "田中様_申込書テンプレート"  # 区分が無く形式不正
    p = _write(tmp_path, {"last_applied_template": bad_value,
                          "log_dir": str(tmp_path / "logs")})
    cfg = cli._load_config_and_init_log(p)
    assert cfg.last_applied_template == ""
    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "config_last_applied_template_fallback" in app_log
    assert bad_value not in app_log


# --- 往復・既存キーとの共存 ---

def test_save_load_roundtrip_keeps_both_keys(tmp_path):
    """save_config → load_config で値が変わらない（GUI が書いた記憶を core が
    そのまま読める）。"""
    p = tmp_path / "config.json"
    cfg = Config(auto_detect_frames_on_open=False,
                 last_applied_template="user:サンプル 帳票")
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded.auto_detect_frames_on_open is False
    assert loaded.last_applied_template == "user:サンプル 帳票"
    assert loaded == cfg


def test_saved_json_contains_both_keys(tmp_path):
    p = tmp_path / "config.json"
    save_config(Config(), p)
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["auto_detect_frames_on_open"] is True
    assert data["last_applied_template"] == ""
    # 1回限りの診断情報は書かない（M-1）——2キーとも設定ではない
    assert "last_template_fallback_reason" not in data
    assert "last_applied_template_fallback_reason" not in data


def test_unknown_key_still_rejected(tmp_path):
    """キーを増やしても未知キー拒否は効いたまま（typo が既定へ黙って落ちない）。"""
    p = _write(tmp_path, {"auto_detect_frames": True})
    with pytest.raises(ConfigError, match="auto_detect_frames"):
        load_config(p)


def test_last_template_stays_lenient(tmp_path):
    """last_applied_template を厳格にしても、last_template の特例（AC-F60・
    不正なら shipped へ倒す）は変わらない。"""
    cfg = load_config(_write(tmp_path, {"last_template": "C:\\x",
                                        "last_applied_template": "shipped"}))
    assert cfg.last_template == "shipped"
    assert cfg.last_template_fallback_reason == "invalid_format"
    assert cfg.last_applied_template == "shipped"


# --- AC-F85: 壊れた last_applied_template でも4コマンドとも起動不能にしない ---
#
# CLI プロセスを subprocess で実際に起動し、config.json 1行の破損が run/
# render/remap/verify のどれも ConfigError で落とさないことを見る
# （07 v1.6 §8.4・FR-F29「設定1行で起動不能にしない」）。Vision API は
# 呼ばない——run は --replay（空ディレクトリ）を使い、--template は既定の
# 出荷テンプレートを明示する（frozen 相当の経路・issue #65-4 と揃える）。

def _write_broken_config(tmp_path, value):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "last_applied_template": value,
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }, ensure_ascii=False), encoding="utf-8")
    return cfg


def _run_cli(cfg_path, *cli_args):
    return subprocess.run(
        [PYTHON, "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg_path), *cli_args],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)


def _assert_started_without_config_error(result, tmp_path):
    """4コマンド共通のアサーション: ConfigError で落ちていない・値を漏らさない。"""
    assert "Traceback" not in result.stderr
    assert "ConfigError" not in result.stderr
    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "config_last_applied_template_fallback error_code=invalid_format" in app_log


@pytest.mark.parametrize("build_args", [
    lambda tmp_path: ["run", "--input", str(_mkdir(tmp_path, "input")),
                       "--replay", str(_mkdir(tmp_path, "responses")),
                       "--template", str(DEFAULT_TPL)],
    lambda tmp_path: ["render", "--template", str(DEFAULT_TPL)],
    lambda tmp_path: ["remap", "--template", str(DEFAULT_TPL)],
    lambda tmp_path: ["verify", "--template", str(DEFAULT_TPL)],
], ids=["run", "render", "remap", "verify"])
def test_last_applied_broken_value_all_commands_start(tmp_path, build_args):
    """代表値 "C:\\x.json"（絶対パス）で run/render/remap/verify の全部が
    ConfigError にならず起動する。終了コードは各コマンド通常の結果でよい
    ——見るのは「設定1行で起動不能にならない」ことだけ。"""
    cfg = _write_broken_config(tmp_path, "C:\\x.json")
    r = _run_cli(cfg, *build_args(tmp_path))
    _assert_started_without_config_error(r, tmp_path)


def _mkdir(tmp_path, name):
    p = tmp_path / name
    p.mkdir(exist_ok=True)
    return p


@pytest.mark.parametrize("value", [5, "user:", "sample"])
def test_last_applied_other_broken_forms_verify_starts(tmp_path, value):
    """代表値以外の壊れ方（数値／区分だけ／区分なし）は verify だけで確認する
    （AC-F84 の型・形の網羅は core 側の test_last_applied_invalid_falls_back_
    without_raising が持つ。ここは「CLI プロセスとして本当に起動不能に
    ならないか」の確認）。"""
    cfg = _write_broken_config(tmp_path, value)
    r = _run_cli(cfg, "verify", "--template", str(DEFAULT_TPL))
    _assert_started_without_config_error(r, tmp_path)
