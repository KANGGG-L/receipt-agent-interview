# -*- coding: utf-8 -*-
"""S1-P2-01：校验失败、无权、业务拒绝都是带中文 msg 的 4xx 信封。"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app

STAFF = {"X-Role": "staff"}


def _chinese(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _assert_envelope(resp):
    assert 400 <= resp.status_code < 500
    body = resp.json()
    assert set(["status", "code", "msg"]) <= set(body)
    assert body["status"] == "error"
    assert isinstance(body["code"], str) and body["code"]
    assert _chinese(body["msg"])
    assert "Bearer" not in body["msg"] and "X-Role" not in body["msg"]


def test_validation_forbidden_and_business_reject_share_one_envelope():
    client = TestClient(app)
    _assert_envelope(client.get("/api/receipts?limit=1001", headers=STAFF))
    _assert_envelope(client.get("/api/cost_report?month=2026-10", headers=STAFF))
    rejected = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "供应商甲",
        "date": "2026-10-05",
        "total_amount": 10,
        "settlement_type": "",
        "items": [{
            "name": "菜心", "quantity": 1, "unit": "斤",
            "unit_price": 10, "amount": 10,
        }],
    })
    _assert_envelope(rejected)
    assert rejected.json()["code"] == "SETTLEMENT_REQUIRED"
