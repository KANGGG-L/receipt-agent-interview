# -*- coding: utf-8 -*-
"""S03–S08 计费客户/运营端点。

权限口径（D-02 / I03）：计费权限只认 identity_store.capabilities_for() 返回的
`billing:billing_owner` / `billing:billing_operator`；餐厅 owner / 平台 admin 不自动继承。
租户一律来自 auth.resolve_tenant(request)，不采信请求体声明的租户/金额。
成功页只查订单事实，客户端金额永不参与授期；套餐价格对外恒为 $***（D06 占位）。
"""

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from app import auth, identity_store
from app.billing import entitlements, inbox, lifecycle, reconcile, refunds
from app.billing.errors import BillingError
from app.billing.policy import PolicyNotConfigured

router = APIRouter(tags=["billing"])

OWNER_CAP = "billing:billing_owner"
OPERATOR_CAP = "billing:billing_operator"

_PERMISSION_MSG = (
    "权限不足：计费操作需要「计费负责人」或「计费操作员」权限。"
    "餐厅老板/店员权限不会自动包含计费权限，请联系平台管理员开通。")
_POLICY_MSG = (
    "退款规则还没配置好（退款比例/审批人未签发），暂时不能提交或处理退款。"
    "原支付记录与历史数据不会受影响。")


def _identity(request, need="read"):
    tenant = auth.resolve_tenant(request)
    account = auth.resolve_account(request)
    email = account.get("email")
    if not email:
        raise HTTPException(status_code=401, detail=auth.unauthenticated_detail())
    caps = identity_store.capabilities_for(email, tenant)
    billing_caps = {c for c in caps if c.startswith("billing:")}
    if need == "owner" and OWNER_CAP not in billing_caps:
        raise HTTPException(status_code=403, detail=_PERMISSION_MSG)
    if need == "operator" and not (billing_caps & {OWNER_CAP, OPERATOR_CAP}):
        raise HTTPException(status_code=403, detail=_PERMISSION_MSG)
    if need == "read" and not billing_caps:
        raise HTTPException(status_code=403, detail=_PERMISSION_MSG)
    return tenant, account, billing_caps


def _error(exc):
    if isinstance(exc, PolicyNotConfigured):
        return JSONResponse(status_code=403, content={
            "status": "error", "code": "POLICY_NOT_CONFIGURED", "msg": _POLICY_MSG,
            "missing": list(exc.missing)})
    if isinstance(exc, BillingError):
        return JSONResponse(status_code=exc.status_code, content=exc.payload())
    raise exc


def _public_order(order):
    attempt = order.get("latest_attempt") or {}
    return {
        "order_no": order.get("order_no"), "status": order.get("status"),
        "plan_ref": order.get("plan_ref"), "currency": order.get("currency"),
        "attempt_status": attempt.get("status"), "price_display": "$***",
        "created_at": order.get("created_at"), "paid_at": order.get("paid_at"),
        # 金额只留在私有证据/运营内部，不对客户展示。
    }


def _public_subscription(tenant_id):
    sub = lifecycle.get_subscription(tenant_id)
    decision = entitlements.evaluate(tenant_id)
    if not sub:
        return {"subscription": None, "entitlement": decision, "price_display": "$***"}
    period = sub.get("current_period") or {}
    return {
        "subscription": {
            "state": sub.get("state"), "plan_ref": sub.get("plan_ref"),
            "policy_ref": sub.get("policy_ref"),
            "trial_started_at": sub.get("trial_started_at"),
            "trial_ends_at": sub.get("trial_ends_at"),
            "cancel_at": sub.get("cancel_at"),
            "period_starts_at": period.get("starts_at"),
            "period_ends_at": period.get("ends_at"),
        },
        "entitlement": decision,
        "price_display": "$***",
    }


# -------------------------------------------------------------
# 试用 / 权益 / 订阅
# -------------------------------------------------------------
@router.post("/api/billing/trial/redeem")
def redeem_trial(request: Request, payload: dict = Body(...)):
    tenant, account, _caps = _identity(request, "owner")
    try:
        result = lifecycle.redeem_trial_coupon(
            tenant, payload.get("coupon_code"), actor=account.get("email"))
        return {"status": "success", "msg": "试用已开通，可以开始新的 AI 识别了。",
                "data": result}
    except Exception as exc:  # noqa: BLE001 域错误统一信封
        return _error(exc)


@router.get("/api/billing/entitlement")
def get_entitlement(request: Request):
    try:
        tenant, _account, _caps = _identity(request, "read")
        return entitlements.evaluate(tenant)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/api/billing/subscription")
def get_subscription_view(request: Request):
    try:
        tenant, _account, _caps = _identity(request, "read")
        return _public_subscription(tenant)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# -------------------------------------------------------------
# 订单 / checkout / 人工收款
# -------------------------------------------------------------
@router.post("/api/billing/checkout")
def create_checkout(request: Request, payload: dict = Body(...)):
    """创建订单：只接受 plan_ref/request_key；payload 内的 amount 被忽略。"""
    try:
        tenant, account, _caps = _identity(request, "owner")
        result = lifecycle.create_checkout(
            tenant, payload.get("plan_ref"), payload.get("request_key"),
            actor=account.get("email"))
        return {"status": "success", "msg": "订单已创建，请按订单信息完成付款。",
                "data": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.get("/api/billing/orders/{order_no}")
def get_order(request: Request, order_no: str):
    """成功页只查订单事实：不采信任何客户端金额/状态参数。"""
    try:
        tenant, _account, _caps = _identity(request, "read")
        order = lifecycle.get_order(order_no, tenant)
        if not order:
            return JSONResponse(status_code=404, content={
                "status": "error", "code": "ORDER_NOT_FOUND",
                "msg": "找不到这笔订单，请刷新页面后再试。"})
        return {"status": "success", "data": _public_order(order)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/billing/orders/{order_no}/manual-payment")
def register_manual_payment(request: Request, order_no: str, payload: dict = Body(...)):
    """人工登记收款（D06 无网关）：金额/币种必须与订单一致，重复提交幂等。"""
    try:
        tenant, account, _caps = _identity(request, "operator")
        result = lifecycle.register_manual_payment(
            tenant, order_no, payload.get("amount_minor"), payload.get("currency"),
            payload.get("request_key"), invoice_no=payload.get("invoice_no", ""),
            actor=account.get("email"), provider="manual")
        return {"status": "success", "msg": "收款已登记，订阅周期已按订单生效。",
                "data": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/billing/subscription/cancel")
def cancel_subscription(request: Request, payload: dict = Body(default={})):
    try:
        tenant, account, _caps = _identity(request, "owner")
        result = lifecycle.cancel_subscription(
            tenant, actor=account.get("email"),
            request_key=(payload or {}).get("request_key", ""))
        return {"status": "success", "msg": "订阅已按规则取消。", "data": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# -------------------------------------------------------------
# 退款（S08）
# -------------------------------------------------------------
@router.post("/api/billing/refunds")
def request_refund(request: Request, payload: dict = Body(...)):
    try:
        tenant, account, caps = _identity(request, "read")
        result = refunds.request_refund(
            tenant, payload.get("order_no"), payload.get("amount_minor"),
            payload.get("request_key"), requested_by=account.get("email"),
            caps=caps, reason=payload.get("reason", ""))
        return {"status": "success", "msg": "退款申请已提交，处理完成前不会修改权益。",
                "data": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


_REFUND_ERROR_MSG = {
    "PROVIDER_RESULT_UNKNOWN": "退款渠道结果暂未确认，系统会继续查询，不会重复提交退款。",
    "ORDER_NOT_PAID": "这笔订单还没确认收款，不能退款。",
}


def _public_refund(row):
    """退款公共视图：不泄漏 request_hash / provider 流水 / 原始金额。"""
    error_code = row.get("error_code") or None
    return {
        "id": row.get("id"), "state": row.get("state"),
        "amount_display": "$***",
        "created_at": row.get("created_at"), "processed_at": row.get("processed_at"),
        "error_code": error_code,
        "error_msg": _REFUND_ERROR_MSG.get(error_code) if error_code else None,
    }


@router.get("/api/billing/refunds/{refund_id}")
def get_refund(request: Request, refund_id: int):
    try:
        tenant, _account, _caps = _identity(request, "read")
        row = refunds.get_refund(tenant, refund_id)
        if not row:
            return JSONResponse(status_code=404, content={
                "status": "error", "code": "REFUND_NOT_FOUND",
                "msg": "找不到这笔退款申请。"})
        return {"status": "success", "data": _public_refund(row)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/billing/refunds/{refund_id}/approve")
def approve_refund(request: Request, refund_id: int):
    try:
        tenant, account, caps = _identity(request, "operator")
        result = refunds.approve_refund(
            tenant, refund_id, caps=caps, actor=account.get("email"))
        return {"status": "success", "msg": "退款已提交渠道处理。", "data": result}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/billing/refunds/{refund_id}/cancel")
def cancel_refund(request: Request, refund_id: int):
    try:
        tenant, _account, caps = _identity(request, "owner")
        return {"status": "success", "data": refunds.cancel_refund(
            tenant, refund_id, caps=caps)}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


# -------------------------------------------------------------
# 回调 / 对账
# -------------------------------------------------------------
@router.post("/api/billing/webhook/{provider}")
async def billing_webhook(provider: str, request: Request):
    """渠道回调：匿名可用，靠原 body HMAC + 时间窗 + merchant/env 绑定验证。"""
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    try:
        result = inbox.receive(provider, raw, headers)
        inbox_id = result.get("inbox_id")
        if inbox_id:
            normalized = inbox.normalize(inbox_ids=[inbox_id])
            failed = [n for n in normalized if n.get("status") != "processed"]
            if failed:
                # Inbox 已持久化但事实归约失败：不能 ack 成功（否则事实可能永久丢），
                # 返回 503 让渠道重投；重投时归一逻辑会再次尝试同一 pending 事件。
                return JSONResponse(status_code=503, content={
                    "status": "error", "code": "WEBHOOK_RETRY_REQUIRED",
                    "retry": True,
                    "msg": "支付回调已收到，但入账暂时没完成。请稍后重投，不会重复扣款。"})
        return result
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/api/billing/reconcile")
def run_reconcile(request: Request, payload: dict = Body(default={})):
    try:
        tenant, _account, _caps = _identity(request, "operator")
        actions = reconcile.reconcile_missing(tenant_id=tenant)
        return {"status": "success", "actions": actions}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
