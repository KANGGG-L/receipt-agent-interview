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
    for i, it in enumerate(data.items, start=1):
        expected = round(it.qty * it.unit_price, 2)
        if abs(expected - it.amount) > 0.01:
            problems.append(
                f"第{i}行 {it.name}: 数量{it.qty}×单价{it.unit_price}="
                f"{expected}，但小计={it.amount}（差{round(expected-it.amount, 2)}）"
            )
    items_sum = round(sum(i.amount for i in data.items), 2)
    if abs(items_sum - data.total) > 0.01:
        problems.append(
            f"明细合计={items_sum}，但总额={data.total}（差{round(items_sum-data.total, 2)}）"
        )
    return problems


def audit_trail(data: ReceiptData) -> dict:
    """生成可审计的算术履历（进 Receipt.raw 审计）。"""
    return {
        "items_sum": round(sum(i.amount for i in data.items), 2),
        "declared_total": data.total,
        "problems": validate_and_report(data),
        "engine": "code_engine",       # 确定性代码，非 LLM
        "tokens": 0,
    }
