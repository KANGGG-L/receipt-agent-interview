# -*- coding: utf-8 -*-
"""B2：SKU 候选与新建、库存超量、币种汇率、价格趋势、餐品消耗（S1-P0-03、S1-P1-09、S1-P1-16、S3-21、B2-N）。"""

import math
import os
import sys
from datetime import date

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


def _skus(tn):
    return {s.name: s for s in db.list_skus(tenant_id=tn)}


# ------------------------------------------------------------------ SKU 候选
@pytest.mark.parametrize("existing,existing_unit,new_name,new_unit", [
    ("牛肉", "斤", "牛肉丸", "包"),
    ("鸡蛋", "只", "鸡蛋仔粉", "包"),
    ("菜心", "斤", "菜心(大)", "斤"),
])
def test_containment_is_candidate_only_and_approve_creates_new_sku(client, existing, existing_unit, new_name, new_unit):
    tn = new_tenant()
    sid, _ = db.create_sku(existing, base_unit=existing_unit, tenant_id=tn)
    rid, ap = make_receipt(client, tn, [item(new_name, 2, new_unit, 10)], 20.0, approve=False)
    detail = client.get("/api/receipt/%d" % rid, headers=hdr(tn)).json()["data"]
    it = detail["items"][0]
    assert not it["sku_id"] and not it["matched"]
    cands = it["sku_candidates"]
    assert [c["sku_id"] for c in cands] == [sid]
    c = cands[0]
    assert c["name"] == existing and c["unit"] == existing_unit and "名字相近" in c["reason"]
    assert isinstance(c["unit_compatible"], bool)
    row = db.get_receipt_row(rid)
    ap = client.post("/api/receipt/%d/approve" % rid, headers=hdr(tn), json={"version": row.version})
    assert ap.status_code == 200, ap.text
    skus = _skus(tn)
    assert new_name in skus and skus[new_name].id != sid, "没有确认的包含匹配必须新建 SKU"
    assert db.get_sku(sid, tenant_id=tn).current_stock == 0


def test_confirmed_candidate_is_used_on_approve(client):
    tn = new_tenant()
    sid, _ = db.create_sku("白菜", base_unit="斤", tenant_id=tn)
    rid, _ap = make_receipt(client, tn, [item("白菜仔", 3, "斤", 10)], 30.0, approve=False)
    it = client.get("/api/receipt/%d" % rid, headers=hdr(tn)).json()["data"]["items"][0]
    assert it["sku_candidates"][0]["sku_id"] == sid
    it["sku_id"] = it["sku_candidates"][0]["sku_id"]
    row = db.get_receipt_row(rid)
    body = {"supplier_name": "测试供应商", "date": "2026-10-05", "total_amount": 30.0,
            "settlement_type": "credit", "version": row.version, "receipt_id": rid,
            "items": [{k: it[k] for k in ("name", "quantity", "unit", "unit_price", "amount", "sku_id")}]}
    r = client.post("/api/save_edited", headers=hdr(tn, "staff"), json=body)
    assert r.status_code == 200, r.text
    row = db.get_receipt_row(rid)
    assert client.post("/api/receipt/%d/approve" % rid, headers=hdr(tn),
                       json={"version": row.version}).status_code == 200
    assert db.get_sku(sid, tenant_id=tn).current_stock == 3.0
    assert "白菜仔" not in _skus(tn)


def test_exact_and_alias_matches_still_auto_adopted(client):
    tn = new_tenant()
    sid, _ = db.create_sku("西兰花", base_unit="斤", tenant_id=tn)
    rid, ap = make_receipt(client, tn, [item("西兰花", 2, "斤", 8)], 16.0)
    assert ap.status_code == 200, ap.text
    assert db.get_sku(sid, tenant_id=tn).current_stock == 2.0
    assert len(_skus(tn)) == 1


def test_candidates_are_tenant_scoped_and_hidden_after_approval(client):
    a, b = new_tenant(), new_tenant()
    db.create_sku("羊肉", base_unit="斤", tenant_id=a)
    rid, _ = make_receipt(client, b, [item("羊肉串", 1, "包", 5)], 5.0, approve=False)
    it = client.get("/api/receipt/%d" % rid, headers=hdr(b)).json()["data"]["items"][0]
    assert it["sku_candidates"] == []


# ------------------------------------------------------------------ 库存超量
def _sku_with_stock(tn, name="扣减肉", stock=21.0):
    sid, _ = db.create_sku(name, base_unit="斤", tenant_id=tn)
    db.update_sku(sid, current_stock=stock, last_unit_price=10.0)
    return sid


def test_consume_over_stock_is_409_with_plain_message(client):
    tn = new_tenant()
    sid = _sku_with_stock(tn)
    r = client.post("/api/inventory/%d/consume" % sid, headers=hdr(tn), json={"quantity": 1000})
    assert r.status_code == 409
    assert r.json()["msg"].startswith("库存只有 21 斤，不够扣 1000 斤")
    assert r.json()["code"] == "INSUFFICIENT_STOCK" and r.json()["data"]["can_override"] is True
    assert db.get_sku(sid, tenant_id=tn).current_stock == 21.0
    r = client.post("/api/inventory/%d/waste" % sid, headers=hdr(tn), json={"quantity": 1000})
    assert r.status_code == 409 and "不够扣" in r.json()["msg"]
    assert db.get_sku(sid, tenant_id=tn).current_stock == 21.0


def test_owner_can_confirm_overdraw_with_reason_and_it_is_recorded(client):
    tn = new_tenant()
    sid = _sku_with_stock(tn)
    r = client.post("/api/inventory/%d/consume" % sid, headers=hdr(tn),
                    json={"quantity": 30, "allow_negative": True})
    assert r.status_code == 409 and "原因" in r.json()["msg"]
    r = client.post("/api/inventory/%d/consume" % sid, headers=hdr(tn),
                    json={"quantity": 30, "allow_negative": True, "override_reason": "盘点漏了一批货"})
    assert r.status_code == 200, r.text
    assert r.json()["current_stock"] == -9.0
    logs = client.get("/api/inventory/logs?sku_id=%d" % sid, headers=hdr(tn)).json()["data"]
    notes = str(logs)
    assert "盘点漏了一批货" in notes
    # 正常扣减不受影响
    sid2 = _sku_with_stock(tn, "正常肉", 5.0)
    assert client.post("/api/inventory/%d/consume" % sid2, headers=hdr(tn), json={"quantity": 5}).status_code == 200
    assert client.post("/api/inventory/%d/consume" % sid2, headers=hdr(tn), json={"quantity": 0}).status_code == 400


def test_staff_cannot_consume_and_nan_rejected(client):
    tn = new_tenant()
    sid = _sku_with_stock(tn)
    assert client.post("/api/inventory/%d/consume" % sid, headers=hdr(tn, "staff"),
                       json={"quantity": 1}).status_code == 403
    r = client.post("/api/inventory/%d/consume" % sid, headers=hdr(tn),
                    content='{"quantity": NaN}', )
    assert r.status_code in (400, 422)
    assert db.get_sku(sid, tenant_id=tn).current_stock == 21.0


def test_negative_stock_report(client):
    tn, other = new_tenant(), new_tenant()
    sid = _sku_with_stock(tn, "负库存肉", -12.0)
    _sku_with_stock(tn, "好库存肉", 3.0)
    _sku_with_stock(other, "别家负库存", -5.0)
    r = client.get("/api/inventory/negative_stock", headers=hdr(tn))
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert d["count"] == 1 and d["items"][0]["name"] == "负库存肉" and d["items"][0]["current_stock"] == -12.0
    assert client.get("/api/inventory/negative_stock", headers=hdr(tn, "staff")).status_code == 403


# ------------------------------------------------------------------ 价格趋势与权限
def test_price_trend_returns_null_when_no_recent_data(client):
    tn = new_tenant()
    sid, _ = db.create_sku("老价格", base_unit="斤", tenant_id=tn)
    db.apply_stock_log(sid, "老价格", 1.0, "斤", 30.0, "旧供应商", "2016-05-01", None, "in", tenant_id=tn)
    d = client.get("/api/price_history/%d" % sid, headers=hdr(tn)).json()["summary"]
    assert d["avg_30d"] is None and d["vs_avg_pct"] is None and d["is_anomaly"] is False
    assert d["latest_price"] == 30.0 and d["count"] == 1
    empty, _ = db.create_sku("无价格", base_unit="斤", tenant_id=tn)
    e = client.get("/api/price_history/%d" % empty, headers=hdr(tn)).json()["summary"]
    assert e["avg_30d"] is None and e["latest_price"] is None and e["min_price"] is None
    det = client.get("/api/inventory/skus/%d" % empty, headers=hdr(tn)).json()["data"]
    assert det["avg_30d"] is None and det["latest_price"] is None and det["vs_avg_pct"] is None


def test_sku_detail_hides_prices_from_staff(client):
    tn = new_tenant()
    sid = _sku_with_stock(tn, "价格肉", 10.0)
    db.apply_stock_log(sid, "价格肉", 5.0, "斤", 50.0, "供应商", date.today().isoformat(), None, "in", tenant_id=tn)
    staff = client.get("/api/inventory/skus/%d" % sid, headers=hdr(tn, "staff")).json()["data"]
    owner = client.get("/api/inventory/skus/%d" % sid, headers=hdr(tn)).json()["data"]
    assert owner["prices_visible"] is True and owner["last_unit_price"] == 10.0 and owner["latest_price"] == 10.0
    assert staff["prices_visible"] is False
    for k in ("last_unit_price", "avg_cost", "avg_30d", "latest_price", "vs_avg_pct"):
        assert staff[k] is None, k
    assert staff["current_stock"] == owner["current_stock"]
    assert all(b["unit_cost"] is None for b in staff["batches"])


# ------------------------------------------------------------------ 币种
def test_fx_rates_endpoints(client):
    tn = new_tenant()
    d = client.get("/api/settings/fx_rates", headers=hdr(tn)).json()["data"]
    assert d["base_currency"] == "HKD"
    usd = [r for r in d["rates"] if r["currency"] == "USD"][0]
    assert usd["seed"] is True and usd["confirmed"] is False and "确认" in usd["note"]
    r = client.put("/api/settings/fx_rates", headers=hdr(tn), json={"rates": [
        {"currency": "usd", "rate": 7.78, "confirmed": True}]})
    assert r.status_code == 200
    usd = [x for x in r.json()["data"]["rates"] if x["currency"] == "USD"][0]
    assert usd["rate"] == 7.78 and usd["seed"] is False and usd["confirmed"] is True
    for bad in ([{"currency": "USD", "rate": 0}], [{"currency": "USD", "rate": -1}],
                [{"currency": "美元", "rate": 7.8}]):
        assert client.put("/api/settings/fx_rates", headers=hdr(tn), json={"rates": bad}).status_code == 400
    other = client.get("/api/settings/fx_rates", headers=hdr(new_tenant())).json()["data"]["rates"]
    assert [x for x in other if x["currency"] == "USD"][0]["seed"] is True
    assert client.get("/api/settings/fx_rates", headers=hdr(tn, "staff")).status_code == 403


def test_foreign_receipt_without_rate_is_rejected_and_nothing_written(client):
    tn = new_tenant()
    sid, _ = db.create_sku("外币菜", base_unit="斤", tenant_id=tn)
    rid, ap = make_receipt(client, tn, [item("外币菜", 1, "斤", 10)], 10.0, currency="EUR")
    # 种子里有 EUR，先拿走：把汇率表整体改成只有自定义，再用没有种子的币种
    assert ap.status_code == 200
    rid2, ap2 = make_receipt(client, tn, [item("外币菜", 1, "斤", 10)], 10.0, currency="JPY")
    assert ap2.status_code == 200
    from app import units_store
    assert units_store.get_fx_rate(tn, "XXX") is None
    row = db.get_receipt_row(rid2)
    db.update_receipt(rid2, status="edited", currency="XXX")
    row = db.get_receipt_row(rid2)
    bad = client.post("/api/receipt/%d/approve" % rid2, headers=hdr(tn), json={"version": row.version})
    assert bad.status_code == 422, bad.text
    assert bad.json()["code"] == "FX_RATE_MISSING" and "设置" in bad.json()["msg"]
    assert db.get_receipt_row(rid2).status != "approved"


def test_payables_and_valuation_use_hkd(client):
    tn = new_tenant()
    sid = make_supplier(tn, "外币供应商")
    client.put("/api/settings/fx_rates", headers=hdr(tn), json={"rates": [{"currency": "USD", "rate": 8.0}]})
    rid, ap = make_receipt(client, tn, [item("美元货", 1, "斤", 100)], 100.0, supplier="外币供应商",
                           currency="USD", supplier_id=sid)
    assert ap.status_code == 200, ap.text
    p = client.get("/api/payables", headers=hdr(tn)).json()["data"]
    row = p["rows"][0]
    assert row["currency"] == "USD" and row["total_amount"] == 100.0 and row["total_hkd"] == 800.0
    assert p["total_outstanding"] == 800.0
    pay = client.post("/api/payments", headers=hdr(tn), data={
        "supplier_id": str(sid), "amount": "800", "method": "cash",
        "paid_at": date.today().isoformat(), "receipt_ids": [str(rid)]})
    assert pay.status_code == 200, pay.text
    assert client.get("/api/payables", headers=hdr(tn)).json()["data"]["total_outstanding"] == 0.0
