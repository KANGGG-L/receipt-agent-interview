# -*- coding: utf-8 -*-
"""S10 计费一致性补强：并发同 key 一个事实、unknown 重启不重扣、对账可解释、日志无敏感。"""

import logging
import os
import sys
import tempfile
import threading

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s10_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s10.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s10-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import lifecycle, reconcile  # noqa: E402
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


def test_concurrent_checkout_same_key_one_order_one_attempt(monkeypatch):
    support.install_policy(monkeypatch)
    barrier = threading.Barrier(2)
    results, errors = [], []

    def _click():
        barrier.wait()
        try:
            results.append(lifecycle.create_checkout("t10a", "PLACEHOLDER", "ck-conc"))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=_click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    assert {r["order_no"] for r in results} and len({r["order_no"] for r in results}) == 1
    assert _count("billing_orders") == 1
    assert _count("payment_attempts") == 1


def test_concurrent_manual_payment_same_key_one_fact(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t10b", "PLACEHOLDER", "ck-manual")
    barrier = threading.Barrier(2)
    results, errors = [], []

    def _pay():
        barrier.wait()
        try:
            results.append(lifecycle.register_manual_payment(
                "t10b", order["order_no"], 9900, "HKD", "pay-conc",
                invoice_no="INV-CONC", actor="op@t10b"))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=_pay) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == [], errors
    assert sorted(r["status"] for r in results) == ["paid", "replayed"]
    assert _count("payment_attempts", "request_key='pay-conc'") == 1
    assert _count("billing_events", "fact_kind='payment_succeeded'") == 1
    assert lifecycle.get_order(order["order_no"], "t10b")["status"] == "paid"
    assert lifecycle.get_subscription("t10b")["state"] == "active"


def test_restart_unknown_no_double_charge(monkeypatch):
    support.install_policy(monkeypatch)
    calls = {"create": 0, "query": 0}

    def _unknown_create(self, *a, **kw):
        calls["create"] += 1
        raise ProviderUnknown()

    monkeypatch.setattr(FakeAdapter, "create_checkout", _unknown_create)
    chk = lifecycle.create_checkout("t10c", "PLACEHOLDER", "ck-restart")
    monkeypatch.setattr(FakeAdapter, "create_checkout", _ORIGINAL_CREATE)

    def _unknown_query(self, *a, **kw):
        calls["query"] += 1
        return {"status": "unknown"}

    monkeypatch.setattr(FakeAdapter, "query_payment", _unknown_query)
    # 模拟 worker 停止后重启：两次对账都允许查询，但绝不再次扣款
    reconcile.reconcile_missing(tenant_id="t10c")
    reconcile.reconcile_missing(tenant_id="t10c")
    assert calls == {"create": 1, "query": 2}
    assert _count("payment_attempts") == 1
    assert _count("billing_events") == 0
    assert lifecycle.get_order(chk["order_no"], "t10c")["status"] == "created"


def test_reconcile_actions_explainable_no_residual_diff(monkeypatch):
    support.install_policy(monkeypatch)
    orders = [lifecycle.create_checkout("t10d", "PLACEHOLDER", "ck-a")["order_no"],
              lifecycle.create_checkout("t10d", "PLACEHOLDER", "ck-b")["order_no"]]

    def _query(self, order_no, provider_payment_id=None):
        return {"status": "succeeded", "provider_payment_id": "pay-" + order_no[-4:],
                "amount_minor": 9900, "currency": "HKD"}

    monkeypatch.setattr(FakeAdapter, "query_payment", _query)
    actions = reconcile.reconcile_missing(tenant_id="t10d")
    assert len(actions) == 2
    known = {"fact_applied", "still_unknown", "provider_unavailable"}
    for a in actions:
        assert a["action"] in known and "error" not in a
    # 对账后每单终态与事实一致，无未解释差异
    for order_no in orders:
        assert reconcile.reduce_state(order_no, "t10d") == "paid"
        assert lifecycle.get_order(order_no, "t10d")["status"] == "paid"


def test_billing_logs_contain_no_sensitive(monkeypatch, caplog):
    support.install_policy(monkeypatch)
    caplog.set_level(logging.DEBUG)
    support.paid_order("t10e", "ck-log")
    lifecycle.create_checkout("t10e", "PLACEHOLDER", "ck-log-2")
    logs = " ".join(r.getMessage() for r in caplog.records
                    if r.name.startswith(("app.billing", "app.api_billing")))
    for forbidden in ("9900", "test-webhook-secret", "manual-pay-",
                      "sha256", "card", "4111"):
        assert forbidden not in logs, (forbidden, logs)
