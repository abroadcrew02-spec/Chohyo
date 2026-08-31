// 帳票OCRツール GUI シェル（設計 §3.1・§7）。
// 処理ロジックを持たない: Python コアの起動・進捗中継・ファイル読み書きに徹する。
use std::collections::HashSet;
use std::io::{BufRead, BufReader};
use std::path::{Component, Path, PathBuf};
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

fn check_args(args: &[String]) -> Result<(), String> {
    match args.first() {
        Some(c) if ALLOWED_SUBCOMMANDS.contains(&c.as_str()) => Ok(()),
        Some(c) => Err(format!("許可されていないコマンド: {c}")),
        None => Err("コマンドが指定されていない".into()),
    }
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
    if accepts && !args.iter().any(|a| a == "--template") {
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

/// ダイアログを介さずに読み書きしてよいフォルダ。アプリルートに加えて、
/// 設定の workdir も含める（編集画面の PDF 展開結果はここに出る。
/// workdir を外部フォルダへ向けた構成でもプレビューを壊さない）。
fn allowed_roots(app: &AppHandle) -> Result<Vec<PathBuf>, String> {
    let root = repo_root(app)?;
    let mut roots = vec![root.canonicalize().unwrap_or_else(|_| root.clone())];
    let workdir = read_config(app.clone())
        .ok()
        .and_then(|c| c.get("workdir").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_else(|| "workdir".to_string());
    let p = PathBuf::from(&workdir);
    // CLI の相対パス設定は cwd=core 基準（open_folder と同じ流儀）
    let abs = if p.is_absolute() { p } else { root.join("core").join(p) };
    if let Ok(c) = abs.canonicalize() {
        if !roots.contains(&c) {
            roots.push(c);
        }
    }
    Ok(roots)
}

/// ダイアログで選ばれたパスを白リストへ登録する。
fn remember(picked: &State<'_, PickedPaths>, p: &Path) {
    if let Ok(abs) = normalize_path(&p.to_string_lossy()) {
        picked.0.lock().unwrap().insert(abs);
    }
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

/// コアを起動し stdout(JSON Lines)/stderr を行単位でイベント中継、終了コードを返す。
#[tauri::command]
async fn run_core(app: AppHandle, state: State<'_, CoreProc>,
                  args: Vec<String>) -> Result<i32, String> {
    check_args(&args)?;
    let root = repo_root(&app)?;
    let args = inject_default_template(args, &root);
    let mut cmd = core_command(&root)?;
    cmd.args(&args).stdout(Stdio::piped()).stderr(Stdio::piped());
    // 2本目を断る（レビュー M-2）。以前は PID を上書きしていたため、2本目が
    // 終わった時点で 1本目の PID を見失い「中断」ボタンが効かなくなった。
    // コア側の実行ロックは同一保存先への二重送信を防ぐが、こちらの取り違えは
    // 防げない。判定〜登録の間に割り込まれないよう spawn までロックを持つ。
    let (mut child, pid) = {
        let mut slot = state.0.lock().unwrap();
        if slot.is_some() {
            return Err("すでに読み取りを実行中です。完了するか中断してください".into());
        }
        let c = cmd.spawn().map_err(|e| format!("コア起動に失敗: {e}"))?;
        let pid = c.id();
        *slot = Some(pid);
        (c, pid)
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
    // 自分の PID のときだけ消す。中断ボタンが take() した後に上書きで復活させない
    let mut slot = state.0.lock().unwrap();
    if *slot == Some(pid) {
        *slot = None;
    }
    drop(slot);
    Ok(status.code().unwrap_or(-1))
}

/// 実行中のコアを子プロセス（pdftoppm 等）ごと停止する。中断分は
/// 「未処理（中断）」として出力され、次回 run で続きから再開する（要件 §5.8）。
#[tauri::command]
fn kill_core(state: State<'_, CoreProc>) -> Result<(), String> {
    let pid = state.0.lock().unwrap().take().ok_or("実行中の処理がありません")?;
    let mut c = Command::new("taskkill");
    c.args(["/T", "/F", "/PID", &pid.to_string()]);
    #[cfg(windows)]
    c.creation_flags(CREATE_NO_WINDOW);
    let out = c.output().map_err(|e| e.to_string())?;
    if out.status.success() { Ok(()) } else { Err("停止できませんでした".into()) }
}

/// コアを起動し stdout を丸ごと返す（編集画面の detect-grid / verify 用）。
#[tauri::command]
async fn run_core_capture(app: AppHandle, args: Vec<String>) -> Result<String, String> {
    check_args(&args)?;
    let root = repo_root(&app)?;
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
fn pick_folder() -> Option<String> {
    rfd::FileDialog::new()
        .pick_folder()
        .map(|p| p.to_string_lossy().to_string())
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

/// 設定の部分更新（要件 §5.7: GUI で選んだ値を保存し次回既定値に）。他キーは保持する。
#[tauri::command]
fn write_config(app: AppHandle, patch: serde_json::Value) -> Result<(), String> {
    let p = config_file(&app)?;
    let mut cur = if p.exists() {
        serde_json::from_str::<serde_json::Value>(
            &std::fs::read_to_string(&p).map_err(|e| e.to_string())?,
        )
        .unwrap_or(serde_json::json!({}))
    } else {
        serde_json::json!({})
    };
    if let (Some(obj), Some(add)) = (cur.as_object_mut(), patch.as_object()) {
        for (k, v) in add {
            obj.insert(k.clone(), v.clone());
        }
    }
    std::fs::write(&p, serde_json::to_string_pretty(&cur).map_err(|e| e.to_string())?)
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
    s.push(".saving.json");
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

/// staged ファイルを本番パスへ確定する（issue #56 T1・保存経路のトランザクション化）。
/// 既存ファイルがあれば `.bak` へ退避してから rename するため、検証 NG のまま
/// 出荷テンプレートが上書きされることも、確定に失敗して両方消えることも無い。
fn promote_staged(abs: &Path) -> Result<(), String> {
    let staged = staged_path(abs);
    if !staged.exists() {
        return Err("一時保存ファイルが見つかりません（先に保存を実行してください）".into());
    }
    if abs.exists() {
        let bak = backup_path(abs);
        std::fs::rename(abs, &bak).map_err(|e| format!("バックアップの作成に失敗: {e}"))?;
    }
    std::fs::rename(&staged, abs).map_err(|e| format!("保存の確定に失敗: {e}"))
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
    use super::check_args;

    fn v(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn allows_operational_subcommands() {
        for c in ["run", "render", "remap", "status", "verify",
                  "detect-grid", "import-credentials"] {
            assert!(check_args(&v(&[c])).is_ok(), "{c}");
        }
    }

    #[test]
    fn denies_purge_and_unknown_and_empty() {
        assert!(check_args(&v(&["purge", "--yes"])).is_err());
        assert!(check_args(&v(&["--config", "x", "run"])).is_err());
        assert!(check_args(&v(&[])).is_err());
    }

    // --- パススコープ（issue #49）---
    use super::{check_scope, normalize_path};
    use std::collections::HashSet;
    use std::path::PathBuf;

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
        // 空引数は何もしない（check_args で先に弾かれる想定だが、単体では防御的に）
        assert_eq!(inject_default_template(v(&[]), &root), v(&[]));
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
}
