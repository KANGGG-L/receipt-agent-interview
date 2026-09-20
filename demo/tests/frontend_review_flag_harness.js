// 行级/整单级「需人工复核」标记的验收脚手架：在 node vm 沙箱里载入**真实 main.js**，
// 调用生产函数本体（itemReviewFlags / itemReviewBadgeHtml / receiptReviewReasons）
// 以及两处表格渲染函数（appendTableRow / appendArcTableRow），把真实 DOM 产物（innerHTML）
// 打成可断言的 JSON 输出。只做只读计算，沙箱内不发任何网络请求。
//
// 用法（cwd 任意）：node demo/tests/frontend_review_flag_harness.js <main.js 路径> [真实单据详情 JSON 路径]
//   第二个参数可选：把一份真实单据详情 payload（GET /api/receipt/{id} 的 data 字段）喂给
//   生产函数 receiptReviewReasons，验证整单级规则在**真实数据**上是否按预期触发。
const fs = require('fs');
const path = require('path');
// 默认路径基于脚本自身位置解析，cwd 任意均可（原先的 cwd 相对默认值会让「跑齐 harness」假失败）
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const liveJsonPath = process.argv[3] || '';
const src = fs.readFileSync(mainPath, 'utf8');

function mkEl(props) {
    const el = Object.assign({
        value: '', tagName: 'DIV', textContent: '', innerText: '', innerHTML: '',
        style: {}, dataset: {}, children: [],
        classList: { add() {}, remove() {}, toggle() {}, contains() { return false; } },
        querySelector() { return null; },
        querySelectorAll() { return []; },
        addEventListener() {}, appendChild(c) { this.children.push(c); }, closest() { return null; },
        setAttribute() {}, getAttribute() { return null; },
    }, props || {});
    return el;
}

const itemTableBody = mkEl({ tagName: 'TBODY' });
const arcTableBody = mkEl({ tagName: 'TBODY' });

// 收集沙箱内的 console 输出：用于断言「异常被静默吞掉」已被可观测化（console.warn）
const sandboxWarns = [];
const sandboxErrors = [];
const sandboxConsole = {
    log() {}, info() {}, debug() {},
    warn(...a) { sandboxWarns.push(a.map(String).join(' ')); },
    error(...a) { sandboxErrors.push(a.map(String).join(' ')); },
};

const sandbox = { console: sandboxConsole, setTimeout, clearTimeout };
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.document = {
    addEventListener() {},
    getElementById(id) {
        if (id === 'itemTableBody') return itemTableBody;
        if (id === 'arcTableBody') return arcTableBody;
        return null;
    },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: null, readyState: 'loading',
};
// 沙箱内禁用网络：任何误发的请求都会抛出，保证本脚手架零副作用
sandbox.fetch = () => { throw new Error('harness 沙箱禁止网络请求'); };

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }

// 主脚本执行完后 sandbox 已被 contextify；用同一 realm 的后续脚本注入 currentReceiptData
// （顶层 `let` 落在该 realm 的全局词法环境，跨 runInContext 可见）。
function injectReceiptData(code) { return vm.runInContext(code, sandbox, { filename: 'harness-inject.js' }); }
const loadWarnCount = sandboxWarns.length;

// 让 deptSelectOptionsHtml / updateGlobalDatalistUnits 一类渲染依赖可用（真实函数优先）
sandbox.departments = sandbox.departments || [];

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; } else { fail++; failures.push(name + ' -- ' + detail); }
}

const itemReviewFlags = sandbox.itemReviewFlags;
const itemReviewBadgeHtml = sandbox.itemReviewBadgeHtml;
const receiptReviewReasons = sandbox.receiptReviewReasons;
const appendTableRow = sandbox.appendTableRow;
const appendArcTableRow = sandbox.appendArcTableRow;

ok('B0 生产函数均已导出',
    typeof itemReviewFlags === 'function' && typeof itemReviewBadgeHtml === 'function'
    && typeof receiptReviewReasons === 'function'
    && typeof appendTableRow === 'function' && typeof appendArcTableRow === 'function',
    [typeof itemReviewFlags, typeof itemReviewBadgeHtml, typeof receiptReviewReasons,
     typeof appendTableRow, typeof appendArcTableRow].join(','));

// ---- A. 行级判据 itemReviewFlags ----
const HUAMA = { name: '菜心', unit_conversion_warning: '包含街市花码，需人工核验', confidence: 0.9 };
ok('A1 花码行命中', itemReviewFlags(HUAMA).length === 1, JSON.stringify(itemReviewFlags(HUAMA)));
ok('A2 花码行角标文案', itemReviewFlags(HUAMA)[0] && itemReviewFlags(HUAMA)[0].label === '花码待核验',
    JSON.stringify(itemReviewFlags(HUAMA)));

// 关键反例：模型自报低置信 0.40 但无确定性信号 → 不再标记（证明触发依据已切换）
const LOWCONF_ONLY = { name: '走地鸡', confidence: 0.40, unit_conversion_warning: null };
ok('A3 仅低置信不再命中', itemReviewFlags(LOWCONF_ONLY).length === 0,
    JSON.stringify(itemReviewFlags(LOWCONF_ONLY)));

// 关键正例：高置信 + 确定性信号 → 仍标记（说明标记不再看置信度）
const HUAMA_HIGH = { name: '菜心', unit_conversion_warning: '包含街市花码，需人工核验', confidence: 0.98 };
ok('A4 高置信但含花码仍命中', itemReviewFlags(HUAMA_HIGH).length === 1,
    JSON.stringify(itemReviewFlags(HUAMA_HIGH)));

ok('A5 空串/空白不命中',
    itemReviewFlags({ unit_conversion_warning: '' }).length === 0
    && itemReviewFlags({ unit_conversion_warning: '   ' }).length === 0,
    'empty/blank 应不命中');
ok('A6 缺失字段不命中',
    itemReviewFlags({ name: 'x' }).length === 0 && itemReviewFlags(null).length === 0,
    'missing/null 应不命中');
const unitOnly = itemReviewFlags({ unit_conversion_warning: '斤/公斤不可折算' });
ok('A7 非花码警告走「单位待核验」',
    unitOnly.length === 1 && unitOnly[0].label === '单位待核验',
    JSON.stringify(unitOnly));

// ---- B. 行级角标 itemReviewBadgeHtml ----
const badgeHtml = itemReviewBadgeHtml(itemReviewFlags(HUAMA), 0.9);
ok('B1 命中渲染 item-conf-badge', badgeHtml.indexOf('class="item-conf-badge"') !== -1, badgeHtml);
ok('B2 角标文案为待核验类而非「低置信」',
    badgeHtml.indexOf('花码待核验') !== -1 && badgeHtml.indexOf('低置信') === -1, badgeHtml);
ok('B3 title 带原因', badgeHtml.indexOf('包含街市花码，需人工核验') !== -1, badgeHtml);
ok('B4 title 带置信度参考值', badgeHtml.indexOf('识别置信度 0.90') !== -1, badgeHtml);
ok('B5 无标记返回空串', itemReviewBadgeHtml([], 0.9) === '', itemReviewBadgeHtml([], 0.9));
ok('B6 置信度缺失不炸', itemReviewBadgeHtml(itemReviewFlags(HUAMA), null)
    .indexOf('识别置信度') === -1, itemReviewBadgeHtml(itemReviewFlags(HUAMA), null));

// ---- C. 复核台真实 DOM 产物（appendTableRow） ----
itemTableBody.children.length = 0;
appendTableRow(HUAMA_HIGH);
const trHuama = itemTableBody.children[itemTableBody.children.length - 1];
const trHuamaHtml = (trHuama && trHuama.innerHTML) || '';
ok('C1 复核台：花码行渲染出角标', trHuamaHtml.indexOf('item-conf-badge') !== -1,
    trHuamaHtml.slice(0, 200));
ok('C2 复核台：角标文案为花码待核验', trHuamaHtml.indexOf('花码待核验') !== -1,
    trHuamaHtml.slice(0, 400));
ok('C3 复核台：命中行带黄底 row-warning',
    !!(trHuama && String(trHuama.className || '').indexOf('row-warning') !== -1),
    String(trHuama && trHuama.className));
ok('C4 复核台：置信度仍随行回传（dataset.confidence=0.98）',
    !!(trHuama && trHuama.dataset && trHuama.dataset.confidence === '0.98'),
    JSON.stringify(trHuama && trHuama.dataset));

itemTableBody.children.length = 0;
appendTableRow(LOWCONF_ONLY);
const trLow = itemTableBody.children[itemTableBody.children.length - 1];
ok('C5 复核台：仅低置信（无确定性信号）不出角标',
    !!trLow && String(trLow.innerHTML || '').indexOf('item-conf-badge') === -1,
    String(trLow && trLow.innerHTML).slice(0, 200));
ok('C6 复核台：仅低置信行不标黄',
    !!trLow && String(trLow.className || '').indexOf('row-warning') === -1,
    String(trLow && trLow.className));
// 置信度保留为参考信息：有读数的行在品名输入框 title 里给出，且不影响是否标记
ok('C7 复核台：无标记但有置信度的行仍带参考 title',
    !!trLow && String(trLow.innerHTML || '').indexOf('识别置信度 0.40') !== -1,
    String(trLow && trLow.innerHTML).slice(0, 300));
ok('C8 复核台：无置信度读数的行不带该 title',
    (function () {
        itemTableBody.children.length = 0;
        appendTableRow({ name: '无读数行', unit_conversion_warning: '包含街市花码，需人工核验' });
        const t = itemTableBody.children[itemTableBody.children.length - 1];
        return !!t && String(t.innerHTML || '').indexOf('识别置信度') === -1
            && String(t.innerHTML || '').indexOf('item-conf-badge') !== -1;
    })(), '无置信度时角标仍应出、但不应有置信度 title');

// ---- D. 归档详情真实 DOM 产物（appendArcTableRow） ----
arcTableBody.children.length = 0;
appendArcTableRow(HUAMA_HIGH);
const arcHuama = arcTableBody.children[arcTableBody.children.length - 1];
ok('D1 归档详情：花码行渲染出角标',
    !!arcHuama && String(arcHuama.innerHTML || '').indexOf('item-conf-badge') !== -1,
    String(arcHuama && arcHuama.innerHTML).slice(0, 200));
ok('D2 归档详情：角标文案与复核台同源（花码待核验）',
    !!arcHuama && String(arcHuama.innerHTML || '').indexOf('花码待核验') !== -1,
    String(arcHuama && arcHuama.innerHTML).slice(0, 400));

arcTableBody.children.length = 0;
appendArcTableRow(LOWCONF_ONLY);
const arcLow = arcTableBody.children[arcTableBody.children.length - 1];
ok('D3 归档详情：仅低置信不出角标',
    !!arcLow && String(arcLow.innerHTML || '').indexOf('item-conf-badge') === -1,
    String(arcLow && arcLow.innerHTML).slice(0, 200));

// ---- E. 整单级判据 receiptReviewReasons ----
const HANDRW = { doc_form: 'ncr_handwritten', math_warnings: [], status: 'parsed' };
ok('E1 手写单命中', receiptReviewReasons(HANDRW).length === 1, JSON.stringify(receiptReviewReasons(HANDRW)));
const GATE = { doc_form: 'printed_delivery_note', math_warnings: ['算术门禁: 明细合计=100，但总额=120（差20）'] };
const gateReasons = receiptReviewReasons(GATE);
ok('E2 门禁警告命中', gateReasons.length === 1, JSON.stringify(gateReasons));
ok('E3 门禁原因经 humanize 人话化',
    gateReasons[0] && gateReasons[0].indexOf('算术门禁') === -1 && gateReasons[0].indexOf('相差') !== -1,
    JSON.stringify(gateReasons));
const BOTH = { doc_form: 'ncr_handwritten', math_warnings: ['检测到街市花码（苏州码子），已强制降权并推送人工复核'] };
ok('E4 手写单+花码警告：两条原因', receiptReviewReasons(BOTH).length === 2,
    JSON.stringify(receiptReviewReasons(BOTH)));
ok('E5 普通印刷单无警告不命中',
    receiptReviewReasons({ doc_form: 'printed_delivery_note', math_warnings: [] }).length === 0,
    JSON.stringify(receiptReviewReasons({ doc_form: 'printed_delivery_note', math_warnings: [] })));
ok('E6 重复警告去重',
    receiptReviewReasons({ doc_form: 'printed_delivery_note', math_warnings: ['明细为空', '明细为空'] }).length === 1,
    JSON.stringify(receiptReviewReasons({ doc_form: 'printed_delivery_note', math_warnings: ['明细为空', '明细为空'] })));
ok('E7 空入参不炸', receiptReviewReasons(null).length === 0, JSON.stringify(receiptReviewReasons(null)));

// ---- H. 静默吞异常收口：整单级信号读取失败必须可观测 + fail-visible ----
// 原实现 `try { ...quality_warnings/review_priority_score... } catch (e) {}`：
// 一旦抛错，「质量预检/重点复核」从每一行静默消失且没有任何痕迹。
ok('H0 readReceiptLevelReviewSignals 已导出',
    typeof sandbox.readReceiptLevelReviewSignals === 'function', typeof sandbox.readReceiptLevelReviewSignals);
// H0b 静态接线：appendTableRow 内部不得再有空 catch，且必须走新的 fail-visible 读取器。
// （main.js 其他位置还有历史空 catch，属既有观测性问题，不在本次改动范围内，故按函数体定位。）
var _atrStart = src.indexOf('function appendTableRow(');
var _atrEnd = src.indexOf('function addEmptyRow(');
var _atrBody = (_atrStart >= 0 && _atrEnd > _atrStart) ? src.slice(_atrStart, _atrEnd) : '';
ok('H0b appendTableRow 内已无空 catch 块',
    _atrBody.length > 0 && _atrBody.indexOf('catch (e) {}') === -1, '仍残留空 catch');
ok('H0b2 appendTableRow 已改用 readReceiptLevelReviewSignals',
    _atrBody.indexOf('readReceiptLevelReviewSignals()') !== -1, '未接入可观测读取器');

function lastRow() { return itemTableBody.children[itemTableBody.children.length - 1]; }
function renderWith(receiptCode, item) {
    itemTableBody.children.length = 0;
    sandboxWarns.length = 0;
    injectReceiptData(receiptCode);
    appendTableRow(item || { name: '走地鸡' });
    return lastRow();
}

// H1 正常单据：无整单级信号 → 不误标、无 warning
var r1 = renderWith('currentReceiptData = { quality_warnings: [], review_priority_score: 0 };');
ok('H1 无整单级信号：不标黄、不误报',
    String(r1.className || '').indexOf('row-warning') === -1 && sandboxWarns.length === 0,
    'cls=' + r1.className + ' warns=' + JSON.stringify(sandboxWarns));

// H2/H3 整单级信号仍生效（重构后未被削弱）
var r2 = renderWith('currentReceiptData = { quality_warnings: ["image_blur"], review_priority_score: 0 };');
ok('H2 质量预检仍触发行黄底',
    String(r2.className || '').indexOf('row-warning') !== -1, String(r2.className));
var r3 = renderWith('currentReceiptData = { quality_warnings: [], review_priority_score: 0.7 };');
ok('H3 重点复核仍触发行黄底',
    String(r3.className || '').indexOf('row-warning') !== -1, String(r3.className));

// H4 读取抛错：必须 console.warn 留痕 + 行标「待核验」（fail-visible，不静默消失）
var r4 = renderWith(
    'currentReceiptData = { get quality_warnings() { throw new Error("注入：quality_warnings 读取失败"); } };');
var r4html = String(r4.innerHTML || '');
ok('H4a 读取失败有 console.warn 留痕', sandboxWarns.length >= 1, JSON.stringify(sandboxWarns));
ok('H4b 读取失败仍渲染出「待核验」角标（fail-visible）',
    r4html.indexOf('item-conf-badge') !== -1 && r4html.indexOf('待核验') !== -1,
    r4html.slice(0, 260));
ok('H4c 读取失败的行仍标黄',
    String(r4.className || '').indexOf('row-warning') !== -1, String(r4.className));

// H5 失败标记不应污染本该正常的行（读取恢复正常后回到无标记）
var r5 = renderWith('currentReceiptData = null;');
ok('H5 恢复正常后不再带「待核验」',
    String(r5.innerHTML || '').indexOf('item-conf-badge') === -1
    && String(r5.className || '').indexOf('row-warning') === -1,
    String(r5.className) + '|' + String(r5.innerHTML).slice(0, 120));
injectReceiptData('currentReceiptData = null;');

const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;
const allText = [badgeHtml].concat(receiptReviewReasons(BOTH)).join(' ');
ok('F1 文案无 emoji', !EMOJI.test(allText), allText);

// ---- G. 真实单据活体校验（可选，仅当传入真实 payload 路径） ----
let liveCheck = null;
if (liveJsonPath && fs.existsSync(liveJsonPath)) {
    const raw = JSON.parse(fs.readFileSync(liveJsonPath, 'utf8'));
    const liveData = (raw && raw.data) ? raw.data : raw;
    const liveReasons = receiptReviewReasons(liveData);
    const liveItems = Array.isArray(liveData.items) ? liveData.items : [];
    const flaggedRows = liveItems.map(function (it, i) { return { i: i, flags: itemReviewFlags(it) }; })
        .filter(function (r) { return r.flags.length > 0; });
    liveCheck = {
        receipt_id: liveData.receipt_id || raw.receipt_id || null,
        doc_form: liveData.doc_form, status: liveData.status,
        math_warnings: liveData.math_warnings || [],
        item_count: liveItems.length,
        row_flagged_count: flaggedRows.length,
        reasons: liveReasons,
    };
}

console.log(JSON.stringify({
    pass: pass, fail: fail, failures: failures, live: liveCheck,
    sandboxWarnsAtLoad: loadWarnCount,
    sample: { badgeHtml: badgeHtml, huamaRowHasBadge: trHuamaHtml.indexOf('item-conf-badge') !== -1,
              lowConfRowHasBadge: String(trLow && trLow.innerHTML).indexOf('item-conf-badge') !== -1 },
}, null, 0));
process.exit(fail === 0 ? 0 : 1);
