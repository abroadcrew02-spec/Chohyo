"""出力列の導出（設計 §4.3）。

列リストをコードへ持たない。管理6列だけが固定で、抽出対象列はテンプレートの
定義順から導出する。

固定列数（218）による拒否は 2026-08-31 に廃止した（ユーザー指示:
「決め打ちはやめよう」）。テンプレートに欄を足せば列は増える。編集ミスで
列構成が黙って変わる事故への防御は、拒否ではなく**見える化**で行う——
verify と編集画面の保存結果が列数・金額列数を必ず表示する。拒否として残す
検証は「列名の重複」のみ（重複すると xlsx/csv の対応が壊れる真の不変条件）。
既存データとの整合は列数ではなく template_hash・geometry_hash のガード
（issue #25）が守る。
"""
from __future__ import annotations

from .template import Template, TemplateError, output_cells

META_COLUMNS: tuple[str, ...] = (
    "要確認セル数",
    "最低信頼度",
    "帳票ID",
    "入力ファイル名",
    "ページ番号",
    "ステータス",
)



def derive_columns(template: Template) -> list[str]:
    """管理6列＋抽出対象列。テンプレートの配列順がそのまま列順になる。

    出力対象外（`output: false`）の欄は列に出さない（issue #66・FR-1.1）。
    対象外判定は output_cells() に集約済み——ここで個別に判定しない。
    """
    cols = list(META_COLUMNS)
    for cell in output_cells(template):
        cols.extend(cell.output_columns())
    return cols


def extract_columns(template: Template) -> list[str]:
    """抽出対象列のみ（要確認セル数・最低信頼度の母集団）。"""
    return derive_columns(template)[len(META_COLUMNS):]


def validate_v1(template: Template) -> list[str]:
    """列導出と不変条件の検証。通れば列リストを返す。

    拒否するのは**列名の重複**と**抽出対象列0**の2つ（列数・金額列数それ自体は
    拒否せず、呼び出し側（verify・編集画面）が表示して人が確認する・
    2026-08-31・決め打ち廃止）。
    """
    cols = derive_columns(template)
    if len(set(cols)) != len(cols):
        dup = sorted({c for c in cols if cols.count(c) > 1})
        raise TemplateError(
            f"導出列名に重複がある: {dup[:5]}。欄の名前・表の列名を見直す")
    # 抽出対象列が0（全欄が output: false）だと render_out が逆転レンジ
    # （例: G2:F2）を書き、壊れた xlsx を生成する。列名重複と同格の
    # 不変条件として拒否する（issue #66 FR-1.3・D-34 ④）
    if len(cols) == len(META_COLUMNS):
        raise TemplateError(
            "出力対象の抽出列が1つもない（全欄が output: false）。"
            "少なくとも1欄は output: true（省略）のままにする")
    return cols


def amount_cell_count(template: Template) -> int:
    """normalize:"amount" のセル数。

    金額正規化は normalize 属性で発火する（列名非依存・issue #11）。エディタで
    表を作り直すと属性が黙って落ちることがあるため、verify と編集画面が
    この数を表示して人が確認する（固定数での拒否は廃止・2026-08-31）。
    """
    return sum(1 for c in template.cells if c.normalize == "amount")

def excel_column_letter(n: int) -> str:
    """1起点の列番号 → Excel 列文字（218 → 'HJ'）。"""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
