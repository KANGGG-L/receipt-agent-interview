# -*- coding: utf-8 -*-
"""app_settings 类型化读写服务（T10 Gap E3：阈值规则配置化）。

- 事实源：db 的 app_settings 表（key/value 文本）；缺失/解析失败一律回退
  本文件 SETTINGS_DEFAULTS 的缺省值（= 迁移前各处硬编码现值，行为零变化）。
- 类型化：按缺省值类型决定解析目标（int/float/bool/str）。
- 实时生效：每次 get 都现读库（不做进程级缓存），管理台改完无需重启。
- 线程安全：只走 db.get_session() 短会话，读多写少，无额外锁。

收口状态（T10 已完成，此段为事实陈述而非待办）：全仓原硬编码阈值均已改由本服务
读取，模块常量仅保留为缺省值——
- rag.MemoryBudget → memory_budget_facts_tokens / memory_budget_per_item_tokens /
  memory_budget_max_items（app/services/rag.py 的 MemoryBudget.from_settings）；
- supervisor 低置信与候选池上限 → eval_candidate_low_confidence /
  eval_candidate_max_pending（app/chains/supervisor.py 的函数内实时读取）；
- db 记忆衰减 → memory_decay_hit_bonus / memory_decay_override_penalty /
  memory_archive_threshold（app/db.py 三处消费）+ memory_initial_decay_score
  （P1-T2：新记忆起始分，写入 upsert_vendor_memory 与恢复 revive_vendor_memory 共用）
  + memory_ttl_days（P1-T3：记忆过期天数，读侧过滤 db._filter_memory_rows_for_read
  与写侧 valid_to 计算共用同一读取函数 db.memory_ttl_days）
  + memory_max_active_per_vendor（W-4：每 (vendor, tenant) active 记忆容量上限，
  写入侧 db._evict_vendor_memory_over_capacity 淘汰消费）；
- rag 记忆总开关 → memory_enabled（app/services/rag.py 的 memory_enabled
  与 retrieve_context 入口短路，C1；读原始值，不走 get_bool）；
- rag 记忆语料 PII 脱敏 → memory_pii_mask_enabled（app/services/rag.py 的
  _mask_memory_text，W-9；缺省 False = 零行为变化，开启后写入路径落库前脱敏 notes/sample）；
- api_receipts 模糊拦截 → blur_laplacian_threshold（app/api_receipts.py:213）；
- models 反馈蒸馏与价格异动 → feedback_distill_threshold（db.should_distill_vendor_memory）
  / price_anomaly_threshold_pct（app/services/price_anomaly.py）；
- 解析超时清扫 → parsing_timeout_seconds（app/services/receipt_utils.parsing_timeout_seconds，
  被 sweep_parsing_timeout 与 app/main.py 启动自检、GET /api/admin/parsing-stale 共用）；
- 评测脚本数值 → evalset_split_* / evalset_min_short_side / gt_* / meta_eval_min_items
  （demo/scripts/_settings.py 统一入口）。
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
    # 新记忆起始衰减分（P1-T2 消费）。必须低于上限 1.0（缺省 0.7）：起始分与上限
    # 相同时命中强化 min(1.0, cur+bonus) 恒等，衰减只剩惩罚一侧、归档不可逆。
    # 取值需高于 memory_archive_threshold，否则新记忆写入即不可注入。
    "memory_initial_decay_score": 0.7,
    # 记忆 TTL 天数（P1-T3 消费：db.memory_ttl_days）。0 = 不过期（缺省），
    # 刻意不默认开启 —— TTL 一开，存量记忆会在某个时刻集体停止注入，属于
    # 「默认值即行为变更」，必须由管理台显式设置。
    "memory_ttl_days": 0,
    # 每 (vendor, tenant) 的 active 记忆容量上限（W-4 / M2 消费：
    # db._evict_vendor_memory_over_capacity，写入侧 upsert_vendor_memory 触发）。
    # 缺省 200 = 宽松但有界：远超正常单供应商的记忆条数，故任何既有用例（远未达上限）
    # 行为逐字节不变；又给"高频供应商无限追加"这道 W-4 缺陷划出边界。
    # 0 或负值 = 不限制（向后兼容，与 memory_ttl_days 同款"关闭即回到旧行为"语义）。
    # why 需要这一档：追加式写入此前无容量边界，高分供应商会被噪声记忆无限淹没，
    # 检索质量随使用下降；淘汰口径确定性且可审计，归档而非物理删除（可经 revive 恢复）。
    "memory_max_active_per_vendor": 200,
    # 记忆总开关（C1 消费：rag.memory_enabled / retrieve_context 入口短路）。
    # 缺省 True = 与接线前行为逐字节一致（零行为变化）。
    # why 需要这一档：闭环上线后如果怀疑记忆污染影响了识别，运维要能**一键停注入**
    # 而不必回滚代码/清库；关闭后 retrieve_context 直接返回空串（不查记忆表、不查
    # 向量、不记 hit），识别链路照常跑。注意它只关"读注入"，不关写入与人工闸 ——
    # 那不是开关而是数据处置，归删除/归档能力（P1-T1）。
    "memory_enabled": True,
    # 首轮供应商预判开关（P2-T6 消费：vendor_precheck.is_enabled）。
    # 缺省 False = 与接线前行为逐字节一致（零行为变化）：默认不额外发一次模型调用，
    # 也不消耗额度；要靠预判把"记忆命中率"从 1.7% 提上去时，由管理台显式开启。
    "vendor_precheck_enabled": False,
    # 首轮记忆注入开关（W-1 / Gap H4 消费：supervisor.run_pipeline 首轮前预判 + 检索）。
    # 缺省 False = 与接线前行为**逐字节一致**（零行为变化）：无显式 vendor_hint 时
    # 首轮不检索、不注入，与既有「首轮补检索只喂重试轮」形态完全相同。
    # 开启后：无显式 vendor_hint 的单据在**首轮 extract 之前**尽力确定供应商并检索
    # 注入记忆先验 —— 供应商来源优先取调用方 vendor_hint，否则调用 vendor_precheck
    # （仅当其自身开关 vendor_precheck_enabled 也为开时才发模型调用），两者都不行则
    # 不猜（纯新上传且无从确定供应商时保持现状，避免错误供应商污染）。
    "memory_first_round_inject_enabled": False,
    # LLM 抽取开关（P3-T2/K3 消费：api_receipts._memory_extract_enabled）。
    # 缺省 False = 与接线前行为逐字节一致（零行为变化）：抽取在写请求（复核保存 /
    # 入账）路径上发一次外部模型调用（审核腿），缺省不额外消耗额度、也不改变既有
    # 端点耗时。要靠「用户修正 → 规则级先验」把记忆反馈闭环接上时，由管理台显式开启
    # （PUT /api/admin/settings）；关闭时两处触发都直接返回，不建模型、不发调用。
    "memory_extract_enabled": False,
    # 记忆语料 PII 脱敏开关（W-9 / W-ISO-2 消费：rag._mask_memory_text）。
    # 缺省 True（遵循个人信息保护最小化原则，落库与写向量前脱敏 notes/sample）。
    # why 需要这一档：记忆语料的 notes/sample 直接落 OCR 明细文本，可能含员工姓名/
    # 电话/证件号等个人数据，且被长期保存、反复注入 prompt —— 香港 PDPO / GDPR 要求
    # 最小化，行业两家官方亦明确「不能只依赖模型过滤」。开启后，写入路径（ingest_memory
    # 与 approve_pending_to_memory 的自由文本分支）在**落库前 + 写向量前**对 notes/sample
    # 做确定性脱敏（复用 ai_registry.tools.pii_masker.v1_0_0，不另写正则实现），并按命中
    # 的 PII 类型计数 + logger.info 留痕（不打印原文）。
    # 脱敏只作用于记忆语料，不改动单据本身（receipts/receipt_items）。
    "memory_pii_mask_enabled": True,
    # FR-9 连续点踩蒸馏阈值（原 models.FEEDBACK_DISTILL_THRESHOLD=3）
    "feedback_distill_threshold": 3,
    # Gate-2 解析超时阈值（秒）：receipt_utils.parsing_timeout_seconds 消费，
    # 判定「卡在 parsing 多久算超期」；启动自检与 GET /api/admin/parsing-stale 同源。
    # 两件事分开陈述，不要合并成一句「无变化」：① 阈值缺省 300 = 接线前硬编码值，未变；
    # ② 执行时机由「从不执行」（sweep_parsing_timeout 此前零调用方）变为「每次服务启动
    # 执行」（main.py 的启动自检），属有意的行为变更 —— 可观测后果：卡 parsing 超过该
    # 阈值的单据会被置 error(parsing_timeout) 并落审计留痕。0/负值会被消费方钳回缺省。
    "parsing_timeout_seconds": 300,
    # FR-6 价格异动口径（原 models.PRICE_ANOMALY_THRESHOLD_PCT=10.0）
    "price_anomaly_threshold_pct": 10.0,
    # T10 预处理纠偏开关：默认 OFF，等灰测数据决定是否默认开启
    "preprocess_enabled": False,
    # T10 正交纠正（90/180/270）开关：默认 False（大角度自动翻转已永久下线）
    # 为避免越权颠倒收据，大角度翻转已停用，由前端显式控制
    "preprocess_orthogonal_enabled": False,
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
    # P2-1 图内隐藏文字检测（ai_registry.tools.image_injection_guard，只告警不拦截）：
    # 阈值默认值按「真实收据零误报」标定，缺省值 = 工具 DEFAULTS，可热调。
    "image_guard_work_max_side": 1400.0,
    "image_guard_faint_band_low": 10.0,
    "image_guard_faint_band_high": 38.0,
    "image_guard_faint_strong_th": 70.0,
    "image_guard_faint_exclude_frac": 0.20,
    "image_guard_faint_bright_min": 100.0,
    "image_guard_faint_min_score": 90.0,
    "image_guard_tiny_abs_max_h": 6.0,
    "image_guard_tiny_ratio": 0.4,
    "image_guard_tiny_contrast_min": 100.0,
    "image_guard_tiny_min_score": 60.0,
    "image_guard_edge_band_frac": 0.04,
    "image_guard_edge_gap_frac": 0.03,
    "image_guard_edge_min_score": 0.45,
    "image_guard_edge_min_components": 3.0,
    # 二维码与覆盖层 (W-SEC-4)
    "image_guard_qr_check_enabled": 1.0,
    "image_guard_overlay_min_score": 0.65,
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


def coerce_bool_raw(raw, default=True):
    """按布尔词解析 app_settings 的**原始文本值**（不查库），供直读消费方复用。

    why 需要这个公开入口：`get_bool` 把「键未设置」与「显式设为缺省值」压成同一个
    结果，而部分开关必须区分三态（未设置 / 显式开 / 显式关，如
    `rag.memory_enabled`）。这类消费方只能自己读 `db.get_app_setting` 的原始值 ——
    但词表与「无法识别回退缺省」的口径必须与 `_coerce` 逐字一致，否则同一个字符串
    在两个入口会解析出相反结果（本仓①类：同一根因多份实现）。故把解析收敛到这里，
    消费方只直读原始值、不复制解析逻辑。
    口径：None / 空串 → default；true/false 词（大小写不敏感）→ 对应布尔；
    无法识别的值 → default。
    """
    return _coerce(raw, default)


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
