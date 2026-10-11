# -*- coding: utf-8 -*-
"""F1a 浏览器回归共用支撑：自起服务（空库、空闲端口）、浏览器夹具、造单辅助。"""
import json
import os
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from _playwright_server import start_pinned_server  # noqa: E402

SHOT_DIR = os.environ.get(
    "F1A_SHOT_DIR",
    "/home/user/.indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/artifacts/review/screens/")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Site:
    def __init__(self, handle, port, workdir):
        self.handle = handle
        self.port = port
        self.url = "http://127.0.0.1:%d" % port
        self.workdir = workdir

    def api(self, method, path, body=None, role="owner"):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.url + path, data=data, method=method)
        req.add_header("X-Role", role)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        for attempt in range(30):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return r.status, json.loads(r.read().decode("utf-8") or "null")
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8") or "null"
                try:
                    return e.code, json.loads(raw)
                except ValueError:
                    return e.code, {"raw": raw}
            except urllib.error.URLError:
                time.sleep(1)
        raise RuntimeError("服务一直连不上：" + path)

    def make_receipt(self, items=None, total=None, supplier="测试供应商", settlement="cash",
                     status="edited", role="staff", date="2026-10-05", **extra):
        items = items or [{"name": "菜心", "quantity": 2, "unit": "斤", "unit_price": 10, "amount": 20}]
        if total is None:
            total = sum(i["amount"] for i in items)
        body = {"supplier_name": supplier, "date": date, "total_amount": total,
                "settlement_type": settlement, "items": items, "version": None,
                "source": "manual", **extra}
        code, ret = self.api("POST", "/api/save_edited", body, role=role)
        assert code == 200 and ret.get("status") == "success", (code, ret)
        return ret.get("receipt_id") or ret.get("id")


@pytest.fixture(scope="module")
def site():
    workdir = tempfile.mkdtemp(prefix="f1a_ui_")
    port = _free_port()
    db_path = os.path.join(workdir, "f1a.db")
    handle = start_pinned_server(
        port, workdir, db_path,
        extra_env={"RAG_DIR": os.path.join(workdir, "rag")})
    try:
        yield Site(handle, port, workdir)
    finally:
        if handle.proc.poll() is not None:
            print("服务进程提前退出，日志尾部：\n" + handle.read_log()[-3000:])
        handle.stop()


@pytest.fixture(scope="module")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        try:
            yield b
        finally:
            b.close()


class Page:
    """一次浏览器会话：带角色、视口，收集 pageerror 与 console error。"""

    def __init__(self, browser, site, role="staff", width=1440, height=900, clock=False):
        self.ctx = browser.new_context(viewport={"width": width, "height": height})
        self.page = self.ctx.new_page()
        self.errors = []
        self.page.on("pageerror", lambda e: self.errors.append("pageerror: %s" % e))
        self.page.add_init_script("if (!localStorage.getItem('demo_role')) localStorage.setItem('demo_role', %r);" % role)
        if clock:
            self.page.clock.install()
        self.site = site
        self.role = role

    def open(self, tab="tab-scan"):
        self.page.goto(self.site.url + "/")
        self.page.wait_for_load_state("load")
        self.page.wait_for_timeout(900)
        self.page.evaluate(
            "t => { const b = document.querySelector('.sidebar-nav .sidebar-btn[data-target=\"' + t + '\"]'); if (b) b.click(); }", tab)
        self.page.wait_for_timeout(300)
        return self.page

    def shot(self, name):
        os.makedirs(SHOT_DIR, exist_ok=True)
        self.page.screenshot(path=os.path.join(SHOT_DIR, name))

    def close(self):
        self.ctx.close()


@pytest.fixture
def make_page(browser, site):
    made = []

    def _make(role="staff", width=1440, height=900, clock=False):
        p = Page(browser, site, role, width, height, clock)
        made.append(p)
        return p

    yield _make
    for p in made:
        p.close()


def make_image(path, kind="receipt"):
    """kind=receipt：画质正常；kind=dark：纯暗图，画质预检不合格。"""
    from PIL import Image, ImageDraw
    if kind == "dark":
        Image.new("RGB", (800, 1000), (6, 6, 6)).save(path, quality=90)
        return path
    import random
    random.seed(3)
    im = Image.new("RGB", (900, 1200), "white")
    d = ImageDraw.Draw(im)
    for i in range(60):
        x, y = random.randint(20, 700), 20 + i * 18
        d.rectangle([x, y, x + random.randint(40, 180), y + 9], fill=(random.randint(0, 80),) * 3)
    im.save(path, quality=92)
    return path
