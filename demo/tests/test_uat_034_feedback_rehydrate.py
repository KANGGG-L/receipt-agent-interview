# -*- coding: utf-8 -*-
"""UAT-034 专项测试：已保存的行反馈重新打开后原因和点踩状态清空显示缺陷修复。

权威定义：
- /Users/ethan/harness-tmp/receipt-grok-takeover-20261015/01-acceptance-checklist.md:378-394 [UAT-034]

测试范围：
1. 回填闭环：对明细行点赞并填写原因 A 保存后，重新打开单据，行内选中态与 textarea 内容精确恢复为已保存值；
2. 改判语义：对同一行先点赞原因 A，再改为点踩原因 B 保存并重复提交，API 与 DB 中同一键仅允许存在 1 条记录，值为 rating=-1 且 comment=B；
3. 刷新重开一致：刷新页面后再次重开，仍为 rating=-1 与原因 B；
4. 不串行：行间互不串扰，跨 tenant 互相隔离（404 拒绝且零改动）；
5. 业务字段零污染：反馈回填与保存前后业务字段与明细逐值相等，记忆表前后零改动。
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

STAFF_A = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat034_a"}
STAFF_B = {"X-Role": "staff", "X-Tenant-Id": "tenant_uat034_b"}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    old_db_path = db.DB_PATH
    test_db_path = str(tmp_path / "test_uat034_feedback.db")
    monkeypatch.setenv("DB_PATH", test_db_path)
    db.DB_PATH = test_db_path
    db._make_engine()
    yield
    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass
    db.DB_PATH = old_db_path
    db._make_engine()


def run_node_harness(js_test_code, mock_api_routes=None):
    """在包含真实 DOM 模拟与 review_core.js / archive_list.js 的 node 沙箱中运行前端测试。"""
    routes_json = json.dumps(mock_api_routes or {})
    harness_code = f"""
    const fs = require('fs');
    const path = require('path');
    const vm = require('vm');

    const REPO_ROOT = {json.dumps(REPO_ROOT)};
    const mainPath = path.join(REPO_ROOT, 'demo', 'static', 'js', 'main.js');
    const src = fs.readFileSync(mainPath, 'utf8');

    const elements = {{}};

    function mkClassList(initialClasses = []) {{
        const s = new Set(initialClasses);
        return {{
            _s: s,
            add(...c) {{ c.forEach(x => {{ if (x) s.add(x); }}); }},
            remove(...c) {{ c.forEach(x => {{ if (x) s.delete(x); }}); }},
            contains(c) {{ return s.has(c); }},
            toString() {{ return Array.from(s).join(' '); }}
        }};
    }}

    function parseHtmlToDom(html, parent) {{
        if (!html || typeof html !== 'string') return [];
        const VOID_TAGS = new Set(['AREA', 'BASE', 'BR', 'COL', 'EMBED', 'HR', 'IMG', 'INPUT', 'LINK', 'META', 'PARAM', 'SOURCE', 'TRACK', 'WBR']);
        const tagRe = /<\\/?([a-z0-9-]+)([^>]*?)(\\/?)>|([^<]+)/gi;
        let m;
        const rootNodes = [];
        const stack = [];

        while ((m = tagRe.exec(html)) !== null) {{
            if (m[4]) {{
                const text = m[4];
                if (stack.length > 0) {{
                    const top = stack[stack.length - 1];
                    top.textContent = (top.textContent || '') + text;
                    if (top.tagName === 'TEXTAREA') top.value = (top.value || '') + text;
                }}
                continue;
            }}

            const isClose = m[0].startsWith('</');
            const tagName = m[1].toUpperCase();
            const rawAttrs = m[2] || '';
            const isSelfClosing = m[3] === '/' || VOID_TAGS.has(tagName);

            if (isClose) {{
                for (let i = stack.length - 1; i >= 0; i--) {{
                    if (stack[i].tagName === tagName) {{
                        stack.splice(i);
                        break;
                    }}
                }}
            }} else {{
                const el = mkEl(null, {{ tagName: tagName }});
                const currParent = stack.length > 0 ? stack[stack.length - 1] : parent;
                if (currParent && currParent.children) {{
                    currParent.children.push(el);
                    el.parentNode = currParent;
                    el.parentElement = currParent;
                }}
                if (stack.length === 0) {{
                    rootNodes.push(el);
                }}

                const attrRegex = /([a-z0-9_-]+)(?:="([^"]*)")?/gi;
                let aMatch;
                while ((aMatch = attrRegex.exec(rawAttrs)) !== null) {{
                    const k = aMatch[1].toLowerCase();
                    const v = aMatch[2] != null ? aMatch[2] : '';
                    if (k === 'id') {{
                        el.id = v;
                        elements[v] = el;
                    }} else if (k === 'class') {{
                        el.className = v;
                        v.split(/\\s+/).filter(Boolean).forEach(c => el.classList.add(c));
                    }} else if (k === 'value') {{
                        el.value = v;
                    }} else if (k.startsWith('data-')) {{
                        const dKey = k.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
                        el.dataset[dKey] = v;
                    }} else if (k === 'style') {{
                        el.style = el.style || {{}};
                        v.split(';').forEach(pair => {{
                            const [sk, sv] = pair.split(':').map(s => s && s.trim());
                            if (sk && sv) el.style[sk] = sv;
                        }});
                    }} else if (k.startsWith('on')) {{
                        el[k] = v;
                    }} else {{
                        el[k] = v;
                    }}
                }}

                if (!isSelfClosing) {{
                    stack.push(el);
                }}
            }}
        }}
        return rootNodes;
    }}

    function mkEl(id, props) {{
        const listeners = {{}};
        const el = Object.assign({{
            id: id || '',
            value: '', tagName: 'DIV', textContent: '', innerText: '', innerHTML: '', style: {{}}, dataset: {{}},
            children: [], parentNode: null, parentElement: null, disabled: false,
            classList: mkClassList(),
            listeners: listeners,
            querySelector(sel) {{
                const found = this.querySelectorAll(sel);
                return found.length > 0 ? found[0] : null;
            }},
            querySelectorAll(sel) {{
                const res = [];
                function matchEl(node) {{
                    if (sel.startsWith('.')) {{
                        const cls = sel.slice(1);
                        if (node.classList && node.classList.contains(cls)) return true;
                    }}
                    if (sel.startsWith('#')) {{
                        if (node.id === sel.slice(1)) return true;
                    }}
                    if (node.tagName && node.tagName.toLowerCase() === sel.toLowerCase()) return true;
                    if (sel.includes('[')) {{
                        const m = sel.match(/\\[([a-z0-9_-]+)(?:="([^"]*)")?\\]/i);
                        if (m) {{
                            const k = m[1];
                            const v = m[2];
                            if (k.startsWith('data-')) {{
                                const dKey = k.slice(5).replace(/-([a-z])/g, (_, ch) => ch.toUpperCase());
                                if (v != null) return node.dataset && String(node.dataset[dKey]) === v;
                                return node.dataset && node.dataset[dKey] !== undefined;
                            }}
                        }}
                    }}
                    return false;
                }}
                function search(curr) {{
                    for (const c of curr.children || []) {{
                        if (matchEl(c)) res.push(c);
                        search(c);
                    }}
                }}
                search(this);
                return res;
            }},
            appendChild(child) {{
                this.children.push(child);
                child.parentNode = this;
                child.parentElement = this;
                return child;
            }},
            removeChild(child) {{
                const idx = this.children.indexOf(child);
                if (idx >= 0) this.children.splice(idx, 1);
                child.parentNode = null;
                child.parentElement = null;
                return child;
            }},
            remove() {{
                if (this.parentNode) this.parentNode.removeChild(this);
            }},
            closest(sel) {{
                if (sel === 'tr' && this.tagName === 'TR') return this;
                if (sel === 'td' && this.tagName === 'TD') return this;
                if (sel === '.feedback-cell' && this.classList.contains('feedback-cell')) return this;
                if (this.parentNode) return this.parentNode.closest(sel);
                return null;
            }},
            addEventListener(t, fn) {{ (listeners[t] = listeners[t] || []).push(fn); }},
            setAttribute(k, v) {{ this[k] = v; }},
            getAttribute(k) {{ return this[k]; }},
            removeAttribute(k) {{ delete this[k]; }},
            focus() {{}},
            select() {{}},
            scrollIntoView() {{}}
        }}, props || {{}});

        Object.defineProperty(el, 'innerHTML', {{
            get() {{ return this._innerHTML || ''; }},
            set(val) {{
                this._innerHTML = val;
                this.children = [];
                parseHtmlToDom(val, this);
            }}
        }});

        if (id) elements[id] = el;
        return el;
    }}

    [
        'arcSupplier', 'arcDate', 'arcSheet', 'arcTotal', 'arcSettlementType',
        'arcDepartmentId', 'arcCurrency', 'arcPaymentMark', 'arcTableBody',
        'btnSaveArchive', 'archiveDetailModal', 'archiveModalTitle',
        'rowFeedbackModal', 'rowFeedbackModalBody', 'archiveMathWarnBanner',
        'archivePreviewImg', 'btnArchiveOpenRaw', 'archiveNoImage',
        'arcDataOnlyToggle', 'arcRagContextCard', 'arcRagContextPre',
        'qualityWarningsBanner'
    ].forEach(id => mkEl(id));

    const mockRoutes = {routes_json};

    const sandbox = {{
        console,
        process,
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
                if (sel === '#arcTableBody tr') return elements['arcTableBody'].querySelectorAll('tr');
                return [];
            }},
            addEventListener() {{}},
            documentElement: {{ style: {{ setProperty() {{}} }} }},
            body: mkEl('body'),
        }},
        localStorage: {{
            _map: {{ demo_tenant_id: 'tenant_uat034_a', demo_role: 'staff' }},
            getItem(k) {{ return this._map[k] || null; }},
            setItem(k, v) {{ this._map[k] = String(v); }},
            removeItem(k) {{ delete this._map[k]; }}
        }},
        track() {{}},
        showToast() {{}},
        showQualityWarnings() {{}},
        populateDeptSelect() {{}},
        renderCurrencySymbol() {{}},
        openAccessibleModal() {{}},
        closeAccessibleModal() {{}},
        closeModalById(id) {{
            const el = elements[id];
            if (el) el.classList.add('hide');
        }},
        fetch(url, opts) {{
            const cleanUrl = url.split('?')[0];
            const route = mockRoutes[cleanUrl] || mockRoutes[url];
            if (route) {{
                return Promise.resolve({{
                    status: route.status || 200,
                    json: () => Promise.resolve(route.data)
                }});
            }}
            return Promise.resolve({{
                status: 404,
                json: () => Promise.resolve({{ status: 'error', msg: 'Not found in mock' }})
            }});
        }},
        apiFetch(url, opts) {{
            return this.fetch(url, opts);
        }}
    }};
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.self = sandbox;

    vm.runInNewContext(src, sandbox, {{ filename: 'main.js' }});
    const {{ loadF4Domains }} = require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js'));
    loadF4Domains((dsrc, name) => vm.runInNewContext(dsrc, sandbox, {{ filename: name }}));

    vm.runInNewContext({json.dumps(js_test_code)}, sandbox, {{ filename: 'test_uat034_block.js' }});
    """
    proc = subprocess.run(["node", "-e", harness_code], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _make_test_receipt(supplier_name="Vendor_A", total_amount=100.0, items=None, tenant_id="tenant_uat034_a", status="approved"):
    rid = db.create_receipt(supplier_name=supplier_name, status=status, tenant_id=tenant_id)
    s = db.get_session()
    try:
        r = s.query(db._ReceiptRow).get(rid)
        r.total_amount = total_amount
        r.receipt_date = "2026-10-16"
        r.sheet_name = "2026-10"
        s.commit()
    finally:
        s.close()
    if items:
        db.set_receipt_items(rid, items, tenant_id=tenant_id)
    return rid


# =========================================================================
# 准则 1：回填闭环
# 对明细行点赞并填写原因 A 保存后，重新打开单据，该行的选中态与 textarea 内容精确恢复
# =========================================================================
def test_01_rehydrate_closed_loop():
    client = TestClient(app)

    # 1. 创建含 2 行明细的单据
    rid = _make_test_receipt(
        supplier_name="Vendor_A",
        total_amount=150.0,
        items=[
            {"name": "Organic Milk 1L", "quantity": 2.0, "unit": "瓶", "unit_price": 50.0, "amount": 100.0},
            {"name": "Bread", "quantity": 1.0, "unit": "个", "unit_price": 50.0, "amount": 50.0},
        ],
        tenant_id="tenant_uat034_a",
    )

    # 2. 对第 0 行提交点赞与原因 A
    post_res = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": 1, "comment": "原因 A", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    assert post_res.status_code == 200, f"Submit feedback failed: {post_res.text}"

    # 3. 准备 mock 数据供前端沙箱读取
    detail_res = client.get(f"/api/receipt/{rid}", headers=STAFF_A)
    assert detail_res.status_code == 200
    fb_res = client.get(f"/api/receipt/{rid}/feedback", headers=STAFF_A)
    assert fb_res.status_code == 200

    mock_routes = {
        f"/api/receipt/{rid}": {"status": 200, "data": detail_res.json()},
        f"/api/receipt/{rid}/feedback": {"status": 200, "data": fb_res.json()},
    }

    # 4. 执行前端重开单据并在沙箱核验 DOM 恢复态
    js_code = f"""
    loadReceiptDetail({rid});
    // 异步完成等候
    setTimeout(() => {{
        const tbody = document.getElementById('arcTableBody');
        const rows = tbody.querySelectorAll('tr');
        if (rows.length !== 2) {{
            console.error(`FAIL: expected 2 rows but got ${{rows.length}}`);
            process.exit(1);
        }}
        const cell0 = rows[0].querySelector('.feedback-cell');
        if (!cell0) {{
            console.error('FAIL: feedback-cell not found on row 0');
            process.exit(1);
        }}
        const likeBtn = cell0.querySelector('.btn-like');
        const dislikeBtn = cell0.querySelector('.btn-dislike');
        const commentEl = cell0.querySelector('.feedback-comment');
        const badgeEl = rows[0].querySelector('.feedback-badge');

        const likeVal = cell0.dataset.like;
        const commentVal = commentEl ? commentEl.value : '';
        const likeActive = likeBtn && likeBtn.classList.contains('active');
        const dislikeActive = dislikeBtn && dislikeBtn.classList.contains('active');
        const badgeText = badgeEl ? badgeEl.textContent : '';

        if (likeVal !== '1') {{
            console.error(`FAIL: row 0 dataset.like expected '1' but got '${{likeVal}}'`);
            process.exit(1);
        }}
        if (!likeActive || dislikeActive) {{
            console.error(`FAIL: row 0 likeBtn active expected true but got likeActive=${{likeActive}}, dislikeActive=${{dislikeActive}}`);
            process.exit(1);
        }}
        if (commentVal !== '原因 A') {{
            console.error(`FAIL: row 0 textarea expected '原因 A' but got '${{commentVal}}'`);
            process.exit(1);
        }}
        if (badgeText !== '已点赞') {{
            console.error(`FAIL: row 0 badgeText expected '已点赞' but got '${{badgeText}}'`);
            process.exit(1);
        }}
        console.log('PASS: test_01_rehydrate_closed_loop completed successfully');
    }}, 50);
    """

    code, out, err = run_node_harness(js_code, mock_routes)
    assert code == 0, f"Harness failed (code {code}):\nSTDOUT: {out}\nSTDERR: {err}"
    assert "PASS: test_01_rehydrate_closed_loop completed successfully" in out


# =========================================================================
# 准则 2：改判语义
# 对同一行先点赞原因 A，再改为点踩原因 B 保存，重复提交后 API/DB 仅同键 1 记录 rating=-1/comment=B
# =========================================================================
def test_02_rejudge_semantic_single_record():
    client = TestClient(app)

    rid = _make_test_receipt(
        supplier_name="Vendor_A",
        total_amount=100.0,
        items=[{"name": "Item 1", "quantity": 1.0, "unit": "kg", "unit_price": 100.0, "amount": 100.0}],
        tenant_id="tenant_uat034_a",
    )

    # 1. 首次点赞提交 (原因 A)
    r1 = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": 1, "comment": "原因 A", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    assert r1.status_code == 200

    # 2. 改判为点踩提交 (原因 B)
    r2 = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": -1, "comment": "原因 B", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    assert r2.status_code == 200

    # 3. 重复提交 (幂等性测试)
    r3 = client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": -1, "comment": "原因 B", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    assert r3.status_code == 200

    # 4. 校验 API 读回：仅有 1 条记录，且为 rating=-1 与 comment="原因 B"
    list_res = client.get(f"/api/receipt/{rid}/feedback", headers=STAFF_A)
    assert list_res.status_code == 200
    feedbacks = list_res.json().get("feedbacks", [])
    row0_fbs = [fb for fb in feedbacks if fb.get("item_index") == 0]
    assert len(row0_fbs) == 1, f"Expected exactly 1 feedback for row 0, got {len(row0_fbs)}"
    assert row0_fbs[0]["like"] == -1, f"Expected rating -1, got {row0_fbs[0]['like']}"
    assert row0_fbs[0]["comment"] == "原因 B", f"Expected comment '原因 B', got {row0_fbs[0]['comment']}"

    # 5. 校验 DB 底层表：仅 1 条，旧值 A 零残留
    s = db.get_session()
    try:
        db_rows = s.query(db._ReceiptFeedbackRow).filter_by(receipt_id=rid, item_index=0).all()
        assert len(db_rows) == 1, f"DB must have exactly 1 row, got {len(db_rows)}"
        assert db_rows[0].like == -1
        assert db_rows[0].comment == "原因 B"
    finally:
        s.close()


# =========================================================================
# 准则 3：刷新重开一致
# 刷新页面后再次重开，仍为 rating=-1 与原因 B
# =========================================================================
def test_03_refresh_and_reopen_consistency():
    client = TestClient(app)

    rid = _make_test_receipt(
        supplier_name="Vendor_A",
        total_amount=100.0,
        items=[{"name": "Item 1", "quantity": 1.0, "unit": "kg", "unit_price": 100.0, "amount": 100.0}],
        tenant_id="tenant_uat034_a",
    )

    # 先点赞再改判点踩原因 B
    client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": 1, "comment": "原因 A", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": -1, "comment": "原因 B", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )

    detail_res = client.get(f"/api/receipt/{rid}", headers=STAFF_A)
    fb_res = client.get(f"/api/receipt/{rid}/feedback", headers=STAFF_A)

    mock_routes = {
        f"/api/receipt/{rid}": {"status": 200, "data": detail_res.json()},
        f"/api/receipt/{rid}/feedback": {"status": 200, "data": fb_res.json()},
    }

    # 模拟浏览器环境完全刷新重开
    js_code = f"""
    loadReceiptDetail({rid});
    setTimeout(() => {{
        const tbody = document.getElementById('arcTableBody');
        const rows = tbody.querySelectorAll('tr');
        const cell0 = rows[0].querySelector('.feedback-cell');
        const likeBtn = cell0.querySelector('.btn-like');
        const dislikeBtn = cell0.querySelector('.btn-dislike');
        const commentEl = cell0.querySelector('.feedback-comment');
        const badgeEl = rows[0].querySelector('.feedback-badge');

        const likeVal = cell0.dataset.like;
        const commentVal = commentEl ? commentEl.value : '';
        const likeActive = likeBtn && likeBtn.classList.contains('active');
        const dislikeActive = dislikeBtn && dislikeBtn.classList.contains('active');
        const badgeText = badgeEl ? badgeEl.textContent : '';

        if (likeVal !== '-1') {{
            console.error(`FAIL: dataset.like expected '-1' but got '${{likeVal}}'`);
            process.exit(1);
        }}
        if (likeActive || !dislikeActive) {{
            console.error(`FAIL: dislikeBtn active expected true but got dislikeActive=${{dislikeActive}}`);
            process.exit(1);
        }}
        if (commentVal !== '原因 B') {{
            console.error(`FAIL: textarea expected '原因 B' but got '${{commentVal}}'`);
            process.exit(1);
        }}
        if (badgeText !== '已点踩') {{
            console.error(`FAIL: badgeText expected '已点踩' but got '${{badgeText}}'`);
            process.exit(1);
        }}
        console.log('PASS: test_03_refresh_and_reopen_consistency completed successfully');
    }}, 50);
    """

    code, out, err = run_node_harness(js_code, mock_routes)
    assert code == 0, f"Harness failed (code {code}):\nSTDOUT: {out}\nSTDERR: {err}"
    assert "PASS: test_03_refresh_and_reopen_consistency completed successfully" in out


# =========================================================================
# 准则 4：不串行与跨 Tenant 严格隔离
# 另一行不得串扰；跨 Tenant 读取/写入拒绝或不可见
# =========================================================================
def test_04_cross_row_and_tenant_isolation():
    client = TestClient(app)

    # 1. 租户 A 单据（2 行明细）
    rid_a = _make_test_receipt(
        supplier_name="Vendor_A",
        total_amount=200.0,
        items=[
            {"name": "Line 0", "quantity": 1.0, "unit": "件", "unit_price": 100.0, "amount": 100.0},
            {"name": "Line 1", "quantity": 1.0, "unit": "件", "unit_price": 100.0, "amount": 100.0},
        ],
        tenant_id="tenant_uat034_a",
    )

    # 仅给第 0 行提交点踩
    client.post(
        f"/api/receipt/{rid_a}/feedback",
        json={"like": -1, "comment": "第0行点踩", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )

    detail_res = client.get(f"/api/receipt/{rid_a}", headers=STAFF_A)
    fb_res = client.get(f"/api/receipt/{rid_a}/feedback", headers=STAFF_A)

    mock_routes = {
        f"/api/receipt/{rid_a}": {"status": 200, "data": detail_res.json()},
        f"/api/receipt/{rid_a}/feedback": {"status": 200, "data": fb_res.json()},
    }

    # 验证前端：第 0 行有点踩，第 1 行保持未评状态（不串行）
    js_code = f"""
    loadReceiptDetail({rid_a});
    setTimeout(() => {{
        const tbody = document.getElementById('arcTableBody');
        const rows = tbody.querySelectorAll('tr');
        const cell1 = rows[1].querySelector('.feedback-cell');
        const likeBtn1 = cell1.querySelector('.btn-like');
        const dislikeBtn1 = cell1.querySelector('.btn-dislike');
        const commentEl1 = cell1.querySelector('.feedback-comment');
        const badgeEl1 = rows[1].querySelector('.feedback-badge');

        const likeVal1 = cell1.dataset.like || '';
        const commentVal1 = (commentEl1 ? commentEl1.value : '').trim();
        const likeActive1 = likeBtn1 && likeBtn1.classList.contains('active');
        const dislikeActive1 = dislikeBtn1 && dislikeBtn1.classList.contains('active');
        const badgeDisp1 = badgeEl1 ? (badgeEl1.style.display || '') : '';

        if (likeVal1 !== '') {{
            console.error(`FAIL: line 1 dataset.like expected empty but got '${{likeVal1}}'`);
            process.exit(1);
        }}
        if (likeActive1 || dislikeActive1) {{
            console.error('FAIL: line 1 like buttons should not be active');
            process.exit(1);
        }}
        if (commentVal1 !== '') {{
            console.error(`FAIL: line 1 comment expected empty but got '${{commentVal1}}'`);
            process.exit(1);
        }}
        if (badgeDisp1 !== 'none' && badgeEl1.textContent !== '') {{
            console.error('FAIL: line 1 badge should be hidden');
            process.exit(1);
        }}
        console.log('PASS: line 1 is cleanly isolated');
    }}, 50);
    """

    code, out, err = run_node_harness(js_code, mock_routes)
    assert code == 0, f"Cross-row harness failed:\nSTDOUT: {out}\nSTDERR: {err}"
    assert "PASS: line 1 is cleanly isolated" in out

    # 2. 跨 Tenant 隔离：Tenant B 越权访问 Tenant A 单据反馈
    cross_read = client.get(f"/api/receipt/{rid_a}/feedback", headers=STAFF_B)
    assert cross_read.status_code == 404, f"Cross-tenant read must be 404, got {cross_read.status_code}"

    cross_write = client.post(
        f"/api/receipt/{rid_a}/feedback",
        json={"like": 1, "comment": "越权篡改", "item_index": 0, "tenant_id": "tenant_uat034_b"},
        headers=STAFF_B,
    )
    assert cross_write.status_code == 404, f"Cross-tenant write must be 404, got {cross_write.status_code}"

    # 校验 Tenant B 的反馈表未被任何数据污染
    s = db.get_session()
    try:
        tenant_b_fbs = s.query(db._ReceiptFeedbackRow).filter_by(tenant_id="tenant_uat034_b").all()
        assert len(tenant_b_fbs) == 0, f"Tenant B must have 0 feedback records, got {len(tenant_b_fbs)}"
    finally:
        s.close()


# =========================================================================
# 准则 5：业务字段零污染与记忆表零意外改动
# 反馈前后业务单据与明细逐值相等；供应商记忆表前后计数零改动
# =========================================================================
def test_05_business_fields_zero_pollution_and_memory_zero_mutation():
    client = TestClient(app)

    rid = _make_test_receipt(
        supplier_name="Vendor_A",
        total_amount=88.5,
        items=[
            {"name": "Organic Tofu", "quantity": 3.0, "unit": "盒", "unit_price": 15.0, "amount": 45.0},
            {"name": "Green Tea", "quantity": 1.0, "unit": "包", "unit_price": 43.5, "amount": 43.5},
        ],
        tenant_id="tenant_uat034_a",
    )

    # 记录初始业务字段与初始记忆表计数
    init_receipt = client.get(f"/api/receipt/{rid}", headers=STAFF_A).json()["data"]
    s = db.get_session()
    try:
        vendor_mem_before = s.query(db._VendorMemoryRow).count()
        pending_mem_before = s.query(db._PendingMemoryRow).count()
    finally:
        s.close()

    # 提交反馈操作（点赞与点踩）
    client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": 1, "comment": "豆腐新鲜", "item_index": 0, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )
    client.post(
        f"/api/receipt/{rid}/feedback",
        json={"like": -1, "comment": "包装有损", "item_index": 1, "tenant_id": "tenant_uat034_a"},
        headers=STAFF_A,
    )

    # 读回操作后业务字段与记忆表计数
    after_receipt = client.get(f"/api/receipt/{rid}", headers=STAFF_A).json()["data"]
    s = db.get_session()
    try:
        vendor_mem_after = s.query(db._VendorMemoryRow).count()
        pending_mem_after = s.query(db._PendingMemoryRow).count()
    finally:
        s.close()

    # 逐值比对业务字段
    assert init_receipt["supplier_name"] == after_receipt["supplier_name"]
    assert init_receipt["total_amount"] == after_receipt["total_amount"]
    assert init_receipt["date"] == after_receipt["date"]
    assert init_receipt["status"] == after_receipt["status"]
    assert len(init_receipt["items"]) == len(after_receipt["items"])

    for it_before, it_after in zip(init_receipt["items"], after_receipt["items"]):
        assert it_before["name"] == it_after["name"]
        assert it_before["quantity"] == it_after["quantity"]
        assert it_before["unit"] == it_after["unit"]
        assert it_before["unit_price"] == it_after["unit_price"]
        assert it_before["amount"] == it_after["amount"]

    # 记忆表零意外改动校验
    assert vendor_mem_before == vendor_mem_after, f"Vendor memory changed: {vendor_mem_before} -> {vendor_mem_after}"
    assert pending_mem_before == pending_mem_after, f"Pending memory changed: {pending_mem_before} -> {pending_mem_after}"

