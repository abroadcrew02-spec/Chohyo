"""吸着（平行移動推定）検証用のテストヘルパー（#70 前提作業・§10.2-2/§10.2-7）。

このモジュール自体はテストではない——`align.estimate_shift` の頑健性を
検証するテスト（今後追加分を含む）が共通で使う変形ユーティリティを置く。

## shift_block_y / shift_response_vertices の実測結果（§10.2-2）

対象: `testdata/local/pages/sample-1.png`（テンプレート `templates/chouhyo-v1.json`・
back 面・table_id="detail"・block_idx=1＝origin x=1123 のブロック）。
`shift_block_y` でこのブロックの矩形領域だけを y 方向へ δ px 動かし、
`chouhyo_ocr.align.estimate_shift` を back 面全体に対して実行した
（実行日 2026-09-02・`.venv/Scripts/python.exe` 経由・実 API 送信なし）。

コマンド（再現用・実行後に一時ファイルは残さない）:
```
PYTHONPATH=core .venv/Scripts/python.exe -X utf8 - <<'PY'
from PIL import Image
import numpy as np
from chouhyo_ocr.align import _exclusion_mask, _otsu, estimate_shift
from chouhyo_ocr.template import load_template
from helpers_geom import shift_block_y

tpl = load_template("templates/chouhyo-v1.json")
face = tpl.face("back")
img = Image.open("testdata/local/pages/sample-1.png").convert("RGB")
for dy in (2, 3, 4, 6):
    shifted = shift_block_y(img, tpl, "back", "detail", 1, dy)
    r = face.source_rect
    face_img = shifted.crop((r.x, r.y, r.x + r.w, r.y + r.h))
    gray = np.asarray(face_img.convert("L"))
    dilate = max(0, round(60 * tpl.dpi_scale))
    coarse = _exclusion_mask(face, dilate)
    th = _otsu(gray, coarse)
    binary = (gray < th) & ~coarse
    est = estimate_shift(binary, face, dpi=tpl.render_dpi)
    print(dy, est.ok, est.reason, est.dx, est.dy, est.matched, est.total)
PY
```

実測結果（δ=0 はベースライン・無変形。実行日 2026-09-02）:

| δ (px) | ok | reason | dx | dy | matched/total |
|---|---|---|---|---|---|
| 0（無変形） | True | ""  | 0 | 0 | 38/42 |
| 2 | True | "" | 0 | 1 | 38/42 |
| 3 | True | "" | 0 | 1 | 38/42 |
| 4 | True | "" | 0 | 1 | 38/42 |
| 6 | False | ambiguous | 0 | 1 | 36/42 |

追加で δ=0..9 の1刻み掃引も実施し、**境界は δ=5（ok）→δ=6（ambiguous）の
ちょうど間**にあることを確認した（δ=5: matched=38/42 ok=True／δ=6:
matched=36/42 ok=False）。

**事前の主張「δ=3 のみ成立（δ≥4 は ambiguous）」は実測と一致しなかった**
（捏造禁止・ルール2により実測値をそのまま記録する）。実際には δ=2〜5 は
すべて ok=True で、グローバル最良解が dy=1（δ=0 の dy=0 から1px ずれる）
に安定する。ブロック1（1123, y=93, 14行）だけを δ px 動かしても、
ブロック0（70, y=93）側の一致本数がそのまま残るため、`_axis_shift` の
最良シフトが「両ブロックの折衷点に近い dy=1」で決まり、次点との差
（`SHIFT_GAP_MIN=2`）を割り込むのは δ=6 から——δ=3 だけが特別という
事前の主張は、この構成・この実データ（sample-1.png）では再現しなかった。
この境界は `align.py` の `SHIFT_GAP_MIN`/`SHIFT_RUNNER_DIST` 較正値・
入力画像に依存するため、定数や対象画像を変更した場合はこの表を
再実測すること。

`shift_response_vertices` は保存済み Vision 応答（S2 応答）の
`boundingBox.vertices`（`fullTextAnnotation.pages[].blocks[].paragraphs[].
words[].symbols[]` および `textAnnotations[]` の各階層に同型でネストする）
を対象に、`core/workdir/responses/帳票抽出検証用2026-08-24_p0001.json` で
動作を確認した（region 内の1点を dy=5 で動かし、region 外の点が不変で
あることをアサートする形・詳細は本ファイル末尾の手動確認コマンド）。
この関数は画像のピクセルを一切参照しない（`ReplayClient` と同じ性質）ため、
`shift_block_y` が対象にする画像と、対応する Vision 応答ファイルが
別々のキャプチャ由来であっても機能に支障はない。
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from chouhyo_ocr.template import Rect, Template


def _block_rect(template: Template, face_id: str, table_id: str,
                block_idx: int) -> Rect:
    """table_id・block_idx から、面の絶対（ページ）座標でのブロック矩形を求める。

    Face は展開済みのセル・TableGeom/TableZone しか保持していない
    （生の tables/blocks 定義は load_template 後に残らない）ので、
    TableZone（table_id を持つ）と TableGeom（同じ順序で1ブロック1件）を
    zip して table_id 一致分だけを数え、block_idx 番目を選ぶ。
    """
    face = template.face(face_id)
    if len(face.table_zones) != len(face.table_geoms):
        raise ValueError(
            f"table_zones と table_geoms の件数が一致しない（face={face_id}）。"
            "テンプレート読み込みの内部契約が変わった可能性がある")
    matches = [
        geom for zone, geom in zip(face.table_zones, face.table_geoms)
        if zone.table_id == table_id
    ]
    if block_idx < 0 or block_idx >= len(matches):
        raise ValueError(
            f"table_id={table_id!r} に block_idx={block_idx} は存在しない"
            f"（面 {face_id} のブロック数: {len(matches)}）")
    g = matches[block_idx]
    r = face.source_rect
    return Rect(r.x + g.x_min, r.y + g.y_min,
               g.x_max - g.x_min, g.y_max - g.y_min)


def shift_block_y(img: "Image.Image", template: Template, face_id: str,
                  table_id: str, block_idx: int, dy: int) -> "Image.Image":
    """指定ブロックの矩形領域だけを y 方向に dy px 平行移動して貼り直す。

    テンプレートの table_geoms（D-25 の平行移動推定アンカーと同じ矩形）から
    ブロックの絶対矩形を求め、その中身だけを dy 動かした画像を返す
    （同寸キャンバス・元画像は変更しない）。空いた帯（矩形の元位置）は
    白で塗る——「1ブロックだけがズレる」という部分的な劣化を模擬する。

    引数:
        img: 入力ページ画像（テンプレートの image_size と同寸を想定）。
        template: 対象テンプレート（Face.table_geoms/table_zones を使う）。
        face_id / table_id / block_idx: 対象ブロックの指定
            （block_idx は table_id 内でのブロック定義順・0起点）。
        dy: y方向の移動量（px）。正で下方向。

    戻り値: 新しい Image（コピー）。移動後にブロックの一部が画像外へ
    はみ出す場合は Image.crop/paste の既定どおり自動的に切り詰められる。
    """
    rect = _block_rect(template, face_id, table_id, block_idx)
    out = img.copy()
    block_content = out.crop((rect.x, rect.y, rect.x + rect.w, rect.y + rect.h))
    # 元位置を白で塗る（空いた帯）
    white = Image.new(out.mode, (rect.w, rect.h), "white" if out.mode != "L" else 255)
    out.paste(white, (rect.x, rect.y))
    # 移動後の位置へ貼り直す（画像外へはみ出す分は PIL が自動でクリップする）
    out.paste(block_content, (rect.x, rect.y + dy))
    return out


def _in_region(x: float, y: float, region: tuple[int, int, int, int]) -> bool:
    rx, ry, rw, rh = region
    return rx <= x < rx + rw and ry <= y < ry + rh


def _walk_shift(node: Any, region: tuple[int, int, int, int], dy: int) -> None:
    """resp_json の入れ子構造を再帰的に歩き、"vertices" リストを見つけたら
    region 内の頂点の y へ dy を加える（in-place・呼び出し元が deepcopy 済み
    のノードに対して呼ぶこと）。
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("vertices", "normalizedVertices") and isinstance(value, list):
                for v in value:
                    if not isinstance(v, dict):
                        continue
                    x, y = v.get("x", 0), v.get("y", 0)
                    if _in_region(x, y, region):
                        v["y"] = y + dy
            else:
                _walk_shift(value, region, dy)
    elif isinstance(node, list):
        for item in node:
            _walk_shift(item, region, dy)


def shift_response_vertices(resp_json: dict, region: Rect | tuple[int, int, int, int],
                            dy: int) -> dict:
    """保存済み Vision 応答（MessageToDict 形式）の boundingBox.vertices のうち、
    region 内の点へ同じ dy を加えた**新しい**辞書を返す（入力は変更しない）。

    Google Vision の DOCUMENT_TEXT_DETECTION 応答は
    `fullTextAnnotation.pages[].blocks[].paragraphs[].words[].symbols[]` の
    各階層と `textAnnotations[]` に同型の `boundingBox`/`boundingPoly` →
    `vertices: [{x, y}, ...]` が繰り返しネストする。位置ぎめの深さに依存
    せず全階層を対象にするため、キー名 "vertices"（"normalizedVertices" も
    ついでに対応）で再帰的に探索する——スキーマの入れ子構造そのものに
    依存する決め打ちパスを持たない。

    引数:
        resp_json: json.load 済みの Vision 応答（dict）。
        region: (x, y, w, h) の絶対（ページ）座標の矩形。半開区間
            [x, x+w) x [y, y+h) に入る頂点だけを動かす。
            chouhyo_ocr.template.Rect を渡しても良い（.x/.y/.w/.h を使う）。
        dy: y方向の移動量（px）。頂点に既存の y が無ければ 0 とみなして加算する
            （Vision は 0 のとき省略することがあるため、加算後は必ずキーが立つ）。

    戻り値: 変更を適用した**新しい** dict（deepcopy）。
    """
    if isinstance(region, Rect):
        region_t = (region.x, region.y, region.w, region.h)
    else:
        region_t = tuple(region)  # type: ignore[assignment]
    out = copy.deepcopy(resp_json)
    _walk_shift(out, region_t, dy)
    return out


# ---------------------------------------------------------------------------
# §10.2-7: 複数テンプレート状態（templates_user/）の fixture
# ---------------------------------------------------------------------------
#
# templates/ 直下は Tauri（gui/src-tauri/tauri.conf.json）が配布物へ丸ごと
# 同梱するため、利用者の顧客固有テンプレートを置く場所ではない
# （06_second_form_findings.md §0.2-2・07要件 v0.4 変更点2）。実運用では
# `templates_user/` に置く想定——ただし現状このディレクトリはリポジトリに
# 存在せず、.gitignore の `*.json` ルールの副作用で偶然無視されているだけ
# だった（AZKi M-6）。本 fixture はテスト実行時にだけ
# `templates_user/formB-v1.json`（testdata/formB/formB-v1.json の複製）を
# 作り、テスト終了後に削除する。
def copy_template_to_user_dir(repo_root: Path | None = None) -> Path:
    """testdata/formB/formB-v1.json を templates_user/formB-v1.json へ複製する。

    戻り値: 複製先のパス。呼び出し元が finally 節で
    `cleanup_user_template_dir()` を呼ぶこと（このモジュールは pytest の
    fixture 登録を行わない——helpers_geom.py 自身は conftest ではないため、
    自動 teardown はしない設計）。
    """
    root = repo_root or Path(__file__).resolve().parents[2]
    src = root / "testdata" / "formB" / "formB-v1.json"
    dst_dir = root / "templates_user"
    dst_dir.mkdir(exist_ok=True)
    dst = dst_dir / "formB-v1.json"
    shutil.copy(src, dst)
    return dst


def cleanup_user_template_dir(repo_root: Path | None = None) -> None:
    """copy_template_to_user_dir が作った templates_user/ を削除する（存在すれば）。"""
    root = repo_root or Path(__file__).resolve().parents[2]
    dst_dir = root / "templates_user"
    if dst_dir.exists():
        shutil.rmtree(dst_dir)
