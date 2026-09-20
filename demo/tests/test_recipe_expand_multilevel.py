# -*- coding: utf-8 -*-
"""WS6 多级 BOM（餐品含子配方）递归展开回归测试。

覆盖：
1. 多级展开正确（父餐品 -> 子配方 -> SKU，聚合到 SKU 维度）；
2. yield_rate 逐层生效（父级子配方出成率 + 子层食材出成率）；
3. 环检测抛错（展开入口与建档校验入口）；
4. 深度上限抛错；
5. 自引用被 API 拒绝。
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.services.recipe_expand import (
    MAX_DEPTH,
    expand_recipe,
    validate_recipe_components,
)
from app.services import recipe_cost

client = TestClient(app)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_recipe_expand_multilevel.db")
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


def _mk_dish(name, price=0.0):
    resp = client.post("/api/dishes", json={
        "name": name, "price": price, "ingredients": [],
    }, headers=_owner_headers())
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


def _add_component(dish_id, ctype, cid, qty, unit, yield_rate=1.0):
    s = db.get_session()
    try:
        s.add(db._DishIngredientRow(
            dish_id=dish_id,
            sku_id=(cid if ctype == "sku" else None),
            component_type=ctype,
            component_id=cid,
            consumption_qty=qty,
            unit=unit,
            yield_rate=yield_rate,
            notes="",
        ))
        s.commit()
    finally:
        s.close()


def test_multilevel_expansion_and_yield_per_layer():
    sku, _ = db.create_sku("多级食材", base_unit="kg")
    db.update_sku(sku, current_stock=100.0, last_unit_price=10.0)

    # 子配方：0.1kg/份，出成率 0.5 -> 有效领料 0.2kg/份
    sub_id = _mk_dish("子配方酱")
    _add_component(sub_id, "sku", sku, 0.1, "kg", yield_rate=0.5)

    # 父餐品：2 份子配方（父级组件出成率 1.0）
    parent_id = _mk_dish("父餐品")
    _add_component(parent_id, "dish", sub_id, 2.0, "份", yield_rate=1.0)

    s = db.get_session()
    try:
        result = expand_recipe(s, parent_id, 1.0, "default")
    finally:
        s.close()
    assert set(result.keys()) == {sku}
    assert result[sku]["unit"] == "kg"
    assert round(result[sku]["qty"], 4) == 0.4   # 2 份 * 0.2kg

    # 份数放大
    s = db.get_session()
    try:
        r2 = expand_recipe(s, parent_id, 2.0, "default")
    finally:
        s.close()
    assert round(r2[sku]["qty"], 4) == 0.8

    # 父级子配方出成率逐层生效：2 份 / yield 0.5 = 4 份 -> 4 * 0.2kg = 0.8kg
    parent2 = _mk_dish("父餐品2")
    _add_component(parent2, "dish", sub_id, 2.0, "份", yield_rate=0.5)
    s = db.get_session()
    try:
        r3 = expand_recipe(s, parent2, 1.0, "default")
    finally:
        s.close()
    assert round(r3[sku]["qty"], 4) == 0.8

    # 理论成本走同一展开：0.4kg * 10元 = 4.0
    s = db.get_session()
    try:
        total, ings = recipe_cost.compute_recipe_theory(s, parent_id, "default")
    finally:
        s.close()
    assert total == 4.0
    assert ings[0]["component_type"] == "dish"
    assert ings[0]["expanded"][0]["sku_id"] == sku
    assert round(ings[0]["expanded"][0]["ingredient_cost"], 2) == 4.0


def test_cycle_detection_raises():
    d1 = _mk_dish("环A")
    d2 = _mk_dish("环B")
    _add_component(d1, "dish", d2, 1.0, "份")
    _add_component(d2, "dish", d1, 1.0, "份")

    s = db.get_session()
    try:
        with pytest.raises(ValueError) as ei:
            expand_recipe(s, d1, 1.0, "default")
        assert "循环" in str(ei.value)
    finally:
        s.close()

    # 建档校验入口同样报错
    s = db.get_session()
    try:
        with pytest.raises(ValueError):
            validate_recipe_components(
                s, d1,
                [{"component_type": "dish", "component_id": d2,
                  "consumption_qty": 1.0, "unit": "份", "yield_rate": 1.0}],
                "default")
    finally:
        s.close()


def test_depth_limit_exceeded():
    sku, _ = db.create_sku("深度食材", base_unit="kg")

    # 构建 MAX_DEPTH+1 层链：L1(sku) -> L2 -> ... -> L(MAX_DEPTH+1)
    leaf = _mk_dish("深度L1")
    _add_component(leaf, "sku", sku, 1.0, "kg")
    prev = leaf
    for i in range(2, MAX_DEPTH + 2):
        cur = _mk_dish(f"深度L{i}")
        _add_component(cur, "dish", prev, 1.0, "份")
        prev = cur

    s = db.get_session()
    try:
        with pytest.raises(ValueError) as ei:
            expand_recipe(s, prev, 1.0, "default")
        assert "深度" in str(ei.value)
    finally:
        s.close()


def _create_dish_http(name, components, price=30.0):
    """经 HTTP 创建带配方的餐品（不直插 DB 行）。"""
    return client.post("/api/dishes", json={
        "name": name, "price": price, "ingredients": components,
    }, headers=_owner_headers())


def _dish_component(child_id, qty=1.0, unit="份"):
    return {"component_type": "dish", "component_id": child_id,
            "consumption_qty": qty, "unit": unit, "yield_rate": 1.0}


def _sku_component(sku_id, qty=1.0, unit="kg"):
    return {"component_type": "sku", "component_id": sku_id,
            "consumption_qty": qty, "unit": unit, "yield_rate": 1.0}


def test_create_dish_rejects_over_depth_chain_http_only():
    """DEF-1：创建路径必须校验深度。全流程仅经 HTTP 构造两条链：
    合法 L1->...->L5（最深层=5，根节点 depth=1）→ 顶层创建 200；
    越界 L1->...->L6（最深层=6 > MAX_DEPTH=5）→ 顶层创建 400 且不落库。
    """
    sku, _ = db.create_sku("链路食材", base_unit="kg")

    # 合法链（自底向上）：L5 引 sku，L4 引 L5 ... L1 引 L2
    legal_top = None
    for i in range(5, 0, -1):
        comps = [_sku_component(sku)] if i == 5 else [_dish_component(legal_top)]
        resp = _create_dish_http(f"合法链L{i}", comps)
        assert resp.status_code == 200, (i, resp.text)
        legal_top = resp.json()["data"]["id"]

    # 越界链（自底向上）：L6 引 sku，L5 引 L6 ... 只有顶层 L1 创建时超深拒绝
    over_root = None
    for i in range(6, 0, -1):
        comps = [_sku_component(sku)] if i == 6 else [_dish_component(over_root)]
        resp = _create_dish_http(f"越界链L{i}", comps)
        if i == 1:
            assert resp.status_code == 400, resp.text
            assert resp.json()["code"] == "RECIPE_GRAPH_INVALID"
            assert "深度" in resp.json()["msg"]
        else:
            assert resp.status_code == 200, (i, resp.text)
            over_root = resp.json()["data"]["id"]

    # 越界链顶层未落库；合法链顶层已落库（均为 HTTP 列表校验）
    names = {d["name"] for d in client.get(
        "/api/dishes", headers=_owner_headers()).json()["data"]}
    assert "越界链L1" not in names
    assert "合法链L1" in names


def test_self_reference_rejected_by_api():
    dish_id = _mk_dish("自引用菜")
    resp = client.put(f"/api/dishes/{dish_id}", json={
        "ingredients": [{
            "component_type": "dish", "component_id": dish_id,
            "consumption_qty": 1.0, "unit": "份",
        }],
    }, headers=_owner_headers())
    assert resp.status_code == 400
    assert resp.json()["code"] == "RECIPE_GRAPH_INVALID"
