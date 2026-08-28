"""パイプラインの業務的拒否（レビュー H-C）。

pipeline.py から分離したのは、cli.py が pipeline を import すると起動時に
numpy/PIL まで引き込まれ、`--help` や `status` のような軽い経路まで重くなるため。
"""
from __future__ import annotations


class OperationRefused(RuntimeError):
    """業務的な拒否（テンプレ変更・多重起動など）。バグではない。

    SystemExit で投げていたときは CLI の except Exception を素通りして
    stderr へ生文字列が出るだけで、GUI は JSON Lines しか解釈しないため
    「終了コード 1。再度押すと続きから処理します」という**誤った案内**が
    出ていた（決定論的な拒否なので押しても永久に同じ結果）。
    hint には利用者が次に取れる行動を入れる。
    """

    def __init__(self, message: str, hint: str = ""):
        super().__init__(message)
        self.hint = hint
