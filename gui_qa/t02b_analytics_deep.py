# -*- coding: utf-8 -*-
"""T02b 观测台深测：手动刷新 Toast(长等待)/租户切换联动/快速切换竞态/4xx 捕获。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def samples_loading(page):
    return page.eval_on_selector(
        "#adminGreySamplesBody", "e => e.innerText.includes('正在拉取')")


def wait_samples_done(page, timeout=30):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if not samples_loading(page):
            return time.time() - t0
        page.wait_for_timeout(250)
    return None


def main(page, qa):
    hits = []
    page.on("response", lambda r: hits.append(f"{r.status} {r.url}") if r.status >= 400 else None)

    goto_home(page)
    page.click("#analyticsBoardBtn")
    page.wait_for_selector("#tab-analytics", state="visible", timeout=10000)
    took = wait_samples_done(page)
    qa.log(f"初次抽样流加载耗时: {took:.1f}s" if took else "初次抽样流 30s 未完成")

    # 1) 手动刷新 Toast：点击后最长等 25s 轮询 .app-toast
    qa.shot(page, "t02b_before_manual_refresh")
    page.click("text=刷新脱敏单据流")
    t0 = time.time()
    toast = None
    while time.time() - t0 < 25:
        found = page.eval_on_selector_all(
            ".app-toast", "els => els.map(e => ({cls: e.className, text: e.innerText.trim().slice(0,140)}))")
        if found:
            toast = found
            qa.shot(page, "t02b_refresh_toast")
            break
        page.wait_for_timeout(200)
    dt = time.time() - t0
    qa.log(f"Toast 出现耗时 {dt:.1f}s: {json.dumps(toast, ensure_ascii=False) if toast else '25s 未出现 ← isManual Toast 未达成'}")

    # 2) 租户正常切换 all → default
    page.select_option("#analyticsTenantSelect", "default")
    page.wait_for_timeout(4000)
    sel_val = page.eval_on_selector("#analyticsTenantSelect", "e => e.value")
    grey = page.eval_on_selector("#analyticsGreyBody", "e => e.innerText.trim().slice(0,80)")
    qa.log(f"切到 default: select={sel_val} 灰测区={grey!r}")
    qa.shot(page, "t02b_tenant_default")

    # 3) 快速连续切换（竞态猎取）：all→default→all→default 各 350ms
    for v in ("all", "default", "all", "default"):
        page.select_option("#analyticsTenantSelect", v)
        page.wait_for_timeout(350)
    page.wait_for_timeout(5000)
    final_sel = page.eval_on_selector("#analyticsTenantSelect", "e => e.value")
    dist_head = page.eval_on_selector(
        "#analyticsEventDistBody", "e => e.innerText.trim().slice(0,60)")
    loading = page.eval_on_selector(
        "#adminGreySamplesBody", "e => e.innerText.slice(0,40)")
    qa.log(f"快速切换后: select={final_sel} 事件区头部={dist_head!r} 抽样流={loading!r}")
    qa.shot(page, "t02b_after_rapid_switch")

    qa.log("4xx/5xx 响应: " + (json.dumps(hits, ensure_ascii=False) if hits else "无"))


if __name__ == "__main__":
    sys.exit(run("t02b_analytics_deep", main))
