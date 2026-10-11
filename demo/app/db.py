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

import functools
import json
import logging
import math as _math
import os
import threading

# QA-Lock-Split §13：单据行写路径的统一写锁实例。api_receipts（11 个端点）与
# services/receipt_utils.save_parsed_data（Job 回写）共用同一对象——单一锁、
# 无嵌套获取，无死锁面。定义在 db 层以避免 api↔services 循环导入。
receipt_write_lock = threading.Lock()
import uuid
from collections import defaultdict
import contextvars
from contextlib import contextmanager
from collections.abc import Mapping as _Mapping
from datetime import datetime, timedelta

from dotenv import load_dotenv

from app import money as _money

# P3-T1（K2）：结构化事实的行字段唯一定义（纯逻辑模块，只依赖标准库，无导入环）。
from app.services.memory_facts import ROW_FIELDS as _MEMORY_FACT_ROW_FIELDS

load_dotenv()

logger = logging.getLogger(__name__)

_default_db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../receipt_demo.db"))
_raw_db_path = os.environ.get("DB_PATH", "").strip()
if not _raw_db_path or _raw_db_path in ("./receipt_demo.db", "receipt_demo.db"):
    DB_PATH = _default_db_path
else:
    DB_PATH = _raw_db_path

_engine = None
_SessionLocal = None

SQLITE_BUSY_TIMEOUT_MS = 5000


def _install_sqlite_pragmas(engine):
    """每条新连接都设 WAL + busy_timeout：读写并发时不立刻抛 database is locked。

    内存库与不支持 WAL 的介质上 journal_mode 切换会静默保持原值，不视为错误。
    """
    from sqlalchemy import event as _sa_event

    @_sa_event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=%d" % SQLITE_BUSY_TIMEOUT_MS)
            cur.execute("PRAGMA synchronous=NORMAL")
        finally:
            cur.close()


_ReceiptRow = _ItemRow = _SkuRow = _StockLogRow = _SupplierRow = None
_DeptRow = _PaymentRow = _VendorMemoryRow = _AppSettingRow = None
_AppSettingOverrideRow = None
_VendorSkuAliasRow = None
_DecisionLogRow = _SnapshotRow = _UserEventRow = _ExperimentRow = _ExperimentAssignRow = None
_ReceiptFeedbackRow = None
_PendingMemoryRow = None
_MemoryExtractRunRow = None
_EvalCandidateRow = None
_GuardrailEventRow = None
_DishRow = _DishIngredientRow = _InventoryBatchRow = _DailyConsumptionRow = _DailyConsumptionDetailRow = None
_DishRecipeVersionRow = None
_LlmCallLogRow = LLMCallLogRow = None
_EngineConfigHistoryRow = None
_SecurityEventRow = None

# W-ISO-1 租户隔离纵深兜底上下文变量
_current_tenant_ctx = contextvars.ContextVar("current_tenant", default=None)
_bypass_tenant_isolation_ctx = contextvars.ContextVar("bypass_tenant_isolation", default=False)


def set_current_tenant_id(tenant_id):
    """设置当前线程/协程请求的租户上下文。"""
    val = str(tenant_id).strip() if tenant_id and str(tenant_id).strip() else None
    _current_tenant_ctx.set(val)


def get_current_tenant_id():
    """获取当前租户上下文。"""
    return _current_tenant_ctx.get()


@contextmanager
def tenant_context(tenant_id):
    """租户上下文管理器：在作用域内绑定租户身份，供 scoped() 在未显式传租户时兜底过滤。"""
    val = str(tenant_id).strip() if tenant_id and str(tenant_id).strip() else None
    t1 = _current_tenant_ctx.set(val)
    try:
        yield
    finally:
        _current_tenant_ctx.reset(t1)


@contextmanager
def bypass_tenant_isolation():
    """后台离线维护或跨租户聚合时的隔离豁免上下文。"""
    token = _bypass_tenant_isolation_ctx.set(True)
    try:
        yield
    finally:
        _bypass_tenant_isolation_ctx.reset(token)
# WS5：价格异动处置留痕表（ignore / adopt 的持久化状态）
_PriceAnomalyAckRow = None


def _make_engine():
    global _engine, _SessionLocal
    global _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow, _SupplierRow
    global _DeptRow, _PaymentRow, _VendorMemoryRow, _AppSettingRow
    global _AppSettingOverrideRow
    global _VendorSkuAliasRow
    global _DecisionLogRow, _SnapshotRow, _UserEventRow, _ExperimentRow, _ExperimentAssignRow
    global _ReceiptFeedbackRow
    global _PendingMemoryRow
    global _MemoryExtractRunRow
    global _EvalCandidateRow
    global _GuardrailEventRow
    global _DishRow, _DishIngredientRow, _InventoryBatchRow, _DailyConsumptionRow, _DailyConsumptionDetailRow
    global _DishRecipeVersionRow
    global _LlmCallLogRow, LLMCallLogRow
    global _EngineConfigHistoryRow
    global _SecurityEventRow
    global _PriceAnomalyAckRow

    from sqlalchemy import create_engine, Column, String, Float, Integer, Text, DateTime, UniqueConstraint, Index
    from sqlalchemy.orm import sessionmaker, declarative_base
    from sqlalchemy.sql import func

    _engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={
        "check_same_thread": False, "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000.0})
    _install_sqlite_pragmas(_engine)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)

    Base = declarative_base()

    class ReceiptRow(Base):
        __tablename__ = "receipts"
        id = Column(Integer, primary_key=True, autoincrement=True)
        status = Column(String, default="uploaded")       # uploaded/parsing/parsed/edited/approved/flagged/error
        supplier_name = Column(String, default="")         # 展示字段：不改语义、不改写历史值
        supplier_code = Column(String, default="")
        # D17：供应商归档外键（结构关联）。历史行由幂等回填补齐；无档案的名字串
        # 保持 NULL（绝不凭空建档），读取侧对无 id 历史行回落归一 key 匹配。
        supplier_id = Column(Integer, index=True, nullable=True)
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
        items_json = Column(Text, default="[]")            # 历史遗留列：无消费方、已不再写入；明细以 receipt_items 表为准
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
        is_golden_sample = Column(Integer, default=0)      # 黄金基准集成员标记（可逆策展层，不改动单据本体）
        rag_context_json = Column(Text, default="")        # RAG 检索上下文（data_only 调试开关可见）
        # P2-1 图内隐藏文字检测结果（JSON：{flagged, reasons, metrics}），只告警不拦截；
        # 缺省 '{}' 表示未检测/无结果，向后兼容历史行。
        image_guard_json = Column(Text, default="{}")
        currency = Column(String, default="HKD")           # 多币种（F-P1-3）
        # Gap 9 / Gap 6 店员可修正字段（识别直出 + save_edited 覆写，双写同列）
        service_fee = Column(Float, default=0.0)           # 加一服务费
        tax_amount = Column(Float, default=0.0)            # 税额/VAT/GST
        discount_amount = Column(Float, default=0.0)
        delivery_fee = Column(Float, default=0.0)
        deposit_amount = Column(Float, default=0.0)
        rounding_adjustment = Column(Float, default=0.0)
        adjustment_notes_json = Column(Text, default="[]") # 手写注记（拒收/短装/调整）list[str]
        payment_evidence = Column(Text, default="")        # 付款标记图面证据描述
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        deleted_at = Column(String, nullable=True)  # 软删除时间戳
        deleted_by = Column(String, nullable=True)  # 放弃单据的操作人（回收站展示）
        # 冲销（approved -> reversed）留痕：谁、何时、为什么
        reversed_at = Column(String, nullable=True)
        reversed_by = Column(String, nullable=True)
        reverse_reason = Column(Text, nullable=True)
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
        # P11：可空——NULL 表示「模型未给出该行置信度」这一事实本身，与「给了 0.5」区分。
        # 不能留 default=0.0：Python 侧默认值会把显式传入的 None 变成 0.0，
        # 又变成一种「伪造的置信度」（且 0.0 会撞上任何低置信阈值）。
        confidence = Column(Float, nullable=True)
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
        # 既有问题 #2（C）：name 去掉库级 unique=True —— 唯一性改按租户命名空间
        # （create_supplier 的 scoped 重名检查为唯一防重线）。旧库的
        # name 全局唯一物理约束（UNIQUE 索引或列约束）由下方迁移做数据保全式
        # 重建消除，否则跨租户同名供应商通过 scoped 检查后仍会在 INSERT 处
        # IntegrityError。与 skus.name 的既有修法（上方 SkuRow 注释）同口径。
        name = Column(String, index=True)
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
        # D16：租户隔离键（payments / departments 补齐，与业务主表同口径）
        tenant_id = Column(String(64), default="default", index=True)

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
        # D16：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

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
        # 追加式不可变（append-only）模型：notes/sample 内容一旦写入即不就地改写，
        # 内容变更一律新增行（新 memory_id）。故行内 version 恒为 1、无需递增——它表示
        # 「本行内容被就地改过几次」，而本模型永不就地改内容。去重命中时只合并
        # source_ref / 刷新 updated_at（元数据刷新），不改变内容版本语义。
        version = Column(Integer, default=1)
        source_kind = Column(String(32), default="manual")  # approve|feedback_distilled|manual
        source_ref = Column(Text, default="[]")            # 触发来源 receipt_id 列表 JSON
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        hit_count = Column(Integer, default=0)
        last_hit_at = Column(String, default="")
        decay_score = Column(Float, default=1.0)           # T8：命中+bonus/覆写-penalty。新写入行的
                                                          # 起始分走 memory_initial_decay_score（P1-T2），
                                                          # 本缺省只兜底未显式赋值的路径（旧行/重建填空）
        status = Column(String(16), default="active")      # active|archived|superseded
        # P1-T3 冲突治理（同 key supersede + TTL）
        # fact_key：结构化事实的键（如 "alias:黑牛" / "unit:斤"）。遗留行与既有
        #   自由文本写入路径恒为 NULL —— NULL 表示"无键"，不参与 supersede 归并，
        #   保持追加式语义（同 vendor 多行并存）。
        fact_key = Column(Text)
        # superseded_by：本条被哪条新记忆取代（新 memory_id）。旧行保留以存历史，
        #   默认读路径不返回；链式可回溯（P3-T3 版本/回滚的载体）。
        superseded_by = Column(String)
        # valid_to：本条记忆的失效时刻（ISO）；空串 = 无显式失效期（由 TTL 兜底）。
        valid_to = Column(String, default="")
        # P2-T1 主体锚点：指向 suppliers.id（NULL = 未锚定，读侧按 vendor 名回退）。
        # why：vendor 是自由文本，同一供应商的繁简/简称/英译写法会分裂成互不可见的
        #   多份记忆（读侧全靠名字相关度门猜）。supplier_id 是档案主键，同一供应商的
        #   一切写法都指向同一个 id —— 名字降为别名与展示字段。
        # 为什么允许 NULL：无法可靠解析出档案时（如历史脏名、测试夹具名）宁可留空，
        #   也不新建/错挂供应商档案（拿不准就不锚定，见 db.resolve_supplier_id）。
        supplier_id = Column(Integer, index=True, nullable=True)
        # P3-T1（K2）结构化事实载荷：MemoryFact 的 kind/value/信任/有效期/写入者。
        # why 必须补这五列：memory_facts.ROW_FIELDS 声明了 14 个行字段，本表此前只有 9 个
        #   —— `MemoryFact.to_row()` 产出的 fact_kind/fact_value/trust_level/valid_from/
        #   written_by 没有落点，构造得出、落不进库（本仓高发的「定义了没接线」）。
        # 列名与 memory_facts.ROW_FIELDS 逐字一致：db 侧不做第二份字段映射，
        #   载荷列集合由 ROW_FIELDS 派生（见 _MEMORY_FACT_PAYLOAD_COLUMNS）。
        # **为什么都不设 Column 缺省（刻意不写 default=）**：这五列只承载结构化事实，
        #   遗留自由文本写入路径（approve 回写 / 反馈蒸馏）不产出事实，其行必须保持 NULL。
        #   读侧 `MemoryFact.from_row` 把 NULL 视为"该字段不存在"；若填缺省值，一行自由
        #   文本会长得跟一条合法事实一样 —— trust_level 尤其致命：填 1.0 等于凭空给它
        #   最高信任，而它从未被任何人确认过。
        fact_kind = Column(String(32))     # alias|unit|layout|price_range（memory_facts 白名单）
        fact_value = Column(Text)          # 事实内容；与 fact_key 一起构成"同键异值"判定
        trust_level = Column(Float)        # 0.0-1.0；人工来源 1.0，模型抽取由调用方下调
        valid_from = Column(String)        # 事实生效时刻（YYYY-MM-DD 或 ISO）
        written_by = Column(String)        # 写入者标识（供审计追溯"这条是谁写的"）

    class VendorSkuAliasRow(Base):
        """P2-T2：供应商 × SKU 别名（C4）。

        语义：**该供应商用这个写法指这个 SKU**。上游的三个写入钩子（approve 入账
        登记 / 自动建档登记 / SKU 合并改指）与一个读取钩子（`_match_sku` 供应商优先）
        共用本表。

        why 需要独立一张表、而不是塞进 vendor_memory 的自由文本：
        1. 匹配是**结构化判定**（供应商 + 写法 → sku_id），自由文本无法参与等值查询；
        2. 港式手写单里同一写法（如「黑牛」）在不同供应商下含义不同 —— 别名必须带
           supplier_id 维度才不至于互相污染；
        3. live 实测 SKU 匹配率仅 23.7%，根因是匹配完全按商品名做全局核心词互含，
           不看供应商历史（`_match_sku` 的旧实现）。
        同时它抑制 SKU 爆炸：同一供应商的同类写法第二次出现直接命中，不再建新 SKU。

        唯一键 (tenant_id, supplier_id, raw_name)：一个写法在同一供应商下只能指向
        一个 SKU —— 读取侧靠它才能确定命中哪一条。unit 只作记录、不进唯一键，
        避免同写法换单位时出现两条都参与匹配、命中结果不确定。
        """
        __tablename__ = "vendor_sku_alias"
        id = Column(Integer, primary_key=True, autoincrement=True)
        tenant_id = Column(String(64), default="default", index=True)
        # 指向 suppliers.id；NULL = 未锚定（本表**不使用** NULL 行：无供应商维度时
        # 读取侧无从判定，登记了也没人消费，写入侧直接跳过并计数）
        supplier_id = Column(Integer, index=True, nullable=True)
        # 指向 skus.id；被合并的副 SKU 由 merge 联动改指到主 SKU（见 _repoint_vendor_sku_alias）
        sku_id = Column(Integer, index=True)
        # 供应商侧的原始写法（匹配键，与解析时 item["name"] 同口径）
        raw_name = Column(Text, default="")
        # 核心词归一后的写法（覆盖繁简/规格/包装前缀差异，第二顺位匹配键）
        canonical_name = Column(Text, default="")
        # 该供货写法惯用的单位。**消费方**＝P2-T4 的复核侧单位先验（按供应商提示
        # 历史单位），本批只写不读；读取侧不拿它改写票面单位（那是重构口径的事）。
        unit = Column(String, default="")
        # 读取侧命中累计：**消费方**＝`find_vendor_sku_alias` 的归一通道排序
        # （同核心词多条候选时取用得最多的一条），同时是后续清理/度量的依据。
        hit_count = Column(Integer, default=0)
        # 登记时刻。**消费方**暂为审计/回滚口径（P3 的版本化），本批只写不读；
        # 明确记在这里，避免变成"只写列无人消费"的静默残留。
        valid_from = Column(String, default="")
        # 空串 = 无显式失效期；非空且已过期 → 读取侧不命中（不删行，保留可审计）
        valid_to = Column(String, default="")
        status = Column(String(16), default="active")   # active|retired
        # 来源留痕：如 "receipt:1455" / "backfill:matched"
        source_ref = Column(Text, default="")
        updated_at = Column(String, default="")
        __table_args__ = (
            # 用显式 Index(unique=True) 而不是 UniqueConstraint：SQLite 会把
            # UniqueConstraint 落成匿名自增索引（sqlite_autoindex_*），与迁移块里
            # 命名唯一索引对不上会造成两个重复唯一索引。命名索引两处同名，新建库
            # 与迁移块互不重复。
            Index("ux_vendor_sku_alias_key", "tenant_id", "supplier_id", "raw_name",
                  unique=True),
        )

    class AppSettingRow(Base):
        __tablename__ = "app_settings"
        # WS3：本表只承载**全局桶**（admin 全局默认值），门店覆盖归 app_setting_overrides（见下）。
        # key 单列主键即提供全局唯一约束，历史 `insert ... on conflict(key) do update`
        # 契约因此天然成立（无复合主键第二列、无额外唯一索引、无数据保全式重建）。
        # 读取口径：settings_service.get 先查门店覆盖（app_setting_overrides），
        # 再回落本表（全局），最后回落 SETTINGS_DEFAULTS。
        key = Column(String, primary_key=True)
        value = Column(Text, default="")

    class AppSettingOverrideRow(Base):
        """WS3 门店级覆盖行（复合主键 (key, tenant_id)），全局桶 app_settings 之外的归属。

        why 独立成表：app_settings 只承载全局桶（key 单列主键，历史 ON CONFLICT(key)
        契约依赖 key 单列唯一）；同一 key 的「全局默认行」与「门店覆盖行」因此分表存放，
        覆盖行在本表按 (key, tenant_id) 唯一。读取口径：settings_service.get 先查本表
        （该租户覆盖）再回落 app_settings（全局），最后回落 SETTINGS_DEFAULTS。
        """
        __tablename__ = "app_setting_overrides"
        key = Column(String, primary_key=True)
        tenant_id = Column(String(64), default="default", server_default="default",
                           primary_key=True)
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
        # P3-T1（K2）字段级 diff 载荷：审核者要能逐字段看到"这条记忆打算写什么"，
        # 而不是对整段 content 只能整体接受/拒绝（无 diff 载体时人工闸的成本高到
        # 只能被绕过，等于闸门形同虚设）。
        # why 用与 vendor_memory.ROW_FIELDS 逐字相同的列名（supplier_id/fact_kind/
        #   fact_key/fact_value）：approve 时可直接把载荷交给 save_memory_fact，
        #   不需要第二次字段映射（本仓高发的「同一口径多份实现」）。
        # **不设缺省**：遗留入队路径（反馈蒸馏）不产出结构化事实，其行保持 NULL =
        #   "没有字段级载荷" —— 审核界面据此退回整段文本视图，而不是显示一排空字段。
        supplier_id = Column(Integer, nullable=True)   # 主体锚点（同 vendor_memory；NULL=未锚定）
        fact_kind = Column(String(32))                # 四类事实白名单之一
        fact_key = Column(Text)                       # 事实键（同键冲突判定用）
        fact_value = Column(Text)                     # 事实内容（diff 视图的"新值"侧）
        # reject_reason：拒绝/落选的可读理由。NULL = 未产生理由。为什么必须落库：
        # 只把理由打日志，审核者与被拒方的下一次写入都看不到"上次为什么没进来"。
        # 写入方（Q2，此前零写入方＝只建列）：api_receipts._record_refused_candidate，
        # 被 `_extract_and_enqueue_memory_facts` 在三条"候选没进队列"的分支上调用 ——
        #   ① 抽取侧闸门拒绝（memory_extract 的逐条理由，只有理由字符串、无 fact 载荷）
        #   ② 写入侧闸门拒绝（memory_facts.validate_fact 的 reason，带 fact 载荷）
        #   ③ decide_action 判 NOOP（memory_consolidate 的 reason，含"低可信不得覆盖高可信"）
        # 消费方：`_pending_row_to_dict` → `GET /api/memory/pending`（含 status=rejected）。
        # 形态约定：这些行落在 status='rejected' 且 reviewed_by 为 NULL ＝ 由系统闸门自动
        # 拒绝（人工拒绝必经 `review_pending_memory`，那里会写 reviewed_by/reviewed_at）。
        reject_reason = Column(Text)

    class MemoryExtractRunRow(Base):
        """P3-T4（B6）：记忆抽取的**运行记录**（中间产物可查、失败可复盘）。

        为什么需要它：`memory_extract.get_last_run()` / `get_last_error()` 是
        `threading.local` 的**内存态** —— 进程重启即丢，事后无法回答"某次抽取为什么产出
        0 条事实 / 是哪一批退了回退文案 / 失败在哪一步"。本表是那份内存态的持久化落点，
        **不重复实现 get_last_run**：入参就是它的内容（见 record_memory_extract_run）。

        与 `pending_memory`（status='rejected'）的区别：那是**候选级**的"为什么这条没进来"，
        本表是**运行级**的"这次抽取整体发生了什么"（含没产出任何候选、甚至没跑成的那些次），
        两者互补、不可互相替代。本表只记运行元数据，不记候选内容（候选内容归 pending）。
        `facts_produced` + `reject_reasons_json` 一起看才能区分"模型没给出候选"与
        "候选都被闸门拒了"，这正是 2026-09-23 那次"看板显示 0 却看不出原因"的缺口。

        幂等：run_id 自增主键，每次调用新增一行 —— 运行记录按"次"计，刻意不去重
        （去重会把"同一张单据反复重跑"这个信号抹掉）。
        """
        __tablename__ = "memory_extract_runs"
        run_id = Column(Integer, primary_key=True, autoincrement=True)
        tenant_id = Column(String(64), default="default", index=True)
        receipt_id = Column(Integer, index=True)
        vendor = Column(String, default="")
        # 主体锚点（同 vendor_memory.supplier_id；NULL = 当时没能解析出锚点，属可查的失败态）
        supplier_id = Column(Integer, nullable=True)
        # 触发来源：save_edited（复核保存）/ approve（入账）。消费方＝复盘时按入口分桶看
        # "哪条路径上的抽取一直在失败"。
        trigger = Column(String(32), default="")
        diffs_count = Column(Integer, default=0)
        facts_produced = Column(Integer, default=0)
        facts_rejected = Column(Integer, default=0)
        # 逐条丢弃理由 JSON。**失败行也非空**（见 record_memory_extract_run 的 why）：
        # 只留 error 时，"失败"与"本来就没什么可记的"在库里长得一样。
        reject_reasons_json = Column(Text, default="[]")
        # 本次用的是注册表文案还是内联回退（memory_extract 的 PROMPT_SOURCE_* 常量值）。
        # 单列的理由：回退率是 prompt 治理失效的**治理信号**，不是"这次结果有问题"，
        # 混进 error 会让告警方向失真。
        prompt_source = Column(String(32), default="")
        error = Column(Text, default="")           # 空串 = 本次抽取成功执行（含 0 候选）
        created_at = Column(String, default="")

    # -------------------------------------------------------------
    # 引擎配置版本历史（本轮新增）：save / promote / rollback / reset 各落一条
    # -------------------------------------------------------------
    class EngineConfigHistoryRow(Base):
        """引擎配置版本历史：每次写操作落一条不可变快照（本轮新增）。

        列名按已批准规格 01-spec.md §4.5 逐字给定（snapshot / changed_keys），
        故此处不套用本仓 JSON 列的 `_json` 后缀习惯。
        action: save|promote|rollback|reset。snapshot 存写入后的配置快照，
        **落库前做 api_key 脱敏**：本表是新增持久面，没有任何理由因为「审计」把真实
        密钥多写一份到磁盘。changed_keys 只存键名列表，不含值。
        """
        __tablename__ = "engine_config_history"
        id = Column(Integer, primary_key=True, autoincrement=True)
        version = Column(Integer, index=True, default=1)
        actor = Column(String, default="")
        action = Column(String(16), default="")     # save|promote|rollback|reset
        created_at = Column(String, default="")
        snapshot = Column(Text, default="{}")
        changed_keys = Column(Text, default="[]")

    # -------------------------------------------------------------
    # 餐品管理与 FIFO 批次库存
    # -------------------------------------------------------------
    class DishRow(Base):
        """餐品主表：名称、品类、售价、状态。"""
        __tablename__ = "dishes"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, index=True, default="")
        category = Column(String, default="默认", index=True)
        price = Column(Float, default=0.0)
        description = Column(String, default="")
        status = Column(String, default="active")          # active/inactive
        version = Column(Integer, default=1)               # BOM 版本号（乐观锁基线）
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        # Gap E2：租户隔离键
        tenant_id = Column(String(64), default="default", index=True)

    class DishIngredientRow(Base):
        """餐品 BOM 配方明细表（支持 sku 食材与 dish 子配方两类组件）。

        - component_type='sku'：component_id 即 SKU id，并回填 sku_id 保持兼容；
        - component_type='dish'：component_id 为子餐品 id，sku_id 置空；
        唯一约束按 (dish_id, component_type, component_id)，同一餐品下同一
        组件只允许一行（sku_id 换组件类型后可重复出现于不同组件）。
        yield_rate 为出成率：有效领料量 = 配方用量 / yield_rate，损耗率派生为 1 - yield_rate。
        """
        __tablename__ = "dish_ingredients"
        id = Column(Integer, primary_key=True, autoincrement=True)
        dish_id = Column(Integer, index=True, nullable=False)
        sku_id = Column(Integer, index=True, nullable=True)
        component_type = Column(String(16), default="sku")     # sku | dish
        component_id = Column(Integer, index=True, nullable=True)
        consumption_qty = Column(Float, default=0.0)
        unit = Column(String, default="")
        yield_rate = Column(Float, default=1.0)
        notes = Column(String, default="")
        __table_args__ = (
            UniqueConstraint("dish_id", "component_type", "component_id"),
        )

    class DishRecipeVersionRow(Base):
        """餐品配方版本快照（BOM 版本化 + 乐观锁 + 可回滚）。"""
        __tablename__ = "dish_recipe_versions"
        id = Column(Integer, primary_key=True, autoincrement=True)
        dish_id = Column(Integer, index=True, nullable=False)
        version = Column(Integer, default=1)
        snapshot_json = Column(Text, default="{}")
        change_summary = Column(String, default="")
        changed_by = Column(String, default="")
        changed_at = Column(String, default="")
        tenant_id = Column(String(64), default="default", index=True)

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
        # WS4：租户归属列（归属不再仅靠 join 推断）
        tenant_id = Column(String(64), default="default", index=True)

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

    class LlmCallLogRow(Base):
        """LLM/VLM 模型调用与输入输出全息日志表。"""
        __tablename__ = "llm_call_log"
        id = Column(Integer, primary_key=True, autoincrement=True)
        receipt_id = Column(Integer, index=True, nullable=True)
        tenant_id = Column(String(64), default="default", index=True)
        trace_id = Column(String(64), default="")
        leg = Column(String(32), nullable=False, default="extract")
        attempt = Column(Integer, default=1)
        engine = Column(String(32), default="")
        model = Column(String(128), default="")
        prompt_messages_json = Column(Text, default="[]")
        raw_output = Column(Text, default="")
        prompt_tokens = Column(Integer, default=0)
        completion_tokens = Column(Integer, default=0)
        cached_tokens = Column(Integer, default=0)
        total_tokens = Column(Integer, default=0)
        cost_hkd = Column(Float, default=0.0)
        cost_estimated = Column(Integer, default=0)
        cost_estimated_reason = Column(String(64), default="")
        elapsed_ms = Column(Float, default=0.0)
        status = Column(String(32), default="success")
        error_msg = Column(Text, default="")
        error_category = Column(String(32), default="")
        adapted_params = Column(String(256), default="")
        created_at = Column(String(64), default="", index=True)

    _LlmCallLogRow = LLMCallLogRow = LlmCallLogRow
    _EngineConfigHistoryRow = EngineConfigHistoryRow

    class PriceAnomalyAckRow(Base):
        """WS5 价格异动处置留痕表（经营处置闭环，不参与异动判定本身）。

        语义：owner 对某 SKU 的价格异动做「忽略 / 采纳」处置后落痕，使前端跨刷新
        保留处置状态，并让埋点 `price_anomaly_adopted` / `price_anomaly_dismissed`
        可采集（此前「忽略」只是会话级内存 Set，刷新即复位，指标恒为 0）。

        - 同一 (sku_id, tenant_id) 只保留一行：重复处置是**更新**而非堆叠
          （唯一索引 ux_price_anomaly_ack_sku_tenant 兜底，upsert 走先查后写）。
        - threshold_pct / snapshot_vs_pct 记录**处置当时**的阈值与涨幅快照：阈值可被
          门店覆盖实时改动，事后按当前配置回算会得到不同数字，故必须留痕供回溯。
        """
        __tablename__ = "price_anomaly_ack"
        id = Column(Integer, primary_key=True, autoincrement=True)
        tenant_id = Column(String(64), default="default", server_default="default", index=True)
        sku_id = Column(Integer, index=True, nullable=False)
        action = Column(String, default="ignore")          # ignore | adopt
        threshold_pct = Column(Float, default=0.0)         # 判定当时所用阈值（百分数）
        snapshot_vs_pct = Column(Float, default=0.0)       # 判定当时涨幅（vs 30 日均价）
        created_at = Column(String, default="")
        __table_args__ = (
            Index("ux_price_anomaly_ack_sku_tenant", "sku_id", "tenant_id", unique=True),
        )

    _PriceAnomalyAckRow = PriceAnomalyAckRow

    Base.metadata.create_all(_engine)

    # SQLite 迁移：幂等确保 llm_call_log 表及关键索引存在
    _apply_migration_ddl(
        _engine,
        """CREATE TABLE IF NOT EXISTS llm_call_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER,
            tenant_id VARCHAR(64) DEFAULT 'default',
            trace_id VARCHAR(64) DEFAULT '',
            leg VARCHAR(32) NOT NULL,
            attempt INTEGER DEFAULT 1,
            engine VARCHAR(32) DEFAULT '',
            model VARCHAR(128) DEFAULT '',
            prompt_messages_json TEXT DEFAULT '[]',
            raw_output TEXT DEFAULT '',
            prompt_tokens INTEGER DEFAULT 0,
            completion_tokens INTEGER DEFAULT 0,
            cached_tokens INTEGER DEFAULT 0,
            total_tokens INTEGER DEFAULT 0,
            cost_hkd REAL DEFAULT 0.0,
            cost_estimated INTEGER DEFAULT 0,
            cost_estimated_reason VARCHAR(64) DEFAULT '',
            elapsed_ms REAL DEFAULT 0.0,
            status VARCHAR(32) DEFAULT 'success',
            error_msg TEXT DEFAULT '',
            error_category VARCHAR(32) DEFAULT '',
            adapted_params VARCHAR(256) DEFAULT '',
            created_at VARCHAR(64) DEFAULT ''
        )"""
    )
    _apply_migration_ddl(_engine, "CREATE INDEX IF NOT EXISTS ix_llm_call_log_receipt_id ON llm_call_log (receipt_id)")
    _apply_migration_ddl(_engine, "CREATE INDEX IF NOT EXISTS ix_llm_call_log_tenant_id ON llm_call_log (tenant_id)")
    _apply_migration_ddl(_engine, "CREATE INDEX IF NOT EXISTS ix_llm_call_log_created_at ON llm_call_log (created_at)")
    _apply_migration_ddl(_engine, "ALTER TABLE llm_call_log ADD COLUMN adapted_params VARCHAR(256) DEFAULT ''")
    class SecurityEventRow(Base):
        """W-SEC-6 安全事件审计流表：记录拦截、告警、Canary、SSRF、脱敏与越权事件。"""
        __tablename__ = "security_events"
        id = Column(Integer, primary_key=True, autoincrement=True)
        event_id = Column(String(64), unique=True, nullable=False, index=True)
        tenant_id = Column(String(64), default="default", nullable=False, index=True)
        event_type = Column(String(32), nullable=False, index=True)
        severity = Column(String(16), nullable=False)
        source_module = Column(String(64), nullable=False)
        details_json = Column(Text, default="{}")
        ip_address = Column(String(45), default="")
        user_email = Column(String(128), default="")
        created_at = Column(String(32), nullable=False, index=True)

    Base.metadata.create_all(_engine)

    # SQLite 迁移（W-SEC-6）：创建 security_events 表并建索引
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS security_events ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " event_id VARCHAR(64) UNIQUE NOT NULL,"
        " tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
        " event_type VARCHAR(32) NOT NULL,"
        " severity VARCHAR(16) NOT NULL,"
        " source_module VARCHAR(64) NOT NULL,"
        " details_json TEXT NOT NULL DEFAULT '{}',"
        " ip_address VARCHAR(45) DEFAULT '',"
        " user_email VARCHAR(128) DEFAULT '',"
        " created_at VARCHAR(32) NOT NULL)",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS ix_sec_events_tenant_type ON security_events (tenant_id, event_type)",
        "CREATE INDEX IF NOT EXISTS ix_sec_events_created_at ON security_events (created_at)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移：给 receipts 表补 use_grey 列（阶段 1：AI 可见性与信任）
    # R5 静默幂等：先查再执行 —— 列已存在静默跳过（零日志）；仅真实异常
    # （DB locked 等故障可被观测）才 logger.warning，且不阻断启动。
    _apply_migration_ddl(
        _engine,
        "ALTER TABLE receipts ADD COLUMN use_grey INTEGER DEFAULT 0",
    )
    # SQLite 迁移：黄金基准集成员标记（看板可加入/移出，策展层可逆，不动审计主数据）
    _apply_migration_ddl(
        _engine,
        "ALTER TABLE receipts ADD COLUMN is_golden_sample INTEGER DEFAULT 0",
    )
    # SQLite 迁移：RAG 上下文 + 多币种（P1 治理）
    for _ddl in (
        "ALTER TABLE receipts ADD COLUMN rag_context_json TEXT DEFAULT ''",
        "ALTER TABLE receipts ADD COLUMN currency VARCHAR(8) DEFAULT 'HKD'",
        "ALTER TABLE receipts ADD COLUMN service_fee REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN tax_amount REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN discount_amount REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN delivery_fee REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN deposit_amount REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN rounding_adjustment REAL DEFAULT 0",
        "ALTER TABLE receipts ADD COLUMN adjustment_notes_json TEXT DEFAULT '[]'",
        "ALTER TABLE receipts ADD COLUMN payment_evidence TEXT DEFAULT ''",
        # P2-1 图内隐藏文字检测结果（只告警不拦截；'{}' = 未检测/无结果）
        "ALTER TABLE receipts ADD COLUMN image_guard_json TEXT DEFAULT '{}'",
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

    # SQLite 迁移（D16/D17 安全加固第二批）：
    # - payments / departments 补 tenant_id 列（与业务主表同口径：存量行归 default）
    # - receipts 补 supplier_id 列（供应商结构关联；历史行由下方幂等回填补齐）
    # 幂等：先查再执行（列/索引已存在静默跳过），仅 ADD COLUMN / CREATE INDEX，
    # 禁重建表。
    for _ddl in (
        "ALTER TABLE payments ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE departments ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        "ALTER TABLE receipts ADD COLUMN supplier_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_payments_tenant_id ON payments (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_departments_tenant_id ON departments (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_receipts_supplier_id ON receipts (supplier_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # D16：payments / departments 存量行 tenant_id 回填 'default'。
    # ADD COLUMN ... DEFAULT 'default' 已让存量行逻辑返回 'default'；此处再显式
    # UPDATE 兜底（覆盖历史上列存在但值为 NULL/'' 的物理形态）。改写后不再命中
    # 判据 —— 二次执行为 no-op（幂等）。
    for _tbl_tid in ("payments", "departments"):
        try:
            from sqlalchemy import text as _sa_text_tid
            with _engine.connect() as _c:
                _c.execute(_sa_text_tid(
                    f"UPDATE {_tbl_tid} SET tenant_id = 'default' "
                    "WHERE tenant_id IS NULL OR tenant_id = ''"))
                _c.commit()
        except Exception as _e:
            _migration_failed(
                "[migrate] %s tenant_id backfill skipped: %s", _tbl_tid, _e)

    # D17：receipts.supplier_id 幂等回填（精确名匹配 -> 归一 key 匹配 -> 保持 NULL）。
    _backfill_receipt_supplier_ids(_engine)

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

    # SQLite 迁移（P1-T3 冲突治理）：vendor_memory 追加 3 列。
    # 幂等写法与上同（先查再执行，列已存在静默跳过零日志）；仅 ADD COLUMN，禁重建表。
    # 为什么单独一个迁移块：与上方 B1 那 10 列不是同一批变更（语义不同、回滚粒度
    # 不同），合并写会让"这一列是哪一批加的"变得不可考。
    for _ddl in (
        # 结构化事实的键；遗留行为 NULL（NULL 不参与 supersede 归并）
        "ALTER TABLE vendor_memory ADD COLUMN fact_key TEXT",
        # 被哪条新记忆取代（旧行保留以存历史）
        "ALTER TABLE vendor_memory ADD COLUMN superseded_by TEXT",
        # 本条记忆的失效时刻（ISO）；空串 = 无显式失效期
        "ALTER TABLE vendor_memory ADD COLUMN valid_to TEXT DEFAULT ''",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（P2-T1 supplier_id 锚点）：vendor_memory 追加 1 列 + 索引。
    # 幂等写法与上同（先查再执行，列已存在静默跳过零日志）；仅 ADD COLUMN /
    # CREATE INDEX，禁重建表。
    # 语义：supplier_id 指向 suppliers.id；NULL = 未锚定（读侧回退按 vendor 名匹配）。
    # **为什么回填不在这里做**：列是结构变更、回填是数据变更，后者必须显式授权
    #   （--apply）才落笔，否则任何一次 import 都会静默改写存量记忆归属。回填走
    #   demo/scripts/backfill_vendor_memory_supplier.py（缺省预演、--apply 才写），
    #   且与新写入侧共用同一解析实现 db.resolve_supplier_id（防「同一根因两份实现」）。
    for _ddl in (
        "ALTER TABLE vendor_memory ADD COLUMN supplier_id INTEGER",
        "CREATE INDEX IF NOT EXISTS idx_vendor_memory_supplier_id"
        " ON vendor_memory (supplier_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（P3-T1 结构化事实载荷）：vendor_memory 追加 5 列。
    # 幂等写法与上同（先查再执行，列已存在静默跳过零日志）；仅 ADD COLUMN，禁重建表。
    # **刻意不带 DEFAULT**：列形态必须与 create_all 建出的新库一致 —— 带 DEFAULT 会让
    #   同一行在"ALTER 出来的老库"与"新建的库"里拿到不同的读侧结果（遗留自由文本行走
    #   读侧 MemoryFact.from_row 时，NULL → 无字段，而 1.0/'' → 看起来像一条事实）。
    # 本块与 `_rebuild_legacy_vendor_memory` 的 target_cols 是同一份 schema 的两处实现，
    #   加列必须两处同时登记（漏一处＝重建后的库缺列，且 ALTER 不会补第二次）。
    for _ddl in (
        "ALTER TABLE vendor_memory ADD COLUMN fact_kind VARCHAR(32)",
        "ALTER TABLE vendor_memory ADD COLUMN fact_value TEXT",
        "ALTER TABLE vendor_memory ADD COLUMN trust_level REAL",
        "ALTER TABLE vendor_memory ADD COLUMN valid_from TEXT",
        "ALTER TABLE vendor_memory ADD COLUMN written_by VARCHAR",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（P2-T2 供应商×SKU 别名，C4）：vendor_sku_alias 新表 + 唯一索引。
    # 幂等 CREATE TABLE IF NOT EXISTS（与 eval_candidate 同款兜底，覆盖裸库场景；
    # 新库由上方 create_all 按 ORM 声明建出等价 schema）；仅新建表/索引，禁重建表。
    # **为什么"唯一索引"是这张表的语义要件而非优化**：读取侧要按
    # (tenant_id, supplier_id, raw_name) 确定性地命中一条，没有唯一约束时重复登记
    # 会留下两条指向不同 SKU 的别名，"同一写法解析到哪个 SKU"就成了不确定行为。
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS vendor_sku_alias ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " tenant_id VARCHAR(64) DEFAULT 'default',"
        " supplier_id INTEGER,"
        " sku_id INTEGER,"
        " raw_name TEXT DEFAULT '',"
        " canonical_name TEXT DEFAULT '',"
        " unit VARCHAR DEFAULT '',"
        " hit_count INTEGER DEFAULT 0,"
        " valid_from TEXT DEFAULT '',"
        " valid_to TEXT DEFAULT '',"
        " status VARCHAR(16) DEFAULT 'active',"
        " source_ref TEXT DEFAULT '',"
        " updated_at TEXT DEFAULT '')",
    )
    for _ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_sku_alias_key"
        " ON vendor_sku_alias (tenant_id, supplier_id, raw_name)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_sku_alias_supplier_id"
        " ON vendor_sku_alias (supplier_id)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_sku_alias_sku_id"
        " ON vendor_sku_alias (sku_id)",
        "CREATE INDEX IF NOT EXISTS ix_vendor_sku_alias_tenant_id"
        " ON vendor_sku_alias (tenant_id)",
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
        " reviewed_at TEXT,"
        # P3-T1（K2）：字段级 diff 载荷 + 拒绝理由（裸库兜底建表，与 ORM 声明一致）
        " supplier_id INTEGER,"
        " fact_kind VARCHAR(32),"
        " fact_key TEXT,"
        " fact_value TEXT,"
        " reject_reason TEXT)",
    )
    # SQLite 迁移（P3-T1 字段级 diff 载荷）：pending_memory 追加 5 列。
    # 幂等写法与上同（先查再执行，列已存在静默跳过零日志）；仅 ADD COLUMN，禁重建表。
    # **刻意不带 DEFAULT**：遗留队列行必须保持 NULL = "无字段级载荷"，否则审核界面
    #   会把空字符串当成一个真实的字段值渲染出来（伪造 diff）。
    for _ddl in (
        "ALTER TABLE pending_memory ADD COLUMN supplier_id INTEGER",
        "ALTER TABLE pending_memory ADD COLUMN fact_kind VARCHAR(32)",
        "ALTER TABLE pending_memory ADD COLUMN fact_key TEXT",
        "ALTER TABLE pending_memory ADD COLUMN fact_value TEXT",
        "ALTER TABLE pending_memory ADD COLUMN reject_reason TEXT",
    ):
        _apply_migration_ddl(_engine, _ddl)
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_tenant_id"
        " ON pending_memory (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_status"
        " ON pending_memory (status)",
        "CREATE INDEX IF NOT EXISTS idx_pending_memory_vendor"
        " ON pending_memory (vendor)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # SQLite 迁移（P3-T4 可观测与可重跑 / B6）：memory_extract_runs 新表 + 索引。
    # 幂等 CREATE TABLE IF NOT EXISTS 走既有迁移块（与 pending_memory / eval_candidate
    # 同款兜底，覆盖裸库场景）；新库由上方 create_all 按 ORM 声明建出等价 schema；
    # 仅新建表/索引，禁重建表。表结构含 run_id 自增主键与 tenant_id 过滤列，二者的
    # 消费方分别是 `list_memory_extract_runs` 的倒序分页与租户隔离。
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS memory_extract_runs ("
        " run_id INTEGER NOT NULL PRIMARY KEY,"
        " tenant_id VARCHAR(64) DEFAULT 'default',"
        " receipt_id INTEGER,"
        " vendor VARCHAR DEFAULT '',"
        " supplier_id INTEGER,"
        " trigger VARCHAR(32) DEFAULT '',"
        " diffs_count INTEGER DEFAULT 0,"
        " facts_produced INTEGER DEFAULT 0,"
        " facts_rejected INTEGER DEFAULT 0,"
        " reject_reasons_json TEXT DEFAULT '[]',"
        " prompt_source VARCHAR(32) DEFAULT '',"
        " error TEXT DEFAULT '',"
        " created_at TEXT DEFAULT '')",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_memory_extract_runs_tenant_id"
        " ON memory_extract_runs (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_extract_runs_receipt_id"
        " ON memory_extract_runs (receipt_id)",
        "CREATE INDEX IF NOT EXISTS idx_memory_extract_runs_created_at"
        " ON memory_extract_runs (created_at)",
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
        _migration_failed("[migrate] vendor_memory memory_id backfill skipped: %s", _e)

    # 索引：memory_id 唯一约束 + vendor 查询索引（先查再执行幂等，放在回填之后）
    for _ddl in (
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_memory_memory_id ON vendor_memory (memory_id)",
        "CREATE INDEX IF NOT EXISTS idx_vendor_memory_vendor ON vendor_memory (vendor)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # -----------------------------------------------------------------
    # BOM / 库存消耗整改迁移（WS1/WS2/WS4/WS6）：
    # 幂等口径同前（先查再执行；仅 ADD COLUMN / CREATE TABLE / CREATE INDEX）；
    # dish_ingredients 的组件列变更（唯一约束变更）走下方数据保全式重建。
    # -----------------------------------------------------------------
    for _ddl in (
        # WS2：BOM 版本号（乐观锁基线）
        "ALTER TABLE dishes ADD COLUMN version INTEGER DEFAULT 1",
        # WS1：出成率（有效领料量 = 配方用量 / yield_rate）
        "ALTER TABLE dish_ingredients ADD COLUMN yield_rate REAL DEFAULT 1.0",
        # WS4：daily_dish_consumptions 补 tenant_id 列（归属不再仅靠 join 推断）
        "ALTER TABLE daily_dish_consumptions ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'default'",
        # 餐品分类字段（兼容历史裸库升级）
        "ALTER TABLE dishes ADD COLUMN category VARCHAR DEFAULT ''",
    ):
        _apply_migration_ddl(_engine, _ddl)
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_daily_dish_consumptions_tenant_id"
        " ON daily_dish_consumptions (tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_dish_ingredients_component"
        " ON dish_ingredients (component_type, component_id)",
        "CREATE INDEX IF NOT EXISTS idx_dishes_category"
        " ON dishes (category)",
        "CREATE INDEX IF NOT EXISTS idx_dishes_tenant_category"
        " ON dishes (tenant_id, category)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # WS2：BOM 版本快照表（CREATE TABLE IF NOT EXISTS 兜底覆盖裸库场景）
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS dish_recipe_versions ("
        " id INTEGER NOT NULL PRIMARY KEY,"
        " dish_id INTEGER NOT NULL,"
        " version INTEGER DEFAULT 1,"
        " snapshot_json TEXT DEFAULT '{}',"
        " change_summary VARCHAR DEFAULT '',"
        " changed_by VARCHAR DEFAULT '',"
        " changed_at TEXT DEFAULT '',"
        " tenant_id VARCHAR(64) DEFAULT 'default')",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS idx_dish_recipe_versions_dish_id"
        " ON dish_recipe_versions (dish_id)",
        "CREATE INDEX IF NOT EXISTS idx_dish_recipe_versions_tenant_id"
        " ON dish_recipe_versions (tenant_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # WS5：价格异动处置留痕表（幂等 CREATE TABLE IF NOT EXISTS + 索引，覆盖裸库场景；
    # 新库由上方 create_all 按 ORM 声明建出等价 schema；仅新建表/索引，禁重建表）。
    _apply_migration_ddl(
        _engine,
        "CREATE TABLE IF NOT EXISTS price_anomaly_ack ("
        " id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,"
        " tenant_id VARCHAR(64) DEFAULT 'default',"
        " sku_id INTEGER NOT NULL,"
        " action VARCHAR DEFAULT 'ignore',"
        " threshold_pct FLOAT DEFAULT 0,"
        " snapshot_vs_pct FLOAT DEFAULT 0,"
        " created_at VARCHAR DEFAULT '')",
    )
    for _ddl in (
        "CREATE INDEX IF NOT EXISTS ix_price_anomaly_ack_tenant_id"
        " ON price_anomaly_ack (tenant_id)",
        "CREATE INDEX IF NOT EXISTS ix_price_anomaly_ack_sku_id"
        " ON price_anomaly_ack (sku_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_price_anomaly_ack_sku_tenant"
        " ON price_anomaly_ack (sku_id, tenant_id)",
    ):
        _apply_migration_ddl(_engine, _ddl)

    # WS4：一次性幂等回填 —— 存量消耗行按关联 dish 补 tenant_id。
    # 判据（关键修正）：ADD COLUMN ... DEFAULT 'default' 会把存量行填成占位值
    # 'default'（而非 NULL），故待回填集合必须含 NULL / '' / 占位 'default' 三种；
    # 且仅在关联 dish 的 tenant_id 是真实非 default 值时才改写（避免空转与误改）。
    # 改写后行值即等于 dish 租户，不再命中判据 —— 二次 import 零动作（幂等）。
    # 依赖 dishes 行存在（硬删菜品的历史消耗无法回填，读取侧保留 join 回退）。
    try:
        from sqlalchemy import text as _sa_text_tc
        with _engine.connect() as _c:
            _c.execute(_sa_text_tc(
                "UPDATE daily_dish_consumptions "
                "SET tenant_id = (SELECT tenant_id FROM dishes "
                "                 WHERE dishes.id = daily_dish_consumptions.dish_id) "
                "WHERE (tenant_id IS NULL OR tenant_id = '' OR tenant_id = 'default') "
                "  AND dish_id IN (SELECT id FROM dishes) "
                "  AND COALESCE((SELECT tenant_id FROM dishes "
                "                WHERE dishes.id = daily_dish_consumptions.dish_id), '') "
                "      NOT IN ('', 'default')"
            ))
            _c.commit()
    except Exception as _e:
        _migration_failed("[migrate] daily_dish_consumptions tenant_id backfill skipped: %s", _e)

    # 餐品分类基线重构：存量空值及「其他」分类一次性平滑迁移至「默认」
    try:
        from sqlalchemy import text as _sa_text_dc
        with _engine.connect() as _c:
            _c.execute(_sa_text_dc(
                "UPDATE dishes "
                "SET category = '默认' "
                "WHERE category IS NULL OR category = '' OR category = '其他'"
            ))
            _c.commit()
    except Exception as _e:
        _migration_failed("[migrate] dishes category backfill skipped: %s", _e)

    # -----------------------------------------------------------------
    # 遗留物理形态重建（本轮单独授权，仅限 vendor_memory / skus / suppliers /
    # dish_ingredients）：
    # SQLite 无法 ALTER 主键/唯一约束，检测到旧物理形态时做数据保全式标准
    # 重建（CREATE 新表 → 按列拷贝 → 校验行数 → DROP 旧表 → RENAME，最后补索引）。
    # 铁律：新形态库零动作；拷贝保全全部列（缺失列按默认值填充）；失败只告警
    # 不阻断启动，且任何失败路径都不得先删旧表。
    # -----------------------------------------------------------------
    _rebuild_legacy_vendor_memory(_engine)
    _rebuild_legacy_skus_name_unique(_engine)
    _rebuild_legacy_suppliers_name_unique(_engine)
    _rebuild_legacy_dish_ingredients(_engine)
    # WS3：门店覆盖归属 app_setting_overrides（与 app_settings 全局桶分表）。
    # app_settings 保持单列主键 (key) 形态 —— 新库由 create_all 直建，旧库/漂移库
    # 物理形态原样保留即可照常读写（key 单列在主键或唯一索引上均已唯一）。
    # 曾经的复合主键重建机器已移除（消除「第二列退化为常量 + 多余重建」的冗余）。
    _ensure_app_setting_overrides_table(_engine)
    _migrate_app_settings_overrides(_engine)

    _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow = ReceiptRow, ItemRow, SkuRow, StockLogRow
    _SupplierRow, _DeptRow, _PaymentRow = SupplierRow, DeptRow, PaymentRow
    _VendorMemoryRow, _AppSettingRow = VendorMemoryRow, AppSettingRow
    _AppSettingOverrideRow = AppSettingOverrideRow
    _VendorSkuAliasRow = VendorSkuAliasRow
    _DecisionLogRow = DecisionLogRow
    _SnapshotRow = SnapshotRow
    _UserEventRow = UserEventRow
    _ExperimentRow = ExperimentRow
    _ExperimentAssignRow = ExperimentAssignRow
    _ReceiptFeedbackRow = ReceiptFeedbackRow
    _PendingMemoryRow = PendingMemoryRow
    _MemoryExtractRunRow = MemoryExtractRunRow
    _EvalCandidateRow = EvalCandidateRow
    _GuardrailEventRow = GuardrailEventRow
    _DishRow, _DishIngredientRow = DishRow, DishIngredientRow
    _DishRecipeVersionRow = DishRecipeVersionRow
    _InventoryBatchRow = InventoryBatchRow
    _DailyConsumptionRow, _DailyConsumptionDetailRow = DailyConsumptionRow, DailyConsumptionDetailRow
    _SecurityEventRow = SecurityEventRow

    _run_ordered_migrations(_engine)
    return Base


# -----------------------------------------------------------------
# 遗留物理形态检测与数据保全式重建（仅限 vendor_memory / skus / suppliers，单独授权）。
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


# -------------------------------------------------------------
# 有序迁移：schema_version 表记录已执行的版本，每个版本只执行一次。
# v1 是「历史基线」：本文件 _make_engine 里按先查再执行写成的存量幂等 DDL/回填，
# 它们全部成功后才记录 v1；此后所有新增结构变更只能追加到下面的列表，
# 禁止再往 _make_engine 里堆新的 ALTER。任何一步失败直接抛 MigrationError 中止启动。
# -------------------------------------------------------------
def _migrate_v2_receipt_lifecycle_columns(engine):
    for ddl in (
        "ALTER TABLE receipts ADD COLUMN deleted_by VARCHAR",
        "ALTER TABLE receipts ADD COLUMN reversed_at VARCHAR",
        "ALTER TABLE receipts ADD COLUMN reversed_by VARCHAR",
        "ALTER TABLE receipts ADD COLUMN reverse_reason TEXT",
    ):
        _apply_migration_ddl(engine, ddl)


def _migrate_v3_revoked_tokens(engine):
    _apply_migration_ddl(
        engine,
        "CREATE TABLE IF NOT EXISTS revoked_tokens ("
        " jti VARCHAR(64) PRIMARY KEY, exp INTEGER NOT NULL)")


def _migrate_v4_receipt_list_indexes(engine):
    for ddl in (
        "CREATE INDEX IF NOT EXISTS ix_receipts_tenant_status "
        "ON receipts (tenant_id, status, deleted_at)",
        "CREATE INDEX IF NOT EXISTS ix_receipt_items_receipt "
        "ON receipt_items (receipt_id)",
    ):
        _apply_migration_ddl(engine, ddl)


SCHEMA_MIGRATIONS = [
    (1, "baseline_inline_schema", None),
    (2, "receipt_lifecycle_columns", _migrate_v2_receipt_lifecycle_columns),
    (3, "revoked_tokens", _migrate_v3_revoked_tokens),
    (4, "receipt_list_indexes", _migrate_v4_receipt_list_indexes),
]


def _run_ordered_migrations(engine):
    from sqlalchemy import text as _t
    with engine.begin() as conn:
        conn.execute(_t(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            " version INTEGER PRIMARY KEY, name VARCHAR NOT NULL,"
            " applied_at VARCHAR NOT NULL)"))
        applied = {r[0] for r in conn.execute(_t("SELECT version FROM schema_version")).fetchall()}
    for version, name, fn in SCHEMA_MIGRATIONS:
        if version in applied:
            continue
        if fn is not None:
            fn(engine)
        with engine.begin() as conn:
            conn.execute(_t(
                "INSERT OR IGNORE INTO schema_version (version, name, applied_at) "
                "VALUES (:v, :n, :t)"),
                         {"v": version, "n": name, "t": datetime.now().isoformat(timespec="seconds")})


def schema_version():
    """当前库已执行到的最大迁移版本（0 表示尚无版本表）。"""
    from sqlalchemy import text as _t
    try:
        with _engine.connect() as conn:
            return int(conn.execute(_t("SELECT COALESCE(MAX(version), 0) FROM schema_version")).scalar() or 0)
    except Exception:
        return 0


def ensure_schema_current():
    """确保迁移已全部执行；缺任何一版即抛 MigrationError（启动时调用，失败中止启动）。"""
    _run_ordered_migrations(_engine)
    latest = SCHEMA_MIGRATIONS[-1][0]
    if schema_version() != latest:
        raise MigrationError(f"库结构版本 {schema_version()} 与程序要求的 {latest} 不一致，已中止启动")


class MigrationError(RuntimeError):
    """库结构迁移失败：直接中止启动，避免带着半套 schema 继续跑业务。"""


def _migration_failed(fmt, *args):
    msg = (fmt % args) if args else fmt
    logger.error(msg)
    raise MigrationError(msg)


def _apply_migration_ddl(engine, ddl):
    """单条迁移 DDL 执行器（R5 静默幂等口径）：

    - 先查再执行：列/索引已存在则静默 continue（零日志，二次 import 干净）；
    - 真实异常（DB locked 等）抛 MigrationError 中止启动；并发进程抢先建好的
      「已存在」不算失败。
    """
    from sqlalchemy import text as _t
    try:
        with engine.connect() as conn:
            if _migration_ddl_already_applied(conn, ddl):
                return  # 已存在：静默跳过，无日志
            conn.execute(_t(ddl))
            conn.commit()
    except Exception as e:
        _low = str(e).lower()
        if "duplicate column" in _low or "already exists" in _low:
            return  # 另一进程抢先完成了同一条迁移
        _migration_failed("[migrate] %s skipped: %s", ddl, e)


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
            # 注意：这里的 decay_score 缺省仍是 1.0，**不是** memory_initial_decay_score。
            # 它只服务「旧物理形态重建时给缺失列填空」，填的是既有历史行 —— 历史行降分
            # 等于在迁移里悄悄改写信任分；新写入行的起始分由 upsert_vendor_memory 决定
            # （P1-T2 起 = memory_initial_decay_score）。两处口径不同是刻意的，非漏改。
            target_cols = [
                ("memory_id", None), ("vendor", None), ("tenant_id", "'default'"),
                ("notes", "''"), ("sample", "''"), ("version", "1"),
                ("source_kind", "'manual'"), ("source_ref", "'[]'"),
                ("created_at", "''"), ("updated_at", "''"),
                ("hit_count", "0"), ("last_hit_at", "''"),
                ("decay_score", "1.0"), ("status", "'active'"),
                # P1-T3 三列必须在这里同步登记：重建分支从 target_cols 建新表，
                # 而 ALTER 迁移块跑在重建之前 —— 漏登记就等于"重建后的库缺列"，
                # 且 ALTER 不会补第二次（本仓高发的「同一根因多份实现只改一份」）。
                ("fact_key", None), ("superseded_by", None), ("valid_to", "''"),
                # P2-T1 同一条纪律：supplier_id 也必须在这里登记（本函数是
                # vendor_memory schema 的第二份实现）。
                ("supplier_id", None),
                # P3-T1 同一条纪律：结构化事实载荷五列同样必须登记，否则"重建过的库
                # 缺列"（而 ALTER 块跑在重建之前，不会补第二次）。
                ("fact_kind", None), ("fact_value", None), ("trust_level", None),
                ("valid_from", None), ("written_by", None),
            ]
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
            conn.execute(_t(
                f"CREATE TABLE {tmp} ("
                " memory_id VARCHAR NOT NULL, vendor VARCHAR,"
                " tenant_id VARCHAR(64), notes TEXT, sample TEXT,"
                " version INTEGER, source_kind VARCHAR(32), source_ref TEXT,"
                " created_at TEXT, updated_at TEXT, hit_count INTEGER,"
                " last_hit_at TEXT, decay_score FLOAT, status VARCHAR(16),"
                " fact_key TEXT, superseded_by VARCHAR, valid_to TEXT,"
                " supplier_id INTEGER, fact_kind VARCHAR(32), fact_value TEXT,"
                " trust_level FLOAT, valid_from TEXT, written_by VARCHAR,"
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
                                    "status": "active",
                                    # P3-T1 载荷五列填 None（NULL）而不是 ""：
                                    # 与 ALTER 路径对既有行的处理保持一致 ——
                                    # "" 在读侧是"有值且为空"，NULL 是"没有该字段"。
                                    "fact_kind": None, "fact_value": None,
                                    "trust_level": None, "valid_from": None,
                                    "written_by": None}.get(col, "")
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
            "CREATE INDEX IF NOT EXISTS idx_vendor_memory_supplier_id"
            " ON vendor_memory (supplier_id)",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(_t(ddl))
            except Exception as e:
                _migration_failed("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        _migration_failed("[migrate] vendor_memory legacy rebuild skipped: %s", e)
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
                _migration_failed("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        _migration_failed("[migrate] skus legacy rebuild skipped: %s", e)
        _drop_rebuild_tmp_table(engine, tmp)


def _rebuild_legacy_suppliers_name_unique(engine):
    """既有问题 #2（C）：suppliers.name 全局 UNIQUE 约束改按租户命名空间。

    旧形态（建表 SQL 含 name UNIQUE 列约束或存在 name 上的唯一索引，
    如 create_all 生成的 ix_suppliers_name UNIQUE）上跨租户同名供应商会
    IntegrityError。检测到 name 唯一索引即数据保全式重建为非唯一形态；
    create_supplier 的 scoped 重名检查成为唯一防重线（同租户重名仍被拒，
    跨租户允许同名 —— 与 skus.name 的既有修法同口径）。
    """
    table, tmp = "suppliers", "suppliers__rebuild"
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
                ("id", None), ("name", "''"), ("supplier_code", "''"),
                ("active", "1"), ("payment_terms_days", None),
                ("settlement_pref", "''"), ("contact_phone", "''"),
                ("notes", "''"), ("tenant_id", "'default'"),
            ]
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
            conn.execute(_t(
                f"CREATE TABLE {tmp} ("
                " id INTEGER NOT NULL, name VARCHAR, supplier_code VARCHAR,"
                " active INTEGER, payment_terms_days INTEGER,"
                " settlement_pref VARCHAR, contact_phone VARCHAR,"
                " notes VARCHAR, tenant_id VARCHAR(64), PRIMARY KEY (id))"
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
            "[migrate] suppliers legacy global UNIQUE(name) rebuilt to "
            "per-tenant namespace (rows=%s)", new_cnt)
        for ddl in (
            "CREATE INDEX IF NOT EXISTS ix_suppliers_name ON suppliers (name)",
            "CREATE INDEX IF NOT EXISTS idx_suppliers_tenant_id"
            " ON suppliers (tenant_id)",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(_t(ddl))
            except Exception as e:
                _migration_failed("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        _migration_failed("[migrate] suppliers legacy rebuild skipped: %s", e)
        _drop_rebuild_tmp_table(engine, tmp)


def _rebuild_legacy_dish_ingredients(engine):
    """WS6：dish_ingredients 旧形态物理重建（单层 sku → 支持 sku/dish 两级组件）。

    旧形态唯一约束为 (dish_id, sku_id)，且无 component_type / component_id 列，
    无法表达「餐品含子配方」。检测到新列缺失或唯一约束未覆盖组件三元组即做
    数据保全式重建：sku_id 非空行回填 component_id=sku_id、component_type='sku'。
    """
    table, tmp = "dish_ingredients", "dish_ingredients__rebuild"
    try:
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            cols = _table_columns(conn, table)
            if not cols:
                return  # 表不存在（全新库由 create_all 建出正确形态）
            uniques = _table_unique_index_columns(conn, table)
            new_form = (["dish_id", "component_type", "component_id"] in uniques)
            if "component_type" in cols and "component_id" in cols and new_form:
                return  # 已是新形态：零动作
            target_cols = [
                ("id", None), ("dish_id", None), ("sku_id", None),
                ("component_type", "'sku'"),
                ("consumption_qty", "0"), ("unit", "''"),
                ("yield_rate", "1.0"), ("notes", "''"),
            ]
            # component_id：新库已有该列则直接沿用，旧库由 sku_id 派生
            has_component_id = "component_id" in cols
            conn.execute(_t(f"DROP TABLE IF EXISTS {tmp}"))
            conn.execute(_t(
                f"CREATE TABLE {tmp} ("
                " id INTEGER NOT NULL, dish_id INTEGER NOT NULL, sku_id INTEGER,"
                " component_type VARCHAR(16), component_id INTEGER,"
                " consumption_qty FLOAT, unit VARCHAR, yield_rate FLOAT,"
                " notes VARCHAR, PRIMARY KEY (id),"
                " UNIQUE (dish_id, component_type, component_id))"
            ))
            select_exprs = []
            for col, default in target_cols:
                select_exprs.append(f'"{col}"' if col in cols else default)
            select_exprs.append(
                '"component_id"' if has_component_id else '"sku_id"')
            conn.execute(_t(
                f"INSERT INTO {tmp} ({', '.join(c for c, _d in target_cols)},"
                f" component_id) SELECT {', '.join(select_exprs)} FROM {table}"
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
            "[migrate] dish_ingredients legacy single-level schema rebuilt to "
            "sku/dish component schema (rows=%s)", new_cnt)
        for ddl in (
            "CREATE INDEX IF NOT EXISTS ix_dish_ingredients_dish_id"
            " ON dish_ingredients (dish_id)",
            "CREATE INDEX IF NOT EXISTS ix_dish_ingredients_sku_id"
            " ON dish_ingredients (sku_id)",
            "CREATE INDEX IF NOT EXISTS ix_dish_ingredients_component_id"
            " ON dish_ingredients (component_id)",
            "CREATE INDEX IF NOT EXISTS idx_dish_ingredients_component"
            " ON dish_ingredients (component_type, component_id)",
        ):
            try:
                with engine.begin() as conn:
                    conn.execute(_t(ddl))
            except Exception as e:
                _migration_failed("[migrate] %s skipped: %s", ddl, e)
    except Exception as e:
        _migration_failed("[migrate] dish_ingredients legacy rebuild skipped: %s", e)
        _drop_rebuild_tmp_table(engine, tmp)


def _ensure_app_setting_overrides_table(engine):
    """幂等确保 app_setting_overrides 表存在（WS3 门店覆盖的唯一归属）。

    新库由上方 create_all 按 ORM 声明直建等价 schema；旧库/裸库由此
    CREATE TABLE IF NOT EXISTS 兜底。只建表，**不动 app_settings 物理形态**：
    app_settings 保持单列主键 (key) 的全局桶，门店覆盖一律落本表。
    幂等：表已存在时 _apply_migration_ddl 静默跳过；失败只告警不阻断启动。
    """
    _apply_migration_ddl(
        engine,
        "CREATE TABLE IF NOT EXISTS app_setting_overrides ("
        " key VARCHAR NOT NULL,"
        " tenant_id VARCHAR(64) NOT NULL DEFAULT 'default',"
        " value TEXT, PRIMARY KEY (key, tenant_id))")


def _migrate_app_settings_overrides(engine):
    """WS3：把 app_settings 里遗留的非全局行（tenant_id != 'default'）迁到
    app_setting_overrides，使 app_settings 真正只余全局桶。

    why 需要：历史中间态把「门店覆盖行」也存在 app_settings 里（同 key 多行）。
    本设计下 app_settings 单列主键只放全局行，全局读取按 key 取值若残留门店行
    会误命中；迁走后读全局只命中全局行。
    幂等：无遗留行则零动作（新库 app_settings 无 tenant_id 列，直接返回）；
    只做 INSERT OR IGNORE + DELETE，绝不丢数据；失败只 logger.warning 不阻断启动。
    """
    try:
        from sqlalchemy import text as _t
        with engine.begin() as conn:
            cols = _table_columns(conn, "app_settings")
            if not cols or "tenant_id" not in cols:
                return  # 无需搬迁（新库/原始旧库的 app_settings 无 tenant_id 列）
            res = conn.execute(_t(
                "INSERT OR IGNORE INTO app_setting_overrides (key, tenant_id, value) "
                "SELECT key, tenant_id, value FROM app_settings "
                "WHERE tenant_id IS NOT NULL AND tenant_id != '' AND tenant_id != 'default'"))
            moved = getattr(res, "rowcount", 0) or 0
            conn.execute(_t(
                "DELETE FROM app_settings "
                "WHERE tenant_id IS NOT NULL AND tenant_id != '' AND tenant_id != 'default'"))
            if moved:
                logger.warning(
                    "[migrate] app_settings -> app_setting_overrides moved %s tenant row(s)",
                    moved)
    except Exception as e:
        _migration_failed("[migrate] app_settings override migration skipped: %s", e)


def _backfill_receipt_supplier_ids(engine):
    """D17：receipts.supplier_id 幂等回填（结构关联修复，用户决策）。

    口径（三步，顺序固定）：
    1. 先按 receipts.supplier_name 与 suppliers.name **精确匹配**填 supplier_id；
    2. 剩余未匹配的，按 canonical_supplier_key(receipts.supplier_name) 与档案名
       归一 key 匹配填；
    3. 仍匹配不到就保持 NULL —— **绝不凭空创建供应商档案**。
    空 supplier_name（NULL / ''）不参与任何匹配（避免 345 张空名单被误挂）。

    租户口径：仅在同一租户内匹配（tenant_id 为空按 default 处理），避免 A 租户
    单据被挂到 B 租户同名档案上（跨租户错挂）。

    幂等：仅处理 supplier_id IS NULL 且在本次匹配到档案的行；改写后即脱离待处理
    集合，二次执行为 no-op（零行更新）。表/列缺失（裸库）直接静默返回。
    """
    try:
        from sqlalchemy import text as _t
        from app.services.supplier_normalizer import canonical_supplier_key
        with engine.begin() as conn:
            rcols = _table_columns(conn, "receipts")
            scols = _table_columns(conn, "suppliers")
            if (not rcols or "supplier_id" not in rcols
                    or "supplier_name" not in rcols):
                return
            if not scols or "id" not in scols or "name" not in scols:
                return
            same_tenant = (
                "COALESCE(NULLIF(s.tenant_id, ''), 'default') = "
                "COALESCE(NULLIF(receipts.tenant_id, ''), 'default')")
            # 步 1：精确名匹配（同租户内；档案名物理唯一）
            conn.execute(_t(
                "UPDATE receipts SET supplier_id = ("
                "  SELECT s.id FROM suppliers s "
                "  WHERE s.name = receipts.supplier_name AND " + same_tenant +
                ") WHERE supplier_id IS NULL "
                "  AND supplier_name IS NOT NULL AND supplier_name <> '' "
                "  AND EXISTS (SELECT 1 FROM suppliers s "
                "              WHERE s.name = receipts.supplier_name AND "
                + same_tenant + ")"))
            # 步 2：归一 key 匹配（剩余未匹配行）
            canon_map = {}
            for sid, sname, stid in conn.execute(_t(
                    "SELECT id, name, tenant_id FROM suppliers ORDER BY id")).fetchall():
                key = canonical_supplier_key(sname)
                tkey = (str(stid or "").strip() or "default", key)
                if key and tkey not in canon_map:
                    canon_map[tkey] = sid
            if canon_map:
                pending = conn.execute(_t(
                    "SELECT id, supplier_name, tenant_id FROM receipts "
                    "WHERE supplier_id IS NULL "
                    "  AND supplier_name IS NOT NULL AND supplier_name <> ''"
                )).fetchall()
                updates = []
                for rid, rname, rtid in pending:
                    key = canonical_supplier_key(rname)
                    sid = canon_map.get(
                        (str(rtid or "").strip() or "default", key))
                    if sid is not None:
                        updates.append({"sid": sid, "rid": rid})
                if updates:
                    conn.execute(_t(
                        "UPDATE receipts SET supplier_id = :sid WHERE id = :rid"),
                        updates)
    except Exception as e:
        _migration_failed("[migrate] receipts.supplier_id backfill skipped: %s", e)


_make_engine()


_ambient_session_ctx = contextvars.ContextVar("ambient_session", default=None)


class AtomicAborted(RuntimeError):
    """atomic() 块内有函数调用了 rollback()：整个事务作废，不得部分提交。"""


class _AmbientSession:
    """atomic() 块内共享的会话代理。

    块内所有经 get_session() 取会话的 db 函数拿到的是同一个会话：它们各自的
    commit() 只做 flush（写入留在同一个事务里），close() 不关闭，真正的提交/回滚
    只发生在 atomic() 退出时。这样 approve 等多步写入无需逐个函数改签名即可原子化。
    """

    def __init__(self, real):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_poisoned", False)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def commit(self):
        self._real.flush()

    def close(self):
        return None

    def rollback(self):
        object.__setattr__(self, "_poisoned", True)
        self._real.rollback()


@contextmanager
def atomic():
    """把块内全部 db 写入收进同一个事务：正常退出一次 commit，任何异常整体回滚。

    可嵌套：内层直接加入外层事务。
    """
    existing = _ambient_session_ctx.get()
    if existing is not None:
        yield existing
        return
    real = _SessionLocal()
    proxy = _AmbientSession(real)
    token = _ambient_session_ctx.set(proxy)
    try:
        yield proxy
        if proxy._poisoned:
            raise AtomicAborted("事务内发生回滚，本次操作已整体撤销")
        real.commit()
    except BaseException:
        real.rollback()
        raise
    finally:
        _ambient_session_ctx.reset(token)
        real.close()


def get_session(tenant_id=None):
    """获取会话。tenant_id 为租户上下文（配合 db.scoped 使用）。

    当前 SQLite 单库实现不按租户分库，tenant_id 仅作上下文透传，
    过滤统一走 db.scoped(query, model, tenant_id)。
    在 atomic() 块内返回共享的事务会话代理（见 _AmbientSession）。
    """
    amb = _ambient_session_ctx.get()
    if amb is not None:
        return amb
    return _SessionLocal()


def scoped(query, model, tenant_id=None):
    """Gap E2 统一租户过滤封装。

    语义：若传入了 tenant_id，按该 tenant_id 过滤；
    若未传入 tenant_id 但当前上下文设置了租户且未 bypass，自动使用上下文租户过滤（DB 级兜底安全收敛）；
    若均未设置，则不过滤（内部脚本与既有测试路径向后兼容）。
    """
    tid = str(tenant_id).strip() if tenant_id and str(tenant_id).strip() else None
    if tid is None and not _bypass_tenant_isolation_ctx.get():
        tid = _current_tenant_ctx.get()
    if tid is None or not str(tid).strip():
        return query
    if hasattr(model, "tenant_id"):
        return query.filter(model.tenant_id == str(tid))
    return query


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
# 后台 Job 跨库写入闸门
# -------------------------------------------------------------
# 说明：识别 Job 跑在后台线程里，写库时读的是「当时的」db.DB_PATH。若任务执行期间
# DB_PATH 被切到另一个库（测试夹具收尾还原、外部调用切换），晚到的 Job 会把结果写进
# 新库 —— 这正是历史「测试污染 live 库」的机制（探针实测 46 次 get_session@LIVE，
# 栈全部来自 receipt_utils 的后台线程）。闸门放在 db 层而不是逐个调用点，是为了覆盖
# Job 线程会走到的一切写库路径（receipt_utils 自身写入 + supervisor 的决策日志/埋点/
# 评测候选回流），避免「修了 receipt_utils 漏了 supervisor」。
_job_db_ctx = threading.local()


def bind_job_db_path(path, job_id=""):
    """把「后台 Job 所属的库路径」绑定到当前线程（线程级，不跨线程泄漏）。

    必须在派发后台线程前或线程启动时调用；只有识别 Job 线程会绑定，普通请求线程
    与脚本从不绑定，因此闸门对它们完全无感。
    """
    _job_db_ctx.path = os.path.abspath(str(path))
    _job_db_ctx.job_id = str(job_id or "")


def unbind_job_db_path():
    """解绑当前线程的 Job 库路径（Job 线程收尾调用；线程结束本身也会释放）。"""
    for _attr in ("path", "job_id"):
        if hasattr(_job_db_ctx, _attr):
            delattr(_job_db_ctx, _attr)


def job_db_path_mismatch():
    """本线程绑定了 Job 库路径且与当前生效 DB_PATH 不一致时，返回 (任务库, 当前库)。

    未绑定（普通请求线程 / 脚本 / 定时任务）恒返回 None。生产路径 DB_PATH 由环境变量
    在导入时确定、运行期不变，绑定值恒等于当前值，故本闸门对正常流程零影响。
    """
    bound = getattr(_job_db_ctx, "path", None)
    if not bound:
        return None
    current = os.path.abspath(str(DB_PATH))
    if current == bound:
        return None
    return bound, current


def _warn_job_write_skipped(fn_name, mismatch):
    """记一条可定位的 warning：哪个 Job、哪个写库函数、从哪个库到哪个库。"""
    bound, current = mismatch
    job_id = getattr(_job_db_ctx, "job_id", "") or "?"
    logger.warning(
        "识别 Job %s 的库路径在任务执行期间被切换（任务库=%s → 当前库=%s），"
        "已放弃 %s 写入以避免污染新库", job_id, bound, current, fn_name)


def _guard_job_db_write(blocked_return=None):
    """写库函数装饰器：Job 线程 + 库路径已变更 → 放弃写入，返回 blocked_return。

    只在「本线程绑定了 Job 库路径」且「与当前 DB_PATH 不一致」时拦截；不抛异常，
    避免把异常抛穿到 Job 外层导致线程卡死或误伤调用方正常返回契约。
    """
    def _decorator(fn):
        @functools.wraps(fn)
        def _wrapper(*args, **kwargs):
            mismatch = job_db_path_mismatch()
            if mismatch is None:
                return fn(*args, **kwargs)
            _warn_job_write_skipped(fn.__name__, mismatch)
            return blocked_return
        return _wrapper
    return _decorator


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


def create_receipt_record(**kwargs):
    """创建完整字段的收据记录并持久化，返回 receipt_id。"""
    if _ReceiptRow is None:
        _make_engine()
    s = get_session()
    try:
        tenant_id = kwargs.pop("tenant_id", "default")
        if "created_at" not in kwargs or not kwargs["created_at"]:
            kwargs["created_at"] = now_iso()
        if "updated_at" not in kwargs or not kwargs["updated_at"]:
            kwargs["updated_at"] = now_iso()
        valid_fields = {c.name for c in _ReceiptRow.__table__.columns}
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in valid_fields}
        row = _ReceiptRow(tenant_id=str(tenant_id or "default"), **filtered_kwargs)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def get_receipt_row(receipt_id, tenant_id=None):
    """按 id 取单据。tenant_id 非空时在 DB 过滤条件中带上 tenant_id，跨租户直接返回 None。"""
    s = get_session()
    try:
        q = scoped(s.query(_ReceiptRow), _ReceiptRow, tenant_id).filter(_ReceiptRow.id == int(receipt_id))
        return q.first()
    finally:
        s.close()


# 金额类列统一舍入到分，数量/单价统一舍入口径见 app/money.py
_RECEIPT_MONEY_FIELDS = frozenset({
    "total_amount", "service_fee", "tax_amount", "discount_amount",
    "delivery_fee", "deposit_amount", "rounding_adjustment",
})


@_guard_job_db_write()
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
                if k in _RECEIPT_MONEY_FIELDS:
                    v = _money.money(v)
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


@_guard_job_db_write()
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
            row.pop("sku_candidates", None)  # 读时现算的只读字段，不落库
            fuzzy = row.pop("fuzzy_candidates", [])
            entity = row.pop("entity_candidates", [])
            raw_name = row.pop("raw_name", None)
            raw_unit = row.pop("raw_unit", None)
            is_void = 1 if row.pop("is_void", 0) else 0
            actual_qty = row.pop("actual_qty", None)
            if actual_qty is not None:
                actual_qty = _money.qty(actual_qty)
            if row.get("amount") is not None:
                row["amount"] = _money.money(row["amount"])
            if row.get("unit_price") is not None:
                row["unit_price"] = _money.unit_price(row["unit_price"])
            if row.get("quantity") is not None:
                row["quantity"] = _money.qty(row["quantity"])
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

    健壮性（为什么必须显式重建）：历史值一旦损坏（非 JSON / 非数组），原先
    `json.loads(row.value or "[]")` 会**每次调用都抛错**，而所有调用点的
    `except Exception: pass` 又会把它静默吞掉 —— 结果是审计链路外观完全正常、
    实际一条也写不进去，且没有任何日志可查。故此处对坏值显式重建为空数组并告警，
    让审计能力自愈且失败可观测。
    """
    _s = get_session()
    try:
        row = _s.get(_AppSettingRow, "system_audit_json")
        if row is None:
            row = _AppSettingRow(key="system_audit_json")
            _s.add(row)
        raw = row.value
        logs = []
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    logs = parsed
                else:
                    logger.warning(
                        "system_audit_json 值不是数组(%s)，已重建为空数组并继续追加审计；"
                        "原值前 200 字符=%r", type(parsed).__name__, str(raw)[:200])
            except Exception as e:
                logger.warning(
                    "system_audit_json 损坏无法解析(%s: %s)，已重建为空数组并继续追加审计；"
                    "原值前 200 字符=%r", type(e).__name__, e, str(raw)[:200])
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
    """读取系统级审计日志（app_settings.system_audit_json）。无数据返回 []。

    与写入侧同口径：坏值不外抛（否则管理台审计面板整页 500），记 warning 后按空返回。
    """
    _s = get_session()
    try:
        row = _s.get(_AppSettingRow, "system_audit_json")
        if row is None or not row.value:
            return []
        try:
            logs = json.loads(row.value)
        except Exception as e:
            logger.warning(
                "system_audit_json 损坏无法解析(%s: %s)，审计面板按空返回；原值前 200 字符=%r",
                type(e).__name__, e, str(row.value)[:200])
            return []
        return logs if isinstance(logs, list) else []
    finally:
        _s.close()


def list_all_image_paths():
    """列出全部单据（含软删）的 image_path 原始值，只取该单列。

    why: 预览转码缓存（uploads/<stem>_web.jpg）的清理必须先证明「已无任何单据引用该
    源文件」。软删单据可经回收站恢复、恢复后仍需展示原图，故不得过滤 deleted_at；
    同理不做租户过滤 —— 跨租户引用同名源文件同样构成引用，漏判即误删缓存。
    只查单列（不加载整行 ORM 对象），供一次引用判定覆盖全部单据。
    """
    if _ReceiptRow is None:
        return []
    _s = get_session()
    try:
        return [row[0] or "" for row in _s.query(_ReceiptRow.image_path).all()]
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


def set_golden_sample(receipt_id, flag=1, tenant_id="default"):
    """标记/取消黄金基准集成员（幂等，可逆；仅改标记列，不动单据本体与审计链）。

    真幂等：仅当目标 flag 与库中现值不同时才写库。
    启动自愈 sync_evalset_receipts_linkage 每次 uvicorn --reload 重启都会调用本函数，
    若无条件 UPDATE updated_at，会导致黄金样本的时间戳被无业务含义地刷新。
    """
    if receipt_id is None or _ReceiptRow is None:
        return False
    _s = get_session()
    try:
        row = _s.get(_ReceiptRow, int(receipt_id))
        if row is None or not _tenant_ok(row, tenant_id):
            return False
        target = 1 if flag else 0
        # 现值可能是 None（历史空值），按 0 处理，避免 None != 0 造成假变更
        if (row.is_golden_sample or 0) == target:
            return True
        row.is_golden_sample = target
        row.updated_at = now_iso()
        _s.commit()
        return True
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


def list_receipt_rows_page(tenant_id=None, status=None, q=None, limit=None, offset=0):
    """分页/过滤版单据列表：返回 (rows, total)。

    status 可为逗号分隔的多个状态；q 在供应商名、开单日期、单号中模糊匹配；
    limit 为 None 表示不分页（与 list_receipt_rows 同结果，保持无参调用兼容）。
    """
    from sqlalchemy import or_
    _s = get_session()
    try:
        qry = scoped(_s.query(_ReceiptRow), _ReceiptRow, tenant_id).filter(
            _ReceiptRow.deleted_at.is_(None))
        if status:
            wanted = [x.strip() for x in str(status).split(",") if x.strip()]
            if wanted:
                qry = qry.filter(_ReceiptRow.status.in_(wanted))
        if q and str(q).strip():
            term = str(q).strip()
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conds = [_ReceiptRow.supplier_name.like(like, escape="\\"),
                     _ReceiptRow.receipt_date.like(like, escape="\\")]
            if term.lstrip("#").isdigit():
                conds.append(_ReceiptRow.id == int(term.lstrip("#")))
            qry = qry.filter(or_(*conds))
        total = qry.count()
        qry = qry.order_by(_ReceiptRow.id.desc())
        if limit is not None:
            qry = qry.offset(max(0, int(offset or 0))).limit(max(1, int(limit)))
        return qry.all(), total
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


def get_receipt_items_multi(receipt_ids=None, tenant_id=None):
    if receipt_ids is not None and len(receipt_ids) == 0:
        return {}
    s = get_session()
    try:
        q = scoped(s.query(_ItemRow), _ItemRow, tenant_id)
        if receipt_ids is not None:
            int_ids = [int(rid) for rid in receipt_ids]
            q = q.filter(_ItemRow.receipt_id.in_(int_ids))
        rows = q.all()
        result = {}
        for r in rows:
            result.setdefault(r.receipt_id, []).append(_row_to_item(r))
        return result
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
    """写库存流水 + 更新 SKU 当前库存 + 挂载入库/消耗批次处理钩子。"""
    qty = _money.qty(qty)
    amount = _money.money(amount)
    s = get_session()
    try:
        log_amount = amount
        if sku_id:
            sku = s.get(_SkuRow, int(sku_id))
            if sku:
                if kind in ("in", "stocktake"):
                    sku.current_stock = _money.qty(
                        sku.current_stock + qty if kind == "in" else qty)
                    if kind == "in" and amount > 0:
                        sku.last_unit_price = (_money.unit_price(amount / qty) if qty
                                               else sku.last_unit_price)
                elif kind in ("consume", "waste"):
                    sku.current_stock = _money.qty(sku.current_stock - qty)
                    from app.services.costing_service import CostingService
                    fifo_cost, _ = CostingService.deduct_consumption_fifo(
                        session=s,
                        sku_id=sku.id,
                        qty_needed=qty,
                        unit_needed=unit or sku.base_unit,
                        date=date or now_iso()[:10],
                    )
                    if (log_amount == 0 or log_amount is None) and fifo_cost > 0:
                        log_amount = round(fifo_cost, 2)
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
        s.add(_StockLogRow(sku_id=sku_id, name=name, qty=qty, unit=unit, amount=log_amount,
                           vendor=vendor, date=date, receipt_id=receipt_id,
                           kind=kind, note=note, created_at=now_iso(),
                           tenant_id=str(tenant_id or "default")))
        s.commit()
    finally:
        s.close()


def stocktake_sku(sku_id, actual_qty, note="", tenant_id="default"):
    s = get_session()
    try:
        sku = s.get(_SkuRow, int(sku_id))
        if sku is None:
            return None
        diff = round(actual_qty - sku.current_stock, 4)
        now_date = now_iso()[:10]
        cost = 0.0
        from app.services.costing_service import CostingService
        if diff < 0:
            cost, _ = CostingService.deduct_consumption_fifo(
                session=s,
                sku_id=sku.id,
                qty_needed=abs(diff),
                unit_needed=sku.base_unit,
                date=now_date,
            )
        elif diff > 0:
            unit_cost = sku.last_unit_price if (sku.last_unit_price and sku.last_unit_price > 0) else 0.0
            if unit_cost == 0.0:
                last_b = s.query(_InventoryBatchRow).filter(
                    _InventoryBatchRow.sku_id == sku.id,
                    _InventoryBatchRow.unit_cost > 0
                ).order_by(_InventoryBatchRow.id.desc()).first()
                if last_b:
                    unit_cost = last_b.unit_cost
            CostingService.record_inbound_batch(
                session=s,
                sku_id=sku.id,
                qty=diff,
                unit_price=unit_cost,
                unit=sku.base_unit,
                date=now_date,
                receipt_id=None,
            )
            cost = round(diff * unit_cost, 2)

        s.add(_StockLogRow(sku_id=sku.id, name=sku.name, qty=diff, unit=sku.base_unit,
                           amount=round(cost, 2), vendor="", date=now_date, receipt_id=None,
                           kind="stocktake", note=note or ("盘点盘盈入库调整" if diff > 0 else "盘点盘亏核销"), created_at=now_iso(),
                           tenant_id=str(tenant_id or "default")))
        sku.current_stock = round(actual_qty, 4)
        s.commit()
        return sku
    finally:
        s.close()


def delete_sku(sku_id, tenant_id=None):
    """安全删除/停用 SKU：
    - 若无任何引用（库存流水 / 收据明细 / BOM 配方）：物理删除并返回 (True, 'DELETED')
    - 有任一引用：标记停用 active=0 并返回 (True, 'DEACTIVATED')
    - 若不存在：返回 (False, 'NOT_FOUND')
    - 租户防御（Wave A F2）：tenant_id 非空时校验 SKU 归属，跨租户视为
      不存在（返回 (False, 'NOT_FOUND')，与不存在同形态）；None=不过滤
      （向后兼容内部脚本与既有测试路径）。

    WS3：BOM 引用保护 —— 被 dish_ingredients 引用的 SKU 不得物理删除
    （否则配方出现悬空行）。引用计数含 sku 组件（component_id=sku_id）与旧库
    回填前的 sku_id 直连行，与既有 DEACTIVATED 形态一致。
    """
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is None or not _tenant_ok(row, tenant_id):
            return False, "NOT_FOUND"
        log_count = s.query(_StockLogRow).filter(_StockLogRow.sku_id == int(sku_id)).count()
        item_count = s.query(_ItemRow).filter(_ItemRow.sku_id == int(sku_id)).count()
        bom_count = (
            s.query(_DishIngredientRow)
            .filter(
                (_DishIngredientRow.sku_id == int(sku_id))
                | (_DishIngredientRow.component_id == int(sku_id))
            )
            .count()
        )
        if log_count == 0 and item_count == 0 and bom_count == 0:
            # P2-T2：别名行也是引用方 —— 物理删除前先级联清掉指向它的别名
            #（否则留下指向不存在 sku_id 的死链；读取侧虽会因取不到 SKU 而回落，
            #  但表里会永久积累无效行，且「别名命中率」这类度量会被污染）。
            # 别名**不**计入"存在引用则只停用"的判据：别名是 SKU 的派生索引，
            # 不是业务引用，删掉 SKU 时一并退休是正确语义。
            s.query(_VendorSkuAliasRow).filter(
                _VendorSkuAliasRow.sku_id == int(sku_id)).delete(synchronize_session=False)
            s.delete(row)
            s.commit()
            return True, "DELETED"
        else:
            row.active = 0
            s.commit()
            return True, "DEACTIVATED"
    finally:
        s.close()


def _repoint_vendor_sku_alias(session, secondary_ids, primary_id):
    """P2-T2 钩子 C：把指向被合并 SKU 的别名改指到主 SKU。

    why 必须做：别名是「这个写法 → 这个 SKU」的直接映射。合并后若仍指向副 SKU，
    读取侧（`find_vendor_sku_alias`）拿到的是一条已停用（active=0）的死链 → 该供应商
    历史上沉淀的全部写法当场作废，匹配率白白回退（典型的"修了 A 漏了 B"）。

    这里可以直接批量 UPDATE，不必逐行判重：唯一键 (tenant_id, supplier_id, raw_name)
    保证「同一写法 + 同一供应商只有一行」，副侧不可能撞上主侧已有的同键行。若将来
    有写入方绕过唯一键（表外写入），事务会**显式失败**而不是静默丢行 —— 这正是要的
    失败姿态（静默丢别名＝历史写法无声蒸发）。

    **两个合并入口（merge_skus / deduplicate_skus_by_canonical）共用本函数** ——
    本仓高发的「同一根因多份实现只改一份」在这里就表现为：只给 merge_skus 加联动，
    自动去重的启动自愈（main.py 每次启动都调 deduplicate_skus_by_canonical）仍会把
    别名留在死 SKU 上。返回被改指的别名行数（供调用方留痕与度量）。
    """
    sec_ids = [int(x) for x in (secondary_ids or [])]
    if not sec_ids:
        return 0
    return session.query(_VendorSkuAliasRow).filter(
        _VendorSkuAliasRow.sku_id.in_(sec_ids)
    ).update({_VendorSkuAliasRow.sku_id: int(primary_id)}, synchronize_session=False)


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

        # P2-T2 钩子 C：别名改指到主 SKU（必须在停用副 SKU 之前/同时完成，
        # 否则别名指向 active=0 的副 SKU，读取侧判为死链直接不命中）
        alias_moved = _repoint_vendor_sku_alias(s, sec_ids, primary.id)

        s.commit()
        return {
            "primary_sku_id": primary.id,
            "primary_name": primary.name,
            "merged_sku_ids": sec_ids,
            "merged_names": secondary_names,
            "new_stock": primary.current_stock,
            "alias_repointed": alias_moved,
        }, None
    finally:
        s.close()


# 归一通道单次最多考察的候选数。canonical_name 没有独立索引（唯一键是 raw_name），
# 同一核心词的写法数量理论上无上限；而这是热路径（每张单据每条明细各一次匹配），
# 故设上界。候选按 hit_count 降序取，被截掉的是"用得最少"的那些先验。
_ALIAS_CANONICAL_CANDIDATE_LIMIT = 20


def _alias_row_usable_sku(session, row, tenant_id):
    """判定一条别名行当前是否可用：可用返回其 SKU 行，不可用返回 None。

    不可用的三种形态（必须跳过，且**绝不能返回给调用方** —— 返回死链等于把已合并/
    已过期的先验当成现役事实）：
    1. `status != active`（人工置停，或更新语义里被取代的那条）；
    2. `valid_to` 非空且已过期（TTL 到点不再召回）；
    3. 指向不存在 / 跨租户 / 已停用（active=0，典型是合并后留下的副 SKU）的 SKU。

    why 抽成函数：精确写法通道与归一通道要做**同一套**判定。P2a 审核 M5 的根因正是
    判定只做在第一条通道上就早退 —— 同一条判据散成两处实现，就是本仓高发的
    "同一根因多份实现只改一份"。
    """
    if row is None:
        return None
    if (row.status or "active") != "active":
        return None
    vt = _parse_iso_dt(row.valid_to or "")
    if vt is not None and datetime.now() > vt:
        return None
    sku = session.get(_SkuRow, int(row.sku_id)) if row.sku_id else None
    if sku is None or not _tenant_ok(sku, tenant_id) or (sku.active or 0) != 1:
        return None
    return sku


def find_vendor_sku_alias(raw_name, tenant_id=None, supplier_id=None, canonical_name=None):
    """P2-T2 钩子 A：按（租户, 供应商）查历史别名 → 返回命中的 SKU 行（未命中 None）。

    语义：本表记录「该供应商用这个写法指这个 SKU」。同一个写法（港式手写单里的
    缩写，如「黑牛」）在不同供应商下含义不同 —— 这正是别名必须带 supplier_id
    维度的理由，故 **supplier_id 为空时直接返回 None**（无从判定供应商维度时回落
    既有全局匹配：不猜、也不返回全量，本仓已出现 3 次"找不到就退回全量"的越权形态）。

    匹配顺序（固定，先精确后归一，**逐条判可用性、不早退**）：
    1. `raw_name` 精确相等（与解析时 item["name"] 同口径，最高置信）；
    2. `canonical_name` 相等（核心词归一，覆盖繁简/规格/包装前缀差异）。
    候选只要**不可用就换下一条**：精确那条若是 retired / 已过期 / 指向被合并的死 SKU，
    早退会把整条别名能力当场作废 —— 同 canonical 明明还有 active 候选，本该命中却
    回落到全局匹配（P2a 审核 M5：早退压低了召回）。两条通道都无可用的才返回 None。
    命中即累加 `hit_count`（度量"这条别名是否真在被用"，也是后续清理/排序的依据）；
    被跳过的候选**不累加** —— 计数必须反映"真的被用上了"。

    返回 SKU 行而不是别名行：调用方（`receipt_utils._match_sku`）还要读
    `sku.last_unit_price` 做价格异动比对，返回别名行会多一次 `get_sku` 往返。

    不命中 / 别名指向已停用 SKU（被合并掉的死链）/ 过期 / 表不存在等任何异常，
    一律返回 None 并留 warning：别名是**增量先验**，它故障时调用方必须能安然回落
    既有全局匹配 —— 不能让"别名表出问题"变成"识别结果没有 SKU"。这里刻意不让
    异常上抛，就是为了不重演本仓"异常被静默吞掉导致功能静默失效"之外的另一种
    形态（主链路被辅助能力拖垮）；warning 保证它有观测性。
    """
    raw = str(raw_name or "").strip()
    if not raw or supplier_id is None or not str(supplier_id).strip():
        return None
    canon = str(canonical_name or "").strip()
    s = get_session()
    try:
        q = scoped(s.query(_VendorSkuAliasRow), _VendorSkuAliasRow, tenant_id).filter(
            _VendorSkuAliasRow.supplier_id == int(supplier_id))
        # 通道一：精确写法。唯一键 (tenant_id, supplier_id, raw_name) 保证同一写法
        # 只有一行，取一条即可。
        exact = q.filter(_VendorSkuAliasRow.raw_name == raw).order_by(
            _VendorSkuAliasRow.id).first()
        hit_row, sku = exact, _alias_row_usable_sku(s, exact, tenant_id)
        # 通道二：核心词归一。**精确通道没给出可用命中就继续找**（不早退）。
        # 这里不再用 `canon != raw` 短路：canonical 与 raw 相等是常见形态（干净写法
        # 归一后不变），短路会让"精确行 retired、同 canonical 另有 active 候选"这种
        # 情形仍然召回丢失 —— 短路只是省一次查询，代价是把上面刚修好的洞留一半。
        # 候选多条（同一核心词的多种写法）→ 按 hit_count 降序、id 升序逐个判可用性：
        # 用得最多的先验优先，同频次取最早登记的那条（确定性）。
        if sku is None and canon:
            exact_id = exact.id if exact is not None else None
            for cand in q.filter(_VendorSkuAliasRow.canonical_name == canon).order_by(
                    _VendorSkuAliasRow.hit_count.desc(),
                    _VendorSkuAliasRow.id).limit(
                    _ALIAS_CANONICAL_CANDIDATE_LIMIT).all():
                if cand.id == exact_id:
                    continue    # 已在精确通道判过不可用，不重复判定
                got = _alias_row_usable_sku(s, cand, tenant_id)
                if got is not None:
                    hit_row, sku = cand, got
                    break
        if sku is None:
            # 两条通道都没有可用候选 → None（调用方回落全局匹配）。已停用/过期/
            # 指向死 SKU 的候选一律不返回：**安全性优先于召回**。
            return None
        hit_row.hit_count = (hit_row.hit_count or 0) + 1
        hit_row.updated_at = now_iso()
        s.commit()
        return sku
    except Exception as e:
        logger.warning("[sku_alias] find_vendor_sku_alias failed (supplier=%s raw=%s): %s",
                       supplier_id, raw, e)
        return None
    finally:
        s.close()


def upsert_vendor_sku_alias(supplier_id, sku_id, raw_name, canonical_name="", unit="",
                            tenant_id="default", source_ref="", protect_live_target=False):
    """P2-T2 钩子 B/C 的写入实现：登记 / 刷新「供应商 × 写法 → SKU」别名。

    幂等键 = (tenant_id, supplier_id, raw_name)：一个写法在同一供应商下只能指向一个
    SKU（读取侧靠它才能确定命中哪一条）；`unit` 只作记录、不进唯一键，避免同一写法
    换单位后出现两条都参与匹配、命中结果不确定。

    已存在时：SKU 不同 → 改指（重新匹配/合并场景）；相同 → 只刷新 canonical/unit，
    `hit_count` 保留（它是"这条别名被用了几次"的累计度量，不能被登记动作清零）。

    `protect_live_target=True` 时的额外保护（P2fix-F1）：既有别名指向一个**仍然存活**
    （存在、同租户、active=1）且**非本次新建**的 SKU 时，不把指向改到本次传入的
    sku_id。why：调用方（`services/inventory.apply_receipt_to_inventory`）在"没匹配上
    就自动新建 SKU"的分支里传 True —— 若入账路径自己刚建了一个重复 SKU 又想覆盖既有
    先验，这条别名就会被翻转指向重复项，下次解析反而落到重复 SKU（正是 P2-T2 要消灭
    的 SKU 爆炸，且是"后跑的实现推翻前一套成果"的自我推翻形态）。既有目标已死
    （被合并/跨租户/删除）时不保护 —— 那种情况下改指才是修复。默认 False 保持既有
    调用方（回填脚本、合并联动、测试）的改指语义不变。

    返回 `(alias_id, reason)`：
    - `(id, None)` 成功；
    - `(id, "TARGET_PROTECTED")` 命中上述保护：行未改指（既有指向保留），id 仍是该行
      —— 返回原因而非静默返回成功，调用方才能把"入账与别名先验分歧"记进告警；
    - `(None, "NO_SUPPLIER")` supplier_id 为空 —— 无供应商维度，登记了也没人消费
      （读取侧要求 supplier_id），故直接跳过；调用方按跳过计数并汇总留痕；
    - `(None, "INVALID_SKU")` sku_id 非法或在库中不存在/跨租户/已停用 —— 防"别名
      指向死 SKU"（这正是 P2-T2 要消灭的形态）；
    - `(None, "INVALID_RAW_NAME")` 写法为空；
    - `(None, "CONFLICT")` 唯一键竞争（并发插入），本次未写入。
    四种失败都返回原因而非静默 pass —— 本仓高发的"异常被静默吞掉导致功能静默失效"
    在这里表现为别名一条都登记不上却毫无痕迹。
    """
    raw = str(raw_name or "").strip()
    if not raw:
        return None, "INVALID_RAW_NAME"
    if supplier_id is None or not str(supplier_id).strip():
        return None, "NO_SUPPLIER"
    try:
        supplier_id_int, sku_id_int = int(supplier_id), int(sku_id)
    except (TypeError, ValueError):
        return None, "INVALID_SKU"
    tid = str(tenant_id or "default")
    s = get_session()
    try:
        sku = s.get(_SkuRow, sku_id_int)
        if sku is None or not _tenant_ok(sku, tid) or (sku.active or 0) != 1:
            return None, "INVALID_SKU"
        row = scoped(s.query(_VendorSkuAliasRow), _VendorSkuAliasRow, tid).filter(
            _VendorSkuAliasRow.supplier_id == supplier_id_int,
            _VendorSkuAliasRow.raw_name == raw).first()
        if row is not None:
            if protect_live_target and int(row.sku_id or 0) != sku_id_int:
                old = s.get(_SkuRow, int(row.sku_id)) if row.sku_id else None
                if old is not None and _tenant_ok(old, tid) and (old.active or 0) == 1:
                    # 既有指向仍存活 → 保留它（不改指、不覆盖 canonical/unit）
                    return int(row.id), "TARGET_PROTECTED"
            row.sku_id = sku_id_int
            if canonical_name:
                row.canonical_name = str(canonical_name)
            if unit:
                row.unit = str(unit)
            row.status = "active"
            row.updated_at = now_iso()
            s.commit()
            return int(row.id), None
        row = _VendorSkuAliasRow(
            tenant_id=tid, supplier_id=supplier_id_int, sku_id=sku_id_int,
            raw_name=raw, canonical_name=str(canonical_name or ""),
            unit=str(unit or ""), hit_count=0, valid_from=now_iso(),
            valid_to="", status="active", source_ref=str(source_ref or ""),
            updated_at=now_iso())
        s.add(row)
        s.commit()
        s.refresh(row)
        return int(row.id), None
    except Exception as e:
        s.rollback()
        try:
            from sqlalchemy.exc import IntegrityError as _IntegrityError
            _is_conflict = isinstance(e, _IntegrityError)
        except Exception:
            _is_conflict = False
        if _is_conflict:
            logger.warning("[sku_alias] upsert conflict (supplier=%s raw=%s)", supplier_id, raw)
            return None, "CONFLICT"
        logger.warning("[sku_alias] upsert_vendor_sku_alias failed (supplier=%s raw=%s): %s",
                       supplier_id, raw, e)
        return None, "ERROR"
    finally:
        s.close()


def delete_vendor_sku_alias_for_supplier(supplier_id, tenant_id=None):
    """FP-2a：物理删除某供应商的全部 SKU 别名行（供应商主档删除的级联之一）。

    why 物理删而不是置 retired：别名行的 valid_to/status 是**治理态**（这条先验是否
    仍在召回），不是 erasure 对象 —— 供应商档案本体已物理删除后，残留的别名行只会
    指向不存在的 supplier_id（死先验）。返回删除行数（供应商不存在/无别名 = 0）。
    """
    if _VendorSkuAliasRow is None:
        return 0
    try:
        sid = int(supplier_id)
    except (TypeError, ValueError):
        return 0
    s = get_session()
    try:
        q = scoped(s.query(_VendorSkuAliasRow), _VendorSkuAliasRow,
                   tenant_id).filter(_VendorSkuAliasRow.supplier_id == sid)
        n = q.delete(synchronize_session=False)
        s.commit()
        return int(n)
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


def find_duplicate_sku_groups(tenant_id=None):
    """只读巡检：列出会被「流水号变体合并」影响的重复货品分组，不改任何数据。"""
    return deduplicate_skus_by_canonical(tenant_id=tenant_id, dry_run=True)


def deduplicate_skus_by_canonical(tenant_id=None, dry_run=False):
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
            if dry_run:
                reports.append({
                    "canonical": canon, "primary_id": primary.id,
                    "primary_name": primary.name, "merged_ids": sec_ids,
                    "merged_names": sec_names,
                })
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
            # P2-T2 钩子 C（第二个合并入口）：别名改指到主 SKU。这条自动去重由
            # 启动自愈每次调用（app/main.py），漏了它会让别名在每次启动后悄悄
            # 指向被停用的旧 SKU。
            alias_moved = _repoint_vendor_sku_alias(s, sec_ids, primary.id)
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
                "alias_repointed": alias_moved,
            })
        if dry_run:
            return reports
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
                        # P2-T2：物理删除前级联清掉指向它的别名（同 delete_sku）
                        s.query(_VendorSkuAliasRow).filter(
                            _VendorSkuAliasRow.sku_id == r.id).delete(synchronize_session=False)
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


def snapshot_dish_recipe(session, dish, changed_by="", summary=""):
    """写入 BOM 版本快照（含菜品字段 + 完整配方）。

    调用方负责 commit（与菜品/配方变更同一事务）；同 (dish, version) 已存在
    则跳过，保证重试幂等、历史不可改写。
    返回新增的快照行；跳过时返回已有行。
    """
    existing = (
        session.query(_DishRecipeVersionRow)
        .filter(
            _DishRecipeVersionRow.dish_id == int(dish.id),
            _DishRecipeVersionRow.version == int(dish.version or 1),
        )
        .first()
    )
    if existing is not None:
        return existing

    ing_rows = (
        session.query(_DishIngredientRow)
        .filter(_DishIngredientRow.dish_id == int(dish.id))
        .order_by(_DishIngredientRow.id.asc())
        .all()
    )
    ingredients = []
    for ing in ing_rows:
        ctype = getattr(ing, "component_type", None) or "sku"
        cid = getattr(ing, "component_id", None)
        if cid is None:
            cid = getattr(ing, "sku_id", None)
        ingredients.append({
            "component_type": ctype,
            "component_id": cid,
            "sku_id": getattr(ing, "sku_id", None),
            "consumption_qty": ing.consumption_qty,
            "unit": ing.unit,
            "yield_rate": getattr(ing, "yield_rate", 1.0),
            "notes": ing.notes or "",
        })

    dish_cat = (dish.category or "").strip()
    if not dish_cat or dish_cat == "其他":
        dish_cat = "默认"

    snapshot = {
        "dish": {
            "id": dish.id,
            "name": dish.name,
            "category": dish_cat,
            "price": dish.price,
            "description": dish.description or "",
            "status": dish.status or "active",
            "tenant_id": getattr(dish, "tenant_id", None) or "default",
            "version": int(dish.version or 1),
        },
        "ingredients": ingredients,
        "captured_at": now_iso(),
    }
    row = _DishRecipeVersionRow(
        dish_id=int(dish.id),
        version=int(dish.version or 1),
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        change_summary=(summary or "").strip(),
        changed_by=(changed_by or "").strip(),
        changed_at=now_iso(),
        tenant_id=getattr(dish, "tenant_id", None) or "default",
    )
    session.add(row)
    session.flush()
    return row


def backfill_dish_recipe_snapshots():
    """存量餐品版本快照回填：为「零快照」餐品补写当前版本的初始快照。

    幂等：仅处理 `dish_recipe_versions` 中尚无任何行的餐品，二次调用零动作；
    单条异常仅告警不抛出（不阻断调用方）。返回实际补写的餐品数。
    """
    s = get_session()
    try:
        covered = {
            row[0] for row in s.query(_DishRecipeVersionRow.dish_id).distinct().all()
            if row[0] is not None
        }
        created = 0
        for dish in s.query(_DishRow).all():
            if dish.id in covered:
                continue
            snapshot_dish_recipe(
                s, dish,
                changed_by="system:backfill",
                summary="存量餐品初始版本回填",
            )
            created += 1
        if created:
            s.commit()
        return created
    except Exception as e:
        s.rollback()
        logger.warning("[migrate] dish recipe snapshot backfill skipped: %s", e)
        return 0
    finally:
        s.close()


def list_dish_recipe_versions(dish_id, tenant_id=None):
    """列出某餐品的版本快照元数据（按版本倒序），不含快照正文。"""
    s = get_session()
    try:
        rows = scoped(
            s.query(_DishRecipeVersionRow), _DishRecipeVersionRow, tenant_id
        ).filter(_DishRecipeVersionRow.dish_id == int(dish_id)) \
         .order_by(_DishRecipeVersionRow.version.desc()).all()
        return [
            {
                "id": r.id,
                "dish_id": r.dish_id,
                "version": r.version,
                "change_summary": r.change_summary or "",
                "changed_by": r.changed_by or "",
                "changed_at": r.changed_at or "",
                "tenant_id": r.tenant_id or "default",
            }
            for r in rows
        ]
    finally:
        s.close()


def get_dish_recipe_version(dish_id, version, tenant_id=None):
    """取某餐品指定版本的完整快照（dict）；不存在返回 None。"""
    s = get_session()
    try:
        row = scoped(
            s.query(_DishRecipeVersionRow), _DishRecipeVersionRow, tenant_id
        ).filter(
            _DishRecipeVersionRow.dish_id == int(dish_id),
            _DishRecipeVersionRow.version == int(version),
        ).first()
        if row is None:
            return None
        try:
            snapshot = json.loads(row.snapshot_json or "{}")
        except Exception:
            snapshot = {}
        return {
            "id": row.id,
            "dish_id": row.dish_id,
            "version": row.version,
            "change_summary": row.change_summary or "",
            "changed_by": row.changed_by or "",
            "changed_at": row.changed_at or "",
            "tenant_id": row.tenant_id or "default",
            "snapshot": snapshot,
        }
    finally:
        s.close()


def _drop_reversed_in_rows(s, rows, tenant_id=None):
    """剔除已被冲销的单据产生的入库流水（价格历史与进价区间不应再计入）。"""
    rids = {r.receipt_id for r in rows if r.receipt_id is not None}
    if not rids:
        return rows
    gone = {x[0] for x in scoped(s.query(_StockLogRow.receipt_id), _StockLogRow, tenant_id).filter(
        _StockLogRow.kind == "reversal", _StockLogRow.receipt_id.in_(rids)).all()}
    return [r for r in rows if r.receipt_id not in gone] if gone else rows


def price_history(sku_id, tenant_id=None):
    """SKU 价格历史：从库存流水（kind=in）推导单价 = amount/qty；已冲销单据不计入。"""
    s = get_session()
    try:
        rows = scoped(s.query(_StockLogRow), _StockLogRow, tenant_id).filter(
            _StockLogRow.sku_id == int(sku_id),
            _StockLogRow.kind == "in",
        ).order_by(_StockLogRow.date.asc()).all()
        rows = _drop_reversed_in_rows(s, rows, tenant_id)
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


def vendor_price_history(sku_id, vendor, tenant_id=None):
    """某 SKU 在某供应商处的历史进货单价（供应商 × SKU 报价区间，供复核侧对照）。

    数据源严格为库存流水（inventory_log，kind == 'in'），单价 unit_price = amount / qty；
    仅保留单价 > 0 的行（qty <= 0 或 amount <= 0 的行剔除，含赠品/冲销/异常行）。
    vendor 先 strip 再与原字符串精确匹配（不做大小写/模糊归一）。
    返回 [(date, unit_price, qty, receipt_id)]，按 date 升序；无历史返回 []。
    注意：**不**回退读 sku.last_unit_price —— 该列只存「最新一笔」，无法构成区间。
    """
    name = str(vendor or "").strip()
    if not name:
        return []
    s = get_session()
    try:
        rows = scoped(s.query(_StockLogRow), _StockLogRow, tenant_id).filter(
            _StockLogRow.sku_id == int(sku_id),
            _StockLogRow.kind == "in",
            _StockLogRow.vendor == name,
        ).order_by(_StockLogRow.date.asc()).all()
        rows = _drop_reversed_in_rows(s, rows, tenant_id)
        out = []
        for r in rows:
            qty = float(r.qty or 0.0)
            amount = float(r.amount or 0.0)
            if qty <= 0 or amount <= 0:
                continue
            out.append((r.date, amount / qty, qty, r.receipt_id))
        return out
    finally:
        s.close()


# -------------------------------------------------------------
# WS5：价格异动处置留痕（ignore / adopt 的持久化状态）
# -------------------------------------------------------------
def _ack_tenant_key(tenant_id):
    """处置行租户键归一：空值一律落 'default'（与 _event_tenant_id 同口径）。"""
    return _event_tenant_id(tenant_id, None)


def _ack_row_dict(row):
    return {
        "id": row.id,
        "tenant_id": row.tenant_id or "default",
        "sku_id": row.sku_id,
        "action": row.action or "",
        "threshold_pct": row.threshold_pct,
        "snapshot_vs_pct": row.snapshot_vs_pct,
        "created_at": row.created_at or "",
    }


def upsert_price_anomaly_ack(sku_id, action, threshold_pct, snapshot_vs_pct,
                             tenant_id=None):
    """写入/更新某 SKU 的价格异动处置（同一 sku 重复操作是更新，不堆叠）。

    threshold_pct / snapshot_vs_pct 为**处置当时**的阈值与涨幅快照（回溯用）。
    action 的合法性由调用方（API 层）校验，本层只落库。
    返回写入后的行 dict；异常向上抛（由调用方决定降级策略）。
    """
    t_id = _ack_tenant_key(tenant_id)
    s = get_session()
    try:
        rows = s.query(_PriceAnomalyAckRow).filter(
            _PriceAnomalyAckRow.sku_id == int(sku_id),
            _PriceAnomalyAckRow.tenant_id == t_id,
        ).order_by(_PriceAnomalyAckRow.id.asc()).all()
        now = now_iso()
        if rows:
            row = rows[0]
            row.action = str(action)
            row.threshold_pct = float(threshold_pct or 0.0)
            row.snapshot_vs_pct = float(snapshot_vs_pct or 0.0)
            row.created_at = now
            # 历史重复行（唯一索引生效前写入的）一并清理，维持「一 sku 一行」。
            for dup in rows[1:]:
                s.delete(dup)
        else:
            row = _PriceAnomalyAckRow(
                tenant_id=t_id,
                sku_id=int(sku_id),
                action=str(action),
                threshold_pct=float(threshold_pct or 0.0),
                snapshot_vs_pct=float(snapshot_vs_pct or 0.0),
                created_at=now,
            )
            s.add(row)
        s.commit()
        return _ack_row_dict(row)
    finally:
        s.close()


def list_price_anomaly_acks(tenant_id=None):
    """本租户全部价格异动处置，返回 {sku_id: {...}}（供前端判定忽略/采纳）。"""
    t_id = _ack_tenant_key(tenant_id)
    s = get_session()
    try:
        rows = s.query(_PriceAnomalyAckRow).filter(
            _PriceAnomalyAckRow.tenant_id == t_id
        ).order_by(_PriceAnomalyAckRow.id.asc()).all()
        out = {}
        for r in rows:
            out[int(r.sku_id)] = _ack_row_dict(r)
        return out
    finally:
        s.close()


def get_price_anomaly_ack(sku_id, tenant_id=None):
    """取单个 SKU 的处置行（无则 None），供 API 回显与撤销。"""
    t_id = _ack_tenant_key(tenant_id)
    s = get_session()
    try:
        row = s.query(_PriceAnomalyAckRow).filter(
            _PriceAnomalyAckRow.sku_id == int(sku_id),
            _PriceAnomalyAckRow.tenant_id == t_id,
        ).order_by(_PriceAnomalyAckRow.id.asc()).first()
        return _ack_row_dict(row) if row is not None else None
    finally:
        s.close()


def clear_price_anomaly_ack(sku_id, tenant_id=None):
    """撤销某 SKU 的处置（DELETE 端点用）。返回删除的行数（幂等：无行返回 0）。"""
    t_id = _ack_tenant_key(tenant_id)
    s = get_session()
    try:
        n = s.query(_PriceAnomalyAckRow).filter(
            _PriceAnomalyAckRow.sku_id == int(sku_id),
            _PriceAnomalyAckRow.tenant_id == t_id,
        ).delete(synchronize_session=False)
        s.commit()
        return int(n or 0)
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


def resolve_supplier_id(vendor_name, tenant_id=None):
    """P2-T1：把记忆的 vendor 自由文本解析成供应商档案 id（supplier_id 锚点）。

    口径（两步，顺序固定，与 D17 的 receipts.supplier_id 回填同款）：
    1. `find_supplier_by_name` 精确名匹配；
    2. 未命中则 `find_supplier_by_canonical_name` 归一 key 匹配（去英文括号注 + 繁转简 +
       常见商号别名）；
    3. 仍拿不准 → **返回 None，绝不新建供应商**。why：记忆写入侧只需一个"归属标签"，
       为它凭空建档案会把错名（买方名、测试夹具名、手写噪声）洗成正式供应商，并污染
       供应商列表与按供应商的统计（live 已有「七月餐室」这类买方名被自动建档）。

    本函数是**唯一的名字→id 解析实现**：写入侧（upsert_vendor_memory）、读取侧
    （rag.retrieve_context）与回填脚本三方共用，避免"同一根因多份实现只改一份"。

    租户：tenant_id 非空时只在同租户内匹配（scoped），防跨租户错挂；None 表示不过滤
    （仅供内部脚本/测试的全量路径，生产两侧都传真实租户）。
    异常一律吞成 None 并留 warning：解析失败不能让记忆写入/检索链路抛错。
    """
    name = str(vendor_name or "").strip()
    if not name:
        return None
    try:
        row = find_supplier_by_name(name, tenant_id=tenant_id)
        if row is not None:
            return int(row.id)
        row = find_supplier_by_canonical_name(name, tenant_id=tenant_id)
        if row is not None:
            return int(row.id)
    except Exception as e:
        logger.warning("[memory] resolve_supplier_id failed (%s): %s", name, e)
        return None
    return None


def resolve_supplier_id_from_memory(vendor_name, tenant_id=None):
    """L0 记忆先验锚点解析：vendor_memory 中与该名字**精确相等**的 active 行所携带的
    supplier_id 恰好唯一时返回该 id；零候选 / 多于一个不同 id 一律返回 None（不猜）。

    why：单据未锚定（receipts.supplier_id 为空、按名解析也落空）时，同一 vendor 名
    的历史记忆行可能携带"上次该名字被锚定到哪个档案"的先验。它只配当**最后一档回退**
    ——历史锚点可能过时（供应商改档/合并后旧记忆行仍指旧 id），故多候选绝不取第一个。

    口径（与 resolve_supplier_id 同一条纪律的两处落实）：
    - **只读**：绝不新建档案、绝不回写任何表、绝不触向量库；
    - 查询不复用 `list_vendor_memory`：它的名字通道含 `_vendor_related` 模糊回退，
      会把"名字相关的别的供应商"放行进来，而别名表读侧是严格 supplier_id 相等，
      两边口径必须同严 —— 这里只认 **vendor 精确相等**；
    - 租户门：tenant_id 为空直接返回 None（宁可不锚定）——scoped(..., None) 不过滤
      租户，会把单据锚到别的租户的同名记忆行/档案上；
    - 唯一候选还要过 `get_supplier(sid, tenant_id=...)` 归属校验（跨租户 id 拒绝
      锚定，同 `_memory_anchor_for_write` 口径）；
    - 任何异常 → 返回 None + warning，绝不向上抛（锚点解析失败不得阻断解析/入账主链路）。
    """
    name = str(vendor_name or "").strip()
    if not name:
        return None
    tid = str(tenant_id or "").strip()
    if not tid:
        # 租户门：scoped(..., None) 是全量扫描，"精确名匹配"会跨租户命中 —— 不锚定。
        return None
    try:
        s = get_session()
        try:
            rows = scoped(s.query(_VendorMemoryRow), _VendorMemoryRow, tid).filter(
                _VendorMemoryRow.status == "active",
                _VendorMemoryRow.vendor == name,
                _VendorMemoryRow.supplier_id.isnot(None),
            ).all()
        finally:
            s.close()
        ids = set()
        for r in rows:
            try:
                ids.add(int(r.supplier_id))
            except (TypeError, ValueError):
                logger.warning(
                    "[supplier_anchor] 记忆先验候选 supplier_id 非法（vendor=%r "
                    "tenant=%s memory_id=%s value=%r），丢弃", name, tid,
                    getattr(r, "memory_id", None), getattr(r, "supplier_id", None))
        if len(ids) != 1:
            logger.info(
                "[supplier_anchor] memory prior 候选 %d 个（ids=%s），不锚定 "
                "vendor=%r tenant=%s", len(ids), sorted(ids), name, tid)
            return None
        sid = ids.pop()
        if get_supplier(sid, tenant_id=tid) is None:
            logger.warning(
                "[supplier_anchor] memory prior supplier_id=%s 不属于租户 %s"
                "（vendor=%r），拒绝锚定", sid, tid, name)
            return None
        return sid
    except Exception as e:
        logger.warning("[supplier_anchor] memory prior 解析失败（vendor=%r tenant=%s）: %s",
                       name, tid, e)
        return None


def _memory_anchor_for_write(vendor, tenant_id, supplier_id=None):
    """写入侧 supplier_id 锚点解析（唯一实现，两条写入链路共用）。

    优先级（顺序固定）：
    1. 调用方显式给出的 `supplier_id`（权威锚点，如 `receipts.supplier_id`）；
    2. 未给 / 给了但不可用 → `resolve_supplier_id(vendor, tenant)` 按名解析；
    3. 仍拿不到 → 返回 None（列写 NULL），**绝不编造、绝不新建档案**。

    why 显式锚点优先于名字解析：调用方给的 id 来自单据行/档案主键，是真正的结构关联；
    名字解析是"猜"，在重名变体（繁简/简称/中文字段后缀）与"同一写法这次指向另一个
    档案"时会把记忆挂到错的供应商上。J3 之前写入侧**只有**名字解析这条路 —— 这正是
    "锚点写了却落不到生产写入路径"的根因。

    why 显式 id 还要校验租户归属：跨租户的 id 会把记忆挂到别的租户档案上，而读侧
    （list_vendor_memory）同时按 tenant + anchor 过滤 → 该行将**永远**召不回且零日志
    （本仓③类缺陷形态）。校验失败只告警并回落名字口径，不抛错（写入不得因锚点故障失败）。

    why 解析不出必须 logger.warning（与读侧口径刻意不同）：读侧"解析返回 None"只是
    这一次没命中，属正常；写侧未命中意味着这一行**永久**失去锚点 —— 除重写或回填外
    无从修复，只能靠这条日志被发现（I2「降级可接受、静默不行」同一条纪律）。
    """
    vid = str(vendor or "").strip()
    tid = str(tenant_id or "default").strip() or "default"
    if supplier_id is not None:
        sid = None
        try:
            sid = int(supplier_id)
        except (TypeError, ValueError):
            logger.warning(
                "[memory] supplier_id 非法（vendor=%s tenant=%s supplier_id=%r），"
                "改按 vendor 名解析", vid, tid, supplier_id)
        if sid is not None:
            try:
                if get_supplier(sid, tenant_id=tid) is not None:
                    return sid
                logger.warning(
                    "[memory] supplier_id=%s 不属于租户 %s（vendor=%s），"
                    "拒绝锚定，改按 vendor 名解析", sid, tid, vid)
            except Exception as e:
                logger.warning(
                    "[memory] supplier_id 归属校验失败（vendor=%s tenant=%s "
                    "supplier_id=%s）：%s，改按 vendor 名解析", vid, tid, sid, e)
    if not vid:
        return None
    sid = resolve_supplier_id(vid, tid)
    if sid is None:
        logger.warning(
            "[memory] supplier_id 解析不出（vendor=%s tenant=%s），本行写 NULL，"
            "读侧只能回退按 vendor 名匹配", vid, tid)
    return sid


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


def _receipts_for_supplier(s, supplier, tenant_id=None):
    """D17：归属某供应商的单据集合（关联口径：优先 supplier_id，回落归一 key）。

    - 已带 supplier_id 的行：仅当等于该供应商 id 时计入（不再按名字猜）；
    - 无 supplier_id 的历史行：按 canonical_supplier_key 归一匹配该供应商名计入；
    - 供应商名为空（归一 key 为空）时不回落匹配任何历史行。
    调用方负责 tenant_id 口径（本函数在传入的 query 上已做租户过滤）。
    """
    from app.services.supplier_normalizer import canonical_supplier_key
    key = canonical_supplier_key(supplier.name or "")
    out = []
    for r in scoped(s.query(_ReceiptRow), _ReceiptRow, tenant_id).all():
        if r.supplier_id is not None:
            if r.supplier_id == supplier.id:
                out.append(r)
        elif key and canonical_supplier_key(r.supplier_name or "") == key:
            out.append(r)
    return out


def list_receipts_for_supplier(supplier_id, tenant_id=None):
    """D17：某供应商名下单据（含无 id 历史行的归一 key 回落）。

    供应商不存在或跨租户 -> 空列表（不返回他人数据）。
    """
    s = get_session()
    try:
        sup = s.get(_SupplierRow, int(supplier_id))
        if sup is None or not _tenant_ok(sup, tenant_id):
            return []
        return _receipts_for_supplier(s, sup, tenant_id)
    finally:
        s.close()


def count_receipts_for_supplier(supplier_id, tenant_id=None):
    """FP-2a：某供应商名下**引用单据**计数（只认 receipts.supplier_id 精确关联）。

    为什么只按 supplier_id、不做归一 key 回落（与 list_receipts_for_supplier 口径
    不同）：本函数服务的是删除守卫 —— 回落匹配会随 supplier_name 改写而漂移，而
    外键引用（receipts.supplier_id）是结构事实；守卫拦的是"还有单据锚在这份档案上"，
    名字撞车不该拦、也不该放（防「删档后恢复软删单」出现死链按的是精确 id）。
    **含软删行**（不看 deleted_at）：软删单可经回收站恢复，恢复后 supplier_id 仍在，
    删档会让它变成死链 —— 与 memory_consistency 的孤儿口径一致（表内行存在即算引用）。
    不校验供应商本身存在/跨租户：404 判定由调用方先经 get_supplier 完成，本函数只计数。
    """
    s = get_session()
    try:
        q = scoped(s.query(_ReceiptRow), _ReceiptRow, tenant_id).filter(
            _ReceiptRow.supplier_id == int(supplier_id))
        return q.count()
    finally:
        s.close()


def delete_supplier_master(supplier_id, tenant_id=None):
    """FP-2a：物理删除供应商主档行（不是 active=0 软删）。

    why 物理删：contact_phone 属个人数据（PDPO s.26「删除」要求数据不再可取回），
    merge 端点的 active=0 是"合并归档"语义（行与手机号都还在），不可复用。
    调用方（DELETE /api/suppliers/{id}）负责先过引用守卫与记忆级联；跨租户/
    不存在返回 False。
    """
    s = get_session()
    try:
        row = s.get(_SupplierRow, int(supplier_id))
        if row is None or not _tenant_ok(row, tenant_id):
            return False
        s.delete(row)
        s.commit()
        return True
    finally:
        s.close()


def supplier_stats(supplier_id, tenant_id=None):
    """供应商聚合：receipt_count / 赊账未付总额。

    D16：先按租户校验供应商归属——不存在或跨租户返回空统计（不再拿别人的行）。
    D17：单据计数优先 supplier_id 精确关联，无 id 的历史行按归一 key 计入。
    """
    s = get_session()
    try:
        _sup = s.get(_SupplierRow, int(supplier_id))
        if _sup is None or not _tenant_ok(_sup, tenant_id):
            return {
                "receipt_count": 0,
                "unpaid_credit_total": 0.0,
                "unpaid_credit_count": 0,
            }
        from app import recon_store
        tid = tenant_id or getattr(_sup, "tenant_id", None) or "default"
        return recon_store.supplier_stat(tid, _sup.id)
    finally:
        s.close()


# -------------------------------------------------------------
# 部门
# -------------------------------------------------------------
def list_departments(tenant_id=None):
    s = get_session()
    try:
        return scoped(s.query(_DeptRow), _DeptRow, tenant_id).all()
    finally:
        s.close()


def create_department(name, tenant_id="default"):
    s = get_session()
    try:
        row = _DeptRow(name=name, active=1,
                       tenant_id=str(tenant_id or "default"))
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def update_department(dept_id, tenant_id=None, **fields):
    """更新部门字段。tenant_id 非空时校验归属，跨租户视为不存在（返回 None）。"""
    s = get_session()
    try:
        row = s.get(_DeptRow, int(dept_id))
        if row is None or not _tenant_ok(row, tenant_id):
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
                   voucher_image_path="", linked_receipt_ids=None,
                   tenant_id="default"):
    s = get_session()
    try:
        tid = str(tenant_id or "default")
        row = _PaymentRow(supplier_id=int(supplier_id), amount=amount, method=method,
                          paid_at=paid_at, notes=notes,
                          voucher_image_path=voucher_image_path,
                          linked_receipt_ids_json=json.dumps(linked_receipt_ids or []),
                          tenant_id=tid)
        s.add(row)
        s.commit()
        s.refresh(row)
        payment_id = row.id
        # 回写收据付款状态（D16：仅限同租户单据，跨租户 id 不得被改写）
        for rid in (linked_receipt_ids or []):
            rc = s.get(_ReceiptRow, int(rid))
            if rc and _tenant_ok(rc, tid):
                rc.paid_at = paid_at
                rc.paid_method = method
                rc.payment_id = payment_id
        s.commit()
        return payment_id
    finally:
        s.close()


def list_payments(tenant_id=None):
    s = get_session()
    try:
        rows = scoped(s.query(_PaymentRow), _PaymentRow, tenant_id).order_by(
            _PaymentRow.paid_at.desc()).all()
        out = []
        for r in rows:
            sup = s.get(_SupplierRow, r.supplier_id) if r.supplier_id else None
            out.append({
                "id": r.id, "supplier_id": r.supplier_id,
                "supplier_name": sup.name if sup else "—",
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
                         memory_id=None, created_at=None,
                         fact_key=None, valid_to=None, supplier_id=None,
                         fact_row=None):
    """每条记忆一行（Gap B1，T3 追加式写入）。

    语义变更：不再把 notes 拼接/覆盖到 (vendor, tenant_id) 单行，而是每次写入
    生成新 memory_id 新行（追加）；同 (vendor, tenant_id) 下 notes+sample 完全
    相同的 active 行已存在时不重复追加（幂等），复用既有 memory_id。
    source_kind: approve | feedback_distilled | manual
    source_ref:  触发来源 receipt_id 列表 JSON（可回溯）
    decay_score: 起始分取 memory_initial_decay_score()（settings 键，缺省 0.7），
                 刻意不硬编码为 1.0 —— 见该函数 docstring（P1-T2 衰减双向）。
    fact_key:    P1-T3 结构化事实键（缺省 None = 无键，保持追加式语义）。
                 **本函数只负责落键，不做 supersede 归并** —— 归并语义在
                 save_memory_fact；经典调用方（approve 回写 / 反馈蒸馏）继续按
                 "同 key 多行并存"运行，行为零变化。
    valid_to:    失效时刻（ISO）；缺省 None → 落空串 = 无显式失效期（不过期）。
                 显式期由 save_memory_fact 按 TTL 计算传入，本函数不自行套 TTL ——
                 否则会顺带改变既有写入路径的过期语义。
    P2-T1 / J3 supplier_id 锚点：本函数在写入时定值，规则见
    `_memory_anchor_for_write`（调用方给的 `supplier_id` 权威优先，未给才按
    `resolve_supplier_id(vendor, tenant)` 名字解析，两者都拿不到就写 NULL）。
    why 放在这里而不是各调用点：写入侧有两条链路（rag.ingest_memory 的人工 approve
    回写、save_memory_fact 的结构化写入），在调用点各自拼一遍就是本仓高发的「同一根因
    多份实现只改一份」；且写入侧的解析口径必须与读侧（rag.retrieve_context）**完全
    一致**，否则"写进去锚的是 A、读的时候按 B 找"，两边都查不到。
    supplier_id: 调用方已知的权威锚点（如 `receipts.supplier_id`）；None = 由本函数
                 按 vendor 名解析。给了值但档案不存在/属别的租户 → 告警后回落解析，
                 绝不编造 id、绝不新建档案。
    fact_row:    P3-T1（K2）结构化事实的**载荷列**行字典（键＝memory_facts.ROW_FIELDS
                 的载荷子集，由 `_memory_fact_row` 从 `MemoryFact.to_row()` 派生）。
                 本函数只逐列 setattr，不做任何字段名映射（映射只有 memory_facts 一份）。
                 身份/治理列（memory_id / tenant_id / supplier_id / source_kind /
                 source_ref / fact_key / valid_to / status / superseded_by）**不从
                 fact_row 覆盖** —— 那些列的取值由本函数的锚点解析、TTL 与 supersede
                 规则决定，被载荷覆盖会绕过 P2-T1 的租户归属校验。
                 缺省 None → 完全不碰这五列（遗留调用方零行为变化）。
    返回 (memory_id, is_new)：is_new=False 表示命中幂等去重。
    """
    vid = str(vendor or "")
    tid = str(tenant_id).strip() if tenant_id else "default"
    tid = tid or "default"
    mid = str(memory_id).strip() if memory_id else uuid.uuid4().hex
    now = now_iso()
    created = str(created_at).strip() if created_at else now
    fkey = str(fact_key).strip() if fact_key else None
    sid = _memory_anchor_for_write(vid, tid, supplier_id)
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
            # version 保持 1 不变：追加式不可变模型下内容变更即新增行，行内内容
            # 不就地改写，故无版本递增语义（此处只刷新 source_ref/updated_at 元数据）。
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
                # P1-T3：内容完全相同的健康行此前可能是在"无键"状态下写入的
                #（键机制上线前落库）。此时去重命中会复用它，若不把键补上，
                # 后续同键新值将找不到它、旧行永远不会被 supersede —— 两个 active
                # 行同时可注入。故在去重命中时就地绑定键（不改变内容与 version）。
                if fkey and not str(dup.fact_key or "").strip():
                    dup.fact_key = fkey
                # P2-T1：同一条纪律 —— 去重命中复用旧行时补锚点。旧行可能是锚点列
                # 上线前写入的（supplier_id IS NULL），只补非空值，不清空已有锚点。
                if sid is not None and dup.supplier_id is None:
                    dup.supplier_id = sid
                # P3-T1：同一条纪律 —— 去重命中复用旧行时补载荷列。旧行可能是载荷列
                # 上线前写入的（fact_kind/trust_level… 为 NULL），只补空值、不覆盖已有
                # 值：一次重复写入不得改写既有事实内容（改写要经过 supersede 语义）。
                _apply_payload_columns(dup, fact_row, only_empty=True)
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
            # 追加式写入：新行 version 恒为 1（内容变更走新增行，不就地改写，
            # 故无 version 递增语义）。
            version=1,
            source_kind=str(source_kind or "manual"),
            source_ref=source_ref or "[]",
            created_at=created,
            updated_at=now,
            hit_count=0,
            last_hit_at="",
            # P1-T2：起始分刻意低于上限 1.0（settings 键 memory_initial_decay_score，
            # 缺省 0.7）。起始分若与上限同为 1.0，命中强化 min(1.0, cur+bonus) 就是
            # 恒等操作 —— 分数只降不升、归档成不可逆终点（旧实现即如此）。
            decay_score=memory_initial_decay_score(),
            status="active",
            fact_key=fkey,
            superseded_by=None,
            valid_to=str(valid_to).strip() if valid_to else "",
            # P2-T1：主体锚点（调用方给的权威 id 优先，否则按名解析，都拿不到为 None，
            # 读侧回退按 vendor 名匹配）
            supplier_id=sid,
        )
        # P3-T1：结构化事实载荷列（缺省 fact_row=None 时不碰，遗留写入路径行保持 NULL）
        _apply_payload_columns(row, fact_row)
        s.add(row)
        s.commit()
    finally:
        s.close()
    # W-4 / M2 容量淘汰：新行已提交后，若同 (vendor, tenant) 的 active 行数超过上限，
    # 归档多余的最低分/最旧行（确定性、可审计，归档而非物理删除）。只在**真的新增了
    # active 行**时调用 —— 幂等去重命中不增行，故不触发（上面 dup 分支已 return）。
    # 淘汰失败不得让写入失败：行已提交，淘汰是可重跑的治理动作，降级留 warning。
    try:
        _evict_vendor_memory_over_capacity(
            vid, tid, keep_memory_id=mid,
            written_by=(fact_row or {}).get("written_by")
            if isinstance(fact_row, dict) else None)
    except Exception as e:
        logger.warning(
            "[db] W-4 容量淘汰失败（新行已提交，淘汰可重跑）"
            "vendor=%s tenant=%s memory_id=%s: %s", vid, tid, mid, e)
    return (mid, True)


def memory_ttl_days():
    """记忆 TTL（settings 实时读取，缺省 0 = 不过期）。

    0 表示"不设过期"（迁移前行为零变化）：TTL 是治理策略，默认打开会让既有记忆
    在某天集体消失，属于「默认值即行为变更」，必须由管理台显式设置。

    过期判据有两条并用（见 _memory_row_expired）：
    1. valid_to 非空且已过 —— 单行的显式失效期（save_memory_fact 按 TTL 写入）；
    2. created_at + TTL 已过 —— 覆盖 TTL 上线前写入、valid_to 为空的存量行。
    只有第 1 条会让"上线前写入的行"永久免于 TTL，故两条都必须有。
    """
    try:
        from app.services import settings_service
        return settings_service.get_int("memory_ttl_days", 0)
    except Exception as e:
        # P20-D9（I2 口径）：回退值 0（不过期）本身是刻意兜底，但**静默**会让
        # "TTL 配置读不出来"与"本来就设了 0"在库里完全同形 —— 治理策略失效而无人可见。
        logger.warning(
            "memory_ttl_days 读取失败，本次按 0（不过期）处理："
            "异常=%s: %s", type(e).__name__, e)
        return 0


def _parse_iso_dt(value):
    """解析记忆时间戳（ISO 文本）为 datetime；空/格式非法返回 None。

    为什么非法值不抛错：本函数只服务"是否过期"的判定，抛错会让整条检索链路被
    except 吞掉（本仓高发的静默失效）。返回 None 由调用方按"无法判定"处理。
    """
    s = str(value or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _memory_row_expired(row, now_dt=None, ttl_days=None):
    """判断一条 vendor_memory 行是否已过期（不参与注入）。

    - ttl_days 由调用方传入（一次读取，避免逐行读 settings）；
    - valid_to / created_at 无法解析时按"未过期"处理：宁可注入一条时间戳脏的
      记忆，也不因为格式问题静默丢掉整批记忆（丢记忆没有日志、无法察觉）。
    """
    now = now_dt or datetime.now()
    vt = _parse_iso_dt(getattr(row, "valid_to", "") or "")
    if vt is not None and now > vt:
        return True
    if ttl_days and ttl_days > 0:
        created = _parse_iso_dt(getattr(row, "created_at", "") or "")
        if created is not None and now > created + timedelta(days=ttl_days):
            return True
    return False


def _filter_memory_rows_for_read(rows, ttl_days=None, now_dt=None):
    """读侧过滤：丢弃过期行 + 同 (tenant, vendor, fact_key) 只留最新一条。

    调用方传入的 rows 必须是 rowid DESC（最新在前）序 —— 首见即最新，去重因此
    只需一个 seen 集合，不必排序。

    为什么去重放在读侧（supersede 已保证同键只有一条 active）：supersede 是两次
    DB 操作（先插新行、再置旧行），中途失败会留下两条同键 active 行；vector/DB
    也可能被外部脚本直写。读侧去重是第二道防线 —— "同 key 只注入最新一条"这条
    承诺不依赖写侧每次都对。fact_key 为空的遗留行不参与去重（保持追加式语义）。
    """
    ttl = memory_ttl_days() if ttl_days is None else ttl_days
    now = now_dt or datetime.now()
    out = []
    seen = set()
    for r in rows:
        if _memory_row_expired(r, now_dt=now, ttl_days=ttl):
            continue
        fkey = str(getattr(r, "fact_key", "") or "").strip()
        if fkey:
            sig = (str(getattr(r, "tenant_id", "") or "default"),
                   str(getattr(r, "vendor", "") or ""), fkey)
            if sig in seen:
                continue
            seen.add(sig)
        out.append(r)
    return out


# -------------------------------------------------------------
# P3-T1（K2）：结构化事实的载荷列
# 事实的"身份 / 治理列"由 upsert_vendor_memory 的既有规则决定，**不允许**被事实载荷
# 覆盖：memory_id（行身份，去重复用）、tenant_id（租户隔离）、supplier_id（P2-T1 锚点
# 解析 + 租户归属校验）、source_kind / source_ref（来源追溯与去重合并）、fact_key
# （supersede 归并的键）、valid_to（P1-T3 TTL 计算，显式期优先）、status /
# superseded_by（P1-T3 状态机）。其余 ROW_FIELDS 成员是纯载荷列，逐列写入。
# why 用"排除法"从 ROW_FIELDS 派生、而不是手抄一份载荷列清单：ROW_FIELDS 是 schema 的
#   唯一定义，手抄第二份就会在它新增字段时静默漏写（本仓高发的「同一根因多份实现只改
#   一份」）。新增 ROW_FIELDS 成员时必须在此判定它属于载荷列还是治理列。
# -------------------------------------------------------------
_MEMORY_FACT_GOVERNANCE_COLUMNS = frozenset({
    "memory_id", "tenant_id", "supplier_id", "source_kind", "source_ref",
    "fact_key", "valid_to", "status", "superseded_by",
})
_MEMORY_FACT_PAYLOAD_COLUMNS = tuple(
    c for c in _MEMORY_FACT_ROW_FIELDS if c not in _MEMORY_FACT_GOVERNANCE_COLUMNS)


def _memory_fact_row(fact):
    """MemoryFact / 行字典 → 行字段字典（键＝memory_facts.ROW_FIELDS）。无 fact 返回 {}。

    映射**只走** `MemoryFact.from_row` / `MemoryFact.to_row`（其内部用
    memory_facts._FACT_TO_ROW），本函数不手写第二份字段映射，也不做字段名转换。
    非 MemoryFact 且非 Mapping 的输入显式抛 TypeError：静默返回 {} 会让"传了事实却
    没落库"变成零痕迹的静默失效（本仓③类缺陷）。
    """
    if fact is None:
        return {}
    from app.services.memory_facts import MemoryFact
    if isinstance(fact, _Mapping):
        fact = MemoryFact.from_row(fact)
    if not isinstance(fact, MemoryFact):
        raise TypeError(
            "save_memory_fact 的 fact 需要 MemoryFact 或行字典，"
            f"实得 {type(fact).__name__}")
    return fact.to_row()


def _apply_payload_columns(row, fact_row, only_empty=False):
    """把事实载荷逐列写入行对象；治理列跳过、未声明的列显式告警。

    why 逐列白名单式写入而不是 `for k, v in fact_row.items(): setattr(row, k, v)`：
    裸 setattr 会让载荷里的 tenant_id / memory_id / status 覆盖掉 upsert 刚算好的治理
    列（绕过 P2-T1 的租户校验与 P1-T3 的状态机），这类"载荷反客为主"的缺陷不会有任何
    报错，只会让隔离与 supersede 静默失效。故 fact_row 里的治理列**一律跳过**（它们是
    memory_facts 声明的行字段，不算异常），只在出现"根本没声明过的列名"时告警 ——
    那说明调用方在拼一个不存在的字段，静默丢弃会让它以为写进去了。

    only_empty=True（去重命中复用旧行时用）：只补空值，不覆盖已有事实内容 —— 一次
    重复写入不得改写既有事实（改写要经 supersede 语义）。
    """
    if not fact_row:
        return
    applied = _MEMORY_FACT_PAYLOAD_COLUMNS
    for col in applied:
        if col not in fact_row:
            continue
        if only_empty and getattr(row, col, None) not in (None, ""):
            continue
        setattr(row, col, fact_row[col])
    known = set(applied) | _MEMORY_FACT_GOVERNANCE_COLUMNS
    unknown = [c for c in fact_row if c not in known]
    if unknown:
        logger.warning(
            "[memory] fact payload 含未声明的行字段，已忽略：%s", sorted(unknown))


def save_memory_fact(tenant_id, vendor, fact_key, notes="", sample="",
                     source_kind="manual", source_ref="[]", fact=None):
    """P1-T3 结构化事实写入：同 (tenant, vendor, fact_key) 冲突时 supersede 旧行。

    supersede 而非覆盖/删除（为什么保留旧行）：记忆的价值一半在"当前值"，一半在
    "这个值被什么取代过"。就地覆盖会丢掉举证链（用户改了什么、什么时候改的），
    硬删则连历史都没有。旧的 active 行改置 status='superseded' 且 superseded_by
    指向新 memory_id，链式可回溯；默认读路径只返 active，故旧值不再注入。

    与向量层对称（R13）：改状态的同时级联删掉这批旧行的向量（复用归档侧同一个实现
    `memory_store.archive_memory_vectors`，本函数在状态提交后调用）。why：一条记忆同时
    落在 vendor_memory 行与 Chroma 向量两处，读侧的 allowed_mids 门虽已挡住非 active 行
    （注入面安全），但向量本身会**永久残留**并让一致性巡检长期报 orphan —— 与 R11 归档
    当初只改 status 不动向量层是同一个不对称（删除有级联 / 归档已有 / supersede 此前没有）。
    只删**本次真发生迁移**的那批 mid（幂等）；向量层故障降级为 warning，状态变更仍成功。

    与 upsert_vendor_memory 的分工：本函数是"带键的结构化写入"，负责 supersede
    与 TTL 计算；行构造仍复用 upsert_vendor_memory（单一实现，避免本仓高发的
    「同一段逻辑两份实现、只改一份」）。

    为什么 fact_key 为空时抛 ValueError：空键会让 supersede 退化成"永远追加"，
    等于把污染源（同 key 多行并存）重新引进来。这种输入是调用方的契约错误，显式
    失败比静默降级安全（本任务不新增"静默吞掉再追加"的路径）。本函数目前由阶段四
    的抽取/整合链路调用，无既有调用方，故新增异常不构成回归。

    TTL：memory_ttl_days > 0 时写 valid_to = 创建时刻 + TTL（显式落库，便于治理
    视图直接看到失效期）；= 0 时 valid_to 落空串（不过期）。

    fact（P3-T1/K2）：`MemoryFact` 或行字典。给了它就按 memory_facts.ROW_FIELDS 把这条
    事实**完整**写入行：载荷列（fact_kind / fact_value / trust_level / valid_from /
    written_by）逐列 setattr，身份与治理列按下列优先级取值 ——
    - 事实的 fact_key / source_kind / source_ref / valid_to 非空时**优先于**同名形参：
      调用方既然交出了事实对象，事实对象就是权威（把这些值再从形参传一遍，等于要求
      每个调用点各拼一份，正是本仓高发的「同一根因多份实现只改一份」）；
    - supplier_id 由事实的锚点传给 `_memory_anchor_for_write`（值不可用则告警并回落名字
      解析，绝不编造 id）—— 事实的 scope 必须真的落库，否则 P3-T6 的读侧按
      (tenant, supplier_id) 收窄时这条事实永远召不回；
    - valid_to 显式期优先于 TTL 计算值（调用方明确给了失效时刻就不该被策略值覆盖）。
    未给 fact（缺省 None）＝遗留契约：不写载荷列（落 NULL），行为与新增形参前完全一致。
    **本函数不做校验**：闸门是 `memory_facts.validate_fact`，调用方须先过闸再写入
    （抽取/整合链路即如此）。

    返回 dict：{"memory_id": 新行 id, "superseded": [被取代的旧 memory_id...],
                "is_new": 本次是否真的新增了行（False = 内容幂等去重命中）}。
    """
    payload = _memory_fact_row(fact)
    vid = str(vendor or "")
    tid = str(tenant_id or "default").strip() or "default"
    key = str(fact_key or payload.get("fact_key") or "").strip()
    if not key:
        raise ValueError("save_memory_fact 需要非空 fact_key（空键会退化为只追加）")
    if not vid:
        raise ValueError("save_memory_fact 需要非空 vendor")
    # 事实对象非空时它是权威来源（见 docstring 的优先级说明）；事实里为空则回落形参
    eff_source_kind = str(payload.get("source_kind") or source_kind)
    eff_source_ref = str(payload.get("source_ref") or source_ref)
    eff_supplier_id = payload.get("supplier_id") or None
    created = now_iso()
    ttl = memory_ttl_days()
    # 显式失效期优先于 TTL：调用方明确给了有效期末尾，就不该被策略值改写成"更晚失效"
    valid_to = str(payload.get("valid_to") or "").strip()
    if not valid_to and ttl and ttl > 0:
        base = _parse_iso_dt(created) or datetime.now()
        valid_to = (base + timedelta(days=ttl)).isoformat(timespec="seconds")
    mid, is_new = upsert_vendor_memory(
        vid, notes, sample, tenant_id=tid,
        source_kind=eff_source_kind, source_ref=eff_source_ref,
        created_at=created, fact_key=key, valid_to=valid_to,
        supplier_id=eff_supplier_id,
        # 载荷列交给 upsert 逐列写入（本函数不碰字段名，避免第二份映射）
        fact_row=payload,
    )
    superseded = []
    s = get_session()
    try:
        rows = s.query(_VendorMemoryRow).filter(
            _VendorMemoryRow.tenant_id == tid,
            _VendorMemoryRow.vendor == vid,
            _VendorMemoryRow.fact_key == key,
            _VendorMemoryRow.status == "active",
            _VendorMemoryRow.memory_id != mid,
        ).all()
        for r in rows:
            r.status = "superseded"
            r.superseded_by = mid
            r.updated_at = now_iso()
            superseded.append(str(r.memory_id))
        if superseded:
            s.commit()
        # R13：supersede 与向量层对称 —— 与 R11 归档同族的第二个不对称。删除
        # （memory_store.delete_memory）与归档（本文件 apply_vendor_memory_override_penalty，
        # R11）都有按 memory_id 的向量级联，而 supersede 此前只改 status、完全不动向量层 →
        # 每取代一次就在向量库留一枚孤儿（巡检 orphan_mids 立刻报，且没有任何写入路径会自愈）。
        # 复用归档侧**同一个实现**（memory_store.archive_memory_vectors → 底层
        # delete_memory_vectors），不另写第二份 collection 操作（本仓①类缺陷的防线）。
        # 只对本次真发生 active -> superseded 迁移的行调用：行一旦被 superseded，上面的查询
        # （status == "active"）就取不到它，故重复写同键不会重复删（幂等）。
        # 级联失败只告警 —— 取代关系此刻已提交，向量残留由巡检可查（与归档侧同口径）。
        if superseded:
            try:
                from app.services import memory_store
                memory_store.archive_memory_vectors(
                    tid, superseded, vendor=vid, reason="superseded")
            except Exception as e:
                logger.warning(
                    "[db] R13 supersede 向量级联失败（取代关系已提交，向量可能残留）"
                    "vendor=%s tenant=%s memory_ids=%s: %s",
                    vid, tid, superseded, e)
        return {"memory_id": mid, "superseded": superseded, "is_new": bool(is_new)}
    finally:
        s.close()


def list_vendor_memory(vendor=None, tenant_id="default", include_archived=False,
                       supplier_id=None):
    """按 (supplier_id 锚点 / vendor 名, tenant_id) 过滤列出记忆（Gap B1 治理视图）。

    - vendor 为空：列出租户下全部记忆（管理视角）
    - vendor 非空：精确匹配优先、模糊/别名相关次之（与 get_vendor_memory 同
      一口径，仅在本租户范围内，绝不跨租户召回）
    - supplier_id 非空（P2-T1 主体锚点，优先于 vendor 名）：先取该档案下的行；
      命中则再用 vendor 名把"同租户内、尚未锚定（supplier_id IS NULL）但名字相关"
      的遗留行并入（回填上线前的存量行不能因为锚点反而读不到）；锚点零命中才回退
      纯名字匹配。
      why 优先锚点：名字相关度门（rag._vendor_related）靠子串/核心词猜，对"大生"与
      "大生隆"这类互相包含的真实商号会互相放行 → 他商记忆泄漏进 prompt；supplier_id
      是档案主键，同一 id 才是同一供应商，重名变体（繁简/简称/后缀/中段英文注）
      也一律命中同一条记忆。
    - **锚点给了却一个都没命中且没给 vendor 名时返回空列表**，绝不返回全量：
      "找不到就退回全量"是本仓已出现 3 次的越权形态（会把别的供应商/租户的记忆
      一并注入）。
    - 默认（include_archived=False）＝**注入/读取口径**：只返 status='active'
      且未过期的行，并对同 (tenant, vendor, fact_key) 只保留最新一条
      （supersede 保证同键只有一条 active；这里是第二道防线，见
      _filter_memory_rows_for_read）。'superseded' 行天然被 status 过滤排除。
    - include_archived=True ＝**治理/历史口径**：不过滤 status、不过滤过期，
      归档行与已被 supersede 的旧行都可见（P3-T3 的"历史可查/可回滚"靠它）。
      调用方若拿它做注入，等于绕过 supersede/TTL —— 注入路径只用默认口径。
    - 排序：追加序倒序（rowid DESC，最新写入在前），精确匹配优先于模糊匹配
    返回 dict 列表，含全部治理字段（含 supplier_id）。
    """
    tid = str(tenant_id).strip() if tenant_id else "default"
    tid = tid or "default"
    v = str(vendor).strip() if vendor and str(vendor).strip() else ""
    try:
        anchor = int(supplier_id) if supplier_id is not None else None
    except (TypeError, ValueError):
        anchor = None
    s = get_session()
    try:
        from sqlalchemy import text as _sa_text
        q = scoped(s.query(_VendorMemoryRow), _VendorMemoryRow, tid)
        if not include_archived:
            q = q.filter(_VendorMemoryRow.status == "active")
        rows = q.order_by(_sa_text("rowid DESC")).all()
        if not include_archived:
            # 过期 + 同键去重放在 vendor 匹配之前：此处仍是 rowid DESC 全序，
            # 去重"首见即最新"成立；vendor 分组会改变顺序，不能再做去重。
            rows = _filter_memory_rows_for_read(rows)

        def _name_matched(candidates):
            """名字口径（精确优先于相关）——锚点回退与无锚点时共用同一实现。"""
            if not v:
                return list(candidates)
            try:
                from app.services.rag import _vendor_related
            except Exception as e:
                _vendor_related = None
                # P20-D9（I2 口径）：回退 None 会让下面的 elif 永不成立 ——
                # 后果必须写进日志，否则运维看到一行 warning 不知道意味着什么：
                # 本次退化为纯精确匹配，模糊/别名不再命中。
                logger.warning(
                    "list_vendor_memory._name_matched 无法加载 _vendor_related，"
                    "本次退化为纯精确匹配，模糊/别名不再命中：vendor=%s tenant_id=%s "
                    "异常=%s: %s", v, tid, type(e).__name__, e)
            exact, fuzzy = [], []
            for r in candidates:
                if r.vendor == v:
                    exact.append(r)
                elif _vendor_related is not None and _vendor_related(v, r.vendor):
                    fuzzy.append(r)
            return exact + fuzzy

        if anchor is not None:
            anchored = [r for r in rows if r.supplier_id == anchor]
            if anchored:
                # 并入"无锚点 + 名字相关"的遗留行（绝不并入别的锚点行）
                legacy = _name_matched(
                    [r for r in rows if r.supplier_id is None])
                rows = anchored + legacy
            else:
                rows = _name_matched(rows) if v else []
        elif v:
            rows = _name_matched(rows)
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
                # P1-T3：冲突治理字段（消费方＝阶段四抽取/整合链路与治理视图；
                # 现有注入链路只读 notes/sample/decay_score/status）
                "fact_key": r.fact_key or "",
                "superseded_by": r.superseded_by or "",
                "valid_to": r.valid_to or "",
                # P2-T1：主体锚点（消费方＝回填脚本的幂等判据、检索侧锚点回退判定、
                # 以及 P2-T2/P3 按 (tenant, supplier) 收窄的读侧）
                "supplier_id": r.supplier_id,
                # P3-T1（K2）：结构化事实载荷。**必须有消费方**，否则这五列就是"只写列"
                # （本仓④类缺陷）：消费方＝`memory_consolidate.decide_action(existing=...)`
                # 的同键比对与信任判定 —— 它接受行字典并按 memory_facts.ROW_FIELDS 解析，
                # 若这里不返回，既有事实在整合侧全部退化成"无 kind/无信任"的遗留行，
                # 冲突判定（低可信不得覆盖人工确认）会整条失效。
                "fact_kind": r.fact_kind,
                "fact_value": r.fact_value,
                "trust_level": r.trust_level,
                "valid_from": r.valid_from or "",
                "written_by": r.written_by or "",
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


def delete_vendor_memory(memory_id=None, vendor=None, tenant_id=None,
                         supplier_id=None, dry_run=False):
    """P1-T1 删除记忆（硬删）：单条（memory_id）或按供应商批量（vendor / supplier_id）。

    返回 (受影响行数, 被删行的 memory_id 列表)。为什么把 memory_id 一并返回：
    记忆同时存在于两张介质（本表 + Chroma 向量库），SQL 事务管不到向量层，只能
    由调用方拿这份 memory_id 清单去级联删（services.memory_store.delete_memory）。

    为什么硬删而不是置 status='archived'：删除权闭环（香港 PDPO s.26「删除已不再
    需要的个人数据」）要求数据真正不再可取回；归档行仍留在库里，且默认查询只列
    active，反而变成"看不见但仍在"的残留。

    租户：tenant_id 传了就强制过滤 —— 跨租户删除因此命中 0 行（端点据此回 404），
    这是本仓 H1「读侧不传租户」那类缺陷的防线；不传则不过滤（仅供内部脚本/测试的
    全量清理路径，端点一律传租户）。

    三个过滤条件都不给时直接返回 (0, [])：避免"删整个租户记忆"这种误删入口，
    调用方必须显式给 memory_id / vendor / supplier_id。

    supplier_id（FP-2a）：主体锚点（suppliers.id），与 vendor 名过滤是 **OR** 关系
    （vendor 是自由文本，锚定到同一供应商但写法不同的行按名删会漏掉）。memory_id
    单条模式优先（给 memory_id 时忽略另两个选择器，沿用"单条即单条"的既有语义）。

    dry_run（FP-2c）：True 时**只查不删**，返回 (0, mids) —— mids 即同一份过滤会
    命中的 memory_id 清单。供 vector-first 删除路径复用（先拿清单删向量、失败即抛，
    DB 行不动），不另写第二份"选中哪些行"的口径。

    批量删除覆盖该供应商的全部 status（active/archived）：意图就是"删掉这个供应商
    的记忆"，把已归档行留下同样违背删除语义。
    """
    if _VendorMemoryRow is None:
        return (0, [])
    mid = str(memory_id).strip() if memory_id is not None else ""
    vid = str(vendor).strip() if vendor is not None else ""
    sid = str(supplier_id).strip() if supplier_id is not None else ""
    if not mid and not vid and not sid:
        return (0, [])
    s = get_session()
    try:
        q = scoped(s.query(_VendorMemoryRow), _VendorMemoryRow, tenant_id)
        if mid:
            q = q.filter(_VendorMemoryRow.memory_id == mid)
        else:
            # OR 口径：名匹配或锚点匹配任一命中即删（两者都给时取并集）
            from sqlalchemy import or_
            cond = []
            if vid:
                cond.append(_VendorMemoryRow.vendor == vid)
            if sid:
                try:
                    cond.append(_VendorMemoryRow.supplier_id == int(sid))
                except ValueError:
                    return (0, [])   # 非法 supplier_id：不匹配任何行，也不误删
            q = q.filter(or_(*cond))
        rows = q.all()
        if not rows:
            return (0, [])
        mids = [str(r.memory_id) for r in rows if r.memory_id]
        if dry_run:
            return (0, mids)
        # 批量删除走 SQL DELETE（不经 ORM 逐行 delete）：历史库的物理主键形态与 ORM
        # 声明的 memory_id 主键未必一致，按 WHERE 删除不受主键映射影响。
        q.delete(synchronize_session=False)
        s.commit()
        return (len(rows), mids)
    finally:
        s.close()


def bump_vendor_memory_hit(memory_ids):
    """Gap B1 命中记账：自增 hit_count 并刷新 last_hit_at（检索注入后调用）。

    T8 非对称衰减（强化慢）：每次命中按 settings 键 'memory_decay_hit_bonus'
    （缺省 +0.05）上调 decay_score，封顶 1.0。
    封顶保留、但起始分已下调（P1-T2）：写入侧起始分取 memory_initial_decay_score
    （缺省 0.7）而非 1.0，否则 min(1.0, cur + bonus) 在起始即封顶，强化恒等、
    衰减只剩惩罚一侧 —— 强化必须真的能抬高分数，本函数才有意义。
    memory_id 不存在的忽略；返回实际更新的行数。异常由调用方兜底（不阻断识别）。
    """
    ids = [str(m) for m in (memory_ids or []) if m]
    if not ids:
        return 0
    try:
        from app.services import settings_service
        bonus = settings_service.get_float("memory_decay_hit_bonus", 0.05)
    except Exception as e:
        bonus = 0.05
        # P20-D9（I2 口径）：回退 +0.05 是刻意兜底，但静默会让"强化档位配置读不出来"
        # 永久不为人知（表现只是记忆衰减得比预期快）。
        logger.warning(
            "bump_vendor_memory_hit 读取 memory_decay_hit_bonus 失败，"
            "本次按缺省 +0.05 强化：memory_ids=%s 异常=%s: %s",
            ids, type(e).__name__, e)
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
                           source_receipt_ids=None,
                           supplier_id=None, fact_kind=None, fact_key=None,
                           fact_value=None, reject_reason=None,
                           status="pending"):
    """蒸馏产物入待确认队列（不直接生效）。

    幂等：同 (vendor, tenant_id, content, status) 已有行时并入触发来源并返回
    既有行 (id, False)。返回 (pending_id, is_new)。

    P3-T1（K2）字段级 diff 载荷（可选）：supplier_id / fact_kind / fact_key /
    fact_value / reject_reason。给了就让审核者能逐字段看到"这条记忆打算写什么"
    （以及被拒时的理由），而不是对整段 content 只能整体接受/拒绝。
    **向后兼容**：全部缺省 None → 新列落 NULL，行为与新增形参前完全一致
    （遗留调用方 rag.distill_feedback_to_pending 零改动）。
    why 载荷用与 vendor_memory.ROW_FIELDS 逐字相同的列名：approve 时可直接把载荷交给
    save_memory_fact，不需要第二次字段映射。

    status（Q2）：`"pending"`（缺省，进人工闸，可被 approve / reject）或 `"rejected"`
    （**闸门自动拒绝的审计行**：只留"这条候选为什么没进来"的可查痕迹，不进可批准队列）。
    为什么被拒也要建行、而不是只打日志：K2 建了 reject_reason 列却零写入方，于是
    "上次为什么没进来"在库里查不到 —— 只打日志时审核者与被拒方的下一次写入都看不见
    （本仓高发的「只建列、零消费方」）。为什么不用 `"pending"` 承载被拒候选：被拒候选的
    载荷必然过不了 `memory_facts.validate_fact`，它在审核界面上却看起来可批准，一旦被
    批准，approve 链路会回落自由文本路径把它写成一条自由文本记忆 —— 等于人工闸给写入侧
    闸门开了后门；且人工闸列表与角标只列 pending，被拒行混进去会把闸门刷成噪声。
    去重按 (vendor, tenant_id, content, status)：同一条理由反复发生＝同一件事反复发生，
    合并触发单据而不是堆行。`"approved"` 不接受：人工批准必须经 review_pending_memory，
    绕过它等于伪造一次审核。
    """
    final_status = str(status or "pending").strip() or "pending"
    if final_status not in ("pending", "rejected"):
        raise ValueError(
            "enqueue_pending_memory 的 status 只允许 'pending' / 'rejected'，实得 %r"
            "（人工批准必须经 review_pending_memory 流转）" % (status,))
    vid = str(vendor or "")
    tid = str(tenant_id or "default").strip() or "default"
    refs = json.dumps([str(r) for r in (source_receipt_ids or [])],
                      ensure_ascii=False)
    payload = {
        "supplier_id": _coerce_supplier_id(supplier_id),
        "fact_kind": fact_kind,
        "fact_key": fact_key,
        "fact_value": fact_value,
        "reject_reason": reject_reason,
    }
    s = get_session()
    try:
        dup = s.query(_PendingMemoryRow).filter(
            _PendingMemoryRow.vendor == vid,
            _PendingMemoryRow.tenant_id == tid,
            _PendingMemoryRow.status == final_status,
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
                # 与 upsert_vendor_memory 去重命中同一条纪律：旧行可能是在载荷列
                # 上线前入队的（列为 NULL），此时补上；只补空值，不覆盖已有载荷
                # （复用旧行不得改写它待审的内容）。
                for col, val in payload.items():
                    if val is None:
                        continue
                    if getattr(dup, col, None) in (None, ""):
                        setattr(dup, col, val)
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
            status=final_status,
            created_at=now_iso(),
            **payload,
        )
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, True
    finally:
        s.close()


def _coerce_supplier_id(value):
    """pending 载荷的 supplier_id 归一成 int；不可归一返回 None（不编造锚点）。

    为什么显式归一而不是直接写：列是 INTEGER，写进 'abc' 这类字符串在 SQLite 里
    会被原样存成文本，之后按 (supplier_id, vendor) 查询永远匹配不上且零报错。
    """
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("[memory] pending supplier_id 非法，落 NULL：%r", value)
        return None


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
        # P3-T1（K2）：字段级 diff 载荷（消费方＝人工闸的 diff 视图与 approve 落库：
        #   None = 该行没有结构化载荷，界面退回整段文本视图 —— 刻意不折算成空串，
        #   否则会渲染出一排"值为空"的假字段）。
        "supplier_id": r.supplier_id,
        "fact_kind": r.fact_kind,
        "fact_key": r.fact_key,
        "fact_value": r.fact_value,
        "reject_reason": r.reject_reason,
    }


def list_pending_memory(status=None, tenant_id=None):
    """待确认记忆列表，id 倒序。

    AH3 契约：**缺省（不传 status，或显式传 None）只返回 `status='pending'`** —— 那才是
    "待确认"的语义。要看其它状态必须显式传：`status='rejected'`（落选审计行）、
    `status='approved'`（已批准行）。本函数**不再提供"不过滤/全状态"模式**：人审队列与
    审计行的语义不同，刻意不提供混装视图，需要跨状态快照的调用方自行按状态分别取。

    why：Q2 之后同一张表里多了一族 `status='rejected'` 的**落选审计行**（候选没过
    `validate_fact`、或与既有同值事实被判 NOOP —— 按设计"不进队列"，只留理由备查）。
    它们不是等人审的行。旧写法 `if status:` 使"不传 status"＝"不过滤"，于是
    「待确认记忆」列表与前端角标会把审计行当成待批记忆（角标虚高、审核者看到永远批不掉
    的条目），与本仓自己的口径（待审界面只列 status='pending'）相反。改后隔离是**默认
    安全**的：新增调用方漏传 status 时落到人审队列，而不是混入审计行的全量表。
    传 None 与不传参数行为一致（刻意不让"签名默认值"与"显式 None"分成两条行为）。
    """
    if _PendingMemoryRow is None:
        return []
    # 传 None / 空串一律按"待确认"处理；否则按显式状态过滤（kwargs 读法不受调用姿势影响）。
    target_status = str(status) if status else "pending"
    s = get_session()
    try:
        q = scoped(s.query(_PendingMemoryRow), _PendingMemoryRow, tenant_id)
        q = q.filter(_PendingMemoryRow.status == target_status)
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


def delete_pending_memory(vendor=None, supplier_id=None, tenant_id=None):
    """FP-2b：删除某主体的全部待确认记忆行（vendor 名 / supplier_id 锚点，OR 过滤）。

    erasure 语义（PDPO s.26）：**覆盖全部 status** —— 已审过的 approved/rejected 历史行
    同样是该主体的个人数据（审计留痕在 system_audit_log，不在 pending 行本身）。
    两个选择器都空直接返回 0：防"不传参 = 删整租户队列"的误删入口（与
    delete_vendor_memory 的双空拒绝同口径）。返回删除的行数。
    """
    if _PendingMemoryRow is None:
        return 0
    vid = str(vendor).strip() if vendor is not None else ""
    sid = str(supplier_id).strip() if supplier_id is not None else ""
    if not vid and not sid:
        return 0
    from sqlalchemy import or_
    cond = []
    if vid:
        cond.append(_PendingMemoryRow.vendor == vid)
    if sid:
        try:
            cond.append(_PendingMemoryRow.supplier_id == int(sid))
        except ValueError:
            return 0    # 非法锚点：不匹配任何行，也不误删
    s = get_session()
    try:
        q = scoped(s.query(_PendingMemoryRow), _PendingMemoryRow,
                   tenant_id).filter(or_(*cond))
        n = q.delete(synchronize_session=False)
        s.commit()
        return int(n)
    finally:
        s.close()


# -------------------------------------------------------------
# P3-T4（B6）：记忆抽取的运行记录（中间产物可查、失败可复盘）
# -------------------------------------------------------------
def record_memory_extract_run(tenant_id="default", receipt_id=None, vendor="",
                              supplier_id=None, trigger="", diffs_count=0,
                              facts_produced=0, facts_rejected=0,
                              reject_reasons=None, prompt_source="", error=""):
    """把一次记忆抽取的运行记录写库，返回新行的 run_id（表不可用时返回 None）。

    为什么要有这张表：`memory_extract.get_last_run()` / `get_last_error()` 是
    `threading.local` 的**内存态** —— 进程重启即丢，事后无法复盘"某次抽取为什么产出 0 条
    / 是不是一直走回退文案 / 失败在哪一步"。本函数是那份内存态的**唯一持久化落点**：
    入参就是它的内容，**不在这里重算任何统计**（本仓高发的「同一根因两份实现」）。

    失败行也写 `reject_reasons_json`：`error` 非空而 `reject_reasons` 为空时，把 error
    本身作为一条理由落进列表。why：本表的两列服务于同一个问题"这次为什么没有候选"，
    若失败行只填 error、理由列留空，库里的"失败了"与"本来就没什么可记的"长得一样 ——
    正是本仓高发的"静默失效"。此归一**只发生在持久化层**，不改 `memory_extract` 的
    内存态（那边的 `reject_reasons` 仍只含候选级理由，两种语义不混）。

    `prompt_source` 单列（不并入 error）：回退文案与注册表版条款等价、结果可用，
    回退率是 **prompt 治理失效**的信号，混进 error 会让告警方向失真。

    幂等：run_id 自增主键，每次调用新增一行。运行记录按"次"计，**刻意不去重** ——
    去重会把"同一张单据被反复重跑"这个信号抹掉（而它正是复盘要找的东西）。
    另：本函数**不吞异常**（与 enqueue_pending_memory 同口径）；调用方负责把它裹进
    try/except —— 记录失败绝不能让已成功的保存/入账变成 500。

    消费方：`list_memory_extract_runs`（复盘查询；其再上层调用方见该函数 docstring）。
    """
    if _MemoryExtractRunRow is None:
        return None
    reasons = [str(r) for r in (reject_reasons or []) if str(r).strip()]
    err = str(error or "")
    if err and not reasons:
        # 见 docstring：失败行的"为什么没有候选"就是 error 本身
        reasons = [err]
    tid = str(tenant_id or "default").strip() or "default"
    rid = None if receipt_id in (None, "") else coerce_int(receipt_id, 0)
    row = _MemoryExtractRunRow(
        tenant_id=tid,
        receipt_id=rid,
        vendor=str(vendor or ""),
        supplier_id=_coerce_supplier_id(supplier_id),
        trigger=str(trigger or ""),
        diffs_count=coerce_int(diffs_count, 0),
        facts_produced=coerce_int(facts_produced, 0),
        facts_rejected=coerce_int(facts_rejected, 0),
        reject_reasons_json=json.dumps(reasons, ensure_ascii=False),
        prompt_source=str(prompt_source or ""),
        error=err,
        created_at=now_iso(),
    )
    s = get_session()
    try:
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.run_id
    finally:
        s.close()


def _memory_extract_run_to_dict(r):
    """运行记录行 → dict。

    `reject_reasons`（已解析的列表）与 `reject_reasons_json`（原始 JSON）都给出：
    前者供调用方直接遍历（复盘脚本），后者保留原样以支持"库里的值到底是什么"的核对。
    JSON 损坏时回落 `[]` 而**不抛**：一条坏行不该让整个复盘查询失败（也不静默装作
    没有理由 —— 原始的坏字符串仍在 reject_reasons_json 里可见）。
    """
    raw = r.reject_reasons_json or "[]"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        parsed = []
    if not isinstance(parsed, list):
        parsed = []
    return {
        "run_id": r.run_id,
        "tenant_id": r.tenant_id or "default",
        "receipt_id": r.receipt_id,
        "vendor": r.vendor or "",
        "supplier_id": r.supplier_id,
        "trigger": r.trigger or "",
        "diffs_count": int(r.diffs_count or 0),
        "facts_produced": int(r.facts_produced or 0),
        "facts_rejected": int(r.facts_rejected or 0),
        "reject_reasons_json": raw,
        "reject_reasons": [str(x) for x in parsed],
        "prompt_source": r.prompt_source or "",
        "error": r.error or "",
        "created_at": r.created_at or "",
    }


def list_memory_extract_runs(tenant_id=None, receipt_id=None, trigger=None,
                             limit=None):
    """记忆抽取运行记录列表（可按租户 / 单据 / 触发入口过滤），默认按时间倒序。

    why 默认倒序：复盘的第一眼看的是"最近几次发生了什么"；同秒多行用 run_id 倒序兜底
    （run_id 自增，单调递增），避免 `created_at` 秒级精度下顺序不确定。

    消费方（**待接线，禁止在别处声称已接**）：
    - P8b·AA2 的 `demo/scripts/memory_extract_replay.py`（`--failed-only` 需要按
      `error != ''` 捞出可重跑的失败运行）；
    - 本波新增的 `tests/test_memory_extract_runs_persistence.py`（租户不跨租判据）。
    当前**尚无生产读端点**接入本函数 —— 不要把它写成"已在管理台可看"。
    """
    if _MemoryExtractRunRow is None:
        return []
    s = get_session()
    try:
        q = scoped(s.query(_MemoryExtractRunRow), _MemoryExtractRunRow, tenant_id)
        if receipt_id not in (None, ""):
            q = q.filter(_MemoryExtractRunRow.receipt_id == coerce_int(receipt_id, 0))
        if trigger not in (None, ""):
            q = q.filter(_MemoryExtractRunRow.trigger == str(trigger))
        q = q.order_by(_MemoryExtractRunRow.created_at.desc(),
                       _MemoryExtractRunRow.run_id.desc())
        if limit not in (None, ""):
            q = q.limit(max(0, coerce_int(limit, 0)))
        return [_memory_extract_run_to_dict(r) for r in q.all()]
    finally:
        s.close()


def apply_vendor_memory_override_penalty(vendor, tenant_id="default"):
    """T8 非对称衰减（淘汰快）：用户覆写 → 该供应商活跃记忆扣分。

    步长取 settings 键 'memory_decay_override_penalty'（缺省 -0.12）；
    扣分后低于归档阈值（键 'memory_archive_threshold'，缺省 0.5）的行自动
    置 archived，停止注入。返回受影响的行数。

    R11-AR1：本次真的发生 active -> archived 迁移的行，会交给
    `services.memory_store.archive_memory_vectors` 级联删掉其向量 —— 归档与删除在
    向量层上必须对称（删除早有级联，归档此前没有，于是每归档一次就留一枚孤儿向量，
    巡检立刻报 `orphan_mids`）。级联失败只告警，不影响衰减结果与调用方（单据保存）。
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
        archived_mids = []   # R11-AR1：本次真的 active -> archived 的 memory_id
        for r in rows:
            cur = float(r.decay_score if r.decay_score is not None else 1.0)
            new = cur - penalty
            r.decay_score = new
            if new <= threshold:
                r.status = "archived"
                archived_mids.append(str(r.memory_id))
            r.updated_at = now_iso()
        s.commit()
        # R11-AR1：归档与向量层对称 —— 删除路径有级联（memory_store.delete_memory），
        # 归档此前没有，于是每归档一次就在向量库留一枚孤儿（巡检 orphan_mids 立刻报）。
        # 只对本次真发生 active -> archived 迁移的行调用：行一旦归档，本查询（status ==
        # "active"）就取不到它，故重复调用不会重复删向量（幂等）。级联失败只告警 ——
        # 衰减结果与调用方（单据保存）不受影响，向量残留由巡检可查。
        if archived_mids:
            try:
                from app.services import memory_store
                memory_store.archive_memory_vectors(tid, archived_mids, vendor=vid)
            except Exception as e:
                logger.warning(
                    "[db] R11 归档向量级联失败（衰减已提交，向量可能残留）"
                    "vendor=%s tenant=%s memory_ids=%s: %s",
                    vid, tid, archived_mids, e)
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


def memory_initial_decay_score():
    """新记忆的起始衰减分（settings 实时读取，缺省 0.7），写入与恢复两处共用。

    为什么需要这个键（P1-T2 修「衰减单向」的根）：起始分若与上限 1.0 相同，命中强化
    的 min(1.0, cur + bonus) 就是恒等操作 —— 分数只降不升，归档成了不可逆终点。
    起始分下调后「命中强化」才有可观测效果，归档也才有配套的恢复语义。

    单点实现（写入 upsert_vendor_memory 与恢复 revive_vendor_memory 共用同一函数），
    避免本仓高发的「同一口径多份实现、只改了一份」。

    取值自洽性：起始分必须高于 memory_archive_threshold（缺省 0.5），否则新写入的
    记忆当次即被判为劣化、不可注入；两个键由管理台维护，本函数不做双向钳制。
    """
    try:
        from app.services import settings_service
        return settings_service.get_float("memory_initial_decay_score", 0.7)
    except Exception:
        return 0.7


def memory_max_active_per_vendor():
    """每 (vendor, tenant) 的 active 记忆容量上限（settings 实时读取，缺省 200）。

    W-4 / M2：追加式写入此前无容量边界，同一供应商可无限追加 active 行，高分供应商
    会被噪声记忆淹没、检索质量随使用下降。0 或负值 = 不限制（向后兼容：关闭淘汰即回到
    「只追加、无边界」的旧行为，与 memory_ttl_days 同款「关闭即老行为」语义）。

    单点读取（写入侧 `_evict_vendor_memory_over_capacity` 与将来的治理视图共用同一
    阈值口径），避免本仓高发的「同一根因多份实现、只改了一份」。
    """
    try:
        from app.services import settings_service
        return settings_service.get_int("memory_max_active_per_vendor", 200)
    except Exception as e:
        # 与 memory_ttl_days 同口径：回退值 200（有界）本身是刻意兜底，但**静默**会让
        # "容量配置读不出来"与"本来就设了 200"在库中完全同形 —— 容量闸失效而无人可见。
        logger.warning(
            "memory_max_active_per_vendor 读取失败，本次按 200（有界）处理："
            "异常=%s: %s", type(e).__name__, e)
        return 200


def _evict_vendor_memory_over_capacity(vendor, tenant_id, keep_memory_id=None,
                                       written_by=None):
    """容量淘汰（W-4）：active 行数**超过**上限时归档多余的最低分/最旧行。

    调用契约：写入侧在**新行已提交**后调用（只有真的新增了 active 行才可能超限；
    幂等去重命中不增行、不会调用）。返回本次归档的 memory_id 列表（未超限返回 []）。

    淘汰口径（确定性 + 可审计，缺一不可）：
      候选 = 同 (vendor, tenant) 下的 active 行，排除 keep_memory_id（= 本次刚写入的
      新行，绝不"写完即被自己淘汰"）；按 (decay_score ASC, created_at ASC, rowid ASC)
      排序，取前 `active 数 - 上限` 条。decay_score 为 NULL 的行按 1.0 处理 —— 与
      `apply_vendor_memory_override_penalty` 同口径（视为高分、优先保留）。三名并列
      用 rowid 破平，故同输入必然淘汰同一批（无随机性）。**只在超过上限时淘汰**：
      active 数 <= 上限直接返回，未超限的既有行为（幂等去重等）逐字节不变。

      FP-4：容量计数**排除已过 `valid_to` 的 active 行**（`now > valid_to` 严格大于；
      空/NULL/非法格式/恰好相等一律照常计入 —— 与 `_memory_row_expired` 的读侧口径
      对齐「解析不出来宁可算入」）。为什么：D4 设计下到期行 status 不迁移，若计入
      容量，满员后每次写入都会误淘一条 active 好行。刻意不复用 `_memory_row_expired`：
      那里还有 created_at+TTL 全局兜底过期，容量口径只有 valid_to（见 SPEC FP-4 §4.2，
      口径蔓延须先改规格）。到期行本身不迁移、不删不归档（治理视图仍可见）。

    归档而非物理删除：与本仓「非 active 迁移需级联向量」的一贯纪律一致 —— 归档路径
    已有向量级联，此处**复用** `memory_store.archive_memory_vectors`（不写第二份
    collection 操作），归档行仍可经 `revive_vendor_memory` 恢复；物理删除不可逆。

    审计：复用 `append_system_audit_log` —— 记忆治理的 approve / reject 用的就是它
    （见 api_memory._safe_audit）；本动作无 receipt 上下文，故不走 receipt 级的
    `append_audit_log`（后者按 receipt_id 定位，无单据时会静默 no-op，等于不留痕）。
    action 记为 `capacity_eviction`，载荷含 vendor / tenant / 淘汰的 memory_id。
    """
    if _VendorMemoryRow is None:
        return []
    vid = str(vendor or "")
    tid = str(tenant_id or "default").strip() or "default"
    if not vid:
        return []
    limit = memory_max_active_per_vendor()
    if limit is None or limit <= 0:
        return []   # 0 / 负值 = 不限制（向后兼容）
    keep = str(keep_memory_id).strip() if keep_memory_id else None
    evicted = []
    s = get_session()
    try:
        from sqlalchemy import func as _func, text as _sa_text
        q = s.query(_VendorMemoryRow).filter(
            _VendorMemoryRow.vendor == vid,
            _VendorMemoryRow.tenant_id == tid,
            _VendorMemoryRow.status == "active",
        # FP-4：排序仍在 SQL 侧完成（rowid 破平是 SQLite 方言，Python 侧拿不到），
        # 过期排除放 Python 侧逐行判定 —— valid_to 是 ISO 文本、秒级精度混合，
        # SQL 字符串比较的顺序不可靠。过滤保序，候选序即淘汰优先序。
        ).order_by(
            _func.coalesce(_VendorMemoryRow.decay_score, 1.0).asc(),
            _VendorMemoryRow.created_at.asc(),
            _sa_text("rowid ASC"),
        ).all()
        now = datetime.now()
        kept = []
        excluded_expired = 0
        for r in q:
            vt = _parse_iso_dt(getattr(r, "valid_to", "") or "")
            if vt is not None and now > vt:
                excluded_expired += 1
                continue    # 已过 valid_to 的行不占容量坑（也不在此迁移/删除）
            kept.append(r)
        total = len(kept)
        if total <= limit:
            return []
        excess = total - limit
        candidates = [
            r for r in kept
            if not keep or str(r.memory_id) != keep][:excess]
        for r in candidates:
            r.status = "archived"
            r.updated_at = now_iso()
            evicted.append(str(r.memory_id))
        if evicted:
            s.commit()
    finally:
        s.close()
    if not evicted:
        return []
    # R11 同族：归档与向量层必须对称 —— 只对**本次真发生 active -> archived** 的行
    # 调用级联（幂等：那些行已非 active，重复淘汰取不到它们）。级联失败只告警，
    # 淘汰结果已提交，向量残留由巡检可查。
    try:
        from app.services import memory_store
        memory_store.archive_memory_vectors(tid, evicted, vendor=vid)
    except Exception as e:
        logger.warning(
            "[db] W-4 容量淘汰向量级联失败（归档已提交，向量可能残留）"
            "vendor=%s tenant=%s memory_ids=%s: %s", vid, tid, evicted, e)
    # 审计（失败只告警，不得让已提交的淘汰/写入失败）
    try:
        append_system_audit_log(
            str(written_by or "").strip() or "system",
            "capacity_eviction", "vendor_memory",
            {"status": "active", "vendor": vid, "tenant_id": tid,
             "memory_ids": evicted, "limit": limit,
             # FP-4：本次被排除出容量计数的过期行数（纯增量，便于 QA 断言）
             "excluded_expired": excluded_expired},
            {"status": "archived"},
        )
    except Exception as e:
        logger.warning(
            "[db] W-4 容量淘汰审计写入失败 vendor=%s tenant=%s: %s", vid, tid, e)
    return evicted


def revive_vendor_memory(memory_id, tenant_id=None):
    """P1-T2 归档恢复：把 status='archived' 的记忆恢复为 active 并重置起始分。

    why 需要恢复通道：apply_vendor_memory_override_penalty 的扣分是单向的 —— 低于
    阈值即归档，而默认查询只列 active，归档行此后既不会被检索命中、也不会被命中
    强化回升。一次误判（或一次合理的业务变更）因此会把该供应商的记忆永久踢出注入
    面。归档必须是可逆的，恢复动作由 admin 经端点显式发起。

    为什么重置为起始分而不是"恢复归档前的原值"：原值已在阈值以下，直接复活会让它
    停在可注入边缘、下次检索前仍近乎不可用；重置为起始分等于「由一次人工背书重新
    开始积累」，且与写入侧同一口径（memory_initial_decay_score）。

    向量层（R11-AR1 起必须补写）：归档路径现已级联删掉该 mid 的向量（与删除对称），
    因此恢复不能只改 status —— 只改 status 会留下「DB 行 active、向量库无该 mid」的
    脱钩（巡检报 missing_vector_mids，且没有写入路径会自愈）。故恢复成功后调用
    `services.memory_store.restore_memory_vectors` 补写；补写失败只告警（状态已生效）。
    （R11 之前的说明是「归档不动向量层、行回到 active 即自动放行」—— 该前提已被
    R11 的归档级联推翻，勿再按它推断。）

    tenant_id：传了就强制过滤 —— 跨租户恢复命中 0 行、端点据此回 404（与删除端点同
    口径：不给出用 403/404 差异探测他租户 memory_id 是否存在的接口）；不传则不过滤
    （仅供内部脚本/测试）。

    返回 dict：「found」区分"目标存在"与"不存在/跨租户"，「revived」区分"本次真的
    恢复了"与"本来就是 active"，端点据此把 404（目标不可见）与 409（无需恢复）分开
    回答，而不是把后者伪装成 404。
    """
    if _VendorMemoryRow is None:
        return {"found": 0, "revived": 0, "already_active": False}
    mid = str(memory_id).strip() if memory_id is not None else ""
    if not mid:
        return {"found": 0, "revived": 0, "already_active": False}
    s = get_session()
    try:
        q = scoped(s.query(_VendorMemoryRow), _VendorMemoryRow, tenant_id)
        row = q.filter(_VendorMemoryRow.memory_id == mid).first()
        if row is None:
            return {"found": 0, "revived": 0, "already_active": False}
        info = {
            "found": 1, "revived": 0, "already_active": False,
            "memory_id": str(row.memory_id), "vendor": row.vendor or "",
        }
        if (row.status or "active") != "archived":
            # 本来就没归档：不改分、不改状态（第二次点击恢复不应把分数重置一遍）
            info["already_active"] = True
            info["status"] = row.status or "active"
            info["decay_score"] = float(
                row.decay_score if row.decay_score is not None else 0.0)
            return info
        rv_mid = str(row.memory_id)
        rv_vendor = str(row.vendor or "")
        rv_tid = str(row.tenant_id or "")
        row.status = "active"
        row.decay_score = memory_initial_decay_score()
        row.updated_at = now_iso()
        s.commit()
        # R11-AR1：状态提交后再补向量（恢复动作不得因向量层故障失败）。取值在 commit 前
        # 先落到局部变量，避免依赖 commit 后的 ORM 属性刷新。只在本行真的从非 active 回到
        # active 时走到这里 —— 本来 active 的行在上面就 return 了（不重复写向量）。
        try:
            from app.services import memory_store
            memory_store.restore_memory_vectors(rv_tid, rv_mid, vendor=rv_vendor)
        except Exception as e:
            logger.warning(
                "[db] R11 恢复补向量失败（状态已置 active，向量可能缺失）"
                "memory_id=%s vendor=%s tenant=%s: %s",
                rv_mid, rv_vendor, rv_tid, e)
        info["revived"] = 1
        info["status"] = "active"
        info["decay_score"] = float(row.decay_score)
        return info
    finally:
        s.close()


# -------------------------------------------------------------
# app_settings 通用键值读写（T10 Gap E3）：settings_service 的底层。
# engine_config / system_audit_json 是历史专用键，其余键走通用读写。
# WS3：全部追加**末位可选参数** tenant_id —— None / 空串等价 'default'（全局桶）。
# 全局桶 = app_settings（key 单列主键）；门店覆盖 = app_setting_overrides。
# 三类读写口径一致：tenant_id 缺省即全局桶，既有调用点零改动。
# -------------------------------------------------------------
def _setting_tenant(tenant_id):
    """租户键归一：None / 空串 / 纯空白 → 'default'（全局桶）。"""
    if tenant_id is None:
        return "default"
    t = str(tenant_id).strip()
    return t or "default"


def get_app_setting(key, default=None, tenant_id=None):
    """读取配置单个键；不存在返回 default（None）。

    tenant_id 缺省（None / 空串）读全局桶 app_settings；
    传入租户则读该门店的覆盖行 app_setting_overrides。
    """
    tenant = _setting_tenant(tenant_id)
    s = get_session()
    try:
        if tenant == "default":
            row = s.query(_AppSettingRow).filter_by(key=str(key)).first()
        else:
            row = s.query(_AppSettingOverrideRow).filter_by(
                key=str(key), tenant_id=tenant).first()
        return row.value if row is not None else default
    finally:
        s.close()


def set_app_setting(key, value, tenant_id=None):
    """写入配置单个键（幂等 upsert，值一律文本化）。

    tenant_id 缺省（None / 空串）写全局桶 app_settings；
    传入租户则写该门店的覆盖行 app_setting_overrides。
    """
    tenant = _setting_tenant(tenant_id)
    s = get_session()
    try:
        if tenant == "default":
            row = s.query(_AppSettingRow).filter_by(key=str(key)).first()
            if row is None:
                row = _AppSettingRow(key=str(key))
                s.add(row)
        else:
            row = s.query(_AppSettingOverrideRow).filter_by(
                key=str(key), tenant_id=tenant).first()
            if row is None:
                row = _AppSettingOverrideRow(key=str(key), tenant_id=tenant)
                s.add(row)
        row.value = "" if value is None else str(value)
        s.commit()
    finally:
        s.close()


def list_app_settings(tenant_id=None):
    """配置键值 dict（含 engine_config 等历史键，调用方自滤）。

    tenant_id 缺省（None / 空串）返回全局桶 app_settings；
    传入租户则返回该门店的覆盖键 app_setting_overrides。
    """
    tenant = _setting_tenant(tenant_id)
    s = get_session()
    try:
        if tenant == "default":
            rows = s.query(_AppSettingRow).all()
        else:
            rows = s.query(_AppSettingOverrideRow).filter_by(
                tenant_id=tenant).all()
        return {r.key: r.value for r in rows}
    finally:
        s.close()


# -------------------------------------------------------------
# 引擎配置（admin 管理）
# -------------------------------------------------------------
def _normalize_legacy_cli_leg(eng, model, default_model):
    """单腿的遗留 CLI 归一：引擎枚举 opencode/codebuddy → openai；CLI 模型名 → default_model。

    返回 (new_eng, new_model)，**未改动的侧为 None**，调用方只写回非 None 的侧。
    这个「只写改动侧」的约定必须保留：读闸门面对的是可能缺字段的存量 JSON，
    把原本不存在的字段填成 None 会让 EngineConfig(**stored) 校验失败。
    判定全部走 models.is_legacy_cli_model 单一实现（原先在 db.py 里抄了 9 份）。
    """
    from app.models import EngineKind, is_legacy_cli_engine, is_legacy_cli_model
    new_eng = EngineKind.OPENAI if is_legacy_cli_engine(eng) else None
    new_model = default_model if is_legacy_cli_model(model) else None
    return new_eng, new_model


def get_engine_config():
    s = get_session()
    try:
        row = s.get(_AppSettingRow, "engine_config")
        if row is None:
            from app.models import EngineConfig
            return EngineConfig()
        from app.models import EngineConfig
        from app import secret_store
        stored = secret_store.decrypt_config_dict(json.loads(row.value))
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
        # 清理已废弃的全局超时字段（UI 已移除，extra=forbid 会拒绝该字段）
        stored.pop("call_timeout_seconds", None)
        # 清理历史已保存的 opencode / codebuddy CLI 引擎与模型配置
        for eng_field, mod_field, def_model in (
            ("recognition_engine", "recognition_model", _REC_DEFAULT_MODEL),
            ("audit_engine", "audit_model", _SF_DEFAULT_AUD_MODEL),
            ("parse_llm_engine", "parse_llm_model", _SF_DEFAULT_REC_MODEL),
            ("grey_recognition_engine", "grey_recognition_model", _REC_DEFAULT_MODEL),
            ("grey_audit_engine", "grey_audit_model", _SF_DEFAULT_AUD_MODEL),
            ("grey_parse_llm_engine", "grey_parse_llm_model", _SF_DEFAULT_REC_MODEL),
        ):
            new_eng, new_model = _normalize_legacy_cli_leg(
                stored.get(eng_field), stored.get(mod_field), def_model)
            if new_eng is not None:
                stored[eng_field] = new_eng
            if new_model is not None:
                stored[mod_field] = new_model
        return EngineConfig(**stored)
    finally:
        s.close()


def set_engine_config(cfg, source=None):
    """写入引擎配置。

    source 非空时覆盖 cfg.config_source（'manual'=管理台手工保存，'auto'=.env 装配）；
    缺省 None 表示保留 cfg 自带来源标记 —— hydrate 重新装配时据此维持 auto，
    而管理台 PUT 显式传 'manual' 声明「重启后不得被 .env 覆盖」（T1 修复）。

    T9 收尾：写入闸门处把 call_timeout_seconds 归一到硬上限（clamp_call_timeout）。
    放在这里而非各调用方，是因为写入口共 6 处（hydrate ×3 / PUT / promote / rollback /
    guardian），逐处记得归一必然漏 —— 典型「修了 A 漏了 B」。放在闸门处可保证
    「DB 里存的值恒等于实际生效值」成为持久层不变量，连回滚旧快照（含 90）也被兜住。
    """
    s = get_session()
    try:
        from app.models import clamp_call_timeout
        if source is not None:
            cfg.config_source = str(source)
        # 存量/回滚快照里的超时超上限值一律归一，避免「显示 90 实际 60」
        cfg.call_timeout_seconds = clamp_call_timeout(getattr(cfg, "call_timeout_seconds", None))
        for tf in (
            "rec_timeout_seconds", "aud_timeout_seconds", "parse_timeout_seconds",
            "grey_rec_timeout_seconds", "grey_aud_timeout_seconds", "grey_parse_timeout_seconds"
        ):
            if hasattr(cfg, tf) and getattr(cfg, tf, None) is not None:
                setattr(cfg, tf, clamp_call_timeout(getattr(cfg, tf)))

        from app.models import clamp_retry_rounds
        for rf in (
            "openai_rec_max_retry_rounds", "openai_aud_max_retry_rounds", "openai_parse_max_retry_rounds",
            "grey_openai_rec_max_retry_rounds", "grey_openai_aud_max_retry_rounds", "grey_openai_parse_max_retry_rounds",
            "rec_max_retry_rounds", "aud_max_retry_rounds", "parse_max_retry_rounds",
            "grey_rec_max_retry_rounds", "grey_aud_max_retry_rounds", "grey_parse_max_retry_rounds",
            "max_retry_rounds",
        ):
            if hasattr(cfg, rf) and getattr(cfg, rf, None) is not None:
                setattr(cfg, rf, clamp_retry_rounds(getattr(cfg, rf)))

        from app.models import clamp_max_tokens, clamp_temperature, clamp_image_detail
        for mf in (
            "openai_rec_max_tokens", "openai_aud_max_tokens", "openai_parse_max_tokens",
            "grey_openai_rec_max_tokens", "grey_openai_aud_max_tokens", "grey_openai_parse_max_tokens"
        ):
            if hasattr(cfg, mf) and getattr(cfg, mf, None) is not None:
                setattr(cfg, mf, clamp_max_tokens(getattr(cfg, mf)))

        for tf in (
            "openai_rec_temperature", "openai_aud_temperature", "openai_parse_temperature",
            "grey_openai_rec_temperature", "grey_openai_aud_temperature", "grey_openai_parse_temperature"
        ):
            if hasattr(cfg, tf) and getattr(cfg, tf, None) is not None:
                setattr(cfg, tf, clamp_temperature(getattr(cfg, tf)))

        for df in ("openai_rec_image_detail", "grey_openai_rec_image_detail"):
            if hasattr(cfg, df) and getattr(cfg, df, None) is not None:
                setattr(cfg, df, clamp_image_detail(getattr(cfg, df)))
        # 写入前确保不残留 opencode / codebuddy 引擎及模型
        for eng_field, mod_field, def_model in (
            ("recognition_engine", "recognition_model", _REC_DEFAULT_MODEL),
            ("audit_engine", "audit_model", _SF_DEFAULT_AUD_MODEL),
            ("parse_llm_engine", "parse_llm_model", _SF_DEFAULT_REC_MODEL),
            ("grey_recognition_engine", "grey_recognition_model", _REC_DEFAULT_MODEL),
            ("grey_audit_engine", "grey_audit_model", _SF_DEFAULT_AUD_MODEL),
            ("grey_parse_llm_engine", "grey_parse_llm_model", _SF_DEFAULT_REC_MODEL),
        ):
            new_eng, new_model = _normalize_legacy_cli_leg(
                getattr(cfg, eng_field, None), getattr(cfg, mod_field, None), def_model)
            if new_eng is not None:
                setattr(cfg, eng_field, new_eng)
            if new_model is not None:
                setattr(cfg, mod_field, new_model)

        row = s.get(_AppSettingRow, "engine_config")
        if row is None:
            row = _AppSettingRow(key="engine_config")
            s.add(row)
        from app import secret_store
        row.value = json.dumps(secret_store.encrypt_config_dict(cfg.model_dump()),
                               ensure_ascii=False)
        s.commit()
    finally:
        s.close()


# -------------------------------------------------------------
# 引擎配置版本历史（本轮新增）
# -------------------------------------------------------------
_HISTORY_ACTIONS = ("save", "promote", "rollback", "reset")
_HISTORY_SECRET_KEYS = (
    "openai_rec_api_key", "openai_aud_api_key", "openai_parse_api_key",
    "grey_openai_rec_api_key", "grey_openai_aud_api_key", "grey_openai_parse_api_key",
)


def _mask_engine_snapshot(snapshot):
    """快照落库前的 api_key 脱敏：本表是新增持久面，不把真实密钥多写一份到磁盘。"""
    if not isinstance(snapshot, dict):
        return {}
    from app.services.security_guard import mask_secret_key
    out = dict(snapshot)
    for key in _HISTORY_SECRET_KEYS:
        if key in out and isinstance(out[key], str):
            out[key] = mask_secret_key(out[key])
    return out


def append_engine_config_history(version, actor, action, snapshot, changed_keys):
    """追加一条引擎配置版本历史（每次写操作一条，不可变、不修改历史行）。

    action 取值收敛到 `_HISTORY_ACTIONS`：未知值原样落库会污染历史口径（前端按动作
    渲染），故这里对白名单外的值记 warning 并仍落库（不静默改写真实发生过的动作）。
    """
    if str(action) not in _HISTORY_ACTIONS:
        logger.warning("engine_config_history 收到未知 action=%r（白名单 %s）",
                       action, _HISTORY_ACTIONS)
    s = get_session()
    try:
        row = _EngineConfigHistoryRow(
            version=int(version or 1),
            actor=str(actor or "unknown"),
            action=str(action or ""),
            created_at=now_iso(),
            snapshot=json.dumps(_mask_engine_snapshot(snapshot), ensure_ascii=False),
            changed_keys=json.dumps(list(changed_keys or []), ensure_ascii=False),
        )
        s.add(row)
        s.commit()
        return row.id
    finally:
        s.close()


def list_engine_config_history(page=1, page_size=20):
    """分页读取版本历史摘要（version 倒序）：{items, total, page, page_size}。

    page_size 上限 100（规格 §4.4）；只回传 {version, actor, action, created_at,
    changed_keys} 摘要，**绝不回传 snapshot**（snapshot 仅落库，无 HTTP 出口）。
    """
    try:
        page = max(1, int(page or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(page_size or 20)
    except (TypeError, ValueError):
        page_size = 20
    page_size = max(1, min(100, page_size))

    s = get_session()
    try:
        q = s.query(_EngineConfigHistoryRow)
        total = int(q.count())
        rows = (q.order_by(_EngineConfigHistoryRow.version.desc(),
                           _EngineConfigHistoryRow.id.desc())
                .offset((page - 1) * page_size).limit(page_size).all())
        items = []
        for r in rows:
            try:
                keys = json.loads(r.changed_keys or "[]")
            except Exception:
                keys = []
            items.append({
                "version": int(r.version or 1),
                "actor": r.actor or "",
                "action": r.action or "",
                "created_at": r.created_at or "",
                "changed_keys": keys if isinstance(keys, list) else [],
            })
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        s.close()


def _is_placeholder_key(key) -> bool:
    k = str(key or "").strip()
    if not k:
        return True
    return "YOUR_" in k.upper()


def _engine_leg_uncallable(base, key, engine) -> bool:
    """某腿是否缺到无法调用：OpenAI 兼容通道下 base 或 key 为空/占位符。

    非 openai 引擎走原生 SDK 通道，不适用本判据（且启动装配已把遗留 CLI 引擎归一为 openai）。
    """
    eng = str(getattr(engine, "value", engine) or "").lower()
    if eng != "openai":
        return False
    return (not str(base or "").strip()) or _is_placeholder_key(key)


def _fill_manual_engine_config_gaps(cfg, log, *args, **kwargs) -> bool:
    """manual 模式下不再自动注入特定供应商密钥，保持 manual 配置。"""
    return False


def _normalize_call_timeout(cfg, log) -> bool:
    from app.models import clamp_call_timeout, MAX_CALL_TIMEOUT_SECONDS
    changed = False
    raw = getattr(cfg, "call_timeout_seconds", None)
    fixed = clamp_call_timeout(raw)
    if fixed != raw:
        cfg.call_timeout_seconds = fixed
        log.warning("[engine-env] 存量超时值 %s 已归一为 %s（硬上限）；"
                    "实际生效超时由 llm._resolve_timeout 钳制，填更大值不会延长等待",
                    raw, MAX_CALL_TIMEOUT_SECONDS)
        changed = True
    for tf in (
        "rec_timeout_seconds", "aud_timeout_seconds", "parse_timeout_seconds",
        "grey_rec_timeout_seconds", "grey_aud_timeout_seconds", "grey_parse_timeout_seconds"
    ):
        v = getattr(cfg, tf, None)
        fv = clamp_call_timeout(v)
        if fv != v:
            setattr(cfg, tf, fv)
            log.warning("[engine-env] 存量超时值 %s=%s 已归一为 %s（硬上限）",
                        tf, v, MAX_CALL_TIMEOUT_SECONDS)
            changed = True
    from app.models import clamp_retry_rounds
    for rf in (
        "openai_rec_max_retry_rounds", "openai_aud_max_retry_rounds", "openai_parse_max_retry_rounds",
        "grey_openai_rec_max_retry_rounds", "grey_openai_aud_max_retry_rounds", "grey_openai_parse_max_retry_rounds",
        "rec_max_retry_rounds", "aud_max_retry_rounds", "parse_max_retry_rounds",
        "grey_rec_max_retry_rounds", "grey_aud_max_retry_rounds", "grey_parse_max_retry_rounds",
        "max_retry_rounds",
    ):
        v = getattr(cfg, rf, None)
        if v is not None:
            fv = clamp_retry_rounds(v)
            if fv != v:
                setattr(cfg, rf, fv)
                changed = True
    return changed


def hydrate_engine_config_from_env():
    """启动装配：从通用环境变量（OPENAI_REC_*、OPENAI_AUD_*、OPENAI_PARSE_*）装配。

    彻底去除 vendor-specific 交叉降级与替补逻辑。
    """
    import logging
    log = logging.getLogger("startup")
    try:
        from dotenv import load_dotenv
        from app.models import EngineKind
        load_dotenv()
        cfg = get_engine_config()
        timeout_fixed = _normalize_call_timeout(cfg, log)

        def _clean_base(raw: str, fallback: str = "") -> str:
            b = (raw or fallback).strip()
            if b.endswith("/chat/completions"):
                b = b[:-len("/chat/completions")]
            return b

        rec_key = (os.environ.get("OPENAI_REC_API_KEY")
                   or os.environ.get("OPENAI_API_KEY") or "").strip()
        rec_base = _clean_base(os.environ.get("OPENAI_REC_BASE_URL")
                               or os.environ.get("OPENAI_BASE_URL", ""))
        rec_model = (os.environ.get("OPENAI_REC_MODEL")
                     or os.environ.get("OPENAI_MODEL", "")).strip()

        aud_key = (os.environ.get("OPENAI_AUD_API_KEY")
                   or os.environ.get("OPENAI_API_KEY") or "").strip()
        aud_base = _clean_base(os.environ.get("OPENAI_AUD_BASE_URL")
                               or os.environ.get("OPENAI_BASE_URL", ""))
        aud_model = (os.environ.get("OPENAI_AUD_MODEL")
                     or os.environ.get("OPENAI_MODEL", "")).strip()

        parse_key = (os.environ.get("OPENAI_PARSE_API_KEY")
                     or os.environ.get("OPENAI_API_KEY") or "").strip()
        parse_base = _clean_base(os.environ.get("OPENAI_PARSE_BASE_URL")
                                 or os.environ.get("OPENAI_BASE_URL", ""))
        parse_model = (os.environ.get("OPENAI_PARSE_MODEL")
                       or os.environ.get("OPENAI_MODEL", "")).strip()

        # manual 模式时优先保持手工配置，仅当超时归一时写回
        if str(getattr(cfg, "config_source", "auto") or "auto") == "manual":
            if timeout_fixed:
                set_engine_config(cfg)
            log.info("[engine-env] 检测到管理台手工配置（config_source=manual），跳过 .env 覆盖")
            return

        changed = False
        if rec_key or rec_base or rec_model:
            cfg.recognition_engine = EngineKind.OPENAI
            if rec_base:
                cfg.openai_rec_base_url = rec_base
            if rec_key:
                cfg.openai_rec_api_key = rec_key
            if rec_model:
                cfg.openai_rec_model = rec_model
                cfg.recognition_model = rec_model
            changed = True

        if aud_key or aud_base or aud_model:
            cfg.audit_engine = EngineKind.OPENAI
            if aud_base:
                cfg.openai_aud_base_url = aud_base
            if aud_key:
                cfg.openai_aud_api_key = aud_key
            if aud_model:
                cfg.openai_aud_model = aud_model
                cfg.audit_model = aud_model
            changed = True

        if parse_key or parse_base or parse_model:
            cfg.parse_llm_engine = EngineKind.OPENAI
            if parse_base:
                cfg.openai_parse_base_url = parse_base
            if parse_key:
                cfg.openai_parse_api_key = parse_key
            if parse_model:
                cfg.openai_parse_model = parse_model
                cfg.parse_llm_model = parse_model
            changed = True

        _normalize_legacy_cli_engines(cfg, log, rec_base=cfg.openai_rec_base_url,
                                      rec_key=cfg.openai_rec_api_key,
                                      rec_model=cfg.openai_rec_model,
                                      aud_base=cfg.openai_aud_base_url,
                                      aud_key=cfg.openai_aud_api_key)
        _align_grey_recognition_leg(cfg, cfg.openai_rec_base_url, cfg.openai_rec_api_key,
                                    cfg.openai_rec_model, log)
        if changed or timeout_fixed:
            set_engine_config(cfg)
        log.info(f"[engine-env] 环境变量水合完成：识别 {cfg.openai_rec_base_url} / {cfg.openai_rec_model}；审核 {cfg.openai_aud_base_url} / {cfg.openai_aud_model}")
    except Exception as e:
        log.warning(f"[engine-env] 启动密钥水合失败（不影响服务）: {e}")


# 默认模型常量：
# - _REC_DEFAULT_MODEL：识别腿（含灰测识别腿）默认模型，走 DashScope 通道（用户决策 2026-09-15）。
#   单一事实源取自 app.models，避免字面量重复漂移。
# - _SF_DEFAULT_REC_MODEL：SiliconFlow 通道的识别/解析默认模型。
#   识别腿在 DashScope 密钥缺失时降级用它；解析腿的 base 仍属 SF 通道，恒用此值。
# - _SF_DEFAULT_AUD_MODEL：审核腿与灰测审核腿默认模型（与识别腿跨厂商异构）。
# 注意 Qwen2.5-VL-7B-Instruct 已下架 SiliconFlow 目录（2026-09 实测），禁止再作为默认。
from app.models import DASHSCOPE_DEFAULT_REC_MODEL as _REC_DEFAULT_MODEL  # noqa: E402
_SF_DEFAULT_REC_MODEL = "Qwen/Qwen3-VL-32B-Instruct"
_SF_DEFAULT_AUD_MODEL = "zai-org/GLM-4.5V"
_SF_DEFAULT_BASE = "https://api.siliconflow.cn/v1"


def _align_grey_recognition_leg(cfg, rec_base: str, rec_key: str, rec_model: str, log) -> bool:
    """灰测识别腿 provider 对齐（T12）：让灰测组与常规组在同一供应商下比模型/参数。

    why：灰测/分组的语义是「控制 provider 不变，只比较模型或 prompt」，否则一次实验里
    同时混入了供应商差异（价格档位、限流、图像预处理、输出风格都不同），A/B 结论无法
    归因到被测模型。此前常规腿走 DashScope、灰测腿走 SiliconFlow，比较基准不纯。

    三条守护，确保不破坏既有行为、也不堵死管理员的自定义路径：
      1. 仅当 `grey_enabled=True` 时才执行 —— 灰测关闭时本函数是纯 no-op，配置逐字段不变
         （当前线上状态即 grey_enabled=False）。
      2. 仅在 auto 装配路径被调用 —— 管理台任何保存都会置 config_source=manual，hydrate
         整体跳过，故「管理员想让灰测腿用别家 provider」仍可通过管理台配置并跨重启保留。
      3. 只对齐「仍是 SiliconFlow 历史默认」的灰测腿（base 为空或 SF 默认、模型为空或 SF
         默认）。已明确改成其它供应商的灰测腿视为刻意配置，保持不动，只记 warning 提示
         比较基准混入了 provider 变量。

    返回是否发生了对齐（True 表示配置需写回）。
    """
    if not getattr(cfg, "grey_enabled", False):
        return False
    from app.models import EngineKind
    g_base = str(getattr(cfg, "grey_openai_rec_base_url", "") or "").strip()
    g_model = str(getattr(cfg, "grey_openai_rec_model", "") or "").strip()
    base_is_legacy_default = (not g_base) or g_base.rstrip("/") == _SF_DEFAULT_BASE
    model_is_legacy_default = (not g_model) or g_model == _SF_DEFAULT_REC_MODEL
    if base_is_legacy_default and model_is_legacy_default:
        cfg.grey_openai_rec_base_url = rec_base or cfg.grey_openai_rec_base_url
        cfg.grey_openai_rec_api_key = rec_key or cfg.grey_openai_rec_api_key
        # 模型一并跟随：base 换成 DashScope 后仍留 SF 模型名（Qwen/... 组织前缀）会 404/400，
        # 比不对齐更糟。管理员随后在管理台把灰测模型改成候选模型即可形成有效对比。
        cfg.grey_openai_rec_model = rec_model or _SF_DEFAULT_REC_MODEL
        cfg.grey_recognition_model = cfg.grey_openai_rec_model
        cfg.grey_recognition_engine = EngineKind.OPENAI
        log.info("[engine-env] 灰测识别腿已对齐常规识别腿 provider: %s / %s",
                 cfg.grey_openai_rec_base_url, cfg.grey_openai_rec_model)
        return True
    if g_base and rec_base and g_base.rstrip("/") != rec_base.rstrip("/"):
        log.warning(
            "[engine-env] 灰测识别腿 provider（%s）与常规识别腿（%s）不一致，"
            "A/B 比较基准混入了供应商变量，结论无法单独归因到模型；"
            "如非刻意配置，请在管理台把灰测识别腿的 Base URL 与常规腿对齐。",
            g_base, rec_base,
        )
    return False


def _normalize_legacy_cli_engines(cfg, log, rec_base: str = "", rec_key: str = "",
                                  rec_model: str = "", aud_base: str = "", aud_key: str = "") -> None:
    """归一历史遗留的 opencode / codebuddy CLI 引擎配置。幂等；业务开关一律不动。

    引擎值 opencode/codebuddy → openai；模型 opencode/* / codebuddy/* / minimax-m3-pay → 标准模型；
    灰测识别腿沿用识别腿 provider（rec_base/rec_key/rec_model，缺失时回落 SF 默认），
    灰测审核腿与解析腿沿用审核腿 provider（aud_base/aud_key）。
    """
    # 本模块未在模块级导入 EngineKind（其余函数均为函数内惰性导入），此处必须显式导入：
    # 漏掉会抛 NameError，被 hydrate 的 except 静默吞掉，导致整套归一逻辑形同虚设。
    from app.models import EngineKind, is_legacy_cli_engine, is_legacy_cli_model
    changed = []

    def _enum_str(v):
        # EngineKind 等枚举字段的字符串取值（str(enum) 是 "EngineKind.X" 形态，不能直接比较）
        return str(getattr(v, "value", v) or "").lower()

    def _is_cli(eng, mod):
        # 判定统一走 models 的单一实现（原先在这里抄了一份 opencode/codebuddy/minimax 字面量）
        return is_legacy_cli_engine(eng) or is_legacy_cli_model(mod)

    # 主识别腿
    if _is_cli(getattr(cfg, "recognition_engine", ""), getattr(cfg, "recognition_model", "")):
        cfg.recognition_engine = EngineKind.OPENAI
        m = str(cfg.recognition_model or "")
        if is_legacy_cli_model(m) or not m:
            cfg.recognition_model = _SF_DEFAULT_REC_MODEL
        changed.append("rec")

    # 主审核腿
    if _is_cli(getattr(cfg, "audit_engine", ""), getattr(cfg, "audit_model", "")):
        cfg.audit_engine = EngineKind.OPENAI
        m = str(cfg.audit_model or "")
        if is_legacy_cli_model(m) or not m:
            cfg.audit_model = _SF_DEFAULT_AUD_MODEL
        changed.append("aud")

    # 解析腿
    if _is_cli(getattr(cfg, "parse_llm_engine", ""), getattr(cfg, "parse_llm_model", "")):
        cfg.parse_llm_engine = EngineKind.OPENAI
        m = str(cfg.parse_llm_model or "")
        if is_legacy_cli_model(m) or not m:
            cfg.parse_llm_model = _SF_DEFAULT_REC_MODEL
        changed.append("parse")

    # 灰测识别腿
    if _is_cli(getattr(cfg, "grey_recognition_engine", ""), getattr(cfg, "grey_recognition_model", "")):
        cfg.grey_recognition_engine = EngineKind.OPENAI
        m = str(cfg.grey_recognition_model or "")
        if is_legacy_cli_model(m) or not m:
            # 跟随识别腿模型（否则会出现 DashScope base + SF 模型名的错配）
            cfg.grey_recognition_model = rec_model or _SF_DEFAULT_REC_MODEL
        if not getattr(cfg, "grey_openai_rec_base_url", "") and rec_base:
            cfg.grey_openai_rec_base_url = rec_base
        if not getattr(cfg, "grey_openai_rec_api_key", "") and rec_key:
            cfg.grey_openai_rec_api_key = rec_key
        if not getattr(cfg, "grey_openai_rec_model", ""):
            cfg.grey_openai_rec_model = rec_model or _SF_DEFAULT_REC_MODEL
        changed.append("grey_rec")

    # 灰测审核腿
    if _is_cli(getattr(cfg, "grey_audit_engine", ""), getattr(cfg, "grey_audit_model", "")):
        cfg.grey_audit_engine = EngineKind.OPENAI
        m = str(cfg.grey_audit_model or "")
        if is_legacy_cli_model(m) or not m:
            cfg.grey_audit_model = _SF_DEFAULT_AUD_MODEL
        if not getattr(cfg, "grey_openai_aud_base_url", "") and aud_base:
            cfg.grey_openai_aud_base_url = aud_base
        if not getattr(cfg, "grey_openai_aud_api_key", "") and aud_key:
            cfg.grey_openai_aud_api_key = aud_key
        if not getattr(cfg, "grey_openai_aud_model", ""):
            cfg.grey_openai_aud_model = _SF_DEFAULT_AUD_MODEL
        changed.append("grey_aud")

    # 灰测解析腿
    if _is_cli(getattr(cfg, "grey_parse_llm_engine", ""), getattr(cfg, "grey_parse_llm_model", "")):
        cfg.grey_parse_llm_engine = EngineKind.OPENAI
        m = str(cfg.grey_parse_llm_model or "")
        if is_legacy_cli_model(m) or not m:
            cfg.grey_parse_llm_model = _SF_DEFAULT_REC_MODEL
        if not getattr(cfg, "grey_openai_parse_base_url", "") and aud_base:
            cfg.grey_openai_parse_base_url = aud_base
        if not getattr(cfg, "grey_openai_parse_api_key", "") and aud_key:
            cfg.grey_openai_parse_api_key = aud_key
        if not getattr(cfg, "grey_openai_parse_model", ""):
            cfg.grey_openai_parse_model = _SF_DEFAULT_REC_MODEL
        changed.append("grey_parse")

    if changed:
        log.info("[engine-env] 已归一遗留 opencode/codebuddy CLI 配置 → OpenAI 兼容通道: %s", ",".join(changed))


def _openai_gateway_healthy(api_key: str, base_url: str = "") -> bool:
    """轻量健康检查：GET {base_url}/models，200 即健康；异常/超时返回 False。

    base_url 留空时回落 OPENAI/SILICONFLOW base（历史调用兼容）；显式传入可避免
    「探 SF 端点却用于判断 DashScope」这类误判。

    P2-4：外呼前一律经统一 SSRF 网关 validate_safe_external_url 校验；内网/元数据
    形态的 base_url 不通过校验时直接判为「不健康」（返回 False）并 [WARN] 留痕，
    既不静默也不抛穿启动流程。
    """
    import urllib.request
    from app.services.security_guard import validate_safe_external_url
    base = (base_url or os.environ.get("SILICONFLOW_BASE_URL")
            or os.environ.get("OPENAI_BASE_URL")
            or "https://api.siliconflow.cn/v1").strip()
    if base.endswith("/chat/completions"):
        base = base[:-len("/chat/completions")]
    is_safe, reason = validate_safe_external_url(base)
    if not is_safe:
        logger.warning("[WARN] 网关健康检查跳过外呼：base_url 未通过 SSRF 安全校验"
                       "（%s）：%s", base, reason)
        return False
    try:
        req = urllib.request.Request(base.rstrip("/") + "/models",
                                     headers={"Authorization": "Bearer " + api_key})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


# -------------------------------------------------------------
# LLM / VLM 调用全息日志记录与查询接口 (Phase 2)
# -------------------------------------------------------------
def insert_llm_call_log(
    receipt_id=None,
    tenant_id="default",
    trace_id="",
    leg="extract",
    attempt=1,
    engine="",
    model="",
    prompt_messages_json="[]",
    raw_output="",
    prompt_tokens=0,
    completion_tokens=0,
    cached_tokens=0,
    total_tokens=0,
    cost_hkd=0.0,
    cost_estimated=0,
    cost_estimated_reason="",
    elapsed_ms=0.0,
    status="success",
    error_msg="",
    error_category="",
    created_at="",
    adapted_params="",
):
    """写一条 LLM/VLM 调用日志（全链路观测）。异常软失败不阻断业务。"""
    s = get_session()
    try:
        if isinstance(prompt_messages_json, (dict, list)):
            p_json = json.dumps(prompt_messages_json, ensure_ascii=False)
        else:
            p_json = str(prompt_messages_json or "[]")

        row = _LlmCallLogRow(
            receipt_id=int(receipt_id) if receipt_id is not None else None,
            tenant_id=str(tenant_id or "default"),
            trace_id=str(trace_id or ""),
            leg=str(leg or "extract"),
            attempt=int(attempt or 1),
            engine=str(engine or ""),
            model=str(model or ""),
            prompt_messages_json=p_json,
            raw_output=str(raw_output or ""),
            prompt_tokens=int(prompt_tokens or 0),
            completion_tokens=int(completion_tokens or 0),
            cached_tokens=int(cached_tokens or 0),
            total_tokens=int(total_tokens or 0),
            cost_hkd=float(cost_hkd or 0.0),
            cost_estimated=int(cost_estimated or 0),
            cost_estimated_reason=str(cost_estimated_reason or ""),
            elapsed_ms=float(elapsed_ms or 0.0),
            status=str(status or "success"),
            error_msg=str(error_msg or ""),
            error_category=str(error_category or ""),
            adapted_params=str(adapted_params or ""),
            created_at=str(created_at or now_iso()),
        )
        s.add(row)
        s.commit()
        return row.id
    except Exception as e:
        logger.warning(f"插入 LLM 调用日志失败 (receipt_id={receipt_id}, leg={leg}): {e}")
        try:
            s.rollback()
        except Exception:
            pass
        return None
    finally:
        s.close()


def list_llm_call_logs_by_receipt(receipt_id, tenant_id=None, s=None):
    """按 receipt_id 查询 LLM 调用日志列表。"""
    should_close = False
    if s is None:
        s = get_session()
        should_close = True
    try:
        q = s.query(_LlmCallLogRow).filter(_LlmCallLogRow.receipt_id == int(receipt_id))
        if tenant_id and tenant_id != "all":
            q = q.filter(_LlmCallLogRow.tenant_id == str(tenant_id))
        rows = q.order_by(_LlmCallLogRow.id.asc()).all()
        results = []
        for r in rows:
            results.append({
                "id": r.id,
                "receipt_id": r.receipt_id,
                "tenant_id": r.tenant_id,
                "trace_id": r.trace_id,
                "leg": r.leg,
                "attempt": r.attempt,
                "engine": r.engine,
                "model": r.model,
                "prompt_messages_json": r.prompt_messages_json,
                "raw_output": r.raw_output,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cached_tokens": r.cached_tokens,
                "total_tokens": r.total_tokens,
                "cost_hkd": r.cost_hkd,
                "cost_estimated": r.cost_estimated,
                "cost_estimated_reason": r.cost_estimated_reason,
                "elapsed_ms": r.elapsed_ms,
                "status": r.status,
                "error_msg": r.error_msg,
                "error_category": r.error_category,
                "adapted_params": _parse_json_safe(getattr(r, "adapted_params", None)) or [],
                "created_at": r.created_at,
            })
        return results
    finally:
        if should_close:
            s.close()


def get_llm_call_log_by_id(log_id, s=None):
    """按 id 查询单条 LLM 调用日志。"""
    should_close = False
    if s is None:
        s = get_session()
        should_close = True
    try:
        r = s.query(_LlmCallLogRow).filter(_LlmCallLogRow.id == int(log_id)).first()
        if not r:
            return None
        return {
            "id": r.id,
            "receipt_id": r.receipt_id,
            "tenant_id": r.tenant_id,
            "trace_id": r.trace_id,
            "leg": r.leg,
            "attempt": r.attempt,
            "engine": r.engine,
            "model": r.model,
            "prompt_messages_json": r.prompt_messages_json,
            "raw_output": r.raw_output,
            "prompt_tokens": r.prompt_tokens,
            "completion_tokens": r.completion_tokens,
            "cached_tokens": r.cached_tokens,
            "total_tokens": r.total_tokens,
            "cost_hkd": r.cost_hkd,
            "cost_estimated": r.cost_estimated,
            "cost_estimated_reason": r.cost_estimated_reason,
            "elapsed_ms": r.elapsed_ms,
            "status": r.status,
            "error_msg": r.error_msg,
            "error_category": r.error_category,
            "adapted_params": _parse_json_safe(getattr(r, "adapted_params", None)) or [],
            "created_at": r.created_at,
        }
    finally:
        if should_close:
            s.close()


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


def _event_tenant_id(tenant_id, receipt_id):
    """埋点租户键缺省链：显式 tenant_id → 单据回填 → default（write/log 两路共用）。"""
    t_id = (tenant_id or "").strip()
    if not t_id and receipt_id is not None:
        try:
            r = get_receipt_row(receipt_id)
            if r and getattr(r, "tenant_id", None):
                t_id = r.tenant_id
        except Exception:
            pass
    return t_id or "default"


def write_user_event(event_type, account_id="", session_id="", receipt_id=None,
                     properties=None, grp=None, tenant_id=None):
    """写一条用户行为埋点（漏斗用）。"""
    t_id = _event_tenant_id(tenant_id, receipt_id)
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


def coerce_int(value, default: int) -> int:
    """整型配置项归一：显式判空，尊重 0。

    why（P2 + 本轮 S2）：本仓连续出现 `int(x or N)` 把合法 0 静默改成 N 的缺陷
    （target_percent=0 = 全部走对照组；min_sample=0 = 不做样本量门槛）。0 是合法
    的业务边界值，不能用 `or` 兜底；这里只对「None / 空串 / 纯空白」回落 default，
    0 原样保留。

    原 coerce_target_percent 的语义与此完全相同，故泛化为通用 helper，避免为
    min_sample 再抄一份（本仓高发模式是「修 A 漏 B / 同名重复实现」）。

    非法值的处理保持既有"不炸"语义：
      - 非数字（如 "abc"）→ 回落 default（原实现会抛 ValueError，调用方炸掉）；
      - 其它非法输入不在此处做范围钳制（保持原实现允许越界的既有行为），
        范围校验由入口负责（如 AdminExpCreateBody 的 Field(ge=...)）。
    """
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


@_guard_job_db_write()
def log_ai_decision(
    receipt_id=None, supplier_id=None, experiment_id=None, grp="control",
    engine="", model="", use_grey=0, decision_type="extract",
    field_path="", ai_value=None, user_value=None, adopted=None,
    confidence=None, is_hallucination=None, extra=None,
):
    """写一条 AI 决策日志。采纳/用户值/幻觉标记可留 NULL，approve 时回填。

    单据若被分配进某个 A/B 实验，实验 ID 与分组以实验分配表为准（调用方没传就自动补），
    这样识别各轮、审核、approve 行都带同一个实验 ID 和分组。
    """
    s = get_session()
    try:
        if receipt_id:
            _assign_q = s.query(_ExperimentAssignRow).filter(
                _ExperimentAssignRow.receipt_id == int(receipt_id))
            if experiment_id:
                _assign_q = _assign_q.filter(_ExperimentAssignRow.experiment_id == int(experiment_id))
            _assign = _assign_q.order_by(_ExperimentAssignRow.assigned_at.desc()).first()
            if _assign is not None:
                experiment_id = _assign.experiment_id
                grp = _assign.grp
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
                # 成本可信度：0=按真实模型单价 x 真实 token 算出的实测值；
                # 1=估算值（拿不到 token / 成本链路异常 / 走了兜底单价），前端据此标注
                "cost_estimated": int(extra_dict.get("cost_estimated") or 0),
                "cost_estimated_reason": extra_dict.get("cost_estimated_reason", ""),
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


@_guard_job_db_write()
def log_user_event(account_id="", session_id="", event_type="",
                   receipt_id=None, properties=None, grp=None, tenant_id=None):
    """前端埋点事件。"""
    t_id = _event_tenant_id(tenant_id, receipt_id)
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
            target_percent=coerce_int(target_percent, 50),
            target_supplier_ids=_safe_json(target_supplier_ids or []),
            min_sample=coerce_int(min_sample, 100),
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


def get_running_experiment():
    """获取当前处于运行状态的 A/B 科学实验（若有）。"""
    s = get_session()
    try:
        row = s.query(_ExperimentRow).filter(_ExperimentRow.status == "running").order_by(_ExperimentRow.id.desc()).first()
        return _exp_to_dict(row) if row else None
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


@_guard_job_db_write()
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

    # 分组以实验分配表为准；样本单位是单据（一张单有多轮识别加审核、approve 多条决策行，
    # 逐行统计会让样本不独立、z 检验失真）。没有单据号的行（离线脚本写入）各自算一个样本。
    s2 = get_session()
    try:
        assign_map = {a.receipt_id: str(a.grp) for a in s2.query(_ExperimentAssignRow).filter(
            _ExperimentAssignRow.experiment_id == int(exp_id)).all()}
    finally:
        s2.close()

    units = {}
    for r in rows:
        key = ("r", r.receipt_id) if r.receipt_id else ("row", r.id)
        g = assign_map.get(r.receipt_id) if r.receipt_id else None
        if g is None:
            g = str(r.grp) if r.grp else "control"
        unit = units.setdefault(key, {"grp": g, "rows": []})
        unit["rows"].append(r)

    groups = {"control": [], "treatment": []}
    for unit in units.values():
        if unit["grp"] in groups:
            groups[unit["grp"]].append(unit["rows"])

    def _stats(unit_rows):
        n = len(unit_rows)
        if n == 0:
            return {"n": 0, "n_reviewed": 0, "accuracy": None,
                    "hallucination_rate": None, "edit_rate": None}
        # 已有人工结论（approve 行写了 adopted）的单据才进入准确率等比率的分母
        reviewed = [rs for rs in unit_rows if any(r.adopted is not None for r in rs)]
        nr = len(reviewed)
        adopted = sum(1 for rs in reviewed
                      if all(r.adopted == 1 for r in rs if r.adopted is not None))
        hallu = sum(1 for rs in reviewed if any(r.is_hallucination == 1 for r in rs))
        edited = sum(1 for rs in reviewed
                     if any(r.user_value and r.user_value.strip() not in ("", "{}") for r in rs))
        return {
            "n": n,
            "n_reviewed": nr,
            "accuracy": round(adopted / nr, 6) if nr else None,
            "hallucination_rate": round(hallu / nr, 6) if nr else None,
            "edit_rate": round(edited / nr, 6) if nr else None,
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
        # 标准正态 CDF：math.erf 精确实现（旧 Abramowitz 近似把 erf 系数配了 e^(-x²/2) 指数，
        # 导致 Φ 失真、p 值可 >1，如 z=1.491 时算出 1.1064）
        def _phi(x):
            return 0.5 * (1.0 + _math.erf(x / _math.sqrt(2.0)))
        # 双侧 p 按 |z| 计算：treatment 低于 control（z<0）同样成立
        pval = 2.0 * (1.0 - _phi(abs(z)))
        return {"z": round(z, 6), "p_value": round(pval, 8),
                "effect_size_pp": round((p2 - p1) * 100, 4)}

    z_acc = _z_test(c["accuracy"], t["accuracy"], c["n_reviewed"], t["n_reviewed"])
    z_hall = _z_test(c["hallucination_rate"], t["hallucination_rate"],
                     c["n_reviewed"], t["n_reviewed"])
    z_edit = _z_test(c["edit_rate"], t["edit_rate"], c["n_reviewed"], t["n_reviewed"])

    return {
        "experiment_id": int(exp_id),
        "control": c,
        "treatment": t,
        "tests": {
            "accuracy": z_acc,
            "hallucination_rate": z_hall,
            "edit_rate": z_edit,
        },
        "low_confidence": min(c["n_reviewed"], t["n_reviewed"]) < 30,
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

    low_confidence = min(c["n"], t["n"]) < coerce_int(min_sample, 30)
    if low_confidence:
        diff = {k: (None if v is not None else None) for k, v in diff.items()}

    return {
        "period_start": start_ts, "period_end": end_ts,
        "control": c, "treatment": t, "diff_pp": diff,
        "low_confidence": low_confidence,
        "min_sample": coerce_int(min_sample, 30),
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
        st["low_confidence"] = st["n"] < coerce_int(min_sample, 30)
        breakdown[k] = st

    all_rows = sum(buckets.values(), [])
    total = _stats(all_rows)
    total["low_confidence"] = total["n"] < coerce_int(min_sample, 30)
    return {
        "period_start": start_ts, "period_end": end_ts,
        "granularity": granularity, "granularity_id": granularity_id,
        "grp": grp,
        "total": total,
        "breakdown": breakdown,
        "min_sample": coerce_int(min_sample, 30),
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


@_guard_job_db_write(blocked_return=(None, False))
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


# -----------------------------------------------------------------
# WS2 收尾：存量餐品版本快照回填（模块加载末尾执行一次）。
# 幂等：仅补「零快照」餐品，二次 import 零动作；失败仅告警不阻断。
# -----------------------------------------------------------------
backfill_dish_recipe_snapshots()


# -----------------------------------------------------------------
# W-SEC-6：安全事件审计与统计（持久化到 security_events 表）
# -----------------------------------------------------------------
def record_security_event(event_type: str, severity: str, source_module: str,
                          details: dict = None, tenant_id: str = "default",
                          ip_address: str = "", user_email: str = ""):
    """写入一条安全事件（持久化到 security_events 表）。"""
    if _SecurityEventRow is None:
        return None
    tid = str(tenant_id or "default").strip() or "default"
    eid = f"sec_{uuid.uuid4().hex}"
    ts = now_iso()
    det_json = _safe_json(details or {})
    s = get_session()
    try:
        with bypass_tenant_isolation():
            row = _SecurityEventRow(
                event_id=eid,
                tenant_id=tid,
                event_type=str(event_type),
                severity=str(severity).upper(),
                source_module=str(source_module),
                details_json=det_json,
                ip_address=str(ip_address or ""),
                user_email=str(user_email or ""),
                created_at=ts,
            )
            s.add(row)
            s.commit()
            return {
                "event_id": eid,
                "tenant_id": tid,
                "event_type": str(event_type),
                "severity": str(severity).upper(),
                "source_module": str(source_module),
                "details": details or {},
                "ip_address": str(ip_address or ""),
                "user_email": str(user_email or ""),
                "created_at": ts,
            }
    except Exception as e:
        logger.warning(f"[security_event] record failed: {e}")
        return None
    finally:
        s.close()


def list_security_events(tenant_id=None, event_type=None, severity=None,
                         limit=100, offset=0):
    """查询安全事件列表，支持按 tenant_id / event_type / severity 过滤。"""
    if _SecurityEventRow is None:
        return []
    s = get_session()
    try:
        q = scoped(s.query(_SecurityEventRow), _SecurityEventRow, tenant_id)
        if event_type:
            q = q.filter(_SecurityEventRow.event_type == str(event_type))
        if severity:
            q = q.filter(_SecurityEventRow.severity == str(severity).upper())
        rows = q.order_by(_SecurityEventRow.id.desc()).offset(int(offset or 0)).limit(int(limit or 100)).all()
        result = []
        for r in rows:
            result.append({
                "id": r.id,
                "event_id": r.event_id,
                "tenant_id": r.tenant_id,
                "event_type": r.event_type,
                "severity": r.severity,
                "source_module": r.source_module,
                "details": _parse_json(r.details_json),
                "ip_address": r.ip_address,
                "user_email": r.user_email,
                "created_at": r.created_at,
            })
        return result
    finally:
        s.close()


def get_security_stats(tenant_id=None):
    """安全大屏聚合分析：事件总数、按类型聚合、按严重等级聚合、按租户聚合、最近 24 小时高危事件统计。"""
    if _SecurityEventRow is None:
        return {
            "total_events": 0,
            "by_type": {},
            "by_severity": {},
            "by_tenant": {},
            "recent_24h_high_risk": 0,
            "recent_24h_events": []
        }
    s = get_session()
    try:
        q = scoped(s.query(_SecurityEventRow), _SecurityEventRow, tenant_id)
        rows = q.all()
        by_type = defaultdict(int)
        by_severity = defaultdict(int)
        by_tenant = defaultdict(int)
        cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
        recent_24h_high_risk = 0
        recent_24h_events = []

        for r in rows:
            by_type[r.event_type] += 1
            by_severity[r.severity] += 1
            by_tenant[r.tenant_id] += 1
            if r.created_at >= cutoff:
                if r.severity in ("CRITICAL", "HIGH"):
                    recent_24h_high_risk += 1
                    recent_24h_events.append({
                        "event_id": r.event_id,
                        "event_type": r.event_type,
                        "severity": r.severity,
                        "tenant_id": r.tenant_id,
                        "created_at": r.created_at,
                    })

        return {
            "total_events": len(rows),
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
            "by_tenant": dict(by_tenant),
            "recent_24h_high_risk": recent_24h_high_risk,
            "recent_24h_events": recent_24h_events[:20],
        }
    finally:
        s.close()
