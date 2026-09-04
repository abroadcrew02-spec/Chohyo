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
        # 既存テストは「枠候補の自動生成 OFF」（config.auto_detect_frames_on_open
        # = false）で回す。この一群は**テンプレートを適用している状態**の
        # 挙動（様式不一致の帯・重なり判定・Undo）を固定するもので、既定 ON の
        # 初回読み込みフローでは記憶が無い限り候補パスに入って前提が変わる。
        # OFF は設計 T-1「従来と完全に同じ」の回帰網でもある——ON 側の検証は
        # ファイル末尾の test_editor_autodetect_* が各自の config で行う。
        # デモの read_config は localStorage を返すだけなので、**実運用の
        # config.json には一切触れない**
        pg.evaluate(
            "() => window.localStorage.setItem('chouhyo-demo-config',"
            " JSON.stringify({ auto_detect_frames_on_open: false }))")
        pg.reload(wait_until="networkidle")
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


def _expect_template_applied(page, name):
    """テンプレート「name」が適用されたことを、報告先の違いに依らずに待つ。

    適用の報告先は画像を開いているかで変わる（Editor.tsx の
    templateDecisionMsg・a11y Should-1）。画像があるときは適用中バーが立ち、
    ライブ領域を1本に絞るため `.msg` は空になる。画像が無いときはバーが
    無いので従来どおり `.msg` に出る。スモークは module スコープの page を
    共有していて「直前のテストが画像を開いたか」で両方に振れるため、どちらでも
    通る形で待つ——この待ちの目的は適用の完了であって、どちらのライブ領域に
    出たかではない。
    """
    expect(
        page.get_by_text(f"適用中のテンプレート: {name}", exact=False)
        .or_(page.get_by_text(f"テンプレート読込: {name}", exact=False))
        .first
    ).to_be_visible()


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
    _expect_template_applied(page, "帳票B")

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
    expect(show_btn.or_(page.locator(".recommend-badge")).first).to_be_visible()
    if show_btn.is_visible():
        show_btn.click()

    # 推奨は記号（旧「★推奨」）ではなく語のバッジで示す（UI 設計 §3・
    # 色だけに頼らない・読み上げでも「推奨」と読める）
    expect(page.locator(".recommend-badge")).to_have_text("推奨")
    # 一覧のボタンは行ごとに同じ見た目のラベルだが、accessible name には
    # テンプレート名が入る（同名ボタンが並ぶのを避ける・UI §7）
    expect(page.get_by_role(
        "button", name="このテンプレートを使う（帳票B）")).to_be_visible()
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
    # 4件（旧: 3件）。デモの c2 は「実コアが --template 無しでも
    # overlaps_existing:true を返す」という実物と食い違う疑似応答だったため、
    # person_氏名 に幾何的に重なる位置へ移して false で返すように直した。
    # 空テンプレートの上では重なる相手がいないので4件すべてが対象になる
    # ——この件数差そのものが「候補は空テンプレートの上で作る」の回帰検知
    page.wait_for_selector("text=4 件を採用しました")

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

# ---------------------------------------------------------- 初回読み込みフロー
# テンプレート編集で帳票を開いたときの分岐（AC-F76〜AC-F83・Q-A2・2026-09-04）。
#
# 上の既存テスト群は fixture で自動生成 OFF に固定してある（従来動作の回帰網）。
# ここから下は各テストが自分で config を置いてから reload する——ON の既定と
# 記憶の有無で挙動が変わるため、前のテストの残りに依存させない。
# reload はデモの巡回カウンタ（bridge.ts の demoImagePickCount）も初期化する
# ので、「帳票を開く」1回目は必ず mismatch.png になる。


def _apply_demo_config(page, cfg):
    """デモの config（localStorage）を丸ごと置き換えて再読み込みする。

    デモモードの read_config は localStorage を返すだけなので、これが実物の
    config.json を書き替える代わりになる。**実運用の config.json には触れない**。
    """
    page.evaluate(
        "(cfg) => window.localStorage.setItem('chouhyo-demo-config', JSON.stringify(cfg))",
        cfg,
    )
    page.reload(wait_until="networkidle")
    expect(page.locator(".tabs button", has_text="テンプレート編集")).to_be_visible()


def _open_editor(page):
    page.locator(".tabs button", has_text="テンプレート編集").click()
    page.wait_for_selector("text=管理者向け")


# 記憶なし（初めてこの紙を開く）／記憶あり（前回 帳票B を適用した）の2状態
_NO_MEMORY = {"auto_detect_frames_on_open": True, "last_applied_template": "",
              "last_template": "shipped"}
_REMEMBERS_B = {"auto_detect_frames_on_open": True, "last_applied_template": "user:帳票B",
                "last_template": "user:帳票B"}


def test_editor_autodetect_no_memory_shows_candidates(page):
    # AC-F76: 記憶が無い状態で帳票を開くと、テンプレートは適用せずこの紙の
    # 枠候補を自動で作る。右パネルは枠候補タブへ切り替わり件数が出る。
    _apply_demo_config(page, _NO_MEMORY)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    page.wait_for_selector("text=枠候補（4 件）")
    expect(page.locator("#edittab-candidates")).to_have_attribute("aria-selected", "true")
    # 上部は「決定カード」に組み替わり、未適用であることを案内する
    expect(page.get_by_text("テンプレートを選ぶ", exact=True)).to_be_visible()
    expect(page.get_by_text("テンプレートはまだ適用していません", exact=False)).to_be_visible()
    expect(page.get_by_role("button", name="候補から新しく作る", exact=True)).to_be_visible()
    # AC-F83 の否定形: 候補パスでは様式不一致の帯を出さない（設計 §2.1）。
    # 1回目の疑似画像は mismatch.png なので、旧実装ならここで黄帯が出る
    expect(page.get_by_text("様式が合いません", exact=False)).to_have_count(0)
    expect(page.get_by_role(
        "button", name="判定を無視して枠を表示する", exact=False)).to_have_count(0)


def test_editor_autodetect_starts_from_empty_template(page):
    # AC-F77/AC-F72: 候補は**空のテンプレートの上**で作る。出荷テンプレートの
    # 枠は1件も載っていない（出力列タブが disabled＝欄・表が0件の機械的な証拠）
    # ため、重なりのある候補も0件になる。
    _apply_demo_config(page, _NO_MEMORY)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    page.wait_for_selector("text=枠候補（4 件）")
    expect(page.locator("#edittab-output")).to_be_disabled()
    expect(page.get_by_text("既存と重なり")).to_have_count(0)


def test_editor_autodetect_apply_template_discards_candidates(page):
    # AC-F79/AC-F82: 決定カードの「このテンプレートを使う」で適用すると、
    # 候補は破棄され欄一覧（出力列タブ）が出る。候補を1件も採用していない
    # 段階なので、**保存確認は出さない**（失うものが無い操作に確認を挟むと
    # 二択が三択に増える）。
    _apply_demo_config(page, _NO_MEMORY)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=枠候補（4 件）")

    native_dialogs = []

    def handle_dialog(dialog):
        native_dialogs.append(dialog.type)
        dialog.dismiss()

    page.on("dialog", handle_dialog)
    try:
        page.get_by_role("button", name="このテンプレートを使う（帳票B）").click()
        _expect_template_applied(page, "帳票B")
    finally:
        page.remove_listener("dialog", handle_dialog)
    # 画面内モーダル（保存前確認・破棄確認はどちらも role="alertdialog"）
    expect(page.get_by_role("alertdialog")).to_have_count(0)
    assert native_dialogs == [], f"ネイティブの確認が出た: {native_dialogs}"

    # 候補は破棄され、確定枠が載る
    expect(page.locator("#edittab-candidates")).to_be_disabled()
    expect(page.locator("#edittab-output")).to_be_enabled()
    page.locator("#edittab-output").click()
    expect(page.get_by_role("button", name="person_氏名", exact=True)).to_be_visible()
    # 決定カードは1行の適用中バーへ畳まれる。手動で選んだので「自動で適用
    # しました」の括弧書きは付かない
    expect(page.get_by_text("適用中のテンプレート: 帳票B", exact=False)).to_be_visible()
    expect(page.get_by_text("前回と同じものを自動で適用しました", exact=False)).to_have_count(0)
    expect(page.get_by_text("テンプレートを選ぶ", exact=True)).to_have_count(0)


def test_editor_autodetect_reopen_applies_remembered_template(page):
    # AC-F80: 適用したテンプレートは config.last_applied_template に覚え、
    # 次に帳票を開いたときは候補生成をスキップしてそれを自動で適用する。
    _apply_demo_config(page, _NO_MEMORY)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=枠候補（4 件）")
    page.get_by_role("button", name="このテンプレートを使う（帳票B）").click()
    _expect_template_applied(page, "帳票B")
    # 記憶が書けたことを、固定 sleep ではなく保存された中身そのもので待つ
    page.wait_for_function(
        """() => {
            try {
                const raw = window.localStorage.getItem('chouhyo-demo-config');
                return !!raw && JSON.parse(raw).last_applied_template === 'user:帳票B';
            } catch (e) { return false; }
        }"""
    )

    page.reload(wait_until="networkidle")
    expect(page.locator(".tabs button", has_text="テンプレート編集")).to_be_visible()
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    expect(page.get_by_text("前回と同じものを自動で適用しました", exact=False)).to_be_visible()
    expect(page.locator("#edittab-output")).to_be_enabled()
    # 候補生成は走らない（記憶があるので二択を出す場面ではない）
    expect(page.get_by_text("枠候補（", exact=False)).to_have_count(0)
    expect(page.locator("#edittab-candidates")).to_be_disabled()


def test_editor_autodetect_off_keeps_legacy_flow(page):
    # AC-F81: config.auto_detect_frames_on_open=false は従来動作へ完全に戻す。
    # 記憶があっても自動適用せず、候補も作らない。
    _apply_demo_config(page, {"auto_detect_frames_on_open": False,
                              "last_applied_template": "user:帳票B",
                              "last_template": "shipped"})
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=この画像に合うテンプレート")

    expect(page.locator("#edittab-candidates")).to_be_disabled()
    expect(page.get_by_text("適用中のテンプレート", exact=False)).to_have_count(0)
    expect(page.get_by_text("テンプレートを選ぶ", exact=True)).to_have_count(0)
    # 起動時に読み込んだ出荷（デモ）テンプレートがそのまま残る
    page.locator("#edittab-output").click()
    expect(page.get_by_role("button", name="person_氏名", exact=True)).to_be_visible()


def test_editor_autodetect_applied_template_still_warns_on_mismatch(page):
    # AC-F83: 様式不一致の黄帯と2ボタンは「テンプレートを適用している状態」
    # では従来どおり出る（候補パスで出さないだけ・設計 §2.3 の読み替え）。
    _apply_demo_config(page, _REMEMBERS_B)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()   # 1回目 = mismatch.png

    expect(page.get_by_text("様式が合いません", exact=False).first).to_be_visible()
    expect(page.get_by_role(
        "button", name="判定を無視して枠を表示する", exact=False)).to_be_visible()
    expect(page.get_by_text("前回と同じものを自動で適用しました", exact=False)).to_be_visible()
    expect(page.get_by_text("枠候補（", exact=False)).to_have_count(0)


def test_editor_autodetect_new_template_button_generates_candidates(page):
    # Q-A2（Orchestrator 判断）: 「この紙用に新しいテンプレートを作る」を
    # 押したら、空テンプレートへ切り替えたあと候補生成まで続ける——押した人の
    # 意図は「この紙の枠が欲しい」なので、そこで手を止めさせない。
    #
    # 疑似画像は mismatch → match → undecidable → size-mismatch の順で巡回
    # する。各回の待ちは、その画像でしか出ない案内で行う（回数を数えて待つと
    # 前の回の表示のまま先へ進める）。
    _apply_demo_config(page, _REMEMBERS_B)
    _open_editor(page)
    open_btn = page.get_by_role("button", name="帳票を開く（PDF・画像）")
    open_btn.click()
    page.wait_for_selector("text=様式が合いません")
    open_btn.click()
    page.wait_for_selector("text=位置合わせ済み")
    open_btn.click()
    page.wait_for_selector("text=判定できませんでした")
    open_btn.click()
    page.wait_for_selector("text=用紙サイズ／向きがテンプレートと合っていません")

    page.get_by_role("button", name="この紙用に新しいテンプレートを作る", exact=True).click()
    # ツールバーの「ページ全体から枠候補を生成」は押さない
    page.wait_for_selector("text=枠候補（4 件）")
    expect(page.locator("#edittab-candidates")).to_have_attribute("aria-selected", "true")
    # 空テンプレートの上なので重なりは0件
    expect(page.get_by_text("既存と重なり")).to_have_count(0)
    # 未適用バーへ切り替わる（適用中バーではない）
    expect(page.get_by_text("テンプレートは適用していません", exact=False)).to_be_visible()
    expect(page.get_by_text("適用中のテンプレート", exact=False)).to_have_count(0)


# ---------------------------------------- レビュー差し戻し（H-1〜H-3・M-1〜M-4）
# 生成中の一手で不変条件が破れる穴（H-1）の受入。デモの detect-frames は本来
# 同じ tick で解決してしまい「生成中」の画面が存在しないため、デモ設定の
# demo_detect_frames_delay_ms（bridge.ts）で実コアの待ち（実測 数百ms〜3秒）を
# 再現する。世代番号そのものの判定は gui-logic の candidateResultApplies で
# 固定してあり、ここで見るのは画面側の締め出し（disabled）と復帰。
_GENERATING_DELAY_MS = 3000


def test_editor_locks_frame_replacing_actions_while_generating(page):
    # H-1／M-2 ＋ AC-F78: 候補の生成中は「確定枠を差し替える操作」をすべて
    # 止め、完了後に必ず戻す。
    #
    # H-1 の経路: 生成中に決定カードの「このテンプレートを使う」を押すと、
    # 確定枠はテンプレートの側へ入れ替わるのに遅れて解決した候補が
    # 「重なり0件・全件チェック済み」で復活し、一括採用で適用中の枠に重なる
    # 枠を足せてしまう。M-2 の経路: 「帳票を開く」「テンプレートを開く」は
    # detectingRef の早期 return に当たり、2枚目の候補が黙って作られない。
    cfg = dict(_NO_MEMORY)
    cfg["demo_detect_frames_delay_ms"] = _GENERATING_DELAY_MS
    _apply_demo_config(page, cfg)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    use_btn = page.get_by_role("button", name="このテンプレートを使う（帳票B）")
    expect(use_btn).to_be_disabled()
    expect(page.get_by_role("button", name="帳票を開く（PDF・画像）")).to_be_disabled()
    expect(page.get_by_role("button", name="テンプレートを開く", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="候補から新しく作る", exact=True)).to_be_disabled()
    expect(page.get_by_role("button", name="欄を追加", exact=True)).to_be_disabled()

    # AC-F78: 完了後は欄の追加・除外範囲・くり返し行・表裏の境界と「開く」系が
    # すべて操作できる状態に戻る（生成中の締め出しが居残らない）
    page.wait_for_selector("text=枠候補（4 件）")
    for name in ["欄を追加", "除外範囲", "くり返し行（家族・明細）", "表裏の境界"]:
        expect(page.get_by_role("button", name=name, exact=True)).to_be_enabled()
    expect(page.get_by_role("button", name="帳票を開く（PDF・画像）")).to_be_enabled()
    expect(page.get_by_role("button", name="テンプレートを開く", exact=True)).to_be_enabled()
    expect(use_btn).to_be_enabled()
    # 空テンプレートの上で作った候補なので重なりは0件のまま
    expect(page.get_by_text("既存と重なり")).to_have_count(0)


def test_editor_open_template_file_clears_applied_bar(page):
    # H-2: 適用中に「テンプレートを開く」でファイルから別のテンプレートを
    # 開いたら、前の名前を出したままの帯を残さない。ファイル起点は従来動作
    # （帯もカードも出さない・T-2）へ戻す。
    _apply_demo_config(page, _NO_MEMORY)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()
    page.wait_for_selector("text=枠候補（4 件）")
    page.get_by_role("button", name="このテンプレートを使う（帳票B）").click()
    _expect_template_applied(page, "帳票B")
    expect(page.get_by_text("適用中のテンプレート: 帳票B", exact=False)).to_be_visible()

    page.get_by_role("button", name="テンプレートを開く", exact=True).click()
    # デモの pick_json は固定パスを返す。帯が消えたことは名前ではなく
    # 「適用中のテンプレート」という語そのものの不在で見る
    page.wait_for_selector("text=テンプレート読込: C:")
    expect(page.get_by_text("適用中のテンプレート", exact=False)).to_have_count(0)
    expect(page.get_by_text("テンプレートは適用していません", exact=False)).to_have_count(0)
    expect(page.get_by_text("テンプレートを選ぶ", exact=True)).to_have_count(0)


def test_editor_stale_memory_still_offers_template_chooser(page):
    # M-1（T-5）: 記憶が指すテンプレートの実体が無いときは記憶を捨てて候補
    # パスへ落ちる。このとき決定カードも未適用バーも出ないと、「破線は何か」
    # 「どこからテンプレートを選ぶのか」が画面から消える。
    _apply_demo_config(page, {"auto_detect_frames_on_open": True,
                              "last_applied_template": "user:存在しない",
                              "last_template": "shipped"})
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    page.wait_for_selector("text=枠候補（4 件）")
    expect(page.get_by_text("見つかりませんでした", exact=False).first).to_be_visible()
    expect(page.get_by_text("テンプレートを選ぶ", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="このテンプレートを使う（帳票B）")).to_be_visible()
    expect(page.get_by_role("button", name="候補から新しく作る", exact=True)).to_be_visible()
    # 記憶は自己修復で空になる（次に開いたときに同じ失敗を繰り返さない）
    page.wait_for_function(
        """() => {
            try {
                const raw = window.localStorage.getItem('chouhyo-demo-config');
                return !!raw && JSON.parse(raw).last_applied_template === '';
            } catch (e) { return false; }
        }"""
    )


def test_editor_auto_apply_does_not_touch_last_template(page):
    # AC-F86 の GUI 分: 記憶からの自動適用は「復元」であって選択ではない。
    # 実行画面の選択（config.last_template）を書き換えない——書き換わると
    # 帳票を開いただけで実行タブの読み取り対象が変わる。
    _apply_demo_config(page, {"auto_detect_frames_on_open": True,
                              "last_applied_template": "shipped",
                              "last_template": "user:帳票B"})
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()

    expect(page.get_by_text("前回と同じものを自動で適用しました", exact=False)).to_be_visible()
    expect(page.get_by_text("枠候補（", exact=False)).to_have_count(0)
    assert page.evaluate(
        "() => JSON.parse(window.localStorage.getItem('chouhyo-demo-config')).last_template"
    ) == "user:帳票B"


def test_editor_new_template_keeps_drawing_hint_on_screen(page):
    # 前回レビューの残件: 未適用バーが立つと templateDecisionMsg が .msg を
    # 空にするため、「どう描き始めるか」の案内（newTemplateNotice）が画面から
    # 消えていた。バーの下に .note として出す。候補が0件の間は「くり返し行で
    # 表の外枠を描く」案内、候補が届いたら「候補を確認して採用」へ入れ替わる。
    cfg = dict(_REMEMBERS_B)
    cfg["demo_detect_frames_delay_ms"] = _GENERATING_DELAY_MS
    _apply_demo_config(page, cfg)
    _open_editor(page)
    page.get_by_role("button", name="帳票を開く（PDF・画像）").click()   # 1回目 = mismatch.png
    page.wait_for_selector("text=様式が合いません")
    page.get_by_role("button", name="この紙用に新しいテンプレートを作る", exact=True).click()

    hint = page.locator(".tpl-note")
    expect(page.get_by_text("テンプレートは適用していません", exact=False)).to_be_visible()
    expect(hint).to_contain_text("空のテンプレートで開きました")
    expect(hint).to_contain_text("くり返し行（家族・明細）")
    # 変化の報告は未適用バー1本に絞る（案内側にライブ領域は付けない・UI §7）
    assert hint.get_attribute("aria-live") is None
    assert hint.get_attribute("role") is None

    page.wait_for_selector("text=枠候補（4 件）")
    expect(hint).to_contain_text("候補を確認して採用してください")
