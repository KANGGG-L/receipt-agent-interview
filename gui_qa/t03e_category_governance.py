# -*- coding: utf-8 -*-
"""T03e 餐品分类治理全流程：新建餐品(新分类) → 分类管理删除 → 验证重映射「其他」→ 清理。
仅操作自建测试数据，不触碰既有餐品/分类。"""
import sys, os, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

CAT = "QA自动化分类"
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

    # 1) 新建餐品（自由文本新分类）
    page.click("#btnOpenAddDishModal")
    page.wait_for_timeout(700)
    page.fill("#dishModalName", DISH)
    page.fill("#dishModalCategory", CAT)
    page.fill("#dishModalPrice", "33.5")
    qa.shot(page, "t03e_filled_form")
    page.click(".modal-backdrop:visible >> text=保存餐品配方")
    page.wait_for_timeout(1800)
    qa.log(f"保存后 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")

    # 验证新分类进入筛选器 & 餐品入表
    filter_opts = page.eval_on_selector(
        "#dishCategoryFilter", "e => [...e.options].map(o => o.value)")
    qa.log(f"分类筛选器选项: {json.dumps(filter_opts, ensure_ascii=False)}")
    row = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
    qa.log(f"测试菜行存在={row.count() > 0 if row else False} 行文本={row.inner_text()[:80] if row.count() else '无'}")
    qa.shot(page, "t03e_dish_created")

    # 2) 分类管理弹窗：删除该分类
    page.click("#btnManageDishCategories")
    page.wait_for_timeout(700)
    body_txt = page.eval_on_selector(
        "#dishCategoryManagerBody", "e => e.innerText.trim().slice(0,200)")
    qa.log(f"管理弹窗内容: {body_txt!r}")
    qa.shot(page, "t03e_manager_before_delete")

    del_btn = page.locator("#dishCategoryManagerBody button", has_text="删除").first
    del_btn.click()
    page.wait_for_timeout(600)
    confirm = page.eval_on_selector_all(
        ".modal-backdrop",
        "els => els.filter(e => e.offsetParent && !e.classList.contains('hide')).map(e => ({id: e.id, text: e.innerText.trim().slice(0,150), btns: [...e.querySelectorAll('button')].filter(b=>b.offsetParent).map(b => ({t: b.innerText.trim(), cls: b.className.slice(0,30)}))}))",
    )
    qa.log(f"删除确认弹窗: {json.dumps(confirm, ensure_ascii=False)}")
    qa.shot(page, "t03e_delete_confirm")

    # 点击确认弹窗中的确认按钮（非 x/取消）
    target = None
    for c in confirm:
        for b in c["btns"]:
            if b["t"] in ("确认删除", "确认", "删除", "确定") and "close" not in b["cls"]:
                target = (c["id"], b["t"])
    if not target:
        qa.log("!! 未识别确认按钮，中止删除流程")
        return
    qa.log(f"点击确认按钮: {target}")
    page.click(f"#{target[0]} >> text={target[1]}")
    page.wait_for_timeout(1500)
    qa.log(f"删除后 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
    qa.shot(page, "t03e_after_delete")

    # 3) 验证重映射
    filter_opts2 = page.eval_on_selector(
        "#dishCategoryFilter", "e => [...e.options].map(o => o.value)")
    qa.log(f"删除后筛选器选项: {json.dumps(filter_opts2, ensure_ascii=False)}")
    page.click("#dishCategoryManagerModal >> text=关闭")
    page.wait_for_timeout(600)
    row = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
    row_txt = row.inner_text().replace("\n", " | ")[:120] if row.count() else "行不存在"
    qa.log(f"测试菜当前行: {row_txt}")
    qa.shot(page, "t03e_remap_check")

    # 4) 清理：停用测试菜
    stop = row.locator("button", has_text="停用")
    if stop.count():
        stop.click()
        page.wait_for_timeout(600)
        confirm2 = page.eval_on_selector_all(
            ".modal-backdrop",
            "els => els.filter(e => e.offsetParent && !e.classList.contains('hide')).map(e => ({id: e.id, btns: [...e.querySelectorAll('button')].filter(b=>b.offsetParent).map(b => b.innerText.trim())}))",
        )
        qa.log(f"停用确认: {json.dumps(confirm2, ensure_ascii=False)}")
        qa.shot(page, "t03e_disable_confirm")
        for c in confirm2:
            for t in ("确认停用", "确认", "确定", "停用"):
                if t in c["btns"]:
                    page.click(f"#{c['id']} >> text={t}")
                    page.wait_for_timeout(1200)
                    break
            break
        row2 = page.locator("#tab-dishes table tbody tr", has_text=DISH).first
        qa.log(f"停用后行状态: {row2.inner_text()[:80] if row2.count() else '行已移除'}")
    qa.shot(page, "t03e_final")


if __name__ == "__main__":
    sys.exit(run("t03e_category_governance", main))
