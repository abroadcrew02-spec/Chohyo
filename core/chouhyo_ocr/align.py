"""表裏分割・位置合わせ・再結合（設計 §6.2）。

- 展開画像をテンプレートの image サイズへ正規化 → source.rect で面を切り出す
- 除外領域は2パスで当てる: 粗（膨張あり・二値化閾値と角度推定の母集団から外す）／
  本（膨張なし・era 判定などに使う二値画像へ適用）
- 傾き推定は射影プロファイルの分散最大化（±3°・0.25°刻み・OpenCV 不使用）。
  同点は 0° を優先し、デジタル由来のきれいな画像で余計な回転をしない
- 回転後も面の寸法は source.rect の w×h のまま（§4.2 の座標系）
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

import numpy as np
from PIL import Image

from .template import Face, Template

COARSE_DILATE = 60  # 粗マスクの膨張量（設計 §6.2: ceil(D*sin(2°)) ≈ 53 → 60）


@dataclass(frozen=True)
class AlignedFace:
    face_id: str
    image: "Image.Image"      # 位置合わせ後の RGB（w×h = source.rect）
    binary: "np.ndarray"      # 位置合わせ後・本マスク適用済みの二値（True=インク）
    angle: float


# 位置合わせ方式の版。処理内容を変えたら上げる（#25: 旧方式で作った中間データを
# 新方式のコードが黙って再利用しないための印。geometry_hash が守るのは
# 「テンプレートの版」、これは「パイプラインの版」——役割が違うため別に持つ）
ALGO_VERSION = "1"


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
    return int(np.nanargmax(sigma))


def binarize_face(gray: "np.ndarray", face: Face) -> "np.ndarray":
    """粗マスク→Otsu→本マスクの二値化。run と remap で同一結果になるよう共通化。"""
    coarse = _exclusion_mask(face, COARSE_DILATE)
    th = _otsu(gray, coarse)
    binary = (gray < th) & ~coarse
    return binary & ~_exclusion_mask(face, 0)


def _deskew_angle(binary: "np.ndarray") -> float:
    """行射影の分散が最大になる角度（degree）。同点は 0° 優先。

    粗（0.75°刻み）→細（最良点の±0.5°を 0.25°刻み）の2段探索。
    全域 0.25°刻み（25回転）と同じ 0.25° 分解能を 13回転で得る。
    """
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


def align_page(page_img: "Image.Image", template: Template) -> tuple[list[AlignedFace], "Image.Image"]:
    """1ページ → 面ごとの位置合わせ結果と、送信用の再結合画像。"""
    W, H = template.image_size
    page = page_img.convert("RGB").resize((W, H))
    composite = Image.new("RGB", (W, H), "white")
    faces: list[AlignedFace] = []

    for face in template.faces:
        r = face.source_rect
        crop = page.crop((r.x, r.y, r.x + r.w, r.y + r.h))
        gray = np.asarray(crop.convert("L"))

        coarse = _exclusion_mask(face, COARSE_DILATE)
        th = _otsu(gray, coarse)
        angle = _deskew_angle((gray < th) & ~coarse)
        if angle != 0.0:
            crop = crop.rotate(angle, expand=False, fillcolor="white",
                               resample=Image.BICUBIC)
            gray = np.asarray(crop.convert("L"))

        binary_fine = binarize_face(gray, face)

        # 送信画像は除外領域（綴じ穴帯・黒塗り・印字ラベル等）を白塗りする
        # （要件 §5.2 のマスク。Vision へ除外領域の内容を送らない）
        masked = crop.copy()
        from PIL import ImageDraw
        drw = ImageDraw.Draw(masked)
        for ex in face.exclusions:
            drw.rectangle((ex.x, ex.y, ex.x + ex.w - 1, ex.y + ex.h - 1), fill="white")
        faces.append(AlignedFace(face.face_id, masked, binary_fine, angle))
        composite.paste(masked, (r.x, r.y))

    return faces, composite
