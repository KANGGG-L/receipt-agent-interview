# -*- coding: utf-8 -*-
"""Unit and integration tests for Unified Admin Config and Single-Leg Probe."""

import os
import pytest
from starlette.testclient import TestClient
from app.main import app
from app import db
from app.models import EngineConfig

client = TestClient(app)


def _admin_headers():
    return {"X-Role": "admin", "X-Email": "admin@demo.hk"}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_unified_isolated.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def test_probe_single_engine_invalid_key():
    """AC-06: probe_single_engine rejects placeholder or empty key."""
    res = client.post(
        "/api/admin/probe-single-engine",
        headers=_admin_headers(),
        json={
            "leg_type": "rec",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key": "YOUR_API_KEY",
            "model_name": "agnes-2.0-flash",
        }
    )
    assert res.status_code == 400
    data = res.json()
    assert data["status"] == "error"
    assert "占位符" in data["msg"] or "无效" in data["msg"]


def test_probe_single_engine_ssrf_intercept():
    """AC-06: probe_single_engine intercepts dangerous local/SSRF URLs."""
    res = client.post(
        "/api/admin/probe-single-engine",
        headers=_admin_headers(),
        json={
            "leg_type": "rec",
            "base_url": "http://169.254.169.254/latest/meta-data",
            "api_key": "sk-realapikey12345678",
            "model_name": "agnes-2.0-flash",
        }
    )
    assert res.status_code == 400
    data = res.json()
    assert data["status"] == "error"
    assert "SSRF" in data["msg"]


def test_unified_config_optimistic_lock():
    """AC-04: Unified config enforces optimistic concurrency control (409 on version conflict)."""
    cfg = db.get_engine_config()
    current_version = int(getattr(cfg, "version", 1) or 1)
    wrong_version = current_version + 999

    res = client.put(
        "/api/admin/unified-config",
        headers=_admin_headers(),
        json={
            "engine_config": {
                "version": wrong_version,
                "recognition_model": "agnes-2.0-flash",
            },
            "settings": {
                "parsing_timeout_seconds": 360
            }
        }
    )
    assert res.status_code == 409
    data = res.json()
    assert data["code"] == "VERSION_CONFLICT"
    assert data["status"] == "error"


def test_unified_config_atomic_save_and_rollback():
    """AC-01/AC-04: Unified config saves both engine and settings atomically in one session."""
    cfg = db.get_engine_config()
    current_version = int(getattr(cfg, "version", 1) or 1)

    # 1. Unknown settings rejection with zero DB mutation
    bad_res = client.put(
        "/api/admin/unified-config",
        headers=_admin_headers(),
        json={
            "engine_config": {
                "version": current_version,
                "recognition_model": "test-should-not-persist",
            },
            "settings": {
                "non_existent_key_xyz": 123
            }
        }
    )
    assert bad_res.status_code == 400
    assert "未知配置项" in bad_res.json()["msg"]

    cfg_after_bad = db.get_engine_config()
    assert int(getattr(cfg_after_bad, "version", 1) or 1) == current_version
    assert cfg_after_bad.recognition_model != "test-should-not-persist"

    # 2. Valid atomic save
    valid_res = client.put(
        "/api/admin/unified-config",
        headers=_admin_headers(),
        json={
            "engine_config": {
                "version": current_version,
                "price_input_rec": 2.5000,
                "price_cached_input_rec": 0.5000,
                "price_output_rec": 12.0000,
            },
            "settings": {
                "parsing_timeout_seconds": 320,
                "price_anomaly_threshold_pct": 25.5
            }
        }
    )
    assert valid_res.status_code == 200
    data = valid_res.json()
    assert data["status"] == "success"
    next_v = data["version"]
    assert next_v == current_version + 1

    # Verify both engine_config and app_settings were updated in SQLite
    cfg_persisted = db.get_engine_config()
    assert cfg_persisted.version == next_v
    assert cfg_persisted.price_input_rec == 2.5000
    assert cfg_persisted.price_cached_input_rec == 0.5000
    assert cfg_persisted.price_output_rec == 12.0000

    from app.services import settings_service
    assert settings_service.get("parsing_timeout_seconds") == 320
    assert abs(settings_service.get("price_anomaly_threshold_pct") - 25.5) < 1e-4


def test_legacy_endpoints_backward_compatibility():
    """Verify legacy PUT /api/admin/engine-config and PUT /api/admin/settings remain working."""
    cfg = db.get_engine_config()
    v = int(getattr(cfg, "version", 1) or 1)

    res_eng = client.put(
        "/api/admin/engine-config",
        headers=_admin_headers(),
        json={"version": v, "price_input_rec": 1.8888}
    )
    assert res_eng.status_code == 200

    res_set = client.put(
        "/api/admin/settings",
        headers=_admin_headers(),
        json={"parsing_timeout_seconds": 299}
    )
    assert res_set.status_code == 200


def test_unified_config_reject_negative_pricing_422():
    """AC-01/AC-05: Pydantic rejects negative pricing parameters with HTTP 422."""
    cfg = db.get_engine_config()
    current_version = int(getattr(cfg, "version", 1) or 1)

    # 1. Negative price_input_rec
    res = client.put(
        "/api/admin/unified-config",
        headers=_admin_headers(),
        json={
            "engine_config": {
                "version": current_version,
                "price_input_rec": -1.5,
            },
            "settings": {}
        }
    )
    assert res.status_code == 422
    err_body = res.json()
    assert any("greater_than_equal" in str(e) or "ge" in str(e) for e in err_body.get("detail", []))

    # 2. Negative price_cached_input_aud
    res_aud = client.put(
        "/api/admin/unified-config",
        headers=_admin_headers(),
        json={
            "engine_config": {
                "version": current_version,
                "price_cached_input_aud": -0.001,
            },
            "settings": {}
        }
    )
    assert res_aud.status_code == 422

    # Verify zero database modification
    cfg_persisted = db.get_engine_config()
    assert int(getattr(cfg_persisted, "version", 1) or 1) == current_version


def test_unified_config_ssrf_intercept_400():
    """AC-04/AC-06: Unified config validates external Base URLs and intercepts SSRF attempts."""
    cfg = db.get_engine_config()
    current_version = int(getattr(cfg, "version", 1) or 1)

    dangerous_urls = [
        "http://169.254.169.254/latest/meta-data",
        "http://127.0.0.1:8000/internal",
        "http://localhost:15010/api",
        "http://10.0.0.1/admin",
    ]

    for d_url in dangerous_urls:
        res = client.put(
            "/api/admin/unified-config",
            headers=_admin_headers(),
            json={
                "engine_config": {
                    "version": current_version,
                    "openai_rec_base_url": d_url,
                },
                "settings": {
                    "parsing_timeout_seconds": 300
                }
            }
        )
        assert res.status_code == 400
        data = res.json()
        assert data["status"] == "error"
        assert "安全校验未通过" in data["msg"] or "SSRF" in data["msg"]

    # Verify zero database modification
    cfg_persisted = db.get_engine_config()
    assert int(getattr(cfg_persisted, "version", 1) or 1) == current_version


def test_probe_single_engine_timeout_simulation():
    """AC-06: Probe proxy handles connection/gateway timeout gracefully without blocking ASGI."""
    from unittest.mock import patch
    import httpx

    with patch("httpx.AsyncClient.post", side_effect=httpx.TimeoutException("Mocked connection timeout")):
        res = client.post(
            "/api/admin/probe-single-engine",
            headers=_admin_headers(),
            json={
                "leg_type": "rec",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-mocked-key-for-probe-12345",
                "model_name": "agnes-2.0-flash",
                "timeout_seconds": 8.0,
            }
        )
        assert res.status_code == 400
        data = res.json()
        assert data["status"] == "error"
        assert "连接超时" in data["msg"]
        assert "8.0s 内未响应" in data["msg"]


def test_probe_single_engine_masked_key_resolution_and_headers():
    """AC-06/AC-09: Probe automatically resolves masked keys from DB and sends Bearer auth."""
    from unittest.mock import patch, MagicMock

    # Seed the DB with a known key for recognition engine
    cfg = db.get_engine_config()
    updates = cfg.model_dump()
    updates["openai_rec_api_key"] = "sk-live-db-stored-secret-987654321"
    db.set_engine_config(EngineConfig(**updates), source="manual")

    captured_headers = {}

    async def fake_post(self, url, json=None, headers=None, **kwargs):
        nonlocal captured_headers
        captured_headers = headers
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"choices": [{"message": {"content": "pong"}}]}
        return mock_resp

    with patch("httpx.AsyncClient.post", new=fake_post):
        # 1. Test with masked key sk-****
        res = client.post(
            "/api/admin/probe-single-engine",
            headers=_admin_headers(),
            json={
                "leg_type": "rec",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-****••••••••",
                "model_name": "agnes-2.0-flash",
            }
        )
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "探测正常" in data["msg"]
        assert captured_headers.get("Authorization") == "Bearer sk-live-db-stored-secret-987654321"

        # 2. Test with missing model_name
        res_no_model = client.post(
            "/api/admin/probe-single-engine",
            headers=_admin_headers(),
            json={
                "leg_type": "rec",
                "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "sk-****••••••••",
                "model_name": "",
            }
        )
        assert res_no_model.status_code == 400
        assert "缺少待测模型标识符" in res_no_model.json()["msg"]

