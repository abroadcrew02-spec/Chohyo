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
    """探索範囲を超えるズレは「位置合わせ失敗」または「様式不一致」の全〓行になる
    （正常顔の誤値ゼロ）。

    2026-09-02（issue #71 (a')・期待値更新・07 v1.2 §10.3 Q-F21）: FR-F09 が
    「位置合わせ失敗の共用バケツを様式判定由来で分離する」ことを要求した
    結果、dx=40,dy=40 は `few_lines` かつ軸別で検出十分・探索境界にも
    張り付かないため `classify()` が「不一致」（様式不一致）と判定する
    ようになった（実測）。104/113px（行ピッチ＝1行ズレ）は非周期アンカー
    検査で `edge_mismatch` に倒れ、`classify()` は判定不能（位置合わせ失敗）
    のまま——このケースは影響を受けない。**引用訂正**（マリン指摘）:
    07 §7.2-4 は期待値書き換えを**禁じる**条項そのもので、許容の根拠には
    ならない（同条が明示的に許すのは `ALGO_VERSION` の直値更新のみ）。
    この更新を認めるのは **07 v1.2 §10.3 Q-F21**——`test_alignment_robustness.py`
    等の期待値更新を「理由と日付を記録したうえで§7.2-4の例外（2件目）と
    して認める」と明記した項目（1件目は Q-S1 に伴う `test_leak_guards.py`
    の `template_path` 期待反転）。

    このテストの本質的な受入条件（関数名・コメントのとおり「正常なのに
    値が違う」だけは絶対に出さない・全〓）はどちらのバケツでも成立する
    ため不変条件として残しつつ、どちらのバケツに落ちるかは実測済みなので
    ケースごとに固定する（緩い `in (...)` のままだと、将来 classify() が
    変わって別の組み合わせが混入しても検知できない）。
    """
    base = _run(Image.open(PAGE), "lbase")
    expected_status = {
        (40, 40): "様式不一致",     # few_lines・軸別で検出十分・境界に非該当 → mismatch
        (0, 104): "位置合わせ失敗",  # 行ピッチ=1行ズレ → edge_mismatch（非周期アンカー）
        (0, 113): "位置合わせ失敗",  # 同上
    }
    for dx, dy in [(40, 40), (0, 104), (0, 113)]:  # 104/113 は行ピッチ（1行ズレ解）
        row = _run(_shift(Image.open(PAGE), dx, dy), f"l{dx}_{dy}")
        if row.status == "正常":
            # 本質的な受入条件: 「正常なのに値が違う」だけは絶対に出さない
            assert list(row.values) == list(base.values), \
                f"shift=({dx},{dy}) が正常顔で誤値を出した"
        else:
            assert row.status == expected_status[(dx, dy)], \
                f"shift=({dx},{dy}) {row.status}"
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
