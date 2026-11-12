# -*- coding: utf-8 -*-
"""S05 回调收验与持久 Inbox（伪造/重放/错金额、重复 event/semantic、DB 失败不 ack）。"""

import json
import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s05_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s05.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s05-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import inbox, lifecycle, reconcile  # noqa: E402
from app.billing.errors import SecurityReject  # noqa: E402
from app.main import app  # noqa: E402
import _billing_support as support  # noqa: E402

db._make_engine()
client = TestClient(app, raise_server_exceptions=False)
_ORIGINAL_APPLY = reconcile.apply_fact


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    support.cleanup_billing()
    yield
    support.cleanup_billing()


def _raw(payload):
    return json.dumps(payload, sort_keys=True).encode("utf-8")


def _headers(raw, timestamp=None, secret=None, signature=None):
    ts = str(int(timestamp if timestamp is not None else time.time()))
    sig = signature if signature is not None else inbox.sign(raw, ts, secret)
    return {"X-Billing-Timestamp": ts, "X-Billing-Signature": sig}


def _payload(tenant, order_no, event_id="evt-1", provider_payment_id="pay-1",
             fact_kind="payment_succeeded", amount_minor=9900, currency="HKD"):
    return {"event_id": event_id, "merchant_id": tenant, "environment": "sandbox",
            "fact_kind": fact_kind, "order_no": order_no,
            "provider_payment_id": provider_payment_id,
            "amount_minor": amount_minor, "currency": currency,
            "object_version": 1}


def _count(table, where="1=1", params=None):
    s = db.get_session()
    try:
        return int(s.execute(text("SELECT COUNT(*) FROM %s WHERE %s" % (table, where)),
                             params or {}).scalar() or 0)
    finally:
        s.close()


def test_forged_replayed_wrong_amount_callback(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t05a", "PLACEHOLDER", "co-05a")
    order_no = order["order_no"]
    payload = _payload("t05a", order_no)

    # 1) 伪造签名
    forged = _headers(_raw(payload), signature="deadbeef")
    with pytest.raises(SecurityReject) as e1:
        inbox.receive("fake", _raw(payload), forged)
    assert e1.value.code == "WEBHOOK_SIGNATURE_INVALID"

    # 2) 时间窗重放（签名本身合法但时间过旧）
    old_ts = int(time.time()) - 3600
    with pytest.raises(SecurityReject) as e2:
        inbox.receive("fake", _raw(payload),
                      _headers(_raw(payload), timestamp=old_ts))
    assert e2.value.code == "WEBHOOK_REPLAY_WINDOW"

    # 3) 金额被篡改（签名合法）
    wrong = _payload("t05a", order_no, amount_minor=1)
    with pytest.raises(SecurityReject) as e3:
        inbox.receive("fake", _raw(wrong), _headers(_raw(wrong)))
    assert e3.value.code == "WEBHOOK_AMOUNT_MISMATCH"

    # 4) merchant 被篡改 → 拒绝（跨商户伪造）
    wrong_merchant = _payload("t05b", order_no)
    with pytest.raises(SecurityReject) as e4:
        inbox.receive("fake", _raw(wrong_merchant), _headers(_raw(wrong_merchant)))
    assert e4.value.code == "WEBHOOK_MERCHANT_MISMATCH"

    assert _count("provider_inbox") == 0 and _count("billing_events") == 0
    assert lifecycle.get_order(order_no, "t05a")["status"] == "created"

    # 5) 合法回调经 HTTP 端点（匿名白名单 + 原 body 验签）→ 先落 inbox，再归一事实
    r = client.post("/api/billing/webhook/fake", content=_raw(payload),
                    headers=_headers(_raw(payload)))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "accepted"
    assert _count("billing_events") == 1
    assert lifecycle.get_order(order_no, "t05a")["status"] == "paid"


def test_duplicate_event_and_semantic_duplicate(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t05c", "PLACEHOLDER", "co-05c")
    order_no = order["order_no"]
    payload = _payload("t05c", order_no, event_id="evt-dup")
    raw = _raw(payload)
    headers = _headers(raw)

    first = inbox.receive("fake", raw, headers)
    inbox.normalize(inbox_ids=[first["inbox_id"]])
    again = inbox.receive("fake", raw, headers)
    assert again["status"] == "duplicate"
    assert _count("provider_inbox") == 1
    assert _count("billing_events") == 1
    assert _count("subscription_periods") == 1

    # 不同 event ID、同 provider fact → 语义重复：入 inbox 但只入账一次、不二次授期
    payload2 = _payload("t05c", order_no, event_id="evt-dup-2")
    raw2 = _raw(payload2)
    second = inbox.receive("fake", raw2, _headers(raw2))
    assert second["status"] == "accepted"
    inbox.normalize(inbox_ids=[second["inbox_id"]])
    assert _count("provider_inbox") == 2
    assert _count("billing_events") == 1
    assert _count("subscription_periods") == 1

    # 不同 provider_payment_id 但同订单 success：记录事实，仍不二次授期
    payload3 = _payload("t05c", order_no, event_id="evt-dup-3",
                        provider_payment_id="pay-other")
    raw3 = _raw(payload3)
    third = inbox.receive("fake", raw3, _headers(raw3))
    inbox.normalize(inbox_ids=[third["inbox_id"]])
    assert _count("billing_events") == 2
    assert _count("subscription_periods") == 1


def test_db_down_retry_delivery(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t05d", "PLACEHOLDER", "co-05d")
    payload = _payload("t05d", order["order_no"], event_id="evt-db")
    raw = _raw(payload)
    headers = _headers(raw)

    def _db_down(*args, **kwargs):
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(inbox, "_insert_inbox", _db_down)
    with pytest.raises(RuntimeError):
        inbox.receive("fake", raw, headers)
    assert _count("provider_inbox") == 0

    # 恢复后渠道重投：同一事件成功入库（不 ack 成功 → 不丢事件）
    monkeypatch.undo()
    support.install_policy(monkeypatch)
    result = inbox.receive("fake", raw, _headers(raw))
    assert result["status"] == "accepted"
    assert _count("provider_inbox") == 1


def test_normalize_failure_stays_pending_and_retry_succeeds(monkeypatch):
    support.install_policy(monkeypatch)
    order = lifecycle.create_checkout("t05e", "PLACEHOLDER", "co-05e")
    payload = _payload("t05e", order["order_no"], event_id="evt-normalize")
    raw = _raw(payload)
    calls = {"n": 0}

    def _fail_once(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("simulated normalize failure")

    monkeypatch.setattr(reconcile, "apply_fact", _fail_once)
    r = client.post("/api/billing/webhook/fake", content=raw,
                    headers=_headers(raw))
    # 已落 Inbox 但归约失败：不得 200 ack，要求渠道重投
    assert r.status_code == 503, r.text
    assert r.json()["retry"] is True
    assert calls["n"] == 1
    s = db.get_session()
    try:
        status = s.execute(text("SELECT processed_status FROM provider_inbox")).scalar()
    finally:
        s.close()
    assert status == "pending"                   # 事实没有标死丢失
    assert _count("billing_events") == 0

    # 渠道重投同一事件：Inbox 去重，但 pending 归一重试成功
    monkeypatch.setattr(reconcile, "apply_fact", _ORIGINAL_APPLY)
    r2 = client.post("/api/billing/webhook/fake", content=raw,
                     headers=_headers(raw))
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "duplicate"
    assert _count("billing_events") == 1
    assert lifecycle.get_order(order["order_no"], "t05e")["status"] == "paid"
