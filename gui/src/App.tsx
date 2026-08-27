// GUI は2画面で構成する（要件 §5.10）: 実行画面とテンプレート編集画面。
// 編集画面に未保存の変更がある状態でのタブ切替は破棄確認を出す（v3.7 追加分）。
import { useEffect, useRef, useState } from "react";
import { getCurrentWindow } from "@tauri-apps/api/window";
import Editor from "./Editor";
import RunScreen from "./RunScreen";
import "./App.css";

export default function App() {
  const [tab, setTab] = useState<"run" | "editor">("run");
  const editorDirty = useRef(false);

  useEffect(() => {
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
      <nav className="tabs">
        <button className={tab === "run" ? "active" : ""}
          onClick={() => switchTo("run")}>実行</button>
        <button className={tab === "editor" ? "active" : ""}
          onClick={() => switchTo("editor")}>テンプレート編集</button>
      </nav>
      {/* 編集画面はマウントを維持し、タブ切替で状態を失わない */}
      <div style={{ display: tab === "run" ? "block" : "none" }}><RunScreen /></div>
      <div className="editor-wrap" style={{ display: tab === "editor" ? "flex" : "none" }}>
        <Editor onDirty={(d) => { editorDirty.current = d; }} />
      </div>
    </div>
  );
}
