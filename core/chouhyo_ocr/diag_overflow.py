"""欄からの溢れ（主枠に部分記入・残りが右隣へ）の**頻度を数える**診断（issue #63）。

検知も修正もしない。#63 は「主枠に部分的に記入され、残りの文字が枠外や隣の欄へ
溢れる」型で、実測では郵便番号1='012345'（6桁しか主枠に入らず部分値のまま確定）
＋住所1='6'（溢れた1文字が隣の欄を汚染）という2欄同時の誤値が、status 正常・
fallback/carve_hole/unassigned のどのカウンタにも掛からずに出た。

検知には行の連続性推定が要り、正常に隣り合う記入と区別できないため誤検知が多い
——だから5巡目は実装を見送った（04_unclear_policy.md §15）。閾値を決める前に
**どれくらい起きているか**を実データで数えるのがこのモジュールの役目で、
閾値設計はここではしない。

- 材料は保存済み token（`run` が Vision の応答から作った symbol 座標）だけ。
  API 送信は発生せず、中間データも一切書き換えない（読み取り専用）
- 出すのは件数と page_id / field_id まで。記入値と座標は出さない（§8.1）
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .store import Store
from .template import Rect, Template

# 期待桁数を導出する欄名パターン（#63）。
#
# CellSpec には桁数の属性が無く、`normalize` も現状 "amount"（金額）だけで
# 郵便番号のルールを持たない（schema/template.schema.json）。そのため欄名から
# 導出する——ただしこれは**診断専用**の推定で、読み取り・出力の挙動は一切
# 変えない。要件 §5.2「抽出・正規化の方式は列名に依存させない」は転記の話で、
# ここは「どの欄なら期待桁数を言えるか」の目安にすぎない。
# 出力には rule を必ず添えて、何を根拠に期待桁数を決めたかを追えるようにする。
#
# 末尾の数字は**何人目か**（レコード番号）であって上3桁／下4桁の別ではない
# ——出荷テンプレートの `person_郵便番号1`(y=361) と `person_郵便番号2`(y=494)
# は同じ x に縦に並び、それぞれの右隣が `person_住所1` / `person_住所2` に
# なっている（2026-09-03 実測）。つまり1欄に 3+4 の7桁が入る。#63 の再現
# （主枠6字＋右へ1字）もこの形。1つの郵便番号を2枠に割るテンプレートが
# 将来出てきた場合、この規則は期待桁数を多く見積もって候補を増やす側に
# 外れる——だから rule を出力に添えて、数え方を後から検証できるようにする。
_DIGIT_RULES: tuple[tuple[re.Pattern[str], int, str], ...] = (
    (re.compile(r"郵便番号\s*\d*\s*$"), 7, "field_name:郵便番号(3+4=7桁)"),
)


@dataclass(frozen=True)
class TargetField:
    """期待桁数を言える欄。"""
    field_id: str
    face_id: str
    rect: Rect
    expected_digits: int
    rule: str


@dataclass(frozen=True)
class Candidate:
    """溢れの疑いが1件。値・座標は持たない（件数と識別子のみ）。"""
    page_id: str
    field_id: str
    main_symbols: int        # 主枠に入った symbol 数
    expected_digits: int     # 欄名から導いた期待桁数
    right_symbols: int       # 右隣の帯にあり主枠の外にある symbol 数
    right_outside_fields: int  # うち、どの欄の受け皿にも入っていない数


@dataclass(frozen=True)
class Report:
    pages_scanned: int
    fields_checked: int
    candidates: tuple[Candidate, ...]
    # 主枠が空（1文字も入っていない）の欄は候補にしない——それは #54(a) の
    # 「主が空なら参照先を採用する」既知の型で、右隣に住所が書かれているだけの
    # 正常なページを大量に拾ってしまう。ただし黙って捨てると母数が見えなく
    # なるので件数だけ残す
    empty_main_skipped: int


def _expected_digits(field_id: str) -> tuple[int, str] | None:
    for pattern, digits, rule in _DIGIT_RULES:
        if pattern.search(field_id):
            return digits, rule
    return None


def target_fields(template: Template) -> list[TargetField]:
    """期待桁数を導出できる欄を集める（choice・分割欄は対象外）。

    kind が choice の欄は丸印判定で桁数の概念が無い。subfields を持つ欄は
    1つの主枠から複数列へ分割されるため「主枠の文字数 < 期待桁数」が
    そのままでは成り立たない——どちらも数えない。
    """
    out: list[TargetField] = []
    for cell in template.cells:
        if cell.kind != "text" or cell.subfields:
            continue
        found = _expected_digits(cell.field_id)
        if found is None:
            continue
        digits, rule = found
        out.append(TargetField(cell.field_id, cell.face_id, cell.rect, digits, rule))
    return out


def _inside(rect: Rect, x: float, y: float) -> bool:
    """mapping.to_face_local と同じ半開区間の判定に揃える。"""
    return rect.x <= x < rect.x + rect.w and rect.y <= y < rect.y + rect.h


def _right_band(rect: Rect, band_scale: float) -> Rect:
    """主枠の右隣の帯。既定の幅は欄の高さ相当（band_scale=1.0）。

    高さを基準にするのは、1行の欄なら「文字がもう数文字はみ出す幅」が
    おおよそ行の高さに比例するため（dpi にも依存しない）。
    """
    return Rect(x=rect.x + rect.w, y=rect.y,
                w=max(1, int(round(rect.h * band_scale))), h=rect.h)


def scan_page(page_id: str, tokens: list[tuple], fields: list[TargetField],
              all_rects_by_face: dict[str, list[Rect]],
              band_scale: float = 1.0) -> tuple[list[Candidate], int]:
    """1ページ分を数える。tokens は store.tokens() の戻り（seq, face, text, conf, x, y）。

    戻り値は (候補, 主枠が空でスキップした欄数)。
    """
    by_face: dict[str, list[tuple[float, float]]] = {}
    for _seq, face_id, _text, _conf, x, y in tokens:
        by_face.setdefault(face_id, []).append((float(x), float(y)))

    candidates: list[Candidate] = []
    empty_main = 0
    for f in fields:
        points = by_face.get(f.face_id, ())
        main = sum(1 for x, y in points if _inside(f.rect, x, y))
        if main == 0:
            empty_main += 1
            continue
        if main >= f.expected_digits:
            continue
        band = _right_band(f.rect, band_scale)
        # 帯にあり、かつ自分の主枠の外（帯は主枠の右端から始まるので通常は
        # 重ならないが、band_scale や extra_rects の指定次第では重なりうる）
        in_band = [(x, y) for x, y in points
                   if _inside(band, x, y) and not _inside(f.rect, x, y)]
        if not in_band:
            continue
        others = all_rects_by_face.get(f.face_id, ())
        outside = sum(1 for x, y in in_band
                      if not any(_inside(r, x, y) for r in others))
        candidates.append(Candidate(
            page_id=page_id, field_id=f.field_id, main_symbols=main,
            expected_digits=f.expected_digits, right_symbols=len(in_band),
            right_outside_fields=outside))
    return candidates, empty_main


def _all_rects_by_face(template: Template) -> dict[str, list[Rect]]:
    """面ごとの「どこかの欄が受け皿にしている矩形」一覧（欄外判定の母集団）。

    参照先（fallback_rect）も含める——郵便番号の参照先は住所欄の上に置かれる
    のが実テンプレートの形（mapping.py の穴あけの説明どおり）で、これを欄
    扱いしないと「欄外に溢れた」件数が実態より多く出る。逆に言うと、実際の
    郵便番号欄では右隣の帯が自分の参照先とほぼ重なるため
    right_outside_fields は 0 に出やすい——溢れた文字が「どこにも属さない」
    のではなく「主が空のときだけ使う参照先」または隣の欄に入っている、
    というのがこの型の実態（#63 の実測では住所欄を汚染していた）。
    """
    out: dict[str, list[Rect]] = {}
    for cell in template.cells:
        rects = list(cell.all_rects())
        if cell.fallback_rect is not None:
            rects.append(cell.fallback_rect)
        out.setdefault(cell.face_id, []).extend(rects)
    return out


def scan(template: Template, store: Store, band_scale: float = 1.0) -> Report:
    """中間データ全体を走査する（読み取りのみ）。

    母集団は token を持つページ——token は応答を割り付けた時点で保存される
    ので、done だけでなく「読めたが様式不一致に倒れた」ページも含む。
    溢れの頻度を測るのに、出力に載ったかどうかで母集団を絞る理由は無い。
    """
    fields = target_fields(template)
    rects_by_face = _all_rects_by_face(template)
    candidates: list[Candidate] = []
    pages = 0
    empty_main = 0
    for page in store.pages():
        tokens = store.tokens(page["page_id"])
        if not tokens:
            continue
        pages += 1
        found, empty = scan_page(page["page_id"], tokens, fields,
                                 rects_by_face, band_scale)
        candidates.extend(found)
        empty_main += empty
    return Report(pages_scanned=pages, fields_checked=len(fields),
                  candidates=tuple(candidates), empty_main_skipped=empty_main)
