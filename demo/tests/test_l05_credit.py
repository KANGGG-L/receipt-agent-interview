# -*- coding: utf-8 -*-
"""L05 信用台账（C-CREDIT：未分配贷项不假付款、纯贷项零库存、发行幂等）。"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l05_credit_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l05.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l05-test-secret-not-the-default-value")
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
def _clean_credit_tables():
    s = db.get_session()
    try:
        for tbl in ("credit_entries", "credit_documents", "credit_allocations",
                    "posting_headers"):
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


def test_unallocated_credit_preserved_not_negative_payment():
    """G05/Q05 关闭：未分配贷项保持未分配——不标 paid、无负 paid_amount、应付保持 gross；
    信用余额独立可查（face/available）。"""
    sku = _new_sku("Item_L05")
    rid, v = _save([_item(sku, 10, 70)])
    assert _approve(rid, v).status_code == 200
    crid, cv = _save([_item(sku, 1, -7)], doc_form="credit_note", amount=-7)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    pay = recon_store.compute_payables("default")
    by_id = {p["receipt_id"]: p for p in pay["payables"]}
    purchase = by_id[rid]
    assert purchase["outstanding_amount"] == 70 and purchase["payment_status"] == "unpaid"
    credit = by_id[crid]
    assert credit["payment_status"] == "credit_unallocated"
    assert credit["paid_amount"] == 0 and credit["outstanding_amount"] == 0
    bucket = pay["by_supplier"][list(pay["by_supplier"].keys())[0]]
    assert bucket["unpaid_credit_total"] == 70  # gross 应付，不被贷项静默冲抵
    # 信用台账：face 700、可用 700（未分配）
    sup_id = purchase["supplier_id"]
    bal = credit_store.credit_balances("default", sup_id)
    assert bal["face_total_minor"] == 700 and bal["available_minor"] == 700
    # owner 查询端点
    r = client.get("/api/supplier-credits/%d" % sup_id, headers=_hdr("owner"))
    assert r.status_code == 200 and r.json()["data"]["available_minor"] == 700
    # staff 不可见（财务域）
    assert client.get("/api/supplier-credits/%d" % sup_id,
                      headers=_hdr("staff")).status_code == 403


def test_issue_idempotent_per_source():
    """同来源 posting 唯一发行：审批幂等（409）+ 台账层按 (source, receipt) 查重。"""
    sku = _new_sku("Item_L05b")
    crid, cv = _save([_item(sku, 1, -7)], doc_form="credit_note", amount=-7)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    assert _approve(crid, cv, action="financial_credit").status_code == 409
    found = credit_store.find_by_source("default", "financial_credit", crid)
    assert found is not None and found["face_minor"] == 700
    s = db.get_session()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM credit_documents"
                           " WHERE original_receipt_id=:r"), {"r": crid}).scalar()
        assert n == 1
    finally:
        s.close()
