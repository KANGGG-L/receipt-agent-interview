# -*- coding: utf-8 -*-
"""收据端点：上传(异步Job)/轮询/列表/详情/保存(乐观锁)/approve/flag/retry/导出。"""

import io
import os
import re
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import db
from app.auth import require_role
from app.services.contract import payment_mark_from_image
from app.services.receipt_utils import (
    build_detail, build_row, get_job, start_recognition_job,
)


def _supplement_payment_mark(receipt_row) -> str:
    """印章判定补红章：若已有 payment_mark 则保留，否则用红章检测补充。

    红章检测单一来源：app.services.contract.detect_red_stamp。
    """
    existing = (receipt_row.payment_mark or "").strip()
    if existing:
        return existing
    return payment_mark_from_image(receipt_row.image_path or "", llm_marked=False)


def _track_event(account, session_id, event_type, receipt_id=None,
                 properties=None, grp=None):
    """埋点封装：失败静默忽略，不影响主业务。"""
    try:
        db.log_user_event(
            account_id=account.get("email", "") if account else "",
            session_id=str(session_id or ""),
            event_type=str(event_type),
            receipt_id=int(receipt_id) if receipt_id else None,
            properties=properties or {},
            grp=str(grp) if grp else None,
        )
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录用户事件失败: {e}")


def _track_ai_decision(account, receipt_id, row=None, **kw):
    """AI 决策日志封装。"""
    try:
        kwargs = dict(kw)
        kwargs.setdefault("grp", "control")
        if row is not None:
            kwargs.setdefault("supplier_id", getattr(row, "supplier_id", None))
            kwargs.setdefault("use_grey", getattr(row, "use_grey", 0) or 0)
        db.log_ai_decision(receipt_id=receipt_id, **kwargs)
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录 AI 决策失败: {e}")

router = APIRouter()

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".tif", ".tiff"}


def _version_error(payload_version, row, action):
    """乐观锁预检（对齐完整版）：缺失/非法 → 400 VERSION_REQUIRED；不匹配 → 409 VERSION_CONFLICT。"""
    current = row.version if row.version is not None else 1
    if payload_version is None:
        return JSONResponse(
            content={"status": "error", "code": "VERSION_REQUIRED",
                     "msg": f"{action}已有单据必须携带当前 version（整数）。"
                            f"请先 GET /api/receipt/{{id}} 获取 version 后再提交；"
                            f"当前单据 version={current}。"},
            status_code=400)
    if isinstance(payload_version, bool):
        return JSONResponse(content={"status": "error", "code": "VERSION_REQUIRED",
                                     "msg": "version 字段必须为整数"}, status_code=400)
    try:
        v = int(payload_version)
    except (TypeError, ValueError):
        return JSONResponse(content={"status": "error", "code": "VERSION_REQUIRED",
                                     "msg": "version 字段必须为整数"}, status_code=400)
    if v != current:
        return JSONResponse(
            content={"status": "error", "code": "VERSION_CONFLICT",
                     "msg": f"版本冲突：提交的 version={v}，单据当前 version={current}。"
                            f"该单据已被他人修改，请刷新加载最新数据后再{action}。"},
            status_code=409)
    return None


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
# HEIC / 非 Web 格式即时转换接口
# -------------------------------------------------------------
@router.post("/api/convert-image")
async def convert_image(
    request: Request,
    file: UploadFile = File(...),
):
    """将 HEIC/HEIF/TIFF 等浏览器无法直接预览的图片即时转换为标准高质量 JPEG。"""
    try:
        content = await file.read()
        if not content:
            return JSONResponse(status_code=400, content={"status": "error", "msg": "上传文件为空"})

        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass

        from PIL import Image, ImageOps
        import io

        with Image.open(io.BytesIO(content)) as img:
            # 依 EXIF 矫正拍摄朝向
            try:
                img = ImageOps.exif_transpose(img)
            except Exception:
                pass

            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out_buf = io.BytesIO()
            img.save(out_buf, format="JPEG", quality=92, optimize=True)
            jpeg_bytes = out_buf.getvalue()

        from fastapi.responses import Response
        return Response(content=jpeg_bytes, media_type="image/jpeg")
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "msg": f"图片转换失败: {str(e)}"})


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
    account = getattr(request.state, "account", {})

    # 图片质量预检：空文件或极端过小文件拦截
    image_path = _save_upload(receipt)
    file_size = os.path.getsize(image_path) if os.path.exists(image_path) else 0
    if file_size < 30:
        if os.path.exists(image_path):
            os.remove(image_path)
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "code": "IMAGE_QUALITY_ERROR",
                "msg": "上传图片损坏、模糊或为空（文件体积过小），请重新拍摄清晰单据",
                "quality_warnings": ["image_empty_or_corrupted"]
            }
        )

    job_id, receipt_id = start_recognition_job(
        image_path, vendor_hint=vendor_hint or "")

    image_url = "/uploads/" + os.path.basename(image_path)
    _track_event(account, getattr(request.state, "session_id", job_id),
                 "upload", receipt_id=receipt_id,
                 properties={"engine_hint": vendor_hint or "",
                             "image_path": os.path.basename(image_path),
                             "async": async_.lower() == "true"},
                 grp="treatment" if codebuddy.lower() == "true" else "control")
    # async=true → queued + job_id（前端轮询）；否则等同步结果
    if async_.lower() == "true":
        return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
                "image_url": image_url}

    # 同步模式：等 job 完成
    import time
    for _ in range(600):
        job = get_job(job_id)
        if job["job_status"] == "done":
            _track_event(account, job_id, "parse_done", receipt_id=receipt_id,
                         properties={"job_id": job_id, "status": "done"})
            return job["result"]
        if job["job_status"] == "error":
            _track_event(account, job_id, "parse_done", receipt_id=receipt_id,
                         properties={"job_id": job_id, "status": "error"})
            return {"status": "error", "msg": job.get("error_msg", "识别失败"),
                    "receipt_id": receipt_id, "image_url": image_url}
        time.sleep(1)
    _track_event(account, job_id, "parse_done", receipt_id=receipt_id,
                 properties={"job_id": job_id, "status": "timeout"})
    return {"status": "error", "msg": "识别超时", "receipt_id": receipt_id}


@router.get("/api/job/{job_id}")
def job_status(job_id: str):
    job = get_job(job_id)
    if job is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该识别任务"},
                            status_code=404)
    return job


@router.post("/api/upload_batch")
async def upload_batch(
    request: Request,
    files: list[UploadFile] = File(...),
    codebuddy: str = Form("true"),
):
    require_role("staff")(request)
    # C5：批量限流 — 单次≤20张
    if len(files) > 20:
        return JSONResponse(
            content={"status": "error", "code": "BATCH_LIMIT_EXCEEDED",
                     "msg": f"单次最多上传20张，当前{len(files)}张，请分批上传"},
            status_code=400)

    # C5：pHash去重检查
    seen_hashes = set()
    results = []
    queue_pos = 0
    for f in files:
        image_path = _save_upload(f)
        # 简易pHash：基于文件内容hash（像素级pHash需imagehash库，此处用文件MD5近似去重）
        import hashlib
        file_hash = hashlib.md5(open(image_path, "rb").read()).hexdigest()
        if file_hash in seen_hashes:
            if os.path.exists(image_path):
                os.remove(image_path)
            results.append({
                "status": "duplicate", "image_url": None,
                "msg": f"疑似重复上传（文件指纹相同），已跳过",
            })
            continue
        seen_hashes.add(file_hash)
        job_id, receipt_id = start_recognition_job(image_path)
        row = db.get_receipt_row(receipt_id)
        queue_pos += 1
        results.append({
            "status": "queued", "receipt_id": receipt_id,
            "image_url": "/uploads/" + os.path.basename(image_path),
            "engine_used": None,
            "quality_warnings": [],
            "version": row.version if row else None,
            "queue_position": queue_pos,
            "data": build_detail(row),
        })
    return {"status": "success", "results": results}


# -------------------------------------------------------------
# 列表 / 详情
# -------------------------------------------------------------
def _mask_sensitive(text: str) -> str:
    """脱敏：手机号(13/14/15/16/17/18/19 开头 11 位)、身份证号(18 位含末尾 X / 15 位旧版) 用掩码替换。

    保证：len(_mask_sensitive(明文)) == len(明文)（对匹配到的手机号/身份证号子串本身也成立）。
    """
    import re
    if not text:
        return text
    # 顺序重要：先脱敏 18/15 位身份证（含词边界），再脱敏手机号。
    # 否则 18 位身份证内部的 "19900101123"（11 位、1 开头）会被手机号正则先匹配，
    # 把生日段遮掉、留下校验段，语义错误（虽长度仍对）。
    # 18 位身份证号（含末尾 X/x）：前 6 + 中间掩码 + 后 4，总长度 == 18
    text = re.sub(r"\b\d{17}[0-9Xx]\b",
                  lambda m: m.group(0)[:6] + "*" * (len(m.group(0)) - 10) + m.group(0)[-4:],
                  text)
    # 旧版 15 位身份证号：前 6 + 中间掩码 + 后 4，总长度 == 15
    text = re.sub(r"(?<!\d)\d{15}(?!\d)",
                  lambda m: m.group(0)[:6] + "*" * (len(m.group(0)) - 10) + m.group(0)[-4:],
                  text)
    # 手机号（13/14/15/16/17/18/19 开头 11 位）：前 3 + 中间掩码 + 后 4，总长度 == 11
    text = re.sub(r"1[3-9]\d{9}",
                  lambda m: m.group(0)[:3] + "*" * (len(m.group(0)) - 7) + m.group(0)[-4:],
                  text)
    return text


@router.get("/api/receipts")
def list_receipts(request: Request):
    require_role("owner")(request)
    desensitized = request.query_params.get("desensitized", "false").lower() == "true"
    rows = db.list_receipt_rows()
    # 印章判定补红章：列表视图在 build_row 前补充，避免 LLM 漏检导致 payment_mark 空
    for r in rows:
        if not (r.payment_mark or "").strip():
            r.payment_mark = _supplement_payment_mark(r)
    data = [build_row(r) for r in rows]
    if desensitized:
        for row in rows:
            _mask_sensitive_text = _mask_sensitive
            row.raw_llm = _mask_sensitive_text(row.raw_llm or "")
            row.payment_mark = _mask_sensitive_text(row.payment_mark or "")
            row.supplier_name = _mask_sensitive_text(row.supplier_name or "")
        # 脱敏视图附加 raw_llm / payment_mark 字段（含掩码值）
        data = []
        for row in rows:
            d = build_row(row)
            d["raw_llm"] = row.raw_llm
            d["payment_mark"] = row.payment_mark
            data.append(d)
    return {"status": "success", "data": data}


@router.get("/api/receipts/export")
def export_receipts(request: Request, desensitized: str = "false"):
    """导出收据 CSV。支持 ?desensitized=true 脱敏导出。"""
    require_role("owner")(request)
    is_desens = desensitized.lower() == "true"
    rows = db.list_receipt_rows()
    data = [
        {
            "id": r.id,
            "supplier": _mask_sensitive(r.supplier_name or "") if is_desens else (r.supplier_name or ""),
            "date": r.receipt_date or "",
            "total": r.total_amount or 0.0,
            "status": r.status,
            "doc_form": r.doc_form or "",
        }
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
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 印章判定补红章：详情视图补充，避免 payment_mark 空
    if not (row.payment_mark or "").strip():
        row.payment_mark = _supplement_payment_mark(row)
    data = build_detail(row)
    desensitized = request.query_params.get("desensitized", "false").lower() == "true"
    if desensitized:
        # 脱敏视图附加 raw_llm（与列表脱敏视图保持一致）
        data["raw_llm"] = _mask_sensitive(row.raw_llm or "")
        if "payment_mark" in data and data["payment_mark"]:
            data["payment_mark"] = _mask_sensitive(data["payment_mark"])
        if "supplier_name" in data and data["supplier_name"]:
            data["supplier_name"] = _mask_sensitive(data["supplier_name"])
    return {"status": "success", "receipt_id": row.id,
            "image_url": "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else ""),
            "data": data}


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
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")

    # 初始化，新建手工单路径不会有历史数据
    row = None
    old_items = []

    if body.receipt_id is None:
        # 新建手工单
        rid = db.create_receipt(supplier_name=body.supplier_name or "通用供应商",
                                status="edited")
        db.append_audit_log(rid, who, "save_edited", "receipt", None, rid)
    else:
        rid = body.receipt_id
        row = db.get_receipt_row(rid)
        if row is None:
            return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                                status_code=404)
        ver_err = _version_error(body.version, row, "保存")
        if ver_err is not None:
            return ver_err
        old_status = row.status
        old_items = db.get_receipt_items(rid)
        db.append_audit_log(rid, who, "save_edited", "status", old_status, "edited")

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

    # 字段级审计：仅编辑既有单据时才对比 old/new（新建手工单无历史可比）
    if body.receipt_id is not None and row is not None:
        for old_it, new_it in zip(old_items, items_raw):
            if old_it.get("unit_price") != new_it.get("unit_price"):
                db.append_audit_log(rid, who, "save_edited", "unit_price",
                                    old_it.get("unit_price"), new_it.get("unit_price"))
            if old_it.get("amount") != new_it.get("amount"):
                db.append_audit_log(rid, who, "save_edited", "amount",
                                    old_it.get("amount"), new_it.get("amount"))
        if row.supplier_name != body.supplier_name:
            db.append_audit_log(rid, who, "save_edited", "supplier_name",
                                row.supplier_name, body.supplier_name)
        if row.total_amount != body.total_amount:
            db.append_audit_log(rid, who, "save_edited", "total_amount",
                                row.total_amount, body.total_amount)

    row = db.get_receipt_row(rid)
    _track_event(account, getattr(request.state, "session_id", "batch"),
                 "upload", receipt_id=rid,
                 properties={"mode": "batch"})
    # C6 ai_decision_log：save_edited 回填 user_value（per-field diff）
    if body.receipt_id is not None:
        ai_row = db.get_receipt_row(rid)
        if ai_row:
            try:
                import json as _j
                ai_prefill = _j.loads(ai_row.ai_prefill_json or "{}")
                ai_items = ai_prefill.get("items", [])
                for i, new_it in enumerate(items_raw):
                    if i < len(ai_items):
                        ai_val = ai_items[i].get("unit_price", "")
                        user_val = new_it.get("unit_price", "")
                        if str(ai_val) != str(user_val):
                            _track_ai_decision(account, rid, row=ai_row,
                                decision_type="edit",
                                field_path=f"items[{i}].unit_price",
                                ai_value=ai_val, user_value=user_val,
                                adopted=0, engine="human")
            except Exception as e:
                import logging
                logging.getLogger("api_receipts").warning(f"[WARN] 记录编辑 AI 决策失败: {e}")
    return {"status": "success", "receipt_id": rid, "version": row.version}


# -------------------------------------------------------------
# 状态流转
# -------------------------------------------------------------
class ApproveBody(BaseModel):
    version: Optional[int] = None


@router.post("/api/receipt/{receipt_id}/approve")
def approve_receipt(receipt_id: int, body: ApproveBody, request: Request):
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    if row.status != "edited":
        return JSONResponse(
            content={"status": "error", "code": "INVALID_STATUS",
                     "msg": f"仅 status='edited' 的单据可 approve，当前 status='{row.status}'"},
            status_code=409)
    ver_err = _version_error(body.version, row, "审核")
    if ver_err is not None:
        return ver_err

    old_status = row.status
    # 幂等入账：写 SKU 库存流水 + 累计
    from app.services.inventory import apply_receipt_to_inventory
    apply_receipt_to_inventory(row)
    db.update_receipt(receipt_id, status="approved", version=row.version + 1)
    db.append_audit_log(receipt_id, who, "approve_receipt", "status", old_status, "approved")
    row = db.get_receipt_row(receipt_id)
    # 供应商自动建档（approve 后成为正式供应商）
    if row.supplier_name and db.find_supplier_by_name(row.supplier_name) is None:
        db.create_supplier(row.supplier_name)
    # 回写 VendorMemory（只认正向信号：老板批准）
    from app.services.rag import ingest_memory
    items_text = "\n".join(f"- {i['name']} {i['quantity']}{i['unit']} @{i['unit_price']} = {i['amount']}"
                           for i in db.get_receipt_items(receipt_id))
    ingest_memory(row.supplier_name, items_text, notes=f"版式：{row.doc_form}")
    # C6 ai_decision_log：approve 回填最终确认 + 幻觉判定
    try:
        import json as _j
        ai_prefill = _j.loads(row.ai_prefill_json or "{}")
        ai_items = ai_prefill.get("items", [])
        final_items = db.get_receipt_items(receipt_id)
        for i, fi in enumerate(final_items):
            if i < len(ai_items):
                ai_val = ai_items[i].get("unit_price", "")
                user_val = fi.get("unit_price", "")
                adopted = 1 if str(ai_val) == str(user_val) else 0
                _track_ai_decision(account, receipt_id, row=row,
                    decision_type="approve",
                    field_path=f"items[{i}].unit_price",
                    ai_value=ai_val, user_value=user_val,
                    adopted=adopted, engine="human")
    except Exception as e:
        import logging
        logging.getLogger("api_receipts").warning(f"[WARN] 记录审批 AI 决策失败: {e}")
    return {"status": "success", "version": row.version}


@router.post("/api/receipt/{receipt_id}/flag")
def flag_receipt(receipt_id: int, request: Request):
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    old_status = row.status
    if old_status == "flagged":
        return {"status": "success",
                "msg": f"单据 #{receipt_id} 已处于 flagged 状态，幂等跳过。"}
    # 对齐完整版：uploaded/parsed/edited → flagged；approved 409
    if old_status not in ("uploaded", "parsed", "edited"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"非法状态转移 [{old_status}] → [flagged]：仅 uploaded/parsed/edited 单据可标记异常"
                            f"（已审核单据的问题修正必须走冲销路径）。"},
            status_code=409)
    db.update_receipt(receipt_id, status="flagged")
    db.append_audit_log(receipt_id, who, "flag_receipt", "status", old_status, "flagged")
    return {"status": "success", "msg": f"单据 #{receipt_id} 已标记为异常"}


@router.post("/api/receipt/{receipt_id}/retry")
def retry_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    old_status = row.status
    if old_status not in ("uploaded", "error"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"当前状态 [{old_status}] 不允许重试：仅 uploaded/error 单据可重跑识别。"},
            status_code=409)
    if not row.image_path or not os.path.exists(row.image_path):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"原图缺失（{row.image_path}），无法重试；请重新上传或转手工录入。"},
            status_code=400)
    job_id, _ = start_recognition_job(row.image_path, receipt_id=receipt_id)
    return {"status": "queued", "job_id": job_id, "receipt_id": receipt_id,
            "version": row.version}


@router.post("/api/receipt/{receipt_id}/convert_manual")
def convert_manual(receipt_id: int, request: Request):
    # 对齐完整版：转手工录入改写单据形态与状态，需 owner
    require_role("owner")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    old_status = row.status
    if old_status not in ("uploaded", "parsed", "error"):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"当前状态 [{old_status}] 不允许转手工录入：仅 uploaded/parsed/error 单据可转换。"},
            status_code=409)
    db.update_receipt(receipt_id, status="edited", doc_form="manual_entry")
    row = db.get_receipt_row(receipt_id)
    return {"status": "success", "receipt_id": receipt_id,
            "image_url": "/uploads/" + (row.image_path.split("/")[-1] if row.image_path else ""),
            "version": row.version,
            "data": build_detail(row),
            "msg": f"单据 #{receipt_id} 已转为手工录入，请补全明细后保存"}


@router.post("/api/receipt/{receipt_id}/discard")
def discard_receipt(receipt_id: int, request: Request):
    require_role("staff")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "code": "RECEIPT_NOT_FOUND",
                                     "msg": "单据不存在或已被删除"}, status_code=404)
    if row.status == "approved":
        return JSONResponse(
            content={"status": "error", "code": "CANNOT_DISCARD_APPROVED",
                     "msg": "该单据已审核确认并入账，无法直接放弃删除；请使用冲销功能修正。"},
            status_code=409)
    # 软删除：保留单据（含原图），进入回收站
    db.soft_delete_receipt(receipt_id)
    db.append_audit_log(receipt_id, who, "discard_receipt", "deleted_at", None, "now")
    return {"status": "success", "receipt_id": receipt_id,
            "msg": "已放弃该单据（进入回收站，可在管理后台恢复）"}


class PayDateBody(BaseModel):
    expected_pay_date: Optional[str] = None


@router.post("/api/receipt/{receipt_id}/pay_date")
def set_pay_date(receipt_id: int, body: PayDateBody, request: Request):
    require_role("owner")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 对齐完整版：null/空 = 清空；非法日期 400；不 bump version
    import re as _re
    from datetime import datetime as _dt
    new_date = None
    raw = (body.expected_pay_date or "").strip()
    if raw:
        if not _re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
            return JSONResponse(
                content={"status": "error",
                         "msg": f"expected_pay_date 必须为 YYYY-MM-DD 格式日期，当前值：'{raw}'"},
                status_code=400)
        try:
            _dt.strptime(raw, "%Y-%m-%d")
        except ValueError:
            return JSONResponse(
                content={"status": "error",
                         "msg": f"expected_pay_date '{raw}' 不是真实存在的日期"},
                status_code=400)
        new_date = raw
    row = db.set_expected_pay_date(receipt_id, new_date)
    from app.services.receipt_utils import build_detail
    return {"status": "success", "receipt_id": receipt_id,
            "expected_pay_date": row.expected_pay_date,
            "payment_status": build_detail(row).get("payment_status"),
            "version": row.version}


# -------------------------------------------------------------
# 采纳 AI 建议（阶段 1：AI 可见性与信任）
# 前端在「AI 识别 vs 当前值」对照面板点击「采纳 AI」按钮时调用，
# 记录一次 ai_suggestion_adopted 审计事件（字段级，含 old/new）。
# 仅记录审计日志，不改动单据其他字段（前端侧已把表单值同步为 AI 值）。
# -------------------------------------------------------------
class AdoptAIBody(BaseModel):
    field: str
    old: Optional[object] = None
    new: Optional[object] = None
    # 兼容前端/测试的 ai_value/user_value 命名
    ai_value: Optional[object] = None
    user_value: Optional[object] = None
    # 乐观锁：采纳 AI 建议也是写操作，须校验 version
    version: Optional[object] = None


@router.post("/api/receipt/{receipt_id}/adopt-ai")
def adopt_ai(receipt_id: int, body: AdoptAIBody, request: Request):
    require_role("staff")(request)
    row = db.get_receipt_row(receipt_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到指定收据"},
                            status_code=404)
    # 乐观锁校验（缺失 → 400 VERSION_REQUIRED；不匹配 → 409 VERSION_CONFLICT）
    ver_err = _version_error(body.version, row, "采纳 AI")
    if ver_err is not None:
        return ver_err
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    field = body.field or ""
    # 字段名安全：只允许 items[n].field 格式，避免注入
    m = re.match(r"^items\[(\d+)\]\.(qty|quantity|unit_price|amount|unit)$", field)
    if not m:
        return JSONResponse(
            content={"status": "error",
                     "msg": f"非法 field 值：'{field}'（须形如 items[0].qty）"},
            status_code=400)
    idx = int(m.group(1))
    # 越界下标：items[N] 必须在 0..len(row.items)-1 范围内
    items = db.get_receipt_items(receipt_id)
    if idx < 0 or idx >= len(items):
        return JSONResponse(
            content={"status": "error",
                     "msg": f"items 下标越界：items[{idx}]，当前单据仅有 {len(items)} 条明细（0..{max(len(items)-1, 0)}）"},
            status_code=400)
    new_val = body.new if body.new is not None else body.ai_value
    old_val = body.old if body.old is not None else body.user_value
    # XSS 防护：对字符串字段做 html.escape，避免 payload 写入 audit_logs_json 后被 innerHTML 渲染
    import html as _html
    def _safe(v):
        return _html.escape(str(v)) if isinstance(v, str) else v
    db.append_audit_log(
        receipt_id,
        who, "ai_suggestion_adopted",
        field, _safe(old_val), _safe(new_val),
    )
    return {"status": "success", "receipt_id": receipt_id,
            "field": field, "new": new_val, "version": row.version}

