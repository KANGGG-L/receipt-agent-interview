# -*- coding: utf-8 -*-
"""T01 角色切换与 RBAC 隔离：纯 GUI 切换 admin/owner/staff，观察侧边栏可见性。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

ADMIN_BTNS = ["adminEngineBtn", "analyticsBoardBtn", "evalsetReviewBtn"]


def sidebar_state(page):
    return page.eval_on_selector_all(
        "aside button",
        "els => els.map(e => ({id: e.id || e.innerText.trim().slice(0,8), t: e.innerText.trim().slice(0,14), v: !!(e.offsetParent)}))",
    )


def main(page, qa):
    goto_home(page)
    qa.log("初始角色=" + page.eval_on_selector("#demoRoleSelect", "e => e.value"))

    # --- 切到 owner ---
    page.select_option("#demoRoleSelect", "owner")
    page.wait_for_timeout(1500)
    st = sidebar_state(page)
    qa.log("owner 视角侧边栏: " + json.dumps(st, ensure_ascii=False))
    qa.shot(page, "t01_role_owner")
    vis = {b["id"]: b["v"] for b in st if b["id"]}
    for b in ADMIN_BTNS:
        qa.log(f"owner 视角 {b} 可见={vis.get(b)}")

    # --- 切到 staff ---
    page.select_option("#demoRoleSelect", "staff")
    page.wait_for_timeout(1500)
    st = sidebar_state(page)
    qa.log("staff 视角侧边栏: " + json.dumps(st, ensure_ascii=False))
    qa.shot(page, "t01_role_staff")
    vis = {b["id"]: b["v"] for b in st if b["id"]}
    for b in ADMIN_BTNS:
        qa.log(f"staff 视角 {b} 可见={vis.get(b)}")

    # staff 视角下主工作元素是否正常
    qa.log("staff 视角上传区可见: " + str(page.locator("#tab-scan").is_visible()))

    # --- 切回 admin ---
    page.select_option("#demoRoleSelect", "admin")
    page.wait_for_timeout(1500)
    vis = {b["id"]: b["v"] for b in sidebar_state(page) if b["id"]}
    qa.log("回到 admin: " + json.dumps({b: vis.get(b) for b in ADMIN_BTNS}, ensure_ascii=False))
    qa.shot(page, "t01_role_back_admin")

    # 角色徽章文本联动
    badge = page.eval_on_selector_all(
        "[class*=role], #demoRoleBadge, .sidebar-role",
        "els => els.map(e => e.innerText.trim().slice(0,30)).filter(t => t)",
    )
    qa.log("角色徽章元素: " + json.dumps(badge, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(run("t01_roles", main))
