# -*- coding: utf-8 -*-
"""数据中心 14→8 重构回归测试（REFACTOR_NEEDS_ANALYSIS 第 4/7 节落地契约）。

覆盖：
- A1（2026-10-06 修订）: /api/export/preview 返回全量行，与导出同口径，无 truncated 字段；
- B1: 合并报表 dish_cost_margin（按日/按期间双粒度）与 spend_summary（部门/供应商双透视 + 环比）；
- B2: 被裁报表请求端点必须 400 拒绝（不是静默兼容旧 id）；
- B3/A2: spend_summary 继承 staff 403 门禁，且 preview 与 download 两端点同时生效；
- B4: 采购补货建议清单的消耗折算走 unit_factors SSOT（斤=0.5kg），计件单位不假装折算。

全程离线：隔离临时库，零网络、零真实引擎、零 live 库写入。
"""

import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app
from app import db
from app.api_export import ExportDataProcessor

client = TestClient(app)


def _owner_headers():
    return {"X-Role": "owner", "X-Email": "boss@demo.hk"}


def _staff_headers():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_export_center_refactor.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


# ---------------------------------------------------------------
# A1（2026-10-06 修订）: 预览返回全量行，与导出同口径，不截断
# ---------------------------------------------------------------
def test_preview_returns_all_rows_full_caliber():
    """18 个 SKU：preview 全量返回 18 行，preview_rows 与 total_rows 严格一致，无 truncated 字段。"""
    for i in range(18):
        sku, _ = db.create_sku(f"截断食材{i}", category="肉类", base_unit="kg",
                               min_stock_alert=1.0)
        assert sku
        db.update_sku(sku, current_stock=5.0)

    res = client.post(
        "/api/export/preview",
        json={"report_id": "inventory_stocktake", "params": {}},
        headers=_owner_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["total_rows"] == 18, "total_rows 必须保持全量口径"
    assert len(data["preview_rows"]) == 18, "预览必须返回全量行，不得截断"
    assert "truncated" not in data, "全量预览契约下不得残留 truncated 字段"
    # summary 与行数据同口径
    assert data["summary"]["总品类数"] == 18


# ---------------------------------------------------------------
# B1: 合并报表 dish_cost_margin（按日 / 按期间双粒度）
# ---------------------------------------------------------------
def _seed_dish_with_consumption():
    """建一菜一消耗：售价 60，理论成本 10.0/份（0.5kg × 20），2026-03-02 售出 4 份。"""
    from app.services.costing_service import CostingService

    sku, _ = db.create_sku("合并测试食材", base_unit="kg")
    db.update_sku(sku, current_stock=100.0, last_unit_price=20.0)
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku, 100.0, 20.0, "kg", date="2026-03-01")
        s.commit()
    finally:
        s.close()
    resp = client.post("/api/dishes", json={
        "name": "合并测试煲",
        "category": "煲仔类",
        "price": 60.0,
        "ingredients": [
            {"sku_id": sku, "consumption_qty": 0.5, "unit": "kg", "yield_rate": 1.0},
        ],
    }, headers=_owner_headers())
    assert resp.status_code == 200, resp.text
    dish_id = resp.json()["data"]["id"]

    batch = client.post("/api/dishes/daily_consumption/batch", json={
        "date": "2026-03-02",
        "items": [{"dish_id": dish_id, "quantity": 4}],
    }, headers=_staff_headers())
    assert batch.status_code == 200, batch.text
    return dish_id


def test_dish_cost_margin_daily_granularity():
    dish_id = _seed_dish_with_consumption()
    res = client.post(
        "/api/export/preview",
        json={"report_id": "dish_cost_margin",
              "params": {"start_date": "2026-03-01", "end_date": "2026-03-31",
                         "cost_granularity": "daily"}},
        headers=_owner_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["title"] == "菜品成本与毛利表"
    assert data["summary"]["核算粒度"] == "按日逐单"
    rows = [r for r in data["preview_rows"] if r[1] == "合并测试煲"]
    assert rows, data["preview_rows"]
    # 逐单行：理论基准成本 = 单份理论 * 份数 = 10.0 * 4
    assert rows[0][9] == 40.0
    assert rows[0][14] == "已核销"


def test_dish_cost_margin_period_granularity():
    dish_id = _seed_dish_with_consumption()
    res = client.post(
        "/api/export/preview",
        json={"report_id": "dish_cost_margin",
              "params": {"start_date": "2026-03-01", "end_date": "2026-03-31",
                         "cost_granularity": "period"}},
        headers=_owner_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["summary"]["核算粒度"] == "按期间汇总"
    rows = [r for r in data["preview_rows"] if r[1] == "合并测试煲"]
    assert rows, data["preview_rows"]
    # 汇总行：份数聚合为 4，理论基准成本与日度口径一致（recipe_cost 唯一实现）
    assert rows[0][3] == 4
    assert rows[0][9] == 40.0
    assert rows[0][14] == "期间汇总"


# ---------------------------------------------------------------
# B1: 合并报表 spend_summary（部门 / 供应商双透视 + 环比）
# ---------------------------------------------------------------
def _seed_approved_receipts():
    """本期 1 笔 100 元 + 上期 1 笔 60 元（approved，无行项目走单据总额口径）。"""
    db.create_supplier("透视供应商甲")
    db.create_receipt_record(
        supplier_name="透视供应商甲", status="approved",
        receipt_date="2026-03-10", total_amount=100.0,
        created_at="2026-03-10T08:00:00", updated_at="2026-03-10T08:00:00")
    db.create_receipt_record(
        supplier_name="透视供应商甲", status="approved",
        receipt_date="2026-02-10", total_amount=60.0,
        created_at="2026-02-10T08:00:00", updated_at="2026-02-10T08:00:00")


def test_spend_summary_department_pivot_with_mom():
    _seed_approved_receipts()
    res = client.post(
        "/api/export/preview",
        json={"report_id": "spend_summary",
              "params": {"start_date": "2026-03-01", "end_date": "2026-03-31"}},
        headers=_owner_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["title"] == "期间花销汇总表"
    assert data["summary"]["透视维度"] == "按部门"
    headers = data["headers"]
    assert "核算对象" in headers and "环比增减额(元)" in headers
    total = [r for r in data["preview_rows"] if r[1] == "合计"]
    assert total, data["preview_rows"]
    # 本期合计 = 100（上期不在本期窗口，环比对照列为 0）
    assert total[0][2] == 100.0


def test_spend_summary_supplier_pivot_and_desensitization():
    _seed_approved_receipts()
    res = client.post(
        "/api/export/preview",
        json={"report_id": "spend_summary",
              "params": {"start_date": "2026-03-01", "end_date": "2026-03-31",
                         "spend_pivot": "supplier"},
              "desensitized": True},
        headers=_owner_headers(),
    )
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["summary"]["透视维度"] == "按供应商"
    rows = [r for r in data["preview_rows"] if r[1] not in ("合计", "未注明供应商")]
    assert rows, data["preview_rows"]
    assert "***" in rows[0][1], f"供应商名称未脱敏: {rows[0][1]}"


# ---------------------------------------------------------------
# B2: 被裁报表在端点层必须拒绝（不得静默兼容旧 id）
# ---------------------------------------------------------------
@pytest.mark.parametrize("report_id", [
    "supplier_payables_aging",
    "supplier_payments_journal",
    "supplier_recon_discrepancies",
    "dish_bom_recipes",
    "department_cost_summary",
    "department_items_breakdown",
])
def test_removed_reports_are_rejected(report_id):
    res = client.post(
        "/api/export/preview",
        json={"report_id": report_id, "params": {}},
        headers=_owner_headers(),
    )
    assert res.status_code == 400, f"被裁报表 {report_id} 应返回 400，实际 {res.status_code}"


# ---------------------------------------------------------------
# B3/A2: spend_summary 继承 staff 门禁（preview 与 download 双端点）
# ---------------------------------------------------------------
def test_spend_summary_role_gate_on_both_endpoints():
    _seed_approved_receipts()
    for path in ("/api/export/preview", "/api/export/download"):
        res = client.post(
            path,
            json={"report_id": "spend_summary", "params": {}},
            headers=_staff_headers(),
        )
        assert res.status_code == 403, f"{path} 对 staff 未阻断"
        assert "需老板或管理人员权限查看" in res.json().get("detail", "")


# ---------------------------------------------------------------
# B4: 消耗折算走 unit_factors SSOT
# ---------------------------------------------------------------
def test_ingredient_consumption_converts_weight_units_via_ssot():
    """斤=0.6048kg（unit_factors 唯一定义，港斤）；计件单位不假装折算（折算列为 -）。"""
    from app.services.costing_service import CostingService

    sku_jin, _ = db.create_sku("折算食材斤", base_unit="kg")
    db.update_sku(sku_jin, current_stock=100.0, last_unit_price=10.0)
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku_jin, 100.0, 10.0, "kg", date="2026-03-01")
        s.commit()
    finally:
        s.close()
    # 消耗 4 斤 = 2.42 kg，成本 40 元
    db.apply_stock_log(sku_jin, "折算食材斤", 4.0, "斤", 40.0, "测试供应商",
                       "2026-03-05", None, "consume", tenant_id="default")

    sku_piece, _ = db.create_sku("折算食材件", base_unit="瓶")
    db.update_sku(sku_piece, current_stock=50.0, last_unit_price=8.0)
    # 消耗 3 瓶：计件单位，kg 折算列必须为 "-"
    db.apply_stock_log(sku_piece, "折算食材件", 3.0, "瓶", 24.0, "测试供应商",
                       "2026-03-05", None, "consume", tenant_id="default")

    data = ExportDataProcessor._handle_dish_ingredient_consumption(
        {"start_date": "2026-03-01", "end_date": "2026-03-31"}, "default", False)
    headers = data["headers"]
    kg_idx = headers.index("折算消耗(kg)")

    rows = {r[0]: r for r in data["rows"]}
    assert rows["折算食材斤"][kg_idx] == 2.42, f"4 斤应折算 2.42kg（斤=0.6048kg），实际 {rows['折算食材斤'][kg_idx]}"
    assert rows["折算食材件"][kg_idx] == "-"
    # 汇总 kg 只累计重量单位（2.42），计件不计入
    assert data["summary"]["折算消耗合计(kg)"] == 2.42
