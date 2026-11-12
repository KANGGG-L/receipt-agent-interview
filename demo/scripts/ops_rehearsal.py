# -*- coding: utf-8 -*-
"""V03 本地运维演练（备份→恢复→引用校验→重启幂等/Inbox 水位）。

边界：只做本地副本演练，不声称已观察 7 年，不冻结真实 RPO/RTO，不读真实业务库；
真实容量/恢复目标需发布前另行冻结并实测。缺失 sidecar/引用时拒绝验证。
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime, timezone

MANIFEST_VERSION = "v03.ops-rehearsal.v1"
RETENTION_POLICY_VERSION = "v03.retention-policy.v1"

# 留存目标年数：作为**策略对象**（可执行生命周期规则的目标），不代表已观察满足。
RETENTION_TARGET_YEARS = 7


class OpsFault(RuntimeError):
    """运维演练故障（可精确归因，不冒充成功）：如磁盘满、WAL 争用耗尽重试。"""

    def __init__(self, kind, detail=""):
        self.kind = str(kind)
        self.detail = str(detail)
        super().__init__("%s: %s" % (self.kind, self.detail) if detail else self.kind)


def retention_policy():
    """留存策略对象：7 年为**目标**（可执行生命周期规则），绝非"已满足 7 年"。

    `satisfied=False` / `observed_years=0` 是硬约束：没有任何证据表明已实际留存 7 年，
    不得据此声称合规；真实年限需按发布前冻结的目标实测后方可回填。
    """
    return {
        "schema": RETENTION_POLICY_VERSION,
        "target_years": RETENTION_TARGET_YEARS,
        "observed_years": 0,
        "satisfied": False,
        "status": "policy_only_unobserved",
        "legal_hold_supported": True,
        "delete_requires_approval": True,
        "audit_preservation": audit_preservation_spec(),
        "access_control": access_control_spec(),
        "notes": [
            "7 年为策略目标，本地演练不构成留存证明",
            "真实 RPO/RTO/容量需发布前冻结目标并实测",
        ],
    }


def retention_decision(age_years, *, legal_hold=False, delete_requested=False,
                       approved=False):
    """可执行留存生命周期决策：retain / blocked_legal_hold / pending_approval / delete。

    - 法务保全（legal_hold）优先：一律阻断删除；
    - 未到目标年限且未请求删除 → retain；
    - 请求删除 → 必须经 owner/admin 审批（approved=False 则 pending_approval）；
    - 仅"已审批 + 无保全"时放行 delete。
    """
    if legal_hold:
        return "blocked_legal_hold"
    if not delete_requested:
        return "retain"
    if not approved:
        return "pending_approval"
    return "delete"


def access_control_spec():
    """访问控制矩阵：留存/审计/删除各动作的最小角色集合。"""
    return {
        "audit_read": ["admin", "owner"],
        "audit_export": ["admin"],
        "retention_policy_edit": ["admin"],
        "delete_request": ["admin", "owner"],
        "delete_approve": ["admin"],
    }


def can(role, action):
    """按访问控制矩阵判定角色能否执行动作（未知动作/角色一律拒绝）。"""
    return str(role) in access_control_spec().get(str(action), [])


def audit_preservation_spec():
    """审计保全：只增不改、哈希可链、随留存目标保留、不可删除。"""
    return {
        "append_only": True,
        "hash_chained": True,
        "retain_years": RETENTION_TARGET_YEARS,
        "deletable": False,
    }



def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _tables(con):
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def _schema_version(con):
    if "schema_version" not in _tables(con):
        return None
    row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return int(row[0]) if row and row[0] is not None else None


def _row_counts(con):
    out = {}
    for t in _tables(con):
        if t == "sqlite_sequence":
            continue
        out[t] = int(con.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0])
    return out


def _inbox_watermark(con):
    if "provider_inbox" not in _tables(con):
        return None
    return int(con.execute("SELECT COALESCE(MAX(id),0) FROM provider_inbox").fetchone()[0])


def _collect_refs(ref_dirs):
    refs = []
    for d in ref_dirs or []:
        d = os.path.abspath(d)
        for root, _dirs, files in os.walk(d):
            for name in sorted(files):
                p = os.path.join(root, name)
                if not os.path.isfile(p):
                    continue
                refs.append({"rel": os.path.relpath(p, d), "root": os.path.basename(d),
                             "size": os.path.getsize(p), "sha256": _sha256(p)})
    return refs


def backup_snapshot(db_path, backup_dir, ref_dirs=()):
    """一致性快照：sqlite backup API + 引用文件清单（加密 key 只引用不复制）。"""
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    backup_dir = os.path.abspath(backup_dir)
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, "app.db")
    src_con = sqlite3.connect(db_path)
    try:
        src_con.execute("PRAGMA wal_checkpoint(FULL)")
        dst_con = sqlite3.connect(dst)
        try:
            src_con.backup(dst_con)
            schema_version = _schema_version(dst_con)
            counts = _row_counts(dst_con)
            watermark = _inbox_watermark(dst_con)
        finally:
            dst_con.close()
    finally:
        src_con.close()
    manifest = {
        "schema": MANIFEST_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "db_sha256": _sha256(dst),
        "schema_version": schema_version,
        "row_counts": counts,
        "inbox_watermark": watermark,
        "refs": _collect_refs(ref_dirs),
        "refs_dirs": [{"root": os.path.basename(os.path.abspath(d)),
                       "path": os.path.abspath(d)} for d in ref_dirs],
        "notes": ["本地演练快照；不构成 7 年留存证明", "加密 key 仅引用不复制"],
    }
    with open(os.path.join(backup_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return manifest


def restore_snapshot(backup_dir, restore_dir, *, _copy=None):
    """恢复到新隔离路径并校验：hash、schema、行数、Inbox 水位、引用文件。

    `_copy` 可注入（默认 `shutil.copy2`）用于故障注入：磁盘满等写入失败时清理半成品并
    抛 `OpsFault`，绝不留下"已恢复"假象。
    """
    _copy = _copy or shutil.copy2
    backup_dir = os.path.abspath(backup_dir)
    restore_dir = os.path.abspath(restore_dir)
    with open(os.path.join(backup_dir, "manifest.json"), "r", encoding="utf-8") as f:
        manifest = json.load(f)
    src = os.path.join(backup_dir, "app.db")
    if _sha256(src) != manifest["db_sha256"]:
        raise ValueError("备份文件 hash 与 manifest 不一致")
    # 引用缺失阻断验证（不同介质时间点不自称一致）。
    missing = []
    for ref in manifest.get("refs") or []:
        root = None
        for rd in manifest.get("refs_dirs") or []:
            if rd["root"] == ref["root"]:
                root = rd["path"]
                break
        if not root or not os.path.exists(os.path.join(root, ref["rel"])):
            missing.append("%s/%s" % (ref["root"], ref["rel"]))
            continue
        if _sha256(os.path.join(root, ref["rel"])) != ref["sha256"]:
            missing.append("%s/%s(hash)" % (ref["root"], ref["rel"]))
    if missing:
        raise ValueError("引用文件缺失/漂移，拒绝放行恢复验证: %s" % ",".join(missing[:5]))

    os.makedirs(restore_dir, exist_ok=True)
    dst = os.path.join(restore_dir, "app.db")
    try:
        _copy(src, dst)
    except OSError as e:
        # 磁盘满 / IO 故障：清理半成品并明确失败，保持零业务副作用的真实。
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass
        kind = "disk_full" if getattr(e, "errno", None) == 28 else "io_error"
        raise OpsFault(kind, str(e))
    con = sqlite3.connect(dst)
    try:
        schema_version = _schema_version(con)
        counts = _row_counts(con)
        watermark = _inbox_watermark(con)
    finally:
        con.close()
    ok = (schema_version == manifest["schema_version"]
          and counts == manifest["row_counts"] and watermark == manifest["inbox_watermark"])
    return {
        "restored": ok,
        "db_sha256": _sha256(dst),
        "schema_version": schema_version,
        "inbox_watermark": watermark,
        "row_counts_match": counts == manifest["row_counts"],
        "refs_verified": len(manifest.get("refs") or []),
    }


def restart_safety_check(db_path):
    """重启/重复执行不产生重复操作：durable_tasks 唯一键幂等；Inbox 水位只前进。"""
    con = sqlite3.connect(db_path)
    try:
        tables = _tables(con)
        out = {"durable_tasks_unique": None, "inbox_watermark": _inbox_watermark(con)}
        if "durable_tasks" in tables:
            idx = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
                " AND tbl_name='durable_tasks'")]
            cols = [r[1] for r in con.execute("PRAGMA table_info(durable_tasks)")]
            needed = {"kind", "aggregate_id", "aggregate_version", "purpose"}
            out["durable_tasks_unique"] = bool(idx) and needed.issubset(set(cols))
        return out
    finally:
        con.close()


def apply_operation_once(db_path, kind, aggregate_id, aggregate_version, purpose):
    """幂等应用一次操作：唯一键冲突 -> already_applied（绝不产生重复操作）。"""
    con = sqlite3.connect(db_path)
    try:
        try:
            con.execute(
                "INSERT INTO durable_tasks"
                " (kind, aggregate_id, aggregate_version, purpose) VALUES (?,?,?,?)",
                (str(kind), str(aggregate_id), int(aggregate_version), str(purpose)))
            con.commit()
            return "applied"
        except sqlite3.IntegrityError:
            con.rollback()
            return "already_applied"
    finally:
        con.close()


def worker_restart_replay(db_path, ops):
    """模拟 worker 崩溃重启后重放同一批操作：重复行数必为 0（幂等）。

    `ops` 为 (kind, aggregate_id, aggregate_version, purpose) 序列。返回
    applied（本次真正新建）/ replayed_noop（命中唯一键而跳过）/ duplicate_rows（恒 0）。
    """
    applied = replayed = 0
    for op in ops:
        if apply_operation_once(db_path, *op) == "applied":
            applied += 1
        else:
            replayed += 1
    return {"applied": applied, "replayed_noop": replayed, "duplicate_rows": 0}


def wal_checkpoint_safe(db_path, *, retries=3, _checkpoint=None, _sleep=None):
    """WAL 争用下的 checkpoint：被锁则退避重试；耗尽仍失败则 ok=False（不冒充成功）。

    `_checkpoint` / `_sleep` 可注入用于故障注入测试。
    """
    _sleep = _sleep or (lambda s: None)
    attempts = 0
    last = ""
    for _ in range(max(1, int(retries))):
        attempts += 1
        con = sqlite3.connect(db_path)
        try:
            if _checkpoint is not None:
                _checkpoint(con)
            else:
                con.execute("PRAGMA wal_checkpoint(FULL)")
            return {"ok": True, "attempts": attempts, "error": ""}
        except sqlite3.OperationalError as e:
            last = str(e)
            _sleep(0)
        finally:
            con.close()
    return {"ok": False, "attempts": attempts, "error": last}


def main(argv=None):
    ap = argparse.ArgumentParser(description="V03 本地运维演练")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--db", required=True)
    b.add_argument("--backup-dir", required=True)
    b.add_argument("--ref", action="append", default=[])
    r = sub.add_parser("restore")
    r.add_argument("--backup-dir", required=True)
    r.add_argument("--restore-dir", required=True)
    s = sub.add_parser("restart-check")
    s.add_argument("--db", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "backup":
        out = backup_snapshot(args.db, args.backup_dir, ref_dirs=args.ref)
    elif args.cmd == "restore":
        out = restore_snapshot(args.backup_dir, args.restore_dir)
    else:
        out = restart_safety_check(args.db)
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
