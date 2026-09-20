# -*- coding: utf-8 -*-
"""WS0 单位换算单一事实源（SSOT）回归测试。

覆盖：
1. costing_service 与 unit_factors 同源（同一对象 re-export，非拷贝漂移）；
2. 折算系数唯一：重量单位（含「斤」= 0.5）显示层与成本层同值，计件/体积/空单位不折算；
3. 显式 市斤=0.5 / 司马斤=0.6048 折算正确（含显示层与成本层）。
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services import unit_factors
from app.services import costing_service as costing_module
from app.services.costing_service import CostingService


def test_costing_service_and_unit_factors_share_single_source():
    """两模块对单位表与换算函数必须是同一对象（同源），而非各自拷贝的等价副本。"""
    assert costing_module.UNIT_WEIGHT_TO_KG is unit_factors.UNIT_WEIGHT_TO_KG
    assert costing_module.UNIT_VOLUME_TO_L is unit_factors.UNIT_VOLUME_TO_L
    assert costing_module.DIMENSION_COUNT is unit_factors.DIMENSION_COUNT
    assert costing_module.DIMENSION_WEIGHT is unit_factors.DIMENSION_WEIGHT
    assert costing_module.DIMENSION_VOLUME is unit_factors.DIMENSION_VOLUME
    # 函数同源：既有 `from app.services.costing_service import convert_unit_quantity` 不变味
    assert costing_module.convert_unit_quantity is unit_factors.convert_unit_quantity
    assert costing_module.check_unit_compatibility is unit_factors.check_unit_compatibility
    assert CostingService.convert_unit_quantity is unit_factors.convert_unit_quantity
    # 系数逐一相等（消除三方背离中的成本/显示两方）
    for k, v in unit_factors.UNIT_WEIGHT_TO_KG.items():
        assert costing_module.UNIT_WEIGHT_TO_KG[k] == v


def test_standard_kg_weight_units_convert_and_count_units_none():
    """重量单位按 unit_factors 折算；计件/体积/空单位返回 None（不折算）。"""
    from app import api_inventory

    assert api_inventory._standard_kg(10.0, "斤") == 5.0
    assert api_inventory._standard_kg(10.0, " 斤 ") == 5.0
    assert api_inventory._standard_kg(10.0, "市斤") == 5.0
    assert api_inventory._standard_kg(10.0, "司马斤") == 6.048
    assert api_inventory._standard_kg(10.0, "kg") == 10.0
    assert api_inventory._standard_kg(10.0, "g") == 0.01
    assert api_inventory._standard_kg(2.0, "磅") == round(2.0 * 0.45359237, 4)
    assert api_inventory._standard_kg(10.0, "份") is None
    assert api_inventory._standard_kg(10.0, "只") is None
    assert api_inventory._standard_kg(10.0, "") is None
    assert api_inventory._standard_kg(10.0, None) is None


def test_convert_unit_quantity_unified_factor():
    """成本层与显示层同系数：「斤」= 市斤 = 0.5；司马斤 = 0.6048。"""
    assert unit_factors.convert_unit_quantity(1.0, "斤", "kg") == 0.5
    assert unit_factors.convert_unit_quantity(1.0, "市斤", "kg") == 0.5
    assert unit_factors.convert_unit_quantity(1.0, "司马斤", "kg") == 0.6048
    assert unit_factors.convert_unit_quantity(604.8, "g", "司马斤") == 1.0
    # 跨量纲（计件 -> 重量）坚决阻断
    with pytest.raises(ValueError):
        unit_factors.convert_unit_quantity(1.0, "份", "kg")


def test_unit_to_kg_factor_single_value():
    """unit_to_kg_factor：重量单位给唯一系数，非重量单位/空值返回 None。"""
    assert unit_factors.unit_to_kg_factor("斤") == 0.5
    assert unit_factors.unit_to_kg_factor(" 斤 ") == 0.5
    assert unit_factors.unit_to_kg_factor("市斤") == 0.5
    assert unit_factors.unit_to_kg_factor("司马斤") == 0.6048
    assert unit_factors.unit_to_kg_factor("份") is None
    assert unit_factors.unit_to_kg_factor("") is None
    assert unit_factors.unit_to_kg_factor(None) is None
