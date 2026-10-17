# -*- coding: utf-8 -*-
"""UAT-024 QA seed: Synthetic_Rice / Synthetic_Oil / long ZH for mobile inventory."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

DB_PATH = os.environ["DB_PATH"]
OUT = Path(os.environ.get("SEED_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat024-qa/logs/seed.json"))

RICE = "Synthetic_Rice"
OIL = "Synthetic_Oil"
LONG_ZH = "特级有机黑糯米_" + ("测" * 20)

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

rice_id = ensure_sku(RICE, category="食材", base_unit="kg", min_stock_alert=1.0)
db.update_sku(rice_id, current_stock=10.0, last_unit_price=12.0)
oil_id = ensure_sku(OIL, category="食材", base_unit="L", min_stock_alert=1.0)
db.update_sku(oil_id, current_stock=5.0, last_unit_price=28.0)
long_id = ensure_sku(LONG_ZH, category="食材", base_unit="kg", min_stock_alert=1.0)
db.update_sku(long_id, current_stock=3.0, last_unit_price=40.0)

# Tenant B isolation fixture (Vendor_B naming in prose only — SKU under tenant t2)
t2_id = ensure_sku("TenantB_Rice", category="食材", base_unit="kg", min_stock_alert=1.0) if False else None
# create under tenant t2 explicitly
existing_t2 = db.find_sku_by_name("Synthetic_Rice", tenant_id="t2")
if existing_t2:
    t2_sku = existing_t2.id
else:
    t2_sku, err = db.create_sku("Synthetic_Rice", category="食材", base_unit="kg",
                                min_stock_alert=1.0, tenant_id="t2")
    assert not err, err
db.update_sku(t2_sku, current_stock=9.0, last_unit_price=11.0)

payload = {
    "rice_id": rice_id,
    "oil_id": oil_id,
    "long_id": long_id,
    "long_name": LONG_ZH,
    "long_len": len(LONG_ZH),
    "t2_sku_id": t2_sku,
    "vendors_in_prose": ["Vendor_A", "Vendor_B"],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
