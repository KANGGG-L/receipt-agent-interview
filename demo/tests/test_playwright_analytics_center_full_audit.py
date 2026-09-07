# -*- coding: utf-8 -*-
"""
Exhaustive Playwright Audit of EVERY component in 'AI 效果观测与评测中心' (tab-analytics):
1. Segmented control switching between all 4 subviews
2. Subview 01: 埋点分布与挽回 (Tenant selector, refresh button, event distribution table, recovery stat cards)
3. Subview 02: 金丝雀发布监控 (Grey status, metrics bar, limit notice, samples table, modal inspection, deep-link button)
4. Subview 03: A/B 科学实验台 (Experiments table, refresh button, pvalue experiment selector switching, pvalue z-test cards)
5. Subview 04: 基准资产与确权 (GT stats, GT workbench link, golden scope filter switching, golden table rows, actions)
"""
import os
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010")
SCREENSHOT_DIR = "demo/gui-test-screenshots"


def test_analytics_center_every_component_audit():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # -------------------------------------------------------------
        # Step 0: 登录并切入 AI 效果观测与评测中心
        # -------------------------------------------------------------
        page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=10000)
        page.evaluate("() => { if (typeof setRole === 'function') setRole('admin'); }")
        page.wait_for_timeout(500)

        page.locator('.sidebar-btn[data-target="tab-analytics"]').click()
        page.wait_for_timeout(300)

        # -------------------------------------------------------------
        # Step 1: 功能区 01 - 埋点分布与挽回 (#sec-telemetry)
        # -------------------------------------------------------------
        print("=== 走查功能区 01: 埋点分布与挽回 ===")
        page.locator("#btnSubViewTelemetry").click()
        page.wait_for_timeout(500)

        # 等待事件分布和挽回指标卡片加载完毕
        page.wait_for_function(
            """() => {
                const dist = document.getElementById('analyticsEventDistBody');
                const rec = document.getElementById('analyticsRecoveryBody');
                return dist && rec && !dist.innerText.includes('加载中') && !rec.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        # 1.1 检查租户选择器
        tenant_sel = page.locator("#analyticsTenantSelect")
        assert tenant_sel.is_visible(), "#analyticsTenantSelect must be visible"
        assert tenant_sel.locator("option").count() >= 1, "Tenant select must have at least 'all' option"
        # 切换租户并触发刷新
        tenant_sel.select_option("all")
        page.wait_for_function(
            """() => {
                const dist = document.getElementById('analyticsEventDistBody');
                return dist && !dist.innerText.includes('加载中') && dist.querySelector('table');
            }""",
            timeout=10000
        )

        # 1.2 检查全量埋点事件分布表格
        dist_body = page.locator("#analyticsEventDistBody")
        assert dist_body.is_visible()
        table_rows = dist_body.locator("table tbody tr")
        print(f"Telemetry event distribution rows: {table_rows.count()}")
        assert table_rows.count() > 0, "Telemetry event distribution table must have rows"

        # 1.3 检查挽回与点踩指标小卡片
        rec_body = page.locator("#analyticsRecoveryBody")
        assert rec_body.is_visible()
        print("Telemetry recovery metrics content loaded successfully")

        # 截屏功能区 01
        shot_01 = os.path.join(SCREENSHOT_DIR, "audit_01_telemetry_board.png")
        page.screenshot(path=shot_01, full_page=False)
        print(f"Saved: {shot_01}")

        # -------------------------------------------------------------
        # Step 2: 功能区 02 - 金丝雀发布监控 (#sec-canary)
        # -------------------------------------------------------------
        print("=== 走查功能区 02: 金丝雀发布监控 ===")
        page.locator("#btnSubViewCanary").click()
        page.wait_for_timeout(500)

        # 等待灰测样本表格与指标加载
        page.wait_for_function(
            """() => {
                const tbody = document.getElementById('adminGreySamplesBody');
                const mTotal = document.getElementById('mGreyTotal');
                return tbody && mTotal && !tbody.innerText.includes('拉取数据') && !tbody.innerText.includes('正在拉取') && mTotal.innerText !== '0';
            }""",
            timeout=15000
        )

        # 2.1 检查指标卡
        m_total = int(page.locator("#mGreyTotal").inner_text() or "0")
        print(f"Canary total samples reported: {m_total}")
        assert m_total > 0, "Canary total samples must be > 0"

        # 2.2 检查样本抽样提示
        notice = page.locator("#adminGreySampleLimitNotice")
        assert notice.is_visible(), "Sample limit notice should be visible when samples > 100"
        print(f"Notice text: {notice.inner_text()}")

        # 2.3 检查脱敏单据流表格无截断
        sample_rows = page.locator("#adminGreySamplesBody tr")
        print(f"Rendered canary sample rows: {sample_rows.count()}")
        assert sample_rows.count() > 0

        # 2.4 测试单据脱敏详情弹窗
        first_detail_btn = sample_rows.first.locator("button:has-text('查看脱敏解析')")
        assert first_detail_btn.is_visible(), "Detail button must be visible"
        first_detail_btn.click()
        page.wait_for_timeout(400)

        modal = page.locator("#adminGreySampleModal")
        assert modal.is_visible(), "adminGreySampleModal must open on click"
        modal_title = page.locator("#adminGreyModalTitle").inner_text()
        print(f"Modal opened: {modal_title}")

        # 截屏弹窗
        shot_modal = os.path.join(SCREENSHOT_DIR, "audit_02_canary_detail_modal.png")
        page.screenshot(path=shot_modal, full_page=False)
        print(f"Saved: {shot_modal}")

        # 关闭弹窗
        modal.locator(".modal-close, button:has-text('关闭')").first.click()
        page.wait_for_timeout(300)
        assert not modal.is_visible(), "Modal must be closed"

        # 2.5 测试“刷新脱敏单据流”按钮
        refresh_canary_btn = page.locator("#sec-canary button:has-text('刷新脱敏单据流')")
        assert refresh_canary_btn.is_visible()
        refresh_canary_btn.click()
        page.wait_for_function(
            """() => {
                const tbody = document.getElementById('adminGreySamplesBody');
                return tbody && !tbody.innerText.includes('正在拉取') && tbody.children.length > 0;
            }""",
            timeout=10000
        )

        # 截屏功能区 02
        shot_02 = os.path.join(SCREENSHOT_DIR, "audit_02_canary_board.png")
        page.screenshot(path=shot_02, full_page=False)
        print(f"Saved: {shot_02}")

        # -------------------------------------------------------------
        # Step 3: 功能区 03 - A/B 科学实验台 (#sec-experiment)
        # -------------------------------------------------------------
        print("=== 走查功能区 03: A/B 科学实验台 ===")
        page.locator("#btnSubViewExperiment").click()
        page.wait_for_timeout(500)

        page.wait_for_function(
            """() => {
                const ab = document.getElementById('analyticsExperimentsBody');
                const sel = document.getElementById('pvalueExperimentSelect');
                const pval = document.getElementById('pvalueCardsContainer');
                return ab && sel && pval && !ab.innerText.includes('加载中') && pval.children.length > 0;
            }""",
            timeout=15000
        )

        # 3.1 检查实验概览表格
        ab_table = page.locator("#analyticsExperimentsBody table")
        assert ab_table.is_visible(), "A/B experiments overview table must be visible"
        ab_rows = ab_table.locator("tbody tr")
        print(f"A/B experiment rows: {ab_rows.count()}")
        assert ab_rows.count() >= 2, "Should have at least 2 experiments (#1, #2)"

        # 3.2 检查“刷新实验数据”按钮
        refresh_exp_btn = page.locator("#sec-experiment button:has-text('刷新实验数据')")
        assert refresh_exp_btn.is_visible()
        refresh_exp_btn.click()
        page.wait_for_timeout(400)

        # 3.3 检查显著性实验选择器切换与卡片联动
        pval_sel = page.locator("#pvalueExperimentSelect")
        assert pval_sel.is_visible()
        assert pval_sel.input_value() == "2", "Default selected should be 2"

        pval_cards = page.locator("#pvalueCardsContainer")
        assert "0.1360" in pval_cards.inner_text(), "Exp #2 p-value 0.1360 must be displayed"

        # 切换到实验 1
        pval_sel.select_option("1")
        page.wait_for_function(
            """() => {
                const pval = document.getElementById('pvalueCardsContainer');
                return pval && !pval.innerText.includes('0.1360');
            }""",
            timeout=5000
        )
        assert pval_sel.input_value() == "1", "Select must hold value 1"
        assert "无法判定" in pval_cards.inner_text()

        # 切回实验 2
        pval_sel.select_option("2")
        page.wait_for_function(
            """() => {
                const pval = document.getElementById('pvalueCardsContainer');
                return pval && pval.innerText.includes('0.1360');
            }""",
            timeout=5000
        )
        assert pval_sel.input_value() == "2", "Select must hold value 2"

        # 截屏功能区 03
        shot_03 = os.path.join(SCREENSHOT_DIR, "audit_03_experiment_board.png")
        page.screenshot(path=shot_03, full_page=False)
        print(f"Saved: {shot_03}")

        # -------------------------------------------------------------
        # Step 4: 功能区 04 - 基准资产与确权 (#sec-eval)
        # -------------------------------------------------------------
        print("=== 走查功能区 04: 基准资产与确权 ===")
        page.locator("#btnSubViewEval").click()
        page.wait_for_timeout(500)

        page.wait_for_function(
            """() => {
                const stats = document.getElementById('gtEvalsetStats');
                const gbody = document.getElementById('goldenBoardBody');
                return stats && gbody && !stats.innerText.includes('加载中') && !gbody.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        # 4.1 检查确权进度统计与独立确权台深链
        evalset_stats = page.locator("#gtEvalsetStats")
        assert evalset_stats.is_visible()
        print(f"GT evalset stats: {evalset_stats.inner_text()}")
        assert "已确权" in evalset_stats.inner_text()

        evalset_link = page.locator("#sec-eval a.btn[href='/evalset']")
        assert evalset_link.is_visible(), "Link to /evalset must be visible"

        # 4.2 检查黄金基准集范围选择器过滤
        scope_sel = page.locator("#goldenScopeSelect")
        assert scope_sel.is_visible()
        golden_rows = page.locator("#goldenBoardBody tr")
        total_all_rows = golden_rows.count()
        print(f"Golden board 'all' rows count: {total_all_rows}")
        assert total_all_rows > 0

        def wait_golden_loaded():
            page.wait_for_function(
                """() => {
                    const gbody = document.getElementById('goldenBoardBody');
                    return gbody && !gbody.innerText.includes('加载中');
                }""",
                timeout=10000
            )

        # 切换范围至仅基准集成员
        scope_sel.select_option("golden")
        wait_golden_loaded()
        assert scope_sel.input_value() == "golden"
        golden_member_rows = page.locator("#goldenBoardBody tr").count()
        print(f"Golden member rows: {golden_member_rows}")
        assert golden_member_rows > 0

        # 切换范围至仅已 GT 确权
        scope_sel.select_option("gt_confirmed")
        wait_golden_loaded()
        assert scope_sel.input_value() == "gt_confirmed"
        confirmed_rows = page.locator("#goldenBoardBody tr").count()
        print(f"GT confirmed rows: {confirmed_rows}")
        assert confirmed_rows >= 5, "At least 5 confirmed GT samples should appear"

        # 切换范围至待确权候选池
        scope_sel.select_option("gt_pending")
        wait_golden_loaded()
        assert scope_sel.input_value() == "gt_pending"

        # 切回全部
        scope_sel.select_option("all")
        wait_golden_loaded()
        assert scope_sel.input_value() == "all"
        assert page.locator("#goldenBoardBody tr").count() == total_all_rows

        # 4.3 检查基准集刷新按钮
        refresh_golden_btn = page.locator("#sec-eval button:has-text('刷新')")
        assert refresh_golden_btn.is_visible()
        refresh_golden_btn.click()
        page.wait_for_timeout(400)

        # 截屏功能区 04
        shot_04 = os.path.join(SCREENSHOT_DIR, "audit_04_golden_eval_board.png")
        page.screenshot(path=shot_04, full_page=False)
        print(f"Saved: {shot_04}")

        print("=== 全量 4 大功能区所有组件走查 100% 成功！ ===")
        browser.close()
