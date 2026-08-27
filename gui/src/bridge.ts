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
    api_calls: total - 1, unclear_cells: 247, overflow: 0,
    xlsx: "output\\output_demo.xlsx", csv: "output\\output_demo.csv" }));
  return 0;
}

const VERIFY_OK = [
  { event: "verify", check: "template", ok: true, columns: 218 },
  { event: "verify", check: "poppler", ok: true },
  { event: "verify", check: "credentials", ok: true, state: "dpapi" },
].map((e) => JSON.stringify(e)).join("\n");

async function mockInvoke(cmd: string, args?: Record<string, unknown>): Promise<unknown> {
  switch (cmd) {
    case "read_config": return {};
    case "write_config": return null;
    case "pick_folder": return "C:\\デモ\\帳票スキャン";
    case "pick_image": return null;
    case "pick_json": return null;
    case "open_folder": return null;
    case "run_core": {
      const a = (args?.args ?? []) as string[];
      if (a[0] === "run") return mockRun();
      return 0;
    }
    case "run_core_capture": {
      const a = (args?.args ?? []) as string[];
      if (a[0] === "verify") return VERIFY_OK;
      return "{}";
    }
    case "kill_core": return null;
    case "write_text": return null;
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
