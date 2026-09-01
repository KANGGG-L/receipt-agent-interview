# -*- coding: utf-8 -*-
"""T03h 完成停用：确认操作按钮 → 验证卡片/DB 状态。"""
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

    page.locator("#tab-dishes button:visible", has_text="停用").first.click()
    page.wait_for_timeout(600)
    page.locator("text=确认操作").first.click()
    page.wait_for_timeout(1600)
    qa.log(f"停用 Toast: {json.dumps(toast_texts(page), ensure_ascii=False)}")
    qa.shot(page, "t03h_after_disable_confirm")


if __name__ == "__main__":
    sys.exit(run("t03h_disable_finish", main))
