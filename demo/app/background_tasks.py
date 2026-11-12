# -*- coding: utf-8 -*-
"""M03 后台任务 worker：单进程单 worker、DB lease、可崩溃恢复。

- start_worker()：daemon 线程；DISABLE_BACKGROUND_JOBS=1 或 pytest 环境不启动
  （与 maintenance.py 同一口径）；幂等（重复调用只起一个）。
- run_once(worker_id)：认领→分发→complete/fail 一轮（测试与运维手动驱动入口）。
- 任务执行体在 lease 之外调用（本模块不持 DB 事务做外呼——ADR05 红线）。
- kind='memory_extract'：委托 api_receipts._extract_and_enqueue_memory_facts
  （唯一实现；save/approve 事务只入队不执行——业务提交与抽取解耦，Q09/MP01）。
- 未知 kind：直接 dead_letter 语义 fail（不无限重试无法执行的任务）。
"""

import logging
import threading
import time

from app import task_store

logger = logging.getLogger("background_tasks")

_WORKER_THREAD = None
_STOP = threading.Event()
_POLL_SECONDS = 1.0


def _handle_memory_extract(task):
    """抽取任务执行体：按 source version 幂等（入队键含 version，重复入队被唯一键挡）。"""
    from app import db
    from app.api_receipts import _extract_and_enqueue_memory_facts, _ai_prefill_items
    rid = int(task["aggregate_id"])
    version = task.get("aggregate_version")
    tenant_id = task.get("tenant_id") or "default"
    row = db.get_receipt_row(rid, tenant_id=tenant_id)
    if row is None:
        logger.warning("[background] memory_extract 目标单据不存在 task=%s rid=%s",
                       task["id"], rid)
        return
    if version is not None and getattr(row, "version", None) != int(version):
        # 源版本已前进：本任务作废（后续版本会重新入队），不按旧快照抽取
        logger.info("[background] memory_extract 跳过过期版本 task=%s rid=%s"
                    " task_version=%s current=%s", task["id"], rid, version, row.version)
        return
    trigger = (task.get("payload") or {}).get("trigger") or "background"
    _extract_and_enqueue_memory_facts(
        rid, row, tenant_id, _ai_prefill_items(row),
        db.get_receipt_items(rid, tenant_id=tenant_id), trigger=trigger)


def _handle_memory_vector_sync(task):
    """M04 补偿执行体：删旧向量 + 补写新向量；任一 False/异常 → 抛出走重试退避。"""
    from app import db
    from app.services import memory_store, rag
    payload = task.get("payload") or {}
    tenant_id = task.get("tenant_id") or "default"
    old_mid = payload.get("old_memory_id")
    new_mid = payload.get("new_memory_id")
    vendor = payload.get("vendor") or ""
    row = db.get_vendor_memory_by_id(str(new_mid), tenant_id=tenant_id)
    if row is None or row.get("status") != "active":
        # 新值已不 active（再次编辑/删除）→ 无需补偿，任务作废
        logger.info("[background] memory_vector_sync 目标非 active，跳过 task=%s", task["id"])
        return
    ok_archive = memory_store.delete_memory_vectors(tenant_id, [str(old_mid)]) if old_mid else True
    ok_restore = rag.ensure_memory_vectors(tenant_id, str(new_mid), vendor=vendor)
    if ok_archive is False or ok_restore is False:
        raise RuntimeError(
            "vector sync still incomplete (archive=%s restore=%s)" % (ok_archive, ok_restore))


_HANDLERS = {
    "memory_extract": _handle_memory_extract,
    "memory_vector_sync": _handle_memory_vector_sync,
}


def run_once(worker_id="manual"):
    """认领并执行一轮；返回处理描述或 None（无可认领任务）。"""
    task = task_store.claim_next(worker_id)
    if task is None:
        return None
    handler = _HANDLERS.get(task["kind"])
    try:
        if handler is None:
            task_store.fail(task["id"], error="unknown kind: %s" % task["kind"],
                            max_attempts=0)  # 未知类型直接 dead_letter
            return {"task": task["id"], "kind": task["kind"], "result": "dead_letter"}
        handler(task)
        task_store.complete(task["id"])
        return {"task": task["id"], "kind": task["kind"], "result": "done"}
    except Exception as e:  # noqa: BLE001 执行体异常 → 重试退避（cap 后 dead_letter）
        outcome = task_store.fail(task["id"], error=str(e))
        logger.warning("[background] task %s failed (%s): %s",
                       task["id"], outcome, e)
        return {"task": task["id"], "kind": task["kind"], "result": outcome,
                "error": str(e)}


def _worker_loop(worker_id):
    while not _STOP.is_set():
        try:
            ran = run_once(worker_id)
            if ran is None:
                _STOP.wait(_POLL_SECONDS)
        except Exception as e:  # noqa: BLE001 worker 主循环绝不带死
            logger.error("[background] worker loop error: %s", e)
            _STOP.wait(_POLL_SECONDS)


def start_worker(worker_id=None):
    """启动常驻 worker 线程（幂等）；测试/禁用环境不启动。"""
    global _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return None
    import os
    if os.environ.get("DISABLE_BACKGROUND_JOBS") == "1" or os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    wid = worker_id or ("worker-%d" % (os.getpid(),))
    _STOP.clear()
    _WORKER_THREAD = threading.Thread(target=_worker_loop, args=(wid,),
                                      name="durable-task-worker", daemon=True)
    _WORKER_THREAD.start()
    logger.info("[background] durable task worker started (id=%s)", wid)
    return _WORKER_THREAD


def stop_worker():
    _STOP.set()
