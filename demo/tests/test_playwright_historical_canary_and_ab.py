# -*- coding: utf-8 -*-
"""Playwright 端到端浏览器验证：AI 效果观测中心中历史灰度测试与 A/B 实验深度数据。"""

import os
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
SCREENSHOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../gui-test-screenshots"))
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_playwright_historical_canary_and_ab_observatory():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()

        # 1. 打开首页
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=10000)

        # 2. 切换至 admin 角色
        current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        if current_role != "admin":
            with page.expect_navigation(timeout=10000):
                page.select_option("#demoRoleSelect", "admin")
            page.wait_for_selector("#demoRoleSelect", timeout=10000)
            page.wait_for_timeout(300)

        # 3. 点击进入「AI 效果观测与评测中心」
        analytics_tab_btn = page.locator(".sidebar-btn[data-target='tab-analytics']")
        analytics_tab_btn.click()
        page.wait_for_selector("#tab-analytics", timeout=10000)
        assert page.locator("#tab-analytics").is_visible()
        page.wait_for_timeout(500)

        # -------------------------------------------------------------
        # 4. 验证 功能区二：金丝雀发布监控 (sec-canary)
        # -------------------------------------------------------------
        canary_btn = page.locator("#btnSubViewCanary")
        canary_btn.click()
        page.wait_for_timeout(800)

        # 验证历史灰测履历卡片
        history_card = page.locator("#adminCanaryHistoryCard")
        assert history_card.is_visible(), "adminCanaryHistoryCard not visible"

        # 等待历史履历表格加载出数据
        page.wait_for_selector("#adminCanaryHistoryTable tbody tr", timeout=8000)
        history_rows = page.locator("#adminCanaryHistoryTable tbody tr")
        assert history_rows.count() > 0, "No rows in adminCanaryHistoryTable"

        # 验证脱敏单据流 Scope 筛选器
        scope_select = page.locator("#greySampleScopeSelect")
        assert scope_select.is_visible(), "greySampleScopeSelect not visible"

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "analytics_canary_history_verified.png"), full_page=False)

        # -------------------------------------------------------------
        # 5. 验证 功能区三：A/B 科学实验台 (sec-experiment)
        # -------------------------------------------------------------
        exp_btn = page.locator("#btnSubViewExperiment")
        exp_btn.click()
        page.wait_for_timeout(1000)

        # 验证 A/B 实验列表
        page.wait_for_selector("#analyticsExperimentsBody table", timeout=8000)
        exp_table = page.locator("#analyticsExperimentsBody table")
        assert exp_table.is_visible(), "analyticsExperimentsBody table not visible"

        # 验证选定 A/B 实验深度全景档案卡片
        detail_card = page.locator("#analyticsExpDetailCard")
        assert detail_card.is_visible(), "analyticsExpDetailCard not visible"

        # 切换选择实验 #2（mimo-v3 测试）
        exp_select = page.locator("#analyticsExpSelect")
        assert exp_select.is_visible(), "analyticsExpSelect not visible"
        exp_select.select_option("2")
        page.wait_for_timeout(1000)

        # 验证元数据横幅包含实验名称
        meta_dossier = page.locator("#expMetaDossier")
        assert "mimo-v3" in meta_dossier.inner_text(), "Exp 2 metadata not loaded"

        # 验证组别核心指标对比表
        sbs_table = page.locator("#expSideBySideTable")
        assert sbs_table.is_visible(), "expSideBySideTable not visible"
        sbs_text = page.locator("#expSideBySideBody").inner_text()
        assert "字段提取准确率" in sbs_text, "Accuracy row missing in Side-by-Side table"

        # 验证实验样本单据流明细表加载出了样本数据
        page.wait_for_selector("#expSampleTableBody tr", timeout=8000)
        sample_rows = page.locator("#expSampleTableBody tr")
        assert sample_rows.count() > 0, "No sample rows loaded in expSampleTableBody"

        detail_card.scroll_into_view_if_needed()
        page.wait_for_timeout(400)
        detail_card.screenshot(path=os.path.join(SCREENSHOT_DIR, "analytics_ab_detail_card.png"))

        page.screenshot(path=os.path.join(SCREENSHOT_DIR, "analytics_ab_detail_verified.png"), full_page=False)

        browser.close()
