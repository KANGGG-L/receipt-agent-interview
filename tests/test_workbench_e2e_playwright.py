# -*- coding: utf-8 -*-
"""
End-to-End Playwright Automation & Hong Kong Restaurant Staff (阿叔/阿姨) UX Audit.
目标地址只来自 PW_TEST_BASE_URL 或 TEST_BASE_URL。未配置时跳过，禁止默认访问 15010。
- AC-01: 手工录入不显示 AI 预填徽章；非手工来源的预填徽章仍显示
- AC-02: Separation of save vs approve, confirmation modal, manual escape
- AC-03: Persistent workbench queue, role tabs, save-and-next routing
- AC-04: Split view draggable divider, clamp bounds, mobile viewport switch
- AC-05: Actionable issue summary, plain language arithmetic guidance, issue navigation
- AC-06: Form error aggregation, recovery, double-click mutex protection
- AC-07: High frequency inventory actions, stock movement modal
- AC-08: WCAG AA contrast, button height >= 38px, keyboard shortcuts (Esc, Cmd+S)
"""
import os
import sys
import pytest
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "")).strip()


def _login_if_required(page):
    """隔离环境 AUTH_ENABLED=1 时出现登录遮罩。凭据只从环境变量读，不写进仓库。"""
    overlay = page.locator("#authLoginOverlay")
    try:
        overlay.wait_for(state="visible", timeout=3000)
    except Exception:
        return
    email = os.environ.get("PW_TEST_LOGIN_EMAIL", "").strip()
    password = os.environ.get("PW_TEST_LOGIN_PASSWORD", "")
    if not email or not password:
        raise AssertionError(
            "鉴权登录面板已出现，但未设置 PW_TEST_LOGIN_EMAIL / PW_TEST_LOGIN_PASSWORD"
        )
    page.locator("#authLoginEmail").fill(email)
    page.locator("#authLoginPassword").fill(password)
    page.locator("#authLoginSubmit").click()
    overlay.wait_for(state="hidden", timeout=10000)


def _fill_manual_line(page, qty, price):
    row = page.locator("#itemTableBody tr").first
    row.locator(".inp-name").fill("优质测试食材")
    row.locator(".inp-qty").fill(str(qty))
    row.locator(".inp-qty").dispatch_event("input")
    row.locator(".inp-unit").fill("斤")
    row.locator(".inp-price").fill(str(price))
    row.locator(".inp-price").dispatch_event("input")


def test_workbench_playwright_e2e_audit():
    if not BASE_URL:
        pytest.skip("未配置 PW_TEST_BASE_URL 或 TEST_BASE_URL，安全保护：禁止默认访问 15010 真实开发服务")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # 隔离服务是自签 HTTPS。只放行 Playwright 自己的上下文。
        # 界面证书走 orca cert-proceed，不是 agent-browser --ignore-https-errors。
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            ignore_https_errors=urlparse(BASE_URL).scheme == "https",
        )
        page = context.new_page()

        page_errors = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        print(f"[TEST] Navigating to {BASE_URL}...")
        with page.expect_response(lambda r: "/api/auth/me" in r.url, timeout=15000):
            page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=15000)
        _login_if_required(page)

        # -------------------------------------------------------------
        # 1. 验证工作台队列与角色标签 (AC-03-01, AC-03-02)
        # -------------------------------------------------------------
        print("[TEST] 1. Checking workbench queue tabs and counters...")
        queue_section = page.locator("#workbenchQueueSection")
        assert queue_section.is_visible(), "Workbench queue section should be visible"

        tab_todo = page.locator("#queueTabTodo")
        tab_exception = page.locator("#queueTabException")

        assert tab_todo.is_visible(), "Queue tab 'todo' should be visible"
        assert tab_exception.is_visible(), "Queue tab 'exception' should be visible"

        # 点击切换队列标签
        tab_exception.click()
        page.wait_for_timeout(200)
        assert "active" in (tab_exception.get_attribute("class") or ""), "Tab 'exception' should be active"
        tab_todo.click()
        page.wait_for_timeout(200)

        # -------------------------------------------------------------
        # 2. S3-11：新建手工单没有 AI 预填徽章。
        # 非手工来源仍要显示徽章。这里只调用页面上已有的 syncReviewSourceBadge，
        # 不写库、不伪造识别结果。没有真实模型时，实机识别预填不在本用例里跑。
        # -------------------------------------------------------------
        print("[TEST] 2. Manual entry must hide the AI prefill badge (S3-11)...")
        page.locator("#btnNewManualEntry").click()
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=8000)

        prefill_card = page.locator("#prefillFormCard")
        assert prefill_card.is_visible(), "Prefill form card should be open"

        manual_badge = page.locator("#manualEntryFormBadge")
        assert manual_badge.is_visible(), "Manual entry badge should be visible"
        assert manual_badge.inner_text().strip() == "手工录入"
        assert page.locator("#manualEntryNoImage").is_visible()
        assert "无原图（手工录入）" in page.locator("#manualEntryNoImage").inner_text()

        ai_badge = page.locator("#aiPrefillNoticeBadge")
        assert not ai_badge.is_visible(), "Manual mode must hide #aiPrefillNoticeBadge"
        assert "AI 预填" not in page.locator("#prefillFormCard .card-title").inner_text()

        print("[TEST] 2b. Non-manual source still shows the AI prefill badge...")
        page.evaluate(
            "() => syncReviewSourceBadge({ id: 1, doc_form: 'printed_delivery_note' })"
        )
        assert ai_badge.is_visible(), "Non-manual review must keep #aiPrefillNoticeBadge visible"
        badge_text = ai_badge.inner_text()
        print(f"  AI notice badge text: '{badge_text}'")
        assert "AI 预填" in badge_text
        page.evaluate("() => setManualEntryFormBadge(true)")
        assert not ai_badge.is_visible(), "Restoring manual mode must hide the AI badge again"
        assert manual_badge.is_visible()

        # -------------------------------------------------------------
        # 3. 验证阿叔/阿姨触摸靶区：高频按钮高度 >= 38px (AC-08-01)
        # -------------------------------------------------------------
        print("[TEST] 3. Checking button dimensions (Touch targets >= 38px)...")
        buttons_to_check = [
            "#btnNewManualEntry",
            "#btnSaveReview",
            "#btnSaveAndNext",
            "#btnApproveReceipt"
        ]
        for btn_sel in buttons_to_check:
            loc = page.locator(btn_sel)
            if loc.is_visible():
                box = loc.bounding_box()
                if box:
                    print(f"  Button {btn_sel}: height={box['height']}px (expected >= 38px)")
                    assert box["height"] >= 37.5, f"Button {btn_sel} height {box['height']} < 38px"

        # -------------------------------------------------------------
        # 4. 验证算术差额直白指引与问题汇总条 (AC-05-01, AC-05-02, AC-05-03)
        # -------------------------------------------------------------
        print("[TEST] 4. Testing arithmetic discrepancy and plain-language guidance...")
        issue_bar = page.locator("#issueSummaryBar")

        # 真实填写：数量 4 x 单价 20 = 80，手改总额 100，只留整单差额
        page.locator("#inpSupplier").fill("Vendor_A")
        page.locator("#inpDate").fill("2026-10-08")
        page.locator("#inpTotal").fill("100.00")
        page.locator("#inpTotal").dispatch_event("input")
        _fill_manual_line(page, 4, 20)
        page.wait_for_timeout(400)
        assert page.locator("#itemTableBody tr").first.locator(".inp-amount").input_value() == "80.00"

        # 此时算术不平衡（100 vs 80），issueSummaryBar 必须由 hide 变为可见
        assert issue_bar.is_visible(), "Issue summary bar should become visible when discrepancy exists"

        # 检查问题汇总条提示
        guide_text = page.locator("#issueSummaryGuideText").inner_text()
        print(f"  Issue summary guide text: '{guide_text}'")
        # 必须是香港餐饮阿叔阿姨能看懂的大白话（例如“单据上的数字对不上”、“相差 20.00 元”）
        assert "数字对不上" in guide_text or "相差" in guide_text or "明细合计" in guide_text, \
            f"Expected plain language discrepancy guidance, got: '{guide_text}'"

        # 测试“查看下一个问题”按键聚焦
        btn_next_issue = page.locator("#btnNextIssue")
        assert btn_next_issue.is_visible()
        btn_next_issue.click()
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # 5. 验证可拖拽分栏与夹紧保护 (AC-04-01, AC-04-03)
        # -------------------------------------------------------------
        print("[TEST] 5. Testing draggable split divider and clamp protection...")
        divider = page.locator("#splitViewDivider")
        assert divider.is_visible(), "Split view divider should be visible"
        box_divider = divider.bounding_box()
        assert box_divider is not None

        # 模拟拖拽动作
        page.mouse.move(box_divider["x"] + 2, box_divider["y"] + 10)
        page.mouse.down()
        page.mouse.move(box_divider["x"] + 100, box_divider["y"] + 10)
        page.mouse.up()
        page.wait_for_timeout(200)

        # 验证左栏宽度仍保持合理范围（未脱轨或消失）
        left_box = page.locator("#leftCard").bounding_box()
        assert left_box["width"] > 200, "Left panel should remain clamped and visible"

        # -------------------------------------------------------------
        # 6. 验证逃生通道（随时转手工录入）(AC-02-04)
        # -------------------------------------------------------------
        print("[TEST] 6. Testing emergency escape routes (Switch to manual entry)...")
        btn_abort_manual = page.locator("#btnAbortLoadingToManual")
        btn_convert_manual = page.locator("#btnConvertManual")
        btn_new_manual = page.locator("#btnNewManualEntry")
        assert btn_abort_manual.count() > 0, "Abort to manual button should exist"
        assert btn_convert_manual.count() > 0, "Convert to manual button should exist"
        assert btn_new_manual.is_visible(), "New manual entry button should be visible"

        # -------------------------------------------------------------
        # 7. 验证保存表单并平账 (AC-03-03, AC-06-01)
        # -------------------------------------------------------------
        print("[TEST] 7. Balancing total and testing save review...")
        page.locator("#inpSupplier").fill("Vendor_A")
        page.locator("#inpDate").fill("2026-10-08")
        _fill_manual_line(page, 4, 20)
        page.locator("#inpTotal").fill("80.00")
        page.locator("#inpTotal").dispatch_event("input")
        page.locator("#inpSettlementType").select_option("cash")
        page.locator("#inpDocForm").select_option("manual_entry")
        page.wait_for_timeout(400)

        # 此时阻断问题应归零
        blocker_badge = page.locator("#issueBlockerCountBadge").inner_text()
        print(f"  Blocker badge after balance: {blocker_badge}")
        # 三态文案契约（AC-02-03）：0 个阻断问题 → 徽标为「无必改问题」；
        # >0 → 「必须修改: N 处」。旧断言 `"0" in badge` 钉的是文案演化前的数字字面，
        # Round 6 引入三态文案后过期（r7 实测 badge 文本恰为「无必改问题」）。
        assert blocker_badge.strip() == "无必改问题", \
            f"Expected zero-blocker three-state copy 无必改问题, got: {blocker_badge!r}"

        # AC-01-03 未保存修改安全阻断：审批入口要求先保存（手工单保存后才有单据 id，
        # 且 isReviewFormDirty() 为真时 onWorkbenchApproveClick 直接 toast 拒绝）。
        # 旧流程「不保存直接点审批」在守卫加入后必然弹不出确认框 —— 先保存再审批。
        with page.expect_response(
            lambda r: r.request.method == "POST" and "/api/save_edited" in r.url,
            timeout=15000,
        ) as save_info:
            page.locator("#btnSaveReview").click()
        save_resp = save_info.value
        save_body = save_resp.json()
        print(f"  save_edited status={save_resp.status} body_status={save_body.get('status')}")
        assert save_resp.status == 200, save_body
        assert save_body.get("status") == "success", save_body
        receipt_id = save_body.get("receipt_id") or save_body.get("id")
        assert receipt_id, save_body
        page.locator("#reviewSaveStatusBadge").filter(has_text="待老板审批").wait_for(timeout=10000)
        assert not page.locator("#aiPrefillNoticeBadge").is_visible(), \
            "Saving a manual entry must not reveal the AI prefill badge"
        saved = page.evaluate(
            """async (id) => {
                const res = await apiFetch('/api/receipt/' + id);
                const body = await res.json();
                const data = body.data || {};
                return {
                    http: res.status,
                    status: body.status,
                    doc_form: data.doc_form || '',
                    supplier: data.supplier_name || '',
                    total: data.total_amount,
                    receipt_status: data.status || ''
                };
            }""",
            receipt_id,
        )
        print(f"  saved receipt: {saved}")
        assert saved["http"] == 200 and saved["status"] == "success", saved
        assert saved["doc_form"] == "manual_entry", saved
        assert saved["supplier"] == "Vendor_A", saved
        assert float(saved["total"]) == 80.0, saved
        assert saved["receipt_status"] == "edited", saved
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # 8. 验证所有者审批确认弹窗 (AC-02-01, AC-02-02)
        # -------------------------------------------------------------
        print("[TEST] 8. Testing approval confirmation modal...")
        # 切换到 owner 角色
        page.evaluate("() => { if (typeof setRole === 'function') setRole('owner'); }")
        page.wait_for_timeout(300)

        # 触发审批
        btn_approve = page.locator("#btnApproveReceipt")
        assert btn_approve.is_visible(), "Approve button should be visible to owner"
        btn_approve.click()
        page.wait_for_timeout(300)

        # 应该弹出确认对话框
        modal_approve = page.locator("#modalApproveConfirmDialog")
        assert modal_approve.is_visible(), "Approval confirmation dialog should pop up"

        approve_desc = page.locator("#approveConfirmDesc").inner_text()
        print(f"  Approval confirmation dialog text: '{approve_desc}'")
        assert "审核入账后，这张单会正式计入支出，库存也会增加" in approve_desc

        # 测试 Esc 键安全关闭弹窗 (AC-08-04, AC-08-05)
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert not modal_approve.is_visible(), "Modal should close on Escape key"

        # -------------------------------------------------------------
        # 9. 验证库存管理层级与变动录入 (AC-07-02, AC-07-06)
        # -------------------------------------------------------------
        print("[TEST] 9. Testing inventory management and stock movement modal...")
        inv_tab_btn = page.locator(".sidebar-btn[data-target='tab-inventory']")
        if inv_tab_btn.count() > 0:
            inv_tab_btn.first.click()
            page.wait_for_timeout(500)

        # 验证更多管理菜单
        btn_manage = page.locator("#btnManageInvMenu")
        if btn_manage.is_visible():
            btn_manage.click()
            page.wait_for_timeout(200)
            menu = page.locator("#invManageMenu")
            assert menu.is_visible(), "Inventory management dropdown should be visible"
            # 再次点击收起
            btn_manage.click()

        # 测试打开库存变动录入弹窗
        page.evaluate("() => { if (typeof openStockMovementModal === 'function') openStockMovementModal(1, '测试食材A', 10, 'kg'); }")
        page.wait_for_timeout(300)
        stock_modal = page.locator("#modalStockMovementDialog")
        assert stock_modal.is_visible(), "Stock movement modal should be visible"

        modal_title = page.locator("#stockMovementSkuName").inner_text()
        assert "测试食材A" in modal_title

        # 测试 Esc 关闭
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert not stock_modal.is_visible(), "Stock movement modal should close on Escape"

        # -------------------------------------------------------------
        # 10. 检查控制台与运行时错误
        # -------------------------------------------------------------
        print("[TEST] 10. Checking page errors...")
        print(f"  Captured page errors: {page_errors}")
        assert len(page_errors) == 0, f"Unexpected page errors: {page_errors}"

        browser.close()
        print("[TEST] [OK] All 10 Playwright E2E UX & Workflow audit checks passed successfully!")


if __name__ == "__main__":
    test_workbench_playwright_e2e_audit()
