# -*- coding: utf-8 -*-
"""度量衡换算单一事实源（SSOT）。

收口「成本层」与「显示层」共用的单位折算表，消除历史上的口径背离：
`api_inventory._standard_kg` 与前端换算器曾对「斤」给出与成本层不同的系数，
导致同一 SKU 的 kg 展示与成本口径不一致。

同一 SKU 的 BOM 只绑定一个 `base_unit`，同单位计算不涉及换算系数；
仅当跨单位（如配方用 g、SKU 用斤）或折算 kg 展示时才使用本表。
本表对「斤」给出唯一定义（0.5 kg）；港式司马斤请使用显式单位
「司马斤 / 港斤」（0.6048）。
"""

import logging
from typing import Dict, Optional, Set, Tuple

logger = logging.getLogger("unit_factors")


# 单位折算表：统一归一到标准单位
UNIT_WEIGHT_TO_KG: Dict[str, float] = {
    "kg": 1.0,
    "公斤": 1.0,
    "千克": 1.0,
    "g": 0.001,
    "克": 0.001,
    "mg": 0.000001,
    "毫克": 0.000001,
    "斤": 0.5,
    "市斤": 0.5,
    "两": 0.05,
    "市两": 0.05,
    "司马斤": 0.6048,
    "司馬斤": 0.6048,
    "港斤": 0.6048,
    "司马两": 0.0378,
    "司馬兩": 0.0378,
    "港两": 0.0378,
    "港兩": 0.0378,
    "磅": 0.45359237,
    "lb": 0.45359237,
    "lbs": 0.45359237,
    "t": 1000.0,
    "吨": 1000.0,
    "噸": 1000.0,
}

UNIT_VOLUME_TO_L: Dict[str, float] = {
    "l": 1.0,
    "升": 1.0,
    "liter": 1.0,
    "ml": 0.001,
    "毫升": 0.001,
}

DIMENSION_WEIGHT: Set[str] = set(UNIT_WEIGHT_TO_KG.keys())
DIMENSION_VOLUME: Set[str] = set(UNIT_VOLUME_TO_L.keys())
DIMENSION_COUNT: Set[str] = {
    "份", "件", "个", "個", "只", "隻", "条", "條", "包", "瓶", "罐", "盒", "碗", "杯", "碟", "支", "粒"
}


def unit_to_kg_factor(unit: Optional[str]) -> Optional[float]:
    """重量单位折算为 kg 的系数；非重量单位返回 None（调用方不得假装折算）。"""
    u = (unit or "").strip().lower()
    if not u:
        return None
    return UNIT_WEIGHT_TO_KG.get(u)


def check_unit_compatibility(u1: str, u2: str) -> Tuple[bool, str]:
    """校验两个单位是否同量纲可换算。"""
    u1_norm = (u1 or "").strip().lower()
    u2_norm = (u2 or "").strip().lower()
    if not u1_norm or not u2_norm:
        return False, "单位不能为空"
    if u1_norm == u2_norm:
        return True, ""
    w1, w2 = u1_norm in UNIT_WEIGHT_TO_KG, u2_norm in UNIT_WEIGHT_TO_KG
    if w1 and w2:
        return True, ""
    v1, v2 = u1_norm in UNIT_VOLUME_TO_L, u2_norm in UNIT_VOLUME_TO_L
    if v1 and v2:
        return True, ""
    return False, f"单位「{u1}」与「{u2}」属于不同度量体系（如重量与计件），无法自动折算"


def convert_unit_quantity(qty: float, from_unit: str, to_unit: str) -> float:
    """将数量从 from_unit 转换为 to_unit。

    若两个单位处于同一量纲（重量或体积），执行精准转换；
    若单位完全相同，保持原数值；
    若跨量纲或无法折算，抛出明确 ValueError。
    """
    if qty == 0.0:
        return 0.0
    u_from = (from_unit or "").strip().lower()
    u_to = (to_unit or "").strip().lower()

    if u_from == u_to:
        return float(qty)

    # 重量体系转换
    if u_from in UNIT_WEIGHT_TO_KG and u_to in UNIT_WEIGHT_TO_KG:
        kg_qty = qty * UNIT_WEIGHT_TO_KG[u_from]
        return round(kg_qty / UNIT_WEIGHT_TO_KG[u_to], 6)

    # 体积体系转换
    if u_from in UNIT_VOLUME_TO_L and u_to in UNIT_VOLUME_TO_L:
        l_qty = qty * UNIT_VOLUME_TO_L[u_from]
        return round(l_qty / UNIT_VOLUME_TO_L[u_to], 6)

    # 跨量纲不可换算，坚决阻断
    raise ValueError(f"跨量纲单位不可换算: {from_unit} -> {to_unit}")
