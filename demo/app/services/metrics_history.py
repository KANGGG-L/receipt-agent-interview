# -*- coding: utf-8 -*-
"""H05 历史指标回算（版本化、unknown 保留）。

原则：
- 只从权威快照 + 不可变事件重建；缺事件/缺 AI 快照 → unknown，**不按审批状态推断编辑**。
- v1 报告冻结：本模块只读旧报告并在输出里引用，不修改旧文件。
- v2 报告带 `recomputed_at` / `as_of` / `source_snapshot_sha256` / 指标定义版本。
"""

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone

METRIC_DEF_VERSION = "h05.metricdef.v1"
REPORT_SCHEMA = "h05.history-metrics.v2"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro(path):
    con = sqlite3.connect("file:%s?mode=ro" % os.path.abspath(path), uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con, name):
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def build_recomputed_report(db_path, as_of=None, old_report=None, tenant=None):
    """返回 v2 回算报告；unknown 一律 None，不写 0。"""
    snap = _sha256(db_path)
    con = _ro(db_path)
    rows = []
    unknown = comparable = 0
    try:
        if not _has_table(con, "receipts"):
            return _empty_report(db_path, snap, as_of, old_report, tenant)
        has_events = _has_table(con, "review_events")
        tf, tp = ((" AND r.tenant_id=?", (tenant,)) if tenant else ("", ()))
        receipts = con.execute(
            "SELECT r.id, r.tenant_id, r.status, r.ai_prefill_json, r.audit_json"
            " FROM receipts r WHERE 1=1" + tf + " ORDER BY r.id", tp)
        for r in receipts:
            rid = int(r["id"])
            reasons = []
            metrics = None
            kinds = {}
            if has_events:
                for ev in con.execute(
                        "SELECT event_kind, COUNT(*) c FROM review_events"
                        " WHERE receipt_id=? GROUP BY event_kind", (rid,)):
                    kinds[ev["event_kind"]] = int(ev["c"])
            has_ai = bool((r["ai_prefill_json"] or "").strip()) or bool(
                (r["audit_json"] or "").strip())
            if not kinds:
                reasons.append("no_review_events")
            if r["status"] == "approved" and not kinds.get("approved"):
                # 审批状态存在但事件缺失：不可据此推断编辑历史。
                reasons.append("approval_event_missing")
            if not has_ai:
                reasons.append("no_ai_snapshot")
            if reasons:
                unknown += 1
                status = "unknown"
            else:
                comparable += 1
                status = "comparable"
                metrics = {
                    "saved_events": kinds.get("saved", 0),
                    "submitted_events": kinds.get("submitted", 0),
                    "approved_events": kinds.get("approved", 0),
                    "reversed_events": kinds.get("reversed", 0),
                }
            rows.append({
                "receipt_id": rid,
                "tenant_id": r["tenant_id"] or "",
                "receipt_status": r["status"],
                "status": status,
                "reasons": reasons,
                "metrics": metrics,
            })
    finally:
        con.close()
    return {
        "schema": REPORT_SCHEMA,
        "metric_definition_version": METRIC_DEF_VERSION,
        "recomputed_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of or "",
        "source_snapshot_sha256": snap,
        "tenant": tenant or "",
        "frozen_previous": old_report,
        "summary": {"receipts": len(rows), "comparable": comparable,
                    "unknown": unknown},
        "rows": rows,
    }


def _empty_report(db_path, snap, as_of, old_report, tenant):
    return {
        "schema": REPORT_SCHEMA,
        "metric_definition_version": METRIC_DEF_VERSION,
        "recomputed_at": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of or "",
        "source_snapshot_sha256": snap,
        "tenant": tenant or "",
        "frozen_previous": old_report,
        "summary": {"receipts": 0, "comparable": 0, "unknown": 0},
        "rows": [],
        "reasons": ["receipts_table_missing"],
    }


def load_old_report(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
