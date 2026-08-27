"""実行環境に依存するパス解決。

開発環境（python 直実行）と PyInstaller 配布後（sys.frozen）でリポジトリ相対の
パスが変わるため、基準ディレクトリの解決をここへ集約する（設計 §12-C1）。
"""
import sys
from pathlib import Path


def app_root() -> Path:
    """同梱リソース（schema/ など）の基準ディレクトリ。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # core/chouhyo_ocr/paths.py → リポジトリルート
    return Path(__file__).resolve().parents[2]


def template_schema_path() -> Path:
    return app_root() / "schema" / "template.schema.json"
