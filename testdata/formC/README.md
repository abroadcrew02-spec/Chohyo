# formC: 同寸別様式 実測記録（#70 前提作業・§10.2-1）

## 1. 画像について

- `formC-1.png` は **`make_formC.py`（同ディレクトリ・引数なし・乱数なし）が
  決定論的に生成する合成画像**。git には入れない（`.gitignore` で `*.png` を
  除外・pre-commit フックが `gui/src-tauri/icons`・`gui/public` 以外の png を
  無条件でブロックするため）。使う前に生成する:
  ```
  .venv\Scripts\python.exe testdata\formC\make_formC.py
  ```
- 記入値（手書き相当のインク）は一切描いていない。罫線と印字ラベル（英字）
  のみの白紙帳票（testdata/formB の前例と同じ方針）。
- 寸法は出荷テンプレート `templates/chouhyo-v1.json` と同一の
  **2490×3510**。表の位置・行ピッチ・列幅・本数は明確に異なる構成にした
  （詳細は `make_formC.py` の docstring内比較表）。

## 2. 出荷テンプレートで align_page に通した実測（2026-09-02）

コマンド（`align_page`・`estimate_shift` を直接呼ぶ測定スクリプトを実行。
`.venv/Scripts/python.exe -X utf8`・`PYTHONPATH=core`・実 API 送信なし）:
- `chouhyo_ocr.template.load_template("templates/chouhyo-v1.json")`
- `chouhyo_ocr.align.estimate_shift(...)` を front/back 面それぞれに実行
  （`align_page` 内部と同じ二値化ロジックを面ごとに手動再現。回転は
  formC-1.png がデジタル生成で傾きが無いため angle=0 前提で省略）
- `chouhyo_ocr.align.align_page(img, template)` もフルパスで実行し、
  例外の理由コードを確認

### 面ごとの結果

| 面 | det_h+det_v | exp_h(set)+exp_v(set) | matched/total | ok | reason |
|---|---|---|---|---|---|
| front | 23+7=30 | 6+10=16 | 2/22 | False | **few_lines** |
| back  | 10+7=17 | 15+11=26 | 7/42 | False | **few_lines** |

`align_page(formC-1.png, chouhyo-v1)` は最初に処理される面（front）で
`AlignError("TRANSLATION_UNRELIABLE_few_lines")` を送出して停止した
（後続の back 面は評価されない——`align_page` はページ単位で最初の
失敗面のみ報告する仕様のため、両面の内訳は上表のとおり個別に実測した）。

「同寸だが構造の異なる別様式」を実際の出荷テンプレートに通すと、両面とも
`few_lines`（検出線が期待線と一致する割合が `SHIFT_MATCH_RATIO=0.5` の
下限を満たさない）で位置合わせ失敗に倒れることを実測で確認した。
`boundary`/`ambiguous`/`edge_mismatch` は本条件（無関係な別様式）では
発生しなかった——これらは後述§3のとおり「正しい様式だが部分的に劣化した
入力」で発生する経路であり、両者は異なる実験条件で異なる理由コードに
帰着することが実測から分かる。

## 3. かすれた本物: back/detail の水平罫線を上から N 本白塗り（2026-09-02）

対象: `workdir/pages/sample-1.png`（実サンプル・テンプレート
`templates/chouhyo-v1.json` の back 面・table_id="detail"）。期待水平線位置
（面ローカル・重複排除・昇順）は `[93, 197, 301, 405, ...]` の15本
（2ブロック共通の行境界）。上から N 本を対応する y 位置で幅±3px 白塗りし、
`estimate_shift` を back 面全体に再実行した。

| N（白塗り本数） | 検出線本数(det_h) | matched/total | ok | reason |
|---:|---:|---:|:---:|---|
| 0（無変形） | 25 | 38/42 | True | (成功) |
| 1 | 24 | 36/42 | False | edge_mismatch |
| 2 | 22 | 34/42 | False | edge_mismatch |
| 3 | 21 | 32/42 | False | edge_mismatch |
| 4 | 19 | 30/42 | False | edge_mismatch |
| 5 | 17 | 28/42 | False | edge_mismatch |
| 6 | 15 | 26/42 | False | edge_mismatch |
| 7 | 14 | 24/42 | False | edge_mismatch |
| 8 | 13 | 22/42 | False | **few_lines** |
| 9 | 11 | 20/42 | False | few_lines |
| 10 | 10 | 18/42 | False | few_lines |
| 11 | 8 | 16/42 | False | few_lines |
| 12 | 7 | 14/42 | False | few_lines |
| 13 | 5 | 12/42 | False | few_lines |
| 14 | 3 | 10/42 | False | few_lines |
| 15（全消し） | 2 | 10/42 | False | few_lines |

### 実測から分かったこと（FR-F45 の二分閾値50%の妥当性の材料）

- **N=1（最上段の1本を消しただけ）で即座に `edge_mismatch` に転じた**。
  `align.py` の「テーブル外形（上端・下端の横罫線）の一致を要求する」
  アンカー検査（周期構造による1行ズレのエイリアシング対策）が、
  `few_lines` の50%閾値よりずっと敏感に効くため——上端行が消えると、
  たとえ他の行がすべて健在でも即座に不一致になる
- **`few_lines`（50%閾値）が実際に効き始めるのは N=8 から**
  （matched=22/42、back 面の y 軸だけで見た必要一致本数
  `need_y=max(2, ceil(30*0.5))=15` を `sy` が下回る境目）。それ以前
  （N=1〜7）は一貫して `edge_mismatch` が先に発火し、50%閾値のゲートには
  到達していない
- 結論: 「かすれ」の実態（上端から段階的に薄くなる劣化）に対しては、
  `few_lines` の50%閾値より先に `edge_mismatch`（非周期アンカー検査）が
  安全側に働くことを確認した。50%閾値の妥当性を検証したいなら、上端・
  下端を保ったまま**中間の行だけ**を間引く実験が必要（本実測の対象外・
  §10.2 の依頼範囲は「上から N 本」のため今回はこの条件のみ実測した）

## 4. 再現手順まとめ

```
# formC-1.png の生成
.venv\Scripts\python.exe testdata\formC\make_formC.py

# §2 の測定（align_page・estimate_shift を直接呼ぶ簡易スクリプトは
# 一時ファイルとして実行後に削除済み。再実行する場合は
# chouhyo_ocr.align.estimate_shift / align_page を直接呼び出せば再現できる）
```

実行日: 2026-09-02。実 API 送信は一度も行っていない（`load_template`・
`align_page`・`estimate_shift` の直接呼び出しのみ）。
