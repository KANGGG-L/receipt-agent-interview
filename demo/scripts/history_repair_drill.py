# -*- coding: utf-8 -*-
"""H04 历史修正副本演练器（dry-run → apply → reconcile → rollback → reapply）。

安全边界（硬）：
- 只允许操作**演练副本**：数据库同目录必须存在 `.h04-drill-copy` 标记文件；
  否则拒绝（退出码 3）。防止误对真实业务库执行。
- 只写副本内的 `repair_drill_ledger`（演练账本），不触碰任何业务表。
- apply 前做快照 CAS（hash 必须等于计划快照 hash），不一致拒绝（退出码 4）。
- 阈值 0：reconcile 出现任何未解释差异即停止（退出码 2）。
- 原审计/业务事实在 rollback 前后必须逐表 hash 不变。

本工具不是生产修库脚本；真实 apply 属 H06，需另行书面授权。
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from history_repair_plan import build_plan  # noqa: E402

DRILL_VERSION = "h04.v1"
LEDGER = "repair_drill_ledger"
META = "repair_drill_meta"
MARKER = ".h04-drill-copy"
_EXCLUDE = {"schema_version", LEDGER, META, "sqlite_sequence"}


def _sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _guard_copy(db_path):
    d = os.path.dirname(os.path.abspath(db_path))
    if not os.path.exists(os.path.join(d, MARKER)):
        raise PermissionError(
            "拒绝执行：%s 不是演练副本（缺少 %s 标记）" % (d, MARKER))
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)


def _business_digest(con):
    """所有业务表（排除体检账本/序列表）的行级摘要，用于证明未改原审计。"""
    h = hashlib.sha256()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        if t in _EXCLUDE:
            continue
        h.update(t.encode())
        for row in con.execute("SELECT * FROM %s ORDER BY rowid" % t):
            h.update(repr(tuple(row)).encode())
    return h.hexdigest()


def _ensure_ledger(con):
    con.execute(
        "CREATE TABLE IF NOT EXISTS %s ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " operation_key VARCHAR(160) NOT NULL UNIQUE,"
        " object_kind VARCHAR(32) NOT NULL,"
        " object_id INTEGER NOT NULL,"
        " reason VARCHAR(64) NOT NULL,"
        " state VARCHAR(16) NOT NULL,"
        " checkpoint INTEGER NOT NULL DEFAULT 0,"
        " created_at VARCHAR NOT NULL)" % LEDGER)
    con.execute(
        "CREATE TABLE IF NOT EXISTS %s ("
        " id INTEGER PRIMARY KEY CHECK (id=1),"
        " snapshot_sha VARCHAR(64) NOT NULL,"
        " created_at VARCHAR NOT NULL)" % META)
    con.commit()


def _meta_sha(con):
    row = con.execute("SELECT snapshot_sha FROM %s WHERE id=1" % META).fetchone()
    return row[0] if row else None


def _operations(plan):
    return [{
        "operation_key": "%s:%s:%s" % (d["object_kind"], d["object_id"], d["reason"]),
        "object_kind": d["object_kind"],
        "object_id": int(d["object_id"]),
        "reason": d["reason"],
    } for d in plan["decisions"]]


def _now():
    return datetime.now(timezone.utc).isoformat()


def dry_run(db_path):
    _guard_copy(db_path)
    plan = build_plan(db_path)
    ops = _operations(plan)
    return {"mode": "dry_run", "snapshot": plan["snapshot"], "operations": len(ops)}


def apply(db_path, expected_hash, max_ops=0):
    _guard_copy(db_path)
    plan = build_plan(db_path)
    ops = _operations(plan)
    con = sqlite3.connect(db_path)
    try:
        has_ledger = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (LEDGER,)).fetchone() is not None
        meta = _meta_sha(con) if has_ledger else None
        if meta is None:
            # 首次 apply：文件快照必须与计划 hash 一致（CAS）。
            if _sha_file(db_path) != expected_hash:
                raise ValueError("快照 hash 与计划不一致（CAS 拒绝）")
        elif meta != expected_hash:
            # 中断恢复/回滚后重做：以演练元数据锚定的快照 hash 为准。
            raise ValueError("演练快照锚点与计划不一致（CAS 拒绝）")
        before_digest = _business_digest(con)
        _ensure_ledger(con)
        if meta is None:
            con.execute("INSERT INTO %s (id, snapshot_sha, created_at) VALUES (1,?,?)"
                        % META, (expected_hash, _now()))
            con.commit()
        applied = 0
        for i, op in enumerate(ops):
            if max_ops and applied >= max_ops:
                break
            cur = con.execute(
                "INSERT OR IGNORE INTO %s (operation_key, object_kind, object_id,"
                " reason, state, checkpoint, created_at) VALUES (?,?,?,?,?,?,?)" % LEDGER,
                (op["operation_key"], op["object_kind"], op["object_id"],
                 op["reason"], "applied", i + 1, _now()))
            if cur.rowcount:
                applied += 1
            con.commit()
        after_digest = _business_digest(con)
        if before_digest != after_digest:
            con.rollback()
            raise RuntimeError("演练账本写入意外改变了业务表")
        total = con.execute("SELECT COUNT(*) FROM %s" % LEDGER).fetchone()[0]
        return {"mode": "apply", "applied_now": applied, "ledger_rows": int(total),
                "planned": len(ops), "business_digest": after_digest}
    finally:
        con.close()


def reconcile(db_path):
    _guard_copy(db_path)
    plan = build_plan(db_path)
    planned = len(_operations(plan))
    con = sqlite3.connect(db_path)
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                           " AND name=?", (LEDGER,)).fetchone():
            return {"mode": "reconcile", "planned": planned, "applied": 0,
                    "unexplained": planned, "ok": planned == 0}
        applied = con.execute(
            "SELECT COUNT(*) FROM %s WHERE state='applied'" % LEDGER).fetchone()[0]
        unexplained = planned - int(applied)
        return {"mode": "reconcile", "planned": planned, "applied": int(applied),
                "unexplained": int(unexplained), "ok": unexplained == 0}
    finally:
        con.close()


def rollback(db_path, expected_digest=None):
    _guard_copy(db_path)
    con = sqlite3.connect(db_path)
    try:
        digest_before = _business_digest(con)
        if expected_digest and digest_before != expected_digest:
            raise ValueError("业务表摘要与 apply 后不一致（拒绝回滚）")
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table'"
                       " AND name=?", (LEDGER,)).fetchone():
            con.execute("DELETE FROM %s" % LEDGER)
            con.commit()
        digest_after = _business_digest(con)
        return {"mode": "rollback", "business_digest": digest_after,
                "ledger_rows": 0, "audit_unchanged": digest_after == digest_before}
    finally:
        con.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="H04 副本演练")
    ap.add_argument("--db", required=True)
    ap.add_argument("--mode", required=True,
                    choices=("dry-run", "apply", "reconcile", "rollback"))
    ap.add_argument("--expected-hash", default="")
    ap.add_argument("--expected-digest", default="")
    ap.add_argument("--max-ops", type=int, default=0)
    args = ap.parse_args(argv)
    try:
        if args.mode == "dry-run":
            out = dry_run(args.db)
        elif args.mode == "apply":
            out = apply(args.db, args.expected_hash, max_ops=args.max_ops)
        elif args.mode == "reconcile":
            out = reconcile(args.db)
        else:
            out = rollback(args.db, expected_digest=args.expected_digest or None)
    except PermissionError as e:
        sys.stderr.write(str(e) + "\n")
        return 3
    except (ValueError, RuntimeError) as e:
        sys.stderr.write(str(e) + "\n")
        return 4
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
    if args.mode == "reconcile" and not out.get("ok"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
