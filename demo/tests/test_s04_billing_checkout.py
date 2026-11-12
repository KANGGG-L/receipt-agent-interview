# -*- coding: utf-8 -*-
"""S04 checkout 与订单幂等（重复点击一个 attempt、忽略客户端金额、unknown 先查）。"""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s04_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s04.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s04-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import lifecycle  # noqa: E402
from app.billing.errors import ProviderUnknown  # noqa: E402
from app.billing.providers.fake import FakeAdapter  # noqa: E402
from app.main import app  # noqa: E402
import _billing_support as support  # noqa: E402

db._make_engine()
client = TestClient(app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    monkeypatch.setenv("BILLING_ENFORCEMENT_ENABLED", "0")
    support.cleanup_billing()
    yield
    support.cleanup_billing()


def _attempt_count(order_no):
    s = db.get_session()
    try:
        return int(s.execute(text(
            "SELECT COUNT(*) FROM payment_attempts a JOIN billing_orders o"
            " ON o.id=a.order_id WHERE o.order_no=:o"), {"o": order_no}).scalar() or 0)
    finally:
        s.close()


def test_checkout_duplicate_one_attempt(monkeypatch):
    support.install_policy(monkeypatch)
    token = support.create_account("owner@t04a.test", tenant="t04a",
                                   billing_role="billing_owner")
    body = {"plan_ref": "PLACEHOLDER", "request_key": "ck-duplicate-1",
            "amount_minor": 1}
    r1 = client.post("/api/billing/checkout", json=body,
                     headers=support.bearer(token))
    r2 = client.post("/api/billing/checkout", json=body,
                     headers=support.bearer(token))
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    d1, d2 = r1.json()["data"], r2.json()["data"]
    assert d1["order_no"] == d2["order_no"]
    assert d1["attempt_id"] == d2["attempt_id"]
    assert _attempt_count(d1["order_no"]) == 1
    # 成功页/订单接口不返回金额，只给占位价
    r3 = client.get("/api/billing/orders/" + d1["order_no"],
                    headers=support.bearer(token))
    assert r3.status_code == 200
    assert r3.json()["data"]["price_display"] == "$***"
    assert "amount_minor" not in r3.json()["data"]


def test_client_amount_ignored(monkeypatch):
    support.install_policy(monkeypatch)
    token = support.create_account("owner@t04b.test", tenant="t04b",
                                   billing_role="billing_owner")
    r = client.post("/api/billing/checkout", json={
        "plan_ref": "PLACEHOLDER", "request_key": "ck-amount-1",
        "amount_minor": 1, "currency": "USD",
    }, headers=support.bearer(token))
    assert r.status_code == 200, r.text
    order_no = r.json()["data"]["order_no"]
    internal = lifecycle.get_order(order_no, "t04b")
    # 服务端 policy 的合成金额（9900 HKD）胜出，客户端 1 USD 被忽略
    assert internal["amount_minor"] == 9900
    assert internal["currency"] == "HKD"
    assert "amount_minor" not in r.json()["data"]
    assert r.json()["data"]["price_display"] == "$***"


def test_unknown_response_queries_before_retry(monkeypatch):
    support.install_policy(monkeypatch)
    calls = {"create": 0, "query": 0}

    def _boom(self, *a, **kw):
        calls["create"] += 1
        raise ProviderUnknown()

    def _query(self, *a, **kw):
        calls["query"] += 1
        return {"status": "unknown"}

    monkeypatch.setattr(FakeAdapter, "create_checkout", _boom)
    monkeypatch.setattr(FakeAdapter, "query_checkout", _query)

    first = lifecycle.create_checkout("t04c", "PLACEHOLDER", "ck-unknown-1")
    assert first["status"] == "unknown" and first["code"] == "PROVIDER_RESULT_UNKNOWN"
    assert calls["create"] == 1

    second = lifecycle.create_checkout("t04c", "PLACEHOLDER", "ck-unknown-1")
    # 未知结果先查询：绝不发起第二次扣款，也不新增 attempt
    assert calls["create"] == 1
    assert calls["query"] >= 1
    assert second["attempt_id"] == first["attempt_id"]
    assert _attempt_count(first["order_no"]) == 1
    assert lifecycle.get_order(first["order_no"], "t04c")["status"] == "created"
