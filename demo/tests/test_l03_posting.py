# -*- coding: utf-8 -*-
"""L03 原子过账与月结/更正分流（C-LED：statement 零库存/重复应付款不增、
多 worker 恰一次、批次后故障整体回滚）。"""

import os
import sys
import tempfile
import threading

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_l03_posting_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_l03.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "l03-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()

_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk", "admin": "admin@demo.hk"}
client = TestClient(app)


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _new_sku(name="Item_A"):
    sku_id, _ = db.create_sku(name, base_unit="kg", tenant_id="default")
    return sku_id


def _item(sku_id, qty, amount):
    return {"name": "Item_A", "quantity": qty, "unit": "kg",
            "unit_price": round(amount / qty, 4), "amount": amount, "sku_id": sku_id}


def _save(items, doc_form="printed_delivery_note", **extra):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": doc_form, "settlement_type": "credit",
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
        return {"stock": sku.current_stock if sku else 0,
                "fifo": sum(b.remaining_qty for b in batches),
                "log_count": len(logs), "batch_count": len(batches)}
    finally:
        s.close()


def test_statement_zero_stock_and_no_duplicate_payable():
    """月结=对照凭证：审批成功但零实物过账（Q01_monthly 关闭）；重复审批零副作用。"""
    sku = _new_sku("Item_Monthly")
    rid, v = _save([_item(sku, 2, 14)], doc_form="monthly_statement")
    r = _approve(rid, v)
    assert r.status_code == 200, r.json()
    st = _stock(sku)
    assert st["stock"] == 0 and st["fifo"] == 0 and st["log_count"] == 0 and st["batch_count"] == 0, st
    # 重复审批：409，且应付款快照不增（无重复应付事实）
    from app import recon_store
    pay_before = recon_store.compute_payables("default")
    r2 = _approve(rid, v)
    assert r2.status_code == 409
    pay_after = recon_store.compute_payables("default")
    assert pay_before == pay_after  # 应付款快照零变化（无重复应付事实）
    assert _stock(sku)["batch_count"] == 0


def test_multiworker_approve_exactly_once():
    """并发审批恰一次：两个 worker 同时打同一单，恰一个 200；库存只入一次。"""
    sku = _new_sku("Item_Race")
    rid, v = _save([_item(sku, 2, 14)])
    results = []
    def _worker():
        c = TestClient(app)
        results.append(c.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"),
                              json={"version": v}).status_code)
    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count(200) == 1, results
    assert set(results) <= {200, 409}, results  # 另一 worker 409（CAS 裁决），无 5xx
    st = _stock(sku)
    assert st["stock"] == 2 and st["batch_count"] == 1 and st["log_count"] == 1, st
    # posting_headers 恰一条
    s = db.get_session()
    try:
        n = s.execute(text("SELECT COUNT(*) FROM posting_headers WHERE receipt_id=:r"),
                      {"r": rid}).scalar()
        assert n == 1, n
    finally:
        s.close()


def test_fault_after_batch_rollback(monkeypatch):
    """批次写入后故障：ambient 事务整体回滚，库存/批次/流水/状态零残留；可重试成功。"""
    sku1 = _new_sku("Item_FaultA")
    sku2 = _new_sku("Item_FaultB")
    rid, v = _save([_item(sku1, 2, 14), _item(sku2, 3, 21)])
    real = db.apply_stock_log
    calls = {"n": 0}
    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("L03 synthetic fault after first batch")
        return real(*args, **kwargs)
    monkeypatch.setattr(db, "apply_stock_log", flaky)
    c500 = TestClient(app, raise_server_exceptions=False)
    r = c500.post("/api/receipt/%d/approve" % rid, headers=_hdr("owner"),
                  json={"version": v})
    assert r.status_code == 500, r.status_code
    monkeypatch.undo()
    assert _stock(sku1)["stock"] == 0 and _stock(sku1)["batch_count"] == 0
    assert _stock(sku2)["stock"] == 0 and _stock(sku2)["log_count"] == 0
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.status == "edited"  # 状态未推进（整体回滚）
    # 修复后重试成功
    r2 = _approve(rid, v)
    assert r2.status_code == 200, r2.json()
    assert _stock(sku1)["stock"] == 2 and _stock(sku2)["stock"] == 3
