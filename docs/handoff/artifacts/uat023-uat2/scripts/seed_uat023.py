# -*- coding: utf-8 -*-
"""UAT-023 independent UAT seed: long Hans biz names for painted preview."""
from __future__ import annotations
import json, os, sys, sqlite3
from pathlib import Path

DB_PATH = os.environ["DB_PATH"]
OUT = Path(os.environ.get("SEED_OUT", "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat023-uat2/logs/seed.json"))

LONG_NAME = "有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）"
LONG_SUPPLIER = "鲜生农产供应商（特长名称说明）"
LONG_DISH = "有机走地鸡胸肉煲（超长餐品名称规格说明）"
LONG_DEPT = "后厨冷链中心（超长部门名称）"
XSS_NAME = "鸡翅<script>alert(1)</script>"

sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db

db.DB_PATH = DB_PATH
db._make_engine()

def ensure_sku(name, **kw):
    existing = db.find_sku_by_name(name, tenant_id="default")
    if existing:
        return existing.id
    sku_id, err = db.create_sku(name, tenant_id="default", **kw)
    assert not err, err
    return sku_id

sku_id = ensure_sku(LONG_NAME, category="肉类", base_unit="kg", min_stock_alert=1.0)
db.update_sku(sku_id, current_stock=12.5, last_unit_price=88.0)
sku_xss = ensure_sku(XSS_NAME, category="肉类", base_unit="kg")
db.update_sku(sku_xss, current_stock=3.0, last_unit_price=40.0)
dept_id = db.create_department(LONG_DEPT)
db.create_supplier(LONG_SUPPLIER)
rid = db.create_receipt_record(
    supplier_name=LONG_SUPPLIER,
    status="approved",
    receipt_date="2026-09-10",
    total_amount=880.0,
    currency="HKD",
    paid_at="2026-09-11T08:00:00",
    expected_pay_date="",
    department_id=dept_id,
    created_at="2026-09-10T08:00:00",
    updated_at="2026-09-10T08:00:00",
)
db.set_receipt_items(
    rid,
    [{"name": LONG_NAME, "quantity": 10, "unit": "kg", "unit_price": 88.0, "amount": 880.0}],
)
db.apply_stock_log(
    sku_id, LONG_NAME, 10.0, "kg", 880.0, LONG_SUPPLIER,
    "2026-09-10", rid, "in", tenant_id="default",
)
db.apply_stock_log(
    sku_id, LONG_NAME, 2.0, "kg", 176.0, LONG_SUPPLIER,
    "2026-09-12", rid, "consume", tenant_id="default",
)
dish_id = None
try:
    dish_id = db.create_dish(LONG_DISH, category="热菜", price=128.0, status="active")
except TypeError:
    try:
        dish_id = db.create_dish(name=LONG_DISH, category="热菜", sell_price=128.0)
    except Exception as e:
        print("WARN create_dish:", e)
except Exception as e:
    print("WARN create_dish:", e)

con = sqlite3.connect(DB_PATH)
sku_name = con.execute("SELECT name FROM skus WHERE id=?", (sku_id,)).fetchone()[0]
sup_name = con.execute("SELECT name FROM suppliers WHERE name=?", (LONG_SUPPLIER,)).fetchone()
con.close()

payload = {
    "sku_id": sku_id,
    "sku_name": sku_name,
    "xss_sku_id": sku_xss,
    "dept_id": dept_id,
    "receipt_id": rid,
    "dish_id": dish_id,
    "long_name": LONG_NAME,
    "long_supplier": LONG_SUPPLIER,
    "long_dept": LONG_DEPT,
    "xss_name": XSS_NAME,
    "db_name_matches": sku_name == LONG_NAME,
    "supplier_present": bool(sup_name),
}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False, indent=2))
