// -*- coding: utf-8 -*-
// 归档对账、付款、供应商合并、回收站、今日待办。依赖 main.js 里的 apiFetch / finState / w2Escape / showToast 等全局，
// 以及 ui-dialog.js 的 uiConfirm / uiAlert。加载顺序：main.js -> settings.js -> finance.js。

// ---------------------------------------------------------------------------
// 工具
// ---------------------------------------------------------------------------
function _fin$(id) { return document.getElementById(id); }
function _round2(n) { return Math.round((Number(n) || 0) * 100) / 100; }

async function finApi(url, opts) {
    let res;
    try {
        res = await apiFetch(url, opts);
    } catch (e) {
        const err = new Error('网络好像断了，请检查网络后再试一次。');
        err.status = 0;
        throw err;
    }
    let body = null;
    try { body = await res.json(); } catch (e) { body = null; }
    if (!res.ok || (body && body.status === 'error')) {
        let msg = body && (body.msg || body.message || (typeof body.detail === 'string' ? body.detail : (body.detail && body.detail.msg)));
        if (!msg || /^[\x20-\x7e]+$/.test(String(msg))) {
            msg = res.status === 403 ? '这个操作只有老板可以做。'
                : res.status === 404 ? '找不到这条记录，可能已经被删除，请刷新页面再看。'
                : res.status === 422 || res.status === 400 ? '填写的内容有误，请检查后再试。'
                : '操作没有成功，请稍后再试。';
        }
        const err = new Error(msg);
        err.status = res.status;
        err.body = body;
        throw err;
    }
    return body;
}

// ---------------------------------------------------------------------------
// 付款口径：已入账（approved）的挂账单才算应付；已付金额以后端 paid_amount 为准
// ---------------------------------------------------------------------------
function payTotalAmountHkd(r) {
    if (!r) return 0;
    if (r.currency && r.currency !== 'HKD') {
        if (r.total_hkd != null) return _round2(r.total_hkd);
        return null;
    }
    return _round2(r.total_hkd != null ? r.total_hkd : r.total_amount);
}

function payPaidAmount(r) {
    if (r && r.paid_amount != null && r.paid_amount !== '') return _round2(r.paid_amount);
    const tot = payTotalAmountHkd(r);
    return (r && r.payment_id && tot != null) ? tot : 0;
}

function payOutstandingAmount(r) {
    if (!r) return 0;
    if (r.outstanding_amount != null && r.outstanding_amount !== '') {
        return _round2(r.outstanding_amount);
    }
    const tot = payTotalAmountHkd(r);
    if (tot === null) return 0;
    return Math.max(0, _round2(tot - payPaidAmount(r)));
}

function payAllocStatusLabel(r) {
    if (r && r.currency && r.currency !== 'HKD' && (r.fx_missing || r.total_hkd == null)) {
        return '汇率未知';
    }
    const paid = payPaidAmount(r);
    const out = payOutstandingAmount(r);
    if (out <= 0 && paid > 0) return '已付清';
    if (paid > 0) return '部分已付';
    return '已入账';
}

// ---------------------------------------------------------------------------
// 登记支付
// ---------------------------------------------------------------------------
let _payRows = [];

function _payError(msg) {
    let box = _fin$('payError');
    if (!box) {
        box = document.createElement('div');
        box.id = 'payError';
        box.className = 'st-msg';
        box.setAttribute('role', 'alert');
        const modalBody = document.querySelector('#paymentModal .modal-body');
        const actions = modalBody && modalBody.lastElementChild;
        if (modalBody && actions) modalBody.insertBefore(box, actions);
    }
    box.textContent = msg || '';
}

function openPaymentModal() {
    if (!finState.supplierId) {
        showToast('请先在上面选一家供应商，再登记付款。', 'warning');
        return;
    }
    const sup = finState.suppliers.find(s => s.id === finState.supplierId);
    if (!sup) return;
    _fin$('paymentSupplierHint').textContent = '供应商：' + sup.name;
    _fin$('payAmount').value = '';
    _fin$('payDate').value = getTodayDateStr();
    _fin$('payDate').max = getTodayDateStr();
    _fin$('payNotes').value = '';
    _fin$('payVoucher').value = '';
    _fin$('payCheckAll').checked = false;
    _payError('');
    const body = _fin$('payReceiptBody');
    body.innerHTML = '<tr><td colspan="8" style="color:var(--text-muted);">正在加载这家供应商的挂账单……</td></tr>';
    openAccessibleModal(_fin$('paymentModal'));

    apiFetch(`/api/payables?supplier_id=${encodeURIComponent(sup.id)}`)
        .then(res => res.json())
        .then(payRet => {
            if (!payRet || payRet.status !== 'success' || !payRet.data) throw new Error('load payables failed');
            const rows = payRet.data.rows || [];
            _payRows = rows.map(p => ({
                id: Number(p.receipt_id || p.id),
                receipt_id: Number(p.receipt_id || p.id),
                supplier_id: p.supplier_id,
                supplier_name: p.supplier_name,
                receipt_date: p.receipt_date,
                settlement_type: 'credit',
                status: 'approved',
                currency: p.currency || 'HKD',
                total_amount: p.total_amount,
                total_hkd: p.total_hkd,
                paid_amount: p.paid_amount,
                outstanding_amount: p.outstanding_amount,
                payment_status: p.payment_status,
                fx_missing: Boolean(p.fx_missing),
                has_fx_snapshot: p.has_fx_snapshot,
            })).filter(r => r.payment_status !== 'paid' && (r.outstanding_amount > 0 || r.fx_missing));
            renderPayRows();
        })
        .catch(() => {
            const loadPayList = (typeof fetchReceiptListShared === 'function')
                ? fetchReceiptListShared(false)
                : apiFetch('/api/receipts').then(res => res.json());
            loadPayList
                .then(ret => {
                    if (!ret || ret.status !== 'success') throw new Error('load');
                    _payRows = (ret.data || []).filter(r =>
                        (r.supplier_id != null ? Number(r.supplier_id) === Number(sup.id) : r.supplier_name === sup.name)
                        && r.settlement_type === 'credit' && r.status === 'approved'
                        && !r.deleted_at && payOutstandingAmount(r) > 0);
                    renderPayRows();
                })
                .catch(() => {
                    body.innerHTML = '<tr><td colspan="8" style="color:var(--danger-strong);">挂账单没有加载出来，请关掉窗口再点一次「登记支付」。</td></tr>';
                });
        });
}

function renderPayRows() {
    const body = _fin$('payReceiptBody');
    body.innerHTML = '';
    if (!_payRows.length) {
        body.innerHTML = '<tr><td colspan="8" style="color:var(--text-muted);">这家供应商没有需要付的挂账单。只有已审批入账、还没付清的挂账单才会出现在这里。</td></tr>';
        _fin$('payAmount').value = '';
        return;
    }
    _payRows.forEach(r => {
        const isForeign = r.currency && r.currency !== 'HKD';
        const isMissingFx = isForeign && (r.fx_missing || r.total_hkd == null);
        const out = isMissingFx ? 0 : payOutstandingAmount(r);
        const paid = isMissingFx ? 0 : payPaidAmount(r);
        const tr = document.createElement('tr');

        let amountCell = '';
        let paidCell = '';
        let outCell = '';
        let inputCell = '';
        let checkCell = '';

        const origStr = typeof formatCurrency === 'function' ? formatCurrency(r.total_amount, r.currency) : `${r.currency} ${r.total_amount}`;

        if (isMissingFx) {
            checkCell = `<input type="checkbox" class="pay-receipt-check" value="${Number(r.id)}" disabled aria-label="单据 #${Number(r.id)} 汇率未知不可选">`;
            amountCell = `<span class="badge badge-warning" style="font-size:0.75rem;">汇率未知</span> <div style="font-size:0.72rem; color:var(--text-muted);">原币 ${w2Escape(origStr)}</div>`;
            paidCell = '<span style="color:var(--text-muted);">—</span>';
            outCell = '<span style="color:var(--text-muted);">—</span>';
            inputCell = `<input type="text" class="form-control" disabled value="需先设置汇率" aria-label="单据 #${Number(r.id)} 需先设置汇率" style="min-height:40px; width:110px; font-size:0.75rem;">`;
        } else if (isForeign) {
            checkCell = `<input type="checkbox" class="pay-receipt-check" value="${Number(r.id)}" aria-label="选择单据 #${Number(r.id)}" checked>`;
            amountCell = `${I18N.fmtMoney(r.total_hkd)} <div style="font-size:0.72rem; color:var(--text-muted);">原币 ${w2Escape(origStr)}</div>`;
            paidCell = `${I18N.fmtMoney(paid)}`;
            outCell = `<strong>${I18N.fmtMoney(out)}</strong>`;
            inputCell = `<input type="number" class="form-control pay-amt-input" data-rid="${Number(r.id)}" data-max="${out}" step="0.01" min="0.01" max="${out}" value="${out.toFixed(2)}" aria-label="单据 #${Number(r.id)} 本次付款金额" style="min-height:40px; width:110px;">`;
        } else {
            checkCell = `<input type="checkbox" class="pay-receipt-check" value="${Number(r.id)}" aria-label="选择单据 #${Number(r.id)}" checked>`;
            amountCell = `${I18N.fmtMoney(r.total_amount)}`;
            paidCell = `${I18N.fmtMoney(paid)}`;
            outCell = `<strong>${I18N.fmtMoney(out)}</strong>`;
            inputCell = `<input type="number" class="form-control pay-amt-input" data-rid="${Number(r.id)}" data-max="${out}" step="0.01" min="0.01" max="${out}" value="${out.toFixed(2)}" aria-label="单据 #${Number(r.id)} 本次付款金额" style="min-height:40px; width:110px;">`;
        }

        tr.innerHTML = `
            <td>${checkCell}</td>
            <td>#${Number(r.id)}</td>
            <td>${renderDateCell(r.receipt_date)}</td>
            <td>${amountCell}</td>
            <td>${paidCell}</td>
            <td>${outCell}</td>
            <td>${inputCell}</td>
            <td>${w2Escape(payAllocStatusLabel(r))}</td>`;
        body.appendChild(tr);
    });
    body.querySelectorAll('.pay-receipt-check:not([disabled]), .pay-amt-input').forEach(el => {
        el.addEventListener('input', recalcPayTotal);
        el.addEventListener('change', recalcPayTotal);
    });
    const enabledChecks = body.querySelectorAll('.pay-receipt-check:not([disabled])');
    _fin$('payCheckAll').checked = enabledChecks.length > 0;
    recalcPayTotal();
}

function togglePayCheckAll(cb) {
    document.querySelectorAll('.pay-receipt-check:not([disabled])').forEach(c => { c.checked = cb.checked; });
    recalcPayTotal();
}

function _collectPayAllocations() {
    const out = [];
    let problem = '';
    document.querySelectorAll('#payReceiptBody tr').forEach(tr => {
        const chk = tr.querySelector('.pay-receipt-check');
        const inp = tr.querySelector('.pay-amt-input');
        if (!chk || !chk.checked || !inp) return;
        const rid = Number(inp.dataset.rid);
        const max = Number(inp.dataset.max);
        const raw = String(inp.value).trim();
        const amt = Number(raw);
        if (!raw || !isFinite(amt) || amt <= 0) {
            problem = problem || ('单据 #' + rid + ' 的本次付款要填一个大于 0 的金额。');
        } else if (_round2(amt) > max + 0.0001) {
            problem = problem || ('单据 #' + rid + ' 只剩 ' + I18N.fmtMoney(max) + ' 没付，本次付款不能超过这个数。');
        }
        out.push({ receipt_id: rid, amount: _round2(amt) });
    });
    return { allocations: out, problem };
}

function recalcPayTotal() {
    const { allocations, problem } = _collectPayAllocations();
    const total = allocations.reduce((a, x) => a + (isFinite(x.amount) ? x.amount : 0), 0);
    _fin$('payAmount').value = allocations.length ? total.toFixed(2) : '';
    _payError(problem);
    const all = document.querySelectorAll('.pay-receipt-check');
    const checked = document.querySelectorAll('.pay-receipt-check:checked');
    _fin$('payCheckAll').checked = all.length > 0 && all.length === checked.length;
}

function submitPayment() {
    const { allocations, problem } = _collectPayAllocations();
    if (!allocations.length) { _payError('请先勾选要付的挂账单。'); return; }
    if (problem) { _payError(problem); return; }
    const paidAt = _fin$('payDate').value;
    if (!paidAt) { _payError('请选择支付日期。'); _fin$('payDate').focus(); return; }
    if (paidAt > getTodayDateStr()) { _payError('支付日期不能晚于今天，请改成实际付款的日期。'); _fin$('payDate').focus(); return; }
    const amount = _round2(allocations.reduce((a, x) => a + x.amount, 0));
    if (!(amount > 0)) { _payError('本次支付金额要大于 0。'); return; }

    const fd = new FormData();
    fd.append('supplier_id', String(finState.supplierId));
    fd.append('amount', String(amount));
    fd.append('method', _fin$('payMethod').value);
    fd.append('paid_at', paidAt);
    const notes = _fin$('payNotes').value.trim();
    if (notes) fd.append('notes', notes);
    allocations.forEach(a => fd.append('receipt_ids', String(a.receipt_id)));
    fd.append('allocations', JSON.stringify(allocations));
    const voucherFile = _fin$('payVoucher').files[0];
    if (voucherFile) fd.append('voucher', voucherFile);

    const btn = document.querySelector('#paymentModal .btn-primary');
    if (btn) btn.disabled = true;
    finApi('/api/payments', { method: 'POST', body: fd })
        .then(() => {
            showToast('已登记支付 ' + I18N.fmtMoney(amount) + '。', 'success');
            closeModalById('paymentModal');
            loadFinancePanel();
            loadReceiptsHistory();
            loadSupplierAdmin();
        })
        .catch(err => { _payError(err.message); showToast('登记支付没有成功：' + err.message, 'error'); })
        .finally(() => { if (btn) btn.disabled = false; });
}

// ---------------------------------------------------------------------------
// 对账：状态人话化 + 期间 + 差异说明
// ---------------------------------------------------------------------------
const RECON_MATCH_TEXT = {
    matched: '已对上',
    pending: '待确认',
    amount_mismatch: '金额对不上',
    supplier_only: '账单有，我们没有单',
    restaurant_only: '我们有单，账单上没有',
};

function reconDiffSummaryText(lines) {
    const open = (lines || []).filter(l => !l.resolved && l.match_status !== 'matched');
    const matched = (lines || []).filter(l => l.match_status === 'matched').length;
    if (!(lines || []).length) return '这个期间没有可以对的单据。';
    if (!open.length) return '所有行都已经对上或处理完，可以点「确认对账完成」。';
    const cnt = k => open.filter(l => l.match_status === k).length;
    const parts = [];
    if (cnt('amount_mismatch')) parts.push('金额对不上 ' + cnt('amount_mismatch') + ' 行（我们的单和账单金额不一样）');
    if (cnt('supplier_only')) parts.push('账单有、我们没有单 ' + cnt('supplier_only') + ' 行（可能漏拍或漏录）');
    if (cnt('restaurant_only')) parts.push('我们有单、账单上没有 ' + cnt('restaurant_only') + ' 行（可能供应商漏记）');
    if (cnt('pending')) parts.push('待确认 ' + cnt('pending') + ' 行');
    return '已对上 ' + matched + ' 行，还有 ' + open.length + ' 行要处理：' + parts.join('；') + '。';
}

// ---------------------------------------------------------------------------
// 供应商合并
// ---------------------------------------------------------------------------
let _mergeSuppliers = [];

function openMergeModal(dropId) {
    const err = _fin$('mergeError');
    if (err) err.textContent = '';
    apiFetch('/api/suppliers')
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') throw new Error('load');
            _mergeSuppliers = (ret.data || []).filter(s => s.active === undefined || s.active === 1);
            if (_mergeSuppliers.length < 2) {
                showToast('现在只有一家供应商，没有可以合并的。', 'info');
                return;
            }
            const opt = s => `<option value="${Number(s.id)}">${w2Escape(s.name)}（${Number(s.receipt_count) || 0} 张单）</option>`;
            const keep = _fin$('mergeKeepSelect');
            const drop = _fin$('mergeDropSelect');
            keep.innerHTML = _mergeSuppliers.map(opt).join('');
            drop.innerHTML = _mergeSuppliers.map(opt).join('');
            if (dropId != null) {
                drop.value = String(dropId);
                const firstOther = _mergeSuppliers.find(s => Number(s.id) !== Number(dropId));
                keep.value = String(firstOther.id);
            } else {
                keep.value = String(_mergeSuppliers[0].id);
                drop.value = String(_mergeSuppliers[1].id);
            }
            renderMergePreview();
            openAccessibleModal(_fin$('mergeModal'), '#mergeKeepSelect');
        })
        .catch(() => showToast('供应商列表没有加载出来，请稍后再试。', 'error'));
}

function renderMergePreview() {
    const box = _fin$('mergePreview');
    const err = _fin$('mergeError');
    if (err) err.textContent = '';
    const keep = _mergeSuppliers.find(s => String(s.id) === _fin$('mergeKeepSelect').value);
    const drop = _mergeSuppliers.find(s => String(s.id) === _fin$('mergeDropSelect').value);
    const btn = _fin$('mergeSubmitBtn');
    if (!keep || !drop) { box.innerHTML = ''; return; }
    if (keep.id === drop.id) {
        box.innerHTML = '<div class="merge-warn">「保留」和「并入」选成了同一家，请换一家。</div>';
        btn.disabled = true;
        return;
    }
    btn.disabled = false;
    box.innerHTML = `
        <div class="merge-title">合并后会这样：</div>
        <ul>
            <li>「${w2Escape(drop.name)}」的 <strong>${Number(drop.receipt_count) || 0}</strong> 张单据，全部改归到「${w2Escape(keep.name)}」</li>
            <li>「${w2Escape(drop.name)}」还没付的挂账单 <strong>${I18N.fmtMoney(drop.unpaid_credit_total)}</strong>（${Number(drop.unpaid_credit_count) || 0} 张），付款记录、别名、对账记录也一起并过去</li>
            <li>合并后「${w2Escape(keep.name)}」共有约 <strong>${(Number(keep.receipt_count) || 0) + (Number(drop.receipt_count) || 0)}</strong> 张单据</li>
            <li class="merge-warn">「${w2Escape(drop.name)}」这个供应商会消失，合并后不能撤销</li>
        </ul>`;
}

async function submitMerge() {
    const keep = _mergeSuppliers.find(s => String(s.id) === _fin$('mergeKeepSelect').value);
    const drop = _mergeSuppliers.find(s => String(s.id) === _fin$('mergeDropSelect').value);
    const err = _fin$('mergeError');
    if (!keep || !drop) { err.textContent = '请先选好要保留的和要并入的供应商。'; return; }
    if (keep.id === drop.id) { err.textContent = '「保留」和「并入」不能是同一家。'; return; }
    const ok = await uiConfirm({
        title: '确认合并供应商',
        message: '把「' + drop.name + '」并入「' + keep.name + '」？\n合并后「' + drop.name + '」会消失，不能撤销。',
        details: ['迁移单据 ' + (Number(drop.receipt_count) || 0) + ' 张', '迁移未付挂账单 ' + I18N.fmtMoney(drop.unpaid_credit_total)],
        confirmText: '确认合并',
        danger: true,
    });
    if (!ok) return;
    const btn = _fin$('mergeSubmitBtn');
    btn.disabled = true;
    finApi('/api/suppliers/merge', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ keep_id: keep.id, drop_id: drop.id }),
    })
        .then(() => {
            showToast('已把「' + drop.name + '」合并到「' + keep.name + '」。', 'success', TOAST_DURATION.long);
            closeModalById('mergeModal');
            loadSuppliersData();
            loadReceiptsHistory();
            loadSupplierAdmin();
            loadFinancePanel();
        })
        .catch(e => { err.textContent = e.message; })
        .finally(() => { btn.disabled = false; });
}

// ---------------------------------------------------------------------------
// 归档对账：单据 / 供应商 / 应付 三个子 Tab
// ---------------------------------------------------------------------------
const ARC_PANES = [['docs', '单据'], ['suppliers', '供应商'], ['payables', '应付与对账']];
let _arcPane = 'docs';

function initArchivePanes() {
    const tab = _fin$('tab-archive');
    if (!tab || _fin$('arcSubTabs')) return;
    const bar = document.createElement('div');
    bar.id = 'arcSubTabs';
    bar.className = 'arc-subtabs';
    bar.setAttribute('role', 'tablist');
    bar.setAttribute('aria-label', '归档对账分类');
    bar.innerHTML = ARC_PANES.map(([k, label]) =>
        `<button type="button" role="tab" class="arc-subtab" id="arcTab-${k}" data-pane="${k}" aria-selected="false">${label}${k === 'payables' ? '<span id="arcPayCount" class="nav-red-dot hide">0</span>' : ''}</button>`).join('');
    tab.insertBefore(bar, tab.firstChild);
    bar.addEventListener('click', e => {
        const b = e.target.closest('.arc-subtab');
        if (b) showArchivePane(b.dataset.pane);
    });
    bar.addEventListener('keydown', e => {
        if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
        const idx = ARC_PANES.findIndex(p => p[0] === _arcPane);
        const next = ARC_PANES[(idx + (e.key === 'ArrowRight' ? 1 : ARC_PANES.length - 1)) % ARC_PANES.length][0];
        showArchivePane(next);
        _fin$('arcTab-' + next).focus();
    });
    let saved = null;
    try { saved = sessionStorage.getItem('arcPane'); } catch (e) {}
    showArchivePane(ARC_PANES.some(p => p[0] === saved) ? saved : 'docs');
}

function showArchivePane(name) {
    if (!ARC_PANES.some(p => p[0] === name)) name = 'docs';
    _arcPane = name;
    try { sessionStorage.setItem('arcPane', name); } catch (e) {}
    document.querySelectorAll('#tab-archive [data-arc-pane]').forEach(el => {
        el.classList.toggle('arc-pane-hidden', el.dataset.arcPane !== name);
    });
    document.querySelectorAll('#arcSubTabs .arc-subtab').forEach(b => {
        const on = b.dataset.pane === name;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', on ? 'true' : 'false');
        b.tabIndex = on ? 0 : -1;
    });
}
window.showArchivePane = showArchivePane;

// ---------------------------------------------------------------------------
// 回收站（owner）
// ---------------------------------------------------------------------------
function initTrashCard() {
    const tab = _fin$('tab-archive');
    if (!tab || _fin$('trashCard')) return;
    const card = document.createElement('div');
    card.className = 'card owner-only';
    card.id = 'trashCard';
    card.setAttribute('data-arc-pane', 'docs');
    card.style.marginTop = '16px';
    card.innerHTML = `
        <details id="trashDetails">
            <summary class="trash-summary">回收站：被放弃的单据 <span id="trashCount" class="badge badge-secondary">0</span></summary>
            <p class="st-sub">店员放弃上传的单据会先放在这里。点「恢复」可以找回来，点「彻底删除」就真的没有了。</p>
            <div id="trashBody" aria-live="polite"></div>
        </details>`;
    tab.appendChild(card);
    _fin$('trashDetails').addEventListener('toggle', e => { if (e.target.open) loadTrash(); });
    bindTrashActions();
    if (typeof applyRoleVisibility === 'function') {
        try { applyRoleVisibility(localStorage.getItem('demo_role') || 'admin'); } catch (e) {}
    }
}

// 捕获阶段委托：按钮被一次刷新替换后，点击仍能打到当前 data-id。
function bindTrashActions() {
    if (window.__trashActionsBound) return;
    window.__trashActionsBound = true;
    document.addEventListener('click', function (e) {
        const node = e.target;
        if (!node || !node.closest) return;
        const restoreBtn = node.closest('#trashBody .trash-restore');
        if (restoreBtn) {
            e.preventDefault();
            e.stopPropagation();
            restoreTrash(restoreBtn.getAttribute('data-id'));
            return;
        }
        const purgeBtn = node.closest('#trashBody .trash-purge');
        if (purgeBtn) {
            e.preventDefault();
            e.stopPropagation();
            purgeTrash(purgeBtn.getAttribute('data-id'));
        }
    }, true);
}

let _trashLoad = null;

async function loadTrash() {
    bindTrashActions();
    if (_trashLoad) return _trashLoad;
    _trashLoad = renderTrash().finally(() => { _trashLoad = null; });
    return _trashLoad;
}

async function renderTrash() {
    const box = _fin$('trashBody');
    if (!box) return;
    const hadRows = !!box.querySelector('.trash-restore, .trash-purge');
    if (!hadRows) {
        box.innerHTML = '<div class="st-state st-state-loading">正在加载回收站……</div>';
    }
    try {
        const ret = await finApi('/api/trash/receipts');
        const rows = (ret && ret.data) || [];
        const count = _fin$('trashCount');
        if (count) count.textContent = String(rows.length);
        if (!rows.length) { box.innerHTML = '<div class="st-state st-state-empty">回收站是空的。</div>'; return; }
        box.innerHTML = '<div class="table-container"><table class="data-table"><thead><tr><th>单据</th><th>供应商</th><th>金额</th><th>放弃时间</th><th>操作</th></tr></thead><tbody>'
            + rows.map(r => `<tr>
                <td>#${Number(r.id)}</td>
                <td>${w2Escape(r.supplier_name || '未识别')}</td>
                <td>${r.total_amount != null ? I18N.fmtMoney(r.total_amount) : '—'}</td>
                <td>${w2Escape(String(r.deleted_at || '').slice(0, 16).replace('T', ' ')) || '—'}</td>
                <td class="st-ops">
                    <button type="button" class="btn btn-primary st-btn trash-restore" data-id="${Number(r.id)}">恢复</button>
                    <button type="button" class="btn btn-secondary st-btn trash-purge" data-id="${Number(r.id)}">彻底删除</button>
                </td></tr>`).join('')
            + '</tbody></table></div>';
    } catch (e) {
        box.innerHTML = '<div class="st-state st-state-error">' + w2Escape(e.message) + ' <button type="button" class="btn btn-secondary st-btn" id="trashRetry">重新加载</button></div>';
        const retry = _fin$('trashRetry');
        if (retry) retry.onclick = loadTrash;
    }
}

const _restoreInflight = new Set();

async function restoreTrash(id) {
    const key = String(id);
    if (!key || _restoreInflight.has(key)) return;
    _restoreInflight.add(key);
    try {
        await finApi('/api/trash/receipts/' + encodeURIComponent(id) + '/restore', { method: 'POST' });
        showToast('已恢复单据 #' + id + '，可以在单据列表里找到。', 'success');
        loadTrash();
        loadReceiptsHistory();
        if (typeof loadWorkbenchQueue === 'function') loadWorkbenchQueue();
    } catch (e) {
        await uiAlert({ title: '没有恢复成功', message: e.message });
    } finally {
        _restoreInflight.delete(key);
    }
}

async function purgeTrash(id) {
    const ok = await uiConfirm({
        title: '彻底删除这张单据',
        message: '彻底删除单据 #' + id + '后无法找回，原图也会一起删掉。确定吗？',
        confirmText: '彻底删除',
        danger: true,
    });
    if (!ok) return;
    try {
        await finApi('/api/trash/receipts/' + encodeURIComponent(id), { method: 'DELETE' });
        showToast('已彻底删除单据 #' + id + '。', 'success');
        loadTrash();
    } catch (e) {
        await uiAlert({ title: '没有删除成功', message: e.message });
    }
}
window.loadTrash = loadTrash;

// ---------------------------------------------------------------------------
// 今日待办（owner 默认首页）
// ---------------------------------------------------------------------------
async function loadToday() {
    const host = _fin$('tab-today');
    if (!host) return;
    if (!host.dataset.ready) {
        host.dataset.ready = '1';
        host.innerHTML = `
            <div class="st-wrap">
                <div class="st-head"><h2>今日待办</h2><p class="st-sub">老板每天先看这里：有几张单等你审批、哪些挂账单要付款、哪些食材快没了。</p></div>
                <div id="todayBody" class="today-grid" aria-live="polite"></div>
            </div>`;
    }
    const body = _fin$('todayBody');
    body.innerHTML = '<div class="st-state st-state-loading">正在汇总今天的待办……</div>';
    let receipts, skus;
    try {
        const [rc, inv] = await Promise.all([
            (typeof fetchReceiptListShared === 'function' ? fetchReceiptListShared(false) : finApi('/api/receipts')),
            finApi('/api/inventory?stock=low'),
        ]);
        receipts = (rc && rc.data) || [];
        skus = (inv && inv.data) || [];
    } catch (e) {
        body.innerHTML = '<div class="st-state st-state-error">' + w2Escape(e.message) + ' <button type="button" class="btn btn-secondary st-btn" id="todayRetry">重新加载</button></div>';
        _fin$('todayRetry').onclick = loadToday;
        return;
    }
    const live = receipts.filter(r => !r.deleted_at);
    const pending = live.filter(r => r.status === 'edited');
    const payables = live.filter(r => r.status === 'approved' && r.settlement_type === 'credit' && r.doc_form !== 'statement' && payOutstandingAmount(r) > 0);
    const isOver = r => r.payment_status === 'overdue' || r.is_overdue === true;
    const overdue = payables.filter(isOver);
    const dueSoon = payables.filter(r => payIsDueSoon(r) && !isOver(r));
    const low = skus.filter(s => s.is_low_stock !== false);

    const li = arr => arr.slice(0, 5).map(x => '<li>' + x + '</li>').join('');
    const block = (id, title, count, unit, listHtml, emptyText, btnLabel, handler) => `
        <section class="card today-card" aria-labelledby="${id}T">
            <h3 id="${id}T">${title}</h3>
            <div class="today-num ${count ? 'is-hot' : ''}">${count}<span class="today-unit">${unit}</span></div>
            ${count ? '<ul class="today-list">' + listHtml + '</ul>' : '<p class="st-sub">' + emptyText + '</p>'}
            <button type="button" class="btn ${count ? 'btn-primary' : 'btn-secondary'} st-btn" data-today="${handler}">${btnLabel}</button>
        </section>`;

    const payList = li(overdue.concat(dueSoon).map(r =>
        w2Escape(r.supplier_name || '未识别') + I18N.fmtMoney(payOutstandingAmount(r)) + (isOver(r) ? '（已逾期）' : '（快到期）')));
    body.innerHTML =
        block('todayApprove', '待我审批', pending.length, ' 张',
            li(pending.map(r => '#' + Number(r.id) + ' ' + w2Escape(r.supplier_name || '未识别') + I18N.fmtMoney(r.total_amount))),
            '没有等你审批的单。', '去审批', 'approve')
        + block('todayPay', '挂账单：逾期 / 快到期', overdue.length + dueSoon.length, ' 张',
            payList, '没有逾期或快到期的挂账单。', '去应付清单', 'pay')
        + block('todayLow', '低库存', low.length, ' 种',
            li(low.map(s => w2Escape(s.name) + '：剩 ' + w2Escape(String(s.current_stock != null ? s.current_stock : '—')) + ' ' + w2Escape(s.base_unit || ''))),
            '没有快用完的食材。', '去库存', 'low');
    const total = pending.length + overdue.length + dueSoon.length + low.length;
    const navCount = _fin$('navTodayCount');
    if (navCount) { navCount.textContent = String(total > 99 ? '99+' : total); navCount.classList.toggle('hide', total === 0); }
    body.querySelectorAll('[data-today]').forEach(b => {
        b.onclick = () => {
            const h = b.dataset.today;
            if (h === 'approve') {
                document.querySelector('.sidebar-btn[data-target="tab-scan"]').click();
                if (typeof setWorkbenchQueueFilter === 'function') setWorkbenchQueueFilter('to_approve');
            } else if (h === 'pay') {
                gotoPayablesList();
            } else {
                document.querySelector('.sidebar-btn[data-target="tab-inventory"]').click();
                const sel = _fin$('invFilterSelect');
                if (sel) { sel.value = 'low'; if (typeof loadInventoryData === 'function') loadInventoryData(); }
            }
        };
    });
}
window.loadToday = loadToday;

// ---------------------------------------------------------------------------
// 接入：标签页激活钩子 + 首屏初始化
// ---------------------------------------------------------------------------
(function () {
    const prev = window.onAppTabActivated;
    window.onAppTabActivated = function (target) {
        if (typeof prev === 'function') prev(target);
        if (target === 'tab-today') loadToday();
        if (target === 'tab-archive') { initArchivePanes(); }
    };
    function boot() {
        initArchivePanes();
        initTrashCard();
        let role = 'admin';
        try { role = localStorage.getItem('demo_role') || 'admin'; } catch (e) {}
        let restored = false;
        try { restored = !!sessionStorage.getItem('app.locale.restoreTab'); } catch (e) {}
        if (role === 'owner' && !restored && !location.hash) {
            const btn = _fin$('todayNavBtn');
            if (btn) btn.click();
        } else if (role !== 'staff') {
            loadTodayBadge();
        }
    }
    async function loadTodayBadge() { /* 红点由 loadToday 填充；非首页时静默拉一次 */
        try { await loadToday(); } catch (e) { /* 忽略 */ }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => setTimeout(boot, 0));
    else setTimeout(boot, 0);
})();

/* =============================================================
   U03 信用工作台（钱货与信用分配：owner 显式抵扣/退款，D-03 契约）
   -------------------------------------------------------------
   纪律（与 memoryMetrics/治理区同一条线）：
   ① 金额原样来自后端（minor/100 仅为展示换算，一次换算不重算）；null/缺失 → 「未知」，
      0 与「取不到」严格分开（0 是真实余额，未知是读不到）。
   ② 抵扣/退款只调 L06/L07 已有端点，不新增第二套账目路径；
      幂等键前端生成（crypto 随机），重复提交由后端幂等裁决。
   ③ 未分配贷项永不自动抵扣：每一笔抵扣必须 owner 在界面上显式选单+输额（D-03）。
   ============================================================= */

function creditMinorToDisplay(minor) {
    if (minor === null || minor === undefined || minor === '') return '未知';
    const n = Number(minor);
    if (!isFinite(n)) return '未知';
    return 'HK$ ' + (n / 100).toFixed(2);
}

function creditWorkbenchHtml(credits) {
    const rows = Array.isArray(credits) ? credits : [];
    let html = '<div class="credit-workbench" style="margin-top:14px;">';
    html += '<div style="font-weight:600; margin-bottom:6px;">供应商信用余额</div>';
    if (!rows.length) {
        html += '<div class="credit-empty" style="color:var(--text-muted);">当前没有未用完的供应商信用。</div>';
    } else {
        html += '<table class="data-table credit-table" style="width:100%;">';
        html += '<thead><tr>'
            + '<th>供应商</th><th>信用面额</th><th>可用余额</th><th>操作</th>'
            + '</tr></thead><tbody>';
        rows.forEach(function (c) {
            const sid = c.supplier_id;
            html += '<tr data-supplier-id="' + sid + '">'
                + '<td>' + String(c.supplier_name || ('供应商 #' + sid)) + '</td>'
                + '<td data-credit="face">' + creditMinorToDisplay(c.face_total_minor) + '</td>'
                + '<td data-credit="available">' + creditMinorToDisplay(c.available_minor) + '</td>'
                + '<td><button type="button" class="credit-allocate-btn" data-supplier-id="'
                + sid + '" style="padding:2px 10px; cursor:pointer;">退款 / 抵扣</button></td>'
                + '</tr>';
        });
        html += '</tbody></table>';
        html += '<div style="color:var(--text-muted); font-size:0.78rem; margin-top:4px;">'
            + '「抵扣」由你在弹窗中选择要抵的挂账单与金额（可部分、可跨单）；系统不会自动抵任何一单。</div>';
    }
    html += '</div>';
    return html;
}

function buildCreditAllocatePayload(supplierId, receiptId, amountMinor, idempotencyKey) {
    // 校验在前：目标/金额/幂等键缺失直接 null（调用方显示错误，不发请求）
    if (!supplierId || !receiptId) return null;
    const amt = Number(amountMinor);
    if (!isFinite(amt) || amt <= 0) return null;
    const key = String(idempotencyKey || '');
    if (!key) return null;
    return { idempotency_key: key,
             targets: [{ receipt_id: Number(receiptId), amount_minor: Math.round(amt) }] };
}

// ---------------------------------------------------------------------------
// U03：角色口径（owner 才可操作；staff 不可见财务；admin 只读不可操作）
// ---------------------------------------------------------------------------
function financeRoleNow() {
    const live = (typeof AuthState !== 'undefined' && AuthState.account && AuthState.account.role)
        ? AuthState.account.role : null;
    if (live) return live;
    try { return localStorage.getItem('demo_role') || 'admin'; } catch (e) { return 'admin'; }
}

function financeCanOperate() {
    return financeRoleNow() === 'owner';
}

function financeReadOnlyHtml() {
    if (financeCanOperate()) return '';
    return '<div class="credit-readonly" role="note" style="color:#b45309; font-size:0.8rem; margin:6px 0;">'
        + '当前角色只能查看信用与退款，登记抵扣/退款需切换到「老板」。'
        + '</div>';
}

const CREDIT_REFUND_STATE_LABELS = {
    pending: '待退款',
    awaiting_receipt: '待退款',
    processing: '退款处理中',
    received: '已收到',
    failed: '退款失败',
    cancelled: '已取消',
};
const CREDIT_REFUND_STATE_BADGES = {
    pending: 'badge-warning',
    awaiting_receipt: 'badge-warning',
    processing: 'badge-warning',
    received: 'badge-success',
    failed: 'badge-danger',
    cancelled: 'badge-secondary',
};

function refundStateLabel(state) {
    return CREDIT_REFUND_STATE_LABELS[state] || '未知状态';
}

function creditMaskedAmount() { return '$***'; }

function creditIdempotencyKey(prefix) {
    return String(prefix || 'credit') + '-' + Date.now() + '-' + Math.floor(Math.random() * 1e9);
}

function creditRowGrossMajor(r) {
    if (!r) return null;
    if (r.currency && r.currency !== 'HKD') {
        return (r.total_hkd === null || r.total_hkd === undefined) ? null : Number(r.total_hkd);
    }
    const v = (r.total_hkd !== null && r.total_hkd !== undefined) ? r.total_hkd : r.total_amount;
    return (v === null || v === undefined || v === '') ? null : Number(v);
}

function _creditSum(rows, pick) {
    return (rows || []).reduce(function (acc, r) {
        const v = pick(r);
        return acc + (isFinite(v) ? v : 0);
    }, 0);
}

// 钱货口径明示：gross / 已付 / 信用已分配 / 净欠 / 未分配
function creditBalanceSummaryHtml(bal, payableRows, allocations) {
    const b = bal || {};
    const rows = payableRows || [];
    const gross = _creditSum(rows, creditRowGrossMajor);
    const paid = _creditSum(rows, function (r) { return Number(r.paid_amount); });
    const net = _creditSum(rows, function (r) { return Number(r.outstanding_amount); });
    const activeAlloc = (allocations || []).filter(function (a) {
        return a && a.reversal_of == null && a.active !== false;
    }).reduce(function (acc, a) { return acc + (Number(a.amount_minor) || 0); }, 0);
    const missingFx = rows.some(function (r) { return creditRowGrossMajor(r) === null; });
    return '<div class="credit-summary" style="display:flex; flex-wrap:wrap; gap:12px; margin:8px 0; font-size:0.86rem;">'
        + '<span>gross（总额）：<strong>' + (missingFx ? '部分未知' : I18N.fmtMoney(gross)) + '</strong></span>'
        + '<span>已付（付款+信用）：<strong>' + I18N.fmtMoney(paid) + '</strong></span>'
        + '<span>信用已分配：<strong>' + creditMinorToDisplay(activeAlloc) + '</strong></span>'
        + '<span>净欠：<strong>' + I18N.fmtMoney(net) + '</strong></span>'
        + '<span>未分配（可用信用）：<strong>' + creditMinorToDisplay(b.available_minor) + '</strong></span>'
        + '<span>信用面额：<strong>' + creditMinorToDisplay(b.face_total_minor) + '</strong></span>'
        + '<span>退款占用：<strong>' + creditMinorToDisplay(b.allocated_refund_occupied_minor) + '</strong></span>'
        + '</div>';
}

function creditPayablesTableHtml(payableRows) {
    const rows = payableRows || [];
    if (!rows.length) {
        return '<div class="credit-empty" style="color:var(--text-muted);">这家供应商没有可抵扣的欠款单。</div>';
    }
    let html = '<div class="table-container" style="margin:6px 0;"><table class="data-table" style="width:100%;">'
        + '<thead><tr><th>欠款单</th><th>gross（总额）</th><th>已付</th><th>净欠</th></tr></thead><tbody>';
    rows.forEach(function (r) {
        const gross = creditRowGrossMajor(r);
        const rid = Number(r.receipt_id || r.id);
        html += '<tr>'
            + '<td>#' + rid + ' · ' + w2Escape(String(r.receipt_date || '').slice(0, 10)) + '</td>'
            + '<td>' + (gross === null ? '未知' : I18N.fmtMoney(gross)) + '</td>'
            + '<td>' + I18N.fmtMoney(Number(r.paid_amount) || 0) + '</td>'
            + '<td><strong>' + I18N.fmtMoney(Number(r.outstanding_amount) || 0) + '</strong></td>'
            + '</tr>';
    });
    return html + '</tbody></table></div>';
}

function creditTargetOptionsHtml(payableRows) {
    const rows = payableRows || [];
    return rows.map(function (r) {
        const rid = Number(r.receipt_id || r.id);
        const gross = creditRowGrossMajor(r);
        const label = '#' + rid + ' · ' + String(r.receipt_date || '').slice(0, 10)
            + ' · gross ' + (gross === null ? '未知' : I18N.fmtMoney(gross))
            + ' · 已付 ' + I18N.fmtMoney(Number(r.paid_amount) || 0)
            + ' · 净欠 ' + I18N.fmtMoney(Number(r.outstanding_amount) || 0);
        return '<option value="' + rid + '">' + w2Escape(label) + '</option>';
    }).join('');
}

function creditDocumentsOptionsHtml(documents, selectedId) {
    const docs = documents || [];
    return docs.map(function (d) {
        const id = Number(d.credit_id);
        const sel = String(selectedId) === String(id) ? ' selected' : '';
        return '<option value="' + id + '"' + sel + '>信用 #' + id + ' · 面额 '
            + creditMinorToDisplay(d.face_minor) + '</option>';
    }).join('');
}

function creditAllocationsHtml(allocations) {
    const active = (allocations || []).filter(function (a) {
        return a && a.reversal_of == null && a.active !== false;
    });
    if (!active.length) {
        return '<div class="credit-alloc-empty" style="color:var(--text-muted); font-size:0.8rem; margin:6px 0;">当前没有生效中的抵扣。</div>';
    }
    const dis = financeCanOperate() ? '' : ' disabled';
    let html = '<div style="font-weight:600; margin:8px 0 4px;">生效中的抵扣</div>'
        + '<div class="table-container"><table class="data-table" style="width:100%;">'
        + '<thead><tr><th>抵扣单</th><th>信用票据</th><th>目标单据</th><th>金额</th><th>时间</th><th>操作</th></tr></thead><tbody>';
    active.forEach(function (a) {
        html += '<tr>'
            + '<td>#' + Number(a.id) + '</td>'
            + '<td>信用 #' + Number(a.credit_id) + '</td>'
            + '<td>#' + Number(a.target_receipt_id) + '</td>'
            + '<td>' + creditMinorToDisplay(a.amount_minor) + '</td>'
            + '<td>' + w2Escape(String(a.created_at || '').slice(0, 16).replace('T', ' ')) + '</td>'
            + '<td><button type="button" class="credit-reverse-btn" data-credit-id="' + Number(a.credit_id)
            + '" data-allocation-id="' + Number(a.id) + '"' + dis + ' style="padding:2px 10px; cursor:pointer;">撤销</button></td>'
            + '</tr>';
    });
    return html + '</tbody></table></div>';
}

// 退款工作台：列出全部退款请求（待退款/处理中/已收/失败/取消），金额掩码 $***
function refundWorkbenchHtml(bal) {
    const b = bal || {};
    const docs = b.documents || [];
    const refunds = b.refunds || [];
    const dis = financeCanOperate() ? '' : ' disabled';
    let html = '<div class="credit-refunds" style="margin-top:12px;">'
        + '<div style="font-weight:600;">供应商退款</div>'
        + '<div style="color:var(--text-muted); font-size:0.78rem; margin:2px 0 6px;">'
        + '退款金额按隐私要求掩码显示为 ' + creditMaskedAmount() + '；待退款与已收都占用可用信用，取消/失败会释放占用。'
        + '</div>'
        + '<div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center;">'
        + '<select id="creditRefundDocument" class="form-control" style="max-width:220px;">'
        + creditDocumentsOptionsHtml(docs) + '</select>'
        + '<input id="creditRefundAmount" type="number" class="form-control" step="0.01" min="0.01" '
        + 'placeholder="退款金额（HK$）" aria-label="退款金额（HK$）" style="max-width:150px;"' + dis + '>'
        + '<button type="button" class="btn btn-secondary credit-refund-request-btn"' + dis + '>发起部分退款</button>'
        + '</div>'
        + '<div id="creditRefundError" role="alert" style="color:#b91c1c; font-size:0.8rem; min-height:1em;"></div>';
    if (!refunds.length) {
        html += '<div class="credit-refund-empty" style="color:var(--text-muted); font-size:0.82rem;">目前没有退款请求。</div>';
    } else {
        html += '<div class="table-container"><table class="data-table" style="width:100%;">'
            + '<thead><tr><th>退款单</th><th>信用票据</th><th>状态</th><th>金额</th>'
            + '<th>收到证据</th><th>创建时间</th><th>操作</th></tr></thead><tbody>';
        refunds.forEach(function (r) {
            const state = String(r.state || '');
            const canSettle = state === 'awaiting_receipt' || state === 'pending';
            let actions = '<span style="color:var(--text-muted);">—</span>';
            if (canSettle) {
                actions = '<input type="text" class="form-control credit-evidence-input" data-refund-id="'
                    + Number(r.refund_id) + '" placeholder="到账凭证号" aria-label="退款 #' + Number(r.refund_id)
                    + ' 收到证据" style="max-width:130px; display:inline-block;"' + dis + '>'
                    + '<button type="button" class="btn btn-success credit-refund-receive-btn" data-credit-id="'
                    + Number(r.credit_id) + '" data-refund-id="' + Number(r.refund_id) + '"' + dis
                    + ' style="padding:2px 8px; margin:2px;">登记已收</button>'
                    + '<button type="button" class="btn btn-secondary credit-refund-cancel-btn" data-credit-id="'
                    + Number(r.credit_id) + '" data-refund-id="' + Number(r.refund_id) + '"' + dis
                    + ' style="padding:2px 8px; margin:2px;">取消</button>';
            }
            html += '<tr data-refund-id="' + Number(r.refund_id) + '">'
                + '<td>#' + Number(r.refund_id) + '</td>'
                + '<td>信用 #' + Number(r.credit_id) + '</td>'
                + '<td><span class="badge ' + (CREDIT_REFUND_STATE_BADGES[state] || 'badge-secondary') + '">'
                + w2Escape(refundStateLabel(state)) + '</span></td>'
                + '<td>' + creditMaskedAmount() + '</td>'
                + '<td>' + w2Escape(r.received_evidence || '—') + '</td>'
                + '<td>' + w2Escape(String(r.created_at || '').slice(0, 16).replace('T', ' ')) + '</td>'
                + '<td style="white-space:nowrap;">' + actions + '</td></tr>';
        });
        html += '</tbody></table></div>';
    }
    html += financeReadOnlyHtml() + '</div>';
    return html;
}

// U03：面板装载（loadFinancePanel 链尾调用；staff 不见财务）
async function loadCreditWorkbench() {
    const box = document.getElementById('creditWorkbenchArea');
    if (!box) return;
    if (financeRoleNow() === 'staff') { box.innerHTML = ''; return; }
    box.innerHTML = '<div class="credit-loading" style="color:var(--text-muted);">正在读取信用余额……</div>';
    try {
        const body = await finApi('/api/supplier-credits');
        if (!body || body.status !== 'success') {
            box.innerHTML = '<div class="credit-error" style="color:#b91c1c;">信用余额读取失败：'
                + ((body && body.msg) || '未知错误') + '</div>';
            return;
        }
        box.innerHTML = creditWorkbenchHtml(body.data || []) + '<div id="creditDetailArea" style="margin-top:10px;"></div>';
        if (typeof box.querySelectorAll === 'function') {
            Array.prototype.forEach.call(box.querySelectorAll('.credit-allocate-btn'), function (btn) {
                btn.addEventListener('click', function () {
                    openCreditAllocateModal(Number(btn.getAttribute('data-supplier-id')));
                });
            });
        }
    } catch (e) {
        box.innerHTML = '<div class="credit-error" style="color:#b91c1c;">信用余额读取失败：网络异常</div>';
    }
}

async function openCreditWorkbench(supplierId) {
    const area = document.getElementById('creditDetailArea');
    if (!area) return;
    area.innerHTML = '<div class="credit-loading" style="color:var(--text-muted);">正在读取这家供应商的欠款单与退款……</div>';
    try {
        const [balBody, payBody] = await Promise.all([
            finApi('/api/supplier-credits/' + encodeURIComponent(supplierId)),
            finApi('/api/payables?supplier_id=' + encodeURIComponent(supplierId)),
        ]);
        if (!balBody || balBody.status !== 'success') {
            area.innerHTML = '<div class="credit-error" style="color:#b91c1c;">信用明细读取失败：'
                + ((balBody && balBody.msg) || '未知错误') + '</div>';
            return;
        }
        const rows = (payBody && payBody.data && payBody.data.rows) || [];
        renderCreditDetail(supplierId, balBody.data || {}, rows);
    } catch (e) {
        area.innerHTML = '<div class="credit-error" style="color:#b91c1c;">信用明细读取失败：'
            + w2Escape(e.message || '网络异常') + '</div>';
    }
}

function renderCreditDetail(supplierId, bal, payableRows) {
    const area = document.getElementById('creditDetailArea');
    if (!area) return;
    const docs = bal.documents || [];
    const allocations = bal.allocations || [];
    const dis = financeCanOperate() ? '' : ' disabled';
    const targetOptions = creditTargetOptionsHtml(payableRows);
    area.innerHTML = '<div class="card" style="padding:10px; border:1px solid var(--border-color, #ddd); margin-top:8px;">'
        + '<div style="font-weight:600;">信用工作台 · 供应商 #' + Number(supplierId) + '</div>'
        + creditBalanceSummaryHtml(bal, payableRows, allocations)
        + creditPayablesTableHtml(payableRows)
        + '<div style="display:flex; flex-wrap:wrap; gap:6px; align-items:center; margin-top:6px;">'
        + '<select id="creditAllocateDocument" class="form-control" style="max-width:220px;"' + dis + '>'
        + creditDocumentsOptionsHtml(docs) + '</select>'
        + '<select id="creditTargetSelect" class="form-control" style="max-width:360px;"' + dis + '>'
        + targetOptions + '</select>'
        + '<input id="creditAllocateAmount" type="number" class="form-control" step="0.01" min="0.01" '
        + 'placeholder="抵扣金额（HK$，可部分）" aria-label="抵扣金额" style="max-width:170px;"' + dis + '>'
        + '<button type="button" class="btn btn-primary credit-allocate-submit-btn"' + dis + '>部分抵扣</button>'
        + '</div>'
        + '<div id="creditAllocError" role="alert" style="color:#b91c1c; font-size:0.8rem; min-height:1em;"></div>'
        + financeReadOnlyHtml()
        + '</div>'
        + creditAllocationsHtml(allocations)
        + refundWorkbenchHtml(bal);
    bindCreditDetailActions(supplierId);
}

function bindCreditDetailActions(supplierId) {
    const area = document.getElementById('creditDetailArea');
    if (!area || typeof area.querySelectorAll !== 'function') return;
    const allocBtn = area.querySelector('.credit-allocate-submit-btn');
    if (allocBtn) allocBtn.addEventListener('click', function () { submitCreditAllocation(supplierId); });
    const refundBtn = area.querySelector('.credit-refund-request-btn');
    if (refundBtn) refundBtn.addEventListener('click', function () { submitCreditRefundRequest(supplierId); });
    Array.prototype.forEach.call(area.querySelectorAll('.credit-reverse-btn'), function (btn) {
        btn.addEventListener('click', function () {
            cancelCreditAllocation(supplierId, Number(btn.getAttribute('data-credit-id')),
                Number(btn.getAttribute('data-allocation-id')));
        });
    });
    Array.prototype.forEach.call(area.querySelectorAll('.credit-refund-cancel-btn'), function (btn) {
        btn.addEventListener('click', function () {
            cancelCreditRefund(supplierId, Number(btn.getAttribute('data-credit-id')),
                Number(btn.getAttribute('data-refund-id')));
        });
    });
    Array.prototype.forEach.call(area.querySelectorAll('.credit-refund-receive-btn'), function (btn) {
        btn.addEventListener('click', function () {
            const rid = btn.getAttribute('data-refund-id');
            const input = area.querySelector('.credit-evidence-input[data-refund-id="' + rid + '"]');
            receiveCreditRefund(supplierId, Number(btn.getAttribute('data-credit-id')),
                Number(rid), input ? input.value : '');
        });
    });
}

function creditReloadDetail(supplierId) {
    return openCreditWorkbench(supplierId);
}

function buildRefundRequestPayload(creditId, amountMinor, idempotencyKey) {
    if (!creditId) return null;
    const amt = Number(amountMinor);
    if (!isFinite(amt) || amt <= 0) return null;
    const key = String(idempotencyKey || '');
    if (!key) return null;
    return { action: 'request', amount_minor: Math.round(amt), idempotency_key: key };
}

function buildRefundSettlePayload(refundId, action, evidence) {
    if (!refundId) return null;
    if (['receive', 'cancel', 'fail'].indexOf(action) === -1) return null;
    const payload = { action: action, refund_id: Number(refundId) };
    if (action === 'receive') {
        const ev = String(evidence || '').trim();
        if (!ev) return null;
        payload.evidence = ev;
    }
    return payload;
}

function buildCreditReversalPayload(allocationId, idempotencyKey) {
    if (!allocationId) return null;
    const key = String(idempotencyKey || '');
    if (!key) return null;
    return { idempotency_key: key, reversal_of: Number(allocationId) };
}

function _creditSubmitError(boxId, msg) {
    const el = document.getElementById(boxId);
    if (el) el.textContent = msg || '';
}

// 发起部分退款：校验/请求失败都保留输入草稿；成功后才清空并刷新
function submitCreditRefundRequest(supplierId) {
    if (!financeCanOperate()) {
        showToast('权限不足：只有老板可以登记退款。', 'error');
        return Promise.resolve(false);
    }
    const docEl = document.getElementById('creditRefundDocument');
    const amtEl = document.getElementById('creditRefundAmount');
    const creditId = docEl ? Number(docEl.value) : 0;
    const amountMinor = amtEl ? Math.round(Number(amtEl.value) * 100) : 0;
    const payload = buildRefundRequestPayload(creditId, amountMinor, creditIdempotencyKey('refund'));
    if (!payload) {
        _creditSubmitError('creditRefundError', '请选择信用票据并填写大于 0 的退款金额。');
        return Promise.resolve(false);
    }
    _creditSubmitError('creditRefundError', '');
    return finApi('/api/supplier-credits/' + encodeURIComponent(creditId) + '/refunds', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(function (body) {
        const data = (body && body.data) || {};
        if (data.applied === false) {
            showToast(data.message || '该退款请求已存在（幂等回放）。', 'info');
        } else {
            showToast('已登记退款请求，退款到账前先占用可用信用。', 'success');
        }
        if (amtEl) amtEl.value = '';
        creditReloadDetail(supplierId);
        return true;
    }).catch(function (err) {
        _creditSubmitError('creditRefundError', (err && err.message) || '退款请求没有成功，请稍后再试。');
        return false;
    });
}

function cancelCreditRefund(supplierId, creditId, refundId) {
    if (!financeCanOperate()) {
        showToast('权限不足：只有老板可以取消退款。', 'error');
        return Promise.resolve(false);
    }
    const payload = buildRefundSettlePayload(refundId, 'cancel');
    const ask = (typeof uiConfirm === 'function')
        ? uiConfirm({ title: '取消退款请求', message: '取消后会释放这笔退款占用的信用，确定吗？',
                      confirmText: '取消退款', danger: true })
        : Promise.resolve(true);
    return Promise.resolve(ask).then(function (ok) {
        if (!ok) return false;
        return finApi('/api/supplier-credits/' + encodeURIComponent(creditId) + '/refunds', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).then(function () {
            showToast('已取消退款请求，占用信用已释放。', 'success');
            creditReloadDetail(supplierId);
            return true;
        }).catch(function (err) {
            _creditSubmitError('creditRefundError', (err && err.message) || '取消退款失败。');
            return false;
        });
    });
}

function receiveCreditRefund(supplierId, creditId, refundId, evidence) {
    if (!financeCanOperate()) {
        showToast('权限不足：只有老板可以登记退款到账。', 'error');
        return Promise.resolve(false);
    }
    const payload = buildRefundSettlePayload(refundId, 'receive', evidence);
    if (!payload) {
        _creditSubmitError('creditRefundError', '登记已收必须填写收到证据（到账凭证号）。');
        return Promise.resolve(false);
    }
    return finApi('/api/supplier-credits/' + encodeURIComponent(creditId) + '/refunds', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(function () {
        showToast('已登记退款到账。', 'success');
        creditReloadDetail(supplierId);
        return true;
    }).catch(function (err) {
        _creditSubmitError('creditRefundError', (err && err.message) || '登记退款到账失败。');
        return false;
    });
}

// 部分抵扣：409 超额时保留已选单据与输入金额（不清草稿）
function submitCreditAllocation(supplierId) {
    if (!financeCanOperate()) {
        showToast('权限不足：只有老板可以做信用抵扣。', 'error');
        return Promise.resolve(false);
    }
    const docEl = document.getElementById('creditAllocateDocument');
    const targetEl = document.getElementById('creditTargetSelect');
    const amtEl = document.getElementById('creditAllocateAmount');
    const creditId = docEl ? Number(docEl.value) : 0;
    const receiptId = targetEl ? Number(targetEl.value) : 0;
    const amountMinor = amtEl ? Math.round(Number(amtEl.value) * 100) : 0;
    const payload = buildCreditAllocatePayload(creditId, receiptId, amountMinor, creditIdempotencyKey('alloc'));
    if (!payload) {
        _creditSubmitError('creditAllocError', '请选择信用票据与欠款单，并填写大于 0 的抵扣金额。');
        return Promise.resolve(false);
    }
    _creditSubmitError('creditAllocError', '');
    return finApi('/api/supplier-credits/' + encodeURIComponent(creditId) + '/allocations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(function (body) {
        const data = (body && body.data) || {};
        if (data.applied === false) {
            showToast(data.message || '该请求已生效（幂等回放）。', 'info');
        } else {
            showToast('抵扣成功：可用信用 ' + creditMinorToDisplay(data.available_minor), 'success');
        }
        if (amtEl) amtEl.value = '';
        creditReloadDetail(supplierId);
        return true;
    }).catch(function (err) {
        // 409/余额不足：保留草稿，交给用户改金额后重试
        _creditSubmitError('creditAllocError', (err && err.message) || '抵扣请求失败，请稍后再试。');
        return false;
    });
}

function cancelCreditAllocation(supplierId, creditId, allocationId) {
    if (!financeCanOperate()) {
        showToast('权限不足：只有老板可以撤销抵扣。', 'error');
        return Promise.resolve(false);
    }
    const payload = buildCreditReversalPayload(allocationId, creditIdempotencyKey('reverse'));
    const ask = (typeof uiConfirm === 'function')
        ? uiConfirm({ title: '撤销抵扣', message: '撤销后这笔信用会回到可用余额，确定吗？',
                      confirmText: '撤销抵扣', danger: true })
        : Promise.resolve(true);
    return Promise.resolve(ask).then(function (ok) {
        if (!ok) return false;
        return finApi('/api/supplier-credits/' + encodeURIComponent(creditId) + '/allocations', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        }).then(function () {
            showToast('已撤销该笔抵扣。', 'success');
            creditReloadDetail(supplierId);
            return true;
        }).catch(function (err) {
            _creditSubmitError('creditAllocError', (err && err.message) || '撤销抵扣失败。');
            return false;
        });
    });
}

function openCreditAllocateModal(supplierId) {
    openCreditWorkbench(supplierId);
}
