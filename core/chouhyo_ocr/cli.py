"""CLI（設計 §3.2）。進捗は stdout へ JSON Lines（§7.3・記入値を含めない）。

各コマンドの中で import しているのは意図的（レビュー LOW への回答）。モジュール
先頭へ集めると、そのコマンドが使わない依存まで毎回読むことになる。実測
（2026-08-28・.venv/Scripts/python.exe -X utf8 -c で計測）: openpyxl 0.471s /
numpy 0.271s / PIL.Image 0.062s。openpyxl が要るのは render だけなので、編集画面が
連打する detect-grid（実測 1回 0.44s）にそのまま 0.5s 上乗せされる。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import cred_store, logging_safe as log
from .config import Config, ConfigError, load_config
from .pipeline_errors import OperationRefused
from .paths import app_root


def _progress(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _client(cfg: Config, replay_dir: str | None):
    if replay_dir:
        from .vision_client import ReplayClient
        return ReplayClient(replay_dir)
    from .vision_client import RealVisionClient
    info = cred_store.load_credentials_info(cfg.workdir)
    return RealVisionClient(credentials_info=info,
                            monthly_cap=cfg.api_monthly_cap)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)
    log.info("run_start", path=args.input, template_path=args.template)
    from .pipeline import run
    summary = run(args.input, args.template, cfg, _client(cfg, args.replay),
                  _progress,
                  resend_on_template_change=args.resend_on_template_change)
    # 1ページも正常に処理できなかった場合は失敗として返す（レビュー M-11）。
    # 常に 0 を返すとスクリプトから成否を判定できない。部分失敗（一部だけ
    # 〓行）は 0 のまま——出力は作られており、判断は要確認セル数で行う
    if summary.rows > 0 and summary.rows == summary.align_failed:
        return 1
    return 0 if summary.rows > 0 or summary.pages == 0 else 1


def cmd_render(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)
    from .pipeline import render
    xlsx, csvp, rows = render(args.template, cfg)
    _progress({"event": "rendered", "rows": len(rows),
               "xlsx": str(xlsx), "csv": str(csvp)})
    return 0


def cmd_remap(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)
    from .pipeline import remap, render
    n = remap(args.template, cfg, progress=_progress)
    xlsx, csvp, rows = render(args.template, cfg)
    _progress({"event": "remapped", "pages": n, "xlsx": str(xlsx), "csv": str(csvp)})
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)  # 監査ログの欠落を防ぐ（M-9）
    from .store import Store
    db = Path(cfg.workdir) / "intermediate.sqlite"
    if not db.exists():
        _progress({"event": "status", "pages": 0})
        return 0
    store = Store(db)
    for p in store.pages():
        _progress({"event": "page", "page_id": p["page_id"], "state": p["state"],
                   "status": p["status"], "attempt": p["attempt"]})
    store.close()
    return 0


def cmd_verify(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)  # 監査ログの欠落を防ぐ（M-9）
    ok = True
    # テンプレート
    try:
        from .columns import amount_cell_count, validate_v1
        from .template import load_template
        t = load_template(args.template)
        cols = validate_v1(t)
        # cells は物理的な欄の数。columns との差は「分割（1欄→複数列）＋管理6列」
        # で、編集画面がこの内訳を表示する（欄数と列数の対応を見えるようにする・
        # ユーザー指摘 2026-08-31）
        _progress({"event": "verify", "check": "template", "ok": True,
                   "columns": len(cols), "cells": len(t.cells),
                   "amount_cells": amount_cell_count(t)})
    except Exception as e:
        ok = False
        _progress({"event": "verify", "check": "template", "ok": False, "error": str(e)})
    # Poppler（存在確認でなく実起動・設計 §8.2）
    try:
        from .ingest import pdftoppm_path
        proc = subprocess.run([str(pdftoppm_path()), "-v"], capture_output=True, timeout=30)
        _progress({"event": "verify", "check": "poppler", "ok": proc.returncode == 0})
        ok = ok and proc.returncode == 0
    except Exception:
        ok = False
        _progress({"event": "verify", "check": "poppler", "ok": False})
    # 保存先が同期フォルダ配下でないか（要配慮個人情報の同期防止・issue #8）
    from .paths import is_cloud_synced_path
    synced = [name for name, d in
              [("workdir", cfg.workdir), ("output_dir", cfg.output_dir),
               ("log_dir", cfg.log_dir)] if is_cloud_synced_path(d)]
    _progress({"event": "verify", "check": "local_storage", "ok": not synced,
               **({"synced_dirs": synced} if synced else {})})
    ok = ok and not synced
    # API 送信の月次残量（安全装置の状態・ユーザー指示 2026-08-28）
    from .api_budget import FREE_TIER_UNITS, remaining, used_this_month
    left = remaining(cfg.api_monthly_cap)
    _progress({"event": "verify", "check": "api_budget", "ok": left > 0,
               "used": used_this_month(), "cap": cfg.api_monthly_cap,
               "free_tier": FREE_TIER_UNITS})
    ok = ok and left > 0
    # 資格情報（値は出さない）
    state = cred_store.credentials_state(cfg.workdir)
    _progress({"event": "verify", "check": "credentials", "ok": state != "missing",
               "state": state})
    ok = ok and state != "missing"
    return 0 if ok else 1


def cmd_import_credentials(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)  # 監査ログの欠落を防ぐ（M-9）
    p = cred_store.import_credentials(args.json_path, cfg.workdir)
    _progress({"event": "credentials_imported", "path": str(p)})
    print("取り込み完了。元の平文 JSON は不要になったら削除すること。", file=sys.stderr)
    return 0


def cmd_expand_page(args) -> int:
    """PDF の1ページを PNG に展開して返す（テンプレート編集画面が呼ぶ）。

    テンプレ作成の入力はスキャン PDF のことが多いのに、編集画面が画像しか
    開けないと最初の一歩で詰まる（2026-08-28 ユーザー指摘）。dpi 既定 300 は
    run の展開・テンプレート座標系（render_dpi）と同じ。
    """
    from .ingest import IngestError, expand, pdf_page_count
    cfg = load_config(args.config)
    log.init(cfg.log_dir)  # 監査ログの欠落を防ぐ（M-9）
    out_dir = Path(cfg.workdir) / "editor_pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(args.input)
    # 総ページ数（pdfinfo・一瞬）。取れたら範囲外を展開前に弾く
    total = pdf_page_count(src) if src.suffix.lower() == ".pdf" else 1
    if total is not None and not 1 <= args.page <= total:
        _progress({"event": "expand_page", "ok": False,
                   "error": f"ページ {args.page} が無い（全 {total} ページ）"})
        return 0  # 業務的失敗は ok:false で伝える（#21）
    try:
        # 該当ページのみ展開（位置合わせ用途・全ページ展開の約1/3の時間）
        pages = expand(src, dpi=args.dpi, out_dir=out_dir, page=args.page)
    except IngestError as e:
        _progress({"event": "expand_page", "ok": False, "error": str(e)})
        return 0
    # 展開しただけの生スキャンは、テンプレート座標系（位置合わせ後の基準位置）
    # と数 mm ズレ・傾きがありうる。生画像の上にテンプレートの枠を重ねると
    # 「枠が記入欄の横に浮いて見える」→ 利用者が枠を手でズラして「直して」
    # しまう——run は位置ズレを自動補正するのでその手直しは不要どころか、
    # テンプレート変更扱いになり再割付・再送信の確認まで誘発する（ユーザー
    # 指摘・2026-08-31）。編集画面には run と同じ align_page を通した画像を
    # 返し、枠が最初から記入欄の上に乗るようにする。
    #
    # 位置合わせできない紙（run でも位置合わせ失敗になる品質）は生画像へ
    # 退避し aligned:false で伝える。編集を止めるほどの失敗ではない。
    page_path = pages[0].resolve()
    aligned = False
    try:
        from PIL import Image

        from .align import AlignError, align_page
        from .template import load_template
        template = load_template(args.template)
        with Image.open(page_path) as img:
            img.load()
            _faces, composite = align_page(img, template)
        # 名前は決め打ちで毎回上書き（同じ紙を開き直すたびに増やさない）。
        # expand() の stale 掃除は「<stem>-<数字>」の完全一致だけを消すため、
        # この名前が巻き添えになることはない
        out = out_dir / f"{src.stem}-p{args.page:04d}-aligned.png"
        composite.save(out, format="PNG", compress_level=3)
        page_path = out.resolve()
        aligned = True
    except Exception:  # noqa: BLE001
        # テンプレート破損・位置合わせ失敗・画像不正のいずれも生画像で続行。
        # 例外メッセージは出さない（パスに入力ファイル名が乗りうる）
        pass
    # 絶対パスで返す。相対だと呼び出し側（GUI）の cwd 基準で解決され、コアの
    # cwd（core/）と食い違って「ファイルが見つからない」になる（実測: dev 窓で
    # 編集画面が「展開中…」のまま止まった原因・2026-08-28）
    _progress({"event": "expand_page", "ok": True,
               "page_path": str(page_path),
               "aligned": aligned,
               **({"pages": total} if total is not None else {})})
    return 0


def cmd_debug_images(args) -> int:
    """読み取りの可視化画像を出力する（開発者モード・API 送信なし）。

    どの文字がどの欄に入ったか／なぜ〓になったかを1ページ1枚の PNG で示す。
    出力は既定で workdir/debug/（読取値が画像に描かれるため中間データ扱い）。
    """
    from .config import load_config
    from .debug_images import write_debug_images
    from .store import Store
    from .template import load_template
    cfg = load_config(args.config)
    log.init(cfg.log_dir)
    template = load_template(args.template)
    wd = Path(cfg.workdir)
    out_dir = Path(args.out) if args.out else wd / "debug"
    store = Store(wd / "intermediate.sqlite")
    try:
        made = write_debug_images(
            store, template, wd / "aligned", out_dir,
            cfg.unclear_threshold,
            page_ids=[args.page] if args.page else None)
    finally:
        store.close()
    _progress({"event": "debug_images", "ok": True,
               "count": len(made), "dir": str(out_dir.resolve())})
    for m in made:
        print(str(m.resolve()))
    return 0


def cmd_detect_grid(args) -> int:
    """枠候補の生成（設計 §6.9）。テンプレート編集画面が呼ぶ・GUI なしでも検証可。"""
    from .grid import detect_ruled, make_uniform
    log.init(load_config(getattr(args, "config", None)).log_dir)  # M-9
    region = tuple(int(v) for v in args.region.split(","))
    if args.mode == "uniform":
        fit = make_uniform(region, args.rows, args.cols)   # 画像を読まない
    else:
        import numpy as np                                  # ruled のときだけ
        from PIL import Image
        if not args.image:
            _progress({"event": "detect_grid", "ok": False,
                       "error": "罫線検出には帳票の画像が必要。先に帳票を開く"})
            return 0
        try:
            gray = np.asarray(Image.open(args.image).convert("L"))
        except (OSError, ValueError):
            # 例外文字列をそのまま出さない（設計 §8.1: パスは記入値ではないが、
            # 画面には日本語の指示だけを出す方針で統一する）
            _progress({"event": "detect_grid", "ok": False,
                       "error": "画像を読み込めない。帳票を開き直す"})
            return 0
        fit = detect_ruled(gray, region)
        if fit is None:
            _progress({"event": "detect_grid", "ok": False,
                       "error": "罫線が検出できない。等分割生成（--mode uniform）へ切り替える"})
            return 0
    _progress({"event": "detect_grid", "ok": True, **fit.to_json()})
    return 0


def cmd_purge(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)  # 監査ログの欠落を防ぐ（M-9）
    if not args.yes:
        print("中間データ削除には --yes が必要（要件 §6.3: 削除は明示操作のみ）",
              file=sys.stderr)
        return 1
    wd = Path(cfg.workdir)
    if wd.exists():
        shutil.rmtree(wd)
    _progress({"event": "purged", "path": str(wd)})
    return 0


def main(argv: list[str] | None = None) -> int:
    # frozen exe では -X utf8 が効かない。JSON Lines（§7.3）が cp932 に
    # なると GUI 側で文字化けするため、stdout/stderr を UTF-8 へ固定する。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="chouhyo-ocr")
    ap.add_argument("--config", default=None, help="設定ファイル（既定: config.json）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    default_tpl = str(app_root() / "templates" / "chouhyo-v1.json")

    p = sub.add_parser("run", help="一括処理（API 送信あり）")
    p.add_argument("--input", required=True)
    p.add_argument("--template", default=default_tpl)
    p.add_argument("--replay", default=None, help="開発用: 保存済み応答ディレクトリで再生")
    p.add_argument("--resend-on-template-change", action="store_true",
                   help="テンプレート変更で無効になった処理済みページを再送する"
                        "（API 送信と課金が発生する。既定は中止）")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("render", help="cell から再出力（API 送信なし）")
    p.add_argument("--template", default=default_tpl)
    p.set_defaults(fn=cmd_render)

    p = sub.add_parser("remap", help="token から cell を作り直す（テンプレート変更後）")
    p.add_argument("--template", default=default_tpl)
    p.set_defaults(fn=cmd_remap)

    p = sub.add_parser("status", help="進捗表示")
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("verify", help="資格情報・Poppler・テンプレートの検証")
    p.add_argument("--template", default=default_tpl)
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("import-credentials", help="資格情報 JSON を DPAPI 暗号化で取り込む")
    p.add_argument("json_path")
    p.set_defaults(fn=cmd_import_credentials)

    p = sub.add_parser("expand-page", help="PDF の1ページを PNG 展開（編集画面用）")
    p.add_argument("--input", required=True)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--template", default=default_tpl)
    p.set_defaults(fn=cmd_expand_page)

    p = sub.add_parser("debug-images",
                       help="読み取りの可視化画像を出力（開発者モード・API送信なし）")
    p.add_argument("--template", default=default_tpl)
    p.add_argument("--out", default=None)
    p.add_argument("--page", default=None, help="特定の帳票IDのみ出力")
    p.set_defaults(fn=cmd_debug_images)

    p = sub.add_parser("detect-grid", help="枠候補の生成（罫線検出 or 等分割）")
    p.add_argument("--image", help="面画像（--mode ruled で必須）")
    p.add_argument("--region", required=True, help="x,y,w,h（面ローカル）")
    p.add_argument("--mode", choices=["ruled", "uniform"], default="ruled")
    p.add_argument("--rows", type=int, default=1)
    p.add_argument("--cols", type=int, default=1)
    p.set_defaults(fn=cmd_detect_grid)

    p = sub.add_parser("purge", help="中間データの削除（--yes 必須）")
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_purge)

    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        print("INTERRUPTED", file=sys.stderr)
        return 130
    except OperationRefused as e:
        # 業務的な拒否は JSON Lines で伝える（レビュー H-C）。exit 0 なのは
        # #21 と同じ契約——非ゼロだと GUI が「終了コード 1。再度押すと続きから
        # 処理します」という**誤った案内**を出す（決定論的な拒否なので、押しても
        # 永久に同じ結果になる）
        _progress({"event": "refused", "ok": False, "error": str(e),
                   **({"hint": e.hint} if e.hint else {})})
        print(str(e), file=sys.stderr)
        return 0
    except ConfigError as e:
        # 設定エラーはメッセージをそのまま出す（内容は設定キー名と設定値のみで
        # 帳票の記入値は含まれない）。固定文言に潰すと利用者が config.json の
        # どこを直せばよいか分からない（レビュー N-2）
        print(f"ERROR ConfigError: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        # 記入値の漏出防止（issue #2）: 例外メッセージには帳票の値が乗りうる
        # （例: openpyxl IllegalCharacterError はセル値をメッセージへ含める）。
        # stderr は GUI のログへ無フィルタで中継されるため、固定文言＋型名のみ出す。
        # スタック（ファイル/行/関数のみ・メッセージ除外）は error.log へ残す。
        import traceback
        log.error("unhandled_exception", error_code=type(e).__name__)
        log.error_trace(type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
        print(f"ERROR {type(e).__name__}: 処理を中止しました。詳細は error.log を参照。",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
