# テスト要件書 — 帳票OCRツール v1

| 項目 | 内容 |
|---|---|
| 版 | v1.0（2026-08-28） |
| 正本 | 本書（テスト要件・トレーサビリティ）。要件は 01_requirements.md、設計は 02_design.md |
| 実行方法 | `python scripts/run_all_tests.py`（pytest 全体＋cargo test＋集計） |

## 1. テスト方針

設計書 §12 の方針「テストは背骨に寄せる」を維持する。網羅率を目標にせず、**壊れると出力の信頼が崩れる不変条件**をテストで固定する:

1. **配置の正しさ**（§8 第1層）— 値が正しい列・正しい行に置かれること。1件でも誤配置なら不合格
2. **転記主義** — 低信頼値が出力に残らない（〓化）。推測で埋めない
3. **行数保存** — どの失敗経路でも入力ページ数＝出力行数
4. **再現性・再開** — 同一入力で同一出力、中断後は未処理分のみ送信
5. **漏出防止** — 記入値がログ・stderr・例外メッセージへ出ない

テストは**実 API を一切呼ばない**。Vision 応答は `workdir/responses/`・`workdir/s2/` の保存済み実応答を `ReplayClient` で再生する（課金ゼロ・ネットワーク不要・決定論）。

## 2. テストレベル

| レベル | 定義 | 実行 |
|---|---|---|
| L1 自動 | `run_all_tests.py` が無人で完走・合否判定する | 毎コミット前 |
| L2 半自動 | スクリプトはあるが環境準備（dev サーバー・release exe）が要る | リリース前 |
| L3 手動 | 人の操作・外部環境が必要 | 節目のみ（§6） |

- L1: pytest 120 件（GUI スモーク 5 件は dev サーバー無しでは自動 skip）＋ cargo test 2 件
- L2: GUI スモーク（`npm run dev` 起動下で L1 に合流）／release exe の CDP 検証（issue #5/#6/#7 の実機確認・scripts 化はレビュー時に都度）
- L3: §6 の残項目

## 3. テストスイート一覧（L1）

| ファイル | 件数 | 対象 |
|---|---|---|
| core/tests/test_template.py | 12 | テンプレート読込・v1 受入範囲・格子展開（218列導出の前提） |
| core/tests/test_columns.py | 7 | 列導出（設計 §4.3 の検証3点: 218列・内訳・field_id 一意） |
| core/tests/test_mapping.py | 6 | symbol 割付・行クラスタ・除外領域（実応答回帰） |
| core/tests/test_normalize.py | 4 | 金額 D-01・複合セル分割 D-23 |
| core/tests/test_render_rows.py | 11 | セル3状態・〓判定・ステータス合成・制御文字〓化 |
| core/tests/test_e2e_replay.py | 12 | run→xlsx/csv 貫通・配置検証・バイト一致再現性 |
| core/tests/test_resume_cap.py | 4 | 再開規則・送信上限（§8-6/7） |
| core/tests/test_duplicate_source.py | 2 | 二重取り込み検知 |
| core/tests/test_charset.py | 1 | 異体字・サロゲートペア保持（§6.4） |
| core/tests/test_grid.py | 4 | 枠候補生成（罫線検出・等分割、§8-16/17 の土台） |
| core/tests/test_process_interrupt.py | 1 | 実プロセス強制終了→再開（C10 の自動化可能部分） |
| core/tests/test_acceptance_gaps.py | 6 | 受入 Gap（TR-G1〜G6・§5） |
| core/tests/test_leak_guards.py | 3 | 漏出防止の再発防止（issue #2/#3/#4） |
| core/tests/test_local_storage_guard.py | 2 | 同期フォルダ検知（issue #8） |
| core/tests/test_review_fixes.py | 14 | レビュー指摘の再発防止（issue #11/#13/#14/#19: normalize 属性・glob エスケープ・config 検証・単一ファイル入力・expand-page） |
| core/tests/test_gui_smoke.py | 5 | GUI 導線（Playwright・デモモック。dev サーバー無しは skip） |
| gui/src-tauri（cargo test） | 2 | サブコマンド白リスト（issue #7） |

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
| 8-12 | xlsx/csv 同時生成・抽出対象列の一致・CSV 先頭ゼロをテキストで判定 | test_acceptance_gaps::TR-G4（212列全突合）・test_e2e_replay | L1 |
| 8-13 | 最低信頼度列（文字ベースのみ・該当なしは空欄） | test_render_rows・test_e2e_replay | L1 |
| 8-14 | エディタ書き出し JSON をコアがそのまま読める | test_template（スキーマ検証）・test_gui_smoke（編集タブ）・test_review_fixes（expand-page） | L1/L2（保存→run 貫通は L3 で1回実施済み・2026-08-27。**ただし「スキャン PDF からゼロにテンプレを作る」導線は 2026-08-28 のユーザー指摘まで未検証だった**——§5 の教訓2） |
| 8-15 | GUI 指定の除外領域が抽出・二値化から除外 | test_mapping（除外領域）・test_e2e_replay | L1 |
| 8-16 | 等分割生成のドリフトなし・クリックで対象外→列に出ない | test_grid（算術位置）・test_template（enabled=false） | L1 |
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

D-01（金額）・D-06（below_table）・D-14（218列導出）・D-15（様式不一致 0.55・TR-G3）・D-21（最低信頼度）・D-23（subfields）・geometry_hash（§6.7・TR-G1）・再現性（render バイト一致）

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
| TR-G4 | xlsx↔csv 抽出212列の全突合 | §8-12 |
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

## 6. 手動残（L3）

| 項目 | 内容 | 時期 |
|---|---|---|
| C10 素の Ctrl+C | コンソールからの SIGINT 伝播（自動化分は test_process_interrupt で担保済み） | リリース前に1回 |
| インストーラ導入 | 別環境（WebView2 有無）での setup.exe 実行（Q-23/Q-25） | 配布前 |
| §8-1 本番再検証 | Q-03/Q-12 解消後、実運用データでのパイロット（Q-16）。自作サンプル合格は暫定 | 実データ受領後 |
| 鍵ローテート | issue #1。GCP コンソールでの旧鍵無効化（ユーザー作業） | 即時 |

## 7. 実行結果の記録

`scripts/run_all_tests.py` は最後に1行サマリ（PASS/FAIL・件数・所要時間）を出す。リリース時はその1行を Release ノートへ転記する（証跡3点セット: コマンド・件数・実行日）。
