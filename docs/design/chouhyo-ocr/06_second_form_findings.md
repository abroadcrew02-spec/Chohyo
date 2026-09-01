# 汎用化第1弾 S-1: 「2つ目の様式を1本通す」実測記録

## 1. 文書情報
- プロジェクト名: 帳票OCRツール
- 文書ID: chouhyo-ocr-06
- 版数: `v1.0`（初版・2026-09-01）
- 目的: §9「様式に依存しない汎用帳票ツールへ展開する」の実験 S-1 として、v1 のコード（`core/`・`gui/`）を一切変更せず、v1 とは意図的に構造を変えた第2の様式（様式B）を作成し、テンプレート検証から `.xlsx`/`.csv` 出力までを実走させた記録。目的は機能追加ではなく、**様式依存の露呈箇所を実測で一覧化すること**
- 関連文書: [01_requirements.md](01_requirements.md) §9（将来の拡張・机上仮説の出典）／[00_roadmap.md](00_roadmap.md)
- 前提となる机上仮説（2026-09-01・ラプラス実査）: C-1（1帳票=1ページ・`record.pages` は未参照）／C-2（`face_id` は front/back のみ）／C-3（各面に `tables` 1つ以上必須）／C-6（px 定数が 300dpi 較正）。本書はこの4件の裏取り・反証と、実測中に見つかった新規の露呈をあわせて記録する
- 実行日: 2026-09-01（すべての実測コマンド・結果は本書内に一次情報として記す。証跡3点セット＝コマンド・出力要点・実行日を各項目に付す）
- **汎用化第1弾の作業ID**（本ラウンドの呼称・他文書からの参照用）: **S-1**＝本書（2つ目の様式の実測）／**A-3**＝px 定数の dpi 正規化（`BASE_DPI`・`Template.dpi_scale`・残件は §7）／**③**＝設計書乖離の訂正（02_design.md の Protocol 実装状況注記）。いずれも 2026-09-01・案件両にらみ期間の dual-use 作業として PM 仕分けで確定

### 1.1 実行環境に関する注記（証跡の前提条件）
検証開始時点で `git status` により、`core/chouhyo_ocr/{align,cli,grid,mapping,pipeline,template}.py` と `docs/design/chouhyo-ocr/02_design.md` が**未コミットの変更を含んでいた**（本書の筆者が加えたものではない。本タスクを通じて `core/`・`gui/` への Write/Edit は一度も行っていない）。`git diff` の内容は `Template.dpi_scale`/`BASE_DPI` の追加など dpi 較正関連の変更で構成されており、背景説明にある「dpi 依存は並行で別担当が改修中」という記述と符合する。したがって本書の実測は**この未コミット状態の作業ツリー**を対象にしており、直近コミット `17fe228` 単体の状態とは一致しない。C-6（px 定数の 300dpi 較正）は本実験の対象外とし、様式Bも `render_dpi: 300`（v1 と同一の基準 dpi）で作成した——dpi 非依存性の検証はこの並行作業の担当範囲とする。

さらに本タスクの実行中、`core/tests/test_dpi_scaling.py` が新規（未追跡）ファイルとして途中から作業ツリーに現れた。これも本書の筆者が作成したものではなく、並行作業側が本タスクの実施中にコミットせず追加したものとみられる。§5 の回帰確認はこのファイルが存在する状態で実行しており、結果の解釈にはこの点を踏まえる。

## 2. 様式B: 構成と素材の場所

v1（`templates/chouhyo-v1.json`）との構造差分を意図的に作った。

| 観点 | v1 | 様式B |
|---|---|---|
| 面数 | 2（front/back、上下合成を分割） | **1**（front のみ） |
| 表裏分割 | あり | **なし** |
| 選択式（choice） | あり（元号） | **なし**（全欄 text） |
| 表の左右ブロック | あり（5行×2, 14行×2） | **なし**（単一ブロック5行） |
| 表の列数 | 3〜4列 | 3列 |
| 単発欄数 | 13 | 3 |
| 総列数（管理6列込み） | 220 | **24** |
| 画像寸法 | 2490×3510（縦・A4相当） | **1800×1200**（横・6in×4in 相当） |
| `record.pages` | 1 | 1（変更せず） |
| `render_dpi` | 300 | 300（変更せず・C-6 は対象外） |

素材の場所（⚠️ 2026-09-01 レビュー S-9/N-3 を受け `testdata/formB/` へ移設。`templates/` 直下は Tauri が配布物へ丸ごと同梱するため検証資産を置かず、`samples/` は .gitignore 対象で再現性が残らないため——検証資産は追跡対象かつ配布物外の `testdata/` に置くのを以後の慣行とする）:
- テンプレート: `testdata/formB/formB-v1.json`（本タスクで新規作成。スキーマ検証・`verify` を通過済み）
- 合成帳票画像: `testdata/formB/formB-1.png`（PIL 生成。表の罫線はテンプレートの `row_pitch`/`row_height`/`columns` と厳密に一致する位置に描画——位置合わせ（罫線射影による平行移動推定・`align.py`）のアンカーとして機能させるため。装飾ラベルは英字表記で代用し CJK フォントの有無に依存させていない）
- 合成 Vision 応答: `testdata/formB/responses/formB-1_p0001.json`（`DOCUMENT_TEXT_DETECTION` の `MessageToDict` 形式を手で構成。画像に描いた内容とは独立——`ReplayClient` は `page_id` からファイルを引くだけで画像のピクセルを読まないため）
- 生成スクリプト（検証用・非成果物）: スクラッチパッド `gen_formB.py`
- 実行用の分離 config（`workdir`/`output_dir`/`log_dir` をスクラッチパッド配下に隔離し、リポジトリの既存 `workdir/` を汚さない設計）: スクラッチパッド `config_formB.json`
- プローブ用テンプレート変異体（拒否確認専用・スクラッチパッド `probe_templates/`）: `formB-badface.json`（`face_id: "omote"`）／`formB-notables.json`（`tables: []`）／`formB-pages2.json`（`record.pages: 2`）

## 3. 通った範囲（コード無改変で実際に動いた部分）

### 3.1 テンプレート検証（`verify`）
```
.venv\Scripts\python.exe -X utf8 -m chouhyo_ocr.cli verify --template testdata/formB/formB-v1.json
```
結果（2026-09-01・実行済み）: `{"event": "verify", "check": "template", "ok": true, "columns": 24, "cells": 18, "amount_cells": 0, ...}`。列数導出（管理6列＋抽出対象列18＝24列）が手計算どおりに一致。副産物として `[W-3] 氏名（欄）と 受付日（欄）の間に100px の隙間がある` の警告が自然発火し、様式に依存しない汎用警告として機能することも確認した。

### 3.2 `run --replay`（実 API 送信ゼロ）
```
.venv\Scripts\python.exe -X utf8 -m chouhyo_ocr.cli --config <isolated>\config_formB.json run --input testdata/formB/formB-1.png --template testdata/formB/formB-v1.json --replay testdata/formB/responses
```
結果（2026-09-01・実行済み・exit=0）: `{"event": "summary", "pages": 1, "rows": 1, "align_failed": 0, "api_calls": 1, "unclear_cells": 2, "risky_cells": 0, ...}`。位置合わせ（傾き0°・平行移動0,0 で成立）→ 割付 → 出力までノーエラーで完走。

### 3.3 第1層検証（配置の正しさ・xlsx を直接確認）
`openpyxl` で読み出した実測値（コマンド: 上記 `run` 出力の xlsx パスを `load_workbook` で読み込み。実行日 2026-09-01）:

| 列 | 期待値 | 実測値 | 一致 |
|---|---|---|---|
| 氏名 | 山田花子 | 山田花子 | ○ |
| 受付日 | 9.1 | 9.1 | ○ |
| 備考 | 視察対応 | 視察対応 | ○ |
| visit_01_来場日/人数/メモ | 9.1/3/挨拶回り | 9.1/3/挨拶回り | ○ |
| visit_02_来場日/人数/メモ | 9.2/5/名刺交換 | 9.2/5/名刺交換 | ○ |
| visit_03_*（完全空行） | 空文字/None | None/None/None | ○ |
| visit_04_来場日（部分記入行） | 9.4 | 9.4 | ○ |
| visit_04_人数・メモ（同一行の未記入セル） | 〓（§5.5: 空行でない行の未読取セルは〓） | 〓／〓 | ○ |
| visit_05_来場日/人数/メモ | 9.5/2/見学 | 9.5/2/見学 | ○ |
| 要確認セル数 | `=COUNTIF(G2:X2,"〓")` | `=COUNTIF(G2:X2,"〓")` | ○（列数24＝Xで終端。導出ロジックが決め打ちでないことを実証） |
| 最低信頼度 | 0.970 | 0.970 | ○ |
| ステータス | 正常 | 正常 | ○ |

取り違え・列ズレは0件。§5.5 の空行判定・未記入セルの〓化という**様式に依存しない一般規則**も、v1 と異なる行数・列配置の様式Bで同一に成立した。

### 3.4 `detect-grid`（枠候補生成・GUI が呼ぶコアAPI）
```
.venv\Scripts\python.exe -X utf8 -m chouhyo_ocr.cli detect-grid --mode uniform --region 100,300,750,400 --rows 5 --cols 3
.venv\Scripts\python.exe -X utf8 -m chouhyo_ocr.cli detect-grid --mode ruled   --region 100,300,750,400 --image testdata/formB/formB-1.png
```
結果（2026-09-01・実行済み）: `uniform` は指定どおりの格子を返却（`residual_px: 0.0`）。`ruled` は実際に描画した罫線を検出し `row_pitch: 79.8`（期待80）・`columns` 幅 200/150/399（期待200/150/400）と誤差1px未満で一致（`residual_px: 0.8`）。罫線検出アルゴリズム自体は様式Bの罫線配置・区画サイズに対しても機能した。

## 4. 露呈一覧（机上仮説との照合＋新規発見）

| # | 現象 | 原因箇所（file:line） | 区分 | 机上仮説との照合 |
|---|---|---|---|---|
| 1 | `face_id: "omote"` のテンプレートは `verify`/`run` いずれも拒否される。メッセージ: `v1 の face_id は front/back のみ受理（指定: omote）` | `core/chouhyo_ocr/template.py:19`（`V1_FACE_IDS = {"front", "back"}`）・503行（拒否箇所） | 拒否（決定論的・実害なし） | **C-2 と一致**（実測済み・2026-09-01） |
| 2 | `tables` を持たない面はテンプレート読み込み自体が拒否される。メッセージ: `face 'front' に tables が無い。位置合わせのアンカーとして各面に1つ以上のテーブル定義が必要` | `core/chouhyo_ocr/template.py:557-560` | 拒否 | **C-3 と一致**（実測済み・2026-09-01） |
| 3 | `record.pages: 2` のテンプレートは拒否される。メッセージ: `v1 は record.pages=1 のみ受理（指定: 2）`。加えて `Template.record_pages` フィールド自体、`template.py` 以外のどこからも参照されていない（`pipeline.py`・`ingest.py` を含め grep で0件） | `core/chouhyo_ocr/template.py:496`（拒否）／`template.py:131,738`（値の保持のみで下流未参照） | 拒否＋実装未着手 | **C-1 と一致**（実測済み・2026-09-01）。仮説の「未参照」は「値を見ずに素通りする」ではなく「拒否はするが、拒否を外しても消費する下流ロジックが無い」の意——文言の精度を上げる形で確定 |
| 4 | テンプレート拒否を `verify` ではなく `run`（`render`/`remap` も同型）経由で踏むと、拒否理由の具体的な文言（例: 指定された `face_id` の値）が失われ、`ERROR TemplateError: 処理を中止しました。詳細は error.log を参照。` という総称メッセージのみになる。`error.log` 側もトレースバックのソースコード行（f-string リテラル。実行時に埋め込んだ値は含まれない）しか記録されず、結局オフェンディングな値はどこにも残らない | `core/chouhyo_ocr/cli.py`（`main()` の例外分岐。`TemplateError` は `OperationRefused`/`ConfigError` のいずれにも該当せず汎用 `Exception` 分岐へ落ちる） | 不便（様式非依存の一般的なUXギャップだが、複数様式運用でテンプレート試行錯誤の頻度が上がるほど踏みやすくなる） | 3仮説とは無関係の**新規発見** |
| 5 | `project_root()`（設定ファイル探索の基準ディレクトリ判定）は、カレントディレクトリとその親を遡り「`templates/chouhyo-v1.json` が存在するか」を唯一のマーカーにしている。様式Bのみが存在し v1 のテンプレートファイルが無い/リネームされた配置では、このマーカー探索が失敗し `app_root()` へフォールバックする | `core/chouhyo_ocr/paths.py:26` | 潜在（静的コード確認のみ・未実行）。今回の構成では `app_root()` と `project_root()` が同一パスに解決されるため実害は出ていない。フォールバック自体はエラーにならず**静かに**別の判定ロジックへ切り替わる点が本質——本番配布（PyInstaller frozen exe）や、v1 テンプレートを整理・改名する将来の複数様式運用で顕在化しうる | 3仮説とは無関係の**新規発見**（実行では確認せず、コードリーディングのみで特定。v1 のテンプレートファイルを移動する実験はリポジトリの他テストへの影響が読めないため見送った） |
| 6 | テンプレート編集GUI（`gui/src/Editor.tsx`）は面を `"front" \| "back"` の Union 型で扱い、`face("front")`/`face("back")` のように2面を決め打ちで生成・保存する（1405-1446行）。UI 上も「表面／裏面」の固定ラベル（2644-2646行）で、面を1つだけ持つ様式・3面以上の様式をこの画面から作ることはできない | `gui/src/Editor.tsx:1405-1446, 2644-2646` | 拒否ではなく構造的に非対応（GUIは未実行・静的コード確認のみ） | 3仮説とは無関係の**新規発見**。コア（Python）側は `faces` を任意長の配列として扱えるが、**編集GUI側が2面固定**という非対称がある |
| 7 | Tauri 側（`gui/src-tauri/src/lib.rs`）はリポジトリルート探索・バックアップ対象判定・出荷テンプレート判定など複数箇所で `templates/chouhyo-v1.json` という具体的ファイル名を直接埋め込んでいる（46, 63, 99, 109, 115, 344, 434, 447, 471, 571, 574行） | `gui/src-tauri/src/lib.rs`（該当行複数） | 拒否ではなく構造的に非対応（GUIは未実行・静的コード確認のみ） | 3仮説とは無関係の**新規発見**。「出荷テンプレート」という概念そのものが単一ファイル名に紐付いており、複数様式を並行運用する構造になっていない |

### 4.1 反証は無し
今回実測した範囲では、C-1/C-2/C-3 のいずれも「拒否される」という仮説どおりの挙動で、拒否されずに素通りして誤動作するケース（＝仮説の反証）は確認されなかった。C-6（dpi 較正）は実験設計上ここでは検証していない（§1.1）。

### 4.2 ポジティブな確認（様式非依存だった箇所）
以下は「壊れなかった」こと自体が汎用化の土台として有効な確認であるため明記する。

- `columns.py` の列導出・`excel_column_letter`・`render_out` の `COUNTIF` 範囲計算は列数（24列）に応じて動的に決まり、v1 の220列決め打ちの痕跡は無かった
- `template.py` の重なり警告群（W-1〜W-4）は様式Bでも自然発火した（W-3が1件）——警告ロジックが特定の欄名・座標レンジに依存していない
- `grid.py`（`detect-grid` の `ruled`/`uniform` 両モード）は様式Bの罫線・区画サイズに対しても機能した
- `render_rows.py` の〓判定規則（§5.5: 空行でない行の未読取セルは〓）は様式Bでも同一に動作した
- `render_out.py` の危険接頭辞スキャン（`risky_cells`）も動作した（0件）

## 5. 検証・後始末

- 既存テストの回帰確認: `.venv\Scripts\python.exe -X utf8 -m pytest core/tests -q`（2026-09-01・4回実行。理由は下記）。結果: 直近3回は `1 failed, 418 passed`（`core\tests\test_output_columns_stage2.py::test_run_and_remap_both_report_excluded_field_counters` が `assert 0 == 3` で失敗）で安定。最初の1回のみ `2 failed, 417 passed`（上記に加え `core\tests\test_dpi_scaling.py::test_coarse_dilate_scales_with_dpi_changes_exclusion_mask` も失敗）だった
  - 両失敗テストとも `formB` を一切参照しない（`grep -rln "formB" core/tests/` が0件・確認済み）ため、**本タスク（様式B の追加）に起因する回帰ではない**
  - `test_dpi_scaling.py` は §1.1 で触れた並行作業（dpi 較正）が本タスクの実行中に新規追加したファイルで、実行のたびに結果が変わった（1回目のみ失敗）のは、並行作業側が編集中の `core/chouhyo_ocr/{align,mapping}.py` がテスト実行と同時に書き換わっていたためとみられる（作業ツリーが実行中に動く状態だった）。この失敗は本タスクの管理範囲外であり、修正もしていない
  - `test_output_columns_stage2.py` の失敗は4回とも再現し、issue #66 の対象外欄カウンタ回帰とみられるが、これも並行作業側の `mapping.py`/`pipeline.py` の変更が原因であり、様式B・本タスクの変更とは無関係（本タスクは `core/` に一切書き込んでいない）
- **最終回帰（A-3 レビュー対応完了後・2026-09-01）**: `.venv\Scripts\python.exe -X utf8 -m pytest core/tests -q`（フォアグラウンド実行）→ **421件 全緑**（8分49秒・300dpi バイト一致を担保する test_e2e_replay を含む）。上記の並行作業起因の失敗2件（`test_output_columns_stage2` の `assert 0 == 3`＝pipeline が assign へ渡す新引数 `dpi` にテスト側モックが未追従で TypeError→map_failed になっていたもの／`test_dpi_scaling` の変動＝実装作業中の作業ツリーで測ったため）は、モックの `**kwargs` 透過とA-3 実装完了により**解消済み**
- 中間データ: すべてスクラッチパッド配下の専用 `workdir`/`output`/`logs` に隔離し、リポジトリの既存 `workdir/` には一切書き込んでいない（`git status --porcelain=v1 -- workdir/` が空であることを確認済み・2026-09-01）
- 実 API 送信: 一度も行っていない（`--replay` 経路のみ使用。`verify` の `credentials` チェックは `state: "missing"` のまま——資格情報を用意せず実施した）
- 本タスクで `core/`・`gui/` に対して行った変更: **0件**（Read/Grep のみ。新規作成は `templates/formB-v1.json`・`samples/formB/**`・本書のみ）
- コミット: 行っていない（依頼どおり）

## 6. 後続ロードマップへの示唆（メモ）

- C-2/C-3/C-1 はいずれも「拒否」で止まる設計であり、様式追加時にクラッシュ・誤出力が起きるリスクは低い。汎用化の作業は「拒否条件を緩める」設計変更として着手しやすい構造になっている
- 一方で、拒否時のエラーメッセージ品質（露呈4）は現状 `verify` に一極集中しており、複数様式運用を前提にするなら `run`/`render`/`remap` 側でも `TemplateError` を専用ハンドリングし、具体的な理由を握りつぶさない改修が対（つい）で必要になる
- GUI（露呈6・7）はコアより先に「2面固定」という設計へ強く結合しており、コア側の `faces` 配列汎化が先行しても GUI 側は別途の作業が要る。汎用化ロードマップの見積りに GUI 分の工数を独立して積む必要がある
- `paths.py` の `project_root()` マーカー（露呈5）は影響範囲が読みにくいため、複数様式対応に着手する前に「マーカーの条件を `templates/*.json` の存在等へ緩めるか」を設計判断として一度潰しておくと安全

## 7. A-3（px 定数の dpi 正規化）の残件一覧

A-3 で対応した定数と、意図的に残した定数・配線の正本。ここに載っていない px 定数を見つけたら本表へ追記する（2026-09-01・レビュー S-4/N-4 対応）。

| 状態 | 対象 | 位置 | 備考 |
|---|---|---|---|
| ✅ 対応済み | `_LINE_GAP`・`_BUCKET`（＋locator の自己記述化） | mapping.py | 300dpi バイト一致契約・レビュー M-1〜M-3 反映 |
| ✅ 対応済み | `COARSE_DILATE`・`SHIFT_RUNNER_DIST` | align.py | |
| ✅ 対応済み | `ROW_INSET`・`projection.LINE_GAP` | grid.py・projection.py | `detect-grid --dpi`（72〜1200 検証付き）経由 |
| ✅ 対応済み | `CHOICE_MARK_MARGIN_PX` | template.py | テンプレート検証の許容値 |
| ⏳ **意図的に除外** | `BAND_PAD`・`BAND_PAD_IN`（元号丸印の帯） | era.py:13-15 | D-31 で外6px/内8px の**向きまで実測較正済み**のため機械的スケールは正解表を壊す。dpi の違う様式で choice を使う際に**再較正とセットで**対応する（スケールだけ掛けない） |
| ⏳ 未配線 | テンプレート編集GUI → `detect-grid --dpi` | gui/src/Editor.tsx:1741,1743 | 画面は `--dpi` を渡しておらず、grid 側の正規化は**製品経路から到達不能**（CLI 手打ちのみ有効）。GUI 改修時に配線する |
| ⏳ 別課題 | `pipeline.py` の広域 except が TypeError 等のコード欠陥を「様式不一致」に化けさせる | pipeline.py:548 付近 | 露呈4（§4）と同根。モジュール境界に引数が増えるたび同型の静かな事故が起きうる——対で改修する（コード欠陥系例外の再送出 or error_code へのメッセージ付加） |
