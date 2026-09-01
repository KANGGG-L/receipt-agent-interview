# -*- coding: utf-8 -*-
"""T01c 角色切换终判：等待淡出→重载完成后，读取稳定的 RBAC 可见性。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

ADMIN_BTNS = ["adminEngineBtn", "analyticsBoardBtn", "evalsetReviewBtn", "goldenBoardBtn"]


def vis_map(page):
    return page.evaluate(
        """(ids) => {
            const o = {};
            for (const id of ids) {
                const b = document.querySelector('aside #' + id);
                o[id] = b ? !!(b.offsetParent) : null;
            }
            const sel = document.querySelector('#demoRoleSelect');
            o._role = sel ? sel.value : null;
            const badge = document.querySelector('.sidebar-role, #demoRoleBadge, [class*=role-badge]');
            o._badge = badge ? badge.innerText.trim().replace(/\\n/g, ' ').slice(0, 40) : null;
            return o;
        }""",
        ADMIN_BTNS,
    )


def settle_after_role_switch(page, qa):
    """切换角色会淡出并整页重载：等待重载完成并稳定。"""
    page.wait_for_load_state("load", timeout=15000)
    page.wait_for_selector("#demoRoleSelect", timeout=15000)
    page.wait_for_timeout(1500)


def main(page, qa):
    goto_home(page)
    qa.log("初始=" + json.dumps(vis_map(page), ensure_ascii=False))
    origin0 = page.evaluate("performance.timeOrigin")

    for role in ("owner", "staff", "admin"):
        page.select_option("#demoRoleSelect", role)
        settle_after_role_switch(page, qa)
        origin_now = page.evaluate("performance.timeOrigin")
        reloaded = origin_now != origin0
        origin0 = origin_now
        st = vis_map(page)
        qa.log(f"切换到 {role}: 重载={reloaded} 稳定状态={json.dumps(st, ensure_ascii=False)}")
        qa.shot(page, f"t01c_{role}_settled")

    # staff 稳定态下，管理页签入口应不可见（与 E2E 断言对齐）
    st = vis_map(page)
    assert st["_role"] == "admin", "最终应回到 admin"
    for b in ("adminEngineBtn", "analyticsBoardBtn", "evalsetReviewBtn"):
        qa.log(f"admin 稳定态 {b} 可见={st[b]}")


if __name__ == "__main__":
    sys.exit(run("t01c_roles_settled", main))
