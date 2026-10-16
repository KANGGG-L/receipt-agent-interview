# -*- coding: utf-8 -*-
"""UAT-021: 店员库存列表与餐品列表泄露价格及毛利 定向测试。

验收准则 (AC):
1. 真实 staff 凭据请求 inventory/dishes 列表，读完整 JSON:
   - last_unit_price, price, theoretical_cost, gross_profit, gross_margin_rate 及所有同义敏感字段 null 或不返回；
2. 详情继续脱敏 (inventory sku 详情与 dish 详情中上述敏感字段依然为 null)；
3. owner 同 fixture 价格毛利正常可见；
4. 伪造 owner 头 (带 staff 凭据时加 X-Role: owner) 不能突破脱敏；
5. staff 仍可看数量与单位，不靠隐藏导航代 API 权限；
6. 跨 tenant 数据不可读取写入，B 租户 baseline 无污染。
"""

import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import auth, db
from app.services.costing_service import CostingService
from _b2_helpers import hdr, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _setup_inventory_and_dish(client, tn, sku_name="料-招牌菜", dish_name="招牌菜",
                              unit_price=10.0, stock=50.0, dish_price=30.0):
    sku, _ = db.create_sku(sku_name, base_unit="斤", tenant_id=tn)
    db.update_sku(sku, current_stock=stock, last_unit_price=unit_price)
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(
            s, sku, stock, unit_price, "斤", date=date.today().isoformat())
        s.commit()
    finally:
        s.close()
    r = client.post("/api/dishes", headers=hdr(tn, "owner"), json={
        "name": dish_name,
        "price": dish_price,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.5, "unit": "斤"}]
    })
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    dish_id = r.json()["data"]["id"]
    return sku, dish_id


def test_staff_inventory_list_price_masked(client):
    """AC 1 & 5: staff 读取库存列表时，last_unit_price/price_anomaly/vs_avg_pct 脱敏为 null/False，但数量和单位可见。"""
    tn = new_tenant()
    sku_id, _ = _setup_inventory_and_dish(client, tn, unit_price=15.0, stock=40.0)

    # staff 角色读取库存列表
    res = client.get("/api/inventory", headers=hdr(tn, "staff"))
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["status"] == "success"
    skus = data["data"]
    matched = [s for s in skus if s["id"] == sku_id]
    assert len(matched) == 1
    s = matched[0]

    # 敏感价格字段必须为 null 或不返回
    assert s.get("last_unit_price") is None, f"staff should not see last_unit_price, got: {s.get('last_unit_price')}"
    assert s.get("price_anomaly") is False or s.get("price_anomaly") is None
    assert s.get("vs_avg_pct") is None
    assert s.get("ack_action") is None

    # 非敏感字段必须保留：品名、数量、单位、警戒线、kg 折算
    assert s["name"] == "料-招牌菜"
    assert s["base_unit"] == "斤"
    assert s["current_stock"] == 40.0

    # meta 区域脱敏
    meta = data.get("meta", {})
    assert meta.get("price_anomaly_count") in (0, None)
    assert meta.get("price_anomaly_threshold_pct") is None
    assert meta.get("price_anomaly_ack_map") in ({}, None)


def test_owner_inventory_list_price_visible(client):
    """AC 3: owner 读取同一库存列表时，价格与毛利正常可见。"""
    tn = new_tenant()
    sku_id, _ = _setup_inventory_and_dish(client, tn, unit_price=15.0, stock=40.0)

    res = client.get("/api/inventory", headers=hdr(tn, "owner"))
    assert res.status_code == 200, res.text
    skus = res.json()["data"]
    s = [x for x in skus if x["id"] == sku_id][0]
    assert s["last_unit_price"] == 15.0
    assert s["current_stock"] == 40.0


def test_inventory_sku_detail_remains_masked_for_staff(client):
    """AC 2: 详情继续脱敏 (GET /api/inventory/skus/{id})。"""
    tn = new_tenant()
    sku_id, _ = _setup_inventory_and_dish(client, tn, unit_price=15.0, stock=40.0)

    res = client.get(f"/api/inventory/skus/{sku_id}", headers=hdr(tn, "staff"))
    assert res.status_code == 200, res.text
    sku_data = res.json()["data"]
    assert sku_data["prices_visible"] is False
    assert sku_data["last_unit_price"] is None
    assert sku_data["avg_cost"] is None
    assert sku_data["vs_avg_pct"] is None
    assert sku_data["latest_price"] is None
    assert sku_data["current_stock"] == 40.0
    assert sku_data["base_unit"] == "斤"
    for b in sku_data.get("batches", []):
        assert b.get("unit_cost") is None


def test_staff_dishes_list_and_detail_price_and_margin_masked(client):
    """AC 1, 2 & 5: staff 读取餐品列表与餐品详情时，price/cost/gross_profit/gross_margin_rate 全部脱敏为 null。"""
    tn = new_tenant()
    sku_id, dish_id = _setup_inventory_and_dish(client, tn, unit_price=10.0, stock=50.0, dish_price=30.0)

    # 1. 列表脱敏
    res_list = client.get("/api/dishes", headers=hdr(tn, "staff"))
    assert res_list.status_code == 200, res_list.text
    dishes = res_list.json()["data"]
    matched = [d for d in dishes if d["id"] == dish_id]
    assert len(matched) == 1
    d = matched[0]

    assert d.get("price") is None, f"staff should not see dish price, got: {d.get('price')}"
    assert d.get("theoretical_cost") is None, f"staff should not see theoretical_cost, got: {d.get('theoretical_cost')}"
    assert d.get("gross_profit") is None, f"staff should not see gross_profit, got: {d.get('gross_profit')}"
    assert d.get("gross_margin_rate") is None, f"staff should not see gross_margin_rate, got: {d.get('gross_margin_rate')}"

    # 配方组件明细中的敏感单价与成本必须为 null
    ings = d.get("ingredients", [])
    assert len(ings) == 1
    ing = ings[0]
    assert ing.get("sku_last_unit_price") is None, f"staff should not see sku_last_unit_price, got: {ing.get('sku_last_unit_price')}"
    assert ing.get("ingredient_cost") is None, f"staff should not see ingredient_cost, got: {ing.get('ingredient_cost')}"
    # 非敏感字段保留：用量、单位、名称、出成率
    assert ing["consumption_qty"] == 0.5
    assert ing["unit"] == "斤"
    assert ing["sku_name"] == "料-招牌菜"

    # 2. 详情脱敏
    res_detail = client.get(f"/api/dishes/{dish_id}", headers=hdr(tn, "staff"))
    assert res_detail.status_code == 200, res_detail.text
    detail = res_detail.json()["data"]
    assert detail.get("price") is None
    assert detail.get("theoretical_cost") is None
    assert detail.get("gross_profit") is None
    assert detail.get("gross_margin_rate") is None
    detail_ing = detail.get("ingredients", [])[0]
    assert detail_ing.get("sku_last_unit_price") is None
    assert detail_ing.get("ingredient_cost") is None
    assert detail_ing["consumption_qty"] == 0.5
    assert detail_ing["unit"] == "斤"


def test_owner_dishes_list_and_detail_price_and_margin_visible(client):
    """AC 3: owner 读取同一餐品列表与详情时，价格与毛利正常可见。"""
    tn = new_tenant()
    sku_id, dish_id = _setup_inventory_and_dish(client, tn, unit_price=10.0, stock=50.0, dish_price=30.0)

    # 列表可见
    res_list = client.get("/api/dishes", headers=hdr(tn, "owner"))
    assert res_list.status_code == 200, res_list.text
    d = [x for x in res_list.json()["data"] if x["id"] == dish_id][0]
    assert d["price"] == 30.0
    assert d["theoretical_cost"] == 5.0
    assert d["gross_profit"] == 25.0
    assert d["gross_margin_rate"] == 83.33
    ing = d["ingredients"][0]
    assert ing["sku_last_unit_price"] == 10.0
    assert ing["ingredient_cost"] == 5.0

    # 详情可见
    res_detail = client.get(f"/api/dishes/{dish_id}", headers=hdr(tn, "owner"))
    assert res_detail.status_code == 200, res_detail.text
    detail = res_detail.json()["data"]
    assert detail["price"] == 30.0
    assert detail["theoretical_cost"] == 5.0
    assert detail["gross_profit"] == 25.0
    assert detail["gross_margin_rate"] == 83.33


def test_fake_owner_header_cannot_bypass_staff_cookie(client):
    """AC 4: 伪造 owner 请求头不能突破已认证的 staff 凭证。"""
    tn = "default"  # 默认预置账号租户
    sku_id, dish_id = _setup_inventory_and_dish(client, tn, unit_price=20.0, stock=30.0, dish_price=50.0)

    # 生成真实的 staff 签名 Token
    staff_token = auth.issue_token("staff@demo.hk", "staff", tn)
    cookies = {auth.SESSION_COOKIE: staff_token}

    # 攻击场景：携带真实 staff Cookie，但 header 注入 X-Role: owner 企图提权
    attack_headers = {"X-Role": "owner", "X-Tenant-Id": tn}

    # 1. 验证库存列表
    inv_res = client.get("/api/inventory", cookies=cookies, headers=attack_headers)
    assert inv_res.status_code == 200
    skus = inv_res.json()["data"]
    s = [x for x in skus if x["id"] == sku_id][0]
    assert s.get("last_unit_price") is None, "fake owner header must not leak last_unit_price"
    assert s.get("price_anomaly") is False or s.get("price_anomaly") is None

    # 2. 验证餐品列表
    dish_res = client.get("/api/dishes", cookies=cookies, headers=attack_headers)
    assert dish_res.status_code == 200
    d = [x for x in dish_res.json()["data"] if x["id"] == dish_id][0]
    assert d.get("price") is None, "fake owner header must not leak dish price"
    assert d.get("gross_profit") is None, "fake owner header must not leak gross_profit"

    # 3. 验证餐品详情
    detail_res = client.get(f"/api/dishes/{dish_id}", cookies=cookies, headers=attack_headers)
    assert detail_res.status_code == 200
    detail = detail_res.json()["data"]
    assert detail.get("price") is None, "fake owner header must not leak dish detail price"
    assert detail.get("theoretical_cost") is None, "fake owner header must not leak theoretical_cost"


def test_tenant_isolation_and_b_baseline_unpolluted(client):
    """AC 6: 跨租户数据隔离，B 租户 baseline 无污染。"""
    tn_a = new_tenant()
    tn_b = new_tenant()

    # 租户 A 建档
    sku_a, dish_a = _setup_inventory_and_dish(client, tn_a, sku_name="A专属食材", dish_name="A专属菜", unit_price=12.0)
    # 租户 B 建档
    sku_b, dish_b = _setup_inventory_and_dish(client, tn_b, sku_name="B专属食材", dish_name="B专属菜", unit_price=88.0)

    # 租户 A 的 staff 查询 inventory/dishes 绝不可看到租户 B
    res_a_inv = client.get("/api/inventory", headers=hdr(tn_a, "staff"))
    inv_ids_a = [x["id"] for x in res_a_inv.json()["data"]]
    assert sku_a in inv_ids_a
    assert sku_b not in inv_ids_a

    res_a_dish = client.get("/api/dishes", headers=hdr(tn_a, "staff"))
    dish_ids_a = [x["id"] for x in res_a_dish.json()["data"]]
    assert dish_a in dish_ids_a
    assert dish_b not in dish_ids_a

    # 租户 A 的 staff 跨租户查详情返回 404
    assert client.get(f"/api/dishes/{dish_b}", headers=hdr(tn_a, "staff")).status_code == 404
    assert client.get(f"/api/inventory/skus/{sku_b}", headers=hdr(tn_a, "staff")).status_code == 404
