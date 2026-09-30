# -*- coding: utf-8 -*-
"""复核侧提示后端：GET /api/vendor_price_hint（供应商×SKU 历史价格区间）测试。

why：前端的 node harness（tests/test_vendor_price_hint.js）只验证「注入数据 → 渲染」，
区间本身算得对不对（按供应商过滤、min/max、是否把当前单价判为超出、跨租户不可见）
必须由后端用例钉住 —— 否则「前端把数字原样显示」这件事没有任何意义。

覆盖：
1. 区间/次数只算「本供应商」的进货流水（别的供应商的价不参与）
2. 区间取 min/max（不是均价），次数 = 有效正价流水条数
3. 当前单价高于/低于区间 → out_of_range + direction；区间内 → False
4. 不传 unit_price → out_of_range is None（不猜）
5. 该供应商无历史 / vendor 为空 → has_history=False（前端据此隐藏空壳）
6. amount=0 的流水（无有效单价）不计入
7. 跨租户 SKU 视为不存在；未知 SKU 报 error
8. 匿名 401；staff / owner 均可读（复核界面由店员使用）
9. 数据源是库存流水（inventory_log kind=in），不是 SKU 上的 last_unit_price

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import importlib
import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB 与 RAG 目录（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="vendor_price_hint_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_vendor_price_hint.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    """每个用例独立数据库文件，避免跨用例串扰。"""
    os.environ["DB_PATH"] = os.path.join(_TMP, f"hint_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _sku(_db, name, tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit="斤", tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _in(_db, sku_id, name, vendor, unit_price, qty=1.0, date="2026-08-01", tenant="default"):
    """写一条进货流水（amount = 单价 * 数量），与 approve 入账同形态。"""
    _db.apply_stock_log(
        sku_id=sku_id, name=name, qty=qty, unit="斤",
        amount=round(unit_price * qty, 4), vendor=vendor, date=date,
        receipt_id=None, kind="in", tenant_id=tenant)


def _get(client, sku_id, vendor, unit_price=None, role="staff", tenant=None):
    headers = {"X-Role": role}
    if tenant:
        headers["X-Tenant-Id"] = tenant
    params = {"sku_id": sku_id, "vendor": vendor}
    if unit_price is not None:
        params["unit_price"] = unit_price
    return client.get("/api/vendor_price_hint", params=params, headers=headers)


# -------------------------------------------------------------
# 1. 区间与次数只算本供应商 + 取 min/max
# -------------------------------------------------------------
def test_range_count_only_from_same_vendor():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "菜心")
    for p in (10.0, 12.0, 20.0):
        _in(_db, sku, "菜心", "佳記欄", p)
    _in(_db, sku, "菜心", "別家供應商", 99.0)   # 别的供应商：不得参与

    ret = _get(client, sku, "佳記欄", unit_price=15).json()
    assert ret["status"] == "success"
    assert ret["has_history"] is True
    assert ret["count"] == 3, ret
    assert ret["low"] == 10.0 and ret["high"] == 20.0, ret
    assert ret["out_of_range"] is False and ret["direction"] == "", ret
    assert ret["vendor"] == "佳記欄" and ret["sku_name"] == "菜心"


# -------------------------------------------------------------
# 2. 区间外 → 标记 + 方向
# -------------------------------------------------------------
def test_out_of_range_up_and_down():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "腩肉")
    _in(_db, sku, "腩肉", "佳記欄", 10.0)
    _in(_db, sku, "腩肉", "佳記欄", 20.0)

    up = _get(client, sku, "佳記欄", unit_price=25).json()
    assert up["out_of_range"] is True and up["direction"] == "up", up

    down = _get(client, sku, "佳記欄", unit_price=5).json()
    assert down["out_of_range"] is True and down["direction"] == "down", down


# -------------------------------------------------------------
# 3. 不传当前单价 → 不判定（不猜）
# -------------------------------------------------------------
def test_missing_unit_price_means_no_judgement():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "唐生菜")
    _in(_db, sku, "唐生菜", "佳記欄", 8.0)

    ret = _get(client, sku, "佳記欄").json()
    assert ret["has_history"] is True and ret["count"] == 1
    assert ret["out_of_range"] is None and ret["direction"] == "", ret


# -------------------------------------------------------------
# 4. 无历史 / vendor 为空 → has_history=False（前端隐藏空壳）
# -------------------------------------------------------------
def test_unknown_vendor_and_blank_vendor_have_no_history():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "白菜")
    _in(_db, sku, "白菜", "佳記欄", 6.0)

    other = _get(client, sku, "從未進過貨的商戶").json()
    assert other["status"] == "success" and other["has_history"] is False
    assert other["count"] == 0, other

    blank = _get(client, sku, "   ").json()
    assert blank["status"] == "success" and blank["has_history"] is False, blank

    no_log_sku = _sku(_db, "無流水商品")
    empty = _get(client, no_log_sku, "佳記欄").json()
    assert empty["has_history"] is False and empty["count"] == 0, empty


# -------------------------------------------------------------
# 5. amount=0（无有效单价）不计入
# -------------------------------------------------------------
def test_zero_amount_rows_are_not_counted():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "豆腐")
    _in(_db, sku, "豆腐", "佳記欄", 3.0)
    _in(_db, sku, "豆腐", "佳記欄", 0.0)   # 赠品 / 无价行

    ret = _get(client, sku, "佳記欄").json()
    assert ret["count"] == 1 and ret["low"] == 3.0 and ret["high"] == 3.0, ret


# -------------------------------------------------------------
# 6. 跨租户视为不存在；未知 SKU 报 error
# -------------------------------------------------------------
def test_cross_tenant_and_unknown_sku():
    _db = _fresh_db()
    client = _client()
    t2_sku = _sku(_db, "他租戶商品", tenant="t2")
    _in(_db, t2_sku, "他租戶商品", "佳記欄", 12.0, tenant="t2")

    # 默认租户视角：拿不到 t2 的 SKU（get_sku 对跨租户返回 None）
    cross = _get(client, t2_sku, "佳記欄").json()
    assert cross["status"] == "error", cross

    # t2 视角：本租户内正常可读
    own = _get(client, t2_sku, "佳記欄", tenant="t2").json()
    assert own["status"] == "success" and own["has_history"] is True, own

    unknown = _get(client, 999999, "佳記欄").json()
    assert unknown["status"] == "error", unknown


# -------------------------------------------------------------
# 7. 权限：匿名 401；staff / owner 可读
# -------------------------------------------------------------
def test_auth_anonymous_401_and_roles_can_read():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "雞蛋")
    _in(_db, sku, "雞蛋", "佳記欄", 2.5)

    anon = client.get("/api/vendor_price_hint",
                      params={"sku_id": sku, "vendor": "佳記欄"})
    assert anon.status_code == 401, anon.text

    for role in ("staff", "owner", "admin"):
        ret = _get(client, sku, "佳記欄", unit_price=2.5, role=role)
        assert ret.status_code == 200, (role, ret.text)
        assert ret.json()["has_history"] is True, (role, ret.json())


# -------------------------------------------------------------
# 8. 数据源是库存流水，不是 SKU 上的 last_unit_price
# -------------------------------------------------------------
def test_source_is_inventory_log_not_sku_last_price():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "凍肉")
    _in(_db, sku, "凍肉", "佳記欄", 30.0, qty=2.0)   # 单价 30（amount 60）

    session = _db.get_session()
    try:
        row = session.get(_db._SkuRow, sku)
        assert round(float(row.last_unit_price or 0), 2) == 30.0
        row.last_unit_price = 999.0        # 人为改 SKU 上的「最新价」
        session.commit()
    finally:
        session.close()

    ret = _get(client, sku, "佳記欄", unit_price=30).json()
    assert ret["low"] == 30.0 and ret["high"] == 30.0, ret
    assert ret["out_of_range"] is False, ret
