"""issue #144: store.alignments() / store.snap_geometry() が保存済み
alignment.transform の壊れた JSON を無言で落としていた分の痕跡化。

どちらのメソッドも「1面ぶんの transform が壊れていたら、その面を落として
呼び出し側に再整列させる」という既存方針は変えない（run 全体を止めない）。
変えるのは、落としたことが app.log に残るかどうかだけ——以前は完全に無言
だったため、「毎回なぜか特定ページだけ再整列が走る」原因を追う手段が
無かった。

DB に壊れた transform を仕込む手段は、公開 API（upsert_alignment は dict を
受けて json.dumps する）では作れないため、行を1本 upsert してから
transform 列だけ直接 UPDATE して壊す。
"""
from chouhyo_ocr import logging_safe
from chouhyo_ocr.store import Store


def _corrupt_transform(store: Store, page_id: str, face_id: str) -> None:
    store.con.execute(
        "UPDATE alignment SET transform=? WHERE page_id=? AND face_id=?",
        ("{not valid json", page_id, face_id))
    store.con.commit()


def test_alignments_drops_broken_row_and_logs_warning(tmp_path):
    logging_safe.init(str(tmp_path / "logs"))
    db = Store(tmp_path / "db.sqlite")
    try:
        db.upsert_alignment("p1", "front", {"dx": 1, "dy": 2}, True,
                            "geo-hash", "algo-1", template_hash="tpl-hash")
        db.upsert_alignment("p1", "back", {"dx": 3, "dy": 4}, True,
                            "geo-hash", "algo-1", template_hash="tpl-hash")
        _corrupt_transform(db, "p1", "front")

        out = db.alignments("p1")

        # 壊れた面（front）は落ち、正常な面（back）だけ残る
        assert "front" not in out
        assert "back" in out
    finally:
        db.close()

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "align_transform_broken" in app_log
    assert "page_id=p1" in app_log


def test_snap_geometry_drops_broken_row_and_logs_warning(tmp_path):
    logging_safe.init(str(tmp_path / "logs"))
    db = Store(tmp_path / "db.sqlite")
    try:
        db.upsert_alignment("p1", "front", {"dx": 1, "dy": 2, "snap": {"dy": 5}},
                            True, "geo-hash", "algo-1", template_hash="tpl-hash",
                            snap_enabled=1)
        db.upsert_alignment("p1", "back", {"dx": 3, "dy": 4, "snap": {"dy": 6}},
                            True, "geo-hash", "algo-1", template_hash="tpl-hash",
                            snap_enabled=1)
        _corrupt_transform(db, "p1", "front")

        out = db.snap_geometry("p1")

        assert "front" not in out
        assert out["back"] == ({"dy": 6}, 1)
    finally:
        db.close()

    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "align_transform_broken" in app_log
    assert "page_id=p1" in app_log
