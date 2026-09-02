"""表裏分割・位置合わせ・再結合（設計 §6.2）。

- 展開画像をテンプレートの image サイズへ正規化 → source.rect で面を切り出す
- 除外領域は2パスで当てる: 粗（膨張あり・二値化閾値と角度推定の母集団から外す）／
  本（膨張なし・era 判定などに使う二値画像へ適用）
- 傾き推定は射影プロファイルの分散最大化（±3°・OpenCV 不使用）。全域を等間隔で
  舐めるのではなく粗 0.75° → 細 0.25° の2段探索で、13回転で 0.25° 分解能を得る
  （estimate_angle の docstring が正）。同点は 0° を優先し、デジタル由来の
  きれいな画像で余計な回転をしない
- 傾きの後に平行移動を罫線射影で常時補正する（D-25）。採否は4条件で、
  満たさなければ値を出さず「位置合わせ失敗」へ倒す（正常顔の誤値を作らない）
- 回転後も面の寸法は source.rect の w×h のまま（§4.2 の座標系）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import logging_safe as log
from .template import BASE_DPI, Face, Template

COARSE_DILATE = 60  # 粗マスクの膨張量（設計 §6.2: ceil(D*sin(2°)) ≈ 53 → 60・300dpi=BASE_DPI 基準値）


@dataclass(frozen=True)
class AlignedFace:
    face_id: str
    image: "Image.Image"      # 位置合わせ後の RGB（w×h = source.rect）
    binary: "np.ndarray"      # 位置合わせ後・本マスク適用済みの二値（True=インク）
    angle: float
    dx: int = 0               # 平行移動の推定・補正量（D-25）
    dy: int = 0
    shift_matched: int = 0    # 期待罫線と一致した検出線の本数（診断・較正用）


# --- 平行移動推定の採否定数（D-25・※実物で較正・§4.6）---
SHIFT_MATCH_RATIO = 0.5   # 期待線のうち一致すべき割合の下限（無次元・dpi非依存）
SHIFT_GAP_MIN = 2         # 最良シフトと次点（4px 以上離れた位置）のスコア差の下限（無次元・dpi非依存）
SHIFT_RUNNER_DIST = 4     # 次点とみなす最小距離（px・300dpi=BASE_DPI 較正値）


@dataclass(frozen=True)
class ShiftEstimate:
    dx: int
    dy: int
    matched: int      # 両軸合計の一致本数
    total: int        # 両軸合計の期待線本数
    ok: bool
    reason: str       # NG のとき: few_lines / boundary / ambiguous


def _axis_shift(detected: list[int], expected: list[int], n: int,
                runner_dist: int = SHIFT_RUNNER_DIST) -> tuple[int, int, int, bool]:
    """1軸のシフト探索。(best_shift, best_score, runner_up, at_boundary)。

    スコア＝検出線が期待線＋シフトの ±1px にある本数。次点は最良から
    runner_dist 以上離れた位置での最大スコア（1行ズレ解との拮抗検出）。
    runner_dist は呼び出し元（estimate_shift）が dpi に応じてスケール済みの
    値を渡す（S-2・汎用化 A-3）。省略時は従来どおり SHIFT_RUNNER_DIST
    （300dpi 較正値）を使う。
    """
    det = set(detected)
    scores: dict[int, int] = {}
    for s in range(-n, n + 1):
        scores[s] = sum(1 for e in expected
                        if (e + s) in det or (e + s - 1) in det or (e + s + 1) in det)
    best_s = max(scores, key=lambda s: (scores[s], -abs(s)))  # 同点は小シフト優先
    runner = max((sc for s, sc in scores.items()
                  if abs(s - best_s) >= runner_dist), default=0)
    return best_s, scores[best_s], runner, abs(best_s) >= n and n > 0


def estimate_shift(binary: "np.ndarray", face: Face, dpi: int = BASE_DPI) -> ShiftEstimate:
    """罫線射影による面の平行移動推定（D-25）。

    テンプレートのテーブル定義（罫線の期待位置）をアンカーに、検出線との
    一致本数が最大になるシフトを探す。線が足りない・探索境界・次点と拮抗の
    いずれかなら ok=False——0 で素通しせず「位置合わせ失敗」へ倒すのは、
    ズレたまま正常顔で出すのが今回潰した故障そのものだから。

    dpi はテンプレートの render_dpi（汎用化 A-3）。projection.LINE_GAP・
    SHIFT_RUNNER_DIST は BASE_DPI=300 較正の px 定数なので、dpi/BASE_DPI の
    比でスケールしてから使う（S-1・S-2）。既定 dpi=BASE_DPI のときは
    従来と完全に同じ値になる。
    """
    from .projection import H_COVERAGE, LINE_GAP, V_COVERAGE, line_positions
    scale = dpi / BASE_DPI
    line_gap = max(0, round(LINE_GAP * scale))
    runner_dist = max(1, round(SHIFT_RUNNER_DIST * scale))
    n_x, n_y = face.shift_limits
    h, w = binary.shape
    det_h: set[int] = set()
    det_v: set[int] = set()
    exp_h: list[int] = []
    exp_v: list[int] = []
    for g in face.table_geoms:
        x0 = max(0, g.x_min - n_x)
        x1 = min(w, g.x_max + n_x)
        strip = binary[:, x0:x1]
        det_h.update(line_positions(strip.sum(axis=1), (x1 - x0) * H_COVERAGE, gap=line_gap))
        exp_h += list(g.h_lines)
        y0 = max(0, g.y_min - n_y)
        y1 = min(h, g.y_max + n_y)
        strip_v = binary[y0:y1, :]
        det_v.update(line_positions(strip_v.sum(axis=0), (y1 - y0) * V_COVERAGE, gap=line_gap))
        exp_v += list(g.v_lines)

    dy, sy, ry, by = _axis_shift(sorted(det_h), exp_h, n_y, runner_dist)
    dx, sx, rx, bx = _axis_shift(sorted(det_v), exp_v, n_x, runner_dist)
    matched, total = sx + sy, len(exp_h) + len(exp_v)

    import math
    need_y = max(2, math.ceil(len(exp_h) * SHIFT_MATCH_RATIO))
    need_x = max(2, math.ceil(len(exp_v) * SHIFT_MATCH_RATIO))
    if sy < need_y or sx < need_x:
        return ShiftEstimate(dx, dy, matched, total, False, "few_lines")
    if by or bx:
        return ShiftEstimate(dx, dy, matched, total, False, "boundary")
    if (sy - ry) < SHIFT_GAP_MIN or (sx - rx) < SHIFT_GAP_MIN:
        return ShiftEstimate(dx, dy, matched, total, False, "ambiguous")
    # テーブル外形（上端・下端の横罫線）の一致を要求する。表は行方向に周期
    # 構造なので、丸1行ピッチずれた入力は「シフト0・中間線ほぼ全一致」に見える
    # （エイリアシング・実測: dy=104 で正常顔の誤値）。端の線は周期の外にある
    # ため、1行ズレでは必ず不一致になる——非周期アンカー（D-25）。
    # 縦線には要求しない: 列間隔は非周期で gap 条件が効くうえ、実帳票の縦罫線は
    # かすれで端の検出が不安定（実測: 無変換の実サンプルで偽陽性になった）。
    # 照合は ±2px（ブロック間の較正差を許容。1行ズレ=ピッチ数十px の検出力に影響なし）
    def hit(det: set[int], v: int) -> bool:
        return any((v + d) in det for d in (-2, -1, 0, 1, 2))

    for g in face.table_geoms:
        if not (hit(det_h, g.h_lines[0] + dy) and hit(det_h, g.h_lines[-1] + dy)):
            return ShiftEstimate(dx, dy, matched, total, False, "edge_mismatch")
    return ShiftEstimate(dx, dy, matched, total, True, "")


# 位置合わせ方式の版。処理内容を変えたら上げる（#25: 旧方式で作った中間データを
# 新方式のコードが黙って再利用しないための印。geometry_hash が守るのは
# 「テンプレートの版」、これは「パイプラインの版」——役割が違うため別に持つ）
# "2": 平行移動補正（罫線射影・D-25）を追加
ALGO_VERSION = "2"


def template_hash(raw_template: dict) -> str:
    """テンプレート全体の正規化ハッシュ（#25: 非幾何の変更も再利用拒否の対象）。"""
    blob = json.dumps(raw_template, ensure_ascii=False,
                      sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def geometry_hash(raw_template: dict) -> str:
    """幾何セクション6要素の正規化ハッシュ（設計 §6.7）。"""
    geo = {
        "render_dpi": raw_template["render_dpi"],
        "image": raw_template["image"],
        "record": raw_template["record"],
        "faces": [
            {
                "face_id": f["face_id"],
                "source": f["source"],
                "exclusions": f.get("exclusions", []),
            }
            for f in raw_template["faces"]
        ],
    }
    blob = json.dumps(geo, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _exclusion_mask(face: Face, dilate: int) -> "np.ndarray":
    """面ローカルの除外マスク（True=除外）。"""
    w, h = face.source_rect.w, face.source_rect.h
    m = np.zeros((h, w), dtype=bool)
    for r in face.exclusions:
        x0 = max(0, r.x - dilate)
        y0 = max(0, r.y - dilate)
        x1 = min(w, r.x + r.w + dilate)
        y1 = min(h, r.y + r.h + dilate)
        m[y0:y1, x0:x1] = True
    return m


def _otsu(gray: "np.ndarray", exclude: "np.ndarray") -> int:
    vals = gray[~exclude]
    hist = np.bincount(vals.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    p = hist / total
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    mu_t = mu[-1]
    denom = omega * (1 - omega)
    denom[denom == 0] = np.nan
    sigma = (mu_t * omega - mu) ** 2 / denom
    if np.isnan(sigma).all():
        # 単一輝度（真っ白など）ではクラス分離が定義できない。閾値は何でも
        # 二値が空になるだけ——後段の平行移動推定が few_lines で失敗へ倒す
        return 128
    return int(np.nanargmax(sigma))


def binarize_face(gray: "np.ndarray", face: Face, dpi: int = BASE_DPI) -> "np.ndarray":
    """粗マスク→Otsu→本マスクの二値化。run と remap で同一結果になるよう共通化。

    dpi はテンプレートの render_dpi（汎用化 A-3）。COARSE_DILATE は
    BASE_DPI=300 較正の px 定数なので、Template.dpi_scale 相当の比で
    スケールしてから使う。既定 dpi=BASE_DPI のときは従来と完全に同じ値になる。
    """
    scale = dpi / BASE_DPI
    dilate = max(0, round(COARSE_DILATE * scale))
    coarse = _exclusion_mask(face, dilate)
    th = _otsu(gray, coarse)
    binary = (gray < th) & ~coarse
    return binary & ~_exclusion_mask(face, 0)


def _deskew_angle(binary: "np.ndarray") -> float:
    """行射影の分散が最大になる角度（degree）。同点は 0° 優先。

    粗（0.75°刻み）→細（最良点の±0.5°を 0.25°刻み）の2段探索。
    全域 0.25°刻み（25回転）と同じ 0.25° 分解能を 13回転で得る。
    """
    if not binary.any():
        return 0.0  # インクゼロ（真っ白）。回転の議論自体が無意味——後段の
        # 平行移動推定が few_lines で位置合わせ失敗へ倒す
    small = binary[::6, ::6]
    im = Image.fromarray(small.astype(np.uint8) * 255)

    def var_at(a: float) -> float:
        rot = np.asarray(im.rotate(a, expand=False, fillcolor=0)) > 0
        return float(np.var(rot.sum(axis=1)))

    best_angle, best_var = 0.0, var_at(0.0)
    for a in np.arange(-3.0, 3.0 + 1e-9, 0.75):
        if abs(a) < 1e-9:
            continue
        v = var_at(float(a))
        if v > best_var + 1e-6:
            best_var, best_angle = v, float(a)
    center = best_angle
    for a in np.arange(center - 0.5, center + 0.5 + 1e-9, 0.25):
        v = var_at(float(a))
        if v > best_var + 1e-6 or (abs(v - best_var) <= 1e-6 and abs(a) < abs(best_angle)):
            best_var, best_angle = v, float(a)
    return best_angle


class AlignError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PageSizeMismatch(AlignError):
    """入力ページの寸法がテンプレートと噛み合わない（Q-H1）。

    AlignError のサブクラス——既存の except AlignError（cli.cmd_expand_page 等）
    はそのまま拾う。pipeline.py は「様式不一致」へ倒すため、この型専用の
    except を AlignError より前に置く（先に拾わないと基底クラスの分岐に
    落ちて「位置合わせ失敗」になってしまう）。
    """


def page_size_verdict(size: tuple[int, int], template: Template) -> str | None:
    """入力ページの寸法をテンプレート寸法と照合する純関数（Q-H1）。

    完全一致は要求しない——dpi の丸め・PDF 展開の 1px 単位の差を吸収するため
    （D-25 の端一致要求は縦の平行移動しか受けず、横向きの歪み・用紙違いは
    無検証で通っていた）。見るのは「等比か」（アスペクト比の相対差 1%以内）
    のみで、等倍でも等比拡大でも通す。用紙違い（実測差 約8%）・向き違い
    （約41%）・切れ（約3.4%）といった粗い事故を確実に止めつつ、dpi 差由来の
    軽微な差は通す帯として 1% を選んだ（着手前実測: 縦だけ 5% 伸ばした入力が
    無検査のまま resize され、位置合わせが angle=0/dx=0/dy=0 で「成功」した
    ——歪んだ内容がそのまま送信される）。

    戻り値: 一致なら None、不一致なら reason 文字列（"aspect_mismatch"）。
    ログ・例外はここでは出さない（呼び出し元の責務）。
    """
    w, h = size
    tw, th = template.image_size
    if w <= 0 or h <= 0 or tw <= 0 or th <= 0:
        return "aspect_mismatch"
    ratio_in = w / h
    ratio_tpl = tw / th
    if abs(ratio_in - ratio_tpl) / ratio_tpl > 0.01:
        return "aspect_mismatch"
    return None


def align_page(page_img: "Image.Image", template: Template,
                mask: bool = True) -> tuple[list[AlignedFace], "Image.Image"]:
    """1ページ → 面ごとの位置合わせ結果と、送信用の再結合画像。

    手順（D-25）: page_size_verdict でテンプレート寸法との噛み合わせを検査
    （Q-H1・不一致は PageSizeMismatch）→ 面を探索余白つきで切り出し →
    傾き推定・回転 → 罫線射影で平行移動を推定（常に補正・信用できなければ
    位置合わせ失敗）→ 補正済みの窓を取り出し → 本マスク。面が1つでも失敗
    ならページ全体を失敗にする（半分だけ正しい行を正常顔で出さない）。

    mask: 除外領域を白塗りするか（既定 True）。要件 §5.2 の送信マスクの
    契約はこの既定に依存しており、run（pipeline.py）は常に既定のまま
    呼び出す＝この引数を渡さない。False は expand-page の --no-mask
    専用（#59 H-8）で、編集画面が除外枠の位置調整・取捨を判断するための
    下地表示にのみ使う。run からは到達できない。
    """
    W, H = template.image_size
    in_w, in_h = page_img.size
    # 寸法は記入値ではない（§8.1 に抵触しない）ので毎ページ記録する。診断用途
    # ——sx/sy=1.0 が「入力とテンプレートの寸法が一致」の目安（Q-H1）
    log.info("page_scale", sx=round(in_w / W, 4) if W else 0.0,
             sy=round(in_h / H, 4) if H else 0.0)
    reason = page_size_verdict((in_w, in_h), template)
    if reason is not None:
        raise PageSizeMismatch(reason)
    page = page_img.convert("RGB").resize((W, H))
    composite = Image.new("RGB", (W, H), "white")
    faces: list[AlignedFace] = []

    # COARSE_DILATE は BASE_DPI=300 較正の px 定数（汎用化 A-3）。pad は
    # face.shift_limits（行ピッチ・列間隔）由来で、既にこのテンプレートの
    # render_dpi 座標系の値なのでスケール不要——加算する COARSE_DILATE 側だけ
    # dpi_scale で合わせる
    dilate = max(0, round(COARSE_DILATE * template.dpi_scale))

    # 探索余白つきキャンバスはページ単位で1回だけ作る（面ごとに作り直すと
    # 30MB 規模の確保と貼り付けが面の数だけ走り、実測で align が数倍遅くなる）
    pad = max((max(f.shift_limits) for f in template.faces), default=0)
    padded = Image.new("RGB", (W + 2 * pad, H + 2 * pad), "white")
    padded.paste(page, (pad, pad))

    for face in template.faces:
        r = face.source_rect
        # ページ外は白。回転を2回かけないため、シフトはこの padded crop の
        # 内側の窓取りで行う
        big = padded.crop((r.x, r.y, r.x + r.w + 2 * pad, r.y + r.h + 2 * pad))

        # 傾き推定は従来どおり中央窓（w×h）で行う
        gray = np.asarray(big.crop((pad, pad, pad + r.w, pad + r.h)).convert("L"))
        coarse = _exclusion_mask(face, dilate + pad)  # ズレの分も覆う（D-25）
        th = _otsu(gray, coarse)
        angle = _deskew_angle((gray < th) & ~coarse)
        if angle != 0.0:
            big = big.rotate(angle, expand=False, fillcolor="white",
                             resample=Image.BICUBIC)
            gray = np.asarray(big.crop((pad, pad, pad + r.w, pad + r.h)).convert("L"))
            th = _otsu(gray, coarse)

        # 平行移動の推定（回転補正後・粗マスク二値。ズレた状態では除外矩形も
        # 同じだけズレているため、本マスクではなく膨張済みの粗マスクを使う）
        est = estimate_shift((gray < th) & ~coarse, face, dpi=template.render_dpi)
        if not est.ok:
            raise AlignError(f"TRANSLATION_UNRELIABLE_{est.reason}")
        crop = big.crop((pad + est.dx, pad + est.dy,
                         pad + est.dx + r.w, pad + est.dy + r.h))
        gray = np.asarray(crop.convert("L"))

        binary_fine = binarize_face(gray, face, dpi=template.render_dpi)

        # 送信画像は除外領域（綴じ穴帯・黒塗り・印字ラベル等）を白塗りする
        # （要件 §5.2 のマスク。Vision へ除外領域の内容を送らない）。
        # mask=False（expand-page --no-mask 専用）のときは白塗りを適用しない
        # ——送信には使われない下地専用の分岐で、run はここへ来ない
        masked = crop.copy()
        if mask:
            from PIL import ImageDraw
            drw = ImageDraw.Draw(masked)
            for ex in face.exclusions:
                drw.rectangle((ex.x, ex.y, ex.x + ex.w - 1, ex.y + ex.h - 1), fill="white")
        faces.append(AlignedFace(face.face_id, masked, binary_fine, angle,
                                 dx=est.dx, dy=est.dy, shift_matched=est.matched))
        composite.paste(masked, (r.x, r.y))

    return faces, composite
