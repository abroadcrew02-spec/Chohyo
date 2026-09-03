"""API 送信ユニットの月次上限（強制停止・2026-08-28 ユーザー指示）。

Google Cloud Vision は**月 1,000 ユニットまで無料**（1画像=1ユニット・機能ごと。
公式料金ページで確認・2026-08-28）。超えると Document Text Detection は
1,000 ユニットあたり $1.50 が課金される。

「請求が立つ前に止めたい」という要求に対し、**警告ではなく強制停止**で応える:
上限（既定 900）に達したら送信そのものを行わず例外で止める。警告は読まれない
前提で設計すべきで、課金は後から取り消せないため。

カウントの置き場は workdir の外（%LOCALAPPDATA%）。workdir は purge で消え、
複数の作業フォルダを使うこともあるため、作業フォルダに依存しない場所へ置く。

**粒度は「この PC のこの Windows ユーザー ＋ 年月」**（issue #91）。保存先が
%LOCALAPPDATA% でキーが年月だけなので、GCP プロジェクトを切り替えても同じ
カウンタを消費する（＝プロジェクト別には数えない）。逆に同じプロジェクトを
2台の PC から使えば、それぞれが別々に 0 から数える。

**限界（正直に記す）**: このカウンタが数えるのは「このツールがこの PC から
送った回数」だけ。別の PC・別のツール・GCP コンソールからの利用は数えられない。
正確な実績は GCP の課金ダッシュボードが正本で、これはあくまで暴走の歯止め。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import logging_safe as log
from .pipeline_errors import OperationRefused

FREE_TIER_UNITS = 1000   # 公式: 月 1,000 ユニットまで無料
DEFAULT_CAP = 900        # 無料枠に余裕を残した強制停止ライン（ユーザー指示）

_HINT = ("config.json の api_monthly_cap を引き上げるか、翌月（無料枠がリセット）"
         "まで待つ。実際の使用量は GCP の課金ダッシュボードで確認する")


class BudgetExceededError(OperationRefused):
    """月次の送信上限に達した。送信は行われていない。

    OperationRefused の派生にしているのは、これがバグではなく**業務的な拒否**
    だから（issue #47）。RuntimeError 直系だった頃は cli.main の
    `except OperationRefused` を素通りして最終 `except Exception` に落ち、
    `ERROR BudgetExceededError: 処理を中止しました` ＋ exit 1 になっていた。
    GUI はそれを見て「終了コード 1。再度『読み取りを開始』を押すと続きから
    処理します」と案内するが、上限は押しても下がらないので決定論的に同じ
    結果になる＝**誤案内**だった。refused 契約（exit 0 ＋ JSON Lines）に乗せる。
    """

    def __init__(self, message: str, hint: str = _HINT):
        super().__init__(message, hint)


def usage_path() -> Path:
    """カウンタの場所（%LOCALAPPDATA%\\ChouhyoOCR\\api_usage.json）。

    **`CHOUHYO_USAGE_DIR_FOR_TESTS` はテスト専用**（本番では設定しない・
    issue #52 M-6）。テストが実行環境の本物のカウンタを踏まないための
    差し替え口で、運用上の設定項目ではない。旧名は `CHOUHYO_USAGE_DIR` で、
    用途が名前から読めず文書化も無かったため「文書化されていない秘密の設定
    経路で上限を回避できる」という指摘（M-6 の③）を受けて改名した。名前に
    用途を書いておけば、設定した人にもレビューする人にも意図が見える。

    カウンタの場所は運用では**変えられない**。作業フォルダを複数使っても、
    config.json を差し替えても、同じ Windows ユーザーなら同じファイルを数える。

    **ファイルを消せば 0 に戻る**——これは仕様として残す（M-6 の①）。守ろうと
    しているのは「暴走で気づかないうちに課金される」ことであって、利用者自身が
    意図して上限を外す操作ではない。後者まで防ごうとすると、消せない場所への
    書き込みや改ざん検知が要り、デスクトップツールの実装として釣り合わない
    （M-6 本文も HMAC 等は過剰と結論している）。実際の請求は GCP の課金
    ダッシュボードが正本で、このカウンタは歯止めであって請求書ではない。
    """
    base = (os.environ.get("CHOUHYO_USAGE_DIR_FOR_TESTS")
            or os.environ.get("LOCALAPPDATA"))
    if not base:
        base = str(Path.home() / ".chouhyo_ocr")
    return Path(base) / "ChouhyoOCR" / "api_usage.json"


def current_month() -> str:
    return time.strftime("%Y-%m")


def _quarantine(p: Path) -> None:
    """壊れたカウンタを退避してログへ残す（issue #91）。

    0 から数え直す方針自体は変えない（壊れたファイルで運用を止めるほうが害が
    大きい）が、**無音でリセットしない**。退避しておけば「上限分がまるごと
    再消費された」ことに後から気づける。退避に失敗しても数え直しは続ける
    ——ここで例外を投げると、壊れたカウンタ1つで送信そのものが止まる。
    """
    dest = p.with_name(f"api_usage.broken.{time.strftime('%Y%m%d_%H%M%S')}.json")
    try:
        os.replace(p, dest)
    except OSError:
        # 退避できなかった（ロック中・権限）。次回の _load がまた壊れた
        # ファイルを読んで同じ経路を通る＝ログは出続ける
        log.error("api_usage_corrupt", path=p.name, state="kept")
    else:
        # ファイル名のみ（絶対パスは出さない・設計 §8.1）
        log.error("api_usage_corrupt", path=dest.name, state="quarantined")


def _load() -> dict:
    p = usage_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # 読めないカウンタは「使用量不明」＝安全側で上限扱いにはせず、
        # 0 から数え直す。壊れたファイルで運用を止めるほうが害が大きい。
        # ただし退避＋ログは残す（issue #91: 旧実装はここが完全に無音で、
        # リセットが起きても利用者が気づく手段が無かった）。
        # issue #52 M-6 は逆に「破損時は上限到達扱い（fail-closed）」を提案して
        # いたが、issue #91 で「退避＋ログ＋0 から数え直す」に確定した——
        # 月初にカウンタが1つ壊れただけで月次バッチが丸ごと止まる副作用のほうが
        # 実害が大きく、破損の事実は退避ファイルとログで追える
        _quarantine(p)
        return {}
    if not isinstance(data, dict):
        # JSON としては読めるが形が違う（配列・数値）。破損と同じ扱い
        _quarantine(p)
        return {}
    return data


def _save(data: dict) -> None:
    p = usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # tmp 名はプロセス固有にする（issue #91）。固定名（.json.tmp）だと別
    # workdir の2プロセスが同じ tmp を掴み、(a) 書きかけの内容が os.replace で
    # 正本へ昇格して JSON が壊れる (b) 先に replace した側が tmp を消し、
    # 後続の os.replace が FileNotFoundError を投げて処理全体が異常終了する
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, p)  # 途中で落ちてもカウンタを壊さない
    except BaseException:
        # replace まで届かなかった tmp を残さない。後始末自体の失敗は本題では
        # ないので握りつぶし、元の例外を伝播させる
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


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
    # ⚠️ 読む→加算→書く の間にプロセス間の排他は無い（issue #91・今回は入れ
    # ない方針）。別 workdir の2プロセスが同時にここへ入ると、双方が同じ
    # `now` を読んで 1 ユニットずつ上書きし、1衝突あたり最大1ユニットの
    # 過小記録になる（cap=900・使用量899 で両方が cap 判定を通過し、記録900・
    # 実送信2件）。想定運用（1つの入力フォルダを1人が一括処理）では起きにくい
    # ため、実害が観測されてから msvcrt.locking 等を検討する
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
