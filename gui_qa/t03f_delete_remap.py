# -*- coding: utf-8 -*-
"""T03f 删除分类→重映射「其他」终验 + 清理测试数据。确认按钮定位改为全页 text。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

CAT = "QA自动化分类"
DISH = "QA自动化测试菜"


def toast_texts(page):
    return page.eval_on_selector_all(
        ".app-toast", "els => els.map(e => e.innerText.trim().slice(0,140))")


def main(page, qa):
    goto_home(page)
    page.click("aside >> text=餐品与消耗")
    page.wait_for_timeout(1200)
    page.click("text=餐品 BOM 配方库")
    page.wait_for_timeout(1500)

    # 删除前：测试菜行文本
    row = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
    qa.log(f"删除前行: {row.inner_text().replace(chr(10), ' | ')[:100]}")

    # 管理分类 → 删除 QA自动化分类
    page.click("#btnManageDishCategories")
    page.wait_for_timeout(700)
    target_row = page.locator("#dishCategoryManagerBody >> text=" + CAT).first
    qa.log(f"目标分类行可见={target_row.is_visible()}")
    del_btn = page.locator("#dishCategoryManagerBody button.btn-delete-dish-cat").first
    del_btn.click()
    page.wait_for_timeout(600)
    confirm_btn = page.locator("text=确认删除").first
    qa.log(f"确认删除按钮可见={confirm_btn.is_visible()}")
    qa.shot(page, "t03f_confirm_visible")
    confirm_btn.click()
    page.wait_for_timeout(1600)
    qa.log(f"删除后 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
    qa.shot(page, "t03f_after_delete")

    # 弹窗中该分类应消失
    still = page.locator("#dishCategoryManagerBody >> text=" + CAT).count()
    qa.log(f"管理弹窗中分类残留={still}")
    page.click("#dishCategoryManagerModal >> text=关闭")
    page.wait_for_timeout(600)

    # UI 重映射验证：筛选器选项 + 餐品行
    filter_opts = page.eval_on_selector(
        "#dishCategoryFilter", "e => [...e.options].map(o => o.value)")
    qa.log(f"删除后筛选器: {json.dumps(filter_opts, ensure_ascii=False)}")
    row = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
    qa.log(f"重映射后行: {row.inner_text().replace(chr(10), ' | ')[:110] if row.count() else '行不存在'}")
    qa.shot(page, "t03f_remap_verified")

    # 清理：停用测试菜
    stop_btn = row.locator("button", has_text="停用")
    if stop_btn.count():
        stop_btn.click()
        page.wait_for_timeout(600)
        c = page.locator("text=确认停用").first
        if c.count() and c.is_visible():
            qa.shot(page, "t03f_disable_confirm")
            c.click()
            page.wait_for_timeout(1400)
            qa.log(f"停用后 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
        else:
            qa.log("停用无确认框（直接执行）")
    row2 = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
    qa.log(f"清理后行: {row2.inner_text().replace(chr(10), ' | ')[:110] if row2.count() else '行已消失'}")
    qa.shot(page, "t03f_final")


if __name__ == "__main__":
    sys.exit(run("t03f_delete_remap", main))
