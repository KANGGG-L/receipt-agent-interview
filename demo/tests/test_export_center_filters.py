# -*- coding: utf-8 -*-
"""数据中心 13 项新 Filter 行为回归（EXPORT_CENTER_FILTERS_PLAN §1-§9）。

覆盖：
- receipts_itemized_ledger: keyword（单号 #id / 裸 id / 供应商 / 品名）、
  paid_status 四值、amount_min/max 含端点、currency 大小写不敏感；
- dish_cost_margin: margin_pct_max（period 粒度行过滤 + summary 全量口径）、
  dish_status（period 分支不再硬编码 active）、dish_category；
- dish_ingredient_consumption: only_suggested（过滤后 summary 仍全量种数）；
- inventory_price_anomaly: direction 互斥、threshold_pct 临时覆盖且不写回 SSOT、
  window_days 窗口截断 + 表头跟随；
- spend_summary: 部门/供应商聚焦 + 合计重算、无效聚焦安全空桶、amount_min；
- inventory_stock_logs: receipt_ref（兼容 # 前缀 / 无数字空结果）、vendor 模糊；
- inventory_valuation: value_min 行级过滤但 summary 货值全量；
- REPORTS_META: 8 张报表不变 + 各表 filters 登记名（对齐 spec §8）。

沿用 test_export_center_refactor.py 的隔离临时库模式：零网络、零真实引擎、
零 live 库写入。
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



def _staff_headers():
    return {"X-Role": "staff", "X-Email": "staff@demo.hk"}


def _owner_headers():
    return {"X-Role": "owner", "X-Email": "boss@demo.hk"}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_export_center_filters.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    db.DB_PATH = old_path
    db._make_engine()


def _preview(report_id, params, headers=None, **extra):
    body = {"report_id": report_id, "params": params}
    body.update(extra)
    res = client.post(
        "/api/export/preview", json=body, headers=headers or _owner_headers()
    )
    assert res.status_code == 200, res.text
    return res.json()["data"]


# ---------------------------------------------------------------------------
# 1. 采购入账明细台账 receipts_itemized_ledger
# ---------------------------------------------------------------------------
def _seed_ledger():
    dept_id = db.create_department("后厨部")
    db.create_supplier("鲜果供应商")
    db.create_supplier("海鲜供应商")

    rid1 = db.create_receipt_record(
        supplier_name="鲜果供应商", status="approved",
        receipt_date="2026-09-10", total_amount=100.0, currency="hkd",
        paid_at="2026-09-11T08:00:00", expected_pay_date="", department_id=dept_id,
        created_at="2026-09-10T08:00:00", updated_at="2026-09-10T08:00:00")
    db.set_receipt_items(rid1, [
        {"name": "苹果", "quantity": 10, "unit": "kg", "unit_price": 10.0, "amount": 100.0},
    ])

    rid2 = db.create_receipt_record(
        supplier_name="鲜果供应商", status="approved",
        receipt_date="2026-09-12", total_amount=200.0, currency="CNY",
        paid_at="", expected_pay_date="2020-01-01", department_id=dept_id,
        created_at="2026-09-12T08:00:00", updated_at="2026-09-12T08:00:00")
    db.set_receipt_items(rid2, [
        {"name": "香蕉", "quantity": 20, "unit": "kg", "unit_price": 10.0, "amount": 200.0},
    ])

    rid3 = db.create_receipt_record(
        supplier_name="海鲜供应商", status="approved",
        receipt_date="2026-09-15", total_amount=300.0, currency="USD",
        paid_at="", expected_pay_date="", department_id=dept_id,
        created_at="2026-09-15T08:00:00", updated_at="2026-09-15T08:00:00")
    db.set_receipt_items(rid3, [
        {"name": "虾仁", "quantity": 5, "unit": "kg", "unit_price": 60.0, "amount": 300.0},
    ])
    return rid1, rid2, rid3


def test_ledger_keyword_hits_receipt_supplier_and_item_names():
    """keyword：`#id` / 裸 id / 供应商名（整单保留）/ 品名（只留命中行）；空参不过滤。"""
    rid1, _rid2, _rid3 = _seed_ledger()

    # 单号 `#id`
    data = _preview("receipts_itemized_ledger", {"keyword": f"#{rid1}"})
    assert [r[8] for r in data["preview_rows"]] == ["苹果"]
    assert all(r[0] == f"#{rid1}" for r in data["preview_rows"])

    # 裸 id（无 # 前缀）
    data = _preview("receipts_itemized_ledger", {"keyword": str(rid1)})
    assert [r[8] for r in data["preview_rows"]] == ["苹果"]

    # 供应商名：命中单据级 → 该供应商整单行全部保留（苹果 + 香蕉）
    data = _preview("receipts_itemized_ledger", {"keyword": "鲜果供应商"})
    assert sorted(r[8] for r in data["preview_rows"]) == ["苹果", "香蕉"]

    # 品名：只留命中品名的行
    data = _preview("receipts_itemized_ledger", {"keyword": "虾仁"})
    assert [r[8] for r in data["preview_rows"]] == ["虾仁"]

    # 空参不过滤反例
    data = _preview("receipts_itemized_ledger", {})
    assert len(data["preview_rows"]) == 3


def test_ledger_paid_status_four_values():
    """paid_status：paid / overdue / unpaid / all 四值与空参不过滤。"""
    rid1, rid2, rid3 = _seed_ledger()

    paid = _preview("receipts_itemized_ledger", {"paid_status": "paid"})["preview_rows"]
    assert {r[0] for r in paid} == {f"#{rid1}"}

    overdue = _preview("receipts_itemized_ledger", {"paid_status": "overdue"})["preview_rows"]
    assert {r[0] for r in overdue} == {f"#{rid2}"}

    unpaid = _preview("receipts_itemized_ledger", {"paid_status": "unpaid"})["preview_rows"]
    # spec: unpaid = paid_at empty, so overdue (#rid2) is included in the unpaid set
    assert {r[0] for r in unpaid} == {f"#{rid2}", f"#{rid3}"}

    all_rows = _preview("receipts_itemized_ledger", {"paid_status": "all"})["preview_rows"]
    assert len(all_rows) == 3

    # 空参不过滤反例
    assert len(_preview("receipts_itemized_ledger", {})["preview_rows"]) == 3


def test_ledger_amount_range_includes_endpoints():
    """amount_min/amount_max 按单据总额含端点过滤；空参不过滤。"""
    rid1, rid2, rid3 = _seed_ledger()

    # 双端点：100(下界) 与 200(上界) 均含在内，300 被排除
    data = _preview("receipts_itemized_ledger",
                    {"amount_min": 100, "amount_max": 200})
    assert {r[0] for r in data["preview_rows"]} == {f"#{rid1}", f"#{rid2}"}

    # 下界含端点：200 与 300 都在，100 被排除
    assert {r[0] for r in _preview(
        "receipts_itemized_ledger", {"amount_min": 200})["preview_rows"]} == {
            f"#{rid2}", f"#{rid3}"}
    # 上界含端点：100 与 200 都在，300 被排除
    assert {r[0] for r in _preview(
        "receipts_itemized_ledger", {"amount_max": 200})["preview_rows"]} == {
            f"#{rid1}", f"#{rid2}"}
    assert len(_preview("receipts_itemized_ledger", {})["preview_rows"]) == 3


def test_ledger_currency_is_case_insensitive():
    """currency 精确匹配但输入自动 upper（hkd 命中 DB 中的 hkd）；空参不过滤。"""
    rid1, rid2, _rid3 = _seed_ledger()

    assert {r[0] for r in _preview(
        "receipts_itemized_ledger", {"currency": "hkd"})["preview_rows"]} == {f"#{rid1}"}
    assert {r[0] for r in _preview(
        "receipts_itemized_ledger", {"currency": "HKD"})["preview_rows"]} == {f"#{rid1}"}
    assert {r[0] for r in _preview(
        "receipts_itemized_ledger", {"currency": "cny"})["preview_rows"]} == {f"#{rid2}"}
    assert len(_preview("receipts_itemized_ledger", {})["preview_rows"]) == 3


# ---------------------------------------------------------------------------
# 2. 菜品成本与毛利表 dish_cost_margin
# ---------------------------------------------------------------------------
def _create_dish_with_consumption(name, category, price, qty=1.0,
                                  date_str="2026-03-02"):
    """建菜 + 售价；0.5kg/份（进价 20 => 理论/实际成本 10 元/份），当日售出 qty 份。"""
    from app.services.costing_service import CostingService

    sku, err = db.create_sku(f"{name}食材", category="肉类", base_unit="kg")
    assert sku, err
    db.update_sku(sku, current_stock=100.0, last_unit_price=20.0)
    s = db.get_session()
    try:
        CostingService.record_inbound_batch(s, sku, 100.0, 20.0, "kg", date="2026-03-01")
        s.commit()
    finally:
        s.close()

    resp = client.post("/api/dishes", json={
        "name": name, "category": category, "price": price,
        "ingredients": [
            {"sku_id": sku, "consumption_qty": 0.5, "unit": "kg", "yield_rate": 1.0},
        ],
    }, headers=_owner_headers())
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "success", resp.text
    dish_id = resp.json()["data"]["id"]

    batch = client.post("/api/dishes/daily_consumption/batch", json={
        "date": date_str, "items": [{"dish_id": dish_id, "quantity": qty}],
    }, headers=_staff_headers())
    assert batch.status_code == 200, batch.text
    return dish_id


def _cost_margin_period(**params):
    p = {"start_date": "2026-03-01", "end_date": "2026-03-31",
         "cost_granularity": "period"}
    p.update(params)
    return _preview("dish_cost_margin", p)


def test_cost_margin_period_margin_pct_max_filters_rows_keeps_summary_full():
    """period 粒度 margin_pct_max 只留低毛利行；summary 销售额/成本保持全量口径。"""
    # 售价 12，成本 10 => 实际毛利率 16.7%（低）；售价 60 => 83.3%（高）
    _create_dish_with_consumption("低毛利煲", "煲仔类", 12.0)
    _create_dish_with_consumption("高毛利汤", "汤羹类", 60.0)

    data = _cost_margin_period(margin_pct_max=50)
    assert {r[1] for r in data["preview_rows"]} == {"低毛利煲"}
    # summary 全量：总销售额 12 + 60 = 72；实际成本 10 + 10 = 20
    assert data["summary"]["总销售额(元)"] == 72.0
    assert data["summary"]["实际食材总成本(元)"] == 20.0

    # 空参不过滤反例
    data_all = _cost_margin_period()
    assert {r[1] for r in data_all["preview_rows"]} == {"低毛利煲", "高毛利汤"}
    assert data_all["summary"]["总销售额(元)"] == 72.0


def test_cost_margin_dish_status_inactive_kept_in_period_branch():
    """period 分支 dish_status 不再硬编码 active：inactive 菜可被筛出。"""
    _create_dish_with_consumption("在售煲", "煲仔类", 12.0)
    dish_off = _create_dish_with_consumption("下架汤", "汤羹类", 60.0)
    s = db.get_session()
    try:
        d = s.get(db._DishRow, dish_off)
        d.status = "inactive"
        s.commit()
    finally:
        s.close()

    inactive = _cost_margin_period(dish_status="inactive")
    assert {r[1] for r in inactive["preview_rows"]} == {"下架汤"}

    active = _cost_margin_period(dish_status="active")
    assert {r[1] for r in active["preview_rows"]} == {"在售煲"}

    # 空参（all）不过滤反例：两种状态都在
    all_rows = _cost_margin_period()
    assert {r[1] for r in all_rows["preview_rows"]} == {"在售煲", "下架汤"}


def test_cost_margin_dish_category_filter():
    """dish_category 按餐品分类过滤；空参不过滤。"""
    _create_dish_with_consumption("分类煲", "煲仔类", 12.0)
    _create_dish_with_consumption("分类汤", "汤羹类", 60.0)

    data = _cost_margin_period(dish_category="汤羹类")
    assert {r[1] for r in data["preview_rows"]} == {"分类汤"}

    assert {r[1] for r in _cost_margin_period()["preview_rows"]} == {"分类煲", "分类汤"}


# ---------------------------------------------------------------------------
# 3. 采购补货建议清单 dish_ingredient_consumption
# ---------------------------------------------------------------------------
def _seed_consumption():
    """充足食材（库存 98 > 线 5，建议 0）与紧缺食材（库存负，建议 > 0）。"""
    sku_full, err = db.create_sku("充足食材", category="蔬菜", base_unit="kg",
                                  min_stock_alert=5.0)
    assert sku_full, err
    db.update_sku(sku_full, current_stock=100.0, last_unit_price=10.0)
    db.apply_stock_log(sku_full, "充足食材", 2.0, "kg", 20.0, "测试供应商",
                       "2026-03-05", None, "consume", tenant_id="default")

    sku_low, err = db.create_sku("紧缺食材", category="肉类", base_unit="kg",
                                 min_stock_alert=10.0)
    assert sku_low, err
    db.update_sku(sku_low, current_stock=2.0, last_unit_price=10.0)
    db.apply_stock_log(sku_low, "紧缺食材", 3.0, "kg", 30.0, "测试供应商",
                       "2026-03-05", None, "consume", tenant_id="default")
    return sku_full, sku_low


def test_consumption_only_suggested_filters_rows_but_summary_full():
    """only_suggested=true 后无"库存充足"行；summary 种数/kg 仍为全量口径。"""
    _seed_consumption()
    period = {"start_date": "2026-03-01", "end_date": "2026-03-31"}

    all_data = _preview("dish_ingredient_consumption", dict(period))
    assert {r[0] for r in all_data["preview_rows"]} == {"充足食材", "紧缺食材"}

    data = _preview("dish_ingredient_consumption", {**period, "only_suggested": "true"})
    assert {r[0] for r in data["preview_rows"]} == {"紧缺食材"}
    assert all(r[8] != "库存充足" for r in data["preview_rows"])
    # summary 全量：种数 2、折算 kg 合计 2 + 3 = 5
    assert data["summary"]["消耗食材种数"] == 2
    assert data["summary"]["折算消耗合计(kg)"] == 5.0

    # 空参不过滤反例
    assert len(_preview("dish_ingredient_consumption", dict(period))["preview_rows"]) == 2


# ---------------------------------------------------------------------------
# 4. 采购进价波动与比价表 inventory_price_anomaly
# ---------------------------------------------------------------------------
def _seed_price_anomaly():
    """涨价 SKU：30 天前 10 元 / 3 天前 20 元；跌价 SKU：10 → 5。"""
    old = (date.today() - timedelta(days=30)).isoformat()
    recent = (date.today() - timedelta(days=3)).isoformat()

    sku_up, err = db.create_sku("涨价食材", category="肉类", base_unit="kg")
    assert sku_up, err
    db.apply_stock_log(sku_up, "涨价食材", 10.0, "kg", 100.0, "V1",
                       old, None, "in", tenant_id="default")
    db.apply_stock_log(sku_up, "涨价食材", 10.0, "kg", 200.0, "V1",
                       recent, None, "in", tenant_id="default")

    sku_down, err = db.create_sku("跌价食材", category="蔬菜", base_unit="kg")
    assert sku_down, err
    db.apply_stock_log(sku_down, "跌价食材", 10.0, "kg", 100.0, "V2",
                       old, None, "in", tenant_id="default")
    db.apply_stock_log(sku_down, "跌价食材", 10.0, "kg", 50.0, "V2",
                       recent, None, "in", tenant_id="default")
    return sku_up, sku_down


def test_price_anomaly_direction_is_mutually_exclusive():
    """direction=up 只留涨价、down 只留跌价；空参/all 不过滤。"""
    _seed_price_anomaly()

    up = _preview("inventory_price_anomaly", {"direction": "up"})["preview_rows"]
    assert {r[0] for r in up} == {"涨价食材"}
    assert up[0][6].startswith("+"), f"涨价波动幅度应为正号: {up[0][6]}"

    down = _preview("inventory_price_anomaly", {"direction": "down"})["preview_rows"]
    assert {r[0] for r in down} == {"跌价食材"}
    assert down[0][6].startswith("-"), f"跌价波动幅度应为负号: {down[0][6]}"

    assert {r[0] for r in _preview(
        "inventory_price_anomaly", {"direction": "all"})["preview_rows"]} == {
            "涨价食材", "跌价食材"}
    # 空参不过滤反例
    assert len(_preview("inventory_price_anomaly", {})["preview_rows"]) == 2


def test_price_anomaly_threshold_override_takes_effect_and_does_not_write_back():
    """threshold_pct 临时覆盖生效，且不写回租户 SSOT（调用前后 get_threshold_pct 不变）。"""
    from app.services.price_anomaly import get_threshold_pct

    _seed_price_anomaly()
    tenant = "default"
    before = get_threshold_pct(tenant)

    # 低阈值 1%：涨幅 33.3% > 1 → 异动
    low = _preview("inventory_price_anomaly", {"threshold_pct": 1})
    row_up_low = [r for r in low["preview_rows"] if r[0] == "涨价食材"][0]
    assert row_up_low[7] == "[涨价异动]"
    assert low["summary"]["生效阈值(%)"] == 1.0

    # 高阈值 99%：涨幅 33.3% <= 99 → 正常
    high = _preview("inventory_price_anomaly", {"threshold_pct": 99})
    row_up_high = [r for r in high["preview_rows"] if r[0] == "涨价食材"][0]
    assert row_up_high[7] == "正常"

    # SSOT 未被写回
    assert get_threshold_pct(tenant) == before

    # 再调一次不带参数：仍走 SSOT，且 summary 无覆盖提示
    default = _preview("inventory_price_anomaly", {})
    assert "生效阈值(%)" not in default["summary"]
    assert get_threshold_pct(tenant) == before


def test_price_anomaly_window_days_truncates_and_header_follows():
    """window_days=7/30 因窗口截断产生不同均价，表头同步变「近N天均价(元)」。"""
    _seed_price_anomaly()

    d7 = _preview("inventory_price_anomaly", {"window_days": 7})
    assert "近7天均价(元)" in d7["headers"]
    row_up_7 = [r for r in d7["preview_rows"] if r[0] == "涨价食材"][0]
    assert row_up_7[5] == 20.0  # 7 天窗口只剩 3 天前那一笔
    assert d7["summary"]["均价窗口(天)"] == 7

    d30 = _preview("inventory_price_anomaly", {"window_days": 30})
    assert "近30天均价(元)" in d30["headers"]
    row_up_30 = [r for r in d30["preview_rows"] if r[0] == "涨价食材"][0]
    assert row_up_30[5] == 15.0  # (10 + 20) / 2
    assert d30["summary"]["均价窗口(天)"] == 30

    # 空参默认 30 天
    d_default = _preview("inventory_price_anomaly", {})
    assert "近30天均价(元)" in d_default["headers"]
    assert d_default["summary"]["均价窗口(天)"] == 30


# ---------------------------------------------------------------------------
# 5. 期间花销汇总表 spend_summary
# ---------------------------------------------------------------------------
def _seed_spend_summary():
    dept_a = db.create_department("后厨部")
    dept_b = db.create_department("楼面部")
    sup_a, err = db.create_supplier("供应商甲")
    assert sup_a, err
    sup_b, err = db.create_supplier("供应商乙")
    assert sup_b, err

    db.create_receipt_record(
        supplier_name="供应商甲", status="approved", receipt_date="2026-03-10",
        total_amount=100.0, department_id=dept_a,
        created_at="2026-03-10T08:00:00", updated_at="2026-03-10T08:00:00")
    db.create_receipt_record(
        supplier_name="供应商乙", status="approved", receipt_date="2026-03-12",
        total_amount=40.0, department_id=dept_b,
        created_at="2026-03-12T08:00:00", updated_at="2026-03-12T08:00:00")
    return dept_a, dept_b, sup_a, sup_b


_SPEND_PERIOD = {"start_date": "2026-03-01", "end_date": "2026-03-31"}


def test_spend_summary_department_focus_and_total_recompute():
    """department_id 聚焦只剩该部门行；合计行从过滤后条目重算。"""
    _dept_a, dept_b, _sup_a, _sup_b = _seed_spend_summary()

    data = _preview("spend_summary", {**_SPEND_PERIOD, "department_id": dept_b},
                    headers=_owner_headers())
    rows = data["preview_rows"]
    assert [r[1] for r in rows] == ["楼面部", "合计"]
    assert rows[0][2] == 40.0
    assert rows[1][1] == "合计" and rows[1][2] == 40.0

    # 空参不过滤反例：两个部门 + 未分配桶 + 合计
    all_data = _preview("spend_summary", dict(_SPEND_PERIOD),
                        headers=_owner_headers())
    labels = [r[1] for r in all_data["preview_rows"]]
    assert "后厨部" in labels and "楼面部" in labels


def test_spend_summary_supplier_focus():
    """spend_pivot=supplier + supplier_id 聚焦后只剩该供应商行 + 合计重算。"""
    _dept_a, _dept_b, sup_a, _sup_b = _seed_spend_summary()

    data = _preview("spend_summary",
                    {**_SPEND_PERIOD, "spend_pivot": "supplier", "supplier_id": sup_a},
                    headers=_owner_headers())
    rows = data["preview_rows"]
    assert [r[1] for r in rows] == ["供应商甲", "合计"]
    assert rows[0][2] == 100.0
    assert rows[1][2] == 100.0


def test_spend_summary_invalid_focus_returns_single_empty_total_row():
    """无效聚焦（不属于本租户）安全返回空桶：仅一条合计行且本期为 0。"""
    _seed_spend_summary()

    dept_data = _preview("spend_summary",
                         {**_SPEND_PERIOD, "department_id": 999999},
                         headers=_owner_headers())
    dept_rows = dept_data["preview_rows"]
    assert len(dept_rows) == 1
    assert dept_rows[0][1] == "合计"
    assert dept_rows[0][2] == 0.0
    assert dept_data["summary"]["本期采购总计(元)"] == 0.0

    sup_data = _preview("spend_summary",
                        {**_SPEND_PERIOD, "spend_pivot": "supplier",
                         "supplier_id": 999999},
                        headers=_owner_headers())
    sup_rows = sup_data["preview_rows"]
    assert len(sup_rows) == 1
    assert sup_rows[0][1] == "合计"
    assert sup_rows[0][2] == 0.0


def test_spend_summary_amount_min_filters_rows_but_keeps_total():
    """amount_min 行级过滤（合计行始终保留，且合计=过滤后条目重算）。"""
    _seed_spend_summary()

    data = _preview("spend_summary", {**_SPEND_PERIOD, "amount_min": 80},
                    headers=_owner_headers())
    labels = [r[1] for r in data["preview_rows"]]
    assert "后厨部" in labels
    assert "楼面部" not in labels
    total = [r for r in data["preview_rows"] if r[1] == "合计"][0]
    assert total[2] == 100.0

    # 空参不过滤反例
    all_rows = _preview("spend_summary", dict(_SPEND_PERIOD),
                        headers=_owner_headers())["preview_rows"]
    assert "楼面部" in [r[1] for r in all_rows]


# ---------------------------------------------------------------------------
# 6. 出入库全量流水台账 inventory_stock_logs
# ---------------------------------------------------------------------------
def _seed_stock_logs():
    sku, err = db.create_sku("台账食材", base_unit="kg")
    assert sku, err
    db.update_sku(sku, current_stock=100.0, last_unit_price=10.0)
    db.apply_stock_log(sku, "台账食材", 10.0, "kg", 100.0, "德利行食品",
                       "2026-03-05", 1502, "in", tenant_id="default")
    db.apply_stock_log(sku, "台账食材", 5.0, "kg", 50.0, "美好食品",
                       "2026-03-06", None, "consume", tenant_id="default")
    db.apply_stock_log(sku, "台账食材", 2.0, "kg", 20.0, "其他供应商",
                       "2026-03-07", 1503, "waste", tenant_id="default")
    return sku


def test_stock_logs_receipt_ref_accepts_hash_prefix_and_empty_for_non_numeric():
    """receipt_ref 兼容 `#1502` / `1502`；无数字输入落到空结果（不静默全量）。"""
    _seed_stock_logs()

    for ref in ("#1502", "1502"):
        data = _preview("inventory_stock_logs", {"receipt_ref": ref})
        assert len(data["preview_rows"]) == 1, ref
        assert data["preview_rows"][0][8] == "#1502"

    empty = _preview("inventory_stock_logs", {"receipt_ref": "ABC"})
    assert empty["total_rows"] == 0
    assert empty["preview_rows"] == []

    # 空参不过滤反例
    assert len(_preview("inventory_stock_logs", {})["preview_rows"]) == 3


def test_stock_logs_vendor_keyword_fuzzy_match():
    """vendor 参数 LIKE %v% 模糊命中；空参不过滤。"""
    _seed_stock_logs()

    data = _preview("inventory_stock_logs", {"vendor": "美好"})
    assert len(data["preview_rows"]) == 1
    assert data["preview_rows"][0][7] == "美好食品"

    assert len(_preview("inventory_stock_logs", {})["preview_rows"]) == 3


# ---------------------------------------------------------------------------
# 7. 库存货值结存表 inventory_valuation
# ---------------------------------------------------------------------------
def _seed_valuation():
    sku_a, err = db.create_sku("高价食材", category="肉类", base_unit="kg")
    assert sku_a, err
    db.update_sku(sku_a, current_stock=10.0, last_unit_price=100.0)

    sku_b, err = db.create_sku("低价食材", category="蔬菜", base_unit="kg")
    assert sku_b, err
    db.update_sku(sku_b, current_stock=1.0, last_unit_price=5.0)


def test_valuation_value_min_filters_rows_but_summary_keeps_full_total():
    """value_min 行级过滤行，summary 估算总货值/标准重量合计保持全量口径。"""
    _seed_valuation()

    full = _preview("inventory_valuation", {})
    assert {r[1] for r in full["preview_rows"]} == {"高价食材", "低价食材"}
    assert full["summary"]["估算总货值(元)"] == 1005.0

    data = _preview("inventory_valuation", {"value_min": 100})
    assert {r[1] for r in data["preview_rows"]} == {"高价食材"}
    # summary 仍为全量：10 * 100 + 1 * 5 = 1005
    assert data["summary"]["估算总货值(元)"] == 1005.0
    assert data["summary"]["标准重量合计(kg)"] == 11.0


# ---------------------------------------------------------------------------
# 8. REPORTS_META 登记名契约（8 张报表不变 + 新 filter 登记）
# ---------------------------------------------------------------------------
def test_reports_meta_eight_reports_and_new_filter_registration():
    """8 张报表集合不变；各表 filters 数组包含 spec §8 新登记名。"""
    res = client.get("/api/export/reports", headers=_staff_headers())
    assert res.status_code == 200
    reports = res.json()["data"]
    assert len(reports) == 8

    expected_ids = {
        "inventory_stocktake",
        "dish_ingredient_consumption",
        "inventory_valuation",
        "inventory_price_anomaly",
        "dish_cost_margin",
        "spend_summary",
        "inventory_stock_logs",
        "receipts_itemized_ledger",
    }
    by_id = {r["id"]: r for r in reports}
    assert set(by_id) == expected_ids

    expected_filters = {
        "receipts_itemized_ledger": {"keyword", "paid_status", "amount_range", "currency"},
        "dish_cost_margin": {"margin_threshold", "dish_status", "dish_category"},
        "dish_ingredient_consumption": {"only_suggested"},
        "inventory_price_anomaly": {"direction", "threshold_override", "window_days"},
        "spend_summary": {"pivot_focus", "amount_range"},
        "inventory_stock_logs": {"receipt_ref", "vendor_keyword"},
        "inventory_valuation": {"value_min"},
    }
    for rid, filters in expected_filters.items():
        declared = set(by_id[rid].get("filters", []))
        assert filters <= declared, f"{rid} 缺登记名: {filters - declared}"
