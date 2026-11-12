# -*- coding: utf-8 -*-
"""L06 owner 显式抵扣（C-CREDIT：部分/多目标、竞态不超额、跨商拒绝、幂等冲突、撤销反冲）。"""

import os
import sys
import tempfile
import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l06_alloc_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l06.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l06-test-secret-not-the-default-value")
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
    _rs._gate._ensure(None)  # recon 表惰性建表（Gate 独立连接路径）
    s = db.get_session()
    try:
        for tbl in ("credit_entries", "credit_documents", "credit_allocations",
                    "posting_headers", "payment_allocations"):
            s.execute(text("DELETE FROM %s" % tbl))
        s.execute(text("UPDATE receipts SET payment_id=NULL, paid_at=NULL"))
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


def _alloc(credit_id, targets, key, version=None, reversal_of=None):
    body = {"idempotency_key": key, "targets": targets}
    if version is not None:
        body["version"] = version
    if reversal_of is not None:
        body["reversal_of"] = reversal_of
    return client.post("/api/supplier-credits/%d/allocations" % credit_id,
                       headers=_hdr("owner"), json=body)


def _setup_purchase_and_credit(purchase_amount=70, credit_amount=-7):
    sku = _new_sku("Item_L06_%d" % purchase_amount)
    rid, v = _save([_item(sku, 10, purchase_amount)])
    assert _approve(rid, v).status_code == 200
    crid, cv = _save([_item(sku, 1, credit_amount)], doc_form="credit_note",
                     amount=credit_amount)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    doc = credit_store.find_by_source("default", "financial_credit", crid)
    return rid, doc["credit_id"]


def test_partial_multi_receipt_allocate():
    """一张信用部分抵两单（owner 显式选择）；应付随之净减；余额扣减。"""
    sku = _new_sku("Item_L06a")
    rid1, v1 = _save([_item(sku, 5, 35)])
    rid2, v2 = _save([_item(sku, 5, 35)])
    assert _approve(rid1, v1).status_code == 200
    assert _approve(rid2, v2).status_code == 200
    crid, cv = _save([_item(sku, 1, -7)], doc_form="credit_note", amount=-7)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    credit_id = credit_store.find_by_source("default", "financial_credit", crid)["credit_id"]
    r = _alloc(credit_id, [{"receipt_id": rid1, "amount_minor": 400},
                           {"receipt_id": rid2, "amount_minor": 300}], "key-multi-1")
    assert r.status_code == 200, r.json()
    data = r.json()["data"]
    assert data["applied"] is True and data["allocated_minor"] == 700
    assert data["available_minor"] == 0
    # 应付反映分配：35-4=31、35-3=32（净减，非静默——是 owner 的显式选择）
    pay = recon_store.compute_payables("default")
    by_id = {p["receipt_id"]: p for p in pay["payables"]}
    assert by_id[rid1]["outstanding_amount"] == 31
    assert by_id[rid2]["outstanding_amount"] == 32


def test_payment_credit_race_no_overallocate():
    """并发两笔抵扣同一信用：恰一笔成功，另一笔 409 CREDIT_INSUFFICIENT（含最新版本）。"""
    rid, credit_id = _setup_purchase_and_credit()
    results = []
    def _worker(key):
        c = TestClient(app)
        results.append(c.post("/api/supplier-credits/%d/allocations" % credit_id,
                              headers=_hdr("owner"),
                              json={"idempotency_key": key,
                                    "targets": [{"receipt_id": rid, "amount_minor": 500}]}).status_code)
    threads = [threading.Thread(target=_worker, args=("race-1",)),
               threading.Thread(target=_worker, args=("race-2",))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [200, 409], results
    bal = credit_store.credit_balances("default",
                                       recon_store.compute_payables("default")["payables"][0]["supplier_id"])
    assert bal["available_minor"] == 200  # 700-500，不超额


def test_cross_supplier_denied():
    """跨供应商抵扣 422 CREDIT_SUPPLIER_MISMATCH。"""
    rid, credit_id = _setup_purchase_and_credit()
    sku_b = _new_sku("Item_L06B")
    # Vendor_B 的已入账采购
    payload = {"source": "manual", "supplier_name": "Vendor_B", "date": "2026-10-26",
               "doc_form": "printed_delivery_note", "settlement_type": "credit",
               "currency": "HKD", "total_amount": 50,
               "items": [{"name": "Item_B1", "quantity": 5, "unit": "kg",
                          "unit_price": 10, "amount": 50}]}
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    rid_b = r.json()["receipt_id"]
    assert _approve(rid_b, r.json()["version"]).status_code == 200
    r = _alloc(credit_id, [{"receipt_id": rid_b, "amount_minor": 100}], "key-cross")
    assert r.status_code == 422 and r.json()["code"] == "CREDIT_SUPPLIER_MISMATCH"


def test_same_key_diff_body_conflict():
    """同幂等键异内容 → 409 IDEMPOTENCY_REUSED；同键同内容 → 幂等回放。"""
    rid, credit_id = _setup_purchase_and_credit()
    r1 = _alloc(credit_id, [{"receipt_id": rid, "amount_minor": 100}], "key-idem")
    assert r1.status_code == 200 and r1.json()["data"]["applied"] is True
    r2 = _alloc(credit_id, [{"receipt_id": rid, "amount_minor": 100}], "key-idem")
    assert r2.status_code == 200 and r2.json()["data"]["applied"] is False  # 回放
    r3 = _alloc(credit_id, [{"receipt_id": rid, "amount_minor": 200}], "key-idem")
    assert r3.status_code == 409 and r3.json()["code"] == "IDEMPOTENCY_REUSED"
    bal = credit_store.credit_balances("default",
                                       recon_store.compute_payables("default")["payables"][0]["supplier_id"])
    assert bal["available_minor"] == 600  # 只生效一次


def test_reverse_allocation_restores():
    """撤销抵扣：追加 reversal（不删历史）、可用余额与目标应付恢复；重复撤销幂等。"""
    rid, credit_id = _setup_purchase_and_credit()
    r1 = _alloc(credit_id, [{"receipt_id": rid, "amount_minor": 300}], "key-rev")
    assert r1.status_code == 200
    alloc_id = r1.json()["data"]["allocation_ids"][0]
    r2 = _alloc(credit_id, [], "key-rev2", reversal_of=alloc_id)
    assert r2.status_code == 200 and r2.json()["data"]["applied"] is True
    bal = credit_store.credit_balances("default",
                                       recon_store.compute_payables("default")["payables"][0]["supplier_id"])
    assert bal["available_minor"] == 700
    _pay = {p["receipt_id"]: p for p in
            recon_store.compute_payables("default")["payables"]}
    assert _pay[rid]["outstanding_amount"] == 70
    r3 = _alloc(credit_id, [], "key-rev3", reversal_of=alloc_id)
    assert r3.status_code == 200 and r3.json()["data"]["applied"] is False  # 幂等
