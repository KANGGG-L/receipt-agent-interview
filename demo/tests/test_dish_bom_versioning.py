# -*- coding: utf-8 -*-
"""WS2 BOM 版本化 + 乐观锁回归测试。

覆盖：
1. 建店写 v1 快照；
2. 更新版本自增并写新快照，历史快照不可改写；
3. `expected_version` 不一致返回 409 VERSION_CONFLICT；
4. `GET /versions` 列表与 `GET /versions/{v}` 详情；
5. restore 将历史快照恢复为**新版本**（不改写历史）。
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
    test_db_path = str(tmp_path / "test_dish_bom_versioning.db")
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


def test_bom_versioning_snapshot_lifecycle():
    sku, _ = db.create_sku("版本测试食材", base_unit="kg")
    db.update_sku(sku, current_stock=50.0, last_unit_price=10.0)

    # 1. 建店写 v1 快照
    created = client.post("/api/dishes", json={
        "name": "版本测试菜",
        "price": 30.0,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.1, "unit": "kg"}],
    }, headers=_owner_headers()).json()["data"]
    dish_id = created["id"]
    assert created["version"] == 1

    v1_list = client.get(f"/api/dishes/{dish_id}/versions",
                         headers=_staff_headers()).json()["data"]
    assert v1_list["current_version"] == 1
    assert [v["version"] for v in v1_list["versions"]] == [1]

    v1_detail = client.get(f"/api/dishes/{dish_id}/versions/1",
                           headers=_staff_headers()).json()["data"]
    assert v1_detail["version"] == 1
    assert v1_detail["changed_by"] == "boss@demo.hk"
    assert v1_detail["snapshot"]["dish"]["name"] == "版本测试菜"
    assert v1_detail["snapshot"]["dish"]["price"] == 30.0
    assert len(v1_detail["snapshot"]["ingredients"]) == 1
    assert v1_detail["snapshot"]["ingredients"][0]["consumption_qty"] == 0.1

    # 2. 更新配方：版本自增并写 v2 快照，历史 v1 不被改写
    upd = client.put(f"/api/dishes/{dish_id}", json={
        "price": 36.0,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.2, "unit": "kg"}],
    }, headers=_owner_headers()).json()
    assert upd["status"] == "success"
    assert upd["data"]["version"] == 2

    vlist = client.get(f"/api/dishes/{dish_id}/versions",
                       headers=_staff_headers()).json()["data"]
    assert vlist["current_version"] == 2
    assert [v["version"] for v in vlist["versions"]] == [2, 1]
    v2_detail = client.get(f"/api/dishes/{dish_id}/versions/2",
                           headers=_staff_headers()).json()["data"]
    assert v2_detail["snapshot"]["ingredients"][0]["consumption_qty"] == 0.2
    v1_again = client.get(f"/api/dishes/{dish_id}/versions/1",
                          headers=_staff_headers()).json()["data"]
    assert v1_again["snapshot"]["ingredients"][0]["consumption_qty"] == 0.1

    # 3. expected_version 不一致 -> 409 VERSION_CONFLICT（乐观锁防并发覆盖）
    conflict = client.put(f"/api/dishes/{dish_id}", json={
        "expected_version": 1,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.3, "unit": "kg"}],
    }, headers=_owner_headers())
    assert conflict.status_code == 409
    cbody = conflict.json()
    assert cbody["code"] == "VERSION_CONFLICT"
    assert cbody["current_version"] == 2

    # 携带正确版本可更新
    ok = client.put(f"/api/dishes/{dish_id}", json={
        "expected_version": 2,
        "ingredients": [{"sku_id": sku, "consumption_qty": 0.3, "unit": "kg"}],
    }, headers=_owner_headers())
    assert ok.status_code == 200
    assert ok.json()["data"]["version"] == 3

    # 4. restore 生成新版本（v4），不改写历史
    restore = client.post(f"/api/dishes/{dish_id}/versions/1/restore",
                          json={}, headers=_owner_headers())
    assert restore.status_code == 200, restore.text
    rdata = restore.json()["data"]
    assert rdata["version"] == 4
    assert rdata["restored_from_version"] == 1
    assert rdata["price"] == 30.0
    assert rdata["ingredients"][0]["consumption_qty"] == 0.1

    vfinal = client.get(f"/api/dishes/{dish_id}/versions",
                        headers=_staff_headers()).json()["data"]
    assert [v["version"] for v in vfinal["versions"]] == [4, 3, 2, 1]
    assert vfinal["current_version"] == 4


def test_versions_endpoint_rejects_cross_tenant():
    """跨租户不可见他人餐品版本历史。"""
    headers_a = {"X-Role": "owner", "X-Email": "a@a.hk", "X-Tenant-Id": "tenant_a"}
    headers_b = {"X-Role": "owner", "X-Email": "b@b.hk", "X-Tenant-Id": "tenant_b"}

    sku_a = client.post("/api/inventory/skus", json={"name": "版本租户A食材", "base_unit": "kg"},
                        headers=headers_a).json()["id"]
    dish_a = client.post("/api/dishes", json={
        "name": "版本租户A菜", "price": 10.0,
        "ingredients": [{"sku_id": sku_a, "consumption_qty": 0.1, "unit": "kg"}],
    }, headers=headers_a).json()["data"]["id"]

    resp = client.get(f"/api/dishes/{dish_a}/versions", headers=headers_b)
    assert resp.status_code == 404
