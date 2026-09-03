"""資格情報と config の検証・原子的書き込み（issue #97）。

旧実装の穴:
- import_credentials の検証は `json.loads(raw)` の1行だけで戻り値を捨てて
  いた。`{}`・`[]`・数値でも既存の cred.dpapi を上書きでき、誤った鍵は run の
  送信段階まで発覚しなかった
- credentials_state はファイルの**存在**しか見ないため、中身が壊れていても
  verify は緑になった
- cred blob と config.json の書き込みが素の write で、中断すると壊れた
  ファイルが残った（config.json が壊れると全コマンドが起動不能）
- config.json の JSON 構文エラーは cli.main の包括ハンドラへ落ち、
  「詳細は error.log を参照」と案内するのに **log.init 前なので error.log には
  何も書かれない**
"""
import json
import os
import stat

import pytest

from chouhyo_ocr import cli, config as config_mod, cred_store
from chouhyo_ocr.config import Config, ConfigError, load_config, save_config

VALID_SA = {
    "type": "service_account",
    "project_id": "example-project",
    "private_key_id": "0123456789abcdef",
    "private_key": "-----BEGIN PRIVATE KEY-----\nZHVtbXk=\n-----END PRIVATE KEY-----\n",
    "client_email": "svc@example-project.iam.gserviceaccount.com",
    "client_id": "1234567890",
}


def _blob():
    """取り込み先の blob（issue #52 M-11 で workdir 直下から store_dir() へ移動）。

    テストは conftest の `CHOUHYO_CRED_DIR_FOR_TESTS` で隔離済みなので、
    ここが指すのは常に一時ディレクトリ。
    """
    return cred_store.store_dir() / cred_store.blob_name()


def _sa_file(tmp_path, name="sa.json", **override) -> str:
    data = dict(VALID_SA)
    for k, v in override.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return str(p)


# ---------- 取り込みの検証（issue #97 (2)）----------

def test_valid_service_account_is_imported_and_decrypts_back(tmp_path):
    """正しい鍵は取り込め、復号すると元の内容に戻る。"""
    wd = tmp_path / "wd"
    out = cred_store.import_credentials(_sa_file(tmp_path), wd)
    assert out == _blob()
    assert cred_store.load_credentials_info(wd) == VALID_SA
    # 一時ファイルを残さない
    assert [p.name for p in out.parent.glob("*.tmp")] == []


@pytest.mark.parametrize("name,content", [
    ("トップレベルが配列", "[]"),
    ("トップレベルが数値", "123"),
    ("トップレベルが文字列", '"service_account"'),
    ("空オブジェクト", "{}"),
    ("JSON ではない", "-----BEGIN PRIVATE KEY-----"),
])
def test_non_service_account_json_is_rejected(tmp_path, name, content):
    """JSON として読めるだけでは通さない（旧実装は `{}`・`[]` でも通した）。"""
    src = tmp_path / "bad.json"
    src.write_text(content, encoding="utf-8")
    with pytest.raises(cred_store.CredentialError):
        cred_store.import_credentials(src, tmp_path / "wd")
    assert not (tmp_path / "wd" / cred_store.blob_name()).exists(), f"{name}: 取り込まれた"


@pytest.mark.parametrize("override,expect_in_message", [
    ({"type": "authorized_user"}, "service_account"),
    ({"type": None}, "service_account"),
    ({"client_email": None}, "client_email"),
    ({"private_key": None}, "private_key"),
    ({"private_key": "   "}, "private_key"),
    ({"client_email": 12345}, "client_email"),
])
def test_service_account_shape_is_checked(tmp_path, override, expect_in_message):
    """type・client_email・private_key の3点を見る。"""
    with pytest.raises(cred_store.CredentialError) as ei:
        cred_store.import_credentials(_sa_file(tmp_path, **override), tmp_path / "wd")
    assert expect_in_message in str(ei.value)


def test_rejection_message_leaks_neither_key_nor_path(tmp_path):
    """メッセージに鍵の値もファイルパスも出さない（設計 §8.1）。"""
    src = _sa_file(tmp_path, name="秘密の鍵.json", type="authorized_user")
    with pytest.raises(cred_store.CredentialError) as ei:
        cred_store.import_credentials(src, tmp_path / "wd")
    message = str(ei.value)
    assert VALID_SA["private_key"] not in message
    assert VALID_SA["client_email"] not in message
    assert "秘密の鍵" not in message and str(tmp_path) not in message


def test_invalid_import_does_not_overwrite_existing_blob(tmp_path):
    """不正なファイルの取り込みで、既にある正しい鍵を壊さない。"""
    wd = tmp_path / "wd"
    cred_store.import_credentials(_sa_file(tmp_path), wd)
    before = _blob().read_bytes()
    with pytest.raises(cred_store.CredentialError):
        cred_store.import_credentials(_sa_file(tmp_path, name="bad.json", type=None), wd)
    assert _blob().read_bytes() == before
    assert cred_store.load_credentials_info(wd) == VALID_SA


def test_credential_error_is_a_config_error():
    """cli.main の `except ConfigError` 分岐に載る（理由をそのまま表示する）。"""
    assert issubclass(cred_store.CredentialError, ConfigError)


# ---------- 原子的書き込み（issue #97 (3)(5)）----------

def test_failed_blob_write_keeps_previous_blob(tmp_path, monkeypatch):
    """置き換えに失敗しても、前の blob は壊れず一時ファイルも残らない。"""
    wd = tmp_path / "wd"
    cred_store.import_credentials(_sa_file(tmp_path), wd)
    before = _blob().read_bytes()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(cred_store.os, "replace", boom)
    with pytest.raises(OSError):
        cred_store.import_credentials(
            _sa_file(tmp_path, name="other.json", client_id="999"), wd)
    assert _blob().read_bytes() == before
    assert [p.name for p in _blob().parent.glob("*.tmp")] == []


def test_blob_tmp_name_is_process_specific(tmp_path, monkeypatch):
    """一時ファイル名にプロセス ID が入る（同時実行で同じ tmp を取り合わない）。"""
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(cred_store.os, "replace", spy)
    cred_store.import_credentials(_sa_file(tmp_path), tmp_path / "wd")
    assert seen and str(os.getpid()) in seen[0] and seen[0].endswith(".tmp")


def test_save_config_is_atomic(tmp_path, monkeypatch):
    """save_config も tmp + os.replace（config が壊れると全コマンドが起動不能）。"""
    p = tmp_path / "config.json"
    save_config(Config(send_limit=7), p)
    assert load_config(p).send_limit == 7
    assert [q.name for q in tmp_path.glob("*.tmp")] == []

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(config_mod.os, "replace", boom)
    with pytest.raises(OSError):
        save_config(Config(send_limit=9), p)
    assert load_config(p).send_limit == 7, "前の設定が壊れた"
    assert [q.name for q in tmp_path.glob("*.tmp")] == []


# ---------- 壊れた blob の状態表示（issue #97 (3)）----------

def test_credentials_state_reports_broken_blob(tmp_path, monkeypatch):
    """復号できない blob は dpapi でも missing でもなく broken。"""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / cred_store.blob_name()).write_bytes(b"\x00broken blob\x00")
    assert cred_store.credentials_state(wd) == "broken"


def test_credentials_state_reports_broken_for_wrong_json(tmp_path, monkeypatch):
    """復号はできるが中身がサービスアカウント JSON でない場合も broken。

    形の検証を後から厳しくしても、取り込み済みの blob は素通りしない。
    """
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    wd = tmp_path / "wd"
    wd.mkdir()
    # import_credentials を通さず、旧実装が許していた形（{}）を直接暗号化する
    (wd / cred_store.blob_name()).write_bytes(
        cred_store._crypt(b"{}", protect=True))
    assert cred_store.credentials_state(wd) == "broken"


def test_credentials_state_valid_blob_is_dpapi(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    wd = tmp_path / "wd"
    cred_store.import_credentials(_sa_file(tmp_path), wd)
    assert cred_store.credentials_state(wd) == "dpapi"


# ---------- verify での見せ方（3値契約を保ったまま理由を添える）----------

def _cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"output_dir": str(tmp_path / "out"),
                             "workdir": str(tmp_path / "wd"),
                             "log_dir": str(tmp_path / "logs")}),
                 encoding="utf-8")
    return p


def _credentials_event(tmp_path, capsys):
    cli.main(["--config", str(_cfg(tmp_path)), "verify"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    return next(e for e in events if e.get("check") == "credentials")


def test_verify_reports_broken_blob_as_not_ok(tmp_path, monkeypatch, capsys):
    """壊れた鍵は ok:false。state は3値契約のまま（GUI が未知の値で実行を許さない）。"""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    wd = tmp_path / "wd"
    wd.mkdir(parents=True)
    (wd / cred_store.blob_name()).write_bytes(b"\x00broken blob\x00")
    ev = _credentials_event(tmp_path, capsys)
    assert ev["ok"] is False
    assert ev["state"] == "missing", "GUI が知らない state を渡すと実行が止まらない"
    assert ev["cred_error"] == "broken"


def test_verify_broken_blob_with_env_keeps_both_reasons(tmp_path, monkeypatch, capsys):
    """壊れた blob と平文の環境変数が同時にある場合、両方の理由が残る。"""
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(tmp_path / "key.json"))
    wd = tmp_path / "wd"
    wd.mkdir(parents=True)
    (wd / cred_store.blob_name()).write_bytes(b"\x00broken blob\x00")
    ev = _credentials_event(tmp_path, capsys)
    assert ev["cred_error"] == "broken"
    assert ev["reason"] == "env_plaintext" and ev["warn"] is True
    assert ev["env_present"] is True


# ---------- config.json の構文エラー（issue #97 (1) の Python 側）----------

def test_broken_config_json_raises_config_error(tmp_path):
    p = tmp_path / "config.json"
    p.write_text('{"send_limit": 3,,}', encoding="utf-8")
    with pytest.raises(ConfigError) as ei:
        load_config(p)
    assert "config.json" in str(ei.value)


def test_config_top_level_must_be_object(tmp_path):
    p = tmp_path / "config.json"
    p.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_cli_shows_reason_for_broken_config_not_error_log(tmp_path, capsys):
    """「詳細は error.log を参照」に潰れない。

    この失敗は log.init より前に起きるので error.log には何も書かれず、
    旧メッセージは存在しないファイルを案内していた。
    """
    p = tmp_path / "config.json"
    p.write_text('{"send_limit": 3', encoding="utf-8")
    rc = cli.main(["--config", str(p), "verify"])
    err = capsys.readouterr().err
    assert rc == 1
    assert "ERROR ConfigError:" in err
    assert "error.log" not in err
    assert "削除すると既定値で起動する" in err


# ---------- 置き場の分離と後方互換（issue #52 M-11）----------

def _config_file(tmp_path):
    """CLI 用の設定ファイルパス（_cfg と同じ内容を str で返すだけ）。"""
    return str(_cfg(tmp_path))


def test_import_writes_outside_workdir(tmp_path):
    """新規取り込みは store_dir() へ。workdir 直下には作らない。

    workdir は中間データの置き場で purge の対象。ここに鍵を置くと
    「読み取ったデータを消す」たびに鍵の再取り込みが要る（M-11）。
    """
    wd = tmp_path / "wd"
    out = cred_store.import_credentials(_sa_file(tmp_path), wd)
    assert out == _blob()
    assert not (wd / cred_store.blob_name()).exists()
    assert cred_store.credentials_state(wd) == "dpapi"


def test_legacy_blob_is_migrated_on_read(tmp_path):
    """旧置き場（workdir 直下）の blob は読み込み時に新しい置き場へ移る。

    移行後は旧側が消え、新側から復号できる（既存利用者の取り込み直しは不要）。
    """
    wd = tmp_path / "wd"
    wd.mkdir(parents=True)
    legacy = wd / cred_store.blob_name()
    legacy.write_bytes(cred_store._crypt(
        json.dumps(VALID_SA).encode("utf-8"), protect=True))
    assert not _blob().exists()

    assert cred_store.load_credentials_info(wd) == VALID_SA

    assert _blob().exists()
    assert not legacy.exists()
    assert cred_store.credentials_state(wd) == "dpapi"


def test_legacy_blob_stays_when_migration_fails(tmp_path, monkeypatch):
    """移行に失敗したら旧側をそのまま使う（鍵を消さない・読めなくしない）。"""
    wd = tmp_path / "wd"
    wd.mkdir(parents=True)
    legacy = wd / cred_store.blob_name()
    legacy.write_bytes(cred_store._crypt(
        json.dumps(VALID_SA).encode("utf-8"), protect=True))

    def boom(path, data):
        raise OSError("disk full")

    monkeypatch.setattr(cred_store, "_atomic_write_bytes", boom)
    assert cred_store.load_credentials_info(wd) == VALID_SA
    assert legacy.exists()
    assert not _blob().exists()


def test_missing_in_both_places_is_missing(tmp_path, monkeypatch):
    """新旧どちらにも無ければ従来どおり missing（state の語彙は変えない）。"""
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    assert cred_store.credentials_state(tmp_path / "wd") == "missing"
    assert cred_store.load_credentials_info(tmp_path / "wd") is None


def test_import_cleans_up_legacy_blob(tmp_path):
    """取り込み直すと旧置き場の残骸も消える（purge の keep-list が守り続けない）。"""
    wd = tmp_path / "wd"
    wd.mkdir(parents=True)
    legacy = wd / cred_store.blob_name()
    legacy.write_bytes(b"old-and-unreadable")
    cred_store.import_credentials(_sa_file(tmp_path), wd)
    assert not legacy.exists()
    assert cred_store.load_credentials_info(wd) == VALID_SA


# ---------- 元ファイルの削除（issue #52 M-10）----------

def test_delete_source_removes_plaintext_key(tmp_path, capsys):
    """--delete-source: DPAPI 書き込みの成功後に元の平文 JSON が消える。"""
    src = _sa_file(tmp_path)
    rc = cli.main(["--config", _config_file(tmp_path),
                   "import-credentials", src, "--delete-source"])
    out, err = capsys.readouterr()
    assert rc == 0
    assert not os.path.exists(src)
    assert '"credentials_source_deleted"' in out
    assert "削除しました" in err
    assert cred_store.load_credentials_info(tmp_path / "wd") == VALID_SA


def test_without_the_flag_the_message_tells_you_to_delete(tmp_path, capsys):
    """既定では消さないが、文言は「削除して構いません」ではなく削除の指示。"""
    src = _sa_file(tmp_path)
    rc = cli.main(["--config", _config_file(tmp_path), "import-credentials", src])
    err = capsys.readouterr().err
    assert rc == 0 and os.path.exists(src)
    assert "削除してください" in err and "平文" in err


def test_source_is_kept_when_dpapi_write_fails(tmp_path, monkeypatch, capsys):
    """DPAPI 書き込みが失敗したら元ファイルは消さない（順序の保証）。"""
    src = _sa_file(tmp_path)

    def boom(data, protect):
        raise OSError("DPAPI 呼び出しに失敗した（Windows エラー 1）")

    monkeypatch.setattr(cred_store, "_crypt", boom)
    rc = cli.main(["--config", _config_file(tmp_path),
                   "import-credentials", src, "--delete-source"])
    capsys.readouterr()
    assert rc != 0
    assert os.path.exists(src)


def test_delete_failure_is_reported_as_a_warning(tmp_path, capsys):
    """削除できなかったら「完了」で終わらせず警告を出す（exit 0 は保つ）。

    残存パスは出さない（設計 §8.1: 鍵ファイルの所在を画面・ログへ残さない）。
    """
    src = _sa_file(tmp_path)
    os.chmod(src, stat.S_IREAD)
    try:
        rc = cli.main(["--config", _config_file(tmp_path),
                       "import-credentials", src, "--delete-source"])
        out, err = capsys.readouterr()
    finally:
        os.chmod(src, stat.S_IWRITE | stat.S_IREAD)
    assert rc == 0
    assert os.path.exists(src)
    assert '"credentials_source_kept"' in out
    assert "元のファイルが残っています" in err
    assert src not in err


def test_shred_overwrites_before_unlinking(tmp_path, monkeypatch):
    """削除の前にランダム上書きを SHRED_PASSES 回行う（issue #1 と同じ手順）。"""
    p = tmp_path / "secret.json"
    p.write_bytes(b"A" * 64)
    written = []
    real_urandom = os.urandom

    def spy(n):
        written.append(n)
        return real_urandom(n)

    monkeypatch.setattr(cred_store.os, "urandom", spy)
    cred_store.shred(p)
    assert not p.exists()
    assert written == [64] * cred_store.SHRED_PASSES


# ---------- DPAPI 失敗メッセージの切り分け（issue #53 L-15）----------

def test_corrupt_blob_reports_corruption_not_wrong_account(tmp_path):
    """壊れた blob の復号は「取り込み直す」と案内する。

    実測（2026-08-31）では Windows エラー 87（ERROR_INVALID_PARAMETER）が
    返る。旧実装はどの失敗でも「別のアカウントで実行していないか」の1種類
    だけを出しており、アカウントを確認しても直らなかった。
    """
    wd = tmp_path / "wd"
    blob = _blob()
    blob.parent.mkdir(parents=True, exist_ok=True)
    blob.write_bytes(b"not a dpapi blob")
    with pytest.raises(OSError) as ei:
        cred_store.load_credentials_info(wd)
    msg = str(ei.value)
    assert "壊れている" in msg and "取り込み直す" in msg
    assert "別のアカウント" not in msg
    # 状態表示は4値目の broken に畳む（契約は変えない）
    assert cred_store.credentials_state(wd) == "broken"


def test_wrong_account_message_is_kept_for_other_errors():
    """破損以外のコードでは従来どおりアカウントを案内する。"""
    other = cred_store._crypt_error_message(-2146893813, protect=False)
    assert "別のアカウント" in other and "壊れている" not in other
    corrupt = cred_store._crypt_error_message(87, protect=False)
    assert "壊れている" in corrupt
    # 暗号化側の失敗にアカウントの話は無関係
    assert "別のアカウント" not in cred_store._crypt_error_message(87, protect=True)
