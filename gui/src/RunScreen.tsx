// 実行画面（設計 §7.1・最小構成の6機能）。処理ロジックを持たず、
// コアの起動と JSON Lines 進捗（§7.3）の表示に徹する。
import { invoke } from "@tauri-apps/api/core";
import { listen, UnlistenFn } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";

type Summary = {
  pages: number; rows: number; align_failed: number;
  api_calls: number; unclear_cells: number; overflow: number;
  xlsx?: string; csv?: string;
};

export default function RunScreen() {
  const [inputDir, setInputDir] = useState("");
  const [outputDir, setOutputDir] = useState("output");
  const [running, setRunning] = useState(false);
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    invoke<Record<string, unknown>>("read_config").then((c) => {
      if (typeof c.output_dir === "string") setOutputDir(c.output_dir);
    }).catch(() => {});
  }, []);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  useEffect(() => {
    const subs: Promise<UnlistenFn>[] = [
      listen<string>("core-line", (e) => {
        setLog((l) => [...l.slice(-400), e.payload]);
        try {
          const ev = JSON.parse(e.payload);
          if (ev.event === "start") { setTotal(ev.todo ?? ev.total ?? 0); setDone(0); }
          if (ev.event === "page") setDone((d) => d + 1);
          if (ev.event === "summary") setSummary(ev as Summary);
        } catch { /* JSON 以外の行は無視 */ }
      }),
      listen<string>("core-err", (e) =>
        setLog((l) => [...l.slice(-400), `[err] ${e.payload}`])),
    ];
    return () => { subs.forEach((p) => p.then((un) => un())); };
  }, []);

  const pickInput = async () => {
    const p = await invoke<string | null>("pick_folder");
    if (p) setInputDir(p);
  };
  const pickOutput = async () => {
    const p = await invoke<string | null>("pick_folder");
    if (!p) return;
    setOutputDir(p);
    // 要件 §5.7: 選んだ値を設定へ保存し次回起動時の既定値にする
    await invoke("write_config", { patch: { output_dir: p } });
  };

  const start = async () => {
    setRunning(true); setSummary(null); setError(""); setLog([]); setDone(0); setTotal(0);
    try {
      const code = await invoke<number>("run_core", { args: ["run", "--input", inputDir] });
      if (code !== 0) setError(`コアが終了コード ${code} で終了`);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const openOutput = () => invoke("open_folder", { path: outputDir }).catch((e) => setError(String(e)));

  return (
    <div className="run-screen">
      <section className="controls">
        <div className="row">
          <button onClick={pickInput} disabled={running}>入力フォルダ選択</button>
          <span className="path">{inputDir || "（未選択）"}</span>
        </div>
        <div className="row">
          <button onClick={pickOutput} disabled={running}>出力先選択</button>
          <span className="path">{outputDir}</span>
        </div>
        <div className="row">
          <button className="primary" onClick={start} disabled={running || !inputDir}>
            {running ? "処理中…" : "実行開始"}
          </button>
          <button onClick={openOutput} disabled={running}>出力フォルダを開く</button>
        </div>
      </section>

      {(running || total > 0) && (
        <section className="progress">
          <progress value={done} max={Math.max(total, 1)} />
          <span>{done} / {total} ページ</span>
        </section>
      )}

      {summary && (
        <section className="summary">
          {/* 完了サマリ6項目（要件 §5.9 と同一） */}
          <div><label>処理枚数</label><b>{summary.pages}</b></div>
          <div><label>出力行数</label><b>{summary.rows}</b></div>
          <div><label>位置合わせ失敗</label><b>{summary.align_failed}</b></div>
          <div><label>API送信回数</label><b>{summary.api_calls}</b></div>
          <div><label>要確認セル数総計</label><b>{summary.unclear_cells}</b></div>
          <div><label>行数超過件数</label><b>{summary.overflow}</b></div>
        </section>
      )}

      {error && <div className="error">{error}</div>}
      <pre className="log" ref={logRef}>{log.join("\n")}</pre>
    </div>
  );
}
