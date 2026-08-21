# -*- coding: utf-8 -*-
"""
P0-2 修复专项测试：审计日志 audit_trail 正确跳过划线作废 is_void=True 明细行。
"""

import pytest
from app.models import ReceiptData, ReceiptItem, DocForm
from app.services.math_engine import validate_and_report, audit_trail


def test_audit_trail_skips_void_items():
    # 模拟一张包含 1 行正常商品（85.00）与 1 行作废商品（150.00）的单据，总额为 85.00
    items = [
        ReceiptItem(name="特级有机菜心", qty=10.0, unit="斤", unit_price=8.5, amount=85.0, is_void=False),
        ReceiptItem(name="黄花鱼", qty=3.0, unit="条", unit_price=50.0, amount=150.0, is_void=True),
    ]
    data = ReceiptData(
        doc_form=DocForm.NCR_HAND,
        vendor="老记蔬菜",
        date="2026-08-21",
        items=items,
        total=85.0,
        payment_marked=False,
        confidence=0.9
    )

    # 1. 验证门禁校验通过（无报错）
    problems = validate_and_report(data)
    assert problems == [], f"算术门禁错误拦截作废单据: {problems}"

    # 2. 验证审计日志中的 items_sum 为 85.0（准确跳过作废的 150.0）
    trail = audit_trail(data)
    assert trail["items_sum"] == 85.0, f"audit_trail items_sum 未跳过作废行: {trail['items_sum']} != 85.0"
    assert trail["declared_total"] == 85.0
    assert trail["problems"] == []
