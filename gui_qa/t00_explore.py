# -*- coding: utf-8 -*-
"""T00 页面探查：以默认身份进入首页，识别导航结构与核心功能区（只读）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    qa.log("首页加载完成, title=" + page.title())
    qa.shot(page, "t00_home_default")

    # 侧边栏导航结构（只读枚举）
    btns = page.eval_on_selector_all(
        "aside button, nav button",
        "els => els.map(e => ({id: e.id, text: e.innerText.trim().slice(0,20), visible: !!(e.offsetParent)}))",
    )
    qa.log("侧边栏按钮: " + json_dumps(btns))

    # 当前可见的 tab 区块
    tabs = page.eval_on_selector_all(
        "section.tab-content",
        "els => els.map(e => ({id: e.id, visible: !!(e.offsetParent)}))",
    )
    qa.log("tab区块: " + json_dumps(tabs))

    # 角色选择器当前值与选项
    role_opts = page.eval_on_selector(
        "#demoRoleSelect",
        "e => ({value: e.value, options: [...e.options].map(o => o.value)})",
    )
    qa.log("角色选择器: " + json_dumps(role_opts))

    # 租户选择器（若存在）
    tenants = page.eval_on_selector_all(
        "select[id*=enant]",
        "els => els.map(e => ({id: e.id, value: e.value, visible: !!(e.offsetParent), options: [...e.options].map(o=>o.value).slice(0,10)}))",
    )
    qa.log("租户选择器: " + json_dumps(tenants))


def json_dumps(o):
    import json
    return json.dumps(o, ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(run("t00_explore", main))
