"""core-dist（同梱 exe）鮮度検査のテスト。

2026-09-02: 同梱 exe が再ビルドされないまま core 側の変更を反映せず配布された
事故（core 側17コミット未反映のまま argparse エラー）を受けて追加した。
ビルド時のソース内容ハッシュ（スタンプ）と現在のソースの内容ハッシュを比較する
scripts/dist_stamp.py の判定ロジックを、疑似リポジトリ（tmp_path）で固定する。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dist_stamp  # noqa: E402


def _make_repo(tmp_path: Path) -> Path:
    """dist_stamp が参照する3系統（core/chouhyo_ocr・schema・templates）だけを
    持つ最小のリポジトリもどきを作る。"""
    (tmp_path / "core" / "chouhyo_ocr").mkdir(parents=True)
    (tmp_path / "core" / "chouhyo_ocr" / "cli.py").write_text(
        "print('hello')\n", encoding="utf-8")
    (tmp_path / "schema").mkdir()
    (tmp_path / "schema" / "template.schema.json").write_text(
        "{}\n", encoding="utf-8")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "chouhyo-v1.json").write_text(
        "{}\n", encoding="utf-8")
    return tmp_path


def _make_app_dir(root: Path) -> Path:
    app = root / "core-dist" / "chouhyo-core"
    app.mkdir(parents=True)
    (app / "chouhyo-core.exe").write_bytes(b"dummy exe")
    return app


def test_no_exe_is_skip(tmp_path):
    root = _make_repo(tmp_path)
    status, detail = dist_stamp.check_freshness(root)
    assert status == "SKIP"
    assert "未ビルド" in detail


def test_exe_without_stamp_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    _make_app_dir(root)
    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "build_dist.py" in detail


def test_write_stamp_then_check_is_pass(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    status, detail = dist_stamp.check_freshness(root)
    assert status == "PASS"
    assert "built_at" in detail


def test_changed_core_py_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    target = root / "core" / "chouhyo_ocr" / "cli.py"
    target.write_text(target.read_text(encoding="utf-8") + "# changed\n",
                       encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "core/chouhyo_ocr/cli.py" in detail


def test_changed_schema_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (root / "schema" / "template.schema.json").write_text(
        '{"changed": true}\n', encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "schema/template.schema.json" in detail


def test_changed_template_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (root / "templates" / "chouhyo-v1.json").write_text(
        '{"changed": true}\n', encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "templates/chouhyo-v1.json" in detail


def test_pycache_changes_are_ignored(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    pycache = root / "core" / "chouhyo_ocr" / "__pycache__"
    pycache.mkdir()
    # 通常は .pyc だが、拡張子ではなくディレクトリ名で除外していることを
    # 確認するため、あえて .py 拡張子で置く
    (pycache / "cli.cpython-313.py").write_text("stale\n", encoding="utf-8")
    dist_stamp.write_stamp(root, app)

    (pycache / "cli.cpython-313.py").write_text("changed\n", encoding="utf-8")
    (pycache / "new.py").write_text("new\n", encoding="utf-8")

    status, _ = dist_stamp.check_freshness(root)
    assert status == "PASS"


def test_stamp_that_is_a_list_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.stamp_path(app).write_text("[]\n", encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "形式が不正" in detail


def test_stamp_with_null_files_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.stamp_path(app).write_text('{"files": null}\n', encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "形式が不正" in detail


def test_added_py_file_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (root / "core" / "chouhyo_ocr" / "new_module.py").write_text(
        "x = 1\n", encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "追加" in detail
    assert "core/chouhyo_ocr/new_module.py" in detail


def test_removed_py_file_is_fail(tmp_path):
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (root / "core" / "chouhyo_ocr" / "cli.py").unlink()

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "削除" in detail
    assert "core/chouhyo_ocr/cli.py" in detail


def test_subdirectory_py_change_is_detected(tmp_path):
    root = _make_repo(tmp_path)
    sub = root / "core" / "chouhyo_ocr" / "sub"
    sub.mkdir()
    (sub / "x.py").write_text("a = 1\n", encoding="utf-8")
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (sub / "x.py").write_text("a = 2\n", encoding="utf-8")

    status, detail = dist_stamp.check_freshness(root)
    assert status == "FAIL"
    assert "core/chouhyo_ocr/sub/x.py" in detail


def test_out_of_scope_files_do_not_affect_result(tmp_path):
    """検査対象3系統の外（core/tests・README.md）を変えても PASS のまま
    （SOURCE_GLOBS の範囲外への誤検知が無いことの境界テスト）。"""
    root = _make_repo(tmp_path)
    app = _make_app_dir(root)
    dist_stamp.write_stamp(root, app)

    (root / "core" / "tests").mkdir(parents=True, exist_ok=True)
    (root / "core" / "tests" / "foo.py").write_text("pass\n", encoding="utf-8")
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    status, _ = dist_stamp.check_freshness(root)
    assert status == "PASS"
