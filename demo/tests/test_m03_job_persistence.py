# -*- coding: utf-8 -*-
"""M03 终审补强：识别 Job 持久终态 / 重启恢复 / lease 崩溃 / 同版本只抽一次。

全部离线：假 run_pipeline（零外呼/零真实 DB 读取），真实 DB 隔离库。
"""

import os
import sys
import tempfile
import time

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_m03_job_persist_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_m03_jobs.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "0"
os.environ["DURABLE_TASKS_ENABLED"] = "1"
os.environ["DISABLE_BACKGROUND_JOBS"] = "1"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db, task_store, background_tasks, maintenance  # noqa: E402
from app.chains import supervisor  # noqa: E402
from app.services import receipt_utils  # noqa: E402

db._make_engine()


@pytest.fixture(autouse=True)
def _clean():
    with receipt_utils.JOBS_LOCK:
        receipt_utils.JOBS.clear()
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM durable_tasks"))
        s.execute(text("DELETE FROM recognition_jobs"))
        s.commit()
    finally:
        s.close()
    yield
    with receipt_utils.JOBS_LOCK:
        receipt_utils.JOBS.clear()


def _wait_terminal(job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = receipt_utils.get_job(job_id)
        if job and job.get("job_status") in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError("Job 未收口: %s" % (receipt_utils.get_job(job_id),))


def _job_row(job_id):
    s = db.get_session()
    try:
        return s.execute(text(
            "SELECT status, attempt, created_at, started_at FROM recognition_jobs"
            " WHERE job_id=:j"), {"j": job_id}).fetchone()
    finally:
        s.close()


def test_job_restart_terminal_preserved(monkeypatch):
    """终态落库：清空内存（模拟重启）后仍是 failed，不回到 pending；
    超时清洗 receipt 也不得改写 Job 的 created_at/started_at（原起点）。"""
    def _fake_fail(*args, **kwargs):
        return {"data": None, "error": "synthetic", "user_error": "合成识别失败",
                "error_code": "SYNTHETIC"}

    monkeypatch.setattr(supervisor, "run_pipeline", _fake_fail)
    job_id, rid = receipt_utils.start_recognition_job(
        os.path.join(_TMP, "synthetic.jpg"), vendor_hint="Vendor_A",
        tenant_id="default")
    job = _wait_terminal(job_id)
    assert job["job_status"] == "error"

    row = _job_row(job_id)
    assert row is not None and row[0] == "failed", row
    created_at, started_at = row[2], row[3]

    with receipt_utils.JOBS_LOCK:
        receipt_utils.JOBS.clear()  # 进程重启
    restored = receipt_utils.get_job(job_id)
    assert restored is not None and restored["job_status"] == "error", restored
    assert _job_row(job_id)[0] == "failed"  # 终态不被洗回 pending

    # 超时清洗：先让单据处于超期 parsing，再 sweep；Job 原起点必须一字不动
    s = db.get_session()
    try:
        s.execute(text("UPDATE receipts SET status='parsing',"
                       " created_at='2000-01-01T00:00:00' WHERE id=:i"), {"i": rid})
        s.commit()
    finally:
        s.close()
    assert maintenance.sweep_parsing() == [rid]
    row2 = _job_row(job_id)
    assert (row2[2], row2[3]) == (created_at, started_at), row2
    assert row2[0] == "failed"


def test_worker_crash_lease_recover():
    """worker 崩溃（lease 过期）→ 另一 worker 可重新认领且 attempt+1；正常 lease 内互斥。"""
    tid = task_store.enqueue("memory_extract", "8801", 1, "extract", tenant_id="default")
    first = task_store.claim_next("worker-a")
    assert first is not None and first["id"] == tid and first["attempt"] == 1
    assert task_store.claim_next("worker-b") is None  # lease 内重复 claim 幂等拒绝

    s = db.get_session()
    try:
        s.execute(text("UPDATE durable_tasks SET lease_expires_at='2000-01-01T00:00:00'"
                       " WHERE id=:i"), {"i": tid})
        s.commit()
    finally:
        s.close()

    recovered = task_store.claim_next("worker-b")
    assert recovered is not None and recovered["id"] == tid
    assert recovered["attempt"] == 2  # 崩溃恢复：同一任务继续，不新建
    task_store.complete(tid)
    assert task_store.claim_next("worker-b") is None
    assert len(task_store.stats()) and task_store.stats().get("done") == 1


def test_extract_once_per_source_version():
    """同 (receipt, version) 重复入队/重复 save 只产生一个任务，旧版本完成不重抽。"""
    t1 = task_store.enqueue("memory_extract", "9901", 7, "extract", tenant_id="default")
    t2 = task_store.enqueue("memory_extract", "9901", 7, "extract", tenant_id="default")
    assert t1 == t2
    assert task_store.stats().get("pending") == 1

    claimed = task_store.claim_next("worker")
    assert claimed is not None and claimed["id"] == t1
    task_store.complete(t1)

    # 重复 save 同版本：返回既有 done 任务，不产生新的 pending
    t3 = task_store.enqueue("memory_extract", "9901", 7, "extract", tenant_id="default")
    assert t3 == t1 and task_store.stats().get("pending") is None
    assert background_tasks.run_once("worker") is None

    # 源版本前进才允许新任务
    t4 = task_store.enqueue("memory_extract", "9901", 8, "extract", tenant_id="default")
    assert t4 != t1
