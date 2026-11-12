# -*- coding: utf-8 -*-
"""M05 终审补强：operations API（process reset / 读写失败计数 / 持久 sync backlog /
owner-technical 视图分离 / v3 no_data 三态）。零外呼，临时库。"""

import os
import sys
import tempfile

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_m05_ops_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_m05.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["DURABLE_TASKS_ENABLED"] = "1"
os.environ["DISABLE_BACKGROUND_JOBS"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "m05-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, task_store  # noqa: E402
from app.main import app  # noqa: E402
from app.services import rag, memory_store  # noqa: E402

db._make_engine()
client = TestClient(app)
_EMAIL = {"admin": "admin@demo.hk", "owner": "boss@demo.hk", "staff": "staff@demo.hk"}


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_EMAIL[role], role, "default")}


@pytest.fixture(autouse=True)
def _clean():
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM durable_tasks"))
        s.execute(text("DELETE FROM pending_memory"))
        s.execute(text("DELETE FROM receipt_items"))
        s.execute(text("DELETE FROM receipts"))
        s.commit()
    finally:
        s.close()
    rag.reset_retrieve_failure_counts()
    memory_store.reset_write_failure_counts()
    yield


def test_metrics_v3_consumed_and_no_data():
    """v3 结构必须原样透出；空库分母=0 时 rate=null+原因，前端不得渲染 0%。"""
    r = client.get("/api/admin/memory-metrics", headers=_hdr("admin"))
    assert r.status_code == 200
    body = r.json()
    v3 = (body.get("metrics") or {}).get("memory_metrics_v2") or {}
    assert v3.get("schema_version") == "v3"
    prod = (v3.get("injection_rate") or {}).get("production") or {}
    assert prod.get("rate_pct") is None, prod
    assert prod.get("unknown_reason"), prod
    assert "coverage" in v3 and "effect_ab" in v3
    eff = (body.get("metrics") or {}).get("memory_hit_effective") or {}
    assert eff.get("no_data") is True and eff.get("effective_rate_pct") is None


def test_sync_failed_retry_role():
    """持久 sync backlog 三态；死信错误进技术视图；owner/technical 角色互不可见。"""
    # 技术视图：无数据态（不是 0）
    tech = client.get("/api/admin/operations/technical", headers=_hdr("admin"))
    assert tech.status_code == 200
    t1 = tech.json()
    assert t1["sync_backlog"]["state"] == "no_data"
    assert t1["sync_backlog"]["stats"]["total"] == 0
    assert t1["process"]["counter_scope"] == "process"

    # 写入一条 pending sync backlog，技术视图变 ok 且持久可读
    tid = task_store.enqueue("memory_vector_sync", "101", 1, "sync", tenant_id="default")
    t2 = client.get("/api/admin/operations/technical", headers=_hdr("admin")).json()
    assert t2["sync_backlog"]["state"] == "ok"
    assert t2["sync_backlog"]["stats"]["pending"] == 1
    assert t2["sync_backlog"]["persistent"] is True

    # 失败转到死信：错误与重试次数在技术视图可见
    task_store.claim_next("w")
    assert task_store.fail(tid, error="synthetic vector failure", max_attempts=0) == "dead_letter"
    t3 = client.get("/api/admin/operations/technical", headers=_hdr("admin")).json()
    assert t3["sync_backlog"]["stats"]["dead_letter"] == 1
    assert t3["errors"]["state"] == "ok"
    assert t3["errors"]["dead_letter"][0]["last_error"].startswith("synthetic vector failure")
    assert t3["errors"]["retry_limit"] == 5

    # owner 视图：只看商业记忆候选/编辑
    db.enqueue_pending_memory(vendor="Vendor_A", content="alias:Item_A", tenant_id="default")
    own = client.get("/api/admin/operations/owner", headers=_hdr("owner"))
    assert own.status_code == 200
    o = own.json()
    assert o["role"] == "owner"
    assert o["memory_candidates"]["state"] == "ok"
    assert o["memory_candidates"]["items"][0]["vendor"] == "Vendor_A"
    assert o["edits"]["state"] == "no_data"
    assert "read_failures" not in o and "sync_backlog" not in o

    # 角色分离：不能互看
    assert client.get("/api/admin/operations/technical", headers=_hdr("owner")).status_code == 403
    assert client.get("/api/admin/operations/owner", headers=_hdr("admin")).status_code == 403
    assert client.get("/api/admin/operations/owner", headers=_hdr("staff")).status_code == 403


def test_process_reset_and_write_failure_counts():
    """process reset 清零读/写失败计数并记录重启基线；写失败计数在技术视图可见。"""
    rag._note_retrieve_failure("exact", RuntimeError("synthetic read"))
    memory_store._note_write_failure("ingest", RuntimeError("synthetic write"))
    before = client.get("/api/admin/operations/technical", headers=_hdr("admin")).json()
    assert before["read_failures"]["state"] == "ok" and before["read_failures"]["total"] == 1
    assert before["write_failures"]["state"] == "ok" and before["write_failures"]["total"] == 1

    reset = client.post("/api/admin/operations/process-reset", headers=_hdr("admin"))
    assert reset.status_code == 200
    data = reset.json()["data"]
    assert data["process"]["baseline_reset_at"]
    assert data["read_failures"]["state"] == "no_data" and data["read_failures"]["total"] == 0
    assert data["write_failures"]["state"] == "no_data" and data["write_failures"]["total"] == 0

    after = client.get("/api/admin/operations/technical", headers=_hdr("admin")).json()
    assert after["write_failures"]["total"] == 0
    assert after["process"]["restart_resets_counters"] is True
    assert client.post("/api/admin/operations/process-reset",
                       headers=_hdr("owner")).status_code == 403
