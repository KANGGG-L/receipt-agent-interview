# -*- coding: utf-8 -*-
"""单据生命周期的老板端接口：冲销、回收站、重复货品确认合并。

- POST   /api/receipt/{id}/reverse        冲销已入账单据（owner）
- GET    /api/trash/receipts              回收站列表（owner，按租户）
- POST   /api/trash/receipts/{id}/restore 从回收站恢复（owner）
- DELETE /api/trash/receipts/{id}         彻底删除回收站里的单据（owner）
- GET    /api/inventory/sku-duplicates    重复货品巡检结果（owner，只读）
- POST   /api/inventory/sku-duplicates/merge  老板确认后合并（owner）
"""

import logging
import os
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import db
from app.auth import require_role, resolve_tenant

router = APIRouter()
_LOCK = db.receipt_write_lock
log = logging.getLogger("api_receipt_lifecycle")


def _err(status, code, msg, **extra):
    return JSONResponse(status_code=status,
                        content={"status": "error", "code": code, "msg": msg, **extra})


class ReverseBody(BaseModel):
    version: Optional[int] = None
    reason: str = ""


@router.post("/api/receipt/{receipt_id}/reverse")
def reverse_receipt(receipt_id: int, body: ReverseBody, request: Request):
    """冲销一张已入账的单据（老板专用）。

    入账时写入的库存与批次被反向冲减，原单标记为「已冲销」，原因与操作人写进审计。
    同一张单重复冲销是安全的：第二次直接返回成功（already_reversed=true），不会再扣一次。
    """
    from app.api_receipts import _version_error
    from app.services.inventory import ReversalBlocked, reverse_receipt_inventory

    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    tenant_id = resolve_tenant(request)
    reason = (body.reason or "").strip()

    with _LOCK:
        row = db.get_receipt_row(receipt_id, tenant_id=tenant_id)
        if row is None or row.deleted_at:
            return _err(404, "RECEIPT_NOT_FOUND", "找不到这张单，可能已被删除。请刷新页面后再试。")
        if row.status == "reversed":
            return {"status": "success", "already_reversed": True, "version": row.version,
                    "msg": f"单据 #{receipt_id} 之前已经冲销过了，库存没有再变动。"}
        if row.status != "approved":
            return _err(409, "NOT_APPROVED",
                        "只有已经入账的单据才能冲销。这张单还没入账，直接修改或放弃就行。")
        if not reason:
            return _err(422, "REASON_REQUIRED", "请写明冲销的原因（例如：数量录错了），方便以后查账。")
        ver_err = _version_error(body.version, row, "冲销")
        if ver_err is not None:
            return ver_err
        try:
            with db.atomic():
                written = reverse_receipt_inventory(
                    receipt_id, who, reason=reason, tenant_id=tenant_id)
                db.update_receipt(
                    receipt_id, status="reversed", version=(row.version or 1) + 1,
                    reversed_at=db.now_iso(), reversed_by=who, reverse_reason=reason)
                db.append_audit_log(
                    receipt_id, who, "reverse_receipt", "status", "approved", "reversed",
                    details={"reason": reason, "stock_lines": len(written)})
        except ReversalBlocked as e:
            return _err(409, "REVERSAL_STOCK_CONSUMED", e.msg, data={"items": e.details})
        paid = bool(row.paid_at or row.payment_id)
    resp = {"status": "success", "already_reversed": False, "version": (row.version or 1) + 1,
            "reversed_lines": len(written),
            "msg": f"单据 #{receipt_id} 已冲销，库存已扣回。"}
    if paid:
        resp["paid_notice"] = "这张单之前登记过付款，冲销不会自动退款，请在付款记录里另行处理。"
    return resp


def _trash_row(r):
    return {
        "id": r.id,
        "supplier_name": r.supplier_name or "",
        "receipt_date": r.receipt_date or "",
        "total_amount": r.total_amount or 0.0,
        "status": r.status,
        "created_at": r.created_at or "",
        "deleted_at": r.deleted_at or "",
        "deleted_by": getattr(r, "deleted_by", None) or "",
    }


@router.get("/api/trash/receipts")
def list_trash(request: Request):
    require_role("owner")(request)
    rows = db.list_trash_receipts(tenant_id=resolve_tenant(request))
    return {"status": "success", "data": [_trash_row(r) for r in rows], "total": len(rows)}


@router.post("/api/trash/receipts/{receipt_id}/restore")
def restore_from_trash(receipt_id: int, request: Request):
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    with _LOCK:
        row = db.get_receipt_row(receipt_id, tenant_id=resolve_tenant(request))
        if row is None or not row.deleted_at:
            return _err(404, "NOT_IN_TRASH", "回收站里没有这张单，可能已经恢复或被彻底删除。请刷新页面。")
        with db.atomic():
            db.restore_receipt(receipt_id)
            db.update_receipt(receipt_id, deleted_by="")
            db.append_audit_log(receipt_id, who, "restore_receipt", "deleted_at",
                                row.deleted_at, None)
    return {"status": "success", "receipt_id": receipt_id,
            "msg": f"单据 #{receipt_id} 已从回收站恢复。"}


@router.delete("/api/trash/receipts/{receipt_id}")
def purge_from_trash(receipt_id: int, request: Request):
    """彻底删除回收站里的单据（不可恢复）。已入账/已冲销的单据有库存与账务痕迹，不允许删除。"""
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    with _LOCK:
        row = db.get_receipt_row(receipt_id, tenant_id=resolve_tenant(request))
        if row is None or not row.deleted_at:
            return _err(404, "NOT_IN_TRASH", "回收站里没有这张单，可能已经恢复或被彻底删除。请刷新页面。")
        if row.status in ("approved", "reversed"):
            return _err(409, "CANNOT_PURGE_APPROVED",
                        "这张单有入账记录，不能彻底删除。请先恢复，需要作废的话用「冲销」。")
        image_path = row.image_path
        summary = f"#{receipt_id} {row.supplier_name or ''} {row.receipt_date or ''} {row.total_amount or 0}"
        with db.atomic():
            db.delete_receipt(receipt_id)
            db.append_system_audit_log(who, "purge_receipt", "receipt", summary, "deleted")
    # 原图：没有任何别的单据引用时才删，避免误伤
    try:
        from app.api_receipts import _resolve_upload_file, _cleanup_unreferenced_preview_cache
        if image_path and image_path not in db.list_all_image_paths():
            f = _resolve_upload_file(image_path)
            if f is not None and os.path.isfile(f):
                os.remove(f)
            _cleanup_unreferenced_preview_cache(image_path)
    except Exception as e:  # noqa: BLE001 清理原图失败不影响删除结果
        log.warning("彻底删除单据 #%s 后清理原图失败: %s", receipt_id, e)
    return {"status": "success", "receipt_id": receipt_id,
            "msg": f"单据 #{receipt_id} 已彻底删除。"}


@router.get("/api/inventory/sku-duplicates")
def list_sku_duplicates(request: Request):
    """重复货品巡检（只读）：列出可以合并的货品分组，等老板确认后再合并。"""
    require_role("owner")(request)
    groups = db.find_duplicate_sku_groups(tenant_id=resolve_tenant(request)) or []
    return {"status": "success", "data": groups, "total": len(groups)}


@router.post("/api/inventory/sku-duplicates/merge")
def merge_sku_duplicates(request: Request):
    """老板确认后，合并本租户的重复货品（流水与库存并入主货品，副货品停用）。"""
    require_role("owner")(request)
    account = getattr(request.state, "account", {})
    who = account.get("email", "unknown")
    tenant_id = resolve_tenant(request)
    with db.atomic():
        reports = db.deduplicate_skus_by_canonical(tenant_id=tenant_id) or []
        if reports:
            db.append_system_audit_log(who, "confirm_merge_duplicate_skus", "skus",
                                       f"{len(reports)} groups", "merged")
    return {"status": "success", "merged_groups": len(reports), "data": reports,
            "msg": f"已合并 {len(reports)} 组重复货品。" if reports else "没有需要合并的重复货品。"}
