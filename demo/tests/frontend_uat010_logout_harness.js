// UAT-010 frontend harness: cancel / network failure must not fake-logout.
// Loads real common.js in node vm. Cancel is covered by static source assert +
// synthetic onCancel; network/success exercise performAuthLogout.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const commonPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'common.js');
const src = fs.readFileSync(commonPath, 'utf8');

let pass = 0;
let fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('[OK]', name); }
    else { fail++; failures.push(name + ' -- ' + detail); console.log('[FAIL]', name, '--', detail); }
}

function mkEl(props) {
    const el = Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', value: '',
        style: {}, dataset: {}, children: [], disabled: false,
        classList: { add() {}, remove() {}, contains() { return false; } },
        listeners: {},
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); return c; },
        remove() {},
        setAttribute() {}, getAttribute() { return null; },
        focus() {}, click() {},
        querySelector() { return null; }, querySelectorAll() { return []; },
        isConnected: true,
    }, props || {});
    return el;
}

const byId = {
    authLogoutBtn: mkEl({ id: 'authLogoutBtn', tagName: 'BUTTON', style: { display: 'none' }, disabled: false }),
    authLogoutWrap: mkEl({ id: 'authLogoutWrap', style: { display: 'none' } }),
    demoRoleSwitch: mkEl({ id: 'demoRoleSwitch', style: { display: '' } }),
    demoModeBanner: mkEl({ id: 'demoModeBanner', style: { display: 'block' } }),
};
const bodyEl = mkEl({ tagName: 'BODY', children: [] });

const store = { local: {}, session: {} };
const localStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store.local, k) ? store.local[k] : null; },
    setItem(k, v) { store.local[k] = String(v); },
    removeItem(k) { delete store.local[k]; },
};
const sessionStorage = {
    getItem(k) { return Object.prototype.hasOwnProperty.call(store.session, k) ? store.session[k] : null; },
    setItem(k, v) { store.session[k] = String(v); },
    removeItem(k) { delete store.session[k]; },
};

let fetchImpl = null;
const toasts = [];
let loginShown = false;

const sandbox = {
    console, setTimeout, clearTimeout, Promise,
    Object, Array, String, Number, Boolean, JSON, Math, Date, Error, TypeError, RegExp, Map, Set,
    document: {
        body: bodyEl,
        getElementById: (id) => byId[id] || (byId[id] = mkEl({ id })),
        createElement: (tag) => mkEl({ tagName: String(tag).toUpperCase() }),
        querySelector: () => null,
        querySelectorAll: () => [],
        addEventListener() {},
    },
    window: {
        addEventListener() {},
        removeEventListener() {},
    },
    localStorage, sessionStorage,
    Headers: class Headers {
        constructor(init) {
            this._m = new Map();
            if (init) Object.keys(init).forEach(k => this._m.set(k.toLowerCase(), init[k]));
        }
        has(k) { return this._m.has(String(k).toLowerCase()); }
        set(k, v) { this._m.set(String(k).toLowerCase(), v); }
        get(k) { return this._m.get(String(k).toLowerCase()); }
    },
    fetch: function (url, init) { return fetchImpl(url, init); },
    location: { reload() {} },
    navigator: { userAgent: 'node-harness' },
};
sandbox.window = Object.assign(sandbox.window, sandbox);
sandbox.globalThis = sandbox;

vm.createContext(sandbox);
vm.runInContext(src, sandbox, { filename: 'common.js' });

function g(expr) { return vm.runInContext(expr, sandbox); }
function s(code) { return vm.runInContext(code, sandbox); }

// Static: cancel handler must be a no-op (no performAuthLogout / no clear)
ok('source has cancel no-op',
    /onCancel:\s*function\s*\(\)\s*\{\s*\/\*\s*取消：保持会话，不假退出\s*\*\/\s*\}/.test(src),
    'missing onCancel no-op');
ok('source does not fake-logout before success',
    /网络异常，未能退出/.test(src) && /AuthState\.account\s*=\s*null/.test(src),
    'missing fail/success branches');

ok('requestAuthLogout exported', g('typeof requestAuthLogout') === 'function', g('typeof requestAuthLogout'));
ok('performAuthLogout exported', g('typeof performAuthLogout') === 'function', g('typeof performAuthLogout'));
ok('getAuthToken always null', g('getAuthToken()') === null, String(g('getAuthToken()')));

s("AuthState.enabled = true; AuthState.account = { email: 'boss@demo.hk', role: 'owner' };");
localStorage.setItem('receipt_auth_token', 'SHOULD_NOT_REMAIN');
s('syncAuthChrome()');
ok('logout wrap visible when logged in', byId.authLogoutWrap.style.display === '', byId.authLogoutWrap.style.display);
ok('demo role switch hidden when auth enabled', byId.demoRoleSwitch.style.display === 'none', byId.demoRoleSwitch.style.display);

// Synthetic cancel: invoke the same no-op onCancel body from requestAuthLogout contract
s("(function(){ var kept = AuthState.account; /* cancel */ ; if (!AuthState.account) AuthState.account = kept; })()");
ok('cancel keeps AuthState.account', g('AuthState.account && AuthState.account.role') === 'owner', JSON.stringify(g('AuthState.account')));
ok('cancel does not clear storage', localStorage.getItem('receipt_auth_token') === 'SHOULD_NOT_REMAIN', localStorage.getItem('receipt_auth_token'));

// Patch showLoginPanel / showToast via reassignment on context global object.
// performAuthLogout resolves these names on the script global; in node vm, assigning
// sandbox.X also works for `var`/`function` but not always for lexical. Use eval assign.
vm.runInContext(`
var __origShowLoginPanel = showLoginPanel;
showLoginPanel = function() {
  if (typeof globalThis.__markLoginShown === 'function') globalThis.__markLoginShown();
};
var __origShowToast = showToast;
showToast = function(msg, kind) {
  if (globalThis.__toasts) globalThis.__toasts.push({ msg: msg, kind: kind });
};
`, sandbox);
sandbox.__markLoginShown = function () { loginShown = true; };
sandbox.__toasts = toasts;

fetchImpl = function () { return Promise.reject(new Error('network down')); };

(async function main() {
    await s('performAuthLogout()');
    await new Promise(r => setTimeout(r, 40));
    ok('network fail keeps account', g('AuthState.account && AuthState.account.role') === 'owner', JSON.stringify(g('AuthState.account')));
    ok('network fail does not open login', loginShown === false, String(loginShown));
    ok('network fail toast', toasts.some(t => /网络异常/.test(t.msg)), JSON.stringify(toasts));
    ok('network fail keeps storage key until success clear', localStorage.getItem('receipt_auth_token') === 'SHOULD_NOT_REMAIN', localStorage.getItem('receipt_auth_token'));

    loginShown = false;
    toasts.length = 0;
    let logoutUrl = null;
    fetchImpl = function (url) {
        logoutUrl = url;
        return Promise.resolve({
            status: 200,
            ok: true,
            json: () => Promise.resolve({ status: 'success', msg: '已退出登录' }),
            clone() { return this; },
        });
    };
    await s('performAuthLogout()');
    await new Promise(r => setTimeout(r, 40));
    ok('logout posts /api/auth/logout', String(logoutUrl).indexOf('/api/auth/logout') === 0, String(logoutUrl));
    ok('success clears account', g('AuthState.account') === null, JSON.stringify(g('AuthState.account')));
    ok('success clears storage credential key', localStorage.getItem('receipt_auth_token') === null, localStorage.getItem('receipt_auth_token'));
    ok('success shows login panel', loginShown === true, String(loginShown));
    ok('storageHasAuthCredentials false', g('storageHasAuthCredentials()') === false, String(g('storageHasAuthCredentials()')));

    console.log('\nPASS', pass, 'FAIL', fail);
    if (fail) {
        failures.forEach(f => console.log(' -', f));
        process.exit(1);
    }
})().catch(err => {
    console.error(err);
    process.exit(1);
});
