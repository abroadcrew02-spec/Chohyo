// 実行画面（設計 §7.1・最小構成の6機能）。処理ロジックを持たず、
// コアの起動と JSON Lines 進捗（§7.3）の表示に徹する。
// UI はデザインカンバス「帳票OCRツール GUI」準拠: 番号つき手順・平易な言葉。
import { invoke } from "./bridge";
import { listen } from "./bridge";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";

type Summary = {
  pages: number; rows: number; align_failed: number;
  api_calls: number; unclear_cells: number; overflow: number;
  xlsx?: string; csv?: string;
};
type Verify = { template: boolean; poppler: boolean; cred: string };
type Failure = { page_id: string; status: string };

// ステータス → 平易な言葉（エラー一覧用）
const STATUS_JA: Record<string, string> = {
  "位置合わせ失敗": "位置合わせに失敗しました（行全体が〓です）",
  "様式不一致": "帳票の様式が一致しませんでした（行全体が〓です）",
  "展開失敗": "ファイルを開けませんでした",
  "送信失敗": "送信に失敗しました（通信環境を確認してください）",
  "未処理（送信上限到達）": "送信上限に達したため未処理です（次回実行時に処理されます）",
  "未処理（中断）": "中断のため未処理です（次回実行時に処理されます）",
  "超過あり": "記入が定義済みの行数を超えています",
};

const FolderIcon = ({ c }: { c: string }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

export default function RunScreen() {
  const [inputDir, setInputDir] = useState("");
  const [outputDir, setOutputDir] = useState("output");
  const [running, setRunning] = useState(false);
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [verify, setVerify] = useState<Verify | null>(null);
  const [importing, setImporting] = useState(false);
  const [failures, setFailures] = useState<Failure[]>([]);
  const interruptedRef = useRef(false);
  const [notice, setNotice] = useState("");
  const logRef = useRef<HTMLPreElement>(null);

  const parseVerify = (text: string): Verify => {
    const v: Verify = { template: false, poppler: false, cred: "missing" };
    for (const line of text.split("\n")) {
      try {
        const e = JSON.parse(line);
        if (e.event !== "verify") continue;
        if (e.check === "template") v.template = !!e.ok;
        if (e.check === "poppler") v.poppler = !!e.ok;
        if (e.check === "credentials") v.cred = e.state ?? (e.ok ? "env" : "missing");
      } catch { /* skip */ }
    }
    return v;
  };
  const runVerify = async () => {
    try {
      setVerify(parseVerify(await invoke<string>("run_core_capture", { args: ["verify"] })));
    } catch (e) {
      setVerify(parseVerify(String(e)));  // verify は不備時に終了コード1で stdout ごと届く
    }
  };
  useEffect(() => { runVerify(); }, []);

  const importCredentials = async () => {
    const p = await invoke<string | null>("pick_json", { save: false });
    if (!p) return;
    setImporting(true);
    try {
      await invoke<string>("run_core_capture", { args: ["import-credentials", p] });
      setNotice("認証キーを暗号化して保存しました。元のファイルは削除して構いません。");
      await runVerify();
    } catch (e) {
      setError(`認証キーの取り込みに失敗しました: ${e}`);
    } finally {
      setImporting(false);
    }
  };

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
          if (ev.event === "page") {
            setDone((d) => d + 1);
            if (ev.status && ev.status !== "done") {
              setFailures((f) => [...f, { page_id: ev.page_id, status: ev.status }]);
            }
          }
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
    setRunning(true); setSummary(null); setError(""); setNotice("");
    setLog([]); setDone(0); setTotal(0); setFailures([]);
    interruptedRef.current = false;
    try {
      const code = await invoke<number>("run_core", { args: ["run", "--input", inputDir] });
      if (interruptedRef.current) {
        setNotice("中断しました。処理済みの内容は保存されています。再開すると続きから処理します。");
      } else if (code !== 0) {
        setError(`読み取りが中断されました（終了コード ${code}）。再度「読み取りを開始」を押すと続きから処理します。`);
      }
    } catch (e) {
      if (!interruptedRef.current) setError(String(e));
    } finally {
      setRunning(false);
    }
  };
  const interrupt = async () => {
    interruptedRef.current = true;
    try { await invoke("kill_core"); } catch { /* 既に終了 */ }
  };
  const openOutput = () =>
    invoke("open_folder", { path: outputDir }).catch((e) => setError(String(e)));

  const xlsxName = summary?.xlsx?.split(/[\\/]/).pop();

  return (
    <div className="run-screen">
      <div className="run-main">

        {/* 完了バナー */}
        {summary && (
          <div className="banner ok">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#16a34a"
              strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><path d="M8 12.5l3 3 5-6" />
            </svg>
            <div className="txt">
              <b>読み取りが完了しました</b>
              <span>Excel と CSV を保存しました{xlsxName ? `（${xlsxName}）` : ""}</span>
            </div>
          </div>
        )}

        {/* 完了サマリ6項目（要件 §5.9 と同一。副題は平易な言葉） */}
        {summary && (
          <div className="summary6">
            <div className="sumcard"><span className="k">処理枚数</span>
              <span className="v">{summary.pages}</span><span className="s">読み取ったページ数</span></div>
            <div className="sumcard"><span className="k">出力行数</span>
              <span className="v">{summary.rows}</span><span className="s">Excel に出力した行数</span></div>
            <div className="sumcard"><span className="k">API送信回数</span>
              <span className="v">{summary.api_calls}</span><span className="s">クラウド OCR の送信回数</span></div>
            <div className="sumcard warn"><span className="k">要確認セル数総計</span>
              <span className="v">{summary.unclear_cells}</span><span className="s">〓の個数（要修正箇所）</span></div>
            <div className={summary.align_failed > 0 ? "sumcard err" : "sumcard"}>
              <span className="k">位置合わせ失敗</span>
              <span className="v">{summary.align_failed}</span><span className="s">読み取れなかったページ数</span></div>
            <div className="sumcard"><span className="k">行数超過件数</span>
              <span className="v">{summary.overflow}</span><span className="s">行数を超過したページ数</span></div>
          </div>
        )}
        {summary && (
          <div style={{ display: "flex", gap: 12 }}>
            <button className="btn primary big" onClick={openOutput}>
              <FolderIcon c="#ffffff" />出力フォルダを開く
            </button>
            <button className="btn big" onClick={start}>再度読み取る</button>
            <button className="btn" onClick={() => setSummary(null)}>条件を変更して読み取る</button>
          </div>
        )}

        {/* 処理中 */}
        {running && (
          <div className="card progress-card">
            <div className="head">
              <svg className="spin" width="28" height="28" viewBox="0 0 24 24" fill="none"
                stroke="#2563eb" strokeWidth="2.5" strokeLinecap="round">
                <path d="M21 12a9 9 0 1 1-6.2-8.56" />
              </svg>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <b>読み取り中…</b>
                <span>完了までお待ちください。他の作業を続けていただいて構いません。</span>
              </div>
            </div>
            <div className="counter">処理中: <b>{Math.min(done + 1, Math.max(total, 1))}</b> / <b>{total || "?"}</b> ページ</div>
            <div className="bar"><div style={{ width: `${total ? (done / total) * 100 : 4}%` }} /></div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div className="softnote" style={{ flex: 1 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5a6577"
                  strokeWidth="2" strokeLinecap="round"><path d="M13 2L4 14h6l-1 8 9-12h-6z" /></svg>
                途中で終了しても問題ありません。次回起動時は未処理分から再開します。
              </div>
              <button className="btn" onClick={interrupt}>中断</button>
            </div>
          </div>
        )}

        {/* はじめの準備（資格情報が無いときだけ） */}
        {!running && verify && verify.cred === "missing" && (
          <div className="card" style={{ borderColor: "var(--warn-line)", background: "var(--warn-bg)" }}>
            <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8a5a13"
                strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <circle cx="8" cy="15" r="4" />
                <path d="M11 12L21 2" /><path d="M17 6l3 3" /><path d="M14 9l2 2" /></svg>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <b style={{ color: "var(--warn-ink)", fontSize: 15 }}>初回設定: 読み取り用の認証キーを設定します</b>
                <div style={{ fontSize: 12.5, color: "#7a5a26", lineHeight: 1.7 }}>
                  管理者から受け取った<b>認証キーファイル（JSON）</b>を選択してください。
                  暗号化して保存され、元のファイルは以後不要です。
                </div>
                <button className="btn primary" style={{ width: "fit-content" }}
                  onClick={importCredentials} disabled={importing}>
                  {importing ? "取り込み中…" : "認証キーを選択"}
                </button>
              </div>
            </div>
          </div>
        )}

        {notice && <div className="tipbox">{notice}</div>}

        {/* 手順 1〜3（完了後は「条件を変更して読み取る」で再表示） */}
        {!running && !summary && (
          <>
            <div className="card step on">
              <div className="no">1</div>
              <div className="body">
                <div className="t">読み取る帳票フォルダの選択</div>
                <div className="d">スキャン済み PDF が保存されているフォルダを指定してください。</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <button className="btn outline" onClick={pickInput}>
                    <FolderIcon c="#2563eb" />フォルダを選ぶ
                  </button>
                  {inputDir
                    ? <div className="pathbox">{inputDir}</div>
                    : <span className="muted">未選択</span>}
                </div>
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">2</div>
              <div className="body">
                <div className="t">Excel の保存先の確認</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div className="pathbox">{outputDir}</div>
                  <button className="btn" onClick={pickOutput}>変更</button>
                </div>
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">3</div>
              <div className="body">
                <button className="btn primary big" style={{ width: "fit-content" }}
                  onClick={start} disabled={!inputDir || verify?.cred === "missing"}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="#ffffff">
                    <polygon points="6,4 20,12 6,20" /></svg>
                  読み取りを開始
                </button>
                {!inputDir && <span className="muted">フォルダを選択すると実行できます</span>}
              </div>
            </div>

            <div className="hintline">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#5a6577"
                strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="9" />
                <path d="M2 12h20" /><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" /></svg>
              読み取りにはインターネット接続が必要です
            </div>
          </>
        )}

        {summary && failures.length > 0 && (
          <div className="card">
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>
              処理できなかったページ（{failures.length} 件）
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {failures.map((f, i) => (
                <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5,
                  alignItems: "baseline", borderTop: i ? "1px solid var(--line)" : "none",
                  paddingTop: i ? 6 : 0 }}>
                  <span style={{ fontFamily: "Consolas, monospace", color: "var(--sub)",
                    flexShrink: 0 }}>{f.page_id}</span>
                  <span>{STATUS_JA[f.status] ?? f.status}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {log.length > 0 && (
          <details className="logbox">
            <summary>詳細ログ</summary>
            <pre ref={logRef}>{log.join("\n")}</pre>
          </details>
        )}
      </div>

      {/* 右カラム */}
      <div className="run-side">
        {summary ? (
          <>
            <div className="card nextsteps">
              <div className="explain"><div className="h">次の作業（目視確認）</div></div>
              <div className="row"><b>1.</b>
                <div>Excel を開き、先頭列の<b>「要確認セル数」</b>を降順に並べ替えます</div></div>
              <div className="row"><b>2.</b>
                <div>背景色付きの <span className="mark">〓</span> セルを、原本と照合して修正します</div></div>
              <div className="row"><b>3.</b>
                <div>修正のたびに「要確認セル数」は自動的に減ります。<b>合計が 0</b> になれば完了です</div></div>
            </div>
            {summary.align_failed > 0 && (
              <div className="errbox">
                位置合わせに失敗したページが {summary.align_failed} 件あります。該当行はすべて〓のため、原本を参照して直接入力してください。
              </div>
            )}
          </>
        ) : (
          <>
            <div className="card explain">
              <div className="h">このツールの動作</div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#2563eb"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="3" width="14" height="18" rx="2" />
                  <line x1="9" y1="8" x2="15" y2="8" /><line x1="9" y1="12" x2="15" y2="12" /></svg>
                <div>スキャンした帳票を1ページずつ読み取り、<b>1ページを Excel の1行</b>に変換します。</div>
              </div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#b45309"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3l9 16H3z" /><line x1="12" y1="10" x2="12" y2="14" /></svg>
                <div>確実に読み取れなかった箇所には、誤った文字を出力せず
                  <span className="mark">〓</span> を出力します。後の目視確認で修正します。</div>
              </div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#16a34a"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6L9 17l-5-5" /></svg>
                <div>処理が中断しても、<b>次回は未処理分から</b>再開します。同じページを重複して送信することはありません。</div>
              </div>
            </div>
            <div className="tipbox">
              補足: アプリを終了してもデータは保持されます。再度起動して「読み取りを開始」を押すと続きから処理します。
            </div>
          </>
        )}
      </div>
    </div>
  );
}
