// 待确认记忆「字段级 diff」验收脚手架（gap 台账 W-10）。
//
// 缺陷：人工闸审核界面（评测台 demo/static/js/evalset.js + templates/evalset.html）
// 原先只摊一整段候选内容，审核者看不到 fact_kind / fact_key / fact_value 的逐字段
// 差异，只能整段接受/拒绝。
//
// 本脚手架在 node vm 沙箱里载入**真实 evalset.js**，用注入的 fetch 调度器（零真实
// 网络）验证：
//   ① pendingFactDiffHtml()：结构化候选逐字段渲染 kind / key / 既有值 / 候选值；
//      无既有值时显示「新增」而不是空串；遗留纯文本候选（无任何结构化字段）退回中性
//      说明且**不**渲染 fact-diff 块（向后兼容旧候选）；
//   ② loadPendingMemories()：待审表格真的多出一列「字段级 diff」，且 approve / reject
//      两个按钮与既有流程**不受影响**（按钮数、点击后打的端点都不变）；
//   ③ XSS：候选键值里的 HTML 必须被转义（审核界面是注入候选的展示面）；
//   ④ 接线：evalset.html 的待审表头含「字段级 diff」列、占位行 colspan 与之匹配、
//      diff 样式类在页面里有定义（防「渲染了类但全仓无样式」）；
//   ⑤ 红线：全程 console.error 为 0。
//
// 沙箱内零真实网络、只读，不碰任何 live 数据。
// 用法（cwd 任意；路径由 __dirname 解析）：node demo/tests/frontend_pending_memory_diff_harness.js [evalset.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const jsPath = process.argv[2] || path.join(demoDir, 'static', 'js', 'evalset.js');
const src = fs.readFileSync(jsPath, 'utf8');
// 浏览器加载链（evalset.html）：ui-dialog.js 先于 evalset.js 加载。F1 后 evalset.js 的
// ask() 走 window.uiConfirm（应用内对话框），沙箱必须按同一契约载入真实 ui-dialog.js，
// 否则点击批准/拒绝时抛「window.uiConfirm is not a function」。
const uiDialogPath = process.argv[3] || path.join(demoDir, 'static', 'js', 'ui-dialog.js');
const uiDialogSrc = fs.readFileSync(uiDialogPath, 'utf8');
const htmlPath = path.join(demoDir, 'templates', 'evalset.html');
const evalHtml = fs.readFileSync(htmlPath, 'utf8');

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; failures.push(name + ' :: ' + detail); console.log('FAIL ' + name + ' -- ' + detail); }
}

function escHtml(s) {
    return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// ---- 最小 DOM 面（真实 esc() 依赖 createElement('div').textContent -> innerHTML） ----
function mkEl(props) {
    const el = Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {}, title: '', onclick: null,
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
        removeChild(c) {
            const i = this.children.indexOf(c);
            if (i !== -1) this.children.splice(i, 1);
            if (c) c.parentNode = null;
            return c;
        },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; },
        querySelectorAll(sel) {
            // 只需支撑 loadPendingMemories 的 tr.querySelectorAll('button')：按 innerHTML 里
            // 的 <button 计数造等量替身（够用来断言按钮存在与接线）。每个元素缓存同一批替身，
            // 好让生产代码写上的 onclick 在本脚本里能持久读到。
            if (sel === 'button') {
                if (!this._btns) {
                    const n = (String(this.innerHTML).match(/<button\b/g) || []).length;
                    this._btns = Array.from({ length: n }, () => mkEl({ tagName: 'BUTTON' }));
                }
                return this._btns;
            }
            return [];
        },
    }, props || {});
    let _tc = el.textContent || '';
    Object.defineProperty(el, 'textContent', {
        configurable: true,
        get() { return _tc; },
        set(v) { _tc = (v === null || v === undefined) ? '' : String(v); el.innerHTML = escHtml(_tc); },
    });
    return el;
}

// 自动按 id 造元素：evalset.js 启动时会取 roleNote 并调 reloadAll()（牵动多元素）。
const byId = {};
function getEl(id) { return byId[id] || (byId[id] = mkEl({ id: id })); }

// ---- fetch 调度器：按 URL 分流，绝不发真实请求 ----
let pendingRows = [];
const calls = [];
function mkRes(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        clone: () => mkRes(status, body),
    };
}
function dispatcher(url, options) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/api/memory/pending') === 0) {
        return Promise.resolve(mkRes(200, { status: 'success', data: pendingRows }));
    }
    if (u.indexOf('/api/evalset/stats') === 0) {
        return Promise.resolve(mkRes(200, { status: 'success', data: { splits: {} } }));
    }
    if (u.indexOf('/api/evalset/samples') === 0) {
        return Promise.resolve(mkRes(200, { data: { samples: [] } }));
    }
    if (u.indexOf('/api/evalset/candidates') === 0) {
        return Promise.resolve(mkRes(200, { data: { candidates: [] } }));
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
    confirm: () => true,   // approve/reject 前的 window.confirm 一律放行
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
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/evalset', reload() {}, assign() {} };
sandbox.document = {
    addEventListener() {},
    removeEventListener() {},
    getElementById: getEl,
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement(tag) { return mkEl({ tagName: String(tag || 'div').toUpperCase() }); },
    documentElement: { style: { setProperty() {} } },
    head: mkEl({ tagName: 'HEAD' }),
    body: mkEl({ tagName: 'BODY' }), readyState: 'loading',
};

try { vm.runInNewContext(uiDialogSrc, sandbox, { filename: 'ui-dialog.js' }); }
catch (e) { console.log('ui-dialog load note:', e.message); }
try { vm.runInNewContext(src, sandbox, { filename: 'evalset.js' }); }
catch (e) { console.log('load note:', e.message); }

const { pendingFactDiffHtml, loadPendingMemories } = sandbox;

const STRUCT = {
    id: 12, vendor: '祥興食品', content: 'alias：黑牛 → 黑牛牌黑胡椒牛油（UPDATE 既有值：本地牛腩）',
    source_receipt_ids_json: '[1494]', status: 'pending', fact_kind: 'alias',
    fact_key: 'alias:黑牛', fact_value: '黑牛牌黑胡椒牛油', existing_fact_value: '本地牛腩',
};
const NEW_FACT = {
    id: 13, vendor: '祥興食品', content: 'unit：斤 → 公斤', source_receipt_ids_json: '[]',
    status: 'pending', fact_kind: 'unit', fact_key: 'unit:斤', fact_value: '公斤',
    existing_fact_value: null,
};
const LEGACY = {
    id: 14, vendor: '祥興食品', content: '舊版自由文本候選（連續糾偏蒸餾）',
    source_receipt_ids_json: '[]', status: 'pending', fact_kind: null, fact_key: null,
    fact_value: null, existing_fact_value: null,
};
const XSS = {
    id: 15, vendor: '祥興食品', content: 'x', source_receipt_ids_json: '[]', status: 'pending',
    fact_kind: 'alias', fact_key: 'alias:<b>k</b>', fact_value: '<img src=x onerror=alert(1)>',
    existing_fact_value: null,
};

async function main() {
    ok('前置: 生产函数已定义', typeof pendingFactDiffHtml === 'function'
        && typeof loadPendingMemories === 'function',
        'diff=' + typeof pendingFactDiffHtml + ' load=' + typeof loadPendingMemories);

    // ---- 1. 纯函数：结构化 / 无既有值 / 遗留 ----
    const sHtml = pendingFactDiffHtml(STRUCT);
    ok('结构化候选 → 渲染 fact-diff 块',
        sHtml.indexOf('fact-diff') !== -1, 'html=' + sHtml);
    ok('结构化候选 → kind 逐字段可见（fd-kind）',
        sHtml.indexOf('fd-kind') !== -1 && sHtml.indexOf('alias') !== -1, 'html=' + sHtml);
    ok('结构化候选 → key 逐字段可见（fd-key）',
        sHtml.indexOf('fd-key') !== -1 && sHtml.indexOf('alias:黑牛') !== -1, 'html=' + sHtml);
    ok('结构化候选 → 既有值（旧值）可见且标旧（fd-old）',
        sHtml.indexOf('本地牛腩') !== -1 && sHtml.indexOf('fd-old') !== -1, 'html=' + sHtml);
    ok('结构化候选 → 候选值（新值）可见且标新（fd-new）',
        sHtml.indexOf('黑牛牌黑胡椒牛油') !== -1 && sHtml.indexOf('fd-new') !== -1, 'html=' + sHtml);

    const nHtml = pendingFactDiffHtml(NEW_FACT);
    ok('无既有值 → 明确「新增，无既有值」，不渲染成空串',
        nHtml.indexOf('（新增，无既有值）') !== -1 && nHtml.indexOf('fd-old') === -1,
        'html=' + nHtml);
    ok('无既有值 → 新值仍逐字段可见', nHtml.indexOf('公斤') !== -1, 'html=' + nHtml);

    const lHtml = pendingFactDiffHtml(LEGACY);
    ok('遗留纯文本候选 → 中性说明，不退化成空白',
        lHtml.indexOf('纯文本候选（无结构化字段）') !== -1, 'html=' + lHtml);
    ok('遗留纯文本候选 → 不渲染 fact-diff 块',
        lHtml.indexOf('fact-diff') === -1, 'html=' + lHtml);

    // ---- 2. 全链路：loadPendingMemories 真的多出一列且不破坏审批流 ----
    pendingRows = [STRUCT, NEW_FACT, LEGACY, XSS];
    byId['pendingMemoryBody'] = mkEl({ id: 'pendingMemoryBody' });
    await loadPendingMemories();
    const body = byId['pendingMemoryBody'];
    ok('列表渲染了 4 行候选', body.children.length === 4, 'rows=' + body.children.length);

    const htmlOf = i => String((body.children[i] || {}).innerHTML || '');
    ok('行1（结构化）→ 含字段级 diff 单元格',
        htmlOf(0).indexOf('fact-diff-td') !== -1 && htmlOf(0).indexOf('alias:黑牛') !== -1,
        'row=' + htmlOf(0));
    ok('行1 → 同格可见既有值 本地牛腩', htmlOf(0).indexOf('本地牛腩') !== -1, 'row=' + htmlOf(0));
    ok('行2（无既有值）→ 含「新增，无既有值」',
        htmlOf(1).indexOf('（新增，无既有值）') !== -1, 'row=' + htmlOf(1));
    ok('行3（遗留纯文本）→ 不渲染 fact-diff 块，仍显示纯文本候选说明',
        htmlOf(2).indexOf('class="fact-diff"') === -1
        && htmlOf(2).indexOf('纯文本候选（无结构化字段）') !== -1, 'row=' + htmlOf(2));

    // 审批流不破坏：每行仍是两个按钮（批准 / 拒绝）
    const btnsPerRow = body.children.map(tr => tr.querySelectorAll('button').length);
    ok('approve/reject 流程不破坏：每行仍是 2 个操作按钮',
        btnsPerRow.every(n => n === 2), 'counts=' + JSON.stringify(btnsPerRow));

    // ---- 3. XSS：候选键值里的 HTML 必须被转义 ----
    ok('XSS：候选值里的 <img onerror> 被转义（不出现原始标签）',
        htmlOf(3).indexOf('&lt;img src=x onerror=alert(1)&gt;') !== -1
        && htmlOf(3).indexOf('<img src=x') === -1, 'row=' + htmlOf(3));
    ok('XSS：候选 key 里的 <b> 被转义',
        htmlOf(3).indexOf('&lt;b&gt;k&lt;/b&gt;') !== -1 && htmlOf(3).indexOf('<b>k</b>') === -1,
        'row=' + htmlOf(3));

    // ---- 4. 点击真接线到既有端点 ----
    // F1 后批准/拒绝会弹真实 ui-dialog（window.uiConfirm，返回 Promise）。沙箱里没有
    // 真人点击，这里驱动生产对话框本身：找到最新挂载的 mask，点它的确认按钮（okBtn
    // 是 actions 里最后一个 button），让生产链路 ask → uiConfirm → doReviewMemory 走完。
    async function clickRowButtonAndConfirm(btn) {
        btn.onclick();
        const mask = [...sandbox.document.body.children].reverse()
            .find(c => c.className === 'ui-dlg-mask');
        if (!mask) throw new Error('ui-dialog 未挂载 mask（uiConfirm 未被调用）');
        const dlgBtns = [];
        (function walk(n) {
            (n.children || []).forEach(walk);
            if (n.tagName === 'BUTTON' && n.listeners && n.listeners.click) dlgBtns.push(n);
        })(mask);
        if (!dlgBtns.length) throw new Error('对话框内无可点的确认按钮');
        const evt = { key: 'Enter', preventDefault() {}, stopPropagation() {} };
        dlgBtns[dlgBtns.length - 1].listeners.click.forEach(fn => fn(evt));
        await new Promise(r => setTimeout(r, 10));
    }

    calls.length = 0;
    const row0Btns = body.children[0].querySelectorAll('button');
    await clickRowButtonAndConfirm(row0Btns[0]);
    ok('点「批准并生效」→ POST /api/memory/pending/12/approve',
        calls.some(u => u === '/api/memory/pending/12/approve'),
        'calls=' + JSON.stringify(calls));

    calls.length = 0;
    const row1Btns = body.children[1].querySelectorAll('button');
    await clickRowButtonAndConfirm(row1Btns[1]);
    ok('点「拒绝」→ POST /api/memory/pending/13/reject',
        calls.some(u => u === '/api/memory/pending/13/reject'),
        'calls=' + JSON.stringify(calls));

    // ---- 5. 接线：HTML 表头 / 占位列 / 样式 ----
    ok('接线: 待审表头含「字段级 diff」列', evalHtml.indexOf('字段级 diff') !== -1,
        '未找到表头');
    ok('接线: 待审占位行 colspan 已随列数调整为 7',
        /id="pendingMemoryBody"[\s\S]*?colspan="7"/.test(evalHtml), '未找到匹配的 colspan');
    // F3·B1 后 <style> 块外移到 external css：diff 样式类的「有消费方」判据改为
    // 守真不变量——页面声明的全部样式来源（内联 style 块 + 外链 stylesheet）联合
    // 起来必须定义这三个类；且样式来源本身不得为空（防扫描面失效后的假绿）。
    const cssSources = [];
    const inlineStyles = [...evalHtml.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
        .map(m => m[1]).join('\n');
    if (inlineStyles.trim()) cssSources.push({ from: 'inline <style>', css: inlineStyles });
    [...evalHtml.matchAll(/<link[^>]+rel="stylesheet"[^>]*>/g)].forEach(tag => {
        const href = (tag[0].match(/href="([^"]+)"/) || [])[1];
        if (!href) return;
        const file = path.join(demoDir, 'static', href.replace(/^\/static\//, ''));
        cssSources.push({ from: href, css: fs.readFileSync(file, 'utf8') });
    });
    const allCss = cssSources.map(s => s.from + '\n' + s.css).join('\n');
    ok('接线: 页面样式来源非空（外链 stylesheet 至少 1 个）', cssSources.length >= 1,
        'sources=' + JSON.stringify(cssSources.map(s => s.from)));
    ok('接线: 页面定义了 diff 样式类（.fact-diff / .fd-key / .fd-old）',
        ['.fact-diff', '.fd-key', '.fd-old'].every(sel => allCss.indexOf(sel) !== -1),
        '样式缺定义；已扫描样式来源=' + JSON.stringify(cssSources.map(s => s.from)));

    ok('红线: 全程 console.error 为 0', consoleErrors.length === 0,
        'errors=' + JSON.stringify(consoleErrors.slice(0, 3)));

    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    // 供 pytest 包装解析的机器可读摘要（本仓前端脚手架的通用约定）。
    console.log(JSON.stringify({ pass: pass, fail: fail, failures: failures }));
    process.exit(fail === 0 ? 0 : 1);
}

main().catch(e => {
    console.log('FAIL harness 自身异常: ' + e.stack);
    process.exit(1);
});
