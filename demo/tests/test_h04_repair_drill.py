# -*- coding: utf-8 -*-
"""H04：副本演练可重复/可中断/可回滚/可重做；拒绝非副本与 hash 漂移；不动原审计。"""

import os
import sqlite3
import sys
import tempfile

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import history_repair_drill as h04  # noqa: E402


def _make_copy():
    d = tempfile.mkdtemp(prefix="h04_")
    open(os.path.join(d, h04.MARKER), "w").close()
    path = os.path.join(d, "copy.db")
    con = sqlite3.connect(path)
    for stmt in (
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY)",
        "INSERT INTO schema_version VALUES (7)",
        "CREATE TABLE receipts (id INTEGER PRIMARY KEY, status TEXT, image_path TEXT,"
        " total_amount REAL, items_json TEXT, deleted_at TEXT, reversed_at TEXT,"
        " tenant_id TEXT)",
        "CREATE TABLE receipt_items (id INTEGER PRIMARY KEY, receipt_id INTEGER,"
        " quantity REAL, line_uuid TEXT, tenant_id TEXT)",
        "CREATE TABLE review_events (id INTEGER PRIMARY KEY, receipt_id INTEGER,"
        " event_kind TEXT, tenant_id TEXT)",
        "CREATE TABLE posting_headers (id INTEGER PRIMARY KEY, tenant_id TEXT,"
        " receipt_id INTEGER, action TEXT, status TEXT)",
        "CREATE TABLE stock_movements (id INTEGER PRIMARY KEY, posting_id INTEGER,"
        " qty_scaled INTEGER, tenant_id TEXT)",
        "CREATE TABLE batch_allocations (id INTEGER PRIMARY KEY, movement_id INTEGER,"
        " qty_scaled INTEGER, cost_minor INTEGER)",
        "CREATE TABLE credit_documents (id INTEGER PRIMARY KEY, tenant_id TEXT,"
        " supplier_id INTEGER, face_minor INTEGER, state TEXT)",
        "CREATE TABLE credit_entries (id INTEGER PRIMARY KEY, tenant_id TEXT,"
        " credit_id INTEGER, event_kind TEXT, amount_minor INTEGER)",
        "CREATE TABLE credit_allocations (id INTEGER PRIMARY KEY, tenant_id TEXT,"
        " credit_id INTEGER, target_receipt_id INTEGER, amount_minor INTEGER,"
        " reversal_of INTEGER)",
        "CREATE TABLE supplier_refunds (id INTEGER PRIMARY KEY, tenant_id TEXT,"
        " credit_id INTEGER, state TEXT)",
        "CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name TEXT, tenant_id TEXT)",
    ):
        con.execute(stmt)
    con.execute("INSERT INTO receipts VALUES (1,'approved','/x.jpg',100.0,'[]',NULL,NULL,'t1')")
    con.execute("INSERT INTO receipt_items VALUES (1,1,1.0,'lu-1','t1')")
    con.execute("INSERT INTO review_events VALUES (1,1,'approved','t1')")
    con.execute("INSERT INTO posting_headers VALUES (1,'t1',1,'post','approved')")
    con.execute("INSERT INTO posting_headers VALUES (2,'t1',99,'post','approved')")
    con.execute("INSERT INTO stock_movements VALUES (1,1,3000,'t1')")
    con.execute("INSERT INTO batch_allocations VALUES (1,1,3000,NULL)")
    con.execute("INSERT INTO suppliers VALUES (1,'Vendor_A','t1')")
    con.execute("INSERT INTO credit_documents VALUES (1,'t1',1,1000,'active')")
    con.execute("INSERT INTO credit_entries VALUES (1,'t1',1,'issued',900)")
    con.execute("INSERT INTO credit_allocations VALUES (1,'t1',1,1,1100,NULL)")
    con.commit()
    con.close()
    return d, path


def test_repair_repeat_interrupt_rollback_reapply():
    d, path = _make_copy()
    try:
        run = h04.dry_run(path)
        planned = run["operations"]
        assert planned >= 3
        digest = h04._sha_file(path)
        # 中断：只 apply 1 步
        part = h04.apply(path, digest, max_ops=1)
        assert part["applied_now"] == 1 and part["planned"] == planned
        mid = h04.reconcile(path)
        assert mid["ok"] is False and mid["unexplained"] == planned - 1
        # 继续 apply 到全量
        full = h04.apply(path, digest)
        assert full["ledger_rows"] == planned
        assert h04.reconcile(path)["ok"] is True
        # 回滚：只删演练账本，原审计不变
        rb = h04.rollback(path, expected_digest=full["business_digest"])
        assert rb["ledger_rows"] == 0 and rb["audit_unchanged"] is True
        # 重做：同 operation_key，幂等
        again = h04.apply(path, digest)
        assert again["ledger_rows"] == planned
        con = sqlite3.connect(path)
        try:
            keys = [r[0] for r in con.execute(
                "SELECT operation_key FROM %s ORDER BY operation_key" % h04.LEDGER)]
            assert len(keys) == len(set(keys)) == planned
        finally:
            con.close()
        assert h04.reconcile(path)["ok"] is True
    finally:
        import shutil
        shutil.rmtree(d)


def test_refuses_non_drill_copy_and_hash_mismatch():
    d, path = _make_copy()
    try:
        os.remove(os.path.join(d, h04.MARKER))
        with pytest.raises(PermissionError):
            h04.apply(path, h04._sha_file(path))
        open(os.path.join(d, h04.MARKER), "w").close()
        with pytest.raises(ValueError):
            h04.apply(path, "0" * 64)
        # 守卫拒绝时不产生账本
        con = sqlite3.connect(path)
        try:
            assert con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                               " AND name=?", (h04.LEDGER,)).fetchone() is None
        finally:
            con.close()
    finally:
        import shutil
        shutil.rmtree(d)


def test_rollback_refuses_digest_mismatch():
    d, path = _make_copy()
    try:
        full = h04.apply(path, h04._sha_file(path))
        with pytest.raises(ValueError):
            h04.rollback(path, expected_digest="deadbeef")
        # 未回滚：账本仍在
        assert h04.reconcile(path)["applied"] == full["ledger_rows"]
    finally:
        import shutil
        shutil.rmtree(d)
