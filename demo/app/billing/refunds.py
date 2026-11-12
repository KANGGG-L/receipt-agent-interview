# -*- coding: utf-8 -*-
"""S08 平台退费请求：policy fail-closed、累计不超已付、幂等、unknown 先查。

D06：refund.policy 未签发 → 禁止任何退款动作（POLICY_NOT_CONFIGURED）。
状态：requested → processing → succeeded / failed；requested 可 cancelled。
pending（requested/processing）不产生最终权益；只有 refund_succeeded 财务事实才修正权益。
"""

import hashlib
import json
import uuid

from sqlalchemy import text

from app import db
from app.billing import lifecycle, reconcile
from app.billing.errors import (
    BillingConflict, BillingDenied, BillingNotFound, BillingError, ProviderUnknown,
)
from app.billing.policy import PolicyNotConfigured, load_policy

OWNER_CAP = "billing:billing_owner"
OPERATOR_CAP = "billing:billing_operator"


def _session():
    return db.get_session()


def _rowdict(row):
    return {k: row._mapping[k] for k in row._mapping.keys()} if row is not None else None


def _require_owner(caps):
    if OWNER_CAP not in (caps or set()):
        raise BillingDenied(
            "BILLING_CAPABILITY_REQUIRED",
            "权限不足：提交退款需要「计费负责人」权限。餐厅老板/店员权限不会自动包含计费权限。")


def _require_operator(caps):
    if not (OPERATOR_CAP in (caps or set()) or OWNER_CAP in (caps or set())):
        raise BillingDenied(
            "BILLING_CAPABILITY_REQUIRED",
            "权限不足：处理退款需要「计费操作员」或「计费负责人」权限。")


def load_refund_policy():
    """退款规则 fail-closed：字段缺失或值为空一律拒绝（不猜比例/例外/审批人）。"""
    policy, ref = load_policy()
    value = ((policy.get("refund") or {}).get("policy"))
    if value is None or (isinstance(value, str) and not value.strip()) or value == []:
        raise PolicyNotConfigured(["refund.policy"])
    return policy, ref


def _request_hash(order_no, amount_minor):
    raw = json.dumps({"order_no": str(order_no), "amount_minor": int(amount_minor)},
                     sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_refund(tenant_id, refund_id):
    s = _session()
    try:
        row = s.execute(text(
            "SELECT * FROM refund_requests WHERE id=:i AND tenant_id=:t"),
            {"i": int(refund_id), "t": tenant_id}).fetchone()
        return _rowdict(row)
    finally:
        s.close()


def request_refund(tenant_id, order_no, amount_minor, request_key, requested_by,
                   caps, reason=""):
    """客户 billing_owner 提交退款请求；累计 reservation 不得超过已付。"""
    _require_owner(caps)
    _policy, ref = load_refund_policy()
    tenant_id = str(tenant_id or "").strip()
    key = str(request_key or "").strip()
    if not tenant_id or not key:
        raise BillingError("REFUND_INVALID", "退款申请需要商户与操作编号，请刷新页面后重试。")
    try:
        amount_minor = int(amount_minor)
    except (TypeError, ValueError):
        raise BillingError("REFUND_INVALID", "退款金额填写有误，请重新填写。")
    if amount_minor <= 0:
        raise BillingError("REFUND_INVALID", "退款金额必须大于 0。")
    rh = _request_hash(order_no, amount_minor)

    s = _session()
    try:
        order = s.execute(text(
            "SELECT * FROM billing_orders WHERE order_no=:o AND tenant_id=:t"),
            {"o": str(order_no), "t": tenant_id}).fetchone()
        if not order:
            raise BillingNotFound("ORDER_NOT_FOUND", "找不到要退款的订单，请刷新页面后再试。")
        order = _rowdict(order)
        if order.get("status") not in ("paid", "refunded"):
            raise BillingConflict("ORDER_NOT_PAID", "这笔订单还没确认收款，不能退款。")

        prior = s.execute(text(
            "SELECT * FROM refund_requests WHERE tenant_id=:t AND request_key=:k"),
            {"t": tenant_id, "k": key}).fetchone()
        if prior:
            prior = _rowdict(prior)
            if prior.get("request_hash") and prior["request_hash"] != rh:
                raise BillingConflict(
                    "IDEMPOTENCY_REUSED", "这个操作编号已经用于另一个退款申请，请刷新后重试。")
            return {"status": "replayed", "refund_id": prior["id"],
                    "state": prior.get("state"), "amount_minor": prior.get("amount_minor")}

        paid = int(order["amount_minor"])
        reserved = s.execute(text(
            "SELECT COALESCE(SUM(amount_minor),0) FROM refund_requests"
            " WHERE order_id=:i AND state IN ('requested','processing','succeeded')"),
            {"i": order["id"]}).scalar() or 0
        if int(reserved) + amount_minor > paid:
            raise BillingConflict(
                "REFUND_EXCEEDS_PAID",
                "这单累计退款金额会超过已收金额，不能提交。请核对已退金额后重新填写。")
        now = lifecycle.to_iso(lifecycle.utcnow())
        s.execute(text(
            "INSERT INTO refund_requests (order_id, payment_attempt_id, tenant_id, state,"
            " amount_minor, requested_by, request_key, request_hash, reason, provider,"
            " created_at, updated_at)"
            " VALUES (:o,NULL,:t,'requested',:a,:by,:k,:h,:r,'fake',:now,:now)"),
            {"o": order["id"], "t": tenant_id, "a": amount_minor,
             "by": str(requested_by or ""), "k": key, "h": rh,
             "r": str(reason or ""), "now": now})
        refund_id = int(s.execute(text(
            "SELECT id FROM refund_requests WHERE tenant_id=:t AND request_key=:k"),
            {"t": tenant_id, "k": key}).scalar())
        s.commit()
        return {"status": "requested", "refund_id": refund_id, "state": "requested",
                "amount_minor": amount_minor, "policy_ref": ref}
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def approve_refund(tenant_id, refund_id, caps, actor="", adapter=None):
    """operator 审批并提交渠道。unknown 先查询；绝不发起第二次退款请求。"""
    _require_operator(caps)
    load_refund_policy()
    refund = get_refund(tenant_id, refund_id)
    if not refund:
        raise BillingNotFound("REFUND_NOT_FOUND", "找不到这笔退款申请，请刷新页面后再试。")
    state = refund.get("state")
    if state == "succeeded":
        return {"status": "succeeded", "refund_id": refund["id"],
                "provider_refund_id": refund.get("provider_refund_id")}
    if state in ("failed", "cancelled"):
        raise BillingConflict("REFUND_NOT_ACTIONABLE",
                              "这笔退款已经结束，不能再处理。请刷新页面查看最新状态。")

    order = _order_of(refund["order_id"])
    if not order:
        raise BillingNotFound("ORDER_NOT_FOUND", "退款对应的订单不存在。")
    adapter = adapter or _adapter_for(order.get("provider") or "fake")

    if state == "requested":
        # 先把「已发起」事实落库（provider_request_key），再调用渠道；重试只查询。
        key = "refund-%s" % refund["id"]
        now = lifecycle.to_iso(lifecycle.utcnow())
        s = _session()
        try:
            updated = s.execute(text(
                "UPDATE refund_requests SET state='processing', provider_request_key=:k,"
                " approved_by=:a, updated_at=:now WHERE id=:i AND state='requested'"),
                {"k": key, "a": str(actor or ""), "now": now,
                 "i": refund["id"]}).rowcount
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
        if updated != 1:
            return approve_refund(tenant_id, refund_id, caps, actor, adapter)
        refund = get_refund(tenant_id, refund_id)
        try:
            result = adapter.refund(refund.get("payment_provider_payment_id")
                                    or _payment_id(order["id"]),
                                    int(refund["amount_minor"]))
        except (ProviderUnknown, TimeoutError) as exc:
            _set_error(refund["id"], getattr(exc, "code", "PROVIDER_RESULT_UNKNOWN"))
            return {"status": "processing", "refund_id": refund["id"],
                    "code": "PROVIDER_RESULT_UNKNOWN",
                    "msg": "退款渠道结果暂未确认，系统会先查询，不会重复提交退款。"}
        return _finish_success(tenant_id, refund, order, result, actor)
    # processing：只查询，不再调用 refund。
    query = getattr(adapter, "query_refund", None)
    try:
        result = query(refund.get("provider_request_key")) if callable(query) else \
            {"status": "unknown"}
    except Exception:
        result = {"status": "unknown"}
    result = dict(result or {})
    if result.get("status") == "succeeded" and result.get("provider_refund_id"):
        return _finish_success(tenant_id, refund, order, result, actor)
    return {"status": "processing", "refund_id": refund["id"],
            "code": "PROVIDER_RESULT_UNKNOWN",
            "msg": "退款还在渠道处理中，系统只会查询结果，不会重复提交退款。"}


def _payment_id(order_id):
    s = _session()
    try:
        row = s.execute(text(
            "SELECT provider_payment_id FROM payment_attempts WHERE order_id=:i"
            " AND status='succeeded' ORDER BY id DESC LIMIT 1"), {"i": int(order_id)}).fetchone()
        return (row[0] if row else None) or "order-%s" % order_id
    finally:
        s.close()


def _order_of(order_id):
    s = _session()
    try:
        return _rowdict(s.execute(text("SELECT * FROM billing_orders WHERE id=:i"),
                                  {"i": int(order_id)}).fetchone())
    finally:
        s.close()


def _adapter_for(provider):
    from app.billing.providers.fake import get_adapter
    try:
        return get_adapter(provider)
    except NotImplementedError:
        raise BillingDenied("PROVIDER_NOT_AVAILABLE", "这个支付渠道还没接入，暂时不能在线退款。")


def _finish_success(tenant_id, refund, order, result, actor):
    provider_refund_id = str(result.get("provider_refund_id")
                             or "refund-%s" % uuid.uuid4().hex[:16])
    fact = {
        "tenant_id": tenant_id, "order_no": order["order_no"],
        "provider_payment_id": provider_refund_id, "fact_kind": reconcile.FACT_REFUND,
        "object_version": 1, "amount_minor": int(refund["amount_minor"]),
        "currency": order.get("currency"),
        "event_id": "refund-evt-%s" % refund["id"],
        "payload_digest": hashlib.sha256(provider_refund_id.encode()).hexdigest(),
    }
    reconcile.apply_fact(fact)
    now = lifecycle.to_iso(lifecycle.utcnow())
    s = _session()
    try:
        s.execute(text(
            "UPDATE refund_requests SET state='succeeded', provider_refund_id=:pr,"
            " processed_at=:now, updated_at=:now, error_code=NULL WHERE id=:i"),
            {"pr": provider_refund_id, "now": now, "i": refund["id"]})
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return {"status": "succeeded", "refund_id": refund["id"],
            "provider_refund_id": provider_refund_id,
            "order_status": reconcile.reduce_state(order["order_no"], tenant_id)}


def _set_error(refund_id, code):
    s = _session()
    try:
        s.execute(text(
            "UPDATE refund_requests SET error_code=:c, updated_at=:now WHERE id=:i"),
            {"c": str(code or "")[:64],
             "now": lifecycle.to_iso(lifecycle.utcnow()), "i": int(refund_id)})
        s.commit()
    finally:
        s.close()


def cancel_refund(tenant_id, refund_id, caps):
    """requested 可取消；processing 不支持撤销（渠道未知时拒绝假装取消）。"""
    _require_owner(caps)
    refund = get_refund(tenant_id, refund_id)
    if not refund:
        raise BillingNotFound("REFUND_NOT_FOUND", "找不到这笔退款申请。")
    if refund["state"] == "requested":
        s = _session()
        try:
            s.execute(text(
                "UPDATE refund_requests SET state='cancelled', updated_at=:now"
                " WHERE id=:i AND state='requested'"),
                {"now": lifecycle.to_iso(lifecycle.utcnow()), "i": refund["id"]})
            s.commit()
        finally:
            s.close()
        return {"status": "cancelled", "refund_id": refund["id"]}
    if refund["state"] == "processing":
        raise BillingConflict(
            "REFUND_IN_PROGRESS",
            "退款已经提交渠道，无法直接撤销。请等渠道结果出来后再处理。")
    return {"status": refund["state"], "refund_id": refund["id"]}


def refundable_summary(tenant_id, order_no):
    """对账/客户视图：已付与已占用退款额度（金额只在私有证据/内部使用）。"""
    s = _session()
    try:
        order = s.execute(text(
            "SELECT * FROM billing_orders WHERE order_no=:o AND tenant_id=:t"),
            {"o": order_no, "t": tenant_id}).fetchone()
        if not order:
            return None
        order = _rowdict(order)
        rows = [_rowdict(r) for r in s.execute(text(
            "SELECT state, amount_minor FROM refund_requests WHERE order_id=:i"),
            {"i": order["id"]}).fetchall()]
        reserved = sum(int(r["amount_minor"]) for r in rows
                       if r["state"] in ("requested", "processing", "succeeded"))
        succeeded = sum(int(r["amount_minor"]) for r in rows if r["state"] == "succeeded")
        return {"order_no": order_no, "paid_minor": int(order["amount_minor"]),
                "reserved_minor": reserved, "refunded_minor": succeeded,
                "currency": order["currency"]}
    finally:
        s.close()
