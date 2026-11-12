# -*- coding: utf-8 -*-
"""M04 闭环：向量同步 partial → 补偿任务入 outbox → worker 重试至对称。

Q06 探针形态（delete 抛受控异常 + ensure False）+ 补偿恢复两段。
"""

import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_m04_outbox_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_m04o.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["DURABLE_TASKS_ENABLED"] = "1"
os.environ["DISABLE_BACKGROUND_JOBS"] = "1"
os.environ.setdefault("DEMO_TOKEN_SECRET", "m04o-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, background_tasks  # noqa: E402
import pytest  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()
client = TestClient(app)
_ACCOUNT_EMAIL = {"admin": "admin@demo.hk"}


@pytest.fixture(autouse=True)
def _clean_tasks():
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM durable_tasks"))
        s.commit()
    finally:
        s.close()
    yield


def _hdr():
    return {"Authorization": "Bearer " + auth.issue_token(
        _ACCOUNT_EMAIL["admin"], "admin", "default")}


def _make_memory():
    mid, _ = db.upsert_vendor_memory(vendor="Vendor_A", notes="original", sample="layout",
                                     tenant_id="default", fact_key="alias:Item_A",
                                     fact_row={"fact_kind": "alias", "fact_value": "Item_A"})
    return mid


def _patch_memory(mid):
    return client.patch("/api/memory/" + mid, headers=_hdr(), json={"notes": "updated"})


def _compensation_rows():
    s = db.get_session()
    try:
        return s.execute(text(
            "SELECT id, kind, status, purpose FROM durable_tasks WHERE"
            " kind='memory_vector_sync' ORDER BY id")).fetchall()
    finally:
        s.close()


def test_partial_sync_enqueues_compensation_and_worker_recovers():
    """同步失败 → 200 partial + 补偿任务；worker 重试至对称 → done；不重复补偿。"""
    from app.services import rag
    mid = _make_memory()
    fake = MagicMock()
    fake._collection.delete.side_effect = RuntimeError("QA synthetic delete failure")
    with patch.object(rag, "_store", return_value=fake), \
         patch.object(rag, "ensure_memory_vectors", return_value=False):
        r = _patch_memory(mid)
    body = r.json()
    assert r.status_code == 200 and body.get("status") == "partial", body
    assert body.get("code") == "VECTOR_SYNC_PENDING"
    assert body.get("operation_id")
    assert body["data"]["vector_status"] == "sync_failed"
    rows = _compensation_rows()
    assert len(rows) == 1 and rows[0][2] == "pending"
    op_key = rows[0][3]
    # worker 重试：仍失败（ensure False）→ pending 退避
    from app.services import memory_store as _ms
    with patch.object(rag, "ensure_memory_vectors", return_value=False), \
         patch.object(_ms, "delete_memory_vectors", return_value=0):
        ran = background_tasks.run_once("test-worker")
    assert ran is not None and ran["result"] == "retry_scheduled"
    # 恢复：穿越退避后 ensure True → done
    s2 = db.get_session()
    try:
        s2.execute(text("UPDATE durable_tasks SET next_retry_at='2000-01-01T00:00:00'"))
        s2.commit()
    finally:
        s2.close()
    with patch.object(rag, "ensure_memory_vectors", return_value=True), \
         patch.object(_ms, "delete_memory_vectors", return_value=1):
        ran2 = background_tasks.run_once("test-worker")
    assert ran2 is not None and ran2["result"] == "done"
    assert len(_compensation_rows()) == 1  # 同 operation 幂等，不重复补偿
    # 重复触发同步失败：编辑当前 active 的新 id → 新 operation → 新任务（不同 purpose）
    new_mid1 = body["data"]["new_memory_id"]
    with patch.object(rag, "_store", return_value=fake), \
         patch.object(rag, "ensure_memory_vectors", return_value=False):
        r3 = _patch_memory(new_mid1)
    assert r3.status_code == 200 and r3.json().get("status") == "partial"
    rows2 = _compensation_rows()
    assert len(rows2) == 2 and rows2[0][3] != rows2[1][3]
