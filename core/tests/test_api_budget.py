"""API 送信ユニットの月次上限（強制停止・2026-08-28 ユーザー指示）。

「請求が立つ前に止めたい」という要求に対し、警告ではなく**強制停止**で応える。
ここで固定する不変条件:
1. 上限に達したら送信そのものを行わない（1バイトも送らない）
2. Replay（テスト）はカウントしない——テストで枠を食い潰さない
3. カウントは workdir の外に持つ（purge・複数 workdir をまたいで合算される）
4. 月が変われば数え直す
"""
import json
import os

import pytest

from chouhyo_ocr import api_budget
from chouhyo_ocr.config import Config
from chouhyo_ocr.vision_client import ReplayClient


@pytest.fixture()
def isolated_counter(tmp_path, monkeypatch):
    """カウンタを一時ディレクトリへ隔離する（実カウンタを汚さない）。"""
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(tmp_path))
    return tmp_path


def test_counts_and_stops_at_cap(isolated_counter):
    """上限に達したら例外で止まり、カウントはそれ以上増えない。"""
    for i in range(1, 4):
        assert api_budget.check_and_count(1, cap=3) == i
    with pytest.raises(api_budget.BudgetExceededError, match="上限"):
        api_budget.check_and_count(1, cap=3)
    assert api_budget.used_this_month() == 3  # 拒否した分は数えない


def test_real_client_stops_before_sending(isolated_counter, monkeypatch):
    """上限超過時、**API クライアントを一切呼ばずに**止まる。

    「送ってから記録」だと異常終了で取りこぼすため、送信の直前で数える。
    ここでは実 API を使わず、呼ばれたら分かるダミーを差し込んで検証する。
    """
    from chouhyo_ocr import vision_client

    called = []

    class FakeVision:
        def Image(self, content):  # noqa: N802
            return content

        def ImageContext(self, language_hints):  # noqa: N802
            return language_hints

        class ImageAnnotatorClient:
            def document_text_detection(self, **kw):
                called.append(kw)
                raise AssertionError("上限超過なのに送信された")

    client = vision_client.RealVisionClient.__new__(vision_client.RealVisionClient)
    client._vision = FakeVision()
    client._to_dict = lambda x: {}
    client.client = FakeVision.ImageAnnotatorClient()
    client.monthly_cap = 2

    api_budget.check_and_count(2, cap=2)  # 枠を使い切らせる
    with pytest.raises(api_budget.BudgetExceededError):
        client.annotate(b"png", "p1")
    assert called == [], "上限に達しているのに API を呼んだ"


def test_replay_client_does_not_count(isolated_counter, tmp_path):
    """Replay（テスト・再生）はカウントしない——テストで無料枠を食わない。"""
    resp = tmp_path / "r"; resp.mkdir()
    (resp / "p1.json").write_text(json.dumps({"fullTextAnnotation": {"pages": []}}),
                                  encoding="utf-8")
    before = api_budget.used_this_month()
    ReplayClient(resp).annotate(b"png", "p1")
    assert api_budget.used_this_month() == before


def test_counter_lives_outside_workdir(isolated_counter):
    """カウンタは workdir の外（purge しても消えない・複数 workdir で合算）。"""
    p = api_budget.usage_path()
    assert "workdir" not in str(p).lower()
    api_budget.check_and_count(1, cap=10)
    assert p.exists()


def test_month_rollover_resets(isolated_counter, monkeypatch):
    """月が変われば数え直す（無料枠は月次のため）。"""
    api_budget.check_and_count(5, cap=10)
    assert api_budget.used_this_month() == 5
    monkeypatch.setattr(api_budget, "current_month", lambda: "2099-01")
    assert api_budget.used_this_month() == 0
    assert api_budget.remaining(cap=10) == 10


def test_config_carries_the_cap():
    """既定の上限は 900（無料枠 1,000 に余裕を残す）。"""
    assert Config().api_monthly_cap == 900
    assert api_budget.DEFAULT_CAP == 900
    assert api_budget.FREE_TIER_UNITS == 1000
    assert Config().api_monthly_cap < api_budget.FREE_TIER_UNITS


def test_corrupt_counter_does_not_block_work(isolated_counter, tmp_path):
    """壊れたカウンタで運用を止めない（0 から数え直す）。

    issue #91: 数え直す方針は変えないが、**無音でリセットしない**。壊れた
    ファイルを退避してログへ残す（旧実装はここが1行も出さず、上限分がまるごと
    再消費されても利用者が気づく手段が無かった）。
    """
    from chouhyo_ocr import logging_safe

    logging_safe.init(str(tmp_path / "logs"))
    p = api_budget.usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{壊れた", encoding="utf-8")
    assert api_budget.used_this_month() == 0
    assert api_budget.check_and_count(1, cap=5) == 1

    broken = list(p.parent.glob("api_usage.broken.*.json"))
    assert len(broken) == 1, "壊れたカウンタを退避していない"
    assert broken[0].read_text(encoding="utf-8") == "{壊れた"
    log_text = (tmp_path / "logs" / "error.log").read_text(encoding="utf-8")
    assert "api_usage_corrupt" in log_text and "state=quarantined" in log_text
    # 退避先はファイル名のみ（絶対パスを出さない・設計 §8.1）
    assert str(p.parent) not in log_text


def test_counter_of_wrong_shape_is_quarantined(isolated_counter, tmp_path):
    """JSON としては読めるが形が違う（配列）場合も破損扱いで退避する。"""
    from chouhyo_ocr import logging_safe

    logging_safe.init(str(tmp_path / "logs"))
    p = api_budget.usage_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    assert api_budget.used_this_month() == 0
    assert list(p.parent.glob("api_usage.broken.*.json"))


def test_save_tmp_name_is_process_specific(isolated_counter, monkeypatch):
    """一時ファイル名にプロセス ID が入る（issue #91）。

    固定名（.json.tmp）だと別 workdir の2プロセスが同じ tmp を掴み、
    書きかけの昇格・FileNotFoundError の2通りで壊れる。
    """
    import os

    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(os.path.basename(src))
        real_replace(src, dst)

    monkeypatch.setattr(api_budget.os, "replace", spy)
    api_budget.check_and_count(1, cap=5)
    assert seen and str(os.getpid()) in seen[0] and seen[0].endswith(".tmp")


def test_failed_save_keeps_previous_counter(isolated_counter, monkeypatch):
    """置き換えに失敗しても前のカウンタは壊れず、一時ファイルも残らない。"""
    import os

    api_budget.check_and_count(3, cap=10)
    p = api_budget.usage_path()

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(api_budget.os, "replace", boom)
    with pytest.raises(OSError):
        api_budget.check_and_count(1, cap=10)
    assert api_budget.used_this_month() == 3
    assert [q.name for q in p.parent.glob("*.tmp")] == []


def test_docstring_describes_the_real_granularity():
    """粒度の説明を実装（PC・ユーザー単位＋年月キー）に合わせる（issue #91 (4)）。

    旧 docstring は「課金は GCP プロジェクト単位で合算されるため、カウンタも
    同じ粒度で持つ」と説明していたが、実装のキーは年月だけで保存先は
    %LOCALAPPDATA%。プロジェクトを切り替えても同じカウンタを消費する。
    """
    assert "GCP プロジェクト単位で合算" not in api_budget.__doc__
    assert "年月" in api_budget.__doc__
