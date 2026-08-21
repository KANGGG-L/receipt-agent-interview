# -*- coding: utf-8 -*-
from __future__ import annotations
"""契约门禁：Pydantic 校验 AI 输出，拒绝 schema 外字段与非法类型。

对应完整版 S3 契约门禁。规则：
- 字段不在 schema 内（extra="forbid"）→ 拒绝
- 金额必须数值；日期必须 YYYY-MM-DD；枚举白名单（doc_form）
- 校验失败返回结构化错误，打回重试，绝不静默放行
"""

import re
from typing import Any, Optional, Tuple

from pydantic import ValidationError

from app.models import ReceiptData, DocForm

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

ALLOWED_DOC_FORMS = {f.value for f in DocForm}


def validate_contract(payload: dict) -> Tuple[Optional[ReceiptData], Optional[str]]:
    """契约门禁入口。

    返回 (data, error)：合法 → (ReceiptData, None)；非法 → (None, error_msg)。
    """
    if not isinstance(payload, dict):
        return None, "AI 输出不是 JSON 对象"
    try:
        data = ReceiptData(**payload)
    except ValidationError as e:
        return None, _format_validation_error(e)

    # 业务级校验（Pydantic 之外再守一道）
    if not data.items:
        return None, "明细为空"
    if _is_nan_or_inf(data.total):
        return None, "总额非法（NaN/Infinity）"
    if data.total == 0:
        return None, "总额不能为0"
    if data.total < 0 and data.doc_form not in (DocForm.CREDIT, DocForm.CORRECTION):
        return None, f"非退款/更正单据总额不能为负数: {data.total}"
    for i, it in enumerate(data.items):
        if _is_nan_or_inf(it.qty) or _is_nan_or_inf(it.unit_price) or _is_nan_or_inf(it.amount):
            return None, f"第{i+1}行数值非法（NaN/Infinity）"
        if it.qty <= 0 or it.unit_price < 0:
            return None, f"第{i+1}行数量/单价非法"
    if not _DATE_RE.match(data.date or ""):
        return None, f"日期格式非法: {data.date}"
    if data.doc_form.value not in ALLOWED_DOC_FORMS:
        return None, f"非法单据形态: {data.doc_form}"
    if not data.vendor.strip():
        return None, "供应商为空"
    return data, None


def _is_nan_or_inf(value: float) -> bool:
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return True
    return fv != fv or fv in (float("inf"), float("-inf"))


def _format_validation_error(e: ValidationError) -> str:
    errors = []
    for err in e.errors():
        loc = ".".join(str(x) for x in err["loc"])
        errors.append(f"{loc}: {err['msg']}")
    return "契约校验失败: " + "; ".join(errors)


def sanitize_nan(value: Any, default: float = 0.0) -> float:
    """AI 可能输出 NaN/Infinity → 拒绝（完整版 A1：库存入参拒绝 Infinity）。"""
    if value is None:
        return default
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return default
    if fv != fv or fv in (float("inf"), float("-inf")):
        return default
    return fv
