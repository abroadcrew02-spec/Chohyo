"""資格情報の保護（設計 §8.2・M0-S3 の実測により DPAPI ファイル暗号化で確定）。

Windows DPAPI（CryptProtectData・CurrentUser スコープ）で暗号化したファイルを
保持し、実行時にメモリ内で復号してクライアントへ渡す。
平文の JSON をアプリ側で保存しない。値をログ・画面へ出さない。

置き場は `%LOCALAPPDATA%\\ChouhyoOCR\\`（issue #52 M-11・api_budget.usage_path()
と同じ思想）。以前は workdir 直下だったが、workdir は中間データの置き場で
purge の対象でもあるため、「要配慮個人情報を消す」という正しい操作が資格情報
まで巻き添えにし、結果として利用者が平文の鍵を手元に保管し続ける動機を生んで
いた。中間データと資格情報のライフサイクルをここで分ける。

**後方互換**: 読み込みは「新しい置き場 → 無ければ従来の workdir 直下」の順で
探す。従来側で見つかったら新しい置き場へ移してから使うので、既存の利用者が
取り込み直す必要はない。

ファイル名が cred_store.py なのは、開発環境のガードレールが
credentials.* パターンへの書き込みを拒否するため（設計 §5 の
credentials.py から改名・機能は同一）。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import json
import os
from pathlib import Path

from . import logging_safe as log
from .config import ConfigError

_BLOB_NAME = "cred.dpapi"
_ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"

# 置き場のベースディレクトリを差し替える**テスト専用**の環境変数（issue #52
# M-11）。本番では設定しない——設定すると資格情報の置き場が丸ごと移動し、
# 「取り込んだはずの鍵が見つからない」状態になる。api_budget の
# CHOUHYO_USAGE_DIR_FOR_TESTS と同じ命名規約で、名前自体に用途を書いておく
# （旧 CHOUHYO_USAGE_DIR は用途が名前から読めず、文書化もされていない
# 「秘密の設定経路」として issue #52 M-6 に挙がった）
_ENV_TEST_DIR = "CHOUHYO_CRED_DIR_FOR_TESTS"

# ランダム上書きの回数（issue #52 M-10・issue #1 の平文鍵削除と同じ手順）
SHRED_PASSES = 3

# DPAPI 失敗時の Windows エラーコードのうち「blob が壊れている」側（issue #53
# L-15）。実測: 壊れた blob を CryptUnprotectData に渡すと 87 が返る
# （2026-08-31）。13 は同じ系統の「データが無効」で、どちらも取り込み直しが
# 正しい対処。これ以外（NTE_BAD_KEY_STATE 系の 0x8009xxxx など）は鍵の状態・
# 実行アカウントの問題で、対処が違う
_ERR_INVALID_DATA = 13        # ERROR_INVALID_DATA
_ERR_INVALID_PARAMETER = 87   # ERROR_INVALID_PARAMETER
_CORRUPT_CODES = frozenset({_ERR_INVALID_DATA, _ERR_INVALID_PARAMETER})

# サービスアカウント JSON に必ずある項目（issue #97）。**キー名だけ**で値は
# 見ない——メッセージへ出すのはこの名前であり、鍵そのものではない
_REQUIRED_FIELDS = ("client_email", "private_key")


class CredentialError(ConfigError):
    """取り込もうとしたファイルがサービスアカウント JSON ではない。

    ConfigError を継承するのは、cli.main の `except ConfigError` 分岐
    （理由をそのまま表示する）へ載せるため——資格情報ファイルも利用者が
    自分で選んだ設定であり、`ERROR ...: 処理を中止しました。詳細は
    error.log を参照。` に潰すと何を選び直せばよいか分からない（レビュー
    N-2 と同じ理由）。**メッセージに鍵の値・ファイルパスは含めない**
    （設計 §8.1）。
    """


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _crypt_error_message(err: int, protect: bool) -> str:
    """DPAPI の失敗を、利用者が次に取る行動が変わる単位で言い分ける（L-15）。

    旧実装はどの失敗でも「別のアカウントで実行していないか確認する」の1種類
    だけを返していた。実測（2026-08-31）では壊れた blob の復号で 87
    （ERROR_INVALID_PARAMETER）が返るのに、この案内が出ていた——アカウントを
    確認しても直らないので、利用者は取り込み直しに辿り着けない。

    `credentials_state()` が返す `broken` と対になる分岐で、あちらが「使えない」
    の一語へ畳むのに対し、こちらは復号を実際に試みた側として理由を出す。
    """
    if protect:
        # 暗号化側の失敗にアカウントの話は無関係（今のアカウントで暗号化する
        # だけなので、別アカウントで作られた blob という状況が存在しない）
        return (f"資格情報の暗号化に失敗した（Windows エラー {err}）。"
                "時間を置いて取り込み直す。繰り返す場合は Windows の"
                "ユーザープロファイルが壊れていないか確認する")
    if err in _CORRUPT_CODES:
        return (f"資格情報の復号に失敗した（Windows エラー {err}）。"
                "暗号化ファイルが壊れている可能性が高い。"
                "import-credentials で取り込み直す")
    return (f"資格情報の復号に失敗した（Windows エラー {err}）。"
            "資格情報は暗号化した Windows アカウントでしか復号できないため、"
            "別のアカウントで実行していないか確認する")


def _crypt(data: bytes, protect: bool) -> bytes:
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _DATA_BLOB()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)):
        # 失敗理由を捨てると「別ユーザーで暗号化したものを復号しようとした」
        # （復号は暗号化した Windows アカウントでしかできない）のか、
        # ファイルが壊れているのかを切り分けられない（レビュー LOW）
        err = kernel32.GetLastError()   # fn の直後に読む（間に他の呼び出しを挟まない）
        raise OSError(_crypt_error_message(err, protect))
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _validate_service_account(info: object) -> dict:
    """サービスアカウント JSON の形を検証する（issue #97）。

    旧実装は `json.loads(raw)` の1行だけで戻り値を捨てていたため、`{}`・
    `[]`・数値でも既存の cred.dpapi を上書きできた。誤った鍵は run の送信段階
    ——帳票を投入して処理を始めた後という最も高コストなタイミング——まで
    発覚しない。

    見るのは「形」だけで、鍵として実際に通るかは見ない。
    `service_account.Credentials.from_service_account_info` の試行はここでは
    **行わない**: この層（と verify）を google ライブラリの import に依存させ
    ないため（vision_client が遅延 import している層分離・_NON_RETRYABLE の
    コメントと同じ方針）。形は正しいが失効・別プロジェクトの鍵は従来どおり
    送信時に判明する。
    """
    if not isinstance(info, dict):
        raise CredentialError(
            "サービスアカウント JSON ではない（中身が JSON オブジェクトでない）。"
            "Google Cloud のサービスアカウント鍵（JSON）を選ぶ")
    if info.get("type") != "service_account":
        raise CredentialError(
            'サービスアカウント JSON ではない（"type" が "service_account" でない）。'
            "Google Cloud のサービスアカウント鍵（JSON）を選ぶ")
    missing = [k for k in _REQUIRED_FIELDS
               if not isinstance(info.get(k), str) or not info[k].strip()]
    if missing:
        # 出すのは項目名だけ（値は出さない）
        raise CredentialError(
            f"サービスアカウント JSON に必要な項目が無い: {', '.join(missing)}")
    return info


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """一時ファイル + os.replace で置き換える（issue #97・api_budget._save と同型）。

    素の write_bytes は書き込み中断で blob を壊し、次回の復号が「別のアカウント
    で実行していないか」という別原因のメッセージに落ちる。tmp 名をプロセス
    固有にするのは #91 と同じ理由（同名 tmp の取り合いを避ける）。
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        # replace まで届かなかった tmp を残さない。後始末自体の失敗は
        # 本題ではないので握りつぶし、元の例外を伝播させる
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def store_dir() -> Path:
    """暗号化資格情報の置き場（issue #52 M-11）。

    `%LOCALAPPDATA%\\ChouhyoOCR\\`。workdir に依存しないのは api_budget の
    カウンタと同じ理由——workdir は purge で消え、作業フォルダを複数使うことも
    あるため、そこへ置くと「中間データを消す」たびに鍵の再取り込みが要る。

    `CHOUHYO_CRED_DIR_FOR_TESTS` は**テスト専用**（本番では設定しない）。
    テストが実行環境の本物の %LOCALAPPDATA% へ blob を書き込まないための
    差し替え口で、運用上の設定項目ではない。
    """
    base = os.environ.get(_ENV_TEST_DIR) or os.environ.get("LOCALAPPDATA")
    if not base:
        base = str(Path.home() / ".chouhyo_ocr")
    return Path(base) / "ChouhyoOCR"


def shred(path: str | Path, passes: int = SHRED_PASSES) -> None:
    """ファイルをランダムなバイト列で上書きしてから削除する（issue #52 M-10）。

    issue #1 で平文の鍵ファイルを始末したときと同じ手順（ランダム3回上書き →
    削除）を、取り込みの標準手順として関数にしたもの。

    **完全消去の保証ではない**（正直に書く）: SSD のウェアレベリング・
    コピーオンライトのファイルシステム・シャドウコピーでは、上書きが元の
    物理ブロックに届かず内容が残りうる。ここで得られるのは「同じパスの
    ファイルを普通に復元しても中身が読めない」までで、それ以上は端末側の
    暗号化（BitLocker 等）の仕事。

    失敗は OSError のまま呼び出し元へ返す——削除できたと誤って報告すると、
    平文の鍵が残っているのに利用者が安心する。
    """
    p = Path(path)
    size = p.stat().st_size
    with open(p, "r+b", buffering=0) as f:
        for _ in range(passes):
            f.seek(0)
            f.write(os.urandom(size))
            f.flush()
            os.fsync(f.fileno())
    p.unlink()


def _legacy_blob(workdir: str | Path) -> Path:
    """旧置き場（workdir 直下）。移行と後方互換の読み込みでのみ使う。"""
    return Path(workdir) / _BLOB_NAME


def _migrate_legacy(legacy: Path, dest: Path) -> Path:
    """旧置き場の blob を新しい置き場へ移す（issue #52 M-11・後方互換）。

    コピーしてから旧側を消す順序にする（先に消すと、コピーが失敗した瞬間に
    資格情報が消滅する）。コピーに失敗したら旧側をそのまま使い続ける——
    移行は利便性の話で、ここで例外を投げると鍵が読めなくなる。
    旧側の削除だけ失敗した場合は新しい側を使い、旧側は残す（次回また移行を
    試みるが、コピー先が既にあるのでこの関数には入らない）。
    """
    if legacy.is_symlink():
        # リンクは辿らない（issue #83 の purge と同じ方針）。実体がどこを
        # 指しているか分からないものを黙って別の場所へ複製しない
        return legacy
    try:
        data = legacy.read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(dest, data)
    except OSError:
        log.error("cred_migrate_failed", path=_BLOB_NAME, state="kept")
        return legacy
    try:
        legacy.unlink()
    except OSError:
        # 新旧が並ぶ。読むのは新しい側なので動作に影響しないが、旧側に
        # 暗号化済みの鍵が残り続けるのでログに残す
        log.warn("cred_migrate_source_kept", path=_BLOB_NAME)
    else:
        log.info("cred_migrated", path=_BLOB_NAME)
    return dest


def _resolve_blob(workdir: str | Path) -> Path | None:
    """読み込むべき blob のパス（新 → 旧の順）。どちらも無ければ None。"""
    dest = store_dir() / _BLOB_NAME
    if dest.exists():
        return dest
    legacy = _legacy_blob(workdir)
    if not legacy.exists():
        return None
    return _migrate_legacy(legacy, dest)


def import_credentials(json_path: str | Path, workdir: str | Path) -> Path:
    """サービスアカウント JSON を DPAPI 暗号化して取り込む。

    保存先は store_dir()（workdir ではない・issue #52 M-11）。workdir は旧置き場
    の後始末にだけ使う——同じ名前の blob が残っていると、purge の keep-list
    （issue #83）が消えない古い鍵を守り続けることになる。
    """
    raw = Path(json_path).read_bytes()
    try:
        info = json.loads(raw)
    except ValueError:
        # パーサのメッセージは位置と構文の説明だけだが、ファイル名も鍵の値も
        # 出さない固定文言に寄せる（このファイルは丸ごと機密）
        raise CredentialError(
            "選んだファイルが JSON として読めない。"
            "Google Cloud のサービスアカウント鍵（JSON）を選ぶ") from None
    _validate_service_account(info)
    out = store_dir() / _BLOB_NAME
    out.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_bytes(out, _crypt(raw, protect=True))
    legacy = _legacy_blob(workdir)
    # 旧置き場の残骸を片付ける。新しい鍵を取り込んだ後なので、ここで消えるのは
    # 常に「今より古い鍵」。失敗しても取り込み自体は成功しているので止めない
    if legacy.exists() and not legacy.is_symlink() and legacy != out:
        try:
            legacy.unlink()
        except OSError:
            log.warn("cred_legacy_kept", path=_BLOB_NAME)
    return out


def load_credentials_info(workdir: str | Path) -> dict | None:
    """暗号化済み資格情報を復号して dict で返す。無ければ None。"""
    p = _resolve_blob(workdir)
    if p is None:
        return None
    return json.loads(_crypt(p.read_bytes(), protect=False))


def env_credentials_present() -> bool:
    """環境変数の平文鍵が設定されているか（設定の有無だけ・値もパスも返さない）。

    credentials_state() は dpapi を優先して1つの状態に畳むため、DPAPI 取り込み
    済みの環境では env 側の平文鍵が state から見えなくなる。「DPAPI があるから
    緑」で平文鍵の残置を見逃す経路を塞ぐため、dpapi と独立した述語として切り出す
    （S-MB）。credentials_state 側の値は変えない（issue #97 で broken を足した
    のは state の内訳であって、この述語の意味ではない）。
    """
    return bool(os.environ.get(_ENV_VAR))


def credentials_state(workdir: str | Path) -> str:
    """verify 用の状態表示（値は含めない）。dpapi / broken / env / missing。

    issue #97: 旧実装はファイルの**存在**しか見なかったため、中身が壊れて
    いても verify は緑になり、誤りは run の送信段階まで発覚しなかった。
    ここで復号と形の検証まで行い、通らなければ `broken` を返す。

    `broken` は3値契約（dpapi/env/missing）に対する**4値目**。呼び出し側
    （cli.verify）は JSON Lines の `state` を3値のまま保ち、`broken` は別キー
    で伝える——GUI（RunScreen.tsx）は `state` を未知の値のまま持ち回り、
    `missing` でなければ実行を許してしまうため。

    置き場が新旧どちらでも同じ値を返す（issue #52 M-11）。状態の語彙は
    変えない——GUI・verify の契約は置き場の移動と無関係。
    """
    blob = _resolve_blob(workdir)
    if blob is not None:
        try:
            _validate_service_account(
                json.loads(_crypt(blob.read_bytes(), protect=False)))
        except Exception:  # noqa: BLE001
            # 復号失敗（OSError・別アカウントで暗号化）／JSON 破損（ValueError）／
            # 形の不一致（CredentialError）をまとめて「使えない」に畳む。
            # 理由の切り分けは復号を試みる run 側のメッセージに任せる
            return "broken"
        return "dpapi"
    if env_credentials_present():
        return "env"
    return "missing"


def blob_name() -> str:
    """暗号化資格情報ファイルの名前（値ではなくファイル名のみ）。

    cli.py の purge（issue #83）が「消してよい中間データ」から資格情報を
    区別するために参照する。値そのもの（"cred.dpapi"）を呼び出し側に
    ハードコードさせず、常にこの1箇所から取る。

    issue #52 M-11 で置き場が store_dir() へ移ったため、purge が守る
    workdir 直下のこの名前は**旧置き場の残り**を指す。新しく取り込んだ
    資格情報はそもそも purge の対象範囲（workdir）に無い。keep-list は
    移行前から使っている利用者のために残す。
    """
    return _BLOB_NAME
