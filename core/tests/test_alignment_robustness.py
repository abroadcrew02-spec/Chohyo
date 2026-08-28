"""位置合わせの頑健性（回転入力の再配置・2026-08-28 実測の回帰固定）。

テンプレ較正に使った画像そのものを流す検証は自己整合の確認にすぎない
（ユーザー指摘）。ここでは入力を意図的に回転させ、deskew が補正して
無変換ベースラインと同一の出力になることを固定する。

平行移動は現状補正機構が無く、±12px で choice の誤選択・±18px で大規模
混入が「正常」ステータスのまま出る（issue #30・実測 2026-08-28）。
補正/検出の実装後にここへ耐性テストを追加する。
"""
import json
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from chouhyo_ocr.config import Config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.pipeline import render, run
from chouhyo_ocr.vision_client import ReplayClient

PAGE = app_root() / "workdir" / "pages" / "sample-1.png"
RESP = (app_root() / "core" / "workdir" / "responses"
        / "帳票抽出検証用2026-08-24_p0001.json")
TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(
    not (RESP.exists() and PAGE.exists()), reason="保存済み応答が無い環境")


def _run(img: Image.Image, tag: str):
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        inp = td / "in"; inp.mkdir()
        rd = td / "resp"; rd.mkdir()
        img.save(inp / "case.png")
        (rd / "case_p0001.json").write_bytes(RESP.read_bytes())
        cfg = Config(output_dir=str(td / "o"), workdir=str(td / "w"),
                     log_dir=str(td / "l"))
        run(inp, TPL, cfg, ReplayClient(rd))
        _x, _c, rows = render(TPL, cfg, timestamp=tag)
        return rows[0]


def test_rotated_input_is_realigned():
    """±0.5° 回転した入力が deskew で戻り、無変換と同一の 212 列になる。"""
    base = _run(Image.open(PAGE), "base")
    for ang in (0.5, -0.5):
        rot = Image.open(PAGE).convert("RGB").rotate(
            ang, expand=False, fillcolor="white", resample=Image.BICUBIC)
        row = _run(rot, f"rot{ang}")
        assert list(row.values) == list(base.values), f"angle={ang} で不一致"
        assert row.status == base.status == "正常"
