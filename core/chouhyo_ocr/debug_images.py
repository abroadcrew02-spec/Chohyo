"""開発者向けの読み取り可視化画像（2026-08-31・ユーザー要望）。

「記入されているはずなのに〓になる」を1枚の画像で切り分けるためのモード。
ページごとに位置合わせ済みの紙を再構成し、その上へ

- 欄の枠（青=文字欄・紫=選択式・水色=参照先・マークは細い紫）
- 読み取った1文字ずつの位置と文字（緑=採用 / 橙=信頼度不足=〓の原因 /
  赤=どの欄にも入らなかった）
- 〓になった欄のうっすら赤い塗り

を重ねて PNG に出力する。API は呼ばない（保存済みの読取データだけを使う）。

出力先は既定で workdir/debug/。**読取値がそのまま画像に描かれる**ため、
中間データと同じ扱い（クラウド同期外・共有しない・purge で消える場所）にする。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import logging_safe as log
from .store import Store
from .template import CellSpec, Template

# 画像上の色（線・点）。紙が白ベースなので濃色ではっきり分ける
COL_TEXT = (30, 120, 220)      # 文字欄の枠
COL_CHOICE = (150, 60, 200)    # 選択式の枠・マーク
COL_FALLBACK = (0, 160, 170)   # 参照先の枠
COL_OK = (0, 150, 60)          # 採用された文字
COL_LOW = (235, 140, 0)        # 信頼度不足（〓の原因）
COL_STRAY = (220, 30, 30)      # どの欄にも入らなかった文字
FILL_UNCLEAR = (255, 60, 60, 40)   # 〓欄のうっすら塗り


def _font(size: int):
    """日本語が描けるフォント。無ければ既定（図形だけでも役に立つ）。"""
    for name in ("meiryo.ttc", "msgothic.ttc", "YuGothM.ttc"):
        try:
            return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
        except OSError:
            continue
    return ImageFont.load_default()


def _cell_targets(cell: CellSpec, ox: int, oy: int):
    """欄の全領域（主＋追加）をページ座標で返す。"""
    for r in cell.all_rects():
        yield (r.x + ox, r.y + oy, r.x + r.w + ox, r.y + r.h + oy)


def write_debug_images(store: Store, template: Template, aligned_dir: Path,
                       out_dir: Path, unclear_threshold: float,
                       page_ids: list[str] | None = None) -> list[Path]:
    """ページごとの可視化 PNG を out_dir へ書き、パスの一覧を返す。

    位置合わせ画像（aligned/{page_id}_{face_id}.png）が無いページは飛ばす
    （展開失敗・位置合わせ失敗のページには重ねる土台が無い）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = template.image_size
    face_origin = {f.face_id: (f.source_rect.x, f.source_rect.y)
                   for f in template.faces}
    font = _font(26)
    font_small = _font(20)

    made: list[Path] = []
    for page in store.pages():
        pid = page["page_id"]
        if page_ids is not None and pid not in page_ids:
            continue
        tokens = store.tokens(pid)
        if not tokens:
            continue

        # --- 土台: 位置合わせ済みの面を貼り直した1枚 ---
        base = Image.new("RGB", (W, H), "white")
        missing = False
        for f in template.faces:
            p = aligned_dir / f"{pid}_{f.face_id}.png"
            if not p.exists():
                missing = True
                break
            with Image.open(p) as im:
                base.paste(im.convert("RGB"), (f.source_rect.x, f.source_rect.y))
        if missing:
            log.info("debug_image_skip_no_aligned", page_id=pid)
            continue

        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(overlay)
        cells = store.cells(pid)

        # --- 欄の枠と〓の塗り ---
        for c in template.cells:
            ox, oy = face_origin[c.face_id]
            raw = cells.get(c.field_id)
            # 〓の条件は render と同じ向きで近似する: 信頼度が閾値未満、
            # または選択式で値が決まらなかった欄
            unclear = False
            if raw is not None:
                _text, conf, kind, _emp = raw
                if conf is not None and conf < unclear_threshold:
                    unclear = True
                if kind == "choice" and not _text:
                    unclear = True
            color = COL_CHOICE if c.kind == "choice" else COL_TEXT
            for box in _cell_targets(c, ox, oy):
                if unclear:
                    dr.rectangle(box, fill=FILL_UNCLEAR)
                dr.rectangle(box, outline=color, width=3)
            for m in c.choice_marks:
                dr.rectangle((m.rect.x + ox, m.rect.y + oy,
                              m.rect.x + m.rect.w + ox, m.rect.y + m.rect.h + oy),
                             outline=COL_CHOICE, width=2)
            if c.fallback_rect is not None:
                r = c.fallback_rect
                dr.rectangle((r.x + ox, r.y + oy, r.x + r.w + ox, r.y + r.h + oy),
                             outline=COL_FALLBACK, width=3)
                dr.text((r.x + ox + 4, r.y + oy + 2),
                        f"{c.field_id} の参照先", fill=COL_FALLBACK, font=font_small)
            # 欄名は主枠の左上（読めるサイズ固定・原寸画像なので潰れない）
            dr.text((c.rect.x + ox + 4, c.rect.y + oy + 2),
                    c.field_id, fill=color, font=font_small)

        # --- 1文字ずつ: 割付先を再現して色分け ---
        n_ok = n_low = n_stray = 0
        for _seq, face, text, conf, x, y in tokens:
            ox, oy = face_origin.get(face, (0, 0))
            px, py = x + ox, y + oy
            assigned = False
            for c in template.cells:
                if c.face_id != face:
                    continue
                hit = any(r.x <= x < r.x + r.w and r.y <= y < r.y + r.h
                          for r in c.all_rects())
                if not hit and c.fallback_rect is not None:
                    r = c.fallback_rect
                    hit = r.x <= x < r.x + r.w and r.y <= y < r.y + r.h
                if hit:
                    assigned = True
                    break
            if not assigned:
                color = COL_STRAY
                n_stray += 1
            elif conf < unclear_threshold:
                color = COL_LOW
                n_low += 1
            else:
                color = COL_OK
                n_ok += 1
            dr.ellipse((px - 4, py - 4, px + 4, py + 4), fill=color)
            dr.text((px + 6, py - 26), f"{text}", fill=color, font=font)
            dr.text((px + 6, py + 2), f"{conf:.2f}", fill=color, font=font_small)

        # --- 凡例 ---
        legend = [
            (COL_OK, f"緑 = 読み取って採用（{n_ok}字）"),
            (COL_LOW, f"橙 = 信頼度が閾値 {unclear_threshold} 未満 → 〓の原因（{n_low}字）"),
            (COL_STRAY, f"赤 = どの欄にも入らなかった（{n_stray}字）"),
            (COL_TEXT, "青枠 = 文字欄 ／ 紫枠 = 選択式 ／ 水色枠 = 参照先"),
            ((90, 90, 90), "うっすら赤い欄 = 〓判定"),
        ]
        lx, ly = 20, H - 40 * (len(legend) + 1)
        dr.rectangle((lx - 10, ly - 10, lx + 900, ly + 40 * len(legend) + 5),
                     fill=(255, 255, 255, 235), outline=(90, 90, 90))
        for i, (col, label) in enumerate(legend):
            dr.ellipse((lx, ly + i * 40 + 6, lx + 18, ly + i * 40 + 24), fill=col)
            dr.text((lx + 28, ly + i * 40), label, fill=(20, 20, 20), font=font)

        merged = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        out = out_dir / f"{pid}_debug.png"
        merged.save(out, format="PNG", compress_level=3)
        made.append(out)
        log.info("debug_image_written", page_id=pid)
    return made
