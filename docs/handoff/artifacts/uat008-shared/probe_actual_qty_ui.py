# -*- coding: utf-8 -*-
"""UAT-008 (2026-10-17 DEV) real-browser probe on an isolated port (never 15010).

Real clicks / typing in the workbench review form + network capture of /api/save_edited
and /approve, then DB read-back. Env: BASE, ART, DB_PATH.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import request

from playwright.sync_api import sync_playwright

BASE = os.environ["BASE"].rstrip("/")
assert ":15010" not in BASE, "refuse 15010"
ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
EV, UI = ART / "evidence", ART / "ui"
EV.mkdir(parents=True, exist_ok=True)
UI.mkdir(parents=True, exist_ok=True)
T = "uat008ui-a"
FEES = {"delivery_fee": 5.0, "deposit_amount": 4.0, "service_fee": 3.0,
        "tax_amount": 2.0, "discount_amount": 1.0, "rounding_adjustment": 0.5}
result = {"card": "UAT-008", "probe": "ui", "steps": [], "hashes": {}, "network": []}


def step(name, ok, detail=None):
    result["steps"].append({"name": name, "pass": bool(ok), "detail": detail})
    print("PASS" if ok else "FAIL", name, json.dumps(detail, ensure_ascii=False, default=str)[:700])


def snap(pg, name):
    path = UI / name
    pg.screenshot(path=str(path), full_page=False)
    result["hashes"][name] = hashlib.md5(path.read_bytes()).hexdigest()


def seed(name):
    body = {"supplier_name": "Vendor_A", "date": "2026-10-17", "total_amount": 22.5,
            "settlement_type": "cash", "source": "manual", "confirm_mismatch": False,
            "items": [{"name": name, "quantity": 1, "unit": "pcs", "unit_price": 10,
                       "amount": 10, "actual_qty": None}], **FEES}
    req = request.Request(BASE + "/api/save_edited", data=json.dumps(body).encode(), method="POST",
                          headers={"X-Role": "staff", "X-Tenant-Id": T, "Content-Type": "application/json"})
    return json.loads(request.urlopen(req).read())["receipt_id"]


def db(sql, args=()):
    c = sqlite3.connect(DB_PATH)
    try:
        return c.execute(sql, args).fetchall()
    finally:
        c.close()


STATE_JS = """() => ({
  total: document.getElementById('inpTotal').value,
  actual: document.querySelector('#itemTableBody tr .inp-actual-qty').value,
  overridden: isTotalOverridden(),
  hint: (document.getElementById('totalOverrideHint')||{}).textContent || '',
  hintVisible: !!document.getElementById('totalOverrideHint') && !document.getElementById('totalOverrideHint').classList.contains('hide'),
  issues: calculateReceiptIssues(collectReviewFormData()).map(i => ({id: i.id, type: i.type, guide: i.guide})),
  toasts: [...document.querySelectorAll('.toast, .toast-item, [class*=toast]')].map(e => e.textContent.trim()).filter(Boolean).slice(-3),
})"""


def open_receipt(pg, rid):
    pg.evaluate(f"() => window.openReceiptInWorkbench({rid}, true, true)")
    pg.wait_for_function(f"() => String((window.currentReceiptData||{{}}).id||currentReceiptId) === '{rid}' && document.querySelector('#itemTableBody tr .inp-actual-qty')", timeout=15000)
    pg.wait_for_timeout(600)


def main():
    rid_zero = seed("Item_UI_zero")
    rid_null = seed("Item_UI_null")
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        ctx = br.new_context(viewport={"width": 1440, "height": 1000},
                             extra_http_headers={"X-Role": "owner", "X-Tenant-Id": T})
        pg = ctx.new_page()

        def on_req(r):
            if "/api/save_edited" in r.url or "/approve" in r.url:
                result["network"].append({"phase": "request", "url": r.url, "method": r.method,
                                          "body": r.post_data})

        def on_resp(r):
            if "/api/save_edited" in r.url or "/approve" in r.url:
                try:
                    b = r.json()
                except Exception:
                    b = None
                result["network"].append({"phase": "response", "url": r.url, "status": r.status, "body": b})

        pg.on("request", on_req)
        pg.on("response", on_resp)
        pg.goto(BASE + "/", wait_until="domcontentloaded", timeout=60000)
        pg.wait_for_timeout(1200)

        # ---------- scenario A: actual_qty = 0 ----------
        open_receipt(pg, rid_zero)
        s0 = pg.evaluate(STATE_JS)
        step("A0_opened_total_22.5_actual_blank_no_total_issue",
             s0["total"] == "22.50" and s0["actual"] == "" and not any(i["id"] == "err_grand_total_math" for i in s0["issues"]), s0)

        actual = pg.locator("#itemTableBody tr .inp-actual-qty").first
        actual.click()
        actual.fill("0")
        pg.wait_for_timeout(400)
        s1 = pg.evaluate(STATE_JS)
        step("A1_type_actual_0_auto_total_becomes_12.50", s1["total"] == "12.50" and s1["actual"] == "0", s1)
        snap(pg, "01_actual0_auto_total_12_50.png")

        tot = pg.locator("#inpTotal")
        tot.click()
        tot.fill("22.5")
        pg.wait_for_timeout(400)
        s2 = pg.evaluate(STATE_JS)
        gi = [i for i in s2["issues"] if i["id"] == "err_grand_total_math"]
        step("A2_total_typed_22.5_hint_shows_diff_10_and_override_kept",
             s2["overridden"] is True and s2["hintVisible"] and "10.00" in s2["hint"] and "12.50" in s2["hint"], s2)
        step("A2_issue_summary_blocker_diff_10_visible",
             bool(gi) and gi[0]["type"] == "blocker" and "10.00" in gi[0]["guide"], gi)
        tot.scroll_into_view_if_needed()
        snap(pg, "02_total_22_5_diff_10_visible.png")

        n_before = len([n for n in result["network"] if n["phase"] == "request" and "save_edited" in n["url"]])
        pg.locator("#btnSaveReview").click()
        pg.wait_for_timeout(1200)
        n_after = len([n for n in result["network"] if n["phase"] == "request" and "save_edited" in n["url"]])
        toast = pg.evaluate("() => document.body.innerText.match(/还有 \\d+ 处必须先改的地方[^\\n]*/)?.[0] || ''")
        step("A3_save_blocked_locally_with_diff_no_request",
             n_after == n_before and "10.00" in toast, {"toast": toast, "save_requests_delta": n_after - n_before})
        snap(pg, "03_save_blocked_diff_10.png")
        pg.locator("#btnApproveReceipt").click()
        pg.wait_for_timeout(800)
        appr_req = [n for n in result["network"] if n["phase"] == "request" and n["url"].endswith("/approve")]
        step("A3b_approve_click_blocked_locally_no_request", len(appr_req) == 0, {"approve_requests": len(appr_req)})

        # correct total to 12.5
        tot.click()
        tot.fill("12.5")
        pg.wait_for_timeout(400)
        s4 = pg.evaluate(STATE_JS)
        step("A4_total_12.5_no_total_issue_hint_hidden",
             not any(i["id"] == "err_grand_total_math" for i in s4["issues"]) and not s4["hintVisible"], s4)
        with pg.expect_response(lambda r: "/api/save_edited" in r.url, timeout=15000) as rinfo:
            pg.locator("#btnSaveReview").click()
        resp = rinfo.value
        sreq = [n for n in result["network"] if n["phase"] == "request" and "save_edited" in n["url"]][-1]
        sbody = json.loads(sreq["body"])
        step("A5_save_request_actual_qty_0_total_12.5_response_200",
             resp.status == 200 and sbody["items"][0]["actual_qty"] == 0 and sbody["items"][0]["actual_qty"] is not None
             and abs(float(sbody["total_amount"]) - 12.5) < 1e-9,
             {"status": resp.status, "actual_qty": sbody["items"][0]["actual_qty"], "total": sbody["total_amount"],
              "fees": {k: sbody.get(k) for k in FEES}})
        pg.wait_for_timeout(1500)
        snap(pg, "04_saved_matched_12_5.png")
        rows = db("SELECT actual_qty, quantity, amount FROM receipt_items WHERE receipt_id=?", (rid_zero,))
        step("A5b_db_actual_qty_0_persisted", rows and rows[0][0] == 0.0, rows)

        pg.locator("#btnApproveReceipt").click()
        pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)", timeout=10000)
        snap(pg, "05_approve_confirm_dialog.png")
        with pg.expect_response(lambda r: r.url.endswith(f"/api/receipt/{rid_zero}/approve"), timeout=15000) as ainfo:
            pg.locator("#btnExecuteApproveConfirm").click()
        aresp = ainfo.value
        pg.wait_for_timeout(1500)
        st = db("SELECT status, total_amount FROM receipts WHERE id=?", (rid_zero,))
        logs = db("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM inventory_log WHERE receipt_id=?", (rid_zero,))
        step("A6_ui_approve_200_db_approved_stock_delta_0",
             aresp.status == 200 and st and st[0][0] == "approved" and abs(logs[0][1]) < 1e-9,
             {"approve_status": aresp.status, "receipt": st, "inventory_log(count,sum_qty)": logs})
        snap(pg, "06_approved_actual0.png")

        # ---------- scenario B: actual_qty blank = null ----------
        open_receipt(pg, rid_null)
        sb = pg.evaluate(STATE_JS)
        step("B0_blank_actual_total_22.5_no_issue", sb["actual"] == "" and not any(i["id"] == "err_grand_total_math" for i in sb["issues"]), sb)
        with pg.expect_response(lambda r: "/api/save_edited" in r.url, timeout=15000) as rinfo:
            pg.locator("#btnSaveReview").click()
        sreq = [n for n in result["network"] if n["phase"] == "request" and "save_edited" in n["url"]][-1]
        sbody = json.loads(sreq["body"])
        step("B1_save_payload_actual_qty_null_200",
             rinfo.value.status == 200 and sbody["items"][0]["actual_qty"] is None and abs(float(sbody["total_amount"]) - 22.5) < 1e-9,
             {"status": rinfo.value.status, "actual_qty": sbody["items"][0]["actual_qty"], "total": sbody["total_amount"]})
        pg.wait_for_timeout(1500)
        pg.locator("#btnApproveReceipt").click()
        pg.wait_for_selector("#modalApproveConfirmDialog:not(.hide)", timeout=10000)
        with pg.expect_response(lambda r: r.url.endswith(f"/api/receipt/{rid_null}/approve"), timeout=15000) as ainfo:
            pg.locator("#btnExecuteApproveConfirm").click()
        pg.wait_for_timeout(1500)
        logs = db("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM inventory_log WHERE receipt_id=?", (rid_null,))
        rows = db("SELECT actual_qty FROM receipt_items WHERE receipt_id=?", (rid_null,))
        step("B2_ui_approve_200_null_uses_quantity_1",
             ainfo.value.status == 200 and rows[0][0] is None and abs(logs[0][1] - 1.0) < 1e-9,
             {"approve": ainfo.value.status, "actual_qty": rows, "inventory_log(count,sum_qty)": logs})
        snap(pg, "07_approved_null_qty1.png")
        br.close()

    hashes = list(result["hashes"].values())
    step("screenshots_md5_distinct", len(hashes) == len(set(hashes)), result["hashes"])
    passed = sum(s["pass"] for s in result["steps"])
    result.update({"passed": passed, "total": len(result["steps"]),
                   "verdict": "PASS" if passed == len(result["steps"]) else "FAIL",
                   "receipt_ids": {"zero": rid_zero, "null": rid_null}})
    (EV / "actual-qty-ui-probe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (UI / "ui-hashes.txt").write_text("\n".join(f"{v}  {k}" for k, v in result["hashes"].items()) + "\n", encoding="utf-8")
    print(f"SUMMARY {result['verdict']} {passed}/{len(result['steps'])}")
    sys.exit(0 if result["verdict"] == "PASS" else 1)


main()
