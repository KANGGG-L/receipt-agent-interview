# -*- coding: utf-8 -*-
"""UAT-033 isolated-port self-check: fixture + API evidence + cleanup hooks."""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("DEV_BASE", "http://127.0.0.1:15155")
TN = "uat033dev"
TN_B = "uat033tenantb"
OUT = os.environ.get("UAT033_OUT", "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat033-dev/logs")


def req(method, path, headers=None, body=None):
    data = None
    hdrs = {"X-Role": "owner", "X-Tenant-Id": TN, "Content-Type": "application/json"}
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


def staff_save(items, total, currency="CNY", date="2026-10-12", supplier="Vendor_NFX_UAT033"):
    body = {
        "supplier_name": supplier, "date": date, "total_amount": total,
        "settlement_type": "credit", "items": items, "version": None,
        "source": "manual", "currency": currency,
    }
    st, j = req("POST", "/api/save_edited", headers={"X-Role": "staff", "X-Tenant-Id": TN}, body=body)
    assert st == 200 and j.get("status") == "success", (st, j)
    return j.get("receipt_id") or j.get("id")


def approve(rid):
    st, detail = req("GET", "/api/receipt/%d" % rid)
    assert st == 200, (st, detail)
    ver = (detail.get("data") or detail).get("version")
    return req("POST", "/api/receipt/%d/approve" % rid, body={"version": ver})


def main():
    os.makedirs(OUT, exist_ok=True)
    evidence = {"base": BASE, "tenant": TN, "steps": []}

    # Tenant B baseline
    st, jb = req("GET", "/api/cost_report?month=2026-10", headers={"X-Role": "owner", "X-Tenant-Id": TN_B})
    evidence["steps"].append({"name": "tenant_b_baseline", "status": st, "data": jb.get("data")})
    assert st == 200
    assert (jb.get("data") or {}).get("month_total") == 0

    # Seed CNY so we can approve, then strip to legacy NFX
    st, _ = req("PUT", "/api/settings/fx_rates",
                body={"rates": [{"currency": "CNY", "rate": 1.1, "confirmed": True}]})
    assert st == 200, _

    rid = staff_save(
        [{"name": "NFX货", "quantity": 1, "unit": "斤", "unit_price": 100, "amount": 100}],
        100.0, currency="CNY", date="2026-10-12",
    )
    st, ap = approve(rid)
    assert st == 200, ap
    evidence["steps"].append({"name": "approved_cny", "rid": rid})

    # Strip snapshot + rewrite currency via local DB (server shares DB_PATH)
    sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
    os.environ.setdefault("DB_PATH", os.environ.get("DB_PATH", ""))
    from sqlalchemy import text
    from app import db, units_store

    with units_store._engine().begin() as conn:
        conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE receipt_id=:r"), {"r": rid})
        conn.execute(text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t AND currency='CNY'"), {"t": TN})
        conn.execute(text("DELETE FROM tenant_fx_rates WHERE tenant_id=:t AND currency='NFX'"), {"t": TN})
    db.update_receipt(rid, currency="NFX")
    evidence["steps"].append({"name": "stripped_to_nfx", "rid": rid})

    # HKD companion so totals are non-empty
    rid_h = staff_save(
        [{"name": "本地菜", "quantity": 2, "unit": "斤", "unit_price": 10, "amount": 20}],
        20.0, currency="HKD", date="2026-10-12", supplier="Vendor_HKD_UAT033",
    )
    st, ap = approve(rid_h)
    assert st == 200, ap

    st, rep = req("GET", "/api/cost_report?month=2026-10")
    d = rep["data"]
    evidence["steps"].append({
        "name": "api_after_nfx",
        "month_total": d.get("month_total"),
        "unconverted_count": d.get("unconverted_count"),
        "missing_fx_count": d.get("missing_fx_count"),
        "unconverted": d.get("unconverted"),
        "unconverted_warning": d.get("unconverted_warning"),
    })
    assert d["month_total"] == 20.0
    assert d["unconverted_count"] == 1
    assert d["missing_fx_count"] == 1
    assert len(d["unconverted"]) == 1
    assert "汇率" in (d.get("unconverted_warning") or "")

    # Tenant B unchanged
    st, jb2 = req("GET", "/api/cost_report?month=2026-10", headers={"X-Role": "owner", "X-Tenant-Id": TN_B})
    assert (jb2.get("data") or {}).get("month_total") == 0
    assert (jb2.get("data") or {}).get("unconverted_count", 0) in (0, None) or \
           (jb2.get("data") or {}).get("unconverted_count") == 0

    path = os.path.join(OUT, "api-before-fx.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    print("API_BEFORE_OK", path)
    print("RID_NFX", rid)
    print("WARNING", d.get("unconverted_warning"))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print("FAIL", e)
        raise
