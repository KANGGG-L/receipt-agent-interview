# -*- coding: utf-8 -*-
"""T04 引擎与系统配置页签：渲染/职责隔离（无抽样观测 DOM）/关键控件枚举（只读，不保存）。"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qa_common import run, goto_home


def main(page, qa):
    goto_home(page)
    page.click("#adminEngineBtn")
    page.wait_for_selector("#tab-engine", state="visible", timeout=10000)
    page.wait_for_timeout(2000)
    qa.shot(page, "t04_engine_top")

    # 职责隔离：tab-engine 内不得有抽样观测 DOM
    leaked = page.evaluate(
        """() => {
            const t = document.querySelector('#tab-engine');
            const leaks = [];
            for (const id of ['adminGreySamplesBody', 'adminGreySampleMetrics', 'analyticsTenantSelect', 'adminGreySampleModal']) {
                if (t.querySelector('#' + id)) leaks.push(id);
            }
            return leaks;
        }"""
    )
    qa.log(f"tab-engine 内观测DOM残留: {json.dumps(leaked)}  ← 应为 []")

    # 关键控件枚举
    ctrls = page.eval_on_selector_all(
        "#tab-engine select, #tab-engine input, #tab-engine button",
        "els => els.filter(e => e.offsetParent).slice(0, 30).map(e => ({tag: e.tagName.toLowerCase(), id: e.id, t: (e.innerText || e.value || '').trim().slice(0, 18)}))",
    )
    qa.log("可见控件: " + json.dumps(ctrls, ensure_ascii=False))

    # 全页滚动截图下半部分
    page.keyboard.press("End")
    page.wait_for_timeout(600)
    qa.shot(page, "t04_engine_bottom")


if __name__ == "__main__":
    sys.exit(run("t04_engine_tab", main))
