"""サードパーティ表記（THIRD-PARTY-NOTICES.txt）の生成。

配布物には第三者のソフトウェアが多数含まれる。MIT・Apache-2.0・Unicode-3.0 の
いずれも、バイナリだけを配る場合でも著作権表示と許諾条文を添えることを求めて
いる。このスクリプトは、その表記を実際の依存関係から機械的に集めて 1 ファイル
にまとめる。手書きしない——依存が変わるたびに人手で追うのは追従できない。

出力: <repo>/THIRD-PARTY-NOTICES.txt
  第1部 Rust   … GUI 実行ファイルへリンクされるクレート（cargo-about）
  第2部 Python … コア CLI へ同梱されるパッケージ（pip-licenses）

実行:
    .venv\\Scripts\\python.exe scripts\\gen_notices.py

前提となるツール（未導入なら下記を先に実行する）:
    cargo install cargo-about --locked --features cli
    .venv\\Scripts\\pip install pip-licenses

Python 側は「PyInstaller が実際に何を取り込んだか」を正とするため、先に
scripts/build_dist.py を通しておく必要がある。ビルドの中間生成物
（workdir_build/pyi/chouhyo-core/*.toc）を読んで同梱パッケージを決めるので、
pytest や playwright のような開発専用の依存は自動的に外れる。
"""
import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TAURI = ROOT / "gui" / "src-tauri"
PYI_TOC_DIR = ROOT / "workdir_build" / "pyi" / "chouhyo-core"
OUT = ROOT / "THIRD-PARTY-NOTICES.txt"

# 配布対象。Cargo.lock は全プラットフォーム分のクレートを含むため、ここで
# 絞らないと配布物に載らない macOS/Linux 向けクレートまで表記に混ざる。
TARGET = "x86_64-pc-windows-msvc"

RULE = "=" * 78
THIN = "-" * 78

# PyInstaller の TOC はビルド時のファイルパスを持つ。site-packages 直下の
# 名前を拾えば、取り込まれた配布物（パッケージ）を機械的に割り出せる。
SITE_PACKAGES_REF = re.compile(r"site-packages[\\/]+([A-Za-z0-9_.\-]+)")

# 実行ファイルへ載る TOC だけを見る。Analysis-00.toc は「解析した」だけで
# 除外されたモジュールも含むため使わない（playwright 等が紛れ込む）。
SHIPPED_TOCS = ("PYZ-00.toc", "COLLECT-00.toc", "EXE-00.toc", "PKG-00.toc")

# PyInstaller 本体は GPL-2.0 だが、凍結したアプリケーションへ組み込まれる
# ブートローダには例外規定があり、生成物を任意のライセンスで配布できる。
# 表記からは外さない——ブートローダのバイナリは配布物に実在するため。
PYINSTALLER_NOTE = (
    "PyInstaller 本体は GPL-2.0 だが、凍結した実行ファイルへ組み込まれる\n"
    "ブートローダには例外規定（bootloader exception）があり、生成された\n"
    "実行ファイルを任意のライセンスで配布できる。本ツールは PyInstaller を\n"
    "改変せずに使っているため、この例外の範囲内にある。"
)

# 表記だけでは誤読される配布物への個別注記。パッケージ全体のライセンスと、
# 実際に配布物へ載る部分の扱いが食い違うものだけをここに置く。
PACKAGE_NOTES = {
    "pyinstaller-hooks-contrib":
        "※ 配布物に含まれるのは runtime hooks"
        "（_pyinstaller_hooks_contrib/rthooks/）のみ。適用ライセンスは Apache-2.0。",
}


def lf(text: str) -> str:
    """改行を LF に揃える。

    ライセンス条文は配布元ごとに CRLF だったり LF だったりする。そのまま
    連結すると 1 ファイルの中で改行コードが混ざり、Git の自動変換
    （core.autocrlf）がかかったときに作業ツリーと生成結果が食い違う。
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


def run(cmd, cwd=None):
    """外部コマンドを実行して stdout を返す。失敗したら例外で止める。"""
    print("+ " + " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        sys.stderr.write(r.stdout or "")
        sys.stderr.write(r.stderr or "")
        raise SystemExit(f"NG: コマンドが失敗した（終了コード {r.returncode}）")
    if r.stderr.strip():
        # cargo-about はライセンス判定の警告を stderr に出す。握り潰さない。
        print(r.stderr.strip(), file=sys.stderr, flush=True)
    return r.stdout


# --------------------------------------------------------------------------
# Rust（cargo-about）
# --------------------------------------------------------------------------

def collect_rust(offline: bool) -> dict:
    cmd = ["cargo", "about", "generate", "--format", "json", "--locked"]
    if offline:
        cmd.append("--offline")
    return json.loads(run(cmd, cwd=TAURI))


def rust_notice_files(data: dict) -> list[tuple[str, str]]:
    """クレートが同梱している NOTICE ファイルを集める。

    Apache-2.0 §4(d) は、配布物に NOTICE ファイルが含まれる場合その内容を
    再現することを求める。cargo-about の JSON には NOTICE を扱う項目が無い
    （出力は name/id/first_of_kind/text/source_path/used_by だけ）ので、
    クレートの展開先を自分で見る。
    """
    found = []
    for c in data.get("crates", []):
        pkg = c["package"]
        root = Path(pkg["manifest_path"]).parent
        if not root.is_dir():
            continue
        for f in sorted(root.iterdir()):
            if f.is_file() and f.name.upper().startswith("NOTICE"):
                found.append((f'{pkg["name"]} {pkg["version"]}',
                              lf(f.read_text(encoding="utf-8", errors="replace"))))
    return found


def render_rust(data: dict) -> tuple[list[str], int, dict]:
    crates = data.get("crates", [])
    licenses = data.get("licenses", [])

    # ライセンス ID ごとに、テキストの実体（＝著作権表示が違うもの）を束ねる。
    # MIT のように 190 クレートが使うライセンスでも、著作権者はクレートごとに
    # 違うため、テキストは 1 つにまとめられない。
    by_id = defaultdict(list)
    for lic in licenses:
        by_id[lic["id"]].append(lic)

    counts = {o["id"]: o["count"] for o in data.get("overview", [])}
    names = {o["id"]: o["name"] for o in data.get("overview", [])}

    out = [RULE, "第1部  Rust — GUI 実行ファイルに含まれるクレート", RULE, ""]
    out.append(f"対象クレート数: {len(crates)}（ターゲット {TARGET}）")
    out.append("ビルド時にのみ使うクレート（tauri-build 等）と手続きマクロは、")
    out.append("実行ファイルに載らないため含めていない。")
    out.append("")
    out.append("ライセンス別の内訳:")
    for lid in sorted(counts, key=lambda k: (-counts[k], k)):
        out.append(f"    {lid:<28} {counts[lid]:>4} クレート")
    out.append("")

    for lid in sorted(by_id):
        entries = by_id[lid]
        out += ["", RULE, f"[Rust] {names.get(lid, lid)}  ({lid})", RULE, ""]
        # 並びは対象クレート名で決める。source_path はビルド環境の絶対パスで、
        # 実行するマシンが変わると順序が動く（そもそも None のこともある）。
        def sort_key(lic):
            users = [f'{u["crate"]["name"]} {u["crate"]["version"]}'
                     for u in lic.get("used_by", [])]
            return (sorted(users), lic.get("text") or "")

        for lic in sorted(entries, key=sort_key):
            users = sorted(
                f'{u["crate"]["name"]} {u["crate"]["version"]}'
                for u in lic.get("used_by", []))
            out.append("対象クレート: " + (", ".join(users) if users else "（なし）"))
            out.append("")
            out.append(lf(lic.get("text") or "").rstrip())
            out += ["", THIN, ""]

    notices = rust_notice_files(data)
    out += ["", RULE, "[Rust] 各クレート同梱の NOTICE（Apache-2.0 §4(d)）", RULE, ""]
    if notices:
        for name, text in notices:
            out += [f"対象クレート: {name}", "", text.rstrip(), "", THIN, ""]
    else:
        # 「探した上で無かった」ことを残す。依存が増えて NOTICE を持つクレートが
        # 入れば、この行が消えるので --check が食い違いとして拾う。
        out += ["NOTICE ファイルを同梱するクレートは無い（全 "
                f"{len(crates)} クレートを走査）。", ""]
    return out, len(crates), counts


# --------------------------------------------------------------------------
# Python（pip-licenses）
# --------------------------------------------------------------------------

def shipped_python_packages() -> list[str]:
    """PyInstaller が実行ファイルへ取り込んだ配布物の名前を返す。

    「開発環境に入っているもの」ではなく「配布物に載ったもの」を数える。
    pytest・playwright・pip-licenses 自身のような開発専用の依存はここで落ちる。
    """
    from importlib.metadata import packages_distributions

    if not PYI_TOC_DIR.is_dir():
        raise SystemExit(
            f"NG: PyInstaller の中間生成物が無い（{PYI_TOC_DIR}）。\n"
            "    先に .venv\\Scripts\\python.exe scripts\\build_dist.py を実行する。")

    tops = set()
    for name in SHIPPED_TOCS:
        toc = PYI_TOC_DIR / name
        if not toc.exists():
            continue
        text = toc.read_text(encoding="utf-8", errors="replace")
        tops.update(m.group(1) for m in SITE_PACKAGES_REF.finditer(text))
    if not tops:
        raise SystemExit(f"NG: {PYI_TOC_DIR} から同梱パッケージを読み取れない。")

    mapping = packages_distributions()
    found, unknown = set(), set()
    for top in tops:
        if top.endswith((".dist-info", ".egg-info")):
            # 配布物のメタデータディレクトリ。トップレベル名の側で拾えるので飛ばす。
            continue
        dists = mapping.get(top.split(".")[0]) or mapping.get(top)
        if dists:
            found.update(dists)
        else:
            unknown.add(top)
    if unknown:
        print(f"注意: 配布物を特定できないトップレベル名 {sorted(unknown)}",
              file=sys.stderr)
    return sorted(found, key=str.lower)


def collect_python(packages: list[str]) -> list[dict]:
    cmd = [sys.executable, "-m", "piplicenses",
           "--format=json", "--with-license-file", "--with-authors",
           "--with-urls",
           # Apache-2.0 §4(d) は、配布物に NOTICE ファイルが含まれる場合その
           # 内容を再現することを求める。--with-license-file と併用が前提。
           "--with-notice-file",
           # setuptools は既定で「システム扱い」として除外される。実際には
           # 配布物へ載っているので明示的に含める。
           "--with-system",
           "--packages", *packages]
    return json.loads(run(cmd, cwd=ROOT))


def render_python(entries: list[dict]) -> tuple[list[str], int, dict]:
    counts = defaultdict(int)
    for e in entries:
        counts[e.get("License", "UNKNOWN")] += 1

    out = ["", RULE, "第2部  Python — コア CLI（chouhyo-core.exe）に同梱されるパッケージ",
           RULE, ""]
    out.append(f"対象パッケージ数: {len(entries)}")
    out.append("PyInstaller が実行ファイルへ取り込んだものだけを載せている。")
    out.append("テスト専用の依存（pytest・playwright 等）は配布物に含まれない。")
    out.append("")
    out.append("ライセンス別の内訳:")
    for lid in sorted(counts, key=lambda k: (-counts[k], k)):
        out.append(f"    {lid:<46} {counts[lid]:>3} パッケージ")
    out += ["", "PyInstaller について:", PYINSTALLER_NOTE, ""]

    for e in sorted(entries, key=lambda x: x["Name"].lower()):
        out += ["", RULE,
                f'[Python] {e["Name"]} {e["Version"]}  ({e.get("License", "UNKNOWN")})',
                RULE, ""]
        note = PACKAGE_NOTES.get(e["Name"].lower())
        if note:
            out += [note, ""]
        if e.get("Author") and e["Author"] != "UNKNOWN":
            out.append(f'著作者: {e["Author"]}')
        if e.get("URL") and e["URL"] != "UNKNOWN":
            out.append(f'配布元: {e["URL"]}')
        out.append("")
        text = lf(e.get("LicenseText") or "").strip()
        if text and text != "UNKNOWN":
            out.append(text)
        else:
            # 条文が取れないものは黙って空欄にしない。手当てが要ると分かるようにする。
            out.append("※ ライセンス条文のファイルが配布物のメタデータに含まれて"
                       "いない。上記の配布元を参照すること。")
        notice = lf(e.get("NoticeText") or "").strip()
        if notice and notice != "UNKNOWN":
            out += ["", "--- NOTICE（配布元の表記・Apache-2.0 §4(d)）---", notice]
        out += ["", THIN, ""]
    return out, len(entries), dict(counts)


# --------------------------------------------------------------------------

def header(rust_n: int, py_n: int) -> list[str]:
    return [
        RULE,
        "帳票OCRツール（chouhyo-ocr）— サードパーティ・ライセンス表記",
        RULE,
        "",
        "このファイルは、本ソフトウェアの配布物に含まれる第三者ソフトウェアの",
        "著作権表示と許諾条文をまとめたものです。それぞれのソフトウェアは、",
        "以下に記載した条件のもとで提供されています。",
        "",
        f"生成日: {date.today().isoformat()}",
        "生成方法: python scripts/gen_notices.py（手で編集しないこと）",
        f"対象: Windows 64bit（{TARGET}）向け配布物",
        "",
        f"内訳: Rust {rust_n} クレート / Python {py_n} パッケージ",
        "",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="サードパーティ表記を生成する")
    ap.add_argument("--offline", action="store_true",
                    help="cargo-about をネットワークなしで実行する"
                         "（取得済みのファイルだけを走査する）")
    ap.add_argument("--check", action="store_true",
                    help="生成せず、既存の THIRD-PARTY-NOTICES.txt が最新かだけ見る")
    args = ap.parse_args()

    rust_lines, rust_n, _ = render_rust(collect_rust(args.offline))
    py_entries = collect_python(shipped_python_packages())
    py_lines, py_n, _ = render_python(py_entries)

    body = "\n".join(header(rust_n, py_n) + rust_lines + py_lines).rstrip() + "\n"

    if args.check:
        if not OUT.exists():
            print(f"NG: {OUT.name} が無い")
            return 1
        # 生成日の行だけは毎回変わるので比較から外す。
        strip = lambda s: "\n".join(  # noqa: E731
            l for l in s.splitlines() if not l.startswith("生成日: "))
        if strip(OUT.read_text(encoding="utf-8")) != strip(body):
            print(f"NG: {OUT.name} が依存関係と食い違っている。"
                  "scripts/gen_notices.py を実行して更新する")
            return 1
        print(f"OK: {OUT.name} は最新")
        return 0

    # 改行は LF 固定。Git の追跡対象で、生成環境によって差分が出ると困る。
    OUT.write_text(body, encoding="utf-8", newline="\n")
    print(f"OK: {OUT} "
          f"（Rust {rust_n} クレート / Python {py_n} パッケージ / "
          f"{len(body.encode('utf-8')) // 1024} KB）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
