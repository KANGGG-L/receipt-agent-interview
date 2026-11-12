# -*- coding: utf-8 -*-
"""S05/S06 支付事实归约与对账：事实优先、append-only、乱序/late success/退款安全。

归约优先级（终态从高到低）：refund_succeeded > payment_succeeded >
payment_failed > pending。后到的低优先级事实只入账不改终态；退款后的 late
payment_succeeded 仍记录财务事实，但绝不二次授期/复活权益。

对账只读：缺失/unknown 事件定时 query，绝不创建第二次扣款；每条 attempt 只对账一次。
"""

import hashlib
import json

from sqlalchemy import text

from app import db
from app.billing import lifecycle
from app.billing.errors import BillingConflict, BillingNotFound

FACT_REFUND = "refund_succeeded"
FACT_SUCCESS = "payment_succeeded"
FACT_FAILED = "payment_failed"


def _session():
    return db.get_session()


def _rowdict(row):
    return {k: row._mapping[k] for k in row._mapping.keys()} if row is not None else None


def _event_id(fact):
    explicit = str(fact.get("event_id") or "").strip()
    if explicit:
        return explicit
    raw = "|".join(str(fact.get(k) or "") for k in (
        "order_no", "provider_payment_id", "fact_kind", "object_version"))
    return "evt-" + hashlib.sha256(raw.encode()).hexdigest()[:24]


def apply_fact(fact):
    """写入一条 verified 财务事实并归约订单/订阅状态（同一事实只入账一次）。

    返回 {"duplicate": bool, "order_status": str, "requires_policy_review": bool}。
    """
    tenant_id = str(fact.get("tenant_id") or "").strip()
    order_no = str(fact.get("order_no") or "").strip()
    fact_kind = str(fact.get("fact_kind") or "").strip()
    if not tenant_id or not order_no or not fact_kind:
        raise BillingConflict("FACT_INVALID", "支付事实缺少商户、订单或类型，已拒绝入账。")
    object_version = int(fact.get("object_version") or 1)
    provider_fact_id = str(fact.get("provider_payment_id") or "").strip()
    if not provider_fact_id:
        raise BillingConflict("FACT_INVALID", "支付事实缺少渠道流水号，已拒绝入账。")
    amount = fact.get("amount_minor")
    currency = str(fact.get("currency") or "").upper()
    now = fact.get("now") or lifecycle.utcnow()

    s = _session()
    try:
        order = s.execute(text(
            "SELECT * FROM billing_orders WHERE order_no=:o AND tenant_id=:t"),
            {"o": order_no, "t": tenant_id}).fetchone()
        if not order:
            raise BillingNotFound("ORDER_NOT_FOUND", "找不到对应的订单，支付事实已拒绝入账。")
        order = _rowdict(order)
        # 同一事实的重放必须保持幂等：已存在时不参与累计校验，否则会把自身重复计入。
        already_exists = s.execute(text(
            "SELECT id FROM billing_events WHERE provider_payment_id=:pp"
            " AND fact_kind=:fk AND object_version=:v"),
            {"pp": provider_fact_id, "fk": fact_kind,
             "v": object_version}).fetchone() is not None
        if fact_kind == FACT_SUCCESS:
            if amount is not None and int(amount) != int(order["amount_minor"]):
                raise BillingConflict(
                    "PAYMENT_AMOUNT_MISMATCH",
                    "渠道回调金额与订单金额不一致，已拒绝入账，请联系平台核对。")
            if currency and currency != str(order["currency"]).upper():
                raise BillingConflict(
                    "PAYMENT_CURRENCY_MISMATCH",
                    "渠道回调币种与订单币种不一致，已拒绝入账，请联系平台核对。")
        elif fact_kind == FACT_REFUND and amount is not None and not already_exists:
            amount_int = int(amount)
            if amount_int <= 0:
                raise BillingConflict(
                    "REFUND_AMOUNT_INVALID", "退款金额必须大于 0，已拒绝入账。")
            # 事实层独立兜底累计退款：退款申请层的额度校验不能替代渠道回调/重放路径。
            prior_refunded = int(s.execute(text(
                "SELECT COALESCE(SUM(amount_minor),0) FROM billing_events"
                " WHERE order_id=:i AND fact_kind=:fk"),
                {"i": order["id"], "fk": FACT_REFUND}).scalar() or 0)
            if prior_refunded + amount_int > int(order["amount_minor"]):
                raise BillingConflict(
                    "REFUND_EXCEEDS_PAID",
                    "累计退款金额超过该笔订单已付金额，已拒绝入账，请联系平台核对。")

        # 审计载荷只保存人工登记的业务凭据（发票号/操作人/来源），绝不落卡号、密钥、
        # 请求 hash 或渠道原始报文。
        audit = {}
        if fact.get("invoice_no"):
            audit["invoice_no"] = str(fact.get("invoice_no"))
        if fact.get("actor"):
            audit["actor"] = str(fact.get("actor"))
        if fact.get("source"):
            audit["source"] = str(fact.get("source"))
        inserted = s.execute(text(
            "INSERT OR IGNORE INTO billing_events (tenant_id, order_id, provider_payment_id,"
            " fact_kind, object_version, payload_digest, amount_minor, currency, event_id,"
            " payload_json, created_at)"
            " VALUES (:t,:o,:pp,:fk,:v,:d,:a,:c,:e,:pj,:now)"),
            {"t": tenant_id, "o": order["id"], "pp": provider_fact_id, "fk": fact_kind,
             "v": object_version, "d": str(fact.get("payload_digest") or ""),
             "a": int(amount) if amount is not None else None,
             "c": currency or None, "e": _event_id(fact),
             "pj": json.dumps(audit, ensure_ascii=False) if audit else "",
             "now": lifecycle.to_iso(now)}).rowcount == 1

        result = {"duplicate": not inserted, "order_no": order_no,
                  "requires_policy_review": False}
        # 语义重复（不同 event id / 重放）不再归约，避免二次授期。
        if inserted:
            if fact_kind == FACT_SUCCESS:
                order["paid_provider_payment_id"] = provider_fact_id
            result.update(_reduce_order(s, order, now, newest_fact=fact_kind))
        else:
            result["order_status"] = order.get("status")
        s.commit()
        return result
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def _order_facts(s, order_id):
    rows = s.execute(text(
        "SELECT fact_kind, amount_minor FROM billing_events WHERE order_id=:i"),
        {"i": int(order_id)}).fetchall()
    return [_rowdict(r) for r in rows]


def _reduce_order(s, order, now, newest_fact=None):
    facts = _order_facts(s, order["id"])
    kinds = {f["fact_kind"] for f in facts}
    refunded_total = sum(int(f.get("amount_minor") or 0) for f in facts
                         if f["fact_kind"] == FACT_REFUND)
    has_success = FACT_SUCCESS in kinds
    # 累计退款区别对待：只有累计达到已付才把订单置 refunded；部分退款仍 paid
    # （退款事实已 append 记录，权益不因部分退款被撤销）。
    if has_success and refunded_total >= int(order["amount_minor"]):
        target = "refunded"
    elif has_success:
        target = "paid"
    elif FACT_FAILED in kinds:
        target = "failed"
    else:
        target = "pending"

    requires_review = False
    if target == "refunded":
        was_refunded = order.get("status") == "refunded"
        if not was_refunded:
            s.execute(text("UPDATE billing_orders SET status='refunded', updated_at=:now"
                           " WHERE id=:i"),
                      {"now": lifecycle.to_iso(now), "i": order["id"]})
            # 全额退款 → 只在本单首次进入 refunded 时落权益修正事实；退款后 late
            # success 只入财务事实，不再重复/反向修正权益。
            _revoke_entitlement_for_refund(s, order, now)
        order["status"] = "refunded"
    elif target == "paid":
        if order.get("status") != "paid":
            s.execute(text(
                "UPDATE billing_orders SET status='paid', paid_at=:now, updated_at=:now"
                " WHERE id=:i"), {"now": lifecycle.to_iso(now), "i": order["id"]})
            _mark_latest_attempt(s, order["id"], "succeeded", order.get("paid_provider_payment_id"))
            outcome = lifecycle.apply_paid_order(s, order, now)
            requires_review = bool(outcome.get("requires_policy_review"))
        order["status"] = "paid"
    elif target == "failed":
        if order.get("status") not in ("paid", "refunded"):
            s.execute(text(
                "UPDATE billing_orders SET status='failed', updated_at=:now WHERE id=:i"),
                {"now": lifecycle.to_iso(now), "i": order["id"]})
            _mark_latest_attempt(s, order["id"], "failed", None)
            _mark_past_due_if_expired(s, order["tenant_id"], now)
            order["status"] = "failed"
    return {"order_status": target, "requires_policy_review": requires_review,
            "partially_refunded": bool(has_success and 0 < refunded_total
                                       < int(order["amount_minor"]))}


def _revoke_entitlement_for_refund(s, order, now):
    """全额退款且该订单是当前周期资金来源 → 终止订阅并落 false 快照。

    若退款的是历史周期，不能误伤当前周期权益。
    """
    sub_row = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                        {"t": order["tenant_id"]}).fetchone()
    if not sub_row:
        return
    sub = _rowdict(sub_row)
    if not sub.get("current_period_id"):
        return
    period = s.execute(text("SELECT * FROM subscription_periods WHERE id=:i"),
                       {"i": sub["current_period_id"]}).fetchone()
    if not period:
        return
    period = _rowdict(period)
    if str(period.get("order_id") or "") != str(order["id"]):
        return
    s.execute(text("UPDATE subscription_periods SET state='refunded' WHERE id=:i"),
              {"i": period["id"]})
    s.execute(text(
        "UPDATE subscriptions SET state='terminated', terminated_at=:now, updated_at=:now"
        " WHERE id=:i"), {"now": lifecycle.to_iso(now), "i": sub["id"]})
    _snapshot(s, order["tenant_id"], False, "refunded", sub.get("policy_ref"))


def _mark_latest_attempt(s, order_id, status, provider_payment_id=None):
    row = s.execute(text(
        "SELECT id FROM payment_attempts WHERE order_id=:i ORDER BY id DESC LIMIT 1"),
        {"i": int(order_id)}).fetchone()
    if not row:
        return
    params = {"st": status, "i": int(row[0]),
              "now": lifecycle.to_iso(lifecycle.utcnow())}
    sql = "UPDATE payment_attempts SET status=:st, updated_at=:now"
    if provider_payment_id:
        sql += ", provider_payment_id=COALESCE(provider_payment_id,:pp)"
        params["pp"] = provider_payment_id
    sql += " WHERE id=:i"
    s.execute(text(sql), params)


def _mark_past_due_if_expired(s, tenant_id, now):
    sub = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                    {"t": tenant_id}).fetchone()
    if not sub:
        return
    sub = _rowdict(sub)
    if sub.get("state") != "active" or not sub.get("current_period_id"):
        return
    period = s.execute(text("SELECT * FROM subscription_periods WHERE id=:i"),
                       {"i": sub["current_period_id"]}).fetchone()
    if period and lifecycle.parse_iso(_rowdict(period)["ends_at"]) <= now:
        s.execute(text(
            "UPDATE subscriptions SET state='past_due', past_due_since=:now, updated_at=:now"
            " WHERE id=:i"), {"now": lifecycle.to_iso(now), "i": sub["id"]})
        _snapshot(s, tenant_id, False, "past_due", sub.get("policy_ref"))


def _snapshot(s, tenant_id, allow, basis, policy_ref=""):
    s.execute(text(
        "INSERT INTO entitlement_snapshots (tenant_id, allow_new_ai, basis, policy_ref,"
        " created_at) VALUES (:t,:a,:b,:p,:now)"),
        {"t": tenant_id, "a": 1 if allow else 0, "b": basis, "p": policy_ref or "",
         "now": lifecycle.to_iso(lifecycle.utcnow())})


def reduce_state(order_no, tenant_id=None):
    """只读归约（用于对账/成功页）：返回数据库事实推导出的订单终态。"""
    s = _session()
    try:
        params = {"o": order_no}
        sql = "SELECT * FROM billing_orders WHERE order_no=:o"
        if tenant_id:
            sql += " AND tenant_id=:t"
            params["t"] = tenant_id
        order = s.execute(text(sql), params).fetchone()
        if not order:
            return None
        order = _rowdict(order)
        facts = _order_facts(s, order["id"])
        kinds = {f["fact_kind"] for f in facts}
        refunded_total = sum(int(f.get("amount_minor") or 0) for f in facts
                             if f["fact_kind"] == FACT_REFUND)
        if FACT_SUCCESS in kinds and refunded_total >= int(order["amount_minor"]):
            return "refunded"
        if FACT_SUCCESS in kinds:
            return "paid"
        if FACT_FAILED in kinds:
            return "failed"
        return order.get("status") or "created"
    finally:
        s.close()


def mark_payment_failed(tenant_id, order_no, provider_payment_id, amount_minor,
                        currency, event_id="", object_version=1):
    """登记一次失败尝试（续费失败/渠道拒绝）；不改已成功订单的终态。"""
    return apply_fact({
        "tenant_id": tenant_id, "order_no": order_no,
        "provider_payment_id": provider_payment_id, "fact_kind": FACT_FAILED,
        "object_version": object_version, "amount_minor": amount_minor,
        "currency": currency, "event_id": event_id,
    })


def reconcile_missing(tenant_id=None, now=None, adapter_factory=None):
    """定时对账：只查询 pending/unknown attempts，单次查询、绝不重新扣款。

    每条 attempt 通过 reconciled_at 标记「已对账一次」，重复运行不重复查询。
    返回动作列表（便于审计/runbook 观测）。
    """
    from app.billing.providers.fake import get_adapter
    now = now or lifecycle.utcnow()
    s = _session()
    try:
        params = {}
        sql = ("SELECT a.*, o.order_no, o.tenant_id, o.currency AS order_currency"
               " FROM payment_attempts a JOIN billing_orders o ON o.id=a.order_id"
               " WHERE a.status IN ('pending','unknown') AND a.reconciled_at IS NULL")
        if tenant_id:
            sql += " AND o.tenant_id=:t"
            params["t"] = tenant_id
        attempts = [_rowdict(r) for r in s.execute(text(sql), params).fetchall()]
    finally:
        s.close()

    actions = []
    for a in attempts:
        # 逐笔隔离：单笔金额不符/渠道异常不得中断整批对账。
        try:
            factory = adapter_factory or get_adapter
            try:
                adapter = factory(a.get("provider") or "fake")
            except NotImplementedError:
                actions.append({"attempt_id": a["id"], "order_no": a["order_no"],
                                "action": "provider_unavailable"})
                continue
            query = getattr(adapter, "query_payment", None)
            try:
                result = query(a["order_no"], a.get("provider_payment_id")) \
                    if callable(query) else {"status": "unknown"}
            except Exception:
                result = {"status": "unknown"}
            result = dict(result or {})
            status = result.get("status") or "unknown"
            action = {"attempt_id": a["id"], "order_no": a["order_no"],
                      "query_status": status}
            if status in ("succeeded", "failed"):
                fact_kind = FACT_SUCCESS if status == "succeeded" else FACT_FAILED
                apply_fact({
                    "tenant_id": a["tenant_id"], "order_no": a["order_no"],
                    "provider_payment_id": (result.get("provider_payment_id")
                                            or a.get("provider_payment_id")
                                            or "reconciled-%s" % a["id"]),
                    "fact_kind": fact_kind, "object_version": 1,
                    "amount_minor": result.get("amount_minor", a.get("amount_minor")),
                    "currency": result.get("currency") or a.get("order_currency"),
                    "event_id": "reconcile-%s" % a["id"],
                    "source": "reconcile",
                })
                action["action"] = "fact_applied"
            else:
                action["action"] = "still_unknown"
            _mark_reconciled(a["id"], status)
            actions.append(action)
        except Exception as exc:  # noqa: BLE001 记录后继续下一笔
            _mark_reconciled(a["id"], "error")
            actions.append({"attempt_id": a["id"], "order_no": a.get("order_no"),
                            "action": "error", "error": str(exc)[:240]})
    return actions


def _mark_reconciled(attempt_id, status):
    """终态才写 reconciled_at；unknown/error 保留待下次查询（outage 后可重查）。"""
    s = _session()
    try:
        sql = ("UPDATE payment_attempts SET"
               " reconcile_count=COALESCE(reconcile_count,0)+1, updated_at=:now")
        params = {"now": lifecycle.to_iso(lifecycle.utcnow()), "i": int(attempt_id)}
        if status in ("succeeded", "failed"):
            sql += ", reconciled_at=:now"
        if status == "unknown":
            sql += ", status='unknown'"
        sql += " WHERE id=:i"
        s.execute(text(sql), params)
        s.commit()
    finally:
        s.close()
