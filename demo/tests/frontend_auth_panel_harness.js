// 登录面板首屏判定（脆弱耦合修复）的验收脚手架。
//
// 背景：/api/auth/me 在鉴权白名单内、匿名恒 200，`decideAuthProbe` 原先对任何 2xx 直接
// 放行，于是「未登录 → 登录面板」实际是靠首屏数据端点（/api/receipts 等）返回 401 →
// onUnauthorized 才弹出 —— 判定退化成依赖后续请求时序的偶发行为。
//
// 修复口径：首屏判定直接使用 /api/auth/me 已回显的 auth_enabled 与 account：
//   auth_enabled=true 且 account=null → 直接显示登录面板；
//   auth_enabled=false（开发态）→ 免登录直通，绝不弹面板；
//   401 → onUnauthorized 兜底路径**保持不变**。
//
// 本脚手架在 node vm 沙箱里载入**真实 main.js**，调用生产函数本体
// （decideAuthProbe / probeAuthAndEnter / showLoginPanel / createAuthFetchWrapper），
// 沙箱内 fetch 由本脚本接管，只按模式回响应，绝不发真实请求。
//
// 用法（cwd 任意）：node demo/tests/frontend_auth_panel_harness.js [main.js 路径]
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
const OVERLAY_ID = 'authLoginOverlay';
const bodyEl = { tagName: 'BODY', id: '', className: '', children: [], innerText: '', textContent: '', style: {}, dataset: {} };

function mkEl(props) {
    const el = Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', value: '',
        style: {}, dataset: {}, children: [], disabled: false, type: '',
        isConnected: false, parentElement: null, parentNode: null,
        classList: mkClassList(),
        listeners: {},
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; },
        removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
        remove() {
            this._removed = true;
            if (this.parentElement && this.parentElement.removeChild) this.parentElement.removeChild(this);
        },
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k] != null ? this[k] : null; },
        focus() {}, click() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        closest() { return null; },
    }, props || {});
    el.innerHTML = '';
    return el;
}
// bodyEl 也需要 appendChild/removeChild：直接复用 mkEl 的行为
Object.assign(bodyEl, {
    appendChild(c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
});

const byId = {};
function getEl(id) {
    for (let i = 0; i < bodyEl.children.length; i++) {
        if (bodyEl.children[i].id === id) return bodyEl.children[i];
    }
    if (!byId[id]) byId[id] = mkEl({ id: id });
    return byId[id];
}

// ---- fetch 分发器：按模式回响应，绝不发真实请求 ----
let mode = 'unauth_enabled';
const calls = [];
function mkRes(status, bodyObj) {
    return {
        ok: status >= 200 && status < 300,
        status: status,
        json: () => Promise.resolve(bodyObj),
        clone: () => mkRes(status, bodyObj),
    };
}
function dispatcher(url) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/api/auth/me') === 0) {
        if (mode === 'me_401') return Promise.resolve(mkRes(401, { status: 'error', msg: '请先登录' }));
        if (mode === 'unauth_enabled') {
            return Promise.resolve(mkRes(200, { status: 'success', auth_enabled: true, account: null, identity_source: 'missing' }));
        }
        if (mode === 'dev_disabled') {
            return Promise.resolve(mkRes(200, { status: 'success', auth_enabled: false, account: null, identity_source: 'missing' }));
        }
        // authed
        return Promise.resolve(mkRes(200, {
            status: 'success', auth_enabled: true,
            account: { email: 'owner@demo.local', role: 'owner' }, identity_source: 'header',
        }));
    }
    // 首屏数据端点：鉴权开启且未登录时真实服务会 401，这里按模式回
    if (mode === 'unauth_enabled' || mode === 'me_401') return Promise.resolve(mkRes(401, { detail: '请先登录' }));
    return Promise.resolve(mkRes(200, { status: 'success', data: [] }));
}

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
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.document = {
    addEventListener() {},
    getElementById: getEl,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement(tag) { return mkEl({ tagName: String(tag || 'div').toUpperCase() }); },
    documentElement: { style: { setProperty() {} } },
    body: bodyEl,
    readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { sandboxWarns.push('load note: ' + e.message); }

const decideAuthProbe = sandbox.decideAuthProbe;
const probeAuthAndEnter = sandbox.probeAuthAndEnter;
const createAuthFetchWrapper = sandbox.createAuthFetchWrapper;

ok('P0 生产函数均已导出',
    typeof decideAuthProbe === 'function' && typeof probeAuthAndEnter === 'function'
    && typeof createAuthFetchWrapper === 'function',
    [typeof decideAuthProbe, typeof probeAuthAndEnter, typeof createAuthFetchWrapper].join(','));

// ---- A. 纯函数判据 ----
ok('A1 鉴权开启 + 无身份 → 需要登录（不再依赖后续请求时序）',
    decideAuthProbe(200, { auth_enabled: true, account: null }) === 'login',
    decideAuthProbe(200, { auth_enabled: true, account: null }));
ok('A2 鉴权开启 + 有身份 → 正常进入',
    decideAuthProbe(200, { auth_enabled: true, account: { email: 'a@b.c', role: 'owner' } }) === 'proceed',
    decideAuthProbe(200, { auth_enabled: true, account: { email: 'a@b.c', role: 'owner' } }));
ok('A3 开发态（auth_enabled=false，account=null）→ 免登录直通（既有行为不变）',
    decideAuthProbe(200, { auth_enabled: false, account: null }) === 'proceed',
    decideAuthProbe(200, { auth_enabled: false, account: null }));
ok('A4 401 → 登录（兜底路径不变）',
    decideAuthProbe(401, { auth_enabled: false, account: null }) === 'login',
    decideAuthProbe(401, null));
ok('A5 网络层/非 2xx/空 body → 不阻断进入',
    decideAuthProbe(0, null) === 'proceed' && decideAuthProbe(500, null) === 'proceed'
    && decideAuthProbe(200, null) === 'proceed',
    [decideAuthProbe(0, null), decideAuthProbe(500, null), decideAuthProbe(200, null)].join(','));

// ---- B. 首屏行为：面板是否真的出现 ----
function overlayShowing() {
    return bodyEl.children.some(c => c && c.id === OVERLAY_ID);
}
function tick() { return new Promise(r => setTimeout(r, 0)); }
function runProbe(m) {
    mode = m;
    // 每个用例前先把面板状态归零（hideLoginPanel 走生产实现）
    if (typeof sandbox.hideLoginPanel === 'function') sandbox.hideLoginPanel();
    return Promise.resolve(probeAuthAndEnter(false)).then(tick).then(tick);
}

runProbe('unauth_enabled').then(() => {
    ok('B1 auth_enabled=true 且 account=null → 首屏直接显示登录面板',
        overlayShowing(), 'body ids=' + JSON.stringify(bodyEl.children.map(c => c.id)));
    ok('B2 该场景下 AuthState 未被误标为「已进入」（account 仍为空）',
        vm.runInContext('AuthState.account', sandbox) === null,
        JSON.stringify(vm.runInContext('JSON.parse(JSON.stringify(AuthState))', sandbox)));
    return runProbe('dev_disabled');
}).then(() => {
    ok('B3 开发态（auth_enabled=false）不显示登录面板（免登录直通回归）',
        !overlayShowing(), 'body ids=' + JSON.stringify(bodyEl.children.map(c => c.id)));
    return runProbe('authed');
}).then(() => {
    ok('B4 已登录（account 非空）不显示登录面板',
        !overlayShowing(), 'body ids=' + JSON.stringify(bodyEl.children.map(c => c.id)));
    return runProbe('me_401');
}).then(() => {
    ok('B5 /me 返回 401 → 仍然显示登录面板（兜底路径不变）',
        overlayShowing(), 'body ids=' + JSON.stringify(bodyEl.children.map(c => c.id)));

    // ---- C. 401 → onUnauthorized 兜底路径结构性不变 ----
    let hit = 0;
    const wrapped = createAuthFetchWrapper(
        () => Promise.resolve(mkRes(401, { detail: '请先登录' })),
        { getToken: () => null, onUnauthorized: () => { hit++; } }
    );
    return wrapped('/api/receipts', {}).then(() => {
        ok('C1 /api/ 响应 401 仍触发 onUnauthorized（面板兜底不变）', hit === 1, 'hit=' + hit);
        return wrapped('/api/auth/login', {});
    }).then(() => {
        ok('C2 登录端点自身 401 不回弹面板（豁免路径不变）', hit === 1, 'hit=' + hit);
        return wrapped('https://evil.example/x', {});
    }).then(() => {
        ok('C3 非 /api/ 外链 401 不触发（不变）', hit === 1, 'hit=' + hit);

        console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures }, null, 0));
        process.exit(fail === 0 ? 0 : 1);
    });
}).catch(e => {
    console.log(JSON.stringify({ pass: pass, fail: fail + 1,
        failures: failures.concat(['harness 自身异常: ' + (e && e.stack || e)]) }, null, 0));
    process.exit(1);
});
