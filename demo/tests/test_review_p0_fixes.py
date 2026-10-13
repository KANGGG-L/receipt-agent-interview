# -*- coding: utf-8 -*-
"""审查 P0 修复回归：折扣落库、approve 重算、成本报表口径、应付、对账租户、转手工权限、数量覆盖、SKU 误并、单位换算、jsStr。"""

import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db

OWNER = {"X-Role": "owner"}
STAFF = {"X-Role": "staff"}
MAIN_JS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "static", "js", "main.js")


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _approved_receipt(client, items, total, supplier="测试供应商", date="2026-10-05",
                      settlement="credit", **fees):
    body = {"supplier_name": supplier, "date": date, "total_amount": total,
            "settlement_type": settlement, "items": items, "version": None,
            "source": "manual", **fees}
    r = client.post("/api/save_edited", headers=STAFF, json=body)
    assert r.status_code == 200 and r.json().get("status") == "success", r.text
    rid = r.json().get("receipt_id") or r.json().get("id")
    row = db.get_receipt_row(rid)
    ap = client.post("/api/receipt/%d/approve" % rid, headers=OWNER, json={"version": row.version})
    return rid, ap


def _item(name, qty, unit, price, **kw):
    return {"name": name, "quantity": qty, "unit": unit, "unit_price": price,
            "amount": round(qty * price, 2), **kw}


def test_discount_survives_save_and_approve(client):
    rid, ap = _approved_receipt(client, [_item("菜心", 10, "斤", 10)], 90.0,
                                discount_amount=10.0)
    assert ap.status_code == 200, ap.text
    row = db.get_receipt_row(rid)
    assert row.discount_amount == 10.0
    detail = client.get("/api/receipt/%d" % rid, headers=OWNER).json()["data"]
    assert detail["discount_amount"] == 10.0


def test_convert_manual_allowed_for_staff(client):
    rid = db.create_receipt(supplier_name="x", status="error")
    r = client.post("/api/receipt/%d/convert_manual" % rid, headers=STAFF)
    assert r.status_code == 200, r.text
    assert db.get_receipt_row(rid).status == "edited"


def test_cost_report_excludes_void_items(client):
    before = client.get("/api/cost_report?month=2026-10", headers=OWNER).json()["data"]["month_total"]
    rid, ap = _approved_receipt(
        client, [_item("菜心", 1, "斤", 100), _item("作废行", 1, "斤", 50, is_void=1)], 100.0)
    assert ap.status_code == 200, ap.text
    r = client.get("/api/cost_report?month=2026-10", headers=OWNER).json()
    assert round(r["data"]["month_total"] - before, 2) == 100.0


def test_unpaid_credit_excludes_paid_receipts(client):
    sid, _err = db.create_supplier("甲供应商")
    assert sid, _err
    rid, ap = _approved_receipt(client, [_item("菜心", 1, "斤", 100)], 100.0, supplier="甲供应商")
    assert ap.status_code == 200, ap.text
    db.update_receipt(rid, supplier_id=sid)
    assert db.supplier_stats(sid)["unpaid_credit_total"] == 100.0
    db.update_receipt(rid, paid_at="2026-10-06", payment_id=1)
    assert db.supplier_stats(sid)["unpaid_credit_total"] == 0.0


def _line(i=1, amount=1.0, status="pending"):
    return {"id": i, "side": "restaurant", "line_date": "2026-10-01", "docket_no": "#1",
            "amount": amount, "match_status": status, "match_status_label": "待核对",
            "matched_receipt_id": None, "resolution_note": "", "resolved": False}


def test_reconciliation_task_not_visible_across_tenants(client):
    from app import recon_store
    tid = recon_store.create_task("tenantB", 1, "month", "", "", [_line()])
    h = {"X-Role": "owner", "X-Tenant-Id": "tenantA"}
    assert client.get("/api/reconciliation/%s" % tid, headers=h).status_code == 404
    assert client.post("/api/reconciliation/%s/resolve" % tid, headers=h,
                       json={"line_id": 1, "action": "write_off"}).status_code == 404
    ids = [t["id"] for t in client.get("/api/reconciliation", headers=h).json()["data"]]
    assert tid not in ids
    assert recon_store.get_task("tenantB", tid)["lines"][0]["match_status"] == "pending"


def test_reconciliation_resolve_rejects_unknown_line_or_action(client):
    from app import recon_store
    tid = recon_store.create_task("default", 1, "month", "", "", [_line()])
    assert client.post("/api/reconciliation/%s/resolve" % tid, headers=OWNER,
                       json={"line_id": 99, "action": "write_off"}).status_code == 404
    assert client.post("/api/reconciliation/%s/resolve" % tid, headers=OWNER,
                       json={"line_id": 1, "action": "whatever"}).status_code == 400


def test_multipack_quantity_not_overwritten_when_present():
    """品名含 5L*2樽、AI 给了数量 3：经 extract_chain 后处理数量必须保持 3，门禁结果不得怪到 AI。"""
    from app.chains import extract_chain, supervisor
    raw = {
        "doc_form": "printed_delivery_note", "vendor": "九龙粮油", "date": "2026-10-05",
        "items": [{"name": "大豆油 5L*2樽", "qty": 3, "unit": "樽", "unit_price": 100, "amount": 300}],
        "total": 300, "payment_marked": False, "confidence": 0.9,
    }
    data, err = extract_chain._parse_to_receipt(raw)
    assert err is None, err
    assert data.items[0].qty == 3.0
    checked, gate_err, issues = supervisor._run_gates_ex(data)
    assert gate_err is None and issues == []


def test_fuzzy_sku_match_rejects_different_product(client):
    from app.services.receipt_utils import _match_sku
    db.create_sku("牛肉", base_unit="斤")
    item = {"name": "牛肉丸", "unit": "包", "unit_price": 50.0, "sku_id": None, "matched": 0}
    _match_sku(item, tenant_id="default")
    assert not item.get("sku_id")
    db.create_sku("鸡蛋仔粉", base_unit="包")
    item = {"name": "鸡蛋", "unit": "只", "unit_price": 1.0, "sku_id": None, "matched": 0}
    _match_sku(item, tenant_id="default")
    assert not item.get("sku_id")


def test_stock_in_converts_to_base_unit(client):
    sku_id, _ = db.create_sku("白菜", base_unit="斤")
    db.update_sku(sku_id, current_stock=8.0) if hasattr(db, "update_sku") else None
    rid, ap = _approved_receipt(client, [_item("白菜", 2, "kg", 10)], 20.0, settlement="cash")
    assert ap.status_code == 200, ap.text
    sku = db.get_sku(sku_id)
    # 1 kg = 1/0.6048 斤（斤按港斤 0.6048kg 统一定义）
    assert sku.current_stock == pytest.approx(8.0 + 2 / 0.6048, abs=1e-3)


def test_jsstr_is_safe_inside_single_quoted_attribute():
    # F5 S4-11：jsStr 实现已随公共工具域迁至 common.js。守真不变量＝「前端加载集合
    # （main.js + 全部域文件）中恰有一份 jsStr 定义，且其行为安全」，实现位置变化不放宽。
    import glob
    frontend_files = [MAIN_JS] + sorted(
        p for p in glob.glob(os.path.join(os.path.dirname(MAIN_JS), "*.js"))
        if os.path.basename(p) != "main.js")
    hits = [p for p in frontend_files
            if re.search(r"^function jsStr\(v\) \{", open(p, encoding="utf-8").read(), re.M)]
    assert len(hits) == 1, "jsStr 必须恰有一份实现（前端加载集合内）：%r" % hits
    js = ("const fs=require('fs');const s=fs.readFileSync(%r,'utf8');"
          "const m=s.match(/function jsStr\\(v\\) \\{[\\s\\S]*?\\n\\}/)[0];"
          "eval(m);const o=jsStr('&quot;+alert(1)+&quot;');console.log(o);" % hits[0])
    out = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=20).stdout.strip()
    assert "&" not in out and "<" not in out and "'" not in out
    assert re.fullmatch(r'"[^"]*"', out)
