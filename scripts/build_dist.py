"""配布物ビルド（M7）: Python コアを PyInstaller onedir 化し、
schema/templates/Poppler を同梱レイアウトへ複製する。

出力: <repo>/core-dist/chouhyo-core/
  chouhyo-core.exe        コア CLI（GUI が優先して起動・§lib.rs core_command）
  schema/  templates/     frozen 時の app_root() 解決先（paths.py）
  poppler/pdftoppm.exe 他 frozen 時の pdftoppm_path() 解決先（ingest.py）

実行: .venv/Scripts/python.exe scripts/build_dist.py
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "core-dist"
APP = DIST / "chouhyo-core"


def main() -> int:
    entry = ROOT / "workdir_build" / "core_entry.py"
    entry.parent.mkdir(exist_ok=True)
    entry.write_text(
        "import sys\nfrom chouhyo_ocr.cli import main\nsys.exit(main())\n",
        encoding="utf-8")

    pyinstaller = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    cmd = [str(pyinstaller), "--noconfirm", "--onedir", "--console",
           "--name", "chouhyo-core",
           "--distpath", str(DIST),
           "--workpath", str(ROOT / "workdir_build" / "pyi"),
           "--specpath", str(ROOT / "workdir_build"),
           "--paths", str(ROOT / "core"),
           str(entry)]
    print("+", " ".join(cmd), flush=True)
    r = subprocess.run(cmd, cwd=ROOT / "core")
    if r.returncode != 0:
        return r.returncode

    # 同梱リソース（frozen の app_root=exe ディレクトリに合わせる）
    for name in ("schema", "templates"):
        dst = APP / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(ROOT / name, dst)

    poppler_bins = sorted((ROOT / "vendor" / "poppler").glob("**/Library/bin"))
    if not poppler_bins:
        print("NG: vendor/poppler が見つからない", file=sys.stderr)
        return 1
    dst = APP / "poppler"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(poppler_bins[0], dst)

    # 疎通: 同梱 exe で verify（資格情報は環境依存のため結果コードは見ない）
    print("+ smoke: chouhyo-core.exe verify", flush=True)
    subprocess.run([str(APP / "chouhyo-core.exe"), "verify"], cwd=ROOT / "core")
    print(f"OK: {APP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
