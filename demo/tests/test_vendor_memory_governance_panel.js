// -*- coding: utf-8 -*-
// 已生效记忆治理区验收脚手架（收敛计划 P8 / AC2，闭环残债 R1 的界面半边）。
//
// 在 node vm 沙箱里载入**真实 main.js**，用注入的 fetch 调度器（零真实网络）验证：
//   ① vendorMemoryRowHtml()：逐字段渲染且与注入数据一致 —— 供应商 / 类型 / 值 / 信任度 /
//      衰减分 / 命中次数 / 谁写的 / 状态；数值原样取自后端（前端不重算），null/缺字段显「未知」
//      而不冒充 0；status 中文标签旁保留原始值 data-status；
//   ② renderVendorMemoryGovernance()：有数据 → 每行都渲染且行数与注入一致；空数据 →
//      显「暂无已生效记忆」空态（不是空白）；端点失败 → 只显错误态、不渲染半截列表；
//   ③ deleteVendorMemory()：二次确认后调**唯一删除端点** DELETE /api/memory/{memory_id}，
//      成功后重载列表且该条消失；取消确认时不发删除请求；端点报错/网络失败都不抛未捕获异常；
//   ④ 接线：index.html 有 #vendorMemoryGovernancePanel 锚点、main.js 在 tab-engine 分支
//      调用 loadVendorMemoryGovernance()（不是定义了没人调）；
//   ⑤ 样式消费方（前端版的「只写列·零消费方」）：渲染产物里每个 vm-* class 都带非空 style；
//   ⑥ 红线：全程 console.error 为 0。
//
// 本 harness 接受被测 main.js 路径作为参数 —— 反证与「改动前后 A/B 对照」因此免费：
//   反证形态（突变体式，仓库文件全程只读）：把渲染里 written_by 那一列去掉 → 字段断言必须失败。
//     cp demo/static/js/main.js /tmp/mutant_main.js
//     node demo/tests/test_vendor_memory_governance_panel.js /tmp/mutant_main.js   # 期望非 0 退出
//
// 用法（cwd 任意；路径一律由 __dirname 解析，避免本仓「写死 demo/... 只在仓库根通过」的坑）：
//   node demo/tests/test_vendor_memory_governance_panel.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const mainPath = process.argv[2] || path.join(demoDir, 'static', 'js', 'main.js');
const indexPath = path.join(demoDir, 'templates', 'index.html');
const src = fs.readFileSync(mainPath, 'utf8');
const indexHtml = fs.readFileSync(indexPath, 'utf8');

let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}

// ---- 最小 DOM 面（只支撑治理区这条链路 + toast） ----
function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {}, isConnected: true,
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return (k in this) ? this[k] : null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
}

const panelEl = mkEl({ id: 'vendorMemoryGovernancePanel', innerHTML: '加载中...' });
const bodyEl = mkEl({ tagName: 'BODY' });

// ---- fetch 调度器：只接本区两个端点，绝不发真实请求 ----
let listRows = [];
let listMode = 'ok';        // ok | network
let deleteMode = 'ok';      // ok | error | network
let confirmAnswer = true;
const calls = [];           // {url, method}
function mkRes(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        clone: () => mkRes(status, body),
    };
}
function dispatcher(url, init) {
    const u = String(url);
    const method = (init && init.method) || 'GET';
    calls.push({ url: u, method });
    if (u.indexOf('/api/admin/vendor-memory') === 0) {
        if (listMode === 'network') return Promise.reject(new Error('offline'));
        return Promise.resolve(mkRes(200, {
            status: 'success', data: listRows.slice(), total: listRows.length,
            limit: 50, offset: 0, tenant_id: 'default',
        }));
    }
    if (method === 'DELETE' && u.indexOf('/api/memory/') === 0) {
        if (deleteMode === 'network') return Promise.reject(new Error('offline'));
        if (deleteMode === 'error') {
            return Promise.resolve(mkRes(200, { status: 'error', msg: '未找到该记忆' }));
        }
        const mid = decodeURIComponent(u.slice('/api/memory/'.length));
        listRows = listRows.filter(r => r.memory_id !== mid);
        return Promise.resolve(mkRes(200, { status: 'success', data: { affected: 1 } }));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定的 URL' }));
}

const consoleErrors = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {}, warn() {},
        error(...a) { consoleErrors.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    confirm: () => confirmAnswer,
    localStorage: {
        _m: { demo_role: 'admin' },
        getItem(k) { return (k in this._m) ? this._m[k] : null; },
        setItem(k, v) { this._m[k] = String(v); },
        removeItem(k) { delete this._m[k]; },
    },
    Headers: class Headers {
        constructor(init) { this._m = new Map(Object.entries(init || {})); }
        has(k) { return this._m.has(k); }
        set(k, v) { this._m.set(k, v); }
        get(k) { return this._m.get(k); }
    },
    fetch: dispatcher,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.open = () => null;
sandbox.document = {
    addEventListener() {},
    getElementById(id) { return id === 'vendorMemoryGovernancePanel' ? panelEl : null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: bodyEl, readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

const { vendorMemoryRowHtml, renderVendorMemoryGovernance,
        loadVendorMemoryGovernance, deleteVendorMemory } = sandbox;

// 从一段渲染产物里取某个 data-field 单元格的文本（到下一个 < 为止）
function fieldText(html, field) {
    const m = new RegExp('data-field="' + field + '"[^>]*>([^<]*)<').exec(String(html));
    return m ? m[1] : null;
}
function fieldAttr(html, field, attr) {
    const seg = new RegExp('data-field="' + field + '"[^>]*>').exec(String(html));
    if (!seg) return null;
    const m = new RegExp(attr + '="([^"]*)"').exec(seg[0]);
    return m ? m[1] : null;
}
function delBtnHtml(html) {
    const m = /<button[^>]*class="vm-del"[^>]*>[\s\S]*?<\/button>/.exec(String(html));
    return m ? m[0] : null;
}
const tick = () => new Promise(r => setTimeout(r, 20));

const ROW_A = {
    memory_id: 'mid-aaaa', vendor: '德利行 Tak Lee Hong', fact_kind: 'alias',
    fact_value: '有机菜心', trust_level: 0.6, decay_score: 0.72, hit_count: 7,
    written_by: 'owner@demo.hk', status: 'active',
};
const ROW_B = {
    memory_id: 'mid-bbbb', vendor: '鴻運餐飲', fact_kind: 'layout',
    fact_value: 'ncr_handwritten', trust_level: 0.9, decay_score: 0.95, hit_count: 3,
    written_by: 'staff@demo.hk', status: 'active',
};

async function main() {
    ok('前置: 生产函数已定义',
        typeof vendorMemoryRowHtml === 'function'
        && typeof renderVendorMemoryGovernance === 'function'
        && typeof loadVendorMemoryGovernance === 'function'
        && typeof deleteVendorMemory === 'function',
        'row=' + typeof vendorMemoryRowHtml + ' render=' + typeof renderVendorMemoryGovernance
        + ' load=' + typeof loadVendorMemoryGovernance + ' del=' + typeof deleteVendorMemory);

    // ---- 1. 逐字段渲染且与注入数据一致（数值原样，前端不重算）----
    const rowHtml = vendorMemoryRowHtml(ROW_A);
    const expectEqual = [
        ['vendor', '德利行 Tak Lee Hong'],
        ['fact_kind', 'alias'],
        ['fact_value', '有机菜心'],
        ['trust_level', '0.6'],
        ['decay_score', '0.72'],
        ['hit_count', '7'],
        ['written_by', 'owner@demo.hk'],
    ];
    const badFields = expectEqual.filter(([f, v]) => fieldText(rowHtml, f) !== v)
        .map(([f, v]) => f + '=' + JSON.stringify(fieldText(rowHtml, f)) + ' 期望 ' + JSON.stringify(v));
    ok('逐字段: 有数据时每个治理字段都渲染且与注入数据一致', badFields.length === 0,
        '不一致=' + JSON.stringify(badFields) + ' html=' + rowHtml);
    ok('逐字段: 状态渲染中文标签且旁存原始值 data-status',
        fieldText(rowHtml, 'status') === '生效中' && fieldAttr(rowHtml, 'status', 'data-status') === 'active',
        'label=' + fieldText(rowHtml, 'status') + ' raw=' + fieldAttr(rowHtml, 'status', 'data-status'));
    ok('逐字段: 行上带 data-memory-id（删除按钮据此定位该条）',
        rowHtml.indexOf('data-memory-id="mid-aaaa"') !== -1, 'html=' + rowHtml);

    // 取不到值不能冒充 0
    const nullRow = vendorMemoryRowHtml({ memory_id: 'm', vendor: '甲', fact_kind: 'alias',
        fact_value: 'x', trust_level: null, decay_score: undefined, hit_count: null,
        written_by: '', status: 'active' });
    ok('逐字段: trust/decay/hit 为 null/缺失 → 「未知」而不是 0',
        fieldText(nullRow, 'trust_level') === '未知' && fieldText(nullRow, 'decay_score') === '未知'
        && fieldText(nullRow, 'hit_count') === '未知',
        'trust=' + fieldText(nullRow, 'trust_level') + ' decay=' + fieldText(nullRow, 'decay_score')
        + ' hit=' + fieldText(nullRow, 'hit_count'));
    ok('逐字段: written_by 为空 → 「未知」（谁写的必须有可见落点）',
        fieldText(nullRow, 'written_by') === '未知', 'who=' + fieldText(nullRow, 'written_by'));

    // 删除动作接线：每行的删除按钮指向正确的 memory_id 且调用 deleteVendorMemory
    const btn = delBtnHtml(rowHtml);
    ok('删除动作: 每行渲染删除按钮且携带该条 memory_id',
        !!btn && btn.indexOf('data-memory-id="mid-aaaa"') !== -1, 'btn=' + btn);
    ok('删除动作: 按钮点击调用 deleteVendorMemory（唯一删除逻辑入口）',
        !!btn && btn.indexOf('deleteVendorMemory(') !== -1, 'btn=' + btn);

    // ---- 2. 有数据 / 空数据 / 错误态 渲染 ----
    listRows = [ROW_A, ROW_B];
    renderVendorMemoryGovernance({ status: 'success', data: listRows.slice(), total: 2 });
    const rowCount = (panelEl.innerHTML.match(/class="vm-row"/g) || []).length;
    ok('渲染: 两行数据 → 渲染两行且都带 memory_id',
        rowCount === 2 && panelEl.innerHTML.indexOf('mid-aaaa') !== -1
        && panelEl.innerHTML.indexOf('mid-bbbb') !== -1
        && panelEl.innerHTML.indexOf('共 2 条已生效记忆') !== -1, 'inner=' + panelEl.innerHTML);

    renderVendorMemoryGovernance({ status: 'success', data: [], total: 0 });
    ok('渲染: 空数据 → 显「暂无已生效记忆」空态而不是空白',
        panelEl.innerHTML.indexOf('暂无已生效记忆') !== -1
        && panelEl.innerHTML.indexOf('class="vm-row"') === -1, 'inner=' + panelEl.innerHTML);

    renderVendorMemoryGovernance({ status: 'error', msg: '模拟取数故障' });
    ok('渲染: 端点失败 → 只显错误态、不渲染半截列表',
        panelEl.innerHTML.indexOf('已生效记忆读取失败') !== -1
        && panelEl.innerHTML.indexOf('模拟取数故障') !== -1
        && panelEl.innerHTML.indexOf('class="vm-row"') === -1, 'inner=' + panelEl.innerHTML);

    // ---- 3. loadVendorMemoryGovernance：端点接线 + 角色门 + 失败静默 ----
    calls.length = 0;
    listMode = 'ok';
    listRows = [ROW_A, ROW_B];
    loadVendorMemoryGovernance();
    await tick();
    ok('load: 请求打在只读治理端点 /api/admin/vendor-memory?status=active',
        calls.length === 1 && calls[0].method === 'GET'
        && calls[0].url === '/api/admin/vendor-memory?status=active',
        'calls=' + JSON.stringify(calls));
    ok('load: 响应渲染进面板（有数据逐字段可取）',
        fieldText(panelEl.innerHTML, 'written_by') === 'owner@demo.hk',
        'inner=' + panelEl.innerHTML);

    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'owner';
    loadVendorMemoryGovernance();
    await tick();
    ok('load: 非 admin 不发这次必然 403 的请求', calls.length === 0, 'calls=' + JSON.stringify(calls));
    sandbox.localStorage._m.demo_role = 'admin';

    calls.length = 0;
    listMode = 'network';
    loadVendorMemoryGovernance();
    await tick();
    ok('load: 网络失败 → 渲染错误态而不是空白，且不抛未捕获异常（console.error=0）',
        panelEl.innerHTML.indexOf('已生效记忆读取失败') !== -1 && consoleErrors.length === 0,
        'inner=' + panelEl.innerHTML + ' errors=' + JSON.stringify(consoleErrors.slice(0, 2)));
    listMode = 'ok';

    // ---- 4. 删除动作：调唯一端点 + 重载且该条消失 ----
    listRows = [ROW_A, ROW_B];
    deleteMode = 'ok';
    confirmAnswer = true;
    renderVendorMemoryGovernance({ status: 'success', data: listRows.slice(), total: 2 });
    calls.length = 0;
    await Promise.resolve(deleteVendorMemory('mid-aaaa', '德利行 Tak Lee Hong'));
    await tick();
    const delCalls = calls.filter(c => c.method === 'DELETE');
    ok('删除: 调唯一删除端点 DELETE /api/memory/{memory_id}（编码后的 memory_id）',
        delCalls.length === 1 && delCalls[0].url === '/api/memory/mid-aaaa',
        'calls=' + JSON.stringify(calls));
    ok('删除: 成功后重载列表（再次 GET 治理端点）',
        calls.filter(c => c.method === 'GET' && c.url.indexOf('/api/admin/vendor-memory') === 0).length === 1,
        'calls=' + JSON.stringify(calls));
    ok('删除: 该条已从列表消失（另一条仍在）',
        panelEl.innerHTML.indexOf('mid-aaaa') === -1 && panelEl.innerHTML.indexOf('mid-bbbb') !== -1,
        'inner=' + panelEl.innerHTML);

    // 取消确认 → 不发删除请求、不改列表
    listRows = [ROW_A, ROW_B];
    confirmAnswer = false;
    renderVendorMemoryGovernance({ status: 'success', data: listRows.slice(), total: 2 });
    calls.length = 0;
    const cancelled = await Promise.resolve(deleteVendorMemory('mid-aaaa', '德利行 Tak Lee Hong'));
    await tick();
    ok('删除: 二次确认取消 → 不发任何删除请求且返回 false',
        calls.filter(c => c.method === 'DELETE').length === 0 && cancelled === false,
        'calls=' + JSON.stringify(calls) + ' ret=' + cancelled);
    confirmAnswer = true;

    // 端点报错 → 不抛未捕获异常、不误重载
    listRows = [ROW_A, ROW_B];
    deleteMode = 'error';
    calls.length = 0;
    await Promise.resolve(deleteVendorMemory('mid-aaaa', '德利行 Tak Lee Hong'));
    await tick();
    ok('删除: 端点返回 error 时不抛未捕获异常（console.error=0）',
        consoleErrors.length === 0, 'errors=' + JSON.stringify(consoleErrors.slice(0, 2)));

    deleteMode = 'network';
    calls.length = 0;
    await Promise.resolve(deleteVendorMemory('mid-aaaa', '德利行 Tak Lee Hong'));
    await tick();
    ok('删除: 网络失败时不抛未捕获异常（console.error=0）',
        consoleErrors.length === 0, 'errors=' + JSON.stringify(consoleErrors.slice(0, 2)));
    deleteMode = 'ok';

    // ---- 5. 样式消费方：渲染产物里每个 vm-* class 都带非空 style ----
    function vmTags(html) {
        const out = [];
        const re = /<[a-z][a-z0-9]*\b[^>]*\bclass="(vm-[a-z0-9-]+)"[^>]*>/gi;
        let m;
        while ((m = re.exec(String(html))) !== null) out.push({ cls: m[1], tag: m[0] });
        return out;
    }
    function styleOf(tag) { const m = /\bstyle="([^"]*)"/.exec(tag); return m ? m[1] : ''; }

    const artifacts = [];
    renderVendorMemoryGovernance({ status: 'success', data: [ROW_A, ROW_B], total: 2 });
    artifacts.push(panelEl.innerHTML);
    renderVendorMemoryGovernance({ status: 'success', data: [], total: 0 });
    artifacts.push(panelEl.innerHTML);
    renderVendorMemoryGovernance({ status: 'error', msg: '模拟取数失败' });
    artifacts.push(panelEl.innerHTML);

    const tags = artifacts.flatMap(html => vmTags(html));
    const unstyled = tags.filter(t => !styleOf(t.tag));
    const renderedCls = new Set(tags.map(t => t.cls));
    ok('样式消费方: 渲染产物里每个 vm-* class 都带非空 style（无样式即失败）',
        tags.length > 0 && unstyled.length === 0,
        '无样式的标签=' + JSON.stringify(unstyled.map(t => t.cls)));
    for (const cls of ['vm-head', 'vm-row', 'vm-cell', 'vm-hcell', 'vm-action',
                       'vm-del', 'vm-empty', 'vm-error']) {
        ok('样式消费方: 渲染产物里真实出现 ' + cls, renderedCls.has(cls),
            '缺失=' + cls + ' 已有=' + JSON.stringify([...renderedCls]));
    }

    // ---- 6. 接线：HTML 锚点 + tab-engine 分支调用 ----
    ok('接线: index.html 存在 #vendorMemoryGovernancePanel 锚点',
        indexHtml.indexOf('id="vendorMemoryGovernancePanel"') !== -1, '未找到锚点');
    const engineBranch = src.split("if (targetId === 'tab-engine')")[1];
    ok('接线: tab-engine 分支调用 loadVendorMemoryGovernance()（不是定义了没人调）',
        !!engineBranch && engineBranch.split('}')[0].indexOf('loadVendorMemoryGovernance()') !== -1,
        'branch=' + (engineBranch || '').split('}')[0]);
    ok('接线: 删除动作复用 P1-T1 端点（源码里 DELETE 到 /api/memory/）',
        src.indexOf("'/api/memory/' + encodeURIComponent(mid)") !== -1,
        '未看到删除端点的唯一调用形态');

    // ---- 7. 红线：无未捕获异常 ----
    ok('全程 console.error 为 0', consoleErrors.length === 0,
        'errors=' + JSON.stringify(consoleErrors.slice(0, 3)));

    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    process.exit(fail === 0 ? 0 : 1);
}

main();
