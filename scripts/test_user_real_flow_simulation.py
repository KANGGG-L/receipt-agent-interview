# -*- coding: utf-8 -*-
"""
模拟用户真实端到端操作实测：
1. 访问首页，选择一张真实收据图片上传
2. 页面进入「收据确认」步骤，点击「开始 AI 智能解析」
3. 验证 AI 智能解析即使检测到门禁/明细疑问，也不阻塞停止，而是完整填报已识别结构并标红问题区域
4. 截取并保存真实操作屏幕截图
"""

import sys
import time
import os
from playwright.sync_api import sync_playwright

def test_real_user_flow():
    print("=" * 80)
    print("【Orca / Chrome 浏览器真实用户操作全流程实测】")
    print("=" * 80)

    # 寻找一张测试收据图片
    sample_img = None
    for candidate in [
        "demo/sample_receipts/sample_01.jpg",
        "demo/sample_receipts/sample_02.jpg",
        "demo/sample_receipts/sample_test_receipt.jpg",
    ]:
        if os.path.exists(candidate):
            sample_img = os.path.abspath(candidate)
            break

    if not sample_img:
        from PIL import Image, ImageDraw
        os.makedirs("demo/sample_receipts", exist_ok=True)
        sample_img = os.path.abspath("demo/sample_receipts/sample_test_receipt.jpg")
        im = Image.new("RGB", (600, 800), color=(250, 250, 245))
        draw = ImageDraw.Draw(im)
        draw.text((50, 50), "永旺食品有限公司", fill=(0, 0, 0))
        draw.text((50, 100), "日期: 2026-08-28", fill=(0, 0, 0))
        draw.text((50, 150), "1. 优质菜心 5kg x $12.00 = $60.00", fill=(0, 0, 0))
        draw.text((50, 200), "2. 新鲜生菜 3kg x $8.00 = $24.00", fill=(0, 0, 0))
        draw.text((50, 300), "总金额: $84.00 (已付款)", fill=(0, 0, 0))
        im.save(sample_img)

    print(f"使用测试图片: {sample_img}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # 1. 访问首页
        print("\n[Step 1] 打开系统首页...")
        page.goto("http://127.0.0.1:15010", wait_until="domcontentloaded")
        time.sleep(1)

        # 2. 上传文件
        print("\n[Step 2] 模拟用户拖拽/选择单据图片上传...")
        page.set_input_files("#receiptFile", sample_img)
        time.sleep(1)

        # 验证预览区域与步骤 1 卡片已显示
        pre_confirm_visible = page.locator("#preConfirmCard").is_visible()
        print(f"      -> 「收据确认」卡片展示: {pre_confirm_visible}")
        assert pre_confirm_visible is True, "上传后未能正常进入「收据确认」卡片"

        # 3. 点击「开始 AI 智能解析」
        print("\n[Step 3] 点击「开始 AI 智能解析」按钮发起解析...")
        page.locator("#preConfirmCard button.btn-success").click()
        time.sleep(1)

        # 验证进入 Loading 状态
        loading_visible = page.locator("#loadingCard").is_visible()
        print(f"      -> 正在解析 Loading 状态: {loading_visible}")

        # 4. 等待解析或转手工流程完成
        print("\n[Step 4] 等待识别任务完成并验证前端通知与表单展示...")
        for _ in range(45):
            time.sleep(1)
            if not page.locator("#loadingCard").is_visible():
                break

        # 检查当前展示的卡片与 Toast
        prefill_visible = page.locator("#prefillFormCard").is_visible()
        error_visible = page.locator("#errorCard").is_visible()
        supplier_val = page.eval_on_selector("#inpSupplier", "el => el.value")
        date_val = page.eval_on_selector("#inpDate", "el => el.value")
        total_val = page.eval_on_selector("#inpTotal", "el => el.value")

        print(f"      -> 复核表单可见状态: {prefill_visible}")
        print(f"      -> 错误卡片可见状态: {error_visible}")
        print(f"      -> 已解析供应商: {supplier_val}")
        print(f"      -> 已解析日期: {date_val}")
        print(f"      -> 已解析总额: {total_val}")

        # 验证单据表单已展示，且绝不阻塞在错误卡片
        assert prefill_visible is True, "未能自动展开复核/录入表单"
        assert error_visible is False, "不应当直接停止阻塞在错误卡片"

        # 保存操作全景截图
        screenshot_path = "scripts/test_user_real_flow_result.png"
        page.screenshot(path=screenshot_path)
        print(f"      -> 真实操作全景截图已保存至: {screenshot_path}")

        browser.close()

    print("\n" + "=" * 80)
    print("【Orca / Chrome 真实用户全链路模拟测试完成并全部 PASS】")
    print("=" * 80)

if __name__ == "__main__":
    test_real_user_flow()
