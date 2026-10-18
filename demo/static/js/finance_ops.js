// demo/static/js/finance_ops.js
// Wave 2 财务闭环（T7/M5-M8，D15/D18/D20）：应付清单面板与筛选、登记付款、对账任务、
        // 发起对账、导入账单、对账差异明细、盘点调整、统一出库（消耗/损耗）。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// Wave 1：应付清单行内操作与提醒入口（index.html 内联 onclick 引用）
window.gotoPayablesList = gotoPayablesList;
window.savePayDate = savePayDate;
window.openPaymentModalForReceipt = openPaymentModalForReceipt;
window.applyPayablesFilters = applyPayablesFilters;

// ==========================================================================
// Wave 2 财务闭环（T7/M5-M8，D15/D18/D20）
//   - CSV 导出（归档筛选参数透传）
//   - 支付登记 + 未付挂账单/账期/逾期看板
//   - 月结对账引擎前端（任务列表 / 差异明细 / 处理 / 确认锁定）
//   - 供应商合并（D18）
//   - 盘点调整（D15）
// ==========================================================================

// ---- 小工具 ----（w2Escape/jsStr 已移至文件头部并立为硬规则，见文件开头注释）

function closeModalById(id) {
    const el = document.getElementById(id);
    if (el) {
        if (typeof closeAccessibleModal === 'function') closeAccessibleModal(el);
        else el.classList.add('hide');
        el.setAttribute('aria-hidden', 'true');
    }
}
window.closeModalById = closeModalById;

function openModalById(id) {
    const el = document.getElementById(id);
    if (el) {
        el.removeAttribute('aria-hidden');
        if (typeof openAccessibleModal === 'function') openAccessibleModal(el);
        else el.classList.remove('hide');
    }
}
window.openModalById = openModalById;

function uiText(s) {
    const raw = String(s == null ? '' : s);
    try {
        if (window.I18N && typeof I18N.getLocale === 'function' && I18N.getLocale() !== 'zh-Hans' && typeof I18N.convert === 'function') {
            return I18N.convert(raw);
        }
    } catch (e) {}
    return raw;
}

function printLocaleTag() {
    try {
        const loc = (window.I18N && typeof I18N.getLocale === 'function') ? I18N.getLocale() : 'zh-Hans';
        if (loc === 'zh-Hant-HK' || loc === 'zh-Hant') return 'zh-Hant-HK';
        return 'zh-Hans';
    } catch (e) {
        return 'zh-Hans';
    }
}

// ---- 支付与对账面板（Wave 1 升级：跨供应商应付清单，03 章） ----
const finState = {
    suppliers: [],
    supplierId: null,      // null = 全部供应商（全局视图，可按供应商收敛）
    receipts: [],          // 全量单据（/api/receipts，含契约①付款域派生字段）
    currentTaskId: null,
    currentDetail: null,
    resolveCtx: null,
};

const RECON_STATUS_BADGES = {
    unreconciled: ['badge-secondary', '未对账'],
    reconciling: ['badge-warning', '对账中'],
    disputed: ['badge-danger', '争议中'],
    confirmed: ['badge-success', '已确认'],
};
const MATCH_BADGES = {
    matched: ['badge-success', '已对上'],
    pending: ['badge-warning', '待确认'],
    amount_mismatch: ['badge-danger', '金额对不上'],
    supplier_only: ['badge-danger', '账单有，我们没有单'],
    restaurant_only: ['badge-warning', '我们有单，账单没有'],
};

// Wave 1：付款方式展示口径（契约① paid_method 四态；未付显示 —）
const PAY_METHOD_LABELS = {
    cash: ' 现金',
    bank_transfer: ' 银行转账',
    cheque: ' 支票',
    fps: ' FPS',
};

// owner 判定：AUTH_ENABLED=0 时 /api/auth/me 返回 owner；仅明确 staff 时隐藏资金类行内操作
function isOwnerRole() {
    // C3：与 isOwnerRoleNow 收敛为单一事实来源链——优先 /me 回填的服务端确认角色，
    // 未回填时回落 localStorage demo_role（X-Role 同源），消除双来源漂移。
    const role = AuthState.account && AuthState.account.role;
    if (role) return role !== 'staff';
    return isOwnerRoleNow();
}

// owner 判定（同步可用，与 buildAuthHeaders/X-Role 同源：localStorage demo_role，
// 不依赖异步 /me 回填）。动态渲染的「停用/删除」「冲销作废」按钮用它判定，
// 避免 staff 首帧在 AuthState.account 回填前误渲染出 owner 按钮。
function isOwnerRoleNow() {
    try {
        return localStorage.getItem('demo_role') !== 'staff';
    } catch (e) { return true; }
}

// ISO 日期（YYYY-MM-DD）距今天数：正数=未来，负数=已过；非法/空 → null
function daysFromToday(dateStr) {
    const m = String(dateStr || '').match(/^(\d{4})-(\d{2})-(\d{2})$/);
    if (!m) return null;
    const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
    const t = new Date();
    const today = new Date(t.getFullYear(), t.getMonth(), t.getDate());
    return Math.round((d - today) / 86400000);
}

// 应付清单行域（与 compute_supplier_payables 债务口径对齐）：
// 现金单/挂账单；月结账单 statement 是下属挂账单汇总单避免重复计债；
// 仅 parsed/edited/approved 有效态（flagged 已隔离、识别中/失败不入债务）。
function isPayableRow(r) {
    if (!r) return false;
    const st = r.settlement_type;
    if (st !== 'credit' && st !== 'cash') return false;
    if (r.doc_form === 'statement') return false;
    const s = r.status || '';
    // 应付只算已审批入账的单据（与供应商表「已入账未付挂账单」同口径）；没审批的挂账单入账后才计入
    return s === 'approved';
}

// 7 天内到期：后端的 due_soon 字段目前恒为 false，前端按预期付款日自己算（未付清、没逾期、0 到 7 天内）
function payIsDueSoon(r) {
    if (!r) return false;
    if (r.due_soon === true) return true;
    if (typeof payOutstandingAmount === 'function' && payOutstandingAmount(r) <= 0) return false;
    if (r.is_overdue === true || r.payment_status === 'overdue') return false;
    const n = daysFromToday(r.expected_pay_date || r.due_date);
    return n !== null && n >= 0 && n <= 7;
}
window.payIsDueSoon = payIsDueSoon;

// Wave 1 付款状态徽章（契约①五态）；未知值兜底过 w2Escape（P0 硬规则）
function payStatusBadgeHtml(r) {
    const st = r && r.payment_status;
    if (st === 'paid_at_delivery') return '<span class="badge badge-success">现结已付</span>';
    if (st === 'paid') return '<span class="badge badge-success">已付清</span>';
    if (st === 'partial') {
        return '<span class="badge badge-warning">部分已付</span>' + (r.is_overdue === true ? ' <span class="badge badge-danger">已逾期</span>' : '');
    }
    if (st === 'overdue') {
        const n = daysFromToday(r.expected_pay_date);
        const daysTxt = (n !== null && n < 0) ? ' ' + (-n) + '天' : '';
        return '<span class="badge badge-danger">逾期' + daysTxt + '</span>';
    }
    if (st === 'unpaid') {
        let badge = '<span class="badge badge-warning">未付</span>';
        if (payIsDueSoon(r)) {
            const n = daysFromToday(r.expected_pay_date);
            const daysTxt = (n !== null) ? n + '天到期' : '快到期';
            badge += '<div style="font-size:0.72rem; color:#d97706; margin-top:2px;">' + daysTxt + '</div>';
        }
        return badge;
    }
    if (st === 'unknown') return '<span class="badge badge-secondary">未识别</span>';
    return '<span class="badge badge-secondary">' + w2Escape(st ?? '—') + '</span>';
}

// 归档列表轻量付款徽章（03 章六：只展示不编辑）；
// 未进入付款域的单据（结算未识别/缺失）显示弱化 —，避免满屏噪音
function payStatusLiteBadgeHtml(r) {
    const st = r && r.payment_status;
    if (st === 'paid_at_delivery' || st === 'paid' || st === 'unpaid' || st === 'overdue' || st === 'partial') {
        return payStatusBadgeHtml(r);
    }
    return '<span style="color:var(--text-muted); font-size:0.8rem;">—</span>';
}

// Wave 1 提醒统计（02 章：派生不存储；红点计数 = 逾期 + 快到期 单据数）
function computePayStats(receipts) {
    let unpaidTotal = 0, unpaidCount = 0, overdueCount = 0, dueSoonCount = 0;
    (receipts || []).forEach(r => {
        const st = r.payment_status;
        if (st === 'unpaid' || st === 'overdue' || st === 'partial') {
            unpaidTotal += (typeof payOutstandingAmount === 'function') ? payOutstandingAmount(r) : Number(r.total_amount || 0);
            unpaidCount += 1;
        }
        if (st === 'overdue' || r.is_overdue === true) overdueCount += 1;
        if (payIsDueSoon(r)) dueSoonCount += 1;
    });
    return { unpaidTotal, unpaidCount, overdueCount, dueSoonCount };
}

function loadFinancePanel() {
    // 店员不发财务域请求（payments/reconciliation 为 owner 域），红点/横幅提醒属老板视角
    if (isStaffRoleNow()) return;
    // 全局视图：供应商 + 应付清单 + 全量单据并拉（契约①③）
    Promise.all([
        apiFetch('/api/suppliers').then(res => res.json()),
        apiFetch('/api/payables').then(res => res.json()),
        fetchReceiptListShared(false),
    ])
        .then(([supRet, payRet, rcRet]) => {
            if (supRet && supRet.status === 'success') finState.suppliers = supRet.data || [];
            if (rcRet && rcRet.status === 'success') finState.receipts = rcRet.data || [];
            if (payRet && payRet.status === 'success' && payRet.data) {
                finState.payables = payRet.data.rows || [];
            } else {
                finState.payables = [];
            }
            // 用权威 /api/payables 数据丰富 finState.receipts 中的应付字段
            const payMap = {};
            (finState.payables || []).forEach(p => {
                const rid = Number(p.receipt_id || p.id);
                if (rid) payMap[rid] = p;
            });
            (finState.receipts || []).forEach(r => {
                const p = payMap[Number(r.id)];
                if (p) {
                    r.total_hkd = p.total_hkd;
                    r.paid_amount = p.paid_amount;
                    r.outstanding_amount = p.outstanding_amount;
                    r.payment_status = p.payment_status;
                    r.is_overdue = p.is_overdue;
                    r.fx_missing = p.fx_missing;
                    r.has_fx_snapshot = p.has_fx_snapshot;
                    if (p.due_date) r.due_date = p.due_date;
                }
            });

            const sel = document.getElementById('finSupplier');
            if (!sel) return;
            const prev = sel.value || (finState.supplierId ? String(finState.supplierId) : '');
            sel.innerHTML = '<option value="">全部供应商</option>';
            finState.suppliers.forEach(s => {
                const opt = document.createElement('option');
                opt.value = String(s.id);
                const overdue = s.is_overdue ? ' 逾期' : '';
                opt.textContent = `${s.name} [${s.supplier_code || '-'}]${overdue}`;
                sel.appendChild(opt);
            });
            if (prev && finState.suppliers.some(s => String(s.id) === prev)) sel.value = prev;
            else sel.value = '';
            finState.supplierId = sel.value ? Number(sel.value) : null;

            renderPayablesAll();
        })
        .catch(err => { if (!window.__roleSwitchPending) console.error('加载财务面板失败:', err); });
}

function renderPayablesAll() {
    renderPayablesSummary();
    applyPayablesFilters();
    updatePaymentReminders();
    loadPaymentHistory();
    loadReconTasks();
}

// 顶部汇总：未付总额 / 逾期数 / 7 天内到期数（选中供应商时补供应商口径）
function renderPayablesSummary() {
    const box = document.getElementById('financeSummary');
    if (!box) return;
    const stats = computePayStats((finState.receipts || []).filter(isPayableRow));
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;

    const notPosted = (finState.receipts || []).filter(r => r.settlement_type === 'credit' && !r.deleted_at
        && r.doc_form !== 'statement' && (r.status === 'edited' || r.status === 'parsed' || r.status === 'parsed_with_warnings'));
    let html = `
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center;">
            <span style="font-size:1.02rem;">已入账未付总额：
                <strong style="color:${stats.unpaidTotal > 0 ? '#b45309' : '#047857'};">
                    ${I18N.fmtMoney(stats.unpaidTotal)}</strong>
                （${stats.unpaidCount} 张）</span>
            <span class="badge ${stats.overdueCount ? 'badge-danger' : 'badge-success'}">逾期 ${stats.overdueCount} 张</span>
            <span class="badge ${stats.dueSoonCount ? 'badge-warning' : 'badge-success'}">7 天内到期 ${stats.dueSoonCount} 张</span>
        </div>
        <div style="font-size:0.88rem; color:var(--text-muted); margin-top:6px;">这里和上面「供应商」表的口径一样：只算已审批入账的挂账单。${notPosted.length ? '另有 ' + notPosted.length + ' 张挂账单还没审批，审批入账后才会算进来。' : ''}</div>`;
    if (sup) {
        const overdueBadge = sup.is_overdue
            ? `<span class="badge badge-danger">该供应商存在逾期</span>`
            : '<span class="badge badge-success">该供应商账期内</span>';
        const termsTxt = (sup.payment_terms_days != null)
            ? w2Escape(String(sup.payment_terms_days)) + ' 天' : '未设置';
        html += `
        <div style="display:flex; flex-wrap:wrap; gap:10px; align-items:center; margin-top:8px;">
            <span class="badge badge-secondary">当前筛选：${w2Escape(sup.name)}</span>
            <span class="badge badge-secondary">账期 ${termsTxt}</span>
            <span style="font-size:0.92rem;">已入账未付挂账单：
                <strong style="color:${sup.unpaid_credit_total > 0 ? '#b45309' : '#047857'};">
                    ${I18N.fmtMoney(sup.unpaid_credit_total)}</strong>
                （${sup.unpaid_credit_count} 张）</span>
            ${overdueBadge}
        </div>`;
    }
    box.innerHTML = html;
}

// 筛选：供应商（收敛）/ 已付未付 / 付款方式 / 逾期·快到期·未设日期（03 章三）
function getFilteredPayables() {
    const fltPay = document.getElementById('fltFinPay')?.value || '';
    const fltMethod = document.getElementById('fltFinMethod')?.value || '';
    const fltDue = document.getElementById('fltFinDue')?.value || '';

    let rows = (finState.receipts || []).filter(isPayableRow);
    if (finState.supplierId) {
        const sup = finState.suppliers.find(s => s.id === finState.supplierId);
        if (sup) rows = rows.filter(r => r.supplier_name === sup.name);
    }
    if (fltPay === 'unpaid') {
        rows = rows.filter(r => r.payment_status === 'unpaid' || r.payment_status === 'overdue' || r.payment_status === 'partial');
    } else if (fltPay === 'paid') {
        rows = rows.filter(r => r.payment_status === 'paid' || r.payment_status === 'paid_at_delivery');
    }
    if (fltMethod) rows = rows.filter(r => r.paid_method === fltMethod);
    if (fltDue === 'overdue') rows = rows.filter(r => r.payment_status === 'overdue' || r.is_overdue === true);
    else if (fltDue === 'due_soon') rows = rows.filter(r => payIsDueSoon(r));
    else if (fltDue === 'no_date') rows = rows.filter(r => r.payment_status === 'unpaid' && !r.expected_pay_date);

    // 默认排序（03 章二）：未付在前、expected_pay_date 升序（最急在前）、无日期沉底；
    // 已付行沉底按支付时点倒序（现结无支付记录回退开单日期）
    rows.sort((a, b) => {
        const aPaid = a.payment_status === 'paid' || a.payment_status === 'paid_at_delivery';
        const bPaid = b.payment_status === 'paid' || b.payment_status === 'paid_at_delivery';
        if (aPaid !== bPaid) return aPaid ? 1 : -1;
        if (!aPaid) {
            const ad = a.expected_pay_date || '';
            const bd = b.expected_pay_date || '';
            if (ad && bd && ad !== bd) return ad < bd ? -1 : 1;
            if (ad && !bd) return -1;
            if (!ad && bd) return 1;
            return (b.id || 0) - (a.id || 0);
        }
        const at = a.paid_at || a.receipt_date || '';
        const bt = b.paid_at || b.receipt_date || '';
        if (at !== bt) return at < bt ? 1 : -1;
        return (b.id || 0) - (a.id || 0);
    });
    return rows;
}

function applyPayablesFilters() {
    renderPayablesTable(getFilteredPayables());
}

function renderPayablesTable(rows) {
    const body = document.getElementById('payablesBody');
    if (!body) return;
    body.innerHTML = '';
    if (!rows.length) {
        body.innerHTML = '<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:28px;">当前筛选条件下没有应付单据</td></tr>';
        return;
    }
    const owner = isOwnerRole();
    rows.forEach(r => {
        const rid = Number(r.id);
        const tr = document.createElement('tr');
        // 逾期行整行标红、快到期行高亮（02 章三）
        if (r.payment_status === 'overdue' || r.is_overdue === true) tr.className = 'payable-row-overdue';
        else if (payIsDueSoon(r)) tr.className = 'payable-row-due-soon';

        const supCode = r.supplier_code
            ? ' <span class="badge badge-secondary" style="font-size:0.72rem; font-family:monospace; opacity:0.85;">' + w2Escape(r.supplier_code) + '</span>'
            : '';

        // 预期付款日：未付挂账单行内日期可编辑（契约④，owner 专属）；其余只读
        let dateCell;
        const editable = owner && r.settlement_type === 'credit' && !r.payment_id &&
            (r.payment_status === 'unpaid' || r.payment_status === 'overdue' || r.payment_status === 'partial');
        if (editable) {
            dateCell = '<input type="date" class="form-control" style="font-size:0.8rem; padding:3px 6px; width:100%; max-width:140px; box-sizing:border-box;" ' +
                'value="' + w2Escape(r.expected_pay_date || '') + '" ' +
                'title="设置预期付款日；清空即取消日期" ' +
                'onchange="savePayDate(' + rid + ', this.value)">';
        } else {
            dateCell = renderDateCell(r.expected_pay_date);
        }
        if ((r.payment_status === 'paid' || r.payment_status === 'paid_at_delivery') && r.paid_at) {
            dateCell += '<div style="font-size:0.72rem; color:var(--text-muted);">支付于 ' + renderDateCell(r.paid_at) + '</div>';
        }

        const methodCell = r.paid_method
            ? w2Escape(PAY_METHOD_LABELS[r.paid_method] || r.paid_method)
            : '<span style="color:var(--text-muted);">—</span>';

        // 操作：未付挂账单可登记支付（沿用既有护栏：现结单/账单不可挂）
        let actionHtml;
        if (r.settlement_type === 'credit' && payOutstandingAmount(r) > 0) {
            if (r.currency && r.currency !== 'HKD' && (r.fx_missing || r.total_hkd == null)) {
                actionHtml = '<button class="btn btn-secondary" style="min-height:40px; padding:4px 12px; font-size:0.85rem;" disabled title="汇率未知，无法登记支付"> 汇率未知</button>';
            } else {
                actionHtml = '<button class="btn btn-primary" style="min-height:40px; padding:4px 12px; font-size:0.85rem;" ' +
                    'onclick="openPaymentModalForReceipt(' + rid + ')"> 登记支付</button>';
            }
        } else {
            actionHtml = '<span style="color:var(--text-muted); font-size:0.78rem;">—</span>';
        }

        let amountHtml = '';
        const isForeign = r.currency && r.currency !== 'HKD';
        const outAmt = payOutstandingAmount(r);
        const paidAmt = payPaidAmount(r);

        if (isForeign) {
            const origStr = typeof formatCurrency === 'function' ? formatCurrency(r.total_amount, r.currency) : `${r.currency} ${r.total_amount}`;
            if (r.fx_missing || r.total_hkd == null) {
                // 缺 snapshot legacy 提示未知，不硬标 HKD
                amountHtml = `<strong style="color:var(--text-muted);">未知</strong> <span class="badge badge-warning" style="font-size:0.72rem;">汇率未知</span>` +
                    `<div style="font-size:0.78rem; color:var(--text-muted);">原币 ${w2Escape(origStr)}</div>`;
            } else {
                // 外币已折算港币：主金额显示折算后的港币，并显式展示原币
                amountHtml = `<strong>${I18N.fmtMoney(r.total_hkd)}</strong>` +
                    `<div style="font-size:0.78rem; color:var(--text-muted);">原币 ${w2Escape(origStr)}</div>`;
                if (paidAmt > 0 && outAmt > 0) {
                    amountHtml += `<div style="font-size:0.78rem; color:var(--text-muted);">已付 ${I18N.fmtMoney(paidAmt)}，待付 ${I18N.fmtMoney(outAmt)}</div>`;
                }
            }
        } else {
            amountHtml = `<strong>${I18N.fmtMoney(r.total_amount)}</strong>`;
            if (paidAmt > 0 && outAmt > 0) {
                amountHtml += `<div style="font-size:0.78rem; color:var(--text-muted);">已付 ${I18N.fmtMoney(paidAmt)}，待付 ${I18N.fmtMoney(outAmt)}</div>`;
            }
        }

        tr.innerHTML = `
            <td style="vertical-align:middle; word-break:break-all; white-space:normal; text-align:center;">#${rid}<br>
                <strong>${w2Escape(r.supplier_name || '-')}</strong>${supCode}</td>
            <td style="vertical-align:middle;">${renderDateCell(r.receipt_date)}</td>
            <td style="vertical-align:middle;">${amountHtml}</td>
            <td style="vertical-align:middle;">${payStatusBadgeHtml(r)}</td>
            <td style="vertical-align:middle;">${dateCell}</td>
            <td style="vertical-align:middle;">${methodCell}</td>
            <td style="vertical-align:middle; white-space:nowrap;">${actionHtml}</td>`;
        body.appendChild(tr);
    });
}

// Wave 1 提醒（02 章）：导航红点 + 顶部横幅；逾期+快到期为 0 时全部隐藏
function updatePaymentReminders() {
    const stats = computePayStats((finState.receipts || []).filter(isPayableRow));
    const count = stats.overdueCount + stats.dueSoonCount;
    const dot = document.getElementById('navPayRedDot');
    if (dot) {
        dot.textContent = count > 99 ? '99+' : String(count);
        dot.classList.toggle('hide', count === 0);
    }
    const arcDot = document.getElementById('arcPayCount');
    if (arcDot) {
        arcDot.textContent = count > 99 ? '99+' : String(count);
        arcDot.classList.toggle('hide', count === 0);
    }
    const banner = document.getElementById('payReminderBanner');
    if (banner) {
        if (count === 0) {
            banner.classList.add('hide');
            banner.innerHTML = '';
        } else {
            const parts = [];
            if (stats.overdueCount > 0) parts.push(w2Escape(String(stats.overdueCount)) + ' 张挂账单已逾期');
            if (stats.dueSoonCount > 0) parts.push(w2Escape(String(stats.dueSoonCount)) + ' 张 7 天内到期');
            banner.innerHTML = '你有 <strong>' + parts.join('、') + '</strong>，点击查看应付清单 →';
            banner.classList.remove('hide');
        }
    }
}

// 横幅/外部入口点击 → 切到归档 Tab 并滚动到应付清单
function gotoPayablesList() {
    const btn = document.querySelector('.sidebar-btn[data-target="tab-archive"]');
    if (btn && btn.click) btn.click();
    if (typeof showArchivePane === 'function') showArchivePane('payables');
    const card = document.getElementById('payablesCard');
    if (card && card.scrollIntoView) {
        setTimeout(() => card.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    }
}

// 行内设预期付款日（契约④）：空值 = 清空；成功后全量刷新状态徽章与提醒
function savePayDate(receiptId, dateStr) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    const value = String(dateStr || '').trim();
    apiFetch(`/api/receipt/${rid}/pay_date`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ expected_pay_date: value || null }),
    })
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') {
                showToast((ret && ret.msg) || '设置预期付款日失败', 'error');
                loadFinancePanel();   // 回滚输入框显示，与服务端对齐
                return;
            }
            showToast(value
                ? `单据 #${rid} 预期付款日已设为 ${value}`
                : `已清空单据 #${rid} 的预期付款日`, 'success');
            loadFinancePanel();       // 重算付款状态徽章 / 汇总 / 提醒
            loadReceiptsHistory();    // 归档列表付款状态徽章联动
        })
        .catch(err => { console.error('设置预期付款日异常', err); showToast('设置预期付款日失败' + toastFailDetail(err), 'error'); });
}

// 应付清单行级「登记支付」：定位到该单供应商后复用既有 Modal 与护栏
function openPaymentModalForReceipt(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    const r = (finState.receipts || []).find(x => Number(x.id) === rid);
    if (!r) { showToast('未找到该单据，请刷新后重试', 'warning'); return; }
    if (r.settlement_type !== 'credit' || r.status !== 'approved' || payOutstandingAmount(r) <= 0) {
        showToast('只有已审批入账、还没付清的挂账单可以登记支付。', 'warning');
        return;
    }
    const sup = (finState.suppliers || []).find(s => s.name === r.supplier_name);
    if (!sup) { showToast('未找到该单据对应的供应商，请先在上方下拉选择供应商', 'warning'); return; }
    const sel = document.getElementById('finSupplier');
    if (sel) sel.value = String(sup.id);
    finState.supplierId = sup.id;
    openPaymentModal();
}

// 支付履历：选中供应商 → 该供应商；未选 → 全部
function loadPaymentHistory() {
    const payArea = document.getElementById('paymentHistoryArea');
    const body = document.getElementById('paymentHistoryBody');
    if (!payArea || !body) return;
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;
    const url = sup ? `/api/payments?supplier_id=${sup.id}` : '/api/payments';
    apiFetch(url)
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            const pays = ret.data || [];
            body.innerHTML = '';
            if (!pays.length) {
                payArea.classList.add('hide');
                return;
            }
            const title = document.getElementById('paymentHistoryTitle');
            if (title) title.textContent = '支付履历';
            payArea.classList.remove('hide');
            pays.forEach(p => {
                const voucher = p.voucher_image_url
                    ? `<a href="${w2Escape(p.voucher_image_url)}" target="_blank">查看</a>` : '-';
                const linked = Array.isArray(p.linked_receipt_ids) ? p.linked_receipt_ids : [];
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>#${Number(p.id)}</td>
                    <td>${w2Escape(p.supplier_name || '-')}</td>
                    <td>${renderDateCell(p.paid_at)}</td>
                    <td><strong>${I18N.fmtMoney(p.amount)}</strong></td>
                    <td>${w2Escape(p.method_label)}</td>
                    <td>${Number(p.linked_receipt_count) || 0} 张${linked.length
                        ? '（' + linked.map(x => '#' + Number(x)).join('、') + '）' : ''}</td>
                    <td>${voucher}</td>
                    <td>${w2Escape(p.notes || '-')}</td>`;
                body.appendChild(tr);
            });
        });
}

// 对账任务列表：选中供应商 → 该供应商；未选 → 全部
function loadReconTasks() {
    const sup = finState.supplierId
        ? finState.suppliers.find(s => s.id === finState.supplierId) : null;
    const url = sup ? `/api/reconciliation?supplier_id=${sup.id}` : '/api/reconciliation';
    apiFetch(url)
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') return;
            const title = document.getElementById('reconTaskTitle');
            if (title) title.textContent = '对账任务';
            renderReconTasks(ret.data || []);
        });
}

function renderReconTasks(tasks) {
    const area = document.getElementById('reconTaskArea');
    const body = document.getElementById('reconTaskBody');
    body.innerHTML = '';
    if (!tasks.length) {
        area.classList.add('hide');
        document.getElementById('reconDetailArea')?.classList.add('hide');
        return;
    }
    area.classList.remove('hide');
    tasks.forEach(t => {
        // 兜底 label 过 w2Escape（状态值理论上受控，防御纵深；P0 硬规则）
        const [cls, label] = RECON_STATUS_BADGES[t.status] || ['badge-secondary', w2Escape(t.status)];
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>#${w2Escape(String(t.id))}</td>
            <td>${w2Escape(t.supplier_name || '-')}</td>
            <td>${t.period_start || t.period_end ? w2Escape(t.period_start || '开头') + ' ~ ' + w2Escape(t.period_end || '今天') : '没限定期间'}</td>
            <td>${w2Escape(t.period_type_label)}</td>
            <td><span class="badge ${cls}">${label}</span></td>
            <td>${t.summary.total_lines}</td>
            <td style="color:#047857; font-weight:600;">${t.summary.matched}</td>
            <td style="color:${t.summary.open_issues ? '#b45309' : '#047857'}; font-weight:600;">${t.summary.open_issues}</td>
            <td><button class="btn btn-secondary" style="min-height:40px; padding:4px 14px; font-size:0.9rem;"
                onclick='viewReconTask(${jsStr(t.id)})'> 查看详情</button></td>`;
        body.appendChild(tr);
    });
}

// ---- 登记支付 Modal：见 finance.js ----

// ---- 发起对账 Modal ----
// UAT-017：经 openAccessibleModal 注册 dialog 语义 / 焦点陷阱 / Esc 归还触发焦点
function openReconModal() {
    if (!finState.supplierId) { showToast('请先选择供应商', 'warning'); return; }
    const sup = finState.suppliers.find(s => s.id === finState.supplierId);
    const hint = document.getElementById('reconSupplierHint');
    if (hint) hint.innerText = `供应商：${sup ? sup.name : ('#' + finState.supplierId)}`;
    const period = document.getElementById('reconPeriodType');
    if (period) period.value = 'month';
    const start = document.getElementById('reconStart');
    if (start) start.value = '';
    const end = document.getElementById('reconEnd');
    if (end) end.value = '';
    const modal = document.getElementById('reconCreateModal');
    if (!modal) return;
    // 保持 HTML 上的 aria-labelledby / aria-describedby；仅补全运行时 dialog 注册
    if (!modal.getAttribute('aria-labelledby')) {
        modal.setAttribute('aria-labelledby', 'reconCreateTitle');
    }
    if (!modal.getAttribute('aria-describedby')) {
        modal.setAttribute('aria-describedby', 'reconSupplierHint');
    }
    const trigger = document.activeElement;
    if (typeof openAccessibleModal === 'function') {
        openAccessibleModal(modal, '#reconPeriodType', trigger);
    } else {
        openModalById('reconCreateModal');
    }
}

function reconPeriodTypeChanged() {
    // 周期变化时清空手工日期，避免残留误导（留空即走缺省）
    document.getElementById('reconStart').value = '';
    document.getElementById('reconEnd').value = '';
}

function submitReconCreate() {
    const payload = {
        supplier_id: finState.supplierId,
        period_type: document.getElementById('reconPeriodType').value,
    };
    const start = document.getElementById('reconStart').value;
    const end = document.getElementById('reconEnd').value;
    if (start) payload.start = start;
    if (end) payload.end = end;

    apiFetch('/api/reconciliation', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('发起对账失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 发起对账成功', ret.msg);
            showToast('已发起对账', 'success', TOAST_DURATION.long);
            closeModalById('reconCreateModal');
            const taskId = ret.task_id;
            loadFinancePanel();
            setTimeout(() => viewReconTask(taskId), 400);
        })
        .catch(err => { console.error('发起对账异常', err); showToast('发起对账失败' + toastFailDetail(err), 'error'); });
}

function _selectedFinanceSupplierId() {
    const sel = document.getElementById('finSupplier');
    if (sel && sel.value) finState.supplierId = Number(sel.value);
    return finState.supplierId || null;
}

function addImportStatementLine() {
    const body = document.getElementById('importStatementLines');
    if (!body) return;
    const tr = document.createElement('tr');
    tr.className = 'import-statement-line';
    const date = document.createElement('input');
    date.type = 'date';
    date.className = 'form-control import-line-date';
    date.setAttribute('aria-label', '日期');
    date.style.minHeight = '40px';
    const no = document.createElement('input');
    no.type = 'text';
    no.className = 'form-control import-line-no';
    no.setAttribute('aria-label', '单号');
    no.maxLength = 80;
    no.style.minHeight = '40px';
    const amount = document.createElement('input');
    amount.type = 'number';
    amount.className = 'form-control import-line-amount';
    amount.setAttribute('aria-label', '金额');
    amount.step = '0.01';
    amount.style.minHeight = '40px';
    [date, no, amount].forEach(input => {
        const td = document.createElement('td');
        td.appendChild(input);
        tr.appendChild(td);
    });
    body.appendChild(tr);
}

function openImportStatementModal() {
    if (!isOwnerRoleNow()) {
        showToast('权限不足：只有老板可以导入对账单', 'error');
        return;
    }
    const supplierId = _selectedFinanceSupplierId();
    if (!supplierId) {
        showToast('请先选择供应商，再导入对账单', 'warning');
        return;
    }
    const sup = (finState.suppliers || []).find(s => Number(s.id) === Number(supplierId));
    const hint = document.getElementById('importStatementSupplierHint');
    if (hint) hint.textContent = '供应商：' + ((sup && sup.name) || ('#' + supplierId));
    const lines = document.getElementById('importStatementLines');
    if (lines) lines.innerHTML = '';
    addImportStatementLine();
    const useCurrent = document.getElementById('importStatementUseCurrentTask');
    if (useCurrent) {
        const hasTask = !!finState.currentTaskId;
        useCurrent.checked = hasTask;
        useCurrent.disabled = !hasTask;
    }
    const submitBtn = document.getElementById('btnSubmitImportStatement');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerText = '提交对账单';
    }
    openModalById('importStatementModal');
}

function _collectImportStatementLines() {
    const rows = document.querySelectorAll('#importStatementLines tr.import-statement-line');
    const lines = [];
    let incomplete = false;
    rows.forEach(tr => {
        const date = ((tr.querySelector('.import-line-date') || {}).value || '').trim();
        const invoice = ((tr.querySelector('.import-line-no') || {}).value || '').trim();
        const amountRaw = ((tr.querySelector('.import-line-amount') || {}).value || '').trim();
        if (!date && !invoice && !amountRaw) return;
        const amount = Number(amountRaw);
        if (!date || !Number.isFinite(amount)) incomplete = true;
        lines.push({ date: date, invoice_no: invoice, amount: amount });
    });
    return { lines: lines, incomplete: incomplete };
}

function submitImportStatement() {
    if (!isOwnerRoleNow()) {
        showToast('权限不足：只有老板可以导入对账单', 'error');
        return;
    }
    const btn = document.getElementById('btnSubmitImportStatement');
    if (btn && btn.disabled) return;
    const supplierId = _selectedFinanceSupplierId();
    if (!supplierId) {
        showToast('请先选择供应商，再导入对账单', 'warning');
        return;
    }
    const collected = _collectImportStatementLines();
    if (collected.incomplete || collected.lines.length === 0) {
        showToast('请至少填一行，日期和金额都要有', 'warning');
        return;
    }
    const useCurrent = document.getElementById('importStatementUseCurrentTask');
    const payload = {
        supplier_id: supplierId,
        statement_period: '',
        external_lines: collected.lines,
    };
    if (useCurrent && useCurrent.checked) {
        if (!finState.currentTaskId) {
            showToast('请先打开一个对账，或取消「导入到当前对账」', 'warning');
            return;
        }
        payload.task_id = finState.currentTaskId;
    }
    if (btn) {
        btn.disabled = true;
        btn.innerText = '正在导入...';
    }
    apiFetch('/api/reconciliation/import-statement', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json().then(data => ({ status: res.status, body: data || {} })))
        .then(ret => {
            if (btn) {
                btn.disabled = false;
                btn.innerText = '提交对账单';
            }
            if (ret.status === 409) {
                showToast((ret.body && ret.body.msg) || '这个对账已经导入过对账单了', 'error');
                return;
            }
            if (ret.status !== 200 || !ret.body || ret.body.status !== 'success') {
                showToast('导入对账单失败' + toastFailDetail(ret.body && ret.body.msg), 'error');
                return;
            }
            showToast('对账单已导入', 'success');
            closeModalById('importStatementModal');
            const taskId = ret.body.task_id;
            loadFinancePanel();
            if (taskId) setTimeout(() => viewReconTask(taskId), 400);
        })
        .catch(err => {
            if (btn) {
                btn.disabled = false;
                btn.innerText = '提交对账单';
            }
            console.error('导入对账单异常', err);
            showToast('导入没有完成，请检查网络后再试', 'error');
        });
}
window.openImportStatementModal = openImportStatementModal;
window.addImportStatementLine = addImportStatementLine;
window.submitImportStatement = submitImportStatement;

// ---- 对账差异明细 ----
function viewReconTask(taskId) {
    apiFetch(`/api/reconciliation/${taskId}`)
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('加载对账详情失败' + toastFailDetail(ret.msg), 'error'); return; }
            finState.currentTaskId = taskId;
            finState.currentDetail = ret;
            renderReconDetail();
        });
}

function renderReconDetail() {
    const detail = finState.currentDetail;
    const area = document.getElementById('reconDetailArea');
    if (!detail) { area.classList.add('hide'); return; }
    area.classList.remove('hide');

    const t = detail.task;
    const [cls, label] = RECON_STATUS_BADGES[t.status] || ['badge-secondary', t.status];
    document.getElementById('reconDetailTitle').innerHTML =
        `任务 #${w2Escape(String(t.id))}｜${t.period_start || t.period_end ? w2Escape(t.period_start || '开头') + ' ~ ' + w2Escape(t.period_end || '今天') : '没限定期间'}｜` +
        `<span class="badge ${cls}">${label}</span>` +
        `<div class="recon-diff-note" id="reconDiffNote">${w2Escape(typeof reconDiffSummaryText === 'function' ? reconDiffSummaryText(detail.lines) : '')}</div>`;

    const locked = t.status === 'confirmed';
    document.getElementById('btnConfirmRecon').classList.toggle('hide', locked);

    const body = document.getElementById('reconLineBody');
    body.innerHTML = '';
    detail.lines.forEach(ln => {
        const [mCls, mLabel] = MATCH_BADGES[ln.match_status] || ['badge-secondary', ln.match_status];
        const sideLabel = ln.side === 'statement'
            ? '<span class="badge badge-secondary">账单行</span>'
            : '<span class="badge badge-warning">餐厅挂账单</span>';
        const matchedDesc = ln.matched_receipt && ln.side === 'statement'
            ? ` ↔ 挂账单 #${ln.matched_receipt_id}` : '';

        let actionHtml;
        if (locked) {
            actionHtml = '<span style="color:var(--text-muted); font-size:0.78rem;"> 已锁定</span>';
        } else if (ln.resolved) {
            actionHtml = '<span class="badge badge-success">已处理</span>';
        } else {
            actionHtml = `
                <button class="btn btn-success" style="min-height:40px; padding:4px 12px; font-size:0.85rem; margin:2px;"
                    onclick="resolveLine(${ln.id}, 'confirm_match')">确认对上</button>
                <button class="btn btn-secondary" style="min-height:40px; padding:4px 12px; font-size:0.85rem; margin:2px;"
                    onclick="resolveLine(${ln.id}, 'write_off')">这笔不用管</button>
                <button class="btn btn-danger" style="min-height:40px; padding:4px 12px; font-size:0.85rem; margin:2px;"
                    onclick="resolveLine(${ln.id}, 'dispute')">有争议</button>`;
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${sideLabel}</td>
            <td>${renderDateCell(ln.line_date)}</td>
            <td>${w2Escape(ln.docket_no || '-')}${matchedDesc}</td>
            <td>${I18N.fmtMoney(ln.amount)}</td>
            <td><span class="badge ${mCls}">${mLabel}</span></td>
            <td style="font-size:0.8rem; color:var(--text-muted);">${w2Escape(ln.resolution_note || '-')}</td>
            <td style="white-space:nowrap;">${actionHtml}</td>`;
        body.appendChild(tr);
    });
}

function resolveLine(lineId, action) {
    const detail = finState.currentDetail;
    if (!detail) return;
    const line = detail.lines.find(l => l.id === lineId);
    if (!line) return;

    const titles = { confirm_match: '确认对上', write_off: '这笔不用管（核销）', dispute: '标记有争议' };
    document.getElementById('reconResolveTitle').innerText = titles[action] || '处理对账差异';
    const esc = s => w2Escape(s == null ? '' : String(s));
    const sideDesc = line.side === 'statement'
        ? `账单行：${renderDateCell(line.line_date)}｜单号 ${esc(line.docket_no)}｜金额 ${I18N.fmtMoney(line.amount)}`
        : `餐厅留底挂账单 #${esc(line.matched_receipt_id)}｜${renderDateCell(line.line_date)}｜金额 ${I18N.fmtMoney(line.amount)}`;
    document.getElementById('reconResolveDesc').innerHTML =
        sideDesc + '<br>当前状态：' + esc(line.match_status_label || line.match_status);
    document.getElementById('reconResolveNote').value = '';

    const grp = document.getElementById('reconResolveReceiptGroup');
    if (action === 'confirm_match') {
        grp.classList.remove('hide');
        const sel = document.getElementById('reconResolveReceipt');
        sel.innerHTML = '';
        // 候选 = 期间内尚未匹配的挂账单 + 本行已有候选
        const candidates = (detail.restaurant_receipts || []).filter(r =>
            !r.matched || r.id === line.matched_receipt_id);
        if (!candidates.length) {
            sel.innerHTML = '<option value="">期间内无可选挂账单</option>';
        }
        candidates.forEach(r => {
            const opt = document.createElement('option');
            opt.value = String(r.id);
            opt.textContent = `挂账单 #${r.id}｜${formatMonthDay(r.receipt_date)}｜${I18N.fmtMoney(r.total_amount)}`;
            sel.appendChild(opt);
        });
        if (line.matched_receipt_id) sel.value = String(line.matched_receipt_id);
    } else {
        grp.classList.add('hide');
    }

    finState.resolveCtx = { lineId, action };
    document.getElementById('reconResolveModal').classList.remove('hide');
}

function submitReconResolve() {
    const ctx = finState.resolveCtx;
    if (!ctx) return;
    const payload = { line_id: ctx.lineId, action: ctx.action };
    const note = document.getElementById('reconResolveNote').value.trim();
    if (note) payload.note = note;
    if (ctx.action === 'confirm_match') {
        const rid = document.getElementById('reconResolveReceipt').value;
        if (!rid) { showToast('请选择要匹配的挂账单', 'warning'); return; }
        payload.receipt_id = Number(rid);
    }

    apiFetch(`/api/reconciliation/${finState.currentTaskId}/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('处理差异行失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 差异行处理成功', ret.msg);
            showToast('已处理该差异行', 'success', TOAST_DURATION.long);
            closeModalById('reconResolveModal');
            loadFinancePanel();
            viewReconTask(finState.currentTaskId);
        })
        .catch(err => { console.error('对账处理异常', err); showToast('处理失败' + toastFailDetail(err), 'error'); });
}

async function confirmReconTask() {
    const taskId = finState.currentTaskId;
    if (!taskId) return;
    const okRecon = await uiConfirm({
        title: '确认对账完成',
        message: `确认对账任务 #${taskId} 完成？\n确认后这个期间就锁定了，不能再改；没处理的差异会留在记录里。`,
        confirmText: '确认完成',
    });
    if (!okRecon) return;

    apiFetch(`/api/reconciliation/${taskId}/confirm`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('确认对账失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[recon] 对账确认成功', ret.msg);
            showToast('已确认对账完成', 'success', TOAST_DURATION.long);
            loadFinancePanel();
            viewReconTask(taskId);
        })
        .catch(err => { console.error('确认对账异常', err); showToast('确认失败' + toastFailDetail(err), 'error'); });
}

// ---- 盘点调整（D15/M5） ----
let stocktakeSkuId = null;

function openStocktakeModal(skuId, skuName, currentStock, baseUnit) {
    stocktakeSkuId = skuId;
    document.getElementById('stocktakeHint').innerHTML =
        `食材：<strong>${w2Escape(skuName)}</strong>｜当前账面 ` +
        `<strong>${Number(currentStock || 0)}</strong> ${w2Escape(baseUnit || '')}`;
    document.getElementById('stocktakeQty').value = currentStock ?? '';
    document.getElementById('stocktakeNote').value = '';
    document.getElementById('stocktakeModal').classList.remove('hide');
}

function submitStocktake() {
    if (!stocktakeSkuId) return;
    const qtyRaw = document.getElementById('stocktakeQty').value;
    const actualQty = parseFloat(qtyRaw);
    if (qtyRaw === '' || isNaN(actualQty) || actualQty < 0) {
        showToast('请输入有效的实盘数量', 'warning');
        return;
    }
    const payload = { actual_qty: actualQty };
    const note = document.getElementById('stocktakeNote').value.trim();
    if (note) payload.note = note;

    apiFetch(`/api/inventory/${stocktakeSkuId}/stocktake`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') { showToast('盘点调整失败' + toastFailDetail(ret.msg), 'error'); return; }
            console.log('[inventory] 盘点成功', ret.msg);
            showToast('已完成盘点调整', 'success');
            closeModalById('stocktakeModal');
            stocktakeSkuId = null;
            loadInventoryData();
        })
        .catch(err => { console.error('盘点异常', err); showToast('盘点失败' + toastFailDetail(err), 'error'); });
}

// ---- W4 出库：消耗 / 损耗（B-30/B-31/B-32）合并为统一出库 ----
let outboundSkuId = null;
let outboundKind = null; // 'consume' | 'waste'

const OUTBOUND_META = {
    consume: {
        title: '出库登记 - 日常用量',
        hint: '登记厨房或营业日常使用量，同步扣减账面库存。',
        path: 'consume',
        btn: '确认出库（用量）',
    },
    waste: {
        title: '出库登记 - 报损耗',
        hint: '登记食材变质或损坏等损耗，同步扣减账面库存。',
        path: 'waste',
        btn: '确认出库（损耗）',
    },
};

function onOutboundKindChange() {
    const sel = document.getElementById('outboundKind');
    if (!sel) return;
    const kind = sel.value;
    outboundKind = kind;
    const meta = OUTBOUND_META[kind];
    if (!meta) return;
    const hintEl = document.getElementById('outboundKindHint');
    if (hintEl) hintEl.textContent = meta.hint;
    const btn = document.getElementById('outboundSubmitBtn');
    if (btn) btn.textContent = meta.btn;
}

function openOutboundModal(skuId, skuName, currentStock, baseUnit, kind) {
    // kind 可选：未传或非法时默认 consume，后续可通过下拉切换
    let initKind = kind && OUTBOUND_META[kind] ? kind : 'consume';
    outboundSkuId = skuId;
    outboundKind = initKind;
    const sel = document.getElementById('outboundKind');
    if (sel) sel.value = initKind;
    const meta = OUTBOUND_META[initKind];
    document.getElementById('outboundModalTitle').textContent = '出库登记';
    document.getElementById('outboundHint').innerHTML =
        `食材：<strong>${w2Escape(skuName)}</strong>｜当前账面 ` +
        `<strong>${Number(currentStock || 0)}</strong> ${w2Escape(baseUnit || '')}`;
    const hintEl = document.getElementById('outboundKindHint');
    if (hintEl) hintEl.textContent = meta.hint;
    document.getElementById('outboundQty').value = '';
    document.getElementById('outboundNote').value = '';
    document.getElementById('outboundSubmitBtn').textContent = meta.btn;
    document.getElementById('outboundModal').classList.remove('hide');
}

async function submitOutbound() {
    if (!outboundSkuId) return;
    // 以下拉当前值为准，兼容旧调用仍传 kind 的场景
    const sel = document.getElementById('outboundKind');
    const kind = sel ? sel.value : outboundKind;
    if (!kind || !OUTBOUND_META[kind]) return;
    outboundKind = kind;
    const meta = OUTBOUND_META[kind];
    const qtyRaw = document.getElementById('outboundQty').value;
    const qty = parseFloat(qtyRaw);
    if (qtyRaw === '' || isNaN(qty) || qty <= 0) {
        showToast('请输入有效的出库数量', 'warning');
        return;
    }
    const payload = { quantity: qty };
    const notes = document.getElementById('outboundNote').value.trim();
    if (notes) payload.notes = notes;
    const outSku = inventorySkusMap.get(outboundSkuId) || {};
    postStockOutflow(`/api/inventory/${outboundSkuId}/${meta.path}`, payload, outSku.current_stock, outSku.base_unit, kind === 'waste' ? '报损' : '扣减')
        .then(o => (o ? { httpStatus: o.httpStatus, body: o.ret } : null))
        .then(res => {
            if (!res) return;
            const { httpStatus, body } = res;
            if (!body || body.status !== 'success') {
                console.error('[inventory] 出库失败', httpStatus, body);
                const why = body && body.msg ? ('：' + body.msg) : '，请稍后重试';
                showToast(`${meta.title}没有成功${why}`, 'error');
                if (httpStatus === 409) loadInventoryData();
                return;
            }
            console.log('[inventory] 出库成功', body.msg);
            showToast(`已完成${meta.title}，账面库存已扣减`, 'success');
            closeModalById('outboundModal');
            outboundSkuId = null;
            outboundKind = null;
            loadInventoryData();
        })
        .catch(err => { console.error('出库异常', err); showToast('出库失败' + toastFailDetail(err), 'error'); });
}

