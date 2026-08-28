"""GUI スモークテスト（Playwright・bridge.ts のデモモック経由）。

vite dev サーバー（tauri dev が起動する http://localhost:1420）を素の
Chromium で開く。Tauri 外では bridge.ts がデモモックに切り替わるため、
コアなしで画面遷移とサマリ表示を検証できる。

検証するのは §8-11 の「GUI から実行を開始でき、進捗が更新され、完了後に
サマリが表示される」の UI 側配線。dev サーバーが立っていない環境では skip。
"""
import urllib.request

import pytest

URL = "http://localhost:1420"


def _dev_server_up() -> bool:
    try:
        urllib.request.urlopen(URL, timeout=2)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _dev_server_up(), reason="vite dev サーバー未起動")


@pytest.fixture(scope="module")
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1280, "height": 860})
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_timeout(500)
        yield pg
        assert errors == [], f"ページ内 JS エラー: {errors}"
        browser.close()


def test_initial_state_guides_user(page):
    # 手順1〜3 と説明が出ている・開始は無効
    assert page.get_by_text("読み取る帳票フォルダの選択").is_visible()
    start = page.get_by_role("button", name="読み取りを開始")
    assert start.is_disabled()
    assert page.get_by_text("未選択").is_visible()


def test_run_flow_shows_progress_and_summary(page):
    page.get_by_role("button", name="フォルダを選ぶ").click()
    start = page.get_by_role("button", name="読み取りを開始")
    assert start.is_enabled()
    start.click()

    # 処理中: 進捗と中断ボタン
    page.wait_for_selector("text=読み取り中", timeout=5000)
    assert page.get_by_role("button", name="中断", exact=True).is_visible()

    # 完了: バナー＋サマリ6項目（モックの値）
    page.wait_for_selector("text=読み取りが完了しました", timeout=15000)
    for label in ["処理枚数", "出力行数", "API送信回数",
                  "要確認セル数総計", "位置合わせ失敗", "行数超過件数"]:
        assert page.get_by_text(label, exact=True).is_visible(), label
    assert page.locator(".sumcard.warn .v", has_text="247").is_visible()  # 要確認セル数（モック値）
    assert page.get_by_text("次の作業（目視確認）").is_visible()
    # 完了後は手順が畳まれ、結果が主役になる（スクショレビューでの修正点）
    assert not page.get_by_text("未選択").is_visible()


def test_failure_list_uses_plain_language(page):
    # モックは3枚目を「位置合わせ失敗」で返す → 平易な言葉の一覧
    assert page.get_by_text("処理できなかったページ（1 件）").is_visible()
    assert page.get_by_text("位置合わせに失敗しました（行全体が〓です）").is_visible()


def test_settings_modal_six_items(page):
    page.locator("button[title=設定]").click()
    page.wait_for_selector("text=通常は変更不要です")
    for label in ["〓と判定する基準値", "丸印と判定する基準値", "上限ページ数",
                  "Excel の保存先", "中間データの保存先", "ログの保存先"]:
        assert page.get_by_text(label, exact=False).first.is_visible(), label
    page.get_by_role("button", name="保存", exact=True).click()
    page.wait_for_selector("text=保存しました")
    page.get_by_role("button", name="閉じる").click()


def test_editor_tab_admin_guardrails(page):
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")
    for tool in ["選択", "欄を追加", "除外範囲", "表を作成", "表裏の境界"]:
        assert page.get_by_role("button", name=tool).is_visible(), tool
    # 実行タブへ戻れる（未保存なしなので確認は出ない）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")
