# -*- coding: utf-8 -*-
"""WS5 日消耗 (date,dish) 幂等 —— 合并累加回归测试。

覆盖：
1. 同 (date,dish) 二次提交合并：份数累加、库存只按增量扣一次、明细追挂到原记录；
2. 响应体 merged / quantity 语义；
3. 批内同一 dish_id 的多个 item 先合并再处理；
4. void 后再提交视为新建（不合并已作废记录）。
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_daily_consumption_merge.db")
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


def _setup_dish_and_batch(sku_name, dish_name, unit_price=10.0, per_portion=0.5,
                          inbound=100.0, date="2026-04-01"):
    from app.services.costing_service import CostingService

    sku, _ = db.create_sku(sku_name, base_unit="kg")
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku, inbound, unit_price, "kg", date=date)
        s.commit()
    finally:
        s.close()
    db.update_sku(sku, current_stock=inbound, last_unit_price=unit_price)

    dish = client.post("/api/dishes", json={
        "name": dish_name,
        "price": 30.0,
        "ingredients": [{"sku_id": sku, "consumption_qty": per_portion, "unit": "kg"}],
    }, headers=_owner_headers()).json()["data"]
    return sku, dish["id"]


def test_daily_consumption_second_submit_merges():
    sku, dish_id = _setup_dish_and_batch("合并测试牛肉", "合并测试牛肉饭")

    r1 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-04-02",
        "items": [{"dish_id": dish_id, "quantity": 10}],
    }, headers=_staff_headers()).json()
    assert r1["status"] == "success"
    assert r1["data"]["merged"] is False
    assert r1["data"]["quantity"] == 10.0
    assert len(r1["data"]["consumption_ids"]) == 1
    assert round(db.get_sku(sku).current_stock, 4) == 95.0

    first_id = r1["data"]["consumption_ids"][0]

    # 二次提交同日期同餐品 -> 合并累加，不新建
    r2 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-04-02",
        "items": [{"dish_id": dish_id, "quantity": 4}],
    }, headers=_staff_headers()).json()
    assert r2["status"] == "success"
    assert r2["data"]["merged"] is True
    assert r2["data"]["quantity"] == 14.0
    assert r2["data"]["consumption_ids"] == [first_id]
    assert r2["data"]["items"][0]["merged"] is True
    assert r2["data"]["items"][0]["quantity"] == 14.0

    # 库存只按增量扣减一次（4 份 * 0.5kg = 2kg）
    assert round(db.get_sku(sku).current_stock, 4) == 93.0

    # 台账合并：单一记录、份数与成本累加、明细追挂
    day = client.get("/api/dishes/daily_consumption?date=2026-04-02",
                     headers=_staff_headers()).json()["data"]
    assert len(day["consumptions"]) == 1
    cons = day["consumptions"][0]
    assert cons["id"] == first_id
    assert cons["quantity"] == 14.0
    assert cons["total_cost"] == 70.0          # 14 * 0.5kg * 10元/kg
    assert round(cons["unit_cost"], 2) == 5.0
    assert len(cons["details"]) == 2           # 两次提交各追挂一条明细
    assert day["summary"]["total_quantity"] == 14.0

    # void 后再提交视为新建（不合并已作废记录）
    void = client.post(f"/api/dishes/daily_consumption/{first_id}/void",
                       headers=_owner_headers())
    assert void.status_code == 200
    assert round(db.get_sku(sku).current_stock, 4) == 100.0

    r3 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-04-02",
        "items": [{"dish_id": dish_id, "quantity": 3}],
    }, headers=_staff_headers()).json()
    assert r3["data"]["merged"] is False
    assert r3["data"]["consumption_ids"] != [first_id]
    assert r3["data"]["quantity"] == 3.0
    assert round(db.get_sku(sku).current_stock, 4) == 98.5


def test_merge_writes_two_consume_inventory_logs():
    """合并提交仍逐次写 inventory_log：两次提交产生 2 条 kind=consume 台账，
    qty 合计等于总份数用量，第二次 note 标注「合并」。"""
    sku, dish_id = _setup_dish_and_batch("台账牛肉", "台账牛肉饭",
                                         unit_price=10.0, per_portion=0.5)

    r1 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-06-01",
        "items": [{"dish_id": dish_id, "quantity": 10}],
    }, headers=_staff_headers()).json()
    assert r1["status"] == "success"

    r2 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-06-01",
        "items": [{"dish_id": dish_id, "quantity": 4}],
    }, headers=_staff_headers()).json()
    assert r2["status"] == "success"
    assert r2["data"]["merged"] is True

    s = db.get_session()
    try:
        logs = (
            s.query(db._StockLogRow)
            .filter(db._StockLogRow.sku_id == sku,
                    db._StockLogRow.kind == "consume")
            .order_by(db._StockLogRow.id.asc())
            .all()
        )
    finally:
        s.close()

    assert len(logs) == 2
    # 10 份 * 0.5kg + 4 份 * 0.5kg = 7kg
    assert round(sum(float(l.qty) for l in logs), 4) == 7.0
    assert "合并" in (logs[1].note or "")
    assert "合并" not in (logs[0].note or "")


def test_batch_internal_duplicate_items_merged():
    sku, dish_id = _setup_dish_and_batch("批内合并牛肉", "批内合并牛肉饭")

    r = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-05-01",
        "items": [
            {"dish_id": dish_id, "quantity": 2},
            {"dish_id": dish_id, "quantity": 3},
        ],
    }, headers=_staff_headers()).json()

    # 无既有活跃记录（batch 内合并）：merged=False，但只落一条记录、份数合并
    assert r["status"] == "success"
    assert r["data"]["merged"] is False
    assert r["data"]["quantity"] == 5.0
    assert len(r["data"]["consumption_ids"]) == 1
    assert len(r["data"]["items"]) == 1
    assert round(db.get_sku(sku).current_stock, 4) == 97.5
