# -*- coding: utf-8 -*-
from __future__ import annotations
"""领域模型：Pydantic 契约 + SQLAlchemy 持久化。"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


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


# -------------------------------------------------------------
# 领域模型（SQLAlchemy）
# -------------------------------------------------------------
class Receipt(BaseModel):
    """收据记录（内存态 + 轻量持久化的简化版）。"""
    model_config = ConfigDict(extra="forbid")

    id: Optional[str] = None
    image_path: str = ""
    status: str = "uploaded"   # uploaded → parsing → parsed → edited → approved/flagged/error
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
    CODEBUDDY = "codebuddy"      # 本机 CodeBuddy CLI
    OPENCODE = "opencode"        # 本机 opencode CLI
    OPENAI = "openai"            # 自定义 OpenAI 兼容（base_url + api_key + model）


import os
from dotenv import load_dotenv
load_dotenv()


class EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # ---- 常规引擎配置 ----
    # 识别引擎（优先从 .env 读取）
    recognition_engine: EngineKind = Field(
        default_factory=lambda: EngineKind(os.environ.get("RECOGNITION_ENGINE", "opencode").lower())
        if os.environ.get("RECOGNITION_ENGINE", "opencode").lower() in [e.value for e in EngineKind]
        else EngineKind.OPENCODE
    )
    recognition_model: str = Field(
        default_factory=lambda: os.environ.get("OPENCODE_RECOGNITION_MODEL", os.environ.get("RECOGNITION_MODEL", "opencode/mimo-v2.5-free")),
        description="识别腿模型（生成器）。生产基线为 SiliconFlow Qwen/Qwen3-VL-32B-Thinking（openai 引擎）。",
    )
    # 识别 transport：subprocess(默认，CLI 快路径) | persistent(常驻进程，需显式开启)
    recognition_transport: str = "subprocess"
    # 审核引擎
    # 生成器-评估器强制异构（Gap A3）：审核腿必须与识别腿跨厂商异构，禁止同源。
    # 用户决策（2026-08-30）：SiliconFlow 为默认 provider，审核腿默认 SF DeepSeek 系
    # （与识别腿 Qwen 系跨厂商异构，Gap A3）；opencode 系仅作显式选择，不再作为默认。
    audit_engine: EngineKind = Field(
        default_factory=lambda: EngineKind(os.environ.get("AUDIT_ENGINE", "openai").lower())
        if os.environ.get("AUDIT_ENGINE", "openai").lower() in [e.value for e in EngineKind]
        else EngineKind.OPENAI,
        description="审核引擎（评估器）。强制与识别腿跨厂商异构（Gap A3），禁止与识别腿同引擎同家族。",
    )
    audit_model: str = Field(
        default_factory=lambda: os.environ.get("SILICONFLOW_AUDIT_MODEL", os.environ.get("AUDIT_MODEL", "zai-org/GLM-4.5V")),
        description="审核腿模型（评估器）。默认 SF GLM-4.5V（视觉模型，支持 text/vlm/ondemand 三种审核模式），与识别腿 Qwen 系跨厂商异构；识别=GLM 系时审核必须换 Qwen/DeepSeek 系。审核调用 temperature 必须 0（llm._build 对 aud 侧强制）。灰测组 grey_audit_model 同此约束。",
    )
    audit_enabled: bool = True
    # 审核模式：text=纯文本确定性校验（不重读原图，毫秒级，默认）
    #           vlm=原图 + JSON 交叉审核（原行为）
    #           ondemand=置信度低于阈值才走 vlm，否则 text
    audit_mode: str = "text"
    # 审核 transport：subprocess(默认) | persistent
    audit_transport: str = "subprocess"
    # 常规自定义 OpenAI 兼容引擎（识别/审核各自独立参数，优先从 .env 读取）
    openai_rec_base_url: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_REC_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    )
    openai_rec_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_REC_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")))
    )
    openai_rec_model: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_REC_MODEL", os.environ.get("OPENAI_MODEL", ""))
    )
    openai_aud_base_url: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_AUD_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    )
    openai_aud_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_AUD_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")))
    )
    openai_aud_model: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_AUD_MODEL", os.environ.get("SILICONFLOW_AUDIT_MODEL", "zai-org/GLM-4.5V"))
    )
    # 单引擎调用超时（秒）：超过即快速失败，取代 llm.py 写死的 240s
    call_timeout_seconds: int = 90
    # 常规解析 LLM（VLM 识别后 → LLM 规范化解析，可选）
    parse_llm_enabled: bool = False
    parse_llm_engine: EngineKind = EngineKind.OPENCODE
    parse_llm_model: str = "opencode/mimo-v2.5-free"
    openai_parse_base_url: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_PARSE_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    )
    openai_parse_api_key: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_PARSE_API_KEY", os.environ.get("OPENAI_API_KEY", os.environ.get("SILICONFLOW_API_KEY", "")))
    )
    openai_parse_model: str = Field(
        default_factory=lambda: os.environ.get("OPENAI_PARSE_MODEL", os.environ.get("OPENAI_MODEL", ""))
    )
    # 解析 LLM transport：subprocess(默认) | persistent
    parse_transport: str = "subprocess"

    # ---- 分组测试（灰测）配置：与常规完全隔离 ----
    grey_enabled: bool = False                       # 是否启用灰测
    grey_percent: int = 0                            # 随机分配概率 0-100
    grey_assign_mode: GreyAssignMode = GreyAssignMode.RECEIPT
    grey_supplier_ids: list[int] = []                # 灰测指定的供应商名单（ID allowlist）
    # 灰测组识别引擎/模型
    grey_recognition_engine: EngineKind = EngineKind.OPENCODE
    grey_recognition_model: str = "opencode/mimo-v2.5-free"
    grey_recognition_transport: str = "subprocess"
    # 灰测组审核引擎/模型
    grey_audit_enabled: bool = True
    grey_audit_engine: EngineKind = EngineKind.OPENCODE
    grey_audit_model: str = "opencode/mimo-v2.5-free"
    grey_audit_transport: str = "subprocess"
    # 灰测组自定义 OpenAI 兼容参数（识别/审核各自独立）
    grey_openai_rec_base_url: str = ""
    grey_openai_rec_api_key: str = ""
    grey_openai_rec_model: str = ""
    grey_openai_aud_base_url: str = ""
    grey_openai_aud_api_key: str = ""
    grey_openai_aud_model: str = ""
    # 灰测组解析 LLM
    grey_parse_llm_enabled: bool = False
    grey_parse_llm_engine: EngineKind = EngineKind.OPENCODE
    grey_parse_llm_model: str = "opencode/mimo-v2.5-free"
    grey_openai_parse_base_url: str = ""
    grey_openai_parse_api_key: str = ""
    grey_openai_parse_model: str = ""
    grey_parse_transport: str = "subprocess"
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
