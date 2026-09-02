"""formC-1.png（同寸・別様式の合成帳票画像）を決定論的に生成する。

要件: docs/design/chouhyo-ocr/07_frame_detection_requirements.md §10.2-1。
`templates/chouhyo-v1.json`（出荷テンプレート）と**同じ画像寸法**
（2490x3510・300dpi 相当の A4 縦）だが、表の位置・行ピッチ・列幅・本数が
明確に異なる罫線構成を持つ「別様式」を作る。目的は
`align_page`/`estimate_shift`（core/chouhyo_ocr/align.py）に通したときの
理由コード（few_lines/boundary/ambiguous/edge_mismatch）を実測すること。

- 引数なし・乱数なし。実行するたびバイト単位で同一の PNG を
  `testdata/formC/formC-1.png` へ書き出す（テストの再現性のため）。
- 装飾ラベルは英字（PIL 同梱の既定ビットマップフォント・
  `ImageFont.load_default()`）のみ。CJK フォントの有無に依存しない
  （testdata/formB の前例 §3 と同じ方針）。
- 記入値（手書き相当のインク）は一切描かない。罫線と印字ラベルのみの
  白紙帳票——将来別途 Vision 応答を合成する場合の下地としても使える。

v1 との構造差分（意図的）:

| 観点 | v1 front（family） | v1 back（detail） | formC（単一の表） |
|---|---|---|---|
| ブロック数 | 2（x=389, 1410） | 2（x=70, 1123） | 1（x=150） |
| 行ピッチ | 113 | 104 | 70 |
| 行高さ | 105 | 100 | 60 |
| 行数/ブロック | 5 | 14 | 30 |
| 列数 | 4 | 5 | 6 |
| 列幅合計 | 1011 | 1053 | 1650 |
| 開始 y | 907 | 93（面ローカル） | 400（ページ座標） |

formC の表は単一ブロックで縦に長く、front 面の帯（page y: 0-1880）と
back 面の帯（page y: 1880-3510）の両方にまたがって配置される——
どちらの面から見ても「期待する罫線位置」がまったく当たらない構成になる。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT_PATH = Path(__file__).parent / "formC-1.png"

# --- 画像寸法（出荷テンプレートと同一・§10.2-1）---
WIDTH, HEIGHT = 2490, 3510

# --- formC の表（単一ブロック・v1 のいずれの表とも位置/ピッチ/列幅/本数が異なる）---
TABLE_ORIGIN_X = 150
TABLE_ORIGIN_Y = 400
ROW_PITCH = 70
ROW_HEIGHT = 60
ROWS = 30
COLUMN_WIDTHS = [150, 400, 200, 300, 300, 300]  # 合計 1650
COLUMN_NAMES = ["ID", "Item Name", "Date", "Qty", "Location", "Memo"]

LINE_COLOR = (0, 0, 0)
LINE_WIDTH = 2


def _table_right() -> int:
    return TABLE_ORIGIN_X + sum(COLUMN_WIDTHS)


def _table_bottom() -> int:
    return TABLE_ORIGIN_Y + ROW_PITCH * ROWS


def _draw_table(draw: "ImageDraw.ImageDraw") -> None:
    right = _table_right()
    bottom = _table_bottom()

    # 横罫線: 行境界（0..ROWS、行ピッチ間隔）
    for i in range(ROWS + 1):
        y = TABLE_ORIGIN_Y + ROW_PITCH * i
        draw.line((TABLE_ORIGIN_X, y, right, y), fill=LINE_COLOR, width=LINE_WIDTH)

    # 縦罫線: 列境界
    x = TABLE_ORIGIN_X
    xs = [x]
    for w in COLUMN_WIDTHS:
        x += w
        xs.append(x)
    for vx in xs:
        draw.line((vx, TABLE_ORIGIN_Y, vx, bottom), fill=LINE_COLOR, width=LINE_WIDTH)


def _draw_labels(draw: "ImageDraw.ImageDraw", font: "ImageFont.ImageFont") -> None:
    draw.text((TABLE_ORIGIN_X, 200), "FORM C - INVENTORY CHECK SHEET", fill=LINE_COLOR, font=font)
    draw.text((TABLE_ORIGIN_X, 250), "(synthetic / no filled values)", fill=LINE_COLOR, font=font)

    x = TABLE_ORIGIN_X
    for name, w in zip(COLUMN_NAMES, COLUMN_WIDTHS):
        draw.text((x + 6, TABLE_ORIGIN_Y - 30), name, fill=LINE_COLOR, font=font)
        x += w

    draw.text((TABLE_ORIGIN_X, _table_bottom() + 20), "-- end of table --", fill=LINE_COLOR, font=font)


def build() -> "Image.Image":
    img = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    # ページ外枠（装飾。位置合わせのアンカーには使われない）
    draw.rectangle((40, 40, WIDTH - 40, HEIGHT - 40), outline=LINE_COLOR, width=2)
    _draw_labels(draw, font)
    _draw_table(draw)
    return img


def main() -> None:
    img = build()
    img.save(OUT_PATH)
    print(f"wrote {OUT_PATH} ({img.size[0]}x{img.size[1]})")


if __name__ == "__main__":
    main()
