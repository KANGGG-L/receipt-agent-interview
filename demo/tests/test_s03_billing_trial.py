# -*- coding: utf-8 -*-
"""S03 14 天试用与服务端权益（C-BILL：trial/relogin、AI-only 门、inflight 跨到期）。"""

import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_s03_billing_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_s03.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ.setdefault("DEMO_TOKEN_SECRET", "s03-test-secret-not-the-default-value")
os.environ.pop("BILLING_POLICY_JSON", None)
for _k in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
    os.environ.pop(_k, None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, "..")), _TESTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402
from app.billing import entitlements, lifecycle  # noqa: E402
from app.billing.errors import BillingConflict  # noqa: E402
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


def test_trial_14d_server_clock_and_relogin(monkeypatch):
    support.install_policy(monkeypatch)
    support.create_account("trial@t03.test", tenant="t03a", tenant_role="staff")
    lifecycle.issue_coupon("TRIAL-T03-1", "t03a", created_by="platform")

    result = lifecycle.redeem_trial_coupon("t03a", "TRIAL-T03-1", actor="trial@t03.test")
    assert result["trial_days"] == 14
    assert result["trial_started_at"] == "2026-10-26T00:00:00+00:00"
    # 服务端时钟：恰好 14 天后到期，不受客户端时间影响
    assert result["trial_ends_at"] == "2026-11-09T00:00:00+00:00"
    assert entitlements.allow_new_ai("t03a") is True

    # 重登后换一张 coupon 也不能重复领取
    lifecycle.issue_coupon("TRIAL-T03-2", "t03a", created_by="platform")
    with pytest.raises(BillingConflict) as ei:
        lifecycle.redeem_trial_coupon("t03a", "TRIAL-T03-2", actor="trial@t03.test")
    assert ei.value.code == "TRIAL_ALREADY_USED"

    # 同一 coupon 再兑换：一次性
    with pytest.raises(BillingConflict) as ei2:
        lifecycle.redeem_trial_coupon("t03a", "TRIAL-T03-1", actor="trial@t03.test")
    assert ei2.value.code == "COUPON_ALREADY_REDEEMED"

    monkeypatch.setattr(lifecycle, "utcnow",
                        lambda: FIXED + timedelta(days=14, seconds=1))
    lifecycle.refresh_state("t03a")
    assert lifecycle.get_subscription("t03a")["state"] == "trial_expired"
    assert entitlements.evaluate("t03a")["allow_new_ai"] is False


def test_ai_only_gate_keeps_manual_history_export(monkeypatch):
    support.install_policy(monkeypatch)
    spec = support.create_account("boss@t03b.test", role="owner", tenant="t03b",
                                  tenant_role="owner")
    staff = support.create_account("staff@t03b.test", tenant="t03b",
                                   tenant_role="staff")
    lifecycle.issue_coupon("TRIAL-T03-3", "t03b")
    lifecycle.redeem_trial_coupon("t03b", "TRIAL-T03-3")
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=15))
    lifecycle.refresh_state("t03b")
    assert entitlements.allow_new_ai("t03b") is False

    # 新 AI 入口：上传被 402 人话拒绝（不落图、不建任务）
    r = client.post("/api/upload",
                    files=[("receipt", ("x.jpg", b"not-an-image", "image/jpeg"))],
                    headers=support.bearer(staff))
    assert r.status_code == 402, r.text
    body = r.json()
    assert body["code"] == "AI_ENTITLEMENT_REQUIRED"
    assert "手工" in body["msg"] and "历史" in body["msg"]

    # 手工路径不受限：历史列表、转手工、导出报表仍可用
    rid = db.create_receipt(status="error", tenant_id="t03b")
    assert client.get("/api/receipts", headers=support.bearer(staff)).status_code == 200
    assert client.post(f"/api/receipt/{rid}/convert_manual",
                       headers=support.bearer(staff)).status_code == 200
    assert client.get("/api/export/reports",
                      headers=support.bearer(spec)).status_code == 200


def test_inflight_expiry_complete(monkeypatch):
    support.install_policy(monkeypatch)
    lifecycle.issue_coupon("TRIAL-T03-4", "t03c")
    lifecycle.redeem_trial_coupon("t03c", "TRIAL-T03-4")

    # 试用期内受理的新任务建立预留（带权益快照）
    first = entitlements.authorize_new_ai("t03c", "req-inflight-1", "hash-1")
    assert first["allow_new_ai"] is True and first["state"] == "reserved"

    # 到期后：同 key 回放视为 inflight 继续；新 key 一律拒绝
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=15))
    replay = entitlements.authorize_new_ai("t03c", "req-inflight-1", "hash-1")
    assert replay["allow_new_ai"] is True and replay.get("inflight") is True
    assert entitlements.consume("t03c", "req-inflight-1") is True
    assert entitlements.get_reservation("t03c", "req-inflight-1")["state"] == "consumed"

    denied = entitlements.authorize_new_ai("t03c", "req-inflight-2", "hash-2")
    assert denied["allow_new_ai"] is False
    assert entitlements.get_reservation("t03c", "req-inflight-2") is None

    # 已释放（转手工）的 key 不享受 inflight：到期后重试仍拒绝
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED)
    lifecycle.issue_coupon("TRIAL-T03-5", "t03d")
    lifecycle.redeem_trial_coupon("t03d", "TRIAL-T03-5")
    assert entitlements.authorize_new_ai("t03d", "req-release")["allow_new_ai"] is True
    assert entitlements.release("t03d", "req-release") is True
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=15))
    assert entitlements.authorize_new_ai("t03d", "req-release")["allow_new_ai"] is False


def test_trial_concurrent_double_coupon_only_one(monkeypatch):
    support.install_policy(monkeypatch)
    lifecycle.issue_coupon("TRIAL-CONC-1", "t03e")
    lifecycle.issue_coupon("TRIAL-CONC-2", "t03e")
    barrier = threading.Barrier(2)
    results = []

    def _claim(code):
        barrier.wait()
        try:
            results.append(("ok", lifecycle.redeem_trial_coupon("t03e", code)))
        except BillingConflict as exc:
            results.append(("conflict", exc.code))

    threads = [threading.Thread(target=_claim, args=(c,))
               for c in ("TRIAL-CONC-1", "TRIAL-CONC-2")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 2
    assert [r[0] for r in results].count("ok") == 1, results
    assert ("conflict", "TRIAL_ALREADY_USED") in results, results
    s = db.get_session()
    try:
        assert s.execute(text("SELECT COUNT(*) FROM subscriptions")).scalar() == 1
        assert s.execute(text(
            "SELECT COUNT(*) FROM subscription_periods WHERE kind='trial'")).scalar() == 1
    finally:
        s.close()


def test_upload_rejected_releases_reservation(monkeypatch):
    support.install_policy(monkeypatch)
    lifecycle.issue_coupon("TRIAL-REL-1", "t03f")
    lifecycle.redeem_trial_coupon("t03f", "TRIAL-REL-1")
    staff = support.create_account("staff@t03f.test", tenant="t03f",
                                   tenant_role="staff")
    r = client.post("/api/upload",
                    files=[("receipt", ("bad.jpg", b"not-an-image", "image/jpeg"))],
                    data={"request_key": "upload-reject-1"},
                    headers=support.bearer(staff))
    assert r.status_code == 400, r.text
    reservation = entitlements.get_reservation("t03f", "upload-reject-1")
    assert reservation is not None and reservation["state"] == "released"


def test_replace_image_gated_and_releases(monkeypatch):
    support.install_policy(monkeypatch)
    staff_expired = support.create_account("staff@t03g.test", tenant="t03g",
                                           tenant_role="staff")
    lifecycle.issue_coupon("TRIAL-REPL-1", "t03g")
    lifecycle.redeem_trial_coupon("t03g", "TRIAL-REPL-1")
    monkeypatch.setattr(lifecycle, "utcnow", lambda: FIXED + timedelta(days=15))
    lifecycle.refresh_state("t03g")
    rid = db.create_receipt(status="error", tenant_id="t03g")
    r = client.post(f"/api/receipt/{rid}/replace-image",
                    files={"receipt": ("new.jpg", b"not-an-image", "image/jpeg")},
                    data={"force": "true"}, headers=support.bearer(staff_expired))
    assert r.status_code == 402, r.text          # 到期后换图不能绕过权益门

    # 试用有效时，坏图拒绝也必须释放换图预留
    staff_active = support.create_account("staff@t03h.test", tenant="t03h",
                                          tenant_role="staff")
    lifecycle.issue_coupon("TRIAL-REPL-2", "t03h")
    lifecycle.redeem_trial_coupon("t03h", "TRIAL-REPL-2")
    rid2 = db.create_receipt(status="error", tenant_id="t03h")
    r2 = client.post(f"/api/receipt/{rid2}/replace-image",
                     files={"receipt": ("bad.jpg", b"still-not-an-image", "image/jpeg")},
                     data={"force": "true"}, headers=support.bearer(staff_active))
    assert r2.status_code == 400, r2.text
    reservation = entitlements.get_reservation(
        "t03h", "replace:%s:1" % rid2)
    assert reservation is not None and reservation["state"] == "released"
