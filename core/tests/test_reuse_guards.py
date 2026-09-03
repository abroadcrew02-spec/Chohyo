"""中間データ再利用の歯止め（issue #25・D-26）と重複行出力（#29 B-2・D-27）。

不変条件: 出力は、その出力を組み立てたテンプレートと同一のテンプレートで
作られた中間データからのみ生成する。run はテンプレ変更後の再送を明示
オプトインなしに行わない（要配慮個人情報の再開示・課金をツールが自発しない）。
"""
import json
import shutil

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import OperationRefused, remap, render, run
from chouhyo_ocr.vision_client import ReplayClient

RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
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


def setup_done(tmp_path, cfg):
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    run(inp, TPL, cfg, ReplayClient(resp))
    return inp, resp


def geo_changed(tmp_path):
    t = json.loads(TPL.read_text(encoding="utf-8"))
    t["faces"][0].setdefault("exclusions", []).append(
        {"id": "added", "rect": {"x": 5, "y": 5, "w": 10, "h": 10}})
    p = tmp_path / "geo.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return p


def field_moved(tmp_path):
    t = json.loads(TPL.read_text(encoding="utf-8"))
    t["faces"][0]["fields"][0]["rect"]["x"] += 5
    p = tmp_path / "moved.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return p


def test_render_rejects_geometry_change(tmp_path):
    """幾何変更後の render は出力を作らず拒否する（#25 の実証シナリオ）。"""
    cfg = make_cfg(tmp_path)
    setup_done(tmp_path, cfg)
    with pytest.raises(OperationRefused, match="run"):
        render(geo_changed(tmp_path), cfg, timestamp="g")
    assert not list((tmp_path / "out").glob("output_g.*"))  # 1バイトも書かない


def test_render_rejects_nongeometry_change_and_names_remap(tmp_path):
    """欄の矩形だけ動かした場合も拒否し、remap を名指しする。"""
    cfg = make_cfg(tmp_path)
    setup_done(tmp_path, cfg)
    tpl2 = field_moved(tmp_path)
    with pytest.raises(OperationRefused, match="remap"):
        render(tpl2, cfg, timestamp="m")
    # remap → render で通る（正しい復旧経路）
    assert remap(tpl2, cfg) == 1
    xlsx, csvp, rows = render(tpl2, cfg, timestamp="m2")
    assert rows[0].status == "正常"


def test_run_preflight_stops_before_any_api_call(tmp_path):
    """テンプレ変更後の run は annotate を1回も呼ばずに中止する。"""
    cfg = make_cfg(tmp_path)
    inp, resp = setup_done(tmp_path, cfg)
    client = CountingReplay(resp)
    with pytest.raises(OperationRefused, match="ページ"):
        run(inp, geo_changed(tmp_path), cfg, client)
    assert client.calls == 0


def test_run_optin_resends_only_stale_pages(tmp_path):
    """--resend-on-template-change 相当のオプトインで不一致ページのみ再送される。"""
    cfg = make_cfg(tmp_path)
    inp, resp = setup_done(tmp_path, cfg)
    client = CountingReplay(resp)
    summary = run(inp, geo_changed(tmp_path), cfg, client,
                  resend_on_template_change=True)
    assert client.calls == 1  # 1ページが降格→再送
    assert summary.rows == 1
    # 再度同じテンプレで run → 一致しているので再送されない（§8-7）
    client2 = CountingReplay(resp)
    run(inp, geo_changed(tmp_path), cfg, client2)
    assert client2.calls == 0


def test_duplicate_inputs_produce_skip_rows(tmp_path):
    """同内容・別名の2ファイル → 2行（正常＋スキップ（重複））・送信1回（D-27）。"""
    cfg = make_cfg(tmp_path)
    inp = tmp_path / "input"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(PAGE_PNG, inp / "b.png")  # 同一内容・別名
    shutil.copy(RESP, resp / "a_p0001.json")
    client = CountingReplay(resp)
    run(inp, TPL, cfg, client)
    assert client.calls == 1  # 重複は送信しない（§5.1 Could の趣旨維持）

    _x, _c, rows = render(TPL, cfg, timestamp="dup")
    assert len(rows) == 2  # 入力ページ数＝出力行数（§3.4）
    by_file = {r.source_file: r for r in rows}
    assert by_file["a.png"].status == "正常"
    dup = by_file["b.png"]
    assert dup.status == "スキップ（重複）"
    assert all(v == "〓" for v in dup.values)
    assert dup.unclear_count == len(dup.values)

    # 再実行しても「スキップ（重複）」は送信対象にならない（PM 条件B・§5.8）
    client2 = CountingReplay(resp)
    run(inp, TPL, cfg, client2)
    assert client2.calls == 0
