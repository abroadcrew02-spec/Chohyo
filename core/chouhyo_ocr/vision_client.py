"""Vision 送信（設計 §6.3）。

- DOCUMENT_TEXT_DETECTION（M0-S2 実測で確定）・言語ヒント ja
- クライアント内蔵リトライは無効化し、自前の指数バックオフに一本化（§12-C5）
- 開発・テスト用に ReplayClient（保存済み応答の再生・課金ゼロ）を持つ。
  応答は毎回 workdir/responses/ へ保存し、以後の remap/render を再送信なしで賄う
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol

from . import logging_safe as log


class OcrClient(Protocol):
    def annotate(self, image_png: bytes, page_id: str) -> dict:
        """MessageToDict 形式（fullTextAnnotation を含む dict）を返す。"""
        ...


class SendError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RealVisionClient:
    """実 API。認証は GOOGLE_APPLICATION_CREDENTIALS または credentials_info。"""

    MAX_ATTEMPTS = 5
    BACKOFF_INITIAL = 1.0

    def __init__(self, credentials_info: dict | None = None,
                 monthly_cap: int | None = None):
        from google.cloud import vision
        from google.protobuf.json_format import MessageToDict
        self._vision = vision
        self._to_dict = MessageToDict
        from .api_budget import DEFAULT_CAP
        self.monthly_cap = DEFAULT_CAP if monthly_cap is None else monthly_cap
        if credentials_info is not None:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_info(credentials_info)
            self.client = vision.ImageAnnotatorClient(credentials=creds)
        else:
            self.client = vision.ImageAnnotatorClient()

    def annotate(self, image_png: bytes, page_id: str) -> dict:
        from .api_budget import check_and_count
        image = self._vision.Image(content=image_png)
        ctx = self._vision.ImageContext(language_hints=["ja"])
        delay = self.BACKOFF_INITIAL
        last_code = "SEND_FAILED"
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            # 送信の直前で月次上限を確認する（ユーザー指示 2026-08-28: 請求が
            # 立つ前に強制停止）。**上限に達していればここで止まり、1バイトも
            # 送らない**。数えてから送るのは、送信後に記録すると異常終了で
            # 取りこぼすため（多めに数えるほうが課金より安全）。
            # 再試行も1回ごとに数える（レビュー M-4）: resp.error.message 系の
            # 失敗は _NON_RETRYABLE に該当せず最大 MAX_ATTEMPTS 回投げるので、
            # ループの外で1回だけ数えると上限を最大5倍まで超えうる。
            # ※どの失敗応答が実際に課金されるかは GCP 側の仕様で、こちらから
            # 確認できない（**未検証**）。歯止めとしては多めに数える側へ倒す。
            # 上限に当たったらその場でリトライを打ち切る（例外が伝播する）
            used = check_and_count(1, self.monthly_cap)
            log.info("api_units_used", count=used)
            try:
                # retry=None で内蔵リトライを止め、自前バックオフに一本化する
                resp = self.client.document_text_detection(
                    image=image, image_context=ctx, retry=None, timeout=120)
            except Exception as e:
                # 再試行しても結果が変わらないエラー（認証・権限・引数不正）は
                # 即座に諦める（レビュー M-8: 旧実装は 1+2+4+8=15 秒を無駄にし、
                # 例外種別も TRANSPORT へ潰して原因が追えなかった）。
                # 型名は記入値を含まないのでログへ出してよい（cli.py と同じ方針）
                code = type(e).__name__
                fatal = code in _NON_RETRYABLE
                log.error("vision_transport_error", page_id=page_id, attempt=attempt,
                          error_code=code)
                last_code = "SEND_TRANSPORT"
                if fatal:
                    raise SendError(f"SEND_{code}") from None
            else:
                if resp.error.message:
                    log.error("vision_api_error", page_id=page_id, attempt=attempt,
                              error_code=resp.error.code)
                    last_code = "SEND_API_ERROR"
                else:
                    return self._to_dict(resp._pb)
            if attempt < self.MAX_ATTEMPTS:
                time.sleep(delay)
                delay *= 2
        raise SendError(last_code)


class ReplayClient:
    """保存済み応答の再生。page_id → JSON ファイル。無ければ SendError。"""

    def __init__(self, response_dir: str | Path):
        self.dir = Path(response_dir)

    def annotate(self, image_png: bytes, page_id: str) -> dict:
        p = self.dir / f"{page_id}.json"
        if not p.exists():
            raise SendError("REPLAY_MISSING")
        return json.loads(p.read_text(encoding="utf-8"))


def load_saved_response(workdir: str | Path, page_id: str) -> dict | None:
    """保存済み応答を読む（issue #38: 受信済みページを再送しないため）。

    壊れている・読めない場合は None を返して再送へ倒す（黙って古い内容を
    使うより、送り直すほうが安全）。
    """
    p = Path(workdir) / "responses" / f"{page_id}.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# 再試行しても変わらないエラー（google.api_core の例外名）。
# 名前で判定するのは、この層が google ライブラリの import に依存しないため
_NON_RETRYABLE = frozenset([
    "Unauthenticated", "PermissionDenied", "InvalidArgument",
    "NotFound", "FailedPrecondition",
])


def save_response(workdir: str | Path, page_id: str, resp: dict) -> Path:
    d = Path(workdir) / "responses"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{page_id}.json"
    p.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    return p
