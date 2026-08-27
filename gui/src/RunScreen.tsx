// 実行画面（設計 §7.1・最小構成の6機能）。処理ロジックを持たず、
// コアの起動と JSON Lines 進捗（§7.3）の表示に徹する。
// UI はデザインカンバス「帳票OCRツール GUI」準拠: 番号つき手順・平易な言葉。
import { invoke } from "@tauri-apps/api/core";
import { listen, UnlistenFn } from "@tauri-apps/api/event";
import { useEffect, useRef, useState } from "react";

type Summary = {
  pages: number; rows: number; align_failed: number;
  api_calls: number; unclear_cells: number; overflow: number;
  xlsx?: string; csv?: string;
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
      if (code !== 0) setError(`読み取りが途中で止まりました（終了コード ${code}）。もう一度「読み取りを開始する」を押すと続きから進みます。`);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
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
              <b>読み取りが終わりました</b>
              <span>Excel と CSV を保存しました{xlsxName ? `（${xlsxName}）` : ""}</span>
            </div>
          </div>
        )}

        {/* 完了サマリ6項目（要件 §5.9 と同一。副題は平易な言葉） */}
        {summary && (
          <div className="summary6">
            <div className="sumcard"><span className="k">処理枚数</span>
              <span className="v">{summary.pages}</span><span className="s">読み取った紙の枚数</span></div>
            <div className="sumcard"><span className="k">出力行数</span>
              <span className="v">{summary.rows}</span><span className="s">Excel にできた行の数</span></div>
            <div className="sumcard"><span className="k">API送信回数</span>
              <span className="v">{summary.api_calls}</span><span className="s">クラウドで読み取った回数</span></div>
            <div className="sumcard warn"><span className="k">要確認セル数総計</span>
              <span className="v">{summary.unclear_cells}</span><span className="s">〓の数＝あとで直す箇所</span></div>
            <div className={summary.align_failed > 0 ? "sumcard err" : "sumcard"}>
              <span className="k">位置合わせ失敗</span>
              <span className="v">{summary.align_failed}</span><span className="s">読み取れなかった紙</span></div>
            <div className="sumcard"><span className="k">行数超過件数</span>
              <span className="v">{summary.overflow}</span><span className="s">欄からはみ出た紙の数</span></div>
          </div>
        )}
        {summary && (
          <div style={{ display: "flex", gap: 12 }}>
            <button className="btn primary big" onClick={openOutput}>
              <FolderIcon c="#ffffff" />出力フォルダを開く
            </button>
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
                <b>読み取っています…</b>
                <span>このままお待ちください。パソコンを操作してもかまいません。</span>
              </div>
            </div>
            <div className="counter">いま <b>{Math.min(done + 1, Math.max(total, 1))}</b> 枚目 / 全 <b>{total || "?"}</b> 枚</div>
            <div className="bar"><div style={{ width: `${total ? (done / total) * 100 : 4}%` }} /></div>
            <div className="softnote">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5a6577"
                strokeWidth="2" strokeLinecap="round"><path d="M13 2L4 14h6l-1 8 9-12h-6z" /></svg>
              とちゅうで閉じても大丈夫。次に開いたとき、残りの紙だけを読み取ります。
            </div>
          </div>
        )}

        {/* 手順 1〜3 */}
        {!running && (
          <>
            <div className="card step on">
              <div className="no">1</div>
              <div className="body">
                <div className="t">読み取る帳票のフォルダを選ぶ</div>
                <div className="d">スキャンした PDF が入っているフォルダを選んでください。</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <button className="btn outline" onClick={pickInput}>
                    <FolderIcon c="#2563eb" />フォルダを選ぶ
                  </button>
                  {inputDir
                    ? <div className="pathbox">{inputDir}</div>
                    : <span className="muted">まだ選ばれていません</span>}
                </div>
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">2</div>
              <div className="body">
                <div className="t">Excel の保存先をたしかめる</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div className="pathbox">{outputDir}</div>
                  <button className="btn" onClick={pickOutput}>変更する</button>
                </div>
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">3</div>
              <div className="body">
                <button className="btn primary big" style={{ width: "fit-content" }}
                  onClick={start} disabled={!inputDir}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="#ffffff">
                    <polygon points="6,4 20,12 6,20" /></svg>
                  {summary ? "もう一度読み取る" : "読み取りを開始する"}
                </button>
                {!inputDir && <span className="muted">フォルダを選ぶと押せるようになります</span>}
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

        {error && <div className="error">{error}</div>}

        {log.length > 0 && (
          <details className="logbox">
            <summary>くわしい記録</summary>
            <pre ref={logRef}>{log.join("\n")}</pre>
          </details>
        )}
      </div>

      {/* 右カラム */}
      <div className="run-side">
        {summary ? (
          <>
            <div className="card nextsteps">
              <div className="explain"><div className="h">次にやること（目検）</div></div>
              <div className="row"><b>1.</b>
                <div>Excel を開き、いちばん左の<b>「要確認セル数」</b>を大きい順に並べ替える</div></div>
              <div className="row"><b>2.</b>
                <div>色つきの <span className="mark">〓</span> のセルを、紙の原本と見比べて手で直す</div></div>
              <div className="row"><b>3.</b>
                <div>直すたびに「要確認セル数」が自動で減る。<b>合計が 0</b> になったら完成</div></div>
            </div>
            {summary.align_failed > 0 && (
              <div className="errbox">
                「位置合わせ失敗」が {summary.align_failed} 件あります。その行はぜんぶ〓になっているので、紙を見ながらその行を入力してください。
              </div>
            )}
          </>
        ) : (
          <>
            <div className="card explain">
              <div className="h">このツールがやること</div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#2563eb"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="5" y="3" width="14" height="18" rx="2" />
                  <line x1="9" y1="8" x2="15" y2="8" /><line x1="9" y1="12" x2="15" y2="12" /></svg>
                <div>スキャンした帳票を1枚ずつ読み取り、<b>1枚＝Excel の1行</b>にします。</div>
              </div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#b45309"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 3l9 16H3z" /><line x1="12" y1="10" x2="12" y2="14" /></svg>
                <div>自信を持って読めなかった箇所は、まちがった字を入れずに
                  <span className="mark">〓</span> と書きます。あとで人が直します。</div>
              </div>
              <div className="row">
                <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#16a34a"
                  strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6L9 17l-5-5" /></svg>
                <div>途中でパソコンが止まっても、<b>次回は続きから</b>再開します。同じ紙を二重に読むことはありません。</div>
              </div>
            </div>
            <div className="tipbox">
              こまったら: 画面を閉じてもデータは消えません。もう一度開いて「読み取りを開始する」を押せば続きから進みます。
            </div>
          </>
        )}
      </div>
    </div>
  );
}
