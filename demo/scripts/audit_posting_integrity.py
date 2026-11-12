# -*- coding: utf-8 -*-
"""H01 历史只读影响清单（C-HISTORY）。

只读打开一个**已授权的历史快照副本**，按租户/对象检查账本不变量，输出脱敏
manifest（JSON）。本工具不写任何表、不读真实业务库；调用方负责提供副本路径。

分类口径（与 H03/H04 交接）：
- rebuildable       事实齐全，可按来源重建；
- owner_adjudication 有明确差异但需 owner 裁决（不自动修正）；
- non_rebuildable   缺关键历史事实（事件/行稳定 ID 等），不得按位置猜；
- unknown           证据缺失或数值未知——**绝不写 0**。

manifest 绑定 snapshot_sha256 + rule_version；若运行期间文件被改动，直接失败。
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

RULE_VERSION = "h01.v1"


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro_connect(path):
    uri = "file:%s?mode=ro" % os.path.abspath(path)
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def _has_table(con, name):
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def _columns(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def _tenant_filter(tenant):
    if not tenant:
        return "", ()
    return " AND tenant_id=?", (tenant,)


def _entry(objects, counts, kind, obj_id, tenant, cls, reason, downstream=None):
    objects.append({
        "object_kind": kind,
        "object_id": int(obj_id),
        "tenant_id": tenant or "",
        "class": cls,
        "reason": reason,
        "downstream": downstream or {},
    })
    counts[cls] = counts.get(cls, 0) + 1


def build_manifest(db_path, tenant=None, rule_version=RULE_VERSION):
    """返回脱敏 manifest；不修改数据库。"""
    snap_before = _sha256(db_path)
    con = _ro_connect(db_path)
    absent = []
    objects = []
    counts = {}
    invariants = {}
    try:
        for t in ("receipts", "receipt_items", "review_events", "posting_headers",
                  "stock_movements", "batch_allocations", "credit_documents",
                  "credit_entries", "credit_allocations", "supplier_refunds",
                  "suppliers"):
            if not _has_table(con, t):
                absent.append(t)
        if "receipts" not in absent:
            _check_receipts(con, tenant, objects, counts, invariants)
            _check_postings(con, tenant, objects, counts, invariants)
            _check_credits(con, tenant, objects, counts, invariants)
        else:
            invariants["receipts_table_missing"] = True
    finally:
        con.close()
    snap_after = _sha256(db_path)
    if snap_before != snap_after:
        raise RuntimeError("快照在审计期间被修改：%s" % db_path)
    return {
        "schema": "h01-impact-manifest.v1",
        "rule_version": rule_version,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "tenant": tenant or "",
        "snapshot": {"file": os.path.basename(db_path), "sha256": snap_before,
                     "size": os.path.getsize(db_path)},
        "absent_tables": absent,
        "counts": counts,
        "invariants": invariants,
        "objects": objects,
    }


def _check_receipts(con, tenant, objects, counts, invariants):
    tf, tp = _tenant_filter(tenant)
    rows = con.execute(
        "SELECT id, tenant_id, status, image_path, total_amount, items_json,"
        " deleted_at, reversed_at FROM receipts WHERE 1=1" + tf + " ORDER BY id", tp)
    checked = 0
    for r in rows:
        checked += 1
        rid = r["id"]
        ten = r["tenant_id"]
        if not (r["image_path"] or "").strip():
            _entry(objects, counts, "receipt", rid, ten, "unknown",
                   "missing_source_image")
        if r["total_amount"] is None:
            _entry(objects, counts, "receipt", rid, ten, "unknown",
                   "unknown_total_amount")
        # 历史行没有稳定 line_uuid：不按数组位置猜，标记不可重建。
        if _has_table(con, "receipt_items"):
            nul = con.execute(
                "SELECT COUNT(*) FROM receipt_items WHERE receipt_id=?"
                " AND line_uuid IS NULL", (rid,)).fetchone()[0]
            if nul:
                _entry(objects, counts, "receipt", rid, ten, "non_rebuildable",
                       "line_uuid_missing", {"lines_without_uuid": int(nul)})
            unknown_qty = con.execute(
                "SELECT COUNT(*) FROM receipt_items WHERE receipt_id=?"
                " AND quantity IS NULL", (rid,)).fetchone()[0]
            if unknown_qty:
                _entry(objects, counts, "receipt", rid, ten, "unknown",
                       "unknown_quantity", {"lines": int(unknown_qty)})
        # 审批事实缺失：无法推断历史编辑/审批版本。
        if r["status"] == "approved" and _has_table(con, "review_events"):
            ev = con.execute(
                "SELECT COUNT(*) FROM review_events WHERE receipt_id=?"
                " AND event_kind='approved'", (rid,)).fetchone()[0]
            if ev == 0:
                _entry(objects, counts, "receipt", rid, ten, "non_rebuildable",
                       "approval_event_missing")
    invariants["receipts_checked"] = checked


def _check_postings(con, tenant, objects, counts, invariants):
    if not _has_table(con, "posting_headers"):
        return
    tf, tp = _tenant_filter(tenant)
    rows = con.execute(
        "SELECT id, tenant_id, receipt_id, action, status FROM posting_headers"
        " WHERE 1=1" + tf + " ORDER BY id", tp)
    checked = 0
    for r in rows:
        checked += 1
        pid = r["id"]
        ten = r["tenant_id"]
        exists = con.execute("SELECT 1 FROM receipts WHERE id=?",
                             (r["receipt_id"],)).fetchone()
        if not exists:
            _entry(objects, counts, "posting", pid, ten, "non_rebuildable",
                   "receipt_missing")
            continue
        moves = 0
        allocs = 0
        if _has_table(con, "stock_movements"):
            moves = con.execute(
                "SELECT COUNT(*) FROM stock_movements WHERE posting_id=?",
                (pid,)).fetchone()[0]
            if moves:
                allocs = con.execute(
                    "SELECT COUNT(*) FROM batch_allocations WHERE movement_id IN"
                    " (SELECT id FROM stock_movements WHERE posting_id=?)",
                    (pid,)).fetchone()[0]
        _entry(objects, counts, "posting", pid, ten, "rebuildable",
               "facts_present", {"movements": int(moves), "allocations": int(allocs)})
    invariants["postings_checked"] = checked


def _check_credits(con, tenant, objects, counts, invariants):
    if not _has_table(con, "credit_documents"):
        return
    tf, tp = _tenant_filter(tenant)
    rows = con.execute(
        "SELECT id, tenant_id, supplier_id, face_minor, state FROM credit_documents"
        " WHERE 1=1" + tf + " ORDER BY id", tp)
    checked = 0
    for r in rows:
        checked += 1
        cid = r["id"]
        ten = r["tenant_id"]
        if _has_table(con, "suppliers"):
            sup = con.execute("SELECT 1 FROM suppliers WHERE id=?",
                              (r["supplier_id"],)).fetchone()
            if not sup:
                _entry(objects, counts, "credit", cid, ten, "owner_adjudication",
                       "supplier_missing")
        if _has_table(con, "credit_entries"):
            used = con.execute(
                "SELECT COALESCE(SUM(amount_minor),0) FROM credit_entries"
                " WHERE credit_id=?", (cid,)).fetchone()[0]
            if int(used) != int(r["face_minor"]):
                # 金额不进 manifest（脱敏）；只记「账实不符」事实与条目数。
                _entry(objects, counts, "credit", cid, ten, "owner_adjudication",
                       "credit_balance_mismatch", {"entries_present": True})
        if _has_table(con, "credit_allocations"):
            alloc = con.execute(
                "SELECT COALESCE(SUM(amount_minor),0) FROM credit_allocations"
                " WHERE credit_id=? AND reversal_of IS NULL", (cid,)).fetchone()[0]
            if int(alloc) > int(r["face_minor"]):
                _entry(objects, counts, "credit", cid, ten, "owner_adjudication",
                       "credit_overallocated")
    invariants["credits_checked"] = checked


def main(argv=None):
    ap = argparse.ArgumentParser(description="H01 历史只读影响清单")
    ap.add_argument("--db", required=True, help="已授权快照副本路径（只读）")
    ap.add_argument("--out", required=True, help="manifest 输出路径")
    ap.add_argument("--tenant", default="", help="仅审计指定租户")
    ap.add_argument("--rule-version", default=RULE_VERSION)
    args = ap.parse_args(argv)
    try:
        manifest = build_manifest(args.db, tenant=args.tenant or None,
                                  rule_version=args.rule_version)
    except RuntimeError as e:
        sys.stderr.write(str(e) + "\n")
        return 2
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
