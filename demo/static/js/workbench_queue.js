// demo/static/js/workbench_queue.js
// 当天任务工作台队列（Category 03，AC-03-01 至 AC-03-06）：角色过滤、两次点击继续、
        // 保存下一张、approved 排除。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// =============================================================
// Category 03: 当天任务工作台队列（AC-03-01 至 AC-03-06）
// 角色过滤、两次点击继续、保存下一张、approved 排除
// =============================================================

function setWorkbenchQueueFilter(filter) {
    currentWorkbenchQueueFilter = filter || 'todo';
    // AC-03-03: 持久化当前队列筛选，刷新/重进后保持，不被重置回默认 tab
    try { localStorage.setItem('workbenchQueueFilter', currentWorkbenchQueueFilter); } catch (e) {}
    document.querySelectorAll('.queue-tab').forEach(btn => {
        if (btn.dataset.queueFilter === currentWorkbenchQueueFilter) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });
    renderWorkbenchQueueTable();
}
window.setWorkbenchQueueFilter = setWorkbenchQueueFilter;

// AC-03-03: 恢复上次使用的队列筛选（刷新后不丢）
function restoreWorkbenchQueueFilter() {
    let saved = null;
    try { saved = localStorage.getItem('workbenchQueueFilter'); } catch (e) {}
    const valid = ['todo', 'to_review', 'to_approve', 'issues'];
    if (saved && valid.indexOf(saved) !== -1) {
        currentWorkbenchQueueFilter = saved;
        document.querySelectorAll('.queue-tab').forEach(btn => {
            if (btn.dataset.queueFilter === saved) btn.classList.add('active');
            else btn.classList.remove('active');
        });
        return true;
    }
    return false;
}
window.restoreWorkbenchQueueFilter = restoreWorkbenchQueueFilter;

function loadWorkbenchQueue(forceRefresh = false) {
    fetchReceiptListShared(!!forceRefresh)
        .then(ret => {
            cachedWorkbenchQueueData = ret.data;
            updateWorkbenchQueueCounters();
            renderWorkbenchQueueTable();
        })
        .catch(err => {
            console.error('加载工作台队列异常:', err);
        });
}
window.loadWorkbenchQueue = loadWorkbenchQueue;

function updateWorkbenchQueueCounters() {
    const list = cachedWorkbenchQueueData.filter(r => !r.deleted_at);
    
    // AC-03-01: 角色计数严格按集合，approved 不入未完成队列
    const todoList = list.filter(r => r.status !== 'approved');
    const reviewList = list.filter(r => r.status === 'parsed' || r.status === 'parsed_with_warnings' || r.status === 'error' || r.status === 'flagged');
    const approveList = list.filter(r => r.status === 'edited');
    const issueList = list.filter(r => (r.status === 'flagged') || (r.math_warnings && r.math_warnings.length > 0) || (r.quality_warnings && r.quality_warnings.length > 0));

    const qCountTodo = document.getElementById('qCountTodo');
    const qCountReview = document.getElementById('qCountReview');
    const qCountApprove = document.getElementById('qCountApprove');
    const qCountIssues = document.getElementById('qCountIssues');
    const qTotalBadge = document.getElementById('workbenchQueueTotalBadge');

    if (qCountTodo) qCountTodo.textContent = todoList.length;
    if (qCountReview) qCountReview.textContent = reviewList.length;
    if (qCountApprove) qCountApprove.textContent = approveList.length;
    if (qCountIssues) qCountIssues.textContent = issueList.length;
    if (qTotalBadge) qTotalBadge.textContent = `${todoList.length} 张待办`;
}

function renderWorkbenchQueueTable() {
    const tbody = document.getElementById('workbenchQueueBody');
    if (!tbody) return;

    const list = cachedWorkbenchQueueData.filter(r => !r.deleted_at);
    let filtered = [];

    if (currentWorkbenchQueueFilter === 'todo') {
        filtered = list.filter(r => r.status !== 'approved');
    } else if (currentWorkbenchQueueFilter === 'to_review') {
        filtered = list.filter(r => r.status === 'parsed' || r.status === 'parsed_with_warnings' || r.status === 'error' || r.status === 'flagged');
    } else if (currentWorkbenchQueueFilter === 'to_approve') {
        filtered = list.filter(r => r.status === 'edited');
    } else if (currentWorkbenchQueueFilter === 'issues') {
        filtered = list.filter(r => (r.status === 'flagged') || (r.math_warnings && r.math_warnings.length > 0) || (r.quality_warnings && r.quality_warnings.length > 0));
    } else {
        filtered = list;
    }

    if (filtered.length === 0) {
        tbody.innerHTML = `<tr><td colspan="8" style="text-align:center; padding:24px; color:var(--text-muted);">当前队列暂无待处理单据</td></tr>`;
        return;
    }

    const isOwner = (typeof currentRole !== 'undefined' && currentRole === 'owner');

    let html = '';
    filtered.forEach(row => {
        let statusBadge = '<span class="badge badge-secondary">待处理</span>';
        if (row.status === 'parsed') {
            statusBadge = '<span class="badge badge-info">待复核</span>';
        } else if (row.status === 'parsed_with_warnings') {
            const count = (row.math_warnings ? row.math_warnings.length : 0) + (row.quality_warnings ? row.quality_warnings.length : 0);
            statusBadge = `<span class="badge badge-warning">发现 ${count} 处提醒</span>`;
        } else if (row.status === 'edited') {
            statusBadge = '<span class="badge badge-primary">' + _termStatus('edited', 'label') + '</span>';
        } else if (row.status === 'approved') {
            statusBadge = '<span class="badge badge-success">已入账</span>';
        } else if (row.status === 'flagged' || row.status === 'error') {
            statusBadge = '<span class="badge badge-danger">识别异常</span>';
        }

        const thumb = row.image_url 
            ? `<img src="${row.image_url}" alt="原图" style="width:36px; height:36px; object-fit:cover; border-radius:4px; border:1px solid #cbd5e1;">`
            : `<div style="width:36px; height:36px; background:#f1f5f9; border-radius:4px; display:flex; align-items:center; justify-content:center; font-size:0.75rem; color:#4b5563;">无图</div>`;

        const dateStr = (row.receipt_date || (row.created_at ? row.created_at.slice(0, 10) : '未知日期')) + ' (HKT)';
        const supplierName = row.supplier_name || 'Vendor_A';
        const totalFormatted = row.total_amount ? `$${Number(row.total_amount).toFixed(2)}` : '$0.00';

        // AC-03-02: 队列行显式「问题数」列（按 problem.id 语义去重后的提醒+阻断计数）。
        // 无问题用明确的 0，而不是留空（空会被读成「未知」）。
        const _mw = Array.isArray(row.math_warnings) ? row.math_warnings.length : 0;
        const _qw = Array.isArray(row.quality_warnings) ? row.quality_warnings.length : 0;
        const _issueCount = (row.status === 'flagged' ? 1 : 0) + _mw + _qw;
        const problemCell = _issueCount > 0
            ? `<span class="badge badge-warning" title="该单据待处理的问题数（算术/画质/异常）">${_issueCount}</span>`
            : `<span class="badge badge-secondary" title="未发现问题">0</span>`;

        let actionBtn = '';
        if (row.status === 'edited' && isOwner) {
            actionBtn = `<button type="button" class="btn btn-sm btn-primary" style="min-height:40px; padding:6px 12px;" onclick="openApproveConfirmModalFromQueue(${row.id})">审核入账</button>`;
        } else {
            actionBtn = `<button type="button" class="btn btn-sm btn-secondary" style="min-height:40px; padding:6px 12px;" onclick="openReceiptInWorkbench(${row.id})">继续复核</button>`;
        }

        html += `
            <tr data-receipt-id="${row.id}">
                <td>${thumb}</td>
                <td><strong>#${row.id}</strong></td>
                <td>${escapeHtml(supplierName)}</td>
                <td>${dateStr}</td>
                <td style="font-weight:600; color:var(--primary);">${totalFormatted}</td>
                <td>${statusBadge}</td>
                <td style="text-align:center;">${problemCell}</td>
                <td style="text-align:center;">${actionBtn}</td>
            </tr>
        `;
    });

    tbody.innerHTML = html;
}

let _workbenchOpenSeq = 0;
let _pendingWorkbenchSwitchTarget = null;  // AC-06-04: 保存后待切换的目标单据

// AC-03-02: 两次点击继续——直接从首页打开任一未完成任务至工作台
function openReceiptInWorkbench(receiptId, silent = false, _guarded = false) {
    if (!receiptId) return;
    const targetId = Number(receiptId);
    if (!targetId || isNaN(targetId) || targetId <= 0) return;

    // AC-06-04: 切换到另一张单据前，若当前表单有未保存修改，必须先给可执行的选择
    // （保存后再切换 / 放弃并切换 / 保留本地留在当前），不得静默丢弃本地编辑。
    if (!_guarded && currentReceiptId && Number(currentReceiptId) !== targetId
        && typeof isReviewFormDirty === 'function' && isReviewFormDirty()) {
        showUnsavedChangesGuard({
            receiptLabel: '单据 #' + targetId,
            onSave: () => { _pendingWorkbenchSwitchTarget = targetId; submitSaveEdited(false); },
            onDiscard: () => { _reviewIsDirty = false; openReceiptInWorkbench(targetId, silent, true); }
        });
        return;
    }

    const seq = ++_workbenchOpenSeq;
    apiFetch(`/api/receipt/${targetId}`)
        .then(res => res.json())
        .then(ret => {
            if (seq !== _workbenchOpenSeq) return;
            if (ret.status !== 'success' || !ret.data) {
                showToast('读取单据详情失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
                return;
            }
            currentReceiptId = targetId;
            currentReceiptData = ret.data;
            currentReceiptData.id = targetId;
            currentReceiptData.receipt_id = targetId;
            if (ret.data.version != null) currentReceiptData.version = ret.data.version;

            // 切换原图预览
            const previewImg = document.getElementById('previewImg');
            const noImg = document.getElementById('manualEntryNoImage');
            if (ret.data.image_url) {
                if (previewImg) {
                    previewImg.src = ret.data.image_url;
                    previewImg.style.display = '';
                }
                if (noImg) noImg.classList.add('hide');
            } else {
                if (previewImg) previewImg.style.display = 'none';
                if (noImg) noImg.classList.remove('hide');
            }

            // 展开分栏并渲染编辑表单
            const splitView = document.getElementById('splitViewArea');
            if (splitView) splitView.classList.remove('hide');
            const prefillCard = document.getElementById('prefillFormCard');
            if (prefillCard) prefillCard.classList.remove('hide');
            const loadingCard = document.getElementById('loadingCard');
            if (loadingCard) loadingCard.classList.add('hide');
            const preConfirmCard = document.getElementById('preConfirmCard');
            if (preConfirmCard) preConfirmCard.classList.add('hide');
            const errorCard = document.getElementById('errorCard');
            if (errorCard) errorCard.classList.add('hide');

            renderEditForm(currentReceiptData);

            // 更新保存状态指示
            const saveBadge = document.getElementById('reviewSaveStatusBadge');
            if (saveBadge) {
                if (ret.data.status === 'edited') {
                    saveBadge.textContent = '已提交，待老板审批';
                    saveBadge.className = 'badge badge-info';
                } else if (ret.data.status === 'approved') {
                    saveBadge.textContent = '已入账 (锁定)';
                    saveBadge.className = 'badge badge-success';
                } else {
                    saveBadge.textContent = '未保存';
                    saveBadge.className = 'badge badge-secondary';
                }
            }

            // 检查本地草稿恢复（AC-06-04）
            checkAndPromptLocalDraft(receiptId);

            if (!silent) {
                showToast(ret.data.image_url ? `已打开单据 #${receiptId}，可对照左侧原图核对` : `已打开单据 #${receiptId}（手工录入，没有原图）`, 'info');
            }
            prefillCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
        })
        .catch(err => {
            console.error('加载单据异常:', err);
            showToast('加载单据失败，请检查网络设置', 'error');
        });
}
window.openReceiptInWorkbench = openReceiptInWorkbench;

// AC-03-01 & QA2-P1-01: 获取当前筛选与角色上下文下的有效可操作单据列表
function getOperableWorkbenchQueueList() {
    if (!Array.isArray(cachedWorkbenchQueueData)) return [];
    const list = cachedWorkbenchQueueData.filter(r => !r.deleted_at);
    const isStaff = (typeof currentRole !== 'undefined' && currentRole === 'staff');

    let filtered = [];
    if (currentWorkbenchQueueFilter === 'to_review') {
        filtered = list.filter(r => r.status === 'parsed' || r.status === 'parsed_with_warnings' || r.status === 'error' || r.status === 'flagged');
    } else if (currentWorkbenchQueueFilter === 'to_approve') {
        filtered = list.filter(r => r.status === 'edited');
    } else if (currentWorkbenchQueueFilter === 'issues') {
        filtered = list.filter(r => (r.status === 'flagged') || (r.math_warnings && r.math_warnings.length > 0) || (r.quality_warnings && r.quality_warnings.length > 0));
    } else {
        // 'todo', 'all' 等默认待办队列
        filtered = list.filter(r => r.status !== 'approved');
    }

    // 角色权限与单据可操作状态收敛：
    // staff 绝对不可审批入账，已提交老板审批的项（edited）对 staff 而言不可操作，绝不能跳转
    if (isStaff) {
        filtered = filtered.filter(r => r.status !== 'edited' && r.status !== 'approved');
    } else {
        filtered = filtered.filter(r => r.status !== 'approved');
    }

    return filtered;
}
window.getOperableWorkbenchQueueList = getOperableWorkbenchQueueList;

// AC-03-03 & QA2-P1-01: 保存并下一张，严格遵循当前筛选与角色，并避免三重 toast 遮挡
function navigateNextOperableQueueItem(currentId, isManual = false) {
    const operableList = getOperableWorkbenchQueueList();
    if (!operableList || operableList.length === 0) {
        showToast('当前筛选下的待办单据已全部处理完毕', 'success');
        const splitView = document.getElementById('splitViewArea');
        if (splitView) splitView.classList.add('hide');
        const qSection = document.getElementById('workbenchQueueSection');
        if (qSection) qSection.scrollIntoView({ behavior: 'smooth' });
        return;
    }

    let nextItem = null;
    let currentIndex = -1;
    if (Array.isArray(cachedWorkbenchQueueData)) {
        for (let i = 0; i < cachedWorkbenchQueueData.length; i++) {
            if (String(cachedWorkbenchQueueData[i].id) === String(currentId)) {
                currentIndex = i;
                break;
            }
        }
    }

    if (currentIndex !== -1) {
        // 优先在当前单据之后寻找下一个符合筛选与角色的可操作项
        for (let i = currentIndex + 1; i < cachedWorkbenchQueueData.length; i++) {
            const item = cachedWorkbenchQueueData[i];
            if (operableList.some(op => String(op.id) === String(item.id))) {
                nextItem = item;
                break;
            }
        }
    }

    // 若后序无匹配，从队列首部循环找首个不是 currentId 的可操作项
    if (!nextItem) {
        for (let i = 0; i < cachedWorkbenchQueueData.length; i++) {
            const item = cachedWorkbenchQueueData[i];
            if (String(item.id) !== String(currentId) && operableList.some(op => String(op.id) === String(item.id))) {
                nextItem = item;
                break;
            }
        }
    }

    // 若依然未找到（例如队列中仅剩 currentId），说明已处理完毕
    if (!nextItem) {
        showToast('当前筛选下的待办单据已全部处理完毕', 'success');
        const splitView = document.getElementById('splitViewArea');
        if (splitView) splitView.classList.add('hide');
        const qSection = document.getElementById('workbenchQueueSection');
        if (qSection) qSection.scrollIntoView({ behavior: 'smooth' });
        return;
    }

    // 仅弹出一个清晰明了的成功提示，避免多重 toast 堆叠遮挡
    const notifyMsg = isManual
        ? `已保存手工单据 #${currentId}，已切换至下一张待办单据 #${nextItem.id}`
        : `已保存修改，已切换至下一张待办单据 #${nextItem.id}`;
    showToast(notifyMsg, 'success');
    openReceiptInWorkbench(nextItem.id, true);
}

