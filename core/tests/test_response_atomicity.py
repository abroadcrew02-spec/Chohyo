"""応答保存の原子性と入力画像ハッシュの紐づけ（issue #92）。

旧実装は `write_text` の直接書き込みで、書き込み途中に落ちると壊れた JSON が
残り、次の run が **課金済みのページを再送** していた。また応答には入力画像の
手がかりが無く、保存済み応答がその page_id の最新画像に対応するものかを
後から検証できなかった。
"""
import json
import os

import pytest

from chouhyo_ocr import vision_client
from chouhyo_ocr.vision_client import (load_saved_response, response_meta_path,
                                       response_path, save_response,
                                       saved_image_hash)

RESP = {"fullTextAnnotation": {"text": "dummy"}}
H1 = "a" * 64
H2 = "b" * 64


def test_save_writes_body_and_sidecar_without_leftovers(tmp_path):
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    assert json.loads(response_path(tmp_path, "p_0001").read_text(encoding="utf-8")) == RESP
    assert saved_image_hash(tmp_path, "p_0001") == H1
    assert [p.name for p in (tmp_path / "responses").glob("*.tmp")] == []


def test_body_stays_the_raw_api_response(tmp_path):
    """本体にはハッシュを混ぜない（ReplayClient の再生素材と同じ形を保つ）。

    本体へキーを足すと「保存した応答」と「API の応答」が別物になり、
    testdata/local/s2 の再生素材や remap がそれを前提にできなくなる。
    """
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    body = json.loads(response_path(tmp_path, "p_0001").read_text(encoding="utf-8"))
    assert body == RESP
    assert not any(k.startswith("image") or k.startswith("_") for k in body)


def test_matching_hash_is_reused(tmp_path):
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    assert load_saved_response(tmp_path, "p_0001", image_sha256=H1) == RESP


def test_changed_image_is_not_reused(tmp_path):
    """画像が変わっていたら再利用しない（＝再送する）。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    assert load_saved_response(tmp_path, "p_0001", image_sha256=H2) is None


def test_response_without_hash_is_still_reused(tmp_path):
    """この機能より前に保存された応答（サイドカー無し）は従来どおり再利用する。

    後方互換を壊すと、既存 workdir の受信済みページが一斉に再送＝再課金になる。
    """
    d = tmp_path / "responses"
    d.mkdir()
    (d / "p_0001.json").write_text(json.dumps(RESP), encoding="utf-8")
    assert not response_meta_path(tmp_path, "p_0001").exists()
    assert load_saved_response(tmp_path, "p_0001", image_sha256=H1) == RESP


def test_unreadable_sidecar_falls_back_to_reuse(tmp_path):
    """サイドカーが壊れている場合もハッシュ無しと同じ扱い（再送に倒さない）。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    response_meta_path(tmp_path, "p_0001").write_text("{壊れた", encoding="utf-8")
    assert saved_image_hash(tmp_path, "p_0001") is None
    assert load_saved_response(tmp_path, "p_0001", image_sha256=H2) == RESP


def test_broken_body_is_not_reused(tmp_path):
    """書きかけの壊れた本体は再送へ倒す（サイドカーが一致していても）。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    response_path(tmp_path, "p_0001").write_text('{"fullText', encoding="utf-8")
    assert load_saved_response(tmp_path, "p_0001", image_sha256=H1) is None


def test_save_without_hash_drops_stale_sidecar(tmp_path):
    """ハッシュ無しの上書きで古いサイドカーを残さない（誤判定で再送になる）。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    save_response(tmp_path, "p_0001", {"fullTextAnnotation": {"text": "new"}})
    assert not response_meta_path(tmp_path, "p_0001").exists()
    assert saved_image_hash(tmp_path, "p_0001") is None


def test_failed_save_keeps_previous_response(tmp_path, monkeypatch):
    """置き換えに失敗しても前の応答は壊れず、一時ファイルも残らない。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(vision_client.os, "replace", boom)
    with pytest.raises(OSError):
        save_response(tmp_path, "p_0001", {"fullTextAnnotation": {"text": "new"}},
                      image_sha256=H2)
    assert json.loads(response_path(tmp_path, "p_0001").read_text(encoding="utf-8")) == RESP
    assert saved_image_hash(tmp_path, "p_0001") == H1
    assert [p.name for p in (tmp_path / "responses").glob("*.tmp")] == []


def test_tmp_name_is_process_specific(tmp_path, monkeypatch):
    """一時ファイル名にプロセス ID が入る（同時実行で同じ tmp を取り合わない）。"""
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(vision_client.os, "replace", spy)
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    assert seen and all(str(os.getpid()) in name and name.endswith(".tmp")
                        for name in seen)


def test_replay_client_ignores_the_sidecar(tmp_path):
    """再生素材の置き場に .meta.json が増えても ReplayClient は本体だけを読む。"""
    save_response(tmp_path, "p_0001", RESP, image_sha256=H1)
    client = vision_client.ReplayClient(tmp_path / "responses")
    assert client.annotate(b"png", "p_0001") == RESP
