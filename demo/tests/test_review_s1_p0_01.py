# -*- coding: utf-8 -*-
"""S1-P0-01：折扣后的应付总额可以入账，库存流水记在这张已入账单上。"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app

STAFF = {"X-Role": "staff"}
OWNER = {"X-Role": "owner"}


def test_discounted_payable_posts_and_inventory_follows_that_receipt():
    client = TestClient(app)
    saved = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "供应商甲",
        "date": "2026-10-05",
        "total_amount": 90,
        "discount_amount": 10,
        "settlement_type": "cash",
        "items": [{
            "name": "菜心", "quantity": 10, "unit": "斤",
            "unit_price": 10, "amount": 100,
        }],
        "source": "manual",
    })
    assert saved.status_code == 200, saved.text
    rid = saved.json()["receipt_id"]
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.total_amount == 90.0 and row.discount_amount == 10.0
    approved = client.post("/api/receipt/%d/approve" % rid, headers=OWNER,
                           json={"version": row.version})
    assert approved.status_code == 200, approved.text
    posted = db.get_receipt_row(rid, tenant_id="default")
    assert posted.status == "approved" and posted.total_amount == 90.0
    session = db.get_session()
    try:
        logs = session.query(db._StockLogRow).filter(db._StockLogRow.receipt_id == rid).all()
    finally:
        session.close()
    assert len(logs) == 1 and logs[0].kind == "in"
    assert logs[0].qty == 10.0
    assert logs[0].name == "菜心"
