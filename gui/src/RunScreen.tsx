// 実行画面（設計 §7.1・最小構成の6機能）。処理ロジックを持たず、
// コアの起動と JSON Lines 進捗（§7.3）の表示に徹する。
// UI はデザインカンバス「帳票OCRツール GUI」準拠: 番号つき手順・平易な言葉。
import { invoke, isTauri } from "./bridge";
import { listen } from "./bridge";
import type { UnlistenFn } from "@tauri-apps/api/event";
import { getCurrentWindow, currentMonitor } from "@tauri-apps/api/window";
import { LogicalSize } from "@tauri-apps/api/dpi";
import { useEffect, useRef, useState } from "react";

type Summary = {
  pages: number; rows: number; align_failed: number;
  api_calls: number; unclear_cells: number; overflow: number;
  // 様式不一致で失敗したページ数（枠D で pipeline.py が追加したキー。
  // 旧コアでは undefined・issue N-1）
  format_mismatch?: number;
  risky_cells?: number;  // CSV を Excel で直接開くと数式化しうるセル数（D-28）
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
type Failure = { page_id: string; status: string };

// ウィンドウサイズ運用（起動時は小窓・完了サマリ表示で縦に自動拡大）。
// 幅は常に730固定——tauri.conf.json の windows[0].width と一致させる。
export const RUN_WINDOW_WIDTH = 730;

// 実行前の最大状態（Playwright 実測: フォルダ未選択時の手順3ヒント文まで
// 込みで appbar 65px + run-screen 547px = 612px。folder選択後はヒント文が
// 消えて逆に低くなるため、未選択時の方が実は高い）に、実機WebView2と
// 計測に使ったヘッドレスChromiumのフォント描画差を吸収する安全マージン
// 8pxを足した値。tauri.conf.json の windows[0].height と同じ値を保つこと
// （既定サイズの正本は tauri.conf.json 側。ここでは resize 判定の
// 下限値として参照する）。
export const RUN_WINDOW_HEIGHT_DEFAULT = 620;

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
  "超過あり": "記入が定義済みの行数を超えています",
};

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
  // 削除の導線は GUI に無い（purge は ALLOWED_SUBCOMMANDS 外・要件 §6.3
  // 「削除は明示操作のみ」）。「purge で削除してください」だけだと画面上の
  // どこかにボタンがあるように読めるため、コマンドである旨を書く（issue N-6）
  return `中間データに ${pages} ページ蓄積しています${suffix}。`
    + `提出済みのバッチはコマンド（purge --yes）で削除してください。`;
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
    case "summary":
    case "remap_summary": {
      // P-H1: counterNotice（欠落〓の出所）の隣に accumulationNotice（中間
      // データの累積警告）を添える。どちらも null なら通知自体を出さない
      const parts = [counterNotice(ev), accumulationNotice(ev)]
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
  const interruptedRef = useRef(false);
  const refusedRef = useRef(false);
  // 終了時の文言判定（completionNotice）で使う最新のサマリ。state の方は
  // start() のクロージャが古い値を掴むため、interruptedRef と同じ流儀で
  // ref にも持つ（issue N-1）
  const summaryRef = useRef<Summary | null>(null);
  const [notice, setNotice] = useState("");
  const [notices, setNotices] = useState<string[]>([]);  // 実行時の警告（M-2・#28）
  const [refused, setRefused] = useState("");  // 業務的な拒否（H-C）
  const [loadError, setLoadError] = useState("");  // 設定読み込み失敗（issue Q-MF）
  const logRef = useRef<HTMLPreElement>(null);
  const screenRef = useRef<HTMLDivElement>(null);

  const runVerify = async () => {
    try {
      setVerify(parseVerify(await invoke<string>("run_core_capture", { args: ["verify"] })));
    } catch (e) {
      setVerify(parseVerify(String(e)));  // verify は不備時に終了コード1で stdout ごと届く
    }
  };
  useEffect(() => { runVerify(); }, []);

  const importCredentials = async () => {
    // 認証キーは白リストへ登録しない（登録すると鍵の平文 JSON が
    // セッション中ずっと read_text で読める・レビュー4巡目）
    const p = await invoke<string | null>("pick_json",
      { save: false, rememberPick: false });
    if (!p) return;
    setImporting(true);
    try {
      await invoke<string>("run_core_capture", { args: ["import-credentials", p] });
      setNotice("認証キーを暗号化して保存しました。元のファイルは削除して構いません。");
      await runVerify();
    } catch (e) {
      setError(`認証キーの取り込みに失敗しました: ${e}`);
    } finally {
      setImporting(false);
    }
  };

  useEffect(() => {
    // 設定モーダルで保存されたら読み直す（M-3: 変更後も古いパスを表示し、
    // 「出力フォルダを開く」が別の場所を開いていた）。
    // 読み込み失敗を握りつぶさない（issue Q-MF）——出力先が既定値（"output"）
    // のまま表示され、実際の設定と食い違っていることに気づけなかった
    invoke<Record<string, unknown>>("read_config").then((c) => {
      if (typeof c.output_dir === "string") setOutputDir(c.output_dir);
      setLoadError("");
    }).catch((e) => setLoadError(String(e)));
  }, [configRev]);

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [log]);

  // ウィンドウの縦幅を実行画面の状態に揃える（起動時の余白なし・完了サマリ
  // でのスクロールなしの両立・ユーザー承認済み 2026-09-01）。
  //   - サマリ未表示（起動直後・入力待ち・処理中）: 既定の小窓に揃える
  //   - サマリ表示中: 本文の実測高から必要な高さを求め、縦だけ拡大する
  // タブ切替のたびに規定サイズへ揃える方針のため、この画面がアクティブに
  // なった瞬間（active）と、サマリの有無が変わった瞬間（summary）の両方で
  // 発火させる——手動リサイズの保持はしない。ブラウザのデモモードでは
  // window API が無いため isTauri で no-op にする（bridge.ts と同じ流儀）。
  //
  // running を deps に追加し、running===false のときだけ計測する（issue
  // Q-MD）。完了直後は「summary が入る」「running が false になる」の2つの
  // 状態更新がほぼ同時に起き、どちらも deps 変化として effect を再発火
  // させるため、旧実装（deps=[active, summary]）は setSize を2回呼びかねな
  // かった。running===true の間は何もしない（実行中に options が動くのは
  // 望ましくない）うえ、実際の計測は rAF 1回分だけ遅らせてまとめる——
  // ResizeObserver は setSize との相互発火（発振）を招くため使わない。
  useEffect(() => {
    if (!isTauri || !active || running) return;
    const raf = requestAnimationFrame(() => {
      (async () => {
        const win = getCurrentWindow();
        if (!summary) {
          await win.setSize(new LogicalSize(RUN_WINDOW_WIDTH, RUN_WINDOW_HEIGHT_DEFAULT));
          return;
        }
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
  }, [active, summary, running]);

  useEffect(() => {
    const subs: Promise<UnlistenFn>[] = [
      listen<string>("core-line", (e) => {
        setLog((l) => [...l.slice(-400), e.payload]);
        try {
          const ev = JSON.parse(e.payload);
          if (ev.event === "start") { setTotal(ev.todo ?? ev.total ?? 0); setDone(0); }
          if (ev.event === "page") {
            setDone((d) => d + 1);
            if (ev.status && ev.status !== "done") {
              setFailures((f) => [...f, { page_id: ev.page_id, status: ev.status }]);
            }
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
      listen<string>("core-err", (e) =>
        setLog((l) => [...l.slice(-400), `[err] ${e.payload}`])),
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
  useEffect(() => { activeRef.current = active; }, [active]);
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
    setLog([]); setDone(0); setTotal(0); setFailures([]); setNotices([]);
    setRefused("");
    interruptedRef.current = false; refusedRef.current = false;
    summaryRef.current = null;
    try {
      const code = await invoke<number>("run_core", { args: ["run", "--input", inputDir] });
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

  return (
    <div className="run-screen" ref={screenRef}>
      {dropping && (
        <div className="dropzone-overlay">
          ここにドロップすると読み取り対象になります（フォルダ・PDF ファイルどちらでも）
        </div>
      )}
      <div className="run-main">

        {/* 完了バナー */}
        {summary && (
          <div className="banner ok">
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#16a34a"
              strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="10" /><path d="M8 12.5l3 3 5-6" />
            </svg>
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
            {notices.length > 0 && (
              <div className="card warnbox">
                <b>実行時のお知らせ</b>
                {notices.map((t, i) => <div key={i}>{t}</div>)}
              </div>
            )}
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
          </>
        )}

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
                <b>この状態では読み取りを開始できません</b>。設定でローカルの
                フォルダへ変更してください。</div>)}
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
              onClick={importCredentials} disabled={importing}>
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
                  暗号化して保存され、元のファイルは以後不要です。
                </div>
                <button className="btn primary" style={{ width: "fit-content" }}
                  onClick={importCredentials} disabled={importing}>
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
                  disabled={!inputDir || (!!verify && !verify.parsed)
                    || verify?.cred === "missing"
                    || (!!verify && verify.budgetUsed >= verify.budgetCap)
                    || (!!verify && !verify.storage)}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="#ffffff">
                    <polygon points="6,4 20,12 6,20" /></svg>
                  読み取りを開始
                </button>
                {!inputDir && <span className="muted">読み取る帳票を選択すると実行できます</span>}
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
            <div style={{ fontSize: 13.5, fontWeight: 700, marginBottom: 10 }}>
              処理できなかったページ（{failures.length} 件）
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {failures.map((f, i) => (
                <div key={i} style={{ display: "flex", gap: 10, fontSize: 12.5,
                  alignItems: "baseline", borderTop: i ? "1px solid var(--line)" : "none",
                  paddingTop: i ? 6 : 0 }}>
                  <span style={{ fontFamily: "Consolas, monospace", color: "var(--sub)",
                    flexShrink: 0 }}>{f.page_id}</span>
                  <span>{STATUS_JA[f.status] ?? f.status}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {error && <div className="error">{error}</div>}

        {log.length > 0 && (
          <details className="logbox">
            <summary>詳細ログ</summary>
            <pre ref={logRef}>{log.join("\n")}</pre>
          </details>
        )}
      </div>
    </div>
  );
}
