# 設計メモ: 枠判定の自動化（07 v1.1 の実装設計）

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
| #71 (a') | 既存判定の接続・3値化・理由コード分離・記録（FR-F01〜F13・F45／AC-F01〜F15） | **§2 に設計を確定** |
| #72 (t) | テンプレートの保存・選択・照合提示 | §3・未着手 |
| #73 (b) | ページ全体からの枠候補生成 | §4・未着手 |
| #74 (c) | 位置合わせ残差・吸着量の記録 | §5・未着手 |
| #75 (f) | 実行時のブロック単位吸着 | §6・未着手 |

**§7 に、07 v1.1 と本設計の食い違い（要件側の修正提案）を列挙する。** §7-1 は 07 §4.1 の対応表そのものを変える提案で、**ユーザー判断を経るまで (a') の実装を確定させない**。

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

### 1.4 残っている穴（3件・いずれも未対応）

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

### 1.5 テスト（実装済み＋追加提案）

| 状態 | テスト | 内容 |
|---|---|---|
| 実装済み | `test_leak_guards.py:28` | `run_start` に `template_path=` が出ない／`template_loaded template_hash=` は出る |
| 実装済み | `:70` | `_fmt` が `template_path`・`field_id` を落とし、`template_hash`・`cell_idx`・`col_idx` を通す |
| 実装済み | `:110` | 危険接頭の警告に記入値も列名も入らない |
| 実装済み | `:141` | AC-F65: 機微な名前を持つテンプレートで実行してもログに名前が出ない |
| **追加提案** | 新規 | 穴 #3 の静的検査（AST 走査） |
| **追加提案** | 新規 | 穴 #2 を塞いだ後の確認: `verify` の app.log に `template_hash` と `cell_idx` が**同時に**存在する |

⚠️ **本書の執筆時点でテストは実行していない。** 上表の「実装済み」はテスト関数が存在することの確認であって、緑であることの確認ではない（証跡3点セットの③が無い）。回帰ゲートの実走は実装側の完了報告に委ねる。

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

**未着手。** 着手条件は 07 §3.4（Q-F16 の確認完了）と §10.2-8。設計は (a') の `check_page` が固まってから書く。

## 4. (b) ページ全体からの枠候補生成（#73）

**未着手。**

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

## 7. 要件側の修正提案（07 v1.1 との差分）

実装を確定させる前に、07 側で判断が要る4件。**7-1 は 07 §4.1 の対応表そのものを変える提案**で、(a') の中核に触る。

### 7-1. `edge_mismatch` を「不一致」から「判定不能」へ倒す（最優先）

- **問題**: 07 §4.1 の対応表は `edge_mismatch` を「不一致」に置き、根拠を「端の線は周期の外にあり、1行ズレでは必ず落ちる。端が合わないのは別の紙の可能性が高い」としている。しかし実測（`testdata/formC/README.md` §3・2026-09-02）は逆を示した——**本物の紙（sample-1）の上端の水平罫線を1本消しただけで `edge_mismatch` に転じる**（N=1 で matched 36/42・det_h 24 と、他はほぼ健全）
- 07 のままだと、**上端が1本かすれた本物の紙で編集画面の枠が消える**。07 §9.1 が「判定不能は不一致ではない——線が取れていないだけの状態で枠を消すと、罫線がかすれた本物の紙で作業ができなくなる」と書いて最も避けたかった事態
- **中核要望は損なわれない**: 同寸別様式（formC）を出荷テンプレートに通すと**両面とも `few_lines`**（front matched 2/22・back 7/42）で、`edge_mismatch` には到達しない。無関係な紙は一致本数の下限（`need`）を通れないため必ず `few_lines` で落ちる。`edge_mismatch` を判定不能へ倒しても「関係ない PDF で枠を描かない」は成立する
- **提案**: 対応表の `edge_mismatch` 行を「不一致」→「判定不能」に変え、根拠を実測へ差し替える。`edge_mismatch` の本来の役目（1行ズレのエイリアシングで**誤った値を出さない**）は run 側の `位置合わせ失敗`＝全〓行で果たされており、編集画面で枠を消す必要はない
- **採らない場合**: 上端が1本かすれた紙で枠が消える。利用者から見ると「正しいテンプレートなのに枠が出ない」で、元の苦情（枠が出続ける）と対をなす別の苦情になる

### 7-2. `few_lines` の二分を軸別にし、残存リスクを明記する

- **(a) 軸別化**: 07 FR-F45 は検出線を両軸合算（`len(det_h) + len(det_v)`）で数える。しかし `det` にはテンプレートに無い線も入り（formC front は det 30 vs 期待 16＝187%）、かすれ実験では `det_v` が不変なので**合算比は 50% を割りにくい**。AC-F03 が要求する「50% を下回る最小の N」は、軸別なら **N=12（det_h=7 < 15×0.5）** と確定できるが、合算では成立しない見込み（`det_v` の実測は未取得）。**FR-F45 の比較を軸別（h 軸・v 軸のどちらかが 50% を割れば「乏しい」）に改めたい**
- **(b) 残存リスクの明記**: ★1（7-1）を入れても、**かすれが進んだ本物の紙（sample-1 の N=8〜11）は `few_lines` かつ検出十分に分類され、「不一致」＝枠が消える**。検出線の本数は「線が見えているか」の代理指標として弱い。一致率（スコア）なら分離できる（formC 9〜17% 対 かすれ 24〜86%）が、07 FR-F01 が「スコアは判定に使わない」と定めている（較正の母集団が無いため）。**この帯を 07 §3.5 と同じ形で「残存リスク」として明記し、Q-F6 の較正で解く対象に積みたい**
- **(c) ★3 の追加**: `few_lines` かつ検出十分でも、最良シフトが**探索境界に張り付いている**ときは判定不能へ倒す。07 が `boundary` を判定不能に置いた理由（「大きくズレただけの正しい紙で枠が消える」）は、`few_lines` が先に発火するため対応表のままでは機能しない。`at_boundary` は `_axis_shift` が既に計算しており取得は無料

### 7-3. 既存テストの期待値変更が2件目として必要になりうる（07 §7.2-4）

- 07 は「期待値の書き換えによる緑」を禁じ、例外として **`test_leak_guards.py` の1件だけ**を認めている（§0.6）
- FR-F09 が要求する「`AlignError` の一部を `様式不一致` へ付け替える」は、`test_alignment_robustness.py:66-77` の期待値に触れうる。1行ズレ（`dy=104`／`dy=113`）は 7-1 の提案を採れば `位置合わせ失敗` のまま影響を受けないが、**`dx=40,dy=40` のケースは reason が未実測**で、`様式不一致` に変わる可能性がある（※未検証）
- **提案**: 07 §7.2-4 の例外リストに「FR-F09 のバケツ付け替えに伴う `test_alignment_robustness` の期待値更新（バケツ名 → 不変条件への書き直し）」を追記し、理由と日付の記録を要件側で指定する
- 併せて、**AC-F13「全ページのスコアが記録される」と #45 の整列再利用の関係**を明記してほしい。再利用ページは `estimate_shift` を走らせないため、判定のためだけに整列相当の計算を回すか、`unknown` を許すかの判断が要る（本設計は後者を採った）

### 7-4. (f) の刺激 δ と、編集画面が画像でも `expand-page` を通すこと

- **AC-F30 の δ**: 07 §0.5-1 は「`SHIFT_RUNNER_DIST = 4`・`SHIFT_GAP_MIN = 2` の制約から成立窓は `2 < δ < 4`＝整数では δ=3 のみ」と書くが、実測（`core/tests/helpers_geom.py`・2026-09-02）は **δ=2〜5 で ok・δ=6 で `ambiguous`**。面の dy が 1 に収束するため block1 の残差は δ−1 になる。**OFF でラベル混入を観測しつつ ON で許容幅 4px に収まる刺激は δ=4（残差 3px）**。AC-F30 の δ を訂正したい
- **編集画面の入口**: 現状 `Editor.tsx` は **PDF のときだけ** `expand-page` を呼ぶ。PNG／JPG は生画像を直接読み込むため位置合わせも様式判定も走らず、**AC-F01／AC-F02 の素材（PNG）ではこのままだと AC-F02 が成立しない**。「編集画面は画像でも `expand-page` を通す」を FR-F04 の付帯条件として明記したい。副作用として**画像ファイルの下地も位置合わせ後の画像に変わる**（PDF と同じ挙動になる）ので、利用者から見た挙動変更として要件に書いておきたい
- **`run_start` の入力パス**: `path=<入力フォルダの絶対パス>` はログに残ったままにした。07 §0.6 の秘匿対象は「テンプレート名・欄名」で入力パスを含まない一方、§7.3 は「絶対パスをログへ出さない」と書いており、読み方が割れる。本設計は §0.6 の表に従って**残す**扱いにした（※要確認）
