"""実行環境に依存するパス解決。

開発環境（python 直実行）と PyInstaller 配布後（sys.frozen）でリポジトリ相対の
パスが変わるため、基準ディレクトリの解決をここへ集約する（設計 §12-C1）。
"""
import os
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


def user_templates_dir() -> Path:
    """利用者テンプレートの保存先（issue #72 (t)・08 §3.1・2026-09-02
    Orchestrator 判断）。

    **Rust 側が唯一の決定者**（`app_data_dir()/templates_user/`）——環境変数
    `CHOUHYO_USER_DIR` で core へ受け渡す。列挙・reparse point 検査は Rust の
    1箇所に集約する契約（08 §3.10 不変条件3）で、ここは**受け取った値を
    検証するだけ**（環境変数は他プロセスからも渡りうるため信用しない）。

    `CHOUHYO_USER_DIR` が設定されていれば、絶対パス・実在・ディレクトリ・
    reparse point でないことを検証する。**不正なら ConfigError 相当で
    明示的に失敗する**——FR-F29／`last_template` が採る「設定1つで起動
    不能にしない」フォールバック方針とは意図的に違う。あちらは利用者が
    手編集しうる `config.json` の1キーの解釈だが、こちらは Rust が
    渡したはずの実行環境値そのものの整合性が崩れているサインであり、
    黙って別の場所（`project_root()/templates_user`）へフォールバックすると
    「利用者は `templates_user` に保存したつもりが、実際には別の場所に
    保存されていた」という気づきにくい事故になる。

    未設定時のフォールバック（`project_root()/templates_user`）は
    **frozen でない（開発・CLI 単体運用）ときだけ**許可する。frozen
    （配布・GUI 起動）なのに未設定なら `ConfigError` で明示的に失敗する
    （2026-09-02 AZKi 指摘 M-6）——frozen 環境は本来 Rust が必ずこの環境変数を
    設定して core を起動するため、未設定は「Rust 側で `user_templates_dir(app)`
    の解決に失敗したのに、環境変数を付けずに core を起動してしまった」異常
    事態のサイン。ここで黙って `project_root()/templates_user`
    （frozen なら exe の親ディレクトリ相当）へ倒すと、この docstring が
    防ごうとしている「利用者は templates_user に保存したつもりが、実際には
    別の場所に保存されていた」事故がまさに発生する。
    """
    from .config import ConfigError  # 遅延 import（config.py → paths.py の循環を避ける）
    raw = os.environ.get("CHOUHYO_USER_DIR")
    if not raw:
        if getattr(sys, "frozen", False):
            raise ConfigError(
                "CHOUHYO_USER_DIR が未設定です。GUI から起動してください"
                "（配布環境では Rust が起動時にこの環境変数を必ず設定します）")
        return project_root() / "templates_user"
    p = Path(raw)
    if not p.is_absolute():
        raise ConfigError(f"CHOUHYO_USER_DIR は絶対パスにする（現在: {raw!r}）")
    # M-2（2026-09-02 マリン指摘）: is_symlink() だけでは Windows のジャンクション
    # （IO_REPARSE_TAG_MOUNT_POINT）を検出できない場合がある——is_symlink() が
    # 見ているのは IO_REPARSE_TAG_SYMLINK のみ。os.path.isjunction()（Python
    # 3.13+）と両方を通す
    if p.is_symlink() or os.path.isjunction(p):
        raise ConfigError(
            "CHOUHYO_USER_DIR に reparse point（symlink・ジャンクション）は使えない"
            f"（現在: {raw!r}）")
    if not p.is_dir():
        raise ConfigError(f"CHOUHYO_USER_DIR は実在するディレクトリにする（現在: {raw!r}）")
    # `..` を含むパスは resolve() で正規化してから返す（LOW・M-2 と同時）。
    # 生の文字列のまま返すと、これを受け取った側が素朴な文字列比較で範囲を
    # 判定した場合に `..` で範囲外へ抜けられる余地が残る——正規化した絶対パス
    # を返すことで、この関数の戻り値を信頼する呼び出し側の実装を単純にする。
    # resolve() 後に再度 reparse point でないことを確認する（symlink 越しに
    # 別の場所へ解決されていないかの最終防御・TOCTOU 対策）
    resolved = p.resolve()
    if resolved.is_symlink() or os.path.isjunction(resolved):
        raise ConfigError(
            "CHOUHYO_USER_DIR の解決先に reparse point がある"
            f"（現在: {raw!r} → {resolved}）")
    return resolved


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
