# -*- coding: utf-8 -*-
"""L04 第一增量：贷项动作确认门（Q01_credit 三变体关闭）与纯金额贷项事实落账。"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l04_credit_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l04.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l04-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()
client = TestClient(app)
_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk", "admin": "admin@demo.hk"}


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _new_sku(name="Item_A"):
    sku_id, _ = db.create_sku(name, base_unit="kg", tenant_id="default")
    return sku_id


def _item(sku_id, qty, amount):
    return {"name": "Item_A", "quantity": qty, "unit": "kg",
            "unit_price": round(amount / qty, 4), "amount": amount, "sku_id": sku_id}


def _save(items, doc_form="printed_delivery_note", amount=None, **extra):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": doc_form, "settlement_type": "credit",
               "currency": "HKD",
               "total_amount": sum(i["amount"] for i in items) if amount is None else amount,
               "items": items}
    payload.update(extra)
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def _approve(rid, version, action=None):
    body = {"version": version}
    if action:
        body["business_action"] = action
    return client.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"), json=body)


def _stock(sku_id):
    s = db.get_session()
    try:
        sku = s.get(db._SkuRow, int(sku_id))
        batches = s.query(db._InventoryBatchRow).filter_by(sku_id=int(sku_id)).all()
        return {"stock": sku.current_stock, "fifo": sum(b.remaining_qty for b in batches),
                "batch_count": len(batches),
                "neg_cost": any(b.unit_cost < 0 for b in batches)}
    finally:
        s.close()


def _credit_rows(rid):
    s = db.get_session()
    try:
        docs = s.execute(text(
            "SELECT id, face_minor, state FROM credit_documents WHERE original_receipt_id=:r"),
            {"r": rid}).fetchall()
        entries = s.execute(text(
            "SELECT event_kind, amount_minor FROM credit_entries WHERE credit_id IN"
            " (SELECT id FROM credit_documents WHERE original_receipt_id=:r)"),
            {"r": rid}).fetchall()
        return docs, entries
    finally:
        s.close()


def test_credit_sign_variants_no_positive_inbound():
    """Q01_credit 三变体关闭：未确认动作的贷项单一律 422 拒批（不猜实物方向）；
    owner 显式 financial_credit 后零实物移动、贷方余额入账、无负成本批次。"""
    for variant, qty, actual in (("pos_no_actual", 1, None), ("pos_actual", 1, 1),
                                 ("neg_qty", -1, -1)):
        sku = _new_sku("Item_Credit_%s" % variant)
        rid, v = _save([_item(sku, 10, 70)])
        assert _approve(rid, v).status_code == 200
        before = _stock(sku)
        assert before["stock"] == 10
        credit_items = [_item(sku, qty, -7)]
        if actual is not None:
            credit_items[0]["actual_qty"] = actual
        crid, cv = _save(credit_items, doc_form="credit_note", amount=-7)
        r = _approve(crid, cv)
        assert r.status_code == 422, (variant, r.status_code)  # 未确认动作 → 拒批
        assert _stock(sku) == before, variant  # 库存零变化（保图草稿不变）
        # owner 显式确认纯金额贷项 → 入账；库存仍零变化，贷方余额事实落账
        r2 = _approve(crid, cv, action="financial_credit")
        assert r2.status_code == 200, (variant, r2.json())
        after = _stock(sku)
        assert after["stock"] == 10 and after["batch_count"] == before["batch_count"]
        assert not after["neg_cost"], variant  # 无负成本批次（正入库反例关闭）
        docs, entries = _credit_rows(crid)
        assert len(docs) == 1 and docs[0][1] == 700 and docs[0][2] == "active", variant
        assert len(entries) == 1 and entries[0] == ("issued", 700), variant


def test_credit_duplicate_no_double_issue():
    """贷项审批幂等：重复审批 409，credit_documents/entries 不重复发行。"""
    sku = _new_sku("Item_Credit_Idem")
    rid, v = _save([_item(sku, 5, 35)])
    assert _approve(rid, v).status_code == 200
    crid, cv = _save([_item(sku, 1, -7)], doc_form="credit_note", amount=-7)
    assert _approve(crid, cv, action="financial_credit").status_code == 200
    docs, entries = _credit_rows(crid)
    assert _approve(crid, cv, action="financial_credit").status_code == 409
    docs2, entries2 = _credit_rows(crid)
    assert (len(docs), len(entries)) == (len(docs2), len(entries2)) == (1, 1)
