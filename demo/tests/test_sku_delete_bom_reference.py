# -*- coding: utf-8 -*-
"""WS3 SKU 删除 BOM 引用保护回归测试。

被配方（dish_ingredients）引用的 SKU 不得物理删除，只能停用（active=0），
返回 (True, "DEACTIVATED")；无任何引用时物理删除，返回 (True, "DELETED")。
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
    test_db_path = str(tmp_path / "test_sku_delete_bom_reference.db")
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


def test_referenced_sku_deactivated_not_deleted():
    sku_ref, _ = db.create_sku("被配方引用食材", base_unit="kg")
    db.update_sku(sku_ref, current_stock=10.0, last_unit_price=10.0)

    dish = client.post("/api/dishes", json={
        "name": "引用食材菜",
        "price": 20.0,
        "ingredients": [{"sku_id": sku_ref, "consumption_qty": 0.1, "unit": "kg"}],
    }, headers=_owner_headers()).json()["data"]

    # 被引用：停用而非物理删除
    ok, code = db.delete_sku(sku_ref)
    assert ok is True
    assert code == "DEACTIVATED"

    row = db.get_sku(sku_ref)
    assert row is not None            # 未被物理删除
    assert row.active == 0            # 已停用

    # 配方行仍可解析出组件名称（无悬空行）
    detail = client.get(f"/api/dishes/{dish['id']}", headers=_staff_headers()).json()["data"]
    ing = detail["ingredients"][0]
    assert ing["sku_id"] == sku_ref
    assert ing["sku_name"] == "被配方引用食材"


def test_unreferenced_sku_hard_deleted():
    sku_free, _ = db.create_sku("孤立食材", base_unit="kg")

    ok, code = db.delete_sku(sku_free)
    assert (ok, code) == (True, "DELETED")
    assert db.get_sku(sku_free) is None


def test_missing_sku_returns_not_found():
    ok, code = db.delete_sku(999999)
    assert (ok, code) == (False, "NOT_FOUND")
