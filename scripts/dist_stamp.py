# -*- coding: utf-8 -*-
"""core-dist（同梱 exe）の鮮度検査。

2026-09-02: GUI が優先起動する同梱 exe（core-dist/chouhyo-core/chouhyo-core.exe）
が 2026-08-31 16:45 のビルドのまま放置され、その後の core 側17コミット
（expand-page --no-mask 追加・schema への output 属性追加など）が反映されず、
テンプレート編集の PDF 展開が argparse エラーで失敗した。README には
「core/・schema/・templates/ を変えたら build_dist.py を再実行する」と書いて
あるが、scripts/run_all_tests.py（回帰ゲート）は同梱物に一切触れないため、
ゲートが緑のまま配布物だけ古くなっていた。

この検査は、ビルド時に書き出したソースの内容ハッシュ（スタンプ）と、現在の
ソースの内容ハッシュを比較して差分の有無を判定する。mtime 比較は git
checkout・clone のたびに値が変わってしまうため採らない。

見ていないもの: `core/pyproject.toml`（依存バージョン）と `vendor/poppler/**`
は同梱されるがハッシュ対象外（README の「再ビルドが要る3系統」に範囲を合わせた
意図的な除外）。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

# README「core/ のコード・schema/・templates/ のいずれかを変更したら必ず
# 再ビルドする」の3系統と一致させる（scripts/build_dist.py の複製元と同じ）
SOURCE_GLOBS = (
    "core/chouhyo_ocr/**/*.py",
    "schema/**/*.json",
    "templates/chouhyo-v1.json",
)


def source_digest(root: Path) -> dict[str, str]:
    """再ビルド対象3系統のファイルを repo 相対パス（/区切り）→sha256 の辞書にする。"""
    root = Path(root)
    digest: dict[str, str] = {}
    for pattern in SOURCE_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(root).as_posix()
            digest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return dict(sorted(digest.items()))


def stamp_path(app_dir: Path) -> Path:
    return Path(app_dir) / "BUILD_STAMP.json"


def _git_head(root: Path) -> str | None:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    head = r.stdout.strip()
    return head or None


def write_stamp(root: Path, app_dir: Path) -> Path:
    """ビルド成功直後に呼ぶ。現在のソース内容ハッシュをスタンプとして書き出す。"""
    root = Path(root)
    app_dir = Path(app_dir)
    payload = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": source_digest(root),
    }
    head = _git_head(root)
    if head:
        payload["git_head"] = head
    path = stamp_path(app_dir)
    # newline="" で改行変換を無効化し LF 固定にする（既定だと Windows では書き込み
    # 時に \n が \r\n へ変換され CRLF になる。実害はないがバイトを安定させる）
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8", newline="")
    return path


def check_freshness(root: Path, app_dir: Path | None = None) -> tuple[str, str]:
    """core-dist の鮮度を判定する。戻りは (\"SKIP\"|\"PASS\"|\"FAIL\", 説明文)。"""
    root = Path(root)
    app_dir = Path(app_dir) if app_dir is not None else root / "core-dist" / "chouhyo-core"
    exe = app_dir / "chouhyo-core.exe"
    if not exe.exists():
        return "SKIP", "core-dist 未ビルド（開発環境では GUI が .venv を使うため任意）"

    stamp_file = stamp_path(app_dir)
    if not stamp_file.exists():
        return "FAIL", ("スタンプ無し（この検査より前のビルド）。再ビルド: "
                         ".venv\\Scripts\\python.exe scripts\\build_dist.py")

    try:
        stamped = json.loads(stamp_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return "FAIL", f"スタンプを読めない（{type(e).__name__}）。再ビルドしてください"

    # JSON としては読めても期待した形（dict かつ files が dict）でなければ、
    # 直後の .get()/set() で例外を投げてゲートごと落ちる（実測: [] で
    # AttributeError、{"files": null} で TypeError）。壊れた/手編集されたスタンプ
    # は再ビルドを促す FAIL として扱う
    if not isinstance(stamped, dict) or not isinstance(stamped.get("files"), dict):
        return "FAIL", ("スタンプの形式が不正。再ビルド: "
                         ".venv\\Scripts\\python.exe scripts\\build_dist.py")

    stamped_files = stamped["files"]
    current_files = source_digest(root)

    diffs = []
    for rel in sorted(set(stamped_files) | set(current_files)):
        old = stamped_files.get(rel)
        new = current_files.get(rel)
        if old == new:
            continue
        if old is None:
            diffs.append(f"追加: {rel}")
        elif new is None:
            diffs.append(f"削除: {rel}")
        else:
            diffs.append(f"変更: {rel}")

    if diffs:
        shown = diffs[:5]
        detail = "、".join(shown)
        rest = len(diffs) - len(shown)
        if rest > 0:
            detail += f"、ほか {rest} 件"
        return "FAIL", (f"同梱 exe がソースより古い（{detail}）。再ビルド: "
                         ".venv\\Scripts\\python.exe scripts\\build_dist.py")

    built_at = stamped.get("built_at", "?")
    return "PASS", f"built_at {built_at} と一致"
