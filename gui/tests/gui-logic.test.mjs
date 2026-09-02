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
      'export { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice, isOutput, outputAttrForJson, countOutputDisabled, findColumnPositions, findTableColumnPositions, outputCheckboxLabel, saveConfirmWarnings, unclearPopulationNote, fieldColumnPositionNote, tableColumnRangeInfo, tableColumnOrderNote, outputOrderSnapshot, outputOrderChanged, fieldGeometrySnapshot, geometryUnchanged, reorderCarveBlockedNotice, orderChangeReportNote, fieldsForFace, moveFieldOutputOrder, moveTableColumnOrder, tableColumnReorderImpactNote, columnDecreaseFor, keyAction, clampRect, outOfFaceElements, buildTemplateJson } from "./Editor.tsx";\n' +
      'export { noticeFor, STATUS_JA, outputDisabledNotice, counterNotice, targetWindowHeight, RUN_WINDOW_HEIGHT_DEFAULT, RUN_WINDOW_WIDTH, parseVerify, credNotice, accumulationNotice } from "./RunScreen.tsx";\n',
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
// mod（名前空間全体）も保持する。AC-2.5（faces/blocks を並べ替える UI 相当の
// エクスポートが存在しないことの構造確認）に使う——ここで export した名前
// だけがこのバンドルの外部から呼べる操作の全量なので、その中に face/block の
// 並べ替えに相当する名前が無いことを機械的に確認できる
const mod = await import(pathToFileURL(outFile).href);
const { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice, isOutput, outputAttrForJson, countOutputDisabled, findColumnPositions, findTableColumnPositions, outputCheckboxLabel, saveConfirmWarnings, unclearPopulationNote, fieldColumnPositionNote, tableColumnRangeInfo, tableColumnOrderNote, outputOrderSnapshot, outputOrderChanged, fieldGeometrySnapshot, geometryUnchanged, reorderCarveBlockedNotice, orderChangeReportNote, fieldsForFace, moveFieldOutputOrder, moveTableColumnOrder, tableColumnReorderImpactNote, columnDecreaseFor, keyAction, clampRect, outOfFaceElements, buildTemplateJson, noticeFor, STATUS_JA, outputDisabledNotice, counterNotice, targetWindowHeight, RUN_WINDOW_HEIGHT_DEFAULT, RUN_WINDOW_WIDTH, parseVerify, credNotice, accumulationNotice } = mod;

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

// ---------------------------------------------------------------- issue #65-3 S2
// counterNotice: run/remap の完了サマリに乗る新カウンタ（fallback_used/
// fallback_discarded/carve_hole/conflict_excluded_field）の「実行時のお知らせ」
// 1行化。マリンレビュー S-3（conflict の区別漏れ）・S-4（carve_hole が〓に
// 触れていない）・N-7（「参照先から採用/破棄」の主語を明確化）を反映した文言
test("S2-1 counterNotice: 非0カウンタから対象外欄由来の内訳・〓の結果・食い違いを含む通知が作られる", () => {
  const t = counterNotice({
    fallback_used: 2, fallback_discarded: 1, fallback_discarded_excluded_field: 1,
    carve_hole: 3, carve_hole_excluded_field: 2,
    conflict_excluded_field: 1,
  });
  assert.ok(t, "null が返っている（カウンタを捨てている）");
  assert.ok(t.includes("参照先の文字"), "N-7: 破棄されるのが参照先の文字だと分かる主語が無い: " + t);
  assert.ok(t.includes("採用 2件"), t);
  assert.ok(t.includes("破棄 1件"), t);
  assert.ok(t.includes("うち出力しない欄由来 1件"), "fallback_discarded の内訳が無い: " + t);
  assert.ok(t.includes("3件"), t);
  assert.ok(t.includes("〓になっています"), "S-4: carve_hole が〓化の結果に触れていない: " + t);
  assert.ok(t.includes("うち出力しない欄由来 2件"), "carve_hole の内訳が無い: " + t);
  // S-3: conflict は fallback_discarded の内訳に混ぜず独立句で出す
  // （fallback_discarded にも二重計上されるが「主と参照先の食い違い」という
  //  別の事実なので、破棄の件数とは別に読めること）
  assert.ok(t.includes("主と参照先の食い違い") && t.includes("1件"),
    "conflict_excluded_field の区別が出ていない: " + t);
});

test("S2-2 counterNotice: 全カウンタ0なら null（0件表示はノイズになるので出さない）", () => {
  assert.equal(counterNotice({}), null);
  assert.equal(counterNotice({
    fallback_used: 0, fallback_discarded: 0, carve_hole: 0,
    fallback_discarded_excluded_field: 0, carve_hole_excluded_field: 0,
    conflict_excluded_field: 0,
  }), null);
});

test("S2-2b counterNotice: conflict 単独が非0でも他が0なら独立句だけが出る", () => {
  // conflict は総数カウンタを持たず対象外欄由来の内訳しか無い（S-3）ため、
  // fallback_used/fallback_discarded/carve_hole がすべて0でも単独で発火しうる
  const t = counterNotice({ conflict_excluded_field: 1 });
  assert.ok(t, "conflict だけでは null になっている（区別が消えている）");
  assert.equal(t, "主と参照先の食い違い 1件（出力しない欄）");
});

// 片配線の回帰ゲート: run 用の summary にしかカウンタを配線せず remap_summary
// を default 節に落として捨てる、という #60 M-2（source_renamed/rename_fallback）
// と同種の事故を防ぐ。noticeFor を通した結果が両イベントで同じ counterNotice を
// 経由していることを固定する（conflict_excluded_field も含めて配線を確認）
test("S2-3 noticeFor: summary と remap_summary の両方で counterNotice が呼ばれる", () => {
  const counts = {
    fallback_used: 2, fallback_discarded: 1, fallback_discarded_excluded_field: 1,
    carve_hole: 3, carve_hole_excluded_field: 2, conflict_excluded_field: 1,
  };
  const viaSummary = noticeFor({ event: "summary", ...counts });
  const viaRemap = noticeFor({ event: "remap_summary", ...counts });
  assert.ok(viaSummary, "summary 側で null が返っている（配線漏れ）");
  assert.equal(viaSummary, viaRemap,
    "summary と remap_summary で文言が異なる（同じ counterNotice を通っていない）");
  assert.equal(viaSummary, counterNotice(counts));
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
  const loaded = { fields: 194, amountCells: 28, exclusions: 9, columns: 220 };
  const decreased = saveDiffNote(loaded, { fields: 193, amountCells: 28, exclusions: 9, columns: 220 });
  assert.ok(decreased.text.includes("欄 194 → 193（-1）"), decreased.text);
  assert.ok(decreased.text.includes("金額 28"), decreased.text);
  assert.ok(!decreased.text.includes("金額 28 →"), "不変の項目には矢印を付けない: " + decreased.text);
  assert.deepEqual(decreased.decreasedLabels, ["欄"]);

  const quiet = saveDiffNote(loaded, loaded);
  assert.equal(quiet.text, "欄 194・金額 28・除外 9・列 220");
  assert.deepEqual(quiet.decreasedLabels, [], "不変時は静か（警告対象なし）");

  const increased = saveDiffNote(loaded, { fields: 195, amountCells: 28, exclusions: 9, columns: 220 });
  assert.ok(increased.text.includes("欄 194 → 195（+1）"), increased.text);
  assert.deepEqual(increased.decreasedLabels, [], "増加は減少扱いしない");

  // issue #66 段3: 列数も対象に追加。列が減れば decreasedLabels に「列」が乗る
  const colDecreased = saveDiffNote(loaded, { ...loaded, columns: 218 });
  assert.ok(colDecreased.text.includes("列 220 → 218（-2）"), colDecreased.text);
  assert.deepEqual(colDecreased.decreasedLabels, ["列"]);
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
  const verifyResponseAtLoad = { fields: 194, amountCells: 28, exclusions: 9, columns: 220 };
  const verifyResponseAtSave = { fields: 194, amountCells: 28, exclusions: 9, columns: 220 };
  const diff = saveDiffNote(verifyResponseAtLoad, verifyResponseAtSave);
  assert.equal(diff.text, "欄 194・金額 28・除外 9・列 220",
    "無編集保存なのに矢印つきの差分が出ている（F-10 の再発）: " + diff.text);
  assert.deepEqual(diff.decreasedLabels, []);

  // 対比: 旧実装のように読み込み時だけ「単発欄のみ」の小さい数（例: 14）を
  // 使うと、無編集でも巨大な差分が出ていた——これが直した不具合そのもの
  const buggyLoadedSnapshot = { fields: 14, amountCells: 1, exclusions: 9, columns: 220 };
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

// ---------------------------------------------------------------- issue #66 段3（出力列制御 MVP・第1弾UI）
test("isOutput / outputAttrForJson: output省略・trueは出力する、falseだけ出力しない扱い", () => {
  assert.equal(isOutput({}), true);
  assert.equal(isOutput({ output: true }), true);
  assert.equal(isOutput({ output: false }), false);
  assert.deepEqual(outputAttrForJson(undefined), {});
  assert.deepEqual(outputAttrForJson(true), {}, "true でも書かない（省略時trueと同義・B-S4）");
  assert.deepEqual(outputAttrForJson(false), { output: false });
});

test("countOutputDisabled: 欄・表の列のうち output:false の総数を数える（タブ見出しバッジ用）", () => {
  const fields = [{ output: false }, { output: true }, {}];
  const tables = [{ columns: [{ output: false }, {}] }, { columns: [{ output: false }] }];
  assert.equal(countOutputDisabled(fields, tables), 3);
  assert.equal(countOutputDisabled([], []), 0);
});

test("findColumnPositions: 単発欄の列位置を column_names から探す（見つからなければ null）", () => {
  const columnNames = [
    "要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名", "ページ番号", "ステータス",
    "person_氏名", "person_生年月日_元号", "person_生年月日_年",
    "person_生年月日_月", "person_生年月日_日",
  ];
  assert.deepEqual(findColumnPositions(columnNames, "person_氏名"), { first: 7, last: 7 });
  assert.deepEqual(findColumnPositions(columnNames, "person_生年月日"), { first: 8, last: 11 },
    "subfields で複数列に分かれる欄は最初と最後の位置を返す");
  assert.equal(findColumnPositions(columnNames, "person_住所1"), null,
    "存在しない（output:false 等の）列は null——出力列タブでは「—」表示になる");
  assert.equal(findColumnPositions(null, "person_氏名"), null, "column_names 未取得なら null");
});

test("findTableColumnPositions: 行展開された表の列を table_id と列名から探す", () => {
  const columnNames = [
    "要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名", "ページ番号", "ステータス",
    "family_01_続柄", "family_01_氏名", "family_02_続柄", "family_02_氏名",
  ];
  assert.deepEqual(findTableColumnPositions(columnNames, "family", "続柄"), { first: 7, last: 9 });
  assert.deepEqual(findTableColumnPositions(columnNames, "family", "氏名"), { first: 8, last: 10 });
  assert.equal(findTableColumnPositions(columnNames, "family", "金額"), null);
  assert.equal(findTableColumnPositions(null, "family", "続柄"), null);
});

test("outputCheckboxLabel: accessible name に識別子と現在の状態を含める（AC-1.21・AC-1.25）", () => {
  assert.equal(outputCheckboxLabel("氏名", false, null), "氏名を出力する（現在: 出力対象外）");
  assert.equal(outputCheckboxLabel("氏名", true, { first: 9, last: 9 }), "氏名を出力する（現在: 9列目）");
  assert.equal(outputCheckboxLabel("生年月日", true, { first: 8, last: 11 }),
    "生年月日を出力する（現在: 8〜11列目）");
  assert.equal(outputCheckboxLabel("氏名", true, null), "氏名を出力する（現在: 出力する）",
    "column_names 未取得時は誤った列番号を言わない（FR-0.1: 再導出しない）");
});

test("outputCheckboxLabel: 欄が違えばラベルも異なる（SR一覧での重複読み上げ防止・AC-1.21）", () => {
  const a = outputCheckboxLabel("氏名", true, { first: 7, last: 7 });
  const b = outputCheckboxLabel("住所1", true, { first: 8, last: 8 });
  assert.notEqual(a, b);
});

// ---------------------------------------------------------------- issue #66 段5
// 列位置表示（FR-2.3・AC-2.7前半＝「表示が column_names と一致」）
const COLUMN_NAMES_220 = (() => {
  // 実物に近い220列を組み立てる: 管理6列 + 埋め草2列 + 単発欄 person_氏名(9列目・
  // コーディネーター指定例と一致させる) + family 表4行×2列(続柄・氏名) +
  // 残りは埋め草の単発欄
  const names = ["要確認セル数", "最低信頼度", "帳票ID", "入力ファイル名", "ページ番号", "ステータス",
    "dummy_a", "dummy_b", "person_氏名"];
  for (let row = 1; row <= 4; row++) {
    names.push(`family_${String(row).padStart(2, "0")}_続柄`);
    names.push(`family_${String(row).padStart(2, "0")}_氏名`);
  }
  while (names.length < 220) names.push(`dummy_${names.length}`);
  return names;
})();

test("fieldColumnPositionNote: 単発欄の位置注記（左から9列目 / 全220列・コーディネーター指定例）", () => {
  const pos = findColumnPositions(COLUMN_NAMES_220, "person_氏名");
  assert.deepEqual(pos, { first: 9, last: 9 });
  assert.equal(fieldColumnPositionNote(pos, COLUMN_NAMES_220.length),
    "左から9列目 / 全220列");
});

test("fieldColumnPositionNote: position・totalColumns のどちらか欠けたら null（未取得時は番号を出さない）", () => {
  assert.equal(fieldColumnPositionNote(null, 220), null);
  assert.equal(fieldColumnPositionNote({ first: 1, last: 1 }, null), null);
  assert.equal(fieldColumnPositionNote(null, null), null);
});

test("fieldColumnPositionNote: 範囲がある場合（複合欄など）は first〜last で表示", () => {
  assert.equal(fieldColumnPositionNote({ first: 8, last: 11 }, 220), "左から8〜11列目 / 全220列");
});

test("tableColumnRangeInfo: 表の範囲を column_names から実引きする（範囲表記・AC-2.7、コーディネーター指定例に近い形）", () => {
  const info = tableColumnRangeInfo(COLUMN_NAMES_220, "family");
  assert.deepEqual(info, {
    first: 10, last: 17, count: 8,
    exampleName: "family_01_続柄", examplePosition: 10,
  });
});

test("tableColumnRangeInfo: column_names 未取得・該当なしは null（安全側）", () => {
  assert.equal(tableColumnRangeInfo(null, "family"), null);
  assert.equal(tableColumnRangeInfo(COLUMN_NAMES_220, "no_such_table"), null);
});

test("tableColumnRangeInfo: 例文は column_names に実在するエントリをそのまま引く（FR-0.1・組み立て直し禁止）", () => {
  const info = tableColumnRangeInfo(COLUMN_NAMES_220, "family");
  assert.ok(COLUMN_NAMES_220.includes(info.exampleName));
  assert.equal(COLUMN_NAMES_220[info.examplePosition - 1], info.exampleName);
});

test("tableColumnOrderNote: 表の中での定義順と x_offset の左右順を併記（付録A）", () => {
  const columns = [{ x_offset: 100 }, { x_offset: 0 }, { x_offset: 200 }];
  assert.equal(tableColumnOrderNote(columns, 0, true), "表の中で1番目・帳票では左から2番目");
  assert.equal(tableColumnOrderNote(columns, 1, true), "表の中で2番目・帳票では左から1番目");
  assert.equal(tableColumnOrderNote(columns, 2, true), "表の中で3番目・帳票では左から3番目");
});

test("tableColumnOrderNote: output:false の列は番号でなく「出力対象外」（段3実装と整合）", () => {
  const columns = [{ x_offset: 100 }, { x_offset: 0 }];
  assert.equal(tableColumnOrderNote(columns, 0, false), "出力対象外");
});

test("tableColumnOrderNote: 範囲外の index は null（防御的）", () => {
  assert.equal(tableColumnOrderNote([{ x_offset: 0 }], 5, true), null);
  assert.equal(tableColumnOrderNote([{ x_offset: 0 }], -1, true), null);
});

// ---------------------------------------------------------------- issue #66 段6
// 座標不変ガード（FR-2.2・AC-2.4）: 並べ替えを含む保存では resolveOverlaps の
// 自動調整（切り抜き）を許さない
test("outputOrderSnapshot / outputOrderChanged: 単発欄の並び替えを検知する", () => {
  const A = { uid: "a", output: undefined }, B = { uid: "b", output: undefined };
  const loaded = outputOrderSnapshot([A, B], []);
  assert.equal(outputOrderChanged(loaded, outputOrderSnapshot([A, B], [])), false,
    "同じ並びなら変化なし");
  assert.equal(outputOrderChanged(loaded, outputOrderSnapshot([B, A], [])), true,
    "配列順が入れ替われば変化あり");
});

test("outputOrderSnapshot / outputOrderChanged: 表の配列順・表内列の並びも検知する", () => {
  const t1 = { uid: "t1", table_id: "family", columns: [{ name: "続柄" }, { name: "氏名" }] };
  const t2 = { uid: "t2", table_id: "person", columns: [{ name: "住所" }] };
  const loaded = outputOrderSnapshot([], [t1, t2]);
  assert.equal(outputOrderChanged(loaded, outputOrderSnapshot([], [t2, t1])), true,
    "表どうしの配列順が変われば検知する");
  const t1Reordered = { ...t1, columns: [{ name: "氏名" }, { name: "続柄" }] };
  assert.equal(outputOrderChanged(loaded, outputOrderSnapshot([], [t1Reordered, t2])), true,
    "表の内部列の並びが変われば検知する（.colrow の並べ替え）");
  assert.equal(outputOrderChanged(loaded, outputOrderSnapshot([], [t1, t2])), false);
});

test("outputOrderChanged: 基準（loaded）が無ければ false 側に倒す（初回未読込時にガードを誤発火させない）", () => {
  assert.equal(outputOrderChanged(null, outputOrderSnapshot([{ uid: "a" }], [])), false);
});

test("fieldGeometrySnapshot / geometryUnchanged: rect・fallback・extras の1px変化も検知する", () => {
  const base = [{ uid: "a", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [] }];
  const before = fieldGeometrySnapshot(base);
  assert.equal(geometryUnchanged(before, fieldGeometrySnapshot(base)), true, "無変化は true");
  const rectMoved = [{ uid: "a", rect: { x: 1, y: 0, w: 80, h: 40 }, marks: [] }];
  assert.equal(geometryUnchanged(before, fieldGeometrySnapshot(rectMoved)), false, "1px でも検知");
  const fallbackAdded = [{ uid: "a", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [],
    fallback: { x: 0, y: 40, w: 80, h: 40 } }];
  assert.equal(geometryUnchanged(before, fieldGeometrySnapshot(fallbackAdded)), false,
    "fallback の有無の変化も検知");
  const extrasAdded = [{ uid: "a", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [],
    extras: [{ x: 80, y: 0, w: 20, h: 40 }] }];
  assert.equal(geometryUnchanged(before, fieldGeometrySnapshot(extrasAdded)), false,
    "extras の増減も検知");
});

test("reorderCarveBlockedNotice: 保存中止の理由文（errbox・「保存していません」系）", () => {
  const text = reorderCarveBlockedNotice();
  assert.ok(text.startsWith("保存していません:"));
  assert.ok(text.includes("並べ替え"));
});

test("orderChangeReportNote: 並べ替えを含む保存の成功サマリにだけ1行足す（FR-2.6・AC-2.10）", () => {
  assert.equal(orderChangeReportNote(false, 14), null, "順序不変では出さない");
  assert.equal(orderChangeReportNote(true, 14), "列順を変更（欄 14 は増減なし）");
});

// AC-2.4 の3ケース（コーディネーター指定の判定マトリクス）を、実際に保存時ガードが
// 計算する式（outputOrderChanged && !geometryUnchanged）どおりに組み立てて検証する
const wouldBlockSave = (loadedOrder, fieldsNow, tablesNow, splitY) => {
  const orderChangedNow = outputOrderChanged(loadedOrder, outputOrderSnapshot(fieldsNow, tablesNow));
  const before = fieldGeometrySnapshot(fieldsNow);
  const resolved = resolveOverlaps(fieldsNow, splitY);
  const after = fieldGeometrySnapshot(resolved.fields);
  return orderChangedNow && !geometryUnchanged(before, after);
};

test("AC-2.4 ①: 順序不変・切り抜きあり＝通る（従来どおり自動調整を許す）", () => {
  const A = { uid: "a", field_id: "A", kind: "text", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [] };
  const B = { uid: "b", field_id: "B", kind: "text", rect: { x: 60, y: 0, w: 100, h: 40 }, marks: [] };
  const loaded = outputOrderSnapshot([A, B], []);
  assert.equal(wouldBlockSave(loaded, [A, B], []), false);
});

test("AC-2.4 ②: 順序変更・切り抜き発生＝中止（並べ替えと自動調整を両立させない・付録A）", () => {
  const A = { uid: "a", field_id: "A", kind: "text", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [] };
  const B = { uid: "b", field_id: "B", kind: "text", rect: { x: 60, y: 0, w: 100, h: 40 }, marks: [] };
  const loaded = outputOrderSnapshot([A, B], []);
  assert.equal(wouldBlockSave(loaded, [B, A], []), true);
});

test("AC-2.4 ③: 順序変更・切り抜きなし＝通る（重なりが無ければ並べ替えだけの保存は妨げない）", () => {
  const A = { uid: "a", field_id: "A", kind: "text", rect: { x: 0, y: 0, w: 80, h: 40 }, marks: [] };
  const B = { uid: "b", field_id: "B", kind: "text", rect: { x: 200, y: 0, w: 100, h: 40 }, marks: [] };
  const loaded = outputOrderSnapshot([A, B], []);
  assert.equal(wouldBlockSave(loaded, [B, A], []), false);
});

// ---------------------------------------------------------------- issue #66 段7
// 並べ替え UI（FR-2.1/2.5・AC-2.1〜2.3/2.5/2.8〜2.10）
test("fieldsForFace: splitY 境界で表面/裏面に分け、配列順は保つ", () => {
  const A = { uid: "a", rect: { y: 0 } }, B = { uid: "b", rect: { y: 500 } },
        C = { uid: "c", rect: { y: 1900 } };
  const H = 3510;
  assert.deepEqual(fieldsForFace([A, B, C], "front", 1880, H).map((f) => f.uid), ["a", "b"]);
  assert.deepEqual(fieldsForFace([A, B, C], "back", 1880, H).map((f) => f.uid), ["c"]);
  // 境界ちょうど（y===splitY）は裏面に属する（buildTemplate の inFace と同じ述語）
  const D = { uid: "d", rect: { y: 1880 } };
  assert.deepEqual(fieldsForFace([D], "front", 1880, H), []);
  assert.deepEqual(fieldsForFace([D], "back", 1880, H).map((f) => f.uid), ["d"]);
});

test("AC-2.1: 面内での並べ替えで fieldsForFace（buildTemplate と同じ抽出）の配列順が変わる", () => {
  const A = { uid: "a", rect: { y: 0 } }, B = { uid: "b", rect: { y: 100 } },
        C = { uid: "c", rect: { y: 200 } };
  const splitY = 1880, H = 3510;
  assert.deepEqual(fieldsForFace([A, B, C], "front", splitY, H).map((f) => f.uid), ["a", "b", "c"]);
  const moved = moveFieldOutputOrder([A, B, C], "b", "up", splitY);
  assert.deepEqual(fieldsForFace(moved, "front", splitY, H).map((f) => f.uid), ["b", "a", "c"],
    "B を1つ上へ移動すると buildTemplate が書く配列順（=JSON の列順）も b,a,c になる");
});

test("moveFieldOutputOrder: 面の先頭/末尾では null（3閉区間の境界・C-2 disabled）", () => {
  const A = { uid: "a", rect: { y: 0 } }, B = { uid: "b", rect: { y: 100 } };
  const splitY = 1880;
  assert.equal(moveFieldOutputOrder([A, B], "a", "up", splitY), null, "面の先頭はこれ以上上へ行けない");
  assert.equal(moveFieldOutputOrder([A, B], "b", "down", splitY), null, "面の末尾はこれ以上下へ行けない");
  assert.equal(moveFieldOutputOrder([A, B], "no-such-uid", "up", splitY), null, "存在しない uid は null");
});

test("moveFieldOutputOrder: 面をまたいだ隣接は探さない（AC-2.2・裏面の欄には動かない）", () => {
  const front = { uid: "f", rect: { y: 0 } };
  const back = { uid: "b", rect: { y: 2000 } };
  const splitY = 1880;
  // front の唯一の欄は「上」も「下」も同面に隣が無いのでどちらも null。
  // back の欄が配列の隣にあっても、面が違うので隣接扱いしない
  assert.equal(moveFieldOutputOrder([front, back], "f", "down", splitY), null);
  assert.equal(moveFieldOutputOrder([front, back], "back", "up", splitY), null);
});

test("moveFieldOutputOrder: 配列中に他面の欄が挟まっていても同面の最近傍と入れ替える（インターリーブ耐性）", () => {
  // 追加操作は常に配列末尾に append されるため、表面の欄の後に裏面の欄を
  // 挟んでから表面の欄を追加する、といった操作で面が配列中で入り混じりうる。
  // 配列順: [A(表), X(裏), B(表)]。B を「上」へ動かすと、間の X(裏) は
  // 飛び越して同面の最近傍（A）と入れ替わるはず
  const A = { uid: "a", rect: { y: 0 } };
  const X = { uid: "x", rect: { y: 2000 } };
  const B = { uid: "b", rect: { y: 100 } };
  const splitY = 1880;
  const moved = moveFieldOutputOrder([A, X, B], "b", "up", splitY);
  assert.deepEqual(moved.map((f) => f.uid), ["b", "x", "a"],
    "X（裏面）はそのまま・B と A（ともに表面）だけが入れ替わる");
});

test("moveFieldOutputOrder / moveTableColumnOrder: 入力配列を変更しない（イミュータブル・AC-2.9 の前提）", () => {
  const A = { uid: "a", rect: { y: 0 } }, B = { uid: "b", rect: { y: 100 } };
  const fieldsBefore = [A, B];
  const snapshotBefore = JSON.stringify(fieldsBefore);
  moveFieldOutputOrder(fieldsBefore, "b", "up", 1880);
  assert.equal(JSON.stringify(fieldsBefore), snapshotBefore, "元の配列は変更されない（新しい配列を返す）");

  const columns = [{ name: "続柄" }, { name: "氏名" }];
  const colSnapshot = JSON.stringify(columns);
  moveTableColumnOrder(columns, 0, "down");
  assert.equal(JSON.stringify(columns), colSnapshot, "元の columns 配列も変更されない");
});

test("moveTableColumnOrder: 隣接列と入れ替える・境界は null", () => {
  const columns = [{ name: "続柄" }, { name: "氏名" }, { name: "生年月日" }];
  const moved = moveTableColumnOrder(columns, 0, "down");
  assert.deepEqual(moved.map((c) => c.name), ["氏名", "続柄", "生年月日"]);
  assert.equal(moveTableColumnOrder(columns, 0, "up"), null, "先頭列はこれ以上上へ行けない");
  assert.equal(moveTableColumnOrder(columns, 2, "down"), null, "末尾列はこれ以上下へ行けない");
});

test("AC-2.3: 表内列の並べ替えは、行展開後の全行に反映される（1回の並べ替えで全行が動く）", () => {
  // 展開規則は table_id_行番号_列名（findTableColumnPositions のコメントと同じ）。
  // GUI 側で行展開そのものを組み立て直すことはしない（FR-0.1）——ここではその
  // 規則を使って「1回の列順変更が全行に一様に反映される」ことだけを確認する
  const expand = (tableId, rows, columns) => {
    const out = [];
    for (let r = 1; r <= rows; r++)
      for (const c of columns) out.push(`${tableId}_${String(r).padStart(2, "0")}_${c.name}`);
    return out;
  };
  const columns = [{ name: "続柄" }, { name: "氏名" }];
  const before = expand("family", 3, columns);
  assert.deepEqual(before, [
    "family_01_続柄", "family_01_氏名",
    "family_02_続柄", "family_02_氏名",
    "family_03_続柄", "family_03_氏名"]);
  const moved = moveTableColumnOrder(columns, 0, "down");   // 続柄・氏名 を入れ替え
  const after = expand("family", 3, moved);
  assert.deepEqual(after, [
    "family_01_氏名", "family_01_続柄",
    "family_02_氏名", "family_02_続柄",
    "family_03_氏名", "family_03_続柄"],
    "3行すべてで 氏名/続柄 の並びが入れ替わっている（1行だけが動くバグは無い）");
});

test("tableColumnReorderImpactNote: 行数は常に示し、列数は取れたときだけ添える（FR-0.1）", () => {
  assert.equal(tableColumnReorderImpactNote(28, 140), "この変更は 28 行分・140 列の並びに影響します");
  assert.equal(tableColumnReorderImpactNote(28, null), "この変更は 28 行分の並びに影響します",
    "column_names 未取得時は列数を言わない（誤った数字を出さない）");
});

test("AC-2.5: faces/blocks を並べ替える UI 相当のエクスポートが存在しない（構造確認）", () => {
  // このバンドルが export している名前の全量（=このモジュールの外から呼べる
  // 操作の全量）に、面や行ブロックを並べ替える系の名前が無いことを機械的に
  // 確認する。段7で新設したのは単発欄（フィールド）・表の内部列の並べ替えのみ
  // 単語境界で見る（部分一致だと reorderCarveBlockedNotice の
  // "Blocked" が "block" に誤爆する）。camelCase を単語に割ってから判定する
  const camelWords = (name) =>
    name.replace(/([a-z0-9])([A-Z])/g, "$1 $2").toLowerCase().split(/[\s_]+/);
  const keys = Object.keys(mod);
  const forbidden = keys.filter((k) => {
    const words = camelWords(k);
    const hasTarget = words.includes("face") || words.includes("faces")
      || words.includes("block") || words.includes("blocks");
    const hasVerb = words.some((w) => ["move", "reorder", "sort", "swap"].includes(w));
    return hasTarget && hasVerb;
  });
  assert.deepEqual(forbidden, [], `想定外のエクスポートを検出: ${forbidden.join(", ")}`);
  // 新設した並べ替え関数自体は「欄」「表内列」に限定した名前になっている
  assert.ok(keys.includes("moveFieldOutputOrder"));
  assert.ok(keys.includes("moveTableColumnOrder"));
});

test("saveConfirmWarnings: ⚠が0件ならモーダルを出さない（空配列・C-5 empty）", () => {
  assert.deepEqual(saveConfirmWarnings({ isShipped: false, imageSizeMismatch: null,
    exclusionNotice: null, columnDecrease: null }), []);
});

test("saveConfirmWarnings: 該当する項目だけを順に積む（4種統合・付録A）", () => {
  const w = saveConfirmWarnings({
    isShipped: true,
    imageSizeMismatch: { from: "2490×3510", to: "2480×3500" },
    exclusionNotice: "除外領域が減っています",
    columnDecrease: { kind: "decrease", from: 220, to: 217 },
  });
  assert.equal(w.length, 4);
  assert.deepEqual(w.map((x) => x.key), ["shipped", "image-size", "exclusion", "columns"]);
  assert.ok(w[0].text.includes("出荷テンプレート"), w[0].text);
  assert.ok(w[1].text.includes("2490×3510") && w[1].text.includes("2480×3500"), w[1].text);
  assert.equal(w[2].text, "除外領域が減っています");
  assert.ok(w[3].text.includes("220 → 217"), w[3].text);
  assert.ok(w[3].text.includes("枠と読み取りは残ります"),
    "対象外欄の可逆性（Q-29）を保存前確認にも明示する: " + w[3].text);
});

test("saveConfirmWarnings: columnDecrease が unknown（列数比較不能）でも⚠を出す（issue #65-1・fail-open 修正の配線確認）", () => {
  const w = saveConfirmWarnings({ isShipped: false, imageSizeMismatch: null,
    exclusionNotice: null, columnDecrease: { kind: "unknown" } });
  assert.equal(w.length, 1);
  assert.equal(w[0].key, "columns");
  // unknown は基準未取得（穴A）・今回値の欠落（穴B）のどちらでも立つため、
  // 原因を一方に決め打ちした文言にしない（レビュー指摘 S-1）
  assert.ok(w[0].text.includes("列数を比較できません"), w[0].text);
  assert.ok(!w[0].text.includes("基準未取得"), w[0].text);
});

// ---------------------------------------------------------------- issue #65-1（M2・列数比較の fail-open 修正）
test("columnDecreaseFor: baseline未取得（0）は decrease でなく unknown を返す（穴A・fail-open 回帰ゲート）", () => {
  // loadedCounts.columns の初期値は0（useState宣言部）で、verify応答の
  // 取得に失敗した経路でもそのまま残る。0のまま `current < baseline` の
  // ような直接比較をすると常に false（=減っていない）になり、実際に列が
  // 減っても無警告になる（fail-open・issue #65-1 本丸）。0はvalidate_v1が
  // 抽出列0を拒否するため到達しないsentinelとして扱い、比較不能を
  // unknownで明示しなければならない——nullを返すと「減っていない」と
  // 誤認され、この関数がまさに直そうとしているバグを再現してしまう
  assert.deepEqual(columnDecreaseFor(0, 220), { kind: "unknown" });
  assert.notEqual(columnDecreaseFor(0, 220), null);
});

test("columnDecreaseFor: 基準ありで列が減れば decrease（from/toを保持）、同数・増加は null", () => {
  assert.deepEqual(columnDecreaseFor(220, 217), { kind: "decrease", from: 220, to: 217 });
  assert.equal(columnDecreaseFor(220, 220), null, "同数は decrease ではない");
  assert.equal(columnDecreaseFor(220, 230), null, "増加は decrease ではない");
});

test("columnDecreaseFor: current が数値でなければ unknown（穴B・verify応答の欠落防御）", () => {
  assert.deepEqual(columnDecreaseFor(220, undefined), { kind: "unknown" });
  assert.deepEqual(columnDecreaseFor(220, NaN), { kind: "unknown" });
  assert.deepEqual(columnDecreaseFor(220, "220"), { kind: "unknown" });
});

test("columnDecreaseFor: baseline が数値でなくても unknown（レビュー指摘 M-1・片側だけ緩いガードだと fail-open が残る）", () => {
  // baseline<=0 の判定だけに頼ると、baseline が非数値のとき
  // `undefined <= 0` も `NaN <= 0` も false ですり抜け、null（＝減っていない）
  // を返してしまう——current 側と同じ型ガードを baseline 側にも必須で通す
  assert.deepEqual(columnDecreaseFor(undefined, 220), { kind: "unknown" });
  assert.deepEqual(columnDecreaseFor(NaN, 220), { kind: "unknown" });
});

test("unclearPopulationNote: 要確認セル数の母集団が縮小したときだけ確定文言を返す（AC-1.16・T-S8）", () => {
  assert.equal(unclearPopulationNote(214, 214, 0), null, "抽出列数不変なら null");
  assert.equal(unclearPopulationNote(214, 211, 0), null,
    "抽出列数は減っても対象外欄が0件なら null（別要因の減少・列並べ替え等は本関数の対象外）");
  const note = unclearPopulationNote(214, 211, 3);
  assert.equal(note, "要確認セル数の母集団: 214列 → 211列（出力しない 3 欄を除く）");
});

// ---------------------------------------------------------------- issue #66 段4（FR-1.9・かなたS-5）
test("outputDisabledNotice: N=0・フィールド欠落（旧コア互換）は非表示", () => {
  assert.equal(outputDisabledNotice(0), null, "対象外0件は表示しない");
  assert.equal(outputDisabledNotice(undefined), null, "旧コア（フィールド欠落）は非表示に倒す");
  assert.equal(outputDisabledNotice(-1), null, "非負のはずが崩れても表示しない（防御的）");
});

test("outputDisabledNotice: N=1・複数のときは欄数を含む1行を返す", () => {
  const one = outputDisabledNotice(1);
  assert.ok(one, "1件は表示するはず");
  assert.ok(one.includes("1"), one);
  assert.ok(one.includes("欄"), one);

  const many = outputDisabledNotice(3);
  assert.ok(many.includes("3"), many);
  assert.notEqual(one, many, "件数によって文言が変わるはず");
});

// ---------------------------------------------------------------- 第1弾QA条件付きOK・切替条件①（AC-1.8回帰ガード）
// P3-a: output は resolveOverlaps の入力に一切影響しない（主張元にも
// 被切り抜き側にもなる）。§11 NG事項「output の切替で矩形が動くこと」の
// 見張り。output の有無で結果の rect 群がバイト単位（deepEqual）で
// 同一であることを固定する
test("resolveOverlaps: output:false は切り抜き結果（幾何・carved/skipped/warned）に一切影響しない（AC-1.8・P3-a）", () => {
  // Pair 1: X（output:false）の fallback が A を切り抜く（主張元としての効力）
  const X = { uid: "x", field_id: "X", kind: "text",
              rect: { x: 500, y: 500, w: 10, h: 10 }, marks: [],
              fallback: { x: 100, y: 0, w: 80, h: 40 }, output: false };
  const A = { uid: "a", field_id: "A", kind: "text",
              rect: { x: 100, y: 0, w: 300, h: 40 }, marks: [] };
  // Pair 2: B（output:true）が C（output:false）を切り抜く（被切り抜き側としての効力）
  const B = { uid: "b", field_id: "B", kind: "text",
              rect: { x: 0, y: 100, w: 80, h: 40 }, marks: [] };
  const C = { uid: "c", field_id: "C", kind: "text",
              rect: { x: 60, y: 100, w: 200, h: 40 }, marks: [], output: false };

  const withOutput = [X, A, B, C];
  // output キー自体を取り除いた（省略＝出力する、と同義のはずの）同一幾何
  const withoutOutput = withOutput.map(({ output: _output, ...rest }) => rest);

  const r1 = resolveOverlaps(withOutput);
  const r2 = resolveOverlaps(withoutOutput);
  const geom = (r) => r.fields.map((f) => ({ uid: f.uid, rect: f.rect, extras: f.extras ?? [] }));

  assert.deepEqual(geom(r1), geom(r2),
    "output の有無で切り抜き後の幾何（rect/extras）が変わっている（P3-a・AC-1.8違反）");
  assert.deepEqual(r1.carved, r2.carved, "output の有無で carved 判定が変わっている");
  assert.deepEqual(r1.skipped, r2.skipped, "output の有無で skipped 判定が変わっている");
  assert.deepEqual(r1.warned, r2.warned, "output の有無で warned 判定が変わっている");

  // 「何も起きていないテスト」にならないよう、実際に両方の役割で切り抜きが
  // 発生していることを確認する
  assert.ok(r1.carved.includes("A"), "X（output:false）の主張で A が切り抜かれるはず");
  assert.ok(r1.carved.includes("C"), "C（output:false）自身も被切り抜き側として切り抜かれるはず");
});

// ---------------------------------------------------------------- 第1弾QA条件付きOK・切替条件②(a)（AC-1.18等価テスト・GUI側）
// buildTemplate は `{ ...属性, ...outputAttrForJson(f.output) }` という形で
// 1欄をシリアライズする（Editor.tsx の実装と同じ組み方）。ここでは
// outputAttrForJson 単体のテストから1段強め、実際の欄オブジェクト全体を
// 直列化した結果が「手書き JSON と同じ最小形」になる契約を固定する
// （AC-1.18: JSON 直接編集で output:false を書いたテンプレートの run 出力が
// 画面経由で作ったものと一致する——その前提となる直列化契約。
// core 側の対の検証は core/tests/test_output_columns_ac118_equivalence.py）
test("AC-1.18 (a): 欄オブジェクトの直列化は「false のときだけ output:false」で、他の属性に影響せず手書きJSONと同じ最小形になる", () => {
  const serializeField = (f) => ({
    field_id: f.field_id, kind: f.kind, rect: f.rect,
    ...outputAttrForJson(f.output),
  });

  // 出力する欄（省略）: GUI は output キー自体を書かない（手書きの最小形と同じ）
  const enabled = serializeField({ field_id: "person_氏名", kind: "text",
    rect: { x: 0, y: 0, w: 10, h: 10 }, output: undefined });
  assert.deepEqual(enabled,
    { field_id: "person_氏名", kind: "text", rect: { x: 0, y: 0, w: 10, h: 10 } },
    "output 省略時、GUI が書く JSON に output キーが混ざってはいけない");
  assert.ok(!("output" in enabled));

  // 出力しない欄: 手書きで "output": false と書いたときと同じ形になる
  const disabled = serializeField({ field_id: "person_備考", kind: "text",
    rect: { x: 0, y: 100, w: 10, h: 10 }, output: false });
  assert.deepEqual(disabled,
    { field_id: "person_備考", kind: "text", rect: { x: 0, y: 100, w: 10, h: 10 },
      output: false });

  // output:true を明示された場合も、GUI の直列化規則では省略扱いになる
  // （B-S4: 無関係な保存で template_hash を動かさない）
  const explicitTrue = serializeField({ field_id: "person_住所1", kind: "text",
    rect: { x: 0, y: 200, w: 10, h: 10 }, output: true });
  assert.deepEqual(explicitTrue,
    { field_id: "person_住所1", kind: "text", rect: { x: 0, y: 200, w: 10, h: 10 } });
  assert.ok(!("output" in explicitTrue));
});

// ---------------------------------------------------------------- issue #69 Q-H3
// Editor のグローバル keydown は実行タブ表示中も生きていて、Delete で欄が
// 消える・ボタンにフォーカスがある状態で Space を押すとボタンのクリックが
// 奪われる事故があった。判定を純関数 keyAction に切り出して固定する
const keyEv = (over = {}) => ({ code: "", key: "", shiftKey: false,
  ctrlKey: false, metaKey: false, ...over });
const keyCtx = (over = {}) => ({ active: true, typing: false,
  isButtonFocused: false, hasSel: true, ...over });

test("Q-H3: 非アクティブ（実行タブ表示中）は種類を問わず常に null を返す", () => {
  assert.equal(keyAction(keyEv({ key: "Delete" }), keyCtx({ active: false })), null);
  assert.equal(keyAction(keyEv({ key: "ArrowLeft" }), keyCtx({ active: false })), null);
  assert.equal(keyAction(keyEv({ code: "Space" }), keyCtx({ active: false })), null);
  assert.equal(keyAction(keyEv({ key: "z", ctrlKey: true }), keyCtx({ active: false })), null);
  assert.equal(keyAction(keyEv({ key: "Escape" }), keyCtx({ active: false })), null);
});

test("Q-H3: ボタンにフォーカスがある間の Space は null（preventDefault されない＝ボタンのクリックに譲る）", () => {
  const r = keyAction(keyEv({ code: "Space" }), keyCtx({ isButtonFocused: true }));
  assert.equal(r, null);
});

test("Q-H3: 通常入力欄にフォーカスがある間の Space も typing 扱いで null になる（ボタンだけの特例ではない）", () => {
  const r = keyAction(keyEv({ code: "Space" }), keyCtx({ typing: true, isButtonFocused: false }));
  assert.equal(r, null);
});

test("Q-H3: アクティブ・非 typing・選択ありなら Delete は delete アクション（preventDefault: true）", () => {
  const r = keyAction(keyEv({ key: "Delete" }), keyCtx());
  assert.deepEqual(r, { action: { type: "delete" }, preventDefault: true });
});

test("Q-H3: 入力欄にフォーカスがある間の Delete は null（テキスト編集を優先）", () => {
  const r = keyAction(keyEv({ key: "Delete" }), keyCtx({ typing: true }));
  assert.equal(r, null);
});

test("Q-H3: Shift+矢印キーは10pxのnudgeアクションを返す", () => {
  const r = keyAction(keyEv({ key: "ArrowUp", shiftKey: true }), keyCtx());
  assert.deepEqual(r, { action: { type: "nudge", dx: 0, dy: -10 }, preventDefault: true });
});

// ---------------------------------------------------------------- issue #69 Q-H2
// 編集画面の保存は範囲外（面のどちらにも入らない）欄/表/除外領域を無言で
// 欠落させていた——画像を開き直すと H が変わり、過去の座標が範囲外に
// なることがある。① clampRect で移動・リサイズ・新規作成の出口を塞ぎ、
// ② outOfFaceElements + buildTemplateJson の droppedCount で保存時に
// 検知できることを固定する
test("Q-H2 clampRect: 負の座標は 0 へ丸められる", () => {
  const r = clampRect({ x: -10, y: -20, w: 100, h: 50 }, 1000, 1000);
  assert.deepEqual(r, { x: 0, y: 0, w: 100, h: 50 });
});

test("Q-H2 clampRect: 右下へのはみ出しは矩形の大きさを保ったまま位置だけ戻す", () => {
  const r = clampRect({ x: 950, y: 980, w: 100, h: 50 }, 1000, 1000);
  assert.deepEqual(r, { x: 900, y: 950, w: 100, h: 50 });
});

test("Q-H2 clampRect: 面より大きい矩形は面いっぱいまで縮められる", () => {
  const r = clampRect({ x: 10, y: 10, w: 2000, h: 3000 }, 1000, 1000);
  assert.deepEqual(r, { x: 0, y: 0, w: 1000, h: 1000 });
});

test("Q-H2: clampRect を毎回の出口で通すと連続 nudge でも y が 0 未満にならない", () => {
  const W = 2490, H = 1880;
  let r = { x: 100, y: 3, w: 50, h: 50 };
  for (let i = 0; i < 10; i++) r = clampRect({ ...r, y: r.y - 1 }, W, H);
  assert.equal(r.y, 0, `連続 nudge の末尾で y が負になった: ${r.y}`);
});

test("Q-H2 outOfFaceElements: y=-5 の欄と y>=H の欄を id で返す", () => {
  const H = 1880, splitY = 940;
  const fields = [
    { uid: "u1", field_id: "f_neg", kind: "text", marks: [],
      rect: { x: 0, y: -5, w: 10, h: 10 } },
    { uid: "u2", field_id: "f_over", kind: "text", marks: [],
      rect: { x: 0, y: H, w: 10, h: 10 } },
    { uid: "u3", field_id: "f_ok", kind: "text", marks: [],
      rect: { x: 0, y: 100, w: 10, h: 10 } },
  ];
  const out = outOfFaceElements({ fields, tables: [], excls: [], splitY, H });
  assert.deepEqual(out.slice().sort(), ["f_neg", "f_over"]);
});

test("Q-H2 buildTemplateJson: clamp 済み入力なら droppedCount=0 で全要素を書く（欠落ゼロ）", () => {
  const W = 2490, H = 3510, splitY = 1880;
  const fields = [
    { uid: "u1", field_id: "f1", kind: "text", marks: [],
      rect: { x: 10, y: 10, w: 100, h: 50 } },
    { uid: "u2", field_id: "f2", kind: "text", marks: [],
      rect: { x: 10, y: 2000, w: 100, h: 50 } },
  ];
  const tables = [
    { uid: "t1", table_id: "tbl1", row_pitch: 100, row_height: 90,
      blocks: [{ x: 200, y: 600, rows: 3 }],
      columns: [{ name: "c1", x_offset: 0, width: 100, kind: "text", subfields: "", marks: [] }] },
  ];
  const excls = [{ uid: "e1", id: "excl_1", rect: { x: 50, y: 50, w: 100, h: 100 } }];
  const meta = { template_id: "t", render_dpi: 300, image: null, record: { pages: 1 } };
  const { template, droppedCount } = buildTemplateJson({ fields, tables, excls, splitY, W, H, meta });
  assert.equal(droppedCount, 0);
  assert.equal(template.faces[0].fields.length + template.faces[1].fields.length, fields.length);
  assert.equal(template.faces[0].tables.length + template.faces[1].tables.length, tables.length);
  assert.equal(
    template.faces[0].exclusions.length + template.faces[1].exclusions.length, excls.length);
});

test("Q-H2 (L-Q1): fieldsForFace と outOfFaceElements の面判定述語が一致する", () => {
  const H = 1000, splitY = 400;
  const fields = [
    { uid: "u1", field_id: "a", kind: "text", marks: [], rect: { x: 0, y: -1, w: 10, h: 10 } },
    { uid: "u2", field_id: "b", kind: "text", marks: [], rect: { x: 0, y: 0, w: 10, h: 10 } },
    { uid: "u3", field_id: "c", kind: "text", marks: [], rect: { x: 0, y: 399, w: 10, h: 10 } },
    { uid: "u4", field_id: "d", kind: "text", marks: [], rect: { x: 0, y: 400, w: 10, h: 10 } },
    { uid: "u5", field_id: "e", kind: "text", marks: [], rect: { x: 0, y: 999, w: 10, h: 10 } },
    { uid: "u6", field_id: "f", kind: "text", marks: [], rect: { x: 0, y: 1000, w: 10, h: 10 } },
  ];
  const inFront = new Set(fieldsForFace(fields, "front", splitY, H).map((f) => f.field_id));
  const inBack = new Set(fieldsForFace(fields, "back", splitY, H).map((f) => f.field_id));
  const outIds = new Set(outOfFaceElements({ fields, tables: [], excls: [], splitY, H }));
  for (const f of fields) {
    const coveredByFace = inFront.has(f.field_id) || inBack.has(f.field_id);
    assert.equal(coveredByFace, !outIds.has(f.field_id),
      `${f.field_id}: fieldsForFace と outOfFaceElements の判定が食い違う`);
  }
});

// ---------------------------------------------------------------- ウィンドウサイズ最終仕様
// targetWindowHeight: 完了サマリ表示時にウィンドウを縦拡大する高さのクランプ
// ロジック（最小=既定・最大=作業領域相当）。実際の setSize 呼び出しは
// Tauri 実機依存のため、ここでは純関数のクランプ挙動だけを固定する
test("targetWindowHeight: 通常は本文高+アプリバー高がそのまま採用される", () => {
  const h = targetWindowHeight(800, 65, 1200);
  assert.equal(h, 865);
  assert.ok(h > RUN_WINDOW_HEIGHT_DEFAULT, "既定より小さいケースではない前提が崩れている");
});

test("targetWindowHeight: 作業領域を超える本文は上限（作業領域）でクランプされる", () => {
  const h = targetWindowHeight(5000, 65, 1000);
  assert.equal(h, 1000, "作業領域の高さでクランプされていない");
});

test("targetWindowHeight: workAreaHeight が不正値なら安全側の固定上限にフォールバックし、"
  + "本文が既定未満でも既定を割らない", () => {
  // currentMonitor() が取得できない/失敗した場合を想定した NaN
  const fallback = targetWindowHeight(10, 65, NaN);
  assert.equal(fallback, RUN_WINDOW_HEIGHT_DEFAULT,
    "本文が小さいのに既定より低い値を返している");
  // 0 や負値など、作業領域として意味を持たない値も同様にフォールバックする
  assert.equal(targetWindowHeight(5000, 65, 0), targetWindowHeight(5000, 65, NaN),
    "workAreaHeight=0 が NaN と異なる扱いになっている");
  assert.equal(targetWindowHeight(5000, 65, -100), targetWindowHeight(5000, 65, NaN),
    "workAreaHeight が負値でも同じフォールバックになっていない");
  // contentHeight/chromeHeight 側の不正値（NaN・負値）も落ちずに既定へ丸まる
  assert.equal(targetWindowHeight(NaN, NaN, NaN), RUN_WINDOW_HEIGHT_DEFAULT);
  assert.equal(targetWindowHeight(-50, 65, 1200), RUN_WINDOW_HEIGHT_DEFAULT);
});

test("RUN_WINDOW_WIDTH は tauri.conf.json の既定幅（730）と一致する", () => {
  assert.equal(RUN_WINDOW_WIDTH, 730);
});

// ---------------------------------------------------------------- issue Q-ME
// parseVerify: event:"verify" 行を1つも見なかった場合を parsed=false で
// 区別する。以前はこの区別が無く、budgetCap 900 等の既定値がそのまま
// 「現状」として画面に出ていた（検証が走っていないのに走った体を装う）
test("parseVerify: 実 core と同型の verify 応答は parsed=true・各値を反映する", () => {
  const lines = [
    { event: "verify", check: "template", ok: true, output_disabled_cells: 3 },
    { event: "verify", check: "poppler", ok: true },
    { event: "verify", check: "local_storage", ok: true },
    { event: "verify", check: "api_budget", ok: true, used: 40, cap: 900, free_tier: 1000 },
    { event: "verify", check: "credentials", ok: true, state: "dpapi" },
  ].map((e) => JSON.stringify(e)).join("\n");
  const v = parseVerify(lines);
  assert.equal(v.parsed, true);
  assert.equal(v.template, true);
  assert.equal(v.poppler, true);
  assert.equal(v.storage, true);
  assert.equal(v.cred, "dpapi");
  assert.equal(v.budgetUsed, 40);
  assert.equal(v.budgetCap, 900);
  assert.equal(v.outputDisabledCells, 3);
  assert.equal(v.rawFirstLine, undefined, "parsed=true のときは rawFirstLine を持たない");
});

test("parseVerify: event:\"verify\" 行が1つも無ければ parsed=false・生エラーの先頭行を保持する", () => {
  const v = parseVerify("Error: core が見つかりません\n詳細スタックトレース…");
  assert.equal(v.parsed, false);
  assert.equal(v.rawFirstLine, "Error: core が見つかりません");
});

test("parseVerify: 空文字列も parsed=false（budgetCap 900 等の既定値を現状として出さない）", () => {
  const v = parseVerify("");
  assert.equal(v.parsed, false);
  assert.equal(v.budgetCap, 900, "既定値自体はテスト容易性のため保持するが、呼び出し側は parsed で判定する");
});

// ---------------------------------------------------------------- issue S-MB
// credNotice: 認証キーが環境変数（平文）で使われている旨の常時警告
test("credNotice: cred===\"env\" なら警告文を返す", () => {
  const t = credNotice("env", undefined);
  assert.ok(t && t.includes("GOOGLE_APPLICATION_CREDENTIALS"), "環境変数名を含む具体的な文言であること");
  assert.ok(t.includes("DPAPI"), "取り込み後の暗号化方式に触れていること");
});

test("credNotice: envPresent===true なら cred が \"dpapi\" でも警告文を返す（Wave 2 の env_present 併存ケース）", () => {
  const t = credNotice("dpapi", true);
  assert.ok(t !== null);
});

test("credNotice: cred が env でも env_present でもなければ null（missing/dpapi 単独）", () => {
  assert.equal(credNotice("dpapi", false), null);
  assert.equal(credNotice("dpapi", undefined), null);
  assert.equal(credNotice("missing", undefined), null);
});

// ---------------------------------------------------------------- issue P-H1
// accumulationNotice: 中間データの累積が1,000頁を超えたら purge を促す
// （レビュー7巡目 Wave 0・らでん逆張り採用分）。total_done_pages・
// render_seconds は枠D が並行で追加中のキーのため、欠落を防御的に扱う
test("accumulationNotice: 1,000頁以上・render_seconds ありは件数と秒数を含む", () => {
  const t = accumulationNotice({ total_done_pages: 1500, render_seconds: 12.3 });
  assert.ok(t.includes("1500"));
  assert.ok(t.includes("12.3"));
  assert.ok(t.includes("purge"));
});

test("accumulationNotice: 999頁は null（閾値未満）", () => {
  assert.equal(accumulationNotice({ total_done_pages: 999, render_seconds: 5 }), null);
});

test("accumulationNotice: total_done_pages キー自体が無ければ null（枠D未反映・旧コア互換）", () => {
  assert.equal(accumulationNotice({}), null);
  assert.equal(accumulationNotice({ render_seconds: 99 }), null);
});

test("accumulationNotice: render_seconds が無くても1,000頁以上なら閾値超過の事実だけ伝える", () => {
  const t = accumulationNotice({ total_done_pages: 1000 });
  assert.ok(t !== null);
  assert.ok(t.includes("1000"));
  assert.ok(!t.includes("秒"), "render_seconds 欠落時は秒数の括弧書きを出さない");
});

test("noticeFor: summary で accumulationNotice が counterNotice と併記される", () => {
  const t = noticeFor({ event: "summary", total_done_pages: 2000, render_seconds: 40,
    fallback_used: 1 });
  assert.ok(t.includes("採用 1件"), "counterNotice 側の内容が失われていない");
  assert.ok(t.includes("中間データに 2000 ページ蓄積しています"));
});

// scripts/run_all_tests.py の集計器が読む形式（"N passed ... in <秒>"）で
// 出す。これが無いと「実行された試験が0件」と判定されて FAIL になる
const secs = ((Date.now() - t0) / 1000).toFixed(2);
console.log(`\n${passed} passed, ${failed} failed in ${secs}s`);
console.log(failed === 0 ? "すべて成功" : `失敗 ${failed} 件`);
process.exit(failed === 0 ? 0 : 1);
