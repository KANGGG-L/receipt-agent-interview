# -*- coding: utf-8 -*-
"""S1-P2-06 / S2-06：同步上传还卡在保存文件时，健康检查仍能返回。"""

import os
import sys
import threading

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import api_receipts
from app.main import app

STAFF = {"X-Role": "staff"}


def test_health_returns_while_sync_upload_is_still_blocked(monkeypatch, tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocked(_file):
        started.set()
        assert release.wait(5)
        dest = tmp_path / "up.jpg"
        dest.write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
        return str(dest)

    monkeypatch.setattr(api_receipts, "_save_upload", blocked)
    client = TestClient(app)
    holder = {}

    def upload():
        holder["resp"] = client.post(
            "/api/upload",
            headers=STAFF,
            files={"receipt": ("a.jpg", b"\xff\xd8\xff" + b"\x00" * 80, "image/jpeg")},
        )

    worker = threading.Thread(target=upload)
    worker.start()
    try:
        assert started.wait(3)
        assert worker.is_alive()
        health = client.get("/api/health")
        assert worker.is_alive()
        assert health.status_code == 200
        assert health.json().get("status") in ("ok", "degraded")
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
