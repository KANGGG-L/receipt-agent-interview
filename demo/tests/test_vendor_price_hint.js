// -*- coding: utf-8 -*-
// 复核侧「供应商×SKU 历史价格区间」提示验收脚手架（收敛计划 P2-T4 / 概念 C5）。
//
// 在 node vm 沙箱里载入**真实 main.js**，用注入的 fetch 调度器（零真实网络）验证：
//   ① vendorPriceHintHtml()：有历史 → 渲染区间与次数；无历史 / 数字非法 → 空串（隐藏空壳）；
//      区间外 → 带标记与方向；
//   ② refreshVendorPriceHint()：请求打在只读端点 /api/vendor_price_hint 上、数字原样来自
//      后端注入数据（前端不自算区间）、无 SKU 或无供应商时不发请求且清空、
//      失败时隐藏但不静默（console.warn 留痕）；
//   ③ 接线：提示容器在单价单元格内（不新增列）、单价 change 会重取、供应商变化的两处
//      （onchange + 下拉选中）与换绑 SKU 都会重取；
//   ④ 红线：区间提示**不进生成侧 prompt**（生成侧链路文件零引用该端点），
//      明细表仍是 7 列（不改列数/列序）。
//
// 用法（cwd 任意；路径一律由 __dirname 解析，避免本仓「写死 demo/... 只在仓库根通过」的坑）：
//   node demo/tests/test_vendor_price_hint.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const mainPath = process.argv[2] || path.join(demoDir, 'static', 'js', 'main.js');
const indexPath = path.join(demoDir, 'templates', 'index.html');
// 生成侧链路（识别 prompt 组装处）：这些文件必须对该提示零引用。
const GENERATION_SIDE_FILES = [
    path.join(demoDir, 'app', 'services', 'rag.py'),
    path.join(demoDir, 'app', 'chains', 'extract_chain.py'),
    path.join(demoDir, 'app', 'chains', 'supervisor.py'),
];
const src = fs.readFileSync(mainPath, 'utf8');

let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}

// ---- 最小 DOM 面（只支撑提示这条链路） ----
function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {},
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
}

// 复核表单里的供应商输入框（提示的供应商维度来源）
const supplierEl = mkEl({ id: 'inpSupplier', value: '佳記欄' });

const consoleErrors = [];
const consoleWarns = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {},
        warn(...a) { consoleWarns.push(a.map(String).join(' ')); },
        error(...a) { consoleErrors.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    localStorage: {
        _m: {},
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
    getElementById(id) {
        if (id === 'inpSupplier') return supplierEl;
        return null;   // 其余一律返回 null，让生产代码走各自的兜底分支
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: mkEl({ tagName: 'BODY' }), readyState: 'loading',
};

// ---- fetch 调度器：按 URL 分流，绝不发真实请求 ----
let hintMode = 'ok';       // ok | network
let hintResponse = null;
const calls = [];
function mkRes(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        clone: () => mkRes(status, body),
    };
}
function dispatcher(url) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/api/vendor_price_hint') === 0) {
        if (hintMode === 'network') return Promise.reject(new Error('offline'));
        return Promise.resolve(mkRes(200, hintResponse));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定的 URL' }));
}

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

const { vendorPriceHintHtml, refreshVendorPriceHint, refreshAllVendorPriceHints } = sandbox;

// 造一行明细：只有提示链路需要的那三个查找目标。
function mkRow(opts) {
    const o = opts || {};
    const box = mkEl({ className: 'vendor-price-hint-box' });
    const idInput = mkEl({ value: o.skuId == null ? '' : String(o.skuId) });
    const priceInput = mkEl({ value: o.price == null ? '' : String(o.price) });
    const tr = mkEl({
        querySelector(sel) {
            if (sel === '.vendor-price-hint-box') return box;
            if (sel === '.inp-sku-id') return idInput;
            if (sel === '.inp-price') return priceInput;
            return null;
        },
    });
    return { tr, box, idInput, priceInput };
}

async function main() {
    ok('前置: 生产函数已定义', typeof vendorPriceHintHtml === 'function'
        && typeof refreshVendorPriceHint === 'function'
        && typeof refreshAllVendorPriceHints === 'function',
        'render=' + typeof vendorPriceHintHtml + ' refresh=' + typeof refreshVendorPriceHint
        + ' refreshAll=' + typeof refreshAllVendorPriceHints);

    // ---- 1. 纯渲染函数：各种输入形态 ----
    const inRange = vendorPriceHintHtml({ has_history: true, count: 5, low: 12.5, high: 18, out_of_range: false });
    ok('渲染: 有历史 → 显示区间与次数', inRange.indexOf('供应商历史区间') !== -1
        && inRange.indexOf('HK$12.50–18.00') !== -1 && inRange.indexOf('（5 次）') !== -1,
        'html=' + inRange);
    ok('渲染: 区间内不加超出标记', inRange.indexOf('vendor-price-hint-out') === -1,
        'html=' + inRange);

    const outUp = vendorPriceHintHtml({ has_history: true, count: 3, low: 10, high: 20, out_of_range: true, direction: 'up' });
    ok('渲染: 区间外（偏高）→ 带标记与方向', outUp.indexOf('vendor-price-hint-out') !== -1
        && outUp.indexOf('高于区间') !== -1, 'html=' + outUp);
    const outDown = vendorPriceHintHtml({ has_history: true, count: 3, low: 10, high: 20, out_of_range: true, direction: 'down' });
    ok('渲染: 区间外（偏低）→ 方向文案为低于区间', outDown.indexOf('低于区间') !== -1, 'html=' + outDown);

    ok('渲染: 无历史 → 空串（隐藏空壳）', vendorPriceHintHtml({ has_history: false, count: 0 }) === '',
        'html=' + JSON.stringify(vendorPriceHintHtml({ has_history: false, count: 0 })));
    ok('渲染: null / 计数为 0 / 数字非法 → 空串（绝不显示空壳）',
        vendorPriceHintHtml(null) === '' && vendorPriceHintHtml(undefined) === ''
        && vendorPriceHintHtml({ has_history: true, count: 0, low: 1, high: 2 }) === ''
        && vendorPriceHintHtml({ has_history: true, count: 2, low: 'x', high: 2 }) === '',
        'count0/nonnumeric 非空');
    const single = vendorPriceHintHtml({ has_history: true, count: 1, low: 7, high: 7 });
    ok('渲染: 单点历史（low==high）只显示一个数字', single.indexOf('HK$7.00（1 次）') !== -1
        && single.indexOf('HK$7.00–7.00') === -1, 'html=' + single);

    // ---- 2. 端到端：真实 refreshVendorPriceHint + 注入后端响应 ----
    supplierEl.value = '佳記欄';
    hintMode = 'ok';
    hintResponse = { status: 'success', has_history: true, count: 5, low: 12.5, high: 18, out_of_range: false };

    const row = mkRow({ skuId: 42, price: 15 });
    await refreshVendorPriceHint(row.tr);
    ok('端到端: 有历史 → 容器渲染出提示', row.box.innerHTML.indexOf('供应商历史区间') !== -1,
        'html=' + JSON.stringify(row.box.innerHTML));
    ok('端到端: 数字原样来自后端注入数据（前端不自算区间）',
        row.box.innerHTML.indexOf('HK$12.50–18.00') !== -1 && row.box.innerHTML.indexOf('（5 次）') !== -1,
        'html=' + row.box.innerHTML);
    ok('端到端: 请求打在只读端点且带上供应商×SKU×单价三要素',
        calls[calls.length - 1] === '/api/vendor_price_hint?sku_id=42&vendor=%E4%BD%B3%E8%A8%98%E6%AC%84&unit_price=15',
        'url=' + calls[calls.length - 1]);

    // 无历史 → 隐藏（且清掉上一次的结果，绝不残留）
    hintResponse = { status: 'success', has_history: false, count: 0 };
    await refreshVendorPriceHint(row.tr);
    ok('端到端: 无历史 → 隐藏（清空容器，无空壳）', row.box.innerHTML === '',
        'html=' + JSON.stringify(row.box.innerHTML));

    // 区间外（价格改高）→ 标记
    hintResponse = { status: 'success', has_history: true, count: 4, low: 10, high: 20, out_of_range: true, direction: 'up' };
    row.priceInput.value = '35';
    await refreshVendorPriceHint(row.tr);
    ok('端到端: 区间外的单价 → 带标记', row.box.innerHTML.indexOf('vendor-price-hint-out') !== -1
        && row.box.innerHTML.indexOf('高于区间') !== -1, 'html=' + row.box.innerHTML);
    ok('端到端: 超出判定由后端给出（前端只把当前单价带过去）',
        calls[calls.length - 1].indexOf('unit_price=35') !== -1, 'url=' + calls[calls.length - 1]);

    // 无 SKU / 无供应商 → 不发请求 + 清空
    let before = calls.length;
    const noSku = mkRow({ skuId: '', price: 15 });
    noSku.box.innerHTML = '旧内容';
    await refreshVendorPriceHint(noSku.tr);
    ok('边界: 无 SKU 时不发请求且清空容器',
        calls.length === before && noSku.box.innerHTML === '',
        'calls=' + (calls.length - before) + ' html=' + JSON.stringify(noSku.box.innerHTML));

    supplierEl.value = '';
    before = calls.length;
    const noVendor = mkRow({ skuId: 9, price: 15 });
    noVendor.box.innerHTML = '旧内容';
    await refreshVendorPriceHint(noVendor.tr);
    ok('边界: 无供应商时不发请求且清空容器（供应商是提示的必需维度）',
        calls.length === before && noVendor.box.innerHTML === '',
        'calls=' + (calls.length - before) + ' html=' + JSON.stringify(noVendor.box.innerHTML));
    supplierEl.value = '佳記欄';

    // 网络失败 → 隐藏 + 留痕（不静默）
    before = consoleWarns.length;
    hintMode = 'network';
    const bad = mkRow({ skuId: 7, price: 15 });
    bad.box.innerHTML = '旧内容';
    await refreshVendorPriceHint(bad.tr);
    ok('降级: 网络失败 → 隐藏提示且不留空壳', bad.box.innerHTML === '',
        'html=' + JSON.stringify(bad.box.innerHTML));
    ok('降级: 失败留痕（console.warn），不静默吞掉',
        consoleWarns.length > before && consoleWarns[consoleWarns.length - 1].indexOf('vendor-price-hint') !== -1,
        'warns=' + JSON.stringify(consoleWarns.slice(before)));
    hintMode = 'ok';

    // 整表重取：两个明细行都拿到提示（动态行也覆盖）
    const rows = [mkRow({ skuId: 1, price: 5 }), mkRow({ skuId: 2, price: 5 })];
    const tbody = mkEl({ querySelectorAll: () => rows.map(r => r.tr) });
    const realGetById = sandbox.document.getElementById;
    sandbox.document.getElementById = (id) => (id === 'itemTableBody' ? tbody : realGetById(id));
    hintResponse = { status: 'success', has_history: true, count: 2, low: 1, high: 9, out_of_range: false };
    refreshAllVendorPriceHints();
    await new Promise(r => setTimeout(r, 10));
    sandbox.document.getElementById = realGetById;
    ok('接线: 整表重取覆盖所有明细行',
        rows.every(r => r.box.innerHTML.indexOf('供应商历史区间') !== -1),
        'html=' + rows.map(r => JSON.stringify(r.box.innerHTML)).join(' | '));

    // ---- 3. 源码级接线校验 ----
    const priceCell = /<div class="price-input-wrap">[\s\S]*?<\/td>/.exec(src);
    ok('接线: 提示容器渲染在单价单元格内（不新增列、不改列序）',
        !!priceCell && priceCell[0].indexOf('vendor-price-hint-box') !== -1,
        '单价单元格片段未包含提示容器');

    // 单价 change → 重取：绑定在 appendTableRow（真实建行处），沙箱里的假行拿不到该监听，
    // 故此处做源码级校验（连同上面的「端到端: 改价后重取」行为断言一起构成完整证据）。
    const appendRow = (src.split('function appendTableRow(item = {}) {', 2)[1] || '')
        .split('\nfunction ')[0];
    ok('接线: 单价 change 会重取该行提示（价格是判定输入）',
        /if \(priceInput\) priceInput\.addEventListener\('change', \(\) => refreshVendorPriceHint\(tr\)\);/
            .test(appendRow) && appendRow.indexOf('refreshVendorPriceHint(tr);') !== -1,
        'appendTableRow 未把单价 change 接到 refreshVendorPriceHint');

    const selSupplier = (src.split('function selectSupplierItem(', 2)[1] || '').split('\nfunction ')[0];
    ok('接线: 供应商下拉选中后整表重取（程序化赋值不触发 change）',
        selSupplier.indexOf('refreshAllVendorPriceHints()') !== -1,
        'selectSupplierItem 未调用 refreshAllVendorPriceHints');

    const selSku = (src.split('function selectSkuItem(', 2)[1] || '').split('\nfunction ')[0];
    ok('接线: 换绑 SKU 后重取该行提示',
        selSku.indexOf("refreshVendorPriceHint(itemElem.closest('tr'))") !== -1,
        'selectSkuItem 未调用 refreshVendorPriceHint');

    const renderForm = (src.split('function renderEditForm(data) {', 2)[1] || '').split('\nfunction ')[0];
    ok('接线: 供应商输入框 change → 整表重取',
        renderForm.indexOf("getElementById('inpSupplier')") !== -1
        && renderForm.indexOf('refreshAllVendorPriceHints') !== -1,
        'renderEditForm 未绑定供应商 change');

    ok('边界: 供应商名读的是实时输入值（不是渲染时的快照）',
        src.indexOf('function currentSupplierForHint()') !== -1
        && src.indexOf("getElementById('inpSupplier')") !== -1,
        '未找到实时供应商读取');

    // ---- 4. 红线 ----
    const leaked = GENERATION_SIDE_FILES.filter(p => fs.readFileSync(p, 'utf8').indexOf('vendor_price_hint') !== -1);
    ok('红线: 区间提示不进生成侧 prompt（生成侧链路零引用该端点）', leaked.length === 0,
        '以下生成侧文件引用了该端点：' + JSON.stringify(leaked));

    const html = fs.readFileSync(indexPath, 'utf8');
    const itemTableHead = html.split('id="itemTableBody"', 1)[0]
        .split('<table class="data-table receipt-item-table">').pop();
    const thCount = (itemTableHead.match(/<th\b/g) || []).length;
    ok('红线: 明细表仍是 7 列（未新增列/改列序）', thCount === 7, 'th 数量=' + thCount);
    ok('接线: 单价表头说明区间来源（发现性）',
        /<th[^>]*>\s*单价 \(\$\)<\/th>/.test(itemTableHead)
        && /title="[^"]*供应商历史区间[^"]*"/.test(itemTableHead),
        '单价表头缺少来源说明');

    ok('静默: 全程 console.error 为 0（无未捕获异常被吞）', consoleErrors.length === 0,
        'consoleErrors=' + JSON.stringify(consoleErrors));

    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(fail ? 1 : 0);
}

main().catch(e => {
    console.log('FAIL harness 自身异常: ' + (e && e.stack ? e.stack : e));
    process.exit(1);
});
