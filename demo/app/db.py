# -*- coding: utf-8 -*-
"""持久化层：SQLite + SQLAlchemy 单库实现（DB_PATH，缺省 demo/receipt_demo.db）。

Infra 演进路径见 Gap 分析 E7：SQLite 单库为当前阶段的刻意取舍，
后续可平移至带迁移框架的独立数据库服务。

- 幂等列迁移：启动时按「先查再执行」追加缺失列/索引（仅 ADD COLUMN /
  CREATE INDEX，禁重建表）；vendor_memory / skus 两表检测到旧物理形态时
  做数据保全式标准重建（CREATE 新表 → 按列拷贝 → 校验行数 → DROP 旧表
  → RENAME，失败不阻断启动且任何失败路径不先删旧表）。
- 租户上下文（Gap E2）：get_session(tenant_id) 透传租户；scoped(query,
  model, tenant_id) 统一过滤（tenant_id 为 None/空不过滤，向后兼容内部
  脚本与既有测试路径）；_tenant_ok(row, tenant_id) 做按 id 取单条后的
  行级归属校验。业务主表（receipts / receipt_items / suppliers / skus /
  inventory_log / dishes / vendor_memory）带 tenant_id 列并建索引。

数据表：
- receipts（收据主表，含乐观锁 version / 付款字段）
- receipt_items（收据明细）
- inventory_log（进出流水：入库/消耗/损耗/盘点；库存主数据在
  skus.current_stock，无独立 inventory 表）
- suppliers（供应商）
- departments（部门）
- payments（付款登记）
- vendor_memory / app_settings / skus
- dishes / dish_ingredients（餐品与 BOM 配方）
- inventory_batches / daily_dish_consumptions / daily_consumption_details
  （FIFO 批次池与每日餐品消耗穿透明细）
- ai_decision_log / receipt_feedback 等履历与治理表
"""

import json
import logging
import math as _math
import os
import uuid
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "../receipt_demo.db"))

_engine = None
_SessionLocal = None

_ReceiptRow = _ItemRow = _SkuRow = _StockLogRow = _SupplierRow = None
_DeptRow = _PaymentRow = _VendorMemoryRow = _AppSettingRow = None
_DecisionLogRow = _SnapshotRow = _UserEventRow = _ExperimentRow = _ExperimentAssignRow = None
_ReceiptFeedbackRow = None
_PendingMemoryRow = None
_EvalCandidateRow = None
_GuardrailEventRow = None
_DishRow = _DishIngredientRow = _InventoryBatchRow = _DailyConsumptionRow = _DailyConsumptionDetailRow = None


def _make_engine():
    global _engine, _SessionLocal
    global _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow, _SupplierRow
    global _DeptRow, _PaymentRow, _VendorMemoryRow, _AppSettingRow
    global _DecisionLogRow, _SnapshotRow, _UserEventRow, _ExperimentRow, _ExperimentAssignRow
    global _ReceiptFeedbackRow
    global _PendingMemoryRow
    global _EvalCandidateRow
    global _GuardrailEventRow
    global _DishRow, _DishIngredientRow, _InventoryBatchRow, _DailyConsumptionRow, _DailyConsumptionDetailRow

    from sqlalchemy import create_engine, Column, String, Float, Integer, Text, DateTime, UniqueConstraint, Index
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.sql import func

    _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    Base = declarative_base()

    class ReceiptRow(Base):
        __tablename__ = "receipts"
        id = Column(Integer, primary_key=True, autoincrement=True)
        status = Column(String, default="uploaded")       # uploaded/parsing/parsed/edited/approved/flagged/error
        supplier_name = Column(String, default="")
        supplier_code = Column(String, default="")
        image_path = Column(String, default="")
        receipt_date = Column(String, default="")
        sheet_name = Column(String, default="")            # 月份，如 2026-07
        total_amount = Column(Float, default=0.0)
        settlement_type = Column(String, default="")       # cash/credit
        payment_mark = Column(String, default="")
        doc_form = Column(String, default="")
        layout_type = Column(String, default="")
        department_id = Column(Integer, nullable=True)
        version = Column(Integer, default=1)
        math_warnings_json = Column(Text, default="[]")
        quality_warnings_json = Column(Text, default="[]")
        review_priority_score = Column(Float, default=0.0)
        items_json = Column(Text, default="[]")            # 已解析明细（dict list）
        raw_llm = Column(Text, default="")                 # VLM 原始输出（可审计）
        audit_json = Column(Text, default="{}")
        confidence = Column(Float, default=0.0)
        ai_prefill_json = Column(Text, default="{}")       # AI 预填原始数据
        # 付款字段
        expected_pay_date = Column(String, default="")
        paid_at = Column(String, default="")
        paid_method = Column(String, default="")           # cash/bank_transfer/cheque/fps
        payment_id = Column(Integer, nullable=True)
        # 审计日志
        audit_logs_json = Column(Text, default="[]")
        use_grey = Column(Integer, default=0)              # 灰测组标记（阶段 1 持久化）
        rag_context_json = Column(Text, default="")        # RAG 检索上下文（data_only 调试开关可见）
        currency = Column(String, default="HKD")           # 多币种（F-P1-3）
        # Gap 9 / Gap 6 店员可修正字段（识别直出 + save_edited 覆写，双写同列）
        service_fee = Column(Float, default=0.0)           # 加一服务费
        tax_amount = Column(Float, default=0.0)            # 税额/VAT/GST
        adjustment_notes_json = Column(Text, default="[]") # 手写注记（拒收/短装/调整）list[str]
        payment_evidence = Column(Text, default="")        # 付款标记图面证据描述
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        deleted_at = Column(String, nullable=True)  # 软删除时间戳
        # Gap E2：租户隔离键（多租户贯穿业务主表）
        tenant_id = Column(String(64), default="default", index=True)

    class ItemRow(Base):
        __tablename__ = "receipt_items"
        id = Column(Integer, primary_key=True, autoincrement=True)
        receipt_id = Column(Integer, index=True)
        name = Column(String, default="")
        quantity = Column(Float, default=0.0)
        unit = Column(String, default="")
        unit_price = Column(Float, default=0.0)
        amount = Column(Float, default=0.0)
        sku_id = Column(Integer, nullable=True)
        cost_center_id = Column(Integer, nullable=True)     # 部门
        confidence = Column(Float, default=0.0)
        matched = Column(Integer, default=0)                # SKU 是否匹配
        price_anomaly = Column(Integer, default=0)
        price_anomaly_direction = Column(String, default="")
        price_diff_percent = Column(Float, default=0.0)
        unit_conversion_warning = Column(String, default="")
        raw_name = Column(String, default="")
        raw_unit = Column(String, default="")
        fuzzy_candidates_json = Column(Text, default="[]")
        entity_candidates_json = Column(Text, default="[]")
        is_void = Column(Integer, default=0)                # 划线作废（Gap 6 / D-P1-4）
        actual_qty = Column(Float, nullable=True)           # 手写实收数量（Gap 6）
        evidence_json = Column(Text, nullable=True)         # 字段级证据（Gap E1 / T7）：{page, bbox 归一化, raw_text}
        # Gap E2：租户隔离键（随所属单据）
        tenant_id = Column(String(64), default="default", index=True)

    class SkuRow(Base):
        __tablename__ = "skus"
        id = Column(Integer, primary_key=True, autoincrement=True)
        # 既有问题 #1（B）：name 去掉库级 unique=True —— 唯一性改按租户命名空间
        # （create_sku/update_sku 的 scoped 重名检查为唯一防重线）。旧库的
        # name 全局唯一物理约束（UNIQUE 索引）由下方迁移做数据保全式重建消除，
        # 否则跨租户同名 SKU 通过 scoped 检查后仍会在 INSERT 处 IntegrityError。
        name = Column(String, index=True)
        category = Column(String, default="")
        base_unit = Column(String, default="")
        min_stock_alert = Column(Float, default=0.0)
        current_stock = Column(Float, default=0.0)
        last_unit_price = Column(Float, default=0.0)
        sku_code = Column(String, default="")
        active = Column(Integer, default=1)
        # Gap E2：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

    class StockLogRow(Base):
        __tablename__ = "inventory_log"
        id = Column(Integer, primary_key=True, autoincrement=True)
        sku_id = Column(Integer, index=True, nullable=True)
        name = Column(String, default="")
        qty = Column(Float, default=0.0)
        unit = Column(String, default="")
        amount = Column(Float, default=0.0)
        vendor = Column(String, default="")
        date = Column(String, default="")
        receipt_id = Column(Integer, nullable=True)
        kind = Column(String, default="in")                # in/consume/waste/stocktake
        note = Column(String, default="")
        created_at = Column(String, default="")
        # Gap E2：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

    class SupplierRow(Base):
        __tablename__ = "suppliers"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, index=True, unique=True)
        supplier_code = Column(String, default="")
        active = Column(Integer, default=1)
        payment_terms_days = Column(Integer, nullable=True)
        settlement_pref = Column(String, default="")       # cash/credit/mixed
        contact_phone = Column(String, default="")
        notes = Column(String, default="")
        # Gap E2：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

    class DeptRow(Base):
        __tablename__ = "departments"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, default="")
        active = Column(Integer, default=1)

    class PaymentRow(Base):
        __tablename__ = "payments"
        id = Column(Integer, primary_key=True, autoincrement=True)
        supplier_id = Column(Integer, index=True)
        amount = Column(Float, default=0.0)
        method = Column(String, default="")                # cash/bank_transfer/cheque/fps
        paid_at = Column(String, default="")
        notes = Column(String, default="")
        voucher_image_path = Column(String, default="")
        linked_receipt_ids_json = Column(Text, default="[]")

    class VendorMemoryRow(Base):
        __tablename__ = "vendor_memory"
        # Gap B1（T3 演进）：每条记忆一行。T2 的 (vendor, tenant_id) 复合主键是
        # "一 vendor 一租户一行"语义，与追加式记忆写入冲突，改为 memory_id 主键
        # + vendor/tenant_id 普通索引列（隔离口径不变：查询一律按租户过滤）。
        # 已知折中：存量 SQLite 库物理主键无法变更（与 T2 相同），由幂等 ALTER
        # 补列 + memory_id 回填；新库/测试库由 create_all 建出正确 schema。
        memory_id = Column(String, primary_key=True)       # 每条记忆唯一 UUID
        vendor = Column(String, index=True)
        tenant_id = Column(String(64), default="default", index=True)
        notes = Column(Text, default="")
        sample = Column(Text, default="")
        # Gap B1 治理元数据
        # 本 Wave 为追加式写入，每行 version 初始 1；同行演进与 version 递增
        # 在 T8 记忆生命周期治理中落地 TODO(T8)
        version = Column(Integer, default=1)
        source_kind = Column(String(32), default="manual")  # approve|feedback_distilled|manual
        source_ref = Column(Text, default="[]")            # 触发来源 receipt_id 列表 JSON
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        hit_count = Column(Integer, default=0)
        last_hit_at = Column(String, default="")
        decay_score = Column(Float, default=1.0)           # T8 消费：采纳+0.05/覆写-0.12
        status = Column(String(16), default="active")      # active|archived

    class AppSettingRow(Base):
        __tablename__ = "app_settings"
        key = Column(String, primary_key=True)
        value = Column(Text, default="")

    # -------------------------------------------------------------
    # 阶段 2：AI 产品指标 / 漏斗 / A/B 实验
    # -------------------------------------------------------------
    class DecisionLogRow(Base):
        """每次 AI 决策一行：extract / audit / review。

        字段对齐 `docs/AI产品演进路线图.md` 存储模型：
        ai_decision_log(receipt_id, supplier_id, experiment_id, grp,
                        engine, model, use_grey, decision_type, field_path,
                        ai_value, user_value, adopted, confidence,
                        is_hallucination, extra)
        采纳判定必须等 approve 时回填（用户可能改了又改），所以 user_value/
        adopted/is_hallucination 三列允许 NULL。
        """
        __tablename__ = "ai_decision_log"
        id = Column(Integer, primary_key=True, autoincrement=True)
        ts = Column(String, default="")
        receipt_id = Column(Integer, index=True, nullable=True)
        supplier_id = Column(Integer, index=True, nullable=True)
        experiment_id = Column(Integer, index=True, nullable=True)
        grp = Column(String, default="control")   # 'control' | 'treatment'
        engine = Column(String, default="")
        model = Column(String, default="")
        use_grey = Column(Integer, default=0)
        decision_type = Column(String, default="extract")  # extract|parse|audit|review
        field_path = Column(String, default="")            # items[0].unit_price
        ai_value = Column(Text, default="")
        user_value = Column(Text, nullable=True)
        adopted = Column(Integer, nullable=True)       # 0|1|null
        confidence = Column(Float, nullable=True)
        is_hallucination = Column(Integer, nullable=True)  # 0|1|null
        extra = Column(Text, default="")

    class SnapshotRow(Base):
        """按时间窗+粒度聚合的 AI 指标快照，避免仪表盘现算全表。

        granularity: global|supplier|engine|model|experiment
        sample_size < 30 → 仪表盘标灰。
        """
        __tablename__ = "ai_metric_snapshot"
        id = Column(Integer, primary_key=True, autoincrement=True)
        period_start = Column(String, default="")
        period_end = Column(String, default="")
        granularity = Column(String, default="global")
        granularity_id = Column(String, default="")
        grp = Column(String, nullable=True)
        accuracy = Column(Float, nullable=True)
        hallucination_rate = Column(Float, nullable=True)
        edit_rate = Column(Float, nullable=True)
        audit_adoption_rate = Column(Float, nullable=True)
        trust_score = Column(Float, nullable=True)
        sample_size = Column(Integer, default=0)
        # T9（Gap D4）方向性约束指标：不直接触发回滚，连续同向漂移只告警
        avg_output_tokens = Column(Float, nullable=True)
        avg_tool_calls = Column(Float, nullable=True)
        avg_retry_rounds = Column(Float, nullable=True)
        computed_at = Column(String, default="")

    class UserEventRow(Base):
        """前端埋点事件。用于漏斗查询（upload → parse_done → edit → ...）。

        properties: JSON dict；grp: 'control'|'treatment'（实验分组）。
        """
        __tablename__ = "user_event"
        id = Column(Integer, primary_key=True, autoincrement=True)
        ts = Column(String, default="")
        account_id = Column(String, default="")
        session_id = Column(String, default="")
        event_type = Column(String, index=True, default="")
        receipt_id = Column(Integer, index=True, nullable=True)
        properties = Column(Text, default="{}")
        grp = Column(String, nullable=True)
        tenant_id = Column(String(64), default="default", index=True)

    class ExperimentRow(Base):
        """A/B 实验主体。状态：draft→running→stopped/concluded。"""
        __tablename__ = "experiment"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, default="")
        hypothesis = Column(Text, default="")
        success_metric = Column(String, default="accuracy")
        guardrail_metrics = Column(Text, default="[]")
        status = Column(String, default="draft")     # draft|running|stopped|concluded
        grey_snapshot = Column(Text, default="{}")
        target_percent = Column(Integer, default=0)
        target_supplier_ids = Column(Text, default="[]")
        start_ts = Column(String, nullable=True)
        end_ts = Column(String, nullable=True)
        min_sample = Column(Integer, default=100)
        conclusion = Column(String, nullable=True)           # promote|rollback|inconclusive
        conclusion_reason = Column(Text, nullable=True)
        concluded_by = Column(String, nullable=True)
        concluded_at = Column(String, nullable=True)

    class ExperimentAssignRow(Base):
        """实验—单据 归属（复合主键）。"""
        __tablename__ = "experiment_assignment"
        experiment_id = Column(Integer, primary_key=True)
        receipt_id = Column(Integer, primary_key=True)
        grp = Column(String, default="control")
        assigned_at = Column(String, default="")

    class GuardrailEventRow(Base):
        """T9（Gap C3）：实验守护动作留痕。

        action: rollback | freeze | alert；metrics_json 记触发时指标快照，可回溯。
        """
        __tablename__ = "guardrail_event"
        id = Column(Integer, primary_key=True, autoincrement=True)
        ts = Column(String, default="")
        experiment_id = Column(Integer, index=True, nullable=True)
        action = Column(String(24), default="")   # rollback|freeze|alert
        reason = Column(Text, default="")
        metrics_json = Column(Text, default="{}")

    # -------------------------------------------------------------
    # FR-8/FR-9 反馈飞轮：receipt_feedback
    # like: 1=点赞, -1=点踩, 0=未表态；item_index: None=整单, 数字=明细行
    # tenant_id 强制隔离，vendor 快照用于 FR-9 三次连续提炼
    # -------------------------------------------------------------
    class ReceiptFeedbackRow(Base):
        """收据反馈表：逐行/整单点赞点踩 + 文本反馈，支撑 FR-8/FR-9 飞轮。"""
        __tablename__ = "receipt_feedback"
        id = Column(Integer, primary_key=True, autoincrement=True)
        receipt_id = Column(Integer, index=True, nullable=False)
        item_index = Column(Integer, nullable=True)
        like = Column(Integer, nullable=True)          # 1=点赞 -1=点踩
        comment = Column(Text, default="")
        quality_warnings_json = Column(Text, default="[]")
        tenant_id = Column(String, default="default", index=True)
        vendor = Column(String, default="")
        created_at = Column(String, default="")
        updated_at = Column(String, default="")

    # -------------------------------------------------------------
    # T8（Gap D1）：待确认记忆队列 —— 反馈蒸馏产物不再自动生效，
    # 先入队，经人工 approve 才走既有 upsert 链路落 vendor_memory + 向量库。
    # -------------------------------------------------------------
    class PendingMemoryRow(Base):
        __tablename__ = "pending_memory"
        id = Column(Integer, primary_key=True, autoincrement=True)
        tenant_id = Column(String(64), default="default", index=True)
        vendor = Column(String, index=True, default="")
        content = Column(Text, default="")
        source_kind = Column(String(32), default="feedback_distilled")
        source_receipt_ids_json = Column(Text, default="[]")  # 触发的单据 id 列表 JSON（可回溯）
        status = Column(String(16), default="pending", index=True)  # pending|approved|rejected
        created_at = Column(String, default="")
        reviewed_by = Column(String, nullable=True)
        reviewed_at = Column(String, nullable=True)

    # -------------------------------------------------------------
    # 餐品管理与 FIFO 批次库存
    # -------------------------------------------------------------
    class DishRow(Base):
        """餐品主表：名称、品类、售价、状态。"""
        __tablename__ = "dishes"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, index=True, default="")
        category = Column(String, default="")
        price = Column(Float, default=0.0)
        description = Column(String, default="")
        status = Column(String, default="active")          # active/inactive
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        # Gap E2：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

    class DishIngredientRow(Base):
        """餐品 BOM 配方明细表（带 dish_id, sku_id 唯一约束）。"""
        __tablename__ = "dish_ingredients"
        id = Column(Integer, primary_key=True, autoincrement=True)
        dish_id = Column(Integer, index=True, nullable=False)
        sku_id = Column(Integer, index=True, nullable=False)
        consumption_qty = Column(Float, default=0.0)
        unit = Column(String, default="")
        notes = Column(String, default="")
        __table_args__ = (UniqueConstraint("dish_id", "sku_id"),)

    class EvalCandidateRow(Base):
        """线上低置信样本回流候选（Gap A5 / T6）。

        四类触发：low_confidence（置信度低于阈值）/ gate_reject（门禁拒绝）/
        user_edit（人工保存与 AI 预填差异）/ audit_discrepancy（交叉审核分歧）。
        每单每 reason 幂等（create_eval_candidate 查重）；status 流转：
        pending -> promoted_to_val | promoted_to_test | rejected。
        ai_candidate_json 存 AI 候选 GT（供 promote 复制进评测集）。
        """
        __tablename__ = "eval_candidate"
        id = Column(Integer, primary_key=True, autoincrement=True)
        receipt_id = Column(Integer, index=True)
        tenant_id = Column(String(64), default="default", index=True)
        doc_form = Column(String, default="")
        confidence = Column(Float, nullable=True)
        reason = Column(String(32), default="")   # low_confidence|gate_reject|user_edit|audit_discrepancy
        status = Column(String(24), default="pending")  # pending|promoted_to_val|promoted_to_test|rejected
        created_at = Column(String, default="")
        promoted_at = Column(String, nullable=True)
        ai_candidate_json = Column(Text, default="{}")
        note = Column(Text, default="")

    class InventoryBatchRow(Base):
        """库存批次池（FIFO 成本溯源）。"""
        __tablename__ = "inventory_batches"
        id = Column(Integer, primary_key=True, autoincrement=True)
        sku_id = Column(Integer, index=True, nullable=False)
        receipt_id = Column(Integer, nullable=True)
        inbound_date = Column(String, default="")
        unit_cost = Column(Float, default=0.0)
        initial_qty = Column(Float, default=0.0)
        remaining_qty = Column(Float, default=0.0)
        unit = Column(String, default="")
        is_closed = Column(Integer, default=0)             # 0=open, 1=closed/exhausted
        is_estimated = Column(Integer, default=0)          # 0=actual, 1=estimated
        created_at = Column(String, default="")
        __table_args__ = (Index("idx_sku_batch", "sku_id", "is_closed", "inbound_date", "id"),)

    class DailyConsumptionRow(Base):
        """每日餐品消耗主表。"""
        __tablename__ = "daily_dish_consumptions"
        id = Column(Integer, primary_key=True, autoincrement=True)
        date = Column(String, index=True, default="")
        dish_id = Column(Integer, index=True, nullable=False)
        quantity = Column(Float, default=0.0)
        total_cost = Column(Float, default=0.0)
        unit_cost = Column(Float, default=0.0)
        notes = Column(String, default="")
        is_void = Column(Integer, default=0)               # 0=normal, 1=voided
        created_at = Column(String, default="")

    class DailyConsumptionDetailRow(Base):
        """每日餐品消耗食材 FIFO 批次扣减穿透溯源明细。"""
        __tablename__ = "daily_consumption_details"
        id = Column(Integer, primary_key=True, autoincrement=True)
        consumption_id = Column(Integer, index=True, nullable=False)
        sku_id = Column(Integer, index=True, nullable=False)
        qty_consumed = Column(Float, default=0.0)
        unit = Column(String, default="")
        unit_cost = Column(Float, default=0.0)
        total_cost = Column(Float, default=0.0)
        batch_id = Column(Integer, nullable=True)
        batch_date = Column(String, default="")

    Base.metadata.create_all(_engine)

    # SQLite 迁移：给 receipts 表补 use_grey 列（阶段 1：AI 可见性与信任）
    # R5 静默幂等：先查再执行 —— 列已存在静默跳过（零日志）；仅真实异常
    # （DB locked 等故障可被观测）才 logger.warning，且不阻断启动。
    _apply_migration_ddl(
        _engine,
        "ALTER TABLE receipts ADD COLUMN use_grey INTEGER DEFAULT 0",
    )
    # SQLite 迁移：RAG 上下文 + 多币种（P1 治理）
    for _ddl in (
        "ALTER TABLE receipts ADD COLUMN rag_context_json TEXT DEFAULT ''",
        "ALTER TABLE receipts ADD COLUMN currency VARCHAR(8) DEFAULT 'HKD'",
        "ALTER TABLE receipts ADD COLUMN service_fee REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN tax_amount REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN adjustment_notes_json TEXT DEFAULT '[]'",
        "ALTER TABLE receipts ADD COLUMN payment_evidence TEXT DEFAULT ''",
        "ALTER TABLE receipt_items ADD COLUMN is_void INTEGER DEFAULT 0",
        "ALTER TABLE receipt_items ADD COLUMN actual_qty REAL",
        # Gap E1 / T7：字段级证据列（可空；旧行无证据为 NULL，向后兼容）
        "ALTER TABLE receipt_items ADD COLUMN evidence_json TEXT",
    ):
        _apply_migration_ddl(_engine, _ddl)
    # SQLite 迁移（Gap E2 租户隔离）：7 张业务主表补 tenant_id 列并建索引。
    # 幂等：先查再执行 —— 列/索引已存在则静默 continue（零日志）；仅 ADD
    # COLUMN / CREATE INDEX，禁重建表。注意：存量行经 DEFAULT 'default' 归入
    # 默认租户，历史行为向后兼容。
    for _ddl in (
        "ALTER TABLE receipts ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE receipt_items ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE suppliers ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE skus ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE inventory_log ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE dishes ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE vendor_memory ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE user_event ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "CREATE INDEX IF NOT EXISTS idx_receipts_tenant_id ON receipts (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_receipt_items_tenant_id ON receipt_items (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_suppliers_tenant_id ON suppliers (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_skus_tenant_id ON skus (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_inventory_log_tenant_id ON inventory_log (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_dishes_tenant_id ON dishes (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_memory_tenant_id ON vendor_memory (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_user_event_tenant_id ON user_event (tenant_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（Gap B1 记忆治理元数据，T3）：vendor_memory 追加 10 列。
    # 幂等写法与上同（先查再执行，列已存在静默跳过零日志）；仅 ADD COLUMN，
    # 禁重建表。已知折中：存量库物理主键无法变更，memory_id 由下方回填补齐。
    for _ddl in (
        "ALTER TABLE vendor_memory ADD COLUMN memory_id TEXT",
        "ALTER TABLE vendor_memory ADD COLUMN version INTEGER DEFAULT 1",
        "ALTER TABLE vendor_memory ADD COLUMN source_kind VARCHAR(32) DEFAULT 'manual'",
        "ALTER TABLE vendor_memory ADD COLUMN source_ref TEXT DEFAULT '[]'",
        "ALTER TABLE vendor_memory ADD COLUMN created_at TEXT DEFAULT ''",
        "ALTER TABLE vendor_memory ADD COLUMN updated_at TEXT DEFAULT ''",
        "ALTER TABLE vendor_memory ADD COLUMN hit_count INTEGER DEFAULT 0",
        "ALTER TABLE vendor_memory ADD COLUMN last_hit_at TEXT DEFAULT ''",
        "ALTER TABLE vendor_memory ADD COLUMN decay_score REAL DEFAULT 1.0",
        "ALTER TABLE vendor_memory ADD COLUMN status VARCHAR(16) DEFAULT 'active'",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（Gap A5 线上样本回流，T6）：eval_candidate 新表。
    # 幂等 CREATE TABLE IF NOT EXISTS 走既有迁移块（create_all 对新表同样幂等，
    # 此处显式 DDL 兜底覆盖裸库场景）；仅新建表/索引，禁重建表。
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS eval_candidate ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " receipt_id INTEGER,"
        " tenant_id VARCHAR(64) DEFAULT 'default',"
        " doc_form VARCHAR DEFAULT '',"
        " confidence FLOAT,"
        " reason VARCHAR(32) DEFAULT '',"
        " status VARCHAR(24) DEFAULT 'pending',"
        " created_at TEXT DEFAULT '',"
        " promoted_at TEXT,"
        " ai_candidate_json TEXT DEFAULT '{}',"
        " note TEXT DEFAULT '')",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_eval_candidate_receipt_reason"
        " ON eval_candidate (receipt_id, reason)",
        "CREATE INDEX IF NOT EXISTS idx_eval_candidate_status_reason"
        " ON eval_candidate (status, reason)",
        "CREATE INDEX IF NOT EXISTS idx_eval_candidate_tenant_id"
        " ON eval_candidate (tenant_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（T8 Gap D1 记忆写入人工闸）：pending_memory 新表。
    # 幂等 CREATE TABLE IF NOT EXISTS（与 eval_candidate 同款兜底），禁重建表。
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS pending_memory ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " tenant_id VARCHAR(64) DEFAULT 'default',"
        " vendor VARCHAR DEFAULT '',"
        " content TEXT DEFAULT '',"
        " source_kind VARCHAR(32) DEFAULT 'feedback_distilled',"
        " source_receipt_ids_json TEXT DEFAULT '[]',"
        " status VARCHAR(16) DEFAULT 'pending',"
        " created_at TEXT DEFAULT '',"
        " reviewed_by VARCHAR,"
        " reviewed_at TEXT)",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_tenant_id"
        " ON pending_memory (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_status"
        " ON pending_memory (status)",
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_vendor"
        " ON pending_memory (vendor)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（T9 Gap C3+D4 实验守护）：
    # ai_metric_snapshot 追加 3 个方向性列 + guardrail_event 新表（均幂等）。
    for _ddl in (
        "ALTER TABLE ai_metric_snapshot ADD COLUMN avg_output_tokens FLOAT",
        "ALTER TABLE ai_metric_snapshot ADD COLUMN avg_tool_calls FLOAT",
        "ALTER TABLE ai_metric_snapshot ADD COLUMN avg_retry_rounds FLOAT",
    ):
        _apply_migration_ddl(_engine, _ddl)
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS guardrail_event ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " ts TEXT DEFAULT '',"
        " experiment_id INTEGER,"
        " action VARCHAR(24) DEFAULT '',"
        " reason TEXT DEFAULT '',"
        " metrics_json TEXT DEFAULT '{}')",
    )
    _apply_migration_ddl(
        _engine,
        "CREATE INDEX IF NOT EXISTS idx_guardrail_event_experiment_id"
        " ON guardrail_event (experiment_id)",
    )

    # memory_id 回填：存量行补 UUID（SQLite ADD COLUMN 无法使用非常量默认值）
    try:
        from sqlalchemy import text as _sa_text5
        with _engine.connect() as _c:
            _rows = _c.execute(_sa_text5(
                "SELECT rowid FROM vendor_memory "
                "WHERE memory_id IS NULL OR memory_id = ''"
            )).fetchall()
            for _r in _rows:
                _c.execute(_sa_text5(
                    "UPDATE vendor_memory SET memory_id = :mid WHERE rowid = :rid"
                ), {"mid": uuid.uuid4().hex, "rid": _r[0]})
            _c.commit()
    except Exception as _e:
        logger.warning("[migrate] vendor_memory memory_id backfill skipped: %s", _e)

    # 索引：memory_id 唯一约束 + vendor 查询索引（先查再执行幂等，放在回填之后）
    for _ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_memory_memory_id ON vendor_memory (memory_id)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_memory_vendor ON vendor_memory (vendor)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # -----------------------------------------------------------------
    # 遗留物理形态重建（本轮单独授权，仅限 vendor_memory / skus 两表）：
    # SQLite 无法 ALTER 主键/唯一约束，检测到旧物理形态时做数据保全式标准
    # 重建（CREATE 新表 → 按列拷贝 → 校验行数 → DROP 旧表 → RENAME，最后补索引）。
    # 铁律：新形态库零动作；拷贝保全全部列（缺失列按默认值填充）；失败只告警
    # 不阻断启动，且任何失败路径都不得先删旧表。
    # -----------------------------------------------------------------
    _rebuild_legacy_vendor_memory(_engine)
    _rebuild_legacy_skus_name_unique(_engine)

    _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow = ReceiptRow, ItemRow, SkuRow, StockLogRow
    _SupplierRow, _DeptRow, _PaymentRow = SupplierRow, DeptRow, PaymentRow
    _VendorMemoryRow, _AppSettingRow = VendorMemoryRow, AppSettingRow
    _DecisionLogRow = DecisionLogRow
    _SnapshotRow = SnapshotRow
    _UserEventRow = UserEventRow
    _ExperimentRow = ExperimentRow
    _ExperimentAssignRow = ExperimentAssignRow
    _ReceiptFeedbackRow = ReceiptFeedbackRow
    _PendingMemoryRow = PendingMemoryRow
    _EvalCandidateRow = EvalCandidateRow
    _GuardrailEventRow = GuardrailEventRow
    _DishRow, _DishIngredientRow = DishRow, DishIngredientRow
    _InventoryBatchRow = InventoryBatchRow
    _DailyConsumptionRow, _DailyConsumptionDetailRow = DailyConsumptionRow, DailyConsumptionDetailRow
    return Base


# -----------------------------------------------------------------
# 遗留物理形态检测与数据保全式重建（仅限 vendor_memory / skus，单独授权）。
# 通用纪律：
# - 仅当检测到旧物理形态才动手，新形态库零动作零异常（幂等，二次 import 零动作）
# - 顺序：CREATE 新表 → 按列拷贝（缺失列默认值/NULL memory_id 用 Python UUID 回填）
#   → 行数校验 → DROP 旧表 → RENAME —— 任何失败都发生在 drop 之前，旧表不丢
# - 失败 logger.warning，不阻断启动
# -----------------------------------------------------------------

def _table_columns(conn, table):
    """PRAGMA table_info 的列名集合。"""
    from sqlalchemy import text as _t
    return {r[1] for r in conn.execute(_t(f"PRAGMA table_info({table})")).fetchall()}


def _table_pk_columns(conn, table):
    """PRAGMA table_info 的主键列名列表（pk 位置序）。"""
    from sqlalchemy import text as _t
    pks = [r for r in conn.execute(_t(f"PRAGMA table_info({table})")).fetchall() if r[5] > 0]
    pks.sort(key=lambda r: r[5])
    return [r[1] for r in pks]


def _table_unique_index_columns(conn, table):
    """返回覆盖各唯一索引的列集合列表（不依赖 origin/partial 等高版本字段）。"""
    from sqlalchemy import text as _t
    out = []
    for idx in conn.execute(_t(f"PRAGMA index_list({table})")).fetchall():
        idx_name, is_unique = idx[1], idx[2]
        if not is_unique:
            continue
        cols = [r[2] for r in
                conn.execute(_t(f"PRAGMA index_info({idx_name})")).fetchall()]
        out.append(cols)
    return out


def _column_exists(conn, table, column):
    """PRAGMA table_info 判断列是否已存在（迁移先查再执行用）。"""
    from sqlalchemy import text as _t
    try:
        rows = conn.execute(_t(f"PRAGMA table_info({table})")).fetchall()
    except Exception:
        return False
    return any(r[1] == column for r in rows)


def _index_exists(conn, table, index_name):
    """PRAGMA index_list 判断索引是否已存在（迁移先查再执行用）。"""
    from sqlalchemy import text as _t
    try:
        rows = conn.execute(_t(f"PRAGMA index_list({table})")).fetchall()
    except Exception:
        return False
    return any(r[1] == index_name for r in rows)


def _migration_ddl_already_applied(conn, ddl):
    """先查再执行：解析单条 ALTER ADD COLUMN / CREATE INDEX 迁移 DDL，
    目标列/索引已存在返回 True（调用方静默跳过，不产生任何日志）。"""
    import re as _re
    s = " ".join((ddl or "").split())
    m = _re.match(
        r"(?i)^ALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+(\S+)", s)
    if m:
        return _column_exists(conn, m.group(1), m.group(2))
    m = _re.match(
        r"(?i)^CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?"
        r"(\S+)\s+ON\s+(\S+)", s)
    if m:
        return _index_exists(conn, m.group(2), m.group(1))
    return False


def _apply_migration_ddl(engine, ddl):
    """单条迁移 DDL 执行器（R5 静默幂等口径）：

    - 先查再执行：列/索引已存在则静默 continue（零日志，二次 import 干净）；
    - 仅真实异常（DB locked 等）才 logger.warning 留痕，且不阻断启动。
    """
    from sqlalchemy import text as _t
    try:
        with engine.connect() as conn:
            if _migration_ddl_already_applied(conn, ddl):
                return  # 已存在：静默跳过，无日志
            conn.execute(_t(ddl))
            conn.commit()
    except Exception as e:
        logger.warning("[migrate] %s skipped: %s", ddl, e)


def _drop_rebuild_tmp_table(engine, tmp):
    """失败路径清理 __rebuild 临时表（尽力而为：清理失败仅告警，不再抛出）。

    SQLite DDL 不随 engine.begin() 的事务回滚（pysqlite 隐式提交语义），
    拷贝阶段失败时已 CREATE 的临时表会残留，必须显式 DROP。
    """
    try:
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
    except Exception as cleanup_err:
        logger.warning("[migrate] %s cleanup skipped: %s", tmp, cleanup_err)


def _rebuild_legacy_vendor_memory(engine):
    """Gap 遗留 #1（A）：vendor_memory 旧库物理主键重建。

    旧形态（pre-T2 vendor 单键 / T2 (vendor, tenant_id) 复合键 / ALTER 后
    memory_id 非主键）上同 vendor 追加第二条记忆会撞主键 IntegrityError。
    检测：memory_id 不是唯一主键列即重建为 ORM 当前形态（memory_id 主键）。
    """
    table, tmp = "vendor_memory", "vendor_memory__rebuild"
    try:
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            cols = _table_columns(conn, table)
            if not cols:
                return  # 表不存在（全新库由 create_all 建出正确形态）
            if _table_pk_columns(conn, table) == ["memory_id"]:
                return  # 已是新形态：零动作
            target_cols = [
                ("memory_id", None), ("vendor", None), ("tenant_id", "'default'"),
                ("notes", "''"), ("sample", "''"), ("version", "1"),
                ("source_kind", "'manual'"), ("source_ref", "'[]'"),
                ("created_at", "''"), ("updated_at", "''"),
                ("hit_count", "0"), ("last_hit_at", "''"),
                ("decay_score", "1.0"), ("status", "'active'"),
            ]
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
            conn.execute(_t(
                f"CREATE TABLE {tmp} ("
                " memory_id VARCHAR NOT NULL, vendor VARCHAR,"
                " tenant_id VARCHAR(64), notes TEXT, sample TEXT,"
                " version INTEGER, source_kind VARCHAR(32), source_ref TEXT,"
                " created_at TEXT, updated_at TEXT, hit_count INTEGER,"
                " last_hit_at TEXT, decay_score FLOAT, status VARCHAR(16),"
                " PRIMARY KEY (memory_id))"
            ))
            col_sql = ", ".join(c for c, _d in target_cols)
            # 拷贝 + 回填（Python 逐行）：NULL/空 memory_id 用 UUID 回填；
            # 旧表缺失的列按默认值填充，保全全部既有数据
            old_rows = conn.execute(_t(f"SELECT rowid, * FROM {table}")).mappings().all()
            insert_sql = f"INSERT INTO {tmp} ({col_sql}) VALUES ({', '.join(':' + c for c, _d in target_cols)})"
            payload = []
            for row in old_rows:
                rec = {}
                for col, default in target_cols:
                    if col in cols:
                        rec[col] = row[col]
                    else:  # 缺失列按默认值填充
                        rec[col] = {"tenant_id": "default", "version": 1,
                                    "source_kind": "manual", "source_ref": "[]",
                                    "hit_count": 0, "decay_score": 1.0,
                                    "status": "active"}.get(col, "")
                if rec.get("memory_id") is None or str(rec.get("memory_id") or "") == "":
                    rec["memory_id"] = uuid.uuid4().hex
                payload.append(rec)
            if payload:
                conn.execute(_t(insert_sql), payload)
            new_cnt = conn.execute(_t(f"SELECT COUNT(*) FROM {tmp}")).scalar()
            if int(new_cnt or 0) != len(old_rows):
                raise RuntimeError(
                    f"row count mismatch old={len(old_rows)} new={new_cnt}")
            # 拷贝完成且校验通过后才允许 drop 旧表
            conn.execute(_t(f"DROP TABLE {table}"))
            conn.execute(_t(f"ALTER TABLE {tmp} RENAME TO {table}"))
        logger.warning(
            "[migrate] vendor_memory legacy primary key rebuilt "
            "(rows=%s) -> memory_id PK", len(payload))
        for ddl in (
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_memory_memory_id"
            " ON vendor_memory (memory_id)",
            "CREATE INDEX IF NOT EXISTS idx_vendor_memory_vendor ON vendor_memory (vendor)",
            "CREATE INDEX IF NOT EXISTS idx_vendor_memory_tenant_id ON vendor_memory (tenant_id)",
            "CREATE INDEX IF NOT EXISTS ix_vendor_memory_vendor ON vendor_memory (vendor)",
            "CREATE INDEX IF NOT EXISTS ix_vendor_memory_tenant_id ON vendor_memory (tenant_id)",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(_t(ddl))
            except Exception as e:
                logger.warning("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        logger.warning("[migrate] vendor_memory legacy rebuild skipped: %s", e)
        _drop_rebuild_tmp_table(engine, tmp)


def _rebuild_legacy_skus_name_unique(engine):
    """既有问题 #1（B）：skus.name 全局 UNIQUE 约束改按租户命名空间。

    旧形态（建表 SQL 含 name UNIQUE 列约束或存在 name 上的唯一索引，
    如 create_all 生成的 ix_skus_name UNIQUE）上跨租户同名 SKU 会
    IntegrityError。检测到 name 唯一索引即数据保全式重建为非唯一形态；
    应用层 scoped 重名检查成为唯一防重线。
    """
    table, tmp = "skus", "skus__rebuild"
    try:
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            cols = _table_columns(conn, table)
            if not cols:
                return  # 表不存在（全新库由 create_all 建出正确形态）
            has_unique_name = any(c == ["name"] for c in
                                  _table_unique_index_columns(conn, table))
            if not has_unique_name:
                return  # 已是新形态：零动作
            target_cols = [
                ("id", None), ("name", "''"), ("category", "''"),
                ("base_unit", "''"), ("min_stock_alert", "0"),
                ("current_stock", "0"), ("last_unit_price", "0"),
                ("sku_code", "''"), ("active", "1"), ("tenant_id", "'default'"),
            ]
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
            conn.execute(_t(
                f"CREATE TABLE {tmp} ("
                " id INTEGER NOT NULL, name VARCHAR, category VARCHAR,"
                " base_unit VARCHAR, min_stock_alert FLOAT, current_stock FLOAT,"
                " last_unit_price FLOAT, sku_code VARCHAR, active INTEGER,"
                " tenant_id VARCHAR(64), PRIMARY KEY (id))"
            ))
            select_exprs = []
            for col, default in target_cols:
                select_exprs.append(f'"{col}"' if col in cols else default)
            conn.execute(_t(
                f"INSERT INTO {tmp} ({', '.join(c for c, _d in target_cols)}) "
                f"SELECT {', '.join(select_exprs)} FROM {table}"
            ))
            new_cnt = conn.execute(_t(f"SELECT COUNT(*) FROM {tmp}")).scalar()
            old_cnt = conn.execute(_t(f"SELECT COUNT(*) FROM {table}")).scalar()
            if int(new_cnt or 0) != int(old_cnt or 0):
                raise RuntimeError(
                    f"row count mismatch old={old_cnt} new={new_cnt}")
            # 拷贝完成且校验通过后才允许 drop 旧表
            conn.execute(_t(f"DROP TABLE {table}"))
            conn.execute(_t(f"ALTER TABLE {tmp} RENAME TO {table}"))
        logger.warning(
            "[migrate] skus legacy global UNIQUE(name) rebuilt to per-tenant "
            "namespace (rows=%s)", new_cnt)
        for ddl in (
            "CREATE INDEX IF NOT EXISTS ix_skus_name ON skus (name)",
            "CREATE INDEX IF NOT EXISTS idx_skus_tenant_id ON skus (tenant_id)",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(_t(ddl))
            except Exception as e:
                logger.warning("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        logger.warning("[migrate] skus legacy rebuild skipped: %s", e)
        _drop_rebuild_tmp_table(engine, tmp)


_make_engine()


def get_session(tenant_id=None):
    """获取会话。tenant_id 为租户上下文（配合 db.scoped 使用）。

    当前 SQLite 单库实现不按租户分库，tenant_id 仅作上下文透传，
    过滤统一走 db.scoped(query, model, tenant_id)。
    """
    return _SessionLocal()


def scoped(query, model, tenant_id=None):
    """Gap E2 统一租户过滤封装。

    语义：tenant_id 为 None/空 时不过滤（内部脚本与既有测试路径向后兼容）；
    传了就追加 filter(model.tenant_id == tenant_id)。
    用法：db.scoped(s.query(Model), Model, tenant_id).all()
    """
    if tenant_id is None or not str(tenant_id).strip():
        return query
    return query.filter(model.tenant_id == str(tenant_id))


def _tenant_ok(row, tenant_id):
    """行级租户校验（按 id 取单条后调用）：行的 tenant_id 为空按 default 处理。"""
    if tenant_id is None or not str(tenant_id).strip():
        return True
    return (getattr(row, "tenant_id", None) or "default") == str(tenant_id)


def now_iso():
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:8]


# -------------------------------------------------------------
# 收据
# -------------------------------------------------------------
def create_receipt(supplier_name="", status="uploaded", tenant_id="default"):
    s = get_session()
    try:
        row = _ReceiptRow(status=status, supplier_name=supplier_name,
                          tenant_id=str(tenant_id or "default"),
                          created_at=now_iso(), updated_at=now_iso())
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def get_receipt_row(receipt_id, tenant_id=None):
    """按 id 取单据。tenant_id 非空时校验归属，跨租户视为不存在（返回 None）。"""
    s = get_session()
    try:
        row = s.get(_ReceiptRow, int(receipt_id))
        if row is not None and not _tenant_ok(row, tenant_id):
            return None
        return row
    finally:
        s.close()


def update_receipt(receipt_id, **fields):
    """按字段更新收据；version 字段若传入则 +1。返回更新后的 row。"""
    if receipt_id is None or _ReceiptRow is None:
        return None
    s = get_session()
    try:
        row = s.get(_ReceiptRow, int(receipt_id))
        if row is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        row.updated_at = now_iso()
        s.commit()
        s.refresh(row)
        return row
    finally:
        s.close()


def delete_receipt(receipt_id):
    """物理删除收据及其明细（对齐完整版 discard）。返回是否存在。"""
    s = get_session()
    try:
        row = s.get(_ReceiptRow, int(receipt_id))
        if row is None:
            return False
        s.query(_ItemRow).filter(_ItemRow.receipt_id == int(receipt_id)).delete()
        s.delete(row)
        s.commit()
        return True
    finally:
        s.close()


def set_expected_pay_date(receipt_id, value):
    """设置/清空预期付款日（允许 None，update_receipt 会跳过 None）。返回更新后 row。"""
    s = get_session()
    try:
        row = s.get(_ReceiptRow, int(receipt_id))
        if row is None:
            return None
        row.expected_pay_date = value
        row.updated_at = now_iso()
        s.commit()
        s.refresh(row)
        return row
    finally:
        s.close()


def set_receipt_items(receipt_id, items, tenant_id=None):
    """整体替换收据明细（先删后插）。items = list[dict]。

    dict 键为前端契约名（fuzzy_candidates/entity_candidates 等），
    落到 _json 列。tenant_id 缺省时沿用所属单据的租户。
    """
    s = get_session()
    try:
        if tenant_id is None or not str(tenant_id).strip():
            rc = s.get(_ReceiptRow, int(receipt_id))
            tenant_id = getattr(rc, "tenant_id", None) if rc is not None else None
        from app.services.contract import normalize_evidence
        s.query(_ItemRow).filter(_ItemRow.receipt_id == int(receipt_id)).delete()
        for it in items:
            row = dict(it)
            row.pop("id", None)  # 旧 id 不保留，重新自增
            row.pop("tenant_id", None)  # 租户以单据归属为准，不接受逐行覆盖
            fuzzy = row.pop("fuzzy_candidates", [])
            entity = row.pop("entity_candidates", [])
            raw_name = row.pop("raw_name", None)
            raw_unit = row.pop("raw_unit", None)
            is_void = 1 if row.pop("is_void", 0) else 0
            actual_qty = row.pop("actual_qty", None)
            # Gap E1 / T7：字段级证据（可选）——归一后落 evidence_json，非法降级 NULL 不阻断
            evidence = normalize_evidence(row.pop("evidence", None))
            s.add(_ItemRow(
                receipt_id=int(receipt_id),
                raw_name=raw_name,
                raw_unit=raw_unit,
                fuzzy_candidates_json=json.dumps(fuzzy, ensure_ascii=False),
                entity_candidates_json=json.dumps(entity, ensure_ascii=False),
                is_void=is_void,
                actual_qty=actual_qty,
                evidence_json=(json.dumps(evidence, ensure_ascii=False)
                               if evidence else None),
                tenant_id=str(tenant_id or "default"),
                **row,
            ))
        s.commit()
    finally:
        s.close()


def append_audit_log(receipt_id, who, action, field, old, new, details=None):
    """向 receipts.audit_logs_json 追加一条审计记录。

    结构：list[{who, action, field, old, new, ts, details}]。
    允许 old/new 为 dict/list（用于结构化变更，如 engine config）。
    """
    _s = get_session()
    try:
        row = _s.get(_ReceiptRow, int(receipt_id))
        if row is None:
            return
        logs = json.loads(row.audit_logs_json or "[]")
        if not isinstance(logs, list):
            logs = []
        entry = {
            "who": who or "unknown",
            "action": action,
            "field": field,
            "old": old,
            "new": new,
            "ts": now_iso(),
        }
        if details is not None:
            entry["details"] = details
        logs.append(entry)
        row.audit_logs_json = json.dumps(logs, ensure_ascii=False)
        row.updated_at = now_iso()
        _s.commit()
    finally:
        _s.close()


def soft_delete_receipt(receipt_id):
    """软删除：置 deleted_at 时间戳。返回是否存在。"""
    _s = get_session()
    try:
        row = _s.get(_ReceiptRow, int(receipt_id))
        if row is None:
            return False
        row.deleted_at = now_iso()
        row.updated_at = now_iso()
        _s.commit()
        return True
    finally:
        _s.close()


def restore_receipt(receipt_id):
    """回收站恢复：清除 deleted_at。返回是否存在。"""
    _s = get_session()
    try:
        row = _s.get(_ReceiptRow, int(receipt_id))
        if row is None or row.deleted_at is None:
            return False
        row.deleted_at = None
        row.updated_at = now_iso()
        _s.commit()
        return True
    finally:
        _s.close()


def append_system_audit_log(who, action, field, old, new):
    """系统级审计（无 receipt_id 的场景：engine config 变更等）。

    追加到 app_settings.system_audit_json（list）。
    """
    _s = get_session()
    try:
        row = _s.get(_AppSettingRow, "system_audit_json")
        if row is None:
            row = _AppSettingRow(key="system_audit_json")
            _s.add(row)
        logs = json.loads(row.value or "[]")
        if not isinstance(logs, list):
            logs = []
        logs.append({
            "who": who or "unknown",
            "action": action,
            "field": field,
            "old": old,
            "new": new,
            "ts": now_iso(),
        })
        row.value = json.dumps(logs, ensure_ascii=False)
        _s.commit()
    finally:
        _s.close()


def read_system_audit_log():
    """读取系统级审计日志（app_settings.system_audit_json）。无数据返回 []。"""
    _s = get_session()
    try:
        row = _s.get(_AppSettingRow, "system_audit_json")
        if row is None or not row.value:
            return []
        logs = json.loads(row.value)
        return logs if isinstance(logs, list) else []
    finally:
        _s.close()


def list_trash_receipts(tenant_id=None):
    """返回软删除单据列表（含 deleted_at），按 id 倒序。"""
    _s = get_session()
    try:
        q = scoped(_s.query(_ReceiptRow), _ReceiptRow, tenant_id)
        return q.filter(
            _ReceiptRow.deleted_at.isnot(None)
        ).order_by(_ReceiptRow.id.desc()).all()
    finally:
        _s.close()


def list_receipt_rows(tenant_id=None):
    _s = get_session()
    try:
        return scoped(_s.query(_ReceiptRow), _ReceiptRow, tenant_id).filter(
            _ReceiptRow.deleted_at.is_(None)
        ).order_by(_ReceiptRow.id.desc()).all()
    finally:
        _s.close()


def get_receipt_items(receipt_id, tenant_id=None):
    s = get_session()
    try:
        rows = scoped(s.query(_ItemRow), _ItemRow, tenant_id).filter(
            _ItemRow.receipt_id == int(receipt_id)).all()
        return [_row_to_item(r) for r in rows]
    finally:
        s.close()


def update_item_sku(item_id, sku_id):
    """回写明细行的 sku_id（approve 建 SKU 后，供前端显示 SKU 关联）。"""
    s = get_session()
    try:
        row = s.get(_ItemRow, int(item_id))
        if row is not None:
            row.sku_id = int(sku_id) if sku_id else None
            row.matched = 1 if sku_id else 0
            s.commit()
    finally:
        s.close()


def _row_to_item(r):
    # Gap E1 / T7：字段级证据回读（坏 JSON 容错为 None，不阻断）
    try:
        evidence = json.loads(getattr(r, "evidence_json", None) or "null")
    except (TypeError, ValueError):
        evidence = None
    if not isinstance(evidence, dict):
        evidence = None
    return {
        "id": r.id, "name": r.name, "raw_name": r.raw_name or r.name,
        "quantity": r.quantity, "unit": r.unit, "raw_unit": r.raw_unit or r.unit,
        "unit_price": r.unit_price, "amount": r.amount,
        "sku_id": r.sku_id, "cost_center_id": r.cost_center_id,
        "confidence": r.confidence, "matched": bool(r.matched),
        "price_anomaly": bool(r.price_anomaly),
        "price_anomaly_direction": r.price_anomaly_direction,
        "price_diff_percent": r.price_diff_percent,
        "unit_conversion_warning": r.unit_conversion_warning,
        "fuzzy_candidates": json.loads(r.fuzzy_candidates_json or "[]"),
        "entity_candidates": json.loads(r.entity_candidates_json or "[]"),
        "is_void": bool(getattr(r, "is_void", 0) or 0),
        "actual_qty": getattr(r, "actual_qty", None),
        "evidence": evidence,
    }


# -------------------------------------------------------------
# SKU 库存
# -------------------------------------------------------------
def list_skus(include_inactive=False, tenant_id=None):
    s = get_session()
    try:
        q = scoped(s.query(_SkuRow), _SkuRow, tenant_id)
        if not include_inactive:
            q = q.filter(_SkuRow.active == 1)
        return q.all()
    finally:
        s.close()


def get_sku(sku_id, tenant_id=None):
    """按 id 取 SKU。tenant_id 非空时校验归属，跨租户视为不存在（返回 None）。"""
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is not None and not _tenant_ok(row, tenant_id):
            return None
        return row
    finally:
        s.close()


def create_sku(name, category="", base_unit="", min_stock_alert=0.0,
               tenant_id="default"):
    s = get_session()
    try:
        # P2-1: 重名检查按租户 scope（上游须传租户；tenant 为空时不过滤，向后兼容）
        existing = scoped(s.query(_SkuRow), _SkuRow,
                          tenant_id).filter(_SkuRow.name == name).first()
        if existing:
            return None, "SKU_NAME_CONFLICT"
        row = _SkuRow(name=name, category=category, base_unit=base_unit,
                      min_stock_alert=min_stock_alert,
                      tenant_id=str(tenant_id or "default"),
                      sku_code="SKU-" + new_id().upper())
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, None
    finally:
        s.close()


def update_sku(sku_id, tenant_id=None, **fields):
    """更新 SKU。P2-1: 重命名重名检查按租户 scope；
    tenant_id 缺省 None 时不过滤（保持既有全局行为，向后兼容）。
    注意 tenant_id 为显式参数，不会落入 fields 覆盖行的租户归属。

    单位变更封锁（既有问题 #2，patch_sku 的 UNIT_CHANGE_BLOCKED 映射自此生效）：
    代码考古结论（见 docs/04.../02-前端UX地毯式走查报告.md 库存 PATCH 用例与
    前端 toast「账面库存不为 0，请先盘点调整为 0 再改单位」）—— 判据是账面
    库存 current_stock 非 0，而非仅存在历史流水（stock=0 的 SKU 即使有流水
    也允许改单位，对齐前端引导的「先盘点归零再改」路径）。账面库存非 0 时
    改 base_unit 会使存量数量与历史单价的量纲口径失真，故拒绝。
    """
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is None:
            return None, "NOT_FOUND"
        if "name" in fields and fields["name"] and fields["name"] != row.name:
            clash = scoped(s.query(_SkuRow), _SkuRow,
                           tenant_id).filter(_SkuRow.name == fields["name"]).first()
            if clash:
                return None, "SKU_NAME_CONFLICT"
        new_unit = fields.get("base_unit")
        if new_unit and str(new_unit) != str(row.base_unit or "") \
                and (row.current_stock or 0) != 0:
            return None, "UNIT_CHANGE_BLOCKED"
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        s.commit()
        return row, None
    finally:
        s.close()


def find_sku_by_name(name, tenant_id=None):
    s = get_session()
    try:
        return scoped(s.query(_SkuRow), _SkuRow,
                      tenant_id).filter(_SkuRow.name == name).first()
    finally:
        s.close()


def apply_stock_log(sku_id, name, qty, unit, amount, vendor, date, receipt_id,
                    kind, note="", tenant_id="default"):
    """写库存流水 + 更新 SKU 当前库存 + 挂载入库批次生成钩子。"""
    s = get_session()
    try:
        s.add(_StockLogRow(sku_id=sku_id, name=name, qty=qty, unit=unit, amount=amount,
                           vendor=vendor, date=date, receipt_id=receipt_id,
                           kind=kind, note=note, created_at=now_iso(),
                           tenant_id=str(tenant_id or "default")))
        if sku_id:
            sku = s.get(_SkuRow, int(sku_id))
            if sku:
                if kind in ("in", "stocktake"):
                    sku.current_stock += qty if kind == "in" else (qty - sku.current_stock)
                    if kind == "in" and amount > 0:
                        sku.last_unit_price = amount / qty if qty else sku.last_unit_price
                elif kind in ("consume", "waste"):
                    sku.current_stock -= qty
            # 批次入库钩子：kind == 'in' 时自动记录入库批次
            if kind == "in" and qty > 0:
                unit_price = (amount / qty) if qty > 0 else (sku.last_unit_price if sku else 0.0)
                from app.services.costing_service import CostingService
                CostingService.record_inbound_batch(
                    session=s,
                    sku_id=int(sku_id),
                    qty=qty,
                    unit_price=unit_price,
                    unit=unit or (sku.base_unit if sku else ""),
                    date=date or now_iso()[:10],
                    receipt_id=receipt_id,
                )
        s.commit()
    finally:
        s.close()


def stocktake_sku(sku_id, actual_qty, note="", tenant_id="default"):
    s = get_session()
    try:
        sku = s.get(_SkuRow, int(sku_id))
        if sku is None:
            return None
        diff = actual_qty - sku.current_stock
        s.add(_StockLogRow(sku_id=sku.id, name=sku.name, qty=diff, unit=sku.base_unit,
                           amount=0, vendor="", date=now_iso()[:10], receipt_id=None,
                           kind="stocktake", note=note or "盘点", created_at=now_iso(),
                           tenant_id=str(tenant_id or "default")))
        sku.current_stock = actual_qty
        s.commit()
        return sku
    finally:
        s.close()


def delete_sku(sku_id, tenant_id=None):
    """安全删除/停用 SKU：
    - 若无库存流水引用：直接物理删除并返回 (True, 'DELETED')
    - 若有库存流水引用：标记停用 active=0 并返回 (True, 'DEACTIVATED')
    - 若不存在：返回 (False, 'NOT_FOUND')
    - 租户防御（Wave A F2）：tenant_id 非空时校验 SKU 归属，跨租户视为
      不存在（返回 (False, 'NOT_FOUND')，与不存在同形态）；None=不过滤
      （向后兼容内部脚本与既有测试路径）。
    """
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is None or not _tenant_ok(row, tenant_id):
            return False, "NOT_FOUND"
        log_count = s.query(_StockLogRow).filter(_StockLogRow.sku_id == int(sku_id)).count()
        item_count = s.query(_ItemRow).filter(_ItemRow.sku_id == int(sku_id)).count()
        if log_count == 0 and item_count == 0:
            s.delete(row)
            s.commit()
            return True, "DELETED"
        else:
            row.active = 0
            s.commit()
            return True, "DEACTIVATED"
    finally:
        s.close()


def merge_skus(primary_sku_id, secondary_sku_ids, tenant_id=None):
    """合并 SKU：
    - 将 secondary_sku_ids 关联的所有 _StockLogRow / _ItemRow 迁移到 primary_sku_id
    - 将 secondary SKU 的名称及现有库存按规则合并至 primary
    - 将 secondary SKU 标记停用 (active=0)
    - 返回合并受影响的记录数
    - 租户防御（Wave A F2）：tenant_id 非空时对 primary 与每个 secondary
      校验归属，跨租户分别返回 (None, 'PRIMARY_NOT_FOUND') /
      (None, 'SECONDARY_NOT_FOUND')，与既有错误形态一致；None=不过滤
      （向后兼容内部脚本与既有测试路径）。
    """
    s = get_session()
    try:
        primary = s.get(_SkuRow, int(primary_sku_id))
        if primary is None or not _tenant_ok(primary, tenant_id):
            return None, "PRIMARY_NOT_FOUND"
        sec_ids = [int(x) for x in secondary_sku_ids if int(x) != int(primary_sku_id)]
        if not sec_ids:
            return None, "NO_SECONDARY_SKUS"

        secondary_skus = s.query(_SkuRow).filter(_SkuRow.id.in_(sec_ids)).all()
        for sku in secondary_skus:
            if not _tenant_ok(sku, tenant_id):
                return None, "SECONDARY_NOT_FOUND"
        secondary_names = [sku.name for sku in secondary_skus]

        # 迁移 stock logs
        s.query(_StockLogRow).filter(_StockLogRow.sku_id.in_(sec_ids)).update(
            {_StockLogRow.sku_id: primary.id}, synchronize_session=False
        )
        # 迁移 items
        s.query(_ItemRow).filter(_ItemRow.sku_id.in_(sec_ids)).update(
            {_ItemRow.sku_id: primary.id}, synchronize_session=False
        )

        # 合并库存
        total_sec_stock = sum(sku.current_stock for sku in secondary_skus)
        primary.current_stock += total_sec_stock

        # 停用副 SKU 并清零迁移后的库存（避免重复合并翻倍）
        for sec in secondary_skus:
            sec.active = 0
            sec.current_stock = 0.0

        s.commit()
        return {
            "primary_sku_id": primary.id,
            "primary_name": primary.name,
            "merged_sku_ids": sec_ids,
            "merged_names": secondary_names,
            "new_stock": primary.current_stock
        }, None
    finally:
        s.close()


def list_sku_tenant_ids():
    """返回 skus 表现存的所有 distinct tenant_id（启动自愈按租户循环用）。

    空表返回空 list；任何异常一律返回 []，不得阻断启动。
    """
    s = get_session()
    try:
        rows = s.query(_SkuRow.tenant_id).distinct().all()
        return sorted({r[0] for r in rows if r[0]})
    except Exception:
        return []
    finally:
        s.close()


def deduplicate_skus_by_canonical(tenant_id=None):
    """B-P0-1 存量脏数据迁移：按 canonical 名归一去重，合并 _\\d{10} 变体。

    遍历全部 SKU（含停用），以 canonical_name 为分组键，将同组内的
    变体合并至组内 id 最小的活跃 SKU（保留主），其余停用并迁移流水。
    特例保障：本地新鲜菜心_1787140411 → 本地新鲜菜心（sku 07 → sku 03）。
    返回合并报告 list[dict]。
    幂等、无损历史流水（迁移 inventory_log / receipt_items 至主 SKU，停用副 SKU，日志 audit_logs 留痕）
    修复要点：仅对含 _\\d{10} 流水号的分组去重，避免 existing_sku_0 等泛化下划线误合并；
    主 SKU 若仍带流水号则重命名为 canonical 纯净名，确保 _17871* 清零；繁简归一通过 SmartSplitter 别名映射支持。
    租户口径（P3-5）：tenant_id 非空时仅在指定租户内扫描/合并（分组、残留
    清理与重命名冲突检查全部 scoped）；None 不过滤（内部脚本与存量调用兼容）。
    """
    import re as _re
    _SERIAL_RE = _re.compile(r"_\d{10}$")
    try:
        from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool as _ST
        _tool = _ST()
        def _canon(n): return _tool.sanitize_name(n)
    except Exception:
        _pat = _re.compile(r"_\d{10}$")
        def _canon(n): return _pat.sub("", (n or "").strip()).strip()

    s = get_session()
    try:
        rows = scoped(s.query(_SkuRow), _SkuRow, tenant_id).all()
        groups = {}
        for r in rows:
            try:
                c = _canon(r.name)
            except Exception:
                c = _re.sub(r"_\d{10}$", "", (r.name or "").strip()).strip()
            if not c:
                continue
            groups.setdefault(c, []).append(r)
        reports = []
        for canon, members in groups.items():
            if len(members) <= 1:
                continue
            active_members = [m for m in members if m.active == 1]
            if len(active_members) <= 1:
                continue
            # 仅处理含流水号变体的分组，避免 existing_sku_* 等泛化合并
            if not any(_SERIAL_RE.search(m.name or "") for m in active_members):
                continue
            # 主 SKU 选择：优先无流水号的干净名（含繁简别名映射的无流水号），其次最早 id
            def _is_clean(m):
                if m.name == canon:
                    return 0
                if not _SERIAL_RE.search(m.name or "") and _canon(m.name) == canon:
                    return 0.5
                return 1
            active_sorted = sorted(active_members, key=lambda x: (_is_clean(x), x.id))
            primary = active_sorted[0]
            secondaries = active_sorted[1:]
            sec_ids = [m.id for m in secondaries]
            sec_names = [m.name for m in secondaries]
            if not sec_ids:
                continue
            old_primary_name = primary.name
            # 若主仍带流水号或繁体别名差异，归一重命名为 canonical
            if primary.name != canon:
                clash = scoped(s.query(_SkuRow), _SkuRow,
                               tenant_id).filter(_SkuRow.name == canon, _SkuRow.id != primary.id).first()
                if clash is None or clash.active == 0:
                    primary.name = canon
                else:
                    # 活跃冲突且不在同组（异常），保留原名
                    pass
            # 迁移流水（inventory_log + receipt_items）至主 SKU
            s.query(_StockLogRow).filter(_StockLogRow.sku_id.in_(sec_ids)).update(
                {_StockLogRow.sku_id: primary.id}, synchronize_session=False
            )
            s.query(_ItemRow).filter(_ItemRow.sku_id.in_(sec_ids)).update(
                {_ItemRow.sku_id: primary.id}, synchronize_session=False
            )
            total_sec_stock = sum(m.current_stock for m in secondaries)
            primary.current_stock += total_sec_stock
            for sec in secondaries:
                sec.active = 0
                sec.current_stock = 0.0
            # 系统审计留痕（每组合并一条，幂等可追溯，复用同一会话避免锁）
            try:
                _audit_row = s.get(_AppSettingRow, "system_audit_json")
                if _audit_row is None:
                    _audit_row = _AppSettingRow(key="system_audit_json")
                    s.add(_audit_row)
                    _audit_row.value = "[]"
                _audit_logs = json.loads(_audit_row.value or "[]")
                if not isinstance(_audit_logs, list):
                    _audit_logs = []
                _audit_logs.append({
                    "who": "system",
                    "action": "deduplicate_skus_by_canonical",
                    "field": canon,
                    "old": old_primary_name,
                    "new": canon,
                    "details": {
                        "canonical": canon,
                        "primary_id": primary.id,
                        "old_primary_name": old_primary_name,
                        "merged_ids": sec_ids,
                        "merged_names": sec_names,
                    },
                    "ts": now_iso(),
                })
                _audit_row.value = json.dumps(_audit_logs, ensure_ascii=False)
            except Exception:
                pass
            reports.append({
                "canonical": canon,
                "primary_id": primary.id,
                "primary_name": primary.name,
                "old_primary_name": old_primary_name,
                "merged_ids": sec_ids,
                "merged_names": sec_names,
                "new_stock": primary.current_stock,
            })
        # 兜底清理：处理残留的 _17871* 命名（包括历史已停用但无流水的可物理删除，确保 sqlite 验证清零）
        has_residual = False
        try:
            residual = [r for r in scoped(s.query(_SkuRow), _SkuRow,
                                          tenant_id).all()
                        if _SERIAL_RE.search(r.name or "")]
            for r in residual:
                if r.active == 0:
                    log_cnt = s.query(_StockLogRow).filter(_StockLogRow.sku_id == r.id).count()
                    item_cnt = s.query(_ItemRow).filter(_ItemRow.sku_id == r.id).count()
                    if log_cnt == 0 and item_cnt == 0:
                        s.delete(r)
                        has_residual = True
                    else:
                        try:
                            can = _canon(r.name)
                            if can and can != r.name:
                                clash = scoped(s.query(_SkuRow), _SkuRow,
                                               tenant_id).filter(_SkuRow.name == can, _SkuRow.id != r.id).first()
                                if clash is None:
                                    r.name = can
                                else:
                                    r.name = f"{can}_archived_{r.id}"
                                has_residual = True
                        except Exception:
                            pass
                else:
                    # 活跃残留脏数据（单例或漏网）：直接归一重命名为 canonical
                    try:
                        can = _canon(r.name)
                        if can and can != r.name:
                            clash = scoped(s.query(_SkuRow), _SkuRow,
                                           tenant_id).filter(_SkuRow.name == can, _SkuRow.id != r.id).first()
                            if clash is None or clash.active == 0:
                                r.name = can
                                has_residual = True
                    except Exception:
                        pass
        except Exception:
            pass
        if reports or has_residual:
            s.commit()
        return reports
    finally:
        s.close()


def price_history(sku_id, tenant_id=None):
    """SKU 价格历史：从库存流水（kind=in）推导单价 = amount/qty。"""
    s = get_session()
    try:
        rows = scoped(s.query(_StockLogRow), _StockLogRow, tenant_id).filter(
            _StockLogRow.sku_id == int(sku_id),
            _StockLogRow.kind == "in",
        ).order_by(_StockLogRow.date.asc()).all()
        out = []
        for r in rows:
            unit_price = (r.amount / r.qty) if r.qty else 0.0
            out.append(type("_", (), {
                "date": r.date, "unit_price": unit_price, "qty": r.qty,
                "vendor": r.vendor, "receipt_id": r.receipt_id,
                "source": "receipt",
            }))
        return out
    finally:
        s.close()


# -------------------------------------------------------------
# 供应商
# -------------------------------------------------------------
def list_suppliers(include_inactive=False, tenant_id=None):
    s = get_session()
    try:
        q = scoped(s.query(_SupplierRow), _SupplierRow, tenant_id)
        if not include_inactive:
            q = q.filter(_SupplierRow.active == 1)
        return q.all()
    finally:
        s.close()


def get_supplier(supplier_id, tenant_id=None):
    """按 id 取供应商。tenant_id 非空时校验归属，跨租户视为不存在（返回 None）。"""
    s = get_session()
    try:
        row = s.get(_SupplierRow, int(supplier_id))
        if row is not None and not _tenant_ok(row, tenant_id):
            return None
        return row
    finally:
        s.close()


def find_supplier_by_name(name, tenant_id=None):
    s = get_session()
    try:
        return scoped(s.query(_SupplierRow), _SupplierRow,
                      tenant_id).filter(_SupplierRow.name == name).first()
    finally:
        s.close()


def find_supplier_by_canonical_name(name, tenant_id=None):
    """U-06：按归一 key（去英文括号注 + 繁转简）查找已有供应商，命中则返回该行。"""
    from app.services.supplier_normalizer import canonical_supplier_key
    key = canonical_supplier_key(name)
    if not key:
        return None
    s = get_session()
    try:
        for sup in scoped(s.query(_SupplierRow), _SupplierRow, tenant_id).all():
            if canonical_supplier_key(sup.name) == key:
                return sup
        return None
    finally:
        s.close()


def create_supplier(name, tenant_id="default", **fields):
    s = get_session()
    try:
        existing = scoped(s.query(_SupplierRow), _SupplierRow,
                          tenant_id).filter(_SupplierRow.name == name).first()
        if existing:
            return None, "已存在同名供应商"
        s.close()
        # U-06：核心词归一命中已有供应商 → 复用其 id，不再新建变体档案
        hit = find_supplier_by_canonical_name(name, tenant_id)
        if hit is not None:
            return hit.id, None
        s = get_session()
        row = _SupplierRow(name=name, tenant_id=str(tenant_id or "default"),
                           supplier_code="SUP-" + new_id().upper(), **fields)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, None
    finally:
        try:
            s.close()
        except Exception:
            pass


def update_supplier(supplier_id, **fields):
    s = get_session()
    try:
        row = s.get(_SupplierRow, int(supplier_id))
        if row is None:
            return None, "NOT_FOUND"
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        s.commit()
        return row, None
    finally:
        s.close()


def supplier_stats(supplier_id, tenant_id=None):
    """供应商聚合：receipt_count / 赊账未付总额。"""
    s = get_session()
    try:
        _sup = s.get(_SupplierRow, int(supplier_id))
        sup_name = _sup.name if _sup is not None else ""
        receipts = scoped(s.query(_ReceiptRow), _ReceiptRow, tenant_id).filter(
            _ReceiptRow.supplier_name == sup_name).all()
        return {
            "receipt_count": len(receipts),
            "unpaid_credit_total": sum(r.total_amount for r in receipts
                                       if r.settlement_type == "credit" and r.status == "approved"),
            "unpaid_credit_count": sum(1 for r in receipts
                                       if r.settlement_type == "credit" and r.status == "approved"),
        }
    finally:
        s.close()


# -------------------------------------------------------------
# 部门
# -------------------------------------------------------------
def list_departments():
    s = get_session()
    try:
        return s.query(_DeptRow).all()
    finally:
        s.close()


def create_department(name):
    s = get_session()
    try:
        row = _DeptRow(name=name, active=1)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def update_department(dept_id, **fields):
    s = get_session()
    try:
        row = s.get(_DeptRow, int(dept_id))
        if row is None:
            return None
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        s.commit()
        return row
    finally:
        s.close()


# -------------------------------------------------------------
# 付款
# -------------------------------------------------------------
def create_payment(supplier_id, amount, method, paid_at, notes="",
                   voucher_image_path="", linked_receipt_ids=None):
    s = get_session()
    try:
        row = _PaymentRow(supplier_id=int(supplier_id), amount=amount, method=method,
                          paid_at=paid_at, notes=notes,
                          voucher_image_path=voucher_image_path,
                          linked_receipt_ids_json=json.dumps(linked_receipt_ids or []))
        s.add(row)
        s.commit()
        s.refresh(row)
        payment_id = row.id
        # 回写收据付款状态
        for rid in (linked_receipt_ids or []):
            rc = s.get(_ReceiptRow, int(rid))
            if rc:
                rc.paid_at = paid_at
                rc.paid_method = method
                rc.payment_id = payment_id
        s.commit()
        return payment_id
    finally:
        s.close()


def list_payments():
    s = get_session()
    try:
        rows = s.query(_PaymentRow).order_by(_PaymentRow.paid_at.desc()).all()
        out = []
        for r in rows:
            sup = s.get(_SupplierRow, r.supplier_id) if r.supplier_id else None
            out.append({
                "id": r.id, "supplier_name": sup.name if sup else "—",
                "paid_at": r.paid_at, "amount": r.amount,
                "method": r.method,
                "method_label": {"cash": "现金", "bank_transfer": "银行转账",
                                 "cheque": "支票", "fps": "转数快"}.get(r.method, r.method),
                "linked_receipt_count": len(json.loads(r.linked_receipt_ids_json or "[]")),
                "linked_receipt_ids": json.loads(r.linked_receipt_ids_json or "[]"),
                "voucher_image_url": r.voucher_image_path or "",
                "notes": r.notes or "",
            })
        return out
    finally:
        s.close()


# -------------------------------------------------------------
# VendorMemory
# -------------------------------------------------------------
def upsert_vendor_memory(vendor, notes, sample, tenant_id="default",
                         source_kind="manual", source_ref="[]",
                         memory_id=None, created_at=None):
    """每条记忆一行（Gap B1，T3 追加式写入）。

    语义变更：不再把 notes 拼接/覆盖到 (vendor, tenant_id) 单行，而是每次写入
    生成新 memory_id 新行（追加）；同 (vendor, tenant_id) 下 notes+sample 完全
    相同的 active 行已存在时不重复追加（幂等），复用既有 memory_id。
    source_kind: approve | feedback_distilled | manual
    source_ref:  触发来源 receipt_id 列表 JSON（可回溯）
    返回 (memory_id, is_new)：is_new=False 表示命中幂等去重。
    """
    vid = str(vendor or "")
    tid = str(tenant_id).strip() if tenant_id else "default"
    tid = tid or "default"
    mid = str(memory_id).strip() if memory_id else uuid.uuid4().hex
    now = now_iso()
    created = str(created_at).strip() if created_at else now
    s = get_session()
    try:
        dup = s.query(_VendorMemoryRow).filter(
            _VendorMemoryRow.vendor == vid,
            _VendorMemoryRow.tenant_id == tid,
            _VendorMemoryRow.status == "active",
            _VendorMemoryRow.notes == (notes or ""),
            _VendorMemoryRow.sample == (sample or ""),
        ).first()
        if dup is not None:
            # P3-3：去重命中时把新 source_ref 的 receipt_id 并入既有 JSON
            #（集合式去重合并）并刷新 updated_at，避免新触发来源丢失可回溯性。
            # version 保持 1 不变 —— 行内演进与 version 递增归 TODO(T8) 口径。
            try:
                old_refs = json.loads(dup.source_ref or "[]")
                if not isinstance(old_refs, list):
                    old_refs = []
                new_refs = json.loads(source_ref or "[]")
                if not isinstance(new_refs, list):
                    new_refs = []
                merged = [str(r) for r in old_refs]
                for r in new_refs:
                    if str(r) not in merged:
                        merged.append(str(r))
                dup.source_ref = json.dumps(merged, ensure_ascii=False)
                dup.updated_at = now_iso()
                s.commit()
            except Exception as e:
                logger.warning("[memory] source_ref merge on dedup hit failed: %s", e)
                s.rollback()
            return (dup.memory_id, False)
        row = _VendorMemoryRow(
            memory_id=mid,
            vendor=vid,
            tenant_id=tid,
            notes=notes or "",
            sample=sample or "",
            # 本 Wave 为追加式写入，每行 version 初始 1；同行演进与 version 递增
            # 在 T8 记忆生命周期治理中落地 TODO(T8)
            version=1,
            source_kind=str(source_kind or "manual"),
            source_ref=source_ref or "[]",
            created_at=created,
            updated_at=now,
            hit_count=0,
            last_hit_at="",
            decay_score=1.0,
            status="active",
        )
        s.add(row)
        s.commit()
        return (mid, True)
    finally:
        s.close()


def list_vendor_memory(vendor=None, tenant_id="default", include_archived=False):
    """按 (vendor, tenant_id) 过滤列出记忆（Gap B1 治理视图）。

    - vendor 为空：列出租户下全部记忆（管理视角）
    - vendor 非空：精确匹配优先、模糊/别名相关次之（与 get_vendor_memory 同
      一口径，仅在本租户范围内，绝不跨租户召回）
    - 默认只列 status='active'（include_archived=True 时含归档行）
    - 排序：追加序倒序（rowid DESC，最新写入在前），精确匹配优先于模糊匹配
    返回 dict 列表，含全部治理字段。
    """
    tid = str(tenant_id).strip() if tenant_id else "default"
    tid = tid or "default"
    s = get_session()
    try:
        from sqlalchemy import text as _sa_text
        q = scoped(s.query(_VendorMemoryRow), _VendorMemoryRow, tid)
        if not include_archived:
            q = q.filter(_VendorMemoryRow.status == "active")
        rows = q.order_by(_sa_text("rowid DESC")).all()
        if vendor and str(vendor).strip():
            v = str(vendor).strip()
            try:
                from app.services.rag import _vendor_related
            except Exception:
                _vendor_related = None
            exact, fuzzy = [], []
            for r in rows:
                if r.vendor == v:
                    exact.append(r)
                elif _vendor_related is not None and _vendor_related(v, r.vendor):
                    fuzzy.append(r)
            rows = exact + fuzzy
        out = []
        for r in rows:
            out.append({
                "memory_id": r.memory_id,
                "vendor": r.vendor,
                "tenant_id": r.tenant_id,
                "notes": r.notes or "",
                "sample": r.sample or "",
                "version": int(r.version or 1),
                "source_kind": r.source_kind or "manual",
                "source_ref": r.source_ref or "[]",
                "created_at": r.created_at or "",
                "updated_at": r.updated_at or "",
                "hit_count": int(r.hit_count or 0),
                "last_hit_at": r.last_hit_at or "",
                "decay_score": float(r.decay_score if r.decay_score is not None else 1.0),
                "status": r.status or "active",
            })
        return out
    finally:
        s.close()


def get_vendor_memory(vendor, tenant_id="default"):
    """精确/别名查找该供应商最新一条 active 记忆（向后兼容 dict 形态）。

    返回 {vendor, notes, sample}；多行时取最新一条（追加序倒序首个）。
    需要完整治理字段或全部行时改用 list_vendor_memory。
    """
    if not vendor or not str(vendor).strip():
        return None
    rows = list_vendor_memory(vendor, tenant_id=tenant_id, include_archived=False)
    if not rows:
        return None
    r = rows[0]
    return {"vendor": r["vendor"], "notes": r["notes"], "sample": r["sample"]}


def bump_vendor_memory_hit(memory_ids):
    """Gap B1 命中记账：自增 hit_count 并刷新 last_hit_at（检索注入后调用）。

    T8 非对称衰减（强化慢）：每次命中按 settings 键 'memory_decay_hit_bonus'
    （缺省 +0.05）上调 decay_score，封顶 1.0。
    memory_id 不存在的忽略；返回实际更新的行数。异常由调用方兜底（不阻断识别）。
    """
    ids = [str(m) for m in (memory_ids or []) if m]
    if not ids:
        return 0
    try:
        from app.services import settings_service
        bonus = settings_service.get_float("memory_decay_hit_bonus", 0.05)
    except Exception:
        bonus = 0.05
    s = get_session()
    try:
        rows = s.query(_VendorMemoryRow).filter(
            _VendorMemoryRow.memory_id.in_(ids)).all()
        now = now_iso()
        for r in rows:
            r.hit_count = int(r.hit_count or 0) + 1
            r.last_hit_at = now
            cur = float(r.decay_score if r.decay_score is not None else 1.0)
            r.decay_score = min(1.0, cur + bonus)
        s.commit()
        return len(rows)
    finally:
        s.close()


# -------------------------------------------------------------
# T8（Gap D1 + B2）：待确认记忆队列 + 非对称淘汰
# -------------------------------------------------------------
def enqueue_pending_memory(vendor, content, tenant_id="default",
                           source_kind="feedback_distilled",
                           source_receipt_ids=None):
    """蒸馏产物入待确认队列（不直接生效）。

    幂等：同 (vendor, tenant_id, content) 已有 pending 行时并入触发来源并返回
    既有行 (id, False)。返回 (pending_id, is_new)。
    """
    vid = str(vendor or "")
    tid = str(tenant_id or "default").strip() or "default"
    refs = json.dumps([str(r) for r in (source_receipt_ids or [])],
                      ensure_ascii=False)
    s = get_session()
    try:
        dup = s.query(_PendingMemoryRow).filter(
            _PendingMemoryRow.vendor == vid,
            _PendingMemoryRow.tenant_id == tid,
            _PendingMemoryRow.status == "pending",
            _PendingMemoryRow.content == (content or ""),
        ).first()
        if dup is not None:
            try:
                old = json.loads(dup.source_receipt_ids_json or "[]")
                if not isinstance(old, list):
                    old = []
                merged = [str(x) for x in old]
                for r in (source_receipt_ids or []):
                    if str(r) not in merged:
                        merged.append(str(r))
                dup.source_receipt_ids_json = json.dumps(merged, ensure_ascii=False)
                s.commit()
            except Exception:
                s.rollback()
            return dup.id, False
        row = _PendingMemoryRow(
            tenant_id=tid,
            vendor=vid,
            content=content or "",
            source_kind=str(source_kind or "feedback_distilled"),
            source_receipt_ids_json=refs,
            status="pending",
            created_at=now_iso(),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, True
    finally:
        s.close()


def _pending_row_to_dict(r):
    return {
        "id": r.id,
        "tenant_id": r.tenant_id or "default",
        "vendor": r.vendor or "",
        "content": r.content or "",
        "source_kind": r.source_kind or "feedback_distilled",
        "source_receipt_ids_json": r.source_receipt_ids_json or "[]",
        "status": r.status or "pending",
        "created_at": r.created_at or "",
        "reviewed_by": r.reviewed_by,
        "reviewed_at": r.reviewed_at,
    }


def list_pending_memory(status=None, tenant_id=None):
    """待确认记忆列表（可按 status / 租户过滤），id 倒序。"""
    if _PendingMemoryRow is None:
        return []
    s = get_session()
    try:
        q = scoped(s.query(_PendingMemoryRow), _PendingMemoryRow, tenant_id)
        if status:
            q = q.filter(_PendingMemoryRow.status == str(status))
        rows = q.order_by(_PendingMemoryRow.id.desc()).all()
        return [_pending_row_to_dict(r) for r in rows]
    finally:
        s.close()


def get_pending_memory(pending_id, tenant_id=None):
    """按 id 取待确认记忆；跨租户视为不存在（返回 None）。"""
    if _PendingMemoryRow is None:
        return None
    s = get_session()
    try:
        row = s.get(_PendingMemoryRow, int(pending_id))
        if row is None:
            return None
        if tenant_id is not None and str(tenant_id).strip() \
                and row.tenant_id != str(tenant_id).strip():
            return None
        return _pending_row_to_dict(row)
    except (TypeError, ValueError):
        return None
    finally:
        s.close()


def review_pending_memory(pending_id, status, reviewed_by, tenant_id=None):
    """流转待确认记忆：仅 pending 可转 approved / rejected。

    返回 True=流转成功；False=不存在（或跨租户）/已审过/非法目标状态。
    """
    if status not in ("approved", "rejected"):
        return False
    if _PendingMemoryRow is None:
        return False
    s = get_session()
    try:
        row = s.get(_PendingMemoryRow, int(pending_id))
        if row is None:
            return False
        if tenant_id is not None and str(tenant_id).strip() \
                and row.tenant_id != str(tenant_id).strip():
            return False
        if row.status != "pending":
            return False
        row.status = status
        row.reviewed_by = str(reviewed_by or "")
        row.reviewed_at = now_iso()
        s.commit()
        return True
    except (TypeError, ValueError):
        return False
    finally:
        s.close()


def apply_vendor_memory_override_penalty(vendor, tenant_id="default"):
    """T8 非对称衰减（淘汰快）：用户覆写 → 该供应商活跃记忆扣分。

    步长取 settings 键 'memory_decay_override_penalty'（缺省 -0.12）；
    扣分后低于归档阈值（键 'memory_archive_threshold'，缺省 0.5）的行自动
    置 archived，停止注入。返回受影响的行数。
    """
    if _VendorMemoryRow is None:
        return 0
    try:
        from app.services import settings_service
        penalty = abs(settings_service.get_float(
            "memory_decay_override_penalty", 0.12))
        threshold = settings_service.get_float("memory_archive_threshold", 0.5)
    except Exception:
        penalty, threshold = 0.12, 0.5
    vid = str(vendor or "")
    tid = str(tenant_id or "default").strip() or "default"
    if not vid:
        return 0
    s = get_session()
    try:
        rows = s.query(_VendorMemoryRow).filter(
            _VendorMemoryRow.vendor == vid,
            _VendorMemoryRow.tenant_id == tid,
            _VendorMemoryRow.status == "active",
        ).all()
        for r in rows:
            cur = float(r.decay_score if r.decay_score is not None else 1.0)
            new = cur - penalty
            r.decay_score = new
            if new <= threshold:
                r.status = "archived"
            r.updated_at = now_iso()
        s.commit()
        return len(rows)
    finally:
        s.close()


def memory_archive_threshold():
    """归档阈值（settings 实时读取，缺省 0.5），供检索侧过滤。"""
    try:
        from app.services import settings_service
        return settings_service.get_float("memory_archive_threshold", 0.5)
    except Exception:
        return 0.5


# -------------------------------------------------------------
# app_settings 通用键值读写（T10 Gap E3）：settings_service 的底层。
# engine_config / system_audit_json 是历史专用键，其余键走通用读写。
# -------------------------------------------------------------
def get_app_setting(key, default=None):
    """读取 app_settings 单个键；不存在返回 default（None）。"""
    s = get_session()
    try:
        row = s.get(_AppSettingRow, str(key))
        return row.value if row is not None else default
    finally:
        s.close()


def set_app_setting(key, value):
    """写入 app_settings 单个键（幂等 upsert，值一律文本化）。"""
    s = get_session()
    try:
        row = s.get(_AppSettingRow, str(key))
        if row is None:
            row = _AppSettingRow(key=str(key))
            s.add(row)
        row.value = "" if value is None else str(value)
        s.commit()
    finally:
        s.close()


def list_app_settings():
    """全量 app_settings 键值 dict（含 engine_config 等历史键，调用方自滤）。"""
    s = get_session()
    try:
        rows = s.query(_AppSettingRow).all()
        return {r.key: r.value for r in rows}
    finally:
        s.close()


# -------------------------------------------------------------
# 引擎配置（admin 管理）
# -------------------------------------------------------------
def get_engine_config():
    s = get_session()
    try:
        row = s.get(_AppSettingRow, "engine_config")
        if row is None:
            from app.models import EngineConfig
            return EngineConfig()
        from app.models import EngineConfig
        stored = json.loads(row.value)
        # 旧配置迁移：openai_* 单组 → 拆分为 rec/aud 两组
        if "openai_base_url" in stored and not stored.get("openai_rec_base_url"):
            stored["openai_rec_base_url"] = stored.get("openai_base_url", "")
            stored["openai_rec_api_key"] = stored.get("openai_api_key", "")
            stored["openai_rec_model"] = stored.get("openai_model", "")
            stored["openai_aud_base_url"] = stored.get("openai_base_url", "")
            stored["openai_aud_api_key"] = stored.get("openai_api_key", "")
            stored["openai_aud_model"] = stored.get("openai_model", "")
        for k in ("openai_base_url", "openai_api_key", "openai_model"):
            stored.pop(k, None)
        # 旧 grey_mode → grey_percent（cross_audit 旧语义 = 开启审核，保留审核开关）
        if "grey_mode" in stored:
            stored.pop("grey_mode", None)
        return EngineConfig(**stored)
    finally:
        s.close()


def set_engine_config(cfg):
    s = get_session()
    try:
        row = s.get(_AppSettingRow, "engine_config")
        if row is None:
            row = _AppSettingRow(key="engine_config")
            s.add(row)
        row.value = json.dumps(cfg.model_dump(), ensure_ascii=False)
        s.commit()
    finally:
        s.close()


def _is_placeholder_key(key) -> bool:
    k = str(key or "").strip()
    if not k:
        return True
    return "YOUR_" in k.upper()


def hydrate_engine_config_from_env():
    """启动装配：SiliconFlow 为默认 provider（用户决策 2026-08-30），识别与审核双腿强制装配。

    每次重启都把双腿装配回 SiliconFlow 通道——即使 DB 已存其他引擎配置，
    管理台的临时切换在重启后不保留（灰测组不受影响）。
    识别腿：SF Qwen-VL 系（OPENAI_MODEL）；审核腿：SF GLM-4.5V（SILICONFLOW_AUDIT_MODEL，
    视觉模型支持 text/vlm/ondemand 审核，与识别腿跨厂商异构，Gap A3）。
    opencode/CLI 仅作显式选择，不再出现在默认路径。
    SiliconFlow 健康检查失败自动降级 DashScope（识别 qwen3-vl / 审核 qwen3-max，降级期间
    双腿同家族、异构性弱化，日志提示）；两者皆不可用则保持现有配置不动。幂等，可每次启动安全执行。
    """
    import logging
    log = logging.getLogger("startup")
    try:
        from dotenv import load_dotenv
        load_dotenv()
        cfg = get_engine_config()
        sf_key = (os.environ.get("SILICONFLOW_API_KEY")
                  or os.environ.get("OPENAI_REC_API_KEY")
                  or os.environ.get("OPENAI_API_KEY") or "").strip()
        ds_key = (os.environ.get("DASHSCOPE_API_KEY") or "").strip()
        if sf_key and not _is_placeholder_key(sf_key) and _openai_gateway_healthy(sf_key):
            cfg.recognition_engine = "openai"
            base = (os.environ.get("SILICONFLOW_BASE_URL")
                    or os.environ.get("OPENAI_BASE_URL")
                    or "https://api.siliconflow.cn/v1").strip()
            # 容错：有人会把 /chat/completions 一并填进 base_url，协议层会自动追加，需去重
            if base.endswith("/chat/completions"):
                base = base[:-len("/chat/completions")]
            cfg.openai_rec_base_url = base
            cfg.openai_rec_api_key = sf_key
            # 强制语义：模型解析不读 DB 旧值（env → 内置默认），管理台临时切换不跨重启
            cfg.openai_rec_model = (os.environ.get("SILICONFLOW_MODEL")
                                    or os.environ.get("OPENAI_MODEL")
                                    or "Qwen/Qwen2.5-VL-7B-Instruct")
            # 审核腿：SF DeepSeek 系（与识别 Qwen 系跨厂商异构），禁止回落 opencode
            cfg.audit_engine = "openai"
            cfg.openai_aud_base_url = base
            cfg.openai_aud_api_key = sf_key
            cfg.openai_aud_model = (os.environ.get("SILICONFLOW_AUDIT_MODEL")
                                    or "zai-org/GLM-4.5V")
            cfg.audit_model = cfg.openai_aud_model
            source = "SILICONFLOW/OPENAI（默认 provider，重启双腿强制装配）"
        elif ds_key and not _is_placeholder_key(ds_key):
            cfg.recognition_engine = "openai"
            base = (os.environ.get("DASHSCOPE_BASE_URL")
                    or "https://dashscope.aliyuncs.com/compatible-mode/v1")
            cfg.openai_rec_base_url = base
            cfg.openai_rec_api_key = ds_key
            cfg.openai_rec_model = (os.environ.get("QWEN_VL_MODEL")
                                    or cfg.openai_rec_model or "qwen3-vl-flash")
            # 审核腿降级：DashScope 文本模型；与识别腿同为 Qwen 家族，异构性弱化（日志提示）
            cfg.audit_engine = "openai"
            cfg.openai_aud_base_url = base
            cfg.openai_aud_api_key = ds_key
            cfg.openai_aud_model = (os.environ.get("DASHSCOPE_AUDIT_MODEL")
                                    or "qwen3-vl-plus")
            cfg.audit_model = cfg.openai_aud_model
            source = "DASHSCOPE_API_KEY（SiliconFlow 不可用，降级装配；降级期间双腿同家族）"
        else:
            log.info("[engine-env] .env 无可用真实密钥（SILICONFLOW_API_KEY/DASHSCOPE_API_KEY 均为空），保持现有引擎配置")
            return
        set_engine_config(cfg)
        log.info(f"[engine-env] 已从 .env {source}: 识别 {cfg.openai_rec_base_url} / {cfg.openai_rec_model}；审核 {cfg.openai_aud_model}")
    except Exception as e:
        log.warning(f"[engine-env] 启动密钥水合失败（不影响服务）: {e}")


def _openai_gateway_healthy(api_key: str) -> bool:
    """轻量健康检查：GET {OPENAI/SILICONFLOW base}/models，200 即健康；异常/超时返回 False。"""
    import urllib.request
    base = (os.environ.get("SILICONFLOW_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.siliconflow.cn/v1").strip()
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models",
                                     headers={"Authorization": "Bearer " + api_key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


# -------------------------------------------------------------
# 阶段 2：AI 指标 / 漏斗 / A/B 实验 helper
# -------------------------------------------------------------
def write_decision_log(receipt_id=None, supplier_id=None, experiment_id=None,
                       grp="control", engine="", model="", use_grey=0,
                       decision_type="extract", field_path="",
                       ai_value=None, user_value=None, adopted=None,
                       confidence=None, is_hallucination=None, extra=None):
    """写一条 AI 决策日志（extract / edit / audit / approve 四阶段皆可）。"""
    s = get_session()
    try:
        s.add(_DecisionLogRow(
            ts=now_iso(),
            receipt_id=int(receipt_id) if receipt_id is not None else None,
            supplier_id=int(supplier_id) if supplier_id is not None else None,
            experiment_id=int(experiment_id) if experiment_id is not None else None,
            grp=grp or "control",
            engine=engine or "",
            model=model or "",
            use_grey=int(use_grey or 0),
            decision_type=decision_type or "extract",
            field_path=field_path or "",
            ai_value=json.dumps(ai_value, ensure_ascii=False) if isinstance(ai_value, (dict, list)) else (str(ai_value) if ai_value is not None else None),
            user_value=json.dumps(user_value, ensure_ascii=False) if isinstance(user_value, (dict, list)) else (str(user_value) if user_value is not None else None),
            adopted=adopted,
            confidence=float(confidence) if confidence is not None else None,
            is_hallucination=is_hallucination,
            extra=json.dumps(extra, ensure_ascii=False) if isinstance(extra, (dict, list)) else (str(extra) if extra is not None else ""),
        ))
        s.commit()
    finally:
        s.close()


def write_user_event(event_type, account_id="", session_id="", receipt_id=None,
                     properties=None, grp=None, tenant_id=None):
    """写一条用户行为埋点（漏斗用）。"""
    t_id = (tenant_id or "").strip()
    if not t_id and receipt_id is not None:
        try:
            r = get_receipt_row(receipt_id)
            if r and getattr(r, "tenant_id", None):
                t_id = r.tenant_id
        except Exception:
            pass
    if not t_id:
        t_id = "default"
    s = get_session()
    try:
        s.add(_UserEventRow(
            ts=now_iso(),
            account_id=account_id or "",
            session_id=session_id or "",
            event_type=event_type or "",
            receipt_id=int(receipt_id) if receipt_id is not None else None,
            properties=json.dumps(properties, ensure_ascii=False) if isinstance(properties, dict) else "{}",
            grp=grp or None,
            tenant_id=t_id,
        ))
        s.commit()
    finally:
        s.close()


def _parse_json_safe(v):
    """容错 json.loads：None/空串 → None；非 dict/list → 原样返回。"""
    if not v:
        return None
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return v


def _zscore(p):
    """标准正态分布双侧 p-value → z-score（正）。用数学库手算，不依赖 scipy。"""
    import math
    # 用 Abramowitz & Stegun 26.2.17 近似 inverse CDF，然后取反求 z
    p = max(min(float(p), 1.0), 1e-12)
    p_half = p / 2.0
    if p_half >= 1.0:
        return 0.0
    # rational approximation for inverse normal CDF (Acklam, 2003)
    a = [-3.969683028665376e+01, 2.209460984245205e+02,
         -2.759285104469687e+02, 1.383577518672690e+02,
         -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02,
         -1.556989798598866e+02, 6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
         4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01,
         2.445134137142996e+00, 3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p_half < plow:
        q = math.sqrt(-2 * math.log(p_half))
        x = (((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    elif p_half > phigh:
        q = math.sqrt(-2 * math.log(1 - p_half))
        x = -(((((c[0]*q + c[1])*q + c[2])*q + c[3])*q + c[4])*q + c[5]) / \
            ((((d[0]*q + d[1])*q + d[2])*q + d[3])*q + 1)
    else:
        q = p_half - 0.5
        r = q * q
        x = (((((a[0]*r + a[1])*r + a[2])*r + a[3])*r + a[4])*r + a[5]) * q / \
            (((((b[0]*r + b[1])*r + b[2])*r + b[3])*r + b[4])*r + 1)
    return x


def _two_proportion_ztest(x1, n1, x2, n2):
    """两比例双侧 z 检验。返回 {z, p_value, diff}。
    x1/n1 = treatment, x2/n2 = control（treatment 成功率 - control 成功率）。
    """
    import math
    if n1 <= 0 or n2 <= 0:
        return {"z": 0.0, "p_value": 1.0, "diff": 0.0, "error": "样本不足"}
    p1 = x1 / n1
    p2 = x2 / n2
    p_pool = (x1 + x2) / (n1 + n2)
    se = math.sqrt(p_pool * (1 - p_pool) * (1.0 / n1 + 1.0 / n2)) if p_pool > 0 and p_pool < 1 else 1e-9
    z = (p1 - p2) / se
    # 双侧 p 值 = 2 * P(Z > |z|)
    abs_z = abs(z)
    # 标准正态 CDF
    t = 1.0 / (1.0 + 0.2316419 * abs_z)
    d = 0.3989422804014327  # 1/sqrt(2*pi)
    phi = d * math.exp(-0.5 * abs_z * abs_z)
    cdf = 1.0 - phi * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))))
    p_value = 2.0 * (1.0 - cdf) if abs_z > 0 else 1.0
    return {"z": z, "p_value": p_value, "diff": p1 - p2, "error": None}


def compute_metrics(rows, grp=None):
    """从决策日志行列表计算 6 项指标。返回 dict。

    rows: list of _DecisionLogRow 或 dict-like。
    指标：accuracy / hallucination_rate / edit_rate / audit_adoption_rate / trust_score / sample_size
    """
    total = len(rows)
    if total == 0:
        return {
            "accuracy": None, "hallucination_rate": None,
            "edit_rate": None, "audit_adoption_rate": None,
            "trust_score": None, "sample_size": 0,
        }
    acc_ok = 0
    halo_cnt = 0
    edit_cnt = 0
    audit_ok = 0
    audit_total = 0
    confs = []
    for r in rows:
        if r.decision_type == "extract":
            # accuracy: adopted=1 且 is_hallucination!=1
            if getattr(r, "adopted", None) == 1:
                acc_ok += 1
            # hallucination
            if getattr(r, "is_hallucination", None) == 1:
                halo_cnt += 1
            if r.confidence is not None:
                confs.append(float(r.confidence))
        elif r.decision_type in ("edit", "review"):
            # edit: user_value 与 ai_value 不同
            ai = getattr(r, "ai_value", None)
            uv = getattr(r, "user_value", None)
            if ai is not None and uv is not None and str(ai) != str(uv):
                edit_cnt += 1
            # audit: adopted=1
            if r.decision_type == "audit":
                audit_total += 1
                if getattr(r, "adopted", None) == 1:
                    audit_ok += 1
    accuracy = acc_ok / total
    hallucination_rate = halo_cnt / total
    edit_rate = edit_cnt / total
    audit_adoption_rate = (audit_ok / audit_total) if audit_total > 0 else None
    trust_score = (sum(confs) / len(confs)) if confs else None
    return {
        "accuracy": accuracy,
        "hallucination_rate": hallucination_rate,
        "edit_rate": edit_rate,
        "audit_adoption_rate": audit_adoption_rate,
        "trust_score": trust_score,
        "sample_size": total,
    }


def query_ai_metrics(granularity="global", granularity_id="", grp=None,
                     period_start=None, period_end=None,
                     engine=None, model=None):
    """按时间窗 + 粒度 + 分组聚合 AI 指标。返回 {metrics, sample_size}。"""
    s = get_session()
    try:
        q = s.query(_DecisionLogRow)
        # 时间窗
        if period_start:
            q = q.filter(_DecisionLogRow.ts >= str(period_start))
        if period_end:
            q = q.filter(_DecisionLogRow.ts <= str(period_end))
        # 分组过滤（不聚合，只筛）
        if grp:
            q = q.filter(_DecisionLogRow.grp == grp)
        if engine:
            q = q.filter(_DecisionLogRow.engine == engine)
        if model:
            q = q.filter(_DecisionLogRow.model == model)
        if granularity == "supplier" and granularity_id:
            q = q.filter(_DecisionLogRow.supplier_id == int(granularity_id))
        elif granularity == "experiment" and granularity_id:
            q = q.filter(_DecisionLogRow.experiment_id == int(granularity_id))
        rows = q.all()
        m = compute_metrics(rows, grp=grp)
        return {"metrics": m, "sample_size": m["sample_size"],
                "grouped_by": {"granularity": granularity, "granularity_id": granularity_id,
                               "grp": grp, "period_start": period_start, "period_end": period_end,
                               "engine": engine, "model": model}}
    finally:
        s.close()


def grey_compare_metrics(period_start=None, period_end=None):
    """灰测 vs 常规 对比：按 use_grey 分组算准确率/幻觉率/修正率。

    返回 {control, treatment, diff}:
      diff.accuracy_diff = treatment - control (百分点)
      当两组样本 >= 30 时返回显著性提示。
    """
    s = get_session()
    try:
        q = s.query(_DecisionLogRow)
        if period_start:
            q = q.filter(_DecisionLogRow.ts >= str(period_start))
        if period_end:
            q = q.filter(_DecisionLogRow.ts <= str(period_end))
        rows = q.all()
        ctrl = [r for r in rows if r.use_grey == 0]
        treat = [r for r in rows if r.use_grey == 1]
        m_ctrl = compute_metrics(ctrl)
        m_treat = compute_metrics(treat)

        def _safe(m, k):
            return m.get(k)

        diff = {}
        for k in ("accuracy", "hallucination_rate", "edit_rate"):
            a = _safe(m_treat, k)
            b = _safe(m_ctrl, k)
            if a is not None and b is not None:
                diff[k + "_diff"] = a - b
            else:
                diff[k + "_diff"] = None

        # 显著性：仅两组样本都 >=30 时给建议
        sig = None
        if m_ctrl["sample_size"] >= 30 and m_treat["sample_size"] >= 30:
            if diff.get("accuracy_diff") is not None and diff["accuracy_diff"] > 0.03:
                sig = "promote_candidate"   # 准确率差 >3pp
            elif diff.get("accuracy_diff") is not None and diff["accuracy_diff"] < -0.05:
                sig = "reject"              # 准确率差 < -5pp
            else:
                sig = "inconclusive"
        else:
            sig = "insufficient_sample"

        return {
            "control": m_ctrl,
            "treatment": m_treat,
            "diff": diff,
            "suggestion": sig,
        }
    finally:
        s.close()


def funnel_config():
    """返回漏斗配置（可配 JSON）。默认 6 步。"""
    return [
        {"step": "upload", "label": "上传", "event": "receipt_uploaded"},
        {"step": "parse_done", "label": "解析完成", "event": "receipt_parsed"},
        {"step": "edit", "label": "编辑", "event": "receipt_edited"},
        {"step": "audit_flag", "label": "审核标记", "event": "receipt_flagged"},
        {"step": "approve", "label": "审批", "event": "receipt_approved"},
        {"step": "inventory_in", "label": "入库", "event": "inventory_in"},
    ]


def query_funnel(period_start=None, period_end=None, grp=None,
                 config=None):
    """查询漏斗各步计数。返回 {steps: [{step, label, count, conv_from_prev}], bottleneck: step|None}。

    conv_from_prev 用 receipt_id 去重，保证每一步计数是该步内独立单据数（去重防止同一单据多事件）。
    """
    s = get_session()
    try:
        q = s.query(_UserEventRow)
        if period_start:
            q = q.filter(_UserEventRow.ts >= str(period_start))
        if period_end:
            q = q.filter(_UserEventRow.ts <= str(period_end))
        if grp:
            q = q.filter(_UserEventRow.grp == grp)
        raw = q.all()
        steps_cfg = config or funnel_config()

        # event → count（按 receipt_id 去重）
        step_counts = {}
        for sc in steps_cfg:
            ev = sc["event"]
            rids = set()
            for r in raw:
                if r.event_type == ev:
                    rids.add(r.receipt_id)
            step_counts[sc["step"]] = len(rids)

        out = []
        prev = None
        for sc in steps_cfg:
            c = step_counts[sc["step"]]
            if prev is None:
                conv = None
            else:
                conv = (c / prev) if prev > 0 else 0.0
            out.append({
                "step": sc["step"],
                "label": sc["label"],
                "event": sc["event"],
                "count": c,
                "conv_from_prev": conv,
            })
            prev = c

        # 找卡点：转化率最小且 > 0 的一步
        bottleneck = None
        best_conv = 1.0
        for entry in out:
            if entry["conv_from_prev"] is not None and entry["conv_from_prev"] < best_conv:
                best_conv = entry["conv_from_prev"]
                bottleneck = entry["step"]

        return {"steps": out, "bottleneck": bottleneck, "config": steps_cfg}
    finally:
        s.close()


def create_experiment(name, hypothesis, success_metric="accuracy",
                      guardrail_metrics=None, target_percent=50,
                      target_supplier_ids=None, min_sample=100):
    """创建 A/B 实验（draft）。返回 experiment_id。"""
    s = get_session()
    try:
        row = _ExperimentRow(
            name=name or "",
            hypothesis=hypothesis or "",
            success_metric=success_metric or "accuracy",
            guardrail_metrics=json.dumps(guardrail_metrics or [], ensure_ascii=False),
            status="draft",
            target_percent=int(target_percent or 50),
            target_supplier_ids=json.dumps(target_supplier_ids or [], ensure_ascii=False),
            min_sample=int(min_sample or 100),
            grey_snapshot=json.dumps(get_engine_config().model_dump(), ensure_ascii=False),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def start_experiment(experiment_id):
    """启动实验：draft → running。返回 experiment 行 dict。"""
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(experiment_id))
        if row is None:
            return None
        if row.status != "draft":
            return {"status": "error", "msg": f"当前状态 {row.status}，仅 draft 可启动"}
        row.status = "running"
        row.start_ts = now_iso()
        s.commit()
        s.refresh(row)
        return _row_to_exp_dict(row)
    finally:
        s.close()


def _row_to_exp_dict(row):
    return {
        "id": row.id, "name": row.name, "hypothesis": row.hypothesis,
        "success_metric": row.success_metric,
        "guardrail_metrics": _parse_json_safe(row.guardrail_metrics) or [],
        "status": row.status,
        "target_percent": row.target_percent,
        "target_supplier_ids": _parse_json_safe(row.target_supplier_ids) or [],
        "start_ts": row.start_ts, "end_ts": row.end_ts,
        "min_sample": row.min_sample,
        "conclusion": row.conclusion, "conclusion_reason": row.conclusion_reason,
        "concluded_by": row.concluded_by, "concluded_at": row.concluded_at,
    }


def conclude_experiment(experiment_id, min_sample=None, mde=0.05,
                        guardrail_metrics=None):
    """结题实验：z-test + 效应量 + 样本量 + 护栏。返回结题报告。"""
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(experiment_id))
        if row is None:
            return {"status": "error", "msg": "实验不存在"}
        if row.status not in ("running", "draft"):
            return {"status": "error", "msg": f"当前状态 {row.status}，仅 running/draft 可结题"}

        target_sample = int(min_sample or row.min_sample or 100)
        target_mde = float(mde or 0.05)
        guardrails = guardrail_metrics or (_parse_json_safe(row.guardrail_metrics) or [])

        # 按 experiment_id + grp 取决策日志
        q = s.query(_DecisionLogRow).filter(
            _DecisionLogRow.experiment_id == int(experiment_id))
        rows = q.all()
        ctrl = [r for r in rows if r.grp == "control"]
        treat = [r for r in rows if r.grp == "treatment"]

        metric = row.success_metric or "accuracy"
        m_ctrl = compute_metrics(ctrl)
        m_treat = compute_metrics(treat)

        # 主指标：取两组在 success_metric 上的值
        ctrl_val = m_ctrl.get(metric)
        treat_val = m_treat.get(metric)

        reasons = []
        verdict = "inconclusive"
        reject = False

        # 1) 样本量检查
        if m_ctrl["sample_size"] < target_sample or m_treat["sample_size"] < target_sample:
            reasons.append(f"样本量不足：control={m_ctrl['sample_size']} treatment={m_treat['sample_size']} < 要求={target_sample}")

        # 2) z-test
        z_info = {"p_value": 1.0, "z": 0.0, "diff": 0.0}
        p_value = 1.0
        effect_diff = 0.0
        if ctrl_val is not None and treat_val is not None:
            x1 = int(round(treat_val * m_treat["sample_size"]))
            x2 = int(round(ctrl_val * m_ctrl["sample_size"]))
            z_info = _two_proportion_ztest(x1, m_treat["sample_size"],
                                           x2, m_ctrl["sample_size"])
            p_value = z_info["p_value"]
            effect_diff = z_info["diff"]

        significant = p_value < 0.05
        big_enough = abs(effect_diff) > target_mde
        if significant and big_enough:
            if effect_diff > 0:
                verdict = "promote"
            else:
                verdict = "rollback"
                reject = True
        elif p_value >= 0.05:
            reasons.append(f"p_value={p_value:.4f} ≥ 0.05，不显著")
        elif abs(effect_diff) <= target_mde:
            reasons.append(f"效应量={effect_diff:.4f} 小于 MDE={target_mde}")

        # 3) 护栏检查：guardrail_metrics 中列出的指标在 treatment 中不能显著恶化
        guardrail_ok = True
        guardrail_notes = []
        for gm in guardrails:
            cv = m_ctrl.get(gm)
            tv = m_treat.get(gm)
            if cv is not None and tv is not None:
                # accuracy 类指标恶化 = tv < cv；其他（hallucination/edit）恶化 = tv > cv
                bad = (tv < cv) if gm == "accuracy" else (tv > cv)
                diff_pct = abs(tv - cv)
                if bad and diff_pct > 0.05:  # 护栏退化 > 5pp
                    guardrail_ok = False
                    guardrail_notes.append(f"{gm} 恶化：control={cv:.3f} treatment={tv:.3f}")
            else:
                guardrail_notes.append(f"{gm} 数据不足无法评估")

        if not guardrail_ok and verdict == "promote":
            verdict = "rollback"
            reject = True
            reasons.extend(["护栏指标未通过：" + "; ".join(guardrail_notes)])

        if not reasons:
            reasons.append("通过 z-test 与护栏检查")

        row.status = "concluded"
        row.conclusion = verdict
        row.conclusion_reason = json.dumps({
            "suggestion": verdict,
            "control_metrics": m_ctrl,
            "treatment_metrics": m_treat,
            "z_test": z_info,
            "effect_diff": effect_diff,
            "mde": target_mde,
            "sample_check": {
                "control": m_ctrl["sample_size"],
                "treatment": m_treat["sample_size"],
                "min_sample": target_sample,
                "passed": m_ctrl["sample_size"] >= target_sample and m_treat["sample_size"] >= target_sample,
            },
            "guardrail_check": {
                "passed": guardrail_ok,
                "details": guardrail_notes,
            },
            "reasons": reasons,
        }, ensure_ascii=False)
        row.concluded_by = "system"
        row.concluded_at = now_iso()
        s.commit()
        s.refresh(row)
        report = _row_to_exp_dict(row)
        report["z_test"] = z_info
        report["effect_diff"] = effect_diff
        report["reasons"] = reasons
        report["guardrail_notes"] = guardrail_notes
        return report
    finally:
        s.close()


def experiment_detail(experiment_id):
    """实验详情 + 分组指标 + 归属明细数。"""
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(experiment_id))
        if row is None:
            return None
        detail = _row_to_exp_dict(row)
        # 分组样本量与决策日志
        q = s.query(_DecisionLogRow).filter(
            _DecisionLogRow.experiment_id == int(experiment_id))
        all_rows = q.all()
        ctrl = [r for r in all_rows if r.grp == "control"]
        treat = [r for r in all_rows if r.grp == "treatment"]
        detail["groups"] = {
            "control": compute_metrics(ctrl),
            "treatment": compute_metrics(treat),
        }
        # 归属数
        assigns = s.query(_ExperimentAssignRow).filter(
            _ExperimentAssignRow.experiment_id == int(experiment_id)).all()
        detail["assignments"] = {
            "control": sum(1 for a in assigns if a.grp == "control"),
            "treatment": sum(1 for a in assigns if a.grp == "treatment"),
        }
        return detail
    finally:
        s.close()


def stop_experiment(experiment_id):
    """停止实验。"""
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(experiment_id))
        if row is None:
            return None
        row.status = "stopped"
        row.end_ts = now_iso()
        s.commit()
        return _row_to_exp_dict(row)
    finally:
        s.close()


# -------------------------------------------------------------
# 阶段 2：AI 决策日志 / 埋点 / A/B 实验
# -------------------------------------------------------------
def _safe_json(obj):
    """把任意对象序列化为 JSON 字符串，失败返回 '{}'。"""
    if obj is None:
        return "{}"
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return "{}"


def _parse_json(raw):
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def log_ai_decision(
    receipt_id=None, supplier_id=None, experiment_id=None, grp="control",
    engine="", model="", use_grey=0, decision_type="extract",
    field_path="", ai_value=None, user_value=None, adopted=None,
    confidence=None, is_hallucination=None, extra=None,
):
    """写一条 AI 决策日志。采纳/用户值/幻觉标记可留 NULL，approve 时回填。"""
    s = get_session()
    try:
        row = _DecisionLogRow(
            ts=now_iso(),
            receipt_id=int(receipt_id) if receipt_id else None,
            supplier_id=int(supplier_id) if supplier_id else None,
            experiment_id=int(experiment_id) if experiment_id else None,
            grp=str(grp or "control"),
            engine=str(engine or ""),
            model=str(model or ""),
            use_grey=int(use_grey or 0),
            decision_type=str(decision_type or "extract"),
            field_path=str(field_path or ""),
            ai_value=_safe_json(ai_value),
            user_value=_safe_json(user_value) if user_value is not None else None,
            adopted=int(adopted) if adopted is not None else None,
            confidence=float(confidence) if confidence is not None else None,
            is_hallucination=int(is_hallucination) if is_hallucination is not None else None,
            extra=_safe_json(extra),
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def list_ai_decisions(receipt_id):
    """U-2：查询单据的 AI 决策履历（只读）。

    按 (ts ASC, id ASC) 排序返回 dict 列表：
    id/ts/decision_type/engine/model/use_grey/field_path/ai_value/extra/tokens。
    最严格记忆落盘：extra 中含 {tokens_prompt, tokens_completion, tokens_total, cost_hkd, elapsed_ms, success, image_path, supplier, doc_form}
    用于 /api/receipt/{id} 详情与归档弹窗「AI 决策履历」块，API 层直接暴露 token 消耗。
    """
    if not receipt_id:
        return []
    s = get_session()
    try:
        rows = (
            s.query(_DecisionLogRow)
            .filter(_DecisionLogRow.receipt_id == int(receipt_id))
            .order_by(_DecisionLogRow.ts.asc(), _DecisionLogRow.id.asc())
            .all()
        )
        out = []
        for r in rows:
            # 解析 extra 结构化记忆（DB 可查）
            extra_dict = {}
            try:
                extra_dict = json.loads(r.extra) if r.extra else {}
                if not isinstance(extra_dict, dict):
                    extra_dict = {}
            except Exception:
                extra_dict = {}
            # 解析 ai_value 中的 token 冗余（兼容旧数据）
            ai_val_raw = r.ai_value or ""
            ai_tokens = {}
            try:
                ai_parsed = json.loads(ai_val_raw) if ai_val_raw and ai_val_raw.strip().startswith("{") else None
                if isinstance(ai_parsed, dict):
                    ai_tokens = {k: ai_parsed.get(k) for k in ("tokens_total", "cost_hkd", "elapsed_ms", "success") if k in ai_parsed}
            except Exception:
                ai_tokens = {}
            # 统一 tokens 来源：优先 extra，其次 ai_value
            tokens_total = extra_dict.get("tokens_total", ai_tokens.get("tokens_total", 0))
            cost_hkd = extra_dict.get("cost_hkd", ai_tokens.get("cost_hkd", 0))
            elapsed_ms = extra_dict.get("elapsed_ms", ai_tokens.get("elapsed_ms", {}))
            success = extra_dict.get("success", ai_tokens.get("success"))
            out.append({
                "id": r.id,
                "ts": r.ts or "",
                "decision_type": r.decision_type or "",
                "engine": r.engine or "",
                "model": r.model or "",
                "use_grey": r.use_grey or 0,
                "field_path": r.field_path or "",
                "ai_value": r.ai_value or "",
                "extra": r.extra or "",
                # 最严格：直接暴露 token 与耗时（前端 grep tokens 即可）
                "tokens_total": tokens_total,
                "tokens_prompt": extra_dict.get("tokens_prompt", 0),
                "tokens_completion": extra_dict.get("tokens_completion", 0),
                "cost_hkd": cost_hkd,
                "elapsed_ms": elapsed_ms,
                "success": success,
                "image_path": extra_dict.get("image_path", ""),
                "supplier": extra_dict.get("supplier", ""),
                "doc_form": extra_dict.get("doc_form", ""),
            })
        return out
    finally:
        s.close()


def list_experiment_decision_rows(experiment_id):
    """T9：按实验取全部决策行（extra 已解析为 dict），供守护指标计算。

    返回 [{grp, decision_type, receipt_id, extra}]，id 正序。
    """
    if not experiment_id:
        return []
    s = get_session()
    try:
        rows = (
            s.query(_DecisionLogRow)
            .filter(_DecisionLogRow.experiment_id == int(experiment_id))
            .order_by(_DecisionLogRow.id.asc())
            .all()
        )
        return [{
            "grp": r.grp or "control",
            "decision_type": r.decision_type or "",
            "receipt_id": r.receipt_id,
            "extra": _parse_json(r.extra),
        } for r in rows]
    except (TypeError, ValueError):
        return []
    finally:
        s.close()


def log_user_event(account_id="", session_id="", event_type="",
                   receipt_id=None, properties=None, grp=None, tenant_id=None):
    """前端埋点事件。"""
    t_id = (tenant_id or "").strip()
    if not t_id and receipt_id is not None:
        try:
            r = get_receipt_row(receipt_id)
            if r and getattr(r, "tenant_id", None):
                t_id = r.tenant_id
        except Exception:
            pass
    if not t_id:
        t_id = "default"
    s = get_session()
    try:
        row = _UserEventRow(
            ts=now_iso(),
            account_id=str(account_id or ""),
            session_id=str(session_id or ""),
            event_type=str(event_type or ""),
            receipt_id=int(receipt_id) if receipt_id else None,
            properties=_safe_json(properties),
            grp=str(grp) if grp else None,
            tenant_id=t_id,
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def get_funnel(name="upload_to_inventory", groups=("control", "treatment"),
               period_days=7):
    """计算漏斗：各步骤计数 + 转化率 + 累计转化率。

    name: 保留以便扩展；目前只支持 upload_to_inventory
    groups: ('control','treatment') 或 ('all',) 全局聚合
    """
    from datetime import timedelta
    end_ts = db_now = now_iso()
    start_ts = (datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
                if "+" in end_ts or "Z" in end_ts
                else datetime.fromisoformat(end_ts)).isoformat()
    start = datetime.fromisoformat(end_ts.replace("Z", ""))
    start_ts = (start - timedelta(days=int(period_days or 7))).isoformat()

    steps = [
        ("upload", ("upload", "receipt_uploaded")),
        ("ocr_parse_started", ("ocr_parse_started",)),
        ("ocr_parsed", ("parse_done", "ocr_parsed")),
        ("edit", ("save_edited", "receipt_review_submitted")),
        ("approve", ("approve", "receipt_approved")),
    ]

    def _compute(grp_label):
        s = get_session()
        try:
            q = s.query(_UserEventRow).filter(_UserEventRow.ts >= start_ts)
            if grp_label != "all":
                q = q.filter(_UserEventRow.grp == grp_label)
            counts = {}
            for e in q.all():
                et = e.event_type
                counts[et] = counts.get(et, 0) + 1
            total = counts.get("upload", 0) + counts.get("receipt_uploaded", 0) or 1
            out_steps = []
            for step_key, event_keys in steps:
                c = sum(counts.get(k, 0) for k in event_keys)
                conv = round(c / total, 4) if total else 0.0
                out_steps.append({
                    "step": step_key,
                    "event": event_keys[0],
                    "count": c,
                    "conversion_from_upload": conv,
                })
            return {"group": grp_label, "period_start": start_ts,
                    "period_end": end_ts, "steps": out_steps,
                    "sample_size": counts.get("upload", 0) + counts.get("receipt_uploaded", 0)}
        finally:
            s.close()

    results = [_compute(g) for g in (groups or ("all",))]
    low_confidence = any(r["sample_size"] < 30 for r in results)
    return {
        "funnel_name": name,
        "period_days": int(period_days or 7),
        "groups": results,
        "low_confidence": low_confidence,
        "step_definition": [s[0] for s in steps],
    }


def create_experiment(name, hypothesis="", success_metric="accuracy",
                      guardrail_metrics=None, target_percent=50,
                      target_supplier_ids=None, min_sample=100):
    s = get_session()
    try:
        row = _ExperimentRow(
            name=str(name),
            hypothesis=str(hypothesis or ""),
            success_metric=str(success_metric or "accuracy"),
            guardrail_metrics=_safe_json(guardrail_metrics or []),
            target_percent=int(target_percent or 50),
            target_supplier_ids=_safe_json(target_supplier_ids or []),
            min_sample=int(min_sample or 100),
            status="draft",
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"id": row.id, "name": row.name, "status": row.status}
    finally:
        s.close()


def start_experiment(exp_id, grey_snapshot=None):
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(exp_id))
        if row is None:
            return None
        row.status = "running"
        row.start_ts = now_iso()
        row.grey_snapshot = _safe_json(grey_snapshot or {})
        s.commit()
        s.refresh(row)
        return _exp_to_dict(row)
    finally:
        s.close()


def stop_experiment(exp_id):
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(exp_id))
        if row is None:
            return None
        row.status = "stopped"
        row.end_ts = now_iso()
        s.commit()
        s.refresh(row)
        return _exp_to_dict(row)
    finally:
        s.close()


def conclude_experiment(exp_id, conclusion, reason=None, by=None,
                        control_accuracy=None, treatment_accuracy=None,
                        control_hallucination=None, treatment_hallucination=None,
                        control_edit=None, treatment_edit=None,
                        control_sample=None, treatment_sample=None,
                        p_value=None, effect_size=None):
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(exp_id))
        if row is None:
            return None
        row.status = "concluded"
        row.end_ts = now_iso()
        row.conclusion = str(conclusion or "inconclusive")
        row.conclusion_reason = str(reason or "")
        row.concluded_by = str(by or "")
        row.concluded_at = now_iso()
        # 把结题快照塞进 grey_snapshot（对齐阶段 0 回滚语义）
        summary = {
            "conclusion": row.conclusion,
            "conclusion_reason": row.conclusion_reason,
            "p_value": p_value,
            "effect_size": effect_size,
            "control_accuracy": control_accuracy,
            "treatment_accuracy": treatment_accuracy,
            "control_hallucination": control_hallucination,
            "treatment_hallucination": treatment_hallucination,
            "control_edit_rate": control_edit,
            "treatment_edit_rate": treatment_edit,
            "control_sample": control_sample,
            "treatment_sample": treatment_sample,
        }
        snap = _parse_json(row.grey_snapshot)
        snap["conclusion_summary"] = summary
        row.grey_snapshot = _safe_json(snap)
        s.commit()
        s.refresh(row)
        return _exp_to_dict(row)
    finally:
        s.close()


def _exp_to_dict(row):
    if row is None:
        return None
    return {
        "id": row.id, "name": row.name, "hypothesis": row.hypothesis or "",
        "success_metric": row.success_metric or "accuracy",
        "guardrail_metrics": _parse_json(row.guardrail_metrics),
        "status": row.status, "target_percent": row.target_percent,
        "target_supplier_ids": _parse_json(row.target_supplier_ids),
        "start_ts": row.start_ts, "end_ts": row.end_ts,
        "min_sample": row.min_sample,
        "conclusion": row.conclusion,
        "conclusion_reason": row.conclusion_reason,
        "concluded_by": row.concluded_by,
        "concluded_at": row.concluded_at,
        "grey_snapshot": _parse_json(row.grey_snapshot),
    }


def get_experiment(exp_id):
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(exp_id))
        return _exp_to_dict(row)
    finally:
        s.close()


def list_experiments():
    s = get_session()
    try:
        rows = s.query(_ExperimentRow).order_by(_ExperimentRow.id.desc()).all()
        return [_exp_to_dict(r) for r in rows]
    finally:
        s.close()


# -------------------------------------------------------------
# T9（Gap C3 + D4）：实验守护——事件留痕 / 方向性快照 / 冻结
# -------------------------------------------------------------
def write_guardrail_event(experiment_id, action, reason="", metrics=None):
    """守护动作留痕（rollback|freeze|alert），metrics 记触发时指标快照。"""
    s = get_session()
    try:
        row = _GuardrailEventRow(
            ts=now_iso(),
            experiment_id=int(experiment_id) if experiment_id else None,
            action=str(action or ""),
            reason=str(reason or "")[:1000],
            metrics_json=_safe_json(metrics or {}),
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def list_guardrail_events(experiment_id=None):
    """守护事件列表（可按实验过滤），id 倒序。"""
    if _GuardrailEventRow is None:
        return []
    s = get_session()
    try:
        q = s.query(_GuardrailEventRow)
        if experiment_id is not None:
            q = q.filter(_GuardrailEventRow.experiment_id == int(experiment_id))
        rows = q.order_by(_GuardrailEventRow.id.desc()).all()
        return [{
            "id": r.id, "ts": r.ts or "",
            "experiment_id": r.experiment_id,
            "action": r.action or "", "reason": r.reason or "",
            "metrics": _parse_json(r.metrics_json),
        } for r in rows]
    finally:
        s.close()


def write_experiment_directional_snapshot(exp_id, avg_output_tokens=None,
                                          avg_tool_calls=None,
                                          avg_retry_rounds=None,
                                          accuracy=None, sample_size=None,
                                          grp="treatment"):
    """T9：写入一期实验粒度方向性快照（granularity='experiment'）。"""
    s = get_session()
    try:
        now = now_iso()
        row = _SnapshotRow(
            period_start="", period_end=now,
            granularity="experiment", granularity_id=str(exp_id),
            grp=str(grp) if grp else None,
            accuracy=accuracy, sample_size=sample_size or 0,
            avg_output_tokens=avg_output_tokens,
            avg_tool_calls=avg_tool_calls,
            avg_retry_rounds=avg_retry_rounds,
            computed_at=now,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return {"id": row.id, "computed_at": row.computed_at,
                "avg_output_tokens": row.avg_output_tokens,
                "avg_tool_calls": row.avg_tool_calls,
                "avg_retry_rounds": row.avg_retry_rounds}
    finally:
        s.close()


def list_experiment_snapshots(exp_id, limit=3):
    """实验粒度方向性快照最近 N 期（时间正序返回，便于看连续漂移）。"""
    if _SnapshotRow is None:
        return []
    s = get_session()
    try:
        rows = (
            s.query(_SnapshotRow)
            .filter(_SnapshotRow.granularity == "experiment",
                    _SnapshotRow.granularity_id == str(exp_id))
            .order_by(_SnapshotRow.id.desc())
            .limit(int(limit or 3))
            .all()
        )
        out = [{
            "id": r.id, "computed_at": r.computed_at or "",
            "avg_output_tokens": r.avg_output_tokens,
            "avg_tool_calls": r.avg_tool_calls,
            "avg_retry_rounds": r.avg_retry_rounds,
            "accuracy": r.accuracy, "sample_size": r.sample_size,
        } for r in rows]
        out.reverse()
        return out
    finally:
        s.close()


def freeze_experiment_rollback(exp_id, reason="", by="guardian"):
    """T9：守护触发回滚时冻结实验 —— status='stopped' + conclusion='rollback'。

    与人工 conclude 区分：守护冻结保留 stopped 状态（不进入正常结题口径），
    conclusion='rollback' 供看板与审计识别。返回实验 dict；不存在返回 None。
    """
    s = get_session()
    try:
        row = s.get(_ExperimentRow, int(exp_id))
        if row is None:
            return None
        row.status = "stopped"
        row.end_ts = now_iso()
        row.conclusion = "rollback"
        row.conclusion_reason = str(reason or "")[:1000]
        row.concluded_by = str(by or "guardian")
        row.concluded_at = now_iso()
        s.commit()
        s.refresh(row)
        return _exp_to_dict(row)
    except (TypeError, ValueError):
        return None
    finally:
        s.close()


def add_assignment(experiment_id, receipt_id, grp="control"):
    s = get_session()
    try:
        row = _ExperimentAssignRow(
            experiment_id=int(experiment_id),
            receipt_id=int(receipt_id),
            grp=str(grp),
            assigned_at=now_iso(),
        )
        s.merge(row)  # 复合主键：已存在则更新
        s.commit()
        return True
    except Exception:
        s.rollback()
        return False
    finally:
        s.close()


def get_experiment_assignments(exp_id):
    s = get_session()
    try:
        rows = s.query(_ExperimentAssignRow).filter(
            _ExperimentAssignRow.experiment_id == int(exp_id)).all()
        return [{"receipt_id": r.receipt_id, "grp": r.grp,
                 "assigned_at": r.assigned_at} for r in rows]
    finally:
        s.close()


def get_experiment_metrics(exp_id):
    """计算实验两组的 accuracy/hallucination_rate/edit_rate 并做双比例 z 检验。

    基于 ai_decision_log 中 experiment_id 匹配的记录。
    采纳判定（adopted）用于 accuracy：adopted=1 为正确。
    幻觉（is_hallucination）直接统计。
    edit_rate：user_value 非空（用户改过）。
    """
    from math import sqrt

    s = get_session()
    try:
        rows = s.query(_DecisionLogRow).filter(
            _DecisionLogRow.experiment_id == int(exp_id)).all()
    finally:
        s.close()

    groups = {"control": [], "treatment": []}
    for r in rows:
        g = str(r.grp) if r.grp else "control"
        if g in groups:
            groups[g].append(r)

    def _stats(recs):
        n = len(recs)
        if n == 0:
            return {"n": 0, "accuracy": None, "hallucination_rate": None,
                    "edit_rate": None}
        adopted = sum(1 for r in recs if r.adopted == 1)
        hallu = sum(1 for r in recs if r.is_hallucination == 1)
        edited = sum(1 for r in recs if r.user_value and r.user_value.strip() not in ("", "{}"))
        return {
            "n": n,
            "accuracy": round(adopted / n, 6) if n else None,
            "hallucination_rate": round(hallu / n, 6) if n else None,
            "edit_rate": round(edited / n, 6) if n else None,
        }

    c = _stats(groups["control"])
    t = _stats(groups["treatment"])

    def _z_test(pc, pt, nc, nt):
        """双比例 z 检验：H0: p_control == p_treatment。
        返回 (z, p_value_approx)。p_value 用标准正态双侧近似。
        样本不足或分母为 0 返回 None。
        """
        if nc is None or nt is None or nc == 0 or nt == 0:
            return None
        if pc is None or pt is None:
            return None
        n1, n2 = float(nc), float(nt)
        p1, p2 = float(pc), float(pt)
        p_pool = (p1 * n1 + p2 * n2) / (n1 + n2)
        denom_sq = p_pool * (1 - p_pool) * (1 / n1 + 1 / n2)
        if denom_sq <= 0:
            return None
        denom = sqrt(denom_sq)
        z = (p2 - p1) / denom
        # 近似正态 CDF（Horner）
        def _phi(x):
            ax = abs(x)
            a1 = 0.254829592; a2 = -0.284496736; a3 = 1.421413741
            a4 = -1.453152027; a5 = 1.061405429; p = 0.3275911
            t = 1.0 / (1.0 + p * ax)
            y = 1.0 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * _math.exp(-ax * ax / 2.0)
            return 0.5 * y if x >= 0 else 1.0 - 0.5 * y
        pval = 2.0 * (1.0 - _phi(z))
        return {"z": round(z, 6), "p_value": round(pval, 8),
                "effect_size_pp": round((p2 - p1) * 100, 4)}

    z_acc = _z_test(c["accuracy"], t["accuracy"], c["n"], t["n"])
    z_hall = _z_test(c["hallucination_rate"], t["hallucination_rate"],
                     c["n"], t["n"])
    z_edit = _z_test(c["edit_rate"], t["edit_rate"], c["n"], t["n"])

    return {
        "experiment_id": int(exp_id),
        "control": c,
        "treatment": t,
        "tests": {
            "accuracy": z_acc,
            "hallucination_rate": z_hall,
            "edit_rate": z_edit,
        },
        "low_confidence": min(c["n"], t["n"]) < 30,
    }


def get_grey_compare(period_days=30, min_sample=30):
    """按 use_grey 分组（0=常规，1=灰测）算 accuracy/hallucination_rate/edit_rate。

    数据不足：返回 low_confidence=true + 空指标，不 500。
    """
    from datetime import timedelta
    end_ts = now_iso()
    start = datetime.fromisoformat(end_ts.replace("Z", ""))
    start_ts = (start - timedelta(days=int(period_days or 30))).isoformat()

    s = get_session()
    try:
        rows = s.query(_DecisionLogRow).filter(_DecisionLogRow.ts >= start_ts).all()
    finally:
        s.close()

    groups = {"control": [], "treatment": []}
    for r in rows:
        g = "treatment" if int(r.use_grey or 0) == 1 else "control"
        if g in groups:
            groups[g].append(r)

    def _stats(recs):
        n = len(recs)
        if n == 0:
            return {"n": 0, "accuracy": None, "hallucination_rate": None,
                    "edit_rate": None}
        adopted = sum(1 for r in recs if r.adopted == 1)
        hallu = sum(1 for r in recs if r.is_hallucination == 1)
        edited = sum(1 for r in recs if r.user_value and r.user_value.strip() not in ("", "{}"))
        return {
            "n": n,
            "accuracy": round(adopted / n, 6) if n else None,
            "hallucination_rate": round(hallu / n, 6) if n else None,
            "edit_rate": round(edited / n, 6) if n else None,
        }

    c = _stats(groups["control"])
    t = _stats(groups["treatment"])

    def _diff(tc, cc):
        if tc is None or cc is None:
            return None
        return round((tc - cc) * 100, 4)

    diff = {
        "accuracy_pp": _diff(t["accuracy"], c["accuracy"]),
        "hallucination_rate_pp": _diff(t["hallucination_rate"], c["hallucination_rate"]),
        "edit_rate_pp": _diff(t["edit_rate"], c["edit_rate"]),
    }

    low_confidence = min(c["n"], t["n"]) < int(min_sample or 30)
    if low_confidence:
        diff = {k: (None if v is not None else None) for k, v in diff.items()}

    return {
        "period_start": start_ts, "period_end": end_ts,
        "control": c, "treatment": t, "diff_pp": diff,
        "low_confidence": low_confidence,
        "min_sample": int(min_sample or 30),
    }


def get_ai_metrics(period_days=7, granularity="global",
                   granularity_id=None, grp=None, min_sample=30):
    """AI 指标体系主查询：按分组聚合并计算 accuracy/hallucination_rate/edit_rate/
    audit_adoption_rate/trust_score。支持维度：global/supplier/engine/model/experiment。
    """
    from datetime import timedelta
    end_ts = now_iso()
    start = datetime.fromisoformat(end_ts.replace("Z", ""))
    start_ts = (start - timedelta(days=int(period_days or 7))).isoformat()

    s = get_session()
    try:
        q = s.query(_DecisionLogRow).filter(_DecisionLogRow.ts >= start_ts)
    finally:
        s.close()

    # 重新开 session 拿数据（避免闭包坑）
    s = get_session()
    try:
        rows = q.all()
    finally:
        s.close()

    def _group_key(r):
        g = granularity or "global"
        if g == "supplier":
            return str(r.supplier_id) if r.supplier_id else "__none__"
        if g == "engine":
            return str(r.engine) if r.engine else "__none__"
        if g == "model":
            return str(r.model) if r.model else "__none__"
        if g == "experiment":
            return str(r.experiment_id) if r.experiment_id else "__none__"
        return "global"

    buckets = {}
    for r in rows:
        if grp and str(r.grp) != str(grp):
            continue
        k = _group_key(r)
        buckets.setdefault(k, []).append(r)

    def _stats(recs):
        n = len(recs)
        if n == 0:
            return {"n": 0, "accuracy": None, "hallucination_rate": None,
                    "edit_rate": None, "audit_adoption_rate": None,
                    "trust_score": None}
        adopted = sum(1 for r in recs if r.adopted == 1)
        hallu = sum(1 for r in recs if r.is_hallucination == 1)
        edited = sum(1 for r in recs if r.user_value and r.user_value.strip() not in ("", "{}"))
        audit_rows = [r for r in recs if r.decision_type == "audit"]
        audit_adopted = sum(1 for r in audit_rows if r.adopted == 1)
        confs = [r.confidence for r in recs if r.confidence is not None]
        audit_confs = [r.confidence for r in audit_rows if r.confidence is not None]
        trust = None
        if audit_confs:
            trust = round(sum(audit_confs) / len(audit_confs), 4)
        return {
            "n": n,
            "accuracy": round(adopted / n, 6) if n else None,
            "hallucination_rate": round(hallu / n, 6) if n else None,
            "edit_rate": round(edited / n, 6) if n else None,
            "audit_adoption_rate": round(audit_adopted / len(audit_rows), 6)
            if audit_rows else None,
            "trust_score": trust,
            "avg_confidence": round(sum(confs) / len(confs), 4) if confs else None,
        }

    breakdown = {}
    for k, recs in buckets.items():
        st = _stats(recs)
        st["low_confidence"] = st["n"] < int(min_sample or 30)
        breakdown[k] = st

    all_rows = sum(buckets.values(), [])
    total = _stats(all_rows)
    total["low_confidence"] = total["n"] < int(min_sample or 30)
    return {
        "period_start": start_ts, "period_end": end_ts,
        "granularity": granularity, "granularity_id": granularity_id,
        "grp": grp,
        "total": total,
        "breakdown": breakdown,
        "min_sample": int(min_sample or 30),
    }


def snapshot_ai_metrics(period_days=1, granularity="global",
                        granularity_id=None, grp=None):
    """计算一次快照并写入 ai_metric_snapshot 表。"""
    s = get_session()
    try:
        from datetime import timedelta
        end_ts = now_iso()
        start = datetime.fromisoformat(end_ts.replace("Z", ""))
        ps = (start - timedelta(days=int(period_days or 1))).isoformat()

        q = s.query(_DecisionLogRow).filter(_DecisionLogRow.ts >= ps)
        if grp:
            q = q.filter(_DecisionLogRow.grp == str(grp))
        rows = q.all()
    finally:
        s.close()

    def _stats(recs):
        n = len(recs)
        if n == 0:
            return {}
        adopted = sum(1 for r in recs if r.adopted == 1)
        hallu = sum(1 for r in recs if r.is_hallucination == 1)
        edited = sum(1 for r in recs if r.user_value and r.user_value.strip() not in ("", "{}"))
        audit_rows = [r for r in recs if r.decision_type == "audit"]
        audit_adopted = sum(1 for r in audit_rows if r.adopted == 1)
        audit_confs = [r.confidence for r in audit_rows if r.confidence is not None]
        trust = round(sum(audit_confs) / len(audit_confs), 4) if audit_confs else None
        return {
            "accuracy": round(adopted / n, 6),
            "hallucination_rate": round(hallu / n, 6),
            "edit_rate": round(edited / n, 6),
            "audit_adoption_rate": round(audit_adopted / len(audit_rows), 6)
            if audit_rows else None,
            "trust_score": trust,
            "sample_size": n,
        }

    snap = _stats(rows)
    snap["period_start"] = ps
    snap["period_end"] = end_ts
    snap["granularity"] = str(granularity or "global")
    snap["granularity_id"] = str(granularity_id or "")
    snap["grp"] = str(grp) if grp else None
    snap["computed_at"] = end_ts

    s = get_session()
    try:
        row = _SnapshotRow(**{k: snap.get(k) for k in
                              ("period_start", "period_end", "granularity",
                               "granularity_id", "grp", "accuracy",
                               "hallucination_rate", "edit_rate",
                               "audit_adoption_rate", "trust_score",
                               "sample_size", "computed_at") if k in snap})
        s.add(row)
        s.commit()
        return snap
    finally:
        s.close()


# -------------------------------------------------------------
# FR-8/FR-9 反馈飞轮：receipt_feedback CRUD + 三次连续提炼判定
# -------------------------------------------------------------
def upsert_receipt_feedback(receipt_id, like=None, comment="", item_index=None,
                            tenant_id="default", vendor="", quality_warnings=None):
    """写入/更新反馈。幂等：同 receipt_id+item_index 覆盖。

    like: 1=点赞, -1=点踩, None/0=未表态
    quality_warnings: list[str] 快照
    返回 row dict。
    """
    s = get_session()
    try:
        # 归一 like
        if like is True:
            like_val = 1
        elif like is False:
            like_val = -1
        elif isinstance(like, str):
            lv = like.strip().lower()
            if lv in ("1", "like", "up", "thumbs_up", "true"):
                like_val = 1
            elif lv in ("-1", "dislike", "down", "thumbs_down", "false"):
                like_val = -1
            else:
                try:
                    like_val = int(lv)
                    like_val = 1 if like_val > 0 else (-1 if like_val < 0 else None)
                except Exception:
                    like_val = None
        elif isinstance(like, (int, float)):
            like_val = 1 if int(like) > 0 else (-1 if int(like) < 0 else None)
        else:
            like_val = None

        # comment 净化：截断 2000 字，防注入（入库前已转义由前端负责）
        comment_str = str(comment or "")[:2000]
        qw_json = json.dumps(quality_warnings or [], ensure_ascii=False)

        # 查找已存在（同 receipt + item_index 去重）
        q = s.query(_ReceiptFeedbackRow).filter(
            _ReceiptFeedbackRow.receipt_id == int(receipt_id)
        )
        if item_index is not None:
            q = q.filter(_ReceiptFeedbackRow.item_index == int(item_index))
        else:
            q = q.filter(_ReceiptFeedbackRow.item_index.is_(None))
        existing = q.first()

        if existing:
            existing.like = like_val
            existing.comment = comment_str
            existing.quality_warnings_json = qw_json
            existing.tenant_id = tenant_id or "default"
            if vendor:
                existing.vendor = vendor
            existing.updated_at = now_iso()
            s.commit()
            s.refresh(existing)
            row = existing
        else:
            row = _ReceiptFeedbackRow(
                receipt_id=int(receipt_id),
                item_index=int(item_index) if item_index is not None else None,
                like=like_val,
                comment=comment_str,
                quality_warnings_json=qw_json,
                tenant_id=tenant_id or "default",
                vendor=vendor or "",
                created_at=now_iso(),
                updated_at=now_iso(),
            )
            s.add(row)
            s.commit()
            s.refresh(row)

        return {
            "id": row.id,
            "receipt_id": row.receipt_id,
            "item_index": row.item_index,
            "like": row.like,
            "comment": row.comment,
            "quality_warnings": json.loads(row.quality_warnings_json or "[]"),
            "tenant_id": row.tenant_id,
            "vendor": row.vendor,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }
    finally:
        s.close()


def list_receipt_feedbacks(receipt_id=None, vendor=None, tenant_id=None):
    """查询反馈。支持按 receipt_id / vendor / tenant 过滤。"""
    s = get_session()
    try:
        q = s.query(_ReceiptFeedbackRow)
        if receipt_id is not None:
            q = q.filter(_ReceiptFeedbackRow.receipt_id == int(receipt_id))
        if vendor:
            q = q.filter(_ReceiptFeedbackRow.vendor == str(vendor))
        if tenant_id:
            q = q.filter(_ReceiptFeedbackRow.tenant_id == str(tenant_id))
        rows = q.order_by(_ReceiptFeedbackRow.id.desc()).all()
        out = []
        for r in rows:
            out.append({
                "id": r.id,
                "receipt_id": r.receipt_id,
                "item_index": r.item_index,
                "like": r.like,
                "comment": r.comment,
                "quality_warnings": json.loads(r.quality_warnings_json or "[]"),
                "tenant_id": r.tenant_id,
                "vendor": r.vendor,
                "created_at": r.created_at,
                "updated_at": r.updated_at,
            })
        return out
    finally:
        s.close()


def count_vendor_feedbacks(vendor, tenant_id="default"):
    """统计某供应商在某租户下的反馈数量与点踩次数。"""
    s = get_session()
    try:
        q = s.query(_ReceiptFeedbackRow).filter(
            _ReceiptFeedbackRow.vendor == str(vendor),
            _ReceiptFeedbackRow.tenant_id == str(tenant_id or "default"),
        )
        rows = q.all()
        total = len(rows)
        dislike = sum(1 for r in rows if r.like == -1)
        like = sum(1 for r in rows if r.like == 1)
        return {"total": total, "dislike": dislike, "like": like}
    finally:
        s.close()


def should_distill_vendor_memory(vendor, tenant_id="default", threshold=None):
    """FR-9 判定：同供应商同租户连续 N 次点踩（dislike）触发提炼。

    阈值经 settings_service 键 'feedback_distill_threshold' 实时读取
    （缺省 FEEDBACK_DISTILL_THRESHOLD=3），可由 threshold 参数覆盖。
    语义：最近 N 条反馈均为点踩，认为需要沉淀为供应商记忆。
    返回 True 需调用 rag 沉淀。
    """
    from app.models import FEEDBACK_DISTILL_THRESHOLD

    if threshold:
        n = int(threshold)
    else:
        from app.services import settings_service  # 局部引入避免循环依赖
        n = settings_service.get_int("feedback_distill_threshold",
                                     FEEDBACK_DISTILL_THRESHOLD)
    s = get_session()
    try:
        rows = s.query(_ReceiptFeedbackRow).filter(
            _ReceiptFeedbackRow.vendor == str(vendor),
            _ReceiptFeedbackRow.tenant_id == str(tenant_id or "default"),
        ).order_by(_ReceiptFeedbackRow.id.desc()).limit(n).all()
        if len(rows) < n:
            return False
        for r in rows:
            if r.like != -1:
                return False
        return True
    finally:
        s.close()


# -------------------------------------------------------------
# Gap A5（T6）：线上低置信样本回流为评测候选
# -------------------------------------------------------------
EVAL_CANDIDATE_REASONS = ("low_confidence", "gate_reject", "user_edit", "audit_discrepancy")
EVAL_CANDIDATE_STATUSES = ("pending", "promoted_to_val", "promoted_to_test", "rejected")

# L3/T6 候选池卫生：pending 候选总量上限，防止线上信号把候选池刷爆。
# 超限时 create_eval_candidate 返回既有错误形态 (None, False)，不抛异常不阻断主链路。
# T10 收口：本常量仅作 settings 缺省值，运行时值经 settings_service 键
# 'eval_candidate_max_pending' 实时读取（管理台改后无需重启）。
EVAL_CANDIDATE_MAX_PENDING = 500


def _receipt_image_sha1(s, receipt_id):
    """计算单据原图文件 sha1（L3 候选池去重，复用 manifest src_sha1 思路）。

    图片缺失/读取失败返回 ""（不阻断建候选，仅跳过去重比对）。
    """
    import hashlib
    try:
        rc = s.get(_ReceiptRow, int(receipt_id)) if _ReceiptRow is not None else None
        path = getattr(rc, "image_path", "") if rc is not None else ""
        if not path or not os.path.exists(path):
            return ""
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except Exception:
        return ""


def create_eval_candidate(receipt_id, reason, tenant_id=None, doc_form="",
                          confidence=None, ai_candidate=None, note=""):
    """创建评测候选（每单每 reason 幂等）。

    同 receipt_id 同 reason 已存在（任意状态）时返回 (既有 id, False)，不重复建。
    tenant_id 缺省时回读单据归属；均无则落 'default'。
    返回 (candidate_id, created)。

    L3 候选池卫生：
    - pending 候选总量达上限时拒绝新建，返回 (None, False)
      （既有错误形态，不抛异常；supervisor 钩子侧负责 warning）
      上限经 settings_service 键 'eval_candidate_max_pending' 实时读取
    - 原图 sha1 相同且 reason 相同的 pending 候选已存在时跳过不重复建，
      返回 (既有候选 id, False)；sha1 记入 ai_candidate_json.src_sha1
    """
    if receipt_id is None or _EvalCandidateRow is None:
        return None, False
    if reason not in EVAL_CANDIDATE_REASONS:
        raise ValueError("非法候选 reason: %r（合法枚举: %s）"
                         % (reason, ", ".join(EVAL_CANDIDATE_REASONS)))
    from app.services import settings_service  # 局部引入避免循环依赖
    max_pending = settings_service.get_int(
        "eval_candidate_max_pending", EVAL_CANDIDATE_MAX_PENDING)
    s = get_session()
    try:
        existing = s.query(_EvalCandidateRow).filter(
            _EvalCandidateRow.receipt_id == int(receipt_id),
            _EvalCandidateRow.reason == str(reason),
        ).first()
        if existing is not None:
            return existing.id, False
        # L3 卫生 1：pending 总量上限（超限拒绝，返回既有 (None, False) 形态）
        pending_count = s.query(_EvalCandidateRow).filter(
            _EvalCandidateRow.status == "pending",
        ).count()
        if pending_count >= max_pending:
            return None, False
        # L3 卫生 2：原图 sha1 去重（同图同 reason 已有 pending 则跳过）
        src_sha1 = _receipt_image_sha1(s, receipt_id)
        if src_sha1:
            same_image = s.query(_EvalCandidateRow).filter(
                _EvalCandidateRow.reason == str(reason),
                _EvalCandidateRow.status == "pending",
            ).all()
            for cand in same_image:
                try:
                    cand_json = json.loads(cand.ai_candidate_json or "{}")
                except Exception:
                    continue
                if cand_json.get("src_sha1") == src_sha1:
                    return cand.id, False
        if tenant_id is None or not str(tenant_id).strip():
            rc = s.get(_ReceiptRow, int(receipt_id))
            tenant_id = getattr(rc, "tenant_id", None) if rc is not None else None
        conf = None
        if confidence is not None:
            try:
                conf = float(confidence)
            except (TypeError, ValueError):
                conf = None
        ai_payload = dict(ai_candidate or {})
        if src_sha1:
            ai_payload["src_sha1"] = src_sha1
        row = _EvalCandidateRow(
            receipt_id=int(receipt_id),
            reason=str(reason),
            tenant_id=str(tenant_id or "default"),
            doc_form=str(doc_form or "")[:60],
            confidence=conf,
            ai_candidate_json=json.dumps(ai_payload, ensure_ascii=False),
            note=str(note or "")[:500],
            status="pending",
            created_at=now_iso(),
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, True
    finally:
        s.close()


def get_eval_candidate(candidate_id, tenant_id=None):
    """按 id 取候选。tenant_id 非空时校验归属，跨租户视为不存在（返回 None）。"""
    if _EvalCandidateRow is None:
        return None
    s = get_session()
    try:
        row = s.get(_EvalCandidateRow, int(candidate_id))
        if row is not None and not _tenant_ok(row, tenant_id):
            return None
        return row
    finally:
        s.close()


def list_eval_candidates(status=None, reason=None, tenant_id=None, limit=500):
    """候选列表（可按 status / reason / 租户过滤），id 倒序（新的在前）。"""
    if _EvalCandidateRow is None:
        return []
    s = get_session()
    try:
        q = scoped(s.query(_EvalCandidateRow), _EvalCandidateRow, tenant_id)
        if status:
            q = q.filter(_EvalCandidateRow.status == str(status))
        if reason:
            q = q.filter(_EvalCandidateRow.reason == str(reason))
        return q.order_by(_EvalCandidateRow.id.desc()).limit(int(limit or 500)).all()
    finally:
        s.close()


def set_eval_candidate_status(candidate_id, status, tenant_id=None):
    """候选状态流转（仅 pending -> 目标状态，防重复处理）。

    返回 (row, err)；err in (None, 'NOT_FOUND', 'INVALID_STATUS', 'CONFLICT')。
    promote_to_* 写 promoted_at；rejected 不写。
    """
    if status not in EVAL_CANDIDATE_STATUSES:
        return None, "INVALID_STATUS"
    s = get_session()
    try:
        row = s.get(_EvalCandidateRow, int(candidate_id))
        if row is None or not _tenant_ok(row, tenant_id):
            return None, "NOT_FOUND"
        if row.status != "pending":
            return row, "CONFLICT"
        row.status = str(status)
        if status in ("promoted_to_val", "promoted_to_test"):
            row.promoted_at = now_iso()
        s.commit()
        s.refresh(row)
        return row, None
    finally:
        s.close()
