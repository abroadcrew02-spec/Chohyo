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
               extras?: Rect[];
               // 出力列に出すか（issue #66 出力列制御 MVP・FR-1.1）。省略/undefined
               // は true と同義（既存テンプレ互換・FR-1.7）。false のときだけ
               // buildTemplateJson が JSON に書く。枠・座標・読み取りには影響しない
               // （P3-a：resolveOverlaps の入力から外さない）
               output?: boolean };
type ColMark = { value: string; x_offset: number; width: number; y_offset?: number; height?: number };
type Column = { name: string; x_offset: number; width: number; kind: "text" | "choice";
                subfields: string; marks: ColMark[]; normalize?: string; output?: boolean };
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

// 切り抜き後の主 rect がこれを下回ると「文字が1つも入らない」（issue #59 H-4
// のエディタ側／設計書 04_unclear_policy.md §6 U-08。実応答の文字寸法 p10＝
// 幅27px・高さ36px が根拠。※実データでの較正は未実施と設計書に明記あり）
const MIN_CARVED_W = 27, MIN_CARVED_H = 36;

const _totalArea = (f: { rect: Rect; extras?: Rect[] }) =>
  f.rect.w * f.rect.h + (f.extras ?? []).reduce((s, r) => s + r.w * r.h, 0);

export type CarveVerdict =
  | { tier: "auto"; field: Field }
  | { tier: "warn"; field: Field; reductionPct: number }
  | { tier: "skip"; reason: string };

/// 欄1つを claim で切り抜いてよいかを3段階で判定する（issue #59 H-4 の
/// エディタ側／設計書 04_unclear_policy.md §6 U-08）。
/// 減少率 = 1 - 残余面積(主+extras) / 元の面積(主+extras)。
///   減少率 < 10%                          : auto（従来どおり自動で切り抜く）
///   10% ≤ 減少率 < 30%                     : warn（切り抜くが警告色で明示・D-7）
///   減少率 ≥ 30%、または切り抜き後の主 rect が 27×36px 未満: skip（切り抜かない）
/// 選択式・完全に覆われる欄（carveField が null を返す）も skip に含める。
/// エディタ＝置く前に止める（面積という静的な情報で判断できる）役割で、
/// 実行時に穴へ落ちた文字を捕まえるコア側（U-07）とは別の防御層。
///
/// splitY を渡すと、切り抜きで主 rect が表裏の面をまたいで移動する場合も
/// skip にする（issue #59 H-2）。carveField は最大断片を主 rect に据える
/// だけで面を意識しないため、splitY 直上の欄を切り抜くと残った下側の断片が
/// 主になり front→back へ黙って移動しうる。CSV の列は face_id の並び
/// （front→back）で決まるため、これは列位置がずれる変更で人の確認が要る。
export function evaluateCarve(
  f: Field, claim: Rect, splitY?: number, minSize = 6): CarveVerdict {
  if (f.kind !== "text")
    return { tier: "skip", reason: `${f.field_id}: 選択式のため自動調整できません` };
  const before = _totalArea(f);
  const next = carveField(f, claim, minSize);
  if (!next)
    return { tier: "skip", reason: `${f.field_id}: 完全に覆われるため自動調整できません` };
  if (splitY != null && (f.rect.y < splitY) !== (next.rect.y < splitY)) {
    return { tier: "skip",
      reason: `${f.field_id}: 切り抜くと表裏の面をまたぐため自動調整しません`
        + "（CSV の列位置が変わります）。枠の配置を見直してください" };
  }
  const after = _totalArea(next);
  const reductionPct = before > 0 ? Math.round((1 - after / before) * 100) : 0;
  const tooSmall = next.rect.w < MIN_CARVED_W || next.rect.h < MIN_CARVED_H;
  if (reductionPct >= 30 || tooSmall) {
    const why = tooSmall
      ? `切り抜き後の主枠が最小サイズ（${MIN_CARVED_W}×${MIN_CARVED_H}px）未満になる`
      : `面積が${reductionPct}%減る（30%以上）`;
    return { tier: "skip",
      reason: `${f.field_id}: 切り抜くと${why}ため自動調整しません。枠の配置を見直してください` };
  }
  return reductionPct >= 10
    ? { tier: "warn", field: next, reductionPct }
    : { tier: "auto", field: next };
}

/// 10%以上30%未満の切り抜き（issue #59 H-4・設計書 U-08）を警告色で明示する
/// ための文言。灰色の msg に混ぜると気づかれない（D-7）ため、専用チャンネル
/// （warnMsg・.warnbox）で表示する。対象が無ければ null
export function carveWarningNotice(
  items: { id: string; reductionPct: number }[]): string | null {
  if (!items.length) return null;
  const shown = items.slice(0, 3).map((i) => `「${i.id}」${i.reductionPct}%`).join("、");
  const more = items.length > 3 ? `、ほか ${items.length - 3} 件` : "";
  return "切り抜きで面積が10%以上30%未満減った欄があります: " + shown + more
    + "。意図した配置か確認してください。";
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
/// 切り抜きは evaluateCarve の3段階判定（issue #59 H-4・設計書 U-08）に従う。
/// 減少率30%未満は carved に、10%以上30%未満は warned にも追加で載る
/// （呼び出し側が警告色で表示する）。切り抜けないもの（選択式・完全に覆われた
/// 欄・減少率30%以上・切り抜き後が最小サイズ未満）は理由付きの文言で skipped
/// に返し、呼び出し側が赤帯で警告する（保存自体は続け、コア検証が最終判定する）。
export function resolveOverlaps(fields: Field[], splitY?: number): {
  fields: Field[]; carved: string[]; warned: { id: string; reductionPct: number }[];
  skipped: string[];
} {
  let fs = fields;
  const carved = new Set<string>();
  const warned = new Map<string, number>();
  const skipped: string[] = [];
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
        const verdict = evaluateCarve(f, rect, splitY);
        if (verdict.tier === "skip") { skipped.push(verdict.reason); return f; }
        carved.add(f.field_id);
        if (verdict.tier === "warn") warned.set(f.field_id, verdict.reductionPct);
        return verdict.field;
      });
    }
  }
  return { fields: fs, carved: [...carved],
           warned: [...warned].map(([id, reductionPct]) => ({ id, reductionPct })),
           skipped };
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

/// 保存成功時に「読み込み時点」と比べる基準値（issue #59 H-9・最後の検知網）。
/// 欄数・金額列数・除外数の増減を見せないと、静かに欄が減っても数字が
/// 変わるだけで気づけない（列数決め打ち廃止の代替として掲げた「拒否では
/// なく見える化」に、比較対象が無かった）。
export type CountSnapshot = { fields: number; amountCells: number; exclusions: number;
                              columns: number };

/// 読み込み時点との差分を表示用の文言にする（issue #59 H-9・issue #66 段3で
/// 列数も対象に追加）。増減が無い項目は現行どおり単一の数値で表示してよい
/// （呼び出し側の判断で省略しても破綻しない設計）。decreasedLabels は
/// 減少した項目名——呼び出し側が warnbox で強調するために使う
/// （灰色の msg に混ぜると気づかれない・D-7）。列の減少はここでは保存前の
/// 確認（saveConfirmWarnings・FR-1.6）が別途ブロックする対象なので、この
/// 関数自体は判定せず一様に差分を見せるだけに留める
export function saveDiffNote(loaded: CountSnapshot, current: CountSnapshot): {
  text: string; decreasedLabels: string[];
} {
  const decreasedLabels: string[] = [];
  const part = (label: string, from: number, to: number) => {
    if (from === to) return `${label} ${to}`;
    if (to < from) decreasedLabels.push(label);
    const diff = to - from;
    return `${label} ${from} → ${to}（${diff > 0 ? "+" : ""}${diff}）`;
  };
  const text = [
    part("欄", loaded.fields, current.fields),
    part("金額", loaded.amountCells, current.amountCells),
    part("除外", loaded.exclusions, current.exclusions),
    part("列", loaded.columns, current.columns),
  ].join("・");
  return { text, decreasedLabels };
}

/// promote（staged→本番パスへの確定）が失敗したときの表示文言（マリン最終
/// レビュー H-1）。verify は既に OK を返している段階なので「保存していません」
/// と言うと嘘になる——それどころか lib.rs の promote_staged は確定の rename
/// が失敗すると .bak からの巻き戻しを試みるため、本番パスの状態は「無傷」
/// 「巻き戻し済み」「巻き戻しにも失敗」のいずれかになりうる。その区別は
/// lib.rs 側のエラー文言（rustError）にすでに入っているので素通りする。
/// staged（<path>.saving.json）は promote_staged のどの失敗経路でも消えない
/// （rename は失敗時に元を消さない）ため、その在り処だけは常に案内できる。
export function promoteFailureNotice(path: string, rustError: string): string {
  return `保存を確定できませんでした（${rustError}）。編集内容は ${path}.saving.json `
    + "に残っています。保存をやり直すか、このファイルを手動で確認してください。";
}

/// 表の choice 列の marks（列に対する相対 x_offset/width）を、列の width が
/// 変わったときに比率で追従させる（issue #60 M-8・#48 の単発欄向け
/// remapMarks と同じ考え方）。x_offset（列の位置）は marks が列に対する
/// 相対値のため影響を受けない——width（列の幅）が変わったときだけ、
/// marks の相対配置が崩れないよう比率でスケールする。
export function remapColumnMarks(
  marks: ColMark[], oldWidth: number, newWidth: number): ColMark[] {
  if (marks.length === 0 || oldWidth === newWidth || oldWidth <= 0) return marks;
  const sx = newWidth / oldWidth;
  return marks.map((m) => ({
    ...m,
    x_offset: Math.round(m.x_offset * sx),
    width: Math.max(1, Math.round(m.width * sx)),
  }));
}

/// 添字が現在の extras 配列の範囲内かを確認する（issue #60 M-4）。
/// carve（evaluateCarve/carveField）は欄の extras を丸ごと再構築するため、
/// 選択中の part（"extra:<n>"）が古いままだと存在しない/別の領域を指しうる。
/// 添字が無効なまま applySelRect/nudge/removeSel を動かすと、
/// 「何も変わっていないのに markDirty だけ立つ」（無効なら何も変えない）
/// または「別の領域が動く/消える」（有効に見えても中身が別物）という
/// 2種類の事故になる——後者は carve 直後に選択をクリアする対策
/// （Editor コンポーネント側）と合わせて防ぐ。
export function extraIndexValid(
  f: { extras?: Rect[] } | undefined, i: number): boolean {
  return !!f && Number.isInteger(i) && i >= 0 && i < (f.extras?.length ?? 0);
}

// ============================================================
// 出力列制御 MVP・第1弾（issue #66 段3）: 欄単位の「出力しない」
// ============================================================

/// output が false のときだけ「出力しない」（P3-b）。省略/undefined は
/// 出力する（既存テンプレ互換・FR-1.7）。
export const isOutput = (item: { output?: boolean }): boolean => item.output !== false;

/// buildTemplateJson が output 属性を JSON へどう書くかの規則（FR-1.1 B-確定・
/// B-S4）。false のときだけ書く（省略時 true＝出力する）——無関係な保存で
/// template_hash を動かさないため。Field・Column の両シリアライズが同じ
/// 規則を1箇所から呼ぶことで、書き方が2箇所で食い違う事故を防ぐ。
export function outputAttrForJson(output: boolean | undefined): { output: false } | object {
  return output === false ? { output: false } : {};
}

/// 現在「出力しない」に設定されている欄・表の列の総数を数える（FR-1.8 の
/// タブ見出しバッジ用）。列番号の再導出ではなく単なるフラグの集計なので
/// FR-0.1 の「GUI側での列名・列順の再導出禁止」には抵触しない。
export function countOutputDisabled(
  fields: { output?: boolean }[],
  tables: { columns: { output?: boolean }[] }[]): number {
  const fromFields = fields.filter((f) => !isOutput(f)).length;
  const fromTables = tables.reduce((s, t) => s + t.columns.filter((c) => !isOutput(c)).length, 0);
  return fromFields + fromTables;
}

/// 単発欄の field_id が verify の column_names（FR-0.1）の中でどの位置に
/// あるかを探す。列名は "<field_id>" または "<field_id>_<subfield>" の形
/// （F-2）なので、完全一致または "field_id_" 接頭一致で拾う。subfields で
/// 複数列に分かれる欄は最初と最後の位置を返す。見つからなければ null
/// （output:false・columnNames 未取得・該当なしのいずれか）。
/// **列の並び自体は再導出しない**——column_names に実際にある位置を
/// そのまま読むだけ（FR-0.1・T-M11 の趣旨を維持）。
export function findColumnPositions(
  columnNames: string[] | null, fieldId: string): { first: number; last: number } | null {
  if (!columnNames) return null;
  const idxs: number[] = [];
  columnNames.forEach((name, i) => {
    if (name === fieldId || name.startsWith(fieldId + "_")) idxs.push(i + 1);
  });
  return idxs.length ? { first: idxs[0], last: idxs[idxs.length - 1] } : null;
}

/// 表の列（table_id・column名）が column_names のどの位置にあるかを探す。
/// 表の列名は行展開により "<table_id>_<行番号>_<列名>"（例: family_01_生年月日）
/// の形で複数回登場する（F-1）。findColumnPositions と同じく実在する位置を
/// 読むだけで、行展開そのものを GUI 側で組み立て直しはしない。
export function findTableColumnPositions(
  columnNames: string[] | null, tableId: string, columnName: string):
  { first: number; last: number } | null {
  if (!columnNames) return null;
  const prefix = `${tableId}_`;
  const idxs: number[] = [];
  columnNames.forEach((name, i) => {
    if (!name.startsWith(prefix)) return;
    const rest = name.slice(prefix.length);
    // "<行番号>_<列名>..." の行番号部分を飛ばして列名と突き合わせる
    const m = /^\d+_(.*)$/.exec(rest);
    const tail = m ? m[1] : rest;
    if (tail === columnName || tail.startsWith(columnName + "_")) idxs.push(i + 1);
  });
  return idxs.length ? { first: idxs[0], last: idxs[idxs.length - 1] } : null;
}

/// 「出力する」チェックボックスの accessible name（AC-1.21・AC-1.25）。
/// 欄の識別子を必ず含み（AC-1.21・SR のフォームコントロール一覧で行が
/// 重複しないようにする）、チェック操作の結果（現在の状態）を動的に含める
/// （AC-1.25・SC 4.1.3）。output:false は常に「出力対象外」（ローカルに
/// 確定できる）。output:true は column_names 上の位置が分かれば列番号を、
/// 分からなければ（未読込・編集直後で verify 未反映など）素直に
/// 「出力する」とだけ言い、誤った列番号を言わない。
export function outputCheckboxLabel(
  displayName: string, output: boolean,
  position: { first: number; last: number } | null): string {
  if (!output) return `${displayName}を出力する（現在: 出力対象外）`;
  if (!position) return `${displayName}を出力する（現在: 出力する）`;
  const posText = position.first === position.last
    ? `${position.first}列目` : `${position.first}〜${position.last}列目`;
  return `${displayName}を出力する（現在: ${posText}）`;
}

/// 単発欄パネルの説明文に添える列位置の注記（issue #66 段5・FR-2.3・AC-2.7前半）。
/// position・totalColumns のどちらか一方でも欠けたら null（column_names 未取得・
/// 不整合時は番号を出さない——段3 の安全側判断を踏襲）。output:false の場合の
/// 表示は呼び出し側の既存文言（「ただし今は出力しない設定です」）に委ねるため、
/// ここでは output は受け取らない（重複した「出力対象外」表記を避ける）。
export function fieldColumnPositionNote(
  position: { first: number; last: number } | null,
  totalColumns: number | null): string | null {
  if (!position || totalColumns == null) return null;
  const posText = position.first === position.last
    ? `左から${position.first}列目` : `左から${position.first}〜${position.last}列目`;
  return `${posText} / 全${totalColumns}列`;
}

export type TableColumnRangeInfo = {
  first: number; last: number; count: number;
  exampleName: string; examplePosition: number;
};
/// 表全体が CSV・Excel の何列目〜何列目を占めるかを column_names から実引きする
/// （issue #66 段5・FR-2.3）。表の列は行展開で複数回登場するため単一の数字は
/// 作れず範囲表記にする。例文（family_01_氏名 = 22列目 のような1件）も
/// column_names に実在するエントリからそのまま引く（GUI 側での組み立て直し禁止・
/// FR-0.1）。該当エントリが1つも無ければ null（column_names 未取得時など）。
export function tableColumnRangeInfo(
  columnNames: string[] | null, tableId: string): TableColumnRangeInfo | null {
  if (!columnNames) return null;
  const prefix = `${tableId}_`;
  const idxs: number[] = [];
  columnNames.forEach((name, i) => { if (name.startsWith(prefix)) idxs.push(i); });
  if (!idxs.length) return null;
  return {
    first: idxs[0] + 1, last: idxs[idxs.length - 1] + 1, count: idxs.length,
    exampleName: columnNames[idxs[0]], examplePosition: idxs[0] + 1,
  };
}

/// 表の内部列（.colrow）に薄く併記する注記（issue #66 段5・付録A）。
/// 「表の中で n 番目」は列の定義順（配列インデックス）、「帳票では左から n 番目」は
/// x_offset 順——列を後から追加すると定義順と見た目の左右順がずれるため、
/// 両方を示す。CSV・Excel の列番号（column_names 由来）とは別の、表単体で
/// ローカルに求まる情報なので column_names は使わない。output:false の列は
/// 番号を出さず「出力対象外」（段3実装と整合）。
export function tableColumnOrderNote(
  columns: { x_offset: number }[], index: number, output: boolean): string | null {
  if (!output) return "出力対象外";
  if (index < 0 || index >= columns.length) return null;
  const order = columns.map((_, i) => i)
    .sort((a, b) => columns[a].x_offset - columns[b].x_offset);
  const rank = order.indexOf(index) + 1;
  return `表の中で${index + 1}番目・帳票では左から${rank}番目`;
}

/// 出力列の並び（issue #66 段6・FR-2.2・AC-2.4）を読み込み時／直近保存時の基準と
/// 比べるためのスナップショット。field_id ではなく uid・列名で比較する——field_id
/// や列名は編集で変わりうるが uid は不変（列名は同一テーブル内での重複が
/// 想定しづらいため、そのテーブルの列順の識別にそのまま使う）
export type OutputOrderSnapshot = {
  fieldUids: string[]; tableUids: string[];
  tableColumns: { uid: string; names: string[] }[];
};
export function outputOrderSnapshot(fields: Field[], tables: Table[]): OutputOrderSnapshot {
  return {
    fieldUids: fields.map((f) => f.uid),
    tableUids: tables.map((t) => t.uid),
    tableColumns: tables.map((t) => ({ uid: t.uid, names: t.columns.map((c) => c.name) })),
  };
}
const _sameOrder = (a: string[], b: string[]): boolean =>
  a.length === b.length && a.every((v, i) => v === b[i]);
/// 読み込み時／直近保存時（loaded）から現在（current）までに、出力へ影響する
/// 並び（単発欄の配列順・表の配列順・各表内の列の配列順）が変わったかどうか。
/// 基準が無ければ（初回未読込等）false 側に倒す——並べ替えガードを不必要に
/// 発火させない（保存自体は従来どおり進められる）
export function outputOrderChanged(
  loaded: OutputOrderSnapshot | null, current: OutputOrderSnapshot): boolean {
  if (!loaded) return false;
  if (!_sameOrder(loaded.fieldUids, current.fieldUids)) return true;
  if (!_sameOrder(loaded.tableUids, current.tableUids)) return true;
  if (loaded.tableColumns.length !== current.tableColumns.length) return true;
  const curByUid = new Map(current.tableColumns.map((t) => [t.uid, t.names]));
  for (const lt of loaded.tableColumns) {
    const cur = curByUid.get(lt.uid);
    if (!cur || !_sameOrder(lt.names, cur)) return true;
  }
  return false;
}

/// 単発欄の rect / fallback / extras の座標不変ガード（issue #66 段6・FR-2.2・
/// AC-2.4）に使うジオメトリのスナップショット。resolveOverlaps 前後で撮り、
/// geometryUnchanged で突き合わせる
export type FieldGeometrySnapshot = {
  uid: string; rect: Rect; fallback: Rect | null; extras: Rect[];
};
export function fieldGeometrySnapshot(fields: Field[]): FieldGeometrySnapshot[] {
  return fields.map((f) => ({
    uid: f.uid, rect: f.rect, fallback: f.fallback ?? null, extras: f.extras ?? [],
  }));
}
const _rectEq = (a: Rect, b: Rect): boolean =>
  a.x === b.x && a.y === b.y && a.w === b.w && a.h === b.h;
/// 並べ替えを含む保存では resolveOverlaps による自動調整（切り抜き）を許さない
/// （付録A・U-2）。before/after で1px でもずれた欄があれば false——呼び出し側は
/// 保存を中止する。順序が変わっていない保存では呼ばない（このガードの対象外・
/// 従来どおり切り抜きを許す）
export function geometryUnchanged(
  before: FieldGeometrySnapshot[], after: FieldGeometrySnapshot[]): boolean {
  if (before.length !== after.length) return false;
  const byUid = new Map(after.map((a) => [a.uid, a]));
  for (const b of before) {
    const a = byUid.get(b.uid);
    if (!a) return false;
    if (!_rectEq(b.rect, a.rect)) return false;
    if ((b.fallback === null) !== (a.fallback === null)) return false;
    if (b.fallback && a.fallback && !_rectEq(b.fallback, a.fallback)) return false;
    if (b.extras.length !== a.extras.length) return false;
    for (let i = 0; i < b.extras.length; i++) {
      if (!_rectEq(b.extras[i], a.extras[i])) return false;
    }
  }
  return true;
}

/// 並べ替えを含む保存で自動調整（切り抜き）が起きたときに保存を中止する理由文
/// （issue #66 段6・付録A）。「保存していません」系の文言（errbox）と統一する
export function reorderCarveBlockedNotice(): string {
  return "保存していません: 並べ替えを含む保存では枠の自動調整は行えません。"
    + "先に重なりを解消してから並べ替えてください。";
}

/// 保存後の順序変化報告（issue #66 段7・FR-2.6・AC-2.10）。並べ替えを含む保存の
/// 成功サマリにだけ添える1行——「順序が変わった」ことと「欄数自体は変わって
/// いない」ことをセットで伝える。件数の食い違い（増減）は saveDiffNote が
/// 既に検知・報告しているので、ここでは並べ替えの有無だけを言う。warnbox
/// （確認を求める・K-M3）には出さない——呼び出し側は setMsg 側にだけ足すこと
export function orderChangeReportNote(
  orderChangedThisSave: boolean, fieldCount: number): string | null {
  return orderChangedThisSave ? `列順を変更（欄 ${fieldCount} は増減なし）` : null;
}

/// ある面（表面/裏面）に属する単発欄だけを、配列順を保ったまま取り出す
/// （issue #66 段7・AC-2.1）。buildTemplateJson の face() 内の inFace 判定と
/// 同じ述語——ここで抽出することで「並べ替え後に buildTemplateJson が書く
/// 配列順が実際に変わる」ことをテストできる
// fieldsForFace と outOfFaceElements で共有する面range（L-Q1: 両者の面判定が
// ズレると「fieldsForFace には入るのに outOfFaceElements は範囲外と言う」
// という矛盾が起きうる。述語を1箇所に集約して同じ計算を使わせることで防ぐ）
function faceRangeContains(y: number, faceId: "front" | "back",
  splitY: number, imgH: number): boolean {
  const [y0, y1] = faceId === "front" ? [0, splitY] : [splitY, imgH];
  return y >= y0 && y < y1;
}

export function fieldsForFace(
  fields: Field[], faceId: "front" | "back", splitY: number, imgH: number): Field[] {
  return fields.filter((f) => faceRangeContains(f.rect.y, faceId, splitY, imgH));
}

/// 矩形をページ全体 [0,W)×[0,H) の中へ収める（issue #69 Q-H2）。画像を
/// 開き直すと過去の座標がキャンバス外へはみ出すことがあり、そのまま保存すると
/// buildTemplateJson の面 filter に無言で落ちる（欄が保存のたびに勝手に
/// 消えるのに保存自体は「成功」と表示される）。移動・リサイズ・新規作成の
/// 出口（applySelRect・nudge・norm・onMove の moveTable）に集約して置くことで、
/// はみ出しは「その場で見えて直る」形にする——保存時点で初めて気づく事故を防ぐ
export function clampRect(r: Rect, W: number, H: number): Rect {
  const w = Math.min(Math.max(r.w, 0), Math.max(W, 0));
  const h = Math.min(Math.max(r.h, 0), Math.max(H, 0));
  const x = Math.min(Math.max(r.x, 0), Math.max(W - w, 0));
  const y = Math.min(Math.max(r.y, 0), Math.max(H - h, 0));
  return { x, y, w, h };
}

/// 面（表面/裏面）のどちらにも入らない要素の id を集める（issue #69 Q-H2）。
/// buildTemplateJson は表面/裏面それぞれの inFace で fields/tables/excls を
/// フィルタするため、どちらの面にも入らない要素は最終 JSON から**無言で**
/// 消える。判定は fieldsForFace と同じ面range を使い、述語のズレを起こさない
/// （L-Q1: fieldsForFace の面判定と一致させる）。保存直前（saveTemplateInner）
/// で呼び、非空なら保存を止めて id を列挙する——保存前確認モーダルには乗せない
/// （「承知の上で続行」に安全な続行が存在しないため・reorderCarveBlockedNotice
/// と同型）
export function outOfFaceElements(input: {
  fields: Field[]; tables: Table[]; excls: Excl[]; splitY: number; H: number;
}): string[] {
  const { fields, tables, excls, splitY, H } = input;
  const inAnyFace = (y: number) =>
    faceRangeContains(y, "front", splitY, H) || faceRangeContains(y, "back", splitY, H);
  const out: string[] = [];
  for (const f of fields) if (!inAnyFace(f.rect.y)) out.push(f.field_id);
  for (const t of tables) if (t.blocks[0] && !inAnyFace(t.blocks[0].y)) out.push(t.table_id);
  for (const e of excls) if (!inAnyFace(e.rect.y)) out.push(e.id);
  return out;
}

/// 不一致（mismatch）と判定された面を集める（issue #71 (a')・FR-F04・
/// 設計08 §2.7.3）。faces は expand-page の応答（面ごとの verdict）。
/// override 中（「それでもこのテンプレートで開く」・FR-F05）は空集合を返し
/// 全枠を可視化する。faces 未提供（旧コア・verdict 無し応答）も空集合——
/// 現行どおり全て描く（後方互換）。
export function hiddenFaces(
  faces: { face_id: string; verdict: string }[] | undefined,
  override: boolean,
): Set<string> {
  if (override || !faces) return new Set();
  return new Set(faces.filter((f) => f.verdict === "mismatch").map((f) => f.face_id));
}

function faceIdOf(y: number, splitY: number, imgH: number): "front" | "back" {
  return faceRangeContains(y, "front", splitY, imgH) ? "front" : "back";
}

/// 描画（draw）とヒットテスト（onDown の hitAll）の両方が見る唯一の可視集合
/// （L-Q1 の教訓と同型: 述語を2つ持つと「描かれているのに掴めない／描かれて
/// いないのに掴める」というズレが起きる）。hidden が空なら配列をそのまま
/// 返す（旧コア・一致・上書き中の経路で余計な再割当をしない）。
export function visibleFields(
  fields: Field[], hidden: Set<string>, splitY: number, imgH: number): Field[] {
  if (hidden.size === 0) return fields;
  return fields.filter((f) => !hidden.has(faceIdOf(f.rect.y, splitY, imgH)));
}

export function visibleTables(
  tables: Table[], hidden: Set<string>, splitY: number, imgH: number): Table[] {
  if (hidden.size === 0) return tables;
  return tables.filter((t) =>
    !t.blocks[0] || !hidden.has(faceIdOf(t.blocks[0].y, splitY, imgH)));
}

export function visibleExcls(
  excls: Excl[], hidden: Set<string>, splitY: number, imgH: number): Excl[] {
  if (hidden.size === 0) return excls;
  return excls.filter((e) => !hidden.has(faceIdOf(e.rect.y, splitY, imgH)));
}

/// sel が指す要素が、様式不一致で隠れている面（hidden）に属していないかを
/// 判定する（issue #71 (a')・スバル差し戻し1）。canvas 側は hitAll が
/// visibleFields 等でフィルタ済みなので隠れた要素を選べないが、出力列タブの
/// 一覧（outputListPanel）は独立した選択経路を持っていたため、そこで選んだ
/// sel が漏れて nudge／削除に渡っていた。**ガードをこの1関数に集約**し、
/// 出力列タブ側の選択不可表示（fieldRow/tableRow の disabled）と、
/// nudge／removeSel／「この欄を削除」の入口の両方から同じ判定を通す。
export function selHiddenByFormat(
  sel: { type: "field" | "table" | "excl"; uid: string } | null,
  fields: Field[], tables: Table[], excls: Excl[],
  hidden: Set<string>, splitY: number, imgH: number,
): boolean {
  if (!sel || hidden.size === 0) return false;
  if (sel.type === "field") {
    const f = fields.find((v) => v.uid === sel.uid);
    return !!f && hidden.has(faceIdOf(f.rect.y, splitY, imgH));
  }
  if (sel.type === "table") {
    const t = tables.find((v) => v.uid === sel.uid);
    return !!t && !!t.blocks[0] && hidden.has(faceIdOf(t.blocks[0].y, splitY, imgH));
  }
  if (sel.type === "excl") {
    const e = excls.find((v) => v.uid === sel.uid);
    return !!e && hidden.has(faceIdOf(e.rect.y, splitY, imgH));
  }
  return false;
}

/// buildTemplate の純粋版（issue #69 Q-H2）。画面の state（フック）に依存
/// しないため node で直接テストできる——1,883行側で唯一テストできなかった
/// 最重要の直列化ロジックを可視化する。保存直前に outOfFaceElements が
/// 範囲外要素を検知して保存自体を止めるため、ここでの面 filter は通常は
/// 何も落とさない前提だが、想定外の不整合を握り潰さないよう「落とした件数」を
/// droppedCount として返す（呼び出し側が無視できない形の二重防御）
export function buildTemplateJson(input: {
  fields: Field[]; tables: Table[]; excls: Excl[]; splitY: number;
  W: number; H: number;
  meta: { template_id: string; render_dpi: number;
          image: { width: number; height: number } | null;
          record: Record<string, unknown> };
}): { template: unknown; droppedCount: number } {
  const { fields, tables, excls, splitY, W, H, meta } = input;
  const face = (id: "front" | "back") => {
    const [y0, y1] = id === "front" ? [0, splitY] : [splitY, H];
    const inFace = (y: number) => y >= y0 && y < y1;
    const facedFields = fieldsForFace(fields, id, splitY, H);
    const facedExcls = excls.filter((e) => inFace(e.rect.y));
    const facedTables = tables.filter((t) => t.blocks[0] && inFace(t.blocks[0].y));
    return {
      face_id: id,
      source: { page_offset: 0, rect: { x: 0, y: y0, w: W, h: y1 - y0 } },
      exclusions: facedExcls.map((e) => ({
        id: e.id, rect: { ...e.rect, y: e.rect.y - y0 } })),
      // 配列順をそのまま書く（既存挙動・変更なし）。抽出先は fieldsForFace
      // （issue #66 段7・AC-2.1・付録A・fieldList.filter(inFace) と同じ述語）
      fields: facedFields.map((f) => ({
        field_id: f.field_id, kind: f.kind,
        rect: { ...f.rect, y: f.rect.y - y0 },
        ...(f.kind === "text" && f.fallback
          ? { fallback_rect: { ...f.fallback, y: f.fallback.y - y0 } } : {}),
        ...(f.kind === "text" && f.extras?.length
          ? { extra_rects: f.extras.map((r) => ({ ...r, y: r.y - y0 })) } : {}),
        ...(f.normalize && f.kind === "text" ? { normalize: f.normalize } : {}),
        ...(f.kind === "choice" ? { choice_marks: f.marks.map((m) => ({
          value: m.value, rect: { ...m.rect, y: m.rect.y - y0 } })) } : {}),
        // false のときだけ書く（省略時 true・FR-1.1 B-確定）。無関係な保存で
        // template_hash を動かさない（B-S4）
        ...outputAttrForJson(f.output) })),
      tables: facedTables.map((t) => ({
        table_id: t.table_id, row_pitch: t.row_pitch,
        row_height: t.row_height,
        blocks: t.blocks.map((b) => ({ origin: { x: b.x, y: b.y - y0 }, rows: b.rows })),
        columns: t.columns.map((c) => ({
          name: c.name, x_offset: c.x_offset, width: c.width, kind: c.kind,
          ...(c.subfields.trim()
            ? { subfields: c.subfields.split(",").map((s) => s.trim()).filter(Boolean) } : {}),
          ...(c.normalize && c.kind === "text" && !c.subfields.trim()
            ? { normalize: c.normalize } : {}),
          ...(c.kind === "choice" ? { choice_marks: c.marks } : {}),
          ...outputAttrForJson(c.output) })) })),
    };
  };
  const front = face("front");
  const back = face("back");
  const droppedCount =
    (fields.length - (front.fields.length + back.fields.length))
    + (excls.length - (front.exclusions.length + back.exclusions.length))
    + (tables.length - (front.tables.length + back.tables.length));
  return {
    template: {
      schema_version: 1, template_id: meta.template_id,
      render_dpi: meta.render_dpi,
      image: { width: W, height: H }, record: meta.record,
      faces: [front, back],
    },
    droppedCount,
  };
}

/// 出力列タブでの単発欄の並べ替え（issue #66 段7・FR-2.1・AC-2.1・AC-2.2・
/// 付録A）。移動対象と同じ面（表面/裏面）内で、配列順でいちばん近い同面の欄と
/// 入れ替える——面をまたぐ隣接は探さない（面またぎ移動は UI に存在しない構造で
/// 担保する・AC-2.2）。面の先頭/末尾（3閉区間の境界）では隣が無いので null。
/// 呼び出し側はこれで [↑][↓] を disabled にする（C-2）
export function moveFieldOutputOrder(
  fields: Field[], uid: string, dir: "up" | "down", splitY: number): Field[] | null {
  const face = (f: Field): "front" | "back" => (f.rect.y < splitY ? "front" : "back");
  const i = fields.findIndex((f) => f.uid === uid);
  if (i === -1) return null;
  const sameFace = face(fields[i]);
  const step = dir === "up" ? -1 : 1;
  let j = i + step;
  while (j >= 0 && j < fields.length && face(fields[j]) !== sameFace) j += step;
  if (j < 0 || j >= fields.length) return null;
  const next = fields.slice();
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/// 表の内部列（.colrow）の並べ替え（issue #66 段7・FR-2.1・AC-2.3）。220列中
/// 200列を動かす本体——同じ表の中で隣接する列と入れ替えるだけの単純な操作
export function moveTableColumnOrder(
  columns: Column[], index: number, dir: "up" | "down"): Column[] | null {
  const j = dir === "up" ? index - 1 : index + 1;
  if (index < 0 || index >= columns.length || j < 0 || j >= columns.length) return null;
  const next = columns.slice();
  [next[index], next[j]] = [next[j], next[index]];
  return next;
}

/// 表内列の並べ替えが影響する範囲の事前1行（issue #66 段7・付録A）。行数は
/// blocks から即座に求まるローカルな情報（常に分かる）。列数（行展開後の
/// CSV 列数）は column_names から実引きした tableColumnRangeInfo.count を渡す
/// ——未取得のときは行数だけの文言に落とす（誤った列数を言わない・FR-0.1）
export function tableColumnReorderImpactNote(
  totalRows: number, totalColumns: number | null): string {
  return totalColumns == null
    ? `この変更は ${totalRows} 行分の並びに影響します`
    : `この変更は ${totalRows} 行分・${totalColumns} 列の並びに影響します`;
}

/// 保存前確認の列数比較（issue #65-1・M2）。loadedCounts.columns はコンポーネント
/// マウント直後の初期値が 0（useState 宣言部）で、verify 応答の取得に失敗した
/// 経路（refreshLoadedCounts 内の catch・invoke 失敗・自動読込の失敗で
/// refreshLoadedCounts 自体が呼ばれない経路）でもそのまま残る。baseline<=0の
/// まま `tpl.columns < loadedCounts.columns` のような直接比較をすると、0は
/// どんな列数より必ず小さいため、列が実際に減っても警告が出ない（fail-open）。
/// validate_v1 は抽出列0を拒否したうえで管理6列を必ず加えるため、verify成功時
/// の列数は必ず7以上——0はこの未取得の初期値でしか現れないsentinelとして
/// 扱える。baseline・current のどちらが数値でない場合（verify応答の欠落・
/// 旧コア・破損応答）も比較できないため同じ unknown 扱いにする——baseline側
/// だけ緩いガードにすると、baseline が非数値のときに `<= 0` 判定が false
/// （`undefined <= 0`・`NaN <= 0` はどちらも false）をすり抜けて null（＝
/// 減っていない）を返してしまい、今回塞いだのと同型の fail-open が片側に
/// 残る（issue #65-1 レビュー指摘 M-1）。
export type ColumnDecreaseCheck =
  | { kind: "decrease"; from: number; to: number }
  | { kind: "unknown" }
  | null;
export function columnDecreaseFor(baseline: unknown, current: unknown): ColumnDecreaseCheck {
  if (typeof baseline !== "number" || !Number.isFinite(baseline) || baseline <= 0) {
    return { kind: "unknown" };
  }
  if (typeof current !== "number" || !Number.isFinite(current)) return { kind: "unknown" };
  if (current < baseline) return { kind: "decrease", from: baseline, to: current };
  return null;
}

/// 保存前確認モーダルに出す⚠一覧を組み立てる（FR-1.6・付録A）。純粋な判定
/// のみ——実際の確認 UI（モーダル）の表示は呼び出し側（saveTemplate）が
/// 行う。4件のいずれも該当しなければ空配列を返し、呼び出し側はモーダルを
/// 出さずに保存を続ける（C-5 の empty 状態）。
export type SaveWarning = { key: string; text: string };
export function saveConfirmWarnings(input: {
  isShipped: boolean;
  imageSizeMismatch: { from: string; to: string } | null;
  exclusionNotice: string | null;
  columnDecrease: ColumnDecreaseCheck;
}): SaveWarning[] {
  const warnings: SaveWarning[] = [];
  if (input.isShipped) {
    warnings.push({ key: "shipped",
      text: "出荷テンプレートを上書きします。読み取りに直ちに影響します。" });
  }
  if (input.imageSizeMismatch) {
    warnings.push({ key: "image-size",
      text: "開いている画像の寸法がテンプレートと異なります（"
        + `${input.imageSizeMismatch.from} → ${input.imageSizeMismatch.to}）。`
        + "保存すると全ページが再送信（課金）の対象になります。" });
  }
  if (input.exclusionNotice) {
    warnings.push({ key: "exclusion", text: input.exclusionNotice });
  }
  if (input.columnDecrease?.kind === "decrease") {
    warnings.push({ key: "columns",
      text: `出力列が ${input.columnDecrease.from} → ${input.columnDecrease.to} 列に減ります。`
        + "csv を取り込むシステムがある場合は、列構成の変更を先方と合わせてから"
        + "保存してください（README §7）。枠と読み取りは残ります（あとで戻せます）。" });
  } else if (input.columnDecrease?.kind === "unknown") {
    // unknown は「読み込み時基準が未取得」（穴A）・「今回の列数を取得できな
    // かった」（穴B）のどちらでも立つ。原因を一方に決め打ちしない
    // （issue #65-1 レビュー指摘 S-1）
    warnings.push({ key: "columns",
      text: "列数を比較できません（列数を取得できませんでした）。"
        + "保存後の verify で列構成を確認してください。" });
  }
  return warnings;
}

/// 保存サマリの要確認セル数・母集団縮小の注記（AC-1.16・T-S8）。
/// 「要確認セル数の母集団: 214列 → 211列（出力しない 3 欄を除く）」の形式。
/// 抽出列数（列数から管理6列を除いた数）が減っていない、または対象外が
/// 0件のときは null（呼び出し側は何も表示しない）。
export function unclearPopulationNote(
  loadedExtractColumns: number, currentExtractColumns: number, disabledCount: number): string | null {
  if (currentExtractColumns >= loadedExtractColumns || disabledCount <= 0) return null;
  return `要確認セル数の母集団: ${loadedExtractColumns}列 → ${currentExtractColumns}列`
    + `（出力しない ${disabledCount} 欄を除く）`;
}

/// expand-page が返す位置合わせ失敗の理由（ぺこら担当・core 側で追加中）。
/// 欠落時は旧コア互換で "align" 扱いにフォールバックする。
/// "size"（N-2）: PageSizeMismatch（Q-H1・寸法/向き不一致）。従来は
/// AlignError の基底クラス経由で "align" に潰れており、run では様式不一致で
/// 弾かれる紙に「枠は動かさないでください（自動補正される）」という誤った
/// 案内が出ていた。
export type ExpandAlignReason = "template" | "align" | "size" | "image" | "other";

/// expand-page が新たに返す様式判定の3値（issue #71 (a')・設計08 §2.3.1）。
/// 未対応の旧コアでは undefined——3値経路には入らず、reason ベースの
/// 従来分岐（旧コア互換）へフォールバックする。
export type ExpandVerdict = "match" | "mismatch" | "undecidable";

/// PDF/画像を開いた直後の位置合わせ・様式判定結果から、案内文言を出し分ける
/// （5巡目レビュー・いろは指摘＋issue #71 (a') で verdict/level を追加）。
/// 優先順は 07 FR-F07: template（テンプレ破損） > size（寸法不一致） >
/// mismatch（様式不一致） > undecidable（判定不能） > match（一致）。
/// 赤帯（isError・level="error"）は template と size のみ。mismatch は
/// 黄帯（level="warn"）で「それでもこのテンプレートで開く」（FR-F05）を
/// 案内する。undecidable は現行文言（自動補正されるため枠は動かさない）を
/// そのまま維持する——線が取れていないだけの紙で枠を消さないため（07 §9.1）。
/// verdict が渡されない（旧コア）場合は reason だけを見る従来分岐のまま。
export function expandAlignNotice(
  aligned: boolean, reason: ExpandAlignReason | undefined, pageNote: string,
  verdict?: ExpandVerdict, templateId?: string):
  { text: string; isError: boolean; level: "error" | "warn" | "info" } {
  const r = reason;
  if (r === "template") {
    return {
      text: `（${pageNote}テンプレートを読み込めないため位置合わせできませんでした。`
        + "テンプレートが壊れている可能性があります。編集を続ける前にテンプレートの検証"
        + "（保存して検証）を行ってください）",
      isError: true, level: "error",
    };
  }
  if (r === "size") {
    return {
      text: `（${pageNote}用紙サイズ／向きがテンプレートと合っていません`
        + "（縦横比の差が 1% 超）。この画像は読み取り時に様式不一致として扱われます）",
      isError: true, level: "error",
    };
  }
  if (verdict === "mismatch") {
    const id = templateId || "現在のテンプレート";
    return {
      text: `（${pageNote}この画像はテンプレート（${id}）と様式が合いません。`
        + "枠は表示していません。別のテンプレートを開くか、この紙のテンプレートを"
        + "新しく作ってください。それでもこのテンプレートのまま直す場合は、下の"
        + "「判定を無視して枠を表示する」ボタンを押してください）",
      isError: false, level: "warn",
    };
  }
  if (verdict === "undecidable") {
    return {
      text: `（${pageNote}位置合わせできませんでした。枠が少しズレて見えても、` +
        "読み取り時に自動補正されるため枠は動かさないでください）",
      isError: false, level: "info",
    };
  }
  if (verdict === "match" || aligned) {
    return { text: `（${pageNote}位置合わせ済み・枠が記入欄に重なって表示されます）`,
             isError: false, level: "info" };
  }
  // verdict 未提供（旧コア）: reason だけを見る従来分岐にフォールバックする
  const rr = r ?? "align";
  if (rr === "align") {
    return {
      text: `（${pageNote}位置合わせできませんでした。枠が少しズレて見えても、` +
        "読み取り時に自動補正されるため枠は動かさないでください）",
      isError: false, level: "info",
    };
  }
  // image / other: 原因を「テンプレのせい」とも「自動補正される」とも
  // 言い切らない中立文言（reason 拡張時に安全側へ倒す）
  return {
    text: `（${pageNote}位置合わせできませんでした。原因は特定できていません。` +
      "画像やテンプレートの内容を確認してください）",
    isError: false, level: "info",
  };
}

/// 画像を開く前のキャンバス案内（2026-09-02 ユーザー指摘）。出荷テンプレの
/// 自動読み込み（issue #56 T1-4・2026-08-31 対応）はそのまま維持しつつ、
/// 画像の無い暗いキャンバスにその座標の枠だけが浮いて見えるのは誤解を招く
/// ため、枠の代わりにこの案内を描く。キャンバス内の文字（draw()）と、
/// スクリーンリーダー向けの DOM 表示（msg）の両方から呼ぶので、行ごとの
/// テキストと結合済みテキストの両方を返す。
/// 欄・表がどちらも0のとき（自動読み込み失敗・loadTemplate 前）は「読み込み
/// 済み」と嘘をつかず、未読込であることを案内する（マリンレビュー M-2）
export function noImageNotice(
  templateId: string, fieldCount: number, tableCount: number):
  { line1: string; line2: string; text: string } {
  const line2 = "帳票の画像か PDF を開くと、枠が記入欄に重ねて表示されます";
  if (fieldCount === 0 && tableCount === 0) {
    const line1 = "テンプレートを読み込めていません。"
      + "「テンプレートを開く」で読み込むか、帳票を開いて枠を作成してください";
    return { line1, line2, text: `${line1}。${line2}` };
  }
  // 「出荷」を付けると、自動読み込みされた出荷テンプレでなく利用者自身の
  // JSON（loadTemplate 経由）を開いた場合にも「出荷テンプレート」と表示され
  // 誤解を招く（マリンレビュー M-1）ため、由来を問わない中立な言い方にする
  const line1 = `テンプレート（${templateId}）を読み込み済み・欄 ${fieldCount}・表 ${tableCount}`;
  return { line1, line2, text: `${line1}。${line2}` };
}

/// 起動時の自動読み込み案内（issue #72 (t)・スバル差し戻し1）。
/// read_default_template は config.last_template を解決して返す
/// （gui/src-tauri/src/lib.rs・あくあ実装）ため、Editor 起動時に「出荷」と
/// 「前回使った利用者テンプレート」のどちらが復元されたかが画面から
/// 分からなかった。判定は template_id の値ではなく last_template 自体で行う
/// ——デモモードの疑似出荷テンプレートは template_id が "demo" 等の任意値に
/// なりうり、id を出荷既定（"chouhyo-v1"）と比較すると実物と食い違う。
/// last_template が "user:<名前>" のときだけ「前回のテンプレート」と明示し、
/// それ以外（"shipped"・空・未設定・不正値）は従来どおり noImageNotice を使う。
export function restoredTemplateNotice(
  lastTemplate: string, templateId: string, fieldCount: number, tableCount: number,
): { text: string } {
  const base = noImageNotice(templateId, fieldCount, tableCount);
  if (!lastTemplate.startsWith("user:") || (fieldCount === 0 && tableCount === 0)) {
    return { text: base.text };
  }
  return { text: `前回のテンプレート（${templateId}）を読み込みました。`
    + `欄 ${fieldCount}・表 ${tableCount}。${base.line2}` };
}

/// テンプレート切替時（照合パネルの「開く」・利用者テンプレート一覧から
/// 開く・取り込み）、表示中の画像の寸法とテンプレートの image 寸法が
/// 食い違っていたら黄帯へ出す注意（issue #72 (t)・スバル差し戻し2）。
/// 保存時の寸法不一致確認（saveConfirmWarnings の "image-size"）と同じ
/// 要点（テンプレの寸法と画像の寸法が違う）を、こちらはブロックせず
/// 情報として伝えるだけの文言にする。imgSize が無い（画像未表示）・
/// テンプレの image 未設定・寸法が一致していれば null。
export function templateSwitchImageSizeNotice(
  imgSize: { w: number; h: number } | null,
  templateImage: { width: number; height: number } | null | undefined,
): string | null {
  if (!imgSize || !templateImage) return null;
  if (templateImage.width === imgSize.w && templateImage.height === imgSize.h) return null;
  return "開いている画像の寸法がこのテンプレートと異なります（"
    + `テンプレート ${templateImage.width}×${templateImage.height} → `
    + `画像 ${imgSize.w}×${imgSize.h}）。枠の位置がずれる可能性があります。`;
}

/// 「判定を無視して枠を表示する」（formatOverride=true）を押した後の常時
/// 警告文（issue #72 (t)・実機通し確認の指摘）。押した事実（何を上書きした
/// か）に加えて、やり直しの導線（別のテンプレートを試す2つの経路）を
/// 添える——上書き後は判定パネル・黄帯の操作ボタンが消えるため、押した
/// ことを忘れた利用者が別のテンプレートへ戻る手段を見失わないようにする。
export function formatOverrideBannerText(): string {
  return "様式判定を無視して枠を表示しています。別のテンプレートを試すには"
    + "「帳票を開く」で開き直すか、「この画像に合うテンプレート」の一覧から選んでください。";
}

/// 画像が無い間はキャンバス上の枠操作（選択・追加・ドラッグ・リサイズ・
/// 除外範囲の作成・表裏境界の移動）を無効化する（同上のユーザー指摘）。
/// パン（ドラッグ移動）とホイールズームは表示位置の調整に過ぎず誤操作の
/// 実害が無いため画像の有無に関わらず許可するが、その判定は呼び出し側
/// （onDown）の分岐順（パン判定がこの関数の呼び出しより前）で担保する——
/// ここでは tool を問わず画像の有無だけを見る（マリンレビュー H-1: 以前は
/// "pan" という仮のツール名をここへ渡す小細工をしていたが、渡し忘れる経路
/// （待ち受け状態が残ったまま次のクリックへ進む等）があり見通しが悪かった）
export function canvasInteractionAllowed(hasImage: boolean, _tool: string): boolean {
  return hasImage;
}

// ---------------------------------------------------------------- issue #72 (t)
// テンプレートの保存・選択・照合提示（設計08 §3）。この画面が持つのは
// 「一覧・照合結果を並べ替えて見せる」「空のテンプレートを組み立てる」
// という表示規則の純関数だけ。列挙・パス検査・照合の計算そのものは
// Rust／core 側の責務（08 §3.10 不変条件3）。

/// match_templates（Rust コマンド。core の match-templates を1プロセスで
/// 呼ぶ）が返す `results[]` の1件（core/chouhyo_ocr/cli.py:cmd_match_templates
/// 実測・2026-09-02）。`updated_at` はここでは ISO8601 文字列（cli.py が
/// `datetime.isoformat()` で整形済み）——list_user_templates の
/// UserTemplateListEntry（エポックミリ秒の数値）とは型が違う点に注意。
export type Candidate = {
  name: string; kind: "shipped" | "user"; template_id: string;
  fields: number; tables: number; updated_at: string;
  verdict: "match" | "mismatch" | "undecidable" | "skipped" | "unknown";
  reason: string; score: number; detected: number; expected: number;
};

/// list_user_templates（Rust コマンド）が返す一覧1件（UserTemplateInfo・
/// gui/src-tauri/src/user_templates.rs 実測）。updated_at は UNIX エポック
/// ミリ秒（UTC）——日付整形は GUI 側の責務（Rust 側の意図的な選択）。
export type UserTemplateListEntry = {
  name: string; template_id: string; fields: number; tables: number; updated_at: number;
};
export type ExcludedEntry = { name: string; reason: string };

const MATCH_NOTICE =
  "この判定は罫線の位置関係だけを見ており、中身の同一性は保証しません。";

// issue #72 (t)・ころね（user_advocate）の初見ユーザー予測レビュー: 利用者
// テンプレートの名前入力（window.prompt）が命名規則を示さないまま送信し、
// Rust の検証エラーで初めて拒否理由を知る作りだった（07 §7.4・
// validate_user_template_name の許可リスト方式）。規則を先に見せておく
const USER_TEMPLATE_NAME_RULE =
  "使える文字: 日本語・英数字・-・_・スペース（先頭末尾不可）、64文字まで。"
  + "「.」で終わる名前・CON/NUL などの予約名は使えません。拡張子は付けません。";

/// 照合結果の並び（issue #72 (t)・FR-F46・設計08 §3.4・AC-F53/F54）。
/// core・Rust は並べ替えない（並び順は表示規則であって判定ではない）ため、
/// この関数が唯一の並び順の決定箇所になる。
///   一致候補が1件、または上位2件のスコア差が0.1以上 → スコア降順・その1件を推奨
///   一致候補が複数・僅差（差<0.1）              → 名前順・推奨なし・スコア非表示
///   truncated（打ち切りあり）                    → 名前順・推奨なし・スコア非表示
///   一致候補ゼロ                                  → 名前順・推奨なし・スコアは表示
/// notice には常に「幾何一致のみを見ている」旨を含める（FR-F46 ③）。
export function rankCandidates(cands: Candidate[], truncated: boolean):
  { rows: Candidate[]; recommend: string | null; showScore: boolean; notice: string } {
  const byName = (a: Candidate, b: Candidate) => a.name.localeCompare(b.name, "ja");
  const byScoreDesc = (a: Candidate, b: Candidate) => b.score - a.score;

  if (truncated) {
    return { rows: [...cands].sort(byName), recommend: null, showScore: false,
      notice: `${MATCH_NOTICE} 一部は照合を打ち切りました（候補が多い・時間切れ）。` };
  }
  const matched = cands.filter((c) => c.verdict === "match");
  if (matched.length === 0) {
    return { rows: [...cands].sort(byName), recommend: null, showScore: true, notice: MATCH_NOTICE };
  }
  const matchedSorted = [...matched].sort(byScoreDesc);
  const closeCall = matchedSorted.length >= 2
    && matchedSorted[0].score - matchedSorted[1].score < 0.1;
  if (closeCall) {
    return { rows: [...cands].sort(byName), recommend: null, showScore: false, notice: MATCH_NOTICE };
  }
  return { rows: [...cands].sort(byScoreDesc), recommend: matchedSorted[0].name,
    showScore: true, notice: MATCH_NOTICE };
}

// マリン（reviewer）core レビュー分・issue #72 (t): excluded[].reason の
// 日本語化。list_user_templates（Rust・gui/src-tauri/src/user_templates.rs）
// と match_templates（core/chouhyo_ocr/cli.py）の両方が出す理由コードを
// まとめて1箇所で訳す——訳が2箇所に散ると片方だけ直し忘れるため。
// core の "invalid_json" は "parse" へ統一される予定（マリン指摘）だが、
// 移行中の互換のため両方を同じ訳へ倒す。未知のコードは生値をそのまま返す
// （存在しない訳を捏造しない）
const EXCLUDED_REASON_JA: Record<string, string> = {
  parse: "JSON として読めません",
  invalid_json: "JSON として読めません",   // core 側の呼称統一（parse）までの互換
  not_found: "ファイルがありません",
  schema: "テンプレートの形式が不正です",
  size: "サイズ上限（5MB）超過",
  limit: "件数上限で未照合",
  invalid_name: "名前が規則に合いません",
  check_failed: "照合処理でエラー",
};
export function excludedReasonJa(reason: string): string {
  return EXCLUDED_REASON_JA[reason] ?? reason;
}

// マリン core レビュー分: match_templates が ok:false を返したとき（core の
// error は固定コード input_not_found／expand_failed／input_unreadable／
// internal へ変更予定）に見せる理由。未知のコード・空は生値かフォール
// バック文言を返す（存在しない訳を捏造しない）
const MATCH_ERROR_JA: Record<string, string> = {
  input_not_found: "入力画像が見つかりません",
  expand_failed: "画像の展開に失敗しました",
  input_unreadable: "入力画像を読み込めません",
  internal: "内部エラーが発生しました",
};
export function matchErrorJa(code: string | undefined | null): string {
  if (!code) return "不明なエラー";
  return MATCH_ERROR_JA[code] ?? code;
}

/// 不一致・判定不能だった画像用の空テンプレート（issue #72 (t)・FR-F30・
/// 設計08 §3.6）。image は開いた画像の実寸、faces は現行の表裏境界
/// （splitY）で front/back の2面に分け、cells・tables・exclusions は空。
/// toEditorState（このファイル内・コンポーネント内関数）がそのまま読める
/// 形にする——buildTemplateJson が書き出す face の形と揃える。
export function emptyTemplateFor(width: number, height: number, splitY: number): {
  schema_version: number; template_id: string; render_dpi: number;
  image: { width: number; height: number }; record: { pages: number };
  faces: { face_id: "front" | "back";
            source: { page_offset: number; rect: Rect };
            fields: never[]; tables: never[]; exclusions: never[] }[];
} {
  const sy = Math.max(0, Math.min(splitY, height));
  const face = (id: "front" | "back", y0: number, y1: number) => ({
    face_id: id,
    source: { page_offset: 0, rect: { x: 0, y: y0, w: width, h: y1 - y0 } },
    fields: [] as never[], tables: [] as never[], exclusions: [] as never[],
  });
  return {
    schema_version: 1, template_id: "new-template", render_dpi: 300,
    image: { width, height }, record: { pages: 1 },
    faces: [face("front", 0, sy), face("back", sy, height)],
  };
}

/// 空のテンプレートを開いた直後の案内（issue #72 (t)・FR-F31・設計08 §3.6）。
/// (b)（ページ全体からの枠候補生成）は今回未実装のため、hasCandidates は
/// 現状常に false——将来 (b) が入ったときの分岐だけ先に用意しておく。
export function newTemplateNotice(hasCandidates: boolean): string {
  if (hasCandidates) {
    return "この画像から検出した枠の候補があります。候補を確認して採用してください。";
  }
  return "空のテンプレートで開きました。「くり返し行（家族・明細）」で表の外枠を描くと、"
    + "行と列を自動検出できます（等分割の生成にも切り替えられます）。単発の欄は"
    + "「欄を追加」で描いてください。";
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

// キー入力の実行結果（副作用そのものではなく「何をすべきか」の記述）。
// keyRef.current 側がこれを見て実際の関数呼び出し・setState を行う
export type KeyActionType =
  | { type: "space-down" }
  | { type: "undo" } | { type: "redo" }
  | { type: "fit" } | { type: "zoom-reset" } | { type: "zoom-in" } | { type: "zoom-out" }
  | { type: "escape" }
  | { type: "delete" }
  | { type: "nudge"; dx: number; dy: number };
export type KeyAction = { action: KeyActionType; preventDefault: boolean };

/// キー入力の判定だけを行う純関数（issue #69 Q-H3）。実際の副作用（Undo・
/// 削除・nudge 等の呼び出し）は keyRef.current 側が返り値の action を見て
/// 行う。ここを純関数に切り出すことで、フックの外（node）から
/// 「実行タブ表示中（!active）は常に null」「ボタンにフォーカスがある間の
/// Space は素通りする（preventDefault されない）」を直接固定できる
export function keyAction(
  e: { code: string; key: string; shiftKey: boolean; ctrlKey: boolean; metaKey: boolean },
  ctx: { active: boolean; typing: boolean; isButtonFocused: boolean; hasSel: boolean },
): KeyAction | null {
  // Editor が表示されていないタブ（実行タブ等）ではキー入力を一切拾わない。
  // 旧実装はグローバル window リスナーがタブ非表示中も生き続けていたため、
  // 実行タブで Delete を押すとテンプレートの欄が消える事故があった
  if (!ctx.active) return null;
  if (e.code === "Space" && !ctx.typing) {
    // ボタンにフォーカスがある間の Space はボタン自身のクリック起動に譲る。
    // ここを typing 扱いにはしない——それだと Delete/矢印キーまで死ぬため、
    // Space だけをこの1行で除外する
    if (ctx.isButtonFocused) return null;
    // ページスクロールとボタンの再押下を防ぐ。押しっぱなしのリピートは
    // 呼び出し側（spaceRef 済みなら無視）で吸収する
    return { action: { type: "space-down" }, preventDefault: true };
  }
  if (ctx.typing) return null;   // 入力欄では通常のテキスト編集を優先する
  const ctrl = e.ctrlKey || e.metaKey;
  if (ctrl && e.key.toLowerCase() === "z")
    return { action: { type: e.shiftKey ? "redo" : "undo" }, preventDefault: true };
  if (ctrl && e.key.toLowerCase() === "y")
    return { action: { type: "redo" }, preventDefault: true };
  if (ctrl && e.key === "0") return { action: { type: "fit" }, preventDefault: true };
  if (ctrl && e.key === "1") return { action: { type: "zoom-reset" }, preventDefault: true };
  if (ctrl && (e.key === "+" || e.key === "="))
    return { action: { type: "zoom-in" }, preventDefault: true };
  if (ctrl && e.key === "-") return { action: { type: "zoom-out" }, preventDefault: true };
  if (e.key === "Escape") return { action: { type: "escape" }, preventDefault: false };
  if ((e.key === "Delete" || e.key === "Backspace") && ctx.hasSel)
    return { action: { type: "delete" }, preventDefault: true };
  if (e.key.startsWith("Arrow") && ctx.hasSel) {
    const step = e.shiftKey ? 10 : 1;
    return { action: { type: "nudge",
      dx: e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0,
      dy: e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0 },
      preventDefault: true };
  }
  return null;
}

/// 画像なしキャンバスの案内文を、幅 maxWidth（CSS px・現在の ctx.font 基準）に
/// 収まるよう複数行へ折り返す（2026-09-02 コーディネータ指摘1: 下限フォント
/// サイズまで縮めても収まらない長文がプレート外へはみ出していた）。
/// 「。」「、」の直後を優先した文節区切りで改行し、1文節がそれでも幅を
/// 超える場合だけ文字単位で強制的に詰める。CanvasRenderingContext2D 依存で
/// 純関数のテスト対象にならないため export しない（gui-logic のテストは
/// draw() のスクリーンショット確認で代替する）
function wrapNoticeText(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string[] {
  const chunks: string[] = [];
  let cur = "";
  for (const ch of text) {
    cur += ch;
    if (ch === "。" || ch === "、") { chunks.push(cur); cur = ""; }
  }
  if (cur) chunks.push(cur);

  const lines: string[] = [];
  let line = "";
  for (const chunk of chunks) {
    if (line && ctx.measureText(line + chunk).width > maxWidth) {
      lines.push(line);
      line = "";
    }
    if (ctx.measureText(chunk).width <= maxWidth) {
      line += chunk;
      continue;
    }
    // 1文節でも幅を超える→文字単位で詰める（区切りが無い長文への保険）
    for (const ch of chunk) {
      if (line && ctx.measureText(line + ch).width > maxWidth) {
        lines.push(line);
        line = "";
      }
      line += ch;
    }
  }
  if (line) lines.push(line);
  return lines.length ? lines : [text];
}

export default function Editor(
  { onDirty, active }: { onDirty: (d: boolean) => void; active: boolean }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [imgSize, setImgSize] = useState<{ w: number; h: number } | null>(null);
  // 画像が読み込まれているか（2026-09-02 ユーザー指摘・マリンレビュー H-1）。
  // JSX（disabled/title 等）と onDown のガードは、値を変えると必ず再レンダー
  // させたいので state（imgSize）から導く。draw() だけは rAF から都度呼ばれる
  // 命令的な描画関数で、im.onload の直後（setImgSize 反映前）にも正しく
  // 「画像ありで描く/無しで描く」を切り替えたいため、そこでは同じ判定に
  // imgRef（ref）を直接見る——同じ im.onload 内で両方を同時にセットしている
  // ので、レンダーのタイミングによる不一致は起きない（コーディネータ指摘7）
  const hasImage = imgSize !== null;
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
  // 失敗ではないが目立たせたい注意（切り抜きの10〜30%減・コアの verify
  // 警告）。灰色の msg に混ぜると気づかれない（D-7）ため errMsg（赤帯）とは
  // 別チャンネルにする（.warnbox・issue #59 H-4／設計書 U-08・U-09）
  const [warnMsg, setWarnMsg] = useState("");
  // 様式不一致の黄帯文言（issue #71 (a')・FR-F04）。carve 警告と同じ warnMsg に
  // 混ぜると、編集操作のたびに carveWarningNotice の setWarnMsg("") 上書きで
  // 消えてしまう（別の事実なので別チャンネルにする）
  const [formatWarnMsg, setFormatWarnMsg] = useState("");
  // expand-page が返した面ごとの様式判定（FR-F04・未対応の旧コアでは空配列の
  // まま＝現行どおり全て描く）
  const [formatFaces, setFormatFaces] =
    useState<{ face_id: string; verdict: string }[]>([]);
  // 「それでもこのテンプレートで開く」（FR-F05）。画像を開き直すと false に戻す
  const [formatOverride, setFormatOverride] = useState(false);
  // issue #72 (t)・照合提示（FR-F28/F46）。画像を開くたびに match_templates
  // を呼び直す。結果は「この画像に合うテンプレート」パネルへ出す
  // （設計08 §3.3・§3.4）
  const [matchResult, setMatchResult] = useState<null
    | { candidates: Candidate[]; excluded: ExcludedEntry[]; truncated: boolean }>(null);
  const [matchLoading, setMatchLoading] = useState(false);
  const [matchError, setMatchError] = useState("");
  // 現在開いているテンプレートが一致（match）なら、提示を畳んでおく
  // （設計08 §3.4「現在開いているテンプレートが match なら提示は畳んでおく」）
  const [matchCollapsed, setMatchCollapsed] = useState(false);
  // 利用者テンプレート一覧（「利用者テンプレートから開く」パネル・FR-F27/F29）
  const [userTplPanel, setUserTplPanel] = useState<null
    | { templates: UserTemplateListEntry[]; excluded: ExcludedEntry[]; error: string }>(null);
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
  // 読み込み時点の欄数・金額列数・列数（issue #59 H-9・#66 FR-1.6・最後の
  // 検知網）。除外数は上の loadedExcls.length で足りるためここには持たない
  const [loadedCounts, setLoadedCounts] =
    useState({ fields: 0, amountCells: 0, exclusions: 0, columns: 0 });
  // 読み込み時点の verify column_names（issue #66 段3・FR-0.1）。列位置表示
  // （出力列タブ・チェックの accessible name）の唯一の入力源——GUI 側で
  // 列名・列順を再導出しない（FR-0.1・F-10 の再発防止）。編集中の増減には
  // 追従しない（次の verify＝保存成功まで据え置き）が、その場合は
  // outputCheckboxLabel が列番号を省略する（誤った番号を言わないため）
  const [columnNames, setColumnNames] = useState<string[] | null>(null);
  // 読み込み時／直近保存時点の出力順の基準（issue #66 段6・FR-2.2・AC-2.4）。
  // 並べ替えを含む保存かどうかの判定・段7の列位置表示の失効判定の両方に使う
  const [loadedOrder, setLoadedOrder] = useState<OutputOrderSnapshot | null>(null);
  // 右パネルのタブ（issue #66 段3・FR-1.7・C-1）。「選択中」欄の詳細か
  // 「出力列」一覧かを切り替える
  const [panelTab, setPanelTab] = useState<"selected" | "output">("selected");
  // 保存前確認モーダル（issue #66 段3・FR-1.6・AC-1.23）。window.confirm の
  // 3連発＋列減少チェックを1枚のモーダルに統合するための Promise ブリッジ。
  // resolve(true)=このまま保存 / resolve(false)=保存しない
  const [confirmModal, setConfirmModal] = useState<
    { warnings: SaveWarning[]; busy: boolean; resolve: (proceed: boolean) => void } | null>(null);
  const saveBtnRef = useRef<HTMLButtonElement>(null);
  const modalCancelRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLDivElement>(null);
  // 保存モーダルの直近の開閉状態（開いた瞬間だけ初期フォーカスするための番兵）
  const prevModalOpen = useRef(false);
  // saveTemplate の多重起動防止（C-5「二重押下防止」の根っこのガード。
  // モーダルのボタン disabled はこれの補助で、保存フロー全体を1本化する）
  const savingRef = useRef(false);
  // 選択肢入力の編集中の値（M-13）。選択が変わったら捨てる
  const [choiceDraft, setChoiceDraft] = useState<string | null>(null);
  // パネルで触っている列（canvas ハイライト用・レビュー D-3）
  const [hlCol, setHlCol] = useState<number | null>(null);
  // 出力列タブの行 hover/focus で canvas の該当欄をハイライトする
  // （issue #66 段3・FR-1.8・C-1）。sel（選択）とは独立——一覧を眺めている
  // だけで選択状態を変えたくない
  const [hlFieldUid, setHlFieldUid] = useState<string | null>(null);
  // 並べ替え成功時の行フラッシュ（issue #66 段7・付録A・C-2 success）。
  // 600ms 後に自動で消える。連打時は最後の1回だけ光らせる——毎クリックで
  // タイマーを張り直す（古いタイマーは clearTimeout）ので必ず「最後の
  // クリックから600ms」で消える
  const [flashUid, setFlashUid] = useState<string | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashRow = (uid: string) => {
    if (flashTimer.current) clearTimeout(flashTimer.current);
    setFlashUid(uid);
    flashTimer.current = setTimeout(() => setFlashUid(null), 600);
  };
  const drag = useRef<{ mode: string; start: { x: number; y: number };
                        orig?: Rect; extra?: { x: number; y: number } } | null>(null);
  // 開いたテンプレートのメタ情報。faces 以外を編集画面は触らないが、保存時に
  // 既定値で上書きすると render_dpi/image が壊れる（issue #15）ため往復保持する
  const meta = useRef<{ template_id: string; render_dpi: number;
                        image: { width: number; height: number } | null;
                        record: Record<string, unknown> }>({
    template_id: "chouhyo-v1", render_dpi: 300, image: null, record: { pages: 1 } });

  // 現在のキャンバス寸法（優先順: 実際に開いた画像 > 開いたテンプレートの
  // image > 新規既定値）。座標クランプの全出口とテンプレ書き出しで同じ
  // 優先順を使うことで、クランプ先と実際に書き出す寸法がずれるのを防ぐ
  const curSize = (): { W: number; H: number } => ({
    W: imgSize?.w ?? meta.current.image?.width ?? 2490,
    H: imgSize?.h ?? meta.current.image?.height ?? 3510,
  });

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
    const px = 1 / zoom;
    if (!imgRef.current) {
      // 画像を開くまで枠を描かない（2026-09-02 ユーザー指摘）。8/31 対応の
      // 出荷テンプレ自動読み込みはそのまま活かしつつ、画像の無い暗い
      // キャンバスにその座標の枠だけが浮くのは誤解を招く——用紙サイズの
      // 白い下地だけは、通常と同じパン／ズーム変換の内側（用紙座標）で描く。
      // 画像が読み込まれた瞬間は imgSize の変化で draw() が再実行され、
      // この分岐を通らなくなり、位置合わせ済み画像と枠が同時に出る
      const { W: nw, H: nh } = curSize();
      ctx.fillStyle = "#ffffff";
      ctx.fillRect(0, 0, nw, nh);
      ctx.restore();

      // 案内2行は変換の外＝画面座標（CSS px）で描く（2026-09-02 コーディネータ
      // 指摘）。用紙座標のまま描くと、縮小ズーム時は画面下端に半分だけ・
      // パン位置次第では白い下地の外の暗い背景にはみ出して読めなくなる
      // （実機スクショで確認）。ズーム・パンに関わらずキャンバス表示領域の
      // 中央に固定する
      const notice = noImageNotice(meta.current.template_id, fields.length, tables.length);
      const pad = 16;
      const maxW = width - 40;
      let size1 = 20, size2 = 18;
      const measureBoth = () => {
        ctx.font = `bold ${size1}px sans-serif`;
        const w1 = ctx.measureText(notice.line1).width;
        ctx.font = `${size2}px sans-serif`;
        const w2 = ctx.measureText(notice.line2).width;
        return Math.max(w1, w2);
      };
      let textW = measureBoth();
      // ボックス幅が収まらない間はフォントを1pxずつ縮める（下限14px）。
      // 極端に長い template_id・欄数でも無限ループにならないよう下限で止める
      while (textW + pad * 2 > maxW && (size1 > 14 || size2 > 14)) {
        if (size1 > 14) size1--;
        if (size2 > 14) size2--;
        textW = measureBoth();
      }
      // 下限フォントでもなお収まらない場合ははみ出す代わりに折り返す
      // （コーディネータ指摘1: M-2 の未読込文言のような長文が 1000×700 で
      // プレート幅からはみ出していた）。折り返し後の実測幅でボックス幅を
      // 決め直す——常に maxW いっぱいのボックスにはしない
      const innerW = maxW - pad * 2;
      ctx.font = `bold ${size1}px sans-serif`;
      const rows1 = wrapNoticeText(ctx, notice.line1, innerW);
      ctx.font = `${size2}px sans-serif`;
      const rows2 = wrapNoticeText(ctx, notice.line2, innerW);
      const measureRows = (rows: string[], font: string) => {
        ctx.font = font;
        return rows.reduce((m, r) => Math.max(m, ctx.measureText(r).width), 0);
      };
      const rowsW = Math.max(
        measureRows(rows1, `bold ${size1}px sans-serif`),
        measureRows(rows2, `${size2}px sans-serif`));
      const rowH1 = size1 * 1.3, rowH2 = size2 * 1.3;
      const boxW = Math.min(maxW, rowsW + pad * 2);
      const boxH = rows1.length * rowH1 + rows2.length * rowH2 + pad * 2;
      const boxX = width / 2 - boxW / 2;
      const boxY = height / 2 - boxH / 2;
      const r = 8;
      ctx.beginPath();
      ctx.moveTo(boxX + r, boxY);
      ctx.arcTo(boxX + boxW, boxY, boxX + boxW, boxY + boxH, r);
      ctx.arcTo(boxX + boxW, boxY + boxH, boxX, boxY + boxH, r);
      ctx.arcTo(boxX, boxY + boxH, boxX, boxY, r);
      ctx.arcTo(boxX, boxY, boxX + boxW, boxY, r);
      ctx.closePath();
      ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.strokeStyle = "#cccccc";
      ctx.lineWidth = 1;
      ctx.stroke();

      ctx.fillStyle = "#333333";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      let y = boxY + pad + rowH1 / 2;
      ctx.font = `bold ${size1}px sans-serif`;
      for (const row of rows1) { ctx.fillText(row, width / 2, y); y += rowH1; }
      ctx.font = `${size2}px sans-serif`;
      for (const row of rows2) { ctx.fillText(row, width / 2, y); y += rowH2; }
      return;
    }
    ctx.drawImage(imgRef.current, 0, 0);
    const W = imgSize?.w ?? 2490;
    const H = imgSize?.h ?? 0;

    // 表裏分割線
    ctx.strokeStyle = "#ff5577"; ctx.lineWidth = 3 * px;
    ctx.beginPath(); ctx.moveTo(0, splitY); ctx.lineTo(W, splitY); ctx.stroke();

    // 様式不一致（mismatch）と判定された面の枠は描かない（issue #71 (a')・
    // FR-F04）。draw() とヒットテスト（onDown の hitAll）は同じ
    // hiddenFaces／visibleFields／visibleTables／visibleExcls を見る
    // （L-Q1 の教訓: 述語を2つ持たない・見えない枠を掴ませない）
    const hidden = hiddenFaces(formatFaces, formatOverride);
    const visFields = visibleFields(fields, hidden, splitY, H);
    const visTables = visibleTables(tables, hidden, splitY, H);
    const visExcls = visibleExcls(excls, hidden, splitY, H);

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
    // 出力しない欄・表の列の描画（issue #66 段3・FR-1.5・AC-1.22）。枠色は
    // 維持したまま斜線ハッチを重ね、16px固定（画面上で常に同じ大きさ・
    // label() 非経由）の⊘バッジを不透明チップ（塗り背景＋前景記号）で描く。
    // グレーアウトは除外領域の灰色と、破線は参照先の表現と意味が衝突する
    // ため使わない。選択中でもハッチ・バッジは消さない（C-4: 選択で状態を
    // 見失わせない）
    const hatchArea = (r: Rect) => {
      ctx.save();
      ctx.beginPath(); ctx.rect(r.x, r.y, r.w, r.h); ctx.clip();
      ctx.strokeStyle = "rgba(28,31,38,0.30)"; ctx.lineWidth = 2 * px;
      const step = 10 * px;
      for (let d = -r.h; d < r.w + r.h; d += step) {
        ctx.beginPath();
        ctx.moveTo(r.x + d, r.y);
        ctx.lineTo(r.x + d - r.h, r.y + r.h);
        ctx.stroke();
      }
      ctx.restore();
    };
    const outputBadge = (r: Rect) => {
      // チップ単体で 3:1 以上のコントラストを持たせる（可変のスキャン画像を
      // 背景に取らない・AC-1.22）。不透明な濃色円＋白い⊘記号の自己完結配色
      const size = 16 * px, m = 2 * px;
      const cx = r.x + r.w - size / 2 - m, cy = r.y + size / 2 + m;
      ctx.beginPath(); ctx.arc(cx, cy, size / 2, 0, Math.PI * 2);
      ctx.fillStyle = "#1c1f26"; ctx.fill();
      const rr = size / 2 - 3 * px;
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = Math.max(1.4 * px, px);
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, Math.PI * 2); ctx.stroke();
      const d = rr * Math.SQRT1_2;
      ctx.beginPath();
      ctx.moveTo(cx - d, cy + d); ctx.lineTo(cx + d, cy - d);
      ctx.stroke();
    };
    ctx.lineWidth = 2 * px;
    for (const e of visExcls)
      rect(e.rect, sel?.uid === e.uid ? "#ffd54a" : "#888",
           "rgba(120,120,120,0.35)");
    for (const f of visFields) {
      rect(f.rect, sel?.uid === f.uid && sel?.part !== "fallback"
        ? "#ffd54a" : f.kind === "choice" ? "#c586ff" : "#4fc3f7");
      if (hlFieldUid === f.uid) {
        ctx.fillStyle = "rgba(255,213,74,0.28)";
        ctx.fillRect(f.rect.x, f.rect.y, f.rect.w, f.rect.h);
      }
      if (!isOutput(f)) { hatchArea(f.rect); outputBadge(f.rect); }
      for (const m of f.marks) rect(m.rect, "#c586ff");
      ctx.fillStyle = "#9fd8ff";
      label(f.field_id, f.rect.x + 4 * px, f.rect.y + 26 * px,
            f.rect.w - 8 * px, f.rect.h);
      for (let i = 0; i < (f.extras?.length ?? 0); i++) {
        const ex = f.extras![i];
        rect(ex, sel?.uid === f.uid && sel?.part === `extra:${i}`
          ? "#ffd54a" : f.kind === "choice" ? "#c586ff" : "#4fc3f7");
        if (!isOutput(f)) hatchArea(ex);   // 追加領域も同じ欄の一部（出力しない欄は全域）
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
    for (const t of visTables) {
      const totalW = t.columns.length
        ? Math.max(...t.columns.map((c) => c.x_offset + c.width)) : 0;
      for (const b of t.blocks) {
        const bh = t.row_pitch * (b.rows - 1) + t.row_height;
        rect({ x: b.x, y: b.y, w: totalW, h: bh },
             sel?.uid === t.uid ? "#ffd54a" : "#7ce38b");
        if (hlFieldUid === t.uid) {
          ctx.fillStyle = "rgba(255,213,74,0.28)";
          ctx.fillRect(b.x, b.y, totalW, bh);
        }
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
        // 出力しない列（issue #66 段3・付録A）: ブロック全高に1枚のハッチ＋
        // バッジ（行ごとには描かない——全行一括で外れる仕様のため・FR-1.1）
        for (const c of t.columns) {
          if (isOutput(c)) continue;
          const colRect = { x: b.x + c.x_offset, y: b.y, w: c.width, h: bh };
          hatchArea(colRect);
          outputBadge(colRect);
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
  }, [excls, fields, tables, pending, sel, splitY, zoom, pan, imgSize, hlCol, hlFieldUid,
      formatFaces, formatOverride]);

  // draw() は全欄のラベルをループで measureText トリムするため、ドラッグ中の
  // mousemove のたびに毎回同期実行すると重い（issue #60 M-3・実測で back面
  // 140セル中84セル以上がトリム必至）。requestAnimationFrame へ間引くことで、
  // 同じ描画フレーム内に複数回 fields 等が更新されても実際の描画は1回で済む
  // ——ラベル単位のメモ化（zoom×field_idキャッシュ）も検討したが、
  // 参照先ラベルなど text がfield_idそのものでない呼び出しがあり
  // キー設計が複雑になるため、より単純なこちらを選んだ（過剰設計回避）
  useEffect(() => {
    const raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [draw]);
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
  // issue #72 (t)・照合提示（FR-F28/F46・設計08 §3.3）。core は列挙を行わない
  // （§3.10 不変条件3）ため、まず list_user_templates で表示名の一覧を取り、
  // その名前だけを Rust の match_templates（core の match-templates を1プロセス
  // で呼ぶ新設 Tauri コマンド・§3.3.1 C-1）へ渡す。GUI は絶対パスを一切
  // 持たない（07 §7.3・§9.4）ので候補パスの解決は Rust 側の責務。
  const runMatchTemplates = async (input: string) => {
    setMatchLoading(true); setMatchError(""); setMatchResult(null);
    try {
      let names: string[] = [];
      try {
        const list = await invoke<{ templates: UserTemplateListEntry[]; excluded: ExcludedEntry[] }>(
          "list_user_templates");
        names = (list?.templates ?? []).map((e) => e.name);
      } catch { /* 一覧取得の失敗は照合そのものを止めない（出荷1件だけでも試す） */ }
      // match_templates（Rust）は core の match-templates サブコマンドの
      // stdout（JSON Lines・match_templates イベント1行）をそのまま文字列で
      // 返す（run_core_capture と同じ流儀）。核心のキーは `results`（core
      // 実装名・cli.py:cmd_match_templates 実測。08 §3.3.2 の例示は
      // `candidates` だが、実装済みの core・Rust は `results` で統一されている）
      const raw = await invoke<string>("match_templates", { input, names });
      const parsed = JSON.parse(raw) as { ok?: boolean; error?: string; truncated?: boolean;
        results?: Candidate[]; excluded?: ExcludedEntry[] };
      // マリン core レビュー分: ok:false（core が入力を開けなかった等・
      // event:"match_templates" の失敗応答）を空パネルのまま握り潰さない。
      // error は固定コード（input_not_found／expand_failed／input_unreadable／
      // internal・core 側で統一予定）を想定し、未知値は生のコードを見せる
      if (parsed?.ok === false) {
        setMatchError(`照合できませんでした（${matchErrorJa(parsed.error)}）`);
        return;
      }
      setMatchResult({
        candidates: Array.isArray(parsed?.results) ? parsed.results : [],
        excluded: Array.isArray(parsed?.excluded) ? parsed.excluded : [],
        truncated: !!parsed?.truncated,
      });
    } catch (e) {
      setMatchError(`テンプレート候補の照合に失敗しました: ${e}`);
    } finally {
      setMatchLoading(false);
    }
  };

  const loadImage = async () => {
    if (!confirmDiscard()) return;
    const p = await invoke<string | null>("pick_image");
    if (!p) return;
    const isPdf = p.toLowerCase().endsWith(".pdf");
    let imagePath = p;
    let note = "";
    // テンプレ破損・様式不一致由来の帯は setErrMsg("")／setFormatWarnMsg("")
    // が im.onload で無条件に呼ばれた後に上書きされないよう変数で持ち回り、
    // onload 側で反映する（いろは5巡目指摘・issue #71 (a') で黄帯を追加）
    let alignErr = "";
    let alignWarn = "";
    let faces: { face_id: string; verdict: string }[] = [];
    let verdict: string | undefined;
    // 画像（PNG/JPG 等）でも expand-page を通す（issue #71 (a')・設計08
    // §2.7.1・AC-F02 の前提）。ingest.expand は PDF 以外を入力そのまま
    // 返すため、コア側の変更は不要——PDF 専用だった従来の分岐を外すだけで
    // 位置合わせ・様式判定の両方が画像にも掛かるようになる
    setMsg(isPdf ? "PDF を展開しています…" : "画像を確認しています…");
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
        setErrMsg(`${isPdf ? "PDF" : "画像"}を開けませんでした: ${ev?.error ?? "不明"}`);
        setMsg("");
        return;
      }
      imagePath = ev.page_path;
      // 位置合わせ済みの画像なら、テンプレートの枠が最初から記入欄の上に
      // 乗る。合わせられなかった紙・様式が違う紙は、原因（reason）・
      // 様式判定（verdict）に応じて案内を出し分ける
      // （いろは5巡目指摘＋issue #71 (a')。expandAlignNotice 参照）
      const pageNote = ev.pages > 1 ? `PDF の 1/${ev.pages} ページ目・` : "";
      const align = expandAlignNotice(
        ev.aligned, ev.reason, pageNote, ev.verdict, meta.current.template_id);
      if (align.level === "error") alignErr = align.text;
      else if (align.level === "warn") alignWarn = align.text;
      else note = align.text;
      faces = Array.isArray(ev.faces) ? ev.faces : [];
      verdict = ev.verdict;
    } catch (e) {
      setErrMsg(`${isPdf ? "PDF" : "画像"}を開けませんでした: ${e}`);
      setMsg("");
      return;
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
      // テンプレ破損由来の位置合わせ失敗（alignErr）があれば赤帯を残す。
      // 無ければ従来どおりクリアする。様式不一致（alignWarn）は黄帯専用の
      // formatWarnMsg へ（carve 警告の warnMsg とは別チャンネル）
      setErrMsg(alignErr);
      setFormatWarnMsg(alignWarn);
      setFormatFaces(faces);
      // 新しい画像を開いたら上書き表示（FR-F05）は必ずリセットする
      // （設計08 §2.7.4「画像を開き直したら false に戻す」）
      setFormatOverride(false);
      setMsg(`画像 ${im.naturalWidth}×${im.naturalHeight}${note}`);
      draw();
    };
    im.onerror = () => { setErrMsg("画像の表示に失敗しました"); setMsg(""); };
    im.src = src;
    // 照合提示（issue #72 (t)・FR-F28）。現在開いているテンプレートが一致
    // 判定なら提示を畳んでおく（設計08 §3.4）。画像の描画（im.onload）を
    // 待たずに並行して照合する——結果はパネル1つの表示だけに使う
    setMatchCollapsed(verdict === "match");
    void runMatchTemplates(imagePath);
  };

  const toEditorState = (t: any): { fieldCount: number; tableCount: number } => {
    meta.current = {
      // 空文字の template_id（壊れたテンプレJSON等）を拾い漏らさないよう
      // ?? ではなく || にする（マリンレビュー LOW）
      template_id: t.template_id || "chouhyo-v1",
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
                    ({ value: m.value, rect: { ...m.rect, y: m.rect.y + oy } })),
                  // output 省略＝出力する（FR-1.7・既存テンプレ互換）
                  output: f.output === false ? false : undefined });
      for (const tb of face.tables ?? [])
        ts.push({ uid: uid(), table_id: tb.table_id, row_pitch: tb.row_pitch,
                  row_height: tb.row_height,
                  blocks: tb.blocks.map((b: any) =>
                    ({ x: b.origin.x, y: b.origin.y + oy, rows: b.rows })),
                  columns: tb.columns.map((c: any) => ({
                    name: c.name, x_offset: c.x_offset, width: c.width, kind: c.kind,
                    subfields: (c.subfields ?? []).join(","),
                    normalize: c.normalize,
                    marks: c.choice_marks ?? [],
                    output: c.output === false ? false : undefined })) });
    }
    setFields(fs); setTables(ts); setExcls(es); setSplitY(sy ?? 1880);
    setLoadedExcls(es.map((e) => ({ id: e.id, rect: e.rect })));
    // 出力順の読み込み時基準（issue #66 段6・FR-2.2）。この後の並べ替えが
    // あったかどうかを、この時点の配列順と比べて判定する
    setLoadedOrder(outputOrderSnapshot(fs, ts));
    // 欄数・金額列数の読み込み時基準（issue #59 H-9）は、この関数の呼び出し側
    // （auto-load useEffect・loadTemplate）が refreshLoadedCounts で verify
    // 応答から別途取得する。toEditorState 自身は同期関数で verify（非同期）を
    // 呼べないうえ、GUI 側で fs.length 等から再導出すると保存時（core の
    // verify＝行展開後の全セル数）と母集団がずれる（issue #66 段0・F-10）。
    // 戻り値の fieldCount/tableCount はこの基準とは別物——画像なしキャンバスの
    // 案内表示専用の単純な件数であり、差分判定には使わない（2026-09-02）
    return { fieldCount: fs.length, tableCount: ts.length };
  };

  // 読み込み時点の欄数・金額列数・除外数の基準を verify 応答から取得する
  // （issue #66 段0・出力列制御 MVP・F-10 バグ修正）。以前は GUI 側で
  // fields.length・countAmountCells 等を数えて基準にしていたが、これは
  // 単発欄だけを数え表の列を含まない値で、保存時（core の verify＝行展開後の
  // 全セル数）と母集団が異なっていた——無編集で保存しても「欄 14→194」の
  // ように差分が出る既知バグの原因だった。読み込み時・保存時のどちらも
  // 同じ verify（template チェック）応答の cells/amount_cells/exclusions を
  // 基準にすることで、母集団を必ず揃える。templatePath が null のときは
  // --template を省略し、lib.rs の inject_default_template が出荷テンプレを
  // 注入する（第0段の配線・read_default_template と同じファイルになる）
  const refreshLoadedCounts = async (templatePath: string | null) => {
    try {
      const args = ["verify"];
      if (templatePath) args.push("--template", templatePath);
      const out = await invoke<string>("run_core_capture", { args });
      const tpl = out.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
        .find((e) => e && e.check === "template");
      if (tpl?.ok) {
        setLoadedCounts({ fields: tpl.cells, amountCells: tpl.amount_cells,
                          exclusions: tpl.exclusions, columns: tpl.columns });
        // column_names は verify がまだ返さない旧コアでも動くよう、フィールド
        // 欠落時は null のまま（outputCheckboxLabel 等が列番号を省略する）
        setColumnNames(Array.isArray(tpl.column_names) ? tpl.column_names : null);
      }
      // NG のときは基準の更新を諦める（読み込み自体は既に成功しているので
      // ブロックしない）。古い基準のまま残るが、次に保存できる状態になった
      // 時点でまた比較すればよい——このテンプレートは verify NG のままだと
      // どのみち保存時にも NG になり、差分計算まで到達しない
    } catch { /* 基準取得の失敗も同様に読み込み自体は妨げない */ }
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
        const { fieldCount, tableCount } = toEditorState(parsed);
        resetHistory();   // 読み込み前の空状態へ Ctrl+Z で戻れると事故のもと
        markDirty(false);
        // last_template（issue #72 (t)・スバル差し戻し1）: read_default_template
        // は config.last_template を解決して返すため、「出荷」と「前回使った
        // 利用者テンプレート」のどちらが復元されたかを画面へ明示する。
        // last_template 自体が取れなければ従来どおり（noImageNotice 相当）
        let lastTemplate = "";
        try {
          const cfg = await invoke<Record<string, unknown>>("read_config");
          if (typeof cfg.last_template === "string") lastTemplate = cfg.last_template;
        } catch { /* 取得できなくても「前回のテンプレート」表示を諦めるだけ */ }
        // 画像を開くまでキャンバスに枠を描かない（2026-09-02 ユーザー指摘）。
        // 案内はキャンバス内の文字（draw()）にも出すが、そちらはスクリーン
        // リーダーに読めないため、同じ内容をこの msg（DOM）にも出す
        setMsg(restoredTemplateNotice(
          lastTemplate, meta.current.template_id, fieldCount, tableCount).text);
        await refreshLoadedCounts(null);
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
    const { fieldCount, tableCount } = toEditorState(parsed);
    resetHistory();   // 別テンプレートをまたぐ Undo は誤操作のもと
    setTplPath(p);    // 保存ダイアログの既定をこのファイルにする（issue #56 T1-3）
    // 前のファイルの検証エラー・警告を現在の状態と誤読させない（レビュー N-5）
    setErrMsg(""); setWarnMsg("");
    markDirty(false);
    // 画像がまだ無ければ、DOM 案内をキャンバスの文字（draw()）と同じ内容に
    // 揃える（マリンレビュー M-1）。以前は常に「テンプレート読込: <path>」
    // 固定で、利用者自身の JSON を開いても canvas 側の「出荷テンプレート」
    // 文言と食い違っていた。画像が既にあればそちらの経路は使わず、
    // どのファイルを読み込んだかが分かる従来の文言を維持する
    if (imgRef.current) {
      setMsg(`テンプレート読込: ${p}`);
    } else if (fieldCount === 0 && tableCount === 0) {
      // 欄0・表0は自動読み込み失敗時と同じ判定式だが、ここは「今まさに
      // 有効な JSON を p から読み込んだ」ことが分かっている経路——
      // noImageNotice の「テンプレートを読み込めていません」をそのまま出すと
      // 直前に選んだファイルが読み込めなかったかのように誤解される
      // （コーディネータ指摘5）。パスと件数を残した専用の文言にする
      setMsg(`テンプレート読込: ${p}（欄 ${fieldCount}・表 ${tableCount}）。`
        + noImageNotice(meta.current.template_id, fieldCount, tableCount).line2);
    } else {
      setMsg(noImageNotice(meta.current.template_id, fieldCount, tableCount).text);
    }
    await refreshLoadedCounts(p);
  };

  // issue #72 (t)・FR-F26/F27/F29・設計08 §3.2.2/§3.6。利用者テンプレート
  // （kind="user"）または出荷テンプレート（kind="shipped"）を、名前だけを
  // 手掛かりに開く。GUI は絶対パスを持たない（07 §7.3）ため、利用者
  // テンプレートは read_user_template(name)、出荷テンプレートは
  // last_template を "shipped"（config.py の既定値・唯一の非 user: 有効値）
  // へ戻したうえで read_default_template（既存の出荷解決経路・§3.5.2）を
  // 使う——専用の「出荷テンプレートを名前で読む」コマンドは無いため、この
  // 2既存コマンドの組み合わせで賄う。「利用者テンプレート一覧から開く」
  // 「照合パネルの『開く』」の両方から呼ぶ。
  const openMatchedTemplate = async (kind: "shipped" | "user", name: string) => {
    if (!confirmDiscard()) return;
    let text: string;
    try {
      if (kind === "user") {
        text = await invoke<string>("read_user_template", { name });
      } else {
        await invoke("write_config", { patch: { last_template: "shipped" } });
        text = await invoke<string>("read_default_template");
      }
      const parsed = JSON.parse(text);
      if (!parsed || !Array.isArray(parsed.faces)) {
        throw new Error("faces が無い（テンプレート JSON ではありません）");
      }
      const { fieldCount, tableCount } = toEditorState(parsed);
      resetHistory();
      setTplPath(null);   // 絶対パスを持たないテンプレート（ファイル保存ダイアログの既定にしない）
      setErrMsg(""); setWarnMsg("");
      // テンプレートを切り替えたので、直前の画像に対する様式判定（別テンプレ
      // 基準の mismatch/undecidable）は意味を持たない。ここでは画像の
      // 再照合まではしない（開き直すと loadImage が再評価する・§3.5.2 注記）。
      // 代わりに、表示中の画像とこのテンプレートの寸法だけは即座に比較して
      // 案内する（issue #72 (t)・スバル差し戻し2・ブロックはしない）
      setFormatFaces([]);
      setFormatWarnMsg(templateSwitchImageSizeNotice(imgSize, parsed.image) ?? "");
      setFormatOverride(false);
      markDirty(false);
      if (kind === "user") {
        await invoke("write_config", { patch: { last_template: `user:${name}` } }).catch(() => {});
      }
      setMsg(`テンプレート読込: ${name}（欄 ${fieldCount}・表 ${tableCount}）`);
      await refreshLoadedCounts(null);
      setMatchResult(null);
      setUserTplPanel(null);
    } catch (e) {
      setErrMsg(`テンプレートを読み込めませんでした: ${e}`);
    }
  };

  // 「利用者テンプレートから開く」パネルを開く（一覧を取得するだけ・破壊的操作なし）。
  // list_user_templates は { templates: [...], excluded: [...] } を返す
  // （gui/src-tauri/src/user_templates.rs の UserTemplateInfo/ExcludedInfo）
  const openUserTemplateList = async () => {
    if (!confirmDiscard()) return;
    try {
      const list = await invoke<{ templates: UserTemplateListEntry[]; excluded: ExcludedEntry[] }>(
        "list_user_templates");
      setUserTplPanel({ templates: list?.templates ?? [], excluded: list?.excluded ?? [], error: "" });
    } catch (e) {
      setUserTplPanel({ templates: [], excluded: [], error: `一覧を取得できませんでした: ${e}` });
    }
  };

  // 書き出し（FR-F49・設計08 §3.7）: 既存コマンドの組み合わせだけで成立する
  // （専用コマンドは増やさない）
  const exportUserTemplate = async (name: string) => {
    try {
      const text = await invoke<string>("read_user_template", { name });
      const dest = await invoke<string | null>("pick_json",
        { save: true, defaultPath: `${name}.json` });
      if (!dest) return;
      await invoke("write_text", { path: dest, content: text });
      setMsg(`書き出しました: ${dest}`);
    } catch (e) {
      setErrMsg(`書き出しに失敗しました: ${e}`);
    }
  };

  // save_user_template(name, content, overwrite) の1回呼び出し（設計08
  // §3.2.3）。Rust は staged→verify→promote を内部で通し切るため、戻り値
  // （Ok）は「呼び出し自体が失敗しなかった」ことしか意味しない——検証NGでも
  // Rust は例外を投げず verify の JSON（stdout）をそのまま返す。保存できた
  // かどうかは、既存の保存フロー（saveTemplateInner）と同じく stdout を
  // 自前でパースして check:"template" の ok を見る必要がある。
  // 同名で overwrite=false のときは Err("AlreadyExists") が返る
  // （lib.rs:1197）——ここでは真偽だけ返し、確認ダイアログの出し分けは
  // 呼び出し側に委ねる。
  const trySaveUserTemplate = async (
    name: string, content: string, overwrite: boolean,
  ): Promise<{ ok: true } | { ok: false; alreadyExists: boolean; error: string }> => {
    let stdout: string;
    try {
      stdout = await invoke<string>("save_user_template", { name, content, overwrite });
    } catch (e) {
      const msg = String(e);
      return { ok: false, alreadyExists: msg.includes("AlreadyExists"), error: msg };
    }
    const tpl = stdout.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
      .find((e) => e && e.check === "template");
    if (!tpl?.ok) {
      return { ok: false, alreadyExists: false,
        error: `コアの検証で問題が見つかりました: ${tpl?.error ?? "不明"}` };
    }
    return { ok: true };
  };

  // 取り込み（FR-F49・設計08 §3.7）: pick_json → read_text → 名前確認 →
  // save_user_template（名前検証＋verify＋promote を通る。「検証なしには
  // templates_user/ に入らない」という要件をこの1コマンドが担保する）
  const importUserTemplate = async () => {
    const p = await invoke<string | null>("pick_json", { save: false });
    if (!p) return;
    let text: string;
    let parsed: any;
    try {
      text = await invoke<string>("read_text", { path: p });
      parsed = JSON.parse(text);
      if (!parsed || !Array.isArray(parsed.faces)) {
        throw new Error("faces が無い（テンプレート JSON ではありません）");
      }
    } catch (e) {
      setErrMsg(`取り込むファイルを読み込めませんでした: ${e}`);
      return;
    }
    const base = p.replace(/^.*[\\/]/, "").replace(/\.json$/i, "");
    const name = window.prompt(
      `取り込み後のテンプレート名を入力してください。\n${USER_TEMPLATE_NAME_RULE}`, base);
    if (name === null) return;
    let res = await trySaveUserTemplate(name, text, false);
    if (!res.ok && res.alreadyExists) {
      if (!window.confirm(`同名の利用者テンプレート「${name}」が既にあります。上書きしますか？`)) return;
      res = await trySaveUserTemplate(name, text, true);
    }
    if (!res.ok) {
      setErrMsg(`取り込みに失敗しました: ${res.error}`);
      return;
    }
    // issue #72 (t)・スバル差し戻し2: 取り込みは編集中のテンプレートを
    // 差し替えない（保存するだけ）が、表示中の画像があれば寸法差だけは
    // 情報として伝える（ブロックしない・既存の formatWarnMsg 黄帯を再利用）
    const sizeNotice = templateSwitchImageSizeNotice(imgSize, parsed.image);
    if (sizeNotice) setFormatWarnMsg(sizeNotice);
    setMsg(`取り込みました（利用者テンプレート: ${name}）`);
  };

  // 不一致時の導線（FR-F30/F31・設計08 §3.6）。(b) 未実装のため到達点は
  // 「空のテンプレートで開く」まで——枠候補の一括生成はしない
  const createTemplateForThisImage = () => {
    if (!confirmDiscard()) return;
    const { W, H } = curSize();
    const empty = emptyTemplateFor(W, H, splitY);
    toEditorState(empty);
    resetHistory();
    setTplPath(null);
    setErrMsg("");
    // 直前のテンプレートに対する様式判定はもう意味を持たない（新しい
    // テンプレートは常に「一致」の定義そのものになる）
    setFormatFaces([]); setFormatWarnMsg(""); setFormatOverride(false);
    markDirty(true);   // 空でも「まだ保存していない」新規状態として扱う
    setMsg(newTemplateNotice(false));
  };

  // 確認ダイアログでキャンセルされたときの共通後始末。ファイルには一切
  // 触れていない時点で呼ぶ（issue #56 T1・T3・#59 H-1・#55: 確認なしの
  // 経路では上書きしない）
  const abortSave = (why: string) => {
    setMsg("");
    setErrMsg(`保存を中止しました（${why}）`);
  };

  // 保存前確認モーダル（issue #66 段3・FR-1.6・AC-1.23）。window.confirm 3連発
  // ＋列減少チェックを1枚のモーダルに統合する Promise ブリッジ。
  // resolve(true)=このまま保存 / resolve(false)=保存しない
  const askConfirm = (warnings: SaveWarning[]): Promise<boolean> =>
    new Promise((resolve) => setConfirmModal({ warnings, busy: false, resolve }));
  const closeConfirmModal = (proceed: boolean) => {
    setConfirmModal((m) => {
      m?.resolve(proceed);
      return null;
    });
    // フォーカスを保存ボタンへ戻す（AC-1.23）。モーダルの unmount 後に
    // 対象が存在する必要があるため次フレームへずらす
    requestAnimationFrame(() => saveBtnRef.current?.focus());
  };
  // 開いた瞬間だけ「保存しない」へ初期フォーカスする（破壊的でない側を既定・C-5）
  useEffect(() => {
    const isOpen = !!confirmModal;
    if (isOpen && !prevModalOpen.current) modalCancelRef.current?.focus();
    prevModalOpen.current = isOpen;
  }, [confirmModal]);
  // Esc=キャンセル・Tab はモーダル内で循環（AC-1.23）
  const onModalKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") { e.preventDefault(); closeConfirmModal(false); return; }
    if (e.key !== "Tab") return;
    const root = modalRef.current;
    if (!root) return;
    const focusables = Array.from(root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter((el) => !el.hasAttribute("disabled"));
    if (focusables.length === 0) return;
    const first = focusables[0], last = focusables[focusables.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  const saveTemplate = async () => {
    if (savingRef.current) return;   // 二重押下防止（C-5）。モーダルの外側の起点
    savingRef.current = true;
    try {
      await saveTemplateInner();
    } finally {
      savingRef.current = false;
    }
  };

  const saveTemplateInner = async () => {
    const p = await invoke<string | null>("pick_json",
      { save: true, defaultPath: tplPath ?? undefined });
    if (!p) return;

    // 保存先が出荷テンプレートかは、以降の確認モーダルに含めるため先に
    // 取得しておく（issue #56 T1-3）。確認そのものは verify OK が確定した
    // 後（下）に1枚のモーダルへ統合する（issue #66 段3・FR-1.6・付録A）
    const isShipped = await invoke<boolean>("is_shipped_template_path", { path: p })
      .catch(() => false);

    // 座標不変ガード（issue #66 段6・FR-2.2・AC-2.4・付録A）: 並べ替えを含む
    // 保存では resolveOverlaps の自動調整（切り抜き）を許さない。両立を許すと
    // 「並べ替えたつもりが枠まで動いていた」という気づきにくい破壊が起きうる
    // ため、before/after の座標を突き合わせて1px でもずれたら保存自体を
    // 中止する（まだファイルに何も書いていない時点なので中止コストは低い）。
    // 順序が変わっていない保存は従来どおり切り抜きを許す（このガードの対象外）
    const orderChangedNow = outputOrderChanged(loadedOrder, outputOrderSnapshot(fields, tables));
    const geomBefore = fieldGeometrySnapshot(fields);

    // 保存直前に重なりを一括解消する。ドロップ時の自動切り抜きは
    // 「置いた瞬間」にしか効かないため、開き直した下書きなど**以前から
    // 重なったままの状態**はここで拾う（ユーザー報告 2026-08-31）
    const resolved = resolveOverlaps(fields, splitY);

    if (orderChangedNow && !geometryUnchanged(geomBefore, fieldGeometrySnapshot(resolved.fields))) {
      setMsg("");
      setErrMsg(reorderCarveBlockedNotice());
      return;
    }

    // 面の範囲外に落ちた要素があれば保存を止める（issue #69 Q-H2）。画像を
    // 開き直すと H が変わり、過去の座標が範囲外になっていることがある——
    // このまま保存すると buildTemplateJson の面 filter で無言破棄され、
    // 除外領域（マスク）なら要配慮情報がそのまま Vision へ送られてしまう。
    // 「承知の上で続行」に安全な続行が無いため、保存前確認モーダルには乗せず
    // ここで止める（reorderCarveBlockedNotice と同型）
    const { W, H } = curSize();
    const outOfFace = outOfFaceElements(
      { fields: resolved.fields, tables, excls, splitY, H });
    if (outOfFace.length) {
      setMsg("");
      setErrMsg(`保存していません: 面の範囲外にある要素があります: ${outOfFace.join("、")}。`
        + "画像を開き直すと座標が範囲外になることがあります。位置を修正してから保存してください。");
      return;
    }

    if (resolved.carved.length) {
      setFields(resolved.fields);
      setSel(null);
    }
    const carveNote = resolved.carved.length
      ? `重なった欄を自動で切り抜きました: ${resolved.carved.join("、")}。`
      : "";
    if (resolved.skipped.length) {
      setErrMsg(`切り抜けない欄があります: ${resolved.skipped.join("／")}`);
    }
    // 10%以上30%未満の切り抜きは警告色で明示する（issue #59 H-4・設計書
    // U-08・D-7）。verify の警告（あれば）は成功時にここへ追記する
    const carveWarnNote = carveWarningNotice(resolved.warned);
    setWarnMsg(carveWarnNote ?? "");

    // 除外領域（Vision へ送らないマスク）が読み込み時点から劣化していないかの
    // 判定材料（issue #55・#59 QA再判定条件④）。件数減少だけでなく、同一
    // idの座標・サイズ変化（例: blackoutがy1775→1640へ135pxズレる）や
    // idの入れ替わりも検知する。resolveOverlaps は excls を変更しないので、
    // ここでの内容はカーブ前後で変わらない。実際の確認は下のモーダルで行う
    const currentExclSnapshot: ExclSnapshot[] =
      excls.map((e) => ({ id: e.id, rect: e.rect }));
    const currentExclCount = currentExclSnapshot.length;
    const exclNotice = exclusionChangeNotice(loadedExcls, currentExclSnapshot);

    const built = buildTemplateJson({ fields: resolved.fields, tables, excls, splitY,
      W, H, meta: meta.current });
    if (built.droppedCount > 0) {
      // 二重防御: 上の outOfFaceElements を通り抜けたのに面 filter で
      // 落ちた要素がある——想定外の不整合なので握り潰さず保存を止める
      setMsg("");
      setErrMsg(`保存していません: テンプレートの書き出しで ${built.droppedCount} 件の要素が`
        + "面の範囲外として除外されました（内部不整合のため保存を中止しました）。");
      return;
    }
    const content = JSON.stringify(built.template, null, 2);

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

    // 保存物をコアで検証（§8-14: エディタの JSON をコアがそのまま読めること）。
    // verify・promote・表示組み立てをそれぞれ別の try に分ける
    // （マリン最終レビュー H-1）。以前は3つとも同じ try 内にあり、
    // promote 成功後の表示組み立てで例外が起きても catch が discard_staged
    // を呼んで「保存していません」と表示していた——verify は通り、
    // 場合によっては promote も終わって本番パスへ確定済みなのに、
    // 唯一の新内容（staged）まで消してしまう嘘の失敗報告になっていた
    let tpl: any = null;
    try {
      const out = await invoke<string>("run_core_capture",
        { args: ["verify", "--template", stagedPath] });
      tpl = out.split("\n").map((l) => { try { return JSON.parse(l); } catch { return null; } })
        .find((e) => e && e.check === "template");
    } catch (e) {
      // verify の呼び出し自体が失敗。まだ何も確定していないので
      // discard_staged で掃除して「保存していません」は正しい
      await invoke("discard_staged", { path: p }).catch(() => {});
      setMsg("");
      setErrMsg(`保存していません: コアの検証に失敗しました: ${e}`);
      return;
    }

    if (!tpl?.ok) {
      // NG のときは元ファイルを無傷のまま保つ。検証NGを成功と同じ灰色の
      // 小さい文字で出すと気づかれない（レビュー D-7）ため赤帯へ出し、
      // 「保存していません」と明言する（issue #56 T1）
      await invoke("discard_staged", { path: p }).catch(() => {});
      setMsg("");
      setErrMsg("保存していません: コアの検証で問題が見つかりました: "
        + (tpl?.error ?? "不明"));
      return;
    }

    // ここまでで verify は OK＝内容の正しさは確定した。保存前確認モーダルは
    // ここで初めて出す（issue #66 段3・FR-1.6・付録A）。列数の増減判定は
    // GUI 側で再導出せず、この verify 応答（staged 側）の tpl.columns を
    // 読み込み時基準（loadedCounts.columns・同じく verify 由来）と比べる
    // だけにする（FR-0.1 の原則を保つ）。4件の⚠を1枚のモーダルへ統合し、
    // ⚠が無ければモーダルを出さずそのまま保存を続ける（C-5 の empty 状態）
    const imageSizeMismatch = (meta.current.image && imgSize
        && (meta.current.image.width !== imgSize.w
            || meta.current.image.height !== imgSize.h))
      ? { from: `${meta.current.image.width}×${meta.current.image.height}`,
          to: `${imgSize.w}×${imgSize.h}` }
      : null;
    const columnDecrease = columnDecreaseFor(loadedCounts.columns, tpl.columns);
    const warnings = saveConfirmWarnings(
      { isShipped, imageSizeMismatch, exclusionNotice: exclNotice, columnDecrease });
    if (warnings.length) {
      const proceed = await askConfirm(warnings);
      if (!proceed) {
        await invoke("discard_staged", { path: p }).catch(() => {});
        abortSave("保存前の確認でキャンセル");
        return;
      }
    }

    // ここから確定。promote は別の try に分ける——ここで例外が
    // 起きても「検証NG」でも「保存していません」でもない。lib.rs 側
    // （promote_staged）は確定の rename に失敗すると .bak からの巻き戻しを
    // 試み、戻せたかどうかを Err 文言に載せて返す。discard_staged は
    // 絶対に呼ばない（rename 失敗時に残る唯一の新内容＝staged を
    // 消してしまうため・マリン最終レビュー H-1）
    try {
      await invoke("promote_template", { path: p });
    } catch (e) {
      setMsg("");
      setErrMsg(promoteFailureNotice(p, String(e)));
      return;
    }

    // 確定は完了した＝保存済み。ここから先は表示の組み立てだけなので、
    // 例外が起きても「保存していません」と嘘をつかない（マリン最終
    // レビュー H-1 (c)）。状態の更新（保存成功の事実）は表示の try の外で
    // 先に確定させる
    setTplPath(p);
    setLoadedExcls(currentExclSnapshot);
    markDirty(false);
    if (!resolved.skipped.length) setErrMsg("");
    try {
      // 欄数と列数の対応を常に見せる（差分は「分割＋管理6列」だけ、が
      // 一目で分かるように・ユーザー指摘 2026-08-31）。除外数は verify が
      // 数えたもの（シオン担当・T4 追加予定）を優先し、無ければ保存物側の
      // 数で代える
      // tpl.columns の欠落防御（issue #65-1 穴B）。旧コア・応答破損で列数が
      // 数値以外になっても、以下の差分表示（saveDiffNote）・母集団注記
      // （unclearPopulationNote）を NaN のまま出さない——直下の
      // tpl.column_names（Array.isArray ガード）・tpl.warnings（?? []）と
      // 同じ「欠落時は安全側に倒す」方針に揃える。読み込み時基準へ
      // フォールバックすることで、比較（増減判定）は「変化なし」として続行する
      const columnsUnknown = typeof tpl.columns !== "number";
      const tplColumns = columnsUnknown ? loadedCounts.columns : tpl.columns;
      // 上の tplColumns はあくまで比較用の内部値——「今回保存したテンプレート
      // の列数」として画面に出すと、実際には取得できていない数値を事実として
      // 見せてしまう（捏造）。表示側は columnsUnknown を見て数値を出さず
      // 「列数不明」に倒す（issue #65-1 レビュー指摘 S-2・fail-visible 方針）
      const columnsText = columnsUnknown
        ? "列数不明（verify で確認してください）" : `${tplColumns} 列`;
      const split = !columnsUnknown && tpl.cells != null ? tplColumns - 6 - tpl.cells : null;
      const exclCount = tpl.exclusions ?? currentExclCount;
      // 読み込み時点との差分（issue #59 H-9・最後の検知網）。比較の両辺を
      // 必ず verify 応答（staged 側＝この tpl）に一本化する（issue #66 段0・
      // F-10 バグ修正）。読み込み時側も refreshLoadedCounts が同じ verify の
      // template チェックから取得済みなので、母集団は必ず揃う——無編集保存で
      // 「欄 14→194」のような差分が出ていたのは、読み込み時だけ GUI 側で
      // fields.length 等を数えていたため（表の列を含まない・保存時とは別物）
      const diff = saveDiffNote(
        { fields: loadedCounts.fields, amountCells: loadedCounts.amountCells,
          exclusions: loadedCounts.exclusions, columns: loadedCounts.columns },
        { fields: tpl.cells, amountCells: tpl.amount_cells, exclusions: exclCount,
          columns: tplColumns });
      // 出力しない欄の件数（issue #66 段3・保存サマリ）。列位置表示の唯一の
      // 入力源は verify（column_names）に一本化する（FR-0.1）
      const disabledCount = countOutputDisabled(resolved.fields, tables);
      const popNote = unclearPopulationNote(
        loadedCounts.columns - 6, tplColumns - 6, disabledCount);
      setLoadedCounts({ fields: tpl.cells, amountCells: tpl.amount_cells,
                        exclusions: exclCount, columns: tplColumns });
      setColumnNames(Array.isArray(tpl.column_names) ? tpl.column_names : null);
      // 出力順の基準も保存成功のたびに更新する（issue #66 段6・FR-2.2）。
      // resolved.fields／tables は今回実際に書き出した並びそのもの
      const orderNote = orderChangeReportNote(orderChangedNow, tpl.cells ?? resolved.fields.length);
      setLoadedOrder(outputOrderSnapshot(resolved.fields, tables));
      setMsg(carveNote + `保存＋コア検証 OK（`
        + (tpl.cells != null
           ? (columnsUnknown
              ? `欄 ${tpl.cells}・${columnsText}`
              : `欄 ${tpl.cells} → ${columnsText}＝欄${tpl.cells}`
                + (split ? `＋分割+${split}` : "") + `＋管理6`)
           : columnsText)
        + (tpl.amount_cells != null ? `・金額 ${tpl.amount_cells} 列` : "")
        + `・除外 ${exclCount}`
        + (disabledCount ? `・うち出力しない ${disabledCount} 欄` : "")
        + `）: ${p} ／ 読み込み時から: ${diff.text}`
        + (popNote ? ` ／ ${popNote}` : "")
        + (orderNote ? ` ／ ${orderNote}` : ""));
      // コアの verify 警告（W-1/W-2 等・設計書 U-09）。保存自体は成功して
      // いるので errbox（赤帯）ではなく warnbox（黄系）に出す。あくあ側が
      // 未実装でもフィールド欠落時は安全に無視する（`tpl.warnings ?? []`）。
      // 件数のみ出す（レビュー M-2）——毎回同じ十数件の定型文がそのまま
      // 出ると、本当に注意すべき変化が埋もれて信号にならない。詳細は
      // verify で確認できる旨を添える
      const coreWarnings: string[] = tpl.warnings ?? [];
      const coreWarnNote = coreWarnings.length
        ? `コアからの警告 ${coreWarnings.length} 件（詳細は verify で確認できます）`
        : null;
      const decreaseWarnNote = diff.decreasedLabels.length
        ? `読み込み時点より減った項目があります: ${diff.decreasedLabels.join("、")}。`
          + "意図した変更か確認してください。"
        : null;
      // 後退検知（decrease）を先頭に（レビュー M-2）——異常時にしか出ない
      // 分だけ気づいてほしい優先度が高い
      setWarnMsg([decreaseWarnNote, carveWarnNote, coreWarnNote].filter(Boolean).join(" ／ "));
    } catch (e) {
      // 表示の組み立てに失敗しても保存自体は成功している。嘘をつかない
      setMsg(`保存＋コア検証 OK: ${p}（表示の組み立てに失敗しました: ${e}）`);
    }
  };

  // issue #72 (t)・FR-F26・設計08 §3.2.3。「利用者テンプレートとして保存」。
  // ファイル保存（saveTemplateInner）と違い保存先を webview は知らない
  // （絶対パスを持てない・07 §7.3）ため、Rust の save_user_template(name,
  // content) に任せる。名前検証・staged→verify→promote は Rust の中で完結
  // する（設計08 §3.2.3 のとおり、GUI 側は保存前の validation と重複
  // 検出だけを担う）。座標不変ガード（並べ替え中の切り抜き禁止）は
  // ファイル保存フロー専用として残し、ここでは重複させない——このボタンは
  // 「新しいテンプレートとして書き出す」操作で、既存ファイルの並べ替え
  // 保存契約とは別物のため
  const saveAsUserTemplate = async () => {
    if (savingRef.current) return;
    savingRef.current = true;
    try {
      await saveAsUserTemplateInner();
    } finally {
      savingRef.current = false;
    }
  };

  const saveAsUserTemplateInner = async () => {
    const resolved = resolveOverlaps(fields, splitY);
    const { W, H } = curSize();
    const outOfFace = outOfFaceElements(
      { fields: resolved.fields, tables, excls, splitY, H });
    if (outOfFace.length) {
      setMsg("");
      setErrMsg(`保存していません: 面の範囲外にある要素があります: ${outOfFace.join("、")}。`
        + "画像を開き直すと座標が範囲外になることがあります。位置を修正してから保存してください。");
      return;
    }
    const built = buildTemplateJson(
      { fields: resolved.fields, tables, excls, splitY, W, H, meta: meta.current });
    if (built.droppedCount > 0) {
      setMsg("");
      setErrMsg(`保存していません: テンプレートの書き出しで ${built.droppedCount} 件の要素が`
        + "面の範囲外として除外されました（内部不整合のため保存を中止しました）。");
      return;
    }
    if (resolved.carved.length) { setFields(resolved.fields); setSel(null); }
    const carveNote = resolved.carved.length
      ? `重なった欄を自動で切り抜きました: ${resolved.carved.join("、")}。` : "";
    const carveWarnNote = carveWarningNotice(resolved.warned);
    if (carveWarnNote) setWarnMsg(carveWarnNote);

    const suggestedName = meta.current.template_id && meta.current.template_id !== "chouhyo-v1"
      ? meta.current.template_id : "";
    const name = window.prompt(
      `利用者テンプレートとして保存する名前を入力してください。\n${USER_TEMPLATE_NAME_RULE}`,
      suggestedName);
    if (name === null) return;   // キャンセル（pick_json のキャンセルと同じく無言で中止）

    // 上書き確認は GUI 側で先に出す（設計08 §3.2.3「Rust は名前の妥当性
    // だけを見て、確認は UI の責務」）。save_user_template(name, content,
    // overwrite=false) を先に投げ、同名が既にあれば Err("AlreadyExists")
    // が返る（lib.rs:1197）ので、そこで初めて確認ダイアログを出し
    // overwrite=true で再送する——一覧を別途取得して名寄せする必要は無い
    // （NFC 正規化・大小無視の同名判定は Rust の validate_user_template_name
    // が一箇所で担う）
    const content = JSON.stringify(built.template, null, 2);
    let res = await trySaveUserTemplate(name, content, false);
    if (!res.ok && res.alreadyExists) {
      if (!window.confirm(`同名の利用者テンプレート「${name}」が既にあります。上書きしますか？`)) {
        abortSave("同名テンプレートの上書きをキャンセル");
        return;
      }
      res = await trySaveUserTemplate(name, content, true);
    }
    if (!res.ok) {
      setMsg("");
      setErrMsg(`保存していません: ${res.error}`);
      return;
    }
    await invoke("write_config", { patch: { last_template: `user:${name}` } }).catch(() => {});
    setTplPath(null);
    markDirty(false);
    setMsg(carveNote + `利用者テンプレートとして保存しました: ${name}`);
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
  // 重なりエラーを無くす（ユーザー要望 2026-08-31）。切り抜きの可否は
  // evaluateCarve の3段階判定（issue #59 H-4・設計書 U-08）に従う。
  // 判定は閉包の fields から先に計算し、setFields は写像1回だけにする
  // （updater 内で配列に push すると StrictMode の二重実行で重複する）
  const autoCarve = (claim: Rect, ownerUid: string) => {
    const carved = new Map<string, Field>();
    const carvedNames: string[] = [];
    const warned: { id: string; reductionPct: number }[] = [];
    const skipped: string[] = [];
    for (const f of fields) {
      if (f.uid === ownerUid) continue;
      const touches = [f.rect, ...(f.extras ?? [])]
        .some((r) => _rectsTouch(r, claim));
      if (!touches) continue;
      const verdict = evaluateCarve(f, claim, splitY);
      if (verdict.tier === "skip") { skipped.push(verdict.reason); continue; }
      carved.set(f.uid, verdict.field);
      carvedNames.push(f.field_id);
      if (verdict.tier === "warn") warned.push({ id: f.field_id, reductionPct: verdict.reductionPct });
    }
    if (carved.size) {
      // Ctrl+Z の履歴は通常 400ms 静止後にしか積まれない（下の履歴 useEffect）。
      // carve 直後（400ms 以内）に押すと history.past が空で無反応だった
      // のに、トーストは「Ctrl+Z で戻せます」と案内していた（issue #61 L-5）。
      // carve 適用の直前に今のスナップショットを即時に履歴へ積んでおく。
      // carvedFields と同じ参照を snapRef にも入れておくことで、後で
      // デバウンス側の useEffect が動いても「差分なし」と判定され
      // 二重には積まれない
      if (snapRef.current) {
        history.current.past.push(snapRef.current);
        if (history.current.past.length > 100) history.current.past.shift();
        history.current.future = [];
      }
      const carvedFields = fields.map((f) => carved.get(f.uid) ?? f);
      snapRef.current = { fields: carvedFields, tables, excls, splitY };
      setFields(carvedFields);
      // carve で extras が総入れ替えになった欄を選択中だと、part の添字が
      // 存在しない/別の領域を指すようになる（issue #60 M-4）。安全側へ倒し、
      // carve された当人を選択中のときだけ選択を外す（無関係な選択までは
      // 消さない）
      if (sel && sel.type === "field" && carved.has(sel.uid)) setSel(null);
    }
    if (carvedNames.length) {
      setMsg(`重なった欄を自動で切り抜きました: ${carvedNames.join("、")}（Ctrl+Z で戻せます）`);
      markDirty(true);
    }
    // 切り抜けない欄は赤帯へ（灰色の msg に混ぜると気づかれない・D-7。
    // 保存時＝resolveOverlaps/saveTemplate と同じ表示経路に揃える）
    if (skipped.length) setErrMsg(`切り抜けない欄があります: ${skipped.join("／")}`);
    setWarnMsg(carveWarningNotice(warned) ?? "");
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

  // selHiddenByFormat（モジュール直下・純関数）の薄いラッパー。現在の
  // state を閉じ込めるだけで判定ロジックは持たない（1箇所に集約・
  // 純関数化して gui-logic でテストできるようにした・スバル差し戻し1）
  const selIsHiddenByFormat = (): boolean =>
    selHiddenByFormat(sel, fields, tables, excls,
      hiddenFaces(formatFaces, formatOverride), splitY, imgSize?.h ?? 0);

  // 点の下にある要素を**すべて**前面順で返す。先頭が従来の hit() と同じ
  // 最前面。Ctrl+クリックの循環選択（下の要素を選ぶ）が全候補を必要とする
  const hitAll = (p: { x: number; y: number }): NonNullable<Sel>[] => {
    const inR = (r: Rect) => p.x >= r.x && p.x < r.x + r.w && p.y >= r.y && p.y < r.y + r.h;
    const out: NonNullable<Sel>[] = [];
    // draw() と同じ可視集合を見る（issue #71 (a')・見えない枠を掴ませない・
    // L-Q1 の教訓）。imgSize?.h が無い（画像未読込）間は hidden が常に空になり
    // 実害は無い——canvasInteractionAllowed のガードが onDown 側で先に効く
    const imgH = imgSize?.h ?? 0;
    const hidden = hiddenFaces(formatFaces, formatOverride);
    const visFields = visibleFields(fields, hidden, splitY, imgH);
    const visTables = visibleTables(tables, hidden, splitY, imgH);
    const visExcls = visibleExcls(excls, hidden, splitY, imgH);
    for (const f of visFields) if (inR(f.rect)) out.push({ type: "field", uid: f.uid });
    for (const f of visFields)
      (f.extras ?? []).forEach((ex, i) => {
        if (inR(ex)) out.push({ type: "field", uid: f.uid, part: `extra:${i}` });
      });
    for (const f of visFields)
      if (f.fallback && inR(f.fallback))
        out.push({ type: "field", uid: f.uid, part: "fallback" });
    for (const e of visExcls) if (inR(e.rect)) out.push({ type: "excl", uid: e.uid });
    for (const t of visTables) {
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
    const isPan = e.button === 1 || e.altKey || spaceRef.current;
    if (isPan) {
      drag.current = { mode: "pan", start: { x: e.clientX, y: e.clientY },
                       extra: { ...pan } };
      return;
    }
    // 画像が無い間はキャンバス上の枠操作を無効化する（2026-09-02 ユーザー
    // 指摘・マリンレビュー H-1）。パン判定は上で確定済みなのでここでは
    // 画像の有無だけを見る。抜けるときに待ち受け状態（領域を追加・別の欄と
    // 結合・参照先の枠を描く）を必ず畳む——畳まずに return すると、この後
    // 画像を開いた直後の最初のドラッグが無言で追加領域／参照先枠になる
    // （待ち受けが残ったまま次のクリックへ進んでしまう再現条件）
    if (!canvasInteractionAllowed(hasImage, tool)) {
      setMergeTarget(null); setExTarget(null); setFbTarget(null);
      // ヒットテストはしない（枠が描かれていないので、見えない枠を当てて
      // 選ばせない・コーディネータ指摘6）が、select ツールでのクリックは
      // 選択解除の合図として尊重する。無視すると「選択中」パネルを
      // クリックだけでは閉じられなくなる
      if (tool === "select") setSel(null);
      // 案内文はキャンバスの文字（draw()）・DOM 初期表示と同じ内容にする
      // （コーディネータ指摘4）。以前の固定文言では読み上げ経路から
      // template_id・欄数・表数が消えていた
      setMsg(noImageNotice(meta.current.template_id, fields.length, tables.length).text);
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
    const { W, H } = curSize();
    // 新規に描く矩形はキャンバス範囲へクランプする（issue #69 Q-H2）。
    // クランプしないと端でドラッグを始めた枠が範囲外のまま作られ、保存時に
    // 無言で欠落する（出口を集約する4箇所の1つ）
    const norm = (a: { x: number; y: number }, b: { x: number; y: number }): Rect =>
      clampRect({
        x: Math.round(Math.min(a.x, b.x)), y: Math.round(Math.min(a.y, b.y)),
        w: Math.round(Math.abs(b.x - a.x)), h: Math.round(Math.abs(b.y - a.y)) }, W, H);
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
        // block[0]（アンカー）をキャンバス範囲へクランプしてから、そこで
        // 決まった移動量を全 block へ一様に適用する（issue #69 Q-H2）。
        // block ごとに個別クランプすると行間の相対位置が崩れるため、
        // アンカー1点をクランプして差分を全体へ伝播させる
        const target = clampRect(
          { x: Math.round(d.extra.x + dx), y: Math.round(d.extra.y + dy), w: 1, h: 1 }, W, H);
        const ddx = target.x - t.blocks[0].x;
        const ddy = target.y - t.blocks[0].y;
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
    if (selIsHiddenByFormat()) return;   // issue #71 (a')・見えない枠を動かさない
    if (sel.type === "table") {
      const t = tables.find((x) => x.uid === sel.uid);
      if (t && t.blocks[0]) {
        // block[0]（アンカー）をキャンバス範囲へクランプし、そこで決まった
        // 移動量を全 block へ一様に適用する（issue #69 Q-H2・onMove の
        // moveTable と同じ考え方）。block ごとに個別クランプすると行間の
        // 相対位置が崩れるため、アンカー1点をクランプして差分を伝播させる
        const { W, H } = curSize();
        const target = clampRect(
          { x: t.blocks[0].x + dx, y: t.blocks[0].y + dy, w: 1, h: 1 }, W, H);
        const ddx = target.x - t.blocks[0].x;
        const ddy = target.y - t.blocks[0].y;
        setTables((ts) => ts.map((x) => x.uid === sel.uid
          ? { ...x, blocks: x.blocks.map((b) => ({ ...b, x: b.x + ddx, y: b.y + ddy })) } : x));
      }
      markDirty(true);
      return;
    }
    // field（主／参照先／追加領域）・excl は目標矩形を作って applySelRect へ
    // 委譲する（issue #69 Q-H2）。旧実装はこの分岐を絶対座標の差分適用として
    // ここへ複製していたが、applySelRect が既に同じ分岐（添字無効チェック
    // 込み）を持つため重複していた——委譲することで判定を1箇所にまとめ、
    // クランプ（はみ出し防止）も自動で効くようにする
    const cur = selectedRect();
    if (!cur) return;   // 添字が無効な extra 等（旧: extraIndexValid の早期 return）
    applySelRect({ ...cur, x: cur.x + dx, y: cur.y + dy });
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
  // 1クリック=1 Undo コマ（issue #66 段7・K-M4）。通常の編集は400ms静止で
  // 1コマにまとめる下の useEffect に任せるが、並べ替えボタンは連打されても
  // 1回1回を別のコマにしたい（「3つ上げたつもりが1回のUndoで全部戻る」を
  // 防ぐ）ため、クリック時点で待たずに history へ積む
  const pushHistoryNow = (next: Snap) => {
    const prev = snapRef.current ?? { fields, tables, excls, splitY };
    history.current.past.push(prev);
    if (history.current.past.length > 100) history.current.past.shift();
    history.current.future = [];
    snapRef.current = next;
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
    // 実行タブ表示中などはここで即 return（issue #69 Q-H3）。ref は毎レンダー
    // 再代入されるため、この時点の `active` は常に最新——追加の ref は不要
    if (!active) return;
    const el = document.activeElement as HTMLElement | null;
    const tag = (el?.tagName ?? "").toLowerCase();
    // 入力欄相当の判定に isContentEditable を加える（issue #69 Q-H3）。
    // tag 判定（input/textarea/select）だけでは contentEditable 要素での
    // 編集中に Delete 等のショートカットが割り込みうる
    const typing = tag === "input" || tag === "textarea" || tag === "select"
      || !!el?.isContentEditable;
    const ka = keyAction(e,
      { active, typing, isButtonFocused: tag === "button", hasSel: !!sel });
    if (!ka) return;
    if (ka.preventDefault) e.preventDefault();
    switch (ka.action.type) {
      case "space-down":
        if (!spaceRef.current) { spaceRef.current = true; setSpaceHeld(true); }
        break;
      case "undo": undoEdit(); break;
      case "redo": redoEdit(); break;
      case "fit": fitView(); break;
      case "zoom-reset": zoomBy(1 / zoom); break;
      case "zoom-in": zoomBy(1.15); break;
      case "zoom-out": zoomBy(1 / 1.15); break;
      case "escape":
        setSel(null); setPending(null); setFbTarget(null);
        setExTarget(null); setMergeTarget(null); drag.current = null;
        break;
      case "delete": removeSel(); break;
      case "nudge":
        // 画像なしでは枠が見えない（draw() が案内文言だけを描く）ため、
        // 矢印キーで見えないまま座標だけ動いて dirty になるのを防ぐ
        // （マリンレビュー M-3）。削除は一覧からの操作として引き続き許可する
        if (hasImage) nudge(ka.action.dx, ka.action.dy);
        break;
    }
  };
  // 実行タブへ切り替わるなど非アクティブ化された瞬間の後始末（issue #69
  // Q-H3）。Space パン中にタブが切り替わると window の keyup を取りこぼし、
  // 編集タブへ戻ったときに「パン状態が固まって残る」ため、ここで解除する。
  // ドラッグ中の掴み情報（drag.current）も同様に破棄する
  useEffect(() => {
    if (!active) {
      spaceRef.current = false;
      setSpaceHeld(false);
      drag.current = null;
    }
  }, [active]);
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
    // 座標変更の出口（issue #69 Q-H2）。ここで一度クランプしておけば、
    // ドラッグ移動・リサイズ・nudge のすべてがキャンバス範囲内に収まる
    const { W, H } = curSize();
    const clamped = clampRect(r, W, H);
    if (sel.type === "field") {
      if (sel.part === "fallback")
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, fallback: clamped } : f));
      else if (sel.part?.startsWith("extra:")) {
        const i = Number(sel.part.slice(6));
        // 添字が古いまま（carve 後）だと存在しない/別の領域を指す。
        // 無効なら何もしない（issue #60 M-4）
        if (!extraIndexValid(fields.find((f) => f.uid === sel.uid), i)) return;
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, extras: (f.extras ?? []).map((ex, j) => j === i ? clamped : ex) } : f));
      } else
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? applyRectToField(f, clamped) : f));
    }
    if (sel.type === "excl")
      setExcls((es) => es.map((x) => x.uid === sel.uid ? { ...x, rect: clamped } : x));
    markDirty(true);
  };
  const updateField = (u: string, patch: Partial<Field>) => {
    setFields((fs) => fs.map((f) => f.uid === u ? { ...f, ...patch } : f)); markDirty(true);
  };
  const updateTable = (u: string, patch: Partial<Table>) => {
    setTables((ts) => ts.map((t) => t.uid === u ? { ...t, ...patch } : t)); markDirty(true);
  };
  // 出力列タブの [↑][↓]（issue #66 段7・FR-2.1・付録A）。境界（面の先頭/末尾）
  // では moveFieldOutputOrder が null を返す——ボタン側も同じ判定で disabled に
  // するので通常は呼ばれないが、防御的に何もしない
  const moveField = (uid: string, dir: "up" | "down") => {
    const next = moveFieldOutputOrder(fields, uid, dir, splitY);
    if (!next) return;
    pushHistoryNow({ fields: next, tables, excls, splitY });
    setFields(next);
    markDirty(true);
    flashRow(uid);
  };
  // .colrow の [↑][↓]（issue #66 段7・FR-2.1・AC-2.3）。表の内部列の並べ替え。
  // 移動後の行（新しい位置）を光らせるため、flash の識別子は `表uid:新index`
  // にする（列自体は uid を持たない・name は重複しうるので配列位置で識別）
  const moveTableColumn = (tableUid: string, index: number, dir: "up" | "down") => {
    const t = tables.find((x) => x.uid === tableUid);
    if (!t) return;
    const next = moveTableColumnOrder(t.columns, index, dir);
    if (!next) return;
    const nextTables = tables.map((x) => x.uid === tableUid ? { ...x, columns: next } : x);
    pushHistoryNow({ fields, tables: nextTables, excls, splitY });
    setTables(nextTables);
    markDirty(true);
    flashRow(`${tableUid}:${dir === "up" ? index - 1 : index + 1}`);
  };
  const removeSel = () => {
    if (!sel) return;
    if (selIsHiddenByFormat()) return;   // issue #71 (a')・見えない枠を削除しない
    if (sel.type === "field") {
      if (sel.part === "fallback")
        setFields((fs) => fs.map((f) => f.uid === sel.uid
          ? { ...f, fallback: undefined } : f));
      else if (sel.part?.startsWith("extra:")) {
        const i = Number(sel.part.slice(6));
        // 添字が古いまま（carve 後）だと、無検査の filter は何も消さない
        // まま markDirty だけ立てていた（issue #60 M-4）。選択自体は
        // 古いので外すが、データは変更しない
        if (!extraIndexValid(fields.find((f) => f.uid === sel.uid), i)) {
          setSel(null);
          return;
        }
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
          {/* チェック極性は「出力する」（P3-b）。JSON の output と一致させる。
              表示文言・警告は「出力しない」を使う（NFR-06） */}
          <label style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
            <input type="checkbox" checked={isOutput(f)}
              aria-label={outputCheckboxLabel(f.field_id || "（名前未設定）", isOutput(f),
                findColumnPositions(columnNames, f.field_id))}
              onChange={(e) => updateField(f.uid, { output: e.target.checked ? undefined : false })} />
            出力する{!isOutput(f) && <span className="note" style={{ marginLeft: 6 }}>（現在: 出力しない）</span>}
          </label>
          <p className="note">この枠の読み取り結果は CSV・Excel の
            「{f.field_id || "（名前未設定）"}」列{isOutput(f) && !orderChangedSinceLoad && (() => {
              const posNote = fieldColumnPositionNote(
                findColumnPositions(columnNames, f.field_id), columnNames?.length ?? null);
              return posNote ? `（${posNote}）` : "";
            })()}へ出力されます{!isOutput(f)
              ? "——ただし今は出力しない設定です（枠・読み取りは維持されます）"
              : (isOutput(f) && orderChangedSinceLoad
                  ? "——並べ替え後のため、列番号は保存して検証すると確定します" : "")}</p>
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
              {/* 押しても待ち受け状態が空振りするだけにしないよう、画像が
                  無い間は無効化する（マリンレビュー H-1）。押せたとしても
                  onDown 側のガードで次のドラッグは弾かれるが、そもそも
                  押せないほうが「なぜ効かないのか」を迷わせない */}
              <button disabled={!hasImage}
                title={hasImage ? undefined : "帳票の画像か PDF を開くと使えます"}
                onClick={() => {
                setExTarget(f.uid);
                setMsg("追加する領域を帳票上でドラッグして描いてください（Esc で中止）");
              }}>領域を追加</button>
              <button disabled={!hasImage}
                title={hasImage ? undefined : "帳票の画像か PDF を開くと使えます"}
                onClick={() => {
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
                <button disabled={!hasImage}
                  title={hasImage ? undefined : "帳票の画像か PDF を開くと使えます"}
                  onClick={() => {
                  setFbTarget(f.uid);
                  setMsg("参照先の枠を帳票上でドラッグして描いてください（Esc で中止）");
                }}>参照先の枠を描く</button>
              )}
            </>
          )}
          {/* 「参照先を削除」と並ぶため、対象を明示する（誤クリック防止） */}
          <button disabled={selIsHiddenByFormat()}
            title={selIsHiddenByFormat()
              ? "様式が違う面の欄のため編集できません（上書き表示中は編集できます）" : undefined}
            onClick={() => {
            // 参照先を選択中でも「この欄を削除」は欄ごと消す（部位に依らない）。
            // removeSel を経由しないため、隠れた面のガードもここで直接効かせる
            // （issue #71 (a')・スバル差し戻し1）
            if (selIsHiddenByFormat()) return;
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
        {(() => {
          const range = tableColumnRangeInfo(columnNames, t.table_id);
          const totalRows = t.blocks.reduce((sum, b) => sum + b.rows, 0);
          return (<>
            {range
              ? <p className="note">この表は CSV・Excel の
                  {range.first === range.last
                    ? `${range.first}列目` : `${range.first}〜${range.last}列目`}
                  （{range.count}列）を占めます。各行が {t.table_id}_行番号_列名として
                  展開されます（例: {range.exampleName} = {range.examplePosition}列目）</p>
              : <p className="note">各行×各列が CSV・Excel の
                  「{t.table_id}_行番号_列名」列（例: {t.table_id}_01_
                  {t.columns[0]?.name || "列名"}）へ1行ずつ出力されます</p>}
            {/* issue #66 段7・付録A: 列の並べ替えが影響する範囲の事前注記。
                行数は blocks から常に分かる・列数は column_names 実引き（count）
                が取れたときだけ添える */}
            <p className="note">{tableColumnReorderImpactNote(totalRows, range?.count ?? null)}
              （[↑][↓] は行を含む列全体の並びに影響します）</p>
          </>);
        })()}
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
          <div className={`colrow${flashUid === `${t.uid}:${i}` ? " flash" : ""}`} key={i}
            onMouseEnter={() => setHlCol(i)} onMouseLeave={() => setHlCol(null)}
            onFocus={() => setHlCol(i)} onBlur={() => setHlCol(null)}>
            {/* issue #66 段7・FR-2.1・AC-2.3: 220列中200列を動かす本体。同じ表の
                中で隣接する列と入れ替える（面またぎ・表またぎの概念がそもそも無い） */}
            <button type="button" className="btn" disabled={i === 0}
              title={i === 0 ? "この表の先頭列です" : undefined}
              aria-label={`${c.name || `列${i + 1}`} を1つ上へ`}
              onClick={() => moveTableColumn(t.uid, i, "up")}>↑</button>
            <button type="button" className="btn" disabled={i === t.columns.length - 1}
              title={i === t.columns.length - 1 ? "この表の末尾列です" : undefined}
              aria-label={`${c.name || `列${i + 1}`} を1つ下へ`}
              onClick={() => moveTableColumn(t.uid, i, "down")}>↓</button>
            <input className="w8" value={c.name} title="列名"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, name: e.target.value } : v) })} />
            <input className="w4" type="number" value={c.x_offset} title="x_offset"
              onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                j === i ? { ...v, x_offset: +e.target.value } : v) })} />
            <input className="w4" type="number" value={c.width} title="width"
              onChange={(e) => {
                const newWidth = +e.target.value;
                // 選択式列は width を狭める/広げるとマークが列に対して
                // ずれる。#48 の単発欄と同じ考え方で比率追従させる
                // （issue #60 M-8・GUI で作れて GUI で直せない状態を防ぐ）
                updateTable(t.uid, { columns: t.columns.map((v, j) =>
                  j === i ? { ...v, width: newWidth,
                    marks: v.kind === "choice"
                      ? remapColumnMarks(v.marks, v.width, newWidth) : v.marks } : v) });
              }} />
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
            <label style={{ flexDirection: "row", alignItems: "center", gap: 4, margin: 0 }}>
              <input type="checkbox" checked={isOutput(c)}
                aria-label={outputCheckboxLabel(c.name || `列${i + 1}`, isOutput(c),
                  orderChangedSinceLoad ? null
                    : findTableColumnPositions(columnNames, t.table_id, c.name))}
                onChange={(e) => updateTable(t.uid, { columns: t.columns.map((v, j) =>
                  j === i ? { ...v, output: e.target.checked ? undefined : false } : v) })} />
              <span className="lbl">出力</span>
            </label>
            {/* issue #66 段5・付録A: 表の内部列に「表の中で何番目／帳票では左から
                何番目」を薄く併記する。x_offset 順は列を後から足すと定義順とずれる
                ため、両方示す（column_names は使わない・表単体でローカルに求まる） */}
            <span className="note" style={{ marginLeft: 2 }}>
              {tableColumnOrderNote(t.columns, i, isOutput(c))}
            </span>
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

  // 「出力列」タブ（issue #66 段3・FR-1.8・付録A・C-1/C-2、段7・FR-2.1・FR-2.5・
  // AC-2.1〜2.3/2.5/2.8〜2.10 で並べ替えボタンを追加）。枠を1つずつクリックしないと
  // 出力対象外が分からない状態にしない。面見出しで区切り、管理6列は固定表示、
  // 表は1行ユニット（並べ替えボタンなし・[開く]のみ——表ユニットは面の
  // いちばん後ろに固定される。理由は buildTemplateJson の面オブジェクトが
  // fields→tables の順で JSON を書く構造そのものにある）。対象外行は列番号「—」。
  // [↑][↓] は同じ面（表面/裏面）の欄どうしでしか動かない——面をまたぐ隣接ボタンは
  // そもそも存在しない（AC-2.2・UI に存在しない構造で担保）
  const outputListPanel = () => {
    // issue #71 (a')・スバル差し戻し1: 出力列タブは canvas の hitAll とは別の
    // 選択経路を持っており、様式不一致で隠れている面の欄・表もクリックで
    // 選べてしまっていた（→矢印キーで動かせる／削除できる漏れ）。同じ
    // hiddenFaces を面単位で見て、隠れている面の行は選択不可＋グレー表示にする
    const hidden = hiddenFaces(formatFaces, formatOverride);
    const front = { fields: fields.filter((f) => f.rect.y < splitY),
                    tables: tables.filter((t) => t.blocks[0] && t.blocks[0].y < splitY) };
    const back = { fields: fields.filter((f) => f.rect.y >= splitY),
                   tables: tables.filter((t) => t.blocks[0] && t.blocks[0].y >= splitY) };
    const hiddenBadge = <span className="format-hidden-badge">非表示（様式不一致）</span>;
    const fieldRow = (f: Field, faceHidden: boolean) => {
      const pos = orderChangedSinceLoad ? null : findColumnPositions(columnNames, f.field_id);
      const out = isOutput(f);
      const name = f.field_id || "（名前未設定）";
      const canUp = moveFieldOutputOrder(fields, f.uid, "up", splitY) !== null;
      const canDown = moveFieldOutputOrder(fields, f.uid, "down", splitY) !== null;
      return (
        <div key={f.uid}
          className={`panel-outrow${out ? "" : " off"}${flashUid === f.uid ? " flash" : ""}${
            faceHidden ? " format-hidden" : ""}`}
          onMouseEnter={() => setHlFieldUid(f.uid)} onMouseLeave={() => setHlFieldUid(null)}
          onFocus={() => setHlFieldUid(f.uid)} onBlur={() => setHlFieldUid(null)}>
          <span className="reorder-btns">
            <button type="button" className="btn" disabled={!canUp}
              title={canUp ? undefined : "面の先頭です"}
              aria-label={`${name} を1つ上へ`}
              onClick={() => moveField(f.uid, "up")}>↑</button>
            <button type="button" className="btn" disabled={!canDown}
              title={canDown ? undefined : "面の末尾です"}
              aria-label={`${name} を1つ下へ`}
              onClick={() => moveField(f.uid, "down")}>↓</button>
          </span>
          <span className="colpos">{out ? (pos ? pos.first : "") : "—"}</span>
          <input type="checkbox" checked={out}
            aria-label={outputCheckboxLabel(name, out, pos)}
            onChange={(e) => updateField(f.uid, { output: e.target.checked ? undefined : false })} />
          <button className="name" type="button" disabled={faceHidden}
            title={faceHidden ? "様式が違う面の欄のため選択できません（上書き表示中は選べます）" : undefined}
            style={{ textAlign: "left",
              background: "none", border: "none", cursor: faceHidden ? "not-allowed" : "pointer",
              padding: 0, font: "inherit" }}
            onClick={faceHidden ? undefined
              : () => { setSel({ type: "field", uid: f.uid }); setPanelTab("selected"); }}>
            {name}
          </button>
          {faceHidden && hiddenBadge}
        </div>);
    };
    const tableRow = (t: Table, faceHidden: boolean) => (
      <div key={t.uid} className={`panel-outrow${faceHidden ? " format-hidden" : ""}`}
        onMouseEnter={() => setHlFieldUid(t.uid)} onMouseLeave={() => setHlFieldUid(null)}
        onFocus={() => setHlFieldUid(t.uid)} onBlur={() => setHlFieldUid(null)}>
        <span className="reorder-btns" title="表は面のいちばん後ろに出力されます" />
        <span className="colpos" title="表は面のいちばん後ろに出力されます">表</span>
        <span className="name">{t.table_id}（列{t.columns.length}・うち出力
          {t.columns.filter((c) => isOutput(c)).length}）</span>
        {faceHidden && hiddenBadge}
        <button className="btn" type="button" disabled={faceHidden}
          title={faceHidden ? "様式が違う面の表のため選択できません（上書き表示中は選べます）" : undefined}
          style={{ minHeight: 28, padding: "3px 10px" }}
          onClick={faceHidden ? undefined
            : () => { setSel({ type: "table", uid: t.uid }); setPanelTab("selected"); }}>開く</button>
      </div>);
    const faceSection = (
      label: string, group: { fields: Field[]; tables: Table[] }, faceId: "front" | "back") => {
      const faceHidden = hidden.has(faceId);
      return (
        <div key={label}>
          <h4>{label}{faceHidden && <> {hiddenBadge}</>}</h4>
          {group.fields.length === 0 && group.tables.length === 0
            ? <p className="note">欄がありません</p>
            : <>{group.fields.map((f) => fieldRow(f, faceHidden))}
                {group.tables.map((t) => tableRow(t, faceHidden))}</>}
        </div>);
    };
    return (
      <div className="panel">
        <h3>出力列</h3>
        <p className="note">🔒 管理6列（要確認セル数・最低信頼度・帳票ID・入力ファイル名・
          ページ番号・ステータス）は常に先頭固定で出力されます（並べ替え対象外）</p>
        {faceSection("表面", front, "front")}
        {faceSection("裏面", back, "back")}
        <p className="note">[↑][↓] で同じ面（表面/裏面）の欄どうしを並べ替えられます。
          表は面のいちばん後ろに固定され、内部の列は「開く」から並べ替えてください。
          並べ替え後は「保存して検証」で列番号が確定します</p>
      </div>);
  };

  const templateLoaded = fields.length > 0 || tables.length > 0;
  const outputDisabledTotal = countOutputDisabled(fields, tables);
  // 並べ替え（段7）で読み込み時の並びから変わっているか（段6 のガードと同じ
  // 判定）。変わっていれば、CSV・Excel の列番号（column_names 由来）の表示を
  // 一時的に省く——並べ替え直後は column_names がまだ古い並びのままなので、
  // 誤った番号を出さないため（FR-0.1）。次の保存成功で解消する
  const orderChangedSinceLoad = outputOrderChanged(loadedOrder, outputOrderSnapshot(fields, tables));
  // 様式不一致の黄帯（issue #71 (a')・FR-F04・FR-F05）。上書き中は文言を
  // 差し替え、ボタンは隠す（押し直す必要が無い・押した理由が画面に残る）
  const hasFormatMismatch = formatFaces.some((f) => f.verdict === "mismatch");
  const formatBannerText = formatOverride
    ? formatOverrideBannerText()
    : formatWarnMsg;

  return (
    <div className="editor">
      <div className="adminstrip">この画面では<b>帳票の読み取り位置（枠）を定義します</b>（管理者向け）。通常の読み取りは「実行」タブから行ってください。</div>
      <div className="toolbar">
        <button className="btn" onClick={loadImage}>帳票を開く（PDF・画像）</button>
        <button className="btn" onClick={loadTemplate}>テンプレートを開く</button>
        <button className="btn" onClick={openUserTemplateList}>利用者テンプレートから開く</button>
        <button ref={saveBtnRef} className="btn primary" onClick={saveTemplate}>保存して検証</button>
        <button className="btn" onClick={saveAsUserTemplate}>利用者テンプレートとして保存</button>
        <button className="btn" onClick={importUserTemplate}>取り込み</button>
        <span className="sep" />
        {(["select", "field", "excl", "table", "split"] as Tool[]).map((t) => {
          // select（一覧・パネルからの操作の起点）は画像なしでも押せるが、
          // それ以外はキャンバス上に枠を描く／操作するツールなので、押せても
          // 何も起きない状態を見せないよう画像が無い間は無効化する
          // （マリンレビュー H-1・onDown 側のガードと二重で塞ぐ）
          const need = t !== "select" && !hasImage;
          return (
            <button key={t} className={tool === t ? "btn active" : "btn"}
              disabled={need} title={need ? "帳票の画像か PDF を開くと使えます" : undefined}
              onClick={() => setTool(t)}>
              {{ select: "選択", field: "欄を追加", excl: "除外範囲",
                 table: "くり返し行（家族・明細）", split: "表裏の境界" }[t]}
            </button>);
        })}
        <span className="msg" role="status" aria-live="polite">
          {msg}{dirtyState ? "（未保存）" : ""}</span>
      </div>
      {errMsg && <div className="errbox" style={{ margin: "8px 18px" }}>{errMsg}</div>}
      {formatBannerText && (
        <div className="warnbox" role="status" aria-live="polite"
          style={{ margin: "8px 18px", display: "flex",
          alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap" }}>
          <span>{formatBannerText}</span>
          {/* ころね（user_advocate）の初見ユーザー予測レビュー: 2つのボタンが
              何をする操作か区別しにくかった（どちらも似た見た目・似た文言）。
              ラベルを「操作」→「結果」の形にし、各1行で効果と使いどころを
              添える（FR-F05・FR-F30/F31） */}
          {hasFormatMismatch && !formatOverride && (
            <span style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <button className="btn" onClick={() => setFormatOverride(true)}>
                  判定を無視して枠を表示する（このテンプレートのまま直す）
                </button>
                <span className="note">枠がズレて見えるときは読み取り時に自動補正されるため、
                  枠は動かさないでください</span>
              </span>
              {/* issue #72 (t)・FR-F30/F31: 不一致時の導線。(b) 未実装のため
                  到達点は「空のテンプレートで開く」まで（設計08 §3.6） */}
              <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <button className="btn" onClick={createTemplateForThisImage}>
                  この紙用に新しいテンプレートを作る
                </button>
                <span className="note">空のテンプレートから表を描き直します</span>
              </span>
            </span>
          )}
          {/* issue #72 (t)・実機通し確認の指摘: 上書き中（formatOverride）は
              判定パネルの操作ボタンが消えるため、元の判定表示（不一致面を
              隠す）へ戻す手段が画面から消えていた。押すと formatOverride を
              false に戻すだけ——formatFaces（判定結果自体）は変えない */}
          {formatOverride && (
            <button className="btn" onClick={() => setFormatOverride(false)}>
              判定に戻す
            </button>
          )}
        </div>
      )}
      {warnMsg && <div className="warnbox" style={{ margin: "8px 18px" }}>{warnMsg}</div>}
      {/* issue #72 (t)・FR-F28/F46: 照合提示（「この画像に合うテンプレート」）。
          画像を開くたびに match_templates を呼び直す（loadImage 参照）。
          現在開いているテンプレートが一致判定なら畳んでおく（matchCollapsed） */}
      {(matchLoading || matchError || matchResult) && (
        <div className="card" style={{ margin: "8px 18px" }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <b>この画像に合うテンプレート</b>
            <button type="button" className="btn"
              onClick={() => setMatchCollapsed((c) => !c)}>
              {matchCollapsed ? "表示する" : "折りたたむ"}
            </button>
          </div>
          {!matchCollapsed && (matchLoading ? (
            <p className="note">照合しています…</p>
          ) : matchError ? (
            <p className="note" style={{ color: "var(--err-ink)" }}>{matchError}</p>
          ) : matchResult && (() => {
            const ranked = rankCandidates(matchResult.candidates, matchResult.truncated);
            return (
              <>
                <p className="note">{ranked.notice}</p>
                {ranked.rows.map((c) => (
                  <div key={`${c.kind}:${c.name}`} className="panel-outrow">
                    <span>{c.name}{c.kind === "shipped" ? "（出荷）" : ""}
                      {ranked.recommend === c.name && " ★推奨"}</span>
                    <span className="note">欄{c.fields}・表{c.tables}
                      {c.updated_at ? `・更新 ${c.updated_at}` : ""}
                      {ranked.showScore ? `・スコア ${c.score.toFixed(2)}` : ""}</span>
                    <button className="btn" type="button"
                      onClick={() => openMatchedTemplate(c.kind, c.name)}>開く</button>
                  </div>
                ))}
                {matchResult.excluded.length > 0 && (
                  <p className="note">読めないテンプレート {matchResult.excluded.length} 件（
                    {matchResult.excluded.map((e) => `${e.name}: ${excludedReasonJa(e.reason)}`).join("、")}）</p>
                )}
              </>
            );
          })())}
        </div>
      )}
      <div className="editor-body">
        <canvas ref={canvasRef} className="canvas"
          style={spaceHeld ? { cursor: "grab" }
                 : !hasImage ? { cursor: "not-allowed" }
                 : hoverCursor ? { cursor: hoverCursor } : undefined}
          onMouseDown={onDown} onMouseMove={onMove} onMouseUp={onUp}
          onWheel={onWheel} onContextMenu={(e) => e.preventDefault()} />
        {/* 「選択中/出力列」の2タブ構成（issue #66 段3・FR-1.7・C-1・AC-1.24）。
            出力列タブはテンプレート未読込時 disabled（C-1 の empty 定義） */}
        <div className="panel-wrap">
          <div className="tabs panel-tabs" role="tablist" aria-label="編集パネル">
            <button type="button" role="tab" id="edittab-selected"
              aria-selected={panelTab === "selected"} aria-controls="edittabpanel"
              className={panelTab === "selected" ? "active" : ""}
              onClick={() => setPanelTab("selected")}>選択中</button>
            <button type="button" role="tab" id="edittab-output"
              aria-selected={panelTab === "output"} aria-controls="edittabpanel"
              className={panelTab === "output" ? "active" : ""}
              disabled={!templateLoaded}
              title={templateLoaded ? undefined : "テンプレートを開くと使えます"}
              onClick={() => setPanelTab("output")}>
              出力列
              {outputDisabledTotal > 0 && <span className="badge">⊘{outputDisabledTotal}</span>}
            </button>
          </div>
          <div id="edittabpanel" role="tabpanel"
            aria-labelledby={panelTab === "selected" ? "edittab-selected" : "edittab-output"}>
            {panelTab === "output" && templateLoaded ? outputListPanel() : panel()}
          </div>
        </div>
      </div>
      {confirmModal && (
        <div className="modal-back" onClick={() => closeConfirmModal(false)}>
          <div className="modal" ref={modalRef} role="alertdialog" aria-modal="true"
            aria-labelledby="save-confirm-title" onClick={(e) => e.stopPropagation()}
            onKeyDown={onModalKeyDown}>
            <h3 id="save-confirm-title">保存前の確認</h3>
            <ul style={{ margin: "0 0 16px", paddingLeft: 20, lineHeight: 1.8, fontSize: 13 }}>
              {confirmModal.warnings.map((w) => <li key={w.key}>{w.text}</li>)}
            </ul>
            <div style={{ display: "flex", gap: 10 }}>
              <button ref={modalCancelRef} type="button" className="btn"
                disabled={confirmModal.busy}
                onClick={() => closeConfirmModal(false)}>保存しない</button>
              <button type="button" className="btn primary" disabled={confirmModal.busy}
                onClick={() => {
                  setConfirmModal((m) => m && { ...m, busy: true });
                  closeConfirmModal(true);
                }}>このまま保存</button>
            </div>
          </div>
        </div>
      )}
      {/* issue #72 (t)・FR-F27/F29: 「利用者テンプレートから開く」パネル。
          list_user_templates の一覧を表示名だけで並べる（絶対パスは持たない） */}
      {userTplPanel && (
        <div className="modal-back" onClick={() => setUserTplPanel(null)}>
          <div className="modal" role="dialog" aria-modal="true"
            aria-labelledby="user-tpl-list-title" onClick={(e) => e.stopPropagation()}>
            <h3 id="user-tpl-list-title">利用者テンプレートから開く</h3>
            {userTplPanel.error && (
              <p className="note" style={{ color: "var(--err-ink)" }}>{userTplPanel.error}</p>
            )}
            {!userTplPanel.error && userTplPanel.templates.length === 0
              && userTplPanel.excluded.length === 0 && (
              <p className="note">保存済みの利用者テンプレートはまだありません。</p>
            )}
            {userTplPanel.templates.map((e) => (
              <div key={e.name} className="panel-outrow">
                <span>{e.name}</span>
                <span className="note">欄{e.fields}・表{e.tables}
                  ・更新 {new Date(e.updated_at).toLocaleString()}</span>
                <button className="btn" type="button"
                  onClick={() => openMatchedTemplate("user", e.name)}>開く</button>
                <button className="btn" type="button"
                  onClick={() => exportUserTemplate(e.name)}>書き出し</button>
              </div>
            ))}
            {userTplPanel.excluded.map((e) => (
              <div key={e.name} className="panel-outrow">
                <span>{e.name}<span className="format-hidden-badge">
                  読み込めません（{excludedReasonJa(e.reason)}）</span></span>
              </div>
            ))}
            <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
              <button type="button" className="btn" onClick={() => setUserTplPanel(null)}>閉じる</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
