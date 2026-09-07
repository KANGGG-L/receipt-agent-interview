# -*- coding: utf-8 -*-
"""
真实浏览器 Playwright E2E 回归测试：系统与引擎配置中的 A/B 科学实验编排高级抽屉及闭环深链
Verifies:
1. 系统与引擎配置 (#tab-engine) 包含并列的双抽屉结构：
   - Drawer 1 (#adminGreyDrawer): 标题包含 "高级配置 · 金丝雀灰度发布 (Canary Rollout)"
   - Drawer 2 (#adminExperimentDrawer): 标题包含 "高级配置 · A/B 科学实验编排 (A/B Experimentation)"
2. 点击展开 #adminExperimentDrawer，抽屉移除 .collapsed
3. 下拉列表 #adminExpSelect 成功加载实验，实验详情卡片 #adminExpDetailCard 可见
4. 核心实验要素字段非空：
   - 实验假设 #adminExpHypothesis
   - 主判定指标 #adminExpMetric
   - 分流配比 #adminExpTraffic
   - 最小样本量 #adminExpMinSample
5. 对照组 vs 实验组模型对比卡片正常渲染 (#adminExpCtrlModel 与 #adminExpTreatModel 可见且非空)
6. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_drawer.png
7. 闭环深链验证：
   - 点击抽屉底部的 "前往 A/B 科学实验台查看 p 值指标看板" 链接
   - 页面平滑跳转至 AI 效果观测与评测中心 (#tab-analytics.active)
   - 激活的功能区为 A/B 科学实验台 (#sec-experiment)
   - 标题包含 "A/B 科学实验台"
   - 实验数据表格 (#analyticsExperimentsBody) 与 p 值统计卡片 (#pvalueCardsContainer) 成功渲染可见
8. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_deep_link_land.png
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


def switch_demo_role(page, target_role="admin"):
    """切换角色并通过 reload 重新初始化权限状态"""
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


def test_playwright_ab_experiment_drawer():
    """执行 A/B 科学实验高级抽屉及闭环深链的 Playwright 真实浏览器端到端核验"""
    server_proc = ensure_server()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 960})
            page = context.new_page()

            page.goto(BASE_URL, wait_until="domcontentloaded")
            page.wait_for_selector("#demoRoleSelect", timeout=12000)

            # 1. 切换为 admin 角色
            switch_demo_role(page, "admin")
            admin_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
            assert admin_role_val == "admin", f"Expected admin role, got {admin_role_val}"

            # 2. 导航至系统与引擎配置 (click #adminEngineBtn)
            print("\n[Step 1] 点击 #adminEngineBtn 进入系统与引擎配置...")
            page.locator("#adminEngineBtn").click()
            page.wait_for_selector("#tab-engine", timeout=10000)
            assert page.locator("#tab-engine").is_visible(), "#tab-engine should be visible"

            # 3. 断言两个高级抽屉均存在且标题正确
            print("[Step 2] 校验金丝雀灰度发布抽屉与 A/B 科学实验编排抽屉...")
            grey_drawer = page.locator("#adminGreyDrawer")
            exp_drawer = page.locator("#adminExperimentDrawer")

            assert grey_drawer.is_visible(), "#adminGreyDrawer should exist and be visible"
            assert exp_drawer.is_visible(), "#adminExperimentDrawer should exist and be visible"

            grey_title_text = grey_drawer.locator(".engine-drawer-title").inner_text().strip()
            assert "高级配置 · 金丝雀灰度发布 (Canary Rollout)" in grey_title_text, (
                f"Expected canary title in #adminGreyDrawer, got '{grey_title_text}'"
            )

            exp_title_text = exp_drawer.locator(".engine-drawer-title").inner_text().strip()
            assert "高级配置 · A/B 科学实验编排 (A/B Experimentation)" in exp_title_text, (
                f"Expected A/B title in #adminExperimentDrawer, got '{exp_title_text}'"
            )

            # 4. 点击 #adminExperimentDrawer 头部展开抽屉 (移除 .collapsed)
            print("[Step 3] 展开 #adminExperimentDrawer 抽屉...")
            exp_drawer_header = exp_drawer.locator(".engine-drawer-header")
            exp_drawer_header.click()
            page.wait_for_timeout(300)

            drawer_classes = exp_drawer.get_attribute("class") or ""
            assert "collapsed" not in drawer_classes, (
                f"#adminExperimentDrawer should not be collapsed after click, got classes: '{drawer_classes}'"
            )

            # 5. 等待 #adminExpSelect 被实验选项填充
            print("[Step 4] 等待实验下拉列表 #adminExpSelect 加载...")
            page.wait_for_function(
                """() => {
                    const sel = document.getElementById('adminExpSelect');
                    return sel && sel.options.length > 0 && sel.options[0].value !== '';
                }""",
                timeout=15000
            )

            # 6. 断言实验详情面板 #adminExpDetailCard 可见
            print("[Step 5] 校验实验详情面板及关键要素...")
            detail_card = page.locator("#adminExpDetailCard")
            assert detail_card.is_visible(), "#adminExpDetailCard should be visible"

            # 7. 断言假设、指标、分流与样本量具有非空内容
            hyp_text = page.locator("#adminExpHypothesis").inner_text().strip()
            metric_text = page.locator("#adminExpMetric").inner_text().strip()
            traffic_text = page.locator("#adminExpTraffic").inner_text().strip()
            sample_text = page.locator("#adminExpMinSample").inner_text().strip()

            assert hyp_text and hyp_text != "—", f"Expected non-empty hypothesis, got '{hyp_text}'"
            assert metric_text and metric_text != "—", f"Expected non-empty metric, got '{metric_text}'"
            assert traffic_text and traffic_text != "—", f"Expected non-empty traffic, got '{traffic_text}'"
            assert sample_text and sample_text != "—", f"Expected non-empty min sample, got '{sample_text}'"

            # 8. 断言 Control vs Treatment 模型对比卡片可见且非空
            ctrl_model = page.locator("#adminExpCtrlModel")
            treat_model = page.locator("#adminExpTreatModel")
            assert ctrl_model.is_visible(), "#adminExpCtrlModel should be visible"
            assert treat_model.is_visible(), "#adminExpTreatModel should be visible"

            ctrl_model_text = ctrl_model.inner_text().strip()
            treat_model_text = treat_model.inner_text().strip()
            assert ctrl_model_text, "Expected non-empty Control model text"
            assert treat_model_text, "Expected non-empty Treatment model text"

            # 9. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_drawer.png
            shot_drawer = os.path.join(SCREENSHOT_DIR, "admin_ab_experiment_drawer.png")
            page.screenshot(path=shot_drawer, full_page=False)
            print(f"Saved drawer screenshot: {shot_drawer}")

            # 10. 测试闭环深链 (Deep Link)
            print("[Step 6] 校验由抽屉直达 A/B 科学实验台的深链 (Deep Link)...")
            deep_link = exp_drawer.locator("a", has_text="前往 A/B 科学实验台查看 p 值指标看板")
            assert deep_link.is_visible(), "Deep link in #adminExperimentDrawer should be visible"
            deep_link.click()

            # 等待 #tab-analytics 变为 active
            page.wait_for_selector("#tab-analytics.active", timeout=10000)
            assert page.locator("#tab-analytics.active").is_visible(), "#tab-analytics.active should be visible"

            # 断言 #sec-experiment 可见
            sec_experiment = page.locator("#sec-experiment")
            page.wait_for_selector("#sec-experiment", state="visible", timeout=10000)
            assert sec_experiment.is_visible(), "#sec-experiment should be visible"

            # 断言标题包含 'A/B 科学实验台'
            exp_title = sec_experiment.locator(".analytics-section-title")
            assert exp_title.is_visible(), ".analytics-section-title in #sec-experiment should be visible"
            assert "A/B 科学实验台" in exp_title.inner_text(), (
                f"Expected 'A/B 科学实验台' in title, got '{exp_title.inner_text()}'"
            )

            # 等待实验指标与 p-value 卡片加载
            page.wait_for_function(
                """() => {
                    const ab = document.getElementById('analyticsExperimentsBody');
                    const pval = document.getElementById('pvalueCardsContainer');
                    return ab && pval && !ab.innerText.includes('加载中') && pval.children.length > 0;
                }""",
                timeout=15000
            )

            # 断言核心实验组件可见
            assert page.locator("#analyticsExperimentsBody").is_visible(), "#analyticsExperimentsBody should be visible"
            assert page.locator("#pvalueCardsContainer").is_visible(), "#pvalueCardsContainer should be visible"

            # 11. 截图保存: demo/gui-test-screenshots/admin_ab_experiment_deep_link_land.png
            shot_landing = os.path.join(SCREENSHOT_DIR, "admin_ab_experiment_deep_link_land.png")
            page.screenshot(path=shot_landing, full_page=False)
            print(f"Saved deep link landing screenshot: {shot_landing}")

            browser.close()
            print("\nPlaywright A/B Experiment Drawer E2E test passed successfully.")

    finally:
        if server_proc:
            server_proc.terminate()
            server_proc.wait()


if __name__ == "__main__":
    test_playwright_ab_experiment_drawer()
