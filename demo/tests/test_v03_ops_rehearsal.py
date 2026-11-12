# -*- coding: utf-8 -*-
"""V03 本地演练：备份→恢复→引用校验；重启不产生重复操作；缺 sidecar 阻放行。"""

import json
import os
import sqlite3
import sys

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DEMO_DIR = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_DEMO_DIR, "scripts")
for _p in (_DEMO_DIR, _SCRIPTS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ops_rehearsal as v03  # noqa: E402


def _make_db(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    db = d / "app.db"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE schema_version (version INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO schema_version VALUES (15)")
    con.execute("CREATE TABLE provider_inbox (id INTEGER PRIMARY KEY, event_id TEXT)")
    con.execute("INSERT INTO provider_inbox (event_id) VALUES ('e1')")
    con.execute("INSERT INTO provider_inbox (event_id) VALUES ('e2')")
    con.execute(
        "CREATE TABLE durable_tasks (id INTEGER PRIMARY KEY, kind TEXT, aggregate_id TEXT,"
        " aggregate_version INTEGER, purpose TEXT)")
    con.execute("CREATE UNIQUE INDEX ux_tasks ON durable_tasks"
                " (kind, aggregate_id, aggregate_version, purpose)")
    con.execute("INSERT INTO durable_tasks (kind, aggregate_id, aggregate_version, purpose)"
                " VALUES ('repair', 'r1', 1, 'apply')")
    con.commit()
    con.close()
    refs = d / "uploads"
    refs.mkdir()
    (refs / "a.jpg").write_bytes(b"synthetic-image")
    return d, str(db), str(refs)


def test_backup_restore_keeps_schema_counts_and_watermark(tmp_path):
    d, db, refs = _make_db(tmp_path)
    backup = str(tmp_path / "backup")
    m = v03.backup_snapshot(db, backup, ref_dirs=[refs])
    assert m["schema_version"] == 15
    assert m["inbox_watermark"] == 2
    assert len(m["refs"]) == 1
    with open(os.path.join(backup, "manifest.json"), encoding="utf-8") as f:
        assert json.load(f)["db_sha256"] == m["db_sha256"]
    out = v03.restore_snapshot(backup, str(tmp_path / "restore"))
    assert out["restored"] is True
    assert out["inbox_watermark"] == 2 and out["row_counts_match"] is True
    assert out["refs_verified"] == 1


def test_missing_sidecar_blocks_restore(tmp_path):
    d, db, refs = _make_db(tmp_path)
    backup = str(tmp_path / "backup")
    v03.backup_snapshot(db, backup, ref_dirs=[refs])
    os.remove(os.path.join(refs, "a.jpg"))
    with pytest.raises(ValueError):
        v03.restore_snapshot(backup, str(tmp_path / "restore"))


def test_restart_check_requires_unique_operation_key(tmp_path):
    d, db, refs = _make_db(tmp_path)
    out = v03.restart_safety_check(db)
    assert out["durable_tasks_unique"] is True
    con = sqlite3.connect(db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO durable_tasks (kind, aggregate_id,"
                        " aggregate_version, purpose) VALUES ('repair','r1',1,'apply')")
    finally:
        con.close()


def test_restart_no_duplicate_operations(tmp_path):
    """V03：worker 崩溃重启后重放同批操作不产生重复操作（幂等）。"""
    d, db, refs = _make_db(tmp_path)
    ops = [("repair", "r2", 1, "apply"), ("repair", "r3", 1, "apply")]
    first = v03.worker_restart_replay(db, ops)
    assert first["applied"] == 2 and first["replayed_noop"] == 0
    # 模拟重启后重放：全部命中唯一键跳过，重复行 0
    second = v03.worker_restart_replay(db, ops)
    assert second["applied"] == 0 and second["replayed_noop"] == 2
    assert second["duplicate_rows"] == 0
    con = sqlite3.connect(db)
    try:
        n = con.execute("SELECT COUNT(*) FROM durable_tasks").fetchone()[0]
        assert n == 3   # 建库 r1 + r2 + r3，无重复
    finally:
        con.close()


def test_restore_billing_inbox_watermark(tmp_path):
    """V03：恢复回滚账单 Inbox 水位到快照点，不虚高（备份后新增事件被回滚）。"""
    d, db, refs = _make_db(tmp_path)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE billing_inbox (id INTEGER PRIMARY KEY, event_id TEXT)")
        con.execute("INSERT INTO billing_inbox (event_id) VALUES ('b1')")
        con.execute("INSERT INTO billing_inbox (event_id) VALUES ('b2')")
        con.commit()
    finally:
        con.close()
    backup = str(tmp_path / "backup")
    m = v03.backup_snapshot(db, backup, ref_dirs=[refs])
    assert m["row_counts"]["billing_inbox"] == 2
    # 备份后新增更高水位事件 —— 恢复必须回到快照水位
    con = sqlite3.connect(db)
    try:
        con.execute("INSERT INTO billing_inbox (event_id) VALUES ('b3')")
        con.commit()
    finally:
        con.close()
    out = v03.restore_snapshot(backup, str(tmp_path / "restore"))
    assert out["restored"] is True and out["row_counts_match"] is True
    rcon = sqlite3.connect(os.path.join(str(tmp_path / "restore"), "app.db"))
    try:
        bw = rcon.execute("SELECT COALESCE(MAX(id),0) FROM billing_inbox").fetchone()[0]
        assert bw == 2
    finally:
        rcon.close()


def test_retention_policy_not_observed():
    """V03：7 年为策略目标（未观察满足）；保全阻断删除、删除需审批。"""
    p = v03.retention_policy()
    assert p["target_years"] == 7
    assert p["satisfied"] is False
    assert p["observed_years"] == 0
    assert p["status"] == "policy_only_unobserved"
    assert v03.retention_decision(0.5) == "retain"
    assert v03.retention_decision(8, delete_requested=True) == "pending_approval"
    assert v03.retention_decision(8, delete_requested=True, approved=True) == "delete"
    assert v03.retention_decision(8, legal_hold=True, delete_requested=True,
                                  approved=True) == "blocked_legal_hold"


def test_access_control_and_audit_preservation():
    """V03：访问控制最小角色矩阵；审计只增不删、随留存目标保留。"""
    assert v03.can("admin", "delete_approve") is True
    assert v03.can("owner", "delete_approve") is False
    assert v03.can("owner", "audit_read") is True
    assert v03.can("staff", "audit_read") is False
    assert v03.can("admin", "unknown_action") is False
    a = v03.audit_preservation_spec()
    assert a["append_only"] is True and a["deletable"] is False
    assert a["retain_years"] == 7


def test_wal_contention_retries_and_reports(tmp_path):
    """V03：WAL 争用退避重试；耗尽仍失败则明确 ok=False，不冒充成功。"""
    d, db, refs = _make_db(tmp_path)
    calls = {"n": 0}

    def flaky(con):
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")

    out = v03.wal_checkpoint_safe(db, retries=3, _checkpoint=flaky)
    assert out["ok"] is True and out["attempts"] == 3

    def always_locked(con):
        raise sqlite3.OperationalError("database is locked")

    out2 = v03.wal_checkpoint_safe(db, retries=2, _checkpoint=always_locked)
    assert out2["ok"] is False and out2["attempts"] == 2
    assert "locked" in out2["error"]


def test_disk_full_restore_fails_clean(tmp_path):
    """V03：磁盘满故障注入——明确失败并清理半成品，不留'已恢复'假象。"""
    d, db, refs = _make_db(tmp_path)
    backup = str(tmp_path / "backup")
    v03.backup_snapshot(db, backup, ref_dirs=[refs])
    restore_dir = str(tmp_path / "restore")

    def disk_full(src, dst):
        with open(dst, "wb") as f:
            f.write(b"partial")
        raise OSError(28, "No space left on device")

    with pytest.raises(v03.OpsFault) as ei:
        v03.restore_snapshot(backup, restore_dir, _copy=disk_full)
    assert ei.value.kind == "disk_full"
    assert not os.path.exists(os.path.join(restore_dir, "app.db"))
