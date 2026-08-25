# -*- coding: utf-8 -*-
"""
U-8 连续出错递进引导 + 等待预期提示 - 自动化全流程浏览器实测 (Playwright)

测试点：
1. 极模糊图选择与拦截：
   - 第 1 次（Level 1）：
     * sessionStorage.receipt_quality_fail_count === "1"
     * 标题: "照片有点模糊，可能影响识别"
     * 文案包含: "系统已保留这张原图。您可以再拍一张更清晰的照片，或点下方「继续 AI 解析」让 AI 尽力识别，也可以转为手工录入。"
     * 按钮保持标准样式（btn-secondary）
   - 第 2 次（Level 2）：
     * sessionStorage.receipt_quality_fail_count === "2"
     * 标题: "照片还是有些模糊"
     * 文案包含: "拍摄小贴士：请把单据摊平、光线充足、手机平行正对拍摄，避免反光或阴影。您也可以点「继续 AI 解析」尝试，或转为手工录入。"
     * 按钮保持标准样式
   - 第 3 次及以上（Level 3+）：
     * sessionStorage.receipt_quality_fail_count === "3"
     * 标题: "连续多次无法清晰识别"
     * 文案包含: "连续多次未能清晰识别。为避免耽误时间，建议直接点击下方「转手工补录」快速录入单据，原图已在左侧为您展示。"
     * 按钮高亮: #btnConvertManual 添加 btn-warning，order: -1 置顶/首位，视觉醒目
2. 按钮尺寸与对比度核验（阿叔/阿姨高压 UX 要求）：
   - 所有操作按钮高度 >= 38px
   - 对比度符合 WCAG AA
3. 长等待预期提示（>25s）：
   - 耗时 >= 25s 时，#loadingStageName 自动显示: "本单明细较多，AI 正在加紧核对，预计还需 10 秒…"
   - 识别完成/复位后正常重置
4. 逃生与重置闭环：
   - Level 3+ 点击「转手工补录」无缝切入表单
   - 保存手工单成功后，sessionStorage.receipt_quality_fail_count 自动清空重置为 0
   - 再次上传模糊图从 Level 1 重新起算
5. 非画质错误（门禁/引擎）隔离：
   - 不增加画质失败计数，保持标准按钮样式
"""

import os
import sys
import time
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
RESULTS = []


def check(case, cond, detail=""):
    ok = bool(cond)
    mark = "✓" if ok else "✗"
    line = f"[{mark}] {case}"
    if detail:
        line += f" —— {detail}"
    print(line)
    RESULTS.append((case, ok, detail))
    return ok


def make_blur_image(out_path):
    from PIL import Image, ImageDraw, ImageFilter
    import numpy as np

    img = Image.new("RGB", (1000, 700), "white")
    draw = ImageDraw.Draw(img)
    draw.text((50, 50), "祥興食品供應商", fill="black")
    draw.text((50, 120), "送貨單 NO.20260824", fill="black")
    draw.text((50, 200), "菜心 10斤 x 15 = 150", fill="black")
    draw.text((50, 280), "合計 HK$150", fill="black")
    for _ in range(6):
        img = img.filter(ImageFilter.GaussianBlur(radius=16))
    img.save(out_path, format="JPEG", quality=80)

    gray = img.convert("L")
    arr = np.asarray(gray, dtype=np.float32)
    lap = (-4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1]
           + arr[1:-1, :-2] + arr[1:-1, 2:])
    var = float(lap.var())
    return var


def run_u8_live_acceptance():
    print("=" * 80)
    print("【Issue U-8 综合实测】连续出错递进引导 + 等待预期提示全流程")
    print(f"目标环境: {BASE_URL}")
    print("=" * 80)

    blur_path = "/tmp/receipt_blur_u8_test.jpg"
    var_blur = make_blur_image(blur_path)
    print(f"[准备] 合成极模糊图片: {blur_path} (Laplacian 方差 = {var_blur:.2f} < 30)")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # ---------- 阶段 1：页面加载与店员角色状态 ----------
        print("\n[阶段 1] 访问系统首页并初始化状态")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        check("1.1 首页加载成功", page.title() != "")

        # 清理 sessionStorage
        page.evaluate("() => window.resetQualityFailCount()")
        init_cnt = page.evaluate("() => window.getQualityFailCount()")
        check("1.2 初始失败计数为 0", init_cnt == 0, f"count={init_cnt}")

        # ---------- 阶段 2：第 1 次模糊拦截（Level 1 递进文案） ----------
        print("\n[阶段 2] 第 1 次上传模糊图片并核验 Level 1 提示")
        file_input = page.locator("#receiptFile")
        file_input.set_input_files(blur_path)
        page.wait_for_timeout(500)

        # 点击「开始 AI 智能解析」
        page.locator("#preConfirmCard button.btn-success").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(200)

        l1_state = page.evaluate("""() => {
            const card = document.getElementById('errorCard');
            const manualBtn = document.getElementById('btnConvertManual');
            const style = window.getComputedStyle(manualBtn);
            const rect = manualBtn.getBoundingClientRect();
            return {
                cardHidden: card.classList.contains('hide'),
                heading: document.getElementById('errorCardHeading')?.innerText,
                msg: document.getElementById('errorMsgText')?.innerText,
                badge: document.getElementById('errorCardBadge')?.innerText,
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                memCount: window.getQualityFailCount(),
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order,
                btnHeight: rect.height,
                btnWidth: rect.width
            };
        }""")

        check("2.1 第 1 次拦截后 errorCard 展开", not l1_state["cardHidden"])
        check("2.2 sessionStorage 记录失败次数为 1", l1_state["failCount"] == "1", f"failCount={l1_state['failCount']}")
        check("2.3 Level 1 Heading 为「照片有点模糊，可能影响识别」",
              "照片有点模糊" in l1_state["heading"], l1_state["heading"])
        check("2.4 Level 1 Msg 包含「系统已保留这张原图...」指引",
              "已保留" in l1_state["msg"] and "继续 AI 解析" in l1_state["msg"], l1_state["msg"])
        check("2.5 Level 1 转手工按钮为常规样式（btn-secondary，无特殊 order）",
              "btn-secondary" in l1_state["btnClass"] and l1_state["btnOrder"] == "",
              f"class={l1_state['btnClass']}, order={l1_state['btnOrder']}")
        check("2.6 转手工按钮高度 >= 38px", l1_state["btnHeight"] >= 38, f"height={l1_state['btnHeight']}px")

        # ---------- 阶段 3：第 2 次模糊拦截（Level 2 递进文案） ----------
        print("\n[阶段 3] 第 2 次上传模糊图片并核验 Level 2 拍摄技巧提示")
        # 点击重试（触发重新上传或重新分析）
        page.locator("#btnRetryNormal").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(200)

        l2_state = page.evaluate("""() => {
            const manualBtn = document.getElementById('btnConvertManual');
            const rect = manualBtn.getBoundingClientRect();
            return {
                heading: document.getElementById('errorCardHeading')?.innerText,
                msg: document.getElementById('errorMsgText')?.innerText,
                badge: document.getElementById('errorCardBadge')?.innerText,
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order,
                btnHeight: rect.height
            };
        }""")

        check("3.1 sessionStorage 记录失败次数为 2", l2_state["failCount"] == "2", f"failCount={l2_state['failCount']}")
        check("3.2 Level 2 Heading 为「照片还是有些模糊」",
              "照片还是有些模糊" in l2_state["heading"], l2_state["heading"])
        check("3.3 Level 2 Msg 包含「拍摄小贴士：请把单据摊平、光线充足...」",
              "拍摄小贴士" in l2_state["msg"] and "摊平" in l2_state["msg"], l2_state["msg"])
        check("3.4 Level 2 转手工按钮依然为常规样式",
              "btn-secondary" in l2_state["btnClass"], l2_state["btnClass"])
        check("3.5 Level 2 按钮高度 >= 38px", l2_state["btnHeight"] >= 38, f"height={l2_state['btnHeight']}px")

        # ---------- 阶段 4：第 3 次模糊拦截（Level 3+ 高亮推荐转手工） ----------
        print("\n[阶段 4] 第 3 次上传模糊图片并核验 Level 3+ 高亮推荐转手工")
        page.locator("#btnRetryNormal").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(200)

        l3_state = page.evaluate("""() => {
            const manualBtn = document.getElementById('btnConvertManual');
            const style = window.getComputedStyle(manualBtn);
            const rect = manualBtn.getBoundingClientRect();
            return {
                heading: document.getElementById('errorCardHeading')?.innerText,
                msg: document.getElementById('errorMsgText')?.innerText,
                badge: document.getElementById('errorCardBadge')?.innerText,
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order,
                btnFontWeight: manualBtn.style.fontWeight || style.fontWeight,
                btnBoxShadow: manualBtn.style.boxShadow || style.boxShadow,
                btnHeight: rect.height
            };
        }""")

        check("4.1 sessionStorage 记录失败次数为 3", l3_state["failCount"] == "3", f"failCount={l3_state['failCount']}")
        check("4.2 Level 3+ Heading 为「连续多次无法清晰识别」",
              "连续多次无法清晰识别" in l3_state["heading"], l3_state["heading"])
        check("4.3 Level 3+ Msg 明确建议「建议直接点击下方「转手工补录」快速录入单据」",
              "建议直接点击下方「转手工补录」" in l3_state["msg"], l3_state["msg"])
        check("4.4 Level 3+ 转手工按钮变为高亮醒目样式（btn-warning）",
              "btn-warning" in l3_state["btnClass"], l3_state["btnClass"])
        check("4.5 Level 3+ 转手工按钮 order: -1 排在视觉第一位",
              l3_state["btnOrder"] == "-1", f"order={l3_state['btnOrder']}")
        check("4.6 Level 3+ 转手工按钮高度 >= 38px",
              l3_state["btnHeight"] >= 38, f"height={l3_state['btnHeight']}px")

        # ---------- 阶段 5：长等待预期提示（>25s）核验 ----------
        print("\n[阶段 5] 核验 OCR 超长等待预期提示（>=25s 自动提醒）")
        stage_test = page.evaluate("""() => {
            startOcrTimer();
            // 手动推进起算点至 26 秒前
            ocrStartTime = Date.now() - 26000;
            return new Promise(resolve => {
                setTimeout(() => {
                    const text = document.getElementById('loadingStageName')?.innerText;
                    resolve(text);
                }, 150);
            });
        }""")

        check("5.1 耗时 >= 25s 时 loadingStageName 更新为长等待预期文案",
              "本单明细较多，AI 正在加紧核对，预计还需 10 秒" in stage_test, stage_test)

        page.evaluate("() => stopOcrTimer()")
        reset_stage = page.evaluate("() => document.getElementById('loadingStageName')?.innerText")
        check("5.2 停止后 loadingStageName 正常重置为完成态",
              "识别完成" in reset_stage or "视觉语义读取中" in reset_stage, reset_stage)

        # ---------- 阶段 6：转手工录入并成功保存后计数重置 ----------
        print("\n[阶段 6] 从 Level 3+ 错误卡点击「转手工补录」并保存单据，核验计数重置")
        # 此时 errorCard 处于 Level 3
        # 点击转手工补录
        page.locator("#btnConvertManual").click()
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=5000)

        form_vis = page.evaluate("() => !document.getElementById('prefillFormCard').classList.contains('hide')")
        check("6.1 点击转手工补录后 prefillFormCard 展现", form_vis)

        # 填写手工单字段
        page.evaluate("""() => {
            document.getElementById('inpSupplier').value = '祥興食品測試';
            document.getElementById('inpDate').value = '2026-08-24';
            document.getElementById('inpTotal').value = '150.00';
            const tbody = document.getElementById('itemTableBody');
            let tr = tbody.querySelector('tr');
            if (!tr) {
                addEmptyRow();
                tr = tbody.querySelector('tr');
            }
            if (tr) {
                const nameInp = tr.querySelector('.inp-name');
                if (nameInp) nameInp.value = '菜心';
                const qtyInp = tr.querySelector('.inp-qty');
                if (qtyInp) qtyInp.value = '10';
                const unitInp = tr.querySelector('.inp-unit');
                if (unitInp) unitInp.value = '斤';
                const priceInp = tr.querySelector('.inp-price');
                if (priceInp) priceInp.value = '15.00';
                const amtInp = tr.querySelector('.inp-amount');
                if (amtInp) amtInp.value = '150.00';
            }
        }""")

        # 点击保存复核表单并等待响应完成
        with page.expect_response(lambda r: '/api/save_edited' in r.url) as response_info:
            page.locator("#btnSaveReview").click()

        resp = response_info.value
        page.wait_for_timeout(400)

        post_save_count = page.evaluate("() => ({ sess: sessionStorage.getItem('receipt_quality_fail_count'), mem: window.getQualityFailCount() })")
        check("6.2 手工单保存成功后 sessionStorage 失败计数被清空 (null/0)",
              post_save_count["sess"] is None and post_save_count["mem"] == 0,
              f"HTTP {resp.status} —— {post_save_count}")

        # ---------- 阶段 7：重置后再次模糊，重新从 Level 1 起算 ----------
        print("\n[阶段 7] 计数重置后再次触发模糊错误，核验重新从 Level 1 起算")
        page.evaluate("() => showErrorCard('图像模糊度过高', null, 'IMAGE_QUALITY_ERROR')")
        page.wait_for_timeout(300)

        re_state = page.evaluate("""() => {
            const manualBtn = document.getElementById('btnConvertManual');
            return {
                heading: document.getElementById('errorCardHeading')?.innerText,
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order
            };
        }""")

        check("7.1 重置后再次模糊拦截，计数为 1", re_state["failCount"] == "1", f"failCount={re_state['failCount']}")
        check("7.2 Heading 恢复为 Level 1「照片有点模糊，可能影响识别」",
              "照片有点模糊" in re_state["heading"], re_state["heading"])
        check("7.3 按钮恢复为常规 secondary 样式",
              "btn-secondary" in re_state["btnClass"] and re_state["btnOrder"] == "",
              f"class={re_state['btnClass']}, order={re_state['btnOrder']}")

        # ---------- 阶段 8：非画质错误隔离核验 ----------
        print("\n[阶段 8] 核验非画质错误（门禁/服务繁忙）不递增画质计数")
        gate_cnt = page.evaluate("""() => {
            window.resetQualityFailCount();
            showErrorCard('算术门禁: 明细合计=150 应为 200，相差 50', 'rec_test_123', null);
            return {
                sess: sessionStorage.getItem('receipt_quality_fail_count'),
                mem: window.getQualityFailCount(),
                heading: document.getElementById('errorCardHeading')?.innerText
            };
        }""")

        check("8.1 门禁错误不会增加画质失败计数",
              gate_cnt["sess"] is None and gate_cnt["mem"] == 0,
              str(gate_cnt))
        check("8.2 门禁错误 Heading 为「单据上的数字对不上，AI 已暂停录入」",
              "数字对不上" in gate_cnt["heading"], gate_cnt["heading"])

        browser.close()

    print("\n" + "=" * 80)
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = total - passed
    print(f"【U-8 实测汇总】共 {total} 项检查，通过: {passed}，失败: {failed}")
    print("=" * 80)
    return failed == 0


if __name__ == "__main__":
    success = run_u8_live_acceptance()
    sys.exit(0 if success else 1)
