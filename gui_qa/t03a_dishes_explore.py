# -*- coding: utf-8 -*-
"""T03a 餐品与消耗页签探查：只读枚举交互元素，为治理操作建立定位事实。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(2500)
    qa.shot(page, "t03a_dishes_tab")
    qa.log("tab-dishes 可见=" + str(page.locator("#tab-dishes").is_visible()))

    # 页签内全部按钮（含 id）
    btns = page.eval_on_selector_all(
        "#tab-dishes button",
        "els => els.slice(0, 40).map(e => ({id: e.id, t: e.innerText.trim().slice(0,16), v: !!(e.offsetParent)}))",
    )
    qa.log("按钮: " + json.dumps([b for b in btns if b["v"]], ensure_ascii=False))

    # 下拉与输入
    inputs = page.eval_on_selector_all(
        "#tab-dishes select, #tab-dishes input",
        "els => els.slice(0, 25).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, ph: e.placeholder || '', v: !!(e.offsetParent)}))",
    )
    qa.log("输入/下拉: " + json.dumps([i for i in inputs if i["v"]], ensure_ascii=False))

    # 分类相关区域
    cat = page.eval_on_selector_all(
        "[id*='ategory'], [class*='category']",
        "els => els.slice(0, 20).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, cls: String(e.className).slice(0,40), t: e.innerText.trim().slice(0,24), v: !!(e.offsetParent)}))",
    )
    qa.log("分类元素: " + json.dumps([c for c in cat if c["v"]], ensure_ascii=False))

    # 餐品表格行数与样例
    rows = page.eval_on_selector_all(
        "#tab-dishes table tbody tr",
        "els => ({count: els.length, first: els[0] ? els[0].innerText.trim().slice(0,80) : null})",
    )
    qa.log("餐品表: " + json.dumps(rows, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(run("t03a_dishes_explore", main))
