"""S-MC: `purge --include-output` の削除範囲（レビュー7巡目 #69）。

出力先（output_dir）は GUI で任意のフォルダを指せる——デスクトップや共有
フォルダを指している運用がありうる。「出力も消す」をフォルダ削除で実装すると
利用者が自分で置いたファイルを巻き込むため、**このツールの命名に一致する
ファイルだけを1件ずつ消す**（フォルダ自体は残す）仕様をここで固定する。

命名の正は render_out.write_outputs（`output_<日時>.xlsx` / `.csv` /
`_columns.txt`）と、その退避 `.bak`・一時ファイル `.tmp`。日時は
pipeline._render_locked の `%Y%m%d_%H%M%S`。
"""
import json

from chouhyo_ocr import cli

TS = "20260101_000000"
TOOL_FILES = (f"output_{TS}.xlsx", f"output_{TS}.csv",
              f"output_{TS}_columns.txt", f"output_{TS}.xlsx.bak")
KEEP_FILES = ("user_memo.xlsx", "2026年1月分_提出用.xlsx")


def _setup(tmp_path, tool_files=TOOL_FILES, keep_files=KEEP_FILES):
    """config.json ＋ 中間データ ＋ 出力先のファイル群を用意する。"""
    out = tmp_path / "out"
    out.mkdir()
    for name in (*tool_files, *keep_files):
        (out / name).write_text("x", encoding="utf-8")
    wd = tmp_path / "wd"
    wd.mkdir()
    (wd / "intermediate.sqlite").write_text("x", encoding="utf-8")
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"output_dir": str(out), "workdir": str(wd),
                               "log_dir": str(tmp_path / "logs")}),
                   encoding="utf-8")
    return cfg, out, wd


def _purge(cfg, *extra):
    return cli.main(["--config", str(cfg), "purge", *extra])


def _events(capsys):
    out = capsys.readouterr().out
    return ([json.loads(line) for line in out.splitlines()
             if line.startswith("{")], out)


def test_include_output_removes_generated_files_only(tmp_path, capsys):
    """生成物4件は消え、利用者のファイル2件は残る（件数は出力とログの両方に出る）。"""
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--yes", "--include-output") == 0

    assert not wd.exists()                                   # 中間データは従来どおり
    assert sorted(p.name for p in out.iterdir()) == sorted(KEEP_FILES)
    assert out.is_dir()                                      # フォルダ自体は残す

    events, raw = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert (ev["output_removed"], ev["output_kept"], ev["output_failed"]) == (4, 2, 0)
    assert "削除 4 件／対象外として残したファイル 2 件" in raw

    # 削除前の走査結果がログに残る（件数と日時のみ・記入値は含まない）
    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "purge_output_scan count=4 kept=2" in app_log
    assert f"timestamps={TS}" in app_log
    assert "purge_output_done count=4 failed=0 kept=2" in app_log


def test_include_output_accepts_compact_timestamp(tmp_path):
    """日時が区切りなし14桁の命名も対象にする（呼び出し側が渡す形式の揺れ）。"""
    compact = ("output_20260101000000.xlsx", "output_20260101000000.csv",
               "output_20260101000000_columns.txt",
               "output_20260101000000.csv.bak")
    cfg, out, _wd = _setup(tmp_path, tool_files=compact)
    assert _purge(cfg, "--yes", "--include-output") == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(KEEP_FILES)


def test_similar_names_are_kept(tmp_path, capsys):
    """惜しい名前は消さない——誤爆したら利用者のファイルが戻らない。"""
    near_miss = ("output.xlsx", "output_2026.xlsx", "output_20260101_00.xlsx",
                 f"output_{TS}.pdf", f"myoutput_{TS}.xlsx",
                 f"output_{TS}.xlsx.bak2", f"output_{TS}_columns.txt.old")
    cfg, out, _wd = _setup(tmp_path, tool_files=(f"output_{TS}.xlsx",),
                           keep_files=near_miss)
    assert _purge(cfg, "--yes", "--include-output") == 0
    assert sorted(p.name for p in out.iterdir()) == sorted(near_miss)
    _events(capsys)


def test_subdirectory_is_left_alone(tmp_path, capsys):
    """サブフォルダは中身ごと残す（走査は直下のみ・rmtree していないことの証跡）。"""
    cfg, out, _wd = _setup(tmp_path)
    sub = out / "提出済み"
    sub.mkdir()
    (sub / f"output_{TS}.xlsx").write_text("x", encoding="utf-8")

    assert _purge(cfg, "--yes", "--include-output") == 0
    assert (sub / f"output_{TS}.xlsx").exists()
    # ディレクトリは「残したファイル数」に数えない（数えるのは直下のファイルのみ）
    events, _raw = _events(capsys)
    assert next(e for e in events if e["event"] == "purged")["output_kept"] == 2


def test_without_include_output_keeps_outputs(tmp_path, capsys):
    """既定（--include-output なし）は出力に一切触れない。"""
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--yes") == 0
    assert not wd.exists()
    assert len(list(out.iterdir())) == len(TOOL_FILES) + len(KEEP_FILES)

    events, raw = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert "output_removed" not in ev and "削除" not in raw


def test_requires_yes_even_with_include_output(tmp_path):
    """--yes なしは拒否（TR-G5 と同型）——出力側の追加でも明示操作の要求は同じ。"""
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--include-output") == 1
    assert wd.exists()
    assert len(list(out.iterdir())) == len(TOOL_FILES) + len(KEEP_FILES)


def test_missing_output_dir_is_not_an_error(tmp_path, capsys):
    """出力先が無い／空でも件数0で正常終了する（未実行の workdir を消すだけの用途）。"""
    cfg, out, _wd = _setup(tmp_path, tool_files=(), keep_files=())
    out.rmdir()
    assert _purge(cfg, "--yes", "--include-output") == 0

    events, raw = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert (ev["output_removed"], ev["output_kept"]) == (0, 0)
    assert "削除 0 件／対象外として残したファイル 0 件" in raw
