# -*- coding: utf-8 -*-
"""S08 退费请求与资金状态（累计不超已付、pending 无最终权益、unknown 不二次请求、无权限拒绝）。"""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s08_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s08.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s08-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import entitlements, reconcile, refunds  # noqa: E402
from app.billing.errors import BillingConflict, BillingDenied  # noqa: E402
from app.billing.policy import PolicyNotConfigured  # noqa: E402
from app.billing.providers.fake import FakeAdapter  # noqa: E402
from app.main import app  # noqa: E402
import _billing_support as support  # noqa: E402

db._make_engine()
client = TestClient(app)
OWNER = {"billing:billing_owner"}
OPERATOR = {"billing:billing_operator", "billing:billing_owner"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    support.cleanup_billing()
    yield
    support.cleanup_billing()


def _count(table, where="1=1", params=None):
    s = db.get_session()
    try:
        return int(s.execute(text("SELECT COUNT(*) FROM %s WHERE %s" % (table, where)),
                             params or {}).scalar() or 0)
    finally:
        s.close()


def test_partial_duplicate_refund_limit(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08a", "ref-base")
    order_no = order["order_no"]

    first = refunds.request_refund("t08a", order_no, 4000, "ref-1", "owner@t08a",
                                   OWNER, reason="部分退款")
    assert first["state"] == "requested"
    # 同 key 同 body → 回放，不增加占用
    replay = refunds.request_refund("t08a", order_no, 4000, "ref-1", "owner@t08a", OWNER)
    assert replay["status"] == "replayed" and replay["refund_id"] == first["refund_id"]
    summary = refunds.refundable_summary("t08a", order_no)
    assert summary["reserved_minor"] == 4000 and summary["refunded_minor"] == 0

    # 累计不得超已付 9900：4000 + 7000 > 9900
    with pytest.raises(BillingConflict) as ei:
        refunds.request_refund("t08a", order_no, 7000, "ref-2", "owner@t08a", OWNER)
    assert ei.value.code == "REFUND_EXCEEDS_PAID"

    # 补齐到已付上限可以；再加 1 分则拒绝
    refunds.request_refund("t08a", order_no, 5900, "ref-3", "owner@t08a", OWNER)
    with pytest.raises(BillingConflict) as ei2:
        refunds.request_refund("t08a", order_no, 1, "ref-4", "owner@t08a", OWNER)
    assert ei2.value.code == "REFUND_EXCEEDS_PAID"
    assert _count("refund_requests") == 2


def test_refund_pending_no_final_entitlement(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08b", "ref-pending")
    before = entitlements.evaluate("t08b")
    req = refunds.request_refund("t08b", order["order_no"], 4000, "ref-pending-1",
                                 "owner@t08b", OWNER)
    assert req["state"] == "requested"
    # pending 不写退款成功事实、不改权益
    assert _count("billing_events", "fact_kind='refund_succeeded'") == 0
    assert _count("entitlement_snapshots", "basis='refunded'") == 0
    after = entitlements.evaluate("t08b")
    assert after["allow_new_ai"] == before["allow_new_ai"] is True
    assert refunds.get_refund("t08b", req["refund_id"])["state"] == "requested"


def test_refund_retry_unknown_no_second_request(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08c", "ref-unknown")
    req = refunds.request_refund("t08c", order["order_no"], 4000, "ref-unknown-1",
                                 "owner@t08c", OWNER)
    calls = {"refund": 0, "query": 0}

    def _timeout(self, *a, **kw):
        calls["refund"] += 1
        raise TimeoutError("provider timeout")

    monkeypatch.setattr(FakeAdapter, "refund", _timeout)
    first = refunds.approve_refund("t08c", req["refund_id"], caps=OPERATOR,
                                   actor="op@t08c")
    assert first["status"] == "processing"
    assert calls["refund"] == 1
    assert _count("billing_events", "fact_kind='refund_succeeded'") == 0

    def _query_unknown(self, *a, **kw):
        calls["query"] += 1
        return {"status": "unknown"}

    monkeypatch.setattr(FakeAdapter, "query_refund", _query_unknown)
    second = refunds.approve_refund("t08c", req["refund_id"], caps=OPERATOR,
                                    actor="op@t08c")
    assert second["status"] == "processing"
    assert calls["refund"] == 1          # 绝不发起第二次退款请求
    assert calls["query"] == 1

    def _query_ok(self, *a, **kw):
        calls["query"] += 1
        return {"status": "succeeded", "provider_refund_id": "fake-ref-late"}

    monkeypatch.setattr(FakeAdapter, "query_refund", _query_ok)
    third = refunds.approve_refund("t08c", req["refund_id"], caps=OPERATOR,
                                   actor="op@t08c")
    assert third["status"] == "succeeded"
    assert calls["refund"] == 1
    assert _count("billing_events", "fact_kind='refund_succeeded'") == 1
    assert refunds.get_refund("t08c", req["refund_id"])["state"] == "succeeded"


def test_unauthorized_refund_denied(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08d", "ref-auth")

    # 餐厅 owner（无 billing 能力）直连领域层 → 拒绝
    with pytest.raises(BillingDenied) as ei:
        refunds.request_refund("t08d", order["order_no"], 1000, "ref-auth-1",
                               "boss@t08d", {"receipt_approve", "finance_read"})
    assert ei.value.code == "BILLING_CAPABILITY_REQUIRED"

    # HTTP：餐厅 owner token 403，人话提示计费权限独立
    owner_token = support.create_account("boss@t08d.test", role="owner", tenant="t08d",
                                         tenant_role="owner")
    r = client.post("/api/billing/refunds", json={
        "order_no": order["order_no"], "amount_minor": 1000, "request_key": "ref-http-1"},
        headers=support.bearer(owner_token))
    assert r.status_code == 403, r.text
    assert "计费" in r.json()["msg"]

    # refund.policy 未签发 → billing owner 也被 fail-closed 拒绝
    monkeypatch.setenv("BILLING_POLICY_JSON", support.policy_json(refund__policy=None))
    billing_token = support.create_account("bill@t08d.test", tenant="t08d",
                                           billing_role="billing_owner")
    r2 = client.post("/api/billing/refunds", json={
        "order_no": order["order_no"], "amount_minor": 1000, "request_key": "ref-http-2"},
        headers=support.bearer(billing_token))
    assert r2.status_code == 403, r2.text
    assert r2.json()["code"] == "POLICY_NOT_CONFIGURED"
    with pytest.raises(PolicyNotConfigured):
        refunds.request_refund("t08d", order["order_no"], 1000, "ref-auth-2",
                               "bill@t08d", OWNER)


def test_fact_layer_cumulative_refund_limit(monkeypatch):
    """事实层独立兜底：渠道回调/重放累计退款也不得超过已付。"""
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08f", "ref-fact")
    order_no = order["order_no"]

    def _refund_fact(provider_id, amount):
        return {"tenant_id": "t08f", "order_no": order_no,
                "provider_payment_id": provider_id, "fact_kind": "refund_succeeded",
                "object_version": 1, "amount_minor": amount, "currency": "HKD",
                "event_id": "evt-" + provider_id}

    first = reconcile.apply_fact(_refund_fact("ref-fact-1", 6000))
    assert first["duplicate"] is False
    assert _count("billing_events", "fact_kind='refund_succeeded'") == 1

    # 第二笔 6000：累计 12000 > 已付 9900 → 拒绝，且不落事实/不改权益
    with pytest.raises(BillingConflict) as ei:
        reconcile.apply_fact(_refund_fact("ref-fact-2", 6000))
    assert ei.value.code == "REFUND_EXCEEDS_PAID"
    assert _count("billing_events", "fact_kind='refund_succeeded'") == 1
    assert reconcile.reduce_state(order_no, "t08f") == "paid"
    assert entitlements.allow_new_ai("t08f") is True

    # 同一事实重放仍幂等（不得因累计校验把自身重复计入而失败）
    replay = reconcile.apply_fact(_refund_fact("ref-fact-1", 6000))
    assert replay["duplicate"] is True

    # 合法累计恰好等于已付（6000 + 3900）→ 成功，终态全额退款
    reconcile.apply_fact(_refund_fact("ref-fact-3", 3900))
    assert reconcile.reduce_state(order_no, "t08f") == "refunded"
    assert entitlements.allow_new_ai("t08f") is False


def test_refund_public_view_no_internal_leak(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t08e", "ref-view")
    token = support.create_account("bill@t08e.test", tenant="t08e",
                                   billing_role="billing_owner")
    req = refunds.request_refund("t08e", order["order_no"], 1000, "ref-view-1",
                                 "bill@t08e.test", OWNER)
    r = client.get("/api/billing/refunds/%s" % req["refund_id"],
                   headers=support.bearer(token))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["id"] == req["refund_id"] and data["state"] == "requested"
    assert data["amount_display"] == "$***"
    assert data["created_at"] and "processed_at" in data
    # 公共视图不得泄漏内部金额/hash/渠道流水
    for forbidden in ("amount_minor", "request_hash", "request_key",
                      "provider_request_key", "provider_refund_id",
                      "provider_payment_id"):
        assert forbidden not in data, forbidden
