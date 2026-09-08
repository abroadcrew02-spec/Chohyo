"""S-MC: `purge --include-output` の削除範囲（レビュー7巡目 #69）。
   併せて issue #83: purge が資格情報 cred.dpapi まで削除する事故の修正。

出力先（output_dir）は GUI で任意のフォルダを指せる——デスクトップや共有
フォルダを指している運用がありうる。「出力も消す」をフォルダ削除で実装すると
利用者が自分で置いたファイルを巻き込むため、**このツールの命名に一致する
ファイルだけを1件ずつ消す**（フォルダ自体は残す）仕様をここで固定する。

命名の正は render_out.write_outputs（`output_<日時>.xlsx` / `.csv` /
`_columns.txt`）と、その退避 `.bak`・一時ファイル `.tmp`。日時は
pipeline._render_locked の `%Y%m%d_%H%M%S`。

workdir 側（中間データ）は以前 shutil.rmtree(wd) で丸ごと消していたため、
workdir 直下に置く暗号化資格情報 cred.dpapi（cred_store.blob_name()）も
巻き込んで消えていた（issue #83）。keep-list 方式（cred.dpapi だけを残し
それ以外は種類を問わず全部消す）への切り替えを、このファイル下半分の
テストで固定する。
"""
import json
import shutil
import stat
import subprocess
import sys

import pytest

from chouhyo_ocr import cli, cred_store

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
    """stdout の JSON Lines イベント一覧・stdout 生テキスト・stderr 生テキストを返す。

    issue #144（2026-09-08）: 人が読む要約行（「削除 N 件／…」「中間データ N 件を
    削除し…」）は stdout の JSON Lines「1行1イベント」契約を破らないよう
    stderr へ移した。呼び出し側はこの2つを別々に検証する。
    """
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()
             if line.startswith("{")]
    return events, captured.out, captured.err


def _force_tty(monkeypatch):
    """人が読む1行（issue #144 続き）は isatty のときだけ出る。件数そのものは
    JSON イベント（`_events` の1つ目の戻り値）に必ず載るので GUI 側は
    そちらを見る——この1行の有無を確かめたいテストだけがこのヘルパーで
    端末相当にする。
    """
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)


def test_human_lines_are_stderr_tty_only(tmp_path, capsys):
    """人が読む1行（「中間データ N 件を削除し…」「削除 N 件／…」）は、
    パイプ実行（GUI・このテストのようなキャプチャ環境）では stderr に
    出ない（issue #144 続き）。GUI は core の stderr をそのまま
    `[err] ${line}` として core-err ログへ流すため、この行を出したままだと
    成功時のサマリまでエラー調に見えてしまう。件数は purged イベント
    （JSON Lines）に必ず載るので、GUI 側の可視化はそちらで担保する。
    """
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--yes", "--include-output") == 0

    events, _raw, err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert (ev["removed"], ev["output_removed"], ev["output_kept"]) == (1, 4, 2)
    assert err == ""


def test_include_output_removes_generated_files_only(tmp_path, capsys, monkeypatch):
    """生成物4件は消え、利用者のファイル2件は残る（件数は出力とログの両方に出る）。"""
    _force_tty(monkeypatch)
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--yes", "--include-output") == 0

    # workdir 自体は keep-list 方式（#83）では残るが、中身の中間データは消える
    assert wd.exists()
    assert not (wd / "intermediate.sqlite").exists()
    assert sorted(p.name for p in out.iterdir()) == sorted(KEEP_FILES)
    assert out.is_dir()                                      # フォルダ自体は残す

    events, _raw, err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert (ev["output_removed"], ev["output_kept"], ev["output_failed"]) == (4, 2, 0)
    assert "削除 4 件／対象外として残したファイル 2 件" in err

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
    events, _raw, _err = _events(capsys)
    assert next(e for e in events if e["event"] == "purged")["output_kept"] == 2


def test_without_include_output_keeps_outputs(tmp_path, capsys, monkeypatch):
    """既定（--include-output なし）は出力に一切触れない。

    workdir 側の削除件数は人が読む1行として（端末なら）stderr に出る
    （セキュリティレビューの指摘・--include-output と同じ規律。issue #144 で
    stdout の JSON Lines 契約を破らないよう stderr へ移し、続く指摘で
    isatty のときだけ出す形にした）ため、端末相当にすれば「削除」という語
    自体は出力側に触れていなくても err に現れる。ここで見るのは output
    固有のキー・文言が無いことだけに絞る。
    """
    _force_tty(monkeypatch)
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--yes") == 0
    assert wd.exists()                                       # keep-list 方式（#83）
    assert not (wd / "intermediate.sqlite").exists()
    assert len(list(out.iterdir())) == len(TOOL_FILES) + len(KEEP_FILES)

    events, raw, err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert "output_removed" not in ev and "対象外として残したファイル" not in raw
    assert ev["cred_kept"] is False                          # cred.dpapi は元々無い
    assert ev["removed"] == 1 and ev["failed"] == 0          # intermediate.sqlite の1件
    # issue #108 PM決定 (b): 許可リスト方式になり、残した件数（0件でも）が
    # 同じ1行に載る（issue #144: この1行は stderr）
    assert "中間データ 1 件を削除し、ツールが作ったものではない 0 件は残した" \
        "（資格情報は無かった）" in err


def test_requires_yes_even_with_include_output(tmp_path):
    """--yes なしは拒否（TR-G5 と同型）——出力側の追加でも明示操作の要求は同じ。"""
    cfg, out, wd = _setup(tmp_path)
    assert _purge(cfg, "--include-output") == 1
    assert wd.exists()
    assert len(list(out.iterdir())) == len(TOOL_FILES) + len(KEEP_FILES)


def test_missing_output_dir_is_not_an_error(tmp_path, capsys, monkeypatch):
    """出力先が無い／空でも件数0で正常終了する（未実行の workdir を消すだけの用途）。"""
    _force_tty(monkeypatch)
    cfg, out, _wd = _setup(tmp_path, tool_files=(), keep_files=())
    out.rmdir()
    assert _purge(cfg, "--yes", "--include-output") == 0

    events, _raw, err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert (ev["output_removed"], ev["output_kept"]) == (0, 0)
    assert "削除 0 件／対象外として残したファイル 0 件" in err


# ========== issue #83: 資格情報 cred.dpapi を purge から守る（keep-list 方式） ==========

def test_purge_keeps_credentials_but_removes_intermediate_data(tmp_path, capsys):
    """cred.dpapi と複数種の中間データが両方ある場合、cred.dpapi だけ残る。"""
    cfg, _out, wd = _setup(tmp_path)
    cred = wd / cred_store.blob_name()
    cred.write_bytes(b"dummy")
    # intermediate.sqlite（_setup 由来）に加え、他の中間データの種類も用意する。
    # keep-list は種類を列挙しないので、新顔の中間データでも同様に消えるはず
    for sub in ("pages", "editor_pages", "detect_frames_pages"):
        d = wd / sub
        d.mkdir()
        (d / "0001.png").write_text("x", encoding="utf-8")

    assert _purge(cfg, "--yes") == 0

    assert wd.exists()                       # workdir 自体は keep-list のため残る
    assert cred.exists()                     # 資格情報は残る（#83 の本題）
    assert not (wd / "intermediate.sqlite").exists()
    for sub in ("pages", "editor_pages", "detect_frames_pages"):
        assert not (wd / sub).exists()

    events, _raw, _err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert ev["cred_kept"] is True
    # 資格情報の中身・絶対パスは出さない（値は "dummy" を含まず、パスは
    # workdir のルートのみ）——stdout・stderr のどちらにも漏れていないことを見る
    assert "dummy" not in _raw and "dummy" not in _err
    assert str(cred) not in _raw and str(cred) not in _err


def test_purge_missing_workdir_is_not_an_error(tmp_path):
    """workdir が最初から無くても例外を出さず正常終了する（既存挙動の維持）。"""
    cfg, _out, wd = _setup(tmp_path)
    shutil.rmtree(wd)
    assert not wd.exists()
    assert _purge(cfg, "--yes") == 0
    assert not wd.exists()                   # 何も作られない


def test_purge_without_credentials_still_removes_intermediate_data(tmp_path, capsys):
    """cred.dpapi が無い（環境変数 GOOGLE_APPLICATION_CREDENTIALS 運用）場合でも
    例外にならず、中間データは通常どおり消える。"""
    cfg, _out, wd = _setup(tmp_path)
    assert not (wd / cred_store.blob_name()).exists()

    assert _purge(cfg, "--yes") == 0

    assert wd.exists()
    assert not (wd / "intermediate.sqlite").exists()
    events, _raw, _err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert ev["cred_kept"] is False
def test_purge_clears_readonly_file_via_chmod_retry(tmp_path, capsys, monkeypatch):
    """読み取り専用ファイルは chmod で書き込み許可を復元して削除する
    （セキュリティ表層レビュー担当/セキュリティレビューの指摘）。ロックではなく読み取り専用属性だけの場合は
    chmod で解除できるため、failed=0（削除できた側）に固定する。

    ファイル名は中間データの命名（WAL ファイル）に揃える——issue #108 の
    other_items 拒否（このツールが作ったと分からない名前が1件でもあれば
    削除しない）に引っかからないようにするため。
    """
    _force_tty(monkeypatch)
    cfg, _out, wd = _setup(tmp_path)
    ro = wd / "intermediate.sqlite-wal"
    ro.write_text("x", encoding="utf-8")
    ro.chmod(stat.S_IREAD)
    try:
        assert _purge(cfg, "--yes") == 0
    finally:
        if ro.exists():
            ro.chmod(stat.S_IWRITE)  # pytest の tmp_path クリーンアップ対策
    assert not ro.exists()
    assert not (wd / "intermediate.sqlite").exists()

    events, _raw, err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert ev["failed"] == 0
    assert ev["removed"] == 2  # intermediate.sqlite-wal + intermediate.sqlite
    assert "中間データ 2 件を削除し、ツールが作ったものではない 0 件は残した" in err


def test_purge_removes_junction_link_but_keeps_target_contents(tmp_path, capsys):
    """workdir 配下のジャンクションはリンク自体だけ外し、リンク先の中身は残す
    （セキュリティ表層レビュー担当/セキュリティレビューの指摘）。rmtree をリンクへ渡すとリンク先ごと消えるので、
    ここで「リンク先が無事」であることを固定する。

    リンク名は中間データが実際に使うディレクトリ名（"aligned"）に揃える——
    issue #108 の other_items 拒否（このツールが作ったと分からない名前が
    1件でもあれば削除しない）に引っかからないようにするため。
    """
    cfg, _out, wd = _setup(tmp_path)
    real_dir = tmp_path / "outside_target"
    real_dir.mkdir()
    (real_dir / "should_survive.txt").write_text("x", encoding="utf-8")

    link = wd / "aligned"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real_dir)],
        capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        pytest.skip(f"この環境ではジャンクションを作成できない: {result.stderr}")

    assert _purge(cfg, "--yes") == 0

    assert not link.exists()                                  # リンクは消える
    assert real_dir.exists()                                  # リンク先は無傷
    assert (real_dir / "should_survive.txt").exists()
    assert not (wd / "intermediate.sqlite").exists()           # 通常の中間データは消える
    assert wd.exists()

    events, _raw, _err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert ev["failed"] == 0
    assert ev["removed"] == 2  # aligned（リンク自体）+ intermediate.sqlite


def test_purge_refuses_when_workdir_itself_is_a_junction(tmp_path, capsys):
    """workdir 自体が reparse point の場合は何も消さず rc=2（セキュリティ表層レビュー担当/セキュリティレビューの指摘・
    issue #108 で unsafe_root の1種として purge_refused イベントに統合）。

    wd.iterdir() は reparse point 越しにリンク先を列挙してしまう
    （コーディネーター実測・junction_probe.py）ため、削除前にここで弾く。
    ミューテーション: cmd_purge の reparse point 検査を外すと、このテストは
    「リンク先の中身が消えている」側で赤くなる。
    """
    real_dir = tmp_path / "real_target"
    real_dir.mkdir()
    (real_dir / "keep_me.sqlite").write_text("x", encoding="utf-8")

    wd_link = tmp_path / "wd"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(wd_link), str(real_dir)],
        capture_output=True, text=True, errors="replace")
    if result.returncode != 0:
        pytest.skip(f"この環境ではジャンクションを作成できない: {result.stderr}")
    assert wd_link.is_dir()

    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"output_dir": str(tmp_path / "out"),
                               "workdir": str(wd_link),
                               "log_dir": str(tmp_path / "logs")}),
                   encoding="utf-8")

    assert _purge(cfg, "--yes") == 2
    assert (real_dir / "keep_me.sqlite").exists()             # リンク先は無傷
    assert wd_link.is_dir()                                    # リンク自体も残る（拒否のみ）

    events, _raw, _err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purge_refused")
    assert ev["reason"] == "unsafe_root" and ev["unsafe_reason"] == "reparse_point"


def test_purge_does_not_keep_a_symlink_named_cred_dpapi(tmp_path, capsys):
    """cred.dpapi という名前でも symlink なら資格情報として残さない
    （実体ではなく偽装されうるため信用しない・reparse point 判定を
    名前一致より先に見る・セキュリティ表層レビュー担当指摘 (e)）。

    ファイル symlink の作成には Windows で管理者権限または開発者モードが
    要ることがある——作れない環境では意味のある検証にならないため skip する
    （test_paths.py の symlink テストと同じ流儀）。
    """
    cfg, _out, wd = _setup(tmp_path)
    real_file = tmp_path / "real_secret.bin"
    real_file.write_bytes(b"not-a-real-cred")
    fake_cred = wd / cred_store.blob_name()
    try:
        fake_cred.symlink_to(real_file)
    except OSError:
        pytest.skip("この環境ではファイル symlink を作成できない（権限不足）")

    assert _purge(cfg, "--yes") == 0

    assert not fake_cred.exists()   # リンクは keep されず消える
    assert real_file.exists()       # リンク先の実ファイルは無傷

    events, _raw, _err = _events(capsys)
    ev = next(e for e in events if e["event"] == "purged")
    assert ev["cred_kept"] is False
