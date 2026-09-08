"""issue #156: app.log / error.log をローテーションする。

既定のログ（workdir の兄弟 logs/）は purge の対象外で、ローテーションも
無かったため無停止で際限なく育っていた（実測: 2026-08-27 から無停止で
app.log 3.87MB）。source_file（入力ファイル名）・path（絶対パス）は白リスト
済みで意図的に通しているため、ファイルを人名で命名する運用では削除操作の
後もログが氏名の索引として残り続ける——上限を切ることで実害の量を抑える。

ここでは `RotatingFileHandler` の世代管理そのもの（何バイトで何世代作るか）
を検証する。ログの中身（許可キーのみ）は test_leak_guards.py の担当で、
このファイルでは扱わない。
"""
from chouhyo_ocr import logging_safe


def test_app_log_rotates_past_max_bytes(tmp_path):
    """app.log が上限を超えたら app.log.1 へ退避し、世代数の上限を守る。"""
    log_dir = tmp_path / "logs"
    logging_safe.init(str(log_dir))

    # "path" は許可キー（絶対パス想定）——ここではテスト用のダミー文字列を
    # 詰めて1行の分量を稼ぎ、5MB 超過までの反復回数を抑える（実行時間対策）。
    pad = "x" * 400
    n = 15_000  # 1行 約470バイト × 15000 ≈ 7MB（5MB を確実に超える）
    for i in range(n):
        logging_safe.info("rotation_probe", count=i, path=pad)

    app_log = log_dir / "app.log"
    assert app_log.exists()
    # 現行ファイルは上限に張り付く程度に収まる（多少の超過は許容——
    # RotatingFileHandler は「今回の書き込みで超えたら次回ロールする」ため、
    # 直前の1回分だけ上限を超えうる）
    assert app_log.stat().st_size <= logging_safe._MAX_LOG_BYTES * 1.05

    backups = sorted(log_dir.glob("app.log.*"))
    assert backups, "ローテーションが一度も起きていない"
    assert len(backups) <= logging_safe._LOG_BACKUP_COUNT


def test_max_bytes_and_backup_count_are_the_documented_values():
    """issue #156 の想定値（5MB・世代3）そのものを固定する。"""
    assert logging_safe._MAX_LOG_BYTES == 5 * 1024 * 1024
    assert logging_safe._LOG_BACKUP_COUNT == 3


def test_reinit_still_appends_without_error(tmp_path):
    """同じ log_dir へ複数回 init しても（run→render 等の複数回呼び出し）、
    RotatingFileHandler への切り替え後も例外にならず書き続けられる。
    """
    log_dir = tmp_path / "logs"
    logging_safe.init(str(log_dir))
    logging_safe.info("first_init")
    logging_safe.init(str(log_dir))  # 同じ場所へ再初期化（cli.py の複数コマンド呼び出しと同型）
    logging_safe.info("second_init")

    text = (log_dir / "app.log").read_text(encoding="utf-8")
    assert "first_init" in text
    assert "second_init" in text
