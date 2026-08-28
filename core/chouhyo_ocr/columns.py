"""出力列の導出（設計 §4.3）。

列リストをコードへ持たない。管理6列だけが固定で、抽出対象列はテンプレートの
定義順から導出する。v1 は起動時に導出結果が 218 列であることを検証し、
不一致ならテンプレートを拒否する（テンプレート編集ミスで列構成が黙って
変わる事故の防止）。
"""
from __future__ import annotations

from .template import Template, TemplateError

META_COLUMNS: tuple[str, ...] = (
    "要確認セル数",
    "最低信頼度",
    "帳票ID",
    "入力ファイル名",
    "ページ番号",
    "ステータス",
)

V1_EXPECTED_TOTAL = 218  # 管理6＋本人12＋家族60＋明細140（要件 §5.6 v3.11・郵便番号は住所へ統合）
V1_EXPECTED_AMOUNT = 28  # normalize:"amount" のセル数（明細28行×金額1列）


def derive_columns(template: Template) -> list[str]:
    """管理6列＋抽出対象列。テンプレートの配列順がそのまま列順になる。"""
    cols = list(META_COLUMNS)
    for cell in template.cells:
        cols.extend(cell.output_columns())
    return cols


def extract_columns(template: Template) -> list[str]:
    """抽出対象列のみ（要確認セル数・最低信頼度の母集団）。"""
    return derive_columns(template)[len(META_COLUMNS):]


def validate_v1(template: Template) -> list[str]:
    """v1 の列数検証。通れば列リストを返し、不一致なら TemplateError。"""
    cols = derive_columns(template)
    if len(cols) != V1_EXPECTED_TOTAL:
        raise TemplateError(
            f"導出列数が {len(cols)} 列（v1 の期待は {V1_EXPECTED_TOTAL} 列）。"
            "テンプレートの行数・列定義を確認する"
        )
    if len(set(cols)) != len(cols):
        raise TemplateError("導出列名に重複がある")
    # 金額正規化は normalize 属性で発火する（列名非依存・issue #11）。エディタで
    # 表を作り直すと属性が落ちても列数は変わらないため、件数で明示的に検証する
    n_amount = sum(1 for c in template.cells if c.normalize == "amount")
    if n_amount != V1_EXPECTED_AMOUNT:
        # 文言はエディタ画面の語彙（正規化／金額／列）で書く。JSON の生の記法を
        # 出しても管理者は画面のコントロールに結び付けられない（レビュー D-7）
        direction = (
            "金額以外の列で「金額」を選んでいないか確認してください"
            if n_amount > V1_EXPECTED_AMOUNT
            else "明細表の金額列で「正規化」を「金額」に設定してください")
        raise TemplateError(
            f"正規化「金額」が設定されたセルが {n_amount} 個です"
            f"（想定は {V1_EXPECTED_AMOUNT} 個＝明細{V1_EXPECTED_AMOUNT}行×金額1列）。"
            f"{direction}"
        )
    return cols


def excel_column_letter(n: int) -> str:
    """1起点の列番号 → Excel 列文字（218 → 'HJ'）。"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
