"""開発者向けの読み取り可視化画像（2026-08-31・ユーザー要望）。

「記入されているはずなのに〓になる」を1枚の画像で切り分けるためのモード。
ページごとに位置合わせ済みの紙を再構成し、その上へ

- 欄の枠（青=文字欄・紫=選択式・水色=参照先・マークは細い紫）
- 読み取った1文字ずつの位置と文字（緑=採用 / 橙=信頼度不足=〓の原因 /
  赤=どの欄にも入らなかった / 参照先の採用・破棄・欄の穴は専用色）
- 〓になった欄のうっすら赤い塗り

を重ねて PNG に出力する。API は呼ばない（保存済みの読取データだけを使う）。

出力先は既定で workdir/debug/。**読取値がそのまま画像に描かれる**ため、
中間データと同じ扱い（クラウド同期外・共有しない・purge で消える場所）にする。

2026-08-31（5巡目 第2段・#60 M-1 の②③）: 〓判定を render_rows.unclear_reason と
共有し（conf が None のケースの取りこぼしを解消）、1文字の割付先も
mapping.locate_symbol（assign() と同じ索引）で調べ直すことで、参照先で
「採用された文字」と「破棄された文字」を区別して塗り分ける（従来は破棄分も
緑=採用扱いになっていた）。M-1 の①（check_reusable）④（count:0固定）は
第2段の範囲外（#60 で扱う）。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import logging_safe as log
from . import mapping
from .config import Config
from .render_rows import unclear_reason
from .store import Store
from .template import CellSpec, Template

# 画像上の色（線・点）。紙が白ベースなので濃色ではっきり分ける
COL_TEXT = (30, 120, 220)      # 文字欄の枠
COL_CHOICE = (150, 60, 200)    # 選択式の枠・マーク
COL_FALLBACK = (0, 160, 170)   # 参照先の枠
COL_OK = (0, 150, 60)          # 採用された文字（欄の領域）
COL_LOW = (235, 140, 0)        # 信頼度不足（〓の原因）
COL_STRAY = (220, 30, 30)      # どの欄にも入らなかった文字
COL_FALLBACK_OK = (0, 180, 120)     # 参照先で採用された文字（従来の緑と区別・U-15）
COL_FALLBACK_DISCARD = (200, 60, 200)  # 参照先で破棄された文字（U-15・判定表A）
COL_HOLE = (120, 70, 20)            # 欄の穴に落ちた文字（U-15・判定表#7）
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


def _field_origins(locators: dict, tokens) -> dict[str, str]:
    """このページのトークンから、フィールドごとの由来（''/'fallback'/'conflict'）を
    再現する（mapping.assign() と同じ fallback_decision を使う・#60 M-1③）。
    """
    counts: dict[str, list[int]] = {}
    for _seq, face, _text, _conf, x, y in tokens:
        loc = locators.get(face)
        if loc is None:
            continue
        fid, tag = mapping.locate_symbol(loc, x, y)
        if fid is None:
            continue
        c = counts.setdefault(fid, [0, 0])
        if tag == "region":
            c[0] += 1
        elif tag == "fallback":
            c[1] += 1
    return {fid: mapping.fallback_decision(n_main, n_fb)
            for fid, (n_main, n_fb) in counts.items()}


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
    cfg = Config(unclear_threshold=unclear_threshold)

    # symbol の行き先索引は面ごとに固定（テンプレートはページ間で共通）。
    # 全 symbol × 全セルの線形照合をやめ、assign() と同じ空間インデックスで
    # 引く（#60 M-6・56倍の性能差の解消）
    cells_by_face: dict[str, list[CellSpec]] = {}
    for c in template.cells:
        cells_by_face.setdefault(c.face_id, []).append(c)
    locators = {face_id: mapping.build_symbol_locator(cs)
               for face_id, cs in cells_by_face.items()}

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
        field_origins = _field_origins(locators, tokens)

        # --- 欄の枠と〓の塗り ---
        for c in template.cells:
            ox, oy = face_origin[c.face_id]
            # render_rows.build_row と同じ既定値（field_id が cell に無ければ
            # raw=""・conf=None・is_empty=False）。conf が None のケースを
            # 取りこぼさない（#60 M-1②）
            _text, conf, kind, _emp = cells.get(c.field_id, ("", None, c.kind, False))
            if _emp:
                unclear = False
            elif kind == "choice":
                unclear = not _text
            else:
                unclear = unclear_reason(_text, conf, cfg) is not None
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
        n_ok = n_low = n_stray = n_fb_ok = n_fb_discard = n_hole = 0
        for _seq, face, text, conf, x, y in tokens:
            ox, oy = face_origin.get(face, (0, 0))
            px, py = x + ox, y + oy
            loc = locators.get(face)
            fid, tag = mapping.locate_symbol(loc, x, y) if loc is not None else (None, None)
            if fid is None:
                color = COL_STRAY
                n_stray += 1
            elif tag == "hole":
                color = COL_HOLE
                n_hole += 1
            elif tag == "fallback" and field_origins.get(fid) != "fallback":
                # 参照先の枠に入ったが、主が空でない（採用されなかった）＝破棄
                # （判定表 A）。従来は無条件で緑=採用に塗っていた（#60 M-1③）
                color = COL_FALLBACK_DISCARD
                n_fb_discard += 1
            elif conf < unclear_threshold:
                color = COL_LOW
                n_low += 1
            elif tag == "fallback":
                color = COL_FALLBACK_OK
                n_fb_ok += 1
            else:
                color = COL_OK
                n_ok += 1
            dr.ellipse((px - 4, py - 4, px + 4, py + 4), fill=color)
            dr.text((px + 6, py - 26), f"{text}", fill=color, font=font)
            dr.text((px + 6, py + 2), f"{conf:.2f}", fill=color, font=font_small)

        # --- 凡例 ---
        legend = [
            (COL_OK, f"緑 = 読み取って採用（{n_ok}字）"),
            (COL_FALLBACK_OK, f"緑（参照先） = 参照先が採用された（{n_fb_ok}字）"),
            (COL_LOW, f"橙 = 信頼度が閾値 {unclear_threshold} 未満 → 〓の原因（{n_low}字）"),
            (COL_FALLBACK_DISCARD, f"マゼンタ = 参照先に書かれたが破棄された（{n_fb_discard}字）"),
            (COL_HOLE, f"茶 = 欄の穴に落ちた（その欄が〓になる・{n_hole}字）"),
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
