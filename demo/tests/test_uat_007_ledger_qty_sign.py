# -*- coding: utf-8 -*-
"""UAT-007：消耗台账数量方向显示为正数 — 开发专项。

根因：consume/waste 在 DB 存正数 magnitude；旧 UI 用 (qty>0?'+':'')+qty 把消耗显示成 +1。
修复：API 派生 qty_signed；UI 用 qty_signed 展示。禁止改写扣库存逻辑（避免二次扣减）。

验收映射：
1) 合成 10kg 入库后消耗 1kg → 台账 qty_signed=-1，stock=9
2) 再入库 1 → qty_signed=+1，stock=10
3) 冲销（receipt reversal / void adjust）方向相反
4) 同一事件 id 在 DB / API 对齐（UI 渲染同 id）
负向：跨 tenant 不可见；状态机/扣库存逻辑不变。
"""
from __future__ import annotations

import atexit
import importlib
import os
import shutil
import sys
import tempfile
import uuid

_TMP = tempfile.mkdtemp(prefix="uat007_ledger_sign_")
os.environ["DB_PATH"] = os.path.join(_TMP, "init.db")
os.environ["RAG_DIR"] = os.path.join(_TMP, "rag")
os.environ["AUTH_ENABLED"] = "0"
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)

_DEMO_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _DEMO_DIR not in sys.path:
    sys.path.insert(0, _DEMO_DIR)

import app.db as db  # noqa: E402


def _fresh_db():
    os.environ["DB_PATH"] = os.path.join(_TMP, f"u7_{uuid.uuid4().hex[:8]}.db")
    importlib.reload(db)
    return db


def _client():
    from fastapi.testclient import TestClient
    from app.main import app as fastapi_app
    return TestClient(fastapi_app)


def _hdr(role="owner", tenant="default"):
    return {"X-Role": role, "X-Tenant-Id": tenant}


def test_ledger_qty_signed_helper_unit():
    from app.api_inventory import ledger_qty_signed
    assert ledger_qty_signed("consume", 1.0) == -1.0
    assert ledger_qty_signed("waste", 2.5) == -2.5
    assert ledger_qty_signed("in", 1.0) == 1.0
    assert ledger_qty_signed("stocktake", -3.0) == -3.0
    assert ledger_qty_signed("stocktake", 4.0) == 4.0
    assert ledger_qty_signed("reversal", -1.0) == -1.0
    assert ledger_qty_signed("adjust", 1.0) == 1.0
    # outbound must not flip an already-negative stored value into positive
    assert ledger_qty_signed("consume", -1.0) == -1.0


def test_consume_inbound_signed_stock_and_id_alignment():
    _db = _fresh_db()
    client = _client()
    tn = "t_uat007_a"
    sku_id, err = _db.create_sku("Synthetic_Rice", category="食材", base_unit="kg",
                                 tenant_id=tn)
    assert sku_id, err
    # synthesize 10kg inbound
    _db.apply_stock_log(
        sku_id=sku_id, name="Synthetic_Rice", qty=10.0, unit="kg", amount=100.0,
        vendor="Vendor_A", date="2026-10-17", receipt_id=None, kind="in",
        tenant_id=tn)
    assert _db.get_sku(sku_id, tenant_id=tn).current_stock == 10.0

    r = client.post(f"/api/inventory/{sku_id}/consume", headers=_hdr(tenant=tn),
                    json={"quantity": 1.0, "notes": "UAT007 consume"})
    assert r.status_code == 200, r.text
    assert r.json()["current_stock"] == 9.0

    logs = client.get(f"/api/inventory/logs?sku_id={sku_id}",
                      headers=_hdr("staff", tn)).json()["data"]
    consume_rows = [x for x in logs if x["kind"] == "consume"]
    assert len(consume_rows) == 1
    c = consume_rows[0]
    assert c["qty"] == 1.0  # DB magnitude unchanged
    assert c["qty_signed"] == -1.0
    # DB / API same event id
    session = _db.get_session()
    try:
        row = session.get(_db._StockLogRow, int(c["id"]))
        assert row is not None
        assert row.id == c["id"]
        assert row.sku_id == sku_id
        assert float(row.qty) == 1.0
        assert row.kind == "consume"
        assert (row.tenant_id or "default") == tn
    finally:
        session.close()

    # inbound +1 -> stock 10
    _db.apply_stock_log(
        sku_id=sku_id, name="Synthetic_Rice", qty=1.0, unit="kg", amount=10.0,
        vendor="Vendor_B", date="2026-10-17", receipt_id=None, kind="in",
        tenant_id=tn)
    assert _db.get_sku(sku_id, tenant_id=tn).current_stock == 10.0
    logs2 = client.get(f"/api/inventory/logs?sku_id={sku_id}",
                       headers=_hdr("staff", tn)).json()["data"]
    in_rows = [x for x in logs2 if x["kind"] == "in" and float(x["qty"]) == 1.0]
    assert in_rows
    assert in_rows[0]["qty_signed"] == 1.0


def test_reversal_and_adjust_opposite_direction():
    _db = _fresh_db()
    client = _client()
    tn = "t_uat007_rev"
    sku_id, err = _db.create_sku("Synthetic_Oil", category="食材", base_unit="kg",
                                 tenant_id=tn)
    assert sku_id, err
    _db.apply_stock_log(
        sku_id=sku_id, name="Synthetic_Oil", qty=5.0, unit="kg", amount=50.0,
        vendor="Vendor_A", date="2026-10-17", receipt_id=101, kind="in",
        tenant_id=tn)
    # receipt-style reversal: negative qty
    session = _db.get_session()
    try:
        session.add(_db._StockLogRow(
            sku_id=sku_id, name="Synthetic_Oil", qty=-5.0, unit="kg", amount=-50.0,
            vendor="Vendor_A", date="2026-10-17", receipt_id=101, kind="reversal",
            note="冲销单据 #101", created_at=_db.now_iso(), tenant_id=tn))
        # dish void-style adjust: positive restore
        session.add(_db._StockLogRow(
            sku_id=sku_id, name="Synthetic_Oil", qty=1.0, unit="kg", amount=8.0,
            vendor="", date="2026-10-17", receipt_id=None, kind="adjust",
            note="冲销作废餐品消耗 #9 恢复库存", created_at=_db.now_iso(), tenant_id=tn))
        session.commit()
    finally:
        session.close()

    logs = client.get(f"/api/inventory/logs?sku_id={sku_id}",
                      headers=_hdr("staff", tn)).json()["data"]
    by_kind = {x["kind"]: x for x in logs}
    assert by_kind["in"]["qty_signed"] == 5.0
    assert by_kind["reversal"]["qty_signed"] == -5.0
    assert by_kind["adjust"]["qty_signed"] == 1.0


def test_cross_tenant_logs_invisible_and_stock_machine_unchanged():
    _db = _fresh_db()
    client = _client()
    a, b = "t_uat007_ta", "t_uat007_tb"
    sid_a, _ = _db.create_sku("TenantA_SKU", category="食材", base_unit="kg", tenant_id=a)
    sid_b, _ = _db.create_sku("TenantB_SKU", category="食材", base_unit="kg", tenant_id=b)
    _db.apply_stock_log(
        sku_id=sid_a, name="TenantA_SKU", qty=10.0, unit="kg", amount=1.0,
        vendor="Vendor_A", date="2026-10-17", receipt_id=None, kind="in", tenant_id=a)
    _db.apply_stock_log(
        sku_id=sid_b, name="TenantB_SKU", qty=7.0, unit="kg", amount=1.0,
        vendor="Vendor_B", date="2026-10-17", receipt_id=None, kind="in", tenant_id=b)
    before_b = _db.get_sku(sid_b, tenant_id=b).current_stock
    r = client.post(f"/api/inventory/{sid_a}/consume", headers=_hdr(tenant=a),
                    json={"quantity": 1.0})
    assert r.status_code == 200
    assert _db.get_sku(sid_b, tenant_id=b).current_stock == before_b
    logs_b = client.get("/api/inventory/logs", headers=_hdr("staff", b)).json()["data"]
    assert all(x.get("sku_id") != sid_a for x in logs_b)
    # A cannot consume B's sku
    deny = client.post(f"/api/inventory/{sid_b}/consume", headers=_hdr(tenant=a),
                       json={"quantity": 1.0})
    assert deny.status_code == 404
    assert _db.get_sku(sid_b, tenant_id=b).current_stock == before_b


def test_ui_uses_qty_signed_not_blind_plus():
    js_path = os.path.join(_DEMO_DIR, "static", "js", "inventory_board.js")
    js = open(js_path, encoding="utf-8").read()
    assert "invLedgerQtyText" in js
    assert "invLedgerQtySigned" in js
    assert "qty_signed" in js
    # the buggy pattern of blind positive prefix on raw qty must not remain in ledger loop
    assert "const qtyText = (qty > 0 ? '+' : '') + qty;" not in js
    assert "invLedgerQtyText(r)" in js


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
