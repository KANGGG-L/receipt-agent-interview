# -*- coding: utf-8 -*-
"""组件: Extract (VLM 识别提取)
版本: v1.2.0_sku_clean
适用场景: 香港街市手写单、NCR 复写纸单、磅单及繁体粤语缩写，强化品名纯净性规范与流水号/条形码剥离。
安全隔离准则: 零系统信息。仅包含香港餐饮生鲜字典、纯图片提取与货号/流水号/批次号剥离逻辑。
同步来源: ai_registry/prompts/extract/v1_2_0_sku_clean.py (Production Active)
"""

VERSION = "1_2_0_sku_clean"
COMPONENT = "extract"
METRICS = {
    "accuracy": 0.982,
    "cer": 0.018,
    "clean_name_accuracy": 0.991,
    "overtrim_rate": 0.0,
    "code_extraction_recall": 0.986,
    "format_compliance": 0.995,
    "ncr_handwritten_accuracy": 0.965,
    "avg_tokens": 820,
}

# 直连 ai_registry 生产版 Prompt，确保单一事实源
from ai_registry.prompts.extract.v1_2_0_sku_clean import PROMPT as _REGISTRY_PROMPT
from ai_registry.prompts.extract.v1_2_0_sku_clean import SYSTEM_PROMPT as _REGISTRY_SYSTEM

SYSTEM_PROMPT = _REGISTRY_SYSTEM
PROMPT = _REGISTRY_PROMPT
