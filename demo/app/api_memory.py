# -*- coding: utf-8 -*-
"""待确认记忆治理端点（T8 Gap D1：规则级记忆写入人工闸）。

- GET  /api/memory/pending                列表（可按 status 过滤，admin）
- POST /api/memory/pending/{id}/approve   批准：经既有 upsert 链路落
                                          vendor_memory + Chroma（治理元数据齐全）
- POST /api/memory/pending/{id}/reject    拒绝：不入任何记忆库

流转仅 pending → approved/rejected；已审过的再操作返回 409。
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import db
from app.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()


def _tenant_id(request: Request) -> str:
    return (request.headers.get("X-Tenant-Id")
            or request.headers.get("x-tenant-id") or "default").strip() or "default"


@router.get("/api/memory/pending")
def list_pending(request: Request, status: str = ""):
    """待确认记忆列表（id 倒序）。status 可选：pending|approved|rejected。"""
    require_admin(request)
    tenant_id = _tenant_id(request)
    rows = db.list_pending_memory(status=status or None, tenant_id=tenant_id)
    return {"status": "success", "data": rows}


@router.post("/api/memory/pending/{pending_id}/approve")
def approve_pending(pending_id: int, request: Request):
    """批准待确认记忆：走既有 upsert 链路落 vendor_memory + Chroma。"""
    require_admin(request)
    tenant_id = _tenant_id(request)
    row = db.get_pending_memory(pending_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该待确认记忆"},
                            status_code=404)
    if row["status"] != "pending":
        return JSONResponse(
            content={"status": "error", "code": "ALREADY_REVIEWED",
                     "msg": f"该记忆已审核（当前状态：{row['status']}），不可重复操作"},
            status_code=409)
    account = getattr(request.state, "account", {})
    who = account.get("email", "admin") if isinstance(account, dict) else "admin"
    try:
        refs = json.loads(row.get("source_receipt_ids_json") or "[]")
        if not isinstance(refs, list):
            refs = []
    except Exception:
        refs = []
    from app.services.rag import approve_pending_to_memory
    try:
        approve_pending_to_memory(
            row["vendor"], row["content"], tenant_id=row["tenant_id"],
            source_receipt_ids=refs)
    except Exception as e:
        logger.warning("[memory] approve ingest failed id=%s: %s", pending_id, e)
        return JSONResponse(
            content={"status": "error", "msg": f"记忆落库失败：{e}"},
            status_code=500)
    ok = db.review_pending_memory(pending_id, "approved", who, tenant_id=tenant_id)
    if not ok:
        return JSONResponse(content={"status": "error", "msg": "状态流转失败，请重试"},
                            status_code=409)
    try:
        db.append_system_audit_log(
            who, "pending_memory_approve", f"pending_memory:{pending_id}",
            "pending", "approved")
    except Exception as e:
        logging.getLogger("api_memory").warning(
            "审计写入失败(action=pending_memory_approve pending_id=%s who=%s): %s",
            pending_id, who, e)
    return {"status": "success", "data": db.get_pending_memory(pending_id,
                                                               tenant_id=tenant_id)}


@router.post("/api/memory/pending/{pending_id}/reject")
def reject_pending(pending_id: int, request: Request):
    """拒绝待确认记忆：仅流转状态，不写任何记忆库。"""
    require_admin(request)
    tenant_id = _tenant_id(request)
    row = db.get_pending_memory(pending_id, tenant_id=tenant_id)
    if row is None:
        return JSONResponse(content={"status": "error", "msg": "未找到该待确认记忆"},
                            status_code=404)
    if row["status"] != "pending":
        return JSONResponse(
            content={"status": "error", "code": "ALREADY_REVIEWED",
                     "msg": f"该记忆已审核（当前状态：{row['status']}），不可重复操作"},
            status_code=409)
    account = getattr(request.state, "account", {})
    who = account.get("email", "admin") if isinstance(account, dict) else "admin"
    ok = db.review_pending_memory(pending_id, "rejected", who, tenant_id=tenant_id)
    if not ok:
        return JSONResponse(content={"status": "error", "msg": "状态流转失败，请重试"},
                            status_code=409)
    try:
        db.append_system_audit_log(
            who, "pending_memory_reject", f"pending_memory:{pending_id}",
            "pending", "rejected")
    except Exception as e:
        logging.getLogger("api_memory").warning(
            "审计写入失败(action=pending_memory_reject pending_id=%s who=%s): %s",
            pending_id, who, e)
    return {"status": "success", "data": db.get_pending_memory(pending_id,
                                                               tenant_id=tenant_id)}
