"""同一 workdir への多重起動を防ぐロック（issue #35）。

二重起動すると2プロセスが同じ todo を独立に取得し、**全ページが2回送信され**
（実測: 30ページ全件 attempt=2）、send_limit はプロセスローカル変数なので
実質「上限×プロセス数」まで送られる。さらに出力の書き込みが同一秒で衝突すると
片方が壊れた xlsx を rc=0 で「成功」として返す（実測）。

ページ単位の排他ではなく実行単位の排他にしたのは、この処理が「1つの入力
フォルダを1人が一括処理する」運用だから（要件 §3.1）。並列実行に価値が無い
以上、複雑な claim 機構より「2本目を明示的に断る」ほうが壊れ方が分かりやすい。
"""
from __future__ import annotations

import os
from pathlib import Path


class RunLockError(RuntimeError):
    """既に別プロセスが同じ workdir で実行中。"""


class RunLock:
    """workdir 単位の排他ロック（Windows/POSIX とも O_EXCL の原子性に依存）。

    プロセスが異常終了してロックが残った場合は、記録した PID が生きているかを
    見て自動的に奪う——手で消させると「毎回消せばいい」と学習されてしまい、
    本来の二重起動まで素通りする。
    """

    def __init__(self, workdir: str | Path):
        self.path = Path(workdir) / ".run.lock"
        self.fd: int | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            busy = RunLockError(
                "同じ保存先で別の処理が実行中。二重に実行すると同じページを"
                "2回送信して課金が二重になるため中止した。"
                "先の処理の完了を待つか、GUI を1つだけ開いて実行する")
            if self._holder_alive():
                raise busy from None
            # 死んだプロセスの残骸。奪って続行する（nt では _holder_alive が
            # 判定と同時に削除済み。POSIX はここで消す）
            try:
                if os.name != "nt":
                    self.path.unlink()
                self.fd = os.open(self.path,
                                  os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except OSError:
                raise busy from None
        os.write(self.fd, str(os.getpid()).encode("ascii"))

    def _holder_alive(self) -> bool:
        """保持者が生きているか。

        **Windows で os.kill(pid, 0) を使ってはいけない**——POSIX の「存在確認」
        とは違い、TerminateProcess でプロセスを本当に殺す。PID が再利用されて
        いれば無関係のプロセスを落とす。代わりにファイルの削除可否で判定する:
        保持者は fd を開いたままなので、生きていれば Windows は削除を拒む。
        異常終了した場合は OS が handle を閉じるので削除できる。
        """
        if os.name == "nt":
            return not self._can_remove()
        # POSIX は開かれたままでも unlink できるので PID の生存を見る
        try:
            pid = int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return False  # 中身が読めない残骸は死んでいる扱い
        try:
            os.kill(pid, 0)  # POSIX ではシグナル0＝存在確認（送信はしない）
        except OSError:
            return False
        return True

    def _can_remove(self) -> bool:
        try:
            self.path.unlink()
        except OSError:
            return False
        return True

    def release(self) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "RunLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
