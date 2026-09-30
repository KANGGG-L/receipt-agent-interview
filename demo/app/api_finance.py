# -*- coding: utf-8 -*-
"""支付 + 对账端点（简化实现，界面可用的最小功能）。"""

import json
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db
from app.auth import require_role, resolve_tenant
from app.services.receipt_utils import (
    verify_voucher_image_signature, voucher_image_signature,
)

router = APIRouter()


def _tenant_id(request: Request) -> str:
    """Gap E2 租户键：委托 app.auth.resolve_tenant（生产态由身份派生，开发态读 X-Tenant-Id）。"""
    return resolve_tenant(request)

BASE_DIR = Path(__file__).resolve().parent.parent
VOUCHER_DIR = BASE_DIR / "uploads" / "vouchers"
VOUCHER_DIR.mkdir(parents=True, exist_ok=True)


# -------------------------------------------------------------
# 付款凭证取图：签名端点（/uploads 静态挂载已撤销后的唯一出口）
# -------------------------------------------------------------
def _resolve_voucher_file(raw):
    """把库里的凭证名归一到 VOUCHER_DIR 内的绝对路径；越界返回 None。

    why: 凭证端点不经请求头鉴权对外暴露，必须防 `../` 路径穿越与绝对路径注入。先
    basename 归一（丢弃任何目录成分），再 resolve 后校验父目录链落在模块级
    VOUCHER_DIR 内（resolve 同时穿透符号链接）。用模块级常量以便测试 monkeypatch 到
    临时目录。与 api_receipts._resolve_upload_file 同构，后者收敛的是原图目录
    （uploads/），凭证目录是它的子目录，故各用各的模块级根。
    """
    if not raw:
        return None
    name = os.path.basename(str(raw).strip())
    if not name or name in (".", ".."):
        return None
    base = Path(VOUCHER_DIR).resolve()
    try:
        candidate = (base / name).resolve()
    except OSError:
        return None
    if base not in candidate.parents:
        return None
    return candidate


def _voucher_basename(raw):
    """库内凭证字段 → 纯文件名。

    兼容两种写法：早期落库的 `/uploads/vouchers/<name>` 路径，与当前落库的相对名
    `<name>`。两者一律按 basename 归一，读侧无需 DB 迁移（旧数据继续可用）。
    """
    if not raw:
        return ""
    return os.path.basename(str(raw).strip().replace("\\", "/"))


def public_voucher_url(name, tenant_id="default"):
    """付款凭证对外 URL：签名取图端点 GET /api/payment/voucher/{name}。

    why: `/uploads` 静态挂载已撤销，凭证不再有「直链文件」形态。签名绑定
    「凭证文件名 + 租户」，与收据原图端点同构（见 receipt_utils.voucher_image_signature）；
    `?v=` 与路径段同为该文件名，端点据此把签名主体与真实资源严格对齐。
    非默认租户追加 `&tenant_id=`，端点据此完成租户归属校验；default 省略该参数。
    凭证名为空时返回 ""（语义为「无凭证」），由消费方按空值降级。
    """
    bare = _voucher_basename(name)
    if not bare:
        return ""
    tenant = str(tenant_id or "").strip() or "default"
    url = "/api/payment/voucher/%s?v=%s&sig=%s" % (
        quote(bare, safe=""), quote(bare, safe=""),
        voucher_image_signature(bare, tenant))
    if tenant != "default":
        url += "&tenant_id=" + quote(tenant, safe="")
    return url


def _voucher_tenant_owns(name, tenant):
    """该租户的付款行里是否存在引用此凭证名的行（租户归属校验）。

    why: 凭证文件名可猜，签名只保证「URL 由后端签发」；同一目录下的文件不区分租户，
    必须再按「本租户确有付款行引用它」判定归属，避免 A 租户拿到的签名 URL 读到
    B 租户的凭证。判定失败一律视为无归属（宁拒不放）。
    """
    try:
        for p in db.list_payments(tenant_id=tenant):
            if _voucher_basename(p.get("voucher_image_url")) == name:
                return True
    except Exception as e:
        logging.getLogger("api_finance").warning(f"[WARN] 凭证租户归属判定失败: {e}")
        return False
    return False


@router.get("/api/payment/voucher/{name}")
def get_payment_voucher(name: str, request: Request):
    """返回付款凭证原件（图片原样透传，内容类型按扩展名判定）。

    why 刻意不加 require_role：凭证由 `<a href target=_blank>` 在浏览器原生发起，
    无法携带 X-Role / Authorization 等请求头，加请求头 RBAC 会让凭证请求全部 401。
    防护改为三重业务校验：HMAC 签名（绑定 凭证名 + 租户）、租户归属（本租户确有付款行
    引用它）、文件落在 VOUCHER_DIR 内（防路径穿越）。签名校验置于任何 DB / 磁盘访问
    之前，未签名扫描不产生查询与 IO 开销。
    """
    tenant = (request.query_params.get("tenant_id") or "").strip() or _tenant_id(request)
    bare = _voucher_basename(name)
    stem = (request.query_params.get("v") or "").strip()
    if not bare or stem != bare or not verify_voucher_image_signature(
            bare, tenant, request.query_params.get("sig")):
        return JSONResponse(status_code=403, content={
            "status": "error", "code": "VOUCHER_SIGNATURE_INVALID",
            "msg": "凭证链接签名无效或缺失"})
    if not _voucher_tenant_owns(bare, tenant):
        return JSONResponse(status_code=404, content={
            "status": "error", "code": "VOUCHER_NOT_FOUND", "msg": "凭证不存在"})
    src = _resolve_voucher_file(bare)
    if src is None or not src.is_file():
        return JSONResponse(status_code=404, content={
            "status": "error", "code": "VOUCHER_NOT_FOUND", "msg": "凭证文件缺失"})
    guessed, _ = mimetypes.guess_type(str(src))
    return FileResponse(str(src), media_type=guessed or "application/octet-stream")


# -------------------------------------------------------------
# 支付
# -------------------------------------------------------------
@router.get("/api/payments")
def list_payments(request: Request, supplier_id: int = None):
    """支付列表。supplier_id 非空时只返回该供应商的支付。

    why（口径分叉修复 + D16 租户隔离）：原先 `data = db.list_payments()` 无任何
    过滤，不带 supplier_id 时把**全租户**付款一并返回；带 supplier_id 时又用
    「按供应商名匹配」这套跨租户回退口径（同名档案会串号）。
    现改为：
    - 基础列表透传请求租户（db.list_payments(tenant_id=...)），天然只返回本租户；
    - supplier_id 过滤改按付款行自带的 supplier_id 精确关联（删除按名匹配的
      跨租户回退逻辑）；供应商不存在或跨租户 -> 空列表（不退回全量）。

    凭证 URL 在返回前就地改写为后端签发的签名端点地址：历史遗留的库内字符串
    `/uploads/vouchers/<name>` 与当前落库的相对名走同一条 basename 归一逻辑，免
    DB 迁移（旧数据继续可用），且响应里不再出现任何 `/uploads/` 出口。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    data = db.list_payments(tenant_id=tenant_id)
    for p in data:
        p["voucher_image_url"] = public_voucher_url(
            p.get("voucher_image_url"), tenant_id)
    if supplier_id:
        # 供应商按租户校验：不存在或跨租户 -> 空列表（不退回全量）
        sup = db.get_supplier(supplier_id, tenant_id=tenant_id)
        if not sup:
            return {"status": "success", "data": []}
        data = [p for p in data if p.get("supplier_id") == supplier_id]
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
    voucher_name = ""
    if voucher and voucher.filename:
        ext = os.path.splitext(voucher.filename)[1].lower() or ".jpg"
        path = VOUCHER_DIR / f"{db.new_id()}{ext}"
        with open(path, "wb") as f:
            import shutil
            shutil.copyfileobj(voucher.file, f)
        # 落库存相对名（不含任何 `/uploads/vouchers/` 前缀）：库内不再出现指向静态
        # 目录的字符串，URL 一律由读侧按租户签发（见 public_voucher_url）。
        voucher_name = path.name

    payment_id = db.create_payment(supplier_id, amount, method, paid_at,
                                   notes, voucher_name, receipt_ids or [],
                                   tenant_id=_tenant_id(request))
    return {"status": "success", "msg": "付款已登记", "id": payment_id}


# -------------------------------------------------------------
# 对账（简化：单供应商一次生成，行级匹配）
# -------------------------------------------------------------
RECON_TASKS = {}


@router.get("/api/reconciliation")
def list_reconciliation(request: Request, supplier_id: int = None):
    """对账任务列表。supplier_id 非空时只返回该供应商的任务（前端选中供应商即传该参数）。

    why: 该形参此前被声明却零使用，前端 `main.js` 的 `loadReconTasks()` 确实按选中
    供应商拼了 `?supplier_id=`，于是「选中供应商」筛选对列表完全无效、始终返回全量。
    """
    require_role("owner")(request)
    out = []
    for t in RECON_TASKS.values():
        if supplier_id and t["supplier_id"] != supplier_id:
            continue
        supplier = db.get_supplier(t["supplier_id"], tenant_id=_tenant_id(request))
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
    supplier = db.get_supplier(body.supplier_id, tenant_id=_tenant_id(request))
    if supplier is None:
        return {"status": "error", "msg": "供应商不存在"}
    task_id = db.new_id()
    # 生成对账行：该供应商已 approve 收据（仅当前租户）
    lines = []
    for r in db.list_receipt_rows(tenant_id=_tenant_id(request)):
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
    supplier = db.get_supplier(t["supplier_id"], tenant_id=_tenant_id(request))
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
                                for r in db.list_receipt_rows(tenant_id=_tenant_id(request))
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


class ImportStatementBody(BaseModel):
    supplier_id: int
    statement_period: str = ""
    external_lines: list[dict] = []  # [{"date": "2026-08-01", "invoice_no": "INV-001", "amount": 500.0, "notes": ""}]


@router.post("/api/reconciliation/import-statement")
def import_supplier_statement(body: ImportStatementBody, request: Request):
    """双边对账导入：导入供应商月结对账单，并创建双向核对任务。"""
    require_role("owner")(request)
    from fastapi.responses import JSONResponse

    supplier = db.get_supplier(body.supplier_id, tenant_id=_tenant_id(request))
    if supplier is None:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "供应商不存在"})

    task_id = db.new_id()
    # 系统内该供应商 approved/edited 的收据（仅当前租户）
    system_receipts = [
        r for r in db.list_receipt_rows(tenant_id=_tenant_id(request))
        if r.supplier_name == supplier.name and r.status in ("approved", "edited")
    ]

    sys_total = round(sum(r.total_amount or 0.0 for r in system_receipts), 2)
    ext_total = round(sum(float(line.get("amount", 0.0)) for line in body.external_lines), 2)
    diff = round(ext_total - sys_total, 2)

    # 简单双向匹配算法
    matched_lines = []
    unmatched_ext = []
    unmatched_sys = {r.id: r for r in system_receipts}

    for ext in body.external_lines:
        ext_amt = float(ext.get("amount", 0.0))
        ext_date = ext.get("date", "")
        # 寻找匹配的系统收据（同金额，或同日期）
        found_rid = None
        for rid, sys_r in list(unmatched_sys.items()):
            if abs((sys_r.total_amount or 0.0) - ext_amt) < 0.01:
                found_rid = rid
                break
        if found_rid is not None:
            del unmatched_sys[found_rid]
            matched_lines.append({
                "type": "matched",
                "external": ext,
                "system_receipt_id": found_rid,
                "amount": ext_amt,
            })
        else:
            unmatched_ext.append({
                "type": "vendor_only",
                "external": ext,
                "amount": ext_amt,
                "issue": "供应商多记/系统缺失单据",
            })

    sys_only_lines = [
        {
            "type": "restaurant_only",
            "receipt_id": r.id,
            "receipt_date": r.receipt_date or "",
            "amount": r.total_amount or 0.0,
            "issue": "系统内已记/供应商月结未列出",
        }
        for r in unmatched_sys.values()
    ]

    RECON_TASKS[task_id] = {
        "id": task_id,
        "supplier_id": body.supplier_id,
        "statement_period": body.statement_period,
        "status": "reconciling",
        "external_total": ext_total,
        "system_total": sys_total,
        "diff_amount": diff,
        "matched": matched_lines,
        "discrepancies": unmatched_ext + sys_only_lines,
    }

    return {
        "status": "success",
        "task_id": task_id,
        "summary": {
            "external_statement_total": ext_total,
            "system_receipts_total": sys_total,
            "diff_amount": diff,
            "discrepancy_count": len(unmatched_ext) + len(sys_only_lines),
        },
        "discrepancies": unmatched_ext + sys_only_lines,
    }


@router.get("/api/reconciliation/{task_id}/discrepancies")
def get_reconciliation_discrepancies(task_id: str, request: Request):
    """获取双边对账差异核对结果。"""
    require_role("owner")(request)
    from fastapi.responses import JSONResponse
    t = RECON_TASKS.get(task_id)
    if t is None:
        return JSONResponse(status_code=404, content={"status": "error", "msg": "对账任务不存在"})
    return {
        "status": "success",
        "task_id": task_id,
        "external_total": t.get("external_total", 0.0),
        "system_total": t.get("system_total", 0.0),
        "diff_amount": t.get("diff_amount", 0.0),
        "discrepancies": t.get("discrepancies", []),
    }

