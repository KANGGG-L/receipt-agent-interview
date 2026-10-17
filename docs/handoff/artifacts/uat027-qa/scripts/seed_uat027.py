# -*- coding: utf-8 -*-
"""UAT-027 QA seed: synthetic prices 1234.5 / 0 / null; inbound for price history; SGD finance row."""
from __future__ import annotations
import json, os, sys
from pathlib import Path

DB_PATH = os.environ["DB_PATH"]
OUT = Path(os.environ.get(
    "SEED_OUT",
    "/workspace/receipt-agent-interview/docs/handoff/artifacts/uat027-qa/logs/seed.json",
))

sys.path.insert(0, "/workspace/receipt-agent-interview/demo")
from app import db

db.DB_PATH = DB_PATH
db._make_engine()

TENANT = "default"


def ensure_sku(name, **kw):
    existing = db.find_sku_by_name(name, tenant_id=TENANT)
    if existing:
        return existing.id
    sku_id, err = db.create_sku(name, tenant_id=TENANT, **kw)
    assert not err, err
    return sku_id


sku_hi = ensure_sku("UAT027_SynthHi", category="食材", base_unit="kg", min_stock_alert=1.0)
sku_zero = ensure_sku("UAT027_SynthZero", category="食材", base_unit="kg", min_stock_alert=1.0)
sku_null = ensure_sku("UAT027_SynthNull", category="食材", base_unit="kg", min_stock_alert=1.0)

db.update_sku(sku_hi, current_stock=10.0, last_unit_price=1234.5)
db.update_sku(sku_zero, current_stock=5.0, last_unit_price=0.0)
db.update_sku(sku_null, current_stock=3.0, last_unit_price=None)

# Price history (kind=in) — unit 1234.5
# Clear prior logs for idempotent re-seed of this SKU's in rows is not required; apply once.
s = db.get_session()
try:
    existing_in = s.query(db._StockLogRow).filter(
        db._StockLogRow.sku_id == sku_hi, db._StockLogRow.kind == "in"
    ).count()
finally:
    s.close()

if existing_in == 0:
    db.apply_stock_log(
        sku_hi, "UAT027_SynthHi", qty=2.0, unit="kg", amount=2469.0,
        vendor="Vendor_A", date="2026-09-01", receipt_id=None, kind="in", tenant_id=TENANT,
    )
    db.apply_stock_log(
        sku_hi, "UAT027_SynthHi", qty=1.0, unit="kg", amount=1234.5,
        vendor="Vendor_A", date="2026-10-01", receipt_id=None, kind="in", tenant_id=TENANT,
    )

s = db.get_session()
try:
    z_in = s.query(db._StockLogRow).filter(
        db._StockLogRow.sku_id == sku_zero, db._StockLogRow.kind == "in"
    ).count()
finally:
    s.close()
if z_in == 0:
    db.apply_stock_log(
        sku_zero, "UAT027_SynthZero", qty=1.0, unit="kg", amount=0.0,
        vendor="Vendor_B", date="2026-10-10", receipt_id=None, kind="in", tenant_id=TENANT,
    )

# Foreign SGD receipt for finance (original currency path)
s = db.get_session()
try:
    Receipt = db._ReceiptRow
    existing = s.query(Receipt).filter(Receipt.supplier_name == "Vendor_SGD_UAT027").first()
    if not existing:
        r = Receipt(
            supplier_name="Vendor_SGD_UAT027",
            receipt_date="2026-10-01",
            sheet_name="2026-10",
            currency="SGD",
            total_amount=1234.5,
            status="approved",
            settlement_type="credit",
            tenant_id=TENANT,
            created_at=db.now_iso(),
            updated_at=db.now_iso(),
        )
        s.add(r)
        s.commit()
        rid = r.id
    else:
        rid = existing.id
finally:
    s.close()

payload = {
    "sku_hi": sku_hi,
    "sku_zero": sku_zero,
    "sku_null": sku_null,
    "sgd_receipt_id": rid,
    "prices": {"hi": 1234.5, "zero": 0.0, "null": None},
    "vendors_in_prose": ["Vendor_A", "Vendor_B", "Vendor_SGD_UAT027"],
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(payload, ensure_ascii=False))
