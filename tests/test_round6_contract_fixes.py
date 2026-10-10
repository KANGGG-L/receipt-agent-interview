# -*- coding: utf-8 -*-
"""Round 6 开发回归：付款三态语义与提醒确认持久审计（AC-01-05 / AC-05-05）。

判据设计原则（见项目纪律「新口径必须有自校准用例」）：
- 每条断言都对准「修复前会失败」的行为，而不是恒真；
- 付款三态覆盖 detected / not_detected / uncertain，且 unknown 不折算成 not_detected；
- 提醒确认覆盖「非阻断可确认 + 落持久审计」「阻断项不可确认」「已入账不可确认」三面。
"""

import json

import pytest
from starlette.requests import Request

from app import db
from app.api_receipts import approve_receipt, acknowledge_warning, ApproveBody, AcknowledgeWarningBody
from app.services.contract import payment_mark_state, payment_mark_tristate
from app.services.receipt_utils import build_detail


def _mock_request(role="owner", tenant="test_tenant", email="test@example.com"):
    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"host", b"localhost"), (b"x-role", role.encode()), (b"x-tenant-id", tenant.encode())],
        "state": {
            "account": {"email": email, "role": role, "tenant_id": tenant},
            "tenant_id": tenant,
            "session_id": "test_sess_r6",
        },
    }
    return Request(scope)


# ---------------------------------------------------------------
# AC-01-05：付款标记三态
# ---------------------------------------------------------------

def test_payment_mark_state_three_way_no_collapse():
    """三态互相独立：空/缺失=uncertain，绝不与「明确未检测到」混同。"""
    assert payment_mark_state("已付款") == "detected"
    assert payment_mark_state("stamp") == "detected"
    assert payment_mark_state("未付款") == "not_detected"
    assert payment_mark_state("") == "uncertain"
    assert payment_mark_state(None) == "uncertain"
    # 反证：uncertain 与 not_detected 必须不同（修复前二者都落 "" → 会失败）
    assert payment_mark_state("") != payment_mark_state("未付款")


def test_payment_mark_tristate_no_image_is_uncertain():
    """无原图 → 不确定（""），不得凭空折算成「未付款」。"""
    assert payment_mark_tristate("", llm_marked=False) == ""
    assert payment_mark_tristate("/nonexistent/nope.jpg", llm_marked=False) == ""
    # LLM 明确标记 → detected
    assert payment_mark_tristate("", llm_marked=True) == "已付款"


def test_build_detail_exposes_payment_mark_state_uncertain():
    """build_detail 必须下发给外三态标签；无原图时是 uncertain。"""
    tenant = "tenant_r6_pay_unknown"
    rid = db.create_receipt(supplier_name="Vendor_A", status="parsed", tenant_id=tenant)
    row = db.get_receipt_row(rid, tenant_id=tenant)
    d = build_detail(row)
    assert d.get("payment_mark_state") == "uncertain"


def test_build_detail_payment_mark_state_detected_and_not_detected():
    tenant = "tenant_r6_pay_states"
    rid_paid = db.create_receipt(supplier_name="Vendor_P", status="parsed", tenant_id=tenant)
    db.update_receipt(rid_paid, payment_mark="已付款", version=1)
    assert build_detail(db.get_receipt_row(rid_paid, tenant_id=tenant))["payment_mark_state"] == "detected"

    rid_unpaid = db.create_receipt(supplier_name="Vendor_U", status="parsed", tenant_id=tenant)
    db.update_receipt(rid_unpaid, payment_mark="未付款", version=1)
    assert build_detail(db.get_receipt_row(rid_unpaid, tenant_id=tenant))["payment_mark_state"] == "not_detected"


# ---------------------------------------------------------------
# AC-05-05：提醒确认持久审计
# ---------------------------------------------------------------

def _seed_clean_receipt(tenant, rid=None):
    rid = rid or db.create_receipt(supplier_name="Vendor_A", status="parsed_with_warnings", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid, [{
        "name": "新鲜菜心", "quantity": 10.0, "unit": "kg",
        "unit_price": 10.0, "amount": 100.0, "is_void": 0,
    }], tenant_id=tenant)
    return rid


def test_acknowledge_warning_persists_durable_audit():
    tenant = "tenant_r6_ack"
    req = _mock_request(role="staff", tenant=tenant)
    rid = _seed_clean_receipt(tenant)

    body = AcknowledgeWarningBody(warning_id="w_math_soft", rule="算术提醒（非阻断）", version=1)
    res = acknowledge_warning(rid, body, req)
    assert isinstance(res, dict) and res.get("status") == "success", f"ack failed: {res}"
    ack = res["acknowledgement"]
    assert ack["rule"] == "算术提醒（非阻断）"
    assert ack["acknowledged_by"]
    assert ack["acknowledged_at"]

    # 持久审计必须落库且可读回（build_detail 暴露 audit_logs）
    row = db.get_receipt_row(rid, tenant_id=tenant)
    logs = json.loads(row.audit_logs_json or "[]")
    ack_entries = [e for e in logs if e.get("action") == "acknowledge_warning"]
    assert len(ack_entries) == 1, f"expected 1 durable ack entry, got {logs}"
    entry = ack_entries[0]
    assert entry["field"] == "warning"
    assert entry["old"] == "w_math_soft"
    assert entry["new"] == "acknowledged"
    assert entry["details"]["rule"] == "算术提醒（非阻断）"
    assert entry["details"]["acknowledged_by"]
    assert entry["ts"]


def test_acknowledge_warning_rejects_blocker_missing_supplier():
    """阻断项（缺供应商）不能靠「提醒确认」绕过。"""
    tenant = "tenant_r6_ack_block"
    req = _mock_request(role="staff", tenant=tenant)
    rid = db.create_receipt(supplier_name="", status="parsed_with_warnings", tenant_id=tenant)
    db.update_receipt(rid, total_amount=100.0, receipt_date="2026-10-08", version=1)
    db.set_receipt_items(rid, [{
        "name": "x", "quantity": 1.0, "unit": "kg", "unit_price": 100.0, "amount": 100.0, "is_void": 0,
    }], tenant_id=tenant)

    res = acknowledge_warning(rid, AcknowledgeWarningBody(warning_id="w", version=1), req)
    assert getattr(res, "status_code", None) == 422
    row = db.get_receipt_row(rid, tenant_id=tenant)
    assert not any(e.get("action") == "acknowledge_warning"
                   for e in json.loads(row.audit_logs_json or "[]"))


def test_acknowledge_warning_rejected_after_approved():
    tenant = "tenant_r6_ack_approved"
    req = _mock_request(role="owner", tenant=tenant)
    rid = _seed_clean_receipt(tenant, rid=db.create_receipt(
        supplier_name="Vendor_A", status="edited", tenant_id=tenant))
    res_appr = approve_receipt(rid, ApproveBody(version=1), req)
    assert isinstance(res_appr, dict) and res_appr.get("status") == "success"

    res = acknowledge_warning(rid, AcknowledgeWarningBody(warning_id="w", version=2), req)
    assert getattr(res, "status_code", None) == 409


def test_acknowledge_warning_staff_forbidden_for_anonymous_tenant_isolation():
    """跨租户确认必须 404（不得读到别租户单据）。"""
    tenant_a = "tenant_r6_ack_a"
    tenant_b = "tenant_r6_ack_b"
    rid_a = _seed_clean_receipt(tenant_a)
    req_b = _mock_request(role="staff", tenant=tenant_b)
    res = acknowledge_warning(rid_a, AcknowledgeWarningBody(warning_id="w", version=1), req_b)
    assert getattr(res, "status_code", None) == 404
