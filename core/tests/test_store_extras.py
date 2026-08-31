"""store.py: cell.char_confs / cell.origin の追加列とマイグレーション（U-04/#62・2026-08-31）。

- 新規 DB でもスキーマに列がある（CREATE TABLE）
- 既存 DB（列が無いバージョンで作られた想定）にも _ensure_column で追加される
- upsert_cell_extras / cell_extras の往復
- cells() の戻り値（4要素タプル）が変わらないこと（設計 §10.2 の後方互換）
- 不変条件: 同一 page_id で cells() と cell_extras() のキー集合が一致する
"""
import sqlite3

from chouhyo_ocr.store import Store


def test_cell_table_has_extra_columns_on_fresh_db(tmp_path):
    db = Store(tmp_path / "fresh.sqlite")
    try:
        cols = {r[1] for r in db.con.execute("PRAGMA table_info(cell)")}
        assert {"char_confs", "origin"} <= cols
    finally:
        db.close()


def test_ensure_column_migrates_old_db_without_extras(tmp_path):
    """char_confs/origin 列が無い旧バージョンの DB でも Store() で自動追加される。"""
    p = tmp_path / "old.sqlite"
    con = sqlite3.connect(p)
    con.executescript("""
        CREATE TABLE page(page_id TEXT PRIMARY KEY, source_file TEXT NOT NULL,
          page_no INTEGER NOT NULL, state TEXT NOT NULL, status TEXT NOT NULL DEFAULT '',
          attempt INTEGER NOT NULL DEFAULT 0, image_path TEXT,
          unassigned_below_table INTEGER NOT NULL DEFAULT 0,
          unassigned_other INTEGER NOT NULL DEFAULT 0,
          updated_at REAL NOT NULL, UNIQUE(source_file, page_no));
        CREATE TABLE cell(page_id TEXT NOT NULL, field_id TEXT NOT NULL,
          raw_text TEXT NOT NULL, conf REAL, kind TEXT NOT NULL,
          is_empty_row INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(page_id, field_id));
    """)
    con.execute(
        "INSERT INTO cell(page_id, field_id, raw_text, conf, kind, is_empty_row) "
        "VALUES('p1','f1','旧データ',0.9,'text',0)")
    con.commit()
    con.close()

    db = Store(p)  # ここで _ensure_column が走る
    try:
        cols = {r[1] for r in db.con.execute("PRAGMA table_info(cell)")}
        assert {"char_confs", "origin"} <= cols
        # 既存行は既定値で埋まる（'' = 情報なし・旧版が書いた印）
        assert db.cell_extras("p1")["f1"] == ("", "")
        # 旧メソッドの戻り値は不変
        assert db.cells("p1")["f1"] == ("旧データ", 0.9, "text", False)
    finally:
        db.close()


def test_upsert_cell_extras_round_trip(tmp_path):
    db = Store(tmp_path / "db.sqlite")
    try:
        db.upsert_cells("p1", [("f1", "旭川市", 0.9, "text", 0),
                               ("f2", "参照", 0.6, "text", 0)])
        db.upsert_cell_extras("p1", [
            ("f1", "0.97,0.31,0.96", ""),
            ("f2", "0.60,0.62", "fallback"),
        ])
        extras = db.cell_extras("p1")
        assert extras["f1"] == ("0.97,0.31,0.96", "")
        assert extras["f2"] == ("0.60,0.62", "fallback")
        # cells() 側の値は upsert_cell_extras の影響を受けない
        assert db.cells("p1")["f1"] == ("旭川市", 0.9, "text", False)
    finally:
        db.close()


def test_cells_and_cell_extras_share_key_set(tmp_path):
    """不変条件（設計 §10.2）: 同一 page_id で cells() と cell_extras() のキー集合が一致する。"""
    db = Store(tmp_path / "db.sqlite")
    try:
        db.upsert_cells("p1", [("f1", "あ", 0.9, "text", 0), ("f2", "", None, "text", 0)])
        assert set(db.cells("p1")) == set(db.cell_extras("p1"))
    finally:
        db.close()


def test_upsert_cells_does_not_clear_previously_set_extras(tmp_path):
    """upsert_cells の再実行（remap 相当）は char_confs/origin を巻き戻さない。

    upsert_cells の ON CONFLICT 節が raw_text/conf/kind/is_empty_row のみを
    更新する設計であることの確認——extras は upsert_cell_extras という別経路で
    独立に管理される（設計 §10.2 の意図どおり）。
    """
    db = Store(tmp_path / "db.sqlite")
    try:
        db.upsert_cells("p1", [("f1", "旧", 0.9, "text", 0)])
        db.upsert_cell_extras("p1", [("f1", "0.9", "fallback")])
        db.upsert_cells("p1", [("f1", "新", 0.95, "text", 0)])  # 再実行（remap 相当）
        assert db.cells("p1")["f1"] == ("新", 0.95, "text", False)
        assert db.cell_extras("p1")["f1"] == ("0.9", "fallback")
    finally:
        db.close()
