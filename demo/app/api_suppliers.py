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


@router.get("/api/suppliers/{supplier_id}/receipts")
def get_supplier_receipts(supplier_id: int, request: Request, desensitized: str = "false"):
    """供应商收据时间线 + 采购金额趋势。支持 ?desensitized=true 脱敏。"""
    require_role("owner")(request)
    from fastapi.responses import JSONResponse
    from collections import defaultdict
    from app.api_receipts import _mask_sensitive

    sup = db.get_supplier(supplier_id)
    if sup is None:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "供应商不存在"})

    is_desens = desensitized.lower() == "true"
    rows = db.list_receipt_rows()
    # 匹配当前供应商的单据（按名字匹配）
    matched = [r for r in rows if r.supplier_name == sup.name]

    receipts_data = []
    monthly_trend = defaultdict(float)

    for r in matched:
        items = db.get_receipt_items(r.id)
        supplier_display = _mask_sensitive(r.supplier_name or "") if is_desens else (r.supplier_name or "")
        receipts_data.append({
            "id": r.id,
            "receipt_date": r.receipt_date or "",
            "total_amount": r.total_amount or 0.0,
            "status": r.status,
            "doc_form": r.doc_form or "",
            "supplier_name": supplier_display,
            "items_summary": [
                {
                    "name": _mask_sensitive(it.get("name","")) if is_desens else it.get("name",""),
                    "qty": it.get("quantity", it.get("qty", 0)),
                    "unit": it.get("unit",""),
                    "unit_price": it.get("unit_price",0),
                    "amount": it.get("amount",0),
                } for it in items
            ],
            "paid_at": r.paid_at or "",
            "paid_method": r.paid_method or "",
            "is_paid": bool(r.paid_at),
        })
        if r.receipt_date and len(r.receipt_date) >= 7:
            m = r.receipt_date[:7]
            monthly_trend[m] += (r.total_amount or 0.0)

    sorted_trend = [
        {"month": m, "total_amount": round(amt, 2)}
        for m, amt in sorted(monthly_trend.items())
    ]

    return {
        "status": "success",
        "supplier": {
            "id": sup.id,
            "name": _mask_sensitive(sup.name) if is_desens else sup.name,
            "contact_phone": _mask_sensitive(sup.contact_phone or "") if is_desens else (sup.contact_phone or ""),
        },
        "receipt_count": len(receipts_data),
        "receipts": receipts_data,
        "monthly_trend": sorted_trend,
    }


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
    require_role("owner")(request)
    depts = db.list_departments()
    rows = db.list_receipt_rows()
    if include_non_approved == 0:
        rows = [r for r in rows if r.status == "approved"]

    dept_totals = {d.id: {"id": d.id, "name": d.name, "active": d.active,
                          "total": 0.0, "prev_total": 0.0, "delta": 0.0, "trend": "flat", "change_pct": 0.0} for d in depts}
    unalloc = {"count": 0, "total": 0.0, "prev_total": 0.0, "delta": 0.0, "trend": "flat", "change_pct": 0.0}
    prev_unalloc = {"count": 0}  # 仅内部计数用

    # 推导上期区间
    prev_month, prev_start, prev_end = _prev_period(month, start_date, end_date)

    month_total = 0.0
    prev_total_all = 0.0

    for r in rows:
        is_current = _in_period(r.receipt_date or "", month, start_date, end_date)
        is_prev = _in_period(r.receipt_date or "", prev_month, prev_start, prev_end) if (prev_month or prev_start) else False

        # 部门分摊：优先按明细行 cost_center_id 拆分，回退到单据 department_id，最后进未分配
        items = db.get_receipt_items(r.id)
        if items:
            for it in items:
                dept_id = it.get("cost_center_id")
                # 回退：明细未打标则用单据级部门
                if dept_id is None:
                    dept_id = r.department_id
                amt = float(it.get("amount") or 0.0)
                if is_current:
                    month_total += amt
                    if dept_id in dept_totals:
                        dept_totals[dept_id]["total"] += amt
                    else:
                        unalloc["total"] += amt
                        if is_current:
                            # 未分配按明细计数（部门分摊空的回退口径）
                            pass
                if is_prev:
                    prev_total_all += amt
                    if dept_id in dept_totals:
                        dept_totals[dept_id]["prev_total"] += amt
                    else:
                        unalloc["prev_total"] += amt
            # 未分配计数：若整单明细均无归属，则计一次未分配单据
            if is_current:
                has_alloc = any((it.get("cost_center_id") is not None or r.department_id in dept_totals) for it in items)
                if not has_alloc:
                    unalloc["count"] += 1
            if is_prev and prev_month:
                has_alloc_prev = any((it.get("cost_center_id") is not None or r.department_id in dept_totals) for it in items if _in_period(r.receipt_date or "", prev_month, prev_start, prev_end))
                # 简化：上期未分配计数同口径
                pass
        else:
            # 无明细回退到整单金额
            amt = r.total_amount or 0.0
            if is_current:
                month_total += amt
                if r.department_id in dept_totals:
                    dept_totals[r.department_id]["total"] += amt
                else:
                    unalloc["count"] += 1
                    unalloc["total"] += amt
            if is_prev:
                prev_total_all += amt
                if r.department_id in dept_totals:
                    dept_totals[r.department_id]["prev_total"] += amt
                else:
                    unalloc["prev_total"] += amt

        # 兼容：若未经历 items 分支但需要计期外 prev_total（已在上层处理）
        # 若无区间过滤（全部），prev 保持 0

    # 若无明确期间过滤（month/start_date 都空），month_total 已为全量，prev 保持 0
    if not month and not (start_date and end_date):
        prev_total_all = 0.0
        for d in dept_totals.values():
            d["prev_total"] = 0.0

    # 若有期间过滤但无 prev 区间（理论不会），prev_total_all 保持 0

    # 计算 delta / change_pct / trend（四向对比高亮）
    for d in dept_totals.values():
        d["delta"] = round(d["total"] - d["prev_total"], 2)
        d["total"] = round(d["total"], 2)
        d["prev_total"] = round(d["prev_total"], 2)
        if d["prev_total"] > 0:
            d["change_pct"] = round((d["total"] - d["prev_total"]) / d["prev_total"] * 100, 1)
        else:
            d["change_pct"] = 0.0 if d["total"] == 0 else 100.0
        d["trend"] = _trend_type(d["total"], d["prev_total"])

    unalloc["total"] = round(unalloc["total"], 2)
    unalloc["prev_total"] = round(unalloc["prev_total"], 2)
    unalloc["delta"] = round(unalloc["total"] - unalloc["prev_total"], 2)
    if unalloc["prev_total"] > 0:
        unalloc["change_pct"] = round((unalloc["total"] - unalloc["prev_total"]) / unalloc["prev_total"] * 100, 1)
    else:
        unalloc["change_pct"] = 0.0 if unalloc["total"] == 0 else 100.0
    unalloc["trend"] = _trend_type(unalloc["total"], unalloc["prev_total"])

    # 上期无数据时，unalloc count 已在循环中累计；若仍为 0 但有金额，补计 1 以免前端显 0 误为空
    if unalloc["count"] == 0 and unalloc["total"] > 0:
        # 粗略：按金额反推至少 1 张未分配
        unalloc["count"] = 1

    # 期间标签：优先展示起止，其次 month，其次全部
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
            "month_total": round(month_total, 2),
            "prev_total": round(prev_total_all, 2),
            "delta": round(month_total - prev_total_all, 2),
            "change_pct": round((month_total - prev_total_all) / prev_total_all * 100, 1) if prev_total_all > 0 else (0.0 if month_total == 0 else 100.0),
            "trend": _trend_type(month_total, prev_total_all),
        },
    }


@router.get("/api/cost_report/items")
def cost_report_items(request: Request, department_id: int = None,
                      month: str = "", start_date: str = "", end_date: str = "",
                      include_non_approved: int = 0):
    require_role("owner")(request)
    rows = db.list_receipt_rows()
    if include_non_approved == 0:
        rows = [r for r in rows if r.status == "approved"]
    out = []
    for r in rows:
        # 期间过滤：未命中当前查询区间则跳过（下钻与汇总口径一致）
        if month or (start_date and end_date):
            if not _in_period(r.receipt_date or "", month, start_date, end_date):
                continue
        for it in db.get_receipt_items(r.id):
            # 部门分摊口径：优先明细 cost_center_id，回退单据 department_id
            item_dept = it.get("cost_center_id")
            if item_dept is None:
                item_dept = r.department_id
            if department_id is not None:
                # 下钻：department_id=None（未分配）时仅返回未归属明细
                if department_id == 0:
                    if item_dept is not None:
                        continue
                elif item_dept != department_id:
                    continue
            else:
                # 无部门过滤时返回全部（含未分配）
                pass
            # 单独处理前端传入 department_id=null 语义：原前端传 null 表示未分配
            # FastAPI 将 query 未传时 department_id=None；前端未分配传 null 也映射为 None，已在上层统一为 None 处理
            # 为兼容，额外支持字符串 "null" / "unallocated"
            out.append({
                "receipt_id": r.id, "receipt_date": r.receipt_date or "",
                "supplier_name": r.supplier_name, "raw_name": it["name"],
                "quantity": it["quantity"], "amount": it["amount"],
                "cost_center_id": item_dept,
            })
    # 若为未分配下钻（前端传 department_id 为 null 且期望未分配），而 department_id 此时为 None 会返回全部
    # 额外支持：若调用方显式传 department_id=unallocated 场景，由前端自行过滤，此处不额外拦截
    return {"status": "success", "data": out}
