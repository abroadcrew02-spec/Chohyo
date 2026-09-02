// 利用者テンプレート（`templates_user/`）まわりの純関数群（issue #72 (t)）。
//
// 設計: docs/design/chouhyo-ocr/08_frame_detection_design.md §3。
// 方針（§3.2.1）:
//   - 既存コマンド（write_text / write_template_staged / promote_template /
//     discard_staged）のスコープは1つも広げない。
//   - webview へ絶対パスを返さない。ここに置く関数の戻り値も表示名のみを
//     やり取りする（呼び出し側の lib.rs が絶対パスを webview へ渡さない
//     ことに責任を持つ）。
//   - 列挙とパス検査は Rust に一本化する（Python 側に同じ規則を書かない）。
//
// テスト方針: `AppHandle` に依存する glue（`user_templates_dir` 自体・
// `resolve_last_template`）は、この crate の既存パターン（`repo_root`・
// `allowed_roots` も同様に直接テストしない）に倣い単体テストしない。
// 代わりに、AppHandle を必要としない純関数（`validate_user_template_name`・
// `ensure_safe_templates_dir`・`list_dir`・`resolve_existing_entry` 等）へ
// ロジックを切り出し、そちらを表駆動でテストする。

use icu_normalizer::ComposingNormalizerBorrowed;
use std::path::{Path, PathBuf};
use tauri::{AppHandle, Manager};

/// 名前を NFC（正規化合成形式）へ揃える。畳むのは合成／分解の表現差
/// （例: 「が」= U+304C 単体 と か(U+304B)+濁点(U+3099) の分解形）のみ——
/// 全角/半角の統一や homoglyph（見た目が似た別文字）の同一視はしない
/// （07 §7.4 の要求も NFC 正規化までで、それ以上は求めていない）。
/// Windows 予約デバイス名の判定は ASCII 大文字化後の比較で足りるため、
/// NFC 化の対象外でも実害はない。
fn nfc(s: &str) -> String {
    ComposingNormalizerBorrowed::new_nfc().normalize(s).into_owned()
}

/// 個々のテンプレートファイルのサイズ上限（07 §7.3・暫定 ※Q-F13）。
pub const MAX_TEMPLATE_BYTES: u64 = 5 * 1024 * 1024;

/// 列挙・照合候補の件数上限（07 NFR-F09・§7.3・暫定 ※Q-F13）。
pub const MAX_LISTED: usize = 20;

/// staged 保存の接尾辞。lib.rs の `STAGED_SUFFIX` と同じ規則
/// （`<name>.json.saving.json`）だが、ここでは列挙時の除外判定にだけ使う。
const SAVING_SUFFIX: &str = ".saving.json";
const BACKUP_SUFFIX: &str = ".bak";

/// 名前検証の結果。衝突は拒否ではなく「上書き確認の対象」として区別する
/// （07 FR-F26・08 §3.2.4）。
#[derive(Debug, PartialEq, Eq)]
pub enum NameVerdict {
    New(String),
    Overwrites(String),
}

/// Windows の予約デバイス名（NFC 正規化後・大小文字無視で比較する・07 §7.4）。
///
/// L-1 追補（レビュー AZKi）: `CLOCK$` を追加。`COM`/`LPT` は 0〜9 の
/// ASCII 数字に加え、Windows が予約する上付き数字（U+00B9 ¹・U+00B2 ²・
/// U+00B3 ³）付きの `COM¹`/`LPT¹` 等も対象にする——`COM0`/`LPT0` は
/// デバイスとしては存在しないが、Win32 の予約名判定自体は 0 も含めて
/// ブロックする（過去の `!= '0'` 除外は誤りだった）。
fn is_reserved_device_name(nfc_name: &str) -> bool {
    let upper = nfc_name.to_uppercase();
    const RESERVED: &[&str] = &["CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$", "CLOCK$"];
    if RESERVED.contains(&upper.as_str()) {
        return true;
    }
    for prefix in ["COM", "LPT"] {
        if let Some(rest) = upper.strip_prefix(prefix) {
            let mut chars = rest.chars();
            if let (Some(c), None) = (chars.next(), chars.next()) {
                if c.is_ascii_digit() || matches!(c, '\u{00B9}' | '\u{00B2}' | '\u{00B3}') {
                    return true;
                }
            }
        }
    }
    false
}

/// 名前の文字種・長さ・先頭/末尾・予約名を検証し、NFC 正規化済みの名前を
/// 返す（07 §7.4 の条件1〜4。既存名・出荷物との衝突は呼び出し側・
/// `validate_user_template_name` が扱う）。
///
/// **NFC 正規化を最初に行う**（M-3 追補・レビュー AZKi）: 分解形（NFD、例:
/// 「か」+ 結合濁点 U+3099）は結合文字そのものが英数・かな漢字いずれの
/// 文字種判定にも当たらず、正規化前に文字種検査すると誤って拒否される。
/// 正規化を最初に済ませてから以降の検査（長さ・文字種・先頭/末尾・予約名）
/// をすべて正規化後の文字列に対して行う。
///
/// 許可する文字集合は「英数・Unicode の文字カテゴリ（日本語等）・`-`・`_`・
/// 空白」のみ——`.`・`:`・パス区切り・制御文字はすべてこの時点で拒否される
/// ため、末尾ドットの拒否は主に説明目的の防御線になる。
pub fn validate_name_shape(name: &str) -> Result<String, String> {
    if name.is_empty() {
        return Err("名前を入力してください".into());
    }
    let normalized = nfc(name);
    let char_count = normalized.chars().count();
    if char_count > 64 {
        return Err("名前は64文字以内にしてください".into());
    }
    // L-2 追補（レビュー AZKi）: 先頭の空白も拒否する（従来は末尾のみ）。
    if normalized.starts_with(' ') || normalized.ends_with(' ') || normalized.ends_with('.') {
        return Err("名前の先頭・末尾に空白を、末尾にピリオドを使用できません".into());
    }
    for c in normalized.chars() {
        if c.is_control() {
            return Err("制御文字は使用できません".into());
        }
        let ok = c.is_alphanumeric() || c == '-' || c == '_' || c == ' ';
        if !ok {
            return Err(format!("使用できない文字が含まれています: '{c}'"));
        }
    }
    if normalized.trim().is_empty() {
        return Err("名前を入力してください".into());
    }
    if is_reserved_device_name(&normalized) {
        return Err(format!("予約されたデバイス名は使用できません: {name}"));
    }
    Ok(normalized)
}

/// 名前の文字種検証＋既存名・出荷テンプレート名との衝突検査（07 §7.4・AC-F51）。
///
/// `existing`／`shipped` は表示名（拡張子なし）の一覧。比較は NFC 正規化後に
/// case-insensitive で行う。既存名との衝突は拒否ではなく `Overwrites` として
/// 返す——上書き確認を出すかどうかは呼び出し側（GUI）の責務（08 §3.2.3）。
pub fn validate_user_template_name(
    name: &str,
    existing: &[String],
    shipped: &[String],
) -> Result<NameVerdict, String> {
    let normalized = validate_name_shape(name)?;
    let key = normalized.to_lowercase();
    for s in shipped {
        let s_key = nfc(s).to_lowercase();
        if s_key == key {
            return Err(format!("出荷テンプレートと同じ名前は使用できません: {name}"));
        }
    }
    for e in existing {
        let e_key = nfc(e).to_lowercase();
        if e_key == key {
            return Ok(NameVerdict::Overwrites(normalized));
        }
    }
    Ok(NameVerdict::New(normalized))
}

/// ディレクトリの reparse point（symlink／Windows ジャンクション）検査＋
/// canonicalize＋`is_safe_root`（08 §3.2.5 手順3〜5）。
/// `AppHandle` に依存しないため単体テスト可能。
fn ensure_safe_templates_dir(dir: &Path) -> Result<PathBuf, String> {
    let meta = std::fs::symlink_metadata(dir)
        .map_err(|e| format!("保存先フォルダを確認できません: {e}"))?;
    if meta.file_type().is_symlink() {
        // Windows のジャンクションもここで true になる（lib.rs の
        // allowed_roots／#69 S-N3 と同じ判定）。
        return Err(
            "templates_user がシンボリックリンクまたはジャンクションのため使用できません".into(),
        );
    }
    let canonical = dir.canonicalize().map_err(|e| e.to_string())?;
    if !crate::is_safe_root(&canonical) {
        return Err("保存先フォルダが安全な範囲の外です".into());
    }
    Ok(canonical)
}

/// 利用者テンプレートの保存先ディレクトリ（08 §3.1.3・B案）。
///
/// `app_data_dir()/templates_user` を優先し、解決できない環境（T-1・
/// ポータブル運用等）では `repo_root()/templates_user` へフォールバックする。
/// 存在しなければ作成し、reparse point 検査＋`is_safe_root` を必ず通す
/// （キャッシュしない——実行中に差し替えられうる・08 §3.10 不変条件4）。
pub fn user_templates_dir(app: &AppHandle) -> Result<PathBuf, String> {
    let base = match app.path().app_data_dir() {
        Ok(d) => d,
        Err(_) => crate::repo_root(app)?,
    };
    let dir = base.join("templates_user");
    std::fs::create_dir_all(&dir).map_err(|e| format!("保存先フォルダを作成できません: {e}"))?;
    ensure_safe_templates_dir(&dir)
}

/// 表示名（検証済み・NFC 正規化後）から user dir 内の実ファイルへ解決する。
/// 読み取り系コマンド（`read_user_template`／`match_templates` の候補解決）で
/// 共通に使う。`dir` は `user_templates_dir()` の戻り値（canonicalize 済み）
/// を渡すこと。
///
/// 満たさなければならない条件（08 §3.2.5 個々のエントリ）:
/// 名前の文字種が妥当・実在する通常ファイル（symlink 拒否）・拡張子 json・
/// サイズ上限以内・canonicalize 後の親が `dir` と完全一致。
pub fn resolve_existing_entry(dir: &Path, name: &str) -> Result<PathBuf, String> {
    let normalized = validate_name_shape(name)?;
    let candidate = dir.join(format!("{normalized}.json"));
    let meta = std::fs::symlink_metadata(&candidate)
        .map_err(|_| "指定されたテンプレートが見つかりません".to_string())?;
    if !meta.file_type().is_file() {
        // ディレクトリ・symlink（junction 含む）はここで弾かれる
        return Err("指定されたテンプレートが見つかりません".into());
    }
    if meta.len() > MAX_TEMPLATE_BYTES {
        return Err("テンプレートファイルが大きすぎます".into());
    }
    let canonical = candidate.canonicalize().map_err(|e| e.to_string())?;
    if canonical.parent() != Some(dir) {
        return Err("テンプレートの保存範囲外です".into());
    }
    Ok(canonical)
}

/// 一覧に載せるテンプレート1件の情報（webview 契約: 表示名のみ・絶対パスなし）。
#[derive(Debug, Clone, serde::Serialize, PartialEq)]
pub struct UserTemplateInfo {
    pub name: String,
    pub template_id: String,
    pub fields: usize,
    pub tables: usize,
    /// UNIX エポックミリ秒（UTC）。日付整形は呼び出し側（GUI）に委ねる——
    /// カレンダー計算を Rust 側に持ち込まないための意図的な選択。
    pub updated_at: u64,
}

/// 列挙から外れたエントリ（08 §3.3.2 の `excluded[]` と同じ形）。
#[derive(Debug, Clone, serde::Serialize, PartialEq)]
pub struct ExcludedInfo {
    pub name: String,
    pub reason: String,
}

#[derive(Debug, Clone, PartialEq)]
pub struct ListResult {
    pub templates: Vec<UserTemplateInfo>,
    pub excluded: Vec<ExcludedInfo>,
}

/// `dir` 直下（非再帰）を列挙し、テンプレート一覧と除外一覧を返す
/// （08 §3.2.5 個々のエントリの除外規則）。
///
/// 除外理由: `size`（5MB 超）／`parse`（JSON として読めない）／
/// `not_found`（canonicalize 失敗・親不一致——列挙と読み取りの間に消えた等）／
/// `limit`（件数上限超過）。`*.saving.json`・`*.bak`・ディレクトリ・
/// symlink／junction・拡張子違いは実装上の雑音として黙って除外する
/// （除外理由としても報告しない——保存フローの内部ファイルであり、
/// 利用者がテンプレートとして作ったものではないため）。
///
/// `AppHandle` に依存しないため、一時ディレクトリを使って直接テストできる。
pub fn list_dir(dir: &Path) -> ListResult {
    let mut entries: Vec<_> = std::fs::read_dir(dir)
        .map(|rd| rd.flatten().collect::<Vec<_>>())
        .unwrap_or_default();
    // 列挙順を安定させる（件数上限の適用順・テストの再現性のため）。
    entries.sort_by_key(|e| e.file_name());

    let mut templates = Vec::new();
    let mut excluded = Vec::new();

    for entry in entries {
        let path = entry.path();
        let file_name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let name = path
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();

        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if !meta.file_type().is_file() {
            // ディレクトリ・symlink／junction（08 §3.2.5・AC-F59）。
            continue;
        }
        let lower = file_name.to_ascii_lowercase();
        if lower.ends_with(SAVING_SUFFIX) || lower.ends_with(BACKUP_SUFFIX) {
            continue;
        }
        let ext_ok = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.eq_ignore_ascii_case("json"))
            .unwrap_or(false);
        if !ext_ok {
            continue;
        }

        // M-7 追補（レビュー AZKi）: 表示名（stem）自体が自前の命名規則
        // （validate_name_shape）を通らない場合（例: `a.b.json` の stem
        // `a.b` はドットを含み許可文字集合の外）は、そのまま一覧に混ぜず
        // 除外理由付きで報告する——名前ベースの後続操作（read_user_template・
        // 衝突判定・match-templates の候補指定）が前提とする文字種を
        // 満たさないファイルを「テンプレート」として扱わない。
        if validate_name_shape(&name).is_err() {
            excluded.push(ExcludedInfo { name, reason: "invalid_name".into() });
            continue;
        }

        if meta.len() > MAX_TEMPLATE_BYTES {
            excluded.push(ExcludedInfo { name, reason: "size".into() });
            continue;
        }

        let canonical = match path.canonicalize() {
            Ok(c) => c,
            Err(_) => {
                excluded.push(ExcludedInfo { name, reason: "not_found".into() });
                continue;
            }
        };
        if canonical.parent() != Some(dir) {
            excluded.push(ExcludedInfo { name, reason: "not_found".into() });
            continue;
        }

        if templates.len() >= MAX_LISTED {
            excluded.push(ExcludedInfo { name, reason: "limit".into() });
            continue;
        }

        let content = match std::fs::read_to_string(&path) {
            Ok(c) => c,
            Err(_) => {
                excluded.push(ExcludedInfo { name, reason: "parse".into() });
                continue;
            }
        };
        let value: serde_json::Value = match serde_json::from_str(&content) {
            Ok(v) => v,
            Err(_) => {
                excluded.push(ExcludedInfo { name, reason: "parse".into() });
                continue;
            }
        };
        let template_id = value
            .get("template_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let (fields, tables) = count_fields_tables(&value);
        let updated_at = meta.modified().ok().map(epoch_millis).unwrap_or(0);
        templates.push(UserTemplateInfo { name, template_id, fields, tables, updated_at });
    }

    ListResult { templates, excluded }
}

/// `faces[].fields[]`／`faces[].tables[]` の件数を面をまたいで合計する
/// （テンプレート JSON スキーマ実測: testdata/formB/formB-v1.json）。
/// 欄数・表数の表示用であり、コアの `verify` によるスキーマ検証を代替しない
/// ——形が崩れていれば 0/0 を返すだけで、保存や照合そのものを妨げない。
fn count_fields_tables(value: &serde_json::Value) -> (usize, usize) {
    let Some(faces) = value.get("faces").and_then(|v| v.as_array()) else {
        return (0, 0);
    };
    let mut fields = 0usize;
    let mut tables = 0usize;
    for face in faces {
        if let Some(f) = face.get("fields").and_then(|v| v.as_array()) {
            fields += f.len();
        }
        if let Some(t) = face.get("tables").and_then(|v| v.as_array()) {
            tables += t.len();
        }
    }
    (fields, tables)
}

fn epoch_millis(t: std::time::SystemTime) -> u64 {
    t.duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// `templates/*.json` の表示名（拡張子を除いたファイル名）一覧
/// （出荷テンプレートとの衝突検査・07 §7.4「出荷物との衝突」用）。
pub fn list_shipped_stems(root: &Path) -> Vec<String> {
    let dir = root.join("templates");
    let mut v = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&dir) {
        for e in entries.flatten() {
            let p = e.path();
            let ext_ok = p
                .extension()
                .and_then(|e| e.to_str())
                .map(|e| e.eq_ignore_ascii_case("json"))
                .unwrap_or(false);
            if ext_ok {
                if let Some(stem) = p.file_stem().and_then(|s| s.to_str()) {
                    v.push(stem.to_string());
                }
            }
        }
    }
    v
}

/// `dir` 直下の `*.json`（`.saving.json`／`.bak`／通常のディレクトリを
/// 除く）の表示名（stem）を**件数上限・内容解析なしで全件**返す
/// （M-1 追補・レビュー AZKi・`save_user_template` の衝突判定専用）。
///
/// `list_dir` は表示用に件数上限（`MAX_LISTED`）・サイズ上限・JSON 解析・
/// 名前検証を掛けるため、21件目以降や壊れた/大きすぎる/名前が自前の規則を
/// 満たさない同名ファイルとの衝突を見逃す——保存時の「同名なら上書き確認」
/// はこれらも含めて必ず検出しなければならない（黙って上書きしない）。
/// こちらは列挙のみで実ファイルを読まない（軽量・件数上限なし）。
///
/// **symlink／junction も衝突集合に含める**（L-6 追補・レビュー AZKi）。
/// `list_dir`（表示用）は reparse point を除外するが、こちらを同じ基準に
/// すると「同名の symlink が既にある」状態を `New` と誤判定し、確認なしで
/// そのパスへ書き込む（`promote_staged` の rename が reparse point を
/// 巻き込む）経路が残る。判定は `is_file() || is_symlink()` とし、
/// 通常のディレクトリ（`is_file()==false` かつ `is_symlink()==false`）だけを
/// 除外する。
pub fn list_all_stems(dir: &Path) -> Vec<String> {
    let mut out = Vec::new();
    let Ok(entries) = std::fs::read_dir(dir) else {
        return out;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let file_name = path
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .to_string();
        let Ok(meta) = std::fs::symlink_metadata(&path) else {
            continue;
        };
        if !(meta.file_type().is_file() || meta.file_type().is_symlink()) {
            continue;
        }
        let lower = file_name.to_ascii_lowercase();
        if lower.ends_with(SAVING_SUFFIX) || lower.ends_with(BACKUP_SUFFIX) {
            continue;
        }
        let ext_ok = path
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| e.eq_ignore_ascii_case("json"))
            .unwrap_or(false);
        if !ext_ok {
            continue;
        }
        if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
            out.push(stem.to_string());
        }
    }
    out
}

/// `names` を `user_templates_dir()` 配下の実ファイルへ解決する
/// （match-templates の `--candidate` 候補選定・純関数）。
///
/// 解決できなかった名前は `excluded` へ理由付きで積んで続行する
/// （1件の不正で照合ループ全体を止めない・FR-F28）。`reason` は
/// `"invalid_name"`（文字種検証落ち）／`"not_found"`（形は妥当だが実在しない・
/// エントリ安全性検査落ち）／`"limit"`（件数上限超過）のいずれか。
pub fn classify_candidates(dir: &Path, names: &[String]) -> (Vec<PathBuf>, Vec<ExcludedInfo>) {
    let mut candidates = Vec::new();
    let mut excluded = Vec::new();
    for name in names.iter().take(MAX_LISTED) {
        match resolve_existing_entry(dir, name) {
            Ok(path) => candidates.push(path),
            Err(_) => {
                let reason = if validate_name_shape(name).is_err() {
                    "invalid_name"
                } else {
                    "not_found"
                };
                excluded.push(ExcludedInfo { name: name.clone(), reason: reason.into() });
            }
        }
    }
    for extra in names.iter().skip(MAX_LISTED) {
        excluded.push(ExcludedInfo { name: extra.clone(), reason: "limit".into() });
    }
    (candidates, excluded)
}

/// `match-templates --input <input> --shipped <shipped> --candidate <c1> ...`
/// の引数配列を組み立てる（純関数）。
pub fn build_match_args(input: &Path, shipped: &Path, candidates: &[PathBuf]) -> Vec<String> {
    let mut args = vec![
        "match-templates".to_string(),
        "--input".to_string(),
        input.to_string_lossy().to_string(),
        "--shipped".to_string(),
        shipped.to_string_lossy().to_string(),
    ];
    for c in candidates {
        args.push("--candidate".to_string());
        args.push(c.to_string_lossy().to_string());
    }
    args
}

/// コアが返した `match_templates` イベントの JSON の `excluded[]` へ、
/// Rust 側で除外したエントリを追加する（純関数）。コア側が `excluded` を
/// 持たない場合は新設する。
pub fn merge_excluded_into(mut value: serde_json::Value, extra: Vec<ExcludedInfo>) -> serde_json::Value {
    let extra_values: Vec<serde_json::Value> = extra
        .into_iter()
        .map(|e| serde_json::json!({"name": e.name, "reason": e.reason}))
        .collect();
    match value.get_mut("excluded").and_then(|v| v.as_array_mut()) {
        Some(arr) => arr.extend(extra_values),
        None => {
            if let Some(obj) = value.as_object_mut() {
                obj.insert("excluded".to_string(), serde_json::Value::Array(extra_values));
            }
        }
    }
    value
}

/// verify の stdout（JSON Lines）から「テンプレート検証」行の `ok` を読む
/// （H-1 追補・レビュー AZKi・純関数）。
///
/// **終了コードに依存しない**——verify は資格情報未設定・API 残量ゼロ等の
/// 理由で個別の JSON Lines を出しつつプロセス自体は非 0 で終わることがある
/// ため、「保存に失敗した」の判定を終了コードで行うと無関係な理由で保存が
/// 巻き添えになる。判定は常に stdout の内容（`event=="verify"` かつ
/// `check=="template"` の行の `ok`）のみで行う。
///
/// 行ごとに独立した JSON として解釈する（1個の JSON として丸ごとパースする
/// と、複数行の JSON Lines は構文エラーになり常に「検証失敗」に見えてしまう
/// ——これが H-1 の実体）。**該当する行が1つも無い場合は false を返す**
/// （安全側に倒す。見つからないのに ok 扱いにすると、コアの出力形式が
/// 変わったときに検証 NG のまま黙って保存してしまう）。
pub fn verify_template_ok(stdout: &str) -> bool {
    stdout
        .lines()
        .filter_map(|l| serde_json::from_str::<serde_json::Value>(l).ok())
        .find(|v| {
            v.get("event").and_then(|e| e.as_str()) == Some("verify")
                && v.get("check").and_then(|c| c.as_str()) == Some("template")
        })
        .and_then(|v| v.get("ok").and_then(|b| b.as_bool()))
        .unwrap_or(false)
}

/// verify の stdout（JSON Lines）を許可キーだけに絞り込んで再構築する
/// （M-2r 追補・レビュー AZKi の実例を受けた再修正・純関数）。
///
/// **`mask_known_paths`（既知の絶対パス文字列を単純置換）は撤回した。**
/// コア側の `OSError` は `repr()` を経由して JSON 文字列へ入るため、
/// バックスラッシュが二重にエスケープされる（実測: パス区切り1文字の `\`
/// が JSON テキスト上では `\\\\` の4連になる）。この結果、Rust 側で構築した
/// 生の絶対パス文字列（1重エスケープ）を単純に検索置換しても一致せず、
/// 機微情報（利用者名・テンプレート名を含みうる `SECRET.json` 等）が
/// webview まで素通りする経路が残っていた。
///
/// **対応方針を「既知の値を消す」から「許可した値だけを通す」へ転換する。**
/// 行ごとに独立した JSON として解釈し、許可キーのみを保持した新しい
/// オブジェクトへ再構築して返す——許可されていないキー（`error` を含む
/// 任意のフィールド）は一切素通りしない（fail-closed。将来コアが新しい
/// エラー詳細フィールドを追加しても、ここで拾わなければ自動的に落ちる）。
/// `error` キーだけは固定文言に置き換えて残す（「何かに失敗した」ことは
/// 利用者へ伝える。詳細な原因は返さない——本アプリの Rust 側にログ基盤が
/// 無いため、"詳細はログ" は将来 Rust 側ログを整備したときの拡張点として
/// 文言に残してある）。JSON として解釈できない行はそのまま落とす（構文
/// エラーの断片に機微情報が部分的に残っている可能性を排除する）。
pub fn sanitize_verify_output(stdout: &str) -> String {
    const ALLOWED_KEYS: &[&str] = &[
        "event", "check", "ok", "columns", "cells", "amount_cells",
        "exclusions", "exclusions_by_face", "warnings", "column_names",
        "output_disabled_cells", "state", "used", "cap", "free_tier",
        "env_present",
    ];
    const ERROR_PLACEHOLDER: &str = "テンプレートの検証に失敗しました（詳細はログ）";

    let mut out_lines = Vec::new();
    for line in stdout.lines() {
        let Ok(value) = serde_json::from_str::<serde_json::Value>(line) else {
            continue;
        };
        let Some(obj) = value.as_object() else {
            continue;
        };
        let mut filtered = serde_json::Map::new();
        for key in ALLOWED_KEYS {
            if let Some(v) = obj.get(*key) {
                filtered.insert((*key).to_string(), v.clone());
            }
        }
        if obj.contains_key("error") {
            filtered.insert(
                "error".to_string(),
                serde_json::Value::String(ERROR_PLACEHOLDER.to_string()),
            );
        }
        out_lines.push(serde_json::Value::Object(filtered).to_string());
    }
    out_lines.join("\n")
}

/// `config.json` の `last_template` の値がどの区分を指しているかを判定する
/// （純関数・08 §3.5.1・§3.5.2）。
///
/// **表記は `"shipped"`（名前なし）または `"user:<名前>"` の2形式のみ**
/// （2026-09-02・coder_backend／coder_frontend 実装済みの表記に合わせて確定。
/// 旧案の `"shipped:<name>"` 形式・空文字既定は採らない）。ファイルの実在
/// 確認・フォールバックは呼び出し側（`resolve_last_template_path`）が行う。
#[derive(Debug, PartialEq, Eq)]
pub enum LastTemplateTarget {
    /// `"shipped"`、または未知の形式（`""`・旧 `"shipped:<name>"` 形式・
    /// 絶対パス等）——すべて出荷既定へ倒す。
    Shipped,
    User(String),
}

pub fn parse_last_template(value: &str) -> LastTemplateTarget {
    if let Some(name) = value.strip_prefix("user:") {
        return LastTemplateTarget::User(name.to_string());
    }
    // "shipped" 以外はすべて Shipped（未知形式・絶対パス・空文字を含む）。
    // "shipped" 自体もここに落ちるため、値の正否をここで区別する必要はない
    // ——確定の表記が1つしかない以上、"それ以外はすべて出荷既定" が
    // AC-F60（範囲外を手書きしても例外を投げずフォールバック）をそのまま満たす。
    LastTemplateTarget::Shipped
}

/// `last_template` の解決（08 §3.5.2）。`AppHandle` を取らない純関数——
/// `user_dir` は呼び出し側が `user_templates_dir()` で解決済みの値
/// （解決できなければ `None`）を渡す。範囲外・存在しない・形式不明は
/// すべて出荷既定へフォールバックする（`_validate` は例外を投げない・AC-F60）。
pub fn resolve_last_template_path(last: &str, root: &Path, user_dir: Option<&Path>) -> PathBuf {
    let shipped_default = root.join("templates").join("chouhyo-v1.json");
    match parse_last_template(last) {
        LastTemplateTarget::Shipped => shipped_default,
        LastTemplateTarget::User(name) => user_dir
            .and_then(|dir| resolve_existing_entry(dir, &name).ok())
            .unwrap_or(shipped_default),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mkdir(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("chouhyo_ut_{name}_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    // --- AC-F51: 名前検証の表駆動15件（08 §3.2.6） ---

    #[test]
    fn rejects_reserved_device_names_mixed_case() {
        // #1
        for n in ["CON", "con", "Nul", "COM1", "lpt9", "CoNiN$"] {
            assert!(
                validate_user_template_name(n, &[], &[]).is_err(),
                "{n} は予約デバイス名として拒否されるべき"
            );
        }
    }

    #[test]
    fn rejects_reserved_device_names_l1_additions() {
        // L-1 追補（レビュー AZKi）: COM0/LPT0・CLOCK$・上付き数字付き COM/LPT。
        for n in ["COM0", "LPT0", "CLOCK$", "clock$",
                  "COM\u{00B9}", "COM\u{00B2}", "COM\u{00B3}",
                  "LPT\u{00B9}", "LPT\u{00B2}", "LPT\u{00B3}"] {
            assert!(
                validate_user_template_name(n, &[], &[]).is_err(),
                "{n} は予約デバイス名として拒否されるべき"
            );
        }
        // COM10・LPT10（2桁）は予約名の対象外（Windows は単一桁のみ予約）
        assert!(validate_user_template_name("COM10", &[], &[]).is_ok());
    }

    #[test]
    fn rejects_trailing_dot_or_space() {
        // #2
        assert!(validate_user_template_name("abc.", &[], &[]).is_err());
        assert!(validate_user_template_name("abc ", &[], &[]).is_err());
    }

    #[test]
    fn rejects_leading_space() {
        // L-2 追補（レビュー AZKi）: 従来は末尾の空白のみ拒否していたが、
        // 先頭の空白も同様に拒否する。
        assert!(validate_user_template_name(" abc", &[], &[]).is_err());
    }

    #[test]
    fn detects_case_insensitive_collision_as_overwrites() {
        // #3
        let existing = vec!["Sample".to_string()];
        assert_eq!(
            validate_user_template_name("sample", &existing, &[]).unwrap(),
            NameVerdict::Overwrites("sample".to_string())
        );
        assert_eq!(
            validate_user_template_name("SAMPLE", &existing, &[]).unwrap(),
            NameVerdict::Overwrites("SAMPLE".to_string())
        );
    }

    #[test]
    fn rejects_colon_alternate_data_stream() {
        // #4
        assert!(validate_user_template_name("a:b", &[], &[]).is_err());
    }

    #[test]
    fn rejects_control_characters() {
        // #5
        assert!(validate_user_template_name("a\u{0007}b", &[], &[]).is_err());
        assert!(validate_user_template_name("a\nb", &[], &[]).is_err());
    }

    #[test]
    fn enforces_length_boundary_64_65() {
        // #6
        let ok64 = "a".repeat(64);
        let bad65 = "a".repeat(65);
        assert!(validate_user_template_name(&ok64, &[], &[]).is_ok());
        assert!(validate_user_template_name(&bad65, &[], &[]).is_err());
    }

    #[test]
    fn rejects_empty_or_whitespace_only() {
        // #7
        assert!(validate_user_template_name("", &[], &[]).is_err());
        assert!(validate_user_template_name("   ", &[], &[]).is_err());
    }

    #[test]
    fn rejects_collision_with_shipped_template_name() {
        // #8
        let shipped = vec!["chouhyo-v1".to_string()];
        assert!(validate_user_template_name("chouhyo-v1", &[], &shipped).is_err());
        assert!(validate_user_template_name("Chouhyo-V1", &[], &shipped).is_err());
    }

    #[test]
    fn rejects_path_separators_and_parent_traversal() {
        // #9
        for n in ["../x", "a/b", "a\\b", ".."] {
            assert!(validate_user_template_name(n, &[], &[]).is_err(), "{n}");
        }
    }

    #[test]
    fn allows_fullwidth_middle_space_hyphen_underscore() {
        // #10
        let v = validate_user_template_name("帳票 A_2026-09", &[], &[]);
        assert!(v.is_ok(), "{v:?}");
    }

    #[test]
    fn nfd_and_nfc_forms_collide_as_same_name() {
        // #11: 「が」= NFC 1文字 (U+304C) 相当を、NFD（か + 濁点 U+3099）で
        // 既存名として登録し、NFC 入力が衝突として検出されることを確認する。
        let nfd_ga = "\u{304B}\u{3099}"; // か + 結合濁点
        let nfc_ga = "\u{304C}"; // が
        assert_ne!(nfd_ga, nfc_ga, "前提: バイト列としては別物");
        let existing = vec![nfd_ga.to_string()];
        let verdict = validate_user_template_name(nfc_ga, &existing, &[]).unwrap();
        assert_eq!(verdict, NameVerdict::Overwrites(nfc_ga.to_string()));
    }

    #[test]
    fn validate_name_shape_normalizes_nfd_input_before_charset_check() {
        // M-3 追補（レビュー AZKi）: 正規化前に文字種検査すると、結合文字
        // （NFD の「か」+ 結合濁点 U+3099）は英数・かな漢字のいずれの
        // 文字種判定にも当たらず誤って拒否されていた。NFC を先に掛けて
        // から検査することで、NFD 入力そのものが通ることを固定する。
        let nfd_ga = "\u{304B}\u{3099}"; // か + 結合濁点（NFD 分解形）
        let result = validate_name_shape(nfd_ga);
        assert!(result.is_ok(), "{result:?}");
        assert_eq!(result.unwrap(), "\u{304C}", "NFC 合成形の「が」へ正規化されるはず");
    }

    #[test]
    fn nfd_input_collides_with_existing_nfc_name() {
        // M-3 追補: 入力側が NFD でも、既存の NFC 名との衝突を正しく検出する
        // （旧実装は入力側 NFD で validate_name_shape が Err を返していた）。
        let nfd_ga = "\u{304B}\u{3099}";
        let nfc_ga = "\u{304C}";
        let existing = vec![nfc_ga.to_string()];
        let verdict = validate_user_template_name(nfd_ga, &existing, &[]).unwrap();
        assert_eq!(verdict, NameVerdict::Overwrites(nfc_ga.to_string()));
    }

    /// Windows のディレクトリジャンクションを作成する（`mklink /J`）。
    /// symlink（`CreateSymbolicLink`）と異なり `SeCreateSymbolicLinkPrivilege`
    /// を要求しないため、管理者権限・Developer Mode なしで作成できる
    /// （2026-09-02 実機確認: 非昇格シェルから `mklink /J` が成功することを
    /// PowerShell で直接確認した上で採用）。
    #[cfg(windows)]
    fn create_junction(link: &Path, target: &Path) -> Result<(), String> {
        let out = std::process::Command::new("cmd")
            .args(["/c", "mklink", "/J"])
            .arg(link)
            .arg(target)
            .output()
            .map_err(|e| e.to_string())?;
        if out.status.success() {
            Ok(())
        } else {
            Err(format!(
                "{}{}",
                String::from_utf8_lossy(&out.stdout),
                String::from_utf8_lossy(&out.stderr)
            ))
        }
    }

    #[cfg(windows)]
    #[test]
    fn ensure_safe_templates_dir_rejects_windows_junction() {
        // #12（AC-F59）: templates_user 自体がジャンクションの場合を拒否する。
        // ジャンクションは非昇格でも作成できるため（上記 create_junction の
        // doc 参照）、権限起因の skip を許さず必ず実証する——symlink 系と違い
        // ここは常に「本当に拒否したこと」を示す。
        let real = mkdir("junction_real_ac59");
        let link = std::env::temp_dir()
            .join(format!("chouhyo_ut_junction_ac59_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&link);

        create_junction(&link, &real).unwrap_or_else(|e| {
            panic!("ディレクトリジャンクションの作成に失敗（mklink /J は非昇格で\
                     成功するはずのため、環境依存の skip にはしない）: {e}")
        });

        // ジャンクションが reparse point として検出される前提を確認する。
        // 実装（ensure_safe_templates_dir）はこの is_symlink() 判定に依存して
        // いるため、この assert が落ちたら FILE_ATTRIBUTE_REPARSE_POINT を
        // 直接見る実装へ切り替える必要がある（現状は実機で true を確認済み）。
        let meta = std::fs::symlink_metadata(&link).unwrap();
        assert!(
            meta.file_type().is_symlink(),
            "ジャンクションが reparse point として検出されていない \
             （is_symlink() が false）"
        );

        assert!(
            ensure_safe_templates_dir(&link).is_err(),
            "AC-F59: templates_user 自体がジャンクションなら拒否されるべき"
        );

        let _ = std::fs::remove_dir(&link); // ジャンクションのリンクだけを外す
        let _ = std::fs::remove_dir_all(&real);
    }

    #[cfg(not(windows))]
    #[test]
    fn ensure_safe_templates_dir_rejects_junction_like_dir() {
        // 非 Windows にはジャンクションという概念が無いため symlink で代替する
        // （このプロジェクトの配布対象は Windows のみだが、cross-compile 時に
        // 壊れないようにしておく）。
        let real = mkdir("junction_real");
        let link = std::env::temp_dir()
            .join(format!("chouhyo_ut_junction_link_{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&link);
        if std::os::unix::fs::symlink(&real, &link).is_err() {
            println!("[SKIP] ensure_safe_templates_dir_rejects_junction_like_dir: symlink 作成に失敗");
            let _ = std::fs::remove_dir_all(&real);
            return;
        }
        assert!(ensure_safe_templates_dir(&link).is_err());
        let _ = std::fs::remove_dir_all(&real);
        let _ = std::fs::remove_dir(&link);
    }

    #[cfg(windows)]
    #[test]
    fn list_dir_excludes_directory_junction_entry() {
        // #13（AC-F59・L-5 追補・レビュー AZKi）: user dir 内のエントリが
        // reparse point の場合、列挙から除外される。旧実装はファイル symlink
        // で検証していたが、Windows のファイル symlink 作成は管理者権限を
        // 要求する（2026-09-02 実機確認: `New-Item -ItemType SymbolicLink` が
        // 非昇格で "Administrator privilege required" を返すことを確認済み）
        // ため権限起因の skip を避けられなかった。
        //
        // `list_dir`／`resolve_existing_entry` が拒否に使っている分岐は
        // `symlink_metadata().file_type().is_file()`（reparse point 全般で
        // false になる）であり、symlink か junction かを区別していない。
        // そこで **非昇格でも作成できるディレクトリジャンクション**を
        // `.json` 拡張子に見せかけた名前（`linked.json`）で作り、同じ
        // `!is_file()` 分岐を skip なしで実証する。
        let dir = mkdir("entry_junction");
        let real = mkdir("entry_junction_target");
        let link = dir.join("linked.json");
        create_junction(&link, &real)
            .expect("ジャンクションの作成に失敗（mklink /J は非昇格で成功するはず）");

        let meta = std::fs::symlink_metadata(&link).unwrap();
        assert!(!meta.file_type().is_file(), "ジャンクションは is_file()==false のはず");

        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert!(result.templates.is_empty(), "{result:?}");
        assert!(result.excluded.is_empty(), "reparse point は理由なしで黙って除外");

        // 読み取り側（resolve_existing_entry）でも拒否されること
        assert!(resolve_existing_entry(&canonical_dir, "linked").is_err());

        let _ = std::fs::remove_dir(&link); // ジャンクションのリンクだけを外す
        let _ = std::fs::remove_dir_all(&real);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(not(windows))]
    #[test]
    fn list_dir_excludes_symlinked_entry() {
        // 非 Windows では symlink で代替する（配布対象は Windows のみだが
        // cross-compile 時に壊れないようにしておく）。
        let dir = mkdir("entry_symlink");
        let real = mkdir("entry_symlink_target");
        let real_file = real.join("real.json");
        std::fs::write(&real_file, "{}").unwrap();
        let link = dir.join("linked.json");
        if std::os::unix::fs::symlink(&real_file, &link).is_err() {
            println!("[SKIP] list_dir_excludes_symlinked_entry: symlink 作成に失敗");
            let _ = std::fs::remove_dir_all(&dir);
            let _ = std::fs::remove_dir_all(&real);
            return;
        }
        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert!(result.templates.is_empty(), "{result:?}");
        assert!(result.excluded.is_empty(), "symlink は理由なしで黙って除外");
        assert!(resolve_existing_entry(&canonical_dir, "linked").is_err());

        let _ = std::fs::remove_dir_all(&dir);
        let _ = std::fs::remove_dir_all(&real);
    }

    #[test]
    fn list_dir_excludes_saving_bak_and_subdirectory() {
        // #14
        let dir = mkdir("exclude_variants");
        std::fs::write(dir.join("x.json.saving.json"), "{}").unwrap();
        std::fs::write(dir.join("x.json.bak"), "{}").unwrap();
        std::fs::create_dir_all(dir.join("subdir.json")).unwrap();
        std::fs::write(dir.join("valid.json"), r#"{"template_id":"t1","faces":[]}"#).unwrap();

        // list_dir は本番では user_templates_dir()（canonicalize 済み）を
        // 受け取る前提——ここでも同じ形で渡す（Windows の canonicalize は
        // \\?\ verbatim 形になるため、生パスのままだと親一致検査が必ず外れる）。
        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert_eq!(result.templates.len(), 1);
        assert_eq!(result.templates[0].name, "valid");
        assert!(result.excluded.is_empty(), "{:?}", result.excluded);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_dir_excludes_oversized_file_with_reason() {
        // #15
        let dir = mkdir("oversize");
        let big = vec![b'a'; (MAX_TEMPLATE_BYTES + 1) as usize];
        std::fs::write(dir.join("big.json"), &big).unwrap();

        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert!(result.templates.is_empty());
        assert_eq!(result.excluded, vec![ExcludedInfo { name: "big".into(), reason: "size".into() }]);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_dir_excludes_name_that_fails_shape_validation() {
        // M-7 追補（レビュー AZKi）: 表示名（stem）自体が validate_name_shape
        // を通らないファイル（例: `a.b.json` の stem `a.b` はドットを含む）は
        // 一覧に混ぜず、理由付きで除外する。
        let dir = mkdir("invalid_stem");
        std::fs::write(dir.join("a.b.json"), "{}").unwrap();
        std::fs::write(dir.join("valid.json"), "{}").unwrap();

        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert_eq!(result.templates.len(), 1);
        assert_eq!(result.templates[0].name, "valid");
        assert_eq!(
            result.excluded,
            vec![ExcludedInfo { name: "a.b".into(), reason: "invalid_name".into() }]
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- 列挙の除外（.saving.json／.bak／ディレクトリ・拡張子違い） ---

    #[test]
    fn list_dir_ignores_non_json_and_reports_parse_failures() {
        let dir = mkdir("misc");
        std::fs::write(dir.join("notes.txt"), "hello").unwrap();
        std::fs::write(dir.join("broken.json"), "{ not json").unwrap();
        std::fs::write(dir.join("ok.json"), r#"{"template_id":"t2","faces":[
            {"fields":[{"field_id":"a"}],"tables":[{"table_id":"tb"}]}
        ]}"#).unwrap();

        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert_eq!(result.templates.len(), 1);
        assert_eq!(result.templates[0].name, "ok");
        assert_eq!(result.templates[0].template_id, "t2");
        assert_eq!(result.templates[0].fields, 1);
        assert_eq!(result.templates[0].tables, 1);
        assert_eq!(result.excluded, vec![ExcludedInfo { name: "broken".into(), reason: "parse".into() }]);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn list_dir_caps_at_max_listed_and_reports_limit() {
        let dir = mkdir("limit");
        // ファイル名をゼロ埋めしてソート順を安定させる
        for i in 0..(MAX_LISTED + 3) {
            std::fs::write(dir.join(format!("t{i:03}.json")), "{}").unwrap();
        }
        let canonical_dir = dir.canonicalize().unwrap();
        let result = list_dir(&canonical_dir);
        assert_eq!(result.templates.len(), MAX_LISTED);
        assert_eq!(result.excluded.len(), 3);
        assert!(result.excluded.iter().all(|e| e.reason == "limit"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- resolve_existing_entry ---

    #[test]
    fn resolve_existing_entry_finds_valid_file() {
        let dir = mkdir("resolve_ok");
        std::fs::write(dir.join("sample.json"), "{}").unwrap();
        let canonical_dir = dir.canonicalize().unwrap();
        let resolved = resolve_existing_entry(&canonical_dir, "sample").unwrap();
        assert_eq!(resolved.file_name().unwrap(), "sample.json");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_existing_entry_rejects_missing_or_invalid_name() {
        let dir = mkdir("resolve_missing");
        let canonical_dir = dir.canonicalize().unwrap();
        assert!(resolve_existing_entry(&canonical_dir, "missing").is_err());
        assert!(resolve_existing_entry(&canonical_dir, "../x").is_err());

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- match_templates: 名前→パス解決／--candidate 反復組み立て／excluded マージ ---

    #[test]
    fn classify_candidates_resolves_valid_names_and_classifies_failures() {
        let dir = mkdir("classify_candidates");
        std::fs::write(dir.join("a.json"), "{}").unwrap();
        std::fs::write(dir.join("b.json"), "{}").unwrap();
        let canonical_dir = dir.canonicalize().unwrap();

        let names = vec![
            "a".to_string(),        // 有効・実在
            "../evil".to_string(),  // 不正な名前（文字種検証落ち）
            "missing".to_string(),  // 形は妥当だが実在しない
            "b".to_string(),        // 有効・実在
        ];
        let (candidates, excluded) = classify_candidates(&canonical_dir, &names);

        assert_eq!(candidates.len(), 2);
        assert!(candidates.iter().any(|p| p.file_name().unwrap() == "a.json"));
        assert!(candidates.iter().any(|p| p.file_name().unwrap() == "b.json"));
        assert_eq!(
            excluded,
            vec![
                ExcludedInfo { name: "../evil".into(), reason: "invalid_name".into() },
                ExcludedInfo { name: "missing".into(), reason: "not_found".into() },
            ]
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn classify_candidates_reports_limit_beyond_max_listed() {
        let dir = mkdir("classify_candidates_limit");
        let canonical_dir = dir.canonicalize().unwrap();
        let names: Vec<String> = (0..(MAX_LISTED + 2)).map(|i| format!("no-such-{i}")).collect();

        let (candidates, excluded) = classify_candidates(&canonical_dir, &names);

        assert!(candidates.is_empty());
        let limit_count = excluded.iter().filter(|e| e.reason == "limit").count();
        let not_found_count = excluded.iter().filter(|e| e.reason == "not_found").count();
        assert_eq!(limit_count, 2, "{excluded:?}");
        assert_eq!(not_found_count, MAX_LISTED, "{excluded:?}");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn build_match_args_assembles_repeated_candidate_flags() {
        let input = PathBuf::from("C:\\wd\\editor_pages\\p0001.png");
        let shipped = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        let candidates = vec![
            PathBuf::from("C:\\appdata\\templates_user\\formB.json"),
            PathBuf::from("C:\\appdata\\templates_user\\formC.json"),
        ];

        let args = build_match_args(&input, &shipped, &candidates);

        assert_eq!(args, vec![
            "match-templates".to_string(),
            "--input".to_string(), "C:\\wd\\editor_pages\\p0001.png".to_string(),
            "--shipped".to_string(), "C:\\app\\templates\\chouhyo-v1.json".to_string(),
            "--candidate".to_string(), "C:\\appdata\\templates_user\\formB.json".to_string(),
            "--candidate".to_string(), "C:\\appdata\\templates_user\\formC.json".to_string(),
        ]);
    }

    #[test]
    fn build_match_args_with_no_candidates_omits_candidate_flags() {
        let input = PathBuf::from("C:\\wd\\editor_pages\\p0001.png");
        let shipped = PathBuf::from("C:\\app\\templates\\chouhyo-v1.json");
        let args = build_match_args(&input, &shipped, &[]);
        assert_eq!(args, vec![
            "match-templates".to_string(),
            "--input".to_string(), "C:\\wd\\editor_pages\\p0001.png".to_string(),
            "--shipped".to_string(), "C:\\app\\templates\\chouhyo-v1.json".to_string(),
        ]);
    }

    #[test]
    fn merge_excluded_into_appends_to_existing_array() {
        let core_json = serde_json::json!({
            "event": "match_templates", "ok": true,
            "excluded": [{"name": "壊れたテンプレ", "reason": "parse"}],
        });
        let extra = vec![ExcludedInfo { name: "../evil".into(), reason: "invalid_name".into() }];

        let merged = merge_excluded_into(core_json, extra);

        let arr = merged["excluded"].as_array().unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0]["reason"], "parse");
        assert_eq!(arr[1], serde_json::json!({"name": "../evil", "reason": "invalid_name"}));
    }

    #[test]
    fn merge_excluded_into_creates_array_when_absent() {
        let core_json = serde_json::json!({"event": "match_templates", "ok": true});
        let extra = vec![ExcludedInfo { name: "missing".into(), reason: "not_found".into() }];

        let merged = merge_excluded_into(core_json, extra);

        assert_eq!(
            merged["excluded"],
            serde_json::json!([{"name": "missing", "reason": "not_found"}])
        );
    }

    // --- last_template の解決（08 §3.5.2） ---

    #[test]
    fn resolve_last_template_path_empty_or_unknown_falls_back_to_shipped() {
        let root = PathBuf::from("C:\\app");
        let shipped_default = root.join("templates").join("chouhyo-v1.json");
        assert_eq!(resolve_last_template_path("", &root, None), shipped_default);
        // "shipped"（名前なし・正規の値）→ 出荷既定
        assert_eq!(resolve_last_template_path("shipped", &root, None), shipped_default);
        // 絶対パス（未知の形式）→ フォールバック
        assert_eq!(
            resolve_last_template_path("C:\\evil\\x.json", &root, None),
            shipped_default
        );
    }

    #[test]
    fn resolve_last_template_path_treats_old_shipped_colon_name_form_as_unknown() {
        // 2026-09-02 coder_backend/coder_frontend 実装の確定表記は
        // "shipped"（名前なし）のみ。旧案の "shipped:<name>" 形式はもはや
        // 正式な値ではなく、未知の形式として出荷既定へ倒れることを固定する
        // ——実在する出荷名を指していても素通りしない（AC-F60 の趣旨）。
        let dir = mkdir("shipped_colon_name_unknown");
        let templates = dir.join("templates");
        std::fs::create_dir_all(&templates).unwrap();
        std::fs::write(templates.join("formB-v1.json"), "{}").unwrap();

        assert_eq!(
            resolve_last_template_path("shipped:formB-v1", &dir, None),
            templates.join("chouhyo-v1.json")
        );

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_last_template_path_user_valid_and_missing() {
        let root = mkdir("user_root");
        let user_dir = mkdir("user_dir");
        std::fs::write(user_dir.join("mine.json"), "{}").unwrap();
        let canonical_user_dir = user_dir.canonicalize().unwrap();

        let resolved = resolve_last_template_path("user:mine", &root, Some(&canonical_user_dir));
        assert_eq!(resolved.file_name().unwrap(), "mine.json");

        // 存在しない user テンプレート → 出荷既定へフォールバック
        let fallback = resolve_last_template_path(
            "user:missing", &root, Some(&canonical_user_dir));
        assert_eq!(fallback, root.join("templates").join("chouhyo-v1.json"));

        // user_dir 自体が解決できない（None）→ フォールバック
        let no_dir = resolve_last_template_path("user:mine", &root, None);
        assert_eq!(no_dir, root.join("templates").join("chouhyo-v1.json"));

        let _ = std::fs::remove_dir_all(&root);
        let _ = std::fs::remove_dir_all(&user_dir);
    }

    #[test]
    fn parse_last_template_recognizes_prefixes() {
        assert_eq!(parse_last_template(""), LastTemplateTarget::Shipped);
        assert_eq!(parse_last_template("shipped"), LastTemplateTarget::Shipped);
        assert_eq!(
            parse_last_template("user:foo"),
            LastTemplateTarget::User("foo".into())
        );
        // 旧案の "shipped:<name>" 形式はもはや正式な値ではない——未知として扱う
        assert_eq!(
            parse_last_template("shipped:bar"),
            LastTemplateTarget::Shipped
        );
        assert_eq!(
            parse_last_template("something-else"),
            LastTemplateTarget::Shipped
        );
    }

    // --- list_shipped_stems / list_all_stems ---

    #[test]
    fn list_shipped_stems_reads_json_stems_only() {
        // list_shipped_stems は root/templates 配下を見る
        let root = mkdir("shipped_stems_root");
        let templates = root.join("templates");
        std::fs::create_dir_all(&templates).unwrap();
        std::fs::write(templates.join("chouhyo-v1.json"), "{}").unwrap();
        std::fs::write(templates.join("readme.txt"), "x").unwrap();

        let stems = list_shipped_stems(&root);
        assert_eq!(stems, vec!["chouhyo-v1".to_string()]);

        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn list_all_stems_has_no_count_cap_and_does_not_parse_content() {
        // M-1 追補（レビュー AZKi）: list_dir ベースの衝突判定は件数上限20・
        // JSON 解析・サイズ上限の影響を受けるため、21件目以降や壊れた/
        // 大きすぎる同名ファイルとの衝突を見逃す。list_all_stems は
        // それらに関わらず全件のファイル名だけを返すことを確認する。
        let dir = mkdir("all_stems");
        for i in 0..(MAX_LISTED + 5) {
            std::fs::write(dir.join(format!("t{i:03}.json")), "{}").unwrap();
        }
        // 壊れた JSON・5MB 超のファイルも stem としては拾われる
        std::fs::write(dir.join("broken.json"), "{ not json").unwrap();
        let big = vec![b'a'; (MAX_TEMPLATE_BYTES + 1) as usize];
        std::fs::write(dir.join("big.json"), &big).unwrap();
        // .saving.json／.bak／非 json は引き続き除外される
        std::fs::write(dir.join("x.json.saving.json"), "{}").unwrap();
        std::fs::write(dir.join("x.json.bak"), "{}").unwrap();
        std::fs::write(dir.join("notes.txt"), "x").unwrap();

        let stems = list_all_stems(&dir);
        assert_eq!(stems.len(), MAX_LISTED + 5 + 2, "{stems:?}"); // t000..t024 + broken + big
        assert!(stems.contains(&"broken".to_string()));
        assert!(stems.contains(&"big".to_string()));
        assert!(!stems.iter().any(|s| s.contains("saving") || s.contains("bak")));

        let _ = std::fs::remove_dir_all(&dir);
    }

    // --- verify_template_ok（H-1・レビュー AZKi） ---

    #[test]
    fn verify_template_ok_reads_the_template_check_line_from_jsonl() {
        let stdout = "\
{\"event\":\"credentials\",\"ok\":true}
{\"event\":\"api_budget\",\"ok\":true,\"remaining\":100}
{\"event\":\"verify\",\"check\":\"schema\",\"ok\":true}
{\"event\":\"verify\",\"check\":\"template\",\"ok\":true,\"warnings\":[]}
{\"event\":\"verify\",\"check\":\"summary\",\"ok\":true}
";
        assert!(verify_template_ok(stdout));
    }

    #[test]
    fn verify_template_ok_false_when_only_template_check_fails() {
        let stdout = "\
{\"event\":\"verify\",\"check\":\"schema\",\"ok\":true}
{\"event\":\"verify\",\"check\":\"template\",\"ok\":false,\"errors\":[\"overlap\"]}
{\"event\":\"verify\",\"check\":\"summary\",\"ok\":true}
";
        assert!(!verify_template_ok(stdout));
    }

    #[test]
    fn verify_template_ok_false_when_template_check_line_missing() {
        let stdout = "{\"event\":\"verify\",\"check\":\"schema\",\"ok\":true}\n\
                       {\"event\":\"credentials\",\"ok\":false}\n";
        assert!(!verify_template_ok(stdout));
    }

    #[test]
    fn verify_template_ok_false_on_empty_stdout() {
        assert!(!verify_template_ok(""));
    }

    // --- sanitize_verify_output（M-2r 追補・レビュー AZKi） ---

    #[test]
    fn sanitize_verify_output_strips_error_details_and_keeps_allowed_fields() {
        // AZKi の実例: OSError の repr が二重エスケープされたバックスラッシュ
        // （実機では \\\\ の4連）を伴って error フィールドへ混入するケース。
        // mask_known_paths（既知パスの単純置換）はこの形に一致しないため、
        // 許可キーのみを通す方式へ転換した。生の JSON テキストを rust の
        // raw string で組み立てる（エスケープ解釈を挟まないため実機の
        // バイト列をそのまま再現できる）。
        let leaked = r#"{"event":"env","ok":false,"error":"[Errno 2] No such file or directory: 'C:\\\\Users\\\\operation\\\\AppData\\\\Roaming\\\\com.holodev.chouhyo-ocr\\\\templates_user\\\\SECRET.json.saving.json'"}"#;
        let template_line = r#"{"event":"verify","check":"template","ok":false,"warnings":["overlap"],"column_names":["氏名","日付"]}"#;
        let stdout = format!("{leaked}\n{template_line}\n");

        let sanitized = sanitize_verify_output(&stdout);

        assert!(!sanitized.contains("SECRET"), "{sanitized}");
        assert!(!sanitized.contains("operation"), "{sanitized}");
        assert!(!sanitized.contains("Errno"), "{sanitized}");
        assert!(!sanitized.contains('\\'), "生パスの痕跡が残っていないこと: {sanitized}");

        let lines: Vec<serde_json::Value> = sanitized
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect();

        // check=="template" 行の ok/warnings/column_names は維持される
        let template_out = lines
            .iter()
            .find(|v| v.get("check").and_then(|c| c.as_str()) == Some("template"))
            .expect("template 行が残っているはず");
        assert_eq!(template_out["ok"], serde_json::json!(false));
        assert_eq!(template_out["warnings"], serde_json::json!(["overlap"]));
        assert_eq!(template_out["column_names"], serde_json::json!(["氏名", "日付"]));

        // error は固定文言へ置換され、原文（Errno 等）は残らない
        let env_out = lines
            .iter()
            .find(|v| v.get("event").and_then(|e| e.as_str()) == Some("env"))
            .expect("env 行が残っているはず");
        assert_eq!(
            env_out["error"],
            serde_json::json!("テンプレートの検証に失敗しました（詳細はログ）")
        );
    }

    #[test]
    fn sanitize_verify_output_drops_keys_not_on_the_allow_list() {
        let stdout = r#"{"event":"verify","check":"template","ok":true,"internal_debug":"C:\\secret\\path","field_id":"氏名"}"#;
        let sanitized = sanitize_verify_output(stdout);
        let value: serde_json::Value = serde_json::from_str(&sanitized).unwrap();
        assert!(value.get("internal_debug").is_none(), "{value}");
        assert!(value.get("field_id").is_none(), "{value}");
        assert_eq!(value["ok"], serde_json::json!(true));
    }

    #[test]
    fn sanitize_verify_output_drops_unparseable_lines() {
        let stdout = "not json at all\n{\"event\":\"verify\",\"check\":\"template\",\"ok\":true}\n";
        let sanitized = sanitize_verify_output(stdout);
        assert_eq!(sanitized.lines().count(), 1, "{sanitized}");
        assert!(!sanitized.contains("not json"), "{sanitized}");
    }

    #[test]
    fn sanitize_verify_output_empty_input_yields_empty_output() {
        assert_eq!(sanitize_verify_output(""), "");
    }
}
