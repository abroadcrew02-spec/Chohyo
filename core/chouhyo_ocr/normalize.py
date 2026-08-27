"""値の正規化。金額（D-01）と複合セルの機械分割（D-23）。

どちらも推測をしない。判定できないものは None（＝〓）へ倒す（要件 §5.5 の転記主義）。
"""
from __future__ import annotations

import re
import unicodedata

_GROUPED = re.compile(r"\d{1,3}([,.]\d{3})+")
_PLAIN = re.compile(r"\d+")
_SEPARATORS = re.compile(r"[.・/,\-]")  # . ・ / , -（NFKC 後なので半角のみ）


def normalize_amount(raw: str) -> int | None:
    """金額の正規化（D-01 の6手順）。None は〓を意味する。

    1. 前後の空白を除去
    2. 全角→半角（NFKC）
    3. 区切り候補（、／，／空白）を , へ置換
    4. 区切りなしの純数字 → そのまま整数化
    5. ^\\d{1,3}([,.]\\d{3})+$ → 区切り除去して整数化
    6. それ以外 → None（〓）
    """
    s = unicodedata.normalize("NFKC", raw.strip())
    s = re.sub(r"[、\s]+", ",", s)  # 読点・空白 → ,（NFKC で ，は , になっている）
    if not s:
        return None
    if _PLAIN.fullmatch(s):
        return int(s)
    if _GROUPED.fullmatch(s):
        return int(re.sub(r"[,.]", "", s))
    return None


def split_composite(raw: str, n: int) -> list[str] | None:
    """複合セルの機械分割（D-23）。「7.7.20」→ ['7','7','20']。

    ①空白を除去 ②区切り候補（. ・ / , -）で分割 ③分割数が n と一致し
    全要素が非空の場合のみ順に割り当てる（転記のまま・ゼロ埋めしない）
    ④それ以外は None（呼び出し側で全サブ列〓にする）。
    'H20.7.20' は ['H20','7','20'] として成立する——明細の年と同じ転記主義。
    """
    s = unicodedata.normalize("NFKC", raw)
    s = re.sub(r"\s+", "", s)
    if not s:
        return None
    parts = _SEPARATORS.split(s)
    if len(parts) == n and all(parts):
        return parts
    return None
