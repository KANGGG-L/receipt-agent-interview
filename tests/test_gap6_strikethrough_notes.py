# -*- coding: utf-8 -*-
"""
Gap 6 专项测试：验货划线作废商品判定、手写拒收/短装注记抽取与算术守恒回归测试集。
"""

import pytest
from ai_registry.tools.item_sanitizer.v1_3_0_notes_clean import ItemSanitizerTool
from app.models import ReceiptData, ReceiptItem, DocForm
from app.services.math_engine import validate_and_report


@pytest.fixture
def sanitizer():
    return ItemSanitizerTool()


def test_strikethrough_and_notes_extraction(sanitizer):
    """测试划线作废商品与独立手写拒收注记识别。"""
    raw_items = [
        {"name": "特级有机菜心", "qty": 10, "unit": "斤", "unit_price": 8.50, "amount": 85.00},
        {"name": "~~冰鲜黄花鱼 5条~~", "qty": 5, "unit": "条", "unit_price": 30.00, "amount": 150.00, "is_void": True},
        {"name": "鲜活草虾", "qty": 4, "unit": "斤", "unit_price": 45.00, "amount": 180.00},
        {"name": "退回1箱番茄 (坏果过多)", "qty": 1, "unit": "箱", "unit_price": 0.00, "amount": 0.00},
        {"name": "拒收2条变质鱼", "qty": 2, "unit": "条", "unit_price": 0.00, "amount": 0.00}
    ]

    clean_items, fees, adj_notes, dropped = sanitizer.filter_and_extract_all(raw_items)

    clean_names = [it["name"] for it in clean_items]
    assert clean_names == ["特级有机菜心", "鲜活草虾"], f"明细未正确剔除作废与注记: {clean_names}"
    assert any("黄花鱼" in n for n in adj_notes), "未记录划线拒收黄花鱼"
    assert any("退回1箱番茄" in n for n in adj_notes), "未记录退回番茄注记"
    assert any("拒收2条变质鱼" in n for n in adj_notes), "未记录拒收注记"


def test_math_engine_with_void_strikethrough():
    """测试算术门禁自动剔除 is_void 作废行，实收金额完全守恒。"""
    items = [
        ReceiptItem(name="特级有机菜心", qty=10, unit="斤", unit_price=8.50, amount=85.00),
        ReceiptItem(name="冰鲜黄花鱼", qty=5, unit="条", unit_price=30.00, amount=150.00, is_void=True),
        ReceiptItem(name="鲜活草虾", qty=4, unit="斤", unit_price=45.00, amount=180.00)
    ]

    # 实付总额为 85 + 180 = 265 (黄花鱼作废)
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="香港源兴批发",
        date="2026-08-21",
        items=items,
        total=265.00,
        adjustment_notes=["划线拒收: 冰鲜黄花鱼 (5条)"],
        payment_marked=False,
        confidence=0.98
    )

    problems = validate_and_report(data)
    assert len(problems) == 0, f"算术门禁误报作废行差错: {problems}"


def test_math_engine_detects_mismatch_with_void():
    """测试当总额未扣减作废行时的真实差错检测。"""
    items = [
        ReceiptItem(name="特级有机菜心", qty=10, unit="斤", unit_price=8.50, amount=85.00),
        ReceiptItem(name="冰鲜黄花鱼", qty=5, unit="条", unit_price=30.00, amount=150.00, is_void=True),
        ReceiptItem(name="鲜活草虾", qty=4, unit="斤", unit_price=45.00, amount=180.00)
    ]

    # 总额填成了包含作废行的 415.00 (实际应为 265.00)
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="香港源兴批发",
        date="2026-08-21",
        items=items,
        total=415.00,
        adjustment_notes=["划线拒收: 冰鲜黄花鱼 (5条)"],
        payment_marked=False,
        confidence=0.98
    )

    problems = validate_and_report(data)
    assert len(problems) == 1, "未成功检测出包含作废行时的总额不守恒"
    assert "预期总额=265.0" in problems[0]
