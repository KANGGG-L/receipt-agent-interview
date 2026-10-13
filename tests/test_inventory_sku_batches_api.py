# -*- coding: utf-8 -*-
"""WS4-6 回归：GET /api/inventory/skus/{sku_id} 的 `batches`（在库批次列表）。

why：价格走势 Modal 的「SKU 概览」需要展示批次（入库日期 / 单价 / 剩余数量 / 单位）。
       批次必须与 `avg_cost` **同源同快照** —— 两者都取自「未关闭且 remaining_qty > 0」
       的批次池，否则概览里的加权成本会与列出的批次对不上账。同时批次是 FIFO 成本
       的唯一事实源，顺序必须是 `inbound_date ASC, id ASC`（不是 id 序）。

覆盖：
1. 无批次 → 返回空数组 `[]`（不是 None）
2. 只含未关闭且仍有余量的批次：被 FIFO 扣空的批次、暂估批次均不出现
3. FIFO 序：按 inbound_date 升序（跨批次日期与建批顺序相反时仍按日期）
4. `remaining_qty` / `unit_cost` 与 `avg_cost` 同快照自洽（手工按返回批次重算一致）
5. 元素字段齐备：batch_id / inbound_date / unit_cost / remaining_qty / unit / receipt_id

全部离线假数据：模块级独立临时 DB + 临时 RAG 目录，零外部调用、零 live 写入。
"""

import atexit
import importlib
import os
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="inventory_sku_batches_api_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_sku_batches.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag_chroma")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo"))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"bt_{uuid.uuid4().hex[:8]}.db")
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


def _in(_db, sku_id, unit_price, qty=1.0, date="2026-08-01", tenant="default",
        receipt_id=None):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤",
        amount=round(unit_price * qty, 4), vendor="測試供應商", date=date,
        receipt_id=receipt_id, kind="in", tenant_id=tenant)


def _consume(_db, sku_id, qty, date="2026-08-02", tenant="default"):
    _db.apply_stock_log(
        sku_id=sku_id, name="測試食材", qty=qty, unit="斤", amount=0.0,
        vendor="", date=date, receipt_id=None, kind="consume",
        tenant_id=tenant)


def _get(client, sku_id, role="owner", tenant=None):
    h = {"X-Role": role}
    if tenant:
        h["X-Tenant-Id"] = tenant
    return client.get(f"/api/inventory/skus/{sku_id}", headers=h)


def _batches(client, sku_id, tenant=None):
    ret = _get(client, sku_id, tenant=tenant)
    assert ret.status_code == 200, ret.text
    data = ret.json()["data"]
    assert "batches" in data, f"详情缺少 batches 字段：{sorted(data.keys())}"
    return data["batches"]


def _assert_fifo(batches):
    """断言严格 FIFO 序：先 inbound_date 升序，再 batch_id 升序。"""
    keys = [(b["inbound_date"], b["batch_id"]) for b in batches]
    assert keys == sorted(keys), f"批次未按 FIFO 序返回：{keys}"


# -------------------------------------------------------------
# 1. 无批次 → 空数组（不是 None）
# -------------------------------------------------------------
def test_no_batches_returns_empty_list_not_none():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "無批次商品")

    batches = _batches(client, sku)
    assert batches == [], f"无批次应返回空数组，实际：{batches!r}"


# -------------------------------------------------------------
# 2. 只含未关闭且 remaining > 0；FIFO 序（按日期，不看建批顺序）
# -------------------------------------------------------------
def test_batches_active_only_and_fifo_ordered_by_date():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "批次排序菜心")

    # 先建「晚日期」批次（id 更小），再建「早日期」批次（id 更大）：
    # 若实现按 id 排序就会错序，按 inbound_date 排序才正确。
    _in(_db, sku, 30.0, qty=4.0, date="2026-09-01")   # batch A
    _in(_db, sku, 10.0, qty=4.0, date="2026-08-01")   # batch B

    batches = _batches(client, sku)
    assert len(batches) == 2, batches
    assert [b["inbound_date"] for b in batches] == ["2026-08-01", "2026-09-01"], batches
    assert [b["unit_cost"] for b in batches] == [10.0, 30.0], batches
    _assert_fifo(batches)

    # 把「早日期」批次完全扣空：剩 4+4=8，扣 4 → B 归零关闭
    _consume(_db, sku, 4.0, date="2026-09-05")
    after = _batches(client, sku)
    assert len(after) == 1, after
    assert after[0]["inbound_date"] == "2026-09-01", after
    assert after[0]["remaining_qty"] == 4.0, after
    # 被扣空的批次不得出现（它既已 is_closed，也 remaining == 0）
    assert all(b["batch_id"] != batches[0]["batch_id"] for b in after), after
    _assert_fifo(after)


def test_exhausted_and_estimated_batches_excluded():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "耗盡與暫估雞蛋")

    _in(_db, sku, 10.0, qty=2.0, date="2026-07-01")
    # 全部扣空
    _consume(_db, sku, 2.0, date="2026-07-05")
    assert _batches(client, sku) == [], "已耗尽批次不应出现在 batches 中"

    # 超卖 3 斤 → 生成 remaining_qty=0 / is_closed=1 的暂估批次
    _consume(_db, sku, 3.0, date="2026-07-06")
    est = _batches(client, sku)
    assert est == [], f"暂估/零剩余批次不应出现：{est!r}"


# -------------------------------------------------------------
# 3. remaining_qty 与 avg_cost 同快照自洽
# -------------------------------------------------------------
def test_batches_and_avg_cost_are_self_consistent_same_snapshot():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "同快照腩肉")

    _in(_db, sku, 10.0, qty=10.0, date="2026-07-01")
    _in(_db, sku, 20.0, qty=10.0, date="2026-07-10")
    _consume(_db, sku, 5.0, date="2026-07-20")   # 全从批次 1 扣：剩 5@10 + 10@20

    ret = _get(client, sku)
    assert ret.status_code == 200, ret.text
    data = ret.json()["data"]
    batches = data["batches"]

    assert [(b["remaining_qty"], b["unit_cost"]) for b in batches] == \
        [(5.0, 10.0), (10.0, 20.0)], batches
    assert [b["batch_id"] for b in batches] == sorted(b["batch_id"] for b in batches)
    _assert_fifo(batches)

    # batches 与 avg_cost 同源：按返回批次重算的加权成本必须等于接口的 avg_cost
    total_qty = sum(float(b["remaining_qty"]) for b in batches)
    total_cost = sum(float(b["remaining_qty"]) * float(b["unit_cost"]) for b in batches)
    manual = round(total_cost / total_qty, 4)
    assert manual == 16.6667, manual
    assert data["avg_cost"] == manual, (data["avg_cost"], manual)
    # 未关闭批次剩余之和 == SKU 当前结存（同快照）
    assert round(total_qty, 4) == data["current_stock"] == 15.0, data


# -------------------------------------------------------------
# 4. 元素字段齐备 + receipt_id 透传
# -------------------------------------------------------------
def test_batch_element_fields_and_receipt_id_passthrough():
    _db = _fresh_db()
    client = _client()
    sku = _sku(_db, "批次欄位白菜")

    _in(_db, sku, 12.0, qty=3.0, date="2026-06-15", receipt_id=4242)

    batches = _batches(client, sku)
    assert len(batches) == 1, batches
    b = batches[0]
    for field in ("batch_id", "inbound_date", "unit_cost", "remaining_qty",
                  "unit", "receipt_id"):
        assert field in b, f"批次缺少字段 {field}: {b}"
    assert b["inbound_date"] == "2026-06-15"
    assert b["unit_cost"] == 12.0
    assert b["remaining_qty"] == 3.0
    assert b["unit"] == "斤"
    assert b["receipt_id"] == 4242


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
