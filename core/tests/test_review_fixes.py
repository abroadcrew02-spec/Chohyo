"""品質レビュー（2026-08-28）HIGH 指摘の再発防止テスト（issue #11/#13/#14）。

- #11: 金額正規化は normalize 属性で発火し、列名に依存しない（要件 §5.2）
- #13: glob メタ文字を含むファイル名の PDF が展開できる
- #14: config.json の不正値・未知キーは ConfigError で明示的に拒否される

#12/#15（エディタ）は TypeScript 側のため tsc ＋実機で確認する。
"""
import json

import pytest

from chouhyo_ocr.config import Config, ConfigError, load_config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import UNCLEAR, build_row
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"
CFG = Config(unclear_threshold=0.85, era_threshold=0.06)
RESP = (app_root() / "core" / "workdir" / "responses"
        / "帳票抽出検証用2026-08-24_p0001.json")
PAGE_PNG_EXISTS = (app_root() / "workdir" / "pages" / "sample-1.png").exists()


# ---------- #11: 金額正規化の発火条件 ----------

def _template_with(tmp_path, rename_to=None, drop_normalize=False):
    t = json.loads(TPL.read_text(encoding="utf-8"))
    for face in t["faces"]:
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                if c["name"] == "金額":
                    if drop_normalize:
                        c.pop("normalize", None)
                    if rename_to:
                        c["name"] = rename_to
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return load_template(p)


def _row_value(template, cells, colname):
    row = build_row(
        template,
        {"page_id": "p", "source_file": "s.png", "page_no": 1,
         "status": "", "unassigned_below_table": 0},
        cells, {}, CFG)
    cols = [oc for c in template.cells for oc in c.output_columns()]
    return row.values[cols.index(colname)]


def test_amount_fires_on_normalize_attr_not_column_name(tmp_path):
    """列名を「支払金額」へ改名しても normalize: amount があれば整数化される。"""
    template = _template_with(tmp_path, rename_to="支払金額")
    cells = {c.field_id: ("", None, c.kind, False) for c in template.cells}
    cells["detail_01_支払金額"] = ("1,234", 0.95, "text", False)
    assert _row_value(template, cells, "detail_01_支払金額") == 1234


def test_amount_does_not_fire_without_normalize_attr(tmp_path):
    """normalize が無ければ、列名が「金額」でも正規化しない（属性だけが契約）。"""
    template = _template_with(tmp_path, drop_normalize=True)
    cells = {c.field_id: ("", None, c.kind, False) for c in template.cells}
    cells["detail_01_金額"] = ("1,234", 0.95, "text", False)
    assert _row_value(template, cells, "detail_01_金額") == "1,234"


def test_shipped_template_declares_amount_normalize():
    """同梱テンプレートの金額列は normalize: amount を宣言している。"""
    template = load_template(TPL)
    amount_cells = [c for c in template.cells if c.normalize == "amount"]
    assert len(amount_cells) == 28  # 明細28行 × 金額1列
    assert all(c.field_id.endswith("金額") for c in amount_cells)


def test_normalize_ignored_on_choice_and_subfields(tmp_path):
    """choice・subfields 付きセルの normalize は読み込み時に落とす（再レビュー D-5）。

    エディタで種類を切り替えると隠れた normalize が JSON に残りうる。残っていても
    無害（CellSpec に載らない）で、validate_v1 の件数 28 も汚染されないこと。
    """
    from chouhyo_ocr.columns import validate_v1
    t = json.loads(TPL.read_text(encoding="utf-8"))
    for face in t["faces"]:
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                if c["kind"] == "choice" or c.get("subfields"):
                    c["normalize"] = "amount"  # エディタの隠れ値を装う
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    template = load_template(p)
    polluted = [c for c in template.cells
                if c.normalize and (c.kind == "choice" or c.subfields)]
    assert polluted == []
    assert len(validate_v1(template)) == 218  # 28 件カウントが汚染されない


def test_validate_v1_rejects_missing_normalize(tmp_path):
    """normalize が落ちたテンプレートは validate_v1 が拒否する（再レビュー N-1/N-4）。

    エディタで表を作り直すと属性が落ちても列数 218 は変わらないため、
    件数チェックがないと「保存＋コア検証 OK」の緑が出てしまう。
    """
    from chouhyo_ocr.columns import validate_v1
    from chouhyo_ocr.template import TemplateError
    ok = _template_with(tmp_path)
    assert len(validate_v1(ok)) == 218
    broken = _template_with(tmp_path, drop_normalize=True)
    # 文言は管理者向けに画面の語彙（正規化／金額）で出す（レビュー D-7）
    with pytest.raises(TemplateError, match="正規化「金額」"):
        validate_v1(broken)


# ---------- #13: glob メタ文字を含むファイル名 ----------

def test_expand_handles_bracket_filename(tmp_path):
    """scan[1].pdf（ブラウザの重複既定名）が展開失敗にならない。"""
    from PIL import Image

    from chouhyo_ocr.ingest import expand
    src = tmp_path / "scan[1].pdf"
    Image.new("L", (200, 280), 255).save(src)  # 1ページの最小 PDF
    out = tmp_path / "pages"
    out.mkdir()
    pages = expand(src, dpi=72, out_dir=out)
    assert len(pages) == 1
    assert pages[0].name.startswith("scan[1]-")


def test_run_accepts_single_file_input(tmp_path):
    """run --input は単一ファイルも受ける（フォルダ縛りの解消・issue #19）。"""
    from chouhyo_ocr.ingest import list_inputs
    f = tmp_path / "scan_0001.png"
    f.write_bytes(b"\x89PNG\r\n")
    assert list_inputs(f) == [f]
    bad = tmp_path / "memo.txt"
    bad.write_text("x")
    assert list_inputs(bad) == []


def test_expand_page_cli_returns_png(tmp_path):
    """expand-page が PDF の指定ページを PNG 展開してパスを返す（issue #19）。"""
    import subprocess

    from PIL import Image
    src = tmp_path / "sample.pdf"
    Image.new("L", (200, 280), 255).save(src)
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"workdir": str(tmp_path / "wd"),
                                    "log_dir": str(tmp_path / "logs")}),
                        encoding="utf-8")
    from chouhyo_ocr.paths import app_root
    python = app_root() / ".venv" / "Scripts" / "python.exe"
    r = subprocess.run(
        [str(python), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg_file), "expand-page", "--input", str(src)],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    ev = next(json.loads(l) for l in r.stdout.splitlines()
              if l.strip() and "expand_page" in l)
    assert ev["ok"] is True and ev["pages"] == 1
    from pathlib import Path as _P
    assert _P(ev["page_path"]).exists()
    # 絶対パスであること。相対だと GUI 側の cwd で解決されて見つからない
    #（dev 窓で「展開中…」のまま止まった実測原因・2026-08-28）
    assert _P(ev["page_path"]).is_absolute()
    # 存在しないページ番号は明示エラー
    r2 = subprocess.run(
        [str(python), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg_file), "expand-page", "--input", str(src),
         "--page", "5"],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert r2.returncode == 1


def test_choice_with_subfields_keeps_row_length(tmp_path):
    """choice＋subfields のテンプレートでも行の値数＝列数が保たれる（issue #26）。

    エディタの種類切替が分割指定を残しても、読み込み時に落として無害化する。
    """
    from chouhyo_ocr.render_rows import build_row
    t = json.loads(TPL.read_text(encoding="utf-8"))
    for face in t["faces"]:
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                if c.get("subfields"):
                    c["kind"] = "choice"  # エディタの切替事故を装う
                    c.pop("normalize", None)
                    c["choice_marks"] = [
                        {"value": "A", "x_offset": 0, "width": 20},
                        {"value": "B", "x_offset": 20, "width": 20}]
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    template = load_template(p)
    # choice セルの subfields は落ち、output_columns は常に1
    assert all(len(c.output_columns()) == 1
               for c in template.cells if c.kind == "choice")
    n_extract = sum(len(c.output_columns()) for c in template.cells)
    cells = {c.field_id: ("", None, c.kind, False) for c in template.cells}
    row = build_row(
        template,
        {"page_id": "p", "source_file": "s.png", "page_no": 1,
         "status": "", "unassigned_below_table": 0},
        cells, {}, CFG)
    assert len(row.values) == n_extract


def test_write_outputs_rejects_row_length_mismatch(tmp_path):
    """値数≠列数の行は明示例外で拒否される（issue #27・assert 非依存）。"""
    from chouhyo_ocr.render_out import write_outputs
    from chouhyo_ocr.render_rows import Row
    cols = ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名",
            "ページ番号", "ステータス", "a", "b", "c"]
    bad = Row(page_id="p1", source_file="s.pdf", page_no=1, status="正常",
              values=["x", "y"], unclear_count=0, min_conf="")  # 3列に2値
    with pytest.raises(ValueError, match="値数"):
        write_outputs(tmp_path, "t", cols, [bad])


def test_store_upserts_sweep_stale_rows(tmp_path):
    """cell/era の総入れ替えで、今回書かなかった残骸が消える（issue #28）。"""
    from chouhyo_ocr.store import Store
    st = Store(tmp_path / "s.db")
    st.upsert_page("p1", "a.pdf", 1, "expanded")
    st.upsert_cells("p1", [("f_old", "旧値", 0.9, "text", 0),
                           ("f_keep", "残す", 0.9, "text", 0)])
    st.upsert_eras("p1", {"era_old": {"昭": 0.5}})
    # テンプレ変更後の再割付を模す: f_old / era_old は今回書かれない
    st.upsert_cells("p1", [("f_keep", "新値", 0.95, "text", 0),
                           ("f_new", "追加", 0.9, "text", 0)])
    st.upsert_eras("p1", {"era_new": {"S": 0.4}})
    cells = st.cells("p1")
    assert set(cells) == {"f_keep", "f_new"}       # f_old が残らない
    assert cells["f_keep"][0] == "新値"
    assert set(st.era_scores("p1")) == {"era_new"}  # 旧選択肢名が残らない
    st.close()


def test_run_reports_stale_pages(tmp_path):
    """入力から消えたファイルの行が残っている場合、run が可視化する（issue #28）。"""
    if not (RESP.exists() and PAGE_PNG_EXISTS):
        pytest.skip("保存済み応答が無い環境")
    import shutil

    from chouhyo_ocr.pipeline import run
    from chouhyo_ocr.vision_client import ReplayClient
    from chouhyo_ocr.paths import app_root
    page_png = app_root() / "workdir" / "pages" / "sample-1.png"
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "o"),
                 workdir=str(tmp_path / "w"), log_dir=str(tmp_path / "l"))
    inp = tmp_path / "in"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(page_png, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    run(inp, TPL, cfg, ReplayClient(resp))

    (inp / "a.png").rename(inp / "b.png")  # a を消し b を足す
    shutil.copy(RESP, resp / "b_p0001.json")
    events = []
    run(inp, TPL, cfg, ReplayClient(resp), progress=events.append)
    stale = [e for e in events if e.get("event") == "stale_pages"]
    assert stale and stale[0]["count"] == 1 and stale[0]["files"] == ["a.png"]


def test_run_e2e_with_single_file_input(tmp_path):
    """単一ファイル入力で run→render が完走し、通常の1行出力になる（issue #19）。"""
    import shutil

    from chouhyo_ocr.paths import app_root
    from chouhyo_ocr.pipeline import render, run
    from chouhyo_ocr.vision_client import ReplayClient
    resp_src = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
    page_png = app_root() / "workdir" / "pages" / "sample-1.png"
    if not (resp_src.exists() and page_png.exists()):
        pytest.skip("保存済み応答が無い環境")
    cfg = Config(unclear_threshold=0.4, output_dir=str(tmp_path / "out"),
                 workdir=str(tmp_path / "wd"), log_dir=str(tmp_path / "logs"))
    single = tmp_path / "scan_0001.png"
    shutil.copy(page_png, single)
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(resp_src, resp / "scan_0001_p0001.json")

    summary = run(single, TPL, cfg, ReplayClient(resp))  # フォルダでなくファイル
    assert summary.pages == 1 and summary.rows == 1
    _x, _c, rows = render(TPL, cfg, timestamp="sf")
    assert rows[0].source_file == "scan_0001.png"
    assert rows[0].status == "正常"


def test_expand_replaced_pdf_does_not_mix_stale_pages(tmp_path):
    """同名 PDF の差し替え再実行で旧展開分が混ざらない（issue #20 の実測シナリオ）。

    12頁→2頁へ差し替えると、旧実装は残骸と合わせ14頁を返し、ゼロ埋め幅の
    差（12頁=2桁・2頁=1桁）で辞書順ソートの並びも崩れていた。
    """
    from PIL import Image

    from chouhyo_ocr.ingest import expand
    out = tmp_path / "pages"; out.mkdir()
    src = tmp_path / "y.pdf"

    frames = [Image.new("L", (100, 140), 255 - i) for i in range(12)]
    frames[0].save(src, save_all=True, append_images=frames[1:])
    first = expand(src, dpi=36, out_dir=out)
    assert len(first) == 12

    frames2 = [Image.new("L", (100, 140), 250), Image.new("L", (100, 140), 249)]
    frames2[0].save(src, save_all=True, append_images=frames2[1:])
    second = expand(src, dpi=36, out_dir=out)
    assert len(second) == 2, f"残骸が混ざった: {[p.name for p in second]}"
    nos = [int(p.stem.rsplit("-", 1)[1]) for p in second]
    assert nos == [1, 2]  # 数値順


def test_expand_does_not_pick_sibling_stem_pages(tmp_path):
    """a.pdf の展開が a-1.pdf 由来の a-1-1.png を拾わない（再レビュー N-13）。"""
    from PIL import Image

    from chouhyo_ocr.ingest import expand
    src = tmp_path / "a.pdf"
    Image.new("L", (200, 280), 255).save(src)
    out = tmp_path / "pages"
    out.mkdir()
    (out / "a-1-1.png").write_bytes(b"stale")   # a-1.pdf の展開分を装う
    (out / "a-extra.png").write_bytes(b"stale")  # 数字でない接尾辞
    pages = expand(src, dpi=72, out_dir=out)
    assert [p.name for p in pages] == ["a-1.png"]


# ---------- #14: config の検証 ----------

def _write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_zero_threshold_rejected(tmp_path):
    """〓閾値 0 は転記主義の無効化なので拒否（GUI の空入力事故の最終防衛線）。"""
    p = _write_cfg(tmp_path, {"unclear_threshold": 0})
    with pytest.raises(ConfigError, match="unclear_threshold"):
        load_config(p)


def test_typo_key_rejected(tmp_path):
    """キー名 typo は無言で既定値に落とさず、キー名を示して拒否する。"""
    p = _write_cfg(tmp_path, {"unclear_thresold": 0.9})
    with pytest.raises(ConfigError, match="unclear_thresold"):
        load_config(p)


def test_wrong_type_rejected(tmp_path):
    p = _write_cfg(tmp_path, {"send_limit": "abc"})
    with pytest.raises(ConfigError, match="send_limit"):
        load_config(p)
    p2 = _write_cfg(tmp_path, {"workdir": ""})
    with pytest.raises(ConfigError, match="workdir"):
        load_config(p2)


def test_valid_config_passes(tmp_path):
    p = _write_cfg(tmp_path, {"unclear_threshold": 0.9, "send_limit": 50,
                              "workdir": "wd"})
    cfg = load_config(p)
    assert cfg.unclear_threshold == 0.9
    assert cfg.send_limit == 50
    assert cfg.era_threshold == 0.05  # 未指定は既定値


def test_send_limit_zero_is_valid(tmp_path):
    """send_limit=0（送信しないドライラン）は従来どおり通る（再レビュー N-6）。"""
    cfg = load_config(_write_cfg(tmp_path, {"send_limit": 0}))
    assert cfg.send_limit == 0
