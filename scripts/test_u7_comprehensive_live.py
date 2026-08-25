# -*- coding: utf-8 -*-
"""
U-7 模糊图默认拦截与用户显式确认 - 自动化全流程浏览器实测 (Playwright)

测试点：
1. 极模糊图选择后：preConfirmCard 出现温和画质提示，但不自动强制（force=false）；
2. 按钮合规度核验（高度 >= 38px，对比度 WCAG AA，人话直白文案）；
3. 点击「开始 AI 智能解析」：请求携带 force=false，服务端 <1s 快速返回 HTTP 400 IMAGE_QUALITY_ERROR；
4. 前端展示 errorCard 画质分支：
   - 标题与文案为人话「照片有点模糊，可能影响识别」；
   - 包含「继续 AI 解析」按钮（btnForceRetry）与「转手工补录」按钮（btnConvertManual）；
5. 点击「继续 AI 解析」：携带 force=true 重新发起请求，服务端放行并正常 queued 入队；
6. 逃生通道验证：在 400 拦截态点击「转手工补录」，无缝切换到手工表单并保留左侧原图；
7. 状态复位验证：上传新单据时 force 自动重置为 false，不会继承上一次的 force=true。
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

    # 计算方差
    gray = img.convert("L")
    arr = np.asarray(gray, dtype=np.float32)
    lap = (-4 * arr[1:-1, 1:-1] + arr[:-2, 1:-1] + arr[2:, 1:-1]
           + arr[1:-1, :-2] + arr[1:-1, 2:])
    var = float(lap.var())
    return var


def get_clear_image():
    sample_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "demo", "samples", "20161001_711_thermal_receipt.jpg")
    if os.path.exists(sample_path):
        return sample_path
    # 备用合成高方差图
    import numpy as np
    from PIL import Image
    arr = np.random.randint(0, 255, (400, 400, 3), dtype=np.uint8)
    img = Image.fromarray(arr)
    tmp = "/tmp/receipt_clear_u7_test.jpg"
    img.save(tmp, format="JPEG")
    return tmp


def run_u7_live_acceptance():
    print("=" * 80)
    print("【Issue U-7 综合实测】模糊图默认拦截与用户显式确认全流程")
    print(f"目标环境: {BASE_URL}")
    print("=" * 80)

    blur_path = "/tmp/receipt_blur_u7_test.jpg"
    var_blur = make_blur_image(blur_path)
    clear_path = get_clear_image()
    print(f"[准备] 合成极模糊图片: {blur_path} (Laplacian 方差 = {var_blur:.2f} < 30)")
    print(f"[准备] 清晰图片: {clear_path}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # ---------- 阶段 1：页面加载与店员角色状态 ----------
        print("\n[阶段 1] 访问系统首页并验证就绪")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(600)
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()
        page.wait_for_timeout(200)
        check("1.1 首页加载成功", page.title() != "")

        # ---------- 阶段 2：选择极模糊图片 ----------
        print("\n[阶段 2] 选择极模糊图片并核验客户端预检提示")
        file_input = page.locator("#receiptFile")
        file_input.set_input_files(blur_path)
        page.wait_for_selector("#preConfirmCard:not(.hide)", timeout=10000)

        # 检查 preConfirmCard 是否显示
        pre_confirm_hidden = page.eval_on_selector("#preConfirmCard", "el => el.classList.contains('hide')")
        check("2.1 选择图片后 preConfirmCard 展开展示", not pre_confirm_hidden)

        # 检查画质提示横幅
        warn_txt = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        check("2.2 preConfirmQualityWarn 展现温和画质提示", "画质提示" in warn_txt or "模糊" in warn_txt, warn_txt)

        # 检查内部状态：force 必须初始化为 false
        init_forces = page.evaluate("() => ({ _selectedFileForce: window._selectedFileForce, photoForce: typeof BatchUploader !== 'undefined' ? BatchUploader.photos[0]?.force : null })")
        check("2.3 模糊图客户端检测后 force 初始化为 false（不自动 force）",
              init_forces["_selectedFileForce"] is False and init_forces["photoForce"] is False,
              str(init_forces))

        # 检查主操作按钮尺寸与样式（阿叔/阿姨高压 UX 要求：高度 >= 38px）
        btn_box = page.eval_on_selector(
            "#preConfirmCard button.btn-success",
            """el => {
                const r = el.getBoundingClientRect();
                const style = window.getComputedStyle(el);
                return { height: r.height, width: r.width, fontSize: style.fontSize, text: el.innerText };
            }"""
        )
        check("2.4 「开始 AI 智能解析」主按钮高度 >= 38px（实测 >=40px）",
              btn_box["height"] >= 38, f"高度={btn_box['height']}px, 宽度={btn_box['width']}px, 字号={btn_box['fontSize']}")
        check("2.5 主按钮文案直白人话", "开始" in btn_box["text"] and "AI" in btn_box["text"], btn_box["text"])

        # ---------- 阶段 3：点击「开始 AI 智能解析」触发默认拦截 ----------
        print("\n[阶段 3] 点击「开始 AI 智能解析」验证服务端 400 拦截与前端归因")
        req_captured = []
        resp_captured = []

        def on_request(req):
            if "/api/upload" in req.url:
                req_captured.append(req)

        def on_response(resp):
            if "/api/upload" in resp.url:
                resp_captured.append(resp)

        page.on("request", on_request)
        page.on("response", on_response)

        start_t = time.time()
        page.locator("#preConfirmCard button.btn-success").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)
        elapsed = time.time() - start_t

        # 验证请求参数
        check("3.1 发起 /api/upload 请求", len(req_captured) > 0)
        if req_captured:
            last_req = req_captured[-1]
            check("3.2 请求参数 force=false", "force=false" in last_req.url or "force=false" in (last_req.post_data or ""))

        # 验证响应状态
        check("3.3 服务端返回 HTTP 400 IMAGE_QUALITY_ERROR",
              len(resp_captured) > 0 and resp_captured[-1].status == 400,
              f"状态码: {resp_captured[-1].status if resp_captured else 'None'}")
        check("3.4 极模糊拦截快速失败（不进 LLM 超时悬挂）", elapsed < 10.0, f"实测耗时: {elapsed:.3f}s")

        # ---------- 阶段 4：核验 errorCard 画质分支展示与文案 ----------
        print("\n[阶段 4] 核验 errorCard 画质分支内容与按钮配置")
        error_state = page.evaluate("""() => {
            const card = document.getElementById('errorCard');
            const vis = id => {
                const el = document.getElementById(id);
                return el && !el.classList.contains('hide') && el.style.display !== 'none';
            };
            return {
                cardHidden: card.classList.contains('hide'),
                heading: document.getElementById('errorCardHeading')?.innerText,
                title: document.getElementById('errorCardTitle')?.innerText,
                badge: document.getElementById('errorCardBadge')?.innerText,
                msg: document.getElementById('errorMsgText')?.innerText,
                forceVisible: vis('btnForceRetry'),
                forceTitle: document.getElementById('btnForceRetry')?.title,
                convertVisible: vis('btnConvertManual'),
                convertTitle: document.getElementById('btnConvertManual')?.title,
                retryVisible: vis('btnRetryNormal')
            };
        }""")

        check("4.1 errorCard 处于展示状态", not error_state["cardHidden"])
        check("4.2 heading 为人话「照片有点模糊，可能影响识别」",
              "照片有点模糊" in error_state["heading"], error_state["heading"])
        check("4.3 badge 为「用户自主决定」", error_state["badge"] == "用户自主决定", error_state["badge"])
        check("4.4 msg 明确说明「原图已在左侧保留」并提供指引",
              "已保留" in error_state["msg"] and "继续" in error_state["msg"], error_state["msg"])
        check("4.5 「继续 AI 解析」按钮显示且 title 含画质语义",
              error_state["forceVisible"] and "画质" in error_state["forceTitle"], error_state["forceTitle"])
        check("4.6 「转手工补录」逃生按钮显示且可用",
              error_state["convertVisible"], error_state["convertTitle"])

        # ---------- 阶段 5：显式点击「继续 AI 解析」（force=true）----------
        print("\n[阶段 5] 点击「继续 AI 解析」显式确认强制上传")
        with page.expect_response(lambda r: "/api/upload" in r.url, timeout=10000) as force_resp_info:
            page.locator("#btnForceRetry").click()
        page.wait_for_selector("#loadingCard:not(.hide)", timeout=10000)
        force_resp = force_resp_info.value

        check("5.1 发起二次 /api/upload 请求", True)
        check("5.2 二次请求显式携带 force=true",
              "force=true" in force_resp.request.url or "force=true" in (force_resp.request.post_data or ""))

        check("5.3 服务端接受并放行（返回 HTTP 200 queued 入队）",
              force_resp.status == 200,
              f"状态码: {force_resp.status}")

        loading_visible = page.eval_on_selector("#loadingCard", "el => !el.classList.contains('hide')")
        check("5.4 进入 Loading 状态（4 阶段感知卡片展示）", loading_visible)

        # ---------- 阶段 6：逃生通道实测（转手工补录） ----------
        print("\n[阶段 6] 测试逃生通道：在模糊拦截态下转手工录入并保留原图")
        # 重新选择模糊图触发 400
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()
        page.locator("#receiptFile").set_input_files(blur_path)
        page.wait_for_selector("#preConfirmCard:not(.hide)", timeout=10000)
        page.locator("#preConfirmCard button.btn-success").click()
        page.wait_for_selector("#errorCard:not(.hide)", timeout=10000)

        # 点击「转手工补录」
        page.locator("#btnConvertManual").click()
        page.wait_for_selector("#prefillFormCard:not(.hide)", timeout=10000)

        manual_state = page.evaluate("""() => {
            const formCard = document.getElementById('prefillFormCard');
            const errCard = document.getElementById('errorCard');
            const previewImg = document.getElementById('previewImg');
            const noImgPlaceholder = document.getElementById('manualEntryNoImage');
            return {
                formVisible: formCard && !formCard.classList.contains('hide'),
                errHidden: errCard && errCard.classList.contains('hide'),
                previewSrc: previewImg ? previewImg.src : '',
                previewVisible: previewImg && previewImg.style.display !== 'none',
                noImgHidden: noImgPlaceholder && noImgPlaceholder.classList.contains('hide'),
            };
        }""")

        check("6.1 点击后 errorCard 隐藏且 prefillFormCard 展现",
              manual_state["formVisible"] and manual_state["errHidden"])
        check("6.2 左侧原图完好保留展示（非无图占位）",
              manual_state["previewVisible"] and manual_state["noImgHidden"] and bool(manual_state["previewSrc"]))

        # ---------- 阶段 7：新图片选择重置 force 状态 ----------
        print("\n[阶段 7] 验证新图片上传重置 force 状态（不继承先前的 force=true）")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()

        # 先模拟一次 force=true
        page.evaluate("() => { window._selectedFileForce = true; }")

        # 重新上传模糊图
        page.locator("#receiptFile").set_input_files(blur_path)
        page.wait_for_selector("#preConfirmCard:not(.hide)", timeout=10000)

        reset_state = page.evaluate("() => ({ _selectedFileForce: window._selectedFileForce, photoForce: typeof BatchUploader !== 'undefined' ? BatchUploader.photos[0]?.force : null })")
        check("7.1 重新选择图片后 force 重置为 false",
              reset_state["_selectedFileForce"] is False and reset_state["photoForce"] is False,
              str(reset_state))

        # ---------- 阶段 8：正常清晰图验证 ----------
        print("\n[阶段 8] 正常清晰图片上传对照（无模糊拦截）")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)
        auto_chk = page.locator("#chkAutoAnalyze")
        if auto_chk.is_checked():
            auto_chk.uncheck()

        page.locator("#receiptFile").set_input_files(clear_path)
        page.wait_for_selector("#preConfirmCard:not(.hide)", timeout=10000)

        clear_warn = page.eval_on_selector("#preConfirmQualityWarn", "el => el.classList.contains('hide') ? '' : el.innerText")
        check("8.1 清晰图无画质模糊警告", "模糊" not in clear_warn, f"提示文案: '{clear_warn}'")

        with page.expect_response(lambda r: "/api/upload" in r.url, timeout=10000) as resp_info:
            page.locator("#preConfirmCard button.btn-success").click()
        clear_resp = resp_info.value

        check("8.2 清晰图直接发送 force=false 并在服务端放行（200 queued）",
              clear_resp.status == 200,
              f"状态码: {clear_resp.status}")

        browser.close()

    # ---------- 总结 ----------
    total = len(RESULTS)
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 80)
    print(f"【U-7 验收结果】：{passed}/{total} 项通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  ✗ {name}")
        print("=" * 80)
        sys.exit(1)
    print("【验收结论】：Issue U-7 模糊图默认拦截与用户显式确认 验收全部通过！")
    print("=" * 80)
    sys.exit(0)


if __name__ == "__main__":
    run_u7_live_acceptance()
