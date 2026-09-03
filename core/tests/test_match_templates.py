"""`match-templates` サブコマンドのテスト（issue #72 (t)・08 §3.3）。

候補の列挙・パス検査は Rust の責務（08 §3.10 不変条件3）——ここでは
`CHOUHYO_USER_DIR` を指した一時ディレクトリを「Rust が列挙した結果」に
見立て、そこから得た絶対パスを `--candidate` として渡す形でテストする
（match-templates 自身はディレクトリ列挙をしない）。
"""
import json
import shutil

import pytest

from chouhyo_ocr import cli
from chouhyo_ocr.paths import app_root, user_templates_dir

TPL = app_root() / "templates" / "chouhyo-v1.json"
FORMB = app_root() / "testdata" / "formB" / "formB-v1.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"

needs_sample = pytest.mark.skipif(
    not PAGE_PNG.exists(), reason="サンプル画像が無い環境")


def _cfg(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    return cfg_path


@needs_sample
def test_shipped_matches_formb_mismatches_and_bad_candidates_are_excluded(
        tmp_path, capsys, monkeypatch):
    """出荷（sample-1 と同寸）が match・formB（別様式）が mismatch・
    不正 JSON が excluded になり、他候補の照合を止めない（FR-F28）。
    存在しない候補パスも `not_found` として除外され、同様に続行する
    （2026-09-02 マリン提案）。
    """
    # 実運用では Rust が create_dir_all してから core へ env を渡す
    # （08 §3.1.3）ので、ここでも先にディレクトリを作ってから
    # user_templates_dir() で検証する
    user_dir = tmp_path / "user_templates"
    user_dir.mkdir(parents=True)
    monkeypatch.setenv("CHOUHYO_USER_DIR", str(user_dir))
    assert user_templates_dir() == user_dir  # 検証を通ることの前提確認
    shutil.copy(FORMB, user_dir / "帳票B.json")
    (user_dir / "壊れたテンプレ.json").write_text("{not valid json", encoding="utf-8")
    missing_candidate = user_dir / "存在しない.json"  # 作らない——not_found の対象
    # Rust が列挙した結果に見立てる（*.json を集めるだけ・実際の除外規則は
    # Rust 側が担う——ここは match-templates 自身の挙動のテストが目的）
    candidates = sorted(user_dir.glob("*.json")) + [missing_candidate]

    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL)]
                  + [x for c in candidates for x in ("--candidate", str(c))])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["event"] == "match_templates" and ev["ok"] is True
    assert ev["input_size"] == [2490, 3510]

    by_name = {r["name"]: r for r in ev["results"]}
    assert by_name["chouhyo-v1"]["kind"] == "shipped"
    assert by_name["chouhyo-v1"]["verdict"] == "match"
    assert 0.0 <= by_name["chouhyo-v1"]["score"] <= 1.0
    # M-5（2026-09-02 マリン指摘）: fields は単発欄数のみ（table_id 付きの
    # 表由来セルを含まない）。出荷テンプレートの raw JSON を独立に数えた
    # 値（14）と一致することを固定する——物理セル数（220 列相当）ではない
    raw_shipped = json.loads(TPL.read_text(encoding="utf-8"))
    expected_fields = sum(len(f.get("fields", [])) for f in raw_shipped["faces"])
    assert by_name["chouhyo-v1"]["fields"] == expected_fields
    assert by_name["chouhyo-v1"]["tables"] > 0
    assert by_name["chouhyo-v1"]["template_id"]
    # updated_at は ISO 8601 文字列（fromisoformat が読めることで確認する）
    from datetime import datetime
    datetime.fromisoformat(by_name["chouhyo-v1"]["updated_at"])

    assert by_name["帳票B"]["kind"] == "user"
    assert by_name["帳票B"]["verdict"] == "mismatch"

    # results[].name に区切り文字が無い（絶対パスの断片が漏れていないこと・
    # 2026-09-02 マリン提案）
    for r in ev["results"]:
        assert "/" not in r["name"] and "\\" not in r["name"]

    excluded_names = {e["name"]: e["reason"] for e in ev["excluded"]}
    # M-4（2026-09-02 マリン指摘）: 語彙を Rust 側と統一
    # （invalid_json → parse・p.stat() の OSError → not_found）
    assert excluded_names["壊れたテンプレ"] == "parse"
    assert excluded_names["存在しない"] == "not_found"

    # 絶対パス・テンプレートファイル名以外の記入値相当の情報が出ないこと
    # （name は表示名のみ・07 §7.3・§9.4——ここでは stdout 契約なので出て
    # よい対象。ログ側の秘匿は test_leak_guards.py 側が担保する）
    assert ev["truncated"] is False
    assert isinstance(ev["elapsed_ms"], int) and ev["elapsed_ms"] >= 0
    # M-3（2026-09-02 マリン指摘）: budget_elapsed_ms は候補ループのみの
    # 時間で、展開・画像読み込みを含む elapsed_ms 以下になる
    assert isinstance(ev["budget_elapsed_ms"], int) and ev["budget_elapsed_ms"] >= 0
    assert ev["budget_elapsed_ms"] <= ev["elapsed_ms"]


@needs_sample
def test_results_preserve_shipped_then_candidate_order(tmp_path, capsys):
    """results は shipped → candidate の指定順で並ぶ（並べ替えは GUI の
    rankCandidates の責務・08 §3.4。core は並べ替えない）。
    """
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL),
                   "--candidate", str(FORMB)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert [r["kind"] for r in ev["results"]] == ["shipped", "user"]
    assert [r["name"] for r in ev["results"]] == ["chouhyo-v1", "formB-v1"]


@needs_sample
def test_oversized_candidate_is_excluded_with_size_reason(tmp_path, capsys):
    """5MB を超える候補はパースを試みる前に除外される（07 §7.3 の暫定上限）。"""
    huge = tmp_path / "huge.json"
    huge.write_bytes(b"0" * (5 * 1024 * 1024 + 1))
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL),
                   "--candidate", str(huge)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert {"name": "huge", "reason": "size"} in ev["excluded"]
    assert ev["results"] == [r for r in ev["results"] if r["name"] != "huge"]


@needs_sample
def test_schema_invalid_template_is_excluded_without_stopping_others(tmp_path, capsys):
    """JSON としては読めるがスキーマ不正（load_template/validate_v1 が拒否）な
    候補は schema として除外され、後続の候補の照合は続く。
    """
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    raw["schema_version"] = 999  # v1 の受理範囲外（TemplateError）
    bad_schema = tmp_path / "bad_schema.json"
    bad_schema.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL),
                   "--candidate", str(bad_schema), "--candidate", str(FORMB)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    excluded_names = {e["name"]: e["reason"] for e in ev["excluded"]}
    assert excluded_names["bad_schema"] == "schema"
    # bad_schema の後ろの formB-v1 の照合は止まっていない（FR-F28）
    assert any(r["name"] == "formB-v1" for r in ev["results"])


@needs_sample
def test_time_budget_truncates_remaining_candidates(tmp_path, capsys, monkeypatch):
    """合計時間予算（NFR-F09 暫定 3.0 秒）を使い切ったら、残りの候補を
    excluded reason:"limit" として打ち切る。打ち切りはテンプレート単位
    （check_page の途中では止めない・08 §3.3.3）——ここでは定数を極小に
    書き換えて確実に発火させる。
    """
    import chouhyo_ocr.cli as cli_mod
    monkeypatch.setattr(cli_mod, "_MATCH_TEMPLATES_TIME_BUDGET_S", -1.0)

    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL),
                   "--candidate", str(FORMB)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["truncated"] is True
    # 予算超過は「次の1件を始める前」に見るため、1件目（shipped）から
    # 打ち切り対象になる
    assert ev["results"] == []
    assert {e["reason"] for e in ev["excluded"]} == {"limit"}
    assert {e["name"] for e in ev["excluded"]} == {"chouhyo-v1", "formB-v1"}


@needs_sample
def test_partial_success_then_time_budget_truncates_rest(tmp_path, capsys, monkeypatch):
    """一部の候補（1件目・出荷）だけ通ったところで時間予算を使い切ると、
    それまでの results は保持したまま残り（2件目以降）だけを
    truncated:true・reason:"limit" で打ち切る（2026-09-02 マリン提案）。
    予算チェックは「次の1件を始める前」（08 §3.3.3）なので、1件目は
    必ず最後まで処理される。
    """
    import time as real_time

    calls = {"n": 0}

    def fake_perf_counter():
        calls["n"] += 1
        # 呼び出し順（実装の実測に基づく）: 1=t0, 2=budget_t0,
        # 3=1件目（shipped）の予算チェック（ここまでは予算内で通す）。
        # 4回目以降は「予算を使い切った」を表す大きな値を返す——これ以降の
        # 呼び出し回数が実装の細部（ログ等）で増減しても、一度超過した後は
        # 超過のままなので後続の候補はすべて limit になる
        return 0.0 if calls["n"] <= 3 else 100.0

    monkeypatch.setattr(real_time, "perf_counter", fake_perf_counter)

    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--shipped", str(TPL),
                   "--candidate", str(FORMB)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["truncated"] is True
    assert [r["name"] for r in ev["results"]] == ["chouhyo-v1"]
    assert {e["name"]: e["reason"] for e in ev["excluded"]} == {"formB-v1": "limit"}


def test_input_not_found_returns_fixed_error_code(tmp_path, capsys):
    """M-6（2026-09-02 マリン指摘）: 存在しない --input は機械可読な固定コード
    input_not_found を返す（type(e).__name__ や例外メッセージではない）。
    """
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(tmp_path / "no_such_file.png"),
                   "--shipped", str(TPL)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev == {"event": "match_templates", "ok": False, "error": "input_not_found"}


@needs_sample
def test_page_out_of_range_returns_expand_failed(tmp_path, capsys):
    """M-6: 範囲外の --page は expand_failed を返す（画像ファイルは常に
    1ページ扱いなので --page 2 は範囲外になる）。
    """
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(PAGE_PNG), "--page", "2",
                   "--shipped", str(TPL)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev == {"event": "match_templates", "ok": False, "error": "expand_failed"}


def test_unreadable_input_returns_fixed_error_code(tmp_path, capsys):
    """M-6: 画像として読めないファイル（拡張子は画像だが中身が壊れている）は
    input_unreadable を返す。
    """
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not a real png file")
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "match-templates",
                   "--input", str(corrupt), "--shipped", str(TPL)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev == {"event": "match_templates", "ok": False, "error": "input_unreadable"}
