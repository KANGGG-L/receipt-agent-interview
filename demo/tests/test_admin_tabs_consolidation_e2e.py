# -*- coding: utf-8 -*-
"""
管理端导航与 AI 效果观测与评测中心单页大盘端到端 (E2E) 测试套件
Admin Tabs Consolidation & Single-Page Unified Dashboard Verification

覆盖范围:
Phase 1: RBAC 角色权限与侧边栏动态导航隔离
  - admin 角色:
    - 侧边栏小节 #adminSidebarSection 可见且包含 "系统与算法工程"
    - 系统与引擎配置入口 #adminEngineBtn 可见且包含 "系统与引擎配置"
    - AI 效果观测与评测中心入口 #analyticsBoardBtn 可见且包含 "AI 效果观测与评测中心"
    - 旧独立入口 #goldenBoardBtn 和 #evalsetReviewBtn 保持不可见 (隐藏)
  - staff 角色:
    - #adminSidebarSection, #adminEngineBtn, #analyticsBoardBtn 均不可见
  - owner 角色:
    - #adminSidebarSection, #adminEngineBtn, #analyticsBoardBtn 均不可见

Phase 2: AI 效果观测与评测中心四大独立功能区 (Segmented Control) 统一渲染
  - 切换回 admin 角色并点击 #analyticsBoardBtn
  - #tab-analytics 激活且可见
  - 分段选项卡 4 功能区 (sec-telemetry, sec-canary, sec-experiment, sec-eval) 独立隔离校验
  - 全部核心卡片/组件加载并存在于对应功能区 DOM 中:
    1. 全量埋点事件分布 (#analyticsEventDistBody) 与 挽回/点踩指标 (#analyticsRecoveryBody)
    2. 金丝雀灰度现状与脱敏单据观测 (#analyticsGreyBody, #adminGreySamplesBody)
    3. A/B 实验与显著性检验 (包含 #analyticsExperimentsBody 与 #pvalueCardsContainer)
    4. 基准资产看板与 GT 确权 (包含 #goldenBoardBody 与 #gtEvalsetStats, a[href="/evalset"])
  - 自动生成高清断言截屏:
    - demo/gui-test-screenshots/admin_tab_sidebar_layout.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview1_telemetry.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview2_canary.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview3_experiment.png
    - demo/gui-test-screenshots/admin_tab_analytics_subview4_eval.png
    - demo/gui-test-screenshots/admin_tab_analytics_unified.png
    - demo/gui-test-screenshots/staff_sidebar_no_admin.png
"""

import os
import sys
import time
import socket
import subprocess
from urllib.parse import urlparse
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DEMO_DIR = os.path.join(ROOT_DIR, "demo")
SCREENSHOT_DIR = os.path.join(DEMO_DIR, "gui-test-screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def is_service_reachable(timeout=1.0):
    """检测本地 15010 端口服务是否连通"""
    parsed = urlparse(BASE_URL)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout):
            return True
    except OSError:
        return False


def ensure_server():
    """确保本地 Demo 服务正在运行，如未运行则拉起后台进程"""
    if is_service_reachable():
        return None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", "15010"],
        cwd=DEMO_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    start = time.time()
    while time.time() - start < 15:
        if is_service_reachable():
            return proc
        time.sleep(0.4)
    return proc


def switch_demo_role(page, target_role):
    """切换角色并通过 reload 重新初始化权限状态"""
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


def test_admin_tabs_consolidation_e2e():
    """Pytest 入口：执行全量管理端导航合并与单页大盘 E2E 测试"""
    run_admin_tabs_consolidation_e2e()


def run_admin_tabs_consolidation_e2e():
    """管理端合并与 AI 观测大盘 E2E 测试完整流程"""
    server_proc = ensure_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()

            # ------------------------------------------------------------
            # Phase 1: RBAC and Sidebar Navigation
            # ------------------------------------------------------------
            print("\n[Phase 1] 校验 RBAC 角色权限与侧边栏动态导航...")
            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_selector("#demoRoleSelect", timeout=12000)

            # 1.1 admin 角色检验
            switch_demo_role(page, "admin")
            admin_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert admin_role_val == "admin", f"Expected admin role, got {admin_role_val}"

            # 验证 #adminSidebarSection 可见且包含 '系统与算法工程'
            admin_section = page.locator("#adminSidebarSection")
            assert admin_section.is_visible(), "adminSidebarSection should be visible for admin role"
            admin_section_text = admin_section.inner_text().strip()
            assert "系统与算法工程" in admin_section_text, (
                f"Expected '系统与算法工程' in #adminSidebarSection, got '{admin_section_text}'"
            )

            # 验证 #adminEngineBtn 可见且包含 '系统与引擎配置'
            admin_engine_btn = page.locator("#adminEngineBtn")
            assert admin_engine_btn.is_visible(), "adminEngineBtn should be visible for admin role"
            admin_engine_text = admin_engine_btn.inner_text().strip()
            assert "系统与引擎配置" in admin_engine_text, (
                f"Expected '系统与引擎配置' in #adminEngineBtn, got '{admin_engine_text}'"
            )

            # 验证 #analyticsBoardBtn 可见且包含 'AI 效果观测与评测中心'
            analytics_btn = page.locator("#analyticsBoardBtn")
            assert analytics_btn.is_visible(), "analyticsBoardBtn should be visible for admin role"
            analytics_btn_text = analytics_btn.inner_text().strip()
            assert "AI 效果观测与评测中心" in analytics_btn_text, (
                f"Expected 'AI 效果观测与评测中心' in #analyticsBoardBtn, got '{analytics_btn_text}'"
            )

            # 验证 #goldenBoardBtn 和 #evalsetReviewBtn 独立入口保持不可见
            golden_btn = page.locator("#goldenBoardBtn")
            evalset_btn = page.locator("#evalsetReviewBtn")
            assert not golden_btn.is_visible(), "goldenBoardBtn must NOT be visible (consolidated into unified dashboard)"
            assert not evalset_btn.is_visible(), "evalsetReviewBtn must NOT be visible (consolidated into unified dashboard)"

            # 保存 admin 侧边栏布局截屏
            shot_sidebar = os.path.join(SCREENSHOT_DIR, "admin_tab_sidebar_layout.png")
            page.screenshot(path=shot_sidebar, full_page=False)
            print(f"Saved: {shot_sidebar}")

            # 1.2 staff 角色检验
            print("Switching to staff role...")
            switch_demo_role(page, "staff")
            staff_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert staff_role_val == "staff", f"Expected staff role, got {staff_role_val}"

            assert not page.locator("#adminSidebarSection").is_visible(), (
                "adminSidebarSection must NOT be visible for staff role"
            )
            assert not page.locator("#adminEngineBtn").is_visible(), (
                "adminEngineBtn must NOT be visible for staff role"
            )
            assert not page.locator("#analyticsBoardBtn").is_visible(), (
                "analyticsBoardBtn must NOT be visible for staff role"
            )
            assert not page.locator("#goldenBoardBtn").is_visible(), (
                "goldenBoardBtn must NOT be visible for staff role"
            )
            assert not page.locator("#evalsetReviewBtn").is_visible(), (
                "evalsetReviewBtn must NOT be visible for staff role"
            )

            # 保存 staff 侧边栏截屏 (无管理入口)
            shot_staff = os.path.join(SCREENSHOT_DIR, "staff_sidebar_no_admin.png")
            page.screenshot(path=shot_staff, full_page=False)
            print(f"Saved: {shot_staff}")

            # 1.3 owner 角色检验
            print("Switching to owner role...")
            switch_demo_role(page, "owner")
            owner_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert owner_role_val == "owner", f"Expected owner role, got {owner_role_val}"

            assert not page.locator("#adminSidebarSection").is_visible(), (
                "adminSidebarSection must NOT be visible for owner role"
            )
            assert not page.locator("#adminEngineBtn").is_visible(), (
                "adminEngineBtn must NOT be visible for owner role"
            )
            assert not page.locator("#analyticsBoardBtn").is_visible(), (
                "analyticsBoardBtn must NOT be visible for owner role"
            )
            assert not page.locator("#goldenBoardBtn").is_visible(), (
                "goldenBoardBtn must NOT be visible for owner role"
            )
            assert not page.locator("#evalsetReviewBtn").is_visible(), (
                "evalsetReviewBtn must NOT be visible for owner role"
            )

            # ------------------------------------------------------------
            # Phase 2: AI 效果观测与评测中心四大独立功能区 (Segmented Subviews)
            # ------------------------------------------------------------
            print("\n[Phase 2] 校验 AI 效果观测与评测中心四大独立功能区 (Segmented Control)...")
            switch_demo_role(page, "admin")

            # 点击 AI 效果观测与评测中心入口
            page.locator("#analyticsBoardBtn").click()
            page.wait_for_selector("#tab-analytics", timeout=10000)

            # 验证 #tab-analytics 处于激活且可见状态
            tab_analytics = page.locator("#tab-analytics")
            assert tab_analytics.is_visible(), "#tab-analytics should be visible after clicking analyticsBoardBtn"
            tab_classes = page.eval_on_selector("#tab-analytics", "el => el.className")
            assert "active" in tab_classes, f"#tab-analytics should have 'active' class, got '{tab_classes}'"

            # 校验四大独立功能区分段导航栏 (Segmented Bar)
            segmented_bar = page.locator(".analytics-segmented-bar")
            assert segmented_bar.is_visible(), ".analytics-segmented-bar must be visible"
            seg_buttons = segmented_bar.locator(".analytics-segment-btn")
            assert seg_buttons.count() == 4, f"Expected 4 segment buttons, got {seg_buttons.count()}"

            # 等待各数据接口异步拉取完成并由前端渲染到 DOM (初始功能区 1 埋点就绪)
            page.wait_for_function(
                """() => {
                    const dist = document.getElementById('analyticsEventDistBody');
                    const rec = document.getElementById('analyticsRecoveryBody');
                    if (!dist || !rec) return false;
                    return !dist.innerText.includes('加载中') &&
                           !rec.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            # ------------------------------------------------------------
            # 2.1 独立功能区一：线上体验感知与埋点监控 (#sec-telemetry)
            # ------------------------------------------------------------
            print("校验独立功能区 1: 线上体验感知与埋点监控...")
            sec_telemetry = page.locator("#sec-telemetry")
            sec_canary = page.locator("#sec-canary")
            sec_experiment = page.locator("#sec-experiment")
            sec_eval = page.locator("#sec-eval")

            # 默认处于功能区一，其它三区保持隐藏 (独立隔离)
            assert sec_telemetry.is_visible(), "sec-telemetry must be visible by default"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 1 is active"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 1 is active"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 1 is active"

            # 验证功能区一内部组件
            event_dist = page.locator("#analyticsEventDistBody")
            assert event_dist.is_visible(), "#analyticsEventDistBody must be visible in view 1"
            recovery = page.locator("#analyticsRecoveryBody")
            assert recovery.is_visible(), "#analyticsRecoveryBody must be visible in view 1"

            shot_subview1 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview1_telemetry.png")
            page.screenshot(path=shot_subview1, full_page=False)
            print(f"Saved: {shot_subview1}")

            # ------------------------------------------------------------
            # 2.2 独立功能区二：金丝雀发布监控 (#sec-canary)
            # ------------------------------------------------------------
            print("校验独立功能区 2: 金丝雀发布监控...")
            page.locator("#btnSubViewCanary").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const grey = document.getElementById('analyticsGreyBody');
                    return grey && !grey.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 2 is active"
            assert sec_canary.is_visible(), "sec-canary must be visible when switched to view 2"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 2 is active"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 2 is active"

            # 验证功能区二内部组件 (纯粹金丝雀灰度，解耦 A/B 实验)
            grey_status = page.locator("#analyticsGreyBody")
            assert grey_status.is_visible(), "#analyticsGreyBody must be visible in view 2"
            assert page.locator("#adminGreySamplesTable").is_visible() or page.locator("#adminGreySamplesBody").is_visible()
            # 验证 #analyticsExperimentsBody 不在 #sec-canary 内
            assert sec_canary.locator("#analyticsExperimentsBody").count() == 0, "#analyticsExperimentsBody must NOT be inside #sec-canary"

            shot_subview2 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview2_canary.png")
            page.screenshot(path=shot_subview2, full_page=False)
            print(f"Saved: {shot_subview2}")

            # ------------------------------------------------------------
            # 2.3 独立功能区三：A/B 科学实验台 (#sec-experiment)
            # ------------------------------------------------------------
            print("校验独立功能区 3: A/B 科学实验台...")
            page.locator("#btnSubViewExperiment").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const ab = document.getElementById('analyticsExperimentsBody');
                    const pval = document.getElementById('pvalueCardsContainer');
                    if (!ab || !pval) return false;
                    return !ab.innerText.includes('加载中') && pval.children.length > 0;
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 3 is active"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 3 is active"
            assert sec_experiment.is_visible(), "sec-experiment must be visible when switched to view 3"
            assert not sec_eval.is_visible(), "sec-eval must NOT be visible when view 3 is active"

            ab_body = page.locator("#analyticsExperimentsBody")
            assert ab_body.is_visible(), "#analyticsExperimentsBody must be visible in view 3"
            pvalue_container = page.locator("#pvalueCardsContainer")
            assert pvalue_container.is_visible(), "#pvalueCardsContainer must be visible in view 3"

            shot_subview3 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview3_experiment.png")
            page.screenshot(path=shot_subview3, full_page=False)
            print(f"Saved: {shot_subview3}")

            # ------------------------------------------------------------
            # 2.4 独立功能区四：基准资产与 GT 确权 (#sec-eval)
            # ------------------------------------------------------------
            print("校验独立功能区 4: 基准资产与 GT 确权...")
            page.locator("#btnSubViewEval").click()
            page.wait_for_timeout(300)
            page.wait_for_function(
                """() => {
                    const golden = document.getElementById('goldenBoardBody');
                    const gt = document.getElementById('gtEvalsetStats');
                    if (!golden || !gt) return false;
                    return !golden.innerText.includes('加载中') && !gt.innerText.includes('加载中');
                }""",
                timeout=15000
            )

            assert not sec_telemetry.is_visible(), "sec-telemetry must NOT be visible when view 4 is active"
            assert not sec_canary.is_visible(), "sec-canary must NOT be visible when view 4 is active"
            assert not sec_experiment.is_visible(), "sec-experiment must NOT be visible when view 4 is active"
            assert sec_eval.is_visible(), "sec-eval must be visible when switched to view 4"

            # 验证功能区四内部组件
            golden_body = page.locator("#goldenBoardBody")
            assert golden_body.is_visible(), "#goldenBoardBody must be visible in view 4"
            gt_stats = page.locator("#gtEvalsetStats")
            assert gt_stats.is_visible(), "#gtEvalsetStats must be visible in view 4"
            evalset_links = page.locator("#sec-eval a[href='/evalset']")
            assert evalset_links.count() >= 1, "Expected GT review link inside #sec-eval"
            assert evalset_links.first.is_visible(), "GT review link in #sec-eval must be visible"

            shot_subview4 = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_subview4_eval.png")
            page.screenshot(path=shot_subview4, full_page=False)
            print(f"Saved: {shot_subview4}")

            # 切回功能区 1 保存统一总览截屏
            page.locator("#btnSubViewTelemetry").click()
            page.wait_for_timeout(300)
            assert sec_telemetry.is_visible(), "sec-telemetry should be visible after switching back"

            shot_analytics = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_unified.png")
            page.screenshot(path=shot_analytics, full_page=False)
            print(f"Saved: {shot_analytics}")

            shot_segmented = os.path.join(SCREENSHOT_DIR, "admin_tab_analytics_segmented.png")
            page.screenshot(path=shot_segmented, full_page=False)
            print(f"Saved: {shot_segmented}")

            browser.close()
            print("\nAll 4 independent functional areas checks passed successfully.")

    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()


if __name__ == "__main__":
    run_admin_tabs_consolidation_e2e()
