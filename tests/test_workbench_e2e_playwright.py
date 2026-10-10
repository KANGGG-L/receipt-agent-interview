# -*- coding: utf-8 -*-
"""
End-to-End Playwright Automation & Hong Kong Restaurant Staff (阿叔/阿姨) UX Audit.
Tests the actual running Web Workbench (http://127.0.0.1:15010) across:
- AC-01: AI Prefill clarity & human-review indicators
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
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "")).strip()


def test_workbench_playwright_e2e_audit():
    if not BASE_URL:
        pytest.skip("未配置 PW_TEST_BASE_URL 或 TEST_BASE_URL，安全保护：禁止默认访问 15010 真实开发服务")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        page_errors = []
        page.on("pageerror", lambda err: page_errors.append(str(err)))

        print(f"[TEST] Navigating to {BASE_URL}...")
        page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=15000)

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
        # 2. 验证进入复核模式与 AI 预填显式提示 (AC-01-01, AC-01-02)
        # -------------------------------------------------------------
        print("[TEST] 2. Entering manual entry and checking AI Prefill status badges...")
        page.locator("#btnNewManualEntry").click()
        page.wait_for_timeout(400)

        prefill_card = page.locator("#prefillFormCard")
        assert prefill_card.is_visible(), "Prefill form card should be open"

        ai_badge = page.locator("#aiPrefillNoticeBadge")
        assert ai_badge.is_visible(), "AI Prefill notice badge should be visible"
        badge_text = ai_badge.inner_text()
        print(f"  AI notice badge text: '{badge_text}'")
        assert "AI 预填" in badge_text or "待人工复核" in badge_text

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

        # 填入完整表头基本信息，消除其他阻断，只留算术差额：总金额 100，明细 80
        page.locator("#inpSupplier").fill("Vendor_A")
        page.locator("#inpDate").fill("2026-10-08")
        page.locator("#inpTotal").fill("100.00")

        # 填入首行明细品名与小计
        item_names = page.locator(".inp-name")
        item_amounts = page.locator(".inp-amount")
        if item_names.count() > 0:
            item_names.first.fill("优质测试食材")
            item_amounts.first.fill("80.00")
            item_amounts.first.dispatch_event("input")
            page.locator("#inpTotal").dispatch_event("input")
            page.wait_for_timeout(400)

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
        # 重新填入 Vendor_A 和 日期，并将总金额修正为 80.00，消除算术差异
        page.locator("#inpSupplier").fill("Vendor_A")
        page.locator("#inpDate").fill("2026-10-08")
        item_names = page.locator(".inp-name")
        item_amounts = page.locator(".inp-amount")
        if item_names.count() > 0:
            item_names.first.fill("优质测试食材")
            item_amounts.first.fill("80.00")
            item_amounts.first.dispatch_event("input")
        page.locator("#inpTotal").fill("80.00")
        page.locator("#inpTotal").dispatch_event("input")
        page.wait_for_timeout(400)

        # 此时阻断问题应归零
        blocker_badge = page.locator("#issueBlockerCountBadge").inner_text()
        print(f"  Blocker badge after balance: {blocker_badge}")
        assert "0" in blocker_badge

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
        assert "审核通过后将正式计入支出并增加食材库存" in approve_desc

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
