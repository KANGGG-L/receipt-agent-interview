# -*- coding: utf-8 -*-
"""B2：成本报表口径（S1-P0-04、S1-P0-05、S1-P1-16）：报表合计 == 已入账单据总额合计。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db
from _b2_helpers import hdr, item, make_receipt, make_supplier, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _report(client, tn, **q):
    qs = "&".join("%s=%s" % kv for kv in q.items())
    return client.get("/api/cost_report?" + qs, headers=hdr(tn)).json()["data"]


def _spend(client, tn, pivot="department", **params):
    r = client.post("/api/export/preview", headers=hdr(tn),
                    json={"report_id": "spend_summary", "params": dict(spend_pivot=pivot, **params)})
    assert r.status_code == 200, r.text
    return r.json()["data"]


def _spend_total(data):
    row = [r for r in data["preview_rows"] if r[1] == "合计"][0]
    return row[2]


def _dept(client, tn, name):
    r = client.post("/api/departments", headers=hdr(tn), json={"name": name})
    assert r.status_code == 200, r.text
    return r.json().get("id")


def test_total_includes_fees_discount_and_excludes_void_lines(client):
    tn = new_tenant()
    # 明细 100 + 作废行 50 + 服务费 10 - 折扣 5 = 应付 105
    rid, ap = make_receipt(client, tn, [item("菜心", 10, "斤", 10), item("作废行", 1, "斤", 50, is_void=1)],
                           105.0, service_fee=10.0, discount_amount=5.0)
    assert ap.status_code == 200, ap.text
    rid2, ap2 = make_receipt(client, tn, [item("白菜", 4, "斤", 5)], 20.0, date="2026-10-06")
    assert ap2.status_code == 200, ap2.text
    # 未入账单不计
    make_receipt(client, tn, [item("排骨", 1, "斤", 999)], 999.0, approve=False)
    rep = _report(client, tn, month="2026-10")
    assert rep["month_total"] == 125.0
    assert abs(sum(d["total"] for d in rep["departments"]) + rep["unallocated"]["total"] - 125.0) < 0.005


def test_report_total_equals_sum_of_approved_receipt_totals(client):
    tn = new_tenant()
    d1, d2 = _dept(client, tn, "厨房"), _dept(client, tn, "吧台")
    specs = [
        ([item("A", 3, "斤", 10, cost_center_id=d1), item("B", 7, "斤", 10, cost_center_id=d2)], 110.0, dict(service_fee=10.0)),
        ([item("C", 1, "斤", 33.33), item("D", 1, "斤", 66.67)], 95.0, dict(discount_amount=5.0)),
        ([item("E", 3, "斤", 3.33), item("F", 3, "斤", 3.33), item("G", 3, "斤", 3.34)], 30.0, dict()),
    ]
    totals = 0.0
    for i, (its, total, extra) in enumerate(specs):
        rid, ap = make_receipt(client, tn, its, total, date="2026-10-%02d" % (i + 1),
                               department_id=d1 if i == 1 else None, **extra)
        assert ap.status_code == 200, ap.text
        totals += total
    rep = _report(client, tn, month="2026-10")
    assert rep["month_total"] == round(totals, 2)
    dept_sum = sum(d["total"] for d in rep["departments"]) + rep["unallocated"]["total"]
    assert abs(dept_sum - totals) < 0.005
    # 下钻明细相加 == 各部门
    for d in rep["departments"]:
        items_ = client.get("/api/cost_report/items?month=2026-10&department_id=%d" % d["id"],
                            headers=hdr(tn)).json()["data"]
        assert abs(sum(x["amount"] for x in items_) - d["total"]) < 0.005
    un = client.get("/api/cost_report/items?month=2026-10&department_id=0", headers=hdr(tn)).json()["data"]
    assert abs(sum(x["amount"] for x in un) - rep["unallocated"]["total"]) < 0.005
    allrows = client.get("/api/cost_report/items?month=2026-10", headers=hdr(tn)).json()["data"]
    assert abs(sum(x["amount"] for x in allrows) - totals) < 0.005
    # 导出口径一致（按部门、按供应商两种透视）
    assert _spend_total(_spend(client, tn, "department", month="2026-10")) == round(totals, 2)
    assert _spend_total(_spend(client, tn, "supplier", month="2026-10")) == round(totals, 2)


def test_dept_allocation_is_proportional_to_line_amounts(client):
    tn = new_tenant()
    d1, d2 = _dept(client, tn, "甲部"), _dept(client, tn, "乙部")
    rid, ap = make_receipt(client, tn, [item("A", 1, "斤", 30, cost_center_id=d1),
                                        item("B", 1, "斤", 70, cost_center_id=d2)],
                           110.0, service_fee=10.0)
    assert ap.status_code == 200, ap.text
    rep = _report(client, tn, month="2026-10")
    by = {d["id"]: d["total"] for d in rep["departments"]}
    assert by[d1] == 33.0 and by[d2] == 77.0


def test_departments_are_tenant_scoped(client):
    tn, other = new_tenant(), new_tenant()
    _dept(client, other, "别家机密部")
    rep = _report(client, tn, month="2026-10")
    assert "别家机密部" not in [d["name"] for d in rep["departments"]]


def test_foreign_currency_converted_with_snapshot_and_listed_when_rate_missing(client):
    tn = new_tenant()
    # 设置里的 CNY 汇率改成 1.10
    r = client.put("/api/settings/fx_rates", headers=hdr(tn),
                   json={"rates": [{"currency": "CNY", "rate": 1.10, "confirmed": True}]})
    assert r.status_code == 200, r.text
    rid, ap = make_receipt(client, tn, [item("人民币货", 10, "斤", 10)], 100.0, currency="CNY")
    assert ap.status_code == 200, ap.text
    # 入账后汇率改了，已入账单据仍按入账时的汇率
    client.put("/api/settings/fx_rates", headers=hdr(tn),
               json={"rates": [{"currency": "CNY", "rate": 2.0, "confirmed": True}]})
    rep = _report(client, tn, month="2026-10")
    assert rep["month_total"] == 110.0 and rep["currency"] == "HKD"
    rows = client.get("/api/cost_report/items?month=2026-10", headers=hdr(tn)).json()["data"]
    assert rows[0]["original_currency"] == "CNY" and rows[0]["original_amount"] == 100.0
    assert rows[0]["amount"] == 110.0
    assert _spend_total(_spend(client, tn, "department", month="2026-10")) == 110.0


def test_unconverted_currency_is_listed_not_summed(client):
    tn = new_tenant()
    rid, ap = make_receipt(client, tn, [item("泰铢货", 1, "斤", 100)], 100.0, currency="CNY")
    assert ap.status_code == 200
    from sqlalchemy import text
    from app import units_store
    # 模拟历史上已入账但没有汇率快照、设置里也没有汇率的外币单
    with units_store._engine().begin() as conn:
        conn.execute(text("DELETE FROM receipt_fx_snapshots WHERE receipt_id=:r"), {"r": rid})
    db.update_receipt(rid, currency="XYZ")
    rep = _report(client, tn, month="2026-10")
    assert rep["month_total"] == 0.0
    assert rep["unconverted"] and rep["unconverted"][0]["currency"] == "XYZ"
    assert "汇率" in rep["unconverted_warning"]
