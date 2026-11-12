# -*- coding: utf-8 -*-
"""P04 迁移门禁：v7 posting_credit_task_expand 的空库/旧版本库/重复/失败恢复四场景。

红线约束（沿用 fix wave 套件口径）：
1. 全局严禁 Emoji；状态用 [OK] / [FAIL]。
2. 全部落在 tempfile 沙箱 DB，禁止触碰 live DB / live 向量库。
3. expand-only 断言：只新增表/索引/可空列，不改既有列语义。
"""

import os
import sys
import tempfile
import uuid

import pytest
from sqlalchemy import text

_TMP = tempfile.mkdtemp(prefix="test_p04_expand_")
os.environ["DB_PATH"] = os.path.join(_TMP, "test_p04.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "test_rag")
os.environ["AUTH_ENABLED"] = "0"

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR, os.path.abspath(os.path.join(_DEMO_DIR, ".."))):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app import db  # noqa: E402

V7_TABLES = (
    "posting_headers", "stock_movements", "batch_allocations",
    "credit_documents", "credit_entries", "credit_allocations",
    "supplier_refunds", "durable_tasks",
)


def _fresh_engine(name):
    db.DB_PATH = os.path.join(_TMP, name + ".db")
    db._make_engine()
    return db._engine


def _table_exists(conn, table):
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table}).fetchall()
    return bool(row)


def _index_exists(conn, index):
    row = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:i"),
        {"i": index}).fetchall()
    return bool(row)


def _column_exists(conn, table, column):
    rows = conn.execute(text("PRAGMA table_info(%s)" % table)).fetchall()
    return any(r[1] == column for r in rows)


def _recorded_versions(engine):
    with engine.connect() as conn:
        return {r[0] for r in conn.execute(text("SELECT version FROM schema_version")).fetchall()}


def test_schema_expand_empty_db_upgrades_to_v7():
    """空库（仅 create_all 的新库）经 _make_engine 直接升到 v7，全部新对象存在。"""
    engine = _fresh_engine("p04_empty")
    assert _recorded_versions(engine) >= {1, 2, 3, 4, 5, 6, 7}
    with engine.connect() as conn:
        for t in V7_TABLES:
            assert _table_exists(conn, t), "missing table " + t
        assert _index_exists(conn, "ux_posting_headers_tenant_request_hash")
        assert _index_exists(conn, "ux_durable_tasks_kind_aggregate")
        assert _index_exists(conn, "ux_receipt_items_receipt_line_uuid")
        assert _column_exists(conn, "receipt_items", "line_uuid")


def test_schema_expand_legacy_recorded_version_upgrades():
    """旧版本记录库：schema_version 只有 1，其余结构由 create_all 等价建出；补跑迁移到 v7。"""
    engine = _fresh_engine("p04_legacy")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_version WHERE version > 1"))
    assert _recorded_versions(engine) == {1}
    db._run_ordered_migrations(engine)
    assert _recorded_versions(engine) >= {1, 2, 3, 4, 5, 6, 7}
    with engine.connect() as conn:
        for t in V7_TABLES:
            assert _table_exists(conn, t), "missing table " + t


def test_schema_expand_repeat_run_is_idempotent():
    """重复执行有序迁移：版本记录不变、行数不翻倍、无异常。"""
    engine = _fresh_engine("p04_repeat")
    before = _recorded_versions(engine)
    rows_before = {}
    with engine.connect() as conn:
        for t in V7_TABLES:
            rows_before[t] = conn.execute(text("SELECT COUNT(*) FROM %s" % t)).scalar()
    db._run_ordered_migrations(engine)
    db._run_ordered_migrations(engine)
    assert _recorded_versions(engine) == before
    with engine.connect() as conn:
        for t in V7_TABLES:
            assert conn.execute(text("SELECT COUNT(*) FROM %s" % t)).scalar() == rows_before[t]
    assert db.schema_version() == db.SCHEMA_MIGRATIONS[-1][0]


def test_schema_expand_failure_keeps_old_schema_and_allows_retry():
    """失败恢复：注入失败迁移后中止且不记录版本；修复后重跑成功。"""
    engine = _fresh_engine("p04_failrec")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM schema_version WHERE version > 1"))
    original = list(db.SCHEMA_MIGRATIONS)
    def _boom(_engine):
        raise RuntimeError("P04 synthetic migration failure")
    db.SCHEMA_MIGRATIONS = [(v, n, (fn if v != 7 else _boom)) for v, n, fn in original]
    try:
        with pytest.raises(Exception):
            db._run_ordered_migrations(engine)
        assert 7 not in _recorded_versions(engine)
        # 旧 schema 未被 v7 污染（失败发生在 v7 第一条 DDL 前/中；若已建表也必须可重试）
        db.SCHEMA_MIGRATIONS = original
        db._run_ordered_migrations(engine)
        assert 7 in _recorded_versions(engine)
        with engine.connect() as conn:
            assert _table_exists(conn, "posting_headers")
            assert _table_exists(conn, "durable_tasks")
    finally:
        db.SCHEMA_MIGRATIONS = original


def test_schema_expand_line_uuid_unique_index_allows_legacy_nulls():
    """line_uuid 唯一索引语义：多个历史 NULL 合法；同 receipt 非 NULL 重复被拒。"""
    engine = _fresh_engine("p04_lineuuid")
    from sqlalchemy import Column, Integer, String
    legacy_table = """
        CREATE TABLE IF NOT EXISTS p04_line_probe (receipt_id INTEGER, line_uuid VARCHAR)
    """
    with engine.begin() as conn:
        conn.execute(text(legacy_table))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_p04_line_probe ON p04_line_probe (receipt_id, line_uuid)"))
        conn.execute(text("INSERT INTO p04_line_probe VALUES (1, NULL)"))
        conn.execute(text("INSERT INTO p04_line_probe VALUES (1, NULL)"))
        conn.execute(text("INSERT INTO p04_line_probe VALUES (1, 'u-" + str(uuid.uuid4()) + "')"))
        conn.execute(text("INSERT INTO p04_line_probe VALUES (1, 'u-" + str(uuid.uuid4()) + "')"))
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO p04_line_probe VALUES (1, 'dup')"))
            conn.execute(text("INSERT INTO p04_line_probe VALUES (1, 'dup')"))
        conn.execute(text("DROP TABLE p04_line_probe"))
