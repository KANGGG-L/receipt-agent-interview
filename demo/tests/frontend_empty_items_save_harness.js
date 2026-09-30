// 空明细复核表单「加行后可保存」+「保存失败不静默」的验收脚手架。
//
// 背景（UAT 阻塞级缺陷）：renderEditForm 在 items=[] 时往 #itemTableBody 写入一条
// **不含任何 input** 的占位行（tr.warning-row）；「+ 新增明细行」只追加、不清占位；
// collectReviewFormData 对每一行无条件取 .inp-name.value → 命中占位行抛
// TypeError，异常冒泡出 submitSaveEdited → 不发任何请求、界面无任何提示（用户卡死）。
//
// 本脚手架在 node vm 沙箱里载入**真实 main.js**，调用生产函数本体：
//   renderEditForm（写入占位行）/ addEmptyRow（+ 新增明细行）/ collectReviewFormData（采集）
//   / submitSaveEdited（保存入口，含失败提示）/ showToast（用户可见提示）
// 断言三层：
//   ① 加行时占位行被移除；
//   ② 采集侧兜底——占位行（无 .inp-name）既不进提交也不抛错；
//   ③ 保存失败路径不静默（人话提示 + [WARN] 留痕），且不误发请求。
//
// 沙箱内零真实网络：fetch 由本脚本注入的记录器接管，只记录、只回 200 success。
// 用法（cwd 任意）：node demo/tests/frontend_empty_items_save_harness.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const src = fs.readFileSync(mainPath, 'utf8');

let pass = 0;
let fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; } else { fail++; failures.push(name + ' -- ' + detail); }
}

// ---------------------------------------------------------------
// 最小 DOM 面（只覆盖本脚手架调用到的生产代码路径）
// ---------------------------------------------------------------
function mkClassList() {
    const s = new Set();
    return {
        _s: s,
        add(...c) { c.forEach(x => s.add(x)); },
        remove(...c) { c.forEach(x => s.delete(x)); },
        toggle(c, on) { if (on === undefined) { s.has(c) ? s.delete(c) : s.add(c); } else if (on) s.add(c); else s.delete(c); },
        contains(c) { return s.has(c); },
    };
}

function mkEl(props) {
    const el = Object.assign({
        tagName: 'DIV', id: '', className: '', textContent: '', innerText: '', value: '',
        style: {}, dataset: {}, children: [], disabled: false, type: '',
        isConnected: false, parentElement: null, parentNode: null,
        classList: mkClassList(),
        listeners: {},
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { c.parentElement = this; c.parentNode = this; this.children.push(c); return c; },
        removeChild(c) {
            const i = this.children.indexOf(c);
            if (i >= 0) this.children.splice(i, 1);
            return c;
        },
        remove() {
            this._removed = true;
            if (this.parentElement && this.parentElement.removeChild) this.parentElement.removeChild(this);
        },
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k] != null ? this[k] : null; },
        focus() {}, click() {}, scrollIntoView() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        closest() { return null; },
    }, props || {});
    // innerHTML 是纯字符串属性（不渲染）：占位行的存在性由 harness 显式同步
    el._html = '';
    el.innerHTML = '';
    return el;
}

// 从行 HTML 里解析 .inp-* 控件（只认 class / value 两个属性，足够本脚手架用）
const INP_TAG_RE = /<(input|select|textarea)\b[^>]*>/gi;
function parseInpControls(html) {
    const out = {};
    let m;
    INP_TAG_RE.lastIndex = 0;
    while ((m = INP_TAG_RE.exec(String(html || '')))) {
        const tag = m[0];
        const cls = (tag.match(/class="([^"]*)"/) || [])[1] || '';
        const val = (tag.match(/value="([^"]*)"/) || [])[1];
        cls.split(/\s+/).forEach(c => {
            if (c.indexOf('inp-') === 0) out['.' + c] = mkEl({ tagName: (m[1] || 'input').toUpperCase(), value: val === undefined ? '' : val });
        });
    }
    return out;
}

// 明细行：innerHTML 写入即解析出行内控件，供 tr.querySelector('.inp-xxx') 使用
function mkTr(props) {
    const tr = mkEl(Object.assign({ tagName: 'TR' }, props || {}));
    tr._inputs = {};
    Object.defineProperty(tr, 'innerHTML', {
        configurable: true,
        get() { return this._html; },
        set(v) {
            this._html = String(v);
            this._inputs = parseInpControls(this._html);
        },
    });
    tr.innerHTML = '';
    tr.querySelector = function (sel) {
        if (typeof sel === 'string' && sel.charAt(0) === '.' && Object.prototype.hasOwnProperty.call(this._inputs, sel)) {
            return this._inputs[sel];
        }
        return null;
    };
    return tr;
}

const bodyEl = mkEl({ tagName: 'BODY' });
const tbody = mkEl({ tagName: 'TBODY', id: 'itemTableBody' });
// tbody.innerHTML 写入即同步 children：renderEditForm 的占位行分支走的就是 innerHTML 赋值
Object.defineProperty(tbody, 'innerHTML', {
    configurable: true,
    get() { return this._html; },
    set(v) {
        this._html = String(v);
        this.children = [];
        const re = /<tr\b[^>]*>/gi;
        let m;
        while ((m = re.exec(this._html))) {
            const cls = (m[0].match(/class="([^"]*)"/) || [])[1] || '';
            const row = mkEl({ tagName: 'TR', className: cls });   // 占位行：无任何输入控件
            row.parentElement = this;
            row.parentNode = this;
            this.children.push(row);
        }
    },
});
tbody.innerHTML = '';
tbody.querySelectorAll = function (sel) {
    if (sel === 'tr') return this.children.slice();
    if (sel === 'tr.warning-row') {
        return this.children.filter(c => String(c.className || '').split(/\s+/).indexOf('warning-row') !== -1);
    }
    return [];
};

const byId = {};
function getEl(id) {
    if (id === 'itemTableBody') return tbody;
    if (!byId[id]) byId[id] = mkEl({ id: id });
    return byId[id];
}

// ---- fetch 记录器：只记录、只回 200 success，绝不发真实请求 ----
const fetchCalls = [];
function mkRes(status, bodyObj) {
    return {
        ok: status >= 200 && status < 300,
        status: status,
        json: () => Promise.resolve(bodyObj),
        clone: () => mkRes(status, bodyObj),
    };
}
function dispatcher(url, init) {
    let body = null;
    if (init && init.body) { try { body = JSON.parse(init.body); } catch (e) { body = init.body; } }
    fetchCalls.push({ url: String(url), method: (init && init.method) || 'GET', body: body });
    return Promise.resolve(mkRes(200, { status: 'success', receipt_id: 4321, version: 2 }));
}

const sandboxWarns = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {},
        warn(...a) { sandboxWarns.push(a.map(String).join(' ')); },
        error(...a) { sandboxWarns.push('[error] ' + a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    localStorage: {
        _s: {},
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
    fetch: dispatcher,
};
sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.document = {
    addEventListener() {},
    getElementById: getEl,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement(tag) {
        const t = String(tag || 'div').toLowerCase();
        if (t === 'tr') return mkTr({ tagName: 'TR' });
        return mkEl({ tagName: t.toUpperCase() });
    },
    documentElement: { style: { setProperty() {} } },
    body: bodyEl,
    readyState: 'loading',
};

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { sandboxWarns.push('load note: ' + e.message); }

function inject(code) { return vm.runInContext(code, sandbox); }

const renderEditForm = sandbox.renderEditForm;
const addEmptyRow = sandbox.addEmptyRow;
const collectReviewFormData = sandbox.collectReviewFormData;
const submitSaveEdited = sandbox.submitSaveEdited;

ok('B0 生产函数均已导出',
    typeof renderEditForm === 'function' && typeof addEmptyRow === 'function'
    && typeof collectReviewFormData === 'function' && typeof submitSaveEdited === 'function',
    [typeof renderEditForm, typeof addEmptyRow, typeof collectReviewFormData, typeof submitSaveEdited].join(','));

// 用户可见提示（真实 showToast 会把 .app-toast-msg 挂到 #toastContainer 里；
// 该容器可能挂在 document.body，也可能只被 getElementById 惰性创建 —— 两处都扫）
function toastMessages() {
    const out = [];
    const seen = new Set();
    const containers = Object.keys(byId).map(k => byId[k]).concat(bodyEl.children || []);
    containers.forEach(c => {
        if (!c || seen.has(c)) return;
        seen.add(c);
        (c.children || []).forEach(t => {
            (t.children || []).forEach(ch => {
                if (ch.className === 'app-toast-msg') out.push(String(ch.innerText || ''));
            });
        });
    });
    return out;
}
function clearToasts() {
    bodyEl.children.length = 0;
    Object.keys(byId).forEach(k => { byId[k].children.length = 0; });
}
function placeholderRows() { return tbody.querySelectorAll('tr.warning-row'); }
function realRows() { return tbody.children.filter(c => c.querySelector && c.querySelector('.inp-name')); }

// ---- ① 空明细渲染态 → 加行：占位行必须被移除 ----
renderEditForm({ receipt_id: null, items: [], quality_warnings: [], math_warnings: [] });
// 单据字段在 renderEditForm 之后填（renderEditForm 会按 data 覆盖它们，与真实链路一致）
getEl('inpSupplier').value = '测试供应商';
getEl('inpDate').value = '2026-01-05';
getEl('inpTotal').value = '100.00';
getEl('inpSettlementType').value = 'cash';
getEl('inpSheet').value = '2026-01';
ok('A1 空明细渲染出占位行（tr.warning-row）', placeholderRows().length === 1,
    'placeholder=' + placeholderRows().length);
ok('A2 占位行内无任何输入控件（这正是缺陷根因）',
    placeholderRows().length === 1 && placeholderRows()[0].querySelector('.inp-name') === null,
    '占位行不应含 .inp-name');

addEmptyRow();
const phAfter = placeholderRows();
ok('A3 加行后占位行已被移除', phAfter.length === 0, 'placeholder=' + phAfter.length);
ok('A4 加行后恰好 1 个真实明细行', realRows().length === 1, 'rows=' + realRows().length);

// ---- ② 采集侧兜底：占位行既不进提交也不抛错 ----
const row = realRows()[0];
row.querySelector('.inp-name').value = '走地鸡';
row.querySelector('.inp-qty').value = '2';
row.querySelector('.inp-unit').value = 'kg';
row.querySelector('.inp-price').value = '50';
row.querySelector('.inp-amount').value = '100';

let collected = null, collectErr = null;
try { collected = collectReviewFormData(); } catch (e) { collectErr = e; }
ok('B1 collectReviewFormData 不抛异常', collectErr === null, collectErr && (collectErr.stack || collectErr.message));
ok('B2 采集结果只含真实行（1 条）', collected && collected.items.length === 1,
    collected && JSON.stringify(collected.items));
ok('B3 采集到的就是真实行的值', collected && collected.items[0]
    && collected.items[0].name === '走地鸡'
    && Number(collected.items[0].quantity) === 2
    && Number(collected.items[0].unit_price) === 50,
    collected && JSON.stringify(collected.items[0]));

// 还原「占位行仍在表内」的最坏情形（例如未来别处又写回占位行）：
// 采集侧兜底必须独立成立——跳过无 .inp-name 的行，而不是抛错、更不是把它当空行提交。
const injectedPlaceholder = mkEl({ tagName: 'TR', className: 'warning-row' });
injectedPlaceholder.parentElement = tbody;
tbody.children.push(injectedPlaceholder);
let collected2 = null, collectErr2 = null;
try { collected2 = collectReviewFormData(); } catch (e) { collectErr2 = e; }
ok('B4 占位行残留时采集仍不抛错', collectErr2 === null, collectErr2 && (collectErr2.stack || collectErr2.message));
ok('B5 占位行不会混进提交（仍只有 1 条真实行）',
    collected2 && collected2.items.length === 1
    && collected2.items.every(it => String(it.name || '').trim() !== ''),
    collected2 && JSON.stringify(collected2.items));
tbody.children.pop();

// ---- ③ 保存入口：加行后真的发请求；采集失败不静默 ----
inject('isManualEntry = true; currentReceiptId = null;');
fetchCalls.length = 0;
clearToasts();
// 修复前此处会抛出未捕获异常（正是缺陷现场）；兜住以便把「断言失败」如实报出来，
// 而不是让脚手架自身崩掉（脚手架对未来的回归同样要能诊断）。
let saveErr = null;
try { submitSaveEdited(); } catch (e) { saveErr = e; }
const saveCalls = fetchCalls.filter(c => c.url.indexOf('/api/save_edited') === 0);
ok('C0 保存入口不向外抛未捕获异常（点保存不会把页面卡死）', saveErr === null,
    saveErr && String(saveErr));
ok('C1 加行后可保存：真的发出了 POST /api/save_edited',
    saveCalls.length === 1 && saveCalls[0].method === 'POST', JSON.stringify(fetchCalls.map(c => c.method + ' ' + c.url)));
ok('C2 提交载荷只含真实行（不含占位行）',
    saveCalls.length === 1 && saveCalls[0].body && saveCalls[0].body.items
    && saveCalls[0].body.items.length === 1
    && saveCalls[0].body.items[0].name === '走地鸡',
    saveCalls.length === 1 ? JSON.stringify(saveCalls[0].body && saveCalls[0].body.items) : 'no call');

// 采集失败路径：注入一个必抛错的采集器（模拟任何读取异常）
const origCollect = sandbox.collectReviewFormData;
sandbox.collectReviewFormData = function () {
    throw new TypeError("Cannot read properties of null (reading 'value')");
};
inject('isManualEntry = true; currentReceiptId = null; isSavingReview = false;');
fetchCalls.length = 0;
clearToasts();
sandboxWarns.length = 0;
let submitErr = null;
try { submitSaveEdited(); } catch (e) { submitErr = e; }
ok('D1 采集失败不再把异常抛给调用方（页面不会卡死）', submitErr === null, submitErr && String(submitErr));
ok('D2 采集失败时不发任何请求', fetchCalls.length === 0, JSON.stringify(fetchCalls.map(c => c.url)));
const msgs = toastMessages();
ok('D3 采集失败必须给人话提示（不再是「点了没反应」）', msgs.length >= 1, JSON.stringify(msgs));
ok('D4 提示文案面向用户（提到保存与明细，店员可执行）',
    msgs.length >= 1 && msgs[msgs.length - 1].indexOf('保存') !== -1
    && msgs[msgs.length - 1].indexOf('明细') !== -1,
    JSON.stringify(msgs));
const lastMsg = msgs.length ? msgs[msgs.length - 1] : '';
ok('D5 提示文案不含技术黑话（无 TypeError/null/undefined/value）',
    lastMsg.indexOf('TypeError') === -1 && lastMsg.indexOf('null') === -1
    && lastMsg.indexOf('undefined') === -1 && lastMsg.indexOf('value') === -1,
    JSON.stringify(lastMsg));
ok('D6 失败留痕用 [WARN] 措辞',
    sandboxWarns.some(w => w.indexOf('[WARN]') !== -1 && w.indexOf('保存') !== -1),
    JSON.stringify(sandboxWarns));
sandbox.collectReviewFormData = origCollect;

// 提示文案不得含 emoji（全仓纯文本要求）
const EMOJI = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;
ok('E1 用户提示文案无 emoji', !EMOJI.test(msgs.join(' ')), msgs.join(' '));

console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures }, null, 0));
process.exit(fail === 0 ? 0 : 1);
