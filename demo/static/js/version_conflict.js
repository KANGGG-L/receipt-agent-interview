// demo/static/js/version_conflict.js
// 版本冲突 409 对比弹窗与本地草稿保护（Category 06，AC-06-03/04）。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// =============================================================
// Category 06: 版本冲突 409 对比弹窗与本地草稿保护（AC-06-03, AC-06-04）
// =============================================================

let conflictLocalDataCache = null;
let conflictReceiptIdCache = null;

function renderConflictDataView(d, isServer = false) {
    if (!d) return isServer ? '服务器已被其他人员更新并递增了版本号。\n请选择是否覆盖或重新加载。' : '未提供本地数据';
    const lines = [];
    if (d.id != null || d.receipt_id != null) lines.push(`单据编号: #${d.id || d.receipt_id}`);
    if (isServer && d.version != null) lines.push(`服务端版本: v${d.version}`);
    if (!isServer && d.version != null) lines.push(`本地提交版本: v${d.version}`);
    lines.push(`供应商: ${d.supplier_name || '未填'}`);
    lines.push(`开单日期: ${d.date || d.receipt_date || '未填'}`);
    lines.push(`整单金额: $${Number(d.total_amount || 0).toFixed(2)}`);
    
    const items = d.items || [];
    lines.push(`明细条目 (${items.length} 项):`);
    if (items.length === 0) {
        lines.push('  (无明细条目)');
    } else {
        items.forEach((it, idx) => {
            const name = it.name || it.raw_name || '未命名食材';
            const qty = it.quantity != null ? it.quantity : 1;
            const unit = it.unit || it.raw_unit || '';
            const price = it.unit_price != null ? `$${Number(it.unit_price).toFixed(2)}` : '';
            const amt = it.amount != null ? `$${Number(it.amount).toFixed(2)}` : '';
            lines.push(`  ${idx + 1}. ${name} × ${qty}${unit} ${price ? `(单价: ${price})` : ''} ${amt ? `小计: ${amt}` : ''}`.trim());
        });
    }
    return lines.join('\n');
}

function showConflictDiffModal(receiptId, localData, serverData, explicitTrigger) {
    conflictReceiptIdCache = receiptId;
    conflictLocalDataCache = localData;

    const modal = document.getElementById('modalConflictDiffDialog');
    const localContent = document.getElementById('conflictLocalDiffContent');
    const serverContent = document.getElementById('conflictServerDiffContent');
    const serverError = document.getElementById('conflictServerFetchError');

    const setServerErrorVisible = (visible, message) => {
        if (!serverError) return;
        if (visible) {
            serverError.textContent = message || '拉取服务端最新数据失败，无法展示差异对比。';
            serverError.classList.remove('hide');
        } else {
            serverError.textContent = '';
            serverError.classList.add('hide');
        }
    };

    const updateContents = (srvData) => {
        if (localContent) {
            localContent.textContent = renderConflictDataView(localData, false);
        }
        if (serverContent) {
            serverContent.textContent = renderConflictDataView(srvData, true);
        }
    };

    // AC-06-03: 拉取服务端最新真实单据；失败时给出可见错误与可点的重试入口，
    // 而不是静默保持空态（用户会以为服务端没有数据）。
    const fetchLatestIntoModal = () => {
        setServerErrorVisible(false);
        updateContents(null);
        apiFetch(`/api/receipt/${receiptId}`)
            .then(res => res.json())
            .then(ret => {
                if (ret.status === 'success' && ret.data) {
                    updateContents(ret.data);
                } else {
                    setServerErrorVisible(true, '读取服务端最新数据失败：' + ((ret && ret.msg) || '响应异常') + '。可点「重试」重新拉取。');
                }
            })
            .catch(err => {
                console.warn('[Conflict] 拉取服务端最新单据详情失败:', err);
                setServerErrorVisible(true, '网络异常，读取服务端最新数据失败。可点「重试」重新拉取。');
            });
    };

    if (serverData) {
        setServerErrorVisible(false);
        updateContents(serverData);
    } else if (receiptId) {
        fetchLatestIntoModal();
    } else {
        updateContents(null);
    }

    // 重试按钮绑定（幂等覆盖，避免重复监听器累积）
    const retryBtn = document.getElementById('btnConflictFetchRetry');
    if (retryBtn) {
        retryBtn.onclick = () => {
            if (conflictReceiptIdCache) {
                receiptId = conflictReceiptIdCache;
                fetchLatestIntoModal();
            }
        };
    }

    if (modal) openAccessibleModal(modal, '#btnCancelConflictDiff', explicitTrigger);
}

function closeConflictDiffModal(discard = false) {
    const modal = document.getElementById('modalConflictDiffDialog');
    if (modal) closeAccessibleModal(modal);
}
window.closeConflictDiffModal = closeConflictDiffModal;

function acceptServerVersionAndReload() {
    closeConflictDiffModal();
    if (conflictReceiptIdCache) {
        refreshReviewFormFromServer(conflictReceiptIdCache);
    }
}
window.acceptServerVersionAndReload = acceptServerVersionAndReload;

function forceOverwriteConflictVersion() {
    closeConflictDiffModal();
    if (!conflictReceiptIdCache || !conflictLocalDataCache) return;

    // 拉取最新版本号以解除冲突
    apiFetch(`/api/receipt/${conflictReceiptIdCache}`)
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data && ret.data.version != null) {
                conflictLocalDataCache.version = ret.data.version;
                if (currentReceiptData) currentReceiptData.version = ret.data.version;
                submitSaveEdited(false);
            }
        });
}
window.forceOverwriteConflictVersion = forceOverwriteConflictVersion;

// 本地草稿保护（AC-06-04）
function checkAndPromptLocalDraft(receiptId) {
    try {
        const saved = localStorage.getItem(`receipt_draft_${receiptId}`);
        if (!saved) return;
        const draft = JSON.parse(saved);
        if (draft && draft.timestamp && (Date.now() - draft.timestamp < 24 * 3600 * 1000)) {
            if (confirm('检测到本单据有未保存的本地草稿，是否恢复之前的修改？')) {
                renderEditForm(draft.data);
                showToast('已成功恢复本地草稿内容', 'info');
            } else {
                localStorage.removeItem(`receipt_draft_${receiptId}`);
            }
        }
    } catch (e) {}
}

window.addEventListener('beforeunload', () => {
    if (currentReceiptId) {
        try {
            const formData = collectReviewFormData();
            localStorage.setItem(`receipt_draft_${currentReceiptId}`, JSON.stringify({
                timestamp: Date.now(),
                data: formData
            }));
        } catch (e) {}
    }
});

