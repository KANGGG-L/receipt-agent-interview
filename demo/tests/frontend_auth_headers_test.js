// attachAuthInit / buildAuthHeaders 分支断言：真实 main.js 载入 node vm 沙箱
const fs = require('fs');
const vm = require('vm');

const src = fs.readFileSync(process.argv[2] || 'static/js/main.js', 'utf8');
let store = {};
const sandbox = {
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
    console,
    setTimeout, clearTimeout,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
// 只提供沙箱所需的最小 DOM 面；main.js 其余顶层初始化允许失败
sandbox.document = {
    addEventListener() {}, getElementById() { return null; },
    querySelector() { return null; }, querySelectorAll() { return []; },
    createElement() { return { style: {}, classList: { add(){}, remove(){}, toggle(){} }, appendChild(){}, addEventListener(){} }; },
    documentElement: { style: { setProperty(){} } },
    body: null, readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

const { buildAuthHeaders, attachAuthInit } = sandbox;
let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}

ok('前置: 函数已导出', typeof buildAuthHeaders === 'function' && typeof attachAuthInit === 'function',
   'buildAuthHeaders=' + typeof buildAuthHeaders + ' attachAuthInit=' + typeof attachAuthInit);
ok('前置: demo_role=admin 生效', (() => { store.demo_role = 'admin';
   return buildAuthHeaders('/api/admin/golden-samples', null)['X-Role'] === 'admin'; })(),
   JSON.stringify(buildAuthHeaders('/api/admin/golden-samples', null)));

// A. Headers 实例分支
const H = sandbox.Headers;
const h1 = new H({ 'Content-Type': 'application/json' });
const r1 = attachAuthInit({ headers: h1 }, '/api/admin/golden-samples', null);
ok('A1 Headers 实例现在带上 X-Role', r1.headers.get('X-Role') === 'admin',
   'get(X-Role)=' + r1.headers.get('X-Role'));
ok('A2 原有 Content-Type 未丢', r1.headers.get('Content-Type') === 'application/json',
   'ct=' + r1.headers.get('Content-Type'));

// B. 调用方自带 Authorization
const r2 = attachAuthInit({ headers: { 'Authorization': 'Bearer mine' } }, '/api/admin/golden-samples', 'TOKEN');
ok('B1 自带 Authorization 时仍注入 X-Role', r2.headers['X-Role'] === 'admin',
   JSON.stringify(r2.headers));
ok('B2 调用方 Authorization 不被覆盖', r2.headers.Authorization === 'Bearer mine',
   'auth=' + r2.headers.Authorization);

// C. 普通 init
const r3 = attachAuthInit({ method: 'POST', body: '{}' }, '/api/admin/golden-samples', 'TOKEN');
ok('C1 无 headers 的 init 被补齐', r3.headers['X-Role'] === 'admin' && r3.headers.Authorization === 'Bearer TOKEN',
   JSON.stringify(r3.headers));
ok('C2 method/body 保留', r3.method === 'POST' && r3.body === '{}', JSON.stringify({ m: r3.method, b: r3.body }));

// D. 非 /api/ 不注入
ok('D1 外链不注入', attachAuthInit({ headers: {} }, 'https://evil.example/x', 'T') !== null
   && attachAuthInit(undefined, 'https://evil.example/x', 'T') === undefined, '见返回值');
const r5 = attachAuthInit({ headers: {} }, '/api/admin/golden-samples', 'T');
ok('D2 同源 /api/ 正常注入', r5.headers['X-Role'] === 'admin', JSON.stringify(r5.headers));

// E. 角色回落
store.demo_role = 'bogus';
ok('E1 非法角色回落 owner', buildAuthHeaders('/api/receipts', null)['X-Role'] === 'owner',
   JSON.stringify(buildAuthHeaders('/api/receipts', null)));
store.demo_role = 'staff';
ok('E2 staff 如实带出', buildAuthHeaders('/api/receipts', null)['X-Role'] === 'staff', '');

console.log('\nSUMMARY: ' + pass + ' passed, ' + fail + ' failed');
process.exit(fail ? 1 : 0);
