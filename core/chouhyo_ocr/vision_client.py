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

    def __init__(self, credentials_info: dict | None = None):
        from google.cloud import vision
        from google.protobuf.json_format import MessageToDict
        self._vision = vision
        self._to_dict = MessageToDict
        if credentials_info is not None:
            from google.oauth2 import service_account
            creds = service_account.Credentials.from_service_account_info(credentials_info)
            self.client = vision.ImageAnnotatorClient(credentials=creds)
        else:
            self.client = vision.ImageAnnotatorClient()

    def annotate(self, image_png: bytes, page_id: str) -> dict:
        image = self._vision.Image(content=image_png)
        ctx = self._vision.ImageContext(language_hints=["ja"])
        delay = self.BACKOFF_INITIAL
        last_code = "SEND_FAILED"
        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            try:
                # retry=None で内蔵リトライを止め、自前バックオフに一本化する
                resp = self.client.document_text_detection(
                    image=image, image_context=ctx, retry=None, timeout=120)
            except Exception:
                log.error("vision_transport_error", page_id=page_id, attempt=attempt,
                          error_code="TRANSPORT")
                last_code = "SEND_TRANSPORT"
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


def save_response(workdir: str | Path, page_id: str, resp: dict) -> Path:
    d = Path(workdir) / "responses"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{page_id}.json"
    p.write_text(json.dumps(resp, ensure_ascii=False), encoding="utf-8")
    return p
