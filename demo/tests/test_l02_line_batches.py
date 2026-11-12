# -*- coding: utf-8 -*-
"""L02 稳定来源行与多行 FIFO 批次（C-LED：Q02 反例关闭 / 重排 identity / 重试幂等）。

红线：全程沙箱 DB；Vendor_A 别名；无 Emoji；AUTH_ENABLED=1 + v2 关闭（RBAC 走旧 rank，
本文件专测过账语义而非身份）。
"""

import os
import sys
import tempfile

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l02_line_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l02.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l02-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402
from app.services.costing_service import CostingService  # noqa: E402

db._make_engine()
client = TestClient(app)


_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk", "admin": "admin@demo.hk"}


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _new_sku(name="Item_A"):
    sku_id, _ = db.create_sku(name, base_unit="kg", tenant_id="default")
    return sku_id


def _save(items, **extra):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": "printed_delivery_note", "settlement_type": "credit",
               "currency": "HKD", "total_amount": sum(i["amount"] for i in items),
               "items": items}
    payload.update(extra)
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def _approve(rid, version):
    return client.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"),
                       json={"version": version})


def _stock(sku_id):
    s = db.get_session()
    try:
        sku = s.get(db._SkuRow, int(sku_id))
        batches = s.query(db._InventoryBatchRow).filter_by(sku_id=int(sku_id)).all()
        logs = s.query(db._StockLogRow).filter_by(sku_id=int(sku_id)).all()
        return {"stock": sku.current_stock,
                "fifo": sum(b.remaining_qty for b in batches),
                "log_qty": sum(l.qty for l in logs),
                "batch_count": len(batches),
                "log_count": len(logs),
                "batch_line_uuids": sorted(b.line_uuid or "" for b in batches)}
    finally:
        s.close()


def _item(sku_id, qty, amount):
    return {"name": "Item_A", "quantity": qty, "unit": "kg",
            "unit_price": round(amount / qty, 4), "amount": amount, "sku_id": sku_id}


def _items(rid):
    s = db.get_session()
    try:
        rows = s.query(db._ItemRow).filter_by(receipt_id=int(rid)).all()
        return [{"line_uuid": r.line_uuid, "name": r.name, "quantity": r.quantity,
                 "unit_price": r.unit_price, "amount": r.amount, "unit": r.unit,
                 "sku_id": r.sku_id} for r in rows]
    finally:
        s.close()


def test_same_sku_two_lines_stock_log_fifo_conserve():
    """Q02 反例关闭：同单同 SKU 两行 → 两个独立批次，数量/金额/流水守恒。"""
    sku = _new_sku()
    rid, v = _save([_item(sku, 2, 14), _item(sku, 3, 21)])
    r = _approve(rid, v)
    assert r.status_code == 200, r.json()
    st = _stock(sku)
    assert st["stock"] == st["fifo"] == st["log_qty"] == 5, st
    assert st["batch_count"] == 2, st  # 旧行为：被 (receipt,sku) 幂等吞成 1 批
    assert st["log_count"] == 2, st
    assert len(set(st["batch_line_uuids"])) == 2  # 两行两 uuid


def test_line_reorder_identity():
    """重排不变身份：内容未变行跨保存保持 line_uuid（不按数组位置猜）。"""
    sku = _new_sku("Item_Reorder")
    items = [{"name": "Item_A1", "quantity": 1, "unit": "kg", "unit_price": 7,
              "amount": 7, "sku_id": sku},
             {"name": "Item_B1", "quantity": 2, "unit": "kg", "unit_price": 9,
              "amount": 18, "sku_id": None}]
    rid, v = _save(items)
    first = {i["name"]: i["line_uuid"] for i in _items(rid)}
    assert all(first.values()) and len(set(first.values())) == 2
    # 整体重排 + 保存（带当前 version）
    row = db.get_receipt_row(rid, tenant_id="default")
    reordered = [_items(rid)[1], _items(rid)[0]]
    r = client.post("/api/save_edited", headers=_hdr("staff"), json={
        "receipt_id": rid, "version": row.version, "source": "manual",
        "supplier_name": "Vendor_A", "date": "2026-10-26",
        "doc_form": "printed_delivery_note", "settlement_type": "credit",
        "currency": "HKD", "total_amount": 25, "items": reordered})
    assert r.status_code == 200, r.json()
    second = {i["name"]: i["line_uuid"] for i in _items(rid)}
    assert second == first, (first, second)  # 身份随内容走，不随位置


def test_retry_batch_no_duplicate_and_legacy_fallback():
    sku = _new_sku("Item_Idem")
    rid, v = _save([_item(sku, 2, 14), _item(sku, 3, 21)])
    assert _approve(rid, v).status_code == 200
    st1 = _stock(sku)
    r = _approve(rid, v)  # 重复审批
    assert r.status_code == 409
    assert _stock(sku) == st1  # 幂等：批次/流水零新增
    # record_inbound_batch 直接幂等：同 (receipt,line) 重放返回同批次。
    # 每次调用用独立短会话并 commit，避免测试会话未提交锁库影响 API 线程。
    def _replay(line_uuid, rid_target):
        s = db.get_session()
        try:
            b = CostingService.record_inbound_batch(
                session=s, sku_id=sku, qty=1, unit_price=7, unit="kg",
                receipt_id=rid_target, line_uuid=line_uuid)
            s.commit()
            return b.id
        finally:
            s.close()

    assert _replay("reuse-me", rid) == _replay("reuse-me", rid)
    assert _replay("reuse-me", rid) != _replay("other-line", rid)
    # legacy 回退：无 line_uuid 保持 (receipt,sku) 幂等（盘点/手动入库路径不变）
    rid2, _ = _save([_item(sku, 1, 7)])
    def _legacy(rid_target):
        s = db.get_session()
        try:
            b = CostingService.record_inbound_batch(
                session=s, sku_id=sku, qty=1, unit_price=7, unit="kg", receipt_id=rid_target)
            s.commit()
            return b.id
        finally:
            s.close()
    assert _legacy(rid2) == _legacy(rid2)
