"""出力1行の組み立て（〓判定・D-01・D-23・元号5値の適用。設計 §6.5〜§6.6）。

閾値の適用はここ（render 段）だけ。cell には読取値と信頼度しか入っていない。

2026-08-31（5巡目 第2段・docs/design/chouhyo-ocr/04_unclear_policy.md）:
U-10〜U-13（文字単位〓・#62）・U-04（由来印）・U-03（矛盾=conflict の強制〓）を
追加した。〓判定の統一基準は同設計書 §2 の判定表を正本とする。
"""
from __future__ import annotations

import math
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
    # 抽出列と同じ並びの由来印（U-04・2026-08-31）。'fallback' は参照先採用を表し
    # xlsx の静的背景色（render_out.FILL_ORIGIN_FALLBACK）に使う。それ以外は ''。
    # 値が〓（欄全体）になった列は常に ''（由来色より〓の条件付き書式を優先させる
    # ため・設計 §3 U-04「由来色は〓でなければ」）
    origins: tuple[str, ...] = ()

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
        # processed=False は build_failure_row（全〓行）専用。そこへ「正常」を
        # 返すと、値が1つも無い行が正常を名乗る。旧実装は両方の枝が STATUS_OK
        # で、呼び出し側のガードだけが事故を防いでいた（レビュー LOW の
        # 「同値2分岐」）。ガードを関数側にも置く
        return STATUS_OK if processed else STATUS_INTERRUPTED
    return ";".join(parts)


def unclear_reason(raw: str, conf, cfg: Config) -> str | None:
    """1つの text セルが〓になる理由を返す（〓でなければ None）。

    判定表（設計 §2）の #4（制御文字）・#10（空値・信頼度なし・型不正）・
    #11〜13（閾値未満）の**値そのものの読み取り品質**だけを見る共通の核。
    build_row と debug_images の両方がこれを使うことで、判定基準が
    独立実装で構造的にずれることを防ぐ（#60 M-1 の②）。

    choice・空行・由来（参照先採用・矛盾）による強制〓はここでは扱わない——
    それらは記入内容ではなく構造的な事実による判定で、呼び出し側が別途見る。
    """
    if conf is not None and (isinstance(conf, bool) or not isinstance(conf, (int, float))):
        conf = None
    if raw and bool(_ILLEGAL_XLSX.search(raw)):
        return "制御文字を含む読取値"
    if raw == "":
        return "読取値が空"
    if conf is None:
        return "信頼度なし"
    if conf < cfg.unclear_threshold:
        return f"信頼度が閾値{cfg.unclear_threshold}未満"
    return None


def _parse_char_confs(s: str) -> tuple[float, ...]:
    """cell.char_confs（カンマ区切り小数）→ タプル。空・不正な値は空タプル

    （呼び出し側が len(raw) と比較して不一致を検知し、安全側=欄全体〓へ倒す・
    設計 §14 不変条件2）。

    NaN・無限大の防御（レビュー差し戻し・2026-08-31）: float() は "nan"/"inf"
    を例外なく通す。NaN は `< 閾値` の比較が常に False、+inf も常に False に
    なるため、パース結果に紛れ込むと「閾値未満なのに文字単位〓の対象から
    漏れる」——H-2 と同型の穴になる。安全側（0.0＝必ず閾値未満扱い）へ倒す
    （mapping.symbols_from_response の型不正防御 conf=0.0 と同じ方針）。
    """
    if not s:
        return ()
    try:
        vals = [float(x) for x in s.split(",")]
    except ValueError:
        return ()
    return tuple(0.0 if not math.isfinite(v) else v for v in vals)


def build_row(template: Template, page: dict, cells: dict[str, tuple],
              era_scores: dict[str, dict], cfg: Config,
              extras: dict[str, tuple[str, str]] | None = None) -> Row:
    """処理済みページの1行。cells: field_id → (raw, conf, kind, is_empty_row)。

    extras: field_id → (char_confs, origin)（store.cell_extras() と同形・設計 §10.2）。
    省略時（None）は従来どおり（由来印なし・文字単位〓なし）——呼び出し側が
    extras を配線していない場合でも既存の挙動と完全に一致する。
    """
    values: list = []
    confs: list[float] = []
    origins: list[str] = []

    for cell in template.cells:
        raw, conf, _kind, is_empty = cells.get(cell.field_id, ("", None, cell.kind, False))
        out_cols = cell.output_columns()
        extra = extras.get(cell.field_id) if extras else None
        char_confs_s, origin = extra if extra else ("", "")

        if cell.kind == "choice":
            decision = era.decide(era_scores.get(cell.field_id, {}), cfg.era_threshold)
            if is_empty:
                values.append("")
            elif decision in (era.UNSELECTED, era.UNDECIDED):
                values.append(UNCLEAR)
            else:
                values.append(decision)
            origins.append("")
            continue

        # text セル
        if is_empty:
            values.extend([""] * len(out_cols))
            origins.extend([""] * len(out_cols))
            continue

        # 判定表 #8（U-03）: 矛盾は閾値に関わらず欄全体〓。raw_text 自体は
        # mapping 側で主のまま保存されている（転記主義）が、出力は必ず〓にする
        if origin == "conflict":
            values.extend([UNCLEAR] * len(out_cols))
            origins.extend([""] * len(out_cols))
            continue

        reason = unclear_reason(raw, conf, cfg)
        # 型不正の conf は「信頼度なし」＝〓へ倒す（issue #39）。以降の分岐で
        # 数値として使うため、ここで build_row 側の conf も確定させる
        if conf is not None and (isinstance(conf, bool)
                                 or not isinstance(conf, (int, float))):
            conf = None

        if reason is not None:
            # 文字単位〓（U-11・#62）が効くのは「原因が閾値未満」だけ——
            # 空値・信頼度なし・制御文字混入は無条件に欄全体〓（判定表 #4/#10）
            is_threshold_case = (raw != "" and conf is not None
                                 and not bool(_ILLEGAL_XLSX.search(raw)))
            simple_text = not cell.subfields and cell.normalize != "amount"
            char_confs = _parse_char_confs(char_confs_s)
            char_level_ok = (cfg.unclear_char_level and simple_text
                             and is_threshold_case
                             and len(char_confs) == len(raw))
            if char_level_ok:
                below = [c < cfg.unclear_threshold for c in char_confs]
                if all(below) or not any(below):
                    # 判定表 #11: 全文字が閾値未満 → 1文字の〓へ畳む
                    # （"〓〓〓" を作らない・設計 §14 不変条件1）。
                    #
                    # H-2（レビュー差し戻し・2026-08-31）: `not any(below)`
                    # は本来ここへ来ないはずの矛盾状態——unclear_reason は
                    # 丸め前の conf_min（cell.conf・REAL 列）を見て「閾値未満」
                    # と判定したのに、char_confs（pipeline._serialize_char_confs
                    # の .3f 直列化で丸められた値）で再判定すると全文字が
                    # 「閾値以上」になるケースがある（実測: conf=0.8496 は
                    # 閾値0.85未満だが、直列化で "0.850" になり 0.850<0.85 は
                    # False）。below が1つも立たないと raw がそのまま出て
                    # 〓が1文字も出ない――unclear_reason が「読み取り品質に
                    # 疑義あり」と判定した事実を静かに握りつぶすことになる。
                    # unclear_reason 側を正として欄全体〓へ倒す（安全側）
                    values.extend([UNCLEAR] * len(out_cols))
                    origins.extend([""] * len(out_cols))
                else:
                    # 判定表 #12: 一部の文字だけ〓（例: 旭〓市）
                    text = "".join(UNCLEAR if b else ch for ch, b in zip(raw, below))
                    kept = [c for c, b in zip(char_confs, below) if not b]
                    values.append(text)
                    origins.append(origin)
                    # 最低信頼度は「置換されなかった文字」の最小値のみを混ぜる
                    # （B-1 の文字単位への延長・設計 §8.2）
                    if kept:
                        confs.append(min(kept))
            else:
                # 判定表 #13: 機能OFF・subfields・amount・char_confs不正 は
                # 従来どおり欄全体〓
                values.extend([UNCLEAR] * len(out_cols))
                origins.extend([""] * len(out_cols))
            continue

        # 判定表 #14: 通常どおり（閾値をクリア）。最低信頼度は
        # 「**出力された値**のうちの最小」——分割失敗・正規化失敗で〓に
        # なったセルの信頼度を混ぜない（レビュー B-1）
        if cell.subfields:
            parts = split_composite(raw, len(cell.subfields))
            if parts:
                values.extend(parts)
                origins.extend([origin] * len(out_cols))
                confs.append(conf)
            else:
                values.extend([UNCLEAR] * len(out_cols))
                origins.extend([""] * len(out_cols))
        elif cell.normalize == "amount":
            amount = normalize_amount(raw)
            if amount is not None:
                values.append(amount)
                origins.append(origin)
                confs.append(conf)
            else:
                values.append(UNCLEAR)
                origins.append("")
        else:
            values.append(raw)
            origins.append(origin)
            confs.append(conf)

    # U-13: 要確認セル数は「〓を含む」で数える（完全一致のままだと文字単位〓が
    # 出荷ゲートをすり抜ける・設計 §8.3）。ただし QA 再判定（2026-08-31・T-16
    # ブロッカー）により unclear_char_level でゲートする——文字単位〓が
    # 有効でなければ〓は必ず単独のセル値になり「含む」と「完全一致」は
    # 数学的に同じ結果になるが、Excel 実機での COUNTIF ワイルドカード動作
    # （T-16）は未検証のため、既定 OFF の経路には未検証の仮定を載せない
    # （render_out.write_xlsx の COUNTIF 式・条件付き書式2本目と対にする）
    if cfg.unclear_char_level:
        unclear_count = sum(1 for v in values if isinstance(v, str) and UNCLEAR in v)
    else:
        unclear_count = sum(1 for v in values if v == UNCLEAR)
    min_conf = f"{min(confs):.3f}" if confs else ""
    status = compose_status(page.get("status", ""), page.get("unassigned_below_table", 0),
                            processed=True)
    return Row(page["page_id"], page["source_file"], page["page_no"],
               status, values, unclear_count, min_conf, tuple(origins))


def build_failure_row(template: Template, page: dict) -> Row:
    """未処理・失敗ページの全〓行（要件 §3.4: 入力ページ数＝出力行数を常に維持）。"""
    n = sum(len(c.output_columns()) for c in template.cells)
    status = compose_status(page.get("status", ""), 0, processed=False)
    return Row(page["page_id"], page["source_file"], page["page_no"],
               status, [UNCLEAR] * n, n, "", ("",) * n)
