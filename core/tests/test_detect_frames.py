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
SAMPLE_PNG = app_root() / "workdir" / "pages" / "sample-1.png"
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
# AC-F16: formB・領域指定なし → 表候補1 + 欄候補3
# ---------------------------------------------------------------------------

def test_ac_f16_formb_unit():
    binary = _binary(FORMB_PNG)
    result = detect_frames(binary, dpi=300)

    assert result.zero_reason is None
    assert len(result.tables) == 1
    t = result.tables[0]
    assert abs(t.row_pitch - 80) <= 1
    widths = [c["width"] for c in t.columns]
    assert widths == pytest.approx([200, 150, 400], abs=1)
    assert t.residual_px <= 1.0

    assert len(result.fields) == 3


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

    tables = [c for c in ev["candidates"] if c["kind"] == "table"]
    fields = [c for c in ev["candidates"] if c["kind"] == "field"]
    assert len(tables) == 1
    assert len(fields) == 3
    t = tables[0]
    assert abs(t["row_pitch"] - 80) <= 1
    assert [c["width"] for c in t["columns"]] == [200, 150, 400]
    assert t["residual_px"] <= 1.0
    # --template 未指定なので face_id は "page" 固定・overlaps_existing は False
    for c in ev["candidates"]:
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
    assert len(result.tables) == 1
    assert len(result.fields) == 3
    t = result.tables[0]
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
    assert result.tables == ()
    assert result.fields == ()


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
    assert result.tables == ()
    assert result.fields == ()
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
    assert result.tables == () and result.fields == ()
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

    assert len(result.tables) == 1
    assert result.tables[0].face_id == "front"
    assert result.tables[0].overlaps_existing is True  # 表定義とちょうど重なる
    for f in result.fields:
        assert f.face_id == "front"
        assert f.overlaps_existing is True  # 単発欄の定義とちょうど重なる


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
    （08 §4.4 が「0.0 固定は禁止」と明記）。全欄が 0.0 に潰れていないこと、
    かつ全て非負であることを確認する。
    """
    binary = _binary(FORMB_PNG)
    result = detect_frames(binary, dpi=300)
    assert len(result.fields) == 3
    residuals = [f.residual_px for f in result.fields]
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
    assert len(result.tables) == 2
    assert len(result.fields) == 0
    for t in sorted(result.tables, key=lambda t: t.rect.x):
        assert t.rows == 2
        assert len(t.columns) == 2
        assert [c["width"] for c in t.columns] == [200, 200]
    xs = sorted(t.rect.x for t in result.tables)
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
    assert len(r300.tables) == 1
    assert r300.tables[0].rows == 3

    r600 = detect_frames(binary, dpi=600)
    assert len(r600.tables) == 1
    assert r600.tables[0].rows == 4


# ---------------------------------------------------------------------------
# formC（同寸別様式・生成素材）: 候補が出ること
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FORMC_PNG.exists(), reason="formC 画像が無い環境（make_formC.py で生成）")
def test_formc_produces_candidates():
    binary = _binary(FORMC_PNG)
    result = detect_frames(binary, dpi=300)
    assert result.zero_reason is None
    assert len(result.tables) >= 1
    t = result.tables[0]
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
    detail_like = [t for t in result.tables
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
