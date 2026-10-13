# -*- coding: utf-8 -*-
"""S2-30：店员读引擎配置拿不到明文。不写入、不打印密钥。"""

import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app


def test_staff_engine_config_is_forbidden_and_has_no_secret_material():
    client = TestClient(app)
    resp = client.get("/api/admin/engine-config", headers={"X-Role": "staff"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["status"] == "error" and body["code"] and body["msg"]
    assert "enc:v1" not in resp.text
    assert "api_key" not in resp.text.lower()
