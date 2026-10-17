# -*- coding: utf-8 -*-
"""UAT019: huge sale must not silently create huge negative stock.

Ordinary batch → 409 INSUFFICIENT_STOCK; force+reason only as existing contract;
normal 1 portion deducts 0.5; illegal date / non-number no write; request_id idempotent.
No new business caps. Do not loosen already-consumed reverse 409.
"""

import os
import sys
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old = db.DB_PATH
    path = str(tmp_path / "uat019.db")
    monkeypatch.setenv("DB_PATH", path)
    db.DB_PATH = path
    db._make_engine()
    yield
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass
    db.DB_PATH = old
    db._make_engine()


def _hdr(role="staff"):
    return {"X-Role": role, "X-Email": "%s@demo.hk" % role}


def _setup(stock=1.0, bom=0.5, price=30.0):
    from app.services.costing_service import CostingService

    sku, _ = db.create_sku("UAT019料", base_unit="kg", tenant_id="default")
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(
            s, sku, stock, 40.0, "kg", date=date.today().isoformat())
        s.commit()
    finally:
        s.close()
    db.update_sku(sku, current_stock=stock, last_unit_price=40.0)
    dish = client.post("/api/dishes", headers=_hdr("owner"), json={
        "name": "UAT019菜",
        "price": price,
        "ingredients": [{"sku_id": sku, "consumption_qty": bom, "unit": "kg"}],
    }).json()["data"]
    return sku, dish["id"]


def test_huge_sale_ordinary_click_returns_409_no_write():
    sku, dish_id = _setup(stock=1.0, bom=0.5)
    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 999999999999}],
        "request_id": "uat019-huge-1",
    })
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["code"] == "INSUFFICIENT_STOCK"
    assert body["data"]["can_override"] is True
    assert body["data"]["reason_required"] is True
    assert db.get_sku(sku).current_stock == 1.0


def test_force_with_reason_allows_negative_as_contract():
    sku, dish_id = _setup(stock=1.0, bom=0.5)
    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 4}],
        "allow_negative": True,
    })
    assert r.status_code == 409 and "原因" in r.json()["msg"]
    assert db.get_sku(sku).current_stock == 1.0

    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 4}],
        "allow_negative": True,
        "override_reason": "进货单未录入",
        "request_id": "uat019-force-1",
    })
    assert r.status_code == 200, r.text
    # 4 * 0.5 = 2kg needed; stock 1 → -1
    assert abs(db.get_sku(sku).current_stock - (-1.0)) < 1e-6


def test_normal_one_portion_deducts_half():
    sku, dish_id = _setup(stock=1.0, bom=0.5)
    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 1}],
        "request_id": "uat019-normal-1",
    })
    assert r.status_code == 200, r.text
    assert abs(db.get_sku(sku).current_stock - 0.5) < 1e-6


def test_illegal_date_and_non_number_do_not_write():
    sku, dish_id = _setup(stock=1.0, bom=0.5)
    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": "not-a-date",
        "items": [{"dish_id": dish_id, "quantity": 1}],
    })
    assert r.status_code == 400 and r.json()["code"] == "INVALID_CONSUMPTION_DATE"
    future = (date.today() + timedelta(days=2)).isoformat()
    assert client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": future, "items": [{"dish_id": dish_id, "quantity": 1}],
    }).status_code == 400
    r = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(),
                    content='{"items":[{"dish_id":%d,"quantity":NaN}]}' % dish_id)
    assert r.status_code in (400, 422)
    assert db.get_sku(sku).current_stock == 1.0


def test_duplicate_request_id_does_not_double_deduct():
    sku, dish_id = _setup(stock=10.0, bom=0.5)
    body = {
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 2}],
        "request_id": "uat019-idem-1",
    }
    r1 = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json=body)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json=body)
    assert r2.status_code == 200 and r2.json()["data"]["replayed"] is True
    # 2 * 0.5 = 1kg once
    assert abs(db.get_sku(sku).current_stock - 9.0) < 1e-6


def test_tenant_b_unchanged_by_default_overdraw_gate():
    sku, dish_id = _setup(stock=1.0, bom=0.5)
    # Create a SKU in tenant_b baseline
    sku_b, _ = db.create_sku("B店料", base_unit="kg", tenant_id="tenant_b")
    db.update_sku(sku_b, current_stock=7.0, last_unit_price=10.0)
    before = db.get_sku(sku_b, tenant_id="tenant_b").current_stock
    client.post("/api/dishes/daily_consumption/batch", headers=_hdr(), json={
        "date": date.today().isoformat(),
        "items": [{"dish_id": dish_id, "quantity": 999999999999}],
    })
    assert db.get_sku(sku_b, tenant_id="tenant_b").current_stock == before
