// -*- coding: utf-8 -*-
// S09 前端契约 harness：价格占位、未知不显示已付、同键重试、计费权限显隐。
// 用法：node demo/tests/test_s09_billing_ui_harness.js
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const demoDir = path.join(__dirname, '..');
const src = fs.readFileSync(path.join(demoDir, 'static', 'js', 'billing.js'), 'utf8');
const commonSrc = fs.readFileSync(path.join(demoDir, 'static', 'js', 'common.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(demoDir, 'templates', 'index.html'), 'utf8');

let pass = 0, fail = 0;
const failures = [];
function ok(name, cond, detail) {
    if (cond) pass++;
    else { fail++; failures.push(name + ' -- ' + (detail || '')); }
}

function mkStorage() {
    const m = new Map();
    return {
        getItem(k) { return m.has(k) ? m.get(k) : null; },
        setItem(k, v) { m.set(k, String(v)); },
        removeItem(k) { m.delete(k); },
    };
}

function mkClassList() {
    const s = new Set();
    return {
        add(...c) { c.forEach(x => s.add(x)); },
        remove(...c) { c.forEach(x => s.delete(x)); },
        contains(c) { return s.has(c); },
    };
}

function mkEnvironment(responder) {
    const elements = {};
    function el(id) {
        if (!elements[id]) {
            elements[id] = {
                id, value: '', textContent: '', innerHTML: '', style: {},
                classList: mkClassList(), listeners: {},
                addEventListener(ev, fn) { this.listeners[ev] = fn; },
            };
        }
        return elements[id];
    }
    const calls = [];
    const storage = mkStorage();
    const windowObj = { sessionStorage: storage, addEventListener() {} };
    const documentObj = {
        getElementById: el,
        addEventListener() {},
        querySelector() { return null; },
        querySelectorAll() { return []; },
        body: { dataset: {} },
    };
    async function apiFetch(url, init) {
        calls.push({ url, init: init || {} });
        return responder(url, init || {}, calls.length);
    }
    const sandbox = {
        window: windowObj, document: documentObj, apiFetch,
        console, setTimeout, clearTimeout, Date, Math, JSON, Number, String, encodeURIComponent,
        showToast() {}, showCustomConfirmModal(opts) { if (opts.onConfirm) opts.onConfirm(); },
    };
    windowObj.window = windowObj;
    vm.createContext(sandbox);
    vm.runInContext(src, sandbox);
    return { sandbox, windowObj, documentObj, elements, el, calls, apiFetch, storage };
}

function jsonResponse(status, payload) {
    return { ok: status >= 200 && status < 300, status, json: async () => payload };
}

(async function main() {
    // 1) 静态接线
    ok('index 有计费页签锚点', indexHtml.indexOf('id="tab-billing"') !== -1);
    ok('index 有计费入口按钮', indexHtml.indexOf('id="billingNavBtn"') !== -1);
    ok('index 加载 billing.js', indexHtml.indexOf('/static/js/billing.js') !== -1);
    ok('common 分发 tab-billing', commonSrc.indexOf("targetId === 'tab-billing'") !== -1);
    ok('common 角色切换探测计费权限', commonSrc.indexOf('probeBillingAccess') !== -1);

    // 2) 状态渲染：金额恒为 $***，不泄漏数值价格
    {
        const env = mkEnvironment(async () => jsonResponse(200, {}));
        env.windowObj.renderBillingStatus({
            subscription: { state: 'active', plan_ref: 'PLACEHOLDER',
                            period_ends_at: '2026-11-26T00:00:00+00:00' },
            entitlement: { allow_new_ai: true },
            price_display: '$***'
        });
        const text = env.el('billingStatusCard').textContent;
        ok('状态卡显示已生效', text.indexOf('已生效') !== -1, text);
        ok('状态卡显示周期', text.indexOf('本周期至') !== -1, text);
        ok('价格显示 $***', env.el('billingPriceDisplay').textContent === '$***',
           env.el('billingPriceDisplay').textContent);
        ok('状态卡不含数值价格', !/(HK\$|\$)\s?\d/.test(text), text);
    }

    // 3) 未知订单不得显示已付
    {
        const env = mkEnvironment(async () => jsonResponse(200, {}));
        env.windowObj.renderBillingOrder({
            order_no: 'ORD-X', status: 'created', attempt_status: 'unknown',
            price_display: '$***'
        });
        const text = env.el('billingOrderResult').textContent;
        ok('未知订单提示待确认', text.indexOf('暂不显示“已付”') !== -1, text);
        ok('未知订单不显示已核实收款', text.indexOf('已核实收款') === -1, text);
    }

    // 4) 同键重试：失败后重试用同一 request_key；成功后轮换
    {
        let checkoutCount = 0;
        const responder = async (url, init) => {
            if (url.indexOf('/api/billing/subscription') === 0) {
                return jsonResponse(200, {
                    subscription: { state: 'trialing', plan_ref: 'PLACEHOLDER' },
                    entitlement: { allow_new_ai: true }, price_display: '$***'
                });
            }
            if (url.indexOf('/api/billing/checkout') === 0) {
                checkoutCount++;
                if (checkoutCount === 1) return jsonResponse(500, { msg: '暂不可用' });
                return jsonResponse(200, { status: 'success', data: {
                    order_no: 'ORD-1', status: 'created', price_display: '$***' } });
            }
            return jsonResponse(200, {});
        };
        const env = mkEnvironment(responder);
        await env.windowObj.loadBillingPanel();
        await env.windowObj.billingActions.createCheckout();
        await env.windowObj.billingActions.createCheckout();
        await env.windowObj.billingActions.createCheckout();
        const bodies = env.calls.filter(c => c.url.indexOf('/api/billing/checkout') === 0)
            .map(c => JSON.parse(c.init.body));
        ok('checkout 调用 3 次', bodies.length === 3, String(bodies.length));
        ok('失败重试复用同 key', bodies[0].request_key === bodies[1].request_key,
           bodies.map(b => b.request_key).join(','));
        ok('成功后轮换 key', bodies[2].request_key !== bodies[1].request_key,
           bodies.map(b => b.request_key).join(','));
    }

    // 5) 403：隐藏入口 + 人话提示，不渲染已付
    {
        const responder = async () => jsonResponse(403, {
            msg: '权限不足：计费操作需要「计费负责人」权限。' });
        const env = mkEnvironment(responder);
        await env.windowObj.loadBillingPanel();
        ok('403 隐藏计费入口', env.el('billingNavBtn').style.display === 'none',
           env.el('billingNavBtn').style.display);
        ok('403 显示权限人话', env.el('billingPermissionMsg').textContent.indexOf('权限不足') !== -1);
        ok('403 不渲染已付', env.el('billingOrderResult').textContent.indexOf('已核实收款') === -1);
    }

    // 6) 试用订阅（plan_ref=TRIAL-COUPON）下单不得把试用 ref 当可售套餐
    {
        const responder = async (url) => {
            if (url.indexOf('/api/billing/subscription') === 0) {
                return jsonResponse(200, {
                    subscription: { state: 'trialing', plan_ref: 'TRIAL-COUPON' },
                    entitlement: { allow_new_ai: true }, price_display: '$***'
                });
            }
            if (url.indexOf('/api/billing/checkout') === 0) {
                return jsonResponse(200, { data: { order_no: 'ORD-2', price_display: '$***' } });
            }
            return jsonResponse(200, {});
        };
        const env = mkEnvironment(responder);
        await env.windowObj.loadBillingPanel();
        await env.windowObj.billingActions.createCheckout();
        const call = env.calls.filter(c => c.url.indexOf('/api/billing/checkout') === 0)[0];
        ok('试用 ref 不作为套餐下单', JSON.parse(call.init.body).plan_ref === '',
           call.init.body);
    }

    // 7) 退款：refund_id 字段与列表渲染
    {
        const responder = async (url) => {
            if (url.indexOf('/api/billing/refunds') === 0 && (!url.match(/refunds\/\d/))) {
                return jsonResponse(200, { data: { refund_id: 7, state: 'requested' } });
            }
            if (url.indexOf('/api/billing/refunds/7') === 0) {
                return jsonResponse(200, { data: { refund_id: 7, state: 'requested',
                    amount_display: '$***', created_at: '2026-10-26T00:00:00+00:00' } });
            }
            return jsonResponse(200, {});
        };
        const env = mkEnvironment(responder);
        env.el('billingOrderNo').value = 'ORD-7';
        env.el('billingRefundAmount').value = '100';
        await env.windowObj.billingActions.requestRefund();
        const text = env.el('billingRefundList').textContent;
        ok('退款列表使用 refund_id 渲染', text.indexOf('退款 #7') !== -1, text);
        ok('退款金额被掩码', text.indexOf('$***') !== -1 && text.indexOf('100') === -1, text);
    }

    // 8) 时间按香港时区展示
    {
        const env = mkEnvironment(async () => jsonResponse(200, {}));
        env.windowObj.renderBillingStatus({
            subscription: { state: 'trialing', trial_ends_at: '2026-11-09T00:00:00+00:00' },
            entitlement: { allow_new_ai: true }, price_display: '$***'
        });
        ok('时间标注香港时区',
           env.el('billingStatusCard').textContent.indexOf('香港时间') !== -1,
           env.el('billingStatusCard').textContent);
    }

    // 9) 人工收款币种不再写死 HKD
    {
        const responder = async (url) => {
            if (url.indexOf('/manual-payment') !== -1) {
                return jsonResponse(200, { data: { status: 'paid' } });
            }
            if (url.indexOf('/api/billing/orders/') === 0) {
                return jsonResponse(200, { data: { order_no: 'ORD-9', status: 'paid',
                    attempt_status: 'succeeded', currency: 'USD', price_display: '$***' } });
            }
            return jsonResponse(200, { subscription: null, entitlement: {}, price_display: '$***' });
        };
        const env = mkEnvironment(responder);
        env.el('billingOrderNo').value = 'ORD-9';
        env.el('billingManualAmount').value = '100';
        env.el('billingManualCurrency').value = 'USD';
        await env.windowObj.billingActions.registerManualPayment();
        const call = env.calls.filter(c => c.url.indexOf('/manual-payment') !== -1)[0];
        ok('人工收款使用输入币种', JSON.parse(call.init.body).currency === 'USD',
           call.init.body);
    }

    console.log('S09 UI harness: pass=' + pass + ' fail=' + fail);
    if (fail) {
        failures.forEach(f => console.log('FAIL: ' + f));
        process.exit(1);
    }
})();
