# S4-b: PyInstaller onedir 化した exe から google-cloud-vision(grpc) が動くか（付録 C11）
# 動的ロードされる protobuf 定義・TLS ルート証明書の同梱漏れを exe 実行で検出する。
# 実 API を1回だけ呼ぶ（小さいクロップ画像・TLS 経路まで含めた完全な検証のため）。
import os
import sys

ROOT = os.environ.get("CHOUHYO_ROOT", os.getcwd())
if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    raise SystemExit(
        "GOOGLE_APPLICATION_CREDENTIALS を設定してから実行する。\n"
        "資格情報のパスをコードへ埋め込まないこと（issue #1）。")

print("frozen:", getattr(sys, "frozen", False))
import certifi
print("certifi:", os.path.exists(certifi.where()))

from google.cloud import vision
client = vision.ImageAnnotatorClient()
with open(os.path.join(ROOT, "workdir", "family_date.png"), "rb") as f:
    img = vision.Image(content=f.read())
resp = client.document_text_detection(image=img, image_context=vision.ImageContext(language_hints=["ja"]))
if resp.error.message:
    print("API error:", resp.error.message)
    sys.exit(1)
n = sum(len(p2.words) for page in resp.full_text_annotation.pages
        for b in page.blocks for p2 in b.paragraphs)
print(f"API round-trip OK: words={n}")
