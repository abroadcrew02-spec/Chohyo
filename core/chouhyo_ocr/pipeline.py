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
from .pipeline_errors import OperationRefused
from .store import Store
from .template import Template, load_template
from .vision_client import (OcrClient, SendError, load_saved_response,
                            save_response)

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


def _warn_risky(risky: list[tuple[str, str]]) -> None:
    """危険接頭セルを app.log へ警告する（D-28）。値は書かない（§8.1）。

    出力の内容は一切変えない——CSV を Excel で直接開いたときだけ現れる危険で、
    値を書き換えるのは転記主義（§5.5）と §8-12 の xlsx↔csv 一致に反するため。
    """
    for page_id, field_id in risky:
        log.warn("csv_formula_risk", page_id=page_id, field_id=field_id)
    if risky:
        log.warn("csv_formula_risk_total", count=len(risky))


def check_reusable(store: Store, geo_hash: str, tpl_hash: str,
                   *, check_template: bool) -> None:
    """中間データが現テンプレート・現方式で作られたものかを検査する（#25）。

    不変条件: 出力は、その出力を組み立てたテンプレートと同一のテンプレートで
    作られた中間データからのみ生成する。remap は cell を作り直すので
    template_hash 不一致は当然（check_template=False）。初回（空）は通す。
    """
    from .align import ALGO_VERSION
    stored_geo = store.geometry_hashes()
    if stored_geo and stored_geo != {geo_hash}:
        store.close()
        raise OperationRefused(
            "テンプレートの幾何セクション（render_dpi/image/record/faces.source/"
            "face_id/exclusions）が変わっている。token 座標が無効のため"
            "——`run` で再処理する（API 送信が発生する）")
    stored_algo = store.algo_versions()
    if stored_algo and stored_algo != {ALGO_VERSION}:
        store.close()
        raise OperationRefused(
            "位置合わせ方式が更新されている。旧方式で作った中間データは"
            "再利用できない——`run` で再処理する（API 送信が発生する）")
    if check_template:
        stored_tpl = store.template_hashes() - {""}
        if stored_tpl and stored_tpl != {tpl_hash}:
            store.close()
            raise OperationRefused(
                "テンプレートが変わっている（欄・列の定義の変更）。"
                "割付が旧テンプレートのままのため——`remap` で割付をやり直す")


def _map_and_score(store: Store, template: Template, page_id: str,
                   resp: dict, aligned_faces) -> tuple[int, int, int, int]:
    """応答 → token 保存 → 割付 → cell/era 保存。

    (below, other, total, page_total) を返す。page_total は応答全体の symbol 数
    で、面内に1つも落ちなかったケース（total==0）を D-15 が素通りする穴を
    塞ぐために使う（issue #37）。
    """
    page_syms = symbols_from_response(resp)
    by_face = {f.face_id: to_face_local(f, page_syms) for f in template.faces}
    total_syms = sum(len(v) for v in by_face.values())
    page_total = len(page_syms)

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
    return (result.unassigned_below_table, result.unassigned_other,
            total_syms, page_total)


def run(input_dir: str | Path, template_path: str | Path, cfg: Config,
        client: OcrClient, progress: Progress = lambda e: None,
        resend_on_template_change: bool = False) -> Summary:
    """一括処理。同一 workdir の多重起動はロックで断る（issue #35）。"""
    from .runlock import RunLock, RunLockError
    lock = RunLock(cfg.workdir)
    try:
        lock.acquire()
    except RunLockError as e:
        raise OperationRefused(str(e)) from None
    try:
        return _run_locked(input_dir, template_path, cfg, client, progress,
                           resend_on_template_change)
    finally:
        lock.release()


def _run_locked(input_dir: str | Path, template_path: str | Path, cfg: Config,
                client: OcrClient, progress: Progress,
                resend_on_template_change: bool) -> Summary:
    template, raw, geo_hash = _load(template_path)
    from .align import ALGO_VERSION, template_hash as _template_hash
    tpl_hash = _template_hash(raw)
    store = Store(_store_path(cfg))
    store.record_run(time.strftime("%Y%m%d_%H%M%S"), json.dumps(cfg.__dict__))

    # プリフライト（#25）: テンプレート・位置合わせ方式が変わっていたら、
    # API を1回も叩く前に止める。要配慮個人情報の再送は明示オプトインのみ
    # ——テンプレ編集の副作用で数百ページを黙って再開示・再課金しない
    outdated_pages = store.stale_done_pages(geo_hash, tpl_hash, ALGO_VERSION)
    if outdated_pages:
        if not resend_on_template_change:
            store.close()
            raise OperationRefused(
                f"テンプレートまたは位置合わせ方式が変わっている"
                f"（対象 {len(outdated_pages)} ページ）。"
                "再処理には API 送信（課金）が発生するため中止した。"
                "旧テンプレートへ戻す／`purge --yes` で作り直す／"
                "`run --resend-on-template-change` で対象ページのみ再送する のいずれかを選ぶ")
        for pid in outdated_pages:
            store.set_state(pid, "pending")  # 降格して再処理（send_limit は従来どおり効く）
        log.error("resend_on_template_change", count=len(outdated_pages))
        progress({"event": "template_changed_resend",
                  "count": len(outdated_pages)})
    pages_dir = Path(cfg.workdir) / "pages"
    aligned_dir = Path(cfg.workdir) / "aligned"
    aligned_dir.mkdir(parents=True, exist_ok=True)

    # --- F1/F2: 列挙・展開（画像を書き終えてから page 行 INSERT・§12-C9）---
    # 採番は DB 上の既存 ID も避ける（レビュー H-A）。実行内だけの集合だと、
    # 別ファイルが持っている ID を奪って UNIQUE(source_file,page_no) と衝突する
    taken: set[str] = store.all_page_ids()
    skipped_files: list[str] = []
    inputs = ingest.list_inputs(input_dir, skipped_files)
    if skipped_files:
        # 対象外ファイルを進捗イベントへ出す（レビュー M-2）。ログだけだと
        # 利用者には「total=0 の正常終了」にしか見えない
        log.info("skip_unsupported_total", count=len(skipped_files))
        progress({"event": "skipped_unsupported", "count": len(skipped_files),
                  "files": skipped_files[:5]})
    for source in inputs:
        # 同一内容の二重投入検知（要件 §5.1 Could）: 別名で同じ中身なら送信しない。
        # ただし黙って落とさず「スキップ（重複）」の全〓行を出す（#29 B-2・
        # PM/architect 裁定 2026-08-28: 投入した紙が黙って消えると §3.4 の
        # 行数保存が破れ、正本がソート順次第で落ちる）
        digest = hashlib.sha1(source.read_bytes()).hexdigest()
        # 同じ名前で中身が変わっていたら、そのファイルの旧データを捨てる
        # （レビュー H-B）。旧実装は hash→name の一方向しか持たず、差し替えても
        # 再送されずに**旧値が「正常」として出続けていた**（実測）。ページ数が
        # 減る差し替えでは余った行が幽霊として残り、stale 検知にもかからない
        prev = store.hash_of_source(source.name)
        if prev is not None and prev != digest:
            dropped = store.drop_pages_of(source.name)
            store.forget_source(source.name)
            log.info("source_content_changed", source_file=source.name,
                     count=dropped)
            progress({"event": "source_replaced", "file": source.name,
                      "dropped_pages": dropped})
        seen_as = store.known_source(digest)
        if seen_as is not None and seen_as != source.name:
            log.info("skip_duplicate_content", source_file=source.name,
                     duplicate_of=seen_as)
            progress({"event": "skip_duplicate", "file": source.name,
                      "same_as": seen_as})
            # ページ数は正本の page 行から求める（内容同一なので等しい。
            # pdftoppm を再実行しない）。正本が未展開なら最低1行は出す
            n_pages = sum(1 for p in store.pages()
                          if p["source_file"] == seen_as) or 1
            for i in range(1, n_pages + 1):
                pid = (store.page_id_of(source.name, i)
                       or ingest.page_id_for(source, i, taken))
                taken.add(pid)
                store.upsert_page(pid, source.name, i, "skipped_duplicate")
                store.set_status(pid, render_rows.STATUS_DUPLICATE)
            continue
        try:
            page_images = ingest.expand(source, template.render_dpi, pages_dir)
        except ingest.IngestError as e:
            pid = (store.page_id_of(source.name, 1)
                   or ingest.page_id_for(source, 1, taken))
            taken.add(pid)
            store.upsert_page(pid, source.name, 1, "failed")
            store.set_status(pid, render_rows.STATUS_EXPAND_FAILED)
            log.error("expand_failed", source_file=source.name, error_code=e.code)
            continue
        store.record_source(digest, source.name)
        for i, img_path in enumerate(page_images, start=1):
            # 既存行があればその page_id を使い続ける（H-A）
            pid = (store.page_id_of(source.name, i)
                   or ingest.page_id_for(source, i, taken))
            taken.add(pid)
            existing = store.page(pid)
            if existing and existing["state"] == "done":
                continue  # 再開規則: 処理済みは再送信しない（要件 §5.8）
            if existing and existing["state"] == "received":
                # 応答は取得済み（受信後・割付前で中断）。ここで expanded へ
                # 戻すと保存済み応答を使える条件が消え、再送＝再課金になる
                # （issue #38）。画像パスだけ更新して進捗は保つ
                store.set_image_path(pid, str(img_path))
                continue
            store.upsert_page(pid, source.name, i, "expanded", str(img_path))

    # 今回の入力に無いページが中間データに残っていれば可視化する（issue #28）。
    # render は store の全ページを出力するため、消えた入力の行が黙って
    # Excel に残り続ける——検知だけでも見えるようにする（削除は purge のみ）
    all_pages = store.pages()
    input_names = {s.name for s in inputs}
    missing_inputs = sorted({p["source_file"] for p in all_pages
                             if p["source_file"] not in input_names})
    if missing_inputs:
        log.error("stale_pages", count=len(missing_inputs))
        progress({"event": "stale_pages", "count": len(missing_inputs),
                  "files": missing_inputs[:5]})

    # skipped_duplicate は処理対象外（送信しない・行だけ出す。image_path も無い）
    todo = [p for p in all_pages
            if p["state"] not in ("done", "skipped_duplicate")]
    page_count = len(all_pages)
    progress({"event": "start", "total": page_count, "todo": len(todo)})

    summary = Summary(pages=page_count)
    sends = 0
    for page in todo:
        pid = page["page_id"]
        # 進捗イベントを出さずに continue すると、todo に数えたページの分だけ
        # バーが埋まらず「4/5」で完了する（レビュー M-7）。失敗も1件として進める
        if page["state"] == "failed" and page["status"] == render_rows.STATUS_EXPAND_FAILED:
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_EXPAND_FAILED})
            continue
        try:
            img = Image.open(page["image_path"])
        except Exception:
            store.set_state(pid, "failed")
            store.set_status(pid, render_rows.STATUS_EXPAND_FAILED)
            log.error("open_failed", page_id=pid)
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_EXPAND_FAILED})
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
            store.upsert_alignment(
                pid, f.face_id,
                {"angle": f.angle, "dx": f.dx, "dy": f.dy,
                 "matched": f.shift_matched},
                True, geo_hash, ALGO_VERSION)
            # 位置合わせ画像はローカル中間データ（remap の再スコア用）で
            # 配布物ではない。圧縮率を下げてエンコード時間を優先する
            # （実測: level 6 で 0.35s/枚 → level 1 で 0.22s/枚・容量は +1MB 程度）
            f.image.save(aligned_dir / f"{pid}_{f.face_id}.png", compress_level=1)
        store.set_state(pid, "aligned")

        # --- F6: 送信（上限・1リクエスト=1画像）---
        if sends >= cfg.send_limit:
            store.set_status(pid, render_rows.STATUS_CAP)
            progress({"event": "page", "page_id": pid, "status": render_rows.STATUS_CAP})
            continue
        # 保存済み応答があれば再送しない（issue #38）。受信後・割付前で落ちた
        # ページは応答を持っているので、再実行のたびに送り直すのは課金の無駄。
        # vision_client の docstring が約束していた契約をここで実装する
        saved = load_saved_response(cfg.workdir, pid)
        if saved is not None and page["state"] == "received":
            resp = saved
            log.info("reuse_saved_response", page_id=pid)
        else:
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
        # 応答の構造異常で落ちても received のまま宙に浮かせない（issue #38）。
        # 浮かせると次回実行で再送対象になり、実行のたびに課金が発生する
        try:
            below, other, total, page_total = _map_and_score(
                store, template, pid, resp, faces)
        except Exception as e:  # noqa: BLE001
            store.set_state(pid, "failed")
            store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH)
            log.error("map_failed", page_id=pid, error_code=type(e).__name__)
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_FORMAT_MISMATCH})
            continue

        # D-15: 配置を信用できないページは様式不一致・全〓行へ（設計 §6.4）。
        # ①応答に symbol が1つも無い（印字ラベルすら検出されない＝白紙か壊れた
        # 応答。実測で正常ページは常に 70+ の印字ラベル symbol を含む）
        # ②面内に1つも落ちない（全部が面外＝座標系が合っていない）
        # ③枠外率が閾値超（母集団は below_table を除く）
        # ①②を入れる前は total==0 でガードごと素通りし「正常なのに212列中200列
        # が空白」になっていた（issue #37 実測）
        mismatch = (page_total == 0 or total == 0
                    or other / total > render_rows.FORMAT_MISMATCH_RATIO)
        if mismatch:
            store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH)
            store.set_state(pid, "failed")
            log.error("format_mismatch", page_id=pid, count=other)
            progress({"event": "page", "page_id": pid,
                      "status": render_rows.STATUS_FORMAT_MISMATCH})
            continue

        store.set_status(pid, "")  # 成功: 失敗系ステータスを剥がす（超過は render で合成）
        store.set_template_hash(pid, tpl_hash)  # この cell を割り付けた版の印（#25）
        store.set_state(pid, "done")
        if below >= render_rows.OVERFLOW_MIN_SYMBOLS:
            summary.overflow += 1
        progress({"event": "page", "page_id": pid, "status": "done"})

    # --- F9: 出力 ---
    # ロック内から呼ぶので内側（ロックを取らない側）を使う——render() を
    # 呼ぶと自分が持っているロックに弾かれる
    xlsx, csvp, rows = _render_locked(template_path, cfg, None)
    summary.rows = len(rows)
    summary.unclear_total = sum(r.unclear_count for r in rows)
    # 危険接頭セルの件数（D-28）。**サマリ6項目（§5.9 Must）には足さない**——
    # 出荷ゲート（要確認セル数の合計0）にも載せない。載せると作業者が
    # ゲートを閉じるために正しい値を書き換える圧力になる
    from .render_out import scan_risky_prefixes
    risky = scan_risky_prefixes(derive_columns(template), rows)
    progress({"event": "summary", "pages": summary.pages, "rows": summary.rows,
              "align_failed": summary.align_failed, "api_calls": summary.api_calls,
              "unclear_cells": summary.unclear_total, "overflow": summary.overflow,
              "risky_cells": len(risky),
              "xlsx": str(xlsx), "csv": str(csvp)})
    store.close()
    return summary


def render(template_path: str | Path, cfg: Config,
           timestamp: str | None = None) -> tuple[Path, Path, list[Row]]:
    """cell / era_score から再出力する（API 送信なし・要件 §5.8）。

    run と同じロックを取る（レビュー L-5）。一時ファイル名が固定なので、
    同一秒に2つの render が走ると互いの tmp をすり替えうる。
    """
    from .runlock import RunLock, RunLockError
    lock = RunLock(cfg.workdir)
    try:
        lock.acquire()
    except RunLockError as e:
        raise OperationRefused(str(e)) from None
    try:
        return _render_locked(template_path, cfg, timestamp)
    finally:
        lock.release()


def _render_locked(template_path: str | Path, cfg: Config,
                   timestamp: str | None) -> tuple[Path, Path, list[Row]]:
    template, raw, geo_hash = _load(template_path)
    from .align import template_hash as _tpl_hash
    columns = derive_columns(template)
    store = Store(_store_path(cfg))
    # 出力を1バイトも書く前に、中間データが現テンプレートの産物かを検査（#25）
    check_reusable(store, geo_hash, _tpl_hash(raw), check_template=True)
    rows: list[Row] = []
    build_failures: list[str] = []
    for page in store.pages():
        p = dict(page)
        if page["state"] == "done":
            # 1ページの破損がバッチ全体の出力を失わせない（issue #39）。
            # 中間データに型不正が残っていた場合、旧実装は render/remap/run の
            # どれを叩いても同じ箇所で落ち、送信済み（＝課金済み）の正常ページも
            # 二度と取り出せなかった（回復手段は purge のみだった）
            try:
                rows.append(build_row(template, p, store.cells(page["page_id"]),
                                      store.era_scores(page["page_id"]), cfg))
                continue
            except Exception as e:  # noqa: BLE001
                import traceback
                # 型名だけだと自コードのバグが全ページ「様式不一致」に化け、
                # 利用者はテンプレートを疑う（レビュー M-2）。スタックは
                # error.log へ（frame のみ・記入値は含まない）
                log.error("row_build_failed", page_id=page["page_id"],
                          error_code=type(e).__name__)
                log.error_trace(type(e).__name__,
                                "".join(traceback.format_tb(e.__traceback__)))
                p["status"] = render_rows.STATUS_FORMAT_MISMATCH
                rows.append(build_failure_row(template, p))
                build_failures.append(page["page_id"])
                continue
        else:
            if not p.get("status"):
                p["status"] = render_rows.STATUS_INTERRUPTED
            rows.append(build_failure_row(template, p))
    ts = timestamp or time.strftime("%Y%m%d_%H%M%S")
    try:
        xlsx, csvp, risky = write_outputs(cfg.output_dir, ts, columns, rows)
    except Exception:
        store.close()   # 出力に失敗しても接続を残さない（レビュー L-6）
        raise
    _warn_risky(risky)
    if build_failures:
        # 全ページ破損＝コード／テンプレの問題で、1ページの破損とは意味が違う
        # （レビュー M-1）。旧実装は件数をどこにも出さず exit 0 だった
        done = [p for p in store.pages() if p["state"] == "done"]
        log.error("row_build_failed_total", count=len(build_failures))
        if done and len(build_failures) == len(done):
            raise OperationRefused(
                f"処理済みページ {len(done)} 件すべてで行の組み立てに失敗した。"
                "テンプレートと中間データの整合を確認する（詳細は error.log）")
    store.close()
    return xlsx, csvp, rows


def remap(template_path: str | Path, cfg: Config,
          progress: Progress = lambda e: None) -> int:
    """保存済み token から cell を作り直す（テンプレートの非幾何変更後・§6.7）。

    幾何セクションが変わっていたら拒否して `run` を促す。
    """
    template, raw, geo_hash = _load(template_path)
    from .align import template_hash as _tpl_hash
    tpl_hash = _tpl_hash(raw)
    store = Store(_store_path(cfg))
    check_reusable(store, geo_hash, tpl_hash, check_template=False)

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
        missing_aligned = 0
        for cell in template.cells:
            if cell.kind != "choice":
                continue
            if (cell.table_id, cell.row_no) in result.empty_rows:
                continue
            img_p = aligned_dir / f"{pid}_{cell.face_id}.png"
            if not img_p.exists():
                # 再スコアできない。無言スキップすると旧スコア残置と区別が
                # つかない（issue #28）——総入れ替えで旧スコアは消え〓へ倒れる
                # ので誤値は出ないが、件数を可視化する
                missing_aligned += 1
                continue
            gray = np.asarray(Image.open(img_p).convert("L"))
            from .align import binarize_face
            binary = binarize_face(gray, template.face(cell.face_id))
            era_scores[cell.field_id] = era.score_cell(binary, cell)
        store.upsert_eras(pid, era_scores)
        store.set_template_hash(pid, tpl_hash)  # 割付し直した版の印（#25）
        if missing_aligned:
            log.error("remap_missing_aligned", page_id=pid, count=missing_aligned)
            progress({"event": "remap_warning", "page_id": pid,
                      "missing_aligned_cells": missing_aligned})
        store.set_unassigned(pid, result.unassigned_below_table, result.unassigned_other)
        n += 1
    store.close()
    return n
