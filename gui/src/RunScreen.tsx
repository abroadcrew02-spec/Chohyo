// 実行画面（設計 §7.1・最小構成の6機能）。処理ロジックを持たず、
// コアの起動と JSON Lines 進捗（§7.3）の表示に徹する。
// UI はデザインカンバス「帳票OCRツール GUI」準拠: 番号つき手順・平易な言葉。
import { invoke, isTauri } from "./bridge";
import { listen } from "./bridge";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow, currentMonitor } from "@tauri-apps/api/window";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

type Summary = {
  pages: number; rows: number; align_failed: number;
  api_calls: number; unclear_cells: number; overflow: number;
  // 様式不一致で失敗したページ数（枠D で pipeline.py が追加したキー。
  // 旧コアでは undefined・issue N-1）
  format_mismatch?: number;
  // 様式不一致のうち「送信前に止まった」件数（issue #71 (a')・FR-F01由来の
  // 理由コードで確定したもの・FR-F10）。format_mismatch はマッピング後の
  // 構造異常や枠外率など送信後の判定も含む総数のため、この値はその内数
  // （旧コアでは undefined）
  format_mismatch_pre_send?: number;
  risky_cells?: number;  // CSV を Excel で直接開くと数式化しうるセル数（D-28）
  // 中間データの整列結果を再利用し送信しなかったページ数（issue #72 (t)・
  // 実機通し確認の指摘。core が summary へ追加中のキー・旧コアでは
  // undefined）。api_calls が処理枚数より少ない理由をここで説明する
  reused_pages?: number;
  // 枠の自動合わせ（吸着）でテンプレートの位置のまま読んだページ数
  // （issue #75 (f)・FR-F41）。2つは原因が違うので別のキーで届く——
  // failsafe は入力した紙の状態由来（毎回変わる）、excluded はテンプレート
  // 定義由来（毎回同じ）。既定 OFF の運用では常に 0（旧コアでは undefined）
  snap_failsafe_pages?: number;
  snap_excluded_pages?: number;
  xlsx?: string; csv?: string;
};
type Verify = { template: boolean; poppler: boolean; cred: string; storage: boolean;
                budgetUsed: number; budgetCap: number;
                // 出力対象外の欄数（issue #66 段4・FR-1.9）。旧コア（フィールド
                // 欠落）との互換のため undefined を許容する
                outputDisabledCells?: number;
                // 認証キーが環境変数（平文）で使われているか（issue S-MB。Wave 2 で
                // core 側の verify イベントに追加される想定のキー。旧コアでは
                // undefined のまま——credNotice は cred === "env" 判定だけでも動く）
                envPresent?: boolean;
                // event:"verify" 行を1つも見なかった（＝検証自体が実行できなかった）
                // ことを区別するフラグ（issue Q-ME）。false のとき budgetCap 900 等の
                // 既定値を「現状」として画面に出してはいけない
                parsed: boolean;
                // parsed=false のときだけ設定する、再試行導線に添える生エラーの先頭行
                rawFirstLine?: string };
// reason_code は issue #71 (a') で追加された、様式不一致・位置合わせ失敗の
// 内訳（旧コアでは undefined・page 進捗イベントに乗る）
type Failure = { page_id: string; status: string; reason_code?: string };

// ウィンドウサイズ運用（起動時は小窓・完了サマリ表示で縦に自動拡大）。
// 幅は常に730固定——tauri.conf.json の windows[0].width と一致させる。
export const RUN_WINDOW_WIDTH = 730;

// 実行前の最大状態（issue #72 (t) 実機再計測・2026-09-03・実機 WebView2 の
// CDP 接続で直接計測: appbar 65px + run-screen 697px = 762px。テンプレート
// 選択カード追加後の値——フォルダ未選択・テンプレート一覧取得済みの状態が
// 最も高い）に、フォント描画差・将来の軽微な文言追加を吸収する安全マージン
// 18pxを足した値。tauri.conf.json の windows[0].height と同じ値を保つこと
// （既定サイズの正本は tauri.conf.json 側。ここでは targetWindowHeight の
// 下限クランプとして参照する——このおかげで、この値より本文が短ければ
// 常にこのサイズへ、長ければ実測に応じて拡大する。620=旧値。実測の詳細は
// 完了報告の実測表を参照）。
export const RUN_WINDOW_HEIGHT_DEFAULT = 780;

// アプリバー（App.tsx の .appbar）の高さ。実行画面の外側にあるため、
// document.querySelector で動的に測る（App.tsx とファイルをまたぐが、
// 単一ウィンドウ構成でこの2つは常に同時にマウントされているため参照可能。
// 取得に失敗した場合だけの保険としてPlaywright実測値を fallback に使う）。
const APPBAR_HEIGHT_FALLBACK = 65;

// currentMonitor() が取得できない場合の安全側の固定上限。値は本機能の
// 実装前まで実際にこのウィンドウの既定高さとして使われていた 1150px
// （旧 tauri.conf.json の height）をそのまま流用する。
const FALLBACK_MAX_WINDOW_HEIGHT = 1150;

/** 完了サマリ表示時にウィンドウを縦へ拡大する高さを決める純関数。
 *  contentHeight: .run-screen の実測 scrollHeight（本文の実高）。
 *  chromeHeight: 本文の外側にあるアプリバー等の高さ。
 *  workAreaHeight: 現在のモニタの作業領域の論理高さ（取得できない/不正な
 *    値なら安全側の固定上限 FALLBACK_MAX_WINDOW_HEIGHT を上限に使う）。
 *  返り値は [RUN_WINDOW_HEIGHT_DEFAULT, 上限] にクランプした整数。
 *  上限でクランプされ本文がそれより高い場合、はみ出した分は setSize では
 *  拡げられない——.run-screen 側の overflow:auto によるスクロールに委ねる
 *  （画面より大きい物理ウィンドウは作れないため、意図してスクロールを
 *  許容する唯一のケース）。 */
export function targetWindowHeight(
  contentHeight: number, chromeHeight: number, workAreaHeight: number,
): number {
  const content = Number.isFinite(contentHeight) && contentHeight > 0 ? contentHeight : 0;
  const chrome = Number.isFinite(chromeHeight) && chromeHeight > 0 ? chromeHeight : 0;
  const needed = Math.ceil(content + chrome);
  const max = Number.isFinite(workAreaHeight) && workAreaHeight > RUN_WINDOW_HEIGHT_DEFAULT
    ? Math.floor(workAreaHeight)
    : FALLBACK_MAX_WINDOW_HEIGHT;
  return Math.min(max, Math.max(RUN_WINDOW_HEIGHT_DEFAULT, needed));
}

// ステータス → 平易な言葉（エラー一覧用）
export const STATUS_JA: Record<string, string> = {
  "位置合わせ失敗": "位置合わせに失敗しました（行全体が〓です）",
  "様式不一致": "帳票の様式が一致しませんでした（行全体が〓です）",
  "展開失敗": "ファイルを開けませんでした",
  "送信失敗": "送信に失敗しました（通信環境を確認してください）",
  "未処理（送信上限到達）": "送信上限に達したため未処理です（次回実行時に処理されます）",
  "未処理（中断）": "中断のため未処理です（次回実行時に処理されます）",
  // core/chouhyo_ocr/render_rows.py の STATUS_DUPLICATE に対応（issue #52 M-3）。
  // 未対応だと生の「スキップ（重複）」がそのまま出て、行が〓な理由が伝わらない
  "スキップ（重複）": "同じ内容のファイルが既にあるため送信しませんでした（行全体が〓です）",
  // core/chouhyo_ocr/render_rows.py の STATUS_RENDER_FAILED に対応（issue #80）。
  // 送信済みのデータから Excel の行を組み立てる所で失敗した状態で、様式の問題
  // ではない。理由コード（row_build_failed / row_build_bug）で原因が分かれる
  "出力失敗": "この行を組み立てられませんでした（行全体が〓です）",
  "超過あり": "記入が定義済みの行数を超えています",
};

/** reason_code（issue #71 (a')・page 進捗イベント）→ 平易な言葉（設計08
 *  §2.8）。frame_* の様式判定由来は「送信前」、mapping/枠外率由来は
 *  「送信後」、罫線が読み取れず判定不能だったものは別の言葉にする——同じ
 *  STATUS_JA（位置合わせ失敗／様式不一致）の中でも原因が違うことを伝える。 */
// issue #71 (a')・スバル差し戻し1: 08 §2.4.3 の10コードに完全一致させる。
// `frame_edge`（edge_mismatch 由来）は07 v1.2／08 ★1 で判定不能へ倒された
// ため「様式不一致」ではなく「位置合わせ失敗」グループに入る——上端が1本
// かすれただけの本物の紙に「様式が違う」と案内しないため（07 §9.1）。
// `frame_check_failed`（AC-F14・判定関数の例外）はどちらのグループとも別の
// 専用文言にする——コード欠陥の可能性を隠さない。
export const REASON_CODE_JA: Record<string, string> = {
  // 様式不一致（送信前）
  frame_size: "様式が違うため送信前に止めました",
  frame_lines: "様式が違うため送信前に止めました",
  frame_ambiguous: "様式が違うため送信前に止めました",
  // 様式不一致（送信後）
  map_failed: "送信後に様式不一致と判定しました",
  outside_ratio: "送信後に様式不一致と判定しました",
  // 出力失敗（issue #80・render 段）。送信済みのデータから行を組み立てる所で
  // 落ちている＝様式の問題ではないので、上の「様式不一致」グループから外した。
  // データ起因（row_build_failed）とコード欠陥の疑い（row_build_bug）を分ける
  // ——後者を前者に混ぜると、利用者が直せない不具合をテンプレートの問題として
  // 探し続けることになる（frame_check_failed と同じ調子で書く）
  row_build_failed: "出力する行を組み立てられませんでした"
    + "（中間データが壊れている可能性があります）",
  row_build_bug: "行の組み立てでエラーが発生しました"
    + "（プログラムの不具合の可能性。ログを確認してください）",
  // 位置合わせ失敗（罫線が読み取れず判定不能・08 ★1〜★3）
  frame_few_lines: "罫線が読み取れず位置合わせできませんでした",
  frame_edge: "罫線が読み取れず位置合わせできませんでした",
  frame_boundary: "罫線が読み取れず位置合わせできませんでした",
  // 位置合わせ失敗（判定関数自体の例外・AC-F14）
  frame_check_failed: "様式判定の処理でエラーが発生したため、"
    + "位置合わせ失敗として扱いました（コード欠陥の可能性。ログを確認してください）",
};

/** reason_code が対応表に無い（旧コア・未知コード）場合は null——呼び出し側は
 *  STATUS_JA だけの表示に留める（存在しない説明を捏造しない）。 */
export function reasonCodeNotice(reasonCode: string | undefined): string | null {
  if (!reasonCode) return null;
  return REASON_CODE_JA[reasonCode] ?? null;
}

/** run/remap の完了サマリに乗るカウンタ（issue #65-3・S2）を「実行時のお知らせ」
 *  1行にまとめる。対象: fallback_used（参照先の文字を採用した数）・
 *  fallback_discarded（参照先の文字を破棄した数）・carve_hole（切り抜きの穴に
 *  落ちた文字数）・conflict_excluded_field（主と参照先の食い違いのうち対象外欄
 *  由来の数）。対象外欄（出力しない欄）由来の内訳（issue #66 段2・FR-1.4）が
 *  非0なら括弧書きで添える。carve_hole は U-07 でその欄が丸ごと〓になる
 *  （mapping.py:463-466）ため、件数だけでなく〓化した事実も一言添える
 *  （noticeFor 全体の趣旨＝〓の出所を画面から辿れるようにする、に合わせる）。
 *
 *  conflict は n_fb>=2（参照先候補が複数）のときだけ立ち、常に fallback_discarded
 *  にも二重に計上される（総数カウンタを持たず対象外欄由来の内訳しか無い）。
 *  件数が別に増えるわけではなく「主と参照先が食い違った」という別の事実の
 *  可視化なので、破棄の内訳に混ぜず独立した句で足す（マリンレビュー S-3）。
 *
 *  4項目とも0なら null——0件表示はノイズになるので出さない。
 *
 *  summary（run）・remap_summary の両方から呼ぶ前提の関数（noticeFor 側で配線）。
 *  run にしか配線しないと remap 経由の出力だけ通知が欠ける
 *  （test_output_columns_stage2.py が指摘した「片配線」の再発防止・issue #66）。 */
export function counterNotice(ev: Record<string, any>): string | null {
  const used = ev.fallback_used ?? 0;
  const discarded = ev.fallback_discarded ?? 0;
  const discardedExcl = ev.fallback_discarded_excluded_field ?? 0;
  const hole = ev.carve_hole ?? 0;
  const holeExcl = ev.carve_hole_excluded_field ?? 0;
  const conflictExcl = ev.conflict_excluded_field ?? 0;
  const segments: string[] = [];
  if (used > 0 || discarded > 0) {
    const parts: string[] = [];
    if (used > 0) parts.push(`採用 ${used}件`);
    if (discarded > 0) {
      parts.push(`破棄 ${discarded}件`
        + (discardedExcl > 0 ? `（うち出力しない欄由来 ${discardedExcl}件）` : ""));
    }
    // N-7: 「参照先から採用/破棄」だと破棄されたのが参照先の文字だと
    // 読み取りにくいため、主語を明示する形にする
    segments.push(`参照先の文字: ${parts.join("／")}`);
  }
  if (hole > 0) {
    let t = `切り抜きの穴に落ちた文字 ${hole}件（その欄は〓になっています`;
    if (holeExcl > 0) t += `・うち出力しない欄由来 ${holeExcl}件`;
    t += "）";
    segments.push(t);
  }
  if (conflictExcl > 0) {
    segments.push(`主と参照先の食い違い ${conflictExcl}件（出力しない欄）`);
  }
  return segments.length ? segments.join("・") : null;
}

/** 枠の自動合わせ（吸着）でテンプレートの位置のまま読んだページ数を
 *  「実行時のお知らせ」へ出す（issue #75 (f)・FR-F41・AC-F40）。
 *
 *  **2つの数字を1つに足さない。** 直す先が違う——
 *  `snap_failsafe_pages` は入力した紙の状態（罫線のかすれ・吸着後に欄が
 *  重なる）で決まるので読み取るたびに変わり、原本の状態を見る話になる。
 *  `snap_excluded_pages` はテンプレートの定義（行数の少ない表）で決まるので
 *  同じテンプレートを使うかぎり毎回同じ件数になり、テンプレートを直す話に
 *  なる。合計だけ出すと、利用者はどちらを見ればよいか分からない。
 *
 *  書けるのは「合わせた／見送った／対象外だった」の事実と件数だけで、
 *  **合わせた結果が正しいかどうかには触れない**（07 §9.3）。許容幅の内側の
 *  誤りを機械が見つける手段は無く、「検知した」と書くと嘘になる。
 *
 *  どちらも0（吸着 OFF の既定運用）・旧コアで undefined なら null——
 *  0件表示はノイズになるので出さない（counterNotice と同じ流儀）。 */
export function snapNotice(ev: Record<string, any>): string | null {
  const failsafe = ev.snap_failsafe_pages ?? 0;
  const excluded = ev.snap_excluded_pages ?? 0;
  const parts: string[] = [];
  if (failsafe > 0) {
    parts.push(`枠の自動合わせを見送ったページが ${failsafe} 件あります。`
      + `罫線がかすれているなどで合わせ先を確かめられなかったため、`
      + `その面はテンプレートの位置で読みました（読み取り自体は終わっています）。`
      + `入力した紙の状態で決まるので、件数は読み取るたびに変わります。`);
  }
  if (excluded > 0) {
    parts.push(`枠の自動合わせの対象外だったページが ${excluded} 件あります。`
      + `表の行数が少なく合わせ先として信用できないため、`
      + `テンプレートの位置で読みました。テンプレート側の性質なので、`
      + `同じテンプレートを使うかぎり毎回同じ件数になります。`);
  }
  return parts.length ? parts.join(" ") : null;
}

/** run/remap の完了サマリに中間データの累積量が乗ったら purge を促す1行
 *  （issue P-H1・レビュー7巡目 Wave 0・らでん逆張り採用分）。
 *
 *  total_done_pages・render_seconds は枠D（pipeline 側）が並行で追加中の
 *  新キーのため、この関数は両方を防御的に扱う——total_done_pages が無い
 *  （旧コア・追加未完了）なら null、render_seconds だけ無ければ秒数の
 *  括弧書きを省いて閾値超過の事実だけ伝える。
 *
 *  閾値は 1,000 頁（README §10.5 の実測: write_xlsx 7.1ms/行・5,000頁蓄積で
 *  末尾レンダー約35s、から「体感し始める手前」として設定）。 */
export function accumulationNotice(ev: Record<string, any>): string | null {
  const pages = ev.total_done_pages;
  if (typeof pages !== "number" || pages < 1000) return null;
  const seconds = ev.render_seconds;
  const suffix = typeof seconds === "number" ? `（出力の書き出しに ${seconds}秒）` : "";
  // 削除の導線がこの画面にできた（issue #52 M-11）。以前は CLI しか手段が
  // なく「コマンド（purge --yes）で」と書いていた（issue N-6）——画面に
  // ボタンがある以上、そのボタン名で案内するのが最短の出口になる
  return `中間データに ${pages} ページ蓄積しています${suffix}。`
    + `提出済みのバッチは下の「読み取ったデータを削除」から削除してください。`;
}

/** 中間データ削除（`purge`）の完了イベントを「実行時のお知らせ」1行にする
 *  （issue #52 M-11・S-MC）。
 *
 *  コア（`core/chouhyo_ocr/cli.py` の `cmd_purge`）が出す `event:"purged"` の
 *  実測キー: `removed`（削除できた件数）・`failed`（削除できなかった件数）・
 *  `cred_kept`（認証キーを残したか）と、`--include-output` を付けたときだけ
 *  増える `output_removed`／`output_kept`（この命名に一致せず残したファイル）
 *  ／`output_failed`。**パス（`path`・`output_dir`）は画面に出さない**——
 *  件数だけで消し損ねの判断はできるうえ、絶対パスを webview 側の表示へ
 *  持ち出さない既存方針（07 §7.3）に揃える。
 *
 *  「削除できなかった件数」を必ず出すのは、Excel で開いたままのファイルが
 *  あると黙って残るため——「削除しました」だけだと片付いたと誤解する。 */
export function purgeNotice(ev: Record<string, any>): string {
  const n = (v: unknown) => (typeof v === "number" && v > 0 ? v : 0);
  const parts: string[] = [];
  parts.push(`中間データを ${n(ev.removed)} 件削除しました`
    + (ev.cred_kept === true ? "（認証キーは残しています）" : "") + "。");
  if (n(ev.failed) > 0) {
    parts.push(`${n(ev.failed)} 件は削除できませんでした`
      + `（他のプログラムが使用中の可能性があります。閉じてからもう一度お試しください）。`);
  }
  if (ev.output_removed !== undefined || ev.output_failed !== undefined) {
    parts.push(`出力ファイルを ${n(ev.output_removed)} 件削除しました`
      + `（対象外として残したファイル ${n(ev.output_kept)} 件）。`);
    if (n(ev.output_failed) > 0) {
      parts.push(`出力ファイル ${n(ev.output_failed)} 件は削除できませんでした`
        + `（Excel などで開いている可能性があります）。`);
    }
  }
  return parts.join("");
}

/** 認証キー取り込み（`import-credentials --delete-source`）の stdout から
 *  トーストの文言を決める（issue #52 M-10）。
 *
 *  コアの実測イベント（`core/chouhyo_ocr/cli.py` の `cmd_import_credentials`）:
 *  削除成功で `credentials_source_deleted`、削除失敗で
 *  `credentials_source_kept`（`warn:true`）。どちらも出ない場合
 *  （`--delete-source` を解さない旧コア）は従来の言い回しへ落とす——
 *  実際には消えていないのに「削除しました」と言わないため、判定は
 *  「イベントを見たか」で行い、既定は消していない側に倒す。 */
export function importCredentialsNotice(stdout: string): string {
  let deleted = false;
  let kept = false;
  for (const line of stdout.split("\n")) {
    try {
      const e = JSON.parse(line);
      if (e.event === "credentials_source_deleted") deleted = true;
      if (e.event === "credentials_source_kept") kept = true;
    } catch { /* JSON 以外の行は無視 */ }
  }
  if (kept) {
    return "認証キーを暗号化して保存しました。元のファイルを削除できませんでした。"
      + "手で削除してください（鍵が平文のまま残っています）。";
  }
  if (deleted) {
    return "認証キーを暗号化して保存し、元のファイルを削除しました。";
  }
  return "認証キーを暗号化して保存しました。元のファイルは削除してください"
    + "（鍵が平文のまま残ります）。";
}

/** 完了バナーの色（issue #69 残置1）。
 *
 *  1件も送信せず（`api_calls === 0`）全ページが様式不一致で終わった実行でも、
 *  緑の「読み取りが完了しました」が最上部に出るため第一印象が成功に振れる。
 *  何が起きたかの説明は completionNotice が既に出しているので、ここで変える
 *  のは色（注意）だけ——文言を二重に持たない。
 *
 *  判定は「送信0 かつ 様式不一致>0」。`format_mismatch` が無い旧コアでは
 *  0 として扱い、従来どおり緑のままにする（欠落キーで色が動かない）。 */
export function completionBannerTone(summary: Summary | null): "ok" | "warn" {
  if (!summary) return "ok";
  return summary.api_calls === 0 && (summary.format_mismatch ?? 0) > 0 ? "warn" : "ok";
}

/** 実行終了時に赤帯へ出す文言（issue N-1）。exit 0 なら null。
 *
 *  完了サマリ（event:"summary"）を受け取っているかで意味が違う:
 *  受け取っていれば**コアは最後まで走り切って Excel も書いた**——「中断」
 *  ではないので「続きから処理します」も嘘になる。全ページが様式不一致
 *  （`rows === format_mismatch`・cli.py の exit 判定と同じ母集団）なら、
 *  同じ入力で再実行しても結果は変わらないため再実行を促さず、用紙サイズ・
 *  向きの確認へ誘導する。
 *
 *  format_mismatch は枠D で追加されたキー。旧コアでは undefined になるので、
 *  その場合は一致判定が成立せず一般の文言へ落ちる（防御的に扱う）。 */
export function completionNotice(summary: Summary | null, exitCode: number): string | null {
  if (exitCode === 0) return null;
  if (!summary) {
    // サマリ前に落ちた／中断された。処理済みページは残っており再開できる
    return `読み取りが中断されました（終了コード ${exitCode}）。`
      + `再度「読み取りを開始」を押すと続きから処理します。`;
  }
  const mismatch = summary.format_mismatch ?? 0;
  const preSend = summary.format_mismatch_pre_send ?? 0;
  // issue #71 (a'): 送信前に様式不一致で止まった件数が全ページと一致するなら、
  // 「用紙サイズ・向きの確認」より具体的な出口（テンプレートを選び直す／
  // この帳票のテンプレートを作る）へ誘導する。preSend===rows は
  // mismatch===rows を含意する（preSend は mismatch の内数）ため、この分岐を
  // 先に見る（設計08 §2.8）
  if (summary.rows > 0 && preSend === summary.rows) {
    return "様式が一致しませんでした。テンプレートを選び直すか、この帳票の"
      + "テンプレートを作成してください（再実行しても同じ結果になります）。";
  }
  if (summary.rows > 0 && mismatch === summary.rows) {
    return "すべてのページが様式不一致でした。用紙サイズ・向きがテンプレートと"
      + "合っているか確認してください（再実行しても同じ結果になります）。";
  }
  if (summary.rows === 0) {
    return `出力できる行がありませんでした（処理 ${summary.pages} ページ）。`
      + `入力のファイルと、テンプレートが対象の帳票のものかを確認してください。`;
  }
  return `読み取れたページがありませんでした（位置合わせ失敗 ${summary.align_failed} 件・`
    + `様式不一致 ${mismatch} 件）。原本の向き・スキャン品質と、テンプレートが`
    + `対象の帳票のものかを確認してください。`;
}

/** 進捗イベント → 「実行時のお知らせ」1件。該当しないイベントは null。
 *
 *  拾い漏らすと、〓だけの行や増減した行数の**出所が画面から辿れない**。
 *  利用者は「読み取り漏れ」と「送信を省いた」を区別できず、原本を探し直す。
 *  対応イベントはコア側の進捗出力（core/chouhyo_ocr/pipeline.py）と対で、
 *  skip_duplicate・template_changed_resend・remap_warning は issue #52 M-3 で追加。
 *  純関数にしてあるのは、この対応表だけを単体で検証できるようにするため。 */
export function noticeFor(ev: Record<string, any>): string | null {
  switch (ev.event) {
    // 対象外ファイル・古いページの警告（レビュー M-2・issue #28）。
    // ログだけだと「total=0 の正常終了」にしか見えない
    case "skipped_unsupported":
      return `読み取れない形式のファイルを ${ev.count} 件とばしました: `
        + (ev.files ?? []).join("、");
    case "stale_pages":
      return `前回までの結果が ${ev.count} 件残っています（今回の入力に無いファイル）。`
        + `出力にはその行も含まれます: ` + (ev.files ?? []).join("、");
    case "source_replaced":
      return `${ev.file} は前回と内容が変わっていたため、`
        + `前回の結果 ${ev.dropped_pages} 件を破棄して読み直します。`;
    case "skip_duplicate":
      return `${ev.file} は ${ev.same_as} と同じ内容のため送信を省きました。`
        + `行は〓で出力されます。`;
    case "template_changed_resend":
      return `テンプレートまたは位置合わせ方式が変わっているため、`
        + `${ev.count} 件のページを読み直します（API 送信が発生します）。`;
    case "remap_warning":
      return `${ev.page_id}: 位置合わせ済みの画像が見つからないセルが `
        + `${ev.missing_aligned_cells} 件あります。該当セルは〓になります。`;
    // #46 で追加されたファイル改名イベント（pipeline.py）。#52 M-3 の修正で
    // 3件足したが、この2件が default 節に落ちて捨てられていた（issue #60 M-2）
    case "source_renamed":
      return `${ev.was} は ${ev.file} に改名されたとみなし、`
        + `${ev.pages} 件のページを引き継ぎました（再送信はしません）。`;
    case "rename_fallback":
      // コア側コメント「送信（課金）が動く分岐なので黙らない」と明言して
      // 出しているイベント（pipeline.py:373）。文言も再送信・課金に触れる
      return `${ev.was} と同じ内容の ${ev.file} を改名として引き継げなかったため、`
        + `新規入力として送信します（API 送信＝課金が発生します）。`;
    // issue #65-3 S2: run の完了サマリ・remap の完了サマリの両方に配線する
    // （#60 M-2 の source_renamed／#66 段2 の片配線と同じ「片方だけ配線」を
    // 繰り返さない）
    // 中間データの削除（issue #52 M-11）。run と同じ core-line 経路で届くので、
    // 結果は他の警告と同じ「実行時のお知らせ」へ積む
    case "purged":
      return purgeNotice(ev);
    case "summary":
    case "remap_summary": {
      // P-H1: counterNotice（欠落〓の出所）の隣に accumulationNotice（中間
      // データの累積警告）を添える。どれも null なら通知自体を出さない。
      // snapNotice（issue #75 (f)）も同じ枠に置く——run の summary にしか
      // 2キーは乗らないので remap_summary では常に null になるが、
      // 片配線（#60 M-2・#66 段2）を繰り返さないために分岐は分けない
      const parts = [counterNotice(ev), snapNotice(ev), accumulationNotice(ev)]
        .filter((s): s is string => !!s);
      return parts.length ? parts.join(" ") : null;
    }
    default:
      return null;
  }
}

/// verify の template チェックが返す output_disabled_cells（issue #66 段4・
/// FR-1.9・かなた S-5「事故防止の最後の砦」）から「N 欄を出力しません」の
/// 1行を組み立てる。テンプレート編集画面を見ない運用者に、出力対象外の
/// 欄があることを実行前に届ける唯一の経路。
///
/// N=0（対象外なし）・フィールド欠落（旧コアとの組み合わせ）はいずれも
/// null——呼び出し側は何も表示しない。GUI 側で欄数を再導出せず、この値を
/// そのまま使う（FR-0.1 と同じ「唯一の正は core 応答」の思想）。
export function outputDisabledNotice(n: number | undefined): string | null {
  if (n == null || n <= 0) return null;
  return `このテンプレートは ${n} 欄を出力しません。`;
}

/// 完了サマリの summary.reused_pages（issue #72 (t)・実機通し確認の指摘）
/// から「既存の読み取り結果を再利用: N ページ（送信なし）」を組み立てる。
/// api_calls が処理枚数（pages）より少ないと、送信していないのか読み
/// 落としたのか利用者から区別できない——中間データの整列結果を再利用した
/// ページ数をここで明示する。core は未提供のキー（旧コア・追加未完了）
/// なので、undefined・0以下はいずれも null（呼び出し側は何も表示しない）。
export function reusedPagesNotice(reusedPages: number | undefined): string | null {
  if (reusedPages == null || reusedPages <= 0) return null;
  return `既存の読み取り結果を再利用: ${reusedPages} ページ（送信なし）`;
}

/** `run_core_capture(["verify"])` の stdout（JSON Lines）を Verify へ変換する。
 *
 *  event:"verify" 行を1つも見なかった場合（コア起動自体に失敗・JSON が
 *  1行も来ない等）を `parsed: false` で区別する（issue Q-ME）。以前は
 *  この区別が無く、budgetCap 900 等の既定値がそのまま「現状」として
 *  画面に出ていた——検証が走っていないのに走った体で表示するのは捏造に近い。
 *
 *  呼び出し側（runVerify）の try/catch は残す: verify は不備（Poppler欠損等）
 *  があると終了コード1で失敗するが、その場合も stdout に検証結果の JSON は
 *  乗っている正常系のため、catch 側でも同じ parseVerify を通す。 */
export function parseVerify(text: string): Verify {
  const v: Verify = { template: false, poppler: false, cred: "missing", storage: true,
                      budgetUsed: 0, budgetCap: 900, parsed: false };
  let sawVerify = false;
  for (const line of text.split("\n")) {
    try {
      const e = JSON.parse(line);
      if (e.event !== "verify") continue;
      sawVerify = true;
      if (e.check === "template") {
        v.template = !!e.ok;
        // 旧コア（フィールド欠落）では undefined のまま——outputDisabledNotice
        // が非表示に倒す（issue #66 段4）
        v.outputDisabledCells = typeof e.output_disabled_cells === "number"
          ? e.output_disabled_cells : undefined;
      }
      if (e.check === "poppler") v.poppler = !!e.ok;
      if (e.check === "credentials") {
        v.cred = e.state ?? (e.ok ? "env" : "missing");
        // Wave 2（S-MB core側）で追加される env_present。無ければ undefined
        v.envPresent = typeof e.env_present === "boolean" ? e.env_present : undefined;
      }
      if (e.check === "local_storage") v.storage = !!e.ok;
      if (e.check === "api_budget") {
        v.budgetUsed = e.used ?? 0; v.budgetCap = e.cap ?? 900;
      }
    } catch { /* skip */ }
  }
  v.parsed = sawVerify;
  if (!sawVerify) v.rawFirstLine = (text.split("\n")[0] ?? "").trim();
  return v;
}

/** S-MB: 認証キーが環境変数（平文 JSON）で使われている旨の常時警告。
 *  cred が "env"、または core が明示的に env_present（Wave 2 追加・dpapi と
 *  env が両方ある場合も env の存在を伝える独立キー）を返した場合に警告文を
 *  返す。それ以外は null。credentials_state の ok（実行可否）はここでは
 *  変えない——env でも実行は許可する設計（プラン確定）。 */
export function credNotice(cred: string, envPresent?: boolean): string | null {
  if (cred !== "env" && envPresent !== true) return null;
  return "認証キーが平文（環境変数 GOOGLE_APPLICATION_CREDENTIALS）で使われています。"
    + "取り込むと DPAPI で暗号化されます。";
}

// ---------------------------------------------------------------- issue #72 (t)
// テンプレート選択（FR-F27・FR-F29・設計08 §3.5.1）。config.json の
// last_template は「"shipped" = 出荷テンプレート（core/chouhyo_ocr/config.py
// の既定値・_validate が唯一この文字列と "user:<名前>" しか通さない）」
// 「"user:<名前>" = 利用者テンプレート」の2値だけを持つ（絶対パスは
// 保存しない・07 FR-F29）。この画面の <select> の value も同じ表現に
// 揃えることで、read_config / write_config との往復で形が1つに保たれる。

export type TemplateRef = { kind: "shipped" | "user"; name: string };

/** config.last_template の文字列を解析する。"user:<名前>" 以外（"shipped"・
 *  空文字・不正な形式）は null——呼び出し側は null を「出荷テンプレート」
 *  として扱う（AC-F60: 範囲外を手書きしてもフォールバックして起動する、と
 *  同じ「例外を投げず安全側へ倒す」方針を GUI 側にも揃える）。 */
export function parseLastTemplate(value: string | undefined | null): TemplateRef | null {
  if (!value) return null;
  const m = /^user:(.+)$/.exec(value);
  return m ? { kind: "user", name: m[1] } : null;
}

/** parseLastTemplate の逆変換。shipped（または null）は常に "shipped"
 *  にする——core/chouhyo_ocr/config.py の `_validate` が受け付ける唯一の
 *  非 user: 値（複数の出荷テンプレートを列挙しない・07 §7.3「固定1件の
 *  既定候補」）。 */
export function formatLastTemplate(ref: TemplateRef | null): string {
  return ref && ref.kind === "user" ? `user:${ref.name}` : "shipped";
}

/** 選択中のテンプレートが一覧（userNames）からいなくなっていた場合
 *  （削除された等）、出荷テンプレートへ戻し、その旨の通知を返す
 *  （設計08 §3.5.3）。出荷選択（"shipped"）・未設定はそのまま素通りする。 */
export function resolveSelectedTemplate(
  selected: string, userNames: string[],
): { value: string; notice: string | null } {
  const ref = parseLastTemplate(selected);
  if (!ref) return { value: "shipped", notice: null };
  if (userNames.includes(ref.name)) return { value: selected, notice: null };
  return { value: "shipped", notice: `選択していたテンプレート「${ref.name}」が見つからないため、`
    + "出荷テンプレートに戻しました。" };
}

/** 「読み取りを開始」が無効の理由（ころね／user_advocate の初見ユーザー
 *  予測レビュー）。ボタンの disabled 条件（start ボタンの JSX）と同じ判定
 *  を同じ優先順でなぞり、最初に該当した1件だけを返す——理由が複数重なる
 *  ケースでも画面に出すのは1行のみ（既存の verify カード群を読めば残りの
 *  理由も分かる）。inputDir 未選択は既存の「読み取る帳票を選択すると
 *  実行できます」に任せるため null。verify 未取得（検証中）も一時的な
 *  状態なので理由を出さない。 */
export function startDisabledReason(inputDir: string, verify: Verify | null,
                                    storageAck = false): string | null {
  if (!inputDir || !verify) return null;
  if (!verify.parsed) return "検証が実行できていません（再試行してください）";
  if (verify.cred === "missing") return "認証キーが未設定です（下の「認証キーを選択」から設定してください）";
  if (verify.budgetUsed >= verify.budgetCap) return "今月の送信上限に達しています";
  // issue #52 M-12／Q-MJ: 同期フォルダ判定は「広めに倒す」設計のため誤検知が
  // ありうる。ハードブロックのままだと、誤検知に当たった利用者はツールを
  // 一切使えない（逃げ道なし）。理解した旨の明示チェック1回で開始できる
  if (!verify.storage && !storageAck) {
    return "保存先がクラウド同期フォルダ等の下にあります"
      + "（設定で変更するか、下の確認チェックを入れてください）";
  }
  return null;
}

/** 失敗一覧（`failures`）に積む上限（issue #53 L-17）。`setLog` の 400 と
 *  そろえる。 */
export const FAILURE_KEEP = 400;

/** 失敗一覧へ1件足す。上限を超えたら**足さない**（先頭 400 件を残す）。
 *
 *  `setLog` の `slice(-400)`（末尾を残す）と向きが逆なのは意図的——ログは
 *  「最後に何が起きたか」を見るもので、失敗一覧は「どのページがどう失敗
 *  したか」の診断材料だから。全ページが同じ理由で失敗するような形は先頭
 *  400 件に必ず現れるし、末尾を残す方式だと「1ページ目から連続して失敗
 *  している」という重要な形が画面から消える。配列を作り直さないぶん、
 *  数千件規模の失敗でも描画コストが伸びない（L-17 の狙い）。 */
export function appendFailure<T>(list: T[], item: T): T[] {
  return list.length >= FAILURE_KEEP ? list : [...list, item];
}

/** 一覧に載せきれなかった件数の注記（issue #53 L-17）。全件出しているなら
 *  null——「他 0 件」は出さない。 */
export function truncatedFailureNotice(total: number, shown: number): string | null {
  const rest = total - shown;
  return rest > 0 ? `他 ${rest} 件（一覧の表示は ${shown} 件までです）` : null;
}

/* ------------------------------------------------------------------ *
 * 実行イベントの取り違え防止（issue #96）
 * ------------------------------------------------------------------ */

/** `run_core` の戻り値（Rust 側 `RunResult`）。 */
export type RunResult = { code: number; run_id: string };

/** `core-line` / `core-err` の payload（Rust 側 `CoreLine`）。
 *  run_id を持たない旧形式（行の文字列だけ）も受ける。 */
export type CoreLinePayload = string | { run_id?: string; line?: string };

/** payload を「行」と「run_id」に開く。
 *
 *  旧形式（文字列）では run_id が undefined になり、フィルタは素通りする
 *  ——ここを fail-closed にすると、payload の形が想定と1つズレただけで
 *  ログも進捗もサマリも一切出ない画面になる。取り違えより無反応の方が
 *  利用者にとって深刻なので、判別できないときは通す。 */
export function readCoreLine(payload: CoreLinePayload): { line: string; runId?: string } {
  if (typeof payload === "string") return { line: payload };
  return { line: payload?.line ?? "", runId: payload?.run_id };
}

/** イベントの取り違えを防ぐフィルタの状態。
 *
 *  `current` は `core-start` で確定した「今回の実行」の ID。まだ届いていない
 *  間は null で、そのときは「終了済みでない ID」を通す——`core-start` と
 *  `core-line` は別のイベント名で、到着順は保証されないため。
 *  `retired` は既に画面から降りた実行の ID（新しい順）。 */
export type RunFilter = { current: string | null; retired: string[] };

/** `retired` の保持数。1実行あたり1件しか増えず、遅れて届く行はプロセス
 *  終了直後の数ミリ秒ぶんなので、直近数件を覚えていれば足りる。 */
const RETIRED_KEEP = 8;

export function emptyRunFilter(): RunFilter {
  return { current: null, retired: [] };
}

/** 実行開始（`invoke("run_core")` の直前）。
 *
 *  直前の実行 ID をここで初めて「古い」側へ移す。`run_core` が解決した時点で
 *  移すと、その実行自身の最後の行（サマリ）がまだ webview へ届いていない
 *  場合に捨ててしまい、完了表示が出なくなる。画面を次の実行用に片付ける
 *  この瞬間なら、前の実行の行はもう表示する先が無い。 */
export function beginRun(f: RunFilter): RunFilter {
  const retired = f.current
    ? [f.current, ...f.retired.filter((id) => id !== f.current)].slice(0, RETIRED_KEEP)
    : f.retired;
  return { current: null, retired };
}

/** `core-start` 受信。以後はこの ID の行だけを受ける。 */
export function adoptRun(f: RunFilter, runId: string): RunFilter {
  return { current: runId, retired: f.retired.filter((id) => id !== runId) };
}

/** `run_core` の解決（正常終了・中断を問わず）。
 *
 *  `core-start` を取り逃していた場合の保険で、ここでも今回の ID を確定させる
 *  ——確定していないと次の `beginRun` がこの実行を `retired` へ移せず、
 *  遅れて届く行を捨てられない。 */
export function finishRun(f: RunFilter, runId: string): RunFilter {
  return f.current === null ? { current: runId, retired: f.retired } : f;
}

/** この行イベントを今回の実行のものとして受けてよいか。 */
export function acceptsRunEvent(f: RunFilter, runId?: string): boolean {
  if (runId === undefined) return true;
  if (f.retired.includes(runId)) return false;
  return f.current === null || f.current === runId;
}

/** 破壊的な操作の前に出す確認ダイアログ（issue #52 M-10／M-11）。
 *
 *  作りは Editor.tsx の保存前確認モーダル（issue #87 項目1）に揃える:
 *  `role="alertdialog"`・`aria-modal`・Tab はダイアログ内で循環・Esc は中止・
 *  初期フォーカスは中止側（Enter を押したときに走るのは「何も変えない側」）。
 *  背景クリックも中止。`window.confirm` を使わないのは、長文が折り返されず
 *  ボタンのラベルも OS 既定に固定されるため（同 issue）。 */
function ConfirmDialog(
  { title, confirmLabel, cancelLabel = "中止", busy = false, danger = false,
    onConfirm, onCancel, children }: {
    title: string; confirmLabel: string; cancelLabel?: string; busy?: boolean;
    danger?: boolean; onConfirm: () => void; onCancel: () => void; children: ReactNode;
  },
) {
  const boxRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { cancelRef.current?.focus(); }, []);
  return (
    <div className="modal-back" onClick={() => { if (!busy) onCancel(); }}>
      <div className="modal" ref={boxRef} role="alertdialog" aria-modal="true"
        aria-labelledby="run-confirm-title" aria-describedby="run-confirm-body"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") { e.preventDefault(); if (!busy) onCancel(); return; }
          if (e.key !== "Tab") return;
          const root = boxRef.current;
          if (!root) return;
          const focusables = Array.from(root.querySelectorAll<HTMLElement>(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
            .filter((el) => !el.hasAttribute("disabled"));
          if (focusables.length === 0) return;
          const first = focusables[0], last = focusables[focusables.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }}>
        <h3 id="run-confirm-title">{title}</h3>
        <div id="run-confirm-body" style={{ fontSize: 13, lineHeight: 1.8, margin: "0 0 16px" }}>
          {children}
        </div>
        <div style={{ display: "flex", gap: 10 }}>
          <button ref={cancelRef} type="button" className="btn" disabled={busy}
            onClick={onCancel}>{cancelLabel}</button>
          <button type="button" className={danger ? "btn danger" : "btn primary"}
            disabled={busy} aria-busy={busy}
            onClick={onConfirm}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}

const FolderIcon = ({ c }: { c: string }) => (
  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke={c} strokeWidth="2"
    strokeLinecap="round" strokeLinejoin="round">
    <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
  </svg>
);

export default function RunScreen(
  { active = true, configRev = 0 }: { active?: boolean; configRev?: number },
) {
  const [inputDir, setInputDir] = useState("");
  const [outputDir, setOutputDir] = useState("output");
  const [running, setRunning] = useState(false);
  const [total, setTotal] = useState(0);
  const [done, setDone] = useState(0);
  const [log, setLog] = useState<string[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [verify, setVerify] = useState<Verify | null>(null);
  const [importing, setImporting] = useState(false);
  const [failures, setFailures] = useState<Failure[]>([]);
  // 一覧は先頭 FAILURE_KEEP 件で打ち切るため、件数は別に数える（issue #53 L-17）
  const [failureTotal, setFailureTotal] = useState(0);
  // 認証キー取り込みの確認（issue #52 M-10）。選んだファイルのパスを保持し、
  // 確認を通ったときだけ取り込みを実行する
  const [credConfirm, setCredConfirm] = useState<string | null>(null);
  // 中間データ削除の二段確認（issue #52 M-11）。"explain"=何が消えて何が残るか、
  // "confirm"=最終確認。null は閉じている状態
  const [purgeStep, setPurgeStep] = useState<"explain" | "confirm" | null>(null);
  const [purgeIncludeOutput, setPurgeIncludeOutput] = useState(false);
  const [purging, setPurging] = useState(false);
  // 同期フォルダ警告の明示チェック（issue #52 M-12／Q-MJ）。**保存しない**
  // ——毎回チェックし直す（設定に残すと「一度通したら以後ずっと素通り」に
  // なり、警告の意味が消える）
  const [storageAck, setStorageAck] = useState(false);
  const interruptedRef = useRef(false);
  const refusedRef = useRef(false);
  // 終了時の文言判定（completionNotice）で使う最新のサマリ。state の方は
  // start() のクロージャが古い値を掴むため、interruptedRef と同じ流儀で
  // ref にも持つ（issue N-1）
  const summaryRef = useRef<Summary | null>(null);
  // 実行イベントの取り違え防止（issue #96）。listen の登録はマウント時の
  // 1回きり（deps=[]）なので、state ではなく ref で持つ
  const runFilterRef = useRef<RunFilter>(emptyRunFilter());
  const [notice, setNotice] = useState("");
  const [notices, setNotices] = useState<string[]>([]);  // 実行時の警告（M-2・#28）
  const [refused, setRefused] = useState("");  // 業務的な拒否（H-C）
  const [loadError, setLoadError] = useState("");  // 設定読み込み失敗（issue Q-MF）
  const logRef = useRef<HTMLPreElement>(null);
  const screenRef = useRef<HTMLDivElement>(null);
  // issue #72 (t)・FR-F27・設計08 §3.5。テンプレート選択（出荷1件＋利用者
  // 一覧）。value は parseLastTemplate/formatLastTemplate と同じ表現
  // （"shipped" = 出荷・"user:<名前>" = 利用者）。GUI は絶対パスを持たない
  const [templates, setTemplates] = useState<TemplateRef[]>([{ kind: "shipped", name: "chouhyo-v1" }]);
  const [selectedTemplate, setSelectedTemplate] = useState("shipped");
  const [templateNotice, setTemplateNotice] = useState("");

  const runVerify = async () => {
    try {
      setVerify(parseVerify(await invoke<string>("run_core_capture", { args: ["verify"] })));
    } catch (e) {
      setVerify(parseVerify(String(e)));  // verify は不備時に終了コード1で stdout ごと届く
    }
  };
  useEffect(() => { runVerify(); }, []);

  // 認証キーを選ぶ → 確認ダイアログ（issue #52 M-10: 元ファイルを消す操作を
  // 黙って行わない）→ 取り込み、の3段。選択と実行の間に確認を挟むため、
  // 選んだパスを credConfirm に持たせる
  const pickCredentials = async () => {
    // 認証キーは白リストへ登録しない（登録すると鍵の平文 JSON が
    // セッション中ずっと read_text で読める・レビュー4巡目）
    const p = await invoke<string | null>("pick_json",
      { save: false, rememberPick: false, kind: "credentials" });
    if (p) setCredConfirm(p);
  };

  const importCredentials = async (p: string) => {
    setCredConfirm(null);
    setImporting(true);
    try {
      // --delete-source（issue #52 M-10）: DPAPI へ書けたら元の平文 JSON を
      // ランダム上書きのうえ削除する。GUI からは常に付ける——取り込みの
      // たびに有効な秘密鍵が平文でディスクに残る状態を作らない
      const out = await invoke<string>("run_core_capture",
        { args: ["import-credentials", p, "--delete-source"] });
      setNotice(importCredentialsNotice(out));
      await runVerify();
    } catch (e) {
      setError(`認証キーの取り込みに失敗しました: ${e}`);
    } finally {
      setImporting(false);
    }
  };

  /** 中間データの削除（issue #52 M-11・要件 §6.3「削除は明示操作のみ」）。
   *
   *  §6.3 を満たしているのは「利用者がボタンを押し、何が消えて何が残るかの
   *  説明を読み、二段目で削除を確定した」という明示操作の連なりであって、
   *  この関数が呼ばれる経路は他に無い（自動実行・起動時の掃除は一切しない）。
   *  run と同じ `run_core` を通すので、実行中は PID スロットが埋まっていて
   *  受け付けられない＝読み取りと削除が同時に走らない。 */
  const runPurge = async () => {
    setPurgeStep(null);
    setPurging(true);
    setError("");
    // 前の実行の遅れて届く行を、この結果の表示へ混ぜない（issue #96）
    runFilterRef.current = beginRun(runFilterRef.current);
    try {
      const args = ["purge", "--yes"];
      if (purgeIncludeOutput) args.push("--include-output");
      const res = await invoke<RunResult>("run_core", { args });
      runFilterRef.current = finishRun(runFilterRef.current, res.run_id);
      if (res.code !== 0) {
        // 件数の内訳は purged イベント（お知らせ）側に出ている。ここでは
        // 「全部は消えていない」ことだけを赤帯で伝える
        setError("削除しきれなかったものがあります。上の「実行時のお知らせ」を確認してください。");
      }
    } catch (e) {
      setError(`削除に失敗しました: ${e}`);
    } finally {
      setPurging(false);
      // 削除後は中間データの再利用ができなくなる（次回は送信からやり直し）。
      // 残量・保存先の状態も取り直す
      await runVerify();
    }
  };

  useEffect(() => {
    // 設定モーダルで保存されたら読み直す（M-3: 変更後も古いパスを表示し、
    // 「出力フォルダを開く」が別の場所を開いていた）。
    // 読み込み失敗を握りつぶさない（issue Q-MF）——出力先が既定値（"output"）
    // のまま表示され、実際の設定と食い違っていることに気づけなかった
    invoke<Record<string, unknown>>("read_config").then((c) => {
      if (typeof c.output_dir === "string") setOutputDir(c.output_dir);
      // last_template（issue #72 (t)・設計08 §3.5.1）。不正な形式は
      // parseLastTemplate が null（出荷扱い）へ倒す——ConfigError で
      // 起動不能になる他キーとは違う「例外を投げない」キー（AC-F60 と
      // 同じ方針を GUI 側にも揃える）
      if (typeof c.last_template === "string") {
        setSelectedTemplate(formatLastTemplate(parseLastTemplate(c.last_template)));
      }
      setLoadError("");
    }).catch((e) => setLoadError(String(e)));
  }, [configRev]);

  // 利用者テンプレートの一覧（issue #72 (t)・FR-F27・FR-F28）。RunScreen は
  // タブ切替でアンマウントされない（App.tsx）ため、タブが表示されるたびに
  // 読み直す——編集画面で保存した直後に反映されるようにするため
  const refreshTemplates = async () => {
    try {
      // list_user_templates は { templates: [{name,...}], excluded: [...] }
      // を返す（gui/src-tauri/src/user_templates.rs の UserTemplateInfo/
      // ExcludedInfo）。ここでは名前だけを使う
      const list = await invoke<{ templates: { name: string }[] }>("list_user_templates");
      const userNames = (list?.templates ?? []).map((e) => e.name);
      setTemplates([{ kind: "shipped", name: "chouhyo-v1" },
        ...userNames.map((name): TemplateRef => ({ kind: "user", name }))]);
      setSelectedTemplate((cur) => {
        const resolved = resolveSelectedTemplate(cur, userNames);
        setTemplateNotice(resolved.notice ?? "");
        return resolved.value;
      });
    } catch { /* 一覧取得に失敗しても実行画面自体は使える（出荷既定のまま） */ }
  };
  useEffect(() => { if (active) refreshTemplates(); }, [active]);

  const onSelectTemplate = async (value: string) => {
    setSelectedTemplate(value);
    setTemplateNotice("");
    try {
      await invoke("write_config", { patch: { last_template: value } });
    } catch (e) {
      setError(`テンプレート選択の保存に失敗しました: ${e}`);
    }
  };

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  // ウィンドウの縦幅を実行画面の状態に揃える（起動時の余白なし・完了サマリ
  // でのスクロールなしの両立・ユーザー承認済み 2026-09-01）。
  // タブ切替のたびに規定サイズへ揃える方針のため、この画面がアクティブに
  // なった瞬間（active）と、サマリの有無が変わった瞬間（summary）の両方で
  // 発火させる——手動リサイズの保持はしない。ブラウザのデモモードでは
  // window API が無いため isTauri で no-op にする（bridge.ts と同じ流儀）。
  //
  // issue #72 (t)・実機通し確認の指摘: 以前は summary が無い間（起動直後・
  // 入力待ち）を「常に既定の小窓（RUN_WINDOW_HEIGHT_DEFAULT）」に固定して
  // 実測をスキップしていた。テンプレート選択カード（list_user_templates の
  // 非同期取得後に伸びる）・再利用ページ数の注記など、summary 表示前の
  // 画面も内容が伸びる経路が増えたため、**summary の有無を問わず常に本文を
  // 実測**する形に直した（既定値は targetWindowHeight の下限クランプとして
  // 働くので、本文が既定より短ければ従来どおり既定の小窓のままになる）。
  //
  // 高さに影響する非同期完了（テンプレート一覧の取得＝templates・verify
  // 結果の変化）も再計測のトリガーに追加した——マウント直後の1回だけでは、
  // verify（認証キー未設定の初回案内カード等、後から現れる大きな要素）が
  // 間に合わないまま測ってしまう。
  //
  // running を deps に残し、running===false のときだけ計測する（issue
  // Q-MD）。完了直後は「summary が入る」「running が false になる」の2つの
  // 状態更新がほぼ同時に起き、どちらも deps 変化として effect を再発火
  // させるため、旧実装（deps=[active, summary]）は setSize を2回呼びかねな
  // かった。running===true の間は何もしない（実行中に options が動くのは
  // 望ましくない）うえ、実際の計測は rAF 1回分だけ遅らせてまとめる——
  // ResizeObserver は setSize との相互発火（発振）を招くため使わない
  // （明示的な state 変化のあとに1回だけ計測する、という既存方針を維持）。
  useEffect(() => {
    if (!isTauri || !active || running) return;
    const raf = requestAnimationFrame(() => {
      (async () => {
        const win = getCurrentWindow();
        const factor = await win.scaleFactor();
        const contentHeight = screenRef.current?.scrollHeight ?? RUN_WINDOW_HEIGHT_DEFAULT;
        const appbarHeight = document.querySelector(".appbar")?.getBoundingClientRect().height
          ?? APPBAR_HEIGHT_FALLBACK;
        let workAreaHeight = NaN;
        try {
          const monitor = await currentMonitor();
          if (monitor) workAreaHeight = monitor.workArea.size.toLogical(factor).height;
        } catch { /* 取得失敗時は targetWindowHeight が安全側の上限へフォールバックする */ }
        const height = targetWindowHeight(contentHeight, appbarHeight, workAreaHeight);
        await win.setSize(new LogicalSize(RUN_WINDOW_WIDTH, height));
      })().catch(() => { /* デモ/取得失敗時は実行の妨げにしない */ });
    });
    return () => cancelAnimationFrame(raf);
  }, [active, summary, running, verify, templates]);

  useEffect(() => {
    const subs: Promise<UnlistenFn>[] = [
      // 今回の実行 ID（issue #96）。読取スレッドが起きる前に 1 回だけ届く
      listen<{ run_id?: string }>("core-start", (e) => {
        const id = e.payload?.run_id;
        if (id) runFilterRef.current = adoptRun(runFilterRef.current, id);
      }),
      listen<CoreLinePayload>("core-line", (e) => {
        // 前の実行の残り行を新しい実行の画面へ混ぜない（issue #96）。
        // Rust 側は読取スレッドを join してから戻るが、emit された行が
        // webview へ届くのと invoke の応答が返るのとで順序の保証が無い
        const { line, runId } = readCoreLine(e.payload);
        if (!acceptsRunEvent(runFilterRef.current, runId)) return;
        setLog((l) => [...l.slice(-400), line]);
        try {
          const ev = JSON.parse(line);
          if (ev.event === "start") { setTotal(ev.todo ?? ev.total ?? 0); setDone(0); }
          if (ev.event === "page") {
            setDone((d) => d + 1);
            if (ev.status && ev.status !== "done") {
              // 一覧は上限まで、件数は全部数える（issue #53 L-17）
              setFailures((f) => appendFailure(f,
                { page_id: ev.page_id, status: ev.status, reason_code: ev.reason_code }));
              setFailureTotal((n) => n + 1);
            }
          }
          // render 経路の失敗（issue #80・`出力失敗`）。ページ単位の進捗は
          // `page` イベントが担うので setDone は呼ばない（進捗バーの二重進行を
          // 避ける）。state は done のままなので次回 run で再送はされない
          if (ev.event === "render_page_failed") {
            setFailures((f) => appendFailure(f,
              { page_id: ev.page_id, status: ev.status, reason_code: ev.reason_code }));
            setFailureTotal((n) => n + 1);
          }
          const n = noticeFor(ev);
          if (n) setNotices((ns) => [...ns, n]);
          // 業務的な拒否（テンプレ変更・多重起動など）を正しく伝える（H-C）。
          // 旧実装は exit≠0 の固定文言「再度押すと続きから処理します」を
          // 出していたが、決定論的な拒否なので押しても永久に同じ結果になる
          if (ev.event === "refused") {
            refusedRef.current = true;
            setRefused(ev.error + (ev.hint ? `
${ev.hint}` : ""));
          }
          if (ev.event === "summary") {
            summaryRef.current = ev as Summary;
            setSummary(ev as Summary);
          }
        } catch { /* JSON 以外の行は無視 */ }
      }),
      listen<CoreLinePayload>("core-err", (e) => {
        const { line, runId } = readCoreLine(e.payload);
        if (!acceptsRunEvent(runFilterRef.current, runId)) return;
        setLog((l) => [...l.slice(-400), `[err] ${line}`]);
      }),
    ];
    return () => { subs.forEach((p) => p.then((un) => un())); };
  }, []);

  const pickInput = async () => {
    const p = await invoke<string | null>("pick_folder");
    if (p) setInputDir(p);
  };

  // フォルダでも PDF ファイル1つでも、ドラッグ＆ドロップなら区別なく受ける
  // （OS のダイアログは「ファイル」「フォルダ」を1つの画面で選べないため、
  //   ボタンを増やす代わりにドロップで両対応する・issue #19）。
  // コアの run --input はフォルダ・ファイルの両方を受ける
  const [dropping, setDropping] = useState(false);
  const activeRef = useRef(active);
  useEffect(() => {
    activeRef.current = active;
    // Rust 側の白リスト登録も、この画面が見えている間だけに絞る
    // （issue #69 セキュリティ LOW (b)）。編集タブを見ている最中のドロップは
    // 画面に何も出ないのに読み書きを許すパスだけが増えていた
    invoke("set_drop_active", { active }).catch(() => { /* デモモードでは何もしない */ });
  }, [active]);
  useEffect(() => {
    if (!isTauri) return;
    let unlisten: (() => void) | undefined;
    import("@tauri-apps/api/webview").then(({ getCurrentWebview }) =>
      getCurrentWebview().onDragDropEvent((e) => {
        if (e.payload.type === "over") setDropping(activeRef.current);
        else if (e.payload.type === "leave") setDropping(false);
        else if (e.payload.type === "drop") {
          setDropping(false);
          // 実行画面が表示されていないときは受け取らない（M-1: 編集タブで
          // ドロップすると、画面に何も出ないまま実行対象が書き換わっていた）
          if (!activeRef.current) return;
          const p = e.payload.paths?.[0];
          if (!p) return;
          // 白リスト（run --input のパススコープ検査・issue S-MD）への登録は
          // Rust 側が同じドロップを OS のイベントとして受けて行う
          // （lib.rs の on_window_event → remember_dropped・issue S-N1）。
          // ここから invoke で登録すると webview が任意パスを白リストへ
          // 入れられてしまうため、画面側は表示の更新だけに徹する
          setInputDir(p);
        }
      })).then((u) => { unlisten = u; });
    return () => unlisten?.();
  }, []);
  const pickOutput = async () => {
    const p = await invoke<string | null>("pick_folder");
    if (!p) return;
    setOutputDir(p);
    // 要件 §5.7: 選んだ値を設定へ保存し次回起動時の既定値にする。
    // write_config は不正パス等で reject しうる（issue Q-MC/S-MA・枠C申し送り）
    try {
      await invoke("write_config", { patch: { output_dir: p } });
    } catch (e) {
      setError(`出力先の保存に失敗しました: ${e}`);
    }
  };
  const start = async () => {
    setRunning(true); setSummary(null); setError(""); setNotice("");
    setLog([]); setDone(0); setTotal(0); setFailures([]); setFailureTotal(0);
    setNotices([]); setRefused("");
    interruptedRef.current = false; refusedRef.current = false;
    summaryRef.current = null;
    // 画面を片付けたこの時点で、前回の実行 ID を「古い」側へ移す（issue #96）。
    // これより後に届く前回の行は、サマリであっても捨てる
    runFilterRef.current = beginRun(runFilterRef.current);
    try {
      const res = await invoke<RunResult>("run_core", { args: ["run", "--input", inputDir] });
      runFilterRef.current = finishRun(runFilterRef.current, res.run_id);
      const code = res.code;
      if (refusedRef.current) {
        // 拒否済み: 固定文言（再実行を促す）を出さない
      } else if (interruptedRef.current) {
        setNotice("中断しました。処理済みの内容は保存されています。再開すると続きから処理します。");
      } else {
        // exit!=0 でもサマリが届いていれば「中断」ではない（issue N-1）。
        // 全ページ様式不一致のバッチは再実行しても同じ結果になるため、
        // 「続きから処理します」ではなく原因の確認へ誘導する
        const text = completionNotice(summaryRef.current, code);
        if (text) setError(text);
      }
    } catch (e) {
      if (!interruptedRef.current) setError(String(e));
    } finally {
      setRunning(false);
      // 実行後に残量を取り直す（issue #47）。旧実装は runVerify がマウント時と
      // 資格情報の取り込み後にしか走らず、100枚読んだ直後も「残り900枚」の
      // ままだった。開始ボタンの disabled は verify を見ているため、
      // 上限に達しても押せてしまい、コア側で拒否されるまで理由が分からない
      await runVerify();
    }
  };
  const interrupt = async () => {
    interruptedRef.current = true;
    try { await invoke("kill_core"); } catch { /* 既に終了 */ }
  };
  const openOutput = () =>
    invoke("open_folder", { path: outputDir }).catch((e) => setError(String(e)));

  const xlsxName = summary?.xlsx?.split(/[\\/]/).pop();
  const bannerTone = completionBannerTone(summary);
  // 「実行時のお知らせ」。完了サマリの付随情報として出すのが基本だが、
  // 中間データの削除（issue #52 M-11）は summary を伴わないため、サマリの
  // 有無に関わらず出せるよう1箇所で組み立てて2箇所から使う
  const noticesCard = notices.length > 0 ? (
    <div className="card warnbox">
      <b>実行時のお知らせ</b>
      {notices.map((t, i) => <div key={i}>{t}</div>)}
    </div>
  ) : null;

  return (
    <div className="run-screen" ref={screenRef}>
      {dropping && (
        <div className="dropzone-overlay">
          ここにドロップすると読み取り対象になります（フォルダ・PDF ファイルどちらでも）
        </div>
      )}
      <div className="run-main">

        {/* 完了バナー。1件も送信せず全ページ様式不一致で終わった実行は
            緑ではなく注意色にする（issue #69 残置1・completionBannerTone）。
            文言は変えない——何が起きたかは completionNotice の赤帯が既に
            説明しており、同じ内容を2箇所に持たない */}
        {summary && (
          <div className={`banner ${bannerTone}`}>
            {bannerTone === "ok" ? (
              <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#16a34a"
                strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" /><path d="M8 12.5l3 3 5-6" />
              </svg>
            ) : (
              <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#a16207"
                strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" role="img"
                aria-label="注意">
                <path d="M12 3.5L21.5 20H2.5z" /><path d="M12 10v4" /><path d="M12 17.2v.1" />
              </svg>
            )}
            <div className="txt">
              <b>読み取りが完了しました</b>
              <span>Excel と CSV を保存しました{xlsxName ? `（${xlsxName}）` : ""}</span>
            </div>
          </div>
        )}

        {/* 完了サマリ6項目（要件 §5.9 と同一。副題は平易な言葉） */}
        {summary && (
          <div className="summary6">
            <div className="sumcard"><span className="k">処理枚数</span>
              <span className="v">{summary.pages}</span><span className="s">読み取ったページ数</span></div>
            <div className="sumcard"><span className="k">出力行数</span>
              <span className="v">{summary.rows}</span><span className="s">Excel に出力した行数</span></div>
            <div className="sumcard"><span className="k">API送信回数</span>
              <span className="v">{summary.api_calls}</span><span className="s">クラウド OCR の送信回数</span></div>
            <div className="sumcard warn"><span className="k">要確認セル数総計</span>
              <span className="v">{summary.unclear_cells}</span><span className="s">〓の個数（要修正箇所）</span></div>
            <div className={summary.align_failed > 0 ? "sumcard err" : "sumcard"}>
              <span className="k">位置合わせ失敗</span>
              <span className="v">{summary.align_failed}</span><span className="s">読み取れなかったページ数</span></div>
            <div className="sumcard"><span className="k">行数超過件数</span>
              <span className="v">{summary.overflow}</span><span className="s">行数を超過したページ数</span></div>
          </div>
        )}
        {/* issue #72 (t)・実機通し確認の指摘: 「API送信回数」がページ数より
            少ない理由（中間データの再利用）を、その項目の直後に説明する。
            summary6 は要件 §5.9 が固定した6項目のグリッドのため、7件目の
            カードとしては足さず、グリッドのすぐ下に注記として置く */}
        {summary && reusedPagesNotice(summary.reused_pages) && (
          <div className="muted" style={{ fontSize: 12.5, marginTop: -8 }}>
            {reusedPagesNotice(summary.reused_pages)}
          </div>
        )}
        {summary && (
          <div style={{ display: "flex", gap: 12 }}>
            <button className="btn primary big" onClick={openOutput}>
              <FolderIcon c="#ffffff" />出力フォルダを開く
            </button>
            <button className="btn big" onClick={start}>再度読み取る</button>
            <button className="btn" onClick={() => setSummary(null)}>条件を変更して読み取る</button>
          </div>
        )}

        {/* 完了後の付随情報（次の作業・実行時のお知らせ・CSV注意・位置合わせ失敗）。
            issue #65-5: 以前は右カラム（幅380px固定）に出していたが、実行前は
            その右カラムが空のまま幅だけ確保されて余白になっていた（issue #65-4
            で説明文を消した後に発覚）。単一カラムへ統合し、完了時にウィンドウ幅を
            変えずに済むようにする（完了の瞬間にリサイズすると体験が悪い） */}
        {summary && (
          <>
            <div className="card nextsteps">
              <div className="explain"><div className="h">次の作業（目視確認）</div></div>
              <div className="row"><b>1.</b>
                <div>Excel を開き、先頭列の<b>「要確認セル数」</b>を降順に並べ替えます</div></div>
              <div className="row"><b>2.</b>
                <div>背景色付きの <span className="mark">〓</span> セルを、原本と照合して修正します</div></div>
              <div className="row"><b>3.</b>
                <div>修正のたびに「要確認セル数」は自動的に減ります。<b>合計が 0</b> になれば完了です</div></div>
            </div>
            {noticesCard}
            {(summary.risky_cells ?? 0) > 0 && (
              // 出荷ゲート（要確認セル数）には載せない警告（D-28）。値は正しく
              // 出ており、修正の必要はない——CSV の開き方だけの注意
              <div className="card warnbox">
                <b>CSV の開き方に注意</b>
                <div>「=」「+」「-」で始まる値が {summary.risky_cells} セルあります。
                  CSV を Excel でダブルクリックして開くと、これらが計算式として実行され、
                  先頭ゼロも失われます。中身を見るときはテキストエディタか、Excel の
                  「データ」→「テキストまたは CSV から」で全列を文字列として取り込んでください。
                  目視確認と提出に使う Excel（.xlsx）側は影響を受けません。</div>
              </div>
            )}
            {summary.align_failed > 0 && (
              <div className="errbox">
                位置合わせに失敗したページが {summary.align_failed} 件あります。該当行はすべて〓のため、原本を参照して直接入力してください。
              </div>
            )}
            {/* 送信前に様式不一致で止まったページ（issue #71 (a')・FR-F09・FR-F10）。
                API を1回も呼んでいない＝課金が発生していないことを明示し、
                出口2択を案内する。issue #72 (t) でテンプレート選択が
                画面上部（補助カード）に増えたため、出口も編集画面ではなく
                この選択＋「再度読み取る」を第一に案内する（ころね／
                user_advocate の初見ユーザー予測レビュー: 旧文言は画面にある
                実際のボタン（出力フォルダを開く／再度読み取る／条件を変更
                して読み取る）のどれとも対応していなかった） */}
            {(summary.format_mismatch_pre_send ?? 0) > 0 && (
              <div className="card warnbox">
                <b>様式不一致（送信前・課金なし）: {summary.format_mismatch_pre_send} 件</b>
                <div>次にできること: テンプレートを選び直して「再度読み取る」か、テンプレート編集タブでこの紙のテンプレートを作ってください
                  （テンプレート選択は「条件を変更して読み取る」を押すと画面上部に出ます）。</div>
              </div>
            )}
          </>
        )}

        {/* サマリが無いとき（削除だけを行った直後など）のお知らせ。
            summary があるときは上の付随情報の並びの中で出している */}
        {!summary && noticesCard}

        {/* 処理中 */}
        {running && (
          <div className="card progress-card">
            <div className="head">
              <svg className="spin" width="28" height="28" viewBox="0 0 24 24" fill="none"
                stroke="#2563eb" strokeWidth="2.5" strokeLinecap="round">
                <path d="M21 12a9 9 0 1 1-6.2-8.56" />
              </svg>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                <b>読み取り中…</b>
                <span>完了までお待ちください。他の作業を続けていただいて構いません。</span>
              </div>
            </div>
            <div className="counter">処理中: <b>{Math.min(done + 1, Math.max(total, 1))}</b> / <b>{total || "?"}</b> ページ</div>
            <div className="bar"><div style={{ width: `${total ? (done / total) * 100 : 4}%` }} /></div>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div className="softnote" style={{ flex: 1 }}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5a6577"
                  strokeWidth="2" strokeLinecap="round"><path d="M13 2L4 14h6l-1 8 9-12h-6z" /></svg>
                途中で終了しても問題ありません。次回起動時は未処理分から再開します。
              </div>
              <button className="btn" onClick={interrupt}>中断</button>
            </div>
          </div>
        )}

        {refused && (
          <div className="card errbox" style={{ whiteSpace: "pre-wrap" }}>
            <b>読み取りを開始できません</b>
            <div>{refused}</div>
          </div>
        )}

        {/* 検証（verify）自体が実行できなかった場合（issue Q-ME）。event:"verify"
            行が1つも来ていないので、budgetCap 900 等の既定値を「現状」として
            出すのは捏造に近い——検証系カードは一切出さず、再試行導線だけ出す */}
        {!running && verify && !verify.parsed && (
          <div className="card errbox">
            <b>検証を実行できませんでした</b>
            <div>詳細: {verify.rawFirstLine || "（エラー内容を取得できませんでした）"}</div>
            <button className="btn" style={{ marginTop: 8 }} onClick={runVerify}>再試行</button>
          </div>
        )}

        {/* API 送信の残量（ユーザー指示 2026-08-28: 請求が立つ前に強制停止）。
            残り0で開始ボタンを止める——押せてしまうとコア側で止まるだけで、
            なぜ進まないのか画面から分からない */}
        {!running && verify && verify.parsed && verify.budgetCap > 0 && (
          <div className={verify.budgetUsed >= verify.budgetCap
            ? "card warnbox" : "card"} style={{ fontSize: 12.5 }}>
            {verify.budgetUsed >= verify.budgetCap ? (
              <>
                <b>今月の送信上限に達しました</b>
                <div>これ以上の読み取りは行いません（無料枠を超えて課金されるのを
                  防ぐためです）。続けるには設定ファイルの api_monthly_cap を
                  引き上げるか、翌月まで待ってください。</div>
              </>
            ) : (
              <div>今月の読み取り可能枚数: 残り <b>{verify.budgetCap - verify.budgetUsed}</b> 枚
                （使用 {verify.budgetUsed} / 上限 {verify.budgetCap}・無料枠 1,000）</div>
            )}
          </div>
        )}

        {/* 実行前の環境チェック（M-1: 旧実装は cred のみ表示で、Poppler 欠損や
            クラウド同期先の警告が画面に出ず、実行して初めて全ページ失敗した） */}
        {!running && verify && verify.parsed
          && (!verify.template || !verify.poppler || !verify.storage) && (
          <div className="card warnbox">
            <b>実行前に確認してください</b>
            {!verify.template && (
              <div>テンプレートを読み込めません（列定義の不整合など）。
                「テンプレート編集」タブで保存し直してください。</div>)}
            {!verify.poppler && (
              <div>PDF を画像化する部品（Poppler）が見つかりません。
                このまま実行するとすべてのページが展開失敗になります。
                インストールし直してください。</div>)}
            {!verify.storage && (
              <div>保存先がクラウド同期フォルダ（OneDrive・Dropbox・Box など）や
                ネットワーク共有の下にあります。中間データには個人情報が含まれるため、
                <b>そのままでは読み取りを開始できません</b>。設定でローカルの
                フォルダへ変更してください。
                {/* issue #52 M-12／Q-MJ: 同期判定は名前で広めに拾うため誤検知が
                    ありうる。ハードブロックだけだと、誤検知に当たった利用者は
                    設定を変える以外に手が無くなる（逃げ道なし）。理解した旨の
                    明示チェック1回を逃げ道にする。状態は保存しない（毎回必要） */}
                <label className="checkrow" style={{ marginTop: 10, marginBottom: 0 }}>
                  <input type="checkbox" checked={storageAck}
                    onChange={(e) => setStorageAck(e.target.checked)} />
                  <span>同期される場所であることを理解したうえで読み取りを開始する
                    （判定が誤っているときの確認です。次回また確認します）</span>
                </label>
              </div>)}
          </div>
        )}

        {/* 出力対象外の欄がある旨（issue #66 段4・FR-1.9）。テンプレート編集画面を
            見ない運用者に届く事故防止の最後の砦——実行して初めて「列が足りない」
            と気づくのを防ぐ。エラーではないので実行はブロックしない */}
        {!running && verify && verify.parsed && outputDisabledNotice(verify.outputDisabledCells) && (
          <div className="card warnbox" style={{ fontSize: 12.5 }}>
            {outputDisabledNotice(verify.outputDisabledCells)}
            枠・読み取りは維持されます（テンプレート編集画面でいつでも戻せます）。
          </div>
        )}

        {/* 認証キーが環境変数（平文）で使われている旨の常時警告（issue S-MB）。
            missing とは独立に出す——env は「実行はできるが平文で危険」、missing
            は「実行そのものができない」で意味が違うため同じカードに混ぜない */}
        {!running && verify && verify.parsed && credNotice(verify.cred, verify.envPresent) && (
          <div className="card warnbox" style={{ fontSize: 12.5 }}>
            <div>{credNotice(verify.cred, verify.envPresent)}</div>
            <button className="btn primary" style={{ width: "fit-content", marginTop: 8 }}
              onClick={pickCredentials} disabled={importing}>
              {importing ? "取り込み中…" : "認証キーを選択"}
            </button>
          </div>
        )}

        {/* はじめの準備（資格情報が無いときだけ） */}
        {!running && verify && verify.parsed && verify.cred === "missing" && (
          <div className="card" style={{ borderColor: "var(--warn-line)", background: "var(--warn-bg)" }}>
            <div style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#8a5a13"
                strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <circle cx="8" cy="15" r="4" />
                <path d="M11 12L21 2" /><path d="M17 6l3 3" /><path d="M14 9l2 2" /></svg>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <b style={{ color: "var(--warn-ink)", fontSize: 15 }}>初回設定: 読み取り用の認証キーを設定します</b>
                <div style={{ fontSize: 12.5, color: "#7a5a26", lineHeight: 1.7 }}>
                  管理者から受け取った<b>認証キーファイル（JSON）</b>を選択してください。
                  暗号化して保存し、元のファイルは取り込み後に削除します
                  （鍵が平文のまま残らないようにするためです）。
                </div>
                <button className="btn primary" style={{ width: "fit-content" }}
                  onClick={pickCredentials} disabled={importing}>
                  {importing ? "取り込み中…" : "認証キーを選択"}
                </button>
              </div>
            </div>
          </div>
        )}

        {notice && <div className="tipbox">{notice}</div>}

        {/* 手順 1〜3（完了後は「条件を変更して読み取る」で再表示） */}
        {!running && !summary && (
          <>
            {/* issue #72 (t)・FR-F27: テンプレート選択。番号付き手順（1〜3）とは
                別の補助カードとして置く（.step を使わない＝丸数字バッジが
                付かない・選ばなくても出荷テンプレートで実行できるため必須の
                手順ではない）。ころね（user_advocate）の初見ユーザー予測
                レビュー: 見出しと説明を「普段は触らなくてよい」ことが
                一目で分かる文言にする。選択は即座に config.last_template へ
                保存する（設計08 §3.5.3） */}
            <div className="card" style={{ background: "var(--bg)" }}>
              <div className="body">
                <div className="t">読み取りに使うテンプレート（通常はこのまま）</div>
                <div className="d">別の様式を読み取るときだけ変えてください。</div>
                <select value={selectedTemplate} aria-label="読み取りに使うテンプレート"
                  onChange={(e) => onSelectTemplate(e.target.value)}>
                  {templates.map((t) => (
                    <option key={`${t.kind}:${t.name}`}
                      value={formatLastTemplate(t.kind === "user" ? t : null)}>
                      {t.kind === "shipped" ? `${t.name}（出荷）` : t.name}
                    </option>
                  ))}
                </select>
                {templateNotice && <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>{templateNotice}</div>}
              </div>
            </div>
            <div className="card step on">
              <div className="no">1</div>
              <div className="body">
                <div className="t">読み取る帳票の選択</div>
                <div className="d">スキャン済み PDF のフォルダを指定してください。
                  PDF ファイル1つだけの場合は、この画面へドラッグ＆ドロップでも選べます。</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
                  <button className="btn outline" onClick={pickInput}>
                    <FolderIcon c="#2563eb" />フォルダを選ぶ
                  </button>
                  {inputDir
                    ? <div className="pathbox">{inputDir}</div>
                    : <span className="muted">未選択</span>}
                </div>
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">2</div>
              <div className="body">
                <div className="t">Excel の保存先の確認</div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div className="pathbox">{outputDir}</div>
                  {/* 設定を読めていないときは保存先を変更させない（issue N-3）。
                      pickOutput は write_config で config.json を書くが、
                      読めない設定を書き換えると他のキー（送信上限・workdir 等）の
                      扱いが不確かなまま保存操作だけが走る。設定モーダル側は
                      枠B で同じ理由で止めてあり、このボタンだけ抜けていた */}
                  <button className="btn" onClick={pickOutput} disabled={!!loadError}>変更</button>
                </div>
                {loadError && (
                  // issue Q-MF: 保存済みの設定を読み込めなかった場合、表示中の
                  // 保存先が既定値の可能性がある旨を伝える（黙って既定値を
                  // 出すと、実際の設定と違う場所だと気づけない）
                  <div className="muted" style={{ fontSize: 12, marginTop: 6 }}>
                    設定を読み込めませんでした（詳細: {loadError}）。
                    表示中の保存先は既定値の可能性があります。
                  </div>
                )}
              </div>
            </div>

            <div className={inputDir ? "card step on" : "card step"}>
              <div className="no">3</div>
              <div className="body">
                <button className="btn primary big" style={{ width: "fit-content" }}
                  onClick={start}
                  disabled={!inputDir || purging || (!!verify && !verify.parsed)
                    || verify?.cred === "missing"
                    || (!!verify && verify.budgetUsed >= verify.budgetCap)
                    || (!!verify && !verify.storage && !storageAck)}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="#ffffff">
                    <polygon points="6,4 20,12 6,20" /></svg>
                  読み取りを開始
                </button>
                {!inputDir && <span className="muted">読み取る帳票を選択すると実行できます</span>}
                {purging && <span className="muted">削除の完了までお待ちください</span>}
                {inputDir && !purging && startDisabledReason(inputDir, verify, storageAck) && (
                  <span className="muted">{startDisabledReason(inputDir, verify, storageAck)}</span>
                )}
              </div>
            </div>

            <div className="hintline">
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="#5a6577"
                strokeWidth="2" strokeLinecap="round"><circle cx="12" cy="12" r="9" />
                <path d="M2 12h20" /><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" /></svg>
              読み取りにはインターネット接続が必要です
            </div>
          </>
        )}

        {summary && failures.length > 0 && (
          <div className="card">
            {/* 件数は全件、一覧は先頭 FAILURE_KEEP 件まで（issue #53 L-17）。
                数千件の失敗でも DOM が伸び続けないようにする */}
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>
              処理できなかったページ（{failureTotal} 件）
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {failures.map((f, i) => (
                <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5,
                  alignItems: "baseline", borderTop: i ? "1px solid var(--line)" : "none",
                  paddingTop: i ? 6 : 0 }}>
                  <span style={{ fontFamily: "Consolas, monospace", color: "var(--sub)",
                    flexShrink: 0 }}>{f.page_id}</span>
                  <span>{STATUS_JA[f.status] ?? f.status}
                    {reasonCodeNotice(f.reason_code)
                      ? `（${reasonCodeNotice(f.reason_code)}）` : ""}</span>
                </div>
              ))}
            </div>
            {truncatedFailureNotice(failureTotal, failures.length) && (
              <div className="muted" style={{ fontSize: 12, marginTop: 8 }}>
                {truncatedFailureNotice(failureTotal, failures.length)}
                （すべての内訳は詳細ログにあります）
              </div>
            )}
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {log.length > 0 && (
          <details className="logbox">
            <summary>詳細ログ</summary>
            <pre ref={logRef}>{log.join("\n")}</pre>
          </details>
        )}

        {/* 読み取ったデータの削除（issue #52 M-11・S-MC の GUI 化）。
            要件 §6.3「削除は明示操作のみ」を満たすのは、①このボタン以外に
            削除が走る経路が無い（起動時・実行後の自動削除はしない）②押しても
            二段確認（何が消えて何が残るかの説明 → 最終確認）を通るまで何も
            消えない、の2点。読み取り中は押せない（コア側も PID スロットで
            二重起動を断るが、押せてしまうと理由が画面から分からない） */}
        <div className="card" style={{ background: "var(--bg)" }}>
          <div className="body">
            <div className="t">読み取ったデータの削除</div>
            <div className="d">読み取りの途中経過（個人情報を含みます）を削除します。
              提出が終わったバッチは削除してください。</div>
            <button className="btn" style={{ width: "fit-content" }}
              disabled={running || purging}
              onClick={() => { setPurgeIncludeOutput(false); setPurgeStep("explain"); }}>
              {purging ? "削除中…" : "読み取ったデータを削除"}
            </button>
          </div>
        </div>

        {/* 認証キーの取り込み前確認（issue #52 M-10）。元のファイルを消す
            操作を、押した本人に伝えないまま行わない */}
        {credConfirm && (
          <ConfirmDialog title="認証キーを取り込みます" confirmLabel="取り込む"
            onCancel={() => setCredConfirm(null)}
            onConfirm={() => { void importCredentials(credConfirm); }}>
            <p style={{ margin: "0 0 10px" }}>
              選んだ認証キーを暗号化して、この PC に保存します。</p>
            <p style={{ margin: 0 }}>
              <b>元のファイルは取り込み後に削除します</b>（鍵が平文のまま残らない
              ようにするためです）。削除できなかったときは、その旨を画面に出します。</p>
          </ConfirmDialog>
        )}

        {/* 削除の1段目: 何が消えて何が残るか（issue #52 M-11） */}
        {purgeStep === "explain" && (
          <ConfirmDialog title="読み取ったデータを削除します" confirmLabel="次へ"
            onCancel={() => setPurgeStep(null)}
            onConfirm={() => setPurgeStep("confirm")}>
            <p style={{ margin: "0 0 10px" }}>
              <b>消えるもの</b>: 読み取りの途中経過（取り込んだページの画像・
              読み取った値・位置合わせの結果）。個人情報はここに残っています。</p>
            <p style={{ margin: "0 0 10px" }}>
              <b>残るもの</b>: 認証キー・テンプレート・設定。認証キーを取り込み
              直す必要はありません。</p>
            <p style={{ margin: "0 0 10px" }}>
              削除すると、同じ帳票をもう一度読み取るときは最初から送信し直しに
              なります（API 送信＝課金が発生します）。</p>
            <label className="checkrow" style={{ marginBottom: 0 }}>
              <input type="checkbox" checked={purgeIncludeOutput}
                onChange={(e) => setPurgeIncludeOutput(e.target.checked)} />
              <span>出力した Excel・CSV も削除する（このツールが作った
                output_日時 のファイルだけが対象です。フォルダと、それ以外の
                ファイルは残します）</span>
            </label>
          </ConfirmDialog>
        )}

        {/* 削除の2段目: 最終確認（issue #52 M-11） */}
        {purgeStep === "confirm" && (
          <ConfirmDialog title="削除してよろしいですか" confirmLabel="削除する"
            danger busy={purging} onCancel={() => setPurgeStep(null)} onConfirm={runPurge}>
            <p style={{ margin: "0 0 10px" }}>
              読み取りの途中経過
              {purgeIncludeOutput ? "と、出力した Excel・CSV" : ""}
              を削除します。<b>元に戻せません。</b></p>
            <p style={{ margin: 0 }}>
              {purgeIncludeOutput
                ? "提出済みであること（出力ファイルが手元に不要なこと）を確認してください。"
                : "出力した Excel・CSV は残ります。"}</p>
          </ConfirmDialog>
        )}
      </div>
    </div>
  );
}
