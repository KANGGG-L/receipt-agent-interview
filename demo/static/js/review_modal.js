// demo/static/js/review_modal.js
// 审核入账确认弹窗与全局无障碍模态管理（Category 01，AC-01-02/03/06 与 AC-08-05）：
        // 焦点受控、别名/日期/金额/明细数/问题数/库存后果列示。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// =============================================================
// Category 01: 审核入账确认弹窗与全局无障碍模态管理（AC-01-02, AC-01-03, AC-01-06, AC-08-05）
// 列示别名、日期、$***、明细数、问题数与库存后果；焦点受控无泄漏
// =============================================================

let _activeAccessibleModals = [];

function getModalFocusableElements(modal) {
    if (!modal) return [];
    const selector = 'button:not([disabled]):not([aria-hidden="true"]), ' +
                     '[href]:not([aria-hidden="true"]), ' +
                     'input:not([disabled]):not([type="hidden"]):not([aria-hidden="true"]), ' +
                     'select:not([disabled]):not([aria-hidden="true"]), ' +
                     'textarea:not([disabled]):not([aria-hidden="true"]), ' +
                     '[tabindex]:not([tabindex="-1"]):not([aria-hidden="true"])';
    const elements = Array.from(modal.querySelectorAll(selector));
    return elements.filter(el => {
        return el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0;
    });
}
window.getModalFocusableElements = getModalFocusableElements;

function openAccessibleModal(modal, preferredFocusSelector, explicitTrigger) {
    if (!modal) return;
    const triggerEl = explicitTrigger || document.activeElement;
    modal.classList.remove('hide');
    modal.setAttribute('role', 'dialog');
    modal.setAttribute('aria-modal', 'true');

    _activeAccessibleModals.push({
        modal: modal,
        trigger: triggerEl
    });

    const setFocus = () => {
        let target = null;
        if (preferredFocusSelector) {
            target = modal.querySelector(preferredFocusSelector);
        }
        if (!target) {
            const focusables = getModalFocusableElements(modal);
            target = focusables.find(el => el.id && (el.id.includes('Cancel') || el.id.includes('btnCancel'))) || focusables[0];
        }
        if (target && typeof target.focus === 'function') {
            target.focus();
        } else {
            modal.setAttribute('tabindex', '-1');
            modal.focus();
        }
    };
    setFocus();
    setTimeout(setFocus, 30);
}
window.openAccessibleModal = openAccessibleModal;

function closeAccessibleModal(modal) {
    if (!modal) return;
    modal.classList.add('hide');
    const idx = _activeAccessibleModals.findIndex(item => item.modal === modal);
    if (idx !== -1) {
        const item = _activeAccessibleModals.splice(idx, 1)[0];
        if (item && item.trigger && typeof item.trigger.focus === 'function') {
            try { item.trigger.focus(); } catch (e) {}
        }
    }
}
window.closeAccessibleModal = closeAccessibleModal;

let pendingApproveReceiptId = null;
let pendingApproveVersion = null;

function openApproveConfirmModalFromQueue(receiptId) {
    const validId = receiptId ? Number(receiptId) : null;
    if (!validId || isNaN(validId) || validId <= 0) return;
    apiFetch(`/api/receipt/${validId}`)
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                const data = ret.data;
                data.id = validId;
                data.receipt_id = validId;
                openApproveConfirmModal(validId, data);
            } else {
                showToast('读取单据详情失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            }
        })
        .catch(err => {
            showToast('读取单据详情失败，请检查网络设置', 'error');
        });
}
window.openApproveConfirmModalFromQueue = openApproveConfirmModalFromQueue;

function openApproveConfirmModal(receiptId, data, explicitTrigger) {
    // AC-01-06: 权限校验，店员无审批入口且阻断
    if (typeof currentRole !== 'undefined' && currentRole === 'staff') {
        showToast('店员无审批入账权限，请联系老板登录审批', 'warning');
        return;
    }

    const validId = receiptId ? Number(receiptId) : null;
    if (!validId || isNaN(validId) || validId <= 0) {
        showToast('未选择或打开有效单据，无法审核入账', 'warning');
        return;
    }

    pendingApproveReceiptId = validId;
    pendingApproveVersion = (data && data.version != null) ? data.version : (currentReceiptData ? currentReceiptData.version : null);

    const titleEl = document.getElementById('approveConfirmTitle');
    if (titleEl) titleEl.textContent = `确认单据 #${validId} 审核入账`;

    const receiptIdEl = document.getElementById('approveConfirmReceiptId');
    if (receiptIdEl) receiptIdEl.textContent = `#${validId}`;

    const supplierEl = document.getElementById('approveConfirmSupplier');
    const dateEl = document.getElementById('approveConfirmDate');
    const totalEl = document.getElementById('approveConfirmTotal');
    const itemsEl = document.getElementById('approveConfirmItemCount');
    const itemPreviewEl = document.getElementById('approveConfirmItemPreview');
    const issueEl = document.getElementById('approveConfirmIssueCount');
    const blockerWarn = document.getElementById('approveConfirmBlockerWarn');
    const submitBtn = document.getElementById('btnExecuteApproveConfirm');

    if (supplierEl) supplierEl.textContent = (data && data.supplier_name) || 'Vendor_A';
    if (dateEl) dateEl.textContent = (data && data.date) || '2026-10-08';
    if (totalEl) totalEl.textContent = (data && data.total_amount) ? `$${Number(data.total_amount).toFixed(2)}` : '$***';
    const itemsCount = (data && data.items) ? data.items.filter(it => !it.is_void).length : 0;
    if (itemsEl) itemsEl.textContent = `${itemsCount} 项`;

    if (itemPreviewEl) {
        let previewText = '--';
        if (data && data.items && data.items.length > 0) {
            const validItems = data.items.filter(it => !it.is_void);
            if (validItems.length > 0) {
                const first = validItems[0].name || validItems[0].raw_name || '';
                previewText = validItems.length > 1 ? `${first} 等 ${validItems.length} 项` : first;
            }
        }
        itemPreviewEl.textContent = previewText || '--';
    }

    // 计算是否有算术或必填阻断 (AC-05-04)
    const issues = calculateReceiptIssues(data);
    const blockers = issues.filter(i => i.type === 'blocker');
    if (issueEl) {
        if (issues.length === 0) {
            issueEl.textContent = '0 处（数据齐全）';
            issueEl.style.color = 'var(--success, #16a34a)';
        } else {
            issueEl.textContent = `${issues.length} 处（需修改 ${blockers.length} 处）`;
            issueEl.style.color = blockers.length > 0 ? 'var(--danger, #dc2626)' : 'var(--warning, #d97706)';
        }
    }

    if (blockers.length > 0) {
        if (blockerWarn) blockerWarn.classList.remove('hide');
        if (submitBtn) submitBtn.disabled = true;
    } else {
        if (blockerWarn) blockerWarn.classList.add('hide');
        if (submitBtn) submitBtn.disabled = false;
    }

    const modal = document.getElementById('modalApproveConfirmDialog');
    if (modal) {
        const triggerEl = explicitTrigger || document.getElementById('btnApproveReceipt') || document.activeElement;
        openAccessibleModal(modal, '#btnCancelApproveConfirm', triggerEl);
    }
}
window.openApproveConfirmModal = openApproveConfirmModal;

function onWorkbenchApproveClick() {
    const triggerBtn = document.getElementById('btnApproveReceipt') || document.activeElement;

    if (typeof currentRole !== 'undefined' && currentRole === 'staff') {
        showToast('店员无审批入账权限，请联系老板登录审批', 'warning');
        return;
    }

    const rawId = (currentReceiptData && (currentReceiptData.id || currentReceiptData.receipt_id)) || currentReceiptId;
    const receiptId = rawId ? Number(rawId) : null;
    if (!receiptId || isNaN(receiptId) || receiptId <= 0) {
        showToast('未选择或打开有效单据，无法审核入账', 'warning');
        return;
    }

    // S1-P1-05：系统读出来但还没人核对过的单，不能直接入账
    const _stNow = String((currentReceiptData && currentReceiptData.status) || '');
    if (isReceiptPendingHumanReview(_stNow)) {
        showToast('这张单还没有人核对过。请先对照原图核对，再点「保存并提交审核」，然后才能「审核入账」', 'warning', 7000);
        const _saveBtn0 = document.getElementById('btnSaveReview');
        if (_saveBtn0) _saveBtn0.focus();
        return;
    }
    if (_stNow === 'flagged') {
        showToast('这张单被标记了有问题，不能入账。请先点「解除异常标记」，确认没问题后再入账', 'warning', 6000);
        return;
    }

    let data;
    try { data = collectReviewFormData(); } catch (e) { data = currentReceiptData || {}; }
    data.id = receiptId;
    data.receipt_id = receiptId;

    // 必填与算术阻断检查 (AC-05-04)
    const issues = calculateReceiptIssues(data);
    const blockers = issues.filter(i => i.type === 'blocker');
    if (blockers.length > 0) {
        showToast(`这张单还有 ${blockers.length} 处必须先改的地方，改好并保存后再审核入账`, 'error');
        currentReceiptIssues = issues;
        currentIssueFocusIndex = -1;
        focusNextReceiptIssue();
        return;
    }

    // AC-01-03: 未保存修改时安全阻断并提示先保存，杜绝静默预保存破坏取消零写入语义
    if (isReviewFormDirty()) {
        showToast('单据修改尚未保存，请先点「保存并提交审核」，再点「审核入账」', 'warning');
        const saveBtn = document.getElementById('btnSaveReview');
        if (saveBtn) saveBtn.focus();
        return;
    }

    const targetData = Object.assign({}, currentReceiptData || {}, data);
    targetData.id = receiptId;
    targetData.receipt_id = receiptId;
    if (currentReceiptData && currentReceiptData.version != null) {
        targetData.version = currentReceiptData.version;
    }

    openApproveConfirmModal(receiptId, targetData, triggerBtn);
}
window.onWorkbenchApproveClick = onWorkbenchApproveClick;

function closeApproveConfirmModal() {
    const modal = document.getElementById('modalApproveConfirmDialog');
    if (modal) {
        closeAccessibleModal(modal);
    }
    pendingApproveReceiptId = null;
    pendingApproveVersion = null;
}
window.closeApproveConfirmModal = closeApproveConfirmModal;

function executeConfirmedApproval() {
    if (!pendingApproveReceiptId) {
        showToast('未选择或打开有效单据，无法审核入账', 'warning');
        return;
    }

    const receiptId = Number(pendingApproveReceiptId);
    if (!receiptId || isNaN(receiptId) || receiptId <= 0) {
        showToast('未选择或打开有效单据，无法审核入账', 'warning');
        return;
    }

    const version = pendingApproveVersion;
    const submitBtn = document.getElementById('btnExecuteApproveConfirm');
    if (submitBtn) submitBtn.disabled = true;

    const executingReceiptId = receiptId;
    const executingVersion = version;

    apiFetch(`/api/receipt/${executingReceiptId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version: executingVersion })
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (httpStatus === 409 && ret && ret.code === 'ALREADY_APPROVED') {
            showToast('该单据之前已完成审核入账，未重复增加入库', 'info');
            closeApproveConfirmModal();
            loadWorkbenchQueue(true);
            loadInventoryData();
            return;
        }
        if (handleApproveError(httpStatus, ret, { receiptId: executingReceiptId, closeModal: closeApproveConfirmModal })) return;
        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('审核入账没有成功：' + TERMS.humanizeCode(ret && ret.code, ret && ret.msg), 'error');
            return;
        }

        showToast(`单据 #${executingReceiptId} 已审核入账，库存已增加`, 'success');
        closeApproveConfirmModal();
        loadWorkbenchQueue(true);
        loadInventoryData();
        loadReceiptsHistory();

        // 若当前正打开该单，同步锁定状态
        if (String(currentReceiptId) === String(executingReceiptId)) {
            const saveBadge = document.getElementById('reviewSaveStatusBadge');
            if (saveBadge) {
                saveBadge.textContent = '已入账 (锁定)';
                saveBadge.className = 'badge badge-success';
            }
        }
    })
    .catch(err => {
        console.error('审核请求异常:', err);
        showToast('审核请求失败，请检查网络设置', 'error');
    })
    .finally(() => {
        if (submitBtn) submitBtn.disabled = false;
    });
}
window.executeConfirmedApproval = executeConfirmedApproval;

