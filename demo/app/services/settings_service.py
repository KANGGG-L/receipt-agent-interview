# -*- coding: utf-8 -*-
"""app_settings 类型化读写服务（T10 Gap E3：阈值规则配置化）。

- 事实源：db 的 app_settings 表（key/value 文本）；缺失/解析失败一律回退
  本文件 SETTINGS_DEFAULTS 的缺省值（= 迁移前各处硬编码现值，行为零变化）。
- 类型化：按缺省值类型决定解析目标（int/float/bool/str）。
- 实时生效：每次 get 都现读库（不做进程级缓存），管理台改完无需重启。
- 线程安全：只走 db.get_session() 短会话，读多写少，无额外锁。

TODO(T10) 收口：全仓原硬编码阈值（rag.MemoryBudget / supervisor 低置信与
候选池上限 / db 记忆衰减 / api_receipts 模糊拦截 / models 反馈蒸馏与价格
异动 / 评测脚本数值）统一改由本服务读取，模块常量保留为缺省值。
"""

import logging

logger = logging.getLogger(__name__)


# 缺省值 = 迁移前的硬编码现值（类型即解析目标类型）
SETTINGS_DEFAULTS = {
    # api_receipts 极模糊前置拦截：Laplacian 方差阈值（原 BLUR_THRESHOLD=30.0）
    "blur_laplacian_threshold": 30.0,
    # supervisor 低置信回流阈值（原 EVAL_CANDIDATE_LOW_CONFIDENCE=0.6）
    "eval_candidate_low_confidence": 0.6,
    # db 候选池卫生：pending 候选总量上限（原 EVAL_CANDIDATE_MAX_PENDING=500）
    "eval_candidate_max_pending": 500,
    # supervisor 审核分歧严重度门槛（原 AUDIT_DISCREPANCY_SEVERE_MIN_COUNT=2）
    "audit_discrepancy_severe_min_count": 2,
    # rag.MemoryBudget 读取预算（原 800/200/6）
    "memory_budget_facts_tokens": 800,
    "memory_budget_per_item_tokens": 200,
    "memory_budget_max_items": 6,
    # 记忆非对称衰减（T8 消费）：命中强化慢 / 覆写淘汰快 / 归档阈值
    "memory_decay_hit_bonus": 0.05,
    "memory_decay_override_penalty": 0.12,
    "memory_archive_threshold": 0.5,
    # FR-9 连续点踩蒸馏阈值（原 models.FEEDBACK_DISTILL_THRESHOLD=3）
    "feedback_distill_threshold": 3,
    # FR-6 价格异动口径（原 models.PRICE_ANOMALY_THRESHOLD_PCT=10.0）
    "price_anomaly_threshold_pct": 10.0,
    # T10 预处理纠偏开关：默认 OFF，等灰测数据决定是否默认开启
    "preprocess_enabled": False,
    # T9（Gap C3）实验守护阈值（GUARD_*）：触发即自动回滚并冻结实验
    "guard_success_drop_pp": 5,
    "guard_cost_rise_pct": 50,
    "guard_p95_latency_ms": 20000,
    # T9 守护巡检间隔（分钟）与方向性指标漂移告警阈值（%，只告警不回滚）
    "guard_interval_minutes": 15,
    "guard_directional_drift_pct": 20,
    # 评测集构建（build_evalset.py 原常量）
    "evalset_split_train": 0.55,
    "evalset_split_val": 0.25,
    "evalset_split_test": 0.20,
    "evalset_min_split_coverage": 3,
    "evalset_min_short_side": 1000,
    # GT 候选生成（gen_gt_candidates.py 原常量）
    "gt_min_short_side": 1000,
    "gt_preview_long_side": 1600,
    "gt_jpeg_quality": 85,
    # 元评测集最小条数（run_meta_eval.py 原常量）
    "meta_eval_min_items": 20,
}

_TRUE_WORDS = ("1", "true", "yes", "on", "y", "t")
_FALSE_WORDS = ("0", "false", "no", "off", "n", "f")


def _coerce(raw, default):
    """按 default 的类型解析 db 里的字符串值；解析失败返回 default。"""
    if raw is None:
        return default
    s = str(raw).strip()
    if not s:
        return default
    try:
        if isinstance(default, bool):
            low = s.lower()
            if low in _TRUE_WORDS:
                return True
            if low in _FALSE_WORDS:
                return False
            return default
        if isinstance(default, int):
            return int(float(s))
        if isinstance(default, float):
            return float(s)
        return s
    except (TypeError, ValueError):
        return default


def get(key, default=None):
    """读取配置：db 覆盖值 > 显式 default 参数 > SETTINGS_DEFAULTS 缺省。

    任何异常（db 不可用等）回退缺省值，绝不向上抛错阻断业务。
    """
    fallback = SETTINGS_DEFAULTS.get(key) if default is None else default
    try:
        from app import db
        raw = db.get_app_setting(key)
    except Exception as e:
        logger.warning("[settings] read %s failed, fallback default: %s", key, e)
        return fallback
    if raw is None:
        return fallback
    return _coerce(raw, fallback)


def get_int(key, default=None):
    return int(get(key, default))


def get_float(key, default=None):
    return float(get(key, default))


def get_bool(key, default=None):
    return bool(get(key, default))


def get_str(key, default=None):
    return str(get(key, default))


def set_value(key, value):
    """写入配置（布尔序列化为 true/false）。写入后即刻对后续 get 可见。"""
    if isinstance(value, bool):
        raw = "true" if value else "false"
    else:
        raw = str(value)
    from app import db
    db.set_app_setting(key, raw)
    return get(key)


def all_settings():
    """全量配置视图（缺省键全部列出，含当前生效值），供管理台表单渲染。"""
    out = {}
    for key in SETTINGS_DEFAULTS:
        out[key] = get(key)
    return out
