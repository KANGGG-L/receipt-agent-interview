// 管理台「解析超时清扫阈值」（settings 键 parsing_timeout_seconds）端到端接线脚手架（P28-AU2）。
//
// why 需要它：该控件是**数值型**，走 ADMIN_SETTINGS_FIELDS 一张表驱动；「整个文件里搜得到
// id 字符串」这类弱断言无法回答「这一条路径到底接线了没有」（id 出现在注释里、出现在别的
// 函数里都会判成命中）。本脚手架在 node vm 沙箱里载入**真实 main.js**，用 index.html 里
// **真实存在的控件 id** 建最小 DOM，把 GET / PUT /api/admin/settings 交给注入的调度器
// （零真实网络），然后**真的调用**三个生产函数，断言该键在三条路径上都真的来回：
//   load    : 服务端值 → 控件 value；且服务端不给该键时控件保持空（证明是数据驱动、不硬编码）；
//   collect : 控件值（含小数 77.6）→ payload（int 类型必须取整成 78）；控件留空 → 不下发该键；
//   reset   : 控件值 → payload 回到服务端给的缺省值（「恢复缺省值」按钮文案与实际一致）。
// 另加防恒真的自校准：不存在的 id 在 DOM 里查不到、其键不在 payload 里。
// 末尾另报「console 错误数」（要求 0）—— 被测代码里被静默吞掉的异常只会在这里现形。
//
// 只读、不碰任何 live 数据（fetch 全部被调度器接管）。
// 用法（cwd 任意）：node demo/tests/frontend_admin_settings_parsing_timeout_harness.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。stdout 末行 JSON 供 pytest 解析。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const htmlPath = path.join(__dirname, '..', 'templates', 'index.html');
const src = fs.readFileSync(mainPath, 'utf8');
const html = fs.readFileSync(htmlPath, 'utf8');

const FIELD_ID = 'setParsingTimeoutSeconds';
const KEY = 'parsing_timeout_seconds';
const ABSENT_ID = 'setParsingTimeoutNotExist';
const ABSENT_KEY = 'parsing_timeout_not_exist';

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; failures.push(name + ': ' + detail); console.log('FAIL ' + name + ' -- ' + detail); }
}

// ---- 最小 DOM 面（照抄既有 harness 的 mkEl，只加 value/checked 所需字段）----
function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        checked: false, tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {},
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            toggle(c, on) { if (on === undefined) { this._s.has(c) ? this._s.delete(c) : this._s.add(c); } else if (on) { this._s.add(c); } else { this._s.delete(c); } },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        removeChild(c) { this.children = this.children.filter(x => x !== c); return c; },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
}

// 页面真实控件集合：index.html 里所有 id= 都建一个元素 —— 控件**没写进模板**时
// getElementById 返回 null，下面的往返断言自然会失败（这正是要钉住的接线）。
const els = {};
for (const m of html.matchAll(/id="([^"]+)"/g)) {
    if (!els[m[1]]) els[m[1]] = mkEl({ id: m[1] });
}
const bodyEl = mkEl({ tagName: 'BODY' });

// ---- fetch 调度器：按 URL 分流，绝不发真实请求 ----
let serverData = {};      // PUT 之外的 GET /api/admin/settings 返回
let serverDefaults = {};  // 缺省值（reset 用）
let lastPutBody = null;
const calls = [];
function mkRes(status, body) {
    return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body), clone: () => mkRes(status, body) };
}
function dispatcher(url, init) {
    const u = String(url);
    calls.push(u);
    const method = ((init && init.method) || 'GET').toUpperCase();
    if (u.indexOf('/api/admin/settings') === 0) {
        if (method === 'PUT') {
            lastPutBody = JSON.parse((init && init.body) || '{}');
            return Promise.resolve(mkRes(200, { status: 'success' }));
        }
        return Promise.resolve(mkRes(200, { status: 'success', data: serverData, defaults: serverDefaults }));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定的 URL' }));
}

const store = { demo_role: 'admin' };
// 与既有兄弟脚手架（frontend_pending_memory_badge_harness / test_memory_metrics_panel 等）
// 同一机制：捕获沙箱内 console.error 并在末尾报「console 错误数」（要求 0）。
// why 必须计：被测 main.js 里被静默吞掉的异常（本仓 ③ 类高发缺陷）只会在这里现形。
const consoleErrors = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {}, warn() {},
        error(...a) { consoleErrors.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    // main.js 顶层在 window 上注册全局事件（beforeunload / 键盘委托）。
    // 缺失会让顶层执行中断、后声明的 let/const 停在 TDZ（2026-10-10，
    // 与 frontend_role_gate_harness.js 同因修复）。
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
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
sandbox.document = {
    addEventListener() {}, removeEventListener() {},
    getElementById(id) { return els[id] || null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: bodyEl, readyState: 'loading',
};
try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }
// F4 S4-11：main.js 按域拆分，沙箱按 index.html 加载链追加全部域文件
// （demo/tests/f4_load_domains.js 共享加载器；overrides 可按文件名指定突变体路径做反证）。
require('./f4_load_domains.js').loadF4Domains(
    (dsrc, name) => vm.runInNewContext(dsrc, sandbox, { filename: name }),
    { warn: m => console.log(m) });

const { loadAdminSettings, _collectAdminSettingsPayload, resetAdminSettingsToDefaults } = sandbox;
ok('前置: 三个生产函数已定义',
    typeof loadAdminSettings === 'function' && typeof _collectAdminSettingsPayload === 'function'
    && typeof resetAdminSettingsToDefaults === 'function',
    'load=' + typeof loadAdminSettings + ' collect=' + typeof _collectAdminSettingsPayload
    + ' reset=' + typeof resetAdminSettingsToDefaults);

const el = els[FIELD_ID] || null;
ok('前置: 模板里有该控件元素（getElementById 能取到）', !!el,
    'index.html 里没有 id="' + FIELD_ID + '" -> collect 读不到，保存必然不生效');

const tick = () => new Promise(r => setTimeout(r, 0));
async function runLoad() {
    const before = calls.length;
    loadAdminSettings();
    await tick(); await tick();
    return calls.length - before;
}

(async () => {
    // 1) load：服务端值 → 控件
    serverData = { [KEY]: 420 };
    serverDefaults = { [KEY]: 300 };
    const n = await runLoad();
    ok('load: 发出 GET /api/admin/settings', n >= 1, '调用次数=' + n);
    ok('load: 服务端值回显到控件', el && el.value === '420', '控件 value=' + (el && el.value));

    // 2) load 自校准：服务端不给该键时控件保持空（证明是数据驱动，不是硬编码 420）
    serverData = {};
    el.value = '';
    await runLoad();
    ok('load 自校准: 服务端不给该键则控件保持空', el.value === '', '控件 value=' + el.value);

    // 3) collect：控件值（小数）→ payload，且 int 类型取整
    el.value = '77.6';
    const p1 = _collectAdminSettingsPayload();
    ok('collect: 下发该键', p1 && p1[KEY] === 78, 'payload[' + KEY + ']=' + (p1 && p1[KEY]));
    ok('collect: int 类型取整为整数', p1 && Number.isInteger(p1[KEY]), '类型=' + typeof (p1 && p1[KEY]));

    // 4) collect：留空不覆盖（后端缺省回退）
    el.value = '';
    const p2 = _collectAdminSettingsPayload();
    ok('collect: 留空则不下发该键', p2 && !(KEY in p2), 'payload 含该键=' + (p2 && (KEY in p2)));

    // 5) reset：控件值 → payload 回到服务端给的缺省值
    serverData = { [KEY]: 420 };
    serverDefaults = { [KEY]: 300 };
    await runLoad();                 // 装载 _adminSettingsDefaults
    el.value = '999';
    lastPutBody = null;
    resetAdminSettingsToDefaults();
    await tick(); await tick();
    ok('reset: PUT 已发出', lastPutBody !== null, 'lastPutBody=' + JSON.stringify(lastPutBody));
    ok('reset: 该键回到缺省值 300', lastPutBody && lastPutBody[KEY] === 300,
        'PUT 里的值=' + (lastPutBody && lastPutBody[KEY]));

    // 6) 自校准：不存在的 id / 键在任何一条路径上都不命中
    ok('自校准: 不存在的 id 查不到元素', sandbox.document.getElementById(ABSENT_ID) === null,
        '竟然取到了元素');
    ok('自校准: 不存在的键不在 payload 里',
        !(ABSENT_KEY in _collectAdminSettingsPayload()), '不存在的键被下发了');

    // 7) 静默检查：全程 console.error 必须为 0（被测代码里的异常不得被吞掉）
    ok('静默: 全程 console.error 为 0（无未捕获异常被吞）', consoleErrors.length === 0,
        'consoleErrors=' + JSON.stringify(consoleErrors));

    console.log(JSON.stringify({ pass, fail, failures, consoleErrors: consoleErrors.length }));
    process.exit(fail === 0 ? 0 : 1);
})().catch(e => {
    console.log('FAIL 脚手架异常: ' + (e && e.stack || e));
    console.log(JSON.stringify({ pass, fail: fail + 1, failures: failures.concat(String(e)),
                                 consoleErrors: consoleErrors.length }));
    process.exit(1);
});
