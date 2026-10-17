# -*- coding: utf-8 -*-
"""UAT-033 QA: AC4 new-approve 422 + AC3 fixture cleanup + Tenant B final."""
import json, os, sys, urllib.request
from sqlalchemy import text

BASE = os.environ.get("QA_BASE", "http://127.0.0.1:15165")
TN = "uat033qa"
TN_B = "uat033qab"
OUT = os.environ["QA_LOG"]
sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db, units_store

def req(method, path, headers=None, body=None, role="owner", tenant=TN):
    data = None
    hdrs = {"X-Role": role, "X-Tenant-Id": tenant, "Content-Type": "application/json"}
    if headers: hdrs.update(headers)
    if body is not None: data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try: return e.code, json.loads(raw) if raw else {}
        except Exception: return e.code, {"raw": raw}

# AC4
body = {
    "supplier_name": "Vendor_NEW_NOFX_QA", "date": "2026-10-13", "total_amount": 50.0,
    "settlement_type": "credit",
    "items": [{"name": "新外币", "quantity": 1, "unit": "斤", "unit_price": 50, "amount": 50}],
    "version": None, "source": "manual", "currency": "CNY",
}
st, j = req("POST", "/api/save_edited", role="staff", body=body)
rid = j.get("receipt_id") or j.get("id")
assert st == 200 and rid, (st, j)
db.update_receipt(rid, currency="XXX")
row = db.get_receipt_row(rid)
st, ap = req("POST", "/api/receipt/%d/approve" % rid, body={"version": row.version})
assert st == 422 and ap.get("code") == "FX_RATE_MISSING", (st, ap)
assert db.get_receipt_row(rid).status != "approved"
# Totals unchanged (XXX not approved)
st, rep3 = req("GET", "/api/cost_report?month=2026-10")
assert (rep3.get("data") or {}).get("month_total") == 220.0
with open(os.path.join(OUT, "api-approve-422.json"), "w", encoding="utf-8") as f:
    json.dump({"status": st, "body": ap, "month_total_after": rep3.get("data", {}).get("month_total")}, f, ensure_ascii=False, indent=2)
print("NEW_APPROVE_422_OK", ap.get("code"))

# Tenant B final
st, jb = req("GET", "/api/cost_report?month=2026-10", tenant=TN_B)
assert (jb.get("data") or {}).get("month_total") == 0
assert (jb.get("data") or {}).get("unconverted_count", 0) in (0, None) or \
       (jb.get("data") or {}).get("unconverted_count") == 0
print("TENANT_B_OK")

# AC3 cleanup
rows = db.list_receipt_rows(tenant_id=TN)
ids = [r.id for r in rows]
for rid in ids:
    try:
        db.delete_receipt(rid, tenant_id=TN)
    except Exception:
        try: db.update_receipt(rid, status="void")
        except Exception: pass
with units_store._engine().begin() as conn:
    conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE tenant_id=:t"), {"t": TN})
    conn.execute(text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t"), {"t": TN})
st, rep2 = req("GET", "/api/cost_report?month=2026-10")
d2 = rep2.get("data") or {}
print("CLEANUP_REPORT", d2.get("month_total"), d2.get("unconverted_count"))
assert d2.get("month_total") in (0, 0.0)
assert d2.get("unconverted_count", 0) in (0, None) or d2.get("unconverted_count") == 0
with open(os.path.join(OUT, "cleanup-report.json"), "w", encoding="utf-8") as f:
    json.dump({"month_total": d2.get("month_total"), "unconverted_count": d2.get("unconverted_count"), "deleted_ids": ids}, f, indent=2)
print("CLEANUP_OK")
