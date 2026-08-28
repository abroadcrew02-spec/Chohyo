// テンプレート編集画面（設計 §7.2・要件 §5.10）。
// この画面が行うのはテンプレート JSON の読み書きと画像表示だけ。
// 枠候補の生成（罫線検出・等分割）はコアの detect-grid を呼ぶ（§6.9）。
// 座標はすべて「ページ座標」で編集し、保存時に表裏の面ローカルへ変換する。
import { invoke } from "./bridge";
import { useCallback, useEffect, useRef, useState } from "react";

type Rect = { x: number; y: number; w: number; h: number };
type Mark = { value: string; rect: Rect };
type Field = { uid: string; field_id: string; kind: "text" | "choice"; rect: Rect; marks: Mark[];
               normalize?: string };
type ColMark = { value: string; x_offset: number; width: number; y_offset?: number; height?: number };
type Column = { name: string; x_offset: number; width: number; kind: "text" | "choice";
                subfields: string; marks: ColMark[]; normalize?: string };
type Block = { x: number; y: number; rows: number };
type Table = { uid: string; table_id: string; row_pitch: number; row_height: number;
               blocks: Block[]; columns: Column[] };
type Excl = { uid: string; id: string; rect: Rect };
type Sel = { type: "field" | "table" | "excl"; uid: string } | null;
type Tool = "select" | "field" | "excl" | "table" | "split";

let seq = 0;
const uid = () => `u${++seq}`;

export default function Editor({ onDirty }: { onDirty: (d: boolean) => void }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  const [splitY, setSplitY] = useState(1880);
  const [fields, setFields] = useState<Field[]>([]);
  const [tables, setTables] = useState<Table[]>([]);
  const [excls, setExcls] = useState<Excl[]>([]);
  const [sel, setSel] = useState<Sel>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [zoom, setZoom] = useState(0.35);
  const [pan, setPan] = useState({ x: 10, y: 10 });
  const [dirtyState, setDirtyState] = useState(false);
  const [msg, setMsg] = useState("画像とテンプレートを読み込んで開始してください");
  const [errMsg, setErrMsg] = useState("");
  const [pending, setPending] = useState<Rect | null>(null); // テーブル外枠（生成待ち）
  const [genRows, setGenRows] = useState(5);
  const [genCols, setGenCols] = useState(4);
  const [genMode, setGenMode] = useState<"ruled" | "uniform">("ruled");
  const [imgPath, setImgPath] = useState("");
  // 選択肢入力の編集中の値（M-13）。選択が変わったら捨てる
  const [choiceDraft, setChoiceDraft] = useState<string | null>(null);
  const drag = useRef<{ mode: string; start: { x: number; y: number };
                        orig?: Rect; extra?: { x: number; y: number } } | null>(null);
  // 開いたテンプレートのメタ情報。faces 以外を編集画面は触らないが、保存時に
  // 既定値で上書きすると render_dpi/image が壊れる（issue #15）ため往復保持する
  const meta = useRef<{ template_id: string; render_dpi: number;
                        image: { width: number; height: number } | null;
                        record: Record<string, unknown> }>({
    template_id: "chouhyo-v1", render_dpi: 300, image: null, record: { pages: 1 } });

  const markDirty = useCallback((d: boolean) => { setDirtyState(d); onDirty(d); }, [onDirty]);
  const nextTableId = () => {
    const used = new Set(tables.map((t) => t.table_id));
    let n = tables.length + 1;
    while (used.has(`table${n}`)) n++;
    return `table${n}`;
  };
  const confirmDiscard = () =>
    !dirtyState || window.confirm("未保存の変更があります。破棄してよろしいですか？");

  // ---------- 描画 ----------
  const draw = useCallback(() => {
    const cv = canvasRef.current;
    if (!cv) return;
    const ctx = cv.getContext("2d")!;
    const { width, height } = cv.getBoundingClientRect();
    cv.width = width; cv.height = height;
    ctx.fillStyle = "#1c1f26"; ctx.fillRect(0, 0, width, height);
    ctx.save();
    ctx.translate(pan.x, pan.y); ctx.scale(zoom, zoom);
    if (imgRef.current) ctx.drawImage(imgRef.current, 0, 0);
    const W = imgSize?.w ?? 2490;
    const px = 1 / zoom;

    // 表裏分割線
    ctx.strokeStyle = "#ff5577"; ctx.lineWidth = 3 * px;
    ctx.beginPath(); ctx.moveTo(0, splitY); ctx.lineTo(W, splitY); ctx.stroke();

    const rect = (r: Rect, stroke: string, fill?: string) => {
      if (fill) { ctx.fillStyle = fill; ctx.fillRect(r.x, r.y, r.w, r.h); }
      ctx.strokeStyle = stroke; ctx.strokeRect(r.x, r.y, r.w, r.h);
    };
    ctx.lineWidth = 2 * px;
    for (const e of excls)
      rect(e.rect, sel?.uid === e.uid ? "#ffd54a" : "#888",
           "rgba(120,120,120,0.35)");
    for (const f of fields) {
      rect(f.rect, sel?.uid === f.uid ? "#ffd54a" : f.kind === "choice" ? "#c586ff" : "#4fc3f7");
      for (const m of f.marks) rect(m.rect, "#c586ff");
      ctx.fillStyle = "#9fd8ff"; ctx.font = `${13 * px * 2}px sans-serif`;
      ctx.fillText(f.field_id, f.rect.x + 4 * px, f.rect.y + 26 * px);
    }
    for (const t of tables) {
      const totalW = t.columns.length
        ? Math.max(...t.columns.map((c) => c.x_offset + c.width)) : 0;
      for (const b of t.blocks) {
        const bh = t.row_pitch * (b.rows - 1) + t.row_height;
        rect({ x: b.x, y: b.y, w: totalW, h: bh },
             sel?.uid === t.uid ? "#ffd54a" : "#7ce38b");
        ctx.strokeStyle = "rgba(124,227,139,0.55)"; ctx.lineWidth = px;
        for (let i = 0; i < b.rows; i++) {
          const top = b.y + t.row_pitch * i;
          for (const c of t.columns)
            ctx.strokeRect(b.x + c.x_offset, top, c.width, t.row_height);
        }
        ctx.lineWidth = 2 * px;
      }
      if (t.blocks[0]) {
        ctx.fillStyle = "#7ce38b";
        ctx.fillText(t.table_id, t.blocks[0].x, t.blocks[0].y - 8 * px);
      }
    }
    if (pending) rect(pending, "#ff9f43");
    ctx.restore();
  }, [excls, fields, tables, pending, sel, splitY, zoom, pan, imgSize]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [draw]);

  // ---------- 入出力 ----------
  const loadImage = async () => {
    if (!confirmDiscard()) return;
    const p = await invoke<string | null>("pick_image");
    if (!p) return;
    let imagePath = p;
    let note = "";
    if (p.toLowerCase().endsWith(".pdf")) {
      // スキャン PDF はコアで1ページ目を 300dpi 展開してから表示する
      //（run と同じ dpi＝テンプレート座標系と一致・issue #19）
      setMsg("PDF を展開しています…");
      try {
        const out = await invoke<string>("run_core_capture",
          { args: ["expand-page", "--input", p] });
        const ev = out.split("\n")
          .map((l) => { try { return JSON.parse(l); } catch { return null; } })
          .find((e) => e && e.event === "expand_page");
        if (!ev?.ok) {
          setErrMsg(`PDF を開けませんでした: ${ev?.error ?? "不明"}`);
          setMsg("");
          return;
        }
        imagePath = ev.page_path;
        note = ev.pages > 1 ? `（PDF の 1/${ev.pages} ページ目を表示）` : "（PDF を展開）";
      } catch (e) {
        setErrMsg(`PDF を開けませんでした: ${e}`);
        setMsg("");
        return;
      }
    }
    // 読み込み失敗を try の外に置くと、直前の「展開しています…」表示のまま
    // 黙って止まる（実測・2026-08-28）。必ず catch してエラーを見せる
    let src: string;
    try {
      src = await invoke<string>("read_file_b64", { path: imagePath });
    } catch (e) {
      setErrMsg(`画像を読み込めませんでした: ${e}`);
      setMsg("");
      return;
    }
    const im = new Image();
    im.onload = () => {
      imgRef.current = im;
      setImgSize({ w: im.naturalWidth, h: im.naturalHeight });
      setImgPath(imagePath);
      setErrMsg("");
      setMsg(`画像 ${im.naturalWidth}×${im.naturalHeight}${note}`);
      draw();
    };
    im.onerror = () => { setErrMsg("画像の表示に失敗しました"); setMsg(""); };
    im.src = src;
  };

  const toEditorState = (t: any) => {
    meta.current = {
      template_id: t.template_id ?? "chouhyo-v1",
      render_dpi: t.render_dpi ?? 300,
      image: t.image ?? null,
      record: t.record ?? { pages: 1 },
    };
    const fs: Field[] = []; const ts: Table[] = []; const es: Excl[] = [];
    let sy = 0;
    for (const face of t.faces) {
      const oy = face.source.rect.y;
      if (face.face_id === "back") sy = oy;
      for (const e of face.exclusions ?? [])
        es.push({ uid: uid(), id: e.id, rect: { ...e.rect, y: e.rect.y + oy } });
      for (const f of face.fields ?? [])
        fs.push({ uid: uid(), field_id: f.field_id, kind: f.kind,
                  rect: { ...f.rect, y: f.rect.y + oy }, normalize: f.normalize,
                  marks: (f.choice_marks ?? []).map((m: any) =>
                    ({ value: m.value, rect: { ...m.rect, y: m.rect.y + oy } })) });
      for (const tb of face.tables ?? [])
        ts.push({ uid: uid(), table_id: tb.table_id, row_pitch: tb.row_pitch,
                  row_height: tb.row_height,
                  blocks: tb.blocks.map((b: any) =>
                    ({ x: b.origin.x, y: b.origin.y + oy, rows: b.rows })),
                  columns: tb.columns.map((c: any) => ({
                    name: c.name, x_offset: c.x_offset, width: c.width, kind: c.kind,
                    subfields: (c.subfields ?? []).join(","),
                    normalize: c.normalize,
                    marks: c.choice_marks ?? [] })) });
    }
    setFields(fs); setTables(ts); setExcls(es); setSplitY(sy || 1880);
  };

  const loadTemplate = async () => {
    if (!confirmDiscard()) return;
    const p = await invoke<string | null>("pick_json", { save: false });
    if (!p) return;
    // JSON でないファイル・テンプレート以外の JSON を選ぶと、旧実装は
    // unhandled rejection で**画面が無反応**になっていた（レビュー M-14）
    let parsed: any;
    try {
      const text = await invoke<string>("read_text", { path: p });
      parsed = JSON.parse(text);
      if (!parsed || !Array.isArray(parsed.faces)) {
        throw new Error("faces が無い（テンプレート JSON ではありません）");
      }
    } catch (e) {
      setErrMsg(`テンプレートを読み込めませんでした: ${e}`);
      return;
    }
    toEditorState(parsed);
    // 前のファイルの検証エラーを現在の状態と誤読させない（レビュー N-5）
    setErrMsg("");
    markDirty(false); setMsg(`テンプレート読込: ${p}`);
  };

  const buildTemplate = () => {
    // 優先順: 実際に開いた画像の寸法 > 開いたテンプレートの image > 新規既定値
    const W = imgSize?.w ?? meta.current.image?.width ?? 2490;
    const H = imgSize?.h ?? meta.current.image?.height ?? 3510;
    const face = (id: "front" | "back") => {
      const [y0, y1] = id === "front" ? [0, splitY] : [splitY, H];
      const inFace = (y: number) => y >= y0 && y < y1;
      return {
        face_id: id,
        source: { page_offset: 0, rect: { x: 0, y: y0, w: W, h: y1 - y0 } },
        exclusions: excls.filter((e) => inFace(e.rect.y)).map((e) => ({
          id: e.id, rect: { ...e.rect, y: e.rect.y - y0 } })),
        fields: fields.filter((f) => inFace(f.rect.y)).map((f) => ({
          field_id: f.field_id, kind: f.kind,
          rect: { ...f.rect, y: f.rect.y - y0 },
          ...(f.normalize && f.kind === "text" ? { normalize: f.normalize } : {}),
          ...(f.kind === "choice" ? { choice_marks: f.marks.map((m) => ({
            value: m.value, rect: { ...m.rect, y: m.rect.y - y0 } })) } : {}) })),
        tables: tables.filter((t) => t.blocks[0] && inFace(t.blocks[0].y)).map((t) => ({
          table_id: t.table_id, row_pitch: t.row_pitch,
          row_height: t.row_height,
          blocks: t.blocks.map((b) => ({ origin: { x: b.x, y: b.y - y0 }, rows: b.rows })),
          columns: t.columns.map((c) => ({
            name: c.name, x_offset: c.x_offset, width: c.width, kind: c.kind,
            ...(c.subfields.trim()
              ? { subfields: c.subfields.split(",").map((s) => s.trim()).filter(Boolean) } : {}),
            ...(c.normalize && c.kind === "text" && !c.subfields.trim()
              ? { normalize: c.normalize } : {}),
            ...(c.kind === "choice" ? { choice_marks: c.marks } : {}) })) })),
      };
    };
    return {
      schema_version: 1, template_id: meta.current.template_id,
      render_dpi: meta.current.render_dpi,
      image: { width: W, height: H }, record: meta.current.record,
      faces: [face("front"), face("back")],
    };
  };

  const saveTemplate = async () => {
    const p = await invoke<string | null>("pick_json", { save: true });
    if (!p) return;
    await invoke("write_text", { path: p, content: JSON.stringify(buildTemplate(), null, 2) });
    markDirty(false);
    // 保存物をコアで検証（§8-14: エディタの JSON をコアがそのまま読めること）
    try {
      const out = await invoke<string>("run_core_capture",
        { args: ["verify", "--template", p] });
      const tpl = out.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
        .find((e) => e && e.check === "template");
      if (tpl?.ok) {
        setErrMsg("");
        setMsg(`保存＋コア検証 OK（${tpl.columns} 列）: ${p}`);
      } else {
        // 検証 NG を成功と同じ灰色の小さい文字で出すと気づかれない（レビュー D-7）
        setMsg(`保存先: ${p}`);
        setErrMsg(`保存しましたが、コアの検証で問題が見つかりました: ${tpl?.error ?? "不明"}`);
      }
    } catch (e) { setMsg(""); setErrMsg(`保存しましたが、コアの検証で問題が見つかりました: ${e}`); }
  };

  // ---------- 枠候補の生成（detect-grid）----------
  const generate = async () => {
    if (!pending) return;
    const region = [pending.x, pending.y, pending.w, pending.h].map(Math.round).join(",");
    try {
      let args: string[];
      if (genMode === "ruled") {
        if (!imgPath) { setMsg("罫線検出には画像が必要。等分割へ切り替えるか画像を開く"); return; }
        args = ["detect-grid", "--image", imgPath, "--region", region];
      } else {
        args = ["detect-grid", "--region", region, "--mode", "uniform",
                "--rows", String(genRows), "--cols", String(genCols)];
      }
      const out = await invoke<string>("run_core_capture", { args });
      const fit = out.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
        .find((e) => e && e.event === "detect_grid");
      if (!fit?.ok) {
        // 失敗は赤枠で出す（レビュー N-4: 灰色 msg だと成功と見分けが付かない）
        setMsg("");
        setErrMsg(fit?.error
          ? `枠を自動生成できませんでした: ${fit.error}`
          : "枠を自動生成できませんでした。「等分割」に切り替えてください。");
        return;
      }
      const t: Table = {
        uid: uid(), table_id: nextTableId(),
        row_pitch: Math.max(1, Math.round(fit.row_pitch)),
        row_height: fit.row_height,
        blocks: [{ x: fit.origin_x, y: fit.origin_y, rows: fit.rows }],
        columns: fit.columns.map((c: any, i: number) =>
          ({ name: `列${i + 1}`, x_offset: c.x_offset, width: c.width,
             kind: "text" as const, subfields: "", marks: [] })),
      };
      setTables((ts) => [...ts, t]); setSel({ type: "table", uid: t.uid });
      setPending(null); markDirty(true);
      setMsg(`枠候補を生成（${fit.mode}・${fit.rows}行・残差 ${fit.residual_px}px）`);
    } catch (e) { setMsg(`detect-grid 失敗: ${e}`); }
  };

  // ---------- マウス ----------
  const toPage = (e: React.MouseEvent) => {
    const r = canvasRef.current!.getBoundingClientRect();
    return { x: (e.clientX - r.left - pan.x) / zoom, y: (e.clientY - r.top - pan.y) / zoom };
  };
  const hit = (p: { x: number; y: number }): Sel => {
    const inR = (r: Rect) => p.x >= r.x && p.x < r.x + r.w && p.y >= r.y && p.y < r.y + r.h;
    for (const f of fields) if (inR(f.rect)) return { type: "field", uid: f.uid };
    for (const e of excls) if (inR(e.rect)) return { type: "excl", uid: e.uid };
    for (const t of tables) {
      const w = t.columns.length ? Math.max(...t.columns.map((c) => c.x_offset + c.width)) : 0;
      for (const b of t.blocks)
        if (inR({ x: b.x, y: b.y, w, h: t.row_pitch * (b.rows - 1) + t.row_height }))
          return { type: "table", uid: t.uid };
    }
    return null;
  };
  const onDown = (e: React.MouseEvent) => {
    const p = toPage(e);
    if (e.button === 1 || e.altKey) {
      drag.current = { mode: "pan", start: { x: e.clientX, y: e.clientY },
                       extra: { ...pan } };
      return;
    }
    if (tool === "split") { setSplitY(Math.round(p.y)); markDirty(true); return; }
    if (tool === "select") {
      const h = hit(p);
      setSel(h);
      if (h) {
        // setSel は非同期なので selRect()（閉包の sel）は前回選択を返す。
        // 必ず今回ヒットした h から矩形を引く（issue #12: 別図形の座標が適用される）
        const r = h.type === "field" ? fields.find((f) => f.uid === h.uid)?.rect ?? null
                : h.type === "excl" ? excls.find((x) => x.uid === h.uid)?.rect ?? null
                : null;
        if (r && h.type !== "table") {
          const nearBR = Math.abs(p.x - (r.x + r.w)) < 12 / zoom &&
                         Math.abs(p.y - (r.y + r.h)) < 12 / zoom;
          drag.current = { mode: nearBR ? "resize" : "move", start: p, orig: { ...r } };
        } else if (h.type === "table") {
          const t = tables.find((x) => x.uid === h.uid)!;
          drag.current = { mode: "moveTable", start: p,
                           extra: { x: t.blocks[0].x, y: t.blocks[0].y } };
        }
      }
      return;
    }
    drag.current = { mode: `draw-${tool}`, start: p };
  };

  const onMove = (e: React.MouseEvent) => {
    const d = drag.current;
    if (!d) return;
    const p = toPage(e);
    if (d.mode === "pan" && d.extra) {
      setPan({ x: d.extra.x + (e.clientX - d.start.x), y: d.extra.y + (e.clientY - d.start.y) });
      return;
    }
    const dx = p.x - d.start.x, dy = p.y - d.start.y;
    const norm = (a: { x: number; y: number }, b: { x: number; y: number }): Rect => ({
      x: Math.round(Math.min(a.x, b.x)), y: Math.round(Math.min(a.y, b.y)),
      w: Math.round(Math.abs(b.x - a.x)), h: Math.round(Math.abs(b.y - a.y)) });
    if (d.mode.startsWith("draw-")) { setPending(norm(d.start, p)); return; }
    if (!sel) return;
    if (d.mode === "move" && d.orig) {
      const r = { ...d.orig, x: Math.round(d.orig.x + dx), y: Math.round(d.orig.y + dy) };
      applySelRect(r);
    } else if (d.mode === "resize" && d.orig) {
      applySelRect({ ...d.orig, w: Math.max(5, Math.round(d.orig.w + dx)),
                     h: Math.max(5, Math.round(d.orig.h + dy)) });
    } else if (d.mode === "moveTable" && d.extra) {
      const t = tables.find((x) => x.uid === sel.uid);
      if (t) {
        const ddx = Math.round(d.extra.x + dx) - t.blocks[0].x;
        const ddy = Math.round(d.extra.y + dy) - t.blocks[0].y;
        updateTable(sel.uid, { blocks: t.blocks.map((b) =>
          ({ ...b, x: b.x + ddx, y: b.y + ddy })) });
      }
    }
  };

  const onUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d || !d.mode.startsWith("draw-") || !pending) return;
    if (pending.w < 8 || pending.h < 8) { setPending(null); return; }
    if (d.mode === "draw-field") {
      // 既存 ID と衝突しない番号を選ぶ（レビュー M-15: length+1 だと
      // 削除後に重複し、保存時のコア検証まで気づけなかった）
      const used = new Set(fields.map((x) => x.field_id));
      let n = fields.length + 1;
      while (used.has(`field_${n}`)) n++;
      const f: Field = { uid: uid(), field_id: `field_${n}`,
                         kind: "text", rect: pending, marks: [] };
      setFields((fs) => [...fs, f]); setSel({ type: "field", uid: f.uid });
      setPending(null); markDirty(true);
    } else if (d.mode === "draw-excl") {
      const x: Excl = { uid: uid(), id: `excl_${excls.length + 1}`, rect: pending };
      setExcls((es) => [...es, x]); setSel({ type: "excl", uid: x.uid });
      setPending(null); markDirty(true);
    }
    // draw-table は pending を残し、サイドパネルの「枠候補を生成」で確定する
  };

  const onWheel = (e: React.WheelEvent) => {
    const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
    const r = canvasRef.current!.getBoundingClientRect();
    const cx = e.clientX - r.left, cy = e.clientY - r.top;
    setPan((p) => ({ x: cx - (cx - p.x) * factor, y: cy - (cy - p.y) * factor }));
    setZoom((z) => Math.min(3, Math.max(0.05, z * factor)));
  };

  // ---------- 選択対象の更新 ----------
  const applySelRect = (r: Rect) => {
    if (!sel) return;
    if (sel.type === "field")
      setFields((fs) => fs.map((f) => f.uid === sel.uid ? { ...f, rect: r } : f));
    if (sel.type === "excl")
      setExcls((es) => es.map((x) => x.uid === sel.uid ? { ...x, rect: r } : x));
    markDirty(true);
  };
  const updateField = (u: string, patch: Partial<Field>) => {
    setFields((fs) => fs.map((f) => f.uid === u ? { ...f, ...patch } : f)); markDirty(true);
  };
  const updateTable = (u: string, patch: Partial<Table>) => {
    setTables((ts) => ts.map((t) => t.uid === u ? { ...t, ...patch } : t)); markDirty(true);
  };
  const removeSel = () => {
    if (!sel) return;
    if (sel.type === "field") setFields((fs) => fs.filter((f) => f.uid !== sel.uid));
    if (sel.type === "excl") setExcls((es) => es.filter((e) => e.uid !== sel.uid));
    if (sel.type === "table") setTables((ts) => ts.filter((t) => t.uid !== sel.uid));
    setSel(null); markDirty(true);
  };
  const genFieldMarks = (f: Field, values: string) => {
    const vs = values.split(",").map((s) => s.trim()).filter(Boolean);
    const h = Math.floor(f.rect.h / Math.max(vs.length, 1));
    updateField(f.uid, { marks: vs.map((v, i) => ({
      value: v, rect: { x: f.rect.x + 4, y: f.rect.y + i * h + 2,
                        w: Math.max(8, f.rect.w - 8), h: Math.max(8, h - 4) } })) });
  };

  // ---------- サイドパネル ----------
  const panel = () => {
    if (pending && tool === "table") return (
      <div className="panel">
        <h3>表の枠を生成</h3>
        <label>生成方式
          <select value={genMode} onChange={(e) => setGenMode(e.target.value as any)}>
            <option value="ruled">罫線から自動検出</option>
            <option value="uniform">等分割（行数と列数を入力）</option>
          </select></label>
        {genMode === "uniform" && (<>
          <label>行数 <input type="number" value={genRows}
            onChange={(e) => setGenRows(+e.target.value)} /></label>
          <label>列数 <input type="number" value={genCols}
            onChange={(e) => setGenCols(+e.target.value)} /></label></>)}
        <button className="btn primary" onClick={generate}>生成</button>
        <button className="btn" onClick={() => setPending(null)}>キャンセル</button>
      </div>);
    if (!sel) return <div className="panel"><h3>要素が選択されていません</h3>
      <p className="note">ツールを選択し、帳票上をドラッグしてください。<br />ホイール: 拡大縮小 ／ Alt＋ドラッグ: 画面移動</p></div>;
    if (sel.type === "field") {
      const f = fields.find((x) => x.uid === sel.uid);
      if (!f) return null;
      return (
        <div className="panel">
          <h3>選択中の欄</h3>
          <label>欄の名前（出力の列名になります）<input value={f.field_id}
            onChange={(e) => updateField(f.uid, { field_id: e.target.value })} /></label>
          <label>欄の種類
            <select value={f.kind}
              onChange={(e) => updateField(f.uid, { kind: e.target.value as any,
                ...(e.target.value === "choice" ? { normalize: undefined } : {}) })}>
              <option value="text">文字（手書き文字を読み取る）</option>
              <option value="choice">選択式（昭・平・令などの丸囲み）</option>
            </select></label>
          {f.kind === "choice" && (
            // 制御コンポーネントにする（レビュー M-13）。defaultValue は
            // パネルの JSX 構造が同じだと React が DOM を再利用して更新されず、
            // **別の欄を選んでも前の欄の選択肢が表示されたまま**になり、
            // その状態で blur すると今の欄が前の値で上書きされていた
            <label>選択肢（カンマ区切り・縦方向に自動配置）
              <input placeholder="昭,平,令"
                value={choiceDraft ?? f.marks.map((m) => m.value).join(",")}
                onChange={(e) => setChoiceDraft(e.target.value)}
                onBlur={(e) => { genFieldMarks(f, e.target.value);
                                 setChoiceDraft(null); }} /></label>)}
          {f.kind === "text" && (
            <label>正規化（金額欄は「金額」を選んでください。桁区切りを外して数値化します）
              <select value={f.normalize ?? ""}
                onChange={(e) => updateField(f.uid, { normalize: e.target.value || undefined })}>
                <option value="">正規化なし</option>
                <option value="amount">金額</option>
              </select></label>)}
          <div className="mono">x:{f.rect.x} y:{f.rect.y} w:{f.rect.w} h:{f.rect.h}</div>
          <button onClick={removeSel}>削除</button>
        </div>);
    }
    if (sel.type === "excl") {
      const x = excls.find((e) => e.uid === sel.uid);
      if (!x) return null;
      return (
        <div className="panel">
          <h3>除外領域</h3>
          <label>id <input value={x.id} onChange={(e) => {
            setExcls((es) => es.map((v) => v.uid === x.uid ? { ...v, id: e.target.value } : v));
            markDirty(true); }} /></label>
          <div className="mono">x:{x.rect.x} y:{x.rect.y} w:{x.rect.w} h:{x.rect.h}</div>
          <button onClick={removeSel}>削除</button>
        </div>);
    }
    const t = tables.find((x) => x.uid === sel.uid);
    if (!t) return null;
    return (
      <div className="panel">
        <h3>選択中の表</h3>
        <label>表の名前 <input value={t.table_id}
          onChange={(e) => updateTable(t.uid, { table_id: e.target.value })} /></label>
        {/* 整数で保持する（レビュー M-22: 描画は float・保存時に round だと
            プレビューと実際の行位置が最大 0.5×(行数-1) px ずれていた。
            スキーマも integer なので入力時点で丸める） */}
        <label>行ピッチ <input type="number" step={1} value={t.row_pitch}
          onChange={(e) => updateTable(t.uid,
            { row_pitch: Math.max(1, Math.round(+e.target.value)) })} /></label>
        <label>行の高さ <input type="number" value={t.row_height}
          onChange={(e) => updateTable(t.uid, { row_height: +e.target.value })} /></label>
        {t.blocks.map((b, i) => (
          <label key={i}>ブロック{i + 1} 行数 <input type="number" value={b.rows}
            onChange={(e) => updateTable(t.uid, { blocks: t.blocks.map((v, j) =>
              j === i ? { ...v, rows: +e.target.value } : v) })} /></label>))}
        <button onClick={() => updateTable(t.uid, { blocks: [...t.blocks,
          { ...t.blocks[t.blocks.length - 1],
            x: t.blocks[t.blocks.length - 1].x + 1020 }] })}>右ブロックを追加（複製）</button>
        <h4>列</h4>
        {t.columns.length === 0 &&
          <p className="note">列がありません。「表を作成」で外枠を描くと生成されます。</p>}
        {t.columns.map((c, i) => (
          <div className="colrow" key={i}>
            <input className="w8" value={c.name} title="列名"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, name: e.target.value } : v) })} />
            <input className="w4" type="number" value={c.x_offset} title="x_offset"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, x_offset: +e.target.value } : v) })} />
            <input className="w4" type="number" value={c.width} title="width"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, width: +e.target.value } : v) })} />
            <select value={c.kind} title="列の種類"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                // 選択式へ切り替えたら正規化・分割は値ごと落とす。残すと画面に
                // 見えない値が保存され続け、分割は行の列数まで狂わせる
                // （レビュー D-5・issue #26）
                j === i ? { ...v, kind: e.target.value as any,
                            ...(e.target.value === "choice"
                              ? { normalize: undefined, subfields: "" } : {}) }
                        : v) })}>
              <option value="text">文字</option><option value="choice">選択式</option>
            </select>
            <span className="lbl">分割</span>
            <input className="w6" placeholder="年,月,日" value={c.subfields}
              disabled={c.kind === "choice"}
              title={c.kind === "choice" ? "選択式の列では使いません"
                                         : "複合セルの分割（例: 年,月,日）"}
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, subfields: e.target.value,
                            ...(e.target.value.trim() ? { normalize: undefined } : {}) } : v) })} />
            <span className="lbl">正規化</span>
            <select value={c.normalize ?? ""}
              disabled={c.kind === "choice" || !!c.subfields.trim()}
              title={c.kind === "choice" || c.subfields.trim()
                ? "選択式・分割指定の列では使いません"
                : "金額列は「金額」を選んでください"}
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, normalize: e.target.value || undefined } : v) })}>
              <option value="">正規化なし</option><option value="amount">金額</option>
            </select>
            <button onClick={() => updateTable(t.uid,
              { columns: t.columns.filter((_, j) => j !== i) })}>×</button>
          </div>))}
        <button onClick={removeSel}>テーブル削除</button>
        {/* 操作を左右する一次情報なので通常 note（--faint）より濃い色で出す（レビュー N-1） */}
        <p className="note" style={{ color: "var(--sub)" }}>金額の列には「正規化」で「金額」を設定してください（未設定は「保存して検証」で検出されます）。
          種類が「選択式」の列、分割を指定した列では正規化は使いません（入力が無効になります）。</p>
        <p className="note">選択式列のマーク位置の微調整は、保存した JSON の直接編集で行えます（v1 の範囲）</p>
      </div>);
  };

  return (
    <div className="editor">
      <div className="adminstrip">この画面では<b>帳票の読み取り位置（枠）を定義します</b>（管理者向け）。通常の読み取りは「実行」タブから行ってください。</div>
      <div className="toolbar">
        <button className="btn" onClick={loadImage}>帳票を開く（PDF・画像）</button>
        <button className="btn" onClick={loadTemplate}>テンプレートを開く</button>
        <button className="btn primary" onClick={saveTemplate}>保存して検証</button>
        <span className="sep" />
        {(["select", "field", "excl", "table", "split"] as Tool[]).map((t) => (
          <button key={t} className={tool === t ? "btn active" : "btn"}
            onClick={() => setTool(t)}>
            {{ select: "選択", field: "欄を追加", excl: "除外範囲", table: "表を作成", split: "表裏の境界" }[t]}
          </button>))}
        <span className="msg">{msg}{dirtyState ? "（未保存）" : ""}</span>
      </div>
      {errMsg && <div className="errbox" style={{ margin: "8px 18px" }}>{errMsg}</div>}
      <div className="editor-body">
        <canvas ref={canvasRef} className="canvas"
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
          onWheel={onWheel} onContextMenu={(e) => e.preventDefault()} />
        {panel()}
      </div>
    </div>
  );
}
