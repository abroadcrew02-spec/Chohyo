// Tauri ブリッジ。Tauri 外（素のブラウザ＝vite dev を直接開いた場合）では
// デモ用モックにフォールバックする。目的は2つ:
//  1. デザイン確認・スクリーンショット検証をブラウザだけで回せるようにする
//  2. UI コードが環境判定で散らからないよう、分岐をこの1ファイルへ閉じ込める
import { invoke as tauriInvoke } from "@tauri-apps/api/core";
import { listen as tauriListen, UnlistenFn } from "@tauri-apps/api/event";

export const isTauri = "__TAURI_INTERNALS__" in window;

type Handler = (e: { payload: string }) => void;
const mockListeners = new Map<string, Set<Handler>>();

function emit(event: string, payload: string) {
  mockListeners.get(event)?.forEach((h) => h({ payload }));
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function mockRun(): Promise<number> {
  const total = 8;
  emit("core-line", JSON.stringify({ event: "start", total, todo: total }));
  for (let i = 1; i <= total; i++) {
    await sleep(280);
    emit("core-line", JSON.stringify({
      event: "page", page_id: `demo_p${String(i).padStart(4, "0")}`,
      status: i === 3 ? "位置合わせ失敗" : "done" }));
  }
  emit("core-line", JSON.stringify({
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
    xlsx: "output\\output_demo.xlsx", csv: "output\\output_demo.csv" }));
  return 0;
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

function countDisabledCells(parsed: any): number {
  let n = 0;
  for (const face of parsed?.faces ?? []) {
    for (const f of face.fields ?? []) if (f.output === false) n++;
    for (const t of face.tables ?? []) for (const c of t.columns ?? []) if (c.output === false) n++;
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

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  switch (cmd) {
    case "read_config": return {};
    case "write_config": return null;
    case "pick_folder": return "C:\\デモ\\帳票スキャン";
    case "pick_image": return null;
    case "pick_json": return "C:\\デモ\\template.json";
    case "read_text": return JSON.stringify(DEMO_TEMPLATE);
    case "read_default_template": return JSON.stringify(DEMO_TEMPLATE);
    case "open_folder": return null;
    case "run_core": {
      const a = (args?.args ?? []) as string[];
      if (a[0] === "run") return mockRun();
      return 0;
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
      return "{}";
    }
    case "kill_core": return null;
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
