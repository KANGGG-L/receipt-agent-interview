# -*- coding: utf-8 -*-
"""H01：历史只读影响清单（C-HISTORY）——错误类全枚举、unknown 不写 0、只读与快照绑定。"""

import hashlib
import json
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

import audit_posting_integrity as h01  # noqa: E402


def _ddl():
    return (
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
        " qty_scaled INTEGER)",
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
    )


def _make_db():
    fd, path = tempfile.mkstemp(prefix="h01_", suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    for stmt in _ddl():
        con.execute(stmt)
    # receipt 1：缺原图/缺总额/行无 line_uuid/数量 unknown/无审批事件
    con.execute("INSERT INTO receipts VALUES (1,'approved','',NULL,'[]',NULL,NULL,'t1')")
    con.execute("INSERT INTO receipt_items VALUES (1,1,NULL,NULL,'t1')")
    # receipt 2：事实齐全（含审批事件）
    con.execute("INSERT INTO receipts VALUES (2,'approved','/x.jpg',100.0,'[]',NULL,NULL,'t1')")
    con.execute("INSERT INTO receipt_items VALUES (2,2,1.0,'lu-2','t1')")
    con.execute("INSERT INTO review_events VALUES (1,2,'approved','t1')")
    # posting 1 正常；posting 2 指向不存在单据
    con.execute("INSERT INTO posting_headers VALUES (1,'t1',2,'post','approved')")
    con.execute("INSERT INTO stock_movements VALUES (1,1,3000,'t1')")
    con.execute("INSERT INTO batch_allocations VALUES (1,1,3000)")
    con.execute("INSERT INTO posting_headers VALUES (2,'t1',99,'post','approved')")
    # 信用：1 账实不符 + 超额分配；2 供应商缺失
    con.execute("INSERT INTO suppliers VALUES (1,'Vendor_A','t1')")
    con.execute("INSERT INTO credit_documents VALUES (1,'t1',1,1000,'active')")
    con.execute("INSERT INTO credit_entries VALUES (1,'t1',1,'issued',900)")
    con.execute("INSERT INTO credit_allocations VALUES (1,'t1',1,2,1100,NULL)")
    con.execute("INSERT INTO credit_documents VALUES (2,'t1',77,500,'active')")
    con.commit()
    con.close()
    return path


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_manifest_enumerates_all_error_classes_and_unknown():
    path = _make_db()
    try:
        m = h01.build_manifest(path)
        reasons = {(o["object_kind"], o["object_id"], o["reason"])
                   for o in m["objects"]}
        assert ("receipt", 1, "missing_source_image") in reasons
        assert ("receipt", 1, "unknown_total_amount") in reasons
        assert ("receipt", 1, "unknown_quantity") in reasons
        assert ("receipt", 1, "line_uuid_missing") in reasons
        assert ("receipt", 1, "approval_event_missing") in reasons
        assert ("posting", 2, "receipt_missing") in reasons
        assert ("credit", 1, "credit_balance_mismatch") in reasons
        assert ("credit", 1, "credit_overallocated") in reasons
        assert ("credit", 2, "supplier_missing") in reasons
        # 四类都出现，且 unknown 没有被写成 0
        assert set(m["counts"]) == {"unknown", "non_rebuildable",
                                    "owner_adjudication", "rebuildable"}
        assert m["snapshot"]["sha256"] == _sha(path)
        assert m["rule_version"] == h01.RULE_VERSION
        assert m["absent_tables"] == []
        # 脱敏：manifest 不含金额字段
        assert "1100" not in json.dumps(m) and "1000" not in json.dumps(m)
    finally:
        os.remove(path)


def test_manifest_is_read_only_and_deterministic():
    path = _make_db()
    try:
        before = _sha(path)
        m1 = h01.build_manifest(path)
        m2 = h01.build_manifest(path)
        assert _sha(path) == before
        assert m1["snapshot"]["sha256"] == m2["snapshot"]["sha256"]
        assert sorted(json.dumps(x, sort_keys=True) for x in m1["objects"]) == \
            sorted(json.dumps(x, sort_keys=True) for x in m2["objects"])
    finally:
        os.remove(path)


def test_tenant_filter_scopes_manifest():
    path = _make_db()
    try:
        con = sqlite3.connect(path)
        con.execute("INSERT INTO receipts VALUES (3,'approved','/y.jpg',50.0,'[]',"
                    "NULL,NULL,'t2')")
        con.commit()
        con.close()
        m = h01.build_manifest(path, tenant="t2")
        assert m["tenant"] == "t2"
        assert all(o["tenant_id"] == "t2" for o in m["objects"])
        assert m["invariants"]["receipts_checked"] == 1
    finally:
        os.remove(path)


def test_snapshot_drift_aborts(monkeypatch):
    path = _make_db()
    try:
        seq = iter(["a" * 64, "b" * 64])
        monkeypatch.setattr(h01, "_sha256", lambda _p: next(seq))
        with pytest.raises(RuntimeError):
            h01.build_manifest(path)
    finally:
        os.remove(path)


def test_cli_writes_manifest(tmp_path):
    path = _make_db()
    out = tmp_path / "manifest.json"
    try:
        rc = h01.main(["--db", path, "--out", str(out), "--rule-version", "h01.test"])
        assert rc == 0
        m = json.loads(out.read_text(encoding="utf-8"))
        assert m["rule_version"] == "h01.test"
        assert m["schema"] == "h01-impact-manifest.v1"
    finally:
        os.remove(path)
