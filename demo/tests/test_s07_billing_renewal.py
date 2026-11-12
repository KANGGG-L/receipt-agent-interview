# -*- coding: utf-8 -*-
"""S07 续费/欠费恢复/取消（失败后恢复同周期、policy 边界、取消后晚到付款复核）。"""

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s07_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s07.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s07-test-secret-not-the-default-value")
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
import _billing_support as support  # noqa: E402

db._make_engine()

FIXED = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED)
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


def test_renew_fail_recover_same_period(monkeypatch):
    support.install_policy(monkeypatch)
    order, _pay = support.paid_order("t07a", "renew-a")
    sub = lifecycle.get_subscription("t07a")
    period_id = sub["current_period_id"]
    first_end = sub["current_period"]["ends_at"]
    assert sub["state"] == "active" and entitlements.allow_new_ai("t07a") is True

    # 周期到期（手动续费，不自动扣款）→ 欠费停新 AI，但周期事实仍在
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=31))
    lifecycle.refresh_state("t07a")
    sub = lifecycle.get_subscription("t07a")
    assert sub["state"] == "past_due"
    assert sub["current_period_id"] == period_id
    assert entitlements.allow_new_ai("t07a") is False

    # 续费尝试失败 → 仍欠费、同一周期
    renewal = lifecycle.create_checkout("t07a", "PLACEHOLDER", "renew-b")
    reconcile.mark_payment_failed("t07a", renewal["order_no"], "pay-renew-fail",
                                  9900, "HKD")
    sub = lifecycle.get_subscription("t07a")
    assert sub["state"] == "past_due" and sub["current_period_id"] == period_id
    assert lifecycle.get_order(renewal["order_no"], "t07a")["status"] == "failed"

    # 之后人工登记成功 → 恢复 active，仍是同一周期（不新增第二期、不重复授期）
    paid = lifecycle.register_manual_payment(
        "t07a", renewal["order_no"], 9900, "HKD", "renew-b-pay", invoice_no="INV-RB",
        actor="billing@test")
    assert paid["status"] == "paid"
    sub = lifecycle.get_subscription("t07a")
    assert sub["state"] == "active" and sub["current_period_id"] == period_id
    assert lifecycle.parse_iso(sub["current_period"]["ends_at"]) > \
        lifecycle.parse_iso(first_end)
    assert entitlements.allow_new_ai("t07a") is True
    assert _count("subscription_periods") == 1

    # 同 request_key 重复登记 → 回放，不产生第二笔成功/第二期
    replay = lifecycle.register_manual_payment(
        "t07a", renewal["order_no"], 9900, "HKD", "renew-b-pay", invoice_no="INV-RB")
    assert replay["status"] == "replayed"
    assert _count("subscription_periods") == 1
    assert _count("billing_events", "fact_kind='payment_succeeded'") == 2

    # 人工收款审计：发票号/操作人随事件持久化，可取回且不含卡号/密钥
    order_id = lifecycle.get_order(renewal["order_no"], "t07a")["id"]
    s = db.get_session()
    try:
        payload = s.execute(text(
            "SELECT payload_json FROM billing_events WHERE order_id=:i"
            " AND fact_kind='payment_succeeded'"), {"i": order_id}).scalar()
    finally:
        s.close()
    assert payload and "INV-RB" in payload and "billing@test" in payload
    assert "card" not in payload.lower() and "secret" not in payload.lower()


def test_cancel_at_policy_boundary(monkeypatch):
    # D06：immediate → 立即终止
    support.install_policy(monkeypatch)
    support.paid_order("t07b", "cancel-imm")
    res = lifecycle.cancel_subscription("t07b", actor="billing@test")
    assert res["state"] == "terminated" and res["effective_at"] == "immediate"
    assert lifecycle.get_subscription("t07b")["state"] == "terminated"
    assert entitlements.allow_new_ai("t07b") is False
    # 重复取消幂等
    assert lifecycle.cancel_subscription("t07b")["status"] == "replayed"

    # 若 policy 签发 period_end：预约到周期结束才终止
    support.install_policy(monkeypatch, cancellation__effective_at="period_end")
    support.paid_order("t07c", "cancel-end")
    res2 = lifecycle.cancel_subscription("t07c", actor="billing@test")
    sub = lifecycle.get_subscription("t07c")
    assert res2["state"] == "cancel_scheduled"
    assert sub["state"] == "cancel_scheduled"
    assert sub["cancel_at"] == sub["current_period"]["ends_at"]
    assert entitlements.allow_new_ai("t07c") is True   # 本周期内仍有效

    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=31))
    lifecycle.refresh_state("t07c")
    assert lifecycle.get_subscription("t07c")["state"] == "terminated"
    assert entitlements.allow_new_ai("t07c") is False


def test_late_payment_after_cancel_policy_review(monkeypatch):
    support.install_policy(monkeypatch)
    support.paid_order("t07d", "late-base")
    pending = lifecycle.create_checkout("t07d", "PLACEHOLDER", "late-order")
    lifecycle.cancel_subscription("t07d", actor="billing@test")
    assert lifecycle.get_subscription("t07d")["state"] == "terminated"

    # 取消后晚到付款：财务事实入账，但权益不自动复活，留 policy 复核事实
    result = lifecycle.register_manual_payment(
        "t07d", pending["order_no"], 9900, "HKD", "late-pay", invoice_no="INV-LATE")
    assert result["requires_policy_review"] is True
    assert lifecycle.get_order(pending["order_no"], "t07d")["status"] == "paid"
    assert lifecycle.get_subscription("t07d")["state"] == "terminated"
    assert entitlements.allow_new_ai("t07d") is False
    assert _count("entitlement_snapshots",
                  "basis='cancelled_late_payment_review'") == 1
