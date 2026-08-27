"""中間データの SQLite 永続化（設計 §4.4）。

- 書き込みは UPSERT（DELETE→INSERT の中間状態を作らない）
- journal_mode=WAL（run 中の status 参照で database is locked にしない・§12-C4）
- page.state は処理の進行、page.status は出力8種。別物として持つ
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# state: pending → expanded → aligned → sending → received → mapped → done / failed
_SCHEMA = """
CREATE TABLE IF NOT EXISTS page(
  page_id TEXT PRIMARY KEY,
  source_file TEXT NOT NULL,
  page_no INTEGER NOT NULL,
  state TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '',
  attempt INTEGER NOT NULL DEFAULT 0,
  image_path TEXT,
  unassigned_below_table INTEGER NOT NULL DEFAULT 0,
  unassigned_other INTEGER NOT NULL DEFAULT 0,
  updated_at REAL NOT NULL,
  UNIQUE(source_file, page_no)
);
CREATE TABLE IF NOT EXISTS token(
  page_id TEXT NOT NULL,
  seq INTEGER NOT NULL,
  face TEXT NOT NULL,
  text TEXT NOT NULL,
  conf REAL NOT NULL,
  x REAL NOT NULL,
  y REAL NOT NULL,
  PRIMARY KEY(page_id, seq)
);
CREATE TABLE IF NOT EXISTS cell(
  page_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  raw_text TEXT NOT NULL,
  conf REAL,
  kind TEXT NOT NULL,
  is_empty_row INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(page_id, field_id)
);
CREATE TABLE IF NOT EXISTS alignment(
  page_id TEXT NOT NULL,
  face_id TEXT NOT NULL,
  transform TEXT NOT NULL,
  ok INTEGER NOT NULL,
  geometry_hash TEXT NOT NULL,
  PRIMARY KEY(page_id, face_id)
);
CREATE TABLE IF NOT EXISTS era_score(
  page_id TEXT NOT NULL,
  field_id TEXT NOT NULL,
  scores TEXT NOT NULL,
  PRIMARY KEY(page_id, field_id)
);
CREATE TABLE IF NOT EXISTS run(
  run_id TEXT PRIMARY KEY,
  started_at REAL NOT NULL,
  config TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path)
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA synchronous=FULL")
        self.con.executescript(_SCHEMA)
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # --- page ---
    def upsert_page(self, page_id: str, source_file: str, page_no: int,
                    state: str, image_path: str | None = None) -> None:
        self.con.execute(
            """INSERT INTO page(page_id, source_file, page_no, state, image_path, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(page_id) DO UPDATE SET
                 state=excluded.state,
                 image_path=COALESCE(excluded.image_path, page.image_path),
                 updated_at=excluded.updated_at""",
            (page_id, source_file, page_no, state, image_path, time.time()))
        self.con.commit()

    def set_state(self, page_id: str, state: str) -> None:
        self.con.execute("UPDATE page SET state=?, updated_at=? WHERE page_id=?",
                         (state, time.time(), page_id))
        self.con.commit()

    def set_status(self, page_id: str, status: str) -> None:
        self.con.execute("UPDATE page SET status=?, updated_at=? WHERE page_id=?",
                         (status, time.time(), page_id))
        self.con.commit()

    def bump_attempt(self, page_id: str) -> None:
        self.con.execute("UPDATE page SET attempt=attempt+1, updated_at=? WHERE page_id=?",
                         (time.time(), page_id))
        self.con.commit()

    def set_unassigned(self, page_id: str, below: int, other: int) -> None:
        self.con.execute(
            "UPDATE page SET unassigned_below_table=?, unassigned_other=?, updated_at=? WHERE page_id=?",
            (below, other, time.time(), page_id))
        self.con.commit()

    def pages(self) -> list[sqlite3.Row]:
        self.con.row_factory = sqlite3.Row
        rows = self.con.execute(
            "SELECT * FROM page ORDER BY source_file, page_no").fetchall()
        self.con.row_factory = None
        return rows

    def page(self, page_id: str) -> sqlite3.Row | None:
        self.con.row_factory = sqlite3.Row
        row = self.con.execute("SELECT * FROM page WHERE page_id=?", (page_id,)).fetchone()
        self.con.row_factory = None
        return row

    # --- token（symbol 単位・面ローカル中心点）---
    def replace_tokens(self, page_id: str, rows: list[tuple]) -> None:
        """rows: (seq, face, text, conf, x, y)。同一ページ分を UPSERT で総入れ替え。"""
        self.con.executemany(
            """INSERT INTO token(page_id, seq, face, text, conf, x, y)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(page_id, seq) DO UPDATE SET
                 face=excluded.face, text=excluded.text, conf=excluded.conf,
                 x=excluded.x, y=excluded.y""",
            [(page_id, *r) for r in rows])
        # 旧実行の余剰 seq を残さない
        self.con.execute("DELETE FROM token WHERE page_id=? AND seq>=?", (page_id, len(rows)))
        self.con.commit()

    def tokens(self, page_id: str) -> list[tuple]:
        return self.con.execute(
            "SELECT seq, face, text, conf, x, y FROM token WHERE page_id=? ORDER BY seq",
            (page_id,)).fetchall()

    # --- cell ---
    def upsert_cells(self, page_id: str, rows: list[tuple]) -> None:
        """rows: (field_id, raw_text, conf, kind, is_empty_row)"""
        self.con.executemany(
            """INSERT INTO cell(page_id, field_id, raw_text, conf, kind, is_empty_row)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(page_id, field_id) DO UPDATE SET
                 raw_text=excluded.raw_text, conf=excluded.conf,
                 kind=excluded.kind, is_empty_row=excluded.is_empty_row""",
            [(page_id, *r) for r in rows])
        self.con.commit()

    def cells(self, page_id: str) -> dict[str, tuple]:
        return {fid: (raw, conf, kind, bool(emp)) for fid, raw, conf, kind, emp in
                self.con.execute(
                    "SELECT field_id, raw_text, conf, kind, is_empty_row FROM cell WHERE page_id=?",
                    (page_id,))}

    # --- alignment / era ---
    def upsert_alignment(self, page_id: str, face_id: str, transform: dict,
                         ok: bool, geometry_hash: str) -> None:
        self.con.execute(
            """INSERT INTO alignment(page_id, face_id, transform, ok, geometry_hash)
               VALUES(?,?,?,?,?)
               ON CONFLICT(page_id, face_id) DO UPDATE SET
                 transform=excluded.transform, ok=excluded.ok,
                 geometry_hash=excluded.geometry_hash""",
            (page_id, face_id, json.dumps(transform), int(ok), geometry_hash))
        self.con.commit()

    def geometry_hashes(self) -> set[str]:
        return {h for (h,) in self.con.execute("SELECT DISTINCT geometry_hash FROM alignment")}

    def upsert_era(self, page_id: str, field_id: str, scores: dict) -> None:
        self.con.execute(
            """INSERT INTO era_score(page_id, field_id, scores) VALUES(?,?,?)
               ON CONFLICT(page_id, field_id) DO UPDATE SET scores=excluded.scores""",
            (page_id, field_id, json.dumps(scores)))
        self.con.commit()

    def era_scores(self, page_id: str) -> dict[str, dict]:
        return {fid: json.loads(s) for fid, s in self.con.execute(
            "SELECT field_id, scores FROM era_score WHERE page_id=?", (page_id,))}

    def record_run(self, run_id: str, config_json: str) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO run(run_id, started_at, config) VALUES(?,?,?)",
            (run_id, time.time(), config_json))
        self.con.commit()
