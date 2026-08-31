"""debug-images（読み取り可視化・開発者モード）のテスト。

実サンプル素材（workdir/pages・workdir/s2）に依存するため、無い環境では skip
（test_e2e_replay と同じ流儀）。
"""
import shutil

import pytest

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import run
from chouhyo_ocr.template import load_template

RESP = app_root() / "workdir" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="replay 素材が無い環境")


from chouhyo_ocr.vision_client import ReplayClient


@pytest.fixture()
def worked(tmp_path):
    cfg = Config(output_dir=str(tmp_path / "o"), workdir=str(tmp_path / "w"),
                 log_dir=str(tmp_path / "l"))
    inp = tmp_path / "in"; inp.mkdir()
    resp = tmp_path / "resp"; resp.mkdir()
    shutil.copy(PAGE_PNG, inp / "a.png")
    shutil.copy(RESP, resp / "a_p0001.json")
    run(inp, TPL, cfg, ReplayClient(resp))
    return cfg, tmp_path


def test_writes_one_png_per_page(worked, tmp_path):
    from pathlib import Path

    from chouhyo_ocr.debug_images import write_debug_images
    from chouhyo_ocr.store import Store
    cfg, _ = worked
    wd = Path(cfg.workdir)
    out = tmp_path / "dbg"
    store = Store(wd / "intermediate.sqlite")
    try:
        made = write_debug_images(store, load_template(TPL), wd / "aligned",
                                  out, cfg.unclear_threshold)
    finally:
        store.close()
    assert len(made) == 1
    p = made[0]
    assert p.exists() and p.suffix == ".png"
    # 白紙 PNG ではなく、実際に紙と注記が描かれていること（サイズで近似）
    assert p.stat().st_size > 100_000, "描画がほぼ空（オーバーレイが失敗している）"
    # 出力サイズはテンプレート座標系
    from PIL import Image
    with Image.open(p) as im:
        assert im.size == load_template(TPL).image_size


def test_page_filter(worked, tmp_path):
    from pathlib import Path

    from chouhyo_ocr.debug_images import write_debug_images
    from chouhyo_ocr.store import Store
    cfg, _ = worked
    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        none = write_debug_images(store, load_template(TPL), wd / "aligned",
                                  tmp_path / "dbg2", cfg.unclear_threshold,
                                  page_ids=["存在しないID"])
    finally:
        store.close()
    assert none == []


# ---------- cli debug-images（5巡目 第3〜4段・#59 H-5・#60 M-1①④） ----------

def _cli_cfg_path(cfg, tmp_path, name="cli_config.json"):
    from chouhyo_ocr.config import save_config
    p = tmp_path / name
    save_config(cfg, p)
    return p


def test_out_rejects_cloud_synced_path(worked, tmp_path):
    """--out が同期フォルダ配下だと拒否する（#59 H-5）。

    読取値・信頼度を焼き込んだ画像は中間データより濃い個人情報で、既定の
    workdir/debug/ は purge・verify の同期検査の対象だが --out は検査の外を
    通っていた。
    """
    from chouhyo_ocr import cli
    cfg, _ = worked
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    synced_out = tmp_path / "OneDrive" / "debug"
    r = cli.main(["--config", str(cfg_path), "debug-images",
                  "--template", str(TPL), "--out", str(synced_out)])
    assert r == 1
    assert not synced_out.exists(), "拒否されたのに出力先が作られている"


def test_out_default_is_not_checked(worked, tmp_path):
    """既定（--out 省略・workdir/debug）は同期フォルダ検査の対象外（従来どおり）。"""
    from chouhyo_ocr import cli
    cfg, _ = worked
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    r = cli.main(["--config", str(cfg_path), "debug-images", "--template", str(TPL)])
    assert r == 0


def test_debug_images_refuses_after_template_change(worked, tmp_path, capsys):
    """テンプレート変更後は check_reusable が拒否する（#60 M-1①）。

    通さないと、変わった枠に旧テンプレ割付の〓判定を重ねた嘘の可視化を
    出してしまう（render/remap と同じ整合ゲート）。
    """
    import json

    from chouhyo_ocr import cli
    cfg, _ = worked
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    raw = json.loads(TPL.read_text(encoding="utf-8"))
    fld = next(f for f in raw["faces"][0]["fields"] if f["kind"] == "text")
    fld["rect"]["x"] += 1  # geometry_hash（faces.source/exclusions等）は不変だが
                           # template_hash（全体）は変わる——#25 の検査対象
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    r = cli.main(["--config", str(cfg_path), "debug-images", "--template", str(changed)])
    assert r == 0  # 業務的な拒否は exit 0（他コマンドの OperationRefused と同じ契約）
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    refused = [e for e in events if e.get("event") == "refused"]
    assert refused and "テンプレートが変わっている" in refused[0]["error"]


def test_debug_images_no_pages_reports_reason(tmp_path, capsys):
    """中間データが空のとき ok:false・reason:no_pages（#60 M-1④）。

    従来は ok:true, count:0 固定で、業務的失敗と「元々0件」の区別がつかなかった。
    """
    import json

    from chouhyo_ocr.config import Config
    from chouhyo_ocr import cli
    cfg = Config(output_dir=str(tmp_path / "o"), workdir=str(tmp_path / "w"),
                 log_dir=str(tmp_path / "l"))
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    r = cli.main(["--config", str(cfg_path), "debug-images", "--template", str(TPL)])
    assert r == 0
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    ev = next(e for e in events if e.get("event") == "debug_images")
    assert ev["ok"] is False and ev["reason"] == "no_pages" and ev["count"] == 0


def test_debug_images_page_not_found_reports_reason(worked, tmp_path, capsys):
    """存在しないページ ID を指定すると、該当なしと明示する（#60 M-1④）。"""
    import json

    from chouhyo_ocr import cli
    cfg, _ = worked
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    r = cli.main(["--config", str(cfg_path), "debug-images",
                  "--template", str(TPL), "--page", "存在しないID"])
    assert r == 0
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    ev = next(e for e in events if e.get("event") == "debug_images")
    assert ev["ok"] is False and ev["reason"] == "page_not_found" and ev["count"] == 0


def test_debug_images_no_aligned_images_reports_reason(worked, tmp_path, capsys):
    """ページはあるが位置合わせ済み画像が無いとき ok:false・reason:no_aligned_images。"""
    import json
    import shutil
    from pathlib import Path

    from chouhyo_ocr import cli
    cfg, _ = worked
    cfg_path = _cli_cfg_path(cfg, tmp_path)
    shutil.rmtree(Path(cfg.workdir) / "aligned")  # 位置合わせ済み画像を消す
    r = cli.main(["--config", str(cfg_path), "debug-images", "--template", str(TPL)])
    assert r == 0
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines() if l.strip()]
    ev = next(e for e in events if e.get("event") == "debug_images")
    assert ev["ok"] is False and ev["reason"] == "no_aligned_images" and ev["count"] == 0


def test_field_origins_matches_assign_on_real_sample(worked, tmp_path):
    """#60 M-1③: debug_images._field_origins が mapping.assign() と同じ結論に
    達すること（実データ経路）。person_郵便番号1 は主が空・参照先採用（実測・
    test_mapping.py::test_person_fields と同じ前提）なので 'fallback' になる。
    """
    from pathlib import Path

    from chouhyo_ocr.debug_images import _field_origins
    from chouhyo_ocr.mapping import build_symbol_locator
    from chouhyo_ocr.store import Store
    from chouhyo_ocr.template import CellSpec

    cfg, _ = worked
    wd = Path(cfg.workdir)
    store = Store(wd / "intermediate.sqlite")
    try:
        pages = store.pages()
        assert len(pages) == 1
        pid = pages[0]["page_id"]
        tokens = store.tokens(pid)
        template = load_template(TPL)
        cells_by_face: dict[str, list[CellSpec]] = {}
        for c in template.cells:
            cells_by_face.setdefault(c.face_id, []).append(c)
        locators = {fid: build_symbol_locator(cs) for fid, cs in cells_by_face.items()}
        origins = _field_origins(locators, tokens)
    finally:
        store.close()
    assert origins.get("person_郵便番号1") == "fallback"
    assert origins.get("person_郵便番号2") == "fallback"
