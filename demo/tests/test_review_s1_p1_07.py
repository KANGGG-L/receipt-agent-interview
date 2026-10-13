# -*- coding: utf-8 -*-
"""S1-P1-07：其它租户读取 Job 得到 404，响应里没有明细和图片地址。"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app.main import app
from app.services import receipt_utils as ru

client = TestClient(app)
STAFF = {"X-Role": "staff"}


def test_other_tenant_job_404_has_no_lines_or_image():
    jid = "b" * 32
    with ru.JOBS_LOCK:
        ru.JOBS[jid] = {
            "job_id": jid,
            "job_status": "done",
            "receipt_id": 1,
            "tenant_id": "tenant_a",
            "started_ts": time.time(),
            "image_url": "/api/receipt/1/image",
            "items": [{"name": "菜心", "qty": 1}],
        }
    try:
        other = client.get("/api/job/%s" % jid, headers={**STAFF, "X-Tenant-Id": "tenant_b"})
    finally:
        with ru.JOBS_LOCK:
            ru.JOBS.pop(jid, None)
    assert other.status_code == 404
    body = other.json()
    assert body["status"] == "error"
    assert body["code"] == "JOB_NOT_FOUND"
    assert "image_url" not in body
    assert "items" not in body
    assert "image_path" not in body
    assert "uploads/" not in other.text
    assert "菜心" not in other.text
