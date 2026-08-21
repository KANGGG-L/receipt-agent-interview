# -*- coding: utf-8 -*-
"""
P1-1 (Gap 9) 修复专项测试：加一服务费、税费与尾数抹零解耦及算术守恒核验。
"""

import pytest
from app.models import ReceiptData, ReceiptItem, DocForm
from app.services.math_engine import validate_and_report
from ai_registry.tools.item_sanitizer.v1_3_0_notes_clean import ItemSanitizerTool


def test_service_fee_tax_rounding_decoupling():
    sanitizer = ItemSanitizerTool()
    raw_items = [
        {"name": "烧鹅半只", "quantity": 1, "unit": "只", "unit_price": 125.0, "amount": 125.0},
        {"name": "加一服務費 10%", "quantity": 1, "unit": "项", "unit_price": 12.5, "amount": 12.5},
        {"name": "增值税 5%", "quantity": 1, "unit": "项", "unit_price": 6.88, "amount": 6.88},
        {"name": "尾数抹零", "quantity": 1, "unit": "项", "unit_price": 0.38, "amount": 0.38},
    ]

    clean_items, fees, notes, dropped = sanitizer.filter_and_extract_all(raw_items)

    assert len(clean_items) == 1
    assert clean_items[0]["name"] == "烧鹅半只"
    assert fees["service_fee"] == 12.5
    assert fees["tax_amount"] == 6.88
    assert fees["rounding_adjustment"] == 0.38


def test_math_engine_with_service_tax_rounding():
    # 场景: 明细 $125.00 + 服务费 $12.50 + 税额 $6.88 - 抹零 $0.38 = $144.00
    items = [
        ReceiptItem(name="烧鹅半只", qty=1.0, unit="只", unit_price=125.0, amount=125.0)
    ]
    data = ReceiptData(
        doc_form=DocForm.THERMAL,
        vendor="翠华餐厅",
        date="2026-08-21",
        items=items,
        total=144.0,
        service_fee=12.5,
        tax_amount=6.88,
        rounding_adjustment=0.38,
        payment_marked=True,
        confidence=0.95
    )

    problems = validate_and_report(data)
    assert problems == [], f"服务费与税费算术守恒误报: {problems}"


def test_math_engine_detects_service_fee_mismatch():
    # 场景: 实际总额应为 $144.00，但单据总额为 $130.00
    items = [
        ReceiptItem(name="烧鹅半只", qty=1.0, unit="只", unit_price=125.0, amount=125.0)
    ]
    data = ReceiptData(
        doc_form=DocForm.THERMAL,
        vendor="翠华餐厅",
        date="2026-08-21",
        items=items,
        total=130.0,
        service_fee=12.5,
        payment_marked=True,
        confidence=0.95
    )

    problems = validate_and_report(data)
    assert len(problems) > 0
    assert "服务费" in problems[0]
