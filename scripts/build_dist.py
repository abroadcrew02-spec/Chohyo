"""配布物ビルド（M7）: Python コアを PyInstaller onedir 化し、
schema/templates/Poppler を同梱レイアウトへ複製する。

出力: <repo>/core-dist/chouhyo-core/
  chouhyo-core.exe        コア CLI（GUI が優先して起動・§lib.rs core_command）
  schema/                 frozen 時の app_root() 解決先（paths.py）
  templates/chouhyo-v1.json  出荷テンプレートのみ複製（issue #59 H-7）。
                          templates/ ディレクトリ丸ごとの複製はエディタの
                          下書き・実験ファイルまで配布物へ混入させるため、
                          出荷テンプレート1ファイルへ絞る
  poppler/pdftoppm.exe 他 frozen 時の pdftoppm_path() 解決先（ingest.py）

実行: .venv/Scripts/python.exe scripts/build_dist.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

# python -m scripts.build_dist や PYTHONSAFEPATH=1 で実行すると、このスクリプトの
# 置き場所（scripts/）が sys.path に自動で入らず兄弟モジュール import が壊れるため
# 明示しておく
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dist_stamp  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "core-dist"
APP = DIST / "chouhyo-core"


def main() -> int:
    # 同梱 exe が古いまま放置される事故（2026-09-02）の再発防止: ビルド開始時に
    # 既存のスタンプを消しておく。この後失敗して return したときに古いスタンプが
    # 残って「鮮度検査 PASS」と誤判定されるのを防ぐ
    dist_stamp.stamp_path(APP).unlink(missing_ok=True)

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
    dst = APP / "schema"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(ROOT / "schema", dst)

    # templates/ は出荷テンプレート（chouhyo-v1.json）のみを複製する。
    # ディレクトリ丸ごとの copytree だと、エディタの保存既定が templates/ 直下
    # のため利用者の下書き・実験ファイルが同じ場所に溜まり、そのまま配布物へ
    # 混入する（issue #59 H-7・実測: 未追跡の実験ファイル9件が複製されていた）
    dst = APP / "templates"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    shutil.copy2(ROOT / "templates" / "chouhyo-v1.json", dst / "chouhyo-v1.json")

    poppler_bins = sorted((ROOT / "vendor" / "poppler").glob("**/Library/bin"))
    if not poppler_bins:
        print("NG: vendor/poppler が見つからない", file=sys.stderr)
        return 1
    dst = APP / "poppler"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(poppler_bins[0], dst)

    # 疎通: 同梱 exe で verify。終了コードは見ない——資格情報の有無で 1 になり、
    # ビルドの成否とは無関係だから。見るのは「exe が起動して verify を出したか」
    # （レビュー M-18: 戻り値も出力も捨てていたので、DLL 欠落で起動できなくても
    # OK と表示していた）
    # --template を明示する（issue #65-4）: 配布版を --template なしで直叩きすると、
    # config.json の last_template が利用者テンプレート（user:<名前>）のとき
    # 「画面から起動したときにだけ保存先が分かる」として ConfigError で止まる。
    # ここで見たいのは exe の起動と verify の出力であって、開発機の直前の
    # テンプレート選択ではないので、出荷テンプレートを固定で渡す
    print("+ smoke: chouhyo-core.exe verify", flush=True)
    try:
        r = subprocess.run([str(APP / "chouhyo-core.exe"), "verify",
                            "--template", str(ROOT / "templates" / "chouhyo-v1.json")],
                           cwd=ROOT / "core", capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"NG: 同梱 exe を起動できない（{type(e).__name__}）", flush=True)
        return 1
    print(r.stdout, end="", flush=True)
    checks = set()
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if ev.get("event") == "verify":
            checks.add(ev.get("check"))
    required = {"template", "poppler", "local_storage"}
    if not required <= checks:
        print(f"NG: verify の出力が足りない（不足: "
              f"{sorted(required - checks)}）。exe が起動していない可能性",
              flush=True)
        if r.stderr.strip():
            print(r.stderr.strip()[:800], flush=True)
        return 1

    # smoke が通ったここで初めてスタンプを書く（鮮度検査の基準点）
    dist_stamp.write_stamp(ROOT, APP)
    print(f"OK: {APP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
