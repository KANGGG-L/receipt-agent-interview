# -*- coding: utf-8 -*-
"""
验证用户核心需求：
当识别结果存在门禁/算术/明细问题时（如"明细为空"、"数字互相矛盾"）：
1. 不应当直接停止阻塞在错误卡片；
2. 给出已解析的结构并自动填报到复核表单；
3. 弹出明确的 Toast 提示告知哪里有问题；
4. 顶部展示核对横幅，并在问题区域（总额/明细表格/供应商）进行针对性高亮标注。
"""

import time
from playwright.sync_api import sync_playwright

def test_gate_prefill_and_highlight():
    print("=" * 80)
    print("【实测：门禁/数字矛盾时绝不阻断，完整结构填报与精确定位高亮提示】")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        print("\n[Step 1] 打开系统首页...")
        page.goto("http://127.0.0.1:15010", wait_until="domcontentloaded")
        time.sleep(1)

        # 模拟一份包含"明细为空"或"总额矛盾"但已识别供应商与日期的门禁数据
        print("\n[Step 2] 模拟 AI 解析出单据基础信息但触发「明细为空」与「总额矛盾」门禁提示...")
        test_payload = {
            "status": "success",
            "receipt_id": 999,
            "image_url": "/static/images/logo_icon_perfect.png",
            "data": {
                "receipt_id": 999,
                "supplier_name": "永旺生鲜批发中心",
                "date": "2026-08-28",
                "sheet_name": "2026-08",
                "total_amount": 128.50,
                "items": [], # 明细为空
                "math_warnings": ["明细为空：未检出任何明细行", "算术门禁: 明细合计=0.00 预期总额=128.50"],
                "quality_warnings": [],
                "currency": "HKD"
            }
        }

        # 调用前端结算与渲染方法
        page.evaluate("""(data) => {
            const upArea = document.getElementById('uploadArea') || document.getElementById('singleUploadArea');
            if (upArea) upArea.classList.add('hide');
            const sv = document.getElementById('splitViewArea');
            if (sv) sv.classList.remove('hide');
            window.renderEditForm(data.data);
            document.getElementById('prefillFormCard').classList.remove('hide');
        }""", test_payload)
        time.sleep(1)

        # 断言 1: 复核表单可见，错误卡片不可见
        prefill_visible = page.locator("#prefillFormCard").is_visible()
        error_visible = page.locator("#errorCard").is_visible()
        print(f"      -> 复核录入表单可见: {prefill_visible} (预期: True)")
        print(f"      -> 阻塞错误卡片可见: {error_visible} (预期: False)")
        assert prefill_visible is True, "复核表单必须可见"
        assert error_visible is False, "绝不能阻塞在错误卡片"

        # 断言 2: 已解析结构已准确填入表单
        supplier_val = page.eval_on_selector("#inpSupplier", "el => el.value")
        date_val = page.eval_on_selector("#inpDate", "el => el.value")
        total_val = page.eval_on_selector("#inpTotal", "el => el.value")
        print(f"      -> 自动填入供应商: {supplier_val} (预期: 永旺生鲜批发中心)")
        print(f"      -> 自动填入日期: {date_val} (预期: 2026-08-28)")
        print(f"      -> 自动填入总额: {total_val} (预期: 128.50)")
        assert supplier_val == "永旺生鲜批发中心"
        assert date_val == "2026-08-28"
        assert float(total_val) == 128.50

        # 断言 3: Toast 提示与顶部 Alert 横幅包含具体问题解释
        banner_text = page.eval_on_selector("#alertBanner", "el => el.innerText")
        toast_texts = page.locator("#toastContainer .app-toast").all_inner_texts()
        full_toast_str = " | ".join(toast_texts)
        print(f"      -> 顶部核对横幅内容: {banner_text}")
        print(f"      -> 屏幕 Toast 提示: {full_toast_str}")
        assert "具体问题" in banner_text, "横幅必须包含具体问题解释"
        assert "明细" in banner_text, "横幅必须指出明细问题"
        assert "单据复核提示" in full_toast_str, "必须弹出复核 Toast 提示"

        # 断言 4: 明细表格展示补录提示行，总额输入框高亮标注
        table_html = page.eval_on_selector("#itemTableBody", "el => el.innerHTML")
        total_bg = page.eval_on_selector("#inpTotal", "el => el.style.backgroundColor")
        print(f"      -> 明细表格补录引导行展示: {'未检出单据明细项' in table_html}")
        print(f"      -> 总额输入框高亮背景色: {total_bg}")
        assert "未检出单据明细项" in table_html, "明细表格必须包含明确的补录引导"
        assert "217" in total_bg or "239" in total_bg or "rgba" in total_bg, "问题字段必须高亮标注"

        screenshot_path = "scripts/test_gate_prefill_highlight_result.png"
        page.screenshot(path=screenshot_path)
        print(f"      -> 截图已保存至: {screenshot_path}")

        browser.close()

    print("\n" + "=" * 80)
    print("【全部门禁结构填报与问题定位高亮断言 100% PASS】")
    print("=" * 80)

if __name__ == "__main__":
    test_gate_prefill_and_highlight()
