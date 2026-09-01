# -*- coding: utf-8 -*-
"""T03d 新建餐品弹窗 + 分类下拉菜单结构快照（修正版：先关管理弹窗）。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1200)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1500)

    page.click("#btnOpenAddDishModal")
    page.wait_for_timeout(900)
    fields = page.eval_on_selector_all(
        ".modal-backdrop input, .modal-backdrop select, .modal-backdrop textarea",
        "els => els.filter(e => e.offsetParent).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, ph: e.placeholder || '', t: e.innerText.slice(0,14)}))",
    )
    qa.log("可见弹窗字段: " + json.dumps(fields, ensure_ascii=False))
    qa.shot(page, "t03d_add_dish_modal")

    # 分类输入框触发下拉菜单
    if page.locator("#dishModalCategory").count():
        page.click("#dishModalCategory")
        page.wait_for_timeout(600)
        menu = page.eval_on_selector(
            "#dishCategoryDropdownMenu",
            "e => ({open: !!(e.offsetParent), items: e.innerText.trim().slice(0,150), children: [...e.children].map(c => c.innerText.trim().slice(0,20))})")
        qa.log("分类下拉菜单: " + json.dumps(menu, ensure_ascii=False))
        qa.shot(page, "t03d_category_menu")
        # 点击别处收起（验证 closeDishCategoryMenuDelay 防抖）
        page.click("#dishModalName", timeout=5000) if page.locator("#dishModalName").count() else page.keyboard.press("Escape")
        page.wait_for_timeout(900)
        menu2 = page.eval_on_selector(
            "#dishCategoryDropdownMenu", "e => !!(e.offsetParent)")
        qa.log(f"点击空白后菜单收起={not menu2}")
    else:
        qa.log("!! 未找到 #dishModalCategory")


if __name__ == "__main__":
    sys.exit(run("t03d_add_dish_snapshot", main))
