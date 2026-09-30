// 前端「低权限角色不暴露 owner-only 入口」的验收脚手架（F5-L1 / F5-L2 / F1）。
//
// 缺陷背景（Orca 浏览器走查）：前端对 staff 角色暴露了 owner-only 写操作入口，
// 而后端对同一接口 fail-closed 返回 403 —— 能力与后端不一致（权限泄漏）。
//   F5-L1 实时库存与价格页：行内「价格走势 / 盘点 / 编辑 / 更多」、工具条「合并食材 / 一键清理重复食材」
//   F5-L2 归档对账页「供应商档案」：行内「查看单据 / 编辑 / 更多」、工具条「+ 新增供应商」
//   F1    RAG data_only 调试入口：仅 admin 可见（owner/staff 均不可见）
//
// 本脚手架在 node vm 沙箱里载入**真实 main.js**，调用生产函数本体：
//   loadInventoryData（内部 render 行）/ renderSupplierAdminRows（供应商行）/ applyRoleVisibility（class 收敛）
// 断言三层：
//   ① staff 下 F5 行内入口不输出，owner/admin 下输出；
//   ② applyRoleVisibility 对 .admin-only / .owner-only 的显示切换正确（含原始内联 display 回写）；
//   ③ 静态接线：index.html 的 class 声明口径正确（新增食材不得被误门禁）。
//
// 沙箱内零真实网络：fetch 由本脚本注入的记录器接管。
// 用法（cwd 任意）：node demo/tests/frontend_role_gate_harness.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const indexHtmlPath = process.argv[3] || path.join(__dirname, '..', 'templates', 'index.html');
const src = fs.readFileSync(mainPath, 'utf8');
const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8');

let pass = 0;
let fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; } else { fail++; failures.push(name + ' -- ' + detail); }
}

function mkClassList() {
    const s = new Set();
    return {
        add(...c) { c.forEach(x => s.add(x)); },
        remove(...c) { c.forEach(x => s.delete(x)); },
        toggle(c, on) { if (on === undefined) { s.has(c) ? s.delete(c) : s.add(c); } else if (on) s.add(c); else s.delete(c); },
        contains(c) { return s.has(c); },
    };
}

function mkEl(props) {
    return Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', value: '',
        style: {}, dataset: {}, children: [], disabled: false, type: '',
        isConnected: false, parentElement: null, parentNode: null,
        classList: mkClassList(),
        addEventListener() {},
        appendChild(c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; },
        removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
        remove() { this._removed = true; },
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k] != null ? this[k] : null; },
        focus() {}, click() {}, scrollIntoView() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        closest() { return null; },
    }, props || {});
}

const invBody = mkEl({ tagName: 'TBODY', id: 'inventoryTableBody' });
const supBody = mkEl({ tagName: 'TBODY', id: 'supplierAdminBody' });
const supEmpty = mkEl({ id: 'supplierEmpty' });
const byId = {
    inventoryTableBody: invBody,
    supplierAdminBody: supBody,
    supplierEmpty: supEmpty,
};

// .admin-only / .owner-only 的「模板元素」：applyRoleVisibility 必须按角色切换其 display。
// o1 带原始内联 display:flex（对应 F1 form-group），用于验证恢复时回写原值而非清空。
const adminOnlyEls = [mkEl({ id: 'ao_nav1' }), mkEl({ id: 'ao_nav2' }), mkEl({ id: 'ao_rag' })];
const ownerOnlyEls = [mkEl({ id: 'oo_merge' }), mkEl({ id: 'oo_dedup', style: { display: 'flex' } })];

const sandboxWarns = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {},
        warn(...a) { sandboxWarns.push(a.map(String).join(' ')); },
        error(...a) { sandboxWarns.push('[error] ' + a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    localStorage: {
        _s: {},
        getItem(k) { return (k in this._s) ? this._s[k] : null; },
        setItem(k, v) { this._s[k] = String(v); },
        removeItem(k) { delete this._s[k]; },
    },
    Headers: class Headers {
        constructor(init) { this._m = new Map(Object.entries(init || {})); }
        has(k) { return this._m.has(k); }
        set(k, v) { this._m.set(k, v); }
        get(k) { return this._m.get(k); }
    },
    fetch: dispatcher,
    URLSearchParams,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.document = {
    addEventListener() {},
    getElementById: (id) => byId[id] || null,
    querySelector() { return null; },
    querySelectorAll(sel) {
        if (sel === '.admin-only') return adminOnlyEls;
        if (sel === '.owner-only') return ownerOnlyEls;
        return [];
    },
    createElement(tag) { return mkEl({ tagName: String(tag || 'div').toUpperCase() }); },
    documentElement: { style: { setProperty() {} } },
    body: mkEl({ tagName: 'BODY' }),
    readyState: 'loading',
};

function mkRes(status, bodyObj) {
    return {
        ok: status >= 200 && status < 300,
        status: status,
        json: () => Promise.resolve(bodyObj),
        clone() { return mkRes(status, bodyObj); },
    };
}

// 记录器：/api/inventory 返回一条 SKU；其余端点回空成功。
function dispatcher(url, init) {
    const u = String(url);
    if (u.indexOf('/api/inventory?') === 0) {
        return Promise.resolve(mkRes(200, {
            status: 'success',
            data: [{
                id: 1, name: '商品A', category: '肉类', base_unit: 'kg',
                current_stock: 42, active: 1, min_stock_alert: 0,
            }],
            meta: {},
        }));
    }
    return Promise.resolve(mkRes(200, { status: 'success', data: [] }));
}

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { sandboxWarns.push('load note: ' + e.message); }

const loadInventoryData = sandbox.loadInventoryData;
const renderSupplierAdminRows = sandbox.renderSupplierAdminRows;
const applyRoleVisibility = sandbox.applyRoleVisibility;
ok('B0 生产函数均已导出',
    typeof loadInventoryData === 'function' && typeof renderSupplierAdminRows === 'function'
    && typeof applyRoleVisibility === 'function',
    [typeof loadInventoryData, typeof renderSupplierAdminRows, typeof applyRoleVisibility].join(','));

const OWNER_ROW_ENTRIES = ['价格走势', '盘点', '编辑', '更多'];
const SUP_ROW_ENTRIES = ['查看单据', '编辑', '更多'];

function setRole(role) { vm.runInContext("localStorage.setItem('demo_role', " + JSON.stringify(role) + ")", sandbox); }

// ---------------------------------------------------------------
// ①-F5-L1 库存行内：staff 不输出，owner 输出
// ---------------------------------------------------------------
function invRenderedHtml() {
    return invBody.children.map(c => String(c.innerHTML || '')).join('\n');
}

setRole('staff');
try { loadInventoryData(); } catch (e) { sandboxWarns.push('inv load err: ' + e.message); }

setTimeout(() => {
    const staffHtml = invRenderedHtml();
    ok('F5L1-a staff 下库存行仍有数据行', invBody.children.length === 1, 'rows=' + invBody.children.length);
    OWNER_ROW_ENTRIES.forEach(t => {
        ok('F5L1-b staff 下不出现 owner-only 行内入口「' + t + '」',
            staffHtml.indexOf(t) === -1, staffHtml.slice(0, 260));
    });

    // 切 owner 重渲染
    setRole('owner');
    invBody.children.length = 0;
    try { loadInventoryData(); } catch (e) { sandboxWarns.push('inv load err(owner): ' + e.message); }

    setTimeout(() => {
        const ownerHtml = invRenderedHtml();
        OWNER_ROW_ENTRIES.forEach(t => {
            ok('F5L1-c owner 下出现行内入口「' + t + '」',
                ownerHtml.indexOf(t) !== -1, ownerHtml.slice(0, 260));
        });

        // ---------------------------------------------------------------
        // ②-F5-L2 供应商行内：staff 不输出，owner 输出
        // ---------------------------------------------------------------
        const suppliers = [{
            id: 7, name: '测试供应商', supplier_code: 'S7', active: 1, is_overdue: 0,
            payment_terms_days: 30, settlement_pref: 'credit', receipt_count: 3,
            unpaid_credit_total: 120, unpaid_credit_count: 2, contact_phone: '000',
        }];
        function supRenderedHtml() {
            return supBody.children.map(c => String(c.innerHTML || '')).join('\n');
        }
        setRole('staff');
        supBody.children.length = 0;
        renderSupplierAdminRows(suppliers);
        const supStaffHtml = supRenderedHtml();
        ok('F5L2-a staff 下供应商仍有数据行', supBody.children.length === 1, 'rows=' + supBody.children.length);
        SUP_ROW_ENTRIES.forEach(t => {
            ok('F5L2-b staff 下不出现 owner-only 供应商入口「' + t + '」',
                supStaffHtml.indexOf(t) === -1, supStaffHtml.slice(0, 300));
        });
        // 只读字段仍在（避免把整行清空）
        ok('F5L2-b2 staff 下仍显示供应商名与未付金额（只读信息保留）',
            supStaffHtml.indexOf('测试供应商') !== -1 && supStaffHtml.indexOf('120') !== -1, supStaffHtml.slice(0, 300));

        setRole('owner');
        supBody.children.length = 0;
        renderSupplierAdminRows(suppliers);
        const supOwnerHtml = supRenderedHtml();
        SUP_ROW_ENTRIES.forEach(t => {
            ok('F5L2-c owner 下出现供应商入口「' + t + '」',
                supOwnerHtml.indexOf(t) !== -1, supOwnerHtml.slice(0, 300));
        });

        // ---------------------------------------------------------------
        // ③ applyRoleVisibility：.admin-only / .owner-only 按角色收敛（F1）
        // ---------------------------------------------------------------
        function displays(els) { return els.map(e => String(e.style.display)); }

        applyRoleVisibility('staff');
        ok('F1-a staff 下 .admin-only 全部隐藏（RAG data_only 调试入口）',
            displays(adminOnlyEls).every(d => d === 'none'), displays(adminOnlyEls));
        ok('F5-a staff 下 .owner-only 全部隐藏（库存/供应商 owner 级工具条）',
            displays(ownerOnlyEls).every(d => d === 'none'), displays(ownerOnlyEls));

        applyRoleVisibility('owner');
        ok('F1-b owner 下 .admin-only 仍隐藏（仅超管可见）',
            displays(adminOnlyEls).every(d => d === 'none'), displays(adminOnlyEls));
        ok('F5-b owner 下 .owner-only 显示，且回写原始内联 display（flex 不被清空）',
            displays(ownerOnlyEls).every(d => d !== 'none') && ownerOnlyEls[1].style.display === 'flex',
            displays(ownerOnlyEls));

        applyRoleVisibility('admin');
        ok('F1-c admin 下 .admin-only 显示',
            displays(adminOnlyEls).every(d => d !== 'none'), displays(adminOnlyEls));
        ok('F5-c admin 下 .owner-only 显示',
            displays(ownerOnlyEls).every(d => d !== 'none'), displays(ownerOnlyEls));

        // ---------------------------------------------------------------
        // ④ 静态接线：index.html class 口径（新增食材不得被误门禁）
        // ---------------------------------------------------------------
        ok('接线-1 index.html 工具条「合并食材」声明 owner-only',
            (function () {
                const i = indexHtml.indexOf('openSkuMergeModalFromInv');
                return i !== -1 && indexHtml.slice(i - 200, i + 20).indexOf('owner-only') !== -1;
            })(),
            '合并食材缺少 owner-only');
        {
            const btnIdx = indexHtml.indexOf('deduplicateSkus()');
            const snippet = indexHtml.slice(btnIdx - 200, btnIdx + 20);
            ok('接线-2 「一键清理重复食材」按钮带 owner-only class', snippet.indexOf('owner-only') !== -1, snippet);
        }
        {
            const btnIdx = indexHtml.indexOf('openAddSkuModal()');
            const snippet = indexHtml.slice(btnIdx - 220, btnIdx + 20);
            ok('接线-3 「+ 新增食材」不得被误门禁（后端 require_role("staff")，店员可用）',
                snippet.indexOf('owner-only') === -1, snippet);
        }
        {
            const idx = indexHtml.indexOf('arcDataOnlyToggle');
            const snippet = indexHtml.slice(Math.max(0, idx - 400), idx);
            ok('接线-4 RAG data_only 调试开关容器声明 admin-only', snippet.indexOf('admin-only') !== -1, snippet.slice(-220));
        }
        {
            const idx = indexHtml.indexOf('arcRagContextCard');
            const snippet = indexHtml.slice(idx, idx + 200);
            ok('接线-5 RAG 上下文调试卡声明 admin-only', snippet.indexOf('admin-only') !== -1, snippet);
        }
        {
            const i = indexHtml.indexOf('openSupplierModal()');
            const snippet = indexHtml.slice(Math.max(0, i - 200), i + 20);
            ok('接线-6 供应商工具条「+ 新增供应商」声明 owner-only',
                i !== -1 && snippet.indexOf('owner-only') !== -1, snippet);
        }

        console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures }, null, 0));
        process.exit(fail === 0 ? 0 : 1);
    }, 30);
}, 30);
