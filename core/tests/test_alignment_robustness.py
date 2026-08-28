"""位置合わせの頑健性（回転入力の再配置・2026-08-28 実測の回帰固定）。

テンプレ較正に使った画像そのものを流す検証は自己整合の確認にすぎない
（ユーザー指摘）。ここでは入力を意図的に回転させ、deskew が補正して
無変換ベースラインと同一の出力になることを固定する。

平行移動は罫線射影による常時補正＋信頼不能時の位置合わせ失敗（D-25・#30）。
不変条件: status=正常 で出た行は、無変換ベースラインと値が一致する。
ズレは補正されるか失敗になるかのどちらかで、その中間（正常顔の誤値）は
存在しない。
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


def _shift(img: Image.Image, dx: int, dy: int) -> Image.Image:
    canvas = Image.new("RGB", img.size, "white")
    canvas.paste(img.convert("RGB"), (dx, dy))
    return canvas


def test_shifted_input_is_realigned():
    """探索範囲内の平行移動は補正され、ベースラインと同一の出力になる（D-25）。

    補正が成功すれば送信画像は無変換時と一致するため、元の Vision 応答が
    そのまま有効——値の全列一致が補正成功の実証になる。
    """
    base = _run(Image.open(PAGE), "sbase")
    for dx, dy in [(2, 2), (5, 5), (12, 12), (18, 18), (-8, -8), (0, 10)]:
        row = _run(_shift(Image.open(PAGE), dx, dy), f"s{dx}_{dy}")
        assert row.status == "正常", f"shift=({dx},{dy}) {row.status}"
        assert list(row.values) == list(base.values), f"shift=({dx},{dy}) で不一致"


def test_large_shift_fails_instead_of_wrong_values():
    """探索範囲を超えるズレは「位置合わせ失敗」の全〓行になる（正常顔の誤値ゼロ）。"""
    base = _run(Image.open(PAGE), "lbase")
    for dx, dy in [(40, 40), (0, 104), (0, 113)]:  # 104/113 は行ピッチ（1行ズレ解）
        row = _run(_shift(Image.open(PAGE), dx, dy), f"l{dx}_{dy}")
        if row.status == "正常":
            # 本質的な受入条件: 「正常なのに値が違う」だけは絶対に出さない
            assert list(row.values) == list(base.values), \
                f"shift=({dx},{dy}) が正常顔で誤値を出した"
        else:
            assert "位置合わせ失敗" in row.status
            assert all(v == "〓" for v in row.values)


def test_erased_rulings_fail_instead_of_passthrough():
    """罫線を消した画像は 0 補正で素通しせず「位置合わせ失敗」へ倒れる。"""
    from PIL import ImageDraw
    img = Image.open(PAGE).convert("RGB")
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width, img.height), fill="white")  # 全消し＝線ゼロ
    row = _run(img, "erased")
    assert "位置合わせ失敗" in row.status
    assert all(v == "〓" for v in row.values)


def test_rotated_input_is_realigned():
    """±0.5° 回転した入力が deskew で戻り、無変換と**全列**同じ出力になる。

    以前は -0.5° で choice（元号）1セルが劣化するのを許容していたが、
    issue #23（帯を境界またぎへ）で解消したので厳格化した（レビュー M-6）
    ——緩いままだと丸印が再び壊れても -0.5° ケースが通ってしまう。
    """
    base = _run(Image.open(PAGE), "base")
    for ang in (0.5, -0.5):
        rot = Image.open(PAGE).convert("RGB").rotate(
            ang, expand=False, fillcolor="white", resample=Image.BICUBIC)
        row = _run(rot, f"rot{ang}")
        assert row.status == base.status == "正常"
        assert list(row.values) == list(base.values), f"angle={ang} で不一致"
