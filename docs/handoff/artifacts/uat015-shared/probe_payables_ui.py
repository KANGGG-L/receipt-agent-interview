# -*- coding: utf-8 -*-
"""UAT-015 browser: payables show HKD converted + original SGD; missing FX shows unknown."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ.get("DB_PATH", "")
UI = ART / "ui"
EV = ART / "evidence"
UI.mkdir(parents=True, exist_ok=True)
EV.mkdir(parents=True, exist_ok=True)

TENANT = "uat015-ui"
TODAY = date.today().isoformat()
result = {"verdict": "FAIL", "steps": []}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print(("PASS" if ok else "FAIL"), name, json.dumps(detail, ensure_ascii=False)[:600] if detail is not None else "")


def hdr(tenant="owner"):
    return {"X-Role": "owner", "X-Tenant-Id": TENANT, "Content-Type": "application/json"}


def call(method, path, body=None, form=False):
    headers = {"X-Role": "owner", "X-Tenant-Id": TENANT}
    data = None
    if form and isinstance(body, dict):
        pairs = []
        for k, v in body.items():
            if isinstance(v, (list, tuple)):
                for item in v:
                    pairs.append((k, str(item)))
            else:
                pairs.append((k, str(v)))
        data = urlencode(pairs).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, json.loads(raw) if raw else None
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = raw
        return e.code, parsed


def seed():
    st, body = call("PUT", "/api/settings/fx_rates",
                    {"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]})
    assert st == 200, body
    st, body = call("POST", "/api/suppliers", {
        "name": "Vendor_UI_SGD",
        "contact_phone": "",
        "notes": "",
        "settlement_pref": "credit",
        "payment_terms_days": 30,
    })
    assert st == 200, body
    sid = (body.get("data") or body).get("id")
    # SGD 100 receipt
    st, body = call("POST", "/api/save_edited", {
        "supplier_name": "Vendor_UI_SGD",
        "date": "2026-10-05",
        "total_amount": 100.0,
        "settlement_type": "credit",
        "items": [{"name": "进口食材", "quantity": 10, "unit": "包", "unit_price": 10.0, "amount": 100.0}],
        "version": None,
        "source": "manual",
        "currency": "SGD",
        "department_id": None,
    })
    assert st == 200 and body.get("status") == "success", body
    rid = body.get("receipt_id") or body.get("id")
    if DB_PATH:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE receipts SET supplier_id=? WHERE id=?", (sid, rid))
        con.commit()
        con.close()
    st, rc = call("GET", f"/api/receipt/{rid}")
    ver = (rc.get("data") or {}).get("version", 1)
    st, ap = call("POST", f"/api/receipt/{rid}/approve", {"version": ver})
    assert st == 200, ap

    # legacy missing snapshot
    st, body = call("POST", "/api/suppliers", {
        "name": "Vendor_UI_Legacy",
        "contact_phone": "",
        "notes": "",
        "settlement_pref": "credit",
        "payment_terms_days": 30,
    })
    sid_l = (body.get("data") or body).get("id")
    st, body = call("POST", "/api/save_edited", {
        "supplier_name": "Vendor_UI_Legacy",
        "date": "2026-10-05",
        "total_amount": 120.0,
        "settlement_type": "credit",
        "items": [{"name": "历史物料", "quantity": 1, "unit": "件", "unit_price": 120.0, "amount": 120.0}],
        "version": None,
        "source": "manual",
        "currency": "SGD",
        "department_id": None,
    })
    rid_l = body.get("receipt_id") or body.get("id")
    if DB_PATH:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE receipts SET supplier_id=? WHERE id=?", (sid_l, rid_l))
        con.commit()
        con.close()
    st, rc = call("GET", f"/api/receipt/{rid_l}")
    ver = (rc.get("data") or {}).get("version", 1)
    st, ap = call("POST", f"/api/receipt/{rid_l}/approve", {"version": ver})
    assert st == 200, ap
    if DB_PATH:
        con = sqlite3.connect(DB_PATH)
        con.execute("DELETE FROM receipt_fx_snapshots WHERE receipt_id=? AND tenant_id=?", (rid_l, TENANT))
        con.commit()
        con.close()
    return {"sid": sid, "rid": rid, "sid_l": sid_l, "rid_l": rid_l}


seeded = seed()
step("ui_seed", True, seeded)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    context = browser.new_context(
        viewport={"width": 1440, "height": 900},
        extra_http_headers={"X-Role": "owner", "X-Tenant-Id": TENANT},
    )
    page = context.new_page()
    page.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1000)
    # open archive → payables
    page.locator('button.sidebar-btn[data-target="tab-archive"]').click()
    page.wait_for_timeout(500)
    page.evaluate("() => window.showArchivePane && window.showArchivePane('payables')")
    page.wait_for_timeout(800)
    page.locator("#payablesCard").wait_for(state="visible", timeout=15000)
    # wait for table rows
    page.wait_for_timeout(1200)
    UI.joinpath("01-payables-pane.png").write_bytes(page.locator("#payablesCard").screenshot())

    body_text = page.locator("#payablesBody").inner_text()
    # Converted HKD should appear (fmtMoney ~ HK$500.00 or 500)
    has_hkd = ("500" in body_text) or ("HK$" in body_text) or ("HKD" in body_text)
    has_orig = ("原币" in body_text) and ("SGD" in body_text or "S$" in body_text or "100" in body_text)
    has_unknown = ("汇率未知" in body_text) or ("未知" in body_text)
    # Must NOT hard-label the legacy principal as HKD 120 without unknown marker
    legacy_hard_hkd = ("HK$120" in body_text or "HKD 120" in body_text) and "汇率未知" not in body_text
    step("ui_shows_hkd500_converted", has_hkd and has_orig, {"snippet": body_text[:800]})
    step("ui_legacy_shows_unknown_not_hard_hkd", has_unknown and not legacy_hard_hkd, {"snippet": body_text[:800]})

    # Partial pay UI path: open payment modal for SGD receipt if button present
    pay_btn = page.locator("#payablesBody button").filter(has_text="登记支付")
    if pay_btn.count() > 0:
        pay_btn.first.click()
        page.wait_for_timeout(600)
        modal = page.locator("#paymentModal")
        if modal.count() and modal.first.is_visible():
            UI.joinpath("02-payment-modal.png").write_bytes(modal.first.screenshot())
            modal_text = modal.first.inner_text()
            step("ui_payment_modal_hkd_balance",
                 ("500" in modal_text) or ("HK$" in modal_text) or ("应付" in modal_text),
                 {"snippet": modal_text[:600]})
            # close modal
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        else:
            step("ui_payment_modal_hkd_balance", False, "modal not visible")
    else:
        # fallback: openPaymentModal via API-seeded supplier
        page.evaluate(
            """(sid) => {
              if (window.finState) {
                window.finState.selectedSupplierId = sid;
              }
              if (typeof openPaymentModal === 'function') openPaymentModal();
            }""",
            seeded["sid"],
        )
        page.wait_for_timeout(800)
        modal = page.locator("#paymentModal")
        visible = modal.count() > 0 and modal.first.is_visible()
        if visible:
            UI.joinpath("02-payment-modal.png").write_bytes(modal.first.screenshot())
            modal_text = modal.first.inner_text()
            step("ui_payment_modal_hkd_balance",
                 ("500" in modal_text) or ("HK$" in modal_text),
                 {"snippet": modal_text[:600]})
        else:
            step("ui_payment_modal_hkd_balance", False, "no pay button and modal not opened")

    UI.joinpath("03-payables-full.png").write_bytes(page.screenshot(full_page=False))
    browser.close()

passed = sum(1 for s in result["steps"] if s["pass"])
total = len(result["steps"])
result["verdict"] = "PASS" if passed == total else "FAIL"
result["passed"] = passed
result["total"] = total
(EV / "payables-ui-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"verdict": result["verdict"], "passed": passed, "total": total}, ensure_ascii=False))
raise SystemExit(0 if result["verdict"] == "PASS" else 1)
