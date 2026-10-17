# -*- coding: utf-8 -*-
"""UAT-008 live isolate API probe: actual_qty=0 arithmetic save/approve/inventory."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib import error, request

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ.get("DB_PATH", "")
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)

TENANT_A = "uat008-a"
TENANT_B = "uat008-b"

FEES = {
    "delivery_fee": 5.0,
    "deposit_amount": 4.0,
    "service_fee": 3.0,
    "tax_amount": 2.0,
    "discount_amount": 1.0,
    "rounding_adjustment": 0.5,
}
# net fees = +12.5; matched total with actual_qty=0 -> 12.5; with null -> 22.5


def hdr(tenant: str, role: str = "staff") -> dict:
    return {"X-Role": role, "X-Tenant-Id": tenant, "Content-Type": "application/json"}


def call(method: str, path: str, body=None, *, tenant: str, role: str = "staff"):
    data = None
    headers = hdr(tenant, role)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return {"ok": True, "status": resp.status, "body": parsed, "raw": raw[:4000]}
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return {"ok": False, "status": e.code, "body": parsed, "raw": raw[:4000]}


checks = []


def check(name: str, cond: bool, detail=None):
    row = {"name": name, "pass": bool(cond), "detail": detail}
    checks.append(row)
    print(("PASS" if cond else "FAIL"), name, json.dumps(detail, ensure_ascii=False, default=str)[:600])
    return cond


# --- AC1: fixture independent fees ---
fee_vals = list(FEES.values())
check(
    "ac1_six_fees_independent",
    len(fee_vals) == 6 and len(set(fee_vals)) == 6 and all(v > 0 for v in fee_vals),
    FEES,
)

# --- AC4 + AC2: actual_qty=0 persists; mismatch visible ---
payload_zero_mismatch = {
    "supplier_name": "Vendor_UAT008_A",
    "date": "2026-10-17",
    "total_amount": 22.5,
    "settlement_type": "cash",
    "confirm_mismatch": False,
    "items": [{
        "name": "Item_Zero", "quantity": 1, "unit": "pcs",
        "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
    }],
    "source": "manual",
    **FEES,
}
r_mm = call("POST", "/api/save_edited", payload_zero_mismatch, tenant=TENANT_A, role="staff")
body_mm = r_mm.get("body") or {}
check(
    "ac2_mismatch_blocked_422",
    r_mm["status"] == 422 and (body_mm.get("code") == "MATH_MISMATCH_CONFIRM"),
    {"status": r_mm["status"], "code": body_mm.get("code"), "msg": str(body_mm)[:400]},
)
msg_blob = str(body_mm)
check(
    "ac2_discrepancy_visible_in_error",
    ("总额对不上" in msg_blob) and ("10" in msg_blob),
    msg_blob[:500],
)

# confirm_mismatch True allows save but approve still blocks
payload_zero_confirm = dict(payload_zero_mismatch, confirm_mismatch=True)
r_save_mm = call("POST", "/api/save_edited", payload_zero_confirm, tenant=TENANT_A, role="staff")
rid_mm = (r_save_mm.get("body") or {}).get("receipt_id")
check("ac2_confirm_allows_save", r_save_mm["status"] == 200 and rid_mm, r_save_mm.get("body"))

r_detail = call("GET", f"/api/receipt/{rid_mm}", tenant=TENANT_A, role="staff")
items = (r_detail.get("body") or {}).get("items") or (r_detail.get("body") or {}).get("data", {}).get("items") or []
# detail shape may nest under data
if not items and isinstance(r_detail.get("body"), dict):
    data = r_detail["body"].get("data") or r_detail["body"]
    items = data.get("items") or []
aq = None
if items:
    aq = items[0].get("actual_qty")
check(
    "ac4_actual_qty_zero_persisted",
    aq == 0 or aq == 0.0,
    {"actual_qty": aq, "items0": items[0] if items else None, "status": r_detail["status"]},
)

r_ap_mm = call("POST", f"/api/receipt/{rid_mm}/approve", {"version": 1}, tenant=TENANT_A, role="owner")
ap_body = r_ap_mm.get("body") or {}
check(
    "ac2_approve_blocked_on_mismatch",
    r_ap_mm["status"] == 422 and ap_body.get("code") == "APPROVAL_BLOCKED",
    {"status": r_ap_mm["status"], "body": ap_body},
)
check(
    "ac2_approve_discrepancy_visible",
    ("总额对不上" in str(ap_body)) and ("10" in str(ap_body)),
    str(ap_body)[:500],
)

# unmatched must not enter inventory
conn = sqlite3.connect(DB_PATH)
try:
    n_logs = conn.execute("SELECT COUNT(*) FROM inventory_log WHERE receipt_id=?", (rid_mm,)).fetchone()[0]
    n_batches = conn.execute(
        "SELECT COUNT(*) FROM inventory_batches WHERE receipt_id=?", (rid_mm,)
    ).fetchone()[0]
except sqlite3.OperationalError as e:
    # table names may differ
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    n_logs = n_batches = -1
    check("inventory_table_lookup", False, {"err": str(e), "tables": tables})
else:
    check("unmatched_zero_inventory_log", n_logs == 0, {"logs": n_logs, "batches": n_batches})
    check("unmatched_zero_batches", n_batches == 0, {"logs": n_logs, "batches": n_batches})
finally:
    conn.close()

# --- AC3: corrected total 12.5 -> save and approve agree ---
payload_matched = {
    "supplier_name": "Vendor_UAT008_A",
    "date": "2026-10-17",
    "total_amount": 12.5,
    "settlement_type": "cash",
    "confirm_mismatch": False,
    "items": [{
        "name": "Item_Matched", "quantity": 1, "unit": "pcs",
        "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
    }],
    "source": "manual",
    **FEES,
}
r_ok = call("POST", "/api/save_edited", payload_matched, tenant=TENANT_A, role="staff")
rid_ok = (r_ok.get("body") or {}).get("receipt_id")
check("ac3_matched_save_200", r_ok["status"] == 200 and rid_ok, r_ok.get("body"))
r_ap_ok = call("POST", f"/api/receipt/{rid_ok}/approve", {"version": 1}, tenant=TENANT_A, role="owner")
check(
    "ac3_matched_approve_200",
    r_ap_ok["status"] == 200 and (r_ap_ok.get("body") or {}).get("status") == "success",
    r_ap_ok.get("body"),
)

# --- AC4: actual_qty=null uses quantity (matched total 22.5) ---
payload_null = {
    "supplier_name": "Vendor_UAT008_A",
    "date": "2026-10-17",
    "total_amount": 22.5,
    "settlement_type": "cash",
    "confirm_mismatch": False,
    "items": [{
        "name": "Item_Null", "quantity": 1, "unit": "pcs",
        "unit_price": 10.0, "amount": 10.0, "actual_qty": None,
    }],
    "source": "manual",
    **FEES,
}
r_null = call("POST", "/api/save_edited", payload_null, tenant=TENANT_A, role="staff")
rid_null = (r_null.get("body") or {}).get("receipt_id")
check("ac4_null_save_200", r_null["status"] == 200 and rid_null, r_null.get("body"))
r_null_d = call("GET", f"/api/receipt/{rid_null}", tenant=TENANT_A, role="staff")
data_n = r_null_d.get("body") or {}
items_n = data_n.get("items") or (data_n.get("data") or {}).get("items") or []
aq_n = items_n[0].get("actual_qty") if items_n else "MISSING"
check("ac4_null_persisted_as_null", aq_n is None, {"actual_qty": aq_n})
r_ap_null = call("POST", f"/api/receipt/{rid_null}/approve", {"version": 1}, tenant=TENANT_A, role="owner")
check(
    "ac4_null_approve_200",
    r_ap_null["status"] == 200,
    r_ap_null.get("body"),
)

# string "0" and float 0.0 variants via save
for label, aq_val in [("str0", "0"), ("float0", 0.0)]:
    p = {
        "supplier_name": "Vendor_UAT008_A",
        "date": "2026-10-17",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "confirm_mismatch": False,
        "items": [{
            "name": f"Item_{label}", "quantity": 1, "unit": "pcs",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": aq_val,
        }],
        "source": "manual",
        **FEES,
    }
    rr = call("POST", "/api/save_edited", p, tenant=TENANT_A, role="staff")
    rid = (rr.get("body") or {}).get("receipt_id")
    rd = call("GET", f"/api/receipt/{rid}", tenant=TENANT_A, role="staff") if rid else {"body": {}}
    data = rd.get("body") or {}
    its = data.get("items") or (data.get("data") or {}).get("items") or []
    got = its[0].get("actual_qty") if its else None
    check(
        f"ac4_variant_{label}_is_zero",
        rr["status"] == 200 and (got == 0 or got == 0.0),
        {"status": rr["status"], "actual_qty": got},
    )

# Tenant B isolation + baseline
conn = sqlite3.connect(DB_PATH)
try:
    b_before = conn.execute(
        "SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TENANT_B,)
    ).fetchone()[0]
except Exception:
    # fallback table name
    try:
        b_before = conn.execute(
            "SELECT COUNT(*) FROM receipt WHERE tenant_id=?", (TENANT_B,)
        ).fetchone()[0]
    except Exception as e:
        b_before = None
        check("tenant_b_baseline_count", False, str(e))
finally:
    conn.close()

r_peek = call("GET", f"/api/receipt/{rid_ok}", tenant=TENANT_B, role="staff")
check("tenant_b_peek_404", r_peek["status"] == 404, {"status": r_peek["status"]})
r_hack = call(
    "POST",
    "/api/save_edited",
    {
        "receipt_id": rid_ok,
        "version": 1,
        "supplier_name": "HACK",
        "date": "2026-10-17",
        "total_amount": 12.5,
        "settlement_type": "cash",
        "items": [{
            "name": "Item_Matched", "quantity": 1, "unit": "pcs",
            "unit_price": 10.0, "amount": 10.0, "actual_qty": 0,
        }],
        "source": "manual",
        **FEES,
    },
    tenant=TENANT_B,
    role="staff",
)
check("tenant_b_save_404", r_hack["status"] == 404, {"status": r_hack["status"]})
r_hack_ap = call("POST", f"/api/receipt/{rid_ok}/approve", {"version": 1}, tenant=TENANT_B, role="owner")
check("tenant_b_approve_404", r_hack_ap["status"] == 404, {"status": r_hack_ap["status"]})

conn = sqlite3.connect(DB_PATH)
try:
    b_after = conn.execute(
        "SELECT COUNT(*) FROM receipts WHERE tenant_id=?", (TENANT_B,)
    ).fetchone()[0]
except Exception:
    try:
        b_after = conn.execute(
            "SELECT COUNT(*) FROM receipt WHERE tenant_id=?", (TENANT_B,)
        ).fetchone()[0]
    except Exception:
        b_after = None
finally:
    conn.close()
if b_before is not None:
    check("tenant_b_baseline_unchanged", b_before == b_after, {"before": b_before, "after": b_after})

passed = sum(1 for c in checks if c["pass"])
total = len(checks)
out = {
    "verdict": "PASS" if passed == total else "FAIL",
    "passed": passed,
    "total": total,
    "checks": checks,
}
(EV / "actual-qty-api-probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"SUMMARY {out['verdict']} {passed}/{total}")
sys.exit(0 if passed == total else 1)
