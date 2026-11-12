# -*- coding: utf-8 -*-
"""H03：修正预览不静默分配信用、未知成本不补 0、只读与版本绑定。"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import history_repair_plan as h03  # noqa: E402


def _make_db():
    fd, path = tempfile.mkstemp(prefix="h03_", suffix=".db")
    os.close(fd)
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
    con.execute("INSERT INTO stock_movements VALUES (1,1,3000,'t1')")
    # 已消耗成本未知：不补 0
    con.execute("INSERT INTO batch_allocations VALUES (1,1,3000,NULL)")
    con.execute("INSERT INTO suppliers VALUES (1,'Vendor_A','t1')")
    # 信用 1000，入账 900，已分配 1100 → 账实不符 + 超额
    con.execute("INSERT INTO credit_documents VALUES (1,'t1',1,1000,'active')")
    con.execute("INSERT INTO credit_entries VALUES (1,'t1',1,'issued',900)")
    con.execute("INSERT INTO credit_allocations VALUES (1,'t1',1,1,1100,NULL)")
    con.commit()
    con.close()
    return path


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_repair_preview_no_silent_credit_allocate():
    path = _make_db()
    try:
        before = _sha(path)
        plan = h03.build_plan(path)
        assert _sha(path) == before
        credit = [d for d in plan["decisions"] if d["object_kind"] == "credit"]
        assert credit, plan["decisions"]
        target = [d for d in credit if d["reason"] == "credit_overallocated"][0]
        assert target["action"] == "reversal_or_adjustment"
        assert target["requires_owner"] is True
        assert target["status"] == "pending"
        # 不静默新增分配行
        con = sqlite3.connect(path)
        try:
            assert con.execute("SELECT COUNT(*) FROM credit_allocations").fetchone()[0] == 1
        finally:
            con.close()
        assert plan["history_version"] == 7
        assert plan["snapshot"]["sha256"] == before
    finally:
        os.remove(path)


def test_consumed_history_unknown_cost_marked():
    path = _make_db()
    try:
        plan = h03.build_plan(path)
        unknown = [d for d in plan["decisions"]
                   if d["reason"] == "unknown_consumed_cost"]
        assert len(unknown) == 1
        assert unknown[0]["class"] == "unknown"
        assert unknown[0]["status"] == "blocked"
        assert unknown[0]["after"] is None
        # 脱敏：不出现原始金额，也没有把未知成本写成 0
        blob = json.dumps(plan, ensure_ascii=False)
        assert "1100" not in blob and "1000" not in blob and "900" not in blob
        assert '"cost_minor": 0' not in blob
    finally:
        os.remove(path)


def test_plan_is_deterministic_and_read_only():
    path = _make_db()
    try:
        p1 = h03.build_plan(path)
        p2 = h03.build_plan(path)
        assert (json.dumps(p1["decisions"], sort_keys=True, ensure_ascii=False)
                == json.dumps(p2["decisions"], sort_keys=True, ensure_ascii=False))
    finally:
        os.remove(path)
