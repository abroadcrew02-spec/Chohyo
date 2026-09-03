# 設計メモ: 枠判定の自動化（07 v1.2 の実装設計）

## 0. 文書情報

- 文書ID: chouhyo-ocr-08
- 版数: `v0.1`（§1・§2 のみ確定。後続段は見出しのみ）
- 最終更新: 2026-09-02
- 上位文書: [07_frame_detection_requirements.md](07_frame_detection_requirements.md) v1.1（**要件の正本**。本書は実装方針のみを書き、要件を再定義しない）
- 関連: [02_design.md](02_design.md)（D-15・D-25・D-26・§6.2）／[03_test_requirements.md](03_test_requirements.md) §2・§3（テストレベルと回帰ゲート基準値の正本）／[05_output_columns_requirements.md](05_output_columns_requirements.md)（列契約）
- GitHub Issue: #77（ログ匿名化・§1）／#71（(a') 既存判定の接続・§2）

### 0.1 本書の範囲

| 段 | 対象 | 本書の状態 |
|---|---|---|
| #77 | ログの匿名化（FR-F50・AC-F65） | **§1 に設計を確定** |
| #71 (a') | 既存判定の接続・3値化・理由コード分離・記録（FR-F01〜F13・F45／AC-F01〜F15） | **§2 に設計を確定**（実装済み: core 5a3b660 / GUI 1541239） |
| #72 (t) | テンプレートの保存・選択・照合提示 | **§3 に設計を確定**（実装済み: #72 クローズ） |
| #73 (b) | ページ全体からの枠候補生成 | **§4 に設計を確定**（FR-F19 と切り抜きの衝突は §7-10 の判断待ち） |
| #74 (c) | 位置合わせ残差・吸着量の記録 | §5・未着手 |
| #75 (f) | 実行時のブロック単位吸着 | §6・未着手 |

**§7 に、07 v1.3 と本設計の食い違い（要件側の修正提案）を列挙する。** 7-1〜7-9 は (a')・(t) の設計時に出したもので要件へ反映済み。**未決は 7-10〜7-13 の4件**で、7-10（既存枠と切り抜きの衝突）は (b) の実装着手前に結論が要る。

### 0.2 コード実測の証跡

本書が「現状こうなっている」と書く箇所は、2026-09-02 に作業ツリーのソースを直接読んで確認した。参照はファイル:行で示す。**テストの実行はしていない**——「このテストが赤くなる」という記述はすべて `※未検証（実測が要る）` と明示する。

⚠️ **執筆中に作業ツリーが動いた。** 起点は `22bf942`（clean）だったが、本書の執筆と並行して **#77（ログ匿名化）の実装** と **#70 前提作業（同寸別様式素材 `testdata/formC/`）** が未コミットで入った（`git status` で確認・2026-09-02）。したがって:

- **§1 は「これから設計するもの」ではなく「入った実装の記録＋残課題」として書き直した**（実装済みの箇所は現物のコードを正とする）
- **§2 は `testdata/formC/README.md` の実測値を取り込んだ。** この実測により **07 §4.1 の対応表そのものに穴があることが判明した**（§7-1）。設計の中核に関わるため、実装着手前にユーザー判断が要る

---

## 1. #77 ログの匿名化（FR-F50・AC-F65・07 §0.6）

**本節は「これから設計するもの」ではない。** 2026-09-02 時点の作業ツリー（未コミット）に実装が入っており、本節はその内容の記録と、**残っている3つの穴**を示す。実装の現物が正。

### 1.1 実装済みの内容（作業ツリー・2026-09-02・未コミット）

| ファイル | 変更 |
|---|---|
| `logging_safe.py` | `_ALLOWED_KEYS` から `field_id`・`template_path` を削除。`cell_idx`・`col_idx` を追加。docstring に Q-S1 の経緯と適用範囲を記載 |
| `cli.py:53-59` | `run_start` の `template_path=` を廃止し、テンプレート JSON を1回読んで `align.template_hash()` を計算し `template_hash=` を載せる |
| `mapping.py:457-525` | `assign()` の内部で `idx_by_field_id = {c.field_id: i for i, c in enumerate(cells)}` を作り、`fallback_used`／`fallback_conflict`／`fallback_discarded`／`carve_hole` の4箇所を `cell_idx=` へ差し替え |
| `pipeline.py:99-120` | `_warn_risky(risky, columns)` へ引数追加。`csv_formula_risk` を **`col_idx`**（抽出対象列＝`columns` から管理6列を除いた並びの 0 始まり序数）へ差し替え |
| `template.py:328-372` | `_exclusion_overlap_warnings` を `enumerate(cells)` に変え、W-1・W-2 の4箇所を `cell_idx=` へ差し替え。GUI 向けの `warnings` 文字列は欄名を含めたまま（stdout は対象外） |
| `test_leak_guards.py` | 既存2件を反転（`template_path` を出さないことの確認へ）＋ AC-F65 のテストを追加 |
| `.gitignore` | `/templates_user/` の明示行（07 §9.4・(t) の前提作業） |

**`run_start` の扱いが本書の初稿と違う。** 初稿は「ハッシュ計算のためにテンプレートを二重に読むのは避け、直後の `template_loaded` に任せる」としたが、実装は run_start でテンプレート JSON を読んでハッシュを載せる方を採った。**実装側を正とする**——`run_start` 単独で追跡でき、`template_loaded` へ到達する前に落ちた run でも由来が残る。コストはテンプレート JSON 1回の読み込みで、run の入口の1回だけ。

**最終決定（2026-09-02・#77 追補・レビュー指摘を受けて明記）**: `run_start` にはハッシュを載せず `path=` のみとする。テンプレートの由来は直後の `template_loaded`（`template_hash`）で残す。ロック取得前に落ちた run は序数のログも出ないため不変条件 A は破れない。二重読みを避ける判断を優先した。

### 1.2 許可キーの現在値

```
source_file, page_no, page_id, step, error_code, conf, count, duplicate_of,
path, state, status, attempt, template_hash, cell_idx, col_idx,
sx, sy, kept, failed, timestamps
```

方針は「**名前を持つキーを1つも許可しない。位置・序数・件数・ハッシュ・列挙値のみ**」。`source_file`・`path`・`duplicate_of` は**入力帳票**のファイル名・パスで、Q-S1 の対象（テンプレート名・欄名）には当たらないため残っている（§7-4 に確認事項として挙げる）。

### 1.3 序数の語彙と不変条件

実装は**2つの序数**を使っている。用途が違うので混ぜない。

| キー | 定義 | 使う場面 |
|---|---|---|
| `cell_idx` | `template.cells` 内の 0 始まり序数（物理的な欄） | `mapping.py` の4イベント・`template.py` の W-1／W-2 |
| `col_idx` | **抽出対象列**（`columns` から管理6列＝`META_COLUMNS` を除いた並び）の 0 始まり序数 | `csv_formula_risk`（危険接頭は「列」単位で出る） |

`cell_idx` と `col_idx` は**同じ空間ではない**（1欄が `subfields` で複数列に分かれる・D-23）。取り違えを防ぐため `logging_safe.py` の docstring に両方の定義がある。

**不変条件 A**: 序数は **`template_hash` とセットでのみ意味を持つ**。テンプレートを1欄でも足すと序数がずれる。「序数を出すコマンドは、同じ run のログに `template_hash` を1行残す」ことが要る。§1.4 の穴 #2 はこれが破れている経路。

**不変条件 B**: ログへ新しいキーを足すときは `_ALLOWED_KEYS` を同時に更新する。守られていないと**黙って落ちる**（§1.4 の穴 #1 が実例）。

### 1.4 残っていた穴（3件・2026-09-02 対応済み）

#### 穴 #1: W-3／W-4 の診断が全部落ちている（FR-F50 が警告した故障が既に発生）

`log.info/warn/error` の呼び出し45件（`grep -rc "log\.\(info\|warn\|error\)(" core/chouhyo_ocr/` の合計・2026-09-02）を走査したところ、**白リストに無いキーだけを渡している行が2つ残っている**。

| 行 | イベント | 渡しているキー | 実際にログへ出る内容 |
|---|---|---|---|
| `template.py:454` | `adjacent_gap_w3` | `face_id`・`field_a`・`field_b`・`gap_px` | **イベント名のみ**（4キーすべて白リスト外） |
| `template.py:491` | `hole_overlap_w4` | `face_id`・`field_a`・`field_b` | **イベント名のみ**（同上） |

W-3（欄間の隙間＝どの欄にも入らない死角）と W-4（穴の重なり＝配列順で割付先が変わる）の診断は、**app.log 上では「何かが起きた」以上の情報を持っていない**。#77 が持ち込んだ退行ではなく以前からの状態だが、FR-F50 が「白リストから外すと黙って落ちて診断が効かなくなる」と警告したのとまったく同じ故障が既に2件成立している。

**対応案**: `face_id` → `face_idx`（`template.faces` 内の 0 始まり序数）、`field_a`／`field_b` → `cell_a`／`cell_b`（`cell_idx` と同じ空間）、`gap_px` はそのまま。3キーを白リストへ追加する。GUI へ返す `warnings` 文字列は従来どおり欄名を含めたままにする（stdout は対象外）。

#### 穴 #2: `verify`・`expand-page` に `template_hash` が出ないため `cell_idx` を復号できない

`template_loaded` を出しているのは `pipeline._load`（`pipeline.py:315`）だけで、`run`／`render`／`remap` の3経路しか通らない。`cmd_verify`（`cli.py:122`）と `cmd_expand_page`（`cli.py:262`）は `load_template` を直接呼ぶ。

- W-1／W-2 は `load_template` の内部で発火するので、**`verify` を実行すると `exclusion_overlap_w1 cell_idx=137` が app.log に出るが、同じログのどこにも `template_hash` が無い**
- 不変条件 A が破れており、この行から欄を特定できない（どのテンプレートの 137 番目かが分からない）

**対応案**: `cmd_verify`・`cmd_expand_page` の `load_template` 直後に `log.info("template_loaded", template_hash=...)` を足す。`run_start` と同じ計算方法を使う。

#### 穴 #3: 白リストの静的検査が無い

穴 #1 は「テストが1件あれば防げた」種類の故障。**`core/chouhyo_ocr/*.py` を AST で走査し、`log.info/warn/error` の全キーワード引数名が `_ALLOWED_KEYS` に含まれることを検査する unit テスト**を足す。

- 「白リストへ足さずにキーを増やす」を機械的に止める（不変条件 B の実装）
- 追加時点では**赤になる**（穴 #1 の2行が引っかかる）。穴 #1 と同じ変更で直して緑にする
- FR-F50 が名指しした故障モードに対する唯一の恒久的な歯止め

**対応（2026-09-02・commit 2cd1c77 と #77 追補）**: 穴 #1 は `face_idx`・`cell_a`／`cell_b`・`gap_px` を白リストへ追加して W-3/W-4 を復活（テストで固定）。穴 #2 は `verify`・`expand-page`・`debug-images` でも `template_loaded`（`template_hash`）を出力（`validate_v1` の前）。穴 #3 は AST 静的検査を `test_leak_guards.py` に追加（`from .logging_safe import warn` 形式の裸呼び出しも対象・`**kwargs` 展開は違反扱い・出力関数名は動的取得・`rglob`）。あわせて経路別（run／remap／verify／expand-page）の不変条件 A テストを追加した。

### 1.5 テスト（実装済み＋追加提案）

| 状態 | テスト | 内容 |
|---|---|---|
| 実装済み | `test_leak_guards.py:28` | `run_start` に `template_path=` が出ない／`template_loaded template_hash=` は出る |
| 実装済み | `:70` | `_fmt` が `template_path`・`field_id` を落とし、`template_hash`・`cell_idx`・`col_idx` を通す |
| 実装済み | `:110` | 危険接頭の警告に記入値も列名も入らない |
| 実装済み | `:141` | AC-F65: 機微な名前を持つテンプレートで実行してもログに名前が出ない |
| **追加提案** | 新規 | 穴 #3 の静的検査（AST 走査） |
| **追加提案** | 新規 | 穴 #2 を塞いだ後の確認: `verify` の app.log に `template_hash` と `cell_idx` が**同時に**存在する |

テストは実行済み（2026-09-02・#77 追補後）: `pytest tests/test_leak_guards.py -q` → 9 passed。上表の「実装済み」は緑を確認したもの。

---

## 2. #71 (a') 既存判定の接続・3値化・理由コード分離・記録

### 2.1 全体の構成

```
                  ┌─────────────────────────────────────┐
                  │ align.estimate_shift（既存・変更なし）│
                  │  det_h/det_v・exp_h/exp_v・matched/total │
                  └───────────────┬─────────────────────┘
                                  │ ShiftEstimate（フィールド追加のみ）
                                  ▼
                  ┌─────────────────────────────────────┐
                  │ format_check.py（新規・純関数）       │
                  │  classify()  3値＋理由コード          │
                  │  fold()      面 → ページ             │
                  │  check_page() 画像＋テンプレ → 判定    │
                  └───────┬──────────────┬──────────────┘
                          │              │
        run（pipeline.py）│              │ expand-page（cli.py）
                          ▼              ▼
             status＋reason_code    JSON: verdict/score/faces
             store の format 列      ─────────────┬─────────
             log: format_verdict                 │
                                                 ▼
                                    GUI Editor.tsx / RunScreen.tsx
```

**判定そのものは新規に作らない。** `estimate_shift`（`align.py:83-138`）が既に計算している値を分類し直すだけで、画像処理は1つも増えない（07 §9.1）。

### 2.2 `align.py` の最小拡張

#### 2.2.1 `ShiftEstimate` へ足すフィールド（既定値つき・既存の値は触らない）

```python
@dataclass(frozen=True)
class ShiftEstimate:
    dx: int
    dy: int
    matched: int          # 既存・変更なし（連結リスト基準）
    total: int            # 既存・変更なし
    ok: bool              # 既存・変更なし
    reason: str           # 既存・変更なし
    # --- 以下は FR-F45／FR-F01 のスコア用。判定には一切使わない ---
    det_h_count: int = 0      # len(det_h)（重複排除済みの検出線・水平）
    det_v_count: int = 0      # len(det_v)
    exp_h_uniq: int = 0       # len(set(exp_h))（重複排除した期待線・水平）
    exp_v_uniq: int = 0       # len(set(exp_v))
    matched_uniq: int = 0     # 重複排除した期待線のうち best shift で当たった本数
    at_boundary_h: bool = False   # _axis_shift の by（few_lines で早期 return しても保持）
    at_boundary_v: bool = False   # 同 bx
```

- 5つの数値はすべて `estimate_shift` が**既に持っている集合・リストから O(n) で得られる**。追加の走査・画像アクセスはゼロ
- `matched_uniq` の計算は `sum(1 for e in set(exp_h) if (e+dy) in det_h or (e+dy-1) in det_h or (e+dy+1) in det_h)` を両軸分（`dy`/`dx` は `_axis_shift` が返した best shift）。**±1 の許容は既存 `_axis_shift` と同一**にする（別の許容にすると2つの一致率が別物になる）
- `at_boundary_h/v` は `_axis_shift` の戻り値 `by`／`bx` をそのまま格納する。**現在は `few_lines` で早期 return するとこの値が捨てられている**（`align.py:117-120`）が、計算はその前に済んでいる（:113-114）。§2.3 の分類がこれを使う
- **既定値を持たせる理由**: `ShiftEstimate(dx, dy, matched, total, False, "few_lines")` の形の位置引数生成が `align.py` 内に5箇所、テストにも生成があるため。既定値なしだと全箇所の書き換えが要る

**NFR-F08（吸着 OFF で出力が着手前と一致）の担保**: 追加したフィールドは `estimate_shift` の中でも `align_page` の中でも**一度も条件分岐に使われない**。`ok`・`reason`・`dx`・`dy`・`matched`・`total` の算出経路は1行も変えない。

#### 2.2.2 `AlignedFace` へ足すフィールド

```python
@dataclass(frozen=True)
class AlignedFace:
    ...  # 既存（face_id/image/binary/angle/dx/dy/shift_matched）は変更なし
    estimate: "ShiftEstimate | None" = None   # 成功時の推定結果（記録用・FR-F12）
```

成功ページのスコアを記録するために要る。`shift_matched` は既存の互換のため残す。

#### 2.2.3 `AlignError` へ診断を持たせる

```python
class AlignError(RuntimeError):
    def __init__(self, code: str, diag: "tuple[FaceDiag, ...]" = ()):
        super().__init__(code)
        self.code = code
        self.diag = diag        # 面ごとの (face_idx, face_id, ShiftEstimate|None)
```

- `align_page` は失敗した面で即 `raise` する（`align.py:357`）現在の挙動を**変えない**。理由: 失敗面が1つ確定した時点でページの判定は「不一致」または「判定不能」に確定する（FR-F03 の畳み込みで一致には戻らない）ので、残りの面を評価する意味が無く、NFR-F01 の予算を使うだけ
- 評価に至らなかった面は `FaceDiag(face_idx, face_id, estimate=None)` として `skipped` を記録する（黙って欠落させない）
- 既存の `except AlignError:`（`cli.py:292`・`pipeline.py:515`）は引数追加の影響を受けない
- `PageSizeMismatch` は `align_page` の入口（`align.py:317-319`）で送出され、面の診断を持たない。判定は `mismatch(size)` で確定するため診断は不要

### 2.3 純関数モジュール `core/chouhyo_ocr/format_check.py`（新規）

#### 2.3.1 型

```python
Verdict = Literal["match", "mismatch", "undecidable", "skipped", "unknown"]

@dataclass(frozen=True)
class FaceVerdict:
    face_idx: int
    face_id: str
    verdict: Verdict
    reason: str          # "" | "lines" | "ambiguous" | "edge" | "few_lines" | "boundary" | "size"
    score: float         # [0,1]。算出できないとき -1.0
    detected: int        # det_h_count + det_v_count
    expected: int        # exp_h_uniq + exp_v_uniq

@dataclass(frozen=True)
class PageVerdict:
    verdict: Verdict
    reason: str
    score: float         # 面の最小値（最も悪い面がページを代表する）
    faces: tuple[FaceVerdict, ...]
```

#### 2.3.2 スコア（FR-F01・無次元・テンプレート間比較可能）

```python
FEW_LINES_DETECT_RATIO = 0.5   # FR-F45 の暫定閾値（Q-F6 の較正対象）

def score_of(est: ShiftEstimate) -> float:
    denom = est.exp_h_uniq + est.exp_v_uniq
    return est.matched_uniq / denom if denom else -1.0
```

- 分母は**重複排除した期待線の位置数**（07 FR-F01）。実測値は front 16・back 26・formB 10（07 §5.1 に記載の値）
- **`matched`（連結リスト基準）をこの分母で割らない。** front は完全な紙で `matched=22`・重複排除分母 16 なので 1.375 となり定義域 [0,1] を外れる。分子も重複排除した `matched_uniq` を使うことで初めて比較可能になる
- **判定には使わない**（07 FR-F01 の「スコアと判定閾値を書き分ける」）。判定は既存の `need_y`／`need_x` のまま

#### 2.3.3 3値分類（07 §4.1 の対応表を条件式にしたもの）

実測に基づき、**07 §4.1 の対応表から2点を変える**（根拠は §2.3.4・提案は §7-1／§7-2）。

```python
def classify(est: ShiftEstimate) -> tuple[Verdict, str]:
    if est.ok:
        return "match", ""
    if est.reason == "boundary":
        return "undecidable", "boundary"
    if est.reason == "edge_mismatch":
        return "undecidable", "edge"          # ★1 07 は「不一致」。実測で覆した
    if est.reason == "ambiguous":
        return "mismatch", "ambiguous"
    if est.reason == "few_lines":
        # ★2 分母・分子を軸別に見る（07 は両軸合算）
        sparse_h = est.exp_h_uniq > 0 and est.det_h_count < est.exp_h_uniq * FEW_LINES_DETECT_RATIO
        sparse_v = est.exp_v_uniq > 0 and est.det_v_count < est.exp_v_uniq * FEW_LINES_DETECT_RATIO
        if sparse_h or sparse_v:
            return "undecidable", "few_lines"     # 線が取れていない
        if est.at_boundary_h or est.at_boundary_v:
            return "undecidable", "boundary"      # ★3 探索境界に張り付いている
        return "mismatch", "lines"                # 線はあるのに期待位置と合わない
    return "undecidable", est.reason or "unknown"  # 未知の reason は安全側
```

`page_size_verdict` 不一致は `estimate_shift` に到達しないため、呼び出し側が
`PageVerdict("mismatch", "size", score=-1.0, faces=())` を直接組む。

#### 2.3.4 3つの変更の根拠（`testdata/formC/README.md`・2026-09-02 実測）

**★1 `edge_mismatch` を「不一致」から「判定不能」へ倒す**

`workdir/pages/sample-1.png`（本物の紙）の back/detail の水平罫線を上から N 本白塗りした実測:

| N | det_h | matched/total | reason |
|---:|---:|---:|---|
| 0 | 25 | 38/42 | （成功） |
| 1 | 24 | 36/42 | **edge_mismatch** |
| 〜7 | 14 | 24/42 | edge_mismatch |
| 8 | 13 | 22/42 | few_lines |
| 15（全消し） | 2 | 10/42 | few_lines |

**上端の罫線を1本消しただけで `edge_mismatch` に転じる。** 07 の対応表どおり「不一致」に置くと、**上端が1本かすれた本物の紙で枠が消える**——07 §9.1 が「判定不能は不一致ではない」と書いて最も避けたかった事態そのもの。`edge_mismatch` は非周期アンカー検査（`align.py:139-151`）で、1行ズレのエイリアシングを潰すために意図的に敏感に作られており、**「別の紙」ではなく「端の線が読めなかった」で発火する頻度の方が高い**。

中核要望（無関係な紙で枠を描かない）は損なわれない。同寸別様式 formC を出荷テンプレートに通した実測は **両面とも `few_lines`**（front matched 2/22・det 30 vs 期待16／back matched 7/42・det 17 vs 期待26）で、`edge_mismatch` には到達しない。無関係な紙は一致本数の下限（`need`）を通れないため、必ず `few_lines` で落ちる。

**★2 `few_lines` の二分を軸別にする**

07 FR-F45 は検出線を両軸合算（`len(det_h) + len(det_v)`）で数え、重複排除期待線の合算と比べる。しかし **`det` にはテンプレートに無い線も入る**（formC front は det 30 に対し期待 16＝187%）。かすれの実測では水平線しか消していないため `det_v` は不変で、合算比は 50% を割りにくい。

| 基準 | sample-1 back の 50% 割れ | 判定 |
|---|---|---|
| 合算（07 の指定） | det_h+det_v < 13 が必要。det_h は全消しで 2 まで落ちるが `det_v` は不変（実測値は未取得） | **成立しない可能性が高い**＝AC-F03 の素材条件が作れない |
| 軸別（本設計の提案） | det_h < 7.5 → **N=12（det_h=7）が最小** | 成立する |

軸別にすると AC-F03 の「検出線の本数が期待線本数の 50% を下回る最小の N」が **N=12** と確定でき、テストの刺激が作れる。※`det_v` の実測は未取得（§7-2 の確認事項）。

**★3 `few_lines` かつ検出十分かつ探索境界**

`few_lines` は「**一致本数**が下限未満」の条件（`align.py:117-120`）なので、**探索範囲を超えてズレただけの正しい紙**もここへ落ちる。その紙は罫線が豊富で「検出十分」に分類され、★2 を通っても「不一致」になる。07 が `boundary` を判定不能に置いた理由（「大きくズレただけの正しい紙で枠が消える」）は、`few_lines` が先に発火するせいで**対応表のままでは機能しない**。`at_boundary` は `_axis_shift` が既に計算しており（`align.py:73`）取得は無料。

**★1〜★3 を入れてもなお残る穴**: かすれが進んだ本物の紙（sample-1 の N=8〜11）は `few_lines` かつ軸別でも検出十分（det_h 8〜13 vs 期待 15）となり、**「不一致」に分類されて枠が消える**。検出線の本数は「線が見えているか」の代理指標として弱く、これを埋めるには一致率（スコア）を判定に使うしかないが、07 FR-F01 が「スコアは判定に使わない」と定めている（較正の母集団が無いため）。**残存リスクとして 07 §3.5 相当に明記してもらう**（§7-2）。

#### 2.3.5 面 → ページの畳み込み（FR-F03）

```python
def fold(faces: Sequence[FaceVerdict]) -> PageVerdict:
    # 優先順: mismatch > undecidable > match（片面でも不一致ならページは不一致）
```

`skipped`（評価に到達しなかった面）は判定に影響しない。全面が `skipped` はありえない（1面目で必ず評価が走る）。

#### 2.3.6 `check_page`（FR-F13・AC-F15）

```python
def check_page(page_img: Image.Image, template: Template) -> PageVerdict:
    """ページ画像＋テンプレート1つ → 3値判定。面切りは内側で行う。"""
```

- 実装は `align_page` の**推定部分だけ**を共有する。`align_page` の面ループから
  「padded crop → 傾き推定 → 回転 → Otsu → `estimate_shift`」を
  `_face_estimate(padded, face, template, pad) -> ShiftEstimate` として切り出し、
  `align_page` と `check_page` の両方がこれを呼ぶ
- `check_page` は `estimate_shift` の後で打ち切る（本二値化・マスク・composite への貼り付けをしない）。(t) の N テンプレートループ（NFR-F09: 合計 3.0 秒以内）で無駄な画像生成を避けるため
- **`align_page` の数値経路を変えないこと**が必須（NFR-F08）。切り出しは純粋な関数抽出に留め、順序・定数・引数を1つも変えない。担保は AC-F45（golden 比較）と `test_alignment_robustness.py`（4件）
- 代替案（不採用）: `check_page` を `align_page` の呼び出しラッパにする。実装は最小だが、テンプレート1件あたり本二値化・マスク・PNG 相当のメモリ確保が走り、(t) の 20 件ループで NFR-F09 を割る見込みが高い（※未計測）。加えて `AlignError` を制御フローに使うことになり、AC-F14 の「例外がバケツに化けない」歯止めと相性が悪い

### 2.4 run 側の配線（FR-F09・FR-F10・FR-F11・AC-F14）

#### 2.4.1 現状（実測）

`pipeline.py:483-521`。`page_size_verdict` → `_restore_alignment`（再利用）→ `align_page` の順で、例外は2つに分岐する。

| 例外 | 現在の status | カウンタ |
|---|---|---|
| `PageSizeMismatch` | `様式不一致` | `summary.format_mismatch += 1` |
| `AlignError` | `位置合わせ失敗` | `summary.align_failed += 1` |

`STATUS_FORMAT_MISMATCH`（`様式不一致`）を立てる箇所は4つある（`pipeline.py:509`・`581`・`598`・`721`）。これが 07 FR-F09 が言う「共用バケツ」。

#### 2.4.2 変更後

```python
except PageSizeMismatch:
    _record_and_fail(pid, PageVerdict("mismatch", "size", -1.0, ()), pre_send=True)
    continue
except AlignError as e:
    try:
        pv = format_check.from_diag(e.diag)      # 純関数・例外を出さない
    except Exception as ex:                       # AC-F14 の歯止め
        log.error("format_check_failed", page_id=pid, error_code=type(ex).__name__)
        log.error_trace("format_check_failed", traceback.format_exc())
        pv = None
    if pv is not None and pv.verdict == "mismatch":
        store.set_status(pid, render_rows.STATUS_FORMAT_MISMATCH)
        store.set_status_reason(pid, "frame_" + pv.reason)
        summary.format_mismatch += 1
        summary.format_mismatch_pre_send += 1     # FR-F10 の「送信前に止まった」件数
    else:
        store.set_status(pid, render_rows.STATUS_ALIGN_FAILED)   # 判定不能・判定失敗は現行維持
        store.set_status_reason(pid, "frame_" + (pv.reason if pv else "check_failed"))
        summary.align_failed += 1
    store.set_format_result(pid, pv)             # 判定不能でもスコアは残す（FR-F12）
    continue
```

**AC-F14 の歯止めの要点は「例外時に `様式不一致` へ倒さない」こと。** 判定関数が壊れると全ページが `様式不一致` になり、それは「新機能が完璧に働いている」見え方と区別できない。例外時は現行バケツ（`位置合わせ失敗`）へ落とし、`format_check_failed` イベントとスタックを残す（既存の `row_build_failed`・`pipeline.py:717` と同型）。

**成功側**: `align_page` が返った後、`faces[i].estimate` から `PageVerdict("match", ...)` を組んで `store.set_format_result` を呼ぶ。一致ページも記録する（FR-F12・AC-F13）。

**再利用ページの扱い**（`reused is not None`・`pipeline.py:526`）: `estimate_shift` を走らせないため推定値が無い。**再計算はしない**（#45 の再利用は「整列をやり直さない」ことに価値があり、判定のために整列相当の計算を回すと意味が失われる）。既に前回の run で書いた `format_*` 列が page 行に残っているのでそれを保持し、無い場合（本機能より前に整列済み）は `verdict="unknown"` を記録して `log.info("format_check_skipped_reuse", page_id=pid)` を出す。※ AC-F13「全ページ」との関係は §7-3 に確認事項として挙げる。

#### 2.4.3 理由コードの一覧（FR-F09 の「分離」）

> `row_build_failed` は**未配線**（2026-09-02・(a') 実装時の判断）: `_render_locked` は `page.status` をローカル辞書にしか書かず DB を更新せず、render 経路にはページ単位の progress イベントも無い。片側だけ永続化すると成功時にクリアする経路が無く新しい残留を生むため、render 側の状態更新設計が別途要る。GUI は未知コードを注記なしで扱うので実害なし。

> 未知の reason（将来 `estimate_shift` に理由が増えた場合）は `classify` が安全側の `undecidable` と `frame_<reason>` で返す。GUI（`REASON_CODE_JA`）は未知コードを注記なしで表示するので壊れない。10 コードの表を更新するのは、理由を追加した本人の責務。

`page.status_reason` に入る値。**`page.status` の既存値（8種）は1つも変えない。**

| status | status_reason | 発火点 | 送信前/後 |
|---|---|---|---|
| 様式不一致 | `frame_size` | `PageSizeMismatch`（`pipeline.py:502`） | 前 |
| 様式不一致 | `frame_lines` | `few_lines` かつ検出十分かつ境界でない | 前 |
| 様式不一致 | `frame_ambiguous` | `ambiguous` | 前 |
| 様式不一致 | `map_failed` | `_map_and_score` の構造異常（`pipeline.py:581`） | 後 |
| 様式不一致 | `outside_ratio` | D-15 枠外率（`pipeline.py:598`） | 後 |
| 様式不一致 | `row_build_failed` | 行組み立て失敗（`pipeline.py:721`） | 後 |
| 位置合わせ失敗 | `frame_few_lines` | `few_lines` かつ**軸別**の検出が乏しい（★2） | 前 |
| 位置合わせ失敗 | `frame_edge` | `edge_mismatch`（★1・07 は不一致に置いている） | 前 |
| 位置合わせ失敗 | `frame_boundary` | `boundary`（★3 の分岐を含む） | 前 |
| 位置合わせ失敗 | `frame_check_failed` | 判定関数の例外（AC-F14） | 前 |

`summary` に `format_mismatch_pre_send` を1つ足す（`frame_*` かつ `様式不一致` の件数）。既存の `format_mismatch`（原因不問の総件数・`pipeline.py:37-39`）は意味を変えない。

### 2.5 記録（FR-F12・AC-F13）

#### 2.5.1 保存先の決定

**`page` テーブルへ列を4つ足す。** `alignment` テーブルは使わない。

| 案 | 内容 | 判断 |
|---|---|---|
| A（採用） | `page` に `format_verdict`・`format_reason`・`format_score`・`format_detail`・`status_reason` を追加 | 失敗ページにも必ず行がある（`page` は取り込み時に作られる）。既存の `_ensure_column` マイグレーションに素直に乗る |
| B（不採用） | `alignment.transform` の JSON へ相乗り | **失敗ページには `alignment` 行が作られない**（`upsert_alignment` は成功パスのみ・`pipeline.py:530-537`）。不一致ページを記録するには `ok=0` の行を新設することになり、`_restore_alignment` の再利用判定（#45）に新しい分岐を持ち込む。中間データ再利用は課金に直結する経路で、記録のために触る場所ではない |
| C（不採用） | 新テーブル `page_format` | 1対1の関係にテーブルを増やす理由が無い。join が要るだけ |

#### 2.5.2 スキーマ変更と migration 方針

```sql
-- Store.__init__ の _ensure_column で追加（store.py:96-107 と同じ流儀）
ALTER TABLE page ADD COLUMN format_verdict TEXT NOT NULL DEFAULT '';   -- '' = 未計測（旧版データ）
ALTER TABLE page ADD COLUMN format_reason  TEXT NOT NULL DEFAULT '';
ALTER TABLE page ADD COLUMN format_score   REAL NOT NULL DEFAULT -1;   -- -1 = 未計測
ALTER TABLE page ADD COLUMN format_detail  TEXT NOT NULL DEFAULT '';   -- faces[] の JSON
ALTER TABLE page ADD COLUMN status_reason  TEXT NOT NULL DEFAULT '';
```

- **`ALGO_VERSION` は "2" のまま上げない。** 上げると既存の中間データの整列結果が全部捨てられ、再整列が走る（`_restore_alignment` の判定材料）。(a') は読み取りアルゴリズムを変えないので上げる理由が無く、07 §7.2-4 が許容する直値更新は (f) の分（"2"→"3"）だけ
- `format_detail` は `[{"face_idx":0,"verdict":"mismatch","reason":"lines","score":0.18,"detected":21,"expected":16}, ...]`。**`face_id`（名前）は入れず `face_idx` で持つ**（§1.4 の語彙に揃える。中間データは秘匿対象外だが、語彙を2つ持たない）
- `store.set_status(pid, "")`（成功時・`pipeline.py:606`）は `status_reason` に触れない。成功時は `set_status_reason(pid, "")` を明示的に呼ぶ

#### 2.5.3 ログイベント

> `format_verdict` の 5 値（verdict／reason_code／score／detected／expected）は必ず同一の代表面（verdict 優先順で最悪の面グループのうちスコア最小の面）から取る（M-5・2026-09-02）。`size` 不一致は判定前に落ちるため detected／expected が 0 で出る。score −1.0 は「未計測」の印で、「計測して 0 本」とは score でのみ区別する。

```
format_verdict page_id=<id> verdict=match reason_code= score=0.98 detected=30 expected=26
format_verdict page_id=<id> verdict=mismatch reason_code=lines score=0.18 detected=21 expected=16
format_check_skipped_reuse page_id=<id>
format_check_failed page_id=<id> error_code=<型名>
```

キーはすべて §1.2 で白リストへ足したもの（`verdict`・`reason_code`・`score`・`detected`・`expected`）。**名前は1つも出ない**（FR-F12 の後半）。

### 2.6 `expand-page` の JSON 契約（後方互換）

現在の出力（`cli.py:305-309`）:

```json
{"event":"expand_page","ok":true,"page_path":"...","aligned":true,"pages":2}
{"event":"expand_page","ok":true,"page_path":"...","aligned":false,"reason":"align"}
```

**`aligned` と `reason` の値域を1つも変えずに、キーを足すだけ**にする。既存 GUI（`Editor.tsx:1588` の `expandAlignNotice(ev.aligned, ev.reason, pageNote)`）は新キーを無視して従来どおり動く。

```json
{"event":"expand_page","ok":true,"page_path":"C:\\...\\x-p0001-aligned.png",
 "aligned":true,"verdict":"match","score":0.97,
 "faces":[{"face_id":"front","verdict":"match","reason":"","score":1.0,"detected":18,"expected":16},
          {"face_id":"back","verdict":"match","reason":"","score":0.94,"detected":30,"expected":26}]}

{"event":"expand_page","ok":true,"page_path":"C:\\...\\raw.png",
 "aligned":false,"reason":"align","verdict":"mismatch","score":0.18,
 "faces":[{"face_id":"front","verdict":"mismatch","reason":"lines","score":0.18,"detected":21,"expected":16},
          {"face_id":"back","verdict":"skipped","reason":"","score":-1,"detected":0,"expected":0}]}

{"event":"expand_page","ok":true,"page_path":"...","aligned":false,"reason":"size",
 "verdict":"mismatch","score":-1,"faces":[]}
```

- **`reason` の既存値（`template`／`align`／`size`／`image`／`other`）は据え置く。** 不一致・判定不能の区別は新キー `verdict` で伝える。`reason:"align"` を期待する既存テスト（`test_review_fixes.py:257`・白紙画像）は影響を受けない（白紙は検出線ゼロ＝判定不能で `reason` は `align` のまま）
- `faces[].face_id` は名前を出す（stdout は秘匿対象外・07 §0.6）。GUI が面を特定するために要る
- `verdict` が無い応答（旧コア）を受けた GUI は、従来どおり `aligned`／`reason` だけで案内を出す（§2.7 のフォールバック）
- `TemplateError`（`reason:"template"`）では判定を行わない（テンプレートが読めていない）。`verdict` は返さない

### 2.7 GUI: `Editor.tsx`

#### 2.7.1 画像ファイルでも `expand-page` を通す（AC-F02 の前提）

**現状の穴**: `loadImage`（`Editor.tsx:1547-1590`）は `if (p.toLowerCase().endsWith(".pdf"))` のときだけ `expand-page` を呼ぶ。**PNG／JPG は生のまま表示され、位置合わせも様式判定も走らない。** AC-F01／AC-F02 の素材は PNG（`workdir/pages/sample-1.png`・#70-1 で作る同寸別様式画像）なので、このままでは「編集画面で開いても判定が出ない」。

- 対応: 拡張子を問わず `expand-page` を通す。`ingest.expand` は PDF 以外を `[source]` としてそのまま返す（`ingest.py:166-167`）ので、コア側の変更は要らない
- 読み取り権限: 成功時の `page_path` は `workdir/editor_pages/` 配下（読み取りルート・#69）。失敗時は利用者が選んだ元ファイルで、`pick_image` 経由で `picked` に登録済みのため `read_file_b64` を通る（`lib.rs` の `check_scope`）
- **副作用**: 画像ファイルも位置合わせ後の下地で表示されるようになる（PDF と同じ挙動になる）。これは 07 に明記が無い挙動変更なので §7-4 に挙げる

#### 2.7.2 案内の優先順（FR-F07・AC-F09）

`expandAlignNotice` の**引数を1つ足し、戻り値にキーを1つ足す**。既存の gui-logic テストは3引数呼び出し・`{text, isError}` 参照のままなので落ちない（07 §7.2-4 の期待値書き換えを避ける）。

```ts
export type ExpandVerdict = "match" | "mismatch" | "undecidable";

export function expandAlignNotice(
  aligned: boolean,
  reason: ExpandAlignReason | undefined,
  pageNote: string,
  verdict?: ExpandVerdict,           // 追加（旧コアでは undefined）
): { text: string; isError: boolean; level: "error" | "warn" | "info" }
```

優先順（07 FR-F07）: **template > size > 不一致 > 判定不能 > 一致**。

| 状態 | level | isError | 帯 | 文言の骨子 | 上書き操作 |
|---|---|---|---|---|---|
| `reason==="template"` | error | true | 赤 | 現行のまま | なし |
| `reason==="size"` | error | true | 赤 | 現行のまま | なし |
| `verdict==="mismatch"` | **warn** | false | **黄** | 「この画像はテンプレートの様式と一致しません。枠は表示していません。別のテンプレートを選ぶか、この画像用のテンプレートを作成してください」 | **あり**（FR-F05） |
| `verdict==="undecidable"` | info | false | 現行 | **現行文言を維持**（「読み取り時に自動補正されるため枠は動かさないでください」） | なし |
| `verdict==="match"` / `aligned===true` | info | false | 現行 | 現行のまま | なし |
| `verdict===undefined`（旧コア） | 現行の分岐そのまま | | | | |

`isError` は「赤帯にするか」を表す既存の意味のまま（template／size のみ true）。黄帯は新設の `level==="warn"` で描く。Editor 側は `errMsg`（赤）／`msg`（通常）の2系統しか持たないため、**黄帯用の state を1つ足す**（`warnMsg`）。

#### 2.7.3 枠の非表示（FR-F04・AC-F02・AC-F06）

- 判定は**面単位**で返る。Editor は面を `splitY` で front／back に分けており（`faceRangeContains`・`Editor.tsx:598-609`）、既存の `fieldsForFace` がその述語を持つ
- **`fields`・`tables`・`excls` の実体は消さない**（保存時に失われる／FR-F06 の作業ができなくなる）。描画とヒットテストの手前で絞る

```ts
/** 不一致と判定された面を集める。上書き中（override）は空集合を返す。 */
export function hiddenFaces(
  faces: { face_id: string; verdict: string }[] | undefined,
  override: boolean): Set<string>

/** 描画・ヒットテストの両方が使う唯一の可視集合（L-Q1 の教訓: 述語を2つ持たない）。 */
export function visibleFields(
  fields: Field[], hidden: Set<string>, splitY: number, imgH: number): Field[]
```

- `draw()`（`Editor.tsx:1342` 周辺のループ）と `onDown` のヒットテスト（`Editor.tsx:2270` 以降）は**同じ `visibleFields` の結果を見る**。片方だけに掛けると「見えない枠が選べる」（`Editor.tsx:2262` のコメントが既に警戒している状態）になる
- `tables`・`excls` も同じ面判定で絞る

#### 2.7.4 「それでもこのテンプレートで開く」（FR-F05・AC-F07）

- state 1つ（`formatOverride: boolean`）。押すと `hiddenFaces` が空集合になり全枠が描かれる
- 上書き中であることを画面へ残す（帯の文言を「上書き表示中」へ差し替える）。押した後に「なぜ枠が出ているか」が分からなくなる状態を作らない
- 画像を開き直したら `false` に戻す

#### 2.7.5 FR-F06（画像あり・枠なしでの操作可否）と `hasImage` ガードの関係

- 本日入れた `canvasInteractionAllowed(hasImage, tool)`（`Editor.tsx:927-929`）は**画像の有無だけ**を見る。不一致状態でも画像はあるので `true` を返す＝**FR-F06 は現行ガードのままで満たされる**
- **禁止事項**: `canvasInteractionAllowed` に `verdict` を渡さない。「不一致だから操作させない」を入れると、新しいテンプレートを作るという作業状態そのものを塞ぐ（FR-F06 の趣旨）
- ヒットテストは §2.7.3 のとおり可視集合に絞る。これは「操作の可否」ではなく「見えない物を掴ませない」であって FR-F06 と衝突しない

### 2.8 GUI: `RunScreen.tsx`（FR-F10・AC-F12）

- 進捗イベント `page` に `reason_code` を足す（`{"event":"page","page_id":...,"status":"様式不一致","reason_code":"frame_lines"}`）
- `type Failure = { page_id: string; status: string; reason_code?: string }`
- `STATUS_JA`（`RunScreen.tsx:84-97`）はそのまま。**理由コード → 平易な言葉**の対応表を1つ足す:

| reason_code | 表示 |
|---|---|
| `frame_size` / `frame_lines` / `frame_ambiguous` / `frame_edge` | 「様式が違うため**送信前に**止めました」 |
| `map_failed` / `outside_ratio` / `row_build_failed` | 「送信後に様式不一致と判定しました」 |
| `frame_few_lines` / `frame_boundary` | 「罫線が読み取れず位置合わせできませんでした」 |

- **出口2択**（FR-F10）: `format_mismatch_pre_send > 0` のとき、一覧の下に固定文で2つ示す。①実行画面でテンプレートを選び直す（(t) 実装前は「テンプレート選択は次段で追加予定」と書かず、**現状の導線（編集画面でテンプレートを開き直す）**を案内する）②編集画面でこの紙のテンプレートを作る
- `completionNotice`（`RunScreen.tsx:180-201`）: 「すべてのページが様式不一致」の文言は現在「用紙サイズ・向きを確認」に固定されている。`format_mismatch_pre_send` が総数と一致する場合は「様式が一致しませんでした。テンプレートを選び直すか、この帳票のテンプレートを作成してください」へ差し替える。※既存テストが文言を固定していないか実装時に確認（`completionNotice` は gui-logic のテスト対象）

### 2.9 テスト計画（AC-F01〜F15）

| AC | レベル | 落とす場所 | 素材 |
|---|---|---|---|
| AC-F01 | integration（**L2**） | formC を `run` → status=様式不一致・`status_reason="frame_lines"`・API 0 回。**実測済み**: 出荷テンプレに通すと front/back とも `few_lines`（front matched 2/22・det 30 vs 期待16／back matched 7/42・det 17 vs 期待26）＝軸別でも検出十分で「不一致」 | `testdata/formC/formC-1.png`（`make_formC.py` で生成・png は git 管理外） |
| AC-F02 | gui-logic ＋ L2 | `hiddenFaces`／`visibleFields` の純関数＋`expandAlignNotice(verdict="mismatch")` が黄帯 | 純関数は素材不要 |
| AC-F03 | unit ＋ integration（L2） | `back/detail` の横罫線を上から N 本消した sample-1 → `undecidable`／枠は描画継続。**軸別（★2）なら N=12（det_h=7 < 15×0.5）が最小**。※合算基準では成立しない見込み（§2.3.4） | sample-1（`.gitignore` 配下） |
| AC-F04 | integration（L2） | sample-1・sample-2 が `match`・status 正常 | sample-1/2 |
| AC-F05 | **unit** | `classify()` に4 reason＋成功＋`size` を与えて対応表どおりの3値。`few_lines` は**軸別**検出比の高低で分岐し、境界フラグでさらに分岐（★2・★3）。`edge_mismatch` は判定不能（★1） | 合成 `ShiftEstimate` のみ |
| AC-F06 | unit ＋ gui-logic | `fold()` が片面 mismatch でページ mismatch／`visibleFields` がその面の枠だけ落とす | 合成 |
| AC-F07 | gui-logic ＋ L2 | `formatOverride=true` で `hiddenFaces` が空・帯が「上書き表示中」 | 合成 |
| AC-F08 | gui-logic | `canvasInteractionAllowed(true, tool)===true`（不一致でも変わらないこと） | 合成 |
| AC-F09 | **unit（gui-logic）** | `expandAlignNotice` の優先順 template>size>mismatch>undecidable>match と帯色 | 合成 |
| AC-F10 | gui-logic | 新設文言が純関数として単体検証できる形になっている | 合成 |
| AC-F11 | gui-logic ＋ 目視 | 判定不能で描画を弱め案内を強調・色以外の手掛かり（線種の破線化など） | 目視は L3 |
| AC-F12 | integration ＋ gui-logic | 一部不一致のバッチで全ページ分の行が出る／`format_mismatch_pre_send` が区別表示される | 合成テンプレ＋既存応答 |
| AC-F13 | integration | 全ページの `format_verdict`／`format_score`／`detected`／`expected` が page 行とログに残る（**一致ページも**） | 既存の replay 素材 |
| AC-F14 | integration（例外注入） | `format_check.from_diag` を monkeypatch で例外化 → status は `位置合わせ失敗` のまま・`format_check_failed` とスタックが残る・`様式不一致` にならない | 合成 |
| AC-F15 | unit | `check_page(img, template)` がスコア（[0,1]）と理由を返し、面切りが内側で行われる | 小さな合成画像＋合成テンプレ |

**既存テストへの影響**

`core/tests/test_alignment_robustness.py:66-77` の `test_large_shift_fails_instead_of_wrong_values` は、`dy=104`／`dy=113`（1行ピッチのズレ）と `dx=40,dy=40` で `位置合わせ失敗` を期待している。

- **1行ズレ（`dy=104`・`dy=113`）は `edge_mismatch`** で落ちる（`align.py:139-151` の非周期アンカーがこのために置かれている）。★1 により判定不能＝`位置合わせ失敗` のままなので、**このケースは影響を受けない**
- `dx=40,dy=40` がどの reason で落ちるかは未実測。`few_lines` かつ軸別で検出十分かつ境界フラグが立たなければ `様式不一致` へ変わり、**このテストが赤くなる**（※未検証・実装時に実測すること）

赤くなった場合の対応は、**期待値をバケツ名から不変条件へ書き直す**:

```python
assert row.status in ("位置合わせ失敗", "様式不一致")
assert all(v == "〓" for v in row.values)
```

このテストの目的は関数名（`fails_instead_of_wrong_values`）とコメント（「正常なのに値が違うだけは絶対に出さない」）のとおり**「正常顔の誤値を出さないこと」**であって、どちらのバケツに入るかではない。バケツの付け替えは FR-F09 が要求した仕様変更なので緑偽装（07 §7.2-4）には当たらないが、**既存テストの期待値変更であることに変わりはない**ので §1 と同じ扱い（理由と日付をテスト内コメントに残す）にする。§7-3 に要件側の追記提案として挙げる。

**golden（AC-F45）**: 07 が `※要確認（着手時点のコミットが未定）` としていた着手前コミットは **`71384a4`** に確定した（`workdir/golden/71384a4/`・manifest は `testdata/golden_manifest.json`・2026-09-02 取得）。#77 のログ変更は出力を変えないため、この golden は (a') の実装後も有効。

### 2.10 変更ファイルと分担

**インターフェース（この2つが決まれば両者は独立に進められる）**

1. `expand-page` の JSON（§2.6）
2. `run` の進捗イベント `page` の `reason_code`＋`summary.format_mismatch_pre_send`（§2.4.3）

| 側 | 担当 | ファイル | 主な変更 |
|---|---|---|---|
| core | シオン（`coder_backend`） | `core/chouhyo_ocr/align.py` | `ShiftEstimate` に7フィールド／`AlignedFace.estimate`／`AlignError.diag`／`_face_estimate` の抽出 |
| core | シオン | `core/chouhyo_ocr/format_check.py`（新規） | `classify`／`score_of`／`fold`／`from_diag`／`check_page` |
| core | シオン | `core/chouhyo_ocr/pipeline.py` | 例外分岐の書き換え・記録・カウンタ・進捗イベント |
| core | シオン | `core/chouhyo_ocr/store.py` | 列5つの追加（`_ensure_column`）・`set_format_result`／`set_status_reason` |
| core | シオン | `core/chouhyo_ocr/cli.py` | `expand-page` の JSON 拡張 |
| core | シオン | `core/tests/test_format_check.py`（新規）ほか | AC-F01・F03〜F06・F12〜F15 |
| GUI | フブキ（`coder_frontend`） | `gui/src/Editor.tsx` | 画像も expand-page 経由／`expandAlignNotice` 拡張／`hiddenFaces`・`visibleFields`／上書き操作／黄帯 state |
| GUI | フブキ | `gui/src/RunScreen.tsx` | `reason_code` 表示・出口2択・`completionNotice` 差し替え |
| GUI | フブキ | `gui/tests/gui-logic.test.mjs` | AC-F02・F06〜F10 の純関数テスト（export リストへの追加が要る） |

**分担境界の注意**: `gui/src-tauri`（Rust）は今回変更しない。`expand-page` は既に `run_core_capture` の白リストを通っている。NFR-F06（GUI に画像処理を持たない）は自動的に満たされる。

### 2.11 守るべき不変条件

1. **`estimate_shift` の `ok`／`reason`／`dx`／`dy`／`matched`／`total` の算出経路を変えない。** 追加フィールドはどの分岐条件にも現れない（NFR-F08）
2. **スコアは判定に使わない。** 判定は既存の `need_y`／`need_x`。スコアは記録と (t) の比較専用（07 FR-F01）
3. **スコアの分子と分母は同じ基準（重複排除）で揃える。** `matched`（連結リスト基準）を重複排除分母で割らない（front で 1.375 になる）
4. **`page.status` の既存8値を増やさない。** 新しい区別は `status_reason` で持つ
5. **判定関数の例外は `様式不一致` に化けない。** 例外時は現行バケツへ落とし `format_check_failed` を残す（AC-F14）
6. **枠の可視判定は1箇所。** 描画とヒットテストが同じ `visibleFields` を見る
7. **`fields`／`tables`／`excls` の実体を判定結果で削らない。** 消すのは描画だけ
8. **`ALGO_VERSION` を上げない**（(a') は読み取りアルゴリズムを変えない）
9. **序数は `template_hash` とセットで出す**（§1.4 不変条件 A）

### 2.12 リスク

| # | リスク | 影響 | 緩和 |
|---|---|---|---|
| R-1 | `_face_estimate` の抽出で `align_page` の数値経路が変わる | 読み取り出力が変わる（NFR-F08 違反） | 純粋な関数抽出に限定。AC-F45（golden 比較）と `test_alignment_robustness` の4件で担保。**抽出と同時に他の変更を入れない** |
| R-2 | **検出線の本数が「線が見えているか」の代理指標として弱い** | かすれが進んだ本物の紙（sample-1 の N=8〜11）が「不一致」に分類され、枠が消える | 消せない。★1（`edge_mismatch` を判定不能へ）で N=1〜7 の帯は救えるが、N=8 以降は残る。判定に一致率を使えば分離できる（formC は 9〜17%・かすれは 24〜86%）が、07 FR-F01 が「スコアは判定に使わない」と定めている。**残存リスクとして要件に明記してもらう**（§7-2）。閾値は暫定値であることを定数名（`FEW_LINES_DETECT_RATIO`）とコメントに残し、Q-F6 の較正対象にする |
| R-3 | 検出比を軸別にする（★2）ことが 07 FR-F45 の指定（合算）と違う | 要件と実装の不一致 | §7-2 で要件側の修正を求める。合算のままだと AC-F03 の素材条件（50% を下回る最小の N）が作れない見込み（§2.3.4） |
| R-4 | 画像ファイルも expand-page 経由になり、編集画面の下地が位置合わせ後に変わる | 利用者から見た挙動変更 | §7-4 に挙げ、実装前に確認を取る |
| R-5 | 再利用ページのスコアが `unknown` のまま残る | AC-F13「全ページ」の解釈が割れる | §7-3 に挙げる。integration テストは新規 workdir で回るため検出されない点に注意 |
| R-6 | `assign()` のシグネチャ変更（§1.3）が `debug_images.py` の並走経路に影響 | 診断画像の生成が壊れる | `debug_images` は `assign` を呼ばず同等処理を再現しているだけ（`debug_images.py:73`）。索引は既定引数にして呼び出し側の変更を最小にする |

---

## 3. (t) テンプレートの保存・選択・照合提示（#72）

> **実装で確定した契約（2026-09-02・本節の例示より実装を正とする）**: `list_user_templates()` → `{templates:[{name,template_id,fields,tables,updated_at}],excluded:[{name,reason}]}`／`read_user_template(name)` → JSON 文字列／`save_user_template(name, content, overwrite)` → 成功時は verify の JSON Lines 文字列（GUI が `check:"template"` の `ok` を見て真の成否を判定）・同名かつ overwrite=false は `Err("AlreadyExists")`／`match_templates(input, names)` → core の `match_templates` イベント（`results[]{kind,name,template_id,verdict,reason,score,detected,expected,fields,tables,updated_at}`・`excluded[]{name,reason}`・`truncated`・`elapsed_ms`）に Rust 側の除外をマージ／core サブコマンド `match-templates --input --shipped --candidate... --page`（列挙しない・1件の不正で止めない・時間予算 3.0 秒）／`config.last_template` は `"shipped"`（名前なし）または `"user:<名前>"`、それ以外は出荷へフォールバック（`_validate` の特例）／`CHOUHYO_USER_DIR` は Rust が付与し core は検証して使う（不正なら ConfigError で明示失敗・未設定は `project_root()/templates_user/`＝開発・CLI 単体）／NFC は既存依存 `icu_normalizer`（default-features=false, compiled_data）。　**レビュー反映（2026-09-02）**: `excluded[].reason` の語彙は core／Rust で統一（`parse`・`not_found`・`schema`・`size`・`limit`・`invalid_name`・`check_failed`）。`results[].fields` は単発欄数（`faces[].fields` の要素数）、`tables` は表の個数。時間予算 3.0 秒の起点は候補ループ直前（画像 load 後）で、`elapsed_ms`（コマンド全体）とは別に `budget_elapsed_ms` を返す。`load_template` の schema はプロセス内キャッシュ。`ok:false` の `error` は固定コード（`input_not_found`／`expand_failed`／`input_unreadable`／`internal`）。`input_size` は契約に含める。`config_last_template_fallback` の警告は `log.init` 後に各コマンドが1回出す。Python 側の reparse point 検査は `is_symlink() or os.path.isjunction()`。

対象要件: 07 v1.2 §4.1(t)・§5.3（FR-F26〜F31・F46・F49）・§7.3・§7.4・§8.3・NFR-F09・§9.4。
前提: (a') は `5a3b660`（core）／`1541239`（GUI）で完了済み。`format_check.check_page(page_img, template) -> PageVerdict`（`core/chouhyo_ocr/format_check.py:177`）が使えるため、**照合の計算部分は新規に作らない**。

### 3.1 保存先の決定（Q-F16・07 §10.2-8 の着手条件）

#### 3.1.1 現状（2026-09-02 実測）

| 事実 | 根拠 |
|---|---|
| インストーラは NSIS 単体（`installMode` 未指定＝既定 `currentUser`） | `gui/src-tauri/tauri.conf.json:27-28` |
| `project_root()` は cwd から `templates/chouhyo-v1.json` を遡って探し、無ければ `app_root()`＝frozen なら exe の親 | `core/chouhyo_ocr/paths.py:19-30` |
| Rust の `repo_root()` は exe 位置 → cwd → resource_dir の順に同じマーカーを探す（開発時は `.git` を持つ祖先を優先） | `lib.rs:161-203` |
| 配布環境では両者ともインストールディレクトリに解決される | 上記2つの帰結 |
| **アプリ更新でインストールディレクトリの中身が保持されるかは未実測** | 07 §10.2-8（⏳ 未完了） |

07 FR-F26 の `project_root()/templates_user/` 案は、利用者データを**アプリのインストール先**に置く。07 自身が `templates/` を退けた理由（配布物と同居させない）を、1階層ずらして再現している。FR-F49（書き出し／取り込み）が Must で入っているのは、この不安を**復旧手段で埋め合わせる**ためであって、原因を取り除いてはいない。

#### 3.1.2 2案の比較

| 観点 | A: `project_root()/templates_user/`（07 の記述） | **B: `app_data_dir()/templates_user/`（推奨）** |
|---|---|---|
| 実体（配布時） | インストールディレクトリ配下 | `%APPDATA%\com.holodev.chouhyo-ocr\templates_user\`（`identifier` は `tauri.conf.json:5`） |
| アプリ更新への耐性 | **未実測**（NSIS がインストール先をどう扱うか次第） | **構造的に安全**——インストーラが触らない領域 |
| アンインストール時 | 一緒に消えうる | 残る（利用者データとして妥当） |
| 書き込み権限 | インストール先が `Program Files` 系に変わると**非管理者で書けない** | 常に本人の権限内 |
| 配布物への混入 | `resources` に足さない運用で防ぐ（人の規律に依存） | **構造的にゼロ**（リポジトリの外） |
| Rust と Python の解決先の一致 | 探索起点が違うため別の場所に解決されうる（FR-F26 の⚠️。AC-F52 で担保する設計） | **Rust が唯一の決定者**で Python は受け取るだけ＝ズレる余地が無い |
| CLI 単体運用（07 §7.1） | そのまま動く | 環境変数未設定時は A へフォールバックするので動く（§3.1.3） |
| 開発環境 | リポジトリ直下で見える | 環境変数未設定＝A と同じ（リポジトリ直下） |

**推奨は B。** 決め手は3つ——①更新耐性が実測待ちではなく構造で決まる ②非管理者書き込みが保証される ③FR-F26 の⚠️が指摘した「Rust と Python の解決先がズレうる」問題を、テスト（AC-F52）ではなく**構造**で消せる。A のままだと配布形態が変わるたびに同じ検証をやり直すことになる。

#### 3.1.3 解決経路（B 案の実体）

```
Rust: app.path().app_data_dir()? / "templates_user"
        |  （存在しなければ create_dir_all）
        |  canonicalize -> reparse point 検査 -> is_safe_root
        +-> template_roots に追加（--template のスコープ検査用・§3.2.5）
        +-> core_command() の env に CHOUHYO_USER_DIR=<絶対パス> を設定
                                     |
Python: paths.user_templates_dir()  <-+
        1. CHOUHYO_USER_DIR があり、絶対パス・実在するディレクトリ・`..` を含まない なら それ
        2. 無ければ project_root()/"templates_user"（開発環境・CLI 単体運用）
```

- **Python 側も受け取った値を検証する。** 環境変数は他プロセスからも渡りうるので信用しない。検証 NG なら 2 へフォールバックし `log.warn("user_dir_fallback")` を残す（エラー停止しない——FR-F29 と同じ「設定1つで起動不能にしない」方針）
- **`config.json`・`workdir`・`log_dir` は現状どおり**（`project_root()` 基準）。移すのは利用者テンプレートだけ。中間データの置き場を動かすと同期フォルダ検知（issue #8）・purge・verify の前提へ波及する
- **`.gitignore` の `/templates_user/`（`2cd1c77` で追加済み）は残す。** 開発環境では A の場所が使われるため引き続き必要
- **移行処理は書かない。** (t) は新機能で、現時点でどの環境にも `templates_user/` は存在しない（`ls templates_user/` → 該当なし・2026-09-02）
- CLI 単体で GUI と同じ場所を見たいときは `CHOUHYO_USER_DIR` を設定する。**AC-F52（CLI と GUI の出力一致）は `--template <絶対パス>` を明示指定して検証するので、この環境変数に依存しない**

07 §4.1(t)・FR-F26・§7.3・§10.2-8 の書き換えが要る（§7-5）。

### 3.2 Rust 側の新コマンド

#### 3.2.1 方針

- **既存コマンドのスコープを1つも広げない**（07 §9.4）。`write_text`／`write_template_staged`／`promote_template`／`discard_staged` の picked 限定は不変
- **webview へ絶対パスを返さない**（07 §7.3）。新コマンドの入出力は**表示名（拡張子なしのファイル名）**だけで組む
- **列挙とパス検査は Rust に一本化する。** 同じ除外規則（`*.saving.json`・`*.bak`・非通常ファイル・reparse point・サイズ上限）を Python にも書くと、セキュリティ上重要な判定が2箇所に割れる

#### 3.2.2 コマンド一覧（新規は3つ）

| コマンド | 引数 | 戻り値 | 用途 |
|---|---|---|---|
| `list_user_templates()` | — | `[{name, size, mtime, excluded?}]`（**表示名のみ・絶対パスなし**） | FR-F28 の列挙・RunScreen の選択肢・保存時の同名検出 |
| `read_user_template(name)` | 表示名 | JSON 文字列 | 編集画面で保存済みテンプレートを開く（FR-F29 の既定復元） |
| `save_user_template(name, content)` | 表示名＋JSON 文字列 | `verify` の JSON 文字列 | FR-F26。**staged → verify → promote を Rust の中で通し切る**（§3.2.3） |

FR-F49（書き出し／取り込み）は**新コマンドを増やさずに既存の組み合わせで成立する**（§3.7）。

#### 3.2.3 `save_user_template` を1コマンドにまとめる理由

現行の保存は GUI が3手で回している——`write_template_staged(path)` → `verify --template <path>.saving.json` → `promote_template(path)`（`Editor.tsx:1723-1740`）。これは **GUI が保存先の絶対パスを知っている**前提で成り立つ。`templates_user/` ではパスを webview へ渡せない（§3.2.1）ので、同じ3手を回せない。

```
save_user_template(name, content):
  1. validate_user_template_name(name, existing, shipped)   -- 純関数・§3.2.4
  2. dir    = user_templates_dir()          -- canonicalize + reparse 検査
     target = dir / (name + ".json")        -- 拡張子はシステムが付与（07 §7.4）
     staged = staged_path(target)           -- 既存の <path>.saving.json 規則を再利用
     target の親の canonicalize 結果が dir と完全一致することを再検査
  3. staged へ書き込む
  4. core を `verify --template <staged>` で起動（run_core_capture の本体を共有）
  5. verify が ok なら promote_with(staged, target, backup_path(target))
     ok でなければ staged を消し、verify の JSON をそのまま返す
```

- **「検証 NG のまま上書きしない」不変条件（#56 T1）を維持する。** 既存の `promote_with`（`lib.rs:997`）・`backup_path`（`lib.rs:970`）をそのまま使う
- **上書き確認は GUI 側で先に出す。** Rust は名前の妥当性だけを見て、同名の存在は「エラー」ではなく戻り値で区別して返す（確認は UI の責務）
- ⚠️ 実装上の注意: `run_core_capture` は `#[tauri::command]`（`lib.rs:603`）でプロセススロット管理と一体になっている。**本体を private fn へ切り出して両者から呼ぶ**。切り出しで多重起動制御（`CoreProc`）の扱いを変えないこと

#### 3.2.4 名前検証（Rust の純関数・AC-F51）

```rust
fn validate_user_template_name(
    name: &str, existing: &[String], shipped: &[String],
) -> Result<NameVerdict, String>   // Ok(NameVerdict::New(normalized) | ::Overwrites(normalized))
```

検査順（すべて許可リスト方式・07 §7.4）:

1. 空でない／長さ 1〜64（**文字数**。バイト数ではない）
2. 文字集合: 英数・`-`・`_`・**中間の**スペース・Unicode の文字カテゴリに属する文字（日本語等）。制御文字・`:`・パス区切り・`..`・その他の記号は拒否
3. 末尾がドット・空白でない
4. 予約デバイス名でない（NFC 正規化後・大小文字無視。`CON`・`NUL`・`PRN`・`AUX`・`COM1`〜`COM9`・`LPT1`〜`LPT9`・`CONIN$`・`CONOUT$`）
5. 既存名と衝突しない（NFC 正規化後・case-insensitive）。**衝突はエラーではなく上書き確認の対象**として呼び出し側へ区別して返す
6. 出荷テンプレートのファイル名と衝突しない。判定は **`templates/*.json` の実在ファイル名から動的に**作る（`chouhyo-v1` を直書きしない・07 §7.4）

**⚠️ NFC 正規化に外部クレートが要る。** `gui/src-tauri/Cargo.toml` の依存は `tauri`・`serde`・`serde_json`・`rfd`・`base64` の5つで、Unicode 正規化を持つものは無い（2026-09-02 確認）。

| 案 | 内容 | 判断 |
|---|---|---|
| **B-1（推奨）** | `unicode-normalization` クレートを追加（MIT/Apache-2.0・実行時依存は `tinyvec` のみ） | 供給網レビュー（AZKi＋ミオ）を通したうえで採用する |
| B-2 | webview 側で `name.normalize("NFC")` してから渡し、Rust は受け取った文字列をそのまま比較 | **不採用**。レンダラを掌握されると NFD の名前が通り、見た目が同一の別ファイルを作れる。AC-F51 が「名前検証を Rust の純関数で」と要求した趣旨からも外れる |
| B-3 | NFC 判定だけ core（Python の `unicodedata`）へ出す | **不採用**。名前検証が Rust と Python に割れる。AC-F51 が `cargo test` の表駆動を求めているのは判定を1箇所へ集めるため |

> **決定（2026-09-02・Orchestrator・security レビューを受けて）**: `unicode-normalization` は追加しない。`cargo tree -e normal -i icu_normalizer` で `icu_normalizer 2.3.0` が tauri → url → idna 経由のランタイム依存として既に解決・リンク済み（compiled_data 有効）と判明したため、これを **直接依存へ昇格（`icu_normalizer = "2.3"`）で確定**。新規の信頼境界ゼロ・バイナリ増分ゼロ。本節の「Unicode 正規化を持つものは無い」は直接依存 5 件のみを見た誤りで、解決後のグラフでは存在した。

したがって §3.2.4 の比較表の B-1 は「新規クレートの追加」ではなく「**既に解決済みの推移依存を直接依存へ昇格**」として実施する。上表の「Unicode 正規化を持つものは無い」は直接依存 5 件だけを見た記述で、解決後の依存グラフには `icu_normalizer` が存在した（`cargo tree` で確認・2026-09-02）。

#### 3.2.5 パススコープ検査（07 §7.3・AC-F59）

`user_templates_dir()` は毎回次を通す（**キャッシュしない**——ディレクトリは実行中に差し替えられうる）:

```
1. app_data_dir()?.join("templates_user")
2. create_dir_all（初回のみ実効）
3. symlink_metadata().file_type().is_symlink() が true なら拒否
   （Windows のジャンクションもここで true になる）
4. canonicalize
5. is_safe_root(canonical) を通す（#69 S-N3 と同じ流儀・生パスだけで判定しない）
```

個々のエントリ（列挙・読み書き）:

```
- symlink_metadata().file_type().is_file() が false なら除外（ディレクトリ・リンク）
- 拡張子が json（小文字化して比較）
- 名前が *.saving.json（STAGED_SUFFIX）・*.bak（backup_path 規則）で終わらない
- サイズ 5 MB 以下（07 §7.3・暫定 ※Q-F13）
- canonicalize 後の parent が user_templates_dir の canonical と完全一致
```

**`--template` のスコープを2系統に分ける。** `allowed_roots()` に user dir を足すと `--input`／`--image`／`read_file_b64` まで巻き添えで広がる。`check_arg_scopes` へ渡す roots を分離する:

| フラグ | roots |
|---|---|
| `--template` | `repo_root` ＋ **`user_templates_dir`** |
| `--input`・`--image` | `repo_root` ＋ `editor_pages`（現状のまま） |
| `read_file_b64` | `editor_pages` ＋ picked（**変更しない**・#69） |

staged ファイル（`<name>.json.saving.json`）は拡張子が `json` で親が user dir なので、この roots だけで通る。`is_staged_of_picked` の特例（`lib.rs:268`）は触らない。

#### 3.2.6 `cargo test` の許可／拒否表（AC-F51・AC-F59）

| # | 入力 | 期待 |
|---|---|---|
| 1 | `CON`・`NUL`・`COM1`・`LPT9`・`CONIN$`（大小混在も） | 拒否 |
| 2 | `abc.`・`abc `（末尾ドット・空白） | 拒否 |
| 3 | 既存 `Sample` に対する `sample`・`SAMPLE` | **衝突**（＝上書き確認へ） |
| 4 | `a:b`（代替データストリーム） | 拒否 |
| 5 | 制御文字を含む名前 | 拒否 |
| 6 | 65 文字 → 拒否／64 文字 → 許可 | 境界 |
| 7 | 空文字・空白のみ | 拒否 |
| 8 | `chouhyo-v1`（出荷テンプレートと同名） | 拒否 |
| 9 | `../x`・`a/b`・`a\b` | 拒否 |
| 10 | `帳票 A_2026-09`（全角＋中間スペース＋`-`＋`_`） | 許可 |
| 11 | NFD の「が」（か＋濁点）と NFC の「が」 | **衝突として検出**（B-1 採用が前提） |
| 12 | `user_templates_dir` 自体がジャンクション | 保存・列挙とも拒否（AC-F59） |
| 13 | user dir 内のエントリが symlink | 列挙から除外・読み取り拒否（AC-F59） |
| 14 | `x.json.saving.json`・`x.json.bak`・サブディレクトリ | 列挙から除外 |
| 15 | 5 MB 超のファイル | `excluded:"size"` として列挙結果に現れる |

### 3.3 照合提示（FR-F28・FR-F46・NFR-F09）

#### 3.3.1 経路の決定: 新サブコマンド `match-templates`

| 案 | 内容 | 判断 |
|---|---|---|
| **C-1（推奨）** | core に `match-templates --input <img> --candidate <p1> --candidate <p2> ...` を新設。**列挙は Rust が行い、core は渡された絶対パスだけを読む** | 採用。1プロセス起動で N 件を回すので NFR-F09（合計 3.0 秒）の測定単位と一致する。**除外規則（reparse・staged・bak・サイズ）が Rust の1箇所に留まる** |
| C-2 | `match-templates --candidates <dir>` で core がディレクトリを列挙する | 不採用。§7.3 の除外規則を Python にも実装することになり、セキュリティ判定が2箇所に割れる |
| C-3 | `expand-page` を拡張して候補照合も返す | 不採用。expand-page は「1ページを展開して返す」責務で、N 件照合を混ぜると失敗時の切り分け（どの候補で落ちたか）が JSON 1つに潰れる。既存契約（GUI が依存）にも触る |

- `--candidate` は反復指定。`check_args_v2`（`lib.rs:70-118`）は同一フラグの重複を弾かないので**そのまま通る**（2026-09-02 コード確認）。20 件 × 260 文字程度＝約 5 KB で、Windows のコマンドライン上限 32 KB に収まる
- **出荷テンプレートも `--candidate` の1つとして Rust が積む**（列挙はしない・07 §7.3）。core 側に「出荷か利用者か」の区別を持たせないため、`kind` は Rust が付けて GUI へ渡すのではなく、**core が受け取った順序の先頭1件を shipped として扱う**のではなく——**`--shipped <path>` と `--candidate <path>` の2フラグに分ける**。core は前者を `kind:"shipped"`、後者を `kind:"user"` として出力に載せる
- Rust 側の許可フラグ表（`allowed_flags`）と `ALLOWED_SUBCOMMANDS` に `match-templates` を追加する

#### 3.3.2 JSON 契約

```json
{"event":"match_templates","ok":true,"elapsed_ms":1840,"truncated":false,
 "candidates":[
   {"name":"帳票B","kind":"user","template_id":"formB-v1","cells":42,"tables":1,
    "mtime":"2026-09-02T10:14:33+09:00",
    "verdict":"match","reason":"","score":0.97,"detected":18,"expected":16},
   {"name":"chouhyo-v1","kind":"shipped","template_id":"chouhyo-v1","cells":220,"tables":2,
    "mtime":"2026-08-31T18:46:02+09:00",
    "verdict":"mismatch","reason":"lines","score":0.11,"detected":30,"expected":16}],
 "excluded":[{"name":"壊れたテンプレ","reason":"parse"},
             {"name":"巨大","reason":"size"}]}
```

- **`name` は表示名（拡張子なしのファイル名）のみ。絶対パスを出さない**（07 §7.3・§9.4）。core は受け取った絶対パスの `stem` を返す
- `verdict`／`reason`／`score`／`detected`／`expected` は (a') の `PageVerdict`／`FaceVerdict` の値をそのまま使う。**面ごとの内訳は返さない**（照合提示に要るのはページ単位の1行だけ）
- `excluded[].reason` は `parse`（JSON として読めない）／`schema`（`load_template`・`validate_v1` が拒否）／`size`（5 MB 超・Rust が付ける）／`limit`（件数上限で照合しなかった）。**1件の不正で照合ループを止めない**（FR-F28）——`try/except` は候補1件ごとに閉じる
- `truncated` は件数上限（20）または合計時間上限（3.0 秒）で打ち切ったことを示す。打ち切り時点以降の候補は `excluded reason:"limit"` として名前だけ載せる（FR-F46 ⑤）
- **画像は1回だけ読む。** `check_page` はテンプレートごとに `resize` と面切りを行うため、テンプレートの `image_size` が異なると再 resize が要る。同じ寸法のテンプレートが続く場合に resize 結果を使い回すキャッシュを1段だけ持つ（NFR-F09 の 3.0 秒に効く）
- `cells`／`tables` は `len(template.cells)` と面をまたいだ `table_id` のユニーク数

#### 3.3.3 時間上限の実装

合計 3.0 秒・1件 1.0 秒・20 件（NFR-F09）。**打ち切りはテンプレート単位**（`check_page` の途中では止めない——面の途中で止めると `fold` が誤った verdict を返す）。1件ごとに経過を測り、次の1件を始める前に合計上限を超えていれば残りを `limit` として積む。

### 3.4 提示の並び（FR-F46・AC-F53・AC-F54）

**GUI の純関数**（gui-logic でテストする）。core は並べ替えない——並び順は表示規則であって判定ではない。

```ts
export function rankCandidates(cands: Candidate[], truncated: boolean):
  { rows: Candidate[]; recommend: string | null; showScore: boolean; notice: string }
```

| 状況 | rows の並び | recommend | showScore |
|---|---|---|---|
| 一致候補が1件 | スコア降順 | その1件 | true |
| 一致候補が複数・最上位と2位のスコア差 **≧ 0.1** | スコア降順 | 最上位 | true |
| 一致候補が複数・差 **< 0.1**（暫定 ※Q-F13） | **名前順** | **null（推奨を出さない）** | **false** |
| `truncated` | 名前順 | null | false（各行に「スコア未計算」） |
| 一致候補ゼロ | 名前順（全件を不一致として並べる） | null | true |

- `notice` には常に「この判定は罫線の幾何一致のみを見ており、中身の同一性は保証しない」を含める（FR-F46 ③）
- 各行の表示: 表示名・`kind`（出荷／利用者の区分を明示）・`template_id`・欄数／表数・最終更新日時（FR-F46 ④）
- `excluded` は一覧の下に「読み込めなかったテンプレート: N 件（内訳）」として**必ず出す**（黙って減らさない・FR-F28）

### 3.5 実行画面の選択（FR-F27・FR-F29・AC-F26・AC-F60・AC-F61）

#### 3.5.1 選択値の永続

`config.json` に **1キー** を足す（FR-F29）。

```
last_template: str  = ""     # "" = 出荷テンプレート
                             # 形式: "shipped:<name>" | "user:<name>"
```

- **絶対パスを保存しない**（07 FR-F29）。区分＋表示名のみ
- **3箇所同時更新**（07 FR-F29 の⚠️）: Rust `KNOWN_CONFIG_KEYS`（`lib.rs:797-802`）／Python `Config` dataclass（`config.py:26-40`）／`config.py:_validate`
- ⚠️ **`_validate` はこのキーで例外を投げない。** 他のキーは不正値で `ConfigError` を上げるが、それをやると AC-F60（範囲外を手書きしてもフォールバックして起動する）と矛盾する。**型が str でない、または形式・名前検証に通らない場合は `""` へ落とす**（＝出荷テンプレート）。この1キーだけ挙動が違うことを `config.py` のコメントに明記する
- `CONFIG_PATH_KEYS`（`is_safe_root` を通すキー）には**入れない**——パスではないため

#### 3.5.2 実行時のテンプレート解決

**`inject_default_template`（`lib.rs:139-155`）を拡張する。** GUI は `--template` を渡さない（渡せない——絶対パスを持たないため）。

```
inject_default_template(args, root, app):
  --template が既にあれば何もしない（現状どおり）
  config.last_template を読む
    "user:<name>"    -> user_templates_dir()/<name>.json
                        名前検証 + §3.2.5 のエントリ検査を再実行
                        通らなければ出荷テンプレートへフォールバック（AC-F60）
    "shipped:<name>" -> templates/<name>.json（実在しなければ出荷既定）
    ""・未知の形式    -> templates/chouhyo-v1.json（現状どおり）
```

- **読み出し時に再検査する**のは既存の `workdir_pages_dir`（`lib.rs:356-369`）と同じ流儀。`config.json` は手編集や別プロセスからも書ける
- この配線により、`run`／`verify`／`expand-page`／`render`／`remap` のすべてが同じ選択値を使う（`TEMPLATE_ACCEPTING_SUBCOMMANDS`・`lib.rs:125-127`）
- **不変条件**: 「編集画面が開いているテンプレート」と「`config.last_template`」を一致させる。編集画面で保存済みテンプレートを開いたら、その場で `write_config({last_template})` を行う。ズレると expand-page の照合対象と画面の枠が食い違う
- 代替案（不採用）: `run_core_with_template(args, {kind, name})` という新コマンドで都度渡す。明示的だが、`--template` を受ける5サブコマンドすべてに同じ引き回しが要り、`inject_default_template` と二重の解決経路ができる

#### 3.5.3 RunScreen の UI

- 選択肢 = `[{kind:"shipped", name}] ++ list_user_templates()`。**絶対パスは出さない**
- 選択を変えたら即 `write_config({last_template})`（読み取り開始ボタンを押す前に確定させる）
- 選択中のテンプレートが列挙から消えていた場合（削除された）→ 出荷へ戻し、その旨を1行出す

### 3.6 編集画面の起動時既定と導線（FR-F29・FR-F30・FR-F31）

- **起動時**: `config.last_template` を見て、`user:` なら `read_user_template(name)`、それ以外は現行の `read_default_template()`（`Editor.tsx:1843`）。読めなければ出荷へフォールバックし、`noImageNotice` の「読み込めていません」経路へは落とさない
- **不一致時の導線（FR-F30）**: (a') が出した `verdict:"mismatch"` の案内（黄帯）に「この紙のテンプレートを作る」を足す。押下時:
  1. 未保存の変更があれば**破棄の確認**（既存 `confirmDiscard()` を使う・`Editor.tsx:1548`）
  2. 続行で**編集履歴を初期化**する（undo が前テンプレートへ戻らないように）
  3. **空のテンプレートで開く**——`image` は開いた画像の実寸（`imgSize`）、`faces` は現行の front/back 2面（`splitY` の既定値）、`cells`・`tables`・`exclusions` は空
- ⚠️ **枠候補の一括生成は (b) の範囲なので今回は作らない。** 導線の到達点は「空のテンプレートで開いた状態」まで。候補ゼロ相当の案内（FR-F31）として、**既存の等分割生成（`detect-grid --mode grid`）と手動作図**を次の手段として示す。07 FR-F31 は「候補ゼロの画面に放置しない」なので、(b) 未実装でもこの案内で満たせる
- 純関数（gui-logic でテスト）: `emptyTemplateFor(width, height, splitY)`／`newTemplateNotice(hasCandidates: boolean)`

### 3.7 書き出し・取り込み（FR-F49・AC-F63）

**新しい Rust コマンドを増やさない。** 既存の組み合わせで成立する:

| 操作 | 手順 |
|---|---|
| 書き出し | `read_user_template(name)` → `pick_json(save=true)`（ダイアログ・選んだ先は `picked` に入る）→ `write_text(path, content)` |
| 取り込み | `pick_json(save=false)` → `read_text(path)` → 名前を利用者に確認（既定は元ファイルの stem を §7.4 で正規化したもの）→ `save_user_template(name, content)`（＝名前検証＋`verify`＋promote を通る） |

取り込みが `save_user_template` を通ることで、**外部から持ち込んだ JSON も検証なしには `templates_user/` に入らない**（07 FR-F49 の「§7.4 の名前検証と `verify` を通して複製する」）。

### 3.8 テスト計画

| AC | レベル | 落とす場所 |
|---|---|---|
| AC-F51 | **unit（`cargo test`）** | `validate_user_template_name` の表駆動15件（§3.2.6）＋ gui-logic（確認ダイアログの分岐） |
| AC-F59 | **unit（`cargo test`）** | user dir 自体がジャンクション／エントリが symlink → 保存・列挙とも拒否。既存の `check_scope` テスト群と同じ流儀 |
| AC-F24 | L2 実走 ＋ ビルド成果物確認 | 保存 → RunScreen で選択 → `run` が完走。`tauri.conf.json` の `resources` に `templates_user` が無いこと／`git status` に現れないこと。**B 案では保存先がリポジトリ外なので後者2つは構造的に満たされる** |
| AC-F52 | integration（Python） | CLI で `--template <user dir の絶対パス>` を指定した `run` が完走し、セル値が GUI 実行と一致 |
| AC-F25 | gui-logic ＋ L2 | `match-templates` の JSON を食わせた `rankCandidates` が「利用者テンプレートのみ列挙＋出荷1件」を返す／自動で切り替わらない |
| AC-F53 | **gui-logic（純関数）** | 近接スコア（差 < 0.1）で推奨なし・名前順・スコア非表示。注意書きが必ず含まれる |
| AC-F54 | 計測（`scripts/perf_check.py`）＋ gui-logic | 21 件 → 20 件で打ち切り・`truncated:true`・残りが `limit`。合計 3.0 秒以内 |
| AC-F62 | integration（Python）＋ gui-logic | `*.saving.json`・`*.bak`・サブディレクトリ・5 MB 超・不正 JSON を置いて `match-templates` → 照合が完了し `excluded` に理由付きで出る |
| AC-F60 | integration（Python）＋ gui-logic | `last_template` に範囲外を手書き → 出荷へフォールバックして起動（`ConfigError` を投げない） |
| AC-F61 | integration（Python） | 新キーを含む `config.json` で `run`／`render`／`remap`／`verify` がすべて起動する（3箇所同時更新の確認） |
| AC-F26 | gui-logic ＋ L2 | 再起動で前回テンプレートが復元される／履歴なしでは出荷 |
| AC-F27 | gui-logic | 未保存 + 導線 → 破棄確認 → 履歴初期化 |
| AC-F28 | gui-logic ＋ L2 | 罫線が取れない画像で導線 → 等分割生成などの次手段が案内される |
| AC-F63 | L2 実走 | 書き出し → 削除 → 取り込み → 同一内容で復元され `run` が完走 |

**素材**: 07 §10.2-7（`templates_user` に formB を複製）は `37d6e5f` で完了扱いだが、**リポジトリに `templates_user/` は存在しない**（2026-09-02 確認）。B 案では実体がリポジトリ外になるため、**テストは `tmp_path` に user dir を作り `CHOUHYO_USER_DIR` を差して回す**（Python 側）／Rust 側は `tempfile` 相当で dir を作って純関数を回す。素材としての formB は `testdata/formB/formB-v1.json`（コミット済み）をコピーして使う。

### 3.9 変更ファイルと分担

**インターフェース（これが決まれば3者は独立に進められる）**

1. Rust コマンド3つのシグネチャと戻り値の形（§3.2.2）
2. `match-templates` の JSON 契約（§3.3.2）
3. `config.json` の `last_template` の形式（§3.5.1）
4. 環境変数 `CHOUHYO_USER_DIR`（§3.1.3）

| 側 | 担当 | ファイル | 主な変更 |
|---|---|---|---|
| Rust | あくあ（`coder_api`） | `gui/src-tauri/src/lib.rs` | `user_templates_dir`／`validate_user_template_name`／新コマンド3つ／`allowed_flags`・`ALLOWED_SUBCOMMANDS` に `match-templates`／`check_arg_scopes` の roots 2系統化／`inject_default_template` の config 解決／`core_command` の env 追加／`KNOWN_CONFIG_KEYS` |
| Rust | あくあ | `gui/src-tauri/Cargo.toml` | `unicode-normalization`（B-1 採用時のみ・供給網レビュー後） |
| core | シオン（`coder_backend`） | `core/chouhyo_ocr/paths.py` | `user_templates_dir()`（環境変数＋検証＋フォールバック） |
| core | シオン | `core/chouhyo_ocr/cli.py` | `match-templates` サブコマンド（`--input`／`--shipped`／`--candidate` 反復） |
| core | シオン | `core/chouhyo_ocr/config.py` | `last_template` の追加と、**例外を投げない**検証 |
| core | シオン | `core/tests/` | AC-F52・F60・F61・F62 |
| GUI | フブキ（`coder_frontend`） | `gui/src/RunScreen.tsx` | テンプレート選択・`write_config` |
| GUI | フブキ | `gui/src/Editor.tsx` | 起動時既定・照合提示 UI・`rankCandidates`／`emptyTemplateFor`／`newTemplateNotice`・保存ダイアログ（名前入力＋上書き確認）・書き出し／取り込み |
| GUI | フブキ | `gui/tests/gui-logic.test.mjs` | AC-F25・F26・F27・F28・F53・F54（export リストへの追加が要る） |

### 3.10 守るべき不変条件

1. **webview へ絶対パスを返さない。** 新コマンドの入出力は表示名のみ
2. **既存コマンドの picked 限定スコープを変えない**（07 §9.4）
3. **列挙と reparse 検査は Rust の1箇所だけ**に置く。Python 側に同じ規則を書かない
4. **`user_templates_dir()` の結果をキャッシュしない**（実行中に差し替えられうる）
5. **検証 NG のテンプレートで既存ファイルを上書きしない**（#56 T1・staged → verify → promote）
6. **`last_template` は `_validate` で例外を投げない唯一のキー**（AC-F60）
7. **1件の不正テンプレートで照合ループを止めない**（FR-F28）
8. **確定は人。** `match-templates` の結果でテンプレートを自動選択しない（07 4.2(h)）
9. **編集画面が開いているテンプレート＝`config.last_template`** を一致させる（§3.5.2）

### 3.11 リスク

| # | リスク | 影響 | 緩和 |
|---|---|---|---|
| T-1 | `app_data_dir()` が使えない環境（ポータブル運用・APPDATA 未設定） | 保存先が決まらず (t) が丸ごと落ちる | Rust 側で `app_data_dir()` が Err なら `repo_root()/templates_user` へフォールバックし、その旨を画面に1行出す（黙って別の場所へ書かない） |
| T-2 | `unicode-normalization` の追加が供給網レビューで却下される | AC-F51 ⑪（NFD 衝突）が満たせない | B-2 へ後退し、**残存リスクとして「見た目が同じ別名ファイルが作れる」を明記**する。範囲逸脱は起きない（親ディレクトリ一致検査は別レイヤ） |
| T-3 | `run_core_capture` の本体切り出しで多重起動制御が壊れる | コアの二重起動・`runlock` との競合 | 切り出しは純粋な関数抽出に留め、`CoreProc` の扱いを1行も変えない。既存の cargo test（61 件）で担保 |
| T-4 | `match-templates` が 20 件 × `check_page` で NFR-F09（3.0 秒）を超える | 画像を開く操作が待たされる | 打ち切りを実装（§3.3.3）。resize 結果の1段キャッシュ。**実測は AC-F54 で取る**——現時点で 1 件あたりの所要は未計測（※要確認） |
| T-5 | 編集画面の開いているテンプレートと `config.last_template` がズレる | 照合対象と画面の枠が食い違う | 不変条件9。テンプレートを開く経路（起動時・`read_user_template`・`pick_json`）をすべて1つの関数へ集約する |
| T-6 | B 案で保存先がリポジトリ外になり、開発中に中身を確認しにくい | 開発効率 | 開発環境（`CHOUHYO_USER_DIR` 未設定）ではリポジトリ直下が使われるので影響なし。配布環境の確認は `%APPDATA%` を開く |

## 4. (b) ページ全体からの枠候補一括生成（#73）

対象要件: 07 v1.3 §4.1(b)・§5.2（FR-F14〜F25・F47）・§8.2（AC-F16〜F23・F55）・NFR-F02。
前提: (a') は `5a3b660`／`1541239`、(t) は #72 で完了。**(b) は本件で新しい画像処理を足すことを許された唯一の範囲**（07 §9.1）。

### 4.1 線分抽出（FR-F47・FR-F15）

#### 4.1.1 現行の限界（実測）

| 事実 | 根拠 |
|---|---|
| `line_positions` は射影ピークの**中心座標しか返さない**（端点を持たない） | `projection.py:16-35` |
| `detect_ruled` は `--region` 必須で、`len(h_lines) < 3` なら `None`＝**1マス枠は必ず落ちる** | `grid.py:39-53`・`cli.py:669` |
| `H_COVERAGE = 0.50`／`V_COVERAGE = 0.35` は**領域幅・領域高に対する比率** | `projection.py:11-12`・`grid.py:50-52` |
| これらの定数は `estimate_shift` と共有 | `align.py:89`（`from .projection import H_COVERAGE, LINE_GAP, V_COVERAGE`） |

**`projection.py` は1行も変えない。** 変えると平行移動推定＝読み取り出力が動き、NFR-F08・AC-F45 と矛盾する（07 FR-F15 の⚠️）。(b) は**新しいモジュール**（`core/chouhyo_ocr/segments.py`）に閉じる。

#### 4.1.2 方式の比較

| 案 | 内容 | 判断 |
|---|---|---|
| **S-1（推奨）ランレングス＋行マージ** | 走査線ごとに暗画素の連続ラン（長さ ≥ 絶対長）を取り、隣接する走査線のランを重なりで束ねて1本の線分にする。垂直は転置して同じ処理 | **採用**。端点が自然に得られる／依存追加ゼロ（numpy のみ）／決定論／O(W×H) の1パス |
| S-2 連結成分ラベリング | 二値画像をラベリングし、細長い成分を線分とみなす | 不採用。ラベリングを自前実装すると S-1 より重く複雑（`scipy` は現在の依存に無い——`core/pyproject.toml` の依存は空で、実行時は numpy・Pillow のみ）。新規依存の追加は供給網レビューを要し、得るものが端点だけなら割に合わない |
| S-3 交点検出のみ | Hough 変換等で直線を求める | 不採用。OpenCV も Hough も依存に無い。斜めの直線を扱う必要が無い（帳票の罫線は軸平行・02 §6.2 の傾き補正後を前提にできる） |

#### 4.1.3 S-1 の手順

```
segments.detect_segments(binary, dpi) -> (h_segments, v_segments)

  水平（垂直は binary.T で同じ関数を通し、返り値の x/y を入れ替える）:
  1. 各行 y について、暗画素の連続ラン [x0, x1] を取る
     - ラン内の切れは HOLE_MAX 以下なら同一ランとして繋ぐ（かすれ・交差切れ）
     - 長さ (x1-x0+1) >= MIN_SEG_LEN のランだけ残す
  2. 隣接行のランを束ねる
     - y 差 <= 1 かつ x 区間の重なり率 >= OVERLAP_MIN の組を同じ線分へ
     - 束の厚み（行数）が THICK_MAX を超えたら線ではなく塗り潰し面とみなし捨てる
  3. 束ごとに1本の線分を作る: y = 束の重心（小数1桁）、x0 = 最小、x1 = 最大
  4. 同一線の橋渡し: |y 差| <= COLLINEAR_TOL かつ x の隙間 <= GAP_BRIDGE の
     2線分を1本に統合する（罫線が縦線との交差で切れる・かすれる分を吸収）
```

**戻り値**は `Segment(kind, pos, start, end, thickness)` の配列。`kind` は `"h"`／`"v"`、`pos` は水平なら y・垂直なら x、`start`/`end` は端点（水平なら x）。**端点を持つ**ことが `line_positions` との差（FR-F47）。

#### 4.1.4 閾値（すべて `render_dpi` 由来の絶対長・FR-F15）

`BASE_DPI = 300` 較正値。実効値は `round(定数 × dpi / BASE_DPI)` で、既定 dpi=300 なら表の値そのもの。**領域幅に対する比率は1つも使わない**（循環を切る・07 §4.1(b)）。

| 定数 | 値（@300dpi） | 実寸 | 根拠 |
|---|---|---|---|
| `MIN_SEG_LEN` | 60 px | 約 5.1 mm | 記入欄として成立する最小の辺。formB の最小欄「受付日」は 300×80 px で、短辺 80 px がこれを上回る。文字のストロークは 3〜10 px 程度で、60 px 連続の暗画素が1行に並ぶのは罫線・下線・塗り潰しに限られる |
| `HOLE_MAX` | 4 px | 約 0.34 mm | 1行内のかすれ。`LINE_GAP = 6`（`projection.py:13`）より小さく取る——あちらは「別の線を同一視する」量、こちらは「1本の中の欠け」 |
| `THICK_MAX` | 12 px | 約 1.0 mm | これを超える帯は罫線でなく塗り潰し（綴じ穴帯・黒ベタ）。02 の除外領域と併用 |
| `OVERLAP_MIN` | 0.5 | — | 隣接行のランを同じ線分に束ねる重なり率 |
| `COLLINEAR_TOL` | 2 px | — | 同一線とみなす `pos` の差。`estimate_shift` の端一致許容（±2px・`align.py:145`）と同じ流儀 |
| `GAP_BRIDGE` | 12 px | 約 1.0 mm | 交差切れの橋渡し。列間隙が 0 の現存テンプレート（07 §4.1(f) の表）でも隣の欄を巻き込まない |
| `MIN_RECT_SIZE` | 20 px | 約 1.7 mm | 候補矩形の最小辺（§4.2） |
| `MAX_RAILS` | 200 /軸 | — | 打ち切りガード（§4.6） |

矩形化の側にはもう2つ定数がある。線分抽出（`segments.py`）ではなく `grid.py` に置いた——線分の性質ではなく「矩形と認めるかどうか」の基準だから。

| 定数 | 値 | 置き場所 | 根拠 |
|---|---|---|---|
| `EDGE_COVER` | 0.90 | `grid.py` | 閉じた矩形と認める4辺それぞれの線分被覆率の下限（§4.2.1 手順3）。同じ基準を「内壁が閉じているか」の判定にも使う |
| `PITCH_TOL` | 2 px@300dpi | `grid.py` | 表ブロックへ束ねるときの行ピッチの許容差（§4.2.1 手順5）。**他の px 定数と同じく dpi でスケールする**（実効値 `round(2 × dpi / 300)`）。`COLLINEAR_TOL` と同値だが意味が違う（あちらは線分の同一視、こちらは行ピッチの揺らぎ）ため別に持つ |

⚠️ **これらは実測で較正していない暫定値**（`MIN_SEG_LEN` のみ formB の欄寸法から下限を確認した）。実装後の実走では表の値のまま AC-F16／AC-F17／AC-F18 が通った（`core` で `..\.venv\Scripts\python.exe -m pytest tests\test_detect_frames.py tests\test_segments.py -q` → 30 passed・2026-09-03）が、通ったのは formB／formC／sample-1 の3枚に対してであり、値の根拠が実測に置き換わったわけではない。Q-F6 と同じ扱いで較正対象に残す。

**較正時の注意: `MIN_RECT_SIZE`(20px) と `MIN_SEG_LEN`(60px) の関係（2026-09-03）**

「線分として拾えるのは 60px 以上なのだから、そこから組んだ矩形の辺は必ず 60px を超え、`MIN_RECT_SIZE`(20px) による `too_small` 除外には到達しないのでは」という筋は、**半分しか当たらない**。線分長が下限を課すのは**線が走っている向きの辺**だけで、もう一方の辺は**レール間隔**——隣り合うレールが 10px しか離れていなければ、10×113px の細長い原子セルができる。

実測（2026-09-03・`workdir/pages/sample-1.png` に `detect_segments` → `_cluster_rails` → `_grid_atomic_cells` を直接通した結果）: 原子セル 146 個のうち **6 個が幅 10px**（高さは 61〜116px）で `too_small` に落ちた。formB では 0 件。**`too_small` は死んだ分岐ではない。**

較正で効いてくるのは次の2点:

- `MIN_RECT_SIZE` を下げると、罫線が二重線・太線で2本のレールに割れた紙で、線そのものを「細い欄」として拾い始める。上げすぎると細い記入欄（印影欄・チェック欄）が消える
- `zero_reason:"all_filtered"` の発火源として実際に確認できているのは `page_outline`（`test_zero_reason_all_filtered_when_only_page_outline_detected`）。`too_small` だけで全滅する紙は今のところ観測していない——**「観測していない」であって「起きない」ではない**ので、案内文（§4.2.4）は理由コードごとに用意したまま残す

#### 4.1.5 二値化と除外領域

- 二値化は **`align.binarize_face` を使わない**。あれは面ローカルの除外マスク前提（`align.py:205-217`）で、(b) はページ全体・テンプレート未確定の入力も扱う。`segments` 側は `gray < th`（`align._otsu` をページ全体に適用）だけを行う
- `--template` が与えられ、**かつ入力画像の寸法がテンプレートの `image_size` と一致する**場合のみ、面の `exclusions` を**検出前に白で潰す**（綴じ穴帯・黒ベタが線分として拾われるのを防ぐ）。テンプレート未指定、または寸法が違う場合は潰さない——座標系が一致しないまま潰すと関係ない場所を消すため（§4.4 `template_skip_reason`）
- 傾き補正は**しない**。編集画面が (a') で位置合わせ済みの下地（`-aligned.png`）を持っているときはそれを渡す前提（`Editor.tsx` は既に `expand-page` 経由の画像を開く）。生画像を直接渡した場合に傾いていれば候補が歪むが、それは利用者が見て分かる（残差 px を出す・FR-F25）

### 4.2 矩形化と候補の粒度（FR-F16・FR-F17）

#### 4.2.1 手順

```
1. レール化: h 線分を pos で COLLINEAR_TOL 以内にクラスタ -> 水平レール（y と、その上の線分群）
             v 線分も同様 -> 垂直レール
2. 交点: 水平レール y と垂直レール x が交わる条件は
         「その y のいずれかの線分が x を含む」かつ「その x のいずれかの線分が y を含む」
         （端点に ±COLLINEAR_TOL の余裕）
3. 閉じた矩形: レール対 (y1<y2, x1<x2) の4隅がすべて交点で、
         かつ4辺それぞれの線分被覆率 >= EDGE_COVER(0.90) を満たすもの
4. 原子セル: 3 のうち、内部に他のレールが「辺を貫通して」通っていないもの
         （y1<y<y2 の水平レールが [x1,x2] を被覆する、等が無い）
5. 表ブロックへの束ね:
   - 原子セルを y 帯ごとに行へまとめる
   - 各行の「垂直レール集合（署名）」が一致し、かつ連続する行のピッチが
     ±PITCH_TOL(2px) で一定な run を1つの表ブロックにする
   - 行数 >= 2 が表の条件。1行しかない run は表にしない
   - 表候補 = {origin(x,y), rows, row_pitch(平均), row_height(セル高の中央値),
              columns: [{x_offset, width}], residual_px}
     residual_px は行境界の実測 y と等ピッチ当てはめの最大差（grid.GridFit と同じ定義）
6. 欄候補: 表に吸収されなかった原子セル -> Rect（単発欄候補）
```

**4 の「原子セル」と 3 の「辺被覆」がこの設計の要。** 隅の交点だけで矩形と認めると、離れた2つの箱が同じレール上に並んでいるだけで「1つの大きな箱」を誤検出する。formB がまさにその形をしている（§4.3）。

**実装時判断（2026-09-03）: 手順3〜4 の原子セル算出を「レール対の総当たり」から「基本グリッドセルの Union-Find」へ変えた。**

原案は「レール対 (y1<y2, x1<x2) の全組み合わせについて4辺の被覆を測り、内部を貫くレールが無いものを残す」だった。これは O(nh² × nv²) で、レールが密な帳票で破綻する——`testdata/formC/formC-1.png`（2490×3510・水平レール37本・垂直レール9本）を通したところ実装中の性能検証で**50 秒超**を実測し、NFR-F02（3.0 秒）を1桁超えた（実測値の記録は `core/tests/test_detect_frames.py::test_formc_completes_within_budget` の docstring）。

実装は次の形にした（`grid._grid_atomic_cells`）:

1. 隣接するレールが囲む**基本グリッドセル**（(nh-1)×(nv-1) 個）を単位にする
2. 各セルの右壁・下壁について、その区間の線分被覆が `EDGE_COVER` に満たなければ**隣のセルと結合**する（Union-Find）
3. 結合後の連結成分のうち、成分のセル数が外接矩形のセル数と一致する（＝L字・凹み・穴が無い）ものを矩形とし、外周4辺がすべて閉じていることを確認して原子セルとする

計算量は O(nh × nv)。辺被覆の閾値（`EDGE_COVER` 0.90）は原案と共有する。

⚠️ **これは原案の近似であり、同値ではない。** 原案は「4辺が閉じていて内部を貫くレールが無いレール対」を列挙するが、実装は**グリッドセル単位の連結成分**を単位にするため、**L 字・凹み・穴のある連結成分は矩形と認めず候補から落とす**（成分のセル数が外接矩形のセル数と一致することを条件にしている）。落ちた成分は `excluded` の `reason:"non_rectangular"` に計上し、総数は `stats.components` と `stats.rects` の差として読める——黙って消さない。原案なら L 字の内側に取れたはずの小さな矩形が候補に出ない場合があり、その面では利用者が手描きか「くり返し行」からの生成に回ることになる。**この差を許容したのは性能の理由**（下記）であって、L 字を落とすことが望ましいからではない。

切り替え後の formC は **1.01 秒**、formB（1800×1200）は **0.23 秒**切り替え後の formC は **1.01 秒**、formB（1800×1200）は **0.23 秒**（2026-09-03 実測・`.venv/Scripts/python.exe` で `detect_frames(binary, dpi=300)` を`time.perf_counter` で挟んだ単体計測・画像読み込みは含まない）。

「4隅が交点であること」の明示チェックは実装していない。辺の被覆率が 0.90 以上あれば両端付近もほぼ確実に線を持つため、手順2の交点判定を独立の段として持つ必要が無かった。

**手順5 の確定（2026-09-03・core レビュー）: `columns` は行に実在する原子セルから作り、セルの無い帯で表を分割する。**

行の列構成をレールの全境界から作ると、その行に原子セルが存在しない帯（＝閉じた内壁が無く矩形になっていない区間）まで列として並んでしまう。**水平罫線を左右で共有している2つのブロックが1つの表に潰れる**のがこれで、出荷テンプレの `family`（front・5行×2ブロック）と `detail`（back・14行×2ブロック）がまさにこの形をしている（§4.7.1 の実測）。

したがって `columns` は次のように作る:

- 各行について、**実在する原子セル**の `x` 範囲を左から並べたものを列とする（`x_offset` は行の先頭セル基準）
- 隣接する原子セルの間に**隙間**（前のセルの右辺と次のセルの左辺が一致しない）があれば、そこで**表を分割**し、左右を別々の表候補として返す
- 分割後の各ブロックについて、行 ≥ 2・署名一致・等ピッチの条件（手順5）を改めて判定する

**`PITCH_TOL` も他の px 定数と同じく dpi でスケールする**（2px@300dpi・`round(2 × dpi / 300)`）。150dpi の入力で 2px 固定のままだと、300dpi 換算で 4px ぶんの揺らぎを許すことになり、別ピッチの行が同じブロックに束ねられる。

#### 4.2.2 除外規則

| 規則 | 内容 | 理由 |
|---|---|---|
| ページ外形 | 幅・高さとも面（またはページ）の 90% 以上を占める矩形を捨てる | 用紙の枠線を「巨大な欄」として出さない |
| 最小サイズ | 辺のどちらかが `MIN_RECT_SIZE`(20px) 未満を捨てる | 罫線交差のノイズ |
| 入れ子 | 手順4で自動的に落ちる（原子セルのみ残す） | 表の外枠と中のセルを二重に出さない |
| 重複 | 同一矩形（±COLLINEAR_TOL）は1つに畳む | レールのゆらぎ由来 |
| 表に吸収済み | 表ブロックを構成した原子セルは欄候補に出さない | 二重提示の防止 |
| 非矩形の連結成分 | L 字・凹み・穴のある連結成分は候補にしない（`excluded` の `non_rectangular`） | 実装がグリッドセル単位の連結成分で近似しているため（§4.2.1 の⚠️）。原案の定義には無い除外で、近似の代償 |

#### 4.2.3 面の割り当てと座標系

- **返す座標は「ページ座標」で統一する。** 編集画面は `fields`／`tables` をページ座標で保持し、保存時に `splitY` で面ローカルへ変換している（`Editor.tsx:660-672`）。core が面ローカルで返すと GUI 側に逆変換が要り、座標系の取り違えという最も高くつくバグの余地を作る
- `--template` が**あり寸法が一致する**場合: 各候補の中心が入る面の `face_id` を付ける。面の境界をまたぐ候補は候補から外し、`excluded` に `reason:"straddles_face"` として出す（黙って消さない）
- `--template` が**ない**場合、または**寸法が違って適用をやめた**場合: `face_id` は `null`（CLI 出力では `"page"`）。面の割り当ては GUI が自分の `splitY` と `faceRangeContains`（`Editor.tsx:598-609`）で行う。**core に GUI の編集中状態（splitY）を渡さない**

#### 4.2.4 成立条件と不成立の明示（FR-F17）

候補ゼロでも `ok:true` を返し、**理由を必ず添える**（黙って空配列を返さない）。

| `zero_reason` | 条件 | GUI の案内 |
|---|---|---|
| `no_lines` | 線分が0本 | 「罫線が検出できません。等分割生成で作るか、手で枠を描いてください」 |
| `no_rect` | 線分はあるが閉じた矩形が0個 | 「罫線はありますが、閉じた枠になっていません（下線のみの帳票など）。等分割生成か手描きへ」 |
| `all_filtered` | 矩形はあったが外形・最小サイズで全部落ちた | 「検出できた枠が用紙の外枠だけでした」 |
| `too_many_lines` | レールが `MAX_RAILS` 超で打ち切り | 「線が多すぎて解析を打ち切りました。領域を指定する既存の生成をお使いください」 |

### 4.3 formB での検算（AC-F16 の成立確認）

**実測**（実行日 2026-09-03・`.venv/Scripts/python.exe` で `testdata/formB/formB-1.png` の射影を直接取得・実 API 送信なし）:

```
size (1800, 1200) / ink 27957 px
水平レール（行射影 > 200px）: 100, 179, 300, 380, 460, 540, 620, 700, 750, 899
垂直レール（列射影 > 60px） : 100, 300, 450, 499, 600, 850, 899, 949
```

テンプレート `formB-v1.json` との対応:

| 定義 | 期待矩形 | 実測レール |
|---|---|---|
| 欄 `氏名` | (100,100,400,80) | x 100–499・y 100–179 ✓ |
| 欄 `受付日` | (600,100,300,80) | x 600–899・y 100–179 ✓ |
| 欄 `備考` | (100,750,850,150) | x 100–949・y 750–899 ✓ |
| 表 `visit` | origin(100,300)・5行・pitch 80・列 200/150/400 | y 300/380/460/540/620/700（6レール・ピッチちょうど 80）・x 100/300/450/850 ✓ |

**この構造で §4.2 の手順を通すと、期待どおり「表候補1 + 欄候補3」になる。**

- 氏名と受付日は**同じ水平レール（y=100・179）を共有する**が、x=499 と x=600 の間に水平線分が無いため、(100,100)-(899,179) は**辺被覆 0.90 を満たさず矩形にならない**。§4.2.1 手順3 の辺被覆判定がここで効く（隅の交点だけを見る実装だと、この2欄が1つの大きな欄に潰れる）
- 表の5行はすべて垂直レール署名 {100,300,450,850} が一致し、ピッチが厳密に 80 なので1ブロックに束ねられる。`residual_px` は 0.0〜0.8 の見込み（06 §3.4 の `--region` 指定時の実測が 0.8）
- 3つの欄はそれぞれ署名が {100,499}／{600,899}／{100,949} で互いに異なり、行数も1なので表にならず欄候補へ落ちる

⚠️ この検算は**射影のレール位置まで**を確認したもので、`segments.detect_segments` の実装を通した結果ではなかった（当時は実装前）。**実装後の実走で AC-F16 は成立**——表候補1（`row_pitch` 80.0・列幅 200/150/400・`residual_px` 0.0）＋欄候補3（2026-09-03 実測・値は §4.4 の例に載せた）。欄の矩形が定義より 1px 小さい（400×80 の定義に対し 399×79）のは、レール位置を境界としてそのまま返すため（罫線の太さぶんを内側にも外側にも足さない）。採用後の1px は人が詰める前提で、テストは欄候補の寸法を条件にしていない——見ているのは個数3件と `face_id`・`overlaps_existing` だけ。

### 4.4 JSON 契約（新サブコマンド `detect-frames`）

```
detect-frames --input <img|pdf> [--page N] [--dpi N] [--template <path>]
```

- `--input` は画像または PDF（`ingest.expand` が PDF なら該当ページを展開・画像はそのまま。`expand-page` と同じ経路）
- `--dpi` は**展開 dpi**（既定 300）。`--template` があるときはテンプレートの `render_dpi` を優先する（FR-F23。06 §7 の未配線を繰り返さない）
- `--template` は任意。あると ①除外領域の白潰し ②`face_id` の割り当て ③`overlaps_existing` の算出 が有効になる。**ただし入力画像の寸法がテンプレートの `image_size` と一致するときだけ**——違えば3つとも行わず `template_applied:false` / `template_skip_reason:"size_mismatch"` を返す（下記）

```json
{"event": "detect_frames", "ok": true, "input_size": [1800, 1200],
 "candidates": [
   {"kind": "table", "face_id": "page",
    "rect": {"x": 100, "y": 300, "w": 750, "h": 400},
    "blocks": [{"x": 100, "y": 300, "rows": 5}],
    "row_pitch": 80.0, "row_height": 80,
    "columns": [{"x_offset": 0, "width": 200}, {"x_offset": 200, "width": 150},
                {"x_offset": 350, "width": 400}],
    "residual_px": 0.0, "overlaps_existing": false},
   {"kind": "field", "face_id": "page",
    "rect": {"x": 100, "y": 100, "w": 399, "h": 79},
    "residual_px": 0.3, "overlaps_existing": false}],
 "stats": {"lines_h": 12, "lines_v": 10, "rects": 18, "rails_h": 10, "rails_v": 8,
           "components": 19},
 "excluded": [{"reason": "non_rectangular", "count": 1}],
 "zero_reason": null,
 "template_applied": null, "template_skip_reason": null,
 "elapsed_ms": 231}
```

**上は実装の実出力**（2026-09-03・`cd core` して `..\.venv\Scripts\python.exe -m chouhyo_ocr.cli detect-frames --input ..\testdata\formB\formB-1.png`。欄候補は3件返るうち先頭1件のみ抜粋、他も同じ形）。契約と実装が一致していることを確認済み。同じ画像に `--template ..\testdata\formB\formB-v1.json` を付けると `face_id` が `"front"`・`overlaps_existing` が `true`・`template_applied` が `true` に変わる。

設計案（v1 の記述）から変わった点:

| 案 | 確定 | 理由 |
|---|---|---|
| `id`（`"c1"`…） | **無し** | GUI が受け取り順で仮 ID を振る。core が振ると「同一応答内で一意」を両側が管理することになり、採用・除去の対象取り違えの余地が増える |
| `source`（`"ruled"` 固定） | **無し** | 1値しか取らないフィールドは分岐の保険にならない。増えたときに足す |
| `blocks:[{"origin":{"x","y"},"rows"}]` | `blocks:[{"x","y","rows"}]` | `template.py` の `tables[].blocks[]` が平坦な `{x, y, rows}`。GUI の `detect-grid` 受け取りコードと同形にする |
| `excluded` の `reason` 3種・count 0 も列挙 | `page_outline` / `too_small` / `straddles_face` / **`non_rectangular`** の4種。**0 件の reason は出さない** | `non_rectangular` は §4.2.1 手順4 の近似で落ちた連結成分（L 字など）。0 件を並べても読み手の判断は変わらない |
| `face_id` は `--template` 無しで `null` | `--template` 無しは **`"page"`** | GUI 側で `null` と面 ID の2系統に分岐させない。`grid.detect_frames` の返り値は設計どおり `None` で、畳むのは CLI 出力の段 |
| （記載なし） | `input_size`: `[width, height]` | 開いた画像と core が見た画像の寸法が同じかを GUI が確かめられる |
| （記載なし） | `template_applied`（`true` / `false` / `null`）と `template_skip_reason` | 下記 |
| `stats` は線分・矩形・レール数 | `components` を追加 | 手順4の連結成分の総数。`rects`（＝原子セルの数）との差が `non_rectangular` の発生量になり、検出が崩れた面の切り分けに使う |

**`template_applied` / `template_skip_reason`（2026-09-03 追加）**: `--template` を渡しても、入力画像の寸法がテンプレートの `image_size` と違えば座標系が一致しない。この状態で除外領域を白潰しすると**関係ない場所を消し**、面割り当てと重なり判定も別の紙の座標で行うことになる。そこで寸法が違う場合は3つの処理をいずれも行わず、`template_applied:false`・`template_skip_reason:"size_mismatch"` を返して `face_id:"page"`・`overlaps_existing:false` に落とす（`--template` 無しと同じ扱い）。**黙って「テンプレートを適用したつもりの結果」を返さない。** テンプレートを渡していない場合は **`template_applied:null`**・`template_skip_reason:null`——「適用しなかった」（false）と「適用する対象が無かった」（null）を区別する。

- **座標はページ座標**（§4.2.3）。`rect` は表候補にも付ける（GUI が候補を1つの矩形として描くため）
- `kind:"table"` の `blocks`／`row_pitch`／`row_height`／`columns` は `template.py` の `tables[]` スキーマにそのまま写る形。`row_height` は**セル高の中央値そのまま**で、`grid.ROW_INSET`（罫線ぶんの控え）は引かない——あれは領域指定の等分割側の経験則で、本節の閾値表（§4.1.4）に無い量だから。formB では `row_pitch` 80 に対し `row_height` 80 が返る（罫線1本ぶんの差は採用後に人が詰める前提）
- `residual_px` は**表候補**では行境界の実測 y と等ピッチ当てはめの最大差、**欄候補**では矩形の4辺と実測線分のずれの最大値（formB の欄候補3件で 0.3／0.0／0.3・2026-09-03 実測）。歪んだ紙・かすれた罫線で候補の信頼度を人が判断するための値（FR-F25）
- `overlaps_existing` は `template_applied:true` のときのみ意味を持つ。判定は既存 `template.cells` の全 rect と候補矩形の重なり。GUI 側は編集中の枠が core の知らない状態にあるため、受け取った値と**現在の枠での再判定を OR して**使う（`Editor.tsx` 実装済み）
- `zero_reason` は候補が0件のときだけ値が入る（§4.2.4）
- **PDF の展開先は `workdir/detect_frames_pages/`**。編集画面の下地（`workdir/editor_pages/`）と分ける——同じ場所に混ぜると、候補生成のために展開した画像が編集画面の掃除で消えたり、その逆が起きたりする。どちらも `workdir` 配下なので `purge --yes` の対象（README の中間データの説明と同じ扱い）

**Rust 側の白リスト追加が要る**: `ALLOWED_SUBCOMMANDS`（`lib.rs:23`）へ `detect-frames`、`allowed_flags`（`lib.rs:35`）へ `("--input", true)`・`("--page", true)`・`("--dpi", true)`・`("--template", true)`、`TEMPLATE_ACCEPTING_SUBCOMMANDS`（`lib.rs:125`）へも追加（`inject_default_template` の対象にする）。`--input` は既存の `check_scope_dir` で `editor_pages` ＋ picked に限定される。

### 4.5 編集画面（FR-F18〜F25）

#### 4.5.1 候補の状態

- `Cand` を新しい state に持つ。**確定枠（`fields`／`tables`）とは別配列**で、保存対象には含めない
- `type Cand = { id: string; kind: "table" | "field"; rect: Rect; faceHint?: string; residual: number; overlaps: boolean; table?: TableSpec; name: string }`
- **描画（FR-F18）**: 破線＋半透明の塗り＋角の小マーカーで確定枠と区別する。**色だけに依存しない**（AC-F22）。ラベルは仮名（`field_01` 等）に `?` を付ける等、テキストでも候補と分かるようにする。実際のコントラスト・記号選定は accessibility（ラミィ）へ委ねる

#### 4.5.2 ⚠️ 一括採用と既存の切り抜き（carve）の衝突

**FR-F19（既存枠を上書き・削除しない）と、編集画面の現行仕様が正面から衝突する。** `Editor.tsx` は「置いた/動かした枠の下にある他の文字欄を自動で切り抜く（後から置いた枠が勝つ）」を持つ（`Editor.tsx:2775` 前後の `carveField`／`evaluateCarve`）。候補をそのまま採用する経路がこの切り抜きを通ると、**既存枠が削られる**——FR-F19 違反になる。

設計は次のとおり:

1. **候補の採用は carve 経路を通さない。** `setFields([...fields, ...採用分])` の追加のみで、`resolveOverlaps`／`carveField` を呼ばない
2. ただし重なったまま保存すると**保存時の重なり検証（issue #24）で拒否される**。したがって **`overlaps_existing:true` の候補は一括採用の対象から外す**（既定でチェックを外した状態にする）
3. 重なる候補を利用者が**個別に**採用しようとしたときだけ、「既存の枠と重なります。採用すると保存時に検証で弾かれます」と警告を出し、採用するかどうかを人に決めさせる（自動で切り抜かない）

これは 07 に書かれていない挙動なので §7-10 に修正提案として挙げる。

#### 4.5.3 操作（FR-F20・FR-F21）

| 操作 | 挙動 |
|---|---|
| 候補生成 | `detect-frames` を呼び、`cands` を差し替える。**既存の候補は捨てる**（2回生成して二重に増えない） |
| 一括採用 | `overlaps_existing:false` の候補をすべて確定枠へ。表候補は `tables` へ、欄候補は `fields` へ |
| 一括除去 | `cands` を空にする。**確定枠は触らない**（AC-F20） |
| 個別採用 | 候補1つを確定枠へ。重なりがあれば §4.5.2-3 の警告 |
| 個別除去 | 候補1つを `cands` から外す |

**Undo の粒度（FR-F21・AC-F21）**: 現行の履歴は `Snap = { fields, tables, excls, splitY }` で 400ms 静止ごとに1コマ積む（`Editor.tsx:3151-3167`）。**候補は `Snap` に含まれないため、このままだと生成を Undo で戻せない。**

- `Snap` に `cands` を足す（`type Snap = { fields; tables; excls; splitY; cands }`）
- 候補生成の完了時に **`pushHistoryNow`（`Editor.tsx:3177`）を使って即座に1コマ積む**（400ms 待ちの経路に任せない）。並べ替えボタンが「1クリック=1コマ」のために同じ手を使っている前例がある
- `restoreSnap` に `setCands(snap.cands)` を足す

#### 4.5.4 生成中の表示と中断（FR-F22）

- 生成中は「枠候補を生成しています…」を出し、**キャンバスの枠操作を無効化**する（`canvasInteractionAllowed` とは別のフラグ。画像の有無とは独立）。パンとズームは許可
- 中断は現行の `kill_core`（`lib.rs:663`）で行う。中断したら `cands` は変更しない
- 実行中は生成ボタンを無効化（二重起動の防止）

#### 4.5.5 仮名と一括リネーム（FR-F24）

- 欄候補: `field_01`・`field_02`…（採用時に既存 `field_id` と衝突しない番号へ自動送り）
- 表候補: `table_01`、列は `列1`…（現行 `detect-grid` の受け取りと同じ）
- **一括リネーム**: 表の列名をカンマ区切りで一度に置き換える純関数 `renameTableColumns(table, "来場日,人数,メモ")`。列数が合わないときは適用せず理由を返す
- **OCR は使わない**（07 §9.2・課金を発生させない）

#### 4.5.6 既存 `detect-grid` との共存

- `detect-grid`（領域指定・`pending` が必要・`grid.py`）は**残す**。ページ全体の検出が成立しない帳票（`zero_reason` が付いた場合）の退避先であり、FR-F31 の案内先でもある
- UI 上は「ページ全体から候補生成」（新・領域不要）と「選択範囲から生成」（現行・`pending` 必須）の2ボタンに分ける
- **core の `detect-grid` は1行も変えない**（`grid.detect_ruled` の `--region` 必須・`len(h_lines)<3` の挙動もそのまま）

### 4.6 性能（NFR-F02）とガード

- 上限は**面1枚あたり 3.0 秒**（NFR-F02・暫定 ※Q-F13）。`detect-frames` は**ページ単位で1回**走り、面切りは内側にある。したがって**2面テンプレートでは実質「ページ1枚 3.0 秒」で測ることになる**——測定単位が要件と食い違う（§7-13）
- 見積り（※未計測）: 2490×3510 の numpy 走査は二値化 0.1 秒＋水平/垂直のラン抽出 0.3〜0.8 秒。矩形化はレール数の積（formB で 10×8=80 対）なので無視できる。**現実的な帳票では 1.5 秒前後**と見るが、根拠は演算量の概算のみで実測ではない
- **ガード**: レールが `MAX_RAILS`(200) を超えたら矩形化に入らず `zero_reason:"too_many_lines"` で返す。網掛け・写真・スキャンノイズの多い紙で組合せ爆発（200×200=4万対 × 辺被覆判定）に落ちるのを防ぐ
- 計測は `scripts/perf_check.py` に項目を足す（AC-F47 と同じ枠組み）

### 4.7 テスト計画（AC-F16〜F23・F55）

| AC | レベル | 落とす場所 |
|---|---|---|
| AC-F55 | **unit** | 合成画像（既知の位置に矩形を描く）に `detect_segments` を掛け、本数と端点が ±2px で一致。**`estimate_shift` の検出条件が変わっていない**ことは既存の位置合わせテスト（`test_alignment_robustness.py` 4件・`test_page_size_guard.py`）が全緑であることで確認 |
| AC-F16 | **unit ＋ CLI 実走** | `testdata/formB/formB-1.png` を**領域指定なし**で `detect-frames` → 表候補1（pitch 80±1・列幅 200/150/400 各±1・residual ≤1.0）＋欄候補3。素材はコミット済みなので **L1 で回せる** |
| AC-F17 | unit | 同画像を 150dpi 相当（900×600）へ縮小し `--dpi 150` を渡す → 候補の個数が等倍と一致。**閾値が dpi 由来の絶対長で効いていることの確認**。※縮小で線が細り検出漏れが出る可能性があり、`MIN_SEG_LEN` の較正が要るかもしれない（§4.10 R-2） |
| AC-F18 | unit | 罫線を全消しした sample-1（`test_alignment_robustness.py` の既存ヘルパと同型で合成）→ `candidates:[]` かつ `zero_reason:"no_lines"`。**空配列だけを返さない**ことを assert |
| AC-F19 | gui-logic（純関数） | `applyCandidates(fields, tables, cands)` が既存要素を1つも削除・変更しない（配列長と各要素の同一性を検査） |
| AC-F20 | gui-logic | 一括採用で `overlaps_existing:false` の全候補が確定枠になる／一括除去で `cands` が空・確定枠は不変 |
| AC-F21 | gui-logic | 生成→Undo1回で生成前へ戻る（`Snap.cands` を含む履歴の検査） |
| AC-F22 | gui-logic ＋ 目視（L3） | 候補と確定枠の描画スタイルが色以外の手掛かり（破線・マーカー・ラベル）で異なる |
| AC-F23 | gui-logic ＋ L2 実走 | 生成中表示・各候補の `residual`・`renameTableColumns` の3点 |
| NFR-F02 | 計測 | `scripts/perf_check.py` で formB（1800×1200）と sample-1（2490×3510）の所要を測る |

**追加で回す素材**: `testdata/formC/formC-1.png`（同寸別様式・`make_formC.py` で生成）と `workdir/pages/sample-1.png`（`.gitignore` 配下＝**L2**）。前者は「別様式でも表候補が取れる」、後者は「出荷テンプレの紙で表候補2ブロック（family・detail）が取れる」ことの確認に使う。**sample-1 の期待個数は未実測**——実装後に実走して記録する（先に期待値を決め打ちしない）。

#### 4.7.1 sample-1 の実測（H-2 適用後・2026-09-03 再実測）

出荷テンプレート `templates/chouhyo-v1.json` と `workdir/pages/sample-1.png`（2490×3510・テンプレートの `image_size` と一致）で再実走した。**H-2（行を x で分割する対応）の前後で結果が変わるため、以下は適用後の値。**

前処理の違いで結果が変わるので2経路とも測った（どちらも `.venv/Scripts/python.exe`・`detect_frames` 単体を `time.perf_counter` で挟む・画像読み込みは含まない）:

| 経路 | 表候補 | 欄候補 | 所要 | back（detail）側 |
|---|---|---|---|---|
| **生画像＋テンプレート**（CLI と同じ前処理: ページ全体 Otsu ＋ 除外白潰し） | 13 | 10 | 0.830 秒 | **1件**（14行・pitch 104.16・7列・`residual_px` 1.65） |
| **`align_page` の合成画像**（`test_sample1_produces_multiple_table_candidates` と同じ） | 10（front 8・back 2） | 15 | 0.757 秒 | **2件**（10行 pitch 104.18・3行 pitch 104.25・各8列） |

```
生画像経路  stats: lines_h 47, lines_v 27, rects 145, rails_h 41, rails_v 26, components 160
            excluded: non_rectangular 2, too_small 6, straddles_face 3
align 経路  stats: lines_h 51, lines_v 22, rects 143, rails_h 37, rails_v 20, components 157
            excluded: non_rectangular 3, too_small 5, straddles_face 3
```

**detail 側（back 面）は pitch が定義どおり出る。** テンプレート定義は pitch 104・14行×2ブロック。`align_page` 経路では pitch 104.18／104.25 の2件に割れ、生画像経路では 14行1件にまとまる——**どちらも pitch は定義と一致し、割れ方だけが前処理で変わる。** テストが条件にしている「`face_id=="back"` かつ pitch 104±2 の表候補が2件以上」は `align_page` 経路で成立する。⚠️ **生画像経路では back の候補が1件なので、この条件は満たさない。** テストと CLI で前処理が違う点は §4.10 R-7 に残す。

**family 側（front 面）は「5行×4列×2ブロック」にはならない。** 定義は pitch 113・5行×2ブロック・4列。`align_page` 経路で返った front 8件の pitch は 60.5／536.9／181.0／134.6／512.4／115.3／110.1／113.8 で、**pitch 113 付近の候補（110.1・113.8・115.3）は含まれる**ものの、行がブロックとして揃わず断片化する。原因は隣接する単発欄（`fields`）との近接で、欄の罫線が family の行罫線と同じレールに載り、行の署名が揃わなくなること。H-2 は幽霊列の除去にあたる対応で、この行分離の問題は別（§4.10 R-4）。

**この面では、候補をそのまま採用しても family 表にはならない。** 利用者は front 側の表候補を捨てて、既存の「くり返し行」からの生成（範囲指定）か手描きに回ることになる。テストが front 側の個数・pitch を条件に含めないのはこのため（実測に基づく判断で、期待値の後付けではない）。

**性能**: 0.757〜0.830 秒。NFR-F02 の 3.0 秒に収まる。formB（0.23 秒）・formC（1.01 秒）との差はレール数（41×26 / 37×20）で説明がつく。

### 4.8 変更ファイルと分担

**インターフェース**: `detect-frames` の JSON 契約（§4.4）だけ。これが決まれば core と GUI は独立に進められる。

| 側 | 担当 | ファイル | 主な変更 |
|---|---|---|---|
| core | シオン（`coder_backend`） | `core/chouhyo_ocr/segments.py`（新規） | `Segment`／`detect_segments`／閾値定数（dpi スケール） |
| core | シオン | `core/chouhyo_ocr/grid.py` | `detect_frames(gray, dpi, template=None)` を追加。**既存の `detect_ruled`／`make_uniform` は1行も変えない** |
| core | シオン | `core/chouhyo_ocr/cli.py` | `detect-frames` サブコマンド |
| core | シオン | `core/tests/test_detect_frames.py`（新規） | AC-F16・F17・F18・F55 |
| GUI | フブキ（`coder_frontend`） | `gui/src-tauri/src/lib.rs` | `ALLOWED_SUBCOMMANDS`／`allowed_flags`／`TEMPLATE_ACCEPTING_SUBCOMMANDS` に `detect-frames` を追加（**それ以外は触らない**） |
| GUI | フブキ | `gui/src/Editor.tsx` | `Cand` 状態・描画・一括/個別の採用と除去・`Snap.cands`・生成中表示・仮名と一括リネーム・ボタン2分割 |
| GUI | フブキ | `gui/tests/gui-logic.test.mjs` | AC-F19〜F23（export リストへの追加が要る） |

### 4.9 守るべき不変条件

1. **`projection.py` を変更しない**（`H_COVERAGE`／`V_COVERAGE`／`LINE_GAP`／`line_positions`）。`estimate_shift` の出力が動くと読み取り結果が変わる
2. **`grid.detect_ruled`／`make_uniform` を変更しない。** (b) は新関数として足す
3. **候補の座標はページ座標。** 面ローカルへの変換は GUI の保存時処理（既存）に任せる
4. **候補の採用は carve を通さない**（FR-F19。§4.5.2）
5. **候補ゼロでも理由を返す**（FR-F17）。空配列を黙って返さない
6. **候補生成1回＝Undo 1コマ**（FR-F21）。`Snap` に `cands` を含める
7. **OCR を使わない**（07 §9.2）
8. 閾値は**すべて `render_dpi` 由来の絶対長**。領域幅比を1つも導入しない（FR-F15）

### 4.10 リスク

| # | リスク | 影響 | 緩和 |
|---|---|---|---|
| R-1 | 閾値8個が未較正 | 実帳票で過検出／未検出 | AC-F16（formB・コミット済み素材）を L1 で回し、formC・sample-1 を L2 で回す。値ではなく根拠から見直す方針を §4.1.4 に明記 |
| R-2 | 150dpi 縮小（AC-F17）で線が細り検出漏れ | AC-F17 が落ちる | `MIN_SEG_LEN` は dpi スケールで 30px になる。線幅は 1px まで細るので `THICK_MAX` は問題にならないが、`HOLE_MAX`（2px へ縮む）がかすれに厳しくなる。**実装時に AC-F17 を先に回して確認する**（※未検証） |
| R-3 | 辺被覆 0.90 が厳しすぎ／緩すぎ | 欄が潰れる／誤結合 | formB の氏名・受付日ペアが分離することを AC-F16 で確認する（§4.3 で構造的に分離することは検算済み） |
| R-4 | 表の束ね条件（署名一致＋等ピッチ）が実帳票で成立しない | 表が欄候補にばらける | **顕在化した**（2026-09-03・§4.7.1）。sample-1 の front 面で family が8件に断片化する（back の detail は pitch 104 で安定）。ばらけても害は無い——人が採用時に判断でき、候補ゼロにもならない。受け皿は「くり返し行」からの生成と手描き |
| R-7 | テスト（`align_page` の合成画像）と CLI（生画像＋ページ全体 Otsu）で前処理が違い、同じ紙でも候補の割れ方が変わる | テストが緑でも利用者の画面では別の個数になる | 実測差は sample-1 の back 側で「2件 vs 1件」（§4.7.1）。編集画面は `expand-page` 経由の位置合わせ済み下地を開くため実運用は前者に近いが、CLI を直接使う開発者は後者を見る。**どちらの経路でも pitch は定義と一致する**ので採用後の実害は小さい。経路を揃えるかは(b) の完了後に判断する |
| R-5 | ページ外形の 90% 判定が、A4 いっぱいの表を誤って捨てる | 本命の表が出ない | 外形判定は**幅と高さの両方**が 90% 以上のときだけ適用する。表は縦方向に余白があるので通常は当たらない。当たった場合に備え `excluded` に理由を出す |
| R-6 | NFR-F02 の測定単位（面1枚 vs ページ1枚）が要件と食い違う | 性能 AC の合否が曖昧 | §7-13 で要件側の明確化を求める |

## 5. (c) 位置合わせ残差・吸着量の記録（#74）

**未着手。**

## 6. (f) 実行時のブロック単位吸着（#75）

**未着手。** ただし前提作業（#70・§10.2-2）の実測が出ているので、設計に効く2点だけ記録しておく。

**吸着の刺激 δ の実測**（`core/tests/helpers_geom.py` の docstring・2026-09-02・`shift_block_y` で back/detail の block_idx=1 のみを y へ δ px 動かし `estimate_shift` を back 面全体へ実行）:

| δ | `estimate_shift` | 面の dy | block1 の残差 | block0 の残差 |
|---:|---|---:|---:|---:|
| 2〜5 | ok | 1 に収束 | δ−1 | 1 |
| 6 | **ambiguous** | — | — | — |

**07 の AC-F30（「成立窓は `2 < δ < 4`＝整数では δ=3 のみ」）は誤り。** 実測では δ=5 まで ok で、`ambiguous` になるのは δ=6 から。面の dy が 1 に収束するため block1 の残差は δ−1 になる。**OFF でラベル混入（02 D-25 の実測: ±2px から）を観測しつつ、ON で許容幅（detail の行間隙 4px）に収まる刺激は δ=4（block1 残差 3px）が最も筋が良い。** (f) のテスト計画はこの値で書く。07 側の訂正提案は §7-4。

**golden**: 着手前コミットは `71384a4`（`workdir/golden/71384a4/`・manifest `testdata/golden_manifest.json`）。AC-F45 の比較対象はこれ。

---

## 7. 要件側の修正提案（07 v1.3 との差分）

**7-1〜7-4 は (a') の設計時に出したもので、07 v1.2 で反映済み**（各項の冒頭に反映先を記す）。履歴として残す。**7-5〜7-9 は (t) の設計時に出したもので、07 v1.3 に反映済み**（保存先は `app_data_dir()` 案が採用された）。**判断が要るのは 7-10〜7-13 の4件**で、うち 7-10（既存枠と切り抜きの衝突）は (b) の実装着手前に結論が要る。

### 7-1. `edge_mismatch` を「不一致」から「判定不能」へ倒す（最優先）

> **状態: 反映済み**（07 v1.2 §0.7 訂正1・FR-F02。実装は `5a3b660` の `format_check.classify`）

- **問題**: 07 §4.1 の対応表は `edge_mismatch` を「不一致」に置き、根拠を「端の線は周期の外にあり、1行ズレでは必ず落ちる。端が合わないのは別の紙の可能性が高い」としている。しかし実測（`testdata/formC/README.md` §3・2026-09-02）は逆を示した——**本物の紙（sample-1）の上端の水平罫線を1本消しただけで `edge_mismatch` に転じる**（N=1 で matched 36/42・det_h 24 と、他はほぼ健全）
- 07 のままだと、**上端が1本かすれた本物の紙で編集画面の枠が消える**。07 §9.1 が「判定不能は不一致ではない——線が取れていないだけの状態で枠を消すと、罫線がかすれた本物の紙で作業ができなくなる」と書いて最も避けたかった事態
- **中核要望は損なわれない**: 同寸別様式（formC）を出荷テンプレートに通すと**両面とも `few_lines`**（front matched 2/22・back 7/42）で、`edge_mismatch` には到達しない。無関係な紙は一致本数の下限（`need`）を通れないため必ず `few_lines` で落ちる。`edge_mismatch` を判定不能へ倒しても「関係ない PDF で枠を描かない」は成立する
- **提案**: 対応表の `edge_mismatch` 行を「不一致」→「判定不能」に変え、根拠を実測へ差し替える。`edge_mismatch` の本来の役目（1行ズレのエイリアシングで**誤った値を出さない**）は run 側の `位置合わせ失敗`＝全〓行で果たされており、編集画面で枠を消す必要はない
- **採らない場合**: 上端が1本かすれた紙で枠が消える。利用者から見ると「正しいテンプレートなのに枠が出ない」で、元の苦情（枠が出続ける）と対をなす別の苦情になる

### 7-2. `few_lines` の二分を軸別にし、残存リスクを明記する

> **状態: 反映済み**（(a) 軸別化 → 07 FR-F45／(c) 探索境界の分岐 → `format_check.py:95`。(b) 残存リスク（かすれが進んだ紙が不一致に落ちる帯）の 07 本文への明記は ※要確認）

- **(a) 軸別化**: 07 FR-F45 は検出線を両軸合算（`len(det_h) + len(det_v)`）で数える。しかし `det` にはテンプレートに無い線も入り（formC front は det 30 vs 期待 16＝187%）、かすれ実験では `det_v` が不変なので**合算比は 50% を割りにくい**。AC-F03 が要求する「50% を下回る最小の N」は、軸別なら **N=12（det_h=7 < 15×0.5）** と確定できるが、合算では成立しない見込み（`det_v` の実測は未取得）。**FR-F45 の比較を軸別（h 軸・v 軸のどちらかが 50% を割れば「乏しい」）に改めたい**
- **(b) 残存リスクの明記**: ★1（7-1）を入れても、**かすれが進んだ本物の紙（sample-1 の N=8〜11）は `few_lines` かつ検出十分に分類され、「不一致」＝枠が消える**。検出線の本数は「線が見えているか」の代理指標として弱い。一致率（スコア）なら分離できる（formC 9〜17% 対 かすれ 24〜86%）が、07 FR-F01 が「スコアは判定に使わない」と定めている（較正の母集団が無いため）。**この帯を 07 §3.5 と同じ形で「残存リスク」として明記し、Q-F6 の較正で解く対象に積みたい**
- **(c) ★3 の追加**: `few_lines` かつ検出十分でも、最良シフトが**探索境界に張り付いている**ときは判定不能へ倒す。07 が `boundary` を判定不能に置いた理由（「大きくズレただけの正しい紙で枠が消える」）は、`few_lines` が先に発火するため対応表のままでは機能しない。`at_boundary` は `_axis_shift` が既に計算しており取得は無料

### 7-3. 既存テストの期待値変更が2件目として必要になりうる（07 §7.2-4）

> **状態: 反映済み**（07 Q-F21 に「記録済みの例外 2件目: `test_alignment_robustness.py`」として登録）

- 07 は「期待値の書き換えによる緑」を禁じ、例外として **`test_leak_guards.py` の1件だけ**を認めている（§0.6）
- FR-F09 が要求する「`AlignError` の一部を `様式不一致` へ付け替える」は、`test_alignment_robustness.py:66-77` の期待値に触れうる。1行ズレ（`dy=104`／`dy=113`）は 7-1 の提案を採れば `位置合わせ失敗` のまま影響を受けないが、**`dx=40,dy=40` のケースは reason が未実測**で、`様式不一致` に変わる可能性がある（※未検証）
- **提案**: 07 §7.2-4 の例外リストに「FR-F09 のバケツ付け替えに伴う `test_alignment_robustness` の期待値更新（バケツ名 → 不変条件への書き直し）」を追記し、理由と日付の記録を要件側で指定する
- 併せて、**AC-F13「全ページのスコアが記録される」と #45 の整列再利用の関係**を明記してほしい。再利用ページは `estimate_shift` を走らせないため、判定のためだけに整列相当の計算を回すか、`unknown` を許すかの判断が要る（本設計は後者を採った）

### 7-4. (f) の刺激 δ と、編集画面が画像でも `expand-page` を通すこと

> **状態: 反映済み**（δ=4 → 07 v1.2 §0.7 訂正2／画像も expand-page を通す → `Editor.tsx:1668-1690`。後者を 07 FR-F04 の付帯条件として明文化するかは ※要確認）

- **AC-F30 の δ**: 07 §0.5-1 は「`SHIFT_RUNNER_DIST = 4`・`SHIFT_GAP_MIN = 2` の制約から成立窓は `2 < δ < 4`＝整数では δ=3 のみ」と書くが、実測（`core/tests/helpers_geom.py`・2026-09-02）は **δ=2〜5 で ok・δ=6 で `ambiguous`**。面の dy が 1 に収束するため block1 の残差は δ−1 になる。**OFF でラベル混入を観測しつつ ON で許容幅 4px に収まる刺激は δ=4（残差 3px）**。AC-F30 の δ を訂正したい
- **編集画面の入口**: 現状 `Editor.tsx` は **PDF のときだけ** `expand-page` を呼ぶ。PNG／JPG は生画像を直接読み込むため位置合わせも様式判定も走らず、**AC-F01／AC-F02 の素材（PNG）ではこのままだと AC-F02 が成立しない**。「編集画面は画像でも `expand-page` を通す」を FR-F04 の付帯条件として明記したい。副作用として**画像ファイルの下地も位置合わせ後の画像に変わる**（PDF と同じ挙動になる）ので、利用者から見た挙動変更として要件に書いておきたい
- **`run_start` の入力パス**: `path=<入力フォルダの絶対パス>` はログに残ったままにした。07 §0.6 の秘匿対象は「テンプレート名・欄名」で入力パスを含まない一方、§7.3 は「絶対パスをログへ出さない」と書いており、読み方が割れる。本設計は §0.6 の表に従って**残す**扱いにした（※要確認）

### 7-5. (t) の保存先を `app_data_dir()` 配下へ変える（Q-F16 の結論）

- **問題**: 07 FR-F26 の `project_root()/templates_user/` は、配布環境では**アプリのインストールディレクトリ**に解決される（`paths.py:19-30`・`lib.rs:161-203`）。インストーラは NSIS per-user で、**更新時に中身が保持されるかは未実測**（07 §10.2-8 は ⏳ 未完了のまま）。07 自身が `templates/` を退けた理由（配布物と同居させない）を1階層ずらして再現している
- **提案**: 保存先を **`app_data_dir()/templates_user/`**（`%APPDATA%\com.holodev.chouhyo-ocr\templates_user\`）に変える。Rust が唯一の決定者となり、環境変数 `CHOUHYO_USER_DIR` で core へ渡す。未設定時（開発環境・CLI 単体）は現行の `project_root()/templates_user/` へフォールバックする（§3.1.3）
- **得られるもの**: ①更新耐性が実測待ちでなく構造で決まる ②非管理者でも書ける ③配布物混入が構造的にゼロ ④**FR-F26 の⚠️（Rust と Python の解決先がズレうる）が消える**——AC-F52 で担保していた一致が、構造で保証される
- **書き換えが要る箇所**: 07 §4.1(t) の保存先／FR-F26 の⚠️と保存先／§7.3 の表の「対象」列／§10.2-8（着手条件の解消）／AC-F24 の「保存先が `templates/` ではないこと」の確認手段
- **採らない場合**: §10.2-8 の実測（NSIS 更新でインストールディレクトリの中身が保持されるか・ACL）を先に済ませる必要があり、(t) の着手条件が残ったままになる

### 7-6. 名前の NFC 正規化に外部クレートが要る（供給網の判断）

> **状態: 決定済み**（2026-09-02・`unicode-normalization` は追加せず、tauri → url → idna 経由で既に解決済みの `icu_normalizer 2.3` を直接依存へ昇格。新規の信頼境界ゼロ・バイナリ増分ゼロ）

- 07 §7.4 は「**NFC 正規化後**に予約名と一致しない」「**NFC 正規化後に case-insensitive** で比較する」を要求している。AC-F51 はこの検証を **Rust の純関数として `cargo test` で表駆動**することを求める
- `gui/src-tauri/Cargo.toml` の依存は `tauri`・`serde`・`serde_json`・`rfd`・`base64` の5つで、**Unicode 正規化を持つものは無い**（2026-09-02 確認）
- **提案**: `unicode-normalization`（MIT/Apache-2.0・実行時依存は `tinyvec` のみ）を追加し、**供給網レビュー（AZKi＋ミオ）を通す**。代替（webview 側で正規化して渡す）はレンダラを掌握されると NFD の名前が通り、見た目が同一の別ファイルを作れる
- 却下される場合は、**「見た目が同じ別名ファイルが作れる」を残存リスクとして 07 §7.4 に明記**したうえで比較を case-insensitive のみに落とす。範囲逸脱（`templates_user/` の外へ書く）は別レイヤ（親ディレクトリ一致検査）で塞がれているため、影響は重複ファイルに留まる

### 7-7. 照合の列挙責務を Rust に一本化する（FR-F28 の表現）

- 07 §4.1(t) は「**`templates/` と `templates_user/` の両方**を走査して各テンプレートに判定関数を掛ける」と書き、FR-F28 は「列挙するのは `templates_user/*.json` のみ・出荷は固定1件の候補」と書いている。前者の表現は「core がディレクトリを走査する」とも読める
- **提案**: 「**列挙とパス検査は Rust が行い、core は渡された絶対パスだけを読む**」を明記する。§7.3 の除外規則（`*.saving.json`・`*.bak`・非通常ファイル・reparse point・サイズ上限）を Python にも実装すると、**セキュリティ判定が2箇所に割れる**
- 併せて、照合の実行手段を **core の新サブコマンド `match-templates --input <img> --shipped <path> --candidate <path>...`**（1プロセスで N 件）として要件に固定したい。NFR-F09 の「照合全体で 3.0 秒」はプロセス起動を含む合計時間なので、1件ごとにコアを起動する実装だと測定単位が変わる

### 7-8. `last_template` は `_validate` で例外を投げない（AC-F60 との整合）

- AC-F60 は「`config.json` の既定テンプレート項目に範囲外を手書きした状態で起動 → **出荷テンプレートへフォールバックして起動し、エラーで停止しない**」を求める
- 一方 `config.py:_validate` は不正値に対して `ConfigError` を上げる設計で、`load_config` がそれを投げると **run/render/remap/verify が全部起動不能**になる（07 FR-F29 の⚠️が指摘している経路そのもの）
- **提案**: 「`last_template` は型が str でない・形式が不正・名前検証に通らない場合、`ConfigError` を投げずに `""`（出荷テンプレート）へ落とす。**`_validate` の中で唯一例外を投げないキーである**」ことを FR-F29 に明記する。`CONFIG_PATH_KEYS`（`is_safe_root` を通すキー群）には入れない——パスではないため

### 7-9. (t) の範囲に関する3点の明確化

- **FR-F49 は新しい Rust コマンドを要さない**。書き出しは `read_user_template` → `pick_json(save=true)` → `write_text`、取り込みは `pick_json` → `read_text` → `save_user_template`（名前検証＋`verify`＋promote を通る）で成立する（§3.7）。07 の書き方は専用コマンドを示唆して読めるので、「既存の選択経路を使う」を明示したい
- **FR-F31 の到達点**（候補ゼロの案内）は、(b) 未実装の段階では「**空のテンプレートで開く**（画像の実寸から `image` を設定）＋等分割生成・手動作図の案内」までとする。枠候補の一括生成は (b) の範囲であり、(t) の完了条件に含めない
- **§10.2-7（`templates_user` に formB を複製）は `37d6e5f` で完了扱いだが、リポジトリに `templates_user/` は存在しない**（2026-09-02 確認）。§7-5 を採ると実体はリポジトリ外になるため、**テストは一時ディレクトリに user dir を作り `CHOUHYO_USER_DIR` を差して回す**方式へ読み替える。素材の元は `testdata/formB/formB-v1.json`（コミット済み）

### 7-10. FR-F19（既存枠を上書きしない）と編集画面の切り抜き（carve）が衝突する

- **問題**: 編集画面は「置いた／動かした枠の下にある他の文字欄を自動で切り抜く（後から置いた枠が勝つ）」を持つ（`Editor.tsx` の `carveField`／`evaluateCarve`・2026-08-31 のユーザー要望）。候補の一括採用がこの経路を通ると**既存枠が削られる**——FR-F19 に正面から違反する
- 一方、切り抜きを通さずに重なったまま採用すると、**保存時の重なり検証（issue #24）で拒否される**。「採用はできたが保存できない」状態になる
- **提案**: FR-F19 に実装レベルの但し書きを足す——①**候補の採用は carve 経路を通さない**（追加のみ）②**既存枠と重なる候補（`overlaps_existing:true`）は一括採用の対象から外す**（既定でチェックを外す）③重なる候補を個別に採用するときだけ「保存時に検証で弾かれます」と警告して人に決めさせる
- **採らない場合**: 一括採用のたびに既存枠が黙って削られるか、保存できないテンプレートができる。どちらも「追加のみ」という要件の意図から外れる

### 7-11. 「表」と「単発欄」を分ける境界を要件に明記する

- 07 FR-F16 は「等ピッチ行の**束**は表候補・それ以外の閉じた矩形は単発欄候補」と書くが、**1行だけの格子**をどちらに入れるかが決まっていない
- 本設計は **行数 ≥ 2 を表・1行は欄**とした。根拠は AC-F16 の期待値——formB の氏名・受付日・備考（いずれも1行1列）が**欄候補3件**として数えられている
- **提案**: FR-F16 に「表候補は**行が2つ以上**連続し、各行の垂直レール署名が一致し、ピッチが一定（±2px）であること。1行しかない閉じた矩形は単発欄候補」を明記する
- 併せて、**AC-F16 の期待値が実際の画像で成立することを検算した**（08 §4.3・2026-09-03 実測）ので、07 §8.2 の AC-F16 に「実測レール位置」を根拠として添えると、実装者が期待値の出所を追える

### 7-12. `detect-frames` のサブコマンド名と Rust 白リストの追加を要件に載せる

- (b) は core の新サブコマンドを1つ増やす。GUI から呼ぶには `ALLOWED_SUBCOMMANDS`（`lib.rs:23`）・`allowed_flags`（`lib.rs:35`）・`TEMPLATE_ACCEPTING_SUBCOMMANDS`（`lib.rs:125`）の3箇所に追加が要る（(t) の `match-templates` と同じ手順）
- 07 §5.2 は機能だけを書き、コマンド名と GUI 境界の追加に触れていない。**`detect-frames --input <img|pdf> [--page N] [--dpi N] [--template <path>]` を要件に固定**したい（`match-templates` を v1.3 で要件に載せたのと同じ扱い）
- `--input` のスコープは既存の `check_scope_dir`（`editor_pages` ＋ picked）で足りる。**新しい権限は要らない**ことも明記したい——(t) と違い、ディレクトリ列挙も一括読み取りも発生しない

### 7-13. NFR-F02 の測定単位（面1枚 vs ページ1枚）

- NFR-F02 は「**面1枚あたり 3.0 秒以内**」。しかし FR-F14 の入力は「面画像」でも、実装は**ページ1枚を1回のプロセス起動で処理し、面切りは内側**に置くのが自然（`format_check.check_page` と同じ構造・面ごとにプロセスを起こすと起動コストが2倍かかる）
- 出荷テンプレートは2面なので、**利用者が待つのはページ1枚ぶんの時間**であり、NFR-F09（照合は「合計 3.0 秒」と明記した）と同じ考え方を採るなら、ここも「ページ1枚あたり」で測るのが実態に合う
- **提案**: NFR-F02 を「**ページ1枚あたり 3.0 秒以内**（面ごとではなく、利用者が待つ合計時間）」へ改める。値は変えない
