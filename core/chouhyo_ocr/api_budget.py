"""API 送信ユニットの月次上限（強制停止・2026-08-28 ユーザー指示）。

Google Cloud Vision は**月 1,000 ユニットまで無料**（1画像=1ユニット・機能ごと。
公式料金ページで確認・2026-08-28）。超えると Document Text Detection は
1,000 ユニットあたり $1.50 が課金される。

「請求が立つ前に止めたい」という要求に対し、**警告ではなく強制停止**で応える:
上限（既定 900）に達したら送信そのものを行わず例外で止める。警告は読まれない
前提で設計すべきで、課金は後から取り消せないため。

カウントの置き場は workdir の外（%LOCALAPPDATA%）。workdir は purge で消え、
複数の作業フォルダを使うこともあるが、**課金は GCP プロジェクト単位で合算**
されるため、カウンタも同じ粒度で持つ必要がある。

**限界（正直に記す）**: このカウンタが数えるのは「このツールがこの PC から
送った回数」だけ。別の PC・別のツール・GCP コンソールからの利用は数えられない。
正確な実績は GCP の課金ダッシュボードが正本で、これはあくまで暴走の歯止め。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

FREE_TIER_UNITS = 1000   # 公式: 月 1,000 ユニットまで無料
DEFAULT_CAP = 900        # 無料枠に余裕を残した強制停止ライン（ユーザー指示）


class BudgetExceededError(RuntimeError):
    """月次の送信上限に達した。送信は行われていない。"""


def usage_path() -> Path:
    base = os.environ.get("CHOUHYO_USAGE_DIR") or os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / ".chouhyo_ocr")
    return Path(base) / "ChouhyoOCR" / "api_usage.json"


def current_month() -> str:
    return time.strftime("%Y-%m")


def _load() -> dict:
    p = usage_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        # 読めないカウンタは「使用量不明」＝安全側で上限扱いにはせず、
        # 0 から数え直す。壊れたファイルで運用を止めるほうが害が大きい
        return {}


def _save(data: dict) -> None:
    p = usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, p)  # 途中で落ちてもカウンタを壊さない


def used_this_month() -> int:
    return int(_load().get(current_month(), 0))


def remaining(cap: int = DEFAULT_CAP) -> int:
    return max(0, cap - used_this_month())


def check_and_count(units: int = 1, cap: int = DEFAULT_CAP) -> int:
    """送信の**直前**に呼ぶ。上限に達していれば送信せず例外を投げる。

    数えてから送るのは、送信後に記録すると異常終了で取りこぼすため
    （多めに数えるほうが、少なく数えて課金するより安全）。
    戻り値は加算後の当月使用量。
    """
    data = _load()
    month = current_month()
    now = int(data.get(month, 0))
    if now + units > cap:
        raise BudgetExceededError(
            f"API 送信の月次上限に達したため停止した（当月 {now} / 上限 {cap} ユニット・"
            f"無料枠 {FREE_TIER_UNITS}）。**このリクエストは送信していない**。"
            f"続けるには上限を引き上げる（config.json の api_monthly_cap）か、"
            f"翌月まで待つ。実際の使用量は GCP の課金ダッシュボードで確認する")
    data[month] = now + units
    _save(data)
    return data[month]
