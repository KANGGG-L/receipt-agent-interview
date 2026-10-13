# -*- coding: utf-8 -*-
"""支付 + 对账端点（简化实现，界面可用的最小功能）。"""

import json
import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional

from app import db, recon_store
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
    return _voucher_response(src)


_INLINE_IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                       ".webp": "image/webp", ".gif": "image/gif"}
_SAFE_HEADERS = {"X-Content-Type-Options": "nosniff",
                 "Content-Security-Policy": "default-src 'none'; sandbox"}


def _voucher_response(src):
    """只有图片允许在页面内显示；PDF 和其他文件一律作为下载件，且不再以 text/html 回放。"""
    ext = src.suffix.lower()
    if ext in _INLINE_IMAGE_TYPES:
        return FileResponse(str(src), media_type=_INLINE_IMAGE_TYPES[ext],
                            content_disposition_type="inline", headers=_SAFE_HEADERS)
    media = "application/pdf" if ext == ".pdf" else "application/octet-stream"
    return FileResponse(str(src), media_type=media, filename="voucher" + (ext if ext in (".pdf",) else ".bin"),
                        content_disposition_type="attachment", headers=_SAFE_HEADERS)


# -------------------------------------------------------------
# 付款凭证上传：只收图片和 PDF
# -------------------------------------------------------------
_PDF_DANGEROUS = (b"/JavaScript", b"/JS", b"/Launch", b"/EmbeddedFile", b"/OpenAction", b"/AA")
_VOUCHER_PDF_MAX_BYTES = 15 * 1024 * 1024


class VoucherRejected(Exception):
    def __init__(self, msg):
        super().__init__(msg)
        self.msg = msg


def _save_voucher(upload) -> str:
    """校验并保存付款凭证，返回库内文件名。

    图片：按文件内容判断格式（不信文件名）、限大小、重新编码去掉夹带内容。
    PDF：按文件头判断，限大小，含脚本或自动执行动作的拒绝。其他类型一律拒绝。
    """
    from app import api_receipts as ar
    try:
        data = ar._read_upload_bounded(upload)
    except ar.UploadRejected as e:
        raise VoucherRejected(e.msg) from e
    if not data:
        raise VoucherRejected("凭证文件是空的，请重新选择文件")
    if data[:5] == b"%PDF-":
        if len(data) > _VOUCHER_PDF_MAX_BYTES:
            raise VoucherRejected("凭证文件太大，请压缩后再上传")
        if any(tok in data for tok in _PDF_DANGEROUS):
            raise VoucherRejected("这个 PDF 里带有脚本或自动执行的内容，不能当凭证。请换成照片或普通的 PDF")
        ext, clean = ".pdf", data
    else:
        family = ar._detect_image_family(data[:12])
        if family is None:
            raise VoucherRejected("凭证只能上传照片（JPG、PNG 等）或 PDF 文件，请换一个文件")
        try:
            clean = ar._sanitize_upload_bytes(data, family)
        except ar.UploadRejected as e:
            raise VoucherRejected(e.msg) from e
        ext = ar._FAMILY_OUTPUT[family][0]
    path = VOUCHER_DIR / f"{db.new_id()}{ext}"
    with open(path, "wb") as f:
        f.write(clean)
    return path.name


# -------------------------------------------------------------
# 支付
# -------------------------------------------------------------
PAYMENT_METHODS = ("cash", "bank_transfer", "cheque", "fps")
_METHOD_LABEL = {"cash": "现金", "bank_transfer": "银行转账", "cheque": "支票", "fps": "转数快"}


def _err(status, code, msg, **extra):
    body = {"status": "error", "code": code, "msg": msg}
    body.update(extra)
    return JSONResponse(status_code=status, content=body)


def _money(x):
    from app.money import money
    return money(x)


@router.get("/api/payments")
def list_payments(request: Request, supplier_id: int = None):
    """支付列表。supplier_id 非空时只返回该供应商的支付（按付款行自带的 supplier_id）。

    每条付款带 allocations（这笔钱分给了哪几张单据、各多少），部分付款因此可追溯。
    凭证 URL 在返回前改写为后端签发的签名地址。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    data = db.list_payments(tenant_id=tenant_id)
    for p in data:
        p["voucher_image_url"] = public_voucher_url(
            p.get("voucher_image_url"), tenant_id)
        p["allocations"] = recon_store.payment_allocations(tenant_id, p["id"])
        allocated = round(sum(a["amount"] for a in p["allocations"]), 2)
        p["unallocated_amount"] = round(max((p.get("amount") or 0.0) - allocated, 0.0), 2)
    if supplier_id:
        sup = db.get_supplier(supplier_id, tenant_id=tenant_id)
        if not sup:
            return {"status": "success", "data": []}
        data = [p for p in data if p.get("supplier_id") == supplier_id]
    return {"status": "success", "data": data}


@router.get("/api/payables")
def get_payables(request: Request, supplier_id: int = None):
    """应付清单：已入账的赊单扣掉已付金额。供应商表的「未付赊单合计」与这里同一口径。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    c = recon_store.compute_payables(tenant_id)
    rows = c["payables"]
    if supplier_id:
        rows = [r for r in rows if r["supplier_id"] == supplier_id]
    open_rows = [r for r in rows if r["outstanding_amount"] > 0]
    return {"status": "success", "data": {
        "rows": rows,
        "total_outstanding": round(sum(r["outstanding_amount"] for r in open_rows), 2),
        "outstanding_count": len(open_rows),
        "overdue_amount": round(sum(r["outstanding_amount"] for r in open_rows if r["is_overdue"]), 2),
        "overdue_count": sum(1 for r in open_rows if r["is_overdue"]),
        "currency": "HKD",
        "basis": "只算已入账的赊单，已付的金额已扣除；外币单据按入账时的汇率折成港币",
    }}


def _parse_paid_at(raw):
    from datetime import date, datetime
    try:
        d = datetime.strptime(str(raw or "").strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None, "付款日期格式不对，请按「2026-10-05」这样填写"
    if len(str(raw).strip()) > 10:
        return None, "付款日期格式不对，请按「2026-10-05」这样填写"
    if d > date.today():
        return None, "付款日期不能是将来的日子，请改成今天或以前"
    return d.isoformat(), None


@router.post("/api/payments")
def create_payment(
    request: Request,
    supplier_id: int = Form(...),
    amount: float = Form(...),
    method: str = Form(...),
    paid_at: str = Form(...),
    notes: str = Form(""),
    receipt_ids: list[int] = Form([]),
    allocations: str = Form(""),
    voucher: UploadFile = File(None),
):
    """登记付款。可以一笔付款分给多张单据，也可以只付一部分。

    - 不传 allocations：按所选单据的日期从早到晚依次付，每张最多付到它未付的金额
    - 传 allocations（JSON，如 [{"receipt_id":3,"amount":50}]）：按指定金额分配，合计必须等于付款金额
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)

    import math
    if not math.isfinite(amount) or amount <= 0:
        return _err(400, "PAYMENT_AMOUNT_INVALID", "付款金额必须大于 0，请重新填写")
    amount = _money(amount)
    if amount <= 0:
        return _err(400, "PAYMENT_AMOUNT_INVALID", "付款金额必须大于 0，请重新填写")
    if method not in PAYMENT_METHODS:
        return _err(400, "PAYMENT_METHOD_INVALID", "付款方式请选：现金、银行转账、支票或转数快")
    paid_date, perr = _parse_paid_at(paid_at)
    if perr:
        return _err(400, "PAYMENT_DATE_INVALID", perr)
    sup = db.get_supplier(supplier_id, tenant_id=tenant_id)
    if sup is None:
        return _err(404, "SUPPLIER_NOT_FOUND", "找不到这个供应商，请刷新页面后重新选择")

    explicit = None
    if allocations and allocations.strip():
        try:
            explicit = json.loads(allocations)
            assert isinstance(explicit, list)
            explicit = [(int(a["receipt_id"]), _money(a["amount"])) for a in explicit]
        except Exception:
            return _err(400, "PAYMENT_ALLOCATION_INVALID", "分配的单据和金额填写有误，请检查后重新提交")
        receipt_ids = [rid for rid, _a in explicit]
    receipt_ids = list(dict.fromkeys(int(x) for x in (receipt_ids or [])))

    with recon_store.write_lock:
        payables = {p["receipt_id"]: p for p in recon_store.compute_payables(tenant_id)["payables"]}
        chosen = []
        for rid in receipt_ids:
            row = db.get_receipt_row(rid, tenant_id=tenant_id)
            if row is None:
                return _err(404, "RECEIPT_NOT_FOUND", "找不到单据 #%d，请刷新页面后重新选择" % rid)
            if row.status != "approved":
                return _err(400, "RECEIPT_NOT_APPROVED",
                            "单据 #%d 还没有入账，不能登记付款。请先让老板审批入账" % rid)
            if row.settlement_type != "credit":
                return _err(400, "RECEIPT_NOT_CREDIT", "单据 #%d 是现结单，不用登记付款" % rid)
            pr = payables.get(rid)
            if pr is None or pr["supplier_id"] != sup.id:
                owner_name = (pr or {}).get("supplier_name") or row.supplier_name or "其他供应商"
                return _err(400, "RECEIPT_SUPPLIER_MISMATCH",
                            "单据 #%d 属于「%s」，不是你选的「%s」，请重新选择" % (rid, owner_name, sup.name))
            if pr["fx_missing"]:
                return _err(400, "FX_RATE_MISSING",
                            "单据 #%d 是外币单，设置里还没有对应汇率，请先到「设置」里填写汇率" % rid)
            if pr["outstanding_amount"] <= 0:
                return _err(409, "RECEIPT_ALREADY_PAID",
                            "单据 #%d 已经付清了，不能重复登记付款。如果是重复录入，请不要再提交" % rid)
            chosen.append(pr)

        plan = []
        if explicit is not None:
            seen = set()
            for rid, amt in explicit:
                if rid in seen:
                    return _err(400, "PAYMENT_ALLOCATION_INVALID", "单据 #%d 重复出现，请合并成一行" % rid)
                seen.add(rid)
                if amt <= 0:
                    return _err(400, "PAYMENT_ALLOCATION_INVALID", "单据 #%d 分到的金额必须大于 0" % rid)
                pr = payables[rid]
                if amt > pr["outstanding_amount"] + 0.005:
                    return _err(400, "PAYMENT_OVER_OUTSTANDING",
                                "单据 #%d 只欠 $%.2f，分到 $%.2f 太多了" % (rid, pr["outstanding_amount"], amt))
                plan.append((rid, amt))
            total_alloc = _money(sum(a for _r, a in plan))
            if abs(total_alloc - amount) > 0.005:
                return _err(400, "PAYMENT_ALLOCATION_MISMATCH",
                            "分配的金额合计 $%.2f，和付款金额 $%.2f 对不上，请核对" % (total_alloc, amount))
        elif chosen:
            need = _money(sum(p["outstanding_amount"] for p in chosen))
            if amount > need + 0.005:
                return _err(400, "PAYMENT_OVER_OUTSTANDING",
                            "付款金额 $%.2f 比所选单据一共欠的 $%.2f 多了 $%.2f，请核对金额或多选几张单据"
                            % (amount, need, amount - need))
            left = amount
            for pr in sorted(chosen, key=lambda p: (p["receipt_date"] or "", p["receipt_id"])):
                if left <= 0.004:
                    break
                part = _money(min(left, pr["outstanding_amount"]))
                plan.append((pr["receipt_id"], part))
                left = _money(left - part)

        voucher_name = ""
        if voucher is not None and voucher.filename:
            try:
                voucher_name = _save_voucher(voucher)
            except VoucherRejected as e:
                return _err(400, "VOUCHER_REJECTED", e.msg)

        from sqlalchemy import text
        eng = recon_store._engine()
        with eng.begin() as conn:
            res = conn.execute(text(
                "INSERT INTO payments (supplier_id, amount, method, paid_at, notes, voucher_image_path, "
                "linked_receipt_ids_json, tenant_id) VALUES (:s,:a,:m,:d,:n,:v,:l,:t)"),
                {"s": sup.id, "a": amount, "m": method, "d": paid_date, "n": notes or "",
                 "v": voucher_name, "l": json.dumps([r for r, _a in plan]), "t": tenant_id})
            payment_id = res.lastrowid
            recon_store.add_allocations(conn, tenant_id, payment_id, plan)
            done = {}
            for rid, amt in plan:
                pr = payables[rid]
                new_out = _money(pr["outstanding_amount"] - amt)
                done[rid] = "paid" if new_out <= 0.004 else "partial"
                if done[rid] == "paid":
                    conn.execute(text(
                        "UPDATE receipts SET paid_at=:d, paid_method=:m, payment_id=:p "
                        "WHERE id=:r AND tenant_id=:t"),
                        {"d": paid_date, "m": method, "p": payment_id, "r": rid, "t": tenant_id})
    return {"status": "success", "msg": "付款已登记", "id": payment_id,
            "data": {"allocations": [{"receipt_id": r, "amount": a, "payment_status": done[r]}
                                     for r, a in plan],
                     "unallocated_amount": _money(amount - sum(a for _r, a in plan))}}


# -------------------------------------------------------------
# 对账
# -------------------------------------------------------------
_PERIOD_LABEL = {"month": "月结", "3d": "3天", "7d": "7天", "14d": "14天"}
_RESOLVE_ACTIONS = ("confirm_match", "write_off", "dispute")
_STATUS_LABEL = {
    "pending": "待核对", "matched": "已匹配", "vendor_only": "对账单有、我方没有",
    "restaurant_only": "我方有、对账单没有", "amount_diff": "单号对上但金额不同",
    "write_off": "核销", "disputed": "争议",
}
_OPEN_STATUSES = ("pending", "vendor_only", "restaurant_only", "amount_diff")


def _period_bounds(period_type, start, end):
    from calendar import monthrange
    from datetime import date, timedelta
    start, end = (start or "").strip(), (end or "").strip()
    if start or end:
        for v in (start, end):
            if v:
                try:
                    date.fromisoformat(v)
                except ValueError:
                    return None, None, "对账日期格式不对，请按「2026-10-01」这样填写"
        if start and end and start > end:
            return None, None, "开始日期不能晚于结束日期"
        return start, end, None
    today = date.today()
    if period_type == "month":
        return (today.replace(day=1).isoformat(),
                today.replace(day=monthrange(today.year, today.month)[1]).isoformat(), None)
    if period_type in ("3d", "7d", "14d"):
        n = int(period_type[:-1])
        return (today - timedelta(days=n - 1)).isoformat(), today.isoformat(), None
    return None, None, "对账周期请选：月结、3天、7天或14天"


def _in_bounds(d, start, end):
    d = (d or "")[:10]
    if start and d < start:
        return False
    if end and d > end:
        return False
    return True


def _system_lines(tenant_id, supplier_id, start, end):
    """该供应商在期间内、已入账的赊单（含合并过的供应商名下的单）。"""
    c = recon_store.compute_payables(tenant_id)
    rows = [r for r in c["payables"]
            if r["supplier_id"] == supplier_id and _in_bounds(r["receipt_date"], start, end)]
    rows.sort(key=lambda r: (r["receipt_date"], r["receipt_id"]))
    return rows


def _sys_line(line_id, r, status="pending"):
    return {
        "id": line_id, "side": "restaurant",
        "line_date": r["receipt_date"], "docket_no": "#%d" % r["receipt_id"],
        "amount": r["total_hkd"] if r["total_hkd"] is not None else r["total_amount"],
        "currency": r["currency"], "original_amount": r["total_amount"],
        "match_status": status, "match_status_label": _STATUS_LABEL[status],
        "matched_receipt_id": r["receipt_id"], "matched_receipt": r["receipt_id"],
        "resolution_note": "", "resolved": False,
    }


def _line_view(t, ln):
    out = dict(ln)
    out["resolved"] = bool(ln.get("resolved")) or ln.get("match_status") == "matched"
    return out


def _summary(t):
    lines = t["lines"]
    sys_lines = [x for x in lines if x["side"] == "restaurant"]
    stmt_total = round(sum(x.get("statement_amount", x["amount"]) for x in lines
                           if x["side"] == "statement" or x.get("statement_amount") is not None), 2)
    rest_only = round(sum(x["amount"] for x in lines if x["match_status"] == "restaurant_only"), 2)
    sys_total = round(sum(x["amount"] for x in sys_lines), 2)
    return {
        "has_statement": t["has_statement"],
        "statement_amount": stmt_total if t["has_statement"] else 0.0,
        "restaurant_only_amount": rest_only,
        "system_total": sys_total,
        "diff_amount": round(stmt_total - sys_total, 2) if t["has_statement"] else 0.0,
        "total_lines": len(lines),
        "matched": sum(1 for x in lines if x["match_status"] == "matched"),
        "open_issues": sum(1 for x in lines if x["match_status"] in _OPEN_STATUSES),
    }


def _norm_docket(v):
    return str(v or "").strip().lstrip("#").strip().lower()


def _match_statement(sys_rows, ext_lines):
    """对账单行与系统单据配对：先按单号，再按 日期+金额。返回行列表（不含序号）。"""
    unmatched = {r["receipt_id"]: r for r in sys_rows}
    keys = {}
    from app import db as _db
    for r in sys_rows:
        keys[str(r["receipt_id"])] = r["receipt_id"]
        try:
            row = _db.get_receipt_row(r["receipt_id"])
            sheet = _norm_docket(getattr(row, "sheet_name", ""))
            if sheet:
                keys[sheet] = r["receipt_id"]
        except Exception:
            pass
    out = []
    pending_ext = []
    for ext in ext_lines:
        amt = _money(ext["amount"])
        docket = _norm_docket(ext.get("invoice_no") or ext.get("docket_no") or ext.get("no"))
        pending_ext.append((ext, amt, docket))
    paired = {}
    for idx, (ext, amt, docket) in enumerate(pending_ext):
        rid = keys.get(docket) if docket else None
        if rid is not None and rid in unmatched:
            paired[idx] = (rid, "docket")
            del unmatched[rid]
    for idx, (ext, amt, docket) in enumerate(pending_ext):
        if idx in paired:
            continue
        d = str(ext.get("date") or "")[:10]
        if not d:
            continue
        for rid, r in list(unmatched.items()):
            sys_amt = r["total_hkd"] if r["total_hkd"] is not None else r["total_amount"]
            if r["receipt_date"][:10] == d and abs(sys_amt - amt) < 0.01:
                paired[idx] = (rid, "date_amount")
                del unmatched[rid]
                break
    sys_by_id = {r["receipt_id"]: r for r in sys_rows}
    for idx, (ext, amt, docket) in enumerate(pending_ext):
        base_ext = {"statement_docket_no": str(ext.get("invoice_no") or ext.get("docket_no") or ""),
                    "statement_date": str(ext.get("date") or ""), "statement_amount": amt,
                    "statement_notes": str(ext.get("notes") or "")}
        if idx in paired:
            rid, how = paired[idx]
            r = sys_by_id[rid]
            sys_amt = r["total_hkd"] if r["total_hkd"] is not None else r["total_amount"]
            ln = _sys_line(0, r, "matched" if abs(sys_amt - amt) < 0.01 else "amount_diff")
            ln.update(base_ext)
            ln["matched_by"] = how
            if ln["match_status"] == "amount_diff":
                ln["resolution_note"] = "对账单金额 $%.2f，我方单据 $%.2f，相差 $%.2f" % (
                    amt, sys_amt, round(amt - sys_amt, 2))
            out.append(ln)
        else:
            out.append({
                "id": 0, "side": "statement", "line_date": base_ext["statement_date"],
                "docket_no": base_ext["statement_docket_no"], "amount": amt,
                "currency": "HKD", "original_amount": amt,
                "match_status": "vendor_only", "match_status_label": _STATUS_LABEL["vendor_only"],
                "matched_receipt_id": None, "matched_receipt": None,
                "resolution_note": "", "resolved": False, **base_ext})
    for rid, r in unmatched.items():
        out.append(_sys_line(0, r, "restaurant_only"))
    for n, ln in enumerate(out, 1):
        ln["id"] = n
    return out


def _task_response_lines(t):
    return [_line_view(t, x) for x in t["lines"]]


def _locked(t):
    if t["status"] == "confirmed":
        return _err(409, "RECON_CONFIRMED", "这个对账已经确认完成，不能再改。需要调整请新建一个对账")
    return None


class ReconCreateBody(BaseModel):
    supplier_id: int
    period_type: str = "month"
    start: str = ""
    end: str = ""


@router.get("/api/reconciliation")
def list_reconciliation(request: Request, supplier_id: int = None):
    """对账任务列表（只返回本租户；supplier_id 非空时只返回该供应商的任务）。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    names = {s.id: s.name for s in db.list_suppliers(include_inactive=True, tenant_id=tenant_id)}
    out = []
    for t in recon_store.list_tasks(tenant_id, supplier_id):
        s = _summary(t)
        out.append({
            "id": t["id"], "supplier_id": t["supplier_id"],
            "supplier_name": names.get(t["supplier_id"], "—"),
            "period_start": t["period_start"], "period_end": t["period_end"],
            "period_type_label": _PERIOD_LABEL.get(t["period_type"], t["period_type"]),
            "status": t["status"], "has_statement": t["has_statement"],
            "summary": {"total_lines": s["total_lines"], "matched": s["matched"],
                        "open_issues": s["open_issues"],
                        "statement_amount": s["statement_amount"], "diff_amount": s["diff_amount"]},
        })
    return {"status": "success", "data": out}


@router.post("/api/reconciliation")
def create_reconciliation(body: ReconCreateBody, request: Request):
    """新建对账：取该供应商在期间内已入账的赊单。此时还没有对账单，所有行都是「待核对」。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    supplier = db.get_supplier(body.supplier_id, tenant_id=tenant_id)
    if supplier is None:
        return _err(404, "SUPPLIER_NOT_FOUND", "找不到这个供应商，请刷新页面后重新选择")
    start, end, perr = _period_bounds(body.period_type, body.start, body.end)
    if perr:
        return _err(400, "RECON_PERIOD_INVALID", perr)
    rows = _system_lines(tenant_id, supplier.id, start, end)
    lines = [_sys_line(n, r) for n, r in enumerate(rows, 1)]
    task_id = recon_store.create_task(tenant_id, supplier.id, body.period_type, start, end, lines)
    return {"status": "success", "task_id": task_id, "msg": "对账已开始"}


@router.get("/api/reconciliation/{task_id}")
def get_reconciliation(task_id: str, request: Request):
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    t = recon_store.get_task(tenant_id, task_id)
    if t is None:
        return _err(404, "RECON_NOT_FOUND", "找不到这个对账任务，可能已被删除，请回到列表重新选择")
    in_lines = {x["matched_receipt_id"] for x in t["lines"]
                if x["side"] == "restaurant" and x["match_status"] == "matched"}
    return {
        "status": "success",
        "task": {"id": t["id"], "supplier_id": t["supplier_id"],
                 "period_start": t["period_start"], "period_end": t["period_end"],
                 "status": t["status"], "summary": _summary(t)},
        "lines": _task_response_lines(t),
        "restaurant_receipts": [
            {"id": r["receipt_id"], "receipt_date": r["receipt_date"],
             "total_amount": r["total_hkd"] if r["total_hkd"] is not None else r["total_amount"],
             "matched": r["receipt_id"] in in_lines}
            for r in _system_lines(tenant_id, t["supplier_id"], t["period_start"], t["period_end"])],
    }


class ResolveBody(BaseModel):
    line_id: int
    action: str
    note: str = ""
    receipt_id: Optional[int] = None


@router.post("/api/reconciliation/{task_id}/resolve")
def resolve_line(task_id: str, body: ResolveBody, request: Request):
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    t = recon_store.get_task(tenant_id, task_id)
    if t is None:
        return _err(404, "RECON_NOT_FOUND", "找不到这个对账任务，可能已被删除，请回到列表重新选择")
    if body.action not in _RESOLVE_ACTIONS:
        return _err(400, "RECON_ACTION_INVALID", "不支持这个处理方式，请选：确认匹配、核销或标记争议")
    locked = _locked(t)
    if locked:
        return locked
    line = next((x for x in t["lines"] if x["id"] == body.line_id), None)
    if line is None:
        return _err(404, "RECON_LINE_NOT_FOUND", "找不到这一行对账明细，请刷新页面后再试")
    if line["match_status"] == "matched":
        return _err(409, "RECON_LINE_MATCHED", "这一行已经和对账单对上了，不需要再处理")

    if body.action == "confirm_match":
        if line["side"] == "statement":
            if body.receipt_id is None:
                return _err(400, "RECON_RECEIPT_REQUIRED", "请选择要对上的赊单")
            target = next((x for x in t["lines"]
                           if x["side"] == "restaurant" and x["matched_receipt_id"] == body.receipt_id
                           and x["match_status"] in ("restaurant_only", "pending")), None)
            if target is None:
                return _err(400, "RECON_RECEIPT_INVALID",
                            "这张赊单不在本次对账范围内，或已经对上了其他行，请重新选择")
            target["match_status"] = "matched"
            target["match_status_label"] = _STATUS_LABEL["matched"]
            target["resolved"] = True
            target["resolution_note"] = body.note or "人工确认对上"
            target["statement_amount"] = line["amount"]
            target["statement_docket_no"] = line.get("docket_no", "")
            target["statement_date"] = line.get("line_date", "")
            recon_store.save_line(tenant_id, task_id, target)
            recon_store.delete_line(tenant_id, task_id, line["id"])
            return {"status": "success", "msg": "已处理"}
        if body.receipt_id is not None and body.receipt_id != line["matched_receipt_id"]:
            return _err(400, "RECON_RECEIPT_INVALID", "这一行对应的是单据 #%s，不能改成别的单据" % line["matched_receipt_id"])
        line["match_status"] = "matched"
    elif body.action == "write_off":
        line["match_status"] = "write_off"
    else:
        line["match_status"] = "disputed"
    line["match_status_label"] = _STATUS_LABEL[line["match_status"]]
    line["resolved"] = True
    line["resolution_note"] = body.note or {"confirm_match": "人工确认对上", "write_off": "核销",
                                            "dispute": "标记争议"}[body.action]
    recon_store.save_line(tenant_id, task_id, line)
    return {"status": "success", "msg": "已处理"}


@router.post("/api/reconciliation/{task_id}/confirm")
def confirm_reconciliation(task_id: str, request: Request):
    """确认对账完成：之后这个对账不能再改。"""
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    t = recon_store.get_task(tenant_id, task_id)
    if t is None:
        return _err(404, "RECON_NOT_FOUND", "找不到这个对账任务，可能已被删除，请回到列表重新选择")
    locked = _locked(t)
    if locked:
        return locked
    recon_store.set_task_status(tenant_id, task_id, "confirmed")
    return {"status": "success", "msg": "对账已确认完成"}


class ImportStatementBody(BaseModel):
    supplier_id: int
    statement_period: str = ""
    external_lines: list[dict] = []  # [{"date": "2026-08-01", "invoice_no": "INV-001", "amount": 500.0, "notes": ""}]
    task_id: Optional[str] = None


def _parse_statement_period(raw, ext_lines):
    from calendar import monthrange
    import re
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{2})", raw)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            return None, None, "对账期间的月份不对，请填「2026-10」这样的格式"
        return "%04d-%02d-01" % (y, mo), "%04d-%02d-%02d" % (y, mo, monthrange(y, mo)[1]), None
    parts = re.split(r"\s*(?:~|～|至|到|/)\s*", raw) if raw else []
    if len(parts) == 2 and all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", p) for p in parts):
        return parts[0], parts[1], None
    if raw:
        return None, None, "对账期间请填「2026-10」，或「2026-10-01~2026-10-31」"
    dates = sorted(str(x.get("date") or "")[:10] for x in ext_lines if x.get("date"))
    if dates:
        return dates[0], dates[-1], None
    return "", "", None


@router.post("/api/reconciliation/import-statement")
def import_statement(body: ImportStatementBody, request: Request):
    """导入供应商对账单并和系统单据配对。

    配对顺序：单号，再 日期+金额。系统一侧只取该供应商在期间内已入账的赊单（未入账的不算）。
    返回的汇总里：对账单合计、系统单据合计、相差、我方多出来的金额分开列。
    """
    require_role("owner")(request)
    tenant_id = _tenant_id(request)
    supplier = db.get_supplier(body.supplier_id, tenant_id=tenant_id)
    if supplier is None:
        return _err(404, "SUPPLIER_NOT_FOUND", "找不到这个供应商，请刷新页面后重新选择")
    import math
    ext_lines = []
    for n, ext in enumerate(body.external_lines, 1):
        try:
            amt = float(ext.get("amount"))
            assert math.isfinite(amt)
        except Exception:
            return _err(400, "STATEMENT_LINE_INVALID", "对账单第 %d 行的金额不对，请检查后重新导入" % n)
        ext_lines.append(dict(ext, amount=amt))
    if not ext_lines:
        return _err(400, "STATEMENT_EMPTY", "对账单里没有任何明细行，请检查后重新导入")
    start, end, perr = _parse_statement_period(body.statement_period, ext_lines)
    if perr:
        return _err(400, "RECON_PERIOD_INVALID", perr)

    task_id = body.task_id
    with recon_store.write_lock:
        if task_id:
            t = recon_store.get_task(tenant_id, task_id)
            if t is None or t["supplier_id"] != supplier.id:
                return _err(404, "RECON_NOT_FOUND", "找不到这个对账任务，可能已被删除，请回到列表重新选择")
            locked = _locked(t)
            if locked:
                return locked
            if t["has_statement"]:
                return _err(409, "STATEMENT_ALREADY_IMPORTED", "这个对账已经导入过对账单了，请新建一个对账再导入")
            start, end = t["period_start"], t["period_end"]
            for ln in t["lines"]:
                recon_store.delete_line(tenant_id, task_id, ln["id"])
        rows = _system_lines(tenant_id, supplier.id, start, end)
        lines = _match_statement(rows, ext_lines)
        stmt_total = round(sum(_money(x["amount"]) for x in ext_lines), 2)
        if task_id:
            recon_store.add_lines(tenant_id, task_id, lines)
            recon_store.update_task_statement(tenant_id, task_id, stmt_total)
        else:
            task_id = recon_store.create_task(tenant_id, supplier.id, "month", start, end, lines,
                                              has_statement=True, statement_total=stmt_total)
    t = recon_store.get_task(tenant_id, task_id)
    s = _summary(t)
    issues = [x for x in _task_response_lines(t) if x["match_status"] in _OPEN_STATUSES]
    return {
        "status": "success",
        "task_id": task_id,
        "summary": {
            "external_statement_total": s["statement_amount"],
            "statement_amount": s["statement_amount"],
            "system_receipts_total": s["system_total"],
            "restaurant_only_amount": s["restaurant_only_amount"],
            "diff_amount": s["diff_amount"],
            "discrepancy_count": len(issues),
        },
        "discrepancies": issues,
    }
