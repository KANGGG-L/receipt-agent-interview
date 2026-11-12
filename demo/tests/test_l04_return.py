# -*- coding: utf-8 -*-
"""L04 第二增量：physical_return（来源批次反向消耗）与退货冲销批次守恒。

C-LED：test_return_after_consumption_blocked / test_return_reverse_batch_conserve。
"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l04_return_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l04r.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l04r-test-secret-not-the-default-value")
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


def _save(items, doc_form="printed_delivery_note", amount=None):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": doc_form, "settlement_type": "credit",
               "currency": "HKD",
               "total_amount": sum(i["amount"] for i in items) if amount is None else amount,
               "items": items}
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def _approve(rid, version, action=None, return_batch_id=None):
    body = {"version": version}
    if action:
        body["business_action"] = action
    if return_batch_id:
        body["return_batch_id"] = return_batch_id
    return client.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"), json=body)


def _first_batch(rid):
    s = db.get_session()
    try:
        b = s.query(db._InventoryBatchRow).filter_by(receipt_id=int(rid)).first()
        return b.id
    finally:
        s.close()


def _batch_state(bid):
    s = db.get_session()
    try:
        b = s.get(db._InventoryBatchRow, int(bid))
        return {"remaining": b.remaining_qty, "closed": b.is_closed,
                "initial": b.initial_qty, "cost": b.unit_cost}
    finally:
        s.close()


def _sku_stock(sku_id):
    s = db.get_session()
    try:
        return s.get(db._SkuRow, int(sku_id)).current_stock
    finally:
        s.close()


def _credit(rid):
    s = db.get_session()
    try:
        return s.execute(text(
            "SELECT face_minor, source FROM credit_documents WHERE original_receipt_id=:r"),
            {"r": rid}).fetchall()
    finally:
        s.close()


def test_return_after_consumption_blocked():
    """来源批次可退量不足 → 409 RETURN_SOURCE_INSUFFICIENT，事务零残留。"""
    sku = _new_sku("Item_Ret1")
    rid, v = _save([_item(sku, 5, 35)])
    assert _approve(rid, v).status_code == 200
    bid = _first_batch(rid)
    # 消耗 3：批次剩 2
    db.apply_stock_log(sku_id=sku, name="Item_Ret1", qty=3, unit="kg", amount=21,
                       vendor="Vendor_A", date="2026-10-26", receipt_id=None,
                       kind="consume", tenant_id="default")
    assert _sku_stock(sku) == 2
    # 退货 4 > 剩余 2 → 409；库存/批次零残留；单据保持 edited 可再处理
    crid, cv = _save([_item(sku, 4, -28)], doc_form="credit_note", amount=-28)
    r = _approve(crid, cv, action="physical_return", return_batch_id=bid)
    assert r.status_code == 409, (r.status_code, r.json())
    assert r.json().get("code") == "RETURN_SOURCE_INSUFFICIENT", r.json()
    assert _sku_stock(sku) == 2 and _batch_state(bid)["remaining"] == 2
    row = db.get_receipt_row(crid, tenant_id="default")
    assert row.status == "edited" and _credit(crid) == []
    # 改退 2（恰好剩余）→ 成功：批次清零关批、库存 0、credit face=140
    crid2, cv2 = _save([_item(sku, 2, -14)], doc_form="credit_note", amount=-14)
    r2 = _approve(crid2, cv2, action="physical_return", return_batch_id=bid)
    assert r2.status_code == 200, r2.json()
    st = _batch_state(bid)
    assert st["remaining"] == 0 and st["closed"] == 1
    assert _sku_stock(sku) == 0
    assert _credit(crid2) == [(1400, "physical_return")]  # 14 港元 = 1400 分
    # 负成本批次不得出现（原批次成本保持）
    assert st["cost"] == 7.0


def test_return_reverse_batch_conserve():
    """退货冲销守恒：reverse 恢复来源批次与库存；幂等不二次恢复。"""
    sku = _new_sku("Item_Ret2")
    rid, v = _save([_item(sku, 5, 35)])
    assert _approve(rid, v).status_code == 200
    bid = _first_batch(rid)
    crid, cv = _save([_item(sku, 2, -14)], doc_form="credit_note", amount=-14)
    assert _approve(crid, cv, action="physical_return", return_batch_id=bid).status_code == 200
    assert _batch_state(bid)["remaining"] == 3 and _sku_stock(sku) == 3
    # reverse 退货单：批次恢复到 5、库存 5、重新开批（需当前 version）
    _row = db.get_receipt_row(crid, tenant_id="default")
    r = client.post("/api/receipt/%d/reverse" % crid, headers=_hdr("owner"),
                    json={"reason": "退货录错数量", "version": _row.version})
    assert r.status_code == 200, r.json()
    st = _batch_state(bid)
    assert st["remaining"] == 5 and st["closed"] == 0, st
    assert _sku_stock(sku) == 5
    # 幂等：再次 reverse 不再恢复
    r2 = client.post("/api/receipt/%d/reverse" % crid, headers=_hdr("owner"),
                     json={"reason": "重复请求", "version": _row.version})
    assert r2.status_code == 200 and r2.json().get("already_reversed") is True
    assert _batch_state(bid)["remaining"] == 5 and _sku_stock(sku) == 5
    # 反转流水恰一（追加事实，不删历史：原 return 流水仍在）
    s = db.get_session()
    try:
        n_rev = s.query(db._StockLogRow).filter_by(receipt_id=int(crid),
                                                   kind="reversal").count()
        n_ret = s.query(db._StockLogRow).filter_by(receipt_id=int(crid),
                                                   kind="return").count()
        assert n_rev == 1 and n_ret == 1
    finally:
        s.close()
