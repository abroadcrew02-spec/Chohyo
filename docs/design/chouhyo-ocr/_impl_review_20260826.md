# 実装目線レビュー結果（Phase3 詳細設計への入力）

- 実施日: 2026-08-26
- 対象: `02_basic_design.md` v1.1（696行）／参照: `01_requirements.md` v3.0
- 位置づけ: 基本設計を Python + OpenCV + openpyxl + google-cloud-vision + SQLite + PyInstaller の構成で実装できるか検証した結果。詳細設計で解消する前提の指摘リスト
- 総評: 3フェーズ分割・Protocol境界（Aligner / EraMarkDetector / OutputFormatter / PlacementVerifier）・実物依存の隔離は成立しており、frozen dataclass + Protocol は Python として素直に実装できる。以下は「壊れている」ではなく「詳細設計で決めないと実装が手戻りする」項目

## Must（詳細設計に反映しないと実装で詰まる）

### M1. SQLite の一意キー（PK/UNIQUE）が §8.3 で未定義
§8.3 の6テーブルは「主なフィールド」の列挙のみで PRIMARY KEY・UNIQUE 制約がない。§4.2 は `remap` 再実行や送信リトライのたびに tokens/cells を INSERT すると書いているため、キー未定義のまま実装すると (a) `remap` を2回実行すると `cell` に同じ `field_id` の行が重複し `render` 時にどちらを使うか不定になる (b) `placement` は §9.5 で案C・案Aを同時に走らせるため `page_id` だけでは PK にできず、複合キーでないと上書きか一意制約違反になる。
- 対応: 各テーブルの PK/UNIQUE を明記する。想定は `page(page_id)` / `token(page_id, seq)` / `cell(page_id, field_id)` / `alignment(page_id, face)` / `placement(page_id, verifier_id)` / `api_call(page_id, attempt)`
- あわせて再実行時の書き込みが「DELETE WHERE page_id=? THEN INSERT」か「INSERT ... ON CONFLICT DO UPDATE（SQLite 3.24+ の UPSERT）」のどちらかを §4.2 に明記する。DELETE→INSERT を採る場合、その間の中断で中間状態が残る点を §9.2 の2フェーズマークと同様に記述する

### M2. `@dataclass(frozen=True)` に ndarray を持たせると `__eq__`/`__hash__` が壊れる
`AlignmentResult`（`transform`, `aligned_image`）・`EraCellContext`（`print_mask`）が frozen dataclass で ndarray フィールドを持つ。frozen=True は既定で eq=True となり `__eq__`/`__hash__` を自動生成するが、`ndarray == ndarray` は要素ごとの真偽値配列を返すため bool キャスト時に ValueError になる。pytest で `assert result == AlignmentResult(...)` と書いた時点で即座に踏む。`hash()` も TypeError。
- 対応: 対象クラスを `@dataclass(frozen=True, eq=False)` にするか、ndarray フィールドに `field(compare=False)` を付ける。テストでは配列部分を `np.testing.assert_array_equal` で別途比較する方針を明記

### M3. openpyxl `write_only=True` と「金額のみ数値・他は文字列・条件付き書式」の共存方法が未設計
§7.1 は性能のため write_only を指定しているが、write_only では `ws.append(list)` に生値を渡すだけでは各セルの `number_format`（文字列強制 `'@'`）を指定できない。列ごとに型を変える要求（01 §5.6・AC-46）を満たすには 208列すべてを `WriteOnlyCell(ws, value=..., style=...)` でラップする必要がある。知らずに実装すると「先頭ゼロが消える」不具合に直面してから書き込みロジックを全面書き直しになる。
- あわせて要確認セル数の COUNTIF 数式は行番号を自前で組み立てる必要があるが、write_only の append は行番号を自動採番するため、書き込み側で行カウンタを保持しないと数式の範囲参照がズレる
- 対応: 「208列 → WriteOnlyCell 生成 → 行カウンタで数式の行番号を追跡」の実装方針を明記。条件付き書式はワークシートレベルの設定のため write_only でも成立する見込みだが未検証 ※要確認。実装着手前に極小サンプル（3行×3列＋COUNTIF＋条件付き書式）で動作検証するステップを設ける

### M4. Windows資格情報ストアのペイロードサイズ上限と認証方式の組み合わせが未検証
Cloud Vision の認証は通常サービスアカウント JSON キー（概ね2〜3KB）を使うが、Windows の資格情報オブジェクトには1エントリあたりのサイズ上限がある ※要確認（具体値は要検証）。基本設計は「サービスアカウント JSON を丸ごと保存」なのか「API キー文字列を保存」なのか認証方式を明言していない（AC-36 の文言は API キー寄りに読める）。上限超過なら `keyring.set_password()` が実行時例外になり保存自体が成立しない。
- 対応: どちらの認証方式かを実装着手前に確定し、JSON を使うなら keyring への実地保存テストを最初に行う（超過時は DPAPI 経由でファイル暗号化する代替経路が要る）。この論点を §10 の保留表に一項目として追加する（現状どの保留にも該当しない）

### M5. PyInstaller `--onedir` 配布時の Poppler 実行パス解決が未設計
開発環境（`python main.py`）と PyInstaller ビルド後（exe）では実行ファイルの位置関係が変わる。リポジトリルート相対で `vendor/poppler/pdftoppm.exe` を組み立てると配布後に FileNotFoundError になり、開発中は再現しないため発見が遅れる。
- 対応: `sys.frozen` の有無で分岐し `sys.executable` の親ディレクトリを基準に絶対パスを組み立てて `subprocess.run` に渡す方針を明記する（`.bat` の PATH 設定に頼らない方が環境差異を排除できる）

### M6. PyInstaller + google-cloud-vision（grpc系）の同梱に既知の落とし穴
grpc は動的ロードされるプロトコルバッファ定義や TLS ルート証明書（certifi 由来）を持ち、PyInstaller は動的ロードのデータファイル・hidden import を検出できないことが多い ※要確認（バージョンの組み合わせでの発生有無は未検証）。開発環境では動くがビルド後の exe で SSL_ERROR / ModuleNotFoundError になる可能性がある。
- 対応: 本実装完了後ではなく疎通確認ができた時点で一度 PyInstaller ビルド→実行を試す「配布経路の早期疎通確認」を工程に組み込む。証明書同梱が要る場合は `--add-data` で `certifi.where()` の指すファイルを明示的に含める

### M7. Vision の confidence 取得可否が Q-18（機能種別選択）の判断材料に入っていない
§5.4 は Q-18 を「公式ドキュメント参照で決まる」としているが、判断基準に confidence を返すかどうかが含まれていない。`TEXT_DETECTION` の `text_annotations` はトークン単位の confidence を持たない（または信頼できない）ことがあり、word/symbol 単位の confidence が確実に取れるのは `DOCUMENT_TEXT_DETECTION`（`full_text_annotation` の階層構造）側と見られる ※要確認（公式ドキュメント確認と両モードのレスポンス実地検証が必須）。confidence が取れない種別を選ぶと AC-23（複数語は最小値）・M-14（閾値による〓化）が根拠データを失い、信頼度ベースの〓化という設計の根幹が成立しなくなる。
- 対応: P-09（今日確定可）の確認事項に「機能種別ごとの confidence 取得可否」を明記。あわせて DOCUMENT_TEXT_DETECTION はレスポンスが階層的（page > block > paragraph > word > symbol）で、`text_annotations[0]` が全文を表す TEXT_DETECTION とはトークンの組み立て方が異なる点を M-09 の実装メモに残す

## Should

- **S1. SQLite の journal_mode が未指定**: §9.2 に `synchronous=FULL` はあるが `journal_mode` がない。既定の DELETE モードでは書き込み中に読み取りがブロックされうる。`run` 実行中に別ターミナルで `status` を叩くのは自然な使い方で `database is locked` に遭遇しうる。接続時に `PRAGMA journal_mode=WAL` を設定する
- **S2. クライアント内蔵リトライと自前指数バックオフの二重化**: Google 公式クライアントは gRPC レベルの既定リトライ（`google.api_core.retry.Retry`）を持つことが多い ※要確認。M-09 の自前バックオフと二重に効くと待ち時間が伸び、リトライ回数の管理が二重帳簿になる。クライアント初期化時に既定 retry を明示指定（無効化 or M-09 のポリシーと一致）する方針を明記
- **S3. LogEvent の型制約は「オブジェクトをそのまま渡す事故」しか防げない**: §7.5 の伏字化は `f"{obj}"` パターンのみを防ぎ、`logger.info(f"value={token_box.text}")` のようにフィールドを取り出す書き方は型で防げない。`chouhyo_ocr` 内で `import logging` を `logging_safe.py` 以外で使わないことを lint ルールかレビュー項目として明文化する
- **S4. `__repr__`/`__str__` はクラス単位で固定文字列を返す実装にする**: `field(repr=False)` をフィールド単位で付ける方式はフィールド追加時の付け忘れリスクが構造的に残る。`def __repr__(self): return f"<{type(self).__name__} field_id={self.field_id} redacted>"` を1つ書く方式なら追加時に自動的に安全側へ倒れる
- **S5. Poppler の stderr がログ型制約の抜け道になる**: 実行失敗時の stderr を LogEvent にそのまま詰めると型で守っている構造を素通りする。abbreviate または固定エラーコード化してから渡す方針を M-05 に明記
- **S6. 画像書き込みと `page` 行 INSERT の順序が未明言**: §4.1 の図は「F3 ページ展開 → W1 page行INSERT」に見えるが、「DB に page 行が存在する ⟹ 対応する画像ファイルは書き込み完了している」という不変条件を1文で明記する（崩れると align が存在しない画像を読みに行く）
- **S7. Ctrl+C の伝播が .bat + PyInstaller onedir 構成で機能するか要実機検証**: Windows のコンソールイベント伝播は SIGINT と挙動が異なり、`.bat` 経由の子プロセス（exe、さらに pdftoppm）まで届くかは検証が要る ※要確認。実装後の早い段階で「run 実行中に Ctrl+C」を試し `state=sending` で正しく止まることを確認する（AC-29 の検証に直結）

## 問い（詳細設計で決める）

1. **`api_call` テーブルは必要か**: 送信回数の累計は `page.attempt` の SUM でも代替できる。`http_status` を1回ごとに残す価値（何回目の送信で失敗したかを追える）と、1人・一度きり運用でテーブルを1つ削る利点を比較して判断する
2. **`verify` コマンドの Poppler 実行可否検証の方法**: `pdftoppm -v` を実際にサブプロセス起動するのか、ファイルの存在確認だけか
3. **`Point` 型が §3.0 で未定義**: `Quad.p: tuple[Point, Point, Point, Point]` と参照されているが定義がない。NamedTuple / dataclass / `tuple[float, float]` のどれにするか明記する（放置すると実装者ごとに別の型ができる）
4. **JSON列（`era_scores`・`transform`・`row_anchor_misses`）の扱い**: SQLite の TEXT 列に JSON 文字列として格納し Python 側で `json.loads`/`json.dumps` する想定でよいか。JSON1 拡張でクエリ内から検索する必要がなければ TEXT で十分

## 注記
※要確認を付けた項目（M4/M6/M7/S2/S7）はライブラリ・OS挙動の記憶に基づく判断のため、実装着手前に一次情報での確認を要する。
