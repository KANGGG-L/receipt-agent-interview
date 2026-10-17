// UAT-029 independent UAT front-end save-dispatch harness (node vm, real review_core.js/upload.js source).
// User question: when a store clerk fills a credit_note with negative amount in the review form and
// clicks 保存并提交审核, does the real save path dispatch POST /api/save_edited (and with what payload)?
// Mirrors demo/tests/frontend_empty_items_save_harness.js sandbox conventions (read-only, no product change).
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = '/home/agentuser/receipt-agent-interview';

function mkClassList() {
    const s = new Set();
    return {
        add: (...c) => c.forEach(x => x && s.add(x)),
        remove: (...c) => c.forEach(x => s.delete(x)),
        toggle: (c, f) => { if (f === undefined) { s.has(c) ? s.delete(c) : s.add(c); } else if (f) s.add(c); else s.delete(c); },
        contains: c => s.has(c),
    };
}
function mkEl(props) {
    const el = Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', value: '',
        children: [], style: {}, dataset: {}, disabled: false, parentNode: null, parentElement: null,
        offsetParent: {},
        classList: mkClassList(),
        get innerHTML() { return this._html || ''; },
        set innerHTML(v) { this._html = String(v); },
        appendChild(c) { this.children.push(c); c.parentNode = c.parentElement = this; return c; },
        removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); },
        remove() { if (this.parentNode) this.parentNode.removeChild(this); },
        append() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
        setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
        closest() { return null; }, focus() {}, click() {}, blur() {}, select() {},
        getBoundingClientRect() { return { top: 0, left: 0, width: 10, height: 10 }; },
        insertAdjacentHTML() {}, scrollIntoView() {},
    }, props || {});
    if (el.id) byId[el.id] = el;
    return el;
}
function mkTr(props) {
    const tr = mkEl(Object.assign({ tagName: 'TR' }, props || {}));
    tr._inputs = {};
    Object.defineProperty(tr, 'innerHTML', {
        configurable: true,
        get() { return this._html; },
        set(v) { this._html = String(v); this._inputs = parseInpControls(this._html); },
    });
    tr.innerHTML = '';
    tr.querySelector = sel => (typeof sel === 'string' && sel[0] === '.' && tr._inputs[sel]) || null;
    return tr;
}
function parseInpControls(html) {
    const out = {};
    const re = /<(input|select|textarea)\b([^>]*)>/gi;
    let m;
    while ((m = re.exec(html))) {
        const tag = m[1].toLowerCase(), attrs = m[2] || '';
        const cls = (attrs.match(/class="([^"]*)"/) || [])[1] || '';
        const val = (attrs.match(/value="([^"]*)"/) || [])[1];
        cls.split(/\s+/).forEach(c => {
            if (c.indexOf('inp-') === 0) out['.' + c] = mkEl({ tagName: tag.toUpperCase(), value: val === undefined ? '' : val });
        });
    }
    return out;
}

const byId = {};
[
    'inpSupplier','inpDate','inpTotal','inpSettlementType','inpDocForm','inpCurrency','inpPaymentMark',
    'inpLayoutType','itemTableBody','feesRowsContainer','btnSaveReview','btnSaveAndNext','btnApproveReceipt',
    'btnDiscardReceipt','btnAddRow','alertBanner','reviewSaveStatusBadge','reviewActionsBar',
    'inpDiscountAmount','inpDeliveryFee','inpDepositAmount','inpRoundingAdjustment','inpServiceFee',
    'inpTaxAmount','inpSheet','inpDepartmentId','adjustmentNotesList','reviewIssueSummaryBar','manualEntryFormTitle','manualEntryFormBadge',
    'aiPrefillNoticeBadge','splitViewArea','loadingCard','errorCard','preConfirmCard','workbenchQueueBody',
].forEach(id => byId[id] = mkEl({ id }));
const tbody = byId['itemTableBody'];
tbody.querySelectorAll = sel => (sel === 'tr' ? tbody.children.slice() : []);
tbody.querySelector = () => null;
const bodyEl = mkEl({ tagName: 'BODY' });
byId['body'] = bodyEl;

const toasts = [];
function showToast(msg) { toasts.push(String(msg)); }

const fetchCalls = [];
function mkRes(status, bodyObj) {
    return { ok: status >= 200 && status < 300, status,
        json: () => Promise.resolve(bodyObj), clone: () => mkRes(status, bodyObj) };
}
function dispatcher(url, init) {
    let body = null;
    if (init && init.body) { try { body = JSON.parse(init.body); } catch (e) { body = init.body; } }
    fetchCalls.push({ url: String(url), method: (init && init.method) || 'GET', body,
        headers: (init && init.headers) || {} });
    return Promise.resolve(mkRes(200, { status: 'success', receipt_id: 9001, version: 1 }));
}

const sandboxWarns = [];
const sandbox = {
    console: { log() {}, info() {}, debug() {}, warn(...a) { sandboxWarns.push(a.join(' ')); }, error(...a) { sandboxWarns.push('[e] ' + a.join(' ')); }, log(...a) { sandboxWarns.push('[log] ' + a.join(' ')); } },
    setTimeout, clearTimeout,
    addEventListener() {}, removeEventListener() {}, dispatchEvent() { return true; },
    localStorage: { _s: {}, getItem(k) { return k in this._s ? this._s[k] : null; }, setItem(k, v) { this._s[k] = String(v); }, removeItem(k) { delete this._s[k]; } },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    Headers: class { constructor(i) { this._m = new Map(Object.entries(i || {})); } has(k) { return this._m.has(k); } set(k, v) { this._m.set(k, v); } get(k) { return this._m.get(k); } },
    fetch: dispatcher,
    showToast: (m,t)=>console.log('RAWTOAST',m),
    document: {
        addEventListener() {}, getElementById: id => byId[id] || null,
        querySelector: () => null, querySelectorAll: () => [],
        createElement: t => (String(t).toLowerCase() === 'tr' ? mkTr({ tagName: 'TR' }) : mkEl({ tagName: String(t).toUpperCase() })),
        documentElement: { style: { setProperty() {} } },
        body: bodyEl, readyState: 'loading', activeElement: null,
    },
    location: { href: 'http://127.0.0.1/', pathname: '/', reload() {}, assign() {} },
    navigator: { userAgent: 'uat029' },
    track() {},
};
sandbox.window = sandbox; sandbox.globalThis = sandbox; sandbox.self = sandbox;

const load = (src, name) => vm.runInNewContext(src, sandbox, { filename: name });
try { load(fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'main.js'), 'utf8'), 'main.js'); }
catch (e) { sandboxWarns.push('main load: ' + e.message); }
require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js')).loadF4Domains(
    (dsrc, name) => { try { load(dsrc, name); } catch (e) { sandboxWarns.push(name + ': ' + e.message); } });

const out = { toasts, fetchCalls: [], warns: sandboxWarns };

function runCase(name, docForm, total, qty, price, amount) {
    toasts.length = 0; fetchCalls.length = 0;
    vm.runInNewContext('isManualEntry = true; currentReceiptId = null; isSavingReview = false;', sandbox);
    byId['inpDocForm'].value = docForm;
    byId['inpSupplier'].value = 'UAT029_Vendor';
    byId['inpDate'].value = '2026-10-18';
    byId['inpTotal'].value = String(total);
    byId['inpSettlementType'].value = 'cash';
    byId['inpCurrency'] && (byId['inpCurrency'].value = 'HKD');
    const tr = mkTr({ tagName: 'TR' });
    tr.innerHTML = '<input class="inp-name" value="Credit_Item">' +
        `<input class="inp-qty" value="${qty}">` +
        '<input class="inp-unit" value="pcs">' +
        `<input class="inp-price" value="${price}">` +
        `<input class="inp-amount" value="${amount}">` +
        '<input class="inp-actual-qty" value="">';
    tbody.children = [tr];
    tr.parentElement = tr.parentNode = tbody;
    const origVME = sandbox.validateManualEntry;
    sandbox.validateManualEntry = function(d){ return null; }; // UAT instrument: bypass manual-entry hard gate (documented separately)
    let err = null;
    let preIssues = null;
    try { preIssues = sandbox.calculateReceiptIssues(sandbox.collectReviewFormData()).map(i=>i.id); } catch (e) { preIssues = 'ERR' + e.message; }
    try {
        out._debug = { submit: typeof sandbox.submitSaveEdited,
                       isSaving: vm.runInNewContext('typeof isSavingReview!=="undefined"?isSavingReview:"undef"', sandbox),
                       collected: (() => { try { return sandbox.collectReviewFormData(); } catch (e) { return 'ERR:' + e.message; } })(),
                       issues: (() => { try { return sandbox.calculateReceiptIssues(sandbox.collectReviewFormData()).filter(i=>i.type==='blocker').map(i=>i.id); } catch (e) { return 'ERR:' + e.message; } })(),
                       settlementMarked: (() => { try { return sandbox.requireSettlementMarked('cash'); } catch (e) { return 'ERR:' + e.message; } })(),
                       evalctx: (() => { try { return sandbox.isEvalWorkbenchContext(); } catch (e) { return 'ERR:' + e.message; } })() };
        sandbox.submitSaveEdited(false);
        out._after = { fetchN: fetchCalls.length };
    } catch (e) { err = String(e); }
    sandbox.validateManualEntry = origVME;
    const saveCalls = fetchCalls.filter(c => c.url.indexOf('/api/save_edited') === 0);
    out.fetchCalls.push({
        case: name, dispatched: saveCalls.length === 1 && saveCalls[0].method === 'POST',
        payload: saveCalls.length ? saveCalls[0].body : null,
        csrf_header: saveCalls.length ? (saveCalls[0].headers['X-Requested-With'] || null) : null,
        toasts: toasts.slice(), error: err, pre_issues: preIssues,
    });
}

runCase('credit_note_neg_amount', 'credit_note', -10, 1, 10, -10);
runCase('credit_note_neg_qty', 'credit_note', -20, -2, 10, -20);
runCase('printed_neg_total', 'printed_delivery_note', -10, 1, 10, -10);
runCase('credit_note_double_neg', 'credit_note', 10, -2, -5, 10);

console.log(JSON.stringify(out, null, 1));
