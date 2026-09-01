# -*- coding: utf-8 -*-
"""T07 输入边界：手工单空提交 / 新建餐品空必填 / 负数售价。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def toasts(page):
    return page.eval_on_selector_all(
        ".app-toast", "els => els.map(e => ({cls: e.className.replace('app-toast ', ''), t: e.innerText.trim().slice(0,80)}))")


def main(page, qa):
    goto_home(page)

    # 1) 新建手工单：直接提交空表单
    page.click("#btnNewManualEntry")
    page.wait_for_timeout(900)
    fields = page.eval_on_selector_all(
        ".modal-backdrop input:visible, .modal-backdrop select:visible, .modal-backdrop button:visible",
        "els => els.slice(0, 20).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, t: (e.innerText || e.placeholder || '').trim().slice(0, 12)}))",
    )
    qa.log("手工单弹窗: " + json.dumps(fields, ensure_ascii=False))
    save = page.locator(".modal-backdrop:visible button", has_text="保存").first
    if save.count():
        save.click()
        page.wait_for_timeout(900)
        qa.log(f"空表单提交反馈: {json.dumps(toasts(page), ensure_ascii=False)}")
        qa.shot(page, "t07_manual_empty_submit")
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)
    # 若仍有弹窗，点关闭
    close = page.locator(".modal-backdrop:visible .modal-close-btn")
    if close.count():
        close.first.click()
        page.wait_for_timeout(500)

    # 2) 新建餐品：全空提交
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1200)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1200)
    page.click("#btnOpenAddDishModal")
    page.wait_for_timeout(700)
    page.click(".modal-backdrop:visible >> text=保存餐品配方")
    page.wait_for_timeout(900)
    qa.log(f"餐品空提交反馈: {json.dumps(toasts(page), ensure_ascii=False)}")
    qa.shot(page, "t07_dish_empty_submit")

    # 3) 填名称 + 负数售价
    page.fill("#dishModalName", "QA负价测试")
    page.fill("#dishModalPrice", "-5")
    page.click(".modal-backdrop:visible >> text=保存餐品配方")
    page.wait_for_timeout(1000)
    qa.log(f"负价提交反馈: {json.dumps(toasts(page), ensure_ascii=False)}")
    qa.shot(page, "t07_dish_negative_price")

    # 数据库不应出现该餐品 → 取消关闭
    cancel = page.locator(".modal-backdrop:visible button", has_text="取消").first
    if cancel.count():
        cancel.click()
        page.wait_for_timeout(500)


if __name__ == "__main__":
    sys.exit(run("t07_boundaries", main))
