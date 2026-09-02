"""ログ（設計 §8.1）。`import logging` はこのモジュールに限る（§12-C6）。

帳票の記入値は一切書かない。出力してよいのは 入力ファイル名・ページ番号・
帳票ID・欄の匿名序数・処理ステップ名・エラーコード・信頼度の数値・設定値・
件数のみ。許可キー以外は黙って落とす（型で守れない書き方への最後の網）。

2026-09-02（Q-S1・FR-F50・NFR-F05 拡張）: 秘匿対象を「記入値」から
「記入値＋テンプレートファイル名＋欄名（field_id・列名・table_id）」へ拡張。
`template_path`・`field_id` は白リストから外した（テンプレート名・欄名が
機微情報になりうるため）。**`face_id` はこの変更で外したのではなく、元々
白リストに入っていなかった**——それが `adjacent_gap_w3`／`hole_overlap_w4`
（template.py の W-3/W-4 警告）が `face_id`／`field_a`／`field_b` を渡しな
がら実際にはイベント名しかログに残っていなかった原因（診断が黙って死んで
いた実害・2026-09-02 実測・08_frame_detection_design.md §1.1）。代替として
`template_hash`（テンプレート全体のハッシュ・issue #59 H-7 からの既存キー）
と、匿名識別子 `cell_idx`（template.cells 内の0始まり序数）・`face_idx`
（template.faces 内の0始まり序数）・`cell_a`/`cell_b`（欄2つを比較する警告用・
値は cell_idx と同じ空間）・`col_idx`（出力列（抽出対象列）内の0始まり序数）・
`gap_px`（px 値・名前を含まない）を残す／追加した。適用範囲はログ（app.log・
error.log）のみ——GUI 表示・stdout の JSON Lines・出力ファイルは対象外
（詳細は docs/design/chouhyo-ocr/07_frame_detection_requirements.md §0.6）。

**復号手順**（人が `cell_idx=137` を欄名に戻す方法）: 同じ run のログに残る
`template_loaded template_hash=...`（または `run_start` 直後の
`template_loaded`）でテンプレートを特定し、そのテンプレート JSON を
`template.load_template()` で読み、`template.cells[137].field_id` を見る。
`col_idx=N` を列名に戻すには `columns.extract_columns(template)[N]`
（＝`columns.derive_columns(template)[len(columns.META_COLUMNS) + N]`。
抽出対象列は管理6列の後ろに続く）。`cell_idx`・`face_idx`・`cell_a`/`cell_b`・
`col_idx` は **template_hash とセットでのみ意味を持つ**——テンプレートを
1欄でも足すと序数がずれる（docs/design/chouhyo-ocr/08_frame_detection_design.md
§1.4 不変条件A）。
"""
from __future__ import annotations

import logging
from pathlib import Path

_ALLOWED_KEYS = {
    "source_file", "page_no", "page_id", "step", "error_code",
    "conf", "count", "duplicate_of", "path", "state", "status", "attempt",
    # テンプレート全体のハッシュ（issue #59 H-7）。帳票の記入値もファイル名も
    # 含まない。出力がどのテンプレート由来かを事後特定できるようにする
    "template_hash",
    # テンプレート内の欄・面・出力列の匿名識別子（Q-S1・FR-F50・2026-09-02）。
    # 欄名・面ID・列名（field_id・face_id・table_id・列名）はログへ出さない
    # 代わりに、序数のみを残す——template_hash と組み合わせれば診断先の欄を
    # 特定できる。cell_a/cell_b は欄2つを比べる警告（W-3・W-4）用で、値の
    # 空間は cell_idx と同じ。gap_px は px 値のみで名前を含まない
    "cell_idx", "face_idx", "cell_a", "cell_b", "col_idx", "gap_px",
    # 様式判定（issue #71 (a')・08 §2.5.3）。verdict は列挙値（match/
    # mismatch/undecidable/skipped/unknown）、reason_code は理由コード
    # （lines/ambiguous/edge/few_lines/boundary 等・記入値を含まない固定語彙）、
    # score は無次元スコア、detected/expected は検出・期待の罫線本数。
    # いずれも名前・記入値を含まない
    "verdict", "reason_code", "score", "detected", "expected",
    # 入力ページの寸法比（Q-H1・align.align_page の page_scale ログ）。
    # 記入値ではなく、幅/高さをテンプレート寸法で割った比率のみ
    "sx", "sy",
    # purge --include-output の削除実績（S-MC）。件数と、ファイル名の日時部分
    # （output_<日時>.xlsx の <日時>）のみで記入値は含まない
    "kept", "failed", "timestamps",
}

_app: logging.Logger | None = None
_err: logging.Logger | None = None


def init(log_dir: str | Path) -> None:
    global _app, _err
    d = Path(log_dir)
    d.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    _app = logging.getLogger("chouhyo.app")
    _app.setLevel(logging.INFO)
    _app.handlers.clear()
    h = logging.FileHandler(d / "app.log", encoding="utf-8")
    h.setFormatter(fmt)
    _app.addHandler(h)

    _err = logging.getLogger("chouhyo.error")
    _err.setLevel(logging.WARNING)
    _err.handlers.clear()
    h2 = logging.FileHandler(d / "error.log", encoding="utf-8")
    h2.setFormatter(fmt)
    _err.addHandler(h2)


def _fmt(event: str, fields: dict) -> str:
    safe = {k: v for k, v in fields.items() if k in _ALLOWED_KEYS}
    body = " ".join(f"{k}={v}" for k, v in sorted(safe.items()))
    return f"{event} {body}".rstrip()


def info(event: str, **fields) -> None:
    if _app:
        _app.info(_fmt(event, fields))


def warn(event: str, **fields) -> None:
    """警告は app.log のみへ（設計 §8.1: error.log はエラー・失敗内容に限る）。"""
    if _app:
        _app.warning(_fmt(event, fields))


def error(event: str, **fields) -> None:
    if _err:
        _err.error(_fmt(event, fields))
    if _app:
        _app.error(_fmt(event, fields))


def error_trace(error_code: str, stack: str) -> None:
    """未捕捉例外のスタックを error.log へ残す（issue #2）。

    stack は traceback.format_tb の出力（ファイル/行/関数とソース行のみ）を
    想定する。例外メッセージ本文は帳票の値を含みうるため受け取らない。
    """
    if _err:
        _err.error(f"unhandled_exception error_code={error_code}\n{stack.rstrip()}")
