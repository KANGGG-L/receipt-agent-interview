# -*- coding: utf-8 -*-
"""T01b 角色切换复测：区分「切换即生效」vs「需重载生效」vs「失效」。
只读检测页面是否发生重载（performance.timeOrigin 变化）；0.5/1.5/3.5s 三时刻采样可见性。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

ADMIN_BTNS = ["adminEngineBtn", "analyticsBoardBtn", "evalsetReviewBtn"]
ROLES = ["owner", "staff", "admin"]


def vis_map(page):
    return page.eval_on_selector(
        "aside",
        """aside => {
            const o = {};
            for (const id of %s) {
                const b = aside.querySelector('#' + id);
                o[id] = b ? !!(b.offsetParent) : null;
            }
            o._scanTab = !!(document.querySelector('#tab-scan')?.offsetParent);
            return o;
        }""" % json.dumps(ADMIN_BTNS),
    )


def time_origin(page):
    return page.evaluate("performance.timeOrigin")


def main(page, qa):
    goto_home(page)
    qa.log("初始角色=" + page.eval_on_selector("#demoRoleSelect", "e=>e.value")
           + " 初始可见性=" + json.dumps(vis_map(page)))
    origin0 = time_origin(page)

    for role in ROLES:
        page.select_option("#demoRoleSelect", role)
        samples = {}
        for delay in (500, 1500, 3500):
            page.wait_for_timeout(delay if delay == 500 else (delay - (500 if delay == 1500 else 1500)))
            samples[delay] = vis_map(page)
        origin_now = time_origin(page)
        reloaded = origin_now != origin0
        origin0 = origin_now
        qa.log(f"切换到 {role}: 重载发生={reloaded} 采样={json.dumps(samples, ensure_ascii=False)}")
        qa.shot(page, f"t01b_{role}_settled")
        # 角色 select 当前值是否保持
        qa.log(f"  select 当前值={page.eval_on_selector('#demoRoleSelect', 'e=>e.value')}")

    # 额外：staff 状态下直接观察 body 上是否有 role 相关类与灰测横幅
    page.select_option("#demoRoleSelect", "staff")
    page.wait_for_timeout(3500)
    body_cls = page.eval_on_selector("body", "e=>e.className")
    qa.log(f"staff 稳定后 body.className={body_cls!r} 可见性={json.dumps(vis_map(page))}")
    qa.shot(page, "t01b_staff_final")


if __name__ == "__main__":
    sys.exit(run("t01b_roles_recheck", main))
