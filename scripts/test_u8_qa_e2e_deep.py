# -*- coding: utf-8 -*-
"""
Issue U-8: 连续出错递进引导 + 等待预期提示 - QA 深度全流程实测与视觉/人机工效评估

验收标准（AC 1~6）:
- AC-1: 失败计数追踪通过 sessionStorage (receipt_quality_fail_count) 并在 sessionStorage 异常时安全兜底到内存变量
- AC-2: #errorCard 递进分级阶梯引导
  * Level 1 (第1次): 温和提示 ("照片有点模糊，可能影响识别...")
  * Level 2 (第2次): 追加实用拍摄指导 ("拍摄小贴士：请把单据摊平、光线充足、手机平行正对拍摄，避免反光或阴影...")
  * Level 3+ (第3次及以上): 强烈推荐转手工，并高亮 #btnConvertManual (btn-warning, order: -1 视觉第一序位)
- AC-3: AI识别成功或手工录入保存后计数重置为 0
- AC-4: 长等待预期提示：OCR 计时器 >= 25s~30s 时，#loadingStageName 动态更新为 "本单明细较多，AI 正在加紧核对，预计还需 10 秒…"，且逃生按钮随时可用
- AC-5: 按钮尺寸与人机工效满足高压低教育阿叔/阿姨需求（高度 >= 38px，WCAG AA 对比度 >= 4.5:1）
- AC-6: 全套测试用例零回归（pytest 全量通过）
"""

import os
import sys
import time
import shutil
from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"
SCREENSHOT_DIR = "/Users/ethan/Documents/GitHub/receipt-agent-interview/artifacts/staff_e2e/screens"
BRAIN_SCREENSHOT_DIR = "/Users/ethan/.gemini/antigravity-cli/brain/73b5f73c-9353-4b14-a095-2a57aa80a571/screens"

os.makedirs(SCREENSHOT_DIR, exist_ok=True)
os.makedirs(BRAIN_SCREENSHOT_DIR, exist_ok=True)

RESULTS = []


def record(ac, case, passed, evidence=""):
    mark = "PASS" if passed else "FAIL"
    status_symbol = "✓" if passed else "✗"
    print(f"[{status_symbol}] [{mark}] [{ac}] {case}")
    if evidence:
        print(f"      └─ 证据: {evidence}")
    RESULTS.append({
        "ac": ac,
        "case": case,
        "passed": bool(passed),
        "evidence": evidence
    })
    return passed


def save_screenshot(page, name):
    local_path = os.path.join(SCREENSHOT_DIR, name)
    brain_path = os.path.join(BRAIN_SCREENSHOT_DIR, name)
    page.screenshot(path=local_path, full_page=False)
    shutil.copyfile(local_path, brain_path)
    print(f"   [📸 截图已保存] {local_path}")
    return local_path


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


def run_qa_deep_verification():
    print("=" * 90)
    print("【QA 实测执行】Issue U-8: 连续出错递进引导 + 等待预期提示 + UX 专项评估")
    print(f"目标环境: {BASE_URL}")
    print("=" * 90)

    blur_path = "/tmp/receipt_blur_u8_qa.jpg"
    var_blur = make_blur_image(blur_path)
    print(f"[数据准备] 合成极模糊图片: {blur_path} (Laplacian 方差 = {var_blur:.2f})")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # =========================================================================
        # 1. 首页加载与初始化状态验证
        # =========================================================================
        print("\n--- 1. 首页加载与店员环境初始化 ---")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        record("AC-1", "系统首页成功加载且运行正常", page.title() != "", f"Title: {page.title()}")

        page.evaluate("() => window.resetQualityFailCount()")
        init_cnt = page.evaluate("() => ({ sess: sessionStorage.getItem('receipt_quality_fail_count'), mem: window.getQualityFailCount() })")
        record("AC-1", "初始状态失败计数完全归零", init_cnt["sess"] is None and init_cnt["mem"] == 0, f"{init_cnt}")

        # =========================================================================
        # 2. AC-1 / AC-2 Level 1 模糊拦截实测 (第 1 次)
        # =========================================================================
        print("\n--- 2. 第 1 次模糊上传拦截（Level 1: 温和提示） ---")
        file_input = page.locator("#receiptFile")
        file_input.set_input_files(blur_path)
        page.wait_for_timeout(400)

        # 点击「开始 AI 智能解析」
        page.locator("#preConfirmCard button.btn-success").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(300)

        save_screenshot(page, "u8_level1_gentle_blur.png")

        l1_info = page.evaluate("""() => {
            const card = document.getElementById('errorCard');
            const heading = document.getElementById('errorCardHeading')?.innerText || '';
            const msg = document.getElementById('errorMsgText')?.innerText || '';
            const badge = document.getElementById('errorCardBadge')?.innerText || '';
            const manualBtn = document.getElementById('btnConvertManual');
            const forceBtn = document.getElementById('btnForceRetry');
            const retryBtn = document.getElementById('btnRetryNormal');
            const styleManual = window.getComputedStyle(manualBtn);
            const rectManual = manualBtn.getBoundingClientRect();
            const rectForce = forceBtn.getBoundingClientRect();
            const rectRetry = retryBtn.getBoundingClientRect();

            function getLuminance(rgbStr) {
                const rgb = rgbStr.match(/\\d+/g);
                if (!rgb || rgb.length < 3) return 1;
                const a = rgb.slice(0, 3).map(v => {
                    v /= 255;
                    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                });
                return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
            }
            function getContrast(fg, bg) {
                const l1 = getLuminance(fg);
                const l2 = getLuminance(bg);
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
            }

            return {
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                memCount: window.getQualityFailCount(),
                heading: heading,
                msg: msg,
                badge: badge,
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order || styleManual.order,
                manualHeight: rectManual.height,
                forceHeight: rectForce.height,
                retryHeight: rectRetry.height,
                manualContrast: getContrast(styleManual.color, styleManual.backgroundColor),
                headingText: heading,
                noTechnicalJargon: !/stack|exception|nullpointer|traceback|undefined|errno/i.test(msg + heading)
            };
        }""")

        record("AC-1", "第 1 次失败后 sessionStorage.receipt_quality_fail_count 记录为 1", l1_info["failCount"] == "1", f"failCount={l1_info['failCount']}")
        record("AC-2", "Level 1 标题文案温和直白（'照片有点模糊，可能影响识别'）", "照片有点模糊" in l1_info["heading"], f"Heading: {l1_info['heading']}")
        record("AC-2", "Level 1 消息包含原图保留与多途径自救指引", "已保留" in l1_info["msg"] and "继续 AI 解析" in l1_info["msg"], f"Msg: {l1_info['msg']}")
        record("AC-2", "Level 1 转手工按钮为标准次要样式（btn-secondary，无高优先级 order）", "btn-secondary" in l1_info["btnClass"] and (l1_info["btnOrder"] == "" or l1_info["btnOrder"] == "0"), f"class={l1_info['btnClass']}, order={l1_info['btnOrder']}")
        record("AC-5", "Level 1 操作按钮高度均 >= 38px (阿叔大靶区)", l1_info["manualHeight"] >= 38 and l1_info["forceHeight"] >= 38 and l1_info["retryHeight"] >= 38, f"Manual: {l1_info['manualHeight']}px, Force: {l1_info['forceHeight']}px, Retry: {l1_info['retryHeight']}px")
        record("AC-5", "报错与提示 100% 直白中文，零技术黑话", l1_info["noTechnicalJargon"], "无任何英文报错或内部变量名")

        # =========================================================================
        # 3. AC-2 Level 2 模糊拦截实测 (第 2 次，拍摄技巧指引)
        # =========================================================================
        print("\n--- 3. 第 2 次模糊上传拦截（Level 2: 拍摄小贴士） ---")
        page.locator("#btnRetryNormal").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(300)

        save_screenshot(page, "u8_level2_shooting_tips.png")

        l2_info = page.evaluate("""() => {
            const manualBtn = document.getElementById('btnConvertManual');
            const rectManual = manualBtn.getBoundingClientRect();
            return {
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                heading: document.getElementById('errorCardHeading')?.innerText || '',
                msg: document.getElementById('errorMsgText')?.innerText || '',
                badge: document.getElementById('errorCardBadge')?.innerText || '',
                btnClass: manualBtn.className,
                manualHeight: rectManual.height
            };
        }""")

        record("AC-1", "第 2 次失败后 sessionStorage 递增至 2", l2_info["failCount"] == "2", f"failCount={l2_info['failCount']}")
        record("AC-2", "Level 2 标题递进为「照片还是有些模糊」", "照片还是有些模糊" in l2_info["heading"], f"Heading: {l2_info['heading']}")
        record("AC-2", "Level 2 详细提供阿叔拍摄小贴士（摊平、光线充足、平行正对、避免反光/阴影）", "拍摄小贴士" in l2_info["msg"] and "摊平" in l2_info["msg"] and "反光" in l2_info["msg"], f"Msg: {l2_info['msg']}")
        record("AC-2", "Level 2 转手工按钮依然保持常规 secondary 样式", "btn-secondary" in l2_info["btnClass"], f"class={l2_info['btnClass']}")

        # =========================================================================
        # 4. AC-2 Level 3+ 模糊拦截实测 (第 3 次，强烈推荐转手工 + 按钮高亮置顶)
        # =========================================================================
        print("\n--- 4. 第 3 次模糊上传拦截（Level 3+: 强烈推荐转手工 + 高亮置顶） ---")
        page.locator("#btnRetryNormal").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        page.wait_for_timeout(300)

        save_screenshot(page, "u8_level3_manual_recommend.png")

        l3_info = page.evaluate("""() => {
            const manualBtn = document.getElementById('btnConvertManual');
            const styleManual = window.getComputedStyle(manualBtn);
            const rectManual = manualBtn.getBoundingClientRect();

            function getLuminance(rgbStr) {
                const rgb = rgbStr.match(/\\d+/g);
                if (!rgb || rgb.length < 3) return 1;
                const a = rgb.slice(0, 3).map(v => {
                    v /= 255;
                    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                });
                return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
            }
            function getContrast(fg, bg) {
                const l1 = getLuminance(fg);
                const l2 = getLuminance(bg);
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
            }

            return {
                failCount: sessionStorage.getItem('receipt_quality_fail_count'),
                heading: document.getElementById('errorCardHeading')?.innerText || '',
                msg: document.getElementById('errorMsgText')?.innerText || '',
                badge: document.getElementById('errorCardBadge')?.innerText || '',
                btnClass: manualBtn.className,
                btnOrder: manualBtn.style.order || styleManual.order,
                btnBoxShadow: styleManual.boxShadow,
                manualHeight: rectManual.height,
                manualWidth: rectManual.width,
                manualContrast: getContrast(styleManual.color, styleManual.backgroundColor),
                parentDisplay: window.getComputedStyle(manualBtn.parentElement).display
            };
        }""")

        record("AC-1", "第 3 次失败后 sessionStorage 递增至 3", l3_info["failCount"] == "3", f"failCount={l3_info['failCount']}")
        record("AC-2", "Level 3+ 标题升级为「连续多次无法清晰识别」", "连续多次无法清晰识别" in l3_info["heading"], f"Heading: {l3_info['heading']}")
        record("AC-2", "Level 3+ 消息明确引导「建议直接点击下方「转手工补录」快速录入单据」", "建议直接点击下方「转手工补录」" in l3_info["msg"], f"Msg: {l3_info['msg']}")
        record("AC-2", "Level 3+ #btnConvertManual 切换为主推荐警示高亮类名 (btn-warning)", "btn-warning" in l3_info["btnClass"], f"class={l3_info['btnClass']}")
        record("AC-2", "Level 3+ #btnConvertManual 设置 order: -1 排在视觉操作第一序位", l3_info["btnOrder"] == "-1", f"order={l3_info['btnOrder']}")
        record("AC-5", "Level 3+ 高亮转手工按钮高度 >= 38px 且视觉靶区充足 (>=100px 宽度)", l3_info["manualHeight"] >= 38 and l3_info["manualWidth"] >= 100, f"Size: {l3_info['manualWidth']:.1f}x{l3_info['manualHeight']:.1f}px")

        # =========================================================================
        # 5. AC-4 OCR 超长等待预期提示与逃生通道实测
        # =========================================================================
        print("\n--- 5. OCR 长等待预期提示 (>=25s 动态提示 + 逃生通道可用) ---")
        stage_test = page.evaluate("""() => {
            startOcrTimer();
            // 手动推进起始时间戳至 27 秒前
            ocrStartTime = Date.now() - 27000;
            return new Promise(resolve => {
                setTimeout(() => {
                    const card = document.getElementById('loadingCard');
                    if (card) card.classList.remove('hide');
                    const text = document.getElementById('loadingStageName')?.innerText || '';
                    const escapeBtn = document.getElementById('btnAbortLoadingToManual');
                    const escapeVisible = escapeBtn && !escapeBtn.classList.contains('hide');
                    const escapeRect = escapeBtn ? escapeBtn.getBoundingClientRect() : null;
                    resolve({
                        text: text,
                        escapeVisible: !!escapeVisible,
                        escapeHeight: escapeRect ? escapeRect.height : 0,
                        escapeText: escapeBtn ? escapeBtn.innerText : ''
                    });
                }, 200);
            });
        }""")

        save_screenshot(page, "u8_long_wait_expectation.png")

        record("AC-4", "耗时 >= 25s 时 loadingStageName 动态展示 '本单明细较多，AI 正在加紧核对，预计还需 10 秒…'",
               "本单明细较多，AI 正在加紧核对，预计还需 10 秒" in stage_test["text"], f"Stage Text: {stage_test['text']}")
        record("AC-4", "长等待期间「等不及？点击取消并转手工补录」逃生按钮可见且高度 >= 38px",
               stage_test["escapeVisible"] and stage_test["escapeHeight"] >= 38 and "转手工补录" in stage_test["escapeText"],
               f"Visible={stage_test['escapeVisible']}, Height={stage_test['escapeHeight']}px, Text={stage_test['escapeText']}")

        # 测试点击逃生按钮
        page.locator("#btnAbortLoadingToManual").click()
        page.wait_for_timeout(300)
        aborted_to_manual = page.evaluate("""() => {
            const formCard = document.getElementById('prefillFormCard');
            const loadingCard = document.getElementById('loadingCard');
            return !formCard.classList.contains('hide') && loadingCard.classList.contains('hide');
        }""")
        record("AC-4", "点击长等待逃生按钮可即刻中止 loading 并平滑切入手工补录表单", aborted_to_manual, "loading 隐藏，手工表单展开")

        page.evaluate("() => stopOcrTimer()")

        # =========================================================================
        # 6. AC-3 转手工补录并保存后计数重置为 0
        # =========================================================================
        print("\n--- 6. 从 Level 3 错误卡进入手工补录并保存，核验计数重置 ---")
        # 重新模拟从 Level 3 errorCard 进入转手工
        page.evaluate("() => { showErrorCard('图像模糊度过高', null, 'IMAGE_QUALITY_ERROR'); }")
        page.locator("#btnConvertManual").click()
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=5000)

        # 检查原图是否保留在左侧
        has_left_img = page.evaluate("""() => {
            const img = document.getElementById('previewImg');
            const splitView = document.getElementById('splitViewArea');
            return !!(img && img.src && img.src.length > 0 && !splitView.classList.contains('hide'));
        }""")
        record("AC-2", "转手工录入时左侧原图清晰保留，店员可对照录入", has_left_img, "原图已保留展示在左侧 previewImg")

        # 填写表单数据
        page.evaluate("""() => {
            document.getElementById('inpSupplier').value = '祥興食品測試手工單';
            document.getElementById('inpDate').value = '2026-08-24';
            document.getElementById('inpTotal').value = '150.00';
            const tbody = document.getElementById('itemTableBody');
            let tr = tbody.querySelector('tr');
            if (!tr) {
                if (typeof addEmptyRow === 'function') addEmptyRow();
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

        save_screenshot(page, "u8_escape_to_manual_save.png")

        # 检查表单按钮高度
        save_btn_height = page.evaluate("() => document.getElementById('btnSaveReview')?.getBoundingClientRect().height || 0")
        record("AC-5", "手工录入表单保存按钮高度 >= 38px", save_btn_height >= 38, f"Height={save_btn_height}px")

        # 点击保存复核表单
        with page.expect_response(lambda r: '/api/save_edited' in r.url) as response_info:
            page.locator("#btnSaveReview").click()

        resp = response_info.value
        page.wait_for_timeout(500)

        post_save = page.evaluate("() => ({ sess: sessionStorage.getItem('receipt_quality_fail_count'), mem: window.getQualityFailCount() })")
        record("AC-3", "手工录入单据保存成功后，失败计数完全重置清空 (null/0)",
               resp.status == 200 and post_save["sess"] is None and post_save["mem"] == 0,
               f"HTTP {resp.status}, sessionStorage={post_save['sess']}, memory={post_save['mem']}")

        # =========================================================================
        # 7. AC-3 AI 识别成功回调 renderPrefillForm 触发重置
        # =========================================================================
        print("\n--- 7. AI 识别成功触发 renderPrefillForm 时计数重置 ---")
        ai_reset_check = page.evaluate("""() => {
            // 先模拟积累 2 次失败
            window.incrementQualityFailCount();
            window.incrementQualityFailCount();
            const before = window.getQualityFailCount();
            // 模拟 AI 解析成功回调 renderPrefillForm
            window.renderPrefillForm({
                vendor: '德利行',
                total_amount: 200.0,
                date: '2026-08-24',
                items: [{ name: '牛肉', qty: 2, unit: '斤', price: 100, amount: 200 }]
            });
            const after = {
                sess: sessionStorage.getItem('receipt_quality_fail_count'),
                mem: window.getQualityFailCount()
            };
            return { before, after };
        }""")

        record("AC-3", "AI 识别成功调用 renderPrefillForm 自动清空重置失败计数",
               ai_reset_check["before"] == 2 and ai_reset_check["after"]["sess"] is None and ai_reset_check["after"]["mem"] == 0,
               f"Before={ai_reset_check['before']}, After={ai_reset_check['after']}")

        # =========================================================================
        # 8. 错误隔离与非画质门禁核验
        # =========================================================================
        print("\n--- 8. 错误归因隔离：门禁拦截与系统繁忙不污染画质计数 ---")
        gate_test = page.evaluate("""() => {
            window.resetQualityFailCount();
            // 触发门禁错误
            showErrorCard('算术门禁: 明细合计=150 应为 200，相差 50', 'rec_gate_001', null);
            const gateHeading = document.getElementById('errorCardHeading')?.innerText || '';
            const gateMsg = document.getElementById('errorMsgText')?.innerText || '';
            const gateCount = {
                sess: sessionStorage.getItem('receipt_quality_fail_count'),
                mem: window.getQualityFailCount()
            };

            // 触发服务繁忙错误
            showErrorCard('API 504 Gateway Timeout: 识别服务繁忙', null, 'SERVICE_BUSY');
            const busyHeading = document.getElementById('errorCardHeading')?.innerText || '';
            const busyMsg = document.getElementById('errorMsgText')?.innerText || '';
            const busyCount = {
                sess: sessionStorage.getItem('receipt_quality_fail_count'),
                mem: window.getQualityFailCount()
            };

            return {
                gateHeading, gateMsg, gateCount,
                busyHeading, busyMsg, busyCount
            };
        }""")

        save_screenshot(page, "u8_gate_error_isolation.png")

        record("AC-2", "算术门禁拦截呈现直白人话标题（'单据上的数字对不上，AI 已暂停录入'）且不增加画质计数",
               "数字对不上" in gate_test["gateHeading"] and gate_test["gateCount"]["sess"] is None,
               f"Gate Heading: {gate_test['gateHeading']}, Count: {gate_test['gateCount']}")
        record("AC-2", "服务繁忙呈现 'AI 服务现在很忙' 且不甩锅画质、不增加画质计数",
               "AI 服务现在很忙" in gate_test["busyHeading"] and "模糊" not in gate_test["busyHeading"] and gate_test["busyCount"]["sess"] is None,
               f"Busy Heading: {gate_test['busyHeading']}")

        # =========================================================================
        # 9. AC-1 存储降级与异常容错核验 (sessionStorage 抛错时 fallback 到内存)
        # =========================================================================
        print("\n--- 9. 容错测试：sessionStorage 受限时自动 fallback 到内存 ---")
        fallback_test = page.evaluate("""() => {
            // 模拟 sessionStorage 抛出异常 (如无痕模式或 SecurityError)
            const origGet = sessionStorage.getItem;
            const origSet = sessionStorage.setItem;
            const origRemove = sessionStorage.removeItem;

            sessionStorage.getItem = () => { throw new Error('SecurityError: Access Denied'); };
            sessionStorage.setItem = () => { throw new Error('SecurityError: Access Denied'); };
            sessionStorage.removeItem = () => { throw new Error('SecurityError: Access Denied'); };

            window.resetQualityFailCount();
            const c0 = window.getQualityFailCount();
            const c1 = window.incrementQualityFailCount();
            const c2 = window.incrementQualityFailCount();
            window.resetQualityFailCount();
            const cEnd = window.getQualityFailCount();

            // 还原
            sessionStorage.getItem = origGet;
            sessionStorage.setItem = origSet;
            sessionStorage.removeItem = origRemove;

            return { c0, c1, c2, cEnd };
        }""")

        record("AC-1", "sessionStorage 抛异常时平滑 fallback 到内存计数器 (0 -> 1 -> 2 -> 0)",
               fallback_test["c0"] == 0 and fallback_test["c1"] == 1 and fallback_test["c2"] == 2 and fallback_test["cEnd"] == 0,
               f"Fallback sequence: {fallback_test}")

        # =========================================================================
        # 10. AC-5 综合 UI / WCAG AA 对比度与阿叔人机工效评估
        # =========================================================================
        print("\n--- 10. 阿叔/阿姨人机工效专项测试（按钮高度 >= 38px，对比度 WCAG AA >= 4.5）---")
        # 恢复 errorCard 展开状态以精确读取其按钮尺寸
        page.evaluate("() => showErrorCard('图像模糊度过高', null, 'IMAGE_QUALITY_ERROR')")
        ux_eval = page.evaluate("""() => {
            function getLuminance(rgbStr) {
                const rgb = rgbStr.match(/\\d+/g);
                if (!rgb || rgb.length < 3) return 1;
                const a = rgb.slice(0, 3).map(v => {
                    v /= 255;
                    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                });
                return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
            }
            function getContrast(fg, bg) {
                const l1 = getLuminance(fg);
                const l2 = getLuminance(bg);
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
            }

            const buttons = [
                { id: 'btnConvertManual', el: document.getElementById('btnConvertManual') },
                { id: 'btnForceRetry', el: document.getElementById('btnForceRetry') },
                { id: 'btnRetryNormal', el: document.getElementById('btnRetryNormal') }
            ];

            return buttons.map(b => {
                if (!b.el) return { id: b.id, exists: false };
                const rect = b.el.getBoundingClientRect();
                const style = window.getComputedStyle(b.el);
                return {
                    id: b.id,
                    exists: true,
                    height: rect.height,
                    width: rect.width,
                    fontSize: style.fontSize,
                    color: style.color,
                    bgColor: style.backgroundColor,
                    contrast: getContrast(style.color, style.backgroundColor)
                };
            });
        }""")

        all_heights_ok = True
        all_contrast_ok = True
        for b in ux_eval:
            if b["exists"]:
                height_ok = b["height"] >= 38
                contrast_ok = b["contrast"] >= 4.5  # WCAG AA >= 4.5:1
                if not height_ok: all_heights_ok = False
                if not contrast_ok: all_contrast_ok = False
                print(f"   - 按钮 #{b['id']}: 尺寸 {b['width']:.1f}x{b['height']:.1f}px (>=38px: {height_ok}), 字体 {b['fontSize']}, 对比度 {b['contrast']:.2f}:1")

        record("AC-5", "所有核心交互按钮高度均 >= 38px", all_heights_ok, f"{[b['id'] + ': ' + str(round(b['height'], 1)) + 'px' for b in ux_eval if b['exists']]}")
        record("AC-5", "所有按钮文本对比度达标 (满足 WCAG AA >= 4.5:1)", all_contrast_ok, f"{[b['id'] + ': ' + str(round(b['contrast'], 2)) + ':1' for b in ux_eval if b['exists']]}")

        browser.close()

    print("\n" + "=" * 90)
    total_checks = len(RESULTS)
    pass_checks = sum(1 for r in RESULTS if r["passed"])
    fail_checks = total_checks - pass_checks
    print(f"【U-8 QA 深度测试结果】共执行 {total_checks} 项检查，通过: {pass_checks}，失败: {fail_checks}")
    print("=" * 90)
    return fail_checks == 0


if __name__ == "__main__":
    success = run_qa_deep_verification()
    sys.exit(0 if success else 1)
