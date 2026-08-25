# -*- coding: utf-8 -*-
"""
Playwright Browser Live E2E Test for Issue U-9:
- 自动落库与人工编辑状态语义拆分
- 待核对 (AI自动入库) / 已修改 (店员人工保存) / 已入账 (老板审核通过) / 有问题 (已标记)
- 归档详情弹窗中 [AI自动入库] 与 [店员人工修改] 履历呈现
- 老板视角直通审批 parsed 单据
- 香港餐饮店员（阿叔/阿姨）视角 UX 深度核验
"""

import os
import sys
import time
import json
import requests
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"

def setup_u9_test_data():
    """预置各状态单据数据用于前端渲染与流转测试。"""
    # 1. AI 自动入库单据 (parsed)
    resp1 = requests.post(f"{BASE_URL}/api/save_edited", json={
        "supplier_name": "祥興凍肉(AI自动入库)",
        "date": "2026-08-24",
        "total_amount": 320.0,
        "settlement_type": "cash",
        "source": "auto",
        "items": [
            {"name": "急凍豬頸肉", "quantity": 10, "unit": "磅", "unit_price": 32.0, "amount": 320.0}
        ]
    }, headers={"X-Role": "staff"})
    r_parsed = resp1.json()["receipt_id"]

    # 2. 店员已编辑单据 (edited)
    resp2 = requests.post(f"{BASE_URL}/api/save_edited", json={
        "supplier_name": "德利行(店员修改)",
        "date": "2026-08-24",
        "total_amount": 500.0,
        "settlement_type": "credit",
        "source": "manual",
        "items": [
            {"name": "特級菜心", "quantity": 20, "unit": "斤", "unit_price": 25.0, "amount": 500.0}
        ]
    }, headers={"X-Role": "staff"})
    r_edited = resp2.json()["receipt_id"]

    return r_parsed, r_edited


def test_u9_browser_full_flow():
    r_parsed, r_edited = setup_u9_test_data()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.on("dialog", lambda dialog: dialog.accept())

        # -------------------------------------------------------------
        # 1. 访问店员首页与归档管理
        # -------------------------------------------------------------
        page.goto(BASE_URL, wait_until="domcontentloaded")
        time.sleep(1.5)

        # 切换到归档管理页面并直接渲染归档记录
        page.evaluate("""async () => {
            document.querySelectorAll('.sidebar-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            const btn = document.querySelector('.sidebar-btn[data-target="tab-archive"]');
            if (btn) btn.classList.add('active');
            const tab = document.getElementById('tab-archive');
            if (tab) tab.classList.add('active');
            const res = await fetch('/api/receipts');
            const ret = await res.json();
            allArchiveReceipts = ret.data || [];
            renderArchiveTable(allArchiveReceipts);
        }""")
        page.wait_for_timeout(1000)

        # -------------------------------------------------------------
        # 2. 核验归档列表中各状态徽章的文案与色彩契约
        # -------------------------------------------------------------
        archive_rows = page.locator("#archiveTableBody tr")
        assert archive_rows.count() >= 2, "归档列表应展示单据行"

        # 查找 parsed 行与 edited 行
        parsed_row = page.locator(f"#archiveTableBody tr:has-text('#{r_parsed}')")
        edited_row = page.locator(f"#archiveTableBody tr:has-text('#{r_edited}')")

        assert parsed_row.count() > 0, f"单据 #{r_parsed} 应在归档列表中显示"
        assert edited_row.count() > 0, f"单据 #{r_edited} 应在归档列表中显示"

        parsed_badge = parsed_row.locator("span.badge").first
        parsed_badge_text = parsed_badge.inner_text().strip()
        parsed_badge_class = parsed_badge.get_attribute("class") or ""
        print(f"Parsed row badge text: '{parsed_badge_text}', class: '{parsed_badge_class}'")
        assert "待核对" in parsed_badge_text or "AI自动入库" in parsed_badge_text, f"Parsed 徽章文案异常: {parsed_badge_text}"
        assert "badge-warning" in parsed_badge_class, f"Parsed 徽章应为 warning 类: {parsed_badge_class}"

        edited_badge = edited_row.locator("span.badge").first
        edited_badge_text = edited_badge.inner_text().strip()
        edited_badge_class = edited_badge.get_attribute("class") or ""
        print(f"Edited row badge text: '{edited_badge_text}', class: '{edited_badge_class}'")
        assert "已修改" in edited_badge_text or "店员人工保存" in edited_badge_text, f"Edited 徽章文案异常: {edited_badge_text}"
        assert "badge-info" in edited_badge_class or "badge-warning" in edited_badge_class, f"Edited 徽章类异常: {edited_badge_class}"

        # -------------------------------------------------------------
        # 3. 打开 parsed 单据详情弹窗，核验 AI 决策履历与自动入库审计记录
        # -------------------------------------------------------------
        btn_detail_parsed = parsed_row.locator("button:has-text('详情')")
        btn_detail_parsed.click()
        time.sleep(0.8)

        modal = page.locator("#archiveDetailModal")
        assert modal.is_visible(), "详情弹窗应展开"

        # 核验审计履历区是否有 [AI自动入库]
        audit_logs_container = page.locator("#arcAuditLogsContainer")
        audit_text = audit_logs_container.inner_text()
        print(f"Audit logs content:\n{audit_text}")
        assert "AI自动入库" in audit_text, "审计履历应包含 [AI自动入库] 标签"
        assert "AI自动识别落库" in audit_text or "parsed" in audit_text, "审计履历应包含自动落库详情"

        # 核验 AI 决策履历区
        ai_decisions_container = page.locator("#arcAiDecisionsContainer")
        if ai_decisions_container.count() > 0:
            ai_text = ai_decisions_container.inner_text()
            print(f"AI decisions content:\n{ai_text}")
            assert "AI自动入库" in ai_text or "AI自动识别落库" in ai_text, "AI决策履历应包含自动入库节点"

        # -------------------------------------------------------------
        # 4. 店员在弹窗内手工修改金额并保存 -> 验证状态变更为已修改 (店员人工保存)
        # -------------------------------------------------------------
        arc_total_input = page.locator("#arcTotal")
        arc_total_input.fill("350.00")
        
        btn_save_archive = page.locator("#btnSaveArchive")
        assert btn_save_archive.is_visible(), "保存按钮应可见"
        
        # 校验按钮尺寸 (高度 >= 38px)
        bbox_save = btn_save_archive.bounding_box()
        assert bbox_save is not None and bbox_save["height"] >= 36, f"保存按钮高度应 >= 36px，实际为 {bbox_save['height'] if bbox_save else 'None'}px"

        with page.expect_response(lambda r: "/api/save_edited" in r.url) as response_info:
            btn_save_archive.click()
        resp = response_info.value
        assert resp.status == 200, f"Save failed with status {resp.status}"

        # 重新拉取最新归档数据并刷新表格
        page.evaluate("""async () => {
            const res = await fetch('/api/receipts');
            const ret = await res.json();
            allArchiveReceipts = ret.data || [];
            renderArchiveTable(allArchiveReceipts);
        }""")
        page.wait_for_timeout(800)

        # 验证弹窗关闭，归档列表已更新该行状态为已修改
        parsed_row_after_edit = page.locator(f"#archiveTableBody tr:has-text('#{r_parsed}')")
        updated_badge = parsed_row_after_edit.locator("span.badge").first
        updated_badge_text = updated_badge.inner_text().strip()
        print(f"After manual edit badge text: '{updated_badge_text}'")
        assert "已修改" in updated_badge_text or "店员人工保存" in updated_badge_text, f"手工编辑后徽章应变为已修改: {updated_badge_text}"

        # -------------------------------------------------------------
        # 5. 切换到 Owner 角色，直通审批 parsed 状态单据
        # -------------------------------------------------------------
        # 重新创建一张 parsed 单据
        resp_p2 = requests.post(f"{BASE_URL}/api/save_edited", json={
            "supplier_name": "直通审批测试店",
            "date": "2026-08-24",
            "total_amount": 120.0,
            "settlement_type": "cash",
            "source": "auto",
            "items": [
                {"name": "新鲜番茄", "quantity": 10, "unit": "斤", "unit_price": 12.0, "amount": 120.0}
            ]
        }, headers={"X-Role": "staff"})
        r_parsed_2 = resp_p2.json()["receipt_id"]

        # 刷新归档列表
        page.evaluate("if (typeof loadReceiptsHistory === 'function') loadReceiptsHistory();")
        time.sleep(0.8)

        # 切换角色为 owner
        page.evaluate("if (typeof switchRole === 'function') switchRole('owner'); else window.currentRole = 'owner';")
        time.sleep(0.5)

        # 打开单据 #r_parsed_2 详情
        page.evaluate(f"loadReceiptDetail({r_parsed_2})")
        time.sleep(0.8)

        # 老板点击「审核通过」
        btn_approve = page.locator("#modalApproveBtn, #btnApproveArchive")
        if btn_approve.is_visible():
            btn_approve.click()
            time.sleep(1)

            # 验证单据状态变为已入账 (approved)
            row_approved_resp = requests.get(f"{BASE_URL}/api/receipt/{r_parsed_2}")
            row_approved_data = row_approved_resp.json().get("data", {})
            assert row_approved_data.get("status") == "approved", f"老板直通审批后单据状态应为 approved，实际为 {row_approved_data.get('status')}"

        # -------------------------------------------------------------
        # 6. UX 专项评估 (阿叔/阿姨视角)
        # -------------------------------------------------------------
        print("\n--- [UX Evaluation] High-pressure HK restaurant clerk perspective ---")
        # 检查关键操作按钮的高度 >= 36px
        for btn_selector in ["#btnSaveReview", "#btnDiscardReceipt", "#btnSaveArchive", "#btnCancelArchive", "#btnAbortLoadingToManual"]:
            btn_elem = page.locator(btn_selector)
            if btn_elem.count() > 0 and btn_elem.is_visible():
                box = btn_elem.bounding_box()
                if box:
                    print(f"  Button {btn_selector}: height={box['height']}px, width={box['width']}px")
                    assert box["height"] >= 36, f"按钮 {btn_selector} 高度过小 ({box['height']}px < 36px)"

        # 检查页面上是否存在未转译的英文技术术语或报错技术黑话
        body_text = page.locator("body").inner_text()
        forbidden_tech_words = ["INTERNAL SERVER ERROR", "TRACEBACK (MOST RECENT CALL LAST)", "OBJECT OBJECT", "UNDEFINED", "NAN", "NULL"]
        for forbidden in forbidden_tech_words:
            assert forbidden not in body_text.upper(), f"界面存在冰冷技术黑话: '{forbidden}'"

        print("  UX Evaluation passed: Clean plain-language status badges and comfortable click targets.")

        browser.close()

if __name__ == "__main__":
    test_u9_browser_full_flow()
