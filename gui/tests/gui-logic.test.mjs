// GUI の純ロジック（座標追従・進捗イベントの文言）の単体テスト。
//
// このリポジトリの GUI にはテストランナーを入れていないため、既に依存にある
// esbuild で該当モジュールだけを束ねて node で実行する。React コンポーネント
// 本体は描画せず、export された純関数だけを対象にする。
//   実行: cd gui && node tests/gui-logic.test.mjs
import { build } from "esbuild";
import { fileURLToPath, pathToFileURL } from "node:url";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import assert from "node:assert/strict";
import path from "node:path";
import fs from "node:fs";

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(here, "..", "src");

// bridge.ts が読み込み時に `"__TAURI_INTERNALS__" in window` を評価するため、
// node 側に最小の window を用意してから束ねたモジュールを取り込む
globalThis.window = globalThis.window ?? {};

const bundle = await build({
  stdin: {
    contents:
      'export { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice } from "./Editor.tsx";\n' +
      'export { noticeFor, STATUS_JA } from "./RunScreen.tsx";\n',
    resolveDir: srcDir,
    sourcefile: "entry.ts",
    loader: "ts",
  },
  bundle: true,
  format: "esm",
  platform: "browser",
  jsx: "automatic",
  define: { "process.env.NODE_ENV": '"production"' },
  write: false,
  logLevel: "silent",
});
const outDir = mkdtempSync(path.join(tmpdir(), "chouhyo-gui-test-"));
const outFile = path.join(outDir, "bundle.mjs");
writeFileSync(outFile, bundle.outputFiles[0].text);
const { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice, noticeFor, STATUS_JA } =
  await import(pathToFileURL(outFile).href);

let failed = 0;
let passed = 0;
const t0 = Date.now();
const test = (name, fn) => {
  try {
    fn();
    passed++;
    console.log(`ok   ${name}`);
  } catch (e) {
    failed++;
    console.log(`FAIL ${name}\n     ${e.message}`);
  }
};

// ---------------------------------------------------------------- issue #48
// 出荷テンプレート templates/chouhyo-v1.json の person_生年月日_元号 と同じ形。
const ERA_RECT = { x: 1758, y: 135, w: 75, h: 148 };
const eraField = () => ({
  kind: "choice",
  rect: { ...ERA_RECT },
  marks: layoutMarks(ERA_RECT, ["昭", "平", "令"]),
});

test("#48-1 選択式の欄を平行移動すると marks が同じ差分だけ動く", () => {
  const f = eraField();
  const next = { ...ERA_RECT, x: ERA_RECT.x + 20, y: ERA_RECT.y - 13 };
  const moved = remapMarks(f, next);
  assert.equal(moved.length, 3);
  for (let i = 0; i < moved.length; i++) {
    assert.equal(moved[i].value, f.marks[i].value);
    assert.equal(moved[i].rect.x, f.marks[i].rect.x + 20);
    assert.equal(moved[i].rect.y, f.marks[i].rect.y - 13);
    assert.equal(moved[i].rect.w, f.marks[i].rect.w);
    assert.equal(moved[i].rect.h, f.marks[i].rect.h);
  }
});

test("#48-1b ドラッグ中の連続適用でも差分が累積してずれない", () => {
  // applySelRect は onMove のたびに「現在の欄」から呼ばれる。1フレームずつ
  // 進めた結果が、一度に動かした結果と一致すること
  let f = eraField();
  for (let step = 0; step < 20; step++) {
    const next = { ...f.rect, x: f.rect.x + 1, y: f.rect.y + 2 };
    f = { ...f, rect: next, marks: remapMarks(f, next) };
  }
  const once = remapMarks(eraField(),
    { ...ERA_RECT, x: ERA_RECT.x + 20, y: ERA_RECT.y + 40 });
  assert.deepEqual(f.marks, once);
  assert.equal(f.marks[0].rect.x, ERA_RECT.x + 4 + 20);
});

test("#48-2 リサイズすると marks が比率どおりに追従し、欄からはみ出さない", () => {
  const f = eraField();
  const next = { x: 1758, y: 135, w: 120, h: 300 };
  const got = remapMarks(f, next);
  assert.equal(got.length, 3);
  assert.deepEqual(got.map((m) => m.value), ["昭", "平", "令"]);
  for (const m of got) {
    assert.ok(m.rect.x >= next.x && m.rect.x + m.rect.w <= next.x + next.w,
      `x 方向が欄からはみ出す: ${JSON.stringify(m.rect)}`);
    assert.ok(m.rect.y >= next.y && m.rect.y + m.rect.h <= next.y + next.h,
      `y 方向が欄からはみ出す: ${JSON.stringify(m.rect)}`);
  }
  // 幅・高さは倍率どおり（欄が 2倍幅・約2倍高になるのでマークも追随する）
  const sx = next.w / f.rect.w, sy = next.h / f.rect.h;
  got.forEach((m, i) => {
    assert.equal(m.rect.w, Math.max(1, Math.min(next.w,
      Math.round(f.marks[i].rect.w * sx))));
    assert.equal(m.rect.h, Math.max(1, Math.min(next.h,
      Math.round(f.marks[i].rect.h * sy))));
  });
});

test("#48-2b リサイズは手で詰めた較正値を捨てない（相対位置が保たれる）", () => {
  // 出荷テンプレートのマークは layoutMarks の算出値から手でずらして較正して
  // ある（出荷テンプレートは x:+4 / y:+2〜+4 / w:-5 / h:+2）。作り直すと
  // この較正が消えるため、欄の変化を写す形で追従することを固定する
  // （issue #23 の帯較正を守る）。ここでは欄の内側に収まる範囲でずらす——
  // はみ出す較正はクランプで丸められるのが正しい挙動なので別テストにする
  const f = eraField();
  f.marks = f.marks.map((m) => ({ ...m,
    rect: { ...m.rect, x: m.rect.x + 4, y: m.rect.y + 2,
            w: m.rect.w - 5, h: m.rect.h - 2 } }));
  const next = { ...f.rect, x: f.rect.x + 30, y: f.rect.y + 10 };  // 平行移動
  const moved = remapMarks(f, next);
  moved.forEach((m, i) => {
    assert.equal(m.rect.x - next.x, f.marks[i].rect.x - f.rect.x,
      "欄に対する相対 x が変わっている（較正が失われた）");
    assert.equal(m.rect.y - next.y, f.marks[i].rect.y - f.rect.y,
      "欄に対する相対 y が変わっている（較正が失われた）");
    assert.equal(m.rect.w, f.marks[i].rect.w);
    assert.equal(m.rect.h, f.marks[i].rect.h);
  });
  // 等倍のリサイズ（w/h 据え置き）でも較正は保たれる
  const same = remapMarks(f, { ...f.rect });
  assert.deepEqual(same, f.marks);
});

test("#48-2c 移動とリサイズが同時でも欄の内側に収まる", () => {
  const f = eraField();
  const next = { x: 100, y: 200, w: 90, h: 148 };  // w が変わっている
  const got = remapMarks(f, next);
  assert.equal(got.length, 3);
  for (const m of got) {
    assert.ok(m.rect.x >= next.x && m.rect.x + m.rect.w <= next.x + next.w);
    assert.ok(m.rect.y >= next.y && m.rect.y + m.rect.h <= next.y + next.h);
  }
});

test("#48-2d 元の欄が潰れている場合は layoutMarks で作り直す", () => {
  const f = { kind: "choice", rect: { x: 10, y: 10, w: 0, h: 0 },
              marks: layoutMarks({ x: 10, y: 10, w: 40, h: 60 }, ["A", "B"]) };
  const next = { x: 10, y: 10, w: 40, h: 60 };
  assert.deepEqual(remapMarks(f, next), layoutMarks(next, ["A", "B"]));
});

test("#48-3 choice 以外・marks 空では marks に触らない", () => {
  const text = { kind: "text", rect: { ...ERA_RECT }, marks: [] };
  const moved = { ...ERA_RECT, x: 0, y: 0 };
  assert.equal(remapMarks(text, moved), text.marks);          // 同一参照＝無変更
  // kind が text のまま marks を持つ状態（種類を切り替えた直後）も触らない
  const stale = { kind: "text", rect: { ...ERA_RECT }, marks: eraField().marks };
  assert.equal(remapMarks(stale, moved), stale.marks);
  const empty = { kind: "choice", rect: { ...ERA_RECT }, marks: [] };
  assert.equal(remapMarks(empty, moved), empty.marks);
  // 変化なしの適用でも新しい配列を作らない
  const f = eraField();
  assert.equal(remapMarks(f, { ...ERA_RECT }), f.marks);
});

test("#48-4 layoutMarks は genFieldMarks の旧実装と同じ配置", () => {
  const r = { x: 10, y: 20, w: 75, h: 148 };
  const vs = ["昭", "平", "令"];
  const h = Math.floor(r.h / vs.length);
  const expected = vs.map((v, i) => ({
    value: v,
    rect: { x: r.x + 4, y: r.y + i * h + 2,
            w: Math.max(8, r.w - 8), h: Math.max(8, h - 4) },
  }));
  assert.deepEqual(layoutMarks(r, vs), expected);
  assert.deepEqual(layoutMarks(r, []), []);
});

// ---------------------------------------------------------------- issue #52
test("#52 skip_duplicate が「実行時のお知らせ」になる", () => {
  const t = noticeFor({ event: "skip_duplicate", file: "b.pdf", same_as: "a.pdf" });
  assert.ok(t, "null が返っている（イベントを捨てている）");
  assert.ok(t.includes("b.pdf") && t.includes("a.pdf"), t);
  assert.ok(t.includes("〓"), t);
});

test("#52 template_changed_resend が「実行時のお知らせ」になる", () => {
  const t = noticeFor({ event: "template_changed_resend", count: 42 });
  assert.ok(t, "null が返っている（イベントを捨てている）");
  assert.ok(t.includes("42"), t);
});

test("#52 remap_warning が「実行時のお知らせ」になる", () => {
  const t = noticeFor({ event: "remap_warning", page_id: "p0007",
                        missing_aligned_cells: 3 });
  assert.ok(t, "null が返っている（イベントを捨てている）");
  assert.ok(t.includes("p0007") && t.includes("3"), t);
});

test("#52 既存3イベントの文言は変わっていない", () => {
  assert.equal(noticeFor({ event: "skipped_unsupported", count: 2, files: ["a.txt", "b.doc"] }),
    "読み取れない形式のファイルを 2 件とばしました: a.txt、b.doc");
  assert.equal(noticeFor({ event: "stale_pages", count: 3, files: ["c.pdf"] }),
    "前回までの結果が 3 件残っています（今回の入力に無いファイル）。"
    + "出力にはその行も含まれます: c.pdf");
  assert.equal(noticeFor({ event: "source_replaced", file: "d.pdf", dropped_pages: 5 }),
    "d.pdf は前回と内容が変わっていたため、前回の結果 5 件を破棄して読み直します。");
});

test("#52 お知らせ対象外のイベントは null", () => {
  for (const ev of ["start", "page", "summary", "refused", "verify"]) {
    assert.equal(noticeFor({ event: ev }), null, ev);
  }
});

test("#52 STATUS_JA に重複スキップの日本語がある", () => {
  // 定数はコア側 core/chouhyo_ocr/render_rows.py の STATUS_DUPLICATE と対
  const ja = STATUS_JA["スキップ（重複）"];
  assert.ok(ja, "STATUS_JA に「スキップ（重複）」が無い");
  assert.ok(ja.includes("〓"), ja);
});

// ---------------------------------------------------------------- issue #60 M-2
// #46 で追加された source_renamed / rename_fallback（pipeline.py）が
// #52 M-3 の3件と一緒に拾われず default 節で捨てられていた（拾い漏れ）
test("#60 source_renamed が「実行時のお知らせ」になる（再送信はしない旨）", () => {
  const t = noticeFor({ event: "source_renamed", file: "b.pdf", was: "a.pdf", pages: 3 });
  assert.ok(t, "null が返っている（イベントを捨てている）");
  assert.ok(t.includes("a.pdf") && t.includes("b.pdf") && t.includes("3"), t);
});

test("#60 rename_fallback が「実行時のお知らせ」になる（課金に触れる）", () => {
  // コア側コメント「送信（課金）が動く分岐なので黙らない」と明言して
  // 出しているイベント。文言にも再送信・課金の趣旨が要る
  const t = noticeFor({ event: "rename_fallback", file: "b.pdf", was: "a.pdf" });
  assert.ok(t, "null が返っている（イベントを捨てている）");
  assert.ok(t.includes("a.pdf") && t.includes("b.pdf"), t);
  assert.ok(t.includes("課金"), "課金に触れていない: " + t);
});

// ---------------------------------------------------------------- issue #47
// 実行後の残量再取得はコンポーネントの副作用で、DOM を持たない node からは
// 呼べない。ここではソース上の配線だけを見張る（描画を伴う検証は手動）。
test("#47 start() の finally で runVerify を呼んでいる", () => {
  const src = fs.readFileSync(path.join(srcDir, "RunScreen.tsx"), "utf8");
  const start = src.slice(src.indexOf("const start = async"));
  const fin = start.slice(start.indexOf("} finally {"),
                          start.indexOf("const interrupt"));
  assert.ok(fin.includes("runVerify()"),
    "finally 節に runVerify() が無い（実行後に残量が更新されない）");
});


// --- 配線の検証（レビュー4巡目）------------------------------------------
// remapMarks 単体のテストは充実しているが、issue #48 の本体は「欄へ rect を
// 適用する経路がマークを連れて動くか」。applyRectToField を経由させることで
// 呼び忘れ（= 元バグ）を検出できる。
test("#48-wiring: applyRectToField は rect とマークを同時に動かす", () => {
  const f = eraField();
  const next = { ...f.rect, x: f.rect.x + 40, y: f.rect.y - 15 };
  const got = applyRectToField(f, next);
  assert.deepEqual(got.rect, next);
  assert.notDeepEqual(got.marks, f.marks, "マークが旧位置に取り残されている");
  assert.deepEqual(got.marks, remapMarks(f, next));
  for (const m of got.marks) {
    assert.ok(m.rect.x >= next.x && m.rect.x + m.rect.w <= next.x + next.w,
      `マーク ${m.value} が欄からはみ出した`);
    assert.ok(m.rect.y >= next.y && m.rect.y + m.rect.h <= next.y + next.h,
      `マーク ${m.value} が欄からはみ出した`);
  }
});

test("#48-wiring: choice 以外は marks を触らない（同一参照）", () => {
  const text = { uid: "t", kind: "text", rect: { x: 0, y: 0, w: 10, h: 10 }, marks: [] };
  const moved = { x: 5, y: 5, w: 10, h: 10 };
  assert.equal(applyRectToField(text, moved).marks, text.marks);
});


// --- リサイズハンドル（レビュー4巡目後のユーザー指摘対応）------------------
const R = { x: 100, y: 200, w: 60, h: 40 };

test("handle: 角と辺の8方向を検出し、遠い点は null", () => {
  assert.equal(handleAt(R, { x: 100, y: 200 }, 6), "nw");
  assert.equal(handleAt(R, { x: 160, y: 240 }, 6), "se");
  assert.equal(handleAt(R, { x: 130, y: 200 }, 6), "n");
  assert.equal(handleAt(R, { x: 100, y: 220 }, 6), "w");
  assert.equal(handleAt(R, { x: 163, y: 243 }, 6), "se");  // 外側からも掴める
  assert.equal(handleAt(R, { x: 130, y: 220 }, 6), null);  // 中央は移動
});

test("resize: se は伸ばし、nw は位置ごと動く", () => {
  assert.deepEqual(resizeBy(R, "se", 10, 5), { x: 100, y: 200, w: 70, h: 45 });
  assert.deepEqual(resizeBy(R, "nw", 10, 5), { x: 110, y: 205, w: 50, h: 35 });
  assert.deepEqual(resizeBy(R, "e", -10, 999), { x: 100, y: 200, w: 50, h: 40 });
  assert.deepEqual(resizeBy(R, "n", 999, 10), { x: 100, y: 210, w: 60, h: 30 });
});

test("resize: 反転させない（最小5pxで止まり w/h が負にならない）", () => {
  const a = resizeBy(R, "se", -200, -200);
  assert.deepEqual(a, { x: 100, y: 200, w: 5, h: 5 });
  const b = resizeBy(R, "nw", 200, 200);
  assert.equal(b.w, 5); assert.equal(b.h, 5);
  assert.equal(b.x, R.x + R.w - 5, "左ハンドルは右端を固定して詰める");
  assert.equal(b.y, R.y + R.h - 5);
});

test("resize: choice 欄は applyRectToField 経由でマークも追従する", () => {
  const f = eraField();
  const next = resizeBy(f.rect, "se", 40, 20);
  const got = applyRectToField(f, next);
  for (const m of got.marks) {
    assert.ok(m.rect.x >= next.x && m.rect.x + m.rect.w <= next.x + next.w);
    assert.ok(m.rect.y >= next.y && m.rect.y + m.rect.h <= next.y + next.h);
  }
});

// --- 重なった枠の循環選択（Ctrl+クリック）----------------------------------
test("overlap: 未選択なら最前面・選択中なら1つ下・末尾から先頭へ循環", () => {
  const a = { type: "field", uid: "a" };
  const b = { type: "excl", uid: "b" };
  const c = { type: "table", uid: "c" };
  const list = [a, b, c];
  assert.deepEqual(nextOverlapPick(list, null), a);
  assert.deepEqual(nextOverlapPick(list, a), b);
  assert.deepEqual(nextOverlapPick(list, b), c);
  assert.deepEqual(nextOverlapPick(list, c), a);            // 循環
  assert.deepEqual(nextOverlapPick(list, { type: "field", uid: "zzz" }), a);
  assert.equal(nextOverlapPick([], a), null);
});

test("overlap: 参照先（part）は主と別の候補として区別される", () => {
  const main = { type: "field", uid: "f1" };
  const fb = { type: "field", uid: "f1", part: "fallback" };
  assert.deepEqual(nextOverlapPick([main, fb], main), fb);
  assert.deepEqual(nextOverlapPick([main, fb], fb), main);
});

// --- 欄の結合（L字化）------------------------------------------------------
test("absorb: B の全領域が A の追加領域になる", () => {
  const A = { uid: "a", field_id: "住所", kind: "text",
              rect: { x: 0, y: 0, w: 100, h: 40 }, marks: [] };
  const B = { uid: "b", field_id: "番地", kind: "text",
              rect: { x: 0, y: 50, w: 60, h: 40 }, marks: [],
              extras: [{ x: 70, y: 50, w: 30, h: 40 }] };
  const m = absorbField(A, B);
  assert.equal(typeof m, "object");
  assert.deepEqual(m.extras, [B.rect, ...B.extras]);
  assert.equal(m.field_id, "住所");   // 名前は取り込む側が残る
});

test("absorb: 選択式・自分自身・参照先つきは結合できない", () => {
  const T = (o) => ({ uid: "x", field_id: "x", kind: "text",
                      rect: { x: 0, y: 0, w: 10, h: 10 }, marks: [], ...o });
  assert.equal(typeof absorbField(T({}), T({})), "string");                  // 同一 uid
  assert.equal(typeof absorbField(T({ uid: "a" }), T({ uid: "b", kind: "choice" })), "string");
  assert.equal(typeof absorbField(T({ uid: "a", kind: "choice" }), T({ uid: "b" })), "string");
  assert.equal(typeof absorbField(
    T({ uid: "a" }),
    T({ uid: "b", fallback: { x: 90, y: 0, w: 5, h: 5 } })), "string");
});

// --- 自動切り抜き（重なった枠が勝ち、下の文字欄がL字になる）----------------
test("subtract: 重なりなしは元のまま・中央くり抜きは上下左右の4帯", () => {
  const R = { x: 0, y: 0, w: 100, h: 100 };
  assert.deepEqual(subtractRect(R, { x: 200, y: 0, w: 10, h: 10 }), [R]);
  const four = subtractRect(R, { x: 30, y: 30, w: 40, h: 40 });
  assert.equal(four.length, 4);
  const area = four.reduce((a, r) => a + r.w * r.h, 0);
  assert.equal(area, 100 * 100 - 40 * 40, "面積が保存されない（隙間か重複がある）");
});

test("subtract: 完全に覆うと空・細片（minSize 未満）は捨てる", () => {
  const R = { x: 0, y: 0, w: 100, h: 100 };
  assert.deepEqual(subtractRect(R, { x: -5, y: -5, w: 200, h: 200 }), []);
  // 右に 3px しか残らない → 捨てられ、上下も無し → 空
  assert.deepEqual(subtractRect(R, { x: 0, y: 0, w: 97, h: 100 }), []);
});

test("carve: 最大の断片が主になり、残りは領域・全滅は null", () => {
  const F = { uid: "f", field_id: "住所", kind: "text",
              rect: { x: 0, y: 0, w: 300, h: 100 }, marks: [] };
  // 左端 60px を奪う → 主は右側の大きい断片になる
  const c = carveField(F, { x: 0, y: 0, w: 60, h: 100 });
  assert.equal(c.rect.x, 60);
  assert.equal(c.rect.w, 240);
  assert.deepEqual(c.extras, []);
  assert.equal(carveField(F, { x: -1, y: -1, w: 302, h: 102 }), null);
});

test("carve: 既存の領域も含めて切り直す", () => {
  const F = { uid: "f", field_id: "x", kind: "text",
              rect: { x: 0, y: 0, w: 100, h: 40 }, marks: [],
              extras: [{ x: 0, y: 50, w: 100, h: 40 }] };
  const c = carveField(F, { x: 40, y: -5, w: 20, h: 200 });  // 縦に貫通
  // 各領域が左右に割れて計4断片。主は最大断片
  const all = [c.rect, ...c.extras];
  assert.equal(all.length, 4);
  for (const r of all) assert.ok(r.x + r.w <= 40 || r.x >= 60);
});

// --- 保存時の一括解消（既存の重なりにも効く）-------------------------------
test("resolve: 参照先の主張が他の文字欄を切り抜く・持ち主は無傷", () => {
  const postal = { uid: "p", field_id: "郵便番号", kind: "text",
                   rect: { x: 0, y: 100, w: 50, h: 30 }, marks: [],
                   fallback: { x: 100, y: 0, w: 80, h: 40 } };
  const addr = { uid: "a", field_id: "住所", kind: "text",
                 rect: { x: 100, y: 0, w: 300, h: 40 }, marks: [] };
  const r = resolveOverlaps([postal, addr]);
  assert.deepEqual(r.carved, ["住所"]);
  assert.deepEqual(r.skipped, []);
  const a2 = r.fields.find((f) => f.uid === "a");
  assert.equal(a2.rect.x, 180, "参照先ゾーンが住所から切り抜かれていない");
  const p2 = r.fields.find((f) => f.uid === "p");
  assert.deepEqual(p2.rect, postal.rect, "主張した側が削られている");
  assert.deepEqual(p2.fallback, postal.fallback);
});

test("resolve: 選択式は切り抜けず skipped に載る・重なりなしは何もしない", () => {
  const owner = { uid: "o", field_id: "o", kind: "text",
                  rect: { x: 0, y: 100, w: 50, h: 30 }, marks: [],
                  fallback: { x: 100, y: 0, w: 80, h: 40 } };
  const era = { uid: "e", field_id: "元号", kind: "choice",
                rect: { x: 120, y: 10, w: 40, h: 20 }, marks: [] };
  const r = resolveOverlaps([owner, era]);
  assert.deepEqual(r.carved, []);
  // issue #59 H-4（設計書 U-08）以降、skipped は理由が分かる文言になる
  assert.deepEqual(r.skipped, ["元号: 選択式のため自動調整できません"]);
  const clean = resolveOverlaps([owner]);
  assert.deepEqual(clean.carved, []);
  assert.equal(clean.fields[0], owner);
});

// ---------------------------------------------------------------- issue #59 H-3
// resolveOverlaps: 主枠も主張になる・主張は逐次カーブ後の最新状態から取り直す
test("resolve-H3-①: 主枠どうしの重なりも autoCarve と同様に解消される", () => {
  // 矢印キーでの移動は autoCarve を通らないため、以前は保存時の一括解消
  // （参照先・追加領域のみが主張）でも重なりが残ったままコア検証まで
  // 落ちていた（issue #59 H-3 前段）。主枠自体を主張に含めることで
  // ドロップ時と同じ解消力にする
  const A = { uid: "a", field_id: "A", kind: "text",
              rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [] };
  const B = { uid: "b", field_id: "B", kind: "text",
              rect: { x: 60, y: 0, w: 100, h: 40 }, marks: [] };
  const r = resolveOverlaps([A, B]);
  assert.deepEqual(r.carved, ["B"], "A の主枠の主張で B が切り抜かれるはず");
  assert.deepEqual(r.skipped, []);
  const a2 = r.fields.find((f) => f.uid === "a");
  const b2 = r.fields.find((f) => f.uid === "b");
  assert.deepEqual(a2.rect, A.rect, "主張した側（先に定義した A）は無傷");
  assert.deepEqual(b2.rect, { x: 80, y: 0, w: 80, h: 40 },
    "B は A の主枠に食われた分だけ右へ詰まるはず");
});

test("resolve-H3-②: 消えた旧領域が無関係な第三の欄を削らない（stale claim 根治）", () => {
  // X の参照先(fallback)が B の主枠の右端をわずかに切り抜く。切り取り線が
  // B の右端ぎりぎり（残り2px）に掛かるため、その2pxは subtractRect の
  // minSize 未満で捨てられる——X の主張が直接触れていない場所（x:998〜1000）
  // が、X にも新しいBにも属さない「宙に浮いた領域」になる。B の主張は
  // post-carve の新しい形（x:0〜950）だけになるはずなので、その宙に浮いた
  // 領域にある無関係な第三の欄 C（x:998〜1098・X の主張の範囲 x:950〜998
  // には掛からない）は、もう誰の主張にも触れないはず。claims を最初に
  // 一括収集する実装（fix 前）だと、B の旧主枠 {x:0,y:0,w:1000,h:100}
  // がそのまま主張として残り、C を誤って削ってしまう。
  // （数値は issue #59 H-4／設計書 U-08 の30%ルール・27×36px下限を跨がない
  // 大きさに調整してある——欄自体が小さすぎると別ルールで skip されてしまう）
  const X = { uid: "x", field_id: "X", kind: "text",
              rect: { x: 5000, y: 5000, w: 50, h: 50 }, marks: [],
              fallback: { x: 950, y: -10, w: 48, h: 120 } };
  const B = { uid: "b", field_id: "B", kind: "text",
              rect: { x: 0, y: 0, w: 1000, h: 100 }, marks: [] };
  const C = { uid: "c", field_id: "C", kind: "text",
              rect: { x: 998, y: 0, w: 100, h: 100 }, marks: [] };
  const r = resolveOverlaps([X, B, C]);
  assert.deepEqual(r.carved, ["B"], "C は削られてはいけない（stale claim なら誤って carved に入る）");
  assert.deepEqual(r.skipped, []);
  const c2 = r.fields.find((f) => f.uid === "c");
  assert.deepEqual(c2.rect, C.rect, "C は無傷のはず（X の参照先の実際の範囲には触れていない）");
  const b2 = r.fields.find((f) => f.uid === "b");
  assert.deepEqual(b2.rect, { x: 0, y: 0, w: 950, h: 100 });
  assert.deepEqual(b2.extras ?? [], []);
});

// ---------------------------------------------------------------- issue #59 H-4（設計書 U-08）
// evaluateCarve: 切り抜きの3段階判定（エディタ側の事前確認）
test("evaluateCarve: 29%は通る（warn・切り抜く）・30%は止まる（skip）という境界", () => {
  const F = { uid: "f", field_id: "境界欄", kind: "text",
              rect: { x: 0, y: 0, w: 100, h: 100 }, marks: [] };
  const at29 = evaluateCarve(F, { x: 71, y: -10, w: 40, h: 120 });
  assert.equal(at29.tier, "warn", "29%はまだ切り抜かれるはず（10%以上30%未満）");
  assert.equal(at29.reductionPct, 29);
  assert.deepEqual(at29.field.rect, { x: 0, y: 0, w: 71, h: 100 });

  const at30 = evaluateCarve(F, { x: 70, y: -10, w: 41, h: 120 });
  assert.equal(at30.tier, "skip", "30%は自動調整しないはず");
  assert.ok(at30.reason.includes("境界欄") && at30.reason.includes("30%"), at30.reason);
});

test("evaluateCarve: 10%未満は auto（減少率を持たない）", () => {
  const F = { uid: "f", field_id: "微小欄", kind: "text",
              rect: { x: 0, y: 0, w: 100, h: 100 }, marks: [] };
  // 左5%を削る
  const v = evaluateCarve(F, { x: -10, y: -10, w: 15, h: 120 });
  assert.equal(v.tier, "auto");
  assert.deepEqual(v.field.rect, { x: 5, y: 0, w: 95, h: 100 });
});

test("evaluateCarve: 減少率が30%未満でも、切り抜き後が最小サイズ未満なら skip", () => {
  // 幅30pxの細長い欄から4px削ると、面積の減少率は13%程度でも残り幅が
  // 27px（U-08 の下限）を割り込む。%だけでは検知できないケース
  const f = { uid: "f", field_id: "細長欄", kind: "text",
              rect: { x: 0, y: 0, w: 30, h: 100 }, marks: [] };
  const claim = { x: 26, y: -10, w: 20, h: 120 };
  const v = evaluateCarve(f, claim);
  assert.equal(v.tier, "skip");
  assert.ok(v.reason.includes("細長欄"), v.reason);
  assert.ok(v.reason.includes("最小サイズ") && v.reason.includes("27"), v.reason);
});

test("evaluateCarve: 選択式・完全被覆は面積によらず skip", () => {
  const choice = { uid: "c", field_id: "元号", kind: "choice",
                   rect: { x: 0, y: 0, w: 200, h: 200 }, marks: [] };
  const tiny = evaluateCarve(choice, { x: 0, y: 0, w: 1, h: 1 });
  assert.equal(tiny.tier, "skip");
  assert.ok(tiny.reason.includes("選択式"), tiny.reason);

  const text = { uid: "t", field_id: "住所", kind: "text",
                 rect: { x: 0, y: 0, w: 50, h: 50 }, marks: [] };
  const covered = evaluateCarve(text, { x: -5, y: -5, w: 100, h: 100 });
  assert.equal(covered.tier, "skip");
  assert.ok(covered.reason.includes("完全に覆われる"), covered.reason);
});

test("carveWarningNotice: 対象なしは null・欄名と減少率を含み4件超は集約", () => {
  assert.equal(carveWarningNotice([]), null);
  const one = carveWarningNotice([{ id: "住所", reductionPct: 18 }]);
  assert.ok(one.includes("住所") && one.includes("18%"), one);
  const many = carveWarningNotice([
    { id: "a", reductionPct: 12 }, { id: "b", reductionPct: 15 },
    { id: "c", reductionPct: 20 }, { id: "d", reductionPct: 25 },
  ]);
  assert.ok(many.includes("ほか 1 件"), many);
});

// T-25（04_unclear_policy.md §12 テスト観点）: resolveOverlaps に減少率
// 5% / 20% / 40% になる claim を複数欄へ同時に与える
test("resolve-T25: 切り抜きの3段が resolveOverlaps でも同じ閾値で効く（複数欄）", () => {
  const mkOwner = (uid, fbX) => ({
    uid, field_id: uid, kind: "text",
    rect: { x: 9000 + fbX, y: 9000, w: 10, h: 10 }, marks: [],  // 無関係な場所
    fallback: { x: fbX, y: -10, w: 40, h: 120 } });
  // V5: 100幅から5%（5px）だけ削る → auto
  const V5 = { uid: "v5", field_id: "V5", kind: "text",
               rect: { x: 0, y: 0, w: 100, h: 100 }, marks: [] };
  const O5 = mkOwner("o5", 95);
  // V20: 100幅から20%（20px）削る → warn
  const V20 = { uid: "v20", field_id: "V20", kind: "text",
                rect: { x: 300, y: 0, w: 100, h: 100 }, marks: [] };
  const O20 = mkOwner("o20", 380);
  // V40: 100幅から40%（40px）削る → skip
  const V40 = { uid: "v40", field_id: "V40", kind: "text",
                rect: { x: 600, y: 0, w: 100, h: 100 }, marks: [] };
  const O40 = mkOwner("o40", 660);

  const r = resolveOverlaps([O5, V5, O20, V20, O40, V40]);
  assert.ok(r.carved.includes("V5") && r.carved.includes("V20"),
    "5%・20%はどちらも切り抜かれる（carved）はず: " + JSON.stringify(r.carved));
  assert.ok(!r.carved.includes("V40"), "40%は切り抜かれてはいけない");
  assert.deepEqual(r.warned, [{ id: "V20", reductionPct: 20 }],
    "警告色で出すべきは20%（10%以上30%未満）だけ");
  assert.equal(r.skipped.length, 1);
  assert.ok(r.skipped[0].includes("V40") && r.skipped[0].includes("30%"), r.skipped[0]);
});

// ---------------------------------------------------------------- issue #59 H-2
// evaluateCarve: splitY をまたぐ切り抜き（表裏の面移動）は自動調整しない
test("evaluateCarve: splitY 直上の欄が切り抜きで下断片主体になると面またぎで skip", () => {
  const splitY = 200;
  // front面（y<200）に属する欄。上側を削ると残る断片が y>=200（back面）に
  // 落ちる——carveField は面を意識せず最大断片を主 rect にするだけなので、
  // 面が黙って front→back へ移る（issue #59 H-2）
  const F = { uid: "f", field_id: "境界欄", kind: "text",
              rect: { x: 0, y: 190, w: 100, h: 100 }, marks: [] };
  const claim = { x: -10, y: 150, w: 120, h: 55 };  // y:150〜205 を削る
  const withoutSplit = evaluateCarve(F, claim);
  assert.notEqual(withoutSplit.tier, "skip",
    "splitY を渡さない場合は面判定をしない（減少率15%相当で本来は warn）");
  const withSplit = evaluateCarve(F, claim, splitY);
  assert.equal(withSplit.tier, "skip", "面をまたぐなら減少率に関わらず skip のはず");
  assert.ok(withSplit.reason.includes("境界欄") && withSplit.reason.includes("面をまたぐ"),
    withSplit.reason);
  assert.ok(withSplit.reason.includes("CSV"), withSplit.reason);
});

test("evaluateCarve: splitY を渡しても面が変わらない切り抜きはそのまま", () => {
  const splitY = 200;
  const F = { uid: "f", field_id: "同面欄", kind: "text",
              rect: { x: 0, y: 0, w: 100, h: 100 }, marks: [] };  // front のまま
  const v = evaluateCarve(F, { x: -10, y: -10, w: 15, h: 120 }, splitY);
  assert.equal(v.tier, "auto");
});

test("resolve: splitY を渡すと保存時の一括解消でも面またぎ切り抜きを止める", () => {
  const splitY = 200;
  const X = { uid: "x", field_id: "X", kind: "text",
              rect: { x: 500, y: 500, w: 10, h: 10 }, marks: [],
              fallback: { x: -10, y: 150, w: 120, h: 55 } };
  const F = { uid: "f", field_id: "境界欄", kind: "text",
              rect: { x: 0, y: 190, w: 100, h: 100 }, marks: [] };
  const r = resolveOverlaps([X, F], splitY);
  assert.deepEqual(r.carved, []);
  assert.equal(r.skipped.length, 1);
  assert.ok(r.skipped[0].includes("境界欄") && r.skipped[0].includes("面をまたぐ"), r.skipped[0]);
  const f2 = r.fields.find((v) => v.uid === "f");
  assert.deepEqual(f2.rect, F.rect, "skip されたので欄は無傷のはず");
});

// ---------------------------------------------------------------- issue #59 H-9
test("saveDiffNote: 減少を検知し、不変なら静か（増減なしは単一の数値のまま）", () => {
  const loaded = { fields: 194, amountCells: 28, exclusions: 9 };
  const decreased = saveDiffNote(loaded, { fields: 193, amountCells: 28, exclusions: 9 });
  assert.ok(decreased.text.includes("欄 194 → 193（-1）"), decreased.text);
  assert.ok(decreased.text.includes("金額 28"), decreased.text);
  assert.ok(!decreased.text.includes("金額 28 →"), "不変の項目には矢印を付けない: " + decreased.text);
  assert.deepEqual(decreased.decreasedLabels, ["欄"]);

  const quiet = saveDiffNote(loaded, loaded);
  assert.equal(quiet.text, "欄 194・金額 28・除外 9");
  assert.deepEqual(quiet.decreasedLabels, [], "不変時は静か（警告対象なし）");

  const increased = saveDiffNote(loaded, { fields: 195, amountCells: 28, exclusions: 9 });
  assert.ok(increased.text.includes("欄 194 → 195（+1）"), increased.text);
  assert.deepEqual(increased.decreasedLabels, [], "増加は減少扱いしない");
});

// ---------------------------------------------------------------- issue #66 段0（F-10 バグ修正・AC-0.2 相当）
// 母集団を verify 応答（core の template チェック: cells/amount_cells/
// exclusions）へ一本化すると、無編集保存で差分ゼロになる（saveDiffNote 自体は
// 既に「両辺が同じ数なら静か」だが、旧実装は読み込み時だけ GUI 側で
// fields.length（単発欄のみ・表の列を含まない）を数えており、保存時の
// tpl.cells（行展開後の全セル数）と母集団が違うために「欄 14→194」のような
// 差分が無編集でも出ていた。この回帰を、母集団を両辺とも core の verify
// 応答由来の数に揃えた場合の結果として固定する
test("saveDiffNote: 両辺を verify 応答（core の cells/amount_cells）に揃えると無編集保存は差分ゼロ（F-10）", () => {
  // 実測値（04_unclear_policy.md §1.2）: 出荷テンプレは cells=194・
  // amount_cells=28・除外9。読み込み時・保存時の両方が同じ verify 応答から
  // 取った数値であれば、テンプレを一切編集していない保存はこの値が
  // そのまま両辺に来る
  const verifyResponseAtLoad = { fields: 194, amountCells: 28, exclusions: 9 };
  const verifyResponseAtSave = { fields: 194, amountCells: 28, exclusions: 9 };
  const diff = saveDiffNote(verifyResponseAtLoad, verifyResponseAtSave);
  assert.equal(diff.text, "欄 194・金額 28・除外 9",
    "無編集保存なのに矢印つきの差分が出ている（F-10 の再発）: " + diff.text);
  assert.deepEqual(diff.decreasedLabels, []);

  // 対比: 旧実装のように読み込み時だけ「単発欄のみ」の小さい数（例: 14）を
  // 使うと、無編集でも巨大な差分が出ていた——これが直した不具合そのもの
  const buggyLoadedSnapshot = { fields: 14, amountCells: 1, exclusions: 9 };
  const buggyDiff = saveDiffNote(buggyLoadedSnapshot, verifyResponseAtSave);
  assert.ok(buggyDiff.text.includes("欄 14 → 194"),
    "母集団が揃っていなければ巨大な差分になる（旧バグの再現・比較用）: " + buggyDiff.text);
});

// ---------------------------------------------------------------- issue #60 M-8
test("remapColumnMarks: width 変更で choice 列のマークが比率追従し、境界を超えない", () => {
  const CHOICE_MARK_MARGIN_PX = 4;  // template.py の許容マージン
  const marks = [
    { value: "昭", x_offset: 0, width: 33 },
    { value: "平", x_offset: 33, width: 33 },
    { value: "令", x_offset: 66, width: 33 },
  ];
  const oldWidth = 100;
  for (const newWidth of [60, 150, 27]) {
    const got = remapColumnMarks(marks, oldWidth, newWidth);
    const maxEdge = Math.max(...got.map((m) => m.x_offset + m.width));
    assert.ok(maxEdge <= newWidth + CHOICE_MARK_MARGIN_PX,
      `newWidth=${newWidth}: マークが列の外へ${maxEdge - newWidth}pxはみ出た`);
    assert.equal(got.length, 3);
    got.forEach((m, i) => assert.equal(m.value, marks[i].value, "value は変えない"));
  }
});

test("remapColumnMarks: x_offset（列の位置）だけの変化・幅0のマークは対象外", () => {
  const marks = [{ value: "昭", x_offset: 10, width: 20 }];
  // widthが変わらなければ元の参照のまま返す
  assert.equal(remapColumnMarks(marks, 100, 100), marks);
  assert.deepEqual(remapColumnMarks([], 100, 50), []);
});

// ---------------------------------------------------------------- issue #60 M-4
test("extraIndexValid: 範囲内のみ true・存在しない/負の添字は false", () => {
  const f = { extras: [{ x: 0, y: 0, w: 1, h: 1 }, { x: 1, y: 1, w: 1, h: 1 }] };
  assert.equal(extraIndexValid(f, 0), true);
  assert.equal(extraIndexValid(f, 1), true);
  assert.equal(extraIndexValid(f, 2), false, "carve で extras が減った後の古い添字");
  assert.equal(extraIndexValid(f, -1), false);
  assert.equal(extraIndexValid(f, 1.5), false, "整数でない添字");
  assert.equal(extraIndexValid({ extras: [] }, 0), false);
  assert.equal(extraIndexValid(undefined, 0), false, "欄自体が見つからない（結合等で消えた）");
});

// ---------------------------------------------------------------- issue #55
test("exclusionRegressionNotice: 除外数が減っていれば確認文言・減っていなければ null", () => {
  assert.equal(exclusionRegressionNotice(7, 7), null, "同数は確認不要");
  assert.equal(exclusionRegressionNotice(3, 5), null, "増える分には確認不要");
  const notice = exclusionRegressionNotice(7, 3);
  assert.ok(notice, "減っていれば null 以外を返すはず");
  assert.ok(notice.includes("7") && notice.includes("3"), notice);
  assert.ok(notice.includes("除外領域"), notice);
});

// issue #59 QA再判定条件④: 「数」だけでなく「座標」の変化も検知する
test("exclusionChangeNotice: 135pxズレ（blackout実測相当）を件数不変のまま検知する", () => {
  // 実測: templates/chouhyo-v1.json（出荷版）の blackout は y=1775 だが、
  // エディタ保存の劣化版9件は y=1640（w/h は 475×105 のまま）だった
  const loaded = [{ id: "blackout", rect: { x: 1955, y: 1775, w: 475, h: 105 } }];
  const current = [{ id: "blackout", rect: { x: 1955, y: 1640, w: 475, h: 105 } }];
  const notice = exclusionChangeNotice(loaded, current);
  assert.ok(notice, "座標が変わっているのに null が返っている（件数比較だけでは検知できない）");
  assert.ok(notice.includes("blackout"), notice);
  assert.ok(notice.includes("1955,1775,475,105") && notice.includes("1955,1640,475,105"),
    "変化前後の座標が文言に出ているはず: " + notice);
  assert.ok(notice.includes("Vision"), notice);
});

test("exclusionChangeNotice: 件数・座標とも変化なしなら null", () => {
  const a = [{ id: "postal_label_1", rect: { x: 0, y: 0, w: 10, h: 10 } },
             { id: "tel_paren_l", rect: { x: 20, y: 0, w: 5, h: 5 } }];
  const b = [{ id: "postal_label_1", rect: { x: 0, y: 0, w: 10, h: 10 } },
             { id: "tel_paren_l", rect: { x: 20, y: 0, w: 5, h: 5 } }];
  assert.equal(exclusionChangeNotice(a, b), null);
});

test("exclusionChangeNotice: 件数減少は exclusionRegressionNotice と同じ強い文言に一本化される", () => {
  const loaded = [{ id: "x", rect: { x: 0, y: 0, w: 1, h: 1 } },
                  { id: "y", rect: { x: 0, y: 0, w: 1, h: 1 } }];
  const current = [{ id: "x", rect: { x: 0, y: 0, w: 1, h: 1 } }];
  assert.equal(exclusionChangeNotice(loaded, current), exclusionRegressionNotice(2, 1));
});

test("exclusionChangeNotice: idの入れ替わり（削除+追加が同数）も確認対象", () => {
  const loaded = [{ id: "old_mask", rect: { x: 0, y: 0, w: 10, h: 10 } }];
  const current = [{ id: "new_mask", rect: { x: 50, y: 50, w: 10, h: 10 } }];
  const notice = exclusionChangeNotice(loaded, current);
  assert.ok(notice, "idが入れ替わっているのに null が返っている");
  assert.ok(notice.includes("old_mask") && notice.includes("new_mask"), notice);
});

test("exclusionChangeNotice: 4件以上の座標変化は「ほかN件」に集約する", () => {
  const loaded = Array.from({ length: 5 }, (_, i) =>
    ({ id: `e${i}`, rect: { x: 0, y: 0, w: 10, h: 10 } }));
  const current = loaded.map((e) => ({ id: e.id, rect: { ...e.rect, x: e.rect.x + 5 } }));
  const notice = exclusionChangeNotice(loaded, current);
  assert.ok(notice.includes("ほか 2 件"),
    "3件表示＋『ほか2件』の集約になっているはず: " + notice);
});

// ---------------------------------------------------------------- いろは5巡目指摘
// expand-page の aligned:false 案内を reason で出し分ける
test("expandAlignNotice: aligned:true は従来どおりの成功文言", () => {
  const r = expandAlignNotice(true, undefined, "");
  assert.equal(r.isError, false);
  assert.ok(r.text.includes("位置合わせ済み"), r.text);
});

test("expandAlignNotice: reason=template はテンプレ破損を疑う赤帯文言（自動補正とは言わない）", () => {
  const r = expandAlignNotice(false, "template", "");
  assert.equal(r.isError, true, "赤帯（errMsg）に出すべき");
  assert.ok(r.text.includes("テンプレート") && r.text.includes("保存して検証"), r.text);
  assert.ok(!r.text.includes("自動補正"),
    "テンプレ破損なのに『待てば自動補正される』と誤案内している: " + r.text);
});

test("expandAlignNotice: reason=align・reason欠落（旧コア互換）は現行文言を維持", () => {
  const withReason = expandAlignNotice(false, "align", "PDF の 1/2 ページ目・");
  const withoutReason = expandAlignNotice(false, undefined, "PDF の 1/2 ページ目・");
  assert.deepEqual(withReason, withoutReason, "reason 欠落は align 相当にフォールバックするはず");
  assert.equal(withReason.isError, false);
  assert.ok(withReason.text.includes("自動補正されるため枠は動かさないでください"), withReason.text);
  assert.ok(withReason.text.includes("PDF の 1/2 ページ目・"), withReason.text);
});

test("expandAlignNotice: reason=image/other は中立文言で自動補正を主張しない・赤帯にもしない", () => {
  for (const reason of ["image", "other"]) {
    const r = expandAlignNotice(false, reason, "");
    assert.equal(r.isError, false, reason);
    assert.ok(!r.text.includes("自動補正"), `${reason}: ` + r.text);
    assert.ok(!r.text.includes("テンプレートが壊れている"), `${reason}: ` + r.text);
  }
});

// ---------------------------------------------------------------- マリン最終レビュー H-1
test("promoteFailureNotice: staged の在り処を必ず案内し、rustError の詳細をそのまま伝える", () => {
  const n = promoteFailureNotice("C:\\app\\templates\\chouhyo-v1.json",
    "保存の確定に失敗しました（アクセスが拒否されました）。直前の内容へ戻しました（壊れていません）。"
      + "新しい編集内容は C:\\app\\templates\\chouhyo-v1.json.saving.json に残っています。保存をやり直してください");
  assert.ok(n.includes("chouhyo-v1.json.saving.json"),
    "staged の在り処（.saving.json）を必ず案内するはず: " + n);
  assert.ok(!n.includes("保存していません"),
    "verify は通っているので『保存していません』は嘘になる: " + n);
  assert.ok(n.includes("直前の内容へ戻しました"), "lib.rs のエラー詳細を落としていない: " + n);
});

test("promoteFailureNotice: 復元にも失敗した場合の文言も落とさず伝える", () => {
  const n = promoteFailureNotice("C:\\app\\t.json",
    "保存の確定に失敗しました（e1）。直前の内容への復元にも失敗しました（e2）。"
      + "C:\\app\\t.json は存在しません。直前の内容は C:\\app\\t.json.bak に、"
      + "新しい編集内容は C:\\app\\t.json.saving.json にあります。手動での復旧が必要です");
  assert.ok(n.includes("t.json.saving.json"));
  assert.ok(n.includes("復元にも失敗"), n);
  assert.ok(n.includes("t.json.bak"), "lib.rs 側が案内した .bak の在り処も残るはず: " + n);
});

// scripts/run_all_tests.py の集計器が読む形式（"N passed ... in <秒>"）で
// 出す。これが無いと「実行された試験が0件」と判定されて FAIL になる
const secs = ((Date.now() - t0) / 1000).toFixed(2);
console.log(`\n${passed} passed, ${failed} failed in ${secs}s`);
console.log(failed === 0 ? "すべて成功" : `失敗 ${failed} 件`);
process.exit(failed === 0 ? 0 : 1);
