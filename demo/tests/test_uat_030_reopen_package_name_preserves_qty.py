# -*- coding: utf-8 -*-
"""UAT-030 专项测试：重新打开带包装品名单据保留数量与品名。

权威定义：
- /Users/ethan/harness-tmp/receipt-grok-takeover-20261015/01-acceptance-checklist.md:358-375 [UAT-030]
- S2-01 权威定义：
  - docs/review-2026-10/S2-ai-pipeline.md:52
  - demo/app/chains/extract_chain.py:1025-1055
  - demo/tests/test_review_s2_01.py:39-81
  - /Users/ethan/harness-tmp/receipt-full-uat-20261014/original-id-semantic-review.json:3140-3156

测试范围：
1. 核心正向全链路：name=Synthetic_Oil 5L*2、quantity=1、unit=箱 经过
   保存 -> 刷新 -> 重开两次 -> 不编辑再保存，覆盖五个观测点，每层 name 与 quantity 精确不变；
2. quantity != 1 回归原 S2-01 保护分支；
3. 缺 qty 的包装品名提示独立测试；
4. 包装提示可见性与完整性；
5. 跨 tenant 隔离与 Tenant B baseline 零改动。
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
from app.chains import extract_chain, supervisor

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat030_a"}
OWNER_A = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat030_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat030_b"}
OWNER_B = {"X-Role": "owner", "X-Tenant-Id": "tenant_uat030_b"}


def run_node_harness(js_test_code):
    """在包含真实 DOM 模拟与 review_core.js / archive_list.js 的 node 沙箱中运行前端测试。"""
    harness_code = f"""
    const fs = require('fs');
    const path = require('path');
    const vm = require('vm');

    const REPO_ROOT = {json.dumps(REPO_ROOT)};
    const mainPath = path.join(REPO_ROOT, 'demo', 'static', 'js', 'main.js');
    const src = fs.readFileSync(mainPath, 'utf8');

    const elements = {{}};

    function parseHtmlNodes(html, parentNode) {{
        const nodes = [];
        if (!html || typeof html !== 'string') return nodes;

        // 匹配 tr 标签
        const trs = html.match(/<tr[^>]*>[\\s\\S]*?<\\/tr>/gi);
        if (trs) {{
            for (const tHtml of trs) {{
                const trEl = mkEl(null, {{ tagName: 'TR' }});
                trEl.parentNode = parentNode;
                const inner = tHtml.replace(/^<tr[^>]*>|<\\/tr>$/gi, '');
                trEl.children = parseHtmlNodes(inner, trEl);
                nodes.push(trEl);
            }}
            return nodes;
        }}

        // 匹配 td 标签
        const tds = html.match(/<td[^>]*>[\\s\\S]*?<\\/td>/gi);
        if (tds) {{
            for (const dHtml of tds) {{
                const tdEl = mkEl(null, {{ tagName: 'TD' }});
                tdEl.parentNode = parentNode;
                const inner = dHtml.replace(/^<td[^>]*>|<\\/td>$/gi, '');
                tdEl.children = parseHtmlNodes(inner, tdEl);
                nodes.push(tdEl);
            }}
            return nodes;
        }}

        // 匹配 input 标签
        const inputs = html.match(/<input[^>]+>/gi) || [];
        for (const inp of inputs) {{
            const valMatch = inp.match(/value="([^"]*)"/i);
            const clsMatch = inp.match(/class="([^"]*)"/i);
            const titleMatch = inp.match(/title="([^"]*)"/i);
            const typeMatch = inp.match(/type="([^"]*)"/i);
            const inpEl = mkEl(null, {{
                tagName: 'INPUT',
                value: valMatch ? valMatch[1] : '',
                className: clsMatch ? clsMatch[1] : '',
                title: titleMatch ? titleMatch[1] : '',
                type: typeMatch ? typeMatch[1] : 'text'
            }});
            inpEl.parentNode = parentNode;
            nodes.push(inpEl);
        }}

        // 匹配 span 标签
        const spans = html.match(/<span[^>]*>[\\s\\S]*?<\\/span>/gi) || [];
        for (const span of spans) {{
            const clsMatch = span.match(/class="([^"]*)"/i);
            const titleMatch = span.match(/title="([^"]*)"/i);
            const textMatch = span.match(/>([^<]*)<\\/span>/i);
            const spanEl = mkEl(null, {{
                tagName: 'SPAN',
                className: clsMatch ? clsMatch[1] : '',
                title: titleMatch ? titleMatch[1] : '',
                textContent: textMatch ? textMatch[1] : ''
            }});
            spanEl.parentNode = parentNode;
            nodes.push(spanEl);
        }}

        // 匹配 select 标签
        const selects = html.match(/<select[^>]*>[\\s\\S]*?<\\/select>/gi) || [];
        for (const sel of selects) {{
            const clsMatch = sel.match(/class="([^"]*)"/i);
            const selEl = mkEl(null, {{
                tagName: 'SELECT',
                className: clsMatch ? clsMatch[1] : ''
            }});
            selEl.parentNode = parentNode;
            nodes.push(selEl);
        }}

        return nodes;
    }}

    function mkEl(id, props) {{
        const el = Object.assign({{
            id: id || '',
            value: '', tagName: 'DIV', textContent: '', style: {{}}, dataset: {{}},
            children: [],
            set innerHTML(val) {{
                this._innerHTML = val;
                this.children = [];
                if (!val) return;
                const parsed = parseHtmlNodes(val, this);
                this.children = parsed;
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
                const search = (node) => {{
                    for (const c of node.children) {{
                        if (sel.startsWith('.')) {{
                            const cls = sel.slice(1);
                            if (c.classList && c.classList.contains(cls)) return c;
                        }} else if (sel.startsWith('#')) {{
                            if (c.id === sel.slice(1)) return c;
                        }} else if (c.tagName === sel.toUpperCase()) {{
                            return c;
                        }}
                        const found = search(c);
                        if (found) return found;
                    }}
                    return null;
                }};
                return search(this);
            }},
            querySelectorAll(sel) {{
                const res = [];
                const search = (node) => {{
                    for (const c of node.children) {{
                        if (sel.startsWith('.')) {{
                            const cls = sel.slice(1);
                            if (c.classList && c.classList.contains(cls)) res.push(c);
                        }} else if (sel.startsWith('#')) {{
                            if (c.id === sel.slice(1)) res.push(c);
                        }} else if (c.tagName === sel.toUpperCase()) {{
                            res.push(c);
                        }}
                        search(c);
                    }}
                }};
                search(this);
                return res;
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
                if (sel === 'td' && this.tagName === 'TD') return this;
                if (this.parentNode) return this.parentNode.closest(sel);
                return null;
            }},
            addEventListener() {{}},
            setAttribute(k, v) {{ this[k] = v; }},
            getAttribute(k) {{ return this[k]; }},
            focus() {{}},
            select() {{}},
            scrollIntoView() {{}}
        }}, props || {{}});
        if (id) elements[id] = el;
        return el;
    }}

    [
        'inpSupplier', 'inpDate', 'inpSheet', 'inpTotal', 'inpSettlementType',
        'inpDepartmentId', 'inpCurrency', 'inpDocForm', 'inpPaymentMark',
        'itemTableBody', 'btnSaveReview', 'btnSaveAndNext', 'reviewSaveStatusBadge',
        'arcSupplier', 'arcDate', 'arcSheet', 'arcTotal', 'arcSettlementType',
        'arcDepartmentId', 'arcCurrency', 'arcPaymentMark', 'arcTableBody',
        'btnSaveArchive', 'archiveDetailModal', 'alertBanner', 'archiveMathWarnBanner',
        'prefillFormCard', 'splitViewArea', 'imgViewerContainer', 'imgToolbar',
        'leftCardTitleActions', 'previewImg', 'manualEntryNoImage'
    ].forEach(id => mkEl(id));

    elements['inpDocForm'].tagName = 'SELECT';
    elements['inpSettlementType'].tagName = 'SELECT';

    let lastSavedPayload = null;
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
                if (sel === '#arcTableBody tr') return elements['arcTableBody'].querySelectorAll('tr');
                return [];
            }},
            addEventListener() {{}},
            documentElement: {{ style: {{ setProperty() {{}} }} }},
            body: mkEl('body'),
        }},
        localStorage: {{ getItem: () => null, setItem: () => {{}}, removeItem: () => {{}} }},
        track() {{}},
        showToast() {{}},
        apiFetch(url, opts) {{
            if (url === '/api/save_edited') {{
                try {{ lastSavedPayload = JSON.parse(opts.body); }} catch (e) {{}}
                return Promise.resolve([200, {{ status: 'success', receipt_id: 201, version: 1 }}]);
            }}
            return Promise.resolve([200, {{ status: 'success' }}]);
        }},
        todayLocalStr() {{ return '2026-10-16'; }},
        populateDeptSelect() {{}},
        showQualityWarnings() {{}},
        showImageGuardNotice() {{}},
        renderCurrencySymbol() {{}},
        refreshReceiptIssueSummary() {{}},
        refreshAllVendorPriceHints() {{}},
        resetQualityFailCount() {{}},
        getLastSavedPayload() {{ return lastSavedPayload; }}
    }};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;

    vm.runInNewContext(src, sandbox, {{ filename: 'main.js' }});
    const {{ loadF4Domains }} = require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js'));
    loadF4Domains((dsrc, name) => vm.runInNewContext(dsrc, sandbox, {{ filename: name }}));

    vm.runInNewContext({json.dumps(js_test_code)}, sandbox, {{ filename: 'test_uat030_block.js' }});
    """
    proc = subprocess.run(["node", "-e", harness_code], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


# =========================================================================
# 准则 1：五个观测点（保存后、刷新后、第一次重开、第二次重开、再保存后）
#        name=Synthetic_Oil 5L*2, quantity=1, unit=箱 每一层精确不变
# =========================================================================
def test_01_reopen_five_observation_points_name_and_qty_preserved():
    client = TestClient(app)

    # 准备阶段：用 Tenant A 发起保存请求
    initial_payload = {
        "source": "manual",
        "supplier_name": "Vendor_A",
        "date": "2026-10-16",
        "sheet_name": "2026-10",
        "total_amount": 100.0,
        "items": [
            {
                "name": "Synthetic_Oil 5L*2",
                "quantity": 1.0,
                "unit": "箱",
                "unit_price": 100.0,
                "amount": 100.0,
                "actual_qty": None,
                "cost_center_id": None,
                "is_void": False,
            }
        ],
        "settlement_type": "credit",
        "doc_form": "printed_delivery_note",
        "currency": "HKD",
    }

    # 观测点 1：保存后 (Point 1: After save)
    resp_save1 = client.post("/api/save_edited", json=initial_payload, headers=STAFF_A)
    assert resp_save1.status_code == 200, f"Save edited failed: {resp_save1.text}"
    receipt_id = resp_save1.json().get("receipt_id")
    assert receipt_id is not None, "Missing receipt_id"

    # DB 直接读回 (Point 1 DB read back)
    items1 = db.get_receipt_items(receipt_id, tenant_id="tenant_uat030_a")
    assert len(items1) == 1, "Expected 1 item in DB"
    assert items1[0]["name"] == "Synthetic_Oil 5L*2", f"Point 1 DB name: {items1[0]['name']}"
    assert float(items1[0]["quantity"]) == 1.0, f"Point 1 DB qty: {items1[0]['quantity']}"

    # 观测点 2：刷新后 (Point 2: After refresh)
    resp_get2 = client.get(f"/api/receipt/{receipt_id}", headers=STAFF_A)
    assert resp_get2.status_code == 200, f"Get receipt failed: {resp_get2.text}"
    data2 = resp_get2.json().get("data")
    assert data2 is not None
    assert len(data2.get("items", [])) == 1
    item2 = data2["items"][0]
    assert item2["name"] == "Synthetic_Oil 5L*2", f"Point 2 API name: {item2['name']}"
    assert float(item2["quantity"]) == 1.0, f"Point 2 API qty: {item2['quantity']}"

    # 观测点 3 & 4：前端重开两次 (Point 3: First reopen, Point 4: Second reopen)
    # 通过 Node 沙箱加载 review_core.js 与 archive_list.js，测试 appendTableRow 与 appendArcTableRow
    js_reopen_test = f"""
    const testData = {json.dumps(data2)};
    const tbody = document.getElementById('itemTableBody');

    // 第一次重开 (Point 3)
    tbody.innerHTML = '';
    renderEditForm(testData);
    const tr1 = tbody.querySelector('tr');
    if (!tr1) throw new Error("Point 3: No table row rendered");
    const nameInp1 = tr1.querySelector('.inp-name');
    const qtyInp1 = tr1.querySelector('.inp-qty');
    if (!nameInp1 || !qtyInp1) throw new Error("Point 3: Missing inp-name or inp-qty");

    const p3_name = nameInp1.value;
    const p3_qty = qtyInp1.value;

    if (p3_name !== "Synthetic_Oil 5L*2") {{
        throw new Error("FAIL [Point 3 第一次重开]: inp-name expected 'Synthetic_Oil 5L*2' but got '" + p3_name + "'");
    }}
    if (parseFloat(p3_qty) !== 1.0) {{
        throw new Error("FAIL [Point 3 第一次重开]: inp-qty expected 1.0 but got '" + p3_qty + "'");
    }}

    // 第二次重开 (Point 4)
    tbody.innerHTML = '';
    renderEditForm(testData);
    const tr2 = tbody.querySelector('tr');
    if (!tr2) throw new Error("Point 4: No table row rendered");
    const nameInp2 = tr2.querySelector('.inp-name');
    const qtyInp2 = tr2.querySelector('.inp-qty');

    const p4_name = nameInp2.value;
    const p4_qty = qtyInp2.value;

    if (p4_name !== "Synthetic_Oil 5L*2") {{
        throw new Error("FAIL [Point 4 第二次重开]: inp-name expected 'Synthetic_Oil 5L*2' but got '" + p4_name + "'");
    }}
    if (parseFloat(p4_qty) !== 1.0) {{
        throw new Error("FAIL [Point 4 第二次重开]: inp-qty expected 1.0 but got '" + p4_qty + "'");
    }}

    // 在归档弹窗中测试重开 (archive_list.js)
    const arcTbody = document.getElementById('arcTableBody');
    arcTbody.innerHTML = '';
    renderArchiveForm(testData);
    const arcTr = arcTbody.querySelector('tr');
    if (!arcTr) throw new Error("Point 3/4 Archive: No archive table row rendered");
    const arcNameInp = arcTr.querySelector('.inp-name');
    const arcQtyInp = arcTr.querySelector('.inp-qty');
    if (arcNameInp.value !== "Synthetic_Oil 5L*2") {{
        throw new Error("FAIL [Archive Reopen]: inp-name expected 'Synthetic_Oil 5L*2' but got '" + arcNameInp.value + "'");
    }}
    if (parseFloat(arcQtyInp.value) !== 1.0) {{
        throw new Error("FAIL [Archive Reopen]: inp-qty expected 1.0 but got '" + arcQtyInp.value + "'");
    }}

    // 观测点 5 前端采集：无编辑再保存 (Point 5 collect and submit)
    const collectedData = collectReviewFormData();
    if (!collectedData || !collectedData.items || collectedData.items.length === 0) {{
        throw new Error("Point 5: collectReviewFormData returned empty");
    }}
    const p5_collected_item = collectedData.items[0];
    if (p5_collected_item.name !== "Synthetic_Oil 5L*2") {{
        throw new Error("FAIL [Point 5 采集]: collected name expected 'Synthetic_Oil 5L*2' but got '" + p5_collected_item.name + "'");
    }}
    if (parseFloat(p5_collected_item.quantity) !== 1.0) {{
        throw new Error("FAIL [Point 5 采集]: collected qty expected 1.0 but got " + p5_collected_item.quantity);
    }}

    console.log(JSON.stringify({{
        point3: {{ name: p3_name, qty: p3_qty }},
        point4: {{ name: p4_name, qty: p4_qty }},
        point5_collected: {{ name: p5_collected_item.name, qty: p5_collected_item.quantity }}
    }}));
    """
    code, out, err = run_node_harness(js_reopen_test)
    assert code == 0, f"Frontend reopen check failed (code={code}):\nSTDOUT: {out}\nSTDERR: {err}"

    # 观测点 5：无编辑再保存并进行 DB 读回
    save5_payload = dict(data2)
    save5_payload["receipt_id"] = receipt_id
    save5_payload["source"] = "manual"
    resp_save5 = client.post("/api/save_edited", json=save5_payload, headers=STAFF_A)
    assert resp_save5.status_code == 200, f"Point 5 Save again failed: {resp_save5.text}"

    items5 = db.get_receipt_items(receipt_id, tenant_id="tenant_uat030_a")
    assert len(items5) == 1
    assert items5[0]["name"] == "Synthetic_Oil 5L*2", f"Point 5 DB name: {items5[0]['name']}"
    assert float(items5[0]["quantity"]) == 1.0, f"Point 5 DB qty: {items5[0]['quantity']}"


# =========================================================================
# 准则 2：quantity != 1 回归原 S2-01 保护分支
#        权威行：demo/app/chains/extract_chain.py:1025-1051
#                demo/tests/test_review_s2_01.py:39-46
# =========================================================================
def test_02_quantity_not_one_preserves_s2_01_protection():
    # 2.1 后端 S2-01 核心保护分支断言：qty=3 不被包装个数 2 覆盖
    item = {"name": "Synthetic_Oil 5L*2", "qty": 3, "unit": "箱", "unit_price": 100, "amount": 300}
    raw = {
        "doc_form": "printed_delivery_note",
        "vendor": "供应商甲",
        "date": "2026-10-16",
        "items": [item],
        "total": 300,
        "payment_marked": False,
        "confidence": 0.9,
    }
    data, err = extract_chain._parse_to_receipt(raw)
    assert err is None, err
    assert data.items[0].qty == 3.0, f"S2-01 branch violated: qty={data.items[0].qty}"
    assert any("数量仍按识别到的 3" in w for w in data.field_warnings), (
        f"Expected S2-01 warning not found in: {data.field_warnings}"
    )
    assert all("已按" not in w and "按 2 处理" not in w for w in data.field_warnings)

    # 2.2 前端重开渲染时 quantity=3 必须完整保留为 3，品名完整保留
    js_qty3_test = """
    const testData = {
        supplier_name: 'Vendor_A',
        date: '2026-10-16',
        total_amount: 300.0,
        items: [{
            name: 'Synthetic_Oil 5L*2',
            quantity: 3.0,
            unit: '箱',
            unit_price: 100.0,
            amount: 300.0
        }]
    };
    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';
    renderEditForm(testData);
    const tr = tbody.querySelector('tr');
    if (!tr) throw new Error("No table row rendered for qty=3");
    const nameInp = tr.querySelector('.inp-name');
    const qtyInp = tr.querySelector('.inp-qty');
    if (!nameInp || !qtyInp) throw new Error("Missing inp-name or inp-qty for qty=3");

    const nameVal = nameInp.value;
    const qtyVal = qtyInp.value;

    if (nameVal !== "Synthetic_Oil 5L*2") {
        throw new Error("S2-01 UI FAIL: name expected 'Synthetic_Oil 5L*2' but got '" + nameVal + "'");
    }
    if (parseFloat(qtyVal) !== 3.0) {
        throw new Error("S2-01 UI FAIL: qty expected 3.0 but got '" + qtyVal + "'");
    }
    console.log("S2-01 UI PASS: name=" + nameVal + ", qty=" + qtyVal);
    """
    code, out, err = run_node_harness(js_qty3_test)
    assert code == 0, f"S2-01 quantity!=1 frontend check failed:\nSTDOUT: {out}\nSTDERR: {err}"


# =========================================================================
# 准则 3：缺 qty 的包装品名提示必须独立可测
#        触发条件：品名含包装规格，但数量未提供（None 或缺失）
#        预期提示：f"「{_label}」的品名里写着 {_pack_txt}{_unit_txt}，数量没有识别到，没有按包装个数填写。请对照原图自己填写数量。"
#        实测结果：qty 保持 0.0，不被包装个数填充
# =========================================================================
def test_03_missing_qty_packaging_prompt_independently_testable():
    # 触发条件：qty 键不存在
    absent = {"name": "Synthetic_Oil 5L*2", "unit": "箱", "unit_price": 100, "amount": 200}
    raw = {
        "doc_form": "printed_delivery_note",
        "vendor": "供应商甲",
        "date": "2026-10-16",
        "items": [absent],
        "total": 200,
        "payment_marked": False,
        "confidence": 0.9,
    }
    data, err = extract_chain._parse_to_receipt(raw)
    assert err is None, err
    assert data.items[0].qty == 0.0, f"Missing qty should default to 0.0, got {data.items[0].qty}"
    assert data.items[0].qty != 2.0, "Missing qty must not be filled from packaging count 2"

    expected_missing_warning = "数量没有识别到，没有按包装个数填写。请对照原图自己填写数量。"
    assert any(expected_missing_warning in w for w in data.field_warnings), (
        f"Missing qty warning not found in: {data.field_warnings}"
    )

    # 触发条件：qty 显式传 None
    explicit_none = {"name": "Synthetic_Oil 5L*2", "qty": None, "unit": "箱", "unit_price": 100, "amount": 200}
    raw_none = dict(raw, items=[explicit_none])
    data_none, err_none = extract_chain._parse_to_receipt(raw_none)
    assert err_none is None
    assert data_none.items[0].qty == 0.0
    assert any(expected_missing_warning in w for w in data_none.field_warnings)


# =========================================================================
# 准则 4：不得关闭所有包装提示，包装提示可见性与完整性验证
# =========================================================================
def test_04_packaging_hints_remain_active_and_visible():
    # 4.1 后端：S2-01 提示文案在正常带包装品名识别时正常给出
    item = {"name": "Synthetic_Oil 5L*2", "qty": 1, "unit": "箱", "unit_price": 100, "amount": 100}
    raw = {
        "doc_form": "printed_delivery_note",
        "vendor": "供应商甲",
        "date": "2026-10-16",
        "items": [item],
        "total": 100,
        "payment_marked": False,
        "confidence": 0.9,
    }
    data, err = extract_chain._parse_to_receipt(raw)
    assert err is None
    assert any("没有自动改。请对照原图，要改请自己填写" in w for w in data.field_warnings), (
        "Backend packaging warning must not be disabled"
    )

    # 4.2 前端：明细行核验标志 itemReviewFlags 包含包装提示
    js_hint_test = """
    const item = {
        name: 'Synthetic_Oil 5L*2',
        quantity: 1.0,
        unit: '箱',
        unit_price: 100.0,
        amount: 100.0
    };
    const flags = itemReviewFlags(item);
    const hasPkgFlag = flags && flags.some(f => f.label === '包装待核验' || /包装/.test(f.reason));
    if (!hasPkgFlag) {
        throw new Error("FAIL: itemReviewFlags does not provide packaging review flag for 'Synthetic_Oil 5L*2'");
    }

    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';
    renderEditForm({
        supplier_name: 'Vendor_A',
        date: '2026-10-16',
        total_amount: 100.0,
        items: [item]
    });
    const tr = tbody.querySelector('tr');
    if (!tr) throw new Error("No table row rendered");
    const hintBadge = tr.querySelector('.packaging-hint') || tr.querySelector('.item-conf-badge');
    if (!hintBadge) {
        throw new Error("FAIL: No visible packaging hint badge rendered in row");
    }
    console.log("PASS: Packaging hint visible with text: " + hintBadge.textContent);
    """
    code, out, err = run_node_harness(js_hint_test)
    assert code == 0, f"Packaging hint visibility test failed:\nSTDOUT: {out}\nSTDERR: {err}"


# =========================================================================
# 准则 5：跨 tenant 读写隔离与 Tenant B baseline 零意外改动
# =========================================================================
def test_05_cross_tenant_isolation_and_tenant_b_zero_mutation():
    client = TestClient(app)

    # 获取 Tenant B 当前基线
    tenant_b_receipts_before = len(client.get("/api/receipts", headers=STAFF_B).json().get("data", []))
    with db.get_session(tenant_id="tenant_uat030_b") as session:
        tenant_b_db_before = len(db.list_receipt_rows(tenant_id="tenant_uat030_b"))

    # Tenant A 创建一张单据
    payload_a = {
        "source": "manual",
        "supplier_name": "Vendor_A",
        "date": "2026-10-16",
        "sheet_name": "2026-10",
        "total_amount": 100.0,
        "items": [
            {
                "name": "Synthetic_Oil 5L*2",
                "quantity": 1.0,
                "unit": "箱",
                "unit_price": 100.0,
                "amount": 100.0,
            }
        ],
        "settlement_type": "credit",
        "doc_form": "printed_delivery_note",
        "currency": "HKD",
    }
    res_a = client.post("/api/save_edited", json=payload_a, headers=STAFF_A)
    assert res_a.status_code == 200
    rid_a = res_a.json()["receipt_id"]

    # Tenant B 跨租户读取尝试 -> 必须 404
    res_b_get = client.get(f"/api/receipt/{rid_a}", headers=STAFF_B)
    assert res_b_get.status_code == 404, f"Cross-tenant read expected 404, got {res_b_get.status_code}"

    # Tenant B 跨租户修改尝试 -> 必须 404
    payload_b_edit = dict(payload_a, receipt_id=rid_a, total_amount=200.0)
    res_b_edit = client.post("/api/save_edited", json=payload_b_edit, headers=STAFF_B)
    assert res_b_edit.status_code == 404, f"Cross-tenant write expected 404, got {res_b_edit.status_code}"

    # 校验 Tenant B 基线前后完全零改动
    tenant_b_receipts_after = len(client.get("/api/receipts", headers=STAFF_B).json().get("data", []))
    with db.get_session(tenant_id="tenant_uat030_b") as session:
        tenant_b_db_after = len(db.list_receipt_rows(tenant_id="tenant_uat030_b"))

    assert tenant_b_receipts_before == tenant_b_receipts_after, (
        f"Tenant B receipts baseline altered: {tenant_b_receipts_before} -> {tenant_b_receipts_after}"
    )
    assert tenant_b_db_before == tenant_b_db_after, (
        f"Tenant B DB receipts baseline altered: {tenant_b_db_before} -> {tenant_b_db_after}"
    )
