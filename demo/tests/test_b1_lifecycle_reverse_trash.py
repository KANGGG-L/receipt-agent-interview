# -*- coding: utf-8 -*-
"""B1 回归：冲销（S1-P1-11）与回收站（S1-P1-12）。"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

client = TestClient(app)
STAFF = {"X-Role": "staff"}
OWNER = {"X-Role": "owner"}
OTHER_OWNER = {"X-Role": "owner", "X-Tenant-Id": "tenant_b"}


def _approved_receipt(items=None, tenant="default"):
    hdr = {"X-Role": "staff", "X-Tenant-Id": tenant}
    items = items or [
        {"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 10.0, "amount": 100.0},
        {"name": "白菜", "quantity": 5, "unit": "斤", "unit_price": 10.0, "amount": 50.0},
    ]
    total = sum(i["amount"] for i in items)
    r = client.post("/api/save_edited", headers=hdr, json={
        "supplier_name": "冲销供应商", "date": "2026-10-01", "total_amount": total,
        "settlement_type": "credit", "doc_form": "printed", "items": items})
    assert r.status_code == 200, r.text
    rid, ver = r.json()["receipt_id"], r.json()["version"]
    ohdr = {"X-Role": "owner", "X-Tenant-Id": tenant}
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=ohdr)
    assert ap.status_code == 200, ap.text
    return rid, ap.json()["version"]


def _stock():
    s = db.get_session()
    try:
        return {x.name: x.current_stock for x in s.query(db._SkuRow).all()}
    finally:
        s.close()


def _logs(rid):
    s = db.get_session()
    try:
        return [(l.kind, l.qty, l.amount, l.note) for l in
                s.query(db._StockLogRow).filter_by(receipt_id=rid).order_by(db._StockLogRow.id)]
    finally:
        s.close()


def _batches(rid):
    s = db.get_session()
    try:
        return [(b.initial_qty, b.remaining_qty, b.is_closed) for b in
                s.query(db._InventoryBatchRow).filter_by(receipt_id=rid)]
    finally:
        s.close()


# ---- 冲销 -------------------------------------------------------------
def test_reverse_restores_stock_writes_linked_logs_and_audit():
    rid, ver = _approved_receipt()
    assert _stock() == {"菜心": 10.0, "白菜": 5.0}
    r = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "数量录错"},
                    headers=OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["already_reversed"] is False and r.json()["reversed_lines"] == 2
    assert _stock() == {"菜心": 0.0, "白菜": 0.0}
    logs = _logs(rid)
    assert [l[0] for l in logs] == ["in", "in", "reversal", "reversal"]
    assert [l[1] for l in logs[2:]] == [-10.0, -5.0]
    assert all(f"冲销单据 #{rid}" in l[3] for l in logs[2:])
    assert all(b[1] == 0.0 and b[2] == 1 for b in _batches(rid))
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.status == "reversed" and row.version == ver + 1
    assert row.reversed_by == "owner@demo.hk" and row.reverse_reason == "数量录错"
    audit = json.loads(row.audit_logs_json)
    assert any(e["action"] == "reverse_receipt" and e["details"]["reason"] == "数量录错"
               for e in audit)


def test_reverse_is_idempotent_and_does_not_double_deduct():
    rid, ver = _approved_receipt()
    first = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "录错"},
                        headers=OWNER)
    again = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "录错"},
                        headers=OWNER)
    assert first.status_code == 200 and again.status_code == 200
    assert again.json()["already_reversed"] is True
    assert _stock() == {"菜心": 0.0, "白菜": 0.0}
    assert len([l for l in _logs(rid) if l[0] == "reversal"]) == 2


def test_reverse_requires_owner_reason_and_approved_status():
    rid, ver = _approved_receipt()
    assert client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "x"},
                       headers=STAFF).status_code == 403
    no_reason = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": " "},
                            headers=OWNER)
    assert no_reason.status_code == 422 and no_reason.json()["code"] == "REASON_REQUIRED"
    stale = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver - 1, "reason": "x"},
                        headers=OWNER)
    assert stale.status_code == 409 and stale.json()["code"] == "VERSION_CONFLICT"
    draft = client.post("/api/save_edited", headers=STAFF, json={
        "supplier_name": "草稿供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": "cash", "items": [
            {"name": "x", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}]}).json()
    nr = client.post(f"/api/receipt/{draft['receipt_id']}/reverse",
                     json={"version": draft["version"], "reason": "x"}, headers=OWNER)
    assert nr.status_code == 409 and nr.json()["code"] == "NOT_APPROVED"


def test_reverse_blocked_when_stock_already_consumed():
    rid, ver = _approved_receipt()
    s = db.get_session()
    try:
        sku = s.query(db._SkuRow).filter_by(name="菜心").first()
        sku_id = sku.id
    finally:
        s.close()
    c = client.post(f"/api/inventory/{sku_id}/consume", headers=OWNER,
                    json={"quantity": 4, "notes": "出品"})
    assert c.status_code == 200, c.text
    before_stock = _stock()
    r = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "录错"},
                    headers=OWNER)
    assert r.status_code == 409 and r.json()["code"] == "REVERSAL_STOCK_CONSUMED"
    assert "菜心" in r.json()["msg"]
    assert _stock() == before_stock
    assert db.get_receipt_row(rid, tenant_id="default").status == "approved"


def test_reversed_receipt_cannot_be_saved_or_approved_and_price_history_skips_it():
    rid, ver = _approved_receipt()
    s = db.get_session()
    try:
        sku_id = s.query(db._SkuRow).filter_by(name="菜心").first().id
    finally:
        s.close()
    assert len(db.price_history(sku_id, tenant_id="default")) == 1
    client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "录错"},
                headers=OWNER)
    assert db.price_history(sku_id, tenant_id="default") == []
    ver2 = db.get_receipt_row(rid, tenant_id="default").version
    sv = client.post("/api/save_edited", headers=STAFF, json={
        "receipt_id": rid, "version": ver2, "supplier_name": "冲销供应商", "date": "2026-10-01",
        "total_amount": 150.0, "settlement_type": "credit", "items": []})
    assert sv.status_code == 409 and sv.json()["code"] == "CANNOT_MODIFY_REVERSED"
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": ver2}, headers=OWNER)
    assert ap.status_code == 409


def test_reverse_is_tenant_scoped():
    rid, ver = _approved_receipt()
    r = client.post(f"/api/receipt/{rid}/reverse", json={"version": ver, "reason": "x"},
                    headers=OTHER_OWNER)
    assert r.status_code == 404
    assert db.get_receipt_row(rid, tenant_id="default").status == "approved"


def test_stale_text_pointing_to_missing_reverse_feature_is_gone():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "app", "api_receipts.py"), encoding="utf-8") as f:
        src = f.read()
    assert "请使用冲销功能" not in src and "请使用冲销" not in src
    assert "可在管理后台恢复" not in src


# ---- 回收站 -----------------------------------------------------------
def _draft(tenant="default", status_flag=False):
    hdr = {"X-Role": "staff", "X-Tenant-Id": tenant}
    r = client.post("/api/save_edited", headers=hdr, json={
        "supplier_name": "回收站供应商", "date": "2026-10-01", "total_amount": 10.0,
        "settlement_type": "cash",
        "items": [{"name": "x", "quantity": 1, "unit": "个", "unit_price": 10, "amount": 10}]})
    rid = r.json()["receipt_id"]
    if status_flag:
        client.post(f"/api/receipt/{rid}/flag", headers={"X-Role": "owner", "X-Tenant-Id": tenant})
    return rid


def test_trash_list_restore_purge_owner_only_and_tenant_scoped():
    mine = _draft()
    theirs = _draft(tenant="tenant_b")
    assert client.post(f"/api/receipt/{mine}/discard", headers=STAFF).status_code == 200
    assert client.post(f"/api/receipt/{theirs}/discard",
                       headers={"X-Role": "staff", "X-Tenant-Id": "tenant_b"}).status_code == 200
    assert client.get("/api/trash/receipts", headers=STAFF).status_code == 403
    lst = client.get("/api/trash/receipts", headers=OWNER).json()
    ids = [r["id"] for r in lst["data"]]
    assert mine in ids and theirs not in ids
    row = [r for r in lst["data"] if r["id"] == mine][0]
    assert row["deleted_by"] == "staff@demo.hk" and row["deleted_at"]
    assert client.post(f"/api/trash/receipts/{theirs}/restore", headers=OWNER).status_code == 404
    assert client.delete(f"/api/trash/receipts/{theirs}", headers=OWNER).status_code == 404
    rs = client.post(f"/api/trash/receipts/{mine}/restore", headers=OWNER)
    assert rs.status_code == 200
    assert db.get_receipt_row(mine, tenant_id="default").deleted_at is None
    assert client.post(f"/api/trash/receipts/{mine}/restore", headers=OWNER).status_code == 404
    client.post(f"/api/receipt/{mine}/discard", headers=STAFF)
    pg = client.delete(f"/api/trash/receipts/{mine}", headers=OWNER)
    assert pg.status_code == 200
    assert db.get_receipt_row(mine, tenant_id="default") is None
    # 别的租户的垃圾桶数据没被动到
    assert db.get_receipt_row(theirs, tenant_id="tenant_b").deleted_at


def test_purge_refuses_approved_receipts():
    rid, _ver = _approved_receipt()
    db.soft_delete_receipt(rid)
    r = client.delete(f"/api/trash/receipts/{rid}", headers=OWNER)
    assert r.status_code == 409 and r.json()["code"] == "CANNOT_PURGE_APPROVED"
    assert db.get_receipt_row(rid, tenant_id="default") is not None


def test_discard_flagged_is_owner_only():
    rid = _draft(status_flag=True)
    r = client.post(f"/api/receipt/{rid}/discard", headers=STAFF)
    assert r.status_code == 403 and r.json()["code"] == "FLAGGED_OWNER_ONLY"
    assert db.get_receipt_row(rid, tenant_id="default").deleted_at is None
    assert client.post(f"/api/receipt/{rid}/discard", headers=OWNER).status_code == 200


def test_old_admin_trash_endpoints_removed():
    assert client.get("/api/admin/trash", headers={"X-Role": "admin"}).status_code == 404


# ---- S1-P1-13 实验结题 ------------------------------------------------
def _experiment_with_samples(n=1, min_sample=0):
    return db.create_experiment("B1 实验", target_percent=50, min_sample=min_sample)["id"]


def test_experiment_conclude_is_admin_only_and_concluded_by_from_identity():
    exp_id = _experiment_with_samples()
    body = {"conclusion": "inconclusive", "conclusion_reason": "回归", "concluded_by": "伪造人"}
    url = f"/api/admin/experiments/{exp_id}/conclude"
    assert client.post(url, json=body, headers=OWNER).status_code == 403
    assert client.post(url, json=body, headers=OTHER_OWNER).status_code == 403
    assert client.post(url, json=body, headers=STAFF).status_code == 403
    r = client.post(url, json=body, headers={"X-Role": "admin"})
    assert r.status_code == 200, r.text
    s = db.get_session()
    try:
        row = s.get(db._ExperimentRow, exp_id)
        assert row.concluded_by == "admin@demo.hk" and row.status == "concluded"
    finally:
        s.close()
    again = client.post(url, json=body, headers={"X-Role": "admin"})
    assert again.status_code == 409 and again.json()["code"] == "EXPERIMENT_CONCLUDED"
