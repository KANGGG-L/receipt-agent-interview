# -*- coding: utf-8 -*-
"""T06 剩余页签巡检：库存/归档/报表/黄金样本/GT抽检 渲染+关键交互+控制台收集。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home

TAB_BTN = {
    "inventory": "实时库存与价格",
    "archive": "供应商与归档",
    "report": "部门花销报表",
    "golden": "黄金样本",
    "evalset": "GT 抽检",
}


def main(page, qa):
    goto_home(page)
    hits = []
    page.on("response", lambda r: hits.append(f"{r.status} {r.url[-70:]}") if r.status >= 400 else None)

    for tab, label in TAB_BTN.items():
        page.click(f"aside >> text={label}")
        page.wait_for_timeout(2200)
        visible = page.evaluate(
            "(id) => !!(document.querySelector('#' + id)?.offsetParent)", f"tab-{tab}")
        body = page.evaluate(
            """(id) => {
                const t = document.querySelector('#' + id);
                if (!t) return {missing: true};
                const txt = t.innerText.trim();
                return {chars: txt.length, head: txt.slice(0, 60).replace(/\\n/g, ' | '),
                        empty: txt.length < 20};
            }""",
            f"tab-{tab}",
        )
        qa.log(f"[{label}] 可见={visible} 内容={json.dumps(body, ensure_ascii=False)}")
        qa.shot(page, f"t06_{tab}")
        page.on("pageerror", lambda e: None)

    qa.log("4xx/5xx: " + (json.dumps(hits, ensure_ascii=False) if hits else "无"))


if __name__ == "__main__":
    sys.exit(run("t06_tabs_sweep", main))
