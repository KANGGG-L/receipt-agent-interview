# -*- coding: utf-8 -*-
"""餐品管理与每日消耗扣减/冲销 API 集成测试。

覆盖场景：
1. 餐品 CRUD 与 BOM 配方联动
2. 查询餐品详情（含配方食材列表与关联 SKU 实时库存）
3. 理论成本与当前毛利率基准计算（支持多单位换算折算）
4. 批量提交当日餐品消耗（原子 FIFO 批次扣减、生成流水、扣减库存）
5. 按日期查询餐品消耗记录、批次穿透明细与当日汇总财务统计
6. 冲销作废消耗记录（反向回滚批次剩余量、is_closed 状态与 SKU 库存）
7. 成本趋势分析（近 7/30 天真实成本变化曲线、总售出份数、总毛利）
8. RBAC 权限控制验证（店员读/录入、老板管理/冲销）
"""

import os
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)

# 使用临时数据库隔离测试
@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_api_dishes.db")
    monkeypatch.setenv("DB_PATH", test_db_path)

    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def _owner_headers():
    return {"X-Role": "owner", "X-Email": "boss@demo.hk"}


def _staff_headers():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


def test_dish_crud_and_bom_calculation():
    # 1. 准备食材 SKU
    sku1_id, _ = db.create_sku("牛腩", category="肉类", base_unit="kg", min_stock_alert=5.0)
    sku2_id, _ = db.create_sku("白萝卜", category="蔬菜", base_unit="kg", min_stock_alert=2.0)
    db.update_sku(sku1_id, last_unit_price=50.0, current_stock=20.0)
    db.update_sku(sku2_id, last_unit_price=6.0, current_stock=30.0)

    # 2. 创建餐品：招牌牛腩煲 (售 68 元，配方: 0.3kg 牛腩 + 200g 白萝卜)
    create_payload = {
        "name": "招牌牛腩煲",
        "category": "煲仔类",
        "price": 68.0,
        "description": "经典港式风味",
        "status": "active",
        "ingredients": [
            {"sku_id": sku1_id, "consumption_qty": 0.3, "unit": "kg", "notes": "精选牛腩"},
            {"sku_id": sku2_id, "consumption_qty": 200.0, "unit": "g", "notes": "清甜白萝卜"},
        ],
    }
    resp = client.post("/api/dishes", json=create_payload, headers=_owner_headers())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "success"
    dish_id = data["data"]["id"]
    assert dish_id is not None
    assert data["data"]["name"] == "招牌牛腩煲"

    # 理论成本 = 0.3 * 50.0 + (200g = 0.2kg) * 6.0 = 15.0 + 1.2 = 16.20 元
    # 毛利率 = (68 - 16.20) / 68 = 51.80 / 68 = 76.18%
    assert data["data"]["theoretical_cost"] == 16.2
    assert round(data["data"]["gross_margin_rate"], 2) == 76.18
    assert len(data["data"]["ingredients"]) == 2

    # 3. 查询餐品列表
    list_resp = client.get("/api/dishes", headers=_staff_headers())
    assert list_resp.status_code == 200
    dishes = list_resp.json()["data"]
    assert len(dishes) == 1
    assert dishes[0]["id"] == dish_id
    assert dishes[0]["theoretical_cost"] == 16.2

    # 4. 查询单个餐品详情
    detail_resp = client.get(f"/api/dishes/{dish_id}", headers=_staff_headers())
    assert detail_resp.status_code == 200
    dish_detail = detail_resp.json()["data"]
    assert dish_detail["id"] == dish_id
    assert len(dish_detail["ingredients"]) == 2
    # 验证关联 SKU 实时库存
    ing1 = next(ing for ing in dish_detail["ingredients"] if ing["sku_id"] == sku1_id)
    assert ing1["sku_current_stock"] == 20.0
    assert ing1["sku_last_unit_price"] == 50.0

    # 5. 更新餐品：调价至 72 元，并修改白萝卜为 300g
    update_payload = {
        "name": "招牌牛腩煲(大份)",
        "price": 72.0,
        "ingredients": [
            {"sku_id": sku1_id, "consumption_qty": 0.3, "unit": "kg", "notes": "精选牛腩"},
            {"sku_id": sku2_id, "consumption_qty": 300.0, "unit": "g", "notes": "加大份白萝卜"},
        ],
    }
    put_resp = client.put(f"/api/dishes/{dish_id}", json=update_payload, headers=_owner_headers())
    assert put_resp.status_code == 200
    updated_dish = put_resp.json()["data"]
    assert updated_dish["name"] == "招牌牛腩煲(大份)"
    assert updated_dish["price"] == 72.0
    # 新理论成本 = 15.0 + 0.3 * 6.0 = 16.80
    assert updated_dish["theoretical_cost"] == 16.8
    # 新毛利率 = (72 - 16.8) / 72 = 55.2 / 72 = 76.67%
    assert round(updated_dish["gross_margin_rate"], 2) == 76.67

    # 6. 停用餐品
    del_resp = client.delete(f"/api/dishes/{dish_id}", headers=_owner_headers())
    assert del_resp.status_code == 200
    assert del_resp.json()["action"] == "DEACTIVATED"

    # 验证列表默认不含停用或可指定 status
    active_list = client.get("/api/dishes?status=active", headers=_staff_headers()).json()["data"]
    assert len(active_list) == 0
    all_list = client.get("/api/dishes?status=all", headers=_staff_headers()).json()["data"]
    assert len(all_list) == 1
    assert all_list[0]["status"] == "inactive"


def test_daily_consumption_batch_and_fifo_deduction():
    from app.services.costing_service import CostingService

    # 1. 准备食材与批次入库 (模拟两天进价波动)
    sku_beef, _ = db.create_sku("黄牛肉", base_unit="kg")
    session = db.get_session()
    try:
        # 批次1: 8-25 入库 10kg @ 40元/kg
        CostingService.record_inbound_batch(session, sku_beef, 10.0, 40.0, "kg", date="2026-08-25", receipt_id=1)
        # 批次2: 8-26 入库 10kg @ 50元/kg
        CostingService.record_inbound_batch(session, sku_beef, 10.0, 50.0, "kg", date="2026-08-26", receipt_id=2)
        session.commit()
    finally:
        session.close()

    # 更新 SKU 当前库存与最新单价
    db.update_sku(sku_beef, current_stock=20.0, last_unit_price=50.0)

    # 2. 创建餐品：小炒牛肉 (每份消耗 0.5kg 黄牛肉，售价 45 元)
    create_dish = {
        "name": "小炒黄牛肉",
        "category": "热炒",
        "price": 45.0,
        "ingredients": [
            {"sku_id": sku_beef, "consumption_qty": 0.5, "unit": "kg", "notes": ""},
        ],
    }
    dish_resp = client.post("/api/dishes", json=create_dish, headers=_owner_headers())
    dish_id = dish_resp.json()["data"]["id"]

    # 3. 批量录入当日消耗：售出 30 份小炒黄牛肉 (总需 15kg 黄牛肉)
    # 按 FIFO 批次扣减：前 10kg 来自批次1 @40元 = 400元；后 5kg 来自批次2 @50元 = 250元
    # 总成本 = 650.00 元，单份平均成本 = 650 / 30 = 21.6667 元
    batch_payload = {
        "date": "2026-08-27",
        "notes": "午市+晚市实耗",
        "items": [
            {"dish_id": dish_id, "quantity": 30, "notes": "堂食 30 份"},
        ],
    }
    batch_resp = client.post("/api/dishes/daily_consumption/batch", json=batch_payload, headers=_staff_headers())
    assert batch_resp.status_code == 200, batch_resp.text
    assert batch_resp.json()["status"] == "success"

    # 4. 验证 SKU 库存已即时减少为 5kg (20 - 15 = 5)
    sku_row = db.get_sku(sku_beef)
    assert sku_row.current_stock == 5.0

    # 5. 按日期查询当日消耗及汇总
    query_resp = client.get("/api/dishes/daily_consumption?date=2026-08-27", headers=_staff_headers())
    assert query_resp.status_code == 200
    res_data = query_resp.json()["data"]
    assert res_data["date"] == "2026-08-27"
    assert len(res_data["consumptions"]) == 1
    cons = res_data["consumptions"][0]
    assert cons["dish_id"] == dish_id
    assert cons["dish_name"] == "小炒黄牛肉"
    assert cons["quantity"] == 30.0
    assert cons["total_cost"] == 650.0
    assert round(cons["unit_cost"], 2) == 21.67
    assert len(cons["details"]) == 2  # 跨两个批次扣减

    # 验证当日财务汇总
    summary = res_data["summary"]
    assert summary["total_quantity"] == 30.0
    assert summary["total_cost"] == 650.0
    assert summary["total_revenue"] == 30 * 45.0  # 1350.0
    assert summary["gross_profit"] == 1350.0 - 650.0  # 700.0
    assert round(summary["gross_margin_rate"], 2) == round(700.0 / 1350.0 * 100, 2)  # 51.85%


def test_void_daily_consumption_and_rollback():
    from app.services.costing_service import CostingService

    # 1. 准备食材与批次
    sku_id, _ = db.create_sku("大闸蟹", base_unit="只")
    session = db.get_session()
    try:
        CostingService.record_inbound_batch(session, sku_id, 10.0, 30.0, "只", date="2026-08-27")
        session.commit()
    finally:
        session.close()

    db.update_sku(sku_id, current_stock=10.0, last_unit_price=30.0)

    # 2. 创建餐品与录入消耗
    dish_resp = client.post("/api/dishes", json={
        "name": "清蒸大闸蟹",
        "price": 58.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 2.0, "unit": "只"}],
    }, headers=_owner_headers())
    dish_id = dish_resp.json()["data"]["id"]

    # 消耗 3 份 (耗 6 只)
    batch_resp = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-27",
        "items": [{"dish_id": dish_id, "quantity": 3}],
    }, headers=_staff_headers())
    assert batch_resp.status_code == 200

    # 验证扣减后库存为 4 只
    assert db.get_sku(sku_id).current_stock == 4.0

    # 获取消耗记录 ID
    cons_list = client.get("/api/dishes/daily_consumption?date=2026-08-27", headers=_staff_headers()).json()["data"]["consumptions"]
    cons_id = cons_list[0]["id"]

    # 3. 冲销作废该消耗记录 (void)
    void_resp = client.post(f"/api/dishes/daily_consumption/{cons_id}/void", headers=_owner_headers())
    assert void_resp.status_code == 200
    assert void_resp.json()["status"] == "success"

    # 4. 验证库存回滚为 10 只
    assert db.get_sku(sku_id).current_stock == 10.0

    # 验证批次剩余量回滚为 10 只且未关闭
    s = db.get_session()
    try:
        batches = CostingService.get_active_batches(s, sku_id)
        assert len(batches) == 1
        assert batches[0].remaining_qty == 10.0
        assert batches[0].is_closed == 0
    finally:
        s.close()

    # 5. 验证冲销后查询：is_void=1 且汇总金额不计入该条
    query_resp = client.get("/api/dishes/daily_consumption?date=2026-08-27", headers=_staff_headers())
    summary = query_resp.json()["data"]["summary"]
    assert summary["total_quantity"] == 0.0
    assert summary["total_cost"] == 0.0


def test_cost_analysis_endpoint():
    from app.services.costing_service import CostingService

    # 1. 准备食材与 3 个不同日期的进货与消耗
    sku_id, _ = db.create_sku("三文鱼", base_unit="kg")
    session = db.get_session()
    try:
        # 3 个进货批次 (进价上涨)
        CostingService.record_inbound_batch(session, sku_id, 10.0, 100.0, "kg", date="2026-08-20")
        CostingService.record_inbound_batch(session, sku_id, 10.0, 120.0, "kg", date="2026-08-22")
        CostingService.record_inbound_batch(session, sku_id, 10.0, 150.0, "kg", date="2026-08-25")
        session.commit()
    finally:
        session.close()

    db.update_sku(sku_id, current_stock=30.0, last_unit_price=150.0)

    dish_resp = client.post("/api/dishes", json={
        "name": "三文鱼刺身",
        "price": 88.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.2, "unit": "kg"}],
    }, headers=_owner_headers())
    dish_id = dish_resp.json()["data"]["id"]

    # 第 1 天 (8-21): 消耗 10 份 (2kg @100 = 200元, 单份 20元)
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-21",
        "items": [{"dish_id": dish_id, "quantity": 10}],
    }, headers=_staff_headers())

    # 第 2 天 (8-23): 消耗 45 份 (9kg: 8kg @100 + 1kg @120 = 800 + 120 = 920元, 单份 20.44元)
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-23",
        "items": [{"dish_id": dish_id, "quantity": 45}],
    }, headers=_staff_headers())

    # 调用成本分析端点
    analysis_resp = client.get("/api/dishes/cost_analysis?days=30", headers=_staff_headers())
    assert analysis_resp.status_code == 200
    analysis = analysis_resp.json()["data"]
    assert "summary" in analysis
    assert "dishes" in analysis
    assert len(analysis["dishes"]) >= 1

    dish_stat = next(d for d in analysis["dishes"] if d["id"] == dish_id)
    assert dish_stat["name"] == "三文鱼刺身"
    assert dish_stat["total_sold_quantity"] == 55.0
    assert dish_stat["total_cost"] == 1120.0
    assert dish_stat["total_revenue"] == 55 * 88.0
    assert len(dish_stat["history"]) == 2


def test_rbac_dishes_permissions():
    # 店员试图新增餐品 -> 403
    resp = client.post("/api/dishes", json={"name": "无权限餐品", "price": 10}, headers=_staff_headers())
    assert resp.status_code == 403

    # 店员试图冲销 -> 403
    resp_void = client.post("/api/dishes/daily_consumption/999/void", headers=_staff_headers())
    assert resp_void.status_code == 403
