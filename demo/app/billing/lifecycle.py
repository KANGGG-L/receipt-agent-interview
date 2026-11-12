# -*- coding: utf-8 -*-
"""S03/S04/S07 订阅生命周期：定向 coupon 试用、人工收款订单、周期、取消。

口径（DEC-EXE-0004 D06 部分签发）：
- 试用 = 定向 coupon 兑换；服务端 UTC 时钟起算 14 天；同 coupon 一次性、同租户只能
  领取一次（重登/换 coupon 都不能重复领）；免绑卡。
- 套餐 = 占位：数值价格不对外展示（API 一律 $***），内部合成金额仅测试证据。
- 渠道 = 人工登记（provider=manual）；FakeAdapter 仅测试/演示。
- 续费 = 手动登记新周期，不自动扣款；到期/欠费只停新 AI，历史保留。
- 取消 = 按 policy 边界；D06 签发 immediate 时立即终止，period_end 时预约到期终止。
- 所有金额为 minor units 整数、币种显式；时间统一 UTC ISO。
"""

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import db
from app.billing.errors import (
    BillingConflict, BillingDenied, BillingNotFound, BillingError, ProviderUnknown,
)
from app.billing.policy import PolicyNotConfigured, load_policy

TRIAL_DAYS_DEFAULT = 14


# -------------------------------------------------------------
# 时间 / 会话 / 序列化
# -------------------------------------------------------------
def utcnow():
    """服务端权威时钟（UTC）。测试通过 monkeypatch 本函数推进时间。"""
    return datetime.now(timezone.utc)


def to_iso(dt):
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _session():
    return db.get_session()


def _rowdict(row):
    return {k: row._mapping[k] for k in row._mapping.keys()} if row is not None else None


# -------------------------------------------------------------
# policy / 套餐占位
# -------------------------------------------------------------
def load_policy_or_raise():
    return load_policy()


def resolve_plan(policy, plan_ref=None):
    """解析套餐占位参数：返回服务端金额/币种/周期天数（客户端金额永不参与）。

    D06：数值价格未签发。tier 上没有合成 amount_minor/currency 时一律拒绝下单，
    绝不猜默认价格；`price_display` 对外恒为 $***。
    """
    tiers = ((policy.get("plan") or {}).get("tiers") or [])
    chosen = None
    if plan_ref:
        for tier in tiers:
            if str(tier.get("ref")) == str(plan_ref):
                chosen = tier
                break
        if chosen is None:
            raise BillingNotFound("PLAN_NOT_FOUND", "找不到这个套餐，请刷新页面后重新选择。")
    else:
        chosen = tiers[0] if tiers else None
    if not chosen:
        raise BillingDenied("PLAN_UNAVAILABLE", "套餐还没配置好，暂时不能下单。请稍后再试。")
    amount = chosen.get("amount_minor")
    currency = chosen.get("currency")
    if amount is None or not currency:
        raise BillingDenied(
            "PLAN_PRICE_UNAVAILABLE",
            "套餐价格还没配置好，暂时不能下单或收款。请稍后再试或联系平台管理员。")
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise BillingDenied("PLAN_PRICE_UNAVAILABLE", "套餐价格配置有误，暂时不能下单。")
    return {
        "plan_ref": str(chosen.get("ref") or plan_ref or "PLACEHOLDER"),
        "amount_minor": amount,
        "currency": str(currency).upper(),
        "period_days": int(chosen.get("period_days") or 30),
    }


def _channel_provider(policy):
    return str(((policy.get("channel") or {}).get("provider") or "")).strip().lower()


def _adapter_for(provider):
    from app.billing.providers.fake import get_adapter
    try:
        return get_adapter(provider)
    except NotImplementedError:
        raise BillingDenied(
            "PROVIDER_NOT_AVAILABLE",
            "这个支付渠道还没接入，暂时不能在线下单；请联系平台人工登记收款。")


# -------------------------------------------------------------
# 试用 coupon
# -------------------------------------------------------------
def issue_coupon(code, tenant_id, trial_days=TRIAL_DAYS_DEFAULT, created_by="",
                 expires_at=None):
    """定向发放试用 coupon（人工线下执行；系统只做核销）。同 code 幂等返回。"""
    code = str(code or "").strip()
    tenant_id = str(tenant_id or "").strip()
    if not code or not tenant_id:
        raise BillingError("COUPON_INVALID", "试用券编号或商户不能为空。")
    s = _session()
    try:
        row = s.execute(text("SELECT * FROM billing_coupons WHERE coupon_code=:c"),
                        {"c": code}).fetchone()
        if row:
            existing = _rowdict(row)
            if existing.get("tenant_id") != tenant_id:
                raise BillingConflict(
                    "COUPON_CODE_REUSED", "这个试用券编号已经发给别的商户了，请换一个编号。")
            return existing
        s.execute(text(
            "INSERT INTO billing_coupons (coupon_code, tenant_id, trial_days, status,"
            " created_by, created_at, expires_at)"
            " VALUES (:c,:t,:d,'issued',:b,:now,:exp)"),
            {"c": code, "t": tenant_id, "d": int(trial_days), "b": str(created_by or ""),
             "now": to_iso(utcnow()), "exp": str(expires_at or "")})
        s.commit()
        return _rowdict(s.execute(text("SELECT * FROM billing_coupons WHERE coupon_code=:c"),
                                  {"c": code}).fetchone())
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_subscription(tenant_id):
    """读取订阅与当前周期（只读，不因读取而改状态）。"""
    s = _session()
    try:
        sub = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                        {"t": tenant_id}).fetchone()
        if not sub:
            return None
        out = _rowdict(sub)
        if out.get("current_period_id"):
            period = s.execute(text("SELECT * FROM subscription_periods WHERE id=:i"),
                               {"i": out["current_period_id"]}).fetchone()
            out["current_period"] = _rowdict(period)
        else:
            out["current_period"] = None
        return out
    finally:
        s.close()


def _snapshot(s, tenant_id, allow, basis, policy_ref=""):
    s.execute(text(
        "INSERT INTO entitlement_snapshots (tenant_id, allow_new_ai, basis, policy_ref,"
        " created_at) VALUES (:t,:a,:b,:p,:now)"),
        {"t": tenant_id, "a": 1 if allow else 0, "b": basis, "p": policy_ref or "",
         "now": to_iso(utcnow())})


def redeem_trial_coupon(tenant_id, code, actor=""):
    """核销 coupon 并启动 14 天试用（服务端时钟；一次性；同租户不可重复领取）。"""
    tenant_id = str(tenant_id or "").strip()
    code = str(code or "").strip()
    if not tenant_id or not code:
        raise BillingError("COUPON_INVALID", "请填写试用券编号后再兑换。")
    policy, policy_ref = load_policy_or_raise()
    now = utcnow()
    s = _session()
    try:
        coupon = s.execute(text("SELECT * FROM billing_coupons WHERE coupon_code=:c"),
                           {"c": code}).fetchone()
        if not coupon:
            raise BillingNotFound("COUPON_NOT_FOUND", "找不到这张试用券，请确认编号是否正确。")
        coupon = _rowdict(coupon)
        if coupon.get("tenant_id") != tenant_id:
            raise BillingDenied("COUPON_TENANT_MISMATCH",
                                "这张试用券不是发给当前商户的，不能兑换。")
        if coupon.get("redeemed_at"):
            raise BillingConflict("COUPON_ALREADY_REDEEMED",
                                  "这张试用券已经用过了，不能重复领取。")
        if coupon.get("expires_at") and parse_iso(coupon["expires_at"]) < now:
            raise BillingConflict("COUPON_EXPIRED", "这张试用券已过期，请联系平台重新发放。")

        prior = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                          {"t": tenant_id}).fetchone()
        if prior and _rowdict(prior).get("trial_started_at"):
            raise BillingConflict(
                "TRIAL_ALREADY_USED",
                "这个商户已经领取过试用，不能重复领取。如需继续使用请登记套餐续费。")

        # 原子核销：只有 redeemed_at 为空时才能更新成功，防并发双领。
        claimed = s.execute(text(
            "UPDATE billing_coupons SET redeemed_at=:now, redeemed_by=:a, status='redeemed'"
            " WHERE coupon_code=:c AND redeemed_at IS NULL"),
            {"now": to_iso(now), "a": str(actor or ""), "c": code})
        if claimed.rowcount != 1:
            raise BillingConflict("COUPON_ALREADY_REDEEMED",
                                  "这张试用券已经用过了，不能重复领取。")

        days = int(coupon.get("trial_days") or TRIAL_DAYS_DEFAULT)
        ends = now + timedelta(days=days)
        if prior:
            sub_id = int(prior._mapping["id"])
            s.execute(text(
                "UPDATE subscriptions SET state='trialing', plan_ref=:p, policy_ref=:pr,"
                " trial_started_at=:st, trial_ends_at=:en, updated_at=:now,"
                " cancelled_at=NULL, cancel_at=NULL, terminated_at=NULL, past_due_since=NULL"
                " WHERE id=:i"),
                {"p": "TRIAL-COUPON", "pr": policy_ref, "st": to_iso(now),
                 "en": to_iso(ends), "now": to_iso(now), "i": sub_id})
        else:
            s.execute(text(
                "INSERT INTO subscriptions (tenant_id, state, plan_ref, policy_ref,"
                " trial_started_at, trial_ends_at, created_at, updated_at)"
                " VALUES (:t,'trialing',:p,:pr,:st,:en,:now,:now)"),
                {"t": tenant_id, "p": "TRIAL-COUPON", "pr": policy_ref,
                 "st": to_iso(now), "en": to_iso(ends), "now": to_iso(now)})
            sub_id = int(s.execute(text("SELECT id FROM subscriptions WHERE tenant_id=:t"),
                                   {"t": tenant_id}).scalar())
        # 历史周期保留，仅把当前试用周期挂到订阅上（append-only：不删旧周期）
        s.execute(text(
            "INSERT INTO subscription_periods (subscription_id, starts_at, ends_at,"
            " verified_payment_id, kind, state, created_at)"
            " VALUES (:s,:st,:en,NULL,'trial','active',:now)"),
            {"s": sub_id, "st": to_iso(now), "en": to_iso(ends), "now": to_iso(now)})
        period_id = int(s.execute(text(
            "SELECT id FROM subscription_periods WHERE subscription_id=:s"
            " ORDER BY id DESC LIMIT 1"), {"s": sub_id}).scalar())
        s.execute(text("UPDATE subscriptions SET current_period_id=:p WHERE id=:s"),
                  {"p": period_id, "s": sub_id})
        _snapshot(s, tenant_id, True, "active_trial", policy_ref)
        s.commit()
        return {
            "tenant_id": tenant_id, "subscription_id": sub_id,
            "state": "trialing", "trial_days": days,
            "trial_started_at": to_iso(now), "trial_ends_at": to_iso(ends),
            "policy_ref": policy_ref,
        }
    except IntegrityError:
        # 双 coupon 并发：两边都读到「无订阅」，先提交者成功，后者撞
        # subscriptions.tenant_id 唯一约束。转成人话 409，绝不把裸 500 抛给客户。
        s.rollback()
        raise BillingConflict(
            "TRIAL_ALREADY_USED",
            "这个商户已经领取过试用，不能重复领取。如需继续使用请登记套餐续费。")
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def refresh_state(tenant_id, now=None):
    """按时间与 policy 收敛状态（试用到期/周期到期欠费/预约取消到期）。

    只做状态与权益快照，不删任何账务/周期事实；重复调用不重复落快照。
    """
    now = now or utcnow()
    s = _session()
    try:
        sub = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                        {"t": tenant_id}).fetchone()
        if not sub:
            return None
        sub = _rowdict(sub)
        period = None
        if sub.get("current_period_id"):
            p = s.execute(text("SELECT * FROM subscription_periods WHERE id=:i"),
                          {"i": sub["current_period_id"]}).fetchone()
            period = _rowdict(p)
        changed = False
        state = sub.get("state")
        if state == "trialing" and sub.get("trial_ends_at") \
                and parse_iso(sub["trial_ends_at"]) <= now:
            state = "trial_expired"
            changed = True
            if period and period.get("kind") == "trial" and period.get("state") == "active":
                s.execute(text("UPDATE subscription_periods SET state='expired' WHERE id=:i"),
                          {"i": period["id"]})
        elif state == "active" and period and parse_iso(period["ends_at"]) <= now:
            # D06 手动续费：到期未登记收款 → 欠费停新 AI，不自动扣款。
            state = "past_due"
            changed = True
        elif state == "cancel_scheduled" and sub.get("cancel_at") \
                and parse_iso(sub["cancel_at"]) <= now:
            state = "terminated"
            changed = True
            if period and period.get("state") == "active":
                s.execute(text("UPDATE subscription_periods SET state='cancelled' WHERE id=:i"),
                          {"i": period["id"]})
        if changed:
            extra = {}
            if state == "past_due":
                extra["past_due_since"] = to_iso(now)
            if state == "terminated":
                extra["terminated_at"] = to_iso(now)
            clauses = ", ".join("%s=:%s" % (k, k) for k in extra)
            sql = "UPDATE subscriptions SET state=:st, updated_at=:now"
            params = {"st": state, "now": to_iso(now), "t": tenant_id}
            if clauses:
                sql += ", " + clauses
                params.update(extra)
            sql += " WHERE id=:i"
            params["i"] = sub["id"]   # 按租户查出的唯一行，id 来自库内
            s.execute(text(sql), params)
            _snapshot(s, tenant_id, False, state, sub.get("policy_ref") or "")
            s.commit()
        return get_subscription(tenant_id)
    finally:
        s.close()


# -------------------------------------------------------------
# 订单 / checkout（S04）
# -------------------------------------------------------------
def _body_hash(plan_ref):
    # 客户端金额刻意不参与 hash：金额只由服务端 policy 决定，改客户端金额不得改变幂等结果。
    raw = json.dumps({"plan_ref": str(plan_ref or "")}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def get_order(order_no, tenant_id=None):
    s = _session()
    try:
        params = {"o": str(order_no)}
        sql = "SELECT * FROM billing_orders WHERE order_no=:o"
        if tenant_id:
            sql += " AND tenant_id=:t"
            params["t"] = tenant_id
        row = s.execute(text(sql), params).fetchone()
        if not row:
            return None
        out = _rowdict(row)
        attempt = s.execute(text(
            "SELECT * FROM payment_attempts WHERE order_id=:i ORDER BY id DESC LIMIT 1"),
            {"i": out["id"]}).fetchone()
        out["latest_attempt"] = _rowdict(attempt)
        return out
    finally:
        s.close()


def _latest_attempt(s, order_id):
    row = s.execute(text(
        "SELECT * FROM payment_attempts WHERE order_id=:i ORDER BY id DESC LIMIT 1"),
        {"i": order_id}).fetchone()
    return _rowdict(row)


def _query_existing_attempt(order_no, tenant_id=None):
    order = get_order(order_no, tenant_id)
    if not order:
        return None
    attempt = order.get("latest_attempt")
    if not attempt:
        return None
    if attempt.get("status") not in ("unknown", "pending"):
        return {"status": attempt.get("status") or "pending"}
    adapter = _adapter_for(attempt.get("provider") or "fake")
    query = getattr(adapter, "query_checkout", None)
    result = query(order_no, attempt.get("checkout_id")) if callable(query) else \
        {"status": "unknown"}
    result = dict(result or {})
    result.setdefault("status", "unknown")
    return result


def _checkout_replay_result(prior, attempt, tenant_id):
    if attempt and attempt.get("status") == "unknown":
        queried = _query_existing_attempt(prior["order_no"], tenant_id)
        return {"status": "unknown", "code": "PROVIDER_RESULT_UNKNOWN",
                "order_no": prior["order_no"], "attempt_id": attempt["id"],
                "queried": True, "query_result": queried,
                "price_display": "$***"}
    return {"status": "replayed", "order_no": prior["order_no"],
            "attempt_id": attempt["id"] if attempt else None,
            "order_status": prior.get("status"), "price_display": "$***"}


def create_checkout(tenant_id, plan_ref, idempotency_key, actor=""):
    """创建/重放订单与单条 pending attempt。

    - 同 key 同 body → 回放同一 order/attempt（重复点击只产生一个 attempt）；
    - 同 key 异 body → 409；
    - 已有 unknown attempt → 先查询，不创建第二次扣款；
    - 并发同 key：唯一索引冲突时回放已存在订单，不抛裸 500；
    - 客户端金额不进入本函数，金额/币种只由 policy 套餐占位决定。
    """
    tenant_id = str(tenant_id or "").strip()
    key = str(idempotency_key or "").strip()
    if not tenant_id or not key:
        raise BillingError("CHECKOUT_INVALID", "下单需要商户与操作编号，请刷新页面后重试。")
    policy, policy_ref = load_policy_or_raise()
    plan = resolve_plan(policy, plan_ref)
    body_hash = _body_hash(plan["plan_ref"])
    provider = _channel_provider(policy)
    now = utcnow()

    s = _session()
    try:
        prior = s.execute(text(
            "SELECT * FROM billing_orders WHERE tenant_id=:t AND idempotency_key=:k"),
            {"t": tenant_id, "k": key}).fetchone()
        if prior:
            prior = _rowdict(prior)
            if prior.get("request_hash") and prior["request_hash"] != body_hash:
                raise BillingConflict(
                    "IDEMPOTENCY_REUSED",
                    "这个操作编号已经用于另一个套餐。请刷新页面后重新下单。")
            attempt = _latest_attempt(s, prior["id"])
            s.commit()
            return _checkout_replay_result(prior, attempt, tenant_id)
        order_no = "ORD-" + uuid.uuid4().hex[:16].upper()
        # INSERT OR IGNORE：并发同 key 时唯一索引不抛异常，后到者回放先到者的订单。
        inserted = s.execute(text(
            "INSERT OR IGNORE INTO billing_orders (tenant_id, order_no, kind, plan_ref,"
            " amount_minor, currency, status, idempotency_key, provider, request_hash,"
            " period_days, created_at, updated_at)"
            " VALUES (:t,:o,'subscription',:p,:a,:c,'created',:k,:pv,:h,:d,:now,:now)"),
            {"t": tenant_id, "o": order_no, "p": plan["plan_ref"],
             "a": plan["amount_minor"], "c": plan["currency"], "k": key,
             "pv": provider, "h": body_hash, "d": plan["period_days"],
             "now": to_iso(now)}).rowcount
        if inserted == 0:
            prior = s.execute(text(
                "SELECT * FROM billing_orders WHERE tenant_id=:t AND idempotency_key=:k"),
                {"t": tenant_id, "k": key}).fetchone()
            if not prior:
                raise BillingConflict("CHECKOUT_CONFLICT",
                                      "这次下单没有完成，请刷新页面后重新操作。")
            prior = _rowdict(prior)
            if prior.get("request_hash") and prior["request_hash"] != body_hash:
                raise BillingConflict(
                    "IDEMPOTENCY_REUSED",
                    "这个操作编号已经用于另一个套餐。请刷新页面后重新下单。")
            attempt = _latest_attempt(s, prior["id"])
            s.commit()
            return _checkout_replay_result(prior, attempt, tenant_id)
        order_id = int(s.execute(text("SELECT id FROM billing_orders WHERE order_no=:o"),
                                 {"o": order_no}).scalar())
        s.execute(text(
            "INSERT OR IGNORE INTO payment_attempts (order_id, provider, amount_minor,"
            " currency, status, request_key, request_hash, created_at, updated_at)"
            " VALUES (:i,:pv,:a,:c,'pending',:k,:h,:now,:now)"),
            {"i": order_id, "pv": provider, "a": plan["amount_minor"],
             "c": plan["currency"], "k": key, "h": body_hash, "now": to_iso(now)})
        attempt_id = int(s.execute(text(
            "SELECT id FROM payment_attempts WHERE order_id=:i ORDER BY id DESC LIMIT 1"),
            {"i": order_id}).scalar())
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    # 不持 DB 锁做网络调用：attempt 已持久化为 pending（outbox）。
    if provider != "manual":
        adapter = _adapter_for(provider)
        try:
            checkout = adapter.create_checkout(order_no, plan["amount_minor"],
                                               plan["currency"], key)
        except (ProviderUnknown, TimeoutError):
            checkout = None
            _update_attempt(attempt_id, status="unknown", checkout_id=None)
            return {"status": "unknown", "code": "PROVIDER_RESULT_UNKNOWN",
                    "order_no": order_no, "attempt_id": attempt_id,
                    "price_display": "$***"}
        _update_attempt(attempt_id, status="pending",
                        checkout_id=(checkout or {}).get("checkout_id"))
    return {"status": "created", "order_no": order_no, "attempt_id": attempt_id,
            "order_status": "created", "price_display": "$***"}


def _update_attempt(attempt_id, **fields):
    if not fields:
        return
    cols = ", ".join("%s=:%s" % (k, k) for k in fields)
    params = dict(fields)
    params["i"] = int(attempt_id)
    params["now"] = to_iso(utcnow())
    s = _session()
    try:
        s.execute(text("UPDATE payment_attempts SET %s, updated_at=:now WHERE id=:i" % cols),
                  params)
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


# -------------------------------------------------------------
# 人工收款（S04/S07）
# -------------------------------------------------------------
def register_manual_payment(tenant_id, order_no, amount_minor, currency, request_key,
                            invoice_no="", actor="", provider="manual"):
    """人工登记已收款项：金额/币种必须与订单一致；同 request_key 幂等。

    财务事实 append-only：写入 payment_succeeded 事件后才更新订单/周期。
    """
    from app.billing import reconcile
    tenant_id = str(tenant_id or "").strip()
    key = str(request_key or "").strip()
    if not tenant_id or not key:
        raise BillingError("PAYMENT_INVALID", "登记收款需要商户与操作编号，请刷新页面后重试。")
    order = get_order(order_no, tenant_id)
    if not order:
        raise BillingNotFound("ORDER_NOT_FOUND", "找不到这笔订单，请刷新页面后再试。")
    try:
        amount_minor = int(amount_minor)
    except (TypeError, ValueError):
        raise BillingError("PAYMENT_INVALID", "收款金额填写有误，请重新填写。")
    if amount_minor != int(order["amount_minor"]):
        raise BillingConflict(
            "PAYMENT_AMOUNT_MISMATCH",
            "登记金额和订单金额不一致。请核对订单后重新登记，不要按客户口头金额记账。")
    if str(currency or "").upper() != str(order["currency"]).upper():
        raise BillingConflict(
            "PAYMENT_CURRENCY_MISMATCH",
            "登记币种和订单币种不一致，请核对后重新登记。")
    if order.get("status") in ("paid", "refunded"):
        # 已付/已退款订单：同 key 回放；新 key 不再产生第二笔财务事实。
        prior = _attempt_by_request_key(order["id"], key)
        if prior:
            return {"status": "replayed", "order_no": order_no, "attempt_id": prior["id"],
                    "order_status": order["status"], "requires_policy_review": False}
        raise BillingConflict("ORDER_ALREADY_SETTLED",
                              "这笔订单已经收款（或已退款），不能重复登记。")

    prior = _attempt_by_request_key(order["id"], key)
    if prior:
        current = get_order(order_no, tenant_id)
        return {"status": "replayed", "order_no": order_no, "attempt_id": prior["id"],
                "order_status": current.get("status") if current else order["status"],
                "requires_policy_review": False}

    now = utcnow()
    provider_payment_id = "manual-pay-" + hashlib.sha256(
        ("%s|%s" % (order_no, key)).encode()).hexdigest()[:24]
    s = _session()
    try:
        # INSERT OR IGNORE：并发同 key 只留一条成功 attempt；后到者回放，不抛 500。
        inserted = s.execute(text(
            "INSERT OR IGNORE INTO payment_attempts (order_id, provider, provider_payment_id,"
            " amount_minor, currency, status, request_key, created_at, updated_at)"
            " VALUES (:i,:pv,:pp,:a,:c,'succeeded',:k,:now,:now)"),
            {"i": order["id"], "pv": "manual", "pp": provider_payment_id,
             "a": amount_minor, "c": str(currency).upper(), "k": key,
             "now": to_iso(now)}).rowcount
        attempt_id = int(s.execute(text(
            "SELECT id FROM payment_attempts WHERE order_id=:i AND request_key=:k"),
            {"i": order["id"], "k": key}).scalar())
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

    if inserted == 0:
        current = get_order(order_no, tenant_id)
        return {"status": "replayed", "order_no": order_no, "attempt_id": attempt_id,
                "order_status": current.get("status") if current else order["status"],
                "requires_policy_review": False}

    result = reconcile.apply_fact({
        "tenant_id": tenant_id, "order_no": order_no,
        "provider_payment_id": provider_payment_id, "fact_kind": "payment_succeeded",
        "object_version": 1, "amount_minor": amount_minor,
        "currency": str(currency).upper(),
        "event_id": "manual-" + hashlib.sha256(provider_payment_id.encode()).hexdigest()[:20],
        "invoice_no": str(invoice_no or ""), "actor": str(actor or ""),
        "source": "manual_collection",
    })
    order_after = get_order(order_no, tenant_id)
    return {"status": "paid", "order_no": order_no, "attempt_id": attempt_id,
            "order_status": order_after.get("status"),
            "requires_policy_review": bool(result.get("requires_policy_review"))}


def _attempt_by_request_key(order_id, key):
    s = _session()
    try:
        row = s.execute(text(
            "SELECT * FROM payment_attempts WHERE order_id=:i AND request_key=:k"),
            {"i": int(order_id), "k": str(key)}).fetchone()
        return _rowdict(row)
    finally:
        s.close()


# -------------------------------------------------------------
# 取消（S07）
# -------------------------------------------------------------
def cancel_subscription(tenant_id, actor="", request_key=""):
    """按 policy 边界取消；D06 签发 immediate 时立即生效。重复取消幂等。"""
    policy, policy_ref = load_policy_or_raise()
    mode = str(((policy.get("cancellation") or {}).get("effective_at") or "")).strip().lower()
    if mode not in ("immediate", "period_end"):
        raise BillingDenied(
            "POLICY_INCOMPLETE", "取消规则还没有配置完整，暂时不能取消订阅，请联系平台管理员。")
    sub = get_subscription(tenant_id)
    if not sub:
        raise BillingNotFound("SUBSCRIPTION_NOT_FOUND", "这个商户还没有订阅，无法取消。")
    if sub["state"] in ("terminated", "cancel_scheduled"):
        return {"status": "replayed", "state": sub["state"],
                "cancel_at": sub.get("cancel_at"), "policy_ref": policy_ref}
    now = utcnow()
    s = _session()
    try:
        if mode == "immediate":
            s.execute(text(
                "UPDATE subscriptions SET state='terminated', cancelled_at=:now,"
                " terminated_at=:now, cancel_at=NULL, updated_at=:now WHERE id=:i"),
                {"now": to_iso(now), "i": sub["id"]})
            if sub.get("current_period_id"):
                s.execute(text("UPDATE subscription_periods SET state='cancelled' WHERE id=:i"),
                          {"i": sub["current_period_id"]})
            _snapshot(s, tenant_id, False, "cancelled_immediate", policy_ref)
            state, cancel_at = "terminated", None
        else:
            period = sub.get("current_period") or {}
            cancel_at = period.get("ends_at") or to_iso(now)
            s.execute(text(
                "UPDATE subscriptions SET state='cancel_scheduled', cancelled_at=:now,"
                " cancel_at=:at, updated_at=:now WHERE id=:i"),
                {"now": to_iso(now), "at": cancel_at, "i": sub["id"]})
            state = "cancel_scheduled"
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
    return {"status": "cancelled", "state": state, "cancel_at": cancel_at,
            "policy_ref": policy_ref, "effective_at": mode}


# -------------------------------------------------------------
# 支付成功后落到订阅周期（由 reconcile 在事实事务后调用）
# -------------------------------------------------------------
def apply_paid_order(s, order, now=None):
    """把已验证的 payment_succeeded 事实落到订阅/周期。

    返回 {"requires_policy_review": bool}。取消后晚到付款不自动复活订阅——
    财务事实保留，权益修正需按 policy 人工复核。
    """
    now = now or utcnow()
    tenant_id = order["tenant_id"]
    sub_row = s.execute(text("SELECT * FROM subscriptions WHERE tenant_id=:t"),
                        {"t": tenant_id}).fetchone()
    if not sub_row:
        period_days = int(order.get("period_days") or 30)
        s.execute(text(
            "INSERT INTO subscriptions (tenant_id, state, plan_ref, policy_ref, created_at,"
            " updated_at) VALUES (:t,'active',:p,:pr,:now,:now)"),
            {"t": tenant_id, "p": order.get("plan_ref") or "PLACEHOLDER",
             "pr": order.get("policy_ref") or "", "now": to_iso(now)})
        sub_id = int(s.execute(text("SELECT id FROM subscriptions WHERE tenant_id=:t"),
                               {"t": tenant_id}).scalar())
        s.execute(text(
            "INSERT INTO subscription_periods (subscription_id, starts_at, ends_at,"
            " verified_payment_id, kind, state, order_id, provider_payment_id, created_at)"
            " VALUES (:s,:st,:en,NULL,'paid','active',:o,:pp,:now)"),
            {"s": sub_id, "st": to_iso(now),
             "en": to_iso(now + timedelta(days=period_days)), "o": order["id"],
             "pp": order.get("paid_provider_payment_id") or "", "now": to_iso(now)})
        period_id = int(s.execute(text(
            "SELECT id FROM subscription_periods WHERE subscription_id=:s"
            " ORDER BY id DESC LIMIT 1"), {"s": sub_id}).scalar())
        s.execute(text("UPDATE subscriptions SET current_period_id=:p WHERE id=:s"),
                  {"p": period_id, "s": sub_id})
        _snapshot(s, tenant_id, True, "verified_period", order.get("policy_ref") or "")
        return {"requires_policy_review": False}

    sub = _rowdict(sub_row)
    if sub.get("state") in ("terminated", "trial_expired") and sub.get("terminated_at"):
        # 取消后晚到付款：不静默复活；只留复核事实（权益仍为 0）。
        _snapshot(s, tenant_id, False, "cancelled_late_payment_review",
                  sub.get("policy_ref") or "")
        return {"requires_policy_review": True}

    period_days = int(order.get("period_days") or 30)
    period = None
    if sub.get("current_period_id"):
        p = s.execute(text("SELECT * FROM subscription_periods WHERE id=:i"),
                      {"i": sub["current_period_id"]}).fetchone()
        period = _rowdict(p)
    if period and period.get("kind") == "paid" and sub.get("state") in (
            "active", "past_due", "cancel_scheduled"):
        # 续费失败后恢复/正常续费：同一周期幂等（不新增第二期、不重复授期）。
        end = parse_iso(period["ends_at"])
        if end < now:
            end = now
        new_end = end + timedelta(days=period_days)
        s.execute(text(
            "UPDATE subscription_periods SET ends_at=:en, state='active',"
            " order_id=COALESCE(order_id,:o), provider_payment_id=:pp WHERE id=:i"),
            {"en": to_iso(new_end), "o": order["id"],
             "pp": order.get("paid_provider_payment_id") or "", "i": period["id"]})
        period_id = period["id"]
    else:
        if period and period.get("state") == "active":
            s.execute(text("UPDATE subscription_periods SET state='superseded' WHERE id=:i"),
                      {"i": period["id"]})
        s.execute(text(
            "INSERT INTO subscription_periods (subscription_id, starts_at, ends_at,"
            " verified_payment_id, kind, state, order_id, provider_payment_id, created_at)"
            " VALUES (:s,:st,:en,NULL,'paid','active',:o,:pp,:now)"),
            {"s": sub["id"], "st": to_iso(now),
             "en": to_iso(now + timedelta(days=period_days)), "o": order["id"],
             "pp": order.get("paid_provider_payment_id") or "", "now": to_iso(now)})
        period_id = int(s.execute(text(
            "SELECT id FROM subscription_periods WHERE subscription_id=:s"
            " ORDER BY id DESC LIMIT 1"), {"s": sub["id"]}).scalar())
    s.execute(text(
        "UPDATE subscriptions SET state='active', current_period_id=:p, plan_ref=:pl,"
        " policy_ref=:pr, past_due_since=NULL, updated_at=:now WHERE id=:i"),
        {"p": period_id, "pl": order.get("plan_ref") or sub.get("plan_ref"),
         "pr": order.get("policy_ref") or sub.get("policy_ref") or "",
         "now": to_iso(now), "i": sub["id"]})
    _snapshot(s, tenant_id, True, "verified_period",
              order.get("policy_ref") or sub.get("policy_ref") or "")
    return {"requires_policy_review": False}
