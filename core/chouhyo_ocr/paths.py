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


def project_root() -> Path:
    """設定・入出力の基準。cwd から templates/ マーカーで遡り、無ければ app_root。

    GUI は cwd=<root>/core でコアを起動する。開発（python -m）と配布（frozen exe）
    で app_root が変わっても、config.json の解決先が GUI と食い違わないようにする。
    """
    d = Path.cwd()
    for cand in [d, *d.parents]:
        if (cand / "templates" / "chouhyo-v1.json").exists():
            return cand
    return app_root()


def template_schema_path() -> Path:
    return app_root() / "schema" / "template.schema.json"


_CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive",
                  "ドロップボックス")


def is_cloud_synced_path(p: str | Path) -> bool:
    """クラウド同期フォルダ・ネットワーク共有配下とみられるパスか（issue #8）。

    中間データは要配慮個人情報を含むため、同期対象パスへの配置を verify で
    警告する。判定はパス文字列のヒューリスティック（OneDrive/Dropbox/
    Google Drive の既定フォルダ名・UNC パス）で、完全ではない。
    """
    s = str(Path(p).resolve()).lower()
    if s.startswith("\\\\"):
        return True  # UNC（ネットワーク共有）
    return any(m in s for m in _CLOUD_MARKERS)
