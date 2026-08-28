# S3: サービスアカウント JSON（実物 2366 bytes）が Windows 資格情報ストアに入るか
# （実装レビュー M4・設計 §8.2。Windows の blob 上限は 2560B・keyring は UTF-16 で格納
#  するため実質 1280 文字が上限とみられる ※本スパイクで実測）
import os

import keyring

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 資格情報のパスは環境変数から取る（コードへ埋め込まない・issue #1）
CRED = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
if not CRED:
    raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS を設定してから実行する。")
SVC, USER = "chouhyo-ocr-spike", "gcp-sa"

print("backend:", keyring.get_keyring())

with open(CRED, encoding="utf-8") as f:
    payload = f.read()
print(f"payload: {len(payload)} chars / {len(payload.encode('utf-8'))} bytes (utf-8)")

# 1) 小さい値でバックエンド自体の動作を確認
keyring.set_password(SVC, "smoke", "hello")
assert keyring.get_password(SVC, "smoke") == "hello"
keyring.delete_password(SVC, "smoke")
print("smoke: OK（バックエンド動作）")

# 2) 実物 JSON 丸ごと
try:
    keyring.set_password(SVC, USER, payload)
    back = keyring.get_password(SVC, USER)
    print("set_password: 成功 / 往復一致:", back == payload)
    keyring.delete_password(SVC, USER)
except Exception as e:
    print(f"set_password: 失敗 → {type(e).__name__}: {e}")
    print("結論: JSON 丸ごとは入らない → DPAPI ファイル暗号化経路（設計 §8.2 の代替）へ")
