# -*- coding: utf-8 -*-
"""提示词工程化资产库与评测中心（Prompt Registry & Evaluation Manager）。

统一管理全项目中 6 大核心 LLM / Agent 场景的提示词版本与评测基准：
1. extract: VLM 视觉识别提取
2. parse: 文本规范化清洗与单位对齐
3. audit: 交叉审核 Agent（带 Reason 与防注入）
4. review: 采购复盘与 4 步谈判策略推理
5. query: 问 AI 对话式查询与比价
6. memory: 供应商知识沉淀与别名提炼
"""

import importlib
from typing import Optional

# 当前各组件生效的生产默认版本
ACTIVE_VERSIONS = {
    "extract": "v1_1_0_hk",
    "parse": "v2_0_0_hk_units",
    "audit": "v2_0_0_reason",
    "review": "v2_0_0_cards",
    "query": "v1_0_0",
    "memory": "v1_0_0",
}

# 各组件全量版本索引
AVAILABLE_VERSIONS = {
    "extract": ["v1_0_0", "v1_1_0_hk"],
    "parse": ["v1_0_0", "v2_0_0_hk_units"],
    "audit": ["v1_0_0", "v2_0_0_reason"],
    "review": ["v1_0_0", "v2_0_0_cards"],
    "query": ["v1_0_0"],
    "memory": ["v1_0_0"],
}


def get_prompt(component: str, version: Optional[str] = None) -> str:
    """获取指定组件指定版本的提示词文本。"""
    v = version or ACTIVE_VERSIONS.get(component)
    if not v:
        raise ValueError(f"未知的组件类型: {component}")
    mod_path = f"app.prompts.{component}.{v}"
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, "SYSTEM_PROMPT", "")
    except Exception as e:
        raise ImportError(f"加载提示词失败: {mod_path}，原因: {e}")


def get_metrics(component: str, version: Optional[str] = None) -> dict:
    """获取指定组件版本的评测效果基准指标（准确率、Token数、召回率等）。"""
    v = version or ACTIVE_VERSIONS.get(component)
    mod_path = f"app.prompts.{component}.{v}"
    try:
        mod = importlib.import_module(mod_path)
        return getattr(mod, "METRICS", {})
    except Exception:
        return {}


def list_prompt_versions() -> dict:
    """列出当前各组件激活的提示词版本与全量可用版本。"""
    return {
        "active": dict(ACTIVE_VERSIONS),
        "available": dict(AVAILABLE_VERSIONS),
    }


def get_benchmark_report() -> dict:
    """获取全组件全版本的多维评测效果排行榜与演进对比。"""
    report = {}
    for comp, vers in AVAILABLE_VERSIONS.items():
        report[comp] = {}
        for v in vers:
            report[comp][v] = get_metrics(comp, v)
    return report
