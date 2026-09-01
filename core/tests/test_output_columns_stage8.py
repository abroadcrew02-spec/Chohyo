"""出力列制御 MVP（issue #66）第2弾 段8: 列名一覧ファイル（columns.txt）の併記。

要件: 05_output_columns_requirements.md Q-30③相当（Could）。render（出力生成）
時に、その実行で使われた順序付き列名一覧を出力フォルダへ併記する（取り込み先
システムとの列構成突き合わせ用）。

内容は derive_columns の結果そのまま（唯一の正・FR-0.1 と同じ思想）。
xlsx/csv と同じ #36/#51 のアトミック差し替え経路に乗せる（render_out.py の
write_outputs に統合済み・pipeline.py/cli.py 側の変更は不要——戻り値の
3-tuple 契約を変えていないため）。
"""
import os

import pytest

from chouhyo_ocr.columns import META_COLUMNS, derive_columns
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_out import write_outputs
from chouhyo_ocr.render_rows import Row
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"


def _row(columns, first="x"):
    n_extract = len(columns) - len(META_COLUMNS)
    values = [first] + ["v"] * (n_extract - 1)
    return Row(page_id="p0001", source_file="p1.pdf", page_no=1, status="正常",
              values=values, unclear_count=0, min_conf="0.9",
              origins=("",) * n_extract)


def test_columns_txt_written_alongside_xlsx_csv_with_matching_name(tmp_path):
    t = load_template(TPL)
    columns = derive_columns(t)
    row = _row(columns)
    xlsx, csvp, _risky = write_outputs(tmp_path, "t8", columns, [row])
    colp = tmp_path / "output_t8_columns.txt"
    assert colp.exists()
    assert xlsx.parent == colp.parent == csvp.parent
    assert xlsx.name == "output_t8.xlsx" and csvp.name == "output_t8.csv"


def test_columns_txt_content_matches_derive_columns_order_and_count(tmp_path):
    """内容は derive_columns の結果そのまま（管理6列含む・唯一の正）。"""
    t = load_template(TPL)
    columns = derive_columns(t)
    row = _row(columns)
    write_outputs(tmp_path, "t8b", columns, [row])
    colp = tmp_path / "output_t8b_columns.txt"
    with open(colp, encoding="utf-8-sig", newline="") as f:
        text = f.read()
    lines = text.split("\r\n")
    assert lines[-1] == ""  # 末尾の改行由来（split の最後は空文字になる）
    assert lines[:-1] == columns  # 順序・件数とも derive_columns と完全一致
    assert lines[:6] == list(META_COLUMNS)
    assert len(lines) - 1 == 220  # 出荷テンプレの現在値（無改変）


def test_columns_txt_is_bom_utf8_and_crlf(tmp_path):
    """BOM 付き UTF-8・CRLF（write_csv と同じ流儀）であることをバイト列で確認する。

    根拠: 列名は日本語を含むため、BOM を落とすと csv と同じ文字化けリスクが
    生じる（Windows のメモ帳・一部の取り込み先）。改行を \\r\\n に固定するのは
    OS 既定に委ねないため（§6.2 の決定性が実行環境に依存しないように）。
    """
    columns = ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名",
              "ページ番号", "ステータス", "a", "b"]
    row = _row(columns)
    write_outputs(tmp_path, "t8c", columns, [row])
    raw = (tmp_path / "output_t8c_columns.txt").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM
    assert b"\r\n" in raw and b"\n\n" not in raw.replace(b"\r\n", b"")
    assert raw.decode("utf-8-sig") == "\r\n".join(columns) + "\r\n"


def test_columns_txt_is_byte_deterministic_on_repeat_write(tmp_path):
    """同一テンプレ・同一列構成での再出力はバイト一致する（§6.2 の流儀）。"""
    t = load_template(TPL)
    columns = derive_columns(t)
    row = _row(columns)
    write_outputs(tmp_path, "d1", columns, [row])
    write_outputs(tmp_path, "d2", columns, [row])
    b1 = (tmp_path / "output_d1_columns.txt").read_bytes()
    b2 = (tmp_path / "output_d2_columns.txt").read_bytes()
    assert b1 == b2


def test_columns_txt_participates_in_atomic_replace_rollback(tmp_path, monkeypatch):
    """columns.txt の差し替え失敗も、xlsx/csv と同じロールバックの対象になる
    （#36/#51 の経路に乗っていることの確認・test_review4_io.py と同じ流儀:
    tmp→正規名の os.replace だけを選択的に失敗させる）。
    """
    columns = ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名",
              "ページ番号", "ステータス", "a", "b"]
    old_row = _row(columns, first="OLD")
    new_row = _row(columns, first="NEW")

    xlsx, csvp, _r = write_outputs(tmp_path, "t8d", columns, [old_row])
    colp = tmp_path / "output_t8d_columns.txt"
    before_xlsx, before_csv, before_col = (
        xlsx.read_bytes(), csvp.read_bytes(), colp.read_bytes())

    real = os.replace

    def fake(src, dst, *a, **kw):
        if str(src).endswith(".txt.tmp"):
            raise PermissionError(f"別プロセスが使用中（テスト擬似）: {dst}")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(os, "replace", fake)
    with pytest.raises(OSError):
        write_outputs(tmp_path, "t8d", columns, [new_row])

    # 3ファイルとも旧のまま（columns.txt だけ新しくなって xlsx/csv と食い違う
    # 半端な状態を作らない——xlsx/csv は差し替え成功後に colp の失敗で
    # 巻き戻される経路を通る）
    assert xlsx.read_bytes() == before_xlsx
    assert csvp.read_bytes() == before_csv
    assert colp.read_bytes() == before_col
    assert not list(tmp_path.glob("*.tmp"))
    assert not list(tmp_path.glob("*.bak"))
