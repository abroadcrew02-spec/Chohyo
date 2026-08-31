"""config.json の unclear_char_level（U-10・U-14・#62・2026-08-31）。

既定 OFF・GUI 設定画面には出さない（api_monthly_cap と同じ扱い）・
段階導入のため既存 config.json（このキーを持たない）でも壊れないことを見る。
"""
import json

import pytest

from chouhyo_ocr.config import Config, ConfigError, load_config


def test_default_is_false():
    assert Config().unclear_char_level is False


def test_missing_key_loads_as_false(tmp_path):
    """既存の config.json（このキーが無い）を読んでも既定 False で埋まる。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unclear_threshold": 0.9}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.unclear_char_level is False
    assert cfg.unclear_threshold == 0.9


def test_explicit_true_loads(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unclear_char_level": True}), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.unclear_char_level is True


def test_rejects_non_bool(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unclear_char_level": "true"}), encoding="utf-8")
    with pytest.raises(ConfigError, match="unclear_char_level"):
        load_config(p)


def test_rejects_int_as_bool(tmp_path):
    """0/1 は bool ではない——typo混入をtruthyで通さない（既存方針・issue #14 と同趣旨）。"""
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"unclear_char_level": 1}), encoding="utf-8")
    with pytest.raises(ConfigError, match="unclear_char_level"):
        load_config(p)
