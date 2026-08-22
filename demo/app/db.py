# -*- coding: utf-8 -*-
"""轻量持久化：SQLite + SQLAlchemy（精简版，无迁移/多租户）。

数据表：
- receipts（收据主表，含乐观锁 version / 付款字段）
- receipt_items（收据明细）
- inventory（SKU 库存主表）
- inventory_log（进出流水：入库/消耗/损耗/盘点）
- suppliers（供应商）
- departments（部门）
- payments（付款登记）
- vendor_memory / app_settings / skus
"""

import json
import math as _math
import os
import uuid
from collections import defaultdict
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "../receipt_demo.db"))

_engine = None
_SessionLocal = None

_ReceiptRow = _ItemRow = _SkuRow = _StockLogRow = _SupplierRow = None
_DeptRow = _PaymentRow = _VendorMemoryRow = _AppSettingRow = None
_DecisionLogRow = _SnapshotRow = _UserEventRow = _ExperimentRow = _ExperimentAssignRow = None
_ReceiptFeedbackRow = None


def _make_engine():
    global _engine, _SessionLocal
    global _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow, _SupplierRow
    global _DeptRow, _PaymentRow, _VendorMemoryRow, _AppSettingRow
    global _DecisionLogRow, _SnapshotRow, _UserEventRow, _ExperimentRow, _ExperimentAssignRow
    global _ReceiptFeedbackRow

    from sqlalchemy import create_engine, Column, String, Float, Integer, Text, DateTime
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
        created_at = Column(String, default="")
        updated_at = Column(String, default="")
        deleted_at = Column(String, nullable=True)  # 软删除时间戳

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

    class SkuRow(Base):
        __tablename__ = "skus"
        id = Column(Integer, primary_key=True, autoincrement=True)
        name = Column(String, index=True, unique=True)
        category = Column(String, default="")
        base_unit = Column(String, default="")
        min_stock_alert = Column(Float, default=0.0)
        current_stock = Column(Float, default=0.0)
        last_unit_price = Column(Float, default=0.0)
        sku_code = Column(String, default="")
        active = Column(Integer, default=1)

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
        vendor = Column(String, primary_key=True)
        notes = Column(Text, default="")
        sample = Column(Text, default="")

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

    Base.metadata.create_all(_engine)

    # SQLite 迁移：给 receipts 表补 use_grey 列（阶段 1：AI 可见性与信任）
    try:
        from sqlalchemy import text as _sa_text
        with _engine.connect() as _c:
            _c.execute(_sa_text(
                "ALTER TABLE receipts ADD COLUMN use_grey INTEGER DEFAULT 0"
            ))
            _c.commit()
    except Exception:
        pass  # 列已存在则忽略
    # SQLite 迁移：RAG 上下文 + 多币种（P1 治理）
    for _ddl in (
        "ALTER TABLE receipts ADD COLUMN rag_context_json TEXT DEFAULT ''",
        "ALTER TABLE receipts ADD COLUMN currency VARCHAR(8) DEFAULT 'HKD'",
        "ALTER TABLE receipt_items ADD COLUMN is_void INTEGER DEFAULT 0",
        "ALTER TABLE receipt_items ADD COLUMN actual_qty REAL",
    ):
        try:
            from sqlalchemy import text as _sa_text2
            with _engine.connect() as _c:
                _c.execute(_sa_text2(_ddl))
                _c.commit()
        except Exception:
            pass

    _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow = ReceiptRow, ItemRow, SkuRow, StockLogRow
    _SupplierRow, _DeptRow, _PaymentRow = SupplierRow, DeptRow, PaymentRow
    _VendorMemoryRow, _AppSettingRow = VendorMemoryRow, AppSettingRow
    _DecisionLogRow = DecisionLogRow
    _SnapshotRow = SnapshotRow
    _UserEventRow = UserEventRow
    _ExperimentRow = ExperimentRow
    _ExperimentAssignRow = ExperimentAssignRow
    _ReceiptFeedbackRow = ReceiptFeedbackRow
    return Base


_make_engine()


def get_session():
    return _SessionLocal()


def now_iso():
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def new_id():
    return uuid.uuid4().hex[:8]


# -------------------------------------------------------------
# 收据
# -------------------------------------------------------------
def create_receipt(supplier_name="", status="uploaded"):
    s = get_session()
    try:
        row = _ReceiptRow(status=status, supplier_name=supplier_name,
                          created_at=now_iso(), updated_at=now_iso())
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id
    finally:
        s.close()


def get_receipt_row(receipt_id):
    s = get_session()
    try:
        return s.get(_ReceiptRow, int(receipt_id))
    finally:
        s.close()


def update_receipt(receipt_id, **fields):
    """按字段更新收据；version 字段若传入则 +1。返回更新后的 row。"""
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


def set_receipt_items(receipt_id, items):
    """整体替换收据明细（先删后插）。items = list[dict]。

    dict 键为前端契约名（fuzzy_candidates/entity_candidates 等），
    落到 _json 列。
    """
    s = get_session()
    try:
        s.query(_ItemRow).filter(_ItemRow.receipt_id == int(receipt_id)).delete()
        for it in items:
            row = dict(it)
            row.pop("id", None)  # 旧 id 不保留，重新自增
            fuzzy = row.pop("fuzzy_candidates", [])
            entity = row.pop("entity_candidates", [])
            raw_name = row.pop("raw_name", None)
            raw_unit = row.pop("raw_unit", None)
            is_void = 1 if row.pop("is_void", 0) else 0
            actual_qty = row.pop("actual_qty", None)
            s.add(_ItemRow(
                receipt_id=int(receipt_id),
                raw_name=raw_name,
                raw_unit=raw_unit,
                fuzzy_candidates_json=json.dumps(fuzzy, ensure_ascii=False),
                entity_candidates_json=json.dumps(entity, ensure_ascii=False),
                is_void=is_void,
                actual_qty=actual_qty,
                **row,
            ))
        s.commit()
    finally:
        s.close()


def append_audit_log(receipt_id, who, action, field, old, new):
    """向 receipts.audit_logs_json 追加一条审计记录。

    结构：list[{who, action, field, old, new, ts}]。
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
        logs.append({
            "who": who or "unknown",
            "action": action,
            "field": field,
            "old": old,
            "new": new,
            "ts": now_iso(),
        })
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


def list_trash_receipts():
    """返回软删除单据列表（含 deleted_at），按 id 倒序。"""
    _s = get_session()
    try:
        return _s.query(_ReceiptRow).filter(
            _ReceiptRow.deleted_at.isnot(None)
        ).order_by(_ReceiptRow.id.desc()).all()
    finally:
        _s.close()


def list_receipt_rows():
    _s = get_session()
    try:
        return _s.query(_ReceiptRow).filter(
            _ReceiptRow.deleted_at.is_(None)
        ).order_by(_ReceiptRow.id.desc()).all()
    finally:
        _s.close()


def get_receipt_items(receipt_id):
    s = get_session()
    try:
        rows = s.query(_ItemRow).filter(_ItemRow.receipt_id == int(receipt_id)).all()
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
    }


# -------------------------------------------------------------
# SKU 库存
# -------------------------------------------------------------
def list_skus(include_inactive=False):
    s = get_session()
    try:
        q = s.query(_SkuRow)
        if not include_inactive:
            q = q.filter(_SkuRow.active == 1)
        return q.all()
    finally:
        s.close()


def get_sku(sku_id):
    s = get_session()
    try:
        return s.get(_SkuRow, int(sku_id))
    finally:
        s.close()


def create_sku(name, category="", base_unit="", min_stock_alert=0.0):
    s = get_session()
    try:
        existing = s.query(_SkuRow).filter(_SkuRow.name == name).first()
        if existing:
            return None, "SKU_NAME_CONFLICT"
        row = _SkuRow(name=name, category=category, base_unit=base_unit,
                      min_stock_alert=min_stock_alert,
                      sku_code="SKU-" + new_id().upper())
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, None
    finally:
        s.close()


def update_sku(sku_id, **fields):
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is None:
            return None, "NOT_FOUND"
        if "name" in fields and fields["name"] and fields["name"] != row.name:
            clash = s.query(_SkuRow).filter(_SkuRow.name == fields["name"]).first()
            if clash:
                return None, "SKU_NAME_CONFLICT"
        for k, v in fields.items():
            if v is not None and hasattr(row, k):
                setattr(row, k, v)
        s.commit()
        return row, None
    finally:
        s.close()


def find_sku_by_name(name):
    s = get_session()
    try:
        return s.query(_SkuRow).filter(_SkuRow.name == name).first()
    finally:
        s.close()


def apply_stock_log(sku_id, name, qty, unit, amount, vendor, date, receipt_id,
                    kind, note=""):
    """写库存流水 + 更新 SKU 当前库存。"""
    s = get_session()
    try:
        s.add(_StockLogRow(sku_id=sku_id, name=name, qty=qty, unit=unit, amount=amount,
                           vendor=vendor, date=date, receipt_id=receipt_id,
                           kind=kind, note=note, created_at=now_iso()))
        if sku_id:
            sku = s.get(_SkuRow, int(sku_id))
            if sku:
                if kind in ("in", "stocktake"):
                    sku.current_stock += qty if kind == "in" else (qty - sku.current_stock)
                    if kind == "in" and amount > 0:
                        sku.last_unit_price = amount / qty if qty else sku.last_unit_price
                elif kind in ("consume", "waste"):
                    sku.current_stock -= qty
        s.commit()
    finally:
        s.close()


def stocktake_sku(sku_id, actual_qty, note=""):
    s = get_session()
    try:
        sku = s.get(_SkuRow, int(sku_id))
        if sku is None:
            return None
        diff = actual_qty - sku.current_stock
        s.add(_StockLogRow(sku_id=sku.id, name=sku.name, qty=diff, unit=sku.base_unit,
                           amount=0, vendor="", date=now_iso()[:10], receipt_id=None,
                           kind="stocktake", note=note or "盘点", created_at=now_iso()))
        sku.current_stock = actual_qty
        s.commit()
        return sku
    finally:
        s.close()


def delete_sku(sku_id):
    """安全删除/停用 SKU：
    - 若无库存流水引用：直接物理删除并返回 (True, 'DELETED')
    - 若有库存流水引用：标记停用 active=0 并返回 (True, 'DEACTIVATED')
    - 若不存在：返回 (False, 'NOT_FOUND')
    """
    s = get_session()
    try:
        row = s.get(_SkuRow, int(sku_id))
        if row is None:
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


def merge_skus(primary_sku_id, secondary_sku_ids):
    """合并 SKU：
    - 将 secondary_sku_ids 关联的所有 _StockLogRow / _ItemRow 迁移到 primary_sku_id
    - 将 secondary SKU 的名称及现有库存按规则合并至 primary
    - 将 secondary SKU 标记停用 (active=0)
    - 返回合并受影响的记录数
    """
    s = get_session()
    try:
        primary = s.get(_SkuRow, int(primary_sku_id))
        if primary is None:
            return None, "PRIMARY_NOT_FOUND"
        sec_ids = [int(x) for x in secondary_sku_ids if int(x) != int(primary_sku_id)]
        if not sec_ids:
            return None, "NO_SECONDARY_SKUS"

        secondary_skus = s.query(_SkuRow).filter(_SkuRow.id.in_(sec_ids)).all()
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


def deduplicate_skus_by_canonical():
    """B-P0-1 存量脏数据迁移：按 canonical 名归一去重，合并 _\\d{10} 变体。

    遍历全部 SKU（含停用），以 canonical_name 为分组键，将同组内的
    变体合并至组内 id 最小的活跃 SKU（保留主），其余停用并迁移流水。
    特例保障：本地新鲜菜心_1787140411 → 本地新鲜菜心（sku 07 → sku 03）。
    返回合并报告 list[dict]。
    """
    import re as _re
    try:
        from ai_registry.tools.smart_splitter.v1_2_0_multi_pack import SmartSplitterTool as _ST
        _tool = _ST()
        def _canon(n): return _tool.sanitize_name(n)
    except Exception:
        _pat = _re.compile(r"_\d{10}$")
        def _canon(n): return _pat.sub("", (n or "").strip()).strip()

    s = get_session()
    try:
        rows = s.query(_SkuRow).all()
        groups = {}
        for r in rows:
            c = _canon(r.name)
            groups.setdefault(c, []).append(r)
        reports = []
        for canon, members in groups.items():
            if len(members) <= 1:
                continue
            # 仅对活跃 SKU 去重（已停用跳过，避免重复合并导致库存翻倍）
            active_members = [m for m in members if m.active == 1]
            if len(active_members) <= 1:
                continue
            # 主 SKU：优先 canonical==name 的干净主，其次活跃中最早创建
            def _is_clean(m): return 0 if m.name == canon else 1
            active_sorted = sorted(active_members, key=lambda x: (_is_clean(x), x.id))
            primary = active_sorted[0]
            secondaries = [m for m in active_sorted[1:]]
            sec_ids = [m.id for m in secondaries]
            sec_names = [m.name for m in secondaries]
            # 迁移流水
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
            reports.append({
                "canonical": canon,
                "primary_id": primary.id,
                "primary_name": primary.name,
                "merged_ids": sec_ids,
                "merged_names": sec_names,
                "new_stock": primary.current_stock,
            })
        if reports:
            s.commit()
        return reports
    finally:
        s.close()


def price_history(sku_id):
    """SKU 价格历史：从库存流水（kind=in）推导单价 = amount/qty。"""
    s = get_session()
    try:
        rows = s.query(_StockLogRow).filter(
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
def list_suppliers(include_inactive=False):
    s = get_session()
    try:
        q = s.query(_SupplierRow)
        if not include_inactive:
            q = q.filter(_SupplierRow.active == 1)
        return q.all()
    finally:
        s.close()


def get_supplier(supplier_id):
    s = get_session()
    try:
        return s.get(_SupplierRow, int(supplier_id))
    finally:
        s.close()


def find_supplier_by_name(name):
    s = get_session()
    try:
        return s.query(_SupplierRow).filter(_SupplierRow.name == name).first()
    finally:
        s.close()


def find_supplier_by_canonical_name(name):
    """U-06：按归一 key（去英文括号注 + 繁转简）查找已有供应商，命中则返回该行。"""
    from app.services.supplier_normalizer import canonical_supplier_key
    key = canonical_supplier_key(name)
    if not key:
        return None
    s = get_session()
    try:
        for sup in s.query(_SupplierRow).all():
            if canonical_supplier_key(sup.name) == key:
                return sup
        return None
    finally:
        s.close()


def create_supplier(name, **fields):
    s = get_session()
    try:
        existing = s.query(_SupplierRow).filter(_SupplierRow.name == name).first()
        if existing:
            return None, "已存在同名供应商"
        s.close()
        # U-06：核心词归一命中已有供应商 → 复用其 id，不再新建变体档案
        hit = find_supplier_by_canonical_name(name)
        if hit is not None:
            return hit.id, None
        s = get_session()
        row = _SupplierRow(name=name, supplier_code="SUP-" + new_id().upper(), **fields)
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


def supplier_stats(supplier_id):
    """供应商聚合：receipt_count / 赊账未付总额。"""
    s = get_session()
    try:
        receipts = s.query(_ReceiptRow).filter(_ReceiptRow.supplier_name ==
                                               (s.get(_SupplierRow, int(supplier_id)).name
                                                if s.get(_SupplierRow, int(supplier_id)) else "")).all()
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
def upsert_vendor_memory(vendor, notes, sample):
    s = get_session()
    try:
        row = s.get(_VendorMemoryRow, vendor)
        if row is None:
            row = _VendorMemoryRow(vendor=vendor)
            s.add(row)
        if notes:
            row.notes = notes
        if sample:
            row.sample = sample
        s.commit()
    finally:
        s.close()


def get_vendor_memory(vendor):
    s = get_session()
    try:
        row = s.get(_VendorMemoryRow, vendor)
        if row is None:
            return None
        return {"vendor": row.vendor, "notes": row.notes or "", "sample": row.sample or ""}
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
                     properties=None, grp=None):
    """写一条用户行为埋点（漏斗用）。"""
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


def log_user_event(account_id="", session_id="", event_type="",
                   receipt_id=None, properties=None, grp=None):
    """前端埋点事件。"""
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
        ("upload", "upload"),
        ("parse_done", "parse_done"),
        ("edit", "save_edited"),
        ("approve", "approve"),
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
            total = counts.get("upload", 0) or 1
            out_steps = []
            for step_key, event_key in steps:
                c = counts.get(event_key, 0)
                conv = round(c / total, 4) if total else 0.0
                out_steps.append({
                    "step": step_key,
                    "event": event_key,
                    "count": c,
                    "conversion_from_upload": conv,
                })
            return {"group": grp_label, "period_start": start_ts,
                    "period_end": end_ts, "steps": out_steps,
                    "sample_size": counts.get("upload", 0)}
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

    阈值取 FEEDBACK_DISTILL_THRESHOLD 常量（默认 3），可由 threshold 参数覆盖。
    语义：最近 N 条反馈均为点踩，认为需要沉淀为供应商记忆。
    返回 True 需调用 rag 沉淀。
    """
    from app.models import FEEDBACK_DISTILL_THRESHOLD

    n = int(threshold) if threshold else int(FEEDBACK_DISTILL_THRESHOLD)
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
