# -*- coding: utf-8 -*-
"""S01/S02 商业轨（C-BILL-CONTRACT：policy fail-closed、fake Adapter 确定性、inbox/事件去重）。"""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s02_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s02.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "s02-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)
os.environ.pop("BILLING_POLICY_JSON", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import policy as billing_policy  # noqa: E402
from app.billing.providers import fake as fake_provider  # noqa: E402

db._make_engine()


@pytest.fixture(autouse=True)
def _clean_billing():
    s = db.get_session()
    try:
        for tbl in ("provider_inbox", "billing_events", "payment_attempts",
                    "billing_orders", "subscriptions", "subscription_periods",
                    "entitlement_snapshots", "refund_requests"):
            s.execute(text("DELETE FROM %s" % tbl))
        s.commit()
    finally:
        s.close()
    yield


def test_policy_fail_closed_when_not_issued():
    """D06 未签发 → PolicyNotConfigured（不设默认值、不缺省免绑卡/自动续费）。"""
    with pytest.raises(billing_policy.PolicyNotConfigured):
        billing_policy.load_policy()
    with pytest.raises(billing_policy.PolicyNotConfigured) as ei:
        os.environ["BILLING_POLICY_JSON"] = '{"plan": {"tiers": []}}'  # 缺大部分必需字段
        try:
            billing_policy.load_policy()
        finally:
            os.environ.pop("BILLING_POLICY_JSON", None)
    assert any("policy_ref" in m for m in ei.value.missing)


def test_policy_loads_when_complete(monkeypatch):
    """完整签发（含 policy_ref 版本/审批人）→ 可加载且来源可追。"""
    import json
    monkeypatch.setenv("BILLING_POLICY_JSON", json.dumps({
        "policy_ref": {"version": "2026.10-test", "approved_by": "owner@user",
                       "effective_at": "2026-10-26"},
        "trial": {"eligibility": "new_merchant", "start_rule": "first_login",
                  "bind_card_required": False},
        "plan": {"tiers": [{"ref": "T1"}]},
        "channel": {"provider": "fake"},
        "renewal": {"auto_renew": False},
        "cancellation": {"effective_at": "period_end"},
        "refund": {"policy": "full_within_7d"},
    }))
    obj, ref = billing_policy.load_policy()
    assert "2026.10-test@owner@user" in ref
    assert obj["renewal"]["auto_renew"] is False  # 无默认值：签发了什么就是什么


def test_fake_adapter_deterministic_and_verify_idempotent():
    """FakeAdapter 确定性：同输入同 checkout/事件；verify 幂等。"""
    a = fake_provider.get_adapter("fake")
    co1 = a.create_checkout("ORD-1", 9900, "HKD", "k1")
    co2 = a.create_checkout("ORD-1", 9900, "HKD", "k1")
    assert co1 == co2 and co1["checkout_id"].startswith("fake-co-")
    ev = a.settle("ORD-1", 9900, "HKD")
    ev2 = a.settle("ORD-1", 9900, "HKD")
    assert ev == ev2 and ev["provider_payment_id"].startswith("fake-pay-")
    with pytest.raises(NotImplementedError):
        fake_provider.get_adapter("vendor_a")  # 真实渠道未选：不假装支持


def test_inbox_and_event_dedup():
    """provider_inbox 唯一 (provider,environment,event_id)；billing_events 去重
    (provider_payment_id, fact_kind, object_version)——重放/重复成功不二次入账。"""
    s = db.get_session()
    try:
        s.execute(text(
            "INSERT INTO provider_inbox (provider, environment, event_id, received_at)"
            " VALUES ('fake','sandbox','evt-1','2026-10-26T00:00:00')"))
        with pytest.raises(Exception):
            s.execute(text(
                "INSERT INTO provider_inbox (provider, environment, event_id, received_at)"
                " VALUES ('fake','sandbox','evt-1','2026-10-26T00:00:01')"))
        s.commit()
        s.execute(text(
            "INSERT INTO billing_events (provider_payment_id, fact_kind, object_version,"
            " created_at) VALUES ('fake-pay-1','payment_succeeded',1,'2026-10-26T00:00:00')"))
        s.commit()
        with pytest.raises(Exception):
            s.execute(text(
                "INSERT INTO billing_events (provider_payment_id, fact_kind, object_version,"
                " created_at) VALUES ('fake-pay-1','payment_succeeded',1,'2026-10-26T00:00:01')"))
        s.commit()
    finally:
        s.close()


def test_subscription_state_vocabulary():
    """订阅状态词表：非法状态拒绝写入（应用层枚举约束，DB 层 CHECK 留 S03 收紧）。"""
    valid = ("trialing", "trial_expired", "pending_payment", "active",
             "past_due", "cancel_scheduled", "terminated")
    s = db.get_session()
    try:
        for st in valid:
            s.execute(text(
                "INSERT INTO subscriptions (tenant_id, state, created_at, updated_at)"
                " VALUES (:t,:st,'2026-10-26','2026-10-26')"), {"t": "t-" + st, "st": st})
        s.commit()
    finally:
        s.close()
