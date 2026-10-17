# -*- coding: utf-8 -*-
"""UAT018 independent QA — API acceptance checks (port 15225)."""
import json
import os
import sys
import urllib.request
import urllib.error
from sqlalchemy import create_engine, text

BASE = os.environ.get("QA_BASE", "http://127.0.0.1:15225")
TENANT_A = os.environ.get("QA_TENANT", "uat018qa")
TENANT_B = os.environ.get("QA_TENANT_B", "uat018qab")
DB_PATH = os.environ.get(
    "DB_PATH",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat018-qa/environment/qa_isolated.db",
)
OUT = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(OUT, exist_ok=True)
checks = []


def req(method, path, body=None, tenant=TENANT_A, allow_nan=False):
    url = BASE + path
    data = None
    headers = {
        "X-Role": "owner",
        "X-Email": "qa@uat018.hk",
        "X-Tenant-Id": tenant,
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body, allow_nan=allow_nan).encode()
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode()
            try:
                j = json.loads(raw)
            except Exception:
                j = {"_raw": raw}
            return resp.status, j
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            j = json.loads(raw)
        except Exception:
            j = {"_raw": raw}
        return e.code, j


def add(name, passed, detail):
    checks.append({"name": name, "pass": bool(passed), "detail": detail})
    print(("PASS" if passed else "FAIL"), name, json.dumps(detail, ensure_ascii=False)[:240])


# Fresh tenant names so prior seed dishes don't confuse; use new tenant
TENANT_A = "uat018qa2"
TENANT_B = "uat018qab2"

st, j = req("GET", "/api/dishes", tenant=TENANT_B)
add("tenant_b_before", st == 200 and len(j.get("data") or []) == 0,
    {"status": st, "n": len(j.get("data") or []), "tenant": TENANT_B})

st, j = req("POST", "/api/inventory/skus", {"name": "QA未采购料UAT018b", "category": "测试", "base_unit": "kg"})
sku_un = j.get("id")
add("create_unpurchased_sku", st == 200 and sku_un, {"status": st, "id": sku_un})

st, j = req("POST", "/api/inventory/skus", {"name": "QA已采购料UAT018b", "category": "测试", "base_unit": "kg"})
sku_purch = j.get("id")
add("create_purchased_sku", st == 200 and sku_purch, {"status": st, "id": sku_purch})

# Seed purchased price (no public price-set API; same approach as specialty pytest db.update_sku)
eng = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
with eng.begin() as conn:
    conn.execute(
        text("UPDATE skus SET last_unit_price=25.0, current_stock=10.0 WHERE id=:id AND tenant_id=:t"),
        {"id": sku_purch, "t": TENANT_A},
    )
    row = conn.execute(
        text("SELECT last_unit_price, current_stock FROM skus WHERE id=:id"), {"id": sku_purch}
    ).fetchone()
add("seed_purchased_price", row and float(row[0]) == 25.0,
    {"last_unit_price": row[0] if row else None, "current_stock": row[1] if row else None, "via": "sqlite_update"})

# AC1
st, j = req("POST", "/api/dishes", {
    "name": "QA极大BOM无采购",
    "price": 88.0,
    "category": "测试",
    "ingredients": [{"sku_id": sku_un, "consumption_qty": 999999999999, "unit": "kg"}],
})
d = j.get("data") or {}
ing0 = (d.get("ingredients") or [{}])[0]
pass_extreme = (
    st == 200
    and d.get("theory_cost_status") == "estimated"
    and d.get("gross_margin_rate") is None
    and d.get("theoretical_cost") is None
    and d.get("gross_profit") is None
    and "unpurchased" in str(d.get("theory_cost_source") or "")
    and float(ing0.get("consumption_qty") or 0) == 999999999999.0
    and ing0.get("cost_known") is False
    # must NOT present definite 100 as certain fields
    and d.get("gross_margin_rate") is not 100
)
add("ac1_extreme_unpurchased_persist_estimate", pass_extreme, {
    "status": st,
    "theory_cost_status": d.get("theory_cost_status"),
    "theory_cost_source": d.get("theory_cost_source"),
    "margin": d.get("gross_margin_rate"),
    "cost": d.get("theoretical_cost"),
    "profit": d.get("gross_profit"),
    "prov_margin": d.get("provisional_gross_margin_rate"),
    "prov_cost": d.get("provisional_theoretical_cost"),
    "qty": ing0.get("consumption_qty"),
    "cost_known": ing0.get("cost_known"),
    "cost_source": ing0.get("cost_source"),
})
dish_extreme_id = d.get("id")

st_l, jl = req("GET", "/api/dishes")
found = next((x for x in (jl.get("data") or []) if x.get("id") == dish_extreme_id), None)
pass_persist = (
    found is not None
    and float((found.get("ingredients") or [{}])[0].get("consumption_qty") or 0) == 999999999999.0
    and found.get("theory_cost_status") == "estimated"
    and found.get("gross_margin_rate") is None
    and found.get("theoretical_cost") is None
)
add("ac1_list_persist_no_clamp", pass_persist, {
    "found": found is not None,
    "qty": (found.get("ingredients") or [{}])[0].get("consumption_qty") if found else None,
    "status": (found or {}).get("theory_cost_status"),
    "margin": (found or {}).get("gross_margin_rate"),
    "cost": (found or {}).get("theoretical_cost"),
})

# AC2
for label, qty in [("neg", -1.0), ("zero", 0.0)]:
    st, j = req("POST", "/api/dishes", {
        "name": f"QA bad {label}",
        "price": 10.0,
        "ingredients": [{"sku_id": sku_un, "consumption_qty": qty, "unit": "kg"}],
    })
    add(f"ac2_reject_{label}", st == 400 and j.get("code") == "INVALID_CONSUMPTION_QTY",
        {"status": st, "code": j.get("code"), "msg": j.get("msg")})

for label, qty in [("nan", float("nan")), ("inf", float("inf")), ("ninf", float("-inf"))]:
    st, j = req("POST", "/api/dishes", {
        "name": f"QA bad {label}",
        "price": 10.0,
        "ingredients": [{"sku_id": sku_un, "consumption_qty": qty, "unit": "kg"}],
    }, allow_nan=True)
    add(f"ac2_reject_{label}", st == 400 and j.get("code") == "INVALID_CONSUMPTION_QTY",
        {"status": st, "code": j.get("code"), "msg": j.get("msg"), "rejected": label})

# AC4
st, j = req("POST", "/api/dishes", {
    "name": "QA正常已采购BOM",
    "price": 100.0,
    "category": "测试",
    "ingredients": [{"sku_id": sku_purch, "consumption_qty": 0.4, "unit": "kg"}],
})
d = j.get("data") or {}
pass_purch = (
    st == 200
    and d.get("theory_cost_status") == "complete"
    and d.get("theoretical_cost") == 10.0
    and d.get("gross_margin_rate") == 90.0
)
add("ac4_purchased_normal_cost", pass_purch, {
    "status": st,
    "theory_cost_status": d.get("theory_cost_status"),
    "cost": d.get("theoretical_cost"),
    "margin": d.get("gross_margin_rate"),
    "source": d.get("theory_cost_source"),
})
dish_purch_id = d.get("id")

sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import dishes_store
add("open_risk_not_b26",
    dishes_store.BOM_QTY_UPPER_LIMIT_STATUS == "open_risk_not_adjudicated"
    and dishes_store.BOM_QTY_UPPER_LIMIT_SOURCE is None,
    {"status": dishes_store.BOM_QTY_UPPER_LIMIT_STATUS, "source": dishes_store.BOM_QTY_UPPER_LIMIT_SOURCE,
     "note": getattr(dishes_store, "BOM_QTY_UPPER_LIMIT_NOTE", "")[:120]})

st, j = req("GET", "/api/dishes", tenant=TENANT_B)
add("tenant_b_after", st == 200 and len(j.get("data") or []) == 0,
    {"status": st, "n": len(j.get("data") or [])})

bom = open("/workspace/receipt-agent-interview/demo/static/js/dish_bom.js", encoding="utf-8").read()
cons = open("/workspace/receipt-agent-interview/demo/static/js/dish_consumption.js", encoding="utf-8").read()
add("ui_source_markers",
    "未知/估算" in bom and "dishTheoryMarginBadgeHtml" in bom
    and "Number.isFinite(qtyVal)" in cons and "不可为 NaN、Infinity" in cons,
    {"bom_unknown": "未知/估算" in bom, "cons_finite": "Number.isFinite(qtyVal)" in cons})

failed = sum(1 for c in checks if not c["pass"])
summary = {
    "total": len(checks), "failed": failed, "pass": failed == 0,
    "dish_extreme_id": dish_extreme_id, "dish_purch_id": dish_purch_id,
    "sku_un": sku_un, "sku_purch": sku_purch,
    "base": BASE, "tenant": TENANT_A, "tenant_b": TENANT_B,
}
result = {"checks": checks, "summary": summary}
with open(os.path.join(OUT, "qa_api_check.json"), "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=2)
# also dump tenant B snapshots
with open(os.path.join(OUT, "tenant_b_final.json"), "w", encoding="utf-8") as f:
    json.dump({"tenant": TENANT_B, "status": st, "data": j.get("data")}, f, ensure_ascii=False, indent=2)
print("SUMMARY", json.dumps(summary, ensure_ascii=False))
sys.exit(0 if failed == 0 else 1)
