# -*- coding: utf-8 -*-
"""UAT018 specialty: BOM qty finite reject + unpurchased cost markers."""

import math
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db, dishes_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old = db.DB_PATH
    path = str(tmp_path / "uat018.db")
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


def _owner():
    return {"X-Role": "owner", "X-Email": "boss@uat018.hk", "X-Tenant-Id": "t_uat018"}


def test_reject_nan_inf_neg_zero_consumption_qty():
    sku_id, _ = db.create_sku("料A", base_unit="kg", tenant_id="t_uat018")
    for qty in (-1.0, 0.0, float("nan"), float("inf"), float("-inf")):
        body = {
            "name": f"bad-{qty}",
            "price": 10.0,
            "ingredients": [{"sku_id": sku_id, "consumption_qty": qty, "unit": "kg"}],
        }
        # TestClient/json may reject nan; call validator path via model + endpoint with allow
        import json
        raw = json.dumps(body, allow_nan=True).encode()
        resp = client.post(
            "/api/dishes",
            content=raw,
            headers={**_owner(), "Content-Type": "application/json"},
        )
        assert resp.status_code == 400, (qty, resp.status_code, resp.text)
        assert resp.json().get("code") == "INVALID_CONSUMPTION_QTY"


def test_unpurchased_shows_estimate_not_certain_100():
    sku_id, _ = db.create_sku("未采购", base_unit="kg", tenant_id="t_uat018")
    resp = client.post("/api/dishes", json={
        "name": "无采购餐",
        "price": 88.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 999999999999, "unit": "kg"}],
    }, headers=_owner())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["theory_cost_status"] == "estimated"
    assert data["gross_margin_rate"] is None
    assert data["theoretical_cost"] is None
    assert data["provisional_gross_margin_rate"] == 100.0
    assert "unpurchased" in data["theory_cost_source"]
    assert data["ingredients"][0]["cost_known"] is False
    # no silent clamp
    assert data["ingredients"][0]["consumption_qty"] == 999999999999.0


def test_purchased_normal_bom_keeps_cost():
    sku_id, _ = db.create_sku("已采购", base_unit="kg", tenant_id="t_uat018")
    db.update_sku(sku_id, last_unit_price=25.0, current_stock=10.0)
    resp = client.post("/api/dishes", json={
        "name": "正常餐",
        "price": 100.0,
        "ingredients": [{"sku_id": sku_id, "consumption_qty": 0.4, "unit": "kg"}],
    }, headers=_owner())
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["theory_cost_status"] == "complete"
    assert data["theoretical_cost"] == 10.0
    assert data["gross_margin_rate"] == 90.0


def test_open_risk_constants_not_b26_claim():
    assert dishes_store.BOM_QTY_UPPER_LIMIT_STATUS == "open_risk_not_adjudicated"
    assert dishes_store.BOM_QTY_UPPER_LIMIT_SOURCE is None


def test_frontend_helpers_present():
    bom = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "dish_bom.js"), encoding="utf-8").read()
    cons = open(os.path.join(os.path.dirname(__file__), "..", "static", "js", "dish_consumption.js"), encoding="utf-8").read()
    assert "dishTheoryMarginBadgeHtml" in bom
    assert "未知/估算" in bom
    assert "Number.isFinite(qtyVal)" in cons
    assert "不可为 NaN、Infinity" in cons
