# -*- coding: utf-8 -*-
"""F1b Playwright 用例共享帮助：独立端口 + 全新空库的服务、接口造数、浏览器上下文、截图。

端口默认 15048（F1B_TEST_PORT 可覆盖；15047 留给开发联调服务）。库是临时目录里的空库，用例造的数据互不污染。
"""

import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

playwright_api = pytest.importorskip("playwright.sync_api")

from _playwright_server import start_pinned_server  # noqa: E402

PORT = int(os.environ.get("F1B_TEST_PORT", "15048"))
BASE = "http://127.0.0.1:%d" % PORT
SHOT_DIR = os.environ.get(
    "F1B_SHOT_DIR",
    "/home/user/.indent/01a12a4e-a627-78c0-a7f6-25a1147af3ec/artifacts/review/screens")

DESKTOP = {"width": 1440, "height": 900}
MOBILE = {"width": 390, "height": 844}


def start_server():
    data_dir = tempfile.mkdtemp(prefix="f1b_pw_")
    db_path = os.path.join(data_dir, "f1b.db")
    rag = os.path.join(data_dir, "rag")
    os.makedirs(rag, exist_ok=True)
    handle = start_pinned_server(PORT, data_dir, db_path, extra_env={"RAG_DIR": rag})
    return handle, data_dir


def stop_server(handle, data_dir):
    handle.stop()
    shutil.rmtree(data_dir, ignore_errors=True)


def api(method, path, body=None, role="owner", form=None):
    headers = {"X-Role": role}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    path = urllib.parse.quote(path, safe="/?&=:%")
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, {"raw": raw.decode("utf-8", "replace")}


def seed_receipt(supplier="测试供应商", date="2026-10-05", items=None, total=None, settlement="credit",
                 approve=True, pay_date=None):
    items = items or [{"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 5, "amount": 50}]
    total = total if total is not None else round(sum(i["amount"] for i in items), 2)
    st, body = api("POST", "/api/save_edited", {
        "supplier_name": supplier, "date": date, "total_amount": total,
        "settlement_type": settlement, "items": items, "source": "manual",
    }, role="staff")
    assert st == 200 and body.get("status") == "success", body
    rid = body.get("receipt_id") or body.get("id")
    if approve:
        st, detail = api("GET", "/api/receipt/%d" % rid)
        version = (detail.get("data") or detail).get("version")
        st, ap = api("POST", "/api/receipt/%d/approve" % rid, {"version": version})
        assert st == 200, ap
    if pay_date:
        st, pd = api("POST", "/api/receipt/%d/pay_date" % rid, {"expected_pay_date": pay_date})
        assert st == 200, pd
    return rid


def new_page(pw, role="owner", viewport=DESKTOP, lang=None, tab=None):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport=viewport)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
    page.on("console", lambda m: errors.append("console: %s" % m.text) if m.type == "error" else None)
    page.add_init_script("localStorage.setItem('demo_role', '%s')" % role)
    page.errors = errors
    page._browser = browser
    return page


def open_app(page, tab=None):
    page.goto(BASE + "/", wait_until="load")
    page.wait_for_timeout(1500)
    if tab:
        go_tab(page, tab)


def go_tab(page, tab):
    page.evaluate("t => document.querySelector('.sidebar-btn[data-target=\"' + t + '\"]').click()", tab)
    page.wait_for_timeout(700)


def shot(page, name):
    os.makedirs(SHOT_DIR, exist_ok=True)
    page.screenshot(path=os.path.join(SHOT_DIR, "F1b-" + name + ".png"))


def close(page):
    try:
        page._browser.close()
    except Exception:
        pass
