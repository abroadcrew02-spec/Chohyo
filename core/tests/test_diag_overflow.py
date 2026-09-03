"""溢れの頻度を数える診断（issue #63）。

#63 は「主枠に部分的に記入され、残りが右隣へ溢れる」型。実測では
郵便番号1='012345'（主枠に6字）＋住所1='6'（溢れた1字が隣の欄へ）で、
status は正常・fallback/carve_hole/unassigned は全て0——現行の検知網の
どれにも掛からない。閾値設計の前に頻度を測るのがこのコマンドの役目なので、
テストも「数え方が仕様どおりか」だけを固定する（検知の良し悪しは測らない）。

合成 token を使うので API 送信も実画像も要らない。
"""
import json

import pytest

from chouhyo_ocr.diag_overflow import Report, scan, scan_page, target_fields
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.store import Store
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"

pytestmark = pytest.mark.skipif(not TPL.exists(), reason="出荷テンプレートが無い環境")


@pytest.fixture()
def template():
    return load_template(TPL)


def _tok(seq, face, x, y):
    """store.tokens() と同じ並び（seq, face, text, conf, x, y）。"""
    return (seq, face, "0", 0.98, float(x), float(y))


def _postal(template):
    return next(f for f in target_fields(template)
                if f.field_id == "person_郵便番号1")


def test_expected_digits_come_from_the_field_name_with_the_rule_attached(template):
    """出荷テンプレートの郵便番号欄が対象になり、根拠（rule）が付く。"""
    fields = {f.field_id: f for f in target_fields(template)}
    assert set(fields) == {"person_郵便番号1", "person_郵便番号2"}
    for f in fields.values():
        # 末尾の 1/2 は何人目かで、上3桁／下4桁の別ではない（同じ x に縦並び）
        assert f.expected_digits == 7
        assert f.rule.startswith("field_name:")
    assert fields["person_郵便番号1"].rect.y != fields["person_郵便番号2"].rect.y


def test_partial_main_plus_right_neighbour_is_one_candidate(template):
    """主枠6字＋右隣に1字 → 候補1件（#63 の再現形）。"""
    f = _postal(template)
    rows = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + f.rect.h // 2)
            for i in range(6)]
    rows.append(_tok(6, f.face_id, f.rect.x + f.rect.w + 5,
                     f.rect.y + f.rect.h // 2))
    found, empty = scan_page("p1", rows, [f], {}, band_scale=1.0)
    assert empty == 0
    assert [(c.field_id, c.main_symbols, c.expected_digits, c.right_symbols)
            for c in found] == [("person_郵便番号1", 6, 7, 1)]


def test_full_main_is_not_a_candidate_even_with_symbols_on_the_right(template):
    """主枠に期待桁数ぶん入っていれば、右隣に文字があっても候補にしない。

    郵便番号の右隣は住所欄で、正常なページでは必ず文字が並ぶ——ここを
    候補に数えると全ページが候補になり、頻度を測る意味が消える。
    """
    f = _postal(template)
    rows = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + f.rect.h // 2)
            for i in range(7)]
    rows.append(_tok(7, f.face_id, f.rect.x + f.rect.w + 5,
                     f.rect.y + f.rect.h // 2))
    found, empty = scan_page("p1", rows, [f], {}, band_scale=1.0)
    assert found == [] and empty == 0


def test_partial_main_without_right_neighbour_is_not_a_candidate(template):
    """右隣に何も無ければ、ただの短い記入（溢れではない）。"""
    f = _postal(template)
    rows = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + f.rect.h // 2)
            for i in range(3)]
    found, empty = scan_page("p1", rows, [f], {}, band_scale=1.0)
    assert found == [] and empty == 0


def test_empty_main_is_counted_separately_not_as_a_candidate(template):
    """主枠が空なら候補にせず、件数だけ別に残す（#54(a) の既知の型）。"""
    f = _postal(template)
    rows = [_tok(0, f.face_id, f.rect.x + f.rect.w + 5, f.rect.y + 10)]
    found, empty = scan_page("p1", rows, [f], {}, band_scale=1.0)
    assert found == [] and empty == 1


def test_band_width_follows_the_field_height_and_the_scale(template):
    """帯の幅は欄の高さ×倍率。外に出た文字は拾わない。"""
    f = _postal(template)
    main = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + 10)
            for i in range(2)]
    # 欄の高さ(78)より遠い位置に1字だけ置く
    far = _tok(9, f.face_id, f.rect.x + f.rect.w + f.rect.h + 20, f.rect.y + 10)
    assert scan_page("p1", main + [far], [f], {}, band_scale=1.0)[0] == []
    found, _ = scan_page("p1", main + [far], [f], {}, band_scale=2.0)
    assert len(found) == 1 and found[0].right_symbols == 1


def test_symbols_outside_the_field_row_are_ignored(template):
    """帯は欄と同じ高さだけ見る（上下の行の文字を巻き込まない）。"""
    f = _postal(template)
    main = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + 10)
            for i in range(2)]
    below = _tok(9, f.face_id, f.rect.x + f.rect.w + 5, f.rect.y + f.rect.h + 30)
    assert scan_page("p1", main + [below], [f], {}, band_scale=1.0)[0] == []


def test_right_outside_fields_separates_neighbour_pollution_from_open_space(
        template):
    """右隣の文字が「どこかの欄の受け皿」に入っているかを内訳で持つ。

    出荷テンプレートでは郵便番号の右隣が住所欄（と自分の参照先）なので、
    溢れた文字は欄外ではなく**隣の欄に入る**——#63 の実測（住所1='6'）が
    まさにこれで、欄外だけを数えていると0件に見えてしまう。
    """
    from chouhyo_ocr.diag_overflow import _all_rects_by_face
    f = _postal(template)
    rows = [_tok(i, f.face_id, f.rect.x + 10 + i * 20, f.rect.y + f.rect.h // 2)
            for i in range(6)]
    rows.append(_tok(6, f.face_id, f.rect.x + f.rect.w + 5,
                     f.rect.y + f.rect.h // 2))
    found, _ = scan_page("p1", rows, [f], _all_rects_by_face(template),
                         band_scale=1.0)
    assert len(found) == 1
    assert found[0].right_symbols == 1
    assert found[0].right_outside_fields == 0     # 住所欄／参照先の中


def test_scan_walks_pages_that_have_tokens(tmp_path, template):
    """中間データ全体の走査（token を持つページだけが母数）。"""
    db = tmp_path / "intermediate.sqlite"
    f = _postal(template)
    with Store(db) as store:
        store.upsert_page("p1", "a.pdf", 1, "done")
        store.upsert_page("p2", "a.pdf", 2, "done")   # token 無し（母数に入らない）
        rows = [(i, f.face_id, "0", 0.98,
                 float(f.rect.x + 10 + i * 20), float(f.rect.y + 30))
                for i in range(6)]
        rows.append((6, f.face_id, "0", 0.98,
                     float(f.rect.x + f.rect.w + 5), float(f.rect.y + 30)))
        store.replace_tokens("p1", rows)
    with Store(db) as store:
        report = scan(template, store)
    assert isinstance(report, Report)
    assert report.pages_scanned == 1 and report.fields_checked == 2
    assert [c.field_id for c in report.candidates] == ["person_郵便番号1"]
    assert report.empty_main_skipped == 1          # p1 の郵便番号2 は空


def test_cli_emits_one_json_line_per_candidate_and_a_summary(tmp_path, template,
                                                             capsys):
    """CLI 出力は JSON Lines（候補1件1行＋集計1行）。記入値・座標は出さない。"""
    from chouhyo_ocr import cli
    wd = tmp_path / "wd"
    f = _postal(template)
    with Store(wd / "intermediate.sqlite") as store:
        store.upsert_page("p1", "a.pdf", 1, "done")
        rows = [(i, f.face_id, "0", 0.98,
                 float(f.rect.x + 10 + i * 20), float(f.rect.y + 30))
                for i in range(6)]
        rows.append((6, f.face_id, "0", 0.98,
                     float(f.rect.x + f.rect.w + 5), float(f.rect.y + 30)))
        store.replace_tokens("p1", rows)
        # 幾何ゲート（check_reusable）は state='done' の alignment を見る。
        # 現テンプレートで作った印を置いて、再利用可と判定させる
        from chouhyo_ocr.align import ALGO_VERSION, geometry_hash
        raw = json.loads(TPL.read_text(encoding="utf-8"))
        store.upsert_alignment("p1", f.face_id, {"angle": 0.0, "dx": 0, "dy": 0},
                               True, geometry_hash(raw), ALGO_VERSION, "")
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({
        "workdir": str(wd), "output_dir": str(tmp_path / "out"),
        "log_dir": str(tmp_path / "logs")}), encoding="utf-8")

    rc = cli.main(["--config", str(cfg_file), "diag-overflow",
                   "--template", str(TPL)])
    assert rc == 0
    events = [json.loads(l) for l in capsys.readouterr().out.splitlines()
              if l.strip()]
    candidates = [e for e in events if e["event"] == "overflow_candidate"]
    assert [(e["page_id"], e["field_id"], e["main_symbols"],
             e["expected_digits"], e["right_symbols"]) for e in candidates] \
        == [("p1", "person_郵便番号1", 6, 7, 1)]
    summary = next(e for e in events if e["event"] == "diag_overflow")
    assert summary["ok"] is True
    assert summary["pages"] == 1 and summary["candidates"] == 1
    assert summary["band_scale"] == 1.0
    # 何を根拠に期待桁数を決めたかが出力に載る
    assert {f["field_id"]: f["rule"] for f in summary["fields"]} == {
        "person_郵便番号1": "field_name:郵便番号(3+4=7桁)",
        "person_郵便番号2": "field_name:郵便番号(3+4=7桁)"}
    # 記入値・座標は出さない（件数と識別子のみ・§8.1）
    for e in candidates:
        assert not {"x", "y", "text", "value", "raw_text"} & set(e)


def test_band_scale_is_range_checked():
    from chouhyo_ocr import cli
    import argparse
    assert cli._band_scale_arg("1.5") == 1.5
    for bad in ("0", "-1", "11"):
        with pytest.raises(argparse.ArgumentTypeError):
            cli._band_scale_arg(bad)
