# -*- coding: utf-8 -*-
"""付款分配、应付、对账任务的存储与计算。

表在首次使用时建（CREATE TABLE IF NOT EXISTS，数据库被重建后会重新检查），租户列必备，
所有读写按租户查询。

金额口径：应付、付款、对账一律用港币。外币单据按入账时的汇率快照折算（见 units_store）。
"""

import json
import logging
import threading
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import text

from app import db, units_store
from app.store_util import Gate

logger = logging.getLogger("recon_store")

_DDL = [
    """CREATE TABLE IF NOT EXISTS payment_allocations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id VARCHAR(64) NOT NULL,
        payment_id INTEGER NOT NULL,
        receipt_id INTEGER NOT NULL,
        amount FLOAT NOT NULL,
        created_at VARCHAR(32) DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS ix_payment_alloc_receipt ON payment_allocations (tenant_id, receipt_id)",
    "CREATE INDEX IF NOT EXISTS ix_payment_alloc_payment ON payment_allocations (tenant_id, payment_id)",
    """CREATE TABLE IF NOT EXISTS supplier_aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tenant_id VARCHAR(64) NOT NULL,
        supplier_id INTEGER NOT NULL,
        alias_name VARCHAR(200) NOT NULL,
        source VARCHAR(32) DEFAULT 'merge',
        created_at VARCHAR(32) DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS ix_supplier_alias_sup ON supplier_aliases (tenant_id, supplier_id)",
    """CREATE TABLE IF NOT EXISTS recon_tasks (
        id VARCHAR(32) PRIMARY KEY,
        tenant_id VARCHAR(64) NOT NULL,
        supplier_id INTEGER NOT NULL,
        period_type VARCHAR(16) DEFAULT 'month',
        period_start VARCHAR(16) DEFAULT '',
        period_end VARCHAR(16) DEFAULT '',
        status VARCHAR(16) DEFAULT 'reconciling',
        has_statement INTEGER DEFAULT 0,
        statement_total FLOAT DEFAULT 0,
        created_at VARCHAR(32) DEFAULT '',
        confirmed_at VARCHAR(32) DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS ix_recon_tasks_tenant ON recon_tasks (tenant_id, supplier_id)",
    """CREATE TABLE IF NOT EXISTS recon_lines (
        task_id VARCHAR(32) NOT NULL,
        tenant_id VARCHAR(64) NOT NULL,
        line_id INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (task_id, line_id))""",
    "CREATE INDEX IF NOT EXISTS ix_recon_lines_tenant ON recon_lines (tenant_id, task_id)",
]

write_lock = threading.RLock()

_gate = Gate(_DDL, ('payment_allocations', 'supplier_aliases', 'recon_tasks', 'recon_lines'))


def _engine():
    return _gate


def _tid(tenant_id) -> str:
    return str(tenant_id or "default").strip() or "default"


# ====================================================================== 付款分配
def allocation_map(tenant_id, receipt_ids=None) -> Dict[int, float]:
    """{收据 id: 已按分配付出的港币金额}。"""
    sql = "SELECT receipt_id, SUM(amount) FROM payment_allocations WHERE tenant_id=:t"
    params = {"t": _tid(tenant_id)}
    if receipt_ids is not None:
        ids = [int(x) for x in receipt_ids]
        if not ids:
            return {}
        sql += " AND receipt_id IN (%s)" % ",".join(str(i) for i in ids)
    sql += " GROUP BY receipt_id"
    with _engine().connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
    return {int(r[0]): round(float(r[1] or 0.0), 2) for r in rows}


def add_allocations(conn, tenant_id, payment_id, allocations: List[tuple]):
    now = db.now_iso()
    for rid, amt in allocations:
        conn.execute(text(
            "INSERT INTO payment_allocations (tenant_id, payment_id, receipt_id, amount, created_at) "
            "VALUES (:t,:p,:r,:a,:c)"),
            {"t": _tid(tenant_id), "p": int(payment_id), "r": int(rid), "a": float(amt), "c": now})
    invalidate_row_cache()


def payment_allocations(tenant_id, payment_id) -> List[dict]:
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT receipt_id, amount FROM payment_allocations WHERE tenant_id=:t AND payment_id=:p ORDER BY id"),
            {"t": _tid(tenant_id), "p": int(payment_id)}).fetchall()
    return [{"receipt_id": int(r[0]), "amount": round(float(r[1]), 2)} for r in rows]


# ====================================================================== 应付
def _row_total_hkd(r, snap, rate_map, tenant_id) -> Optional[float]:
    cur = units_store.norm_currency(getattr(r, "currency", None))
    rate = units_store.hkd_rate_for_receipt(tenant_id, r.id, cur, snap, rate_map)
    if rate is None:
        return None
    return round(float(r.total_amount or 0.0) * rate, 2)


def _due_date(r, terms_days) -> str:
    if r.expected_pay_date:
        return r.expected_pay_date
    if terms_days and r.receipt_date:
        try:
            d = datetime.strptime(r.receipt_date[:10], "%Y-%m-%d").date()
            return (d + timedelta(days=int(terms_days))).isoformat()
        except ValueError:
            return ""
    return ""


def supplier_index(tenant_id, suppliers=None):
    """名字归一 key -> 供应商 id。

    优先级：在用供应商的名字 > 合并留下的别名 > 已停用供应商的名字。同一优先级里同名
    指向不同供应商的无法判定，丢弃。
    """
    from app.services.supplier_normalizer import canonical_supplier_key
    suppliers = suppliers if suppliers is not None else db.list_suppliers(
        include_inactive=True, tenant_id=tenant_id)
    best = {}      # key -> (priority, {sid})

    def put(key, sid, prio):
        if not key:
            return
        cur = best.get(key)
        if cur is None or prio > cur[0]:
            best[key] = (prio, {sid})
        elif prio == cur[0]:
            cur[1].add(sid)

    for s in suppliers:
        put(canonical_supplier_key(s.name or ""), s.id, 3 if s.active else 1)
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT supplier_id, alias_name FROM supplier_aliases WHERE tenant_id=:t"),
            {"t": _tid(tenant_id)}).fetchall()
    for sid, alias in rows:
        put(canonical_supplier_key(alias or ""), int(sid), 2)
    return {k: next(iter(v[1])) for k, v in best.items() if len(v[1]) == 1}


def receipt_supplier_id(r, idx) -> Optional[int]:
    from app.services.supplier_normalizer import canonical_supplier_key
    if r.supplier_id is not None:
        return int(r.supplier_id)
    return idx.get(canonical_supplier_key(r.supplier_name or ""))


def compute_payables(tenant_id, today: Optional[str] = None) -> dict:
    """应付清单与供应商汇总，两处共用同一份计算。

    应付 = 已入账（approved）的赊单，扣掉已付金额（付款分配表；早期整单付款没有分配记录
    的按整单已付）。逾期按单据上的到期日，没有就用「单据日期 + 供应商账期天数」，都没有则不判逾期。
    """
    tid = _tid(tenant_id)
    today = today or date.today().isoformat()
    suppliers = db.list_suppliers(include_inactive=True, tenant_id=tid)
    sup_by_id = {s.id: s for s in suppliers}
    idx = supplier_index(tid, suppliers)
    rows = db.list_receipt_rows(tenant_id=tid)
    alloc = allocation_map(tid)
    snap = units_store.receipt_fx_map(tid)
    rate_map = {x["currency"]: x["rate"] for x in units_store.get_fx_rates(tid)}

    by_supplier: Dict[int, dict] = {}
    payables = []
    pending_by_supplier: Dict[int, dict] = {}

    def bucket(sid):
        return by_supplier.setdefault(sid, {
            "receipt_count": 0, "unpaid_credit_total": 0.0, "unpaid_credit_count": 0,
            "overdue_amount": 0.0, "overdue_count": 0, "is_overdue": False,
            "pending_credit_total": 0.0, "pending_credit_count": 0})

    for r in rows:
        sid = receipt_supplier_id(r, idx)
        if sid is not None:
            bucket(sid)["receipt_count"] += 1
        if r.settlement_type != "credit" or sid is None:
            continue
        total = _row_total_hkd(r, snap, rate_map, tid)
        if r.status in ("parsed", "edited", "parsed_with_warnings"):
            b = bucket(sid)
            b["pending_credit_total"] += total or 0.0
            b["pending_credit_count"] += 1
            continue
        if r.status != "approved":
            continue
        legacy_paid = bool(r.payment_id and r.paid_at)
        paid = total if (legacy_paid and r.id not in alloc) else min(alloc.get(r.id, 0.0), total or 0.0)
        outstanding = round(max((total or 0.0) - (paid or 0.0), 0.0), 2)
        sup = sup_by_id.get(sid)
        due = _due_date(r, getattr(sup, "payment_terms_days", None))
        overdue = outstanding > 0 and bool(due) and due < today
        if outstanding <= 0:
            status = "paid"
        elif paid and paid > 0:
            status = "partial"
        else:
            status = "unpaid"
        row = {"receipt_id": r.id, "supplier_id": sid,
               "supplier_name": sup.name if sup else (r.supplier_name or ""),
               "receipt_date": r.receipt_date or "", "due_date": due,
               "currency": units_store.norm_currency(getattr(r, "currency", None)),
               "total_amount": float(r.total_amount or 0.0),
               "total_hkd": total, "paid_amount": round(paid or 0.0, 2),
               "outstanding_amount": outstanding, "payment_status": status,
               "is_overdue": overdue,
               "overdue_days": (date.fromisoformat(today) - date.fromisoformat(due)).days if overdue else 0,
               "fx_missing": total is None}
        payables.append(row)
        if outstanding > 0:
            b = bucket(sid)
            b["unpaid_credit_total"] += outstanding
            b["unpaid_credit_count"] += 1
            if overdue:
                b["overdue_amount"] += outstanding
                b["overdue_count"] += 1
                b["is_overdue"] = True
    for b in by_supplier.values():
        for k in ("unpaid_credit_total", "overdue_amount", "pending_credit_total"):
            b[k] = round(b[k], 2)
    return {"by_supplier": by_supplier, "payables": payables}


def supplier_stat(tenant_id, supplier_id, computed=None) -> dict:
    c = computed or compute_payables(tenant_id)
    b = c["by_supplier"].get(int(supplier_id))
    if b is None:
        return {"receipt_count": 0, "unpaid_credit_total": 0.0, "unpaid_credit_count": 0,
                "overdue_amount": 0.0, "overdue_count": 0, "is_overdue": False,
                "pending_credit_total": 0.0, "pending_credit_count": 0}
    return b


def receipt_payment_info(tenant_id, row, alloc=None) -> dict:
    """单据级付款状态（给 build_row / build_detail 用）。非赊单、未入账返回空。"""
    out = {"paid_amount": 0.0, "outstanding_amount": 0.0}
    if row.settlement_type != "credit" or row.status != "approved":
        return out
    tid = _tid(tenant_id or getattr(row, "tenant_id", None))
    total = float(row.total_amount or 0.0)
    if row.payment_id and row.paid_at:
        out["paid_amount"] = total
        return out
    a = alloc if alloc is not None else allocation_map(tid, [row.id])
    paid = a.get(row.id, 0.0)
    out["paid_amount"] = round(paid, 2)
    out["outstanding_amount"] = round(max(total - paid, 0.0), 2)
    return out


# ====================================================================== 对账任务
def create_task(tenant_id, supplier_id, period_type, start, end, lines: List[dict],
                has_statement=False, statement_total=0.0) -> str:
    tid = _tid(tenant_id)
    task_id = db.new_id()
    with _engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO recon_tasks (id, tenant_id, supplier_id, period_type, period_start, period_end, "
            "status, has_statement, statement_total, created_at) VALUES (:i,:t,:s,:pt,:ps,:pe,'reconciling',:h,:st,:c)"),
            {"i": task_id, "t": tid, "s": int(supplier_id), "pt": period_type or "month",
             "ps": start or "", "pe": end or "", "h": 1 if has_statement else 0,
             "st": float(statement_total or 0.0), "c": db.now_iso()})
        for ln in lines:
            conn.execute(text(
                "INSERT INTO recon_lines (task_id, tenant_id, line_id, payload) VALUES (:i,:t,:l,:p)"),
                {"i": task_id, "t": tid, "l": int(ln["id"]),
                 "p": json.dumps(ln, ensure_ascii=False)})
    return task_id


def get_task(tenant_id, task_id) -> Optional[dict]:
    """取任务与全部明细；不存在或不属于本租户返回 None。"""
    tid = _tid(tenant_id)
    with _engine().connect() as conn:
        t = conn.execute(text(
            "SELECT id, supplier_id, period_type, period_start, period_end, status, has_statement, "
            "statement_total, created_at, confirmed_at FROM recon_tasks WHERE id=:i AND tenant_id=:t"),
            {"i": str(task_id), "t": tid}).fetchone()
        if t is None:
            return None
        lines = conn.execute(text(
            "SELECT payload FROM recon_lines WHERE task_id=:i AND tenant_id=:t ORDER BY line_id"),
            {"i": str(task_id), "t": tid}).fetchall()
    return {"id": t[0], "tenant_id": tid, "supplier_id": int(t[1]), "period_type": t[2],
            "period_start": t[3], "period_end": t[4], "status": t[5],
            "has_statement": bool(t[6]), "statement_total": float(t[7] or 0.0),
            "created_at": t[8], "confirmed_at": t[9],
            "lines": [json.loads(r[0]) for r in lines]}


def list_tasks(tenant_id, supplier_id=None) -> List[dict]:
    tid = _tid(tenant_id)
    sql = "SELECT id FROM recon_tasks WHERE tenant_id=:t"
    params = {"t": tid}
    if supplier_id:
        sql += " AND supplier_id=:s"
        params["s"] = int(supplier_id)
    sql += " ORDER BY created_at DESC, rowid DESC"
    with _engine().connect() as conn:
        ids = [r[0] for r in conn.execute(text(sql), params).fetchall()]
    return [get_task(tid, i) for i in ids]


def save_line(tenant_id, task_id, line: dict):
    with _engine().begin() as conn:
        conn.execute(text(
            "UPDATE recon_lines SET payload=:p WHERE task_id=:i AND tenant_id=:t AND line_id=:l"),
            {"p": json.dumps(line, ensure_ascii=False), "i": str(task_id),
             "t": _tid(tenant_id), "l": int(line["id"])})


def save_lines(tenant_id, task_id, lines: List[dict]):
    with _engine().begin() as conn:
        for line in lines:
            conn.execute(text(
                "UPDATE recon_lines SET payload=:p WHERE task_id=:i AND tenant_id=:t AND line_id=:l"),
                {"p": json.dumps(line, ensure_ascii=False), "i": str(task_id),
                 "t": _tid(tenant_id), "l": int(line["id"])})


def set_task_status(tenant_id, task_id, status):
    with _engine().begin() as conn:
        conn.execute(text(
            "UPDATE recon_tasks SET status=:s, confirmed_at=:c WHERE id=:i AND tenant_id=:t"),
            {"s": status, "c": db.now_iso() if status == "confirmed" else "",
             "i": str(task_id), "t": _tid(tenant_id)})


# ====================================================================== 供应商合并
def add_supplier_alias(conn, tenant_id, supplier_id, alias_name, source="merge"):
    conn.execute(text(
        "INSERT INTO supplier_aliases (tenant_id, supplier_id, alias_name, source, created_at) "
        "VALUES (:t,:s,:a,:o,:c)"),
        {"t": _tid(tenant_id), "s": int(supplier_id), "a": alias_name, "o": source, "c": db.now_iso()})


def list_supplier_aliases(tenant_id, supplier_id) -> List[str]:
    with _engine().connect() as conn:
        rows = conn.execute(text(
            "SELECT alias_name FROM supplier_aliases WHERE tenant_id=:t AND supplier_id=:s ORDER BY id"),
            {"t": _tid(tenant_id), "s": int(supplier_id)}).fetchall()
    return [r[0] for r in rows]


def delete_line(tenant_id, task_id, line_id):
    with _engine().begin() as conn:
        conn.execute(text(
            "DELETE FROM recon_lines WHERE task_id=:i AND tenant_id=:t AND line_id=:l"),
            {"i": str(task_id), "t": _tid(tenant_id), "l": int(line_id)})


def add_lines(tenant_id, task_id, lines: List[dict]):
    with _engine().begin() as conn:
        for ln in lines:
            conn.execute(text(
                "INSERT INTO recon_lines (task_id, tenant_id, line_id, payload) VALUES (:i,:t,:l,:p)"),
                {"i": str(task_id), "t": _tid(tenant_id), "l": int(ln["id"]),
                 "p": json.dumps(ln, ensure_ascii=False)})


def update_task_statement(tenant_id, task_id, statement_total):
    with _engine().begin() as conn:
        conn.execute(text(
            "UPDATE recon_tasks SET has_statement=1, statement_total=:s WHERE id=:i AND tenant_id=:t"),
            {"s": float(statement_total or 0.0), "i": str(task_id), "t": _tid(tenant_id)})


# ====================================================================== 列表行用的轻量查询
_row_cache = {"engine": None, "at": 0.0, "tenant": {}}
_ROW_TTL = 2.0


def invalidate_row_cache():
    _row_cache["tenant"].clear()


def _tenant_cache(tenant_id):
    import time
    eng = db._engine
    now = time.time()
    if _row_cache["engine"] is not eng or now - _row_cache["at"] > _ROW_TTL:
        _row_cache["tenant"].clear()
        _row_cache["engine"] = eng
        _row_cache["at"] = now
    tid = _tid(tenant_id)
    c = _row_cache["tenant"].get(tid)
    if c is None:
        terms = {s.id: s.payment_terms_days
                 for s in db.list_suppliers(include_inactive=True, tenant_id=tid)}
        c = {"alloc": allocation_map(tid), "terms": terms}
        _row_cache["tenant"][tid] = c
    return c


def list_row_payment_info(row, today: Optional[str] = None) -> dict:
    """列表行/详情用：{payment_status, paid_amount, outstanding_amount, is_overdue, due_date}。

    只对已入账的赊单有意义；其他返回 None 表示由调用方沿用原逻辑。
    """
    if row.settlement_type != "credit" or row.status != "approved":
        return None
    tid = _tid(getattr(row, "tenant_id", None))
    c = _tenant_cache(tid)
    total = float(row.total_amount or 0.0)
    legacy = bool(row.payment_id and row.paid_at) and row.id not in c["alloc"]
    paid = total if legacy else min(c["alloc"].get(row.id, 0.0), total)
    outstanding = round(max(total - paid, 0.0), 2)
    today = today or date.today().isoformat()
    due = _due_date(row, c["terms"].get(row.supplier_id))
    overdue = outstanding > 0 and bool(due) and due < today
    status = "paid" if outstanding <= 0 else ("partial" if paid > 0 else ("overdue" if overdue else "unpaid"))
    return {"payment_status": status, "paid_amount": round(paid, 2),
            "outstanding_amount": outstanding, "is_overdue": overdue, "due_date": due}
