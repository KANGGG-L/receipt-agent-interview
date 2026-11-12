# -*- coding: utf-8 -*-
"""S03–S08 计费测试共用支持：合成 policy、临时库清理、角色/令牌、已付订单。

所有金额均为测试合成值（私有证据），policy 只在用例内通过 monkeypatch 注入，
绝不写入真实环境；OPENAI_* 由各测试模块置空，避免真实外呼。
"""

import json

from sqlalchemy import text

from app import auth, db, identity_store


BASE_POLICY = {
    "policy_ref": {"version": "2026.10-test", "approved_by": "owner@test",
                   "effective_at": "2026-10-26"},
    "trial": {"mechanism": "targeted_coupon", "eligibility": "new_merchant",
              "start_rule": "coupon", "bind_card_required": False},
    "plan": {"tiers": [{"ref": "PLACEHOLDER", "amount_minor": 9900,
                        "currency": "HKD", "period_days": 30}]},
    "channel": {"provider": "fake"},
    "renewal": {"auto_renew": False},
    "cancellation": {"effective_at": "immediate"},
    "refund": {"policy": "full_within_7d"},
}


def policy_json(**overrides):
    obj = json.loads(json.dumps(BASE_POLICY))
    for path, value in overrides.items():
        cur = obj
        parts = path.split("__")
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value
    return json.dumps(obj)


def install_policy(monkeypatch, **overrides):
    monkeypatch.setenv("BILLING_POLICY_JSON", policy_json(**overrides))
    monkeypatch.setenv("BILLING_WEBHOOK_SECRET", "test-webhook-secret")
    monkeypatch.setenv("BILLING_ENVIRONMENT", "sandbox")


def cleanup_billing():
    s = db.get_session()
    try:
        for tbl in ("refund_requests", "entitlement_reservations", "entitlement_snapshots",
                    "billing_coupons", "provider_inbox", "billing_events",
                    "payment_attempts", "billing_orders", "subscription_periods",
                    "subscriptions"):
            s.execute(text("DELETE FROM %s" % tbl))
        s.commit()
    finally:
        s.close()


def create_account(email, role="staff", tenant="default",
                   billing_role=None, tenant_role=None):
    """注册测试账号；billing 角色显式授予（餐厅 owner 不自动继承）。"""
    auth.ACCOUNTS[email] = ("test-pw-not-real", role, tenant)
    if billing_role:
        identity_store.grant_billing_membership(email, tenant, billing_role)
    if tenant_role:
        identity_store.grant_tenant_membership(email, tenant, tenant_role)
    return auth.issue_token(email, role, tenant)


def bearer(token):
    return {"Authorization": "Bearer " + token}


def paid_order(tenant_id, key, amount_minor=9900, currency="HKD", plan_ref="PLACEHOLDER"):
    """建订单并人工登记收款（测试合成金额；返回订单内部视图）。"""
    from app.billing import lifecycle
    checkout = lifecycle.create_checkout(tenant_id, plan_ref, key)
    pay = lifecycle.register_manual_payment(
        tenant_id, checkout["order_no"], amount_minor, currency,
        key + "-pay", invoice_no="INV-" + key, actor="billing@test")
    return lifecycle.get_order(checkout["order_no"], tenant_id), pay
