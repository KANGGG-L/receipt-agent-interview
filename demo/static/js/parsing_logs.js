// demo/static/js/parsing_logs.js
// 05 解析日志与链路审计模块：统一租户遥测同步、埋点遥测面板的解析日志侧、
// 解析日志列表/筛选/分页/详情 Modal。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// 05 解析日志与链路审计模块 (Parsing Logs & Pipeline Audit)
// =====================================================================

const currentParsingLogsState = {
    page: 1,
    pageSize: 20,
    totalPages: 1,
    totalItems: 0,
    items: [],
    activeItem: null,
};

window.currentTelemetryState = {
    subTab: 'audit',
};

function syncTelemetryTenants() {
    // 租户筛选为文本输入：镜像同步隐藏域即可（空值 = 全部租户）
    const mainSel = document.getElementById('analyticsTenantSelect');
    const plSel = document.getElementById('parsingLogTenantSelect');
    if (!mainSel || !plSel) return;
    if (plSel.value !== mainSel.value) {
        plSel.value = mainSel.value;
    }
}
window.syncTelemetryTenants = syncTelemetryTenants;
window.syncParsingLogsTenants = syncTelemetryTenants;

function onUnifiedTenantChange(tId) {
    const v = (tId || '').trim();
    const mainSel = document.getElementById('analyticsTenantSelect');
    const plSel = document.getElementById('parsingLogTenantSelect');
    const evSel = document.getElementById('eventsFilterTenant');
    if (mainSel && mainSel.value !== v) mainSel.value = v;
    if (plSel && plSel.value !== v) plSel.value = v;
    if (evSel && evSel.value !== v) evSel.value = v;
    loadParsingLogs(1);
    loadRecoverySummaryBlocks(v || 'all');
}
window.onUnifiedTenantChange = onUnifiedTenantChange;
window.onAnalyticsTenantChange = onUnifiedTenantChange;

function refreshUnifiedTelemetry() {
    const tId = (document.getElementById('analyticsTenantSelect')?.value) || (document.getElementById('parsingLogTenantSelect')?.value) || (document.getElementById('eventsFilterTenant')?.value) || 'all';
    loadParsingLogs(1);
    loadRecoverySummaryBlocks(tId);
}
window.refreshUnifiedTelemetry = refreshUnifiedTelemetry;

function switchTelemetrySubTab(subTabKey) {
    if (subTabKey !== 'audit' && subTabKey !== 'events') {
        subTabKey = 'audit';
    }
    window.currentTelemetryState.subTab = subTabKey;

    const btnAudit = document.getElementById('btnTelemetrySubTabAudit');
    const btnEvents = document.getElementById('btnTelemetrySubTabEvents');
    const contentAudit = document.getElementById('telemetrySubContentAudit');
    const contentEvents = document.getElementById('telemetrySubContentEvents');

    const isAudit = (subTabKey === 'audit');

    if (btnAudit) {
        btnAudit.classList.toggle('active', isAudit);
        btnAudit.setAttribute('aria-selected', isAudit ? 'true' : 'false');
        btnAudit.style.borderBottomColor = isAudit ? '#2563eb' : 'transparent';
        btnAudit.style.color = isAudit ? '#2563eb' : 'var(--text-muted)';
        btnAudit.style.fontWeight = isAudit ? '600' : 'normal';
    }
    if (btnEvents) {
        btnEvents.classList.toggle('active', !isAudit);
        btnEvents.setAttribute('aria-selected', !isAudit ? 'true' : 'false');
        btnEvents.style.borderBottomColor = !isAudit ? '#2563eb' : 'transparent';
        btnEvents.style.color = !isAudit ? '#2563eb' : 'var(--text-muted)';
        btnEvents.style.fontWeight = !isAudit ? '600' : 'normal';
    }

    if (contentAudit) contentAudit.style.display = isAudit ? 'block' : 'none';
    if (contentEvents) contentEvents.style.display = !isAudit ? 'block' : 'none';

    // 两 Tab 卡片各自内聚独立专属筛选工具栏，平滑切换无空白塌陷

    if (isAudit) {
        if (!currentParsingLogsState.items || currentParsingLogsState.items.length === 0) {
            loadParsingLogs(1);
        }
    } else {
        const sel = document.getElementById('eventsFilterTenant') || document.getElementById('analyticsTenantSelect') || document.getElementById('parsingLogTenantSelect');
        const tId = sel ? sel.value : 'all';
        loadRecoverySummaryBlocks(tId);
    }
}
window.switchTelemetrySubTab = switchTelemetrySubTab;

// 时间跨度周期状态：与趋势弹窗「统计周期」同逻辑（预设周期按钮 + 自定义起止）
window._parsingLogPeriod = { mode: 'all', startDate: null, endDate: null };

function _parsingLogTodayStr() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function _parsingLogDaysAgoStr(n) {
    const d = new Date();
    d.setDate(d.getDate() - n);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

const _PARSING_LOG_PERIOD_BTN_IDS = {
    'all': 'btnLogRangeAll',
    '24h': 'btnLogRange24h',
    '7d': 'btnLogRange7d',
    '14d': 'btnLogRange14d',
    '30d': 'btnLogRange30d',
};

function _updateParsingLogPeriodButtons() {
    const mode = window._parsingLogPeriod.mode;
    Object.keys(_PARSING_LOG_PERIOD_BTN_IDS).forEach(m => {
        const btn = document.getElementById(_PARSING_LOG_PERIOD_BTN_IDS[m]);
        if (!btn) return;
        if (m === mode) {
            btn.className = 'btn btn-sm btn-primary';
            btn.style.fontWeight = '600';
        } else {
            btn.className = 'btn btn-sm btn-secondary';
            btn.style.fontWeight = 'normal';
        }
    });
}

function onParsingLogDateRangeSelectChange(val) {
    if (val === 'custom') {
        const sEl = document.getElementById('parsingLogStartDate');
        const eEl = document.getElementById('parsingLogEndDate');
        if (sEl && eEl && sEl.value && eEl.value) {
            applyParsingLogCustomPeriod(false);
        } else {
            window._parsingLogPeriod.mode = 'custom';
            _updateParsingLogPeriodButtons();
            if (sEl) sEl.focus();
        }
    } else {
        setParsingLogPeriod(val);
    }
}
window.onParsingLogDateRangeSelectChange = onParsingLogDateRangeSelectChange;

function setParsingLogPeriod(mode) {
    const p = window._parsingLogPeriod;
    p.mode = mode;
    p.startDate = null;
    p.endDate = null;
    const sEl = document.getElementById('parsingLogStartDate');
    const eEl = document.getElementById('parsingLogEndDate');
    if (sEl) sEl.value = '';
    if (eEl) eEl.value = '';
    const selEl = document.getElementById('parsingLogDateRangeSelect');
    if (selEl) selEl.value = mode;
    _updateParsingLogPeriodButtons();
    loadParsingLogs(1);
}
window.setParsingLogPeriod = setParsingLogPeriod;

function applyParsingLogCustomPeriod(silent) {
    const sEl = document.getElementById('parsingLogStartDate');
    const eEl = document.getElementById('parsingLogEndDate');
    const sVal = sEl && sEl.value ? sEl.value.trim() : '';
    const eVal = eEl && eEl.value ? eEl.value.trim() : '';
    // 日期选择后自动应用（silent）：结束日期未填时静默等待，不弹窗打断
    if (!eVal) {
        if (!silent) showToast('请选择结束日期（开始日期留空默认从最早一条记录开始）', 'warning');
        return;
    }
    if (sVal && sVal > eVal) {
        if (!silent) showToast('开始日期不能晚于结束日期', 'warning');
        return;
    }
    const p = window._parsingLogPeriod;
    p.mode = 'custom';
    p.startDate = sVal || null;
    p.endDate = eVal;
    const selEl = document.getElementById('parsingLogDateRangeSelect');
    if (selEl) selEl.value = 'custom';
    _updateParsingLogPeriodButtons();
    loadParsingLogs(1);
}
window.applyParsingLogCustomPeriod = applyParsingLogCustomPeriod;

// 供趋势弹窗钻取调用：直接填充自定义区间（不触发加载，由调用方统一加载）
function setParsingLogCustomRange(startDate, endDate) {
    const p = window._parsingLogPeriod;
    p.mode = 'custom';
    p.startDate = startDate || null;
    p.endDate = endDate || null;
    const sEl = document.getElementById('parsingLogStartDate');
    const eEl = document.getElementById('parsingLogEndDate');
    if (sEl) sEl.value = startDate || '';
    if (eEl) eEl.value = endDate || '';
    const selEl = document.getElementById('parsingLogDateRangeSelect');
    if (selEl) selEl.value = 'custom';
    _updateParsingLogPeriodButtons();
}
window.setParsingLogCustomRange = setParsingLogCustomRange;

function loadParsingLogs(page) {
    page = page || 1;
    currentParsingLogsState.page = page;

    const tenant = (document.getElementById('analyticsTenantSelect')?.value) || (document.getElementById('parsingLogTenantSelect')?.value) || 'all';
    const status = (document.getElementById('parsingLogStatusSelect')?.value) || 'all';
    const gateStatus = (document.getElementById('parsingLogGateSelect')?.value) || 'all';
    const feedback = (document.getElementById('parsingLogFeedbackSelect')?.value) || 'all';
    const isModified = (document.getElementById('parsingLogModifiedSelect')?.value) || 'all';
    const period = window._parsingLogPeriod || { mode: 'all', startDate: null, endDate: null };
    const search = (document.getElementById('parsingLogSearchInput')?.value || '').trim();

    const tbody = document.getElementById('parsingLogsTableBody');
    if (tbody) {
        tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:30px; color:var(--text-muted);">正在加载解析日志...</td></tr>';
    }

    const params = new URLSearchParams({
        page: String(page),
        page_size: String(currentParsingLogsState.pageSize),
        tenant_id: tenant,
        status: status,
        gate_status: gateStatus,
        feedback: feedback,
        is_modified: isModified,
        date_range: 'all',
        search: search,
    });
    if (period.mode === 'custom') {
        // 自定义区间：开始留空时不传 start_date，后端即从最早一条记录开始
        if (period.startDate) params.set('start_date', period.startDate + 'T00:00:00');
        if (period.endDate) params.set('end_date', period.endDate + 'T23:59:59.999999');
    } else if (period.mode === '7d' || period.mode === '14d' || period.mode === '30d') {
        // 与趋势弹窗「统计周期」同口径：含今天的最近 N 个自然日
        const n = parseInt(period.mode, 10);
        params.set('start_date', _parsingLogDaysAgoStr(n - 1) + 'T00:00:00');
        params.set('end_date', _parsingLogTodayStr() + 'T23:59:59.999999');
    } else {
        // all / 24h 沿用后端既有口径
        params.set('date_range', period.mode);
    }

    apiFetch('/api/analytics/parsing-logs?' + params.toString())
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (httpStatus !== 200 || !ret || ret.status !== 'success' || !ret.data) {
                if (tbody) {
                    tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:30px; color:#b91c1c;">加载解析日志失败: ' + w2Escape(ret?.detail || '网络或鉴权错误') + '</td></tr>';
                }
                return;
            }

            const data = ret.data;
            const summary = data.summary || {};
            window.currentParsingLogsSummary = summary;
            const pagination = data.pagination || {};
            const items = data.items || [];

            currentParsingLogsState.items = items;
            currentParsingLogsState.totalPages = pagination.total_pages || 1;
            currentParsingLogsState.totalItems = pagination.total_items || 0;

            // 1. 渲染顶部 8 个 KPI 卡片 (吞吐质量 + 体验挽回)
            const kpiTotal = document.getElementById('parsingKpiTotal');
            const kpiSuccess = document.getElementById('parsingKpiSuccessRate');
            const kpiElapsed = document.getElementById('parsingKpiAvgElapsed');
            const kpiTokens = document.getElementById('parsingKpiAvgTokens');
            const kpiPositive = document.getElementById('parsingKpiPositiveRate');
            const kpiEdit = document.getElementById('parsingKpiEditRate');
            const kpiRecoveryEntry = document.getElementById('telemetryKpiRecoveryEntry');
            const kpiRecoverySuccess = document.getElementById('telemetryKpiRecoverySuccess');

            if (kpiTotal) kpiTotal.textContent = (summary.total_parses || 0).toLocaleString();
            if (kpiSuccess) kpiSuccess.textContent = ((summary.success_rate || 0) * 100).toFixed(1) + '%';
            if (kpiElapsed) kpiElapsed.textContent = summary.avg_elapsed_ms ? (summary.avg_elapsed_ms.toFixed(0) + ' ms') : '-';
            if (kpiTokens) kpiTokens.textContent = (summary.avg_tokens || 0).toLocaleString();
            if (kpiPositive) kpiPositive.textContent = ((summary.positive_rate || 0) * 100).toFixed(1) + '%';
            if (kpiEdit) kpiEdit.textContent = ((summary.edit_rate || 0) * 100).toFixed(1) + '%';
            if (kpiRecoveryEntry && summary.recovery_entry_rate != null) {
                kpiRecoveryEntry.textContent = ((summary.recovery_entry_rate || 0) * 100).toFixed(1) + '%';
            }
            if (kpiRecoverySuccess && summary.recovery_success_rate != null) {
                kpiRecoverySuccess.textContent = ((summary.recovery_success_rate || 0) * 100).toFixed(1) + '%';
            }

            // 2. 渲染明细表格
            renderParsingLogsTable(items);

            // 3. 渲染分页器
            renderParsingLogsPagination(pagination);
        })
        .catch(err => {
            if (tbody) {
                tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:30px; color:#b91c1c;">请求异常: ' + w2Escape(err.message || '未知错误') + '</td></tr>';
            }
        });
}

function _extractGateReason(gate) {
    if (!gate || !gate.triggered || gate.status === 'none') return '';
    const raw = String(gate.summary || '').trim();
    if (!raw) return gate.primary_type_label || (gate.status === 'rejected' ? '校验拦截' : '规则告警');

    const sLower = raw.toLowerCase();
    if (/vlm|openai|codebuddy|500|502|503|504|超时|timeout|接口失败|request failed|调用失败/i.test(sLower)) {
        return 'VLM调用失败';
    }
    if (/算术|合计|平算|金额|总额|差额|math/i.test(sLower)) {
        return '算术合计不符';
    }
    if (/画质|清晰度|模糊|花码|quality/i.test(sLower)) {
        return '图片画质模糊';
    }
    if (/安全|隐写|隐藏文字|风控|security|注入/i.test(sLower)) {
        return '安全风控拦截';
    }
    if (/供应商|开单日期|必填|缺少|缺失|契约|schema/i.test(sLower)) {
        return '关键字段缺失';
    }

    const firstWord = raw.split(/[\(（:：\s]/)[0];
    if (firstWord && firstWord.length >= 2 && firstWord.length <= 10) {
        return firstWord;
    }
    return gate.primary_type_label || (gate.status === 'rejected' ? '校验拦截' : '规则告警');
}

function _formatGateBadge(gate) {
    if (!gate || !gate.triggered || gate.status === 'none') {
        return '<span class="badge" style="background:#f0fdf4; color:#16a34a; border:1px solid #bbf7d0; white-space:nowrap; font-weight:600;" title="算术契约检验全部通过">[平算无门禁]</span>';
    }

    const isReject = gate.status === 'rejected';
    const bg = isReject ? '#fee2e2' : '#fef3c7';
    const color = isReject ? '#b91c1c' : '#b45309';
    const border = isReject ? '#fecaca' : '#fde68a';
    const prefix = isReject ? '[门禁拦截: ' : '[门禁告警: ';
    const reason = _extractGateReason(gate);
    const labelText = prefix + reason + ']';
    const rawSummary = String(gate.summary || labelText).trim();

    return '<span class="badge" style="background:' + bg + '; color:' + color + '; border:1px solid ' + border + '; white-space:nowrap; font-weight:600;" title="' + w2Escape(rawSummary) + '">' + w2Escape(labelText) + '</span>';
}

function renderParsingLogsTable(items) {
    const tbody = document.getElementById('parsingLogsTableBody');
    const countEl = document.getElementById('parsingLogTableCount');
    if (countEl) countEl.textContent = '共 ' + currentParsingLogsState.totalItems + ' 条';
    if (!tbody) return;

    if (!items || items.length === 0) {
        tbody.innerHTML = '<tr><td colspan="13" style="text-align:center; padding:36px; color:var(--text-muted);">暂无符合筛选条件的解析日志</td></tr>';
        return;
    }

    let html = '';
    items.forEach(it => {
        const rid = it.record_id;
        const tenant = it.tenant_id || 'default';
        const gid = it.graph_id || ('graph-' + rid);
        const createdAtFormatted = _fmtDateTime(it.created_at);
        const supplier = it.supplier_name || '未命名供应商';
        const docForm = it.doc_form || '标准单据';
        const eng = it.engine || 'vlm';
        const mdl = it.model || '-';
        const tokensTotal = it.token_usage?.tokens_total || 0;
        const costHkd = it.token_usage?.cost_hkd != null ? ('HK$' + it.token_usage.cost_hkd.toFixed(4)) : '-';
        const elapsedTotal = it.elapsed_ms?.total || 0;
        const elapsedStyle = elapsedTotal > 5000 ? 'color:#b45309; font-weight:700;' : '';

        // 门禁状态与原因：收敛为紧凑专业徽章 [门禁拦截: 原因]，悬浮 tooltip 保留完整堆栈细节
        const gate = it.gate_info || {
            triggered: false,
            status: 'none',
            badge_label: '[平算无门禁]',
            primary_type: '',
            primary_type_label: '',
            summary: '算术契约检验全部通过',
            details: []
        };
        const gateBadgeHtml = _formatGateBadge(gate);
        const gateDescHtml = ''; // 细节收敛至徽章 title 悬浮与详情弹窗，主表格仅呈现 [门禁拦截: 原因] 紧凑徽章

        // 状态徽章 (严格禁止 Emoji，禁止截断)
        let statusBadge = '<span class="badge" style="background:#dcfce7; color:#15803d; white-space:nowrap;">[成功]</span>';
        if (it.parse_status === 'warning') {
            statusBadge = '<span class="badge" style="background:#fef3c7; color:#b45309; white-space:nowrap;">[告警通过]</span>';
        } else if (it.parse_status === 'error') {
            statusBadge = '<span class="badge" style="background:#fee2e2; color:#b91c1c; white-space:nowrap;">[失败]</span>';
        } else if (it.parse_status === 'processing') {
            statusBadge = '<span class="badge" style="background:#e0f2fe; color:#0369a1; white-space:nowrap;">[解析中]</span>';
        }

        // 反馈徽章 (严格禁止 Emoji)
        let feedbackBadge = '<span class="badge" style="background:#f1f5f9; color:#64748b; white-space:nowrap;">[未评]</span>';
        if (it.feedback?.like === 1) {
            feedbackBadge = '<span class="badge" style="background:#dcfce7; color:#15803d; white-space:nowrap;" title="' + w2Escape(it.feedback.comment || '满意') + '">[赞]</span>';
        } else if (it.feedback?.like === -1) {
            feedbackBadge = '<span class="badge" style="background:#fee2e2; color:#b91c1c; white-space:nowrap;" title="' + w2Escape(it.feedback.comment || '不满意') + '">[踩]</span>';
        }

        // 改动徽章 (严格禁止 Emoji，零改动统一为绿色积极标识)
        let modBadge = '<span class="badge" style="background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; font-weight:600; white-space:nowrap;">[零改动]</span>';
        if (it.modification?.is_modified) {
            const modCount = it.modification.modified_count || 1;
            modBadge = '<span class="badge" style="background:#eff6ff; color:#1d4ed8; font-weight:600; white-space:nowrap;">[已修正 (' + modCount + '项)]</span>';
        }

        html += '<tr style="border-bottom:1px solid var(--border-color);">'
            + '<td class="cell-nowrap" style="text-align:center; font-weight:600; font-family:monospace;">#' + rid + '</td>'
            + '<td class="cell-nowrap" style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono); font-variant-numeric:tabular-nums; text-align:left;">' + w2Escape(createdAtFormatted) + '</td>'
            + '<td class="cell-nowrap" style="text-align:center;"><span class="badge" style="background:#f1f5f9; color:#475569;">' + w2Escape(tenant) + '</span></td>'
            + '<td class="cell-nowrap" style="text-align:left;"><code style="font-size:0.75rem; color:#0369a1;" title="' + w2Escape(gid) + '">' + w2Escape(gid) + '</code></td>'
            + '<td class="cell-wrap" style="text-align:left;"><div style="font-weight:600; color:var(--text-primary);">' + w2Escape(supplier) + '</div><div style="font-size:0.72rem; color:var(--text-muted);">' + w2Escape(docForm) + '</div></td>'
            + '<td class="cell-wrap" style="text-align:left;"><div style="font-size:0.75rem; color:var(--text-primary); font-family:monospace; word-break:break-all;">' + w2Escape(mdl) + '</div></td>'
            + '<td class="cell-wrap" style="text-align:left; min-width:180px;">' + gateBadgeHtml + '</td>'
            + '<td class="cell-nowrap" style="text-align:right; min-width:130px;"><div style="font-weight:600;">' + tokensTotal.toLocaleString() + '</div><div style="font-size:0.7rem; color:var(--text-muted);" title="折算成本">' + costHkd + '</div></td>'
            + '<td class="cell-nowrap" style="text-align:right;' + elapsedStyle + '">' + elapsedTotal.toFixed(0) + ' ms</td>'
            + '<td class="cell-nowrap" style="text-align:center;">' + statusBadge + '</td>'
            + '<td class="cell-nowrap" style="text-align:center;">' + feedbackBadge + '</td>'
            + '<td class="cell-nowrap" style="text-align:center;">' + modBadge + '</td>'
            + '<td class="cell-nowrap" style="text-align:center;"><button class="btn btn-secondary" style="padding:3px 8px; font-size:0.75rem;" onclick="openParsingLogModal(' + rid + ')">[查看详情]</button></td>'
            + '</tr>';
    });

    tbody.innerHTML = html;
}

function renderParsingLogsPagination(pagination) {
    const page = pagination.page || 1;
    const totalPages = pagination.total_pages || 1;
    const totalItems = pagination.total_items || 0;

    const pageInfo = document.getElementById('parsingLogsPageInfo');
    const curSpan = document.getElementById('parsingLogsCurrentPageSpan');
    const prevBtn = document.getElementById('btnParsingLogsPrev');
    const nextBtn = document.getElementById('btnParsingLogsNext');

    if (pageInfo) pageInfo.textContent = '第 ' + page + ' / ' + totalPages + ' 页 (共 ' + totalItems + ' 笔)';
    if (curSpan) curSpan.textContent = String(page);
    if (prevBtn) prevBtn.disabled = page <= 1;
    if (nextBtn) nextBtn.disabled = page >= totalPages;
}

function onParsingLogsPageChange(delta) {
    const targetPage = currentParsingLogsState.page + delta;
    if (targetPage < 1 || targetPage > currentParsingLogsState.totalPages) return;
    loadParsingLogs(targetPage);
}

function resetParsingLogFilters() {
    const tSel = document.getElementById('parsingLogTenantSelect');
    const mainTSel = document.getElementById('analyticsTenantSelect');
    const sSel = document.getElementById('parsingLogStatusSelect');
    const gSel = document.getElementById('parsingLogGateSelect');
    const fSel = document.getElementById('parsingLogFeedbackSelect');
    const mSel = document.getElementById('parsingLogModifiedSelect');
    const sInp = document.getElementById('parsingLogSearchInput');

    if (tSel) tSel.value = '';
    if (mainTSel) mainTSel.value = '';
    if (sSel) sSel.value = 'all';
    if (gSel) gSel.value = 'all';
    if (fSel) fSel.value = 'all';
    if (mSel) mSel.value = 'all';
    if (sInp) sInp.value = '';

    const p = window._parsingLogPeriod;
    if (p) {
        p.mode = 'all';
        p.startDate = null;
        p.endDate = null;
    }
    const sEl = document.getElementById('parsingLogStartDate');
    const eEl = document.getElementById('parsingLogEndDate');
    if (sEl) sEl.value = '';
    if (eEl) eEl.value = '';
    const drSel = document.getElementById('parsingLogDateRangeSelect');
    if (drSel) drSel.value = 'all';
    _updateParsingLogPeriodButtons();

    loadParsingLogs(1);
}

function renderParsingLogModalContent(item) {
    if (!item) return;

    // 填充元数据
    const metaRecordId = document.getElementById('plMetaRecordId');
    const metaGraphId = document.getElementById('plMetaGraphId');
    const metaTenantId = document.getElementById('plMetaTenantId');
    const metaSupplier = document.getElementById('plMetaSupplier');
    const metaModel = document.getElementById('plMetaModel');
    const metaCreatedAt = document.getElementById('plMetaCreatedAt');
    const metaGateBadge = document.getElementById('plMetaGateBadge');

    if (metaRecordId) metaRecordId.textContent = '#' + item.record_id;
    if (metaGraphId) metaGraphId.textContent = item.graph_id || ('graph-' + item.record_id);
    if (metaTenantId) metaTenantId.textContent = item.tenant_id || 'default';
    if (metaSupplier) metaSupplier.textContent = item.supplier_name || '未命名供应商';
    if (metaModel) metaModel.textContent = (item.engine || 'vlm') + ' / ' + (item.model || '-');
    if (metaCreatedAt) {
        metaCreatedAt.textContent = _fmtDateTime(item.created_at);
        metaCreatedAt.style.fontFamily = 'var(--font-mono)';
        metaCreatedAt.style.fontVariantNumeric = 'tabular-nums';
    }

    if (metaGateBadge) {
        const gInfo = item.gate_info || {};
        metaGateBadge.className = 'badge';
        const isReject = gInfo.status === 'rejected';
        const isNone = !gInfo.triggered || gInfo.status === 'none';
        metaGateBadge.style.background = isNone ? '#f0fdf4' : (isReject ? '#fee2e2' : '#fef3c7');
        metaGateBadge.style.color = isNone ? '#16a34a' : (isReject ? '#b91c1c' : '#b45309');
        metaGateBadge.style.border = '1px solid ' + (isNone ? '#bbf7d0' : (isReject ? '#fecaca' : '#fde68a'));
        metaGateBadge.style.whiteSpace = 'nowrap';
        metaGateBadge.style.fontWeight = '600';

        if (isNone) {
            metaGateBadge.textContent = '[平算无门禁]';
        } else {
            const reason = _extractGateReason(gInfo);
            metaGateBadge.textContent = (isReject ? '[门禁拦截: ' : '[门禁告警: ') + reason + ']';
        }
        metaGateBadge.title = String(gInfo.summary || metaGateBadge.textContent).trim();
    }

    const statusBadge = document.getElementById('plModalStatusBadge');
    if (statusBadge) {
        if (item.parse_status === 'warning') {
            statusBadge.className = 'badge';
            statusBadge.style.background = '#fef3c7';
            statusBadge.style.color = '#b45309';
            statusBadge.textContent = '[告警通过]';
        } else if (item.parse_status === 'error') {
            statusBadge.className = 'badge';
            statusBadge.style.background = '#fee2e2';
            statusBadge.style.color = '#b91c1c';
            statusBadge.textContent = '[失败]';
        } else {
            statusBadge.className = 'badge';
            statusBadge.style.background = '#dcfce7';
            statusBadge.style.color = '#15803d';
            statusBadge.textContent = '[成功]';
        }
    }

    // 填充 Tab 1: Diff 对照
    const diffBody = document.getElementById('plDiffTableBody');
    const diffBadge = document.getElementById('plDiffSummaryBadge');
    const diffs = item.modification?.diffs || [];

    if (diffs.length > 0) {
        if (diffBadge) diffBadge.textContent = '已修正 ' + item.modification.modified_count + ' 项字段';
        let diffHtml = '';
        diffs.forEach(d => {
            let typeBadge = '<span class="badge" style="background:#eff6ff; color:#1d4ed8;">[已修正]</span>';
            if (d.type === 'added') {
                typeBadge = '<span class="badge" style="background:#dcfce7; color:#15803d;">[新增]</span>';
            } else if (d.type === 'deleted') {
                typeBadge = '<span class="badge" style="background:#fee2e2; color:#b91c1c;">[删除]</span>';
            }

            diffHtml += '<tr style="border-bottom:1px solid #f1f5f9;">'
                + '<td style="padding:8px 10px; font-weight:600;">' + w2Escape(d.label || d.field) + '</td>'
                + '<td style="padding:8px 10px; color:#64748b; font-family:monospace;">' + w2Escape(String(d.ai_value ?? '-')) + '</td>'
                + '<td style="padding:8px 10px; color:#1e293b; font-weight:600; font-family:monospace;">' + w2Escape(String(d.user_value ?? '-')) + '</td>'
                + '<td style="padding:8px 10px; text-align:center;">' + typeBadge + '</td>'
                + '</tr>';
        });
        if (diffBody) diffBody.innerHTML = diffHtml;
    } else {
        if (diffBadge) diffBadge.textContent = '零人工改动 (完全采纳)';
        if (diffBody) {
            diffBody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:#16a34a; font-weight:600;">AI 解析结果与入库数据 100% 保持一致，无人工改动</td></tr>';
        }
    }

    // 填充 Tab 2: Token 消耗与性能瀑布
    const tUsage = item.token_usage || {};
    const pTokenEl = document.getElementById('plTokenPrompt');
    const cTokenEl = document.getElementById('plTokenCompletion');
    const cachedTokenEl = document.getElementById('plTokenCached');
    const hitRateEl = document.getElementById('plTokenCacheHitRate');
    const totTokenEl = document.getElementById('plTokenTotal');
    const costEl = document.getElementById('plCostHkd');

    const promptTokens = tUsage.tokens_prompt || 0;
    const cachedTokens = tUsage.tokens_cached || 0;
    const hitRate = tUsage.cache_hit_rate != null ? (tUsage.cache_hit_rate * 100).toFixed(1) + '%' : (promptTokens > 0 ? ((cachedTokens / promptTokens) * 100).toFixed(1) + '%' : '0.0%');

    if (pTokenEl) pTokenEl.textContent = promptTokens.toLocaleString();
    if (cTokenEl) cTokenEl.textContent = (tUsage.tokens_completion || 0).toLocaleString();
    if (cachedTokenEl) cachedTokenEl.textContent = cachedTokens.toLocaleString();
    if (hitRateEl) hitRateEl.textContent = hitRate;
    if (totTokenEl) totTokenEl.textContent = (tUsage.tokens_total || 0).toLocaleString();
    if (costEl) costEl.textContent = 'HK$' + (tUsage.cost_hkd != null ? tUsage.cost_hkd.toFixed(4) : '0.0000');

    const elapsed = item.elapsed_ms || {};
    const totalMs = elapsed.total || 0;
    const extractMs = elapsed.extract || 0;
    const ragMs = elapsed.rag || 0;
    const auditMs = elapsed.audit || 0;

    const baseMs = Math.max(totalMs, (extractMs + ragMs + auditMs), 1);
    const extractPct = Math.min(100, Math.round((extractMs / baseMs) * 100));
    const ragPct = Math.min(100, Math.round((ragMs / baseMs) * 100));
    const auditPct = Math.min(100, Math.round((auditMs / baseMs) * 100));

    const exText = document.getElementById('plElapsedExtractText');
    const exBar = document.getElementById('plElapsedExtractBar');
    const ragText = document.getElementById('plElapsedRagText');
    const ragBar = document.getElementById('plElapsedRagBar');
    const audText = document.getElementById('plElapsedAuditText');
    const audBar = document.getElementById('plElapsedAuditBar');
    const totText = document.getElementById('plElapsedTotalText');

    if (exText) exText.textContent = extractMs.toFixed(1) + ' ms (' + extractPct + '%)';
    if (exBar) exBar.style.width = extractPct + '%';
    if (ragText) ragText.textContent = ragMs.toFixed(1) + ' ms (' + ragPct + '%)';
    if (ragBar) ragBar.style.width = ragPct + '%';
    if (audText) audText.textContent = auditMs.toFixed(1) + ' ms (' + auditPct + '%)';
    if (audBar) audBar.style.width = auditPct + '%';
    if (totText) totText.textContent = totalMs.toFixed(1) + ' ms';

    // 填充 Tab 3: 质检与门禁规则审计 (四大模块)
    const gateDecEl = document.getElementById('plGateDecisionBody');
    if (gateDecEl) {
        const gInfo = item.gate_info || {};
        const rejDetails = (gInfo.details || []).filter(d => d.level === 'rejected');
        if (rejDetails.length > 0) {
            let html = '<div style="color:#b91c1c; font-weight:600; margin-bottom:6px;">门禁拦截阻断触发:</div><ul style="margin:0; padding-left:18px; color:#b91c1c;">';
            rejDetails.forEach(d => {
                html += '<li>[' + w2Escape(d.type_label || '门禁') + '] ' + w2Escape(d.reason || '') + ' (来源: ' + w2Escape(d.source || '') + ')</li>';
            });
            html += '</ul>';
            gateDecEl.innerHTML = html;
        } else if (gInfo.triggered) {
            gateDecEl.innerHTML = '<span style="color:#b45309; font-weight:600;">[告警放行] ' + w2Escape(gInfo.summary || '触发门禁提示但未阻断流程') + '</span>';
        } else {
            gateDecEl.innerHTML = '<span style="color:#15803d; font-weight:600;">[放行] 判定通过，未触发拦截门禁，全链路平算契约正常</span>';
        }
    }

    const mathEl = document.getElementById('plMathWarningsBody');
    const mWarns = item.warnings?.math_warnings || [];
    if (mathEl) {
        if (mWarns.length > 0) {
            let mHtml = '<ul style="margin:0; padding-left:18px; color:#b45309;">';
            mWarns.forEach(w => { mHtml += '<li>' + w2Escape(typeof w === 'string' ? w : JSON.stringify(w)) + '</li>'; });
            mHtml += '</ul>';
            mathEl.innerHTML = mHtml;
        } else {
            mathEl.innerHTML = '<span style="color:#15803d;">[通过] 算术逻辑平算无异常，合计与总额一致</span>';
        }
    }

    const contractEl = document.getElementById('plContractGuardBody');
    if (contractEl) {
        const contractDetails = (item.gate_info?.details || []).filter(d => d.type === 'contract');
        if (contractDetails.length > 0) {
            let cHtml = '<ul style="margin:0; padding-left:18px; color:#b45309;">';
            contractDetails.forEach(d => { cHtml += '<li>' + w2Escape(d.reason || '') + '</li>'; });
            cHtml += '</ul>';
            contractEl.innerHTML = cHtml;
        } else {
            contractEl.innerHTML = '<span style="color:#15803d;">[通过] 供应商、开单日期格式与明细行结构有效</span>';
        }
    }

    const qualityEl = document.getElementById('plQualityWarningsBody');
    const qWarns = item.warnings?.quality_warnings || [];
    if (qualityEl) {
        if (qWarns.length > 0) {
            let qHtml = '<ul style="margin:0; padding-left:18px; color:#b45309;">';
            qWarns.forEach(w => { qHtml += '<li>' + w2Escape(typeof w === 'string' ? w : JSON.stringify(w)) + '</li>'; });
            qHtml += '</ul>';
            qualityEl.innerHTML = qHtml;
        } else {
            qualityEl.innerHTML = '<span style="color:#15803d;">[通过] 图面清晰度与花码校验正常</span>';
        }
    }

    const imgGuardEl = document.getElementById('plImageGuardBody');
    if (imgGuardEl) {
        if (item.warnings?.image_guard_flagged) {
            const secDetails = (item.gate_info?.details || []).filter(d => d.type === 'security');
            let reasonText = secDetails.length > 0 ? secDetails.map(d => d.reason).join('; ') : '触发图内隐藏文字风险排查';
            imgGuardEl.innerHTML = '<span style="color:#b91c1c; font-weight:600;">[告警] ' + w2Escape(reasonText) + '</span>';
        } else {
            imgGuardEl.innerHTML = '<span style="color:#15803d;">[通过] 未发现图内违规/隐藏文字风险</span>';
        }
    }

    // 整合审计事件列表
    const allEvents = [];
    const seenEventKeys = new Set();

    if (Array.isArray(item.timeline)) {
        item.timeline.forEach(ev => {
            const k = (ev.ts || '') + '_' + (ev.type || '') + '_' + (ev.who || '');
            if (!seenEventKeys.has(k)) {
                seenEventKeys.add(k);
                allEvents.push({
                    ts: ev.ts || '',
                    type: ev.type || 'audit',
                    who: ev.who || 'system',
                    details: ev.details || {}
                });
            }
        });
    }

    if (Array.isArray(item.audit_events)) {
        item.audit_events.forEach(ev => {
            const k = (ev.ts || '') + '_' + (ev.event_type || '') + '_' + (ev.account_id || '');
            if (!seenEventKeys.has(k)) {
                seenEventKeys.add(k);
                allEvents.push({
                    ts: ev.ts || '',
                    type: ev.event_type || 'event',
                    who: ev.account_id || 'system',
                    details: ev.properties || {}
                });
            }
        });
    }

    // 填充 Tab 4 节点流: 单据全息生命周期时序流转轨迹
    const flowChainEl = document.getElementById('plLifecycleFlowChain');
    if (flowChainEl) {
        const parseStatus = item.parse_status || '';
        const hasUpload = true;
        const hasParse = ['parsed', 'parsed_with_warnings', 'edited', 'approved', 'success', 'warning'].includes(parseStatus) || allEvents.some(e => ['parse_started', 'ocr_parse_started', 'parsed'].includes(e.type));
        const gateInfo = item.gate_info || {};
        const hasGate = (gateInfo.status != null) || hasParse;
        const gateStatus = gateInfo.status || 'none';
        const isApproved = (item.status === 'approved' || parseStatus === 'approved' || allEvents.some(e => ['receipt_approved', 'approve_receipt'].includes(e.type)));
        const isEdited = ((item.modification && item.modification.is_modified) || allEvents.some(e => ['receipt_review_submitted', 'save_edited'].includes(e.type)));
        const hasReview = isApproved || isEdited;
        const hasFeedback = (item.feedback && item.feedback.like != null) || allEvents.some(e => (e.type || '').includes('feedback'));

        const stages = [
            {
                id: 'upload',
                label: '1. 图片上传',
                active: hasUpload,
                tag: '[OK] 已上传',
                bg: '#dcfce7',
                color: '#15803d',
                border: '#bbf7d0'
            },
            {
                id: 'parse',
                label: '2. 模型解析',
                active: hasParse,
                tag: parseStatus === 'error' ? '[FAIL] 解析失败' : '[OK] 解析完成',
                bg: parseStatus === 'error' ? '#fee2e2' : '#eff6ff',
                color: parseStatus === 'error' ? '#b91c1c' : '#1d4ed8',
                border: parseStatus === 'error' ? '#fecaca' : '#bfdbfe'
            },
            {
                id: 'gate',
                label: '3. 门禁校验',
                active: hasGate,
                tag: gateStatus === 'rejected' ? '[FAIL] 拦截阻断' : (gateStatus === 'warning' ? '[WARN] 告警放行' : '[OK] 校验通过'),
                bg: gateStatus === 'rejected' ? '#fee2e2' : (gateStatus === 'warning' ? '#fef3c7' : '#f0fdf4'),
                color: gateStatus === 'rejected' ? '#b91c1c' : (gateStatus === 'warning' ? '#b45309' : '#16a34a'),
                border: gateStatus === 'rejected' ? '#fecaca' : (gateStatus === 'warning' ? '#fde68a' : '#bbf7d0')
            },
            {
                id: 'review',
                label: '4. 人工复核',
                active: hasReview,
                tag: isApproved ? '[OK] 审核通过' : (isEdited ? '[OK] 已修正' : '[待复核]'),
                bg: isApproved ? '#dcfce7' : (isEdited ? '#eff6ff' : '#f1f5f9'),
                color: isApproved ? '#15803d' : (isEdited ? '#1d4ed8' : '#94a3b8'),
                border: isApproved ? '#bbf7d0' : (isEdited ? '#bfdbfe' : '#e2e8f0')
            },
            {
                id: 'feedback',
                label: '5. 体验评价',
                active: hasFeedback,
                tag: (item.feedback && item.feedback.like === 1) ? '[OK] 满意(点赞)' : ((item.feedback && item.feedback.like === -1) ? '[WARN] 点踩反馈' : (hasFeedback ? '[OK] 已评价' : '[待评价]')),
                bg: (item.feedback && item.feedback.like === 1) ? '#dcfce7' : ((item.feedback && item.feedback.like === -1) ? '#fee2e2' : (hasFeedback ? '#eff6ff' : '#f1f5f9')),
                color: (item.feedback && item.feedback.like === 1) ? '#15803d' : ((item.feedback && item.feedback.like === -1) ? '#b91c1c' : (hasFeedback ? '#1d4ed8' : '#94a3b8')),
                border: (item.feedback && item.feedback.like === 1) ? '#bbf7d0' : ((item.feedback && item.feedback.like === -1) ? '#fecaca' : (hasFeedback ? '#bfdbfe' : '#e2e8f0'))
            }
        ];

        let chainHtml = '';
        stages.forEach((st, idx) => {
            if (idx > 0) {
                chainHtml += '<span style="color:#94a3b8; font-weight:700; user-select:none;">&rarr;</span>';
            }
            chainHtml += '<span class="badge" style="background:' + st.bg + '; color:' + st.color + '; border:1px solid ' + st.border + '; font-weight:600; padding:4px 10px; font-size:0.75rem;">'
                + w2Escape(st.label) + ' ' + w2Escape(st.tag)
                + '</span>';
        });

        flowChainEl.innerHTML = chainHtml;
    }

    // 填充 Tab 4 审计流水表格
    const tlBody = document.getElementById('plTimelineBody');
    if (tlBody) {
        // 审计流水强制统一按时间倒序 (最新在前) 排序
        allEvents.sort((a, b) => String(b.ts || '').localeCompare(String(a.ts || '')));

        if (allEvents.length > 0) {
            const EVENT_TYPE_NAMES = {
                'upload': '图片上传',
                'parse_started': '模型解析触发',
                'ocr_parse_started': 'OCR/VLM抽取',
                'contract_guard_checked': '契约门禁核对',
                'math_guard_checked': '算术平算检验',
                'gate_checked': '全链路门禁判定',
                'receipt_review_submitted': '用户复核提交',
                'receipt_approved': '单据审核入账',
                'approve_receipt': '财务审核批准',
                'receipt_flagged': '标记异常单据',
                'flag_receipt': '异常单据告警',
                'retake_clicked': '重拍补录挽回',
                'reparse_clicked': '重跑识别挽回',
                'feedback': '满意度评价',
                'receipt_feedback': '反馈点赞/点踩',
                'save_edited': '人工保存修改',
                'auto_save': '系统自动归档'
            };

            let tlHtml = '';
            allEvents.forEach(ev => {
                const rawType = ev.type || 'audit';
                const label = EVENT_TYPE_NAMES[rawType] || rawType;
                let typeBadge = '<span class="badge" style="background:#eff6ff; color:#0369a1; border:1px solid #bfdbfe; font-weight:600;">' + w2Escape(label) + '</span>';
                if (rawType.includes('gate') || rawType.includes('guard')) {
                    typeBadge = '<span class="badge" style="background:#fef3c7; color:#b45309; border:1px solid #fde68a; font-weight:600;">' + w2Escape(label) + '</span>';
                } else if (rawType.includes('approve') || rawType === 'upload') {
                    typeBadge = '<span class="badge" style="background:#dcfce7; color:#15803d; border:1px solid #bbf7d0; font-weight:600;">' + w2Escape(label) + '</span>';
                } else if (rawType.includes('flag') || rawType.includes('error')) {
                    typeBadge = '<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #fecaca; font-weight:600;">' + w2Escape(label) + '</span>';
                }

                let detailsText = '';
                try {
                    detailsText = JSON.stringify(ev.details || {}, null, 2);
                } catch (e) {
                    detailsText = String(ev.details || '-');
                }

                tlHtml += '<tr style="border-bottom:1px solid #f1f5f9;">'
                    + '<td style="padding:6px 10px; font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:0.75rem; color:#64748b; vertical-align:top; white-space:nowrap; text-align:left;">' + w2Escape(_fmtDateTime(ev.ts)) + '</td>'
                    + '<td style="padding:6px 10px; vertical-align:top;"><div>' + typeBadge + '</div><div style="font-size:0.7rem; color:var(--text-muted); font-family:monospace; margin-top:3px;">' + w2Escape(rawType) + '</div></td>'
                    + '<td style="padding:6px 10px; color:#475569; vertical-align:top; font-weight:500;">' + w2Escape(ev.who || 'system') + '</td>'
                    + '<td style="padding:6px 10px; vertical-align:top;"><pre style="margin:0; font-family:monospace; font-size:0.75rem; color:#475569; max-height:85px; overflow-y:auto; background:#f8fafc; padding:4px 8px; border-radius:4px; border:1px solid #e2e8f0; white-space:pre-wrap; word-break:break-all;">' + w2Escape(detailsText) + '</pre></td>'
                    + '</tr>';
            });
            tlBody.innerHTML = tlHtml;
        } else {
            tlBody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:var(--text-muted);">暂无链路审计事件留痕</td></tr>';
        }
    }

    // 填充 Tab 5: LLM 原生调用审计 (LLM Prompt & IO)
    const llmCalls = item.llm_calls || [];
    const llmSummaryBadge = document.getElementById('plLlmSummaryBadge');
    if (llmSummaryBadge) {
        llmSummaryBadge.textContent = llmCalls.length + ' 次调用';
    }
    const llmContainer = document.getElementById('plLlmAccordionContainer');
    if (llmContainer) {
        if (llmCalls.length === 0) {
            llmContainer.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted); background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">暂无 LLM 原生调用审计记录</div>';
        } else {
            let accHtml = '';
            llmCalls.forEach((call, cIdx) => {
                const callId = call.id || (cIdx + 1);
                const attemptNum = call.attempt || 1;
                const legLabel = call.leg_label || (call.leg + ' 腿');
                const isSuccess = call.status === 'success';
                const statusTag = isSuccess ? '[OK]' : '[FAIL]';
                const statusColor = isSuccess ? '#16a34a' : '#dc2626';
                const statusBg = isSuccess ? '#dcfce7' : '#fee2e2';
                const elapsedMs = (call.elapsed_ms != null ? Number(call.elapsed_ms).toFixed(1) : '0.0');
                const tu = call.token_usage || {};
                const totalTokens = tu.total_tokens || 0;
                const cachedTokens = tu.cached_tokens || 0;
                const hitRatePct = (tu.cache_hit_rate != null ? (tu.cache_hit_rate * 100).toFixed(1) : '0.0') + '%';
                const modelName = call.model || '-';
                const collapseId = 'plLlmCollapse_' + callId;

                // 格式化 prompt 内容展示 (图片用纯文本卡片呈现)
                let promptRenderHtml = '';
                const pMsgs = Array.isArray(call.prompt_messages) ? call.prompt_messages : [];
                if (pMsgs.length === 0) {
                    promptRenderHtml = '<div style="color:var(--text-muted); font-size:0.75rem;">(空提示词)</div>';
                } else {
                    pMsgs.forEach(m => {
                        const mRole = m.role || 'user';
                        let roleBadge = '<span class="badge" style="background:#f1f5f9; color:#475569; font-size:0.7rem; font-weight:600;">[' + w2Escape(mRole.toUpperCase()) + ']</span>';
                        if (mRole === 'system') {
                            roleBadge = '<span class="badge" style="background:#e0e7ff; color:#3730a3; font-size:0.7rem; font-weight:600;">[SYSTEM]</span>';
                        } else if (mRole === 'assistant') {
                            roleBadge = '<span class="badge" style="background:#dbeafe; color:#1e40af; font-size:0.7rem; font-weight:600;">[ASSISTANT]</span>';
                        }
                        let contentHtml = '';
                        if (typeof m.content === 'string') {
                            contentHtml = '<div style="white-space:pre-wrap; word-break:break-all; font-family:monospace; font-size:0.75rem; color:#334155; margin-top:4px;">' + w2Escape(m.content) + '</div>';
                        } else if (Array.isArray(m.content)) {
                            m.content.forEach(b => {
                                if (b.type === 'text') {
                                    contentHtml += '<div style="white-space:pre-wrap; word-break:break-all; font-family:monospace; font-size:0.75rem; color:#334155; margin-top:4px;">' + w2Escape(b.text || '') + '</div>';
                                } else if (b.type === 'image_ref' && b.image_ref) {
                                    const ir = b.image_ref;
                                    const sizeKb = ir.byte_size ? (ir.byte_size / 1024).toFixed(1) + ' KB' : '未知大小';
                                    const pathName = ir.source_path ? ir.source_path.split('/').pop() : 'receipt_image';
                                    contentHtml += '<div style="margin:6px 0; padding:6px 10px; background:#eff6ff; border:1px solid #bfdbfe; border-radius:6px; font-size:0.75rem; color:#1e40af; display:inline-flex; align-items:center; gap:8px;">'
                                        + '<strong>[IMAGE_REF]</strong> '
                                        + '<span>' + w2Escape(pathName) + ' (' + w2Escape(sizeKb) + ')</span>'
                                        + '<span style="font-family:monospace; color:#64748b; font-size:0.7rem;">sha256:' + w2Escape((ir.sha256 || '').slice(0, 10)) + '...</span>'
                                        + '</div>';
                                } else {
                                    contentHtml += '<div style="white-space:pre-wrap; word-break:break-all; font-family:monospace; font-size:0.75rem; color:#334155; margin-top:4px;">' + w2Escape(JSON.stringify(b)) + '</div>';
                                }
                            });
                        }
                        promptRenderHtml += '<div style="margin-bottom:8px; padding-bottom:8px; border-bottom:1px dashed #e2e8f0;">' + roleBadge + contentHtml + '</div>';
                    });
                }

                // 原始输出
                const rawOutput = call.raw_output || '';
                const rawOutputEscaped = w2Escape(rawOutput);

                // 一键复制辅助
                const copyPromptBtn = '<button type="button" class="btn btn-secondary btn-sm" onclick="copyPLText(\'' + collapseId + '_prompt\')" style="padding:2px 8px; font-size:0.7rem;">复制 Prompt</button>';
                const copyOutputBtn = '<button type="button" class="btn btn-secondary btn-sm" onclick="copyPLText(\'' + collapseId + '_output\')" style="padding:2px 8px; font-size:0.7rem;">复制 Output</button>';

                accHtml += '<div class="card" style="margin-bottom:0; padding:0; border:1px solid #e2e8f0; border-radius:8px; overflow:hidden;">'
                    + '<div onclick="togglePLLlmAccordion(\'' + collapseId + '\')" style="padding:10px 16px; background:#f8fafc; cursor:pointer; display:flex; justify-content:space-between; align-items:center; user-select:none; border-bottom:1px solid #e2e8f0;">'
                    + '  <div style="display:flex; align-items:center; gap:10px; flex-wrap:wrap; font-size:0.8rem;">'
                    + '    <strong style="color:var(--text-primary);">[轮次 ' + attemptNum + ' · ' + w2Escape(legLabel) + ']</strong>'
                    + '    <span class="badge" style="background:' + statusBg + '; color:' + statusColor + '; font-weight:700;">' + statusTag + '</span>'
                    + '    <span style="color:var(--text-muted);">耗时: ' + elapsedMs + 'ms</span>'
                    + '    <span style="color:var(--text-muted);">|</span>'
                    + '    <span>Tokens: <strong>' + totalTokens.toLocaleString() + '</strong> (Cached: <strong style="color:#059669;">' + cachedTokens.toLocaleString() + ' / ' + hitRatePct + '</strong>)</span>'
                    + '    <span style="color:var(--text-muted);">|</span>'
                    + '    <span style="color:#475569;">模型: ' + w2Escape(modelName) + '</span>'
                    + '  </div>'
                    + '  <div style="font-size:0.8rem; color:var(--text-muted); font-weight:bold;" id="' + collapseId + '_icon">[展开]</div>'
                    + '</div>'
                    + '<div id="' + collapseId + '" style="display:none; padding:14px; background:#ffffff;">'
                    + '  <div style="display:grid; grid-template-columns: 1fr 1fr; gap:14px;">'
                    + '    <div style="display:flex; flex-direction:column; min-width:0;">'
                    + '      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">'
                    + '        <strong style="font-size:0.8rem; color:var(--text-primary);">输入提示词 (Prompt Messages)</strong>'
                    + '        ' + copyPromptBtn
                    + '      </div>'
                    + '      <div id="' + collapseId + '_prompt" style="max-height:360px; overflow-y:auto; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px;">'
                    +          promptRenderHtml
                    + '      </div>'
                    + '    </div>'
                    + '    <div style="display:flex; flex-direction:column; min-width:0;">'
                    + '      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">'
                    + '        <strong style="font-size:0.8rem; color:var(--text-primary);">模型原生输出 (Raw Output)</strong>'
                    + '        ' + copyOutputBtn
                    + '      </div>'
                    + '      <pre id="' + collapseId + '_output" style="max-height:360px; overflow-y:auto; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px; margin:0; font-family:monospace; font-size:0.75rem; color:#1e293b; white-space:pre-wrap; word-break:break-all;">'
                    +          (rawOutputEscaped || '<span style="color:var(--text-muted);">(无输出)</span>')
                    + '      </pre>'
                    + '    </div>'
                    + '  </div>'
                    + '</div>'
                    + '</div>';
            });
            llmContainer.innerHTML = accHtml;
        }
    }
}

function openParsingLogModal(recordId) {
    let item = currentParsingLogsState.items.find(x => x.record_id === recordId);
    if (!item && currentParsingLogsState.activeItem && currentParsingLogsState.activeItem.record_id === recordId) {
        item = currentParsingLogsState.activeItem;
    }

    if (item) {
        currentParsingLogsState.activeItem = item;
        renderParsingLogModalContent(item);
    } else {
        const metaRecordId = document.getElementById('plMetaRecordId');
        if (metaRecordId) metaRecordId.textContent = '#' + recordId;
        const diffBody = document.getElementById('plDiffTableBody');
        if (diffBody) diffBody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:var(--text-muted);">正在加载单据明细...</td></tr>';
        const tlBody = document.getElementById('plTimelineBody');
        if (tlBody) tlBody.innerHTML = '<tr><td colspan="4" style="text-align:center; padding:20px; color:var(--text-muted);">正在拉取时序审计事件...</td></tr>';
    }

    // 默认切换到 Tab 1 并打开模态
    switchParsingLogTab('diff');
    const modal = document.getElementById('parsingLogModal') || document.getElementById('parsingLogDetailModal');
    if (modal) modal.classList.remove('hide');

    // 异步请求最新明细与全息事件流
    fetch('/api/analytics/parsing-logs/detail?record_id=' + encodeURIComponent(recordId), {
        headers: { 'Accept': 'application/json' }
    })
    .then(res => res.json())
    .then(resData => {
        if (resData && resData.status === 'success' && resData.data) {
            currentParsingLogsState.activeItem = resData.data;
            renderParsingLogModalContent(resData.data);
        }
    })
    .catch(err => {
        console.warn('[WARN] 获取解析日志详情失败:', err);
    });
}

function openParsingLogDetailModal(recordId) {
    return openParsingLogModal(recordId);
}

function closeParsingLogModal() {
    const modal = document.getElementById('parsingLogModal') || document.getElementById('parsingLogDetailModal');
    if (modal) modal.classList.add('hide');
}

function switchParsingLogTab(tabKey) {
    const tabs = ['diff', 'token', 'guard', 'audit', 'llm'];
    tabs.forEach(t => {
        const btn = document.getElementById('btnPLTab' + t.charAt(0).toUpperCase() + t.slice(1));
        const content = document.getElementById('plTabContent' + t.charAt(0).toUpperCase() + t.slice(1));
        const isMatch = (t === tabKey);
        if (btn) {
            btn.style.borderBottomColor = isMatch ? '#2563eb' : 'transparent';
            btn.style.color = isMatch ? '#2563eb' : 'var(--text-muted)';
            btn.style.fontWeight = isMatch ? '600' : 'normal';
        }
        if (content) {
            content.style.display = isMatch ? 'block' : 'none';
        }
    });
}

function togglePLLlmAccordion(collapseId) {
    const el = document.getElementById(collapseId);
    const icon = document.getElementById(collapseId + '_icon');
    if (!el) return;
    const isHidden = (el.style.display === 'none');
    el.style.display = isHidden ? 'block' : 'none';
    if (icon) {
        icon.textContent = isHidden ? '[收起]' : '[展开]';
    }
}

function copyPLText(elementId) {
    const el = document.getElementById(elementId);
    if (!el) return;
    const text = el.innerText || el.textContent || '';
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showToast('复制成功', 'success');
        }).catch(() => {
            showToast('复制失败，请手动选取', 'warning');
        });
    } else {
        showToast('浏览器不支持剪贴板API，请手动选取', 'warning');
    }
}

// 暴露全局 API
window.loadParsingLogs = loadParsingLogs;
window.renderParsingLogsTable = renderParsingLogsTable;
window.renderParsingLogsPagination = renderParsingLogsPagination;
window.onParsingLogsPageChange = onParsingLogsPageChange;
window.resetParsingLogFilters = resetParsingLogFilters;
window.openParsingLogModal = openParsingLogModal;
window.openParsingLogDetailModal = openParsingLogModal;
window.openParsingLogDetail = openParsingLogModal;
window.closeParsingLogModal = closeParsingLogModal;
window.switchParsingLogTab = switchParsingLogTab;
window.togglePLLlmAccordion = togglePLLlmAccordion;
window.copyPLText = copyPLText;





const ACCESSIBLE_NAME_FALLBACK = {
    chkTreatment: '识别处理',
    receiptFile: '收据图片',
    receiptCamera: '拍照',
    retakeFile: '重新选择图片',
    parsingLogTenantSelect: '日志租户',
    parsingLogEndDate: '解析日志结束日期',
    eventsEndDate: '事件结束日期',
    kpiTrendStartDate: '开始日期',
    kpiTrendEndDate: '结束日期',
    adminGreySlider: '灰度比例',
    dishCategoryFilter: '餐品分类',
    finSupplier: '供应商',
    fltFinMethod: '付款方式',
    fltFinDue: '到期状态',
    adminGreyAuditEnabled: '灰度审核',
    adminGreyParseEnabled: '灰度解析',
    greySampleScopeSelect: '灰度样本范围',
    pvalueExperimentSelect: '实验',
    expSampleScopeSelect: '样本范围',
    goldenScopeSelect: '金标范围',
};

function ensureAccessibleNames(root) {
    const scope = root || document;
    if (!scope || !scope.querySelectorAll) return;
    scope.querySelectorAll('input, select, textarea').forEach(el => {
        if (!el || el.type === 'hidden') return;
        if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return;
        if (el.id && scope.querySelector('label[for="' + el.id + '"]')) return;
        const wrapLabel = el.closest ? el.closest('label') : null;
        if (wrapLabel && (wrapLabel.textContent || '').trim()) return;
        const prev = el.previousElementSibling;
        if (prev && prev.tagName === 'LABEL' && !(prev.getAttribute('for')) && el.id) {
            prev.setAttribute('for', el.id);
            return;
        }
        const group = el.closest ? el.closest('.form-group') : null;
        if (group && el.id) {
            const lab = group.querySelector('label');
            if (lab && !lab.getAttribute('for') && (lab.textContent || '').trim()) {
                lab.setAttribute('for', el.id);
                return;
            }
        }
        let nearby = '';
        if (prev) nearby = (prev.textContent || '').trim().replace(/[:：]\s*$/, '');
        if (nearby && nearby.length <= 20) {
            el.setAttribute('aria-label', nearby);
            return;
        }
        const ph = el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name');
        if (ph) {
            el.setAttribute('aria-label', ph);
            return;
        }
        if (el.id && ACCESSIBLE_NAME_FALLBACK[el.id]) el.setAttribute('aria-label', ACCESSIBLE_NAME_FALLBACK[el.id]);
    });
}
