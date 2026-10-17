# -*- coding: utf-8 -*-
"""UAT-032: 同日餐品二次提交后 FIFO 弹窗须显示合并后份数与累计成本/明细.

覆盖：
1. 同日同餐品先 3 再 2（不同 request_id）→ merged、quantity=5、consumptions 快照 qty=5、
   两次 FIFO detail、累计成本相等；
2. 重放第二 request_id → 仍 quantity=5、不重扣库存；
3. 换日数据不串；
4. 不破坏正确累加/幂等（第三次增量合并到 7）。
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
    test_db_path = str(tmp_path / "test_uat032_fifo_modal.db")
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


def _owner():
    return {"X-Role": "owner", "X-Email": "boss@demo.hk"}


def _staff():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


def _setup(sku_name="UAT032牛肉", dish_name="UAT032牛肉饭",
           unit_price=10.0, per_portion=0.5, inbound=100.0, date="2026-04-01"):
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
    }, headers=_owner()).json()["data"]
    return sku, dish["id"]


def test_uat032_same_day_3_then_2_snapshot_and_replay():
    sku, dish_id = _setup()
    day_a = "2026-04-10"
    day_b = "2026-04-11"

    r1 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 3}],
        "request_id": "uat032-req-1",
    }, headers=_owner()).json()
    assert r1["status"] == "success"
    assert r1["data"]["merged"] is False
    assert r1["data"]["quantity"] == 3.0
    assert len(r1["data"]["consumptions"]) == 1
    snap1 = r1["data"]["consumptions"][0]
    assert snap1["quantity"] == 3.0
    assert snap1["total_cost"] == 15.0  # 3 * 0.5kg * 10
    assert len(snap1["details"]) == 1
    cid = r1["data"]["consumption_ids"][0]
    stock_after_1 = round(db.get_sku(sku).current_stock, 4)
    assert stock_after_1 == 98.5

    r2 = client.post("/api/dishes/daily_consumption/batch", json={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 2}],
        "request_id": "uat032-req-2",
    }, headers=_owner()).json()
    assert r2["status"] == "success"
    assert r2["data"]["merged"] is True
    assert r2["data"]["quantity"] == 5.0
    assert r2["data"]["consumption_ids"] == [cid]
    assert len(r2["data"]["consumptions"]) == 1
    snap2 = r2["data"]["consumptions"][0]
    assert snap2["id"] == cid
    assert snap2["quantity"] == 5.0
    assert snap2["total_cost"] == 25.0  # 5 * 0.5 * 10
    assert len(snap2["details"]) == 2
    detail_cost_sum = round(sum(float(d["total_cost"]) for d in snap2["details"]), 2)
    assert detail_cost_sum == 25.0
    assert round(db.get_sku(sku).current_stock, 4) == 97.5

    # Table/list view must match snapshot (dialog source of truth)
    day = client.get(
        f"/api/dishes/daily_consumption?date={day_a}",
        headers=_owner(),
    ).json()["data"]
    assert len(day["consumptions"]) == 1
    cons = day["consumptions"][0]
    assert cons["id"] == cid
    assert cons["quantity"] == 5.0
    assert cons["total_cost"] == 25.0
    assert len(cons["details"]) == 2
    assert round(sum(float(d["total_cost"]) for d in cons["details"]), 2) == 25.0

    # Replay second request_id: still quantity 5, no second deduct
    replay = client.post("/api/dishes/daily_consumption/batch", json={
        "date": day_a,
        "items": [{"dish_id": dish_id, "quantity": 2}],
        "request_id": "uat032-req-2",
    }, headers=_owner()).json()
    assert replay["status"] == "success"
    assert replay["data"].get("replayed") is True
    assert replay["data"]["quantity"] == 5.0
    assert replay["data"]["consumption_ids"] == [cid]
    assert replay["data"]["consumptions"][0]["quantity"] == 5.0
    assert len(replay["data"]["consumptions"][0]["details"]) == 2
    assert round(db.get_sku(sku).current_stock, 4) == 97.5

    # Different day must not leak day_a totals
    r_other = client.post("/api/dishes/daily_consumption/batch", json={
        "date": day_b,
        "items": [{"dish_id": dish_id, "quantity": 1}],
        "request_id": "uat032-req-dayb",
    }, headers=_owner()).json()
    assert r_other["status"] == "success"
    assert r_other["data"]["merged"] is False
    assert r_other["data"]["quantity"] == 1.0
    assert r_other["data"]["consumptions"][0]["quantity"] == 1.0
    assert r_other["data"]["consumptions"][0]["date"] == day_b
    assert len(r_other["data"]["consumptions"][0]["details"]) == 1

    day_a_again = client.get(
        f"/api/dishes/daily_consumption?date={day_a}",
        headers=_owner(),
    ).json()["data"]
    assert len(day_a_again["consumptions"]) == 1
    assert day_a_again["consumptions"][0]["quantity"] == 5.0
    assert len(day_a_again["consumptions"][0]["details"]) == 2

    day_b_list = client.get(
        f"/api/dishes/daily_consumption?date={day_b}",
        headers=_owner(),
    ).json()["data"]
    assert len(day_b_list["consumptions"]) == 1
    assert day_b_list["consumptions"][0]["quantity"] == 1.0
    assert len(day_b_list["consumptions"][0]["details"]) == 1


def test_uat032_third_merge_still_accumulates():
    """Negative boundary: correct backend accumulate/idempotency not broken."""
    sku, dish_id = _setup(sku_name="UAT032牛2", dish_name="UAT032牛2饭")
    day = "2026-05-01"
    for q, rid, expect_q, expect_merged in [
        (3, "t3-a", 3.0, False),
        (2, "t3-b", 5.0, True),
        (2, "t3-c", 7.0, True),
    ]:
        r = client.post("/api/dishes/daily_consumption/batch", json={
            "date": day,
            "items": [{"dish_id": dish_id, "quantity": q}],
            "request_id": rid,
        }, headers=_owner()).json()
        assert r["status"] == "success"
        assert r["data"]["merged"] is expect_merged
        assert r["data"]["quantity"] == expect_q
        assert r["data"]["consumptions"][0]["quantity"] == expect_q
        assert len(r["data"]["consumptions"][0]["details"]) == (
            1 if expect_q == 3.0 else (2 if expect_q == 5.0 else 3)
        )
    assert round(db.get_sku(sku).current_stock, 4) == 96.5  # 100 - 7*0.5
