# 帳票OCRツール（Chohyo）

手書きの定型帳票をスキャンした PDF を読み取り、**1枚＝1行** の Excel（.xlsx）と CSV に変換するデスクトップツール。OCR は Google Cloud Vision API を使う。

設計の前提は「**空欄を出すコストより、誤った値を出すコストの方が大きい**」。自信を持って読めなかったセルには値を出さず **〓** を置き、行頭の「要確認セル数」で人が直す箇所を絞り込む。読めない箇所を推測で埋めない（詳細: [要件定義書](docs/design/chouhyo-ocr/01_requirements.md)）。

- 出力: 218列固定（管理6＋本人12＋家族欄60＋明細140）。.xlsx が目検と提出の正、.csv はシステム取り込み用
- 〓セルには背景色つき。要確認セル数は COUNTIF 数式で、直すたびに自動で減る
- 中断・再実行しても処理済みの紙は再送信しない（API 課金を無駄にしない）

## オペレーター向け（読み取る人）

1. インストーラ `chouhyo-ocr_x64-setup.exe` を実行する（コード署名なしのため SmartScreen の警告が出る。「詳細情報」→「実行」）
2. アプリを起動し、初回のみ「認証キーを選択」で管理者から受け取った JSON キーを取り込む（暗号化して保存され、元ファイルは以後不要）
3. **スキャンのファイル名は連番にする**（例: `scan_0001.pdf`）。ファイル名は Excel の「入力ファイル名」列やログにそのまま出るため、氏名などを含めない
4. 画面の手順どおり: **1** 帳票 PDF のフォルダを選択 → **2** Excel の保存先を確認 → **3** 読み取りを開始
5. 完了したら「出力フォルダを開く」→ Excel の「要確認セル数」を大きい順に並べ替え、色つきの 〓 を紙の原本と見比べて直す。合計が 0 になったら完成

途中で終了しても問題ない（次回は未処理分から再開する）。インターネット接続が必要。

## 管理者向け

- **テンプレート編集** タブで帳票の読み取り位置（枠）を定義する。「表をつくる」で外枠を描くと罫線から行と列を自動検出（不成立なら等分割に切替）。保存時にコアが検証し、列数（218）が合わなければ拒否する
- 設定（歯車）: 〓閾値・丸印閾値・送信上限・保存先3種の6項目のみ（`config.json`）
- 中間データ（`workdir/`）は**個人情報を含む**。クラウド同期対象外の場所に置き、削除は `purge --yes` のみ

## CLI（GUI と同じ処理を単体実行できる）

```
cd core
..\.venv\Scripts\python.exe -m chouhyo_ocr.cli <command>
```

| コマンド | 何をするか | API 送信 |
|---|---|---|
| `run --input <dir>` | 一括読み取り（`--replay <dir>` で保存済み応答の再生＝課金ゼロ） | する |
| `render` | 閾値変更後などに .xlsx/.csv を作り直す | しない |
| `remap` | テンプレートの枠変更後にセル割付をやり直す（幾何変更は `run` が必要と拒否される） | しない |
| `detect-grid --image <png> --region x,y,w,h` | 罫線から表の行・列を検出（`--mode uniform --rows N --cols M` で等分割） | しない |
| `status` / `verify` | 進捗表示／テンプレート・Poppler・資格情報の点検 | しない |
| `import-credentials <json>` | 鍵を DPAPI 暗号化で取り込む | しない |
| `purge --yes` | 中間データの削除 | しない |

## 開発者向け

構成: Python コア（`core/`・OCR/割付/出力の全ロジック）＋ Tauri/React GUI（`gui/`・コアの起動と表示だけ）。契約はテンプレート JSON（正本スキーマ: `schema/template.schema.json`）と stdout の JSON Lines。

```
# セットアップ
python -m venv .venv
.venv\Scripts\pip install openpyxl jsonschema pillow numpy google-cloud-vision keyring pytest playwright pyinstaller
cd gui && npm install

# テスト一括実行（pytest 106件 + cargo test 2件。GUI スモークは dev サーバー起動中のみ）
.venv\Scripts\python.exe scripts\run_all_tests.py

# 配布物ビルド（PyInstaller onedir → core-dist/ → NSIS インストーラ）
# core/ のコード・schema/・templates/ のいずれかを変更したら必ず再実行する。
# core-dist/ 内の同梱 exe・コピーが古いままだと、GUI（同梱コアを優先して呼ぶ）の
# 検証・実行と開発中コアの挙動が食い違う
.venv\Scripts\python.exe scripts\build_dist.py
cd gui && npm run tauri build
```

- 設計判断の正本: [02_design.md](docs/design/chouhyo-ocr/02_design.md)（D-01〜D-24）／実装順: [00_roadmap.md](docs/design/chouhyo-ocr/00_roadmap.md)
- 開発中の Vision 呼び出しは `--replay` で保存済み応答を使う（`workdir/responses/`）。テストは実 API を一切叩かない
- 帳票の記入値をログ・進捗イベントに書かない（`logging_safe.py` が唯一の logging 入口）

## セキュリティ・取り扱い注意

- **`.csv` を Excel でダブルクリックして開かない。** 先頭ゼロが落ちるうえ、`=` `+` `-` で始まる値が計算式として実行される（実機確認済み）。中身を見るときはテキストエディタか、Excel の「データ」→「テキストまたは CSV から」で全列を文字列として取り込む。目視確認と提出には `.xlsx` を使う（こちらは文字列型を固定しており影響を受けない）
- 実運用の帳票データは指定端末のみで扱い、検証後に削除する（要件 §6.5）
- `workdir/`（読取値・展開画像）・`output/`・鍵ファイルは `.gitignore` 済み。リポジトリへ入れない
- Vision へ送る画像は除外領域（綴じ穴・黒塗り・印字ラベル）をマスクしてから送信する

## API の課金と安全装置

Google Cloud Vision は **月 1,000 ユニットまで無料**（1画像=1ユニット・機能ごとに課金。[公式料金](https://cloud.google.com/vision/pricing) で 2026-08-28 確認）。超過分の Document Text Detection は 1,000 ユニットあたり $1.50。帳票1ページ＝1ユニットなので、月 3,000 枚なら超過 2,000 ユニット＝約 $3。

**強制停止の安全装置**: 月 900 ユニット（`config.json` の `api_monthly_cap`）に達すると、送信を行わずに停止する。警告ではなく停止にしているのは、課金が後から取り消せないため。残量は `verify` と GUI の実行画面に出る。

- カウンタの場所: `%LOCALAPPDATA%\ChouhyoOCR\api_usage.json`（`workdir` の外。`purge` しても消えず、複数の作業フォルダをまたいで合算される）
- **テスト（`--replay`）はカウントしない**——保存済み応答の再生で API を叩かないため、テストで無料枠を消費しない
- 限界: 数えるのは「このツールがこの PC から送った回数」だけ。別 PC・別ツールからの利用は数えられない。**正確な実績は GCP の課金ダッシュボードが正本**で、これは暴走の歯止め
