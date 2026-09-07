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
4. `scripts/build_dist.py` を実行し、`core-dist/chouhyo-core/` を作る（PyInstaller で Python コアを exe 化 → schema・出荷テンプレート・Poppler を複製 → `verify --template templates/chouhyo-v1.json` で起動確認 → 成功したらビルドスタンプ `BUILD_STAMP.json` を書く）
5. `scripts/dist_stamp.py` の `check_freshness` を単体で呼び、直前に作った `core-dist` がソース（`core/chouhyo_ocr/**/*.py`・`schema/**/*.json`・`templates/chouhyo-v1.json`）と一致しているかを確認する。この検査自体はステップ4の直後に走るので通常は PASS になる。ここでの意義は「配布物の鮮度検査を CI の一部として必ず通す」という手順を仕組みとして固定すること（[issue #122](https://github.com/abroadcrew02-spec/Chohyo/issues/122)）
6. Node.js 22 を用意し `npm ci`
7. `npm run tauri build`（`tauri.conf.json` の `beforeBuildCommand` が先に `tsc && vite build` を実行してから、Rust 側をビルドしてインストーラ化する）
8. ビルド成果物一式を `actions/upload-artifact` で保存
9. タグ push のときだけ、`gh release create` でインストーラ（NSIS の `*.exe`）を GitHub Release に添付する。使うのはリポジトリに既定で発行される `GITHUB_TOKEN`（このジョブだけ `permissions: contents: write` に引き上げている）で、追加のシークレットは使わない

## 注意点

- **リリースノートは自動生成**: `gh release create --generate-notes` は、直前のタグからの commit 一覧をもとに GitHub が自動で組み立てる。`CHANGELOG.md` の内容を自動で転記するわけではない。手で書いた説明文にしたい場合は、Release 作成後に `gh release edit <tag> --notes-file <ファイル>` で書き換える。
- **署名はしていない**: インストーラは未署名（`v0.1.0` の Release 説明にある「署名なし」の扱いを引き継いでいる）。SmartScreen の警告が出る前提で配布する。
- **`v0.1.0` は本ワークフロー導入前の手動リリース**: 2026-08-27 に公開された `v0.1.0` は、このワークフローができる前に手元でビルド・手動アップロードされたもの（確認: `gh release view v0.1.0` の `publishedAt` が 2026-08-27T10:03:07Z。`release.yml` は 2026-09-07 に新規作成しており、それより後）。過去のリリースを本ワークフローで作り直す作業はしていない。`v0.1.0` より後に打つタグから、このワークフローの成果物だけを使う運用にする。
- **同じタグに2度 Release は作れない**: `gh release create` は同名の Release が既にあると失敗する。作り直す場合は `gh release delete <tag>` してからタグも打ち直すか、`gh release upload` で成果物だけ追加する。

- 配布先の PC では、`%LOCALAPPDATA%\ChouhyoOCR\`（取り込んだ認証キー `cred.dpapi`・月次の送信回数 `api_usage.json`・中間データの既定の置き場）を **利用者本人だけが読めるフォルダ** にしておく。共有 PC や、`%LOCALAPPDATA%` に別グループの読み取り権限が継承されている環境では、配布前に `icacls %LOCALAPPDATA%\ChouhyoOCR` で権限を確認する（認証キーは Windows のユーザー単位で暗号化してあるので他のアカウントでは復号できないが、中間データには帳票の記入値が含まれる）。

## 確認済み（2026-09-07）

- `workflow_dispatch` で release.yml を実行し、venv＋依存 → Poppler → 配布物ビルド（PyInstaller）→ 鮮度スタンプの検査 → npm ci → Tauri ビルド → artifact 保存まで通ることを確認した（run 34088267394・所要 7 分 48 秒・artifact `chouhyo-ocr-installer-main` 40.4 MB）。
- 1 回目（run 34087471906）は起動確認ログの日本語で `UnicodeEncodeError` になった。Windows ランナーのコンソールは cp1252 なので、ワークフローの `env` で `PYTHONIOENCODING=utf-8`・`PYTHONUTF8=1` を固定し、build_dist.py も自分の出力を UTF-8 にした（4bf3597）。

## 未検証

- タグ push のときだけ動く「GitHub Release へ添付」（`gh release create`）のステップ。次の `v*` タグで確認する。
