# テスト要件書 — 帳票OCRツール v1

| 項目 | 内容 |
|---|---|
| 版 | v1.4（2026-09-04・テンプレート編集の初回読み込み＝候補優先フローの反映: 基準値を 861/355/135/tsc/core-dist へ・GUI スモーク 26・`test_editor_open_config.py` 58 件を追加。v1.3=2026-09-04・issue 一括消化ラウンドの反映: §3 の表を実測へ追従・新設 10 スイート・回帰ゲート基準値を 815/341/131/tsc/core-dist へ更新。v1.2=2026-09-01・issue #64 の一括更新: §2/§3 の件数を実測405件へ是正し5巡目〜#66 の新設21スイートを掲載、回帰ゲート基準値の正本を §3 に置いた。v1.1=2026-08-31・v1.0=2026-08-28） |
| 正本 | 本書（テスト要件・トレーサビリティ）。要件は 01_requirements.md、設計は 02_design.md。**回帰ゲート基準値の管理先は §3**（05 P4-1・要件側で二重管理しない） |
| 実行方法 | `python scripts/run_all_tests.py`（pytest 全体＋gui logic＋tsc 型検査＋cargo test＋core-dist 鮮度検査＋集計）。GitHub 側では `.github/workflows/ci.yml` が同じ範囲を回す（issue #95） |

## 1. テスト方針

設計書 §12 の方針「テストは背骨に寄せる」を維持する。網羅率を目標にせず、**壊れると出力の信頼が崩れる不変条件**をテストで固定する:

1. **配置の正しさ**（§8 第1層）— 値が正しい列・正しい行に置かれること。1件でも誤配置なら不合格
2. **転記主義** — 低信頼値が出力に残らない（〓化）。推測で埋めない
3. **行数保存** — どの失敗経路でも入力ページ数＝出力行数
4. **再現性・再開** — 同一入力で同一出力、中断後は未処理分のみ送信
5. **漏出防止** — 記入値がログ・stderr・例外メッセージへ出ない

テストは**実 API を一切呼ばない**。Vision 応答は `workdir/responses/`・`testdata/local/s2/` の保存済み実応答を `ReplayClient` で再生する（課金ゼロ・ネットワーク不要・決定論）。

## 2. テストレベル

| レベル | 定義 | 実行 |
|---|---|---|
| L1 自動 | `run_all_tests.py` が無人で完走・合否判定する | 毎コミット前 |
| L2 半自動 | スクリプトはあるが環境準備（dev サーバー・release exe）が要る | リリース前 |
| L3 手動 | 人の操作・外部環境が必要 | 節目のみ（§6） |
| CI | `.github/workflows/ci.yml` が GitHub 上で回す | PR ごと（軽量）／main への push（フル） |

- L1: pytest 889 件（collected。GUI スモーク 26 件は dev サーバー無しでは自動 skip・symlink 権限の 2 件も skip）＋ gui-logic 355 件＋ cargo test 135 件＋ tsc（型検査）＋ core-dist 鮮度検査。いずれも `run_all_tests.py` が一括実行する（件数は 2026-09-04 実測・`run_all_tests.py` の集計行）
- L2: GUI スモーク（`npm run dev` 起動下で L1 に合流）／release exe の CDP 検証（issue #5/#6/#7 の実機確認・scripts 化はレビュー時に都度）。**リポジトリのチェックアウト内で release exe を起動すると、`.git` と `.venv` があるため GUI は同梱 exe ではなく `.venv` のコアを動かす**（2026-09-02 の `resolve_core_program`）。同梱 exe 込みで検証するときは環境変数 `CHOUHYO_CORE=bundled` を付けて起動する。付け忘れると「release exe を検証した」が実質 venv の検証になる
- L3: §6 の残項目
- **道具が無い環境の扱い（2026-09-03・issue #95）**: node / cargo が入っていない環境で GUI・Rust のテストを SKIP 扱いにし、集計の合否に効かせないままにしていた。テストが1件も走っていないのに `SUMMARY: PASS` が出る状態だったため **FAIL** に変えた。素材が無くて個々のテストが skip するのは従来どおり許し、skip 率ガード（skip が passed の 10% 超で FAIL）で受け止める
- **CI（`.github/workflows/ci.yml`）の判定が L1 と違う点**: CI は `run_all_tests.py` を呼ばず pytest を直接呼び、「skip は許すが failed は 0」で判定する。テストの約 38% が `.gitignore` 済みの `testdata/local/pages/sample-1.png`・`testdata/local/s2/resp_*.json` を素材にしており、素材の無い CI ではランナーの skip 率ガードが必ず FAIL になるため。skip 件数は `-rs` でログに出す。`core-dist` 鮮度検査は CI では行わない（`core-dist/` は gitignore 済みで比較相手が無い）。PDF 展開のテストのために Poppler 26.02.0 を sha256 照合のうえ `vendor/poppler/` へ展開する
- **CI を Windows に寄せている理由**: core は Windows 専用で、`cred_store.py` が `ctypes.wintypes` を import し（Linux では import 自体が失敗する）、`cli.py` がそれを読み込む。加えて core/tests の 6 ファイルが `.venv/Scripts/python.exe` を直接起動し、`test_response_robustness.py` は msvcrt のファイルロックを使う。pytest を ubuntu へ割るには core/tests 側の改修が要る（2026-09-03 調査）

## 3. テストスイート一覧（L1）

件数は 2026-09-03 実測（`cd core && ..\.venv\Scripts\python.exe -m pytest --collect-only -q tests` → 752 collected をファイル別に集計）。GUI スモーク 14 件は dev サーバー未起動なら skip する。#75（枠のスナップ）のテストは別レーンで追加中のため未掲載。

**素材が要るテストの置き場（2026-09-03・#88）**: 実サンプル（`testdata/local/pages/sample-1.png`・`sample-2.png`）と保存済み Vision 応答（`testdata/local/s2/resp_*.json`）、golden 出力（`testdata/local/golden/<head_short>/`）は `testdata/local/`（`.gitignore` 済み・git 管理外）に置く。以前は `workdir/` 配下にあり、中間データと同居していたため `purge --yes` を一度実行すると golden 比較（AC-F45）と性能計測が再現できなくなっていた。素材を持たない環境（CI・別マシン）では該当テストが skip する——この 32 ファイルが「素材が無いと skip する」母集団。

| ファイル | 件数 | 対象 |
|---|---|---|
| core/tests/test_review_fixes.py | 43 | レビュー指摘の再発防止（issue #11/#13/#14/#19: normalize 属性・glob エスケープ・config 検証・単一ファイル入力・expand-page）＋ `verify --expect-columns` の列数ゲート3方向検証（#65-2 の緑偽装置換）・同期フォルダ判定の誤検知（#53 L-19） |
| core/tests/test_cred_config_atomicity.py | 38 | 資格情報と config の検証・原子的書き込み（#97・#52 M-10/M-11・#53 L-15: service_account 形の検証・tmp+replace・破損 blob を verify が ok:false にする・`cred.dpapi` の `%LOCALAPPDATA%\ChouhyoOCR\` 分離と旧置き場からの移行・`--delete-source` のランダム上書き→削除・config.json 破損時に error.log を案内しない） |
| core/tests/test_field_anchor.py | 35 | 表を持たない面を欄の枠線で位置合わせ（#86・決定 T1〜T13: 表のある面が欄経路へ入らないこと・出荷テンプレの推定不変・ALGO_VERSION 据え置き・1 ピッチずれの拒否・アンカー 0 本／水平のみの拒否・探索上限の render_dpi 比例） |
| core/tests/test_normalize.py | 30 | 金額 D-01・複合セル分割 D-23 |
| core/tests/test_template.py | 25 | テンプレート読込・v1 受入範囲・格子展開（列導出の前提。現行テンプレートの導出結果は220列） |
| core/tests/test_detect_frames.py | 25 | `grid.detect_frames`／`detect-frames`（#73 (b)・08 §4・AC-F16〜F18・NFR-F02。レビュー H-1〜M-6 反映: 罫線を共有する左右2ブロックが1表に融合しないこと・`stats.components`／`excluded` の内訳突合・残差 `residual_px` と `not_closed` の計上 #85） |
| core/tests/test_response_robustness.py | 24 | 応答異常・出力の原子性・多重起動・対象外入力の可視化（#35〜#40・M-2）＋ 保存と状態更新の間で落ちても再送しないこと・部分書き込みは再送・送信画像の sha256 不一致で再送（#92） |
| core/tests/test_format_check.py | 24 | 様式判定の3値化（#71 (a')・08 §2.3/§2.9・AC-F05/F06/F15: 理由コード対応表・片面 mismatch の畳み込み・面切りを関数の内側で行うこと） |
| core/tests/test_char_level_unclear.py | 20 | 文字単位〓（#62・U-10〜U-13・判定表 T-12〜T-21） |
| core/tests/test_local_storage_guard.py | 19 | 同期フォルダ検知（issue #8・`debug-images --out` の検査 #59 H-5 を含む） |
| core/tests/test_fallback.py | 18 | 参照先（fallback_rect）の採否3分岐・由来印（#54・U-02〜U-04） |
| core/tests/test_e2e_replay.py | 18 | run→xlsx/csv 貫通・配置検証・バイト一致再現性 |
| core/tests/test_dpi_scaling.py | 16 | px 定数の dpi 正規化（汎用化 A-3: `mapping._LINE_GAP`・`mapping._BUCKET`・`align.COARSE_DILATE`・`grid.ROW_INSET` が render_dpi 可変でも成り立つこと） |
| core/tests/test_render_status.py | 15 | render 段の失敗ステータス配線（#80・T-20〜T-29: `row_build_failed` / `row_build_bug` を「出力失敗」として page.status と進捗イベントへ残す。T-24＝失敗しても `page.state` は done のまま保ち、次の run での再送＝二重課金を防ぐ） |
| core/tests/test_union_regions.py | 14 | 複数領域（L字・コの字）の連結順「領域→帯→行→x」（#57・U-06） |
| core/tests/test_render_rows.py | 14 | セル3状態・〓判定・ステータス合成・制御文字〓化 |
| core/tests/test_purge_output.py | 14 | `purge` の削除範囲（#83: 資格情報 `cred.dpapi` の保持・keep-list・部分失敗の続行と件数・workdir 自体と配下のジャンクション／symlink の扱い・読み取り専用の再試行）と `--include-output` の命名一致削除。file symlink は権限不足時 skip 1 件 |
| core/tests/test_gui_smoke.py | 14 | GUI 導線（Playwright・デモモック。dev サーバー無しは skip）。寸法不一致→新規テンプレート→生成→採用→保存の通し・実行タブ表示中の Delete 無効化（#69）・画像なし初期表示のピクセル検査。待ちは固定 sleep をやめ `expect` と `SMOKE_TIMEOUT_MS` に一本化（#79・2026-09-03） |
| core/tests/test_review4_io.py | 13 | 4巡目の入出力修正（#51/#47/#52 M-4）＋ 応答内エラーの gRPC ステータス分類（#99: 決定的エラー 3/5/7/9/12/16 は 1 ユニットで停止・一時エラーは再試行・例外は型名と `grpc_status_code` の二段構え） |
| core/tests/test_render_out_unclear.py | 13 | 由来色・部分〓の条件付き書式・COUNTIF の `unclear_char_level` ゲート（U-04/U-12/U-13） |
| core/tests/test_output_columns_stage2.py | 13 | 段2: 対象外欄の母集団維持・W 警告の（出力対象外）印・カウンタの run/remap 両配線（AC-1.4 系） |
| core/tests/test_output_columns_stage1.py | 13 | 出力列制御 段1: `output` 属性・列導出3経路・抽出列0拒否（#66・AC-1.1 系・§8-16 後半） |
| core/tests/test_dist_stamp.py | 13 | core-dist 鮮度検査（`BUILD_STAMP.json` の内容ハッシュ比較・SKIP/PASS/FAIL 判定・追加/削除/サブディレクトリ検出・対象外ファイルの非検出・スタンプ破損時の FAIL・2026-09-02） |
| core/tests/test_align_residual.py | 13 | 位置合わせ残差の記録（#74 段 (c)・08 §5.7: 残差の算出・非対称 2 ブロックでの分離・失敗面の未計測・`alignment` 列と旧 DB 互換・`align_residual` ログの `face_idx` のみ・境界値 6 件。件数は parametrize 展開後） |
| core/tests/test_segments.py | 11 | 端点付き線分抽出 `detect_segments`（#73 (b)・AC-F55。`projection.py` を参照しないため既存の位置合わせの検出条件に影響しないことが前提） |
| core/tests/test_review4_pipeline.py | 11 | 位置合わせの再利用（#45）・入力ファイル改名時の行数保存（#46） |
| core/tests/test_response_atomicity.py | 11 | 応答保存の原子性と入力画像ハッシュの紐づけ（#92: tmp+replace・「応答は残っているのに state=sending」のページを received へ復旧して再送しない・サイドカーの sha256 が合わなければ再送） |
| core/tests/test_paths.py | 11 | `paths.user_templates_dir()`（#72 (t)・08 §3.1: `CHOUHYO_USER_DIR` で受け取った値の検証だけを行い、列挙と reparse point 検査は Rust 側の1箇所に集約する契約） |
| core/tests/test_diag_overflow.py | 11 | `diag-overflow` の数え方（#63: 主枠に部分記入＋右隣へ溢れた候補の判定・欄の高さと倍率から決まる帯・合成 token のみで API 送信ゼロ・CLI が候補1行＋サマリを出すこと） |
| core/tests/test_debug_images.py | 11 | debug-images の判定共通化・テンプレート不一致の拒否ゲート（#60 M-1・U-15）＋ 由来印を DB の `cell.origin` から描くこと（#65-6）。実サンプル素材が無い環境では skip |
| core/tests/test_api_budget.py | 11 | API 送信ユニットの月次上限（強制停止）＋ カウンタの堅牢化（#91: 破損時は退避してログ・無音の 0 リセットをやめる・tmp 名をプロセス固有に・保存失敗時は前回値を残す） |
| core/tests/test_review6_pipeline_low.py | 10 | 6巡目 LOW 群（#53 L-5/L-9/L-10・#65-4/#65-9: `taken` の更新・終了コードの母集団・空リストの SQL・配布版 CLI で利用者テンプレートに `--template` 明示を要求・`upsert_cell_extras` の rowcount 検証） |
| core/tests/test_mapping.py | 10 | symbol 割付・行クラスタ・除外領域（実応答回帰）＋ 面の索引をテンプレートごとに1回だけ構築すること（#53 L-18） |
| core/tests/test_format_check_shared_prep.py | 10 | 様式判定の前処理を候補間で共有しても判定が変わらないこと（#82・08 §3.3.4: `align._face_estimate` と同一の `ShiftEstimate`・素材×テンプレートの全組み合わせで `PageVerdict` 一致・同一幾何での使い回し・キャッシュ上限と寸法不一致の扱い） |
| core/tests/test_page_size_guard.py | 9 | 入力ページの寸法検査（Q-H1: 縦5%伸ばした入力を `align_page` が無検証で resize して「一致」と誤判定していた事故の再発防止） |
| core/tests/test_match_templates.py | 9 | `match-templates` サブコマンド（#72 (t)・08 §3.3: 候補は `--candidate` で受け取り、自身はディレクトリ列挙をしない） |
| core/tests/test_leak_guards.py | 9 | 漏出防止の再発防止（issue #2/#3/#4）・detect-frames 経路の無漏出 |
| core/tests/test_exclusion_warnings.py | 8 | 除外領域×受け皿の重なり警告 W-1/W-2（U-09・H-6） |
| core/tests/test_columns.py | 8 | 列導出（出荷テンプレの現在値220列・内訳・field_id 一意）。拒否は列名重複と抽出列0（FR-1.3）のみ |
| core/tests/test_adjacent_gap_warnings.py | 8 | 受け皿間の隙間（死角）警告 W-3（#61 L-4） |
| core/tests/test_store_extras.py | 7 | cell.char_confs / cell.origin の追加列とマイグレーション（§10） |
| core/tests/test_review4_ingest.py | 7 | PDF 展開の高速化が出力を変えていないことの回帰（#50） |
| core/tests/test_era_band.py | 7 | 元号スコアの測定帯が隣の欄へ食い込まないこと（#52 M-1: `cell.rect`＋slack 4px でクランプ・帯幅は欄の高さと倍率に追随・合成画像だけで完結。実帳票 8/8 の較正は test_era_calibration.py 側） |
| core/tests/test_output_columns_stage7.py | 6 | 段7: 並べ替え3閉区間・座標不変（AC-2.x） |
| core/tests/test_hole_overlap_warnings.py | 6 | 切り抜き穴どうしの重なり警告 W-4（#66 第2弾 段6） |
| core/tests/test_format_check_pipeline.py | 6 | 様式判定の pipeline 配線（#71 (a')・08 §2.4/§2.5・AC-F01/F12/F13/F14: 別様式は API 0回・一部不一致でも全ページ分の行が出る・判定関数の例外が様式不一致に化けない） |
| core/tests/test_acceptance_gaps.py | 6 | 受入 Gap（TR-G1〜G6・§5） |
| core/tests/test_unclear_char_level_config.py | 5 | `unclear_char_level` 設定の検証・既定 OFF（U-14） |
| core/tests/test_runlock_transactions.py | 5 | remap のロック取得と1ページ更新の原子性（#93: remap も RunLock を取り remap→render をロック保持のまま一体化・`Store.transaction` で cell／拡張列／era_score／template_hash／unassigned を単一トランザクションに・busy_timeout=5000） |
| core/tests/test_reuse_guards.py | 5 | 中間データ再利用の歯止め・重複行（#25/#29 B-2）|
| core/tests/test_resume_cap.py | 5 | 再開規則・送信上限（§8-6/7）＋ 中断した sending ページに応答が残っていれば再送しないこと（#92） |
| core/tests/test_output_columns_stage8.py | 5 | 段8: 列名一覧ファイル・列順報告（FR-2.7・AC-2.10） |
| core/tests/test_ingest_arg_safety.py | 5 | pdftoppm/pdfinfo へ渡すパス引数の先頭 `-` 対策（L-S2・CWE-88: `ingest._safe_path_arg` の単体と、実際に subprocess へ渡る args の両方で固定） |
| core/tests/test_grid.py | 5 | 枠候補生成（罫線検出・等分割、§8-16/17 の土台） |
| core/tests/test_exclusion_guard.py | 5 | 除外領域の後退検知・verify の exclusions 出力（#55・#59 H-8） |
| core/tests/test_credentials_warn.py | 5 | 環境変数の平文鍵（`GOOGLE_APPLICATION_CREDENTIALS`）を verify が警告すること（#69 S-MB: 実行可否は変えず、dpapi と併存して state が畳まれる場合も残置が見えること） |
| core/tests/test_output_columns_stage4.py | 4 | 段4: debug-images の対象外表示・verify `output_disabled_cells`（FR-1.9） |
| core/tests/test_output_columns_stage0.py | 4 | 段0: verify `column_names`・保存時差分の母集団是正（AC-0.x） |
| core/tests/test_alignment_robustness.py | 4 | 位置合わせ頑健性（回転・平行移動・#30）|
| core/tests/test_era_calibration.py | 3 | 丸印判定の較正（#23・8箇所全問正解と閾値への余裕）|
| core/tests/test_duplicate_source.py | 2 | 二重取り込み検知 |
| core/tests/test_process_interrupt.py | 1 | 実プロセス強制終了→再開（C10 の自動化可能部分） |
| core/tests/test_output_columns_ac118_equivalence.py | 1 | AC-1.18: JSON 直接編集と画面経由の run 出力一致 |
| core/tests/test_charset.py | 1 | 異体字・サロゲートペア保持（§6.4） |
| core/tests/conftest.py | 0（fixture のみ） | テスト全体を実行環境の `%LOCALAPPDATA%` から隔離する（#52 M-6/M-11）。`api_usage.json` と `cred.dpapi` を使い捨てディレクトリへ向け、開発機の本物のカウンタ・資格情報を読み書きさせない |
| gui/tests/gui-logic.test.mjs（node 直実行） | 336 | GUI 純関数ロジック（保存前確認の警告合成・列数比較 `columnDecreaseFor`・カウンタ通知 `counterNotice`・出力列タブ・画像なし初期表示の `noImageNotice`／`canvasInteractionAllowed`・候補破棄と履歴 `clearCandidates`／`pushHistory`（#87 AC-F20/F21）・判定不能な面の弱い描画（#81 AC-F11）・実行 ID による core-line/core-err の絞り込み（#96）ほか） |
| gui/src-tauri（cargo test） | 131 | サブコマンド白リスト（issue #7）・コア実体の選択規則 `resolve_core_program`（2026-09-02）・テンプレート保存の原子化と古い `.bak` の掃除・staged への reparse point／ハードリンク経由の書き込み拒否・実行 ID の採番（プロセス内で一意・スレッド跨ぎでも重複なし・#96）・`SystemRoot` 由来のプログラムパス解決（#89/#90）ほか GUI 境界 |

**回帰ゲート基準値（正本・05 P4-1 の管理先）**: `python scripts/run_all_tests.py` → `SUMMARY: PASS / pytest: PASS (861 passed, 28 skipped, 184.2s) | gui logic: PASS (355 passed, 0.4s) | cargo test: PASS (135 passed, 4.7s) | tsc: PASS (型エラーなし, 12.9s) | core-dist: PASS (built_at 2026-09-04T03:39:05+00:00 と一致) / total 202.2s`（実行日 2026-09-04・テンプレート編集の初回読み込み＝候補優先フロー（FR-F51〜F55・AC-F67〜F89・`test_editor_open_config.py` 58 件・GUI スモーク +12）の後。dev サーバー未起動のため GUI スモーク 26 件は skip（同日、`npm run dev` 起動下で逐次 `pytest tests/test_gui_smoke.py -q -p no:randomly` → **26 passed, 31.2s**）。skip の残り 2 件は symlink 権限のテスト。直前の基準（同日午前・issue 一括消化ラウンド直後）は `pytest 815 / 16 skipped・gui 341・cargo 131・tsc PASS・core-dist PASS (built_at 2026-09-04T00:44:31+00:00) / total 202.7s`（issue 一括消化ラウンド（#75 (f) ブロック吸着・#86 欄アンカー・#80 出力失敗・#92/#97/#91/#99 の課金と資格情報・#93 排他・#98/#94/#95 のゲート強化・#88 素材移設ほか）の後。dev サーバー未起動のため GUI スモーク 14 件は skip（同日、`npm run dev` 起動下で逐次 `pytest tests/test_gui_smoke.py -q -p no:randomly` → **14 passed, 22.2s**・QA 独立実走）。skip の残り 2 件はファイル symlink の作成に管理者権限が要るテスト（`test_paths.py`・`test_purge_output.py`）。dev サーバー起動下なら 829 passed / 2 skipped が同値（計算値）。直前の基準（2026-09-03・段 (c) 直後）は `pytest 585 / gui 252 / cargo 109 / core-dist PASS (built_at 2026-09-03T08:17:16+00:00) / total 402.2s`（#83 の purge 修正と枠判定の自動化 段 (c)（`test_purge_output.py` 14・`test_align_residual.py` 13。skip 2 件はファイル symlink の作成に管理者権限が要るテスト＝`test_paths.py`・`test_purge_output.py`。cargo の 96 秒は再ビルド込み）の後。段 (b) 直後（同日午後）は 566 / 252 / 109（`test_segments.py` 11・`test_segments.py` 11・`test_detect_frames.py` 19・`test_leak_guards.py` に detect-frames 経路・gui-logic 252・スモーク 14＝寸法不一致→新規テンプレート→生成→採用→保存の通し・cargo 109＝`detect-frames` 白リスト）と初見ユーザー確認の反映（片面テンプレート）の後。段 (t) 直後（2026-09-02）は 534 / 210 / 106（枠判定の自動化 段 (t)（`test_match_templates.py`・`test_paths.py`・gui-logic 210・スモーク 12・cargo 106）と実機の通し確認の反映（summary.reused_pages）の後。skip 1 件はファイル symlink の作成に管理者権限が要るテスト（junction 版は実走）。段 (a') 直後は 505 / 173 / 61。同日午前の 471 / 148 / 61 は同梱 exe 陳腐化対策と編集画面の画像なし初期表示の直後・pytest-xdist `-n auto`。dev サーバー起動下で実走したため GUI スモーク 14 件も passed に含まれる。未起動なら 552 passed / 15 skipped が同値（計算値・段 (t) 時点の実測は 522 / 13）。並列エージェント作業中の負荷で Playwright が 1 件タイムアウトしていた件（#79）は2026-09-03 に解消した——固定 sleep と「今この瞬間」を読む assert を状態変化を待つ `expect` へ置き換え、待ち時間の上限を `SMOKE_TIMEOUT_MS`（既定 45 秒・環境変数 `CHOUHYO_SMOKE_TIMEOUT_MS` で上書き）に一本化した（素の Playwright は操作 30 秒・expect 5 秒とばらばらで、expect 側が先に切れていた）。確認: `pytest tests/test_gui_smoke.py -q` を dev サーバー起動下で 3 回連続 → 14 passed（48.2s / 26.0s / 29.0s）、さらに CPU 8 並列のビジーループを回した状態で 14 passed（50.9s）・実行日 2026-09-03。参考: 同日午前のレビュー7巡目（#69）対応後は 451 passed / 6 skipped・gui 145・cargo 47、2026-09-01 は 421 / 115 / 18。並列化前の直列実測は 400 passed / total 490.7s）。**tsc（型検査）を 2026-09-03 に追加したため、サマリは `tsc: PASS/FAIL` の項目が1つ増えている**（`SUMMARY: ... | tsc: PASS (型エラーなし, 10.8s) | core-dist: ...`。実測 2026-09-03・型エラー 0）。tsc が FAIL ならゲート全体を FAIL にする（判定の形が pytest/cargo と違うため core-dist と同様に個別扱いで、件数集計には乗せない）。`--coverage` を付けたときの `coverage: NN%` 行は測定値を並べるだけで合否には効かない（閾値が未定のため・下記）。上の件数は 2026-09-04 の再測値（issue 一括消化ラウンドの反映後）。判定は**全件 passed かつ passed 件数がこの基準値以上**——skip の増加で件数が保たれる場合を弾くため、件数のみでは判定しない（05 T-S5）。4項目目の `core-dist` は同梱 exe の鮮度で、`build_dist.py` が書く `BUILD_STAMP.json` と core/・schema/・出荷テンプレートの内容ハッシュを比べる。SKIP は core-dist 未ビルド（開発チェックアウトでは GUI が `.venv` のコアを起動するため任意）、FAIL は再ビルド漏れで、FAIL ならゲート全体を FAIL にする（2026-09-02: 同梱 exe が 8/31 ビルドのまま 17 commit 放置され、編集画面の PDF 展開が `--no-mask` の argparse エラーで落ちた事故の再発防止）。skip 28 件のうち 26 件は dev サーバー未起動時の GUI スモーク（既知・L2 で実走する。直近の L2 実走: `npm run dev` 起動下で `pytest tests/test_gui_smoke.py -q -p no:randomly` → **26 passed, 31.2s・実行日 2026-09-04**。7巡目で追加した `test_editor_delete_ignored_while_run_tab_active`＝実行タブ表示中の Delete で編集内容が変わらないこと、を含む）。

**カバレッジ（網羅率）の初回計測値: 83.0%**（`.venv/Scripts/python.exe scripts/run_all_tests.py --coverage` → `coverage: 83.0%`。JSON の `totals.percent_covered` は 83.05、対象 26 ファイル・3,999 文中 3,321 文を通過・実行日 2026-09-03）。**暫定値** — Wave 1 で他レーンが core を編集中の状態で測っており、確定値は編集が収束してから測り直す。計測は既定 OFF で、`--coverage` を付けたときだけ pytest に `--cov` を足す（日常のゲートの所要時間を延ばさないため）。行ごとの内訳は `workdir_build/coverage/coverage.json`（gitignore 済み）。

> ⚠️ **合格ラインは本書にも他のどの文書にも定義されていない**（2026-09-03 にリポジトリ全体を走査して確認。「カバレッジ 80%」「前回比 5% 以内」に相当する記述は存在せず、`_coverage` は `grid.py` の幾何ヘルパで無関係）。この行は測った事実だけを記録したもので、閾値の設定は別途の判断を要する。なお §1 の方針は「網羅率を目標にせず、壊れると出力の信頼が崩れる不変条件を固定する」で、閾値を置くならこの方針との整合を先に決める必要がある。

## 4. トレーサビリティ（要件 §8 合格条件 ⇔ テスト）

| §8 | 合格条件（要約） | 担保するテスト | レベル |
|---|---|---|---|
| 8-1 | 既知サンプルで全値が期待の列・行（取り違え0） | test_e2e_replay（実サンプル2面の配置検証）・test_mapping | L1（自作サンプルによる暫定合格。本番データ再検証は §6） |
| 8-2 | 出力行数＝入力ページ数（どの失敗時も） | test_acceptance_gaps::TR-G2・test_e2e_replay | L1 |
| 8-3 | 低信頼セルの〓化・要確認セル数でソート可能 | test_render_rows・test_e2e_replay | L1 |
| 8-4 | 元号丸印5値判定（不成立は〓） | test_e2e_replay（実サンプル5/5）・test_render_rows | L1 |
| 8-5 | 金額整数化・転記主義 | test_normalize・test_render_rows | L1 |
| 8-6 | 閾値変更→API 再送なしで再出力 | test_resume_cap・test_e2e_replay（render 再実行） | L1 |
| 8-7 | 中断→再実行で未処理分のみ送信 | test_resume_cap・test_process_interrupt | L1 |
| 8-8 | 〓背景色・文字列型・先頭ゼロ保持 | test_e2e_replay（xlsx セル型・書式検証） | L1 |
| 8-9 | ログ・一時ファイルに記入値なし・資格情報平文なし | test_leak_guards・test_acceptance_gaps::TR-G6 | L1 |
| 8-10 | 要確認セル数合計0の運用成立 | test_e2e_replay（COUNTIF 数式検証）＋運用手順（README） | L1＋L3 |
| 8-11 | GUI で実行→進捗→サマリ→フォルダを開く。CLI と出力一致 | test_gui_smoke（導線）・test_e2e_replay（CLI 側） | L2 |
| 8-12 | xlsx/csv 同時生成・抽出対象列の一致・CSV 先頭ゼロをテキストで判定 | test_acceptance_gaps::TR-G4（抽出214列の全突合）・test_e2e_replay | L1 |
| 8-13 | 最低信頼度列（文字ベースのみ・該当なしは空欄） | test_render_rows・test_e2e_replay | L1 |
| 8-14 | エディタ書き出し JSON をコアがそのまま読める | test_template（スキーマ検証）・test_gui_smoke（編集タブ）・test_review_fixes（expand-page） | L1/L2（保存→run 貫通は L3 で1回実施済み・2026-08-27。**ただし「スキャン PDF からゼロにテンプレを作る」導線は 2026-08-28 のユーザー指摘まで未検証だった**——§5 の教訓2） |
| 8-15 | GUI 指定の除外領域が抽出・二値化から除外 | test_mapping（除外領域）・test_e2e_replay | L1 |
| 8-16 | 等分割生成のドリフトなし（後半の「クリックで対象外→列に出ない」は 05 の AC-1.1・AC-1.15 へ分割・2026-09-01・P3-c） | test_grid（算術位置）・test_output_columns_stage1（output=false の列非出現・issue #66） | L1 |
| 8-17 | 罫線自動検出が候補を返す。不成立でも等分割で完走 | test_grid（検出・フォールバック） | L1 |

### NFR（§6）⇔ テスト

| NFR | 内容 | 担保 |
|---|---|---|
| §6.1 性能 | 100頁を目安時間内・メモリ上限内 | scripts/perf_check.py（実測 2026-08-27: 100頁 142.2s・RSS 310MB・PASS）。L2 |
| §6.2 信頼性 | 中断・再開・二重取り込み | test_resume_cap・test_process_interrupt・test_duplicate_source |
| §6.3 誤操作防御 | purge は明示時のみ | TR-G5（CLI）・cargo test（GUI 境界）・csp スモーク（実機） |
| §6.4 文字集合 | 異体字保持 | test_charset |
| §6.5 個人情報 | 漏出防止・ローカル保存・平文鍵なし | test_leak_guards・test_local_storage_guard・TR-G6 |

### 設計決定（D-xx）のうちテストで固定しているもの

D-01（金額）・D-06（below_table）・D-14（列導出＝テンプレート由来。固定列数での拒否は撤去済み・2026-08-31・`2e8f882`）・D-15（様式不一致 0.55・TR-G3）・D-21（最低信頼度）・D-23（subfields）・geometry_hash（§6.7・TR-G1）・再現性（render バイト一致）

### issue ⇔ 再発防止テスト

| issue | 再発防止 |
|---|---|
| #2 制御文字での例外メッセージ漏出 | test_render_rows（制御文字→〓）・test_leak_guards（stderr 無漏出） |
| #3 ログへの値出力 | test_leak_guards（duplicate_of がパスのみ） |
| #4 `__repr__` 経由の漏出 | test_leak_guards（固定 repr） |
| #5/#6/#7 GUI 境界 | cargo test（#7）＋実機 CDP スモーク（#5/#6/#7・2026-08-28 PASS） |
| #8 同期フォルダ | test_local_storage_guard |
| #9/#10 | 仕組み（.gitignore）・運用（README）で対応。テストなし |
| #11 金額正規化の列名依存 | test_review_fixes（normalize 属性で発火・列名非依存・同梱テンプレの宣言数） |
| #13 glob メタ文字ファイル名 | test_review_fixes（scan[1].pdf の実 PDF 展開） |
| #14 config 無検証 | test_review_fixes（閾値0・typo キー・型不正の拒否） |
| #12/#15 エディタ（TS 側） | tsc 型検査＋実機確認（Python テスト対象外） |
| #33 CSV の数式評価 | 運用（README・GUI 警告バナー）＋ test_review_fixes（検出が出力を変えないこと・CSV は読取値とバイト一致） |
| #34 xlsx の数式昇格・COUNTIF 循環 | test_review_fixes（シート XML の `<f>` は COUNTIF の1個のみ・data_type='s'・値は保持） |

## 5. 受入 Gap テスト（TR-G 系）の由来

2026-08-28 の棚卸しで「要件にあるがテストが無い」6件を特定し `test_acceptance_gaps.py` に実装した。**TR-G3 は D-15（様式不一致判定）が設計書のみでコアに未実装だったことを検出し、実装漏れの修正につながった**——受入条件を直接テスト化する価値の実例として記録する。

| ID | 内容 | 対応 |
|---|---|---|
| TR-G1 | remap が幾何変更を拒否し run を促す（非幾何は通す） | §6.7 |
| TR-G2 | 失敗3種混在でも行数維持＋ステータス書き分け | §8-2 |
| TR-G3 | 枠外率>0.55 → 様式不一致・全〓行 | D-15 |
| TR-G4 | xlsx↔csv 抽出214列の全突合 | §8-12 |
| TR-G5 | purge が --yes なしで拒否 | §6.3 |
| TR-G6 | 資格情報なしの verify が失敗コード | §8-9 |

### 教訓2: 字面検証の罠（2026-08-28・issue #19）

「テンプレ編集画面で PDF を開けない」「入力がフォルダ単位のみ」はユーザーの実機確認が検出した。自動テスト・3者レビューとも見逃した原因は、要件 §5.10 の字面が「画像を読み込み」・§5.1 が「入力フォルダ」であり、**検証がその字面の内側に閉じていた**こと。緑判定は「コアの処理」と「画面部品」の緑であって、「PDF だけを渡された利用者の最初の一歩」の緑ではなかった。

対策: expand-page サブコマンド＋PDF 直接オープン＋単一ファイル入力（ドラッグ＆ドロップ）を実装しテスト化。以後、受入検証には「利用者が最初に持っている物（スキャン PDF）から始まる導線」を必ず1本含める。

### 教訓3: 自己整合検証の罠（2026-08-28・ユーザー指摘）

テンプレートの較正に使った画像そのものを流す検証は「テンプレと入力が一致している」ことの確認にすぎず、位置合わせ（ずれた入力の再配置）は一度も試していなかった。入力を意図的にずらす実測（alignment_robustness）の結果:

- 回転: deskew が機能し ±0.5° まで 212/212 完全一致（test_alignment_robustness.py で回帰固定）。1°超で choice（元号）から崩れる（#23 へ追記）
- 平行移動: 補正機構が無く、±12px で元号の誤選択・±18px で大規模混入。**いずれも status=正常のまま**で自己検出（D-15）が発火しない（issue #30）

以後、配置系の受入検証には「較正画像と異なる（ずらした・別スキャンの）入力」を必ず含める。

### 教訓4: 正解表そのものが誤っていた（2026-08-28・issue #23）

丸印判定の「実サンプル 5/5 正解」は、**誤った正解表に対する 5/8** だった。設計書 v2.0 は page2 の家族3行を「丸なし＝未選択が正しい」と記録していたが、切り出し画像を拡大すると3行とも明瞭な丸がある。判定が〓を返すのを「丸が無いのだから正しい」と解釈してしまい、取りこぼしが実績として記録された。

対策: 判定系（丸印・選択式）の較正と評価は、**切り出し画像の目視で正解を確定させてから**行う。test_era_calibration.py は正解表を docstring に明記し、全問正解に加えて「閾値への余裕」（トップ値 0.0658・1位2位差 0.0647）も固定して、ぎりぎりで通る較正に戻らないようにしている。

## 6. 手動残（L3）

| 項目 | 内容 | 時期 |
|---|---|---|
| C10 素の Ctrl+C | コンソールからの SIGINT 伝播（自動化分は test_process_interrupt で担保済み） | リリース前に1回 |
| インストーラ導入 | 別環境（WebView2 有無）での setup.exe 実行（Q-23/Q-25） | 配布前 |
| §8-1 本番再検証 | Q-03/Q-12 解消後、実運用データでのパイロット（Q-16）。自作サンプル合格は暫定 | 実データ受領後 |
| 鍵ローテート | issue #1。GCP コンソールでの旧鍵無効化（ユーザー作業） | 即時 |
| 7巡目 L3-①: 実行画面のボタンが Space で押せる（#69 Q-H3） | **機械実測済み**（2026-09-02・品質再検証で Playwright により `document.activeElement=BUTTON` 時の Space が `defaultPrevented=false` を確認。実 Tauri 窓での手押しは未実施） | 実機確認は任意 |
| 7巡目 L3-②: 平文鍵（環境変数）で起動時に実行画面へ警告カードが出る（#69 S-MB） | **未実施**。core 側は `test_credentials_warn.py`、GUI 側は `credNotice` の node テストで固定済みだが、実 Tauri 窓での表示は未確認 | 次回の実機起動時 |
| 7巡目 L3-③: config.json が壊れている状態で設定モーダルの入力・保存が無効化され理由が出る（#69 Q-MF） | **未実施**。`merge_config` の cargo test と `loadError` の分岐で固定済みだが、実 Tauri 窓での表示は未確認 | 次回の実機起動時 |

## 7. 実行結果の記録

`scripts/run_all_tests.py` は最後に1行サマリ（PASS/FAIL・件数・所要時間）を出す。リリース時はその1行を Release ノートへ転記する（証跡3点セット: コマンド・件数・実行日）。
