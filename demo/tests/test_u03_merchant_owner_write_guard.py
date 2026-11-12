# -*- coding: utf-8 -*-
"""U03 退款/抵扣写端点：商户 owner 显式判定（D-02，不走 rank 继承）。

admin（平台角色）/staff 一律 403 且不改账；owner 正常写入。
I01 v2 开启时还要求 tenant_memberships 显式 owner membership。
"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_u03_owner_guard_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_u03.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "u03-test-secret-not-the-default-value")
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
_EMAILS = {"owner": "boss@demo.hk", "staff": "staff@demo.hk", "admin": "admin@demo.hk"}


@pytest.fixture(autouse=True)
def _clean_tables():
    recon_store._gate._ensure(None)
    s = db.get_session()
    try:
        for tbl in ("credit_entries", "credit_documents", "credit_allocations",
                    "posting_headers", "payment_allocations", "supplier_refunds"):
            s.execute(text("DELETE FROM %s" % tbl))
        s.execute(text("UPDATE receipts SET payment_id=NULL, paid_at=NULL"))
        s.commit()
    finally:
        s.close()
    yield


def _hdr(role, tenant="default"):
    return {"Authorization": "Bearer " + auth.issue_token(_EMAILS[role], role, tenant)}


def _save(items, doc_form="printed_delivery_note", amount=None):
    payload = {"source": "manual", "supplier_name": "Vendor_U03", "date": "2026-10-26",
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


def _setup(credit_amount=-70, purchase_amount=70):
    sku_id, _ = db.create_sku("Item_U03_%d" % abs(purchase_amount), base_unit="kg",
                              tenant_id="default")
    item = {"name": "Item_U03", "quantity": 1, "unit": "kg",
            "unit_price": purchase_amount, "amount": purchase_amount, "sku_id": sku_id}
    rid, v = _save([item])
    assert _approve(rid, v).status_code == 200
    crid, cv = _save([dict(item, unit_price=credit_amount, amount=credit_amount)],
                     doc_form="credit_note", amount=credit_amount)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    doc = credit_store.find_by_source("default", "financial_credit", crid)
    s = db.get_session()
    try:
        supplier_id = s.execute(text("SELECT supplier_id FROM credit_documents WHERE id=:c"),
                                {"c": doc["credit_id"]}).fetchone()[0]
    finally:
        s.close()
    return rid, doc["credit_id"], supplier_id


def _refund(headers, credit_id, body):
    return client.post("/api/supplier-credits/%d/refunds" % credit_id,
                       headers=headers, json=body)


def _alloc(headers, credit_id, targets, key):
    return client.post("/api/supplier-credits/%d/allocations" % credit_id,
                       headers=headers, json={"idempotency_key": key, "targets": targets})


def _ledger_snapshot():
    s = db.get_session()
    try:
        entries = s.execute(text("SELECT COUNT(*) FROM credit_entries")).fetchone()[0]
        allocs = s.execute(text("SELECT COUNT(*) FROM credit_allocations")).fetchone()[0]
        refunds = s.execute(text("SELECT COUNT(*) FROM supplier_refunds")).fetchone()[0]
        return entries, allocs, refunds
    finally:
        s.close()


@pytest.mark.parametrize("role", ["admin", "staff"])
def test_admin_staff_forbidden_on_refund_and_allocation_without_ledger_write(role):
    rid, credit_id, supplier_id = _setup()
    before_bal = credit_store.credit_balances("default", supplier_id)["available_minor"]
    before_ledger = _ledger_snapshot()

    r = _refund(_hdr(role), credit_id,
                {"action": "request", "amount_minor": 1000, "idempotency_key": "rf-" + role})
    assert r.status_code == 403, r.json()
    r2 = _alloc(_hdr(role), credit_id, [{"receipt_id": rid, "amount_minor": 1000}],
                "alloc-" + role)
    assert r2.status_code == 403, r2.json()

    assert credit_store.credit_balances("default", supplier_id)["available_minor"] == before_bal
    assert _ledger_snapshot() == before_ledger


def test_owner_still_can_request_refund_and_allocate():
    rid, credit_id, supplier_id = _setup()
    r = _refund(_hdr("owner"), credit_id,
                {"action": "request", "amount_minor": 1000, "idempotency_key": "rf-owner"})
    assert r.status_code == 200, r.json()
    assert r.json()["data"]["state"] == "awaiting_receipt"
    assert credit_store.credit_balances("default", supplier_id)["available_minor"] == 6000

    r2 = _alloc(_hdr("owner"), credit_id, [{"receipt_id": rid, "amount_minor": 2000}],
                "alloc-owner")
    assert r2.status_code == 200, r2.json()
    assert r2.json()["data"]["applied"] is True


def test_v2_owner_token_needs_explicit_membership(monkeypatch):
    """I01 口径：平台 admin 不继承商户角色；owner 令牌也须显式 membership。"""
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    # owner 令牌但无显式 membership → 403（不继承、不改账）
    r = _refund(_hdr("owner"), 999999,
                {"action": "request", "amount_minor": 100, "idempotency_key": "rf-v2-denied"})
    assert r.status_code == 403, r.json()
    # 未授权 admin 令牌 → 403
    r2 = _refund(_hdr("admin"), 999999,
                 {"action": "request", "amount_minor": 100, "idempotency_key": "rf-v2-admin"})
    assert r2.status_code == 403, r2.json()
    assert _ledger_snapshot() == (0, 0, 0)
