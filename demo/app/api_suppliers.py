# -*- coding: utf-8 -*-
"""供应商端点：列表/搜索/CRUD/合并/删除 + 部门 CRUD + 成本报表。"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role, resolve_tenant
from app.services import memory_store
from app.services.memory_store import MemoryVectorDeleteError
from app.i18n_text import master_key
from app.services.supplier_normalizer import canonical_supplier_key

logger = logging.getLogger(__name__)

router = APIRouter()


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)


def _safe_audit(who, action, field, old, new):
    """系统审计（与 api_memory._safe_audit 同口径）：审计失败不阻断已完成的业务动作。"""
    try:
        db.append_system_audit_log(who, action, field, old, new)
    except Exception as e:
        logger.warning("审计写入失败(action=%s field=%s who=%s): %s",
                       action, field, who, e)


# -------------------------------------------------------------
# 供应商
# -------------------------------------------------------------
UNPAID_BASIS = "只算已入账的赊单，已付的金额已扣除；外币按入账时的汇率折成港币。还没审批入账的赊单另列在「待入账赊单」里"


def _supplier_dict(s, tenant_id=None, computed=None):
    from app import recon_store
    stats = recon_store.supplier_stat(tenant_id or "default", s.id, computed)
    return {
        "id": s.id, "name": s.name, "supplier_code": s.supplier_code or "",
        "active": s.active,
        "payment_terms_days": s.payment_terms_days,
        "settlement_pref": s.settlement_pref or "",
        "contact_phone": s.contact_phone or "",
        "notes": s.notes or "",
        "receipt_count": stats["receipt_count"],
        "unpaid_credit_total": stats["unpaid_credit_total"],
        "unpaid_credit_count": stats["unpaid_credit_count"],
        "is_overdue": bool(stats["is_overdue"]),
        "overdue_amount": stats["overdue_amount"],
        "overdue_count": stats["overdue_count"],
        "pending_credit_total": stats["pending_credit_total"],
        "pending_credit_count": stats["pending_credit_count"],
        "unpaid_basis": UNPAID_BASIS,
    }


@router.get("/api/suppliers")
def list_suppliers(request: Request, q: str = "", include_inactive: int = 0):
    # 只读列表对店员开放（收据页供应商联想下拉需要）；增删改仍限 owner
    require_role("staff")(request)
    from app import recon_store
    tenant_id = _tenant_id(request)
    suppliers = db.list_suppliers(include_inactive=bool(include_inactive),
                                  tenant_id=tenant_id)
    computed = recon_store.compute_payables(tenant_id)
    out = []
    for s in suppliers:
        if q:
            folded_q = master_key(q)
            name_hit = bool(folded_q) and folded_q in master_key(s.name or "")
            if not name_hit and q not in s.name and q not in (s.supplier_code or ""):
                continue
        out.append(_supplier_dict(s, tenant_id=tenant_id, computed=computed))
    return {"status": "success", "data": out}


class SupplierBody(BaseModel):
    name: str = ""
    contact_phone: str = None
    notes: str = None
    settlement_pref: str = None
    payment_terms_days: int = None
    active: int = None


@router.post("/api/suppliers")
def create_supplier(body: SupplierBody, request: Request):
    require_role("owner")(request)
    if not body.name:
        return {"status": "error", "msg": "供应商名称不能为空"}
    tenant_id = _tenant_id(request)
    wanted = master_key(body.name)
    if wanted:
        for row in db.list_suppliers(include_inactive=True, tenant_id=tenant_id):
            if master_key(row.name or "") == wanted:
                return {"status": "success", "id": row.id, "folded": True}
    fields = {k: v for k, v in body.model_dump().items() if k != "name" and v is not None}
    sup_id, err = db.create_supplier(body.name, tenant_id=tenant_id, **fields)
    if err:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "error", "msg": err}, status_code=409)
    return {"status": "success", "id": sup_id}


@router.patch("/api/suppliers/{supplier_id}")
def patch_supplier(supplier_id: int, body: SupplierBody, request: Request):
    require_role("owner")(request)
    if db.get_supplier(supplier_id, tenant_id=_tenant_id(request)) is None:
        return {"status": "error", "msg": "供应商不存在"}
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row, err = db.update_supplier(supplier_id, **fields)
    if row is None:
        return {"status": "error", "msg": "供应商不存在"}
    return {"status": "success"}


@router.delete("/api/suppliers/{supplier_id}")
def delete_supplier(supplier_id: int, request: Request):
    """FP-2a：物理删除供应商主档（PDPO s.26 erasure），owner 权限。

    流程（顺序即规格，SPEC FP-2a）：取档 404 → 单据引用守卫 409（有单据一律拒绝，
    含软删单——防「删档后恢复软删单」出现死链）→ 记忆级联（先向量后 DB 行，向量失败
    500 未生效可重试）→ 候选记忆 → SKU 别名 → 主档行 → 审计 → 200。
    Out of scope：单据/付款等业务引用的级联清理（守卫拒绝即不存在该场景）。

    跨租户 supplier_id 与不存在同样回 404（与 patch 同口径，不区分两者）。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    row = db.get_supplier(supplier_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(status_code=404,
                            content={"status": "error", "msg": "供应商不存在"})
    receipt_count = db.count_receipts_for_supplier(row.id, tenant_id=tenant_id)
    if receipt_count > 0:
        # 守卫拒绝：不做任何删除；留一条拒绝审计（可观测，非删除动作本身）
        _safe_audit(_actor(request), "supplier_delete_refused",
                    f"suppliers:{row.id}", {"receipt_count": receipt_count}, "")
        return JSONResponse(status_code=409, content={
            "status": "error", "code": "SUPPLIER_HAS_RECEIPTS",
            "msg": f"该供应商存在 {receipt_count} 张引用单据，不可删除",
            "data": {"receipt_count": receipt_count}})
    sup_name = row.name or ""
    sup_id = row.id
    has_phone = bool(str(row.contact_phone or "").strip())
    try:
        mem = memory_store.delete_memory(vendor=sup_name, supplier_id=sup_id,
                                         tenant_id=tenant_id)
    except MemoryVectorDeleteError as e:
        # vector-first：此刻 DB 行/候选/别名/主档均未删，本次删除未生效、可重试
        logger.warning("[suppliers] 删除失败（向量级联，未生效）supplier=%s: %s",
                       sup_id, e)
        _safe_audit(_actor(request), "vector_delete_failed",
                    f"suppliers:{sup_id}", "", str(e)[:500])
        return JSONResponse(status_code=500, content={
            "status": "error", "code": "VECTOR_DELETE_FAILED",
            "msg": "向量级联删除失败，本次删除未生效，可重试"})
    pending_deleted = db.delete_pending_memory(vendor=sup_name,
                                               supplier_id=sup_id,
                                               tenant_id=tenant_id)
    alias_deleted = db.delete_vendor_sku_alias_for_supplier(sup_id,
                                                            tenant_id=tenant_id)
    db.delete_supplier_master(sup_id, tenant_id=tenant_id)
    # 审计：old 记删除前主档要点（contact_phone 只记有无，手机号本身不进审计），
    # new 记各级联计数；审计失败不阻断（_safe_audit 内已兜底）。
    _safe_audit(_actor(request), "supplier_master_delete",
                f"suppliers:{sup_id}",
                {"name": sup_name, "has_contact_phone": has_phone},
                {"vendor_memory_deleted": mem["affected"],
                 "vectors_deleted": mem["vectors_deleted"],
                 "pending_memory_deleted": pending_deleted,
                 "vendor_sku_alias_deleted": alias_deleted})
    return {"status": "success", "data": {
        "supplier_id": sup_id,
        "vendor_memory_deleted": mem["affected"],
        "vectors_deleted": mem["vectors_deleted"],
        "pending_memory_deleted": pending_deleted,
        "vendor_sku_alias_deleted": alias_deleted,
    }}


def _actor(request: Request) -> str:
    """操作者标识（审计用）：取已解析身份里的 email，匿名兜底 owner。"""
    account = getattr(request.state, "account", {})
    return account.get("email", "owner") if isinstance(account, dict) else "owner"


class MergeBody(BaseModel):
    keep_id: int
    drop_id: int


@router.post("/api/suppliers/merge")
def merge_suppliers(body: MergeBody, request: Request):
    """合并供应商：把 drop 并入 keep。

    单一事务内迁移：收据、付款、供应商别名（SKU 写法）、供应商记忆；把被并入的名字记成别名，
    之后对账和应付按别名也能找到这些单据；写系统审计。
    """
    require_role("owner")(request)
    from sqlalchemy import text
    from app import recon_store
    tenant_id = _tenant_id(request)
    if body.keep_id == body.drop_id:
        return JSONResponse(status_code=400, content={
            "status": "error", "code": "MERGE_SAME_SUPPLIER",
            "msg": "保留的和要合并掉的是同一个供应商，请选两个不同的供应商"})
    keep = db.get_supplier(body.keep_id, tenant_id=tenant_id)
    drop = db.get_supplier(body.drop_id, tenant_id=tenant_id)
    if keep is None or drop is None:
        return JSONResponse(status_code=404, content={
            "status": "error", "code": "SUPPLIER_NOT_FOUND", "msg": "找不到供应商，请刷新页面后重新选择"})
    drop_key = canonical_supplier_key(drop.name or "")
    receipt_ids = [r.id for r in db.list_receipt_rows(tenant_id=tenant_id)
                   if r.supplier_id == drop.id or (
                       drop_key and r.supplier_id is None
                       and canonical_supplier_key(r.supplier_name or "") == drop_key)]
    moved = {}
    with recon_store.write_lock:
        eng = recon_store._engine()
        with eng.begin() as conn:
            for rid in receipt_ids:
                conn.execute(text(
                    "UPDATE receipts SET supplier_id=:k, supplier_name=:n WHERE id=:r AND tenant_id=:t"),
                    {"k": keep.id, "n": keep.name, "r": rid, "t": tenant_id})
            moved["receipts"] = len(receipt_ids)
            moved["payments"] = conn.execute(text(
                "UPDATE payments SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id}).rowcount
            conn.execute(text(
                "DELETE FROM vendor_sku_alias WHERE tenant_id=:t AND supplier_id=:d AND raw_name IN "
                "(SELECT raw_name FROM vendor_sku_alias WHERE tenant_id=:t AND supplier_id=:k)"),
                {"t": tenant_id, "d": drop.id, "k": keep.id})
            moved["sku_aliases"] = conn.execute(text(
                "UPDATE vendor_sku_alias SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id}).rowcount
            moved["memories"] = conn.execute(text(
                "UPDATE vendor_memory SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id}).rowcount
            conn.execute(text(
                "UPDATE pending_memory SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id})
            conn.execute(text(
                "UPDATE recon_tasks SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id})
            conn.execute(text(
                "UPDATE supplier_aliases SET supplier_id=:k WHERE supplier_id=:d AND tenant_id=:t"),
                {"k": keep.id, "d": drop.id, "t": tenant_id})
            recon_store.add_supplier_alias(conn, tenant_id, keep.id, drop.name or "")
            conn.execute(text(
                "UPDATE suppliers SET active=0, notes=:n WHERE id=:d AND tenant_id=:t"),
                {"n": ((drop.notes or "") + " 已合并到「%s」" % keep.name).strip(),
                 "d": drop.id, "t": tenant_id})
    _safe_audit(_actor(request), "supplier_merge", "suppliers:%d" % drop.id,
                {"drop": {"id": drop.id, "name": drop.name}},
                {"keep": {"id": keep.id, "name": keep.name}, "moved": moved})
    return {"status": "success", "msg": "已合并", "data": {"moved": moved}}


# -------------------------------------------------------------
# 部门
# -------------------------------------------------------------
@router.get("/api/departments")
def list_departments(request: Request):
    require_role("staff")(request)
    depts = db.list_departments(tenant_id=_tenant_id(request))
    return {"status": "success",
            "data": [{"id": d.id, "name": d.name, "active": d.active} for d in depts]}


class DeptBody(BaseModel):
    name: str = ""


@router.post("/api/departments")
def create_department(body: DeptBody, request: Request):
    require_role("owner")(request)
    if not body.name:
        return {"status": "error", "msg": "部门名称不能为空"}
    dept_id = db.create_department(body.name, tenant_id=_tenant_id(request))
    return {"status": "success", "id": dept_id}


@router.patch("/api/departments/{dept_id}")
def patch_department(dept_id: int, body: DeptBody, request: Request):
    require_role("owner")(request)
    db.update_department(dept_id, tenant_id=_tenant_id(request), name=body.name)
    return {"status": "success"}


@router.delete("/api/departments/{dept_id}")
def delete_department(dept_id: int, request: Request):
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    # D16：「不得停用最后一个启用部门」按租户独立判定（计数只看本租户部门）
    depts = db.list_departments(tenant_id=tenant_id)
    if len([d for d in depts if d.active]) <= 1:
        return {"status": "error", "msg": "不得停用最后一个启用部门"}
    db.update_department(dept_id, tenant_id=tenant_id, active=0)
    return {"status": "success"}


# -------------------------------------------------------------
# 成本报表
# -------------------------------------------------------------
def _in_period(receipt_date: str, month: str, start_date: str, end_date: str) -> bool:
    """判断 receipt_date 是否落在查询区间（month / start_date-end_date）。"""
    if start_date and end_date:
        return (receipt_date or "") >= start_date and (receipt_date or "") <= end_date
    if month:
        return (receipt_date or "").startswith(month)
    return True


def _prev_period(month: str, start_date: str, end_date: str):
    """根据当前区间推导上期区间（用于四向对比）。返回 (prev_month, prev_start, prev_end) 或 (prev_month, '', '')。"""
    from datetime import datetime, timedelta
    if start_date and end_date:
        try:
            sd = datetime.strptime(start_date, "%Y-%m-%d")
            ed = datetime.strptime(end_date, "%Y-%m-%d")
            delta_days = (ed - sd).days + 1
            prev_end = sd - timedelta(days=1)
            prev_start = prev_end - timedelta(days=delta_days - 1)
            return "", prev_start.strftime("%Y-%m-%d"), prev_end.strftime("%Y-%m-%d")
        except Exception:
            return "", "", ""
    if month and len(month) >= 7:
        try:
            y, m = int(month[:4]), int(month[5:7])
            if m == 1:
                y, m = y - 1, 12
            else:
                m -= 1
            prev_month = f"{y:04d}-{m:02d}"
            return prev_month, "", ""
        except Exception:
            return "", "", ""
    return "", "", ""


def _trend_type(total: float, prev: float) -> str:
    """四向高亮类型：new / up / down / flat（与前端色板对齐）。"""
    if total > 0 and prev == 0:
        return "new"
    delta = total - prev
    if delta > 0.005:
        return "up"
    if delta < -0.005:
        return "down"
    return "flat"


@router.get("/api/cost_report")
def cost_report(request: Request, month: str = "", start_date: str = "",
                end_date: str = "", include_non_approved: int = 0):
    """部门成本报表。口径见 services/cost_report.py：以已入账单据的总额为准，折成港币，
    按明细金额比例摊到部门；报表合计 == 单据总额合计。"""
    require_role("owner")(request)
    from app.services import cost_report as cr
    tenant_id = _tenant_id(request)
    depts = db.list_departments(tenant_id=tenant_id)
    dept_totals = {d.id: {"id": d.id, "name": d.name, "active": d.active,
                          "total": 0.0, "prev_total": 0.0, "delta": 0.0, "trend": "flat", "change_pct": 0.0} for d in depts}
    unalloc = {"count": 0, "total": 0.0, "prev_total": 0.0, "delta": 0.0, "trend": "flat", "change_pct": 0.0}

    prev_month, prev_start, prev_end = _prev_period(month, start_date, end_date)
    has_prev = bool(prev_month or prev_start)
    built = cr.build_allocations(
        tenant_id,
        lambda d: _in_period(d, month, start_date, end_date),
        (lambda d: _in_period(d, prev_month, prev_start, prev_end)) if has_prev else (lambda d: False),
        include_non_approved=bool(include_non_approved),
        valid_dept_ids=set(dept_totals))

    month_total = 0.0
    prev_total_all = 0.0
    for a in built["allocs"]:
        counted_unalloc = False
        for part in a["alloc"]["parts"]:
            amt = part["amount_hkd"]
            bucket = dept_totals.get(part["dept_id"], unalloc)
            if a["is_cur"]:
                month_total += amt
                bucket["total"] += amt
                if bucket is unalloc and not counted_unalloc:
                    unalloc["count"] += 1
                    counted_unalloc = True
            if a["is_prv"]:
                prev_total_all += amt
                bucket["prev_total"] += amt

    # 没有选期间时没有「上期」可比
    if not month and not (start_date and end_date):
        prev_total_all = 0.0
        for d in dept_totals.values():
            d["prev_total"] = 0.0
        unalloc["prev_total"] = 0.0

    def _finish(d):
        d["total"] = round(d["total"], 2)
        d["prev_total"] = round(d["prev_total"], 2)
        d["delta"] = round(d["total"] - d["prev_total"], 2)
        if d["prev_total"] > 0:
            d["change_pct"] = round((d["total"] - d["prev_total"]) / d["prev_total"] * 100, 1)
        else:
            d["change_pct"] = 0.0 if d["total"] == 0 else 100.0
        d["trend"] = _trend_type(d["total"], d["prev_total"])

    for d in dept_totals.values():
        _finish(d)
    _finish(unalloc)
    month_total = round(month_total, 2)
    prev_total_all = round(prev_total_all, 2)

    if start_date and end_date:
        label = f"{start_date}~{end_date}"
        if month:
            label = f"{month} ({start_date}~{end_date})"
    else:
        label = month or "全部"

    return {
        "status": "success",
        "data": {
            "period": {"label": label, "start_date": start_date or "", "end_date": end_date or "", "prev_label": prev_month or (f"{prev_start}~{prev_end}" if prev_start else "")},
            "month": month,
            "start_date": start_date or "",
            "end_date": end_date or "",
            "departments": list(dept_totals.values()),
            "unallocated": unalloc,
            "month_total": month_total,
            "prev_total": prev_total_all,
            "delta": round(month_total - prev_total_all, 2),
            "change_pct": round((month_total - prev_total_all) / prev_total_all * 100, 1) if prev_total_all > 0 else (0.0 if month_total == 0 else 100.0),
            "trend": _trend_type(month_total, prev_total_all),
            "currency": "HKD",
            "basis": "已入账单据总额（含服务费、税、折扣、运费），按明细金额比例分摊到部门；外币单据按入账时汇率折成港币",
            "unconverted": built["unconverted"],
            "unconverted_warning": cr.unconverted_warning(built["unconverted"]),
        },
    }


@router.get("/api/cost_report/items")
def cost_report_items(request: Request, department_id: int = None,
                      month: str = "", start_date: str = "", end_date: str = "",
                      include_non_approved: int = 0):
    """部门成本下钻：每行的 amount 是该行摊到的港币金额，所有行相加 == 部门成本。"""
    require_role("owner")(request)
    from app.services import cost_report as cr
    tenant_id = _tenant_id(request)
    dept_ids = {d.id for d in db.list_departments(tenant_id=tenant_id)}
    built = cr.build_allocations(
        tenant_id, lambda d: _in_period(d, month, start_date, end_date), lambda d: False,
        include_non_approved=bool(include_non_approved), valid_dept_ids=dept_ids)
    out = []
    for a in built["allocs"]:
        r = a["receipt"]
        for part in a["alloc"]["parts"]:
            item_dept = part["dept_id"]
            if department_id is not None:
                # department_id=0 表示未分配
                if department_id == 0:
                    if item_dept is not None:
                        continue
                elif item_dept != department_id:
                    continue
            it = part["item"] or {}
            out.append({
                "receipt_id": r.id, "receipt_date": r.receipt_date or "",
                "supplier_name": r.supplier_name,
                "raw_name": it.get("name") or "（整单，无明细）",
                "quantity": it.get("quantity", 0),
                "amount": round(part["amount_hkd"], 2),
                "line_amount": float(it.get("amount") or 0.0),
                "original_currency": a["currency"],
                "original_amount": round(part["amount_orig"], 2),
                "cost_center_id": item_dept,
            })
    return {"status": "success", "data": out}
