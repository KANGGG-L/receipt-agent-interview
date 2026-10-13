# -*- coding: utf-8 -*-
"""B1 回归：单据状态机、数据可信度与审计顺序。

覆盖 S1-P0-01（折扣/运费/押金/抹零端到端）、S1-P1-03（flagged 不可被店员保存解除）、
S1-P1-04（迟到 Job 不覆盖人工终态）、S1-P1-05（source=auto 不对外开放）、
S1-P2-03（校验先于审计）、S3-05（结算方式与二次确认契约）、S1-P1-15（入账原子性）。
全程隔离临时库（demo/conftest.py 自动隔离），不调用任何真实模型。
"""

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


def _payload(**over):
    body = {
        "supplier_name": "测试供应商", "date": "2026-10-01", "total_amount": 90.0,
        "settlement_type": "cash", "doc_form": "printed", "currency": "HKD",
        "discount_amount": 10.0,
        "items": [{"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 10.0,
                   "amount": 100.0}],
    }
    body.update(over)
    return body


def _save(body, headers=STAFF):
    return client.post("/api/save_edited", json=body, headers=headers)


def _detail(rid):
    return client.get(f"/api/receipt/{rid}", headers=OWNER).json()["data"]


def _seed_parsed(status="parsed", total=100.0):
    rid = db.create_receipt(supplier_name="识别供应商", status=status, tenant_id="default")
    db.update_receipt(rid, receipt_date="2026-10-01", total_amount=total,
                      settlement_type="cash", doc_form="printed", version=1)
    db.set_receipt_items(rid, [{"name": "白菜", "quantity": 10.0, "unit": "斤",
                                "unit_price": 10.0, "amount": 100.0}], tenant_id="default")
    return rid


# ---- S1-P0-01 ----------------------------------------------------------
def test_discount_delivery_deposit_rounding_save_approve_readback():
    body = _payload(total_amount=100 - 10 + 5 + 20 - 0.4, discount_amount=10.0,
                    delivery_fee=5.0, deposit_amount=20.0, rounding_adjustment=0.4)
    r = _save(body)
    assert r.status_code == 200, r.text
    assert r.json()["math_blockers"] == []
    rid, ver = r.json()["receipt_id"], r.json()["version"]
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    assert ap.status_code == 200, ap.text
    d = _detail(rid)
    assert d["status"] == "approved"
    assert (d["discount_amount"], d["delivery_fee"], d["deposit_amount"],
            d["rounding_adjustment"]) == (10.0, 5.0, 20.0, 0.4)
    assert d["total_amount"] == pytest.approx(114.6)


def test_save_gate_and_approve_gate_agree_on_mismatch():
    bad = _payload(total_amount=95.0)  # 100 - 10 = 90，不是 95
    r = _save(bad)
    assert r.status_code == 200
    blockers = r.json()["math_blockers"]
    assert blockers, "保存时门禁应给出阻断项"
    rid, ver = r.json()["receipt_id"], r.json()["version"]
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    assert ap.status_code == 422 and ap.json()["code"] == "APPROVAL_BLOCKED"
    assert ap.json()["blockers"] == blockers


# ---- S3-05 -------------------------------------------------------------
def test_settlement_required_on_create_and_no_orphan_receipt():
    before = len(db.list_receipt_rows(tenant_id="default"))
    r = _save(_payload(settlement_type=None))
    assert r.status_code == 422 and r.json()["code"] == "SETTLEMENT_REQUIRED"
    assert len(db.list_receipt_rows(tenant_id="default")) == before


def test_settlement_required_on_update_and_audit_not_written():
    rid = _save(_payload()).json()["receipt_id"]
    row = db.get_receipt_row(rid, tenant_id="default")
    n_audit = len(json.loads(row.audit_logs_json or "[]"))
    r = _save(_payload(receipt_id=rid, version=row.version, settlement_type=""))
    assert r.status_code == 422 and r.json()["code"] == "SETTLEMENT_REQUIRED"
    row2 = db.get_receipt_row(rid, tenant_id="default")
    assert len(json.loads(row2.audit_logs_json or "[]")) == n_audit
    assert row2.version == row.version


def test_detail_does_not_default_settlement_to_credit():
    rid = db.create_receipt(supplier_name="x", status="edited", tenant_id="default")
    assert _detail(rid)["settlement_type"] == ""


def test_confirm_mismatch_contract():
    bad = _payload(total_amount=95.0)
    r = _save({**bad, "confirm_mismatch": False})
    assert r.status_code == 422 and r.json()["code"] == "MATH_MISMATCH_CONFIRM"
    assert r.json()["data"]["warnings"]
    ok = _save({**bad, "confirm_mismatch": True})
    assert ok.status_code == 200 and ok.json()["math_blockers"]


# ---- S1-P1-03 ----------------------------------------------------------
def test_flag_bumps_version_audits_and_staff_cannot_save_flagged():
    rid = _save(_payload(total_amount=90.0)).json()["receipt_id"]
    ver = db.get_receipt_row(rid, tenant_id="default").version
    f = client.post(f"/api/receipt/{rid}/flag", headers=OWNER)
    assert f.status_code == 200 and f.json()["version"] == ver + 1
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.status == "flagged" and row.version == ver + 1
    assert any(e["action"] == "flag_receipt" for e in json.loads(row.audit_logs_json))
    s = _save(_payload(receipt_id=rid, version=row.version))
    assert s.status_code == 409 and s.json()["code"] == "RECEIPT_FLAGGED"
    assert db.get_receipt_row(rid, tenant_id="default").status == "flagged"
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": row.version}, headers=OWNER)
    assert ap.status_code == 409


def test_unflag_restores_status_bumps_version_and_audits():
    rid = _save(_payload(total_amount=90.0)).json()["receipt_id"]
    client.post(f"/api/receipt/{rid}/flag", headers=OWNER)
    row = db.get_receipt_row(rid, tenant_id="default")
    assert client.post(f"/api/receipt/{rid}/unflag", headers=STAFF).status_code == 403
    u = client.post(f"/api/receipt/{rid}/unflag", headers=OWNER)
    assert u.status_code == 200 and u.json()["status_after"] == "edited"
    row2 = db.get_receipt_row(rid, tenant_id="default")
    assert row2.status == "edited" and row2.version == row.version + 1
    assert any(e["action"] == "unflag_receipt" for e in json.loads(row2.audit_logs_json))


# ---- S1-P1-04 ----------------------------------------------------------
@pytest.mark.parametrize("status", ["flagged", "edited", "approved", "reversed"])
def test_late_job_does_not_overwrite_human_final_status(status):
    from app.models import DocForm, ReceiptData, ReceiptItem
    from app.services import receipt_utils
    rid = _seed_parsed(status=status)
    data = ReceiptData(doc_form=DocForm.PRINTED, vendor="迟到供应商", date="2026-10-02",
                       items=[ReceiptItem(name="迟到品", qty=1, unit="个", unit_price=1, amount=1)],
                       total=1, payment_marked=False, confidence=0.9)
    receipt_utils.save_parsed_data(rid, data, {})
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.status == status
    assert row.supplier_name == "识别供应商"


def test_late_job_failure_does_not_overwrite_flagged():
    from app.services import receipt_utils
    rid = _seed_parsed(status="flagged")
    receipt_utils._mark_job_failed(rid)
    assert db.get_receipt_row(rid, tenant_id="default").status == "flagged"
    rid2 = _seed_parsed(status="parsing")
    receipt_utils._mark_job_failed(rid2)
    assert db.get_receipt_row(rid2, tenant_id="default").status == "error"


# ---- S1-P1-05 ----------------------------------------------------------
def test_external_source_auto_cannot_create_parsed_receipt():
    r = _save(_payload(source="auto"))
    assert r.status_code == 200
    rid = r.json()["receipt_id"]
    row = db.get_receipt_row(rid, tenant_id="default")
    assert row.status == "edited"
    logs = json.loads(row.audit_logs_json)
    assert not any(e["action"] == "auto_save" for e in logs)


def test_source_auto_on_existing_parsed_row_keeps_parsed_unreviewed():
    rid = _seed_parsed(status="parsed")
    r = _save(_payload(receipt_id=rid, version=1, source="auto", settlement_type=None,
                       discount_amount=0.0, total_amount=100.0))
    assert r.status_code == 200, r.text
    assert db.get_receipt_row(rid, tenant_id="default").status == "parsed"
    # 不是 parsed 的单据（例如已 edited），source=auto 一律按人工保存处理
    rid2 = _save(_payload()).json()["receipt_id"]
    row2 = db.get_receipt_row(rid2, tenant_id="default")
    r2 = _save(_payload(receipt_id=rid2, version=row2.version, source="auto"))
    assert r2.status_code == 200
    assert db.get_receipt_row(rid2, tenant_id="default").status == "edited"


def test_approve_parsed_receipt_reports_needs_review():
    rid = _seed_parsed(status="parsed")
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": 1}, headers=OWNER)
    assert ap.status_code == 200, ap.text
    assert ap.json()["needs_review"] is True and ap.json()["review_notice"]
    logs = json.loads(db.get_receipt_row(rid, tenant_id="default").audit_logs_json)
    assert any(e["action"] == "approve_receipt" and "未经店员复核" in str(e.get("details"))
               for e in logs)
    rid2 = _save(_payload(total_amount=90.0)).json()["receipt_id"]
    v2 = db.get_receipt_row(rid2, tenant_id="default").version
    ap2 = client.post(f"/api/receipt/{rid2}/approve", json={"version": v2}, headers=OWNER)
    assert ap2.json()["needs_review"] is False


# ---- S1-P1-15 ----------------------------------------------------------
def _state_snapshot(rid):
    s = db.get_session()
    try:
        return {
            "logs": s.query(db._StockLogRow).count(),
            "batches": s.query(db._InventoryBatchRow).count(),
            "skus": s.query(db._SkuRow).count(),
            "stock": sorted((x.name, x.current_stock) for x in s.query(db._SkuRow).all()),
            "suppliers": s.query(db._SupplierRow).count(),
            "status": s.get(db._ReceiptRow, rid).status,
            "version": s.get(db._ReceiptRow, rid).version,
        }
    finally:
        s.close()


def _two_line_receipt():
    body = _payload(total_amount=150.0, discount_amount=0.0, items=[
        {"name": "菜心", "quantity": 10, "unit": "斤", "unit_price": 10.0, "amount": 100.0},
        {"name": "白菜", "quantity": 5, "unit": "斤", "unit_price": 10.0, "amount": 50.0},
    ])
    r = _save(body)
    return r.json()["receipt_id"], r.json()["version"]


def test_approve_is_all_or_nothing_under_fault_injection(monkeypatch):
    rid, ver = _two_line_receipt()
    before = _state_snapshot(rid)
    real = db.apply_stock_log
    calls = {"n": 0}

    def boom(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("模拟入账中途崩溃")
        return real(*a, **kw)

    monkeypatch.setattr(db, "apply_stock_log", boom)
    with pytest.raises(RuntimeError):
        client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    assert _state_snapshot(rid) == before, "失败必须整体回滚：库存、批次、新建 SKU、状态都不变"
    monkeypatch.setattr(db, "apply_stock_log", real)
    ap = client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    assert ap.status_code == 200, ap.text
    after = _state_snapshot(rid)
    assert after["logs"] == 2 and after["status"] == "approved"
    assert dict(after["stock"]) == {"菜心": 10.0, "白菜": 5.0}


def test_approve_after_simulated_crash_does_not_double_count(monkeypatch):
    rid, ver = _two_line_receipt()
    real_update = db.update_receipt

    def crash_on_status(receipt_id, **fields):
        if fields.get("status") == "approved":
            raise KeyboardInterrupt("进程在提交前被杀")
        return real_update(receipt_id, **fields)

    monkeypatch.setattr(db, "update_receipt", crash_on_status)
    with pytest.raises(KeyboardInterrupt):
        client.post(f"/api/receipt/{rid}/approve", json={"version": ver}, headers=OWNER)
    monkeypatch.setattr(db, "update_receipt", real_update)
    assert client.post(f"/api/receipt/{rid}/approve", json={"version": ver},
                       headers=OWNER).status_code == 200
    snap = _state_snapshot(rid)
    assert snap["logs"] == 2 and dict(snap["stock"]) == {"菜心": 10.0, "白菜": 5.0}


def test_concurrent_approve_single_winner_single_ledger():
    import concurrent.futures as cf
    rid, ver = _two_line_receipt()

    def go(_):
        return client.post(f"/api/receipt/{rid}/approve", json={"version": ver},
                           headers=OWNER).status_code

    with cf.ThreadPoolExecutor(8) as ex:
        codes = list(ex.map(go, range(8)))
    assert codes.count(200) == 1 and codes.count(409) == 7
    assert _state_snapshot(rid)["logs"] == 2
