# -*- coding: utf-8 -*-
"""W-SEC-6 安全可观测性与审计看板单元测试。

验证：
1. record_security_event / log_security_event 持久化入库与敏感凭据自动脱敏。
2. 六大核心安全事件类型全部可记录并正确入库。
3. GET /api/admin/security-events 分页与多维度过滤 (tenant_id, event_type, severity)。
4. GET /api/admin/security-stats 聚合指标计算 (总数、分类聚合、租户聚合、24h高危趋势)。
5. RBAC 鉴权保护（非 admin 请求拦截）。
"""

import json
import os
import sys
import pytest
from starlette.testclient import TestClient

DEMO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "demo"))
if DEMO_DIR not in sys.path:
    sys.path.insert(0, DEMO_DIR)

from app import db
from app.main import app
from app.services.security_guard import log_security_event, mask_secret_key

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_sec_obs.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_db_path
    db._make_engine()


def test_record_all_six_security_event_types():
    """验证六大核心安全事件类型均可写入数据库且凭据敏感字段自动脱敏。"""
    event_types = [
        ("PROMPT_INJECTION_DETECTED", "HIGH", "prompt_guard", {"payload": "drop table", "api_key": "sk-secret12345678"}),
        ("IMAGE_GUARD_FLAGGED", "MEDIUM", "image_guard", {"reasons": ["faint_ink"]}),
        ("CANARY_VERIFICATION_FAILED", "CRITICAL", "canary_guard", {"token": "canary_abc_123"}),
        ("SSRF_ATTEMPT_BLOCKED", "HIGH", "security_guard", {"target_ip": "169.254.169.254"}),
        ("OUTBOUND_PII_MASKED", "LOW", "rag", {"masked_count": 2}),
        ("TENANT_VIOLATION_BLOCKED", "CRITICAL", "db_isolation", {"attempted_tenant": "tenant_b"}),
    ]

    for etype, sev, mod, details in event_types:
        evt = log_security_event(
            event_type=etype,
            severity=sev,
            source_module=mod,
            details=details,
            tenant_id="tenant_alpha",
            ip_address="192.0.2.1",
            user_email="auditor@example.com"
        )
        assert evt is not None
        assert evt["event_type"] == etype
        assert evt["severity"] == sev
        assert evt["tenant_id"] == "tenant_alpha"

    # 验证敏感字段已脱敏
    events = db.list_security_events(tenant_id="tenant_alpha")
    assert len(events) == 6
    inj_event = next(e for e in events if e["event_type"] == "PROMPT_INJECTION_DETECTED")
    assert inj_event["details"]["api_key"] == "sk-sec****5678"
    assert "sk-secret12345678" not in json.dumps(inj_event)


def test_list_security_events_endpoint_and_filtering():
    """测试 GET /api/admin/security-events 端点及过滤能力。"""
    log_security_event("PROMPT_INJECTION_DETECTED", "HIGH", "guard", {}, tenant_id="tenant_a")
    log_security_event("SSRF_ATTEMPT_BLOCKED", "CRITICAL", "guard", {}, tenant_id="tenant_a")
    log_security_event("IMAGE_GUARD_FLAGGED", "LOW", "guard", {}, tenant_id="tenant_b")

    # 1. 匿名访问 -> 401
    resp_anon = client.get("/api/admin/security-events")
    assert resp_anon.status_code == 401

    # 2. Staff 访问 -> 403
    resp_staff = client.get("/api/admin/security-events", headers={"X-Role": "staff"})
    assert resp_staff.status_code == 403

    # 3. Admin 全量查询
    resp_all = client.get("/api/admin/security-events?tenant_id=all", headers={"X-Role": "admin"})
    assert resp_all.status_code == 200
    data_all = resp_all.json()
    assert data_all["status"] == "success"
    assert data_all["total"] == 3

    # 4. 按 tenant 过滤
    resp_ta = client.get("/api/admin/security-events?tenant_id=tenant_a", headers={"X-Role": "admin"})
    assert resp_ta.status_code == 200
    assert len(resp_ta.json()["data"]) == 2

    # 5. 按 event_type 过滤
    resp_type = client.get("/api/admin/security-events?tenant_id=all&event_type=SSRF_ATTEMPT_BLOCKED",
                           headers={"X-Role": "admin"})
    assert resp_type.status_code == 200
    assert len(resp_type.json()["data"]) == 1
    assert resp_type.json()["data"][0]["event_type"] == "SSRF_ATTEMPT_BLOCKED"

    # 6. 按 severity 过滤
    resp_sev = client.get("/api/admin/security-events?tenant_id=all&severity=CRITICAL",
                          headers={"X-Role": "admin"})
    assert resp_sev.status_code == 200
    assert len(resp_sev.json()["data"]) == 1


def test_security_stats_endpoint():
    """测试 GET /api/admin/security-stats 统计聚合大屏。"""
    log_security_event("CANARY_VERIFICATION_FAILED", "CRITICAL", "canary", {}, tenant_id="tenant_1")
    log_security_event("SSRF_ATTEMPT_BLOCKED", "HIGH", "net", {}, tenant_id="tenant_1")
    log_security_event("OUTBOUND_PII_MASKED", "LOW", "rag", {}, tenant_id="tenant_2")

    resp = client.get("/api/admin/security-stats?tenant_id=all", headers={"X-Role": "admin"})
    assert resp.status_code == 200
    stats = resp.json()["data"]

    assert stats["total_events"] == 3
    assert stats["by_type"]["CANARY_VERIFICATION_FAILED"] == 1
    assert stats["by_type"]["SSRF_ATTEMPT_BLOCKED"] == 1
    assert stats["by_type"]["OUTBOUND_PII_MASKED"] == 1
    assert stats["by_severity"]["CRITICAL"] == 1
    assert stats["by_severity"]["HIGH"] == 1
    assert stats["by_severity"]["LOW"] == 1
    assert stats["by_tenant"]["tenant_1"] == 2
    assert stats["by_tenant"]["tenant_2"] == 1
    assert stats["recent_24h_high_risk"] == 2  # CRITICAL + HIGH
