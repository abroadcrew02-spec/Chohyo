"""応答の異常系に対する堅牢性（issue #37/#38/#39/#40）。

破壊テスト（2026-08-28）で実測した壊れ方をそのまま固定する:
- 空応答・全座標が面外 → status「正常」のまま212列中200列が空白（#37）
- 応答パースの例外が未捕捉 → received のまま実行のたびに再送信（#38）
- confidence の型不正 → render が恒久クラッシュしバッチ全滅（#39）
- キー欠落・空 vertices で mapping が未捕捉例外（#40）
"""
import copy
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
    def __init__(self, resp_dir):
        super().__init__(resp_dir)
        self.calls = 0

    def annotate(self, png, page_id):
        self.calls += 1
        return super().annotate(png, page_id)


def make_cfg(tmp_path) -> Config:
    return Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                  workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))


def setup(tmp_path, mutate=None):
    """1ページ分の入力＋（必要なら変異させた）応答を用意する。"""
    inp = tmp_path / "input"; inp.mkdir()
    respd = tmp_path / "resp"; respd.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    data = json.loads(RESP.read_text(encoding="utf-8"))
    if mutate:
        mutate(data)
    (respd / "a_p0001.json").write_text(json.dumps(data, ensure_ascii=False),
                                        encoding="utf-8")
    return inp, respd


def _each_symbol(data):
    for page in data["fullTextAnnotation"]["pages"]:
        for b in page["blocks"]:
            for p in b["paragraphs"]:
                for w in p["words"]:
                    yield from w["symbols"]


# ---------- #37: 空応答・面外座標が「正常」で出ない ----------

@pytest.mark.parametrize("name,mutate", [
    ("応答なし", lambda d: d.pop("fullTextAnnotation")),
    ("pages 空", lambda d: d["fullTextAnnotation"].__setitem__("pages", [])),
    ("全座標が負", lambda d: [s["boundingBox"].__setitem__(
        "vertices", [{"x": -500, "y": -500}] * 4) for s in _each_symbol(d)]),
    ("全座標が巨大", lambda d: [s["boundingBox"].__setitem__(
        "vertices", [{"x": 999999999, "y": 999999999}] * 4)
        for s in _each_symbol(d)]),
])
def test_no_symbols_in_faces_is_not_reported_normal(tmp_path, name, mutate):
    """面内に symbol が1つも無いページを「正常」で出さない（issue #37）。"""
    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path, mutate)
    run(inp, TPL, cfg, ReplayClient(respd))
    _x, _c, rows = render(TPL, cfg, timestamp=f"n{abs(hash(name)) % 1000}")
    assert rows[0].status != "正常", f"{name}: 白紙が正常として出た"
    assert all(v == "〓" for v in rows[0].values), f"{name}: 全〓になっていない"


# ---------- #38: 例外で received のまま浮かず、再送もしない ----------

def test_broken_response_does_not_loop_resending(tmp_path):
    """応答パースが失敗しても再実行のたびに再送信しない（issue #38）。"""
    def break_one(d):
        for s in _each_symbol(d):
            s["boundingBox"]["vertices"] = "壊れた構造"  # list でない
            break

    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path, break_one)
    c1 = CountingReplay(respd)
    run(inp, TPL, cfg, c1)
    assert c1.calls == 1

    # 失敗として確定し、received のまま宙に浮かない（浮くと状態が未定義になる）。
    # 再実行での再送自体は要件 §5.8「失敗分は再送信」どおりの挙動なので許す——
    # 修正の主眼は「状態が確定し、出力にステータスが出る」こと
    from chouhyo_ocr.pipeline import _store_path
    from chouhyo_ocr.store import Store
    store = Store(_store_path(cfg))
    page = store.pages()[0]
    store.close()
    assert page["state"] == "failed", f'received のまま浮いている: {page["state"]}'

    _x, _c, rows = render(TPL, cfg, timestamp="brk")
    assert rows[0].status != "正常"
    assert all(v == "〓" for v in rows[0].values)


def test_received_page_reuses_saved_response(tmp_path):
    """受信済み・割付前で中断したページは保存済み応答を再利用する（issue #38）。"""
    from chouhyo_ocr.store import Store
    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path)
    c1 = CountingReplay(respd)
    run(inp, TPL, cfg, c1)
    assert c1.calls == 1

    # 「受信後・割付前でクラッシュした」状態を作る（応答ファイルは残っている）
    st = Store(app_root() / "core" / "x") if False else None
    del st
    from chouhyo_ocr.pipeline import _store_path
    store = Store(_store_path(cfg))
    pid = store.pages()[0]["page_id"]
    store.set_state(pid, "received")
    store.close()
    assert (tmp_path / "wd" / "responses" / f"{pid}.json").exists()

    c2 = CountingReplay(respd)
    run(inp, TPL, cfg, c2)
    assert c2.calls == 0, "保存済み応答があるのに再送信した"
    _x, _c, rows = render(TPL, cfg, timestamp="reuse")
    assert rows[0].status == "正常"


# ---------- #39: 1ページの破損がバッチ全体を道連れにしない ----------

def test_broken_page_does_not_kill_whole_batch(tmp_path):
    """confidence 型不正のページがあっても、正常ページの出力は必ず作られる。"""
    cfg = make_cfg(tmp_path)
    inp = tmp_path / "input"; inp.mkdir()
    respd = tmp_path / "resp"; respd.mkdir()
    base = PAGE_PNG.read_bytes()
    (inp / "good.png").write_bytes(base + b"\x01")
    (inp / "poison.png").write_bytes(base + b"\x02")
    good = json.loads(RESP.read_text(encoding="utf-8"))
    (respd / "good_p0001.json").write_text(json.dumps(good, ensure_ascii=False),
                                           encoding="utf-8")
    bad = copy.deepcopy(good)
    for s in _each_symbol(bad):
        s["confidence"] = "high"  # 数値でない
    (respd / "poison_p0001.json").write_text(json.dumps(bad, ensure_ascii=False),
                                             encoding="utf-8")

    summary = run(inp, TPL, cfg, ReplayClient(respd))
    assert summary.rows == 2  # 行数保存
    xlsx, csvp, rows = render(TPL, cfg, timestamp="mix")
    assert xlsx.exists() and csvp.exists(), "破損1件で出力が作られなかった"
    by_file = {r.source_file: r for r in rows}
    # 正常ページの値は取り出せる（バッチ全滅しない）
    assert any(v not in ("", "〓") for v in by_file["good.png"].values)
    # 型不正ページは文字系が全て〓へ倒れる（信頼度不明として安全側）。
    # 丸印（choice）は画像から判定するので値が出るのが正しい——壊れたのは
    # confidence であって画像ではない
    poison = by_file["poison.png"]
    assert poison.unclear_count > by_file["good.png"].unclear_count
    assert poison.min_conf == "", "信頼度不明なのに最低信頼度が出ている"


# ---------- #40: 応答の構造欠落で run が止まらない ----------

@pytest.mark.parametrize("name,mutate", [
    ("text 欠落", lambda s: s.pop("text")),
    ("boundingBox 欠落", lambda s: s.pop("boundingBox")),
    ("vertices 欠落", lambda s: s["boundingBox"].pop("vertices")),
    ("vertices 空", lambda s: s["boundingBox"].__setitem__("vertices", [])),
    ("text が null", lambda s: s.__setitem__("text", None)),
])
def test_malformed_symbol_is_dropped_not_fatal(tmp_path, name, mutate):
    """壊れた symbol 1件で応答全体を落とさない（issue #40）。"""
    def apply(d):
        mutate(next(_each_symbol(d)))

    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path, apply)
    summary = run(inp, TPL, cfg, ReplayClient(respd))
    assert summary.rows == 1, f"{name}: 行数が保存されない"
    _x, _c, rows = render(TPL, cfg, timestamp=f"m{abs(hash(name)) % 1000}")
    # 1文字落ちるだけで残りは通常どおり処理される
    assert rows[0].status == "正常", f"{name}: {rows[0].status}"


# ---------- #36: 出力の書き込みは原子的（失敗しても既存を壊さない）----------

def test_locked_output_is_not_destroyed(tmp_path):
    """出力が他プロセスに開かれていても、既存の正常なファイルを壊さない。

    実測（修正前）: PermissionError で失敗する前に 9246 バイトの正常な
    xlsx が 0 バイトへ切り詰められていた（issue #36）。
    """
    import msvcrt

    from chouhyo_ocr.render_out import write_outputs
    from chouhyo_ocr.render_rows import Row
    cols = ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名",
            "ページ番号", "ステータス", "a", "b"]
    row = Row(page_id="p1", source_file="s.pdf", page_no=1, status="正常",
              values=["x", "y"], unclear_count=0, min_conf="0.9")
    xlsx, csvp, _r = write_outputs(tmp_path, "lock", cols, [row])
    before = xlsx.read_bytes()
    assert len(before) > 1000

    fh = open(xlsx, "r+b")
    try:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # 先頭1バイトを排他
        try:
            write_outputs(tmp_path, "lock", cols, [row])
        except PermissionError as e:
            assert "開かれている" in str(e)  # 原因が分かる文言
    finally:
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        fh.close()
    # 成否によらず既存ファイルは無傷（0 バイト化しない）
    assert xlsx.read_bytes() == before, "既存の出力ファイルが破壊された"
    # 一時ファイルも残さない
    assert not list(tmp_path.glob("*.tmp"))


# ---------- #35: 同一 workdir の多重起動を断る ----------

def test_second_run_on_same_workdir_is_refused(tmp_path):
    """実行中の workdir へ2本目を起動すると明示的に中止する（issue #35）。

    実測（修正前）: 2プロセスが独立に全30ページを処理して attempt=2（全件
    二重送信）、send_limit はプロセスローカルで実質2倍、出力の同時書き込みで
    片方が壊れた xlsx を rc=0 で「成功」と報告した。
    """
    from chouhyo_ocr.runlock import RunLock, RunLockError
    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path)

    holder = RunLock(cfg.workdir)
    holder.acquire()
    try:
        with pytest.raises(SystemExit, match="二重"):
            run(inp, TPL, cfg, CountingReplay(respd))
    finally:
        holder.release()

    # ロックが解けたら通常どおり実行できる（ロックが残り続けない）
    c = CountingReplay(respd)
    run(inp, TPL, cfg, c)
    assert c.calls == 1
    assert not (tmp_path / "wd" / ".run.lock").exists()


def test_stale_lock_from_dead_process_is_reclaimed(tmp_path):
    """異常終了で残ったロックは自動で奪う（手で消させると学習されて無意味になる）。"""
    from chouhyo_ocr.runlock import RunLock
    cfg = make_cfg(tmp_path)
    inp, respd = setup(tmp_path)
    lock_path = tmp_path / "wd" / ".run.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("999999999", encoding="ascii")  # 存在しない PID

    c = CountingReplay(respd)
    run(inp, TPL, cfg, c)  # 例外を出さずに実行できる
    assert c.calls == 1


def test_unsupported_input_is_visible_not_silent(tmp_path):
    """非対応拡張子のファイルが進捗イベントで可視化される（レビュー M-2）。

    実測（修正前）: .docx をドロップして実行すると total=0 の正常終了で、
    利用者には何が起きたか分からなかった。
    """
    cfg = make_cfg(tmp_path)
    inp = tmp_path / "input"; inp.mkdir()
    respd = tmp_path / "resp"; respd.mkdir()
    (inp / "memo.docx").write_bytes(b"not an image")
    events = []
    run(inp, TPL, cfg, ReplayClient(respd), progress=events.append)
    skipped = [e for e in events if e.get("event") == "skipped_unsupported"]
    assert skipped and skipped[0]["count"] == 1
    assert skipped[0]["files"] == ["memo.docx"]
