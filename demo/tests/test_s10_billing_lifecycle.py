# -*- coding: utf-8 -*-
"""S10 人工核销全流程集成验证（D06 调整后无渠道沙箱）：

试用 coupon → checkout → 人工登记收款 → 权益生效 → 部分退款 pending 不改权益 →
取消立即生效 → 历史/导出入口不被锁 → 两租户与角色矩阵负向。
零真实扣款/外呼；金额仅测试合成值。
"""

import os
import sys
import tempfile
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

_TMP = tempfile.mkdtemp(prefix="test_s10_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s10.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s10-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
           "OPENAI_REC_API_KEY", "OPENAI_REC_BASE_URL", "OPENAI_REC_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import lifecycle  # noqa: E402
from app.main import app  # noqa: E402
import _billing_support as support  # noqa: E402

db._make_engine()
FIXED = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("AUTH_ENABLED", "1")
    monkeypatch.setenv("IDENTITY_V2_ENABLED", "1")
    monkeypatch.setenv("BILLING_ENFORCEMENT_ENABLED", "1")
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED)
    support.cleanup_billing()
    yield
    support.cleanup_billing()


def _accounts():
    # 计费角色与商户业务角色分离：这里同一账号同时持有两者，才能验“取消后历史仍可读”。
    owner_a = support.create_account("s10-owner-a@test", role="owner", tenant="s10a",
                                     billing_role="billing_owner", tenant_role="owner")
    op_a = support.create_account("s10-op-a@test", role="owner", tenant="s10a",
                                  billing_role="billing_operator", tenant_role="owner")
    owner_b = support.create_account("s10-owner-b@test", role="owner", tenant="s10b",
                                     billing_role="billing_owner", tenant_role="owner")
    staff_a = support.create_account("s10-staff-a@test", role="staff", tenant="s10a",
                                     tenant_role="staff")
    return owner_a, op_a, owner_b, staff_a


def test_manual_lifecycle_two_tenants_and_cancel_keeps_history(monkeypatch):
    support.install_policy(monkeypatch)
    owner_a, op_a, owner_b, staff_a = _accounts()

    # 1) 试用 coupon：服务端 14 天，权益可用
    lifecycle.issue_coupon("S10-COUPON-1", "s10a", created_by="platform")
    r = client.post("/api/billing/trial/redeem", headers=support.bearer(owner_a),
                    json={"coupon_code": "S10-COUPON-1"})
    assert r.status_code == 200, r.text
    ent = client.get("/api/billing/entitlement", headers=support.bearer(owner_a)).json()
    assert ent["allow_new_ai"] is True and ent["basis"] == "active_trial"

    # 2) checkout：客户端伪造金额被忽略；重复 key 只回放
    body = {"plan_ref": "PLACEHOLDER", "request_key": "s10-co-1", "amount_minor": 1}
    r1 = client.post("/api/billing/checkout", headers=support.bearer(owner_a), json=body)
    assert r1.status_code == 200, r1.text
    order_no = r1.json()["data"]["order_no"]
    order = lifecycle.get_order(order_no, "s10a")
    assert order["amount_minor"] == 9900  # 服务端 policy，不是客户端 1
    r2 = client.post("/api/billing/checkout", headers=support.bearer(owner_a), json=body)
    assert r2.status_code == 200 and r2.json()["data"]["order_no"] == order_no
    s = db.get_session()
    try:
        from sqlalchemy import text
        attempts = s.execute(text("SELECT COUNT(*) FROM payment_attempts WHERE order_id=:i"),
                             {"i": order["id"]}).scalar()
        assert attempts == 1
    finally:
        s.close()

    # 3) 人工登记收款（operator）：权益转为已验证周期
    r = client.post("/api/billing/orders/%s/manual-payment" % order_no,
                    headers=support.bearer(op_a),
                    json={"amount_minor": 9900, "currency": "HKD",
                          "request_key": "s10-pay-1", "invoice_no": "INV-S10-1"})
    assert r.status_code == 200, r.text
    ent = client.get("/api/billing/entitlement", headers=support.bearer(owner_a)).json()
    assert ent["allow_new_ai"] is True and ent["basis"] == "verified_period"
    # 同 key 回放、异 key 拒重复登记
    r = client.post("/api/billing/orders/%s/manual-payment" % order_no,
                    headers=support.bearer(op_a),
                    json={"amount_minor": 9900, "currency": "HKD",
                          "request_key": "s10-pay-1", "invoice_no": "INV-S10-1"})
    assert r.status_code == 200 and r.json()["data"]["status"] == "replayed"
    r = client.post("/api/billing/orders/%s/manual-payment" % order_no,
                    headers=support.bearer(op_a),
                    json={"amount_minor": 9900, "currency": "HKD",
                          "request_key": "s10-pay-2", "invoice_no": "INV-S10-2"})
    assert r.status_code == 409

    # 4) 跨租户：他租户查不到订单/不能收款/不能退款
    assert client.get("/api/billing/orders/%s" % order_no,
                      headers=support.bearer(owner_b)).status_code == 404
    assert client.post("/api/billing/orders/%s/manual-payment" % order_no,
                       headers=support.bearer(owner_b),
                       json={"amount_minor": 9900, "currency": "HKD",
                             "request_key": "s10-b-pay"}).status_code == 404
    assert client.post("/api/billing/refunds", headers=support.bearer(owner_b),
                       json={"order_no": order_no, "amount_minor": 100,
                             "request_key": "s10-b-refund"}).status_code == 404

    # 5) 退款权限：staff 无计费能力 403；超已付 409；部分退款 pending 不改权益
    assert client.post("/api/billing/refunds", headers=support.bearer(staff_a),
                       json={"order_no": order_no, "amount_minor": 100,
                             "request_key": "s10-staff-refund"}).status_code == 403
    assert client.post("/api/billing/refunds", headers=support.bearer(owner_a),
                       json={"order_no": order_no, "amount_minor": 9901,
                             "request_key": "s10-over"}).status_code == 409
    r = client.post("/api/billing/refunds", headers=support.bearer(owner_a),
                    json={"order_no": order_no, "amount_minor": 100,
                          "request_key": "s10-refund-1", "reason": "partial"})
    assert r.status_code == 200, r.text
    refund_id = r.json()["data"]["refund_id"]
    ent = client.get("/api/billing/entitlement", headers=support.bearer(owner_a)).json()
    assert ent["allow_new_ai"] is True  # pending 不产生最终权益修正
    r = client.post("/api/billing/refunds/%s/approve" % refund_id,
                    headers=support.bearer(op_a))
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "succeeded"

    # 6) 取消立即生效；历史/导出入口不被锁
    r = client.post("/api/billing/subscription/cancel", headers=support.bearer(owner_a),
                    json={"request_key": "s10-cancel"})
    assert r.status_code == 200 and r.json()["data"]["state"] == "terminated"
    ent = client.get("/api/billing/entitlement", headers=support.bearer(owner_a)).json()
    assert ent["allow_new_ai"] is False
    assert client.get("/api/receipts", headers=support.bearer(owner_a)).status_code == 200
    assert client.get("/api/export/reports", headers=support.bearer(owner_a)).status_code == 200
