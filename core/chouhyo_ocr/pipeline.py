"""`run` の処理フロー F1〜F9（設計 §3.3）と `remap`・`render`（§6.7）。"""
from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from . import era, format_check, ingest, logging_safe as log, render_rows, snap
from .align import (AlignedFace, AlignError, PageSizeMismatch, align_page,
                    geometry_hash, page_size_verdict)
from .columns import META_COLUMNS, derive_columns, validate_v1
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
    # 実機の通し確認で判明（コーディネーター指示 2026-09-02）。同じ workdir で
    # 既に done（前回の run で処理・送信済み）なページは todo から外れて無言で
    # 再利用され、api_calls だけでは「なぜ入力枚数より送信数が少ないか」が
    # サマリから読めなかった。skip_duplicate 同様の可視化として、今回の run で
    # 再利用（未送信）だったページ数をここに積む（既存の集計は変えない）
    reused_pages: int = 0
    align_failed: int = 0
    # 様式不一致（STATUS_FORMAT_MISMATCH）の総件数（原因を問わない・Q-H1）。
    # cli.cmd_run の「全滅なら exit 1」判定の母集団に含める——align_failed だけ
    # 見ていると、PageSizeMismatch や _map_and_score の構造異常で全ページが
    # 様式不一致に倒れた実行が exit 0（成功扱い）になってしまう
    format_mismatch: int = 0
    # FR-F10・issue #71 (a')。format_mismatch のうち FR-F01（様式判定）由来で
    # 送信前に止まった件数の内訳（原因不問の format_mismatch とは別に持つ・
    # 08 §2.4.3）。GUI（RunScreen.tsx）がこのキー名で参照する
    format_mismatch_pre_send: int = 0
    # #53 L-9。今回の run が実際に処理したページ数（todo の件数）と、そのうち
    # 失敗として確定した件数。cli.cmd_run の終了コード判定はこの2つだけを見る
    # ——旧実装は store 全体の出力行数（過去の run が作った done 行を含む）と
    # 今回の失敗数を比べていたため、**今回の入力が全滅しても過去の行が残って
    # いれば exit 0** になっていた。送信上限で見送ったページはどちらにも
    # 入らない（失敗ではなく「まだ送っていない」）ため、
    # processed_pages - processed_failed が成功とは限らない
    processed_pages: int = 0
    processed_failed: int = 0
    api_calls: int = 0
    unclear_total: int = 0
    overflow: int = 0
    # U-04/U-07（設計 §10.3・2026-08-31）。サマリ6項目（§5.9 Must）には数えない
    # ——risky_cells と同じ扱いの追加の可視化項目（出荷ゲートには載せない）
    fallback_used: int = 0
    fallback_discarded: int = 0
    carve_hole: int = 0
    # issue #92。実行開始時に「応答は保存済みなのに state が sending」だった
    # ページを received へ戻した件数（＝再送＝再課金を止めた件数）。
    # 既存の集計キーは変えない
    recovered_responses: int = 0
    # issue #66 段2（FR-1.4・AC-1.10）。上記3件のうち output: false の欄が
    # 発火元のものだけの内訳（総数からは減らさない・値は記入値を含まない）
    fallback_discarded_excluded_field: int = 0
    carve_hole_excluded_field: int = 0
    conflict_excluded_field: int = 0
    # issue #75 (f)・FR-F41。**2つを1つの数字に混ぜない**——原因が違う。
    # snap_failsafe_pages は入力画像由来（罫線のかすれ・吸着後の重なり）で
    # 毎回変わり、snap_excluded_pages はテンプレート定義由来（期待横線 4 本
    # 以下の表）で毎回同じ件数になる。単位はページ（面ではない）で、1ページを
    # 両方に数えない（excluded を優先・08 §6 判断4-H）
    snap_failsafe_pages: int = 0
    snap_excluded_pages: int = 0


def _png_bytes(img: "Image.Image") -> bytes:
    """送信用 PNG エンコード（issue #52 M-15）。

    実測（2026-09-03・実際の送信画像＝align_page の composite 2490×3510 RGB・
    各3回・Pillow 12.3.0）:

        level 0: 25.02MB / 0.932s   level 1: 3.31MB / 0.703s
        level 3:  2.36MB / 0.890s   level 6:  2.26MB / 1.063s（=未指定と同一）

    速いのは低圧縮だが、この出力は**ネットワークへ出る**ので、縮まない分だけ
    アップロード時間が増える。損益分岐の上り速度は level 3 で約 4.6Mbps
    （+0.10MB を 0.173s 以内に送れるか）、level 1 で約 23Mbps（+1.05MB を
    0.360s 以内）——通信環境が不明な以上、level 1 は賭けになる。

    それでも level 6（=現状）を選ぶ理由は、速さより**再送＝再課金**の側にある:
    保存済み応答のサイドカーは「送信したバイト列の sha256」を持ち（issue #92）、
    圧縮率を変えると同じ画像でもバイト列が変わって全件ハッシュ不一致
    ——既存 workdir の受信済みページが一斉に再送になる。ハッシュ対象を
    エンコード前のピクセル（img.tobytes()）へ移せばこの罠は消えるが、
    実測で tobytes 123ms + sha256 185ms = 308ms/枚（PNG バイト列なら 19ms）で、
    level 3 の節約 173ms を食い潰して逆に遅い。

    未指定のままにせず level 6 を明示するのは、Pillow 側の既定値が将来
    変わったときに、こちらは何も変えていないのにサイドカーが全件不一致
    ——つまり黙って再課金——になるのを防ぐため（未指定と level 6 は
    バイト単位で同一なことを確認済み: sha256 85eea166… 2,372,063 bytes・
    2026-09-03）。level 3 へ動かすなら、サイドカーに圧縮率を記録して
    旧レベルで再計算して照合する仕組みが要る（vision_client 側の変更）。
    """
    buf = io.BytesIO()
    img.save(buf, format="PNG", compress_level=6)
    return buf.getvalue()


def _serialize_char_confs(confs: tuple[float, ...]) -> str:
    """char_confs（symbol 単位の信頼度タプル）→ store.cell.char_confs の書式
    （カンマ区切り小数3桁・設計 §3 U-04・§10.1）。

    空タプルは空文字列——render_rows._parse_char_confs 側で「情報なし」として
    安全側（文字単位〓を適用せず欄全体〓）へ倒れる（設計 §14 不変条件2）。
    """
    return ",".join(f"{c:.3f}" for c in confs)


def _extras_rows(template: Template, result) -> list[tuple[str, str, str]]:
    """mapping.MappingResult → store.upsert_cell_extras() の rows 形式。

    run（_map_and_score）と remap の両方で同じ変換を使う——片方だけ直すと
    再割付のたびに char_confs/origin が消える（U-04/#62 の設計 §12「remap にも
    同じ変更が要る」を満たす）。
    """
    rows: list[tuple[str, str, str]] = []
    for cell in template.cells:
        content = result.cells.get(cell.field_id)
        rows.append((
            cell.field_id,
            _serialize_char_confs(content.char_confs) if content else "",
            content.origin if content else "",
        ))
    return rows


def _store_path(cfg: Config) -> Path:
    return Path(cfg.workdir) / "intermediate.sqlite"


def _recover_sent_pages(store: Store, cfg: Config) -> int:
    """応答は保存済みなのに state が sending のページを received へ戻す（issue #92）。

    送信の成功後は「応答を responses/ へ保存 → state を received に更新」の
    2ステップで、その間に落ちると**課金済みの応答を持ったまま state=sending**
    で残る。次の run はその state を見て再送する（＝同じページに二重課金）。
    実行開始時に一度だけ、応答が正常に読めるページの state を進めておく。

    ここでは画像ハッシュを照合しない。state を進めるだけで、実際に再利用して
    よいかは従来どおり送信直前の load_saved_response が決める（ハッシュが
    食い違えば、その場で再送に倒れる）。応答が無い・壊れているページは
    触らない＝従来どおり再送する。
    """
    recovered = 0
    for row in store.pages():
        if row["state"] != "sending":
            continue
        if load_saved_response(cfg.workdir, row["page_id"]) is None:
            continue
        store.set_state(row["page_id"], "received")
        log.info("recovered_saved_response", page_id=row["page_id"])
        recovered += 1
    if recovered:
        log.info("recovered_saved_response_total", count=recovered)
    return recovered


def _load(template_path: str | Path) -> tuple[Template, dict, str]:
    template = load_template(template_path)
    raw = json.loads(Path(template_path).read_text(encoding="utf-8"))
    # load_template 成功直後・validate_v1 より前に出す（2026-09-02 #77 追補・
    # マリン指摘）。W-1〜W-4 の cell_idx・face_idx は load_template 内部
    # （validate_v1 より前）で既に発火しているため、ここより後で
    # template_loaded を出すと validate_v1 が TemplateError で落ちたときに
    # 「cell_idx はあるが template_hash が無い」状態が残る（不変条件A・
    # Q-S1・FR-F50・08_frame_detection_design.md §1.4）。3経路（run／render／
    # remap）すべてがここを通るため一度だけ出せば足りる
    from .align import template_hash as _tpl_hash
    log.info("template_loaded", template_hash=_tpl_hash(raw))
    validate_v1(template)
    return template, raw, geometry_hash(raw)


def _warn_risky(risky: list[tuple[str, str]], columns: list[str]) -> None:
    """危険接頭セルを app.log へ警告する（D-28）。値は書かない（§8.1）。

    列名も書かない（Q-S1・FR-F50・2026-09-02）——`risky` の各要素は
    render_out.scan_risky_prefixes が返す (page_id, 列名) だが、列名は
    出力ファイル・GUI 向けの戻り値としては残る一方、ログには出さない。
    代わりに抽出対象列（columns から管理6列を除いた列）内の0始まりの
    序数を col_idx として残す。

    出力の内容は一切変えない——CSV を Excel で直接開いたときだけ現れる危険で、
    値を書き換えるのは転記主義（§5.5）と §8-12 の xlsx↔csv 一致に反するため。
    """
    extract_cols = columns[len(META_COLUMNS):]
    col_idx_by_name = {name: i for i, name in enumerate(extract_cols)}
    for page_id, name in risky:
        log.warn("csv_formula_risk", page_id=page_id,
                 col_idx=col_idx_by_name[name])
    if risky:
        log.warn("csv_formula_risk_total", count=len(risky))


def _record_format_result(store: Store, page_id: str, pv) -> None:
    """様式判定結果を中間データへ記録し、同じ内容を1行ログへも残す
    （FR-F12・AC-F13・08 §2.5.3・2026-09-02 マリン指摘 H-1）。

    永続化は store.set_format_result（DB のみ）に任せ、ここでログ出力の
    責務を足す——4つの呼び出し点（PageSizeMismatch／AlignError／再利用時の
    unknown／成功時）すべてがこの1関数を通ることで、ログの書き漏らしを
    構造的に防ぐ。pv が None（AC-F14: 判定関数自体が壊れた場合）は
    どちらも行わない。
    """
    store.set_format_result(page_id, pv)
    if pv is None:
        return
    log.info("format_verdict", page_id=page_id, verdict=pv.verdict,
             reason_code=pv.reason, score=pv.score,
             detected=pv.detected, expected=pv.expected)


def check_reusable(store: Store, geo_hash: str, tpl_hash: str,
                   *, check_template: bool, snap_enabled: bool) -> None:
    """中間データが現テンプレート・現方式で作られたものかを検査する（#25）。

    不変条件: 出力は、その出力を組み立てたテンプレートと同一のテンプレートで
    作られた中間データからのみ生成する。remap は cell を作り直すので
    template_hash 不一致は当然（check_template=False）。初回（空）は通す。

    store は閉じない（Q-MG）——「閉じるのは開いた側」に一本化した。全呼び出し元
    （_render_locked・_remap_locked・cli.cmd_debug_images・cli.cmd_diag_overflow）
    が `with Store(...) as store:` で包んでおり、ここで raise した例外は with の
    __exit__ が確実に close する。

    snap_enabled（issue #75・FR-F38）に**既定値を付けない**理由: 既定 False を
    付けると、配線し忘れた呼び出し元が「常に OFF として照合」＝素通りする
    （fail-open）。キーワード必須にすれば配線漏れが `TypeError` として実行前に
    落ちる。呼び出し元は3つしかないので保守コストは無い。
    """
    from .align import ALGO_VERSION
    stored_geo = store.geometry_hashes()
    if stored_geo and stored_geo != {geo_hash}:
        raise OperationRefused(
            "テンプレートの幾何セクション（render_dpi/image/record/faces.source/"
            "face_id/exclusions）が変わっている。token 座標が無効のため"
            "——`run` で再処理する（API 送信が発生する）")
    stored_algo = store.algo_versions()
    if stored_algo and stored_algo != {ALGO_VERSION}:
        raise OperationRefused(
            "位置合わせ方式が更新されている。旧方式で作った中間データは"
            "再利用できない——`run` で再処理する（API 送信が発生する）")
    # 4つ目のガード（issue #75・FR-F38・AC-F37）。既存3ハッシュが一致していても
    # ここで止まる——ON で作った座標を OFF の実行へ流用させない。既定 OFF で
    # 作った既存 workdir を OFF のまま使う場合は {0} == {0} で素通りする
    # （AC-F43b: 拒否されず Vision 0 回）
    stored_snap = store.snap_flags()
    if stored_snap and stored_snap != {int(snap_enabled)}:
        raise OperationRefused(
            "枠の自動合わせ（吸着）の設定が中間データと違う。"
            "吸着の有無で枠の位置が変わるため、設定を元に戻すか"
            "`run` で再処理する（API 送信が発生する）")
    if check_template:
        stored_tpl = store.template_hashes() - {""}
        if stored_tpl and stored_tpl != {tpl_hash}:
            raise OperationRefused(
                "テンプレートが変わっている（欄・列の定義の変更）。"
                "割付が旧テンプレートのままのため——`remap` で割付をやり直す")


# 「既に位置合わせ済み」を意味する state（#45）。done は todo に入らないので
# 不要。failed は入れない——位置合わせ失敗も failed なので、再整列に倒す側で扱う
_ALIGNED_STATES = frozenset({"aligned", "sending", "received"})


def _restore_alignment(store: Store, template: Template, aligned_dir: Path,
                       page_id: str, geo_hash: str, algo_version: str,
                       tpl_hash: str, *, snap_enabled: bool
                       ) -> tuple[list[AlignedFace], "Image.Image"] | None:
    """保存済みの位置合わせ結果から faces/composite を復元する（#45）。

    send_limit・月次上限による分割送信が通常運用のため、未送信ページは run の
    たびに再整列されていた（実測 約1.6s/ページ。send_limit=3・10ページの2回
    連続 run で2回目も7ページを再整列）。位置合わせは decode 済みの中間データで
    再現できるので、条件を満たすページは作り直さない。

    再利用できるのは、面ごとに ①alignment 行がある ②ok ③geometry_hash が
    現テンプレートと一致 ④algo_version が現コードと一致 ⑤template_hash が
    現テンプレートと一致 ⑥整列画像が実在し寸法が source.rect と一致
    ⑦吸着 ON/OFF が現在の設定と一致（issue #75・FR-F38・AC-F58）の
    **すべて** を満たす場合だけ。1つでも欠けたら None を返して再整列させる
    （古い定義を使い回して誤った値を出さない）。

    ⑦が要るのは、⑥までのゲートは吸着の有無を見ないため。ON で整列した面の
    保存済み座標（`transform["snap"]`）を OFF の実行が読み戻すと、設定を
    OFF にしたのに吸着後の枠で割り付ける。戻り値は**2要素のまま**にして、
    吸着量は呼び出し元が `store.snap_geometry()` から別に取る——再利用経路は
    課金に直結する #45 の資産で、契約を変えるほどの必要が無い（08 §6 判断3-E）。

    ⑤が要るのは、平行移動の探索アンカー（estimate_shift）が faces[].tables の
    blocks.origin / row_pitch / columns から作られるのに対し、geometry_hash は
    render_dpi / image / record / faces.{face_id,source,exclusions} しか見ない
    ため。罫線定義のズレ補正という最も普通のテンプレ編集が geometry_hash を
    変えないので、④までのゲートでは旧アンカーで求めた dx/dy を新しいセル定義に
    使ってしまう（実測: family blocks の origin.y を 5px 動かしたテンプレートで
    再実行しても align 呼び出しが 0 回のまま＝旧アンカーの位置合わせを再利用して
    いた。tests/test_review4_pipeline.py::test_table_change_forces_realign）。
    このゲートを厳しくしても API 送信は増えない——不成立時に起きるのは
    ローカルの再整列だけで、page の state も保存済み応答も触らないため、
    received ページは従来どおり保存済み応答を再利用する（issue #38）。

    二値は align_page と同じ binarize_face で作り直す。整列画像は除外領域を
    白で塗った RGB の PNG（可逆）で、Otsu の母集団も最終マスクも除外領域の
    外側だけ——align_page が作る binary と一致する。remap も同じ経路で
    環状帯を再スコアしている。
    """
    rows = store.alignments(page_id)
    if not rows:
        return None
    # ⑦ 吸着 ON/OFF の照合（FR-F38）。面が1つでも違う設定で作られていたら
    # 再整列へ倒す（部分的に混ざった座標を作らない）
    snap_rows = store.snap_geometry(page_id)
    for face in template.faces:
        rec = snap_rows.get(face.face_id)
        if rec is None or rec[1] != int(snap_enabled):
            return None
    import numpy as np

    from .align import binarize_face
    w_img, h_img = template.image_size
    composite = Image.new("RGB", (w_img, h_img), "white")
    faces: list[AlignedFace] = []
    for face in template.faces:
        rec = rows.get(face.face_id)
        if rec is None:
            return None
        transform, ok, geo, algo, tpl = rec
        if not ok or geo != geo_hash or algo != algo_version or tpl != tpl_hash:
            return None  # tpl=='' は旧版データ。証明できないものは作り直す
        img_path = aligned_dir / f"{page_id}_{face.face_id}.png"
        if not img_path.exists():
            return None
        r = face.source_rect
        try:
            with Image.open(img_path) as fh:
                img = fh.convert("RGB")
        except Exception:  # noqa: BLE001 — 壊れた中間データは再整列で作り直す
            return None
        if img.size != (r.w, r.h):
            return None
        binary = binarize_face(np.asarray(img.convert("L")), face, dpi=template.render_dpi)
        faces.append(AlignedFace(
            face.face_id, img, binary, float(transform.get("angle", 0.0)),
            dx=int(transform.get("dx", 0)), dy=int(transform.get("dy", 0)),
            shift_matched=int(transform.get("matched", 0))))
        composite.paste(img, (r.x, r.y))
    return faces, composite


def _map_and_score(store: Store, template: Template, page_id: str,
                   resp: dict, aligned_faces, *,
                   snap_by_face: "dict[str, snap.FaceSnap]"
                   ) -> tuple[int, int, int, int, int, int, int, int, int, int]:
    """応答 → token 保存 → 割付 → cell/era 保存。

    (below, other, total, page_total, fallback_used, fallback_discarded,
    carve_hole, fallback_discarded_excluded_field, carve_hole_excluded_field,
    conflict_excluded_field) を返す。page_total は応答全体の symbol 数で、
    面内に1つも落ちなかったケース（total==0）を D-15 が素通りする穴を塞ぐ
    ために使う（issue #37）。fallback_used/fallback_discarded/carve_hole は
    U-04/U-07 の件数（設計 §10.3）——呼び出し側が進捗イベント・run サマリへ
    出す。末尾3つは issue #66 段2（FR-1.4）: 上記のうち output: false の欄が
    発火元のものだけの内訳（MappingResult をそのまま素通しするだけで、
    ここでは判定しない——判定は mapping.assign() に集約済み）。

    snap_by_face（issue #75・FR-F37 の経路①）: 面ごとの吸着結果。冒頭で
    `apply_snap` を1回だけ通し、以降の割付・丸印判定は吸着後の `t2` を使う。
    吸着していないときは `t2 is template`（同一オブジェクト）になるので、
    OFF の挙動は1バイトも変わらない。
    ⚠️ `binarize_face` へ渡す face は**吸着前**のまま——除外領域は紙に固定
    されたマスクでブロックとは無関係（08 §6 判断2）。ここでは二値は
    aligned_faces が持っているものをそのまま使うので、その性質は保たれる。
    """
    t2 = snap.apply_snap(template, snap_by_face)
    page_syms = symbols_from_response(resp)
    by_face = {f.face_id: to_face_local(f, page_syms) for f in t2.faces}
    total_syms = sum(len(v) for v in by_face.values())
    page_total = len(page_syms)

    token_rows = [
        (seq, fid, s.text, s.conf, s.x, s.y)
        for seq, (fid, s) in enumerate(
            (fid, s) for fid, syms in by_face.items() for s in syms)]

    result = assign(t2.cells, by_face, t2.faces, dpi=t2.render_dpi)

    cell_rows = []
    for cell in t2.cells:
        content = result.cells.get(cell.field_id)
        is_empty = (cell.table_id, cell.row_no) in result.empty_rows
        cell_rows.append((cell.field_id,
                          content.text if content else "",
                          content.conf_min if content else None,
                          cell.kind, int(is_empty)))

    binaries = {f.face_id: f.binary for f in aligned_faces}
    era_scores: dict[str, dict] = {}
    for cell in t2.cells:
        if cell.kind != "choice":
            continue
        if (cell.table_id, cell.row_no) in result.empty_rows:
            continue  # 空行に丸印判定を走らせない（要件 §5.4）
        era_scores[cell.field_id] = era.score_cell(binaries[cell.face_id], cell)

    # 書き込みは計算を終えてから1トランザクションで（issue #93）。1ページ分の
    # 中間データが「token は新しいが cell は旧」のような中途半端な組み合わせで
    # 残らないようにする。割付・丸印判定はこの外側で終わっているので、
    # 書き込みロックを保持するのは SQLite の実行時間だけ。
    # 直後の set_status / set_template_hash / set_state（呼び出し側の成功時
    # 処理）は別トランザクションになるが、その間に落ちても state が done へ
    # 進んでいないため render の母集団にも check_reusable の母集団
    # （state='done'）にも入らない——次回の run が保存済み応答から割付を
    # やり直す（送信＝課金は発生しない）
    with store.transaction():
        store.replace_tokens(page_id, token_rows)
        store.upsert_cells(page_id, cell_rows)
        # U-04/#62: 文字単位信頼度・値の由来を cell_rows と同じ内容から作り、
        # 同じ page_id へ拡張列として保存する（store.cells() の戻り値は不変の
        # まま・設計 §10.2）
        store.upsert_cell_extras(page_id, _extras_rows(t2, result))
        store.upsert_eras(page_id, era_scores)
        store.set_unassigned(page_id, result.unassigned_below_table,
                             result.unassigned_other)
    return (result.unassigned_below_table, result.unassigned_other,
            total_syms, page_total,
            result.fallback_used, result.fallback_discarded, result.carve_hole,
            result.fallback_discarded_excluded_field,
            result.carve_hole_excluded_field, result.conflict_excluded_field)


@contextmanager
def _run_lock(cfg: Config):
    """workdir の実行ロックを取る（issue #35・#93）。

    取れなければ RunLockError を OperationRefused へ翻訳する——CLI は
    OperationRefused を「業務的な拒否」として exit 0 + refused イベントで
    扱う契約（cli.main）で、二重起動はまさにそれ。

    run / render / remap / remap_and_render の4経路が同じ形でこれを使う。
    remap だけロックを取っていなかったのが #93（共有 SQLite を無防備に
    書き換えていた）。
    """
    from .runlock import RunLock, RunLockError
    lock = RunLock(cfg.workdir)
    try:
        lock.acquire()
    except RunLockError as e:
        raise OperationRefused(str(e)) from None
    try:
        yield
    finally:
        lock.release()


def run(input_dir: str | Path, template_path: str | Path, cfg: Config,
        client: OcrClient, progress: Progress = lambda e: None,
        resend_on_template_change: bool = False) -> Summary:
    """一括処理。同一 workdir の多重起動はロックで断る（issue #35）。"""
    with _run_lock(cfg):
        return _run_locked(input_dir, template_path, cfg, client, progress,
                           resend_on_template_change)


def _run_locked(input_dir: str | Path, template_path: str | Path, cfg: Config,
                client: OcrClient, progress: Progress,
                resend_on_template_change: bool) -> Summary:
    template, raw, geo_hash = _load(template_path)
    from .align import ALGO_VERSION, template_hash as _template_hash
    tpl_hash = _template_hash(raw)
    # template_loaded（template_hash 付き）は _load() が出す（issue #59 H-7・
    # Q-S1・FR-F50・08_frame_detection_design.md §1.4）。run_start（cli.py）の
    # 時点ではテンプレートを読んでおらずハッシュが分からないため、算出できた
    # ここ（_load 経由）で別行に残る——「同じ run の中でテンプレートの特定は
    # できる」という契約は変えていない
    with Store(_store_path(cfg)) as store:
        store.record_run(time.strftime("%Y%m%d_%H%M%S"), json.dumps(cfg.__dict__))

        # プリフライト（#25）: テンプレート・位置合わせ方式が変わっていたら、
        # API を1回も叩く前に止める。要配慮個人情報の再送は明示オプトインのみ
        # ——テンプレ編集の副作用で数百ページを黙って再開示・再課金しない
        outdated_pages = store.stale_done_pages(geo_hash, tpl_hash, ALGO_VERSION,
                                                int(cfg.snap_blocks))
        if outdated_pages:
            if not resend_on_template_change:
                raise OperationRefused(
                    f"テンプレートまたは位置合わせ方式・枠の自動合わせの設定が変わっている"
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
        # 重複判定（#46）と stale 検知（#28）の両方が使うので、ループの前に1回だけ作る
        input_names = {s.name for s in inputs}
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
                # 消したページの ID は採番集合から外す（#53 L-5）。残したままだと
                # 差し替えたファイルの帳票 ID が「使用済み」を避けて
                # `<stem>_<hash8>_p0001` へ逃げる——旧行はもう無いので、
                # 元の `<stem>_p0001` をそのまま再利用してよい
                taken -= set(store.page_ids_of(source.name))
                dropped = store.drop_pages_of(source.name)
                store.forget_source(source.name)
                log.info("source_content_changed", source_file=source.name,
                         count=dropped)
                progress({"event": "source_replaced", "file": source.name,
                          "dropped_pages": dropped})
            seen_as = store.known_source(digest)
            if (seen_as is not None and seen_as != source.name
                    and seen_as not in input_names):
                # 改名（#46）: 同じ中身の元ファイルが今回の入力に **無い** なら、
                # 二重投入ではなく「同じ紙が改名された」。重複として扱うと
                # 「新しい名前＝スキップ（重複）行」と「古い名前＝stale 行」の2行が
                # 出て §3.4（入力ページ数＝出力行数）が破れる（実測: 要確認セル数が
                # 4→216 に跳ねる）。中間データを新しい名前へ付け替えて引き継ぐ
                # ——page_id は据え置くので再送信（課金）は発生しない
                states = store.states_of_source(source.name)
                if not states or states == {"skipped_duplicate"}:
                    # 改名先の名前に「スキップ（重複）」の空行だけが残っている場合も
                    # 付け替える（レビュー4巡目 MEDIUM）。空行は送信も割付もして
                    # いないので捨てて構わない——残すと UNIQUE(source_file, page_no)
                    # 違反を避けるために通常処理へ落ち、同じ紙をもう一度送る
                    # （課金）ことになる。実測: 1ファイルの入力に対し api=1・
                    # 「正常」行が2行（旧名の stale と新名）並んでいた
                    if states:
                        taken -= set(store.page_ids_of(source.name))  # #53 L-5
                        store.drop_pages_of(source.name)
                    moved = store.rename_source(seen_as, source.name)
                    log.info("source_renamed", source_file=source.name, count=moved)
                    progress({"event": "source_renamed", "file": source.name,
                              "was": seen_as, "pages": moved})
                else:
                    # 実データを持つページが新しい名前に既にある。付け替えると
                    # UNIQUE(source_file, page_no) 違反になるので、重複にはせず
                    # 通常の入力として処理する——黙って全〓行にするより送り直す
                    # ほうが安全だが、送信（課金）が動く分岐なので黙らない
                    log.info("source_rename_fallback", source_file=source.name)
                    progress({"event": "rename_fallback", "file": source.name,
                              "was": seen_as})
                seen_as = None
            if seen_as is not None and seen_as != source.name:
                log.info("skip_duplicate_content", source_file=source.name,
                         duplicate_of=seen_as)
                progress({"event": "skip_duplicate", "file": source.name,
                          "same_as": seen_as})
                # ページ数は正本の page 行から求める（内容同一なので等しい。
                # pdftoppm を再実行しない）。正本が未展開なら最低1行は出す
                n_pages = store.page_count_of(seen_as) or 1
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
                    # 再開規則: 処理済みは再送信しない（要件 §5.8）。ただし画像パスは
                    # 追随させる（#46 の改名で旧パスが実在しなくなるため。state は
                    # 触らないので送信・割付の判断は変わらない）
                    if existing["image_path"] != str(img_path):
                        store.set_image_path(pid, str(img_path))
                    continue
                if existing and existing["state"] in _ALIGNED_STATES:
                    # 進んだ状態を expanded へ巻き戻さない。
                    # received（受信後・割付前で中断）は、戻すと保存済み応答を使える
                    # 条件が消えて再送＝再課金になる（issue #38）。
                    # aligned / sending（送信上限・月次上限で止まった分割送信の
                    # 途中）は、戻すと位置合わせ済みの印が消えて run のたびに
                    # 再整列される（#45・実測 約1.6s/ページ）。
                    # どちらも画像パスだけ更新して進捗は保つ
                    store.set_image_path(pid, str(img_path))
                    continue
                store.upsert_page(pid, source.name, i, "expanded", str(img_path))

        # 応答保存と state 更新の非原子性からの復旧（issue #92）。**all_pages を
        # 読む前**に実行する——この後のループは state のスナップショットで
        # 再利用可否を判断するので、先に直しておかないと今回の run が再送する
        recovered_responses = _recover_sent_pages(store, cfg)

        # 今回の入力に無いページが中間データに残っていれば可視化する（issue #28）。
        # render は store の全ページを出力するため、消えた入力の行が黙って
        # Excel に残り続ける——ここでは検知だけで、この経路では消さない。
        # 中間データが消えるのは `purge --yes`（全削除）と、同名で中身が
        # 変わった入力に対する drop_pages_of（そのファイル分だけ・上の
        # source_replaced 経路）の2つ（#53 L-3: 旧コメントは前者だけを
        # 「唯一の削除経路」と書いていたが、H-B の修正で後者が入っている）
        all_pages = store.pages()
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
        summary.processed_pages = len(todo)  # #53 L-9
        summary.recovered_responses = recovered_responses
        # 既に done なページ（todo から外れて無言で再利用される）を可視化する
        # （コーディネーター指示 2026-09-02）。API へは送らない・状態も動かさない
        # ——今回の run では何もしていないことをそのまま伝えるだけの通知
        reused_pages = [p for p in all_pages if p["state"] == "done"]
        summary.reused_pages = len(reused_pages)
        for page in reused_pages:
            progress({"event": "page", "page_id": page["page_id"],
                      "status": "done", "reused": True})
        sends = 0
        for page in todo:
            pid = page["page_id"]
            # 進捗イベントを出さずに continue すると、todo に数えたページの分だけ
            # バーが埋まらず「4/5」で完了する（レビュー M-7）。失敗も1件として進める
            if page["state"] == "failed" and page["status"] == render_rows.STATUS_EXPAND_FAILED:
                summary.processed_failed += 1  # #53 L-9
                progress({"event": "page", "page_id": pid,
                          "status": render_rows.STATUS_EXPAND_FAILED})
                continue
            try:
                img = Image.open(page["image_path"])
            except Exception:
                store.set_state(pid, "failed")
                store.set_status(pid, render_rows.STATUS_EXPAND_FAILED)
                summary.processed_failed += 1  # #53 L-9
                log.error("open_failed", page_id=pid)
                progress({"event": "page", "page_id": pid,
                          "status": render_rows.STATUS_EXPAND_FAILED})
                continue

            # --- F3/F4/F5: 切り出し・位置合わせ・再結合 ---
            # 位置合わせ済みのページは作り直さない（#45）。分割送信（send_limit・
            # 月次上限）が通常運用なので、毎 run の再整列は素の無駄になる
            try:
                # N-4: _restore_alignment が成功する再利用経路は align_page を
                # 通らないため、align_page 内部の page_size_verdict 検査
                # （Q-H1）が掛からず、修正前に整列済み・未送信のページが寸法
                # 未検査のまま送信されうる。reused/align_page のどちらに分岐
                # するかを決める前に一度だけ検査し、どちらの経路でも同じ
                # 様式不一致として扱う（ALGO_VERSION は上げない・done ページの
                # 再送＝再課金を避けるため、この検査は再利用の可否には効かない）
                reason = page_size_verdict(img.size, template)
                if reason is not None:
                    raise PageSizeMismatch(reason)
                reused = (_restore_alignment(store, template, aligned_dir, pid,
                                             geo_hash, ALGO_VERSION, tpl_hash,
                                             snap_enabled=cfg.snap_blocks)
                          if page["state"] in _ALIGNED_STATES else None)
                if reused is None:
                    faces, composite = align_page(img, template)
            except PageSizeMismatch:
                # Q-H1: 入力の寸法がテンプレートと噛み合わない（無検証で
                # resize すると歪んだ画像がそのまま送信されていた）。
                # PageSizeMismatch は AlignError のサブクラスなので、この
                # except を基底クラスより前に置かないと下の分岐に落ちて
                # 「位置合わせ失敗」に化ける
                # issue #71 (a')・FR-F09/FR-F10: 専用理由コード（frame_size）と
                # 判定結果（size は 08 §2.3.3 の対応表で不一致・estimate_shift
                # に到達しないため呼び出し側が PageVerdict を直接組む）を記録する
                store.set_state(pid, "failed")
                store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH, reason="frame_size")
                summary.format_mismatch += 1
                summary.format_mismatch_pre_send += 1
                summary.processed_failed += 1  # #53 L-9
                _record_format_result(
                    store, pid, format_check.PageVerdict("mismatch", "size", -1.0, ()))
                log.error("page_size_mismatch", page_id=pid)
                progress({"event": "page", "page_id": pid,
                          "status": render_rows.STATUS_FORMAT_MISMATCH,
                          "reason_code": "frame_size"})
                continue
            except AlignError as e:
                # issue #71 (a')・FR-F01/FR-F02/FR-F09（08 §2.4.2）: e.diag
                # （面ごとの判定材料）を classify へ通し、mismatch のみ
                # 様式不一致へ付け替える（undecidable は従来どおり
                # 位置合わせ失敗のまま）。AC-F14: 判定関数自体が壊れても
                # 「全ページ様式不一致」に化けさせない——例外時は現行バケツ
                # （位置合わせ失敗）へ落とし、format_check_failed をトレース
                # 付きで残す（row_build_failed と同型の歯止め）
                try:
                    pv = format_check.from_diag(e.diag)
                except Exception as ex:  # noqa: BLE001
                    import traceback
                    # error_trace の第1引数は error_code（型名）——row_build_failed
                    # と同型（pipeline.py の別箇所参照）。format_tb のみ渡す
                    # （例外メッセージ本文は帳票の値を含みうるため出さない・
                    # logging_safe.error_trace の docstring）
                    log.error("format_check_failed", page_id=pid,
                              error_code=type(ex).__name__)
                    log.error_trace(type(ex).__name__,
                                    "".join(traceback.format_tb(ex.__traceback__)))
                    pv = None
                store.set_state(pid, "failed")
                if pv is not None and pv.verdict == "mismatch":
                    store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH,
                                     reason="frame_" + pv.reason)
                    summary.format_mismatch += 1
                    summary.format_mismatch_pre_send += 1
                    page_status = render_rows.STATUS_FORMAT_MISMATCH
                else:
                    store.set_status(
                        pid, render_rows.STATUS_ALIGN_FAILED,
                        reason="frame_" + (pv.reason if pv else "check_failed"))
                    summary.align_failed += 1
                    page_status = render_rows.STATUS_ALIGN_FAILED
                summary.processed_failed += 1  # #53 L-9（様式不一致・位置合わせ失敗の両分岐）
                _record_format_result(store, pid, pv)  # 判定不能でもスコアは残す（FR-F12）
                progress({"event": "page", "page_id": pid, "status": page_status,
                          "reason_code": "frame_" + (pv.reason if pv else "check_failed")})
                continue

            if reused is not None:
                # state は動かさない（received を aligned へ戻すと保存済み応答を
                # 使う条件が消え、再送＝再課金になる・issue #38）
                faces, composite = reused
                # 吸着量は保存済みの結果を読み戻す（FR-F37 の経路⑤・AC-F57）。
                # 再計算しない——再利用の価値は「整列をやり直さない」ことにある
                snap_by_face = snap.from_store_rows(store.snap_geometry(pid))
                log.info("reuse_alignment", page_id=pid)
                # issue #71 (a')・08 §2.4.2「再利用ページの扱い」: 判定のためだけに
                # 整列相当の計算を回さない（#45 の再利用は「整列をやり直さない」
                # ことに価値がある）。前回の run が書いた format_* 列が残って
                # いればそれを保持し、無ければ（本機能より前に整列済み）unknown を
                # 記録する
                existing = store.format_result(pid)
                if existing is None or not existing[0]:
                    _record_format_result(
                        store, pid, format_check.PageVerdict("unknown", "", -1.0, ()))
                    log.info("format_check_skipped_reuse", page_id=pid)
            else:
                # 吸着の計画（issue #75 (f)・FR-F33/F35/F36/F42）。ここで面ごとに
                # 1回だけ確定させ、以降は同じ FaceSnap を記録と適用の両方へ使う。
                # 第3条件（吸着後の新しい重なり）は面をまたいだ幾何が要るので
                # 計画の後で reject_overlapping に通す（08 §6 判断4-F）
                snap_by_face = snap.reject_overlapping(template, {
                    f.face_id: snap.plan_face_snap(
                        template.face(f.face_id), f.estimate, cfg.snap_blocks)
                    for f in faces})
                counter = snap.page_counter_key(snap_by_face)
                if counter == "excluded":
                    summary.snap_excluded_pages += 1
                elif counter == "failsafe":
                    summary.snap_failsafe_pages += 1
                for idx, f in enumerate(faces):
                    # 残差の記録（issue #74 (c)・FR-F32・08 §5.4）。align_page が
                    # 例外なく返った以上 f.estimate は None ではないはずだが、
                    # 呼び出し元の変化に備えて防御的に None を許容する（未計測
                    # のまま -1/'' で書く・§5.9 不変条件6）
                    residual = f.estimate.residual if f.estimate else None
                    if residual is not None:
                        align_residual_px = float(max(residual.h.max, residual.v.max))
                        align_residual_detail = json.dumps(asdict(residual), ensure_ascii=False)
                        # ログは面ごとに1イベント（pipeline.py・upsert_alignment の
                        # 隣）。estimate_shift の中では出さない — match-templates
                        # 等の候補照合ループから何度も呼ばれるため（08 §5.5）。
                        # face_id は渡さない（白リスト外・Q-S1）——face_idx のみ
                        log.info("align_residual", page_id=pid, face_idx=idx,
                                 res_h=residual.h.max, res_v=residual.v.max,
                                 res_pairs=residual.h.pairs + residual.v.pairs,
                                 res_unpaired=residual.h.unpaired + residual.v.unpaired)
                    else:
                        align_residual_px = -1.0
                        align_residual_detail = ""
                    # 吸着の記録（issue #75・FR-F43・AC-F42）。幾何（適用した
                    # dy）は transform["snap"] へ、内訳（測った量・一致本数・
                    # 理由コード）は snap_detail へ分ける——同じ値を2箇所に
                    # 持たせない（08 §6 判断3-B）
                    fs = snap_by_face[f.face_id]
                    log.info("snap_result", page_id=pid, face_idx=idx,
                             snap_dy_max=int(max((abs(d) for d in fs.dy_by_block()),
                                                 default=0)),
                             snap_reason=fs.reason or "applied",
                             snap_blocks=len(fs.blocks))
                    store.upsert_alignment(
                        pid, f.face_id,
                        {"angle": f.angle, "dx": f.dx, "dy": f.dy,
                         "matched": f.shift_matched,
                         "snap": snap.to_transform_json(fs)},
                        True, geo_hash, ALGO_VERSION, tpl_hash,
                        align_residual_px=align_residual_px,
                        align_residual_detail=align_residual_detail,
                        snap_enabled=int(cfg.snap_blocks),
                        snap_px=snap.snap_px_of(fs),
                        snap_detail=snap.to_detail_json(fs))
                    # 位置合わせ画像はローカル中間データ（remap の再スコア用・#45 の
                    # 再利用元）で配布物ではない。圧縮率を下げてエンコード時間を優先する
                    # （実測: level 6 で 0.35s/枚 → level 1 で 0.22s/枚・容量は +1MB 程度）
                    f.image.save(aligned_dir / f"{pid}_{f.face_id}.png",
                                 compress_level=1)
                store.set_state(pid, "aligned")
                # 成功側の記録（FR-F12・AC-F13）。一致ページも記録する——
                # align_page が例外なく返った以上、全面 match のはず
                _record_format_result(store, pid, format_check.from_faces(faces))

            # --- F6: 送信（上限・1リクエスト=1画像）---
            if sends >= cfg.send_limit:
                # 上限で見送ったページは processed_failed に数えない（#53 L-9）
                # ——分割送信は通常運用で、失敗ではなく「まだ送っていない」
                store.set_status(pid, render_rows.STATUS_CAP)
                progress({"event": "page", "page_id": pid, "status": render_rows.STATUS_CAP})
                continue
            # 保存済み応答があれば再送しない（issue #38）。受信後・割付前で落ちた
            # ページは応答を持っているので、再実行のたびに送り直すのは課金の無駄。
            # vision_client の docstring が約束していた契約をここで実装する
            # 送信するバイト列と、保存済み応答に紐づけるハッシュは同一のものを
            # 使う（issue #92）。「この応答はこの画像に対するもの」を後から
            # 検証できるようにし、入力が差し替わっていれば再利用しない。
            # PNG 化を再利用経路でも払うことになるが、対象は state=received の
            # ページだけ（done は todo に入らない）で、送信するページは
            # どのみち同じバイト列が要る
            png = _png_bytes(composite)
            image_sha256 = hashlib.sha256(png).hexdigest()
            saved = load_saved_response(cfg.workdir, pid, image_sha256=image_sha256)
            if saved is not None and page["state"] == "received":
                resp = saved
                log.info("reuse_saved_response", page_id=pid)
            else:
                store.set_state(pid, "sending")
                store.bump_attempt(pid)
                sends += 1
                try:
                    resp = client.annotate(png, pid)
                except SendError as e:
                    store.set_state(pid, "failed")
                    store.set_status(pid, render_rows.STATUS_SEND_FAILED)
                    summary.processed_failed += 1  # #53 L-9
                    log.error("send_failed", page_id=pid, error_code=e.code)
                    progress({"event": "page", "page_id": pid,
                              "status": render_rows.STATUS_SEND_FAILED})
                    continue
                summary.api_calls += 1
                save_response(cfg.workdir, pid, resp, image_sha256=image_sha256)
                store.set_state(pid, "received")

            # --- F7/F8: 割付・丸印 ---
            # 応答の構造異常で落ちても received のまま宙に浮かせない（issue #38）。
            # 浮かせると次回実行で再送対象になり、実行のたびに課金が発生する
            try:
                (below, other, total, page_total,
                 fb_used, fb_discarded, hole,
                 fb_discarded_excl, hole_excl, conflict_excl) = _map_and_score(
                    store, template, pid, resp, faces,
                    snap_by_face=snap_by_face)
            except Exception as e:  # noqa: BLE001
                store.set_state(pid, "failed")
                # M-2（2026-09-02 マリン指摘）: 送信後3コードにも専用理由コードを
                # 配線する（FR-F09「pipeline.py の4箇所が共用」の全箇所を分離）
                store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH,
                                 reason="map_failed")
                summary.format_mismatch += 1
                summary.processed_failed += 1  # #53 L-9
                log.error("map_failed", page_id=pid, error_code=type(e).__name__)
                progress({"event": "page", "page_id": pid,
                          "status": render_rows.STATUS_FORMAT_MISMATCH,
                          "reason_code": "map_failed"})
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
                # M-2（2026-09-02 マリン指摘）: 送信後3コードにも専用理由コードを
                # 配線する（FR-F09「pipeline.py の4箇所が共用」の全箇所を分離）
                store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH,
                                 reason="outside_ratio")
                store.set_state(pid, "failed")
                summary.format_mismatch += 1
                summary.processed_failed += 1  # #53 L-9
                log.error("format_mismatch", page_id=pid, count=other)
                progress({"event": "page", "page_id": pid,
                          "status": render_rows.STATUS_FORMAT_MISMATCH,
                          "reason_code": "outside_ratio"})
                continue

            # 成功: 失敗系ステータスを剥がす（超過は render で合成）。
            # set_status の既定 reason=""（M-2）が status_reason も同時に
            # 空へ戻す——以前の失敗（frame_lines 等）の理由コードが再送・
            # 再処理後の成功ページに残留しない
            # done と template_hash は必ず揃える（issue #93）。片方だけ残ると
            # check_reusable（母集団は state='done'）が旧世代のハッシュを
            # 見て、次の render を「テンプレートが変わっている」と誤って拒否する
            with store.transaction():
                store.set_status(pid, "")
                store.set_template_hash(pid, tpl_hash)  # この cell を割り付けた版の印（#25）
                store.set_state(pid, "done")
            if below >= render_rows.OVERFLOW_MIN_SYMBOLS:
                summary.overflow += 1
            summary.fallback_used += fb_used
            summary.fallback_discarded += fb_discarded
            summary.carve_hole += hole
            summary.fallback_discarded_excluded_field += fb_discarded_excl
            summary.carve_hole_excluded_field += hole_excl
            summary.conflict_excluded_field += conflict_excl
            # U-04/U-07: このページで発火した件数のみ載せる（0件のページばかりの
            # 進捗ログを埋めない）。記入値は含めない（field_id・件数のみ）
            progress({"event": "page", "page_id": pid, "status": "done",
                      **({"fallback_used": fb_used} if fb_used else {}),
                      **({"fallback_discarded": fb_discarded} if fb_discarded else {}),
                      **({"carve_hole": hole} if hole else {}),
                      # issue #66 段2（FR-1.4）: 対象外欄由来の内訳も同じ
                      # 「非ゼロのときだけキーを足す」流儀に揃える
                      **({"fallback_discarded_excluded_field": fb_discarded_excl}
                         if fb_discarded_excl else {}),
                      **({"carve_hole_excluded_field": hole_excl} if hole_excl else {}),
                      **({"conflict_excluded_field": conflict_excl} if conflict_excl else {})})

        # --- F9: 出力 ---
        # ロック内から呼ぶので内側（ロックを取らない側）を使う——render() を
        # 呼ぶと自分が持っているロックに弾かれる
        # render_seconds は P-H1（全件再レンダー累積）の可視化用実測値
        # （えーちゃん指示 2026-09-02・GUI 側の閾値超バナーが使う）
        _render_t0 = time.perf_counter()
        xlsx, csvp, rows = _render_locked(template_path, cfg, None, progress)
        render_seconds = round(time.perf_counter() - _render_t0, 1)
        summary.rows = len(rows)
        summary.unclear_total = sum(r.unclear_count for r in rows)
        # 危険接頭セルの件数（D-28）。**サマリ6項目（§5.9 Must）には足さない**——
        # 出荷ゲート（要確認セル数の合計0）にも載せない。載せると作業者が
        # ゲートを閉じるために正しい値を書き換える圧力になる
        from .render_out import scan_risky_prefixes
        risky = scan_risky_prefixes(derive_columns(template), rows)
        progress({"event": "summary", "pages": summary.pages, "rows": summary.rows,
                  # コーディネーター指示 2026-09-02: 既に done で再利用（未送信）
                  # だったページ数。api_calls が入力枚数より少ない理由をここで読める
                  "reused_pages": summary.reused_pages,
                  "align_failed": summary.align_failed,
                  # Q-H1: 様式不一致の総件数（PageSizeMismatch 起因を含む・原因不問）
                  "format_mismatch": summary.format_mismatch,
                  # FR-F10（issue #71 (a')）: 様式不一致のうち送信前に止まった
                  # 件数（GUI RunScreen.tsx の出口2択・完了案内が参照するキー名）
                  "format_mismatch_pre_send": summary.format_mismatch_pre_send,
                  "api_calls": summary.api_calls,
                  # issue #92: 応答が保存済みなのに state が古かったページを
                  # 復旧した件数（＝再送＝再課金を止めた件数）
                  "recovered_responses": summary.recovered_responses,
                  "unclear_cells": summary.unclear_total, "overflow": summary.overflow,
                  "risky_cells": len(risky),
                  # U-04/U-07（設計 §10.3）。risky_cells と同じ扱いの追加項目
                  # ——サマリ6項目（§5.9 Must）・出荷ゲートには数えない
                  "fallback_used": summary.fallback_used,
                  "fallback_discarded": summary.fallback_discarded,
                  "carve_hole": summary.carve_hole,
                  # issue #66 段2（FR-1.4・AC-1.10）。対象外欄由来の内訳
                  # （常にキーを出す・remap_summary と同じ流儀）
                  "fallback_discarded_excluded_field": summary.fallback_discarded_excluded_field,
                  "carve_hole_excluded_field": summary.carve_hole_excluded_field,
                  "conflict_excluded_field": summary.conflict_excluded_field,
                  # issue #75 (f)・FR-F41。**2項目を別に出す**（原因が違う）。
                  # 0 でも常に出す（remap_summary と同じ流儀）——毎回同じキーが
                  # 並ぶ方が呼び出し側（GUI）の分岐が単純になる。サマリ6項目
                  # （要件 §5.9）には数えない＝警告カード側で扱う
                  "snap_failsafe_pages": summary.snap_failsafe_pages,
                  "snap_excluded_pages": summary.snap_excluded_pages,
                  # P-H1 可視化（累積コストの目安）。total_done_pages は今回処理分
                  # ではなく store に蓄積された state=='done' の累積件数
                  # （えーちゃん指示 2026-09-02）
                  "total_done_pages": store.done_page_count(),
                  "render_seconds": render_seconds,
                  "xlsx": str(xlsx), "csv": str(csvp)})
        return summary


# render 段が自分で付ける status_reason（issue #80）。成功時に status を
# クリアしてよいのはこの2つが付いているページだけ——他の経路（送信前の様式
# 判定・送信後の割付失敗など）が付けた印を render が消すと、失敗の記録が
# 出力し直しただけで黙って消える
_RENDER_OWNED_REASONS = frozenset({"row_build_failed", "row_build_bug"})

# 中間データの壊れ方として説明がつく例外だけを列挙する（issue #80・決定13）。
# ここに無い例外は「コード欠陥の疑い」（row_build_bug）へ倒す＝許可リスト方式。
# ValueError は json.JSONDecodeError・float() の失敗・UnicodeDecodeError を含む。
# TypeError は意図的にコード欠陥側へ置く: データ起因でも起こりうるが、モジュール
# 境界の引数追加で最初に出るのも TypeError で、取り違えたときの損害が非対称
# （過剰報告は triage で解けるが、逆向きは不具合を様式の問題として隠す）。
# KeyboardInterrupt・SystemExit は BaseException 派生なのでそもそもここに来ない
_ROW_BUILD_DATA_ERRORS = (ValueError, KeyError, IndexError, sqlite3.Error)


def render(template_path: str | Path, cfg: Config,
           timestamp: str | None = None,
           progress: Progress = lambda e: None) -> tuple[Path, Path, list[Row]]:
    """cell / era_score から再出力する（API 送信なし・要件 §5.8）。

    run と同じロックを取る（レビュー L-5）。一時ファイル名が固定なので、
    同一秒に2つの render が走ると互いの tmp をすり替えうる。

    progress は既定引数（issue #80）——`render(TPL, cfg, timestamp=tag)` で
    呼んでいる既存の呼び出し側・テストは無改修で通る。
    """
    with _run_lock(cfg):
        return _render_locked(template_path, cfg, timestamp, progress)


def _render_locked(template_path: str | Path, cfg: Config,
                   timestamp: str | None,
                   progress: Progress = lambda e: None
                   ) -> tuple[Path, Path, list[Row]]:
    template, raw, geo_hash = _load(template_path)
    from .align import template_hash as _tpl_hash
    columns = derive_columns(template)
    with Store(_store_path(cfg)) as store:
        # 出力を1バイトも書く前に、中間データが現テンプレートの産物かを検査（#25）
        check_reusable(store, geo_hash, _tpl_hash(raw), check_template=True,
                       snap_enabled=cfg.snap_blocks)
        rows: list[Row] = []
        data_failures: list[str] = []
        bug_failures: list[str] = []
        for page in store.pages():
            p = dict(page)
            if page["state"] == "done":
                # render が前回付けた印は、組み立てる前に剥がす（issue #80）。
                # 剥がさないと compose_status が「今回は成功した行」に前回の
                # 失敗ステータスを載せ、直った後もずっと全〓行が出続ける
                # （08 §2.4.3 が未配線の理由に挙げていた残留そのもの）。
                # 剥がすのは render 自身が付けた印だけ——run が付けた status を
                # 消すと「送信前に止まった」等の記録が黙って失われる
                stale = p.get("status_reason", "") in _RENDER_OWNED_REASONS
                if stale:
                    p["status"] = ""
                    p["status_reason"] = ""
                # 1ページの破損がバッチ全体の出力を失わせない（issue #39）。
                # 中間データに型不正が残っていた場合、旧実装は render/remap/run の
                # どれを叩いても同じ箇所で落ち、送信済み（＝課金済み）の正常ページも
                # 二度と取り出せなかった（回復手段は purge のみだった）
                try:
                    row = build_row(template, p, store.cells(page["page_id"]),
                                    store.era_scores(page["page_id"]), cfg,
                                    extras=store.cell_extras(page["page_id"]))
                except Exception as e:  # noqa: BLE001
                    import traceback
                    # 型名だけだと自コードのバグが全ページ「様式不一致」に化け、
                    # 利用者はテンプレートを疑う（レビュー M-2）。スタックは
                    # error.log へ（frame のみ・記入値は含まない）。
                    # issue #80: データ起因（_ROW_BUILD_DATA_ERRORS）と
                    # コード欠陥の疑い（それ以外）を理由コードで割る
                    is_data = isinstance(e, _ROW_BUILD_DATA_ERRORS)
                    code = "row_build_failed" if is_data else "row_build_bug"
                    log.error(code, page_id=page["page_id"],
                              error_code=type(e).__name__)
                    log.error_trace(type(e).__name__,
                                    "".join(traceback.format_tb(e.__traceback__)))
                    p["status"] = render_rows.STATUS_RENDER_FAILED
                    rows.append(build_failure_row(template, p))
                    (data_failures if is_data else bug_failures).append(page["page_id"])
                    # state は動かさない（done のまま）。failed に落とすと次の
                    # run の todo に入り、送信済みページを再送＝再課金する
                    if (page["status"], page["status_reason"]) != (
                            render_rows.STATUS_RENDER_FAILED, code):
                        # set_status は毎回 commit する。値が変わるときだけ呼ぶ
                        # （全 done ページで無条件に呼ぶと 1 万ページ＝1 万 commit）
                        store.set_status(page["page_id"],
                                         render_rows.STATUS_RENDER_FAILED, reason=code)
                    progress({"event": "render_page_failed", "page_id": page["page_id"],
                              "status": render_rows.STATUS_RENDER_FAILED,
                              "reason_code": code})
                    continue
                rows.append(row)
                if stale:  # 直った。render が付けた印だけを剥がす
                    store.set_status(page["page_id"], "")
                continue
            else:
                if not p.get("status"):
                    p["status"] = render_rows.STATUS_INTERRUPTED
                rows.append(build_failure_row(template, p))
        ts = timestamp or time.strftime("%Y%m%d_%H%M%S")
        # unclear_char_level を write_xlsx の COUNTIF・条件付き書式ゲートまで
        # 貫通させる（QA 再判定・T-16 ブロッカーの解消・2026-08-31）。渡し
        # 忘れると常に既定 False（完全一致）扱いになり、cfg.unclear_char_level
        # を ON にしても xlsx 側の「含む」化だけが有効化されない。
        # 出力に失敗しても接続を残さない（レビュー L-6・Q-MG）——with が
        # 例外の種類を問わず確実に close するので、close 専用の try/except は
        # もう要らない
        xlsx, csvp, risky = write_outputs(cfg.output_dir, ts, columns, rows,
                                          unclear_char_level=cfg.unclear_char_level)
        _warn_risky(risky, columns)
        build_failures = data_failures + bug_failures
        if bug_failures:
            # コード欠陥の疑いは件数も別に出す（issue #80）。データ起因の破損と
            # 混ぜて数えると、開発側が「テンプレートの問題」として片付けてしまう
            log.error("row_build_bug_total", count=len(bug_failures))
        if build_failures:
            # 全ページ破損＝コード／テンプレの問題で、1ページの破損とは意味が違う
            # （レビュー M-1）。旧実装は件数をどこにも出さず exit 0 だった
            done = [p for p in store.pages() if p["state"] == "done"]
            log.error("row_build_failed_total", count=len(build_failures))
            if done and len(build_failures) == len(done):
                raise OperationRefused(
                    f"処理済みページ {len(done)} 件すべてで行の組み立てに失敗した。"
                    "テンプレートと中間データの整合を確認する（詳細は error.log）")
        # 全滅時は上の OperationRefused が優先して、このサマリは出ない
        # （GUI へは refused イベントで届く）
        progress({"event": "render_summary", "pages": len(rows),
                  "row_build_failed": len(data_failures),
                  "row_build_bug": len(bug_failures)})
        return xlsx, csvp, rows


def remap(template_path: str | Path, cfg: Config,
          progress: Progress = lambda e: None) -> int:
    """保存済み token から cell を作り直す（テンプレートの非幾何変更後・§6.7）。

    run / render と同じロックを取る（issue #93）。取らずに共有 SQLite を
    書き換えていたため、run と並走するとページごとに異なるテンプレート世代の
    セルが混ざり、結果がタイミング依存になっていた。

    remap の直後に render する CLI 経路は remap_and_render() を使う——
    ここで取ったロックは戻り値を返す時点で解放されるので、単体で2回呼ぶと
    その間に別プロセスが割り込める。
    """
    with _run_lock(cfg):
        return _remap_locked(template_path, cfg, progress)


def remap_and_render(template_path: str | Path, cfg: Config,
                     progress: Progress = lambda e: None,
                     timestamp: str | None = None
                     ) -> tuple[int, Path, Path, list[Row]]:
    """remap → render を**ロックを保持したまま**続けて実行する（issue #93）。

    CLI の remap コマンドは「割付をやり直して出力し直す」1つの操作で、
    途中に別プロセスの run が割り込むと、出力が「新テンプレートで割り付けた
    セル」と「割り込みが書いた別世代のセル」の混成になる。remap 単体・
    render 単体の API はそのまま残す（GUI・テストが個別に使う）。

    戻り値は (割付し直したページ数, xlsx, csv, 行)。
    """
    with _run_lock(cfg):
        n = _remap_locked(template_path, cfg, progress)
        xlsx, csvp, rows = _render_locked(template_path, cfg, timestamp, progress)
        return n, xlsx, csvp, rows


def _remap_locked(template_path: str | Path, cfg: Config,
                  progress: Progress) -> int:
    """remap の本体（ロックは呼び出し側が保持している前提）。

    幾何セクションが変わっていたら拒否して `run` を促す。
    """
    template, raw, geo_hash = _load(template_path)
    from .align import template_hash as _tpl_hash
    tpl_hash = _tpl_hash(raw)
    with Store(_store_path(cfg)) as store:
        check_reusable(store, geo_hash, tpl_hash, check_template=False,
                       snap_enabled=cfg.snap_blocks)

        n = 0
        fb_used_total = fb_discarded_total = carve_hole_total = 0
        # issue #66 段2（FR-1.4・AC-1.10）: run と同じ内訳を remap 側にも配線する
        # （run だけに入れると remap 経由の出力で対象外欄由来の警告が消える）
        fb_discarded_excl_total = carve_hole_excl_total = conflict_excl_total = 0
        aligned_dir = Path(cfg.workdir) / "aligned"
        for page in store.pages():
            if page["state"] != "done":
                continue
            pid = page["page_id"]
            # 吸着後座標の復元（issue #75・FR-F37 の経路②③・AC-F36）。
            # 読み出し口は store.snap_geometry() 1つだけで、run と同じ
            # apply_snap を通す——座標をコピーして持ち回らない
            t2 = snap.apply_snap(
                template, snap.from_store_rows(store.snap_geometry(pid)))
            by_face: dict[str, list] = {f.face_id: [] for f in t2.faces}
            from .mapping import Symbol
            for _seq, face_id, text, conf, x, y in store.tokens(pid):
                by_face.setdefault(face_id, []).append(Symbol(text, x, y, conf))
            result = assign(t2.cells, by_face, t2.faces, dpi=t2.render_dpi)
            cell_rows = []
            for cell in t2.cells:
                content = result.cells.get(cell.field_id)
                is_empty = (cell.table_id, cell.row_no) in result.empty_rows
                cell_rows.append((cell.field_id,
                                  content.text if content else "",
                                  content.conf_min if content else None,
                                  cell.kind, int(is_empty)))
            fb_used_total += result.fallback_used
            fb_discarded_total += result.fallback_discarded
            carve_hole_total += result.carve_hole
            fb_discarded_excl_total += result.fallback_discarded_excluded_field
            carve_hole_excl_total += result.carve_hole_excluded_field
            conflict_excl_total += result.conflict_excluded_field

            # choice_marks の変更に追従: 保存済み位置合わせ画像から環状帯を再スコア
            import numpy as np
            era_scores: dict[str, dict] = {}
            missing_aligned = 0
            for cell in t2.cells:
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
                # 二値化に渡す face は**吸着前**（template 側）——除外領域は紙に
                # 固定されたマスクで、ブロックの罫線とは無関係（08 §6 判断2）。
                # 帯を測る cell だけが吸着後（t2）になる
                binary = binarize_face(gray, template.face(cell.face_id), dpi=template.render_dpi)
                era_scores[cell.field_id] = era.score_cell(binary, cell)

            # 1ページ分の5更新を1トランザクションにまとめる（issue #93）。
            # 個別 commit のままだと、途中で落ちたページが「cell は新テンプレート
            # の割付なのに page.template_hash は旧のまま」という自己矛盾した
            # 状態で残り、次の render が check_reusable で誤って拒否する。
            # 位置合わせ画像の読み込み・丸印の再スコアはこの外側で終えてある
            # ——重い処理を中に入れると書き込みロックを持つ時間が延びる
            with store.transaction():
                store.upsert_cells(pid, cell_rows)
                # U-04/#62: run と同じ変換（_extras_rows）で char_confs/origin も
                # 作り直す。ここを直さないと、再割付のたびに由来印・文字単位〓の
                # 材料が既定値 '' へ巻き戻る（設計 §12「remap にも同じ変更が要る」）
                store.upsert_cell_extras(pid, _extras_rows(t2, result))
                store.upsert_eras(pid, era_scores)
                store.set_template_hash(pid, tpl_hash)  # 割付し直した版の印（#25）
                store.set_unassigned(pid, result.unassigned_below_table,
                                     result.unassigned_other)
            if missing_aligned:
                log.error("remap_missing_aligned", page_id=pid, count=missing_aligned)
                progress({"event": "remap_warning", "page_id": pid,
                          "missing_aligned_cells": missing_aligned})
            n += 1
        # U-04/U-07（設計 §10.3）: remap は戻り値が既存契約で n（ページ数）の
        # int 固定のため（cli.py の cmd_remap・既存テストが n の型に依存）、
        # 件数は run の summary イベントと同じ形の専用イベントで出す。0 件でも
        # 出す（run の summary と同じく毎回同じキーが並ぶ方が呼び出し側の
        # 分岐が単純になる）。記入値は含めない（件数のみ）
        progress({"event": "remap_summary", "pages": n,
                  "fallback_used": fb_used_total,
                  "fallback_discarded": fb_discarded_total,
                  "carve_hole": carve_hole_total,
                  # issue #66 段2（FR-1.4・AC-1.10）。run の summary イベントと
                  # 同じキー名・同じ「常に出す」流儀（0件でも出す）
                  "fallback_discarded_excluded_field": fb_discarded_excl_total,
                  "carve_hole_excluded_field": carve_hole_excl_total,
                  "conflict_excluded_field": conflict_excl_total,
                  # P-H1 可視化（run の summary と同じキー・えーちゃん指示
                  # 2026-09-02）。remap は render を呼ばないため render_seconds は
                  # 持たない——実測できない値を捏造しない（ルール2）
                  "total_done_pages": store.done_page_count()})
        return n
