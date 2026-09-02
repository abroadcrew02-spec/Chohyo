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
    assert page.get_by_text("読み取る帳票の選択").is_visible()
    # issue #19: ファイル単位はドラッグ＆ドロップで受ける（案内文の存在を確認）
    assert page.get_by_text("ドラッグ＆ドロップでも選べます", exact=False).is_visible()
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
    # 数値入力は全選択→削除で打ち直せる（M-5: 旧実装は空文字を捨てて値が戻り、
    # 既存の数字を避けながら編集する必要があった）
    thr = page.locator("input[type=number]").first
    thr.fill("")
    assert thr.input_value() == "", "空にできない（打ち直しが阻害される）"
    thr.fill("0.9")
    page.get_by_role("button", name="保存", exact=True).click()
    page.wait_for_selector("text=保存しました")
    page.get_by_role("button", name="閉じる").click()


def test_editor_tab_admin_guardrails(page):
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")
    for tool in ["選択", "欄を追加", "除外範囲",
                 "くり返し行（家族・明細）", "表裏の境界"]:
        assert page.get_by_role("button", name=tool).is_visible(), tool
    # 実行タブへ戻れる（未保存なしなので確認は出ない）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_no_image_notice_blocks_canvas_edits(page):
    # issue 2026-09-02: 出荷テンプレートの自動読み込み（8/31 対応）はそのまま
    # 維持しつつ、画像を開く前は座標の枠を描かない・キャンバス操作を無効化する。
    # キャンバス内の文字はスクリーンリーダーに読めないため、同じ案内が
    # msg（DOM）側にも出ていることをまず確認する
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")
    assert page.get_by_text("帳票の画像か PDF を開くと", exact=False).is_visible()

    # デモモードは pick_image が null を返す（bridge.ts）ため画像は開けない。
    # 出力列タブは id 指定で開く（マリンレビュー LOW: 出力しない欄があると
    # バッジ「⊘N」が名前に付き get_by_role(name=...) が壊れうるため）
    page.locator("#edittab-output").click()
    before = page.locator(".panel-outrow").count()
    assert before > 0, "デモテンプレートの欄・表が出力列タブに出ていない"

    # ツールボタンは select 以外、画像が無い間 disabled になる
    # （マリンレビュー H-1・押しても無反応にしない）
    for tool in ["欄を追加", "除外範囲", "くり返し行（家族・明細）", "表裏の境界"]:
        assert page.get_by_role("button", name=tool).is_disabled(), tool
    assert page.get_by_role("button", name="選択", exact=True).is_enabled()

    # disabled のツールボタンはクリックしても tool が切り替わらない（React の
    # disabled 属性がそのまま効く前提だが、念のため実際にキャンバスへの
    # ドラッグでも欄が増えないことを二重に確認する——onDown 側のガードが
    # 効いているかの回帰検知を兼ねる
    canvas = page.locator("canvas.canvas")
    box = canvas.bounding_box()
    page.mouse.move(box["x"] + 50, box["y"] + 50)
    page.mouse.down()
    page.mouse.move(box["x"] + 200, box["y"] + 200, steps=5)
    page.mouse.up()

    after = page.locator(".panel-outrow").count()
    assert after == before, "画像を開く前にキャンバス操作で欄が増えてしまった"
    # コーディネータ指摘2: 上の件数不変チェックは tool="select"（既定）では
    # ヒットテストが空振りするだけの恒真判定になる（select 経路に欄を増やす
    # 手段が無いため、ガードが効いていなくても同じ結果になる）。ガード
    # （onDown 冒頭の canvasInteractionAllowed 分岐）が実際に発火したことは、
    # そこが出す案内文言（コーディネータ指摘4で template_id・件数入りの
    # noImageNotice に戻した）が status 領域に出ていることで確認する
    status = page.locator('[role="status"]')
    assert status.filter(has_text="テンプレート（demo）を読み込み済み・欄 1・表 1").is_visible(), \
        f"ガードの案内文言に template_id・件数が出ていない: {status.inner_text()}"

    # マリンレビュー M-4→コーディネータ指摘3: 特定の帯（想定パン/ズーム前提）
    # だけを見ると帯がずれた場合に偽 PASS するため、キャンバス全体を粗い格子
    # （40px刻み）で走査し、表裏分割線の色（#ff5577）が1件も無いことを見る。
    # getImageData は1回で全体を取得し、格子の判定は JS 側で行う
    hits = page.evaluate("""
        () => {
            const cv = document.querySelector('canvas.canvas');
            const ctx = cv.getContext('2d');
            const { data, width: w, height: h } = ctx.getImageData(0, 0, cv.width, cv.height);
            const step = 40;
            const found = [];
            for (let y = 0; y < h; y += step) {
                for (let x = 0; x < w; x += step) {
                    const i = (y * w + x) * 4;
                    const r = data[i], g = data[i + 1], b = data[i + 2];
                    if (Math.abs(r - 255) < 25 && Math.abs(g - 85) < 25 && Math.abs(b - 119) < 25) {
                        found.push([x, y, r, g, b]);
                    }
                }
            }
            return found;
        }
    """)
    assert hits == [], f"画像なしなのに表裏分割線の色(#ff5577)近傍が検出された: {hits}"

    # H-1 再現手順: 出力列タブから欄を選んでも「領域を追加」「別の欄と結合」
    # （どちらもキャンバス操作の待ち受けを立てるボタン）は画像が無い間は
    # disabled で押せない。押せてしまうと、後で画像を開いた直後の最初の
    # ドラッグが無言で追加領域／結合になってしまう（マリンレビュー H-1）
    page.get_by_role("button", name="person_氏名", exact=True).click()
    assert page.get_by_text("選択中の欄").is_visible()
    assert page.get_by_role("button", name="領域を追加", exact=True).is_disabled(), \
        "画像が無いのに「領域を追加」が押せてしまう（H-1）"
    assert page.get_by_role("button", name="別の欄と結合", exact=True).is_disabled(), \
        "画像が無いのに「別の欄と結合」が押せてしまう（H-1）"

    # コーディネータ指摘6: 画像が無い間もキャンバスクリックで選択解除だけは
    # 効く（ヒットテストはしないので、どこをクリックしても解除される）
    page.mouse.click(box["x"] + 300, box["y"] + 300)
    assert page.get_by_text("要素が選択されていません").is_visible(), \
        "画像なしのキャンバスクリックで選択解除できない（コーディネータ指摘6）"

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_delete_ignored_while_run_tab_active(page):
    # issue #69 Q-H3: Editor のグローバル keydown が実行タブ表示中も生きて
    # いたため、実行タブで Delete を押すと編集中の欄が消えていた。テンプレート
    # 編集タブは state を保ったままマウントし続ける（App.tsx）ので、選択状態を
    # 作ってからタブを切り替え、Delete を押しても欄が残ることを確認する
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    # 出力列タブから出荷（デモ）テンプレートの欄「person_氏名」を選択する
    page.locator("#edittab-output").click()
    page.get_by_role("button", name="person_氏名", exact=True).click()
    # 選択すると「選択中」タブへ自動で戻り、欄の詳細（名前入力）が出る
    assert page.get_by_text("選択中の欄").is_visible()
    name_input = page.get_by_label("欄の名前（出力の列名になります）")
    assert name_input.input_value() == "person_氏名"

    # 実行タブへ切り替える（Editor は active=false になり非表示になるが
    # アンマウントはされない）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")
    page.keyboard.press("Delete")

    # テンプレート編集タブへ戻り、欄が消えていない（選択も保持されたまま）
    # ことを確認する。旧実装ならここで「要素が選択されていません」に変わり、
    # 出力列一覧から person_氏名 が消えている
    page.locator(".tabs button", has_text="テンプレート編集").click()
    assert page.get_by_text("選択中の欄").is_visible()
    assert page.get_by_label("欄の名前（出力の列名になります）").input_value() == "person_氏名"
    page.locator("#edittab-output").click()
    assert page.get_by_role("button", name="person_氏名", exact=True).is_visible()

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")
