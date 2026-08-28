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

# state: pending → expanded → aligned → sending → received → done
#        枝分かれ: failed（処理不能）／skipped_duplicate（同一内容の再投入）
# ※ 旧コメントにあった "mapped" は実在しない値だった（レビュー LOW）。
#   コードが書く値は pipeline.py の set_state 呼び出しがすべて。
#   出力に寄与するのは done のみ（geometry_hashes 等の母集団もこれに揃える）
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
  template_hash TEXT NOT NULL DEFAULT '',
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
  algo_version TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS source_file(
  hash TEXT PRIMARY KEY,
  name TEXT NOT NULL
);
"""


class Store:
    def __init__(self, db_path: str | Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(db_path)
        self.con.execute("PRAGMA journal_mode=WAL")
        # WAL では NORMAL で十分な耐久性（アプリクラッシュ・プロセス強制終了では
        # 何も失われず、DB も壊れない）。FULL は commit 毎 fsync で、ページ毎に
        # 十数回 commit する本ツールでは実測に響く（issue #16）。失われうるのは
        # 電源断・OS クラッシュ時の未チェックポイント分のみ（複数ページ分に及び
        # うる）。巻き戻ったページは次回 run が未処理として再処理するため、実害は
        # その分の API 再送（課金重複）にとどまる——再開設計（§6.7）が吸収する
        self.con.execute("PRAGMA synchronous=NORMAL")
        self.con.executescript(_SCHEMA)
        # 既存 DB への列追加（CREATE TABLE IF NOT EXISTS は既存テーブルに効かない）。
        # 追加列の既定 '' は「旧版が作った・出所を証明できない」印として扱う（#25）
        self._ensure_column("page", "template_hash", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("alignment", "algo_version", "TEXT NOT NULL DEFAULT ''")
        self.con.commit()

    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in self.con.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- page ---
    def upsert_page(self, page_id: str, source_file: str, page_no: int,
                    state: str, image_path: str | None = None) -> None:
        """ページ行の登録・更新。

        page_id を再利用する場合（同 stem・別拡張子の入替など）に
        source_file / page_no も更新する（レビュー B-3）。据え置くと出力の
        「入力ファイル名」列と実際の値の由来が食い違う。UNIQUE(source_file,
        page_no) 違反はここでは握りつぶさず、呼び出し側の失敗として扱う。
        """
        self.con.execute(
            """INSERT INTO page(page_id, source_file, page_no, state, image_path, updated_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(page_id) DO UPDATE SET
                 state=excluded.state,
                 source_file=excluded.source_file,
                 page_no=excluded.page_no,
                 image_path=COALESCE(excluded.image_path, page.image_path),
                 updated_at=excluded.updated_at""",
            (page_id, source_file, page_no, state, image_path, time.time()))
        self.con.commit()

    def set_state(self, page_id: str, state: str) -> None:
        self.con.execute("UPDATE page SET state=?, updated_at=? WHERE page_id=?",
                         (state, time.time(), page_id))
        self.con.commit()

    def page_id_of(self, source_file: str, page_no: int) -> str | None:
        """既存行の page_id（レビュー H-A）。

        同じ入力ファイル・同じページ番号の行が既にあるなら、その page_id を
        使い続ける。新しい ID を採ると UNIQUE(source_file, page_no) と衝突し、
        run が IntegrityError で恒久的に落ちる（同 stem・別拡張子の共存履歴＋
        先勝ち側の削除で到達・実測）。
        """
        row = self.con.execute(
            "SELECT page_id FROM page WHERE source_file=? AND page_no=?",
            (source_file, page_no)).fetchone()
        return row[0] if row else None

    def all_page_ids(self) -> set[str]:
        return {r[0] for r in self.con.execute("SELECT page_id FROM page")}

    def set_image_path(self, page_id: str, image_path: str) -> None:
        """state を触らずに展開画像のパスだけ更新する（issue #38）。"""
        self.con.execute(
            "UPDATE page SET image_path=?, updated_at=? WHERE page_id=?",
            (image_path, time.time(), page_id))
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

    def _rows(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        """Row 形式で取得する。例外時も row_factory を必ず戻す（レビュー M-21）。

        戻し忘れると以降の全メソッドの戻り型が変わり、原因の分かりにくい
        壊れ方をする。
        """
        self.con.row_factory = sqlite3.Row
        try:
            return self.con.execute(sql, params).fetchall()
        finally:
            self.con.row_factory = None

    def pages(self) -> list[sqlite3.Row]:
        return self._rows("SELECT * FROM page ORDER BY source_file, page_no")

    def page_count_of(self, source_file: str) -> int:
        """その入力ファイルのページ数。

        呼び出し側は全 page 行を取って Python 側で数えていた（レビュー LOW:
        重複ファイル1件ごとに総ページを走査＝O(N×M)）。件数だけなら SQL で足りる。
        """
        return self.con.execute(
            "SELECT COUNT(*) FROM page WHERE source_file=?",
            (source_file,)).fetchone()[0]

    def page(self, page_id: str) -> sqlite3.Row | None:
        rows = self._rows("SELECT * FROM page WHERE page_id=?", (page_id,))
        return rows[0] if rows else None

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
        """rows: (field_id, raw_text, conf, kind, is_empty_row)。同一ページ分を総入れ替え。

        今回書かなかった field_id は消す（issue #28: 旧テンプレートの残骸セルが
        生き残ると、field_id 再利用＋rect 移動→render の順で旧位置の値が出る。
        replace_tokens と同じ「余剰を明示 DELETE」の形に揃える）
        """
        self.con.executemany(
            """INSERT INTO cell(page_id, field_id, raw_text, conf, kind, is_empty_row)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(page_id, field_id) DO UPDATE SET
                 raw_text=excluded.raw_text, conf=excluded.conf,
                 kind=excluded.kind, is_empty_row=excluded.is_empty_row""",
            [(page_id, *r) for r in rows])
        keep = [r[0] for r in rows]
        ph = ",".join("?" * len(keep))
        self.con.execute(
            f"DELETE FROM cell WHERE page_id=? AND field_id NOT IN ({ph})",
            (page_id, *keep))
        self.con.commit()

    def cells(self, page_id: str) -> dict[str, tuple]:
        return {fid: (raw, conf, kind, bool(emp)) for fid, raw, conf, kind, emp in
                self.con.execute(
                    "SELECT field_id, raw_text, conf, kind, is_empty_row FROM cell WHERE page_id=?",
                    (page_id,))}

    # --- alignment / era ---
    def upsert_alignment(self, page_id: str, face_id: str, transform: dict,
                         ok: bool, geometry_hash: str, algo_version: str) -> None:
        self.con.execute(
            """INSERT INTO alignment(page_id, face_id, transform, ok, geometry_hash,
                                     algo_version)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(page_id, face_id) DO UPDATE SET
                 transform=excluded.transform, ok=excluded.ok,
                 geometry_hash=excluded.geometry_hash,
                 algo_version=excluded.algo_version""",
            (page_id, face_id, json.dumps(transform), int(ok), geometry_hash,
             algo_version))
        self.con.commit()

    # 再利用検査の母集団は **出力に寄与する done ページのみ**（レビュー C-1）。
    # 失敗ページの古い alignment 行まで数えると、1ページの位置合わせ失敗が
    # バッチ全体を恒久的に封鎖する——送信済み（課金済み）の正常ページも
    # purge 以外で取り出せなくなり、issue #39 で潰した故障が別経路で復活する。
    # 特に ALGO_VERSION の初回アップグレード（'' → "2"）は全利用者が通る
    def geometry_hashes(self) -> set[str]:
        return {h for (h,) in self.con.execute(
            """SELECT DISTINCT a.geometry_hash FROM alignment a
               JOIN page p ON p.page_id = a.page_id WHERE p.state='done'""")}

    def algo_versions(self) -> set[str]:
        """位置合わせ方式の版（#25/#30: コード側の版違いも再利用拒否の対象）。"""
        return {v for (v,) in self.con.execute(
            """SELECT DISTINCT a.algo_version FROM alignment a
               JOIN page p ON p.page_id = a.page_id WHERE p.state='done'""")}

    def template_hashes(self) -> set[str]:
        """cell を割り付けたテンプレート全体のハッシュ（'' は旧版データ）。"""
        return {h for (h,) in self.con.execute(
            "SELECT DISTINCT template_hash FROM page WHERE state='done'")}

    def set_template_hash(self, page_id: str, template_hash: str) -> None:
        self.con.execute(
            "UPDATE page SET template_hash=?, updated_at=? WHERE page_id=?",
            (template_hash, time.time(), page_id))
        self.con.commit()

    def stale_done_pages(self, geometry_hash: str, template_hash: str,
                         algo_version: str) -> list[str]:
        """処理済みのうち、現テンプレート・現方式で作られていないページ（#25）。"""
        rows = self.con.execute(
            """SELECT DISTINCT p.page_id FROM page p
               LEFT JOIN alignment a ON a.page_id = p.page_id
               WHERE p.state='done'
                 AND (p.template_hash != ? OR a.geometry_hash IS NULL
                      OR a.geometry_hash != ? OR a.algo_version != ?)""",
            (template_hash, geometry_hash, algo_version))
        return [r[0] for r in rows]

    def upsert_eras(self, page_id: str, scores_by_field: dict[str, dict]) -> None:
        """ページ内の choice セル全件を1トランザクションで総入れ替え（issue #16/#28）。

        今回書かなかった field_id の旧スコアは消す——残すとテンプレートに
        存在しない選択肢名が render で出うる（issue #28 実証）。
        """
        # 総入れ替えなので先に当該ページ分を消してから入れる
        self.con.execute("DELETE FROM era_score WHERE page_id=?", (page_id,))
        self.con.executemany(
            "INSERT INTO era_score(page_id, field_id, scores) VALUES(?,?,?)",
            [(page_id, fid, json.dumps(s)) for fid, s in scores_by_field.items()])
        self.con.commit()

    def era_scores(self, page_id: str) -> dict[str, dict]:
        return {fid: json.loads(s) for fid, s in self.con.execute(
            "SELECT field_id, scores FROM era_score WHERE page_id=?", (page_id,))}

    def known_source(self, file_hash: str) -> str | None:
        """同一内容の取り込み済みファイル名（要件 §5.1 Could の二重投入検知）。"""
        row = self.con.execute(
            "SELECT name FROM source_file WHERE hash=?", (file_hash,)).fetchone()
        return row[0] if row else None

    def hash_of_source(self, name: str) -> str | None:
        """記録済みの内容ハッシュ（レビュー H-B）。

        source_file は hash→name の一方向だったため「同じ中身・別の名前」
        （送信を減らす最適化）は検出できるのに、**「同じ名前・別の中身」
        （誤ったデータを出す事故）は検出できなかった**。危険なのは後者で、
        差し替えても再送されず旧値が「正常」として出続けていた（実測）。
        """
        row = self.con.execute(
            "SELECT hash FROM source_file WHERE name=? ORDER BY hash LIMIT 1",
            (name,)).fetchone()
        return row[0] if row else None

    def forget_source(self, name: str) -> None:
        """内容が変わったファイルの旧ハッシュを捨てる（H-B）。"""
        self.con.execute("DELETE FROM source_file WHERE name=?", (name,))
        self.con.commit()

    def drop_pages_of(self, name: str) -> int:
        """入力ファイルに紐づくページと派生データを消す（H-B の差し替え時）。"""
        ids = [r[0] for r in self.con.execute(
            "SELECT page_id FROM page WHERE source_file=?", (name,))]
        for pid in ids:
            for table in ("token", "cell", "alignment", "era_score"):
                self.con.execute(f"DELETE FROM {table} WHERE page_id=?", (pid,))
        self.con.execute("DELETE FROM page WHERE source_file=?", (name,))
        self.con.commit()
        return len(ids)

    def record_source(self, file_hash: str, name: str) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO source_file(hash, name) VALUES(?,?)",
            (file_hash, name))
        self.con.commit()

    def record_run(self, run_id: str, config_json: str) -> None:
        self.con.execute(
            "INSERT OR REPLACE INTO run(run_id, started_at, config) VALUES(?,?,?)",
            (run_id, time.time(), config_json))
        self.con.commit()
