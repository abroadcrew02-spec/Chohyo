"""`run` の処理フロー F1〜F9（設計 §3.3）と `remap`・`render`（§6.7）。"""
from __future__ import annotations

import hashlib
import io
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from . import era, ingest, logging_safe as log, render_rows
from .align import AlignError, align_page, geometry_hash
from .columns import derive_columns, validate_v1
from .config import Config
from .mapping import assign, symbols_from_response, to_face_local
from .render_out import write_outputs
from .render_rows import Row, build_failure_row, build_row
from .store import Store
from .template import Template, load_template
from .vision_client import OcrClient, SendError, save_response

Progress = Callable[[dict], None]


@dataclass
class Summary:
    pages: int = 0
    rows: int = 0
    align_failed: int = 0
    api_calls: int = 0
    unclear_total: int = 0
    overflow: int = 0


def _png_bytes(img: "Image.Image") -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _store_path(cfg: Config) -> Path:
    return Path(cfg.workdir) / "intermediate.sqlite"


def _load(template_path: str | Path) -> tuple[Template, dict, str]:
    template = load_template(template_path)
    validate_v1(template)
    raw = json.loads(Path(template_path).read_text(encoding="utf-8"))
    return template, raw, geometry_hash(raw)


def _map_and_score(store: Store, template: Template, page_id: str,
                   resp: dict, aligned_faces) -> tuple[int, int]:
    """応答 → token 保存 → 割付 → cell/era 保存。(below, other, total) を返す。"""
    page_syms = symbols_from_response(resp)
    by_face = {f.face_id: to_face_local(f, page_syms) for f in template.faces}
    total_syms = sum(len(v) for v in by_face.values())

    store.replace_tokens(page_id, [
        (seq, fid, s.text, s.conf, s.x, s.y)
        for seq, (fid, s) in enumerate(
            (fid, s) for fid, syms in by_face.items() for s in syms)])

    result = assign(template.cells, by_face, template.faces)

    cell_rows = []
    for cell in template.cells:
        content = result.cells.get(cell.field_id)
        is_empty = (cell.table_id, cell.row_no) in result.empty_rows
        cell_rows.append((cell.field_id,
                          content.text if content else "",
                          content.conf_min if content else None,
                          cell.kind, int(is_empty)))
    store.upsert_cells(page_id, cell_rows)

    binaries = {f.face_id: f.binary for f in aligned_faces}
    era_scores: dict[str, dict] = {}
    for cell in template.cells:
        if cell.kind != "choice":
            continue
        if (cell.table_id, cell.row_no) in result.empty_rows:
            continue  # 空行に丸印判定を走らせない（要件 §5.4）
        era_scores[cell.field_id] = era.score_cell(binaries[cell.face_id], cell)
    store.upsert_eras(page_id, era_scores)

    store.set_unassigned(page_id, result.unassigned_below_table, result.unassigned_other)
    return result.unassigned_below_table, result.unassigned_other, total_syms


def run(input_dir: str | Path, template_path: str | Path, cfg: Config,
        client: OcrClient, progress: Progress = lambda e: None) -> Summary:
    template, _raw, geo_hash = _load(template_path)
    store = Store(_store_path(cfg))
    store.record_run(time.strftime("%Y%m%d_%H%M%S"), json.dumps(cfg.__dict__))
    pages_dir = Path(cfg.workdir) / "pages"
    aligned_dir = Path(cfg.workdir) / "aligned"
    aligned_dir.mkdir(parents=True, exist_ok=True)

    # --- F1/F2: 列挙・展開（画像を書き終えてから page 行 INSERT・§12-C9）---
    taken: set[str] = set()
    for source in ingest.list_inputs(input_dir):
        # 同一内容の二重投入検知（要件 §5.1 Could）: 別名で同じ中身なら追加しない
        digest = hashlib.sha1(source.read_bytes()).hexdigest()
        seen_as = store.known_source(digest)
        if seen_as is not None and seen_as != source.name:
            log.info("skip_duplicate_content", source_file=source.name,
                     duplicate_of=seen_as)
            progress({"event": "skip_duplicate", "file": source.name,
                      "same_as": seen_as})
            continue
        try:
            page_images = ingest.expand(source, template.render_dpi, pages_dir)
        except ingest.IngestError as e:
            pid = ingest.page_id_for(source, 1, taken)
            taken.add(pid)
            store.upsert_page(pid, source.name, 1, "failed")
            store.set_status(pid, render_rows.STATUS_EXPAND_FAILED)
            log.error("expand_failed", source_file=source.name, error_code=e.code)
            continue
        store.record_source(digest, source.name)
        for i, img_path in enumerate(page_images, start=1):
            pid = ingest.page_id_for(source, i, taken)
            taken.add(pid)
            existing = store.page(pid)
            if existing and existing["state"] == "done":
                continue  # 再開規則: 処理済みは再送信しない（要件 §5.8）
            store.upsert_page(pid, source.name, i, "expanded", str(img_path))

    todo = [p for p in store.pages() if p["state"] != "done"]
    total = len(store.pages())
    progress({"event": "start", "total": total, "todo": len(todo)})

    summary = Summary(pages=total)
    sends = 0
    for page in todo:
        pid = page["page_id"]
        if page["state"] == "failed" and page["status"] == render_rows.STATUS_EXPAND_FAILED:
            continue
        try:
            img = Image.open(page["image_path"])
        except Exception:
            store.set_state(pid, "failed")
            store.set_status(pid, render_rows.STATUS_EXPAND_FAILED)
            continue

        # --- F3/F4/F5: 切り出し・位置合わせ・再結合 ---
        try:
            faces, composite = align_page(img, template)
        except AlignError:
            store.set_state(pid, "failed")
            store.set_status(pid, render_rows.STATUS_ALIGN_FAILED)
            summary.align_failed += 1
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_ALIGN_FAILED})
            continue
        for f in faces:
            store.upsert_alignment(pid, f.face_id, {"angle": f.angle}, True, geo_hash)
            f.image.save(aligned_dir / f"{pid}_{f.face_id}.png")
        store.set_state(pid, "aligned")

        # --- F6: 送信（上限・1リクエスト=1画像）---
        if sends >= cfg.send_limit:
            store.set_status(pid, render_rows.STATUS_CAP)
            progress({"event": "page", "page_id": pid, "status": render_rows.STATUS_CAP})
            continue
        store.set_state(pid, "sending")
        store.bump_attempt(pid)
        sends += 1
        try:
            resp = client.annotate(_png_bytes(composite), pid)
        except SendError as e:
            store.set_state(pid, "failed")
            store.set_status(pid, render_rows.STATUS_SEND_FAILED)
            log.error("send_failed", page_id=pid, error_code=e.code)
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_SEND_FAILED})
            continue
        summary.api_calls += 1
        save_response(cfg.workdir, pid, resp)
        store.set_state(pid, "received")

        # --- F7/F8: 割付・丸印 ---
        below, other, total = _map_and_score(store, template, pid, resp, faces)

        # D-15: 枠外率が閾値超なら配置を信用できない → 様式不一致・全〓行へ
        # （母集団は below_table を除く。設計 §6.4）
        if total > 0 and other / total > render_rows.FORMAT_MISMATCH_RATIO:
            store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH)
            store.set_state(pid, "failed")
            log.error("format_mismatch", page_id=pid, count=other)
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_FORMAT_MISMATCH})
            continue

        store.set_status(pid, "")  # 成功: 失敗系ステータスを剥がす（超過は render で合成）
        store.set_state(pid, "done")
        if below >= render_rows.OVERFLOW_MIN_SYMBOLS:
            summary.overflow += 1
        progress({"event": "page", "page_id": pid, "status": "done"})

    # --- F9: 出力 ---
    xlsx, csvp, rows = render(template_path, cfg)
    summary.rows = len(rows)
    summary.unclear_total = sum(r.unclear_count for r in rows)
    progress({"event": "summary", "pages": summary.pages, "rows": summary.rows,
              "align_failed": summary.align_failed, "api_calls": summary.api_calls,
              "unclear_cells": summary.unclear_total, "overflow": summary.overflow,
              "xlsx": str(xlsx), "csv": str(csvp)})
    store.close()
    return summary


def render(template_path: str | Path, cfg: Config,
           timestamp: str | None = None) -> tuple[Path, Path, list[Row]]:
    """cell / era_score から再出力する（API 送信なし・要件 §5.8）。"""
    template, _raw, _gh = _load(template_path)
    columns = derive_columns(template)
    store = Store(_store_path(cfg))
    rows: list[Row] = []
    for page in store.pages():
        p = dict(page)
        if page["state"] == "done":
            rows.append(build_row(template, p, store.cells(page["page_id"]),
                                  store.era_scores(page["page_id"]), cfg))
        else:
            if not p.get("status"):
                p["status"] = render_rows.STATUS_INTERRUPTED
            rows.append(build_failure_row(template, p))
    ts = timestamp or time.strftime("%Y%m%d_%H%M%S")
    xlsx, csvp = write_outputs(cfg.output_dir, ts, columns, rows)
    store.close()
    return xlsx, csvp, rows


def remap(template_path: str | Path, cfg: Config) -> int:
    """保存済み token から cell を作り直す（テンプレートの非幾何変更後・§6.7）。

    幾何セクションが変わっていたら拒否して `run` を促す。
    """
    template, _raw, geo_hash = _load(template_path)
    store = Store(_store_path(cfg))
    stored = store.geometry_hashes()
    if stored and stored != {geo_hash}:
        store.close()
        raise SystemExit(
            "テンプレートの幾何セクション（render_dpi/image/record/faces.source/"
            "face_id/exclusions）が変わっている。token 座標が無効のため remap では"
            "処理できない——`run` で再送信する")

    n = 0
    aligned_dir = Path(cfg.workdir) / "aligned"
    for page in store.pages():
        if page["state"] != "done":
            continue
        pid = page["page_id"]
        by_face: dict[str, list] = {f.face_id: [] for f in template.faces}
        from .mapping import Symbol
        for _seq, face_id, text, conf, x, y in store.tokens(pid):
            by_face.setdefault(face_id, []).append(Symbol(text, x, y, conf))
        result = assign(template.cells, by_face, template.faces)
        cell_rows = []
        for cell in template.cells:
            content = result.cells.get(cell.field_id)
            is_empty = (cell.table_id, cell.row_no) in result.empty_rows
            cell_rows.append((cell.field_id,
                              content.text if content else "",
                              content.conf_min if content else None,
                              cell.kind, int(is_empty)))
        store.upsert_cells(pid, cell_rows)

        # choice_marks の変更に追従: 保存済み位置合わせ画像から環状帯を再スコア
        import numpy as np
        era_scores: dict[str, dict] = {}
        for cell in template.cells:
            if cell.kind != "choice":
                continue
            if (cell.table_id, cell.row_no) in result.empty_rows:
                continue
            img_p = aligned_dir / f"{pid}_{cell.face_id}.png"
            if not img_p.exists():
                continue
            gray = np.asarray(Image.open(img_p).convert("L"))
            from .align import binarize_face
            binary = binarize_face(gray, template.face(cell.face_id))
            era_scores[cell.field_id] = era.score_cell(binary, cell)
        store.upsert_eras(pid, era_scores)
        store.set_unassigned(pid, result.unassigned_below_table, result.unassigned_other)
        n += 1
    store.close()
    return n
