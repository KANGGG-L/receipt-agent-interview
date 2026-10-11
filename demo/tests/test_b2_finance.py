# -*- coding: utf-8 -*-
"""B2：应付、付款、对账、供应商合并、付款凭证（S1-P0-06/07、S1-P1-01/02/08、S1-P2-04、S3-22）。"""

import io
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

from app import db, recon_store
from _b2_helpers import hdr, item, make_receipt, make_supplier, new_tenant


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _approved(client, tn, sid, name, amount, d="2026-10-05", **kw):
    rid, ap = make_receipt(client, tn, [item("菜", 1, "斤", amount)], amount, supplier=name,
                           date=d, supplier_id=sid, **kw)
    assert ap.status_code == 200, ap.text
    return rid


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


# ------------------------------------------------------------------ 应付
def test_unpaid_total_deducts_paid_and_partial(client):
    tn = new_tenant()
    sid = make_supplier(tn, "应付甲")
    r1 = _approved(client, tn, sid, "应付甲", 100.0, "2026-10-01")
    r2 = _approved(client, tn, sid, "应付甲", 60.0, "2026-10-02")
    assert _sup(client, tn, sid)["unpaid_credit_total"] == 160.0
    assert _pay(client, tn, sid, 100, [r1]).status_code == 200
    assert _sup(client, tn, sid)["unpaid_credit_total"] == 60.0
    assert _pay(client, tn, sid, 25, [r2]).status_code == 200
    s = _sup(client, tn, sid)
    assert s["unpaid_credit_total"] == 35.0 and s["unpaid_credit_count"] == 1
    # 单据状态按已付金额推导
    rows = {r["id"]: r for r in client.get("/api/receipts", headers=hdr(tn)).json()["data"]}
    assert rows[r1]["payment_status"] == "paid"
    assert rows[r2]["payment_status"] == "partial"
    assert rows[r2]["paid_amount"] == 25.0 and rows[r2]["outstanding_amount"] == 35.0


def test_overdue_uses_payment_terms_and_receipt_date(client):
    tn = new_tenant()
    sid = make_supplier(tn, "账期乙", terms=30)
    old = (date.today() - timedelta(days=45)).isoformat()
    fresh = (date.today() - timedelta(days=5)).isoformat()
    r_old = _approved(client, tn, sid, "账期乙", 80.0, old)
    r_new = _approved(client, tn, sid, "账期乙", 20.0, fresh)
    s = _sup(client, tn, sid)
    assert s["is_overdue"] is True and s["overdue_amount"] == 80.0 and s["overdue_count"] == 1
    assert s["unpaid_credit_total"] == 100.0
    # 部分付款按付款分配扣减逾期金额
    assert _pay(client, tn, sid, 30, [r_old]).status_code == 200
    s = _sup(client, tn, sid)
    assert s["overdue_amount"] == 50.0
    # 无账期、无到期日不判逾期
    sid2 = make_supplier(tn, "无账期")
    _approved(client, tn, sid2, "无账期", 10.0, old)
    assert _sup(client, tn, sid2)["is_overdue"] is False


def test_supplier_table_and_payables_list_same_total(client):
    tn = new_tenant()
    sid = make_supplier(tn, "口径丙")
    r1 = _approved(client, tn, sid, "口径丙", 100.0)
    _approved(client, tn, sid, "口径丙", 40.0)
    _pay(client, tn, sid, 70, [r1])
    s = _sup(client, tn, sid)
    p = client.get("/api/payables?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert s["unpaid_credit_total"] == p["total_outstanding"] == 70.0
    assert s["unpaid_credit_count"] == p["outstanding_count"] == 2
    assert "已入账" in s["unpaid_basis"] and "已入账" in p["basis"]
    # 待入账赊单另列，不混进未付合计
    make_receipt(client, tn, [item("待审", 1, "斤", 33)], 33.0, supplier="口径丙", supplier_id=sid, approve=False)
    s = _sup(client, tn, sid)
    assert s["unpaid_credit_total"] == 70.0 and s["pending_credit_total"] == 33.0


def test_supplier_stats_do_not_scan_per_supplier(client):
    """供应商列表一次聚合：N 个供应商不会随 N 增加单据全表扫描次数。"""
    tn = new_tenant()
    for i in range(6):
        sid = make_supplier(tn, "批量%d" % i)
        _approved(client, tn, sid, "批量%d" % i, 10.0 + i)
    calls = {"n": 0}
    orig = db.list_receipt_rows

    def counting(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    db.list_receipt_rows = counting
    try:
        rows = client.get("/api/suppliers", headers=hdr(tn)).json()["data"]
    finally:
        db.list_receipt_rows = orig
    assert len(rows) >= 6 and calls["n"] == 1


# ------------------------------------------------------------------ 付款登记校验
def test_payment_validation_messages(client):
    tn = new_tenant()
    sid = make_supplier(tn, "校验丁")
    other = make_supplier(tn, "别家戊")
    rid = _approved(client, tn, sid, "校验丁", 100.0)
    edited, _ = make_receipt(client, tn, [item("未入账", 1, "斤", 5)], 5.0, supplier="校验丁",
                             supplier_id=sid, approve=False)
    cases = [
        (dict(sid=999999, amount=10, rids=[]), 404, "SUPPLIER_NOT_FOUND"),
        (dict(sid=sid, amount=-1, rids=[]), 400, "PAYMENT_AMOUNT_INVALID"),
        (dict(sid=sid, amount=0, rids=[]), 400, "PAYMENT_AMOUNT_INVALID"),
        (dict(sid=sid, amount=10, rids=[], method="bitcoin"), 400, "PAYMENT_METHOD_INVALID"),
        (dict(sid=sid, amount=10, rids=[], paid_at="not-a-date"), 400, "PAYMENT_DATE_INVALID"),
        (dict(sid=sid, amount=10, rids=[], paid_at="2999-01-01"), 400, "PAYMENT_DATE_INVALID"),
        (dict(sid=sid, amount=10, rids=[edited]), 400, "RECEIPT_NOT_APPROVED"),
        (dict(sid=other, amount=10, rids=[rid]), 400, "RECEIPT_SUPPLIER_MISMATCH"),
        (dict(sid=sid, amount=10, rids=[987654]), 404, "RECEIPT_NOT_FOUND"),
        (dict(sid=sid, amount=500, rids=[rid]), 400, "PAYMENT_OVER_OUTSTANDING"),
    ]
    for c, status, code in cases:
        r = _pay(client, tn, c["sid"], c["amount"], c["rids"], method=c.get("method", "cash"),
                 paid_at=c.get("paid_at"))
        assert r.status_code == status and r.json()["code"] == code, (c, r.text)
        assert r.json()["msg"] and "status=" not in r.json()["msg"]
    # 失败的请求不留任何付款
    assert client.get("/api/payments", headers=hdr(tn)).json()["data"] == []
    row = db.get_receipt_row(edited)
    assert not row.paid_at and not row.payment_id


def test_cross_tenant_receipt_or_supplier_rejected(client):
    a, b = new_tenant(), new_tenant()
    sid_a = make_supplier(a, "甲租户供应商")
    rid_a = _approved(client, a, sid_a, "甲租户供应商", 50.0)
    sid_b = make_supplier(b, "乙租户供应商")
    assert _pay(client, b, sid_b, 50, [rid_a]).status_code == 404
    assert _pay(client, b, sid_a, 50, []).status_code == 404


def test_duplicate_payment_on_paid_receipt_is_409(client):
    tn = new_tenant()
    sid = make_supplier(tn, "重复己")
    rid = _approved(client, tn, sid, "重复己", 100.0)
    assert _pay(client, tn, sid, 100, [rid]).status_code == 200
    r = _pay(client, tn, sid, 100, [rid])
    assert r.status_code == 409 and r.json()["code"] == "RECEIPT_ALREADY_PAID"
    assert len(client.get("/api/payments", headers=hdr(tn)).json()["data"]) == 1


def test_one_payment_for_many_receipts_with_allocations(client):
    tn = new_tenant()
    sid = make_supplier(tn, "多单庚")
    r1 = _approved(client, tn, sid, "多单庚", 100.0, "2026-10-01")
    r2 = _approved(client, tn, sid, "多单庚", 50.0, "2026-10-02")
    r3 = _approved(client, tn, sid, "多单庚", 70.0, "2026-10-03")
    resp = _pay(client, tn, sid, 130, [r1, r2, r3])
    assert resp.status_code == 200, resp.text
    alloc = {a["receipt_id"]: (a["amount"], a["payment_status"]) for a in resp.json()["data"]["allocations"]}
    assert alloc[r1] == (100.0, "paid") and alloc[r2] == (30.0, "partial") and r3 not in alloc
    pays = client.get("/api/payments?supplier_id=%d" % sid, headers=hdr(tn)).json()["data"]
    assert pays[0]["allocations"] == [{"receipt_id": r1, "amount": 100.0}, {"receipt_id": r2, "amount": 30.0}]
    assert _sup(client, tn, sid)["unpaid_credit_total"] == 90.0
    # 指定分配
    import json
    r = client.post("/api/payments", headers=hdr(tn), data={
        "supplier_id": str(sid), "amount": "40", "method": "fps",
        "paid_at": date.today().isoformat(),
        "allocations": json.dumps([{"receipt_id": r2, "amount": 20}, {"receipt_id": r3, "amount": 20}])})
    assert r.status_code == 200, r.text
    bad = client.post("/api/payments", headers=hdr(tn), data={
        "supplier_id": str(sid), "amount": "40", "method": "fps",
        "paid_at": date.today().isoformat(),
        "allocations": json.dumps([{"receipt_id": r3, "amount": 10}])})
    assert bad.status_code == 400 and bad.json()["code"] == "PAYMENT_ALLOCATION_MISMATCH"
    assert _sup(client, tn, sid)["unpaid_credit_total"] == 50.0


# ------------------------------------------------------------------ 付款凭证
def _png_bytes():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


_PNG = _png_bytes()


def test_voucher_html_rejected_and_image_served_safely(client):
    tn = new_tenant()
    sid = make_supplier(tn, "凭证辛")
    rid = _approved(client, tn, sid, "凭证辛", 100.0)
    r = client.post("/api/payments", headers=hdr(tn),
                    data={"supplier_id": str(sid), "amount": "10", "method": "cash",
                          "paid_at": date.today().isoformat(), "receipt_ids": [str(rid)]},
                    files={"voucher": ("x.html", b"<script>alert(1)</script>", "text/html")})
    assert r.status_code == 400 and r.json()["code"] == "VOUCHER_REJECTED"
    assert client.get("/api/payments", headers=hdr(tn)).json()["data"] == []
    r = client.post("/api/payments", headers=hdr(tn),
                    data={"supplier_id": str(sid), "amount": "10", "method": "cash",
                          "paid_at": date.today().isoformat(), "receipt_ids": [str(rid)]},
                    files={"voucher": ("proof.jpg", _PNG, "image/jpeg")})
    assert r.status_code == 200, r.text
    url = client.get("/api/payments", headers=hdr(tn)).json()["data"][0]["voucher_image_url"]
    assert url.endswith(("&tenant_id=" + tn)) or tn in url
    got = client.get(url)
    assert got.status_code == 200
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"


def test_voucher_pdf_ok_but_served_as_attachment_and_script_pdf_rejected(client):
    tn = new_tenant()
    sid = make_supplier(tn, "凭证壬")
    rid = _approved(client, tn, sid, "凭证壬", 100.0)
    ok_pdf = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    bad_pdf = b"%PDF-1.4\n1 0 obj\n<< /OpenAction << /S /JavaScript /JS (app.alert(1)) >> >>\nendobj\n%%EOF"
    base = {"supplier_id": str(sid), "amount": "10", "method": "cash", "paid_at": date.today().isoformat(),
            "receipt_ids": [str(rid)]}
    assert client.post("/api/payments", headers=hdr(tn), data=base,
                       files={"voucher": ("a.pdf", bad_pdf, "application/pdf")}).status_code == 400
    r = client.post("/api/payments", headers=hdr(tn), data=base,
                    files={"voucher": ("a.pdf", ok_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    url = client.get("/api/payments", headers=hdr(tn)).json()["data"][0]["voucher_image_url"]
    got = client.get(url)
    assert got.headers["content-type"] == "application/pdf"
    assert "attachment" in got.headers["content-disposition"]
    assert got.headers["x-content-type-options"] == "nosniff"


def test_legacy_html_voucher_is_not_replayed_as_html(client, tmp_path):
    from app import api_finance
    tn = new_tenant()
    sid = make_supplier(tn, "旧凭证")
    name = "legacy1.html"
    (api_finance.VOUCHER_DIR / name).write_text("<script>alert(1)</script>")
    try:
        db.create_payment(sid, 5, "cash", "2026-10-01", "", name, [], tenant_id=tn)
        url = client.get("/api/payments", headers=hdr(tn)).json()["data"][0]["voucher_image_url"]
        got = client.get(url)
        assert got.status_code == 200
        assert "text/html" not in got.headers["content-type"]
        assert "attachment" in got.headers["content-disposition"]
        assert got.headers["x-content-type-options"] == "nosniff"
    finally:
        (api_finance.VOUCHER_DIR / name).unlink(missing_ok=True)


# ------------------------------------------------------------------ 供应商合并
def test_merge_rejects_same_supplier(client):
    tn = new_tenant()
    sid = make_supplier(tn, "合并自己")
    r = client.post("/api/suppliers/merge", headers=hdr(tn), json={"keep_id": sid, "drop_id": sid})
    assert r.status_code == 400
    assert db.get_supplier(sid, tenant_id=tn).active == 1


def test_merge_migrates_payments_aliases_memory_and_keeps_payables_traceable(client):
    tn = new_tenant()
    keep = make_supplier(tn, "保留公司")
    drop = make_supplier(tn, "旧名公司")
    rid = _approved(client, tn, drop, "旧名公司", 100.0)
    assert _pay(client, tn, drop, 40, [rid]).status_code == 200
    sku_id, _ = db.create_sku("合并菜", base_unit="斤", tenant_id=tn)
    db.upsert_vendor_sku_alias(supplier_id=drop, sku_id=sku_id, raw_name="合并菜A", canonical_name="合并菜",
                               unit="斤", tenant_id=tn)
    db.upsert_vendor_sku_alias(supplier_id=keep, sku_id=sku_id, raw_name="已有写法", canonical_name="已有",
                               unit="斤", tenant_id=tn)
    r = client.post("/api/suppliers/merge", headers=hdr(tn), json={"keep_id": keep, "drop_id": drop})
    assert r.status_code == 200, r.text
    moved = r.json()["data"]["moved"]
    assert moved["receipts"] == 1 and moved["payments"] == 1 and moved["sku_aliases"] >= 1
    assert db.get_receipt_row(rid).supplier_id == keep
    pays = client.get("/api/payments?supplier_id=%d" % keep, headers=hdr(tn)).json()["data"]
    assert len(pays) == 1 and pays[0]["amount"] == 40.0
    assert client.get("/api/payments?supplier_id=%d" % drop, headers=hdr(tn)).json()["data"] == []
    a = db.find_vendor_sku_alias("合并菜A", tenant_id=tn, supplier_id=keep, canonical_name="合并菜")
    assert a is not None
    s = _sup(client, tn, keep)
    assert s["unpaid_credit_total"] == 60.0
    assert db.get_supplier(drop, tenant_id=tn).active == 0
    assert "旧名公司" in recon_store.list_supplier_aliases(tn, keep)
    audit = db.get_session().get(db._AppSettingRow, "system_audit_json").value
    assert "supplier_merge" in audit


# ------------------------------------------------------------------ 对账
def _recon_create(client, tn, sid, **body):
    r = client.post("/api/reconciliation", headers=hdr(tn), json=dict(supplier_id=sid, **body))
    assert r.status_code == 200, r.text
    return r.json()["task_id"]


def _task(client, tn, tid):
    return client.get("/api/reconciliation/%s" % tid, headers=hdr(tn)).json()


def test_recon_is_persisted_and_tenant_scoped(client):
    a, b = new_tenant(), new_tenant()
    sid = make_supplier(a, "对账甲")
    _approved(client, a, sid, "对账甲", 100.0, "2026-10-05")
    tid = _recon_create(client, a, sid, start="2026-10-01", end="2026-10-31")
    # 模拟重启：重建数据库引擎，任务仍在
    db._make_engine()
    t = _task(client, a, tid)
    assert t["status"] == "success" and len(t["lines"]) == 1
    # 别的租户：读、核销、确认都是 404，列表里也看不到
    assert client.get("/api/reconciliation/%s" % tid, headers=hdr(b)).status_code == 404
    assert client.post("/api/reconciliation/%s/resolve" % tid, headers=hdr(b),
                       json={"line_id": 1, "action": "write_off"}).status_code == 404
    assert client.post("/api/reconciliation/%s/confirm" % tid, headers=hdr(b)).status_code == 404
    assert tid not in [x["id"] for x in client.get("/api/reconciliation", headers=hdr(b)).json()["data"]]
    assert client.get("/api/reconciliation/nonexistent", headers=hdr(a)).status_code == 404
    assert recon_store.get_task(a, tid)["lines"][0]["match_status"] == "pending"


def test_recon_filters_by_period_and_supplier(client):
    tn = new_tenant()
    s1, s2 = make_supplier(tn, "对账乙"), make_supplier(tn, "对账丙")
    _approved(client, tn, s1, "对账乙", 10.0, "2026-09-15")
    _approved(client, tn, s1, "对账乙", 20.0, "2026-10-15")
    _approved(client, tn, s2, "对账丙", 30.0, "2026-10-16")
    tid = _recon_create(client, tn, s1, start="2026-10-01", end="2026-10-31")
    lines = _task(client, tn, tid)["lines"]
    assert [x["amount"] for x in lines] == [20.0]
    bad = client.post("/api/reconciliation", headers=hdr(tn),
                      json={"supplier_id": s1, "start": "10月", "end": ""})
    assert bad.status_code == 400
    assert client.post("/api/reconciliation", headers=hdr(tn), json={"supplier_id": 99999}).status_code == 404
    # 列表按供应商过滤
    ids = [x["id"] for x in client.get("/api/reconciliation?supplier_id=%d" % s2, headers=hdr(tn)).json()["data"]]
    assert tid not in ids


def test_import_statement_matches_by_docket_then_date_amount(client):
    tn = new_tenant()
    sid = make_supplier(tn, "对账丁")
    r1 = _approved(client, tn, sid, "对账丁", 100.0, "2026-10-05")
    r2 = _approved(client, tn, sid, "对账丁", 50.0, "2026-10-06")
    r3 = _approved(client, tn, sid, "对账丁", 50.0, "2026-10-07")
    edited, _ = make_receipt(client, tn, [item("未入账", 1, "斤", 999)], 999.0, supplier="对账丁",
                             supplier_id=sid, date="2026-10-08", approve=False)
    ext = [
        {"date": "2026-10-06", "invoice_no": "#%d" % r3, "amount": 50.0},     # 单号命中 r3（日期故意写错）
        {"date": "2026-10-05", "invoice_no": "ZZ-1", "amount": 100.0},        # 单号不认识，日期+金额命中 r1
        {"date": "2026-10-30", "invoice_no": "X9", "amount": 77.0},           # 对账单多出来的
    ]
    r = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json={
        "supplier_id": sid, "statement_period": "2026-10", "external_lines": ext})
    assert r.status_code == 200, r.text
    body = r.json()
    s = body["summary"]
    assert s["external_statement_total"] == 227.0
    assert s["system_receipts_total"] == 200.0      # 未入账的 edited 单不算
    assert s["diff_amount"] == 27.0
    assert s["restaurant_only_amount"] == 50.0      # r2 对账单里没有
    assert s["restaurant_only_amount"] != s["external_statement_total"]
    t = _task(client, tn, body["task_id"])
    by = {x["match_status"]: [] for x in t["lines"]}
    for x in t["lines"]:
        by[x["match_status"]].append(x)
    assert {x["matched_receipt_id"] for x in by["matched"]} == {r1, r3}
    assert [x["matched_receipt_id"] for x in by["restaurant_only"]] == [r2]
    assert by["vendor_only"][0]["amount"] == 77.0
    assert t["task"]["summary"]["statement_amount"] == 227.0
    assert len(body["discrepancies"]) == 2


def test_matched_means_agrees_with_statement_not_paid(client):
    tn = new_tenant()
    sid = make_supplier(tn, "对账戊")
    rid = _approved(client, tn, sid, "对账戊", 100.0, "2026-10-05")
    assert _pay(client, tn, sid, 100, [rid]).status_code == 200   # 已付款
    tid = _recon_create(client, tn, sid, start="2026-10-01", end="2026-10-31")
    line = _task(client, tn, tid)["lines"][0]
    assert line["match_status"] == "pending"                       # 付了款也不算「已匹配」
    # 对账单金额不一致：单号对上、金额不同 -> 不算匹配
    r = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json={
        "supplier_id": sid, "statement_period": "2026-10",
        "external_lines": [{"date": "2026-10-05", "invoice_no": "#%d" % rid, "amount": 90.0}]})
    lines = _task(client, tn, r.json()["task_id"])["lines"]
    assert lines[0]["match_status"] == "amount_diff" and "10.00" in lines[0]["resolution_note"]


def test_import_statement_uses_supplier_id_and_merged_alias(client):
    tn = new_tenant()
    keep, drop = make_supplier(tn, "正名公司"), make_supplier(tn, "曾用名公司")
    rid = _approved(client, tn, drop, "曾用名公司", 100.0, "2026-10-05")
    assert client.post("/api/suppliers/merge", headers=hdr(tn),
                       json={"keep_id": keep, "drop_id": drop}).status_code == 200
    # 同名字符串但没有 supplier_id 的历史单也按别名归到保留方
    legacy, _ = make_receipt(client, tn, [item("菜", 1, "斤", 40)], 40.0, supplier="曾用名公司",
                             date="2026-10-06")
    db.update_receipt(legacy, supplier_id=None)
    r = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json={
        "supplier_id": keep, "statement_period": "2026-10",
        "external_lines": [{"date": "2026-10-05", "invoice_no": "", "amount": 100.0},
                           {"date": "2026-10-06", "invoice_no": "", "amount": 40.0}]})
    assert r.status_code == 200, r.text
    assert r.json()["summary"]["system_receipts_total"] == 140.0
    assert r.json()["discrepancies"] == []


def test_import_statement_validation(client):
    tn = new_tenant()
    sid = make_supplier(tn, "对账己")
    def post(**kw):
        return client.post("/api/reconciliation/import-statement", headers=hdr(tn),
                           json=dict(supplier_id=sid, statement_period="2026-10", **kw))
    assert post(external_lines=[]).status_code == 400
    assert post(external_lines=[{"date": "2026-10-01", "amount": "abc"}]).status_code == 400
    bad = client.post("/api/reconciliation/import-statement", headers=hdr(tn),
                      json={"supplier_id": sid, "statement_period": "上个月",
                            "external_lines": [{"date": "2026-10-01", "amount": 1}]})
    assert bad.status_code == 400
    other = client.post("/api/reconciliation/import-statement", headers=hdr(new_tenant()),
                        json={"supplier_id": sid, "statement_period": "2026-10",
                              "external_lines": [{"date": "2026-10-01", "amount": 1}]})
    assert other.status_code == 404


def test_resolve_validates_action_and_line_and_locks_after_confirm(client):
    tn = new_tenant()
    sid = make_supplier(tn, "对账庚")
    _approved(client, tn, sid, "对账庚", 100.0, "2026-10-05")
    tid = _recon_create(client, tn, sid, start="2026-10-01", end="2026-10-31")
    def resolve(**kw):
        return client.post("/api/reconciliation/%s/resolve" % tid, headers=hdr(tn), json=kw)
    assert resolve(line_id=424242, action="write_off").status_code == 404
    assert resolve(line_id=1, action="whatever").status_code == 400
    r = resolve(line_id=1, action="dispute", note="价格不对")
    assert r.status_code == 200
    line = _task(client, tn, tid)["lines"][0]
    assert line["match_status"] == "disputed" and line["resolution_note"] == "价格不对"
    assert client.post("/api/reconciliation/%s/confirm" % tid, headers=hdr(tn)).status_code == 200
    r = resolve(line_id=1, action="write_off")
    assert r.status_code == 409 and r.json()["code"] == "RECON_CONFIRMED"


def test_resolve_confirm_match_links_statement_line_to_receipt(client):
    tn = new_tenant()
    sid = make_supplier(tn, "对账辛")
    rid = _approved(client, tn, sid, "对账辛", 100.0, "2026-10-05")
    r = client.post("/api/reconciliation/import-statement", headers=hdr(tn), json={
        "supplier_id": sid, "statement_period": "2026-10",
        "external_lines": [{"date": "2026-10-09", "invoice_no": "Q1", "amount": 100.0}]})
    tid = r.json()["task_id"]
    lines = _task(client, tn, tid)["lines"]
    stmt = [x for x in lines if x["side"] == "statement"][0]
    bad = client.post("/api/reconciliation/%s/resolve" % tid, headers=hdr(tn),
                      json={"line_id": stmt["id"], "action": "confirm_match", "receipt_id": 424242})
    assert bad.status_code == 400
    ok = client.post("/api/reconciliation/%s/resolve" % tid, headers=hdr(tn),
                     json={"line_id": stmt["id"], "action": "confirm_match", "receipt_id": rid})
    assert ok.status_code == 200, ok.text
    t = _task(client, tn, tid)
    assert len(t["lines"]) == 1 and t["lines"][0]["match_status"] == "matched"
    assert t["task"]["summary"]["restaurant_only_amount"] == 0.0
    assert t["task"]["summary"]["diff_amount"] == 0.0
