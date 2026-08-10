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
import os
import uuid
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "../receipt_demo.db"))

_engine = None
_SessionLocal = None

_ReceiptRow = _ItemRow = _SkuRow = _StockLogRow = _SupplierRow = None
_DeptRow = _PaymentRow = _VendorMemoryRow = _AppSettingRow = None


def _make_engine():
    global _engine, _SessionLocal
    global _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow, _SupplierRow
    global _DeptRow, _PaymentRow, _VendorMemoryRow, _AppSettingRow

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
        created_at = Column(String, default="")
        updated_at = Column(String, default="")

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

    Base.metadata.create_all(_engine)

    _ReceiptRow, _ItemRow, _SkuRow, _StockLogRow = ReceiptRow, ItemRow, SkuRow, StockLogRow
    _SupplierRow, _DeptRow, _PaymentRow = SupplierRow, DeptRow, PaymentRow
    _VendorMemoryRow, _AppSettingRow = VendorMemoryRow, AppSettingRow
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
            s.add(_ItemRow(
                receipt_id=int(receipt_id),
                raw_name=raw_name,
                raw_unit=raw_unit,
                fuzzy_candidates_json=json.dumps(fuzzy, ensure_ascii=False),
                entity_candidates_json=json.dumps(entity, ensure_ascii=False),
                **row,
            ))
        s.commit()
    finally:
        s.close()


def list_receipt_rows():
    s = get_session()
    try:
        return s.query(_ReceiptRow).order_by(_ReceiptRow.id.desc()).all()
    finally:
        s.close()


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


def create_supplier(name, **fields):
    s = get_session()
    try:
        existing = s.query(_SupplierRow).filter(_SupplierRow.name == name).first()
        if existing:
            return None, "已存在同名供应商"
        row = _SupplierRow(name=name, supplier_code="SUP-" + new_id().upper(), **fields)
        s.add(row)
        s.commit()
        s.refresh(row)
        return row.id, None
    finally:
        s.close()


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
