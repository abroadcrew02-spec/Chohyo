# 配布物のリリース手順

配布する `chouhyo-ocr` のインストーラ（Windows NSIS・署名なし）を作って GitHub Release に置くまでの手順。作るのは [`.github/workflows/release.yml`](../.github/workflows/release.yml) というワークフロー（GitHub 上で自動的に動く一連の作業）で、`v` から始まるタグ（例: `v0.2.0`）を push すると起動する。

## 大原則: 配布する exe は release.yml が作ったものだけを使う

手元のパソコンで `scripts/build_dist.py` を実行して作った `core-dist/`（同梱 exe）は、動作確認用にとどめる。GitHub Release に添付する・利用者に配る、はしない。

理由: 手元のビルドは、その時点の作業ツリーの状態に依存する。コミットし忘れた変更・チェックアウトし忘れたブランチが混ざっていても、ビルド自体は成功する。release.yml は「タグを打った commit だけ」をチェックアウトしてビルドするため、何をビルドしたかが commit のハッシュとタグ名で誰でも後から確認できる。

## タグを打つ前に確認すること

release.yml はソースコードをビルドするだけで、バージョン番号は書き換えない。以下の4か所の `"version"` を、打つタグ名（`v` を除いた部分）に手で揃えてから commit する。揃っていないと、インストーラのファイル名（例: `chouhyo-ocr_0.1.0_x64-setup.exe`）がタグ名と食い違う。

- `gui/src-tauri/tauri.conf.json` の `version`（インストーラのファイル名に直結）
- `gui/src-tauri/Cargo.toml` の `version`
- `gui/package.json` の `version`
- `core/pyproject.toml` の `version`

あわせて `CHANGELOG.md` の `[Unreleased]` を `[x.y.z] - YYYY-MM-DD` に変える（[Keep a Changelog](https://keepachangelog.com/ja/1.1.0/) 形式。書式は `CHANGELOG.md` の既存エントリを参照）。

## 手順

1. 上記のバージョン番号 4 か所と CHANGELOG.md を更新して commit する（このリポジトリでは main ブランチへ）。
2. タグを打って push する。

   ```bash
   git tag v0.2.0
   git push origin v0.2.0
   ```

3. GitHub の Actions タブで `release` ワークフローの進行を見る（Windows ランナーでビルドするため、フロントエンド・Rust・PyInstaller を通しで数十分かかる見込み。※所要時間は実測していない）。
4. 成功すると、タグに対応する GitHub Release が自動で作られ、インストーラ（`*_x64-setup.exe`）が添付される。

タグを打つ前に「本当にビルドが通るか」だけ確認したい場合は、GitHub の Actions タブから `release` ワークフローを `workflow_dispatch`（手動実行ボタン）で起動できる。この場合は Release を作らず、ビルド成果物を Actions のアーティファクト（ワークフロー実行画面からダウンロードできる一時保存）としてのみ残す。

## release.yml が実際にやること

1. `actions/checkout` でタグの commit をチェックアウト
2. Python 3.13 を用意し、`.venv` を作って `core[dev]`（PyInstaller を含む）を導入
3. Poppler（PDF→画像変換ツール）を、`.github/workflows/ci.yml` の full ジョブと同じ URL・同じ sha256 チェックでダウンロード・展開
4. `scripts/build_dist.py` を実行し、`core-dist/chouhyo-core/` を作る（PyInstaller で Python コアを exe 化 → schema・出荷テンプレート・Poppler（実行ファイルとライセンス本文 `COPYING`/`COPYING.adobe`/`COPYING.gpl2`）を複製 → `verify --template templates/chouhyo-v1.json` で起動確認 → 成功したらビルドスタンプ `BUILD_STAMP.json` を書く）
5. `scripts/dist_stamp.py` の `check_freshness` を単体で呼び、直前に作った `core-dist` がソース（`core/chouhyo_ocr/**/*.py`・`schema/**/*.json`・`templates/chouhyo-v1.json`）と一致しているかを確認する。この検査自体はステップ4の直後に走るので通常は PASS になる。ここでの意義は「配布物の鮮度検査を CI の一部として必ず通す」という手順を仕組みとして固定すること（[issue #122](https://github.com/abroadcrew02-spec/Chohyo/issues/122)）
6. `cargo-about` を導入し、`scripts/gen_notices.py --check` で `THIRD-PARTY-NOTICES.txt` が依存関係（Rust・Python・poppler）と食い違っていないかを確認する（[issue #153](https://github.com/abroadcrew02-spec/Chohyo/issues/153)）。食い違っていたら Release を作らずに止まる
7. Node.js 22 を用意し `npm ci`
8. `npm run tauri build`（`tauri.conf.json` の `beforeBuildCommand` が先に `tsc && vite build` を実行してから、Rust 側をビルドしてインストーラ化する）
9. インストーラの `SHA256SUMS`（sha256 ハッシュ）を作る（[issue #159](https://github.com/abroadcrew02-spec/Chohyo/issues/159)）
10. ビルド成果物一式（インストーラ・`SHA256SUMS` を含む）を `actions/upload-artifact` で保存
11. タグ push のときだけ、`gh release create` でインストーラ（NSIS の `*.exe`）と `SHA256SUMS` を GitHub Release に添付する。使うのはリポジトリに既定で発行される `GITHUB_TOKEN`（このジョブだけ `permissions: contents: write` に引き上げている）で、追加のシークレットは使わない

## 注意点

- **リリースノートは自動生成**: `gh release create --generate-notes` は、直前のタグからの commit 一覧をもとに GitHub が自動で組み立てる。`CHANGELOG.md` の内容を自動で転記するわけではない。手で書いた説明文にしたい場合は、Release 作成後に `gh release edit <tag> --notes-file <ファイル>` で書き換える。
- **署名はしていない**: インストーラは未署名（`v0.1.0` の Release 説明にある「署名なし」の扱いを引き継いでいる）。SmartScreen の警告が出る前提で配布する。
- **入手後は `SHA256SUMS` と突き合わせる**: Release にはインストーラと並んで `SHA256SUMS`（ハッシュ値・空白2つ・ファイル名の1行形式）を添付している（issue #159）。ダウンロードしたインストーラの sha256 を PowerShell で計算し、`SHA256SUMS` に書かれた値と一致するか確認する。

  ```powershell
  Get-FileHash .\chouhyo-ocr_x.y.z_x64-setup.exe -Algorithm SHA256
  ```

  一致しなければダウンロードが壊れているか差し替えられている。コード署名はしていないため、これは「配布時点のファイルと変わっていないか」の確認であり、配布者本人の身元を証明するものではない。
- **`v0.1.0` は本ワークフロー導入前の手動リリース**: 2026-08-27 に公開された `v0.1.0` は、このワークフローができる前に手元でビルド・手動アップロードされたもの（確認: `gh release view v0.1.0` の `publishedAt` が 2026-08-27T10:03:07Z。`release.yml` は 2026-09-07 に新規作成しており、それより後）。過去のリリースを本ワークフローで作り直す作業はしていない。`v0.1.0` より後に打つタグから、このワークフローの成果物だけを使う運用にする。
- **同じタグに2度 Release は作れない**: `gh release create` は同名の Release が既にあると失敗する。作り直す場合は `gh release delete <tag>` してからタグも打ち直すか、`gh release upload` で成果物だけ追加する。

- 配布先の PC では、`%LOCALAPPDATA%\ChouhyoOCR\`（取り込んだ認証キー `cred.dpapi`・月次の送信回数 `api_usage.json`・中間データの既定の置き場）を **利用者本人だけが読めるフォルダ** にしておく。共有 PC や、`%LOCALAPPDATA%` に別グループの読み取り権限が継承されている環境では、配布前に `icacls %LOCALAPPDATA%\ChouhyoOCR` で権限を確認する（認証キーは Windows のユーザー単位で暗号化してあるので他のアカウントでは復号できないが、中間データには帳票の記入値が含まれる）。

## 配布物に同梱している poppler（GPL）について

`chouhyo-core.exe` は PDF を画像へ展開するために `pdftoppm` ほか poppler のバイナリ 39 本を同梱し、別プロセスとして起動する（本体へのリンクはしない）。技術側の対応は issue #161 で行った。

- バージョン: 26.02.0（`.github/workflows/release.yml`・`ci.yml` に記載の版と同じ）
- 入手元: `https://github.com/oschwartz10612/poppler-windows/releases/download/v26.02.0-0/Release-26.02.0-0.zip`（sha256 は各ワークフローに記載の値で照合してから展開している）
- このリポジトリ（`oschwartz10612/poppler-windows`）自体は MIT ライセンスだが、配布しているのは conda-forge の `poppler-feedstock` がビルドした poppler 本体（GPL-2.0）を zip へ再パッケージしたもの
- ライセンス本文（`COPYING`・`COPYING.adobe`・`COPYING.gpl2`）は `scripts/build_dist.py` が `core-dist/chouhyo-core/poppler/` へ複製し、`THIRD-PARTY-NOTICES.txt` の第3部にも載せている

**法務判断待ち（未確定）**: 別プロセス起動（subprocess で `pdftoppm` を呼ぶだけでリンクしない構成）が GPL の mere aggregation にあたるか、GPL §3 のソース提供義務（ソースまたは書面の申し出）をどう満たすかは、法務担当の判断が必要（issue #161・#78 の LICENSE 議論と同じ論点）。ここに書いたのはライセンス本文の同梱・出所の記録という技術側の対応のみで、この判断自体はまだ行っていない。

## 確認済み（2026-09-07）

- `workflow_dispatch` で release.yml を実行し、venv＋依存 → Poppler → 配布物ビルド（PyInstaller）→ 鮮度スタンプの検査 → npm ci → Tauri ビルド → artifact 保存まで通ることを確認した（run 34088267394・所要 7 分 48 秒・artifact `chouhyo-ocr-installer-main` 40.4 MB）。
- 1 回目（run 34087471906）は起動確認ログの日本語で `UnicodeEncodeError` になった。Windows ランナーのコンソールは cp1252 なので、ワークフローの `env` で `PYTHONIOENCODING=utf-8`・`PYTHONUTF8=1` を固定し、build_dist.py も自分の出力を UTF-8 にした（4bf3597）。

## 未検証

- タグ push のときだけ動く「GitHub Release へ添付」（`gh release create`）のステップ。次の `v*` タグで確認する。
- 2026-09-08 に追加した以下のステップは、手元では個別に確認したが、実際の GitHub Actions 上で通しで実行して確認してはいない（次の release.yml 実行で確認する）。
  - `cargo-about` の導入 + `scripts/gen_notices.py --check`（依存関係と `THIRD-PARTY-NOTICES.txt` の整合性チェック。手元では `cargo about generate` → `gen_notices.py` → `gen_notices.py --check` の一連が通ることを確認済み）
  - `SHA256SUMS` の作成・Release への添付
  - `gh release create` のタグ名を `env: TAG` 経由に変更した点（`actionlint` での構文チェックのみ実施）
- 同日 `.github/workflows/ci.yml` の full ジョブに追加した以下のステップも同様に未検証（次の main push で確認する）。
  - `cargo deny check`（手元では `gui/src-tauri` で `cargo deny check` が exit 0 で通ることを確認済み）
  - GUI スモーク（`npm run dev` のバックグラウンド起動 → ポート待ち合わせ → `playwright install chromium` → `pytest tests/test_gui_smoke.py`）。テストの実行自体は、稼働中の vite dev サーバーに対して手元で `32 passed` を確認したが、CI ランナー上で「dev サーバーの起動から待ち合わせまで」を含めて通しで確認してはいない
