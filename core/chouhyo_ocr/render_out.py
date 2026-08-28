"""出力（.xlsx / .csv 同時生成・設計 §6.6）。

- .xlsx: write_only（M0-S1 で成立確認）。金額のみ数値型・他は文字列型（'@'）・
  〓へ条件付き書式・要確認セル数は COUNTIF 数式
- .csv: 全列クォート・BOM 付き UTF-8・要確認セル数は静的な数値
- 再現性: 同一中間データ・同一設定 → バイト一致（要件 §6.2）。xlsx は
  docProps の日時を固定し、zip エントリの日時も正規化する
"""
from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill

from .columns import META_COLUMNS, excel_column_letter
from .render_rows import Row

_FIXED_DT = datetime(2000, 1, 1)
_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")


def _normalize_zip(path: Path) -> None:
    """zip エントリの日時・順序と docProps の日時を固定してバイト決定性を得る。

    openpyxl は save 時に dcterms:modified を現在時刻で上書きするため
    （wb.properties への設定は無視される）、ここで固定値へ書き戻す。
    """
    src = zipfile.ZipFile(path, "r")
    entries = {n: src.read(n) for n in src.namelist()}
    src.close()
    core = "docProps/core.xml"
    if core in entries:
        import re
        entries[core] = re.sub(
            rb"(<dcterms:modified[^>]*>)[^<]*(</dcterms:modified>)",
            rb"\g<1>2000-01-01T00:00:00Z\g<2>", entries[core])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(entries):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, entries[name])
    path.write_bytes(buf.getvalue())


def write_xlsx(path: Path, columns: list[str], rows: list[Row]) -> None:
    n_extract = len(columns) - len(META_COLUMNS)
    first = excel_column_letter(len(META_COLUMNS) + 1)          # G
    last = excel_column_letter(len(columns))                    # 218列なら HJ

    wb = Workbook(write_only=True)
    wb.properties.created = _FIXED_DT
    wb.properties.modified = _FIXED_DT
    ws = wb.create_sheet("output")
    ws.conditional_formatting.add(
        f"{first}1:{last}{len(rows) + 1}",
        CellIsRule(operator="equal", formula=['"〓"'], fill=_FILL))

    def text(v):
        c = WriteOnlyCell(ws, value=v)
        c.number_format = "@"
        if isinstance(v, str) and v:
            # data_type を明示的に文字列へ固定する（issue #34・D-28）。openpyxl の
            # _bind_value は "=" 始まりの値を無条件に数式型へ昇格させ、
            # number_format="@" はこの判定に関与しない。結果、記入値 "=SUM(A1:A9)"
            # が <f> タグとして書かれて Excel で実行され、さらに同じ行の COUNTIF
            # （要確認セル数）を計算グラフへ巻き込んで無言で壊す（実測）。
            # **値そのものは書き換えない**——型宣言を正すだけなので転記主義に反しない
            c.data_type = "s"
        return c

    ws.append([text(c) for c in columns])
    row_no = 1
    for r in rows:
        row_no += 1
        formula = WriteOnlyCell(
            ws, value=f'=COUNTIF({first}{row_no}:{last}{row_no},"〓")')
        meta = [formula, text(r.min_conf), text(r.page_id),
                text(r.source_file), text(str(r.page_no)), text(r.status)]
        body = [WriteOnlyCell(ws, value=v) if isinstance(v, int) else text(v)
                for v in r.values]
        ws.append(meta + body)
    wb.save(path)
    _normalize_zip(path)


def write_csv(path: Path, columns: list[str], rows: list[Row]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
        w.writerow(columns)
        for r in rows:
            w.writerow([str(r.unclear_count), r.min_conf, r.page_id,
                        r.source_file, str(r.page_no), r.status]
                       + [str(v) for v in r.values])


# Excel が数式として解釈しうる接頭文字（D-28）。@ とタブは実測で無害だったが、
# 検出は無害化ではないので偽陽性コストが低く、Excel のバージョン差・LibreOffice・
# 別の取り込み先まで含めて広く取る
RISKY_PREFIXES = "=+-@\t\r\n"


def scan_risky_prefixes(columns: list[str],
                        rows: list[Row]) -> list[tuple[str, str]]:
    """Excel が数式として解釈しうる接頭文字を持つセルを列挙する（D-28）。

    返すのは (page_id, 列名) のみ——**値は返さない**（設計 §8.1: 記入値を
    ログ・イベントへ出さない方針を、型で守る）。値の書き換えも〓化もしない
    （要件 §5.5 転記主義）。CSV の内容はこの検出の有無で1バイトも変わらない。
    """
    extract_cols = columns[6:]
    hits: list[tuple[str, str]] = []
    for r in rows:
        for name, v in zip(extract_cols, r.values):
            if isinstance(v, str) and v and v[0] in RISKY_PREFIXES:
                hits.append((r.page_id, name))
    return hits


def write_outputs(
    out_dir: str | Path, timestamp: str, columns: list[str], rows: list[Row]
) -> tuple[Path, Path, list[tuple[str, str]]]:
    """xlsx/csv を書き、危険接頭セルの一覧（page_id, 列名）を併せて返す。

    検出をここに置くのは呼び忘れを構造で防ぐため（issue #27 の行長検査と同じ
    置き方）。検出は出力内容に一切影響しない（D-28）。
    """
    # 行の値数＝抽出列数は出力の中核不変条件。assert（-O で消える）でなく
    # 明示例外で、xlsx/csv 両形式が必ず通るこの一箇所で検査する（issue #27）
    n_extract = len(columns) - 6
    for r in rows:
        if len(r.values) != n_extract:
            raise ValueError(
                f"行の値数({len(r.values)})が抽出列数({n_extract})と一致しない"
                f"（帳票ID: {r.page_id}）。テンプレートと中間データの整合を確認する")
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    xlsx = d / f"output_{timestamp}.xlsx"
    csvp = d / f"output_{timestamp}.csv"
    write_xlsx(xlsx, columns, rows)
    write_csv(csvp, columns, rows)
    return xlsx, csvp, scan_risky_prefixes(columns, rows)
