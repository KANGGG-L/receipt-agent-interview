# -*- coding: utf-8 -*-
"""组件: Parse (收据解析与规范化 LLM)
版本: v1_0_0
适用场景: 将 VLM 或 OCR 粗提取的杂乱文本规范化为标准 JSON。
评测效果: 字段准确率 89.5%, Token消耗 ~450, 耗时 ~1.8s。
"""

VERSION = "1_0_0"
COMPONENT = "parse"
METRICS = {
    "accuracy": 0.895,
    "format_compliance": 0.98,
    "avg_latency_s": 1.8,
    "avg_tokens": 450,
}

SYSTEM_PROMPT = """你是一个收据文本结构化解析助手。
请将输入的不规则 OCR 文本规范化清洗为标准的商品明细列表。

输出严格 JSON：
{
  "vendor": "供应商名称",
  "date": "YYYY-MM-DD",
  "items": [
    {"name": "商品名称", "qty": 1.0, "unit": "单位", "unit_price": 0.0, "amount": 0.0}
  ],
  "total": 0.0
}
"""
