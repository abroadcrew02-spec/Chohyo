// 帳票OCRツール GUI シェル（設計 §3.1・§7）。
// 処理ロジックを持たない: Python コアの起動・進捗中継・ファイル読み書きに徹する。
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

/// 実行中のコアの PID（中断ボタン用・同時実行は1つの前提）
pub struct CoreProc(pub Mutex<Option<u32>>);

/// webview から起動できるサブコマンドの白リスト（issue #7）。
/// purge（中間データ全削除）は要件 §6.3「削除は明示操作のみ」のため
/// GUI 境界からは呼べない——必要なら CLI を直接使う。
const ALLOWED_SUBCOMMANDS: &[&str] = &[
    "run", "render", "remap", "status", "verify", "detect-grid",
    "import-credentials",
];

fn check_args(args: &[String]) -> Result<(), String> {
    match args.first() {
        Some(c) if ALLOWED_SUBCOMMANDS.contains(&c.as_str()) => Ok(()),
        Some(c) => Err(format!("許可されていないコマンド: {c}")),
        None => Err("コマンドが指定されていない".into()),
    }
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
    for start in starts {
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
    let mut cmd = core_command(&root)?;
    cmd.args(&args).stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = cmd.spawn().map_err(|e| format!("コア起動に失敗: {e}"))?;
    *state.0.lock().unwrap() = Some(child.id());

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
    *state.0.lock().unwrap() = None;
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
fn pick_image() -> Option<String> {
    rfd::FileDialog::new()
        .add_filter("画像", &["png", "jpg", "jpeg"])
        .pick_file()
        .map(|p| p.to_string_lossy().to_string())
}

#[tauri::command]
fn pick_json(save: bool) -> Option<String> {
    let d = rfd::FileDialog::new().add_filter("テンプレート", &["json"]);
    let p = if save { d.set_file_name("template.json").save_file() } else { d.pick_file() };
    p.map(|p| p.to_string_lossy().to_string())
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
#[tauri::command]
fn read_file_b64(path: String) -> Result<String, String> {
    use base64::Engine;
    let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
    let ext = std::path::Path::new(&path)
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("png")
        .to_lowercase();
    let mime = if ext == "jpg" || ext == "jpeg" { "image/jpeg" } else { "image/png" };
    Ok(format!("data:{};base64,{}",
               mime,
               base64::engine::general_purpose::STANDARD.encode(bytes)))
}

#[tauri::command]
fn read_text(path: String) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

#[tauri::command]
fn write_text(path: String, content: String) -> Result<(), String> {
    std::fs::write(path, content).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(CoreProc(Mutex::new(None)))
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            run_core,
            run_core_capture,
            kill_core,
            pick_folder,
            pick_image,
            pick_json,
            open_folder,
            read_config,
            write_config,
            read_file_b64,
            read_text,
            write_text
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
}
