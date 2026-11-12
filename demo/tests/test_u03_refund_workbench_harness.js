// U03 退款/信用工作台 harness：真实 finance.js + 假 fetch，零真实网络。
// 覆盖：退款列表渲染（待退款/处理中/已收/失败/取消 + 金额掩码）、发起部分退款、
// 取消 pending、登记已收证据、部分抵扣、超分配 409 保草稿、撤销抵扣、角色权限。
// 运行：cd demo && node tests/test_u03_refund_workbench_harness.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.join(__dirname, '..');
const financePath = path.join(root, 'static', 'js', 'finance.js');
const indexHtmlPath = path.join(root, 'templates', 'index.html');
const src = fs.readFileSync(financePath, 'utf8');
const indexHtml = fs.readFileSync(indexHtmlPath, 'utf8');

let pass = 0;
let fail = 0;
const failures = [];
function check(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; failures.push(name + ' -- ' + (detail || '')); console.log('FAIL ' + name + (detail ? ' -- ' + detail : '')); }
}

// ---- 极简 DOM：只提供被测路径需要的接口 ----
const els = {};
function mkEl(id, props) {
    return Object.assign({
        id: id || '', innerHTML: '', textContent: '', value: '', style: {}, dataset: {},
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {},
        getAttribute() { return null; },
        setAttribute() {},
        appendChild() {},
        focus() {},
        click() {},
    }, props || {});
}
['creditWorkbenchArea', 'creditDetailArea', 'creditAllocateDocument', 'creditTargetSelect',
 'creditAllocateAmount', 'creditAllocError', 'creditRefundDocument', 'creditRefundAmount',
 'creditRefundError'].forEach(id => { els[id] = mkEl(id); });

// ---- 假 fetch：按 URL/方法回放固定响应；POST 记录请求体 ----
const calls = [];
const responses = {
    refundStatus: 200,
    refundBody: { status: 'success', data: { applied: true, refund_id: 7, state: 'awaiting_receipt' } },
    allocStatus: 200,
    allocBody: { status: 'success', data: { applied: true, available_minor: 2000 } },
};
function mkRes(status, body) {
    return { ok: status >= 200 && status < 300, status, json: () => Promise.resolve(body) };
}
function fakeFetch(url, opts) {
    const u = String(url);
    const method = (opts && opts.method) || 'GET';
    let body = null;
    if (opts && opts.body) { try { body = JSON.parse(opts.body); } catch (e) { body = opts.body; } }
    calls.push({ url: u, method, body });
    if (method === 'POST' && /\/api\/supplier-credits\/\d+\/refunds$/.test(u)) {
        return Promise.resolve(mkRes(responses.refundStatus, responses.refundBody));
    }
    if (method === 'POST' && /\/api\/supplier-credits\/\d+\/allocations$/.test(u)) {
        return Promise.resolve(mkRes(responses.allocStatus, responses.allocBody));
    }
    if (u === '/api/supplier-credits') {
        return Promise.resolve(mkRes(200, { status: 'success', data: [
            { supplier_id: 1, supplier_name: 'Vendor_A', face_total_minor: 7000, available_minor: 4000 },
        ] }));
    }
    if (/^\/api\/supplier-credits\/\d+$/.test(u)) {
        return Promise.resolve(mkRes(200, { status: 'success', data: {
            supplier_id: 1, face_total_minor: 7000, available_minor: 4000,
            allocated_refund_occupied_minor: 3000, document_count: 1,
            documents: [{ credit_id: 11, face_minor: 7000, currency: 'HKD' }],
            allocations: [{ id: 3, credit_id: 11, target_receipt_id: 9, amount_minor: 3000,
                            reversal_of: null, active: true, created_at: '2026-10-26T10:00:00' }],
            refunds: [
                { refund_id: 5, credit_id: 11, state: 'awaiting_receipt', received_evidence: '', created_at: '2026-10-26T10:00:00' },
                { refund_id: 6, credit_id: 11, state: 'received', received_evidence: 'BANK-1', created_at: '2026-10-26T10:00:00' },
            ],
        } }));
    }
    if (u.indexOf('/api/payables') === 0) {
        return Promise.resolve(mkRes(200, { status: 'success', data: { rows: [
            { receipt_id: 9, receipt_date: '2026-10-20', currency: 'HKD', total_amount: 100,
              total_hkd: 100, paid_amount: 30, outstanding_amount: 70, payment_status: 'partial' },
        ] } }));
    }
    return Promise.resolve(mkRes(200, { status: 'success', data: [] }));
}

const sandbox = {
    console: { log() {}, info() {}, debug() {}, warn() {}, error() {} },
    setTimeout, clearTimeout,
    localStorage: {
        _s: {},
        getItem(k) { return (k in this._s) ? this._s[k] : null; },
        setItem(k, v) { this._s[k] = String(v); },
        removeItem(k) { delete this._s[k]; },
    },
    AuthState: { account: { role: 'owner' } },
    I18N: { fmtMoney(v) { return 'HK$ ' + (Number(v) || 0).toFixed(2); } },
    w2Escape(s) {
        return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    },
    showToast(msg, kind) { sandbox.__toasts.push({ msg: String(msg), kind: kind || '' }); },
    uiConfirm() { return Promise.resolve(true); },
    apiFetch(url, opts) { return fakeFetch(url, opts); },
    document: {
        readyState: 'complete',
        addEventListener() {},
        getElementById(id) { return els[id] || null; },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        createElement(tag) { return mkEl('', { tagName: String(tag || 'div').toUpperCase() }); },
        documentElement: { style: { setProperty() {} } },
        body: mkEl('body'),
    },
    __toasts: [],
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;

try {
    vm.runInNewContext(src, sandbox, { filename: 'finance.js' });
} catch (e) {
    check('finance.js 可在沙箱装载', false, e.message);
    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    process.exit(1);
}
check('finance.js 可在沙箱装载', typeof sandbox.loadCreditWorkbench === 'function');

function flush() { return new Promise(r => setTimeout(r, 0)); }
function setRole(role) { sandbox.AuthState.account.role = role; }
function resetCalls() { calls.length = 0; }
function posts() { return calls.filter(c => c.method === 'POST'); }

(async function run() {
    // ---------------------------------------------------------------
    // 1. 退款工作台渲染：五态 + 金额掩码 + pending 行动作
    // ---------------------------------------------------------------
    setRole('owner');
    const bal = {
        documents: [{ credit_id: 11, face_minor: 7000 }],
        refunds: [
            { refund_id: 1, credit_id: 11, state: 'awaiting_receipt', received_evidence: '', created_at: '2026-10-26T10:00:00' },
            { refund_id: 2, credit_id: 11, state: 'processing', received_evidence: '', created_at: '2026-10-26T10:00:00' },
            { refund_id: 3, credit_id: 11, state: 'received', received_evidence: 'BANK-1', created_at: '2026-10-26T10:00:00' },
            { refund_id: 4, credit_id: 11, state: 'failed', received_evidence: '', created_at: '2026-10-26T10:00:00' },
            { refund_id: 5, credit_id: 11, state: 'cancelled', received_evidence: '', created_at: '2026-10-26T10:00:00' },
        ],
    };
    const refundHtml = sandbox.refundWorkbenchHtml(bal);
    check('退款列表渲染待退款', refundHtml.indexOf('待退款') !== -1);
    check('退款列表渲染处理中', refundHtml.indexOf('退款处理中') !== -1);
    check('退款列表渲染已收到', refundHtml.indexOf('已收到') !== -1);
    check('退款列表渲染失败', refundHtml.indexOf('退款失败') !== -1);
    check('退款列表渲染已取消', refundHtml.indexOf('已取消') !== -1);
    check('退款金额全部掩码 $***', (refundHtml.match(/\$\*\*\*/g) || []).length >= 5,
        String((refundHtml.match(/\$\*\*\*/g) || []).length));
    check('pending 行可取消', (refundHtml.match(/credit-refund-cancel-btn/g) || []).length === 1);
    check('pending 行可登记已收', (refundHtml.match(/credit-refund-receive-btn/g) || []).length === 1);
    check('登记已收含证据输入', refundHtml.indexOf('credit-evidence-input') !== -1);
    check('已收/失败/取消无操作按钮', refundHtml.indexOf('data-refund-id="3"') !== -1
        && refundHtml.indexOf('credit-refund-cancel-btn" data-credit-id="11" data-refund-id="3"') === -1);
    check('owner 下按钮不禁用', refundHtml.indexOf(' disabled') === -1);

    // ---------------------------------------------------------------
    // 2. 钱货口径明示 + 欠款单
    // ---------------------------------------------------------------
    const rows = [{ receipt_id: 9, receipt_date: '2026-10-20', currency: 'HKD',
                    total_amount: 100, total_hkd: 100, paid_amount: 30, outstanding_amount: 70 }];
    const summary = sandbox.creditBalanceSummaryHtml(
        { face_total_minor: 7000, available_minor: 4000, allocated_refund_occupied_minor: 3000 },
        rows, [{ amount_minor: 3000, reversal_of: null, active: true }]);
    ['gross', '已付', '信用已分配', '净欠', '未分配'].forEach(label => {
        check('汇总明示「' + label + '」', summary.indexOf(label) !== -1, summary.slice(0, 300));
    });
    check('汇总 net=70', summary.indexOf('HK$ 70.00') !== -1);
    check('汇总 未分配=HK$ 40.00', summary.indexOf('HK$ 40.00') !== -1);
    const payHtml = sandbox.creditPayablesTableHtml(rows);
    check('欠款单显示 gross/已付/净欠', payHtml.indexOf('gross') !== -1
        && payHtml.indexOf('HK$ 30.00') !== -1 && payHtml.indexOf('HK$ 70.00') !== -1);

    // ---------------------------------------------------------------
    // 3. 发起部分退款（真实函数 + 假 fetch）
    // ---------------------------------------------------------------
    setRole('owner');
    els.creditRefundDocument.value = '11';
    els.creditRefundAmount.value = '12.34';
    resetCalls();
    let ok = await sandbox.submitCreditRefundRequest(1);
    check('发起退款成功返回 true', ok === true);
    let post = posts()[0];
    check('退款 POST 打到信用票据端点', !!post && post.url === '/api/supplier-credits/11/refunds', post && post.url);
    check('退款 payload action=request', !!post && post.body.action === 'request');
    check('退款金额换算成分 1234', !!post && post.body.amount_minor === 1234, post && post.body.amount_minor);
    check('退款 payload 带幂等键', !!post && !!post.body.idempotency_key);
    check('退款成功后清空金额草稿', els.creditRefundAmount.value === '');

    // 退款失败保留草稿
    responses.refundStatus = 409;
    responses.refundBody = { status: 'error', code: 'CREDIT_INSUFFICIENT', msg: '可用信用不足：请减少退款金额。' };
    els.creditRefundAmount.value = '99.00';
    resetCalls();
    ok = await sandbox.submitCreditRefundRequest(1);
    check('退款 409 返回 false', ok === false);
    check('退款 409 不清草稿', els.creditRefundAmount.value === '99.00');
    check('退款 409 显示后端原因', els.creditRefundError.textContent.indexOf('可用信用不足') !== -1,
        els.creditRefundError.textContent);
    responses.refundStatus = 200;
    responses.refundBody = { status: 'success', data: { applied: true, refund_id: 7 } };

    // ---------------------------------------------------------------
    // 4. 取消 pending / 登记已收
    // ---------------------------------------------------------------
    resetCalls();
    ok = await sandbox.cancelCreditRefund(1, 11, 5);
    post = posts()[0];
    check('取消退款成功', ok === true);
    check('取消退款 body action=cancel', !!post && post.body.action === 'cancel');
    check('取消退款 body refund_id=5', !!post && post.body.refund_id === 5);

    resetCalls();
    ok = await sandbox.receiveCreditRefund(1, 11, 5, '   ');
    check('无证据登记已收被拒', ok === false && posts().length === 0);
    check('无证据提示', els.creditRefundError.textContent.indexOf('收到证据') !== -1);
    resetCalls();
    ok = await sandbox.receiveCreditRefund(1, 11, 5, 'BANK-REF-001');
    post = posts()[0];
    check('登记已收成功', ok === true);
    check('登记已收 action=receive + evidence',
        !!post && post.body.action === 'receive' && post.body.evidence === 'BANK-REF-001');

    // ---------------------------------------------------------------
    // 5. 部分抵扣 + 超分配 409 保草稿
    // ---------------------------------------------------------------
    els.creditAllocateDocument.value = '11';
    els.creditTargetSelect.value = '9';
    els.creditAllocateAmount.value = '30';
    resetCalls();
    ok = await sandbox.submitCreditAllocation(1);
    post = posts()[0];
    check('部分抵扣成功', ok === true);
    check('抵扣打到 credit_id 端点（不是 supplier_id）', !!post && post.url === '/api/supplier-credits/11/allocations', post && post.url);
    check('抵扣 target receipt=9 / amount_minor=3000',
        !!post && post.body.targets[0].receipt_id === 9 && post.body.targets[0].amount_minor === 3000);
    check('抵扣成功清空金额', els.creditAllocateAmount.value === '');

    responses.allocStatus = 409;
    responses.allocBody = { status: 'error', code: 'CREDIT_INSUFFICIENT', msg: '可用信用不足：可用 HK$ 40.00，本次要抵 HK$ 999.00。' };
    els.creditAllocateDocument.value = '11';
    els.creditTargetSelect.value = '9';
    els.creditAllocateAmount.value = '999';
    resetCalls();
    ok = await sandbox.submitCreditAllocation(1);
    check('超分配 409 返回 false', ok === false);
    check('超分配 409 不清草稿（金额）', els.creditAllocateAmount.value === '999');
    check('超分配 409 不清草稿（目标单据）', els.creditTargetSelect.value === '9');
    check('超分配 409 不清草稿（信用票据）', els.creditAllocateDocument.value === '11');
    check('超分配 409 显示原因', els.creditAllocError.textContent.indexOf('可用信用不足') !== -1,
        els.creditAllocError.textContent);
    responses.allocStatus = 200;
    responses.allocBody = { status: 'success', data: { applied: true, available_minor: 1000 } };

    // ---------------------------------------------------------------
    // 6. 撤销抵扣（reversal_of，不新增端点语义）
    // ---------------------------------------------------------------
    resetCalls();
    ok = await sandbox.cancelCreditAllocation(1, 11, 3);
    post = posts()[0];
    check('撤销抵扣成功', ok === true);
    check('撤销走既有 allocations 端点', !!post && post.url === '/api/supplier-credits/11/allocations');
    check('撤销 body reversal_of=3', !!post && post.body.reversal_of === 3);
    check('撤销 payload 带幂等键', !!post && !!post.body.idempotency_key);

    // ---------------------------------------------------------------
    // 7. 权限：staff 不见财务；admin 只读不可操作；owner 可操作
    // ---------------------------------------------------------------
    setRole('staff');
    els.creditWorkbenchArea.innerHTML = 'stale';
    resetCalls();
    await sandbox.loadCreditWorkbench();
    check('staff 不渲染财务工作台', els.creditWorkbenchArea.innerHTML === '');
    check('staff 不发信用余额请求', calls.length === 0, JSON.stringify(calls));

    setRole('admin');
    const adminHtml = sandbox.refundWorkbenchHtml(bal);
    check('admin 看到只读说明', adminHtml.indexOf('只能查看') !== -1);
    check('admin 退款按钮禁用', adminHtml.indexOf('credit-refund-request-btn" disabled') !== -1);
    els.creditAllocateDocument.value = '11';
    els.creditTargetSelect.value = '9';
    els.creditAllocateAmount.value = '10';
    resetCalls();
    ok = await sandbox.submitCreditAllocation(1);
    check('admin 抵扣被前端拒绝', ok === false && posts().length === 0);
    check('admin 抵扣给出权限提示',
        sandbox.__toasts.some(t => t.kind === 'error' && t.msg.indexOf('权限不足') !== -1));
    resetCalls();
    ok = await sandbox.submitCreditRefundRequest(1);
    check('admin 退款被前端拒绝', ok === false && posts().length === 0);

    setRole('owner');
    const ownerHtml = sandbox.refundWorkbenchHtml(bal);
    check('owner 退款表单可用', ownerHtml.indexOf('credit-refund-request-btn" disabled') === -1);
    check('owner 可操作', sandbox.financeCanOperate() === true);

    // ---------------------------------------------------------------
    // 8. 静态接线：月结对账单入口 / 财务卡对 staff 隐藏 / 无原生弹窗
    // ---------------------------------------------------------------
    const cardIdx = indexHtml.indexOf('id="payablesCard"');
    const cardTag = indexHtml.slice(indexHtml.lastIndexOf('<div', cardIdx), cardIdx + 20);
    check('应付/信用卡声明 owner-only（staff 不见财务）', cardTag.indexOf('owner-only') !== -1, cardTag);
    check('月结对账单入口存在', indexHtml.indexOf('id="btnMonthlyStatement"') !== -1);
    check('月结对账单走 CSP 绑定', indexHtml.indexOf('openMonthlyStatementEntry') !== -1
        && indexHtml.indexOf('data-csp-click') !== -1);
    check('不再使用浏览器原生 prompt 弹窗', src.indexOf('window.prompt') === -1);

    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    if (fail) console.log('FAILURES:\n' + failures.join('\n'));
    process.exit(fail ? 1 : 0);
})().catch(e => {
    console.error('harness crashed:', e && e.stack || e);
    process.exit(1);
});
