# -*- coding: utf-8 -*-
"""S1-P2-03：结算方式不合法的保存不得留下状态变更审计。"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app

STAFF = {"X-Role": "staff"}


def test_rejected_save_does_not_append_status_audit():
    client = TestClient(app)
    rid = db.create_receipt(supplier_name="供应商甲", status="edited", tenant_id="default")
    before = db.get_receipt_row(rid, tenant_id="default")
    prior = before.audit_logs_json
    resp = client.post("/api/save_edited", headers=STAFF, json={
        "receipt_id": rid,
        "version": before.version,
        "supplier_name": "供应商甲",
        "date": "2026-10-05",
        "total_amount": 10,
        "settlement_type": "",
        "items": [{
            "name": "菜心", "quantity": 1, "unit": "斤",
            "unit_price": 10, "amount": 10,
        }],
    })
    assert resp.status_code == 422
    after = db.get_receipt_row(rid, tenant_id="default")
    assert after.audit_logs_json == prior
    assert "状态已变更" not in (after.audit_logs_json or "")
    assert after.status == "edited"
