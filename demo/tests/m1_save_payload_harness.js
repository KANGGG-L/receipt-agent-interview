// M1 验收工具：在 node vm 沙箱里载入真实 main.js，调用真实的前端保存函数，
// 把产物（save payload）打到 stdout。用法：
//   node m1_save_payload_harness.js <main.js 路径> <mode>
// mode:
//   funnel         -> buildSavePayloadFromData（AI 自动保存路径，预填数据带 T5 两列）
//   form           -> collectReviewFormData -> buildSavePayloadFromData（复核保存路径）
//   funnel-missing -> 预填数据缺列/非法值（confidence='high'、警告为空串）
//   form-missing   -> 行 dataset 无 T5 两列（等价旧单据）
// 只做只读计算，不发任何网络请求（沙箱内 fetch 被禁用）。
const fs = require('fs');
const path = require('path');
// 默认路径基于脚本自身位置解析，cwd 任意均可（原先的 cwd 相对默认值会让「跑齐 harness」假失败）
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const mode = process.argv[3] || 'funnel';
const src = fs.readFileSync(mainPath, 'utf8');

const store = {};
function mkEl(props) {
    return Object.assign({
        value: '', tagName: 'INPUT', textContent: '', style: {}, dataset: {},
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {}, appendChild() {}, closest() { return null; },
    }, props || {});
}

// 复核表单（Tab1）用到的固定控件
const form = {
    inpSupplier: mkEl({ value: '祥興欄' }),
    inpDate: mkEl({ value: '2026-09-19' }),
    inpSheet: mkEl({ value: '2026-09' }),
    inpTotal: mkEl({ value: '50.00' }),
    inpSettlementType: mkEl({ value: 'cash' }),
    inpDepartmentId: mkEl({ value: '' }),
    inpCurrency: mkEl({ value: 'HKD' }),
    inpDocForm: mkEl({ value: 'printed_delivery_note' }),
    inpPaymentMark: mkEl({ value: '', tagName: 'SELECT' }),
    feesRowsContainer: mkEl({ querySelectorAll() { return []; } }),
    notesRowsContainer: mkEl({ querySelectorAll() { return []; } }),
    unitsDatalist: mkEl({ querySelectorAll() { return []; } }),
};

// 单行明细：dataset 里的 T5 两列由 appendTableRow 在渲染期写入
const row = mkEl({
    dataset: {
        confidence: '0.4',
        unitConversionWarning: '包含街市花码，需人工核验',
        evidence: '',
    },
    querySelector(sel) {
        const map = {
            '.inp-name': { value: '本地菜心' },
            '.inp-qty': { value: '5' },
            '.inp-unit': { value: '斤' },
            '.inp-price': { value: '10' },
            '.inp-amount': { value: '50.00' },
            '.inp-actual-qty': { value: '' },
            '.inp-dept': { value: '' },
            '.inp-sku-id': null,
        };
        return sel in map ? map[sel] : null;
    },
});
form.itemTableBody = mkEl({ querySelectorAll(sel) { return sel === 'tr' ? [row] : []; } });

const sandbox = {
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
    },
    console,
    setTimeout, clearTimeout,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.fetch = () => Promise.reject(new Error('harness: 网络被禁用'));
sandbox.document = {
    addEventListener() {},
    getElementById(id) { return id in form ? form[id] : null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return mkEl({ tagName: 'DIV' }); },
    documentElement: { style: { setProperty() {} } },
    body: null, readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

let data;
if (mode === 'form' || mode === 'form-missing') {
    if (mode === 'form-missing') {
        row.dataset.confidence = '';
        row.dataset.unitConversionWarning = '';
    }
    data = sandbox.collectReviewFormData();
    data.source = 'auto';
} else if (mode === 'funnel-missing') {
    data = {
        source: 'auto',
        supplier_name: '祥興欄',
        date: '2026-09-19',
        total_amount: 50,
        settlement_type: 'cash',
        items: [{
            name: '本地菜心', quantity: 5, unit: '斤', unit_price: 10, amount: 50,
            confidence: 'high',
            unit_conversion_warning: '',
        }],
    };
} else {
    data = {
        source: 'auto',
        supplier_name: '祥興欄',
        date: '2026-09-19',
        total_amount: 50,
        settlement_type: 'cash',
        items: [{
            name: '本地菜心', quantity: 5, unit: '斤', unit_price: 10, amount: 50,
            confidence: 0.40,
            unit_conversion_warning: '包含街市花码，需人工核验',
        }],
    };
}
process.stdout.write(JSON.stringify(sandbox.buildSavePayloadFromData(data, 1)));
