# -*- coding: utf-8 -*-
"""供应商端点：列表/搜索/CRUD/合并 + 部门 CRUD + 成本报表。"""

from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role

router = APIRouter()


# -------------------------------------------------------------
# 供应商
# -------------------------------------------------------------
def _supplier_dict(s):
    stats = db.supplier_stats(s.id)
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
        "is_overdue": False, "overdue_amount": 0.0,
    }


@router.get("/api/suppliers")
def list_suppliers(request: Request, q: str = "", include_inactive: int = 0):
    require_role("owner")(request)
    suppliers = db.list_suppliers(include_inactive=bool(include_inactive))
    out = []
    for s in suppliers:
        if q and q not in s.name and q not in (s.supplier_code or ""):
            continue
        out.append(_supplier_dict(s))
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
    fields = {k: v for k, v in body.model_dump().items() if k != "name" and v is not None}
    sup_id, err = db.create_supplier(body.name, **fields)
    if err:
        from fastapi.responses import JSONResponse
        return JSONResponse({"status": "error", "msg": err}, status_code=409)
    return {"status": "success", "id": sup_id}


@router.patch("/api/suppliers/{supplier_id}")
def patch_supplier(supplier_id: int, body: SupplierBody, request: Request):
    require_role("owner")(request)
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    row, err = db.update_supplier(supplier_id, **fields)
    if row is None:
        return {"status": "error", "msg": "供应商不存在"}
    return {"status": "success"}


class MergeBody(BaseModel):
    keep_id: int
    drop_id: int


@router.post("/api/suppliers/merge")
def merge_suppliers(body: MergeBody, request: Request):
    require_role("owner")(request)
    keep = db.get_supplier(body.keep_id)
    drop = db.get_supplier(body.drop_id)
    if keep is None or drop is None:
        return {"status": "error", "msg": "供应商不存在"}
    # 收据供应商名合并
    for r in db.list_receipt_rows():
        if r.supplier_name == drop.name:
            db.update_receipt(r.id, supplier_name=keep.name)
    db.update_supplier(body.drop_id, active=0)
    return {"status": "success", "msg": "已合并"}


# -------------------------------------------------------------
# 部门
# -------------------------------------------------------------
@router.get("/api/departments")
def list_departments(request: Request):
    require_role("staff")(request)
    depts = db.list_departments()
    return {"status": "success",
            "data": [{"id": d.id, "name": d.name, "active": d.active} for d in depts]}


class DeptBody(BaseModel):
    name: str = ""


@router.post("/api/departments")
def create_department(body: DeptBody, request: Request):
    require_role("owner")(request)
    if not body.name:
        return {"status": "error", "msg": "部门名称不能为空"}
    dept_id = db.create_department(body.name)
    return {"status": "success", "id": dept_id}


@router.patch("/api/departments/{dept_id}")
def patch_department(dept_id: int, body: DeptBody, request: Request):
    require_role("owner")(request)
    db.update_department(dept_id, name=body.name)
    return {"status": "success"}


@router.delete("/api/departments/{dept_id}")
def delete_department(dept_id: int, request: Request):
    require_role("owner")(request)
    depts = db.list_departments()
    if len([d for d in depts if d.active]) <= 1:
        return {"status": "error", "msg": "不得停用最后一个启用部门"}
    db.update_department(dept_id, active=0)
    return {"status": "success"}


# -------------------------------------------------------------
# 成本报表
# -------------------------------------------------------------
@router.get("/api/cost_report")
def cost_report(request: Request, month: str = "", start_date: str = "",
                end_date: str = "", include_non_approved: int = 0):
    require_role("owner")(request)
    depts = db.list_departments()
    rows = db.list_receipt_rows()
    if include_non_approved == 0:
        rows = [r for r in rows if r.status == "approved"]

    dept_totals = {d.id: {"id": d.id, "name": d.name, "active": d.active,
                          "total": 0.0, "prev_total": 0.0, "delta": 0.0} for d in depts}
    unalloc = {"count": 0, "total": 0.0, "prev_total": 0.0, "delta": 0.0}
    month_total = 0.0

    for r in rows:
        dept_id = r.department_id
        amount = r.total_amount or 0.0
        month_total += amount
        if dept_id in dept_totals:
            dept_totals[dept_id]["total"] += amount
        else:
            unalloc["count"] += 1
            unalloc["total"] += amount

    return {
        "status": "success",
        "data": {
            "period": {"label": month or "全部"},
            "month": month,
            "departments": list(dept_totals.values()),
            "unallocated": unalloc,
            "month_total": round(month_total, 2),
            "prev_total": 0.0,
            "delta": 0.0,
        },
    }


@router.get("/api/cost_report/items")
def cost_report_items(request: Request, department_id: int = None,
                      month: str = "", include_non_approved: int = 0):
    require_role("owner")(request)
    rows = db.list_receipt_rows()
    if include_non_approved == 0:
        rows = [r for r in rows if r.status == "approved"]
    out = []
    for r in rows:
        if department_id is not None and r.department_id != department_id:
            continue
        for it in db.get_receipt_items(r.id):
            out.append({
                "receipt_id": r.id, "receipt_date": r.receipt_date or "",
                "supplier_name": r.supplier_name, "raw_name": it["name"],
                "quantity": it["quantity"], "amount": it["amount"],
            })
    return {"status": "success", "data": out}
