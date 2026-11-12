# -*- coding: utf-8 -*-
"""H05：缺事件=unknown（不按审批状态推断）、v1 冻结、v2 带来源版本。"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
for _p in (_DEMO_DIR,):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.services import metrics_history as h05  # noqa: E402


def _make_db():
    fd, path = tempfile.mkstemp(prefix="h05_", suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE receipts (id INTEGER PRIMARY KEY, status TEXT,"
        " ai_prefill_json TEXT, audit_json TEXT, tenant_id TEXT)")
    con.execute(
        "CREATE TABLE review_events (id INTEGER PRIMARY KEY, receipt_id INTEGER,"
        " event_kind TEXT, tenant_id TEXT)")
    # 1：批准但无事件、无 AI 快照 → unknown
    con.execute("INSERT INTO receipts VALUES (1,'approved',NULL,NULL,'t1')")
    # 2：事实齐全，可回算
    con.execute("INSERT INTO receipts VALUES (2,'approved','{\"x\":1}',NULL,'t1')")
    con.execute("INSERT INTO review_events VALUES (1,2,'saved','t1')")
    con.execute("INSERT INTO review_events VALUES (2,2,'approved','t1')")
    con.commit()
    con.close()
    return path


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def test_history_without_events_unknown():
    path = _make_db()
    try:
        before = _sha(path)
        rep = h05.build_recomputed_report(path, as_of="2026-10-25")
        rows = {r["receipt_id"]: r for r in rep["rows"]}
        assert rows[1]["status"] == "unknown"
        assert rows[1]["metrics"] is None  # 不写 0
        assert "no_review_events" in rows[1]["reasons"]
        assert "approval_event_missing" in rows[1]["reasons"]
        assert rows[2]["status"] == "comparable"
        assert rows[2]["metrics"]["saved_events"] == 1
        assert rep["summary"] == {"receipts": 2, "comparable": 1, "unknown": 1}
        assert rep["source_snapshot_sha256"] == before
        assert _sha(path) == before
    finally:
        os.remove(path)


def test_recomputed_report_version_preserves_old(tmp_path):
    path = _make_db()
    try:
        old = {"schema": "h05.history-metrics.v1", "as_of": "2026-09-30",
               "summary": {"receipts": 2, "comparable": 2, "unknown": 0}}
        old_file = tmp_path / "v1.json"
        old_file.write_text(json.dumps(old), encoding="utf-8")
        old_sha = _sha(str(old_file))
        rep = h05.build_recomputed_report(
            path, as_of="2026-10-25", old_report=h05.load_old_report(str(old_file)))
        assert rep["schema"] == "h05.history-metrics.v2"
        assert rep["metric_definition_version"] == "h05.metricdef.v1"
        assert rep["as_of"] == "2026-10-25"
        assert rep["recomputed_at"]
        assert rep["frozen_previous"] == old
        assert _sha(str(old_file)) == old_sha  # v1 文件未被改写
    finally:
        os.remove(path)
