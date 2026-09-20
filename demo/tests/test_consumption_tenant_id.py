# -*- coding: utf-8 -*-
"""WS4 daily_dish_consumptions 补 tenant_id 回归测试。

覆盖：
1. 写入侧（新建）显式落 tenant_id；
2. 存量历史空值行的一次性幂等回填（重建引擎触发迁移）；
3. 行级租户隔离（跨租户查询不可见）。
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
    test_db_path = str(tmp_path / "test_consumption_tenant_id.db")
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


def _headers(role, email, tenant):
    return {"X-Role": role, "X-Email": email, "X-Tenant-Id": tenant}


def test_consumption_tenant_id_write_backfill_and_isolation():
    from app.services.costing_service import CostingService

    headers_a = _headers("owner", "boss@a.hk", "tenant_a")
    headers_b = _headers("staff", "staff@b.hk", "tenant_b")

    # 租户 A 建 SKU（经 API，带租户归属）+ 餐品
    sku_a = client.post("/api/inventory/skus",
                        json={"name": "租户A食材", "base_unit": "kg"},
                        headers=headers_a).json()["id"]
    db.update_sku(sku_a, current_stock=50.0, last_unit_price=10.0)

    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku_a, 50.0, 10.0, "kg", date="2026-06-01")
        s.commit()
    finally:
        s.close()

    dish_id = client.post("/api/dishes", json={
        "name": "租户A菜", "price": 25.0,
        "ingredients": [{"sku_id": sku_a, "consumption_qty": 0.5, "unit": "kg"}],
    }, headers=headers_a).json()["data"]["id"]

    # 写入侧显式落 tenant_id
    r = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-06-02",
        "items": [{"dish_id": dish_id, "quantity": 2}],
    }, headers=headers_a).json()
    assert r["status"] == "success"
    cons_id = r["data"]["consumption_ids"][0]

    s = db.get_session()
    try:
        row = s.get(db._DailyConsumptionRow, cons_id)
        assert row.tenant_id == "tenant_a"
    finally:
        s.close()

    # 行级隔离：租户 B 查询看不到 A 的消耗；A 自己可见
    b_view = client.get("/api/dishes/daily_consumption?date=2026-06-02",
                        headers=headers_b).json()["data"]
    assert b_view["consumptions"] == []
    a_view = client.get("/api/dishes/daily_consumption?date=2026-06-02",
                        headers=headers_a).json()["data"]
    assert len(a_view["consumptions"]) == 1

    # 模拟历史行：ADD COLUMN ... DEFAULT 'default' 会把存量行填成占位值，
    # 重建引擎触发一次性幂等回填，把占位值改写为关联 dish 的真实租户。
    s = db.get_session()
    try:
        legacy = db._DailyConsumptionRow(
            date="2026-06-03",
            dish_id=dish_id,
            quantity=1.0,
            total_cost=5.0,
            unit_cost=5.0,
            notes="",
            is_void=0,
            created_at=db.now_iso(),
        )
        s.add(legacy)
        s.commit()
        legacy_id = legacy.id
        # 确认落库为占位值（非 dish 租户），否则本用例失去意义
        assert s.get(db._DailyConsumptionRow, legacy_id).tenant_id == "default"
    finally:
        s.close()

    db._make_engine()

    s = db.get_session()
    try:
        backfilled = s.get(db._DailyConsumptionRow, legacy_id)
        assert backfilled.tenant_id == "tenant_a"
    finally:
        s.close()

    # 幂等：再次重建引擎不改动已回填行
    db._make_engine()
    s = db.get_session()
    try:
        assert s.get(db._DailyConsumptionRow, legacy_id).tenant_id == "tenant_a"
    finally:
        s.close()

    # 回填后仍行级隔离：B 不可见
    b2 = client.get("/api/dishes/daily_consumption?date=2026-06-03",
                    headers=headers_b).json()["data"]
    assert b2["consumptions"] == []
    a2 = client.get("/api/dishes/daily_consumption?date=2026-06-03",
                    headers=headers_a).json()["data"]
    assert len(a2["consumptions"]) == 1
