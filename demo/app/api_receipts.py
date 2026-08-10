# -*- coding: utf-8 -*-
"""收据端点：上传(异步Job)/轮询/列表/详情/保存(乐观锁)/approve/flag/retry/导出。"""

import io
import os
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import db
from app.auth import require_role
from app.services.receipt_utils import (
    build_detail, build_row, get_job, start_recognition_job,
)

router = APIRouter()

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


def _save_upload(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower() or ".jpg"
    if ext not in ALLOWED_EXT:
        ext = ".jpg"
    path = UPLOAD_DIR / f"{db.new_id()}{ext}"
    with open(path, "wb") as f:
        import shutil
        shutil.copyfileobj(file.file, f)
    return str(path)


# -------------------------------------------------------------
# 上传 + Job 轮询
# -------------------------------------------------------------
@router.post("/api/upload")
async def upload_receipt(
    request: Request,
    receipt: UploadFile = File(...),
    codebuddy: str = Form("true"),
    async_: str = Form("true"),
    vendor_hint: str = Form(""),
):
    require_role("staff")(request)
    image_path = _save_upload(receipt)
    job_id, receipt_id = start_recognition_job(
        image_path, vendor_hint=vendor_hint or "")

    image_url = "/uploads/" + os.path.basename(image_path)
    # async=true → queued + job_id（前端轮询）；否则等同步结果
    if async_.lower() == "true":
        return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
                "image_url": image_url}

    # 同步模式：等 job 完成
    import time
    for _ in range(600):
        job = get_job(job_id)
        if job["job_status"] == "done":
            return job["result"]
        if job["job_status"] == "error":
            return {"status": "error", "msg": job.get("error_msg", "识别失败"),
                    "receipt_id": receipt_id, "image_url": image_url}
        time.sleep(1)
    return {"status": "error", "msg": "识别超时", "receipt_id": receipt_id}


@router.get("/api/job/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return {"status": "error", "msg": "任务不存在"}
    return job


@router.post("/api/upload_batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    codebuddy: str = Form("true"),
):
    require_role("staff")(request)
    results = []
    for f in files:
        image_path = _save_upload(f)
        job_id, receipt_id = start_recognition_job(image_path)
        results.append({
            "status": "parsed", "receipt_id": receipt_id,
            "image_url": "/uploads/" + os.path.basename(image_path),
            "data": build_detail(db.get_receipt_row(receipt_id)),
        })
    return {"status": "success", "results": results}


# -------------------------------------------------------------
# 列表 / 详情
# -------------------------------------------------------------
@router.get("/api/receipts")
def list_receipts(request: Request):
    require_role("owner")(request)
    rows = db.list_receipt_rows()
    return {"status": "success", "data": [build_row(r) for r in rows]}


@router.get("/api/receipts/export")
def export_receipts(request: Request):
    require_role("owner")(request)
    rows = db.list_receipt_rows()
    data = [
        {"id": r.id, "supplier": r.supplier_name, "date": r.receipt_date,
         "total": r.total_amount, "status": r.status, "doc_form": r.doc_form}
        for r in rows
    ]
    df = pd.DataFrame(data)
    buf = io.StringIO()
    df.to_csv(buf, index=False, encoding="utf-8-sig")
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=receipts_export.csv"},
    )


@router.get("/api/receipt/{receipt_id}")
def get_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return {"status": "error", "msg": "收据不存在"}
    return {"status": "success", "receipt_id": row.id,
            "image_url": "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else ""),
            "data": build_detail(row)}


# -------------------------------------------------------------
# 保存（乐观锁 version）
# -------------------------------------------------------------
class SaveEditedBody(BaseModel):
    receipt_id: Optional[int] = None
    supplier_name: str = ""
    date: str = ""
    sheet_name: str = ""
    total_amount: float = 0.0
    items: list = []
    settlement_type: Optional[str] = None
    department_id: Optional[int] = None
    payment_mark: str = ""
    doc_form: str = ""
    layout_type: str = ""
    version: Optional[int] = None


@router.post("/api/save_edited")
def save_edited(body: SaveEditedBody, request: Request):
    require_role("staff")(request)

    if body.receipt_id is None:
        # 新建手工单
        rid = db.create_receipt(supplier_name=body.supplier_name or "通用供应商",
                                status="edited")
    else:
        rid = body.receipt_id
        row = db.get_receipt_row(rid)
        if row is None:
            return {"status": "error", "msg": "收据不存在"}
        if body.version is None:
            return {"status": "error", "code": "VERSION_REQUIRED",
                    "msg": "缺少版本号，请刷新后重试", "version": row.version}
        if body.version != row.version:
            return {"status": "error", "code": "VERSION_CONFLICT",
                    "msg": "单据已被其他操作修改，请刷新后重试", "version": row.version}

    if body.settlement_type is None and body.receipt_id is not None:
        return {"status": "error", "code": "SETTLEMENT_REQUIRED",
                "msg": "缺少结算方式"}

    items_raw = []
    for it in body.items or []:
        items_raw.append({
            "name": it.get("name", ""), "raw_name": it.get("raw_name", it.get("name", "")),
            "quantity": float(it.get("quantity", 0) or 0),
            "unit": it.get("unit", "") or "", "raw_unit": it.get("raw_unit", it.get("unit", "")) or "",
            "unit_price": float(it.get("unit_price", 0) or 0),
            "amount": float(it.get("amount", 0) or 0),
            "sku_id": it.get("sku_id"), "cost_center_id": it.get("cost_center_id"),
            "confidence": it.get("confidence", 0.5),
            "matched": int(bool(it.get("sku_id"))),
            "price_anomaly": int(it.get("price_anomaly", 0) or 0),
            "price_anomaly_direction": it.get("price_anomaly_direction", ""),
            "price_diff_percent": float(it.get("price_diff_percent", 0) or 0),
            "unit_conversion_warning": it.get("unit_conversion_warning", ""),
            "fuzzy_candidates": it.get("fuzzy_candidates", []),
            "entity_candidates": it.get("entity_candidates", []),
        })

    db.update_receipt(
        rid,
        supplier_name=body.supplier_name or "通用供应商",
        receipt_date=body.date or "",
        sheet_name=body.sheet_name or (body.date or "")[:7],
        total_amount=float(body.total_amount or 0),
        settlement_type=body.settlement_type,
        payment_mark=body.payment_mark or "",
        doc_form=body.doc_form or "",
        layout_type=body.layout_type or "",
        department_id=body.department_id,
        status="edited",
        version=body.version + 1 if body.version is not None else 1,
    )
    db.set_receipt_items(rid, items_raw)
    row = db.get_receipt_row(rid)
    return {"status": "success", "receipt_id": rid, "version": row.version}


# -------------------------------------------------------------
# 状态流转
# -------------------------------------------------------------
class ApproveBody(BaseModel):
    version: Optional[int] = None


@router.post("/api/receipt/{receipt_id}/approve")
def approve_receipt(receipt_id: int, body: ApproveBody, request: Request):
    require_role("owner")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return {"status": "error", "msg": "收据不存在"}
    if body.version is None:
        return {"status": "error", "code": "VERSION_REQUIRED",
                "msg": "缺少版本号，请刷新后重试", "version": row.version}
    if body.version != row.version:
        return {"status": "error", "code": "VERSION_CONFLICT",
                "msg": "单据已被修改，请刷新后重试", "version": row.version}

    # 幂等入账：写 SKU 库存流水 + 累计
    from app.services.inventory import apply_receipt_to_inventory
    apply_receipt_to_inventory(row)
    db.update_receipt(receipt_id, status="approved", version=row.version + 1)
    row = db.get_receipt_row(receipt_id)
    # 供应商自动建档（approve 后成为正式供应商）
    if row.supplier_name and db.find_supplier_by_name(row.supplier_name) is None:
        db.create_supplier(row.supplier_name)
    # 回写 VendorMemory（只认正向信号：老板批准）
    from app.services.rag import ingest_memory
    items_text = "\n".join(f"- {i['name']} {i['quantity']}{i['unit']} @{i['unit_price']} = {i['amount']}"
                           for i in db.get_receipt_items(receipt_id))
    ingest_memory(row.supplier_name, items_text, notes=f"版式：{row.doc_form}")
    return {"status": "success", "version": row.version}


@router.post("/api/receipt/{receipt_id}/flag")
def flag_receipt(receipt_id: int, request: Request):
    require_role("owner")(request)
    db.update_receipt(receipt_id, status="flagged")
    return {"status": "success", "msg": "已标记"}


@router.post("/api/receipt/{receipt_id}/retry")
def retry_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return {"status": "error", "msg": "收据不存在"}
    job_id, _ = start_recognition_job(row.image_path, receipt_id=receipt_id)
    return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id}


@router.post("/api/receipt/{receipt_id}/convert_manual")
def convert_manual(receipt_id: int, request: Request):
    require_role("staff")(request)
    db.update_receipt(receipt_id, status="parsed")
    row = db.get_receipt_row(receipt_id)
    return {"status": "success", "receipt_id": receipt_id,
            "image_url": "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else ""),
            "data": build_detail(row)}


@router.post("/api/receipt/{receipt_id}/discard")
def discard_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    db.update_receipt(receipt_id, status="error")
    return {"status": "success", "msg": "已丢弃"}


class PayDateBody(BaseModel):
    expected_pay_date: Optional[str] = None


@router.post("/api/receipt/{receipt_id}/pay_date")
def set_pay_date(receipt_id: int, body: PayDateBody, request: Request):
    require_role("owner")(request)
    db.update_receipt(receipt_id, expected_pay_date=body.expected_pay_date or "")
    return {"status": "success", "msg": "付款日期已更新"}
