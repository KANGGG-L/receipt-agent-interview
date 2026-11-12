// U03 信用工作台 harness：金额展示纪律（未知≠0）、抵扣 payload 校验、无自动抵扣文案。
// 运行：node demo/tests/test_credit_workbench_harness.js（抽取 finance.js 纯函数）
const fs = require('fs');
const path = require('path');
const src = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'finance.js'), 'utf8');
// 抽取被测纯函数（与 test_memory_metrics_panel.js 同机制）
function extract(name) {
    const re = new RegExp('function ' + name + '\\([^)]*\\) \\{[\\s\\S]*?\\n}', 'm');
    const m = src.match(re);
    if (!m) throw new Error('function not found: ' + name);
    return m[0];
}
const sandbox = {};
eval(extract('creditMinorToDisplay'));
eval(extract('creditWorkbenchHtml'));
eval(extract('buildCreditAllocatePayload'));
const fnCreditMinorToDisplay = creditMinorToDisplay;
const fnCreditWorkbenchHtml = creditWorkbenchHtml;
const fnBuildCreditAllocatePayload = buildCreditAllocatePayload;

let pass = 0, fail = 0;
function check(name, cond) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name); }
}

// 1. 金额展示：0 是真实余额；null/undefined/空串是「未知」；NaN/Infinity 是「未知」
check('0 minor → HK$ 0.00（不冒充未知）', fnCreditMinorToDisplay(0) === 'HK$ 0.00');
check('null → 未知', fnCreditMinorToDisplay(null) === '未知');
check('undefined → 未知', fnCreditMinorToDisplay(undefined) === '未知');
check('NaN → 未知', fnCreditMinorToDisplay(NaN) === '未知');
check('7000 minor → HK$ 70.00', fnCreditMinorToDisplay(7000) === 'HK$ 70.00');

// 2. 工作台渲染：空列表空态文案；非空列表含供应商行与显式抵扣按钮
const emptyHtml = fnCreditWorkbenchHtml([]);
check('空列表 → 空态文案（不是 0 抵扣）', emptyHtml.indexOf('没有未用完的供应商信用') !== -1);
const html = fnCreditWorkbenchHtml([
    { supplier_id: 1, supplier_name: 'Vendor_A', face_total_minor: 7000, available_minor: 4000 },
    { supplier_id: 2, supplier_name: null, face_total_minor: null, available_minor: null },
]);
check('非空 → 供应商名渲染', html.indexOf('Vendor_A') !== -1);
check('面额 7000 → HK$ 70.00', html.indexOf('HK$ 70.00') !== -1);
check('可用 4000 → HK$ 40.00', html.indexOf('HK$ 40.00') !== -1);
check('null 面额 → 未知（不折 0）', html.indexOf('未知') !== -1);
check('每行都有显式抵扣按钮', (html.match(/credit-allocate-btn/g) || []).length === 2);
check('无供应商名 → 编号回退（不空白）', html.indexOf('供应商 #2') !== -1);
check('声明「系统不会自动抵任何一单」（D-03 口径在界面可见）',
    html.indexOf('系统不会自动抵任何一单') !== -1);

// 3. 抵扣 payload：缺失/非法一律 null（不发请求）
check('正常 → payload 结构', JSON.stringify(fnBuildCreditAllocatePayload(1, 9, 30000, 'k1'))
    === JSON.stringify({ idempotency_key: 'k1', targets: [{ receipt_id: 9, amount_minor: 30000 }] }));
check('缺 receipt → null', fnBuildCreditAllocatePayload(1, null, 100, 'k') === null);
check('金额 0 → null', fnBuildCreditAllocatePayload(1, 9, 0, 'k') === null);
check('金额负 → null', fnBuildCreditAllocatePayload(1, 9, -5, 'k') === null);
check('金额非数 → null', fnBuildCreditAllocatePayload(1, 9, 'abc', 'k') === null);
check('缺幂等键 → null', fnBuildCreditAllocatePayload(1, 9, 100, '') === null);
check('小数金额 → 四舍五入整数分', fnBuildCreditAllocatePayload(1, 9, 100.4, 'k')
    .targets[0].amount_minor === 100);

console.log('\n' + pass + ' passed / ' + fail + ' failed');
process.exit(fail ? 1 : 0);
