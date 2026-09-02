"""S-MB: 環境変数の平文鍵を verify が警告する（レビュー7巡目 #69）。

`GOOGLE_APPLICATION_CREDENTIALS` が指すのは平文のサービスアカウント JSON で、
DPAPI 取り込み（import-credentials）と違い保護されていない。旧実装は
credentials_state() の3値だけを見ており、(a) env 単独は ok:true の緑判定、
(b) dpapi と env が両方ある環境では state が "dpapi" に畳まれて平文鍵の残置が
verify から見えなかった。実行可否（ok）は変えずに、警告として見せる。
"""
import json

from chouhyo_ocr import cli, cred_store

ENV_VAR = "GOOGLE_APPLICATION_CREDENTIALS"


def _cfg(tmp_path):
    p = tmp_path / "config.json"
    p.write_text(json.dumps({"output_dir": str(tmp_path / "out"),
                             "workdir": str(tmp_path / "wd"),
                             "log_dir": str(tmp_path / "logs")}),
                 encoding="utf-8")
    return p


def _credentials_event(tmp_path, capsys):
    """verify の credentials イベントだけを取り出す。

    poppler・テンプレート等ほかのチェックの成否は環境依存なので、
    overall の終了コードではなくこのイベント自身を見る（TR-G6・
    test_review_fixes._verify_template_event と同じ流儀）。
    """
    cli.main(["--config", str(_cfg(tmp_path)), "verify"])
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()
              if line.startswith("{")]
    return next(e for e in events if e.get("check") == "credentials")


def _put_dpapi_blob(tmp_path):
    wd = tmp_path / "wd"
    wd.mkdir(parents=True, exist_ok=True)
    # 復号はしない（credentials_state は存在だけを見る）ためダミーで足りる
    (wd / cred_store._BLOB_NAME).write_bytes(b"dummy")


def test_env_credentials_present_reads_env_only(monkeypatch, tmp_path):
    """新設の述語は環境変数の有無だけを見る（値・パスは返さない）。"""
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert cred_store.env_credentials_present() is False
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "key.json"))
    assert cred_store.env_credentials_present() is True
    monkeypatch.setenv(ENV_VAR, "")          # 空文字は「未設定」と同じ扱い
    assert cred_store.env_credentials_present() is False


def test_env_only_warns_but_stays_ok(monkeypatch, tmp_path, capsys):
    """env 単独: 実行はできる（ok は変えない）が警告を付ける。"""
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "key.json"))
    ev = _credentials_event(tmp_path, capsys)
    assert ev["state"] == "env"          # 3値契約は変えない
    assert ev["ok"] is True
    assert ev["env_present"] is True
    assert ev["warn"] is True and ev["reason"] == "env_plaintext"


def test_dpapi_with_env_still_reports_plaintext(monkeypatch, tmp_path, capsys):
    """dpapi と env が両方ある: state は dpapi のまま、env の残置は別キーで見える。"""
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "key.json"))
    _put_dpapi_blob(tmp_path)
    ev = _credentials_event(tmp_path, capsys)
    assert ev["state"] == "dpapi"
    assert ev["env_present"] is True
    assert ev["warn"] is True and ev["reason"] == "env_plaintext"


def test_dpapi_only_has_no_warning(monkeypatch, tmp_path, capsys):
    """DPAPI 取り込み済みだけなら警告は出ない（正常運用の形）。"""
    monkeypatch.delenv(ENV_VAR, raising=False)
    _put_dpapi_blob(tmp_path)
    ev = _credentials_event(tmp_path, capsys)
    assert ev["state"] == "dpapi" and ev["ok"] is True
    assert ev["env_present"] is False
    assert "warn" not in ev and "reason" not in ev


def test_missing_credentials_has_no_warning(monkeypatch, tmp_path, capsys):
    """どちらも無い: 従来どおり ok:false・missing（警告は付けない）。"""
    monkeypatch.delenv(ENV_VAR, raising=False)
    ev = _credentials_event(tmp_path, capsys)
    assert ev["state"] == "missing" and ev["ok"] is False
    assert ev["env_present"] is False
    assert "warn" not in ev
