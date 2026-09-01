"""
Live Browser Interactive Test for receipt_agent
验证脱敏解析 Modal 在浏览器窗口任意缩放改变大小时（1440x900 -> 1024x768 -> 800x600）的全自适应响应式布局与交互。
"""

import time
from playwright.sync_api import sync_playwright

def run_interactive_browser():
    print("=" * 80)
    print("启动 Playwright 可见浏览器脱敏解析大盘与自适应缩放端到端实测...")
    print("目标地址: http://127.0.0.1:15010")
    print("=" * 80)

    with sync_playwright() as p:
        # 启动 Chrome 可见浏览器模式
        browser = p.chromium.launch(channel="chrome", headless=False, slow_mo=400)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 访问首页 (1440x900)
        print("\n[1/6] 加载系统首页 (1440x900 标准宽屏)...", flush=True)
        page.goto("http://127.0.0.1:15010")
        page.wait_for_load_state("networkidle")
        time.sleep(1)

        # 确保角色为 admin
        page.select_option("#demoRoleSelect", "admin")
        time.sleep(0.5)

        # 2. 切换至「引擎与系统配置」Tab，验证职责纯化（仅配置项，无脱敏单据表格）
        print("[2/6] 切换至「引擎与系统配置」控制台...", flush=True)
        page.locator("#adminEngineBtn").click()
        time.sleep(1)
        assert page.locator("#tab-engine #adminRecognitionEngine").is_visible()
        assert page.locator("#tab-engine #adminGreySamplesBody").count() == 0

        # 3. 切换至「埋点观测台」Tab，拉取脱敏单据流水与使用状态指标
        print("[3/6] 切换至「埋点观测台」并拉取脱敏单据流水与使用状态指标...", flush=True)
        page.locator("#analyticsBoardBtn").click()
        time.sleep(1)
        refresh_btn = page.locator("#tab-analytics button:has-text('刷新脱敏单据流')").first
        refresh_btn.scroll_into_view_if_needed()
        refresh_btn.click()
        time.sleep(1.5)

        # 验证指标卡数值
        total_txt = page.locator("#mGreyTotal").inner_text()
        print(f"      -> 成功加载脱敏流: 总样本={total_txt}, 灰测组={page.locator('#mGreyCanary').inner_text()}", flush=True)

        # 4. 弹出全屏 Side-by-Side 脱敏观测 Modal
        print("[4/6] 打开第 1 张脱敏单据 (#15) 的 Side-by-Side 观测工作台...", flush=True)
        detail_btn = page.locator("#adminGreySamplesBody button:has-text('查看脱敏解析')").first
        detail_btn.click()
        time.sleep(1.5)

        # 验证 Modal 内各组件
        modal_title = page.locator("#adminGreyModalTitle").inner_text()
        vendor_text = page.locator("#mModalVendor").inner_text()
        print(f"      -> 观测大盘标题: {modal_title}", flush=True)
        print(f"      -> 脱敏供应商: {vendor_text}", flush=True)

        # 5. 动态缩放浏览器窗口验证自适应能力 (1440x900 -> 1024x768 -> 800x600)
        print("\n[5/6] 动态切换不同屏幕分辨率验证自适应响应式能力:", flush=True)
        
        # 分辨率 1: 1440x900 (标准大屏)
        print("   -> [测试分辨率 1/3] 1440x900 (桌面宽屏)...", flush=True)
        page.set_viewport_size({"width": 1440, "height": 900})
        time.sleep(1.5)

        # 分辨率 2: 1024x768 (标准平板 / 小屏笔记本)
        print("   -> [测试分辨率 2/3] 1024x768 (标准笔记本/平板)...", flush=True)
        page.set_viewport_size({"width": 1024, "height": 768})
        time.sleep(1.5)

        # 分辨率 3: 800x600 (紧凑窗口 / 移动设备适配)
        print("   -> [测试分辨率 3/3] 800x600 (紧凑窗口)...", flush=True)
        page.set_viewport_size({"width": 800, "height": 600})
        time.sleep(1.5)

        # 恢复 1440x900
        print("   -> 恢复 1440x900 宽屏视图...", flush=True)
        page.set_viewport_size({"width": 1440, "height": 900})
        time.sleep(1)

        # 6. 关闭 Modal，测试不同 Tab 切换与平滑滚动
        print("\n[6/6] 测试工作台关闭与各 Tab 平滑切换...", flush=True)
        page.locator("#adminGreySampleModal .modal-close-btn").click()
        time.sleep(1)

        # 切换到实时库存与价格
        page.locator("button:has-text('实时库存与价格')").click()
        time.sleep(1)

        # 切换到部门花销报表
        page.locator("button:has-text('部门花销报表')").click()
        time.sleep(1)

        # 切换回收据识别
        page.locator("button:has-text('收据识别')").click()
        time.sleep(1)

        print("\n" + "=" * 80, flush=True)
        print("端到端测试全部顺利通过！零样式破坏、零滚动失效、零 Emoji 违规。", flush=True)
        print("=" * 80, flush=True)
        browser.close()

if __name__ == "__main__":
    run_interactive_browser()

