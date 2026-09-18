# -*- coding: utf-8 -*-
"""
Playwright E2E 验证：系统与引擎配置中的「新建 A/B 科学实验」弹窗交互与全链路闭环验证
"""

import os
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
SCREENSHOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../gui-test-screenshots"))
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def test_playwright_create_ab_experiment_modal():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("\n[Step 1] 访问首页 http://127.0.0.1:15010/ ...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_selector("#demoRoleSelect", timeout=10000)

        print("[Step 2] 切换角色为 admin ...")
        current_role = page.eval_on_selector("#demoRoleSelect", "el => el.value")
        if current_role != "admin":
            with page.expect_navigation(timeout=10000):
                page.select_option("#demoRoleSelect", "admin")
            page.wait_for_selector("#demoRoleSelect", timeout=10000)
            page.wait_for_timeout(300)

        print("[Step 3] 点击进入「系统与引擎配置」...")
        page.locator("#adminEngineBtn").click()
        page.wait_for_selector("#tab-engine", timeout=10000)
        assert page.locator("#tab-engine").is_visible()

        print("[Step 4] 展开「高级配置 · A/B 科学实验编排」抽屉...")
        exp_drawer = page.locator("#adminExperimentDrawer")
        if "collapsed" in (exp_drawer.get_attribute("class") or ""):
            exp_drawer.locator(".engine-drawer-header").click()
            page.wait_for_timeout(400)

        # 等待实验列表初次加载
        page.wait_for_function("() => { const s = document.getElementById('adminExpSelect'); return s && s.options.length > 0 && s.options[0].value !== ''; }", timeout=10000)

        print("[Step 5] 点击「+ 新建 A/B 实验」按钮...")
        create_btn = page.locator("button:has-text('+ 新建 A/B 实验')")
        assert create_btn.is_visible()
        create_btn.click()
        page.wait_for_timeout(300)

        print("[Step 6] 检查弹窗视口渲染与居中状态...")
        modal = page.locator("#createExperimentModal")
        assert modal.is_visible(), "Modal should be visible"

        modal_card = modal.locator(".modal-card")
        assert modal_card.is_visible(), "Modal card should be visible"

        # 检查视口及样式数据
        info = page.evaluate("""() => {
            const m = document.getElementById('createExperimentModal');
            const mc = m.querySelector('.modal-card');
            const mRect = m.getBoundingClientRect();
            const cRect = mc.getBoundingClientRect();
            const mStyle = window.getComputedStyle(m);
            return {
                mPosition: mStyle.position,
                mZIndex: parseInt(mStyle.zIndex, 10),
                mDisplay: mStyle.display,
                cTop: cRect.top,
                cBottom: cRect.bottom,
                cLeft: cRect.left,
                cRight: cRect.right,
                cWidth: cRect.width,
                cHeight: cRect.height,
                windowInnerHeight: window.innerHeight,
                windowInnerWidth: window.innerWidth
            };
        }""")

        print("Modal Metrics:", info)
        assert info["mPosition"] == "fixed", f"Expected position fixed, got {info['mPosition']}"
        assert info["mZIndex"] >= 1000, f"Expected z-index >= 1000, got {info['mZIndex']}"
        # 弹窗必须在可见视口内 (0 <= top < bottom <= 900)
        assert info["cTop"] >= 0, f"Modal card top {info['cTop']} is outside viewport!"
        assert info["cBottom"] <= info["windowInnerHeight"], f"Modal card bottom {info['cBottom']} exceeds viewport {info['windowInnerHeight']}!"

        # 校验模仿灰度发布参数卡片控件存在且可见
        ctrl_box = page.locator("#newExpCtrlBox")
        treat_box = page.locator("#newExpTreatBox")
        assert ctrl_box.is_visible(), "Control box should be visible"
        assert treat_box.is_visible(), "Treatment box should be visible"

        ctrl_preset = page.locator("#newExpCtrlPreset")
        ctrl_base_url = page.locator("#newExpCtrlBaseUrl")
        ctrl_api_key = page.locator("#newExpCtrlApiKey")
        ctrl_input = page.locator("#newExpControlModel")

        treat_preset = page.locator("#newExpTreatPreset")
        treat_base_url = page.locator("#newExpTreatBaseUrl")
        treat_api_key = page.locator("#newExpTreatApiKey")
        treat_input = page.locator("#newExpTreatmentModel")

        assert ctrl_preset.is_visible(), "Control preset should be visible"
        assert ctrl_base_url.is_visible(), "Control Base URL should be visible"
        assert ctrl_api_key.is_visible(), "Control API Key should be visible"
        assert ctrl_input.is_visible(), "Control model input should be visible"

        assert treat_preset.is_visible(), "Treatment preset should be visible"
        assert treat_base_url.is_visible(), "Treatment Base URL should be visible"
        assert treat_api_key.is_visible(), "Treatment API Key should be visible"
        assert treat_input.is_visible(), "Treatment model input should be visible"

        # 保存包含两组模仿灰度发布模型与网关设置的完整弹窗视觉证据截图
        shot_path = os.path.join(SCREENSHOT_DIR, "create_exp_modal_with_model_settings.png")
        page.screenshot(path=shot_path)
        print(f"已保存包含模型设置的弹窗截图: {shot_path}")

        print("[Step 7] 测试模仿灰度发布的网关预设自动填充...")
        # 测试对照组预设自动填充
        ctrl_preset.select_option("agnes")
        assert "agnes" in ctrl_base_url.input_value()
        assert ctrl_input.input_value() == "agnes-2.0-flash"
        ctrl_input.fill("gpt-4o-mini-2024-07-18")

        # 测试实验组预设自动填充
        treat_preset.select_option("bailian")
        assert "dashscope.aliyuncs.com" in treat_base_url.input_value()
        assert treat_input.input_value() == "qwen3.5-omni-flash"
        treat_input.fill("Qwen/Qwen3-VL-32B-Instruct")

        exp_name = f"AutoTest-Exp-{int(time.time())}"
        page.locator("#newExpName").fill(exp_name)
        page.locator("#newExpHypothesis").fill("测试香港手写餐饮小票准确率提升验证")

        page.locator("#newExpSuccessMetric").select_option("accuracy")
        page.locator("#newExpTargetPercent").fill("50")
        page.locator("#newExpMinSample").fill("30")

        print("[Step 8] 提交新建实验...")
        page.locator("#createExperimentForm button[type='submit']").click()
        page.wait_for_timeout(800)

        # 弹窗应已关闭
        assert not modal.is_visible(), "Modal should be closed after submit"

        print("[Step 9] 校验实验列表已选中新创建的实验并呈现两组模型...")
        selected_text = page.eval_on_selector("#adminExpSelect", "el => el.options[el.selectedIndex]?.text || ''")
        print(f"当前选中实验项: {selected_text}")
        assert exp_name in selected_text, f"Expected '{exp_name}' in selected option text, got '{selected_text}'"

        # 校验详情面板假设与两组模型
        hyp_text = page.locator("#adminExpHypothesis").inner_text()
        assert "测试香港手写餐饮小票准确率提升验证" in hyp_text

        ctrl_model_text = page.locator("#adminExpCtrlModel").inner_text()
        assert "gpt-4o-mini-2024-07-18" in ctrl_model_text, f"Expected control model in card, got '{ctrl_model_text}'"

        treat_model_text = page.locator("#adminExpTreatModel").inner_text()
        assert "Qwen/Qwen3-VL-32B-Instruct" in treat_model_text, f"Expected treatment model in card, got '{treat_model_text}'"

        print("\n[Step 10] 测试取消按钮关闭弹窗逻辑...")
        create_btn.click()
        page.wait_for_timeout(200)
        assert modal.is_visible()
        modal.locator("button:has-text('取消')").click()
        page.wait_for_timeout(200)
        assert not modal.is_visible()

        print("\n[Step 11] 测试遮罩点击关闭逻辑...")
        create_btn.click()
        page.wait_for_timeout(200)
        assert modal.is_visible()
        # 点击遮罩空白处 (例如 x=50, y=50)
        page.mouse.click(50, 50)
        page.wait_for_timeout(200)
        assert not modal.is_visible()

        # 保存成功创建后实验面板已选中的截图
        shot_final = os.path.join(SCREENSHOT_DIR, "create_exp_modal_created_success.png")
        page.screenshot(path=shot_final)
        print(f"已保存创建成功并自动装配截图: {shot_final}")

        browser.close()
        print("\n[PASS] Playwright 所有「新建 A/B 实验模型配置」端到端操作与持久化测试全部通过！")


if __name__ == "__main__":
    test_playwright_create_ab_experiment_modal()
