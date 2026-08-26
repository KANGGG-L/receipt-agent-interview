# -*- coding: utf-8 -*-
"""组件: Parse (收据解析与规范化 LLM)
版本: v2_1_0_sku_clean
适用场景: 司马斤/磅/板标准化换算 + 繁简对齐 + 食材品名纯净化 + 流水号/条形码/批次号剥离。
同步来源: ai_registry/prompts/parse/v2_1_0_sku_clean.py (Production Active)
"""

VERSION = "2_1_0_sku_clean"
COMPONENT = "parse"
METRICS = {
    "accuracy": 0.985,
    "unit_conversion_rate": 0.991,
    "clean_name_accuracy": 0.995,
    "overtrim_rate": 0.0,
    "avg_tokens": 610,
}

from ai_registry.prompts.parse.v2_1_0_sku_clean import PROMPT as _REGISTRY_PROMPT
from ai_registry.prompts.parse.v2_1_0_sku_clean import SYSTEM_PROMPT as _REGISTRY_SYSTEM

SYSTEM_PROMPT = _REGISTRY_SYSTEM
PROMPT = _REGISTRY_PROMPT
