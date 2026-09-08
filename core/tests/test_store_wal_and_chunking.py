"""issue #150 の LOW群のうち store.py 関連2点。

(2) `PRAGMA journal_mode=WAL` の戻り値を検証していなかった——ネットワーク
共有・読み取り専用 FS 等では要求どおりに切り替わらず黙って delete モードの
ままになりうる（SQLite は PRAGMA 自体を失敗させず、切り替え前のモード名を
返すだけ）。busy_timeout の前提（複数接続の書き込みが待ちで吸収される）が
崩れるため、切り替わらなかった場合は app.log へ痕跡を残す。

(3) `IN (...)` のプレースホルダを行数ぶん生成しており、SQLite の既定上限
999 を超えるテンプレート（約1,000升超）で実行時エラーになっていた——
「様式不一致」に化けて原因が分からなくなる。900件ずつのチャンクへ分割する。
"""
from chouhyo_ocr import logging_safe
from chouhyo_ocr.store import Store


# ---------- (2): WAL モードの戻り値検証 ----------

def test_wal_mode_confirmed_no_warning(tmp_path):
    """通常のファイル DB では WAL が有効になり、警告は出ない。"""
    logging_safe.init(str(tmp_path / "logs"))
    db = Store(tmp_path / "db.sqlite")
    try:
        mode = db.con.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        db.close()

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "wal_mode_not_active" not in app_log


def test_wal_mode_not_active_logs_warning(tmp_path):
    """in-memory DB は仕様上 WAL に切り替わらず "memory" のまま
    （SQLite の既知の挙動）——これを使って戻り値の不一致を実際に再現する。
    """
    logging_safe.init(str(tmp_path / "logs"))
    db = Store(":memory:")
    try:
        pass
    finally:
        db.close()

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "wal_mode_not_active" in app_log
    assert "state=memory" in app_log


# ---------- (3): IN (...) のチャンク分割 ----------
#
# この開発環境の SQLite（3.50.4・変数上限32766）では 1,000 件程度の単一
# NOT IN クエリでは実際にはクラッシュを再現しない（既定999という上限値は
# 古い/別ビルドの SQLite 前提）。ここではクラッシュの再現ではなく、
# チャンク分割そのものの正しさ（keep の他チャンク分を巻き込んで消さない・
# 削除対象を漏れなく消す）を、実際に複数チャンクへまたがる規模で固定する。

def _rows(field_ids, text="x") -> list[tuple]:
    return [(fid, text, 0.9, "text", 0) for fid in field_ids]


def test_upsert_cells_chunked_delete_keeps_exactly_the_new_set(tmp_path):
    """900件超の keep から 900件超を削る2回目の upsert_cells で、
    削除対象（旧 - 新）がチャンク境界をまたいでも過不足なく処理される。
    """
    db = Store(tmp_path / "db.sqlite")
    try:
        pid = "p1"
        all_ids = [f"f{i:04d}" for i in range(3000)]
        db.upsert_cells(pid, _rows(all_ids))  # 1回目: 3000件を新規作成
        assert set(db.cells(pid)) == set(all_ids)

        keep_ids = [f"f{i:04d}" for i in range(1000)]  # 2回目: 1000件だけ残す
        # 削除対象は 3000-1000=2000件——_CELL_DELETE_CHUNK(900) の chunk が
        # 3本（900+900+200）に分かれることを実際に実行された SQL 文で確認する
        # （sqlite3.Connection.execute は読み取り専用属性でモンキーパッチ
        # できないため、set_trace_callback で実行文そのものを観測する）
        delete_calls = []
        db.con.set_trace_callback(
            lambda sql: delete_calls.append(sql)
            if sql.strip().startswith("DELETE FROM cell") and " IN (" in sql
            else None)
        try:
            db.upsert_cells(pid, _rows(keep_ids, text="y"))
        finally:
            db.con.set_trace_callback(None)

        assert len(delete_calls) == 3  # ceil(2000 / 900) == 3

        remaining = db.cells(pid)
        assert set(remaining) == set(keep_ids)
        # 更新された値（2回目の text="y"）で残っていること（削除漏れの逆＝
        # 誤って別データへ化けていないことも確認）
        assert remaining["f0000"][0] == "y"
    finally:
        db.close()


def test_upsert_cells_chunk_path_matches_small_path_semantics(tmp_path):
    """900件以下（従来の1クエリ経路）と901件以上（チャンク経路）で、
    「keep 以外は消え、keep はすべて残る」という結果が一致することを固定する。
    """
    db = Store(tmp_path / "db.sqlite")
    try:
        # 従来経路（<=900）
        db.upsert_cells("small", _rows([f"s{i}" for i in range(50)]))
        db.upsert_cells("small", _rows([f"s{i}" for i in range(10)], text="y"))
        assert set(db.cells("small")) == {f"s{i}" for i in range(10)}

        # チャンク経路（>900）
        db.upsert_cells("big", _rows([f"b{i}" for i in range(950)]))
        db.upsert_cells("big", _rows([f"b{i}" for i in range(10)], text="y"))
        assert set(db.cells("big")) == {f"b{i}" for i in range(10)}
    finally:
        db.close()
