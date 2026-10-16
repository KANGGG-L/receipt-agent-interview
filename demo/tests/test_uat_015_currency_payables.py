# -*- coding: utf-8 -*-
"""UAT-015: 外币应付金额被错误标为港币原值 定向测试。
验证 AC：
1. 明确合成 SGD100、snapshot 5 -> HKD 500，归档原币 S$100，应付/支付 HK$500
2. 港币部分付 100 -> 未付 400，全付 -> 0
3. 修改实时 FX 后历史仍 snapshot 值
4. 缺 snapshot legacy 提示未知不硬标 HKD
5. 负向边界：超额付款拦截、跨租户隔离
"""

import os
import sys
from datetime import date
from sqlalchemy import text

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db, units_store
from _b2_helpers import hdr, item, make_receipt, make_supplier, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _pay(client, tn, sid, amount, rids, method="cash", paid_at=None, **extra):
    data = {"supplier_id": str(sid), "amount": str(amount), "method": method,
            "paid_at": paid_at or date.today().isoformat()}
    data.update(extra)
    if rids:
        data["receipt_ids"] = [str(r) for r in rids]
    return client.post("/api/payments", headers=hdr(tn), data=data)


def _sup(client, tn, sid):
    rows = client.get("/api/suppliers", headers=hdr(tn)).json()["data"]
    return [s for s in rows if s["id"] == sid][0]


def test_sgd_payable_with_snapshot_and_partial_full_payments(client):
    tn = new_tenant()
    sid = make_supplier(tn, "Vendor_A")
    # 1. 设置 SGD 汇率 = 5.0
    r = client.put("/api/settings/fx_rates", headers=hdr(tn),
                   json={"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]})
    assert r.status_code == 200, r.text

    # 合成单据：SGD 100.0，挂账入账
    rid, ap = make_receipt(client, tn, [item("进口食材", 10, "包", 10.0)], 100.0,
                           supplier="Vendor_A", supplier_id=sid, currency="SGD",
                           settlement="credit", approve=True)
    assert ap.status_code == 200, ap.text

    # 查归档单据列表：原币仍为 SGD 100.0
    rc = client.get("/api/receipt/%d" % rid, headers=hdr(tn)).json()["data"]
    assert rc["currency"] == "SGD"
    assert rc["total_amount"] == 100.0

    # 查应付接口：应付清单折成 HKD 500.0，未付 500.0
    p = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert p["currency"] == "HKD"
    assert p["total_outstanding"] == 500.0
    assert p["outstanding_count"] == 1
    row = [r for r in p["rows"] if r["receipt_id"] == rid][0]
    assert row["currency"] == "SGD"
    assert row["total_amount"] == 100.0
    assert row["total_hkd"] == 500.0
    assert row["outstanding_amount"] == 500.0
    assert row["paid_amount"] == 0.0
    assert row["payment_status"] == "unpaid"
    assert row["fx_missing"] is False
    assert row["has_fx_snapshot"] is True

    # 供应商统计口径一致
    s = _sup(client, tn, sid)
    assert s["unpaid_credit_total"] == 500.0

    # 2. 港币部分付 100 -> 未付 400
    pay_res1 = _pay(client, tn, sid, 100.0, [rid], method="bank_transfer")
    assert pay_res1.status_code == 200, pay_res1.text

    p2 = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert p2["total_outstanding"] == 400.0
    row2 = [r for r in p2["rows"] if r["receipt_id"] == rid][0]
    assert row2["total_hkd"] == 500.0
    assert row2["paid_amount"] == 100.0
    assert row2["outstanding_amount"] == 400.0
    assert row2["payment_status"] == "partial"

    s2 = _sup(client, tn, sid)
    assert s2["unpaid_credit_total"] == 400.0

    # 3. 修改实时 FX 为 6.0，历史单据仍使用入账时 snapshot 值 5.0 (总额仍 500，未付仍 400)
    r_fx = client.put("/api/settings/fx_rates", headers=hdr(tn),
                      json={"rates": [{"currency": "SGD", "rate": 6.0, "confirmed": True}]})
    assert r_fx.status_code == 200, r_fx.text

    p3 = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert p3["total_outstanding"] == 400.0
    row3 = [r for r in p3["rows"] if r["receipt_id"] == rid][0]
    assert row3["total_hkd"] == 500.0
    assert row3["outstanding_amount"] == 400.0

    # 4. 全额付清剩余 400 -> 未付 0
    pay_res2 = _pay(client, tn, sid, 400.0, [rid], method="fps")
    assert pay_res2.status_code == 200, pay_res2.text

    p4 = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert p4["total_outstanding"] == 0.0
    row4 = [r for r in p4["rows"] if r["receipt_id"] == rid][0]
    assert row4["outstanding_amount"] == 0.0
    assert row4["paid_amount"] == 500.0
    assert row4["payment_status"] == "paid"

    s4 = _sup(client, tn, sid)
    assert s4["unpaid_credit_total"] == 0.0


def test_legacy_foreign_receipt_missing_snapshot(client):
    tn = new_tenant()
    sid = make_supplier(tn, "Vendor_Legacy")
    # 建立一条单据
    rid, ap = make_receipt(client, tn, [item("历史物料", 1, "件", 120.0)], 120.0,
                           supplier="Vendor_Legacy", supplier_id=sid, currency="SGD",
                           settlement="credit", approve=True)
    assert ap.status_code == 200

    # 模拟历史遗留单据：从 receipt_fx_snapshots 中删除该单据的汇率快照
    with units_store._engine().begin() as conn:
        conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE receipt_id=:r AND tenant_id=:t"),
                     {"r": rid, "t": tn})

    # 查应付接口：由于缺少 snapshot，标记为 fx_missing=True，total_hkd 为 None，不硬标 HKD 原值
    p = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    row = [r for r in p["rows"] if r["receipt_id"] == rid][0]
    assert row["has_fx_snapshot"] is False
    assert row["fx_missing"] is True
    assert row["total_hkd"] is None

    # 尝试登记支付：因汇率缺失而被拒绝
    res = _pay(client, tn, sid, 120.0, [rid])
    assert res.status_code == 400
    assert res.json().get("code") == "FX_RATE_MISSING"


def test_overpayment_rejected_on_foreign_payable(client):
    tn = new_tenant()
    sid = make_supplier(tn, "Vendor_Overpay")
    client.put("/api/settings/fx_rates", headers=hdr(tn),
               json={"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]})
    rid, ap = make_receipt(client, tn, [item("货物", 1, "批", 100.0)], 100.0,
                           supplier="Vendor_Overpay", supplier_id=sid, currency="SGD",
                           settlement="credit", approve=True)
    assert ap.status_code == 200

    # 应付港币为 500.0，若超额支付 550.0 则被拒绝
    res = _pay(client, tn, sid, 550.0, [rid])
    assert res.status_code == 400
    assert res.json().get("code") == "PAYMENT_OVER_OUTSTANDING"


def test_tenant_isolation_on_payables_and_payments(client):
    tn_a = new_tenant()
    tn_b = new_tenant()
    sid_a = make_supplier(tn_a, "Vendor_A")
    sid_b = make_supplier(tn_b, "Vendor_B")

    client.put("/api/settings/fx_rates", headers=hdr(tn_a),
               json={"rates": [{"currency": "SGD", "rate": 5.0, "confirmed": True}]})
    rid_a, ap = make_receipt(client, tn_a, [item("货物A", 1, "批", 100.0)], 100.0,
                             supplier="Vendor_A", supplier_id=sid_a, currency="SGD",
                             settlement="credit", approve=True)
    assert ap.status_code == 200

    # Tenant B 无法查到 Tenant A 的应付
    pb = client.get("/api/payables", headers=hdr(tn_b)).json()["data"]
    assert len(pb["rows"]) == 0

    # Tenant B 试图支付 Tenant A 的单据被拦截
    res = _pay(client, tn_b, sid_b, 500.0, [rid_a])
    assert res.status_code == 404
