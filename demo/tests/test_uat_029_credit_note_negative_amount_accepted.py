# -*- coding: utf-8 -*-
"""UAT-029 专项测试：选择贷项单（credit_note）允许负金额/负数量并全链路闭环。

覆盖准则与边界：
1. credit_note 两种合法变体各一（负 amount 与负 qty）：在前端 UI 保存成功、重新打开数据一致，并能走完审核入账且金额符号合规；
2. 普通 purchase 类型的负额仍必须被拒绝（不得因放宽贷项而放开采购单）；
3. 费用算术守恒：放宽符号后，明细金额、六项费用与总额的守恒关系仍然成立；
4. 已被消耗的冲销单据在冲销时仍必须返回 409 REVERSAL_STOCK_CONSUMED；
5. 严格负向边界：双负组合（负 qty 且负 unit_price）严禁当作合法；不得新增贷项类型；不得放宽其它类型校验；
6. 跨租户隔离：跨 tenant 读写被拒绝或不可见 (404)，Tenant B baseline 前后零意外改动。
"""

import json
import os
import subprocess
import sys
import pytest
from decimal import Decimal
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db
from app.main import app
from app.models import ReceiptData, ReceiptItem, DocForm
from app.services import contract
from ai_registry.tools.math_engine.v2_1_0 import validate_and_report, evaluate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat029_a"}
OWNER_A = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat029_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat029_b"}
OWNER_B = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat029_b"}


def run_node_harness(js_test_code):
    """在包含真实 DOM 模拟与 review_core.js 的 node 沙箱中运行前端测试断言。"""
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
                if (sel === 'select' || sel === 'input') {{
                    for (const c of this.children) {{
                        if (c.tagName === sel.toUpperCase()) return c;
                    }}
                }}
                return null;
            }},
            querySelectorAll(sel) {{
                if (!sel) return [];
                if (sel.startsWith('.')) {{
                    const cls = sel.slice(1);
                    return this.children.filter(c => c.classList && c.classList.contains(cls));
                }}
                if (sel === 'tr') return this.children.filter(c => c.tagName === 'TR');
                if (sel === '.fee-row') return this.children.filter(c => c.classList && c.classList.contains('fee-row'));
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
                if (sel === 'tr' && this.tagName === 'TR') return this;
                if (sel === '.fee-row' && this.classList && this.classList.contains('fee-row')) return this;
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
        'itemTableBody', 'btnSaveReview', 'btnSaveAndNext', 'reviewSaveStatusBadge',
        'issueSummaryBar', 'issueBlockerCountBadge', 'issueWarningCountBadge',
        'issueSummaryGuideText', 'aiValidationStatusBadge', 'feesDrawerContent',
        'feesToggleIcon', 'feesSummaryBadge', 'feesRowsEmpty', 'feesRowsContainer',
        'notesDrawerContent', 'notesToggleIcon', 'notesSummaryBadge', 'notesRowsEmpty',
        'notesRowsContainer', 'totalOverrideHint', 'editCard'
    ].forEach(id => mkEl(id));

    elements['inpDocForm'].tagName = 'SELECT';
    elements['inpSettlementType'].tagName = 'SELECT';
    elements['feesRowsEmpty'].classList.add('hide');

    let savedPayload = null;
    const sandbox = {{
        console,
        setTimeout, clearTimeout,
        document: {{
            getElementById(id) {{
                if (!elements[id]) mkEl(id);
                return elements[id];
            }},
            createElement(tagName) {{
                return mkEl(null, {{ tagName: tagName.toUpperCase() }});
            }},
            querySelector(sel) {{
                if (sel.startsWith('#')) return elements[sel.slice(1)] || null;
                return null;
            }},
            querySelectorAll(sel) {{
                if (sel === '#itemTableBody tr') return elements['itemTableBody'].querySelectorAll('tr');
                return [];
            }},
            addEventListener() {{}},
            documentElement: {{ style: {{ setProperty() {{}} }} }},
            body: mkEl('body'),
        }},
        localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
        track() {{}},
        showToast() {{}},
        fetch(url, opts) {{
            if (url === '/api/save_edited') {{
                try {{ savedPayload = JSON.parse(opts.body); }} catch (e) {{}}
                return Promise.resolve({{
                    status: 200,
                    ok: true,
                    json: () => Promise.resolve({{ status: 'success', receipt_id: 101, version: 1 }})
                }});
            }}
            return Promise.resolve({{
                status: 200,
                ok: true,
                json: () => Promise.resolve({{ status: 'success' }})
            }});
        }},
        apiFetch(url, opts) {{
            if (url === '/api/save_edited') {{
                try {{ savedPayload = JSON.parse(opts.body); }} catch (e) {{}}
                return Promise.resolve([200, {{ status: 'success', receipt_id: 101, version: 1 }}]);
            }}
            return Promise.resolve([200, {{ status: 'success' }}]);
        }},
        todayLocalStr() {{ return '2026-10-16'; }},
        populateDeptSelect() {{}},
        showQualityWarnings() {{}},
        setPaymentMarkBadge() {{}},
        refreshReceiptIssueSummary() {{}},
        resetQualityFailCount() {{}},
        captureResultVersion() {{}},
        getSavedPayload() {{ return savedPayload; }}
    }};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;

    vm.runInNewContext(src, sandbox, {{ filename: 'main.js' }});
    const {{ loadF4Domains }} = require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js'));
    loadF4Domains((dsrc, name) => vm.runInNewContext(dsrc, sandbox, {{ filename: name }}));

    vm.runInNewContext({json.dumps(js_test_code)}, sandbox, {{ filename: 'test_block.js' }});
    """
    proc = subprocess.run(["node", "-e", harness_code], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# =========================================================================
# 准则 1：credit_note 两种合法变体各一（负 amount 与负 qty）全链路验证
# =========================================================================
def test_01_credit_note_two_valid_variants_ui_and_backend():
    # 1.1 前端校验与保存逻辑验证 (Node 沙箱)
    js_test = """
    const results = [];

    // 变体 1: 负 amount (quantity=1, price=10, amount=-10, total=-10)
    const var1Data = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'credit_note',
        settlement_type: 'cash',
        items: [{
            name: '退货死虾',
            quantity: 1,
            unit: '斤',
            unit_price: 10,
            amount: -10,
            actual_qty: 1
        }]
    };
    const issues1 = calculateReceiptIssues(var1Data);
    const blockers1 = issues1.filter(i => i.type === 'blocker');
    results.push({ variant: 1, blockerCount: blockers1.length, blockers: blockers1 });

    // 变体 2: 负 qty (quantity=-1, price=10, amount=-10, total=-10)
    const var2Data = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'credit_note',
        settlement_type: 'cash',
        items: [{
            name: '退回多送菜心',
            quantity: -1,
            unit: '斤',
            unit_price: 10,
            amount: -10,
            actual_qty: -1
        }]
    };
    const issues2 = calculateReceiptIssues(var2Data);
    const blockers2 = issues2.filter(i => i.type === 'blocker');
    results.push({ variant: 2, blockerCount: blockers2.length, blockers: blockers2 });

    // 测试 saveReviewData 真实提交：构建 DOM 树并执行保存
    document.getElementById('inpDocForm').value = 'credit_note';
    document.getElementById('inpSupplier').value = 'Tak Lee';
    document.getElementById('inpDate').value = '2026-10-16';
    document.getElementById('inpTotal').value = '-10.00';
    document.getElementById('inpSettlementType').value = 'cash';

    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';
    const tr = document.createElement('TR');
    const n = document.createElement('INPUT'); n.className = 'inp-name'; n.value = '退货死虾'; tr.appendChild(n);
    const q = document.createElement('INPUT'); q.className = 'inp-qty'; q.value = '1'; tr.appendChild(q);
    const u = document.createElement('INPUT'); u.className = 'inp-unit'; u.value = '斤'; tr.appendChild(u);
    const p = document.createElement('INPUT'); p.className = 'inp-price'; p.value = '10'; tr.appendChild(p);
    const a = document.createElement('INPUT'); a.className = 'inp-amount'; a.value = '-10.00'; tr.appendChild(a);
    const aq = document.createElement('INPUT'); aq.className = 'inp-actual-qty'; aq.value = '1'; tr.appendChild(aq);
    tbody.appendChild(tr);

    submitSaveEdited(false);
    const payload = getSavedPayload();

    console.log(JSON.stringify({ results, payload }));
    """
    code, stdout, stderr = run_node_harness(js_test)
    assert code == 0, f"Node execution failed: {stderr}"
    data = json.loads(stdout.strip())
    assert data["results"][0]["blockerCount"] == 0, f"Variant 1 had blockers: {data['results'][0]['blockers']}"
    assert data["results"][1]["blockerCount"] == 0, f"Variant 2 had blockers: {data['results'][1]['blockers']}"
    assert data["payload"] is not None, "saveReviewData failed to dispatch save payload"
    assert data["payload"]["total_amount"] == -10.0
    assert data["payload"]["doc_form"] == "credit_note"
    assert data["payload"]["items"][0]["amount"] == -10.0

    # 1.2 后端 API /api/save_edited 与 /api/receipt/{id}/approve 全链路验证
    client = TestClient(app)
    # 先入库相同 SKU 10kg/单价10/总100 保证库存足够冲减
    init_res = client.post("/api/save_edited", json={
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "Tak Lee",
        "date": "2026-10-15",
        "total_amount": 100.0,
        "doc_form": "printed_delivery_note",
        "settlement_type": "cash",
        "currency": "HKD",
        "items": [{
            "name": "海捕死虾",
            "quantity": 10.0,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": 100.0,
            "actual_qty": 10.0
        }]
    }, headers=STAFF_A)
    assert init_res.status_code == 200
    init_rid = init_res.json()["receipt_id"]
    app_res = client.post(f"/api/receipt/{init_rid}/approve", json={"version": 1}, headers=OWNER_A)
    assert app_res.status_code == 200

    # 变体 1: 负 amount
    res1 = client.post("/api/save_edited", json={
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "Tak Lee",
        "date": "2026-10-16",
        "total_amount": -10.0,
        "doc_form": "credit_note",
        "settlement_type": "cash",
        "currency": "HKD",
        "items": [{
            "name": "海捕死虾",
            "quantity": 1.0,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": -10.0,
            "actual_qty": 1.0
        }]
    }, headers=STAFF_A)
    assert res1.status_code == 200, f"Variant 1 save failed: {res1.text}"
    rid1 = res1.json()["receipt_id"]

    # 重新读取详情校验数据一致性
    detail1 = client.get(f"/api/receipt/{rid1}", headers=STAFF_A).json()["data"]
    assert detail1["total_amount"] == -10.0
    assert detail1["doc_form"] == "credit_note"
    assert detail1["items"][0]["amount"] == -10.0

    # 审核入账
    app1 = client.post(f"/api/receipt/{rid1}/approve", json={"version": 1}, headers=OWNER_A)
    assert app1.status_code == 200, f"Variant 1 approve failed: {app1.text}"

    # 变体 2: 负 qty
    res2 = client.post("/api/save_edited", json={
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "Tak Lee",
        "date": "2026-10-16",
        "total_amount": -10.0,
        "doc_form": "credit_note",
        "settlement_type": "cash",
        "currency": "HKD",
        "items": [{
            "name": "海捕死虾",
            "quantity": -1.0,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": -10.0,
            "actual_qty": -1.0
        }]
    }, headers=STAFF_A)
    assert res2.status_code == 200, f"Variant 2 save failed: {res2.text}"
    rid2 = res2.json()["receipt_id"]

    detail2 = client.get(f"/api/receipt/{rid2}", headers=STAFF_A).json()["data"]
    assert detail2["total_amount"] == -10.0
    assert detail2["items"][0]["quantity"] == -1.0
    assert detail2["items"][0]["amount"] == -10.0

    app2 = client.post(f"/api/receipt/{rid2}/approve", json={"version": 1}, headers=OWNER_A)
    assert app2.status_code == 200, f"Variant 2 approve failed: {app2.text}"


# =========================================================================
# 准则 2：普通 purchase 类型的负额仍必须被拒绝
# =========================================================================
def test_02_purchase_negative_amount_rejected():
    # 2.1 前端 UI 必须阻断普通采购单的负额
    js_test = """
    const purchData = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'printed_delivery_note',
        settlement_type: 'cash',
        items: [{
            name: '普通采购品',
            quantity: 1,
            unit: '件',
            unit_price: 10,
            amount: -10
        }]
    };
    const issues = calculateReceiptIssues(purchData);
    const totalBlocker = issues.find(i => i.id === 'err_total');

    // 尝试在 UI 中保存普通采购单负额
    document.getElementById('inpDocForm').value = 'printed_delivery_note';
    document.getElementById('inpSupplier').value = 'Tak Lee';
    document.getElementById('inpDate').value = '2026-10-16';
    document.getElementById('inpTotal').value = '-10.00';
    document.getElementById('inpSettlementType').value = 'cash';

    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';
    const tr = document.createElement('TR');
    const n = document.createElement('INPUT'); n.className = 'inp-name'; n.value = '普通采购品'; tr.appendChild(n);
    const q = document.createElement('INPUT'); q.className = 'inp-qty'; q.value = '1'; tr.appendChild(q);
    const u = document.createElement('INPUT'); u.className = 'inp-unit'; u.value = '件'; tr.appendChild(u);
    const p = document.createElement('INPUT'); p.className = 'inp-price'; p.value = '10'; tr.appendChild(p);
    const a = document.createElement('INPUT'); a.className = 'inp-amount'; a.value = '-10.00'; tr.appendChild(a);
    tbody.appendChild(tr);

    submitSaveEdited(false);
    const payload = getSavedPayload();

    console.log(JSON.stringify({ totalBlocker, payloadDispatched: payload !== null }));
    """
    code, stdout, stderr = run_node_harness(js_test)
    assert code == 0, f"Node execution failed: {stderr}"
    data = json.loads(stdout.strip())
    assert data["totalBlocker"] is not None, "Ordinary purchase with negative total was not blocked by calculateReceiptIssues"
    assert "总金额必须大于 0" in data["totalBlocker"]["title"]
    assert data["payloadDispatched"] is False, "Ordinary purchase with negative total should NOT dispatch save payload"

    # 2.2 后端契约层校验普通采购单负总额
    purch_model = ReceiptData(
        doc_form=DocForm.PRINTED,
        vendor="Tak Lee",
        date="2026-10-16",
        items=[ReceiptItem(name="商品A", qty=1, unit="件", unit_price=10, amount=-10)],
        total=-10.0,
        payment_marked=False,
        confidence=0.9
    )
    _, err = contract.validate_contract(purch_model.model_dump())
    assert err is not None and "非退款/更正单据总额不能为负数" in err, f"Expected rejection but got: {err}"


# =========================================================================
# 准则 3：费用算术匹配（放宽符号后，六项费用与总额守恒关系仍然成立）
# =========================================================================
def test_03_fee_arithmetic_conservation():
    js_test = """
    // 明细 itemsSum = -20.0
    // 附加费用：delivery_fee=2.0, tax_amount=1.0, discount_amount=1.0
    // 期望守恒公式：itemsSum - discount - rounding + deposit + delivery + service + tax
    // = -20.0 - 1.0 + 2.0 + 1.0 = -18.0
    const correctData = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -18.0,
        doc_form: 'credit_note',
        settlement_type: 'cash',
        delivery_fee: 2.0,
        tax_amount: 1.0,
        discount_amount: 1.0,
        items: [{
            name: '退货死虾',
            quantity: 2,
            unit: '斤',
            unit_price: 10,
            amount: -20,
            actual_qty: 2
        }]
    };
    const issuesCorrect = calculateReceiptIssues(correctData);
    const mathBlockerCorrect = issuesCorrect.find(i => i.id === 'err_grand_total_math');

    // 错误总额：写成 -15.0，相差 3.0 元
    const wrongData = Object.assign({}, correctData, { total_amount: -15.0 });
    const issuesWrong = calculateReceiptIssues(wrongData);
    const mathBlockerWrong = issuesWrong.find(i => i.id === 'err_grand_total_math');

    console.log(JSON.stringify({
        hasCorrectMathBlocker: mathBlockerCorrect != null,
        hasWrongMathBlocker: mathBlockerWrong != null,
        wrongGuide: mathBlockerWrong ? mathBlockerWrong.guide : ''
    }));
    """
    code, stdout, stderr = run_node_harness(js_test)
    assert code == 0, f"Node execution failed: {stderr}"
    data = json.loads(stdout.strip())
    assert data["hasCorrectMathBlocker"] is False, "Correct fee arithmetic should not trigger math blocker"
    assert data["hasWrongMathBlocker"] is True, "Mismatched fee arithmetic must trigger math blocker"
    assert "不符" in data["wrongGuide"]

    # 后端 math_engine 计算结果对齐
    d_backend = ReceiptData(
        doc_form=DocForm.CREDIT,
        vendor="Tak Lee",
        date="2026-10-16",
        delivery_fee=2.0,
        tax_amount=1.0,
        discount_amount=1.0,
        items=[ReceiptItem(name="退货死虾", qty=2, unit="斤", unit_price=10, amount=-20)],
        total=-18.0,
        payment_marked=False,
        confidence=0.9
    )
    assert validate_and_report(d_backend) == [], "math_engine should accept matching negative fee arithmetic"

    d_backend_wrong = ReceiptData(
        doc_form=DocForm.CREDIT,
        vendor="Tak Lee",
        date="2026-10-16",
        delivery_fee=2.0,
        tax_amount=1.0,
        discount_amount=1.0,
        items=[ReceiptItem(name="退货死虾", qty=2, unit="斤", unit_price=10, amount=-20)],
        total=-15.0,
        payment_marked=False,
        confidence=0.9
    )
    issues = validate_and_report(d_backend_wrong)
    assert len(issues) > 0 and any("总额对不上" in iss for iss in issues)


# =========================================================================
# 准则 4：已被消耗的冲销单据在冲销时仍必须返回 409
# =========================================================================
def test_04_consumed_reversal_still_returns_409():
    client = TestClient(app)
    # 创建一张正常入库单并 approve
    res = client.post("/api/save_edited", json={
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "Tak Lee",
        "date": "2026-10-16",
        "total_amount": 50.0,
        "doc_form": "printed_delivery_note",
        "settlement_type": "cash",
        "currency": "HKD",
        "items": [{
            "name": "待冲销消耗食材",
            "quantity": 5.0,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": 50.0,
            "actual_qty": 5.0
        }]
    }, headers=STAFF_A)
    assert res.status_code == 200
    rid = res.json()["receipt_id"]

    app_res = client.post(f"/api/receipt/{rid}/approve", json={"version": 1}, headers=OWNER_A)
    assert app_res.status_code == 200

    # 模拟库存批次被消耗（remaining_qty 减至 2，消耗了 3）
    s = db.get_session()
    batch = s.query(db._InventoryBatchRow).filter(
        db._InventoryBatchRow.receipt_id == rid
    ).first()
    assert batch is not None
    batch.remaining_qty = 2.0
    s.commit()

    # 尝试冲销已被消耗的单据 -> 必须返回 409
    rev_res = client.post(f"/api/receipt/{rid}/reverse", json={
        "version": 2,
        "reason": "录错品名测试冲销"
    }, headers=OWNER_A)
    assert rev_res.status_code == 409, f"Expected 409 but got {rev_res.status_code}: {rev_res.text}"
    rev_json = rev_res.json()
    assert rev_json["code"] == "REVERSAL_STOCK_CONSUMED"


# =========================================================================
# 准则 5：严格负向边界（双负组合、零数量、未知贷项类型）
# =========================================================================
def test_05_negative_boundary_double_negative_and_invalid_forms():
    js_test = """
    // 组合 1：负 qty 且负 unit_price（双负非法组合）
    const doubleNegData = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'credit_note',
        settlement_type: 'cash',
        items: [{
            name: '双负死虾',
            quantity: -1,
            unit: '斤',
            unit_price: -10,
            amount: -10
        }]
    };
    const issuesDoubleNeg = calculateReceiptIssues(doubleNegData);
    const doubleNegBlocker = issuesDoubleNeg.find(i => i.id === 'err_item_num_0');

    // 组合 2：零数量 (qty == 0 非法)
    const zeroQtyData = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'credit_note',
        settlement_type: 'cash',
        items: [{
            name: '零数量死虾',
            quantity: 0,
            unit: '斤',
            unit_price: 10,
            amount: -10
        }]
    };
    const issuesZeroQty = calculateReceiptIssues(zeroQtyData);
    const zeroQtyBlocker = issuesZeroQty.find(i => i.id === 'err_item_num_0');

    // 组合 3：未知/新增贷项类型 (如 custom_credit_note 或 manual_entry 负额)
    const invalidDocData = {
        supplier_name: 'Tak Lee',
        date: '2026-10-16',
        total_amount: -10,
        doc_form: 'custom_credit_note',
        settlement_type: 'cash',
        items: [{
            name: '未识别贷项',
            quantity: 1,
            unit: '斤',
            unit_price: 10,
            amount: -10
        }]
    };
    const issuesInvalidDoc = calculateReceiptIssues(invalidDocData);
    const invalidDocBlocker = issuesInvalidDoc.find(i => i.id === 'err_total');

    console.log(JSON.stringify({
        hasDoubleNegBlocker: doubleNegBlocker != null,
        hasZeroQtyBlocker: zeroQtyBlocker != null,
        hasInvalidDocBlocker: invalidDocBlocker != null
    }));
    """
    code, stdout, stderr = run_node_harness(js_test)
    assert code == 0, f"Node execution failed: {stderr}"
    data = json.loads(stdout.strip())
    assert data["hasDoubleNegBlocker"] is True, "Double negative (qty < 0 and unit_price < 0) must be blocked as invalid"
    assert data["hasZeroQtyBlocker"] is True, "Zero quantity must be blocked as invalid"
    assert data["hasInvalidDocBlocker"] is True, "Unrecognized credit doc_form with negative total must be blocked"


# =========================================================================
# 准则 6：跨租户隔离与 Tenant B baseline 零污染
# =========================================================================
def test_06_cross_tenant_isolation_and_tenant_b_unaffected():
    client = TestClient(app)
    # 统计 Tenant B 测试前基线
    tenant_b_receipts_before = len(client.get("/api/receipts", headers=STAFF_B).json().get("data", []))

    # Tenant A 创建一张贷项单
    res_a = client.post("/api/save_edited", json={
        "receipt_id": None,
        "source": "manual",
        "supplier_name": "Tak Lee",
        "date": "2026-10-16",
        "total_amount": -10.0,
        "doc_form": "credit_note",
        "settlement_type": "cash",
        "currency": "HKD",
        "items": [{
            "name": "租户A退货",
            "quantity": 1.0,
            "unit": "斤",
            "unit_price": 10.0,
            "amount": -10.0,
            "actual_qty": 1.0
        }]
    }, headers=STAFF_A)
    assert res_a.status_code == 200
    rid_a = res_a.json()["receipt_id"]

    # Tenant B 尝试越权读取 Tenant A 的贷项单 -> 必须 404
    cross_read = client.get(f"/api/receipt/{rid_a}", headers=STAFF_B)
    assert cross_read.status_code == 404, f"Cross-tenant access should be 404, got {cross_read.status_code}"

    # Tenant B 测试后基线核验
    tenant_b_receipts_after = len(client.get("/api/receipts", headers=STAFF_B).json().get("data", []))
    assert tenant_b_receipts_before == tenant_b_receipts_after, "Tenant B receipts baseline was altered!"
