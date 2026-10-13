# -*- coding: utf-8 -*-
"""B2 补审 api_dishes：BOM 版本、成本核算、每日消耗幂等、软删、租户隔离（B2-1 ~ B2-8）。"""

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db
from _b2_helpers import hdr, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _stocked_dish(client, tn, name="测试菜", price=30.0, stock=50.0):
    sku, _ = db.create_sku("料-" + name, base_unit="斤", tenant_id=tn)
    db.update_sku(sku, current_stock=stock, last_unit_price=10.0)
    s = db.get_session()
    try:
        from app.services.costing_service import CostingService
        CostingService.record_inbound_batch(s, sku, stock, 10.0, "斤", date=date.today().isoformat())
        s.commit()
    finally:
        s.close()
    r = client.post("/api/dishes", headers=hdr(tn), json={
        "name": name, "price": price,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.5, "unit": "斤"}]})
    assert r.status_code == 200 and r.json()["status"] == "success", r.text
    return r.json()["data"]["id"], sku


def _consume(client, tn, dish_id, qty=2, role="staff", **extra):
    body = {"items": [{"dish_id": dish_id, "quantity": qty}]}
    body.update(extra)
    return client.post("/api/dishes/daily_consumption/batch", headers=hdr(tn, role), json=body)


def test_same_request_id_is_applied_once(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn)
    r1 = _consume(client, tn, dish, 2, request_id="abc-123")
    assert r1.status_code == 200, r1.text
    r2 = _consume(client, tn, dish, 2, request_id="abc-123")
    assert r2.status_code == 200 and r2.json()["data"]["replayed"] is True
    assert r2.json()["data"]["consumption_ids"] == r1.json()["data"]["consumption_ids"]
    assert db.get_sku(sku, tenant_id=tn).current_stock == 49.0       # 只扣了 2 份 x 0.5 斤 一次
    # 不同的 request_id 视为新的一单，沿用同日同餐品合并累加
    r3 = _consume(client, tn, dish, 2, request_id="abc-124")
    assert r3.json()["data"]["merged"] is True and db.get_sku(sku, tenant_id=tn).current_stock == 48.0
    # 别的租户用同一个 request_id 互不影响
    other = new_tenant()
    d2, s2 = _stocked_dish(client, other, "他家菜")
    assert _consume(client, other, d2, 2, request_id="abc-123").json()["data"].get("replayed") is None


def test_invalid_inputs_rejected_with_plain_messages(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn)
    r = _consume(client, tn, dish, 1, date="banana")
    assert r.status_code == 400 and r.json()["code"] == "INVALID_CONSUMPTION_DATE"
    future = (date.today() + timedelta(days=3)).isoformat()
    assert _consume(client, tn, dish, 1, date=future).status_code == 400
    r = client.post("/api/dishes/daily_consumption/batch", headers=hdr(tn, "staff"),
                    content='{"items":[{"dish_id":%d,"quantity":NaN}]}' % dish)
    assert r.status_code in (400, 422)
    assert db.get_sku(sku, tenant_id=tn).current_stock == 50.0


def test_inactive_dish_cannot_be_consumed(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn)
    assert client.delete("/api/dishes/%d" % dish, headers=hdr(tn)).json()["action"] == "DEACTIVATED"
    r = _consume(client, tn, dish, 1)
    assert r.status_code == 400 and r.json()["code"] == "DISH_INACTIVE" and "停用" in r.json()["msg"]
    assert db.get_sku(sku, tenant_id=tn).current_stock == 50.0


def test_dish_status_and_price_validation(client):
    tn = new_tenant()
    bad = client.post("/api/dishes", headers=hdr(tn), json={"name": "坏菜", "status": "weird"})
    assert bad.status_code == 400
    bad = client.post("/api/dishes", headers=hdr(tn), content='{"name":"坏菜2","price":NaN}')
    assert bad.status_code in (400, 422)
    dish, _ = _stocked_dish(client, tn, "好菜")
    assert client.put("/api/dishes/%d" % dish, headers=hdr(tn), json={"status": "weird"}).status_code == 400


def test_hard_delete_blocked_when_used_as_sub_recipe_or_has_history(client):
    tn = new_tenant()
    sub, sku = _stocked_dish(client, tn, "子配方")
    parent = client.post("/api/dishes", headers=hdr(tn), json={
        "name": "总菜", "price": 50, "ingredients": [
            {"component_type": "dish", "component_id": sub, "consumption_qty": 1, "unit": "份"}]})
    assert parent.status_code == 200, parent.text
    r = client.delete("/api/dishes/%d?hard=1" % sub, headers=hdr(tn))
    assert r.status_code == 409 and r.json()["code"] == "DISH_IN_USE"
    solo, _ = _stocked_dish(client, tn, "有销量")
    assert _consume(client, tn, solo, 1).status_code == 200
    r = client.delete("/api/dishes/%d?hard=1" % solo, headers=hdr(tn))
    assert r.status_code == 409 and r.json()["code"] == "DISH_HAS_HISTORY"
    empty, _ = _stocked_dish(client, tn, "无记录")
    assert client.delete("/api/dishes/%d?hard=1" % empty, headers=hdr(tn)).json()["action"] == "DELETED"
    assert client.get("/api/dishes/%d/versions" % empty, headers=hdr(tn)).status_code == 404


def test_revenue_uses_price_at_time_of_sale(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn, "改价菜", price=30.0)
    assert _consume(client, tn, dish, 2).status_code == 200
    assert client.put("/api/dishes/%d" % dish, headers=hdr(tn), json={"price": 100.0}).status_code == 200
    d = client.get("/api/dishes/cost_analysis?days=7", headers=hdr(tn)).json()["data"]
    row = [x for x in d["dishes"] if x["id"] == dish][0]
    assert row["total_revenue"] == 60.0, "涨价不能改写已经卖出的收入"
    # 涨价后再卖，按新价；合并成同一条记录时按份数加权
    assert _consume(client, tn, dish, 2).status_code == 200
    d = client.get("/api/dishes/cost_analysis?days=7", headers=hdr(tn)).json()["data"]
    row = [x for x in d["dishes"] if x["id"] == dish][0]
    assert row["total_revenue"] == 260.0
    assert row["history"][0]["revenue"] == 260.0


def test_void_twice_is_409_and_restores_stock_once(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn)
    cid = _consume(client, tn, dish, 4).json()["data"]["consumption_ids"][0]
    assert db.get_sku(sku, tenant_id=tn).current_stock == 48.0
    assert client.post("/api/dishes/daily_consumption/%d/void" % cid, headers=hdr(tn)).status_code == 200
    assert db.get_sku(sku, tenant_id=tn).current_stock == 50.0
    r = client.post("/api/dishes/daily_consumption/%d/void" % cid, headers=hdr(tn))
    assert r.status_code == 409
    assert db.get_sku(sku, tenant_id=tn).current_stock == 50.0
    assert client.post("/api/dishes/daily_consumption/%d/void" % cid, headers=hdr(new_tenant())).status_code == 404


def test_bom_version_bumps_and_optimistic_lock(client):
    tn = new_tenant()
    dish, sku = _stocked_dish(client, tn)
    cur = client.get("/api/dishes/%d" % dish, headers=hdr(tn)).json()["data"]
    assert cur["version"] == 1
    r = client.put("/api/dishes/%d" % dish, headers=hdr(tn), json={
        "expected_version": 1, "ingredients": [{"sku_id": sku, "consumption_qty": 0.8, "unit": "斤"}]})
    assert r.status_code == 200 and r.json()["data"]["version"] == 2
    stale = client.put("/api/dishes/%d" % dish, headers=hdr(tn), json={
        "expected_version": 1, "ingredients": [{"sku_id": sku, "consumption_qty": 0.9, "unit": "斤"}]})
    assert stale.status_code == 409
    versions = client.get("/api/dishes/%d/versions" % dish, headers=hdr(tn)).json()["data"]
    assert [v["version"] for v in versions["versions"]] == [2, 1]
    # 改价格不动配方版本
    assert client.put("/api/dishes/%d" % dish, headers=hdr(tn), json={"price": 55}).json()["data"]["version"] == 2


def test_consumption_uses_tenant_unit_conversion(client):
    tn = new_tenant()
    sku, _ = db.create_sku("按桶卖的油", base_unit="升", tenant_id=tn)
    db.update_sku(sku, current_stock=10.0, last_unit_price=20.0)
    s = db.get_session()
    try:
        from app.services.costing_service import CostingService
        CostingService.record_inbound_batch(s, sku, 10.0, 20.0, "升", date=date.today().isoformat())
        s.commit()
    finally:
        s.close()
    assert client.post("/api/settings/unit_conversions", headers=hdr(tn),
                       json={"from_unit": "勺", "to_unit": "毫升", "factor": 15}).status_code == 200
    r = client.post("/api/dishes", headers=hdr(tn), json={"name": "炒菜", "price": 20, "ingredients": [
        {"sku_id": sku, "consumption_qty": 2, "unit": "勺"}]})
    assert r.status_code == 200, r.text
    dish = r.json()["data"]["id"]
    assert _consume(client, tn, dish, 10).status_code == 200
    assert abs(db.get_sku(sku, tenant_id=tn).current_stock - (10 - 0.3)) < 1e-6     # 10 份 x 2 勺 x 15 毫升 = 0.3 升
    # 其他租户没有「勺」的换算：同样的配方建不了
    other = new_tenant()
    sku2, _ = db.create_sku("别家油", base_unit="升", tenant_id=other)
    r3 = client.post("/api/dishes", headers=hdr(other), json={"name": "炒菜", "price": 20, "ingredients": [
        {"sku_id": sku2, "consumption_qty": 2, "unit": "勺"}]})
    assert r3.status_code == 400 and r3.json()["code"] == "UNIT_DIMENSION_MISMATCH"
