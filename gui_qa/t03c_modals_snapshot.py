# -*- coding: utf-8 -*-
"""T03c 弹窗结构快照：分类管理弹窗 + 新建餐品弹窗 + 分类下拉菜单（只读枚举）。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1200)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1500)

    # 分类管理弹窗
    page.click("#btnManageDishCategories")
    page.wait_for_timeout(800)
    st = page.eval_on_selector(
        "#dishCategoryManagerModal", "e => ({open: !e.classList.contains('hide'), text: e.innerText.trim().slice(0,300)})")
    qa.log("管理弹窗: " + json.dumps(st, ensure_ascii=False))
    qa.shot(page, "t03c_manager_modal")
    inner = page.eval_on_selector_all(
        "#dishCategoryManagerBody input, #dishCategoryManagerBody button, #dishCategoryManagerBody select",
        "els => els.map(e => ({tag: e.tagName.toLowerCase(), id: e.id, t: (e.innerText || e.placeholder || e.value || '').trim().slice(0,20)}))",
    )
    qa.log("管理弹窗内部控件: " + json.dumps(inner, ensure_ascii=False))

    # 关闭管理弹窗（找关闭按钮）
    close_btns = page.eval_on_selector_all(
        "#dishCategoryManagerModal button",
        "els => els.map(e => ({t: e.innerText.trim(), cls: e.className.slice(0,40)}))",
    )
    qa.log("管理弹窗按钮全集: " + json.dumps(close_btns, ensure_ascii=False))

    # 新建餐品弹窗
    page.click("#btnOpenAddDishModal")
    page.wait_for_timeout(800)
    st2 = page.eval_on_selector_all(
        "[id*='dishModal'], #dishModal",
        "els => els.map(e => ({tag: e.tagName.toLowerCase(), id: e.id, v: !!(e.offsetParent), t: (e.innerText||e.placeholder||'').trim().slice(0,18)}))",
    )
    qa.log("新建餐品弹窗元素: " + json.dumps(st2, ensure_ascii=False))
    qa.shot(page, "t03c_add_dish_modal")

    # 分类下拉菜单触发：点击分类输入框
    cat_input = page.locator("#dishModalCategory")
    if cat_input.count():
        cat_input.click()
        page.wait_for_timeout(500)
        menu = page.eval_on_selector(
            "#dishCategoryDropdownMenu",
            "e => ({open: !!(e.offsetParent), items: e.innerText.trim().slice(0,120), childCount: e.children.length})")
        qa.log("分类下拉菜单: " + json.dumps(menu, ensure_ascii=False))
        qa.shot(page, "t03c_category_menu")


if __name__ == "__main__":
    sys.exit(run("t03c_modals_snapshot", main))
