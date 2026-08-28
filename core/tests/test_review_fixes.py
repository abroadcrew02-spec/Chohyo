"""品質レビュー（2026-08-28）HIGH 指摘の再発防止テスト（issue #11/#13/#14）。

- #11: 金額正規化は normalize 属性で発火し、列名に依存しない（要件 §5.2）
- #13: glob メタ文字を含むファイル名の PDF が展開できる
- #14: config.json の不正値・未知キーは ConfigError で明示的に拒否される

#12/#15（エディタ）は TypeScript 側のため tsc ＋実機で確認する。
"""
import json

import pytest

from chouhyo_ocr.config import Config, ConfigError, load_config
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import UNCLEAR, build_row
from chouhyo_ocr.template import load_template

TPL = app_root() / "templates" / "chouhyo-v1.json"
CFG = Config(unclear_threshold=0.85, era_threshold=0.06)


# ---------- #11: 金額正規化の発火条件 ----------

def _template_with(tmp_path, rename_to=None, drop_normalize=False):
    t = json.loads(TPL.read_text(encoding="utf-8"))
    for face in t["faces"]:
        for tb in face.get("tables", []):
            for c in tb["columns"]:
                if c["name"] == "金額":
                    if drop_normalize:
                        c.pop("normalize", None)
                    if rename_to:
                        c["name"] = rename_to
    p = tmp_path / "t.json"
    p.write_text(json.dumps(t, ensure_ascii=False), encoding="utf-8")
    return load_template(p)


def _row_value(template, cells, colname):
    row = build_row(
        template,
        {"page_id": "p", "source_file": "s.png", "page_no": 1,
         "status": "", "unassigned_below_table": 0},
        cells, {}, CFG)
    cols = [oc for c in template.cells for oc in c.output_columns()]
    return row.values[cols.index(colname)]


def test_amount_fires_on_normalize_attr_not_column_name(tmp_path):
    """列名を「支払金額」へ改名しても normalize: amount があれば整数化される。"""
    template = _template_with(tmp_path, rename_to="支払金額")
    cells = {c.field_id: ("", None, c.kind, False) for c in template.cells}
    cells["detail_01_支払金額"] = ("1,234", 0.95, "text", False)
    assert _row_value(template, cells, "detail_01_支払金額") == 1234


def test_amount_does_not_fire_without_normalize_attr(tmp_path):
    """normalize が無ければ、列名が「金額」でも正規化しない（属性だけが契約）。"""
    template = _template_with(tmp_path, drop_normalize=True)
    cells = {c.field_id: ("", None, c.kind, False) for c in template.cells}
    cells["detail_01_金額"] = ("1,234", 0.95, "text", False)
    assert _row_value(template, cells, "detail_01_金額") == "1,234"


def test_shipped_template_declares_amount_normalize():
    """同梱テンプレートの金額列は normalize: amount を宣言している。"""
    template = load_template(TPL)
    amount_cells = [c for c in template.cells if c.normalize == "amount"]
    assert len(amount_cells) == 28  # 明細28行 × 金額1列
    assert all(c.field_id.endswith("金額") for c in amount_cells)


# ---------- #13: glob メタ文字を含むファイル名 ----------

def test_expand_handles_bracket_filename(tmp_path):
    """scan[1].pdf（ブラウザの重複既定名）が展開失敗にならない。"""
    from PIL import Image

    from chouhyo_ocr.ingest import expand
    src = tmp_path / "scan[1].pdf"
    Image.new("L", (200, 280), 255).save(src)  # 1ページの最小 PDF
    out = tmp_path / "pages"
    out.mkdir()
    pages = expand(src, dpi=72, out_dir=out)
    assert len(pages) == 1
    assert pages[0].name.startswith("scan[1]-")


# ---------- #14: config の検証 ----------

def _write_cfg(tmp_path, data):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_zero_threshold_rejected(tmp_path):
    """〓閾値 0 は転記主義の無効化なので拒否（GUI の空入力事故の最終防衛線）。"""
    p = _write_cfg(tmp_path, {"unclear_threshold": 0})
    with pytest.raises(ConfigError, match="unclear_threshold"):
        load_config(p)


def test_typo_key_rejected(tmp_path):
    """キー名 typo は無言で既定値に落とさず、キー名を示して拒否する。"""
    p = _write_cfg(tmp_path, {"unclear_thresold": 0.9})
    with pytest.raises(ConfigError, match="unclear_thresold"):
        load_config(p)


def test_wrong_type_rejected(tmp_path):
    p = _write_cfg(tmp_path, {"send_limit": "abc"})
    with pytest.raises(ConfigError, match="send_limit"):
        load_config(p)
    p2 = _write_cfg(tmp_path, {"workdir": ""})
    with pytest.raises(ConfigError, match="workdir"):
        load_config(p2)


def test_valid_config_passes(tmp_path):
    p = _write_cfg(tmp_path, {"unclear_threshold": 0.9, "send_limit": 50,
                              "workdir": "wd"})
    cfg = load_config(p)
    assert cfg.unclear_threshold == 0.9
    assert cfg.send_limit == 50
    assert cfg.era_threshold == 0.05  # 未指定は既定値
