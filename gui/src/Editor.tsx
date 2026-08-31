// テンプレート編集画面（設計 §7.2・要件 §5.10）。
// この画面が行うのはテンプレート JSON の読み書きと画像表示だけ。
// 枠候補の生成（罫線検出・等分割）はコアの detect-grid を呼ぶ（§6.9）。
// 座標はすべて「ページ座標」で編集し、保存時に表裏の面ローカルへ変換する。
import { invoke } from "./bridge";
import { useCallback, useEffect, useRef, useState } from "react";

export type Rect = { x: number; y: number; w: number; h: number };
export type Mark = { value: string; rect: Rect };
type Field = { uid: string; field_id: string; kind: "text" | "choice"; rect: Rect; marks: Mark[];
               normalize?: string;
               // 参照先の枠（文字欄のみ・任意）。主の枠が完全に空のときだけ
               // ここの読取値を採用する（読めない〓のときは参照しない）
               fallback?: Rect;
               // 追加の領域（文字欄のみ・任意）。主の枠と等価な受け皿で、
               // どの領域の文字も同じ欄に集まり読み順で1つの値になる
               //（L字・コの字の欄。「別の欄と結合」「領域を追加」で作る）
               extras?: Rect[] };
type ColMark = { value: string; x_offset: number; width: number; y_offset?: number; height?: number };
type Column = { name: string; x_offset: number; width: number; kind: "text" | "choice";
                subfields: string; marks: ColMark[]; normalize?: string };
type Block = { x: number; y: number; rows: number };
type Table = { uid: string; table_id: string; row_pitch: number; row_height: number;
               blocks: Block[]; columns: Column[] };
type Excl = { uid: string; id: string; rect: Rect };
// part: "fallback"＝参照先 ／ "extra:<n>"＝追加領域の n 番目
type Sel = { type: "field" | "table" | "excl"; uid: string;
             part?: string } | null;
type Tool = "select" | "field" | "excl" | "table" | "split";
// 元に戻す／やり直しの1コマ。編集対象の4状態をまとめて差し替える
type Snap = { fields: Field[]; tables: Table[]; excls: Excl[]; splitY: number };

let seq = 0;
const uid = () => `u${++seq}`;

/** 選択肢マークを欄の矩形の中へ縦に等分配置する。
 *  選択肢テキストの入力（genFieldMarks）と、欄のリサイズ追従（issue #48）で
 *  同じ配置規則を使う必要があるため関数に切り出してある。 */
export function layoutMarks(rect: Rect, values: string[]): Mark[] {
  const h = Math.floor(rect.h / Math.max(values.length, 1));
  return values.map((v, i) => ({
    value: v,
    rect: { x: rect.x + 4, y: rect.y + i * h + 2,
            w: Math.max(8, rect.w - 8), h: Math.max(8, h - 4) } }));
}

/** 欄の矩形が変わったときに選択肢マークを追従させる（issue #48）。
 *
 *  旧実装は rect だけ差し替えていたため、選択式の欄を動かすとマークの帯が
 *  旧位置に取り残された。保存しても検証は通ってしまい、以後の読み取りが
 *  **誤った選択値**（元号など）を出す——〓に倒れないので転記主義に反する。
 *
 *  追従は「欄の変化をそのままマークへ写す」相似変換で行う。移動は平行移動、
 *  リサイズは比率でスケールする。layoutMarks で作り直さないのは、手で詰めた
 *  較正値を捨てないため——出荷テンプレートの person_生年月日_元号 は
 *  layoutMarks の算出値から x:+4 / y:+2〜+4 / w:-5 / h:+2 ずらして較正されており、
 *  丸印判定の余裕（1位2位差 0.0647）はこの較正に乗っている（issue #23）。
 *  欄を 1px 縮めただけで較正が消えるのは、利用者から見て予測できない。 */
export function remapMarks(
  f: { kind: "text" | "choice"; rect: Rect; marks: Mark[] }, next: Rect): Mark[] {
  if (f.kind !== "choice" || f.marks.length === 0) return f.marks;
  const cur = f.rect;
  if (next.x === cur.x && next.y === cur.y
      && next.w === cur.w && next.h === cur.h) return f.marks;
  // 元の欄が潰れていると比率を取れない。作り直しへ退避する
  if (cur.w <= 0 || cur.h <= 0) return layoutMarks(next, f.marks.map((m) => m.value));
  const sx = next.w / cur.w, sy = next.h / cur.h;
  return f.marks.map((m) => {
    const w = Math.max(1, Math.min(next.w, Math.round(m.rect.w * sx)));
    const h = Math.max(1, Math.min(next.h, Math.round(m.rect.h * sy)));
    // 丸めで欄から出ないように収める。欄外のマークはコア側の検証が
    // テンプレートごと拒否するため、ここで必ず内側に留める（#48 のコア側検証）
    const x = Math.min(Math.max(next.x + Math.round((m.rect.x - cur.x) * sx), next.x),
                       next.x + next.w - w);
    const y = Math.min(Math.max(next.y + Math.round((m.rect.y - cur.y) * sy), next.y),
                       next.y + next.h - h);
    return { ...m, rect: { x, y, w, h } };
  });
}

/// r から cut を引いた残りを最大4つの矩形（上・下・左・右の帯）に分解する。
/// minSize 未満の細片は捨てる（1px の破片が残ると掴めない・読めないため）。
/// 重なっていなければ r をそのまま返す。
export function subtractRect(r: Rect, cut: Rect, minSize = 6): Rect[] {
  const ix = Math.max(r.x, cut.x), iy = Math.max(r.y, cut.y);
  const ix2 = Math.min(r.x + r.w, cut.x + cut.w);
  const iy2 = Math.min(r.y + r.h, cut.y + cut.h);
  if (ix >= ix2 || iy >= iy2) return [r];
  const out: Rect[] = [];
  if (iy - r.y >= minSize)
    out.push({ x: r.x, y: r.y, w: r.w, h: iy - r.y });
  if (r.y + r.h - iy2 >= minSize)
    out.push({ x: r.x, y: iy2, w: r.w, h: r.y + r.h - iy2 });
  if (ix - r.x >= minSize && iy2 - iy >= minSize)
    out.push({ x: r.x, y: iy, w: ix - r.x, h: iy2 - iy });
  if (r.x + r.w - ix2 >= minSize && iy2 - iy >= minSize)
    out.push({ x: ix2, y: iy, w: r.x + r.w - ix2, h: iy2 - iy });
  return out;
}

/// 文字欄 f から claim の面積を切り抜く。全領域（主＋追加）を分解し直し、
/// 最大の断片を主に据える。何も残らなければ null（呼び出し側は削らず警告）。
export function carveField(f: Field, claim: Rect, minSize = 6): Field | null {
  const pieces = [f.rect, ...(f.extras ?? [])]
    .flatMap((r) => subtractRect(r, claim, minSize));
  if (pieces.length === 0) return null;
  let bi = 0;
  pieces.forEach((r, i) => {
    if (r.w * r.h > pieces[bi].w * pieces[bi].h) bi = i;
  });
  return { ...f, rect: pieces[bi],
           extras: pieces.filter((_r, i) => i !== bi) };
}

/// テンプレート全体の重なりを一括で解消する（保存時に使う）。
/// 各欄の主枠・参照先・追加領域を「主張」として、他の文字欄を切り抜く。
/// 主枠も主張に含める（issue #59 H-3）——ドロップ時の autoCarve は主枠の
/// 移動・リサイズでも切り抜きが働くのに、保存時の一括解消は参照先・追加領域
/// しか見ておらず、矢印キー移動で作った主枠どうしの重なりが検証NGまで
/// 解消されないまま残っていた。
///
/// 主張の矩形は欄ごとに**その時点の最新状態**から取り直す（issue #59 H-3・
/// stale claim の根治）。以前は全欄の主張を最初に一括収集していたため、
/// 先に処理された欄の切り抜きで別の欄の領域構成が総入れ替えになっても、
/// 収集済みの「切り抜き前の旧領域」がそのまま主張として残り、もう存在しない
/// 領域が無関係な第三の欄を削っていた。定義順で前の欄が主張優先という
/// 現行方針は変えず、各欄を処理する直前に fs から取り直すことで対応する。
/// 切り抜けないもの（選択式・完全に覆われた欄）は skipped に返し、
/// 呼び出し側が警告する（保存自体は続け、コア検証が最終判定する）。
export function resolveOverlaps(fields: Field[]): {
  fields: Field[]; carved: string[]; skipped: string[];
} {
  let fs = fields;
  const carved = new Set<string>();
  const skipped = new Set<string>();
  for (const owner of fields.map((f) => f.uid)) {
    const cur = fs.find((f) => f.uid === owner);
    if (!cur) continue;   // 結合等で既に消えている場合は主張しない
    const claimRects = [cur.rect,
      ...(cur.fallback ? [cur.fallback] : []), ...(cur.extras ?? [])];
    for (const rect of claimRects) {
      fs = fs.map((f) => {
        if (f.uid === owner) return f;
        const touches = [f.rect, ...(f.extras ?? [])]
          .some((r) => _rectsTouch(r, rect));
        if (!touches) return f;
        if (f.kind !== "text") { skipped.add(f.field_id); return f; }
        const next = carveField(f, rect);
        if (!next) { skipped.add(f.field_id); return f; }
        carved.add(f.field_id);
        return next;
      });
    }
  }
  return { fields: fs, carved: [...carved], skipped: [...skipped] };
}

const _rectsTouch = (a: Rect, b: Rect) =>
  a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

/// 保存時、除外領域（Vision へ送らないマスク）が読み込み時点より減っていないか
/// 判定する（issue #55・検知層）。減っていれば確認メッセージを返し、
/// 減っていなければ null（確認不要）を返す。実際の confirm 呼び出しは
/// 呼び出し側（saveTemplate）が行う——ここは純粋な判定のみ。
export function exclusionRegressionNotice(
  loadedCount: number, currentCount: number): string | null {
  if (currentCount >= loadedCount) return null;
  return `除外領域（Vision へ送らないマスク）が ${loadedCount}→${currentCount} `
    + "に減っています。減らした覚えがなければキャンセルしてください。";
}

/// 読み込み時点の除外領域1件分のスナップショット（id → rect）。
/// id は draw-excl で採番される時点で全面を通して一意（`excl_${n}` を
/// 重複しないまで数え上げる）なので、面をまたいだタグ付けは不要。
export type ExclSnapshot = { id: string; rect: Rect };

/// 除外領域（Vision へ送らないマスク）が読み込み時点からどう変わったかを判定
/// する（issue #55・#59 QA再判定条件④「数と座標」）。件数比較だけでは
/// 「blackout が y1775→1640 へ135pxズレたが件数は変わらない」劣化を見逃す
/// ため、①件数減少 ②同一idのrect変化（位置・サイズ） ③idの入れ替わり
/// （削除+追加が同数）の順に検知する。いずれも無ければ null。
export function exclusionChangeNotice(
  loaded: ExclSnapshot[], current: ExclSnapshot[]): string | null {
  // ① 件数減少は既存の強い文言をそのまま使う（優先度最高）
  if (current.length < loaded.length)
    return exclusionRegressionNotice(loaded.length, current.length);

  // ② 同一 id で rect（位置・サイズ）が変わったもの
  const curById = new Map(current.map((e) => [e.id, e.rect]));
  const fmt = (r: Rect) => `${r.x},${r.y},${r.w},${r.h}`;
  const changed = loaded
    .map((l) => ({ id: l.id, from: l.rect, to: curById.get(l.id) }))
    .filter((c): c is { id: string; from: Rect; to: Rect } =>
      !!c.to && (c.to.x !== c.from.x || c.to.y !== c.from.y
                 || c.to.w !== c.from.w || c.to.h !== c.from.h));
  if (changed.length) {
    const shown = changed.slice(0, 3)
      .map((c) => `「${c.id}」の位置/サイズが変わっています（${fmt(c.from)} → ${fmt(c.to)}）`)
      .join("、");
    const more = changed.length > 3 ? `、ほか ${changed.length - 3} 件` : "";
    return `除外領域${shown}${more}。`
      + "マスクの位置がズレると隠すべき領域が Vision へ送信されます。意図した変更ですか？";
  }

  // ③ id の入れ替わり（削除と追加が同数＝件数は変わらないが構成が別物）
  const loadedIds = new Set(loaded.map((e) => e.id));
  const currentIds = new Set(current.map((e) => e.id));
  const removed = loaded.filter((e) => !currentIds.has(e.id)).map((e) => e.id);
  const added = current.filter((e) => !loadedIds.has(e.id)).map((e) => e.id);
  if (removed.length && removed.length === added.length) {
    return "除外領域の構成が入れ替わっています（削除: " + removed.join("、")
      + " ／ 追加: " + added.join("、")
      + "）。減らした・ズラした覚えがなければキャンセルしてください。";
  }
  return null;
}

/// 2つの欄を1つに結合する（B の全領域が A の追加領域になり、B は消える）。
/// 成功なら結合後の A を、できない場合は理由の文字列を返す。
export function absorbField(a: Field, b: Field): Field | string {
  if (a.uid === b.uid) return "同じ欄どうしは結合できません";
  if (a.kind !== "text" || b.kind !== "text")
    return "結合できるのは文字欄どうしだけです（選択式は帯の判定が単一矩形前提）";
  if (b.fallback)
    return "結合先に参照先が設定されています。先に参照先を削除してください";
  return { ...a, extras: [...(a.extras ?? []), b.rect, ...(b.extras ?? [])] };
}

/// 重なった枠の循環選択。cands は前面→背面の順。現在の選択が候補に
/// 含まれるときは**その次**（1つ下）を返し、末尾なら先頭へ戻る。
/// 含まれなければ最前面。Ctrl+クリックで呼ばれる（クリックのたびに
/// 1枚ずつ下へ潜り、最背面まで行ったら最前面へ戻る）。
export function nextOverlapPick<T extends { type: string; uid: string; part?: string }>(
  cands: T[], cur: { type: string; uid: string; part?: string } | null): T | null {
  if (cands.length === 0) return null;
  if (!cur) return cands[0];
  const i = cands.findIndex((c) =>
    c.type === cur.type && c.uid === cur.uid && c.part === cur.part);
  return i < 0 ? cands[0] : cands[(i + 1) % cands.length];
}

/// リサイズハンドル。8方向（角4＋辺の中点4）。
export type Handle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w";

const HANDLES: Handle[] = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];

export function handlePoints(r: Rect): Record<Handle, { x: number; y: number }> {
  const cx = r.x + r.w / 2, cy = r.y + r.h / 2;
  return {
    nw: { x: r.x, y: r.y }, n: { x: cx, y: r.y }, ne: { x: r.x + r.w, y: r.y },
    e: { x: r.x + r.w, y: cy }, se: { x: r.x + r.w, y: r.y + r.h },
    s: { x: cx, y: r.y + r.h }, sw: { x: r.x, y: r.y + r.h }, w: { x: r.x, y: cy },
  };
}

/// 点 p が矩形のどのハンドル上にあるか（tol は掴み判定の半径・template 座標系）。
export function handleAt(r: Rect, p: { x: number; y: number },
                         tol: number): Handle | null {
  const pts = handlePoints(r);
  for (const h of HANDLES) {
    const q = pts[h];
    if (Math.abs(p.x - q.x) <= tol && Math.abs(p.y - q.y) <= tol) return h;
  }
  return null;
}

/// ハンドル方向に応じた矩形の変形。最小 5px で止め、左・上ハンドルは
/// 位置も詰める（反転させない——反転を許すと w/h が負になり保存で壊れる）。
export function resizeBy(orig: Rect, handle: Handle,
                         dx: number, dy: number): Rect {
  let { x, y, w, h } = orig;
  if (handle.includes("e")) w = orig.w + dx;
  if (handle.includes("s")) h = orig.h + dy;
  if (handle.includes("w")) { x = orig.x + dx; w = orig.w - dx; }
  if (handle.includes("n")) { y = orig.y + dy; h = orig.h - dy; }
  if (w < 5) { if (handle.includes("w")) x = orig.x + orig.w - 5; w = 5; }
  if (h < 5) { if (handle.includes("n")) y = orig.y + orig.h - 5; h = 5; }
  return { x: Math.round(x), y: Math.round(y),
           w: Math.round(w), h: Math.round(h) };
}

const HANDLE_CURSOR: Record<Handle, string> = {
  nw: "nwse-resize", se: "nwse-resize", ne: "nesw-resize", sw: "nesw-resize",
  n: "ns-resize", s: "ns-resize", e: "ew-resize", w: "ew-resize",
};

/// 欄に新しい矩形を適用する（移動・リサイズの唯一の入口）。
///
/// 選択肢マークも一緒に動かす（issue #48）。rect だけ差し替えると帯が旧位置に
/// 残り、〓ではなく**誤った選択値**を出す原因になる。この配線自体をテストで
/// 守るために純関数として分けている——remapMarks の単体テストだけでは
/// 「呼び忘れ」を検出できない（レビュー4巡目）。
export function applyRectToField(f: Field, r: Rect): Field {
  return { ...f, rect: r, marks: remapMarks(f, r) };
}

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
  // Space 押下中はドラッグが常にパンになる（画像アプリの手のひらツール相当）。
  // ref は onDown での判定用、state はカーソル表示用
  const spaceRef = useRef(false);
  const [spaceHeld, setSpaceHeld] = useState(false);
  // 元に戻す／やり直し。ドラッグ中の連続更新を1コマにするため、状態が
  // 400ms 静止したときだけ履歴に積む（下の useEffect）
  const history = useRef<{ past: Snap[]; future: Snap[] }>({ past: [], future: [] });
  const snapRef = useRef<Snap | null>(null);
  const restoring = useRef(false);
  const [dirtyState, setDirtyState] = useState(false);
  const [msg, setMsg] = useState("画像とテンプレートを読み込んで開始してください");
  const [errMsg, setErrMsg] = useState("");
  const [pending, setPending] = useState<Rect | null>(null); // テーブル外枠（生成待ち）
  // 「参照先の枠を描く」で待ち受け中の欄 uid。セット中は次のドラッグが参照先になる
  const [fbTarget, setFbTarget] = useState<string | null>(null);
  // 「領域を追加」で待ち受け中の欄 uid（次のドラッグが追加領域になる）
  const [exTarget, setExTarget] = useState<string | null>(null);
  // 「別の欄と結合」で待ち受け中の欄 uid（次にクリックした欄を取り込む）
  const [mergeTarget, setMergeTarget] = useState<string | null>(null);
  // ハンドルにホバーしたときのリサイズカーソル（"" = 既定）
  const [hoverCursor, setHoverCursor] = useState("");
  const [genRows, setGenRows] = useState(5);
  const [genCols, setGenCols] = useState(4);
  const [genMode, setGenMode] = useState<"ruled" | "uniform">("ruled");
  const [imgPath, setImgPath] = useState("");
  // 現在読み込んでいるテンプレートの絶対パス（保存ダイアログの既定に使う・
  // issue #56 T1-3）。起動時の自動読込では未確定（null）のままにし、
  // 保存ダイアログ側で出荷テンプレートへフォールバックさせる
  const [tplPath, setTplPath] = useState<string | null>(null);
  // 読み込み時点の除外領域スナップショット（id→rect）。保存時にこれより
  // 減っていないか・座標やサイズが変わっていないかを確認する
  // （issue #55・#59 QA再判定条件④）。保存成功のたびに新しい基準へ更新する
  const [loadedExcls, setLoadedExcls] = useState<ExclSnapshot[]>([]);
  // 選択肢入力の編集中の値（M-13）。選択が変わったら捨てる
  const [choiceDraft, setChoiceDraft] = useState<string | null>(null);
  // パネルで触っている列（canvas ハイライト用・レビュー D-3）
  const [hlCol, setHlCol] = useState<number | null>(null);
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
    // 高精細ディスプレイでは CSS px と物理 px が 1:1 でない。バッファを
    // devicePixelRatio 倍で取り、以降を CSS px 座標系へ戻す（レビュー LOW）。
    // 座標を1px単位で詰める画面なので、罫線のにじみはそのまま作業精度に響く
    const dpr = window.devicePixelRatio || 1;
    cv.width = Math.round(width * dpr); cv.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
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
    // ラベルは画面上で読める固定サイズ（約26px）で描くため、縮小すると
    // 枠のほうが文字より小さくなる。あふれた文字が隣の枠に重なって
    // 画面が読めなくなるので（ユーザー報告・2026-08-31）、幅に収まらない分は
    // 「…」で詰め、1文字も収まらない・枠がラベルより背が低いときは描かない。
    // 枠の色で種別は分かり、クリックすれば右パネルに名前が出る
    const labelFont = `${26 * px}px sans-serif`;
    const label = (text: string, x: number, y: number,
                   maxW: number, maxH: number | null) => {
      if (!text) return;
      ctx.font = labelFont;
      if (maxH !== null && maxH < 30 * px) return;   // 枠が文字より低い
      let t = text;
      if (ctx.measureText(t).width > maxW) {
        while (t.length > 1 && ctx.measureText(t + "…").width > maxW)
          t = t.slice(0, -1);
        if (t.length <= 1) return;                    // 1文字も入らない
        t += "…";
      }
      ctx.fillText(t, x, y);
    };
    ctx.lineWidth = 2 * px;
    for (const e of excls)
      rect(e.rect, sel?.uid === e.uid ? "#ffd54a" : "#888",
           "rgba(120,120,120,0.35)");
    for (const f of fields) {
      rect(f.rect, sel?.uid === f.uid && sel?.part !== "fallback"
        ? "#ffd54a" : f.kind === "choice" ? "#c586ff" : "#4fc3f7");
      for (const m of f.marks) rect(m.rect, "#c586ff");
      ctx.fillStyle = "#9fd8ff";
      label(f.field_id, f.rect.x + 4 * px, f.rect.y + 26 * px,
            f.rect.w - 8 * px, f.rect.h);
      for (let i = 0; i < (f.extras?.length ?? 0); i++) {
        const ex = f.extras![i];
        rect(ex, sel?.uid === f.uid && sel?.part === `extra:${i}`
          ? "#ffd54a" : f.kind === "choice" ? "#c586ff" : "#4fc3f7");
        // 同じ欄の一部であることを細線で示す（参照先の破線と区別して実線）
        ctx.strokeStyle = "rgba(79,195,247,0.45)"; ctx.lineWidth = px;
        ctx.beginPath();
        ctx.moveTo(f.rect.x + f.rect.w / 2, f.rect.y + f.rect.h / 2);
        ctx.lineTo(ex.x + ex.w / 2, ex.y + ex.h / 2);
        ctx.stroke();
        ctx.lineWidth = 2 * px;
      }
      if (f.fallback) {
        // 参照先は破線＋主の枠との接続線。実線の欄と見分けが付き、
        // どの欄の参照先かが線で追える
        const fb = f.fallback;
        ctx.setLineDash([8 * px, 5 * px]);
        rect(fb, sel?.uid === f.uid && sel?.part === "fallback" ? "#ffd54a" : "#4fc3f7");
        ctx.strokeStyle = "rgba(79,195,247,0.5)"; ctx.lineWidth = px;
        ctx.beginPath();
        ctx.moveTo(f.rect.x + f.rect.w / 2, f.rect.y + f.rect.h / 2);
        ctx.lineTo(fb.x + fb.w / 2, fb.y + fb.h / 2);
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineWidth = 2 * px;
        ctx.fillStyle = "#9fd8ff";
        label(`${f.field_id} の参照先`, fb.x + 4 * px, fb.y + 26 * px,
              fb.w - 8 * px, fb.h);
      }
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
        // パネルで触っている列を画面上で示す（レビュー D-3: どれが金額列か
        // 数値を読んで突き合わせるしかなかった）
        if (sel?.uid === t.uid && hlCol !== null && t.columns[hlCol]) {
          const c = t.columns[hlCol];
          ctx.fillStyle = "rgba(255,213,74,0.28)";
          ctx.fillRect(b.x + c.x_offset, b.y, c.width, bh);
          ctx.strokeStyle = "#ffd54a"; ctx.lineWidth = 3 * px;
          ctx.strokeRect(b.x + c.x_offset, b.y, c.width, bh);
          ctx.lineWidth = 2 * px;
        }
        ctx.lineWidth = 2 * px;
      }
      if (t.blocks[0]) {
        ctx.fillStyle = "#7ce38b";
        // 表の上に出すラベルも表の実幅に収める。最低幅の緩衝を持たせると
        // 縮小時にはみ出た文字が隣の枠へ重なる（欄ラベルと同じ扱いにする）
        label(t.table_id, t.blocks[0].x, t.blocks[0].y - 8 * px,
              totalW - 4 * px, null);
      }
    }
    if (pending) rect(pending, "#ff9f43");
    // 選択中の矩形にリサイズハンドルを描く（画面上で常に同じ大きさ）。
    // 掴み所が見えないと「リサイズできない」ように見える（ユーザー指摘）
    {
      const selR = (() => {
        if (!sel) return null;
        if (sel.type === "field") {
          const f = fields.find((v) => v.uid === sel.uid);
          return sel.part === "fallback" ? f?.fallback ?? null : f?.rect ?? null;
        }
        if (sel.type === "excl")
          return excls.find((v) => v.uid === sel.uid)?.rect ?? null;
        return null;
      })();
      if (selR) {
        const hs = 8 * px;
        ctx.fillStyle = "#ffd54a";
        ctx.strokeStyle = "#1c1f26"; ctx.lineWidth = px;
        for (const q of Object.values(handlePoints(selR))) {
          ctx.fillRect(q.x - hs / 2, q.y - hs / 2, hs, hs);
          ctx.strokeRect(q.x - hs / 2, q.y - hs / 2, hs, hs);
        }
      }
    }
    ctx.restore();
  }, [excls, fields, tables, pending, sel, splitY, zoom, pan, imgSize, hlCol]);

  useEffect(() => { draw(); }, [draw]);
  useEffect(() => {
    const onResize = () => draw();
    window.addEventListener("resize", onResize);
    // タブが display:none の間に初回描画が走ると、getBoundingClientRect が
    // 0×0 を返して canvas バッファが 0 のまま固まる。実測（2026-08-28・実窓
    // CDP）で、編集画面へ切り替えても bufW=0 / cssW=960 のまま白紙で、
    // ウィンドウをリサイズして初めて描かれていた。表示サイズの変化を
    // 監視して描き直す——タブ切替も「0→実寸」の変化として拾える
    const cv = canvasRef.current;
    const ro = cv ? new ResizeObserver(() => draw()) : null;
    if (cv && ro) ro.observe(cv);
    return () => {
      window.removeEventListener("resize", onResize);
      ro?.disconnect();
    };
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
        // --no-mask: 除外領域を白塗りしない下地で返す（issue #59 H-8）。
        // 従来は常に出荷テンプレの除外を焼いた画像が下地になり、除外枠の
        // 位置調整・取捨の判断材料が画面から見えなかった。除外は既存の
        // 枠オーバーレイ描画（draw 内の excls ループ）で見えているので、
        // 下地側は焼かずに済む。--template は今読み込んでいるテンプレを
        // 明示する（未読込＝tplPath が null のときは省略し、lib.rs の
        // inject_default_template が出荷テンプレを注入する・第0段の配線）
        const args = ["expand-page", "--input", p, "--no-mask"];
        if (tplPath) args.push("--template", tplPath);
        const out = await invoke<string>("run_core_capture", { args });
        const ev = out.split("\n")
          .map((l) => { try { return JSON.parse(l); } catch { return null; } })
          .find((e) => e && e.event === "expand_page");
        if (!ev?.ok) {
          setErrMsg(`PDF を開けませんでした: ${ev?.error ?? "不明"}`);
          setMsg("");
          return;
        }
        imagePath = ev.page_path;
        // 位置合わせ済みの画像なら、テンプレートの枠が最初から記入欄の上に
        // 乗る。合わせられなかった紙は生画像なので、枠のズレを手で直さない
        // よう注意を出す（run は位置ズレを自動補正する——手直しはテンプレ
        // 変更扱いになり再割付・再送信の確認まで誘発する）
        const pageNote = ev.pages > 1 ? `PDF の 1/${ev.pages} ページ目・` : "";
        note = ev.aligned
          ? `（${pageNote}位置合わせ済み・枠が記入欄に重なって表示されます）`
          : `（${pageNote}位置合わせできませんでした。枠が少しズレて見えても、` +
            "読み取り時に自動補正されるため枠は動かさないでください）";
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
    // 見つからなかったことを 0 で表すと、裏面の原点が本当に 0 のときと
    // 区別できない（レビュー LOW: falsy-zero）。null を番兵にする
    let sy: number | null = null;
    for (const face of t.faces) {
      const oy = face.source.rect.y;
      if (face.face_id === "back") sy = oy;
      for (const e of face.exclusions ?? [])
        es.push({ uid: uid(), id: e.id, rect: { ...e.rect, y: e.rect.y + oy } });
      for (const f of face.fields ?? [])
        fs.push({ uid: uid(), field_id: f.field_id, kind: f.kind,
                  rect: { ...f.rect, y: f.rect.y + oy }, normalize: f.normalize,
                  fallback: f.fallback_rect
                    ? { ...f.fallback_rect, y: f.fallback_rect.y + oy } : undefined,
                  extras: (f.extra_rects ?? []).map((r: any) =>
                    ({ ...r, y: r.y + oy })),
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
    setFields(fs); setTables(ts); setExcls(es); setSplitY(sy ?? 1880);
    setLoadedExcls(es.map((e) => ({ id: e.id, rect: e.rect })));
  };

  // 起動時に出荷テンプレート（run が既定で使う templates/chouhyo-v1.json）を
  // 読み込む。エディタは「1から作る画面」ではなく「読み取りが実際に使っている
  // 欄を直す画面」——白紙で始めると、テンプレ編集なしの読み取りがどう仕分けて
  // いるのか見えず、全欄を手作業で作るものと誤解される（ユーザー指摘・2026-08-31）
  useEffect(() => {
    (async () => {
      try {
        const text = await invoke<string>("read_default_template");
        const parsed = JSON.parse(text);
        if (!parsed || !Array.isArray(parsed.faces)) throw new Error("faces が無い");
        toEditorState(parsed);
        resetHistory();   // 読み込み前の空状態へ Ctrl+Z で戻れると事故のもと
        markDirty(false);
        setMsg("出荷テンプレート（chouhyo-v1）を読み込みました。帳票を開いて位置を確認してください");
      } catch (e) {
        // 配布物欠損・開発中の白紙スタート。無言のままだと、この白紙が
        // 正常な初期状態と区別できず、保存時に出荷テンプレートを上書きする
        // 恐れがある（issue #56 T1-4）ため必ず可視化する
        setErrMsg("出荷テンプレートを自動読み込みできませんでした。白紙から始めると、"
          + `保存時に出荷テンプレートを上書きする恐れがあります: ${e}`);
      }
    })();
    // マウント時に1回だけ実行する（toEditorState は再生成されるが挙動は不変）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
    resetHistory();   // 別テンプレートをまたぐ Undo は誤操作のもと
    setTplPath(p);    // 保存ダイアログの既定をこのファイルにする（issue #56 T1-3）
    // 前のファイルの検証エラーを現在の状態と誤読させない（レビュー N-5）
    setErrMsg("");
    markDirty(false); setMsg(`テンプレート読込: ${p}`);
  };

  const buildTemplate = (fieldList: Field[] = fields) => {
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
        fields: fieldList.filter((f) => inFace(f.rect.y)).map((f) => ({
          field_id: f.field_id, kind: f.kind,
          rect: { ...f.rect, y: f.rect.y - y0 },
          ...(f.kind === "text" && f.fallback
            ? { fallback_rect: { ...f.fallback, y: f.fallback.y - y0 } } : {}),
          ...(f.kind === "text" && f.extras?.length
            ? { extra_rects: f.extras.map((r) => ({ ...r, y: r.y - y0 })) } : {}),
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

  // 確認ダイアログでキャンセルされたときの共通後始末。ファイルには一切
  // 触れていない時点で呼ぶ（issue #56 T1・T3・#59 H-1・#55: 確認なしの
  // 経路では上書きしない）
  const abortSave = (why: string) => {
    setMsg("");
    setErrMsg(`保存を中止しました（${why}）`);
  };

  const saveTemplate = async () => {
    const p = await invoke<string | null>("pick_json",
      { save: true, defaultPath: tplPath ?? undefined });
    if (!p) return;

    // 保存先が出荷テンプレートなら、以降の処理へ進む前に即確認する
    // （issue #56 T1-3）。保存の既定を「今読み込んでいるファイル」へ
    // 変えた後も、出荷テンプレートを明示的に選んだ場合はここで止める
    const isShipped = await invoke<boolean>("is_shipped_template_path", { path: p })
      .catch(() => false);
    if (isShipped && !window.confirm(
        "出荷テンプレートを上書きします。読み取りに直ちに影響します。"
        + "続行してよろしいですか？")) {
      abortSave("出荷テンプレートの上書き確認でキャンセル");
      return;
    }

    // 開いている画像の寸法がテンプレートと異なるまま保存すると、
    // geometry_hash が変わり全ページが再送信（課金）対象になる
    // （issue #59 H-1）。無言で上書きせず、寸法が変わるときだけ確認する。
    // テンプレートにまだ image が無い（新規作成中）場合は比較対象が無いので
    // 確認しない
    if (meta.current.image && imgSize
        && (meta.current.image.width !== imgSize.w
            || meta.current.image.height !== imgSize.h)) {
      const ok = window.confirm(
        "開いている画像の寸法がテンプレートと異なります（"
        + `${meta.current.image.width}×${meta.current.image.height} → `
        + `${imgSize.w}×${imgSize.h}）。`
        + "保存すると全ページが再送信（課金）の対象になります。続行してよろしいですか？");
      if (!ok) { abortSave("画像寸法の確認でキャンセル"); return; }
    }

    // 保存直前に重なりを一括解消する。ドロップ時の自動切り抜きは
    // 「置いた瞬間」にしか効かないため、開き直した下書きなど**以前から
    // 重なったままの状態**はここで拾う（ユーザー報告 2026-08-31）
    const resolved = resolveOverlaps(fields);
    if (resolved.carved.length) {
      setFields(resolved.fields);
      setSel(null);
    }
    const carveNote = resolved.carved.length
      ? `重なった欄を自動で切り抜きました: ${resolved.carved.join("、")}。`
      : "";
    if (resolved.skipped.length) {
      setErrMsg(`切り抜けない欄があります: ${resolved.skipped.join("、")}`
        + "（選択式・完全に覆われた欄は自動調整できません）");
    }

    // 除外領域（Vision へ送らないマスク）が読み込み時点から劣化していないか
    // 確認する（issue #55・#59 QA再判定条件④）。件数減少だけでなく、同一
    // idの座標・サイズ変化（例: blackoutがy1775→1640へ135pxズレる）や
    // idの入れ替わりも検知する。resolveOverlaps は excls を変更しないので、
    // ここでの内容はカーブ前後で変わらない
    const currentExclSnapshot: ExclSnapshot[] =
      excls.map((e) => ({ id: e.id, rect: e.rect }));
    const currentExclCount = currentExclSnapshot.length;
    const exclNotice = exclusionChangeNotice(loadedExcls, currentExclSnapshot);
    if (exclNotice && !window.confirm(exclNotice)) {
      abortSave("除外領域の変化確認でキャンセル");
      return;
    }

    const content = JSON.stringify(buildTemplate(resolved.fields), null, 2);

    // トランザクショナルな保存: まず一時ファイルへ書き、コア検証が ok の
    // ときだけ本番パスへ確定する。以前は検証より先にファイルを書き切って
    // いたため、検証 NG でも出荷テンプレートが壊れたまま「保存しました」と
    // 表示され、バックアップも巻き戻しも無かった（issue #56 T1）
    let stagedPath: string;
    try {
      stagedPath = await invoke<string>("write_template_staged", { path: p, content });
    } catch (e) {
      setMsg("");
      setErrMsg(`保存していません: 一時ファイルへの書き込みに失敗しました: ${e}`);
      return;
    }

    // 保存物をコアで検証（§8-14: エディタの JSON をコアがそのまま読めること）
    try {
      const out = await invoke<string>("run_core_capture",
        { args: ["verify", "--template", stagedPath] });
      const tpl = out.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
        .find((e) => e && e.check === "template");
      if (tpl?.ok) {
        await invoke("promote_template", { path: p });
        setTplPath(p);
        setLoadedExcls(currentExclSnapshot);
        markDirty(false);
        if (!resolved.skipped.length) setErrMsg("");
        // 欄数と列数の対応を常に見せる（差分は「分割＋管理6列」だけ、が
        // 一目で分かるように・ユーザー指摘 2026-08-31）。除外数は verify が
        // 数えたもの（シオン担当・T4 追加予定）を優先し、無ければ保存物側の
        // 数で代える
        const split = tpl.cells != null ? tpl.columns - 6 - tpl.cells : null;
        const exclCount = tpl.exclusions ?? currentExclCount;
        setMsg(carveNote + `保存＋コア検証 OK（`
          + (tpl.cells != null
             ? `欄 ${tpl.cells} → ${tpl.columns} 列＝欄${tpl.cells}`
               + (split ? `＋分割+${split}` : "") + `＋管理6`
             : `${tpl.columns} 列`)
          + (tpl.amount_cells != null ? `・金額 ${tpl.amount_cells} 列` : "")
          + `・除外 ${exclCount}`
          + `）: ${p}`);
      } else {
        // NG のときは元ファイルを無傷のまま保つ。検証NGを成功と同じ灰色の
        // 小さい文字で出すと気づかれない（レビュー D-7）ため赤帯へ出し、
        // 「保存していません」と明言する（issue #56 T1）
        await invoke("discard_staged", { path: p }).catch(() => {});
        setMsg("");
        setErrMsg("保存していません: コアの検証で問題が見つかりました: "
          + (tpl?.error ?? "不明"));
      }
    } catch (e) {
      await invoke("discard_staged", { path: p }).catch(() => {});
      setMsg("");
      setErrMsg(`保存していません: コアの検証に失敗しました: ${e}`);
    }
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
        // スキーマは integer 必須。描画は float で見えるのに保存で丸められると
        // 画面と JSON がずれる（レビュー M-22）ので、持つ時点で整数にする
        row_height: Math.max(1, Math.round(fit.row_height)),
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
  // ハンドルの掴み半径。画面上で約12px だが、縮小時に 12/zoom が枠の寸法を
  // 超えると「中央をクリックしても辺リサイズ」「右辺のつもりが角」になる。
  // 枠の短辺の 1/3 でキャップして、ハンドル同士と内部（移動）を食わない
  const grabTol = (r: Rect) =>
    Math.min(12 / zoom, Math.max(4, Math.min(r.w, r.h) / 3));

  // 置いた/動かした枠（claim）の下にある他の文字欄を自動で切り抜く。
  // 「後から置いた枠が勝つ」——重なりを手で組み直す手間と保存時の
  // 重なりエラーを無くす（ユーザー要望 2026-08-31）。切り抜けないもの
  // （選択式・完全に覆った欄）はそのまま残し、保存時の検証が受け止める。
  // 判定は閉包の fields から先に計算し、setFields は写像1回だけにする
  // （updater 内で配列に push すると StrictMode の二重実行で重複する）
  const autoCarve = (claim: Rect, ownerUid: string) => {
    const carved = new Map<string, Field>();
    const carvedNames: string[] = [];
    const skipped: string[] = [];
    for (const f of fields) {
      if (f.uid === ownerUid) continue;
      const touches = [f.rect, ...(f.extras ?? [])]
        .some((r) => _rectsTouch(r, claim));
      if (!touches) continue;
      if (f.kind !== "text") { skipped.push(f.field_id); continue; }
      const next = carveField(f, claim);
      if (!next) { skipped.push(f.field_id); continue; }
      carved.set(f.uid, next);
      carvedNames.push(f.field_id);
    }
    if (carved.size)
      setFields((fs) => fs.map((f) => carved.get(f.uid) ?? f));
    if (carvedNames.length || skipped.length) {
      const parts: string[] = [];
      if (carvedNames.length)
        parts.push(`重なった欄を自動で切り抜きました: ${carvedNames.join("、")}（Ctrl+Z で戻せます）`);
      if (skipped.length)
        parts.push(`切り抜けない欄があります: ${skipped.join("、")}（選択式・完全に覆われた欄は自動調整できません。枠を見直してください）`);
      setMsg(parts.join(" ／ "));
      if (carvedNames.length) markDirty(true);
    }
  };

  // 欄の部位（主／参照先／追加領域 n）から矩形を引く
  const partRect = (f: Field | undefined, part?: string): Rect | null => {
    if (!f) return null;
    if (!part) return f.rect;
    if (part === "fallback") return f.fallback ?? null;
    if (part.startsWith("extra:"))
      return f.extras?.[Number(part.slice(6))] ?? null;
    return null;
  };

  // 選択中の矩形（field の各部位／excl）。table は格子定義のため対象外
  const selectedRect = (): Rect | null => {
    if (!sel) return null;
    if (sel.type === "field")
      return partRect(fields.find((v) => v.uid === sel.uid), sel.part);
    if (sel.type === "excl")
      return excls.find((v) => v.uid === sel.uid)?.rect ?? null;
    return null;
  };

  // 点の下にある要素を**すべて**前面順で返す。先頭が従来の hit() と同じ
  // 最前面。Ctrl+クリックの循環選択（下の要素を選ぶ）が全候補を必要とする
  const hitAll = (p: { x: number; y: number }): NonNullable<Sel>[] => {
    const inR = (r: Rect) => p.x >= r.x && p.x < r.x + r.w && p.y >= r.y && p.y < r.y + r.h;
    const out: NonNullable<Sel>[] = [];
    for (const f of fields) if (inR(f.rect)) out.push({ type: "field", uid: f.uid });
    for (const f of fields)
      (f.extras ?? []).forEach((ex, i) => {
        if (inR(ex)) out.push({ type: "field", uid: f.uid, part: `extra:${i}` });
      });
    for (const f of fields)
      if (f.fallback && inR(f.fallback))
        out.push({ type: "field", uid: f.uid, part: "fallback" });
    for (const e of excls) if (inR(e.rect)) out.push({ type: "excl", uid: e.uid });
    for (const t of tables) {
      const w = t.columns.length ? Math.max(...t.columns.map((c) => c.x_offset + c.width)) : 0;
      for (const b of t.blocks)
        if (inR({ x: b.x, y: b.y, w, h: t.row_pitch * (b.rows - 1) + t.row_height })) {
          out.push({ type: "table", uid: t.uid });
          break;   // 同じ表の複数ブロックを重複候補にしない
        }
    }
    return out;
  };
  const hit = (p: { x: number; y: number }): Sel => hitAll(p)[0] ?? null;
  const onDown = (e: React.MouseEvent) => {
    const p = toPage(e);
    if (e.button === 1 || e.altKey || spaceRef.current) {
      drag.current = { mode: "pan", start: { x: e.clientX, y: e.clientY },
                       extra: { ...pan } };
      return;
    }
    if (mergeTarget) {
      // 「別の欄と結合」の待ち受け中: クリックした欄を取り込む
      const src = fields.find((f) => f.uid === mergeTarget);
      const hitSel = hitAll(p).find((c) => c.type === "field" && !c.part);
      setMergeTarget(null);
      if (!src || !hitSel) { setMsg("結合を中止しました（欄をクリックしてください）"); return; }
      const dst = fields.find((f) => f.uid === hitSel.uid)!;
      const merged = absorbField(src, dst);
      if (typeof merged === "string") { setErrMsg(merged); return; }
      setFields((fs) => fs.filter((f) => f.uid !== dst.uid)
        .map((f) => (f.uid === src.uid ? merged : f)));
      setSel({ type: "field", uid: src.uid });
      markDirty(true);
      setMsg(`「${dst.field_id}」を「${src.field_id}」に結合しました（領域 ${
        (merged.extras ?? []).length + 1} 個の1つの欄になりました）`);
      return;
    }
    if (exTarget) {
      // 「領域を追加」の待ち受け中: 次のドラッグが同じ欄の追加領域になる
      drag.current = { mode: "draw-extra", start: p };
      return;
    }
    if (fbTarget) {
      // 「参照先の枠を描く」の待ち受け中: 次のドラッグを参照先として描く
      drag.current = { mode: "draw-fallback", start: p };
      return;
    }
    if (tool === "split") { setSplitY(Math.round(p.y)); markDirty(true); return; }
    if (tool === "select") {
      // 選択中の矩形のハンドルは最優先で掴む。ハンドルは矩形の外周に
      // はみ出して表示されるため、hit()（矩形の内側判定）より先に見ないと
      // 角・辺の外側からの掴みが「選択解除」に化ける
      const cur = selectedRect();
      const curHnd = cur ? handleAt(cur, p, grabTol(cur)) : null;
      if (cur && curHnd) {
        drag.current = { mode: `resize-${curHnd}`, start: p, orig: { ...cur } };
        return;
      }
      // Ctrl+クリック: 重なった枠を前面から順に1枚ずつ潜って選ぶ。
      // 普通のクリックは従来どおり最前面（ドラッグ移動の起点を変えない）
      const h = e.ctrlKey ? nextOverlapPick(hitAll(p), sel) : hit(p);
      setSel(h);
      if (h) {
        // setSel は非同期なので selRect()（閉包の sel）は前回選択を返す。
        // 必ず今回ヒットした h から矩形を引く（issue #12: 別図形の座標が適用される）
        const r = h.type === "field"
                ? partRect(fields.find((f) => f.uid === h.uid), h.part)
                : h.type === "excl" ? excls.find((x) => x.uid === h.uid)?.rect ?? null
                : null;
        if (r && h.type !== "table") {
          const hnd = handleAt(r, p, grabTol(r));
          drag.current = hnd
            ? { mode: `resize-${hnd}`, start: p, orig: { ...r } }
            : { mode: "move", start: p, orig: { ...r } };
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
    if (!d) {
      // ドラッグ中でなければハンドルのホバー判定だけ行い、カーソルで
      // 「ここを掴めばリサイズできる」を見せる（変化時のみ setState）
      if (tool === "select") {
        const cur = selectedRect();
        const hnd = cur ? handleAt(cur, toPage(e), grabTol(cur)) : null;
        const want = hnd ? HANDLE_CURSOR[hnd] : "";
        if (want !== hoverCursor) setHoverCursor(want);
      } else if (hoverCursor) setHoverCursor("");
      return;
    }
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
    } else if (d.mode.startsWith("resize-") && d.orig) {
      applySelRect(resizeBy(d.orig, d.mode.slice(7) as Handle, dx, dy));
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
    // 欄（主・参照先・領域）の移動/リサイズを離した位置で、下の文字欄を
    // 自動切り抜き。矢印ナッジでは発動しない（1px 刻みの通過で隣の欄を
    // 少しずつ削ってしまうため。細かい重なりは保存時の検証が受け止める）
    if (d && (d.mode === "move" || d.mode.startsWith("resize-"))
        && sel?.type === "field") {
      const claim = partRect(fields.find((v) => v.uid === sel.uid), sel.part);
      if (claim) autoCarve(claim, sel.uid);
    }
    if (!d || !d.mode.startsWith("draw-") || !pending) return;
    if (pending.w < 8 || pending.h < 8) { setPending(null); return; }
    if (d.mode === "draw-extra") {
      if (exTarget && pending && pending.w >= 8 && pending.h >= 8) {
        const uid0 = exTarget;
        let idx = 0;
        setFields((fs) => fs.map((f) => {
          if (f.uid !== uid0) return f;
          idx = (f.extras ?? []).length;
          return { ...f, extras: [...(f.extras ?? []), pending] };
        }));
        setSel({ type: "field", uid: uid0, part: `extra:${idx}` });
        markDirty(true);
        setMsg("領域を追加しました（同じ欄の一部として読まれます）");
        autoCarve(pending, uid0);
      }
      setExTarget(null); setPending(null);
      return;
    }
    if (d.mode === "draw-fallback") {
      if (fbTarget) {
        const uid0 = fbTarget;
        setFields((fs) => fs.map((f) => f.uid === uid0
          ? { ...f, fallback: pending } : f));
        setSel({ type: "field", uid: uid0, part: "fallback" });
        setFbTarget(null); setPending(null); markDirty(true);
        setMsg("参照先の枠を追加しました（主の枠が空のときだけ読まれます）");
        autoCarve(pending, uid0);
      }
      return;
    }
    if (d.mode === "draw-field") {
      // 既存 ID と衝突しない番号を選ぶ（レビュー M-15: length+1 だと
      // 削除後に重複し、保存時のコア検証まで気づけなかった）
      const used = new Set(fields.map((x) => x.field_id));
      let n = fields.length + 1;
      while (used.has(`field_${n}`)) n++;
      const f: Field = { uid: uid(), field_id: `field_${n}`,
                         kind: "text", rect: pending, marks: [] };
      setFields((fs) => [...fs, f]); setSel({ type: "field", uid: f.uid });
      autoCarve(pending, f.uid);
      setPending(null); markDirty(true);
    } else if (d.mode === "draw-excl") {
      const usedE = new Set(excls.map((e) => e.id));   // 欄と同じく衝突回避（M-15）
      let m = excls.length + 1;
      while (usedE.has(`excl_${m}`)) m++;
      const x: Excl = { uid: uid(), id: `excl_${m}`, rect: pending };
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

  // ---------- キーボードショートカット（画像アプリ標準の流用） ----------
  const zoomBy = (factor: number) => {
    const cv = canvasRef.current;
    if (!cv) return;
    const { width, height } = cv.getBoundingClientRect();
    const cx = width / 2, cy = height / 2;   // 画面中央を基準に拡縮
    setPan((p) => ({ x: cx - (cx - p.x) * factor, y: cy - (cy - p.y) * factor }));
    setZoom((z) => Math.min(3, Math.max(0.05, z * factor)));
  };
  const fitView = () => {
    const cv = canvasRef.current;
    if (!cv) return;
    const { width, height } = cv.getBoundingClientRect();
    const W = imgSize?.w ?? meta.current.image?.width ?? 2490;
    const H = imgSize?.h ?? meta.current.image?.height ?? 3510;
    const z = Math.min(3, Math.max(0.05, Math.min((width - 40) / W, (height - 40) / H)));
    setZoom(z);
    setPan({ x: (width - W * z) / 2, y: (height - H * z) / 2 });
  };
  const nudge = (dx: number, dy: number) => {
    if (!sel) return;
    if (sel.type === "field") {
      if (sel.part === "fallback")
        setFields((fs) => fs.map((f) => f.uid === sel.uid && f.fallback
          ? { ...f, fallback: { ...f.fallback,
                                x: f.fallback.x + dx, y: f.fallback.y + dy } } : f));
      else if (sel.part?.startsWith("extra:")) {
        const i = Number(sel.part.slice(6));
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, extras: (f.extras ?? []).map((ex, j) =>
              j === i ? { ...ex, x: ex.x + dx, y: ex.y + dy } : ex) } : f));
      } else
        // マークも一緒に動かす（issue #48 と同じ経路）
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? applyRectToField(f, { ...f.rect, x: f.rect.x + dx, y: f.rect.y + dy }) : f));
    }
    if (sel.type === "excl")
      setExcls((es) => es.map((x) => x.uid === sel.uid
        ? { ...x, rect: { ...x.rect, x: x.rect.x + dx, y: x.rect.y + dy } } : x));
    if (sel.type === "table")
      setTables((ts) => ts.map((t) => t.uid === sel.uid
        ? { ...t, blocks: t.blocks.map((b) => ({ ...b, x: b.x + dx, y: b.y + dy })) } : t));
    markDirty(true);
  };

  // 履歴: 編集状態が 400ms 静止したら1コマとして積む（ドラッグ1回=1コマ）。
  // 復元直後の変化は積まない（restoring フラグ）
  useEffect(() => {
    if (restoring.current) { restoring.current = false; return; }
    const t = setTimeout(() => {
      const cur: Snap = { fields, tables, excls, splitY };
      const prev = snapRef.current;
      if (prev === null) { snapRef.current = cur; return; }   // 基準の初期化
      if (prev.fields !== cur.fields || prev.tables !== cur.tables
          || prev.excls !== cur.excls || prev.splitY !== cur.splitY) {
        history.current.past.push(prev);
        if (history.current.past.length > 100) history.current.past.shift();
        history.current.future = [];
        snapRef.current = cur;
      }
    }, 400);
    return () => clearTimeout(t);
  }, [fields, tables, excls, splitY]);

  const resetHistory = () => {
    history.current = { past: [], future: [] };
    snapRef.current = null;   // 次の静止時点が新しい基準になる
  };
  const restoreSnap = (snap: Snap) => {
    restoring.current = true;
    snapRef.current = snap;
    setFields(snap.fields); setTables(snap.tables);
    setExcls(snap.excls); setSplitY(snap.splitY);
    setSel(null); setPending(null); markDirty(true);
  };
  const undoEdit = () => {
    const prev = history.current.past.pop();
    if (!prev || !snapRef.current) return;
    history.current.future.push(snapRef.current);
    restoreSnap(prev);
  };
  const redoEdit = () => {
    const next = history.current.future.pop();
    if (!next || !snapRef.current) return;
    history.current.past.push(snapRef.current);
    restoreSnap(next);
  };

  // ハンドラは毎レンダー作り直されるため、リスナーは1回だけ張って
  // ref 経由で最新版を呼ぶ（依存配列で張り直すとキー押下中に外れる）
  const keyRef = useRef<(e: KeyboardEvent) => void>(() => {});
  keyRef.current = (e: KeyboardEvent) => {
    const tag = (document.activeElement?.tagName ?? "").toLowerCase();
    const typing = tag === "input" || tag === "textarea" || tag === "select";
    if (e.code === "Space" && !typing) {
      // ページスクロールとボタンの再押下を防ぐ。押しっぱなしのリピートは無視
      e.preventDefault();
      if (!spaceRef.current) { spaceRef.current = true; setSpaceHeld(true); }
      return;
    }
    if (typing) return;   // 入力欄では通常のテキスト編集を優先する
    const ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === "z") {
      e.preventDefault(); (e.shiftKey ? redoEdit : undoEdit)(); return;
    }
    if (ctrl && e.key.toLowerCase() === "y") { e.preventDefault(); redoEdit(); return; }
    if (ctrl && e.key === "0") { e.preventDefault(); fitView(); return; }
    if (ctrl && e.key === "1") { e.preventDefault(); zoomBy(1 / zoom); return; }
    if (ctrl && (e.key === "+" || e.key === "=")) { e.preventDefault(); zoomBy(1.15); return; }
    if (ctrl && e.key === "-") { e.preventDefault(); zoomBy(1 / 1.15); return; }
    if (e.key === "Escape") {
      setSel(null); setPending(null); setFbTarget(null);
      setExTarget(null); setMergeTarget(null); drag.current = null; return;
    }
    if ((e.key === "Delete" || e.key === "Backspace") && sel) {
      e.preventDefault(); removeSel(); return;
    }
    if (e.key.startsWith("Arrow") && sel) {
      e.preventDefault();
      const step = e.shiftKey ? 10 : 1;
      nudge(e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0,
            e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0);
    }
  };
  useEffect(() => {
    const kd = (ev: KeyboardEvent) => keyRef.current(ev);
    const ku = (ev: KeyboardEvent) => {
      if (ev.code === "Space") { spaceRef.current = false; setSpaceHeld(false); }
    };
    // ウィンドウからフォーカスが外れたまま keyup を取りこぼすと Space が
    // 押しっぱなし扱いで固まる。blur で必ず解除する
    const blur = () => { spaceRef.current = false; setSpaceHeld(false); };
    window.addEventListener("keydown", kd);
    window.addEventListener("keyup", ku);
    window.addEventListener("blur", blur);
    return () => {
      window.removeEventListener("keydown", kd);
      window.removeEventListener("keyup", ku);
      window.removeEventListener("blur", blur);
    };
  }, []);

  // ---------- 選択対象の更新 ----------
  const applySelRect = (r: Rect) => {
    if (!sel) return;
    if (sel.type === "field") {
      if (sel.part === "fallback")
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, fallback: r } : f));
      else if (sel.part?.startsWith("extra:")) {
        const i = Number(sel.part.slice(6));
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, extras: (f.extras ?? []).map((ex, j) => j === i ? r : ex) } : f));
      } else
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? applyRectToField(f, r) : f));
    }
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
    if (sel.type === "field") {
      if (sel.part === "fallback")
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, fallback: undefined } : f));
      else if (sel.part?.startsWith("extra:")) {
        const i = Number(sel.part.slice(6));
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, extras: (f.extras ?? []).filter((_ex, j) => j !== i) } : f));
      } else
        setFields((fs) => fs.filter((f) => f.uid !== sel.uid));
    }
    if (sel.type === "excl") setExcls((es) => es.filter((e) => e.uid !== sel.uid));
    if (sel.type === "table") setTables((ts) => ts.filter((t) => t.uid !== sel.uid));
    setSel(null); markDirty(true);
  };
  const genFieldMarks = (f: Field, values: string) => {
    const vs = values.split(",").map((s) => s.trim()).filter(Boolean);
    updateField(f.uid, { marks: layoutMarks(f.rect, vs) });
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
      <p className="note">ツールを選択し、帳票上をドラッグしてください。</p>
      <p className="note">ホイール: 拡大縮小 ／ Space・Alt・中ボタン＋ドラッグ: 画面移動<br />
        Ctrl+0: 全体表示 ／ Ctrl+1: 原寸 ／ Ctrl+「+」「-」: 拡大縮小<br />
        矢印キー: 選択した枠を1px移動（Shift で10px）／ Delete: 削除<br />
        Ctrl+Z: 元に戻す ／ Ctrl+Y: やり直し ／ Esc: 選択解除<br />
        Ctrl+クリック: 重なった枠を1枚ずつ下へ選択</p>
      <p className="note">枠を重ねて置くと、下の文字欄は自動で切り抜かれて
        L字になります（Ctrl+Z で戻せます）</p></div>;
    if (sel.type === "field") {
      const f = fields.find((x) => x.uid === sel.uid);
      if (!f) return null;
      return (
        <div className="panel">
          <h3>選択中の欄</h3>
          <p className="note">この枠の読み取り結果は CSV・Excel の
            「{f.field_id || "（名前未設定）"}」列へ出力されます</p>
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
          {f.kind === "text" && (
            <>
              <h4>領域（1つの欄を複数の枠で構成）</h4>
              <p className="note">L字・コの字の欄を作れます。どの領域の文字も
                この欄の値として読み順でつながります{f.extras?.length
                  ? `（現在 ${f.extras.length + 1} 領域）` : ""}</p>
              <button onClick={() => {
                setExTarget(f.uid);
                setMsg("追加する領域を帳票上でドラッグして描いてください（Esc で中止）");
              }}>領域を追加</button>
              <button onClick={() => {
                setMergeTarget(f.uid);
                setMsg("結合する欄をクリックしてください（クリックした欄はこの欄に取り込まれます・Esc で中止）");
              }}>別の欄と結合</button>
              {sel.part?.startsWith("extra:") && (
                <p className="note">選択中の領域は Delete で個別に削除できます</p>
              )}
              <h4>参照先（この枠が空のとき読む場所）</h4>
              {f.fallback ? (
                <>
                  <p className="note">主の枠に文字が1つも無いときだけ、
                    参照先の読取値を使います。読めない（〓）ときは参照しません</p>
                  <div className="mono">x:{f.fallback.x} y:{f.fallback.y} w:
                    {f.fallback.w} h:{f.fallback.h}</div>
                  <button onClick={() => {
                    setFields((fs) => fs.map((v) => v.uid === f.uid
                      ? { ...v, fallback: undefined } : v));
                    setSel({ type: "field", uid: f.uid });
                    markDirty(true);
                  }}>参照先を削除</button>
                </>
              ) : (
                <button onClick={() => {
                  setFbTarget(f.uid);
                  setMsg("参照先の枠を帳票上でドラッグして描いてください（Esc で中止）");
                }}>参照先の枠を描く</button>
              )}
            </>
          )}
          {/* 「参照先を削除」と並ぶため、対象を明示する（誤クリック防止） */}
          <button onClick={() => {
            // 参照先を選択中でも「この欄を削除」は欄ごと消す（部位に依らない）
            setFields((fs) => fs.filter((v) => v.uid !== f.uid));
            setSel(null); markDirty(true); }}>この欄を削除</button>
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
        <h3>選択中のくり返し行（表）</h3>
        <p className="note">各行×各列が CSV・Excel の
          「{t.table_id}_行番号_列名」列（例: {t.table_id}_01_
          {t.columns[0]?.name || "列名"}）へ1行ずつ出力されます</p>
        <label>表の名前 <input value={t.table_id}
          onChange={(e) => updateTable(t.uid, { table_id: e.target.value })} /></label>
        {/* 整数で保持する（レビュー M-22: 描画は float・保存時に round だと
            プレビューと実際の行位置が最大 0.5×(行数-1) px ずれていた。
            スキーマも integer なので入力時点で丸める） */}
        <label>行ピッチ <input type="number" step={1} value={t.row_pitch}
          onChange={(e) => updateTable(t.uid,
            { row_pitch: Math.max(1, Math.round(+e.target.value)) })} /></label>
        <label>行の高さ <input type="number" step={1} value={t.row_height}
          onChange={(e) => updateTable(t.uid,
            { row_height: Math.max(1, Math.round(+e.target.value)) })} /></label>
        {t.blocks.map((b, i) => (
          <label key={i}>ブロック{i + 1} 行数 <input type="number" value={b.rows}
            onChange={(e) => updateTable(t.uid, { blocks: t.blocks.map((v, j) =>
              j === i ? { ...v, rows: +e.target.value } : v) })} /></label>))}
        <button onClick={() => updateTable(t.uid, { blocks: [...t.blocks,
          { ...t.blocks[t.blocks.length - 1],
            x: t.blocks[t.blocks.length - 1].x + 1020 }] })}>右ブロックを追加（複製）</button>
        <h4>列</h4>
        {t.columns.length === 0 &&
          <p className="note">列がありません。「くり返し行（家族・明細）」で外枠を描くと生成されます。</p>}
        {t.columns.map((c, i) => (
          <div className="colrow" key={i}
            onMouseEnter={() => setHlCol(i)} onMouseLeave={() => setHlCol(null)}
            onFocus={() => setHlCol(i)} onBlur={() => setHlCol(null)}>
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
            {{ select: "選択", field: "欄を追加", excl: "除外範囲",
               table: "くり返し行（家族・明細）", split: "表裏の境界" }[t]}
          </button>))}
        <span className="msg">{msg}{dirtyState ? "（未保存）" : ""}</span>
      </div>
      {errMsg && <div className="errbox" style={{ margin: "8px 18px" }}>{errMsg}</div>}
      <div className="editor-body">
        <canvas ref={canvasRef} className="canvas"
          style={spaceHeld ? { cursor: "grab" }
                 : hoverCursor ? { cursor: hoverCursor } : undefined}
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
          onWheel={onWheel} onContextMenu={(e) => e.preventDefault()} />
        {panel()}
      </div>
    </div>
  );
}
