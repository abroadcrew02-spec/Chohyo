"""吸着 ON/OFF で読取値が変わる欄を数える診断（issue #75 (f)・FR-F40・08 §6.6）。

**数えるだけで何も直さない。** 保存済みの token を材料に、吸着 OFF（テンプレート
座標）と吸着 ON（`alignment.transform["snap"]` に記録済みの座標）で割付とスコア
付けをやり直し、出力列の値が変わる欄の件数を返す。`run` を 2 回回す形にすると
課金が 2 倍になるため採らない（FR-F40）。

守っている不変条件:

1. **Vision へ送らない。** `OcrClient` を 1 つも作らない。材料は `token` 表だけ
2. **`.xlsx`／`.csv` を書かない。** `render_out` を呼ばず、`render_rows.build_row`
   をメモリ上で 2 回呼んで比べるだけ
3. **中間データを 1 バイトも変えない。** SQLite を一時ディレクトリへ複製してから
   開く——`Store.__init__` は `_ensure_column` で `ALTER TABLE` を打つため、
   旧 DB を直接開くだけで行の形が変わる（AC-F39 は実行前後で該当行が同一である
   ことを求める）
4. **座標の作り方は他の 4 経路と同じ 2 行だけ**（08 §6.3）。`transform["snap"]` を
   自分で読まない——読み出し口は `store.snap_geometry()` の 1 つに保つ
5. **どちらが正しいかを機械が言わない。** ON と OFF を並べて件数を出すところまでが
   このコマンドの仕事で、「誤吸着を検知した」とは書かない（07 §9.3 の NG 事項）。
   判断は人が行う（Q-F14 の受け入れ手順）

出力（stdout の JSON Lines・1 実行 1 行）には**記入値を載せない**。載せるのは
件数と、差分の出た `page_id`・出力列名だけ——値の比較そのものはメモリ上で終わり、
画面にもファイルにも残さない。ログへ出すのは件数のみ（Q-S1・NFR-F05）。
"""
from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import logging_safe as log
from . import snap
from .columns import extract_columns
from .config import Config
from .pipeline_errors import OperationRefused
from .store import Store
from .template import Template

# `diffs` 配列の既定の打ち切り件数。超えた分は truncated:true で示す——
# 全ページ差分だらけの workdir で 1 行の JSON が数 MB になるのを防ぐ
DEFAULT_LIMIT = 200


@dataclass(frozen=True)
class CellDiff:
    """吸着 ON/OFF で値が変わった 1 欄。**値そのものは持たない。**"""
    page_id: str
    column: str


@dataclass(frozen=True)
class DiffReport:
    pages: int            # 比べた done ページ数
    pages_snapped: int    # そのうち吸着後の座標が実際に動いていたページ数
    cells_compared: int   # 比べた欄の延べ数（ページ数 × 出力列数）
    diff_cells: int       # 値が変わった欄の延べ数（打ち切り前の総数）
    diffs: tuple[CellDiff, ...]
    aligned_missing: int  # 丸印の採点をやり直せなかった (ページ, 面) の数
    truncated: bool


def copy_store(db: Path, dst_dir: Path) -> Path:
    """中間データを複製して、複製側のパスを返す（不変条件 3）。

    WAL の内容は `-wal` ファイル側にあるため、あれば一緒に複製する。`-shm` は
    複製しない——SQLite が複製側で作り直す共有メモリの索引で、他プロセスの
    ロック状態を持ち込むと開けなくなりうる。
    """
    dst = dst_dir / db.name
    shutil.copy2(db, dst)
    wal = db.with_name(db.name + "-wal")
    if wal.exists():
        shutil.copy2(wal, dst_dir / wal.name)
    return dst


def refusal_reason(store: Store, geo_hash: str, tpl_hash: str) -> str:
    """`check_reusable` が拒否した理由を機械可読なコードにする。

    `OperationRefused` は利用者向けの日本語メッセージしか持たないため、
    同じ順序で同じ集合を見て理由コードを決める。**メッセージ本文は例外から
    そのまま使う**（文言を二重管理しない）。順序が `check_reusable` と
    ずれても止まる／止まらないの判断は変わらない——変わるのは理由コードの
    名前だけで、`error` に載る本文は常に正しい。
    """
    from .align import ALGO_VERSION
    stored_geo = store.geometry_hashes()
    if stored_geo and stored_geo != {geo_hash}:
        return "geometry_mismatch"
    stored_algo = store.algo_versions()
    if stored_algo and stored_algo != {ALGO_VERSION}:
        return "algo_version_mismatch"
    stored_snap = store.snap_flags()
    if stored_snap and stored_snap != {1}:
        # 吸着 ON と OFF のページが 1 つの workdir に混ざっている状態。
        # 「ON の記録が無い」（no_snap_recorded）とは別物なので分けて名付ける
        return "snap_flag_mixed"
    return "not_reusable"


def _binary_of(gray_cache: dict, template: Template, aligned_dir: Path,
               page_id: str, face_id: str) -> object | None:
    """丸印の採点に使う二値化画像（(ページ, 面) 単位でキャッシュ）。

    二値化に渡す face は**吸着前**（`template` 側）——除外領域は紙に固定された
    マスクで、ブロックの罫線とは無関係（08 §6 判断 2）。ON/OFF の両側で同じ
    画像を使うので、キャッシュは 2 度目の呼び出しでそのまま効く。
    """
    key = (page_id, face_id)
    if key in gray_cache:
        return gray_cache[key]
    import numpy as np
    from PIL import Image

    from .align import binarize_face
    p = aligned_dir / f"{page_id}_{face_id}.png"
    if not p.exists():
        gray_cache[key] = None
        return None
    with Image.open(p) as im:
        gray = np.asarray(im.convert("L"))
    binary = binarize_face(gray, template.face(face_id), dpi=template.render_dpi)
    gray_cache[key] = binary
    return binary


def _values_of(tpl: Template, template: Template, page: dict, symbols_by_face: dict,
               cfg: Config, aligned_dir: Path, gray_cache: dict,
               missing: set) -> list:
    """片側（吸着前 or 吸着後の `tpl`）の出力列の値を組み立てる。

    `run`／`remap` と同じ 3 段（`mapping.assign` → 丸印の再スコア →
    `render_rows.build_row`）を通す。`build_row` まで通すのは、〓の判定・
    元号の 5 値・金額の正規化まで含めて「出力に出る形」で比べるため——
    セルの生テキストだけを比べると、丸印の判定が動いた差分を取り落とす。
    """
    from . import era
    from .mapping import assign
    from .pipeline import _extras_rows
    from .render_rows import build_row

    result = assign(tpl.cells, symbols_by_face, tpl.faces, dpi=tpl.render_dpi)
    cells: dict[str, tuple] = {}
    era_scores: dict[str, dict] = {}
    for cell in tpl.cells:
        content = result.cells.get(cell.field_id)
        is_empty = (cell.table_id, cell.row_no) in result.empty_rows
        cells[cell.field_id] = (content.text if content else "",
                                content.conf_min if content else None,
                                cell.kind, is_empty)
        if cell.kind != "choice" or is_empty:
            continue
        binary = _binary_of(gray_cache, template, aligned_dir,
                            page["page_id"], cell.face_id)
        if binary is None:
            # 採点し直せない面。ON/OFF の**両側で同じように**落とすので差分は
            # 生まれない（片側だけ落とすと存在しない差分を作る）。件数だけ残す
            missing.add((page["page_id"], cell.face_id))
            continue
        era_scores[cell.field_id] = era.score_cell(binary, cell)
    extras = {fid: (cc, origin) for fid, cc, origin in _extras_rows(tpl, result)}
    return build_row(tpl, page, cells, era_scores, cfg, extras=extras).values


def scan(store: Store, template: Template, cfg: Config, aligned_dir: Path,
         *, page_id: str | None = None, limit: int = DEFAULT_LIMIT) -> DiffReport:
    """done ページごとに ON/OFF の出力列を比べる。**Store は読むだけ。**"""
    from .mapping import Symbol

    columns = extract_columns(template)
    pages = pages_snapped = diff_cells = 0
    diffs: list[CellDiff] = []
    missing: set = set()
    for row in store.pages():
        if row["state"] != "done":
            continue
        pid = row["page_id"]
        if page_id is not None and pid != page_id:
            continue
        pages += 1
        # 座標の作り方は 08 §6.3 の 2 行だけ（他の 4 経路と同一）
        t2 = snap.apply_snap(
            template, snap.from_store_rows(store.snap_geometry(pid)))
        if t2 is template:
            # 吸着後の座標がテンプレート座標と同一（未記録・fail-safe・
            # 適用量が全ブロック 0）。`apply_snap` が同一オブジェクトを返す
            # ことが「下流が 1 バイトも変わらない」ことの証明なので、
            # 計算せずに差分 0 と数えてよい（08 §6.7 不変条件 2）
            continue
        pages_snapped += 1
        page = dict(row)
        by_face: dict[str, list] = {f.face_id: [] for f in template.faces}
        for _seq, face_id, text, conf, x, y in store.tokens(pid):
            by_face.setdefault(face_id, []).append(Symbol(text, x, y, conf))
        gray_cache: dict = {}
        off = _values_of(template, template, page, by_face, cfg,
                         aligned_dir, gray_cache, missing)
        on = _values_of(t2, template, page, by_face, cfg,
                        aligned_dir, gray_cache, missing)
        for name, a, b in zip(columns, off, on):
            if a == b:
                continue
            diff_cells += 1
            if len(diffs) < limit:
                diffs.append(CellDiff(page_id=pid, column=name))
    return DiffReport(pages=pages, pages_snapped=pages_snapped,
                      cells_compared=pages * len(columns),
                      diff_cells=diff_cells, diffs=tuple(diffs),
                      aligned_missing=len(missing),
                      truncated=diff_cells > len(diffs))


def _refused(reason: str, error: str) -> dict:
    return {"event": "snap_diff", "ok": False, "reason": reason, "error": error}


def diff_event(template_path: str | Path, cfg: Config, *,
               page_id: str | None = None, limit: int = DEFAULT_LIMIT) -> dict:
    """`snap-diff` の 1 行分の JSON（CLI はこれを出すだけ）。

    拒否条件は 3 つ:

    - 中間データが無い／done ページが無い → 比べる材料が無い
    - **吸着 ON で作られた記録が 1 件も無い**（`no_snap_recorded`）。ON 側の座標は
      保存済みの結果を読むもので、画像から吸着をやり直さない——やり直すと
      `run` と同じコストが掛かり、「保存済み token に対して再計算」という
      FR-F40 の前提から外れる
    - 幾何・位置合わせ方式・吸着フラグが現在のテンプレート／方式と違う
      （`check_reusable`）。方式の違いを吸着の差として読むと判断を誤る
    """
    import json

    from .align import ALGO_VERSION, geometry_hash
    from .align import template_hash as _tpl_hash
    from .pipeline import check_reusable

    from .columns import validate_v1
    from .template import load_template
    raw = json.loads(Path(template_path).read_text(encoding="utf-8"))
    template = load_template(template_path)
    geo_hash, tpl_hash = geometry_hash(raw), _tpl_hash(raw)
    # `pipeline._load` を経由しないコマンドは template_loaded を自前で出す
    # （不変条件 A・Q-S1・FR-F50。`debug-images`・`diag-overflow` と同じ）
    log.info("template_loaded", template_hash=tpl_hash)
    # 列名の重複したテンプレートで比べると、どの列の差分か特定できない
    validate_v1(template)

    wd = Path(cfg.workdir)
    db = wd / "intermediate.sqlite"
    if not db.exists():
        return _refused("no_store",
                        "中間データが無い（run で処理してから実行する）")
    with tempfile.TemporaryDirectory(prefix="snap_diff_") as tmp:
        # 原本は開かない。開くだけで ALTER TABLE が走り、AC-F39 の
        # 「実行前後で該当行が同一」が壊れる
        with Store(copy_store(db, Path(tmp))) as store:
            rows = store.pages()
            if not any(p["state"] == "done" for p in rows):
                return _refused(
                    "no_pages",
                    "読み取り済みのページが無い（run で処理してから実行する）")
            if page_id is not None and not any(
                    p["page_id"] == page_id for p in rows):
                return _refused("page_not_found",
                                f"ページ '{page_id}' が中間データに無い")
            if 1 not in store.snap_flags():
                return _refused(
                    "no_snap_recorded",
                    "枠の自動合わせ（吸着）を有効にして読み取った記録が中間データに"
                    "無い。比べる相手が作られていないため実行しない"
                    "——`config.json` の snap_blocks を true にして `run` で"
                    "読み取ってから実行する（API 送信が発生する）")
            try:
                check_reusable(store, geo_hash, tpl_hash,
                               check_template=False, snap_enabled=True)
            except OperationRefused as e:
                return _refused(refusal_reason(store, geo_hash, tpl_hash), str(e))
            report = scan(store, template, cfg, wd / "aligned",
                          page_id=page_id, limit=limit)
    # ログには件数だけ（欄名・記入値は出さない・Q-S1）
    log.info("snap_diff", count=report.diff_cells)
    return {"event": "snap_diff", "ok": True,
            "pages": report.pages, "pages_snapped": report.pages_snapped,
            "cells_compared": report.cells_compared,
            "diff_cells": report.diff_cells,
            "aligned_missing": report.aligned_missing,
            "algo_version": ALGO_VERSION,
            "snap_blocks_config": bool(cfg.snap_blocks),
            "truncated": report.truncated,
            "diffs": [{"page_id": d.page_id, "column": d.column}
                      for d in report.diffs]}
