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


# 既定の同期フォルダ名。判定はパス成分の完全一致（または「<名前> - 会社名」形式の
# 接頭辞一致）なので、`C:\work\dropbox_backup` のような無関係な名前は拾わない。
# 業務利用のある同期クライアントを広めに含める（レビュー4巡目 M-12）。
# 検知漏れの代償は要配慮個人情報のクラウド送出で、誤検知の代償は
# 「保存先を変えてください」と一度言われることなので、広めに倒す。
_CLOUD_MARKERS = ("onedrive", "dropbox", "google drive", "googledrive",
                  "ドロップボックス",
                  "box", "box sync", "boxdrive",
                  "nextcloud", "owncloud",
                  "icloud drive", "iclouddrive",
                  "egnyte", "syncplicity", "pcloud", "seafile")


def is_cloud_synced_path(p: str | Path) -> bool:
    """クラウド同期フォルダ・ネットワーク共有配下とみられるパスか（issue #8）。

    中間データは要配慮個人情報を含むため、同期対象パスへの配置を verify で
    警告する。判定はパス文字列のヒューリスティック（OneDrive/Dropbox/
    Google Drive の既定フォルダ名・UNC パス）で、完全ではない。
    """
    resolved = Path(p).resolve()
    if str(resolved).startswith("\\\\"):
        return True  # UNC（ネットワーク共有）
    # パス成分の完全一致で見る（レビュー M-10: 部分文字列一致だと
    # C:\work\dropbox_backup のような無関係なフォルダ名でも検知していた）。
    # 「OneDrive - 会社名」形式は実在するため接頭辞一致も許す
    parts = [part.lower() for part in resolved.parts]
    for part in parts:
        if part in _CLOUD_MARKERS:
            return True
        if any(part.startswith(m + " -") or part.startswith(m + "-")
               for m in _CLOUD_MARKERS):
            return True
    return False
