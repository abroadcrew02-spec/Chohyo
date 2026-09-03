# -*- coding: utf-8 -*-
"""性能 NFR の実測（要件 §6.1: 物理メモリ8GB で100枚連続を OOM・停止なく完走）。

保存済み Vision 応答の replay で N ページを実プロセスで処理し、
ピーク RSS・RSS の推移・所要時間・成果物サイズを計測する（API 課金ゼロ）。

計測は2部構成（issue #52 M-14）:
  1. PDF 展開（pdftoppm → PNG）— 1ページごとに必ず通る経路。**入力を .png に
     すると通らない**ため、replay 中心の計測では支配的コストを見落とす
  2. パイプライン全体 — replay で N ページを流し、RSS の推移を見る

既定（--only all）はこの2部だけを走らせる。ベースライン（100ページ 142.2s）の
意味を変えないため、以下の2つは opt-in にしてある:

  3. 枠検出 1 枚の所要時間（--only frames・issue #87 項目2・AC-F47/NFR-F02）
  4. run を繰り返したときの再レンダー時間の伸び（--only cumulative・issue #100）

実行:
  .venv/Scripts/python.exe scripts/perf_check.py                 # 1と2
  .venv/Scripts/python.exe scripts/perf_check.py --only expand   # 展開のみ
  .venv/Scripts/python.exe scripts/perf_check.py --pages 250     # 枚数を変える
  .venv/Scripts/python.exe scripts/perf_check.py --only frames   # 枠検出1枚
  .venv/Scripts/python.exe scripts/perf_check.py --only cumulative --runs 5 --pages 20
  .venv/Scripts/python.exe scripts/perf_check.py --snap on --pages 20  # 吸着 ON で計測

`--snap on|off`（既定 off）は計測用 config の `snap_blocks` を切り替える
（#75）。同じ計測を両モードで回して所要時間を比べるためのもので、パイプライン
計測と累積計測には summary の `snap_failsafe_pages`（入力画像由来で吸着を
見送ったページ数）と `snap_excluded_pages`（テンプレート定義由来で対象外に
したページ数）を並べて出す。
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "workdir_build" / "perf"
PAGE = ROOT / "testdata" / "local" / "pages" / "sample-1.png"
RESP = ROOT / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
TPL = ROOT / "templates" / "chouhyo-v1.json"
FORMB_PNG = ROOT / "testdata" / "formB" / "formB-1.png"
FORMB_TPL = ROOT / "testdata" / "formB" / "formB-v1.json"
FORMC_PNG = ROOT / "testdata" / "formC" / "formC-1.png"

# NFR-F02（07 §6）: 枠候補の一括生成はページ 1 枚あたり 3.0 秒以内
FRAMES_BUDGET_S = 3.0


def measure_expand() -> int:
    """PDF 展開の実測（issue #50 の最適化を守るための計測・M-14 (a)）。

    サンプル PDF は .gitignore 済みで環境によっては無い。その場合は skip する
    （計測できないことを黙って PASS にしない）。
    """
    import tempfile

    sys.path.insert(0, str(ROOT / "core"))
    from chouhyo_ocr.ingest import expand

    pdfs = sorted(ROOT.joinpath("samples").glob("*.pdf")) if (ROOT / "samples").is_dir() else []
    if not pdfs:
        print("展開計測: SKIP（samples/ に PDF が無い。.gitignore 済みのため環境依存）")
        return 0
    src = pdfs[0]
    dpi = json.loads(TPL.read_text(encoding="utf-8")).get("render_dpi", 300) if TPL.exists() else 300

    out = Path(tempfile.mkdtemp(prefix="perf_expand_"))
    t0 = time.perf_counter()
    pages = expand(src, dpi, out)
    elapsed = time.perf_counter() - t0
    total_mb = sum(p.stat().st_size for p in pages) / (1024 * 1024)
    shutil.rmtree(out, ignore_errors=True)

    print(f"展開計測: {len(pages)}ページ {elapsed:.2f}s "
          f"({elapsed/len(pages):.2f}s/ページ) 出力 {total_mb:.2f}MB "
          f"({total_mb/len(pages):.2f}MB/ページ) @{dpi}dpi")
    return 0


def measure_pipeline(N: int, snap: bool = False) -> int:
    # 前提の成果物が無いと base = PAGE.read_bytes() が素の FileNotFoundError で
    # 落ち、素材の無い環境で「壊れた」と誤読される（レビュー M-19）。何を用意すれば
    # よいかを先に言う
    missing = [p for p in (PAGE, RESP) if not p.exists()]
    if missing:
        print("性能計測に必要な素材がありません:", flush=True)
        for p in missing:
            print(f"  - {p}", flush=True)
        print("先に replay 用の 1 ページ分（サンプル画像と保存済み応答）を"
              "用意してから実行する。素材は testdata/local/ に置く（#88 以降 purge では消えない）",
              flush=True)
        return 2
    if BASE.exists():
        shutil.rmtree(BASE)
    inp = BASE / "input"; inp.mkdir(parents=True)
    resp = BASE / "resp"; resp.mkdir()
    base = PAGE.read_bytes()
    for i in range(1, N + 1):
        # 二重取り込み検知に食われないよう内容をユニーク化（IEND 後の1バイト）
        (inp / f"perf{i:03d}.png").write_bytes(base + bytes([i % 250 + 1, i // 250]))
        shutil.copy(RESP, resp / f"perf{i:03d}_p0001.json")
    cfg = BASE / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(BASE / "out"),
        "workdir": str(BASE / "wd"),
        "log_dir": str(BASE / "logs"),
        "snap_blocks": snap,
    }), encoding="utf-8")

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    t0 = time.time()
    # stdout は必ずファイルへ流す。PIPE を読まずに待つと 60 ページ規模で
    # パイプバッファが詰まり、コア側の print がブロックしてハングする（実測）
    log_out = open(BASE / "run.out", "wb")
    log_err = open(BASE / "run.err", "wb")
    proc = subprocess.Popen(
        [str(py), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(inp), "--replay", str(resp)],
        cwd=ROOT / "core", stdout=log_out, stderr=log_err)
    ps = psutil.Process(proc.pid)
    peak = 0
    samples: list[int] = []   # RSS の推移（リーク兆候の判定に使う・M-14 (b)）
    while proc.poll() is None:
        try:
            rss = ps.memory_info().rss
            for ch in ps.children(recursive=True):
                try:
                    rss += ch.memory_info().rss
                except psutil.Error:
                    pass
            peak = max(peak, rss)
            samples.append(rss)
        except psutil.Error:
            break
        time.sleep(0.25)
    proc.wait()
    log_out.close(); log_err.close()
    elapsed = time.time() - t0
    if proc.returncode != 0:
        print("NG: 終了コード", proc.returncode)
        print((BASE / "run.err").read_text("utf-8", "replace")[-800:])
        return 1
    # summary 行が無い（コアが途中で落ちた等）と next() が素の StopIteration で
    # 落ちる。何が起きたかを言ってから終わる（レビュー LOW）
    summary = next((json.loads(l) for l in
                    (BASE / "run.out").read_text("utf-8").splitlines()
                    if '"summary"' in l), None)
    if summary is None:
        print("NG: コアが summary を出していない。run.out / run.err を確認する")
        print((BASE / "run.err").read_text("utf-8", "replace")[-800:])
        return 1
    outs = sorted((BASE / "out").glob("*.xlsx"))
    if not outs:
        print("NG: 出力 xlsx が生成されていない")
        return 1
    xlsx = outs[-1]
    db = BASE / "wd" / "intermediate.sqlite"
    mb = 1024 * 1024
    print(f"pages={summary['pages']} rows={summary['rows']} "
          f"align_failed={summary['align_failed']}")
    print(_snap_line(snap, summary))
    print(f"elapsed={elapsed:.1f}s ({elapsed/N:.2f}s/枚)")
    print(f"peak_rss={peak/mb:.0f}MB  xlsx={xlsx.stat().st_size/mb:.1f}MB  "
          f"db={db.stat().st_size/mb:.1f}MB")

    # RSS の推移。先頭20%と末尾20%の平均を比べる。単調増加ならリークを疑う。
    # ピーク値だけでは「起動直後の一時ピーク」と「じわじわ増える漏れ」を
    # 区別できない（250ページで横ばいを実測済み・issue #52 M-14）
    leak_ok = True
    if len(samples) >= 10:
        k = max(1, len(samples) // 5)
        head = sum(samples[:k]) / k / mb
        tail = sum(samples[-k:]) / k / mb
        growth = (tail - head) / head * 100 if head else 0.0
        leak_ok = growth < 25.0
        print(f"rss_trend: head={head:.0f}MB tail={tail:.0f}MB "
              f"({growth:+.1f}%)  {'横ばい' if leak_ok else 'リークの疑い'}")
    else:
        print("rss_trend: サンプル不足（枚数を増やして再計測する）")

    ok = summary["rows"] == N and peak / mb < 2000 and leak_ok
    print("PASS: 8GB 環境に十分な余裕" if ok else "確認要")
    return 0 if ok else 1


def _run_core(cfg: Path, argv: list[str]) -> dict | None:
    """コアの CLI を1プロセス実行し、JSON Lines の最後のイベント行を返す。"""
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    proc = subprocess.run(
        [str(py), "-X", "utf8", "-m", "chouhyo_ocr.cli", "--config", str(cfg), *argv],
        cwd=ROOT / "core", capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    if proc.returncode != 0:
        print("NG: 終了コード", proc.returncode)
        print(proc.stderr[-800:])
        return None
    last = None
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                last = json.loads(line)
            except json.JSONDecodeError:
                pass
    return last


def _perf_config(name: str, snap: bool = False) -> Path:
    """計測用の使い捨て config（出力・作業・ログを workdir_build/perf 配下へ）。

    `snap` はブロック単位吸着（`snap_blocks`・#75）の ON/OFF。既定は core と
    同じ False で、`--snap on` のときだけ True を書く。
    """
    base = BASE / name
    if base.exists():
        shutil.rmtree(base)
    base.mkdir(parents=True)
    cfg = base / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(base / "out"),
        "workdir": str(base / "wd"),
        "log_dir": str(base / "logs"),
        "snap_blocks": snap,
    }), encoding="utf-8")
    return cfg


def _snap_line(snap: bool, summary: dict) -> str:
    """吸着の効き方を summary の 2 キーで1行に出す（#75 Unit C）。

    `snap_failsafe_pages` は入力画像由来（罫線のかすれ・吸着後の重なり）で
    フェイルセーフに落ちたページ数、`snap_excluded_pages` はテンプレート定義
    由来（行数が足りず合わせ先に使えない）で対象外にしたページ数。原因が
    違うので合算せず並べる。
    """
    return (f"snap={'on' if snap else 'off'}  "
            f"snap_failsafe_pages={summary.get('snap_failsafe_pages')}  "
            f"snap_excluded_pages={summary.get('snap_excluded_pages')}")


def _detect_only_ms(png: Path, tpl: "Path | None", repeat: int) -> list[int]:
    """`grid.detect_frames` 単体の所要（ms）。08 §4.7.1 と同じ測り方。

    CLI の `elapsed_ms` との差が、プロセス起動・設定読み・画像読み・ページ全体の
    Otsu にかかっているぶん。CLI（cmd_detect_frames）と同じ前処理を再現する。
    """
    sys.path.insert(0, str(ROOT / "core"))
    import numpy as np
    from PIL import Image

    from chouhyo_ocr.align import _otsu
    from chouhyo_ocr.grid import detect_frames
    from chouhyo_ocr.template import Rect, load_template

    template = load_template(tpl) if tpl is not None else None
    dpi = template.render_dpi if template is not None else 300
    with Image.open(png) as img:
        img.load()
        gray = np.asarray(img.convert("L"))
        size = [img.width, img.height]
    binary = gray < _otsu(gray, np.zeros(gray.shape, dtype=bool))

    exclusions: list = []
    effective = None
    if template is not None and size == list(template.image_size):
        effective = template
        for f in template.faces:
            r = f.source_rect
            exclusions += [Rect(r.x + ex.x, r.y + ex.y, ex.w, ex.h) for ex in f.exclusions]

    out = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        detect_frames(binary, dpi, exclusions=exclusions, existing=effective)
        out.append(int((time.perf_counter() - t0) * 1000))
    return out


def measure_frames(repeat: int = 3, snap: bool = False) -> int:
    """枠候補の一括生成（detect-frames）1 枚の所要時間（issue #87 項目2）。

    受入基準 AC-F47・NFR-F02（ページ 1 枚 3.0 秒）の実測。素材は
    sample-1（実サンプル・出荷テンプレート付き）・formB-1・formC-1 の3種を
    それぞれ repeat 回。あわせて打ち切り案内（zero_reason="too_many_lines"）が
    出る条件を、レール数の上限を超える合成画像で1回だけ確認する。

    2つの尺度を並べる。GUI が待たされるのは前者:

    - `elapsed_ms`: CLI 1 プロセスぶん（起動・設定読み・画像展開・ページ全体の
      Otsu・検出）。GUI の待ち時間はこれに Rust の呼び出し分が乗る
    - `detect_ms`: `grid.detect_frames` 単体（08 §4.7.1 の測り方と同じ。
      二値画像を作るところまでは計測外）
    """
    cases = [
        ("sample-1 + 出荷テンプレ", PAGE, TPL),
        ("formB-1 + formB テンプレ", FORMB_PNG, FORMB_TPL),
        ("formC-1 (テンプレなし)", FORMC_PNG, None),
    ]
    missing = [str(png) for _n, png, _t in cases if not png.exists()]
    if missing:
        print("枠検出計測: SKIP（素材が無い）:")
        for m in missing:
            print("  -", m)
        print("  formC は testdata/formC/make_formC.py で生成する")
        return 0

    cfg = _perf_config("frames", snap)
    # detect-frames は吸着を通らない（run のパイプラインだけが通る）。config には
    # 同じ値を書いて条件を揃えるが、summary の 2 キーは出ない
    print(f"枠検出計測（AC-F47・NFR-F02 予算 {FRAMES_BUDGET_S:.1f}s・各{repeat}回・"
          f"snap={'on' if snap else 'off'}／detect-frames は吸着を通らない）")
    print(f"  {'素材':24s} {'elapsed_ms(CLI)':>20s} {'detect_ms(単体)':>18s} "
          f"{'rails h/v':>10s} {'候補':>5s}  zero_reason")
    worst = 0
    for label, png, tpl in cases:
        argv = ["detect-frames", "--input", str(png)]
        if tpl is not None:
            argv += ["--template", str(tpl)]
        times, last = [], None
        for _ in range(repeat):
            ev = _run_core(cfg, argv)
            if ev is None or not ev.get("ok"):
                print(f"  {label}: NG {ev}")
                return 1
            times.append(ev["elapsed_ms"])
            last = ev
        st = last.get("stats", {})
        worst = max(worst, max(times))
        inner = _detect_only_ms(png, tpl, repeat)
        cell = "/".join(str(t) for t in times)
        rails = f"{st.get('rails_h', 0)}/{st.get('rails_v', 0)}"
        print(f"  {label:24s} {cell:>20s} "
              f"{'/'.join(str(t) for t in inner):>18s} {rails:>10s} "
              f"{len(last.get('candidates', [])):5d}  {last.get('zero_reason')}")

    # 打ち切り案内の発火条件（grid.MAX_RAILS を1軸で超える合成画像）
    sys.path.insert(0, str(ROOT / "core"))
    import numpy as np
    from PIL import Image

    from chouhyo_ocr.grid import MAX_RAILS
    n = MAX_RAILS + 5
    dense = np.full((n * 8 + 8, 1200), 255, dtype=np.uint8)
    for i in range(n):
        # 横罫線を上限より多く引く。厚み方向に濃淡を付けるのは、真っ黒と
        # 真っ白の2値だけの画像だと Otsu の閾値が 0 になり（align._otsu は
        # 「閾値以下が暗いクラス」を返すのに呼び出し側は gray < th で切る）
        # インクが1画素も残らないため
        dense[i * 8, 50:1150] = 0
        dense[i * 8 + 1, 50:1150] = 40
    dense_png = BASE / "frames" / "dense.png"
    Image.fromarray(dense).save(dense_png)
    ev = _run_core(cfg, ["detect-frames", "--input", str(dense_png)]) or {}
    st = ev.get("stats", {})
    print(f"  打ち切り確認: MAX_RAILS={MAX_RAILS}（軸ごと）に対し "
          f"rails_h={st.get('rails_h')} rails_v={st.get('rails_v')} "
          f"→ zero_reason={ev.get('zero_reason')}")

    ok = worst <= FRAMES_BUDGET_S * 1000
    print(f"  最大 {worst} ms / 予算 {int(FRAMES_BUDGET_S * 1000)} ms: "
          + ("PASS" if ok else "予算超過（NFR-F02 に抵触）"))
    return 0 if ok else 1


def _report_cumulative(rows: list) -> None:
    """累積計測の要約（最小二乗の傾きと外挿）。rows は (run, done, render_s, wall)。"""
    if len(rows) < 2:
        print("  （run が1回ぶんしか取れていないので傾きは出せない）")
        return
    xs = [done for _r, done, _rs, _w in rows]
    ys = [rs * 1000 for _r, _d, rs, _w in rows]
    if len(set(xs)) < 2:
        return
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var
    intercept = my - slope * mx
    # 1頁あたりの値をそのまま比べると、件数に依らない固定費（切片）が
    # 小さい run ほど大きく見えて紛れる。傾きで見る
    print(f"  最小二乗: render_ms ≒ {slope:.1f} x 累積頁数 + {intercept:.0f}")
    for target in (1000, 5000):
        print(f"    → 累積 {target} ページのときの render 見込み: "
              f"{(slope * target + intercept) / 1000:.0f} 秒/回")
    share = [rs / w * 100 for _r, _d, rs, w in rows if w]
    if share:
        print(f"  render が run 全体に占める割合: {min(share):.0f}〜{max(share):.0f}%")
    print("  ※ render は毎回 Store の全 done ページを作り直す（issue #100）。"
          "件数に比例して伸びるのが期待どおりの挙動。傾きが 0 に近ければ差分出力の"
          "必要は薄く、大きければ #100 の着手根拠になる")
    print("  ※ この規模では render は run 全体のごく一部で、実行環境の負荷ゆらぎの"
          "ほうが大きい。判断に使うなら --pages を増やして測り直す")


def measure_cumulative(runs: int, pages_per_run: int, snap: bool = False) -> int:
    """run を繰り返したときの再レンダー時間の伸び（issue #100）。

    run() は毎回 Store 内の全 done ページを対象に出力を作り直す。同じ workdir へ
    pages_per_run ページずつ追加しながら runs 回実行し、run ごとの
    `render_seconds`（summary が既に返している値）と累積 done 頁数を並べる。
    差分レンダーは実装しない（issue #100 は「実運用を見てから」）——ここで
    測るのは伸び方の形だけ。
    """
    missing = [p for p in (PAGE, RESP) if not p.exists()]
    if missing:
        print("累積計測に必要な素材がありません:")
        for p in missing:
            print(f"  - {p}")
        return 2

    cfg = _perf_config("cumulative", snap)
    base = PAGE.read_bytes()
    print(f"累積計測（{runs} 回 x {pages_per_run} ページ・同一 workdir へ追加・"
          f"snap={'on' if snap else 'off'}）")
    print(f"  {'run':>3s} {'追加':>5s} {'累積done':>9s} {'render_s':>9s} "
          f"{'run全体_s':>10s} {'render_ms/頁':>13s} {'snap_fs':>8s} {'snap_ex':>8s}")
    rows = []
    seq = 0
    for r in range(1, runs + 1):
        inp = BASE / "cumulative" / f"in{r:02d}"
        resp = BASE / "cumulative" / f"resp{r:02d}"
        inp.mkdir(parents=True)
        resp.mkdir(parents=True)
        for _ in range(pages_per_run):
            seq += 1
            name = f"cum{seq:04d}"
            # 二重取り込み検知に食われないよう内容をユニーク化（IEND 後の1バイト）
            (inp / f"{name}.png").write_bytes(
                base + bytes([seq % 250 + 1, (seq // 250) % 250]))
            shutil.copy(RESP, resp / f"{name}_p0001.json")
        t0 = time.perf_counter()
        ev = _run_core(cfg, ["run", "--input", str(inp), "--replay", str(resp)])
        wall = time.perf_counter() - t0
        if ev is None or ev.get("event") != "summary":
            # 途中の run が落ちても、そこまでの表と傾きは出す（計測できた分を
            # 捨てない）。戻り値では失敗を伝える
            print(f"  run {r}: NG（summary が出ていない）: {ev}")
            _report_cumulative(rows)
            return 1
        # total_done_pages = store に蓄積された state=="done" の累積件数
        # （pipeline.py の summary が既に返している。P-H1 可視化）
        done = ev.get("total_done_pages") or seq
        rs = ev.get("render_seconds", 0.0)
        rows.append((r, done, rs, wall))
        # snap_failsafe_pages（入力画像由来）と snap_excluded_pages（テンプレート
        # 定義由来）は原因が違うので合算せず別々の列に出す
        print(f"  {r:3d} {pages_per_run:5d} {done:9d} {rs:9.1f} {wall:10.1f} "
              f"{(rs * 1000 / done if done else 0):13.1f} "
              f"{ev.get('snap_failsafe_pages', 0):8d} "
              f"{ev.get('snap_excluded_pages', 0):8d}")

    _report_cumulative(rows)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="性能 NFR の実測")
    ap.add_argument("--pages", type=int, default=100,
                    help="replay で流す枚数（--only cumulative では 1 run あたりの追加枚数）")
    ap.add_argument("--only", default="all",
                    choices=["all", "expand", "pipeline", "frames", "cumulative"],
                    help="all は展開＋パイプライン（従来どおり）。frames と cumulative は opt-in")
    ap.add_argument("--runs", type=int, default=5,
                    help="--only cumulative のときの run 回数（issue #100）")
    ap.add_argument("--repeat", type=int, default=3,
                    help="--only frames のときの素材あたり反復回数")
    ap.add_argument("--snap", default="off", choices=["on", "off"],
                    help="ブロック単位吸着（config の snap_blocks・#75）。"
                         "既定 off は core の既定と同じ。on/off を切り替えて"
                         "同じ計測を両モードで回し、所要時間と "
                         "snap_failsafe_pages / snap_excluded_pages を比べる")
    a = ap.parse_args()
    snap = a.snap == "on"
    rc = 0
    if a.only in ("all", "expand"):
        rc |= measure_expand()
    if a.only in ("all", "pipeline"):
        rc |= measure_pipeline(a.pages, snap)
    if a.only == "frames":
        rc |= measure_frames(a.repeat, snap)
    if a.only == "cumulative":
        rc |= measure_cumulative(a.runs, a.pages, snap)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
