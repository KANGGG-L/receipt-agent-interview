# -*- coding: utf-8 -*-
"""L07 供应商退款（C-CREDIT：pending 占用、取消释放、已收保持占用、幂等、不足 409）。"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l07_refund_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l07.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l07-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402
from app import auth, db  # noqa: E402
from app import credit_store, recon_store  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()
client = TestClient(app)
_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk"}


@pytest.fixture(autouse=True)
def _clean_tables():
    from app import recon_store as _rs
    _rs._gate._ensure(None)
    s = db.get_session()
    try:
        for tbl in ("credit_entries", "credit_documents", "credit_allocations",
                    "posting_headers", "payment_allocations", "supplier_refunds"):
            s.execute(text("DELETE FROM %s" % tbl))
        s.commit()
    finally:
        s.close()
    yield


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _new_sku(name="Item_A"):
    sku_id, _ = db.create_sku(name, base_unit="kg", tenant_id="default")
    return sku_id


def _item(sku_id, qty, amount):
    return {"name": "Item_A", "quantity": qty, "unit": "kg",
            "unit_price": round(amount / qty, 4), "amount": amount, "sku_id": sku_id}


def _save(items, doc_form="printed_delivery_note", amount=None):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": doc_form, "settlement_type": "credit",
               "currency": "HKD",
               "total_amount": sum(i["amount"] for i in items) if amount is None else amount,
               "items": items}
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def _approve(rid, version, action=None):
    body = {"version": version}
    if action:
        body["business_action"] = action
    return client.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"), json=body)


def _refund(credit_id, body):
    return client.post("/api/supplier-credits/%d/refunds" % credit_id,
                       headers=_hdr("owner"), json=body)


def _setup(credit_amount=-50):
    sku = _new_sku("Item_L07_%d" % abs(credit_amount))
    crid, cv = _save([_item(sku, 1, credit_amount)], doc_form="credit_note",
                     amount=credit_amount)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    return credit_store.find_by_source("default", "financial_credit", crid)["credit_id"]


def _bal(credit_id):
    s = db.get_session()
    try:
        sup = s.execute(text("SELECT supplier_id FROM credit_documents WHERE id=:c"),
                        {"c": credit_id}).fetchone()[0]
    finally:
        s.close()
    return credit_store.credit_balances("default", sup)["available_minor"]


def test_pending_refund_reserves_and_cancel_releases():
    """pending 占用可用额；取消释放（追加 reversal 不删历史）；重复取消幂等。"""
    credit_id = _setup()
    assert _bal(credit_id) == 5000
    r = _refund(credit_id, {"action": "request", "amount_minor": 2000,
                            "idempotency_key": "rf-1"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "awaiting_receipt"
    assert _bal(credit_id) == 3000  # pending 占用
    # 幂等：同 key 再请求 → 回放，不重复占用
    r1b = _refund(credit_id, {"action": "request", "amount_minor": 2000,
                              "idempotency_key": "rf-1"})
    assert r1b.status_code == 200 and r1b.json()["data"]["applied"] is False
    assert _bal(credit_id) == 3000
    refund_id = r.json()["data"]["refund_id"]
    # 无证据登记收到 → 422 REFUND_NOT_RECEIVED
    r2 = _refund(credit_id, {"action": "receive", "refund_id": refund_id})
    assert r2.status_code == 422 and r2.json()["code"] == "REFUND_NOT_RECEIVED"
    # 取消 → 释放
    r3 = _refund(credit_id, {"action": "cancel", "refund_id": refund_id})
    assert r3.status_code == 200 and r3.json()["data"]["state"] == "cancelled"
    assert _bal(credit_id) == 5000
    # 重复取消幂等
    r4 = _refund(credit_id, {"action": "cancel", "refund_id": refund_id})
    assert r4.status_code == 200 and r4.json()["data"]["applied"] is False
    assert _bal(credit_id) == 5000
    # pending 事实保留（不删历史）：refund_pending 2000 + reversal 2000 都在
    s = db.get_session()
    try:
        kinds = s.execute(text(
            "SELECT event_kind, amount_minor FROM credit_entries WHERE credit_id=:c"
            " ORDER BY id"), {"c": credit_id}).fetchall()
        assert ("refund_pending", 2000) in kinds and ("reversal", 2000) in kinds
    finally:
        s.close()


def test_received_keeps_occupied_and_insufficient_409():
    """登记收到（附证据）：占用转已收（可用不再含该额）；不足请求 409。"""
    credit_id = _setup()
    r = _refund(credit_id, {"action": "request", "amount_minor": 5000,
                            "idempotency_key": "rf-all"})
    assert r.status_code == 200
    assert _bal(credit_id) == 0
    # 超额退款请求 → 409
    r2 = _refund(credit_id, {"action": "request", "amount_minor": 1,
                             "idempotency_key": "rf-over"})
    assert r2.status_code == 409 and r2.json()["code"] == "CREDIT_INSUFFICIENT"
    refund_id = r.json()["data"]["refund_id"]
    r3 = _refund(credit_id, {"action": "receive", "refund_id": refund_id,
                             "evidence": "BANK-REF-20261026-001"})
    assert r3.status_code == 200 and r3.json()["data"]["state"] == "received"
    assert _bal(credit_id) == 0  # 已收不回到可用（钱已从供应商处收回，信用面额不变）
    # 重复 receive 幂等
    r4 = _refund(credit_id, {"action": "receive", "refund_id": refund_id,
                             "evidence": "BANK-REF-20261026-001"})
    assert r4.status_code == 200 and r4.json()["data"]["applied"] is False
