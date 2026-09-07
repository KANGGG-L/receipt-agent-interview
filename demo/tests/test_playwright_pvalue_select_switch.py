# -*- coding: utf-8 -*-
"""
E2E Playwright test verifying that select#pvalueExperimentSelect can switch experiments
smoothly without having its value reset or losing state.
"""
import os
import pytest
from playwright.sync_api import sync_playwright

BASE_URL = os.environ.get("TEST_BASE_URL", "http://127.0.0.1:15010")
SCREENSHOT_DIR = "demo/gui-test-screenshots"


def test_pvalue_experiment_select_switch():
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 访问首页并以 admin 身份登录
        page.goto(BASE_URL)
        page.wait_for_selector(".app-wrapper", timeout=10000)
        page.evaluate("() => { if (typeof setRole === 'function') setRole('admin'); }")
        page.wait_for_timeout(500)

        # 2. 切换到 AI 效果观测与评测中心
        page.locator('.sidebar-btn[data-target="tab-analytics"]').click()
        page.wait_for_timeout(300)

        # 3. 切换到 功能区 3: A/B 科学实验台
        page.locator("#btnSubViewExperiment").click()
        page.wait_for_timeout(500)

        # 等待实验指标与 p-value 卡片加载
        page.wait_for_function(
            """() => {
                const sel = document.getElementById('pvalueExperimentSelect');
                const pval = document.getElementById('pvalueCardsContainer');
                return sel && sel.options.length >= 2 && pval && pval.children.length > 0;
            }""",
            timeout=15000
        )

        select = page.locator("#pvalueExperimentSelect")
        initial_val = select.input_value()
        print(f"Initial pvalueExperimentSelect value: {initial_val}")
        assert initial_val == "2", f"Expected initial value to be '2', got '{initial_val}'"

        cards = page.locator("#pvalueCardsContainer")
        assert "0.1360" in cards.inner_text(), "Expected '0.1360' in cards for initial experiment 2"

        # 4. 尝试切换至实验 #1 (草稿/无样本)
        select.select_option("1")
        page.wait_for_timeout(500)

        # 等待卡片内容更新为实验 #1 的数据 (p-value 变为 -，0.1360 消失)
        page.wait_for_function(
            """() => {
                const pval = document.getElementById('pvalueCardsContainer');
                return pval && !pval.innerText.includes('0.1360');
            }""",
            timeout=10000
        )

        # 断言：选择值必须保持为 '1'，不能被重置为 '2'
        current_val = select.input_value()
        print(f"After selecting '1', current value is: {current_val}")
        assert current_val == "1", (
            f"pvalueExperimentSelect failed to switch: expected '1', but got '{current_val}'"
        )
        assert "0.1360" not in cards.inner_text(), "0.1360 should not be in cards for experiment 1"
        assert "无法判定" in cards.inner_text(), "Expected '无法判定' in cards for experiment 1"

        # 截图记录切换至实验 1 后的效果
        shot_exp1 = os.path.join(SCREENSHOT_DIR, "pvalue_select_switched_to_1.png")
        page.screenshot(path=shot_exp1, full_page=False)
        print(f"Saved experiment 1 screenshot: {shot_exp1}")

        # 5. 尝试切回实验 #2 (已结题/有样本数据)
        select.select_option("2")
        page.wait_for_timeout(500)

        # 等待卡片内容更新回实验 #2 的数据 (出现 0.1360)
        page.wait_for_function(
            """() => {
                const pval = document.getElementById('pvalueCardsContainer');
                return pval && pval.innerText.includes('0.1360');
            }""",
            timeout=10000
        )

        val_back = select.input_value()
        print(f"After selecting '2', current value is: {val_back}")
        assert val_back == "2", f"Expected value to be '2', got '{val_back}'"
        assert "0.1360" in cards.inner_text(), "Expected '0.1360' in cards for experiment 2"
        assert "不显著" in cards.inner_text(), "Expected '不显著' in cards for experiment 2"

        # 截图记录切回实验 2 后的效果
        shot_exp2 = os.path.join(SCREENSHOT_DIR, "pvalue_select_switched_back_to_2.png")
        page.screenshot(path=shot_exp2, full_page=False)
        print(f"Saved experiment 2 screenshot: {shot_exp2}")

        browser.close()
