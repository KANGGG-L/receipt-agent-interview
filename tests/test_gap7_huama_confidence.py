# -*- coding: utf-8 -*-
"""
Gap 7 专项测试：街市花码（苏州码子）特征检测、转译辅助与置信度硬性校准压降回归测试集。
"""

import pytest
from ai_registry.tools.huama_evaluator.v1_0_0 import HuamaEvaluatorTool


@pytest.fixture
def evaluator():
    return HuamaEvaluatorTool()


def test_huama_character_detection(evaluator):
    """测试各种常见街市花码字符检测。"""
    cases = [
        ("菜心 〡〇斤", True),
        ("草虾 〤斤", True),
        ("黄花鱼 〥条", True),
        ("和牛 〧斤", True),
        ("生菜 卄斤", True),
        ("有机菜心 10斤", False),
        ("可口可乐 24罐", False),
    ]
    for text, expected in cases:
        assert evaluator.contains_huama(text) == expected, f"花码检测错误: {text}"


def test_huama_digit_translation(evaluator):
    """测试花码到阿拉伯数字的转换翻译。"""
    cases = [
        ("〡〇", "10"),
        ("〤〥", "45"),
        ("〨.〥", "8.5"),
        ("卄", "20"),
        ("〣条", "3条"),
        ("〦斤", "6斤"),
    ]
    for raw, exp in cases:
        trans, has_h = evaluator.translate_huama_digits(raw)
        assert has_h is True, f"未识别出花码: {raw}"
        assert trans == exp, f"花码转译错误: {trans} != {exp} (原值: {raw})"


def test_confidence_suppression_and_flags(evaluator):
    """测试发现花码时，强制压降置信度至 <= 0.40 并打上待复核标签。"""
    payload = {
        "supplier_name": "油麻地街市老记蔬菜",
        "date": "2026-08-21",
        "confidence": 0.98,  # 模型虚高自报 0.98
        "items": [
            {
                "name": "有机菜心 〡〇斤",
                "quantity": 10,
                "unit": "斤",
                "unit_price": 8.5,
                "amount": 85.0,
                "confidence": 0.95  # 模型虚高自报 0.95
            },
            {
                "name": "鲜活草虾",
                "quantity": 4,
                "unit": "斤",
                "unit_price": 45.0,
                "amount": 180.0,
                "confidence": 0.92
            }
        ]
    }

    calibrated = evaluator.calibrate_confidence_and_flags(payload)

    # 1. 验证整单置信度被压降至 <= 0.45
    assert calibrated["confidence"] <= 0.45, f"整单置信度未被有效压降: {calibrated['confidence']}"
    assert calibrated["contains_huama"] is True, "未打上 contains_huama 标签"

    # 2. 验证包含花码的菜心行置信度被压降至 <= 0.40
    item1 = calibrated["items"][0]
    assert item1["confidence"] <= 0.40, f"明细行置信度未被压降: {item1['confidence']}"
    assert item1["contains_huama"] is True, "明细行未打上 contains_huama 标签"
    assert "花码" in item1.get("unit_conversion_warning", ""), "明细行缺少花码警示信息"

    # 3. 验证触发了人工复核警告
    assert any("花码" in w for w in calibrated.get("math_warnings", [])), "缺少人工复核警告信息"
