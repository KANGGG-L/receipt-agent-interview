// 待确认记忆「入口发现性」验收脚手架（收敛计划 P1-T5 / 波次 B2）：
// 在 node vm 沙箱里载入**真实 main.js**，调用生产函数本体验证两件事——
//   ① loadPendingMemoryBadge()：角标条数来自 GET /api/memory/pending?status=pending；
//      无 pending / 无权限 / 网络失败一律隐藏角标，且不抛未捕获异常、不写 console.error；
//   ② submitArcFeedback() 在 distilled=true 时给出的 toast 行动点，真的会跳到
//      /evalset#pendingMemoryBody，而不是只留一句文案（M11 的「有字样、无入口」）。
// 另做「接线」校验：index.html 里的角标锚点 href 与跳转目标 id 必须与 main.js 同源，
// 且该 id 在评测台页面真实存在（防「定义了没接线」）。
//
// 沙箱内零真实网络：fetch 由本脚本注入的调度器接管。只读，不碰任何 live 数据。
// 用法（cwd 任意）：node demo/tests/frontend_pending_memory_badge_harness.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const indexPath = path.join(__dirname, '..', 'templates', 'index.html');
const evalsetPath = path.join(__dirname, '..', 'templates', 'evalset.html');
const src = fs.readFileSync(mainPath, 'utf8');

let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}

// ---- 最小 DOM 面：只需支撑角标与 toast 两条链路 ----
function mkEl(props) {
    const el = Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
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
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
    return el;
}

const badgeEl = mkEl({ id: 'pendingMemoryBadge' });
badgeEl.classList.add('hide');   // 初始 HTML 就是 hide（无 pending 不显示空壳）
const bodyEl = mkEl({ tagName: 'BODY' });

// 反馈链路用的假归档反馈单元格
const statusEl = mkEl({});
const textareaEl = mkEl({ value: '' });
const feedbackCell = mkEl({
    dataset: { rowIndex: '0', like: '-1' },
    querySelector(sel) {
        if (sel === '.feedback-comment') return textareaEl;
        if (sel === '.feedback-status') return statusEl;
        return null;
    },
});
const feedbackBtn = mkEl({ closest: sel => (sel === '.feedback-cell' ? feedbackCell : null) });

// ---- fetch 调度器：按 URL 分流，绝不发真实请求 ----
let pendingMode = 'rows';      // rows | empty | forbidden | network
let pendingRows = [];
const calls = [];
function mkRes(okStatus, body) {
    return {
        ok: okStatus >= 200 && okStatus < 300,
        status: okStatus,
        json: () => Promise.resolve(body),
        clone: () => mkRes(okStatus, body),
    };
}
function dispatcher(url) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/api/memory/pending') === 0) {
        if (pendingMode === 'network') return Promise.reject(new Error('offline'));
        if (pendingMode === 'forbidden') return Promise.resolve(mkRes(403, { detail: '权限不足：仅 admin' }));
        return Promise.resolve(mkRes(200, { status: 'success', data: pendingRows }));
    }
    if (u.indexOf('/api/receipt/') === 0 && u.indexOf('/feedback') !== -1) {
        return Promise.resolve(mkRes(200, { status: 'success', distilled: true }));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定的 URL' }));
}

const store = { demo_role: 'admin' };
const consoleErrors = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {}, warn() {},
        error(...a) { consoleErrors.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
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
sandbox.addEventListener = function () {};
sandbox.removeEventListener = function () {};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
const opened = [];
sandbox.open = (url, target) => { opened.push([url, target]); return null; };
sandbox.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'pendingMemoryBadge') return badgeEl;
        return null;   // toastContainer 等一律返回 null，让生产代码走创建分支
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
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

const { loadPendingMemoryBadge, openPendingMemoryQueue, submitArcFeedback } = sandbox;

ok('前置: 生产函数已定义', typeof loadPendingMemoryBadge === 'function'
    && typeof openPendingMemoryQueue === 'function' && typeof submitArcFeedback === 'function',
    'badge=' + typeof loadPendingMemoryBadge + ' open=' + typeof openPendingMemoryQueue
    + ' submit=' + typeof submitArcFeedback);

// 收集 body 上的 toast 容器（真实 showToast 会把容器挂到 document.body）
function lastToastActionBtn() {
    for (let i = bodyEl.children.length - 1; i >= 0; i--) {
        const container = bodyEl.children[i];
        for (let j = container.children.length - 1; j >= 0; j--) {
            const toast = container.children[j];
            for (const child of toast.children || []) {
                if (child.className === 'app-toast-action') return child;
            }
        }
    }
    return null;
}

// 断言辅助：跑一次角标加载（含两次微任务 tick，让 promise 链跑完）
function runBadge(mode, rows, role) {
    pendingMode = mode;
    pendingRows = rows || [];
    store.demo_role = role || 'admin';
    const before = calls.length;
    const p = Promise.resolve(loadPendingMemoryBadge());
    // loadPendingMemoryBadge 内部是 promise 链，等 3 个 tick 保证 settle
    return p.then(() => new Promise(r => setTimeout(r, 0)))
        .then(() => new Promise(r => setTimeout(r, 0)))
        .then(() => calls.length - before);
}

function resetBadge() { badgeEl.classList.add('hide'); badgeEl.textContent = '待确认记忆 0 条'; }

resetBadge();
runBadge('rows', [{ id: 3 }, { id: 2 }, { id: 1 }]).then(fetchCount => {
    ok('角标: 3 条 pending 时显示并给出带「记忆」语义的条数', badgeEl.textContent === '待确认记忆 3 条'
        && !badgeEl.classList.contains('hide'),
        'text=' + JSON.stringify(badgeEl.textContent) + ' hidden=' + badgeEl.classList.contains('hide'));
    ok('角标: 请求打在只读接口 /api/memory/pending?status=pending 上',
        calls.some(u => u === '/api/memory/pending?status=pending') && fetchCount === 1,
        'calls=' + JSON.stringify(calls.slice(-2)));

    resetBadge();
    return runBadge('empty', []);
}).then(() => {
    ok('角标: 0 条 pending 时不显示', badgeEl.classList.contains('hide'),
        'hidden=' + badgeEl.classList.contains('hide'));

    resetBadge();
    return runBadge('forbidden', []);
}).then(() => {
    ok('角标: 403（无权限）时静默隐藏', badgeEl.classList.contains('hide'),
        'hidden=' + badgeEl.classList.contains('hide'));

    resetBadge();
    return runBadge('network', []);
}).then(() => {
    ok('角标: 网络失败时静默隐藏（catch 兜住，不产生未捕获异常）',
        badgeEl.classList.contains('hide'), 'hidden=' + badgeEl.classList.contains('hide'));

    resetBadge();
    return runBadge('rows', [{ id: 1 }], 'staff');
}).then(fetchCount => {
    ok('角标: 非 admin 不发这次必然 403 的请求（不打扰页面）',
        fetchCount === 0 && badgeEl.classList.contains('hide'),
        'fetchCount=' + fetchCount + ' hidden=' + badgeEl.classList.contains('hide'));

    // --- toast 行动点 -> 真的跳转 ---
    opened.length = 0;
    vm.runInContext('currentArchiveReceiptId = 123;', sandbox);
    submitArcFeedback(feedbackBtn);
    return new Promise(r => setTimeout(r, 10));
}).then(() => {
    const btn = lastToastActionBtn();
    ok('toast: distilled=true 时给出可点击行动点', !!btn,
        '未找到 .app-toast-action（toast 未生成或未带 action）');
    if (btn) {
        ok('toast: 行动点文案为「查看待确认记忆」', String(btn.innerText) === '查看待确认记忆',
            'innerText=' + JSON.stringify(btn.innerText));
        const handlers = btn.listeners.click || [];
        handlers.forEach(fn => { try { fn(); } catch (e) { console.log('click handler 抛错: ' + e.message); } });
        ok('toast: 点击行动点跳到评测台待确认记忆区块',
            opened.length === 1 && opened[0][0] === '/evalset#pendingMemoryBody',
            'opened=' + JSON.stringify(opened));
        ok('toast: 新标签打开（与主台既有 /evalset 入口同口径）', opened.length === 1 && opened[0][1] === '_blank',
            'opened=' + JSON.stringify(opened));
    }

    // --- 接线校验：跳转目标 id 必须真实存在于评测台 ---
    const evalHtml = fs.readFileSync(evalsetPath, 'utf8');
    const hashTarget = '/evalset#pendingMemoryBody'.split('#')[1];
    ok('接线: 跳转锚点 id 在评测台页面真实存在', evalHtml.indexOf('id="' + hashTarget + '"') !== -1,
        'evalSet.html 缺少 id="' + hashTarget + '" -> 跳转会停在页首');

    const indexHtml = fs.readFileSync(indexPath, 'utf8');
    ok('接线: 主台角标锚点 href 与跳转目标同源',
        indexHtml.indexOf('id="pendingMemoryBadge"') !== -1
        && indexHtml.indexOf('href="/evalset#' + hashTarget + '"') !== -1,
        'index.html 缺少 badge 锚点或 href 不一致');
    ok('接线: 角标初始态为隐藏（无 pending 不显示空壳）',
        /id="pendingMemoryBadge"[^>]*class="[^"]*hide/.test(indexHtml)
        || /class="[^"]*hide[^"]*"[^>]*id="pendingMemoryBadge"/.test(indexHtml),
        '未看到 badge 带 hide 初始类');

    ok('静默: 全程 console.error 为 0（无未捕获异常被吞）', consoleErrors.length === 0,
        'consoleErrors=' + JSON.stringify(consoleErrors));

    console.log('\n' + pass + ' passed, ' + fail + ' failed');
    process.exit(fail ? 1 : 0);
}).catch(e => {
    console.log('FAIL harness 自身异常: ' + e.stack);
    process.exit(1);
});
