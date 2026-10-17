# -*- coding: utf-8 -*-
"""Optional Playwright: submit 3 then 2; assert FIFO modal shows 售出 5 份 + table 5."""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("DEV_BASE", "http://127.0.0.1:15142")
OUT = os.environ.get(
    "UAT032_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat032-qa/logs",
)
UI_DIR = OUT.replace("/logs", "/ui")


def api(method, path, body=None, role="owner"):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, method=method,
        headers={
            "Content-Type": "application/json",
            "X-Role": role,
            "X-Email": "boss@demo.hk",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def seed():
    st, j = api("POST", "/api/inventory/skus", {
        "name": "UI牛肉UAT032", "base_unit": "kg", "category": "肉类",
        "min_stock_alert": 0,
    })
    assert st == 200, j
    sku = j["id"]
    total = 100 * 10
    st, j = api("POST", "/api/save_edited", {
        "supplier_name": "UI供应商", "date": "2026-04-01", "total_amount": total,
        "settlement_type": "credit", "currency": "HKD",
        "items": [{"name": "UI牛肉UAT032", "quantity": 100, "unit": "kg",
                   "unit_price": 10, "amount": total}],
        "version": None, "source": "manual",
    }, role="staff")
    rid = j.get("receipt_id") or j.get("id")
    st, detail = api("GET", "/api/receipt/%d" % rid)
    ver = (detail.get("data") or detail).get("version")
    st, _ = api("POST", "/api/receipt/%d/approve" % rid, {"version": ver})
    assert st == 200, _
    st, dj = api("POST", "/api/dishes", {
        "name": "UI牛肉饭UAT032", "price": 30.0,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.5, "unit": "kg"}],
    })
    assert st == 200, dj
    return dj["data"]["id"]


def main():
    from playwright.sync_api import sync_playwright
    os.makedirs(UI_DIR, exist_ok=True)
    dish_id = seed()
    today = time.strftime("%Y-%m-%d")
    result = {"verdict": "FAIL", "dish_id": dish_id, "date": today}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        # Match API role headers used by demo when AUTH_ENABLED=0
        page = context.new_page()
        page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(500)
        # Switch demo role to owner (price + consume)
        role_sel = page.locator("#demoRoleSelect")
        if role_sel.count():
            role_sel.select_option("owner")
            page.wait_for_timeout(800)
        page.locator('button.sidebar-btn[data-target="tab-dishes"]').click()
        page.wait_for_selector("#tab-dishes", timeout=10000)
        page.wait_for_timeout(1000)
        # Ensure daily subtab
        page.locator('button.dish-subtab-btn[data-subtab="subtab-dish-daily"]').click()
        page.wait_for_timeout(500)
        date_inp = page.locator("#dishConsumptionDate")
        if date_inp.count():
            date_inp.fill(today)
            page.evaluate(
                "(d) => { if (typeof onDishConsumptionDateChange === 'function') onDishConsumptionDateChange(d); }",
                today,
            )
            page.wait_for_timeout(1200)
        qty_sel = "#dishConsumeQty_%d" % dish_id
        try:
            page.wait_for_selector(qty_sel, timeout=8000)
        except Exception:
            # force reload daily list
            page.evaluate("typeof loadDailyConsumption==='function' && loadDailyConsumption()")
            page.wait_for_timeout(1500)
            page.wait_for_selector(qty_sel, timeout=8000)
        qty = page.locator(qty_sel)
        qty.fill("3")
        page.locator("#btnSubmitDailyConsumeBatch").click()
        page.wait_for_timeout(2000)
        # close first modal
        modal = page.locator("#costTraceModal")
        if modal.count() and modal.is_visible():
            page.locator("#costTraceModal button.modal-close-btn, #costTraceModal button:has-text('关闭')").first.click()
            page.wait_for_timeout(500)
        qty.fill("2")
        page.locator("#btnSubmitDailyConsumeBatch").click()
        page.wait_for_timeout(2500)
        # wait for modal content
        page.wait_for_function(
            """() => {
              const el = document.getElementById('traceDishQty');
              return el && /售出\\s*5/.test(el.innerText || '');
            }""",
            timeout=15000,
        )
        qty_text = page.locator("#traceDishQty").inner_text()
        total_text = page.locator("#traceTotalCost").inner_text()
        table_text = page.locator("#dishDailyHistoryTableBody").inner_text()
        details = page.locator("#costTraceDetailsContainer").inner_text()
        result.update({
            "traceDishQty": qty_text,
            "traceTotalCost": total_text,
            "table": table_text[:800],
            "details": details[:800],
            "qty_has_5": ("售出" in qty_text and "5" in qty_text),
            "table_has_5": ("5" in table_text),
            "details_has_two_batches": details.count("批次") >= 1 or details.count("kg") >= 1,
        })
        result["cost_has_25"] = ("25" in total_text)
        result["details_lines_ok"] = (
            details.count("kg") >= 2 or details.count("批次") >= 2 or details.count("\n") >= 2
        )
        # Do not accept table=5 alone without dialog/cost evidence
        result["verdict"] = "PASS" if (
            result["qty_has_5"] and result["table_has_5"]
            and result["cost_has_25"] and result.get("details_has_two_batches")
        ) else "FAIL"
        page.screenshot(path=os.path.join(UI_DIR, "modal-after-second.png"), full_page=True)
        browser.close()
    path = os.path.join(OUT, "ui-evidence.json")
    open(path, "w", encoding="utf-8").write(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        path = os.path.join(OUT, "ui-evidence.json")
        open(path, "w", encoding="utf-8").write(
            json.dumps({"verdict": "FAIL", "error": str(e)}, ensure_ascii=False, indent=2))
        print("FAIL:", e)
        sys.exit(1)
