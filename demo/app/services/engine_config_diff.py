# -*- coding: utf-8 -*-
"""引擎配置字段级 diff（配置版本化共用实现）。

为什么单独一个模块：写操作分布在 `api_admin`（PUT 保存 / 推全 / 复位）与
`services.guardian`（回滚）两处，而它们都要回答同一个问题「这次写让哪些字段变了」
（409 冲突响应、版本历史 changed_keys）。各写一份比较逻辑必然漂移 —— 本仓高发的
「同一口径多份实现」，故收敛为一份。

脱敏口径：api_key 系字段的新旧值一律只回显掩码，真实密钥不得进入任何响应体。
"""

from app.services.security_guard import mask_secret_key


def _norm_value(value):
    """比较用归一：枚举取 .value（与请求体里的字符串同口径），其余原样。"""
    return value.value if hasattr(value, "value") else value


def _side_value(key, value):
    """回显用归一 + 脱敏：api_key 系只回显掩码。"""
    if isinstance(key, str) and "api_key" in key:
        return mask_secret_key(value if isinstance(value, str) else str(value or ""))
    return value


def engine_config_field_diff(old, new, ignore=()) -> list:
    """字段级 diff：返回 [{key, old, new}]，只列实际发生变化的键（值级比较）。

    old / new 为两份 `EngineConfig.model_dump()`（或以字段名到值映射的 dict）。
    ignore 用于剔除不该参与比较的键（调用方用于排除 version 自身）。
    """
    old = old or {}
    new = new or {}
    diffs = []
    for key, new_value in new.items():
        if key in ignore:
            continue
        old_value = old.get(key)
        if _norm_value(old_value) != _norm_value(new_value):
            diffs.append({
                "key": key,
                "old": _side_value(key, _norm_value(old_value)),
                "new": _side_value(key, _norm_value(new_value)),
            })
    return diffs
