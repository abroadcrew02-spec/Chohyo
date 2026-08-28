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
  // 数値入力の全消去は +"" === 0 になる。0 の〓閾値は「低信頼値がすべて素通り」で
  // 転記主義を無効化するため、空・NaN は前の値を維持する（issue #14）。
  // 入力途中の値へキーストローク毎にクランプを掛けると「0.9」と打つ途中の「0」が
  // 補正されて意図しない値が確定するため、範囲補正は保存時に1回だけ行う（N-3）
  const setNum = (k: keyof Cfg, raw: string, int = false) => {
    if (raw === "") return;
    const n = int ? Math.trunc(+raw) : +raw;
    if (Number.isNaN(n)) return;
    set(k, n);
  };
  const [err, setErr] = useState("");
  const save = async () => {
    if ([cfg.output_dir, cfg.workdir, cfg.log_dir].some((d) => !d.trim())) {
      setErr("保存先のパスが空欄です。すべて入力してください。");
      return;
    }
    const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
    const fixed: Cfg = {
      ...cfg,
      unclear_threshold: clamp(cfg.unclear_threshold, 0.01, 1),
      era_threshold: clamp(cfg.era_threshold, 0.01, 1),
      send_limit: Math.max(0, Math.trunc(cfg.send_limit)),
    };
    setCfg(fixed);
    setErr("");
    await invoke("write_config", { patch: fixed as unknown as Record<string, unknown> });
    setSaved(true);
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>設定</h3>
        <p className="note" style={{ marginTop: -6 }}>
          通常は変更不要です。
        </p>
        <label>〓と判定する基準値（0〜1）。大きいほど〓が増え、読み誤りの見落としが減ります
          <input type="number" min={0.01} max={1} step={0.01} value={cfg.unclear_threshold}
            onChange={(e) => setNum("unclear_threshold", e.target.value)} />
        </label>
        <label>丸印と判定する基準値（0〜1）
          <input type="number" min={0.01} max={1} step={0.01} value={cfg.era_threshold}
            onChange={(e) => setNum("era_threshold", e.target.value)} />
        </label>
        <label>1回の実行で送信する上限ページ数
          <input type="number" min={0} step={1} value={cfg.send_limit}
            onChange={(e) => setNum("send_limit", e.target.value, true)} />
        </label>
        <label>Excel の保存先
          <input value={cfg.output_dir} onChange={(e) => set("output_dir", e.target.value)} />
        </label>
        <label>中間データの保存先（個人情報を含むため、クラウド同期されない場所を指定してください）
          <input value={cfg.workdir} onChange={(e) => set("workdir", e.target.value)} />
        </label>
        <label>ログの保存先
          <input value={cfg.log_dir} onChange={(e) => set("log_dir", e.target.value)} />
        </label>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
          <button className="btn primary" onClick={save}>保存</button>
          <button className="btn" onClick={onClose}>閉じる</button>
          {saved && <span style={{ color: "var(--ok-ink)", fontSize: 12.5 }}>保存しました。次回の読み取りから適用されます。</span>}
          {err && <span style={{ color: "var(--err-ink)", fontSize: 12.5 }}>{err}</span>}
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
          !window.confirm("テンプレートに未保存の変更があります。破棄して終了しますか？")) {
        e.preventDefault();
      }
    });
    return () => { un.then((f) => f()); };
  }, []);

  const switchTo = (t: "run" | "editor") => {
    if (tab === "editor" && t !== "editor" && editorDirty.current &&
        !window.confirm("テンプレートに未保存の変更があります。破棄してよろしいですか？")) {
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
          <span>スキャンした帳票を Excel データへ変換します</span>
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
