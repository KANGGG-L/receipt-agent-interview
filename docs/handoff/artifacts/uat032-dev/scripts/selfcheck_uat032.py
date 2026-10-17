# -*- coding: utf-8 -*-
"""UAT-032 isolated-port self-check: same-day 3+2 merge → response/table/dialog snapshot qty=5."""
import json
import os
import re
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("DEV_BASE", "http://127.0.0.1:15132")
TN = "uat032dev"
TN_B = "uat032tenantb"
OUT = os.environ.get(
    "UAT032_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat032-dev/logs",
)
REPO = "/workspace/receipt-agent-interview"
JS = os.path.join(REPO, "demo/static/js/dish_consumption.js")
API = os.path.join(REPO, "demo/app/api_dishes.py")


def req(method, path, body=None, tenant=TN, role="owner"):
    data = None
    hdrs = {
        "X-Role": role,
        "X-Email": "boss@uat032.hk" if role == "owner" else "staff@uat032.hk",
        "X-Tenant-Id": tenant,
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    r = urllib.request.Request(BASE + path, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw) if raw else {}
        except Exception:
            return e.code, {"raw": raw}


def create_sku(name, unit="kg", tenant=TN):
    st, j = req(
        "POST",
        "/api/inventory/skus",
        role="staff",
        tenant=tenant,
        body={"name": name, "base_unit": unit, "category": "肉类", "min_stock_alert": 0},
    )
    assert st == 200 and j.get("status") == "success", (st, j)
    return j["id"] if "id" in j else (j.get("data") or {})["id"]


def staff_save(name, qty, unit_price, supplier, date="2026-04-01", tenant=TN):
    total = qty * unit_price
    body = {
        "supplier_name": supplier,
        "date": date,
        "total_amount": total,
        "settlement_type": "credit",
        "currency": "HKD",
        "items": [{
            "name": name,
            "quantity": qty,
            "unit": "kg",
            "unit_price": unit_price,
            "amount": total,
        }],
        "version": None,
        "source": "manual",
    }
    st, j = req("POST", "/api/save_edited", role="staff", tenant=tenant, body=body)
    assert st == 200 and j.get("status") == "success", (st, j)
    return j.get("receipt_id") or j.get("id") or (j.get("data") or {}).get("receipt_id")


def approve(rid, tenant=TN):
    st, detail = req("GET", "/api/receipt/%d" % int(rid), tenant=tenant)
    assert st == 200, (st, detail)
    data = detail.get("data") or detail
    ver = data.get("version")
    st2, ap = req(
        "POST",
        "/api/receipt/%d/approve" % int(rid),
        tenant=tenant,
        body={"version": ver},
    )
    assert st2 == 200 and ap.get("status") == "success", (st2, ap)
    return ap


def main():
    evidence = {"checks": [], "verdict": "FAIL"}
    os.makedirs(OUT, exist_ok=True)

    def ok(name, cond, detail=None):
        evidence["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
        if not cond:
            raise AssertionError("%s failed: %s" % (name, detail))

    # Static source markers (dialog race fix)
    js = open(JS, encoding="utf-8").read()
    api = open(API, encoding="utf-8").read()
    ok("js_await_load_before_modal",
       "await loadDailyConsumption(dateStr)" in js and "fromSnap" in js)
    ok("js_showCostTraceModal_always_fetch",
       "始终按当日接口取最新累计记录" in js or "UAT-032：始终按当日" in js)
    ok("js_cache_bust_uat032",
       "dish_consumption.js?v=20261017_uat032" in open(
           os.path.join(REPO, "demo/templates/index.html"), encoding="utf-8").read())
    ok("api_consumptions_snapshot",
       "consumption_snapshots" in api and "_serialize_consumption_row" in api)

    # Tenant B baseline
    sku_b = create_sku("B侧牛肉UAT032", tenant=TN_B)
    rid_b = staff_save("B侧牛肉UAT032", 3.0, 30.0, "B供应商", tenant=TN_B)
    approve(rid_b, tenant=TN_B)
    st, inv_b0 = req("GET", "/api/inventory/skus/%d" % int(sku_b), tenant=TN_B)
    assert st == 200, (st, inv_b0)
    b0 = inv_b0.get("data") or inv_b0
    stock_b0 = float(b0.get("current_stock") or 0)
    ok("tenant_b_baseline", stock_b0 == 3.0, stock_b0)

    # Tenant A: sku + dish
    sku = create_sku("UAT032牛肉")
    rid = staff_save("UAT032牛肉", 100.0, 10.0, "A供应商")
    approve(rid)
    st, dish_j = req("POST", "/api/dishes", tenant=TN, body={
        "name": "UAT032牛肉饭",
        "price": 30.0,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.5, "unit": "kg"}],
    })
    assert st == 200 and dish_j.get("status") == "success", (st, dish_j)
    dish_id = dish_j["data"]["id"]
    day_a = "2026-04-10"
    day_b = "2026-04-11"

    # First submit 3
    st, r1 = req("POST", "/api/dishes/daily_consumption/batch", body={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 3}],
        "request_id": "uat032-iso-1",
    })
    ok("first_submit_ok", st == 200 and r1.get("status") == "success", (st, r1))
    ok("first_not_merged", r1["data"]["merged"] is False)
    ok("first_qty_3", r1["data"]["quantity"] == 3.0)
    ok("first_snap_details_1", len(r1["data"]["consumptions"][0]["details"]) == 1)
    cid = r1["data"]["consumption_ids"][0]

    # Second submit 2 (different request_id) → merge 5
    st, r2 = req("POST", "/api/dishes/daily_consumption/batch", body={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 2}],
        "request_id": "uat032-iso-2",
    })
    ok("second_submit_ok", st == 200 and r2.get("status") == "success", (st, r2))
    ok("second_merged", r2["data"]["merged"] is True)
    ok("second_qty_5", r2["data"]["quantity"] == 5.0, r2["data"].get("quantity"))
    ok("second_same_cid", r2["data"]["consumption_ids"] == [cid])
    snap = r2["data"]["consumptions"][0]
    ok("dialog_snap_qty_5", snap["quantity"] == 5.0, snap.get("quantity"))
    ok("dialog_snap_cost_25", float(snap["total_cost"]) == 25.0, snap.get("total_cost"))
    ok("dialog_snap_details_2", len(snap["details"]) == 2, len(snap["details"]))
    ok("dialog_snap_detail_cost_sum",
       round(sum(float(d["total_cost"]) for d in snap["details"]), 2) == 25.0)

    # Table/list
    st, day = req("GET", "/api/dishes/daily_consumption?date=" + day_a)
    cons = day["data"]["consumptions"]
    ok("table_one_row", len(cons) == 1)
    ok("table_qty_5", cons[0]["quantity"] == 5.0)
    ok("table_cost_25", float(cons[0]["total_cost"]) == 25.0)
    ok("table_details_2", len(cons[0]["details"]) == 2)
    ok("table_matches_dialog_snap",
       cons[0]["quantity"] == snap["quantity"]
       and float(cons[0]["total_cost"]) == float(snap["total_cost"])
       and len(cons[0]["details"]) == len(snap["details"]))

    # Replay second request_id
    st, replay = req("POST", "/api/dishes/daily_consumption/batch", body={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 2}],
        "request_id": "uat032-iso-2",
    })
    ok("replay_ok", st == 200 and replay.get("status") == "success")
    ok("replay_flag", replay["data"].get("replayed") is True)
    ok("replay_qty_still_5", replay["data"]["quantity"] == 5.0)
    ok("replay_snap_details_2",
       len(replay["data"]["consumptions"][0]["details"]) == 2)

    # Different day no leak
    st, ro = req("POST", "/api/dishes/daily_consumption/batch", body={
        "date": day_b,
        "items": [{"dish_id": dish_id, "quantity": 1}],
        "request_id": "uat032-iso-dayb",
    })
    ok("other_day_qty_1", ro["data"]["quantity"] == 1.0 and ro["data"]["merged"] is False)
    st, day_a2 = req("GET", "/api/dishes/daily_consumption?date=" + day_a)
    ok("day_a_unpolluted",
       len(day_a2["data"]["consumptions"]) == 1
       and day_a2["data"]["consumptions"][0]["quantity"] == 5.0
       and len(day_a2["data"]["consumptions"][0]["details"]) == 2)
    st, day_b2 = req("GET", "/api/dishes/daily_consumption?date=" + day_b)
    ok("day_b_isolated",
       len(day_b2["data"]["consumptions"]) == 1
       and day_b2["data"]["consumptions"][0]["quantity"] == 1.0)

    # Tenant B unchanged
    st, one_b = req("GET", "/api/inventory/skus/%d" % int(sku_b), tenant=TN_B)
    stock_b1 = float((one_b.get("data") or one_b).get("current_stock") or 0)
    ok("tenant_b_unchanged", stock_b1 == stock_b0, (stock_b0, stock_b1))

    # Static asset served with new cache bust
    st_asset = None
    try:
        r = urllib.request.Request(
            BASE + "/static/js/dish_consumption.js?v=20261017_uat032")
        with urllib.request.urlopen(r, timeout=30) as resp:
            st_asset = resp.status
            body_js = resp.read().decode("utf-8", errors="replace")
        ok("static_js_served", st_asset == 200 and "fromSnap" in body_js)
    except Exception as e:
        ok("static_js_served", False, str(e))

    evidence["verdict"] = "PASS" if all(c["pass"] for c in evidence["checks"]) else "FAIL"
    evidence["cid"] = cid
    evidence["day_a"] = day_a
    path = os.path.join(OUT, "selfcheck-evidence.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": evidence["verdict"],
                      "passed": sum(1 for c in evidence["checks"] if c["pass"]),
                      "total": len(evidence["checks"]),
                      "path": path}, ensure_ascii=False))
    return 0 if evidence["verdict"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        evidence_path = os.path.join(OUT, "selfcheck-evidence.json")
        os.makedirs(OUT, exist_ok=True)
        with open(evidence_path, "w", encoding="utf-8") as f:
            json.dump({"verdict": "FAIL", "error": str(e)}, f, ensure_ascii=False, indent=2)
        print("FAIL:", e)
        sys.exit(1)
