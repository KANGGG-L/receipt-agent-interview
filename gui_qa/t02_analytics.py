# -*- coding: utf-8 -*-
"""T02 治理与埋点观测台：四维渲染/租户下拉/手动刷新 Toast(瞬态)/脱敏弹窗开合。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def read_block(page, sel):
    return page.eval_on_selector(
        sel,
        "e => e ? {text: e.innerText.trim().slice(0, 220), rows: e.querySelectorAll('tr').length, loading: e.innerText.includes('加载中'), failed: e.innerText.includes('失败')} : null",
    )


def main(page, qa):
    goto_home(page)

    # 进入观测台（GUI 点击）
    page.click("#analyticsBoardBtn")
    page.wait_for_selector("#tab-analytics", state="visible", timeout=10000)
    page.wait_for_timeout(2500)
    qa.shot(page, "t02_board_top")

    # 1) 四维度渲染
    dims = {}
    for sel, name in [
        ("#analyticsEventDistBody", "事件分布"),
        ("#analyticsRecoveryBody", "挽回指标"),
        ("#analyticsGreyBody", "灰测状态"),
        ("#adminGreySamplesBody", "抽样流"),
        ("#analyticsExperimentsBody", "AB实验"),
    ]:
        dims[name] = read_block(page, sel)
        qa.log(f"[{name}] {json.dumps(dims[name], ensure_ascii=False)[:200]}")
    qa.shot(page, "t02_board_full")

    # 滚动到抽样流与实验区截图（视觉验证下半屏）
    page.eval_on_selector("#analyticsExperimentsBody", "e => e.scrollIntoView({block:'center'})")
    page.wait_for_timeout(400)
    qa.shot(page, "t02_board_bottom")

    # 2) 租户下拉实际选项
    opts = page.eval_on_selector(
        "#analyticsTenantSelect", "e => [...e.options].map(o => ({v: o.value, t: o.text}))")
    qa.log(f"租户下拉选项: {json.dumps(opts, ensure_ascii=False)}  ← 仅当 >1 才可下钻")

    # 3) 手动刷新 Toast（瞬态捕获：点击→轮询 toast 元素→立即截图）
    page.eval_on_selector("#adminGreySamplesBody", "e => e.scrollIntoView({block:'center'})")
    page.wait_for_timeout(300)
    qa.shot(page, "t02_before_refresh")

    toast_seen = None
    page.click("text=刷新脱敏单据流")
    t0 = time.time()
    while time.time() - t0 < 4.0:
        toasts = page.eval_on_selector_all(
            "[class*='toast'], [id*='toast'], [class*='Toast']",
            "els => els.filter(e => e.offsetParent).map(e => ({cls: e.className.slice(0,60), text: e.innerText.trim().slice(0,120)}))",
        )
        if toasts:
            toast_seen = toasts
            qa.shot(page, "t02_refresh_toast")
            break
        page.wait_for_timeout(120)
    qa.log(f"手动刷新 Toast 捕获: {json.dumps(toast_seen, ensure_ascii=False) if toast_seen else '未见任何 toast 元素'}")

    # 4) 脱敏弹窗开合
    btn = page.locator("#adminGreySamplesBody button", has_text="查看脱敏解析").first
    btn.click()
    page.wait_for_timeout(800)
    modal_visible = page.eval_on_selector(
        "#adminGreySampleModal",
        "e => ({visible: !!(e.offsetParent), hasHide: e.classList.contains('hide'), title: document.querySelector('#adminGreyModalTitle')?.innerText.slice(0,60), vendor: document.querySelector('#mModalVendor')?.innerText.slice(0,40)})",
    )
    qa.log(f"弹窗打开: {json.dumps(modal_visible, ensure_ascii=False)}")
    qa.shot(page, "t02_modal_open")

    page.click("#adminGreySampleModal .modal-close-btn")
    page.wait_for_timeout(500)
    closed = page.eval_on_selector(
        "#adminGreySampleModal", "e => ({visible: !!(e.offsetParent), hasHide: e.classList.contains('hide')})")
    qa.log(f"弹窗关闭: {json.dumps(closed, ensure_ascii=False)}")
    qa.shot(page, "t02_modal_closed")


if __name__ == "__main__":
    sys.exit(run("t02_analytics", main))
