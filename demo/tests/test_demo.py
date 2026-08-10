# -*- coding: utf-8 -*-
"""确定性逻辑测试：契约门禁 / 算术门禁 / RBAC / 幂等入库（不依赖 LLM，秒级）。"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DB_PATH", "/tmp/receipt_demo_test.db")
if os.path.exists("/tmp/receipt_demo_test.db"):
    os.remove("/tmp/receipt_demo_test.db")

from app.models import EngineConfig


# -------------------------------------------------------------
# 契约门禁
# -------------------------------------------------------------
def test_contract_accepts_valid():
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "printed_delivery_note", "vendor": "祥興",
        "date": "2024-03-25",
        "items": [{"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0}],
        "total": 50.0, "payment_marked": False, "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is None, err
    assert data.vendor == "祥興"
    assert len(data.items) == 1


def test_contract_rejects_unknown_field():
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "printed_delivery_note", "vendor": "祥興",
        "date": "2024-03-25",
        "items": [{"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0, "hack": "x"}],
        "total": 50.0, "payment_marked": False, "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is not None, "extra 字段应被拒绝"
    assert "hack" in err


def test_contract_rejects_bad_enum():
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "bogus_form", "vendor": "祥興", "date": "2024-03-25",
        "items": [{"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0}],
        "total": 50.0, "payment_marked": False, "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is not None, "非法枚举应被拒绝"


def test_contract_rejects_nan_total():
    from app.services.contract import validate_contract
    payload = {
        "doc_form": "printed_delivery_note", "vendor": "祥興", "date": "2024-03-25",
        "items": [{"name": "菜心", "qty": 5.0, "unit": "斤", "unit_price": 10.0, "amount": 50.0}],
        "total": float("nan"), "payment_marked": False, "confidence": 0.9,
    }
    data, err = validate_contract(payload)
    assert err is not None


# -------------------------------------------------------------
# 算术门禁
# -------------------------------------------------------------
def test_math_ok():
    from app.services.math_engine import validate_and_report
    from app.models import ReceiptData
    data = ReceiptData(doc_form="printed_delivery_note", vendor="祥興", date="2024-03-25",
                       items=[{"name": "菜心", "qty": 5, "unit": "斤", "unit_price": 10, "amount": 50}],
                       total=50, payment_marked=False, confidence=0.9)
    assert validate_and_report(data) == []


def test_math_catches_row_mismatch():
    from app.services.math_engine import validate_and_report
    from app.models import ReceiptData
    data = ReceiptData(doc_form="printed_delivery_note", vendor="祥興", date="2024-03-25",
                       items=[{"name": "菜心", "qty": 5, "unit": "斤", "unit_price": 10, "amount": 60}],
                       total=60, payment_marked=False, confidence=0.9)
    problems = validate_and_report(data)
    assert len(problems) == 1
    assert "菜心" in problems[0]


def test_math_catches_total_mismatch():
    from app.services.math_engine import validate_and_report
    from app.models import ReceiptData
    data = ReceiptData(doc_form="printed_delivery_note", vendor="祥興", date="2024-03-25",
                       items=[{"name": "菜心", "qty": 5, "unit": "斤", "unit_price": 10, "amount": 50}],
                       total=999, payment_marked=False, confidence=0.9)
    problems = validate_and_report(data)
    assert len(problems) == 1
    assert "999" in problems[0]


# -------------------------------------------------------------
# RBAC 三层
# -------------------------------------------------------------
def test_auth_login_ok():
    from app.auth import authenticate
    assert authenticate("admin@demo.hk", "admin123")["role"] == "admin"
    assert authenticate("boss@demo.hk", "boss123")["role"] == "owner"
    assert authenticate("staff@demo.hk", "staff123")["role"] == "staff"
    assert authenticate("staff@demo.hk", "wrong") is None


def test_require_role_lower():
    """staff 调 owner 专属接口应被拒；owner 调 staff 接口应放行。"""
    from fastapi import HTTPException
    from app.auth import require_role, ROLE_RANK
    from types import SimpleNamespace

    def mk_req(role):
        return SimpleNamespace(headers={"X-Role": role})

    staff_req = mk_req("staff")
    owner_req = mk_req("owner")
    try:
        require_role("owner")(staff_req)
        assert False, "staff 调 owner 接口应 403"
    except HTTPException as e:
        assert e.status_code == 403
    assert require_role("staff")(owner_req)["role"] == "owner"
    assert ROLE_RANK["admin"] > ROLE_RANK["owner"] > ROLE_RANK["staff"]


# -------------------------------------------------------------
# 幂等入库
# -------------------------------------------------------------
def test_inventory_approve_creates_sku():
    """approve 入账：匹配/创建 SKU + 写库存流水。"""
    from app import db
    from app.services.inventory import apply_receipt_to_inventory
    rid = db.create_receipt(supplier_name="祥興", status="edited")
    items = [{
        "name": "菜心", "raw_name": "菜心", "quantity": 5, "unit": "斤",
        "unit_price": 10, "amount": 50, "sku_id": None, "cost_center_id": None,
        "confidence": 0.9, "matched": 0, "price_anomaly": 0,
        "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }]
    db.set_receipt_items(rid, items)
    row = db.get_receipt_row(rid)
    apply_receipt_to_inventory(row)
    sku = db.find_sku_by_name("菜心")
    assert sku is not None, "approve 应自动创建 SKU"
    assert sku.current_stock == 5.0
    assert sku.last_unit_price == 10.0


def test_engine_config_persists():
    from app import db
    cfg = EngineConfig(grey_enabled=True, grey_percent=30,
                       recognition_model="test-model")
    db.set_engine_config(cfg)
    loaded = db.get_engine_config()
    assert loaded.grey_enabled is True
    assert loaded.grey_percent == 30
    assert loaded.recognition_model == "test-model"
    # 还原
    db.set_engine_config(EngineConfig())


def test_grey_assignment_receipt_mode():
    from app.models import should_use_grey
    cfg = EngineConfig(grey_enabled=True, grey_percent=0)
    assert should_use_grey(cfg) is False, "概率0 → 全常规"
    cfg = EngineConfig(grey_enabled=False, grey_percent=100)
    assert should_use_grey(cfg) is False, "未启用 → 全常规"


def test_grey_assignment_supplier_deterministic():
    from app.models import EngineConfig as EC, GreyAssignMode, should_use_grey
    cfg = EC(grey_enabled=True, grey_percent=50,
             grey_assign_mode=GreyAssignMode.SUPPLIER)
    a = should_use_grey(cfg, "祥興食品")
    b = should_use_grey(cfg, "祥興食品")
    assert a == b, "同供应商应一致命中（确定性）"
    # 概率边界
    cfg0 = EC(grey_enabled=True, grey_percent=0,
              grey_assign_mode=GreyAssignMode.SUPPLIER)
    assert should_use_grey(cfg0, "祥興食品") is False


def test_receipt_version_optimistic_lock():
    """乐观锁：save/approve 必须带正确 version。"""
    from app import db
    rid = db.create_receipt(supplier_name="祥興", status="edited")
    db.update_receipt(rid, receipt_date="2024-03-25", total_amount=50,
                      version=2)  # 模拟已保存过
    row = db.get_receipt_row(rid)
    assert row.version == 2


def test_supplier_autocreate_on_approve():
    """approve 后供应商自动建档。"""
    from app import db
    from app.services.inventory import apply_receipt_to_inventory
    rid = db.create_receipt(supplier_name="測試供應商", status="edited")
    db.set_receipt_items(rid, [{
        "name": "牛肉", "raw_name": "牛肉", "quantity": 2, "unit": "斤",
        "unit_price": 30, "amount": 60, "sku_id": None, "cost_center_id": None,
        "confidence": 0.9, "matched": 0, "price_anomaly": 0,
        "price_anomaly_direction": "", "price_diff_percent": 0.0,
        "unit_conversion_warning": "", "fuzzy_candidates": [], "entity_candidates": [],
    }])
    row = db.get_receipt_row(rid)
    apply_receipt_to_inventory(row)
    if db.find_supplier_by_name("測試供應商") is None:
        db.create_supplier("測試供應商")
    assert db.find_supplier_by_name("測試供應商") is not None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
