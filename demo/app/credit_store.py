# -*- coding: utf-8 -*-
"""L05 信用台账查询/余额接口（v7 credit_documents/credit_entries + L06 credit_allocations）。

口径（SP03/ADR03/P03 §2）：
- 可用信用 = face − 有效抵扣分配 − 退款占用/已收（pending 先占用；reversal 反冲）。
- 未分配贷项不静默 paid；已付原单也保信用而非负 payment（G05）。
- 同来源 posting 唯一发行：approve 幂等（409）+ 本层按 (source, original_receipt_id) 查重。
- 本模块只读聚合 + 幂等查询；写侧仅 approve（发行）与 L06/L07（分配/退款）。
"""

from sqlalchemy import text

ENTRY_OCCUPIES = ("allocated", "refund_pending", "refund_received")
ENTRY_RELEASES = ("reversal",)


def _engine():
    from app import db
    if getattr(db, "_engine", None) is None:
        db._make_engine()
    return db._engine


def credit_balances(tenant_id, supplier_id):
    """供应商信用余额（minor 整数）。返回聚合 + 明细；无信用返回全零。"""
    eng = _engine()
    with eng.connect() as conn:
        docs = conn.execute(text(
            "SELECT id, face_minor, currency, source, original_receipt_id, state,"
            " created_at FROM credit_documents"
            " WHERE tenant_id=:t AND supplier_id=:s AND state='active' ORDER BY id"),
            {"t": str(tenant_id), "s": int(supplier_id)}).fetchall()
        doc_ids = [int(d[0]) for d in docs]
        face_total = sum(int(d[1]) for d in docs)
        occupied = 0
        if doc_ids:
            # 单一事实源原则：抵扣占用=credit_allocations（reversal_of IS NULL，撤销行
            # 本身就是反冲事实不参与占用）；退款占用/已收=credit_entries（L07 写入，
            # reversal entries 反冲）。两条轨道各管各的域，不重复相加。
            alloc_rows = conn.execute(text(
                "SELECT COALESCE(SUM(CASE WHEN reversal_of IS NULL THEN amount_minor"
                " ELSE -amount_minor END),0) FROM credit_allocations"
                " WHERE tenant_id=:t AND credit_id IN %s"
                % ("(%s)" % ",".join(str(int(i)) for i in doc_ids))),
                {"t": str(tenant_id)}).fetchone()
            occupied += int(alloc_rows[0] or 0)
            refund_rows = conn.execute(text(
                "SELECT COALESCE(SUM(CASE WHEN event_kind IN ('refund_pending','refund_received')"
                " THEN amount_minor ELSE 0 END),0),"
                " COALESCE(SUM(CASE WHEN event_kind='reversal' THEN amount_minor ELSE 0 END),0)"
                " FROM credit_entries WHERE tenant_id=:t AND credit_id IN %s"
                % ("(%s)" % ",".join(str(int(i)) for i in doc_ids))),
                {"t": str(tenant_id)}).fetchone()
            occupied += int(refund_rows[0] or 0) - int(refund_rows[1] or 0)
    occupied = max(occupied, 0)
    return {
        "supplier_id": int(supplier_id),
        "face_total_minor": face_total,
        "allocated_refund_occupied_minor": occupied,
        "available_minor": face_total - occupied,
        "document_count": len(docs),
        "documents": [
            {"credit_id": int(d[0]), "face_minor": int(d[1]), "currency": d[2],
             "source": d[3], "original_receipt_id": d[4], "state": d[5],
             "created_at": d[6]} for d in docs],
    }


def find_by_source(tenant_id, source, original_receipt_id):
    """同来源 posting 唯一发行查询（发行幂等的读侧实现）。"""
    with _engine().connect() as conn:
        row = conn.execute(text(
            "SELECT id, face_minor, state FROM credit_documents"
            " WHERE tenant_id=:t AND source=:s AND original_receipt_id=:r"),
            {"t": str(tenant_id), "s": str(source), "r": int(original_receipt_id)}).fetchone()
    return {"credit_id": int(row[0]), "face_minor": int(row[1]), "state": row[2]} if row else None


def credit_allocation_map(tenant_id, receipt_ids=None):
    """{采购单 id: 已按信用抵扣的港币金额}（reversal_of IS NULL 的有效分配）。

    compute_payables 与付款 allocation_map 同口径合并（L06：应付= gross − 有效付款
    − 有效信用分配）。金额以 minor/100 折港元浮点（与 payment_allocations 一致）。
    """
    sql = ("SELECT target_receipt_id, SUM(CASE WHEN reversal_of IS NULL"
           " THEN amount_minor ELSE -amount_minor END) FROM credit_allocations"
           " WHERE tenant_id=:t")
    params = {"t": str(tenant_id)}
    if receipt_ids is not None:
        ids = [int(x) for x in receipt_ids]
        if not ids:
            return {}
        sql += " AND target_receipt_id IN (%s)" % ",".join(str(i) for i in ids)
    sql += " GROUP BY target_receipt_id"
    with _engine().connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return {int(r[0]): round(int(r[1] or 0) / 100.0, 2) for r in rows}
