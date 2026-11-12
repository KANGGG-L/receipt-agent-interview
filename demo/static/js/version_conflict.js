// demo/static/js/version_conflict.js
// 版本冲突 409 对比弹窗与本地草稿保护（Category 06，AC-06-03/04）。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// =============================================================
// Category 06: 版本冲突 409 对比弹窗与本地草稿保护（AC-06-03, AC-06-04）
// =============================================================

let conflictLocalDataCache = null;
let conflictReceiptIdCache = null;

// -------------------------------------------------------------
// U04：409 冲突——本地草稿保底 + 双方版本/字段级差异
// 保存冲突时先把本地待保存内容留一份（按 tenant/account 分桶，不跨身份可见），
// 再展示本地 vs 服务端：版本号 + 逐字段差异；不清空当前表单/本地编辑。
// -------------------------------------------------------------
const CONFLICT_DRAFT_PREFIX = 'finsaro_conflict_local_draft_v1';

function conflictDraftStorageKey(receiptId) {
    const prefix = CONFLICT_DRAFT_PREFIX + ':' + (receiptId == null ? 'na' : receiptId);
    if (typeof identityScopedStorageKey === 'function') return identityScopedStorageKey(prefix);
    return prefix;
}

function preserveConflictLocalDraft(receiptId, localData) {
    if (receiptId == null || !localData) return null;
    const snapshot = {
        receipt_id: receiptId,
        version: localData.version != null ? localData.version : null,
        data: localData,
        at: new Date().toISOString(),
    };
    try { localStorage.setItem(conflictDraftStorageKey(receiptId), JSON.stringify(snapshot)); } catch (e) {}
    return snapshot;
}

// 复核草稿也按身份分桶：换账号登录时不会提示恢复上一位用户的未保存草稿
function reviewDraftStorageKey(receiptId) {
    const prefix = 'receipt_draft_' + receiptId;
    if (typeof identityScopedStorageKey === 'function') return identityScopedStorageKey(prefix);
    return prefix;
}

function loadConflictLocalDraft(receiptId) {
    try {
        const raw = localStorage.getItem(conflictDraftStorageKey(receiptId));
        if (!raw) return null;
        const parsed = JSON.parse(raw);
        return (parsed && parsed.data) ? parsed : null;
    } catch (e) { return null; }
}

function conflictFieldRaw(d, key) {
    if (!d) return '';
    if (key === 'date') return d.date || d.receipt_date || '';
    if (key === 'version') return d.version != null ? d.version : '';
    const v = d[key];
    return v == null ? '' : v;
}

function conflictFieldText(key, v) {
    if (v === '' || v == null) return '未填';
    if (key === 'version') return 'v' + v;
    if (key === 'total_amount') return '$' + Number(v || 0).toFixed(2);
    return String(v);
}

function conflictItemRaw(it, key) {
    if (!it) return '';
    if (key === 'name') return it.name || it.raw_name || '';
    const v = it[key];
    return v == null ? '' : v;
}

function conflictItemText(key, v) {
    if (v === '' || v == null) return '未填';
    if (key === 'unit_price' || key === 'amount') return '$' + Number(v || 0).toFixed(2);
    return String(v);
}

// 字段级差异：单据头 + 明细逐行；仅收集真正不同的字段
function buildConflictFieldDiff(localData, serverData) {
    const rows = [];
    if (!localData || !serverData) return rows;
    const fields = [
        ['supplier_name', '供应商'],
        ['date', '开单日期'],
        ['total_amount', '整单金额'],
        ['settlement_type', '结算方式'],
        ['version', '版本号'],
    ];
    fields.forEach(pair => {
        const key = pair[0];
        const lv = conflictFieldRaw(localData, key);
        const sv = conflictFieldRaw(serverData, key);
        if (String(lv) !== String(sv)) {
            rows.push({ field: key, label: pair[1], local: conflictFieldText(key, lv), server: conflictFieldText(key, sv) });
        }
    });
    const li = Array.isArray(localData.items) ? localData.items : [];
    const si = Array.isArray(serverData.items) ? serverData.items : [];
    if (li.length !== si.length) {
        rows.push({ field: 'items.length', label: '明细行数', local: li.length + ' 项', server: si.length + ' 项' });
    }
    const itemFields = [['name', '品名'], ['quantity', '数量'], ['unit_price', '单价'], ['amount', '小计']];
    const max = Math.max(li.length, si.length);
    for (let i = 0; i < max; i++) {
        itemFields.forEach(pair => {
            const key = pair[0];
            const lv = conflictItemRaw(li[i], key);
            const sv = conflictItemRaw(si[i], key);
            if (String(lv) !== String(sv)) {
                rows.push({
                    field: `items[${i}].${key}`,
                    label: `明细 ${i + 1} ${pair[1]}`,
                    local: conflictItemText(key, lv),
                    server: conflictItemText(key, sv),
                });
            }
        });
    }
    return rows;
}

function renderConflictFieldDiff(localData, serverData) {
    const rows = buildConflictFieldDiff(localData, serverData);
    if (!rows.length) return '字段逐项对比：本地与服务端一致（可能仅版本号不同）。';
    return '字段级差异（本地 → 服务端）：\n'
        + rows.map(r => `${r.label}: ${r.local} → ${r.server}`).join('\n');
}

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
    // U04：保存 409 时不清空本地编辑——先把本地待保存内容留底（非敏感草稿），
    // 再展示双方版本与字段级差异；表单本身保持用户当前填写状态。
    preserveConflictLocalDraft(receiptId, localData);

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
            // 本地侧额外展示「本地 → 服务端」字段级差异（含版本号），
            // 服务端数据未取回时只显示本地内容，不伪造差异结论。
            const diffText = srvData ? renderConflictFieldDiff(localData, srvData) : '';
            localContent.textContent = renderConflictDataView(localData, false)
                + (diffText ? '\n\n' + diffText : '');
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
        const saved = localStorage.getItem(reviewDraftStorageKey(receiptId));
        if (!saved) return;
        const draft = JSON.parse(saved);
        if (draft && draft.timestamp && (Date.now() - draft.timestamp < 24 * 3600 * 1000)) {
            if (confirm('检测到本单据有未保存的本地草稿，是否恢复之前的修改？')) {
                renderEditForm(draft.data);
                showToast('已成功恢复本地草稿内容', 'info');
            } else {
                localStorage.removeItem(reviewDraftStorageKey(receiptId));
            }
        }
    } catch (e) {}
}

window.addEventListener('beforeunload', () => {
    if (currentReceiptId) {
        try {
            const formData = collectReviewFormData();
            localStorage.setItem(reviewDraftStorageKey(currentReceiptId), JSON.stringify({
                timestamp: Date.now(),
                data: formData
            }));
        } catch (e) {}
    }
});

