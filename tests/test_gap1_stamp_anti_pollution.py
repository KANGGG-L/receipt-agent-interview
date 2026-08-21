# -*- coding: utf-8 -*-
"""
Gap 1 专项测试：印章/签名/手写批注污染明细行防护与白名单保护回归测试集。
"""

import pytest
from ai_registry.tools.item_sanitizer.v1_0_0 import ItemSanitizerTool


@pytest.fixture
def sanitizer():
    return ItemSanitizerTool()


def test_stamp_filtration(sanitizer):
    """测试各类印章词被准确识别并过滤。"""
    stamp_cases = [
        "现金收讫", "現金收訖", "收讫", "收訖", "已收讫",
        "PAID", "CASH PAID", "CASH", "RECEIVED", "PAYMENT RECEIVED", "SETTLED",
        "已收", "已付", "收妥", "已結清", "已结清", "付讫"
    ]
    for text in stamp_cases:
        is_noise, reason = sanitizer.is_stamp_or_annotation(text, qty=1, unit_price=0, amount=0)
        assert is_noise is True, f"印章文字 '{text}' 未被正确识别为杂质 (reason: {reason})"


def test_signature_and_annotation_filtration(sanitizer):
    """测试签名、经手人、免责声明等批注行被准确识别并过滤。"""
    annotation_cases = [
        "司厨签收", "司厨签收: 李师傅", "經手人: 陳先生", "经手人: 王五",
        "收货人: 赵六", "验收人", "签名: 张三", "支票支付", "FPS 转账",
        "如有遗失恕不负责", "货物出门恕不退换", "THANK YOU 多谢惠顾"
    ]
    for text in annotation_cases:
        is_noise, reason = sanitizer.is_stamp_or_annotation(text, qty=1, unit_price=0, amount=0)
        assert is_noise is True, f"批注文字 '{text}' 未被正确识别为杂质 (reason: {reason})"


def test_legit_item_whitelist_protection(sanitizer):
    """测试合法包含印章/收据字眼的正常商品绝不误杀。"""
    legit_cases = [
        ("印章印油", 2.0, 15.0, 30.0),
        ("红色印泥 1盒", 1.0, 20.0, 20.0),
        ("送货单收据本", 5.0, 10.0, 50.0),
        ("现金账本", 1.0, 25.0, 25.0),
        ("优质原子印油", 3.0, 18.0, 54.0),
        ("有机菜心", 10.0, 5.5, 55.0),
        ("鲜鸡蛋 30只", 2.0, 45.0, 90.0),
    ]
    for name, qty, price, amt in legit_cases:
        is_noise, reason = sanitizer.is_stamp_or_annotation(name, qty, price, amt)
        assert is_noise is False, f"合法商品 '{name}' 被错误判定为杂质 (reason: {reason})"


def test_filter_items_pipeline(sanitizer):
    """测试商品明细批处理过滤流水线。"""
    dirty_items = [
        {"name": "优质白菜", "qty": 10, "unit_price": 4.0, "amount": 40.0},
        {"name": "现金收讫", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "有机菜心", "qty": 5, "unit_price": 8.0, "amount": 40.0},
        {"name": "司厨签收: 李大厨", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "PAID", "qty": 1, "unit_price": 0.0, "amount": 0.0},
        {"name": "印章印油", "qty": 2, "unit_price": 15.0, "amount": 30.0},
    ]

    clean_items, removed = sanitizer.filter_items(dirty_items)

    assert len(clean_items) == 3, f"预期保留 3 项合法商品，实际保留 {len(clean_items)} 项"
    assert len(removed) == 3, f"预期过滤 3 项杂质行，实际过滤 {len(removed)} 项"

    clean_names = [it["name"] for it in clean_items]
    assert clean_names == ["优质白菜", "有机菜心", "印章印油"]

    removed_names = [it["name"] for it in removed]
    assert "现金收讫" in removed_names
    assert "司厨签收: 李大厨" in removed_names
    assert "PAID" in removed_names
