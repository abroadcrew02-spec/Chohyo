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
第3〜4段で cli.cmd_debug_images 側に実装済み（このモジュール自体の変更ではない）。

2026-09-03（issue #65-6）: 欄の由来（fallback／conflict）をトークン座標から
再計算するのをやめ、中間データの `cell.origin` を正として描く。あわせて
`origin=='conflict'`（主と参照先の食い違いによる強制〓）を枠の色で区別する。
このモジュールが独自に判断するのは symbol 1文字の**位置**だけで、欄の由来と
〓の理由は run/remap が確定させた事実を読むだけになった。
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
# 出力対象外（output: false）の欄の枠・ラベル（issue #66 段4・ぼたん S-9）。
# 読み取りは他の欄と同じく継続する（record 側は変えない）ため symbol の色分けは
# そのまま——ここで塗り分けるのは「欄の枠・ラベル」だけで、なぜこの欄が出力に
# 無いのかを画像から追えるようにする。既存の色（青=文字欄・紫=選択式・
# 水色=参照先）のどれとも被らない灰色を新設する
COL_EXCLUDED = (140, 140, 140)
# 主と参照先が食い違った欄（origin=='conflict'・U-03 判定表 #8・issue #65-6）。
# 値は主のまま保存されるが出力は信頼度に関わらず欄全体〓になる。〓の赤い塗りは
# 他の〓と同じなので、**なぜ〓なのか**を枠の色で区別する。既存の枠色（青=文字欄・
# 紫=選択式・水色=参照先・灰=出力対象外）と混ざらない赤紫
COL_CONFLICT = (205, 40, 110)


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


def _field_origins(store: Store, page_id: str) -> dict[str, str]:
    """欄ごとの由来（''／'fallback'／'conflict'）を中間データから読む（#65-6）。

    旧実装はトークン座標から `mapping.fallback_decision` を呼び直して由来を
    **再計算**していた。同じ結論に達するよう作ってあったが、由来を決める規則が
    mapping と debug_images の2箇所に並ぶ二重真実で、片方だけ直せば可視化が
    実際の出力と食い違う——しかも食い違ったことに気づく仕組みが無い。

    由来は run/remap の時点で `cell.origin` として確定・保存されている
    （store.cell_extras・設計 §10.2）ので、それをそのまま正とする。可視化は
    「保存された事実を描く」だけで、判断はしない。
    """
    return {fid: origin for fid, (_char_confs, origin) in
            store.cell_extras(page_id).items()}


def write_debug_images(store: Store, template: Template, aligned_dir: Path,
                       out_dir: Path, cfg: Config,
                       page_ids: list[str] | None = None) -> list[Path]:
    """ページごとの可視化 PNG を out_dir へ書き、パスの一覧を返す。

    位置合わせ画像（aligned/{page_id}_{face_id}.png）が無いページは飛ばす
    （展開失敗・位置合わせ失敗のページには重ねる土台が無い）。

    2026-08-31（レビュー差し戻し M-1）: 引数は unclear_threshold（スカラー）
    ではなく Config 本体を受け取る。以前はここで `Config(unclear_threshold=...)`
    を組み直しており、呼び出し元（cli.py）が実際に読み込んだ設定の
    unclear_char_level が常に既定 False へ落ちていた（cli.py 側で `cfg.
    unclear_threshold` のみを渡していたため）。cfg をそのまま貫通させることで
    今後 unclear_char_level を使う判定を足しても取りこぼさない。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = template.image_size
    face_origin = {f.face_id: (f.source_rect.x, f.source_rect.y)
                   for f in template.faces}
    font = _font(26)
    font_small = _font(20)
    unclear_threshold = cfg.unclear_threshold

    # symbol の行き先索引は面ごとに固定（テンプレートはページ間で共通）。
    # 全 symbol × 全セルの線形照合をやめ、assign() と同じ空間インデックスで
    # 引く（#60 M-6・56倍の性能差の解消）
    cells_by_face: dict[str, list[CellSpec]] = {}
    for c in template.cells:
        cells_by_face.setdefault(c.face_id, []).append(c)
    locators = {face_id: mapping.build_symbol_locator(cs, dpi=template.render_dpi)
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
        field_origins = _field_origins(store, pid)
        n_conflict = 0

        # --- 欄の枠と〓の塗り ---
        for c in template.cells:
            ox, oy = face_origin[c.face_id]
            # render_rows.build_row と同じ既定値（field_id が cell に無ければ
            # raw=""・conf=None・is_empty=False）。conf が None のケースを
            # 取りこぼさない（#60 M-1②）
            _text, conf, kind, _emp = cells.get(c.field_id, ("", None, c.kind, False))
            # 判定表 #8（U-03）: 主と参照先の食い違いは信頼度に関わらず欄全体〓
            # （render_rows.build_row と同じ順序で、閾値判定より先に見る・#65-6）
            conflict = field_origins.get(c.field_id) == "conflict" and kind != "choice"
            if _emp:
                unclear = False
            elif kind == "choice":
                unclear = not _text
            elif conflict:
                unclear = True
            else:
                unclear = unclear_reason(_text, conf, cfg) is not None
            if conflict and not _emp:
                n_conflict += 1
            # issue #66 段4: output: false の欄は枠・ラベルを専用色で塗り分ける
            # （読み取り自体は継続するので symbol の色分け・〓塗りは変えない）。
            # 食い違いの赤紫は出力される欄にだけ出す——対象外欄では「列に出ない」
            # ほうが先に効く情報で、2つの意味を1本の枠線に重ねない
            if not c.output:
                color = COL_EXCLUDED
            elif conflict and not _emp:
                color = COL_CONFLICT
            else:
                color = COL_CHOICE if c.kind == "choice" else COL_TEXT
            for box in _cell_targets(c, ox, oy):
                if unclear:
                    dr.rectangle(box, fill=FILL_UNCLEAR)
                dr.rectangle(box, outline=color, width=3)
            mark_color = COL_EXCLUDED if not c.output else COL_CHOICE
            for m in c.choice_marks:
                dr.rectangle((m.rect.x + ox, m.rect.y + oy,
                              m.rect.x + m.rect.w + ox, m.rect.y + m.rect.h + oy),
                             outline=mark_color, width=2)
            if c.fallback_rect is not None:
                r = c.fallback_rect
                fb_color = COL_EXCLUDED if not c.output else COL_FALLBACK
                dr.rectangle((r.x + ox, r.y + oy, r.x + r.w + ox, r.y + r.h + oy),
                             outline=fb_color, width=3)
                dr.text((r.x + ox + 4, r.y + oy + 2),
                        f"{c.field_id} の参照先", fill=fb_color, font=font_small)
            # 欄名は主枠の左上（読めるサイズ固定・原寸画像なので潰れない）。
            # 対象外欄は「なぜこの欄が出力に無いのか」を画像から追えるよう
            # ラベルへも印を付す（template.py の W 警告と同じ文言に揃える）
            label = c.field_id if c.output else f"{c.field_id}（出力対象外）"
            dr.text((c.rect.x + ox + 4, c.rect.y + oy + 2),
                    label, fill=color, font=font_small)

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
            (COL_CONFLICT, f"赤紫枠 = 主と参照先が食い違い → 欄全体が〓（{n_conflict}欄）"),
            (COL_EXCLUDED, "灰枠 = 出力対象外（output: false・読み取りは継続するが列には出ない）"),
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
