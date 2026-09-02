// 帳票OCRツール GUI シェル（設計 §3.1・§7）。
// 処理ロジックを持たない: Python コアの起動・進捗中継・ファイル読み書きに徹する。
use std::collections::HashSet;
use std::io::{BufRead, BufReader};
use std::path::{Component, Path, PathBuf, Prefix};
use std::process::{Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// 実行中のコアの PID（中断ボタン用・同時実行は1つの前提）
pub struct CoreProc(pub Mutex<Option<u32>>);

/// ファイル選択ダイアログで利用者が実際に選んだパス（issue #49）。
/// webview は任意のパスで invoke できるため、ファイル読み書きの許可は
/// 「ここに登録された物」または「アプリ管理下のフォルダ」に限る。
/// テンプレート編集は任意の場所の JSON を開ける必要があるので、
/// 白リストをルート固定にはせずダイアログの選択結果で広げる。
pub struct PickedPaths(pub Mutex<HashSet<PathBuf>>);

/// webview から起動できるサブコマンドの白リスト（issue #7）。
/// purge（中間データ全削除）は要件 §6.3「削除は明示操作のみ」のため
/// GUI 境界からは呼べない——必要なら CLI を直接使う。
const ALLOWED_SUBCOMMANDS: &[&str] = &[
    "run", "render", "remap", "status", "verify", "detect-grid",
    "expand-page", "import-credentials",
];

/// サブコマンドが受け付けるフラグと、値を取るかどうかの対応表
/// （issue #52 M-7: 旧 `check_args` が argv[0] しか検証しておらず、以降の
/// フラグが無検証で子プロセスへ渡っていた穴を塞ぐ）。表に無いフラグは
/// 拒否する。`--replay`・`--resend-on-template-change` は cli.py の run が
/// 実際に持つフラグだが、意図的にどのサブコマンドの表にも入れていない——
/// 要配慮個人情報の再送・任意ディレクトリでの再生は GUI 境界からは常に
/// 禁止し、CLI 直叩き限定にする（S-MD 方針・#52 M-7 の対応方針どおり）。
fn allowed_flags(subcommand: &str) -> &'static [(&'static str, bool)] {
    match subcommand {
        "run" => &[("--input", true), ("--template", true)],
        "render" | "remap" => &[("--template", true)],
        "status" => &[],
        "verify" => &[("--template", true), ("--expect-columns", true)],
        "detect-grid" => &[
            ("--image", true), ("--region", true), ("--mode", true),
            ("--rows", true), ("--cols", true), ("--dpi", true),
        ],
        "expand-page" => &[
            ("--input", true), ("--page", true), ("--dpi", true),
            ("--template", true), ("--no-mask", false),
        ],
        // import-credentials は位置引数 json_path のみ（check_args_v2 内で別扱い）。
        _ => &[],
    }
}

/// GUI 境界からは常に禁止するフラグ（issue #52 M-7・S-MD）。
/// サブコマンドの許可表に載せない、では不十分——将来 `run` の表へ他のフラグを
/// 足すときに紛れ込む事故を避けるため、明示的な拒否リストとして独立させる。
const FORBIDDEN_FLAGS: &[&str] = &["--replay", "--resend-on-template-change"];

/// `--flag=value` 形式を (flag, value) へ分解する（`--flag value` の2トークン
/// 形式と両対応・inject_default_template の等号扱いと揃える・issue L-3）。
fn split_eq(arg: &str) -> Option<(&str, &str)> {
    if arg.starts_with("--") { arg.split_once('=') } else { None }
}

/// サブコマンド白リスト＋フラグ表による引数検査（issue #7・#52 M-7・S-MD）。
/// 通った引数を (flag, value) の一覧として返す——値を取らないフラグの value
/// は空文字列、位置引数（import-credentials の json_path）は flag側に
/// `"json_path"` を入れる。呼び出し側（run_core）はこれを使い、
/// `--input`/`--template` の値をさらにパススコープ検査する。
fn check_args_v2(args: &[String]) -> Result<Vec<(String, String)>, String> {
    let cmd = match args.first() {
        Some(c) if ALLOWED_SUBCOMMANDS.contains(&c.as_str()) => c.as_str(),
        Some(c) => return Err(format!("許可されていないコマンド: {c}")),
        None => return Err("コマンドが指定されていない".into()),
    };
    for forbidden in FORBIDDEN_FLAGS {
        let hit = args.iter().any(|a| {
            a.as_str() == *forbidden || split_eq(a).is_some_and(|(f, _)| f == *forbidden)
        });
        if hit {
            return Err(format!("この操作からは使えない引数です: {forbidden}"));
        }
    }
    let specs = allowed_flags(cmd);
    let mut pairs = Vec::new();
    let mut i = 1;
    while i < args.len() {
        let a = args[i].as_str();
        if let Some((name, value)) = split_eq(a) {
            let spec = specs.iter().find(|s| s.0 == name)
                .ok_or_else(|| format!("許可されていない引数です: {name}"))?;
            if !spec.1 {
                return Err(format!("値を取らない引数に値が指定されています: {name}"));
            }
            pairs.push((spec.0.to_string(), value.to_string()));
            i += 1;
        } else if let Some(spec) = specs.iter().find(|s| s.0 == a) {
            if spec.1 {
                let value = args.get(i + 1)
                    .ok_or_else(|| format!("{a} に値が指定されていません"))?;
                pairs.push((spec.0.to_string(), value.clone()));
                i += 2;
            } else {
                pairs.push((spec.0.to_string(), String::new()));
                i += 1;
            }
        } else if a.starts_with('-') {
            return Err(format!("許可されていない引数です: {a}"));
        } else if cmd == "import-credentials" {
            // 位置引数（json_path）。他のサブコマンドは位置引数を持たない
            pairs.push(("json_path".to_string(), a.to_string()));
            i += 1;
        } else {
            return Err(format!("許可されていない引数です: {a}"));
        }
    }
    Ok(pairs)
}

/// `--template` を受け付けるサブコマンド（core/chouhyo_ocr/cli.py 準拠・issue #58）。
/// ALLOWED_SUBCOMMANDS（webview から呼べるコマンドの白リスト）とは別物として
/// 保つ——debug-images は --template を受けるが白リスト外のまま（GUI からは
/// 呼べない・方針どおり）なので、ここに含めても inject_default_template まで
/// 到達しない。
const TEMPLATE_ACCEPTING_SUBCOMMANDS: &[&str] = &[
    "run", "render", "remap", "verify", "expand-page", "debug-images",
];

/// サブコマンドが `--template` を受け付けるのに引数へ含まれていない場合、
/// 出荷テンプレート（`<repo>/templates/chouhyo-v1.json`）を明示的に追記する
/// （issue #58）。
///
/// 同梱 exe（core-dist/chouhyo-core/chouhyo-core.exe）優先起動時、frozen 側の
/// `app_root()`（core/chouhyo_ocr/paths.py）は exe の親ディレクトリを指すため、
/// `--template` 未指定だと CLI 既定値が core-dist 側の別実体テンプレートを
/// 指してしまい、エディタが保存する `<repo>/templates/chouhyo-v1.json` への
/// 変更が読み取りへ反映されない（テンプレート二重実体）。ここで常に明示指定
/// することで、GUI からの起動はどのコアバイナリでも同じファイルを読む。
fn inject_default_template(mut args: Vec<String>, root: &Path) -> Vec<String> {
    let accepts = args
        .first()
        .map(|c| TEMPLATE_ACCEPTING_SUBCOMMANDS.contains(&c.as_str()))
        .unwrap_or(false);
    // `--template=path`（等号形式）も明示指定として扱う。素通りすると
    // 二重に --template を積んでコアの argparse がエラーになる（issue L-3）
    if accepts && !args.iter().any(|a| a == "--template" || a.starts_with("--template=")) {
        let tpl = root.join("templates").join("chouhyo-v1.json");
        args.push("--template".to_string());
        args.push(tpl.to_string_lossy().to_string());
    }
    args
}

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// リポジトリ（アプリ）ルートの解決。templates/ をマーカーに、
/// exe 位置 → cwd → Tauri リソースディレクトリ（インストール済みレイアウト）の順で探す。
fn repo_root(app: &AppHandle) -> Result<PathBuf, String> {
    let mut starts: Vec<PathBuf> = Vec::new();
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            starts.push(dir.to_path_buf());
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        starts.push(cwd);
    }
    if let Ok(res) = app.path().resource_dir() {
        starts.push(res);
    }
    // 開発環境ではリポルート（.git を持つ祖先）を最優先する。
    // tauri dev は resources（templates/core-dist 等）を target/debug へコピーする
    // ため、templates マーカーだけだと exe 親の target/debug が先にヒットし、
    // 「起動時スナップショットの旧コア・別 config」の世界で動いてしまう
    // （実測: 編集画面の PDF 展開が旧コアの相対パスで失敗・2026-08-28）
    for start in &starts {
        let mut dir = Some(start.as_path());
        while let Some(d) = dir {
            if d.join(".git").exists()
                && d.join("templates").join("chouhyo-v1.json").exists()
            {
                return Ok(d.to_path_buf());
            }
            dir = d.parent();
        }
    }
    for start in &starts {
        let mut dir = Some(start.as_path());
        while let Some(d) = dir {
            if d.join("templates").join("chouhyo-v1.json").exists() {
                return Ok(d.to_path_buf());
            }
            dir = d.parent();
        }
    }
    Err("アプリのルートが見つからない（templates/chouhyo-v1.json を基準に探索）".into())
}

/// 実在しないファイル（保存先）も扱えるパス正規化（issue #49）。
/// `..` は canonicalize の前に弾く——canonicalize は実在するパスしか畳めず、
/// 「保存先の親だけ実在」というケースで外へ抜ける余地を残すため。
fn normalize_path(path: &str) -> Result<PathBuf, String> {
    let p = Path::new(path);
    if p.components().any(|c| matches!(c, Component::ParentDir)) {
        return Err("パスに .. を含めることはできません".into());
    }
    if let Ok(c) = p.canonicalize() {
        return Ok(c);
    }
    // 未作成のファイル（「保存して検証」の新規保存先）は親を畳んで組み立てる
    let parent = p
        .parent()
        .filter(|d| !d.as_os_str().is_empty())
        .ok_or_else(|| "パスを解決できません".to_string())?;
    let name = p
        .file_name()
        .ok_or_else(|| "パスを解決できません".to_string())?;
    let base = parent
        .canonicalize()
        .map_err(|_| "保存先のフォルダが見つかりません".to_string())?;
    Ok(base.join(name))
}

/// 拡張子とパススコープの検査（issue #49）。副作用を持たないので単体テスト可能。
fn check_scope(abs: &Path, exts: &[&str], roots: &[PathBuf],
               picked: &HashSet<PathBuf>) -> Result<(), String> {
    let ok_ext = abs
        .extension()
        .and_then(|e| e.to_str())
        .map(|e| exts.contains(&e.to_ascii_lowercase().as_str()))
        .unwrap_or(false);
    if !ok_ext {
        return Err(format!("この操作で扱えるのは {} だけです", exts.join("・")));
    }
    if picked.contains(abs) || roots.iter().any(|r| abs.starts_with(r)) {
        Ok(())
    } else {
        Err("アプリの管理外のパスです。ファイル選択ダイアログから選び直してください".into())
    }
}

/// フォルダ（または拡張子を問わない入力）のパススコープ検査。`check_scope`
/// は拡張子必須で `run --input`（フォルダ、または拡張子を問わない PDF/画像
/// ファイル1つ・issue #19）には使えないため分けた（issue S-MD）。
fn check_scope_dir(abs: &Path, roots: &[PathBuf], picked: &HashSet<PathBuf>) -> Result<(), String> {
    if picked.contains(abs) || roots.iter().any(|r| abs.starts_with(r)) {
        Ok(())
    } else {
        Err("選択されていないフォルダです。フォルダを選び直してください".into())
    }
}

/// staged 保存で使う一時ファイルの接尾辞（`<path>.saving.json`）。
const STAGED_SUFFIX: &str = ".saving.json";

/// abs が picked のいずれかに対する staged 一時ファイル（`<picked>.saving.json`）か
/// （issue S-N2）。
///
/// テンプレート保存は `write_template_staged`（保存先は picked 限定）が作った
/// 一時ファイルを `verify --template <path>.saving.json` に渡して検証する
/// （`Editor.tsx:1723-1740`）。この一時ファイル自体はダイアログを通らないので
/// picked には無く、素の `check_scope` では拒否される。接尾辞を外した本体が
/// picked にあることを条件にすれば、保存フローだけを通しつつ「任意の
/// `.saving.json` を読ませる」抜け道は作らない。
fn is_staged_of_picked(abs: &Path, picked: &HashSet<PathBuf>) -> bool {
    let Some(s) = abs.as_os_str().to_str() else { return false };
    let Some(base) = s.strip_suffix(STAGED_SUFFIX) else { return false };
    !base.is_empty() && picked.contains(Path::new(base))
}

/// `--template` の値のスコープ検査（issue S-N2）。通常の JSON（picked または
/// roots 配下）に加えて、保存フローの staged 一時ファイルだけを許す。
fn check_template_scope(abs: &Path, roots: &[PathBuf],
                        picked: &HashSet<PathBuf>) -> Result<(), String> {
    if is_staged_of_picked(abs, picked) {
        return Ok(());
    }
    check_scope(abs, &["json"], roots, picked)
}

/// コアへ渡す引数のうち、パスを値に取るフラグをスコープ検査する
/// （issue S-MD・S-N2）。`run_core`（run）と `run_core_capture`
/// （verify / detect-grid / expand-page）で同じ関数を通す。
///
/// 以前は run だけを検査していたため、`expand-page --input <任意の PDF>` で
/// 任意のファイルを読ませ、その展開結果（`editor_pages` の PNG）を
/// `read_file_b64` で吸い出す連鎖が残っていた——API 送信・課金が無くても
/// 「webview から任意パスの中身を見る」経路としては成立する。
fn check_arg_scopes(pairs: &[(String, String)], roots: &[PathBuf],
                    picked: &HashSet<PathBuf>) -> Result<(), String> {
    for (flag, value) in pairs {
        match flag.as_str() {
            // フォルダ・拡張子なしのファイルもありうる（run --input・#19）
            "--input" => check_scope_dir(&normalize_path(value)?, roots, picked)?,
            "--image" => check_scope(&normalize_path(value)?,
                                     &["png", "jpg", "jpeg"], roots, picked)?,
            "--template" => check_template_scope(&normalize_path(value)?, roots, picked)?,
            // import-credentials の json_path は対象外。pick_json が
            // remember_pick=false で呼ばれ、鍵を picked へ入れない設計
            // （lib.rs :594 のコメント）と衝突するため
            _ => {}
        }
    }
    Ok(())
}

/// output_dir/workdir/log_dir に許すパスの安全性判定（issue Q-MC/S-MA）。
/// 空・空白のみ／ドライブルート（`C:\`・`C:`・`/`）／UNC（`\\server\share`）／
/// `..` を含むものを拒否する。判定は `Path::components()` ベースで
/// `normalize_path`（:121-141）の流儀に揃え、文字列プレフィックス一致だけに
/// 頼らない。
///
/// **呼び出し側の注意**: canonicalize 前の生パスに対して呼ぶこと。
/// `Path::canonicalize()` は Windows で `\\?\` verbatim プレフィックスを
/// 付与するため、その後段では UNC 判定が別物になる（VerbatimUNC も併せて
/// 見ているのはこのため）。
fn is_safe_root(p: &Path) -> bool {
    match p.as_os_str().to_str() {
        Some(s) if !s.trim().is_empty() => {}
        _ => return false,
    }
    let mut has_normal = false;
    for c in p.components() {
        match c {
            Component::Prefix(prefix) => {
                if matches!(prefix.kind(), Prefix::UNC(..) | Prefix::VerbatimUNC(..)) {
                    return false;
                }
            }
            Component::ParentDir => return false,
            Component::Normal(_) => has_normal = true,
            _ => {}
        }
    }
    // ドライブルート（`C:\`・`C:`・`/`）は Normal 成分を1つも持たない
    has_normal
}

/// 編集画面が読む PDF 展開結果の置き場（`core/chouhyo_ocr/cli.py:229`
/// `out_dir = Path(cfg.workdir) / "editor_pages"` と一致させる・issue S-N4）。
const EDITOR_PAGES_DIR: &str = "editor_pages";

/// 設定の workdir から「ダイアログ抜きで読んでよいフォルダ」を導く
/// （issue S-N4）。返すのは canonicalize 前の絶対パス。
///
/// workdir 全体ではなく `<workdir>/editor_pages` に絞る。GUI が workdir 配下で
/// 読むのは `expand-page` が書く PNG だけ（`Editor.tsx:1424` の
/// `read_file_b64`・`detect-grid --image`）なのに対し、workdir 直下には
/// `responses/`（Vision API の生応答＝帳票の記入値そのもの）や
/// `cred.dpapi`・中間 SQLite が同居する。拡張子制限で今は読めない物も、
/// 将来 read_text 等の許可拡張子が増えれば射程に入る——読み取りルート自体を
/// 必要最小の1階層へ落としておく。
fn workdir_pages_dir(root: &Path, workdir: &str) -> Option<PathBuf> {
    // 空文字は join で消えて `<root>/core` になり、is_safe_root の空判定を
    // すり抜ける。設定値そのものの段階で弾く
    if workdir.trim().is_empty() {
        return None;
    }
    let p = PathBuf::from(workdir);
    // CLI の相対パス設定は cwd=core 基準（open_folder と同じ流儀）
    let abs = if p.is_absolute() { p } else { root.join("core").join(p) };
    // config は手編集や別プロセス（コア側）からも書けるため、write_config の
    // validate_config_patch だけでは守れない（issue Q-MC/S-MA）。同じ安全性
    // 判定を canonicalize 前（is_safe_root の要件）にここでも通す。
    if is_safe_root(&abs) { Some(abs.join(EDITOR_PAGES_DIR)) } else { None }
}

/// ダイアログを介さずに読み書きしてよいフォルダ。アプリルートに加えて、
/// 設定の workdir 配下の `editor_pages` を含める（編集画面の PDF 展開結果は
/// ここに出る。workdir を外部フォルダへ向けた構成でもプレビューを壊さない）。
fn allowed_roots(app: &AppHandle) -> Result<Vec<PathBuf>, String> {
    let root = repo_root(app)?;
    let mut roots = vec![root.canonicalize().unwrap_or_else(|_| root.clone())];
    let workdir = read_config(app.clone())
        .ok()
        .and_then(|c| c.get("workdir").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_else(|| "workdir".to_string());
    // 不正な workdir はルートへ足さない——allowed_roots 全体を Err にはせず、
    // repo_root だけの scope へ縮退させる（fail-safe。他コマンドを巻き込んで
    // 落とさない）
    if let Some(pages) = workdir_pages_dir(&root, &workdir) {
        if let Ok(c) = pages.canonicalize() {
            // canonicalize の**結果にも** is_safe_root を通す（issue S-N3）。
            // ジャンクション（`C:\app\core\toroot → C:\`）を挟むと、生パスは
            // 「普通のサブフォルダ」として通るのに畳んだ先がドライブ直下に
            // なりうる。生パスだけの判定では、その1本で読み取りルートが
            // ドライブ全域へ広がる。verbatim プレフィックス（`\\?\C:\`）は
            // Prefix + RootDir だけで Normal 成分を持たないため、
            // components ベースの is_safe_root がそのまま false を返す。
            if is_safe_root(&c) && !roots.contains(&c) {
                roots.push(c);
            }
        }
    }
    Ok(roots)
}

/// ドラッグ＆ドロップで OS から渡されたパスを白リストへ登録する（issue S-N1）。
///
/// 登録元は **webview が invoke で渡してくる文字列ではなく**、Tauri の
/// `WindowEvent::DragDrop`（`run()` の `on_window_event`）が持つ `PathBuf` に
/// 限る。以前は `remember_dropped_path` コマンドとして webview から任意の
/// パスを登録できたため、レンダラを掌握されると「ダイアログで選ばれた物だけ
/// 読み書きを許す」という `PickedPaths` の前提そのものを webview 側から
/// 無効化できた（白リストの自己申告化）。
///
/// 正規化に失敗したパス（`..` を含む等）は登録せず読み飛ばす——ドロップは
/// 複数パスを一度に運ぶので、1つの不正で残りを巻き添えにしない。
fn remember_dropped(picked: &PickedPaths, paths: &[PathBuf]) {
    let mut set = picked.0.lock().unwrap();
    for p in paths {
        if let Ok(abs) = normalize_path(&p.to_string_lossy()) {
            set.insert(abs);
        }
    }
}

/// ダイアログで選ばれたパスを白リストへ登録する。
fn remember(picked: &PickedPaths, p: &Path) {
    remember_dropped(picked, std::slice::from_ref(&p.to_path_buf()));
}

/// コア起動コマンドを組み立てる。配布版は同梱 exe、開発版は venv の python -m。
fn core_command(root: &PathBuf) -> Result<Command, String> {
    let bundled = root.join("core-dist").join("chouhyo-core").join("chouhyo-core.exe");
    let mut cmd = if bundled.exists() {
        Command::new(bundled)
    } else {
        let py = root.join(".venv").join("Scripts").join("python.exe");
        if !py.exists() {
            return Err("Python コアが見つからない（.venv 未構築・配布物欠損）".into());
        }
        let mut c = Command::new(py);
        c.args(["-X", "utf8", "-m", "chouhyo_ocr.cli"]);
        c
    };
    let cwd = root.join("core");
    let _ = std::fs::create_dir_all(&cwd); // インストール直後は core/ が無い
    cmd.current_dir(cwd);
    cmd.env("PYTHONUTF8", "1");
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    Ok(cmd)
}

/// 指定 PID を子プロセスごと停止する（`taskkill /T /F`）。Windows は親
/// プロセス終了だけでは子（pdftoppm 等）が生き残り課金が続くため、常に
/// `/T`（プロセスツリー）を付ける。エラーの扱いは呼び出し側に委ねる——
/// `kill_core`（中断ボタン）は利用者にそのまま返す一方、`PidSlot::drop`
/// はベストエフォートとして無視する（早期 return からの後始末で、失敗を
/// 更に誰かに投げる先が無いため）。
fn kill_pid(pid: u32) -> Result<(), String> {
    let mut c = Command::new("taskkill");
    c.args(["/T", "/F", "/PID", &pid.to_string()]);
    #[cfg(windows)]
    c.creation_flags(CREATE_NO_WINDOW);
    let out = c.output().map_err(|e| e.to_string())?;
    if out.status.success() { Ok(()) } else { Err("停止できませんでした".into()) }
}

/// run_core が確保する PID スロットの RAII ガード（issue Q-MB）。
///
/// spawn 直後に確保し、以降の `?` による早期 return（stdout/stderr 取得
/// 失敗・`child.wait()` 失敗）を含む全ての経路でスコープを抜けるときに
/// 解放する。以前は成功パスの末尾でしか解放しておらず、早期 return すると
/// スロットが `Some(pid)` のまま残り、次の実行が「すでに読み取りを実行中
/// です」で恒久的にブロックされた——しかも子プロセス（pdftoppm・API 呼び出し
/// 中の本体）はそのまま生き続けるため課金も止まらない。
///
/// 「自分の pid のときだけ消す」不変条件は維持する——中断ボタン
/// （`kill_core`）が既に `take()` した後に、別の run の PID を上書きで
/// 消さない（旧実装 :406-410 と同じ判定）。ロックは Drop の中で取る
/// （async fn 内で MutexGuard を await にまたがせない）。
struct PidSlot<'a> {
    state: &'a Mutex<Option<u32>>,
    pid: u32,
    kill_on_drop: bool,
}

impl<'a> PidSlot<'a> {
    fn new(state: &'a Mutex<Option<u32>>, pid: u32) -> Self {
        Self { state, pid, kill_on_drop: true }
    }

    /// 正常終了（`child.wait()` 済み）を伝える。以後の Drop は kill を試みない
    /// ——既に終了した pid へ taskkill してもエラーにはならないが、OS が pid を
    /// 再利用する僅かな窓を、成功する毎回のパスで払う理由が無い（早期
    /// return の異常系だけがこのリスクを取る価値がある）。
    fn disarm(&mut self) {
        self.kill_on_drop = false;
    }
}

impl Drop for PidSlot<'_> {
    fn drop(&mut self) {
        let mut slot = self.state.lock().unwrap();
        release_slot(&mut slot, self.pid);
        drop(slot);
        if self.kill_on_drop {
            let _ = kill_pid(self.pid);
        }
    }
}

/// スロットが自分の pid を指しているときだけ解放する（issue Q-MB）。
/// 「自分の pid のときだけ消す」不変条件そのもの——中断ボタン
/// （`kill_core`）が既に `take()` した後に、別の run の PID を上書きで
/// 消さない。ロック済みの中身だけを扱う純関数として切り出し、実際の
/// `Mutex`/プロセスを持ち出さずに単体テストできるようにしてある
/// （`PidSlot::drop` から呼ぶ）。
fn release_slot(slot: &mut Option<u32>, pid: u32) {
    if *slot == Some(pid) {
        *slot = None;
    }
}

/// コアを起動し stdout(JSON Lines)/stderr を行単位でイベント中継、終了コードを返す。
#[tauri::command]
async fn run_core(app: AppHandle, state: State<'_, CoreProc>,
                  picked: State<'_, PickedPaths>,
                  args: Vec<String>) -> Result<i32, String> {
    let pairs = check_args_v2(&args)?;
    let root = repo_root(&app)?;
    // 値のパススコープ検査（issue S-MD・S-N2）。run_core_capture 側と同じ
    // check_arg_scopes を通す。ロックは await をまたがせない（Send 制約）ため
    // このブロック内で閉じる
    {
        let roots = allowed_roots(&app)?;
        let picked_set = picked.0.lock().unwrap();
        check_arg_scopes(&pairs, &roots, &picked_set)?;
    }
    let args = inject_default_template(args, &root);
    let mut cmd = core_command(&root)?;
    cmd.args(&args).stdout(Stdio::piped()).stderr(Stdio::piped());
    // 2本目を断る（レビュー M-2）。以前は PID を上書きしていたため、2本目が
    // 終わった時点で 1本目の PID を見失い「中断」ボタンが効かなくなった。
    // コア側の実行ロックは同一保存先への二重送信を防ぐが、こちらの取り違えは
    // 防げない。判定〜登録の間に割り込まれないよう spawn までロックを持つ。
    let (mut child, mut slot_guard) = {
        let mut slot = state.0.lock().unwrap();
        if slot.is_some() {
            return Err("すでに読み取りを実行中です。完了するか中断してください".into());
        }
        let c = cmd.spawn().map_err(|e| format!("コア起動に失敗: {e}"))?;
        let pid = c.id();
        *slot = Some(pid);
        (c, PidSlot::new(&state.0, pid))
    };

    let stdout = child.stdout.take().ok_or("stdout を取得できない")?;
    let app_out = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = app_out.emit("core-line", line);
        }
    });
    let stderr = child.stderr.take().ok_or("stderr を取得できない")?;
    let app_err = app.clone();
    std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            let _ = app_err.emit("core-err", line);
        }
    });

    let status = tauri::async_runtime::spawn_blocking(move || child.wait())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| e.to_string())?;
    // 正常終了: 子プロセスは wait() 済みで既に居ない。kill は試みず、
    // スロットの解放だけを PidSlot::drop（関数末尾）に任せる
    slot_guard.disarm();
    Ok(status.code().unwrap_or(-1))
}

/// 実行中のコアを子プロセス（pdftoppm 等）ごと停止する。中断分は
/// 「未処理（中断）」として出力され、次回 run で続きから再開する（要件 §5.8）。
#[tauri::command]
fn kill_core(state: State<'_, CoreProc>) -> Result<(), String> {
    let pid = state.0.lock().unwrap().take().ok_or("実行中の処理がありません")?;
    kill_pid(pid)
}

/// コアを起動し stdout を丸ごと返す（編集画面の detect-grid / verify 用）。
#[tauri::command]
async fn run_core_capture(app: AppHandle, picked: State<'_, PickedPaths>,
                          args: Vec<String>) -> Result<String, String> {
    let pairs = check_args_v2(&args)?;
    let root = repo_root(&app)?;
    // run_core と同じ値検査を通す（issue S-N2）。フラグ表検査だけだと
    // `expand-page --input <任意.pdf>` で任意ファイルを展開させられる
    {
        let roots = allowed_roots(&app)?;
        let picked_set = picked.0.lock().unwrap();
        check_arg_scopes(&pairs, &roots, &picked_set)?;
    }
    let args = inject_default_template(args, &root);
    let mut cmd = core_command(&root)?;
    cmd.args(&args);
    let out = tauri::async_runtime::spawn_blocking(move || cmd.output())
        .await
        .map_err(|e| e.to_string())?
        .map_err(|e| format!("コア起動に失敗: {e}"))?;
    let stdout = String::from_utf8_lossy(&out.stdout).to_string();
    if out.status.success() {
        Ok(stdout)
    } else {
        // detect-grid の不成立などは stdout の JSON にも理由が載る
        Err(if stdout.trim().is_empty() {
            String::from_utf8_lossy(&out.stderr).to_string()
        } else {
            stdout
        })
    }
}

#[tauri::command]
fn pick_folder(picked: State<'_, PickedPaths>) -> Option<String> {
    // run --input のパススコープ検査（issue S-MD）を通すには picked への
    // 登録が要る。以前は登録しておらず、フォルダ選択直後の run が
    // allowed_roots（アプリルート・workdir）の外だと拒否されていた
    // （出力フォルダは登録不要だが、同じダイアログを共用しているため
    // ここで一括登録しても実害は無い——読み書きコマンド側の拡張子制限は
    // 別に効いている）。
    let p = rfd::FileDialog::new().pick_folder()?;
    remember(&picked, &p);
    Some(p.to_string_lossy().to_string())
}

#[tauri::command]
fn pick_image(picked: State<'_, PickedPaths>) -> Option<String> {
    // テンプレ作成の入力はスキャン PDF のことが多い。PDF はコアの expand-page で
    // 1ページ目を PNG 展開してから表示する（フロント側 loadImage が分岐）
    let p = rfd::FileDialog::new()
        .add_filter("帳票（PDF・画像）", &["pdf", "png", "jpg", "jpeg"])
        .pick_file()?;
    remember(&picked, &p);
    Some(p.to_string_lossy().to_string())
}


#[tauri::command]
fn pick_json(app: AppHandle, picked: State<'_, PickedPaths>, save: bool,
             remember_pick: Option<bool>, default_path: Option<String>) -> Option<String> {
    let mut d = rfd::FileDialog::new().add_filter("テンプレート", &["json"]);
    let p = if save {
        // 保存の既定は「エディタが今読み込んでいるファイル」（default_path）。
        // 指定が無い（起動時の自動読込のまま一度も別ファイルを開いていない）
        // ときだけ出荷テンプレートへフォールバックする。以前は保存の既定が
        // 常に出荷テンプレート固定だったため、別テンプレートを編集していても
        // Enter 1回で出荷テンプレートを上書きしてしまう経路になっていた
        // （issue #56 T1-3）。出荷テンプレへの保存だけは呼び出し側
        // （is_shipped_template_path・Editor.tsx）でも明示確認を挟む
        let dp = default_path.as_deref().map(Path::new)
            .filter(|p| !p.as_os_str().is_empty());
        if let Some(dp) = dp {
            if let Some(dir) = dp.parent().filter(|p| !p.as_os_str().is_empty()) {
                d = d.set_directory(dir);
            }
            if let Some(name) = dp.file_name() {
                d = d.set_file_name(name.to_string_lossy().into_owned());
            }
        } else if let Ok(root) = repo_root(&app) {
            d = d.set_directory(root.join("templates"));
            d = d.set_file_name("chouhyo-v1.json");
        }
        d.save_file()
    } else { d.pick_file() }?;
    // 認証キーの取り込みは remember_pick=false で呼ぶ。白リストへ入れると
    // GCP サービスアカウント鍵（平文 JSON）がセッション中ずっと read_text で
    // 読める状態になる——鍵を DPAPI へ退避させる操作が、その鍵を読める窓を
    // 開けてしまう。テンプレートの読み書きだけが白リストの用途（issue #49）
    if remember_pick.unwrap_or(true) {
        remember(&picked, &p);
    }
    Some(p.to_string_lossy().to_string())
}

#[tauri::command]
fn open_folder(app: AppHandle, path: String) -> Result<(), String> {
    let root = repo_root(&app)?;
    let p = PathBuf::from(&path);
    // CLI の相対パス設定（既定 "output"）は cwd=core 基準
    let abs = if p.is_absolute() { p } else { root.join("core").join(p) };
    // explorer は引数が実行可能ファイルなら起動してしまう（LOLBin）。
    // フォルダを開く用途しかないため、ディレクトリ以外は拒否する（issue #5）
    if !abs.is_dir() {
        return Err("フォルダではないため開けません".into());
    }
    Command::new("explorer")
        .arg(abs)
        .spawn()
        .map_err(|e| e.to_string())?;
    Ok(())
}

fn config_file(app: &AppHandle) -> Result<PathBuf, String> {
    Ok(repo_root(app)?.join("config.json"))
}

#[tauri::command]
fn read_config(app: AppHandle) -> Result<serde_json::Value, String> {
    let p = config_file(&app)?;
    if !p.exists() {
        return Ok(serde_json::json!({}));
    }
    let text = std::fs::read_to_string(p).map_err(|e| e.to_string())?;
    serde_json::from_str(&text).map_err(|e| e.to_string())
}

/// config.json が受け付ける既知キー（`core/chouhyo_ocr/config.py:26-40` の
/// `Config` dataclass フィールドと一致させる・issue Q-MC/S-MA）。
const KNOWN_CONFIG_KEYS: &[&str] = &[
    "unclear_threshold", "era_threshold", "send_limit",
    "output_dir", "workdir", "log_dir",
    "api_monthly_cap", "unclear_char_level",
];

/// パス文字列として安全性検査が要るキー（`is_safe_root` を通す・issue Q-MC/S-MA）。
const CONFIG_PATH_KEYS: &[&str] = &["output_dir", "workdir", "log_dir"];

/// `write_config` の patch を検証する（issue Q-MC/S-MA）。
///
/// 未知キーは拒否する——ここで止めないと、`config.json` へ書かれた未知キーが
/// 次のコア起動時に `load_config`（`config.py:76-78`）の `ConfigError` を
/// 引き起こし、run/render/verify を含む**全コマンドが起動不能**になる
/// （設定ファイル1つでローカル DoS が成立する）。output_dir/workdir/log_dir の
/// 3キーは `allowed_roots`（`read_file_b64` の読み取り範囲の起点）にそのまま
/// 使われるため、パスの安全性（空・ドライブ直下・UNC・`..`）も検査する。
///
/// **設計判断（意図的なスコープ限定）**: unclear_threshold 等の型・範囲検証
/// （0〜1・0以上の整数、など）は行わない。それらは `config.py:_validate` が
/// 既に唯一の正として検証しており、ここで重複させると2箇所の定義が
/// 将来ズレる（片方だけ範囲を変えて他方を直し忘れる）リスクの方が高いと
/// 判断した。patch 側で拒否できなかった不正値は、次のコア起動時に
/// `ConfigError` として core 側で捕捉される（起動不能にはなるが、少なくとも
/// 理由が明示される）。
fn validate_config_patch(patch: &serde_json::Value) -> Result<(), String> {
    let obj = patch.as_object()
        .ok_or_else(|| "設定の形式が不正です（オブジェクトではありません）".to_string())?;
    for key in obj.keys() {
        if !KNOWN_CONFIG_KEYS.contains(&key.as_str()) {
            return Err(format!("未知の設定キーです: {key}"));
        }
    }
    for key in CONFIG_PATH_KEYS {
        let Some(v) = obj.get(*key) else { continue };
        let s = v.as_str()
            .ok_or_else(|| format!("{key} は文字列で指定してください"))?;
        if !is_safe_root(Path::new(s)) {
            return Err(format!(
                "{key} に使えないパスです（空・ドライブ直下・ネットワークパス・.. は指定できません）"
            ));
        }
    }
    Ok(())
}

/// 既存の config.json（未作成なら None）と patch から、書き出す内容を作る
/// （issue N-3）。
///
/// 壊れた config.json を空として作り直さない。以前はパース失敗を
/// `unwrap_or(json!({}))` で握り潰していたため、既存の workdir・send_limit・
/// api_monthly_cap 等が patch 以外まとめて消え、しかも「保存しました」と
/// 表示された——出力先を選び直しただけの操作で送信上限が失われるのは、
/// 画面からは気づけないうえ復元もできない。読めないときは書かずに理由を
/// 返し、手で直す（または削除する）判断を利用者へ渡す。
///
/// トップレベルがオブジェクトでない JSON（配列・数値等）も同じ扱いにする。
/// 素通しすると patch がどこにも入らないまま「保存しました」になる。
fn merge_config(existing: Option<&str>, patch: &serde_json::Value)
                -> Result<serde_json::Value, String> {
    validate_config_patch(patch)?;
    let broken = |detail: String| {
        format!("設定ファイル（config.json）を読めないため保存できません（{detail}）。\
                 内容を直すか、ファイルを削除してからやり直してください")
    };
    let mut cur = match existing {
        Some(text) => serde_json::from_str::<serde_json::Value>(text)
            .map_err(|e| broken(e.to_string()))?,
        None => serde_json::json!({}),
    };
    let obj = cur.as_object_mut()
        .ok_or_else(|| broken("オブジェクトではありません".to_string()))?;
    if let Some(add) = patch.as_object() {
        for (k, v) in add {
            obj.insert(k.clone(), v.clone());
        }
    }
    Ok(cur)
}

/// 設定の部分更新（要件 §5.7: GUI で選んだ値を保存し次回既定値に）。他キーは保持する。
#[tauri::command]
fn write_config(app: AppHandle, patch: serde_json::Value) -> Result<(), String> {
    let p = config_file(&app)?;
    let existing = if p.exists() {
        Some(std::fs::read_to_string(&p).map_err(|e| e.to_string())?)
    } else {
        None
    };
    let merged = merge_config(existing.as_deref(), &patch)?;
    std::fs::write(&p, serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}

/// 画像を data URL で返す（編集画面のキャンバス表示用・asset protocol 不使用）。
/// 白リスト（ダイアログで選ばれた画像／アプリ管理下の展開結果）に限る。
/// 無検証だと、レンダラを掌握された場合に workdir の cred.dpapi や
/// 中間 SQLite を b64 で吸い出せてしまう（issue #49）。
#[tauri::command]
fn read_file_b64(app: AppHandle, picked: State<'_, PickedPaths>,
                 path: String) -> Result<String, String> {
    use base64::Engine;
    let abs = normalize_path(&path)?;
    check_scope(&abs, &["png", "jpg", "jpeg"], &allowed_roots(&app)?,
                &picked.0.lock().unwrap())?;
    let bytes = std::fs::read(&abs).map_err(|e| e.to_string())?;
    let ext = abs
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("png")
        .to_lowercase();
    let mime = if ext == "jpg" || ext == "jpeg" { "image/jpeg" } else { "image/png" };
    Ok(format!("data:{};base64,{}",
               mime,
               base64::engine::general_purpose::STANDARD.encode(bytes)))
}

/// 出荷テンプレート（templates/chouhyo-v1.json）を読む。
///
/// パスは固定で webview から受け取らない。read_text は #49 でダイアログ選択
/// パスのみに締めたため、エディタ起動時の自動読み込みはこの専用コマンドで
/// 行う（緩めると responses/ の記入値 JSON が読める穴が戻る）。run が既定で
/// 使うテンプレートと同じファイルなので、エディタは「1から作る画面」でなく
/// 「読み取りが実際に使っている欄を直す画面」として開ける——この一致は
/// inject_default_template（issue #58）が保証している。同梱 exe 優先起動時は
/// frozen 側の app_root() が core-dist 側の別実体を指すため、注入なしでは
/// 「read_default_template はここ、run は別ファイル」という二重実体が
/// 成立していた。
#[tauri::command]
fn read_default_template(app: AppHandle) -> Result<String, String> {
    let p = repo_root(&app)?.join("templates").join("chouhyo-v1.json");
    // 絶対パスは webview へ返さない。既存の read_text/write_text と同じ
    // 粒度（固定文言＋OS エラーの Display 表現のみ）に揃える（issue #61 L-2）。
    // 実害は情報開示のみ（CSP で外部送出は塞がれている）だが、他コマンドと
    // 不揃いだった
    std::fs::read_to_string(&p).map_err(|e| format!("出荷テンプレートを読み込めません: {e}"))
}

/// テンプレート JSON の読み出し。**ダイアログで選ばれたパスだけ**に限る。
///
/// アプリ管理下（repo_root・workdir）を無条件に許すと、`workdir/responses/*.json`
/// ——Vision API の生応答で、帳票の記入値そのもの——が読める。フロントの
/// read_text 呼び出しは `pick_json` の戻り値に対してだけなので（Editor.tsx）、
/// roots を渡さず picked のみで足りる（レビュー4巡目・#49 の締め直し）。
#[tauri::command]
fn read_text(picked: State<'_, PickedPaths>, path: String) -> Result<String, String> {
    let abs = normalize_path(&path)?;
    check_scope(&abs, &["json"], &[], &picked.0.lock().unwrap())?;
    std::fs::read_to_string(abs).map_err(|e| e.to_string())
}

/// テンプレート JSON の書き出し。読み出しと同じくダイアログで選ばれたパスのみ。
///
/// roots を許すと、レンダラを掌握された場合に出荷テンプレート
/// （`templates/chouhyo-v1.json`）や `config.json` を書き換えられる。
#[tauri::command]
fn write_text(picked: State<'_, PickedPaths>,
              path: String, content: String) -> Result<(), String> {
    let abs = normalize_path(&path)?;
    check_scope(&abs, &["json"], &[], &picked.0.lock().unwrap())?;
    std::fs::write(abs, content).map_err(|e| e.to_string())
}

/// staged 保存で使う一時ファイルのパス（`<path>.saving.json`）。
/// 拡張子を差し替えるのではなく丸ごと追記する。コアの `--template` は
/// 拡張子の形を検査しないため、".json" で終わってさえいれば読める。
fn staged_path(target: &Path) -> PathBuf {
    let mut s = target.as_os_str().to_os_string();
    s.push(STAGED_SUFFIX);
    PathBuf::from(s)
}

/// promote 時に既存ファイルを退避する先（`<path>.bak`）。
fn backup_path(target: &Path) -> PathBuf {
    let mut s = target.as_os_str().to_os_string();
    s.push(".bak");
    PathBuf::from(s)
}

/// write_template_staged / promote_template / discard_staged 共通の入力検証。
/// write_text と同じ scope（picked パス由来の .json のみ）に限定する。
fn validate_template_target(path: &str, picked: &HashSet<PathBuf>) -> Result<PathBuf, String> {
    let abs = normalize_path(path)?;
    check_scope(&abs, &["json"], &[], picked)?;
    Ok(abs)
}

/// staged ファイルを本番パスへ確定する本体。rename を注入可能にしてあるのは、
/// 「確定の rename（staged→abs）が失敗する」経路を単体テストで固定するため
/// （マリン最終レビュー H-1）。Windows では読み取り専用属性がファイル作成を
/// 妨げず、オープンハンドルの共有モードもプラットフォーム依存で不安定なため、
/// OS レベルで確実に rename 失敗を誘発する方法が無かった。
///
/// 既存ファイルがあれば `.bak` へ退避してから rename するため、検証 NG のまま
/// 出荷テンプレートが上書きされることは無い。ただし退避が成功した**後**に
/// 確定の rename が失敗すると、素朴な実装では本番パスが空になり、かつ
/// staged だけが唯一の新内容になる。この関数はその失敗時に `.bak` を
/// 本番パスへ書き戻す（render_out.py の `_rollback` と同じ考え方）。
/// 書き戻せたかどうかは返す Err 文言に必ず載せる——戻せていないのに
/// 「戻した」と断言すると、本番パスが存在しないまま利用者を安心させてしまう。
fn promote_with<F>(staged: &Path, abs: &Path, bak: &Path, mut rename: F) -> Result<(), String>
where
    F: FnMut(&Path, &Path) -> std::io::Result<()>,
{
    if !staged.exists() {
        return Err("一時保存ファイルが見つかりません（先に保存を実行してください）".into());
    }
    let had_backup = abs.exists();
    if had_backup {
        rename(abs, bak).map_err(|e| format!("バックアップの作成に失敗: {e}"))?;
    }
    if let Err(e) = rename(staged, abs) {
        return Err(if !had_backup {
            // 元々 abs が無かった（初回保存）ので戻す先も無い。staged は
            // rename 失敗時に消えないので、そこに新内容が残っている
            format!(
                "保存の確定に失敗しました（{e}）。編集内容は {} に残っています。\
                 保存をやり直すか、このファイルを手動で {} へ移動してください",
                staged.display(), abs.display())
        } else {
            match rename(bak, abs) {
                Ok(()) => format!(
                    "保存の確定に失敗しました（{e}）。{} は直前の内容へ戻しました\
                     （壊れていません）。新しい編集内容は {} に残っています。\
                     保存をやり直してください",
                    abs.display(), staged.display()),
                Err(e2) => format!(
                    "保存の確定に失敗しました（{e}）。直前の内容への復元にも失敗しました\
                     （{e2}）。{} は存在しません。直前の内容は {} に、新しい編集内容は {} に\
                     あります。手動での復旧が必要です",
                    abs.display(), bak.display(), staged.display()),
            }
        });
    }
    Ok(())
}

/// staged ファイルを本番パスへ確定する（issue #56 T1・保存経路のトランザクション化）。
fn promote_staged(abs: &Path) -> Result<(), String> {
    let staged = staged_path(abs);
    let bak = backup_path(abs);
    promote_with(&staged, abs, &bak, |from, to| std::fs::rename(from, to))
}

/// staged ファイルを破棄する（コア検証 NG 時の掃除）。既に無ければ成功扱い（冪等）。
fn discard_staged_file(abs: &Path) -> Result<(), String> {
    let staged = staged_path(abs);
    match std::fs::remove_file(&staged) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(e.to_string()),
    }
}

/// abs が出荷テンプレート（`<repo>/templates/chouhyo-v1.json`）と同一ファイルか。
/// 保存先が出荷テンプレのときだけ上書き前に明示確認を挟むための判定（issue #56 T1-3）。
fn is_shipped_template(root: &Path, abs: &Path) -> bool {
    let shipped = root.join("templates").join("chouhyo-v1.json");
    match (shipped.canonicalize(), abs.canonicalize()) {
        (Ok(a), Ok(b)) => a == b,
        // 保存先が未作成（新規保存）だと canonicalize できないので、
        // 正規化済みパスどうしの単純比較にフォールバックする
        _ => shipped == abs,
    }
}

/// テンプレート JSON を一時ファイル（`<path>.saving.json`）へ書き出す。
/// 検証（verify）が通ってから promote_template で本番パスへ確定する二段構えに
/// することで、検証 NG のまま出荷テンプレートが上書きされる事故を防ぐ
/// （issue #56 T1）。
#[tauri::command]
fn write_template_staged(picked: State<'_, PickedPaths>,
                         path: String, content: String) -> Result<String, String> {
    let abs = validate_template_target(&path, &picked.0.lock().unwrap())?;
    let staged = staged_path(&abs);
    std::fs::write(&staged, content).map_err(|e| e.to_string())?;
    Ok(staged.to_string_lossy().to_string())
}

/// staged ファイルをコア検証 OK の後に本番パスへ確定する。
#[tauri::command]
fn promote_template(picked: State<'_, PickedPaths>, path: String) -> Result<(), String> {
    let abs = validate_template_target(&path, &picked.0.lock().unwrap())?;
    promote_staged(&abs)
}

/// staged ファイルを破棄する（コア検証 NG のとき、元ファイルを無傷のまま保つ）。
#[tauri::command]
fn discard_staged(picked: State<'_, PickedPaths>, path: String) -> Result<(), String> {
    let abs = validate_template_target(&path, &picked.0.lock().unwrap())?;
    discard_staged_file(&abs)
}

/// path が出荷テンプレートかどうかを返す（保存前の上書き確認を出すかの判定用）。
#[tauri::command]
fn is_shipped_template_path(app: AppHandle, path: String) -> Result<bool, String> {
    let root = repo_root(&app)?;
    let abs = normalize_path(&path)?;
    Ok(is_shipped_template(&root, &abs))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(CoreProc(Mutex::new(None)))
        .manage(PickedPaths(Mutex::new(HashSet::new())))
        // ドラッグ＆ドロップのパスは OS のイベントから直接受け取る（issue S-N1）。
        // webview 側（RunScreen.tsx の onDragDropEvent）は同じドロップを受けて
        // 入力欄の表示を更新するだけで、白リストへの登録には関与しない——
        // webview から任意パスを登録できる経路（旧 remember_dropped_path）は
        // PickedPaths の前提そのものを崩すため削除した。
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::DragDrop(
                tauri::DragDropEvent::Drop { paths, .. }) = event
            {
                remember_dropped(&window.state::<PickedPaths>(), paths);
            }
        })
        // opener プラグインは撤去した（issue #49）。gui/src からの呼び出しは
        // 0 件で、IPC 経由で OS のブラウザ・エクスプローラを起動できる分
        // CSP の connect-src では止められない外部送出経路になっていた
        .invoke_handler(tauri::generate_handler![
            run_core,
            run_core_capture,
            kill_core,
            pick_folder,
            pick_image,
            pick_json,
            read_default_template,
            open_folder,
            read_config,
            write_config,
            read_file_b64,
            read_text,
            write_text,
            write_template_staged,
            promote_template,
            discard_staged,
            is_shipped_template_path
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::check_args_v2;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn allows_operational_subcommands() {
        for c in ["run", "render", "remap", "status", "verify",
                  "detect-grid", "import-credentials"] {
            assert!(check_args_v2(&v(&[c])).is_ok(), "{c}");
        }
    }

    #[test]
    fn denies_purge_and_unknown_and_empty() {
        assert!(check_args_v2(&v(&["purge", "--yes"])).is_err());
        assert!(check_args_v2(&v(&["--config", "x", "run"])).is_err());
        assert!(check_args_v2(&v(&[])).is_err());
    }

    // --- サブコマンドごとのフラグ表検査（issue #52 M-7・S-MD）---

    #[test]
    fn accepts_known_flags_with_values_and_returns_pairs() {
        let pairs = check_args_v2(&v(&["run", "--input", "C:\\in", "--template", "C:\\t.json"]))
            .expect("既知フラグは通るはず");
        assert_eq!(pairs, vec![
            ("--input".to_string(), "C:\\in".to_string()),
            ("--template".to_string(), "C:\\t.json".to_string()),
        ]);
        // --flag=value（等号形式）も同じ結果になる
        let eq = check_args_v2(&v(&["run", "--input=C:\\in"])).unwrap();
        assert_eq!(eq, vec![("--input".to_string(), "C:\\in".to_string())]);
    }

    #[test]
    fn rejects_unknown_flag_for_subcommand() {
        // --input は run のフラグ表にはあるが status には無い
        assert!(check_args_v2(&v(&["status", "--input", "x"])).is_err());
        assert!(check_args_v2(&v(&["run", "--not-a-real-flag"])).is_err());
    }

    #[test]
    fn rejects_missing_value_for_value_taking_flag() {
        assert!(check_args_v2(&v(&["run", "--input"])).is_err());
    }

    #[test]
    fn rejects_replay_and_resend_on_template_change_on_every_subcommand() {
        // #52 M-7: どちらも cli.py の run には実在するフラグだが、GUI 境界からは
        // 常に禁止（許可表に載っていないだけでなく、明示的な拒否リストで守る）
        assert!(check_args_v2(&v(&["run", "--input", "x", "--replay", "C:\\dir"])).is_err());
        assert!(check_args_v2(&v(&["run", "--resend-on-template-change"])).is_err());
        assert!(check_args_v2(&v(&["run", "--replay=C:\\dir"])).is_err());
    }

    #[test]
    fn boolean_flag_rejects_an_attached_value() {
        // --no-mask は値を取らない
        assert!(check_args_v2(&v(&["expand-page", "--input", "x", "--no-mask"])).is_ok());
        assert!(check_args_v2(&v(&["expand-page", "--input", "x", "--no-mask=1"])).is_err());
    }

    #[test]
    fn import_credentials_accepts_one_positional_json_path() {
        let pairs = check_args_v2(&v(&["import-credentials", "C:\\key.json"])).unwrap();
        assert_eq!(pairs, vec![("json_path".to_string(), "C:\\key.json".to_string())]);
        // 他のサブコマンドは位置引数を持たない
        assert!(check_args_v2(&v(&["status", "C:\\key.json"])).is_err());
    }

    #[test]
    fn detect_grid_accepts_its_full_flag_set() {
        let pairs = check_args_v2(&v(&["detect-grid", "--image", "C:\\p.png",
                                       "--region", "1,2,3,4", "--mode", "ruled",
                                       "--rows", "2", "--cols", "3", "--dpi", "300"])).unwrap();
        assert_eq!(pairs.len(), 6);
    }

    // --- パススコープ（issue #49・S-MD）---
    use super::{check_scope, check_scope_dir, normalize_path};
    use std::collections::HashSet;
    use std::path::{Path, PathBuf};

    #[test]
    fn normalize_rejects_parent_traversal() {
        // 実在パスの途中に .. を挟む形（canonicalize なら畳めてしまう）
        assert!(normalize_path("C:\\app\\..\\secret\\cred.json").is_err());
        assert!(normalize_path("../secret.json").is_err());
        assert!(normalize_path("C:\\app\\templates\\..\\..\\x.json").is_err());
    }

    #[test]
    fn normalize_resolves_existing_dir() {
        // 実在するフォルダは畳める（保存先の新規ファイルもここを経由する）
        let tmp = std::env::temp_dir();
        let target = tmp.join("chouhyo_scope_test.json");
        let abs = normalize_path(&target.to_string_lossy()).expect("親が実在すれば解決できる");
        assert_eq!(abs.file_name().unwrap(), "chouhyo_scope_test.json");
    }

    #[test]
    fn scope_allows_only_listed_extensions() {
        let roots = vec![PathBuf::from("C:\\app")];
        let picked = HashSet::new();
        assert!(check_scope(&PathBuf::from("C:\\app\\t.json"), &["json"], &roots, &picked).is_ok());
        assert!(check_scope(&PathBuf::from("C:\\app\\t.JSON"), &["json"], &roots, &picked).is_ok());
        assert!(check_scope(&PathBuf::from("C:\\app\\t.exe"), &["json"], &roots, &picked).is_err());
        assert!(check_scope(&PathBuf::from("C:\\app\\cred"), &["json"], &roots, &picked).is_err());
        // 画像コマンドが SQLite や資格情報を読めないこと
        assert!(check_scope(&PathBuf::from("C:\\app\\workdir\\intermediate.sqlite"),
                            &["png", "jpg", "jpeg"], &roots, &picked).is_err());
        assert!(check_scope(&PathBuf::from("C:\\app\\workdir\\cred.dpapi"),
                            &["png", "jpg", "jpeg"], &roots, &picked).is_err());
    }

    #[test]
    fn scope_rejects_outside_roots_unless_picked() {
        let roots = vec![PathBuf::from("C:\\app")];
        let outside = PathBuf::from("C:\\Users\\u\\Documents\\t.json");
        let mut picked = HashSet::new();
        assert!(check_scope(&outside, &["json"], &roots, &picked).is_err());
        // ダイアログで選ばれた JSON は開ける（テンプレート編集の正当な用途）
        picked.insert(outside.clone());
        assert!(check_scope(&outside, &["json"], &roots, &picked).is_ok());
        // 選ばれていない別ファイルは、同じフォルダでも通らない
        assert!(check_scope(&PathBuf::from("C:\\Users\\u\\Documents\\other.json"),
                            &["json"], &roots, &picked).is_err());
    }

    #[test]
    fn scope_dir_allows_root_or_picked_regardless_of_extension() {
        // issue S-MD: run --input はフォルダ・拡張子なしのファイルもありうるため
        // check_scope（拡張子必須）ではなく check_scope_dir を使う
        let roots = vec![PathBuf::from("C:\\app")];
        let mut picked = HashSet::new();
        assert!(check_scope_dir(&PathBuf::from("C:\\app\\input"), &roots, &picked).is_ok());
        let outside = PathBuf::from("D:\\scans");
        assert!(check_scope_dir(&outside, &roots, &picked).is_err(),
                "選ばれていない/ルート外のフォルダは拒否されるべき");
        picked.insert(outside.clone());
        assert!(check_scope_dir(&outside, &roots, &picked).is_ok(),
                "ダイアログ or D&D 登録で選ばれたフォルダは通る");
    }

    // --- D&D パスの登録（issue S-N1）---
    use super::{remember_dropped, PickedPaths};
    use std::sync::Mutex;

    #[test]
    fn remember_dropped_registers_os_paths_and_skips_unresolvable_ones() {
        // OS のドロップイベントが渡す PathBuf を白リストへ入れる。webview から
        // 任意パスを登録する経路（旧 remember_dropped_path）は削除済み
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_drop_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join("scan.pdf");
        std::fs::write(&f, "x").unwrap();

        let picked = PickedPaths(Mutex::new(HashSet::new()));
        // 2件目は `..` を含み normalize_path が拒否する。1件の不正で
        // 残りを巻き添えにしない（ドロップは複数パスを一度に運ぶ）
        remember_dropped(&picked, &[f.clone(), dir.join("..").join("etc.json")]);

        let set = picked.0.lock().unwrap();
        assert_eq!(set.len(), 1, "不正なパスは登録しない");
        assert!(set.contains(&normalize_path(&f.to_string_lossy()).unwrap()),
                "ドロップされたファイルは正規化して登録される");
        drop(set);

        // フォルダのドロップ（run --input の主用途）も同じ経路で通る
        remember_dropped(&picked, &[dir.clone()]);
        assert!(picked.0.lock().unwrap()
                .contains(&normalize_path(&dir.to_string_lossy()).unwrap()));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- テンプレート既定値の注入（issue #58）---
    use super::inject_default_template;

    #[test]
    fn injects_template_for_accepting_subcommand_without_one() {
        let root = PathBuf::from("C:\\app");
        let out = inject_default_template(v(&["run", "--input", "x"]), &root);
        assert_eq!(out, v(&["run", "--input", "x", "--template",
                            "C:\\app\\templates\\chouhyo-v1.json"]));
    }

    #[test]
    fn does_not_override_explicit_template_or_unrelated_subcommand() {
        let root = PathBuf::from("C:\\app");
        // 明示指定済みなら触らない
        let explicit = v(&["render", "--template", "C:\\other\\t.json"]);
        assert_eq!(inject_default_template(explicit.clone(), &root), explicit);
        // --template を持たないサブコマンドはそのまま（status・detect-grid 等）
        let status = v(&["status"]);
        assert_eq!(inject_default_template(status.clone(), &root), status);
        // 空引数は何もしない（check_args_v2 で先に弾かれる想定だが、単体では防御的に）
        assert_eq!(inject_default_template(v(&[]), &root), v(&[]));
        // --template=path（等号形式）も明示指定として扱う（issue L-3）
        let eq_form = v(&["render", "--template=C:\\other\\t.json"]);
        assert_eq!(inject_default_template(eq_form.clone(), &root), eq_form);
    }

    // --- staged 保存（issue #56 T1・保存経路のトランザクション化）---
    use super::{backup_path, discard_staged_file, is_shipped_template, promote_staged,
                staged_path, validate_template_target};

    #[test]
    fn staged_and_backup_paths_append_suffix() {
        let p = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        assert_eq!(staged_path(&p),
                   PathBuf::from("C:\\app\\templates\\chouhyo-v1.json.saving.json"));
        assert_eq!(backup_path(&p),
                   PathBuf::from("C:\\app\\templates\\chouhyo-v1.json.bak"));
    }

    #[test]
    fn validate_template_target_requires_picked_json() {
        let mut picked = HashSet::new();
        let p = std::env::temp_dir().join("chouhyo_validate_test.json");
        assert!(validate_template_target(&p.to_string_lossy(), &picked).is_err(),
                "picked に無いパスは拒否されるべき");
        picked.insert(normalize_path(&p.to_string_lossy()).unwrap());
        assert!(validate_template_target(&p.to_string_lossy(), &picked).is_ok());

        // 拡張子が json 以外なら picked に入っていても拒否される
        let bad = p.with_extension("txt");
        picked.insert(normalize_path(&bad.to_string_lossy()).unwrap());
        assert!(validate_template_target(&bad.to_string_lossy(), &picked).is_err(),
                "json 以外の拡張子は拒否されるべき");
    }

    #[test]
    fn promote_staged_backs_up_existing_target_and_renames() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("t.json");
        let staged = staged_path(&target);
        let bak = backup_path(&target);
        std::fs::write(&target, "old").unwrap();
        std::fs::write(&staged, "new").unwrap();

        promote_staged(&target).expect("promote は成功するはず");

        assert_eq!(std::fs::read_to_string(&target).unwrap(), "new");
        assert_eq!(std::fs::read_to_string(&bak).unwrap(), "old",
                   "既存ファイルは .bak へ退避されるはず");
        assert!(!staged.exists(), "staged ファイルは rename で消えているはず");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn promote_staged_without_existing_target_skips_backup() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_new_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("t.json");
        let bak = backup_path(&target);
        std::fs::write(&staged_path(&target), "new").unwrap();

        promote_staged(&target).expect("promote は成功するはず");

        assert_eq!(std::fs::read_to_string(&target).unwrap(), "new");
        assert!(!bak.exists(), "初回保存（対象が未作成）ではバックアップを作らない");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn promote_staged_errors_without_staged_file_and_leaves_target_untouched() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_missing_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("t.json");
        std::fs::write(&target, "old").unwrap();

        assert!(promote_staged(&target).is_err(),
                "staged ファイルが無いのに確定できてはいけない");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "old",
                   "失敗時は元ファイルを無傷に保つ（検証NGでファイルが壊れる事故の再発防止）");

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- 確定 rename 失敗時の巻き戻し（マリン最終レビュー H-1）---
    //
    // OS レベルで rename 失敗を確実に誘発する手段が無い（Windows の読み取り
    // 専用属性はファイル作成を妨げず、オープンハンドルの共有モードは
    // プラットフォーム依存）ため、promote_with の rename を注入して固定する。
    // バックアップ（1回目）とロールバック（3回目）は実ファイルへの本物の
    // rename を使い、確定（2回目）だけを合成失敗させることで、
    // 「その他は正常」という現実的な条件で巻き戻し経路を検証する。
    use super::promote_with;

    #[test]
    fn promote_with_rolls_back_bak_when_final_rename_fails() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_rollback_ok_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let abs = dir.join("t.json");
        let staged = staged_path(&abs);
        let bak = backup_path(&abs);
        std::fs::write(&abs, "old").unwrap();
        std::fs::write(&staged, "new").unwrap();

        let mut call = 0;
        let result = promote_with(&staged, &abs, &bak, |from, to| {
            call += 1;
            if call == 2 {
                // 確定（staged→abs）だけを失敗させる。実ファイルには触れない
                return Err(std::io::Error::other("simulated rename failure"));
            }
            std::fs::rename(from, to)
        });

        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("直前の内容へ戻しました"), "{msg}");
        assert_eq!(std::fs::read_to_string(&abs).unwrap(), "old",
                   "ロールバックにより abs は元の内容へ戻っているはず");
        assert!(!bak.exists(), "ロールバック成功時は bak が消えて abs へ戻っているはず");
        assert_eq!(std::fs::read_to_string(&staged).unwrap(), "new",
                   "staged は promote の失敗経路では消えない（新しい編集内容の唯一の控え）");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn promote_with_reports_when_rollback_also_fails() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_rollback_fail_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let abs = dir.join("t.json");
        let staged = staged_path(&abs);
        let bak = backup_path(&abs);
        std::fs::write(&abs, "old").unwrap();
        std::fs::write(&staged, "new").unwrap();

        let mut call = 0;
        let result = promote_with(&staged, &abs, &bak, |from, to| {
            call += 1;
            if call >= 2 {
                // 確定（2回目）とロールバック（3回目）を両方失敗させる
                return Err(std::io::Error::other("simulated failure"));
            }
            std::fs::rename(from, to)
        });

        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(msg.contains("復元にも失敗"), "{msg}");
        assert!(!abs.exists(), "abs はどちらの rename も失敗したので存在しないはず");
        assert_eq!(std::fs::read_to_string(&bak).unwrap(), "old",
                   "old の内容は bak に残っているはず（手動復旧の手がかり）");
        assert_eq!(std::fs::read_to_string(&staged).unwrap(), "new",
                   "staged も消えない（もう1つの手がかり）");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn promote_with_without_backup_mentions_staged_path_only() {
        // 初回保存（abs が元々存在しない）で確定 rename が失敗するケース。
        // 戻す先の bak が無いので、staged の在り処だけを案内する
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_promote_first_save_fail_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let abs = dir.join("t.json");
        let staged = staged_path(&abs);
        let bak = backup_path(&abs);
        std::fs::write(&staged, "new").unwrap();

        let result = promote_with(&staged, &abs, &bak,
            |_from, _to| Err(std::io::Error::other("simulated failure")));

        assert!(result.is_err());
        let msg = result.unwrap_err();
        assert!(!msg.contains("復元"), "バックアップが無いのに復元の話をしている: {msg}");
        assert_eq!(std::fs::read_to_string(&staged).unwrap(), "new");
        assert!(!abs.exists());

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn discard_staged_file_is_idempotent() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_discard_test_{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("t.json");
        let staged = staged_path(&target);
        std::fs::write(&staged, "throwaway").unwrap();

        discard_staged_file(&target).expect("discard は成功するはず");
        assert!(!staged.exists());
        // 既に無い状態で呼んでも失敗しない（verify NG 経路からの二重掃除を許す）
        discard_staged_file(&target).expect("discard は冪等であるべき");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn is_shipped_template_matches_only_repo_templates_path() {
        let root = PathBuf::from("C:\\app");
        let shipped = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        let other = PathBuf::from("C:\\app\\templates\\my-draft.json");
        assert!(is_shipped_template(&root, &shipped));
        assert!(!is_shipped_template(&root, &other));
    }

    // --- config.json のパス安全性判定（issue Q-MC/S-MA）---
    use super::is_safe_root;

    #[test]
    fn is_safe_root_rejects_drive_roots_and_unc_and_blank() {
        assert!(!is_safe_root(&PathBuf::from("C:\\")));
        assert!(!is_safe_root(&PathBuf::from("C:")));
        assert!(!is_safe_root(&PathBuf::from("/")));
        assert!(!is_safe_root(&PathBuf::from("\\\\server\\share")));
        assert!(!is_safe_root(&PathBuf::from("\\\\server\\share\\folder")),
                "UNC は途中にサブフォルダが付いても拒否されるべき");
        assert!(!is_safe_root(&PathBuf::from("")));
        assert!(!is_safe_root(&PathBuf::from("   ")));
    }

    #[test]
    fn is_safe_root_rejects_parent_traversal() {
        assert!(!is_safe_root(&PathBuf::from("C:\\app\\core\\..\\..\\other")));
        assert!(!is_safe_root(&PathBuf::from("..\\..\\foo")));
    }

    #[test]
    fn is_safe_root_accepts_normal_subfolders() {
        assert!(is_safe_root(&PathBuf::from("C:\\app\\workdir")));
        assert!(is_safe_root(&PathBuf::from("C:\\app\\core\\workdir")));
        // 外部ドライブへ向けた構成も許す（仕様: workdir を外部フォルダへ
        // 向けられる・:288-290 のコメントどおり）
        assert!(is_safe_root(&PathBuf::from("D:\\ChouhyoWorkdir")));
    }

    #[test]
    fn is_safe_root_rejects_canonicalized_drive_root(){
        // issue S-N3: allowed_roots は canonicalize の結果にもこの判定を通す。
        // ジャンクション（`C:\app\core\toroot → C:\`）を挟むと生パスは通るのに
        // 畳んだ先がドライブ直下になりうるため、verbatim 形（`\\?\C:\`）を
        // components ベースで拒否できることを固定する
        assert!(!is_safe_root(&PathBuf::from("\\\\?\\C:\\")),
                "canonicalize 後のドライブ直下（verbatim）は拒否されるべき");
        assert!(!is_safe_root(&PathBuf::from("\\\\?\\UNC\\server\\share")),
                "verbatim UNC も拒否されるべき");
        // 正常な canonicalize 結果（Normal 成分あり）は通す
        assert!(is_safe_root(&PathBuf::from("\\\\?\\C:\\app\\workdir\\editor_pages")));
    }

    // --- 読み取りルートの限定（issue S-N4）---
    use super::workdir_pages_dir;

    #[test]
    fn workdir_pages_dir_narrows_root_to_editor_pages() {
        let root = PathBuf::from("C:\\app");
        // 相対 workdir は cwd=core 基準（CLI の流儀）
        assert_eq!(workdir_pages_dir(&root, "workdir"),
                   Some(PathBuf::from("C:\\app\\core\\workdir\\editor_pages")));
        // 絶対 workdir はそのまま
        assert_eq!(workdir_pages_dir(&root, "D:\\ChouhyoWorkdir"),
                   Some(PathBuf::from("D:\\ChouhyoWorkdir\\editor_pages")));
        // workdir 直下（responses/ や cred.dpapi が同居する層）は読み取り
        // ルートに含めない
        let pages = workdir_pages_dir(&root, "workdir").unwrap();
        assert!(!pages.starts_with("C:\\app\\core\\workdir\\responses"));
        assert_ne!(pages, PathBuf::from("C:\\app\\core\\workdir"));
    }

    #[test]
    fn workdir_pages_dir_rejects_unsafe_workdir() {
        let root = PathBuf::from("C:\\app");
        assert_eq!(workdir_pages_dir(&root, "C:\\"), None);
        assert_eq!(workdir_pages_dir(&root, "\\\\server\\share"), None);
        assert_eq!(workdir_pages_dir(&root, "..\\escape"), None);
        assert_eq!(workdir_pages_dir(&root, ""), None);
    }

    // --- 引数の値スコープ検査（issue S-N2）---
    use super::{check_arg_scopes, is_staged_of_picked};

    /// check_arg_scopes 用の実ファイル環境。normalize_path は実在する親を
    /// 要求するため、架空パスでは「解決できない」で常に Err になり検査の
    /// 是非を確かめられない
    struct ScopeFixture {
        base: PathBuf,
        root: PathBuf,
        pages: PathBuf,
        outside: PathBuf,
    }

    impl ScopeFixture {
        fn new(name: &str) -> Self {
            let base = std::env::temp_dir()
                .join(format!("chouhyo_argscope_{name}_{}", std::process::id()));
            let root = base.join("app");
            let pages = base.join("wd").join("editor_pages");
            let outside = base.join("outside");
            for d in [&root, &pages, &outside] {
                std::fs::create_dir_all(d).unwrap();
            }
            std::fs::write(pages.join("page1.png"), "x").unwrap();
            std::fs::write(outside.join("scan.pdf"), "x").unwrap();
            std::fs::write(outside.join("t.json"), "{}").unwrap();
            std::fs::write(outside.join("other.json"), "{}").unwrap();
            Self { base, root, pages, outside }
        }

        fn roots(&self) -> Vec<PathBuf> {
            vec![self.root.canonicalize().unwrap(), self.pages.canonicalize().unwrap()]
        }

        fn abs(&self, p: &Path) -> PathBuf {
            normalize_path(&p.to_string_lossy()).unwrap()
        }
    }

    impl Drop for ScopeFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.base);
        }
    }

    fn pairs(items: &[(&str, &Path)]) -> Vec<(String, String)> {
        items.iter()
            .map(|(f, p)| (f.to_string(), p.to_string_lossy().to_string()))
            .collect()
    }

    #[test]
    fn arg_scopes_reject_expand_page_input_outside_picked_and_roots() {
        // issue S-N2: `expand-page --input <任意.pdf>` → editor_pages に PNG が
        // 出て read_file_b64 で読める連鎖を、入力の時点で止める
        let fx = ScopeFixture::new("input");
        let roots = fx.roots();
        let mut picked = HashSet::new();
        let pdf = fx.outside.join("scan.pdf");

        assert!(check_arg_scopes(&pairs(&[("--input", &pdf)]), &roots, &picked).is_err(),
                "選ばれていないファイルを展開させてはいけない");
        picked.insert(fx.abs(&pdf));
        assert!(check_arg_scopes(&pairs(&[("--input", &pdf)]), &roots, &picked).is_ok(),
                "pick_image で選ばれた PDF は通る（編集画面の正当な用途）");
    }

    #[test]
    fn arg_scopes_allow_detect_grid_image_only_from_editor_pages_or_picked() {
        let fx = ScopeFixture::new("image");
        let roots = fx.roots();
        let mut picked = HashSet::new();
        let page = fx.pages.join("page1.png");
        let outside_png = fx.outside.join("elsewhere.png");

        assert!(check_arg_scopes(&pairs(&[("--image", &page)]), &roots, &picked).is_ok(),
                "expand-page が書いた editor_pages の PNG は通る");
        assert!(check_arg_scopes(&pairs(&[("--image", &outside_png)]), &roots, &picked).is_err());
        picked.insert(fx.abs(&outside_png));
        assert!(check_arg_scopes(&pairs(&[("--image", &outside_png)]), &roots, &picked).is_ok());
        // 拡張子違いは picked でも拒否（画像コマンドで JSON を読ませない）
        let json = fx.outside.join("t.json");
        picked.insert(fx.abs(&json));
        assert!(check_arg_scopes(&pairs(&[("--image", &json)]), &roots, &picked).is_err());
    }

    #[test]
    fn arg_scopes_allow_staged_template_of_a_picked_path() {
        // テンプレート保存フロー: write_template_staged（保存先は picked 限定）が
        // 作った `<picked>.saving.json` を verify へ渡す（Editor.tsx:1723-1740）。
        // この一時ファイルはダイアログを通らないので picked には無い
        let fx = ScopeFixture::new("staged");
        let roots = fx.roots();
        let mut picked = HashSet::new();
        let target = fx.outside.join("t.json");
        picked.insert(fx.abs(&target));
        let staged = super::staged_path(&fx.abs(&target));
        std::fs::write(&staged, "{}").unwrap();

        assert!(check_arg_scopes(&pairs(&[("--template", &staged)]), &roots, &picked).is_ok(),
                "保存フローの一時ファイルは拒否されてはいけない");
        assert!(check_arg_scopes(&pairs(&[("--template", &target)]), &roots, &picked).is_ok(),
                "picked 本体もこれまでどおり通る");

        // 本体が picked に無い `.saving.json` は通さない（任意ファイルを
        // `.saving.json` という名前で読ませる抜け道を作らない）
        let orphan = super::staged_path(&fx.abs(&fx.outside.join("other.json")));
        std::fs::write(&orphan, "{}").unwrap();
        assert!(check_arg_scopes(&pairs(&[("--template", &orphan)]), &roots, &picked).is_err());
        assert!(!is_staged_of_picked(&fx.abs(&orphan), &picked));
    }

    #[test]
    fn arg_scopes_ignore_flags_without_paths() {
        let fx = ScopeFixture::new("other");
        let roots = fx.roots();
        let picked = HashSet::new();
        let flags = vec![("--region".to_string(), "1,2,3,4".to_string()),
                         ("--mode".to_string(), "uniform".to_string()),
                         ("--dpi".to_string(), "300".to_string()),
                         // import-credentials の鍵は picked へ入れない設計
                         // （pick_json remember_pick=false）なので検査対象外
                         ("json_path".to_string(), "C:\\key.json".to_string())];
        assert!(check_arg_scopes(&flags, &roots, &picked).is_ok());
    }

    // --- write_config のパッチ検証（issue Q-MC/S-MA）---
    use super::validate_config_patch;
    use serde_json::json;

    #[test]
    fn validate_config_patch_rejects_unknown_keys() {
        assert!(validate_config_patch(&json!({"unclear_threshold": 0.9})).is_ok());
        assert!(validate_config_patch(&json!({"totally_unknown_key": 1})).is_err(),
                "未知キーを通すと core 側の load_config が ConfigError で全コマンド起動不能になる");
    }

    #[test]
    fn validate_config_patch_checks_path_keys_only() {
        for key in ["output_dir", "workdir", "log_dir"] {
            assert!(validate_config_patch(&json!({key: "output"})).is_ok(), "{key}: 通常の相対パス");
            assert!(validate_config_patch(&json!({key: "C:\\"})).is_err(), "{key}: ドライブ直下");
            assert!(validate_config_patch(&json!({key: ""})).is_err(), "{key}: 空文字");
            assert!(validate_config_patch(&json!({key: "\\\\server\\share"})).is_err(), "{key}: UNC");
            assert!(validate_config_patch(&json!({key: "..\\escape"})).is_err(), "{key}: 親traversal");
        }
        // パス検証対象でないキーは触らない（数値の範囲検証は core 側の役割のまま）
        assert!(validate_config_patch(&json!({"send_limit": -1})).is_ok(),
                "型/範囲検証は意図的に core 側に一本化している（設計判断）");
    }

    #[test]
    fn validate_config_patch_rejects_non_string_path_value() {
        assert!(validate_config_patch(&json!({"workdir": 123})).is_err());
    }

    // --- 壊れた config.json を空扱いにしない（issue N-3）---
    use super::merge_config;

    #[test]
    fn merge_config_keeps_untouched_keys() {
        let existing = r#"{"workdir":"D:\\wd","send_limit":50,"api_monthly_cap":300}"#;
        let merged = merge_config(Some(existing), &json!({"output_dir": "out"})).unwrap();
        assert_eq!(merged["output_dir"], json!("out"));
        assert_eq!(merged["workdir"], json!("D:\\wd"), "patch 外のキーは保持する");
        assert_eq!(merged["send_limit"], json!(50));
        assert_eq!(merged["api_monthly_cap"], json!(300));
    }

    #[test]
    fn merge_config_creates_file_content_when_absent() {
        let merged = merge_config(None, &json!({"output_dir": "out"})).unwrap();
        assert_eq!(merged, json!({"output_dir": "out"}));
    }

    #[test]
    fn merge_config_refuses_to_rebuild_from_broken_file() {
        // 以前は unwrap_or(json!({})) で空から作り直し、workdir・send_limit を
        // 黙って既定へ戻したうえで「保存しました」と表示していた
        let err = merge_config(Some("{ broken json"), &json!({"output_dir": "out"}))
            .expect_err("パースできない設定を空扱いにしてはいけない");
        assert!(err.contains("読めないため保存できません"), "{err}");
        // トップレベルがオブジェクトでない場合も同様（patch がどこにも
        // 入らないまま成功扱いになるのを防ぐ）
        assert!(merge_config(Some("[1,2]"), &json!({"output_dir": "out"})).is_err());
        assert!(merge_config(Some("\"just a string\""), &json!({"output_dir": "out"})).is_err());
    }

    #[test]
    fn merge_config_still_validates_the_patch_first() {
        assert!(merge_config(Some("{}"), &json!({"totally_unknown_key": 1})).is_err());
        assert!(merge_config(Some("{}"), &json!({"workdir": "C:\\"})).is_err());
    }

    // --- PID スロットの解放（issue Q-MB）---
    use super::release_slot;

    #[test]
    fn release_slot_clears_only_when_pid_matches() {
        let mut slot = Some(111u32);
        release_slot(&mut slot, 111);
        assert_eq!(slot, None);
    }

    #[test]
    fn release_slot_does_not_clobber_a_different_pid() {
        // 中断ボタンが take() → 別の run が新しい pid を登録した後に、
        // 元の run の後始末が来ても新しい pid を消してはいけない
        let mut slot = Some(222u32);
        release_slot(&mut slot, 111);
        assert_eq!(slot, Some(222), "自分の pid でないときは触らない");
    }

    #[test]
    fn release_slot_is_noop_when_already_empty() {
        let mut slot: Option<u32> = None;
        release_slot(&mut slot, 111);
        assert_eq!(slot, None);
    }
}
