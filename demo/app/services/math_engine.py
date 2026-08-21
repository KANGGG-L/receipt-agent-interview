# -*- coding: utf-8 -*-
from __future__ import annotations
"""算术门禁：AI 的金额必须过确定性代码校验（零 token，可审计）。

对应完整版 S4 算术门禁 + D25「校验+建议、不静默改写」。
规则：
- 每行 amount ≈ qty × unit_price
- Σ items.amount ≈ total
- 校验失败 → 返回差异清单，交给人工/重试裁决，绝不静默改数值
"""

from app.models import ReceiptData


def validate_and_report(data: ReceiptData) -> list[str]:
    """逐行与总额校验，返回差异描述列表（空 = 全部通过）。"""
    problems = []
    active_items_sum = 0.0

    for i, it in enumerate(data.items, start=1):
        if getattr(it, "is_void", False):
            # 划线作废/拒收行，不计入有效实付小计
            continue

        effective_qty = it.actual_qty if (getattr(it, "actual_qty", None) is not None and it.actual_qty > 0) else it.qty
        expected = round(effective_qty * it.unit_price, 2)
        effective_amount = it.amount if getattr(it, "actual_qty", None) is None else expected

        if abs(expected - effective_amount) > 0.01:
            problems.append(
                f"第{i}行 {it.name}: 数量{effective_qty}×单价{it.unit_price}="
                f"{expected}，但小计={effective_amount}（差{round(expected-effective_amount, 2)}）"
            )
        active_items_sum += effective_amount

    items_sum = round(active_items_sum, 2)
    discount = round(float(getattr(data, "discount_amount", 0.0) or 0.0), 2)
    deposit = round(float(getattr(data, "deposit_amount", 0.0) or 0.0), 2)
    delivery = round(float(getattr(data, "delivery_fee", 0.0) or 0.0), 2)
    service_fee = round(float(getattr(data, "service_fee", 0.0) or 0.0), 2)
    tax = round(float(getattr(data, "tax_amount", 0.0) or 0.0), 2)
    rounding = round(float(getattr(data, "rounding_adjustment", 0.0) or 0.0), 2)

    expected_total = round(items_sum - discount - rounding + deposit + delivery + service_fee + tax, 2)
    if abs(expected_total - data.total) > 0.01:
        detail_msg = f"明细合计={items_sum}"
        if discount > 0:
            detail_msg += f" - 折扣={discount}"
        if rounding > 0:
            detail_msg += f" - 抹零={rounding}"
        if deposit > 0:
            detail_msg += f" + 押金={deposit}"
        if delivery > 0:
            detail_msg += f" + 运费={delivery}"
        if service_fee > 0:
            detail_msg += f" + 服务费={service_fee}"
        if tax > 0:
            detail_msg += f" + 税额={tax}"
        problems.append(
            f"{detail_msg} 预期总额={expected_total}，但总额={data.total}（差{round(expected_total-data.total, 2)}）"
        )
    return problems


def audit_trail(data: ReceiptData) -> dict:
    """生成可审计的算术履历（进 Receipt.raw 审计）。"""
    return {
        "items_sum": round(sum(i.amount for i in data.items if not getattr(i, "is_void", False)), 2),
        "declared_total": data.total,
        "problems": validate_and_report(data),
        "engine": "code_engine",       # 确定性代码，非 LLM
        "tokens": 0,
    }
