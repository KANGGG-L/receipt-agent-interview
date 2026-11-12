# -*- coding: utf-8 -*-
"""H03 历史修正预览与 owner 裁决工作单（只读，C-HISTORY）。

输入：H01 的 manifest（或直接由快照副本生成 manifest）+ 快照副本。
输出：逐对象 before/after（金额一律掩码）、理由、原单/批次引用、下游引用、
拟采取动作（adjustment / reversal / owner_reference_fix / noop / pending）与
owner 裁决状态。**不写库、不自动分配信用、不把 unknown 当 0。**

与 H04 的边界：本工具只产出计划；任何 apply 均在副本演练工具（H04）中进行。
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from audit_posting_integrity import build_manifest, RULE_VERSION as H01_RULE  # noqa: E402

PLAN_VERSION = "h03.v1"

_REASON_TO_ACTION = {
    "credit_overallocated": ("reversal_or_adjustment", "pending", True),
    "credit_balance_mismatch": ("adjustment", "pending", True),
    "supplier_missing": ("owner_reference_fix", "pending", True),
    "unknown_consumed_cost": ("pending_evidence", "blocked", True),
    "unknown_quantity": ("pending_evidence", "blocked", True),
    "unknown_total_amount": ("pending_evidence", "blocked", True),
    "missing_source_image": ("pending_evidence", "blocked", True),
    "line_uuid_missing": ("none_non_rebuildable", "blocked", False),
    "approval_event_missing": ("none_non_rebuildable", "blocked", False),
    "receipt_missing": ("none_non_rebuildable", "blocked", False),
    "facts_present": ("noop", "ready", False),
}


def _ro(path):
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _schema_version(con):
    if not _has_table(con, "schema_version"):
        return None
    try:
        row = con.execute("SELECT MAX(version) FROM schema_version").fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _unknown_consumed_costs(con, tenant):
    """已消耗历史成本未知：qty>0 但 cost_minor 缺失——单独列为 unknown，不补 0。"""
    out = []
    if not _has_table(con, "batch_allocations"):
        return out
    tf, tp = ((" AND m.tenant_id=?", (tenant,)) if tenant else ("", ()))
    rows = con.execute(
        "SELECT a.id, m.tenant_id FROM batch_allocations a"
        " JOIN stock_movements m ON m.id=a.movement_id"
        " WHERE a.cost_minor IS NULL AND a.qty_scaled != 0" + tf, tp)
    for r in rows:
        out.append({"object_kind": "batch_allocation", "object_id": int(r["id"]),
                    "tenant_id": r["tenant_id"], "class": "unknown",
                    "reason": "unknown_consumed_cost", "downstream": {}})
    return out


def _irreversible_facts(con, tenant):
    """不可逆资金事实只记数量，不记金额。"""
    facts = {}
    tf, tp = ((" WHERE tenant_id=?", (tenant,)) if tenant else ("", ()))
    if _has_table(con, "credit_allocations"):
        facts["credit_allocations"] = con.execute(
            "SELECT COUNT(*) FROM credit_allocations" + tf, tp).fetchone()[0]
    if _has_table(con, "supplier_refunds"):
        facts["supplier_refunds"] = con.execute(
            "SELECT COUNT(*) FROM supplier_refunds" + tf, tp).fetchone()[0]
    return {k: int(v) for k, v in facts.items()}


def _masked(value):
    """金额一律掩码；只保留存在性，避免 manifest 泄漏真实数值。"""
    return "***" if value else None


def build_plan(db_path, tenant=None):
    manifest = build_manifest(db_path, tenant=tenant)
    con = _ro(db_path)
    try:
        schema_version = _schema_version(con)
        extra = _unknown_consumed_costs(con, tenant)
        irreversible = _irreversible_facts(con, tenant)
    finally:
        con.close()

    decisions = []
    for obj in list(manifest["objects"]) + extra:
        reason = obj["reason"]
        action, status, requires_owner = _REASON_TO_ACTION.get(
            reason, ("owner_review", "pending", True))
        decisions.append({
            "object_kind": obj["object_kind"],
            "object_id": obj["object_id"],
            "tenant_id": obj.get("tenant_id") or "",
            "class": obj["class"],
            "reason": reason,
            "before": {"present": True, "amount": _masked(True)},
            "after": ({"present": True, "amount": _masked(True)}
                      if action in ("adjustment", "reversal_or_adjustment")
                      else None),
            "action": action,
            "status": status,
            "requires_owner": requires_owner,
            "source_refs": {"receipt_or_batch": True},
            "downstream": obj.get("downstream") or {},
        })

    summary = {}
    for d in decisions:
        key = (d["class"], d["action"], d["status"])
        summary[str(key)] = summary.get(str(key), 0) + 1

    return {
        "schema": "h03-repair-plan.v1",
        "plan_version": PLAN_VERSION,
        "rule_version": H01_RULE,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant": tenant or "",
        "snapshot": manifest["snapshot"],
        "history_version": schema_version,
        "irreversible_facts": irreversible,
        "summary": summary,
        "decisions": decisions,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="H03 历史修正预览（只读）")
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tenant", default="")
    args = ap.parse_args(argv)
    plan = build_plan(args.db, tenant=args.tenant or None)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
