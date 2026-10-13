// -*- coding: utf-8 -*-
// 记忆度量看板验收脚手架（收敛计划 P2-T5 / 概念 C11）。
//
// 在 node vm 沙箱里载入**真实 main.js**，用注入的 fetch 调度器（零真实网络）验证：
//   ① memoryMetricRateHtml()/memoryMetricsRowsHtml()：分母为 0 → 显示「无数据」而不是
//      0%（0% 表示"有数据且结果全是坏"，与"取不到数"语义相反）；分母>0 → 比率取自后端
//      注入值、并把分子/分母一并显示（前端不自算任何比率）；
//   ①b 有效命中率（memory_hit_effective，P2-T6 验收口径）必须**真的渲染出来**，与旧口径
//      memory_hit 并列且带不可混读说明；分母为 0 → 「无数据（当前库中没有可用记忆）」而
//      不是 0%，分母>0 且注入=0 → 仍是 0.00%（真 0% 与无数据必须区分）；字段整体缺失时
//      只说「无数据」，不误断言「库里没有记忆」；**记忆总开关关闭时（disabled_by_switch=true）
//      必须说「记忆已关闭（未测量）」并换成关闭态的漏斗说明** —— 那是第三种状态，既不是
//      「真 0%」也不是「库里没有可用记忆」（Q3-M3）；
//   ② renderMemoryMetrics()：失败态（status=error / metrics=null）时一个比率都不许显示，
//      只显示错误文案；开关三态（true/false/null）各自显式；
//   ②b 样式消费方（本项目高发的「只写列·零消费方」在前端的形态）：从渲染产物里抽出所有
//      mm-* class，断言每个都带非空 style、9 个类全部真实出现、且源码里写下的类名都被渲染出来
//      —— 防止再出现「class 生成了但全仓无样式定义」（看板退化成无样式行文、三态无色差）；
//      并断言开关三态的 style 片段互不相同（状态必须有色差，不能只差一个字）；
//   ③ 接线：index.html 有 id="memoryMetricsPanel" 锚点、main.js 在 tab-engine 分支调用
//      loadMemoryMetrics()、请求打在只读端点 /api/admin/memory-metrics、非 admin 不发请求；
//   ④ 红线：全程 console.error 为 0。
//
// 用法（cwd 任意；路径一律由 __dirname 解析，避免本仓「写死 demo/... 只在仓库根通过」的坑）：
//   node demo/tests/test_memory_metrics_panel.js [main.js 路径]
// 退出码 0 = 全部通过；非 0 = 有断言失败。
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const mainPath = process.argv[2] || path.join(demoDir, 'static', 'js', 'main.js');
const indexPath = path.join(demoDir, 'templates', 'index.html');
const src = fs.readFileSync(mainPath, 'utf8');
const indexHtml = fs.readFileSync(indexPath, 'utf8');
// F4 S4-11 起 main.js 按域拆分：源码级扫描断言作用于「main.js + 全部域文件」联合全文
// （与浏览器实际加载的代码集合同构），不再钉「必须在 main.js」。
const F4_LOADER = require('./f4_load_domains.js');
const srcAll = [src].concat(
    F4_LOADER.F4_DOMAIN_FILES.map(name => fs.readFileSync(path.join(__dirname, '..', 'static', 'js', name), 'utf8'))
).join('\n');


let pass = 0, fail = 0;
function ok(name, cond, detail) {
    if (cond) { pass++; console.log('PASS ' + name); }
    else { fail++; console.log('FAIL ' + name + ' -- ' + detail); }
}

// ---- 最小 DOM 面（只支撑看板这条链路） ----
function mkEl(props) {
    return Object.assign({
        id: '', className: '', textContent: '', innerText: '', innerHTML: '', value: '',
        tagName: 'DIV', style: {}, dataset: {}, children: [], disabled: false,
        listeners: {},
        classList: {
            _s: new Set(),
            add(...c) { c.forEach(x => this._s.add(x)); },
            remove(...c) { c.forEach(x => this._s.delete(x)); },
            contains(c) { return this._s.has(c); },
        },
        addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
        appendChild(c) { this.children.push(c); return c; },
        remove() {}, focus() {}, setAttribute(k, v) { this[k] = v; },
        getAttribute() { return null; }, closest() { return null; },
        querySelector() { return null; }, querySelectorAll() { return []; },
    }, props || {});
}

const panelEl = mkEl({ id: 'memoryMetricsPanel', innerHTML: '度量加载中...' });

const consoleErrors = [];
const sandbox = {
    console: {
        log() {}, info() {}, debug() {}, warn() {},
        error(...a) { consoleErrors.push(a.map(String).join(' ')); },
    },
    setTimeout, clearTimeout,
    localStorage: {
        _m: { demo_role: 'admin' },
        getItem(k) { return (k in this._m) ? this._m[k] : null; },
        setItem(k, v) { this._m[k] = String(v); },
        removeItem(k) { delete this._m[k]; },
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
sandbox.addEventListener = function () {};
sandbox.removeEventListener = function () {};
sandbox.globalThis = sandbox;
sandbox.self = sandbox;
sandbox.location = { href: 'http://127.0.0.1/', reload() {}, assign() {} };
sandbox.open = () => null;
sandbox.document = {
    addEventListener() {},
    getElementById(id) { return id === 'memoryMetricsPanel' ? panelEl : null; },
    querySelector() { return null; },
    querySelectorAll() { return []; },
    createElement() { return mkEl(); },
    documentElement: { style: { setProperty() {} } },
    body: mkEl({ tagName: 'BODY' }), readyState: 'loading',
};

// ---- fetch 调度器：只接 /api/admin/memory-metrics，绝不发真实请求 ----
let metricsMode = 'ok';        // ok | network
let metricsResponse = null;
const calls = [];
function mkRes(status, body) {
    return {
        ok: status >= 200 && status < 300,
        status,
        json: () => Promise.resolve(body),
        clone: () => mkRes(status, body),
    };
}
function dispatcher(url) {
    const u = String(url);
    calls.push(u);
    if (u.indexOf('/api/admin/memory-metrics') === 0) {
        if (metricsMode === 'network') return Promise.reject(new Error('offline'));
        return Promise.resolve(mkRes(200, metricsResponse));
    }
    return Promise.resolve(mkRes(404, { detail: 'harness 未约定的 URL' }));
}

try { vm.runInNewContext(src, sandbox, { filename: 'main.js' }); }
catch (e) { console.log('load note:', e.message); }
// F4 S4-11：main.js 按域拆分，沙箱按 index.html 加载链追加全部域文件
// （demo/tests/f4_load_domains.js 共享加载器；overrides 可按文件名指定突变体路径做反证）。
require('./f4_load_domains.js').loadF4Domains(
    (dsrc, name) => vm.runInNewContext(dsrc, sandbox, { filename: name }),
    { warn: m => console.log(m) });

const { memoryMetricRateHtml, memoryMetricsRowsHtml, memoryMetricsSwitchHtml,
        renderMemoryMetrics, loadMemoryMetrics } = sandbox;

// 取某个 data-metric 行（到该行 </div> 为止）
function rowHtml(html, key) {
    const parts = String(html).split('data-metric="' + key + '"');
    if (parts.length < 2) return '';
    return parts[1].split('</div>')[0];
}

const GOOD_METRICS = {
    memory_hit: { total_receipts: 4, with_memory_context: 2, rate_pct: 50.0,
                  note: '分母=全部单据；有效命中率口径待 P2-T6 落地后启用' },
    memory_hit_effective: {
        receipts_total: 4, vendor_determinable: 3, excluded_no_usable_memory: 1,
        eligible_denominator: 2, injected: 1, effective_rate_pct: 50.0, no_data: false,
        incomparable_reason: '旧口径分母含无缘命中的单据，本字段分母只含有条件命中的单据，两者不可混读。',
    },
    sku_match: { items: 3, matched: 2, rate_pct: 66.67 },
    edit_rate_ab: {
        has_memory: { receipts: 2, edited: 1, edited_rate_pct: 50.0 },
        no_memory: { receipts: 2, edited: 1, edited_rate_pct: 50.0 },
        delta_pp: 0.0,
    },
    decision_log: { decisions: 5, not_adopted: 2, not_adopted_rate_pct: 40.0 },
};

async function main() {
    ok('前置: 生产函数已定义',
        typeof memoryMetricRateHtml === 'function'
        && typeof memoryMetricsRowsHtml === 'function'
        && typeof memoryMetricsSwitchHtml === 'function'
        && typeof renderMemoryMetrics === 'function'
        && typeof loadMemoryMetrics === 'function',
        'rate=' + typeof memoryMetricRateHtml + ' rows=' + typeof memoryMetricsRowsHtml
        + ' switch=' + typeof memoryMetricsSwitchHtml + ' render=' + typeof renderMemoryMetrics
        + ' load=' + typeof loadMemoryMetrics);

    // ---- 1. 分母为 0 → 无数据（不用 0% 冒充）----
    const zero = memoryMetricRateHtml(0.0, 0);
    ok('分母=0 → 显示「无数据」且不出现 0%',
        zero.indexOf('无数据') !== -1 && zero.indexOf('0.00%') === -1, 'html=' + zero);
    ok('分母缺失 / null / NaN → 一律「无数据」',
        memoryMetricRateHtml(50, undefined).indexOf('无数据') !== -1
        && memoryMetricRateHtml(50, null).indexOf('无数据') !== -1
        && memoryMetricRateHtml(50, 'x').indexOf('无数据') !== -1
        && memoryMetricRateHtml(50, -1).indexOf('无数据') !== -1, 'missing/null/Nan 未判无数据');

    // 分母>0 → 显示后端给的比率（前端不改口径）
    const some = memoryMetricRateHtml(66.67, 3);
    ok('分母>0 → 显示比率（两位小数，取值来自后端）',
        some.indexOf('66.67%') !== -1 && some.indexOf('无数据') === -1, 'html=' + some);
    ok('比率非数值 → 回落 0.00% 而不是 NaN%',
        memoryMetricRateHtml('x', 3).indexOf('NaN') === -1, 'html=' + memoryMetricRateHtml('x', 3));

    // ---- 2. 四组指标行：分子/分母同时展示，前端不自算 ----
    const rows = memoryMetricsRowsHtml(GOOD_METRICS);
    for (const key of ['memory_hit', 'memory_hit_effective', 'sku_match',
                       'edit_rate_has_memory', 'edit_rate_no_memory', 'not_adopted']) {
        ok('指标行存在: ' + key, rows.indexOf('data-metric="' + key + '"') !== -1, 'html=' + rows);
    }
    ok('命中率行: 2 / 4 + 50.00%',
        rowHtml(rows, 'memory_hit').indexOf('50.00%') !== -1
        && rowHtml(rows, 'memory_hit').indexOf('2 / 4') !== -1, 'row=' + rowHtml(rows, 'memory_hit'));
    ok('SKU 匹配率行: 2 / 3 + 66.67%',
        rowHtml(rows, 'sku_match').indexOf('66.67%') !== -1
        && rowHtml(rows, 'sku_match').indexOf('2 / 3') !== -1, 'row=' + rowHtml(rows, 'sku_match'));
    ok('未采纳率行: 2 / 5 + 40.00%',
        rowHtml(rows, 'not_adopted').indexOf('40.00%') !== -1
        && rowHtml(rows, 'not_adopted').indexOf('2 / 5') !== -1, 'row=' + rowHtml(rows, 'not_adopted'));
    ok('两组修改率之差（0.00pp）单独一行', rows.indexOf('data-metric="edit_rate_delta"') !== -1
        && rows.indexOf('0.00pp') !== -1, 'html=' + rows);
    ok('口径来源与分组键必须写在数字旁（代理口径不得被读成因果）',
        rows.indexOf('data-metric="memory_hit_note"') !== -1
        && rowHtml(rows, 'memory_hit_note').indexOf('分母=全部单据') !== -1
        && rows.indexOf('分组键=供应商是否有记忆行') !== -1
        && rowHtml(rows, 'edit_rate_delta').indexOf('memory_ab_baseline.py') !== -1,
        'html=' + rows);

    // 有记忆组无样本 → 该行无数据、delta 也不猜
    const halfAb = memoryMetricsRowsHtml({
        edit_rate_ab: { no_memory: { receipts: 3, edited: 1, edited_rate_pct: 33.33 } },
    });
    ok('有记忆组无样本 → 该行「无数据」且 delta 显示无数据（不猜）',
        rowHtml(halfAb, 'edit_rate_has_memory').indexOf('无数据') !== -1
        && rowHtml(halfAb, 'edit_rate_delta').indexOf('无数据') !== -1, 'html=' + halfAb);
    ok('空 metrics 不抛异常且全行「无数据」',
        memoryMetricsRowsHtml(null).indexOf('无数据') !== -1
        && memoryMetricsRowsHtml({}).indexOf('无数据') !== -1, 'empty metrics 未回落');

    // ---- 2b. 有效命中率（P2-T6 口径）：必须真的渲染出来，且与旧口径并列、不可混读 ----
    ok('有效命中率行: 1 / 2 + 50.00%（分子/分母取自后端，前端不自算）',
        rowHtml(rows, 'memory_hit_effective').indexOf('50.00%') !== -1
        && rowHtml(rows, 'memory_hit_effective').indexOf('1 / 2') !== -1,
        'row=' + rowHtml(rows, 'memory_hit_effective'));
    ok('两个命中率并列: 明确标注不可混读（取后端 incomparable_reason）',
        rows.indexOf('data-metric="memory_hit_effective_note"') !== -1
        && rowHtml(rows, 'memory_hit_effective_note').indexOf('不可混读') !== -1,
        'note=' + rowHtml(rows, 'memory_hit_effective_note'));

    // 分母为 0 → 「无数据（当前库中没有可用记忆）」，绝不用 0% 冒充
    const effNoData = memoryMetricsRowsHtml({
        memory_hit_effective: { receipts_total: 4, vendor_determinable: 2,
            eligible_denominator: 0, injected: 0, no_data: true, effective_rate_pct: null },
    });
    ok('有效命中率分母=0 → 显示「无数据（当前库中没有可用记忆）」且不出现 0%',
        rowHtml(effNoData, 'memory_hit_effective').indexOf('无数据（当前库中没有可用记忆）') !== -1
        && rowHtml(effNoData, 'memory_hit_effective').indexOf('0.00%') === -1,
        'row=' + rowHtml(effNoData, 'memory_hit_effective'));
    // 分母>0 且注入=0：这是真 0%（有数据但结果全是坏），必须与「无数据」区分开
    const effZero = memoryMetricsRowsHtml({
        memory_hit_effective: { eligible_denominator: 3, injected: 0, no_data: false,
            effective_rate_pct: 0.0, incomparable_reason: '分母不同，不可混读。' },
    });
    ok('有效命中率分母>0 且注入=0 → 显示 0.00%（不是「无数据」）',
        rowHtml(effZero, 'memory_hit_effective').indexOf('0.00%') !== -1
        && rowHtml(effZero, 'memory_hit_effective').indexOf('无数据') === -1,
        'row=' + rowHtml(effZero, 'memory_hit_effective'));
    // 字段整体缺失 ≠ 「库里没有记忆」：只说「无数据」，不替后端断言原因
    const effMissing = memoryMetricsRowsHtml({ memory_hit: { total_receipts: 1 } });
    ok('有效命中率字段缺失 → 只显示「无数据」，不误断言「当前库中没有可用记忆」',
        rowHtml(effMissing, 'memory_hit_effective').indexOf('无数据') !== -1
        && rowHtml(effMissing, 'memory_hit_effective').indexOf('当前库中没有可用记忆') === -1,
        'row=' + rowHtml(effMissing, 'memory_hit_effective'));

    // ---- 2c. 记忆总开关关闭（Q3-M3）：后端判「未测量」→ 前端必须讲关闭态 ----
    // 第三种状态：既不是「真 0%」（那是开了但没命中），也不是「当前库中没有可用记忆」
    // （那是另一个原因 —— 库里未必没有记忆，只是根本没测）。用错理由比不显示更糟。
    const effDisabled = memoryMetricsRowsHtml({
        memory_hit_effective: { receipts_total: 5, vendor_determinable: 4,
            excluded_no_usable_memory: 0, eligible_denominator: 0, injected: 0,
            no_data: true, disabled_by_switch: true, effective_rate_pct: null,
            incomparable_reason: '分母不同，不可混读。' },
    });
    ok('开关关闭 → 「记忆已关闭（未测量）」，不出现 0% 也不误断言「当前库中没有可用记忆」',
        rowHtml(effDisabled, 'memory_hit_effective').indexOf('记忆已关闭（未测量）') !== -1
        && rowHtml(effDisabled, 'memory_hit_effective').indexOf('0.00%') === -1
        && rowHtml(effDisabled, 'memory_hit_effective').indexOf('当前库中没有可用记忆') === -1,
        'row=' + rowHtml(effDisabled, 'memory_hit_effective'));
    ok('开关关闭 → 漏斗说明改成关闭态，不印「因无可用记忆被排除」（错误理由）',
        rowHtml(effDisabled, 'memory_hit_effective_note').indexOf('记忆总开关 memory_enabled 已关闭') !== -1
        && rowHtml(effDisabled, 'memory_hit_effective_note').indexOf('因无可用记忆被排除') === -1,
        'note=' + rowHtml(effDisabled, 'memory_hit_effective_note'));

    // ---- 3. 开关三态 ----
    ok('开关 true → 已开启',
        memoryMetricsSwitchHtml(true).indexOf('已开启') !== -1
        && memoryMetricsSwitchHtml(true).indexOf('data-switch="on"') !== -1,
        'html=' + memoryMetricsSwitchHtml(true));
    ok('开关 false → 已关闭（说明检索入口短路）',
        memoryMetricsSwitchHtml(false).indexOf('已关闭') !== -1
        && memoryMetricsSwitchHtml(false).indexOf('data-switch="off"') !== -1,
        'html=' + memoryMetricsSwitchHtml(false));
    ok('开关 null（读取失败）→ 状态未知，不混进「已关闭」',
        memoryMetricsSwitchHtml(null).indexOf('状态未知') !== -1
        && memoryMetricsSwitchHtml(null).indexOf('已关闭') === -1,
        'html=' + memoryMetricsSwitchHtml(null));

    // ---- 4. renderMemoryMetrics：成功态 / 失败态 ----
    renderMemoryMetrics({ status: 'success', memory_enabled: true, metrics: GOOD_METRICS });
    ok('成功态 → 面板内渲染开关 + 四组指标',
        panelEl.innerHTML.indexOf('已开启') !== -1
        && panelEl.innerHTML.indexOf('50.00%') !== -1
        && panelEl.innerHTML.indexOf('40.00%') !== -1, 'inner=' + panelEl.innerHTML);

    renderMemoryMetrics({ status: 'error', msg: '记忆度量取数失败: 模拟', memory_enabled: null, metrics: null });
    ok('失败态 → 显示错误原文 + 状态未知',
        panelEl.innerHTML.indexOf('度量读取失败') !== -1
        && panelEl.innerHTML.indexOf('模拟') !== -1
        && panelEl.innerHTML.indexOf('状态未知') !== -1, 'inner=' + panelEl.innerHTML);
    ok('失败态 → 一个比率都不显示（不得用 0% 冒充无数据）',
        panelEl.innerHTML.indexOf('%') === -1, 'inner=' + panelEl.innerHTML);

    // ---- 4b. 样式消费方：class 必须有 style（判别式断言）----
    // 抽签口径：只看**渲染产物**，不看源码里有没有写 class —— 源码写了而产物没样式，
    // 正是「定义了没接线」的形态，必须判失败。
    const MM_ALL = ['mm-no-data', 'mm-rate', 'mm-row', 'mm-label', 'mm-denom',
                    'mm-note', 'mm-delta', 'mm-switch', 'mm-error'];
    function mmTags(html) {
        const out = [];
        const re = /<[a-z][a-z0-9]*\b[^>]*\bclass="(mm-[a-z0-9-]+)"[^>]*>/gi;
        let m;
        while ((m = re.exec(String(html))) !== null) out.push({ cls: m[1], tag: m[0] });
        return out;
    }
    function mmStyleOf(tag) {
        const m = /\bstyle="([^"]*)"/.exec(tag);
        return m ? m[1] : '';
    }

    const artifacts = [];
    renderMemoryMetrics({ status: 'success', memory_enabled: true, metrics: GOOD_METRICS });
    artifacts.push(['成功有数据', panelEl.innerHTML]);
    renderMemoryMetrics({ status: 'success', memory_enabled: false, metrics: {} });   // 全部分母为 0
    artifacts.push(['全部分母为 0', panelEl.innerHTML]);
    renderMemoryMetrics({ status: 'error', msg: '模拟取数失败', memory_enabled: null, metrics: null });
    artifacts.push(['取数失败', panelEl.innerHTML]);

    const tags = artifacts.flatMap(([name, html]) => mmTags(html).map(t => Object.assign({ name }, t)));
    const renderedCls = new Set(tags.map(t => t.cls));
    const unstyled = tags.filter(t => !mmStyleOf(t.tag));
    ok('样式消费方: 渲染产物里每个 mm-* class 都带非空 style（无样式即失败）',
        tags.length > 0 && unstyled.length === 0,
        '无样式的标签=' + JSON.stringify(unstyled.map(t => t.cls + ' @' + t.name)));
    ok('样式消费方: 9 个 mm-* 类全部真实出现在渲染产物里（不留从未渲染的类）',
        MM_ALL.every(c => renderedCls.has(c)),
        '缺失=' + JSON.stringify(MM_ALL.filter(c => !renderedCls.has(c))));

    // 源码侧反查：看板区块里写下的 mm-* 类名必须都被渲染出来（定义了必有消费方）
    const panelSrc = srcAll.split('记忆度量看板（收敛计划 P2-T5')[1].split('function escapeHtml')[0];
    const srcCls = new Set((panelSrc.match(/mm-[a-z0-9-]+/g) || []));
    ok('样式消费方: 源码看板区块里的 mm-* 类名全部被渲染出来（防「定义了没接线」）',
        srcCls.size > 0 && [...srcCls].every(c => renderedCls.has(c)),
        '源码有而产物无=' + JSON.stringify([...srcCls].filter(c => !renderedCls.has(c))));

    // 开关三态：style 片段必须互不相同（状态有色差），且 data-switch 取值三态各一
    const swOn = memoryMetricsSwitchHtml(true);
    const swOff = memoryMetricsSwitchHtml(false);
    const swUnk = memoryMetricsSwitchHtml(null);
    function swAttr(html, attr) {
        const m = new RegExp(attr + '="([^"]*)"').exec(html);
        return m ? m[1] : '';
    }
    const stOn = swAttr(swOn, 'style'), stOff = swAttr(swOff, 'style'), stUnk = swAttr(swUnk, 'style');
    ok('三态色差: 开 / 关 / 未知 三态 style 片段互不相同（不能只差文案）',
        !!stOn && !!stOff && !!stUnk && stOn !== stOff && stOn !== stUnk && stOff !== stUnk,
        'on=' + stOn + ' | off=' + stOff + ' | unknown=' + stUnk);
    ok('三态色差: 三态同时带可识别的 data-switch 标记与配色，且作用在同一元素上',
        swAttr(swOn, 'data-switch') === 'on' && swAttr(swOff, 'data-switch') === 'off'
        && swAttr(swUnk, 'data-switch') === 'unknown'
        && mmTags(swOn).length === 1 && mmStyleOf(mmTags(swOn)[0].tag) === stOn,
        'on=' + swOn + ' || off=' + swOff + ' || unknown=' + swUnk);

    // 异常态可见：分母为 0 与取数失败都必须有可见文案，不得渲染成空白
    const zeroHtml = artifacts[1][1];
    ok('异常态: 全部分母为 0 → 每行都显示「无数据」而不是 0.00%',
        (zeroHtml.match(/无数据/g) || []).length >= 5 && zeroHtml.indexOf('0.00%') === -1,
        'html=' + zeroHtml);
    ok('异常态: 取数失败 → 显示错误态且面板非空（不渲染成空白）',
        artifacts[2][1].indexOf('度量读取失败') !== -1
        && mmTags(artifacts[2][1]).some(t => t.cls === 'mm-error'),
        'html=' + artifacts[2][1]);

    // ---- 5. loadMemoryMetrics：端点接线 + 角色门 ----
    calls.length = 0;
    metricsMode = 'ok';
    metricsResponse = { status: 'success', memory_enabled: true, metrics: GOOD_METRICS };
    loadMemoryMetrics();
    await new Promise(r => setTimeout(r, 20));
    ok('loadMemoryMetrics → 请求打在只读端点 /api/admin/memory-metrics',
        calls.length === 1 && calls[0].indexOf('/api/admin/memory-metrics') === 0, 'calls=' + JSON.stringify(calls));
    ok('loadMemoryMetrics → 响应渲染进面板',
        panelEl.innerHTML.indexOf('66.67%') !== -1, 'inner=' + panelEl.innerHTML);

    calls.length = 0;
    sandbox.localStorage._m.demo_role = 'owner';
    loadMemoryMetrics();
    await new Promise(r => setTimeout(r, 20));
    ok('非 admin 角色 → 不发请求（后端 403 兜底之外的前端省流）',
        calls.length === 0, 'calls=' + JSON.stringify(calls));
    sandbox.localStorage._m.demo_role = 'admin';

    calls.length = 0;
    metricsMode = 'network';
    loadMemoryMetrics();
    await new Promise(r => setTimeout(r, 20));
    ok('网络失败 → 渲染错误态而不是空白', panelEl.innerHTML.indexOf('度量读取失败') !== -1,
        'inner=' + panelEl.innerHTML);

    // ---- 6. 接线：HTML 锚点 + tab-engine 分支调用 ----
    ok('接线: index.html 存在 #memoryMetricsPanel 锚点',
        indexHtml.indexOf('id="memoryMetricsPanel"') !== -1, '未找到锚点');
    const engineBranch = srcAll.split("if (targetId === 'tab-engine')")[1];
    ok('接线: tab-engine 分支调用 loadMemoryMetrics()（不是定义了没人调）',
        !!engineBranch && engineBranch.split('}')[0].indexOf('loadMemoryMetrics()') !== -1,
        'branch=' + (engineBranch || '').split('}')[0]);

    // ---- 7. 红线：无未捕获异常 ----
    ok('全程 console.error 为 0', consoleErrors.length === 0,
        'errors=' + JSON.stringify(consoleErrors.slice(0, 3)));

    console.log('\n' + pass + ' passed / ' + fail + ' failed');
    process.exit(fail === 0 ? 0 : 1);
}

main();
