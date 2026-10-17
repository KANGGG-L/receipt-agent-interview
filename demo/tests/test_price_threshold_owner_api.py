# -*- coding: utf-8 -*-
"""WS3：owner 门店级价格预警阈值自助端点测试。

端点：`GET /api/inventory/price_threshold`、`PUT /api/inventory/price_threshold`。

why：阈值归属改为「admin 全局默认 + owner 门店覆盖」后，"owner 能改自己门店、
      改不动别人的门店与全局默认"必须有硬约束；且改完阈值后**同一 SKU 的异动判定
      与走势 summary 的 threshold_pct 必须同步变化** —— 这是 WS3 的核心不变量
      （此前 summary.threshold_pct 是硬编码 10.0，与判定所用的实时阈值可能不一致）。

覆盖：
1. GET：staff → 403；owner → 200 且返回 threshold_pct / global_default_pct
2. PUT：owner 200；0 / 负数 / >100 → 400；写入后 GET 可读到新值
3. 门店覆盖不污染默认租户：owner 在 t1 写 25，默认租户读值仍是 10
4. 核心不变量：改阈值后同一 SKU 的 is_anomaly 与 /api/price_history 的
   summary.threshold_pct 同步变化（/api/inventory 的 meta 同源）

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import datetime
import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="price_threshold_owner_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_price_threshold.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402

THRESHOLD_KEY = "price_anomaly_threshold_pct"


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"pt_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _sku(_db, name, base_unit="斤", tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit=base_unit,
                                 tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _in(_db, sku_id, unit_price, date, qty=1.0, tenant="default"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤",
        amount=round(unit_price * qty, 4), vendor="測試供應商", date=date,
        receipt_id=None, kind="in", tenant_id=tenant)


def _days_ago(n):
    return (datetime.date.today() - datetime.timedelta(days=n)).isoformat()


def _headers(role, tenant=None):
    h = {"X-Role": role}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


# -------------------------------------------------------------
# 1. GET 权限与载荷
# -------------------------------------------------------------
def test_get_staff_403_owner_200_with_payload():
    _fresh_db()
    client = _client()

    staff = client.get("/api/inventory/price_threshold",
                       headers=_headers("staff"))
    assert staff.status_code == 403, staff.text

    anon = client.get("/api/inventory/price_threshold")
    assert anon.status_code == 401, anon.text

    owner = client.get("/api/inventory/price_threshold",
                       headers=_headers("owner"))
    assert owner.status_code == 200, owner.text
    data = owner.json()["data"]
    assert data["threshold_pct"] == 10.0, data
    assert data["global_default_pct"] == 10.0, data


# -------------------------------------------------------------
# 2. PUT 校验 + 持久化（写入后 GET 可读） + 门店/全局互不污染
# -------------------------------------------------------------
def test_put_validation_persistence_and_tenant_scope():
    _db = _fresh_db()
    client = _client()

    # staff 不得写
    denied = client.put("/api/inventory/price_threshold",
                        json={"threshold_pct": 25},
                        headers=_headers("staff"))
    assert denied.status_code == 403, denied.text

    # 越界值 → 400
    for bad in (0, -1, -0.5, 100.1, 150):
        ret = client.put("/api/inventory/price_threshold",
                         json={"threshold_pct": bad},
                         headers=_headers("owner", tenant="shopA"))
        assert ret.status_code == 400, (bad, ret.status_code, ret.text)

    # 上界 100 合法
    upper = client.put("/api/inventory/price_threshold",
                       json={"threshold_pct": 100},
                       headers=_headers("owner", tenant="shopA"))
    assert upper.status_code == 200, upper.text

    # 门店写 25 → 本门店读到 25，全局默认仍是 10
    ok = client.put("/api/inventory/price_threshold",
                    json={"threshold_pct": 25},
                    headers=_headers("owner", tenant="shopA"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["threshold_pct"] == 25.0, ok.text
    assert ok.json()["data"]["global_default_pct"] == 10.0, ok.text

    shop = client.get("/api/inventory/price_threshold",
                      headers=_headers("owner", tenant="shopA")).json()["data"]
    assert shop["threshold_pct"] == 25.0, shop

    default_view = client.get("/api/inventory/price_threshold",
                              headers=_headers("owner")).json()["data"]
    assert default_view["threshold_pct"] == 10.0, default_view
    assert default_view["global_default_pct"] == 10.0, default_view

    # 另一个门店看不到 shopA 的覆盖
    other = client.get("/api/inventory/price_threshold",
                       headers=_headers("owner", tenant="shopB")).json()["data"]
    assert other["threshold_pct"] == 10.0, other

    # 落库核实：门店覆盖在 app_setting_overrides，全局桶未被改写
    assert _db.get_app_setting(THRESHOLD_KEY) is None
    assert _db.get_app_setting(THRESHOLD_KEY, tenant_id="shopA") == "25.0"


# -------------------------------------------------------------
# 3. 核心不变量：改阈值 → is_anomaly 与 summary.threshold_pct 同步变化
# -------------------------------------------------------------
def test_threshold_change_moves_anomaly_and_summary_in_lockstep():
    _db = _fresh_db()
    client = _client()
    tenant = "shopA"
    sku = _sku(_db, "同步驗證菜心", tenant=tenant)
    # 窗口内两点：10 与 20 → avg 15，latest 20 → vs +33.3%
    _in(_db, sku, 10.0, _days_ago(30), tenant=tenant)
    _in(_db, sku, 20.0, _days_ago(0), tenant=tenant)

    def _snapshot(role="owner"):
        hist = client.get(f"/api/price_history/{sku}",
                          headers=_headers(role, tenant=tenant)).json()
        # UAT-021：price_anomaly / meta.threshold 仅 owner 可见；staff 恒 False/None
        inv = client.get("/api/inventory",
                         headers=_headers("owner", tenant=tenant)).json()
        row = next(x for x in inv["data"] if x["id"] == sku)
        return {
            "summary_threshold": hist["summary"]["threshold_pct"],
            "summary_anomaly": hist["summary"]["is_anomaly"],
            "inv_anomaly": row["price_anomaly"],
            "inv_meta_threshold": inv["meta"]["price_anomaly_threshold_pct"],
        }

    # 默认阈值 10：+33.3% 判异动
    before = _snapshot()
    assert before["summary_threshold"] == 10.0, before
    assert before["summary_anomaly"] is True, before
    assert before["inv_anomaly"] is True, before
    assert before["inv_meta_threshold"] == 10.0, before

    # 阈值调到 50：不再判异动（summary / 列表 / meta 三处同源同变）
    put_high = client.put("/api/inventory/price_threshold",
                          json={"threshold_pct": 50},
                          headers=_headers("owner", tenant=tenant))
    assert put_high.status_code == 200, put_high.text
    after_high = _snapshot()
    assert after_high["summary_threshold"] == 50.0, after_high
    assert after_high["summary_anomaly"] is False, after_high
    assert after_high["inv_anomaly"] is False, after_high
    assert after_high["inv_meta_threshold"] == 50.0, after_high

    # 阈值调到 5：重新判异动
    put_low = client.put("/api/inventory/price_threshold",
                         json={"threshold_pct": 5},
                         headers=_headers("owner", tenant=tenant))
    assert put_low.status_code == 200, put_low.text
    after_low = _snapshot()
    assert after_low["summary_threshold"] == 5.0, after_low
    assert after_low["summary_anomaly"] is True, after_low
    assert after_low["inv_anomaly"] is True, after_low


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
