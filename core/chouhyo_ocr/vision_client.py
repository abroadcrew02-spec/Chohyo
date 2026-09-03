"""Vision 送信（設計 §6.3）。

- DOCUMENT_TEXT_DETECTION（M0-S2 実測で確定）・言語ヒント ja
- クライアント内蔵リトライは無効化し、自前の指数バックオフに一本化（§12-C5）
- 開発・テスト用に ReplayClient（保存済み応答の再生・課金ゼロ）を持つ。
  応答は毎回 workdir/responses/ へ保存し、以後の remap/render を再送信なしで賄う
"""
from __future__ import annotations

import json
import os
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
                # 二段構え（issue #99）: 型名の集合（ライブラリ非依存）に加えて
                # gRPC ステータス番号でも判定する。名前だけだと派生クラス・
                # クラス名変更を取りこぼすため。番号は数値なので、これを見ても
                # この層は google ライブラリの import に依存しない
                fatal = (code in _NON_RETRYABLE
                         or _status_number(getattr(e, "grpc_status_code", None))
                         in _NON_RETRYABLE_STATUS)
                log.error("vision_transport_error", page_id=page_id, attempt=attempt,
                          error_code=code)
                last_code = "SEND_TRANSPORT"
                if fatal:
                    raise SendError(f"SEND_{code}") from None
            else:
                if resp.error.message:
                    status = _status_number(getattr(resp.error, "code", None))
                    log.error("vision_api_error", page_id=page_id, attempt=attempt,
                              error_code=status)
                    last_code = "SEND_API_ERROR"
                    if status in _NON_RETRYABLE_STATUS:
                        # 決定的エラー（認証・権限・引数不正）は再送しても同じ
                        # 結果になる（issue #99）。旧実装は種別を見ずに
                        # MAX_ATTEMPTS 回まで再送し、ページ1枚あたり5ユニットと
                        # 約15秒を確定で失っていた。カウンタ消費は従来どおり
                        # 「試行の直前に1」なので、ここで打ち切れば1ユニットで済む
                        raise SendError(f"SEND_API_{status}") from None
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


def response_path(workdir: str | Path, page_id: str) -> Path:
    return Path(workdir) / "responses" / f"{page_id}.json"


def response_meta_path(workdir: str | Path, page_id: str) -> Path:
    """応答に付随するサイドカー（入力画像ハッシュ・issue #92）。

    応答本体（<page_id>.json）へキーを足すのではなくサイドカーにするのは、
    本体を Vision の応答そのもの（MessageToDict の出力）のままに保つため。
    ReplayClient の再生素材（workdir/s2 等）や remap/render は本体をそのまま
    読むので、独自キーを混ぜると「保存した応答」と「API の応答」が別物に
    なる。サイドカーなら本体は素のまま、無ければ従来どおり扱えばよい。
    """
    return Path(workdir) / "responses" / f"{page_id}.meta.json"


def saved_image_hash(workdir: str | Path, page_id: str) -> str | None:
    """保存済み応答に紐づく入力画像ハッシュ。無い・読めないなら None。"""
    p = response_meta_path(workdir, page_id)
    if not p.exists():
        return None
    try:
        meta = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = meta.get("image_sha256") if isinstance(meta, dict) else None
    return value if isinstance(value, str) and value else None


def load_saved_response(workdir: str | Path, page_id: str,
                        image_sha256: str | None = None) -> dict | None:
    """保存済み応答を読む（issue #38: 受信済みページを再送しないため）。

    壊れている・読めない場合は None を返して再送へ倒す（黙って古い内容を
    使うより、送り直すほうが安全）。

    image_sha256 を渡すと、保存時に記録した入力画像のハッシュと照合し、
    食い違えば None を返す（＝再送・issue #92）。**ハッシュが記録されていない
    保存済み応答（この機能より前に保存されたもの）は従来どおり再利用する**
    ——後方互換を壊すと、既存 workdir の受信済みページが一斉に再送＝再課金に
    なるため。
    """
    p = response_path(workdir, page_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if image_sha256 is not None:
        saved = saved_image_hash(workdir, page_id)
        if saved is not None and saved != image_sha256:
            log.info("response_image_changed", page_id=page_id)
            return None
    return data


# 再試行しても変わらないエラー（google.api_core の例外名）。
# 名前で判定するのは、この層が google ライブラリの import に依存しないため
_NON_RETRYABLE = frozenset([
    "Unauthenticated", "PermissionDenied", "InvalidArgument",
    "NotFound", "FailedPrecondition",
])

# 同じ「再試行しても変わらない」を gRPC ステータス番号で表したもの（issue #99）。
# 応答内エラー（resp.error.code）と例外の grpc_status_code の両方に使う。
# 上の名前集合と1対1（3=InvalidArgument / 5=NotFound / 7=PermissionDenied /
# 9=FailedPrecondition / 16=Unauthenticated）＋ 12=Unimplemented——12 は名前側に
# 対応が無い（google.api_core では MethodNotImplemented）が、応答側でも例外側でも
# 決定的なので番号で拾う。13=Internal・14=Unavailable・4=DeadlineExceeded・
# 8=ResourceExhausted は一時的エラーなので**入れない**（従来どおり再送する）
_NON_RETRYABLE_STATUS = frozenset({3, 5, 7, 9, 12, 16})


def _status_number(status: object) -> int | None:
    """gRPC ステータスを数値へ均す（grpc / google の import はしない・issue #99）。

    受けうる形は3通り: 応答内エラーの resp.error.code（素の int）、
    google.api_core 例外の grpc_status_code（grpc.StatusCode 列挙。value が
    (番号, 説明) のタプルで、番号自体も IntEnum）、None。どれでもなければ
    None を返す＝「判定できない」＝従来どおり再送側に倒す。
    """
    if isinstance(status, bool):
        return None
    if isinstance(status, int):
        return int(status)
    value = getattr(status, "value", None)
    if isinstance(value, tuple) and value and isinstance(value[0], int):
        return int(value[0])
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return None


def _atomic_write_text(path: Path, text: str) -> None:
    """一時ファイル + os.replace（issue #92・api_budget._save と同型）。

    素の write_text は書き込み途中で落ちると壊れた JSON を残し、
    load_saved_response が None を返して**課金済みのページを再送**する。
    tmp 名をプロセス固有にするのは #91 と同じ理由。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def save_response(workdir: str | Path, page_id: str, resp: dict,
                  image_sha256: str | None = None) -> Path:
    """応答を保存する。image_sha256 は送信した画像のハッシュ（issue #92）。

    本体を先に書いてからサイドカーを書く。間で落ちると「本体はあるが
    ハッシュが無い」状態になるが、それは後方互換の経路（ハッシュ無し＝
    従来どおり再利用）と同じで、再送＝再課金にはならない。逆順にすると
    「ハッシュはあるが本体が無い」状態が残る。
    """
    d = Path(workdir) / "responses"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{page_id}.json"
    _atomic_write_text(p, json.dumps(resp, ensure_ascii=False))
    meta = response_meta_path(workdir, page_id)
    if image_sha256:
        _atomic_write_text(meta, json.dumps({"image_sha256": image_sha256},
                                            ensure_ascii=False))
    elif meta.exists():
        # ハッシュ無しで上書きしたのに古いサイドカーが残ると、次の再利用で
        # 「別の画像の応答」と誤判定して不要な再送になる
        try:
            meta.unlink()
        except OSError:
            pass
    return p
