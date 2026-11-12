# -*- coding: utf-8 -*-
"""M03 持久任务存储：durable_tasks（v7 表）之上的 outbox/lease 语义。

口径（ADR05 / P03 §4 / backlog M03）：
- 事务内 enqueue（INSERT OR IGNORE，唯一键 kind+aggregate_id+aggregate_version+purpose）：
  业务事务提交则任务存在、回滚则不存在——outbox 语义；同键重复入队返回既有任务。
- lease 认领：claim 以条件 UPDATE 抢占（status pending 或 lease 过期的 running），
  attempt+1、写 lease_owner/expires；worker 崩溃后 lease 到期即可被重新认领（恢复）。
- 重试：fail 时 attempt+1，指数退避 next_retry_at=now+2^attempt 秒；超过 max_attempts
  转 dead_letter（有上限 cap，不无限重试）。
- 无外呼在 DB 锁内：本模块只做 DB 操作；任务执行体在 worker 侧、lease 之外调用。
- 开关：DURABLE_TASKS_ENABLED（默认关）——关闭时调用方走既有同步路径，行为逐字节不变。
"""

import json
import os
import time
from datetime import datetime

from sqlalchemy import text

DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_LEASE_SECONDS = 60


def enabled():
    return os.environ.get("DURABLE_TASKS_ENABLED", "0").strip().lower() in ("1", "true", "yes")


def _now():
    return time.time()


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _engine():
    from app import db
    if getattr(db, "_engine", None) is None:
        db._make_engine()
    return db._engine


def enqueue(kind, aggregate_id, aggregate_version, purpose="", payload=None,
            tenant_id=None):
    """事务内入队（outbox）：同 (kind, aggregate, version, purpose) 幂等。返回 task id。"""
    eng = _engine()
    payload_json = json.dumps(payload, ensure_ascii=False) if payload else None
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT id FROM durable_tasks WHERE kind=:k AND aggregate_id=:a"
            " AND ifnull(aggregate_version,-1)=ifnull(:v,-1) AND purpose=:p"),
            {"k": kind, "a": str(aggregate_id), "v": aggregate_version,
             "p": str(purpose or "")}).fetchone()
        if row:
            return int(row[0])
        res = conn.execute(text(
            "INSERT INTO durable_tasks (tenant_id, kind, aggregate_id, aggregate_version,"
            " purpose, payload_ref, status, attempt, created_at, updated_at)"
            " VALUES (:t, :k, :a, :v, :p, :pl, 'pending', 0, :ts, :ts)"),
            {"t": str(tenant_id or "default"), "k": kind, "a": str(aggregate_id),
             "v": aggregate_version, "p": str(purpose or ""), "pl": payload_json,
             "ts": _iso(_now())})
        return int(res.lastrowid)


def claim_next(worker_id, lease_seconds=DEFAULT_LEASE_SECONDS):
    """认领下一个可执行任务（pending，或 lease 过期的 running——崩溃恢复）。

    条件 UPDATE 抢占（行数=1 才算认领成功）：attempt+1、status=running、
    lease_owner/lease_expires_at 写入。返回任务 dict 或 None。
    """
    eng = _engine()
    now = _now()
    with eng.begin() as conn:
        cand = conn.execute(text(
            "SELECT id FROM durable_tasks WHERE (status='pending'"
            "  AND (next_retry_at IS NULL OR next_retry_at <= :now_iso))"
            " OR (status='running' AND ifnull(lease_expires_at,'') < :now_iso)"
            " ORDER BY id LIMIT 1"), {"now_iso": _iso(now)}).fetchone()
        if not cand:
            return None
        res = conn.execute(text(
            "UPDATE durable_tasks SET status='running', attempt=attempt+1,"
            " lease_owner=:w, lease_expires_at=:exp, updated_at=:ts"
            " WHERE id=:i AND ((status='pending'"
            "  AND (next_retry_at IS NULL OR next_retry_at <= :now_iso))"
            " OR (status='running' AND ifnull(lease_expires_at,'') < :now_iso))"),
            {"w": str(worker_id), "exp": _iso(now + int(lease_seconds)),
             "ts": _iso(now), "i": int(cand[0]), "now_iso": _iso(now)})
        if res.rowcount != 1:
            return None  # 并发抢走：本轮放弃，下轮再看
        row = conn.execute(text(
            "SELECT id, tenant_id, kind, aggregate_id, aggregate_version, purpose,"
            " payload_ref, attempt FROM durable_tasks WHERE id=:i"),
            {"i": int(cand[0])}).fetchone()
    return {"id": int(row[0]), "tenant_id": row[1], "kind": row[2],
            "aggregate_id": row[3], "aggregate_version": row[4], "purpose": row[5],
            "payload": (json.loads(row[6]) if row[6] else {}),
            "attempt": int(row[7])}


def complete(task_id):
    with _engine().begin() as conn:
        conn.execute(text(
            "UPDATE durable_tasks SET status='done', lease_owner=NULL,"
            " lease_expires_at=NULL, updated_at=:ts WHERE id=:i"),
            {"ts": _iso(_now()), "i": int(task_id)})


def fail(task_id, error="", max_attempts=DEFAULT_MAX_ATTEMPTS):
    """失败重试：指数退避；超过 max_attempts 转 dead_letter（cap，不无限重试）。"""
    eng = _engine()
    with eng.begin() as conn:
        row = conn.execute(text(
            "SELECT attempt FROM durable_tasks WHERE id=:i"), {"i": int(task_id)}).fetchone()
        if not row:
            return "missing"
        attempt = int(row[0] or 0)
        if attempt >= int(max_attempts):
            conn.execute(text(
                "UPDATE durable_tasks SET status='dead_letter', last_error=:e,"
                " lease_owner=NULL, lease_expires_at=NULL, updated_at=:ts WHERE id=:i"),
                {"e": str(error or "")[:2000], "ts": _iso(_now()), "i": int(task_id)})
            return "dead_letter"
        backoff = 2 ** attempt
        conn.execute(text(
            "UPDATE durable_tasks SET status='pending', last_error=:e,"
            " next_retry_at=:retry, lease_owner=NULL, lease_expires_at=NULL,"
            " updated_at=:ts WHERE id=:i"),
            {"e": str(error or "")[:2000], "retry": _iso(_now() + backoff),
             "ts": _iso(_now()), "i": int(task_id)})
        return "retry_scheduled"


def stats():
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status, COUNT(*) FROM durable_tasks GROUP BY status")).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def stats_by_kind(kind):
    """按 kind 的持久 backlog（M05 同步补偿可观测）：计数 + 最老 pending 起点。

    只读 durable_tasks（DB 持久），不是进程内队列快照；无行时 total=0。
    """
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT status, COUNT(*) FROM durable_tasks WHERE kind=:k GROUP BY status"),
            {"k": str(kind)}).fetchall()
        oldest = conn.execute(text(
            "SELECT created_at FROM durable_tasks WHERE kind=:k AND status='pending'"
            " ORDER BY id LIMIT 1"), {"k": str(kind)}).fetchone()
    out = {r[0]: int(r[1]) for r in rows}
    out["total"] = sum(out.values())
    out["oldest_pending_created_at"] = oldest[0] if oldest else None
    return out


def dead_letter_tasks(limit=20):
    """最近的死信任务（技术 admin 视图看错误与重试次数）。"""
    eng = _engine()
    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, kind, aggregate_id, aggregate_version, attempt, last_error,"
            " updated_at FROM durable_tasks WHERE status='dead_letter'"
            " ORDER BY id DESC LIMIT :n"), {"n": int(limit)}).fetchall()
    return [{"id": int(r[0]), "kind": r[1], "aggregate_id": r[2],
             "aggregate_version": r[3], "attempt": int(r[4] or 0),
             "last_error": (r[5] or "")[:300], "updated_at": r[6]} for r in rows]
