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
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)


def _recent_date(days_ago: int) -> str:
    """返回距今 days_ago 天的日期字符串（YYYY-MM-DD）。

    why：/api/dishes/cost_analysis 以 datetime.now() 反推「近 N 天」窗口
    （start = now - (days-1)）。用例若写死绝对日期，当前日期一旦越过窗口，
    消耗记录就落不进查询范围，断言随日期推移必然失败（时间炸弹）。
    改为按当前时间相对构造，任何日期运行都落在窗口内。
    """
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

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
    # 日期一律按当前时间相对构造，保证落在 cost_analysis 的近 30 天窗口内，
    # 且保持「进货 → 消耗 → 进货 → 消耗 → 进货」的相对先后顺序（FIFO 口径不变）。
    sku_id, _ = db.create_sku("三文鱼", base_unit="kg")
    session = db.get_session()
    try:
        # 3 个进货批次 (进价上涨)
        CostingService.record_inbound_batch(session, sku_id, 10.0, 100.0, "kg", date=_recent_date(27))
        CostingService.record_inbound_batch(session, sku_id, 10.0, 120.0, "kg", date=_recent_date(25))
        CostingService.record_inbound_batch(session, sku_id, 10.0, 150.0, "kg", date=_recent_date(22))
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

    # 第 1 天: 消耗 10 份 (2kg @100 = 200元, 单份 20元)
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": _recent_date(26),
        "items": [{"dish_id": dish_id, "quantity": 10}],
    }, headers=_staff_headers())

    # 第 2 天: 消耗 45 份 (9kg: 8kg @100 + 1kg @120 = 800 + 120 = 920元, 单份 20.44元)
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": _recent_date(24),
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


def test_dish_categories_list_and_delete():
    # 1. 准备食材 SKU 与两个分类的餐品
    sku_id, _ = db.create_sku("测试食材", base_unit="kg")
    db.update_sku(sku_id, last_unit_price=10.0, current_stock=20.0)

    dish_a = client.post("/api/dishes", json={
        "name": "分类菜甲", "category": "主食热菜", "price": 20.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 1.0, "unit": "kg"}],
    }, headers=_owner_headers()).json()
    assert dish_a["status"] == "success"
    dish_b = client.post("/api/dishes", json={
        "name": "分类菜乙", "category": "主食热菜", "price": 22.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 1.0, "unit": "kg"}],
    }, headers=_owner_headers()).json()
    assert dish_b["status"] == "success"
    dish_c = client.post("/api/dishes", json={
        "name": "分类菜丙", "category": "主菜", "price": 25.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 1.0, "unit": "kg"}],
    }, headers=_owner_headers()).json()
    assert dish_c["status"] == "success"

    # 2. GET /api/dishes/categories：distinct 非空分类 + 引用数，按引用数降序
    cats_resp = client.get("/api/dishes/categories", headers=_staff_headers())
    assert cats_resp.status_code == 200
    data = cats_resp.json()["data"]
    by_name = {c["name"]: c["count"] for c in data}
    assert by_name.get("主食热菜") == 2
    assert by_name.get("主菜") == 1
    assert "" not in by_name

    # 3. 店员无权限删除分类 -> 403
    del_staff = client.delete("/api/dishes/categories/%E4%B8%BB%E8%8F%9C",
                              headers=_staff_headers())
    assert del_staff.status_code == 403

    # 4. owner 删除「主菜」（0 引用场景单独覆盖：先建再删），受影响条数正确
    del_resp = client.delete("/api/dishes/categories/%E4%B8%BB%E8%8F%9C", headers=_owner_headers())
    assert del_resp.status_code == 200
    body = del_resp.json()
    assert body["status"] == "success"
    assert body["data"]["affected"] == 1
    assert body["data"]["replace_with"] == "其他"
    # 被引用餐品 category 已改写为「其他」
    updated = client.get(f"/api/dishes/{dish_c['data']['id']}", headers=_staff_headers()).json()["data"]
    assert updated["category"] == "其他"
    # 删除后 categories 集合不再包含该分类
    after = client.get("/api/dishes/categories", headers=_staff_headers()).json()["data"]
    assert not any(c["name"] == "主菜" for c in after)

    # 5. replace_with 参数生效：把「主食热菜」改为「主食」
    del2 = client.delete(
        "/api/dishes/categories/%E4%B8%BB%E9%A3%9F%E7%83%AD%E8%8F%9C?replace_with=%E4%B8%BB%E9%A3%9F",
        headers=_owner_headers())
    assert del2.status_code == 200
    assert del2.json()["data"]["affected"] == 2
    assert del2.json()["data"]["replace_with"] == "主食"
    updated_b = client.get(f"/api/dishes/{dish_b['data']['id']}", headers=_staff_headers()).json()["data"]
    assert updated_b["category"] == "主食"


def test_delete_category_empty_name_returns_400():
    # 空名 / 纯空白名（%20）删除分类应返回 400，而非 200+status:error
    for empty in ("%20", "%20%20"):
        resp = client.delete(f"/api/dishes/categories/{empty}", headers=_owner_headers())
        assert resp.status_code == 400
        body = resp.json()
        assert body["status"] == "error"
        assert body["msg"] == "分类名称不能为空"


def test_cross_tenant_dish_creation_and_update_isolation():
    """[AC-2.1, AC-2.2] 跨租户创建与更新配方物理隔离阻断。"""
    # 租户 A (tenant_a) 创建食材 SKU
    headers_a = {"X-Role": "owner", "X-Email": "boss@tenant-a.hk", "X-Tenant-Id": "tenant_a"}
    headers_b = {"X-Role": "owner", "X-Email": "boss@tenant-b.hk", "X-Tenant-Id": "tenant_b"}

    resp_sku_a = client.post("/api/inventory/skus", json={"name": "租户A秘制酱汁", "base_unit": "kg"}, headers=headers_a)
    assert resp_sku_a.status_code == 200, resp_sku_a.text
    sku_a_id = resp_sku_a.json()["id"]
    db.update_sku(sku_a_id, current_stock=10.0, last_unit_price=20.0)

    # 1. 租户 B 尝试创建配方引用租户 A 的 SKU -> 400
    create_payload_b = {
        "name": "偷师菜品",
        "category": "主菜",
        "price": 50.0,
        "ingredients": [{"sku_id": sku_a_id, "consumption_qty": 0.2, "unit": "kg"}],
    }
    resp_b = client.post("/api/dishes", json=create_payload_b, headers=headers_b)
    assert resp_b.status_code == 400
    err_data = resp_b.json()
    assert err_data["status"] == "error"
    assert err_data["code"] == "TENANT_SKU_NOT_FOUND"
    assert f"配方中的食材 SKU #{sku_a_id} 不存在或无权使用" in err_data["msg"]

    # 2. 租户 B 创建自己的正常菜品
    resp_sku_b = client.post("/api/inventory/skus", json={"name": "租户B自制豆腐", "base_unit": "kg"}, headers=headers_b)
    assert resp_sku_b.status_code == 200, resp_sku_b.text
    sku_b_id = resp_sku_b.json()["id"]
    db.update_sku(sku_b_id, current_stock=10.0, last_unit_price=5.0)

    resp_dish_b = client.post("/api/dishes", json={
        "name": "租户B家常豆腐",
        "category": "热菜",
        "price": 30.0,
        "ingredients": [{"sku_id": sku_b_id, "consumption_qty": 0.5, "unit": "kg"}],
    }, headers=headers_b)
    assert resp_dish_b.status_code == 200
    dish_b_id = resp_dish_b.json()["data"]["id"]

    # 3. 租户 B 尝试更新配方追加租户 A 的 SKU -> 400
    update_payload_b = {
        "ingredients": [
            {"sku_id": sku_b_id, "consumption_qty": 0.5, "unit": "kg"},
            {"sku_id": sku_a_id, "consumption_qty": 0.1, "unit": "kg"},
        ]
    }
    resp_update = client.put(f"/api/dishes/{dish_b_id}", json=update_payload_b, headers=headers_b)
    assert resp_update.status_code == 400
    assert resp_update.json()["code"] == "TENANT_SKU_NOT_FOUND"


def test_cross_dimension_unit_rejection():
    """[AC-5.1, AC-5.2] 配方建档与更新跨量纲单位前置拦截。"""
    headers = _owner_headers()

    # 创建重量基准的食材
    resp_sku = client.post("/api/inventory/skus", json={"name": "测试五花肉", "base_unit": "kg"}, headers=headers)
    assert resp_sku.status_code == 200, resp_sku.text
    sku_id = resp_sku.json()["id"]
    db.update_sku(sku_id, current_stock=20.0, last_unit_price=30.0)

    # 1. 尝试用计件单位「份」配置重量单位「kg」的食材 -> 400
    resp_create = client.post("/api/dishes", json={
        "name": "跨量纲红烧肉",
        "price": 48.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 1.0, "unit": "份"}],
    }, headers=headers)
    assert resp_create.status_code == 400
    body = resp_create.json()
    assert body["code"] == "UNIT_DIMENSION_MISMATCH"
    assert "无法换算" in body["msg"]

    # 2. 合法同量纲重量单位「g」建档 -> 200
    resp_create_ok = client.post("/api/dishes", json={
        "name": "合规红烧肉",
        "price": 48.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 250.0, "unit": "g"}],
    }, headers=headers)
    assert resp_create_ok.status_code == 200
    dish_id = resp_create_ok.json()["data"]["id"]

    # 3. 尝试更新为体积单位「升」-> 400
    resp_update = client.put(f"/api/dishes/{dish_id}", json={
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.5, "unit": "升"}],
    }, headers=headers)
    assert resp_update.status_code == 400
    assert resp_update.json()["code"] == "UNIT_DIMENSION_MISMATCH"


def test_negative_and_zero_quantity_rejection_in_batch():
    """[AC-3.2] 后端餐品批量消耗负数与零份数拦截拒绝及原子性回滚。"""
    headers_owner = _owner_headers()
    headers_staff = _staff_headers()

    sku_id, _ = db.create_sku("测试鸡胸肉", base_unit="kg")
    db.update_sku(sku_id, current_stock=20.0, last_unit_price=20.0)

    # 建立两个餐品
    d1_resp = client.post("/api/dishes", json={
        "name": "鸡胸肉沙拉", "price": 38.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.2, "unit": "kg"}]
    }, headers=headers_owner).json()
    d1_id = d1_resp["data"]["id"]

    d2_resp = client.post("/api/dishes", json={
        "name": "香煎鸡胸肉", "price": 42.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.3, "unit": "kg"}]
    }, headers=headers_owner).json()
    d2_id = d2_resp["data"]["id"]

    # 1. 提交空 items 列表 -> 400
    resp_empty = client.post("/api/dishes/daily_consumption/batch", json={"items": []}, headers=headers_staff)
    assert resp_empty.status_code == 400
    assert resp_empty.json()["code"] == "EMPTY_CONSUMPTION_ITEMS"

    # 2. 负数消耗：d1 负数, d2 正数 -> 原子拒绝，d2 亦不被扣减
    resp_neg = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-25",
        "items": [
            {"dish_id": d1_id, "quantity": -2.0},
            {"dish_id": d2_id, "quantity": 5.0},
        ]
    }, headers=headers_staff)
    assert resp_neg.status_code == 400
    body = resp_neg.json()
    assert body["code"] == "INVALID_CONSUMPTION_QUANTITY"
    assert "鸡胸肉沙拉" in body["msg"]
    assert "不能填负数或 0" in body["msg"]

    # 验证库存 20.0 毫厘未动
    sku = db.get_sku(sku_id)
    assert sku.current_stock == 20.0


def test_decimal_half_portion_consumption():
    """[AC-4.3, AC-4.4] 小数/半份（0.5 份）餐品消耗提交与后端无损接收回滚。"""
    from app.services.costing_service import CostingService
    headers_owner = _owner_headers()
    headers_staff = _staff_headers()

    session = db.get_session()
    try:
        sku_id, _ = db.create_sku("半份鲈鱼", base_unit="kg")
        CostingService.record_inbound_batch(
            session=session, sku_id=sku_id, qty=10.0, unit_price=60.0, unit="kg", date="2026-08-20"
        )
        sku = session.get(db._SkuRow, sku_id)
        sku.current_stock = 10.0
        session.commit()
    finally:
        session.close()

    # 创建餐品：清蒸鲈鱼，每份用鲈鱼 0.6kg
    dish_resp = client.post("/api/dishes", json={
        "name": "清蒸鲈鱼",
        "price": 88.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.6, "unit": "kg"}]
    }, headers=headers_owner).json()
    dish_id = dish_resp["data"]["id"]

    # 售出 0.5 份 (半份)
    resp_consume = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-26",
        "items": [{"dish_id": dish_id, "quantity": 0.5}]
    }, headers=headers_staff)
    assert resp_consume.status_code == 200, resp_consume.text
    cons_id = resp_consume.json()["data"]["consumption_ids"][0]

    # 验证消耗用量：0.6 * 0.5 = 0.3kg, 库存从 10.0 变 9.7kg
    sku = db.get_sku(sku_id)
    assert abs(sku.current_stock - 9.7) < 1e-4

    # 验证主记录中的 quantity 值为 0.5
    cons_list = client.get("/api/dishes/daily_consumption?date=2026-08-26", headers=headers_staff).json()["data"]["consumptions"]
    assert len(cons_list) == 1
    assert cons_list[0]["quantity"] == 0.5
    assert cons_list[0]["details"][0]["qty_consumed"] == 0.3

    # 冲销回滚验证
    resp_void = client.post(f"/api/dishes/daily_consumption/{cons_id}/void", headers=headers_owner)
    assert resp_void.status_code == 200
    sku = db.get_sku(sku_id)
    assert abs(sku.current_stock - 10.0) < 1e-4


def test_zero_cost_and_estimated_batches_transparency():
    """[AC-6.1, AC-6.2, AC-6.3] 零成本与超卖暂估批次透传及数据完整性预警。"""
    headers_owner = _owner_headers()
    headers_staff = _staff_headers()

    # 创建一个没有任何进货单价和入库批次的食材 (库存=0, last_unit_price=0)
    sku_id, _ = db.create_sku("新到野生菌", base_unit="kg")

    dish_resp = client.post("/api/dishes", json={
        "name": "野菌炖鸡",
        "price": 128.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.2, "unit": "kg"}]
    }, headers=headers_owner).json()
    dish_id = dish_resp["data"]["id"]

    # 消耗该餐品 1 份
    resp_consume = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-08-27",
        "items": [{"dish_id": dish_id, "quantity": 1.0}]
    }, headers=headers_staff)
    assert resp_consume.status_code == 200

    # 查询当日消耗
    resp_daily = client.get("/api/dishes/daily_consumption?date=2026-08-27", headers=headers_staff)
    assert resp_daily.status_code == 200
    data = resp_daily.json()["data"]

    summary = data["summary"]
    assert summary["has_zero_cost_batch"] is True
    assert summary["data_integrity_status"] == "zero_cost_alert"
    assert "包含未录入进货价的食材" in summary["data_integrity_msg"]

    detail_item = data["consumptions"][0]["details"][0]
    assert detail_item["is_estimated"] == 1
    assert detail_item["is_zero_cost"] is True


def test_cost_analysis_cross_tenant_zero_leakage():
    """[AC-2.4] 成本大盘分析跨租户零泄漏验证。"""
    headers_a = {"X-Role": "owner", "X-Email": "boss@tenant-a.hk", "X-Tenant-Id": "tenant_a"}
    headers_b = {"X-Role": "owner", "X-Email": "boss@tenant-b.hk", "X-Tenant-Id": "tenant_b"}

    # 租户 A 建立食材与餐品并消耗
    sku_a_id, _ = db.create_sku("租户A牛肉", base_unit="kg", tenant_id="tenant_a")
    db.update_sku(sku_a_id, current_stock=10.0, last_unit_price=50.0)
    dish_a = client.post("/api/dishes", json={
        "name": "租户A牛肉饭", "price": 60.0,
        "ingredients": [{"sku_id": sku_a_id, "consumption_qty": 0.3, "unit": "kg"}]
    }, headers=headers_a).json()["data"]
    # 消耗日期按「今天」相对构造：cost_analysis 以近 7 天窗口过滤，写死绝对日期
    # 会随当前日期越过窗口而失败（时间炸弹）。
    consume_date = _recent_date(0)
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": consume_date, "items": [{"dish_id": dish_a["id"], "quantity": 2.0}]
    }, headers=headers_a)

    # 租户 B 建立食材与餐品并消耗
    sku_b_id, _ = db.create_sku("租户B秘制烤鸭", base_unit="kg", tenant_id="tenant_b")
    db.update_sku(sku_b_id, current_stock=10.0, last_unit_price=80.0)
    dish_b = client.post("/api/dishes", json={
        "name": "租户B烤鸭套餐", "price": 120.0,
        "ingredients": [{"sku_id": sku_b_id, "consumption_qty": 0.5, "unit": "kg"}]
    }, headers=headers_b).json()["data"]
    client.post("/api/dishes/daily_consumption/batch", json={
        "date": consume_date, "items": [{"dish_id": dish_b["id"], "quantity": 3.0}]
    }, headers=headers_b)

    # 租户 A 查询成本大盘分析
    resp_analysis_a = client.get("/api/dishes/cost_analysis?days=7", headers=headers_a)
    assert resp_analysis_a.status_code == 200
    data_a = resp_analysis_a.json()["data"]

    # 验证仅包含租户 A 的餐品数据，绝对不包含租户 B
    dishes_in_a = [d["name"] for d in data_a["dishes"]]
    assert "租户A牛肉饭" in dishes_in_a
    assert "租户B烤鸭套餐" not in dishes_in_a
    assert data_a["summary"]["total_sold_quantity"] == 2.0

