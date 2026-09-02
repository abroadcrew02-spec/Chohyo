"""様式判定（3値化）— 純関数モジュール（issue #71 (a')・08 §2.3）。

`align.estimate_shift` が既に計算している値を分類するだけで、新しい画像
処理は一切行わない（07 §9.1・FR-F01）。判定は既存の `need_y`／`need_x`
（`align.py` の下限）のまま——スコアは記録と (t) のテンプレート間比較専用で、
判定には使わない（08 §2.11 不変条件2）。

3値分類の根拠（2026-09-02 Orchestrator 判断・07 v1.2 へ反映済み）:
- `edge_mismatch` → 判定不能（実測で「不一致」から訂正。本物の紙の上端
  罫線を1本白塗りしただけで発火するため・08 §2.3.4 ★1）
- `few_lines` の二分は軸別（合算では det にテンプレート外の線が混ざり
  50%閾値が成立しないため・08 §2.3.4 ★2）
- `boundary`（および `few_lines` かつ検出十分でも探索境界に張り付いている
  場合）→ 判定不能（08 §2.3.4 ★3）
- `size`（`PageSizeMismatch`）→ 不一致。`estimate_shift` に到達しないため
  呼び出し元が `PageVerdict` を直接組む
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from .align import FaceDiag, ShiftEstimate

Verdict = Literal["match", "mismatch", "undecidable", "skipped", "unknown"]

# FR-F45 の暫定閾値（Q-F6 の較正対象）。値そのものは軸別の「検出線が
# 期待線の何%以上あれば十分とみなすか」——判定条件（08 §2.3.3）にのみ使う
FEW_LINES_DETECT_RATIO = 0.5


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
    score: float          # 面の最小値（最も悪い面がページを代表する・skipped と
                          # 未計測(-1.0) は除く。全面が対象外なら -1.0）
    faces: tuple[FaceVerdict, ...]
    # FR-F12・08 §2.5.3 のログ（format_verdict）用。verdict/reason/score と
    # 同じ「代表面」（fold が選ぶ最悪面）の detected/expected（08 §2.5.3 の
    # ログ行が単一の detected=/expected= を持つため、面ごとの内訳ではなく
    # 代表面の値を1組だけ持つ）
    detected: int = 0
    expected: int = 0


def score_of(est: ShiftEstimate) -> float:
    """FR-F01 のスコア（無次元・テンプレート間比較可能）。

    分母は**重複排除した期待線の位置数**（07 FR-F01）。`matched`（連結
    リスト基準）をこの分母で割らない——front は完全な紙で `matched=22`・
    重複排除分母16なので1.375になり定義域 [0,1] を外れる（08 §2.11
    不変条件3）。**判定には使わない**（同・不変条件2）。
    """
    denom = est.exp_h_uniq + est.exp_v_uniq
    return est.matched_uniq / denom if denom else -1.0


def classify(est: ShiftEstimate) -> tuple[Verdict, str]:
    """07 §4.1 の対応表を条件式にしたもの（08 §2.3.3）。"""
    if est.ok:
        return "match", ""
    if est.reason == "boundary":
        return "undecidable", "boundary"
    if est.reason == "edge_mismatch":
        return "undecidable", "edge"
    if est.reason == "ambiguous":
        return "mismatch", "ambiguous"
    if est.reason == "few_lines":
        # M-4（2026-09-02 マリン指摘）: 両軸とも期待線が0本（tables を持たない
        # 面）では「検出十分/乏しい」を判定する母数そのものが無い。sparse_h/
        # sparse_v は exp_*_uniq>0 の条件があるためどちらも False になり、
        # 下まで素通りして誤って「不一致」に倒れてしまう——判定材料が無い
        # 以上は安全側（判定不能）に倒す
        if est.exp_h_uniq + est.exp_v_uniq == 0:
            return "undecidable", "few_lines"
        sparse_h = (est.exp_h_uniq > 0
                    and est.det_h_count < est.exp_h_uniq * FEW_LINES_DETECT_RATIO)
        sparse_v = (est.exp_v_uniq > 0
                    and est.det_v_count < est.exp_v_uniq * FEW_LINES_DETECT_RATIO)
        if sparse_h or sparse_v:
            return "undecidable", "few_lines"      # 線が取れていない
        if est.at_boundary_h or est.at_boundary_v:
            return "undecidable", "boundary"       # 探索境界に張り付いている
        return "mismatch", "lines"                 # 線はあるのに期待位置と合わない
    return "undecidable", est.reason or "unknown"  # 未知の reason は安全側


_PRIORITY = {"mismatch": 0, "undecidable": 1, "unknown": 1, "match": 2}


def fold(faces: Sequence[FaceVerdict]) -> PageVerdict:
    """面 → ページの畳み込み（FR-F03）。優先順: mismatch > undecidable > match。

    `skipped`（評価に到達しなかった面）は判定に影響しない。全面が
    `skipped` はありえない（1面目で必ず評価が走る）が、防御的に判定不能で
    返す。

    M-4（2026-09-02 マリン指摘）: スコアが -1.0（未計測——`score_of` の
    分母0や `size` 判定など）の面は最小値の母集団から除く。混ぜると
    「算出できなかった」という事実上の欠測値が、実際に最も悪い面より
    小さい数値として勝ってしまう（欠測とワースト値の意味が違う）。
    """
    judged = [f for f in faces if f.verdict != "skipped"]
    if not judged:
        return PageVerdict("undecidable", "unknown", -1.0, tuple(faces))
    worst = min(judged, key=lambda f: _PRIORITY.get(f.verdict, 1))
    scored = [f.score for f in judged if f.score != -1.0]
    page_score = min(scored) if scored else -1.0
    return PageVerdict(worst.verdict, worst.reason, page_score, tuple(faces),
                       detected=worst.detected, expected=worst.expected)


def from_diag(diag: Sequence[FaceDiag]) -> PageVerdict:
    """`AlignError.diag`（面ごとの判定材料）から `PageVerdict` を組む（08 §2.4.2）。

    align_page が失敗した面で即 raise した後の後始末——評価済みの面
    （`estimate is not None`）は `classify`／`score_of` へ通し、未評価
    （`estimate is None`・skipped）の面はそのまま引き継ぐ。
    """
    face_verdicts = []
    for d in diag:
        if d.estimate is None:
            face_verdicts.append(FaceVerdict(d.face_idx, d.face_id, "skipped", "", -1.0, 0, 0))
            continue
        v, r = classify(d.estimate)
        face_verdicts.append(FaceVerdict(
            d.face_idx, d.face_id, v, r, score_of(d.estimate),
            d.estimate.det_h_count + d.estimate.det_v_count,
            d.estimate.exp_h_uniq + d.estimate.exp_v_uniq))
    return fold(face_verdicts)


def from_faces(faces: Sequence) -> PageVerdict:
    """`align_page` が返した `AlignedFace` 列から `PageVerdict` を組む（成功側・
    08 §2.4.2「成功側」・FR-F12・AC-F13）。

    align_page が例外を出さずに返った以上、全面 `est.ok=True`（match）の
    はずだが、`estimate` が `None`（#45 の再利用復元・`_restore_alignment`
    由来）の面が混ざりうる呼び出し元にも安全に使えるよう `skipped` 扱いに
    しておく。`faces` は `AlignedFace`（`.face_id`／`.estimate` を持つ）の列。
    """
    face_verdicts = []
    for idx, f in enumerate(faces):
        if f.estimate is None:
            face_verdicts.append(FaceVerdict(idx, f.face_id, "skipped", "", -1.0, 0, 0))
            continue
        v, r = classify(f.estimate)
        face_verdicts.append(FaceVerdict(
            idx, f.face_id, v, r, score_of(f.estimate),
            f.estimate.det_h_count + f.estimate.det_v_count,
            f.estimate.exp_h_uniq + f.estimate.exp_v_uniq))
    return fold(face_verdicts)


def check_page(page_img, template) -> PageVerdict:
    """ページ画像＋テンプレート1つ → 3値判定（FR-F13・AC-F15・08 §2.3.6）。

    align_page の**推定部分だけ**（`align._face_estimate`）を共有する。
    `estimate_shift` の後で打ち切り、本二値化・マスク・composite への
    貼り付けをしない——(t) の N テンプレートループ（NFR-F09: 合計 3.0 秒
    以内）で無駄な画像生成を避けるため。

    align_page と異なり**全面を評価する**（例外による早期打ち切りをしない）
    ——FaceVerdict は面ごとに個別の判定材料として必要で（§2.3.1 の型）、
    早期に止めると後続の面が「より悪い」verdict だった場合に見逃す
    （fold は最悪面を選ぶため、全面を見て初めて正しい）。
    """
    from PIL import Image

    from .align import _face_estimate, page_size_verdict

    W, H = template.image_size
    in_w, in_h = page_img.size
    reason = page_size_verdict((in_w, in_h), template)
    if reason is not None:
        return PageVerdict("mismatch", "size", -1.0, ())

    page = page_img.convert("RGB").resize((W, H))
    pad = max((max(f.shift_limits) for f in template.faces), default=0)
    padded = Image.new("RGB", (W + 2 * pad, H + 2 * pad), "white")
    padded.paste(page, (pad, pad))

    face_verdicts = []
    for idx, face in enumerate(template.faces):
        est, _big, _angle = _face_estimate(padded, face, template, pad)
        v, r = classify(est)
        face_verdicts.append(FaceVerdict(
            idx, face.face_id, v, r, score_of(est),
            est.det_h_count + est.det_v_count, est.exp_h_uniq + est.exp_v_uniq))
    return fold(face_verdicts)
