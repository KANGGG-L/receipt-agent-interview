# -*- coding: utf-8 -*-
from __future__ import annotations
"""领域模型：Pydantic 契约 + SQLAlchemy 持久化。"""

from datetime import datetime
from enum import Enum
from typing import ClassVar, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator


# -------------------------------------------------------------
# 收据七种形态（对齐完整版调研结论）
# -------------------------------------------------------------
class DocForm(str, Enum):
    PRINTED = "printed_delivery_note"   # 印刷送货单
    NCR_HAND = "ncr_handwritten"        # 街市 NCR 手写单
    THERMAL = "thermal"                 # 热敏机打
    WEIGH = "weigh_slip"                # 磅单
    CORRECTION = "correction_note"      # 更正单
    CREDIT = "credit_note"              # Credit Note
    MONTHLY = "monthly_statement"       # 月结账单


# -------------------------------------------------------------
# Pydantic 契约（AI 输出契约，契约门禁用）
# -------------------------------------------------------------
class Evidence(BaseModel):
    """字段级证据（Gap E1 / T7）：明细行在原图上的定位证据。

    可选、非强制：契约门禁不校验该字段，未升级的模型不输出也合法；
    子字段宽松模式（extra 默认 ignore），证据杂质不得阻断主链路。
    bbox 为归一化坐标 [x1, y1, x2, y2]（0-1，左上/右下），供前端换算高亮。
    """
    page: int = 1
    bbox: Optional[list[float]] = None
    raw_text: Optional[str] = None


class ReceiptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 契约门禁：拒绝 schema 外字段

    name: str = Field(description="商品/食材名称")
    qty: float = Field(description="数量")
    unit: str = Field(description="单位（斤/公斤/箱/包/只…）")
    unit_price: float = Field(description="单价")
    amount: float = Field(description="小计 = 数量 × 单价")
    raw_name: Optional[str] = Field(default=None, description="原始品名")
    is_void: bool = Field(default=False, description="是否划线作废/拒收 (Gap 6)")
    actual_qty: Optional[float] = Field(default=None, description="手写实收/修改后数量 (Gap 6)")
    evidence: Optional[Evidence] = Field(default=None, description="字段级证据（Gap E1）：page/bbox 归一化坐标/图面原文，可选不强制")
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0, description="本行识别置信度（P11）：低置信行可按行定位；花码行会被 huama_evaluator 压降至 0.40。LLM 未给出时为 None")
    # Gap 7：花码标记必须落在契约内。huama_evaluator 在白名单裁剪之后写这两个键，
    # extra="forbid" 会因此把整单打回（items.0.contains_huama: Extra inputs are not permitted）。
    # 声明为可选：LLM 不输出、非花码行缺失均合法；花码行由校准器写入并随 ai_prefill_json 落库。
    contains_huama: bool = Field(default=False, description="本行是否含街市花码（苏州码子），由 huama_evaluator 校准写入")
    unit_conversion_warning: Optional[str] = Field(default=None, description="单位不可折算/花码待核验提示，落 receipt_items.unit_conversion_warning 并传前端")


class ReceiptData(BaseModel):
    model_config = ConfigDict(extra="forbid")  # 契约门禁：拒绝 schema 外字段

    doc_form: DocForm = Field(description="单据形态")
    vendor: str = Field(description="供应商名称")
    date: str = Field(description="单据日期，YYYY-MM-DD")
    items: list[ReceiptItem] = Field(description="商品明细")
    total: float = Field(description="总额")
    discount_amount: float = Field(default=0.0, description="整单折让/折扣金额")
    deposit_amount: float = Field(default=0.0, description="押金金额（如胶筐押金）")
    delivery_fee: float = Field(default=0.0, description="运费/送货费")
    service_fee: float = Field(default=0.0, description="加一服务费/服务费 (Gap 9)")
    tax_amount: float = Field(default=0.0, description="税额/VAT/GST (Gap 9)")
    rounding_adjustment: float = Field(default=0.0, description="尾数抹零/舍入调整 (Gap 9)")
    fees_detail: dict[str, float] = Field(default_factory=dict, description="费用明细字典 (Gap 9)")
    adjustment_notes: list[str] = Field(default_factory=list, description="手写调整、拒收或短装注记 (Gap 6)")
    payment_marked: bool = Field(description="是否有已付款标记（印章/手写）")
    payment_evidence: str = Field(default="", description="已付款标记的图面证据描述（印章/手写「已付款」等）")
    currency: str = Field(default="HKD", description="币种 (HKD/CNY/USD)")
    confidence: float = Field(ge=0.0, le=1.0, description="整体置信度")
    # Gap 7：整单花码标记与警告。同样由 huama_evaluator 在校准阶段写入，
    # 若不在契约内声明则触发 extra="forbid" 整单打回；可选、缺失合法。
    contains_huama: bool = Field(default=False, description="整单是否检测到街市花码（苏州码子）")
    math_warnings: list[str] = Field(default_factory=list, description="门禁数学/花码等复核警告（只增不改，供人工复核提示）")


# -------------------------------------------------------------
# 领域模型（SQLAlchemy）
# -------------------------------------------------------------
class Receipt(BaseModel):
    """收据记录（内存态 + 轻量持久化的简化版）。"""
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    image_path: str = ""
    status: str = "uploaded"   # uploaded → parsing → parsed / parsed_with_warnings → edited → approved/flagged/error
    vendor: str = ""
    date: str = ""
    doc_form: str = ""
    total: float = 0.0
    items: list[ReceiptItem] = []
    raw_llm: str = ""          # VLM 原始输出（保留可审计）
    audit_result: dict = {}    # 审核 Agent 结论
    confidence: float = 0.0
    created_at: str = ""


class InventoryEntry(BaseModel):
    """幂等入库记录（append-only）。"""
    model_config = ConfigDict(extra="forbid")

    receipt_id: str = ""
    name: str = ""
    qty: float = 0.0
    unit: str = ""
    amount: float = 0.0
    vendor: str = ""
    date: str = ""
    created_at: str = ""


class VendorMemory(BaseModel):
    """VendorMemory：按供应商积累的识别上下文（RAG 语料）。"""
    model_config = ConfigDict(extra="forbid")

    vendor: str = ""
    notes: str = ""            # layout_notes / 别称 / 单位基准
    sample: str = ""           # 最近一次已确认明细（few-shot 来源）


# -------------------------------------------------------------
# 收据反馈飞轮（FR-8 / FR-9）常量
# -------------------------------------------------------------
# T10 收口：本常量仅作 settings 缺省值，运行时经 settings_service 键
# 'feedback_distill_threshold' 实时读取（消费方：db.should_distill_vendor_memory / api_receipts）
FEEDBACK_DISTILL_THRESHOLD = 3  # 连续点踩阈值：同供应商同租户连续 N 次点踩触发记忆沉淀
FEEDBACK_COMMENT_MAXLEN = 2000

# -------------------------------------------------------------
# 价格异动口径（FR-6）：最新单价较均价涨幅超过该百分比视为异常
# 库存页与 AI 发现（weekly_insights）共用，保证两处数字一致
# T10 收口：本常量仅作 settings 缺省值，运行时经 settings_service 键
# 'price_anomaly_threshold_pct' 实时读取（消费方：services.price_anomaly）
# -------------------------------------------------------------
PRICE_ANOMALY_THRESHOLD_PCT = 10.0

# -------------------------------------------------------------
# 收据反馈飞轮（FR-8 / FR-9）
# 每明细行可点赞/点踩 + 文本反馈，落库 receipt_feedback，Chroma 租户隔离沉淀
# -------------------------------------------------------------
class ReceiptFeedback(BaseModel):
    """单据/明细行级反馈（FR-8 逐字段置信度闭环 + FR-9 记忆飞轮）。"""
    model_config = ConfigDict(extra="forbid")

    id: Optional[int] = None
    receipt_id: int = Field(description="关联收据 ID")
    item_index: Optional[int] = Field(default=None, description="明细行下标（None=整单反馈）")
    like: Optional[int] = Field(default=None, description="1=点赞, -1=点踩, 0/None=未表态")
    comment: str = Field(default="", description="文本反馈")
    quality_warnings: list[str] = Field(default_factory=list, description="关联 qualityWarnings 快照")
    tenant_id: str = Field(default="default", description="租户隔离键")
    vendor: str = Field(default="", description="供应商名快照")
    created_at: str = Field(default="", description="创建时间 ISO")
    updated_at: str = Field(default="", description="更新时间 ISO")


# -------------------------------------------------------------
# 灰测 / 引擎配置（admin 可调整）
# -------------------------------------------------------------
class GreyAssignMode(str, Enum):
    RECEIPT = "receipt"          # 按单据随机分配
    SUPPLIER = "supplier"        # 按供应商分配（同供应商一致命中）


class EngineKind(str, Enum):
    """引擎通道类型（现存只有一种：OpenAI 兼容通道）。

    历史注记：本枚举原有 CODEBUDDY / OPENCODE 两个成员（本机 CLI 引擎），
    已于 2026-09-02 随 CLI 引擎弃用一并删除。存量 DB 与回滚快照里可能仍存有
    "opencode" / "codebuddy" 字符串，由 db._normalize_legacy_cli_leg 在构造
    EngineConfig 之前改写为 openai，故删成员不会让旧配置加载失败。
    """
    OPENAI = "openai"            # 自定义 OpenAI 兼容（base_url + api_key + model）


import os
from dotenv import load_dotenv
load_dotenv()

# 识别腿默认 provider：阿里云百炼 DashScope（OpenAI 兼容通道，与 llm.DASHSCOPE_COMPATIBLE_URL 一致）。
# 用户决策（2026-09-15）：识别腿默认切至 DashScope qwen3.5-omni-flash；
# 审核腿保持 SiliconFlow GLM-4.5V（跨厂商异构，Gap A3 不变）。
# 必须锁 recognition_engine="openai"：模型名以 qwen 开头，引擎类型非 openai 时
# 会被 llm._resolve_engine 判去 QwenChatModel 原生 SDK 通道。
DASHSCOPE_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_DEFAULT_REC_MODEL = "qwen3.5-omni-flash"

# 已弃用 CLI 引擎（opencode / codebuddy）的模型名特征。用户决策（2026-09-02）：
# opencode 已过期，不再作为默认或可选项；其模型名若留在配置里，会把请求打进错误的
# provider —— 实测 `AUDIT_MODEL=opencode/mimo-v2.5-free` 时审核腿的 base 是
# api.siliconflow.cn，却带着 opencode 的模型名，必然失败。
# 单一事实源：db._normalize_legacy_cli_engines、db 的读写双闸门、本文件的
# default_factory 全部引用这里，避免同一判定被抄 9 份、只改一份（修 A 漏 B）。
LEGACY_CLI_MODEL_PREFIXES = ("opencode", "codebuddy")
LEGACY_CLI_MODEL_NAMES = ("minimax-m3-pay",)
# 已弃用 CLI 引擎的**引擎字段取值**（与模型名判定区分：一个是 engine 值，一个是 model 名）。
LEGACY_CLI_ENGINE_VALUES = ("opencode", "codebuddy")


def is_legacy_cli_engine(value) -> bool:
    """引擎字段的取值是否属于已弃用 CLI 引擎（"opencode" / "codebuddy"）。

    EngineKind 已删除这两个成员，但存量 DB、推全回滚快照、管理台旧 payload 里仍可能有，
    任何构造 EngineConfig 的路径都必须先把它们改写为 openai。本函数是该判定的唯一实现
    （原先在 EngineConfig 校验器 / db._normalize_legacy_cli_leg / api_admin 各写一份）。
    """
    return str(getattr(value, "value", value) or "").strip().lower() in LEGACY_CLI_ENGINE_VALUES


def is_legacy_cli_model(name) -> bool:
    """模型名是否属于已弃用的 CLI 引擎（opencode/*、codebuddy/*、minimax-m3-pay）。"""
    m = str(name or "").strip().lower()
    if not m:
        return False
    return m.startswith(LEGACY_CLI_MODEL_PREFIXES) or m in LEGACY_CLI_MODEL_NAMES


def env_model_or_default(raw, fallback: str) -> str:
    """.env 取到的模型名：若属已弃用 CLI 引擎则丢弃，回落内置默认。

    缺这一步，「直构 EngineConfig」（empty DB 首次建配置 / 单测路径）与「hydrate 之后」
    会给出**不同的审核模型**：hydrate 路径有 _normalize_legacy_cli_engines 兜底，
    直构路径没有 —— 属「定义了没接线」。把判定接在 default_factory 上，两条路径同口径。
    空串/空白视为未设置（保持「空 = 不覆盖」的既有语义），不做 strip 以外的新解释。
    """
    if is_legacy_cli_model(raw):
        return fallback
    return (str(raw).strip() if raw is not None else "") or fallback


# 单引擎调用超时的硬上限（秒）。高压后厨禁止长期空转（omni 实测单张 2.6-7.1s，60s 结余充足）。
# 这是硬约束，不是管理台可绕过的偏好：无论存量值、管理台保存值还是 env 值，实际生效超时都不超过它。
# 三处钳制统一引用本常量（唯一事实源，避免字面量 60 在 llm/models/api_admin/db 之间漂移）：
#   1. llm._resolve_timeout —— 运行期钳制（真正的执行边界）
#   2. db.hydrate_engine_config_from_env —— 存量 DB 值归一（消除「显示 90 实际 60」的误导）
#   3. api_admin PUT /api/admin/engine-config —— 保存时归一，使落库值恒等于生效值
MAX_CALL_TIMEOUT_SECONDS = 60


def clamp_call_timeout(value):
    """把超时值归一到硬上限内：> MAX_CALL_TIMEOUT_SECONDS 钳到上限，其余原样返回。

    口径与 llm._resolve_timeout 保持一致：None / 0 / 负值 / 非 int 一律**不在此改写**
    （运行期把它们视为「未设置」并回落缺省 30s），避免把非法值静默改成看似合法的 60。
    bool 是 int 的子类，需显式排除，否则 True 会被当成 1 误判。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return value
    if value > MAX_CALL_TIMEOUT_SECONDS:
        return MAX_CALL_TIMEOUT_SECONDS
    return value


def _rec_provider_env() -> tuple:
    """识别腿 (base_url, api_key) 的 .env 成对解析。

    成对返回是为了避免 base 与 key 跨 provider 错配（取到 A 家地址 + B 家密钥必然 401）。
    优先级：
      1. DASHSCOPE_BASE_URL + DASHSCOPE_API_KEY（显式 DashScope）
      2. OPENAI_REC_BASE_URL + OPENAI_REC_API_KEY / OPENAI_API_KEY（显式识别腿覆盖）
      3. 仅有 DASHSCOPE_API_KEY → DashScope 官方 base（默认 provider）
      4. OPENAI_BASE_URL + OPENAI_API_KEY（历史兼容）
      5. 无任何密钥 → DashScope 官方 base 占位
    """
    ds_base = (os.environ.get("DASHSCOPE_BASE_URL") or "").strip()
    ds_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
    rec_base = (os.environ.get("OPENAI_REC_BASE_URL") or "").strip()
    rec_key = (os.environ.get("OPENAI_REC_API_KEY") or "").strip()
    oa_base = (os.environ.get("OPENAI_BASE_URL") or "").strip()
    oa_key = (os.environ.get("OPENAI_API_KEY") or "").strip()

    if ds_base:
        return ds_base, ds_key
    if rec_base:
        return rec_base, (rec_key or oa_key)
    if ds_key:
        return DASHSCOPE_DEFAULT_BASE_URL, ds_key
    if oa_base:
        return oa_base, oa_key
    return DASHSCOPE_DEFAULT_BASE_URL, ""


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # CLI 引擎弃用（2026-09-02）后删除的字段与枚举值。
    _REMOVED_CLI_FIELDS: ClassVar[tuple] = (
        "recognition_transport", "audit_transport", "parse_transport",
        "grey_recognition_transport", "grey_audit_transport", "grey_parse_transport",
    )
    # 已弃用 CLI 引擎的枚举字符串 -> 现行唯一通道 openai（判定走 is_legacy_cli_engine 单一实现）。
    _LEGACY_CLI_ENGINE_VALUES: ClassVar[tuple] = LEGACY_CLI_ENGINE_VALUES
    _ENGINE_FIELDS: ClassVar[tuple] = (
        "recognition_engine", "audit_engine", "parse_llm_engine",
        "grey_recognition_engine", "grey_audit_engine", "grey_parse_llm_engine",
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_removed_cli_fields(cls, data):
        """校验前统一消化「CLI 引擎时代的遗留形状」，使全部构造点都能加载旧数据。

        1) 摘掉已删除的 transport 系 6 个键 —— extra="forbid" 否则直接 ValidationError。
           这不是假想场景：guardian.perform_engine_rollback 会做
           `{**cfg.model_dump(), **prev}`，而 prev 是推全前保存的旧快照，必然带这些键，
           不摘掉就会「回滚配置重建失败」只留一行 warning，回滚静默失效。
        2) 把 6 个引擎字段里的 "opencode" / "codebuddy" 改写为 openai —— EngineKind 已
           删除这两个成员，存量 DB 与旧快照里却仍存有它们。

        放在模型层是唯一能覆盖全部构造点的地方（get_engine_config / api_admin ×4 /
        guardian 回滚 / 脚本裸构造），避免逐点去记得过滤（修 A 漏 B）。
        """
        if not isinstance(data, dict):
            return data
        out = {k: v for k, v in data.items() if k not in cls._REMOVED_CLI_FIELDS}
        for field in cls._ENGINE_FIELDS:
            if is_legacy_cli_engine(out.get(field)):
                out[field] = "openai"
        return out

    # 配置来源（T1）：auto=.env 启动装配，重启后按 .env 重新装配；manual=管理台手工保存，
    # 重启时 hydrate_engine_config_from_env 不得覆盖（否则人工切换活不过一次 uvicorn reload）。
    config_source: str = Field(
        default="auto",
        description="配置来源：auto=.env 启动装配（重启按 .env 重装）；manual=管理台手工保存（重启保留，仅密钥缺失到无法调用时才交由 .env 兜底补全）。",
    )

    # ---- 常规引擎配置 ----
    # 识别引擎（优先从 .env 读取）。用户决策（2026-09-02）：两个本机 CLI 引擎
    # （opencode / codebuddy）已弃用；2026-09-20 起连同 EngineKind 的枚举成员一并从
    # 代码中删除，现存唯一通道是 OpenAI 兼容（见 EngineKind 的说明）。
    # 用户决策（2026-09-15）：默认识别 provider = 阿里云百炼 DashScope（见文件头常量）。
    recognition_engine: EngineKind = Field(
        default_factory=lambda: EngineKind(os.environ.get("RECOGNITION_ENGINE", "openai").lower())
        if os.environ.get("RECOGNITION_ENGINE", "openai").lower() in [e.value for e in EngineKind]
        else EngineKind.OPENAI
    )
    recognition_model: str = Field(
        default_factory=lambda: env_model_or_default(
            os.environ.get("RECOGNITION_MODEL"), DASHSCOPE_DEFAULT_REC_MODEL),
        description="识别腿模型（生成器）。默认 DashScope qwen3.5-omni-flash（全模态，支持图片输入）。"
                    "注意模型名以 qwen 开头，若 recognition_engine 非 openai 会被判去 QwenChatModel 原生 SDK 通道。",
    )
    # 审核引擎
    # 生成器-评估器强制异构（Gap A3）：审核腿必须与识别腿跨厂商异构，禁止同源。
    # 用户决策（2026-08-30）：SiliconFlow 为默认 provider，审核腿默认 SF DeepSeek 系
    # （与识别腿 Qwen 系跨厂商异构，Gap A3）；用户决策（2026-09-02）本机 CLI 引擎
    # （opencode / codebuddy）已弃用并从代码与枚举中删除，审计腿一律走 OpenAI 兼容通道。
    audit_engine: EngineKind = Field(
        default_factory=lambda: EngineKind(os.environ.get("AUDIT_ENGINE", "openai").lower())
        if os.environ.get("AUDIT_ENGINE", "openai").lower() in [e.value for e in EngineKind]
        else EngineKind.OPENAI,
        description="审核引擎（评估器）。强制与识别腿跨厂商异构（Gap A3），禁止与识别腿同引擎同家族。",
    )
    audit_model: str = Field(
        default_factory=lambda: env_model_or_default(
            os.environ.get("SILICONFLOW_AUDIT_MODEL") or os.environ.get("AUDIT_MODEL"),
            "zai-org/GLM-4.5V"),
        description="审核腿模型（评估器）。默认 SF GLM-4.5V（视觉模型，支持 text/vlm/ondemand 三种审核模式），与识别腿 Qwen 系跨厂商异构；识别=GLM 系时审核必须换 Qwen/DeepSeek 系。审核调用 temperature 必须 0（llm._build 对 aud 侧强制）。灰测组 grey_audit_model 同此约束。",
    )
    audit_enabled: bool = True
    # 审核模式：text=纯文本确定性校验（不重读原图，毫秒级，默认）
    #           vlm=原图 + JSON 交叉审核（原行为）
    #           ondemand=置信度低于阈值才走 vlm，否则 text
    audit_mode: str = "text"
    # 常规自定义 OpenAI 兼容引擎（识别/审核各自独立参数，优先从 .env 读取）
    # 识别腿默认 DashScope（见 _rec_provider_env 成对解析）；审核腿默认仍为 SiliconFlow。
    openai_rec_base_url: str = Field(default_factory=lambda: _rec_provider_env()[0])
    openai_rec_api_key: str = Field(default_factory=lambda: _rec_provider_env()[1])
    openai_rec_model: str = Field(
        default_factory=lambda: env_model_or_default(
            os.environ.get("OPENAI_REC_MODEL"), DASHSCOPE_DEFAULT_REC_MODEL)
    )
    openai_aud_base_url: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_AUD_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    )
    openai_aud_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_AUD_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")))
    )
    openai_aud_model: str = Field(
        default_factory=lambda: env_model_or_default(
            os.environ.get("OPENAI_AUD_MODEL") or os.environ.get("SILICONFLOW_AUDIT_MODEL"),
            "zai-org/GLM-4.5V")
    )
    # 单引擎调用超时（秒）：超过即快速失败，取代 llm.py 写死的 240s。
    # T9（P8）：默认值 60 与 llm._resolve_timeout 的硬钳 min(v,60) 对齐——旧默认 90
    # 实际只生效 60，属误导；60 对 omni（实测单张 2.6-7.1s）结余充足且禁止长期空转。
    # 硬钳保留：无论管理台填多大，实际超时都不超过 MAX_CALL_TIMEOUT_SECONDS（60）。
    # 三处钳制（运行期 / hydrate 存量归一 / 保存归一）统一走 clamp_call_timeout。
    call_timeout_seconds: int = MAX_CALL_TIMEOUT_SECONDS
    # 整图识别重试上限（T11/P10）：识别失败或门禁不过时最多跑几轮（含首轮）。
    # 默认 3；与 call_timeout_seconds 同样不在契约层做范围钳制（管理台/DB 可直接改），
    # 由 supervisor.resolve_max_retry 在运行时钳制到 [1, MAX_RETRY_LIMIT]，
    # 避免 0（循环一轮不跑）或离谱大值（长期空转烧钱）。
    # 真正会消耗该额度的只有「引擎瞬时故障」（上游 5xx / 超时）这一类：
    # 门禁快速反馈、输出质量失败、确定性失败都已在 supervisor 内短路，不占额度。
    max_retry_rounds: int = Field(
        default=3,
        description="整图识别最大轮数（含首轮），默认 3，运行时钳制到 [1, 5]。"
                    "仅引擎瞬时故障会用到第 2/3 轮；快速反馈门禁、输出质量失败、确定性失败均短路不重跑。",
    )
    # 常规解析 LLM（VLM 识别后 → LLM 规范化解析，可选）
    parse_llm_enabled: bool = False
    parse_llm_engine: EngineKind = EngineKind.OPENAI
    parse_llm_model: str = "Qwen/Qwen3-VL-32B-Instruct"
    openai_parse_base_url: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_PARSE_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    )
    openai_parse_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_PARSE_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")))
    )
    openai_parse_model: str = Field(
        default_factory=lambda: env_model_or_default(
            os.environ.get("OPENAI_PARSE_MODEL") or os.environ.get("OPENAI_MODEL"), "")
    )

    # ---- 分组测试（灰测）配置：与常规完全隔离 ----
    grey_enabled: bool = False                       # 是否启用灰测
    grey_percent: int = 0                            # 随机分配概率 0-100
    grey_assign_mode: GreyAssignMode = GreyAssignMode.RECEIPT
    grey_supplier_ids: list[int] = []                # 灰测指定的供应商名单（ID allowlist）
    # 灰测组识别引擎/模型（默认 SF 非思考型，与常规腿同源不同参）
    grey_recognition_engine: EngineKind = EngineKind.OPENAI
    grey_recognition_model: str = "Qwen/Qwen3-VL-32B-Instruct"
    # 灰测组审核引擎/模型（与灰测识别腿跨厂商异构：GLM 系）
    grey_audit_enabled: bool = True
    grey_audit_engine: EngineKind = EngineKind.OPENAI
    grey_audit_model: str = "zai-org/GLM-4.5V"
    # 灰测组自定义 OpenAI 兼容参数（识别/审核各自独立）
    grey_openai_rec_base_url: str = ""
    grey_openai_rec_api_key: str = ""
    grey_openai_rec_model: str = ""
    grey_openai_aud_base_url: str = ""
    grey_openai_aud_api_key: str = ""
    grey_openai_aud_model: str = ""
    # 灰测组解析 LLM
    grey_parse_llm_enabled: bool = False
    grey_parse_llm_engine: EngineKind = EngineKind.OPENAI
    grey_parse_llm_model: str = "Qwen/Qwen3-VL-32B-Instruct"
    grey_openai_parse_base_url: str = ""
    grey_openai_parse_api_key: str = ""
    grey_openai_parse_model: str = ""
    # 推全回滚快照（一次：prev 为推全前常规组配置，meta 为操作信息）
    rollback_snapshot: Optional[dict] = None


def should_use_grey(cfg: "EngineConfig", supplier_name: str = "", supplier_id: Optional[int] = None) -> bool:
    """灰测分配：决定本单走常规还是灰测配置。

    - 未启用 / 概率 0 → 常规
    - grey_supplier_ids allowlist 非空时：优先判断 supplier_id 或 name 是否在白名单中
    - grey_assign_mode=receipt → 每单按 random() < percent% 独立判断
    - grey_assign_mode=supplier → 按供应商名 hash 落入 [0,100) 区间，同供应商一致命中
    """
    if not cfg.grey_enabled:
        return False
    # allowlist 白名单优先
    if cfg.grey_supplier_ids:
        if supplier_id is not None and int(supplier_id) in [int(x) for x in cfg.grey_supplier_ids]:
            return True
    if cfg.grey_percent <= 0:
        return False
    pct = max(0, min(100, int(cfg.grey_percent)))
    if cfg.grey_assign_mode == GreyAssignMode.SUPPLIER and supplier_name:
        import hashlib
        bucket = int(hashlib.md5(supplier_name.encode("utf-8")).hexdigest(), 16) % 100
        return bucket < pct
    import random
    return random.random() * 100 < pct


def now_iso():
    return datetime.now().isoformat(timespec="seconds")
