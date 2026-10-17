# -*- coding: utf-8 -*-
"""UAT-015 live isolate probe: SGD snapshot FX payables + partial/full pay + legacy unknown."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from urllib import error, request
from urllib.parse import urlencode

BASE = os.environ["BASE"].rstrip("/")
ART = Path(os.environ["ART"])
DB_PATH = os.environ.get("DB_PATH", "")
EV = ART / "evidence"
EV.mkdir(parents=True, exist_ok=True)

TENANT_A = "uat015-a"
TENANT_B = "uat015-b"
TODAY = date.today().isoformat()


def hdr(tenant: str, role: str = "owner", form: bool = False) -> dict:
    h = {"X-Role": role, "X-Tenant-Id": tenant}
    if not form:
        h["Content-Type"] = "application/json"
    return h


def call(method: str, path: str, body=None, *, tenant: str, role: str = "owner", form: bool = False):
    data = None
    headers = hdr(tenant, role, form=form)
    if form:
        # multipart-ish: use application/x-www-form-urlencoded for payments
        if isinstance(body, dict):
            pairs = []
            for k, v in body.items():
                if isinstance(v, (list, tuple)):
                    for item in v:
                        pairs.append((k, str(item)))
                else:
                    pairs.append((k, str(v)))
            data = urlencode(pairs).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            data = body
    elif body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return {"ok": True, "status": resp.status, "body": parsed, "raw": raw[:3000]}
    except error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            parsed = raw
        return {"ok": False, "status": e.code, "body": parsed, "raw": raw[:3000]}


def check(name: str, cond: bool, detail=None):
    return {"name": name, "pass": bool(cond), "detail": detail}


def create_supplier(tenant: str, name: str) -> int:
    # Prefer API if available; fall back to direct DB insert via save path
    r = call("POST", "/api/suppliers", {
        "name": name,
        "contact_phone": "",
        "notes": "",
        "settlement_pref": "credit",
        "payment_terms_days": 30,
    }, tenant=tenant)
    if r["status"] == 200 and isinstance(r["body"], dict):
        data = r["body"].get("data") or r["body"]
        sid = data.get("id") if isinstance(data, dict) else None
        if sid:
            return int(sid)
    # fallback: list after create via db helper endpoint patterns
    raise RuntimeError("create_supplier failed: %s" % r)


def make_sgd_receipt(tenant: str, supplier: str, sid: int, amount: float = 100.0):
    body = {
        "supplier_name": supplier,
        "date": "2026-10-05",
        "total_amount": amount,
        "settlement_type": "credit",
        "items": [{"name": "进口食材", "quantity": 10, "unit": "包", "unit_price": amount / 10,
                   "amount": amount}],
        "version": None,
        "source": "manual",
        "currency": "SGD",
        "department_id": None,
    }
    r = call("POST", "/api/save_edited", body, tenant=tenant, role="staff")
    assert r["status"] == 200 and r["body"].get("status") == "success", r
    rid = r["body"].get("receipt_id") or r["body"].get("id")
    # bind supplier_id via PATCH if API supports; else DB
    # try update
    row = call("GET", "/api/receipt/%d" % rid, tenant=tenant)
    ver = (row["body"].get("data") or {}).get("version", 1)
    # use approve after ensuring supplier_id — update via admin API if present
    # Direct DB update for supplier_id (same as _b2_helpers)
    if DB_PATH:
        con = sqlite3.connect(DB_PATH)
        con.execute("UPDATE receipts SET supplier_id=? WHERE id=?", (sid, rid))
        con.commit()
        con.close()
    ap = call("POST", "/api/receipt/%d/approve" % rid, {"version": ver}, tenant=tenant)
    return int(rid), ap


def delete_fx_snapshot(rid: int, tenant: str):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM receipt_fx_snapshots WHERE receipt_id=? AND tenant_id=?", (rid, tenant))
    con.commit()
    con.close()


def main():
    results = []
    # --- AC1: FX=5, SGD100 -> HKD500, archive original ---
    fx = call("PUT", "/api/settings/fx_rates",
              {"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]},
              tenant=TENANT_A)
    results.append(check("ac1_set_fx_sgd_5", fx["status"] == 200, {"status": fx["status"]}))

    sid = create_supplier(TENANT_A, "Vendor_A_UAT015")
    results.append(check("ac1_create_supplier", isinstance(sid, int) and sid > 0, {"sid": sid}))

    rid, ap = make_sgd_receipt(TENANT_A, "Vendor_A_UAT015", sid, 100.0)
    results.append(check("ac1_approve", ap["status"] == 200, {"status": ap["status"], "rid": rid, "body": ap["body"]}))

    rc = call("GET", "/api/receipt/%d" % rid, tenant=TENANT_A)
    data = (rc["body"] or {}).get("data") or {}
    results.append(check(
        "ac1_archive_original_sgd100",
        data.get("currency") == "SGD" and float(data.get("total_amount") or 0) == 100.0,
        {"currency": data.get("currency"), "total_amount": data.get("total_amount")},
    ))

    p = call("GET", "/api/payables?supplier_id=%d" % sid, tenant=TENANT_A)
    pdata = (p["body"] or {}).get("data") or {}
    rows = pdata.get("rows") or []
    row = next((r for r in rows if r.get("receipt_id") == rid or r.get("id") == rid), None)
    results.append(check(
        "ac1_payables_hkd500",
        pdata.get("currency") == "HKD"
        and float(pdata.get("total_outstanding") or -1) == 500.0
        and row is not None
        and row.get("currency") == "SGD"
        and float(row.get("total_amount") or 0) == 100.0
        and float(row.get("total_hkd") or 0) == 500.0
        and float(row.get("outstanding_amount") or 0) == 500.0
        and row.get("fx_missing") is False
        and row.get("has_fx_snapshot") is True,
        {"pdata_keys": list(pdata.keys()), "row": row, "total_outstanding": pdata.get("total_outstanding")},
    ))

    # supplier unpaid_credit_total
    sl = call("GET", "/api/suppliers", tenant=TENANT_A)
    srows = (sl["body"] or {}).get("data") or []
    s = next((x for x in srows if x.get("id") == sid), None)
    results.append(check(
        "ac1_supplier_unpaid_500",
        s is not None and float(s.get("unpaid_credit_total") or -1) == 500.0,
        {"supplier": s},
    ))

    # --- AC2: partial 100 -> unpaid 400 ---
    pay1 = call("POST", "/api/payments", {
        "supplier_id": sid,
        "amount": 100.0,
        "method": "bank_transfer",
        "paid_at": TODAY,
        "receipt_ids": [rid],
    }, tenant=TENANT_A, form=True)
    results.append(check("ac2_partial_pay_100", pay1["status"] == 200, {"status": pay1["status"], "body": pay1["body"]}))

    p2 = call("GET", "/api/payables?supplier_id=%d" % sid, tenant=TENANT_A)
    pdata2 = (p2["body"] or {}).get("data") or {}
    row2 = next((r for r in (pdata2.get("rows") or []) if r.get("receipt_id") == rid or r.get("id") == rid), None)
    results.append(check(
        "ac2_unpaid_400",
        float(pdata2.get("total_outstanding") or -1) == 400.0
        and row2 is not None
        and float(row2.get("total_hkd") or 0) == 500.0
        and float(row2.get("paid_amount") or 0) == 100.0
        and float(row2.get("outstanding_amount") or 0) == 400.0
        and row2.get("payment_status") == "partial",
        {"row": row2, "total_outstanding": pdata2.get("total_outstanding")},
    ))

    # --- AC3: live FX -> 6, historical still snapshot 5 ---
    fx6 = call("PUT", "/api/settings/fx_rates",
               {"rates": [{"currency": "SGD", "rate": 6.0, "confirmed": True}]},
               tenant=TENANT_A)
    results.append(check("ac3_live_fx_to_6", fx6["status"] == 200, {"status": fx6["status"]}))

    p3 = call("GET", "/api/payables?supplier_id=%d" % sid, tenant=TENANT_A)
    pdata3 = (p3["body"] or {}).get("data") or {}
    row3 = next((r for r in (pdata3.get("rows") or []) if r.get("receipt_id") == rid or r.get("id") == rid), None)
    results.append(check(
        "ac3_historical_still_snapshot_500_400",
        float(pdata3.get("total_outstanding") or -1) == 400.0
        and row3 is not None
        and float(row3.get("total_hkd") or 0) == 500.0
        and float(row3.get("outstanding_amount") or 0) == 400.0
        and float(row3.get("fx_snapshot_rate") or 0) == 5.0,
        {"row": row3, "total_outstanding": pdata3.get("total_outstanding")},
    ))

    # full pay remaining 400 -> 0
    pay2 = call("POST", "/api/payments", {
        "supplier_id": sid,
        "amount": 400.0,
        "method": "fps",
        "paid_at": TODAY,
        "receipt_ids": [rid],
    }, tenant=TENANT_A, form=True)
    results.append(check("ac2_full_pay_400", pay2["status"] == 200, {"status": pay2["status"], "body": pay2["body"]}))

    p4 = call("GET", "/api/payables?supplier_id=%d" % sid, tenant=TENANT_A)
    pdata4 = (p4["body"] or {}).get("data") or {}
    row4 = next((r for r in (pdata4.get("rows") or []) if r.get("receipt_id") == rid or r.get("id") == rid), None)
    def _f(v, default=None):
        if v is None:
            return default
        return float(v)
    results.append(check(
        "ac2_full_unpaid_0",
        row4 is not None
        and _f(pdata4.get("total_outstanding")) == 0.0
        and _f(row4.get("outstanding_amount")) == 0.0
        and _f(row4.get("paid_amount")) == 500.0
        and row4.get("payment_status") == "paid",
        {"row": row4, "total_outstanding": pdata4.get("total_outstanding")},
    ))

    # --- AC4: missing snapshot legacy → unknown, not hard-label HKD ---
    sid_l = create_supplier(TENANT_A, "Vendor_Legacy_UAT015")
    # reset FX to 5 for new receipt snapshot
    call("PUT", "/api/settings/fx_rates",
         {"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]},
         tenant=TENANT_A)
    rid_l, ap_l = make_sgd_receipt(TENANT_A, "Vendor_Legacy_UAT015", sid_l, 120.0)
    results.append(check("ac4_legacy_approve", ap_l["status"] == 200, {"rid": rid_l, "status": ap_l["status"]}))
    delete_fx_snapshot(rid_l, TENANT_A)

    pl = call("GET", "/api/payables?supplier_id=%d" % sid_l, tenant=TENANT_A)
    pdata_l = (pl["body"] or {}).get("data") or {}
    row_l = next((r for r in (pdata_l.get("rows") or []) if r.get("receipt_id") == rid_l or r.get("id") == rid_l), None)
    results.append(check(
        "ac4_missing_snapshot_unknown_not_hkd",
        row_l is not None
        and row_l.get("has_fx_snapshot") is False
        and row_l.get("fx_missing") is True
        and row_l.get("total_hkd") is None,
        {"row": row_l},
    ))

    pay_l = call("POST", "/api/payments", {
        "supplier_id": sid_l,
        "amount": 120.0,
        "method": "cash",
        "paid_at": TODAY,
        "receipt_ids": [rid_l],
    }, tenant=TENANT_A, form=True)
    code_l = (pay_l["body"] or {}).get("code") if isinstance(pay_l["body"], dict) else None
    results.append(check(
        "ac4_pay_rejected_fx_missing",
        pay_l["status"] == 400 and code_l == "FX_RATE_MISSING",
        {"status": pay_l["status"], "code": code_l, "body": pay_l["body"]},
    ))

    # --- Negative: overpayment ---
    sid_o = create_supplier(TENANT_A, "Vendor_Overpay_UAT015")
    call("PUT", "/api/settings/fx_rates",
         {"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]},
         tenant=TENANT_A)
    rid_o, ap_o = make_sgd_receipt(TENANT_A, "Vendor_Overpay_UAT015", sid_o, 100.0)
    pay_o = call("POST", "/api/payments", {
        "supplier_id": sid_o,
        "amount": 550.0,
        "method": "cash",
        "paid_at": TODAY,
        "receipt_ids": [rid_o],
    }, tenant=TENANT_A, form=True)
    code_o = (pay_o["body"] or {}).get("code") if isinstance(pay_o["body"], dict) else None
    results.append(check(
        "neg_overpayment_rejected",
        pay_o["status"] == 400 and code_o == "PAYMENT_OVER_OUTSTANDING",
        {"status": pay_o["status"], "code": code_o},
    ))

    # --- Tenant B isolation ---
    create_supplier(TENANT_B, "Vendor_B_UAT015")
    pb = call("GET", "/api/payables", tenant=TENANT_B)
    pb_rows = ((pb["body"] or {}).get("data") or {}).get("rows") or []
    # B should not see A's SGD rows
    leak = [r for r in pb_rows if "UAT015" in str(r.get("supplier_name") or "") and "Vendor_B" not in str(r.get("supplier_name") or "")]
    # also check A vendors not in B list
    results.append(check(
        "neg_tenant_b_no_a_payables",
        len(pb_rows) == 0 or len(leak) == 0,
        {"row_count": len(pb_rows), "leak": leak[:3]},
    ))

    sid_b_list = call("GET", "/api/suppliers", tenant=TENANT_B)
    sids_b = [s["id"] for s in ((sid_b_list["body"] or {}).get("data") or [])]
    sid_b = sids_b[0] if sids_b else None
    if sid_b is not None:
        hack = call("POST", "/api/payments", {
            "supplier_id": sid_b,
            "amount": 500.0,
            "method": "cash",
            "paid_at": TODAY,
            "receipt_ids": [rid],
        }, tenant=TENANT_B, form=True)
        results.append(check(
            "neg_tenant_b_cannot_pay_a_receipt",
            hack["status"] in (404, 400),
            {"status": hack["status"], "body": hack["body"]},
        ))
    else:
        results.append(check("neg_tenant_b_cannot_pay_a_receipt", False, "no supplier B"))

    passed = sum(1 for r in results if r["pass"])
    total = len(results)
    verdict = "PASS" if passed == total else "FAIL"
    out = {
        "card": "UAT-015",
        "verdict": verdict,
        "passed": passed,
        "total": total,
        "results": results,
        "meta": {"tenant_a": TENANT_A, "tenant_b": TENANT_B, "rid_main": rid, "sid_main": sid},
    }
    (EV / "currency-payables-probe.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "passed": passed, "total": total}, ensure_ascii=False))
    for r in results:
        print(("PASS" if r["pass"] else "FAIL"), r["name"], r.get("detail") if not r["pass"] else "")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
