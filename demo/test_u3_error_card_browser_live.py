# -*- coding: utf-8 -*-
"""
U-3 错误卡归因三分类 验收 Agent 自动化浏览器实测脚本 (Playwright):

纯前端改动（demo/static/js/main.js showErrorCard 三分类 quality > gate > engine），
无需上传图片——直接 page.evaluate 驱动 showErrorCard 后断言卡片四槽位文案与按钮显隐。

覆盖用例：
1. gate 算术：算术门禁 msg → 「数字对不上」heading、人话化原因（含金额与相差）、不含「画质」、
   btnForceRetry 隐藏 / btnRetryNormal + btnConvertManual 显示；
2. gate 契约前缀：契约校验失败 + payment_marked → gate 分支 + 字段名映射「是否已付款」；
3. gate 裸消息：'总额不能为0'（contract.py 裸 error_msg）→ gate 分支；
4. engine 轮询超时：识别任务轮询超时 → 「很忙」heading，不含「画质」「模糊」，btnForceRetry 隐藏；
5. engine 空消息：showErrorCard('', 42) 与 (undefined, 42) → engine 兜底且无 pageerror；
6. quality 回归：带 code 与不带 code 两遍 → 「照片有点模糊」heading，btnForceRetry 显示且 title 含「画质」；
7. 双关键词优先级：'图像模糊 算术门禁: ...' → quality 优先。

每项 print ✓/✗，末尾总结，exit code 0/1。
"""

import sys

from playwright.sync_api import sync_playwright

BASE_URL = "http://127.0.0.1:15010"

results = []          # (case_name, ok, detail)
page_errors = []      # 浏览器 pageerror 收集


def check(case, cond, detail=""):
    """单条断言记录并打印 ✓/✗。"""
    ok = bool(cond)
    mark = "✓" if ok else "✗"
    line = f"[{mark}] {case}"
    if detail:
        line += f" —— {detail}"
    print(line)
    results.append((case, ok, detail))
    return ok


def snapshot(page):
    """读取错误卡四槽位 + 三按钮可见性/title 的当前状态。"""
    return page.evaluate(
        """
        () => {
            const vis = id => {
                const el = document.getElementById(id);
                if (!el) return null;
                return el.offsetParent !== null && el.style.display !== 'none';
            };
            const card = document.getElementById('errorCard');
            return {
                hidden: card.classList.contains('hide'),
                title: document.getElementById('errorCardTitle').innerText,
                badge: document.getElementById('errorCardBadge').innerText,
                heading: document.getElementById('errorCardHeading').innerText,
                msg: document.getElementById('errorMsgText').innerText,
                cardText: card.innerText,
                forceVisible: vis('btnForceRetry'),
                forceTitle: document.getElementById('btnForceRetry').title,
                retryVisible: vis('btnRetryNormal'),
                retryTitle: document.getElementById('btnRetryNormal').title,
                convertVisible: vis('btnConvertManual')
            };
        }
        """
    )


def run_acceptance_browser():
    print("=" * 80)
    print("【U-3 验收 Agent】错误卡归因三分类（quality > gate > engine）浏览器实测")
    print(f"目标环境: {BASE_URL}")
    print("=" * 80)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("pageerror", lambda e: page_errors.append(str(e)))

        print("\n[步骤 0] 打开系统首页并就绪...")
        page.goto(BASE_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(500)

        # 展开左右分栏工作区（真实链路上传后自动展开），保证按钮可见性可测
        page.evaluate(
            """
            () => {
                document.getElementById('splitViewArea').classList.remove('hide');
                document.getElementById('preConfirmCard').classList.add('hide');
                document.getElementById('loadingCard').classList.add('hide');
                document.getElementById('prefillFormCard').classList.add('hide');
            }
            """
        )

        # ---------- 用例 1：gate 算术 ----------
        print("\n[用例 1] gate 算术门禁：'算术门禁: 明细合计=265 预期总额=265，但总额=100（差165）'")
        page.evaluate("showErrorCard('算术门禁: 明细合计=265 预期总额=265，但总额=100（差165）', 42)")
        s = snapshot(page)
        check("1.1 卡片已展示", not s["hidden"])
        check("1.2 heading 含「数字对不上」", "数字对不上" in s["heading"], s["heading"])
        check("1.3 title 为门禁拦截标题", s["title"] == "[拦截] AI 发现单据数字有疑问", s["title"])
        check("1.4 badge 为「需人工核对」", s["badge"] == "需人工核对", s["badge"])
        check("1.5 msg 含金额 265", "265" in s["msg"])
        check("1.6 msg 含「相差 165」人话化", "相差 165" in s["msg"],
              s["msg"].splitlines()[1] if len(s["msg"].splitlines()) > 1 else s["msg"])
        check("1.7 全卡不含「画质」", "画质" not in s["cardText"])
        check("1.8 btnForceRetry 隐藏", s["forceVisible"] is False)
        check("1.9 btnRetryNormal 显示", s["retryVisible"] is True)
        check("1.10 btnRetryNormal title 为重核语义", s["retryTitle"] == "让 AI 重新核对这张单据", s["retryTitle"])
        check("1.11 btnConvertManual 显示（有 receiptId）", s["convertVisible"] is True)
        check("1.12 msg 含「应为 265」（等号一并吃掉，无「应为=」磕绊）",
              "应为 265" in s["msg"] and "应为=" not in s["msg"])

        # ---------- 用例 2：gate 契约前缀 + 字段映射 ----------
        print("\n[用例 2] gate 契约校验失败：'契约校验失败: payment_marked: Input should be a valid boolean'")
        page.evaluate(
            "showErrorCard('契约校验失败: payment_marked: Input should be a valid boolean', 43)"
        )
        s = snapshot(page)
        check("2.1 heading 含「数字对不上」", "数字对不上" in s["heading"], s["heading"])
        check("2.2 前缀「契约校验失败」已删除", "契约校验失败" not in s["msg"])
        check("2.3 msg 含字段映射「是否已付款」", "是否已付款" in s["msg"], s["msg"])
        check("2.4 btnForceRetry 隐藏", s["forceVisible"] is False)
        check("2.5 btnConvertManual 显示", s["convertVisible"] is True)
        check("2.6 原因行无裸英文（Input should/boolean 已映射为中文）",
              "Input should" not in s["msg"] and "boolean" not in s["msg"])
        check("2.7 msg 含映射文案「填写的内容格式不对」", "填写的内容格式不对" in s["msg"])
        check("2.8 隐藏的 btnForceRetry title 已复位中性（不含「画质」）", "画质" not in s["forceTitle"],
              s["forceTitle"])

        # ---------- 用例 3：gate 裸消息 ----------
        print("\n[用例 3] gate 裸消息（contract.py 无前缀 error_msg）：'总额不能为0'")
        page.evaluate("showErrorCard('总额不能为0', 44)")
        s = snapshot(page)
        check("3.1 heading 含「数字对不上」", "数字对不上" in s["heading"], s["heading"])
        check("3.2 title 为门禁拦截标题", s["title"] == "[拦截] AI 发现单据数字有疑问", s["title"])
        check("3.3 msg 保留裸消息本身", "总额不能为0" in s["msg"])
        check("3.4 btnForceRetry 隐藏", s["forceVisible"] is False)

        # ---------- 用例 4：engine 轮询超时 ----------
        print("\n[用例 4] engine 轮询超时：'识别任务轮询超时：超过设定时限'")
        page.evaluate("showErrorCard('识别任务轮询超时：超过设定时限', 45)")
        s = snapshot(page)
        check("4.1 heading 含「很忙」", "很忙" in s["heading"], s["heading"])
        check("4.2 title 为稍后重试", s["title"] == "[稍后重试] AI 服务暂时繁忙", s["title"])
        check("4.3 badge 为「非照片问题」", s["badge"] == "非照片问题", s["badge"])
        check("4.4 全卡不含「画质」", "画质" not in s["cardText"])
        check("4.5 全卡不含「模糊」", "模糊" not in s["cardText"])
        check("4.6 不透出原始错误串（轮询超时原文不进正文）", "轮询超时" not in s["msg"])
        check("4.7 btnForceRetry 隐藏", s["forceVisible"] is False)
        check("4.8 btnRetryNormal 显示且 title 为重新发起识别",
              s["retryVisible"] is True and s["retryTitle"] == "重新发起识别", s["retryTitle"])
        check("4.9 btnConvertManual 显示", s["convertVisible"] is True)
        check("4.10 隐藏的 btnForceRetry title 复位为中性文案",
              s["forceTitle"] == "重新提交给 AI 解析", s["forceTitle"])

        # ---------- 用例 5：engine 空消息兜底 ----------
        print("\n[用例 5] engine 空消息兜底：showErrorCard('', 42) 与 (undefined, 42)")
        page.evaluate("showErrorCard('', 42)")
        s = snapshot(page)
        check("5.1 空 string → engine（heading 含「很忙」）", "很忙" in s["heading"], s["heading"])
        check("5.2 空 string → 默认原因行「AI 多次核对仍未通过」仅 gate 使用（此处 engine 固定文案）",
              "AI 多次核对仍未通过" not in s["msg"])
        page.evaluate("showErrorCard(undefined, 42)")
        s = snapshot(page)
        check("5.3 undefined → engine（heading 含「很忙」）", "很忙" in s["heading"], s["heading"])
        check("5.4 undefined → btnForceRetry 隐藏", s["forceVisible"] is False)
        page.evaluate("showErrorCard(null, 42)")
        s = snapshot(page)
        check("5.5 null → engine 兜底不抛错", "很忙" in s["heading"], s["heading"])
        check("5.6 全程无 pageerror", len(page_errors) == 0, "; ".join(page_errors))

        # ---------- 用例 6：quality 回归（带 code 与不带 code）----------
        print("\n[用例 6] quality 回归：'图像模糊度过高，请重新拍摄清晰单据'（不带 code / 带 code 两遍）")
        page.evaluate("showErrorCard('图像模糊度过高，请重新拍摄清晰单据', null)")
        s = snapshot(page)
        check("6.1 不带 code → heading「照片有点模糊」", "照片有点模糊" in s["heading"], s["heading"])
        check("6.2 title 为画质预检未达标", s["title"] == "[提示] 图像画质预检未达标（可强制继续）", s["title"])
        check("6.3 badge 为「用户自主决定」", s["badge"] == "用户自主决定", s["badge"])
        check("6.4 btnForceRetry 显示", s["forceVisible"] is True)
        check("6.5 btnForceRetry title 含「画质」", "画质" in s["forceTitle"], s["forceTitle"])
        check("6.6 无 receiptId 时 btnConvertManual 显示（逃生通道随时可用）", s["convertVisible"] is True)
        page.evaluate("showErrorCard('识别失败', 101, 'IMAGE_QUALITY_ERROR')")
        s = snapshot(page)
        check("6.7 带 code IMAGE_QUALITY_ERROR → heading「照片有点模糊」",
              "照片有点模糊" in s["heading"], s["heading"])
        check("6.8 带 code → 三按钮齐备（force/retry/convert 均 visible）",
              s["forceVisible"] is True and s["retryVisible"] is True and s["convertVisible"] is True)

        # ---------- 用例 7：双关键词优先级 ----------
        print("\n[用例 7] 双关键词优先级：'图像模糊 算术门禁: 明细合计=10 预期总额=10，但总额=5（差5）'")
        page.evaluate(
            "showErrorCard('图像模糊 算术门禁: 明细合计=10 预期总额=10，但总额=5（差5）', 46)"
        )
        s = snapshot(page)
        check("7.1 quality 优先（heading「照片有点模糊」）", "照片有点模糊" in s["heading"], s["heading"])
        check("7.2 btnForceRetry 显示（画质语义）", s["forceVisible"] is True)
        check("7.3 不落入门禁文案（heading 不含「数字对不上」）", "数字对不上" not in s["heading"])

        # ---------- 残留状态回归：gate 后切 quality 再切 engine ----------
        print("\n[用例 8] 分类切换按钮状态不残留：gate → quality → engine")
        page.evaluate("showErrorCard('算术门禁: 明细合计=1 预期总额=1，但总额=2（差1）', 47)")
        page.evaluate("showErrorCard('图像模糊度过高', 47)")
        s = snapshot(page)
        check("8.1 gate→quality 后 btnForceRetry 恢复显示", s["forceVisible"] is True)
        check("8.2 gate→quality 后 btnRetryNormal title 复位「重新发起识别」",
              s["retryTitle"] == "重新发起识别", s["retryTitle"])
        check("8.2b gate→quality 后 btnForceRetry title 恢复画质语义（含「画质」）",
              "画质" in s["forceTitle"], s["forceTitle"])
        page.evaluate("showErrorCard('输出不是合法 JSON', 47)")
        s = snapshot(page)
        check("8.3 quality→engine 后 btnForceRetry 重新隐藏", s["forceVisible"] is False)
        check("8.4 全程无 pageerror（终检）", len(page_errors) == 0, "; ".join(page_errors))

        browser.close()

    # ---------- 总结 ----------
    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    failed = [name for name, ok, _ in results if not ok]
    print("\n" + "=" * 80)
    print(f"【U-3 验收总结】：{passed}/{total} 项通过")
    if failed:
        print("失败项：")
        for name in failed:
            print(f"  ✗ {name}")
        print("=" * 80)
        sys.exit(1)
    print("【验收结论】：U-3 错误卡归因三分类（quality > gate > engine）全部用例通过！")
    print("=" * 80)
    sys.exit(0)


if __name__ == "__main__":
    run_acceptance_browser()
