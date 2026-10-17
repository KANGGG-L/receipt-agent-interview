# -*- coding: utf-8 -*-
"""UAT-036 QA seed: a few SKUs under default so recovery shows real numbers; empty tenant stays empty."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

DB_PATH = os.environ["DB_PATH"]
OUT = Path(os.environ.get("SEED_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat036-uat/logs/seed.json"))

sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db

db.DB_PATH = DB_PATH
db._make_engine()

def ensure_sku(name, tenant_id="default", **kw):
    existing = db.find_sku_by_name(name, tenant_id=tenant_id)
    if existing:
        return existing.id
    sku_id, err = db.create_sku(name, tenant_id=tenant_id, **kw)
    assert not err, err
    return sku_id

a = ensure_sku("UAT036_Flour", category="食材", base_unit="kg", min_stock_alert=2.0)
db.update_sku(a, current_stock=10.0, last_unit_price=8.0)
b = ensure_sku("UAT036_Sugar", category="食材", base_unit="kg", min_stock_alert=1.0)
db.update_sku(b, current_stock=0.5, last_unit_price=6.0)  # low stock

# empty tenant: create nothing under empty_uat036
payload = {
    "flour_id": a,
    "sugar_id": b,
    "empty_tenant": "empty_uat036",
    "vendors_in_prose": [],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
