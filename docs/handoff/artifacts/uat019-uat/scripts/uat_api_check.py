# -*- coding: utf-8 -*-
"""UAT019 independent UAT — API acceptance checks (port 15265)."""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date, timedelta
from sqlalchemy import create_engine, text

BASE = os.environ.get("UAT_BASE", "http://127.0.0.1:15265")
TENANT_A = os.environ.get("UAT_TENANT", "uat019uat")
TENANT_B = os.environ.get("UAT_TENANT_B", "uat019uatb")
DB_PATH = os.environ.get(
    "DB_PATH",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat019-uat/environment/uat_isolated.db",
)
OUT = os.path.join(os.path.dirname(__file__), "..", "logs")
os.makedirs(OUT, exist_ok=True)
checks = []


def req(method, path, body=None, tenant=TENANT_A, raw=None, role="owner"):
    url = BASE + path
    data = None
    headers = {
        "X-Role": role,
        "X-Email": "uat@uat019.hk",
        "X-Tenant-Id": tenant,
        "Content-Type": "application/json",
    }
    if raw is not None:
        data = raw if isinstance(raw, bytes) else raw.encode()
    elif body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            raw_b = resp.read().decode()
            try:
                j = json.loads(raw_b)
            except Exception:
                j = {"_raw": raw_b}
            return resp.status, j
    except urllib.error.HTTPError as e:
        raw_b = e.read().decode()
        try:
            j = json.loads(raw_b)
        except Exception:
            j = {"_raw": raw_b}
        return e.code, j


def add(name, passed, detail):
    checks.append({"name": name, "pass": bool(passed), "detail": detail})
    print(("PASS" if passed else "FAIL"), name, json.dumps(detail, ensure_ascii=False)[:280])


def sku_stock(sku_id, tenant=TENANT_A):
    eng = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT current_stock FROM skus WHERE id=:id AND tenant_id=:t"),
            {"id": sku_id, "t": tenant},
        ).fetchone()
    return float(row[0]) if row else None


def seed_stock(sku_id, qty, unit_price=40.0, tenant=TENANT_A):
    """Seed current_stock + FIFO inbound batch via sqlite (same as specialty pytest)."""
    eng = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    today = date.today().isoformat()
    with eng.begin() as conn:
        conn.execute(
            text(
                "UPDATE skus SET current_stock=:q, last_unit_price=:p "
                "WHERE id=:id AND tenant_id=:t"
            ),
            {"q": qty, "p": unit_price, "id": sku_id, "t": tenant},
        )
        existing = conn.execute(
            text(
                "SELECT id FROM inventory_batches WHERE sku_id=:id AND remaining_qty > 0 "
                "LIMIT 1"
            ),
            {"id": sku_id},
        ).fetchone()
        if not existing:
            conn.execute(
                text(
                    "INSERT INTO inventory_batches "
                    "(sku_id, receipt_id, inbound_date, unit_cost, initial_qty, "
                    " remaining_qty, unit, is_closed, is_estimated, created_at) "
                    "VALUES (:sku_id, NULL, :d, :c, :q, :q, 'kg', 0, 0, :d)"
                ),
                {"sku_id": sku_id, "d": today, "c": unit_price, "q": qty},
            )


def setup_dish(stock=1.0, bom=0.5, price=30.0, name_suffix=""):
    st, j = req(
        "POST",
        "/api/inventory/skus",
        {"name": f"UAT019料{name_suffix or '基'}", "category": "测试", "base_unit": "kg"},
    )
    sku_id = j.get("id") or (j.get("data") or {}).get("id")
    if not sku_id:
        add(f"setup_sku{name_suffix}", False, {"status": st, "body": j})
        raise SystemExit(f"setup sku failed: {j}")
    seed_stock(sku_id, stock)
    st2, j2 = req(
        "POST",
        "/api/dishes",
        {
            "name": f"UAT019菜{name_suffix or '基'}",
            "price": price,
            "category": "测试",
            "ingredients": [{"sku_id": sku_id, "consumption_qty": bom, "unit": "kg"}],
        },
    )
    dish_id = (j2.get("data") or {}).get("id")
    add(
        f"setup_dish{name_suffix}",
        st == 200 and st2 == 200 and sku_id and dish_id and abs(sku_stock(sku_id) - stock) < 1e-6,
        {"sku": sku_id, "dish": dish_id, "stock": sku_stock(sku_id), "st_sku": st, "st_dish": st2},
    )
    return sku_id, dish_id


# --- Tenant B baseline before ---
st, j = req("GET", "/api/dishes", tenant=TENANT_B)
add(
    "tenant_b_before",
    st == 200 and len(j.get("data") or []) == 0,
    {"status": st, "n": len(j.get("data") or []), "tenant": TENANT_B},
)

# Seed tenant B SKU baseline 7.0 (isolation check)
st_b, j_b = req(
    "POST",
    "/api/inventory/skus",
    {"name": "B店料UAT019", "category": "测试", "base_unit": "kg"},
    tenant=TENANT_B,
)
sku_b = j_b.get("id") or (j_b.get("data") or {}).get("id")
if sku_b:
    seed_stock(sku_b, 7.0, tenant=TENANT_B)
before_b = sku_stock(sku_b, tenant=TENANT_B)
add("tenant_b_seed", sku_b and before_b == 7.0, {"sku_b": sku_b, "stock": before_b})

# ========== AC1: huge sale ordinary → 409, no write ==========
sku1, dish1 = setup_dish(stock=1.0, bom=0.5, name_suffix="甲")
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish1, "quantity": 999999999999}],
        "request_id": "uat019-huge-1",
    },
)
stock_after = sku_stock(sku1)
add(
    "ac1_huge_ordinary_409",
    st == 409 and j.get("code") == "INSUFFICIENT_STOCK",
    {"status": st, "code": j.get("code"), "msg": j.get("msg"), "data_keys": list((j.get("data") or {}).keys())},
)
add(
    "ac1_stock_unchanged",
    abs((stock_after or -999) - 1.0) < 1e-6,
    {"stock": stock_after},
)
d = j.get("data") or {}
add(
    "ac1_can_override_contract",
    d.get("can_override") is True and d.get("reason_required") is True,
    {"can_override": d.get("can_override"), "reason_required": d.get("reason_required"), "shortfalls": d.get("shortfalls")},
)
add(
    "ac1_not_silent_huge_negative",
    stock_after is not None and stock_after > -1000,
    {"stock": stock_after, "would_have_been": -499999999998.5},
)

# Tenant B unchanged after AC1 reject
after_b1 = sku_stock(sku_b, tenant=TENANT_B)
add("tenant_b_after_ac1", after_b1 == 7.0, {"stock": after_b1})

# ========== AC2: force without reason → 409; with reason → 200 ==========
sku2, dish2 = setup_dish(stock=1.0, bom=0.5, name_suffix="乙")
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish2, "quantity": 4}],
        "allow_negative": True,
    },
)
add(
    "ac2_force_no_reason_409",
    st == 409 and j.get("code") == "INSUFFICIENT_STOCK" and "原因" in (j.get("msg") or ""),
    {"status": st, "code": j.get("code"), "msg": j.get("msg"), "stock": sku_stock(sku2)},
)
add("ac2_stock_still_1", sku2 and abs((sku_stock(sku2) or -999) - 1.0) < 1e-6, {"stock": sku_stock(sku2) if sku2 else None})

st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish2, "quantity": 4}],
        "allow_negative": True,
        "override_reason": "进货单未录入",
        "request_id": "uat019-force-1",
    },
)
add(
    "ac2_force_with_reason_200",
    st == 200 and j.get("status") == "success",
    {"status": st, "body_status": j.get("status"), "msg": j.get("msg")},
)
# 4 * 0.5 = 2 needed; stock 1 → -1
add(
    "ac2_negative_stock_as_contract",
    abs(sku_stock(sku2) - (-1.0)) < 1e-6,
    {"stock": sku_stock(sku2)},
)

# No new business cap: extreme finite WITH force still allowed
sku2b, dish2b = setup_dish(stock=1.0, bom=0.5, name_suffix="丙")
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish2b, "quantity": 999999999999}],
        "allow_negative": True,
        "override_reason": "QA确认极端有限仍可force（无业务cap）",
        "request_id": "uat019-nocap-1",
    },
)
stock_huge = sku_stock(sku2b)
add(
    "ac2_no_new_sale_qty_cap_force_allows_extreme",
    st == 200 and stock_huge is not None and stock_huge < -1e9,
    {"status": st, "stock": stock_huge},
)

# ========== AC3: normal 1 portion deducts 0.5 ==========
sku3, dish3 = setup_dish(stock=1.0, bom=0.5, name_suffix="丁")
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish3, "quantity": 1}],
        "request_id": "uat019-normal-1",
    },
)
add(
    "ac3_normal_deduct_0_5",
    st == 200 and abs(sku_stock(sku3) - 0.5) < 1e-6,
    {"status": st, "stock": sku_stock(sku3)},
)

# ========== AC4: illegal date / non-number no write ==========
sku4, dish4 = setup_dish(stock=1.0, bom=0.5, name_suffix="戊")
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {"date": "not-a-date", "items": [{"dish_id": dish4, "quantity": 1}]},
)
add(
    "ac4_bad_date",
    st == 400 and j.get("code") == "INVALID_CONSUMPTION_DATE",
    {"status": st, "code": j.get("code")},
)
future = (date.today() + timedelta(days=2)).isoformat()
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    {"date": future, "items": [{"dish_id": dish4, "quantity": 1}]},
)
add("ac4_future_date", st == 400, {"status": st, "code": j.get("code")})
st, j = req(
    "POST",
    "/api/dishes/daily_consumption/batch",
    raw=('{"items":[{"dish_id":%d,"quantity":NaN}]}' % dish4).encode(),
)
add("ac4_nan", st in (400, 422), {"status": st, "code": j.get("code"), "msg": j.get("msg")})
add("ac4_stock_untouched", abs(sku_stock(sku4) - 1.0) < 1e-6, {"stock": sku_stock(sku4)})

# ========== AC5: duplicate request_id no double-deduct ==========
sku5, dish5 = setup_dish(stock=10.0, bom=0.5, name_suffix="己")
body = {
    "date": date.today().isoformat(),
    "items": [{"dish_id": dish5, "quantity": 2}],
    "request_id": "uat019-idem-1",
}
st1, j1 = req("POST", "/api/dishes/daily_consumption/batch", body)
st2, j2 = req("POST", "/api/dishes/daily_consumption/batch", body)
add("ac5_first_ok", st1 == 200, {"status": st1})
add(
    "ac5_replay",
    st2 == 200 and (j2.get("data") or {}).get("replayed") is True,
    {"status": st2, "replayed": (j2.get("data") or {}).get("replayed")},
)
add(
    "ac5_no_double_deduct",
    abs(sku_stock(sku5) - 9.0) < 1e-6,
    {"stock": sku_stock(sku5)},
)

# ========== Reverse 409 not loosened (code-level + smoke if possible) ==========
# Inspect inventory.py still raises ReversalBlocked
inv_path = "/workspace/receipt-agent-interview/demo/app/services/inventory.py"
with open(inv_path, "r", encoding="utf-8") as f:
    inv_src = f.read()
add(
    "reverse_reversal_blocked_intact",
    "class ReversalBlocked" in inv_src
    and "raise ReversalBlocked" in inv_src
    and "Do not loosen ReversalBlocked" in inv_src,
    {"has_class": "class ReversalBlocked" in inv_src, "has_raise": "raise ReversalBlocked" in inv_src},
)

# Final tenant B
after_bf = sku_stock(sku_b, tenant=TENANT_B)
st, j = req("GET", "/api/dishes", tenant=TENANT_B)
add(
    "tenant_b_final",
    after_bf == 7.0 and len(j.get("data") or []) == 0,
    {"stock": after_bf, "dishes_n": len(j.get("data") or [])},
)

passed = sum(1 for c in checks if c["pass"])
total = len(checks)
summary = {"passed": passed, "total": total, "failed": [c for c in checks if not c["pass"]], "checks": checks}
out_path = os.path.join(OUT, "uat_api_check.json")
with open(out_path, "w", encoding="utf-8") as f:
    json.dump(summary, f, ensure_ascii=False, indent=2)
print(f"\nSUMMARY {passed}/{total} -> {out_path}")
sys.exit(0 if passed == total else 1)
