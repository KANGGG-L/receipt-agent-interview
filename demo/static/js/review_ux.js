/*
 * 识别复核主流程与手机端的界面逻辑（F1a）。
 * 与 main.js 共用全局函数（showToast、apiFetch、currentReceiptData 等），在 main.js 之后加载。
 * 涵盖：手机底部导航、总额手改提示、结算方式预选、入账失败的人话处理（单位换算缺失等）、
 * 冲销、标记有问题（可填原因）、放弃后撤销、SKU 候选确认。
 */
(function (root) {
    'use strict';

    var doc = root.document;
    function $(id) { return doc.getElementById(id); }
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function role() {
        return (typeof root.currentRoleName === 'function') ? root.currentRoleName() : 'admin';
    }
    function isOwnerOrAbove() { return role() !== 'staff'; }
    function toast(msg, type, dur) {
        if (typeof root.showToast === 'function') return root.showToast(msg, type || 'info', dur);
    }

    // ---------------------------------------------------------------------
    // 应用内确认框（复用 ui-dialog.js 的 uiConfirm；没有时退回 main.js 的 showCustomConfirmModal）
    // ---------------------------------------------------------------------
    function askConfirm(opts) {
        if (typeof root.uiConfirm === 'function') return root.uiConfirm(opts);
        return new Promise(function (resolve) {
            root.showCustomConfirmModal({
                title: opts.title, message: opts.message,
                confirmText: opts.confirmText, cancelText: opts.cancelText,
                onConfirm: function () { resolve(true); },
                onCancel: function () { resolve(false); }
            });
        });
    }

    function askText(opts) {
        if (typeof root.uiPrompt === 'function') return root.uiPrompt(opts);
        return new Promise(function (resolve) {
            root.showCustomPromptModal({
                title: opts.title, message: opts.label, placeholder: opts.placeholder, maxLength: 200,
                confirmText: opts.confirmText, cancelText: opts.cancelText,
                onConfirm: function (v) { resolve(v); },
                onCancel: function () { resolve(null); }
            });
        });
    }

    root.askConfirm = askConfirm;
    root.askText = askText;

    // ---------------------------------------------------------------------
    // 手机底部导航（S3-03）：数据来自桌面侧栏的按钮，不另外维护一份
    // ---------------------------------------------------------------------
    var TAB_BAR = [
        { target: 'tab-scan', label: '识别' },
        { target: 'tab-inventory', label: '库存' },
        { target: 'tab-archive', label: '对账' }
    ];
    var ICONS = {
        'tab-scan': '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8V6a2 2 0 0 1 2-2h2M16 4h2a2 2 0 0 1 2 2v2M20 16v2a2 2 0 0 1-2 2h-2M8 20H6a2 2 0 0 1-2-2v-2"/><path d="M8 12h8"/></svg>',
        'tab-inventory': '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 8l-9-5-9 5 9 5 9-5z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></svg>',
        'tab-archive': '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/></svg>',
        more: '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></svg>'
    };

    function sidebarButtons() {
        return Array.prototype.slice.call(doc.querySelectorAll('.sidebar-nav .sidebar-btn[data-target]'));
    }

    function isShown(el) {
        return el && root.getComputedStyle(el).display !== 'none';
    }

    function navLabel(btn) {
        return btn.getAttribute('data-title') || (btn.textContent || '').replace(/\s+/g, ' ').trim();
    }

    function activeTarget() {
        var a = doc.querySelector('.tab-content.active');
        return a ? a.id : '';
    }

    function goTab(target) {
        var btn = doc.querySelector('.sidebar-nav .sidebar-btn[data-target="' + target + '"]');
        if (btn) btn.click();
        closeMore();
        syncTabBar();
    }

    function syncTabBar() {
        var bar = $('mobileTabBar');
        if (!bar) return;
        var cur = activeTarget();
        var inBar = TAB_BAR.some(function (t) { return t.target === cur; });
        Array.prototype.forEach.call(bar.querySelectorAll('[data-tab]'), function (b) {
            var on = b.getAttribute('data-tab') === cur;
            b.classList.toggle('active', on);
            b.setAttribute('aria-current', on ? 'page' : 'false');
        });
        var more = bar.querySelector('[data-more]');
        if (more) more.classList.toggle('active', !inBar);
        var dot = $('mobileTabArchiveDot');
        var src = $('navPayRedDot');
        if (dot && src) {
            dot.textContent = src.textContent;
            dot.classList.toggle('hide', src.classList.contains('hide'));
        }
    }

    function closeMore() {
        var sheet = $('mobileMoreSheet');
        if (sheet) sheet.classList.add('hide');
        var moreBtn = doc.querySelector('#mobileTabBar [data-more]');
        if (moreBtn) moreBtn.setAttribute('aria-expanded', 'false');
    }

    function buildMoreSheet() {
        var sheet = $('mobileMoreSheet');
        if (!sheet) return;
        var inBar = TAB_BAR.map(function (t) { return t.target; });
        var items = sidebarButtons().filter(function (b) {
            return inBar.indexOf(b.getAttribute('data-target')) === -1 && isShown(b);
        });
        var html = '<div class="mobile-more-panel" role="dialog" aria-modal="true" aria-label="更多功能">'
            + '<div class="mobile-more-head"><strong>更多</strong>'
            + '<button type="button" class="mobile-more-close" aria-label="关闭" data-close>关闭</button></div>'
            + '<div class="mobile-more-list">';
        items.forEach(function (b) {
            var t = b.getAttribute('data-target');
            var isAdminItem = b.classList.contains('admin-only');
            html += '<button type="button" class="mobile-more-item" data-go="' + esc(t) + '">'
                + esc(navLabel(b)) + (isAdminItem ? '<span class="mobile-more-tag">管理员</span>' : '') + '</button>';
        });
        html += '</div>';
        var roleSel = $('demoRoleSelect');
        if (roleSel) {
            html += '<div class="mobile-more-block"><label for="mobileRoleSelect">当前角色</label>'
                + '<select id="mobileRoleSelect" class="form-control">';
            Array.prototype.forEach.call(roleSel.options, function (o) {
                html += '<option value="' + esc(o.value) + '"' + (o.value === roleSel.value ? ' selected' : '') + '>'
                    + esc((o.textContent || '').replace(/^[\s\u25CF]+/, '')) + '</option>';
            });
            html += '</select></div>';
        }
        if (root.I18N && typeof root.I18N.getSupportedLocales === 'function') {
            html += '<div class="mobile-more-block"><span class="mobile-more-label">界面语言</span><div class="mobile-more-langs">';
            root.I18N.getSupportedLocales().forEach(function (loc) {
                var on = loc === root.I18N.getLocale();
                html += '<button type="button" class="lang-switch-btn' + (on ? ' active' : '') + '" translate="no" data-locale="' + esc(loc) + '">'
                    + esc(root.I18N.getNativeName ? root.I18N.getNativeName(loc) : loc) + '</button>';
            });
            html += '</div></div>';
        }
        html += '</div>';
        sheet.innerHTML = html;
        sheet.querySelector('[data-close]').addEventListener('click', closeMore);
        Array.prototype.forEach.call(sheet.querySelectorAll('[data-go]'), function (b) {
            b.addEventListener('click', function () { goTab(b.getAttribute('data-go')); });
        });
        var rs = $('mobileRoleSelect');
        if (rs) {
            rs.addEventListener('change', function () {
                roleSel.value = rs.value;
                roleSel.dispatchEvent(new root.Event('change', { bubbles: true }));
            });
        }
        Array.prototype.forEach.call(sheet.querySelectorAll('[data-locale]'), function (b) {
            b.addEventListener('click', function () { root.I18N.setLocale(b.getAttribute('data-locale')); });
        });
        sheet.addEventListener('click', function (e) { if (e.target === sheet) closeMore(); });
    }

    function initMobileNav() {
        if ($('mobileTabBar')) return;
        var bar = doc.createElement('nav');
        bar.id = 'mobileTabBar';
        bar.className = 'mobile-tabbar';
        bar.setAttribute('aria-label', '主菜单');
        var html = '';
        TAB_BAR.forEach(function (t) {
            html += '<button type="button" class="mobile-tab" data-tab="' + t.target + '">'
                + ICONS[t.target] + '<span>' + t.label + '</span>'
                + (t.target === 'tab-archive' ? '<span id="mobileTabArchiveDot" class="mobile-tab-dot hide">0</span>' : '')
                + '</button>';
        });
        html += '<button type="button" class="mobile-tab" data-more aria-haspopup="dialog" aria-expanded="false">'
            + ICONS.more + '<span>更多</span></button>';
        bar.innerHTML = html;
        doc.body.appendChild(bar);
        var sheet = doc.createElement('div');
        sheet.id = 'mobileMoreSheet';
        sheet.className = 'mobile-more-sheet hide';
        doc.body.appendChild(sheet);

        Array.prototype.forEach.call(bar.querySelectorAll('[data-tab]'), function (b) {
            b.addEventListener('click', function () { goTab(b.getAttribute('data-tab')); });
        });
        bar.querySelector('[data-more]').addEventListener('click', function () {
            var open = !sheet.classList.contains('hide');
            if (open) { closeMore(); return; }
            buildMoreSheet();
            sheet.classList.remove('hide');
            this.setAttribute('aria-expanded', 'true');
        });
        doc.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeMore(); });
        var observer = new root.MutationObserver(syncTabBar);
        Array.prototype.forEach.call(doc.querySelectorAll('.tab-content'), function (el) {
            observer.observe(el, { attributes: true, attributeFilter: ['class'] });
        });
        var dotSrc = $('navPayRedDot');
        if (dotSrc) new root.MutationObserver(syncTabBar).observe(dotSrc, { attributes: true, childList: true, characterData: true, subtree: true });
        syncTabBar();
    }

    // ---------------------------------------------------------------------
    // 总额手改提示与结算方式预选（S3-19）
    // ---------------------------------------------------------------------
    var totalOverridden = false;

    root.isTotalOverridden = function () { return totalOverridden; };
    root.resetTotalOverride = function () {
        totalOverridden = false;
        var hint = $('totalOverrideHint');
        if (hint) { hint.classList.add('hide'); hint.textContent = ''; }
    };

    function itemsSumNow() {
        if (typeof root.collectFeeMap !== 'function') return null;
        var rows = doc.querySelectorAll('#itemTableBody tr');
        var sum = 0;
        rows.forEach(function (tr) {
            if (tr.dataset && tr.dataset.isVoid === '1') return;
            var a = tr.querySelector('.inp-amount');
            if (a) sum += parseFloat(a.value) || 0;
        });
        var f = root.collectFeeMap();
        return Math.max(0, sum - f.discount_amount - f.rounding_adjustment + f.delivery_fee + f.deposit_amount + f.service_fee + f.tax_amount);
    }

    root.updateTotalOverrideHint = function (autoTotal) {
        var hint = $('totalOverrideHint');
        var inp = $('inpTotal');
        if (!hint || !inp) return;
        if (!totalOverridden) { hint.classList.add('hide'); return; }
        var total = parseFloat(inp.value) || 0;
        var diff = Math.round((total - autoTotal) * 100) / 100;
        if (Math.abs(diff) < 0.005) {
            totalOverridden = false;
            hint.classList.add('hide');
            return;
        }
        hint.innerHTML = '总额是你手改的，和明细加起来（' + esc(autoTotal.toFixed(2)) + ' 元）相差 ' + esc(Math.abs(diff).toFixed(2))
            + ' 元。 <button type="button" class="link-btn" onclick="useItemsSumAsTotal()">改回明细合计</button>';
        hint.classList.remove('hide');
    };

    root.useItemsSumAsTotal = function () {
        totalOverridden = false;
        if (typeof root.recalcTotalSum === 'function') root.recalcTotalSum();
        var hint = $('totalOverrideHint');
        if (hint) hint.classList.add('hide');
        if (typeof root.refreshReceiptIssueSummary === 'function') root.refreshReceiptIssueSummary();
    };

    root.onTotalInput = function () {
        totalOverridden = true;
        if (typeof root.renderCurrencySymbol === 'function') root.renderCurrencySymbol();
        var auto = itemsSumNow();
        if (auto != null) root.updateTotalOverrideHint(auto);
    };

    root.applySupplierSettlementPref = function () {
        var sel = $('inpSettlementType');
        var sup = $('inpSupplier');
        if (!sel || !sup || sel.value) return;
        var name = (sup.value || '').trim();
        var list = availableSuppliers || [];
        for (var i = 0; i < list.length; i++) {
            var s = list[i];
            if (s && s.name === name && (s.settlement_pref === 'cash' || s.settlement_pref === 'credit')) {
                sel.value = s.settlement_pref;
                sel.dispatchEvent(new root.Event('change', { bubbles: true }));
                return;
            }
        }
    };

    // ---------------------------------------------------------------------
    // 入账失败的人话处理（单位换算缺失、汇率缺失、必须先改的地方）
    // ---------------------------------------------------------------------
    function pairsLines(pairs) {
        return (pairs || []).map(function (p) {
            return '「' + (p.item_name || '这一行') + '」：「' + p.from_unit + '」换「' + p.to_unit + '」';
        });
    }

    function goToUnitSettings(pair) {
        var btn = $('settingsNavBtn') || doc.querySelector('.sidebar-nav .sidebar-btn[data-target="tab-settings"]');
        if (btn) btn.click();
        var tries = 0;
        (function fill() {
            var from = $('stConvFrom'), to = $('stConvTo'), factor = $('stConvFactor');
            if (!from || !to || !factor) {
                if (tries++ < 30) root.setTimeout(fill, 100);
                return;
            }
            if (pair) {
                from.value = pair.from_unit || '';
                to.value = pair.to_unit || '';
                from.dispatchEvent(new root.Event('input', { bubbles: true }));
                to.dispatchEvent(new root.Event('input', { bubbles: true }));
            }
            var form = $('stConvForm');
            if (form && form.scrollIntoView) form.scrollIntoView({ behavior: 'smooth', block: 'center' });
            factor.focus();
        })();
    }

    root.showUnitConversionMissing = function (ret) {
        var pairs = (ret && ret.data && ret.data.pairs) || [];
        var owner = isOwnerOrAbove();
        var lines = pairsLines(pairs);
        var message = (owner
            ? '这张单里有的单位，和库存单位之间还没有换算关系，所以不能入账。\n请到「设置」里添加换算（例如 1 箱 = 12 瓶），或者把这一行的单位改成库存单位。'
            : '这张单里有的单位，和库存单位之间还没有换算关系，所以不能入账。\n请老板先到「设置」里添加换算，或者把这一行的单位改成库存单位。');
        var done = !owner
            ? (root.uiAlert ? root.uiAlert({ title: '这张单暂时不能入账', message: message + '\n\n' + lines.join('\n'), okText: '知道了' }).then(function () { return false; }) : Promise.resolve(false))
            : (root.uiConfirm
                ? root.uiConfirm({ title: '这张单暂时不能入账', message: message, details: lines, confirmText: '去设置添加换算', cancelText: '先不处理' })
                : Promise.resolve(false));
        return done.then(function (ok) {
            if (ok && owner) goToUnitSettings(pairs[0] || null);
        });
    };

    root.showFxMissing = function (ret) {
        var owner = isOwnerOrAbove();
        var message = ((ret && ret.msg) || '这张外币单还没有设置汇率，所以不能入账。')
            + (owner ? '' : '\n请老板先到「设置」里填好汇率。');
        var done = !owner
            ? (root.uiAlert ? root.uiAlert({ title: '这张单暂时不能入账', message: message, okText: '知道了' }).then(function () { return false; }) : Promise.resolve(false))
            : (root.uiConfirm
                ? root.uiConfirm({ title: '这张单暂时不能入账', message: message, confirmText: '去设置填汇率', cancelText: '先不处理' })
                : Promise.resolve(false));
        return done.then(function (ok) {
            if (ok && owner) {
                var btn = $('settingsNavBtn') || doc.querySelector('.sidebar-nav .sidebar-btn[data-target="tab-settings"]');
                if (btn) btn.click();
            }
        });
    };

    function gateTextsFrom(ret) {
        var list = (ret && ret.gate_issues) || (ret && ret.data && ret.data.gate_issues) || null;
        if (Array.isArray(list) && root.TERMS) return root.TERMS.formatGateIssues(list);
        return [];
    }

    /** 入账接口失败的统一处理。返回 true 表示已经处理（调用方不要再弹提示）。 */
    root.handleApproveError = function (httpStatus, ret, ctx) {
        ctx = ctx || {};
        var code = ret && ret.code;
        if (code === 'UNIT_CONVERSION_MISSING') {
            if (ctx.closeModal) ctx.closeModal();
            root.showUnitConversionMissing(ret);
            return true;
        }
        if (code === 'FX_RATE_MISSING') {
            if (ctx.closeModal) ctx.closeModal();
            root.showFxMissing(ret);
            return true;
        }
        if (code === 'APPROVAL_BLOCKED') {
            var reasons = gateTextsFrom(ret);
            var msg = reasons.length
                ? '这张单还不能入账：' + reasons.join('；')
                : ((root.TERMS && root.TERMS.humanizeCode(code, ret && ret.msg)) || '这张单还有必须改的地方');
            toast(msg, 'error', 8000);
            if (ctx.closeModal) ctx.closeModal();
            var id = ctx.receiptId;
            if (id && String(currentReceiptId) === String(id)) {
                if (typeof root.refreshReceiptIssueSummary === 'function') root.refreshReceiptIssueSummary();
                if (typeof root.focusNextReceiptIssue === 'function') root.focusNextReceiptIssue();
            } else if (id && typeof root.openReceiptInWorkbench === 'function') {
                root.openReceiptInWorkbench(id, true);
                root.setTimeout(function () {
                    if (typeof root.focusNextReceiptIssue === 'function') root.focusNextReceiptIssue();
                }, 900);
            }
            return true;
        }
        return false;
    };

    // ---------------------------------------------------------------------
    // 冲销（owner，已入账单）
    // ---------------------------------------------------------------------
    root.reverseReceipt = function (receiptId, version, done) {
        if (!isOwnerOrAbove()) {
            toast('冲销只有老板能做，请联系老板', 'warning');
            return Promise.resolve(false);
        }
        return askConfirm({
            title: '冲销这张已入账的单？',
            message: '冲销后，这张单会从支出里扣掉，已经增加的库存也会减回去。冲销之后不能再恢复，要重新入账需要重新录一张单。',
            confirmText: '继续冲销',
            cancelText: '先不冲销',
            danger: true
        }).then(function (ok) {
            if (!ok) return false;
            return askText({
                title: '冲销原因',
                label: '请写一句冲销原因（必填），方便以后查账',
                placeholder: '例如：单价抄错了，重新录',
                confirmText: '确认冲销',
                cancelText: '取消',
                validate: function (v) { return (String(v || '').trim().length < 2) ? '请写一句原因，至少 2 个字' : ''; }
            });
        }).then(function (reason) {
            if (!reason) return false;
            return root.apiFetch('/api/receipt/' + receiptId + '/reverse', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ version: version, reason: String(reason).trim() })
            }).then(function (res) {
                return Promise.all([res.status, res.json().catch(function () { return null; })]);
            }).then(function (pair) {
                var ret = pair[1];
                if (!ret || ret.status !== 'success') {
                    var human = root.humanizeBackendError ? root.humanizeBackendError(pair[0], ret) : null;
                    toast(human || (root.TERMS ? root.TERMS.humanizeCode(ret && ret.code, ret && ret.msg) : '冲销没有成功，请稍后再试'), 'error', 7000);
                    return false;
                }
                toast('已冲销单据 #' + receiptId + '，库存和支出已经调整', 'success');
                if (typeof root.loadReceiptsHistory === 'function') root.loadReceiptsHistory();
                if (typeof root.loadInventoryData === 'function') root.loadInventoryData();
                if (typeof root.loadWorkbenchQueue === 'function') root.loadWorkbenchQueue(true);
                if (typeof root.loadFinancePanel === 'function') root.loadFinancePanel();
                if (typeof done === 'function') done();
                return true;
            }).catch(function () {
                toast('网络不通，冲销没有成功，请检查网络后再试', 'error');
                return false;
            });
        });
    };

    // ---------------------------------------------------------------------
    // 标记有问题（可填原因）
    // ---------------------------------------------------------------------
    root.flagReceiptWithReason = function (receiptId, after) {
        return askText({
            title: '标记这张单有问题',
            label: '请写一句原因（可以不写）。标记后这张单不能入账，店员也不能再改。',
            placeholder: '例如：单价明显不对，要问供应商',
            confirmText: '标记有问题',
            cancelText: '取消',
            required: false
        }).then(function (reason) {
            if (reason === null || reason === undefined) return false;
            return root.apiFetch('/api/receipt/' + receiptId + '/flag', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason: String(reason).trim() })
            }).then(function (res) {
                return Promise.all([res.status, res.json().catch(function () { return null; })]);
            }).then(function (pair) {
                var ret = pair[1];
                if (!ret || ret.status !== 'success') {
                    var human = root.humanizeBackendError ? root.humanizeBackendError(pair[0], ret) : null;
                    toast(human || '标记没有成功，请稍后再试', 'error');
                    return false;
                }
                toast('已标记单据 #' + receiptId + ' 有问题', 'warning');
                if (typeof root.loadReceiptsHistory === 'function') root.loadReceiptsHistory();
                if (typeof root.loadWorkbenchQueue === 'function') root.loadWorkbenchQueue(true);
                if (typeof after === 'function') after();
                return true;
            }).catch(function () {
                toast('网络不通，标记没有成功，请稍后再试', 'error');
                return false;
            });
        });
    };

    // ---------------------------------------------------------------------
    // 放弃上传：确认框 + 撤销
    // ---------------------------------------------------------------------
    // 恢复接口只有老板能用（POST /api/trash/receipts/{id}/restore），店员放弃后不承诺能找回。
    root.discardCanUndo = function () { return isOwnerOrAbove(); };

    root.confirmDiscard = function (displayName) {
        var canUndo = root.discardCanUndo();
        return askConfirm({
            title: '不要这张单了？',
            message: '「' + displayName + '」会从待处理里拿走，不会入账。'
                + (canUndo ? '\n放弃后 10 秒内可以点「撤销」找回；之后老板也可以到回收站恢复。' : '\n放弃后店员没法自己找回，需要的话请老板帮忙恢复。'),
            confirmText: '放弃这张单',
            cancelText: '先留着',
            danger: true
        });
    };

    root.announceDiscarded = function (receiptId) {
        if (receiptId && root.discardCanUndo()) {
            toast('已放弃这张单', 'info', {
                duration: 10000,
                action: {
                    label: '撤销',
                    onClick: function () { root.undoDiscard(receiptId); }
                }
            });
        } else {
            toast('已放弃这张单', 'info');
        }
    };

    root.undoDiscard = function (receiptId) {
        return root.apiFetch('/api/trash/receipts/' + receiptId + '/restore', { method: 'POST' })
            .then(function (res) { return Promise.all([res.status, res.json().catch(function () { return null; })]); })
            .then(function (pair) {
                var ret = pair[1];
                if (!ret || ret.status !== 'success') {
                    var human = root.humanizeBackendError ? root.humanizeBackendError(pair[0], ret) : null;
                    toast(human || '没能撤销，请到回收站里恢复', 'error');
                    return false;
                }
                toast('已找回单据 #' + receiptId + '，在「当天收据处理队列」里可以看到', 'success');
                if (typeof root.loadWorkbenchQueue === 'function') root.loadWorkbenchQueue(true);
                if (typeof root.loadReceiptsHistory === 'function') root.loadReceiptsHistory();
                return true;
            })
            .catch(function () { toast('网络不通，没能撤销', 'error'); return false; });
    };

    // ---------------------------------------------------------------------
    // SKU 候选确认（B2：明细上的 sku_candidates）
    // ---------------------------------------------------------------------
    root.renderSkuCandidates = function (tr, item) {
        if (!tr) return;
        var box = tr.querySelector('.sku-candidate-box');
        if (!box) return;
        box.innerHTML = '';
        var cands = (item && Array.isArray(item.sku_candidates)) ? item.sku_candidates : [];
        var idInput = tr.querySelector('.inp-sku-id');
        if (!cands.length || (idInput && idInput.value) || (item && item.sku_id)) {
            box.classList.add('hide');
            return;
        }
        var c = cands[0];
        box.classList.remove('hide');
        box.innerHTML = '<div class="sku-cand-line"><span class="sku-cand-text">可能是：<strong data-i18n-skip>' + esc(c.name)
            + '</strong>' + (c.unit ? '<span class="sku-cand-unit" data-i18n-skip>（库存单位 ' + esc(c.unit) + '）</span>' : '') + '</span>'
            + '<span class="sku-cand-actions"><button type="button" class="btn btn-sm btn-primary sku-cand-yes">确认就是它</button>'
            + '<button type="button" class="btn btn-sm btn-secondary sku-cand-no">都不是，新建</button></span></div>'
            + (c.unit_compatible === false
                ? '<div class="sku-cand-warn">注意：这一行的单位和库存单位之间还没有换算关系，确认前请先核对。</div>' : '')
            + (c.reason ? '<div class="sku-cand-reason">' + esc(c.reason) + '</div>' : '');
        box.querySelector('.sku-cand-yes').addEventListener('click', function () {
            if (idInput) idInput.value = String(c.sku_id);
            var skuInput = tr.querySelector('.inp-sku');
            if (skuInput) skuInput.value = c.name || '';
            tr.dataset.manual = '1';
            tr.dataset.skuDecision = 'confirmed';
            box.innerHTML = '<div class="sku-cand-done">已确认对应库存食材「<span data-i18n-skip>' + esc(c.name) + '</span>」，保存后生效</div>';
            if (typeof root.markReviewDirty === 'function') root.markReviewDirty();
        });
        box.querySelector('.sku-cand-no').addEventListener('click', function () {
            tr.dataset.skuDecision = 'new';
            box.innerHTML = '<div class="sku-cand-done">不是已有食材，入账时会新建一个</div>';
        });
    };

    // ---------------------------------------------------------------------
    // 老板标记过「有问题」的单：店员不能保存，界面说明原因和下一步
    // ---------------------------------------------------------------------
    var FLAG_LOCK_TEXT = '这张单被老板标记了有问题，店员暂时不能修改。请把情况告诉老板，由老板处理后再继续。';

    root.syncFlaggedLock = function (data) {
        var locked = !!(data && data.status === 'flagged' && role() === 'staff');
        ['btnSaveReview', 'btnSaveAndNext', 'btnSaveArchive'].forEach(function (id) {
            var b = $(id);
            if (!b) return;
            b.disabled = locked;
            if (locked) b.title = FLAG_LOCK_TEXT; else if (b.title === FLAG_LOCK_TEXT) b.title = '';
        });
        var banner = $('flaggedLockBanner');
        if (locked) {
            if (!banner) {
                banner = doc.createElement('div');
                banner.id = 'flaggedLockBanner';
                banner.className = 'alert-banner';
                banner.setAttribute('role', 'alert');
                banner.style.cssText = 'border-color:#b91c1c;background:#fef2f2;color:#7f1d1d;margin-bottom:12px;';
                var anchor = $('issueSummaryBar');
                if (anchor && anchor.parentNode) anchor.parentNode.insertBefore(banner, anchor);
            }
            banner.textContent = FLAG_LOCK_TEXT + (data.flag_reason ? '老板写的原因：' + data.flag_reason : '');
            banner.classList.remove('hide');
        } else if (banner) {
            banner.classList.add('hide');
        }
    };

    // 归档详情里：已入账单给老板显示「冲销」
    root.syncArchiveActionButtons = function (data) {
        var btn = $('modalReverseBtn');
        if (btn) {
            var show = !!(data && data.status === 'approved' && isOwnerOrAbove());
            btn.classList.toggle('hide', !show);
            btn.style.display = show ? '' : 'none';
        }
        var unflag = $('modalUnflagBtn');
        if (unflag) {
            var showU = !!(data && data.status === 'flagged' && isOwnerOrAbove());
            unflag.classList.toggle('hide', !showU);
            unflag.style.display = showU ? '' : 'none';
        }
        var flagBtn = $('modalFlagBtn');
        if (flagBtn && isOwnerOrAbove()) {
            var noFlag = !!(data && (data.status === 'flagged' || data.status === 'approved' || data.status === 'reversed'));
            flagBtn.classList.toggle('hide', noFlag);
            flagBtn.style.display = noFlag ? 'none' : '';
        }
        var approve = $('modalApproveBtn');
        if (approve && isOwnerOrAbove()) {
            var done = !!(data && (data.status === 'approved' || data.status === 'flagged' || data.status === 'reversed'));
            approve.classList.toggle('hide', done);
            approve.style.display = done ? 'none' : '';
        }
        root.syncFlaggedLock(data);
    };

    root.modalUnflagReceipt = function () {
        var id = currentArchiveReceiptId;
        if (!id) return;
        askConfirm({
            title: '解除异常标记？',
            message: '解除后，这张单可以继续修改，确认没问题后就能审核入账。',
            confirmText: '解除标记',
            cancelText: '先不解除'
        }).then(function (ok) {
            if (!ok) return;
            return root.apiFetch('/api/receipt/' + id + '/unflag', {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({})
            }).then(function (res) { return Promise.all([res.status, res.json().catch(function () { return null; })]); })
              .then(function (pair) {
                var ret = pair[1];
                if (!ret || ret.status !== 'success') {
                    toast('没能解除标记：' + root.TERMS.humanizeCode(ret && ret.code, ret && ret.msg), 'error', 7000);
                    return;
                }
                toast('已解除单据 #' + id + ' 的异常标记', 'success');
                if (typeof root.loadReceiptsHistory === 'function') root.loadReceiptsHistory();
                if (typeof root.loadWorkbenchQueue === 'function') root.loadWorkbenchQueue(true);
                if (typeof root.loadReceiptDetail === 'function') root.loadReceiptDetail(id);
              });
        });
    };

    root.modalReverseReceipt = function () {
        var id = currentArchiveReceiptId;
        var data = currentArchiveDetailData;
        if (!id) return;
        root.reverseReceipt(id, data ? data.version : null, function () {
            if (typeof root.closeArchiveModal === 'function') root.closeArchiveModal(true);
        });
    };

    // ---------------------------------------------------------------------
    // 进入复核后自动滚到表单（手机上表单在页面很下面；桌面上表单已在视野内就不动）
    // ---------------------------------------------------------------------
    root.scrollReviewIntoView = function () {
        var card = $('prefillFormCard');
        if (!card || card.classList.contains('hide')) return;
        root.setTimeout(function () {
            var top = card.getBoundingClientRect().top;
            var mobile = root.matchMedia && root.matchMedia('(max-width: 640px)').matches;
            if ((mobile || top > root.innerHeight * 0.7 || top < -40) && card.scrollIntoView) {
                card.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }, 80);
    };

    function boot() {
        initMobileNav();
    }
    if (doc.readyState === 'loading') doc.addEventListener('DOMContentLoaded', boot);
    else boot();
})(window);
