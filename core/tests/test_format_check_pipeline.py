"""様式判定の pipeline 配線テスト（issue #71 (a')・08 §2.4・§2.5）。

対応する受入基準（07 §8.1）:
- AC-F01: 別様式（formC）を run に投入 → 様式不一致・専用理由コード・API 0回
- AC-F12: 一部ページだけ不一致のバッチが止まらず全ページ分の行が出る
- AC-F13: 全ページのスコア・理由コードが中間データへ残る（一致ページも）
- AC-F14: 判定関数に例外を注入しても様式不一致に化けない（AC-F14 の歯止め）

formC-1.png は align_page が最初の面（front）で AlignError を送出して停止
するため、Vision への送信に到達しない——保存済み応答（replay 素材）は不要。
"""
import json
import shutil

import pytest

from chouhyo_ocr import format_check, logging_safe, pipeline
from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.store import Store
from chouhyo_ocr.vision_client import ReplayClient

TPL = app_root() / "templates" / "chouhyo-v1.json"
RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
FORMC_PNG = app_root() / "testdata" / "formC" / "formC-1.png"

needs_formc = pytest.mark.skipif(
    not FORMC_PNG.exists(), reason="formC-1.png が無い環境（make_formC.py で生成）")
needs_replay = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・サンプル画像が無い環境")


def _cfg(tmp_path) -> Config:
    return Config(output_dir=str(tmp_path / "out"), workdir=str(tmp_path / "wd"),
                 log_dir=str(tmp_path / "logs"))


@needs_formc
def test_ac_f01_unrelated_form_becomes_format_mismatch_with_zero_api_calls(tmp_path):
    """AC-F01: 同寸別様式（formC）を run に投入すると、送信前に様式不一致へ
    倒れ、FR-F01 由来の専用理由コード（frame_ 接頭）が付き、Vision API
    呼び出しは 0 回になる。
    """
    inp = tmp_path / "input"; inp.mkdir()
    shutil.copy(FORMC_PNG, inp / "formC-1.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()  # 空——送信に到達しない

    events: list[dict] = []
    summary = run(inp, TPL, _cfg(tmp_path), ReplayClient(replay_dir), events.append)

    assert summary.api_calls == 0
    assert summary.format_mismatch == 1
    assert summary.format_mismatch_pre_send == 1

    page_ev = next(e for e in events if e.get("event") == "page")
    assert page_ev["status"] == "様式不一致"
    assert page_ev["reason_code"].startswith("frame_")

    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        pages = store.pages()
        assert len(pages) == 1
        row = pages[0]
        assert row["status"] == "様式不一致"
        assert row["status_reason"].startswith("frame_")
        assert row["format_verdict"] == "mismatch"
        detail = json.loads(row["format_detail"])
        assert detail  # 面ごとの内訳が空でない


@needs_formc
@needs_replay
def test_ac_f12_mixed_batch_does_not_stop_and_counts_pre_send(tmp_path):
    """AC-F12: 一致するページ（sample-1）と不一致のページ（formC）が混在する
    バッチを run しても止まらず、両方のページ分の行が出る。実行結果には
    「送信前に止まった」件数（format_mismatch_pre_send）が区別して残る。
    """
    inp = tmp_path / "input"; inp.mkdir()
    shutil.copy(PAGE_PNG, inp / "sample-1.png")
    shutil.copy(FORMC_PNG, inp / "formC-1.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    summary = run(inp, TPL, _cfg(tmp_path), ReplayClient(replay_dir))
    assert summary.pages == 2
    assert summary.format_mismatch_pre_send == 1
    assert summary.api_calls == 1  # formC は送信されない・sample-1 のみ送信

    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        statuses = {p["source_file"]: p["status"] for p in store.pages()}
    assert statuses["sample-1.png"] == ""  # 正常（空文字列＝失敗系を剥がした状態）
    assert statuses["formC-1.png"] == "様式不一致"


@needs_replay
def test_ac_f13_all_pages_including_matches_are_recorded(tmp_path):
    """AC-F13: 一致したページの分も、全ページのスコア・理由コード・
    検出/期待本数が中間データ**とログ**へ残る（H-1・2026-09-02 マリン指摘:
    FR-F12/AC-F13 のログ側が未実装だった。08 §2.5.3 の format_verdict 行を
    _record_format_result 経由で出す）。
    """
    inp = tmp_path / "input"; inp.mkdir()
    shutil.copy(PAGE_PNG, inp / "sample-1.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    # pipeline.run() 自体は log.init を呼ばない（cli.cmd_run の責務）ため、
    # ここで直接呼ぶ（cli.main() を経由しない直接呼び出しのテストのため）
    logging_safe.init(str(tmp_path / "logs"))
    run(inp, TPL, _cfg(tmp_path), ReplayClient(replay_dir))

    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        row = store.pages()[0]
    assert row["format_verdict"] == "match"
    assert row["format_reason"] == ""
    assert 0.0 <= row["format_score"] <= 1.0
    detail = json.loads(row["format_detail"])
    assert {d["verdict"] for d in detail} == {"match"}
    assert {d["face_idx"] for d in detail} == {0, 1}  # front/back の両方
    for d in detail:
        assert d["detected"] > 0 and d["expected"] > 0

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "template_loaded" in app_log and "template_hash=" in app_log
    # H-1: 08 §2.5.3 のログ行が実際に出ること（キーはすべて logging_safe の
    # 白リストに既に追加済み・verdict/reason_code/score/detected/expected）
    fv_lines = [line for line in app_log.splitlines() if "format_verdict" in line]
    assert fv_lines
    assert any("verdict=match" in line and "detected=" in line
               and "expected=" in line and "score=" in line for line in fv_lines)

    # M-5（2026-09-02 マリン指摘）: ログ行の score/detected/expected が
    # 「同一の代表面」由来であることを実データで固定する。修正前は
    # verdict 優先順で選ぶ代表面と score 最小の面が別々に計算されており、
    # 同順位の面が複数あると食い違いうる（synthetic な再現は
    # test_format_check.py::test_fold_score_detected_expected_come_from_the_same_representative_face
    # 側）。ここでは実データ（front/back の実測値）で、ログに出た5値の
    # 組み合わせが format_detail 中のどれか1面と完全一致することを検証する
    # ログ行は "<asctime> <levelname> format_verdict k=v k=v ..." の形
    # （logging_safe.init の Formatter）——先頭2トークンは時刻・レベルなので
    # 決め打ちで数えず、イベント名 "format_verdict" の直後から切り出す
    after = fv_lines[0].split("format_verdict", 1)[1].strip()
    kv = dict(p.split("=", 1) for p in after.split())
    logged = (kv["verdict"], float(kv["score"]), int(kv["detected"]), int(kv["expected"]))
    assert any(
        (d["verdict"], d["score"], d["detected"], d["expected"]) == logged
        for d in detail
    ), f"logged {kv} は format_detail のどの面とも一致しない: {detail}"


@needs_formc
def test_ac_f14_exception_in_judge_does_not_masquerade_as_format_mismatch(tmp_path, monkeypatch):
    """AC-F14: FR-F01 の判定関数（format_check.from_diag）に例外を注入して
    run する → status は様式不一致にならず、現行バケツ（位置合わせ失敗）の
    まま。format_check_failed とトレースがログに残る（row_build_failed と
    同型の歯止め）。「全ページ様式不一致・API 0回」という新機能が完璧に
    働いているのと同じ見え方にならないことを確認する。
    """
    def _boom(diag):
        # メッセージは変数経由で渡す——traceback.format_tb はソース行
        # （`raise RuntimeError(secret)`）を含むが、実行時の値
        # （変数 secret の中身）はソース行に現れない。文字列リテラルを直接
        # raise 文に書くと、たとえ format_tb でもソース行としてその文字列が
        # 再現されてしまい、このテストの検査対象（例外メッセージの値が
        # 出ないこと）と取り違える
        secret = "judge is broken"
        raise RuntimeError(secret)
    monkeypatch.setattr(format_check, "from_diag", _boom)

    inp = tmp_path / "input"; inp.mkdir()
    shutil.copy(FORMC_PNG, inp / "formC-1.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()

    logging_safe.init(str(tmp_path / "logs"))
    summary = run(inp, TPL, _cfg(tmp_path), ReplayClient(replay_dir))
    assert summary.format_mismatch == 0        # 様式不一致に化けていない
    assert summary.align_failed == 1            # 現行バケツのまま

    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        row = store.pages()[0]
    assert row["status"] == "位置合わせ失敗"
    assert row["status_reason"] == "frame_check_failed"

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    err_log = (tmp_path / "logs" / "error.log").read_text(encoding="utf-8")
    assert "format_check_failed error_code=RuntimeError" in app_log
    assert "format_check_failed error_code=RuntimeError" in err_log
    # トレース本体は error_trace 経由（row_build_failed と同型）で残る。
    # error_trace は独自のイベント名を持たず "unhandled_exception
    # error_code=<型名>" 固定文言で出す（logging_safe.error_trace の既存契約）
    assert "unhandled_exception error_code=RuntimeError" in err_log
    # 例外メッセージ本文は出さない（error_trace は traceback.format_tb のみを
    # 受け取る契約・記入値を含みうるため）
    assert "judge is broken" not in app_log and "judge is broken" not in err_log


def test_m2_status_reason_does_not_leak_across_status_changes(tmp_path):
    """M-2（2026-09-02 マリン指摘）: Store.set_status は reason を明示しない
    限り status_reason を空へ戻す——古い理由コードが後続の status 変更へ
    残留しない構造になっている。

    シナリオ: run 1 相当で frame_lines（送信前に止まった様式不一致）が
    立った後、run 2 相当で整列に成功し送信・割付まで通って正常へ遷移する
    と、status_reason が "" に戻ることを Store 単体で直接確認する
    （「送信前に止まった」ことを示す理由コードが、実際には送信後に正常化
    したページへ残留すると FR-F10 の送信前/送信後の区別が壊れる）。
    """
    db = tmp_path / "intermediate.sqlite"
    with Store(db) as store:
        store.upsert_page("p1", "a.png", 1, "failed")
        store.set_status("p1", "様式不一致", reason="frame_lines")
        row = store.page("p1")
        assert row["status"] == "様式不一致" and row["status_reason"] == "frame_lines"

        # run 2 相当: 整列成功→送信→割付成功で正常へ遷移（reason を渡さない）
        store.set_status("p1", "")
        row = store.page("p1")
        assert row["status"] == ""
        assert row["status_reason"] == ""  # 残留していない


def test_m2_post_send_codes_get_status_reason(tmp_path):
    """M-2: 送信後3コードのうち map_failed・outside_ratio は
    store.set_status(..., reason=...) 経由で status_reason が記録される
    （row_build_failed は render 時に page.status 自体を書き換えない既存
    構造のため対象外——完了報告に理由を記す）。
    """
    inp = tmp_path / "input"; inp.mkdir()
    shutil.copy(PAGE_PNG, inp / "sample-1.png")
    replay_dir = tmp_path / "responses"; replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    def _boom(*a, **kw):
        raise RuntimeError("map broken")

    import chouhyo_ocr.pipeline as pipeline_mod
    orig = pipeline_mod.assign
    pipeline_mod.assign = _boom
    try:
        logging_safe.init(str(tmp_path / "logs"))
        run(inp, TPL, _cfg(tmp_path), ReplayClient(replay_dir))
    finally:
        pipeline_mod.assign = orig

    with Store(tmp_path / "wd" / "intermediate.sqlite") as store:
        row = store.pages()[0]
    assert row["status"] == "様式不一致"
    assert row["status_reason"] == "map_failed"
