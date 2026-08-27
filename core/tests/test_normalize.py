"""normalize.py: 金額（D-01）と複合セル分割（D-23）のテスト。

要件 §5.5・サンプル実測の表記揺れ（10,000 / 10.000 / 100 / 1000 /
100.000 / /0.000）を正とする。
"""
import pytest

from chouhyo_ocr.normalize import normalize_amount, split_composite

# ---- 金額 D-01 ----

@pytest.mark.parametrize("raw,expected", [
    ("10,000", 10000),        # 通常の桁区切り
    ("10.000", 10000),        # ピリオド区切り（サンプル実在）
    ("100,000", 100000),
    ("100.000", 100000),
    ("100", 100),             # 手順4: 区切りなし純数字（サンプル実在・〓にしない）
    ("1000", 1000),
    ("１０，０００", 10000),   # 全角 → NFKC
    ("10、000", 10000),       # 読点区切り
    ("10 000", 10000),        # 空白区切り
    (" 10000 ", 10000),       # 前後空白
])
def test_amount_ok(raw, expected):
    assert normalize_amount(raw) == expected


@pytest.mark.parametrize("raw", [
    "1,00",        # 不正な区切り
    "10,00,0",
    "10000円",     # 単位付き
    "一万",        # 非数値
    "10.5",        # 3桁グループでない小数
    "/0.000",      # サンプル実在の誤読（1→/）。推測補正せず〓
    "",
    "〓",
])
def test_amount_ng(raw):
    assert normalize_amount(raw) is None


# ---- 複合セル分割 D-23 ----

@pytest.mark.parametrize("raw,expected", [
    ("7.7.20", ["7", "7", "20"]),
    ("1.3.31", ["1", "3", "31"]),
    ("3,8,30", ["3", "8", "30"]),       # サンプル実在（カンマ書き）
    ("H20.7.20", ["H20", "7", "20"]),   # 元号プレフィックスは年へ転記（明細の年と同じ）
    ("7・7・20", ["7", "7", "20"]),
    ("7/7/20", ["7", "7", "20"]),
    ("7 . 7 . 20", ["7", "7", "20"]),   # 空白混じり
])
def test_split_ok(raw, expected):
    assert split_composite(raw, 3) == expected


@pytest.mark.parametrize("raw", [
    "7.720",      # 2分割 → 不一致
    "7.7.20.1",   # 4分割 → 不一致
    "7..20",      # 空要素
    "77 20",      # 区切りなし（空白は除去される）
    "",
])
def test_split_ng(raw):
    assert split_composite(raw, 3) is None
