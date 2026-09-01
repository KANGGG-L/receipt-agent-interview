# -*- coding: utf-8 -*-
"""T03g 列表/卡片视图切换一致性 + 停用流程 + 搜索过滤。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

DISH = "QA自动化测试菜"


def toast_texts(page):
    return page.eval_on_selector_all(
        ".app-toast", "els => els.map(e => e.innerText.trim().slice(0,120))")


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1200)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1500)

    # 1) 卡片视图当前徽章
    card = page.locator("#tab-dishes >> text=" + DISH).first
    qa.log(f"卡片视图含测试菜={card.count() > 0}")

    # 2) 切到列表视图
    page.click("#btnToggleDishView")
    page.wait_for_timeout(900)
    qa.shot(page, "t03g_list_view")
    rows = page.eval_on_selector_all(
        "#tab-dishes table tbody tr",
        "els => els.filter(e => e.offsetParent).map(e => e.innerText.trim().replace(/\\n/g,' | ').slice(0,100))",
    )
    qa.log("列表视图可见行: " + json.dumps(rows, ensure_ascii=False))

    # 3) 切回卡片视图
    page.click("#btnToggleDishView")
    page.wait_for_timeout(900)

    # 4) 搜索过滤
    page.fill("#tab-dishes input[placeholder*='搜索餐品']", DISH)
    page.wait_for_timeout(900)
    cards = page.eval_on_selector_all(
        "#tab-dishes >> text=" + DISH, "els => els.filter(e => e.offsetParent).length")
    qa.log(f"搜索 '{DISH}' 后可见匹配={cards}")
    qa.shot(page, "t03g_search_filtered")
    page.fill("#tab-dishes input[placeholder*='搜索餐品']", "")
    page.wait_for_timeout(700)

    # 5) 停用测试菜（卡片视图可见按钮）
    card_zone = page.locator("#tab-dishes div", has_text=DISH).last
    stop_btns = page.locator("#tab-dishes button:visible", has_text="停用")
    n = stop_btns.count()
    qa.log(f"可见停用按钮数={n}")
    # 点第一个（对应排序第一张卡 = QA菜）
    stop_btns.first.click()
    page.wait_for_timeout(700)
    confirm_btn = page.locator("text=确认停用").first
    if confirm_btn.count() and confirm_btn.is_visible():
        qa.shot(page, "t03g_disable_confirm")
        confirm_btn.click()
        page.wait_for_timeout(1500)
        qa.log(f"停用 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
    else:
        qa.log("停用无确认框")
    qa.shot(page, "t03g_after_disable")
    still = page.locator("#tab-dishes >> text=" + DISH).count()
    qa.log(f"停用后测试菜元素数={still}")


if __name__ == "__main__":
    sys.exit(run("t03g_view_toggle_disable", main))
