# -*- coding: utf-8 -*-
"""
Gap 3 专项测试：整单折让/折扣、胶筐押金、运费服务费结构化解耦与升级后算术门禁回归测试集。
"""

import pytest
from ai_registry.tools.item_sanitizer.v1_2_0_fee_clean import ItemSanitizerTool
from demo.app.models import ReceiptData, ReceiptItem, DocForm
from demo.app.services import math_engine


@pytest.fixture
def sanitizer():
    return ItemSanitizerTool()


def test_fee_and_discount_extraction(sanitizer):
    """测试折让、押金、运费等非实物费用被准确识别并剥离至顶层结构。"""
    dirty_items = [
        {"name": "特级有机菜心", "qty": 10, "unit_price": 8.5, "amount": 85.0},
        {"name": "鲜活草虾", "qty": 4, "unit_price": 45.0, "amount": 180.0},
        {"name": "整单折让 -$20.00", "qty": 1, "unit_price": -20.0, "amount": -20.0},
        {"name": "胶筐押金 (2个)", "qty": 2, "unit_price": 20.0, "amount": 40.0},
        {"name": "送货运费", "qty": 1, "unit_price": 30.0, "amount": 30.0},
    ]

    clean_items, fees, removed = sanitizer.filter_and_extract_fees(dirty_items)

    assert len(clean_items) == 2, f"预期保留 2 项合法食材，实际保留 {len(clean_items)} 项"
    assert fees["discount_amount"] == 20.0, f"折让提取错误: {fees['discount_amount']}"
    assert fees["deposit_amount"] == 40.0, f"押金提取错误: {fees['deposit_amount']}"
    assert fees["delivery_fee"] == 30.0, f"运费提取错误: {fees['delivery_fee']}"

    clean_names = [it["name"] for it in clean_items]
    assert clean_names == ["特级有机菜心", "鲜活草虾"]


def test_math_engine_with_discount_and_deposit():
    """测试升级后的算术门禁正确校验 Σitems - discount + deposit + delivery = total。"""
    # 场景 1: 明细 265.0 - 折扣 20.0 + 押金 40.0 + 运费 30.0 = 315.0
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="香港富临海鲜蔬菜批发",
        date="2026-08-21",
        items=[
            ReceiptItem(name="特级有机菜心", qty=10.0, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="鲜活草虾", qty=4.0, unit="斤", unit_price=45.0, amount=180.0),
        ],
        discount_amount=20.0,
        deposit_amount=40.0,
        delivery_fee=30.0,
        total=315.0,  # 85 + 180 = 265; 265 - 20 + 40 + 30 = 315.0
        payment_marked=False,
        confidence=0.98
    )

    problems = math_engine.validate_and_report(data)
    assert len(problems) == 0, f"算术门禁误报错误: {problems}"


def test_math_engine_detects_real_error():
    """测试升级后的算术门禁仍能精准捕获真实差错。"""
    # 场景 2: 预期 315.0，但票面总额写成了 350.0 (差 35.0)
    data = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="香港富临海鲜蔬菜批发",
        date="2026-08-21",
        items=[
            ReceiptItem(name="特级有机菜心", qty=10.0, unit="斤", unit_price=8.5, amount=85.0),
            ReceiptItem(name="鲜活草虾", qty=4.0, unit="斤", unit_price=45.0, amount=180.0),
        ],
        discount_amount=20.0,
        deposit_amount=40.0,
        delivery_fee=30.0,
        total=350.0,  # 错误总额
        payment_marked=False,
        confidence=0.98
    )

    problems = math_engine.validate_and_report(data)
    assert len(problems) == 1, "算术门禁未能捕获真实总额差错"
    assert "预期总额=315.0，但总额=350.0" in problems[0]
