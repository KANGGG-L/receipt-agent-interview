# -*- coding: utf-8 -*-
"""WS4 回归：GET /api/inventory/skus/{sku_id}（SKU 详情接口）。

why：详情接口把「结存 / 计件折算 / 派生加权成本 / 价格趋势 / 本租户生效阈值」
       聚合成一个只读视图。其中
       - `avg_cost` 必须是**批次池派生**的加权值（Σ remaining×unit_cost / Σ remaining），
         而不是 SKU 上的 last_unit_price；
       - 计件单位的 `standard_kg` 必须是 None（不假装折算成 0）；
       - `threshold_pct` 必须走 settings（本租户覆盖 → 全局 → 缺省），不得硬编码 10。

覆盖：
1. staff 可读；字段齐备；计件单位 standard_kg 为 None（不是 0）
2. 不存在 / 跨租户 → 404
3. avg_cost 与按批次池手工计算一致（2 个批次 + 一次 FIFO 消耗）
4. threshold_pct 走 settings（本租户覆盖生效，非硬编码 10）

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import atexit
import datetime
import importlib
import os
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="inventory_sku_detail_api_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_sku_detail.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402
from app.services import settings_service  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"sd_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _sku(_db, name, base_unit="斤", min_stock_alert=0.0, tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit=base_unit,
                                 min_stock_alert=min_stock_alert,
                                 tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _in(_db, sku_id, unit_price, qty=1.0, date="2026-08-01", tenant="default"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤",
        amount=round(unit_price * qty, 4), vendor="測試供應商", date=date,
        receipt_id=None, kind="in", tenant_id=tenant)


def _consume(_db, sku_id, qty, date="2026-08-02", tenant="default"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤", amount=0.0,
        vendor="", date=date, receipt_id=None, kind="consume",
        tenant_id=tenant)


def _headers(role="staff", tenant=None):
    h = {"X-Role": role}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


def _get(client, sku_id, role="owner", tenant=None):
    return client.get(f"/api/inventory/skus/{sku_id}",
                      headers=_headers(role, tenant))


# -------------------------------------------------------------
# 1. staff 可读 + 字段齐备 + 计件单位 standard_kg 为 None
# -------------------------------------------------------------
def test_staff_reads_detail_and_weight_unit_kg():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "詳情菜心", base_unit="斤", min_stock_alert=2.0)
    _in(_db, sku, 10.0, qty=10.0)   # current_stock 10 斤

    ret = _get(client, sku, role="staff")
    assert ret.status_code == 200, ret.text
    data = ret.json()["data"]
    for field in ("id", "name", "sku_code", "category", "base_unit",
                  "current_stock", "standard_kg", "min_stock_alert",
                  "last_unit_price", "active", "is_low_stock", "avg_cost",
                  "vs_avg_pct", "avg_30d", "is_anomaly", "latest_price",
                  "threshold_pct"):
        assert field in data, f"缺少字段 {field}: {data}"
    assert data["id"] == sku and data["name"] == "詳情菜心"
    assert data["current_stock"] == 10.0
    # 斤 → 0.6048 kg（港斤）：10 * 0.6048 = 6.048
    assert data["standard_kg"] == 6.048, data
    assert data["is_low_stock"] is False
    assert _get(client, sku, role="owner").status_code == 200


def test_count_unit_standard_kg_is_none_not_zero():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "計件雞蛋", base_unit="份")
    _in(_db, sku, 2.5, qty=8.0)  # current_stock 8 份

    data = _get(client, sku).json()["data"]
    assert data["current_stock"] == 8.0
    assert data["standard_kg"] is None, f"计件单位不得折算：{data['standard_kg']}"
    assert data["standard_kg"] != 0

    # 空单位同样不假装折算
    empty = _sku(_db, "無單位商品", base_unit="")
    assert _get(client, empty).json()["data"]["standard_kg"] is None


# -------------------------------------------------------------
# 2. 不存在 / 跨租户 → 404
# -------------------------------------------------------------
def test_missing_and_cross_tenant_return_404():
    _db = _fresh_db()
    client = _client()
    assert _get(client, 999999).status_code == 404

    sku_t2 = _sku(_db, "他租戶商品", tenant="t2")
    _in(_db, sku_t2, 12.0, tenant="t2")
    # 默认租户视角：跨租户等同不存在
    assert _get(client, sku_t2).status_code == 404
    # t2 视角：本租户内正常可读
    own = _get(client, sku_t2, tenant="t2")
    assert own.status_code == 200 and own.json()["data"]["id"] == sku_t2


# -------------------------------------------------------------
# 3. avg_cost 与按批次池手工计算一致（2 批次 + 1 次 FIFO 消耗）
# -------------------------------------------------------------
def test_avg_cost_matches_manual_batch_pool():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "加權成本腩肉")
    # 批次 1：10 斤 @10（先进先出先扣）
    _in(_db, sku, 10.0, qty=10.0, date="2026-07-01")
    # 批次 2：10 斤 @20
    _in(_db, sku, 20.0, qty=10.0, date="2026-07-10")
    # 消耗 5 斤 → 全部从批次 1 扣：剩 5 @10 + 10 @20
    _consume(_db, sku, 5.0, date="2026-07-20")

    manual = round((5.0 * 10.0 + 10.0 * 20.0) / (5.0 + 10.0), 4)
    assert manual == 16.6667

    data = _get(client, sku).json()["data"]
    assert data["avg_cost"] == manual, (data["avg_cost"], manual)
    assert data["current_stock"] == 15.0, data
    # 明确不等于 SKU 上的最新价（证明是批次池派生）
    assert data["last_unit_price"] == 20.0
    assert data["avg_cost"] != data["last_unit_price"]


# -------------------------------------------------------------
# 4. threshold_pct 走 settings，不硬编码 10
# -------------------------------------------------------------
def test_threshold_pct_reads_settings_and_drives_anomaly():
    _db = _fresh_db()
    client = _client()
    tenant = "shopA"
    sku = _sku(_db, "閾值驗證白菜", tenant=tenant)

    today = datetime.date.today()
    d30 = (today - datetime.timedelta(days=30)).isoformat()
    d0 = today.isoformat()
    # avg 15，latest 20 → vs +33.3%
    _in(_db, sku, 10.0, qty=1.0, date=d30, tenant=tenant)
    _in(_db, sku, 20.0, qty=1.0, date=d0, tenant=tenant)

    # 缺省 10 → 判异动，threshold_pct 回显缺省
    before = _get(client, sku, tenant=tenant).json()["data"]
    assert before["threshold_pct"] == 10.0, before
    assert before["is_anomaly"] is True, before

    # 门店覆盖 50 → 不再判异动，回显 50（与判定同源）
    settings_service.set_value("price_anomaly_threshold_pct", 50.0,
                               tenant_id=tenant)
    after = _get(client, sku, tenant=tenant).json()["data"]
    assert after["threshold_pct"] == 50.0, after
    assert after["is_anomaly"] is False, after

    # 全局改 40：默认租户详情读到 40，shopA 仍是 50（三级回退）
    settings_service.set_value("price_anomaly_threshold_pct", 40.0)
    assert _get(client, sku, tenant=tenant).json()["data"]["threshold_pct"] == 50.0
    default_sku = _sku(_db, "全局閾值雞蛋")
    assert _get(client, default_sku).json()["data"]["threshold_pct"] == 40.0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
