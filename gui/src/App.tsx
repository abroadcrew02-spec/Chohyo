// GUI は2画面で構成する（要件 §5.10）: 実行画面とテンプレート編集画面。
// 追加で設定モーダル（要件 §5.8 の6項目・Should 標準構成）を持つ。
// 編集画面に未保存の変更がある状態でのタブ切替・ウィンドウを閉じる操作は
// 破棄確認を出す（v3.7 追加分）。
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { LogicalSize } from "@tauri-apps/api/dpi";
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

// テンプレート編集タブを開いたときに合わせるウィンドウサイズ（issue #65-4）。
// 実行画面は幅730の小窓を既定にしている（RunScreen.tsx の
// RUN_WINDOW_WIDTH/RUN_WINDOW_HEIGHT_DEFAULT）が、編集画面は列の並びなど
// 扱う情報量が多く、その縮小サイズのままだと見切れるため専用の広いサイズを持つ
const EDITOR_WINDOW_SIZE = { width: 1280, height: 860 };

function Settings({ onClose }: { onClose: () => void }) {
  const [cfg, setCfg] = useState<Cfg>(CFG_DEFAULT);
  const [saved, setSaved] = useState(false);
  // 読み込み失敗を握りつぶさない（issue Q-MF）。従来は catch(()=>{}) で
  // 既定値のまま表示していたため、実際の設定内容を知らずに保存すると、
  // 触っていない項目まで CFG_DEFAULT で上書きされる（例: send_limit が
  // 既定の100へ戻り、意図せず送信上限が変わる）。読み込めた項目が分から
  // ない以上、保存自体を止める
  const [loadError, setLoadError] = useState("");
  useEffect(() => {
    invoke<Partial<Cfg>>("read_config")
      .then((c) => { setCfg({ ...CFG_DEFAULT, ...c }); setLoadError(""); })
      .catch((e) => setLoadError(String(e)));
  }, []);
  const set = (k: keyof Cfg, v: string | number) => {
    setCfg((c) => ({ ...c, [k]: v })); setSaved(false);
  };
  // 数値入力は「表示用の文字列」を別に持つ（M-5）。制御コンポーネントで
  // 空文字を捨てると全選択→削除しても値が戻り、既存の数字を避けながら
  // 編集する羽目になる。空のまま保存されても 0 が入らないよう、確定は
  // blur と保存の2箇所で行う（issue #14: 〓閾値 0 は転記主義の無効化）。
  // 範囲補正は保存時に1回だけ（N-3: 打鍵ごとのクランプは入力途中の値を壊す）
  const [draft, setDraft] = useState<Partial<Record<keyof Cfg, string>>>({});
  const numValue = (k: keyof Cfg) => draft[k] ?? String(cfg[k]);
  const onNumChange = (k: keyof Cfg, raw: string) =>
    setDraft((d) => ({ ...d, [k]: raw }));
  const commitNum = (k: keyof Cfg, int = false) => {
    const raw = draft[k];
    setDraft((d) => { const n = { ...d }; delete n[k]; return n; });
    if (raw === undefined || raw.trim() === "") return;  // 空欄は前の値を維持
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
    // blur を経ずに保存を押された場合の未確定入力を取り込む
    const num = (k: keyof Cfg, int = false): number => {
      const raw = draft[k];
      if (raw === undefined || raw.trim() === "") return cfg[k] as number;
      const n = int ? Math.trunc(+raw) : +raw;
      return Number.isNaN(n) ? (cfg[k] as number) : n;
    };
    const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
    const fixed: Cfg = {
      ...cfg,
      unclear_threshold: clamp(num("unclear_threshold"), 0.01, 1),
      era_threshold: clamp(num("era_threshold"), 0.01, 1),
      send_limit: Math.max(0, num("send_limit", true)),
    };
    setCfg(fixed);
    setDraft({});
    setErr("");
    // write_config は未知キー・不正パス（空・ドライブ直下・UNC・.. 等）で
    // reject するようになった（issue Q-MC/S-MA・枠C申し送り）。reject を
    // 握りつぶすと「保存しました」の体で実は保存されていない状態になる
    try {
      await invoke("write_config", { patch: fixed as unknown as Record<string, unknown> });
      setSaved(true);
    } catch (e) {
      setErr(`設定の保存に失敗しました: ${e}`);
    }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>設定</h3>
        <p className="note" style={{ marginTop: -6 }}>
          通常は変更不要です。
        </p>
        {loadError && (
          <p className="note" style={{ color: "var(--err-ink)" }}>
            設定を読み込めませんでした（詳細: {loadError}）。
            現在の値が不明なため、保存を停止しています。
          </p>
        )}
        <label>〓と判定する基準値（0〜1）。大きいほど〓が増え、読み誤りの見落としが減ります
          <input type="number" min={0.01} max={1} step={0.01} value={numValue("unclear_threshold")}
            onChange={(e) => onNumChange("unclear_threshold", e.target.value)}
            onBlur={() => commitNum("unclear_threshold")} disabled={!!loadError} />
        </label>
        <label>丸印と判定する基準値（0〜1）
          <input type="number" min={0.01} max={1} step={0.01} value={numValue("era_threshold")}
            onChange={(e) => onNumChange("era_threshold", e.target.value)}
            onBlur={() => commitNum("era_threshold")} disabled={!!loadError} />
        </label>
        <label>1回の実行で送信する上限ページ数
          <input type="number" min={0} step={1} value={numValue("send_limit")}
            onChange={(e) => onNumChange("send_limit", e.target.value)}
            onBlur={() => commitNum("send_limit", true)} disabled={!!loadError} />
        </label>
        <label>Excel の保存先
          <input value={cfg.output_dir} onChange={(e) => set("output_dir", e.target.value)}
            disabled={!!loadError} />
        </label>
        <label>中間データの保存先（個人情報を含むため、クラウド同期されない場所を指定してください）
          <input value={cfg.workdir} onChange={(e) => set("workdir", e.target.value)}
            disabled={!!loadError} />
        </label>
        <label>ログの保存先
          <input value={cfg.log_dir} onChange={(e) => set("log_dir", e.target.value)}
            disabled={!!loadError} />
        </label>
        <div style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 6 }}>
          <button className="btn primary" onClick={save} disabled={!!loadError}>保存</button>
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
  // 設定の保存回数。実行画面が出力先の表示を読み直す合図にする（M-3）
  const [configRev, setConfigRev] = useState(0);
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

  // テンプレート編集タブを開くたびにウィンドウを専用サイズへ揃える。
  // 実行タブ側（RunScreen.tsx）と対になる「タブ切替のたびに規定サイズへ
  // 揃える」方針——手動リサイズの保持はしない（ユーザー承認済み・
  // 2026-09-01）。以前は「現在のサイズがこれより小さい時だけ拡大」という
  // 片方向のガードを持っていたが、規定サイズへ揃える方針に変更したため撤去した。
  // ブラウザのデモモードでは window API が無いため isTauri で no-op にする
  // （bridge.ts の Tauri 判定と同じ流儀）。
  useEffect(() => {
    if (!isTauri || tab !== "editor") return;
    getCurrentWindow()
      .setSize(new LogicalSize(EDITOR_WINDOW_SIZE.width, EDITOR_WINDOW_SIZE.height))
      .catch(() => { /* デモ/取得失敗時は何もしない（実行の妨げにしない） */ });
  }, [tab]);

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
        <RunScreen active={tab === "run"} configRev={configRev} />
      </div>
      <div className="editor-wrap" style={{ display: tab === "editor" ? "flex" : "none" }}>
        <Editor active={tab === "editor"} onDirty={(d) => { editorDirty.current = d; }} />
      </div>
      {showSettings && <Settings onClose={() => { setShowSettings(false);
                                                 setConfigRev((r) => r + 1); }} />}
    </div>
  );
}
