# -*- coding: utf-8 -*-
"""WS1 成本口径统一回归测试。

同一配方、同一数据，四处成本入口必须给出同一理论成本：
1. `_dish_to_dict`（餐品详情 / 列表）
2. 导出报表 5：餐品 BOM 标准配方明细表（理论整单成本列）
3. 导出报表 6：每日销售与食材实际成本汇总表（理论基准成本 = 单份 * 份数）
4. 导出报表 7：餐品理论毛利 vs 真实毛利偏差诊断表（理论成本列）

口径含单位换算（g -> kg）与出成率（有效领料量 = 用量 / yield_rate）。
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
    test_db_path = str(tmp_path / "test_recipe_cost_caliber.db")
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


def test_theory_cost_caliber_consistent_across_detail_and_exports():
    from app.services.costing_service import CostingService
    from app.services.recipe_cost import compute_recipe_theory
    from app.api_export import ExportDataProcessor

    # 食材 A 基准 kg，单价 20；食材 B 基准 kg，单价 8
    sku_a, _ = db.create_sku("牛腩A", base_unit="kg")
    db.update_sku(sku_a, current_stock=100.0, last_unit_price=20.0)
    sku_b, _ = db.create_sku("萝卜B", base_unit="kg")
    db.update_sku(sku_b, current_stock=100.0, last_unit_price=8.0)

    # 入库批次（供 FIFO 扣减，报表口径不依赖库存但消耗提交需要）
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku_a, 100.0, 20.0, "kg", date="2026-03-01")
        CostingService.record_inbound_batch(s, sku_b, 100.0, 8.0, "kg", date="2026-03-01")
        s.commit()
    finally:
        s.close()

    # A: 0.3kg / yield 0.5 -> 有效 0.6kg -> 0.6 * 20 = 12.0
    # B: 200g / yield 1.0 -> 0.2kg -> 0.2 * 8 = 1.6；理论整单成本 = 13.6
    resp = client.post("/api/dishes", json={
        "name": "口径一致测试煲",
        "category": "煲仔类",
        "price": 60.0,
        "ingredients": [
            {"sku_id": sku_a, "consumption_qty": 0.3, "unit": "kg", "yield_rate": 0.5},
            {"sku_id": sku_b, "consumption_qty": 200.0, "unit": "g", "yield_rate": 1.0},
        ],
    }, headers=_owner_headers())
    assert resp.status_code == 200, resp.text
    detail = resp.json()["data"]
    dish_id = detail["id"]

    assert detail["theoretical_cost"] == 13.6
    ing_a = next(i for i in detail["ingredients"] if i["sku_id"] == sku_a)
    # 出成率 / 派生损耗率生效
    assert ing_a["yield_rate"] == 0.5
    assert ing_a["loss_rate"] == 0.5
    assert ing_a["ingredient_cost"] == 12.0
    ing_b = next(i for i in detail["ingredients"] if i["sku_id"] == sku_b)
    assert ing_b["ingredient_cost"] == 1.6

    # 详情端点再次核对（读路径与列表同源）
    detail2 = client.get(f"/api/dishes/{dish_id}", headers=_staff_headers()).json()["data"]
    assert detail2["theoretical_cost"] == detail["theoretical_cost"]

    # 唯一实现直接调用：与详情一致
    s = db.get_session()
    try:
        theory, _ings = compute_recipe_theory(s, dish_id, "default")
    finally:
        s.close()
    assert theory == detail["theoretical_cost"] == 13.6

    # 报表 5：餐品 BOM 标准配方明细表（理论整单成本列 index 9）
    bom = ExportDataProcessor._handle_dish_bom_recipes({}, "default", False)
    bom_rows = [r for r in bom["rows"] if r[0] == "口径一致测试煲"]
    assert bom_rows, bom["rows"]
    assert bom_rows[0][9] == detail["theoretical_cost"]

    # 报表 7：毛利偏差诊断表（理论成本列 index 3）
    mv = ExportDataProcessor._handle_dish_margin_variance({}, "default", False)
    mv_rows = [r for r in mv["rows"] if r[0] == "口径一致测试煲"]
    assert mv_rows, mv["rows"]
    assert mv_rows[0][3] == detail["theoretical_cost"]

    # 报表 6：每日销售成本表（理论基准成本 = 单份 * 份数，index 8）
    qty = 4
    batch = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-03-02",
        "items": [{"dish_id": dish_id, "quantity": qty}],
    }, headers=_staff_headers())
    assert batch.status_code == 200, batch.text

    ds = ExportDataProcessor._handle_dish_daily_sales_cost(
        {"start_date": "2026-03-02", "end_date": "2026-03-02"}, "default", False)
    ds_rows = [r for r in ds["rows"] if r[1] == "口径一致测试煲"]
    assert ds_rows, ds["rows"]
    assert ds_rows[0][8] == round(detail["theoretical_cost"] * qty, 2) == 54.4
