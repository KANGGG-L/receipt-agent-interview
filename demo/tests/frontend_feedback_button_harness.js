// 单据反馈组件全场景显隐重构与首屏触达验收脚手架 (QA Phase 3)
// 依据 01-product-spec.md 与 02-dev-report.md 进行真实 main.js 闭环测试
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const mainPath = process.argv[2] || path.join(__dirname, '..', 'static', 'js', 'main.js');
const indexPath = path.join(__dirname, '..', 'templates', 'index.html');
const cssPath = path.join(__dirname, '..', 'static', 'css', 'style.css');

const src = fs.readFileSync(mainPath, 'utf8');
const indexHtml = fs.readFileSync(indexPath, 'utf8');
const cssContent = fs.readFileSync(cssPath, 'utf8');

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) {
        pass++;
        console.log('[PASS] ' + name);
    } else {
        fail++;
        failures.push(name + ': ' + (detail || 'assertion failed'));
        console.log('[FAIL] ' + name + ' -- ' + detail);
    }
}

// ---------------------------------------------------------------
// DOM 树节点构造
// ---------------------------------------------------------------
function mkClassList(initialClasses = []) {
    const s = new Set(initialClasses);
    return {
        _s: s,
        add(...c) { c.forEach(x => { if (x) s.add(x); }); },
        remove(...c) { c.forEach(x => { if (x) s.delete(x); }); },
        toggle(c, on) {
            if (!c) return false;
            if (on === undefined) {
                s.has(c) ? s.delete(c) : s.add(c);
            } else if (on) {
                s.add(c);
            } else {
                s.delete(c);
            }
            return s.has(c);
        },
        contains(c) { return s.has(c); },
        toString() { return Array.from(s).join(' '); }
    };
}

function mkEl(props) {
    const listeners = {};
    const el = Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        parentElement: null, parentNode: null, listeners: listeners,
        classList: mkClassList(),
        addEventListener(t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
        removeEventListener(t, fn) {
            if (listeners[t]) listeners[t] = listeners[t].filter(f => f !== fn);
        },
        appendChild(c) {
            if (c) {
                c.parentElement = this;
                c.parentNode = this;
                this.children.push(c);
            }
            return c;
        },
        removeChild(c) {
            const idx = this.children.indexOf(c);
            if (idx >= 0) this.children.splice(idx, 1);
            if (c) {
                c.parentElement = null;
                c.parentNode = null;
            }
            return c;
        },
        remove() {
            if (this.parentElement && this.parentElement.removeChild) {
                this.parentElement.removeChild(this);
            }
        },
        focus() {}, click() {}, scrollIntoView() {},
        setAttribute(k, v) { this[k] = v; },
        getAttribute(k) { return this[k] != null ? this[k] : null; },
        closest() { return null; },
        querySelector() { return null; },
        querySelectorAll() { return []; }
    }, props || {});

    if (props && props.className) {
        props.className.split(/\s+/).filter(Boolean).forEach(c => el.classList.add(c));
    }
    return el;
}

// ---------------------------------------------------------------
// 构建 DOM 集合 (从 index.html 自动解析所有 id)
// ---------------------------------------------------------------
const els = {};
for (const m of indexHtml.matchAll(/id="([^"]+)"/g)) {
    if (!els[m[1]]) els[m[1]] = mkEl({ id: m[1] });
}

// 核心卡片初始添加 hide
const cardIds = [
    'loadingCard', 'preConfirmCard', 'errorCard', 'prefillFormCard',
    'manualEntryNoImage', 'btnReparseRotated', 'btnRetryRotated'
];
cardIds.forEach(id => {
    if (els[id]) els[id].classList.add('hide');
});

// 构建反馈按钮组与按钮实例
function createFeedbackGroup(groupId) {
    const groupEl = els[groupId] || mkEl({ id: groupId, className: 'feedback-inline' });
    groupEl.style.display = 'none';

    const upBtn = mkEl({ tagName: 'BUTTON', className: 'feedback-btn feedback-btn-up' });
    const downBtn = mkEl({ tagName: 'BUTTON', className: 'feedback-btn feedback-btn-down' });

    groupEl.appendChild(upBtn);
    groupEl.appendChild(downBtn);
    els[groupId] = groupEl;
    return { groupEl, upBtn, downBtn };
}

const headerGroup = createFeedbackGroup('feedbackHeader');
const inlineGroup = createFeedbackGroup('feedbackInline');
const errorGroup = createFeedbackGroup('feedbackErrorCard');

// 侧边栏 Scan Tab 按钮
const scanTabBtn = mkEl({ tagName: 'BUTTON', className: 'sidebar-btn active', dataset: { target: 'tab-scan' } });

// 全局 querySelector 与 querySelectorAll 接线
function customQuerySelector(sel) {
    if (sel === '.sidebar-btn[data-target="tab-scan"]') return scanTabBtn;
    if (sel === '#feedbackHeader') return els['feedbackHeader'];
    if (sel === '#feedbackInline') return els['feedbackInline'];
    if (sel === '#feedbackErrorCard') return els['feedbackErrorCard'];
    return null;
}

function customQuerySelectorAll(sel) {
    if (sel === '.feedback-btn-up') {
        return [headerGroup.upBtn, inlineGroup.upBtn, errorGroup.upBtn];
    }
    if (sel === '.feedback-btn-down') {
        return [headerGroup.downBtn, inlineGroup.downBtn, errorGroup.downBtn];
    }
    return [];
}

// ---------------------------------------------------------------
// 沙箱环境与调用监控
// ---------------------------------------------------------------
const store = { demo_role: 'admin', demo_tenant_id: 'default' };
const consoleErrors = [];
const apiCalls = [];
const trackCalls = [];
const toastCalls = [];

function mockApiFetch(url, options = {}) {
    const urlStr = String(url);
    const method = (options.method || 'GET').toUpperCase();
    const bodyStr = options.body ? String(options.body) : null;
    let parsedBody = null;
    if (bodyStr) {
        try { parsedBody = JSON.parse(bodyStr); } catch (e) {}
    }

    if (urlStr.includes('/api/track') && parsedBody) {
        trackCalls.push({
            eventName: parsedBody.event_type,
            receiptId: parsedBody.receipt_id,
            properties: parsedBody.properties || {}
        });
        return Promise.resolve({
            ok: true, status: 200,
            json: () => Promise.resolve({ status: 'ok' }),
            clone: () => ({ json: () => Promise.resolve({ status: 'ok' }) })
        });
    }

    apiCalls.push({ url: urlStr, options, method, parsedBody });

    if (urlStr.includes('/feedback')) {
        return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({ status: 'success', msg: 'ok' }),
            clone: () => ({ json: () => Promise.resolve({ status: 'success' }) })
        });
    }
    if (urlStr.includes('/convert_manual')) {
        return Promise.resolve({
            ok: true,
            status: 200,
            json: () => Promise.resolve({
                status: 'success',
                receipt_id: 123,
                data: { supplier_name: '测试供应商', total_amount: 100, items: [] }
            }),
            clone: () => ({ json: () => Promise.resolve({ status: 'success' }) })
        });
    }
    return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve({ status: 'success' }),
        clone: () => ({ json: () => Promise.resolve({ status: 'success' }) })
    });
}

const sandbox = {
    console: {
        log() {}, info() {}, debug() {}, warn() {},
        error(...args) {
            consoleErrors.push(args.map(String).join(' '));
        }
    },
    setTimeout(fn) { return setTimeout(fn, 0); },
    clearTimeout(id) { clearTimeout(id); },
    Date: Date,
    Math: Math,
    Number: Number,
    String: String,
    Boolean: Boolean,
    Array: Array,
    Object: Object,
    JSON: JSON,
    RegExp: RegExp,
    URL: class URL {
        constructor(url, base) { this.href = (base || '') + url; }
        static revokeObjectURL() {}
    },
    localStorage: {
        getItem: k => (k in store ? store[k] : null),
        setItem: (k, v) => { store[k] = String(v); },
        removeItem: k => { delete store[k]; },
    },
    document: {
        getElementById(id) { return els[id] || null; },
        querySelector: customQuerySelector,
        querySelectorAll: customQuerySelectorAll,
        addEventListener() {},
        removeEventListener() {},
        createElement() { return mkEl(); },
        body: mkEl({ tagName: 'BODY' }),
        documentElement: { style: { setProperty() {} } }
    },
    location: { href: 'http://127.0.0.1:8000/', reload() {}, assign() {} },
    fetch: mockApiFetch
};

sandbox.window = sandbox;
sandbox.globalThis = sandbox;
sandbox.self = sandbox;

const context = vm.createContext(sandbox);

// 载入 main.js
try {
    vm.runInContext(src, context, { filename: 'main.js' });
} catch (e) {
    console.error('vm.runInContext load error:', e.message);
}

// 辅助执行函数
function evalInCtx(code) {
    return vm.runInContext(code, context);
}

// 拦截 showToast 记录调用
evalInCtx(`
    const _origShowToast = window.showToast || showToast;
    window.showToast = function(msg, level, duration) {
        toastCalls.push({ msg: String(msg), level: String(level || 'info') });
        if (typeof _origShowToast === 'function') {
            try { return _origShowToast(msg, level, duration); } catch(e){}
        }
    };
`);
context.toastCalls = toastCalls;

// ---------------------------------------------------------------
// 执行验收标准用例测试 (AC-1 ~ AC-13)
// ---------------------------------------------------------------

async function runTests() {
    console.log('--- 开始执行反馈组件全场景自动化验收套件 ---');

    // 0. 前置检查：关键函数暴露
    const t0 = evalInCtx(`({
        shouldShowFeedback: typeof shouldShowFeedback === 'function',
        syncFeedbackButtonsUI: typeof syncFeedbackButtonsUI === 'function',
        syncFeedbackVisibility: typeof syncFeedbackVisibility === 'function',
        onFeedbackClick: typeof onFeedbackClick === 'function'
    })`);

    ok('TC-00 前置函数导出',
        t0.shouldShowFeedback && t0.syncFeedbackButtonsUI &&
        t0.syncFeedbackVisibility && t0.onFeedbackClick,
        '核心函数未正确挂载到 window/全局');

    // 1. AC-6: 排他性隐藏场景（loadingCard 可见）
    els['loadingCard'].classList.remove('hide');
    els['preConfirmCard'].classList.add('hide');
    els['prefillFormCard'].classList.add('hide');
    els['errorCard'].classList.add('hide');
    evalInCtx('syncFeedbackVisibility();');
    ok('TC-01 [AC-6] 解析中(loadingCard)排他隐藏',
        evalInCtx('shouldShowFeedback()') === false &&
        els['feedbackHeader'].style.display === 'none' &&
        els['feedbackInline'].style.display === 'none' &&
        els['feedbackErrorCard'].style.display === 'none',
        'loadingCard 活跃时反馈按钮未严格隐藏');

    // 2. AC-6: 排他性隐藏场景（preConfirmCard 可见）
    els['loadingCard'].classList.add('hide');
    els['preConfirmCard'].classList.remove('hide');
    els['prefillFormCard'].classList.add('hide');
    els['errorCard'].classList.add('hide');
    evalInCtx('syncFeedbackVisibility();');
    ok('TC-02 [AC-6] 上传前预览(preConfirmCard)排他隐藏',
        evalInCtx('shouldShowFeedback()') === false &&
        els['feedbackHeader'].style.display === 'none' &&
        els['feedbackInline'].style.display === 'none' &&
        els['feedbackErrorCard'].style.display === 'none',
        'preConfirmCard 活跃时反馈按钮未严格隐藏');

    // 3. AC-1: 正常解析完成（明细项 >= 1）
    els['loadingCard'].classList.add('hide');
    els['preConfirmCard'].classList.add('hide');
    els['errorCard'].classList.add('hide');
    els['prefillFormCard'].classList.remove('hide');
    evalInCtx(`
        currentReceiptId = 1001;
        currentReceiptData = { items: [{ name: '商品A', unit_price: 10, quantity: 1 }] };
        isManualEntry = false;
        syncFeedbackVisibility();
    `);
    ok('TC-03 [AC-1] 正常解析完成(明细>=1)头部与底部可见',
        evalInCtx('shouldShowFeedback()') === true &&
        els['feedbackHeader'].style.display === 'inline-flex' &&
        els['feedbackInline'].style.display === 'inline-flex' &&
        els['feedbackErrorCard'].style.display === 'none',
        '正常解析完成时头部或底部反馈按钮未展示');

    // 4. AC-1: 正常解析完成但明细为空（0 明细）
    evalInCtx(`
        currentReceiptData = { items: [] };
        syncFeedbackVisibility();
    `);
    ok('TC-04 [AC-1] 0 明细解析完成时反馈按钮仍然穿透可见',
        evalInCtx('shouldShowFeedback()') === true &&
        els['feedbackHeader'].style.display === 'inline-flex' &&
        els['feedbackInline'].style.display === 'inline-flex' &&
        els['feedbackErrorCard'].style.display === 'none',
        '0 明细被错误隐藏，未打破原有限制');

    // 5. AC-3: 识别错误/画质拦截/报错（errorCard 态）
    els['prefillFormCard'].classList.add('hide');
    els['errorCard'].classList.remove('hide');
    evalInCtx(`
        lastErrorReceiptId = 2002;
        syncFeedbackVisibility();
    `);
    ok('TC-05 [AC-3] 识别错误卡片(errorCard)专属反馈入口可见',
        evalInCtx('shouldShowFeedback()') === true &&
        els['feedbackErrorCard'].style.display === 'inline-flex' &&
        els['feedbackHeader'].style.display === 'none' &&
        els['feedbackInline'].style.display === 'none',
        '错误卡片未展示 feedbackErrorCard 或误展示了 prefill 按钮');

    // 6. AC-2: 中途取消转手工（abortLoadingAndSwitchToManual）
    // 模拟正在 loadingCard，用户点击取消等待
    els['loadingCard'].classList.remove('hide');
    els['errorCard'].classList.add('hide');
    els['prefillFormCard'].classList.add('hide');
    evalInCtx('abortLoadingAndSwitchToManual();');
    ok('TC-06 [AC-2] 取消等待转手工后反馈按钮立即展示',
        !els['prefillFormCard'].classList.contains('hide') &&
        els['loadingCard'].classList.contains('hide') &&
        evalInCtx('shouldShowFeedback()') === true &&
        els['feedbackHeader'].style.display === 'inline-flex' &&
        els['feedbackInline'].style.display === 'inline-flex',
        'abortLoadingAndSwitchToManual 后未展示反馈按钮');

    // 7. AC-4: 错误态转手工（触发转手工流程）
    evalInCtx('applyRecognizedResult({ status: "success", receipt_id: 3003, data: { items: [] } }, 3003);');
    ok('TC-07 [AC-4] 错误卡转手工后反馈按钮正常展示',
        !els['prefillFormCard'].classList.contains('hide') &&
        els['errorCard'].classList.contains('hide') &&
        evalInCtx('shouldShowFeedback()') === true &&
        els['feedbackHeader'].style.display === 'inline-flex' &&
        els['feedbackInline'].style.display === 'inline-flex',
        '转手工后反馈按钮未正常可见');

    // 8. AC-5: 纯新建手工单（startManualEntry）
    evalInCtx('startManualEntry();');
    const t8 = evalInCtx('({ isManual: isManualEntry, rid: currentReceiptId, show: shouldShowFeedback() })');
    ok('TC-08 [AC-5] 纯新建手工单反馈按钮正常展示',
        t8.isManual === true &&
        t8.rid === null &&
        !els['prefillFormCard'].classList.contains('hide') &&
        t8.show === true &&
        els['feedbackHeader'].style.display === 'inline-flex' &&
        els['feedbackInline'].style.display === 'inline-flex',
        'startManualEntry 后反馈按钮未正常可见: ' + JSON.stringify(t8));

    // 9. 解耦验证：挽回动作栏与反馈按钮解耦
    ok('TC-09 挽回动作栏与反馈显隐解耦',
        evalInCtx('hasRecognizedResult()') === false &&
        evalInCtx('shouldShowFeedback()') === true,
        '手工单应隐藏挽回动作(无原图)，但必须展示反馈组件');

    // 10. AC-8: 按钮高亮双向联动测试
    evalInCtx('syncFeedbackButtonsUI(1);');
    const allUpActive = [headerGroup.upBtn, inlineGroup.upBtn, errorGroup.upBtn]
        .every(b => b.classList.contains('active'));
    const allDownInactive = [headerGroup.downBtn, inlineGroup.downBtn, errorGroup.downBtn]
        .every(b => !b.classList.contains('active'));
    ok('TC-10 [AC-8] 点赞 UI 广播联动高亮',
        allUpActive && allDownInactive,
        '点赞未同步高亮所有反馈组件的点赞按钮');

    evalInCtx('syncFeedbackButtonsUI(-1);');
    const allDownActive = [headerGroup.downBtn, inlineGroup.downBtn, errorGroup.downBtn]
        .every(b => b.classList.contains('active'));
    const allUpInactive = [headerGroup.upBtn, inlineGroup.upBtn, errorGroup.upBtn]
        .every(b => !b.classList.contains('active'));
    ok('TC-11 [AC-8] 点踩 UI 广播联动高亮',
        allDownActive && allUpInactive,
        '点踩未同步高亮所有反馈组件的点踩按钮');

    evalInCtx('syncFeedbackButtonsUI(null);');
    const allReset = [headerGroup.upBtn, inlineGroup.upBtn, errorGroup.upBtn,
                      headerGroup.downBtn, inlineGroup.downBtn, errorGroup.downBtn]
        .every(b => !b.classList.contains('active'));
    ok('TC-12 [AC-8] 反馈状态复位清空',
        allReset,
        '复位未清空所有按钮的 active 类');

    // 11. AC-9: 有效单据数据提交与埋点
    apiCalls.length = 0;
    trackCalls.length = 0;
    toastCalls.length = 0;
    els['prefillFormCard'].classList.remove('hide');
    els['errorCard'].classList.add('hide');

    evalInCtx(`
        currentReceiptId = 555;
        currentReceiptData = { items: [{ name: '商品X' }] };
        isManualEntry = false;
        onFeedbackClick(1, 'prefill_header');
    `);
    await new Promise(r => setTimeout(r, 20));

    const fbApiCall = apiCalls.find(c => c.url.includes('/api/receipt/555/feedback'));
    const fbTrackCall = trackCalls.find(c => c.eventName === 'receipt_feedback_submitted');
    const fbToast = toastCalls.find(t => t.msg.includes('[OK]'));

    ok('TC-13 [AC-9] 有效单据提交网络调用及请求体契约',
        !!fbApiCall && fbApiCall.method === 'POST' &&
        fbApiCall.parsedBody && fbApiCall.parsedBody.like === 1 &&
        fbApiCall.parsedBody.tenant_id === 'default',
        'POST /api/receipt/555/feedback 请求未正确发出或参数错误: ' + JSON.stringify(fbApiCall));

    ok('TC-14 [AC-9] 有效单据提交埋点上报',
        !!fbTrackCall && fbTrackCall.receiptId === 555 &&
        fbTrackCall.properties.like === 1 &&
        fbTrackCall.properties.source === 'prefill_header',
        'receipt_feedback_submitted 埋点未触发: ' + JSON.stringify(trackCalls));

    ok('TC-15 [AC-9] 有效单据提交纯文本 Toast 提示',
        !!fbToast && !/[\uD800-\uDFFF]/.test(fbToast.msg),
        '未弹出包含 [OK] 的纯文本 Toast 提示: ' + JSON.stringify(toastCalls));

    // 12. AC-10: 无单据安全降级（纯手工单或识别未入库）
    apiCalls.length = 0;
    trackCalls.length = 0;
    toastCalls.length = 0;
    consoleErrors.length = 0;

    evalInCtx(`
        currentReceiptId = null;
        isManualEntry = true;
        onFeedbackClick(-1, 'prefill_footer');
    `);
    await new Promise(r => setTimeout(r, 20));

    const invalidApiCall = apiCalls.find(c => c.url.includes('/api/receipt/null') || c.url.includes('/api/receipt/0'));
    const degradeTrack = trackCalls.find(c => c.eventName === 'feedback_received');
    const degradeToast = toastCalls.find(t => t.msg.includes('[INFO]'));

    ok('TC-16 [AC-10] 无单据安全降级阻断非法 HTTP 请求',
        !invalidApiCall && apiCalls.length === 0,
        '无单据时发起了非法网络请求: ' + JSON.stringify(apiCalls));

    ok('TC-17 [AC-10] 无单据安全降级触发全局体验埋点',
        !!degradeTrack && degradeTrack.receiptId === null &&
        degradeTrack.properties.like === -1 &&
        degradeTrack.properties.scene === 'manual_entry' &&
        degradeTrack.properties.source === 'prefill_footer',
        'feedback_received 全局埋点未正确触发: ' + JSON.stringify(trackCalls));

    ok('TC-18 [AC-10] 降级过程零 JS 异常',
        consoleErrors.length === 0,
        '降级过程产生了控制台异常: ' + consoleErrors.join('; '));

    ok('TC-19 [AC-10] 降级提示纯文本规范',
        !!degradeToast && !/[\uD800-\uDFFF]/.test(degradeToast.msg),
        '降级提示文案异常: ' + JSON.stringify(toastCalls));

    // 13. AC-11: 挽回埋点 prior_feedback 贯通
    trackCalls.length = 0;
    evalInCtx(`
        currentReceiptId = 888;
        isManualEntry = false;
        lastFeedbackForCurrent = -1;
        retryReceiptRecognition = function() {};
        onReparseClick();
    `);
    const reparseTrack = trackCalls.find(c => c.eventName === 'reparse_clicked');
    ok('TC-20 [AC-11] 挽回埋点贯通 prior_feedback',
        !!reparseTrack && reparseTrack.receiptId === 888 &&
        reparseTrack.properties.prior_feedback === -1,
        'reparse_clicked 埋点未携带当前表态 prior_feedback: -1: ' + JSON.stringify(trackCalls));

    // 14. AC-7: 模板结构静态断言（首屏直达）
    const headerPos = indexHtml.indexOf('id="feedbackHeader"');
    const itemTablePos = indexHtml.indexOf('id="itemTableBody"');
    const cardTitlePos = indexHtml.indexOf('id="manualEntryFormTitle"');

    ok('TC-21 [AC-7] 首屏反馈组件挂载在卡片标题区',
        headerPos !== -1 && cardTitlePos !== -1 && Math.abs(headerPos - cardTitlePos) < 600,
        'feedbackHeader 未放置在卡片标题区域');

    ok('TC-22 [AC-7] 首屏反馈组件严格位于明细长表格之上',
        headerPos < itemTablePos,
        'feedbackHeader 位于明细表格下方，无法保证首屏直达');

    // 15. AC-12: 无 Emoji 静态审查
    const emojiRegex = /[\uD83C-\uDBFF\uDC00-\uDFFF\u2600-\u26FF\u2700-\u27BF]/;
    const headerHtml = indexHtml.substring(headerPos, headerPos + 800);
    const errorCardPos = indexHtml.indexOf('id="feedbackErrorCard"');
    const errorHtml = indexHtml.substring(errorCardPos, errorCardPos + 800);

    ok('TC-23 [AC-12] feedbackHeader HTML 模板无 Emoji',
        !emojiRegex.test(headerHtml),
        'feedbackHeader 模板包含 Emoji');

    ok('TC-24 [AC-12] feedbackErrorCard HTML 模板无 Emoji',
        !emojiRegex.test(errorHtml),
        'feedbackErrorCard 模板包含 Emoji');

    // 16. CSS 样式支持验证
    const hasBtnUpActive = cssContent.includes('.feedback-btn-up.active');
    const hasBtnDownActive = cssContent.includes('.feedback-btn-down.active');
    ok('TC-25 CSS 激活态高亮选择器完整支持',
        hasBtnUpActive && hasBtnDownActive,
        'CSS 中缺少 .feedback-btn-up.active 或 .feedback-btn-down.active 样式');

    // 17. 非 scan Tab 隔离隐藏
    scanTabBtn.classList.remove('active');
    evalInCtx('syncFeedbackVisibility();');
    ok('TC-26 非收据识别主 Tab 下强制隐藏',
        evalInCtx('shouldShowFeedback()') === false &&
        els['feedbackHeader'].style.display === 'none' &&
        els['feedbackInline'].style.display === 'none' &&
        els['feedbackErrorCard'].style.display === 'none',
        '离开 Tab Scan 时反馈组件未隐藏');

    console.log(`\n测试汇总: 通过=${pass}, 失败=${fail}`);
    const summary = { pass, fail, failures };
    console.log(JSON.stringify(summary));
    process.exit(fail === 0 ? 0 : 1);
}

runTests();
