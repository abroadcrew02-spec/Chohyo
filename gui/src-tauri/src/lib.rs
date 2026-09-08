// 帳票OCRツール GUI シェル（設計 §3.1・§7）。
// 処理ロジックを持たない: Python コアの起動・進捗中継・ファイル読み書きに徹する。
use std::collections::HashSet;
use std::io::{BufRead, BufReader};
use std::path::{Component, Path, PathBuf, Prefix};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tauri::{AppHandle, Emitter, Manager, State};

mod user_templates;

/// 実行中のコアの PID（中断ボタン用・同時実行は1つの前提）
pub struct CoreProc(pub Mutex<Option<u32>>);

/// ファイル選択ダイアログで利用者が実際に選んだパス（issue #49）。
/// webview は任意のパスで invoke できるため、ファイル読み書きの許可は
/// 「ここに登録された物」または「アプリ管理下のフォルダ」に限る。
/// テンプレート編集は任意の場所の JSON を開ける必要があるので、
/// 白リストをルート固定にはせずダイアログの選択結果で広げる。
pub struct PickedPaths(pub Mutex<HashSet<PathBuf>>);

/// `import-credentials` の位置引数 `json_path` が通ってよい1件だけを覚える
/// 1回限りのスロット（issue #121）。`pick_json(kind:"credentials")` が選択
/// 結果をここへ書き込み、`check_credentials_scope`（`check_arg_scopes` から
/// 呼ぶ）が値の一致を確認したうえで消費して空にする——一致しなければ拒否、
/// 一致しても2回目は通らない。`PickedPaths` には意図的に入れない
/// （remember_pick=false のまま・鍵の平文 JSON を `read_text` で読める窓を
/// 開けないという既存の設計判断はそのまま維持する）。
pub struct PendingCredentialsPath(pub Mutex<Option<PathBuf>>);

/// ドロップを受け付ける画面が表示されているか（issue #69 セキュリティ LOW (b)）。
///
/// `on_window_event` の DragDrop は**タブに関係なく**発火するため、
/// ドロップを受ける画面（実行画面）が隠れている間に落としたファイルまで
/// `PickedPaths` へ登録されていた。画面には何も出ないので、利用者からは
/// 「読み書きを許すパスが増えた」ことが分からない（不可視の権限拡大）。
/// フロント（App.tsx）がタブ切替のたびに `set_drop_active` で更新する。
///
/// 既定は true——起動直後は実行画面が表示されており、フロントが一度も
/// 伝えてこない場合でも従来どおり動く（伝達漏れで機能が壊れる側へは
/// 倒さない）。webview がこの値を偽れても、実際のドロップという利用者の
/// 物理操作なしにはパスは増えない。
pub struct DropActive(pub Mutex<bool>);

/// webview から起動できるサブコマンドの白リスト（issue #7）。
///
/// purge（中間データの削除）は issue #52 M-11 で追加した。要件 §6.3
/// 「削除は明示操作のみ」は「GUI から呼べないこと」ではなく「利用者の
/// 明示操作以外では走らないこと」を求めている——実行画面の二段確認
/// （何が消えて何が残るかの説明 → 最終確認）がその明示操作にあたる。
/// 削除手段が CLI にしか無い状態こそが、中間データ（要配慮個人情報）を
/// 無期限に溜め続ける原因になっていた。受け付けるフラグは
/// `--yes`・`--include-output`・`--preview` だけに限る（`allowed_flags`）。
/// `--preview`（issue #108）はコア側が削除を実行せず対象パス・対象外件数を
/// 1行イベントで返すだけのフラグで、二段確認の説明文に実際の削除対象を
/// 出すために使う。
const ALLOWED_SUBCOMMANDS: &[&str] = &[
    "run", "render", "remap", "status", "verify", "detect-grid",
    "expand-page", "import-credentials", "detect-frames", "purge",
];

/// フラグ（または `import-credentials` の位置引数 `json_path`）が値に取る
/// パスの種別（issue #129・旧 M-4）。
///
/// 以前は `allowed_flags` が「値を取るか（`bool`）」しか持たず、`check_arg_scopes`
/// はフラグ名ごとの match でパス検査の要否を個別に判定していた。既定の腕
/// （`_ => value.clone()`）が「検査しない」を意味していたため、パス系フラグを
/// `allowed_flags` に足しても `check_arg_scopes` の腕を足し忘れると無検査の値が
/// そのまま子プロセスへ渡った（過去に2回発生: #52 M-7 は argv[0] しか見ていな
/// かった／S-N2 は `expand-page --input` が未検査だった）。
///
/// この型を `allowed_flags` の値に持たせることで、`check_arg_scopes` は種別の
/// match になる。列挙型の match は非網羅だとコンパイルが落ちるため、新しい
/// 種別を足したのに対応する腕を書き忘れるとビルドが失敗する——書き忘れが
/// レビュー頼みではなく型検査で止まる。
#[derive(Clone, Copy, PartialEq, Eq)]
enum ArgKind {
    /// 値を取らないフラグ（例: `--yes`）。
    None,
    /// フォルダ、または拡張子を問わない入力（`check_scope_dir` で検査。例: `--input`）。
    Dir,
    /// 拡張子を絞ったファイル（`check_scope` で検査。許す拡張子の一覧を持つ。例: `--image`）。
    File(&'static [&'static str]),
    /// `--template`。JSON かつ、保存フローが作る `<picked>.saving.json` の
    /// 例外を持つ（`check_template_scope`）。
    Template,
    /// `import-credentials` の鍵ファイル（位置引数 `json_path`）。
    /// `PendingCredentialsPath` の1回限りのスロットと照合する
    /// （`check_credentials_scope`）。
    Credentials,
    /// パスではない値（例: `--dpi`・`--page`・`--region`・`--mode`・`--rows`・
    /// `--cols`・`--expect-columns`）。スコープ検査はせずそのまま通す。
    Opaque,
}

impl ArgKind {
    /// `None`（値を取らないフラグ）以外はすべて値を取る。
    fn takes_value(self) -> bool {
        !matches!(self, ArgKind::None)
    }
}

/// サブコマンドが受け付けるフラグと、その値の種別（`ArgKind`）の対応表
/// （issue #52 M-7・#129）。表に無いフラグは拒否する。`--replay`・
/// `--resend-on-template-change` は cli.py の run が実際に持つフラグだが、
/// 意図的にどのサブコマンドの表にも入れていない——要配慮個人情報の再送・
/// 任意ディレクトリでの再生は GUI 境界からは常に禁止し、CLI 直叩き限定に
/// する（S-MD 方針・#52 M-7 の対応方針どおり）。
///
/// **フラグを足すときは値の種別（`ArgKind`）を書くこと。** パスを扱うフラグ
/// なら `Dir`／`File(exts)`／`Template`／`Credentials` のどれかを選ぶ——
/// `Opaque` を安易に選ぶと #129 と同じ「検査漏れ」になる。種別さえ正しく書け
/// ば、`check_arg_scopes`・`rebuild_args` はこの表から挙動を導出するので
/// 個別に対応漏れが起きない。
fn allowed_flags(subcommand: &str) -> &'static [(&'static str, ArgKind)] {
    const IMAGE_EXTS: &[&str] = &["png", "jpg", "jpeg"];
    match subcommand {
        "run" => &[("--input", ArgKind::Dir), ("--template", ArgKind::Template)],
        "render" | "remap" => &[("--template", ArgKind::Template)],
        "status" => &[],
        "verify" => &[("--template", ArgKind::Template), ("--expect-columns", ArgKind::Opaque)],
        "detect-grid" => &[
            ("--image", ArgKind::File(IMAGE_EXTS)), ("--region", ArgKind::Opaque),
            ("--mode", ArgKind::Opaque), ("--rows", ArgKind::Opaque),
            ("--cols", ArgKind::Opaque), ("--dpi", ArgKind::Opaque),
        ],
        "expand-page" => &[
            ("--input", ArgKind::Dir), ("--page", ArgKind::Opaque), ("--dpi", ArgKind::Opaque),
            ("--template", ArgKind::Template), ("--no-mask", ArgKind::None),
        ],
        // detect-frames は #73 (b) のページ全体からの枠候補生成(新しい権限は要らない・
        // run_core_capture 経由・--input は既存の読み取りルート検査に従う)。
        "detect-frames" => &[
            ("--input", ArgKind::Dir), ("--page", ArgKind::Opaque),
            ("--dpi", ArgKind::Opaque), ("--template", ArgKind::Template),
        ],
        // import-credentials は位置引数 json_path（check_args_v2 内で別扱い。値
        // そのものは check_arg_scopes → check_credentials_scope が
        // PendingCredentialsPath の1回限りのスロットと照合する・issue #121）を
        // Credentials 種別として表に持つ（位置引数の扱い自体は check_args_v2・
        // rebuild_args の json_path 分岐が引き続き担う。ここでは種別の一元管理
        // のためだけに載せる）。加えて --delete-source（issue #52 M-10）。
        // 取り込みに成功したら元の平文鍵 JSON をランダム上書きのうえ削除する
        // フラグで、GUI からは既定で付ける——取り込みのたびに平文の秘密鍵が
        // ディスクへ残るのを止める。
        "import-credentials" => &[
            ("json_path", ArgKind::Credentials), ("--delete-source", ArgKind::None),
        ],
        // purge は値を取らない3つだけ（issue #52 M-11・S-MC・#108）。--config
        // のような「どこを消すか」を差し替えられるフラグは絶対に足さない——
        // 消す対象は config.json の workdir/output_dir だけ、という不変条件で
        // 二段確認の説明文（何が消えるか）と実際の削除範囲を一致させている。
        // `--preview`（issue #108）は削除を実行せず対象パス・対象外件数を
        // 1行返すだけで、`--yes` と同時指定してもコア側が preview を優先し
        // 削除は起きない（core/chouhyo_ocr/cli.py 側の実装）。
        "purge" => &[
            ("--yes", ArgKind::None), ("--include-output", ArgKind::None),
            ("--preview", ArgKind::None),
        ],
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
            if !spec.1.takes_value() {
                return Err(format!("値を取らない引数に値が指定されています: {name}"));
            }
            pairs.push((spec.0.to_string(), value.to_string()));
            i += 1;
        } else if a.starts_with("--") {
            // json_path（import-credentials の位置引数）は表に載っているが
            // "--" で始まらないため、ここでは絶対に一致しない——位置引数の値が
            // たまたま文字列 "json_path" と一致しても、この腕でフラグとして
            // 誤消費されることはない。
            let spec = specs.iter().find(|s| s.0 == a)
                .ok_or_else(|| format!("許可されていない引数です: {a}"))?;
            if spec.1.takes_value() {
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
            // 位置引数（json_path）。他のサブコマンドは位置引数を持たない。
            // 2つ目以降は消費が起きる前にここで拒否する（issue #126 L-3）
            // ——check_arg_scopes まで通してしまうと、1つ目が
            // PendingCredentialsPath の一回限りのスロットと一致して
            // 消費された「後」に2つ目で全体が Err になり、正当な選択の
            // トークンだけが失われて二度と使えなくなる
            if pairs.iter().any(|(f, _)| f == "json_path") {
                return Err("import-credentials は位置引数を1つしか受け付けません".into());
            }
            pairs.push(("json_path".to_string(), a.to_string()));
            i += 1;
        } else {
            return Err(format!("許可されていない引数です: {a}"));
        }
    }
    Ok(pairs)
}

/// `check_arg_scopes` が返した（スコープ検査済み・パス値は正規化済みの）
/// pairs から、子プロセスへ渡す argv を組み立て直す（issue #126 (2)）。
///
/// 検査前の生の `args` をそのまま子へ渡すと、相対パスの解決基準が検査時
/// （この関数を呼ぶ GUI プロセスの cwd）と実行時（子プロセスは
/// `<root>/core` を cwd に起動する・`core_command`）でずれうる——検査した
/// 対象と実際に開かれる対象が一致する保証がなくなる。ここで作り直す argv は
/// 常に絶対パス（`normalize_path` の結果）を積むため、その基準差が消える。
///
/// 値を取るかどうかは `pairs` の値の空・非空では判定しない
/// （`--dpi ""` のような値そのものが空の正当な入力と、値を持たないフラグ
/// を区別できないため）。`allowed_flags(cmd)` の表（`ArgKind::takes_value`）を
/// 単一の正とする——表に無いフラグに出会ったら `Err` にする（issue #126 L-2）。
/// `check_args_v2` が通した pairs しかここには来ない想定なので実行時には
/// 起こらないはずだが、将来 `allowed_flags` と `check_args_v2` の対応が崩れた
/// ときに「値の有無を勝手に決めて渡してしまう」より安全に倒す。
///
/// `json_path`（import-credentials の位置引数）だけは表の `ArgKind::Credentials`
/// を見ずに flag 名で直接分岐する——位置引数はフラグトークン自体を出力しない
/// （値だけを push する）という argv 構築上の扱いの違いであって、値種別の
/// 検査は `check_arg_scopes` 側の役目（issue #129）。
///
/// `purge --preview --yes` は `--yes` を落とす（issue #126 M-5）。core 側が
/// preview を優先する実装であっても、GUI 側で「削除は起きない」ことを argv
/// の時点で保証し、コアの実装順序に依存しない。
fn rebuild_args(cmd: &str, pairs: &[(String, String)]) -> Result<Vec<String>, String> {
    let specs = allowed_flags(cmd);
    let drop_yes = cmd == "purge" && pairs.iter().any(|(f, _)| f == "--preview");
    let mut out = vec![cmd.to_string()];
    for (flag, value) in pairs {
        if drop_yes && flag == "--yes" {
            continue;
        }
        if flag == "json_path" {
            // import-credentials の位置引数（フラグトークンは出さず値だけ積む）
            out.push(value.clone());
            continue;
        }
        let kind = specs.iter().find(|s| &s.0 == flag).map(|s| s.1)
            .ok_or_else(|| format!("内部エラー: 未知の引数です: {flag}"))?;
        out.push(flag.clone());
        if kind.takes_value() {
            out.push(value.clone());
        }
    }
    Ok(out)
}

/// `--template` を受け付けるサブコマンド（core/chouhyo_ocr/cli.py 準拠・issue #58）。
/// ALLOWED_SUBCOMMANDS（webview から呼べるコマンドの白リスト）とは別物として
/// 保つ——debug-images は --template を受けるが白リスト外のまま（GUI からは
/// 呼べない・方針どおり）なので、ここに含めても inject_default_template まで
/// 到達しない。
///
/// **detect-frames は意図的に含めない**（レビュー H-3・2026-09-04）。#58 の
/// 注入は「CLI 既定値が core-dist 側の別実体テンプレートを指す」問題への
/// 対処だが、detect-frames の `--template` は既定値が `None` で
/// （cli.py の add_parser("detect-frames")）、この唯一の例外では二重実体が
/// そもそも起きない。注入すると逆に害がある: core は `--template` を受けると
/// ①除外領域を白潰し ②face_id 割り当て ③overlaps_existing 判定 に加えて
/// ④`--dpi` をテンプレートの `render_dpi` で上書きする（cmd_detect_frames）。
/// 出荷テンプレートは除外を 9 件持ち、うち綴じ穴の帯 2 本は高さ 1880px ある
/// ため、GUI が空のテンプレートで開いた候補生成でも罫線が黙って消えていた。
///
/// GUI は自分で `--dpi` を必ず渡すので、注入をやめると dpi の根拠は1本になる
/// ——候補生成（detect-frames）も下地を作る `expand-page` も、Editor.tsx の
/// `expandPageArgs`／`runDetectFrames` が選択中テンプレートの `render_dpi` を
/// 同じ値で渡す（issue #107・2026-09-07。それ以前は expand-page だけ未指定で
/// コア既定 300 に固定されていた）。
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
fn inject_default_template(mut args: Vec<String>, template_path: &Path) -> Vec<String> {
    let accepts = args
        .first()
        .map(|c| TEMPLATE_ACCEPTING_SUBCOMMANDS.contains(&c.as_str()))
        .unwrap_or(false);
    // `--template=path`（等号形式）も明示指定として扱う。素通りすると
    // 二重に --template を積んでコアの argparse がエラーになる（issue L-3）
    if accepts && !args.iter().any(|a| a == "--template" || a.starts_with("--template=")) {
        args.push("--template".to_string());
        args.push(template_path.to_string_lossy().to_string());
    }
    args
}

/// `last_template` の値から実際に注入するテンプレートパスを決める（issue #72
/// (t)・08 設計 §3.5.2）。`AppHandle` に依存する glue のため単体テストはしない
/// （`repo_root`・`allowed_roots` と同じ扱い）——判定ロジック本体は
/// `user_templates::resolve_last_template_path`（純関数）でテストする。
///
/// 読み出し時に毎回 `user_templates_dir` を再検査する（キャッシュしない）。
/// config は手編集や別プロセスからも書けるため、書き込み時の検証だけでは
/// 守れない（`workdir_pages_dir` と同じ理由）。
fn resolve_last_template(app: &AppHandle, root: &Path) -> PathBuf {
    let last = read_config(app.clone())
        .ok()
        .and_then(|c| c.get("last_template").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_default();
    let user_dir = user_templates::user_templates_dir(app).ok();
    user_templates::resolve_last_template_path(&last, root, user_dir.as_deref())
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

/// フラグ（または `json_path`）の `ArgKind` を全サブコマンドの表から引く。
///
/// `check_arg_scopes` は pairs にサブコマンド名を持たない（呼び出し側の
/// `run_core`/`run_core_capture` がサブコマンドを跨いでこの関数を共有して
/// いるため）。フラグ名だけで種別を引けるのは、同じフラグ名がどのサブコマンド
/// の表でも常に同じ種別で定義されている前提があるため——例えば `--input` は
/// run／expand-page／detect-frames のどこでも `Dir`、`--template` はどこでも
/// `Template`。この前提が崩れる（同名フラグを別の意味で複数サブコマンドに
/// 足す）設計は避けること。
fn arg_kind_for(flag: &str) -> Option<ArgKind> {
    ALLOWED_SUBCOMMANDS.iter().copied()
        .find_map(|cmd| allowed_flags(cmd).iter().find(|s| s.0 == flag).map(|s| s.1))
}

/// コアへ渡す引数のうち、パスを値に取るフラグをスコープ検査する
/// （issue S-MD・S-N2）。`run_core`（run）と `run_core_capture`
/// （verify / detect-grid / expand-page）で同じ関数を通す。
///
/// 以前は run だけを検査していたため、`expand-page --input <任意の PDF>` で
/// 任意のファイルを読ませ、その展開結果（`editor_pages` の PNG）を
/// `read_file_b64` で吸い出す連鎖が残っていた——API 送信・課金が無くても
/// 「webview から任意パスの中身を見る」経路としては成立する。
///
/// 戻り値は検査に使った pairs そのものではなく、パスを値に取るフラグだけ
/// `normalize_path` の正規化結果（絶対パス）へ差し替えた新しい pairs
/// （issue #126 (2)）。呼び出し側はこれを `rebuild_args` で argv へ組み直し、
/// 子プロセスへは検査した絶対パスをそのまま渡す——生の（相対もありうる）
/// 文字列を渡すと、相対パスの解決基準が検査時（GUI プロセスの cwd）と
/// 実行時（子プロセスは `<root>/core` を cwd に起動）でずれ、検査した対象と
/// 実際に開かれる対象が一致する保証がなくなる。
///
/// パス系フラグを新しく足すときは、`allowed_flags` の表に正しい `ArgKind` を
/// 書くこと。この関数は種別（`ArgKind`）で match するため、`allowed_flags` に
/// 種別を書きさえすれば検査の腕は自動で決まる——列挙型の match は非網羅だと
/// コンパイルが落ちるので、新しい種別を足したのに腕を書き忘れることはできない
/// （issue #129・旧 M-4）。
fn check_arg_scopes(pairs: &[(String, String)], roots: &[PathBuf],
                    picked: &HashSet<PathBuf>,
                    pending_cred: &mut Option<PathBuf>)
                    -> Result<Vec<(String, String)>, String> {
    let mut checked = Vec::with_capacity(pairs.len());
    for (flag, value) in pairs {
        let kind = arg_kind_for(flag)
            .ok_or_else(|| format!("内部エラー: 未知の引数です: {flag}"))?;
        let out_value = match kind {
            ArgKind::None | ArgKind::Opaque => value.clone(),
            // フォルダ・拡張子なしのファイルもありうる（run --input・#19）
            ArgKind::Dir => {
                let abs = normalize_path(value)?;
                check_scope_dir(&abs, roots, picked)?;
                abs.to_string_lossy().into_owned()
            }
            ArgKind::File(exts) => {
                let abs = normalize_path(value)?;
                check_scope(&abs, exts, roots, picked)?;
                abs.to_string_lossy().into_owned()
            }
            ArgKind::Template => {
                let abs = normalize_path(value)?;
                check_template_scope(&abs, roots, picked)?;
                abs.to_string_lossy().into_owned()
            }
            ArgKind::Credentials => {
                let abs = normalize_path(value)?;
                check_credentials_scope(&abs, pending_cred)?;
                abs.to_string_lossy().into_owned()
            }
        };
        checked.push((flag.clone(), out_value));
    }
    Ok(checked)
}

/// `import-credentials` の位置引数 `json_path` のスコープ検査（issue #121）。
///
/// `json_path` は `PickedPaths` の対象外のまま（`pick_json` が
/// `kind:"credentials"` のとき `remember_pick` の指定によらず登録しない
/// 設計・issue #69 セキュリティ LOW (c)）——白リストへ入れると、取り込み後も
/// 鍵の平文 JSON が `read_text` で読める窓が開いたままになる。その代わり
/// `pick_json(kind:"credentials")` がダイアログで選ばれたパスを
/// `PendingCredentialsPath` の1回限りのスロットへ書き込み、ここで
/// **値が完全一致するときだけ**通す。一致したらスロットを消費して空にする
/// ——同じパスの2回目の呼び出しや、ダイアログを経ていない任意パスの指定を
/// 拒否する。以前はこの分岐が無く `_ => {}` で無検査のまま通していたため、
/// webview を掌握されると任意パスの JSON を鍵として取り込ませ、
/// `--delete-source`（ランダム上書き→削除）を任意ファイルに対して起動できた。
fn check_credentials_scope(abs: &Path, pending: &mut Option<PathBuf>) -> Result<(), String> {
    if pending.as_deref() == Some(abs) {
        *pending = None;
        Ok(())
    } else {
        Err("選択されていない認証キーです。ファイル選択ダイアログから選び直してください".into())
    }
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
///
/// **ジャンクション残余の再評価トリガ（issue #69 残置3）**: 現状の残余リスク
/// （canonicalize 済みルート配下にジャンクションを張られた場合の読み取り拡大）
/// は LOW として受容している。前提は「読めるのは roots 配下の限られた拡張子
/// だけ」「roots は repo_root と workdir/editor_pages の2つだけ」。次のいずれか
/// に手を入れるときは、この受容を **HIGH 相当として再評価**すること:
/// (1) roots を参照するコマンドの許可拡張子を増やす（`check_scope` の
/// `&["png","jpg","jpeg"]` / `&["json"]` を広げる）
/// (2) `read_text` に roots を許す（現在は picked 由来のみ）
/// (3) workdir を GUI から自由入力できるようにする
/// 詳細は docs/design/chouhyo-ocr/08_frame_detection_design.md §3.2.6 の末尾。
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

/// ドロップを受け付ける画面が表示されているかをフロントから伝える
/// （issue #69 セキュリティ LOW (b)）。実行画面のマウント／タブ切替で呼ぶ。
#[tauri::command]
fn set_drop_active(state: State<'_, DropActive>, active: bool) {
    *state.0.lock().unwrap() = active;
}

/// ダイアログで選ばれたパスを白リストへ登録する。
fn remember(picked: &PickedPaths, p: &Path) {
    remember_dropped(picked, std::slice::from_ref(&p.to_path_buf()));
}

/// コア実体（同梱 exe / venv python）の選択結果。
#[derive(Debug, PartialEq, Eq)]
enum CoreProgram {
    /// 同梱 exe（`core-dist/chouhyo-core/chouhyo-core.exe`）を直接起動する。
    Bundled(PathBuf),
    /// venv の python を `-m chouhyo_ocr.cli` で起動する。
    Venv(PathBuf),
}

/// コア実体（同梱 exe / venv python）の選択規則。
///
/// `override_` は環境変数 `CHOUHYO_CORE` の値を想定する。前後の空白を除き、
/// 大文字小文字を区別せず判定する：
/// - 未指定、または空文字（トリム後）: 自動判定する。
/// - `"bundled"`: 同梱 exe を強制する。存在しなければ Err。
/// - `"venv"`: venv python を強制する。存在しなければ Err。
/// - それ以外の空でない値: Err（レビュー指摘 MEDIUM-6）。未知の値を黙って
///   自動判定へ落とすと、指定したのに効いていないことに利用者が気づけない。
///   `CHOUHYO_CORE` は配布物を GUI から検証するための逃げ道であり、
///   「効いていないのに効いたつもりになる」のが一番まずい。
///
/// 自動判定は、開発チェックアウト（`root/.git` が存在し、かつ venv python も
/// 存在する）なら **venv を優先**する。
///
/// 2026-09-02 実測: 以前は同梱 exe が存在すれば `tauri dev`（開発起動）でも
/// 無条件にそちらを使っていたため、同梱 exe が 2026-08-31 16:45 ビルドの
/// まま更新されず、その後の core 側 17 commit（`--no-mask` フラグ追加を
/// 含む）を知らない状態で編集画面の PDF 展開が
/// `unrecognized arguments: --no-mask` の argparse エラーで失敗した。
/// ソース自体にバグは無く、回帰ゲートもソースに対しては緑のまま、GUI だけが
/// 古い配布物で動いていた。開発チェックアウトでは常にソースと同じ venv を
/// 使うことで、この種の「配布物の陳腐化」を構造的に防ぐ。
///
/// `CHOUHYO_CORE=bundled` は、開発チェックアウトで配布物（同梱 exe）自体を
/// GUI 経由であえて検証したいときの逃げ道として残す。
fn resolve_core_program(root: &Path, override_: Option<&str>) -> Result<CoreProgram, String> {
    let bundled = root.join("core-dist").join("chouhyo-core").join("chouhyo-core.exe");
    let venv = root.join(".venv").join("Scripts").join("python.exe");

    // 空文字（トリム後）は未指定と同じ扱いにする。それ以外の空でない値は
    // bundled/venv のいずれでもなければ Err にする（自動判定へ黙って
    // 落とさない）。
    let normalized = override_
        .map(|s| s.trim().to_ascii_lowercase())
        .filter(|s| !s.is_empty());

    match normalized.as_deref() {
        Some("bundled") => {
            return if bundled.exists() {
                Ok(CoreProgram::Bundled(bundled))
            } else {
                Err("CHOUHYO_CORE=bundled が指定されていますが、配布物\
                     （core-dist/chouhyo-core/chouhyo-core.exe）が見つかりません".into())
            };
        }
        Some("venv") => {
            return if venv.exists() {
                Ok(CoreProgram::Venv(venv))
            } else {
                Err("CHOUHYO_CORE=venv が指定されていますが、.venv が見つかりません".into())
            };
        }
        Some(other) => {
            return Err(format!(
                "CHOUHYO_CORE の値 '{other}' は不明です（bundled / venv のいずれか）"
            ));
        }
        None => {}
    }

    if root.join(".git").exists() && venv.exists() {
        return Ok(CoreProgram::Venv(venv));
    }
    if bundled.exists() {
        return Ok(CoreProgram::Bundled(bundled));
    }
    if venv.exists() {
        return Ok(CoreProgram::Venv(venv));
    }
    Err("Python コアが見つからない（.venv 未構築・配布物欠損）".into())
}

/// コア起動コマンドを組み立てる。配布版は同梱 exe、開発版は venv の python -m。
/// 実体の選択は `resolve_core_program` に委ね、`CHOUHYO_CORE` 環境変数で
/// `bundled`/`venv` を強制できる（詳細は同関数の doc を参照）。
///
/// `CHOUHYO_USER_DIR`（issue #72 (t)・08 設計 §3.1.3）: 利用者テンプレートの
/// 保存先を Python 側へ伝える。解決できない環境（`app_data_dir` 不可等）でも
/// 環境変数を単に付けないだけにする——他のサブコマンド（`status` 等、
/// templates_user を使わないもの）を巻き添えで落とさない fail-safe。
/// core 側は環境変数が無ければ `project_root()/templates_user` へ
/// フォールバックする（08 設計 §3.1.3）。
fn core_command(app: &AppHandle, root: &Path) -> Result<Command, String> {
    let override_ = std::env::var("CHOUHYO_CORE").ok();
    let program = resolve_core_program(root, override_.as_deref())?;
    let mut cmd = match program {
        CoreProgram::Bundled(exe) => Command::new(exe),
        CoreProgram::Venv(py) => {
            let mut c = Command::new(py);
            c.args(["-X", "utf8", "-m", "chouhyo_ocr.cli"]);
            c
        }
    };
    let cwd = root.join("core");
    let _ = std::fs::create_dir_all(&cwd); // インストール直後は core/ が無い
    cmd.current_dir(cwd);
    cmd.env("PYTHONUTF8", "1");
    if let Ok(user_dir) = user_templates::user_templates_dir(app) {
        cmd.env("CHOUHYO_USER_DIR", user_dir);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    Ok(cmd)
}

/// コアを起動し stdout を丸ごと返す本体（issue #72 (t)）。`run_core_capture`
/// （webview 発の args・チェック済み）と `save_user_template`／
/// `match_templates`（Rust が組み立てた信頼済み args）の3箇所から共有する。
/// `CoreProc`（`run` の多重起動ロック）はここでは触らない——verify や
/// match-templates は元々そのロックの対象外（従来の `run_core_capture` も
/// 同様に `CoreProc` を参照していない）。
async fn core_output(app: &AppHandle, root: &Path, args: Vec<String>) -> Result<String, String> {
    let mut cmd = core_command(app, root)?;
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
            // issue #126 M-1: ここは run_core_capture（verify・detect-grid・
            // expand-page・detect-frames・import-credentials・purge
            // --preview 全部の実行経路）が非 0 終了かつ stdout が空のときに
            // 通る唯一の stderr 経路。run_core の core-err と同じく、
            // webview（RunScreen.tsx の setError＝赤帯表示）へ渡す前に
            // 絶対パスを伏せる
            String::from_utf8_lossy(&out.stderr)
                .lines()
                .map(redact_absolute_paths)
                .collect::<Vec<_>>()
                .join("\n")
        } else {
            stdout
        })
    }
}

/// `verify` 専用: プロセス起動そのものの失敗だけを `Err` とし、**終了コードに
/// 関わらず** stdout を返す（issue #72 (t)・H-1 追補・セキュリティレビュー）。
///
/// `core_output` は非 0 終了を一律エラー扱いするが、`verify` は資格情報
/// 未設定・API 残量ゼロ等**テンプレート検証とは無関係な**理由で非 0 終了する
/// ことがある。`save_user_template` の保存可否判定は必ず
/// `user_templates::verify_template_ok`（stdout の `event=="verify" &&
/// check=="template"` 行）で行い、終了コードには依存しない。
///
/// エラーメッセージは固定文言＋`ErrorKind` のみ（M-2 追補）——OS エラーの
/// Display 表現が実行ファイルパスを含みうるため、webview へは種別だけ返す。
async fn core_output_stdout_only(app: &AppHandle, root: &Path,
                                 args: Vec<String>) -> Result<String, String> {
    let mut cmd = core_command(app, root)?;
    cmd.args(&args);
    let out = tauri::async_runtime::spawn_blocking(move || cmd.output())
        .await
        .map_err(|_| "コアの実行を待機できません".to_string())?
        .map_err(|e| format!("コアを起動できません（{:?}）", e.kind()))?;
    Ok(String::from_utf8_lossy(&out.stdout).to_string())
}

/// パスの伏せ字化で「確定的にここで終わる／ここの直後でなければパスの
/// 開始とは認めない」とみなす区切り文字（issue #126 (1)）。空白は含めない
/// ——`C:\Program Files\...`・`C:\Users\山田 太郎\...` のようにパスの途中に
/// 空白を含む実在パスがあるため、空白の扱いは `path_run_end` で先読みして
/// 決める（M-2 追補）。全角の丸括弧・鉤括弧・読点は含める——ConfigError
/// （core/chouhyo_ocr/paths.py・config.py）のメッセージが
/// `（現在: '<path>'）` のように、Python の `repr()` が付ける引用符の外側を
/// 全角括弧が空白なしで囲む形をとるため。
fn is_hard_boundary(c: char) -> bool {
    matches!(c, '"' | '\'' | '(' | ')' | '[' | ']' | '`' | ',' | ';'
                | '\u{3001}' | '\u{3002}' | '\u{FF08}' | '\u{FF09}'
                | '\u{300C}' | '\u{300D}')
}

/// `is_path_start` の左境界判定（issue #126 M-2 追補）。空白・hard boundary
/// に加えて `=`（`KEY=/path` 形の環境変数表示）・`:`（`現在:C:\...` の
/// ようにコロン直後に空白なく続く形）も、パスがここから始まってよい
/// 直前の文字として認める。
fn is_left_boundary(c: char) -> bool {
    c.is_whitespace() || is_hard_boundary(c) || matches!(c, '=' | ':')
}

/// `chars[i..]` が（大文字小文字を区別せず）`needle` で始まるか。
fn starts_with_ignore_case(chars: &[char], i: usize, needle: &str) -> bool {
    let needle: Vec<char> = needle.chars().collect();
    i + needle.len() <= chars.len()
        && chars[i..i + needle.len()].iter().zip(needle.iter())
            .all(|(a, b)| a.eq_ignore_ascii_case(b))
}

/// `chars[i]` を絶対パスの開始とみなせるか（issue #126 (1)・M-2 追補
/// L-4）。`file://`／`FILE://` URI・ドライブ絶対パス（`C:\...`・`C:/...`）・
/// UNC（`\\server\share`。verbatim 接頭辞 `\\?\...` もこの形で拾える）・
/// Unix 風絶対パス（`/home/...` 等）の4種の開始パターンを見る。加えて
/// `chars[i]` の直前が左境界（または行頭）であることを要求する——この
/// チェックが無いと `and/or`・`2026/09/07` のような通常の文中の `/` まで
/// 拾ってしまう。
fn is_path_start(chars: &[char], i: usize) -> bool {
    if i > 0 && !is_left_boundary(chars[i - 1]) {
        return false;
    }
    if starts_with_ignore_case(chars, i, "file://") {
        return true;
    }
    if i + 2 < chars.len() && chars[i].is_ascii_alphabetic() && chars[i + 1] == ':'
        && (chars[i + 2] == '\\' || chars[i + 2] == '/') {
        return true;
    }
    if i + 1 < chars.len() && chars[i] == '\\' && chars[i + 1] == '\\' {
        return true;
    }
    // 直後が hard boundary・空白なら、単なる区切り記号（`a / b` の除算・
    // 区切り等）とみなして拾わない
    chars[i] == '/' && i + 1 < chars.len()
        && !is_hard_boundary(chars[i + 1]) && !chars[i + 1].is_whitespace()
}

/// `is_path_start` が真だった位置 `start` から、伏せる範囲の終端（消費し
/// 終えた次のインデックス）を返す（issue #126 M-2 追補）。
///
/// 非空白・非 hard boundary をできるだけ飲み込み、空白に達したら即座には
/// 終端にしない——空白の**直後の1語**（次の空白か hard boundary までの
/// 範囲）に区切り（`\` か `/`）が含まれる限り、その空白1文字を跨いで伸ばす
/// （`C:\Program Files\...`・`C:\Users\山田 太郎\...` を丸ごと伏せるため）。
///
/// 先読みを「次の hard boundary まで」にせず「直後の1語まで」に絞るのが
/// 重要——前者だと `C:\a\b から C:\c\d` のような「別々の2つのパスが読点も
/// 引用符も挟まず1行に並ぶ」行で、間の「から」を挟んで2つ目のパスの区切り
/// まで誤って地続きとみなし、1つの伏せ字に融合してしまう（実装時に単体
/// テストで検出・修正）。
/// 直後の語に区切りが無ければ、その空白の手前で打ち切る（空白自体は
/// 伏せ字の範囲に含めない）。
fn path_run_end(chars: &[char], start: usize) -> usize {
    let mut end = start;
    loop {
        while end < chars.len() && !chars[end].is_whitespace() && !is_hard_boundary(chars[end]) {
            end += 1;
        }
        if end >= chars.len() || is_hard_boundary(chars[end]) {
            return end;
        }
        // ここに来るのは chars[end] が空白のとき。直後の1語（次の空白か
        // hard boundary までの範囲）だけを見る
        let mut peek = end + 1;
        let mut continues = false;
        while peek < chars.len() && !chars[peek].is_whitespace() && !is_hard_boundary(chars[peek]) {
            if chars[peek] == '\\' || chars[peek] == '/' {
                continues = true;
            }
            peek += 1;
        }
        if !continues {
            return end;
        }
        end += 1; // この空白1文字だけ跨いで続行
    }
}

/// stderr の1行から絶対パスらしい範囲を `[path]` へ伏せる（issue #126 (1)）。
///
/// `run`／`purge` は `core-err` イベントで stderr を webview へそのまま
/// 中継しており（`run_core` の err_reader）、その表示自体（RunScreen.tsx の
/// 進捗ログ `[err] ${line}`）は利用者向けの正当な機能なので落とさない
/// ——`save_user_template`／`match_templates` が使う
/// `core_output_stdout_only`（stderr を一切出さない）ほど厳密には塞がず、
/// 値だけを伏せてから中継する。`core_output`（`run_core_capture` が使う。
/// verify・detect-grid・expand-page・detect-frames・import-credentials・
/// purge --preview 全てがここを通る）の非 0 終了時 stderr フォールバックにも
/// 同じ関数を通す（issue #126 M-1 追補）——鍵の取り込み失敗時の赤帯表示
/// （RunScreen.tsx の `setError`）にも絶対パスを出さない。
///
/// 完全な検出は目指さない。誤検知（パスでない文字列を伏せる）より見逃し
/// （パスを伏せ損ねる）のほうが実害が大きい防御なので、判定に迷う側は
/// 「伏せる」に倒す——`is_path_start` の左境界チェックだけは例外で、通常の
/// 文中に現れる `/`（`and/or`・日付表記）まで巻き込まないために必要。
fn redact_absolute_paths(line: &str) -> String {
    let chars: Vec<char> = line.chars().collect();
    let mut out = String::with_capacity(line.len());
    let mut i = 0;
    while i < chars.len() {
        if is_path_start(&chars, i) {
            let end = path_run_end(&chars, i);
            out.push_str("[path]");
            i = end;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

/// 指定 PID を子プロセスごと停止する（`taskkill /T /F`）。Windows は親
/// プロセス終了だけでは子（pdftoppm 等）が生き残り課金が続くため、常に
/// `/T`（プロセスツリー）を付ける。エラーの扱いは呼び出し側に委ねる——
/// `kill_core`（中断ボタン）は利用者にそのまま返す一方、`PidSlot::drop`
/// はベストエフォートとして無視する（早期 return からの後始末で、失敗を
/// 更に誰かに投げる先が無いため）。
/// Windows 標準の実行ファイルを `%SystemRoot%` 起点の絶対パスで解決する
/// （issue #53 L-12・バイナリプランティング対策）。
///
/// `Command::new("taskkill")` のような名前指定は PATH 探索に委ねる。探索順に
/// 攻撃者が書ける場所（同名の taskkill.exe・explorer.exe を置ける場所）が
/// 含まれるかは環境依存で、Rust std の Windows 側探索順に cwd が入るかは
/// **未検証**（issue 本文の指摘どおり）——だが絶対パスで名指しするのは
/// 検証結果に関わらず正しい書き方なので、環境依存の調査結果を待たずに直す。
///
/// `SystemRoot` が無い・空のときは従来どおりの名前指定へフォールバックする
/// （ここで失敗させると、環境変数が壊れているだけでフォルダを開く・中断する
/// といった無関係な操作まで道連れになる）。実在確認はしない——判定を
/// 決定論に保ち（単体テスト可能にし）、パスが誤っていれば spawn の失敗として
/// 呼び出し側のエラー経路に乗せる。
fn system_program(system_root: Option<std::ffi::OsString>, rel: &str,
                  fallback: &str) -> PathBuf {
    match system_root {
        Some(root) if !root.is_empty() => {
            let mut p = PathBuf::from(root);
            p.push(rel);
            p
        }
        _ => PathBuf::from(fallback),
    }
}

/// `%SystemRoot%\System32\taskkill.exe`（issue #53 L-12）。
fn taskkill_program() -> PathBuf {
    system_program(std::env::var_os("SystemRoot"), "System32\\taskkill.exe", "taskkill")
}

/// `%SystemRoot%\explorer.exe`（issue #53 L-12）。
fn explorer_program() -> PathBuf {
    system_program(std::env::var_os("SystemRoot"), "explorer.exe", "explorer")
}

fn kill_pid(pid: u32) -> Result<(), String> {
    let mut c = Command::new(taskkill_program());
    c.args(["/T", "/F", "/PID", &pid.to_string()]);
    #[cfg(windows)]
    c.creation_flags(CREATE_NO_WINDOW);
    let out = c.output().map_err(|e| e.to_string())?;
    if out.status.success() { Ok(()) } else { Err("停止できませんでした".into()) }
}

/// 実行中の core を止める唯一の実装（issue #135・#141）。中断ボタン
/// （`kill_core`）・ウィンドウを閉じる・アプリ終了のすべてがこの関数を通る。
/// kill の実行手段を `killer` として受け取るのは、実プロセスを起動せずに
/// 状態遷移だけを単体テストするため（本番は `kill_pid` を渡す）。
///
/// **ロックは kill が終わるまで持ち続ける**（issue #141）。以前の `kill_core`
/// は `take()` でスロットを先に空にしてからロックの外で `taskkill` していた
/// ため、その間に `run_core` が「未実行」と見なして新しい core を spawn
/// できてしまい、死にきっていない旧プロセスと新しい core が同じ
/// workdir/SQLite に並走する窓があった。ここでは pid を読んだのと同じロックを
/// 握ったまま kill し、完了後に `release_slot`（issue Q-MB の「自分の pid の
/// ときだけ消す」不変条件）で解放する。
///
/// スロットが空なら何もせず `Ok(false)` を返す（対象が無いことをエラーに
/// するかは呼び出し側の判断——中断ボタンは「実行中の処理がありません」を
/// 返したいが、終了経路ではエラーにする意味が無い）。kill が失敗しても
/// スロットは解放する（次回起動を永久にブロックしない）。
fn kill_running_core_with(
    state: &Mutex<Option<u32>>,
    killer: impl FnOnce(u32) -> Result<(), String>,
) -> Result<bool, String> {
    let mut slot = state.lock().unwrap();
    let pid = match *slot {
        Some(pid) => pid,
        None => return Ok(false),
    };
    let result = killer(pid);
    release_slot(&mut slot, pid);
    result.map(|_| true)
}

/// `kill_running_core_with` に本番の kill 手段（`kill_pid`）を固定した薄いラッパ。
fn kill_running_core(state: &Mutex<Option<u32>>) -> Result<bool, String> {
    kill_running_core_with(state, kill_pid)
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
    released: bool,
}

impl<'a> PidSlot<'a> {
    fn new(state: &'a Mutex<Option<u32>>, pid: u32) -> Self {
        Self { state, pid, kill_on_drop: true, released: false }
    }

    /// 正常終了（`child.wait()` 済み）の後始末を**その場で**行う
    /// （issue #53 L-13）。スロットを空にし、以後の Drop は何もしない。
    ///
    /// 呼び出し側は `Child` を**まだ手元に持っている**状態でこれを呼ぶこと。
    /// Windows は開いているプロセスハンドルがある間 pid を再利用しないため、
    /// 「ハンドルを持ったままスロットを空にする」順序にすると、
    /// 「終了済みの pid がスロットに残っている」時間が無くなる。逆順
    /// （Child を先に drop）だと、その間に中断ボタン（`kill_core`）が
    /// スロットの pid を取り出し、再利用済みの別プロセスへ
    /// `taskkill /T /F` を撃つ窓が空く。
    fn release(&mut self) {
        let mut slot = self.state.lock().unwrap();
        release_slot(&mut slot, self.pid);
        self.released = true;
        self.kill_on_drop = false;
    }
}

impl Drop for PidSlot<'_> {
    fn drop(&mut self) {
        if self.released {
            // release() 済み。ここで再びスロットを触ると、その後に始まった
            // 別の実行が同じ pid を取っていた場合にそちらを消してしまう
            return;
        }
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

/// 実行 ID の連番（プロセス内で単調増加・issue #96）。`run_core` を呼ぶたび 1 進む。
static RUN_SEQ: AtomicU64 = AtomicU64::new(0);

/// 実行 ID の文字列表現（issue #96）。`<pid>-<連番>`。
///
/// 連番だけでもプロセス内では一意だが、GUI を落として起動し直すと 0 から
/// 振り直しになる。webview が再読み込みをまたいで古い ID を持っていても
/// 取り違えないよう pid を前に付ける。外部クレートは足さない——UUID の
/// ような大域的な一意性は要らず、必要なのは「同じ GUI で前後する 2 つの
/// 実行を区別できること」だけ。
fn format_run_id(pid: u32, seq: u64) -> String {
    format!("{pid}-{seq}")
}

/// 次の実行 ID を採番する（採番のたびに連番が進む）。
fn next_run_id() -> String {
    format_run_id(std::process::id(), RUN_SEQ.fetch_add(1, Ordering::Relaxed))
}

/// `core-line` / `core-err` の payload（issue #96）。
///
/// 以前は行の文字列だけを送っていたため、前の実行の残り行が次の実行の画面へ
/// 混ざっても webview 側で区別できなかった。`run_id` を添えてフロントで
/// 捨てられるようにする（読取スレッドの join と合わせた二重の防御）。
#[derive(Clone, serde::Serialize)]
struct CoreLine {
    run_id: String,
    line: String,
}

/// `core-start` の payload（issue #96）。読取スレッドを起こす前に 1 回だけ送る。
///
/// run_id を `run_core` の戻り値だけで渡すと、行イベントが流れ終わった後に
/// しか分からずフィルタの用を成さない。開始側の別イベントで先に知らせる。
#[derive(Clone, serde::Serialize)]
struct RunStart {
    run_id: String,
}

/// `run_core` の戻り値（issue #96）。
///
/// 終了コードに加えてその実行の run_id を返す。フロントはこれを「この ID の
/// 実行はもう終わった」の印として使い、後から遅れて届く行イベントを捨てる。
#[derive(Clone, serde::Serialize)]
struct RunResult {
    code: i32,
    run_id: String,
}

/// コアを起動し stdout(JSON Lines)/stderr を行単位でイベント中継、終了コードを返す。
///
/// イベントを出すのはこの経路だけ——`run_core_capture` は `core_output`
/// （`Command::output()` で丸ごと受け取る）を通るため emit を挟まず、
/// run_id を持たせる対象にならない。
#[tauri::command]
async fn run_core(app: AppHandle, state: State<'_, CoreProc>,
                  picked: State<'_, PickedPaths>,
                  pending_cred: State<'_, PendingCredentialsPath>,
                  args: Vec<String>) -> Result<RunResult, String> {
    let pairs = check_args_v2(&args)?;
    let root = repo_root(&app)?;
    // 値のパススコープ検査（issue S-MD・S-N2）。run_core_capture 側と同じ
    // check_arg_scopes を通す。ロックは await をまたがせない（Send 制約）ため
    // このブロック内で閉じる。返ってくる pairs はパス値が正規化済み
    // （issue #126 (2)）——子プロセスには元の生文字列ではなくこちらを渡す
    let checked_pairs = {
        let roots = allowed_roots(&app)?;
        let picked_set = picked.0.lock().unwrap();
        let mut cred_slot = pending_cred.0.lock().unwrap();
        check_arg_scopes(&pairs, &roots, &picked_set, &mut cred_slot)?
    };
    let args = rebuild_args(&args[0], &checked_pairs)?;
    let default_tpl = resolve_last_template(&app, &root);
    let args = inject_default_template(args, &default_tpl);
    let mut cmd = core_command(&app, &root)?;
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
    let stderr = child.stderr.take().ok_or("stderr を取得できない")?;

    // 行イベントより先に「今回の run_id」を知らせる（issue #96）。パイプの
    // 取得に失敗する経路（上の 2 行）では送らない——1 行も流れない実行の
    // ID をフロントに握らせない。
    let run_id = next_run_id();
    let _ = app.emit("core-start", RunStart { run_id: run_id.clone() });

    let app_out = app.clone();
    let id_out = run_id.clone();
    let out_reader = std::thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            let _ = app_out.emit("core-line", CoreLine { run_id: id_out.clone(), line });
        }
    });
    let app_err = app.clone();
    let id_err = run_id.clone();
    let err_reader = std::thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            // stderr は素通しにしない（issue #126 (1)）。ConfigError 等の
            // メッセージは絶対パス（環境変数の生値・resolve 後のパス）を含み
            // うる（core/chouhyo_ocr/paths.py）——「人が読む進捗ログ」という
            // 表示自体（RunScreen.tsx の `[err] ${line}`）は壊さず、伏せ字化
            // してから中継する
            let line = redact_absolute_paths(&line);
            let _ = app_err.emit("core-err", CoreLine { run_id: id_err.clone(), line });
        }
    });

    // `Child` を待機スレッドから**返してもらう**（issue #53 L-13）。閉じた
    // プロセスハンドルは pid の再利用を許すため、ハンドルを持ったまま
    // スロットを空にし、その後で drop する順序にする。以前は `child` を
    // クロージャへ move したままにしていたので、wait() 完了（＝ハンドルが
    // 閉じる）から関数末尾のスロット解放までの間——読取スレッドの join を
    // 挟むので短くない——中断ボタンが再利用済み pid を kill しうる窓が
    // 空いていた。
    let (wait_result, child) =
        tauri::async_runtime::spawn_blocking(move || {
            let status = child.wait();
            (status, child)
        })
        .await
        .map_err(|e| e.to_string())?;
    let status = match wait_result {
        Ok(status) => {
            // 正常終了: 子プロセスは wait() 済みで既に居ない。kill は試みず、
            // ハンドルを持っているうちにスロットだけ空にする
            slot_guard.release();
            drop(child);   // ここで初めて pid が再利用可能になる
            status
        }
        Err(e) => {
            // wait 失敗——プロセスが生きている可能性がある。kill は
            // PidSlot::drop に任せ、ハンドルは kill が終わるまで開けておく
            // （kill 対象の pid が再利用されない）
            drop(slot_guard);
            drop(child);
            return Err(e.to_string());
        }
    };

    // 読取スレッド 2 本の完了を待ってから戻す（issue #96）。`child.wait()` を
    // 見ただけで戻すと、パイプに残っていた行が「次の実行が始まった後」に
    // emit されうる——完了サマリがその 1 行だと、新しい実行の表示を古い
    // サマリが上書きする。
    //
    // ここで詰まらないことは EOF が来ることに依存する。コアは子プロセス
    // （pdftoppm 等）を `subprocess.run(..., capture_output=True)` で起動して
    // おり（core/chouhyo_ocr/ingest.py）、こちらのパイプの書き込み端を孫が
    // 引き継ぐ経路が無い。コアが終わった時点で書き込み端は全て閉じる。
    //
    // `child.wait()` が失敗する経路ではここへ来ない（上の match で戻る）。
    // その場合はハンドルが drop されてスレッドは切り離されるが、
    // `PidSlot::drop` がプロセスを kill してパイプを閉じるため、スレッドは
    // やはり終了する。
    let _ = tauri::async_runtime::spawn_blocking(move || {
        let _ = out_reader.join();
        let _ = err_reader.join();
    })
    .await;

    Ok(RunResult { code: status.code().unwrap_or(-1), run_id })
}

/// 実行中のコアを子プロセス（pdftoppm 等）ごと停止する。中断分は
/// 「未処理（中断）」として出力され、次回 run で続きから再開する（要件 §5.8）。
///
/// 実装は `kill_running_core`（issue #135・#141）に委ねる——ウィンドウを
/// 閉じる・アプリ終了と同じ「kill が終わるまでスロットを保持してから解放する」
/// 経路を通す。
#[tauri::command]
fn kill_core(state: State<'_, CoreProc>) -> Result<(), String> {
    if kill_running_core(&state.0)? {
        Ok(())
    } else {
        Err("実行中の処理がありません".into())
    }
}

/// コアを起動し stdout を丸ごと返す（編集画面の detect-grid / verify 用）。
#[tauri::command]
async fn run_core_capture(app: AppHandle, picked: State<'_, PickedPaths>,
                          pending_cred: State<'_, PendingCredentialsPath>,
                          args: Vec<String>) -> Result<String, String> {
    let pairs = check_args_v2(&args)?;
    let root = repo_root(&app)?;
    // run_core と同じ値検査を通す（issue S-N2）。フラグ表検査だけだと
    // `expand-page --input <任意.pdf>` で任意ファイルを展開させられる。
    // 返ってくる pairs はパス値が正規化済み（issue #126 (2)）——子プロセスに
    // は元の生文字列ではなくこちらを渡す
    let checked_pairs = {
        let roots = allowed_roots(&app)?;
        let picked_set = picked.0.lock().unwrap();
        let mut cred_slot = pending_cred.0.lock().unwrap();
        check_arg_scopes(&pairs, &roots, &picked_set, &mut cred_slot)?
    };
    let args = rebuild_args(&args[0], &checked_pairs)?;
    let default_tpl = resolve_last_template(&app, &root);
    let args = inject_default_template(args, &default_tpl);
    core_output(&app, &root, args).await
}

/// ネイティブダイアログをメインスレッドの外で開く（issue #84）。
///
/// Tauri 2 の**同期**コマンドはメインスレッドで実行されるため、rfd の
/// ダイアログをそのまま呼ぶと、開いている間 GUI プロセス全体（他の invoke を
/// 含む）が止まる（2026-09-02 実機の通し確認で 2 分以上ハングし、ダイアログを
/// 閉じると即再開）。`spawn_blocking` でワーカースレッドへ逃がす。
///
/// rfd 0.15 の Windows 実装（`backend/win_cid/utils.rs::init_com`）は
/// **呼び出しスレッドで** `CoInitializeEx(COINIT_APARTMENTTHREADED)` →
/// `CoUninitialize` を行うため、メインスレッド以外から呼んでよい。rfd 自身の
/// 非同期実装も専用スレッドを起こしている（`backend/win_cid/thread_future.rs`）。
///
/// `FileDialog` はクロージャの**中**で組み立てる（外で組み立てて move
/// させない）——渡すのは `String`／`PathBuf` だけにしておけば、rfd が将来
/// 親ウィンドウハンドルのような非 Send のフィールドを持っても壊れない。
/// `PickedPaths` のロックは await をまたがせない（またぐと未来が Send で
/// なくなりコマンドとして登録できない・`run_core` の PidSlot と同じ制約）
/// ——ここでは await が完了した後にだけ取る。
///
/// ワーカー側が panic した場合は `None`（＝キャンセルと同じ）に倒す。呼び出し
/// 側の契約が `Option<String>` の1本しかなく、Rust 側にログ基盤も無いため、
/// 区別できる情報を返す先が無い。以前はメインスレッドで panic していたので、
/// 「ダイアログが開かなかった」で済むぶん後退ではない。
async fn dialog<T, F>(build_and_show: F) -> Option<T>
where
    F: FnOnce() -> Option<T> + Send + 'static,
    T: Send + 'static,
{
    tauri::async_runtime::spawn_blocking(build_and_show).await.ok().flatten()
}

#[tauri::command]
async fn pick_folder(app: AppHandle) -> Option<String> {
    // run --input のパススコープ検査（issue S-MD）を通すには picked への
    // 登録が要る。以前は登録しておらず、フォルダ選択直後の run が
    // allowed_roots（アプリルート・workdir）の外だと拒否されていた
    // （出力フォルダは登録不要だが、同じダイアログを共用しているため
    // ここで一括登録しても実害は無い——読み書きコマンド側の拡張子制限は
    // 別に効いている）。
    let p = dialog(|| rfd::FileDialog::new().pick_folder()).await?;
    remember(&app.state::<PickedPaths>(), &p);
    Some(p.to_string_lossy().to_string())
}

#[tauri::command]
async fn pick_image(app: AppHandle) -> Option<String> {
    // テンプレ作成の入力はスキャン PDF のことが多い。PDF はコアの expand-page で
    // 1ページ目を PNG 展開してから表示する（フロント側 loadImage が分岐）
    let p = dialog(|| {
        rfd::FileDialog::new()
            .add_filter("帳票（PDF・画像）", &["pdf", "png", "jpg", "jpeg"])
            .pick_file()
    })
    .await?;
    remember(&app.state::<PickedPaths>(), &p);
    Some(p.to_string_lossy().to_string())
}


/// JSON ファイルを選ぶ（テンプレートの読み書き・認証キーの取り込み）。
///
/// `kind` はダイアログのフィルタ名だけを切り替える（issue #97）。省略時は
/// 従来どおり「テンプレート」——鍵の取り込み導線（RunScreen の
/// `import_credentials`）が `kind: "credentials"` を渡すまでは表示が変わらない
/// ので、フロント側の追従が要る。`save` の既定パス解決・白リスト登録の規則は
/// `kind` に依存しない（用途で分けているのは表示名だけ）。
#[tauri::command]
async fn pick_json(app: AppHandle, save: bool, remember_pick: Option<bool>,
                   default_path: Option<String>, kind: Option<String>) -> Option<String> {
    let filter_name = match kind.as_deref() {
        Some("credentials") => "認証キー（JSON）",
        _ => "テンプレート",
    };
    // 保存の既定は「エディタが今読み込んでいるファイル」（default_path）。
    // 指定が無い（起動時の自動読込のまま一度も別ファイルを開いていない）
    // ときだけ出荷テンプレートへフォールバックする。以前は保存の既定が
    // 常に出荷テンプレート固定だったため、別テンプレートを編集していても
    // Enter 1回で出荷テンプレートを上書きしてしまう経路になっていた
    // （issue #56 T1-3）。出荷テンプレへの保存だけは呼び出し側
    // （is_shipped_template_path・Editor.tsx）でも明示確認を挟む
    let mut start_dir: Option<PathBuf> = None;
    let mut start_name: Option<String> = None;
    if save {
        let dp = default_path.as_deref().map(Path::new)
            .filter(|p| !p.as_os_str().is_empty());
        if let Some(dp) = dp {
            start_dir = dp.parent()
                .filter(|p| !p.as_os_str().is_empty())
                .map(|p| p.to_path_buf());
            start_name = dp.file_name().map(|n| n.to_string_lossy().into_owned());
        } else if let Ok(root) = repo_root(&app) {
            start_dir = Some(root.join("templates"));
            start_name = Some("chouhyo-v1.json".to_string());
        }
    }
    let p = dialog(move || {
        let mut d = rfd::FileDialog::new().add_filter(filter_name, &["json"]);
        if let Some(dir) = start_dir {
            d = d.set_directory(dir);
        }
        if let Some(name) = start_name {
            d = d.set_file_name(name);
        }
        if save { d.save_file() } else { d.pick_file() }
    })
    .await?;
    // `kind:"credentials"` で選ばれたパスは PendingCredentialsPath の
    // 1回限りのスロットへ書き込む（issue #121）。import-credentials の
    // json_path はこの値と完全一致するときだけ通る
    // （check_arg_scopes → check_credentials_scope）。正規化に失敗する経路
    // （選択直後に対象が消える等）ではスロットを更新しない——その場合は
    // 後続の check_credentials_scope が「未選択」として拒否する。
    // `!save` を要求する（issue #126 M-3）——`kind:"credentials"` は現状
    // 取り込み（`save:false`）専用の使われ方だが、将来 `save:true` の呼び
    // 出しが増えても、実在しない保存先パスをスロットへ書き込んで
    // 一回限りトークンの意味を壊さないようにする
    if !save && kind.as_deref() == Some("credentials") {
        if let Ok(abs) = normalize_path(&p.to_string_lossy()) {
            *app.state::<PendingCredentialsPath>().0.lock().unwrap() = Some(abs);
        }
    }
    // 認証キーの取り込みは remember_pick=false で呼ぶ。白リストへ入れると
    // GCP サービスアカウント鍵（平文 JSON）がセッション中ずっと read_text で
    // 読める状態になる——鍵を DPAPI へ退避させる操作が、その鍵を読める窓を
    // 開けてしまう。テンプレートの読み書きだけが白リストの用途（issue #49）
    //
    // 既定を true のままにしているのは、テンプレート編集が任意の場所の JSON を
    // 開ける必要があるため（既定を false にすると全呼び出し側の修正が要る）。
    // ただし `kind:"credentials"` のときは呼び出し側の指定によらず登録しない
    // （issue #69 セキュリティ LOW (c)）——「鍵を選ぶダイアログ」で選ばれた
    // ものが白リストに載る経路を、呼び出し側の書き忘れ1つで開かないため。
    // fail-open な既定値に、用途による fail-closed の上書きを重ねる形にする。
    let remember_pick = remember_pick.unwrap_or(true)
        && kind.as_deref() != Some("credentials");
    if remember_pick {
        remember(&app.state::<PickedPaths>(), &p);
    }
    Some(p.to_string_lossy().to_string())
}

/// `pick_json(kind:"credentials")` が預けた1回限りのスロットを明示的に
/// 解放する（issue #126 M-3）。取り込みが実際には起きなかった経路
/// （利用者が確認ダイアログをキャンセルした・`import-credentials` 呼び出し
/// 自体が失敗した）でスロットが埋まったまま残ると、次に別の鍵をダイアログ
/// で選び直すまでその古いパスが「一回だけ使える」状態で残り続ける。
/// フロント側（RunScreen.tsx の確認キャンセル・取り込み失敗の catch）が
/// 呼ぶ想定——呼ばなくても次の `pick_json(kind:"credentials")` が上書きする
/// ため fail-safe だが、キャンセル直後に古いトークンを残さない方が安全。
#[tauri::command]
fn clear_pending_credentials(pending: State<'_, PendingCredentialsPath>) {
    *pending.0.lock().unwrap() = None;
}

#[tauri::command]
fn open_folder(app: AppHandle, path: String) -> Result<(), String> {
    // write_config（validate_config_patch の CONFIG_PATH_KEYS）と同じ判定を
    // 通す（issue #152）。ここが唯一パス検査を通らないコマンドで、UNC
    // （`\\host\share`）を渡すと下の `abs.is_dir()` の時点で SMB セッションが
    // 張られ、Windows が NTLMv2 資格情報を任意ホストへ送ってしまう——検査は
    // ファイルシステムに触れる**前**（canonicalize・is_dir いずれの前）に、
    // コンポーネントだけを見る `is_safe_root` で行う必要がある（同関数の
    // 呼び出し規約どおり）。`is_safe_root` は自身のドキュメントどおり
    // 空・ドライブ直下・UNC・`..` を1関数で拒否するため、`..` 用に
    // 別途 `normalize_path` を通す必要はない（`normalize_path` は保存先が
    // 未作成でも解決できるよう作られており、ここでは意味が重ならない）。
    if !is_safe_root(Path::new(&path)) {
        return Err(
            "フォルダとして開けないパスです（空・ドライブ直下・ネットワークパス・.. は指定できません）"
                .into(),
        );
    }
    let root = repo_root(&app)?;
    let p = PathBuf::from(&path);
    // CLI の相対パス設定（既定 "output"）は cwd=core 基準
    let abs = if p.is_absolute() { p } else { root.join("core").join(p) };
    // explorer は引数が実行可能ファイルなら起動してしまう（LOLBin）。
    // フォルダを開く用途しかないため、ディレクトリ以外は拒否する（issue #5）
    if !abs.is_dir() {
        return Err("フォルダではないため開けません".into());
    }
    // 絶対パス指定（issue #53 L-12）。フォルダを開くだけの操作でも、PATH に
    // 置かれた同名の explorer.exe を起動する余地は残さない
    Command::new(explorer_program())
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

/// config.json が受け付ける既知キー（`core/chouhyo_ocr/config.py` の
/// `Config` dataclass フィールドと一致させる・issue Q-MC/S-MA）。
///
/// 唯一の例外は末尾が `_fallback_reason` の2フィールド——1回限りの診断
/// 情報で `save_config` が `config.json` から除くため、GUI からも書けない
/// のが正しい（`config.py` の M-1）。
///
/// `last_template`（issue #72 (t)・07 FR-F29・08 設計 §3.5.1）は意図的に
/// `CONFIG_PATH_KEYS` に入れない——パスではなく「区分＋表示名」の文字列
/// （`"shipped"`（名前なし） | `"user:<name>"`。2026-09-02 coder_backend／
/// coder_frontend 実装済みの表記を正とする）であり、絶対パスは保存しない。
/// 型・形式の検証も他キーと同じ設計判断で Rust 側では行わない
/// （`validate_config_patch` のコメント参照）——不正値は読み出し時に
/// `resolve_last_template`／`user_templates::resolve_last_template_path` が
/// 例外を投げずに出荷既定へフォールバックする（AC-F60）。
///
/// 真偽値のキー（`CONFIG_BOOL_KEYS`）と `last_applied_template`
/// （`""` | `"shipped"` | `"user:<表示名>"`）は逆に、型と形を **ここでも**
/// 検証する（`validate_config_patch`）。真偽値は core の `_validate` が
/// bool 以外を `ConfigError` で拒否するため、書けてしまうと次のコア起動から
/// run/verify/render/remap がすべて立ち上がらない。`last_applied_template` は
/// core 側が例外を投げず `""`（記憶なし）へ倒す（`last_template` と同じ
/// AC-F60 の扱い）ので起動不能にはならないが、倒された記憶は画面に何も
/// 出ない——「適用したはずのテンプレートが次に開いたとき戻らない」形で
/// 消えるため、やはり書く手前で弾く。
const KNOWN_CONFIG_KEYS: &[&str] = &[
    "unclear_threshold", "era_threshold", "send_limit",
    "output_dir", "workdir", "log_dir",
    "api_monthly_cap", "unclear_char_level",
    "snap_blocks", "last_template",
    "auto_detect_frames_on_open", "last_applied_template",
];

/// パス文字列として安全性検査が要るキー（`is_safe_root` を通す・issue Q-MC/S-MA）。
const CONFIG_PATH_KEYS: &[&str] = &["output_dir", "workdir", "log_dir"];

/// core の `_validate` が **bool 以外を `ConfigError` にする** キー
/// （`config.py` の `Config` dataclass のうち真偽値のもの）。JSON の
/// `true`/`false` 以外（`"yes"`・`0`・`1`）を書かせると、次のコア起動から
/// 全コマンドが止まる。
const CONFIG_BOOL_KEYS: &[&str] = &[
    "unclear_char_level", "snap_blocks", "auto_detect_frames_on_open",
];

/// `last_applied_template` の形を検証する（編集タブの「このテンプレートを
/// 適用する」が書く記憶・07 v1.6 FR-F55。表記規則は FR-F29／§7.3 と同じ）。
///
/// 許すのは `""`（記憶なし）・`"shipped"`・`"user:<表示名>"` の3形だけ。
/// 表示名は利用者テンプレート保存と同じ `validate_name_shape` を通す——
/// 絶対パス（`C:\...`）・`..`・パス区切りは、いずれも同関数の文字種検査
/// （英数・かな漢字・`-`・`_`・空白のみ）で落ちる。**絶対パスは保存しない**
/// 方針（07 §7.3）はここが最後の関門になる。
fn validate_last_applied_template(s: &str) -> Result<(), String> {
    if s.is_empty() || s == "shipped" {
        return Ok(());
    }
    let Some(name) = s.strip_prefix("user:") else {
        return Err("last_applied_template は空文字・shipped・\
                    user:<テンプレート名> のいずれかで指定してください".to_string());
    };
    user_templates::validate_name_shape(name)
        .map(|_| ())
        .map_err(|e| format!("last_applied_template のテンプレート名が不正です: {e}"))
}

/// Python 側 `api_budget.py` の `FREE_TIER_UNITS`（無料枠）と同値
/// （issue #155）。webview から届く `write_config` はこの値までしか
/// `api_monthly_cap` を引き上げられないようにする——`api_monthly_cap` は
/// 「正しさ」の設定ではなく費用の遮断器で、core 側が受け付ける上限
/// （1,000,000・`config.py:132-136`）まで webview 経由で押し上げられると
/// 遮断器の意味が無い。config.json の**手編集**で 1,000,000 まで上げる
/// という文書化済みの逃げ道（`api_budget.py:76-81`）はそのまま残す——
/// 塞ぐのは webview 経路 1 本だけ。値がズレたら `api_budget.py` 側の定義を
/// 見て直すこと（二重管理だが、課金の遮断器という性質上ここは JSON 外出しに
/// しない）。
const API_MONTHLY_CAP_WEBVIEW_MAX: i64 = 1000;

/// `write_config` の patch を検証する（issue Q-MC/S-MA・#143・#155）。
///
/// 未知キーは拒否する——ここで止めないと、`config.json` へ書かれた未知キーが
/// 次のコア起動時に `load_config`（`config.py:76-78`）の `ConfigError` を
/// 引き起こし、run/render/verify を含む**全コマンドが起動不能**になる
/// （設定ファイル1つでローカル DoS が成立する）。output_dir/workdir/log_dir の
/// 3キーは `allowed_roots`（`read_file_b64` の読み取り範囲の起点）にそのまま
/// 使われるため、パスの安全性（空・ドライブ直下・UNC・`..`）も検査する。
///
/// **設計判断の変更（issue #143・#155）**: 数値4キー
/// （unclear_threshold・era_threshold・send_limit・api_monthly_cap）の
/// 型・範囲検証は、当初「`config.py:_validate` に一本化し、ここでは重複
/// させない」という判断だったが、2つの理由で不十分だった。(1) `send_limit`
/// に負値が書けてしまい、次回のコア起動で run/verify/render/remap が
/// すべて `ConfigError`（自己 DoS）になる。(2) `api_monthly_cap` は
/// 「正しさ」ではなく費用の遮断器であり、core の上限（1,000,000）に
/// 一本化すると、掌握された renderer がそこまで引き上げられる（issue
/// #155）。範囲は `config.py:_validate`（unclear_threshold/era_threshold は
/// `0 < v <= 1`・send_limit は `>= 0`）と**同じ境界**をここでも守る——
/// 独自に緩め/狭めた範囲を書くと、ここは通っても core が弾く（またはその
/// 逆）値が生まれ、`api_monthly_cap` を除いて2箇所の定義がズレたときに
/// 気づけない。ズレの検出は #143 の想定対処にある pytest（`KNOWN_CONFIG_KEYS`
/// と `Config.__dataclass_fields__` の比較）は範囲までは見ないため、
/// 境界値を変えるときは両ファイルを一緒に見ること。
///
/// 他のキー（`CONFIG_BOOL_KEYS`・`last_applied_template`）は従来どおり。
/// 表示名の文字種検証も利用者テンプレート保存と同じ
/// `user_templates::validate_name_shape` を使い、許可リストを二重に持たない。
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
    for key in CONFIG_BOOL_KEYS {
        let Some(v) = obj.get(*key) else { continue };
        if !v.is_boolean() {
            return Err(format!("{key} は true / false で指定してください"));
        }
    }
    // unclear_threshold / era_threshold: core の _validate（config.py:113-115）
    // と同じ `0 < v <= 1`。0 ちょうどは「〓閾値の無効化」に相当し core が拒否
    // するので、ここでも 0 を含めない（issue #143・#155）
    for key in ["unclear_threshold", "era_threshold"] {
        let Some(v) = obj.get(key) else { continue };
        let n = v.as_f64()
            .ok_or_else(|| format!("{key} は数値で指定してください"))?;
        if !(n > 0.0 && n <= 1.0) {
            return Err(format!("{key} は 0 より大きく 1 以下の数値にする必要があります"));
        }
    }
    // send_limit: core の _validate（config.py:117-119）と同じ「0 以上の整数」。
    // 0 は「送信しないドライラン」として正当な値なので下限には含める
    if let Some(v) = obj.get("send_limit") {
        let n = v.as_i64()
            .ok_or_else(|| "send_limit は整数で指定してください".to_string())?;
        if n < 0 {
            return Err("send_limit は 0 以上の整数にする必要があります".into());
        }
    }
    // api_monthly_cap: 課金の遮断器（issue #155）。core の上限（1,000,000）
    // より狭い API_MONTHLY_CAP_WEBVIEW_MAX を webview からの上限として使う
    if let Some(v) = obj.get("api_monthly_cap") {
        let n = v.as_i64()
            .ok_or_else(|| "api_monthly_cap は整数で指定してください".to_string())?;
        if !(0..=API_MONTHLY_CAP_WEBVIEW_MAX).contains(&n) {
            return Err(format!(
                "api_monthly_cap は 0〜{API_MONTHLY_CAP_WEBVIEW_MAX} の整数にする必要があります"
            ));
        }
    }
    if let Some(v) = obj.get("last_applied_template") {
        let s = v.as_str().ok_or_else(
            || "last_applied_template は文字列で指定してください".to_string())?;
        validate_last_applied_template(s)?;
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

/// 一時ファイル名のプロセス内一意な連番（issue #137）。`write_atomic_with` を
/// 呼ぶたび 1 進む。
static WRITE_ATOMIC_SEQ: AtomicU64 = AtomicU64::new(0);

/// `<path>.<pid>.<連番>.tmp` へ書いてから rename で置き換える（issue #97・#137・
/// `promote_staged` と同型）。rename を注入可能にしてあるのは、確定の rename が
/// 失敗する経路を単体テストで固定するため（`promote_with` と同じ理由——OS
/// レベルで rename 失敗を確実に誘発する方法が Windows に無い）。
///
/// 失敗しても既存ファイルは書き換わらない。途中で電源が落ちても、壊れた
/// 内容が本体へ入るのは rename の一瞬だけになる（Windows の `MoveFileEx`
/// 相当・`std::fs::rename` は既存ファイルを置き換える）。
///
/// **一時ファイル名（issue #137）**: 固定名 `<path>.tmp` だった頃は、Tauri
/// コマンドがスレッドプールで並行実行されるため2つの `write_config` が
/// 同じ tmp に書き合い、両方が rename すると内容が混ざった。pid＋連番で
/// プロセス内一意にする（Python 側 `config.py:294` の
/// `f"{p.name}.{os.getpid()}.tmp"` と同じ狙い。連番も足すのは、同一プロセス
/// 内での並行呼び出しは pid だけでは区別できないため）。
fn write_atomic_with<F>(path: &Path, content: &str, mut rename: F) -> Result<(), String>
where
    F: FnMut(&Path, &Path) -> std::io::Result<()>,
{
    let seq = WRITE_ATOMIC_SEQ.fetch_add(1, Ordering::Relaxed);
    let mut tmp_name = path.as_os_str().to_os_string();
    tmp_name.push(format!(".{}.{}.tmp", std::process::id(), seq));
    let tmp = PathBuf::from(tmp_name);
    std::fs::write(&tmp, content)
        .map_err(|e| format!("一時ファイルの書き込みに失敗しました（{:?}）", e.kind()))?;
    if let Err(e) = rename(&tmp, path) {
        // 書き損じの一時ファイルを残さない（次回の保存が古い内容の tmp を
        // 見つけて混乱するのを防ぐ）。削除できなくても元の失敗を返す。
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("保存の確定に失敗しました（{:?}）。設定は変更されていません",
                           e.kind()));
    }
    Ok(())
}

fn write_atomic(path: &Path, content: &str) -> Result<(), String> {
    write_atomic_with(path, content, |from, to| std::fs::rename(from, to))
}

/// `write_config` の read-modify-write（`config_file` を読む→`merge_config`→
/// `write_atomic`）をプロセス内で直列化する（issue #137）。tmp ファイル名を
/// 一意にしても、2つの `write_config` がほぼ同時に既存 config を読んでから
/// それぞれ別のキーだけ足して書き戻すと、後から rename した方が先の変更を
/// 消す（read-modify-write の後勝ち）。tmp 名の衝突とは別の問題なので、
/// 一意な tmp 名だけでは直らない——書き込み全体を1本のロックで囲む。
static WRITE_CONFIG_LOCK: Mutex<()> = Mutex::new(());

/// `write_config` の本体（issue #137）。`AppHandle` に依存する `config_file`
/// 解決と、ファイルに対する read-modify-write を分けておくことで、後者を
/// `AppHandle` なしに（実ファイルだけで）単体テストできるようにしてある
/// （`merge_config`／`write_atomic_with` と同じ「コマンドは薄いラッパ」方針）。
fn write_config_to_path(p: &Path, patch: &serde_json::Value) -> Result<(), String> {
    let _serialize = WRITE_CONFIG_LOCK.lock().unwrap();
    let existing = if p.exists() {
        Some(std::fs::read_to_string(p).map_err(|e| e.to_string())?)
    } else {
        None
    };
    let merged = merge_config(existing.as_deref(), patch)?;
    let text = serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?;
    write_atomic(p, &text)
}

/// 設定の部分更新（要件 §5.7: GUI で選んだ値を保存し次回既定値に）。他キーは保持する。
///
/// 書き込みは tmp + rename（issue #97）。`merge_config` は壊れた config への
/// 上書きを拒否するため、素の `fs::write` が途中で切れて config が壊れると
/// GUI からは二度と書けなくなる（防御と非アトミック書き込みが噛み合って
/// 自己修復不能になる）。テンプレート切替のたびに呼ばれる経路であり、
/// 書き込み頻度も低くない。
///
/// `WRITE_CONFIG_LOCK` で read-modify-write 全体を直列化する（issue #137）。
#[tauri::command]
fn write_config(app: AppHandle, patch: serde_json::Value) -> Result<(), String> {
    let p = config_file(&app)?;
    write_config_to_path(&p, &patch)
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

/// 「前回使ったテンプレート」（`config.last_template` の解決結果。無ければ
/// 出荷テンプレート）を読む（issue #58／#72 (t)・07 FR-F29・08 設計 §3.6）。
///
/// パスは固定で webview から受け取らない。read_text は #49 でダイアログ選択
/// パスのみに締めたため、エディタ起動時の自動読み込みはこの専用コマンドで
/// 行う（緩めると responses/ の記入値 JSON が読める穴が戻る）。run が既定で
/// 使うテンプレートと同じ解決規則（`resolve_last_template`）を使うため、
/// エディタは「1から作る画面」でなく「読み取りが実際に使っている欄を直す
/// 画面」として開ける——この一致は inject_default_template（issue #58）が
/// 保証している。同梱 exe 優先起動時は frozen 側の app_root() が core-dist
/// 側の別実体を指すため、注入なしでは「read_default_template はここ、run は
/// 別ファイル」という二重実体が成立していた。
///
/// 引数 `template` を渡すと config を見ずにその指定を解決する（2026-09-04・
/// 初回読み込みフロー設計 §4.4）。編集画面の自動適用が「出荷テンプレートを
/// 副作用なしで読む」ために使う——旧来は config.last_template を "shipped" へ
/// 書き戻してから読む回避策しかなく、画像を開いただけで実行タブの選択が
/// 出荷へ戻る隠れた副作用になっていた。名前付き引数なので `Option` の追加は
/// 既存の引数なし呼び出しと後方互換。
#[tauri::command]
fn read_default_template(app: AppHandle, template: Option<String>) -> Result<String, String> {
    let root = repo_root(&app)?;
    let p = match template {
        Some(t) => {
            let user_dir = user_templates::user_templates_dir(&app).ok();
            user_templates::resolve_last_template_path(&t, &root, user_dir.as_deref())
        }
        None => resolve_last_template(&app, &root),
    };
    // 絶対パスは webview へ返さない。既存の read_text/write_text と同じ
    // 粒度（固定文言＋OS エラーの Display 表現のみ）に揃える（issue #61 L-2）。
    // 実害は情報開示のみ（CSP で外部送出は塞がれている）だが、他コマンドと
    // 不揃いだった
    std::fs::read_to_string(&p).map_err(|e| format!("既定テンプレートを読み込めません: {e}"))
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
/// （最終レビュー H-1）。Windows では読み取り専用属性がファイル作成を
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

/// `.bak` を残す期間（issue #65-11）。これより古い `.bak` は次の保存成功時に
/// 掃除する。
const BACKUP_MAX_AGE: std::time::Duration =
    std::time::Duration::from_secs(7 * 24 * 60 * 60);

/// 保存成功時に、古い `.bak` を掃除する（issue #65-11）。
///
/// **世代方式ではなく日付方式を選んだ理由**: `.bak` の名前は
/// `<保存先>.bak` の固定名で、`promote_with` の rename が毎回同じ名前を
/// 置き換える。つまり「同名の世代」は仕組み上そもそも1つしか存在せず、
/// 「1世代だけ残す」は掃除契機にならない（何も消すものが無い）。実際に
/// `templates/` へ溜まるのは **別のテンプレートを保存したときの `.bak`** と、
/// **元の `.json` を消した後に残る孤児の `.bak`** で、これらは名前では
/// 区別できず更新日時でしか掃除できない。
///
/// 対象は「保存先と同じフォルダ直下」「`.json.bak` で終わる通常ファイル」
/// 「`keep`（今回作ったばかりの `.bak`）以外」「最終更新が `max_age` より
/// 古い」の全てを満たすものだけ。`.json.bak` に限るのは、自分が作る
/// バックアップの命名（保存先は必ず `.json`）と一致させて、利用者が自分で
/// 置いた `.bak` を巻き込まないため。reparse point（symlink・ジャンクション）
/// は対象にしない——リンクを消すつもりがリンク先を消す事故を作らない。
///
/// 失敗は握って続ける（掃除は保存の付随処理で、保存自体を失敗させない）。
/// 戻り値は消せた件数（単体テスト用）。
fn sweep_old_backups(dir: &Path, keep: &Path, max_age: std::time::Duration,
                     now: std::time::SystemTime) -> usize {
    let Ok(entries) = std::fs::read_dir(dir) else { return 0 };
    let mut removed = 0;
    for entry in entries.flatten() {
        let path = entry.path();
        if path == keep {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else { continue };
        if !name.to_ascii_lowercase().ends_with(".json.bak") {
            continue;
        }
        let Ok(meta) = std::fs::symlink_metadata(&path) else { continue };
        if !meta.file_type().is_file() {
            continue;   // ディレクトリ・symlink・ジャンクション
        }
        let Ok(modified) = meta.modified() else { continue };
        let Ok(age) = now.duration_since(modified) else { continue };  // 未来日時は触らない
        if age > max_age && std::fs::remove_file(&path).is_ok() {
            removed += 1;
        }
    }
    removed
}

/// staged ファイルを本番パスへ確定する（issue #56 T1・保存経路のトランザクション化）。
fn promote_staged(abs: &Path) -> Result<(), String> {
    let staged = staged_path(abs);
    let bak = backup_path(abs);
    promote_with(&staged, abs, &bak, |from, to| std::fs::rename(from, to))?;
    // 確定できた後にだけ掃除する（issue #65-11）。失敗経路では `.bak` が
    // 唯一の復元元になりうるので絶対に触らない
    if let Some(dir) = abs.parent() {
        sweep_old_backups(dir, &bak, BACKUP_MAX_AGE, std::time::SystemTime::now());
    }
    Ok(())
}

/// staged ファイルを「既存の名前を外してから新規作成」で書く
/// （issue #69 セキュリティ LOW (a)・ハードリンク対策）。
///
/// #89 の `ensure_not_reparse_point` は symlink／ジャンクションを弾くが、
/// **ハードリンク**は `is_symlink()` に掛からない（同じ実体への別名であって
/// reparse point ではない）。あらかじめ `<名前>.json.saving.json` を範囲外の
/// ファイルへのハードリンクとして置かれると、`fs::write` はその実体を
/// 切り詰めて上書きしてしまう。
///
/// 判定で防ぐ手もあるが、リンク数（`nlink`）を読む
/// `std::os::windows::fs::MetadataExt::number_of_links` は stable では
/// 使えない（`windows_by_handle`・rust-lang/rust#63010。rustc 1.94.1 で
/// E0658 を実測）。新しい依存（windows-sys）を足さずに同じ結果を得るため、
/// **名前を外してから `create_new` で作る**方式にする——ハードリンクの
/// 名前を1つ消してもリンク先の中身は消えず、その後に作るのは必ず新しい
/// 実体になる。検査と書き込みの間に差し込まれても `create_new` は既存
/// ファイルへは書かずに失敗する（TOCTOU で起こせるのは失敗だけ）。
fn write_staged_fresh(staged: &Path, content: &str) -> Result<(), String> {
    use std::io::Write;
    match std::fs::remove_file(staged) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => {
            return Err(format!("一時ファイルを準備できません（{:?}）", e.kind()));
        }
    }
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(staged)
        .map_err(|e| format!("一時ファイルの書き込みに失敗しました（{:?}）", e.kind()))?;
    f.write_all(content.as_bytes())
        .map_err(|e| format!("一時ファイルの書き込みに失敗しました（{:?}）", e.kind()))
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
    // 既存の名前を外してから新規作成する（issue #69 セキュリティ LOW (a)）。
    // 置かれていたのがハードリンクでも、その実体へ書き込まない
    write_staged_fresh(&staged, &content)?;
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

// ============================================================================
// 利用者テンプレート（issue #72 (t)・08 設計 §3.2）
//
// `templates_user/` 配下は webview へ絶対パスを一切返さない（表示名のみ）。
// 列挙・パス検査は Rust に一本化し、Python 側に同じ規則を書かせない
// （08 設計 §3.2.1）。純関数本体は `user_templates` モジュールへ集約し、
// ここでは AppHandle からの解決とコマンド境界だけを担う。
// ============================================================================

/// 保存済み利用者テンプレートの一覧（FR-F28・RunScreen の選択肢・保存時の
/// 同名検出）。`templates_user/` 直下のみを非再帰で走査する（出荷テンプレは
/// 列挙しない・07 §7.3）。
#[tauri::command]
fn list_user_templates(app: AppHandle) -> Result<serde_json::Value, String> {
    let dir = user_templates::user_templates_dir(&app)?;
    let result = user_templates::list_dir(&dir);
    Ok(serde_json::json!({
        "templates": result.templates,
        "excluded": result.excluded,
    }))
}

/// 保存済み利用者テンプレートを表示名で読む（編集画面での既定復元・FR-F29）。
#[tauri::command]
fn read_user_template(app: AppHandle, name: String) -> Result<String, String> {
    let dir = user_templates::user_templates_dir(&app)?;
    let path = user_templates::resolve_existing_entry(&dir, &name)?;
    std::fs::read_to_string(&path).map_err(|e| e.to_string())
}

/// 利用者テンプレートを任意名で保存する（FR-F26）。
///
/// 名前検証（許可リスト方式・07 §7.4）→ staged 書き込み → コア `verify` →
/// 検証 OK なら promote、を Rust の中で通し切る（#56 T1 の「検証 NG のまま
/// 上書きしない」不変条件を維持）。同名が既にあり `overwrite=false` の場合は
/// `"AlreadyExists"` を返す——上書き確認そのものは GUI 側の責務（08 §3.2.3）。
#[tauri::command]
async fn save_user_template(app: AppHandle, name: String, content: String,
                            overwrite: bool) -> Result<String, String> {
    // issue #126 (3)（記録のみ・未対応）: この async fn は下の
    // list_all_stems/write_staged_fresh/promote_staged 等（user_templates.rs）
    // 経由で std::fs を spawn_blocking なしに同期呼び出ししている。対象は
    // 利用者テンプレート JSON（数 KB〜数十 KB）のみで体感する遅延は無い想定
    // ——扱うファイルが大きくなる／件数が増える設計変更が入るときに
    // spawn_blocking へ切り出すことを検討する。
    // M-4 追補（セキュリティレビュー）: 書き込み前にサイズ上限を掛ける。
    if content.len() as u64 > user_templates::MAX_TEMPLATE_BYTES {
        return Err("テンプレートの内容が大きすぎます".into());
    }
    let dir = user_templates::user_templates_dir(&app)?;
    let root = repo_root(&app)?;
    // M-1 追補（セキュリティレビュー）: 衝突判定は件数上限・内容解析なしの全件
    // 列挙（list_all_stems）で行う。list_dir ベースだと21件目以降や
    // 壊れた/大きすぎる同名ファイルを確認なしで上書きしてしまう。
    let existing = user_templates::list_all_stems(&dir);
    let shipped = user_templates::list_shipped_stems(&root);
    let verdict = user_templates::validate_user_template_name(&name, &existing, &shipped)?;
    let (normalized, is_new) = match verdict {
        user_templates::NameVerdict::New(n) => (n, true),
        user_templates::NameVerdict::Overwrites(n) => {
            if !overwrite {
                return Err("AlreadyExists".into());
            }
            (n, false)
        }
    };
    let target = dir.join(format!("{normalized}.json"));
    // 名前検証（07 §7.4）がパス区切りと `..` を落としているため、この字句
    // 比較は構造上必ず真になる——名前検証が将来ゆるんだ場合に備えた後段
    // 防御であって、07 §7.3 の「canonicalize 後の親一致」はこれではなく
    // 書き込み後の ensure_written_inside が担う（#89）。
    if target.parent() != Some(dir.as_path()) {
        return Err("保存先がテンプレート保存フォルダの外です".into());
    }
    // L-2 追補（#90）: `New` 判定なのに実ファイルがある場合の最終防御。
    // Rust の衝突判定（NFC＋Unicode の小文字化）と NTFS の大小文字畳み込みは
    // 一致しないため、別名と見なした先に実体があることがありうる。
    //
    // `overwrite=true` のときは掛けない——GUI は `AlreadyExists` を受けて
    // 上書き確認を出し、同じ名前で再送する（`Editor.tsx` の
    // saveAsUserTemplate）。ここで無条件に弾くと、確認を通しても常に
    // `AlreadyExists` が返り、その名前では二度と保存できなくなる。
    // 目的は「確認なしの上書きを起こさない」ことであって、上書きの禁止では
    // ない（08 §3.2.3: 確認は UI の責務）。
    if is_new && !overwrite {
        user_templates::recheck_new_target_absent(&target)?;
    }
    let staged = staged_path(&target);
    // #89: 保存先・一時ファイルのどちらかが reparse point なら書かない。
    // `fs::write` はリンクを辿るため、この検査が無いと templates_user 内に
    // 置かれたリンク1本で範囲外への書き込みになる（列挙・読み取りは同じ
    // 検査を既に通している・07 §7.3）。
    user_templates::ensure_not_reparse_point(&staged)?;
    user_templates::ensure_not_reparse_point(&target)?;
    // M-2 追補: OS エラーの Display 表現に絶対パスが混入する余地を断つ
    // ため、固定文言＋種別のみを返す。
    // 加えて、既存の名前を外してから create_new で作る（issue #69
    // セキュリティ LOW (a)）——reparse point 検査に掛からないハードリンクを
    // 経由して範囲外のファイルを上書きしない。
    write_staged_fresh(&staged, &content)?;
    // #89: 書けた実体が templates_user 直下にあることを canonicalize して
    // 確認する。外れていたら書いたファイルを消して失敗させる（検査前に
    // 差し替えられた場合でも、範囲外に中身を残さない）。
    if let Err(e) = user_templates::ensure_written_inside(&staged, &dir) {
        let _ = discard_staged_file(&target);
        return Err(e);
    }

    // H-1 対応（セキュリティレビュー）: verify の判定は終了コードに依存しない
    // 専用経路（core_output_stdout_only）で行う。core_output は非 0 終了を
    // 一律エラー扱いするため、資格情報未設定等テンプレート検証と無関係な
    // 理由で verify が非 0 終了すると、以前の実装は常に「検証失敗」と
    // 誤判定して staged を破棄していた（利用者の編集が消える）。
    let verify_args = vec![
        "verify".to_string(),
        "--template".to_string(),
        staged.to_string_lossy().to_string(),
    ];
    let stdout = match core_output_stdout_only(&app, &root, verify_args).await {
        Ok(s) => s,
        Err(e) => {
            let _ = discard_staged_file(&target);
            return Err(e);
        }
    };

    if user_templates::verify_template_ok(&stdout) {
        promote_staged(&target)
            // M-2 追補: promote_with の失敗メッセージは staged/target/bak の
            // 絶対パスを含む（既存の promote_template コマンドは picked＝
            // 利用者がダイアログで選んだパスなのでこれで問題ないが、
            // templates_user 配下は webview に絶対パスを返さない方針
            // （08 §3.2.1）のため、ここでは固定文言に差し替える）。
            .map_err(|_| "保存の確定に失敗しました。もう一度保存し直してください".to_string())?;
        // 「promote 後に実ファイルが存在すること」を返り値の前提として保証する。
        if !target.is_file() {
            return Err("保存を確定しましたが、ファイルが見つかりません。もう一度お試しください".into());
        }
        // L-4 追補: 利用者テンプレートでは .bak を残さない（既存の
        // promote_template コマンド／picked 経由の保存では復旧用に残す
        // 設計のままなので backup_path/promote_with 自体は変更しない）。
        let _ = std::fs::remove_file(backup_path(&target));
    } else {
        let _ = discard_staged_file(&target);
    }
    // M-2r 追補（セキュリティレビュー・実例再確認）: 既知の絶対パス文字列を
    // 単純置換する mask_known_paths は撤回した。コア側の OSError は
    // repr() 経由で二重エスケープされたバックスラッシュを伴って JSON
    // 文字列へ混入し、単純な文字列一致では拾えない（実測: パス区切り
    // 1文字が stdout 上で `\\\\`（4連）になる）。許可キーだけを通す
    // sanitize_verify_output へ切り替える。
    Ok(user_templates::sanitize_verify_output(&stdout))
}

/// 開いた画像に対し、指定した利用者テンプレート（表示名の一覧）と出荷
/// テンプレートを照合する（FR-F28・NFR-F09）。
///
/// `input` は既存の読み取りルート検査（`workdir/editor_pages` 配下、または
/// pick 済みパス）を通す。`names` は文字種・実在・エントリ安全性を検査して
/// 絶対パスへ解決し、解決できないものは `excluded[]` に理由付きで積んで
/// 続行する（1件の不正で照合ループ全体を止めない）。コアの `results[].name`
/// は表示名（stem）であり、Rust 側は絶対パスを webview へ一切返さない。
#[tauri::command]
async fn match_templates(app: AppHandle, picked: State<'_, PickedPaths>,
                         input: String, names: Vec<String>) -> Result<String, String> {
    let root = repo_root(&app)?;
    let input_abs = normalize_path(&input)?;
    {
        let roots = allowed_roots(&app)?;
        let picked_set = picked.0.lock().unwrap();
        check_scope(&input_abs, &["png", "jpg", "jpeg"], &roots, &picked_set)?;
    }
    let dir = user_templates::user_templates_dir(&app)?;
    // issue #126 (3)（記録のみ・未対応）: classify_candidates
    // （user_templates.rs）は候補ごとに std::fs を spawn_blocking なしに
    // 同期呼び出しする。候補は利用者テンプレート一覧（通常数件〜数十件の
    // JSON）で体感する遅延は無い想定——件数が大きく増える設計変更が入る
    // ときに spawn_blocking へ切り出すことを検討する。
    let (candidate_paths, excluded) = user_templates::classify_candidates(&dir, &names)?;

    let shipped = root.join("templates").join("chouhyo-v1.json");
    let args = user_templates::build_match_args(&input_abs, &shipped, &candidate_paths);

    // L-1 追補（#90）: `core_output` は非 0 終了時にコアの stderr をそのまま
    // 返すため、`ConfigError` 経由で `workdir` 等の絶対パスが webview へ出る
    // 経路になっていた（07 §7.3）。`save_user_template` と同じ
    // `core_output_stdout_only`（起動失敗だけを固定文言で Err にする）＋
    // 許可キー方式へ揃える。終了コードは見ない——判定は stdout の
    // `match_templates` イベント1行（`ok`）が唯一の正。
    let stdout = core_output_stdout_only(&app, &root, args).await?;
    let value: serde_json::Value = stdout
        .lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .find(|v| v.get("event").and_then(|e| e.as_str()) == Some("match_templates"))
        .ok_or_else(|| "コアからの応答を解釈できません".to_string())?;
    let merged = user_templates::merge_excluded_into(
        user_templates::sanitize_match_output(&value), excluded);
    serde_json::to_string(&merged).map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(CoreProc(Mutex::new(None)))
        .manage(PickedPaths(Mutex::new(HashSet::new())))
        .manage(PendingCredentialsPath(Mutex::new(None)))
        .manage(DropActive(Mutex::new(true)))
        // ドラッグ＆ドロップのパスは OS のイベントから直接受け取る（issue S-N1）。
        // webview 側（RunScreen.tsx の onDragDropEvent）は同じドロップを受けて
        // 入力欄の表示を更新するだけで、白リストへの登録には関与しない——
        // webview から任意パスを登録できる経路（旧 remember_dropped_path）は
        // PickedPaths の前提そのものを崩すため削除した。
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::DragDrop(tauri::DragDropEvent::Drop { paths, .. }) => {
                // ドロップを受ける画面が表示されているときだけ登録する
                // （issue #69 セキュリティ LOW (b)）。編集タブを見ている
                // 最中のドロップは、画面上は何も起きないのに白リストだけが
                // 増えていた——見えない権限拡大を残さない
                if !*window.state::<DropActive>().0.lock().unwrap() {
                    return;
                }
                remember_dropped(&window.state::<PickedPaths>(), paths);
            }
            tauri::WindowEvent::CloseRequested { .. } => {
                // ウィンドウを閉じても実行中の core は自動では止まらない
                // （issue #135）。`PidSlot::drop` は run_core の spawn_blocking
                // ワーカースレッドの中にあり、アプリが `std::process::exit`
                // 相当で終了するとそちらの Drop は走らない——ここで明示的に
                // kill する。RunEvent::ExitRequested/Exit（下の `.run` 側）と
                // 同じ `kill_running_core` を呼ぶことで、kill を実行する経路を
                // 1本に保つ
                let _ = kill_running_core(&window.state::<CoreProc>().0);
            }
            _ => {}
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
            clear_pending_credentials,
            read_default_template,
            open_folder,
            read_config,
            write_config,
            set_drop_active,
            read_file_b64,
            read_text,
            write_text,
            write_template_staged,
            promote_template,
            discard_staged,
            is_shipped_template_path,
            list_user_templates,
            read_user_template,
            save_user_template,
            match_templates
        ])
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            // アプリ終了イベントでも同じ経路を通す（issue #135）。ウィンドウが
            // 複数あるとき・OS のセッション終了など `CloseRequested` を経ずに
            // 落ちる経路の保険として、`ExitRequested`・`Exit` の両方で拾う
            // （二重に呼んでも `kill_running_core` はスロットが既に空なら
            // 何もしない・冪等）。
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                let _ = kill_running_core(&app_handle.state::<CoreProc>().0);
            }
        });
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
    fn denies_unknown_and_empty() {
        assert!(check_args_v2(&v(&["--config", "x", "run"])).is_err());
        assert!(check_args_v2(&v(&[])).is_err());
        assert!(check_args_v2(&v(&["debug-images"])).is_err());
    }

    #[test]
    fn purge_accepts_only_yes_include_output_and_preview() {
        // issue #52 M-11: GUI から呼べるようにしたが、受け付けるのはこの3つだけ
        assert!(check_args_v2(&v(&["purge", "--yes"])).is_ok());
        let pairs = check_args_v2(&v(&["purge", "--yes", "--include-output"])).unwrap();
        assert_eq!(pairs, vec![("--yes".to_string(), String::new()),
                               ("--include-output".to_string(), String::new())]);
        // 消す場所を差し替えられるフラグ・値付き・位置引数はすべて拒否する
        assert!(check_args_v2(&v(&["purge", "--config", "C:\\other.json"])).is_err());
        assert!(check_args_v2(&v(&["purge", "--yes=1"])).is_err());
        assert!(check_args_v2(&v(&["purge", "C:\\somewhere"])).is_err());
        assert!(check_args_v2(&v(&["purge", "--input", "C:\\in"])).is_err());
    }

    #[test]
    fn purge_accepts_preview_flag_alone_and_combined_with_yes() {
        // issue #108: 削除対象パス・件数を確認するためのプレビュー専用フラグ。
        // コア側は --preview を優先するので --yes との併用も引数検査は通す
        let pairs = check_args_v2(&v(&["purge", "--preview"])).unwrap();
        assert_eq!(pairs, vec![("--preview".to_string(), String::new())]);
        let pairs = check_args_v2(&v(&["purge", "--preview", "--yes"])).unwrap();
        assert_eq!(pairs, vec![("--preview".to_string(), String::new()),
                               ("--yes".to_string(), String::new())]);
        // 値付き・未知フラグは従来どおり拒否
        assert!(check_args_v2(&v(&["purge", "--preview=1"])).is_err());
        assert!(check_args_v2(&v(&["purge", "--dry-run"])).is_err());
    }

    // --- 子プロセスへの argv 再構築（issue #126 (2)）---
    use super::rebuild_args;

    #[test]
    fn rebuild_args_reconstructs_argv_from_checked_pairs() {
        let pairs = vec![("--input".to_string(), "C:\\abs\\in".to_string()),
                         ("--template".to_string(), "C:\\abs\\t.json".to_string())];
        assert_eq!(rebuild_args("run", &pairs).unwrap(),
                   v(&["run", "--input", "C:\\abs\\in", "--template", "C:\\abs\\t.json"]));
    }

    #[test]
    fn rebuild_args_keeps_boolean_flags_without_a_value() {
        let pairs = vec![("--yes".to_string(), String::new()),
                         ("--include-output".to_string(), String::new())];
        assert_eq!(rebuild_args("purge", &pairs).unwrap(),
                   v(&["purge", "--yes", "--include-output"]));
    }

    #[test]
    fn rebuild_args_treats_json_path_as_a_positional_argument() {
        let pairs = vec![("json_path".to_string(), "C:\\abs\\key.json".to_string()),
                         ("--delete-source".to_string(), String::new())];
        assert_eq!(rebuild_args("import-credentials", &pairs).unwrap(),
                   v(&["import-credentials", "C:\\abs\\key.json", "--delete-source"]));
    }

    #[test]
    fn rebuild_args_does_not_drop_the_value_of_a_value_taking_flag_even_if_empty() {
        // `--dpi ""` のように値そのものが空文字である正当な入力を、
        // 「値を取らないフラグ」と取り違えて値を落とさない。判定は pairs の
        // 値の空/非空ではなく allowed_flags の表（唯一の正）で行う
        let pairs = vec![("--dpi".to_string(), String::new())];
        assert_eq!(rebuild_args("detect-grid", &pairs).unwrap(),
                   v(&["detect-grid", "--dpi", ""]));
    }

    #[test]
    fn rebuild_args_errs_on_a_flag_absent_from_the_table() {
        // issue #126 L-2: 表に無いフラグに出会ったら黙って値ありと決めない
        let pairs = vec![("--not-a-real-flag".to_string(), "x".to_string())];
        assert!(rebuild_args("run", &pairs).is_err());
    }

    #[test]
    fn rebuild_args_drops_yes_when_preview_is_present_regardless_of_order() {
        // issue #126 M-5: コアが preview を優先する実装であることに頼らず、
        // argv の時点で --yes を落とす。フラグの並び順に関わらず消える
        let preview_then_yes = vec![("--preview".to_string(), String::new()),
                                    ("--yes".to_string(), String::new())];
        assert_eq!(rebuild_args("purge", &preview_then_yes).unwrap(), v(&["purge", "--preview"]));

        let yes_then_preview = vec![("--yes".to_string(), String::new()),
                                    ("--preview".to_string(), String::new())];
        assert_eq!(rebuild_args("purge", &yes_then_preview).unwrap(), v(&["purge", "--preview"]));

        // --preview が無ければ --yes は残る
        let yes_only = vec![("--yes".to_string(), String::new())];
        assert_eq!(rebuild_args("purge", &yes_only).unwrap(), v(&["purge", "--yes"]));
    }

    // --- core-err・core_output の絶対パス伏せ字化（issue #126 (1)・M-1・M-2）---
    use super::redact_absolute_paths;

    #[test]
    fn redact_absolute_paths_masks_drive_unc_and_unix_style_paths() {
        assert_eq!(
            redact_absolute_paths("設定ファイルが見つかりません: C:\\Users\\someone\\config.json"),
            "設定ファイルが見つかりません: [path]");
        assert_eq!(
            redact_absolute_paths("共有 \\\\server\\share\\file.txt が開けません"),
            "共有 [path] が開けません");
        assert_eq!(
            redact_absolute_paths("CHOUHYO_USER_DIR は絶対パスにする（現在: '/home/someone/x'）"),
            "CHOUHYO_USER_DIR は絶対パスにする（現在: '[path]'）",
            "ConfigError の実際の書式（全角括弧が空白なしで引用符を囲む）を再現");
    }

    #[test]
    fn redact_absolute_paths_leaves_ordinary_text_with_slashes_untouched() {
        // 左境界チェック（is_path_start）が無いと and/or・日付表記まで
        // 巻き込んでしまう
        assert_eq!(redact_absolute_paths("入力と出力（input/output）は両方必須です"),
                   "入力と出力（input/output）は両方必須です");
        assert_eq!(redact_absolute_paths("2026/09/07 に実行"), "2026/09/07 に実行");
    }

    #[test]
    fn redact_absolute_paths_masks_multiple_occurrences_in_one_line() {
        assert_eq!(
            redact_absolute_paths("C:\\a\\b から C:\\c\\d へコピーできません"),
            "[path] から [path] へコピーできません");
    }

    #[test]
    fn redact_absolute_paths_spans_spaces_inside_a_path_until_a_hard_boundary() {
        // issue #126 M-2: 空白で即座に打ち切ると `C:\Program Files\...` の
        // 後半（`Files\...`）が伏せ残る（実測バグの再現）
        assert_eq!(
            redact_absolute_paths(
                "読み込み失敗: C:\\Program Files\\ChouhyoOCR\\logs\\core.log が壊れています"),
            "読み込み失敗: [path] が壊れています");
        // 姓名の間の空白（2箇所）も同様に伏せきる
        assert_eq!(
            redact_absolute_paths(
                "エラー: C:\\Users\\山田 太郎\\Desktop\\a.pdf にアクセスできません"),
            "エラー: [path] にアクセスできません");
    }

    #[test]
    fn redact_absolute_paths_starts_right_after_equals_sign() {
        // issue #126 M-2: `KEY=/path` 形（空白なしでイコールの直後から
        // パスが始まる）も伏せる
        assert_eq!(
            redact_absolute_paths("CHOUHYO_USER_DIR=/home/someone/.chouhyo が見つかりません"),
            "CHOUHYO_USER_DIR=[path] が見つかりません");
    }

    #[test]
    fn redact_absolute_paths_masks_file_uri_and_verbatim_prefix() {
        // issue #126 M-2 L-4: file:// URI と \\?\ verbatim 接頭辞
        assert_eq!(
            redact_absolute_paths("テンプレート file:///C:/app/templates/chouhyo-v1.json を開けません"),
            "テンプレート [path] を開けません");
        assert_eq!(
            redact_absolute_paths("パス \\\\?\\C:\\Users\\someone\\a.json が不正です"),
            "パス [path] が不正です");
    }

    #[test]
    fn import_credentials_accepts_delete_source_flag() {
        // issue #52 M-10: 取り込み後に元の平文鍵を消すフラグ（値は取らない）
        let pairs = check_args_v2(&v(&["import-credentials", "C:\\key.json",
                                       "--delete-source"])).unwrap();
        assert_eq!(pairs, vec![("json_path".to_string(), "C:\\key.json".to_string()),
                               ("--delete-source".to_string(), String::new())]);
        assert!(check_args_v2(&v(&["import-credentials", "C:\\key.json",
                                   "--delete-source=1"])).is_err());
        // 他のサブコマンドには生えていない
        assert!(check_args_v2(&v(&["run", "--input", "x", "--delete-source"])).is_err());
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
    fn import_credentials_rejects_a_second_positional_argument() {
        // issue #126 L-3: 2つ目の位置引数はスコープ検査（PendingCredentialsPath
        // の消費）まで進む前にここで拒否する。1つ目が正当な選択と一致して
        // 消費された「後」に2つ目で全体が失敗すると、消費だけが起きて
        // 正当な取り込みが二度とできなくなる
        assert!(check_args_v2(&v(&["import-credentials", "C:\\a.json", "C:\\b.json"])).is_err());
        assert!(check_args_v2(
            &v(&["import-credentials", "C:\\a.json", "C:\\b.json", "--delete-source"])).is_err());
    }

    #[test]
    fn detect_grid_accepts_its_full_flag_set() {
        let pairs = check_args_v2(&v(&["detect-grid", "--image", "C:\\p.png",
                                       "--region", "1,2,3,4", "--mode", "ruled",
                                       "--rows", "2", "--cols", "3", "--dpi", "300"])).unwrap();
        assert_eq!(pairs.len(), 6);
    }

    #[test]
    fn detect_frames_accepts_its_full_flag_set() {
        // issue #73 (b): detect-frames は新しい権限を要らない
        // （run_core_capture 経由・--input は既存の読み取りルート検査に従う）。
        let pairs = check_args_v2(&v(&["detect-frames", "--input", "C:\\in",
                                       "--page", "1", "--dpi", "300",
                                       "--template", "C:\\t.json"])).unwrap();
        assert_eq!(pairs.len(), 4);
    }

    #[test]
    fn detect_frames_rejects_unknown_flag() {
        assert!(check_args_v2(&v(&["detect-frames", "--input", "C:\\in",
                                   "--not-a-real-flag", "x"])).is_err());
    }

    #[test]
    fn detect_frames_rejects_dpi_without_value() {
        assert!(check_args_v2(&v(&["detect-frames", "--input", "C:\\in", "--dpi"])).is_err());
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

    // --- テンプレート既定値の注入（issue #58・#72 (t) で経路を分離）---
    // 「どのパスを注入するか」（config の last_template 解決・AppHandle 依存）
    // と「注入するかどうか」（純関数）を分離した（08 設計 §3.5.2）。
    // ここは後者のみを検証する——前者は
    // user_templates::resolve_last_template_path の表駆動テストで担保する。
    use super::inject_default_template;

    #[test]
    fn injects_template_for_accepting_subcommand_without_one() {
        let tpl = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        let out = inject_default_template(v(&["run", "--input", "x"]), &tpl);
        assert_eq!(out, v(&["run", "--input", "x", "--template",
                            "C:\\app\\templates\\chouhyo-v1.json"]));
    }

    #[test]
    fn does_not_override_explicit_template_or_unrelated_subcommand() {
        let tpl = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        // 明示指定済みなら触らない
        let explicit = v(&["render", "--template", "C:\\other\\t.json"]);
        assert_eq!(inject_default_template(explicit.clone(), &tpl), explicit);
        // --template を持たないサブコマンドはそのまま（status・detect-grid 等）
        let status = v(&["status"]);
        assert_eq!(inject_default_template(status.clone(), &tpl), status);
        // 空引数は何もしない（check_args_v2 で先に弾かれる想定だが、単体では防御的に）
        assert_eq!(inject_default_template(v(&[]), &tpl), v(&[]));
        // --template=path（等号形式）も明示指定として扱う（issue L-3）
        let eq_form = v(&["render", "--template=C:\\other\\t.json"]);
        assert_eq!(inject_default_template(eq_form.clone(), &tpl), eq_form);
    }

    #[test]
    fn does_not_inject_template_into_detect_frames() {
        // レビュー H-3: detect-frames へは注入しない。注入されると core が
        // 除外領域の白潰し・face_id 割り当て・overlaps_existing 判定に加えて
        // --dpi をテンプレートの render_dpi で上書きし、GUI が空の
        // テンプレートで作った候補から罫線が黙って消える。
        // detect-frames は --template の既定値が None の唯一のサブコマンドで、
        // #58 の「CLI 既定値が別実体を指す」問題が起きない
        let tpl = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        let bare = v(&["detect-frames", "--input", "C:\\in", "--dpi", "300"]);
        assert_eq!(inject_default_template(bare.clone(), &tpl), bare);
        // GUI が自分で渡した場合はそのまま（二重に積まない）
        let explicit = v(&["detect-frames", "--input", "C:\\in", "--template", "C:\\u\\t.json"]);
        assert_eq!(inject_default_template(explicit.clone(), &tpl), explicit);
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

    // --- 確定 rename 失敗時の巻き戻し（最終レビュー H-1）---
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

        assert!(check_arg_scopes(&pairs(&[("--input", &pdf)]), &roots, &picked, &mut None).is_err(),
                "選ばれていないファイルを展開させてはいけない");
        picked.insert(fx.abs(&pdf));
        assert!(check_arg_scopes(&pairs(&[("--input", &pdf)]), &roots, &picked, &mut None).is_ok(),
                "pick_image で選ばれた PDF は通る（編集画面の正当な用途）");
    }

    #[test]
    fn arg_scopes_returns_absolute_paths_so_child_process_cwd_basis_cannot_differ() {
        // issue #126 (2): 検査を通した値は常に絶対パス（normalize_path の結果）
        // で返る。子プロセスは <root>/core を cwd に起動する一方、検査は
        // この関数を呼ぶ GUI プロセスの cwd を基準に解決する——生の
        // （相対でありうる）文字列をそのまま子へ渡すと、検査した対象と
        // 実際に開かれる対象がずれうる。rebuild_args がこの戻り値だけを
        // 使って argv を組むことで、そのずれを構造的に無くす
        let fx = ScopeFixture::new("abs_out");
        let roots = fx.roots();
        let mut picked = HashSet::new();
        let pdf = fx.outside.join("scan.pdf");
        picked.insert(fx.abs(&pdf));

        let checked = check_arg_scopes(&pairs(&[("--input", &pdf)]), &roots, &picked, &mut None)
            .expect("picked 済みのパスは通るはず");
        assert_eq!(checked.len(), 1);
        let (flag, value) = &checked[0];
        assert_eq!(flag, "--input");
        assert!(Path::new(value).is_absolute(),
                "戻り値は絶対パスであるべき: {value}");
        assert_eq!(Path::new(value), fx.abs(&pdf).as_path(),
                   "normalize_path と同じ正規化結果になるべき");
    }

    #[test]
    fn arg_scopes_allow_detect_grid_image_only_from_editor_pages_or_picked() {
        let fx = ScopeFixture::new("image");
        let roots = fx.roots();
        let mut picked = HashSet::new();
        let page = fx.pages.join("page1.png");
        let outside_png = fx.outside.join("elsewhere.png");

        assert!(check_arg_scopes(&pairs(&[("--image", &page)]), &roots, &picked, &mut None).is_ok(),
                "expand-page が書いた editor_pages の PNG は通る");
        assert!(check_arg_scopes(&pairs(&[("--image", &outside_png)]), &roots, &picked, &mut None).is_err());
        picked.insert(fx.abs(&outside_png));
        assert!(check_arg_scopes(&pairs(&[("--image", &outside_png)]), &roots, &picked, &mut None).is_ok());
        // 拡張子違いは picked でも拒否（画像コマンドで JSON を読ませない）
        let json = fx.outside.join("t.json");
        picked.insert(fx.abs(&json));
        assert!(check_arg_scopes(&pairs(&[("--image", &json)]), &roots, &picked, &mut None).is_err());
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

        assert!(check_arg_scopes(&pairs(&[("--template", &staged)]), &roots, &picked, &mut None).is_ok(),
                "保存フローの一時ファイルは拒否されてはいけない");
        assert!(check_arg_scopes(&pairs(&[("--template", &target)]), &roots, &picked, &mut None).is_ok(),
                "picked 本体もこれまでどおり通る");

        // 本体が picked に無い `.saving.json` は通さない（任意ファイルを
        // `.saving.json` という名前で読ませる抜け道を作らない）
        let orphan = super::staged_path(&fx.abs(&fx.outside.join("other.json")));
        std::fs::write(&orphan, "{}").unwrap();
        assert!(check_arg_scopes(&pairs(&[("--template", &orphan)]), &roots, &picked, &mut None).is_err());
        assert!(!is_staged_of_picked(&fx.abs(&orphan), &picked));
    }

    #[test]
    fn arg_scopes_ignore_flags_without_paths() {
        let fx = ScopeFixture::new("other");
        let roots = fx.roots();
        let picked = HashSet::new();
        let flags = vec![("--region".to_string(), "1,2,3,4".to_string()),
                         ("--mode".to_string(), "uniform".to_string()),
                         ("--dpi".to_string(), "300".to_string())];
        assert!(check_arg_scopes(&flags, &roots, &picked, &mut None).is_ok());
    }

    // --- import-credentials の json_path スコープ（issue #121）---
    use super::{check_credentials_scope, PendingCredentialsPath};

    #[test]
    fn credentials_scope_allows_only_the_pending_path_and_consumes_it() {
        let fx = ScopeFixture::new("cred_ok");
        let key = fx.outside.join("t.json");
        let abs = fx.abs(&key);

        let mut pending = Some(abs.clone());
        assert!(check_credentials_scope(&abs, &mut pending).is_ok(),
                "pick_json(kind:credentials) が預けたパスそのものは通る");
        assert_eq!(pending, None, "一致したらスロットは消費されて空になる");

        // 消費済みなので同じパスの2回目はもう通らない
        assert!(check_credentials_scope(&abs, &mut pending).is_err(),
                "消費後の再送は拒否されるべき（一回限り）");
    }

    #[test]
    fn credentials_scope_rejects_unselected_or_mismatched_path() {
        let fx = ScopeFixture::new("cred_ng");
        let key = fx.outside.join("t.json");
        let other = fx.outside.join("other.json");
        let abs = fx.abs(&key);

        // スロットが空（一度も pick_json(kind:credentials) を呼んでいない）
        let mut empty: Option<PathBuf> = None;
        assert!(check_credentials_scope(&abs, &mut empty).is_err(),
                "ダイアログを経ていない任意パスは拒否されるべき");

        // 別のパスが預けられている（選び直し前・攻撃者の推測）場合も拒否し、
        // 正当な選択の分は消費せず残す
        let mut pending = Some(fx.abs(&other));
        assert!(check_credentials_scope(&abs, &mut pending).is_err(),
                "預けたパスと一致しない指定は拒否されるべき");
        assert_eq!(pending, Some(fx.abs(&other)),
                   "不一致の試行でスロットを消費してはいけない（正当な選択がまだ使える）");
    }

    #[test]
    fn check_arg_scopes_routes_json_path_through_credentials_scope() {
        // check_arg_scopes 経由でも同じ判定になることを確認（_ => {} の
        // 免除を外した本体）
        let fx = ScopeFixture::new("cred_route");
        let roots = fx.roots();
        let picked = HashSet::new();
        let key = fx.outside.join("t.json");
        let abs = fx.abs(&key);

        let mut pending = None;
        assert!(check_arg_scopes(&pairs(&[("json_path", &key)]), &roots, &picked, &mut pending)
                    .is_err(), "未選択の json_path は拒否されるべき");

        let mut pending = Some(abs);
        assert!(check_arg_scopes(&pairs(&[("json_path", &key)]), &roots, &picked, &mut pending)
                    .is_ok(), "スロットと一致する json_path は通るべき");
        assert_eq!(pending, None, "check_arg_scopes 経由でも消費される");
    }

    #[test]
    fn pending_credentials_path_state_defaults_to_empty() {
        // Tauri の .manage() 登録と同じ初期値（None）を固定する
        let state = PendingCredentialsPath(Mutex::new(None));
        assert_eq!(*state.0.lock().unwrap(), None);
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
    }

    #[test]
    fn validate_config_patch_rejects_non_string_path_value() {
        assert!(validate_config_patch(&json!({"workdir": 123})).is_err());
    }

    #[test]
    fn validate_config_patch_accepts_last_template_without_path_checks() {
        // issue #72 (t): last_template は CONFIG_PATH_KEYS に含めない
        // （パスではなく区分＋表示名の文字列のため）。ドライブ直下相当の
        // 見た目の値でも Rust 側では拒否しない——不正な形式は読み出し時に
        // resolve_last_template_path が出荷既定へフォールバックする。
        assert!(validate_config_patch(&json!({"last_template": "user:sample"})).is_ok());
        assert!(validate_config_patch(&json!({"last_template": ""})).is_ok());
        assert!(validate_config_patch(&json!({"last_template": "C:\\"})).is_ok());
    }

    #[test]
    fn validate_config_patch_checks_bool_keys_type() {
        for key in ["unclear_char_level", "snap_blocks", "auto_detect_frames_on_open"] {
            assert!(validate_config_patch(&json!({key: true})).is_ok(), "{key}: true");
            assert!(validate_config_patch(&json!({key: false})).is_ok(), "{key}: false");
            // core の _validate は bool 以外を ConfigError にする（書かせると
            // 次の起動から全コマンドが止まる）。0/1 も bool ではない
            assert!(validate_config_patch(&json!({key: "yes"})).is_err(), "{key}: 文字列");
            assert!(validate_config_patch(&json!({key: 1})).is_err(), "{key}: 整数");
        }
    }

    // --- 数値キーの型・範囲検証（issue #143・#155）---
    // 旧テスト `validate_config_patch_checks_path_keys_only` は
    // `send_limit: -1` が通ることを「型/範囲検証は core 側に一本化している
    // （設計判断）」として固定していた。この設計判断は反転した——
    // send_limit の負値は次回のコア起動を ConfigError で全滅させる自己 DoS、
    // api_monthly_cap は core の上限（1,000,000）まで webview 経由で押し上げ
    // られると課金の遮断器として機能しない（issue #155）。以下はその反転後の
    // 挙動を固定する。

    #[test]
    fn validate_config_patch_rejects_negative_or_non_integer_send_limit() {
        // 反転: 以前は「core 側に一本化」の根拠として -1 が通ることを固定
        // していたが、次回のコア起動で run/verify/render/remap がすべて
        // ConfigError になる自己 DoS だったため拒否に変える（issue #143）
        assert!(validate_config_patch(&json!({"send_limit": -1})).is_err(),
                "負値は次回起動を ConfigError で止める自己 DoS になるため拒否する");
        assert!(validate_config_patch(&json!({"send_limit": 0})).is_ok(),
                "0 は送信しないドライランとして core が正当な値にしている");
        assert!(validate_config_patch(&json!({"send_limit": 100})).is_ok());
        assert!(validate_config_patch(&json!({"send_limit": 3.5})).is_err(), "整数でない");
        assert!(validate_config_patch(&json!({"send_limit": true})).is_err(), "真偽値は整数ではない");
    }

    #[test]
    fn validate_config_patch_checks_unclear_and_era_threshold_range() {
        // core の _validate（config.py:113-115）と同じ `0 < v <= 1`。0 は
        // 「〓閾値の無効化」に相当し core が拒否するため、境界に含めない
        for key in ["unclear_threshold", "era_threshold"] {
            assert!(validate_config_patch(&json!({key: 0.85})).is_ok(), "{key}: 通常値");
            assert!(validate_config_patch(&json!({key: 1})).is_ok(), "{key}: 上限（整数1も許可）");
            assert!(validate_config_patch(&json!({key: 0})).is_err(), "{key}: 0 は下限含まず拒否");
            assert!(validate_config_patch(&json!({key: -0.1})).is_err(), "{key}: 負値");
            assert!(validate_config_patch(&json!({key: 1.1})).is_err(), "{key}: 上限超え");
            assert!(validate_config_patch(&json!({key: "0.5"})).is_err(), "{key}: 文字列");
        }
    }

    #[test]
    fn validate_config_patch_caps_api_monthly_cap_below_core_limit() {
        // issue #155: core の上限は 1,000,000 だが、webview からはそこまで
        // 引き上げさせない。API_MONTHLY_CAP_WEBVIEW_MAX（FREE_TIER_UNITS 相当）
        // が実際の境界
        assert!(validate_config_patch(&json!({"api_monthly_cap": 900})).is_ok(), "既定値相当");
        assert!(validate_config_patch(&json!({"api_monthly_cap": 0})).is_ok(), "下限 0 は許可");
        assert!(validate_config_patch(&json!({"api_monthly_cap": super::API_MONTHLY_CAP_WEBVIEW_MAX})).is_ok(),
                "上限ちょうどは許可");
        assert!(validate_config_patch(&json!({"api_monthly_cap": super::API_MONTHLY_CAP_WEBVIEW_MAX + 1})).is_err(),
                "上限+1 は拒否");
        assert!(validate_config_patch(&json!({"api_monthly_cap": 1_000_000})).is_err(),
                "core が受け付ける上限まで webview からは引き上げさせない（issue #155 の核心）");
        assert!(validate_config_patch(&json!({"api_monthly_cap": -1})).is_err(), "負値");
    }

    #[test]
    fn validate_config_patch_checks_last_applied_template_shape() {
        for ok in ["", "shipped", "user:sample", "user:サンプル 帳票"] {
            assert!(validate_config_patch(&json!({"last_applied_template": ok})).is_ok(),
                    "{ok}: 受理する3形");
        }
        // 絶対パスは保存しない（07 §7.3）。区分無し・空の名前・.. ・パス区切りも
        // validate_name_shape の文字種検査で落ちる。core 側は同じ値を例外に
        // せず ""（記憶なし）へ倒すので、ここで弾かないと「適用したはずの
        // テンプレートが黙って忘れられる」だけになる
        for ng in ["C:\\templates\\a.json", "user:C:\\templates\\a", "user:",
                   "user:..", "user:a\\b", "user:a/b", "user:a:b", "sample",
                   "shipped:sample"] {
            assert!(validate_config_patch(&json!({"last_applied_template": ng})).is_err(),
                    "{ng}: 拒否する");
        }
        assert!(validate_config_patch(&json!({"last_applied_template": 5})).is_err(),
                "非文字列");
    }

    // --- config.json の tmp + rename 書き込み（issue #97）---
    use super::{write_atomic, write_atomic_with};

    #[test]
    fn write_atomic_replaces_existing_content_and_leaves_no_tmp() {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_write_atomic_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("config.json");

        write_atomic(&target, "{\"a\":1}").unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"a\":1}");
        write_atomic(&target, "{\"a\":2}").unwrap();
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"a\":2}",
                   "既存ファイルを置き換える");

        let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().flatten()
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| n.ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "一時ファイルが残っている: {leftovers:?}");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_atomic_failure_keeps_existing_file_and_removes_tmp() {
        // 確定の rename が失敗しても、既存の config.json は無傷のまま
        // ——merge_config は壊れた config への上書きを拒否するため、
        // ここで中身が半端に残ると GUI から二度と保存できなくなる（#97）。
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_write_atomic_fail_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("config.json");
        std::fs::write(&target, "{\"keep\":true}").unwrap();

        let err = write_atomic_with(&target, "{\"new\":true}", |_, _| {
            Err(std::io::Error::new(std::io::ErrorKind::PermissionDenied, "denied"))
        })
        .unwrap_err();

        assert!(err.contains("設定は変更されていません"), "{err}");
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"keep\":true}");
        // 一時ファイル名は `<path>.<pid>.<連番>.tmp`（issue #137）に変わったため、
        // 固定名ではなく拡張子で leftovers を判定する（1つ目のテストと同じ流儀）
        let leftovers: Vec<_> = std::fs::read_dir(&dir).unwrap().flatten()
            .map(|e| e.file_name().to_string_lossy().to_string())
            .filter(|n| n.ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty(), "失敗時に一時ファイルを残さない: {leftovers:?}");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_atomic_with_uses_a_pid_and_sequence_scoped_tmp_name() {
        // issue #137: 固定名 `config.json.tmp` だと2つの書き込みが同じ tmp を
        // 取り合う。名前そのものに pid と連番が入ることを確認する
        // （rename を差し替えて tmp のパスを覗き見る）。
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_write_atomic_tmpname_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("config.json");

        let mut seen_tmp_name = String::new();
        write_atomic_with(&target, "{}", |from, to| {
            seen_tmp_name = from.file_name().unwrap().to_string_lossy().to_string();
            std::fs::rename(from, to)
        })
        .unwrap();

        let pid = std::process::id().to_string();
        assert!(seen_tmp_name.starts_with("config.json."), "{seen_tmp_name}");
        assert!(seen_tmp_name.ends_with(".tmp"), "{seen_tmp_name}");
        assert!(seen_tmp_name.contains(&pid), "pid を含む: {seen_tmp_name}");
        assert_ne!(seen_tmp_name, "config.json.tmp", "固定名には戻さない");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_config_to_path_serializes_concurrent_writes_without_losing_either_key() {
        // issue #137: 2 スレッドが同時に別々のキーだけを patch すると、
        // read-modify-write が後勝ちで先の変更を消していた。
        // WRITE_CONFIG_LOCK で直列化した後は、順序に関わらず両方のキーが残る。
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_write_config_concurrent_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let target = dir.join("config.json");
        std::fs::write(&target, "{}").unwrap();

        let t1 = {
            let target = target.clone();
            std::thread::spawn(move || {
                super::write_config_to_path(&target, &json!({"workdir": "a"}))
            })
        };
        let t2 = {
            let target = target.clone();
            std::thread::spawn(move || {
                super::write_config_to_path(&target, &json!({"output_dir": "b"}))
            })
        };
        t1.join().unwrap().unwrap();
        t2.join().unwrap().unwrap();

        let saved: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&target).unwrap()).unwrap();
        assert_eq!(saved.get("workdir").and_then(|v| v.as_str()), Some("a"),
                   "先勝ちでも後勝ちでも、直列化されていれば消えない: {saved}");
        assert_eq!(saved.get("output_dir").and_then(|v| v.as_str()), Some("b"),
                   "先勝ちでも後勝ちでも、直列化されていれば消えない: {saved}");

        let _ = std::fs::remove_dir_all(&dir);
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

    // --- 終了経路の kill を1本化（issue #135・#141）---
    // kill_pid（実プロセスへの taskkill）は起動せず、`kill_running_core_with`
    // に渡す `killer` をダミーへ差し替えて状態遷移だけを検証する。
    use super::kill_running_core_with;

    #[test]
    fn kill_running_core_with_holds_slot_until_killer_returns_then_clears() {
        let state = Mutex::new(Some(999u32));
        let mut seen_pid = None;
        let result = kill_running_core_with(&state, |pid| {
            // killer が呼ばれている時点でもスロットにはまだ pid が残っている
            // ことを、外側の state を直接読める形で確認する
            // （lock() は killer 内で取り直すと自己デッドロックするため行わない
            //   ——それ自体が「kill 完了までロックを保持する」設計の裏付け）。
            seen_pid = Some(pid);
            Ok(())
        });
        assert_eq!(seen_pid, Some(999), "killer には現在の pid が渡る");
        assert_eq!(result, Ok(true), "実行中の core があれば true");
        assert_eq!(*state.lock().unwrap(), None, "kill 完了後にスロットが解放される");
    }

    #[test]
    fn kill_running_core_with_returns_false_without_calling_killer_when_idle() {
        let state: Mutex<Option<u32>> = Mutex::new(None);
        let mut called = false;
        let result = kill_running_core_with(&state, |_| {
            called = true;
            Ok(())
        });
        assert_eq!(result, Ok(false), "実行中の core が無ければ false");
        assert!(!called, "対象が無ければ kill 手段を呼ばない");
    }

    #[test]
    fn kill_running_core_with_clears_slot_even_if_killer_fails() {
        // kill が失敗しても、次回実行を永久にブロックしないようスロットは
        // 解放する（旧 kill_core の take() と同じ「失敗しても後始末はする」
        // 挙動を維持しつつ、ロック保持のタイミングだけを直す）。
        let state = Mutex::new(Some(7u32));
        let result = kill_running_core_with(&state, |_| Err("停止できませんでした".into()));
        assert!(result.is_err());
        assert_eq!(*state.lock().unwrap(), None, "失敗時もスロットは解放する");
    }

    // --- PID 再利用レース（issue #53 L-13）---
    use super::PidSlot;

    #[test]
    fn pid_slot_release_clears_slot_and_later_drop_does_not_touch_it() {
        // release() はロックを取ってその場でスロットを空にする。呼び出し側は
        // このとき Child（プロセスハンドル）をまだ持っており、pid は再利用
        // されない——「終了済みの pid がスロットに残っている」時間が無くなる。
        let state = Mutex::new(Some(4242u32));
        {
            let mut guard = PidSlot::new(&state, 4242);
            guard.release();
            assert_eq!(*state.lock().unwrap(), None, "release でスロットが空になる");
            // 直後に別の実行が始まり、たまたま同じ pid を取った状況を作る
            *state.lock().unwrap() = Some(4242);
        }   // ここで PidSlot::drop
        assert_eq!(*state.lock().unwrap(), Some(4242),
                   "release 済みの Drop はスロットにもプロセスにも触らない");
    }

    // --- Windows 標準実行ファイルの絶対パス解決（issue #53 L-12）---
    use super::system_program;

    #[test]
    fn system_program_uses_system_root_when_present() {
        assert_eq!(
            system_program(Some("C:\\Windows".into()), "System32\\taskkill.exe", "taskkill"),
            PathBuf::from("C:\\Windows\\System32\\taskkill.exe"));
        assert_eq!(
            system_program(Some("C:\\Windows".into()), "explorer.exe", "explorer"),
            PathBuf::from("C:\\Windows\\explorer.exe"));
    }

    #[test]
    fn system_program_falls_back_to_bare_name_without_system_root() {
        // 環境変数が無い・空なら従来どおり PATH 解決へ（機能を壊さない側へ倒す）
        assert_eq!(system_program(None, "explorer.exe", "explorer"),
                   PathBuf::from("explorer"));
        assert_eq!(system_program(Some("".into()), "explorer.exe", "explorer"),
                   PathBuf::from("explorer"));
    }

    // --- 古い .bak の掃除（issue #65-11）---
    use super::{sweep_old_backups, write_staged_fresh, BACKUP_MAX_AGE};
    use std::time::{Duration, SystemTime};

    fn scratch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir()
            .join(format!("chouhyo_{tag}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn sweep_old_backups_removes_only_aged_json_bak_files() {
        let dir = scratch_dir("sweep_old");
        let keep = dir.join("current.json.bak");
        let orphan = dir.join("deleted-template.json.bak");
        let other = dir.join("notes.txt.bak");     // 自分の命名規則ではない
        let live = dir.join("current.json");
        for p in [&keep, &orphan, &other, &live] {
            std::fs::write(p, "x").unwrap();
        }
        // 実ファイルの mtime は動かせないので「今」を未来へずらして老化させる
        let future = SystemTime::now() + Duration::from_secs(3600);
        let removed = sweep_old_backups(&dir, &keep, Duration::from_secs(60), future);

        assert_eq!(removed, 1, "掃除対象は孤児の .json.bak 1件だけ");
        assert!(!orphan.exists(), "7日より古い .json.bak は消える");
        assert!(keep.exists(), "今回作った .bak（1世代）は残す");
        assert!(other.exists(), "利用者が置いた .bak を巻き込まない");
        assert!(live.exists(), "テンプレート本体は対象外");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sweep_old_backups_keeps_recent_files() {
        let dir = scratch_dir("sweep_recent");
        let bak = dir.join("t.json.bak");
        std::fs::write(&bak, "x").unwrap();
        let removed = sweep_old_backups(&dir, &dir.join("other.json.bak"),
                                        BACKUP_MAX_AGE, SystemTime::now());
        assert_eq!(removed, 0, "作ったばかりの .bak は残る（直前の内容の復元元）");
        assert!(bak.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn promote_staged_keeps_the_fresh_backup_and_sweeps_old_ones() {
        // promote 成功時に掃除が走っても、今回の .bak は消えない
        let dir = scratch_dir("sweep_promote");
        let target = dir.join("t.json");
        std::fs::write(&target, "old").unwrap();
        std::fs::write(staged_path(&target), "new").unwrap();

        promote_staged(&target).expect("promote は成功するはず");

        assert_eq!(std::fs::read_to_string(backup_path(&target)).unwrap(), "old");
        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- staged 書き込みのハードリンク対策（issue #69 セキュリティ LOW (a)）---

    #[test]
    fn write_staged_fresh_does_not_write_through_a_hard_link() {
        let dir = scratch_dir("staged_hardlink");
        let victim = dir.join("victim.json");
        std::fs::write(&victim, "secret").unwrap();
        let staged = dir.join("t.json.saving.json");
        // 攻撃者が先回りして staged の名前を範囲外ファイルへのハードリンクに
        // しておく状況（symlink ではないので #89 の reparse point 検査は素通り）
        std::fs::hard_link(&victim, &staged).unwrap();

        write_staged_fresh(&staged, "new content").unwrap();

        assert_eq!(std::fs::read_to_string(&victim).unwrap(), "secret",
                   "リンク先の実体を書き換えてはいけない");
        assert_eq!(std::fs::read_to_string(&staged).unwrap(), "new content",
                   "staged 自体は新しい実体として書けている");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn write_staged_fresh_replaces_an_ordinary_existing_file() {
        let dir = scratch_dir("staged_plain");
        let staged = dir.join("t.json.saving.json");
        std::fs::write(&staged, "前回の書きかけ").unwrap();
        write_staged_fresh(&staged, "新しい内容").unwrap();
        assert_eq!(std::fs::read_to_string(&staged).unwrap(), "新しい内容");
        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- 実行 ID の採番とイベント payload（issue #96）---
    use super::{format_run_id, next_run_id, CoreLine, RunResult, RunStart};

    #[test]
    fn format_run_id_joins_pid_and_sequence() {
        assert_eq!(format_run_id(4321, 0), "4321-0");
        assert_eq!(format_run_id(4321, 7), "4321-7");
        // pid が違えば連番が同じでも別の ID（GUI を起動し直すと連番は 0 に
        // 戻るため、pid が無いと再起動をまたいで同じ ID が生まれる）
        assert_ne!(format_run_id(4321, 0), format_run_id(4322, 0));
    }

    #[test]
    fn next_run_id_is_unique_and_monotonic_within_the_process() {
        // 採番が重複すると、フロントの「今回の run_id 以外を捨てる」判定が
        // 前後の実行を区別できなくなる（issue #96 の防御が無効化する）
        let ids: Vec<String> = (0..64).map(|_| next_run_id()).collect();
        let uniq: std::collections::HashSet<&String> = ids.iter().collect();
        assert_eq!(uniq.len(), ids.len(), "採番が重複した: {ids:?}");

        let seq_of = |id: &str| -> u64 {
            id.rsplit_once('-').expect("<pid>-<連番> 形式").1
                .parse().expect("連番は数値")
        };
        for w in ids.windows(2) {
            assert!(seq_of(&w[1]) > seq_of(&w[0]),
                    "連番が単調増加していない: {} → {}", w[0], w[1]);
        }
    }

    #[test]
    fn next_run_id_is_unique_across_threads() {
        // 採番は AtomicU64。同時に採番されても衝突しないことを固定する
        let ids: Vec<String> = std::thread::scope(|sc| {
            let hs: Vec<_> = (0..8)
                .map(|_| sc.spawn(|| (0..32).map(|_| next_run_id()).collect::<Vec<_>>()))
                .collect();
            hs.into_iter().flat_map(|h| h.join().unwrap()).collect()
        });
        let uniq: std::collections::HashSet<&String> = ids.iter().collect();
        assert_eq!(uniq.len(), ids.len(), "並行採番で重複した");
    }

    #[test]
    fn event_payloads_carry_the_run_id_as_json() {
        // フロント（RunScreen の acceptsRunEvent）が読むキー名を固定する。
        // ここがズレると run_id が undefined になり、フィルタが素通りになる
        let line = serde_json::to_value(CoreLine {
            run_id: "1-2".into(), line: "{\"event\":\"summary\"}".into(),
        }).unwrap();
        assert_eq!(line["run_id"], "1-2");
        assert_eq!(line["line"], "{\"event\":\"summary\"}");

        let start = serde_json::to_value(RunStart { run_id: "1-2".into() }).unwrap();
        assert_eq!(start["run_id"], "1-2");

        let result = serde_json::to_value(RunResult { code: 0, run_id: "1-2".into() }).unwrap();
        assert_eq!(result["code"], 0);
        assert_eq!(result["run_id"], "1-2");
    }

    // --- コア実体の選択（2026-09-02 実測: 同梱 exe の陳腐化事故）---
    use super::{resolve_core_program, CoreProgram};

    /// resolve_core_program 用の実ファイル環境。.git・venv・同梱 exe を
    /// 組み合わせて配置する。環境変数は書き換えない
    /// （resolve_core_program が override を引数で受け取る設計にしてあるのは、
    /// 並列テスト実行時に env var の競合を避けるため）。
    struct CoreProgramFixture {
        root: PathBuf,
    }

    impl CoreProgramFixture {
        fn new(name: &str) -> Self {
            let root = std::env::temp_dir()
                .join(format!("chouhyo_coreprog_{name}_{}", std::process::id()));
            // 前回異常終了した残骸が残っていると create_dir_all 後もファイルが
            // 混在しうる（レビュー LOW 指摘）。作成前に必ず一度掃除しておく
            let _ = std::fs::remove_dir_all(&root);
            std::fs::create_dir_all(&root).unwrap();
            Self { root }
        }

        fn with_git(self) -> Self {
            std::fs::create_dir_all(self.root.join(".git")).unwrap();
            self
        }

        /// `.git` が**ファイル**のケース（worktree・submodule）。`resolve_core_program`
        /// は `exists()` で判定しておりファイルでも真になるため現状の挙動は
        /// 正しいが、将来 `is_dir()` 等へ書き換えられて壊れないよう固定する
        /// （レビュー LOW 指摘）。
        fn with_git_file(self) -> Self {
            std::fs::write(self.root.join(".git"), "gitdir: /path/to/real/gitdir").unwrap();
            self
        }

        fn with_venv(self) -> Self {
            std::fs::create_dir_all(self.venv_path().parent().unwrap()).unwrap();
            std::fs::write(self.venv_path(), "x").unwrap();
            self
        }

        fn with_bundled(self) -> Self {
            std::fs::create_dir_all(self.bundled_path().parent().unwrap()).unwrap();
            std::fs::write(self.bundled_path(), "x").unwrap();
            self
        }

        fn venv_path(&self) -> PathBuf {
            self.root.join(".venv").join("Scripts").join("python.exe")
        }

        fn bundled_path(&self) -> PathBuf {
            self.root.join("core-dist").join("chouhyo-core").join("chouhyo-core.exe")
        }
    }

    impl Drop for CoreProgramFixture {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.root);
        }
    }

    #[test]
    fn resolve_core_program_prefers_venv_in_dev_checkout() {
        // .git + venv + bundled が揃っていても、開発チェックアウトでは
        // venv を優先する（同梱 exe の陳腐化事故の再発防止）
        let fx = CoreProgramFixture::new("dev_checkout").with_git().with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Venv(fx.venv_path())));
    }

    #[test]
    fn resolve_core_program_uses_bundled_when_not_a_dev_checkout() {
        // venv があっても .git が無ければ「開発チェックアウト」ではない
        // （インストール済み配布物のレイアウトを想定）
        let fx = CoreProgramFixture::new("no_git").with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Bundled(fx.bundled_path())));
    }

    #[test]
    fn resolve_core_program_uses_bundled_only() {
        let fx = CoreProgramFixture::new("bundled_only").with_bundled();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Bundled(fx.bundled_path())));
    }

    #[test]
    fn resolve_core_program_uses_venv_only() {
        let fx = CoreProgramFixture::new("venv_only").with_venv();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Venv(fx.venv_path())));
    }

    #[test]
    fn resolve_core_program_errs_when_nothing_present() {
        let fx = CoreProgramFixture::new("nothing");
        assert!(resolve_core_program(&fx.root, None).is_err());
    }

    #[test]
    fn resolve_core_program_override_bundled_wins_over_dev_checkout() {
        let fx = CoreProgramFixture::new("override_bundled")
            .with_git().with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, Some("bundled")),
                   Ok(CoreProgram::Bundled(fx.bundled_path())));
    }

    #[test]
    fn resolve_core_program_override_bundled_errs_without_bundled() {
        let fx = CoreProgramFixture::new("override_bundled_missing")
            .with_git().with_venv();
        assert!(resolve_core_program(&fx.root, Some("bundled")).is_err());
    }

    #[test]
    fn resolve_core_program_override_venv_errs_without_venv() {
        let fx = CoreProgramFixture::new("override_venv_missing").with_bundled();
        assert!(resolve_core_program(&fx.root, Some("venv")).is_err());
    }

    #[test]
    fn resolve_core_program_override_venv_succeeds_when_venv_present() {
        // override "venv" の成功パス（テスト担当の提案）
        let fx = CoreProgramFixture::new("override_venv_ok").with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, Some("venv")),
                   Ok(CoreProgram::Venv(fx.venv_path())));
    }

    #[test]
    fn resolve_core_program_unknown_override_is_err() {
        // MEDIUM-6: 未知の値を黙って自動判定へ落とさない
        let fx = CoreProgramFixture::new("unknown_override")
            .with_git().with_venv().with_bundled();
        let err = resolve_core_program(&fx.root, Some("foo"))
            .expect_err("未知の override 値は Err になるべき");
        assert!(err.contains("foo"), "{err}");
        assert!(err.contains("bundled") && err.contains("venv"), "{err}");
    }

    #[test]
    fn resolve_core_program_override_is_trimmed_and_case_insensitive() {
        // MEDIUM-6: 前後空白＋大文字混じりでも判定できる
        let fx = CoreProgramFixture::new("override_whitespace_case")
            .with_git().with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, Some(" BUNDLED ")),
                   Ok(CoreProgram::Bundled(fx.bundled_path())));
    }

    #[test]
    fn resolve_core_program_empty_override_is_treated_as_unspecified() {
        // MEDIUM-6: 空文字（トリム後含む）は未指定と同じ扱い＝自動判定
        let fx = CoreProgramFixture::new("override_empty")
            .with_git().with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, Some("")),
                   Ok(CoreProgram::Venv(fx.venv_path())));
        assert_eq!(resolve_core_program(&fx.root, Some("   ")),
                   Ok(CoreProgram::Venv(fx.venv_path())));
    }

    #[test]
    fn resolve_core_program_treats_git_file_as_dev_checkout() {
        // LOW: .git がファイル（worktree/submodule）でも開発チェックアウト
        // 判定は変わらないことを固定する
        let fx = CoreProgramFixture::new("git_file")
            .with_git_file().with_venv().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Venv(fx.venv_path())));
    }

    #[test]
    fn resolve_core_program_dev_checkout_without_venv_falls_back_to_bundled() {
        // テスト担当の提案: 開発チェックアウトだが venv 未構築の場合のフォールバック
        let fx = CoreProgramFixture::new("dev_checkout_no_venv")
            .with_git().with_bundled();
        assert_eq!(resolve_core_program(&fx.root, None),
                   Ok(CoreProgram::Bundled(fx.bundled_path())));
    }
}
