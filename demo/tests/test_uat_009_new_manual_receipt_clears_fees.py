# -*- coding: utf-8 -*-
"""UAT-009 专项测试：新建手工单清除上一张单继承的六项附加费用。

核心验证点与正负边界：
1. 打开一张六项费用均非 0 的合成单据，然后新建手工单并填一行明细：六个费用字段与最终 save payload 必须全部为默认 0 或空，且 receipt_id 为 null；
2. 回到旧单据重新打开时，其已保存的六项费用必须原样保留，不得被清空或覆盖（清理只作用于新单）；
3. 不得发生跨会话泄漏：切换单据、刷新页面、关闭后重开，新单费用都必须从默认值开始；
4. 修复必须是清理新单状态，不得通过隐藏字段、改默认展示或屏蔽保存字段来实现；后端契约不变（用户自填费用可正常保存）；
5. 跨 tenant 读写被拒绝或不可见；Tenant B baseline 在测试前后零意外改动。
"""

import json
import os
import subprocess
import sys
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat009_a"}
OWNER_A = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat009_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat009_b"}
OWNER_B = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat009_b"}

FEES_FIXTURE = {
    "delivery_fee": 5.0,
    "deposit_amount": 4.0,
    "service_fee": 3.0,
    "tax_amount": 2.0,
    "discount_amount": 1.0,
    "rounding_adjustment": 0.5,
}

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def run_node_js_harness(js_test_body):
    """在包含真实 DOM 模拟与 main.js + F4 域文件的 node 环境中运行测试断言。"""
    harness_code = f"""
    const fs = require('fs');
    const path = require('path');
    const vm = require('vm');

    const REPO_ROOT = {json.dumps(REPO_ROOT)};
    const mainPath = path.join(REPO_ROOT, 'demo', 'static', 'js', 'main.js');
    const src = fs.readFileSync(mainPath, 'utf8');

    const elements = {{}};
    function mkEl(id, props) {{
        const el = Object.assign({{
            id: id || '',
            value: '', tagName: 'DIV', textContent: '', style: {{}}, dataset: {{}},
            children: [],
            set innerHTML(val) {{
                this._innerHTML = val;
                if (val === '') this.children = [];
            }},
            get innerHTML() {{ return this._innerHTML || ''; }},
            set className(val) {{
                this._className = val;
                if (val) val.split(/\\s+/).forEach(c => c && this.classList.add(c));
            }},
            get className() {{ return this._className || ''; }},
            classList: {{
                classes: new Set(),
                add(c) {{ this.classes.add(c); }},
                remove(c) {{ this.classes.delete(c); }},
                toggle(c, force) {{
                    if (force === undefined) {{
                        if (this.classes.has(c)) this.classes.delete(c);
                        else this.classes.add(c);
                    }} else if (force) {{
                        this.classes.add(c);
                    }} else {{
                        this.classes.delete(c);
                    }}
                }},
                contains(c) {{ return this.classes.has(c); }}
            }},
            querySelector(sel) {{
                if (!sel) return null;
                if (sel.startsWith('.')) {{
                    const cls = sel.slice(1);
                    for (const c of this.children) {{
                        if (c.classList && c.classList.contains(cls)) return c;
                        const nested = c.querySelector(sel);
                        if (nested) return nested;
                    }}
                }}
                return null;
            }},
            querySelectorAll(sel) {{
                if (sel === '.fee-row') return this.children.filter(c => c.classList.contains('fee-row'));
                if (sel === 'tr') return this.children.filter(c => c.tagName === 'TR');
                return [];
            }},
            appendChild(child) {{
                this.children.push(child);
                child.parentNode = this;
                return child;
            }},
            removeChild(child) {{
                const idx = this.children.indexOf(child);
                if (idx >= 0) this.children.splice(idx, 1);
                return child;
            }},
            remove() {{
                if (this.parentNode) this.parentNode.removeChild(this);
            }},
            closest(sel) {{
                if (sel === '.fee-row' && this.classList.contains('fee-row')) return this;
                if (this.parentNode) return this.parentNode.closest(sel);
                return null;
            }},
            addEventListener() {{}},
            setAttribute(k, v) {{ this[k] = v; }},
            getAttribute(k) {{ return this[k]; }},
            focus() {{}},
            scrollIntoView() {{}}
        }}, props || {{}});
        if (id) elements[id] = el;
        return el;
    }}

    [
        'inpSupplier', 'inpDate', 'inpSheet', 'inpTotal', 'inpSettlementType',
        'inpDepartmentId', 'inpCurrency', 'inpDocForm', 'inpPaymentMark',
        'loadingCard', 'preConfirmCard', 'errorCard', 'prefillFormCard', 'splitViewArea',
        'feesDrawerContent', 'feesToggleIcon', 'feesSummaryBadge', 'feesRowsEmpty', 'feesRowsContainer',
        'notesDrawerContent', 'notesToggleIcon', 'notesSummaryBadge', 'notesRowsEmpty', 'notesRowsContainer',
        'manualEntryNoImage', 'imgViewerContainer', 'imgToolbar', 'leftCardTitleActions',
        'manualEntryFormBadge', 'aiPrefillNoticeBadge', 'manualEntryFormTitle', 'previewImg',
        'itemTableBody', 'unitsDatalist', 'reviewSaveStatusBadge', 'btnSaveReview', 'btnSaveAndNext'
    ].forEach(id => mkEl(id));

    elements['feesRowsEmpty'].classList.add('hide');

    const sandbox = {{
        console,
        setTimeout, clearTimeout,
        document: {{
            getElementById(id) {{
                if (!elements[id]) mkEl(id);
                return elements[id];
            }},
            createElement(tagName) {{
                const el = mkEl(null, {{ tagName: tagName.toUpperCase() }});
                return el;
            }},
            querySelector() {{ return null; }},
            querySelectorAll() {{ return []; }},
            addEventListener() {{}},
            documentElement: {{ style: {{ setProperty() {{}} }} }},
            body: mkEl('body'),
        }},
        localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
        track() {{}},
        showToast() {{}},
        apiFetch() {{ return Promise.resolve({{ json: () => Promise.resolve({{}}) }}); }},
        todayLocalStr() {{ return '2026-10-16'; }},
        populateDeptSelect() {{}},
        showQualityWarnings() {{}},
        setPaymentMarkBadge() {{}},
        addEmptyRow() {{
            const tr = mkEl(null, {{ tagName: 'TR' }});
            const inpName = mkEl(null, {{ value: '手工商品1' }}); inpName.classList.add('inp-name');
            const inpQty = mkEl(null, {{ value: '1' }}); inpQty.classList.add('inp-qty');
            const inpUnit = mkEl(null, {{ value: '件' }}); inpUnit.classList.add('inp-unit');
            const inpPrice = mkEl(null, {{ value: '10' }}); inpPrice.classList.add('inp-price');
            const inpAmount = mkEl(null, {{ value: '10.00' }}); inpAmount.classList.add('inp-amount');
            const inpActualQty = mkEl(null, {{ value: '' }}); inpActualQty.classList.add('inp-actual-qty');
            const inpDept = mkEl(null, {{ value: '' }}); inpDept.classList.add('inp-dept');
            tr.appendChild(inpName); tr.appendChild(inpQty); tr.appendChild(inpUnit);
            tr.appendChild(inpPrice); tr.appendChild(inpAmount); tr.appendChild(inpActualQty); tr.appendChild(inpDept);
            elements['itemTableBody'].appendChild(tr);
        }},
        refreshReceiptIssueSummary() {{}},
        syncFeedbackButtonsUI() {{}},
        syncFeedbackVisibility() {{}},
        syncRecoveryVisibility() {{}},
        resetQualityFailCount() {{}},
        captureResultVersion() {{}},
    }};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;

    vm.runInNewContext(src, sandbox, {{ filename: 'main.js' }});
    const {{ loadF4Domains }} = require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js'));
    loadF4Domains((dsrc, name) => vm.runInNewContext(dsrc, sandbox, {{ filename: name }}));

    // 执行用户提供的测试逻辑
    {js_test_body}
    """
    proc = subprocess.run(["node", "-e", harness_code], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def test_uat009_requirement_1_and_4_new_manual_clears_fees_and_retains_contract():
    """验证需求 1 与 4：新建手工单清空前单六项费用，且不破坏用户自填费用的保存契约。"""
    js_test = """
    // 步骤 1：打开一张六项费用均非 0 的旧单据
    const oldReceiptData = {
        id: 888,
        receipt_id: 888,
        supplier_name: '旧供应商',
        date: '2026-10-15',
        total_amount: 112.5,
        delivery_fee: 5.0,
        deposit_amount: 4.0,
        service_fee: 3.0,
        tax_amount: 2.0,
        discount_amount: 1.0,
        rounding_adjustment: 0.5,
        items: [{ name: '旧商品', quantity: 1, unit: '件', unit_price: 100, amount: 100 }]
    };
    sandbox.renderEditForm(oldReceiptData);
    if (elements['feesRowsContainer'].children.length !== 6) {
        console.error("FAIL: 旧单费用行未能渲染 6 行，实际行数=" + elements['feesRowsContainer'].children.length);
        process.exit(101);
    }

    // 步骤 2：新建手工单
    sandbox.startManualEntry();

    // 断言 1：DOM 费用容器必须彻底清空
    const containerCount = elements['feesRowsContainer'].children.length;
    if (containerCount !== 0) {
        console.error("FAIL [Req 1]: 新建手工单费用容器未清空，仍有残留行数=" + containerCount);
        process.exit(1);
    }

    // 断言 2：抽屉折叠与提示状态
    if (!elements['feesDrawerContent'].classList.contains('hide')) {
        console.error("FAIL [Req 1]: 费用抽屉未收起");
        process.exit(2);
    }
    if (!elements['feesSummaryBadge'].classList.contains('hide')) {
        console.error("FAIL [Req 1]: 费用摘要徽章未隐藏");
        process.exit(3);
    }

    // 断言 3：填一行明细后，采集与 save payload 必须全为 0/空且 receipt_id 为 null
    elements['inpSupplier'].value = '新手工供应商';
    elements['inpDate'].value = '2026-10-16';
    elements['inpTotal'].value = '10.00';
    elements['inpSettlementType'].value = 'cash';

    const formData = sandbox.collectReviewFormData();
    ['delivery_fee', 'deposit_amount', 'service_fee', 'tax_amount', 'discount_amount', 'rounding_adjustment'].forEach(k => {
        if (formData[k] !== 0) {
            console.error(`FAIL [Req 1]: collectReviewFormData 中 ${k} 不为 0，实际值=${formData[k]}`);
            process.exit(4);
        }
    });

    const payload = sandbox.buildSavePayloadFromData(formData, sandbox.currentReceiptId);
    if (payload.receipt_id != null) {
        console.error("FAIL [Req 1]: 新建手工单 receipt_id 必须为 null/undefined，实际值=" + payload.receipt_id);
        process.exit(5);
    }
    ['delivery_fee', 'deposit_amount', 'service_fee', 'tax_amount', 'discount_amount', 'rounding_adjustment'].forEach(k => {
        if (payload[k] !== 0) {
            console.error(`FAIL [Req 1]: save payload 中 ${k} 不为 0，实际值=${payload[k]}`);
            process.exit(6);
        }
    });

    // 契约验证（Requirement 4）：如果用户在新建手工单中主动添加一笔费用（如运费 8 元），保存 payload 必须如实携带 8 元
    sandbox.appendFeeRow('delivery_fee', 8.0);
    const customFormData = sandbox.collectReviewFormData();
    if (customFormData.delivery_fee !== 8.0) {
        console.error("FAIL [Req 4]: 用户主动添加运费未被采集，实际值=" + customFormData.delivery_fee);
        process.exit(7);
    }
    const customPayload = sandbox.buildSavePayloadFromData(customFormData, sandbox.currentReceiptId);
    if (customPayload.delivery_fee !== 8.0) {
        console.error("FAIL [Req 4]: 用户主动添加运费在 payload 中未体现，实际值=" + customPayload.delivery_fee);
        process.exit(8);
    }

    console.log("[PASS] Requirement 1 & 4 验证通过");
    """
    code, out, err = run_node_js_harness(js_test)
    assert code == 0, f"Req 1/4 JS 验证失败：exit_code={code}\nstdout={out}\nstderr={err}"


def test_uat009_requirement_2_old_receipt_fees_preserved_db_and_ui():
    """验证需求 2：旧单据已保存六项费用原样保留，重开时不被清空或覆盖。"""
    client = TestClient(app)

    # 1. 后端接口真实保存一张含 6 项非 0 费用的单据
    res_save = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT009_旧单",
        "date": "2026-10-15",
        "total_amount": 112.5,
        "settlement_type": "cash",
        "items": [{
            "name": "旧单商品", "quantity": 1, "unit": "件",
            "unit_price": 100.0, "amount": 100.0,
        }],
        "source": "manual",
        **FEES_FIXTURE,
    })
    assert res_save.status_code == 200, res_save.text
    old_rid = res_save.json()["receipt_id"]

    # DB 读回验证旧单 6 项费用均非 0
    res_detail = client.get(f"/api/receipt/{old_rid}", headers=STAFF_A)
    assert res_detail.status_code == 200, res_detail.text
    old_data = res_detail.json()["data"]
    for k, v in FEES_FIXTURE.items():
        assert float(old_data[k]) == float(v), f"旧单 {k} 必须等于原值 {v}，实际={old_data[k]}"

    # 2. 前端验证：打开旧单 -> 新建手工单 -> 重开旧单
    js_test = f"""
    const oldReceiptData = {json.dumps(old_data)};

    // 第一次打开旧单
    sandbox.renderEditForm(oldReceiptData);
    if (elements['feesRowsContainer'].children.length !== 6) {{
        console.error("FAIL: 首次打开旧单未能渲染 6 项费用");
        process.exit(201);
    }}

    // 新建手工单（费用应清空）
    sandbox.startManualEntry();
    if (elements['feesRowsContainer'].children.length !== 0) {{
        console.error("FAIL: 新建手工单费用未清空");
        process.exit(202);
    }}

    // 重新打开旧单据
    sandbox.renderEditForm(oldReceiptData);
    if (elements['feesRowsContainer'].children.length !== 6) {{
        console.error("FAIL [Req 2]: 重新打开旧单据时，6 项费用未保留！实际行数=" + elements['feesRowsContainer'].children.length);
        process.exit(203);
    }}
    const feesReopen = sandbox.collectFeeMap();
    if (feesReopen.delivery_fee !== 5.0 || feesReopen.deposit_amount !== 4.0 ||
        feesReopen.service_fee !== 3.0 || feesReopen.tax_amount !== 2.0 ||
        feesReopen.discount_amount !== 1.0 || feesReopen.rounding_adjustment !== 0.5) {{
        console.error("FAIL [Req 2]: 重新打开旧单据费用数值被篡改：" + JSON.stringify(feesReopen));
        process.exit(204);
    }}

    console.log("[PASS] Requirement 2 验证通过");
    """
    code, out, err = run_node_js_harness(js_test)
    assert code == 0, f"Req 2 JS 验证失败：exit_code={code}\nstdout={out}\nstderr={err}"

    # 再次 DB 读回对照，确认数据库数据零改动
    res_after = client.get(f"/api/receipt/{old_rid}", headers=STAFF_A)
    assert res_after.status_code == 200
    old_data_after = res_after.json()["data"]
    for k, v in FEES_FIXTURE.items():
        assert float(old_data_after[k]) == float(v), f"旧单 DB 中 {k} 在流程后被意外改动！"


def test_uat009_requirement_3_three_paths_no_cross_session_leakage():
    """验证需求 3：三种路径（切换单据、刷新页面、关闭后重开）新单费用均从默认值开始，无跨会话泄漏。"""
    js_test = """
    const oldReceiptData = {
        id: 777,
        receipt_id: 777,
        supplier_name: '旧供应商',
        date: '2026-10-15',
        total_amount: 112.5,
        delivery_fee: 5.0,
        deposit_amount: 4.0,
        service_fee: 3.0,
        tax_amount: 2.0,
        discount_amount: 1.0,
        rounding_adjustment: 0.5,
        items: [{ name: '商品A', quantity: 1, unit: '件', unit_price: 100, amount: 100 }]
    };

    // --- 路径 1: 切换单据路径 ---
    // 打开旧单 -> 切换到新建手工单
    sandbox.renderEditForm(oldReceiptData);
    sandbox.startManualEntry();
    if (elements['feesRowsContainer'].children.length !== 0) {
        console.error("FAIL [Path 1 切换单据]: 新单费用未清空");
        process.exit(301);
    }
    let fees = sandbox.collectFeeMap();
    if (Object.values(fees).some(v => v !== 0)) {
        console.error("FAIL [Path 1 切换单据]: 新单费用存在非零值: " + JSON.stringify(fees));
        process.exit(302);
    }

    // --- 路径 2: 刷新页面路径 ---
    // 模拟刷新：DOM 元素回到初始 HTML 状态，重新进入新建手工单
    elements['feesRowsContainer'].children = [];
    elements['feesRowsContainer'].innerHTML = '';
    elements['feesDrawerContent'].classList.add('hide');
    elements['feesSummaryBadge'].classList.add('hide');
    sandbox.startManualEntry();
    if (elements['feesRowsContainer'].children.length !== 0) {
        console.error("FAIL [Path 2 刷新页面]: 刷新后新建手工单费用不为 0");
        process.exit(303);
    }
    fees = sandbox.collectFeeMap();
    if (Object.values(fees).some(v => v !== 0)) {
        console.error("FAIL [Path 2 刷新页面]: 刷新后费用存在非零值: " + JSON.stringify(fees));
        process.exit(304);
    }

    // --- 路径 3: 关闭后重开路径 ---
    // 先打开旧单，然后关闭复核区（隐藏 splitViewArea / 清理态），再次新建手工单
    sandbox.renderEditForm(oldReceiptData);
    if (elements['feesRowsContainer'].children.length !== 6) {
        console.error("FAIL [Path 3 关闭后重开]: 准备阶段旧单费用未渲染");
        process.exit(305);
    }
    // 模拟关闭复核
    elements['splitViewArea'].classList.add('hide');
    elements['prefillFormCard'].classList.add('hide');
    sandbox.currentReceiptId = null;
    sandbox.currentReceiptData = null;

    // 重新点击新建手工单
    sandbox.startManualEntry();
    if (elements['feesRowsContainer'].children.length !== 0) {
        console.error("FAIL [Path 3 关闭后重开]: 关闭后重开新单费用未清空");
        process.exit(306);
    }
    fees = sandbox.collectFeeMap();
    if (Object.values(fees).some(v => v !== 0)) {
        console.error("FAIL [Path 3 关闭后重开]: 关闭后重开新单存在非零费用: " + JSON.stringify(fees));
        process.exit(307);
    }

    console.log("[PASS] Requirement 3 三种路径实测全量通过");
    """
    code, out, err = run_node_js_harness(js_test)
    assert code == 0, f"Req 3 JS 验证失败：exit_code={code}\nstdout={out}\nstderr={err}"


def test_uat009_requirement_5_cross_tenant_isolation_and_tenant_b_unaffected():
    """验证需求 5：跨租户读写被拒绝或不可见；Tenant B baseline 前后零意外改动。"""
    client = TestClient(app)

    session = db.get_session()
    try:
        t_b_receipts_before = session.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat009_b").count()
        t_b_logs_before = session.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == "tenant_uat009_b").count()
    finally:
        session.close()

    # Tenant A 创建一张新手工单（无附加费用）
    res_a = client.post("/api/save_edited", headers=STAFF_A, json={
        "supplier_name": "供应商_UAT009_A_新单",
        "date": "2026-10-16",
        "total_amount": 10.0,
        "settlement_type": "cash",
        "items": [{
            "name": "商品隔离测试", "quantity": 1, "unit": "件",
            "unit_price": 10.0, "amount": 10.0,
        }],
        "source": "manual",
        "delivery_fee": 0.0,
        "deposit_amount": 0.0,
        "service_fee": 0.0,
        "tax_amount": 0.0,
        "discount_amount": 0.0,
        "rounding_adjustment": 0.0,
    })
    assert res_a.status_code == 200, res_a.text
    rid_a = res_a.json()["receipt_id"]

    # 1. Tenant B 读取 Tenant A 的单据 -> 404
    res_b_read = client.get(f"/api/receipt/{rid_a}", headers=OWNER_B)
    assert res_b_read.status_code == 404, f"Tenant B 越权读取应返回 404，实际={res_b_read.status_code}"

    # 2. Tenant B 修改 Tenant A 的单据 -> 404
    res_b_save = client.post("/api/save_edited", headers=STAFF_B, json={
        "receipt_id": rid_a,
        "supplier_name": "黑客供应商",
        "date": "2026-10-16",
        "total_amount": 999.0,
        "settlement_type": "cash",
        "items": [{"name": "越权篡改", "quantity": 1, "unit": "件", "unit_price": 999.0, "amount": 999.0}],
        "source": "manual",
    })
    assert res_b_save.status_code == 404, f"Tenant B 越权修改应返回 404，实际={res_b_save.status_code}"

    # 3. Tenant B 基线前后一致性校验
    session = db.get_session()
    try:
        t_b_receipts_after = session.query(db._ReceiptRow).filter(db._ReceiptRow.tenant_id == "tenant_uat009_b").count()
        t_b_logs_after = session.query(db._StockLogRow).filter(db._StockLogRow.tenant_id == "tenant_uat009_b").count()
    finally:
        session.close()

    assert t_b_receipts_after == t_b_receipts_before, "Tenant B receipts 计数前后必须完全一致"
    assert t_b_logs_after == t_b_logs_before, "Tenant B stock_logs 计数前后必须完全一致"


def test_uat009_save_payload_and_db_roundtrip_verification():
    """全链路保存 payload 原文与 DB 读回证明：新建手工单六项费用为 0 且 receipt_id=null。"""
    client = TestClient(app)

    # 模拟前端生成的新建手工单 save payload
    new_manual_payload = {
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "供应商_UAT009_Payload证明",
        "date": "2026-10-16",
        "sheet_name": "",
        "total_amount": 25.0,
        "items": [{
            "name": "手工录入蔬菜",
            "quantity": 2.5,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": 25.0,
            "actual_qty": None,
            "cost_center_id": None,
            "is_void": False,
        }],
        "settlement_type": "cash",
        "doc_form": "printed_delivery_note",
        "currency": "HKD",
        "discount_amount": 0.0,
        "delivery_fee": 0.0,
        "deposit_amount": 0.0,
        "rounding_adjustment": 0.0,
        "service_fee": 0.0,
        "tax_amount": 0.0,
        "adjustment_notes": [],
        "payment_evidence": "",
        "department_id": None,
    }

    res = client.post("/api/save_edited", headers=STAFF_A, json=new_manual_payload)
    assert res.status_code == 200, res.text
    new_rid = res.json()["receipt_id"]
    assert new_rid > 0

    # DB 读回证明
    res_saved = client.get(f"/api/receipt/{new_rid}", headers=STAFF_A)
    assert res_saved.status_code == 200, res_saved.text
    saved_data = res_saved.json()["data"]
    assert float(saved_data["discount_amount"]) == 0.0
    assert float(saved_data["delivery_fee"]) == 0.0
    assert float(saved_data["deposit_amount"]) == 0.0
    assert float(saved_data["rounding_adjustment"]) == 0.0
    assert float(saved_data["service_fee"]) == 0.0
    assert float(saved_data["tax_amount"]) == 0.0
    assert float(saved_data["total_amount"]) == 25.0
