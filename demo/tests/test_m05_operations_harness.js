// M05 终审补强 harness：真实 engine_admin.js/admin_observation.js 源码，零真实网络。
// 覆盖：v3 消费与 no_data 三态、process reset、写失败计数、sync backlog、角色视图分离、CSP 非 inline。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(demoDir, 'static', 'js', 'main.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(demoDir, 'templates', 'index.html'), 'utf8');
const F4 = require('./f4_load_domains.js');

let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}
function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {},
        classList: { _s: new Set(), add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); }, contains(c) { return this._s.has(c); } },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
}

const panels = {
    operationsPanel: mkEl({ id: 'operationsPanel' }),
    memoryMetricsPanel: mkEl({ id: 'memoryMetricsPanel' }),
    ownerOperationsPanel: mkEl({ id: 'ownerOperationsPanel' }),
    ownerOperationsCard: mkEl({ id: 'ownerOperationsCard' }),
};
const consoleErrors = [];
const calls = [];
let adminTechnical = null, ownerPayload = null, metricsPayload = null;

function mkRes(status, body) {
    return { ok: status >= 200 && status < 300, status,
        json: () => Promise.resolve(body), clone: () => mkRes(status, body) };
}
function dispatcher(url, opts) {
    const u = String(url);
    calls.push({ url: u, method: (opts && opts.method) || 'GET' });
    if (u.indexOf('/api/admin/operations/technical') === 0) {
        return Promise.resolve(mkRes(200, adminTechnical));
    }
    if (u.indexOf('/api/admin/operations/owner') === 0) {
        return Promise.resolve(mkRes(200, ownerPayload));
    }
    if (u.indexOf('/api/admin/operations/process-reset') === 0) {
        return Promise.resolve(mkRes(200, { status: 'success', data: {
            process: { started_at: '2026-10-26T00:00:00', baseline_reset_at: '2026-10-26T01:00:00' },
            read_failures: { state: 'no_data', total: 0, counts: {} },
            write_failures: { state: 'no_data', total: 0, counts: {} },
        } }));
    }
    if (u.indexOf('/api/admin/memory-metrics') === 0) {
        return Promise.resolve(mkRes(200, metricsPayload));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定 URL' }));
}

const sandbox = {
    console: { log() {}, info() {}, debug() {}, warn() {},
        error(...a) { consoleErrors.push(a.map(String).join(' ')); } },
    setTimeout, clearTimeout,
    localStorage: { _m: { demo_role: 'admin' }, getItem(k) { return (k in this._m) ? this._m[k] : null; },
        setItem(k, v) { this._m[k] = String(v); }, removeItem(k) { delete this._m[k]; } },
    Headers: class Headers { constructor(init) { this._m = new Map(Object.entries(init || {})); }
        has(k) { return this._m.has(k); } set(k, v) { this._m.set(k, v); } get(k) { return this._m.get(k); } },
    fetch: dispatcher,
};
sandbox.window = sandbox;
sandbox.addEventListener = function () {};
sandbox.removeEventListener = function () {};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.open = () => null;
sandbox.document = {
    addEventListener() {},
    getElementById(id) { return panels[id] || null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: mkEl({ tagName: 'BODY' }), readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }
F4.loadF4Domains((dsrc, name) => vm.runInNewContext(dsrc, sandbox, { filename: name }),
    { warn: m => console.log(m) });

const {
    opsStateHtml, memoryMetricsV3Html, operationsPanelHtml, renderOperationsPanel,
    loadOperationsPanel, resetOperationsBaseline, renderMemoryMetrics,
    ownerOperationsPanelHtml, loadOwnerOperationsPanel,
} = sandbox;

async function main() {
    ok('函数已定义', typeof opsStateHtml === 'function'
        && typeof memoryMetricsV3Html === 'function'
        && typeof operationsPanelHtml === 'function'
        && typeof loadOperationsPanel === 'function'
        && typeof resetOperationsBaseline === 'function',
        '缺少 M05 函数');

    // ---- 1. 三态：unknown/no_data/error 不渲染 0% ----
    const unknown = opsStateHtml('unknown');
    const noData = opsStateHtml('no_data');
    const error = opsStateHtml('error', '', '', '', '读取失败');
    ok('unknown/no_data/error 三态文案与 data-state 分开',
        unknown.indexOf('未知') !== -1 && unknown.indexOf('data-state="unknown"') !== -1
        && noData.indexOf('无数据') !== -1 && noData.indexOf('data-state="no_data"') !== -1
        && error.indexOf('读取失败') !== -1 && error.indexOf('data-state="error"') !== -1,
        unknown + ' | ' + noData + ' | ' + error);
    ok('三态不出现 0%', [unknown, noData, error].every(h => h.indexOf('0%') === -1), unknown + noData + error);

    // ---- 2. v3 消费：null 率 → 未知，不渲染 0.00% ----
    const V3 = {
        schema_version: 'v3',
        coverage: { determinable_receipts: 0, eligible_receipts: 0, coverage_rate_pct: null },
        injection_rate: {
            production: { rate_pct: null, unknown_reason: 'no eligible receipts (denominator=0)' },
            eval_arm: { rate_pct: null, unknown_reason: 'no real eval run evidence' },
        },
        effect_ab: { sample_size_total: 0, delta_pp: 0.0, has_memory: { receipts: 0 } },
    };
    const v3NoData = memoryMetricsV3Html({ memory_metrics_v2: V3 });
    ok('v3 消费：coverage/生产/评测臂 null → 未知或无数据，且无 0%',
        v3NoData.indexOf('v3 指标') !== -1
        && v3NoData.indexOf('未知') !== -1
        && v3NoData.indexOf('0.00%') === -1
        && v3NoData.indexOf('无数据（无可确定供应商）') !== -1,
        v3NoData);
    ok('v3 缺失 → 未知（不假装 0）',
        memoryMetricsV3Html({}).indexOf('未知（后端未给出 v3 指标结构）') !== -1,
        memoryMetricsV3Html({}));

    // v3 进入记忆看板渲染
    metricsPayload = {
        status: 'success', memory_enabled: true,
        metrics: Object.assign({ memory_hit: { total_receipts: 0 }, memory_hit_effective: {} },
            { memory_metrics_v2: V3 }),
        ops: { tasks_enabled: null },
    };
    renderMemoryMetrics(metricsPayload);
    ok('记忆看板真实消费 v3（出现 v3 指标区块）',
        panels.memoryMetricsPanel.innerHTML.indexOf('v3 指标') !== -1,
        panels.memoryMetricsPanel.innerHTML.slice(0, 200));

    // ---- 3. 技术 admin 视图 ----
    adminTechnical = {
        status: 'success', role: 'technical',
        process: { started_at: '2026-10-26T00:00:00', baseline_reset_at: '2026-10-26T01:00:00',
            uptime_seconds: 30, counter_scope: 'process', restart_resets_counters: true },
        read_failures: { state: 'no_data', total: 0, counts: {} },
        write_failures: { state: 'ok', total: 2, counts: { ingest: 2 } },
        sync_backlog: { state: 'ok', persistent: true, stats: { pending: 1, dead_letter: 1, total: 2 } },
        tasks: { state: 'ok', enabled: true, stats: { pending: 1 } },
        errors: { state: 'ok', retry_limit: 5,
            dead_letter: [{ id: 7, kind: 'memory_vector_sync', attempt: 5, last_error: 'synthetic failure' }] },
    };
    const tech = operationsPanelHtml(adminTechnical, 'admin');
    ok('技术视图：process 基线可见', tech.indexOf('进程基线') !== -1 && tech.indexOf('重置') !== -1, tech);
    ok('技术视图：读写失败计数可见（写失败非零）',
        tech.indexOf('读失败计数') !== -1 && tech.indexOf('写失败计数') !== -1
        && tech.indexOf('ingest=2') !== -1, tech);
    ok('技术视图：持久 sync backlog + 死信重试可见',
        tech.indexOf('同步 backlog') !== -1 && tech.indexOf('pending=1') !== -1
        && tech.indexOf('synthetic failure') !== -1 && tech.indexOf('重试上限 5') !== -1, tech);
    ok('技术视图不包含 owner 商业视图内容',
        tech.indexOf('商业记忆候选') === -1 && tech.indexOf('近期编辑') === -1, tech);

    // ---- 4. owner 视图 ----
    ownerPayload = { status: 'success', role: 'owner',
        memory_candidates: { state: 'ok', items: [{ vendor: 'Vendor_A', content: 'alias:Item_A' }] },
        edits: { state: 'no_data', items: [] } };
    const own = operationsPanelHtml(ownerPayload, 'owner');
    ok('owner 视图：商业记忆候选/编辑可见',
        own.indexOf('商业记忆候选') !== -1 && own.indexOf('Vendor_A') !== -1
        && own.indexOf('近期编辑') !== -1 && own.indexOf('无数据') !== -1, own);
    ok('owner 视图不包含技术错误/计数',
        own.indexOf('读失败计数') === -1 && own.indexOf('同步 backlog') === -1
        && own.indexOf('死信') === -1, own);

    // ---- 5. 按权限消费：admin → technical；owner → owner；staff 不发请求 ----
    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'admin';
    loadOperationsPanel();
    await new Promise(r => setTimeout(r, 20));
    ok('admin → 只请求 technical 视图',
        calls.length === 1 && calls[0].url.indexOf('/api/admin/operations/technical') === 0,
        JSON.stringify(calls));
    ok('admin 渲染技术内容', panels.operationsPanel.innerHTML.indexOf('进程基线') !== -1,
        panels.operationsPanel.innerHTML.slice(0, 120));

    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'owner';
    loadOperationsPanel();
    await new Promise(r => setTimeout(r, 20));
    ok('owner → 只请求 owner 视图',
        calls.length === 1 && calls[0].url.indexOf('/api/admin/operations/owner') === 0,
        JSON.stringify(calls));
    ok('owner 渲染商业内容且无技术内容',
        panels.operationsPanel.innerHTML.indexOf('商业记忆候选') !== -1
        && panels.operationsPanel.innerHTML.indexOf('读失败计数') === -1,
        panels.operationsPanel.innerHTML.slice(0, 160));

    // ---- 5b. owner 实际入口（analytics sec-eval）：只 owner 请求/可见 ----
    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'owner';
    loadOwnerOperationsPanel();
    await new Promise(r => setTimeout(r, 20));
    ok('owner 入口 → 只请求 owner 视图并渲染商业候选',
        calls.length === 1 && calls[0].url.indexOf('/api/admin/operations/owner') === 0
        && panels.ownerOperationsPanel.innerHTML.indexOf('商业记忆候选') !== -1,
        JSON.stringify(calls) + ' | ' + panels.ownerOperationsPanel.innerHTML.slice(0, 120));
    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'admin';
    loadOwnerOperationsPanel();
    await new Promise(r => setTimeout(r, 20));
    ok('admin 入口 → owner 视图不发请求且卡片隐藏',
        calls.length === 0 && panels.ownerOperationsCard.classList.contains('hide'),
        JSON.stringify(calls));

    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'staff';
    loadOperationsPanel();
    await new Promise(r => setTimeout(r, 20));
    ok('staff → 不发请求，渲染未知无权限', calls.length === 0
        && panels.operationsPanel.innerHTML.indexOf('无运维视图权限') !== -1,
        JSON.stringify(calls));

    // ---- 6. process reset：admin POST 后刷新技术视图 ----
    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'admin';
    resetOperationsBaseline();
    await new Promise(r => setTimeout(r, 30));
    ok('process reset → POST process-reset 后重新 GET technical',
        calls.length === 2 && calls[0].url.indexOf('/api/admin/operations/process-reset') === 0
        && calls[0].method === 'POST'
        && calls[1].url.indexOf('/api/admin/operations/technical') === 0,
        JSON.stringify(calls));

    // ---- 7. 接线 + CSP：锚点存在、按钮 data-csp-click 非 inline ----
    ok('index.html 存在 #operationsPanel 锚点', indexHtml.indexOf('id="operationsPanel"') !== -1, '无锚点');
    const block = indexHtml.split('id="operationsPanel"')[0].split('M05 运维可观测')[1] || '';
    ok('CSP：运维区块按钮用 data-csp-click，不含 inline onclick',
        block.indexOf('data-csp-click') !== -1 && block.indexOf('onclick=') === -1, block);
    ok('index.html 存在 owner 视图锚点且按钮非 inline',
        indexHtml.indexOf('id="ownerOperationsPanel"') !== -1
        && indexHtml.indexOf('id="ownerOperationsCard"') !== -1
        && indexHtml.split('id="ownerOperationsPanel"')[0].split('id="ownerOperationsCard"')[1]
            .indexOf('data-csp-click') !== -1,
        'owner 锚点/绑定缺失');
    const branch = (src + F4.F4_DOMAIN_FILES.map(n =>
        fs.readFileSync(path.join(demoDir, 'static', 'js', n), 'utf8')).join('\n'))
        .split("if (targetId === 'tab-engine')")[1] || '';
    ok('接线：tab-engine 分支调用 loadOperationsPanel()',
        branch.split('}')[0].indexOf('loadOperationsPanel()') !== -1, branch.split('}')[0]);

    ok('全程 console.error 为 0', consoleErrors.length === 0, JSON.stringify(consoleErrors.slice(0, 3)));
    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    process.exit(fail === 0 ? 0 : 1);
}

main();
