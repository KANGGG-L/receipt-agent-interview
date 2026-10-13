// 实时库存与价格表格「空分类」显示口径的验收脚手架。
//
// 缺陷背景：同一 SKU 的分类为空字符串时，实时库存表渲染为英文占位 `N/A`，
// 而编辑弹窗（main.js:6896 `sku.category || '其他'`）与数据导出中心（后端归一为「其他」）
// 均显示「其他」—— 同一指标在不同页面给出不同口径，且 `N/A` 是英文调试占位混入中文 UI。
//
// 本脚手架在 node vm 沙箱里载入**真实 main.js**，调用生产函数 loadInventoryData，
// 让 /api/inventory 返回一条 category='' 的 SKU，断言表格首行渲染的是「其他」而非 `N/A`。
//
// 沙箱内零真实网络：fetch 由本脚本注入的记录器接管。
// 用法（cwd 任意）：node demo/tests/frontend_inventory_category_harness.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const src = fs.readFileSync(mainPath, 'utf8');

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

const tbody = mkEl({ tagName: 'TBODY', id: 'inventoryTableBody' });
const byId = { inventoryTableBody: tbody };

const sandboxWarns = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {},
        warn(...a) { sandboxWarns.push(a.map(String).join(' ')); },
        error(...a) { sandboxWarns.push('[error] ' + a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    // main.js 顶层在 window 上注册全局事件（beforeunload / 键盘委托）。
    // 缺失会让顶层执行中断、后声明的 let/const 停在 TDZ（2026-10-10，
    // 与 frontend_role_gate_harness.js 同因修复）。
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
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
    querySelectorAll() { return []; },
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

// 记录器：/api/inventory 返回一条 category='' 的 SKU；其余端点回空成功。
function dispatcher(url, init) {
    const u = String(url);
    if (u.indexOf('/api/inventory?') === 0) {
        return Promise.resolve(mkRes(200, {
            status: 'success',
            data: [{
                id: 1, name: '商品A', category: '', base_unit: '个',
                current_stock: 42, active: 1, min_stock_alert: 0,
            }],
            meta: {},
        }));
    }
    return Promise.resolve(mkRes(200, { status: 'success', data: [] }));
}

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { sandboxWarns.push('load note: ' + e.message); }
// F4 S4-11：main.js 按域拆分，沙箱按 index.html 加载链追加全部域文件
// （demo/tests/f4_load_domains.js 共享加载器；overrides 可按文件名指定突变体路径做反证）。
require('./f4_load_domains.js').loadF4Domains(
    (dsrc, name) => vm.runInNewContext(dsrc, sandbox, { filename: name }),
    { warn: m => sandboxWarns.push(m),
      overrides: { 'inventory_board.js': process.argv[3] } });

const loadInventoryData = sandbox.loadInventoryData;
ok('F0 loadInventoryData 已导出', typeof loadInventoryData === 'function', typeof loadInventoryData);

loadInventoryData();

// fetch 链有多跳 microtask，用 setTimeout 等其落定
setTimeout(() => {
    const rows = tbody.children.map(c => String(c.innerHTML || ''));
    const html = rows.join('\n');
    ok('C1 空分类 SKU 渲染出一行', rows.length === 1, 'rows=' + rows.length);
    ok('C2 空分类显示为「其他」（与编辑弹窗/导出中心同口径）',
        html.indexOf('其他') !== -1, html.slice(0, 220));
    ok('C3 不再出现英文调试占位 N/A',
        html.indexOf('N/A') === -1, html.slice(0, 220));
    console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures }, null, 0));
    process.exit(fail === 0 ? 0 : 1);
}, 30);
