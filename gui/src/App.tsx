// GUI は2画面で構成する（要件 §5.10）: 実行画面とテンプレート編集画面。
// 追加で設定モーダル（要件 §5.8 の6項目・Should 標準構成）を持つ。
// 編集画面に未保存の変更がある状態でのタブ切替・ウィンドウを閉じる操作は
// 破棄確認を出す（v3.7 追加分）。
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { invoke, isTauri } from "./bridge";
import Editor from "./Editor";
import RunScreen from "./RunScreen";
import "./App.css";

type Cfg = {
  unclear_threshold: number; era_threshold: number; send_limit: number;
  output_dir: string; workdir: string; log_dir: string;
};
const CFG_DEFAULT: Cfg = {
  unclear_threshold: 0.85, era_threshold: 0.05, send_limit: 100,
  output_dir: "output", workdir: "workdir", log_dir: "logs",
};

function Settings({ onClose }: { onClose: () => void }) {
  const [cfg, setCfg] = useState<Cfg>(CFG_DEFAULT);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    invoke<Partial<Cfg>>("read_config")
      .then((c) => setCfg({ ...CFG_DEFAULT, ...c }))
      .catch(() => {});
  }, []);
  const set = (k: keyof Cfg, v: string | number) => {
    setCfg((c) => ({ ...c, [k]: v })); setSaved(false);
  };
  const save = async () => {
    await invoke("write_config", { patch: cfg as unknown as Record<string, unknown> });
    setSaved(true);
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>設定</h3>
        <p className="note" style={{ marginTop: -6 }}>
          ここを変えるのはまれです。ふだんはそのままで使えます。
        </p>
        <label>〓にする基準（0〜1）。大きくするほど〓が増え、見落としは減ります
          <input type="number" min={0} max={1} step={0.01} value={cfg.unclear_threshold}
            onChange={(e) => set("unclear_threshold", +e.target.value)} />
        </label>
        <label>丸印と判定する基準（0〜1）
          <input type="number" min={0} max={1} step={0.01} value={cfg.era_threshold}
            onChange={(e) => set("era_threshold", +e.target.value)} />
        </label>
        <label>1回の実行で読み取る上限枚数
          <input type="number" min={0} step={1} value={cfg.send_limit}
            onChange={(e) => set("send_limit", Math.max(0, Math.trunc(+e.target.value)))} />
        </label>
        <label>Excel の保存先
          <input value={cfg.output_dir} onChange={(e) => set("output_dir", e.target.value)} />
        </label>
        <label>途中データの保存先（個人情報を含みます・クラウド同期しない場所に）
          <input value={cfg.workdir} onChange={(e) => set("workdir", e.target.value)} />
        </label>
        <label>記録（ログ）の保存先
          <input value={cfg.log_dir} onChange={(e) => set("log_dir", e.target.value)} />
        </label>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
          <button className="btn primary" onClick={save}>保存する</button>
          <button className="btn" onClick={onClose}>閉じる</button>
          {saved && <span style={{ color: "var(--ok-ink)", fontSize: 12.5 }}>保存しました。次の読み取りから使われます。</span>}
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [tab, setTab] = useState<"run" | "editor">("run");
  const [showSettings, setShowSettings] = useState(false);
  const editorDirty = useRef(false);

  useEffect(() => {
    if (!isTauri) return;
    const un = getCurrentWindow().onCloseRequested((e) => {
      if (editorDirty.current &&
          !window.confirm("テンプレートに未保存の編集があります。破棄して終了しますか？")) {
        e.preventDefault();
      }
    });
    return () => { un.then((f) => f()); };
  }, []);

  const switchTo = (t: "run" | "editor") => {
    if (tab === "editor" && t !== "editor" && editorDirty.current &&
        !window.confirm("テンプレートに未保存の編集があります。破棄してよいですか？")) {
      return;
    }
    setTab(t);
  };

  return (
    <div className="app">
      <div className="appbar">
        <div className="logo">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#ffffff"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="4" y="3" width="16" height="18" rx="2" />
            <line x1="8" y1="8" x2="16" y2="8" /><line x1="8" y1="12" x2="16" y2="12" />
            <line x1="8" y1="16" x2="12" y2="16" /></svg>
        </div>
        <div className="titles">
          <b>帳票OCRツール</b>
          <span>紙の帳票を読み取って Excel にするツール</span>
        </div>
        <nav className="tabs">
          <button className={tab === "run" ? "active" : ""}
            onClick={() => switchTo("run")}>実行</button>
          <button className={tab === "editor" ? "active" : ""}
            onClick={() => switchTo("editor")}>テンプレート編集
            <span className="badge">管理者向け</span></button>
          <button title="設定" aria-label="設定" onClick={() => setShowSettings(true)}
            style={{ padding: "9px 12px" }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.7 1.7 0 0 0 .34-1.87 1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.7 1.7 0 0 0 1.87.34h.09a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.7 1.7 0 0 0-.34 1.87v.09a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.55 1z" /></svg>
          </button>
        </nav>
      </div>
      {/* 編集画面はマウントを維持し、タブ切替で状態を失わない */}
      <div style={{ display: tab === "run" ? "flex" : "none", flex: 1, minHeight: 0 }}>
        <RunScreen />
      </div>
      <div className="editor-wrap" style={{ display: tab === "editor" ? "flex" : "none" }}>
        <Editor onDirty={(d) => { editorDirty.current = d; }} />
      </div>
      {showSettings && <Settings onClose={() => setShowSettings(false)} />}
    </div>
  );
}
