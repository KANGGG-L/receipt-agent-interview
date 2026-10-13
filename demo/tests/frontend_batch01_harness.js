// 批 1 前端契约：每日消耗 request_id、导入对账单入口。
// 在 node vm 里加载真实 main.js，调用生产函数，不抄一份逻辑。
// 用法：node demo/tests/frontend_batch01_harness.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const indexPath = process.argv[3] || path.join(__dirname, '..', 'templates', 'index.html');
// F3 B6-2 起每日消耗域在 dish_consumption.js（index.html 中紧跟 main.js 之后加载）；
// 沙箱按同一契约追加载入（可用 argv[4] 指定突变体路径做反证）。
const dishConsumptionPath = process.argv[4] || path.join(__dirname, '..', 'static', 'js', 'dish_consumption.js');
const src = fs.readFileSync(mainPath, 'utf8');
const dishSrc = fs.existsSync(dishConsumptionPath) ? fs.readFileSync(dishConsumptionPath, 'utf8') : '';
const indexHtml = fs.readFileSync(indexPath, 'utf8');

let pass = 0;
let fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) pass++;
    else { fail++; failures.push(name + ' -- ' + detail); }
}

function mkClassList() {
    const s = new Set();
    return {
        add(...c) { c.forEach(x => s.add(x)); },
        remove(...c) { c.forEach(x => s.delete(x)); },
        toggle(c, on) { if (on) s.add(c); else s.delete(c); },
        contains(c) { return s.has(c); },
    };
}

function mkEl(props) {
    const el = Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', innerHTML: '',
        value: '', style: {}, dataset: {}, children: [], disabled: false, type: '',
        isConnected: false, parentElement: null, parentNode: null,
        classList: mkClassList(),
        addEventListener() {},
        removeEventListener() {},
        focus() {}, click() {},
        setAttribute(k, v) { this[k] = v; if (k === 'class') this.className = v; },
        getAttribute(k) { return this[k] != null ? this[k] : null; },
        appendChild(c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; },
        removeChild(c) {
            const i = this.children.indexOf(c);
            if (i >= 0) this.children.splice(i, 1);
            return c;
        },
        remove() { this._removed = true; },
        querySelector(sel) {
            const want = String(sel || '');
            const stack = (this.children || []).slice();
            while (stack.length) {
                const n = stack.shift();
                const cls = String(n.className || '');
                if (want.charAt(0) === '.' && cls.split(/\s+/).indexOf(want.slice(1)) !== -1) return n;
                if (want.charAt(0) === '#' && n.id === want.slice(1)) return n;
                if (n.children) stack.push.apply(stack, n.children);
            }
            return null;
        },
        querySelectorAll() { return []; },
    }, props || {});
    return el;
}

const dishQty = mkEl({ id: 'dishConsumeQty_11', value: '2', className: 'dish-consume-qty-input' });
const dishRow = mkEl({ className: 'dish-consume-row' });
dishRow.getAttribute = function (k) {
    if (k === 'data-dish-id') return '11';
    if (k === 'data-dish-cost') return '1';
    if (k === 'data-dish-price') return '10';
    return null;
};
const dishBody = mkEl({ id: 'dishConsumeEntryTableBody', children: [dishRow] });
const dateEl = mkEl({ id: 'dishConsumptionDate', value: '2026-10-12' });
const submitBtn = mkEl({ id: 'btnSubmitDailyConsumeBatch', innerText: '一键扣减库存并核算真实成本' });
const importLines = mkEl({ id: 'importStatementLines', tagName: 'TBODY' });
const importModal = mkEl({ id: 'importStatementModal' });
importModal.classList.add('hide');
const importHint = mkEl({ id: 'importStatementSupplierHint' });
const importUseCurrent = mkEl({ id: 'importStatementUseCurrentTask', type: 'checkbox' });
const importSubmit = mkEl({ id: 'btnSubmitImportStatement', innerText: '提交对账单' });
const pageBody = mkEl({ tagName: 'BODY' });

const byId = {
    dishConsumeEntryTableBody: dishBody,
    dishConsumptionDate: dateEl,
    dishConsumeQty_11: dishQty,
    btnSubmitDailyConsumeBatch: submitBtn,
    importStatementLines: importLines,
    importStatementModal: importModal,
    importStatementSupplierHint: importHint,
    importStatementUseCurrentTask: importUseCurrent,
    btnSubmitImportStatement: importSubmit,
};

const batchCalls = [];
const importCalls = [];

function mkRes(status, bodyObj) {
    const res = {
        ok: status >= 200 && status < 300,
        status: status,
        json: () => Promise.resolve(bodyObj),
        clone() { return mkRes(status, bodyObj); },
    };
    return res;
}

function fetchImpl(url, init) {
    const u = String(url);
    const rec = { url: u, body: init && init.body };
    if (u.indexOf('/api/dishes/daily_consumption/batch') !== -1) {
        batchCalls.push(rec);
        return new Promise(resolve => { rec.resolve = resolve; });
    }
    if (u.indexOf('/api/reconciliation/import-statement') !== -1) {
        importCalls.push(rec);
        return new Promise(resolve => { rec.resolve = resolve; });
    }
    if (u.indexOf('/api/reconciliation/') === 0) {
        return Promise.resolve(mkRes(200, { status: 'error', msg: '测试桩不打开详情' }));
    }
    return Promise.resolve(mkRes(200, { status: 'success', data: [] }));
}

const sandbox = {
    console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    setTimeout, clearTimeout,
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() { return true; },
    localStorage: {
        _s: { demo_role: 'owner' },
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
    fetch: fetchImpl,
    URLSearchParams,
    crypto: { randomUUID: () => 'uuid-' + Math.random().toString(16).slice(2) },
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
        if (sel === '#dishConsumeEntryTableBody tr.dish-consume-row') return [dishRow];
        if (sel === '.dish-consume-qty-input') return [dishQty];
        if (sel === '#importStatementLines tr.import-statement-line') {
            return (importLines.children || []).filter(c => String(c.className || '').indexOf('import-statement-line') !== -1);
        }
        return [];
    },
    createElement(tag) { return mkEl({ tagName: String(tag || 'div').toUpperCase() }); },
    documentElement: { style: { setProperty() {} } },
    body: pageBody,
    readyState: 'loading',
};

function setRole(role) { sandbox.localStorage.setItem('demo_role', role); }
function wait() { return new Promise(resolve => setTimeout(resolve, 30)); }

let loadError = null;
try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { loadError = e.message; }
if (dishSrc) {
    try { vm.runInContext(dishSrc, sandbox, { filename: 'dish_consumption.js' }); }
    catch (e) { console.error('dish_consumption load note: ' + e.message); }
}
// F4 S4-11：main.js 按域拆分，沙箱按 index.html 加载链追加其余域文件
// （dish_consumption 已单独载入，skip；overrides 可按文件名指定突变体路径做反证）。
require('./f4_load_domains.js').loadF4Domains(
    (dsrc, name) => vm.runInNewContext(dsrc, sandbox, { filename: name }),
    { warn: m => console.error(m), skip: ['dish_consumption.js'] });

function finish() {
    console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures, loadError: loadError }));
    process.exit(fail === 0 && !loadError ? 0 : 1);
}

ok('main.js 加载', !loadError, loadError);
// F3 B1 起标签追加 csp-s-N 类，判据改按标签块判定（存在 + 挂 owner-only），属性序无关
ok('页面有导入对账单按钮且为 owner-only',
    (() => {
        const i = indexHtml.indexOf('id="btnImportStatement"');
        if (i === -1) return false;
        const tagStart = indexHtml.lastIndexOf('<button', i);
        const tagEnd = indexHtml.indexOf('>', i);
        return tagStart !== -1 && tagEnd !== -1
            && indexHtml.slice(tagStart, tagEnd).indexOf('owner-only') !== -1;
    })(),
    'index');
ok('提交函数会调用 import-statement',
    // F5·S4-11：submitImportStatement 已随财务闭环域移入 finance_ops.js；
    // 源码级判据改扫「main.js + 全部域文件」联合全文（与浏览器实际加载集合同构，判据不放宽）。
    [src].concat(require('./f4_load_domains.js').F4_DOMAIN_FILES.map(
        name => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', name), 'utf8'))
    ).join('\n').indexOf('/api/reconciliation/import-statement') !== -1, 'union');

function runRequestId() {
    setRole('staff');
    sandbox.submitDailyConsumptionBatch();
    sandbox.submitDailyConsumptionBatch();
    ok('双击只发出一次每日消耗请求', batchCalls.length === 1, 'calls=' + batchCalls.length);
    const body1 = JSON.parse(batchCalls[0].body);
    ok('请求体含非空 request_id', typeof body1.request_id === 'string' && body1.request_id.length > 0, body1.request_id);
    ok('请求体仍带日期和明细', body1.date === '2026-10-12' && body1.items && body1.items[0].dish_id === 11, JSON.stringify(body1));
    const id1 = body1.request_id;
    batchCalls[0].resolve(mkRes(500, { status: 'error', msg: '暂时无法记录，请再试一次' }));
    return wait().then(() => {
        ok('失败后按钮恢复，便于重试', submitBtn.disabled === false, String(submitBtn.disabled));
        sandbox.submitDailyConsumptionBatch();
        ok('失败重试仍是同一个 request_id', batchCalls.length === 2, 'calls=' + batchCalls.length);
        const body2 = JSON.parse(batchCalls[1].body);
        ok('重试 ID 与第一次相同', body2.request_id === id1, body2.request_id + ' vs ' + id1);
        batchCalls[1].resolve(mkRes(200, {
            status: 'success',
            msg: '成功记录 1 项餐品消耗',
            data: { replayed: false, merged: false, quantity: 2, consumption_ids: [] },
        }));
        return wait();
    }).then(() => {
        dishQty.value = '2';
        sandbox.submitDailyConsumptionBatch();
        ok('成功之后的新提交再发一次', batchCalls.length === 3, 'calls=' + batchCalls.length);
        const body3 = JSON.parse(batchCalls[2].body);
        ok('成功后的新提交使用新的 request_id', body3.request_id && body3.request_id !== id1, body3.request_id);
        batchCalls[2].resolve(mkRes(200, {
            status: 'success',
            msg: '这一单刚才已经记过了，没有重复扣库存',
            data: { replayed: true, consumption_ids: [], quantity: 2 },
        }));
        return wait();
    });
}

function runImport() {
    setRole('staff');
    vm.runInContext('finState.supplierId = 9; finState.suppliers = [{id:9, name:"供应商甲"}]; finState.currentTaskId = null;', sandbox);
    sandbox.openImportStatementModal();
    ok('staff 打不开导入对账单', importModal.classList.contains('hide'), 'modal');
    ok('staff 没有发出导入请求', importCalls.length === 0, 'calls=' + importCalls.length);

    setRole('owner');
    vm.runInContext('finState.supplierId = null;', sandbox);
    sandbox.openImportStatementModal();
    ok('未选供应商时不打开导入窗', importModal.classList.contains('hide'), 'modal');

    vm.runInContext('finState.supplierId = 9; finState.currentTaskId = null;', sandbox);
    sandbox.openImportStatementModal();
    ok('老板选了供应商后打开导入窗', !importModal.classList.contains('hide'), 'modal');
    ok('默认有一行可填', importLines.children.length === 1, 'rows=' + importLines.children.length);
    const line = importLines.children[0];
    line.querySelector('.import-line-date').value = '2026-10-05';
    line.querySelector('.import-line-no').value = 'INV-1';
    line.querySelector('.import-line-amount').value = '12.5';
    sandbox.submitImportStatement();
    ok('发出导入请求', importCalls.length === 1, 'calls=' + importCalls.length);
    const created = JSON.parse(importCalls[0].body);
    ok('新建导入不带 task_id', created.task_id == null, JSON.stringify(created));
    ok('导入行含日期单号金额',
        created.supplier_id === 9
            && created.external_lines[0].date === '2026-10-05'
            && created.external_lines[0].invoice_no === 'INV-1'
            && created.external_lines[0].amount === 12.5,
        JSON.stringify(created));
    importCalls[0].resolve(mkRes(200, { status: 'success', task_id: 'task-new', summary: {} }));
    return wait().then(() => {
        ok('成功后关闭导入窗', importModal.classList.contains('hide'), 'modal');
        vm.runInContext('finState.currentTaskId = "task-new";', sandbox);
        sandbox.openImportStatementModal();
        const again = importLines.children[0];
        again.querySelector('.import-line-date').value = '2026-10-05';
        again.querySelector('.import-line-no').value = 'INV-1';
        again.querySelector('.import-line-amount').value = '12.5';
        ok('已打开对账时默认勾选导入到当前任务', importUseCurrent.checked === true, String(importUseCurrent.checked));
        sandbox.submitImportStatement();
        const replay = JSON.parse(importCalls[1].body);
        ok('第二次提交带上当前 task_id', replay.task_id === 'task-new', JSON.stringify(replay));
        importCalls[1].resolve(mkRes(409, {
            status: 'error',
            code: 'STATEMENT_ALREADY_IMPORTED',
            msg: '这个对账已经导入过对账单了，请新建一个对账再导入',
        }));
        return wait();
    }).then(() => {
        ok('重复导入不关闭窗口', !importModal.classList.contains('hide'), 'modal');
        const texts = [];
        const stack = pageBody.children.slice();
        while (stack.length) {
            const n = stack.pop();
            if (n && n.innerText) texts.push(String(n.innerText));
            if (n && n.children) stack.push.apply(stack, n.children);
        }
        ok('重复导入展示人话', texts.join('\n').indexOf('已经导入过对账单') !== -1, texts.join('|').slice(0, 300));
    });
}

runRequestId().then(runImport).then(finish).catch(err => {
    fail++;
    failures.push('harness exception -- ' + err.stack);
    finish();
});
