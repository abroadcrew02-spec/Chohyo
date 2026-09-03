"""テスト全体を実行環境の %LOCALAPPDATA% から隔離する（issue #52 M-6・M-11）。

api_budget の月次カウンタ（`api_usage.json`）と cred_store の暗号化資格情報
（`cred.dpapi`）は、workdir に依存しない場所＝`%LOCALAPPDATA%\\ChouhyoOCR\\` に
置く。この場所は「作業フォルダを変えても config を変えても動かない」ことが
安全装置の要点だが、そのままだとテストが開発機の**本物の**カウンタと資格情報を
読み書きしてしまう。実害は2つあり、どちらも黙って起きる:

- テストで数えた送信ユニットが実カウンタへ加算され、上限が前倒しで来る
- 資格情報を作るテストが本物の置き場へ blob を残し、以後
  「資格情報が無い状態」を前提にした別のテスト（`credentials_state` が
  `missing` を返すことを固定するもの）が環境依存で落ちる

そこでテストごとに使い捨てのベースディレクトリを与える。各テストが自前で
`monkeypatch.setenv` する場合はそちらが後から勝つ（この fixture はセットアップ
時点の既定値を置くだけ）。

環境変数の名前に `_FOR_TESTS` が付いているのは、本番で設定する項目ではない
ことを名前自体で示すため（M-6 の指摘: 用途の分からない環境変数が上限回避の
経路になっていた）。
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_local_app_state(tmp_path_factory, monkeypatch):
    base = tmp_path_factory.mktemp("localappstate")
    monkeypatch.setenv("CHOUHYO_USAGE_DIR_FOR_TESTS", str(base / "usage"))
    monkeypatch.setenv("CHOUHYO_CRED_DIR_FOR_TESTS", str(base / "cred"))
