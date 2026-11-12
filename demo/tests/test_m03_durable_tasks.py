# -*- coding: utf-8 -*-
"""M03 持久任务与业务提交后抽取（C-TASK 四 AC + 幂等/退避/deadletter）。

开关契约：DURABLE_TASKS_ENABLED=1 本文件专测 outbox 语义；关闭分支由既有 104 项回归覆盖。
"""

import os
import sys
import tempfile
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_m03_tasks_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_m03.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "1"
os.environ["DURABLE_TASKS_ENABLED"] = "1"
os.environ["DISABLE_BACKGROUND_JOBS"] = "1"  # worker 不自动起；用 run_once 显式驱动
os.environ.setdefault("DEMO_TOKEN_SECRET", "m03-test-secret-not-the-default-value")
os.environ.pop("IDENTITY_V2_ENABLED", None)

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import auth, db, task_store, background_tasks  # noqa: E402
from app.main import app  # noqa: E402

db._make_engine()
client = TestClient(app)
_ACCOUNT_EMAIL = {"staff": "staff@demo.hk", "owner": "boss@demo.hk"}


@pytest.fixture(autouse=True)
def _clean_tasks():
    """任务表测试间清空：claim_next 按 id 最旧认领，残留会污染后续用例。"""
    s = db.get_session()
    try:
        s.execute(text("DELETE FROM durable_tasks"))
        s.commit()
    finally:
        s.close()
    yield


def _hdr(role):
    return {"Authorization": "Bearer " + auth.issue_token(_ACCOUNT_EMAIL[role], role, "default")}


def _save_manual(items, total):
    payload = {"source": "manual", "supplier_name": "Vendor_A", "date": "2026-10-26",
               "doc_form": "printed_delivery_note", "settlement_type": "credit",
               "currency": "HKD", "total_amount": total, "items": items}
    r = client.post("/api/save_edited", headers=_hdr("staff"), json=payload)
    assert r.status_code == 200 and r.json().get("status") == "success", r.json()
    return r.json()["receipt_id"], r.json()["version"]


def _task_rows():
    s = db.get_session()
    try:
        return s.execute(text(
            "SELECT id, kind, aggregate_id, aggregate_version, status, attempt,"
            " purpose FROM durable_tasks ORDER BY id")).fetchall()
    finally:
        s.close()


def test_enqueue_idempotent_same_key():
    """同 (kind, aggregate, version, purpose) 重复入队 → 同一任务，不重复。"""
    t1 = task_store.enqueue("memory_extract", "1", 1, "extract", tenant_id="default")
    t2 = task_store.enqueue("memory_extract", "1", 1, "extract", tenant_id="default")
    t3 = task_store.enqueue("memory_extract", "1", 2, "extract", tenant_id="default")
    assert t1 == t2 and t3 != t1
    assert len([r for r in _task_rows()]) == 2


def test_lease_exclusivity_and_crash_recovery():
    """认领互斥；worker 崩溃（lease 过期）后任务可被重新认领（attempt+1）。"""
    tid = task_store.enqueue("memory_extract", "10", 1, "extract", tenant_id="default")
    first = task_store.claim_next("w1")
    assert first is not None and first["id"] == tid
    second = task_store.claim_next("w2")
    assert second is None  # 正常 lease 内互斥
    # 模拟 worker 崩溃：lease 过期
    s = db.get_session()
    try:
        s.execute(text("UPDATE durable_tasks SET lease_expires_at='2000-01-01T00:00:00'"
                       " WHERE id=:i"), {"i": tid})
        s.commit()
    finally:
        s.close()
    third = task_store.claim_next("w2")
    assert third is not None and third["id"] == tid  # 崩溃恢复
    assert third["attempt"] == 2  # 第一次认领+1，恢复再+1


def test_retry_backoff_and_deadletter():
    """失败重试指数退避；超过 cap 转 dead_letter 且不再被认领。"""
    tid = task_store.enqueue("memory_extract", "20", 1, "extract", tenant_id="default")
    outcomes = []
    s = db.get_session()
    def _travel_past():
        """穿越退避：把 next_retry_at 拨到过去，模拟等待时间流逝。"""
        try:
            s.execute(text("UPDATE durable_tasks SET next_retry_at='2000-01-01T00:00:00'"
                           " WHERE id=:i"), {"i": tid})
            s.commit()
        finally:
            pass
    for i in range(5):  # cap=5：第 5 次失败（attempt 已达 5）转 dead_letter
        _travel_past()
        task = task_store.claim_next("w")
        assert task is not None and task["id"] == tid
        outcomes.append(task_store.fail(tid, error="synthetic %d" % i))
    assert outcomes[:4] == ["retry_scheduled"] * 4
    assert outcomes[4] == "dead_letter"
    assert task_store.claim_next("w") is None  # dead_letter 不再认领
    # 退避已排程：dead_letter 终态与最后错误在案
    try:
        row = s.execute(text("SELECT status, next_retry_at, last_error FROM durable_tasks"
                             " WHERE id=:i"), {"i": tid}).fetchone()
        assert row[0] == "dead_letter" and row[2].startswith("synthetic 4")  # 第 5 次失败（i=4）入册
    finally:
        s.close()


def test_save_response_independent_of_slow_extract():
    """业务保存与抽取解耦：开关开时保存即回 200，抽取任务 pending 等待 worker；
    run_once 执行后任务 done、同版本不重复抽取（extract_once_per_source_version）。"""
    os.environ["DURABLE_TASKS_ENABLED"] = "1"
    try:
        t0 = time.monotonic()
        rid, v = _save_manual([{"name": "Item_A", "quantity": 2, "unit": "kg",
                                "unit_price": 7, "amount": 14}], 14)
        # 新单首次保存无抽取（无 AI 预填可比）；编辑既有单才触发抽取——二段保存走入队
        row = db.get_receipt_row(rid, tenant_id="default")
        r2 = client.post("/api/save_edited", headers=_hdr("staff"), json={
            "receipt_id": rid, "version": row.version, "source": "manual",
            "supplier_name": "Vendor_A", "date": "2026-10-26",
            "doc_form": "printed_delivery_note", "settlement_type": "credit",
            "currency": "HKD", "total_amount": 14,
            "items": [{"name": "Item_A", "quantity": 2, "unit": "kg",
                       "unit_price": 7, "amount": 14}]})
        assert r2.status_code == 200
        elapsed = time.monotonic() - t0
        assert elapsed < 3.0, elapsed  # 无同步抽取等待（审计 Q09 形态的解除）
        rows = [r for r in _task_rows() if r[2] == str(rid)]
        assert len(rows) == 1 and rows[0][4] == "pending"
        # worker 驱动一轮 → 任务完成
        ran = background_tasks.run_once("test-worker")
        assert ran is not None and ran["result"] == "done"
        # 同版本重放：无新任务、再 run_once 无可认领（extract once per source version）
        ran2 = background_tasks.run_once("test-worker")
        assert ran2 is None
        rows2 = [r for r in _task_rows() if r[2] == str(rid)]
        assert len(rows2) == 1 and rows2[0][4] == "done"
    finally:
        os.environ["DURABLE_TASKS_ENABLED"] = "0"


def test_flag_off_keeps_sync_behavior(monkeypatch):
    """开关关（默认）：保存走同步抽取路径（旧行为），不产生 durable_tasks 行。"""
    monkeypatch.delenv("DURABLE_TASKS_ENABLED", raising=False)
    rid, v = _save_manual([{"name": "Item_B", "quantity": 1, "unit": "kg",
                            "unit_price": 5, "amount": 5}], 5)
    row = db.get_receipt_row(rid, tenant_id="default")
    r2 = client.post("/api/save_edited", headers=_hdr("staff"), json={
        "receipt_id": rid, "version": row.version, "source": "manual",
        "supplier_name": "Vendor_A", "date": "2026-10-26",
        "doc_form": "printed_delivery_note", "settlement_type": "credit",
        "currency": "HKD", "total_amount": 5,
        "items": [{"name": "Item_B", "quantity": 1, "unit": "kg",
                   "unit_price": 5, "amount": 5}]})
    assert r2.status_code == 200
    rows = [r for r in _task_rows() if r[2] == str(rid)]
    assert rows == []
