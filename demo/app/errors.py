# -*- coding: utf-8 -*-
"""统一错误信封：{status:"error", code, msg, detail?}。

所有错误响应（HTTPException、请求校验失败、未捕获异常）都经本模块输出同一结构：
- code：给程序判断（大写下划线），不展示给店员；
- msg：给店员看的人话（发生了什么 + 下一步怎么做），不含接口路径、状态码、英文术语；
- detail：兼容旧前端读取 body.detail 的写法。字符串错误时等于 msg；校验错误时是
  [{loc, field, msg, type}] 列表（前端按 loc 末段定位字段）。
成功响应结构完全不受影响。
"""

import logging
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("app.errors")

_STATUS_CODE = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    410: "GONE",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "UNPROCESSABLE",
    429: "TOO_MANY_REQUESTS",
}

_STATUS_MSG = {
    400: "请求内容有问题，请检查填写的内容后再试一次。",
    401: "请先登录。你还没有登录，或登录已经过期，请重新登录后再操作。",
    403: "你的账号没有权限做这个操作，请换有权限的账号，或联系老板处理。",
    404: "没有找到你要的内容，可能已被删除。请刷新页面后再试。",
    405: "这个操作不被支持，请刷新页面后再试。",
    409: "这条记录刚刚被别人改过，请刷新页面看最新内容后再操作。",
    413: "上传的文件太大了，请压缩或重新拍小一点的照片再传。",
    415: "这种文件格式不支持，请上传照片（JPG、PNG）或 PDF。",
    422: "填写的内容有问题，请检查后再提交。",
    429: "操作太频繁了，请稍等一会儿再试。",
}

INTERNAL_MSG = "系统出了点问题，这次操作没有完成，数据没有被改动。请稍后再试，如果一直这样请联系管理员。"

FIELD_LABELS = {
    "supplier_name": "供应商名称", "supplier": "供应商", "date": "开单日期",
    "receipt_date": "开单日期", "total_amount": "单据总额", "items": "明细",
    "quantity": "数量", "unit_price": "单价", "amount": "金额", "unit": "单位",
    "name": "名称", "settlement_type": "结算方式", "version": "单据版本",
    "email": "邮箱", "password": "密码", "reason": "原因", "currency": "币种",
    "department_id": "部门", "receipt_id": "单据", "sku_id": "货品",
    "service_fee": "服务费", "tax_amount": "税额", "discount_amount": "折扣",
    "delivery_fee": "运费", "deposit_amount": "押金", "rounding_adjustment": "抹零",
    "paid_at": "付款日期", "method": "付款方式", "note": "备注", "month": "月份",
    "limit": "每页条数", "offset": "起始位置", "status": "状态", "q": "搜索词",
    "factor": "换算数值", "from_unit": "原单位", "to_unit": "目标单位",
    "kind": "类型", "role": "角色", "id": "编号",
}

_TECH_REWRITES = (
    (re.compile(r"Bearer\s*令牌|Bearer\s*token", re.I), "登录凭证"),
    (re.compile(r"X-Role\s*角色头|X-Role"), "角色信息"),
    (re.compile(r"magic bytes", re.I), "文件内容"),
    (re.compile(r"status\s*=\s*'([a-z_]+)'", re.I), "该状态"),
)


def humanize_text(text: str) -> str:
    """兜底：把残留的技术词替换成人话（正面修法是在报错源头直接写人话）。"""
    out = str(text or "")
    for pat, repl in _TECH_REWRITES:
        out = pat.sub(repl, out)
    return out


def envelope(code: str, msg: str, detail=None, **extra) -> dict:
    body = {"status": "error", "code": code, "msg": msg}
    body["detail"] = msg if detail is None else detail
    body.update(extra)
    return body


def error_response(status_code: int, code: str, msg: str, detail=None, **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=envelope(code, msg, detail, **extra))


def _field_label(loc) -> str:
    parts = [p for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    if not parts:
        return ""
    last = parts[-1]
    row = None
    for p in parts[:-1]:
        if isinstance(p, int):
            row = p + 1
    if isinstance(last, int):
        row, last = last + 1, (parts[-2] if len(parts) > 1 else "")
    label = FIELD_LABELS.get(str(last), "")
    if not label:
        return ""
    return f"第 {row} 行的「{label}」" if row else f"「{label}」"


def _validation_item_msg(err: dict) -> str:
    label = _field_label(err.get("loc") or ())
    typ = str(err.get("type") or "")
    ctx = err.get("ctx") or {}
    who = label or "有一项内容"
    if typ in ("missing", "value_error.missing"):
        return f"{who}还没有填写，请补上。"
    if "int_parsing" in typ or "int_from_float" in typ or typ == "int_type":
        return f"{who}需要填整数，请检查后重新填写。"
    if "float_parsing" in typ or typ in ("float_type", "decimal_parsing"):
        return f"{who}需要填数字，请检查后重新填写。"
    if typ == "greater_than":
        return f"{who}必须大于 {ctx.get('gt', 0)}，请检查后重新填写。"
    if typ == "greater_than_equal":
        return f"{who}不能小于 {ctx.get('ge', 0)}，请检查后重新填写。"
    if typ == "less_than":
        return f"{who}必须小于 {ctx.get('lt', '')}，请检查后重新填写。"
    if typ == "less_than_equal":
        return f"{who}不能大于 {ctx.get('le', '')}，请检查后重新填写。"
    if typ in ("string_type", "string_too_short", "string_too_long"):
        return f"{who}的内容不对，请检查后重新填写。"
    if typ in ("json_invalid", "model_attributes_type", "dict_type"):
        return "提交的内容格式不对，请刷新页面后再试一次。"
    if typ.startswith("bool"):
        return f"{who}只能选「是」或「否」，请检查后重新选择。"
    return f"{who}填写的内容不对，请检查后重新填写。"


def validation_envelope(exc: RequestValidationError) -> dict:
    items = []
    msgs = []
    for err in exc.errors():
        loc = [x for x in (err.get("loc") or ())]
        human = _validation_item_msg(err)
        last = next((str(p) for p in reversed(loc) if not isinstance(p, int)), "")
        items.append({"loc": loc, "field": last, "msg": human,
                      "type": str(err.get("type") or "")})
        if human not in msgs:
            msgs.append(human)
    msg = msgs[0] if len(msgs) == 1 else "；".join(msgs[:3]) if msgs else _STATUS_MSG[422]
    return envelope("VALIDATION_ERROR", msg, detail=items)


def _from_http_exception(exc: StarletteHTTPException) -> JSONResponse:
    status = exc.status_code
    detail = exc.detail
    code = _STATUS_CODE.get(status, "ERROR" if status < 500 else "INTERNAL_ERROR")
    if isinstance(detail, dict):
        msg = str(detail.get("msg") or detail.get("message") or _STATUS_MSG.get(status, INTERNAL_MSG))
        code = str(detail.get("code") or code)
    elif isinstance(detail, str) and detail.strip() and detail.strip() not in (
            "Not Found", "Method Not Allowed", "Forbidden", "Unauthorized", "Bad Request"):
        msg = humanize_text(detail)
    else:
        msg = _STATUS_MSG.get(status, INTERNAL_MSG if status >= 500 else "操作没有成功，请稍后再试。")
    headers = getattr(exc, "headers", None)
    return JSONResponse(status_code=status, content=envelope(code, msg), headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        return _from_http_exception(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=validation_envelope(exc))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content=envelope("INTERNAL_ERROR", INTERNAL_MSG))


_JOB_NOT_CONFIGURED_HINTS = ("安全阻断", "Base URL", "base_url", "API Key", "api_key", "apikey",
                            "未配置", "URL 不能为空", "认证", "401", "Unauthorized")
_JOB_TIMEOUT_HINTS = ("timeout", "timed out", "超时")
_TECH_RUN = re.compile(r"[A-Za-z_]{5,}|[{}\[\]]|Traceback|Error|Exception", re.I)

JOB_NOT_CONFIGURED_MSG = "识别服务还没有开通，这张单请直接手工录入，或联系管理员开通识别服务。"
JOB_TIMEOUT_MSG = "识别花的时间太长了，这张单请重新上传一次，或直接手工录入。"
JOB_GENERIC_MSG = "这张单没能自动识别，请重新拍一张更清楚的照片，或直接手工录入。"


def human_job_error(raw) -> str:
    """识别任务失败原因 -> 店员能看懂的话。原始报错只留在日志/管理台，不给店员看。"""
    text = str(raw or "").strip()
    low = text.lower()
    if any(h.lower() in low for h in _JOB_NOT_CONFIGURED_HINTS):
        return JOB_NOT_CONFIGURED_MSG
    if any(h in low for h in _JOB_TIMEOUT_HINTS):
        return JOB_TIMEOUT_MSG
    if not text or _TECH_RUN.search(text):
        return JOB_GENERIC_MSG
    return text
