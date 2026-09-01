# -*- coding: utf-8 -*-
"""GUI 黑盒测试公共驱动：真实用户级操作（click/fill/select/upload），
只读 DOM 观察与截图留证；全程收集 console error 与未捕获页面异常。
不注入任何改状态的 JS；不构造 URL 绕过页面交互。"""
import json
import os
import time
from datetime import datetime

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15010"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(ROOT, "gui-test-screenshots")
LOGS = os.path.join(ROOT, "gui-test-screenshots", "_console_logs")
os.makedirs(SHOTS, exist_ok=True)
os.makedirs(LOGS, exist_ok=True)


class QA:
    def __init__(self, module):
        self.module = module
        self.console = []      # (time, type, text)
        self.pageerrors = []   # [str]
        self.steps = []        # 测试步骤日志

    def log(self, text):
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
        self.steps.append(line)
        print(line, flush=True)

    def shot(self, page, tag):
        path = os.path.join(SHOTS, f"{tag}.png")
        page.screenshot(path=path, full_page=False)
        self.log(f"截图: {tag}.png")
        return path

    def dump(self):
        out = {
            "module": self.module,
            "console_errors": [f"{t}|{ty}|{m}" for t, ty, m in self.console],
            "page_errors": self.pageerrors,
            "steps": self.steps,
        }
        path = os.path.join(LOGS, f"{self.module}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        return path


def run(module, fn, headless=True):
    """每个测试模块一个独立会话；注册只读 console/pageerror 监听。"""
    qa = QA(module)
    pw = sync_playwright().start()
    browser = pw.chromium.launch(headless=headless)
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    page = ctx.new_page()

    def on_console(msg):
        if msg.type in ("error", "warning"):
            qa.console.append(
                (datetime.now().strftime("%H:%M:%S"), msg.type, msg.text[:300])
            )

    page.on("console", on_console)
    page.on("pageerror", lambda e: qa.pageerrors.append(str(e)[:300]))

    code = 0
    try:
        fn(page, qa)
    except Exception as e:
        qa.log(f"!! 模块异常中断: {type(e).__name__}: {e}")
        qa.shot(page, f"{module}_EXCEPTION")
        code = 1
    finally:
        qa.log(f"console error/warning 共 {len(qa.console)} 条; pageerror 共 {len(qa.pageerrors)} 条")
        for t, ty, m in qa.console:
            print(f"  [console.{ty}] {m}", flush=True)
        for e in qa.pageerrors:
            print(f"  [pageerror] {e}", flush=True)
        qa.dump()
        browser.close()
        pw.stop()
    return code


def goto_home(page):
    """入口导航（仅入口 URL，页面内一律点击导航）。"""
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector("#demoRoleSelect", timeout=15000)
    page.wait_for_timeout(1200)
