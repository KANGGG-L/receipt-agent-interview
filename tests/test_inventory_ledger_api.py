# -*- coding: utf-8 -*-
"""WS4 回归：GET /api/inventory/logs（库存流水台账，Append-Only 只读）。

why：台账视图是新增的盘点/追溯入口，过滤口径（sku_id / kind / 日期区间 / limit）
       与租户隔离必须与其余接口同源；且它**只读** —— 任何写端点都是对
       「Append-Only 流水」纪律的破坏。kind 的中文映射还要与导出报表
       （api_export 的 kind_map）以及台账 UI 的选项逐字一致，否则同一笔流水
       在台账页与导出文件里叫法不同，追溯时对不上账。

覆盖：
1. 过滤：sku_id / kind / 日期闭区间 / limit（新在上）
2. kind 非法 → 400；匿名 → 401（staff 可读）
3. 租户隔离
4. 只读：不存在任何 /api/inventory/logs 的写端点（POST/PUT/PATCH/DELETE）
5. kind 中文映射与 api_export.kind_map 及台账 UI 选项一致
   （consume = 餐品消耗、waste = 后厨报损）

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import atexit
import importlib
import os
import re
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="inventory_ledger_api_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_ledger.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"lg_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _sku(_db, name, tenant="default"):
    sku_id, err = _db.create_sku(name, category="食材", base_unit="斤",
                                 tenant_id=tenant)
    assert sku_id, f"建 SKU 失败：{err}"
    return sku_id


def _log(_db, sku_id, kind, date, qty=1.0, amount=10.0, tenant="default"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤", amount=amount,
        vendor="測試供應商", date=date, receipt_id=None, kind=kind,
        tenant_id=tenant)


def _headers(role="staff", tenant=None):
    h = {"X-Role": role}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return h


def _get(client, **params):
    return client.get("/api/inventory/logs", params=params,
                      headers=_headers("staff"))


# -------------------------------------------------------------
# 1. 过滤：sku_id / kind / 日期区间 / limit
# -------------------------------------------------------------
def _seed(_db):
    sku_a = _sku(_db, "台賬菜心")
    sku_b = _sku(_db, "台賬腩肉")
    _log(_db, sku_a, "in", "2026-07-01", qty=2.0, amount=20.0)     # id1
    _log(_db, sku_b, "waste", "2026-07-03", qty=1.0, amount=0.0)    # id2
    _log(_db, sku_a, "consume", "2026-07-05", qty=1.0, amount=0.0)  # id3
    _log(_db, sku_a, "stocktake", "2026-07-10", qty=0.0, amount=0.0)  # id4
    _log(_db, sku_a, "in", "2026-07-20", qty=3.0, amount=60.0)      # id5
    return sku_a, sku_b


def test_filters_sku_kind_date_and_limit():
    _db = _fresh_db()
    client = _client()
    sku_a, sku_b = _seed(_db)

    all_rows = _get(client).json()
    assert all_rows["status"] == "success"
    assert all_rows["meta"]["count"] == 5, all_rows
    # 新在上：第一条是最后写入的（id5）
    assert all_rows["data"][0]["date"] == "2026-07-20", all_rows["data"]

    by_sku = _get(client, sku_id=sku_a).json()
    assert by_sku["meta"]["count"] == 4
    assert {r["sku_id"] for r in by_sku["data"]} == {sku_a}

    by_kind = _get(client, kind="consume").json()
    assert by_kind["meta"]["count"] == 1, by_kind
    assert by_kind["data"][0]["kind"] == "consume"

    by_date = _get(client, start_date="2026-07-04",
                   end_date="2026-07-06").json()
    assert by_date["meta"]["count"] == 1, by_date
    assert by_date["data"][0]["date"] == "2026-07-05"

    # 组合过滤
    combo = _get(client, sku_id=sku_a, kind="in",
                 start_date="2026-07-02").json()
    assert combo["meta"]["count"] == 1
    assert combo["data"][0]["date"] == "2026-07-20"

    # limit 生效（新在上，取最新一条）
    limited = _get(client, limit=1).json()
    assert limited["meta"]["count"] == 1
    assert limited["data"][0]["date"] == "2026-07-20"
    # limit 非法/越界回落默认（不报错、不返回 0 条）
    assert _get(client, limit=0).json()["meta"]["count"] == 5
    assert _get(client, limit=-3).json()["meta"]["count"] == 5

    # 未命中任何行
    assert _get(client, sku_id=sku_b, kind="consume").json()["meta"]["count"] == 0


def test_invalid_kind_400_and_auth():
    _db = _fresh_db()
    client = _client()
    _seed(_db)

    bad = _get(client, kind="not_a_kind")
    assert bad.status_code == 400, bad.text
    assert _get(client, kind="IN").status_code == 400
    assert _get(client, kind="delete").status_code == 400

    anon = client.get("/api/inventory/logs")
    assert anon.status_code == 401, anon.text


# -------------------------------------------------------------
# 2. 租户隔离
# -------------------------------------------------------------
def test_tenant_isolation():
    _db = _fresh_db()
    client = _client()
    sku_t2 = _sku(_db, "他租戶流水商品", tenant="t2")
    _log(_db, sku_t2, "in", "2026-07-01", tenant="t2")
    sku_d = _sku(_db, "默認租戶流水商品")
    _log(_db, sku_d, "in", "2026-07-02")

    def _ids(tenant=None):
        ret = client.get("/api/inventory/logs",
                         headers=_headers("staff", tenant=tenant)).json()
        return {r["sku_id"] for r in ret["data"]}

    assert _ids("t2") == {sku_t2}, "t2 只应看到自己的流水"
    assert _ids() == {sku_d}, "default 不得看到 t2 的流水"
    assert _ids("default") == {sku_d}


# -------------------------------------------------------------
# 3. 只读：无任何写端点
# -------------------------------------------------------------
def test_ledger_is_read_only_no_write_routes():
    from app.main import app as fastapi_app
    from app.routes_util import iter_routes

    write_methods = {"POST", "PUT", "PATCH", "DELETE"}
    ledger_routes = [r for r in iter_routes(fastapi_app)
                     if getattr(r, "path", "") == "/api/inventory/logs"]
    assert ledger_routes, "台账端点必须已注册"
    for r in ledger_routes:
        assert not (set(r.methods) & write_methods), \
            f"台账端点不得有写方法：{r.methods}"
    # 任何以台账路径开头的路由也不得有写方法（防止新增写子路径）
    for r in iter_routes(fastapi_app):
        path = getattr(r, "path", "")
        if path.startswith("/api/inventory/logs"):
            assert not (set(getattr(r, "methods", set())) & write_methods), \
                f"{path} 不应存在写方法：{r.methods}"


# -------------------------------------------------------------
# 4. kind 中文映射与 api_export.kind_map / 台账 UI 选项一致
# -------------------------------------------------------------
def test_kind_labels_match_export_report_and_ledger_ui():
    from app import api_export, api_inventory

    # 从 api_export 源码提取报表内的 kind_map 字面量
    export_src = open(api_export.__file__, encoding="utf-8").read()
    m = re.search(r"kind_map\s*=\s*\{(.*?)\}", export_src, re.S)
    assert m, "api_export 必须保留 kind_map"
    kind_map = dict(re.findall(r'"(\w+)"\s*:\s*"([^"]+)"', m.group(1)))
    assert kind_map["consume"] == "餐品消耗", kind_map
    assert kind_map["waste"] == "后厨报损", kind_map

    # 台账接口接受的 kind 集合 == 报表可翻译的 kind 集合
    assert set(kind_map) == set(api_inventory._VALID_LOG_KINDS), (
        kind_map, api_inventory._VALID_LOG_KINDS)

    # 台账 UI 的下拉选项逐字同映射
    html_path = os.path.join(_DEMO_DIR, "templates", "index.html")
    html = open(html_path, encoding="utf-8").read()
    select = re.search(
        r'id="invLedgerKindSelect".*?</select>', html, re.S)
    assert select, "台账类型下拉必须存在"
    ui_map = dict(re.findall(r'<option value="(\w+)">([^<]+)</option>',
                             select.group(0)))
    assert ui_map["consume"] == kind_map["consume"] == "餐品消耗", ui_map
    assert ui_map["waste"] == kind_map["waste"] == "后厨报损", ui_map
    assert set(ui_map) == set(kind_map), (ui_map, kind_map)

    # 台账渲染用的 JS 译名表也必须同源（不得另起一套中文名）
    js_path = os.path.join(_DEMO_DIR, "static", "js", "main.js")
    js = open(js_path, encoding="utf-8").read()
    fn = re.search(r"function invLedgerKindLabel\s*\(.*?\n\}", js, re.S)
    assert fn, "台账需有 invLedgerKindLabel 译名函数"
    js_map = dict(re.findall(r"'(\w+)'\s*:\s*'([^']+)'", fn.group(0)))
    assert js_map == kind_map, (js_map, kind_map)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
