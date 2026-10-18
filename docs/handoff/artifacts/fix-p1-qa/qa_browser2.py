# -*- coding: utf-8 -*-
"""FIX-P1 QA browser verification (attempt 2: robust login)."""
import json
import os
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:15151"
OUT = "/tmp/fix-p1-qa/evidence"
os.makedirs(OUT, exist_ok=True)
results = {}


def snap(page, name):
    page.screenshot(path=os.path.join(OUT, name))


with sync_playwright() as p:
    browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=False,
                                args=["--no-sandbox", "--disable-dev-shm-usage"])
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.set_default_timeout(15000)

    pg.goto(BASE + "/")
    pg.wait_for_load_state("networkidle")
    pg.wait_for_selector("#authLoginEmail", state="visible", timeout=10000)
    pg.fill("#authLoginEmail", "boss@demo.hk")
    pg.fill("#authLoginPassword", "boss123")
    pg.click("#authLoginSubmit")
    pg.wait_for_load_state("networkidle")
    time.sleep(2)
    snap(pg, "a01-00-logged-in.png")
    login_gone = not pg.locator("#authLoginEmail").is_visible()
    print("login panel gone:", login_gone)

    pg.evaluate("() => { const b = document.querySelector('.sidebar-btn[data-target=\"tab-dishes\"]')||document.querySelector('.nav-tab[data-target=\"tab-dishes\"]'); if(b) b.click(); }")
    pg.wait_for_timeout(800)
    pg.evaluate("typeof loadDailyConsumption === 'function' && loadDailyConsumption()")
    pg.wait_for_selector(".dish-consume-qty-input", timeout=15000)
    qty = pg.locator(".dish-consume-qty-input").first
    qty.fill("1")
    btn = pg.locator("#btnSubmitDailyConsumeBatch")
    btn.click()
    pg.wait_for_function(
        "() => { const m = document.getElementById('costTraceModal');"
        " return m && !m.classList.contains('hide'); }", timeout=10000)
    snap(pg, "a01-01-modal-open.png")
    modal_cls_open = pg.locator("#costTraceModal").get_attribute("class")
    rect_open = pg.evaluate("() => document.getElementById('costTraceModal').getBoundingClientRect().toJSON()")
    pg.locator("#costTraceModal .modal-close-btn").first.click()
    pg.wait_for_timeout(500)
    modal_cls_closed = pg.locator("#costTraceModal").get_attribute("class")
    snap(pg, "a01-02-modal-closed.png")
    click_ok = True
    err = ""
    try:
        pg.locator(".dish-consume-qty-input").first.fill("1", timeout=3000)
        btn.click(timeout=3000)
        pg.wait_for_timeout(1500)
    except Exception as e:
        click_ok = False
        err = str(e)[:200]
    residual = pg.evaluate(
        "() => Array.from(document.querySelectorAll('.modal-backdrop:not(.hide)'))"
        ".map(el => ({id: el.id, display: getComputedStyle(el).display}))")
    results["a01"] = {
        "modal_class_open": modal_cls_open,
        "modal_rect_open": rect_open,
        "modal_class_closed": modal_cls_closed,
        "second_click_ok": click_ok,
        "second_click_err": err,
        "visible_backdrops_after_close": residual,
    }
    try:
        if "hide" not in (pg.locator("#costTraceModal").get_attribute("class") or ""):
            pg.locator("#costTraceModal .modal-close-btn").first.click()
            pg.wait_for_timeout(400)
    except Exception:
        pass
    snap(pg, "a01-03-final.png")

    b001 = {}
    for w, h in ((1440, 900), (768, 1024), (390, 844)):
        pg.set_viewport_size({"width": w, "height": h})
        pg.evaluate("() => { const b = document.querySelector('.sidebar-btn[data-target=\"tab-report\"]')||document.querySelector('.nav-tab[data-target=\"tab-report\"]'); if(b) b.click(); }")
        pg.wait_for_timeout(800)
        info = pg.evaluate("""
            () => {
                const out = [];
                document.querySelectorAll('#tab-report .report-date-cluster input[type=date]').forEach(inp => {
                    const r = inp.getBoundingClientRect();
                    const el = document.elementFromPoint(Math.min(r.x + r.width/2, window.innerWidth - 1), r.y + r.height/2);
                    out.push({id: inp.id, x: Math.round(r.x), right: Math.round(r.right), width: Math.round(r.width),
                              hit_self: el === inp,
                              visible: r.width > 20 && r.right <= window.innerWidth + 0.5 && r.x >= -0.5});
                });
                return {vw: window.innerWidth, inputs: out};
            }
        """)
        snap(pg, "b001-%d.png" % w)
        b001["vp%d" % w] = info
    results["b001"] = b001

    browser.close()

with open(os.path.join(OUT, "a01_b001.json"), "w") as f:
    json.dump(results, f, ensure_ascii=False, indent=2)
print(json.dumps(results, ensure_ascii=False, indent=2))
