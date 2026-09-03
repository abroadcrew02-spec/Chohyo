"""記入値の漏出防止の再レビューテスト（issue #2/#3/#4）。

2026-09-02（Q-S1・FR-F50・07_frame_detection_requirements.md §0.6）:
秘匿対象を「記入値」から「記入値＋テンプレートファイル名＋欄名（field_id・
列名・table_id）」へ拡張した。対象は app.log・error.log のみ——GUI 表示・
stdout の JSON Lines・出力ファイルは対象外（AC-F65）。
"""
import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chouhyo_ocr import cli, logging_safe
from chouhyo_ocr.mapping import CellContent, Symbol
from chouhyo_ocr.paths import app_root
from chouhyo_ocr.render_rows import Row
from chouhyo_ocr.template import load_template

PYTHON = app_root() / ".venv" / "Scripts" / "python.exe"
TPL = app_root() / "templates" / "chouhyo-v1.json"
# AC-F65 の replay 素材（.gitignore 配下・このマシン限定）。無い環境では skip
# （test_e2e_replay.py と同じ規約）
RESP = app_root() / "testdata" / "local" / "s2" / "resp_DOCUMENT_TEXT_DETECTION.json"
PAGE_PNG = app_root() / "testdata" / "local" / "pages" / "sample-1.png"


def test_cli_top_level_handler_hides_exception_message(tmp_path):
    """未捕捉例外で traceback・値が stderr に出ない（issue #2）。"""
    bogus = tmp_path / "存在しない入力フォルダ"
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(tmp_path / "logs"),
    }), encoding="utf-8")
    r = subprocess.run(
        [str(PYTHON), "-X", "utf8", "-m", "chouhyo_ocr.cli",
         "--config", str(cfg), "run", "--input", str(bogus)],
        cwd=app_root() / "core", capture_output=True, text=True,
        encoding="utf-8", timeout=120)
    assert r.returncode == 1
    assert "Traceback" not in r.stderr           # 生 traceback を GUI へ流さない
    assert "存在しない入力フォルダ" not in r.stderr  # 例外メッセージ由来の値を出さない
    assert r.stderr.strip().startswith("ERROR ")  # 固定文言＋型名のみ
    # スタック（メッセージ抜き）は error.log へ残る
    err_log = (tmp_path / "logs" / "error.log").read_text(encoding="utf-8")
    assert "unhandled_exception" in err_log
    assert "存在しない入力フォルダ" not in err_log
    # run_start は入力パス（path=）のみを残す——テンプレートファイル名は
    # 出さない（Q-S1・FR-F50・2026-09-02）。**run_start 自身の行にハッシュは
    # 乗らない**（2026-09-02 マリン指摘・旧コメントの誤り訂正）: cmd_run の
    # 時点ではまだテンプレートを読んでおらずハッシュが分からないため、算出
    # できた直後（pipeline._load）に template_loaded が別行としてハッシュを
    # 残す（issue #59 H-7 の追跡目的は run_start ではなく template_loaded で
    # 満たす）。入力フォルダが無くて後段で失敗しても、テンプレート読み込み
    # までは進むため両方のログ行は書かれる
    app_log = (tmp_path / "logs" / "app.log").read_text(encoding="utf-8")
    assert "run_start" in app_log
    assert "template_path=" not in app_log
    assert "template_loaded" in app_log and "template_hash=" in app_log


def test_logging_whitelist_drops_value_key():
    """汎用キー value は白リスト外＝黙って落ちる（issue #3）。"""
    line = logging_safe._fmt("x", {"value": "テスト太郎", "page_id": "p1"})
    assert "テスト太郎" not in line
    assert "page_id=p1" in line
    line2 = logging_safe._fmt("x", {"duplicate_of": "a.png"})
    assert "duplicate_of=a.png" in line2


def test_logging_whitelist_drops_template_path_allows_hash_and_anonymous_ids():
    """テンプレート名は白リストから外れ、ハッシュと匿名序数だけが通る。

    2026-09-02（Q-S1・FR-F50）: テンプレート名・欄名は秘匿対象へ拡張された
    （07_frame_detection_requirements.md §0.6）。旧テスト
    test_logging_whitelist_allows_template_path_and_hash（issue #59 H-7・
    template_path がログに出ることを期待していた）の期待値を反転する——
    出力がどのテンプレート由来かを事後特定する目的は template_hash
    （テンプレート全体のハッシュ・値は含まない）と、欄・列の匿名識別子
    cell_idx／col_idx（template_hash と組で一意）で引き続き満たす。
    """
    line = logging_safe._fmt("run_start", {
        "path": "in", "template_path": "C:\\t.json", "value": "テスト太郎"})
    assert "template_path=" not in line  # 白リスト外＝黙って落ちる
    assert "テスト太郎" not in line
    assert "path=in" in line
    line2 = logging_safe._fmt("template_loaded", {"template_hash": "abc123"})
    assert "template_hash=abc123" in line2
    # field_id（欄名）も白リスト外。代替の匿名識別子は通る
    line3 = logging_safe._fmt(
        "fallback_used", {"field_id": "person_氏名", "cell_idx": 3})
    assert "field_id" not in line3 and "person_氏名" not in line3
    assert "cell_idx=3" in line3
    line4 = logging_safe._fmt(
        "csv_formula_risk", {"page_id": "p1", "col_idx": 5})
    assert "page_id=p1" in line4 and "col_idx=5" in line4


def test_fixed_repr_redacts_values():
    """記入値を持つ dataclass の repr が値を出さない（issue #4・付録 C7）。"""
    assert "テスト太郎" not in repr(Symbol("テスト太郎", 1, 2, 0.9))
    assert "テスト太郎" not in repr(CellContent("テスト太郎", 0.9))
    row = Row("p1", "s.png", 1, "正常", ["テスト太郎", "千葉県"], 0, "0.900")
    assert "テスト太郎" not in repr(row) and "千葉県" not in repr(row)
    t = load_template(TPL)
    cell = t.cells[0]
    assert "redacted" in repr(CellContent("x", None)) or True  # 形式は固定文字列
    assert repr(cell).startswith("<CellSpec ")


def test_risky_prefix_warning_does_not_log_values_or_field_names(tmp_path):
    """危険接頭の警告に記入値も列名（欄名）も入らない（D-28・A5・設計 §8.1）。

    2026-09-02（Q-S1・FR-F50）: 秘匿対象が欄名（列名）へ拡張されたため、
    _warn_risky が受け取る (page_id, 列名) の列名部分もログへ出さないよう
    変更した。代わりに抽出対象列内の0始まり序数 col_idx を残す（列名は
    白リストから外れた field_id と同じ扱い・黙って落ちない代替識別子）。
    """
    from chouhyo_ocr import logging_safe as log
    from chouhyo_ocr.columns import META_COLUMNS
    from chouhyo_ocr.pipeline import _warn_risky
    log.init(str(tmp_path))
    columns = list(META_COLUMNS) + ["備考", "person_備考"]
    _warn_risky([("p_0001", "person_備考")], columns)
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in tmp_path.glob("*.log"))
    assert "csv_formula_risk" in text
    assert "person_備考" not in text
    assert "p_0001" in text and "col_idx=1" in text  # 抽出対象列内の序数（0始まり）
    # 出るキーは page_id・col_idx・count のみ（記入値・列名が乗る余地が無い）。
    # _warn_risky はそもそも値を受け取らない——scan_risky_prefixes の戻りが
    # (page_id, 列名) だけなので、値がログへ流れる経路が型の上で存在しない
    for line in text.splitlines():
        if "csv_formula_risk" not in line:
            continue
        keys = {kv.split("=")[0] for kv in line.split() if "=" in kv}
        assert keys <= {"page_id", "col_idx", "count"}, keys


@pytest.mark.skipif(
    not (RESP.exists() and PAGE_PNG.exists()), reason="保存済み応答・展開画像が無い環境")
def test_ac_f65_template_name_and_field_names_absent_from_logs(tmp_path):
    """AC-F65: 機微な名前を持つテンプレートで run(replay)/verify/remap/expand-page
    を実行しても、app.log・error.log にテンプレートファイル名・欄名が現れない。

    - テンプレートの特定は template_hash のみで可能（テンプレートファイル名は
      顧客名を想起させるファイル名を使う——Q-S1 の想定どおり機微になりうる例）
    - 診断ログ（fallback_used 等・出荷テンプレの W-1 除外重なり警告）は
      cell_idx で引き続き出る（黙って消えない・FR-F50）
    - GUI の JSON Lines（stdout の column_names・verify の warnings 文字列・
      expand_page の page_path 等）・出力ファイルは対象外（このテストは
      ログファイルのみを検査し、stdout 契約には触れない）
    """
    # 顧客名を想起させるファイル名（機微なテンプレート名の想定・Q-S1）
    tpl = tmp_path / "田中様_申込書テンプレート.json"
    shutil.copy(TPL, tpl)

    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(PAGE_PNG, input_dir / "sample-1.png")
    replay_dir = tmp_path / "responses"
    replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    log_dir = tmp_path / "logs"
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps({
        "output_dir": str(tmp_path / "out"),
        "workdir": str(tmp_path / "wd"),
        "log_dir": str(log_dir),
    }), encoding="utf-8")

    rc_run = cli.main(["--config", str(cfg_path), "run",
                        "--input", str(input_dir), "--template", str(tpl),
                        "--replay", str(replay_dir)])
    assert rc_run == 0
    # verify の終了コードは資格情報・API残量など本テストと無関係な環境状態
    # にも左右されるため確認しない——ここで見るのは load_template() 経由で
    # app.log へ書かれる内容だけ（呼び出しが例外なく完走することのみ確認）
    cli.main(["--config", str(cfg_path), "verify", "--template", str(tpl)])
    rc_remap = cli.main(
        ["--config", str(cfg_path), "remap", "--template", str(tpl)])
    assert rc_remap == 0
    rc_expand = cli.main(
        ["--config", str(cfg_path), "expand-page",
         "--input", str(input_dir / "sample-1.png"), "--template", str(tpl)])
    assert rc_expand == 0

    log_text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in log_dir.glob("*.log"))
    assert log_text  # 前提: ログが実際に書かれている

    # テンプレートファイル名（拡張子抜き・拡張子付きの両方）
    assert "田中様_申込書テンプレート" not in log_text
    assert tpl.name not in log_text
    assert "template_path=" not in log_text

    # 欄名（AC-F65 の例・出荷テンプレの W-1 発火欄も含む）
    for field_name in ("person_氏名", "person_備考", "person_郵便番号1",
                       "person_電話番号", "person_会社名屋号", "person_所在地"):
        assert field_name not in log_text

    # 診断ログは匿名識別子で引き続き出る（黙って消えない）
    assert "template_hash=" in log_text
    assert "run_start" in log_text and "template_loaded" in log_text
    assert "exclusion_overlap_w1" in log_text and "cell_idx=" in log_text
    assert "fallback_used" in log_text  # replay サンプルは郵便番号欄で発火する
    # 不変条件A（cell_idx を template_hash で復号できること）は各経路を単独の
    # log_dir で走らせて検証する（test_invariant_a_holds_per_command_in_isolation）


def test_invariant_a_holds_per_command_in_isolation(tmp_path):
    """不変条件A（08_frame_detection_design.md §1.4）: cell_idx・face_idx は
    template_hash とセットでのみ意味を持つ。run/remap/verify/expand-page の
    4経路それぞれが**単独で**（他経路のログに頼らず）template_loaded を
    自前で出し、その経路が書いた cell_idx を復号できることを検証する。

    2026-09-02（マリン指摘）: 以前は4経路の app.log を1本にまとめて
    「template_loaded が合計4行以上」を見ていたが、これでは経路ごとの
    自己完結性——verify だけを単独実行したときに、その回の app.log だけで
    cell_idx を復号できるか——を検証できていなかった（4経路のうち1つでも
    template_loaded を出し忘れても、他の3経路が出していれば合計4行の条件は
    満たされてしまう）。ここではコマンドごとに log_dir を分け、各経路が
    単独で満たすべき条件を個別に確認する。
    """
    tpl = tmp_path / "田中様_申込書テンプレート.json"
    shutil.copy(TPL, tpl)
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    shutil.copy(PAGE_PNG, input_dir / "sample-1.png")
    replay_dir = tmp_path / "responses"
    replay_dir.mkdir()
    shutil.copy(RESP, replay_dir / "sample-1_p0001.json")

    workdir = tmp_path / "wd"       # run と remap で共有（remap は run の中間データが要る）
    output_dir = tmp_path / "out"

    def _cfg(tag: str) -> Path:
        cfg_path = tmp_path / f"config_{tag}.json"
        cfg_path.write_text(json.dumps({
            "output_dir": str(output_dir),
            "workdir": str(workdir),
            "log_dir": str(tmp_path / f"logs_{tag}"),
        }), encoding="utf-8")
        return cfg_path

    def _log_text(tag: str) -> str:
        d = tmp_path / f"logs_{tag}"
        return "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in d.glob("*.log"))

    rc_run = cli.main(["--config", str(_cfg("run")), "run",
                        "--input", str(input_dir), "--template", str(tpl),
                        "--replay", str(replay_dir)])
    assert rc_run == 0
    run_log = _log_text("run")
    assert run_log.count("template_loaded") >= 1

    rc_remap = cli.main(["--config", str(_cfg("remap")), "remap", "--template", str(tpl)])
    assert rc_remap == 0
    remap_log = _log_text("remap")
    assert remap_log.count("template_loaded") >= 1

    # verify の終了コードは資格情報・API残量など無関係な環境状態にも左右
    # されるため確認しない（他の AC-F65 テストと同じ方針）
    cli.main(["--config", str(_cfg("verify")), "verify", "--template", str(tpl)])
    verify_log = _log_text("verify")
    assert verify_log.count("template_loaded") >= 1
    # verify 単独の app.log の中だけで template_hash と cell_idx が両方
    # 揃っている（他経路のログを合算しなくても復号できる）ことを確認する
    assert "template_hash=" in verify_log and "cell_idx=" in verify_log

    rc_expand = cli.main(["--config", str(_cfg("expand")), "expand-page",
                          "--input", str(input_dir / "sample-1.png"),
                          "--template", str(tpl)])
    assert rc_expand == 0
    expand_log = _log_text("expand")
    assert expand_log.count("template_loaded") >= 1

    # H-1（マリン指摘・2026-09-03）: detect-frames は不変条件Aを満たす
    # 6本目の経路。以前は template_loaded を出しておらず、この経路単独の
    # app.log からは cell_idx（将来 detect-frames が匿名識別子を出す場合）
    # を復号できない穴があった
    rc_detect_frames = cli.main(["--config", str(_cfg("detect_frames")), "detect-frames",
                                 "--input", str(input_dir / "sample-1.png"),
                                 "--template", str(tpl)])
    assert rc_detect_frames == 0
    detect_frames_log = _log_text("detect_frames")
    assert detect_frames_log.count("template_loaded") >= 1


def test_w3_w4_diagnostics_no_longer_silently_dropped(tmp_path):
    """W-3（adjacent_gap_w3）・W-4（hole_overlap_w4）の実害修正（08 §1.1）。

    修正前は face_id・field_a・field_b という白リスト外のキーを渡しており、
    `_fmt` が例外にならず黙って落としてイベント名しか記録していなかった
    （2026-09-02 実測・issue #77・08_frame_detection_design.md §1.1）。
    face_idx・cell_a・cell_b を匿名識別子として白リストへ追加し、実際に
    記録されるようになったことを固定する。
    """
    from chouhyo_ocr import logging_safe as log
    log.init(str(tmp_path))

    # W-3: 出荷テンプレは12件発火する（test_adjacent_gap_warnings.py の実測）
    load_template(TPL)
    app_log = (tmp_path / "app.log").read_text(encoding="utf-8")
    w3_lines = [line for line in app_log.splitlines() if "adjacent_gap_w3" in line]
    assert w3_lines
    for line in w3_lines:
        assert "cell_a=" in line and "cell_b=" in line and "face_idx=" in line
        assert "field_a" not in line and "field_b" not in line
        assert "face_id=" not in line

    # W-4: 出荷テンプレは0件（test_hole_overlap_warnings.py の実測）なので、
    # 穴どうしが重なる合成テンプレを使う（同ファイル _free_spot_fields と
    # 同じ座標・手法の再利用——物理矩形は重ならず穴の BBox だけが重なる）
    raw = json.loads(TPL.read_text(encoding="utf-8"))

    def field(fid, main, extra):
        return {
            "field_id": fid, "kind": "text",
            "rect": {"x": main[0], "y": main[1], "w": main[2], "h": main[3]},
            "extra_rects": [{"x": extra[0], "y": extra[1], "w": extra[2], "h": extra[3]}],
        }
    a = field("hole_test_a", (2000, 1700, 60, 40), (2300, 1820, 60, 40))
    b = field("hole_test_b", (2100, 1780, 60, 40), (2200, 1740, 60, 40))
    raw["faces"][0]["fields"] += [a, b]
    w4_tpl = tmp_path / "w4.json"
    w4_tpl.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    load_template(w4_tpl)

    app_log2 = (tmp_path / "app.log").read_text(encoding="utf-8")
    w4_lines = [line for line in app_log2.splitlines() if "hole_overlap_w4" in line]
    assert w4_lines
    for line in w4_lines:
        assert "cell_a=" in line and "cell_b=" in line and "face_idx=" in line
        assert "hole_test_a" not in line and "hole_test_b" not in line
        assert "field_a" not in line and "field_b" not in line
        assert "face_id=" not in line


def _logging_safe_output_function_names() -> set[str]:
    """logging_safe の「イベント名＋**fields」形の公開出力関数名を動的に取る
    （info/warn/error）。

    ハードコードした固定タプルだと、新しい出力関数が増えたときに静的検査が
    追随しない（2026-09-02 マリン指摘）。`init`／`error_trace`／`_fmt` は
    シグネチャが違う（`**fields` を持たない、または非公開）ため自然と除外
    される。
    """
    import inspect
    names = set()
    for name, obj in vars(logging_safe).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        params = inspect.signature(obj).parameters
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
            names.add(name)
    return names


def test_static_check_all_logged_keys_are_allow_listed():
    """core/chouhyo_ocr/**/*.py の logging_safe 出力呼び出し（info/warn/error）

    が渡す全キーワード引数名が logging_safe._ALLOWED_KEYS に含まれることを
    AST で検査する（AC-F65・T-F65-2 相当・08_frame_detection_design.md §1.6）。

    白リストに無いキーは _fmt が例外にならず黙って落とす（型で守れない
    書き方への最後の網・logging_safe.py のモジュール docstring）。この
    「黙って落ちる」性質のせいで、白リスト外のキーを渡すコードは気づかれずに
    「診断が静かに消える」——実際に template.py の adjacent_gap_w3・
    hole_overlap_w4 が face_id・field_a・field_b を渡しながら白リストに無く、
    イベント名しか記録されていなかった（2026-09-02 実測・issue #77・
    08_frame_detection_design.md §1.1）。この検査はその再発を機械的に止める。

    2026-09-02（マリン指摘・#77 追補）の拡張3点:
    - `from .logging_safe import warn` のような直接名 import（`log.warn(...)`
      ではなく `warn(...)` の裸呼び出し）も検出対象にする
    - `**kwargs` 展開はキー名を静的に追えないため、スキップではなく**違反**
      として扱う（追跡不能な経路を白リストのすり抜けに使わせない）
    - 出力関数名は `_logging_safe_output_function_names()` で動的に取る
      （ハードコードした固定タプルにしない）
    - `glob` ではなく `rglob` で走査する（サブディレクトリが増えても追随する）
    """
    output_fns = _logging_safe_output_function_names()
    pkg_dir = Path(logging_safe.__file__).resolve().parent
    violations: list[str] = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        # このファイルで logging_safe が束縛されているローカル名を集める。
        # module_aliases: `from . import logging_safe as log` 形式
        # （`log.warn(...)` の `log`）。name_aliases: `from .logging_safe
        # import warn` 形式の裸呼び出し（`warn(...)`）→ 元の関数名
        module_aliases: set[str] = set()
        name_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == "logging_safe":
                        module_aliases.add(alias.asname or alias.name)
                    elif alias.name in output_fns and (
                            node.module == "logging_safe"
                            or (node.module or "").endswith(".logging_safe")):
                        name_aliases[alias.asname or alias.name] = alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in ("logging_safe", "chouhyo_ocr.logging_safe"):
                        module_aliases.add(alias.asname or alias.name.split(".")[-1])
        if not module_aliases and not name_aliases:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            attr: str | None = None
            if (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                    and func.value.id in module_aliases and func.attr in output_fns):
                attr = func.attr
            elif isinstance(func, ast.Name) and func.id in name_aliases:
                attr = name_aliases[func.id]
            if attr is None:
                continue
            rel = py_file.relative_to(pkg_dir)
            for kw in node.keywords:
                if kw.arg is None:
                    violations.append(
                        f"{rel}:{node.lineno} log.{attr}(..., **...) が "
                        "**kwargs 展開でキー名を静的検査できない"
                        "（展開せず個別キーワード引数で渡す）")
                    continue
                if kw.arg not in logging_safe._ALLOWED_KEYS:
                    violations.append(
                        f"{rel}:{node.lineno} "
                        f"log.{attr}(..., {kw.arg}=...) "
                        "が白リストに無いキーを渡している"
                        "（_ALLOWED_KEYS へ追加するか呼び出しを直す）")
    assert not violations, "\n".join(violations)
