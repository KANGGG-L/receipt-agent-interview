# -*- coding: utf-8 -*-
"""
P2 扩展功能专项测试：图像质量门禁与香港传统度量衡/货币换算工具。
"""

import pytest
from ai_registry.tools.image_quality_guard.v1_0_0 import ImageQualityGuardTool
from ai_registry.tools.currency_unit_converter.v1_0_0 import CurrencyUnitConverterTool


def test_image_quality_guard():
    guard = ImageQualityGuardTool()

    # 1. 正常高质量图片
    good_meta = {"width": 1200, "height": 1600, "file_size": 2 * 1024 * 1024, "blur_score": 150.0}
    res = guard.execute(good_meta)
    assert res["is_acceptable"] is True
    assert res["quality_score"] >= 0.8
    assert len(res["warnings"]) == 0

    # 2. 严重模糊且分辨率过低图片
    bad_meta = {"width": 150, "height": 200, "file_size": 2048, "blur_score": 10.0}
    res = guard.execute(bad_meta)
    assert res["is_acceptable"] is False
    assert res["quality_score"] < 0.5
    assert len(res["warnings"]) > 0


def test_currency_and_unit_converter():
    converter = CurrencyUnitConverterTool()

    # 1. 司马斤 (Catty) 换算为标准 kg (1 司马斤 ≈ 0.6048 kg)
    kg_qty, unit = converter.convert_weight_to_standard_kg(10.0, "司马斤")
    assert unit == "kg"
    assert abs(kg_qty - 6.048) < 0.01

    kg_qty, unit = converter.convert_weight_to_standard_kg(10.0, "司馬斤")
    assert abs(kg_qty - 6.048) < 0.01

    # 2. 磅 (lb) 换算为标准 kg (10 磅 ≈ 4.536 kg)
    kg_qty, unit = converter.convert_weight_to_standard_kg(10.0, "磅")
    assert abs(kg_qty - 4.536) < 0.01

    # 3. 货币换算
    hkd = converter.convert_to_hkd(100.0, "HKD")
    assert hkd == 100.0

    hkd = converter.convert_to_hkd(100.0, "CNY")
    assert hkd == 110.0
