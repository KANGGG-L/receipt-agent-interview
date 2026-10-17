# -*- coding: utf-8 -*-
import json, os, sys, urllib.request
from sqlalchemy import text

BASE = os.environ.get("DEV_BASE", "http://127.0.0.1:15155")
TN = "uat033dev"
TN_B = "uat033tenantb"
OUT = "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat033-dev/logs"
sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db, units_store


def req(method, path, headers=None, body=None, role="owner", tenant=TN):
    data = None
    hdrs = {"X-Role": role, "X-Tenant-Id": tenant, "Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


# 2) Add FX for NFX
st, _ = req("PUT", "/api/settings/fx_rates",
            body={"rates": [{"currency": "NFX", "rate": 2.0, "confirmed": True}]})
assert st == 200, _
st, rep = req("GET", "/api/cost_report?month=2026-10")
d = rep["data"]
assert d["month_total"] == 220.0, d
assert d["unconverted_count"] == 0
assert not d.get("unconverted")
assert (d.get("unconverted_warning") or "") == ""
with open(os.path.join(OUT, "api-after-fx.json"), "w", encoding="utf-8") as f:
    json.dump(d, f, ensure_ascii=False, indent=2)
print("API_AFTER_FX_OK", d["month_total"])

# 4) New receipt missing FX still 422
body = {
    "supplier_name": "Vendor_NEW_NOFX", "date": "2026-10-13", "total_amount": 50.0,
    "settlement_type": "credit",
    "items": [{"name": "新外币", "quantity": 1, "unit": "斤", "unit_price": 50, "amount": 50}],
    "version": None, "source": "manual", "currency": "CNY",
}
st, j = req("POST", "/api/save_edited", headers={"X-Role": "staff"}, body=body)
rid = j.get("receipt_id") or j.get("id")
assert st == 200 and rid, (st, j)
db.update_receipt(rid, currency="XXX")
row = db.get_receipt_row(rid)
st, ap = req("POST", "/api/receipt/%d/approve" % rid, body={"version": row.version})
assert st == 422 and ap.get("code") == "FX_RATE_MISSING", (st, ap)
assert db.get_receipt_row(rid).status != "approved"
print("NEW_APPROVE_422_OK", ap.get("code"))

# Tenant B
st, jb = req("GET", "/api/cost_report?month=2026-10", tenant=TN_B)
assert (jb.get("data") or {}).get("month_total") == 0
print("TENANT_B_OK")

# 3) cleanup fixture for this tenant
rows = db.list_receipt_rows(tenant_id=TN)
ids = [r.id for r in rows]
for rid in ids:
    try:
        db.delete_receipt(rid, tenant_id=TN)
    except Exception:
        # soft fallback
        db.update_receipt(rid, status="void")
with units_store._engine().begin() as conn:
    conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE tenant_id=:t"), {"t": TN})
    conn.execute(text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t"), {"t": TN})
st, rep2 = req("GET", "/api/cost_report?month=2026-10")
print("CLEANUP_REPORT", rep2.get("data", {}).get("month_total"),
      rep2.get("data", {}).get("unconverted_count"))
print("CLEANUP_OK")
