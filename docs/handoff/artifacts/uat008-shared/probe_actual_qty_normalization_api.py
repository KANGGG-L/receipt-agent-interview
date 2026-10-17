# -*- coding: utf-8 -*-
"""UAT-008 (2026-10-17 DEV) live isolate API probe: actual_qty normalization parity.

Covers the residual defect found in this DEV pass: blank / whitespace / invalid actual_qty
made save pass while approve was blocked (DB stored 0.0). Also re-checks the zero matrix.
Env: BASE, ART, DB_PATH. Never point at 15010.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import error, request

BASE = os.environ["BASE"].rstrip("/")
assert ":15010" not in BASE, "refuse 15010"
ART = Path(os.environ["ART"])
DB_PATH = os.environ["DB_PATH"]
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)
TA, TB = "uat008n-a", "uat008n-b"
FEES = {"delivery_fee": 5.0, "deposit_amount": 4.0, "service_fee": 3.0,
        "tax_amount": 2.0, "discount_amount": 1.0, "rounding_adjustment": 0.5}
checks = []


def call(method, path, body=None, *, tenant, role="staff"):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(BASE + path, data=data, method=method, headers={
        "X-Role": role, "X-Tenant-Id": tenant, "Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "null")
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def check(name, cond, detail=None):
    checks.append({"name": name, "pass": bool(cond), "detail": detail})
    print("PASS" if cond else "FAIL", name, json.dumps(detail, ensure_ascii=False, default=str)[:500])


def payload(aq, total, confirm=False, name="Item_N"):
    return {"supplier_name": "Vendor_A", "date": "2026-10-17", "total_amount": total,
            "settlement_type": "cash", "confirm_mismatch": confirm, "source": "manual",
            "items": [{"name": name, "quantity": 1, "unit": "pcs", "unit_price": 10.0,
                       "amount": 10.0, "actual_qty": aq}], **FEES}


def db_one(sql, args=()):
    c = sqlite3.connect(DB_PATH)
    try:
        return c.execute(sql, args).fetchone()
    finally:
        c.close()


b_before = db_one("SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TB,))[0]

# blank / whitespace: unfilled -> null stored, save+approve agree (22.5)
for label, aq in [("empty", ""), ("spaces", "   ")]:
    s, b = call("POST", "/api/save_edited", payload(aq, 22.5, name="Item_" + label), tenant=TA)
    rid = (b or {}).get("receipt_id") if isinstance(b, dict) else None
    stored = db_one("SELECT actual_qty FROM receipt_items WHERE receipt_id=?", (rid,)) if rid else None
    sa, ba = call("POST", f"/api/receipt/{rid}/approve", {"version": 1}, tenant=TA, role="owner") if rid else (None, None)
    logs = db_one("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM inventory_log WHERE receipt_id=?", (rid,)) if rid else None
    check(f"blank_{label}_save200_null_approve200_stock_qty1",
          s == 200 and stored == (None,) and sa == 200 and logs and logs[0] >= 1 and abs(logs[1] - 1.0) < 1e-9,
          {"save": s, "stored": stored, "approve": sa, "inventory_log(count,sum_qty)": logs})

# invalid: 422 INVALID_ACTUAL_QTY and zero write
for aq in ["abc", "nan", "inf", True]:
    n0 = db_one("SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TA,))[0]
    s, b = call("POST", "/api/save_edited", payload(aq, 22.5, confirm=True), tenant=TA)
    n1 = db_one("SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TA,))[0]
    check(f"invalid_{aq!r}_rejected_no_write",
          s == 422 and isinstance(b, dict) and b.get("code") == "INVALID_ACTUAL_QTY" and n0 == n1,
          {"status": s, "body": b, "receipts_before": n0, "after": n1})

# zero variants matrix
for aq in [0, 0.0, "0", " 0 "]:
    s, b = call("POST", "/api/save_edited", payload(aq, 22.5), tenant=TA)
    check(f"zero_{aq!r}_unmatched_422_diff_visible",
          s == 422 and b.get("code") == "MATH_MISMATCH_CONFIRM" and "相差 10 元" in b.get("msg", ""),
          {"status": s, "msg": b.get("msg") if isinstance(b, dict) else b})
    s2, b2 = call("POST", "/api/save_edited", payload(aq, 22.5, confirm=True), tenant=TA)
    rid = b2["receipt_id"]
    sd, bd = call("GET", f"/api/receipt/{rid}", tenant=TA)
    gate = json.dumps(bd, ensure_ascii=False)
    sa, ba = call("POST", f"/api/receipt/{rid}/approve", {"version": 1}, tenant=TA, role="owner")
    nlog = db_one("SELECT COUNT(*) FROM inventory_log WHERE receipt_id=?", (rid,))[0]
    nbat = db_one("SELECT COUNT(*) FROM inventory_batches WHERE receipt_id=?", (rid,))[0]
    check(f"zero_{aq!r}_confirmed_detail_mismatch_approve_blocked_no_stock",
          "TOTAL_MISMATCH" in gate and sa == 422 and ba.get("code") == "APPROVAL_BLOCKED"
          and "相差 10 元" in ba.get("msg", "") and nlog == 0 and nbat == 0,
          {"approve": sa, "msg": ba.get("msg"), "logs": nlog, "batches": nbat})
    s3, b3 = call("POST", "/api/save_edited", payload(aq, 12.5, name="Item_ok_%r" % (aq,)), tenant=TA)
    rid3 = b3["receipt_id"]
    stored = db_one("SELECT actual_qty FROM receipt_items WHERE receipt_id=?", (rid3,))
    sa3, _ = call("POST", f"/api/receipt/{rid3}/approve", {"version": 1}, tenant=TA, role="owner")
    logs3 = db_one("SELECT COUNT(*), COALESCE(SUM(qty),0) FROM inventory_log WHERE receipt_id=?", (rid3,))
    check(f"zero_{aq!r}_matched_save200_approve200_stored0_stock0",
          s3 == 200 and stored == (0.0,) and sa3 == 200 and abs(logs3[1]) < 1e-9,
          {"save": s3, "stored": stored, "approve": sa3, "inventory_log(count,sum_qty)": logs3})

# tenant B
s, b = call("POST", "/api/save_edited", payload(0, 12.5, name="Item_iso"), tenant=TA)
rid = b["receipt_id"]
check("tenant_b_detail_404", call("GET", f"/api/receipt/{rid}", tenant=TB)[0] == 404)
check("tenant_b_approve_404", call("POST", f"/api/receipt/{rid}/approve", {"version": 1}, tenant=TB, role="owner")[0] == 404)
hack = payload(0, 12.5); hack.update({"receipt_id": rid, "version": 1})
check("tenant_b_save_404", call("POST", "/api/save_edited", hack, tenant=TB)[0] == 404)
b_after = db_one("SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TB,))[0]
check("tenant_b_baseline_unchanged", b_before == b_after, {"before": b_before, "after": b_after})

passed = sum(c["pass"] for c in checks)
out = {"card": "UAT-008", "probe": "normalization_api", "verdict": "PASS" if passed == len(checks) else "FAIL",
       "passed": passed, "total": len(checks), "checks": checks}
(EV / "actual-qty-normalization-api-probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"SUMMARY {out['verdict']} {passed}/{len(checks)}")
sys.exit(0 if out["verdict"] == "PASS" else 1)
