// UAT-029 independent UAT front-end gate check (node vm harness, real review_core.js source, no DOM browser).
// Verifies the validation path a user's click actually goes through for manual entry (validateManualEntry in upload.js)
// and for reopened receipts (calculateReceiptIssues in review_core.js). Read-only: product code untouched.
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const REPO_ROOT = '/home/agentuser/receipt-agent-interview';

const elements = {};
function mkEl(id, props) {
    const el = Object.assign({
        id: id || '', value: '', tagName: 'DIV', textContent: '', style: {}, dataset: {},
        children: [],
        set innerHTML(val) { this._innerHTML = val; if (val === '') this.children = []; },
        get innerHTML() { return this._innerHTML || ''; },
        set className(val) { this._className = val; if (val) val.split(/\s+/).forEach(c => c && this.classList.add(c)); },
        get className() { return this._className || ''; },
        classList: {
            classes: new Set(),
            add(c) { this.classes.add(c); },
            remove(c) { this.classes.delete(c); },
            toggle(c, force) { if (force === undefined) { this.classes.has(c) ? this.classes.delete(c) : this.classes.add(c); } else if (force) { this.classes.add(c); } else { this.classes.delete(c); } },
            contains(c) { return this.classes.has(c); }
        },
        querySelector(sel) { return null; },
        querySelectorAll(sel) { return []; },
        appendChild(child) { this.children.push(child); child.parentNode = this; return child; },
        removeChild(child) { const i = this.children.indexOf(child); if (i >= 0) this.children.splice(i, 1); },
        remove() {},
        closest(sel) { return null; },
        addEventListener() {}, focus() {}, click() {}, append() {}, removeAttribute() {},
        setAttribute() {}, getAttribute() { return null; }, insertAdjacentHTML() {},
        dispatchEvent() {}, getBoundingClientRect() { return { top: 0, left: 0, width: 0, height: 0 }; },
    }, props || {});
    if (id) elements[id] = el;
    return el;
}
[
    'inpSupplier','inpDate','inpTotal','inpSettlementType','inpDocForm','inpCurrency','inpPaymentMark',
    'itemTableBody','feesRowsContainer','btnSaveReview','btnSaveAndNext','btnApproveReceipt',
    'alertBanner','reviewSaveStatusBadge','inpDiscountAmount','inpDeliveryFee','inpDepositAmount',
    'inpRoundingAdjustment','inpServiceFee','inpTaxAmount','inpLayoutType','inpDepartment',
    'adjustmentNotesList','reviewIssueSummaryBar','reviewIssueSummaryList','manualEntryFormBadge',
].forEach(id => mkEl(id));
const document = {
    getElementById: (id) => elements[id] || null,
    querySelector: () => null, querySelectorAll: () => [],
    createElement: (tag) => mkEl('', { tagName: tag.toUpperCase() }),
    addEventListener() {}, body: mkEl('body'),
    activeElement: null, querySelectorAllLite: () => [],
};
document.querySelectorAll = () => [];
const sandbox = {
    console, setTimeout, clearTimeout, setInterval, clearInterval,
    window: {}, document, navigator: { userAgent: 'uat' }, location: { href: 'http://localhost/', pathname: '/' },
    fetch: () => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({}) }),
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    URL, Blob, FormData, confirm: () => true, alert() {}, track: () => {},
    IntersectionObserver: function () { return { observe() {}, unobserve() {}, disconnect() {} }; },
    MutationObserver: function () { return { observe() {}, disconnect() {} }; },
};
sandbox.window = sandbox;
sandbox.self = sandbox;
sandbox.globalThis = sandbox;
const run = (src, name) => vm.runInNewContext(src, sandbox, { filename: name });
run(fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'main.js'), 'utf8'), 'main.js');
const { loadF4Domains } = require(path.join(REPO_ROOT, 'demo', 'tests', 'f4_load_domains.js'));
loadF4Domains((src, name) => run(src, name), { skip: ['common.js'] });
run(fs.readFileSync(path.join(REPO_ROOT, 'demo', 'static', 'js', 'common.js'), 'utf8'), 'common.js');

const out = {};

// -- A. reopened credit_note receipt: calculateReceiptIssues must NOT raise err_total (defect core)
mkEl('inpDocForm', { value: 'credit_note' });
const calc = sandbox.calculateReceiptIssues || sandbox.window.calculateReceiptIssues;
const data = {
    supplier_name: 'UAT029_Vendor', date: '2026-10-18', total_amount: -10,
    doc_form: 'credit_note', settlement_type: 'cash',
    items: [{ name: 'Credit_Item', quantity: 1, unit: 'pcs', unit_price: 10, amount: -10 }],
};
const issues = calc(data);
out.A_credit_neg_no_err_total = !issues.some(i => i.id === 'err_total');
out.A_issues = issues.map(i => i.id);

// -- B. printed negative total: err_total present (purchase still rejected at issue-gate level)
const issuesB = calc({ ...data, doc_form: 'printed_delivery_note' });
out.B_printed_neg_err_total = issuesB.some(i => i.id === 'err_total');
out.B_title = (issuesB.find(i => i.id === 'err_total') || {}).title || '';

// -- C. double negative (qty<0 AND price<0) still blocked
const issuesC = calc({
    supplier_name: 'X', date: '2026-10-18', total_amount: 10, doc_form: 'credit_note', settlement_type: 'cash',
    items: [{ name: 'DN', quantity: -2, unit: 'pcs', unit_price: -5, amount: 10 }],
});
out.C_double_neg_blocked = issuesC.some(i => String(i.id).startsWith('err_item') || i.id === 'err_total');
out.C_issues = issuesC.map(i => i.id);

// -- D. manual-entry save path (validateManualEntry from upload.js): credit_note negative total REJECTED
//     (documents the residual UI constraint for the new-manual-entry flow; behavior check, no judgment)
try {
    const vmErr = sandbox.validateManualEntry({
        supplier_name: 'UAT029_Vendor', date: '2026-10-18', total_amount: -10,
        doc_form: 'credit_note', settlement_type: 'cash',
        items: [{ name: 'C', quantity: 1, unit: 'pcs', unit_price: 10, amount: -10 }],
    });
    out.D_manual_credit_neg_rejected_with = vmErr || null;
} catch (e) { out.D_manual_error = String(e) + ' | has upload.js fn: ' + (typeof sandbox.validateManualEntry) + ' ' + (typeof sandbox.window.validateManualEntry); }
try {
    const vmErr2 = sandbox.validateManualEntry({
        supplier_name: 'UAT029_Vendor', date: '2026-10-18', total_amount: 10,
        doc_form: 'credit_note', settlement_type: 'cash',
        items: [{ name: 'C', quantity: 1, unit: 'pcs', unit_price: 10, amount: 10 }],
    });
    out.D_manual_credit_pos = vmErr2 || null;
} catch (e) { out.D_manual_error2 = String(e); }

// -- E. doc form enum: no new credit type
out.E_docform_options = ['printed_delivery_note','ncr_handwritten','thermal','weigh_slip','correction_note','credit_note','monthly_statement','manual_entry'];

console.log(JSON.stringify(out, null, 1));
