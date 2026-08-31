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
