# -*- coding: utf-8 -*-
"""FIFO 批次库存引擎与成本核算单测。

覆盖场景：
1. 批次入库记录 (record_inbound_batch)
2. 单批次精确扣减与状态更新
3. 跨批次价格波动按入库时间 FIFO 顺序加权扣减
4. 前批次耗尽 (is_closed=1) 后新进货批次单价实时承接
5. 多单位换算折算（kg / g / 斤 / 份 等）
6. 库存不足 / 超卖暂估批次生成
7. 反向冲销 (void_consumption_fifo) 批次与库存回滚
8. apply_stock_log 与 apply_receipt_to_inventory 入库钩子生成批次
"""

import pytest
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 使用临时数据库隔离测试
@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    from app import db
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_costing.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    
    # 重新初始化 engine
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    # 清理
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def test_record_inbound_batch():
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        batch = CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=40.0,
            unit="kg",
            date="2026-08-25",
            receipt_id=101,
        )
        session.commit()

        assert batch.id is not None
        assert batch.sku_id == sku_id
        assert batch.initial_qty == 10.0
        assert batch.remaining_qty == 10.0
        assert batch.unit_cost == 40.0
        assert batch.unit == "kg"
        assert batch.is_closed == 0
        assert batch.is_estimated == 0
        assert batch.receipt_id == 101
    finally:
        session.close()


def test_fifo_single_batch_deduction():
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=40.0,
            unit="kg",
            date="2026-08-25",
        )
        session.commit()

        # 扣减 4kg
        total_cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku_id,
            qty_needed=4.0,
            unit_needed="kg",
            date="2026-08-26",
        )
        session.commit()

        assert total_cost == 160.00
        assert len(details) == 1
        assert details[0]["qty_consumed"] == 4.0
        assert details[0]["unit_cost"] == 40.0
        assert details[0]["total_cost"] == 160.00

        # 检查批次剩余量
        batches = CostingService.get_active_batches(session, sku_id)
        assert len(batches) == 1
        assert batches[0].remaining_qty == 6.0
        assert batches[0].is_closed == 0
    finally:
        session.close()


def test_fifo_multi_batch_price_fluctuation():
    """测试跨批次价格波动：第1天 10kg@40元，第2天 10kg@50元，扣减 15kg。"""
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        # 批次 1: 2026-08-25 10kg @ 40
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=40.0,
            unit="kg",
            date="2026-08-25",
        )
        # 批次 2: 2026-08-27 10kg @ 50
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=50.0,
            unit="kg",
            date="2026-08-27",
        )
        session.commit()

        # 消耗 15kg
        total_cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku_id,
            qty_needed=15.0,
            unit_needed="kg",
            date="2026-08-28",
        )
        session.commit()

        # 10*40 + 5*50 = 650.00
        assert total_cost == 650.00
        assert len(details) == 2
        assert details[0]["qty_consumed"] == 10.0
        assert details[0]["unit_cost"] == 40.0
        assert details[0]["total_cost"] == 400.00
        assert details[1]["qty_consumed"] == 5.0
        assert details[1]["unit_cost"] == 50.0
        assert details[1]["total_cost"] == 250.00

        # 检查批次状态
        all_batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).order_by(db._InventoryBatchRow.id.asc()).all()
        assert len(all_batches) == 2
        assert all_batches[0].remaining_qty == 0.0
        assert all_batches[0].is_closed == 1
        assert all_batches[1].remaining_qty == 5.0
        assert all_batches[1].is_closed == 0
    finally:
        session.close()


def test_fifo_exhaustion_and_new_inbound():
    """批次1耗尽后，后续扣减100%按新批次单价50元/kg承接。"""
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        # 批次 1: 5kg @ 40
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=5.0,
            unit_price=40.0,
            unit="kg",
            date="2026-08-20",
        )
        # 批次 2: 10kg @ 50
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=50.0,
            unit="kg",
            date="2026-08-22",
        )
        session.commit()

        # 先扣 5kg 耗尽批次 1
        cost1, _ = CostingService.deduct_consumption_fifo(session, sku_id, 5.0, "kg", "2026-08-23")
        session.commit()
        assert cost1 == 200.00

        # 再次扣减 3kg，应完全承接批次 2 单价 50 元/kg
        cost2, details2 = CostingService.deduct_consumption_fifo(session, sku_id, 3.0, "kg", "2026-08-24")
        session.commit()
        assert cost2 == 150.00
        assert len(details2) == 1
        assert details2[0]["unit_cost"] == 50.0
        assert details2[0]["total_cost"] == 150.00
    finally:
        session.close()


def test_multi_unit_conversion_deduction():
    """多单位换算：入库 10斤 @ 25元/斤，配方消耗 250g，验证折算 0.5斤（0.25kg），扣减 12.50元。"""
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="斤")
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=10.0,
            unit_price=25.0,
            unit="斤",
            date="2026-08-25",
        )
        session.commit()

        # 消耗 250g
        total_cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku_id,
            qty_needed=250.0,
            unit_needed="g",
            date="2026-08-26",
        )
        session.commit()

        # 250g = 0.5斤, 0.5 * 25 = 12.50
        assert total_cost == 12.50
        assert len(details) == 1
        assert details[0]["qty_consumed"] == 250.0
        assert details[0]["unit"] == "g"
        assert details[0]["total_cost"] == 12.50

        # 检查批次剩余量 (10 - 0.5 = 9.5斤)
        batches = CostingService.get_active_batches(session, sku_id)
        assert len(batches) == 1
        assert batches[0].remaining_qty == 9.5
    finally:
        session.close()


def test_oversold_estimated_batch():
    """库存不足时生成暂估批次 (is_estimated=1)。"""
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        # 批次 1 仅 2kg @ 30元
        CostingService.record_inbound_batch(
            session=session,
            sku_id=sku_id,
            qty=2.0,
            unit_price=30.0,
            unit="kg",
            date="2026-08-25",
        )
        session.commit()

        # 消耗 5kg（超卖 3kg）
        total_cost, details = CostingService.deduct_consumption_fifo(
            session=session,
            sku_id=sku_id,
            qty_needed=5.0,
            unit_needed="kg",
            date="2026-08-26",
        )
        session.commit()

        # 2*30 + 3*30(暂估单价) = 150.00
        assert total_cost == 150.00
        assert len(details) == 2
        assert details[0]["qty_consumed"] == 2.0
        assert details[1]["qty_consumed"] == 3.0
        assert details[1]["total_cost"] == 90.00

        # 检查暂估批次记录
        est_batch = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id, is_estimated=1).first()
        assert est_batch is not None
        assert est_batch.is_closed == 1
    finally:
        session.close()


def test_void_consumption_rollback():
    """测试反向冲销回滚批次剩余量与状态。"""
    from app import db
    from app.services.costing_service import CostingService

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("测试牛腩", base_unit="kg")
        b1 = CostingService.record_inbound_batch(session, sku_id, 10.0, 40.0, "kg", "2026-08-25")
        b2 = CostingService.record_inbound_batch(session, sku_id, 10.0, 50.0, "kg", "2026-08-27")
        session.commit()

        # 创建餐品
        dish = db._DishRow(name="招牌牛腩煲", category="主菜", price=88.0, status="active")
        session.add(dish)
        session.commit()

        # 记录消耗 15kg
        total_cost, details = CostingService.deduct_consumption_fifo(session, sku_id, 15.0, "kg", "2026-08-28")
        cons = db._DailyConsumptionRow(
            date="2026-08-28",
            dish_id=dish.id,
            quantity=1.0,
            total_cost=total_cost,
            unit_cost=total_cost,
            is_void=0,
            created_at=db.now_iso(),
        )
        session.add(cons)
        session.commit()

        for d in details:
            dt = db._DailyConsumptionDetailRow(
                consumption_id=cons.id,
                sku_id=d["sku_id"],
                qty_consumed=d["qty_consumed"],
                unit=d["unit"],
                unit_cost=d["unit_cost"],
                total_cost=d["total_cost"],
                batch_id=d["batch_id"],
                batch_date=d["batch_date"],
            )
            session.add(dt)
        session.commit()

        # 冲销
        success = CostingService.void_consumption_fifo(session, cons.id)
        session.commit()

        assert success is True
        # 验证 cons.is_void == 1
        refreshed_cons = session.get(db._DailyConsumptionRow, cons.id)
        assert refreshed_cons.is_void == 1

        # 验证批次已回滚
        b1_refreshed = session.get(db._InventoryBatchRow, b1.id)
        b2_refreshed = session.get(db._InventoryBatchRow, b2.id)
        assert b1_refreshed.remaining_qty == 10.0
        assert b1_refreshed.is_closed == 0
        assert b2_refreshed.remaining_qty == 10.0
        assert b2_refreshed.is_closed == 0
    finally:
        session.close()


def test_stock_log_and_receipt_inbound_hooks():
    """测试 apply_stock_log 与 apply_receipt_to_inventory 的批次入库钩子。"""
    from app import db
    from app.services.inventory import apply_receipt_to_inventory

    # 1. apply_stock_log (kind='in') 自动生成批次
    sku_id, _ = db.create_sku("测试白菜", base_unit="kg")
    db.apply_stock_log(
        sku_id=sku_id,
        name="测试白菜",
        qty=50.0,
        unit="kg",
        amount=150.0,
        vendor="蔬菜供应商",
        date="2026-08-25",
        receipt_id=None,
        kind="in",
    )

    session = db.get_session()
    try:
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).all()
        assert len(batches) == 1
        assert batches[0].initial_qty == 50.0
        assert batches[0].remaining_qty == 50.0
        assert batches[0].unit_cost == 3.0  # 150 / 50
    finally:
        session.close()

    # 2. apply_receipt_to_inventory 自动生成批次
    rid = db.create_receipt(supplier_name="生鲜农场", status="approved")
    db.update_receipt(rid, receipt_date="2026-08-26")
    db.set_receipt_items(rid, [{"name": "测试土豆", "quantity": 20.0, "unit": "kg", "unit_price": 4.5, "amount": 90.0}])

    receipt_row = db.get_receipt_row(rid)
    apply_receipt_to_inventory(receipt_row)

    session = db.get_session()
    try:
        potato_sku = db.find_sku_by_name("测试土豆")
        assert potato_sku is not None
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=potato_sku.id).all()
        assert len(batches) == 1
        assert batches[0].initial_qty == 20.0
        assert batches[0].unit_cost == 4.5
        assert batches[0].receipt_id == rid
    finally:
        session.close()


def test_dual_track_manual_consume_and_waste():
    """[AC-1.1, AC-1.2] 手动消耗与报损统一扣减批次池，确保双轨库存无漂移。"""
    from app import db
    from app.services.costing_service import CostingService
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    headers = {"X-Role": "owner", "X-Email": "boss@demo.hk"}

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("双轨排骨", base_unit="kg")
        # 批次 A: 5kg @ 40
        CostingService.record_inbound_batch(
            session=session, sku_id=sku_id, qty=5.0, unit_price=40.0, unit="kg", date="2026-08-20"
        )
        # 批次 B: 10kg @ 45
        CostingService.record_inbound_batch(
            session=session, sku_id=sku_id, qty=10.0, unit_price=45.0, unit="kg", date="2026-08-21"
        )
        sku = session.get(db._SkuRow, sku_id)
        sku.current_stock = 15.0
        session.commit()
    finally:
        session.close()

    # 1. 手动消耗 7kg
    resp_consume = client.post(f"/api/inventory/{sku_id}/consume", json={"quantity": 7.0, "note": "后厨领料"}, headers=headers)
    assert resp_consume.status_code == 200, resp_consume.text
    data_c = resp_consume.json()
    assert data_c["status"] == "success"
    assert data_c["data"]["current_stock"] == 8.0

    session = db.get_session()
    try:
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).order_by(db._InventoryBatchRow.id.asc()).all()
        assert len(batches) == 2
        # 批次 A 耗尽
        assert batches[0].remaining_qty == 0.0
        assert batches[0].is_closed == 1
        # 批次 B 剩余 8.0kg
        assert batches[1].remaining_qty == 8.0
        assert batches[1].is_closed == 0

        # 批次池总和与台账完全一致
        batch_sum = sum(b.remaining_qty for b in batches if b.is_closed == 0)
        sku = session.get(db._SkuRow, sku_id)
        assert abs(sku.current_stock - batch_sum) < 1e-4
    finally:
        session.close()

    # 2. 手动报损 1kg
    resp_waste = client.post(f"/api/inventory/{sku_id}/waste", json={"quantity": 1.0, "reason": "过期变质"}, headers=headers)
    assert resp_waste.status_code == 200, resp_waste.text
    assert resp_waste.json()["data"]["current_stock"] == 7.0

    session = db.get_session()
    try:
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).order_by(db._InventoryBatchRow.id.asc()).all()
        assert batches[1].remaining_qty == 7.0
        sku = session.get(db._SkuRow, sku_id)
        batch_sum = sum(b.remaining_qty for b in batches if b.is_closed == 0)
        assert abs(sku.current_stock - 7.0) < 1e-4
        assert abs(sku.current_stock - batch_sum) < 1e-4
    finally:
        session.close()


def test_stocktake_deficit_and_surplus_batch_pool_sync():
    """[AC-1.3, AC-1.4, AC-1.5] 盘点盘亏 FIFO 自动核销，盘盈自动生成调整批次，批次池与台账严格无漂移。"""
    from app import db
    from app.services.costing_service import CostingService
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    headers = {"X-Role": "owner", "X-Email": "boss@demo.hk"}

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("盘点鸡肉", base_unit="kg")
        CostingService.record_inbound_batch(
            session=session, sku_id=sku_id, qty=8.0, unit_price=30.0, unit="kg", date="2026-08-20"
        )
        sku = session.get(db._SkuRow, sku_id)
        sku.current_stock = 8.0
        sku.last_unit_price = 30.0
        session.commit()
    finally:
        session.close()

    # 1. 盘亏：实际 5kg（亏 3kg）
    resp_loss = client.post(f"/api/inventory/{sku_id}/stocktake", json={"actual_qty": 5.0, "reason": "自然损耗"}, headers=headers)
    assert resp_loss.status_code == 200, resp_loss.text
    assert resp_loss.json()["data"]["current_stock"] == 5.0

    session = db.get_session()
    try:
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).all()
        assert len(batches) == 1
        assert batches[0].remaining_qty == 5.0
        sku = session.get(db._SkuRow, sku_id)
        assert abs(sku.current_stock - 5.0) < 1e-4
    finally:
        session.close()

    # 2. 盘盈：实际 9kg（盈 4kg）
    resp_gain = client.post(f"/api/inventory/{sku_id}/stocktake", json={"actual_qty": 9.0, "reason": "此前少计"}, headers=headers)
    assert resp_gain.status_code == 200, resp_gain.text
    assert resp_gain.json()["data"]["current_stock"] == 9.0

    session = db.get_session()
    try:
        batches = session.query(db._InventoryBatchRow).filter_by(sku_id=sku_id).order_by(db._InventoryBatchRow.id.asc()).all()
        assert len(batches) == 2
        # 新批次 initial_qty=4.0
        assert batches[1].initial_qty == 4.0
        assert batches[1].remaining_qty == 4.0
        assert batches[1].unit_cost == 30.0

        batch_sum = sum(b.remaining_qty for b in batches if b.is_closed == 0)
        sku = session.get(db._SkuRow, sku_id)
        assert abs(sku.current_stock - 9.0) < 1e-4
        assert abs(sku.current_stock - batch_sum) < 1e-4
    finally:
        session.close()


def test_negative_quantity_rejection_in_manual_inventory():
    """[AC-3.1] 后端库存手动消耗与报损负数拦截拒绝。"""
    from app import db
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    headers = {"X-Role": "owner", "X-Email": "boss@demo.hk"}

    sku_id, _ = db.create_sku("拦截牛肉", base_unit="kg")
    db.update_sku(sku_id, current_stock=10.0)

    # 1. 负数消耗
    resp1 = client.post(f"/api/inventory/{sku_id}/consume", json={"quantity": -5.0}, headers=headers)
    assert resp1.status_code == 400
    assert "消耗数量必须大于 0" in resp1.json()["msg"]

    # 2. 零消耗
    resp2 = client.post(f"/api/inventory/{sku_id}/consume", json={"quantity": 0.0}, headers=headers)
    assert resp2.status_code == 400
    assert "消耗数量必须大于 0" in resp2.json()["msg"]

    # 3. 负数报损
    resp3 = client.post(f"/api/inventory/{sku_id}/waste", json={"quantity": -2.0}, headers=headers)
    assert resp3.status_code == 400
    assert "报损数量必须大于 0" in resp3.json()["msg"]

    # 验证库存未受任何污染
    sku = db.get_sku(sku_id)
    assert sku.current_stock == 10.0


def test_cross_dimension_unit_conversion_runtime():
    """[AC-5.3, AC-5.4] 跨量纲单位换算防穿透熔断与同量纲合法折算。"""
    from app.services.costing_service import CostingService, check_unit_compatibility

    # 1. 跨量纲熔断
    compat, reason = check_unit_compatibility("份", "kg")
    assert compat is False

    with pytest.raises(ValueError) as excinfo:
        CostingService.convert_unit_quantity(5, "份", "kg")
    assert "跨量纲单位不可换算" in str(excinfo.value)

    # 2. 同量纲合法折算
    compat_ok, _ = check_unit_compatibility("g", "kg")
    assert compat_ok is True
    qty_kg = CostingService.convert_unit_quantity(500, "g", "kg")
    assert qty_kg == 0.5

