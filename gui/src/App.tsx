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

/** 保存で送るキーを「実際に変わったものだけ」に絞る（issue #69 Q-MF 差分送信）。
 *
 *  以前は6項目を毎回まとめて送っていた。read_config が返さなかった項目は
 *  CFG_DEFAULT で埋めて表示しているため、触っていない項目まで既定値として
 *  config.json へ書き込まれる——例えば core 側で既定が変わっても、GUI で
 *  1項目保存した瞬間に旧既定値が固定化される。差分だけ送れば、触っていない
 *  キーは config.json に現れないまま（＝core の既定に従うまま）になる。
 *
 *  ※ このモジュール（App.tsx）は CSS を import しているため gui-logic の
 *  esbuild ハーネス（Editor.tsx / RunScreen.tsx だけを束ねる）から取り込めず、
 *  単体テストは付けていない。ロジックを RunScreen 側へ移すと責務が壊れるので
 *  ここに置く。 */
function changedKeys(loaded: Cfg, next: Cfg): Partial<Cfg> {
  const patch: Partial<Cfg> = {};
  for (const k of Object.keys(next) as (keyof Cfg)[]) {
    if (next[k] !== loaded[k]) (patch[k] as Cfg[keyof Cfg]) = next[k];
  }
  return patch;
}

/** モーダルの Tab 循環キーハンドラ（issue #162 M1）。RunScreen.tsx の
 *  ConfirmDialog／Editor.tsx の modalKeyHandler と同じ実装——Escape で
 *  閉じ、ダイアログ内の最初/最後の要素で Tab が背景へ抜けないようにする。
 *  設定モーダルは App.tsx にしか無いためここに小さな関数として持つ
 *  （RunScreen.tsx・Editor.tsx を跨いだ共有ファイルは無く、App.tsx から
 *  それらを import すると循環 import になる）。 */
function modalKeyHandler(
  rootRef: React.RefObject<HTMLDivElement | null>, onClose: () => void,
) {
  return (e: React.KeyboardEvent) => {
    // issue #136 差し戻し対応: モーダル内のキー入力は Escape/Tab 以外も
    // ここで止める。stopPropagation を呼ばないと window 直付けの keydown
    // リスナー（Editor.tsx のキャンバス keyRef.current・Delete/矢印を
    // 処理する）まで素通りし、設定モーダルの裏で選択中の枠が消える／動く
    // （Editor.tsx の modalKeyHandler と同じ修正）
    e.stopPropagation();
    if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
    if (e.key !== "Tab") return;
    const root = rootRef.current;
    if (!root) return;
    const focusables = Array.from(root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter((el) => !el.hasAttribute("disabled"));
    if (focusables.length === 0) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
}

function Settings({ onClose }: { onClose: () => void }) {
  const [cfg, setCfg] = useState<Cfg>(CFG_DEFAULT);
  // 読み込んだ時点の値。保存時に差分を取る基準（issue #69 Q-MF）
  const [loaded, setLoaded] = useState<Cfg>(CFG_DEFAULT);
  const [saved, setSaved] = useState(false);
  // 読み込み失敗を握りつぶさない（issue Q-MF）。従来は catch(()=>{}) で
  // 既定値のまま表示していたため、実際の設定内容を知らずに保存すると、
  // 触っていない項目まで CFG_DEFAULT で上書きされる（例: send_limit が
  // 既定の100へ戻り、意図せず送信上限が変わる）。読み込めた項目が分から
  // ない以上、保存自体を止める
  const [loadError, setLoadError] = useState("");
  // issue #162 M1: ダイアログとして成立させるための3点——初期フォーカス先
  // （閉じる。破壊的でない側を既定にする＝ConfirmDialog と同じ考え方）・
  // Tab 循環の走査対象（modalRef）・Escape/Tab の共通ハンドラ
  const modalRef = useRef<HTMLDivElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { closeBtnRef.current?.focus(); }, []);
  const onModalKeyDown = modalKeyHandler(modalRef, onClose);
  useEffect(() => {
    invoke<Partial<Cfg>>("read_config")
      .then((c) => {
        const merged = { ...CFG_DEFAULT, ...c };
        setCfg(merged); setLoaded(merged); setLoadError("");
      })
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
    // 変えたキーだけを送る（issue #69 Q-MF 差分送信）。何も変わっていない
    // 保存は書き込み自体を行わない——config.json の更新日時だけが動いたり、
    // 触っていない項目が既定値として固定化されたりするのを防ぐ
    const patch = changedKeys(loaded, fixed);
    if (Object.keys(patch).length === 0) {
      setSaved(true);
      return;
    }
    try {
      await invoke("write_config", { patch: patch as unknown as Record<string, unknown> });
      setLoaded(fixed);
      setSaved(true);
    } catch (e) {
      setErr(`設定の保存に失敗しました: ${e}`);
    }
  };
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" ref={modalRef} role="dialog" aria-modal="true"
        aria-labelledby="settings-title" onClick={(e) => e.stopPropagation()}
        onKeyDown={onModalKeyDown}>
        <h3 id="settings-title">設定</h3>
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
          {/* issue #69 L-S5: 下限検査は 0.01 まで通す。0 に近い値は「〓を
              ほとんど出さない」設定で、目視確認の前提（要確認セルを全部見る）
              が崩れる。止めはしない（正当な調整もありうる）が、既定より
              低いことは伝える */}
          {numValue("unclear_threshold").trim() !== ""
            && +numValue("unclear_threshold") < CFG_DEFAULT.unclear_threshold && (
            <span style={{ color: "var(--warn-ink)" }}>
              既定（{CFG_DEFAULT.unclear_threshold}）より低い値です。〓が減るぶん、
              読み誤りの見落としが増えます。
            </span>
          )}
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
          <button ref={closeBtnRef} className="btn" onClick={onClose}>閉じる</button>
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
  // issue #162 M1: 設定モーダルを閉じたら呼び出し元（歯車ボタン）へ
  // フォーカスを戻す（3点目）。Editor.tsx の closeConfirmModal と同じ理由で
  // 1フレームずらす——モーダルの unmount 後に対象が存在する必要がある
  const settingsBtnRef = useRef<HTMLButtonElement>(null);
  const closeSettings = () => {
    setShowSettings(false);
    setConfigRev((r) => r + 1);
    requestAnimationFrame(() => settingsBtnRef.current?.focus());
  };

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
          <button ref={settingsBtnRef} title="設定" aria-label="設定"
            onClick={() => setShowSettings(true)} style={{ padding: "9px 12px" }}>
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
        {/* issue #136 差し戻し対応: 設定モーダルは App.tsx が描画するため、
            編集タブで枠を選択→歯車→設定モーダル内で Delete を押す経路は
            Editor.tsx の uiConfirm/confirmModal/userTplPanel だけでは
            見えない。showSettings を渡し、anyModalOpen（1つに集約した
            派生値）に含めてキャンバスの keydown を止める */}
        <Editor active={tab === "editor"} onDirty={(d) => { editorDirty.current = d; }}
          showSettings={showSettings} />
      </div>
      {showSettings && <Settings onClose={closeSettings} />}
    </div>
  );
}
