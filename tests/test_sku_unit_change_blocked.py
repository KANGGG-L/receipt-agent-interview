# -*- coding: utf-8 -*-
"""既有问题 #2 专项测试：update_sku 的 UNIT_CHANGE_BLOCKED 死分支复活（TDD）。

考古结论（git log + docs/04-AI技术选型与评测/05-UI交互与视觉审查/
02-前端UX地毯式走查报告.md 库存用例 + 前端 toast 文案）：
- patch_sku 自首个提交起就映射 UNIT_CHANGE_BLOCKED，但 update_sku 从未返回它（死分支）；
- 走查报告用例「PATCH 单位 stock0 → 200；PATCH 单位 stock>0 → 封锁」、
  前端提示「账面库存不为 0，请先盘点调整为 0 再改单位」——
  判据是账面库存 current_stock 非 0，而非仅存在历史流水。

覆盖：
1. 账面库存非 0 的 SKU 改 base_unit → update_sku 返回 "UNIT_CHANGE_BLOCKED"，
   且 SKU 数据不被修改
2. 账面库存为 0 的 SKU 改 base_unit → 允许（对齐「先盘点归零再改」引导）
3. 非 unit 字段（name/category/min_stock_alert）在库存非 0 时照常可改
4. unit 未实际变化（同值）不拦截
5. API 层 patch_sku 对 UNIT_CHANGE_BLOCKED 的既有映射生效
数据一律假数据。
"""

import os
import sys
import tempfile
import uuid

# 隔离环境：独立 DB（必须在首次 import app.* 之前设置）
_TMP = tempfile.mkdtemp(prefix="sku_unit_change_test_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init_unit_change.db")

_DEMO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "demo")
sys.path.insert(0, _DEMO_DIR)

import importlib  # noqa: E402

import app.db as db  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"unit_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _mk_sku(_db, name, unit="斤", stock=0.0, tenant_id="default"):
    sku_id, err = _db.create_sku(name, base_unit=unit, tenant_id=tenant_id)
    assert err is None, f"建 SKU 失败: {err}"
    if stock:
        _db.apply_stock_log(sku_id=sku_id, name=name, qty=stock, unit=unit,
                            amount=stock * 10, vendor="虛擬供應商",
                            date="2026-01-01", receipt_id=None, kind="in",
                            tenant_id=tenant_id)
    return sku_id


# -------------------------------------------------------------
# db 层
# -------------------------------------------------------------
def test_unit_change_blocked_when_stock_nonzero():
    _db = _fresh_db()
    sku_id = _mk_sku(_db, "虛擬食材甲", unit="斤", stock=8.0)
    row, err = _db.update_sku(sku_id, base_unit="公斤")
    assert row is None and err == "UNIT_CHANGE_BLOCKED", \
        f"库存非 0 改单位应被封锁，实际 ({row}, {err})"
    sku = _db.get_sku(sku_id)
    assert sku.base_unit == "斤", "被封锁的单位变更不得落库"


def test_unit_change_allowed_when_stock_zero():
    _db = _fresh_db()
    sku_id = _mk_sku(_db, "虛擬食材乙", unit="斤", stock=0.0)
    row, err = _db.update_sku(sku_id, base_unit="公斤")
    assert err is None and row is not None, \
        f"库存为 0 改单位应放行（先盘点归零再改路径），实际 ({row}, {err})"
    assert _db.get_sku(sku_id).base_unit == "公斤"


def test_non_unit_fields_not_blocked_by_stock():
    _db = _fresh_db()
    sku_id = _mk_sku(_db, "虛擬食材丙", unit="斤", stock=3.0)
    row, err = _db.update_sku(sku_id, name="虛擬食材丙改",
                              category="冷凍", min_stock_alert=2.0)
    assert err is None and row is not None, f"非单位字段不应被封锁: {err}"
    sku = _db.get_sku(sku_id)
    assert sku.name == "虛擬食材丙改" and sku.category == "冷凍"
    assert sku.base_unit == "斤"


def test_same_unit_value_not_blocked():
    _db = _fresh_db()
    sku_id = _mk_sku(_db, "虛擬食材丁", unit="斤", stock=5.0)
    row, err = _db.update_sku(sku_id, base_unit="斤")
    assert err is None and row is not None, "同值单位不应拦截"


# -------------------------------------------------------------
# API 层（patch_sku 既有映射）
# -------------------------------------------------------------
def test_api_patch_sku_returns_unit_change_blocked():
    _db = _fresh_db()
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    client = TestClient(fastapi_app)

    sku_id = _mk_sku(_db, "虛擬食材戊", unit="個", stock=6.0)
    resp = client.patch(f"/api/inventory/skus/{sku_id}",
                        json={"base_unit": "箱"},
                        headers={"X-Role": "owner"})
    body = resp.json()
    assert body.get("status") == "error", body
    assert body.get("code") == "UNIT_CHANGE_BLOCKED", \
        f"patch_sku 应返回既有 UNIT_CHANGE_BLOCKED 映射，实际 {body}"
    assert _db.get_sku(sku_id).base_unit == "個"

    # 归零后再改：API 放行
    _db.stocktake_sku(sku_id, 0.0, note="測試盤點歸零")
    resp2 = client.patch(f"/api/inventory/skus/{sku_id}",
                         json={"base_unit": "箱"},
                         headers={"X-Role": "owner"})
    assert resp2.json().get("status") == "success", resp2.text
    assert _db.get_sku(sku_id).base_unit == "箱"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
