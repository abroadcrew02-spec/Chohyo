"""吸着 ON/OFF の読取値差分を数える診断（issue #75 (f)・FR-F40・AC-F39・Unit C）。

合成 token だけで組み立てる L1 テスト——実画像も保存済み応答も要らない
（`test_diag_overflow.py` と同じ流儀）。出荷テンプレート `chouhyo-v1` の
back/detail は行の高さ 100px・行送り 104px・行間隙 4px なので、行と行の隙間
（y=193〜196）に置いた文字は吸着 OFF ではどの欄にも入らず、ブロックを +3px
動かすと直上の行に入る——「ブロックが動いたことで読取値が変わる」状況を
実画像なしで作れる。

固定するのは4点:

1. 座標が動いても値が変わらなければ差分 0（動いた事実だけでは差分にしない）
2. 値が変わる欄は件数と場所（page_id・出力列名）で出て、**記入値は出さない**
3. 吸着 ON の記録が無い workdir・位置合わせ方式の違う workdir は拒否する
4. **中間データが 1 バイトも変わらない**（AC-F39 の核心。原本の sha256 と
   更新時刻、workdir のファイル一覧を実行前後で比べる）
"""
from __future__ import annotations

import hashlib
import json

import pytest

from chouhyo_ocr.align import ALGO_VERSION, geometry_hash
from chouhyo_ocr.align import template_hash as tpl_hash_of
from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.snap_diff import diff_event
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(not TPL.exists(), reason="出荷テンプレートが無い環境")

PAGE = "a_p0001"


@pytest.fixture()
def template():
    return load_template(TPL)


def _cfg(tmp_path) -> Config:
    return Config(workdir=str(tmp_path / "wd"), output_dir=str(tmp_path / "out"),
                  log_dir=str(tmp_path / "logs"))


def _amount_cell(template, row_no: int):
    """back/detail の block1 にある金額欄（比較対象に使う 1 列）。"""
    return next(c for c in template.cells
                if c.face_id == "back" and c.block_idx == 1
                and c.table_id == "detail" and c.row_no == row_no
                and c.field_id.endswith("金額"))


def _build(tmp_path, template, *, tokens, back_dy=(0, 3), snap_enabled=1,
           algo=None, applied=True) -> Config:
    """吸着 ON で読み取った体の workdir を合成する（画像も応答も作らない）。"""
    cfg = _cfg(tmp_path)
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    geo, tpl_hash = geometry_hash(raw), tpl_hash_of(raw)
    with Store(f"{cfg.workdir}/intermediate.sqlite") as store:
        store.upsert_page(PAGE, "a.png", 1, "done")
        store.set_template_hash(PAGE, tpl_hash)
        store.replace_tokens(PAGE, tokens)
        for face_id, dys in (("front", (0, 0)), ("back", tuple(back_dy))):
            transform = {"angle": 0.0, "dx": 0, "dy": 0, "matched": 12,
                         "snap": {"v": 1, "applied": applied, "dy": list(dys)}}
            store.upsert_alignment(
                PAGE, face_id, transform, True, geo, algo or ALGO_VERSION,
                template_hash=tpl_hash, snap_enabled=snap_enabled,
                snap_px=float(max(abs(d) for d in dys)),
                snap_detail=json.dumps({"applied": applied, "reason": "",
                                        "blocks": []}))
    return cfg


def _tok(seq: int, x: float, y: float, text: str = "1000"):
    """store.tokens() と同じ並び（seq, face, text, conf, x, y）。"""
    return (seq, "back", text, 0.99, float(x), float(y))


def _one_diff_tokens(template, *, text: str = "1000"):
    """「金額欄だけが変わる」token 2 個を作る。

    行 15 を先に埋めておくのが要点。空行のままだと、文字が 1 つ入った瞬間に
    その行が「空行ではない」へ変わり、記入の無い残り 4 列が "" から〓へ
    まとめて動く（判定表 #4）——差分は 5 件になり、狙った 1 列の変化が
    埋もれる。同じ行の品目欄を欄の中央で埋めておけば、動くのは金額欄だけ。
    """
    amount = _amount_cell(template, 15)
    name = next(c for c in template.cells
                if c.face_id == "back" and c.table_id == "detail"
                and c.row_no == 15 and c.field_id.endswith("品目"))
    return [_tok(0, name.rect.x + 40, name.rect.y + 50, "あ"),
            _tok(1, amount.rect.x + 40, amount.rect.y + amount.rect.h + 2, text)]


def test_moved_block_without_value_change_counts_no_diff(tmp_path, template):
    """座標が動いても、どの文字も別の欄へ移らなければ差分 0。

    「ブロックが動いた」ことと「読取値が変わった」ことは別で、後者だけを
    数える——動いた件数を差分として出すと、吸着を入れた瞬間に全ページが
    差分になり、人が見るべき欄が埋もれる。
    """
    cell = _amount_cell(template, 15)
    deep = _tok(0, cell.rect.x + 40, cell.rect.y + 50)  # 欄の中央（3px では出ない）
    cfg = _build(tmp_path, template, tokens=[deep])
    ev = diff_event(TPL, cfg)
    assert ev["ok"] is True
    assert (ev["pages"], ev["pages_snapped"], ev["diff_cells"]) == (1, 1, 0)
    assert ev["diffs"] == [] and ev["truncated"] is False
    assert ev["cells_compared"] > 0


def test_one_cell_changes_when_the_block_moves(tmp_path, template):
    """行間隙に落ちていた文字が、ブロックを 3px 動かすと直上の行へ入る。

    行の高さ 100px・行送り 104px なので、y=195 は行 15（93〜193）の下・
    行 16（197〜297）の上のどちらにも属さない。ブロックを +3px 動かすと
    行 15 が 96〜196 になり、この文字だけが行 15 の金額欄へ入る。
    """
    cfg = _build(tmp_path, template, tokens=_one_diff_tokens(template))
    ev = diff_event(TPL, cfg)
    assert ev["ok"] is True
    assert ev["diff_cells"] == 1, ev["diffs"]
    assert ev["diffs"][0]["page_id"] == PAGE
    assert ev["diffs"][0]["column"].endswith("金額")


def test_the_output_never_carries_the_written_values(tmp_path, template):
    """差分の一覧に載るのは場所だけ。**読み取った文字は 1 つも出さない。**

    stdout は GUI のログへ中継されうる（NFR-F05 の秘匿対象はログと外部送信
    だが、診断の一覧が記入値を持ち回る必要は無い）。件数と欄名で目視の
    当たりは付けられるので、値は持たせない。
    """
    cfg = _build(tmp_path, template,
                 tokens=_one_diff_tokens(template, text="7654321"))
    ev = diff_event(TPL, cfg)
    assert [set(d) for d in ev["diffs"]] == [{"page_id", "column"}]
    assert "7654321" not in json.dumps(ev, ensure_ascii=False)


def test_refused_when_no_snap_was_recorded(tmp_path, template):
    """吸着 OFF で読み取った workdir には比べる相手が無い（拒否条件 1）。

    画像から吸着をやり直せば ON 側は作れるが、それは `run` と同じコストで
    「保存済み token に対して再計算」という FR-F40 の前提から外れる。
    """
    cell = _amount_cell(template, 15)
    cfg = _build(tmp_path, template, tokens=[_tok(0, cell.rect.x + 40, cell.rect.y + 50)],
                 snap_enabled=0, back_dy=(0, 0), applied=False)
    ev = diff_event(TPL, cfg)
    assert (ev["ok"], ev["reason"]) == (False, "no_snap_recorded")
    assert "snap_blocks" in ev["error"]


def test_refused_when_the_alignment_method_differs(tmp_path, template):
    """旧方式で作った中間データは拒否する（拒否条件 3）。

    方式の違いを吸着の差として読むと判断を誤る——ON/OFF の差だけを見せる
    ためのコマンドなので、材料の出所が違う時点で実行しない。
    """
    cell = _amount_cell(template, 15)
    cfg = _build(tmp_path, template,
                 tokens=[_tok(0, cell.rect.x + 40, cell.rect.y + 50)],
                 algo=f"{ALGO_VERSION}-old")
    ev = diff_event(TPL, cfg)
    assert (ev["ok"], ev["reason"]) == (False, "algo_version_mismatch")
    assert "run" in ev["error"]


def test_no_vision_client_is_created(tmp_path, template, monkeypatch):
    """AC-F39: Vision API 呼び出し 0 回。

    送信カウンタを外から数える代わりに、**送信の入口を壊してから実行する**。
    `snap-diff` は `OcrClient` を受け取る引数を持たないので、内部で作ろうと
    した瞬間だけがここで落ちる。
    """
    from chouhyo_ocr import vision_client

    def boom(*a, **k):
        raise AssertionError("snap-diff が Vision の送信口を作った")

    monkeypatch.setattr(vision_client, "RealVisionClient", boom)
    cfg = _build(tmp_path, template, tokens=_one_diff_tokens(template))
    assert diff_event(TPL, cfg)["ok"] is True


def test_no_store_is_refused_not_crashed(tmp_path):
    """中間データがまだ無い workdir では、例外ではなく理由付きで戻る。"""
    ev = diff_event(TPL, _cfg(tmp_path))
    assert (ev["ok"], ev["reason"]) == (False, "no_store")


def test_intermediate_data_is_not_modified(tmp_path, template):
    """AC-F39 の核心: 実行前後で中間データが 1 バイトも変わらない。

    `Store.__init__` は `_ensure_column` で `ALTER TABLE` を打つため、原本を
    直接開くだけで行の形が変わる。複製してから開いていることを、原本の
    sha256・更新時刻・workdir のファイル一覧で確かめる。
    """
    from pathlib import Path
    cfg = _build(tmp_path, template, tokens=_one_diff_tokens(template))
    wd = Path(cfg.workdir)
    db = wd / "intermediate.sqlite"

    def snapshot():
        return (hashlib.sha256(db.read_bytes()).hexdigest(),
                db.stat().st_mtime_ns,
                sorted(p.name for p in wd.iterdir()))

    before = snapshot()
    ev = diff_event(TPL, cfg)
    assert ev["ok"] is True and ev["diff_cells"] == 1
    assert snapshot() == before
