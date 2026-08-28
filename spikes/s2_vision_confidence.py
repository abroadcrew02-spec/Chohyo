# S2: Vision の word/symbol 単位 confidence 取得可否の実測（設計 02_design.md §6.3・リスク1）
# DOCUMENT_TEXT_DETECTION と TEXT_DETECTION の両方を同一画像へ当て、
# confidence の有無・分布を比較する。応答は workdir/ へ保存し再利用する（再課金回避）。
import json
import os
import statistics
import sys

from google.cloud import vision
from google.protobuf.json_format import MessageToDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG = os.path.join(ROOT, "workdir", "pages", "sample-1.png")
OUT = os.path.join(ROOT, "workdir", "s2")
os.makedirs(OUT, exist_ok=True)

if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    raise SystemExit(
        "GOOGLE_APPLICATION_CREDENTIALS を設定してから実行する。\n"
        "資格情報のパスをコードへ埋め込まないこと（issue #1）。")

client = vision.ImageAnnotatorClient()
with open(IMG, "rb") as f:
    content = f.read()
image = vision.Image(content=content)
ctx = vision.ImageContext(language_hints=["ja"])


def word_text(word):
    return "".join(s.text for s in word.symbols)


def stats(vals):
    if not vals:
        return "n=0"
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [min(vals)] * 3
    return (f"n={len(vals)} min={min(vals):.3f} p25={q[0]:.3f} "
            f"med={statistics.median(vals):.3f} p75={q[2]:.3f} max={max(vals):.3f} "
            f"zero率={sum(1 for v in vals if v == 0)/len(vals):.1%}")


results = {}
for name, fn in [("DOCUMENT_TEXT_DETECTION", client.document_text_detection),
                 ("TEXT_DETECTION", client.text_detection)]:
    resp = fn(image=image, image_context=ctx)
    d = MessageToDict(resp._pb)
    with open(os.path.join(OUT, f"resp_{name}.json"), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)

    print(f"\n===== {name} =====")
    if resp.error.message:
        print("API error:", resp.error.message)
        continue

    fta = resp.full_text_annotation
    words, syms = [], []
    for page in fta.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for w in para.words:
                    words.append(w)
                    syms.extend(w.symbols)
    wc = [w.confidence for w in words]
    sc = [s.confidence for s in syms]
    print(f"full_text_annotation: word {stats(wc)}")
    print(f"                      symbol {stats(sc)}")

    ta = resp.text_annotations
    ta_conf = [t.confidence for t in ta[1:]] if len(ta) > 1 else []
    print(f"text_annotations[1:]: {stats(ta_conf)}  (先頭要素=全文は除外)")

    if words:
        ranked = sorted(words, key=lambda w: w.confidence)
        print("confidence 低い順5語:", [(word_text(w), round(w.confidence, 3)) for w in ranked[:5]])
        print("confidence 高い順5語:", [(word_text(w), round(w.confidence, 3)) for w in ranked[-5:]])
    results[name] = {"words": len(words), "word_conf": stats(wc)}

print("\n保存先:", OUT)
