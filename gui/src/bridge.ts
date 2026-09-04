// Tauri ブリッジ。Tauri 外（素のブラウザ＝vite dev を直接開いた場合）では
// デモ用モックにフォールバックする。目的は2つ:
//  1. デザイン確認・スクリーンショット検証をブラウザだけで回せるようにする
//  2. UI コードが環境判定で散らからないよう、分岐をこの1ファイルへ閉じ込める
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen, UnlistenFn } from "@tauri-apps/api/event";

export const isTauri = "__TAURI_INTERNALS__" in window;

type Handler = (e: { payload: unknown }) => void;
const mockListeners = new Map<string, Set<Handler>>();

function emit(event: string, payload: unknown) {
  mockListeners.get(event)?.forEach((h) => h({ payload }));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// 実行ごとの ID（issue #96）。実物は Rust が `<pid>-<連番>` で振る
// （lib.rs の next_run_id）。デモに pid は無いので接頭辞を "demo" にする
// ——RunScreen は ID の中身を解釈せず、前回と違うことだけを見る
let demoRunSeq = 0;
const nextDemoRunId = () => `demo-${demoRunSeq++}`;

/** core-line を実物と同じ構造化 payload で送る（issue #96）。 */
function emitLine(runId: string, line: string) {
  emit("core-line", { run_id: runId, line });
}

async function mockRun(): Promise<{ code: number; run_id: string }> {
  const total = 8;
  const runId = nextDemoRunId();
  emit("core-start", { run_id: runId });
  emitLine(runId, JSON.stringify({ event: "start", total, todo: total }));
  for (let i = 1; i <= total; i++) {
    await sleep(280);
    emitLine(runId, JSON.stringify({
      event: "page", page_id: `demo_p${String(i).padStart(4, "0")}`,
      status: i === 3 ? "位置合わせ失敗" : "done" }));
  }
  emitLine(runId, JSON.stringify({
    event: "summary", pages: total, rows: total, align_failed: 1,
    api_calls: total - 1, unclear_cells: 247, overflow: 0, risky_cells: 2,
    // issue #65-3 S2: 実行時のお知らせに乗る新カウンタをデモモードでも確認
    // できるようにする（参照先の採用/破棄・対象外欄由来の内訳・主と参照先の
    // 食い違い（マリンレビュー S-3）の各セグメントを1回のデモ実行で確認できる
    // 組み合わせ。carve_hole は0のままにして「非0のときだけ表示」の分岐も
    // デモから確認できるようにする）
    fallback_used: 2, fallback_discarded: 1, carve_hole: 0,
    fallback_discarded_excluded_field: 1, carve_hole_excluded_field: 0,
    conflict_excluded_field: 1,
    // issue #75 (f): 枠の自動合わせを見送った／対象外だったページ数。
    // 実物（core）は既定 OFF で常に 0 を出すので、デモでは片方だけ非0に
    // して「0 のときは出さない」分岐も一緒に確かめられるようにする
    snap_failsafe_pages: 1, snap_excluded_pages: 0,
    xlsx: "output\\output_demo.xlsx", csv: "output\\output_demo.csv" }));
  return { code: 0, run_id: runId };
}

/** 中間データ削除（issue #52 M-11）の疑似応答。
 *
 *  実測（core/chouhyo_ocr/cli.py の cmd_purge・2026-09-03）に合わせて
 *  `event:"purged"` を1行だけ流す。`--include-output` を付けたときだけ
 *  output_* のキーが増える点も実物と同じにして、二段確認のチェックの
 *  有無で表示が変わることをブラウザだけで確認できるようにする。
 *  削除できなかった件数（failed）は 0 のままにして「非0のときだけ出す」
 *  分岐も残す——デモで常に警告文が出ると、実物の異常時と見分けが付かない。 */
async function mockPurge(includeOutput: boolean): Promise<{ code: number; run_id: string }> {
  const runId = nextDemoRunId();
  emit("core-start", { run_id: runId });
  await sleep(300);
  emitLine(runId, JSON.stringify({
    event: "purged", path: "C:\\デモ\\workdir", cred_kept: true,
    removed: 12, failed: 0,
    ...(includeOutput
      ? { output_dir: "C:\\デモ\\output", output_removed: 3, output_kept: 1,
          output_failed: 0 }
      : {}),
  }));
  return { code: 0, run_id: runId };
}

// 管理6列＋代表形の抽出列（実列名ではない・issue #66 段3 QA申し送り）。
// デモモードは列名の中身までは検証しないため、実際の出荷テンプレ
// （templates/chouhyo-v1.json）を GUI 側で再導出する必要はない
// （それ自体が FR-0.1 の禁則）——列数（220＝現行の出荷テンプレ実測値）と
// 個数の整合だけを保つ代表形にする
const META_COLUMNS_JA =
  ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名", "ページ番号", "ステータス"];
const DEMO_COLUMN_NAMES_BASE = [
  ...META_COLUMNS_JA,
  ...Array.from({ length: 214 }, (_, i) => `demo_col_${String(i + 1).padStart(3, "0")}`),
];

function demoTemplateEvent(columnNames: string[], outputDisabledCells: number) {
  return {
    event: "verify", check: "template", ok: true,
    columns: columnNames.length, cells: Math.max(0, columnNames.length - 6),
    amount_cells: 0, exclusions: 0, exclusions_by_face: {}, warnings: [],
    column_names: columnNames, output_disabled_cells: outputDisabledCells,
  };
}

const VERIFY_OK = [
  demoTemplateEvent(DEMO_COLUMN_NAMES_BASE, 0),
  { event: "verify", check: "poppler", ok: true },
  { event: "verify", check: "credentials", ok: true, state: "dpapi" },
].map((e) => JSON.stringify(e)).join("\n");

// staged 保存（issue #56 T1）をメモリ上で模擬する（path.saving.json → 内容）。
// promote で「保存」成功とみなし、discard で掃除する。実ファイルには触れない
const mockStaged = new Map<string, string>();

// 実コアの verify は output_disabled_cells を **物理升** で返す
// （cli.py: len(t.cells) - len(output_cells(t))）。issue #66 段9 で GUI 側も
// 升単位に揃えたので、疑似応答も同じ単位で数える——列を1つ外したら行数ぶん、
// 升を1つ外したら1件。列 off の列に対する升の指定は buildTemplateJson が
// 書かないので二重には数えない
function countDisabledCells(parsed: any): number {
  let n = 0;
  for (const face of parsed?.faces ?? []) {
    for (const f of face.fields ?? []) if (f.output === false) n++;
    for (const t of face.tables ?? []) {
      const rows = (t.blocks ?? []).reduce((s: number, b: any) => s + (b.rows ?? 0), 0);
      for (const c of t.columns ?? []) if (c.output === false) n += rows;
      n += (t.output_disabled_cells ?? []).length;
    }
  }
  return n;
}

// デモモードで編集画面を触れるようにする最小テンプレート（GUI スモーク用。
// 列数は v1 の 218 に満たないため、コア検証の代役にはならない）
const DEMO_TEMPLATE = {
  schema_version: 1, template_id: "demo", render_dpi: 300,
  image: { width: 2490, height: 3510 }, record: { pages: 1 },
  faces: [
    { face_id: "front", source: { page_offset: 0, rect: { x: 0, y: 0, w: 2490, h: 1880 } },
      fields: [{ field_id: "person_氏名", kind: "text",
                 rect: { x: 400, y: 300, w: 600, h: 90 },
                 fallback_rect: { x: 1500, y: 300, w: 600, h: 90 } }],
      tables: [{ table_id: "family", row_pitch: 100, row_height: 90,
                 blocks: [{ origin: { x: 200, y: 600 }, rows: 3 }],
                 columns: [
                   { name: "続柄", x_offset: 0, width: 200, kind: "text" },
                   { name: "金額", x_offset: 200, width: 240, kind: "text",
                     normalize: "amount" },
                   { name: "元号", x_offset: 440, width: 180, kind: "choice",
                     choice_marks: [{ value: "昭", x_offset: 0, width: 60 },
                                    { value: "平", x_offset: 60, width: 60 },
                                    { value: "令", x_offset: 120, width: 60 }] }] }] },
    { face_id: "back", source: { page_offset: 0, rect: { x: 0, y: 1880, w: 2490, h: 1630 } },
      fields: [], tables: [] },
  ],
};

// issue #71 (a') デモ検証用: pick_image が選ぶ疑似パス。1x1透明PNG
// （read_file_b64 が返す）に DEMO_TEMPLATE を重ねて開く。expand-page の
// 疑似応答で mismatch/match/undecidable の3値すべてを core なしで手動確認
// できるよう、pick_image を呼ぶたび（＝「帳票を開く」を押すたび）に
// mismatch → match → undecidable の順で巡回する（スバル差し戻し4・任意）。
// **1回目は必ず mismatch**——Playwright スモーク
// （core/tests/test_gui_smoke.py の test_editor_format_mismatch_*）は
// 「帳票を開く」を1回しか押さないため、この順序に依存している。
// size-mismatch.png（ころね UX レビュー Must の検証用）は末尾に足した
// 4番目——既存3件の巡回順（インデックス0〜2）をずらすと上記の「1回目は
// 必ず mismatch」に依存する既存テストが壊れるため、新規分は必ず末尾に足す
const DEMO_FORMAT_FACE = (verdict: string, reason: string, score: number, detected: number, expected: number) =>
  ({ verdict, reason, score, detected, expected });
const DEMO_FORMAT_VARIANTS: Record<string, {
  aligned: boolean; reason?: "align" | "size"; verdict: "mismatch" | "match" | "undecidable"; score: number;
  faces: { face_id: string; verdict: string; reason: string; score: number; detected: number; expected: number }[];
}> = {
  "C:\\デモ\\mismatch.png": {
    aligned: false, reason: "align", verdict: "mismatch", score: 0.2,
    faces: [
      { face_id: "front", ...DEMO_FORMAT_FACE("mismatch", "lines", 0.2, 5, 16) },
      { face_id: "back", ...DEMO_FORMAT_FACE("match", "", 1, 26, 26) },
    ],
  },
  "C:\\デモ\\match.png": {
    aligned: true, verdict: "match", score: 0.97,
    faces: [
      { face_id: "front", ...DEMO_FORMAT_FACE("match", "", 1, 16, 16) },
      { face_id: "back", ...DEMO_FORMAT_FACE("match", "", 0.94, 26, 26) },
    ],
  },
  "C:\\デモ\\undecidable.png": {
    aligned: false, reason: "align", verdict: "undecidable", score: 0.4,
    faces: [
      { face_id: "front", ...DEMO_FORMAT_FACE("undecidable", "few_lines", 0.4, 4, 16) },
      { face_id: "back", ...DEMO_FORMAT_FACE("match", "", 1, 26, 26) },
    ],
  },
  // reason:"size"（PageSizeMismatch・Q-H1）の実応答は verdict/score/faces が
  // 個別の面診断を持たない（core/chouhyo_ocr/cli.py:340-347 実測——寸法検査は
  // 面ごとの罫線判定より前に短絡するため）。score:-1・faces:[] は「診断して
  // いない」ことを表す実コアの値そのもので、デモも同じ形にする
  "C:\\デモ\\size-mismatch.png": {
    aligned: false, reason: "size", verdict: "mismatch", score: -1,
    faces: [],
  },
};
const DEMO_FORMAT_PATHS = Object.keys(DEMO_FORMAT_VARIANTS);
let demoImagePickCount = 0;
// 最小の 1x1 透明 PNG（広く使われる定数）。ページ座標系（DEMO_TEMPLATE の
// 欄・表の rect）はこの画像自体の画素とは無関係——draw() は image を
// (0,0) からそのままの実寸で描くだけで、欄・表は image の外側でも構わず
// 描かれる（実コアの位置合わせ済み画像と同じ座標系の扱い）
const DEMO_MISMATCH_IMAGE_B64 =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=";
// ころね（user_advocate）UX レビュー Must の検証用: createTemplateForThisImage
// は開いている画像の実寸（im.naturalWidth/Height＝imgSize）でテンプレートを
// 組み立てる。上の1x1画像のままだと新規テンプレートも1x1になり、
// detect-frames の疑似候補（page 座標で最大 x:950・y:2100 付近）が軒並み
// 「面の範囲外」になってしまう——実際の紙とは無関係な、デモ疑似データ間の
// 寸法不整合でしかない。size-mismatch.png 専用に DEMO_TEMPLATE と同じ
// 2490×3510（splitY も同じ前提で front/back に収まる）の1bit最小PNGを
// 用意し、read_file_b64 側でパス別に返し分ける
const DEMO_SIZE_MISMATCH_IMAGE_B64 =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAACboAAA22AQAAAABcLfViAAAEP0lEQVR42u3BMQEAAADCoPVPbQhfoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA4DXEdgAB1TENJgAAAABJRU5ErkJggg==";

// issue #72 (t) デモ検証用: 利用者テンプレートの一覧（Rust list_user_templates
// の疑似応答・gui/src-tauri/src/user_templates.rs の UserTemplateInfo/
// ExcludedInfo 実測に合わせる・絶対パスは出さない表示名のみ）。
// 「壊れたテンプレ」は excluded 配列側に理由付きで返す（一覧に出る・FR-F28）。
// save_user_template で新規保存されたテンプレートもこの配列に足していく
// （実ファイルには一切触れない・mockStaged と同じ流儀）
// issue #72 (t)・マリン core レビュー分: 「欄N・表M」の定義は単発欄数
// （fields[]・表の列は数えない）と表の個体数（tables[]）に統一する
// （core 側も合わせる予定）。デモの「帳票B」は DEMO_TEMPLATE と同じ
// faces（front: 単発欄1件「person_氏名」＋表1件「family」／back: 空）なので
// fields:1・tables:1 が正しい
type DemoUserTemplate = { name: string; template_id: string; fields: number; tables: number; updated_at: number };
type DemoExcluded = { name: string; reason: string };
const DEMO_USER_TEMPLATES: DemoUserTemplate[] = [
  { name: "帳票B", template_id: "帳票B", fields: 1, tables: 1,
    updated_at: Date.parse("2026-09-02T10:14:33+09:00") },
];
const DEMO_USER_EXCLUDED: DemoExcluded[] = [
  { name: "壊れたテンプレ", reason: "parse" },
];
const demoUserTemplateContent = new Map<string, string>(
  [["帳票B", JSON.stringify({ ...DEMO_TEMPLATE, template_id: "帳票B" })]]);

// issue #72 (t)・スバル差し戻し1: config.json の疑似永続化。実物の Rust/Python
// は config.json（ファイル）に書くため、Editor 再マウント（＝アプリ再起動）を
// またいで last_template が残る。デモモードのブラウザはページ再読み込みで
// JS モジュールの状態が丸ごと初期化されるため、`localStorage` を使って
// 同じ「再起動をまたぐ永続化」を再現する（Playwright の再読み込みテストが
// これに依存する）。localStorage が使えない環境（一部の私用ブラウザ設定等）
// では静かに永続化を諦める——デモモードはこの機能の主目的（core なしでの
// 画面確認）を損なわない範囲の妥協でよい
const DEMO_CONFIG_STORAGE_KEY = "chouhyo-demo-config";
function demoConfigRead(): Record<string, unknown> {
  try {
    const raw = window.localStorage?.getItem(DEMO_CONFIG_STORAGE_KEY);
    return raw ? JSON.parse(raw) : {};
  } catch { return {}; }
}
function demoConfigWrite(patch: Record<string, unknown>) {
  try {
    window.localStorage?.setItem(
      DEMO_CONFIG_STORAGE_KEY, JSON.stringify({ ...demoConfigRead(), ...patch }));
  } catch { /* 永続化できなくても書き込み自体は失敗させない（デモの妥協） */ }
}

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  switch (cmd) {
    case "read_config": return demoConfigRead();
    case "write_config": {
      const { patch } = (args ?? {}) as { patch?: Record<string, unknown> };
      if (patch) demoConfigWrite(patch);
      return null;
    }
    case "pick_folder": return "C:\\デモ\\帳票スキャン";
    case "pick_image": {
      const path = DEMO_FORMAT_PATHS[demoImagePickCount % DEMO_FORMAT_PATHS.length];
      demoImagePickCount++;
      return path;
    }
    case "read_file_b64": {
      // expand-page の疑似応答は page_path に --input のパスをそのまま
      // エコーする（実コアと同じ・上の DEMO_FORMAT_VARIANTS 参照）ため、
      // ここでも同じパス文字列でどちらの疑似画像を返すか判定できる
      const { path } = (args ?? {}) as { path?: string };
      if (path === "C:\\デモ\\size-mismatch.png") return DEMO_SIZE_MISMATCH_IMAGE_B64;
      return DEMO_MISMATCH_IMAGE_B64;
    }
    case "pick_json": return "C:\\デモ\\template.json";
    case "read_text": return JSON.stringify(DEMO_TEMPLATE);
    // read_default_template は config.last_template を解決して返す
    // （実物: gui/src-tauri/src/lib.rs・あくあ実装）。"user:<名前>" が
    // demoUserTemplateContent に無ければ出荷（DEMO_TEMPLATE）へフォールバック
    // する（AC-F60 と同じ「例外を投げず安全側へ倒す」方針をデモにも揃える）。
    // 任意引数 template（2026-09-04）が渡されたときは config を見ない——
    // 実物も同じで、これが「出荷テンプレートを副作用なしで読む」経路になる
    case "read_default_template": {
      const { template } = (args ?? {}) as { template?: string };
      if (template === "shipped") return JSON.stringify(DEMO_TEMPLATE);
      const lt = demoConfigRead().last_template;
      const m = typeof lt === "string" ? /^user:(.+)$/.exec(lt) : null;
      const content = m ? demoUserTemplateContent.get(m[1]) : undefined;
      return content ?? JSON.stringify(DEMO_TEMPLATE);
    }
    case "open_folder": return null;
    // issue #72 (t)・実測（gui/src-tauri/src/lib.rs:list_user_templates）:
    // { templates: [...], excluded: [...] } を返す（表示名のみ・絶対パスなし）
    case "list_user_templates":
      return {
        templates: DEMO_USER_TEMPLATES.map((t) => ({ ...t })),
        excluded: DEMO_USER_EXCLUDED.map((e) => ({ ...e })),
      };
    case "read_user_template": {
      const { name } = (args ?? {}) as { name: string };
      const content = demoUserTemplateContent.get(name);
      if (content) return content;
      throw new Error(`利用者テンプレート「${name}」が見つかりません`);
    }
    // save_user_template（実測: lib.rs:save_user_template）は
    // staged→verify→promote を Rust の中で通し切り、`overwrite=false` で
    // 同名衝突なら Err("AlreadyExists") を返す（上書き確認は GUI 側の
    // 責務・設計08 §3.2.3）。成功時の戻り値は verify の stdout（JSON Lines）
    // そのもの——呼び出し側（Editor.tsx の trySaveUserTemplate）が
    // check:"template" の ok を見て初めて「保存できた」と判断する
    case "save_user_template": {
      const { name, content, overwrite } = (args ?? {}) as
        { name: string; content: string; overwrite: boolean };
      const existed = demoUserTemplateContent.has(name);
      if (existed && !overwrite) throw new Error("AlreadyExists");
      demoUserTemplateContent.set(name, content);
      if (!existed) {
        DEMO_USER_TEMPLATES.push({ name, template_id: name, fields: 1, tables: 0,
          updated_at: Date.now() });
      }
      return VERIFY_OK;
    }
    // match_templates（実測: core/chouhyo_ocr/cli.py:cmd_match_templates が
    // 返す event:"match_templates" の1行を Rust がそのまま文字列で返す。
    // フィールド名は `results`（08 §3.3.2 の例示 `candidates` ではない）。
    // デモモードは入力に関わらず固定の疑似応答を返し、「この画像に合う
    // テンプレート」パネルを core なしで確認できるようにする
    case "match_templates": {
      return JSON.stringify({
        event: "match_templates", ok: true, elapsed_ms: 120, truncated: false,
        results: [
          // fields は単発欄数（表の列は数えない・マリン core レビュー分）。
          // 出荷テンプレは家族・明細の2表に加えて氏名・生年月日等の単発欄が
          // ある想定の代表値（実際の出荷テンプレの正確な件数はここでは検証
          // 対象ではない・デモの代表形）
          { name: "chouhyo-v1", kind: "shipped", template_id: "chouhyo-v1", fields: 12, tables: 2,
            updated_at: "2026-08-31T18:46:02+09:00",
            verdict: "mismatch", reason: "lines", score: 0.11, detected: 30, expected: 16 },
          { name: "帳票B", kind: "user", template_id: "帳票B", fields: 1, tables: 1,
            updated_at: "2026-09-02T10:14:33+09:00",
            verdict: "match", reason: "", score: 0.97, detected: 18, expected: 16 },
        ],
        // core の除外理由は "invalid_json" → "parse" へ統一される予定
        // （マリン core レビュー分）。デモは統一後の値に揃える
        excluded: [{ name: "壊れたテンプレ", reason: "parse" }],
      });
    }
    case "run_core": {
      const a = (args?.args ?? []) as string[];
      if (a[0] === "run") return mockRun();
      if (a[0] === "purge") return mockPurge(a.includes("--include-output"));
      // run 以外は行を1つも流さない。それでも実物と同じ形（RunResult）で
      // 返す——フロントは戻り値から run_id を取り出す（issue #96）
      return { code: 0, run_id: nextDemoRunId() };
    }
    case "run_core_capture": {
      const a = (args?.args ?? []) as string[];
      if (a[0] === "verify") {
        // --template が staged パス（write_template_staged が返したもの）を
        // 指していれば、その内容から出力対象外の件数を数えて反映する。
        // これで保存経路の「無編集保存は差分ゼロ」「列が減れば確認モーダル」
        // をデモモードでも一通り確認できる（issue #66 段3 QA申し送り）
        const tplIdx = a.indexOf("--template");
        const stagedContent = tplIdx >= 0 ? mockStaged.get(a[tplIdx + 1]) : undefined;
        if (stagedContent) {
          try {
            const disabled = countDisabledCells(JSON.parse(stagedContent));
            const columnNames = disabled > 0
              ? DEMO_COLUMN_NAMES_BASE.slice(
                  0, Math.max(META_COLUMNS_JA.length, DEMO_COLUMN_NAMES_BASE.length - disabled))
              : DEMO_COLUMN_NAMES_BASE;
            return [demoTemplateEvent(columnNames, disabled),
              { event: "verify", check: "poppler", ok: true },
              { event: "verify", check: "credentials", ok: true, state: "dpapi" }]
              .map((e) => JSON.stringify(e)).join("\n");
          } catch { /* staged の内容が壊れていれば静的応答へフォールバック */ }
        }
        return VERIFY_OK;
      }
      // issue #71 (a'): Editor.tsx は PDF/画像を問わず expand-page を通す
      // ようになった。デモモードは pick_image が実体の無い疑似画像パスしか
      // 返さないため、その --input を DEMO_FORMAT_VARIANTS で引いて
      // mismatch/match/undecidable のいずれかを返す。core が未対応のキー
      // （verdict/score/faces）を追加するまでの間も、GUI 側の黄帯・上書き
      // ボタン・枠の非表示／可視化を core なしで確認できるようにする
      // （設計08 §2.6 の JSON 契約どおりの疑似応答）
      if (a[0] === "expand-page") {
        const inputIdx = a.indexOf("--input");
        const inputPath = inputIdx >= 0 ? a[inputIdx + 1] : undefined;
        const variant = inputPath ? DEMO_FORMAT_VARIANTS[inputPath] : undefined;
        if (variant) {
          return JSON.stringify({
            event: "expand_page", ok: true, page_path: inputPath, pages: 1,
            aligned: variant.aligned, ...(variant.reason ? { reason: variant.reason } : {}),
            verdict: variant.verdict, score: variant.score, faces: variant.faces,
          });
        }
      }
      // issue #73 (b)・設計08 §4.4: detect-frames の疑似応答。実測
      // （core/chouhyo_ocr/cli.py:cmd_detect_frames・2026-09-03）の形に
      // 合わせてある——候補に `id` は無い（GUI 側で振る）・`blocks[0]` は
      // 平坦な {x,y,rows}（origin にネストしない）・face_id は
      // --template 未指定時 "page"。固定で表1＋欄3（うち1件
      // overlaps_existing）を返す
      if (a[0] === "detect-frames") {
        // 「生成中」の画面を検証するための遅延フック（デモモード限定・
        // レビュー H-1／M-2 の受入確認用）。実コアの detect-frames は実測で
        // 数百ms〜3秒かかるのに対し、疑似応答は同じ tick で解決するため
        // framesGenerating=true の状態が画面に現れない。デモ設定に
        // demo_detect_frames_delay_ms（数値）があるときだけその分待つ
        // ——既定（キー無し）は従来どおり即時に返す
        const delay = demoConfigRead().demo_detect_frames_delay_ms;
        if (typeof delay === "number" && delay > 0) {
          await new Promise((r) => setTimeout(r, delay));
        }
        return JSON.stringify({
          event: "detect_frames", ok: true, elapsed_ms: 850,
          input_size: [2490, 3510],
          stats: { lines_h: 8, lines_v: 6, rects: 10, rails_h: 8, rails_v: 6 },
          candidates: [
            // 表候補の位置は DEMO_TEMPLATE の既存要素（person_氏名 x:400-1000,
            // y:300-390／family x:200-820,y:600-890）と重ならない場所に置く
            // ——候補パネルの「重なりのため対象外」表示は c2（下記・
            // person_氏名 に重なる欄候補）の1件だけを検証対象にするため
            { kind: "table", face_id: "page",
              rect: { x: 100, y: 1000, w: 750, h: 400 },
              blocks: [{ x: 100, y: 1000, rows: 5 }],
              row_pitch: 80, row_height: 70,
              columns: [{ x_offset: 0, width: 200 }, { x_offset: 200, width: 150 },
                        { x_offset: 350, width: 400 }],
              residual_px: 0.4, overlaps_existing: false },
            // c2 は DEMO_TEMPLATE の person_氏名（x:400-1000・y:300-390）と
            // 幾何的に重なる位置に置き、overlaps_existing は **false** で返す。
            // 実コアは --template を渡さない限り overlaps_existing を立てない
            // （編集画面はテンプレートの絶対パスを持たないので、実運用でも
            // ほぼ常に渡さない）ため、旧デモの true は実コアと食い違っていた。
            // GUI 側の candidateOverlapsExisting に判定させれば、テンプレート
            // 適用中＝1件重なり／空テンプレ＝0件重なり の両方が疑似応答の
            // 細工なしで再現できる（AC-G06／AC-G11 の裏取り）
            { kind: "field", face_id: "page",
              rect: { x: 450, y: 320, w: 300, h: 60 },
              residual_px: 0.0, overlaps_existing: false },
            { kind: "field", face_id: "page",
              rect: { x: 600, y: 100, w: 300, h: 80 },
              residual_px: 0.2, overlaps_existing: false },
            { kind: "field", face_id: "page",
              rect: { x: 100, y: 1950, w: 850, h: 150 },
              residual_px: 0.6, overlaps_existing: false },
          ],
          // マリン core レビュー由来: excluded は {reason,count} の配列
          // （複数理由・count>0 のみ意味を持つ）。template_applied は
          // --template 指定時の適用可否（デモは常に寸法一致想定で true）
          // issue #66 段9／#73 (b) 第2弾: 等ピッチの並びは candidates に混ぜず
          // トップレベル suggestions[] で返す（設計 D-2）。cell_indexes は
          // **同じ応答の candidates[] の受け取り順** の添字（D-3）。ここでは
          // 末尾3件（c2/c3/c4 相当の 1,2,3）を覆う 3行×1列 の提案を1件返し、
          // heading_excluded:true で「見出し行は含めていません」の表示経路も
          // 疑似応答だけで通せるようにする
          suggestions: [
            { kind: "table", face_id: "page",
              rect: { x: 100, y: 1950, w: 850, h: 450 },
              blocks: [{ x: 100, y: 1950, rows: 3 }],
              row_pitch: 150, row_height: 150,
              columns: [{ x_offset: 0, width: 850 }],
              residual_px: 0.7, overlaps_existing: false,
              cell_indexes: [1, 2, 3], heading_excluded: true },
          ],
          excluded: [{ reason: "page_outline", count: 1 }, { reason: "too_small", count: 2 },
                     { reason: "straddles_face", count: 0 }],
          template_applied: true,
          zero_reason: null,
        });
      }
      // issue #52 M-10: 認証キーの取り込み。実測（cli.py の
      // cmd_import_credentials・2026-09-03）は credentials_imported に続けて、
      // 元ファイルを消せたら credentials_source_deleted、消せなければ
      // credentials_source_kept を出す。デモは成功側を返す
      if (a[0] === "import-credentials") {
        const lines: Record<string, unknown>[] = [
          { event: "credentials_imported", path: "C:\\デモ\\workdir\\cred.dpapi" },
        ];
        if (a.includes("--delete-source")) lines.push({ event: "credentials_source_deleted" });
        return lines.map((e) => JSON.stringify(e)).join("\n");
      }
      return "{}";
    }
    case "kill_core": return null;
    // ドロップ受付の有効／無効（issue #69 セキュリティ LOW (b)）。デモには
    // Rust 側の白リストが無いので、受け取って捨てるだけ（例外を投げると
    // 実行画面の useEffect が毎回 catch に落ちる）
    case "set_drop_active": return null;
    case "write_text": return null;
    // トランザクショナルな保存（issue #56 T1）のデモ再現。実ファイルには
    // 触れず、メモリ上の mockStaged だけで staged→promote/discard を模擬する
    case "write_template_staged": {
      const { path, content } = (args ?? {}) as { path: string; content: string };
      const staged = `${path}.saving.json`;
      mockStaged.set(staged, content);
      return staged;
    }
    case "promote_template": {
      const { path } = (args ?? {}) as { path: string };
      mockStaged.delete(`${path}.saving.json`);
      return null;
    }
    case "discard_staged": {
      const { path } = (args ?? {}) as { path: string };
      mockStaged.delete(`${path}.saving.json`);
      return null;
    }
    // デモモードに「出荷テンプレート」という実ファイルは存在しないため、
    // 常に false（上書き確認モーダルは他の3条件だけで判定できる）
    case "is_shipped_template_path": return false;
    default: throw new Error(`ブラウザのデモモードでは ${cmd} は使えません`);
  }
}

export function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  return isTauri ? tauriInvoke<T>(cmd, args) : (mockInvoke(cmd, args) as Promise<T>);
}

export function listen<T>(event: string, handler: (e: { payload: T }) => void): Promise<UnlistenFn> {
  if (isTauri) return tauriListen<T>(event, handler);
  const set = mockListeners.get(event) ?? new Set();
  set.add(handler as Handler);
  mockListeners.set(event, set);
  return Promise.resolve(() => { set.delete(handler as Handler); });
}
