# -*- coding: utf-8 -*-
"""支付 + 对账端点（简化实现，界面可用的最小功能）。"""

import json
import os
from pathlib import Path

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role

router = APIRouter()

VOUCHER_DIR = Path("./uploads/vouchers")
VOUCHER_DIR.mkdir(parents=True, exist_ok=True)


# -------------------------------------------------------------
# 支付
# -------------------------------------------------------------
@router.get("/api/payments")
def list_payments(request: Request, supplier_id: int = None):
    require_role("owner")(request)
    data = db.list_payments()
    if supplier_id:
        # 需要按供应商过滤（简化：查该供应商名匹配）
        sup = db.get_supplier(supplier_id)
        if sup:
            data = [p for p in data if p["supplier_name"] == sup.name]
    return {"status": "success", "data": data}


@router.post("/api/payments")
async def create_payment(
    request: Request,
    supplier_id: int = Form(...),
    amount: float = Form(...),
    method: str = Form(...),
    paid_at: str = Form(...),
    notes: str = Form(""),
    receipt_ids: list[int] = Form([]),
    voucher: UploadFile = File(None),
):
    require_role("owner")(request)
    voucher_path = ""
    if voucher and voucher.filename:
        ext = os.path.splitext(voucher.filename)[1].lower() or ".jpg"
        path = VOUCHER_DIR / f"{db.new_id()}{ext}"
        with open(path, "wb") as f:
            import shutil
            shutil.copyfileobj(voucher.file, f)
        voucher_path = "/uploads/vouchers/" + path.name

    payment_id = db.create_payment(supplier_id, amount, method, paid_at,
                                   notes, voucher_path, receipt_ids or [])
    return {"status": "success", "msg": "付款已登记", "id": payment_id}


# -------------------------------------------------------------
# 对账（简化：单供应商一次生成，行级匹配）
# -------------------------------------------------------------
RECON_TASKS = {}


@router.get("/api/reconciliation")
def list_reconciliation(request: Request, supplier_id: int = None):
    require_role("owner")(request)
    out = []
    for t in RECON_TASKS.values():
        supplier = db.get_supplier(t["supplier_id"])
        lines = t.get("lines", [])
        out.append({
            "id": t["id"], "supplier_name": supplier.name if supplier else "—",
            "period_start": t["period_start"], "period_end": t["period_end"],
            "period_type_label": {"month": "月结", "3d": "3天", "7d": "7天",
                                  "14d": "14天"}.get(t.get("period_type"), t.get("period_type")),
            "status": t["status"],
            "summary": {"total_lines": len(lines),
                        "matched": sum(1 for x in lines if x["match_status"] == "matched"),
                        "open_issues": sum(1 for x in lines if x["match_status"] != "matched")},
        })
    return {"status": "success", "data": out}


class ReconCreateBody(BaseModel):
    supplier_id: int
    period_type: str = "month"
    start: str = ""
    end: str = ""


@router.post("/api/reconciliation")
def create_reconciliation(body: ReconCreateBody, request: Request):
    require_role("owner")(request)
    supplier = db.get_supplier(body.supplier_id)
    if supplier is None:
        return {"status": "error", "msg": "供应商不存在"}
    task_id = db.new_id()
    # 生成对账行：该供应商已 approve 收据
    lines = []
    for r in db.list_receipt_rows():
        if r.supplier_name == supplier.name and r.status == "approved":
            lines.append({
                "id": r.id, "side": "restaurant",
                "line_date": r.receipt_date or "", "docket_no": f"#{r.id}",
                "amount": r.total_amount or 0.0,
                "match_status": "matched" if r.payment_id else "pending",
                "match_status_label": "已匹配" if r.payment_id else "待匹配",
                "matched_receipt_id": r.id, "matched_receipt": r.id,
                "resolution_note": "", "resolved": bool(r.payment_id),
            })
    RECON_TASKS[task_id] = {
        "id": task_id, "supplier_id": body.supplier_id,
        "period_start": body.start or "", "period_end": body.end or "",
        "period_type": body.period_type, "status": "reconciling",
        "lines": lines,
    }
    return {"status": "success", "task_id": task_id, "msg": "对账已开始"}


@router.get("/api/reconciliation/{task_id}")
def get_reconciliation(task_id: str, request: Request):
    require_role("owner")(request)
    t = RECON_TASKS.get(task_id)
    if t is None:
        return {"status": "error", "msg": "任务不存在"}
    supplier = db.get_supplier(t["supplier_id"])
    lines = t["lines"]
    return {
        "status": "success",
        "task": {
            "id": t["id"], "period_start": t["period_start"],
            "period_end": t["period_end"], "status": t["status"],
            "summary": {
                "statement_amount": sum(x["amount"] for x in lines),
                "restaurant_only_amount": sum(x["amount"] for x in lines),
            },
        },
        "lines": lines,
        "restaurant_receipts": [{"id": r.id, "receipt_date": r.receipt_date or "",
                                 "total_amount": r.total_amount or 0.0,
                                 "matched": r.payment_id is not None}
                                for r in db.list_receipt_rows()
                                if r.supplier_name == supplier.name],
    }


class ResolveBody(BaseModel):
    line_id: int
    action: str
    note: str = ""
    receipt_id: Optional[int] = None


@router.post("/api/reconciliation/{task_id}/resolve")
def resolve_line(task_id: str, body: ResolveBody, request: Request):
    require_role("owner")(request)
    t = RECON_TASKS.get(task_id)
    if t is None:
        return {"status": "error", "msg": "任务不存在"}
    for line in t["lines"]:
        if line["id"] == body.line_id:
            line["resolved"] = True
            line["resolution_note"] = body.note or body.action
            if body.action == "confirm_match":
                line["match_status"] = "matched"
                line["match_status_label"] = "已匹配"
            elif body.action == "write_off":
                line["match_status"] = "write_off"
                line["match_status_label"] = "核销"
            else:
                line["match_status"] = "disputed"
                line["match_status_label"] = "争议"
    return {"status": "success", "msg": "已处理"}


@router.post("/api/reconciliation/{task_id}/confirm")
def confirm_reconciliation(task_id: str, request: Request):
    require_role("owner")(request)
    t = RECON_TASKS.get(task_id)
    if t is None:
        return {"status": "error", "msg": "任务不存在"}
    t["status"] = "confirmed"
    return {"status": "success", "msg": "对账已确认"}
