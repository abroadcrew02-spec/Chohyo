"""位置合わせの再利用（#45）と入力ファイル改名時の行数保存（#46）。

不変条件:
- 未送信ページの位置合わせは run をまたいで作り直さない。ただし
  geometry_hash / algo_version / 整列画像 のどれかが欠けたら必ず作り直す
  （古い定義を使い回して誤った値を出さない）
- 入力ページ数＝出力行数（要件 §3.4）は、入力ファイルを改名しても破れない
"""
import json
import shutil

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import render, run
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答が無い環境")


class CountingReplay(ReplayClient):
    """送信回数と、実際に送った PNG を記録する再生クライアント。"""

    def __init__(self, resp_dir):
        super().__init__(resp_dir)
        self.calls = 0
        self.sent: dict[str, bytes] = {}

    def annotate(self, png, page_id):
        self.calls += 1
        self.sent[page_id] = png
        return super().annotate(png, page_id)


class AlignCounter:
    """pipeline.align_page の呼び出しを数える（本物へ委譲する）。"""

    def __init__(self, monkeypatch):
        from chouhyo_ocr import align as align_mod
        from chouhyo_ocr import pipeline as pipeline_mod
        self.calls = 0
        real = align_mod.align_page

        def counted(img, template):
            self.calls += 1
            return real(img, template)

        monkeypatch.setattr(pipeline_mod, "align_page", counted)


def make_cfg(tmp_path, **kw) -> Config:
    return Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                  workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"),
                  **kw)


def put_pages(inp, resp, n: int) -> list[str]:
    """同じ紙面・別内容の入力を n 枚置く。

    中身が完全に同一だと二重投入検知に落ちるため、PNG の IEND より後ろに
    パディングを足して sha1 だけ変える（画素は同一・位置合わせ結果も同じ）。
    """
    raw = PAGE_PNG.read_bytes()
    names = []
    for i in range(1, n + 1):
        name = f"p{i:02d}"
        (inp / f"{name}.png").write_bytes(raw + b"\n" * i)
        shutil.copy(RESP, resp / f"{name}_p0001.json")
        names.append(name)
    return names


def geo_changed(tmp_path):
    t = json.loads(TPL.read_text(encoding="utf-8"))
    t["faces"][0].setdefault("exclusions", []).append(
        {"id": "added", "rect": {"x": 5, "y": 5, "w": 10, "h": 10}})
    p = tmp_path / "geo.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return p


# --- #45: 位置合わせの再利用 ---

def test_aligned_page_is_not_realigned(tmp_path, monkeypatch):
    """送信上限で止まったページは、次の run で位置合わせをやり直さない。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 1)
    cfg = make_cfg(tmp_path, send_limit=0)

    first = AlignCounter(monkeypatch)
    run(inp, TPL, cfg, CountingReplay(resp))
    assert first.calls == 1

    second = AlignCounter(monkeypatch)
    run(inp, TPL, cfg, CountingReplay(resp))
    assert second.calls == 0  # 受入基準1


def test_split_send_does_not_realign_remaining_pages(tmp_path, monkeypatch):
    """分割送信の2回目は、残りページを再整列しない（受入基準2の縮小版）。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 4)
    cfg = make_cfg(tmp_path, send_limit=1)

    first = AlignCounter(monkeypatch)
    c1 = CountingReplay(resp)
    run(inp, TPL, cfg, c1)
    assert (first.calls, c1.calls) == (4, 1)  # 4枚整列・1枚送信

    second = AlignCounter(monkeypatch)
    c2 = CountingReplay(resp)
    summary = run(inp, TPL, cfg, c2)
    assert second.calls == 0
    assert c2.calls == 1        # 送信は従来どおり進む（整列だけを飛ばす）
    assert summary.rows == 4    # 入力ページ数＝出力行数


def test_geometry_change_forces_realign(tmp_path, monkeypatch):
    """幾何セクションが変わったら再利用しない（受入基準3）。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 1)
    cfg = make_cfg(tmp_path, send_limit=0)
    run(inp, TPL, cfg, CountingReplay(resp))

    second = AlignCounter(monkeypatch)
    run(inp, geo_changed(tmp_path), cfg, CountingReplay(resp))
    assert second.calls == 1


def test_algo_version_change_forces_realign(tmp_path, monkeypatch):
    """位置合わせ方式の版が上がったら再利用しない（受入基準3）。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 1)
    cfg = make_cfg(tmp_path, send_limit=0)
    run(inp, TPL, cfg, CountingReplay(resp))

    from chouhyo_ocr import align as align_mod
    monkeypatch.setattr(align_mod, "ALGO_VERSION", "999")
    second = AlignCounter(monkeypatch)
    run(inp, TPL, cfg, CountingReplay(resp))
    assert second.calls == 1


def test_missing_aligned_image_forces_realign(tmp_path, monkeypatch):
    """整列画像を手で消したら再利用しない（受入基準4）。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 1)
    cfg = make_cfg(tmp_path, send_limit=0)
    run(inp, TPL, cfg, CountingReplay(resp))

    pngs = sorted((tmp_path / "wd" / "aligned").glob("*.png"))
    assert pngs
    pngs[0].unlink()
    second = AlignCounter(monkeypatch)
    run(inp, TPL, cfg, CountingReplay(resp))
    assert second.calls == 1


def test_reuse_produces_identical_send_and_output(tmp_path, monkeypatch):
    """再利用しても送信画像・出力行が「再利用しない場合」と一致する（受入基準5）。"""
    # A: 1回目で上限に当てて整列だけ済ませ、2回目に再利用して送信する
    a = tmp_path / "a"; a.mkdir()
    inp_a = a / "input"; inp_a.mkdir()
    resp_a = a / "resp"; resp_a.mkdir()
    put_pages(inp_a, resp_a, 1)
    run(inp_a, TPL, make_cfg(a, send_limit=0), CountingReplay(resp_a))
    counter = AlignCounter(monkeypatch)
    cfg_a = make_cfg(a, send_limit=5)
    client_a = CountingReplay(resp_a)
    run(inp_a, TPL, cfg_a, client_a)
    assert counter.calls == 0  # 再利用した経路であることを確かめてから比べる
    _xa, _ca, rows_a = render(TPL, cfg_a, timestamp="a")

    # B: 一度で整列から送信まで通す（再利用なし）
    b = tmp_path / "b"; b.mkdir()
    inp_b = b / "input"; inp_b.mkdir()
    resp_b = b / "resp"; resp_b.mkdir()
    put_pages(inp_b, resp_b, 1)
    cfg_b = make_cfg(b, send_limit=5)
    client_b = CountingReplay(resp_b)
    run(inp_b, TPL, cfg_b, client_b)
    _xb, _cb, rows_b = render(TPL, cfg_b, timestamp="b")

    assert client_a.sent and client_a.sent.keys() == client_b.sent.keys()
    for pid, png in client_a.sent.items():
        assert png == client_b.sent[pid]          # 送信画像がバイト一致
    assert [r.values for r in rows_a] == [r.values for r in rows_b]
    assert [r.status for r in rows_a] == [r.status for r in rows_b]
    assert [r.unclear_count for r in rows_a] == [r.unclear_count for r in rows_b]


# --- #46: 改名しても1ページ＝1行 ---

def test_renamed_input_keeps_one_row(tmp_path):
    """改名して再実行しても行数・要確認セル数が変わらず、再送信も起きない。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "scan_0001.png")
    shutil.copy(RESP, resp / "scan_0001_p0001.json")
    cfg = make_cfg(tmp_path)

    first = run(inp, TPL, cfg, CountingReplay(resp))
    assert (first.pages, first.rows) == (1, 1)

    (inp / "scan_0001.png").rename(inp / "2026-08分.png")
    events = []
    client = CountingReplay(resp)
    second = run(inp, TPL, cfg, client, progress=events.append)

    assert second.rows == 1                              # 受入基準1
    assert second.unclear_total == first.unclear_total   # 受入基準2
    assert client.calls == 0                             # 受入基準3
    assert not [e for e in events if e.get("event") == "skip_duplicate"]
    assert not [e for e in events if e.get("event") == "stale_pages"]  # 受入基準6
    renamed = [e for e in events if e.get("event") == "source_renamed"]
    assert renamed and renamed[0]["was"] == "scan_0001.png"

    _x, _c, rows = render(TPL, cfg, timestamp="r")
    assert len(rows) == 1
    assert rows[0].source_file == "2026-08分.png"        # 受入基準4
    assert rows[0].status == "正常"


def test_renamed_input_is_still_resumable(tmp_path):
    """改名の直後にもう一度 run しても、重複扱いや再送信に化けない。"""
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "scan_0001.png")
    shutil.copy(RESP, resp / "scan_0001_p0001.json")
    cfg = make_cfg(tmp_path)
    run(inp, TPL, cfg, CountingReplay(resp))
    (inp / "scan_0001.png").rename(inp / "renamed.png")
    run(inp, TPL, cfg, CountingReplay(resp))

    events = []
    client = CountingReplay(resp)
    third = run(inp, TPL, cfg, client, progress=events.append)
    assert third.rows == 1
    assert client.calls == 0
    assert not [e for e in events if e.get("event") in
                ("skip_duplicate", "stale_pages", "source_renamed")]


def tables_changed(tmp_path):
    """罫線定義（tables）だけを動かしたテンプレート。

    geometry_hash は render_dpi/image/record/faces.{face_id,source,exclusions}
    しか見ないため tables を直しても変わらないが、estimate_shift のアンカーは
    tables（blocks.origin / row_pitch / columns）から作られるので位置合わせ
    結果は変わる——再利用ゲートが取りこぼすと、旧アンカーで求めた dx/dy の
    まま新しいセル定義で割り付けることになる。
    """
    t = json.loads(TPL.read_text(encoding="utf-8"))
    for blk in t["faces"][0]["tables"][0]["blocks"]:
        blk["origin"]["y"] -= 5
    p = tmp_path / "tables.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return p


def test_table_change_forces_realign(tmp_path, monkeypatch):
    """罫線定義だけの変更でも再利用しない（レビュー4巡目 HIGH）。"""
    from chouhyo_ocr.align import geometry_hash, template_hash
    changed = tables_changed(tmp_path)
    raw_old = json.loads(TPL.read_text(encoding="utf-8"))
    raw_new = json.loads(changed.read_text(encoding="utf-8"))
    # 前提: geometry_hash では捕まらない変更であること
    assert geometry_hash(raw_old) == geometry_hash(raw_new)
    assert template_hash(raw_old) != template_hash(raw_new)

    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 1)
    cfg = make_cfg(tmp_path, send_limit=0)
    run(inp, TPL, cfg, CountingReplay(resp))

    second = AlignCounter(monkeypatch)
    run(inp, changed, cfg, CountingReplay(resp))
    assert second.calls == 1


def test_resend_on_template_change_realigns_every_page(tmp_path, monkeypatch):
    """テンプレ変更の再送では、done も未送信も同じアンカーで整列し直す。

    stale_done_pages が降格するのは done ページだけなので、再利用ゲートが
    tables を見ていないと「降格＝新アンカー」「aligned のまま＝旧アンカー」が
    同一 run に混ざる。どちらで出た行かは出力にもイベントにも現れない。
    """
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    put_pages(inp, resp, 2)
    cfg = make_cfg(tmp_path, send_limit=1)
    run(inp, TPL, cfg, CountingReplay(resp))  # 1枚 done・1枚 aligned

    second = AlignCounter(monkeypatch)
    run(inp, tables_changed(tmp_path), cfg, CountingReplay(resp),
        resend_on_template_change=True)
    assert second.calls == 2


def test_rename_over_duplicate_placeholder_does_not_resend(tmp_path):
    """重複スキップの空行が残る名前へ改名しても、再送信せず1行になる。

    run1 で a.png（正本）と b.png（重複）を投入 → b.png には「スキップ
    （重複）」の全〓行だけが残る。そこから a.png を消して b.png だけで
    再実行するのは「a を b に改名した」のと同じ状態なので、空行を捨てて
    中間データを付け替える（レビュー4巡目 MEDIUM: 従来は付け替えられず
    通常処理へ落ちて api=1・『正常』行が2行並んでいた）。
    """
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    raw = PAGE_PNG.read_bytes()
    (inp / "a.png").write_bytes(raw)
    (inp / "b.png").write_bytes(raw)          # 中身まで同一＝二重投入
    for name in ("a", "b"):
        shutil.copy(RESP, resp / f"{name}_p0001.json")
    cfg = make_cfg(tmp_path)

    first = run(inp, TPL, cfg, CountingReplay(resp))
    assert first.rows == 2                    # 正本1行＋スキップ（重複）1行
    _x, _c, rows1 = render(TPL, cfg, timestamp="d1")
    keep = [r for r in rows1 if r.status == "正常"]
    assert len(keep) == 1

    (inp / "a.png").unlink()                  # 正本を消す＝b.png へ改名した状態
    events = []
    client = CountingReplay(resp)
    second = run(inp, TPL, cfg, client, progress=events.append)
    assert second.rows == 1                   # 入力1ファイル＝1行
    assert client.calls == 0                  # 再送信（課金）なし
    assert not [e for e in events if e.get("event") == "rename_fallback"]

    _x, _c, rows2 = render(TPL, cfg, timestamp="d2")
    assert len(rows2) == 1
    assert rows2[0].source_file == "b.png"
    assert rows2[0].status == "正常"
    assert rows2[0].unclear_count == keep[0].unclear_count
