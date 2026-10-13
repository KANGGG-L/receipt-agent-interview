# -*- coding: utf-8 -*-
from __future__ import annotations
"""算术门禁：AI 的金额必须过确定性代码校验（零 token，可审计）。

对应完整版 S4 算术门禁 + D25「校验+建议、不静默改写」。
规则：
- 每行 AI 给出的 amount ≈ qty × unit_price（Decimal 四舍五入，容差 0.01）；
  有 actual_qty（实收）时行校验仍用 AI 的 amount，实收金额另算后进合计
- Σ 有效行金额 - 折扣 - 抹零 + 押金 + 运费 + 服务费 + 税额 ≈ total（rounding 字段显式参与）
- 单据把行金额取整到元/角（如 1.35×38.5=51.975 印成 52）：只作提示，不算错
- 贷项单/更正单：行金额为负时按 -(qty×unit_price) 校验；数量或单价为负也成立
- 校验失败 → 返回结构化差异清单（gate_issues），交给人工/重试裁决，绝不静默改数值
"""

from decimal import Decimal, ROUND_HALF_UP

from app.models import ReceiptData

_CENT = Decimal("0.01")
TOLERANCE = Decimal("0.01")
# 浮点输入转 Decimal 时 repr 带来的亚分误差余量，避免 0.0100000x 被判越界
_EPS = Decimal("0.000000001")
_GIFT_KEYWORDS = ["赠", "送", "free", "gift", "免费", "附送", "赠品", "贈品", "贈送"]
_NEGATIVE_FORMS = ("credit_note", "correction_note")


def _d(value) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:
        return Decimal("0")


def _q(value: Decimal) -> Decimal:
    return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _fmt(value) -> str:
    """金额文案：整数不带小数，其余最多两位且去掉尾零。"""
    v = _q(_d(value))
    text = f"{v:f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _f(value: Decimal) -> float:
    return float(_q(value))


def _form_value(data) -> str:
    form = getattr(data, "doc_form", "")
    return str(getattr(form, "value", form) or "")


def _line_ok(expected_raw: Decimal, stated: Decimal) -> bool:
    if abs(_q(expected_raw) - stated) <= TOLERANCE + _EPS:
        return True
    return abs(expected_raw - stated) <= TOLERANCE + _EPS


def _is_rounded_print(expected_raw: Decimal, stated: Decimal) -> bool:
    """行金额被取整到元（偏差 ≤0.5）或角（偏差 ≤0.05）印刷，属街市常见习惯。"""
    diff = abs(expected_raw - stated)
    if stated == stated.to_integral_value():
        return diff <= Decimal("0.5") + _EPS
    if (stated * 10) == (stated * 10).to_integral_value():
        return diff <= Decimal("0.05") + _EPS
    return False


def _issue(code, row_index, item_name, field, expected, actual, diff, hint):
    return {
        "code": code,
        "row_index": row_index,
        "item_name": item_name,
        "field": field,
        "expected": expected,
        "actual": actual,
        "diff": diff,
        "hint": hint,
    }


def evaluate(data: ReceiptData) -> dict:
    """返回 {"issues": [...], "notes": [...]}。issues 是必须交人工核对的差错；notes 只是提示。"""
    issues: list[dict] = []
    notes: list[dict] = []
    active_sum = Decimal("0")
    negative_form = _form_value(data) in _NEGATIVE_FORMS

    for i, it in enumerate(data.items, start=1):
        if getattr(it, "is_void", False):
            continue

        name = str(getattr(it, "name", "") or "")
        qty = _d(it.qty)
        price = _d(it.unit_price)
        stated = _d(it.amount)
        actual_qty = getattr(it, "actual_qty", None)

        raw = qty * price
        if negative_form and stated < 0 < raw:
            raw = -raw

        is_gift = (stated == 0) or (price == 0) or any(kw in name for kw in _GIFT_KEYWORDS)
        if is_gift and stated == 0:
            pass
        elif not _line_ok(raw, stated):
            shown = _q(raw)
            if _is_rounded_print(raw, stated):
                notes.append(_issue(
                    "LINE_ROUNDED", i, name, "amount", _f(shown), _f(stated),
                    _f(abs(shown - stated)),
                    f"第 {i} 行 {name}：单据把小计取整写成 {_fmt(stated)} 元，按数量单价是 {_fmt(shown)} 元，已按单据为准"))
            else:
                diff = abs(shown - stated)
                issues.append(_issue(
                    "LINE_AMOUNT_MISMATCH", i, name, "amount", _f(shown), _f(stated), _f(diff),
                    f"第 {i} 行 {name}：单据写 {_fmt(stated)} 元，按数量单价应是 {_fmt(shown)} 元，相差 {_fmt(diff)} 元"))

        if actual_qty is not None:
            effective = _q(_d(actual_qty) * price)
            if negative_form and stated < 0 < effective:
                effective = -effective
            if _d(actual_qty) != qty:
                notes.append(_issue(
                    "LINE_ACTUAL_QTY", i, name, "actual_qty", _f(effective), _f(stated),
                    _f(abs(effective - stated)),
                    f"第 {i} 行 {name}：实收 {_fmt(actual_qty)}，按实收算 {_fmt(effective)} 元"))
        else:
            effective = stated
        active_sum += effective

    items_sum = _q(active_sum)
    discount = _q(_d(getattr(data, "discount_amount", 0.0)))
    deposit = _q(_d(getattr(data, "deposit_amount", 0.0)))
    delivery = _q(_d(getattr(data, "delivery_fee", 0.0)))
    service_fee = _q(_d(getattr(data, "service_fee", 0.0)))
    tax = _q(_d(getattr(data, "tax_amount", 0.0)))
    rounding = _q(_d(getattr(data, "rounding_adjustment", 0.0)))

    expected_total = _q(items_sum - discount - rounding + deposit + delivery + service_fee + tax)
    total = _q(_d(data.total))
    if abs(expected_total - total) > TOLERANCE:
        parts = [f"各行加起来 {_fmt(items_sum)} 元"]
        if discount > 0:
            parts.append(f"减折扣 {_fmt(discount)} 元")
        if rounding > 0:
            parts.append(f"减抹零 {_fmt(rounding)} 元")
        if deposit > 0:
            parts.append(f"加押金 {_fmt(deposit)} 元")
        if delivery > 0:
            parts.append(f"加运费 {_fmt(delivery)} 元")
        if service_fee > 0:
            parts.append(f"加服务费 {_fmt(service_fee)} 元")
        if tax > 0:
            parts.append(f"加税 {_fmt(tax)} 元")
        diff = abs(expected_total - total)
        issues.append(_issue(
            "TOTAL_MISMATCH", None, "", "total", _f(expected_total), _f(total), _f(diff),
            "总额对不上：" + "，".join(parts) + f"，应付 {_fmt(expected_total)} 元，"
            f"但单据总额写 {_fmt(total)} 元，相差 {_fmt(diff)} 元"))
    return {"issues": issues, "notes": notes}


def validate_and_issues(data: ReceiptData) -> list[dict]:
    """结构化门禁结果：[{code,row_index,item_name,field,expected,actual,diff,hint}]。"""
    return evaluate(data)["issues"]


def validate_and_report(data: ReceiptData) -> list[str]:
    """逐行与总额校验，返回人话差异描述列表（空 = 全部通过）。"""
    return [iss["hint"] for iss in evaluate(data)["issues"]]


def audit_trail(data: ReceiptData) -> dict:
    """生成可审计的算术履历（进 Receipt.raw 审计）。"""
    result = evaluate(data)
    return {
        "items_sum": round(sum(i.amount for i in data.items if not getattr(i, "is_void", False)), 2),
        "declared_total": data.total,
        "problems": [iss["hint"] for iss in result["issues"]],
        "gate_issues": result["issues"],
        "notes": result["notes"],
        "engine": "code_engine",       # 确定性代码，非 LLM
        "tokens": 0,
    }
