# -*- coding: utf-8 -*-
"""S06 乱序/漏回调与对账（事实优先归约、只对账一次、unknown 不重复扣款）。"""

import os
import sys
import tempfile

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s06_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s06.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s06-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import entitlements, lifecycle, reconcile  # noqa: E402
from app.billing.errors import ProviderUnknown  # noqa: E402
from app.billing.providers.fake import FakeAdapter  # noqa: E402
import _billing_support as support  # noqa: E402

db._make_engine()
_ORIGINAL_CREATE = FakeAdapter.create_checkout


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


def _fact(tenant, order_no, provider_id, kind, amount=9900, version=1):
    return {"tenant_id": tenant, "order_no": order_no,
            "provider_payment_id": provider_id, "fact_kind": kind,
            "object_version": version, "amount_minor": amount, "currency": "HKD",
            "event_id": "evt-%s-%s" % (kind, provider_id)}


def test_out_of_order_success_failure_refund(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t06a", "PLACEHOLDER", "co-06a")
    order_no = order["order_no"]

    # 乱序：先失败、后成功、再退款、最后 late success
    reconcile.apply_fact(_fact("t06a", order_no, "pay-fail", "payment_failed"))
    assert reconcile.reduce_state(order_no, "t06a") == "failed"
    reconcile.apply_fact(_fact("t06a", order_no, "pay-ok", "payment_succeeded"))
    assert reconcile.reduce_state(order_no, "t06a") == "paid"
    assert _count("subscription_periods") == 1
    reconcile.apply_fact(_fact("t06a", order_no, "ref-ok", "refund_succeeded"))
    assert reconcile.reduce_state(order_no, "t06a") == "refunded"
    late = reconcile.apply_fact(_fact("t06a", order_no, "pay-late",
                                      "payment_succeeded", version=2))
    assert late.get("requires_policy_review") is not True
    # 退款后 late success：财务事实保留，但终态/周期/权益不二次变化
    assert reconcile.reduce_state(order_no, "t06a") == "refunded"
    assert _count("subscription_periods") == 1
    assert _count("billing_events", "order_id=:i",
                  {"i": lifecycle.get_order(order_no, "t06a")["id"]}) == 4
    assert _count("entitlement_snapshots", "basis='refunded'") == 1

    # 成功后又失败（乱序的另一组合）：成功优先，终态仍 paid
    order2 = lifecycle.create_checkout("t06a", "PLACEHOLDER", "co-06a-2")
    reconcile.apply_fact(_fact("t06a", order2["order_no"], "pay-2", "payment_succeeded"))
    reconcile.apply_fact(_fact("t06a", order2["order_no"], "fail-2", "payment_failed"))
    assert reconcile.reduce_state(order2["order_no"], "t06a") == "paid"
    assert _count("subscription_periods") == 1


def test_missing_callback_reconcile_once(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t06b", "PLACEHOLDER", "co-06b")
    order_no = order["order_no"]
    calls = {"query": 0}

    def _query(self, order_no_, provider_payment_id=None):
        calls["query"] += 1
        return {"status": "succeeded", "provider_payment_id": "pay-recon",
                "amount_minor": 9900, "currency": "HKD"}

    monkeypatch.setattr(FakeAdapter, "query_payment", _query)

    actions = reconcile.reconcile_missing(tenant_id="t06b")
    assert len(actions) == 1 and actions[0]["action"] == "fact_applied"
    assert reconcile.reduce_state(order_no, "t06b") == "paid"
    assert calls["query"] == 1

    # 漏回调只对账一次：第二次运行不再查询、不再入账
    again = reconcile.reconcile_missing(tenant_id="t06b")
    assert again == []
    assert calls["query"] == 1
    assert _count("billing_events") == 1
    assert _count("subscription_periods") == 1


def test_restart_unknown_no_double_charge(monkeypatch):
    support.install_policy(monkeypatch)
    create_calls = {"n": 0}

    def _unknown(self, *a, **kw):
        create_calls["n"] += 1
        raise ProviderUnknown()

    monkeypatch.setattr(FakeAdapter, "create_checkout", _unknown)
    chk = lifecycle.create_checkout("t06c", "PLACEHOLDER", "co-06c")
    assert chk["status"] == "unknown"
    assert _count("payment_attempts") == 1
    order_no = chk["order_no"]

    # 「重启」后恢复渠道方法，对账只查询
    monkeypatch.setattr(FakeAdapter, "create_checkout", _ORIGINAL_CREATE)
    queries = {"n": 0}

    def _query(self, *a, **kw):
        queries["n"] += 1
        return {"status": "unknown", "provider_payment_id": "pay-unknown"}

    monkeypatch.setattr(FakeAdapter, "query_payment", _query)

    first = reconcile.reconcile_missing(tenant_id="t06c")
    second = reconcile.reconcile_missing(tenant_id="t06c")
    assert len(first) == 1 and first[0]["action"] == "still_unknown"
    # unknown 不是终态：outage 后必须允许下次继续查询（不得写死 reconciled_at）
    assert len(second) == 1 and second[0]["action"] == "still_unknown"
    assert queries["n"] == 2
    assert create_calls["n"] == 1          # 只发起过一次扣款
    assert _count("payment_attempts") == 1
    assert lifecycle.get_order(order_no, "t06c")["status"] == "created"
    assert _count("billing_events") == 0


def test_partial_refund_keeps_paid_and_full_refund_revokes(monkeypatch):
    support.install_policy(monkeypatch)
    order, _ = support.paid_order("t06d", "co-refund")
    order_no = order["order_no"]
    assert entitlements.allow_new_ai("t06d") is True

    # 100/9900 部分退款：订单仍 paid、权益不撤销
    reconcile.apply_fact(_fact("t06d", order_no, "ref-partial", "refund_succeeded",
                               amount=100))
    assert reconcile.reduce_state(order_no, "t06d") == "paid"
    assert lifecycle.get_order(order_no, "t06d")["status"] == "paid"
    assert entitlements.allow_new_ai("t06d") is True
    assert _count("entitlement_snapshots", "basis='refunded'") == 0

    # 补齐到全额：订单 refunded、当前周期 refunded、订阅 terminated、权益关闭
    reconcile.apply_fact(_fact("t06d", order_no, "ref-full", "refund_succeeded",
                               amount=9800))
    assert reconcile.reduce_state(order_no, "t06d") == "refunded"
    sub = lifecycle.get_subscription("t06d")
    assert sub["state"] == "terminated"
    assert sub["current_period"]["state"] == "refunded"
    assert entitlements.allow_new_ai("t06d") is False
    assert _count("entitlement_snapshots", "basis='refunded'") == 1


def test_reconcile_error_isolated_and_continues(monkeypatch):
    support.install_policy(monkeypatch)
    bad = lifecycle.create_checkout("t06e", "PLACEHOLDER", "co-bad")
    good = lifecycle.create_checkout("t06e", "PLACEHOLDER", "co-good")

    def _query(self, order_no, provider_payment_id=None):
        if order_no == bad["order_no"]:
            return {"status": "succeeded", "provider_payment_id": "pay-bad",
                    "amount_minor": 1, "currency": "HKD"}      # 金额不符
        return {"status": "succeeded", "provider_payment_id": "pay-good",
                "amount_minor": 9900, "currency": "HKD"}

    monkeypatch.setattr(FakeAdapter, "query_payment", _query)
    actions = reconcile.reconcile_missing(tenant_id="t06e")
    assert len(actions) == 2
    by_order = {a["order_no"]: a for a in actions}
    assert by_order[bad["order_no"]]["action"] == "error"
    assert "error" in by_order[bad["order_no"]]
    assert by_order[good["order_no"]]["action"] == "fact_applied"
    # 单笔失败不中断：好的已入账，坏的保留待人工核对且不假已付
    assert lifecycle.get_order(good["order_no"], "t06e")["status"] == "paid"
    assert lifecycle.get_order(bad["order_no"], "t06e")["status"] == "created"
