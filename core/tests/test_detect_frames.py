"""`grid.detect_frames`／`detect-frames` サブコマンドのテスト（issue #73 (b)・08 §4）。

対象 AC: AC-F16（formB・領域指定なし）・AC-F17（dpi スケール）・
AC-F18（候補ゼロの明示）・NFR-F02（性能）。formC（同寸別様式）・sample-1
（出荷テンプレの紙・無ければ skip）でも実測する。

2026-09-03 マリンのレビュー差し戻し対応（H-1〜M-6）を反映:
- H-2: 水平罫線を共有する左右2ブロックが幽霊列を持つ1表に融合しない
  ことを合成配置で固定
- M-1: `stats.components`／`excluded` の `non_rectangular` を確認
- M-2: `PITCH_TOL` の dpi スケールを確認（dpi=600 で揺らぎ3pxの4行目を拾う）
- M-3: テンプレート寸法不一致で `template_applied=false` になることを確認
- M-4: 欄候補の `residual_px` が実測値になっていることを確認
- M-5: `no_rect`／`all_filtered`／`too_many_lines` を合成画像で固定
  （AC-F18 は「線分はあるが閉じない」入力を素材にする）

2026-09-03 issue #85（(b) レビュー持ち越し）の追加:
- N-1: 表候補の `residual_px` が rows==2 でも判別力を持つ（レールの
  散らばりを反映する）／ピッチ当てはめ側の残差も従来どおり効く
- N-2: 4辺の閉じ判定で落ちた連結成分を `not_closed` として計上し、
  成分の台帳（components = rects + non_rectangular + not_closed）が閉じる

2026-09-04「表を升の集まりとして扱う」（AC-H30〜H45）の追加:
- 閉じた矩形はすべて升候補（`FrameCandidates.cells`）として返る。表に
  吸収されても落ちない。等ピッチ run は `suggestions` として別枠で返る
- セルの台帳（rects = cells + page_outline + too_small + straddles_face）
  が閉じる
- 見出し行の切り離し（`_detach_heading_rows`）は合成データで固定する
  ——実素材（請求書等）が無いため閾値の較正はしていない（07 R-5）

既存 25 件のうち 8 件は上記に合わせて読み替えた（AC の趣旨は変えていない。
例: AC-F16 の「formB で表 1・欄 3」は「升 18・提案 1（5 行 × 3 列）」と
同じ事実の別表現）。

既存 `test_grid.py`（`detect_ruled`／`make_uniform`）は本ファイルの追加で
1行も変更していない——`grid.detect_frames` は独立した新関数として追加した
（08 §4.9 不変条件2）。
"""
import json
import time

import numpy as np
import pytest
from PIL import Image, ImageDraw

from chouhyo_ocr import cli
from chouhyo_ocr.grid import MAX_RAILS, detect_frames
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.template import load_template

FORMB_PNG = app_root() / "testdata" / "formB" / "formB-1.png"
FORMB_TPL = app_root() / "testdata" / "formB" / "formB-v1.json"
FORMC_PNG = app_root() / "testdata" / "formC" / "formC-1.png"
SAMPLE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"
SHIPPED_TPL = app_root() / "templates" / "chouhyo-v1.json"

needs_sample = pytest.mark.skipif(not SAMPLE_PNG.exists(), reason="サンプル画像が無い環境")


def _draw_table(size: tuple[int, int], ys: list[int], xs: list[int]) -> "np.ndarray":
    """罫線だけの合成テーブル画像（True=インク）を作る共通ヘルパー。"""
    img = Image.new("L", size, 255)
    draw = ImageDraw.Draw(img)
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill=0, width=2)
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill=0, width=2)
    return np.asarray(img) < 128


def _binary(path) -> "np.ndarray":
    gray = np.asarray(Image.open(path).convert("L"))
    return gray < 128


def _cfg(tmp_path):
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# AC-F16: formB・領域指定なし → 升候補18 + 提案1（5行×3列）
# 旧「表候補1 + 欄候補3」と同じ事実の別表現（AC-H30）。表に吸収された
# 15 升も候補に残るので、升は 3（表の外の単発欄）ではなく 18（原子セル全件）
# ---------------------------------------------------------------------------

def test_ac_f16_formb_unit():
    binary = _binary(FORMB_PNG)
    result = detect_frames(binary, dpi=300)

    assert result.zero_reason is None
    assert len(result.suggestions) == 1
    t = result.suggestions[0]
    assert t.rows == 5
    assert abs(t.row_pitch - 80) <= 1
    widths = [c["width"] for c in t.columns]
    assert widths == pytest.approx([200, 150, 400], abs=1)
    assert t.residual_px <= 1.0

    # AC-H30: 升候補は原子セル全件（旧実装では表に吸収された 15 升が落ちて 3 だった）
    assert len(result.cells) == 18
    assert result.stats["rects"] == 18
    assert result.stats["cells"] == 18
    assert result.stats["suggestions"] == 1


def test_ac_f16_formb_cli(tmp_path, capsys):
    """CLI 実走: `detect-frames --input formB-1.png`（領域指定なし）。"""
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "detect-frames",
                   "--input", str(FORMB_PNG)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["event"] == "detect_frames" and ev["ok"] is True
    assert ev["input_size"] == [1800, 1200]
    assert ev["zero_reason"] is None

    # AC-H31: candidates は升だけ（kind は "field" 固定）。提案はトップレベルの
    # 別キー——candidates に混ぜると、未知の kind を "field" へ潰す受け取り側が
    # 提案の外接矩形を1つの巨大な欄として採用してしまう（設計 D-2）
    assert [c["kind"] for c in ev["candidates"]] == ["field"] * 18
    assert len(ev["suggestions"]) == 1
    t = ev["suggestions"][0]
    assert t["kind"] == "table"
    assert t["blocks"] == [{"x": 100, "y": 300, "rows": 5}]
    assert abs(t["row_pitch"] - 80) <= 1
    assert [c["width"] for c in t["columns"]] == [200, 150, 400]
    assert t["residual_px"] <= 1.0
    assert t["heading_excluded"] is False
    # --template 未指定なので face_id は "page" 固定・overlaps_existing は False
    for c in ev["candidates"] + ev["suggestions"]:
        assert c["face_id"] == "page"
        assert c["overlaps_existing"] is False
    # --template 未指定時は template_applied/template_skip_reason とも null（M-3）
    assert ev["template_applied"] is None
    assert ev["template_skip_reason"] is None
    # H-3: excluded が JSON に出る（キー自体が存在すること）
    assert "excluded" in ev and isinstance(ev["excluded"], list)


# ---------------------------------------------------------------------------
# AC-F17: 150dpi 相当に縮小しても同じ構造（閾値が dpi 由来の絶対長で効く）
# ---------------------------------------------------------------------------

def test_ac_f17_dpi_scaling_same_structure():
    img = Image.open(FORMB_PNG).convert("L")
    # NEAREST でアンチエイリアスを避け、縮小後も罫線をシャープに保つ
    # （検出閾値の較正はアンチエイリアス処理までは想定していない・§4.10 R-2）
    small = img.resize((900, 600), Image.NEAREST)
    binary = np.asarray(small) < 128

    result = detect_frames(binary, dpi=150)
    assert result.zero_reason is None
    assert len(result.suggestions) == 1
    assert len(result.cells) == 18   # 300dpi と同じ升の数（構造が保たれる）
    t = result.suggestions[0]
    assert t.rows == 5
    assert abs(t.row_pitch - 40) <= 1  # 80 の半分
    assert [c["width"] for c in t.columns] == [100, 75, 200]  # 200/150/400 の半分


# ---------------------------------------------------------------------------
# AC-F18: 候補ゼロが明示して返る（黙って空配列を返さない）。
# 2026-09-03 マリン指摘（M-5）: 07 の原文「罫線を全て消した sample-1」を
# そのまま素材にすると罫線ゼロ＝ no_lines にしかならない。AC-F18 の本来の
# 趣旨（「罫線が閉じた矩形を成さない画像」）を満たすのは no_rect の方
# ——no_lines と no_rect の両方を別々に固定する
# ---------------------------------------------------------------------------

def test_zero_reason_no_lines_on_blank_image():
    """罫線が1本も無い画像 → zero_reason="no_lines"。"""
    blank = np.zeros((1200, 1800), dtype=bool)
    result = detect_frames(blank, dpi=300)
    assert result.zero_reason == "no_lines"
    assert result.suggestions == ()
    assert result.cells == ()


def test_ac_f18_cli_blank_image(tmp_path, capsys):
    blank_path = tmp_path / "blank.png"
    Image.new("L", (400, 300), 255).save(blank_path)
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "detect-frames", "--input", str(blank_path)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["ok"] is True
    assert ev["candidates"] == []
    assert ev["zero_reason"] == "no_lines"
    assert ev["excluded"] == []


def test_ac_f18_lines_without_closed_rect_report_no_rect():
    """AC-F18 本来の趣旨: 線分はあるが閉じた矩形を成さない画像（平行線のみ）
    → zero_reason="no_rect"（候補ゼロを明示・空配列を黙って返さない）。
    """
    img = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(img)
    for y in (50, 100, 150):  # 水平線のみ・垂直線が無いので矩形が閉じない
        draw.line((20, y, 300, y), fill=0, width=2)
    binary = np.asarray(img) < 128
    result = detect_frames(binary, dpi=300)
    assert result.zero_reason == "no_rect"
    assert result.suggestions == ()
    assert result.cells == ()
    assert result.stats["lines_h"] == 3
    assert result.stats["lines_v"] == 0


def test_zero_reason_all_filtered_when_only_page_outline_detected():
    """検出できた唯一の閉じた矩形がページ外形（幅・高さとも90%以上）
    → zero_reason="all_filtered"（M-5）。excluded に page_outline が立つ。
    """
    img = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(img)
    draw.rectangle((5, 5, 395, 295), outline=0, width=2)
    binary = np.asarray(img) < 128
    result = detect_frames(binary, dpi=300)
    assert result.zero_reason == "all_filtered"
    assert result.suggestions == () and result.cells == ()
    assert any(e["reason"] == "page_outline" for e in result.excluded)


def test_zero_reason_too_many_lines_when_rails_exceed_max():
    """水平レールが MAX_RAILS を超える → zero_reason="too_many_lines"
    （組み合わせ爆発の打ち切りガード・M-5）。
    """
    n = MAX_RAILS + 5
    h = n * 5 + 20
    img = Image.new("L", (100, h), 255)
    draw = ImageDraw.Draw(img)
    for i in range(n):
        y = 10 + i * 5
        draw.line((10, y, 90, y), fill=0, width=1)
    binary = np.asarray(img) < 128
    result = detect_frames(binary, dpi=300)
    assert result.zero_reason == "too_many_lines"
    assert result.stats["rails_h"] > MAX_RAILS


# ---------------------------------------------------------------------------
# existing（テンプレート）指定で overlaps_existing が立つ・face_id が割り当たる
# ---------------------------------------------------------------------------

def test_existing_template_assigns_face_id_and_overlap():
    binary = _binary(FORMB_PNG)
    tpl = load_template(FORMB_TPL)
    result = detect_frames(binary, dpi=300, existing=tpl)

    assert len(result.suggestions) == 1
    assert result.suggestions[0].face_id == "front"
    assert result.suggestions[0].overlaps_existing is True  # 表定義とちょうど重なる
    for c in result.cells:
        assert c.face_id == "front"
        assert c.overlaps_existing is True  # 単発欄・表の升の定義とちょうど重なる


def test_existing_template_cli(tmp_path, capsys):
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "detect-frames",
                   "--input", str(FORMB_PNG), "--template", str(FORMB_TPL)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["ok"] is True
    for c in ev["candidates"]:
        assert c["face_id"] == "front"
        assert c["overlaps_existing"] is True
    # M-3: 寸法一致時は template_applied=true・template_skip_reason=null
    assert ev["template_applied"] is True
    assert ev["template_skip_reason"] is None


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_template_size_mismatch_skips_application(tmp_path, capsys):
    """M-3: --input の実寸とテンプレートの image_size が違う場合、除外白潰し・
    face_id 割り当て・overlaps_existing を行わず、template_applied=false・
    template_skip_reason="size_mismatch" を返す（--template 未指定と同じ扱い）。
    """
    cfg_path = _cfg(tmp_path)
    # formC-1.png（2490x3510）に formB のテンプレート（1800x1200 期待）を当てる
    rc = cli.main(["--config", str(cfg_path), "detect-frames",
                   "--input", str(FORMC_PNG), "--template", str(FORMB_TPL)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["ok"] is True
    assert ev["template_applied"] is False
    assert ev["template_skip_reason"] == "size_mismatch"
    # テンプレートが適用されないので --template 未指定と同じ face_id/overlaps
    for c in ev["candidates"]:
        assert c["face_id"] == "page"
        assert c["overlaps_existing"] is False


# ---------------------------------------------------------------------------
# M-4: 欄候補の residual_px は 0.0 固定ではなく実測値
# ---------------------------------------------------------------------------

def test_field_candidate_residual_is_measured_not_zero():
    """formB の氏名・備考欄は実測で微小な residual（0.3px）を持つ
    （08 §4.4 が「0.0 固定は禁止」と明記）。全升が 0.0 に潰れていないこと、
    かつ全て非負であることを確認する。
    """
    binary = _binary(FORMB_PNG)
    result = detect_frames(binary, dpi=300)
    assert len(result.cells) == 18
    residuals = [c.residual_px for c in result.cells]
    assert all(r >= 0.0 for r in residuals)
    assert any(r > 0.0 for r in residuals)  # 少なくとも1件は実測でずれがある


# ---------------------------------------------------------------------------
# H-2: 水平罫線を共有する左右2ブロックが幽霊列を持つ1表に融合しない
# （マリンのレビュー実測配置・2026-09-03 差し戻し）
# ---------------------------------------------------------------------------

def test_h2_shared_rail_side_by_side_blocks_split_into_two_tables():
    """左右2ブロック（x=100..500 と x=700..1100）が同じ3本の水平レール
    （y=100,150,200）を共有していても、間の隙間（x=501..701）を挟んで
    別々の表候補になる——「5列×1表」に融合せず、ギャップが幽霊列として
    columns に混入しない。
    """
    img = Image.new("L", (1300, 400), 255)
    draw = ImageDraw.Draw(img)

    def block(x0):
        xs = [x0, x0 + 200, x0 + 400]
        ys = [100, 150, 200]
        for y in ys:
            draw.line((xs[0], y, xs[-1], y), fill=0, width=2)
        for x in xs:
            draw.line((x, ys[0], x, ys[-1]), fill=0, width=2)

    block(100)
    block(700)
    binary = np.asarray(img) < 128

    result = detect_frames(binary, dpi=300)
    assert result.zero_reason is None
    assert len(result.suggestions) == 2
    # 升候補は 2 ブロック × 2 行 × 2 列 = 8（提案に吸収されても落ちない）
    assert len(result.cells) == 8
    for t in sorted(result.suggestions, key=lambda t: t.rect.x):
        assert t.rows == 2
        assert len(t.columns) == 2
        assert [c["width"] for c in t.columns] == [200, 200]
    xs = sorted(t.rect.x for t in result.suggestions)
    assert xs == [100, 700]


# ---------------------------------------------------------------------------
# M-1: 原子セルはグリッドセル単位の連結成分近似——非矩形（L字型）は
# excluded の non_rectangular として計上し、stats.components に成分総数を返す
# ---------------------------------------------------------------------------

def test_m1_non_rectangular_component_is_excluded_and_counted():
    """formB は罫線構成上、外周の巨大な非矩形連結成分（L字型・ページ外形の
    一部を含む束）が1つ発生する（実測）。excluded に non_rectangular が
    計上され、stats.components が原子セル数（rects）以上であることを
    確認する（README的な回帰確認・値そのものは実測ベース）。
    """
    binary = _binary(FORMB_PNG)
    result = detect_frames(binary, dpi=300)
    assert result.stats["components"] >= result.stats["rects"]
    non_rect = [e for e in result.excluded if e["reason"] == "non_rectangular"]
    assert len(non_rect) == 1
    assert non_rect[0]["count"] >= 1


# ---------------------------------------------------------------------------
# M-2: PITCH_TOL は他の px 閾値と同じく dpi でスケールする
# （07 FR-F16 の「±2px」は 300dpi 換算と読む・Orchestrator 決定）
# ---------------------------------------------------------------------------

def test_m2_pitch_tol_scales_with_dpi():
    """4行のテーブルで、3→4行目のピッチだけ 3px 揺らぐ配置。
    300dpi（PITCH_TOL=2px）では許容を超えて4行目が別 run に切られ3行の
    表になるが、600dpi（PITCH_TOL=4px）では許容内に収まり4行とも拾われる。
    """
    ys = [0, 100, 200, 303, 403]  # y1 の間隔（pitch）: 100, 100, 103
    xs = [0, 250, 500]
    binary = _draw_table((xs[-1] + 50, ys[-1] + 50), ys, xs)

    r300 = detect_frames(binary, dpi=300)
    assert len(r300.suggestions) == 1
    assert r300.suggestions[0].rows == 3

    r600 = detect_frames(binary, dpi=600)
    assert len(r600.suggestions) == 1
    assert r600.suggestions[0].rows == 4


# ---------------------------------------------------------------------------
# N-1（#85）: 表候補の residual_px は「等ピッチ当てはめの残差」と「構成セルの
# レール散らばり」の大きい方。2 行の run では前者が定義上つねに 0 になるため、
# 後者を入れないと rows==2 の候補が全て 0.0 に潰れる
# ---------------------------------------------------------------------------

def test_n1_two_row_table_residual_reflects_rail_scatter():
    """2 行の表の中段レールに、離れた場所の 2px ずれた線が混ざる配置。

    `_cluster_rails` は区間の重なりを問わずに pos が近い線分を1本のレールへ
    束ねるので、遠方の線もレール代表位置（平均）を引っ張る。ピッチ当てはめ
    残差は 2 点なので 0 のままだが、原子セルが測るレールの散らばりは 0 で
    なくなる——この値が表候補の residual_px に出ることを固定する。
    """
    img = Image.new("L", (1300, 400), 255)
    draw = ImageDraw.Draw(img)
    xs, ys = [100, 300, 500], [100, 200, 300]
    for y in ys:
        draw.line((xs[0], y, xs[-1], y), fill=0, width=2)
    for x in xs:
        draw.line((x, ys[0], x, ys[-1]), fill=0, width=2)

    clean = detect_frames(np.asarray(img) < 128, dpi=300)
    assert len(clean.suggestions) == 1
    assert clean.suggestions[0].rows == 2
    assert clean.suggestions[0].residual_px == 0.0   # 揃った配置では残差 0

    # 中段レール（y=200）から 2px ずれた線を、表の右外（x=700..1100）に足す
    draw.line((700, 202, 1100, 202), fill=0, width=2)
    scattered = detect_frames(np.asarray(img) < 128, dpi=300)
    assert len(scattered.suggestions) == 1
    t = scattered.suggestions[0]
    assert t.rows == 2
    assert t.residual_px > 0.0        # 変更前はここが 0.0 のままだった
    assert t.residual_px == pytest.approx(1.0, abs=0.3)


def test_n1_pitch_residual_still_reported_when_larger():
    """レールが揃っていてもピッチが揺らぐ配置では、従来どおり当てはめ残差が
    そのまま出る（max のもう一方を潰していないことの確認）。

    y1 の間隔 100／102（PITCH_TOL=2px 以内なので同じ run）→ 平均ピッチ 101・
    当てはめ残差 1.0。レールの散らばりは 0。
    """
    ys = [100, 200, 302, 402]
    xs = [100, 300, 500]
    binary = _draw_table((xs[-1] + 100, ys[-1] + 50), ys, xs)
    result = detect_frames(binary, dpi=300)
    assert len(result.suggestions) == 1
    t = result.suggestions[0]
    assert t.rows == 3
    assert t.residual_px == pytest.approx(1.0, abs=0.3)


# ---------------------------------------------------------------------------
# N-2（#85）: 4 辺の閉じ判定（被覆率 EDGE_COVER=0.90）で落ちた連結成分を
# excluded の not_closed に計上する（08 §4.2.3「黙って消さない」）
# ---------------------------------------------------------------------------

def test_n2_unclosed_rectangle_is_counted_as_not_closed():
    """上辺だけ半分（被覆率 0.5）の矩形——形は矩形だが閉じていないので
    候補にならない。以前は理由ゼロで消えていた。
    """
    img = Image.new("L", (600, 400), 255)
    draw = ImageDraw.Draw(img)
    draw.line((100, 300, 500, 300), fill=0, width=2)   # 下辺は全長
    draw.line((100, 100, 300, 100), fill=0, width=2)   # 上辺は半分だけ
    draw.line((100, 100, 100, 300), fill=0, width=2)
    draw.line((500, 100, 500, 300), fill=0, width=2)
    result = detect_frames(np.asarray(img) < 128, dpi=300)

    assert result.zero_reason == "no_rect"
    assert result.stats["components"] == 1
    assert result.stats["rects"] == 0
    assert [dict(e) for e in result.excluded] == [{"reason": "not_closed", "count": 1}]


def _component_ledger_gap(result) -> int:
    """成分の台帳の残り: components - rects - non_rectangular - not_closed。

    `excluded` には2つの台帳が混ざる（08 §4.2.3）。`page_outline`・
    `too_small`・`straddles_face` は原子セル（rects）から引かれるセルの台帳
    なので、この式には入れない——全 reason を足して components から引くと
    セル側の除外を二重に数える（sample-1 実測でそのぶん -8 になる）。
    """
    counts = {e["reason"]: e["count"] for e in result.excluded}
    return (result.stats["components"] - result.stats["rects"]
            - counts.get("non_rectangular", 0) - counts.get("not_closed", 0))


def test_n2_component_ledger_closes_on_formb():
    assert _component_ledger_gap(detect_frames(_binary(FORMB_PNG), dpi=300)) == 0


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_n2_component_ledger_closes_on_formc():
    assert _component_ledger_gap(detect_frames(_binary(FORMC_PNG), dpi=300)) == 0


@needs_sample
def test_n2_component_ledger_closes_on_sample1():
    """sample-1（align_page 経路）は 157 成分中 11 個が 4 辺の閉じ判定で
    落ちる（2026-09-03 実測）。この 11 件が not_closed として出て、成分の
    台帳が閉じることを固定する。
    """
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        _faces, composite = align_page(img, tpl)
    result = detect_frames(np.asarray(composite.convert("L")) < 128,
                           dpi=tpl.render_dpi, existing=tpl)
    counts = {e["reason"]: e["count"] for e in result.excluded}
    assert counts.get("not_closed", 0) >= 1
    assert _component_ledger_gap(result) == 0
    # N-1: rows==2 の提案が全部 0.0 に潰れていない（変更前は 8 件すべて 0.0）
    two_row = [t for t in result.suggestions if t.rows == 2]
    assert two_row and all(t.residual_px > 0.0 for t in two_row)


# ---------------------------------------------------------------------------
# formC（同寸別様式・生成素材）: 候補が出ること
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_formc_produces_candidates():
    binary = _binary(FORMC_PNG)
    result = detect_frames(binary, dpi=300)
    assert result.zero_reason is None
    assert len(result.suggestions) >= 1
    t = result.suggestions[0]
    assert t.rows >= 2
    assert len(t.columns) >= 1


# ---------------------------------------------------------------------------
# sample-1（出荷テンプレの紙・.gitignore 配下・無ければ skip）:
# 表候補が複数出ること（family／detail 起因。実測は環境依存のためブロック
# 単体の個数を厳密に固定しない・08 §4.7「先に期待値を決め打ちしない」）
#
# 2026-09-03 H-2 対応後の再実測: H-2（行の x 分割）を入れる前は表候補5件
# （front 3・back 2）だったが、分割ロジックの変更で行が正確に切られる
# ようになった結果、表候補は10件（front 8・back 2）に増えた。back 側
# （detail・pitch 104 実測）は分割前後で 2 件のまま安定。front 側
# （family・pitch 113 期待）は fields 欄との近接で原子セルが崩れやすく、
# pitch がばらけた小さい表候補が複数出る（60.5/181.0/134.6/512.4/115.3/
# 110.1/113.8/536.9 等）——「family が5行×4列×2ブロックのきれいな表として
# そのまま出る」わけではないが、pitch が113付近の候補（110.1・113.8・
# 115.3）は含まれており、「family 側で表候補が全く出ない」わけでもない。
# この不安定さは実データの構造由来（08 §4.10 R-3/R-4）であり、H-2 は
# 「幽霊列の除去」を解決するもので family 側の行分離問題とは別——本テストは
# detail 側の安定した2件のみを厳密条件にする（実測に基づく判断は維持）。
# ---------------------------------------------------------------------------

@needs_sample
def test_sample1_produces_multiple_table_candidates():
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        faces, composite = align_page(img, tpl)
    gray = np.asarray(composite.convert("L"))
    binary = gray < 128

    result = detect_frames(binary, dpi=tpl.render_dpi, existing=tpl)
    assert result.zero_reason is None
    # detail（back 面・pitch 104 実測）由来の表候補が2ブロック分含まれることを
    # 確認する（family 側は fields との近接で原子セルが崩れやすく個数を
    # 保証できないため、detail 側のみを厳密条件にする・実測に基づく判断）
    detail_like = [t for t in result.suggestions
                  if t.face_id == "back" and abs(t.row_pitch - 104) <= 2]
    assert len(detail_like) >= 2


# ---------------------------------------------------------------------------
# NFR-F02: 性能（面1枚 vs ページ1枚の議論はあるが、暫定 3.0 秒以内をここでは
# formB 単体で確認する。perf_check.py への追加は本タスクの範囲外）
# ---------------------------------------------------------------------------

def test_formb_completes_within_budget():
    binary = _binary(FORMB_PNG)
    t0 = time.perf_counter()
    result = detect_frames(binary, dpi=300)
    elapsed = time.perf_counter() - t0
    assert result.zero_reason is None
    assert elapsed < 3.0


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境")
def test_formc_completes_within_budget():
    """formC は37本のレールを持つ密なテーブル——組み合わせ爆発を避ける
    グリッド走査（Union-Find, O(nh*nv)）で 3.0 秒以内に収まることを確認する。
    素朴な全ペア方式では性能検証中に 50 秒超を実測し、設計を変更した。
    """
    binary = _binary(FORMC_PNG)
    t0 = time.perf_counter()
    result = detect_frames(binary, dpi=300)
    elapsed = time.perf_counter() - t0
    assert result.zero_reason is None
    assert elapsed < 3.0


# ===========================================================================
# 「表を升の集まりとして扱う」（2026-09-04）: AC-H30〜H38・H40〜H45
# 07 の採番では AC-F90〜F104（対応は 07 §7 の対照表）
# ===========================================================================

def _cell_ledger_gap(result) -> int:
    """セルの台帳の残り: rects - cells - page_outline - too_small - straddles_face。

    成分の台帳（`_component_ledger_gap`）とは別勘定（08 §4.2.3）。面判定を
    run より前に移したことで、原子セルから引かれる除外がすべてセル単位に
    揃い、この式が閉じるようになった（旧実装は面またぎを run 単位で 1 件と
    数えていたため成立していなかった）。
    """
    counts = {e["reason"]: e["count"] for e in result.excluded}
    return (result.stats["rects"] - len(result.cells)
            - counts.get("page_outline", 0) - counts.get("too_small", 0)
            - counts.get("straddles_face", 0))


def _heading_table(ys: list[int]) -> "np.ndarray":
    """見出し切り離し用の合成表（3 列固定・行の高さだけ ys で変える）。"""
    return _draw_table((900, 800), ys, [100, 350, 550, 750])


# ---------------------------------------------------------------------------
# AC-H30: 提案に吸収された升も候補から落ちない（FR-F56）
# ---------------------------------------------------------------------------

def test_ac_h30_cells_survive_absorption_into_a_suggestion():
    """formB の提案は 5 行 × 3 列 = 15 升を覆うが、その 15 升は候補に残る。

    旧実装は `used_cells` で run に吸収された升を欄候補から除いていたため、
    「表としてはまとめたくない」利用者に升を 1 つずつ採る道が無かった。
    """
    result = detect_frames(_binary(FORMB_PNG), dpi=300)
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert len(s.cell_indexes) == 15                       # 5 行 × 3 列

    # 提案が覆う升がすべて候補に実在する
    covered = [result.cells[i] for i in s.cell_indexes]
    assert len(covered) == 15
    # かつ、それらは提案の外接矩形に収まっている（別物を指していない）
    for c in covered:
        assert s.rect.x <= c.rect.x and c.rect.x + c.rect.w <= s.rect.x + s.rect.w
        assert s.rect.y <= c.rect.y and c.rect.y + c.rect.h <= s.rect.y + s.rect.h
    # 残り 3 升は表の外の単発欄（旧 `fields` に相当）
    assert len(result.cells) - len(covered) == 3


# ---------------------------------------------------------------------------
# AC-H31: CLI 応答の契約（`suggestions[]` はトップレベル・`candidates[]` は
# `kind:"field"` のみ・`stats` に cells/suggestions）
# ---------------------------------------------------------------------------

def test_ac_h31_cli_returns_suggestions_at_top_level(tmp_path, capsys):
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "detect-frames",
                   "--input", str(FORMB_PNG)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())

    # `suggestions` はトップレベルの新設キー。`candidates` に混ぜない
    assert "suggestions" in ev
    assert {c["kind"] for c in ev["candidates"]} == {"field"}
    assert [s["kind"] for s in ev["suggestions"]] == ["table"]
    assert ev["stats"]["cells"] == len(ev["candidates"]) == 18
    assert ev["stats"]["suggestions"] == len(ev["suggestions"]) == 1
    # 既存キーは 1 つも消えていない（旧 GUI が読む形のまま）
    for key in ("event", "ok", "input_size", "candidates", "stats", "excluded",
                "zero_reason", "template_applied", "template_skip_reason", "elapsed_ms"):
        assert key in ev
    # 升候補の形は従来の `kind:"field"` と同一（新しいキーを足していない）
    assert set(ev["candidates"][0]) == {"kind", "face_id", "rect", "residual_px",
                                        "overlaps_existing"}
    # 提案の形は従来の `kind:"table"` ＋ 2 キー
    assert set(ev["suggestions"][0]) == {
        "kind", "face_id", "rect", "blocks", "row_pitch", "row_height", "columns",
        "residual_px", "overlaps_existing", "cell_indexes", "heading_excluded"}
    # 添字が同一応答内の candidates を指す（受け取り側はこれを自前 id へ解決する）
    idxs = ev["suggestions"][0]["cell_indexes"]
    assert all(0 <= i < len(ev["candidates"]) for i in idxs)


def test_ac_h31_cli_blank_image_has_empty_suggestions(tmp_path, capsys):
    """候補ゼロでも `suggestions` キー自体は出る（受け取り側の分岐を増やさない）。"""
    blank_path = tmp_path / "blank.png"
    Image.new("L", (400, 300), 255).save(blank_path)
    cfg_path = _cfg(tmp_path)
    rc = cli.main(["--config", str(cfg_path), "detect-frames", "--input", str(blank_path)])
    assert rc == 0
    ev = json.loads(capsys.readouterr().out.strip())
    assert ev["suggestions"] == []
    assert ev["stats"]["cells"] == 0 and ev["stats"]["suggestions"] == 0


# ---------------------------------------------------------------------------
# AC-H32: 提案を採用した結果の tables[] が従来の表候補と完全一致
# （期待値は変更前のコード（HEAD d9f8b01 の grid.py）を実走して採取した実測値）
# ---------------------------------------------------------------------------

def test_ac_h32_formb_suggestion_matches_legacy_table_candidate():
    s = detect_frames(_binary(FORMB_PNG), dpi=300).suggestions[0]
    assert (s.rect.x, s.rect.y, s.rect.w, s.rect.h) == (100, 300, 750, 400)
    assert (s.origin_x, s.origin_y, s.rows) == (100, 300, 5)
    assert (s.row_pitch, s.row_height) == (80.0, 80)
    assert s.columns == [{"x_offset": 0, "width": 200},
                         {"x_offset": 200, "width": 150},
                         {"x_offset": 350, "width": 400}]
    assert s.residual_px == 0.3


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_ac_h32_formc_suggestion_matches_legacy_table_candidate():
    s = detect_frames(_binary(FORMC_PNG), dpi=300).suggestions[0]
    assert (s.rect.x, s.rect.y, s.rect.w, s.rect.h) == (150, 400, 1650, 2100)
    assert (s.origin_x, s.origin_y, s.rows) == (150, 400, 30)
    assert (s.row_pitch, s.row_height) == (70.0, 70)
    assert [c["width"] for c in s.columns] == [150, 400, 200, 300, 300, 300]
    assert s.residual_px == 0.0


# ---------------------------------------------------------------------------
# AC-H33: セルの台帳が閉じる（#85 の成分の台帳と両立する）
# ---------------------------------------------------------------------------

def test_ac_h33_cell_ledger_closes_on_formb():
    result = detect_frames(_binary(FORMB_PNG), dpi=300)
    assert _cell_ledger_gap(result) == 0
    assert _component_ledger_gap(result) == 0   # 成分の台帳も従来どおり


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_ac_h33_cell_ledger_closes_on_formc():
    result = detect_frames(_binary(FORMC_PNG), dpi=300)
    assert _cell_ledger_gap(result) == 0
    assert _component_ledger_gap(result) == 0


@needs_sample
def test_ac_h33_cell_ledger_closes_on_sample1():
    """sample-1 はテンプレート適用時に too_small 5・straddles_face 3 が立つ
    （2026-09-04 実測）。両方の台帳が同時に閉じることを固定する。
    """
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        _faces, composite = align_page(img, tpl)
    result = detect_frames(np.asarray(composite.convert("L")) < 128,
                           dpi=tpl.render_dpi, existing=tpl)
    counts = {e["reason"]: e["count"] for e in result.excluded}
    assert counts.get("too_small", 0) >= 1
    assert counts.get("straddles_face", 0) >= 1
    assert _cell_ledger_gap(result) == 0
    assert _component_ledger_gap(result) == 0


# ---------------------------------------------------------------------------
# AC-H34: cell_indexes の不変条件（昇順・重複なし・範囲内・件数 = 行×列）
# ---------------------------------------------------------------------------

def _assert_cell_indexes_sane(result):
    for s in result.suggestions:
        idxs = list(s.cell_indexes)
        assert idxs == sorted(set(idxs))                      # 昇順・重複なし
        assert all(0 <= i < len(result.cells) for i in idxs)  # 範囲内
        assert len(idxs) == s.rows * len(s.columns)           # 行×列で埋まっている


def test_ac_h34_cell_indexes_invariants_on_formb():
    _assert_cell_indexes_sane(detect_frames(_binary(FORMB_PNG), dpi=300))


@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_ac_h34_cell_indexes_invariants_on_formc():
    _assert_cell_indexes_sane(detect_frames(_binary(FORMC_PNG), dpi=300))


@needs_sample
def test_ac_h34_cell_indexes_invariants_on_sample1():
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        _faces, composite = align_page(img, tpl)
    result = detect_frames(np.asarray(composite.convert("L")) < 128,
                           dpi=tpl.render_dpi, existing=tpl)
    _assert_cell_indexes_sane(result)


# ---------------------------------------------------------------------------
# AC-H36: formC（30 行 × 6 列の密な表）は升 180・提案 1
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_ac_h36_formc_cells_and_single_suggestion():
    result = detect_frames(_binary(FORMC_PNG), dpi=300)
    assert len(result.cells) == 180          # 30 行 × 6 列（旧実装では欄候補 0 件）
    assert result.stats["cells"] == 180
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert (s.rows, len(s.columns)) == (30, 6)
    assert s.heading_excluded is False


# ---------------------------------------------------------------------------
# AC-H37: 面をまたぐ提案は作らないが、構成する升は候補に残る
# （提案を落としても情報が失われないので `excluded` へは足さない）
# ---------------------------------------------------------------------------

def _two_face_template(tmp_path):
    """front(0-440) / back(440-800) の 2 面テンプレート（900x800・位置合わせ用の錨付き）。"""
    def face(face_id, y, h):
        return {"face_id": face_id,
                "source": {"page_offset": 0, "rect": {"x": 0, "y": y, "w": 900, "h": h}},
                "exclusions": [],
                # 位置合わせの錨として 40px 以上の欄を1つ置く（表の外側）
                "fields": [{"field_id": f"{face_id}_anchor", "kind": "text",
                            "rect": {"x": 780, "y": 20, "w": 100, "h": 60}}],
                "tables": []}
    path = tmp_path / "split-v1.json"
    path.write_text(json.dumps({
        "schema_version": 1, "template_id": "split-v1", "render_dpi": 300,
        "image": {"width": 900, "height": 800}, "record": {"pages": 1},
        "faces": [face("front", 0, 440), face("back", 440, 360)]}), encoding="utf-8")
    return load_template(path)


def test_ac_h37_face_straddling_suggestion_is_dropped_but_cells_remain(tmp_path):
    # 面境界 y=440 をまたいで 5 行（各行はどちらか一方の面に収まる）
    binary = _draw_table((900, 800), [200, 280, 360, 440, 520, 600], [100, 350, 550, 750])

    without = detect_frames(binary, dpi=300)
    assert len(without.suggestions) == 1          # テンプレート無しなら 5 行の提案が出る
    assert without.suggestions[0].rows == 5

    result = detect_frames(binary, dpi=300, existing=_two_face_template(tmp_path))
    assert result.suggestions == ()               # 外接矩形が面をまたぐので提案にしない
    assert len(result.cells) == 15                # 升は 1 つも落ちない
    assert {c.face_id for c in result.cells} == {"front", "back"}
    # 提案を落としたぶんを `excluded` へ足さない（失われた情報が無いため）
    assert not any(e["reason"] == "straddles_face" for e in result.excluded)
    assert _cell_ledger_gap(result) == 0
    assert result.zero_reason is None


# ---------------------------------------------------------------------------
# AC-H38: NFR-F02（3.0 秒以内）。升候補が全件になり `_overlaps_existing` の
# 呼び出しが 18 → 135 回（× テンプレートのセル）に増える経路を実測する
# ---------------------------------------------------------------------------

@needs_sample
def test_ac_h38_sample1_completes_within_budget():
    from chouhyo_ocr.align import align_page

    tpl = load_template(SHIPPED_TPL)
    with Image.open(SAMPLE_PNG) as img:
        _faces, composite = align_page(img, tpl)
    binary = np.asarray(composite.convert("L")) < 128
    t0 = time.perf_counter()
    result = detect_frames(binary, dpi=tpl.render_dpi, existing=tpl)
    elapsed = time.perf_counter() - t0
    assert result.zero_reason is None
    assert len(result.cells) == 135
    assert elapsed < 3.0


# ---------------------------------------------------------------------------
# AC-H40〜H45: 見出し行の切り離し（`_detach_heading_rows`）
#
# 実素材（見出しの高さが本文と違う請求書等）が手元に無いため、素材は
# すべて合成（07 R-5・PM Q-5 の決定）。閾値 HEADING_HEIGHT_RATIO=0.20 は
# 実素材で較正した値ではなく、ここで固定するのは「境界の閉じ方」と
# 「発火しない条件」であって閾値そのものの妥当性ではない
# ---------------------------------------------------------------------------

def test_ac_h40_heading_row_is_detached_before_run_build():
    """見出し 120px ＋ 本文 80px × 4 行 → 4 行の提案 1 件（heading_excluded=true）。

    切り離しをしないと、見出し→1行目の間隔 120 が pitch0 として確定し、
    1行目→2行目の 80 で run が切れて「見出し＋1行目（2行）」「2〜4行目
    （3行）」の 2 件に割れる。run を組んだ**後**に先頭を外す後処理だと
    1 行目が孤児になるため、run 構築の前に外す（設計 D-4）。
    """
    result = detect_frames(_heading_table([200, 320, 400, 480, 560, 640]), dpi=300)
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert s.rows == 4
    assert s.row_pitch == 80.0
    assert s.row_height == 80
    assert s.origin_y == 320                 # 見出し行（y=200）を含まない
    assert s.heading_excluded is True
    assert len(s.cell_indexes) == 12         # 4 行 × 3 列


def test_ac_h41_detached_heading_row_remains_a_cell_candidate():
    """外した見出し行の 3 升は候補に残り、提案の cell_indexes には入らない。"""
    result = detect_frames(_heading_table([200, 320, 400, 480, 560, 640]), dpi=300)
    assert len(result.cells) == 15           # 5 行 × 3 列（見出し行を含む）
    heading = [i for i, c in enumerate(result.cells)
               if c.rect.y == 200 and c.rect.h == 120]
    assert len(heading) == 3
    assert not set(heading) & set(result.suggestions[0].cell_indexes)
    assert _cell_ledger_gap(result) == 0


def test_ac_h42_short_heading_row_is_also_detached():
    """見出しが本文より**低い**場合（50px 対 80px）も切り離す（対称性）。

    高い側だけを見出しとみなす理由が無い——ラベル行が本文より薄い帳票は
    実在するため、比率の絶対値で判定する。
    """
    result = detect_frames(_heading_table([200, 250, 330, 410, 490, 570]), dpi=300)
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert (s.rows, s.row_pitch, s.origin_y) == (4, 80.0, 250)
    assert s.heading_excluded is True
    assert any(c.rect.y == 200 and c.rect.h == 50 for c in result.cells)


def test_ac_h43_height_diff_at_threshold_is_not_detached():
    """本文 80px に対し見出し 96px（差 16px ＝ ちょうど 20%）では外さない。

    判定は `> 0.20 * h_body` の狭義。境界で外さない側に倒すのは、閾値が
    実素材で較正されていない（07 R-5）以上、既存の run の切れ方を勝手に
    変えない方が安全なため。結果として旧実装と同じ 2 件に割れる
    ——これは「割れたまま」を仕様として固定するもので、直したいなら
    閾値の再較正が要る。
    """
    result = detect_frames(_heading_table([200, 296, 376, 456, 536, 616]), dpi=300)
    assert len(result.suggestions) == 2
    assert not any(s.heading_excluded for s in result.suggestions)
    assert sorted(s.rows for s in result.suggestions) == [2, 3]


def test_ac_h44_merged_row_is_not_absorbed_into_a_suggestion():
    """行ごとに列構成が違う（1 行目だけ結合セル）場合、その行は提案に入らず
    升候補のまま残る。垂直レール署名が一致しないので run に繋がらない。
    """
    img = Image.new("L", (900, 800), 255)
    draw = ImageDraw.Draw(img)
    ys = [200, 280, 360, 440, 520]
    for y in ys:
        draw.line((100, y, 750, y), fill=0, width=2)
    for x in (100, 750):                       # 外枠は全高
        draw.line((x, ys[0], x, ys[-1]), fill=0, width=2)
    for x in (350, 550):                       # 内側の仕切りは 2 行目以降だけ
        draw.line((x, 280, x, 520), fill=0, width=2)

    result = detect_frames(np.asarray(img) < 128, dpi=300)
    assert len(result.cells) == 10             # 結合行 1 ＋ 3 行 × 3 列
    assert len(result.suggestions) == 1
    s = result.suggestions[0]
    assert (s.rows, len(s.columns)) == (3, 3)
    assert s.origin_y == 280                   # 結合行（y=200）から始まらない
    merged = [i for i, c in enumerate(result.cells) if c.rect.w == 650]
    assert len(merged) == 1
    assert merged[0] not in s.cell_indexes
    assert s.heading_excluded is False         # 見出し切り離しは発火していない


def test_ac_h45_no_detach_when_body_is_too_short_or_uneven():
    """切り離しが**発火しない**条件を 2 つ固定する（誤発火の防止）。

    1. 署名チェーンが 2 行（見出し＋本文 1 行）——外すと run が作れず、
       提案が 1 件も出なくなるので外す意味がない
    2. 本文の間隔が等ピッチでない——見出しかどうか以前に、run として
       まとまらない
    """
    short = detect_frames(_heading_table([200, 320, 400]), dpi=300)
    assert len(short.suggestions) == 1
    assert short.suggestions[0].rows == 2
    assert short.suggestions[0].heading_excluded is False

    uneven = detect_frames(_heading_table([200, 320, 400, 500, 580]), dpi=300)
    assert not any(s.heading_excluded for s in uneven.suggestions)
    assert sorted(s.rows for s in uneven.suggestions) == [2, 2]
