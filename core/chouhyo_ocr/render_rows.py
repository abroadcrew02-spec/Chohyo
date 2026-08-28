"""出力1行の組み立て（〓判定・D-01・D-23・元号5値の適用。設計 §6.5〜§6.6）。

閾値の適用はここ（render 段）だけ。cell には読取値と信頼度しか入っていない。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from . import era
from .config import Config
from .normalize import normalize_amount, split_composite
from .template import Template

UNCLEAR = "〓"

# xlsx に書けない制御文字（openpyxl の ILLEGAL_CHARACTERS_RE と同範囲）。
# Vision がこれを返した読取値は書き込み時に例外になるうえ内容も信頼できない
# ため、〓へ倒して目検に回す（issue #2・値がそのまま例外メッセージへ乗るのを防ぐ）
_ILLEGAL_XLSX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# ステータス連結順（§4.5・失敗系を先に）
STATUS_EXPAND_FAILED = "展開失敗"
STATUS_ALIGN_FAILED = "位置合わせ失敗"
STATUS_FORMAT_MISMATCH = "様式不一致"
STATUS_SEND_FAILED = "送信失敗"
STATUS_CAP = "未処理（送信上限到達）"
STATUS_INTERRUPTED = "未処理（中断）"
# 「未処理」を名乗らせない（PM 裁定・2026-08-28）: §5.8 で「未処理」は再送対象の
# 含意が確立しており、重複は再実行でも送信しない。名前は仕様である
STATUS_DUPLICATE = "スキップ（重複）"
STATUS_OVERFLOW = "超過あり"
STATUS_OK = "正常"
# 失敗系（全〓行になるステータス）。compose_status がページ status を通す集合
_FAILURE_STATUSES = frozenset([
    STATUS_EXPAND_FAILED, STATUS_ALIGN_FAILED, STATUS_FORMAT_MISMATCH,
    STATUS_SEND_FAILED, STATUS_CAP, STATUS_INTERRUPTED, STATUS_DUPLICATE])

OVERFLOW_MIN_SYMBOLS = 3  # D-06（定数・実物で調整）

# D-15: 枠外 symbol（below_table を除く）の率がこれを超えたら様式不一致。
# 印字ラベル等で正常ページでも ~0.25 程度は枠外に落ちる（実測）ため高めに置く。
# 実物データ到着後に較正する（設計 §4.6）
FORMAT_MISMATCH_RATIO = 0.55


@dataclass(frozen=True)
class Row:
    page_id: str
    source_file: str
    page_no: int
    status: str
    values: list          # 抽出対象列の値（str または int）。列順は columns の抽出部
    unclear_count: int
    min_conf: str         # "0.812" 形式または ""

    def __repr__(self) -> str:  # 記入値を repr へ出さない（設計 §8.1・付録 C7）
        return f"<{type(self).__name__} redacted>"



def compose_status(page_status: str, below_table: int, processed: bool) -> str:
    parts = [page_status] if page_status in _FAILURE_STATUSES else []
    if processed and below_table >= OVERFLOW_MIN_SYMBOLS:
        parts.append(STATUS_OVERFLOW)
    if not parts:
        if page_status and page_status != STATUS_OK:
            # 既知集合に無いステータスを「正常」へ倒さない（レビュー B-4）。
            # 中間データは版をまたいで残るため、定数を1つ改名しただけで
            # 旧 status を持つ既存ページが黙って正常になる事故を防ぐ
            return page_status
        return STATUS_OK if processed else STATUS_OK
    return ";".join(parts)


def build_row(template: Template, page: dict, cells: dict[str, tuple],
              era_scores: dict[str, dict], cfg: Config) -> Row:
    """処理済みページの1行。cells: field_id → (raw, conf, kind, is_empty_row)。"""
    values: list = []
    confs: list[float] = []

    for cell in template.cells:
        raw, conf, _kind, is_empty = cells.get(cell.field_id, ("", None, cell.kind, False))
        out_cols = cell.output_columns()

        if cell.kind == "choice":
            decision = era.decide(era_scores.get(cell.field_id, {}), cfg.era_threshold)
            if is_empty:
                values.append("")
            elif decision in (era.UNSELECTED, era.UNDECIDED):
                values.append(UNCLEAR)
            else:
                values.append(decision)
            continue

        # text セル
        if is_empty:
            values.extend([""] * len(out_cols))
            continue
        # 型不正の conf は「信頼度なし」＝〓へ倒す（issue #39）。数値比較で
        # TypeError を出すと、そのページだけでなくバッチ全体の出力が失われる
        if conf is not None and (isinstance(conf, bool)
                                 or not isinstance(conf, (int, float))):
            conf = None
        unclear = ((raw == "") or (conf is None) or (conf < cfg.unclear_threshold)
                   or bool(_ILLEGAL_XLSX.search(raw)))
        if unclear:
            values.extend([UNCLEAR] * len(out_cols))
            continue

        # 最低信頼度は「**出力された値**のうちの最小」。分割失敗・正規化失敗で
        # 〓になったセルの信頼度を混ぜない（レビュー B-1）——〓の優先確認に
        # 使う列なのに、出ていない値の信頼度が最小として出ていた
        if cell.subfields:
            parts = split_composite(raw, len(cell.subfields))
            if parts:
                values.extend(parts)
                confs.append(conf)
            else:
                values.extend([UNCLEAR] * len(out_cols))
        elif cell.normalize == "amount":
            amount = normalize_amount(raw)
            if amount is not None:
                values.append(amount)
                confs.append(conf)
            else:
                values.append(UNCLEAR)
        else:
            values.append(raw)
            confs.append(conf)

    unclear_count = sum(1 for v in values if v == UNCLEAR)
    min_conf = f"{min(confs):.3f}" if confs else ""
    status = compose_status(page.get("status", ""), page.get("unassigned_below_table", 0),
                            processed=True)
    return Row(page["page_id"], page["source_file"], page["page_no"],
               status, values, unclear_count, min_conf)


def build_failure_row(template: Template, page: dict) -> Row:
    """未処理・失敗ページの全〓行（要件 §3.4: 入力ページ数＝出力行数を常に維持）。"""
    n = sum(len(c.output_columns()) for c in template.cells)
    status = compose_status(page.get("status", ""), 0, processed=False)
    return Row(page["page_id"], page["source_file"], page["page_no"],
               status, [UNCLEAR] * n, n, "")
