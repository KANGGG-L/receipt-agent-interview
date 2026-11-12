// -*- coding: utf-8 -*-
// S09 订阅与账单：客户/计费负责人视图。
// 口径：
// - 价格占位一律 $***（D06 未签数值价格，前端不得自造金额）。
// - 订单未知/处理中不显示“已付”；成功页只查后端订单事实。
// - 重复点击复用同一 request_key（会话内），成功后再轮换。
// - 取消订阅明确提示“不删除历史数据”；历史/手工/导出入口不受限。
// - 计费入口按 capability 单独显隐（餐厅 owner 不自动获得），403 只显示人话提示。

(function () {
    'use strict';

    var STATUS_TEXT = {
        trialing: '试用中',
        trial_expired: '试用已结束',
        pending_payment: '待付款',
        active: '已生效',
        past_due: '欠费（暂停新 AI）',
        cancel_scheduled: '已预约到期取消',
        terminated: '已取消',
        created: '订单已创建',
        paid: '已核实收款',
        failed: '支付失败',
        refunded: '已退款',
        pending: '处理中',
        unknown: '渠道结果待确认',
        succeeded: '已完成',
        cancelled: '已取消',
        requested: '退款申请已提交',
        processing: '退款处理中'
    };

    var state = { subscription: null, entitlement: null, orderNo: '', refundIds: [] };

    function $(id) { return document.getElementById(id); }

    function notify(msg, type) {
        if (typeof showToast === 'function') {
            try { showToast(msg, type || 'info'); return; } catch (e) { /* noop */ }
        }
        var el = $('billingPermissionMsg');
        if (el) { el.textContent = msg; el.classList.remove('hide'); }
    }

    function fmt(iso) {
        if (!iso) return '—';
        try {
            var d = new Date(iso);
            if (!isNaN(d.getTime())) {
                // 服务端存 UTC；界面按香港时区展示，避免“UTC 时间”被误读为本地时间。
                return d.toLocaleString('zh-HK', {
                    timeZone: 'Asia/Hong_Kong', hour12: false,
                    year: 'numeric', month: '2-digit', day: '2-digit',
                    hour: '2-digit', minute: '2-digit'
                }) + '（香港时间）';
            }
        } catch (e) { /* fall through */ }
        return String(iso);
    }

    function label(code) { return STATUS_TEXT[code] || (code || '未知状态'); }

    function storageGet(k) { try { return window.sessionStorage.getItem(k) || ''; } catch (e) { return ''; } }
    function storageSet(k, v) { try { window.sessionStorage.setItem(k, v); } catch (e) { /* noop */ } }
    function storageDel(k) { try { window.sessionStorage.removeItem(k); } catch (e) { /* noop */ } }

    // 同一动作重复点击复用同 key；成功后轮换，避免下一次新操作被当成重试。
    function reqKey(action, bind) {
        var k = 'billing:req:' + action + ':' + (bind || '');
        var v = storageGet(k);
        if (!v) {
            v = 'ui-' + action + '-' + Date.now() + '-' + Math.random().toString(36).slice(2, 10);
            storageSet(k, v);
        }
        return v;
    }
    function rotateKey(action, bind) { storageDel('billing:req:' + action + ':' + (bind || '')); }

    function setText(id, text) {
        var el = $(id);
        if (el) el.textContent = text;
    }

    function showNav(show) {
        var btn = $('billingNavBtn');
        if (btn) btn.style.display = show ? '' : 'none';
    }

    function showPermission(msg) {
        var el = $('billingPermissionMsg');
        if (el) { el.textContent = msg; el.classList.remove('hide'); }
        showNav(false);
    }

    function renderStatus(data) {
        var card = $('billingStatusCard');
        if (!card) return;
        var sub = data.subscription || null;
        var ent = data.entitlement || {};
        var parts = [];
        if (!sub) {
            parts.push('还没有试用或套餐。可兑换试用券，或继续使用手工录入（历史数据不受影响）。');
        } else {
            parts.push('状态：' + label(sub.state));
            if (sub.trial_ends_at) parts.push('试用至：' + fmt(sub.trial_ends_at));
            if (sub.period_ends_at) parts.push('本周期至：' + fmt(sub.period_ends_at));
            if (sub.cancel_at) parts.push('取消生效：' + fmt(sub.cancel_at));
        }
        parts.push(ent.allow_new_ai ? '新 AI 识别：可用' : '新 AI 识别：暂停（历史/导出/手工仍可用）');
        if (ent.msg) parts.push(ent.msg);
        card.textContent = parts.join('；');
        setText('billingPriceDisplay', data.price_display || '$***');
    }

    async function readJson(res) {
        try { return await res.json(); } catch (e) { return {}; }
    }

    function isDenied(res) { return res.status === 401 || res.status === 403; }

    async function probeBillingAccess() {
        if (typeof apiFetch !== 'function') return false;
        try {
            var res = await apiFetch('/api/billing/entitlement');
            if (res.status === 200) { showNav(true); return true; }
            if (isDenied(res)) { showNav(false); return false; }
        } catch (e) { /* 网络异常不改变入口 */ }
        return false;
    }

    async function loadBillingPanel() {
        if (typeof apiFetch !== 'function') return;
        var res = await apiFetch('/api/billing/subscription');
        if (isDenied(res)) {
            var body = await readJson(res);
            showPermission(body.msg || body.detail ||
                '权限不足：计费功能只对计费负责人/计费操作员开放。餐厅老板/店员不会自动获得。');
            var card = $('billingStatusCard');
            if (card) card.textContent = '当前账号没有计费权限。';
            return;
        }
        if (!res.ok) {
            notify('订阅状态加载失败，请稍后重试。', 'error');
            return;
        }
        var data = await readJson(res);
        state.subscription = data.subscription || null;
        state.entitlement = data.entitlement || null;
        showNav(true);
        var perm = $('billingPermissionMsg');
        if (perm) perm.classList.add('hide');
        renderStatus(data);
        refreshRefunds();
    }

    async function redeemTrialCoupon() {
        var code = ($('billingCouponCode') || {}).value || '';
        if (!code.trim()) { notify('请先填写试用券编号。', 'warning'); return; }
        var res = await apiFetch('/api/billing/trial/redeem', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ coupon_code: code.trim() })
        });
        var body = await readJson(res);
        if (!res.ok) { notify(body.msg || body.detail || '试用兑换失败，请核对编号。', 'error'); return; }
        notify(body.msg || '试用已开通。', 'success');
        await loadBillingPanel();
    }

    async function createCheckout() {
        var planRef = (state.subscription && state.subscription.plan_ref) || '';
        // 试用期订阅的 plan_ref 是 TRIAL-COUPON，不是可售套餐；直接下单会 404。
        if (!planRef || /^trial/i.test(planRef)) planRef = '';
        var key = reqKey('checkout', planRef || 'default');
        var res = await apiFetch('/api/billing/checkout', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ plan_ref: planRef, request_key: key })
        });
        var body = await readJson(res);
        if (!res.ok) {
            notify(body.msg || body.detail || '下单失败，请稍后再试。', 'error');
            return;
        }
        var d = body.data || {};
        state.orderNo = d.order_no || '';
        rotateKey('checkout', planRef || 'default');
        var input = $('billingOrderNo');
        if (input && state.orderNo) input.value = state.orderNo;
        notify(body.msg || '订单已创建。', 'success');
        renderOrder({
            order_no: d.order_no, status: d.order_status || d.status,
            attempt_status: d.status, price_display: d.price_display || '$***'
        });
    }

    function renderOrder(order) {
        var el = $('billingOrderResult');
        if (!el) return;
        if (!order) { el.textContent = '没有订单。'; return; }
        if (order.currency) state.orderCurrency = order.currency;
        var lines = [
            '订单号：' + (order.order_no || '—'),
            '订单状态：' + label(order.status),
            '支付状态：' + label(order.attempt_status),
            '价格：' + (order.price_display || '$***')
        ];
        if (order.status === 'paid') lines.push('已核实收款，订阅周期已生效。');
        else if (order.attempt_status === 'unknown' || order.status === 'unknown' ||
                 order.status === 'pending' || order.attempt_status === 'pending') {
            lines.push('渠道结果尚未确认，暂不显示“已付”；请稍后查询或人工登记。');
        }
        el.textContent = lines.join('\n');
    }

    async function lookupOrder() {
        var orderNo = ($('billingOrderNo') || {}).value || state.orderNo;
        if (!orderNo.trim()) { notify('请先填写订单号。', 'warning'); return; }
        var res = await apiFetch('/api/billing/orders/' + encodeURIComponent(orderNo.trim()));
        var body = await readJson(res);
        if (!res.ok) { notify(body.msg || body.detail || '找不到订单。', 'error'); return; }
        state.orderNo = orderNo.trim();
        renderOrder(body.data || {});
    }

    async function registerManualPayment() {
        var orderNo = ($('billingOrderNo') || {}).value || state.orderNo;
        var amount = ($('billingManualAmount') || {}).value || '';
        var invoice = ($('billingManualInvoice') || {}).value || '';
        var currency = (($('billingManualCurrency') || {}).value || state.orderCurrency || 'HKD');
        if (!orderNo.trim()) { notify('请先查询/创建订单。', 'warning'); return; }
        if (!amount.trim()) { notify('请填写登记金额（分）。', 'warning'); return; }
        var key = reqKey('pay', orderNo.trim());
        var res = await apiFetch('/api/billing/orders/' + encodeURIComponent(orderNo.trim()) +
            '/manual-payment', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                amount_minor: Number(amount), currency: String(currency).toUpperCase(),
                request_key: key, invoice_no: invoice.trim()
            })
        });
        var body = await readJson(res);
        if (!res.ok) {
            notify(body.msg || body.detail || '登记收款失败，请核对金额与订单。', 'error');
            return;
        }
        rotateKey('pay', orderNo.trim());
        notify(body.msg || '收款已登记。', 'success');
        await lookupOrder();
        await loadBillingPanel();
    }

    async function cancelSubscription() {
        function doCancel() {
            apiFetch('/api/billing/subscription/cancel', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ request_key: reqKey('cancel', 'subscription') })
            }).then(async function (res) {
                var body = await readJson(res);
                if (!res.ok) { notify(body.msg || body.detail || '取消失败。', 'error'); return; }
                rotateKey('cancel', 'subscription');
                notify(body.msg || '订阅已取消。历史数据不会被删除。', 'success');
                loadBillingPanel();
            }).catch(function () { notify('取消失败，请稍后重试。', 'error'); });
        }
        if (typeof showCustomConfirmModal === 'function') {
            showCustomConfirmModal({
                title: '取消订阅',
                message: '取消后只影响新的 AI 识别；历史单据、导出、对账与手工录入都保留，不会删除数据。确定继续？',
                confirmText: '确定取消',
                cancelText: '先不取消',
                onConfirm: doCancel
            });
            return;
        }
        doCancel();
    }

    async function requestRefund() {
        var orderNo = ($('billingOrderNo') || {}).value || state.orderNo;
        var amount = ($('billingRefundAmount') || {}).value || '';
        var reason = ($('billingRefundReason') || {}).value || '';
        if (!orderNo.trim() || !amount.trim()) {
            notify('请填写订单号与退款金额（分）。', 'warning'); return;
        }
        var key = reqKey('refund', orderNo.trim());
        var res = await apiFetch('/api/billing/refunds', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ order_no: orderNo.trim(), amount_minor: Number(amount),
                                   request_key: key, reason: reason.trim() })
        });
        var body = await readJson(res);
        if (!res.ok) {
            notify(body.msg || body.detail ||
                '退款暂时不能提交（退款规则未配置或权限不足）。', 'error');
            return;
        }
        rotateKey('refund', orderNo.trim());
        var d = body.data || {};
        // 后端返回 refund_id（不是 id）；兼容两种字段，避免列表恒空。
        var rid = (d.refund_id !== undefined) ? d.refund_id : d.id;
        if (rid !== undefined && rid !== null) {
            state.refundIds.push(rid);
            var uniq = state.refundIds.filter(function (v, i, a) { return a.indexOf(v) === i; });
            storageSet('billing:refundIds', JSON.stringify(uniq));
        }
        notify(body.msg || '退款申请已提交，处理完成前不会修改权益。', 'success');
        await refreshRefunds();
    }

    async function refreshRefunds() {
        var el = $('billingRefundList');
        if (!el) return;
        var ids = [];
        try { ids = JSON.parse(storageGet('billing:refundIds') || '[]'); } catch (e) { ids = []; }
        state.refundIds = ids;
        if (!ids.length) { el.textContent = '暂无退款申请。'; return; }
        var lines = [];
        for (var i = 0; i < ids.length; i++) {
            var res = await apiFetch('/api/billing/refunds/' + encodeURIComponent(ids[i]));
            if (!res.ok) continue;
            var body = await readJson(res);
            var d = body.data || {};
            var rid = (d.refund_id !== undefined) ? d.refund_id : d.id;
            lines.push('退款 #' + rid + '：' + label(d.state) +
                       '（金额 ' + (d.amount_display || '$***') + '）' +
                       (d.created_at ? ' 提交于 ' + fmt(d.created_at) : ''));
        }
        el.textContent = lines.length ? lines.join('\n') : '暂无退款申请。';
    }

    function wire() {
        var map = {
            btnRedeemCoupon: redeemTrialCoupon,
            btnCreateCheckout: createCheckout,
            btnLookupOrder: lookupOrder,
            btnManualPayment: registerManualPayment,
            btnCancelSubscription: cancelSubscription,
            btnRequestRefund: requestRefund,
            btnRefreshRefunds: refreshRefunds
        };
        Object.keys(map).forEach(function (id) {
            var el = $(id);
            if (el && el.addEventListener) el.addEventListener('click', map[id]);
        });
        var nav = $('billingNavBtn');
        if (nav && nav.addEventListener) {
            nav.addEventListener('click', function () { loadBillingPanel(); });
        }
    }

    // 首屏探测可能早于角色初始化/登录态回填（此时请求没有身份头会 401）。
    // 做有限次重试，避免有计费权限的用户首屏看不到入口。
    function probeWithRetry(remaining) {
        probeBillingAccess().then(function (ok) {
            if (!ok && remaining > 0) {
                setTimeout(function () { probeWithRetry(remaining - 1); }, 700);
            }
        });
    }

    if (document && document.addEventListener) {
        document.addEventListener('DOMContentLoaded', function () {
            wire();
            probeWithRetry(3);
        });
    }

    window.probeBillingAccess = probeBillingAccess;
    window.loadBillingPanel = loadBillingPanel;
    window.renderBillingStatus = renderStatus;
    window.renderBillingOrder = renderOrder;
    // 供前端契约 harness 调用（浏览器里无副作用，仅暴露同名函数）。
    window.billingActions = {
        redeemTrialCoupon: redeemTrialCoupon,
        createCheckout: createCheckout,
        lookupOrder: lookupOrder,
        registerManualPayment: registerManualPayment,
        cancelSubscription: cancelSubscription,
        requestRefund: requestRefund,
        refreshRefunds: refreshRefunds
    };
})();
