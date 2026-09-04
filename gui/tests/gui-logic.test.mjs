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
      'export { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice, isOutput, outputAttrForJson, countOutputDisabled, findColumnPositions, findTableColumnPositions, outputCheckboxLabel, saveConfirmWarnings, unclearPopulationNote, fieldColumnPositionNote, tableColumnRangeInfo, tableColumnOrderNote, outputOrderSnapshot, outputOrderChanged, fieldGeometrySnapshot, geometryUnchanged, reorderCarveBlockedNotice, orderChangeReportNote, fieldsForFace, moveFieldOutputOrder, moveTableColumnOrder, tableColumnReorderImpactNote, columnDecreaseFor, keyAction, clampRect, outOfFaceElements, buildTemplateJson, noImageNotice, canvasInteractionAllowed, newTemplateActionAvailable, hiddenFaces, visibleFields, visibleTables, visibleExcls, selHiddenByFormat, rankCandidates, emptyTemplateFor, newTemplateNotice, restoredTemplateNotice, templateSwitchImageSizeNotice, excludedReasonJa, matchErrorJa, formatOverrideBannerText, candidateDefaultChecked, candidateOverlapWarning, overlapAcceptedNotice, candidateOverlapsExisting, candidateAriaLabel, excludedSummaryJa, templateSkipReasonNotice, shouldSwitchToCandidatesTab, fieldSpecFromCandidate, tableSpecFromCandidate, applyCandidates, renameTableColumnsWithPrefix, zeroReasonNotice, candidatesFromDetectFrames, layoutColumnMarks, choiceColumnsNeedingMarks, choiceFieldsNeedingMarks, choiceColumnMarksNotice, relativeLuminance, contrastRatio, SELECTION_COLOR, SELECTION_FILL_STYLE, HATCH_STROKE_STYLE, PAPER_BG_COLOR, CANVAS_BG_COLOR, reorderAnnouncement, nextReorderFocusDir, saveConfirmButtonLabel, saveConfirmButtonTitle, saveSuccessNotices, pushHistory, clearCandidates, uiConfirmSpec, saveOkBanner } from "./Editor.tsx";\n' +
      'export { noticeFor, STATUS_JA, outputDisabledNotice, counterNotice, snapNotice, targetWindowHeight, RUN_WINDOW_HEIGHT_DEFAULT, RUN_WINDOW_WIDTH, parseVerify, credNotice, accumulationNotice, completionNotice, reasonCodeNotice, REASON_CODE_JA, parseLastTemplate, formatLastTemplate, resolveSelectedTemplate, startDisabledReason, reusedPagesNotice, readCoreLine, emptyRunFilter, beginRun, adoptRun, finishRun, acceptsRunEvent, purgeNotice, importCredentialsNotice, completionBannerTone, appendFailure, truncatedFailureNotice, FAILURE_KEEP } from "./RunScreen.tsx";\n' +
      // AC-F11（判定不能の弱い描画）。既存の長い export 行に足すと他の作業と
      // 衝突しやすいので独立した1行にする
      'export { frameStyleFor, undecidableFaces, UNDECIDABLE_ALPHA, UNDECIDABLE_DASH, FALLBACK_DASH } from "./Editor.tsx";\n' +
      // 初回読み込みフロー（候補先行・2026-09-04）。ここも独立した1行にする
      'export { autoDetectEnabled, appliedTemplateMemory, autoApplyTarget, applyTemplateMemoryValue, initialFrameView, shouldAutoApplyMemory, formatBandApplies, staleAppliedMemoryNotice, appliedTemplateBarText, unappliedTemplateBarText, templateDecisionMsg, templateChoiceNotice, useTemplateButtonName, detectFramesEffects, autoDetectFailureNotice, candidateOverlapFlag, candidateResultApplies } from "./Editor.tsx";\n',
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
const { layoutMarks, remapMarks, applyRectToField, handleAt, resizeBy, nextOverlapPick, absorbField, subtractRect, carveField, evaluateCarve, carveWarningNotice, resolveOverlaps, exclusionRegressionNotice, exclusionChangeNotice, saveDiffNote, remapColumnMarks, extraIndexValid, expandAlignNotice, promoteFailureNotice, isOutput, outputAttrForJson, countOutputDisabled, findColumnPositions, findTableColumnPositions, outputCheckboxLabel, saveConfirmWarnings, unclearPopulationNote, fieldColumnPositionNote, tableColumnRangeInfo, tableColumnOrderNote, outputOrderSnapshot, outputOrderChanged, fieldGeometrySnapshot, geometryUnchanged, reorderCarveBlockedNotice, orderChangeReportNote, fieldsForFace, moveFieldOutputOrder, moveTableColumnOrder, tableColumnReorderImpactNote, columnDecreaseFor, keyAction, clampRect, outOfFaceElements, buildTemplateJson, noImageNotice, canvasInteractionAllowed, newTemplateActionAvailable, hiddenFaces, visibleFields, visibleTables, visibleExcls, selHiddenByFormat, rankCandidates, emptyTemplateFor, newTemplateNotice, restoredTemplateNotice, templateSwitchImageSizeNotice, excludedReasonJa, matchErrorJa, formatOverrideBannerText, candidateDefaultChecked, candidateOverlapWarning, overlapAcceptedNotice, candidateOverlapsExisting, candidateAriaLabel, excludedSummaryJa, templateSkipReasonNotice, shouldSwitchToCandidatesTab, fieldSpecFromCandidate, tableSpecFromCandidate, applyCandidates, renameTableColumnsWithPrefix, zeroReasonNotice, candidatesFromDetectFrames, layoutColumnMarks, choiceColumnsNeedingMarks, choiceFieldsNeedingMarks, choiceColumnMarksNotice, relativeLuminance, contrastRatio, SELECTION_COLOR, SELECTION_FILL_STYLE, HATCH_STROKE_STYLE, PAPER_BG_COLOR, CANVAS_BG_COLOR, reorderAnnouncement, nextReorderFocusDir, saveConfirmButtonLabel, saveConfirmButtonTitle, saveSuccessNotices, pushHistory, clearCandidates, uiConfirmSpec, saveOkBanner, noticeFor, STATUS_JA, outputDisabledNotice, counterNotice, snapNotice, targetWindowHeight, RUN_WINDOW_HEIGHT_DEFAULT, RUN_WINDOW_WIDTH, parseVerify, credNotice, accumulationNotice, completionNotice, reasonCodeNotice, REASON_CODE_JA, parseLastTemplate, formatLastTemplate, resolveSelectedTemplate, startDisabledReason, reusedPagesNotice, readCoreLine, emptyRunFilter, beginRun, adoptRun, finishRun, acceptsRunEvent, purgeNotice, importCredentialsNotice, completionBannerTone, appendFailure, truncatedFailureNotice, FAILURE_KEEP } = mod;
const { frameStyleFor, undecidableFaces, UNDECIDABLE_ALPHA, UNDECIDABLE_DASH, FALLBACK_DASH } = mod;
const { autoDetectEnabled, appliedTemplateMemory, autoApplyTarget, applyTemplateMemoryValue, initialFrameView, shouldAutoApplyMemory, formatBandApplies, staleAppliedMemoryNotice, appliedTemplateBarText, unappliedTemplateBarText, templateDecisionMsg, templateChoiceNotice, useTemplateButtonName, detectFramesEffects, autoDetectFailureNotice, candidateOverlapFlag, candidateResultApplies } = mod;

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

test("expandAlignNotice: reason=size は様式不一致を案内する赤帯文言（自動補正とは言わない・N-2）", () => {
  const r = expandAlignNotice(false, "size", "");
  assert.equal(r.isError, true, "赤帯（errMsg）に出すべき——run では様式不一致として弾かれるため");
  assert.ok(r.text.includes("用紙サイズ") && r.text.includes("様式不一致"), r.text);
  assert.ok(!r.text.includes("自動補正"),
    "寸法不一致なのに『待てば自動補正される』と誤案内している: " + r.text);
  const withPageNote = expandAlignNotice(false, "size", "PDF の 1/2 ページ目・");
  assert.ok(withPageNote.text.includes("PDF の 1/2 ページ目・"), withPageNote.text);
});

test("expandAlignNotice: reason=image/other は中立文言で自動補正を主張しない・赤帯にもしない", () => {
  for (const reason of ["image", "other"]) {
    const r = expandAlignNotice(false, reason, "");
    assert.equal(r.isError, false, reason);
    assert.ok(!r.text.includes("自動補正"), `${reason}: ` + r.text);
    assert.ok(!r.text.includes("テンプレートが壊れている"), `${reason}: ` + r.text);
  }
});

// ------------------------------------------------------------- issue #71 (a')
// expandAlignNotice: verdict（3値・FR-F02）の追加。優先順は
// template > size > mismatch > undecidable > match（07 FR-F07）
test("expandAlignNotice: verdict=mismatch は黄帯・上書き案内つき・テンプレ名を含む（FR-F04/FR-F05）", () => {
  const r = expandAlignNotice(false, "align", "", "mismatch", "chouhyo-v1");
  assert.equal(r.level, "warn", r.text);
  assert.equal(r.isError, false, "黄帯は errMsg（赤帯）に出してはいけない");
  assert.ok(r.text.includes("chouhyo-v1"), r.text);
  assert.ok(r.text.includes("様式が合いません"), r.text);
  // ころね（user_advocate）の初見ユーザー予測レビュー: 案内文が指す先の
  // ボタン名を新ラベル「判定を無視して枠を表示する」に揃える（旧ラベル
  // 「それでもこのテンプレートで開く」だとボタンと文言が食い違っていた）
  assert.ok(r.text.includes("判定を無視して枠を表示する"), r.text);
});

test("expandAlignNotice: verdict=mismatch はテンプレ破損(template)・寸法不一致(size)より優先度が低い", () => {
  const t = expandAlignNotice(false, "template", "", "mismatch");
  assert.equal(t.level, "error", "template が最優先のはず: " + t.text);
  const s = expandAlignNotice(false, "size", "", "mismatch");
  assert.equal(s.level, "error", "size が mismatch より優先のはず: " + s.text);
});

// AC-F11（2026-09-03）: 期待値を level="info" から "warn" に変えた。判定不能の
// 案内が一致と同じ灰色 12px の主メッセージに出ており、画面上で区別が付いて
// いなかった（QA 実測）。「枠は動かさない」という行動の指示は残す
test("expandAlignNotice: verdict=undecidable は黄帯で強調し、枠を動かすなの指示は残す（AC-F11）", () => {
  const r = expandAlignNotice(false, "align", "", "undecidable");
  assert.equal(r.level, "warn");
  assert.equal(r.isError, false);
  assert.ok(r.text.startsWith("※判定できませんでした:"), r.text);
  assert.ok(r.text.includes("自動補正されるため枠は動かさないでください"), r.text);
});

test("expandAlignNotice: verdict=match は成功文言と同じ（level=info）", () => {
  const r = expandAlignNotice(true, undefined, "", "match");
  assert.equal(r.level, "info");
  assert.ok(r.text.includes("位置合わせ済み"), r.text);
});

test("expandAlignNotice: verdict 未提供（旧コア）は3値経路に入らず従来分岐のまま", () => {
  const r = expandAlignNotice(false, "align", "PDF の 1/2 ページ目・");
  assert.equal(r.level, "info");
  assert.ok(!r.text.includes("様式が合いません"), r.text);
});

// hiddenFaces / visibleFields / visibleTables / visibleExcls（issue #71 (a')・
// FR-F04・設計08 §2.7.3）。draw() とヒットテストが同じ可視集合を見るための
// 純関数——述語を2つ持たない（L-Q1 の教訓と同型）
const FMT_FACES = [
  { face_id: "front", verdict: "mismatch" },
  { face_id: "back", verdict: "match" },
];
test("hiddenFaces: verdict=mismatch の面だけを集める", () => {
  const h = hiddenFaces(FMT_FACES, false);
  assert.deepEqual([...h], ["front"]);
});
test("hiddenFaces: override=true は常に空集合（上書き中は全て可視）", () => {
  assert.equal(hiddenFaces(FMT_FACES, true).size, 0);
});
test("hiddenFaces: faces 未提供（旧コア）は空集合——現行どおり全て描く", () => {
  assert.equal(hiddenFaces(undefined, false).size, 0);
});

const SPLIT_Y = 1880, IMG_H = 3510;
const mkField = (uid, y) => ({ uid, field_id: uid, kind: "text", rect: { x: 0, y, w: 10, h: 10 }, marks: [] });
const mkTable = (uid, y) => ({ uid, table_id: uid, row_pitch: 10, row_height: 8,
  blocks: [{ x: 0, y, rows: 1 }], columns: [] });
const mkExcl = (uid, y) => ({ uid, id: uid, rect: { x: 0, y, w: 10, h: 10 } });

test("visibleFields: front が hidden なら front の欄だけ落ちる（back は残る）", () => {
  const fields = [mkField("f1", 300), mkField("f2", 2000)];
  const vis = visibleFields(fields, new Set(["front"]), SPLIT_Y, IMG_H);
  assert.deepEqual(vis.map((f) => f.uid), ["f2"]);
});
test("visibleTables / visibleExcls: 同じ面判定で表・除外も落ちる", () => {
  const tables = [mkTable("t1", 300), mkTable("t2", 2000)];
  const excls = [mkExcl("e1", 300), mkExcl("e2", 2000)];
  const hidden = new Set(["front"]);
  assert.deepEqual(visibleTables(tables, hidden, SPLIT_Y, IMG_H).map((t) => t.uid), ["t2"]);
  assert.deepEqual(visibleExcls(excls, hidden, SPLIT_Y, IMG_H).map((e) => e.uid), ["e2"]);
});
test("visibleFields: hidden が空集合なら配列をそのまま返す（一致・旧コア・上書き中）", () => {
  const fields = [mkField("f1", 300), mkField("f2", 2000)];
  assert.equal(visibleFields(fields, new Set(), SPLIT_Y, IMG_H), fields);
});

// selHiddenByFormat（issue #71 (a')・スバル差し戻し2）: 出力列タブの一覧経由で
// 選ばれた sel が、隠れている面（不一致）に属していないかを判定する純関数。
// nudge／削除の入口・出力列タブの選択不可表示の両方がこの1関数を通る
test("selHiddenByFormat: 隠れた面（front）の欄を選んでいれば true", () => {
  const fields = [mkField("f1", 300)];
  const sel = { type: "field", uid: "f1" };
  const hidden = new Set(["front"]);
  assert.equal(selHiddenByFormat(sel, fields, [], [], hidden, SPLIT_Y, IMG_H), true);
});
test("selHiddenByFormat: 可視面（back）の欄を選んでいれば false", () => {
  const fields = [mkField("f2", 2000)];
  const sel = { type: "field", uid: "f2" };
  const hidden = new Set(["front"]);
  assert.equal(selHiddenByFormat(sel, fields, [], [], hidden, SPLIT_Y, IMG_H), false);
});
test("selHiddenByFormat: table／excl も同じ判定になる", () => {
  const tables = [mkTable("t1", 300)];
  const excls = [mkExcl("e1", 300)];
  const hidden = new Set(["front"]);
  assert.equal(
    selHiddenByFormat({ type: "table", uid: "t1" }, [], tables, [], hidden, SPLIT_Y, IMG_H), true);
  assert.equal(
    selHiddenByFormat({ type: "excl", uid: "e1" }, [], [], excls, hidden, SPLIT_Y, IMG_H), true);
});
test("selHiddenByFormat: sel が null・hidden が空集合なら false", () => {
  const fields = [mkField("f1", 300)];
  assert.equal(selHiddenByFormat(null, fields, [], [], new Set(["front"]), SPLIT_Y, IMG_H), false);
  assert.equal(
    selHiddenByFormat({ type: "field", uid: "f1" }, fields, [], [], new Set(), SPLIT_Y, IMG_H), false);
});
test("selHiddenByFormat: 選択中の uid が存在しない（既に削除済み等）なら false（安全側）", () => {
  assert.equal(
    selHiddenByFormat({ type: "field", uid: "ghost" }, [], [], [], new Set(["front"]), SPLIT_Y, IMG_H),
    false);
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

// ---------------------------------------------------------------- issue #72 (t)
// reusedPagesNotice（実機通し確認の指摘）: api_calls がページ数より少ない
// 理由（中間データの再利用）をサマリに説明する
test("reusedPagesNotice: undefined・0以下（旧コア・再利用なし）は非表示", () => {
  assert.equal(reusedPagesNotice(undefined), null, "旧コア（キー未提供）は非表示に倒す");
  assert.equal(reusedPagesNotice(0), null, "再利用0件は表示しない");
  assert.equal(reusedPagesNotice(-1), null, "非負のはずが崩れても表示しない（防御的）");
});
test("reusedPagesNotice: N>0 は件数と『送信なし』を含む1行を返す", () => {
  const n = reusedPagesNotice(12);
  assert.ok(n.includes("12"), n);
  assert.ok(n.includes("再利用"), n);
  assert.ok(n.includes("送信なし"), n);
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

// buildTemplateJson: 空面は書き出さない（Orchestrator決定・2回目のころね
// 実機検証で発覚した保存拒否の根本対応）。実コアは面ごとに位置合わせの
// アンカー（tables、無ければ fields の枠線・#86）を要求する（D-25）ため、fields/tables/exclusions が全て空の面をそのまま
// 書き出すと「そのアンカーが無い」という理由だけで保存が拒否されていた。
// front/back どちらでも同じ規則——ただし両方空なら front だけを残す
// （schema の faces minItems:1 を満たすため）。droppedCount は面を間引く
// 前の割り当てから計算するため、この間引きでは変わらない
test("buildTemplateJson: back が空（fields/tables/exclusions すべて0件）なら back を書き出さない", () => {
  const W = 1800, H = 1200, splitY = 1199;
  const fields = [
    { uid: "u1", field_id: "f1", kind: "text", marks: [],
      rect: { x: 100, y: 100, w: 100, h: 50 } },
  ];
  const tables = [
    { uid: "t1", table_id: "tbl1", row_pitch: 80, row_height: 80,
      blocks: [{ x: 100, y: 300, rows: 5 }],
      columns: [{ name: "列1", x_offset: 0, width: 200, kind: "text", subfields: "", marks: [] }] },
  ];
  const meta = { template_id: "t", render_dpi: 300, image: null, record: { pages: 1 } };
  const { template, droppedCount } =
    buildTemplateJson({ fields, tables, excls: [], splitY, W, H, meta });
  assert.equal(droppedCount, 0);
  assert.equal(template.faces.length, 1, "空の back が書き出されてしまっている");
  assert.equal(template.faces[0].face_id, "front");
  assert.equal(template.faces[0].fields.length, 1);
  assert.equal(template.faces[0].tables.length, 1);
});
test("buildTemplateJson: front/back とも空なら front だけを残す（schema の faces minItems:1）", () => {
  const W = 1800, H = 1200, splitY = 600;
  const meta = { template_id: "t", render_dpi: 300, image: null, record: { pages: 1 } };
  const { template, droppedCount } =
    buildTemplateJson({ fields: [], tables: [], excls: [], splitY, W, H, meta });
  assert.equal(droppedCount, 0);
  assert.equal(template.faces.length, 1);
  assert.equal(template.faces[0].face_id, "front");
});
test("buildTemplateJson: front が空・back に内容があれば back だけを残す", () => {
  const W = 1800, H = 1200, splitY = 600;
  const fields = [
    { uid: "u1", field_id: "f1", kind: "text", marks: [],
      rect: { x: 100, y: 900, w: 100, h: 50 } },
  ];
  const meta = { template_id: "t", render_dpi: 300, image: null, record: { pages: 1 } };
  const { template, droppedCount } =
    buildTemplateJson({ fields, tables: [], excls: [], splitY, W, H, meta });
  assert.equal(droppedCount, 0);
  assert.equal(template.faces.length, 1, "空の front が書き出されてしまっている");
  assert.equal(template.faces[0].face_id, "back");
  assert.equal(template.faces[0].fields.length, 1);
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

test("targetWindowHeight: テンプレート選択カード込みの実測本文高（issue #72 (t)・実機通し確認）は"
  + "旧既定（620）を上回り、新しい RUN_WINDOW_HEIGHT_DEFAULT（780）に収まる", () => {
  // 実機 WebView2（CDP 接続）で実測した .run-screen の scrollHeight
  // （フォルダ未選択・テンプレート一覧取得済みの初期状態・2026-09-03）。
  // 修正前はこの状態でも RUN_WINDOW_HEIGHT_DEFAULT（旧620）に固定していた
  // ため、697px の本文が 555px の表示域に収まらず縦スクロールが出ていた
  const MEASURED_CONTENT_HEIGHT = 697;
  const OLD_DEFAULT = 620;
  const neededWithChrome = MEASURED_CONTENT_HEIGHT + 65;   // = 762
  assert.ok(neededWithChrome > OLD_DEFAULT,
    "旧既定のままでは収まらないことを示す前提が崩れている（実測が既定を上回らない）");
  // 新しい既定値は実測の必要量を安全マージン込みで上回るため、targetWindowHeight
  // は「既定（下限クランプ）」をそのまま返す——本文がその既定の中に収まり、
  // 追加の拡大（＝縦スクロールに頼る余地）が発生しない、が実際の確認内容
  const h = targetWindowHeight(MEASURED_CONTENT_HEIGHT, 65, 1200);
  assert.equal(h, RUN_WINDOW_HEIGHT_DEFAULT);
  assert.ok(RUN_WINDOW_HEIGHT_DEFAULT >= neededWithChrome,
    "新しい既定値がこの実測本文を収めきれていない");
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
  // issue #52 M-11 で実行画面に削除ボタンができた。旧文言（issue N-6 の
  // 「コマンド（purge --yes）」）ではなく、画面にある実際のボタン名で案内する
  assert.ok(t.includes("「読み取ったデータを削除」"), t);
  assert.ok(!t.includes("purge --yes"), "画面にある操作をコマンドで案内しない");
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

// ---------------------------------------------------------------- issue N-1
// completionNotice: exit!=0 の赤帯文言。サマリを受け取っていれば「中断」では
// ない（コアは走り切って Excel も書いている）ため、文言と導線を切り替える
const sum = (over = {}) => ({ pages: 3, rows: 3, align_failed: 0, api_calls: 3,
  unclear_cells: 0, overflow: 0, format_mismatch: 0, ...over });

test("completionNotice: exit 0 は null（赤帯を出さない）", () => {
  assert.equal(completionNotice(sum(), 0), null);
  assert.equal(completionNotice(null, 0), null);
});

test("completionNotice: サマリ未受信の exit!=0 は従来どおり中断＋続きから", () => {
  const t = completionNotice(null, 1);
  assert.ok(t.includes("中断されました（終了コード 1）"), t);
  assert.ok(t.includes("続きから処理します"), t);
});

test("completionNotice: 全ページ様式不一致は中断と言わず用紙サイズの確認へ誘導", () => {
  const t = completionNotice(sum({ rows: 3, format_mismatch: 3 }), 1);
  assert.ok(!t.includes("中断"), `「中断」を使ってはいけない: ${t}`);
  assert.ok(!t.includes("続きから"), `再実行を促してはいけない: ${t}`);
  assert.ok(t.includes("すべてのページが様式不一致でした"), t);
  assert.ok(t.includes("用紙サイズ"), t);
});

test("completionNotice: 位置合わせ失敗と様式不一致が混在なら件数を内訳で出す", () => {
  const t = completionNotice(sum({ rows: 4, align_failed: 3, format_mismatch: 1 }), 1);
  assert.ok(!t.includes("中断"), t);
  assert.ok(t.includes("位置合わせ失敗 3 件"), t);
  assert.ok(t.includes("様式不一致 1 件"), t);
});

test("completionNotice: format_mismatch が無い旧コアでも中断文言へは戻らない", () => {
  const { format_mismatch, ...old } = sum({ rows: 2, align_failed: 2 });
  const t = completionNotice(old, 1);
  assert.ok(!t.includes("中断"), t);
  assert.ok(t.includes("様式不一致 0 件"), t);
});

// ------------------------------------------------------------- issue #71 (a')
// completionNotice: format_mismatch_pre_send が全ページと一致するとき、
// 「用紙サイズ確認」ではなく「テンプレートを選び直す／作る」へ誘導する
// （設計08 §2.8）。format_mismatch_pre_send===rows は format_mismatch===rows
// を含意するため、この分岐を先に見る
test("completionNotice: 送信前に全ページ様式不一致なら、用紙サイズではなくテンプレの選び直しへ誘導", () => {
  const t = completionNotice(
    sum({ rows: 3, format_mismatch: 3, format_mismatch_pre_send: 3 }), 1);
  assert.ok(!t.includes("中断"), t);
  assert.ok(!t.includes("用紙サイズ"),
    "送信前に判定できた不一致では用紙サイズ確認ではなくテンプレ選び直しへ誘導すべき: " + t);
  assert.ok(t.includes("テンプレートを選び直す"), t);
});

test("completionNotice: pre_send が一部のみ（rows と不一致）なら従来の用紙サイズ文言のまま", () => {
  const t = completionNotice(
    sum({ rows: 3, format_mismatch: 3, format_mismatch_pre_send: 1 }), 1);
  assert.ok(t.includes("すべてのページが様式不一致でした"), t);
  assert.ok(t.includes("用紙サイズ"), t);
});

test("completionNotice: format_mismatch_pre_send が無い（旧コア）なら従来分岐のまま", () => {
  const t = completionNotice(sum({ rows: 3, format_mismatch: 3 }), 1);
  assert.ok(t.includes("すべてのページが様式不一致でした"), t);
});

// reasonCodeNotice（issue #71 (a')・設計08 §2.4.3・スバル差し戻し1で
// frame_edge を「位置合わせ失敗」グループへ訂正し frame_check_failed を追加）
// reason_code → 平易な言葉
test("reasonCodeNotice: frame_size/frame_lines/frame_ambiguous（様式不一致・送信前）は同じ言葉になる", () => {
  for (const code of ["frame_size", "frame_lines", "frame_ambiguous"]) {
    assert.equal(reasonCodeNotice(code), "様式が違うため送信前に止めました", code);
  }
});
test("reasonCodeNotice: map_failed 系（様式不一致・送信後の判定）は別の言葉になる", () => {
  // issue #80 で row_build_failed はこのグループから外れた（status が
  // 「様式不一致」→「出力失敗」へ移ったため）。下の #80 のテストで拾う
  for (const code of ["map_failed", "outside_ratio"]) {
    assert.equal(reasonCodeNotice(code), "送信後に様式不一致と判定しました", code);
  }
});
test("#80 reasonCodeNotice: row_build_failed / row_build_bug は出力失敗の言葉で、データ起因とコード欠陥を分ける", () => {
  const data = reasonCodeNotice("row_build_failed");
  const bug = reasonCodeNotice("row_build_bug");
  assert.ok(data, "row_build_failed の文言が無い");
  assert.ok(bug, "row_build_bug の文言が無い");
  assert.notEqual(data, bug, "データ起因とコード欠陥が同じ文言になっている");
  // 様式の問題として案内しない（06 §7・利用者がテンプレートを疑う原因）
  assert.ok(!data.includes("様式"), data);
  assert.ok(!bug.includes("様式"), bug);
  // コード欠陥側はログを見る導線を残す（frame_check_failed と同じ調子）
  assert.ok(bug.includes("不具合"), bug);
});
test("#80 STATUS_JA に「出力失敗」がある（render 段の新ステータス・9値化）", () => {
  const ja = STATUS_JA["出力失敗"];
  assert.ok(ja, "STATUS_JA に「出力失敗」が無い");
  assert.ok(!ja.includes("様式"), ja);
});
test("reasonCodeNotice: frame_few_lines / frame_edge / frame_boundary は位置合わせ失敗の言葉になる（frame_edge は判定不能側）", () => {
  // frame_edge（edge_mismatch）は07 v1.2/08 ★1 で「不一致」から「判定不能」へ
  // 訂正された。上端が1本かすれた本物の紙に「様式が違う」と言わないため、
  // frame_few_lines/frame_boundary と同じ「位置合わせ失敗」グループに入る
  for (const code of ["frame_few_lines", "frame_edge", "frame_boundary"]) {
    assert.equal(reasonCodeNotice(code), "罫線が読み取れず位置合わせできませんでした", code);
  }
});
test("reasonCodeNotice: frame_check_failed（AC-F14・判定関数の例外）は専用文言でコード欠陥の可能性に触れる", () => {
  const t = reasonCodeNotice("frame_check_failed");
  assert.ok(t.includes("エラーが発生"), t);
  assert.ok(t.includes("コード欠陥"), t);
});
test("reasonCodeNotice: 未知コード・未提供は null（存在しない説明を捏造しない）", () => {
  assert.equal(reasonCodeNotice(undefined), null);
  assert.equal(reasonCodeNotice("unknown_code"), null);
});
// REASON_CODE_JA のキー集合が 08 §2.4.3 の理由コード表と完全一致することを
// 機械的に固定する（スバル差し戻し1「表のキー集合と一致を assert」）。
// issue #80 で row_build_bug を足して 10 → 11 コード
const FRAME_REASON_CODES_08 = [
  "frame_size", "frame_lines", "frame_ambiguous",
  "map_failed", "outside_ratio",
  "row_build_failed", "row_build_bug",
  "frame_few_lines", "frame_edge", "frame_boundary", "frame_check_failed",
];
test("REASON_CODE_JA: 08 §2.4.3 の11コードとキー集合が完全一致する", () => {
  assert.deepEqual(Object.keys(REASON_CODE_JA).sort(), [...FRAME_REASON_CODES_08].sort());
});

// ---------------------------------------------------------------- 2026-09-02
// noImageNotice / canvasInteractionAllowed: 画像を開く前のキャンバス案内と
// 操作ガード（ユーザー指摘: 出荷テンプレの自動読み込み・8/31 対応 は維持しつつ、
// 画像の無いキャンバスに枠だけ描かれる・操作できるのは誤解を招く）
test("noImageNotice: template_id・欄数・表数が反映され、text は line1+句点+line2 に一致する（恒真判定にしない）", () => {
  const n = noImageNotice("chouhyo-v1", 12, 3);
  assert.ok(n.line1.includes("chouhyo-v1"), n.line1);
  assert.ok(n.line1.includes("12"), n.line1);
  assert.ok(n.line1.includes("3"), n.line1);
  // マリンレビュー M-1: 自動読み込みされた出荷テンプレか loadTemplate 経由の
  // 利用者自身の JSON かを区別しない中立な文言にする（「出荷」を含めない）
  assert.ok(!n.line1.includes("出荷"), n.line1);
  assert.ok(n.line2.includes("画像") || n.line2.includes("PDF"), n.line2);
  assert.equal(n.text, `${n.line1}。${n.line2}`, n.text);
});

test("noImageNotice: 欄数・表数がどちらも0なら『読み込めていません』の未読込文言になる（M-2: 嘘の『読み込み済み』を出さない）", () => {
  const n = noImageNotice("chouhyo-v1", 0, 0);
  assert.ok(n.line1.includes("読み込めていません"), n.line1);
  assert.ok(!n.line1.includes("読み込み済み"), n.line1);
  assert.equal(n.text, `${n.line1}。${n.line2}`, n.text);
});

// コーディネータ指摘8: tool は結果に影響しない恒等関数（hasImage を素通し）
// になったため、複数ツールを回す2件を1件に統合する（gui-logic 総数 -1）。
// パン許可自体は onDown の分岐順（この関数の呼び出しより前）で担保する
test("canvasInteractionAllowed: 画像の有無だけで決まり、tool（pan 含む）は結果に影響しない", () => {
  assert.equal(canvasInteractionAllowed(false, "select"), false);
  assert.equal(canvasInteractionAllowed(false, "pan"), false);
  assert.equal(canvasInteractionAllowed(true, "select"), true);
  assert.equal(canvasInteractionAllowed(true, "pan"), true);
});

// ---------------------------------------------------------------- issue #72 (t)
// rankCandidates（設計08 §3.4・AC-F53/F54）。並び順・推奨・スコア表示の
// 出し分けは core/Rust ではなくこの関数だけが決める（表示規則）
const cand = (name, kind, verdict, score) =>
  ({ name, kind, template_id: name, fields: 10, tables: 1, updated_at: "2026-09-01T00:00:00+09:00",
     verdict, reason: verdict === "match" ? "" : "lines", score, detected: 10, expected: 10 });

test("rankCandidates: 一致候補が1件ならスコア降順・その1件を推奨・スコア表示", () => {
  const cands = [cand("B", "user", "mismatch", 0.1), cand("A", "shipped", "match", 0.9)];
  const r = rankCandidates(cands, false);
  assert.equal(r.recommend, "A");
  assert.equal(r.showScore, true);
  assert.deepEqual(r.rows.map((c) => c.name), ["A", "B"]);
  assert.ok(r.notice.includes("罫線"), r.notice);
});

test("rankCandidates: 一致候補が複数で差が0.1以上なら最上位を推奨", () => {
  const cands = [cand("低", "user", "match", 0.5), cand("高", "shipped", "match", 0.9)];
  const r = rankCandidates(cands, false);
  assert.equal(r.recommend, "高");
  assert.equal(r.showScore, true);
  assert.deepEqual(r.rows.map((c) => c.name), ["高", "低"]);
});

test("rankCandidates: 一致候補が複数で差が0.1未満なら推奨なし・名前順・スコア非表示", () => {
  const cands = [cand("Z帳票", "user", "match", 0.90), cand("A帳票", "shipped", "match", 0.85)];
  const r = rankCandidates(cands, false);
  assert.equal(r.recommend, null);
  assert.equal(r.showScore, false);
  assert.deepEqual(r.rows.map((c) => c.name), ["A帳票", "Z帳票"]);
});

test("rankCandidates: truncated なら名前順・推奨なし・スコア非表示・打ち切りに触れる", () => {
  const cands = [cand("Z", "user", "match", 0.9), cand("A", "shipped", "mismatch", 0.1)];
  const r = rankCandidates(cands, true);
  assert.equal(r.recommend, null);
  assert.equal(r.showScore, false);
  assert.deepEqual(r.rows.map((c) => c.name), ["A", "Z"]);
  assert.ok(r.notice.includes("打ち切り"), r.notice);
});

test("rankCandidates: 一致候補ゼロなら名前順・推奨なしだがスコアは表示する", () => {
  const cands = [cand("Z", "user", "mismatch", 0.3), cand("A", "shipped", "mismatch", 0.1)];
  const r = rankCandidates(cands, false);
  assert.equal(r.recommend, null);
  assert.equal(r.showScore, true);
  assert.deepEqual(r.rows.map((c) => c.name), ["A", "Z"]);
});

test("rankCandidates: notice には常に幾何一致のみを見ている旨を含める", () => {
  for (const [cands, truncated] of [
    [[cand("A", "shipped", "match", 0.9)], false],
    [[cand("A", "shipped", "mismatch", 0.1)], false],
    [[cand("A", "shipped", "match", 0.9)], true],
  ]) {
    assert.ok(rankCandidates(cands, truncated).notice.includes("中身の同一性は保証しません"));
  }
});

// ---------------------------------------------------------------- issue #72 (t)
// emptyTemplateFor / newTemplateNotice（FR-F30/F31・設計08 §3.6）
test("emptyTemplateFor: 画像の実寸・現在の表裏境界で2面・欄/表/除外は空", () => {
  const t = emptyTemplateFor(2490, 3510, 1880);
  assert.equal(t.image.width, 2490);
  assert.equal(t.image.height, 3510);
  assert.equal(t.faces.length, 2);
  const [front, back] = t.faces;
  assert.equal(front.face_id, "front");
  assert.equal(front.source.rect.y, 0);
  assert.equal(front.source.rect.h, 1880);
  assert.equal(back.face_id, "back");
  assert.equal(back.source.rect.y, 1880);
  assert.equal(back.source.rect.h, 3510 - 1880);
  for (const f of t.faces) {
    assert.deepEqual(f.fields, []);
    assert.deepEqual(f.tables, []);
    assert.deepEqual(f.exclusions, []);
  }
});

// splitY >= height（無関係な紙・片面の画像）は面を1つ（表面・全面）だけ
// 返す（Orchestrator決定・2回目のころね実機検証で判明: 実コアは面ごとに
// tables 1件以上を要求するため（D-25）、中身の入りようが無い裏面を機械的に
// 作ると「裏面にテーブルが無い」という理由だけで保存が拒否されていた）
test("emptyTemplateFor: splitY が画像の高さ以上なら面を1つ（表面・全面）だけ返す", () => {
  const t = emptyTemplateFor(1000, 500, 9999);
  assert.equal(t.faces.length, 1);
  const [front] = t.faces;
  assert.equal(front.face_id, "front");
  assert.equal(front.source.rect.y, 0);
  assert.equal(front.source.rect.h, 500);
});
test("emptyTemplateFor: splitY がちょうど画像の高さでも面を1つだけ返す（境界値）", () => {
  const t = emptyTemplateFor(1000, 500, 500);
  assert.equal(t.faces.length, 1);
  assert.equal(t.faces[0].source.rect.h, 500);
});
// splitY のクランプは高さ0ではなく高さ1px以上を保つ（ころね UX Must の
// 実機検証で発見: 高さ0の面は schema/template.schema.json の rect.h
// minimum:1 に反し、保存時に実コアのスキーマ検証で拒否されていた——旧
// テスト名の「負にならない」は満たしていたが「1px以上」までは検査して
// いなかった）。この分岐は splitY < height（2面のまま）のときだけ効く
test("emptyTemplateFor: splitY が0以下でも両面とも高さ1px以上を保つ（2面のまま）", () => {
  const t = emptyTemplateFor(1000, 500, 0);
  const [front, back] = t.faces;
  assert.equal(t.faces.length, 2);
  assert.equal(front.source.rect.h, 1);
  assert.equal(back.source.rect.h, 499);
});

test("newTemplateNotice: 候補なし（(b)未実装の現状）は等分割生成と手動作図を案内する", () => {
  const n = newTemplateNotice(false);
  assert.ok(n.includes("等分割"), n);
  assert.ok(n.includes("欄を追加"), n);
});

test("newTemplateNotice: 候補ありの分岐は候補確認を案内する（将来 (b) 実装後用）", () => {
  const n = newTemplateNotice(true);
  assert.ok(n.includes("候補"), n);
});

// ---------------------------------------------------------------- issue #72 (t)
// restoredTemplateNotice（スバル差し戻し1）: read_default_template が
// config.last_template を解決して返すため、起動時にどちらが復元されたかを
// last_template の値（"user:<名前>" かどうか）だけで判定する。
// template_id の値には依存しない（デモの疑似出荷は id が任意になるため）
test("restoredTemplateNotice: last_template が user:<名前> なら『前回のテンプレート』を明示する", () => {
  const n = restoredTemplateNotice("user:帳票B", "帳票B", 3, 1);
  assert.ok(n.text.includes("前回のテンプレート（帳票B）を読み込みました"), n.text);
  assert.ok(n.text.includes("欄 3"), n.text);
  assert.ok(n.text.includes("表 1"), n.text);
});
test("restoredTemplateNotice: last_template が 'shipped'・空・不正値なら従来の noImageNotice のまま", () => {
  for (const lt of ["shipped", "", "shipped:chouhyo-v1", "bogus"]) {
    const n = restoredTemplateNotice(lt, "chouhyo-v1", 220, 2);
    assert.deepEqual(n, { text: noImageNotice("chouhyo-v1", 220, 2).text }, lt);
    assert.ok(!n.text.includes("前回のテンプレート"), n.text);
  }
});
test("restoredTemplateNotice: template_id が出荷既定と違う値でも last_template が user: でなければ『前回』と言わない（デモの疑似出荷 id 対策）", () => {
  const n = restoredTemplateNotice("shipped", "demo", 1, 1);
  assert.ok(!n.text.includes("前回のテンプレート"), n.text);
});
test("restoredTemplateNotice: 欄・表がどちらも0なら（前回の表示でも）未読込文言のまま", () => {
  const n = restoredTemplateNotice("user:帳票B", "帳票B", 0, 0);
  assert.ok(n.text.includes("読み込めていません"), n.text);
});

// ---------------------------------------------------------------- issue #72 (t)
// templateSwitchImageSizeNotice（スバル差し戻し2）: テンプレート切替時、
// 表示中の画像とテンプレートの image 寸法が食い違っていたら黄帯で伝える
// （ブロックはしない）
test("templateSwitchImageSizeNotice: 寸法が食い違えば理由付きの注意を返す", () => {
  const n = templateSwitchImageSizeNotice({ w: 2490, h: 3510 }, { width: 1240, height: 1750 });
  assert.ok(n, "null が返った");
  assert.ok(n.includes("1240"), n);
  assert.ok(n.includes("3510"), n);
});
test("templateSwitchImageSizeNotice: 寸法が一致すれば null", () => {
  assert.equal(templateSwitchImageSizeNotice({ w: 2490, h: 3510 }, { width: 2490, height: 3510 }), null);
});
test("templateSwitchImageSizeNotice: 画像未表示・テンプレの image 未設定はどちらも null（比較できないだけで注意ではない）", () => {
  assert.equal(templateSwitchImageSizeNotice(null, { width: 100, height: 100 }), null);
  assert.equal(templateSwitchImageSizeNotice({ w: 100, h: 100 }, null), null);
  assert.equal(templateSwitchImageSizeNotice({ w: 100, h: 100 }, undefined), null);
});
test("rankCandidates: truncated の注記は『候補が多い・時間切れ』の文言になる（マリン core レビュー分）", () => {
  const r = rankCandidates([cand("A", "shipped", "match", 0.9)], true);
  assert.ok(r.notice.includes("候補が多い・時間切れ"), r.notice);
});

// ---------------------------------------------------------------- issue #72 (t)
// excludedReasonJa / matchErrorJa（マリン core レビュー分）: 除外理由・
// 照合失敗理由の日本語化。core・Rust の理由コードは複数箇所（list_user_
// templates と match_templates）から出るため、訳語を1関数に集約する
test("excludedReasonJa: 既知コードを日本語へ訳す（list_user_templates・match_templates 双方の値）", () => {
  assert.equal(excludedReasonJa("parse"), "JSON として読めません");
  assert.equal(excludedReasonJa("invalid_json"), "JSON として読めません",
    "core の呼称統一（invalid_json→parse）までの互換");
  assert.equal(excludedReasonJa("not_found"), "ファイルがありません");
  assert.equal(excludedReasonJa("schema"), "テンプレートの形式が不正です");
  assert.equal(excludedReasonJa("size"), "サイズ上限（5MB）超過");
  assert.equal(excludedReasonJa("limit"), "件数上限で未照合");
  assert.equal(excludedReasonJa("invalid_name"), "名前が規則に合いません");
  assert.equal(excludedReasonJa("check_failed"), "照合処理でエラー");
});
test("excludedReasonJa: 未知のコードは生値をそのまま返す（存在しない訳を捏造しない）", () => {
  assert.equal(excludedReasonJa("some_future_code"), "some_future_code");
});
test("matchErrorJa: match_templates の ok:false・固定コードを日本語へ訳す", () => {
  assert.equal(matchErrorJa("input_not_found"), "入力画像が見つかりません");
  assert.equal(matchErrorJa("expand_failed"), "画像の展開に失敗しました");
  assert.equal(matchErrorJa("input_unreadable"), "入力画像を読み込めません");
  assert.equal(matchErrorJa("internal"), "内部エラーが発生しました");
});
test("matchErrorJa: 未知のコード・未提供でも捏造せず生値／フォールバック文言を返す", () => {
  assert.equal(matchErrorJa("some_future_code"), "some_future_code");
  assert.equal(matchErrorJa(undefined), "不明なエラー");
  assert.equal(matchErrorJa(null), "不明なエラー");
});

// ---------------------------------------------------------------- issue #72 (t)
// formatOverrideBannerText（実機通し確認の指摘）: 「判定を無視して枠を
// 表示する」を押した後の常時警告に、やり直しの導線（別テンプレを試す
// 2経路）が含まれること
test("formatOverrideBannerText: 上書き中である旨と、別テンプレを試す2つの経路（帳票を開く直し・照合一覧）を含む", () => {
  const t = formatOverrideBannerText();
  assert.ok(t.includes("様式判定を無視して枠を表示しています"), t);
  assert.ok(t.includes("帳票を開く"), t);
  assert.ok(t.includes("この画像に合うテンプレート"), t);
});

// ---------------------------------------------------------------- issue #73 (b)
// 枠候補一括生成（設計08 §4）。candidatesFromDetectFrames / applyCandidates /
// fieldSpecFromCandidate / tableSpecFromCandidate / renameTableColumnsWithPrefix /
// zeroReasonNotice / candidateDefaultChecked / candidateOverlapWarning
const DETECT_FRAMES_EV = {
  candidates: [
    { id: "c1", kind: "table", face_id: "front",
      rect: { x: 100, y: 300, w: 750, h: 400 },
      blocks: [{ x: 100, y: 300, rows: 5 }],   // 平坦形（core 実測。origin にネストしない）
      row_pitch: 80, row_height: 70,
      columns: [{ x_offset: 0, width: 200 }, { x_offset: 200, width: 150 },
                { x_offset: 350, width: 400 }],
      residual_px: 0.4, overlaps_existing: false },
    { id: "c2", kind: "field", face_id: "front",
      rect: { x: 100, y: 100, w: 400, h: 80 },
      residual_px: 0.0, overlaps_existing: true },
    { id: "c3", kind: "field", face_id: "front",
      rect: { x: 600, y: 100, w: 300, h: 80 },
      residual_px: 0.2, overlaps_existing: false },
  ],
};

test("candidatesFromDetectFrames: JSON を Cand[] へ変換する（表候補の table・欄候補の overlaps を含む）", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  assert.equal(cands.length, 3);
  assert.equal(cands[0].kind, "table");
  assert.equal(cands[0].faceHint, "front");
  assert.equal(cands[0].overlaps, false);
  assert.ok(cands[0].table);
  assert.equal(cands[0].table.rows, 5);
  assert.equal(cands[0].table.columns.length, 3);
  assert.equal(cands[1].kind, "field");
  assert.equal(cands[1].overlaps, true);
  assert.equal(cands[1].table, undefined);
});

test("candidatesFromDetectFrames: 実装済み core の実際の形（id 無し・blocks[0] は平坦な{x,y,rows}・"
  + "--template 未指定時 face_id='page'）を正しく解釈する（core/chouhyo_ocr/cli.py 実測・2026-09-03）", () => {
  const ev = {
    candidates: [
      { kind: "table", face_id: "page",
        rect: { x: 100, y: 300, w: 750, h: 400 },
        blocks: [{ x: 100, y: 300, rows: 5 }],   // ネストした origin ではなく平坦
        row_pitch: 80, row_height: 70,
        columns: [{ x_offset: 0, width: 200 }],
        residual_px: 0.4, overlaps_existing: false },
      { kind: "field", face_id: "page",
        rect: { x: 100, y: 100, w: 400, h: 80 },
        residual_px: 0.0, overlaps_existing: true },
    ],
  };
  const cands = candidatesFromDetectFrames(ev);
  assert.equal(cands.length, 2);
  // id が無いので配列インデックスから振る。かつ2件とも別の id になる（衝突しない）
  assert.equal(cands[0].id, "c0");
  assert.equal(cands[1].id, "c1");
  assert.notEqual(cands[0].id, cands[1].id);
  // face_id="page" は「面ヒントなし」として null 扱い（§4.2.3: GUI が splitY で判定する）
  assert.equal(cands[0].faceHint, null);
  assert.equal(cands[1].faceHint, null);
  // blocks[0] の平坦な x/y を正しく拾う
  assert.deepEqual(cands[0].table.origin, { x: 100, y: 300 });
});

test("candidatesFromDetectFrames: 壊れた/欠落フィールドでも例外を投げず安全側の既定値に倒す", () => {
  const cands = candidatesFromDetectFrames({ candidates: [{ id: "x" }] });
  assert.equal(cands.length, 1);
  assert.equal(cands[0].kind, "field");
  assert.deepEqual(cands[0].rect, { x: 0, y: 0, w: 0, h: 0 });
  assert.equal(cands[0].faceHint, null);
  assert.equal(cands[0].overlaps, false);
});

test("candidateDefaultChecked: overlaps_existing は既定オフ・それ以外はオン（Orchestrator判断）", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  assert.equal(candidateDefaultChecked(cands[0]), true);
  assert.equal(candidateDefaultChecked(cands[1]), false);
  assert.equal(candidateDefaultChecked(cands[2]), true);
});

test("fieldSpecFromCandidate: 連番仮名を振り、既存 field_id と衝突しない", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  const spec1 = fieldSpecFromCandidate(cands[2], []);
  assert.equal(spec1.field_id, "field_01");
  assert.equal(spec1.kind, "text");
  assert.deepEqual(spec1.rect, cands[2].rect);
  const spec2 = fieldSpecFromCandidate(cands[2], ["field_01", "field_02"]);
  assert.equal(spec2.field_id, "field_03");
});

test("tableSpecFromCandidate: blocks/row_pitch/row_height/columns を template.py の tables[] スキーマへ写す", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  const spec = tableSpecFromCandidate(cands[0], []);
  assert.ok(spec);
  assert.equal(spec.table_id, "table_01");
  assert.equal(spec.row_pitch, 80);
  assert.equal(spec.row_height, 70);
  assert.deepEqual(spec.blocks, [{ x: 100, y: 300, rows: 5 }]);
  assert.deepEqual(spec.columns.map((c) => c.name), ["列1", "列2", "列3"]);
  assert.equal(spec.columns[1].x_offset, 200);
  assert.equal(spec.columns[1].width, 150);
});

test("tableSpecFromCandidate: kind=field（table 情報が無い候補）には null を返す", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  assert.equal(tableSpecFromCandidate(cands[1], []), null);
});

test("applyCandidates（AC-F19）: 既存の fields/tables を1件も削除・変更せず、選択かつ非overlapsの候補だけ末尾に追加する", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  const existingField = { uid: "u1", field_id: "person_氏名", kind: "text",
    rect: { x: 0, y: 0, w: 10, h: 10 }, marks: [] };
  const existingFields = [existingField];
  const existingTables = [];
  let n = 0;
  const makeUid = () => `new${++n}`;
  const selected = { c1: true, c2: true, c3: true };   // c2 は overlaps なので対象外のはず
  const result = applyCandidates(existingFields, existingTables, cands, selected, makeUid);
  // 既存要素は同じ参照のまま1件も減らない
  assert.equal(result.fields[0], existingField);
  assert.equal(result.fields.length, 2);   // 既存1 + c3（field）
  assert.equal(result.tables.length, 1);   // c1（table）
  assert.equal(result.acceptedCount, 2);
  // overlaps の c2 は採用されず cands に残る
  assert.deepEqual(result.cands.map((c) => c.id), ["c2"]);
});

test("applyCandidates: チェックを外した候補は overlaps でなくても対象外になる", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  const result = applyCandidates([], [], cands, { c1: false, c2: true, c3: false }, () => "u");
  assert.equal(result.acceptedCount, 0);   // c1/c3はチェック外し・c2はoverlapsで対象外
  assert.equal(result.cands.length, 3);
});

test("applyCandidates: 一括採用は carve を経由しない（既存 fields の中身が変更されないことで確認）", () => {
  const cands = candidatesFromDetectFrames(DETECT_FRAMES_EV);
  // c3（field候補・(600,100,300,80)）と重なる既存欄を用意する
  const overlappingExisting = { uid: "u1", field_id: "overlap_target", kind: "text",
    rect: { x: 650, y: 120, w: 50, h: 20 }, marks: [] };
  const result = applyCandidates([overlappingExisting], [], cands,
    { c1: true, c2: true, c3: true }, () => "u2");
  // 既存欄の rect が carve で切り抜かれていない（そのまま）ことを確認
  const kept = result.fields.find((f) => f.uid === "u1");
  assert.deepEqual(kept.rect, overlappingExisting.rect);
});

test("renameTableColumnsWithPrefix: 列名を「<接頭辞>_1..N」へ一括で変える", () => {
  const table = { uid: "t1", table_id: "table_01", row_pitch: 80, row_height: 70,
    blocks: [{ x: 100, y: 300, rows: 5 }],
    columns: [{ name: "列1", x_offset: 0, width: 200, kind: "text", subfields: "", marks: [] },
              { name: "列2", x_offset: 200, width: 150, kind: "text", subfields: "", marks: [] }] };
  const renamed = renameTableColumnsWithPrefix(table, "来場者");
  assert.deepEqual(renamed.columns.map((c) => c.name), ["来場者_1", "来場者_2"]);
  // 名前以外（x_offset/width/kind）は変えない
  assert.equal(renamed.columns[1].x_offset, 200);
});

test("renameTableColumnsWithPrefix: 接頭辞が空文字なら既定 'field' を使う", () => {
  const table = { uid: "t1", table_id: "table_01", row_pitch: 1, row_height: 1,
    blocks: [], columns: [{ name: "列1", x_offset: 0, width: 1, kind: "text", subfields: "", marks: [] }] };
  const renamed = renameTableColumnsWithPrefix(table, "   ");
  assert.equal(renamed.columns[0].name, "field_1");
});

test("zeroReasonNotice: 4つの zero_reason を案内文へ訳す（§4.2.4）", () => {
  assert.ok(zeroReasonNotice("no_lines").includes("罫線が検出できません"));
  assert.ok(zeroReasonNotice("no_rect").includes("閉じた枠になっていません"));
  assert.ok(zeroReasonNotice("all_filtered").includes("用紙の外枠"));
  assert.ok(zeroReasonNotice("too_many_lines").includes("線が多すぎて"));
});
test("zeroReasonNotice: null/undefined は null・未知コードはコードを含めて捏造しない", () => {
  assert.equal(zeroReasonNotice(null), null);
  assert.equal(zeroReasonNotice(undefined), null);
  assert.ok(zeroReasonNotice("future_code").includes("future_code"));
});

test("candidateOverlapWarning: 実態（切り抜かれる）に合わせた文言になる（スバル差し戻し Must-2）", () => {
  // 旧文言「保存時の重なり検証で拒否されることがあります」は誤り——実態は
  // saveTemplateInner の resolveOverlaps が既存枠を無言で切り抜く（拒否ではない）
  const t = candidateOverlapWarning();
  assert.ok(t.includes("重な"), t);
  assert.ok(t.includes("採用"), t);
  assert.ok(t.includes("切り抜かれ"), t);
  // スバル再レビューの懸念: 表が絡む重なりは切り抜きではなく保存拒否になる
  // （core の同一面セル重なり検査・issue #24）。両方の挙動を明記する
  assert.ok(t.includes("表が絡む"), t);
  assert.ok(t.includes("拒否"), t);
});

test("overlapAcceptedNotice: 保存時に既存枠が調整される旨を含む（スバル差し戻し Must-2・保存まで残す注意）", () => {
  const t = overlapAcceptedNotice();
  assert.ok(t.includes("採用"), t);
  assert.ok(t.includes("切り抜"), t);
  assert.ok(t.includes("表が絡む") && t.includes("拒否"), t);
});

// ---------------------------------------------------------------- issue #73 (b)
// candidateOverlapsExisting（スバル差し戻し Must-1）: tplPath が null で
// --template を渡せない経路でも、GUI 側で独立に重なりを再判定できることを
// 保証する。field との交差／table block との交差／接するだけ（面積0）は
// 非重なり、を確認する
test("candidateOverlapsExisting: 既存 field の rect と交差する候補は重なりと判定する", () => {
  const cand = { id: "c1", kind: "field", rect: { x: 90, y: 90, w: 50, h: 50 },
    faceHint: null, residual: 0, overlaps: false };
  const fields = [{ uid: "u1", field_id: "x", kind: "text",
    rect: { x: 100, y: 100, w: 50, h: 50 }, marks: [] }];
  assert.equal(candidateOverlapsExisting(cand, fields, [], 1880), true);
});

test("candidateOverlapsExisting: 既存 table の block 矩形（x..x+Σwidth, y..y+rows*row_pitch）と交差する候補は重なりと判定する", () => {
  const cand = { id: "c1", kind: "field", rect: { x: 150, y: 350, w: 50, h: 50 },
    faceHint: null, residual: 0, overlaps: false };
  const tables = [{ uid: "t1", table_id: "table_01", row_pitch: 80, row_height: 70,
    blocks: [{ x: 100, y: 300, rows: 5 }],
    columns: [{ name: "列1", x_offset: 0, width: 200, kind: "text", subfields: "", marks: [] },
              { name: "列2", x_offset: 200, width: 150, kind: "text", subfields: "", marks: [] }] }];
  // block 矩形は x:100..350（200+150）・y:300..300+80*4+70=670 の範囲
  assert.equal(candidateOverlapsExisting(cand, [], tables, 1880), true);
});

test("candidateOverlapsExisting: 接するだけ（面積0）は非重なり", () => {
  const cand = { id: "c1", kind: "field", rect: { x: 50, y: 100, w: 50, h: 50 },
    faceHint: null, residual: 0, overlaps: false };
  // cand は x:50..100・既存は x:100..150 -- 辺で接するだけで面積は重ならない
  const fields = [{ uid: "u1", field_id: "x", kind: "text",
    rect: { x: 100, y: 100, w: 50, h: 50 }, marks: [] }];
  assert.equal(candidateOverlapsExisting(cand, fields, [], 1880), false);
});

test("candidateOverlapsExisting: 何とも交差しなければ false（fields/tables 空も含む）", () => {
  const cand = { id: "c1", kind: "field", rect: { x: 0, y: 0, w: 10, h: 10 },
    faceHint: null, residual: 0, overlaps: false };
  assert.equal(candidateOverlapsExisting(cand, [], [], 1880), false);
});

test("runDetectFrames 相当（overlaps の OR）: candidatesFromDetectFrames の overlaps が false でも、"
  + "GUI 側 candidateOverlapsExisting が true を検出したら重なり扱いにする", () => {
  // tplPath が null で --template を渡せない経路の再現: core は overlaps_existing
  // を出せず false のまま返す。GUI 側の再判定と OR することで安全網を保つ
  const cands = candidatesFromDetectFrames({
    candidates: [{ kind: "field", face_id: "page",
      rect: { x: 90, y: 90, w: 50, h: 50 }, residual_px: 0, overlaps_existing: false }],
  });
  const fields = [{ uid: "u1", field_id: "x", kind: "text",
    rect: { x: 100, y: 100, w: 50, h: 50 }, marks: [] }];
  const merged = cands.map((c) => ({
    ...c, overlaps: c.overlaps || candidateOverlapsExisting(c, fields, [], 1880) }));
  assert.equal(cands[0].overlaps, false, "前提: core 由来はfalseのまま");
  assert.equal(merged[0].overlaps, true, "GUI再判定とのORでtrueに確定するはず");
});

// ---------------------------------------------------------------- issue #73 (b)
// candidateAriaLabel（ラミィ／accessibility 差し戻し Should）: チェック
// ボックスの aria-label は可視情報（種別・id・面ヒント・重なり）と同じ
test("candidateAriaLabel: 種別・id・面ヒント・重なりを可視表示と同じ内容で組み立てる", () => {
  const table = { id: "c1", kind: "table", rect: { x: 0, y: 0, w: 1, h: 1 },
    faceHint: "front", residual: 0, overlaps: false };
  // 依頼文の組み立て仕様どおり、id の直後の可変部と「を選択」の間には
  // 常に半角スペースが1つ入る（`...${faceHint?...}${overlaps?...} を選択`）
  assert.equal(candidateAriaLabel(table), "表候補 c1（front） を選択");
  const field = { id: "c2", kind: "field", rect: { x: 0, y: 0, w: 1, h: 1 },
    faceHint: null, residual: 0, overlaps: true };
  assert.equal(candidateAriaLabel(field), "欄候補 c2・既存と重なり を選択");
});

// ---------------------------------------------------------------- issue #73 (b)
// excludedSummaryJa（マリン core レビュー由来）: detect-frames の
// excluded[] を日本語の内訳へ。count<=0 は数えない・未知コードは捏造しない
test("excludedSummaryJa: count>0 の reason だけを日本語で列挙する", () => {
  const t = excludedSummaryJa([
    { reason: "page_outline", count: 2 },
    { reason: "too_small", count: 5 },
    { reason: "straddles_face", count: 0 },   // count=0 は数えない
  ]);
  assert.equal(t, "候補にしなかった枠: ページ外周 2・小さすぎる 5");
});
test("excludedSummaryJa: non_rectangular も訳し、未知コードは生値のまま使う", () => {
  assert.ok(excludedSummaryJa([{ reason: "non_rectangular", count: 1 }]).includes("長方形でない"));
  assert.ok(excludedSummaryJa([{ reason: "future_code", count: 1 }]).includes("future_code"));
});
test("excludedSummaryJa: 空・undefined・全件count0 は null（表示しない）", () => {
  assert.equal(excludedSummaryJa([]), null);
  assert.equal(excludedSummaryJa(undefined), null);
  assert.equal(excludedSummaryJa([{ reason: "page_outline", count: 0 }]), null);
});

// ---------------------------------------------------------------- issue #73 (b)
// templateSkipReasonNotice（マリン core レビュー由来）: --template 指定時に
// 寸法不一致でテンプレートが適用されなかった旨を伝える
test("templateSkipReasonNotice: template_applied:false かつ size_mismatch は面割当/重なり判定をしていない旨を伝える", () => {
  const t = templateSkipReasonNotice(false, "size_mismatch");
  assert.ok(t.includes("寸法"), t);
  assert.ok(t.includes("面の割り当て"), t);
  assert.ok(t.includes("重なり判定"), t);
});
test("templateSkipReasonNotice: template_applied:true・undefined（旧コア/未指定）は null", () => {
  assert.equal(templateSkipReasonNotice(true, null), null);
  assert.equal(templateSkipReasonNotice(undefined, undefined), null);
  // 契約は 3 値: --template 未指定は false ではなく null（適用しなかった／対象が
  // 無かったの区別・08 §4.4）。null を「寸法不一致」と誤読して案内を出さない
  assert.equal(templateSkipReasonNotice(null, null), null);
});
test("templateSkipReasonNotice: 未知の skip_reason でも捏造せず文言化する", () => {
  assert.ok(templateSkipReasonNotice(false, "future_reason").includes("future_reason"));
  assert.ok(templateSkipReasonNotice(false, null).length > 0);
});

// ---------------------------------------------------------------- issue #73 (b)
// shouldSwitchToCandidatesTab（ラミィ／accessibility 3回目確認・Must）:
// 「0件→N件」限定をやめ、生成完了のたびに（候補が既にある状態からの
// 再生成でも）切り替える。候補0件（zero_reason あり）のときだけ切り替えない
test("shouldSwitchToCandidatesTab: 生成完了で候補が1件以上あれば常に切り替える（prevLenの値によらない）", () => {
  assert.equal(shouldSwitchToCandidatesTab(0, 4, null), true, "0→N は切り替える（従来どおり）");
  assert.equal(shouldSwitchToCandidatesTab(4, 6, null), true,
    "N→M（候補が既にある状態からの再生成）でも切り替える——旧実装はここが false だった");
  assert.equal(shouldSwitchToCandidatesTab(4, 4, null), true, "同じ件数への再生成でも切り替える");
});
test("shouldSwitchToCandidatesTab: 候補0件（zero_reason あり）のときは切り替えない", () => {
  assert.equal(shouldSwitchToCandidatesTab(0, 0, "no_lines"), false);
  assert.equal(shouldSwitchToCandidatesTab(5, 0, "no_rect"), false);
});
test("shouldSwitchToCandidatesTab: nextLen=0 かつ zero_reason 無し（防御的）でも切り替えない", () => {
  assert.equal(shouldSwitchToCandidatesTab(0, 0, null), false);
  assert.equal(shouldSwitchToCandidatesTab(0, 0, undefined), false);
});

// newTemplateActionAvailable（ころね／user_advocate UX レビュー Must）:
// 従来は様式不一致の黄帯（hasFormatMismatch）でしか「この紙用に新しい
// テンプレートを作る」ボタンが出ず、寸法／向き不一致の赤帯（reason==="size"）
// では出なかった。README が唯一の復旧導線として案内しているボタンなので、
// 赤帯でも同じ条件で有効になることを確認する。reason==="template"
// （テンプレ破損）は対象外——空テンプレートを作っても保存後の再照合で
// 同じ破損テンプレに当たる問題は解決しないため
test("newTemplateActionAvailable: 寸法/向き不一致の赤帯（reason==='size'）でも有効", () => {
  assert.equal(newTemplateActionAvailable(false, "size", true), true);
});
test("newTemplateActionAvailable: テンプレ破損の赤帯（reason==='template'）では無効", () => {
  assert.equal(newTemplateActionAvailable(false, "template", true), false);
});
test("newTemplateActionAvailable: 様式不一致の黄帯（hasFormatMismatch）でも従来どおり有効", () => {
  assert.equal(newTemplateActionAvailable(true, undefined, true), true);
  assert.equal(newTemplateActionAvailable(true, "align", true), true);
});
test("newTemplateActionAvailable: 画像が無ければどちらの理由でも無効", () => {
  assert.equal(newTemplateActionAvailable(true, "size", false), false);
  assert.equal(newTemplateActionAvailable(false, "size", false), false);
});
test("newTemplateActionAvailable: 不一致要因が無ければ無効", () => {
  assert.equal(newTemplateActionAvailable(false, undefined, true), false);
  assert.equal(newTemplateActionAvailable(false, "align", true), false);
});

// ---------------------------------------------------------------- issue #72 (t)
// startDisabledReason（ころね／user_advocate の初見ユーザー予測レビュー）:
// 「読み取りを開始」が無効な理由をボタン直下へ1行出す
const VERIFY_BASE = { template: true, poppler: true, cred: "dpapi", storage: true,
  budgetUsed: 0, budgetCap: 900, parsed: true };
test("startDisabledReason: 帳票フォルダ未選択・verify 未取得はどちらも null（別の案内に任せる）", () => {
  assert.equal(startDisabledReason("", VERIFY_BASE), null);
  assert.equal(startDisabledReason("C:\\demo", null), null);
});
test("startDisabledReason: verify 未実行（parsed:false）を最優先で伝える", () => {
  assert.equal(startDisabledReason("C:\\demo", { ...VERIFY_BASE, parsed: false }),
    "検証が実行できていません（再試行してください）");
});
test("startDisabledReason: 認証キー未設定・送信上限到達・保存先NGをそれぞれ伝える", () => {
  assert.equal(startDisabledReason("C:\\demo", { ...VERIFY_BASE, cred: "missing" }),
    "認証キーが未設定です（下の「認証キーを選択」から設定してください）");
  assert.equal(startDisabledReason("C:\\demo", { ...VERIFY_BASE, budgetUsed: 900, budgetCap: 900 }),
    "今月の送信上限に達しています");
  // issue #52 M-12／Q-MJ: 逃げ道（明示チェック）ができたので、出口も案内する
  assert.equal(startDisabledReason("C:\\demo", { ...VERIFY_BASE, storage: false }),
    "保存先がクラウド同期フォルダ等の下にあります（設定で変更するか、下の確認チェックを入れてください）");
});
test("startDisabledReason: すべて問題なければ null", () => {
  assert.equal(startDisabledReason("C:\\demo", VERIFY_BASE), null);
});

// ---------------------------------------------------------------- issue #72 (t)
// parseLastTemplate / formatLastTemplate / resolveSelectedTemplate
// （FR-F27・FR-F29・設計08 §3.5）
test("parseLastTemplate: user:<名前> を分解する", () => {
  assert.deepEqual(parseLastTemplate("user:帳票B"), { kind: "user", name: "帳票B" });
});
test("parseLastTemplate: 'shipped'・空文字・未設定・不正な形式（shipped: 等）は null", () => {
  // core/chouhyo_ocr/config.py の _validate が受け付ける非 user: 値はリテラル
  // "shipped" のみ（既定値）。"" や "shipped:<名前>" は同じく null（出荷扱い）
  // に倒す——Rust 側の resolve_last_template_path はこれらも許容するが、
  // GUI が書き戻す値は "shipped" に統一する（形を1つに保つ）
  assert.equal(parseLastTemplate("shipped"), null);
  assert.equal(parseLastTemplate(""), null);
  assert.equal(parseLastTemplate(undefined), null);
  assert.equal(parseLastTemplate("shipped:chouhyo-v1"), null);
  assert.equal(parseLastTemplate("bogus"), null);
});
test("formatLastTemplate: parseLastTemplate の逆変換になる（user）", () => {
  const ref = { kind: "user", name: "帳票B" };
  assert.equal(formatLastTemplate(ref), "user:帳票B");
  assert.deepEqual(parseLastTemplate(formatLastTemplate(ref)), ref);
});
test("formatLastTemplate: shipped・null はどちらも 'shipped'（config.py の既定値・出荷は列挙しない）", () => {
  assert.equal(formatLastTemplate({ kind: "shipped", name: "chouhyo-v1" }), "shipped");
  assert.equal(formatLastTemplate(null), "shipped");
});
test("resolveSelectedTemplate: 選択中の利用者テンプレートが一覧にあればそのまま", () => {
  const r = resolveSelectedTemplate("user:帳票B", ["帳票B", "帳票C"]);
  assert.equal(r.value, "user:帳票B");
  assert.equal(r.notice, null);
});
test("resolveSelectedTemplate: 一覧から消えていれば出荷へ戻し通知を返す（削除された等）", () => {
  const r = resolveSelectedTemplate("user:帳票B", ["帳票C"]);
  assert.equal(r.value, "shipped");
  assert.ok(r.notice && r.notice.includes("帳票B"), r.notice);
});
test("resolveSelectedTemplate: 出荷選択（'shipped'）はそのまま・通知なし", () => {
  const r = resolveSelectedTemplate("shipped", ["帳票B"]);
  assert.equal(r.value, "shipped");
  assert.equal(r.notice, null);
});
test("resolveSelectedTemplate: 空文字（未設定・旧値）も出荷へ倒す・通知なし", () => {
  const r = resolveSelectedTemplate("", ["帳票B"]);
  assert.equal(r.value, "shipped");
  assert.equal(r.notice, null);
});

// --- 実行イベントの取り違え防止（issue #96）---
// Rust 側が core-line / core-err に run_id を添えるようになった（lib.rs の
// CoreLine）。フロントは「今回の実行」以外の行を捨てる。読取スレッドの
// join（Rust 側）と合わせた二重の防御のうち、フロント側の判定を固定する。

test("readCoreLine: 構造化 payload から行と run_id を取り出す", () => {
  assert.deepEqual(readCoreLine({ run_id: "4321-0", line: "hello" }),
    { line: "hello", runId: "4321-0" });
});
test("readCoreLine: run_id の無い旧形式（文字列）はフィルタ対象外にする", () => {
  // 形が想定と1つズレただけでログも進捗も出ない画面にはしない
  const r = readCoreLine("plain line");
  assert.equal(r.line, "plain line");
  assert.equal(r.runId, undefined);
  assert.equal(acceptsRunEvent(emptyRunFilter(), r.runId), true);
});
test("readCoreLine: line が欠けた payload は空行として扱う（例外にしない）", () => {
  assert.deepEqual(readCoreLine({ run_id: "1-0" }), { line: "", runId: "1-0" });
});

test("acceptsRunEvent: core-start 未着（current=null）なら通す", () => {
  // core-start と core-line はイベント名が別で到着順の保証が無い。
  // 先に行が来ても落とさない
  assert.equal(acceptsRunEvent(emptyRunFilter(), "1-0"), true);
});
test("acceptsRunEvent: core-start で確定した ID と一致すれば通す", () => {
  const f = adoptRun(emptyRunFilter(), "1-0");
  assert.equal(acceptsRunEvent(f, "1-0"), true);
});
test("acceptsRunEvent: 確定した ID と違う行は捨てる", () => {
  const f = adoptRun(emptyRunFilter(), "1-1");
  assert.equal(acceptsRunEvent(f, "1-0"), false);
});

test("実行A完了→実行B開始後に届いたAのサマリを捨てる（issue #96 本題）", () => {
  // 前回プロセスの終了直後、パイプに残っていた行が次の実行の開始後に
  // 届く経路。ここを通すと古いサマリが新しい実行の完了表示を上書きする
  let f = emptyRunFilter();
  f = beginRun(f);                 // 実行A開始
  f = adoptRun(f, "4321-0");       // core-start（A）
  assert.equal(acceptsRunEvent(f, "4321-0"), true, "A の行は A の実行中に通る");
  f = finishRun(f, "4321-0");      // run_core 解決（A）

  // A の最後の行が invoke の応答より遅れて届いても、まだ A の画面なので通す
  assert.equal(acceptsRunEvent(f, "4321-0"), true,
    "A 自身のサマリを invoke 解決後に捨ててはいけない");

  f = beginRun(f);                 // 実行B開始（画面を片付けた時点で A は「古い」）
  assert.equal(acceptsRunEvent(f, "4321-0"), false, "A の残り行は捨てる");
  assert.equal(acceptsRunEvent(f, "4321-1"), true, "B の行は core-start 前でも通る");
  f = adoptRun(f, "4321-1");       // core-start（B）
  assert.equal(acceptsRunEvent(f, "4321-0"), false);
  assert.equal(acceptsRunEvent(f, "4321-1"), true);
});

test("finishRun: core-start を取り逃しても run_core の戻り値で ID を確定できる", () => {
  // 確定していないと、次の beginRun がその実行を retired へ移せず
  // 遅れて届く行を捨てられない
  let f = finishRun(beginRun(emptyRunFilter()), "4321-0");
  assert.equal(f.current, "4321-0");
  f = beginRun(f);
  assert.equal(acceptsRunEvent(f, "4321-0"), false);
});
test("finishRun: core-start で確定済みなら戻り値では上書きしない", () => {
  const f = finishRun(adoptRun(emptyRunFilter(), "4321-1"), "4321-0");
  assert.equal(f.current, "4321-1");
});

test("adoptRun: 一度 retired にした ID を core-start が復帰させる", () => {
  // 起こらない想定だが、復帰させないと「今回の実行の行を全部捨てる」に
  // 倒れる（無反応の画面）ため、core-start を最終判断にする
  let f = beginRun(finishRun(beginRun(emptyRunFilter()), "4321-0"));
  assert.equal(acceptsRunEvent(f, "4321-0"), false);
  f = adoptRun(f, "4321-0");
  assert.equal(acceptsRunEvent(f, "4321-0"), true);
});

test("beginRun: retired は上限8件で打ち切る（無制限に伸ばさない）", () => {
  let f = emptyRunFilter();
  for (let i = 0; i < 20; i++) {
    f = beginRun(f);
    f = adoptRun(f, `4321-${i}`);
    f = finishRun(f, `4321-${i}`);
  }
  f = beginRun(f);
  assert.equal(f.retired.length, 8, `retired=${JSON.stringify(f.retired)}`);
  assert.equal(f.retired[0], "4321-19", "新しい順に並ぶ");
  assert.equal(acceptsRunEvent(f, "4321-19"), false, "直前の実行は必ず捨てる");
});
test("beginRun: 実行前（current=null・retired 空）でも壊れない", () => {
  const f = beginRun(emptyRunFilter());
  assert.deepEqual(f, { current: null, retired: [] });
});



// ================================================================ issue #67
// ラミィ（accessibility）再判定 5件。canvas 描画そのものは検査できないため、
// 描画が参照する定数・文言・判定を純関数として固定する

// 1. ハッチ不透明度（Should）
test("#67-1 HATCH_STROKE_STYLE: 出力しない欄のハッチは 45% まで濃くする", () => {
  assert.equal(HATCH_STROKE_STYLE, "rgba(28,31,38,0.45)");
});

// 5. 選択枠の色（Must・WCAG 1.4.11）
test("#67-5 relativeLuminance: WCAG の相対輝度（白=1・黒=0・既知の値と一致）", () => {
  assert.equal(relativeLuminance("#ffffff"), 1);
  assert.equal(relativeLuminance("#000000"), 0);
  // #ffd54a（旧・選択枠色）の輝度は判定書の計算値 0.6933 とおおむね一致する
  assert.ok(Math.abs(relativeLuminance("#ffd54a") - 0.6933) < 0.001,
    String(relativeLuminance("#ffd54a")));
  // 3桁表記・# なしも同じ色として扱う
  assert.equal(relativeLuminance("#fff"), relativeLuminance("ffffff"));
});
test("#67-5 relativeLuminance: 16進でない指定は投げる（黙って0扱いにしない）", () => {
  assert.throws(() => relativeLuminance("red"));
  assert.throws(() => relativeLuminance("#12345"));
});
test("#67-5 contrastRatio: 白と黒は 21:1・順序を入れ替えても同じ", () => {
  assert.ok(Math.abs(contrastRatio("#ffffff", "#000000") - 21) < 0.001);
  assert.equal(contrastRatio("#ffffff", "#a06800"), contrastRatio("#a06800", "#ffffff"));
});
test("#67-5 旧 #ffd54a は白い紙面で 3:1 未達だった（回帰の基準）", () => {
  // 枠は走査画像＝ほぼ白い紙面の上に描かれるので、拘束条件はこちら。
  // 暗い下地（#1c1f26）に対しては旧色でも 3:1 を満たしていた——当初
  // 「下地でも未達」としていたのは、実際には画面に出ない CSS の .canvas
  // 背景（#e7ebf1）で検算していたため（2026-09-03 の実測で判明）
  assert.ok(contrastRatio("#ffd54a", PAPER_BG_COLOR) < 3.0);
  assert.ok(contrastRatio("#ffd54a", CANVAS_BG_COLOR) >= 3.0);
});
test("#67-5 SELECTION_COLOR: 白紙面・キャンバス既定背景のどちらでも 3:1 以上", () => {
  assert.ok(contrastRatio(SELECTION_COLOR, PAPER_BG_COLOR) >= 3.0,
    `白: ${contrastRatio(SELECTION_COLOR, PAPER_BG_COLOR)}`);
  assert.ok(contrastRatio(SELECTION_COLOR, CANVAS_BG_COLOR) >= 3.0,
    `canvas: ${contrastRatio(SELECTION_COLOR, CANVAS_BG_COLOR)}`);
});
test("#67-5 SELECTION_FILL_STYLE: 塗り（薄いハイライト）は線とは別扱いで据え置く", () => {
  // 1.4.11 の対象は状態を伝える線側。塗りを濃くすると下の走査画像が読めなく
  // なるため、色を変えたのは線だけであることをここで固定する
  assert.equal(SELECTION_FILL_STYLE, "rgba(255,213,74,0.28)");
});

// 2. 並べ替えの読み上げ（Must・WCAG 4.1.3）
test("#67-2 reorderAnnouncement: 移動後の位置と全体数を含む", () => {
  assert.equal(reorderAnnouncement("氏名", 2, 5), "氏名 を 2 番目に移動しました（全 5 件中）");
});
test("#67-2 reorderAnnouncement: 名前が空でも呼び出し側の代替名がそのまま入る", () => {
  assert.equal(reorderAnnouncement("（名前未設定）", 1, 1),
    "（名前未設定） を 1 番目に移動しました（全 1 件中）");
});

// 3. 並べ替え後の再フォーカス（Must (a)(b)）
test("#67-3 nextReorderFocusDir: 両側とも有効なら押した向きを保つ", () => {
  assert.equal(nextReorderFocusDir(true, true, "up"), "up");
  assert.equal(nextReorderFocusDir(true, true, "down"), "down");
});
test("#67-3 nextReorderFocusDir: 移動先が先頭なら↓へ・末尾なら↑へ寄せる", () => {
  assert.equal(nextReorderFocusDir(false, true, "up"), "down");
  assert.equal(nextReorderFocusDir(true, false, "down"), "up");
});
test("#67-3 nextReorderFocusDir: 両方無効（動かせる相手がいない）は null", () => {
  assert.equal(nextReorderFocusDir(false, false, "up"), null);
  assert.equal(nextReorderFocusDir(false, false, "down"), null);
});
test("#67-3 moveFieldOutputOrder と組み合わせて境界のフォーカス先が決まる", () => {
  const mk = (uid, y) => ({ uid, field_id: uid, kind: "text", rect: { x: 0, y, w: 10, h: 10 },
                            marks: [] });
  const splitY = 1000;
  const fields = [mk("a", 100), mk("b", 200)];
  // b を上へ動かすと b は面の先頭。上ボタンは無効になるのでフォーカスは下へ
  const moved = moveFieldOutputOrder(fields, "b", "up", splitY);
  assert.ok(moved);
  assert.equal(moved[0].uid, "b");
  const canUp = moveFieldOutputOrder(moved, "b", "up", splitY) !== null;
  const canDown = moveFieldOutputOrder(moved, "b", "down", splitY) !== null;
  assert.equal(canUp, false);
  assert.equal(canDown, true);
  assert.equal(nextReorderFocusDir(canUp, canDown, "up"), "down");
});

// 4. disabled ボタンの理由提示（Must）
test("#67-4 saveConfirmButtonLabel: busy 中は実行側のラベルが処理中を示す", () => {
  assert.equal(saveConfirmButtonLabel("proceed", false), "このまま保存");
  assert.equal(saveConfirmButtonLabel("proceed", true), "保存しています…");
  assert.equal(saveConfirmButtonLabel("cancel", false), "保存しない");
  assert.equal(saveConfirmButtonLabel("cancel", true), "保存しない");
});
test("#67-4 saveConfirmButtonTitle: busy 中だけ理由を出す（普段は属性なし）", () => {
  assert.equal(saveConfirmButtonTitle(false), undefined);
  assert.ok(saveConfirmButtonTitle(true).includes("実行中"));
});

// ================================================================ issue #85
test("#85 excludedSummaryJa: not_closed を日本語（四方が線でつながっていない）で出す", () => {
  const t = excludedSummaryJa([{ reason: "not_closed", count: 3 }]);
  assert.equal(t, "候補にしなかった枠: 四方が線でつながっていない 3");
});

// ================================================================ issue #87
// 項目1: window.confirm を画面内モーダルへ移す。意味論（承諾＝先へ進む／
// 中止＝何も変えない）とボタン文言を純関数で固定する
test("#87-1 uiConfirmSpec: 重なり候補の採用は「採用する／中止」", () => {
  const s = uiConfirmSpec("adopt-overlapping-candidate");
  assert.equal(s.confirmLabel, "採用する");
  assert.equal(s.cancelLabel, "中止");
  // 本文は既存の候補重なり文言をそのまま使う（実態の説明を二重管理しない）
  assert.equal(s.body, candidateOverlapWarning());
  assert.ok(s.title.length > 0);
});
test("#87-1 uiConfirmSpec: 未保存の破棄は「破棄して続ける／戻る」", () => {
  const s = uiConfirmSpec("discard-changes");
  assert.equal(s.confirmLabel, "破棄して続ける");
  assert.equal(s.cancelLabel, "戻る");
  assert.ok(s.body.includes("破棄"), s.body);
});
test("#87-1 uiConfirmSpec: どちらも「戻せない」側が承諾ボタンになっている", () => {
  // 既定フォーカスは中止側（Enter で走るのは何も変えない方）。ここでは
  // 「承諾と中止が入れ替わっていない」ことだけを機械的に確かめる
  for (const kind of ["adopt-overlapping-candidate", "discard-changes"]) {
    const s = uiConfirmSpec(kind);
    assert.notEqual(s.confirmLabel, s.cancelLabel);
    assert.ok(["中止", "戻る"].includes(s.cancelLabel), s.cancelLabel);
  }
});

// AC-F20: 「すべて除去」で候補が空になり、確定枠は変わらない
test("#87 AC-F20 clearCandidates: 候補だけ空になり fields/tables はそのまま", () => {
  const cands = [{ id: "c1", kind: "field", rect: { x: 0, y: 0, w: 1, h: 1 },
                   faceHint: null, residual: 0, overlaps: false }];
  const fields = [{ uid: "u1", field_id: "f1", kind: "text",
                    rect: { x: 0, y: 0, w: 1, h: 1 }, marks: [] }];
  const tables = [{ uid: "t1", table_id: "table1", row_pitch: 10, row_height: 8,
                    blocks: [{ x: 0, y: 0, rows: 1 }], columns: [] }];
  const r = clearCandidates(cands, fields, tables);
  assert.deepEqual(r.cands, []);
  assert.equal(r.fields, fields, "確定枠は同じ配列のまま（差し替えない）");
  assert.equal(r.tables, tables);
  assert.ok(r.notice.includes("確定済みの枠は変更していません"));
});
test("#87 AC-F20 clearCandidates: 候補0件は null（押しても何も起きない）", () => {
  assert.equal(clearCandidates([], [], []), null);
});

// AC-F21: 候補生成1回＝Undo 1コマ
test("#87 AC-F21 pushHistory: 1回の呼び出しでコマはちょうど1つ増える", () => {
  const g1 = { fields: [], tables: [], excls: [], splitY: 100, cands: [] };
  const g2 = { ...g1, cands: [{ id: "c1" }] };
  let past = [];
  past = pushHistory(past, g1);
  assert.equal(past.length, 1);
  past = pushHistory(past, g2);
  assert.equal(past.length, 2);
  // 積むのは「その操作の前の状態」——1コマ戻すと候補が空だった時点に戻る
  assert.deepEqual(past[0].cands, []);
  assert.deepEqual(past[1].cands, [{ id: "c1" }]);
});
test("#87 AC-F21 pushHistory: 元の配列は書き換えない（新しい配列を返す）", () => {
  const past = [{ n: 1 }];
  const next = pushHistory(past, { n: 2 });
  assert.equal(past.length, 1);
  assert.equal(next.length, 2);
});
test("#87 AC-F21 pushHistory: 上限を超えると最古から落ちる（長さは上限で頭打ち）", () => {
  let past = [];
  for (let i = 0; i < 105; i++) past = pushHistory(past, { n: i });
  assert.equal(past.length, 100);
  assert.deepEqual(past[0], { n: 5 }, "最古の5コマが落ちている");
  assert.deepEqual(past[99], { n: 104 });
});

// ================================================================ issue #53 L-6
test("#53 L-6 layoutColumnMarks: 列の幅を等分し、ブロック原点からの相対で置く", () => {
  const marks = layoutColumnMarks({ x_offset: 420, width: 90 }, ["昭", "平", "令"]);
  assert.equal(marks.length, 3);
  assert.deepEqual(marks.map((m) => m.value), ["昭", "平", "令"]);
  // 30px ずつの枠に左右1pxの余白 → x_offset は 421/451/481・width は 28
  assert.deepEqual(marks.map((m) => m.x_offset), [421, 451, 481]);
  assert.deepEqual(marks.map((m) => m.width), [28, 28, 28]);
  // 縦位置は指定しない（省略＝行全体・スキーマの既定）
  for (const m of marks) {
    assert.equal(m.y_offset, undefined);
    assert.equal(m.height, undefined);
  }
});
test("#53 L-6 layoutColumnMarks: 幅が狭くても width>=1・x_offset>=0 を保つ（スキーマの下限）", () => {
  const marks = layoutColumnMarks({ x_offset: 0, width: 3 }, ["A", "B", "C"]);
  assert.equal(marks.length, 3);
  for (const m of marks) {
    assert.ok(m.width >= 1, JSON.stringify(m));
    assert.ok(Number.isInteger(m.x_offset) && m.x_offset >= 0, JSON.stringify(m));
    assert.ok(Number.isInteger(m.width));
  }
});
test("#53 L-6 layoutColumnMarks: 選択肢0件は空配列（呼び出し側が保存前に止める）", () => {
  assert.deepEqual(layoutColumnMarks({ x_offset: 0, width: 90 }, []), []);
});

const l6Table = (columns) => ({ uid: "t1", table_id: "family", row_pitch: 62,
  row_height: 56, blocks: [{ x: 120, y: 820, rows: 5 }], columns });
const l6Col = (over) => ({ name: "元号", x_offset: 420, width: 90, kind: "choice",
  subfields: "", marks: [], ...over });

test("#53 L-6 choiceColumnsNeedingMarks: 選択肢が0件の選択式列を挙げる", () => {
  assert.deepEqual(choiceColumnsNeedingMarks([l6Table([l6Col({})])]),
    ["family の 元号"]);
});
test("#53 L-6 choiceColumnsNeedingMarks: 1件だけでも足りない（schema の minItems:2）", () => {
  const t = l6Table([l6Col({ marks: [{ value: "昭", x_offset: 421, width: 28 }] })]);
  assert.deepEqual(choiceColumnsNeedingMarks([t]), ["family の 元号"]);
});
test("#53 L-6 choiceColumnsNeedingMarks: 同じ値が2つでも足りない（core が〓に倒す・#31）", () => {
  const t = l6Table([l6Col({ marks: [{ value: "昭", x_offset: 421, width: 28 },
                                      { value: "昭", x_offset: 451, width: 28 }] })]);
  assert.deepEqual(choiceColumnsNeedingMarks([t]), ["family の 元号"]);
});
test("#53 L-6 choiceColumnsNeedingMarks: 違う値が2つ以上あれば挙げない", () => {
  const t = l6Table([l6Col({ marks: layoutColumnMarks({ x_offset: 420, width: 90 },
                                                      ["昭", "平"]) })]);
  assert.deepEqual(choiceColumnsNeedingMarks([t]), []);
});
test("#53 L-6 choiceColumnsNeedingMarks: 文字の列は選択肢が無くても対象外", () => {
  const t = l6Table([l6Col({ kind: "text" })]);
  assert.deepEqual(choiceColumnsNeedingMarks([t]), []);
});
test("#53 L-6 choiceColumnsNeedingMarks: 名前が空の列は「列N」で示す（1始まり）", () => {
  const t = l6Table([l6Col({ name: "", kind: "text" }), l6Col({ name: "" })]);
  assert.deepEqual(choiceColumnsNeedingMarks([t]), ["family の 列2"]);
});
test("#53 L-6 choiceFieldsNeedingMarks: 単発欄も同じ判定（選択式で選択肢が2つ未満）", () => {
  const mk = (field_id, kind, values) => ({ uid: field_id, field_id, kind,
    rect: { x: 0, y: 0, w: 10, h: 10 },
    marks: values.map((v) => ({ value: v, rect: { x: 0, y: 0, w: 1, h: 1 } })) });
  assert.deepEqual(choiceFieldsNeedingMarks([mk("era", "choice", [])]), ["era"]);
  assert.deepEqual(choiceFieldsNeedingMarks([mk("era", "choice", ["昭"])]), ["era"]);
  assert.deepEqual(choiceFieldsNeedingMarks([mk("era", "choice", ["昭", "昭"])]), ["era"]);
  assert.deepEqual(choiceFieldsNeedingMarks([mk("era", "choice", ["昭", "平"])]), []);
  assert.deepEqual(choiceFieldsNeedingMarks([mk("name", "text", [])]), []);
  assert.deepEqual(choiceFieldsNeedingMarks([mk("", "choice", [])]), ["（名前未設定の欄）"]);
});
test("#53 L-6 choiceColumnMarksNotice: 保存を止めた理由と直し方を出す・該当なしは null", () => {
  assert.equal(choiceColumnMarksNotice([]), null);
  const t = choiceColumnMarksNotice(["family の 元号"]);
  assert.ok(t.startsWith("保存していません:"), t);
  assert.ok(t.includes("family の 元号"), t);
  assert.ok(t.includes("選択肢"), t);
});

// ================================================================ issue #65-7
test("#65-7 saveSuccessNotices: 注意が無ければ空文字（黄帯を出さない）", () => {
  assert.equal(saveSuccessNotices({ decreasedLabels: [], skipped: [],
    carveWarning: null, coreWarningCount: 0 }), "");
});
test("#65-7 saveSuccessNotices: 切り抜けなかった欄は赤帯ではなく注意1段へ入る", () => {
  const t = saveSuccessNotices({ decreasedLabels: [], skipped: ["person_氏名"],
    carveWarning: null, coreWarningCount: 0 });
  assert.ok(t.includes("person_氏名"), t);
  assert.ok(!t.includes("保存していません"), "成功時の文面に失敗の言い回しを混ぜない");
});
test("#65-7 saveSuccessNotices: 後退の疑い→切り抜けなかった欄→大きな切り抜き→コア警告の順", () => {
  const t = saveSuccessNotices({ decreasedLabels: ["欄"], skipped: ["a"],
    carveWarning: "大きく切り抜かれた欄があります", coreWarningCount: 2 });
  const iDec = t.indexOf("減った項目");
  const iSkip = t.indexOf("切り抜けなかった");
  const iCarve = t.indexOf("大きく切り抜かれた");
  const iCore = t.indexOf("コアからの警告");
  assert.ok(iDec >= 0 && iSkip > iDec && iCarve > iSkip && iCore > iCarve,
    `順序が違う: ${t}`);
  assert.ok(t.includes("2 件"), t);
});
test("#65-7 saveSuccessNotices: コア警告0件は数えない（毎回同じ定型文を出さない）", () => {
  const t = saveSuccessNotices({ decreasedLabels: [], skipped: [],
    carveWarning: null, coreWarningCount: 0 });
  assert.ok(!t.includes("コアからの警告"), t);
});

// ================================================================ issue #52 M-11
// purgeNotice: 中間データ削除（purge）の結果を「実行時のお知らせ」1行にする。
// キー名は core/chouhyo_ocr/cli.py の cmd_purge が出す event:"purged" の実測
// （removed / failed / cred_kept / output_removed / output_kept / output_failed）
test("#52 M-11 purgeNotice: 削除件数と「認証キーは残した」を出す", () => {
  const t = purgeNotice({ event: "purged", path: "C:\\wd", cred_kept: true,
    removed: 12, failed: 0 });
  assert.ok(t.includes("中間データを 12 件削除しました"), t);
  assert.ok(t.includes("認証キーは残しています"), t);
  assert.ok(!t.includes("削除できませんでした"), "失敗0件のときは失敗の話をしない");
});
test("#52 M-11 purgeNotice: 絶対パスは画面へ出さない（07 §7.3）", () => {
  const t = purgeNotice({ event: "purged", path: "C:\\wd", output_dir: "C:\\out",
    cred_kept: true, removed: 1, failed: 0, output_removed: 1, output_kept: 0,
    output_failed: 0 });
  assert.ok(!t.includes("C:\\"), t);
});
test("#52 M-11 purgeNotice: 削除できなかった件数を必ず出す（消し損ねを黙らせない）", () => {
  const t = purgeNotice({ event: "purged", cred_kept: false, removed: 5, failed: 2 });
  assert.ok(t.includes("2 件は削除できませんでした"), t);
  assert.ok(!t.includes("認証キーは残しています"), "cred_kept が false なら言わない");
});
test("#52 M-11 purgeNotice: --include-output のときだけ出力ファイルの内訳を足す", () => {
  const withOut = purgeNotice({ event: "purged", cred_kept: true, removed: 1, failed: 0,
    output_removed: 3, output_kept: 2, output_failed: 1 });
  assert.ok(withOut.includes("出力ファイルを 3 件削除しました"), withOut);
  assert.ok(withOut.includes("対象外として残したファイル 2 件"), withOut);
  assert.ok(withOut.includes("出力ファイル 1 件は削除できませんでした"), withOut);
  const without = purgeNotice({ event: "purged", cred_kept: true, removed: 1, failed: 0 });
  assert.ok(!without.includes("出力ファイル"), without);
});
test("#52 M-11 noticeFor: purged を実行時のお知らせへ配線している", () => {
  const t = noticeFor({ event: "purged", cred_kept: true, removed: 4, failed: 0 });
  assert.ok(t !== null && t.includes("中間データを 4 件削除しました"), t);
});

// ================================================================ issue #52 M-10
// importCredentialsNotice: --delete-source の結果でトーストの文言を変える。
// イベント名は cli.py の cmd_import_credentials の実測
const credLines = (...events) =>
  events.map((e) => JSON.stringify({ event: e })).join("\n");
test("#52 M-10 importCredentialsNotice: 削除できたら「削除しました」", () => {
  const t = importCredentialsNotice(
    credLines("credentials_imported", "credentials_source_deleted"));
  assert.ok(t.includes("元のファイルを削除しました"), t);
  assert.ok(!t.includes("平文"), "消えているのに残存の注意を出さない");
});
test("#52 M-10 importCredentialsNotice: 削除できなければ手作業の削除を促す", () => {
  const t = importCredentialsNotice(
    credLines("credentials_imported", "credentials_source_kept"));
  assert.ok(t.includes("削除できませんでした"), t);
  assert.ok(t.includes("鍵が平文のまま残っています"), t);
});
test("#52 M-10 importCredentialsNotice: どちらのイベントも無ければ「消していない」側へ倒す", () => {
  // --delete-source を解さない旧コア。実際には消えていないのに
  // 「削除しました」と言わない（捏造しない側の既定）
  const t = importCredentialsNotice(credLines("credentials_imported"));
  assert.ok(t.includes("元のファイルは削除してください"), t);
  assert.equal(importCredentialsNotice(""), t, "空の stdout も同じ扱い");
  assert.equal(importCredentialsNotice("not json\n"), t, "JSON 以外の行は無視する");
});

// ================================================================ issue #69 残置1
// completionBannerTone: 1件も送信せず全ページ様式不一致なら緑にしない
test("#69 残置1 completionBannerTone: 送信0かつ様式不一致ありは注意色", () => {
  assert.equal(completionBannerTone({ pages: 3, rows: 3, align_failed: 0, api_calls: 0,
    unclear_cells: 0, overflow: 0, format_mismatch: 3 }), "warn");
});
test("#69 残置1 completionBannerTone: 送信があれば従来どおり緑", () => {
  assert.equal(completionBannerTone({ pages: 3, rows: 3, align_failed: 0, api_calls: 3,
    unclear_cells: 0, overflow: 0, format_mismatch: 3 }), "ok");
});
test("#69 残置1 completionBannerTone: 送信0でも不一致0なら緑（再利用だけの実行）", () => {
  assert.equal(completionBannerTone({ pages: 3, rows: 3, align_failed: 0, api_calls: 0,
    unclear_cells: 0, overflow: 0, format_mismatch: 0 }), "ok");
});
test("#69 残置1 completionBannerTone: format_mismatch が無い旧コア・サマリ無しは緑", () => {
  assert.equal(completionBannerTone({ pages: 1, rows: 1, align_failed: 0, api_calls: 0,
    unclear_cells: 0, overflow: 0 }), "ok");
  assert.equal(completionBannerTone(null), "ok");
});

// ================================================================ issue #53 L-17
// appendFailure / truncatedFailureNotice: 失敗一覧の上限と「他 N 件」
test("#53 L-17 appendFailure: 上限までは足し、超えたら足さない（先頭を残す）", () => {
  let list = [];
  for (let i = 0; i < FAILURE_KEEP + 25; i++) list = appendFailure(list, { page_id: `p${i}` });
  assert.equal(list.length, FAILURE_KEEP);
  assert.equal(list[0].page_id, "p0", "先頭（最初の失敗）が残る");
  assert.equal(list[FAILURE_KEEP - 1].page_id, `p${FAILURE_KEEP - 1}`);
});
test("#53 L-17 appendFailure: 上限に達したら同じ配列をそのまま返す（再確保しない）", () => {
  const full = Array.from({ length: FAILURE_KEEP }, (_, i) => ({ page_id: `p${i}` }));
  assert.equal(appendFailure(full, { page_id: "x" }), full);
});
test("#53 L-17 truncatedFailureNotice: 溢れた件数だけを注記する", () => {
  assert.equal(truncatedFailureNotice(400, 400), null, "全件出ていれば注記しない");
  assert.equal(truncatedFailureNotice(0, 0), null);
  const t = truncatedFailureNotice(1200, 400);
  assert.ok(t.includes("他 800 件"), t);
  assert.ok(t.includes("400 件まで"), t);
});

// ================================================================ issue #52 M-12 / Q-MJ
// startDisabledReason: 同期フォルダ判定の誤検知に逃げ道（明示チェック）を作る
test("#52 M-12 startDisabledReason: 同期フォルダは既定で開始不可・理由に確認チェックを案内", () => {
  const t = startDisabledReason("C:\\demo", { ...VERIFY_BASE, storage: false });
  assert.ok(t.includes("確認チェック"), t);
});
test("#52 M-12 startDisabledReason: 明示チェックを入れると開始できる（他の理由は残る）", () => {
  assert.equal(startDisabledReason("C:\\demo", { ...VERIFY_BASE, storage: false }, true), null);
  // 逃げ道は保存先の話だけ。認証キー未設定はチェックしても解除されない
  assert.equal(
    startDisabledReason("C:\\demo", { ...VERIFY_BASE, storage: false, cred: "missing" }, true),
    "認証キーが未設定です（下の「認証キーを選択」から設定してください）");
});

// ================================================================ issue #67 追補
// 選択枠の色を決めるときの「背景」を、実際に draw() が塗る色に合わせる
test("#67-5 CANVAS_BG_COLOR: 定数は draw() が塗る色そのもの（CSS の .canvas 背景ではない）", () => {
  assert.equal(CANVAS_BG_COLOR, "#1c1f26");
  const src = fs.readFileSync(path.join(srcDir, "Editor.tsx"), "utf8");
  // 下地の塗りは定数を参照する（同じ色を2か所に書かない）
  assert.ok(src.includes("ctx.fillStyle = CANVAS_BG_COLOR; ctx.fillRect(0, 0, width, height);"),
    "draw() の下地が CANVAS_BG_COLOR を参照していない");
  // 画面に出ない CSS 背景（#e7ebf1）を色値として持たない（説明の文中に
  // 出てくるのは可。判定に使う値として書かれていないことを見る）
  assert.ok(!src.includes('"#e7ebf1"'), "#e7ebf1 が色値として Editor.tsx に残っている");
});
test("#67-5 SELECTION_COLOR: 白い紙面で 4.69:1・実際の下地 #1c1f26 で 3.51:1", () => {
  const paper = contrastRatio(SELECTION_COLOR, PAPER_BG_COLOR);
  const canvas = contrastRatio(SELECTION_COLOR, CANVAS_BG_COLOR);
  assert.ok(Math.abs(paper - 4.69) < 0.01, `白: ${paper}`);
  assert.ok(Math.abs(canvas - 3.51) < 0.01, `下地: ${canvas}`);
});

// ================================================================ issue #87 追補
// 確認モーダル: 「進むと何かを失う」側は主ボタンにしない
test("#87-1 uiConfirmSpec: 破棄は主ボタンにしない・採用は主ボタンのまま", () => {
  assert.equal(uiConfirmSpec("discard-changes").confirmVariant, "plain");
  assert.equal(uiConfirmSpec("adopt-overlapping-candidate").confirmVariant, "primary");
});
test("#87-1 uiConfirmSpec: 破棄の本文は結果（元に戻せない）を問いより先に置く", () => {
  const b = uiConfirmSpec("discard-changes").body;
  assert.ok(b.indexOf("元に戻せません") < b.indexOf("破棄して続けますか"), b);
});

// ================================================================ issue #65-7 追補
// 保存成功は灰色1行ではなく、注意帯と対の成功帯に出す
test("#65-7 saveOkBanner: 保存成功の msg は見出し（保存先まで）と詳細に分かれる", () => {
  const msg = "保存＋コア検証 OK（欄 14 → 20列＝欄14＋管理6・除外 2）: C:\t\a.json"
    + " ／ 読み込み時から: 欄 14→14 ／ 並べ替えを反映しました";
  const b = saveOkBanner(msg);
  assert.ok(b);
  assert.equal(b.head, "保存＋コア検証 OK（欄 14 → 20列＝欄14＋管理6・除外 2）: C:\t\a.json");
  assert.equal(b.detail, "読み込み時から: 欄 14→14 ／ 並べ替えを反映しました");
});
test("#65-7 saveOkBanner: 利用者テンプレート保存・切り抜き注記つきも成功帯へ", () => {
  assert.ok(saveOkBanner("利用者テンプレートとして保存しました: 見本"));
  const carve = saveOkBanner("重なった欄を自動で切り抜きました: A。保存＋コア検証 OK: C:\t\a.json");
  assert.ok(carve);
  assert.ok(carve.head.startsWith("重なった欄を"), carve.head);
  assert.equal(carve.detail, "");
});
test("#65-7 saveOkBanner: 進行中・失敗・空は帯を出さない（灰色1行のまま）", () => {
  assert.equal(saveOkBanner(""), null);
  assert.equal(saveOkBanner("画像を確認しています…"), null);
  assert.equal(saveOkBanner("保存していません: コアの検証で問題が見つかりました: x"), null);
  assert.equal(saveOkBanner("結合を中止しました（欄をクリックしてください）"), null);
});

// ================================================================ AC-F11
// 判定不能（undecidable）の面は枠を消さずに弱めて描く。色だけに頼らない
// ため破線を併用し、参照先（fallback）の破線とは別パターンにする
test("AC-F11 undecidableFaces: verdict=undecidable の面だけを集める（mismatch は対象外）", () => {
  const faces = [
    { face_id: "front", verdict: "undecidable" },
    { face_id: "back", verdict: "mismatch" },
  ];
  const u = undecidableFaces(faces);
  assert.equal(u.size, 1);
  assert.ok(u.has("front"));
  assert.ok(!u.has("back"));
});
test("AC-F11 undecidableFaces: faces 未提供（旧コア）は空集合", () => {
  assert.equal(undecidableFaces(undefined).size, 0);
});

test("AC-F11 frameStyleFor: 一致・判定なしは従来どおり（不透明・実線）", () => {
  for (const verdict of [undefined, "match", "mismatch", "skipped"]) {
    const s = frameStyleFor({ verdict });
    assert.equal(s.alpha, 1, String(verdict));
    assert.deepEqual(s.dash, [], String(verdict));
  }
});
test("AC-F11 frameStyleFor: 判定不能は薄く（alpha<1）・破線（色以外の手掛かり）", () => {
  const s = frameStyleFor({ verdict: "undecidable" });
  assert.equal(s.alpha, UNDECIDABLE_ALPHA);
  assert.ok(s.alpha < 1 && s.alpha > 0, String(s.alpha));
  assert.deepEqual(s.dash, UNDECIDABLE_DASH);
  assert.ok(s.dash.length > 0);
});
test("AC-F11 frameStyleFor: 判定不能の破線は参照先の破線と別パターン（意味の衝突を避ける）", () => {
  assert.notDeepEqual(UNDECIDABLE_DASH, FALLBACK_DASH);
});
test("AC-F11 frameStyleFor: 選択中は判定不能でも選択の見た目を優先（薄くも破線にもしない）", () => {
  const s = frameStyleFor({ verdict: "undecidable", selected: true });
  assert.equal(s.alpha, 1);
  assert.deepEqual(s.dash, []);
});
test("AC-F11 frameStyleFor: 参照先の枠は判定不能でも参照先の破線を保ち、弱さは alpha で表す", () => {
  const s = frameStyleFor({ verdict: "undecidable", fallback: true });
  assert.equal(s.alpha, UNDECIDABLE_ALPHA);
  assert.deepEqual(s.dash, FALLBACK_DASH);
  // 一致の紙でも参照先の破線は従来どおり
  const ok = frameStyleFor({ verdict: "match", fallback: true });
  assert.equal(ok.alpha, 1);
  assert.deepEqual(ok.dash, FALLBACK_DASH);
});

// ------------------------------------------------------- issue #75 (f)・FR-F41
// 枠の自動合わせ（吸着）でテンプレートの位置のまま読んだページ数。原因の違う
// 2つの数字を1つに足さないこと・0件では出さないこと・run のサマリから
// noticeFor 経由で「実行時のお知らせ」へ届くことを固定する。
test("AC-F40 snapNotice: 見送ったページ数が件数付きで出る（入力の紙由来と分かる）", () => {
  const t = snapNotice({ snap_failsafe_pages: 3, snap_excluded_pages: 0 });
  assert.match(t, /3 件/);
  assert.match(t, /読み取るたびに変わります/);
  // 許容幅の内側の誤りを機械で見つける手段は無い（07 §9.3）。
  // 「検知」と書けばそれ自体が嘘になる
  assert.ok(!/検知/.test(t), t);
});
test("AC-F40 snapNotice: 対象外のページ数はテンプレート由来として別に出る", () => {
  const t = snapNotice({ snap_failsafe_pages: 0, snap_excluded_pages: 12 });
  assert.match(t, /12 件/);
  assert.match(t, /毎回同じ件数/);
  assert.ok(!/読み取るたびに変わります/.test(t), t);
});
test("AC-F40 snapNotice: 2つの数字を1つに足さない（両方非0なら両方出る）", () => {
  const t = snapNotice({ snap_failsafe_pages: 3, snap_excluded_pages: 12 });
  assert.match(t, /3 件/);
  assert.match(t, /12 件/);
  assert.ok(!/15/.test(t), "合計を出している（直す先が違うので混ぜない）");
});
test("AC-F40 snapNotice: 両方0・旧コア（キー欠落）では出さない", () => {
  assert.equal(snapNotice({ snap_failsafe_pages: 0, snap_excluded_pages: 0 }), null);
  assert.equal(snapNotice({}), null);
});
test("AC-F40 noticeFor: run のサマリから実行時のお知らせへ配線されている", () => {
  const ev = { event: "summary", snap_failsafe_pages: 2, snap_excluded_pages: 0 };
  assert.equal(noticeFor(ev), snapNotice(ev));
  // 吸着が動かない既定運用（両方0）では、サマリで通知自体を出さない
  assert.equal(noticeFor({ event: "summary", snap_failsafe_pages: 0,
                           snap_excluded_pages: 0 }), null);
});

// ---------------------------------------------- 初回読み込みフロー（2026-09-04）
// テンプレート編集で帳票を開いたときの分岐（前回のテンプレートを自動適用する／
// この紙の枠候補を自動で作る）。AC-F67〜AC-F75。

test("AC-F67 autoDetectEnabled: 明示 false のときだけ OFF（欠落・非 bool は ON）", () => {
  assert.equal(autoDetectEnabled({}), true);
  assert.equal(autoDetectEnabled({ auto_detect_frames_on_open: true }), true);
  assert.equal(autoDetectEnabled({ auto_detect_frames_on_open: false }), false);
  // 非 bool は ON（従来動作＝OFF ではなく、既定の新動作へ倒す）
  assert.equal(autoDetectEnabled({ auto_detect_frames_on_open: "no" }), true);
  assert.equal(autoDetectEnabled({ auto_detect_frames_on_open: 0 }), true);
  assert.equal(autoDetectEnabled(null), true);
  assert.equal(autoDetectEnabled(undefined), true);
});

test("AC-F68 initialFrameView: OFF・ファイル起点・記憶ありは template", () => {
  const v = (autoDetect, appliedMemory, hasOpenedTemplateFile) =>
    initialFrameView({ autoDetect, appliedMemory, hasOpenedTemplateFile });
  assert.equal(v(false, "user:帳票B", false), "template");   // OFF×記憶あり
  assert.equal(v(false, "", false), "template");             // OFF×記憶なし
  assert.equal(v(true, "", false), "candidates");            // ON×記憶なし
  assert.equal(v(true, "shipped", false), "template");       // ON×出荷の記憶
  assert.equal(v(true, "user:帳票B", false), "template");    // ON×利用者の記憶
  // 「テンプレートを開く」で人が開いたファイルは、記憶で黙って差し替えない
  assert.equal(v(true, "", true), "template");
  assert.equal(v(true, "user:帳票B", true), "template");
});

test("AC-F68 shouldAutoApplyMemory: OFF・ファイル起点では記憶を復元しない", () => {
  // template を返す理由は3つあり、記憶があるとき以外は「何もしない」。
  // view だけを見て適用すると、OFF にしたのに前回のテンプレートが載る
  assert.equal(shouldAutoApplyMemory({ autoDetect: true, hasOpenedTemplateFile: false }), true);
  assert.equal(shouldAutoApplyMemory({ autoDetect: false, hasOpenedTemplateFile: false }), false);
  assert.equal(shouldAutoApplyMemory({ autoDetect: true, hasOpenedTemplateFile: true }), false);
  assert.equal(shouldAutoApplyMemory({ autoDetect: false, hasOpenedTemplateFile: true }), false);
});

test("AC-F69 autoApplyTarget: 3形式だけを受け付け、それ以外は記憶なし", () => {
  assert.equal(autoApplyTarget(""), null);
  assert.deepEqual(autoApplyTarget("shipped"), { kind: "shipped" });
  assert.deepEqual(autoApplyTarget("user:帳票B"), { kind: "user", name: "帳票B" });
  assert.equal(autoApplyTarget("user:"), null);          // 名前が空
  // 絶対パスは GUI が持たない（07 §7.3）
  assert.equal(autoApplyTarget("C:" + String.fromCharCode(92) + "x.json"), null);
  assert.equal(autoApplyTarget("shipped:formB"), null);  // 旧案の形式
  assert.equal(autoApplyTarget("sample"), null);
});

test("AC-F75 appliedTemplateMemory / applyTemplateMemoryValue: 非文字列は記憶なし・往復する", () => {
  for (const v of [123, null, undefined, {}, [], true]) {
    assert.equal(appliedTemplateMemory({ last_applied_template: v }), "", String(v));
  }
  assert.equal(appliedTemplateMemory({}), "");
  assert.equal(appliedTemplateMemory(null), "");
  assert.equal(appliedTemplateMemory({ last_applied_template: "user:帳票B" }), "user:帳票B");
  // 往復（記憶へ書いた値を読み直すと同じ target になる）
  for (const t of [{ kind: "shipped" }, { kind: "user", name: "帳票B" }]) {
    assert.deepEqual(autoApplyTarget(applyTemplateMemoryValue(t)), t);
  }
});

test("AC-F70 detectFramesEffects: 自動生成は赤帯を消さず・未保存にせず・履歴も積まない", () => {
  const m = detectFramesEffects(true);
  assert.deepEqual(m, { clearErrMsg: true, markDirty: true, pushHistory: true,
                        errorTo: "errMsg" });
  const a = detectFramesEffects(false);
  assert.equal(a.clearErrMsg, false);
  assert.equal(a.markDirty, false);
  assert.equal(a.pushHistory, false);
  assert.equal(a.errorTo, "framesMsg");   // 画像側の赤帯を奪わない（M-8）
});

test("AC-F71 autoDetectFailureNotice: 画像は出ていると伝え、やり直しの導線を示す", () => {
  const t = autoDetectFailureNotice(new Error("core が落ちました"));
  assert.match(t, /画像は表示しています/);
  assert.match(t, /ページ全体から枠候補を生成/);
  assert.match(t, /core が落ちました/);
  // 画像側の失敗と読める語を混ぜない（M-8: 画像表示を妨げない）
  assert.ok(!/画像を開けませんでした/.test(t), t);
  assert.ok(!/画像を読み込めませんでした/.test(t), t);
});

test("AC-F72 candidateOverlapFlag: --template を渡していない core の判定は採らない", () => {
  // 発端: Rust の inject_default_template が detect-frames にも
  // config.last_template を注入し、GUI が渡していないのに core が「画面に
  // 無いテンプレート」基準の overlaps_existing を返していた（実窓実測・
  // 2026-09-04）。注入は lib.rs 側で止めた（H-3）が、core へ何が渡ったかを
  // GUI が推測せずに済む形として、この二重の安全網は残す
  assert.equal(candidateOverlapFlag(true, false, false), false);   // core だけ true → 採らない
  assert.equal(candidateOverlapFlag(true, false, true), true);     // GUI が渡した判定は採る
  assert.equal(candidateOverlapFlag(false, true, false), true);    // GUI 自身の再判定は常に採る
  assert.equal(candidateOverlapFlag(false, true, true), true);
  assert.equal(candidateOverlapFlag(false, false, false), false);
});

test("AC-F72 applyCandidates: 空テンプレートなら重なりは1件も出ず全部採用できる", () => {
  // 実コアは --template 未指定時 overlaps_existing を返さない（＝false）。
  // 空テンプレの上では GUI 側の再判定（candidateOverlapsExisting）も必ず false
  const H = 3510;
  const cands = candidatesFromDetectFrames({
    candidates: [
      { kind: "table", rect: { x: 100, y: 1000, w: 750, h: 400 },
        blocks: [{ x: 100, y: 1000, rows: 5 }], row_pitch: 80, row_height: 70,
        columns: [{ x_offset: 0, width: 200 }, { x_offset: 200, width: 550 }] },
      { kind: "field", rect: { x: 450, y: 320, w: 300, h: 60 } },
      { kind: "field", rect: { x: 600, y: 100, w: 300, h: 80 } },
      { kind: "field", rect: { x: 100, y: 1950, w: 850, h: 150 } },
    ],
  }).map((c) => ({ ...c, overlaps: c.overlaps || candidateOverlapsExisting(c, [], [], H) }));
  assert.equal(cands.length, 4);
  assert.deepEqual(cands.map((c) => c.overlaps), [false, false, false, false]);

  const selected = {};
  for (const c of cands) selected[c.id] = candidateDefaultChecked(c);
  assert.deepEqual(Object.values(selected), [true, true, true, true]);
  let n = 0;
  const r = applyCandidates([], [], cands, selected, () => "u" + (n++));
  assert.equal(r.acceptedCount, 4);
  assert.equal(r.cands.length, 0);
  assert.equal(r.fields.length, 3);
  assert.equal(r.tables.length, 1);
});

test("AC-F73 buildTemplateJson: 候補だけから作った欄に出荷由来の field_id は混ざらない", () => {
  const H = 3510;
  const cands = candidatesFromDetectFrames({
    candidates: [
      { kind: "field", rect: { x: 450, y: 320, w: 300, h: 60 } },
      { kind: "field", rect: { x: 600, y: 100, w: 300, h: 80 } },
    ],
  }).map((c) => ({ ...c, overlaps: c.overlaps || candidateOverlapsExisting(c, [], [], H) }));
  const selected = {};
  for (const c of cands) selected[c.id] = true;
  let n = 0;
  const r = applyCandidates([], [], cands, selected, () => "u" + (n++));
  for (const f of r.fields) assert.match(f.field_id, /^field_[0-9][0-9]$/);
  const { template } = buildTemplateJson({
    fields: r.fields, tables: r.tables, excls: [], splitY: H, W: 2490, H,
    meta: { template_id: "new-template", render_dpi: 300,
            image: { width: 2490, height: H }, record: { pages: 1 } },
  });
  const text = JSON.stringify(template);
  // 出荷テンプレートの代表 id が紛れ込んでいない（空テンプレの上で作った証拠）
  assert.ok(!/person_/.test(text), text.slice(0, 200));
  assert.ok(!/family/.test(text), text.slice(0, 200));
});

test("AC-F74 formatBandApplies: 判定の根拠が別テンプレートなら帯を出さない", () => {
  const f = (view, appliedMemory, lastTemplate, hasOpenedTemplateFile) =>
    formatBandApplies({ view, appliedMemory, lastTemplate, hasOpenedTemplateFile });
  assert.equal(f("candidates", "user:帳票B", "user:帳票B", false), false);
  assert.equal(f("template", "user:帳票B", "user:帳票B", false), true);
  assert.equal(f("template", "user:帳票B", "shipped", false), false);  // 乖離（R-6）
  assert.equal(f("template", "", "shipped", true), true);   // --template を渡した判定
  assert.equal(f("candidates", "", "", true), false);       // 候補パスでは常に出さない
});

test("H-1 candidateResultApplies: 古い世代の生成結果は捨てる", () => {
  const now = { seq: 3, epoch: 7 };
  // 生成を始めたときの世代と一致していれば流してよい
  assert.equal(candidateResultApplies({ seq: 3, epoch: 7 }, now), true);
  // 別の紙へ移った（R-3・既存の穴）
  assert.equal(candidateResultApplies({ seq: 2, epoch: 7 }, now), false);
  // 同じ紙のままテンプレートを適用した（H-1・seq だけでは見抜けない穴）。
  // 「このテンプレートを使う」は seq を進めないため、epoch を見ないと
  // 適用中のテンプレートの上へ重なり0件の候補が復活する
  assert.equal(candidateResultApplies({ seq: 3, epoch: 6 }, now), false);
  // 両方進んでいる（紙を替えたうえでテンプレートも適用した）
  assert.equal(candidateResultApplies({ seq: 2, epoch: 6 }, now), false);
  // 進むのは片方向だけ（世代番号は減らない）が、判定は単純な一致で足りる
  assert.equal(candidateResultApplies({ seq: 4, epoch: 8 }, now), false);
});

test("初回読み込みフローの文言: 由来・状態・次の一手が読み取れる", () => {
  const auto = appliedTemplateBarText("帳票B", true, 12, 1);
  assert.match(auto, /適用中のテンプレート: 帳票B/);
  assert.match(auto, /前回と同じものを自動で適用しました/);
  assert.match(auto, /欄 12・表 1/);
  // 選び直した後は「自動」の括弧書きを消す（手動扱いへ切り替わる）
  const manual = appliedTemplateBarText("帳票B", false, 12, 1);
  assert.ok(!/自動で適用/.test(manual), manual);

  assert.match(unappliedTemplateBarText(), /適用していません/);
  assert.match(unappliedTemplateBarText(), /枠候補/);
  assert.match(templateChoiceNotice(), /まだ適用していません/);
  assert.match(staleAppliedMemoryNotice("帳票B"), /帳票B/);
  assert.match(staleAppliedMemoryNotice("帳票B"), /見つかりませんでした/);

  // 同名ボタンが並ぶので accessible name にテンプレート名を含める。
  // 可視ラベルをそのまま先頭に含む形にする（WCAG 2.5.3 Label in Name）
  const n = useTemplateButtonName("帳票B", "user");
  assert.ok(n.startsWith("このテンプレートを使う"), n);
  assert.match(n, /帳票B/);
  assert.match(useTemplateButtonName("chouhyo-v1", "shipped"), /出荷/);
});

// ラミィ a11y レビュー Should-1（2026-09-04・WCAG 4.1.3）。テンプレ適用と
// 候補での作り直しは適用中バー／未適用バー（role="status"）を必ず同時に
// 更新するため、.msg 側は空にして読み上げを1本へ集約する。実測では
// .msg・寸法不一致の黄帯・バーの3領域が同時に非空だった。
test("Should-1 templateDecisionMsg: バーが出る操作では .msg を空にする", () => {
  // 適用: 内容は appliedTemplateBarText が全部言うので .msg は空
  assert.equal(templateDecisionMsg(true, "テンプレート読込: 帳票B（欄 1・表 1）"), "");
  // 候補で作り直し: 次の一手は unappliedTemplateBarText が言う
  assert.equal(templateDecisionMsg(true, newTemplateNotice(true)), "");
  assert.equal(templateDecisionMsg(true, newTemplateNotice(false)), "");
  // バーは画像がある間だけ描かれる。画像未読込（利用者テンプレート一覧から
  // 開いた場合など）は据え置き——消すと操作の結果がどこにも出ない
  assert.equal(templateDecisionMsg(false, "テンプレート読込: 帳票B（欄 1・表 1）"),
    "テンプレート読込: 帳票B（欄 1・表 1）");
  assert.equal(templateDecisionMsg(false, newTemplateNotice(false)),
    newTemplateNotice(false));
});

// scripts/run_all_tests.py の集計器が読む形式（"N passed ... in <秒>"）で
// 出す。これが無いと「実行された試験が0件」と判定されて FAIL になる
const secs = ((Date.now() - t0) / 1000).toFixed(2);
console.log(`\n${passed} passed, ${failed} failed in ${secs}s`);
console.log(failed === 0 ? "すべて成功" : `失敗 ${failed} 件`);
process.exit(failed === 0 ? 0 : 1);
