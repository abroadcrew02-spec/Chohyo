"""出力列の導出（設計 §4.3）。

列リストをコードへ持たない。管理6列だけが固定で、抽出対象列はテンプレートの
定義順から導出する。v1 は起動時に導出結果が 220 列であることを検証し、
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

V1_EXPECTED_TOTAL = 220  # 管理6＋本人14＋家族60＋明細140（要件 §5.6 v3.10）


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
    return cols


def excel_column_letter(n: int) -> str:
    """1起点の列番号 → Excel 列文字（220 → 'HL'）。"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
