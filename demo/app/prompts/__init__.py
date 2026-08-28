# -*- coding: utf-8 -*-
"""提示词工程化资产库与评测中心（T12 SSOT 收敛后的 ai_registry re-export 委托层）。

唯一事实源已上收至 ai_registry/prompts/（metadata.json + 版本 .py 文件），
本模块仅保留原函数名与返回形状，全部委托 ai_registry.registry.ai_registry 实现，
保证 /api/admin/prompts 与 run_eval 的既有调用方零改动。

原 ACTIVE_VERSIONS / AVAILABLE_VERSIONS 常量与 app.prompts 下各组件版本副本文件
已删除（版本清单改为从 ai_registry metadata 动态派生，杜绝双源漂移）。
"""

from typing import Optional

from ai_registry.registry import ai_registry


def get_prompt(component: str, version: Optional[str] = None) -> str:
    """获取指定组件指定版本的提示词文本（缺省取该场景 active 版本）。"""
    return ai_registry.get_prompt(component, version)


def get_metrics(component: str, version: Optional[str] = None) -> dict:
    """获取指定组件版本的评测效果基准指标（来自 ai_registry metadata 的 metrics 字段）。"""
    try:
        _, meta = ai_registry.get_prompt(component, version, with_metadata=True)
    except Exception:
        return {}
    return meta.get("metrics", {}) if isinstance(meta, dict) else {}


def list_prompt_versions() -> dict:
    """列出当前各组件激活的提示词版本与全量可用版本。"""
    available: dict = {}
    for scene in ai_registry.list_prompt_scenes():
        meta = ai_registry.get_prompt_metadata(scene)
        available[scene] = sorted(meta.get("versions", {}).keys())
    active = {}
    for scene in available:
        meta = ai_registry.get_prompt_metadata(scene)
        av = meta.get("active_version")
        if av:
            active[scene] = av
    return {
        "active": active,
        "available": available,
    }


def get_benchmark_report() -> dict:
    """获取全组件全版本的多维评测效果排行榜与演进对比。"""
    report = {}
    for comp, vers in list_prompt_versions()["available"].items():
        report[comp] = {}
        for v in vers:
            report[comp][v] = get_metrics(comp, v)
    return report
