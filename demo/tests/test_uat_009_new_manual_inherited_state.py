# -*- coding: utf-8 -*-
"""UAT-009 复验（2026-10-17 DEV）：新建手工单不得继承上一张单据的任何单据级状态。

既有 `test_uat_009_new_manual_receipt_clears_fees.py` 只覆盖了六项费用 DOM 行与注记，
且其 `sandbox.currentReceiptId` 读到的是 undefined（main.js 用 `let` 声明，不挂在
global 对象上），receipt_id=null 断言恒真。本文件补齐复验发现的残留路径：

R1 save payload 泄漏：打开六费用非 0 的旧单后点「新建手工单」，payload 的
   payment_evidence（自动付款证据，内存态）、doc_form（单据版式下拉）、currency
   （币种下拉）仍是旧单的值；整单「请重点核对」提示条也仍显示旧单原因。
R2 迟到回包：点「继续复核」打开旧单后、详情回包前点「新建手工单」，旧单回包随后
   把旧单六费用与 receipt_id 灌回表单（保存即改写旧单）；进行中的单张识别同理。

所有前端断言通过 vm.runInContext 读取真实的脚本作用域变量（currentReceiptId 等）。
"""

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_uat_009_new_manual_receipt_clears_fees import run_node_js_harness  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402

FEE_KEYS = ["delivery_fee", "deposit_amount", "service_fee", "tax_amount",
            "discount_amount", "rounding_adjustment"]
FEES = {"delivery_fee": 5.0, "deposit_amount": 4.0, "service_fee": 3.0,
        "tax_amount": 2.0, "discount_amount": 1.0, "rounding_adjustment": 0.5}

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat009r_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat009r_b"}
OWNER_B = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat009r_b"}


def _old_receipt(doc_form="ncr_handwritten", currency="CNY", rid=901):
    return {
        "id": rid, "receipt_id": rid, "version": 3, "status": "edited",
        "supplier_name": "Vendor_A", "date": "2026-10-15", "total_amount": 22.5,
        "settlement_type": "credit", "doc_form": doc_form, "currency": currency,
        "payment_evidence": "PAID stamp Vendor_A", "payment_mark": "已付款",
        "adjustment_notes": ["short 1 box"], "math_warnings": ["总额与明细不符"],
        "items": [{"name": "Item_old", "quantity": 1, "unit": "pcs",
                   "unit_price": 10, "amount": 10}],
        **FEES,
    }


PRELUDE = r"""
const G = (expr) => vm.runInContext(expr, sandbox);
function fail(code, msg) { console.error('FAIL ' + msg); process.exit(code); }
function fillOneRow() {
    elements['inpSupplier'].value = 'Vendor_B';
    elements['inpDate'].value = '2026-10-17';
    elements['inpTotal'].value = '10.00';
    elements['inpSettlementType'].value = 'cash';
}
function newPayload() {
    return G('buildSavePayloadFromData(collectReviewFormData(), currentReceiptId)');
}
const FEE_KEYS = %s;
"""


def _run(body):
    js = PRELUDE % json.dumps(FEE_KEYS) + body
    code, out, err = run_node_js_harness(js)
    assert code == 0, f"exit={code}\nstdout={out}\nstderr={err}"
    return out


@pytest.mark.parametrize("doc_form,currency", [
    ("ncr_handwritten", "CNY"),
    ("credit_note", "USD"),
    ("monthly_statement", "EUR"),
])
def test_new_manual_payload_has_no_inherited_document_state(doc_form, currency):
    old = _old_receipt(doc_form, currency)
    out = _run(f"""
    const old = {json.dumps(old, ensure_ascii=False)};
    sandbox.renderEditForm(old);
    G('currentReceiptId = 901; currentReceiptData = ' + JSON.stringify(old));
    if (elements['inpDocForm'].value !== old.doc_form) fail(10, 'old doc_form not rendered');
    if (elements['inpCurrency'].value !== old.currency) fail(11, 'old currency not rendered');
    if (G('getAutoPaymentEvidence()') !== old.payment_evidence) fail(12, 'old evidence not loaded');
    if (elements['feesRowsContainer'].children.length !== 6) fail(13, 'old fees not rendered');

    sandbox.startManualEntry();
    fillOneRow();
    const p = newPayload();
    const errs = [];   // 汇总全部泄漏项，一次报出（不在第一项失败处短路）
    const chk = (ok, msg) => {{ if (!ok) errs.push(msg); }};
    chk(G('currentReceiptId') === null, 'currentReceiptId=' + G('currentReceiptId'));
    chk(p.receipt_id === null, 'payload.receipt_id=' + p.receipt_id);
    FEE_KEYS.forEach(k => chk(p[k] === 0, k + '=' + p[k]));
    chk(FEE_KEYS.every(k => Object.prototype.hasOwnProperty.call(p, k)), 'fee key stripped');
    chk(p.payment_evidence === '', 'payment_evidence leaked=' + JSON.stringify(p.payment_evidence));
    chk(p.doc_form === 'printed_delivery_note', 'doc_form leaked=' + p.doc_form);
    chk(p.currency === 'HKD', 'currency leaked=' + p.currency);
    chk(JSON.stringify(p.adjustment_notes) === '[]', 'notes leaked=' + JSON.stringify(p.adjustment_notes));
    chk(!('version' in p), 'version leaked=' + p.version);
    chk(p.settlement_type === 'cash', 'settlement=' + p.settlement_type);
    chk(G('document.getElementById("alertBanner").classList.contains("hide")'), 'old alert banner still visible');
    if (errs.length) fail(20, errs.join(' | '));
    console.log('PAYLOAD ' + JSON.stringify(p));
    """)
    assert "PAYLOAD" in out


def test_old_receipt_reopen_keeps_all_saved_document_state():
    old = _old_receipt()
    _run(f"""
    const old = {json.dumps(old, ensure_ascii=False)};
    sandbox.renderEditForm(old);
    sandbox.startManualEntry();
    sandbox.renderEditForm(old);
    G('currentReceiptId = 901; currentReceiptData = ' + JSON.stringify(old));
    const fees = G('collectFeeMap()');
    const want = {json.dumps(FEES)};
    Object.keys(want).forEach(k => {{ if (fees[k] !== want[k]) fail(40, k + '=' + fees[k]); }});
    const p = newPayload();
    if (p.receipt_id !== 901) fail(41, 'reopen receipt_id=' + p.receipt_id);
    if (p.doc_form !== 'ncr_handwritten' || p.currency !== 'CNY') fail(42, p.doc_form + '/' + p.currency);
    if (p.payment_evidence !== old.payment_evidence) fail(43, 'evidence lost');
    if (p.version !== 3) fail(45, 'version=' + p.version);
    """)


def test_user_choices_on_new_manual_are_still_submitted():
    """修复是复位默认值而不是隐藏/剔除字段：新单上用户自选的值必须原样提交。"""
    _run(f"""
    sandbox.renderEditForm({json.dumps(_old_receipt(), ensure_ascii=False)});
    sandbox.startManualEntry();
    fillOneRow();
    elements['inpDocForm'].value = 'credit_note';
    elements['inpCurrency'].value = 'USD';
    G("appendFeeRow('tax_amount', 1.25)");
    const p = newPayload();
    if (p.doc_form !== 'credit_note' || p.currency !== 'USD') fail(50, p.doc_form + '/' + p.currency);
    if (p.tax_amount !== 1.25) fail(51, 'tax=' + p.tax_amount);
    if (p.receipt_id !== null) fail(52, 'rid=' + p.receipt_id);
    """)


def test_late_open_receipt_response_cannot_refill_new_manual():
    old = _old_receipt(rid=902)
    _run(f"""
    const old = {json.dumps(old, ensure_ascii=False)};
    const pending = [];
    // common.js 在无原生 fetch 的沙箱里让 apiFetch 回落到 window.fetch：用可控的延迟回包替换
    // 只延迟「打开单据」详情请求；埋点等其它请求立即回空
    sandbox.fetch = (url) => String(url).startsWith('/api/receipt/')
        ? new Promise(res => pending.push({{url, res}}))
        : Promise.resolve({{ status: 200, ok: true, json: () => Promise.resolve({{}}) }});
    (async () => {{
        sandbox.openReceiptInWorkbench(902, true, true);
        if (pending.length !== 1) fail(60, 'open request not issued: ' + pending.length);
        sandbox.startManualEntry();
        pending[0].res({{ json: () => Promise.resolve({{ status: 'success', data: old }}) }});
        await new Promise(r => setTimeout(r, 30));
        if (G('currentReceiptId') !== null) fail(61, 'late response rebound currentReceiptId=' + G('currentReceiptId'));
        if (elements['feesRowsContainer'].children.length !== 0) fail(62, 'late response refilled fees');
        fillOneRow();
        const p = newPayload();
        if (p.receipt_id !== null) fail(63, 'rid=' + p.receipt_id);
        FEE_KEYS.forEach(k => {{ if (p[k] !== 0) fail(64, k + '=' + p[k]); }});
        // 控制组：之后正常再打开旧单仍然生效（未把打开流程整个禁掉）
        sandbox.openReceiptInWorkbench(902, true, true);
        pending[1].res({{ json: () => Promise.resolve({{ status: 'success', data: old }}) }});
        await new Promise(r => setTimeout(r, 30));
        if (G('currentReceiptId') !== 902) fail(65, 'normal reopen broken: ' + G('currentReceiptId') + ' urls=' + JSON.stringify(pending.map(x => x.url)));
        if (elements['feesRowsContainer'].children.length !== 6) fail(66, 'normal reopen fees missing');
        console.log('RACE_OK');
    }})().catch(e => fail(69, String(e && e.stack || e)));
    """)


def test_in_flight_single_upload_is_invalidated_by_new_manual():
    _run("""
    G('singlePollToken = { cancelled: false }');
    const genBefore = G('singleUploadGen');
    const tok = G('singlePollToken');
    sandbox.startManualEntry();
    if (!(G('singleUploadGen') > genBefore)) fail(70, 'singleUploadGen not bumped');
    if (tok.cancelled !== true) fail(71, 'poll token not cancelled');
    """)


def test_api_new_manual_default_payload_roundtrip_and_tenant_isolation():
    """后端契约不变：前端新单默认 payload 原文保存后 DB 读回逐值为默认；旧单不变；B 不可见。"""
    client = TestClient(app)
    old_body = {"supplier_name": "Vendor_A", "date": "2026-10-15", "total_amount": 22.5,
                "settlement_type": "credit", "source": "manual", "doc_form": "ncr_handwritten",
                "currency": "CNY", "payment_evidence": "PAID stamp Vendor_A",
                "adjustment_notes": ["short 1 box"],
                "items": [{"name": "Item_old", "quantity": 1, "unit": "pcs",
                           "unit_price": 10, "amount": 10}], **FEES}
    r = client.post("/api/save_edited", headers=STAFF_A, json=old_body)
    assert r.status_code == 200, r.text
    old_id = r.json()["receipt_id"]

    s = db.get_session()
    try:
        b_before = s.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat009r_b").count()
    finally:
        s.close()

    new_body = {"receipt_id": None, "source": "manual", "supplier_name": "Vendor_B",
                "date": "2026-10-17", "sheet_name": "2026-10", "total_amount": 10,
                "items": [{"name": "Item_new", "quantity": 1, "unit": "pcs", "unit_price": 10,
                           "amount": 10, "actual_qty": None, "cost_center_id": None, "is_void": False}],
                "settlement_type": "cash", "doc_form": "printed_delivery_note", "currency": "HKD",
                **{k: 0 for k in FEE_KEYS}, "adjustment_notes": [], "payment_evidence": "",
                "department_id": None, "payment_mark": "未付款"}
    r2 = client.post("/api/save_edited", headers=STAFF_A, json=new_body)
    assert r2.status_code == 200, r2.text
    new_id = r2.json()["receipt_id"]
    assert new_id != old_id

    new = client.get(f"/api/receipt/{new_id}", headers=STAFF_A).json()["data"]
    for k in FEE_KEYS:
        assert float(new[k]) == 0.0, (k, new[k])
    assert (new.get("payment_evidence") or "") == ""
    assert new.get("currency") == "HKD"
    assert not new.get("adjustment_notes")

    old = client.get(f"/api/receipt/{old_id}", headers=STAFF_A).json()["data"]
    for k, v in FEES.items():
        assert float(old[k]) == v, (k, old[k])
    assert old.get("payment_evidence") == "PAID stamp Vendor_A"
    assert old.get("currency") == "CNY"

    assert client.get(f"/api/receipt/{old_id}", headers=OWNER_B).status_code == 404
    assert client.get(f"/api/receipt/{new_id}", headers=OWNER_B).status_code == 404
    rb = client.post("/api/save_edited", headers=STAFF_B, json={**new_body, "receipt_id": new_id})
    assert rb.status_code == 404, rb.text
    s = db.get_session()
    try:
        b_after = s.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat009r_b").count()
    finally:
        s.close()
    assert b_after == b_before
