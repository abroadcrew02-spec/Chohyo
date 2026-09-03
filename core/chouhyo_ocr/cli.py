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
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from . import cred_store, logging_safe as log
from .config import Config, ConfigError, load_config
from .pipeline_errors import OperationRefused
from .paths import app_root


def _progress(event: dict) -> None:
    print(json.dumps(event, ensure_ascii=False), flush=True)


def _load_config_and_init_log(config_path) -> Config:
    """load_config + log.init を1箇所にまとめる（issue #72 (t)・M-1・
    2026-09-02 マリン指摘）。

    本番の呼び出し順は「load_config → log.init」——Config._validate は
    last_template のフォールバックが起きても例外を投げず（AC-F60）、
    Config.last_template_fallback_reason へ理由コードを積むだけでログは
    出さない。_validate の時点ではまだ log.init が呼ばれておらず、そこで
    warn しても logging_safe が未初期化（_app/_err が None）で黙って
    消える。ここで load_config の直後に log.init し、その直後に warn する
    ことで、フォールバックが起きたことを確実にログへ残す。
    """
    cfg = load_config(config_path)
    log.init(cfg.log_dir)
    if cfg.last_template_fallback_reason:
        log.warn("config_last_template_fallback",
                 error_code=cfg.last_template_fallback_reason)
    return cfg


def _render_dpi_arg(value: str) -> int:
    """--dpi の範囲検証（S-8）。schema/template.schema.json の render_dpi と
    同じ 72〜1200 を受理範囲とする——detect-grid の --dpi は grid.detect_ruled/
    make_uniform の px 定数スケール（汎用化 A-3）にそのまま渡る値なので、
    範囲外を渡すとテンプレートでは拒否される値がここだけ静かに通ってしまう。
    範囲外は argparse の標準エラー（exit code 2）で拒否する。
    """
    n = int(value)
    if not 72 <= n <= 1200:
        raise argparse.ArgumentTypeError(
            f"--dpi は 72〜1200 の範囲で指定する（指定: {n}）")
    return n


def _client(cfg: Config, replay_dir: str | None):
    if replay_dir:
        from .vision_client import ReplayClient
        return ReplayClient(replay_dir)
    from .vision_client import RealVisionClient
    info = cred_store.load_credentials_info(cfg.workdir)
    return RealVisionClient(credentials_info=info,
                            monthly_cap=cfg.api_monthly_cap)


def cmd_run(args) -> int:
    cfg = _load_config_and_init_log(args.config)
    # テンプレートファイル名はログへ出さない（Q-S1・FR-F50・2026-09-02）。
    # ここではまだテンプレートを読んでおらずハッシュが分からない——算出のため
    # だけに二重読みはしない。直後に pipeline._run_locked が
    # template_loaded template_hash=... を出すので、同じ run の中でテンプレート
    # の特定はできる（08_frame_detection_design.md §1.5）
    log.info("run_start", path=args.input)
    from .pipeline import run
    summary = run(args.input, args.template, cfg, _client(cfg, args.replay),
                  _progress,
                  resend_on_template_change=args.resend_on_template_change)
    # 1ページも正常に処理できなかった場合は失敗として返す（レビュー M-11）。
    # 常に 0 を返すとスクリプトから成否を判定できない。部分失敗（一部だけ
    # 〓行）は 0 のまま——出力は作られており、判断は要確認セル数で行う。
    # 母集団は align_failed（位置合わせ失敗）＋format_mismatch（様式不一致・
    # Q-H1 の PageSizeMismatch を含む）——後者だけを見ていると、寸法不一致や
    # 応答構造異常で全ページが様式不一致に倒れた実行が exit 0（成功扱い）に
    # なってしまう（Q-H1 着手時のレビュー指摘）
    total_failed = summary.align_failed + summary.format_mismatch
    if summary.rows > 0 and summary.rows == total_failed:
        return 1
    return 0 if summary.rows > 0 or summary.pages == 0 else 1


def cmd_render(args) -> int:
    cfg = _load_config_and_init_log(args.config)
    from .pipeline import render
    xlsx, csvp, rows = render(args.template, cfg)
    _progress({"event": "rendered", "rows": len(rows),
               "xlsx": str(xlsx), "csv": str(csvp)})
    return 0


def cmd_remap(args) -> int:
    cfg = _load_config_and_init_log(args.config)
    from .pipeline import remap, render
    n = remap(args.template, cfg, progress=_progress)
    xlsx, csvp, rows = render(args.template, cfg)
    _progress({"event": "remapped", "pages": n, "xlsx": str(xlsx), "csv": str(csvp)})
    return 0


def cmd_status(args) -> int:
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9）
    from .store import Store
    db = Path(cfg.workdir) / "intermediate.sqlite"
    if not db.exists():
        _progress({"event": "status", "pages": 0})
        return 0
    with Store(db) as store:
        for p in store.pages():
            _progress({"event": "page", "page_id": p["page_id"], "state": p["state"],
                       "status": p["status"], "attempt": p["attempt"]})
    return 0


def cmd_verify(args) -> int:
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9）
    ok = True
    # テンプレート
    try:
        from .columns import amount_cell_count, validate_v1
        from .template import load_template, output_cells
        t = load_template(args.template)
        # verify は pipeline._load を経由しないため template_loaded を
        # 自前で出す——出さないと、この経路で書かれる W-1〜W-4 の cell_idx・
        # face_idx を事後にどのテンプレートのものか特定できない（不変条件A・
        # Q-S1・FR-F50・08_frame_detection_design.md §1.4）
        from .align import template_hash as _tpl_hash
        log.info("template_loaded", template_hash=_tpl_hash(
            json.loads(Path(args.template).read_text(encoding="utf-8"))))
        cols = validate_v1(t)
        # cells は物理的な欄の数。columns との差は「分割（1欄→複数列）＋管理6列」
        # で、編集画面がこの内訳を表示する（欄数と列数の対応を見えるようにする・
        # ユーザー指摘 2026-08-31）
        # 除外領域（マスク）の個数も同列で出す（#55: 保存経路に保全機構が無く、
        # 除外が編集中に静かに消えても検証がそれを検知していなかった）。件数・
        # 座標そのものの妥当性検査ではなく、読み込めたテンプレの現在値を
        # そのまま見える化するだけ——比較・拒否の判断は呼び出し側（編集画面）が行う
        exclusions_by_face = {f.face_id: len(f.exclusions) for f in t.faces}
        # 除外領域×受け皿の重なり警告（U-09・H-6）。拒否はしない——見える化のみ
        # （§7.1: 出荷テンプレートに意図的な重なりが実在するため）。GUI 側と
        # 合意済みの契約: warnings は文字列配列（無警告時は空配列）
        # column_names は derive_columns の結果そのもの（validate_v1 の戻り値＝
        # cols と同一。管理6列を含む順序付き全列名）。出力列制御 MVP・FR-0.1:
        # 列構成の唯一の正を verify 応答に置き、GUI 側での再導出（F-10 の
        # 原因だった二重実装）を無くすための入り口。220件規模の文字列配列だが
        # verify は対話操作でしか呼ばれず、頻度・サイズとも問題にならない
        # issue #66 段4（フブキ実測）: GUI 側の差分計算（cells+6-columns）は
        # subfields 展開で破綻する（cells=194・columns=220 のとき -20 になる）。
        # 「N 欄を出力しません」（FR-1.9）に使う N は欄数（物理セル数）であり
        # 列数ではないため、output_cells() を経由してここで直接数える
        # （GUI 側での再導出はしない・FR-0.1 と同じ「唯一の正」の思想）
        output_disabled_cells = len(t.cells) - len(output_cells(t))
        # --expect-columns（issue #65-1 穴C）: GUI 側の読み込み時基準
        # （loadedCounts.columns）が verify 失敗・invoke 失敗等で取得できな
        # かった場合でも、CLI 単体で列数の後退を検知できるようにする最後の
        # 砦。省略時（None）は従来どおり常に True——挙動を変えない
        column_count_ok = (args.expect_columns is None
                            or len(cols) >= args.expect_columns)
        event = {"event": "verify", "check": "template", "ok": column_count_ok,
                 "columns": len(cols), "cells": len(t.cells),
                 "amount_cells": amount_cell_count(t),
                 "exclusions": sum(exclusions_by_face.values()),
                 "exclusions_by_face": exclusions_by_face,
                 "warnings": list(t.warnings),
                 "column_names": cols,
                 "output_disabled_cells": output_disabled_cells}
        if not column_count_ok:
            # 記入値は含まれない（列数という件数のみ）ため、既存の記入値
            # 漏出防止方針（設計 §8.1）に抵触しない
            event["error"] = (f"列数が期待値を下回っています（{len(cols)} 列 < "
                               f"--expect-columns {args.expect_columns}）")
        _progress(event)
        ok = ok and column_count_ok
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
    # 環境変数の平文鍵は state と独立に見る（S-MB）。state は3値契約
    # （dpapi/env/missing）のまま dpapi を優先するため、両方ある環境では state
    # だけでは平文鍵の残置に気づけない。ok は変えない——env でも実行はできる
    env_present = cred_store.env_credentials_present()
    cred_event = {"event": "verify", "check": "credentials",
                  "ok": state != "missing", "state": state,
                  "env_present": env_present}
    if state == "env" or env_present:
        cred_event["warn"] = True
        cred_event["reason"] = "env_plaintext"
        # 変数名だけを出す（設定値＝鍵ファイルのパスは出さない・既存方針）
        print("警告: 環境変数 GOOGLE_APPLICATION_CREDENTIALS の平文の鍵ファイルを"
              "参照している。import-credentials で取り込み、平文 JSON と環境変数を"
              "消すこと。", file=sys.stderr)
    _progress(cred_event)
    ok = ok and state != "missing"
    return 0 if ok else 1


def cmd_import_credentials(args) -> int:
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9）
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
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9）
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
    fail_reason: str | None = None
    verdict_fields: dict = {}  # issue #71 (a')・08 §2.6: verdict/score/faces（追加のみ）
    try:
        from PIL import Image

        from . import format_check
        from .align import AlignError, PageSizeMismatch, align_page
        from .align import template_hash as _tpl_hash
        from .template import TemplateError, load_template
        template = load_template(args.template)
        # expand-page も pipeline._load を経由しないため template_loaded を
        # 自前で出す（cmd_verify と同じ理由・不変条件A・Q-S1・FR-F50・
        # 08_frame_detection_design.md §1.4）
        log.info("template_loaded", template_hash=_tpl_hash(
            json.loads(Path(args.template).read_text(encoding="utf-8"))))
        with Image.open(page_path) as img:
            img.load()
            # --no-mask: 除外領域を白塗りしない下地を返す（#59 H-8）。編集画面が
            # 出荷テンプレの除外を焼いた画像しか下地に持てず、除外枠の位置調整・
            # 取捨の判断材料が無かった問題への対応。run（送信経路）はこの引数に
            # 到達しない——expand-page からしか呼ばれない分岐
            _faces, composite = align_page(img, template, mask=not args.no_mask)
        # 名前は決め打ちで毎回上書き（同じ紙を開き直すたびに増やさない）。
        # 別ページを開き直すと旧ページの -aligned.png は上書きされず残るため、
        # expand() の stale 掃除（<stem>-<数字> 完全一致）に -aligned.png 用の
        # 分岐を足して一緒に消している（#60 M-7・帳票原本の複製が滞留する問題）
        out = out_dir / f"{src.stem}-p{args.page:04d}-aligned.png"
        composite.save(out, format="PNG", compress_level=3)
        page_path = out.resolve()
        aligned = True
        # 成功側の verdict（全面 match のはず・08 §2.6 の例）。
        # M-3（2026-09-02 マリン指摘）: from_faces 自体を内側 try で囲む。
        # aligned=True 確定後にここで例外が起きると、囲わない場合は下の
        # except 節（例: 汎用 Exception → fail_reason="other"）に落ちて
        # 「aligned:true なのに reason も乗る」という既存契約違反の応答に
        # なる（aligned:false のときだけ reason を返す契約・テストで固定
        # 済み）。判定関数の例外は verdict を欠落させるだけに留め、
        # expand-page 自体（画像は既に保存済み）は成功のまま返す
        try:
            verdict_fields = _expand_page_verdict_fields(format_check.from_faces(_faces))
        except Exception as ex:  # noqa: BLE001
            import traceback
            log.error("format_check_failed", error_code=type(ex).__name__)
            log.error_trace(type(ex).__name__,
                            "".join(traceback.format_tb(ex.__traceback__)))
    # テンプレート破損・位置合わせ失敗・画像不正のいずれも生画像で続行する
    # （契約は変えない・GUI は aligned:false のまま編集を続けられる）。以前は
    # bare except Exception 一本で全部を同じ aligned:false に潰していたため、
    # テンプレート破損（設定ミス・要修正）と位置合わせ失敗（紙の品質）を
    # GUI 側で区別できなかった。reason に**種別のみ**を載せる——例外メッセージ
    # 本文は出さない（パスに入力ファイル名が乗りうる・既存方針どおり）
    except TemplateError:
        fail_reason = "template"
        # テンプレートが読めていないため判定を行わない（verdict は返さない・
        # 08 §2.6）
    # N-2: PageSizeMismatch は AlignError のサブクラス（Q-H1）。基底クラスより
    # 前に置かないと下の except AlignError に落ちて "align"（位置合わせ失敗）
    # に化ける——run（送信経路）ではこの入力は様式不一致として弾かれるため、
    # 編集画面には "align" ではなく専用の reason を返して案内を分ける
    except PageSizeMismatch:
        fail_reason = "size"
        # LOW（2026-09-02 マリン指摘）: size 用の PageVerdict を直接組んで
        # 唯一の整形関数（_expand_page_verdict_fields）へ通す——辞書リテラルを
        # 個別に持つと、_expand_page_verdict_fields 側のキー構成を変えたときに
        # ここだけ追随し忘れる二重定義になる（pipeline.py の同種構成と統一）
        verdict_fields = _expand_page_verdict_fields(
            format_check.PageVerdict("mismatch", "size", -1.0, ()))
    except AlignError as e:
        fail_reason = "align"
        # AC-F14 と同じ歯止め: 判定関数の例外で verdict を欠落させるだけに
        # 留め、expand-page 自体は生画像＋aligned:false で従来どおり続行する
        try:
            pv = format_check.from_diag(e.diag)
            verdict_fields = _expand_page_verdict_fields(pv)
        except Exception as ex:  # noqa: BLE001
            import traceback
            # error_trace の第1引数は error_code（型名）。format_tb のみ渡す
            # （例外メッセージ本文は帳票の値を含みうるため出さない・
            # logging_safe.error_trace の docstring・pipeline.py と同型）
            log.error("format_check_failed", error_code=type(ex).__name__)
            log.error_trace(type(ex).__name__,
                            "".join(traceback.format_tb(ex.__traceback__)))
    except (OSError, ValueError):
        fail_reason = "image"
    except Exception:  # noqa: BLE001
        fail_reason = "other"
    # 絶対パスで返す。相対だと呼び出し側（GUI）の cwd 基準で解決され、コアの
    # cwd（core/）と食い違って「ファイルが見つからない」になる（実測: dev 窓で
    # 編集画面が「展開中…」のまま止まった原因・2026-08-28）
    _progress({"event": "expand_page", "ok": True,
               "page_path": str(page_path),
               "aligned": aligned,
               **({"reason": fail_reason} if fail_reason else {}),
               **({"pages": total} if total is not None else {}),
               **verdict_fields})
    return 0


def _expand_page_verdict_fields(pv) -> dict:
    """`format_check.PageVerdict` → expand-page の JSON 追加フィールド
    （issue #71 (a')・08 §2.6）。

    stdout の JSON Lines は秘匿対象外（07 §0.6）——GUI が面を特定するために
    `face_id`（名前）をそのまま出す。ログ（logging_safe 経由）が使う匿名の
    `face_idx` とは別の語彙で、ここでは意図して face_id を使う。
    """
    return {
        "verdict": pv.verdict,
        "score": pv.score,
        "faces": [
            {"face_id": f.face_id, "verdict": f.verdict, "reason": f.reason,
             "score": f.score, "detected": f.detected, "expected": f.expected}
            for f in pv.faces
        ],
    }


_MATCH_TEMPLATES_TIME_BUDGET_S = 3.0  # NFR-F09（暫定）。合計でこれを超えたら打ち切る
_MATCH_TEMPLATES_SIZE_LIMIT = 5 * 1024 * 1024  # 07 §7.3 の1件あたり上限（暫定）


def cmd_match_templates(args) -> int:
    """入力1枚に出荷＋候補テンプレートを照合する（issue #72 (t)・08 §3.3）。

    **候補の列挙・パス検査は Rust 側の責務**（08 §3.10 不変条件3）——ここは
    渡された絶対パスをそのまま読むだけで、ディレクトリ列挙も reparse point
    検査もしない。1件の不正テンプレートで照合ループを止めない（FR-F28）。
    ログにはテンプレート名を出さない（Q-S1・FR-F50・#77 の方針）——識別は
    `template_hash` と序数のみ。stdout の JSON Lines は秘匿対象外（07 §0.6）
    なので表示名（ファイル名の stem）をそのまま返す。

    `ok:false` の `error` は機械可読な固定コードのみ（2026-09-02 マリン
    指摘 M-6）——`type(e).__name__` や例外メッセージは出さない（パスや
    帳票の値が乗りうるため・issue #2 と同じ方針）:
    `input_not_found` / `expand_failed` / `input_unreadable` / `internal`。
    """
    import time as _time
    from datetime import datetime

    from PIL import Image

    from . import format_check
    from .align import template_hash as _tpl_hash
    from .columns import validate_v1
    from .ingest import IngestError, expand, pdf_page_count
    from .template import TemplateError, load_template

    t0 = _time.perf_counter()
    cfg = _load_config_and_init_log(args.config)

    out_dir = Path(cfg.workdir) / "editor_pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(args.input)

    # LOW（マリン提案）: expand() へ投げる前に存在と --page 範囲を確認する
    # （cmd_expand_page と同じ流儀）。expand() 自身も範囲外を IngestError で
    # 弾くが、ここで先に確認すると「無いファイル」「範囲外ページ」を
    # expand_failed の1コードへ素直に収められる
    if not src.exists():
        _progress({"event": "match_templates", "ok": False, "error": "input_not_found"})
        return 0
    total = pdf_page_count(src) if src.suffix.lower() == ".pdf" else 1
    if total is not None and not 1 <= args.page <= total:
        _progress({"event": "match_templates", "ok": False, "error": "expand_failed"})
        return 0
    try:
        # PDF は該当ページのみ展開（expand-page と同じ経路・PNG/JPG はそのまま
        # 返る）。300dpi は run/expand-page の既定と同じ（テンプレート側の
        # render_dpi が違っても check_page が image_size へ resize するので
        # ここでの dpi は下地画像の解像度を決めるだけ）
        pages = expand(src, dpi=300, out_dir=out_dir, page=args.page)
    except IngestError:
        _progress({"event": "match_templates", "ok": False, "error": "expand_failed"})
        return 0

    results: list[dict] = []
    excluded: list[dict] = []
    truncated = False
    entries = [("shipped", args.shipped)] + [("user", c) for c in (args.candidate or [])]

    try:
        img = Image.open(pages[0])
        img.load()
    except (OSError, ValueError):
        _progress({"event": "match_templates", "ok": False, "error": "input_unreadable"})
        return 0

    try:
        with img:
            input_size = [img.width, img.height]
            # M-3（2026-09-02 マリン指摘）: 予算の起点を画像の読み込み完了後
            # （候補ループ直前）に移す——展開（PDF ラスタライズ）にかかる時間は
            # 候補照合そのものではないため、予算から除く。elapsed_ms は
            # コマンド全体、budget_elapsed_ms は候補ループだけを別に返す
            budget_t0 = _time.perf_counter()
            for kind, raw_path in entries:
                if (_time.perf_counter() - budget_t0) > _MATCH_TEMPLATES_TIME_BUDGET_S:
                    # 打ち切りはテンプレート単位（08 §3.3.3）——check_page の
                    # 途中では止めない（面の途中で止めると fold が誤った
                    # verdict を返す）。次の1件を「始める前」にだけ見る
                    truncated = True
                    excluded.append({"name": Path(raw_path).stem, "reason": "limit"})
                    continue

                p = Path(raw_path)
                name = p.stem
                try:
                    st = p.stat()
                except OSError as e:
                    # M-4（マリン指摘）: 語彙を Rust 側と統一（not_found）
                    excluded.append({"name": name, "reason": "not_found"})
                    log.warn("match_template_excluded", error_code=type(e).__name__)
                    continue
                if st.st_size > _MATCH_TEMPLATES_SIZE_LIMIT:
                    excluded.append({"name": name, "reason": "size"})
                    log.warn("match_template_excluded", error_code="TooLarge")
                    continue
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except Exception as e:  # noqa: BLE001 — 壊れた JSON は候補から外すだけ（FR-F28）
                    # M-4: 語彙を Rust 側と統一（invalid_json → parse）
                    excluded.append({"name": name, "reason": "parse"})
                    log.warn("match_template_excluded", error_code=type(e).__name__)
                    continue
                # ここから先はパースできた JSON があるので template_hash で
                # 識別できる（名前は出さない）
                tpl_hash = _tpl_hash(raw)
                try:
                    template = load_template(p)
                    validate_v1(template)
                except TemplateError as e:
                    excluded.append({"name": name, "reason": "schema"})
                    log.warn("match_template_excluded", template_hash=tpl_hash,
                             error_code=type(e).__name__)
                    continue
                except Exception as e:  # noqa: BLE001
                    # M-7（マリン指摘）: TemplateError 以外（想定外）は
                    # error+トレースを残す（row_build_failed と同型。
                    # error_trace は format_tb のみ・例外メッセージ本文は
                    # 値を含みうるため渡さない）
                    import traceback
                    excluded.append({"name": name, "reason": "check_failed"})
                    log.error("match_template_failed", template_hash=tpl_hash,
                             error_code=type(e).__name__)
                    log.error_trace(type(e).__name__,
                                    "".join(traceback.format_tb(e.__traceback__)))
                    continue

                # M-7: check_page 自体も候補ごとに try で包む——ここで例外が
                # 起きても、既にできている results／excluded を捨てず次の
                # 候補へ進む
                try:
                    pv = format_check.check_page(img, template)
                except Exception as e:  # noqa: BLE001
                    import traceback
                    excluded.append({"name": name, "reason": "check_failed"})
                    log.error("match_template_failed", template_hash=tpl_hash,
                             error_code=type(e).__name__)
                    log.error_trace(type(e).__name__,
                                    "".join(traceback.format_tb(e.__traceback__)))
                    continue

                # M-5（マリン指摘）: fields は単発欄数のみ（table_id が付いた
                # 表由来のセルを含まない・Rust の一覧（faces[].fields の要素数）
                # と揃える）。物理セル数（len(template.cells)）は返さない
                fields = sum(1 for c in template.cells if c.table_id is None)
                tables = len({c.table_id for c in template.cells if c.table_id is not None})
                updated_at = datetime.fromtimestamp(
                    st.st_mtime).astimezone().isoformat(timespec="seconds")
                # LOW（マリン提案）: 成功時のログを1行残す（診断用・名前は出さない）
                log.info("template_matched", template_hash=tpl_hash,
                         verdict=pv.verdict, score=pv.score)
                results.append({
                    "kind": kind, "name": name, "template_id": template.template_id,
                    "verdict": pv.verdict, "reason": pv.reason, "score": pv.score,
                    "detected": pv.detected, "expected": pv.expected,
                    "fields": fields, "tables": tables,
                    "updated_at": updated_at,
                })
            budget_elapsed_ms = int((_time.perf_counter() - budget_t0) * 1000)
    except Exception as e:  # noqa: BLE001 — ループ外枠の想定外failure（internal）
        import traceback
        log.error("match_templates_failed", error_code=type(e).__name__)
        log.error_trace(type(e).__name__, "".join(traceback.format_tb(e.__traceback__)))
        _progress({"event": "match_templates", "ok": False, "error": "internal"})
        return 0

    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    log.info("match_templates_done", count=len(results), failed=len(excluded))
    _progress({"event": "match_templates", "ok": True,
               "input_size": input_size,
               "results": results, "excluded": excluded,
               "truncated": truncated, "elapsed_ms": elapsed_ms,
               "budget_elapsed_ms": budget_elapsed_ms})
    return 0


def cmd_debug_images(args) -> int:
    """読み取りの可視化画像を出力する（開発者モード・API 送信なし）。

    どの文字がどの欄に入ったか／なぜ〓になったかを1ページ1枚の PNG で示す。
    出力は既定で workdir/debug/（読取値が画像に描かれるため中間データ扱い）。

    2026-08-31（5巡目 第3〜4段・#59 H-5・#60 M-1①④）:
    - --out に同期フォルダ検査を適用する（H-5）。読取値・信頼度を焼き込んだ
      画像は中間データより濃い個人情報で、既定の workdir/debug/ は purge・
      verify の同期検査の対象だが --out は検査の外を通っていた
    - render/remap と同じテンプレート整合ゲート（check_reusable）を通す
      （M-1①）。通さないと、テンプレート変更後に「現テンプレの枠」×「旧
      テンプレ割付の〓判定」を1枚に重ねた嘘の可視化を返してしまう
    - 生成0件を ok:true, count:0 で固定せず、理由付きの ok:false で返す
      （M-1④）。存在しないページ ID 指定は該当なしと明示する
    """
    from .align import geometry_hash, template_hash as _tpl_hash
    from .columns import validate_v1
    from .config import load_config
    from .debug_images import write_debug_images
    from .paths import is_cloud_synced_path
    from .pipeline import check_reusable
    from .store import Store
    from .template import load_template
    cfg = _load_config_and_init_log(args.config)
    wd = Path(cfg.workdir)
    out_dir = Path(args.out) if args.out else wd / "debug"
    # --out の同期フォルダ検査（#59 H-5）。既定（workdir/debug）は従来どおり
    # 検査しない——workdir 自体が同期フォルダ配下かは verify が別途見ている
    if args.out and is_cloud_synced_path(out_dir):
        # 業務的な拒否は exit 0 で伝える（レビュー差し戻し M-3・main() の規約
        # コメント :443-450 と同じ理由）。ここだけ 1 を返すと、同一コマンド内の
        # page_not_found/no_pages/OperationRefused（すべて 0）と矛盾し、
        # 呼び出し側が「1=再試行で直る一時失敗」と誤解しうる
        _progress({"event": "debug_images", "ok": False, "reason": "synced_path",
                   "error": "--out が同期フォルダ配下を指している。読取値を焼き込んだ"
                            "画像は要配慮個人情報を含むため、同期対象外の場所を指定する",
                   "synced_dir": str(out_dir)})
        return 0
    template = load_template(args.template)
    raw = json.loads(Path(args.template).read_text(encoding="utf-8"))
    # debug-images も pipeline._load を経由しないため template_loaded を
    # 自前で出す（cmd_verify と同じ理由・不変条件A・Q-S1・FR-F50・
    # 08_frame_detection_design.md §1.4）。validate_v1 より前に出す
    # （2026-09-02 #77 追補・マリン指摘）——後ろだと validate_v1 が
    # TemplateError で落ちたときに「cell_idx はあるが template_hash が無い」
    # 状態が残る
    log.info("template_loaded", template_hash=_tpl_hash(raw))
    validate_v1(template)
    with Store(wd / "intermediate.sqlite") as store:
        # テンプレート変更後の「旧割付×新枠」の嘘可視化を拒否する（#60 M-1①）。
        # render と同じ整合ゲート——check_template=True で cell の割付内容も
        # テンプレート由来かを見る。不一致なら OperationRefused（main() が拒否
        # として処理し、with が確実に close する・Q-MG「閉じるのは開いた側」）
        check_reusable(store, geometry_hash(raw), _tpl_hash(raw), check_template=True)
        all_pages = store.pages()
        all_ids = {p["page_id"] for p in all_pages}
        if args.page and args.page not in all_ids:
            _progress({"event": "debug_images", "ok": False, "reason": "page_not_found",
                       "error": f"ページ '{args.page}' が中間データに無い", "count": 0,
                       "dir": str(out_dir.resolve())})
            return 0
        if not all_pages:
            _progress({"event": "debug_images", "ok": False, "reason": "no_pages",
                       "error": "中間データにページが無い（run で処理してから実行する）",
                       "count": 0, "dir": str(out_dir.resolve())})
            return 0
        # cfg 本体を渡す（レビュー差し戻し M-1）。以前は cfg.unclear_threshold
        # のみを渡しており、debug_images 側で Config を組み直すと
        # unclear_char_level が常に既定 False に落ちていた
        made = write_debug_images(
            store, template, wd / "aligned", out_dir,
            cfg,
            page_ids=[args.page] if args.page else None)
    if not made:
        _progress({"event": "debug_images", "ok": False, "reason": "no_aligned_images",
                   "error": "位置合わせ済み画像が無い（未処理、または位置合わせ失敗の"
                            "ページのみ）ため、可視化画像を1枚も作れなかった",
                   "count": 0, "dir": str(out_dir.resolve())})
        return 0
    _progress({"event": "debug_images", "ok": True,
               "count": len(made), "dir": str(out_dir.resolve())})
    for m in made:
        print(str(m.resolve()))
    return 0


def cmd_detect_frames(args) -> int:
    """ページ全体（領域指定なし）からの枠候補一括生成（issue #73 (b)・08 §4）。

    `detect-grid`（`--region` 必須）とは独立の新系統。`--template` を渡すと
    ①面の除外領域を検出前に白潰し ②候補への face_id 割り当て
    ③既存セルとの重なり（overlaps_existing）判定 が有効になる（08 §4.1.5・
    §4.2.3）。渡さなければ face_id は全候補 "page"・overlaps_existing は
    常に False。

    **テンプレートは「位置合わせ後・`image_size` と一致する寸法のページ
    画像」を前提とする**（M-3）——`--input` の実寸とテンプレートの
    `image_size` が一致しない場合、除外白潰し・face_id 割り当て・
    `overlaps_existing` 判定を**行わず**、線分抽出のみ行う（`--template`
    未指定と同じ扱い）。この場合 JSON へ `template_applied: false` と
    `template_skip_reason: "size_mismatch"` を返す（寸法一致時は
    `true`/`null`、`--template` 未指定時は `null`/`null`）。寸法の合わない
    テンプレートをそのまま当てると、無関係な面座標に基づいて誤った
    face_id・重なり判定を返しかねないため。

    ログにはテンプレート名・欄名を出さない（Q-S1・FR-F50 の方針）。
    """
    import time as _time

    import numpy as np
    from PIL import Image

    from .align import _otsu
    from .align import template_hash as _tpl_hash
    from .grid import detect_frames
    from .ingest import IngestError, expand, pdf_page_count
    from .template import Rect as _Rect
    from .template import TemplateError, load_template

    t0 = _time.perf_counter()
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9 と同じ流儀）
    # M-6: expand() は out_dir 直下の同一 stem 残骸（-aligned.png 含む）を
    # 展開のたびに掃除する（ingest.expand の仕様）。editor_pages と共用すると
    # 編集画面が開いている -aligned.png を detect-frames の実行が消しうるため、
    # 専用ディレクトリに分ける
    out_dir = Path(cfg.workdir) / "detect_frames_pages"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = Path(args.input)

    if not src.exists():
        _progress({"event": "detect_frames", "ok": False, "error": "input_not_found"})
        return 0
    total = pdf_page_count(src) if src.suffix.lower() == ".pdf" else 1
    if total is not None and not 1 <= args.page <= total:
        _progress({"event": "detect_frames", "ok": False, "error": "expand_failed"})
        return 0

    template = None
    dpi = args.dpi
    if args.template:
        try:
            template = load_template(args.template)
        except TemplateError:
            _progress({"event": "detect_frames", "ok": False, "error": "template_invalid"})
            return 0
        # detect-frames も pipeline._load を経由しないため template_loaded を
        # 自前で出す（cmd_verify/cmd_expand_page と同じ理由・不変条件A・
        # Q-S1・FR-F50・08_frame_detection_design.md §1.4。H-1 レビュー指摘）
        log.info("template_loaded", template_hash=_tpl_hash(
            json.loads(Path(args.template).read_text(encoding="utf-8"))))
        # --template 指定時はテンプレートの render_dpi を優先する
        # （FR-F23・06 §7 の未配線を繰り返さない）
        dpi = template.render_dpi

    try:
        # PDF は該当ページのみ展開（expand-page と同じ経路・PNG/JPG はそのまま）
        pages = expand(src, dpi=dpi, out_dir=out_dir, page=args.page)
    except IngestError:
        _progress({"event": "detect_frames", "ok": False, "error": "expand_failed"})
        return 0

    try:
        # LOW: Image.open 成功後 load() で失敗しても close されるよう
        # with ブロックの中で読み切る（以前は img.load() 失敗時に
        # close 漏れがあった）
        with Image.open(pages[0]) as img:
            img.load()
            gray = np.asarray(img.convert("L"))
            input_size = [img.width, img.height]
    except (OSError, ValueError):
        _progress({"event": "detect_frames", "ok": False, "error": "input_unreadable"})
        return 0

    # 二値化は align の流儀（08 §4.1.5）。align.binarize_face は面ローカルの
    # 除外マスク前提のため使わない——ページ全体へ Otsu を適用するだけ
    no_exclusion = np.zeros(gray.shape, dtype=bool)
    th = _otsu(gray, no_exclusion)
    binary = gray < th

    # M-3: テンプレートの image_size と入力実寸が一致するときのみ、除外
    # 白潰し・face_id 割り当て・overlaps_existing を有効にする
    template_applied: bool | None = None
    template_skip_reason: str | None = None
    effective_template = None
    if template is not None:
        if input_size == [template.image_size[0], template.image_size[1]]:
            template_applied = True
            effective_template = template
        else:
            template_applied = False
            template_skip_reason = "size_mismatch"

    # --template 指定時（かつ寸法一致時）のみ、面の除外領域（ページ座標）を
    # 検出前に白潰しする（08 §4.1.5）
    exclusions: list[_Rect] = []
    if effective_template is not None:
        for f in effective_template.faces:
            r = f.source_rect
            for ex in f.exclusions:
                exclusions.append(_Rect(r.x + ex.x, r.y + ex.y, ex.w, ex.h))

    result = detect_frames(binary, dpi, exclusions=exclusions, existing=effective_template)

    candidates: list[dict] = []
    for t in result.tables:
        candidates.append({
            "kind": "table",
            "face_id": t.face_id or "page",
            "rect": {"x": t.rect.x, "y": t.rect.y, "w": t.rect.w, "h": t.rect.h},
            "blocks": [{"x": t.origin_x, "y": t.origin_y, "rows": t.rows}],
            "row_pitch": t.row_pitch,
            "row_height": t.row_height,
            "columns": t.columns,
            "residual_px": t.residual_px,
            "overlaps_existing": t.overlaps_existing,
        })
    for fcand in result.fields:
        candidates.append({
            "kind": "field",
            "face_id": fcand.face_id or "page",
            "rect": {"x": fcand.rect.x, "y": fcand.rect.y,
                    "w": fcand.rect.w, "h": fcand.rect.h},
            "residual_px": fcand.residual_px,
            "overlaps_existing": fcand.overlaps_existing,
        })

    elapsed_ms = int((_time.perf_counter() - t0) * 1000)
    _progress({"event": "detect_frames", "ok": True,
               "input_size": input_size,
               "candidates": candidates,
               "stats": result.stats,
               "excluded": list(result.excluded),  # H-3: 除外理由の内訳を返す
               "zero_reason": result.zero_reason,
               "template_applied": template_applied,
               "template_skip_reason": template_skip_reason,
               "elapsed_ms": elapsed_ms})
    return 0


def cmd_detect_grid(args) -> int:
    """枠候補の生成（設計 §6.9）。テンプレート編集画面が呼ぶ・GUI なしでも検証可。"""
    from .grid import detect_ruled, make_uniform
    cfg = _load_config_and_init_log(getattr(args, "config", None))  # M-9
    region = tuple(int(v) for v in args.region.split(","))
    if args.mode == "uniform":
        fit = make_uniform(region, args.rows, args.cols, dpi=args.dpi)   # 画像を読まない
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
        fit = detect_ruled(gray, region, dpi=args.dpi)
        if fit is None:
            _progress({"event": "detect_grid", "ok": False,
                       "error": "罫線が検出できない。等分割生成（--mode uniform）へ切り替える"})
            return 0
    _progress({"event": "detect_grid", "ok": True, **fit.to_json()})
    return 0


# 出力先から消してよいのは、このツールが作った命名に一致するファイルだけ
# （S-MC）。output_dir は GUI で任意のフォルダ（デスクトップ・共有フォルダ等）を
# 指せるため、rmtree もディレクトリ削除も使わない——1件ずつ unlink する。
# 命名の正は render_out.write_outputs（`output_<ts>.xlsx` / `.csv` /
# `_columns.txt`）と、その退避 `+".bak"`・一時ファイル `.xlsx.tmp` /
# `.csv.tmp` / `.txt.tmp`。ts は pipeline._render_locked の
# time.strftime("%Y%m%d_%H%M%S")。呼び出し側が渡す形式も受けられるよう
# 14桁連番（区切りなし）も許容するが、それ以外の任意文字列は受けない
# （テストが渡す timestamp="g4" のような名前を対象にしない）
_OUTPUT_TS = r"(?:\d{8}_\d{6}|\d{14})"
_OUTPUT_NAME_RE = re.compile(
    rf"^output_{_OUTPUT_TS}(?:\.xlsx|\.csv|_columns\.txt)(?:\.bak|\.tmp)?$")
_OUTPUT_TS_RE = re.compile(rf"^output_({_OUTPUT_TS})")


def _output_purge_targets(out_dir: Path) -> tuple[list[Path], int]:
    """出力先を走査して (削除対象, 対象外として残るファイル数) を返す。

    走査は out_dir 直下のみ（再帰しない）。ディレクトリは対象にも件数にも
    含めない——「残るファイル数」は利用者が手で置いたファイルの見える化が
    目的で、消し忘れの判断材料にする。存在しない・空でも例外にしない。
    """
    if not out_dir.is_dir():
        return [], 0
    targets: list[Path] = []
    kept = 0
    for p in sorted(out_dir.iterdir()):
        if not p.is_file():
            continue
        if _OUTPUT_NAME_RE.match(p.name):
            targets.append(p)
        else:
            kept += 1
    return targets, kept


def _output_timestamps(targets: list[Path]) -> list[str]:
    """削除対象のファイル名から日時部分だけを取り出す（記入値は含まない）。"""
    found = set()
    for p in targets:
        m = _OUTPUT_TS_RE.match(p.name)
        if m:
            found.add(m.group(1))
    return sorted(found)


def _is_reparse_point(p: Path) -> bool:
    """symlink または Windows ジャンクション（mount point）か（issue #83）。

    Path.is_symlink() は IO_REPARSE_TAG_SYMLINK しか見ない——ジャンクション
    （IO_REPARSE_TAG_MOUNT_POINT・`mklink /J`）を検出するには
    os.path.isjunction()（Python 3.13+）も併用する必要がある（M-2 と同じ
    規律・paths.py:73-80 の user_templates_dir() 参照）。
    """
    return p.is_symlink() or os.path.isjunction(p)


def _remove_workdir_entry(p: Path) -> None:
    """workdir 直下の1エントリを削除する（issue #83・レビュー指摘対応）。

    reparse point（symlink・ジャンクション）はリンク先を辿らずリンク自体
    だけを外す——rmtree をリンクに対して呼ぶと（Python/OS の組み合わせに
    よっては）リンク先の中身ごと消える事故になるため、リンクの種別に応じて
    os.rmdir（ディレクトリ型 reparse point）／unlink（ファイル型）を使い分け、
    rmtree には通常のディレクトリだけを渡す。

    読み取り専用ファイルは PermissionError になるため、chmod で書き込み
    許可を復元してから一度だけ再試行する（_output_purge_targets の
    「1件の失敗で残りを諦めない」規律をここでも踏襲——失敗はそのまま
    OSError を呼び出し元へ伝播させ、続行判断は _purge_workdir に委ねる）。
    """
    if _is_reparse_point(p):
        # lstat（リンク自体を見る・辿らない）でリンクの型を判定する
        # （AZKi 指摘）。p.is_dir() はリンク先を辿って判定するため、リンク先
        # が壊れている（dangling）ジャンクションでは判定できない・誤判定
        # しうる。os.lstat().st_mode ならリンク自体の属性を見るので、
        # リンク先の生死に関わらず正しく rmdir/unlink を選べる
        if stat.S_ISDIR(os.lstat(p).st_mode):
            os.rmdir(p)          # ジャンクション・ディレクトリ symlink
        else:
            p.unlink()           # ファイル symlink
        return
    if p.is_dir():
        def _clear_readonly_and_retry(func, path, _exc_info):
            os.chmod(path, stat.S_IWRITE)
            func(path)
        shutil.rmtree(p, onexc=_clear_readonly_and_retry)
    else:
        try:
            p.unlink()
        except PermissionError:
            os.chmod(p, stat.S_IWRITE)
            p.unlink()


def _purge_workdir(wd: Path) -> tuple[bool, int, int]:
    """workdir 配下を資格情報以外すべて削除する（issue #83・keep-list 方式）。

    以前は shutil.rmtree(wd) で workdir ごと丸ごと消しており、workdir 直下に
    置かれる暗号化資格情報 cred_store.blob_name()（cred.dpapi）まで巻き込んで
    消えていた。「消してよいものを列挙する」方式は列挙漏れがそのまま個人情報
    の残留・削除漏れになるため採らず、「残すものだけを cred.dpapi 1つに限定し、
    それ以外は種類を問わず全部消す」fail-closed な keep-list 方式に切り替える
    （PM判断・#83）。将来 workdir 配下の中間データの種類が増えても、この
    keep-list には載らない限り自動で消える。

    golden/・s2/ のような開発素材が workdir に同居していても、ここでは
    特別扱いしない（purge の責務ではなく、workdir 構造側の別課題）。

    reparse point 対策（#83 のレビュー指摘）: workdir 自身が
    symlink・ジャンクションの場合はこの関数を呼ぶ前に cmd_purge が弾く
    （wd.iterdir() は reparse point 越しにリンク先を列挙してしまうため、
    ここへ来る時点で wd は実ディレクトリであることが前提）。配下の各
    エントリが reparse point の場合は _remove_workdir_entry がリンク自体
    だけを外す。cred.dpapi という名前の symlink は資格情報の実体ではない
    ため keep 対象にしない（reparse point 判定を名前一致より先に見る）。

    個々の削除は OSError を捕まえて続行する——1件の失敗（使用中・読み取り
    専用）で残りを諦めると中途半端に個人情報が残るため。

    戻り値: (cred.dpapi を残せたか, 削除できた件数, 削除できなかった件数)。
    workdir が存在しない場合は (False, 0, 0)。
    """
    if not wd.exists():
        return False, 0, 0
    blob = cred_store.blob_name()
    kept_cred = False
    removed = failed = 0
    for p in sorted(wd.iterdir()):
        if not _is_reparse_point(p) and p.name == blob and p.is_file():
            kept_cred = True
            continue
        try:
            _remove_workdir_entry(p)
            removed += 1
        except OSError:
            failed += 1
    return kept_cred, removed, failed


def cmd_purge(args) -> int:
    cfg = _load_config_and_init_log(args.config)  # 監査ログの欠落を防ぐ（M-9）
    if not args.yes:
        print("中間データ削除には --yes が必要（要件 §6.3: 削除は明示操作のみ）",
              file=sys.stderr)
        return 1
    wd = Path(cfg.workdir)
    # M-2 と同じ規律（paths.py の user_templates_dir()）。wd.iterdir() は
    # reparse point 越しにリンク先を列挙してしまうため、削除前にここで弾く
    # （fail-closed）——列挙してから個別に弾く方式だと、iterdir 自体が
    # 意図しないリンク先の中身を返す時点で手遅れになる
    if _is_reparse_point(wd):
        print(f"workdir が symlink またはジャンクションになっているため削除しない"
              f"（{wd}）。config.json の workdir 設定を確認してから再実行する。",
              file=sys.stderr)
        return 1
    cred_kept, wd_removed, wd_failed = _purge_workdir(wd)
    # cred.dpapi の中身やそのファイル自身のパスは出さない。既存の "path" は
    # workdir のルートで元々出ていたもの（cfg.workdir・利用者が設定した値）。
    # 資格情報側で新たに足すのは残せたかどうかの真偽値と削除件数のみ（S-MC）
    event = {"event": "purged", "path": str(wd), "cred_kept": cred_kept,
              "removed": wd_removed, "failed": wd_failed}
    # --include-output 側（削除 N 件／対象外として残したファイル N 件）と
    # 同じ形で、workdir 側も人が読む1行を必ず出す（AZKi 指摘: 消し損ねが
    # あっても「purged」とだけ出て気づかれない事故を防ぐ）
    cred_note = "資格情報は残した" if cred_kept else "資格情報は無かった"
    print(f"中間データ {wd_removed} 件を削除した（{cred_note}）")
    rc = 0
    if wd_failed:
        print(f"workdir 内の {wd_failed} 件を削除できなかった（使用中または"
              "権限の問題が残っている可能性がある）。閉じるか権限を確認して"
              "から再実行する。", file=sys.stderr)
        rc = 1
    if args.include_output:
        out_dir = Path(cfg.output_dir)
        targets, kept = _output_purge_targets(out_dir)
        # 消す前に何を消すかを残す（S-MC）。出るのはファイル名の日時と件数だけで
        # 帳票の記入値は含まない。kept は「消したつもり」を防ぐための可視化
        log.info("purge_output_scan", path=str(out_dir), count=len(targets),
                 kept=kept, timestamps=",".join(_output_timestamps(targets)))
        removed = failed = 0
        for p in targets:
            try:
                p.unlink()
                removed += 1
            except OSError:
                # Excel で開いたままだと消せない（PermissionError）。1件の失敗で
                # 残りを諦めると中途半端に個人情報が残るため、続行して件数で返す
                failed += 1
        log.info("purge_output_done", count=removed, failed=failed, kept=kept)
        event.update({"output_dir": str(out_dir), "output_removed": removed,
                      "output_kept": kept, "output_failed": failed})
        # 標準出力の JSON Lines（§7.3）は GUI 用だが、purge は GUI 境界で禁止
        # されている（lib.rs の check_args_v2）CLI 専用コマンドなので、人が読む
        # 1行を併記する
        print(f"削除 {removed} 件／対象外として残したファイル {kept} 件")
        if failed:
            print(f"削除できないファイルが {failed} 件ある（Excel などで開かれて"
                  "いる可能性）。閉じてからやり直す。", file=sys.stderr)
            rc = 1
    _progress(event)
    return rc


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
    p.add_argument("--expect-columns", type=int, default=None,
                   help="期待する最小列数。実際の列数がこれを下回ったら"
                        "template チェックを失敗として報告する（GUI 側の"
                        "読み込み時基準が取得できない場合の最後の砦・"
                        "issue #65-1）。省略時は従来どおり比較しない")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("import-credentials", help="資格情報 JSON を DPAPI 暗号化で取り込む")
    p.add_argument("json_path")
    p.set_defaults(fn=cmd_import_credentials)

    p = sub.add_parser("expand-page", help="PDF の1ページを PNG 展開（編集画面用）")
    p.add_argument("--input", required=True)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--template", default=default_tpl)
    p.add_argument("--no-mask", action="store_true",
                   help="除外領域を白塗りしない下地を返す（テンプレート編集画面が"
                        "除外枠を調整する用途専用・#59 H-8）。run には無い")
    p.set_defaults(fn=cmd_expand_page)

    p = sub.add_parser("match-templates",
                       help="入力1枚を出荷＋候補テンプレートへ照合する（編集画面用・issue #72）")
    p.add_argument("--input", required=True)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--shipped", required=True, help="出荷テンプレートの絶対パス")
    p.add_argument("--candidate", action="append",
                   help="利用者テンプレートの絶対パス（反復指定可）。列挙は呼び出し側"
                        "（Rust）の責務——ここは渡されたパスをそのまま読む")
    p.set_defaults(fn=cmd_match_templates)

    p = sub.add_parser("debug-images",
                       help="読み取りの可視化画像を出力（開発者モード・API送信なし）")
    p.add_argument("--template", default=default_tpl)
    p.add_argument("--out", default=None)
    p.add_argument("--page", default=None, help="特定の帳票IDのみ出力")
    p.set_defaults(fn=cmd_debug_images)

    p = sub.add_parser("detect-frames",
                       help="ページ全体からの枠候補一括生成（領域指定なし・issue #73）")
    p.add_argument("--input", required=True)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--dpi", type=_render_dpi_arg, default=300,
                   help="展開 dpi（既定 300・72〜1200）。--template 指定時は"
                        "テンプレートの render_dpi を優先する")
    p.add_argument("--template", default=None,
                   help="任意。指定すると除外領域の白潰し・face_id 割り当て・"
                        "既存枠との重なり判定が有効になる")
    p.set_defaults(fn=cmd_detect_frames)

    p = sub.add_parser("detect-grid", help="枠候補の生成（罫線検出 or 等分割）")
    p.add_argument("--image", help="面画像（--mode ruled で必須）")
    p.add_argument("--region", required=True, help="x,y,w,h（面ローカル）")
    p.add_argument("--mode", choices=["ruled", "uniform"], default="ruled")
    p.add_argument("--rows", type=int, default=1)
    p.add_argument("--cols", type=int, default=1)
    # px 定数（grid.ROW_INSET）の dpi 正規化（汎用化 A-3）。既定 300（=BASE_DPI）は
    # 未対応の呼び出し元（省略時）が従来どおりの値を得るための後方互換。
    # 範囲検証は schema の render_dpi と同値（72〜1200・S-8）
    p.add_argument("--dpi", type=_render_dpi_arg, default=300,
                   help="この画像の render_dpi（既定 300・72〜1200）。ROW_INSET 等の"
                        "px 定数をこの dpi に合わせてスケールする")
    p.set_defaults(fn=cmd_detect_grid)

    p = sub.add_parser("purge", help="中間データの削除（--yes 必須）")
    p.add_argument("--yes", action="store_true")
    p.add_argument("--include-output", action="store_true",
                   help="出力先の生成物（output_<日時>.xlsx / .csv / "
                        "_columns.txt と、その .bak・.tmp）も削除する。"
                        "フォルダ自体と、この命名に一致しないファイルは残す")
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
