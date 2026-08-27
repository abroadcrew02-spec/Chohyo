"""CLI（設計 §3.2）。進捗は stdout へ JSON Lines（§7.3・記入値を含めない）。"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from . import cred_store, logging_safe as log
from .config import Config, load_config, save_config
from .paths import app_root


def _progress(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _client(cfg: Config, replay_dir: str | None):
    if replay_dir:
        from .vision_client import ReplayClient
        return ReplayClient(replay_dir)
    from .vision_client import RealVisionClient
    info = cred_store.load_credentials_info(cfg.workdir)
    return RealVisionClient(credentials_info=info)


def cmd_run(args) -> int:
    cfg = load_config(args.config)
    log.init(cfg.log_dir)
    log.info("run_start", path=args.input)
    from .pipeline import run
    run(args.input, args.template, cfg, _client(cfg, args.replay), _progress)
    return 0


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
    n = remap(args.template, cfg)
    xlsx, csvp, rows = render(args.template, cfg)
    _progress({"event": "remapped", "pages": n, "xlsx": str(xlsx), "csv": str(csvp)})
    return 0


def cmd_status(args) -> int:
    cfg = load_config(args.config)
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
    ok = True
    # テンプレート
    try:
        from .columns import validate_v1
        from .template import load_template
        cols = validate_v1(load_template(args.template))
        _progress({"event": "verify", "check": "template", "ok": True, "columns": len(cols)})
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
    # 資格情報（値は出さない）
    state = cred_store.credentials_state(cfg.workdir)
    _progress({"event": "verify", "check": "credentials", "ok": state != "missing",
               "state": state})
    ok = ok and state != "missing"
    return 0 if ok else 1


def cmd_import_credentials(args) -> int:
    cfg = load_config(args.config)
    p = cred_store.import_credentials(args.json_path, cfg.workdir)
    _progress({"event": "credentials_imported", "path": str(p)})
    print("取り込み完了。元の平文 JSON は不要になったら削除すること。", file=sys.stderr)
    return 0


def cmd_detect_grid(args) -> int:
    """枠候補の生成（設計 §6.9）。テンプレート編集画面が呼ぶ・GUI なしでも検証可。"""
    import numpy as np
    from PIL import Image
    from .grid import detect_ruled, make_uniform
    region = tuple(int(v) for v in args.region.split(","))
    if args.mode == "uniform":
        fit = make_uniform(region, args.rows, args.cols)
    else:
        gray = np.asarray(Image.open(args.image).convert("L"))
        fit = detect_ruled(gray, region)
        if fit is None:
            _progress({"event": "detect_grid", "ok": False,
                       "error": "罫線が検出できない。等分割生成（--mode uniform）へ切り替える"})
            return 1
    _progress({"event": "detect_grid", "ok": True, **fit.to_json()})
    return 0


def cmd_purge(args) -> int:
    cfg = load_config(args.config)
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
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
