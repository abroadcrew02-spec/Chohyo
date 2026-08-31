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
      'export { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, resolveOverlaps } from "./Editor.tsx";\n' +
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
const { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, resolveOverlaps, noticeFor, STATUS_JA } =
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
  assert.deepEqual(r.skipped, ["元号"]);
  const clean = resolveOverlaps([owner]);
  assert.deepEqual(clean.carved, []);
  assert.equal(clean.fields[0], owner);
});

// scripts/run_all_tests.py の集計器が読む形式（"N passed ... in <秒>"）で
// 出す。これが無いと「実行された試験が0件」と判定されて FAIL になる
const secs = ((Date.now() - t0) / 1000).toFixed(2);
console.log(`\n${passed} passed, ${failed} failed in ${secs}s`);
console.log(failed === 0 ? "すべて成功" : `失敗 ${failed} 件`);
process.exit(failed === 0 ? 0 : 1);
