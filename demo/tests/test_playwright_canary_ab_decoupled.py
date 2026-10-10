# -*- coding: utf-8 -*-
"""
真实浏览器 Playwright E2E 回归套件：金丝雀灰度发布与 A/B 科学实验台彻底解耦核验
Verifies:
1. 系统与引擎配置中的灰度高级抽屉仅包含金丝雀发布 (Canary Rollout)，不含 A/B 实验干扰
2. AI 效果观测与评测中心 4 功能区 (sec-telemetry, sec-canary, sec-experiment, sec-eval) 独立切换
3. 金丝雀发布监控区与 A/B 科学实验台各自闭环与解耦，不互相污染

服务自举统一经 `playwright_live_server` 夹具（见 conftest.py 与 `_playwright_server.py`）：
服务必然经 `_playwright_serve.py` 启动器拉起并锁定域名 DNS 解析。此前本文件自带一套
`ensure_server()` 直接 `-m uvicorn` 起服务（未锁定 DNS），是「文件名顺序决定谁先起服务」
的顺序相关假失败根因之一，故一并收敛到唯一自举路径。
"""

import os
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("PW_TEST_BASE_URL", os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010"))
SCREENSHOT_DIR = os.environ.get("GUI_SCREENSHOT_DIR") or os.environ.get("ARTIFACTS_DIR") or os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../gui-test-screenshots"))
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def switch_demo_role(page, target_role="admin"):
    """切换角色并通过 reload 重新初始化权限状态"""
    current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
    if current_role == target_role:
        return
    with page.expect_navigation(timeout=15000):
        page.select_option("#demoRoleSelect", target_role)
    page.wait_for_selector("#demoRoleSelect", timeout=10000)
    page.wait_for_timeout(400)


@pytest.mark.usefixtures("playwright_live_server")
def test_playwright_canary_ab_decoupled():
    """执行金丝雀灰度与 A/B 实验解耦的 Playwright E2E 完整回归"""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 960})
        page = context.new_page()

        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=12000)

        # 切换为 admin 角色
        switch_demo_role(page, "admin")
        admin_role_val = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        assert admin_role_val == "admin", f"Expected admin role, got {admin_role_val}"

        # ------------------------------------------------------------
        # Test 1: Verify Engine Drawer (系统与引擎配置 -> 金丝雀灰度抽屉)
        # ------------------------------------------------------------
        print("\n[Test 1] 校验系统与引擎配置中的金丝雀灰度抽屉纯净化...")
        page.locator("#adminEngineBtn").click()
        page.wait_for_selector("#tab-engine", timeout=10000)
        # 切换至「02 流量调度与实验」子分页
        page.locator("#engineSubNav_02").click()
        page.wait_for_timeout(200)

        # 展开灰度抽屉
        drawer = page.locator("#adminGreyDrawer")
        assert drawer.is_visible(), "#adminGreyDrawer should exist in engine tab"

        drawer_title_el = drawer.locator(".engine-drawer-title")
        drawer_title_text = drawer_title_el.inner_text().strip()

        # 断言抽屉标题包含金丝雀发布且不含 A/B（标题为实际 UI 文案「高级配置 · 金丝雀灰度发布」）
        assert "高级配置 · 金丝雀灰度发布" in drawer_title_text, (
            f"Expected canary title in drawer, got '{drawer_title_text}'"
        )
        assert "A/B" not in drawer_title_text, (
            f"Drawer title should NOT contain A/B, got '{drawer_title_text}'"
        )

        # 打开抽屉核验内容
        drawer_header = drawer.locator(".engine-drawer-header")
        if "collapsed" in (drawer.get_attribute("class") or ""):
            drawer_header.click()
            page.wait_for_timeout(300)
        assert "collapsed" not in (drawer.get_attribute("class") or ""), "Drawer should be expanded"

        # ------------------------------------------------------------
        # Test 2: AI 效果观测与评测中心 4 功能区切换与截图
        # ------------------------------------------------------------
        print("\n[Test 2] 校验 AI 效果观测与评测中心 4 功能区解耦与切换...")
        page.locator("#analyticsBoardBtn").click()
        page.wait_for_selector("#tab-analytics", timeout=10000)
        assert page.locator("#tab-analytics").is_visible(), "#tab-analytics should be visible"

        sec_telemetry = page.locator("#sec-telemetry")
        sec_canary = page.locator("#sec-canary")
        sec_experiment = page.locator("#sec-experiment")
        sec_eval = page.locator("#sec-eval")

        # 2.1 切换至功能区 2: 金丝雀发布监控
        print("校验功能区 2: 金丝雀发布监控...")
        page.locator("#btnSubViewCanary").click()
        page.wait_for_timeout(300)

        # 等待金丝雀数据加载
        page.wait_for_function(
            """() => {
                const grey = document.getElementById('analyticsGreyBody');
                return grey && !grey.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        assert sec_canary.is_visible(), "#sec-canary must be visible"
        assert not sec_telemetry.is_visible(), "#sec-telemetry must NOT be visible"
        assert not sec_experiment.is_visible(), "#sec-experiment must NOT be visible"
        assert not sec_eval.is_visible(), "#sec-eval must NOT be visible"

        # 标题断言
        canary_title = sec_canary.locator(".analytics-section-title")
        assert canary_title.is_visible(), "Canary title should be visible"
        assert "金丝雀发布监控" in canary_title.inner_text(), (
            f"Expected '金丝雀发布监控', got '{canary_title.inner_text()}'"
        )

        # 断言 A/B 实验区域不在 #sec-canary 中
        assert sec_canary.locator("#analyticsExperimentsBody").count() == 0, (
            "A/B experiment body should NOT be inside #sec-canary"
        )
        assert "A/B 科学实验台" not in sec_canary.inner_text(), (
            "A/B experiment text should NOT be inside #sec-canary"
        )

        # 灰度监控表格 / 状态可见
        assert page.locator("#analyticsGreyBody").is_visible()
        assert page.locator("#adminGreySamplesTable").is_visible() or page.locator("#adminGreySamplesBody").is_visible()

        # 截屏功能区 2
        shot_canary = os.path.join(SCREENSHOT_DIR, "sec_canary_decoupled.png")
        page.screenshot(path=shot_canary, full_page=False)
        print(f"Saved canary screenshot: {shot_canary}")

        # 2.2 切换至功能区 3: A/B 科学实验台
        print("校验功能区 3: A/B 科学实验台...")
        page.locator("#btnSubViewExperiment").click()
        page.wait_for_timeout(300)

        # 等待实验指标与 p-value 卡片加载
        page.wait_for_function(
            """() => {
                const ab = document.getElementById('analyticsExperimentsBody');
                const pval = document.getElementById('pvalueCardsContainer');
                return ab && pval && !ab.innerText.includes('加载中') && pval.children.length > 0;
            }""",
            timeout=15000
        )

        assert sec_experiment.is_visible(), "#sec-experiment must be visible"
        assert not sec_telemetry.is_visible(), "#sec-telemetry must NOT be visible"
        assert not sec_canary.is_visible(), "#sec-canary must NOT be visible"
        assert not sec_eval.is_visible(), "#sec-eval must NOT be visible"

        # 标题断言
        exp_title = sec_experiment.locator(".analytics-section-title")
        assert exp_title.is_visible(), "A/B experiment title should be visible"
        assert "A/B 科学实验台" in exp_title.inner_text(), (
            f"Expected 'A/B 科学实验台', got '{exp_title.inner_text()}'"
        )

        # 断言核心实验组件可见
        assert page.locator("#analyticsExperimentsBody").is_visible()
        assert page.locator("#pvalueCardsContainer").is_visible()

        # 截屏功能区 3
        shot_experiment = os.path.join(SCREENSHOT_DIR, "sec_experiment_decoupled.png")
        page.screenshot(path=shot_experiment, full_page=False)
        print(f"Saved experiment screenshot: {shot_experiment}")

        # 2.3 切换至功能区 4: 基准资产与确权
        print("校验功能区 4: 基准资产与确权...")
        page.locator("#btnSubViewEval").click()
        page.wait_for_timeout(300)

        # 等待基准资产与确权数据加载
        page.wait_for_function(
            """() => {
                const golden = document.getElementById('goldenBoardBody');
                const gt = document.getElementById('gtEvalsetStats');
                return golden && gt && !golden.innerText.includes('加载中') && !gt.innerText.includes('加载中');
            }""",
            timeout=15000
        )

        assert sec_eval.is_visible(), "#sec-eval must be visible"
        assert not sec_telemetry.is_visible(), "#sec-telemetry must NOT be visible"
        assert not sec_canary.is_visible(), "#sec-canary must NOT be visible"
        assert not sec_experiment.is_visible(), "#sec-experiment must NOT be visible"

        # 标题断言
        eval_title = sec_eval.locator(".analytics-section-title")
        assert eval_title.is_visible(), "Benchmark eval title should be visible"
        assert "基准资产" in eval_title.inner_text() and "确权" in eval_title.inner_text(), (
            f"Expected benchmark & gt confirmation in title, got '{eval_title.inner_text()}'"
        )
        assert "基准资产与确权" in page.locator("#btnSubViewEval").inner_text()

        # 断言基准资产与确权组件可见
        assert page.locator("#goldenBoardBody").is_visible()
        assert page.locator("#gtEvalsetStats").is_visible()
        evalset_links = page.locator("#sec-eval a[href='/evalset']")
        assert evalset_links.count() >= 1, "Expected GT review link inside #sec-eval"
        assert evalset_links.first.is_visible(), "GT review link in #sec-eval must be visible"

        browser.close()
        print("\nPlaywright Canary & A/B Decoupled E2E tests passed successfully.")


if __name__ == "__main__":
    test_playwright_canary_ab_decoupled()
