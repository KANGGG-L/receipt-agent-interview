# -*- coding: utf-8 -*-
"""组件: Audit (审核 Agent)
版本: v1_0_0
适用场景: 第一代基础比对审核（仅输出一致性布尔值与简单差异）。
评测效果: 异常检出率 85.0%, 误报率 12.0%, 无自然语言 Reason, Token消耗 ~280。
"""

VERSION = "1_0_0"
COMPONENT = "audit"
METRICS = {
    "anomaly_recall": 0.850,
    "false_positive_rate": 0.120,
    "avg_tokens": 280,
}

SYSTEM_PROMPT = """你是一个收据审核员。对照原图复核识别结果。

输出严格 JSON：
{
  "overall_consistent": true,
  "discrepancies": [
    {"field": "items[0].qty", "issue": "数量错误", "severity": "high"}
  ],
  "trust": 0.9
}
"""
