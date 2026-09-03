"""GUI スモークテスト（Playwright・bridge.ts のデモモック経由）。

vite dev サーバー（tauri dev が起動する http://localhost:1420）を素の
Chromium で開く。Tauri 外では bridge.ts がデモモックに切り替わるため、
コアなしで画面遷移とサマリ表示を検証できる。

検証するのは §8-11 の「GUI から実行を開始でき、進捗が更新され、完了後に
サマリが表示される」の UI 側配線。dev サーバーが立っていない環境では skip。
"""
import os
import urllib.request

import pytest

try:
    from playwright.sync_api import expect
except ImportError:  # playwright 未導入でも収集は通す（下の skipif が効く）
    expect = None

URL = "http://localhost:1420"

# 待ち時間の上限（issue #79）。
#
# 素の Playwright は操作 30 秒・expect 5 秒とばらばらで、状態変化を待つ側
# （expect）だけが先に切れる。並列で cargo／PyInstaller／pytest が走って
# いる最中はブラウザのメインスレッドが数秒単位で止まるため、ここを 1 か所に
# 集約し、混雑した環境では環境変数で伸ばせるようにする。
#
# 上限を大きく取っても正常時の実行時間は増えない（expect は状態が整った
# 時点で先へ進む）。代わりに、本当に壊れているときの失敗確認が遅くなる。
SMOKE_TIMEOUT_MS = int(os.environ.get("CHOUHYO_SMOKE_TIMEOUT_MS", "45000"))


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
        pg.set_default_timeout(SMOKE_TIMEOUT_MS)
        pg.set_default_navigation_timeout(SMOKE_TIMEOUT_MS)
        expect.set_options(timeout=SMOKE_TIMEOUT_MS)
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL, wait_until="networkidle")
        # 固定 sleep をやめ、初期画面が描けたことで待つ（issue #79）
        expect(pg.get_by_text("読み取る帳票の選択")).to_be_visible()
        yield pg
        assert errors == [], f"ページ内 JS エラー: {errors}"
        browser.close()


def test_initial_state_guides_user(page):
    # 手順1〜3 と説明が出ている・開始は無効
    expect(page.get_by_text("読み取る帳票の選択")).to_be_visible()
    # issue #19: ファイル単位はドラッグ＆ドロップで受ける（案内文の存在を確認）
    expect(page.get_by_text("ドラッグ＆ドロップでも選べます", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="読み取りを開始")).to_be_disabled()
    expect(page.get_by_text("未選択")).to_be_visible()


def test_run_flow_shows_progress_and_summary(page):
    page.get_by_role("button", name="フォルダを選ぶ").click()
    start = page.get_by_role("button", name="読み取りを開始")
    expect(start).to_be_enabled()
    start.click()

    # 処理中: 進捗と中断ボタン。個別の timeout は置かず、状態が変わるまで
    # 待つ（issue #79）——短い固定 timeout は並列負荷でだけ切れる
    expect(page.get_by_text("読み取り中", exact=False).first).to_be_visible()
    expect(page.get_by_role("button", name="中断", exact=True)).to_be_visible()

    # 完了: バナー＋サマリ6項目（モックの値）
    expect(page.get_by_text("読み取りが完了しました", exact=False).first).to_be_visible()
    for label in ["処理枚数", "出力行数", "API送信回数",
                  "要確認セル数総計", "位置合わせ失敗", "行数超過件数"]:
        expect(page.get_by_text(label, exact=True)).to_be_visible()
    # 要確認セル数（モック値）
    expect(page.locator(".sumcard.warn .v", has_text="247")).to_be_visible()
    expect(page.get_by_text("次の作業（目視確認）")).to_be_visible()
    # 完了後は手順が畳まれ、結果が主役になる（スクショレビューでの修正点）
    expect(page.get_by_text("未選択")).not_to_be_visible()


def test_failure_list_uses_plain_language(page):
    # モックは3枚目を「位置合わせ失敗」で返す → 平易な言葉の一覧
    expect(page.get_by_text("処理できなかったページ（1 件）")).to_be_visible()
    expect(page.get_by_text("位置合わせに失敗しました（行全体が〓です）")).to_be_visible()


def test_settings_modal_six_items(page):
    page.locator("button[title=設定]").click()
    page.wait_for_selector("text=通常は変更不要です")
    for label in ["〓と判定する基準値", "丸印と判定する基準値", "上限ページ数",
                  "Excel の保存先", "中間データの保存先", "ログの保存先"]:
        expect(page.get_by_text(label, exact=False).first).to_be_visible()
    # 数値入力は全選択→削除で打ち直せる（M-5: 旧実装は空文字を捨てて値が戻り、
    # 既存の数字を避けながら編集する必要があった）
    thr = page.locator("input[type=number]").first
    thr.fill("")
    expect(thr).to_have_value("")   # 空にできない＝打ち直しが阻害される
    thr.fill("0.9")
    page.get_by_role("button", name="保存", exact=True).click()
    page.wait_for_selector("text=保存しました")
    page.get_by_role("button", name="閉じる").click()


def test_editor_tab_admin_guardrails(page):
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")
    for tool in ["選択", "欄を追加", "除外範囲",
                 "くり返し行（家族・明細）", "表裏の境界"]:
        expect(page.get_by_role("button", name=tool)).to_be_visible()
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
    expect(page.get_by_text("帳票の画像か PDF を開くと", exact=False)).to_be_visible()

    # このテストは「帳票を開く」を一度もクリックしないため、bridge.ts の
    # pick_image が何を返すか（issue #71 (a') 以降は固定の疑似画像パス）に
    # 依存しない。画像を開いた状態の検証は
    # test_editor_format_mismatch_hides_frames_until_override 側で行う。
    # 出力列タブは id 指定で開く（マリンレビュー LOW: 出力しない欄があると
    # バッジ「⊘N」が名前に付き get_by_role(name=...) が壊れうるため）
    page.locator("#edittab-output").click()
    before = page.locator("#edittabpanel .panel-outrow").count()
    assert before > 0, "デモテンプレートの欄・表が出力列タブに出ていない"

    # ツールボタンは select 以外、画像が無い間 disabled になる
    # （マリンレビュー H-1・押しても無反応にしない）
    for tool in ["欄を追加", "除外範囲", "くり返し行（家族・明細）", "表裏の境界"]:
        expect(page.get_by_role("button", name=tool)).to_be_disabled()
    expect(page.get_by_role("button", name="選択", exact=True)).to_be_enabled()

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

    after = page.locator("#edittabpanel .panel-outrow").count()
    assert after == before, "画像を開く前にキャンバス操作で欄が増えてしまった"
    # コーディネータ指摘2: 上の件数不変チェックは tool="select"（既定）では
    # ヒットテストが空振りするだけの恒真判定になる（select 経路に欄を増やす
    # 手段が無いため、ガードが効いていなくても同じ結果になる）。ガード
    # （onDown 冒頭の canvasInteractionAllowed 分岐）が実際に発火したことは、
    # そこが出す案内文言（コーディネータ指摘4で template_id・件数入りの
    # noImageNotice に戻した）が status 領域に出ていることで確認する
    status = page.locator('[role="status"]')
    # ガードの案内文言に template_id・件数が出ている
    expect(status.filter(
        has_text="テンプレート（demo）を読み込み済み・欄 1・表 1")).to_be_visible()

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
    expect(page.get_by_text("選択中の欄")).to_be_visible()
    # 画像が無いのに押せてしまうと、後で画像を開いた直後の最初のドラッグが
    # 無言で追加領域／結合になる（H-1）
    expect(page.get_by_role("button", name="領域を追加", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="別の欄と結合", exact=True)).to_be_disabled()

    # コーディネータ指摘6: 画像が無い間もキャンバスクリックで選択解除だけは
    # 効く（ヒットテストはしないので、どこをクリックしても解除される）
    page.mouse.click(box["x"] + 300, box["y"] + 300)
    # 画像なしのキャンバスクリックでも選択解除は効く（コーディネータ指摘6）
    expect(page.get_by_text("要素が選択されていません")).to_be_visible()

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_format_mismatch_hides_frames_until_override(page):
    # issue #71 (a'): expand-page が返す面ごとの様式判定（verdict）が
    # "mismatch" の面は、「判定を無視して枠を表示する」（FR-F05）で
    # 上書きするまで枠を描かず・掴めない（FR-F04・FR-F06・AC-F02/AC-F06/
    # AC-F07・設計08 §2.7.3「見えない枠を掴ませない」）。
    # デモモードの pick_image は実ファイルダイアログを持たない（null を
    # 返すだけだった）ため、bridge.ts のデモ分岐に疑似の expand-page 応答を
    # 追加してこの経路を検証する。pick_image は1回目に必ず front=mismatch の
    # 疑似パスを返す（2回目以降は match/undecidable を巡回する・任意対応）
    # ——core 側の verdict/score/faces 追加が完了していなくても、GUI 側の
    # 配線（黄帯・上書きボタン・可視集合）を単独で確認できる。
    #
    # 枠の可視・不可視は canvas 上の細い矩形ストロークで、低ズーム
    # （既定 zoom=0.35）ではピクセル走査が不安定になりうるため、より頑健で
    # 意味も直接的なヒットテスト（クリックで選択できるか）で検証する
    # （L-Q1 の教訓どおり draw() とヒットテストは同じ可視集合を見るため、
    # ヒットテストで確認すれば描画も同じ結果になっているはず）。
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    expect(page.get_by_text("様式が合いません", exact=False).first).to_be_visible()
    expect(page.get_by_role(
        "button", name="判定を無視して枠を表示する", exact=False)).to_be_visible()

    # DEMO_TEMPLATE の front 面にある「person_氏名」欄（page 座標
    # x:400,y:300,w:600,h:90）の中心を、既定の zoom(0.35)・pan({x:10,y:10})
    # から画面座標へ逆算する。バナーの高さ変化で canvas の位置がずれても
    # 追従できるよう、クリックのたびに bounding_box を取り直す
    def field_center():
        box = page.locator("canvas.canvas").bounding_box()
        return box["x"] + 10 + 700 * 0.35, box["y"] + 10 + 345 * 0.35

    fx, fy = field_center()
    page.mouse.click(fx, fy)
    # 不一致面の欄は上書き前には選択できない（見えない枠を掴ませない）
    expect(page.get_by_text("要素が選択されていません")).to_be_visible()

    # 上書きすると枠が出て掴めるようになる
    page.get_by_role("button", name="判定を無視して枠を表示する", exact=False).click()
    expect(page.get_by_text("様式判定を無視して枠を表示しています", exact=False)
           .first).to_be_visible()
    fx, fy = field_center()
    page.mouse.click(fx, fy)
    expect(page.get_by_text("選択中の欄")).to_be_visible()

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
    # 選択すると「選択中」タブへ自動で戻り、欄の詳細（名前入力）が出る。
    # 判定はすべて expect（状態が整うまで再試行する）で置く（issue #79）
    # ——is_visible()／input_value() の素の assert は「今この瞬間」を読むため、
    # タブ切替の再描画が並列負荷で遅れると、実装が正しくても落ちていた
    expect(page.get_by_text("選択中の欄")).to_be_visible()
    expect(page.get_by_label("欄の名前（出力の列名になります）")).to_have_value("person_氏名")

    # 実行タブへ切り替える（Editor は active=false になり非表示になるが
    # アンマウントはされない）
    page.locator(".tabs button", has_text="実行").click()
    expect(page.get_by_text("次の作業（目視確認）")).to_be_visible()
    page.keyboard.press("Delete")

    # テンプレート編集タブへ戻り、欄が消えていない（選択も保持されたまま）
    # ことを確認する。旧実装ならここで「要素が選択されていません」に変わり、
    # 出力列一覧から person_氏名 が消えている
    page.locator(".tabs button", has_text="テンプレート編集").click()
    expect(page.get_by_text("選択中の欄")).to_be_visible()
    expect(page.get_by_label("欄の名前（出力の列名になります）")).to_have_value("person_氏名")
    page.locator("#edittab-output").click()
    expect(page.get_by_role("button", name="person_氏名", exact=True)).to_be_visible()

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    expect(page.get_by_text("次の作業（目視確認）")).to_be_visible()


def test_editor_user_template_list_opens_saved_template(page):
    # issue #72 (t)・FR-F27/F29: 「利用者テンプレートから開く」は
    # list_user_templates（表示名のみ・絶対パスなし・設計08 §3.2.2）の一覧を
    # 出し、読み込めないテンプレートは理由付きで示す（FR-F28「1件の不正で
    # 一覧全体を止めない」）。開くと read_user_template(name) を経由して
    # 編集状態が入れ替わる。
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    page.get_by_role("button", name="利用者テンプレートから開く").click()
    dialog = page.get_by_role("dialog", name="利用者テンプレートから開く")
    expect(dialog.get_by_text("帳票B")).to_be_visible()
    # 壊れたテンプレートも理由付きで一覧に出る（FR-F28）
    expect(dialog.get_by_text("読み込めません（JSON として読めません）")).to_be_visible()

    dialog.get_by_role("button", name="開く", exact=True).click()
    page.wait_for_selector("text=テンプレート読込: 帳票B")

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_match_panel_shows_candidates_after_opening_image(page):
    # issue #72 (t)・FR-F28/F46: 画像を開くと match_templates（core の
    # match-templates を1プロセスで呼ぶ Rust コマンド・設計08 §3.3）を呼び、
    # 「この画像に合うテンプレート」パネルへ候補を並べる（rankCandidates・
    # 設計08 §3.4）。デモの疑似応答は入力によらず固定candidatesを返すため、
    # pick_image の疑似パスの巡回（mismatch/match/undecidable）とは独立に
    # 検証できる。
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=この画像に合うテンプレート")
    # 現在開いているテンプレートが一致判定の巡回に当たっていると畳まれる
    # （設計08 §3.4）ため、その場合だけ展開する。分岐の前にパネルの中身が
    # 描けたことを待つ（issue #79）——描画前に is_visible() を読むと常に
    # False になり、畳まれたまま先へ進んで後の判定が落ちる
    show_btn = page.get_by_role("button", name="表示する")
    expect(show_btn.or_(page.get_by_text("★推奨")).first).to_be_visible()
    if show_btn.is_visible():
        show_btn.click()

    expect(page.get_by_text("★推奨")).to_be_visible()   # 1件だけの一致候補が推奨される
    expect(page.get_by_text("帳票B", exact=False).first).to_be_visible()
    # 照合できなかったテンプレートの件数・理由が出る（FR-F28）
    expect(page.get_by_text("読めないテンプレート 1 件", exact=False)).to_be_visible()

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_save_as_user_template_confirms_overwrite(page):
    # issue #72 (t)・FR-F26・設計08 §3.2.3: 「利用者テンプレートとして保存」は
    # 名前を尋ね（window.prompt）、既存の同名テンプレートがあれば上書き確認
    # （window.confirm）を GUI 側で先に出す——Rust の save_user_template は
    # 名前の妥当性だけを見て、確認は UI の責務（設計どおり）。
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    seen_dialog_types = []

    def handle_dialog(dialog):
        seen_dialog_types.append(dialog.type)
        if dialog.type == "prompt":
            # デモの一覧に既にある名前（帳票B）を指定し、上書き確認を発火させる
            dialog.accept("帳票B")
        else:
            dialog.accept()

    page.on("dialog", handle_dialog)
    try:
        page.get_by_role("button", name="利用者テンプレートとして保存", exact=True).click()
        page.wait_for_selector("text=利用者テンプレートとして保存しました")
    finally:
        page.remove_listener("dialog", handle_dialog)

    assert "prompt" in seen_dialog_types, "名前入力（window.prompt）が発火していない"
    assert "confirm" in seen_dialog_types, "同名の上書き確認（window.confirm）が発火していない"

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=次の作業（目視確認）")


def test_editor_restores_last_template_notice_after_reload(page):
    # issue #72 (t)・スバル差し戻し1: read_default_template は
    # config.last_template を解決して返す（gui/src-tauri/src/lib.rs・あくあ
    # 実装）。これまでは「出荷」「前回使った利用者テンプレート」のどちらが
    # 復元されたかが画面から分からなかった——restoredTemplateNotice
    # （Editor.tsx）が last_template を読んで「前回のテンプレート（<id>）を
    # 読み込みました」と明示する。デモモードは write_config を localStorage
    # へ永続化するため、ページ再読み込み（＝アプリ再起動相当）をまたいで
    # last_template が残る（AC-F26 と同じ「再起動で前回テンプレートが復元
    # される」ことの確認）。このテストは最後に実行する——reload でデモの
    # インメモリ状態（mockStaged 等）が丸ごと初期化されるため。
    page.locator(".tabs button", has_text="実行").click()
    # 先行テスト（test_run_flow_shows_progress_and_summary）が完了サマリを
    # 表示したままの可能性がある（RunScreen はタブをまたいでマウントされ
    # 続けるため summary state が残る）。テンプレート選択カードはサマリ非
    # 表示時（手順1〜3と同じ条件）にしか出ないため、必要なら手前へ戻す
    change_btn = page.get_by_role("button", name="条件を変更して読み取る")
    # 分岐の前に実行タブの中身が描けたことを待つ（issue #79）。描画前に
    # is_visible() を読むと常に False になり、サマリを畳まないまま
    # 「読み取る帳票の選択」を待って落ちる
    expect(change_btn.or_(page.get_by_text("読み取る帳票の選択")).first).to_be_visible()
    if change_btn.is_visible():
        change_btn.click()
    page.wait_for_selector("text=読み取る帳票の選択")

    select = page.get_by_label("読み取りに使うテンプレート")
    select.select_option(label="帳票B")
    # write_config（デモモードは localStorage へ書く・bridge.ts の
    # demoConfigWrite）は onChange 内の非同期処理。固定 sleep で「たぶん
    # 書けた」を仮定せず、保存された中身そのものを待つ（issue #79）
    page.wait_for_function(
        """() => {
            try {
                const raw = window.localStorage.getItem('chouhyo-demo-config');
                return !!raw && JSON.parse(raw).last_template === 'user:帳票B';
            } catch (e) { return false; }
        }"""
    )

    page.reload(wait_until="networkidle")
    # 再読み込み後も固定 sleep をやめ、タブが描けたことで待つ
    edit_tab = page.locator(".tabs button", has_text="テンプレート編集")
    expect(edit_tab).to_be_visible()

    edit_tab.click()
    page.wait_for_selector("text=前回のテンプレート（帳票B）を読み込みました")

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）。reload で
    # summary state も初期化されているため、以前と違い手順1〜3の初期画面へ
    # 戻る（このテストが最後なので後続テストへの影響はない）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=読み取る帳票の選択")


def test_editor_detect_frames_generate_accept_and_undo(page):
    # issue #73 (b)・設計08 §4: ページ全体からの枠候補一括生成。生成→一覧に
    # 表示→一括採用（overlaps_existing の候補は対象外）→確定枠（出力列一覧）
    # に増える→Undo で直前の状態に戻ることを確認する。
    # Orchestrator決定（おかゆ実機検証後）: 枠候補パネルは編集領域上部の
    # 全幅カードから .panel-wrap の第3タブ「枠候補」へ移した。生成した瞬間に
    # 自動でこのタブへ切り替わる（候補が残っている間は自動で戻らない）ため、
    # 出力列一覧を数える箇所では明示的に #edittab-output へ切り替える。
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")

    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=この画像に合うテンプレート")

    page.locator("#edittab-output").click()
    before = page.locator("#edittabpanel .panel-outrow").count()

    page.get_by_role("button", name="ページ全体から枠候補を生成").click()
    page.wait_for_selector("text=枠候補（4 件）")
    # 生成すると自動で「枠候補」タブへ切り替わる
    expect(page.locator("#edittab-candidates")).to_have_attribute("aria-selected", "true")
    # 疑似応答: 表1（overlaps無し）＋欄3（うち1件 overlaps_existing）。
    # 件数の判定も expect（再試行つき）で置く（issue #79）——count() は
    # 「今この瞬間」を数えるため、再描画の遅れがそのまま偽の失敗になる
    expect(page.get_by_text("既存と重なり")).to_have_count(1)

    page.get_by_role("button", name="選んだ候補を採用").click()
    page.wait_for_selector("text=3 件を採用しました")
    # overlaps だった候補（c2）が1件残るため自動ではタブが戻らない設計——
    # 出力列一覧は明示的にタブを切り替えて数える
    page.locator("#edittab-output").click()
    outrows = page.locator("#edittabpanel .panel-outrow")
    expect(outrows).to_have_count(before + 3)   # 採用後の欄/表の増分
    expect(page.get_by_text("field_01", exact=False)).to_be_visible()
    # overlaps だった候補は個別採用の対象として枠候補タブに残る
    page.locator("#edittab-candidates").click()
    expect(page.get_by_text("既存と重なり")).to_have_count(1)

    # Undo（Ctrl+Z）で直前の採用を取り消す。履歴コマは編集後 400ms 静止で
    # 積まれる（Editor.tsx の履歴 useEffect）。この待ちは**ページ側の**
    # タイマーで行う（issue #79）——wait_for_timeout はドライバ側の実時間で
    # 数えるため、並列負荷でページのイベントループが止まると「500ms 経った
    # のに履歴はまだ積まれていない」が起きる。同じイベントループに後から
    # 積んだタイマーなら、先に登録された 400ms の方が必ず先に発火する
    page.evaluate("() => new Promise((r) => setTimeout(r, 600))")
    page.keyboard.press("Control+z")
    # 候補は1→4件の非ゼロ遷移なので自動タブ切替は起きない。出力列タブへ
    # 切り替えて確定枠が採用前の件数に戻ったことを確認する（固定 sleep の
    # 代わりに、件数が戻るまで再試行する expect で待つ）
    page.locator("#edittab-output").click()
    expect(outrows).to_have_count(before)   # Undo で採用前の件数へ戻る

    # 実行タブへ戻す（後続テストが増えても状態を素直に保つ）。候補の採用で
    # markDirty(true) が立ち、Undo は「保存済みに戻す」操作ではないため
    # dirtyState は立ったまま——App.tsx の switchTo はタブ離脱時に
    # window.confirm で破棄確認を挟む（未保存のまま編集タブを離れる事故を
    # 防ぐ設計どおりの動作）。ここでは実際に保存はしないため確認を承諾する。
    # 直前の test_editor_restores_last_template_notice_after_reload が reload
    # で summary state を初期化済み（このテストはその summary=null の状態を
    # 引き継ぐ・ここでは一度も読み取りを実行していない）ため、完了後専用の
    # 「次の作業（目視確認）」ではなく手順1〜3の初期画面を待つ
    page.once("dialog", lambda d: d.accept())
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=読み取る帳票の選択")


def test_editor_size_mismatch_new_template_generate_accept_save(page):
    # ころね（user_advocate）の初見ユーザー目線レビュー Must: 用紙サイズ／
    # 向き不一致（expandAlignNotice の reason==="size"・赤帯）では、様式不一致
    # （罫線判定・黄帯）の時にしか出なかった「この紙用に新しいテンプレートを
    # 作る」ボタンが出ていなかった。README はこのボタンを唯一の復旧導線として
    # 案内しているため、初見ユーザーは出荷テンプレートの上で候補生成→採用→
    # 列名変更まで進み、保存時に「面の範囲外にある要素があります」で手戻りに
    # なっていた。赤帯にも同じボタンを出す修正後、そこから新規テンプレート
    # 作成→枠候補生成→採用→利用者テンプレート保存まで一気通貫で通ることを
    # 確認する。
    #
    # デモモードの pick_image は「帳票を開く」を押すたび mismatch → match →
    # undecidable → size-mismatch の順で疑似パスを巡回する（bridge.ts の
    # DEMO_FORMAT_VARIANTS・demoImagePickCount）。この巡回カウンタは他の
    # テストの実行順・押下回数に依存して累積するため、reload でリセットして
    # から4回押し、4回目で確実に size-mismatch.png を引く。
    page.reload(wait_until="networkidle")
    # 固定 sleep をやめ、タブが描けたことで待つ（issue #79）
    edit_tab = page.locator(".tabs button", has_text="テンプレート編集")
    expect(edit_tab).to_be_visible()
    edit_tab.click()
    page.wait_for_selector("text=管理者向け")

    open_btn = page.get_by_role("button", name="帳票を開く（PDF・画像）")
    for _ in range(3):
        open_btn.click()
        page.wait_for_selector("text=この画像に合うテンプレート")

    open_btn.click()
    page.wait_for_selector("text=用紙サイズ／向きがテンプレートと合っていません")
    new_tpl_btn = page.get_by_role(
        "button", name="この紙用に新しいテンプレートを作る", exact=True)
    # 寸法/向き不一致の赤帯にも新規テンプレート作成の導線が出る
    expect(new_tpl_btn).to_be_visible()
    expect(page.get_by_text("この紙でテンプレートを新しく作るには下のボタン",
                            exact=False)).to_be_visible()

    new_tpl_btn.click()
    page.wait_for_selector("text=空のテンプレートで開きました")

    page.get_by_role("button", name="ページ全体から枠候補を生成").click()
    page.wait_for_selector("text=枠候補（4 件）")

    page.get_by_role("button", name="選んだ候補を採用").click()
    page.wait_for_selector("text=3 件を採用しました")

    seen_dialog_types = []

    def handle_dialog(dialog):
        seen_dialog_types.append(dialog.type)
        # walkthrough_b: 機微情報を含まない検証専用の名前
        dialog.accept("walkthrough_b")

    page.on("dialog", handle_dialog)
    try:
        page.get_by_role("button", name="利用者テンプレートとして保存", exact=True).click()
        page.wait_for_selector("text=利用者テンプレートとして保存しました")
    finally:
        page.remove_listener("dialog", handle_dialog)
    assert "prompt" in seen_dialog_types, "名前入力（window.prompt）が発火していない"

    # 実行タブへ戻す（このテストの冒頭で reload しており、一度も読み取りを
    # 実行していないため summary は null——完了後専用の
    # 「次の作業（目視確認）」ではなく手順1〜3の初期画面を待つ）
    page.locator(".tabs button", has_text="実行").click()
    page.wait_for_selector("text=読み取る帳票の選択")
