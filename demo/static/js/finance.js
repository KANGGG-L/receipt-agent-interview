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
function payPaidAmount(r) {
    if (r && r.paid_amount != null && r.paid_amount !== '') return _round2(r.paid_amount);
    return r && r.payment_id ? _round2(r.total_amount) : 0;
}
function payOutstandingAmount(r) {
    return Math.max(0, _round2(_round2(r && r.total_amount) - payPaidAmount(r)));
}
function payAllocStatusLabel(r) {
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
        const out = payOutstandingAmount(r);
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><input type="checkbox" class="pay-receipt-check" value="${Number(r.id)}" aria-label="选择单据 #${Number(r.id)}" checked></td>
            <td>#${Number(r.id)}</td>
            <td>${renderDateCell(r.receipt_date)}</td>
            <td>${I18N.fmtMoney(r.total_amount)}</td>
            <td>${I18N.fmtMoney(payPaidAmount(r))}</td>
            <td><strong>${I18N.fmtMoney(out)}</strong></td>
            <td><input type="number" class="form-control pay-amt-input" data-rid="${Number(r.id)}" data-max="${out}" step="0.01" min="0.01" max="${out}" value="${out.toFixed(2)}" aria-label="单据 #${Number(r.id)} 本次付款金额" style="min-height:40px; width:110px;"></td>
            <td>${w2Escape(payAllocStatusLabel(r))}</td>`;
        body.appendChild(tr);
    });
    body.querySelectorAll('.pay-receipt-check, .pay-amt-input').forEach(el => {
        el.addEventListener('input', recalcPayTotal);
        el.addEventListener('change', recalcPayTotal);
    });
    _fin$('payCheckAll').checked = true;
    recalcPayTotal();
}

function togglePayCheckAll(cb) {
    document.querySelectorAll('.pay-receipt-check').forEach(c => { c.checked = cb.checked; });
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
