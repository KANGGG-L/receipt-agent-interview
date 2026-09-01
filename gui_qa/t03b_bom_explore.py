# -*- coding: utf-8 -*-
"""T03b BOM 配方库子视图探查：定位餐品 CRUD 与分类管理入口。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1500)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1800)
    qa.shot(page, "t03b_bom_view")

    btns = page.eval_on_selector_all(
        "#tab-dishes button",
        "els => els.slice(0, 40).map(e => ({id: e.id, t: e.innerText.trim().slice(0,18), v: !!(e.offsetParent)}))",
    )
    qa.log("BOM 按钮: " + json.dumps([b for b in btns if b["v"]], ensure_ascii=False))

    rows = page.eval_on_selector_all(
        "#tab-dishes table tbody tr",
        "els => ({count: els.length, first: els[0] ? els[0].innerText.trim().replace(/\\n/g,' | ').slice(0,120) : null})",
    )
    qa.log("BOM 表: " + json.dumps(rows, ensure_ascii=False))

    # 全页可见弹窗/菜单容器（分类管理弹窗与下拉菜单触发点）
    cat = page.eval_on_selector_all(
        "[id*='ategory']",
        "els => els.map(e => ({tag: e.tagName.toLowerCase(), id: e.id, v: !!(e.offsetParent), t: e.innerText.trim().slice(0,20)}))",
    )
    qa.log("全页分类元素: " + json.dumps(cat, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(run("t03b_bom_explore", main))
