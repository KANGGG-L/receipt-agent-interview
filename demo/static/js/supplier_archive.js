// -*- coding: utf-8 -*-
// 供应商档案 + 归档工作台 + 待办条（F3 B6-1 自 main.js 抽出，函数体逐字节未改）。
// 依赖 main.js 的 apiFetch / w2Escape / showToast / isOwnerRoleNow / closeModalById / toastFailDetail /
//   loadSuppliersData / loadReceiptsHistory / applyArchiveFilters / applyPayablesFilters /
//   STATUS_GROUP_MAP / allArchiveReceipts / getTodayDateStr。
// 供应商合并实现见 finance.js。加载顺序：main.js -> supplier_archive.js（index.html 中紧跟 main.js 之后）。

// ---- 供应商档案与归档工作台（09 方案：M1/M2/M3/M4/M6/M7 + S1/S2/S4） ----
const SupplierUI = { q: '', includeInactive: false };
const ArchiveUI = { q: '', statusGroup: '', quickRange: '' };

function loadSupplierAdmin() {
    // 与库存档案对称：从 checkbox 同步 includeInactive，避免「显示已停用」勾了却不带 query
    const chkInc = document.getElementById('supShowInactive');
    if (chkInc) SupplierUI.includeInactive = !!chkInc.checked;
    const params = new URLSearchParams();
    if (SupplierUI.q) params.set('q', SupplierUI.q);
    if (SupplierUI.includeInactive) params.set('include_inactive', '1');
    apiFetch('/api/suppliers?' + params.toString())
        .then(res => {
            if (!res || !res.ok) throw new Error('suppliers_http_' + ((res && res.status) || 'net'));
            return res.json();
        })
        .then(ret => {
            if (ret.status !== 'success') return;
            window.__supplierCache = ret.data || [];
            renderSupplierAdminRows(window.__supplierCache);
            // 仅刷新待办条展示口径；若归档 KPI 仍失败则 updateTodoBar 自行短路。
            updateTodoBar();
        })
        .catch(err => {
            console.warn('[supplier] 供应商档案加载失败：', err);
        });
}

function renderSupplierAdminRows(suppliers) {
    const body = document.getElementById('supplierAdminBody');
    const empty = document.getElementById('supplierEmpty');
    if (!body) return;
    body.innerHTML = '';
    if (suppliers.length === 0) {
        if (empty) empty.classList.remove('hide');
        return;
    }
    if (empty) empty.classList.add('hide');

    suppliers.forEach(s => {
        const active = (s.active === 1 || s.active === undefined) ? 1 : 0;
        const statusBadge = active
            ? '<span class="badge badge-success">启用</span>'
            : '<span class="badge badge-secondary">已停用</span>';
        const overdueBadge = (s.is_overdue && active)
            ? ' <span class="badge badge-danger">逾期</span>' : '';

        const termsText = (s.payment_terms_days != null && s.payment_terms_days !== '')
            ? `${s.payment_terms_days} 天` : '现结';

        const prefText = ({cash:'现结', credit:'挂账', mixed:'混合'})[s.settlement_pref] || '';

        // F5-L2：店员隐藏 owner-only 行内写操作（查看单据 / 编辑 / 更多）。
        // 查看单据=GET /api/suppliers/{id}/receipts、编辑=PATCH /api/suppliers/{id}、
        // 更多内「停用/启用/合并」亦为 owner 域，后端均 require_role("owner")，店员点击必 403。
        const supplierActionsHtml = isOwnerRoleNow() ? `
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="viewSupplierReceipts(${Number(s.id)})">查看单据</button>
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="openSupplierModal(${Number(s.id)})">编辑</button>
                <button class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="event.stopPropagation(); toggleSupplierMore(this, ${Number(s.id)}, ${active})">更多 ▾</button>` : '';

        const tr = document.createElement('tr');
        if (!active) tr.className = 'sup-row-inactive';
        tr.innerHTML = `
            <td>
                <div style="font-weight:600;">${w2Escape(s.name || '（未命名供应商）')}</div>
                <div class="sup-code-weak">${w2Escape(s.supplier_code || '-')}${prefText ? ' · ' + w2Escape(prefText) : ''}</div>
            </td>
            <td class="col-center">${w2Escape(termsText)}</td>
            <td class="col-center">${Number(s.receipt_count) || 0}</td>
            <td>
                <div class="supplier-unpaid-stack">
                    <span class="supplier-unpaid-amount ${s.unpaid_credit_total > 0 ? 'is-unpaid' : 'is-paid'}">${I18N.fmtMoney(s.unpaid_credit_total)}</span>
                    <span class="supplier-unpaid-count">${s.unpaid_credit_count} 张${overdueBadge}</span>
                </div>
            </td>
            <td>${w2Escape(s.contact_phone || '-')}</td>
            <td class="col-center">${statusBadge}</td>
            <td class="col-center">
                ${supplierActionsHtml}
            </td>`;
        body.appendChild(tr);
    });
}

function onSupplierSearchInput() {
    SupplierUI.q = (document.getElementById('supSearch')?.value || '').trim();
    loadSupplierAdmin();
}

// ---- 新增 / 编辑 Modal ----
let _editSupplierId = null;

function openSupplierModal(id) {
    _editSupplierId = (id != null && id !== undefined) ? Number(id) : null;
    const title = document.getElementById('supplierModalTitle');
    const errBox = document.getElementById('supplierModalError');
    if (errBox) errBox.classList.add('hide');
    ['supName', 'supPhone', 'supNotes', 'supPref', 'supTerms'].forEach(id => document.getElementById(id).value = '');
    document.getElementById('supCodePreview').innerHTML = '新增后自动生成';
    setSupplierTerms('30'); // 默认 30 天（与 upsert 一致）

    if (_editSupplierId != null) {
        const sup = (window.__supplierCache || []).find(s => s.id === _editSupplierId);
        if (sup) {
            title.textContent = '编辑供应商';
            document.getElementById('supName').value = sup.name || '';
            document.getElementById('supPhone').value = sup.contact_phone || '';
            document.getElementById('supNotes').value = sup.notes || '';
            document.getElementById('supPref').value = sup.settlement_pref || '';
            const t = (sup.payment_terms_days != null) ? String(sup.payment_terms_days) : '';
            document.getElementById('supTerms').value = t;
            setSupplierTermsActive(t);
            document.getElementById('supCodePreview').textContent = sup.supplier_code || '-';
        }
    } else {
        title.textContent = '新增供应商';
    }
    document.getElementById('supplierModal').classList.remove('hide');
    setTimeout(() => document.getElementById('supName').focus(), 50);
}

function setSupplierTerms(val) {
    const input = document.getElementById('supTerms');
    if (input) input.value = (val === '' || val == null) ? '' : String(val);
    setSupplierTermsActive(val);
}

function setSupplierTermsActive(val) {
    document.querySelectorAll('#supplierModal [data-terms]').forEach(c => {
        c.classList.toggle('active', c.getAttribute('data-terms') === String(val));
    });
}

function saveSupplier() {
    const name = (document.getElementById('supName').value || '').trim();
    const phone = (document.getElementById('supPhone').value || '').trim();
    const notes = (document.getElementById('supNotes').value || '').trim();
    const pref = document.getElementById('supPref').value || '';
    const termsRaw = (document.getElementById('supTerms').value || '').trim();
    const errBox = document.getElementById('supplierModalError');

    if (!name) {
        if (errBox) { errBox.textContent = '供应商名称必填'; errBox.classList.remove('hide'); }
        return;
    }
    let terms = null;
    if (termsRaw !== '') {
        const n = Number(termsRaw);
        if (!Number.isInteger(n) || n < 0) {
            if (errBox) { errBox.textContent = '账期必须为非负整数'; errBox.classList.remove('hide'); }
            return;
        }
        terms = n;
    }

    const payload = { name, contact_phone: phone, notes: notes,
                       settlement_pref: pref || null, payment_terms_days: terms };

    const isEdit = _editSupplierId != null;
    const origSup = isEdit ? (window.__supplierCache || []).find(s => s.id === _editSupplierId) : null;
    const origTerms = origSup ? origSup.payment_terms_days : undefined;
    const termsChanged = isEdit && ((origTerms ?? null) !== (terms ?? null));
    const url = isEdit ? `/api/suppliers/${_editSupplierId}` : '/api/suppliers';
    const method = isEdit ? 'PATCH' : 'POST';

    apiFetch(url, {
        method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    })
        .then(res => res.json().then(d => ({ status: res.status, body: d })))
        .then(({ status, body }) => {
            if (status === 409) {
                if (errBox) { errBox.textContent = (body.msg || '已存在同名供应商') ; errBox.classList.remove('hide'); }
                return;
            }
            if (status === 400) {
                if (errBox) { errBox.textContent = body.msg || '参数有误'; errBox.classList.remove('hide'); }
                return;
            }
            if (!body || body.status !== 'success') {
                if (errBox) { errBox.textContent = (body && body.msg) || '保存失败'; errBox.classList.remove('hide'); }
                return;
            }
            let msg = isEdit ? '供应商已更新' : `供应商「${name}」已创建`;
            if (termsChanged) msg += '；账期仅影响之后的新单，不回溯旧单预期付款日';
            showToast(msg, 'success');
            closeModalById('supplierModal');
            loadSupplierAdmin();
            loadSuppliersData();   // 刷新复核 combobox
            loadReceiptsHistory(); // 表头供应商名联动
        })
        .catch(err => {
            console.error('保存供应商异常', err);
            if (errBox) { errBox.textContent = '保存失败，请稍后重试'; errBox.classList.remove('hide'); }
        });
}

async function deactivateSupplier(id) {
    closeSupplierMore();
    const okSup = await uiConfirm({
        title: '停用供应商',
        message: '停用这家供应商？\n停用后新单不会再自动归到它名下；以前的单据都还在，勾选「显示已停用」可以看到，也可以随时重新启用。',
        confirmText: '停用',
        danger: true,
    });
    if (!okSup) return;
    patchSupplierActive(id, 0);
}

function activateSupplier(id) {
    closeSupplierMore();
    patchSupplierActive(id, 1);
}

function patchSupplierActive(id, active) {
    apiFetch(`/api/suppliers/${Number(id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ active }),
    })
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') { showToast((ret && ret.msg) || '操作失败', 'error'); return; }
            showToast(active ? '已启用供应商' : '已停用供应商', 'success');
            loadSupplierAdmin();
            loadSuppliersData();
            loadReceiptsHistory();
        })
        .catch(err => { console.error('供应商启停异常', err); showToast('操作失败' + toastFailDetail(err), 'error'); });
}

// ---- 更多菜单（停用/启用/合并，D-SUP-12） ----
let _supMoreMenuEl = null;

function toggleSupplierMore(btn, id, active) {
    if (_supMoreMenuEl) { closeSupplierMore(); return; }
    const menu = document.createElement('div');
    menu.id = 'supMoreMenu';
    menu.style.cssText = 'position:fixed; z-index:1200; background:#fff; border:1px solid var(--border-color,#d8dce0);'
        + ' border-radius:8px; box-shadow:0 6px 20px rgba(0,0,0,.15); min-width:140px; padding:6px;';
    const items = [];
    if (active === 1) {
        items.push(`<div class="sup-more-item" onclick="deactivateSupplier(${Number(id)})">停用</div>`);
    } else {
        items.push(`<div class="sup-more-item" onclick="activateSupplier(${Number(id)})">启用</div>`);
    }
    items.push(`<div class="sup-more-item" onclick="closeSupplierMore(); openMergeModal(${Number(id)})">合并重复供应商…</div>`);
    menu.innerHTML = items.join('');
    document.body.appendChild(menu);
    const rect = btn.getBoundingClientRect();
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = Math.max(4, rect.right - menu.offsetWidth) + 'px';
    menu.querySelectorAll('.sup-more-item').forEach(el => {
        el.style.cssText = 'padding:7px 10px; font-size:0.82rem; cursor:pointer; border-radius:6px;';
        el.onmouseenter = () => el.style.background = 'var(--hover-bg,#f1f5f9)';
        el.onmouseleave = () => el.style.background = 'transparent';
    });
    _supMoreMenuEl = menu;
    setTimeout(() => document.addEventListener('click', closeSupplierMoreOutside, true), 0);
}

function closeSupplierMoreOutside(e) {
    if (_supMoreMenuEl && !_supMoreMenuEl.contains(e.target)) closeSupplierMore();
}

function closeSupplierMore() {
    if (_supMoreMenuEl) { _supMoreMenuEl.remove(); _supMoreMenuEl = null; }
    document.removeEventListener('click', closeSupplierMoreOutside, true);
}

// ---- 查看单据：一键筛选该供应商并滚动（D-SUP-9） ----
function viewSupplierReceipts(id) {
    const sup = (window.__supplierCache || []).find(s => s.id === Number(id));
    const name = sup ? sup.name : '';
    const sel = document.getElementById('fltSupplier');
    if (sel) {
        // 确保选项存在（无单据的供应商也可能不在下拉里）
        let opt = Array.from(sel.options).find(o => o.value === name);
        if (!opt && name) {
            opt = document.createElement('option');
            opt.value = name; opt.textContent = name;
            sel.appendChild(opt);
        }
        sel.value = name;
    }
    if (typeof showArchivePane === 'function') showArchivePane('docs');
    // 清空其它归档筛选，只看该家
    document.getElementById('fltKeyword').value = '';
    ArchiveUI.q = '';
    setArchiveStatusGroup('');
    setArchiveQuickRange('');
    applyArchiveFilters();
    const arcCard = document.getElementById('archiveRecordCount');
    if (arcCard) arcCard.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
    if (name) showToast(`已筛选「${name}」的单据`, 'info');
}

// ---- 待办条（D-SUP-10 / M7 / UAT-002） ----
function updateTodoBar() {
    // UAT-036：归档 KPI 已处于失败「—」时，禁止用空 allArchiveReceipts 刷成假 0
    //（例如供应商档案成功回调里顺带调用本函数）。
    const retry = document.getElementById('archiveKpiRetry');
    const pendingProbe = document.getElementById('todoPendingNum');
    if (retry && !retry.classList.contains('hide')
        && pendingProbe && String(pendingProbe.textContent).trim() === '—') {
        return;
    }
    const recs = allArchiveReceipts || [];
    let pending = 0, failed = 0, overdue = 0;
    recs.forEach(r => {
        const st = r.status || 'uploaded';
        if (STATUS_GROUP_MAP.pending.includes(st)) pending++;
        else if (st === 'error') failed++;
        if (r.payment_status === 'overdue' || r.is_overdue === true) overdue++;
    });
    setText('todoPendingNum', pending);
    setText('todoFailedNum', failed);
    setText('todoOverdueNum', overdue);

    // UAT-002: 口径可见性（5态枚举）。标签文案以 index.html 源头为准，禁止按 DOM 文本 shim。
    // title 必须同时保留「点击筛选」提示与统计口径说明（不得无条件覆盖 HTML title）。
    const pendingEl = document.getElementById('todoPendingNum');
    if (pendingEl) {
        const card = pendingEl.closest('[data-todo-metric="pending"]') || pendingEl.closest('.metric-card');
        if (card) {
            const statsPart = '统计口径：归档待处理全量单据（包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种待处理状态）';
            const clickHint = (card.getAttribute('data-click-hint') || '').trim()
                || '点击筛选待处理单据';
            card.title = clickHint + '。' + statsPart;
            card.setAttribute('aria-label', '归档待处理全量单据：包含已上传、识别中、待核对、待核对（有疑问）、待老板审批共5种状态，共' + pending + '张。' + clickHint);
        }
    }
}

function todoFilter(type) {
    if (typeof showArchivePane === 'function') showArchivePane(type === 'overdue' ? 'payables' : 'docs');
    if (type === 'overdue') {
        const sel = document.getElementById('fltFinDue');
        if (sel) { sel.value = 'overdue'; if (typeof applyPayablesFilters === 'function') applyPayablesFilters(); }
        const card = sel ? sel.closest('.card') : null;
        if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return;
    }
    setArchiveStatusGroup(type === 'failed' ? 'failed' : 'pending');
    const arc = document.getElementById('archiveRecordCount');
    if (arc) arc.closest('.card').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ---- 归档关键字 / 状态聚合 / 时间快捷（M5 / S2 / S4） ----
function onArchiveKeywordInput() {
    ArchiveUI.q = (document.getElementById('fltKeyword')?.value || '').trim();
    applyArchiveFilters();
}

function setArchiveStatusGroup(g) {
    ArchiveUI.statusGroup = g;
    document.querySelectorAll('[data-group]').forEach(c =>
        c.classList.toggle('active', c.getAttribute('data-group') === g));
    applyArchiveFilters();
}

function setArchiveQuickRange(r) {
    ArchiveUI.quickRange = r;
    document.querySelectorAll('[data-range]').forEach(c =>
        c.classList.toggle('active', c.getAttribute('data-range') === r));
    applyQuickRange(r);
    applyArchiveFilters();
}

function applyQuickRange(r) {
    const from = document.getElementById('fltReceiptDateFrom');
    const to = document.getElementById('fltReceiptDateTo');
    if (!from || !to) return;
    const today = getTodayDateStr();
    if (!r) { from.value = ''; to.value = ''; return; }
    const d = new Date();
    let start;
    if (r === 'week') {
        const diff = (d.getDay() + 6) % 7; // 周一为一周起点
        start = new Date(d); start.setDate(d.getDate() - diff);
    } else if (r === 'month') {
        start = new Date(d.getFullYear(), d.getMonth(), 1);
    } else if (r === 'last_month') {
        const s = new Date(d.getFullYear(), d.getMonth() - 1, 1);
        const e = new Date(d.getFullYear(), d.getMonth(), 0);
        from.value = fmtDateLocal(s); to.value = fmtDateLocal(e);
        return;
    }
    from.value = fmtDateLocal(start); to.value = today;
}

function fmtDateLocal(d) {
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${y}-${m}-${day}`;
}

function toggleArchiveAdvanced() {
    const el = document.getElementById('archiveAdvanced');
    const toggle = document.getElementById('archiveAdvancedToggle');
    if (!el) return;
    const hidden = el.classList.toggle('hide');
    if (toggle) toggle.textContent = hidden ? '高级筛选 ▾' : '高级筛选 ▴';
}

function setText(id, val) {
    const el = document.getElementById(id);
    if (el) el.textContent = val;
}

// ---- 供应商合并：见 finance.js ----

