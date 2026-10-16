// demo/static/js/admin_observation.js
// 管理台观测与实验域：观测台 stale-response 守卫、时间格式统一标准、灰测用户
// 使用状态与脱敏样本、黄金样本 57 看板、p-value 卡片、埋点观测台、事件明细
// 审计 Modal、实验覆写与结论、KPI 趋势的载体页钩子。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// F3：观测台加载器 stale-response 守卫（对齐 dailyConsumptionReqSeq 模式）。
// 每个加载器独立计数：仅当响应仍属该加载器最新一次发起时才允许渲染/写选择器。
const _analyticsLoadSeq = { recovery: 0, greySamples: 0, greyStatus: 0, experiments: 0, goldenBoard: 0 };
function _analyticsSeq(key) {
    _analyticsLoadSeq[key] = (_analyticsLoadSeq[key] || 0) + 1;
    const seq = _analyticsLoadSeq[key];
    return () => seq === _analyticsLoadSeq[key];
}

// =====================================================================
// 观测中心数据展示与时间格式统一标准 (Consistency Standard)
// 标准时间格式: YYYY-MM-DD HH:mm:ss (等宽字体，定长19位，剥除微秒/T/时区)
// 标准日期格式: YYYY-MM-DD
// 统一空值占位: -
// =====================================================================
function _fmtDateTime(val) {
    if (val === null || val === undefined || val === '') return '-';
    if (val instanceof Date) {
        if (isNaN(val.getTime())) return '-';
        const Y = val.getFullYear();
        const M = String(val.getMonth() + 1).padStart(2, '0');
        const D = String(val.getDate()).padStart(2, '0');
        const h = String(val.getHours()).padStart(2, '0');
        const m = String(val.getMinutes()).padStart(2, '0');
        const s = String(val.getSeconds()).padStart(2, '0');
        return `${Y}-${M}-${D} ${h}:${m}:${s}`;
    }
    if (typeof val === 'number') {
        if (isNaN(val) || val <= 0) return '-';
        const ms = val < 1e11 ? val * 1000 : val;
        const d = new Date(ms);
        if (isNaN(d.getTime())) return '-';
        return _fmtDateTime(d);
    }
    let s = String(val).trim();
    if (!s || s === 'None' || s === 'null' || s === 'undefined' || s === '-' || s === '—') return '-';
    if (/^\d{10,13}$/.test(s)) {
        return _fmtDateTime(Number(s));
    }
    s = s.replace('T', ' ');
    const dotIdx = s.indexOf('.');
    if (dotIdx > 0) s = s.substring(0, dotIdx);
    const plusIdx = s.indexOf('+');
    if (plusIdx > 0) s = s.substring(0, plusIdx);
    if (s.endsWith('Z')) s = s.substring(0, s.length - 1);
    s = s.trim();
    if (s.length > 19) s = s.substring(0, 19);
    if (/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(s)) {
        return s;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(s)) {
        return s + ' 00:00:00';
    }
    return s || '-';
}

function _fmtDateOnly(val) {
    if (val === null || val === undefined || val === '') return '-';
    if (val instanceof Date) {
        if (isNaN(val.getTime())) return '-';
        const Y = val.getFullYear();
        const M = String(val.getMonth() + 1).padStart(2, '0');
        const D = String(val.getDate()).padStart(2, '0');
        return `${Y}-${M}-${D}`;
    }
    if (typeof val === 'number') {
        if (isNaN(val) || val <= 0) return '-';
        const ms = val < 1e11 ? val * 1000 : val;
        const d = new Date(ms);
        if (isNaN(d.getTime())) return '-';
        return _fmtDateOnly(d);
    }
    let s = String(val).trim();
    if (!s || s === 'None' || s === 'null' || s === 'undefined' || s === '-' || s === '—') return '-';
    if (/^\d{10,13}$/.test(s)) {
        return _fmtDateOnly(Number(s));
    }
    s = s.replace('T', ' ');
    if (s.length >= 10 && s.indexOf('-') === 4) {
        return s.substring(0, 10);
    }
    return s || '-';
}

// -------------------------------------------------------------
// Admin 灰测用户使用状态与脱敏样本观测逻辑
// -------------------------------------------------------------
let _adminGreySamplesCache = [];

function loadAdminGreySamples(tenantId, isManual = false) {
    const tbody = document.getElementById('adminGreySamplesBody');
    if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#666;">正在拉取脱敏单据流并执行 PII 过滤与 AI 效果评估...</td></tr>';

    const noticeEl = document.getElementById('adminGreySampleLimitNotice');
    if (noticeEl) noticeEl.style.display = 'none';

    const scopeSel = document.getElementById('greySampleScopeSelect');
    const scope = scopeSel ? scopeSel.value : 'all';
    const tId = tenantId || (document.getElementById('analyticsTenantSelect')?.value) || 'all';
    let url = '/api/admin/grey-test/samples?scope=' + encodeURIComponent(scope);
    if (tId && tId !== 'all') {
        url += '&tenant_id=' + encodeURIComponent(tId);
    }
    const isCurrent = _analyticsSeq('greySamples');

    apiFetch(url)
        .then(r => r.json())
        .then(res => {
            if (!isCurrent()) return;
            if (!res || res.status !== 'success' || !res.samples) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00;">拉取失败，请确认是否具备 admin 权限</td></tr>';
                if (noticeEl) noticeEl.style.display = 'none';
                return;
            }
            _adminGreySamplesCache = res.samples;
            
            // 更新指标
            const mTotal = document.getElementById('mGreyTotal');
            const mAppr = document.getElementById('mGreyApproved');
            const mEdit = document.getElementById('mGreyEdited');
            const mCanary = document.getElementById('mGreyCanary');
            const mPos = document.getElementById('mGreyPositive');
            const mMod = document.getElementById('mGreyModified');
            const mAvg = document.getElementById('mGreyAvgMatch');

            if (mTotal) mTotal.textContent = res.total_count || 0;
            if (mAppr) mAppr.textContent = res.user_approved_count || 0;
            if (mEdit) mEdit.textContent = res.user_edited_count || 0;
            if (mCanary) mCanary.textContent = res.grey_count || 0;
            if (mPos) mPos.textContent = res.positive_feedback_count || 0;
            if (mMod) mMod.textContent = res.modified_feedback_count || 0;
            // avg_match_rate 只由有真实 AI 预填的样本贡献；无可比样本时如实显示「-」
            if (mAvg) mAvg.textContent = (res.avg_match_rate != null ? res.avg_match_rate + '%' : '-');

            const totalCount = res.total_count || res.samples.length;
            if (noticeEl) {
                if (totalCount > 100) {
                    noticeEl.textContent = `* 仅展示最新 100 条脱敏单据抽样观测记录，全量样本共 ${totalCount} 份`;
                    noticeEl.style.display = 'block';
                } else {
                    noticeEl.style.display = 'none';
                }
            }

            if (res.samples.length === 0) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#999;">暂无单据样本</td></tr>';
                return;
            }

            const displaySamples = res.samples.slice(0, 100);
            let html = '';
            displaySamples.forEach((s, idx) => {
                const statusBadge = s.is_approved
                    ? '<span class="badge" style="background:#d4edda; color:#155724;">老板已入库</span>'
                    : (s.is_user_edited
                        ? '<span class="badge" style="background:#cce5ff; color:#004085;">店员已编辑</span>'
                        : '<span class="badge" style="background:#fff3cd; color:#856404;">待复核</span>');
                
                const engineBadge = s.use_grey
                    ? '<span class="badge-grey">灰测实验组</span>'
                    : '<span class="badge" style="background:#e9ecef; color:#495057;">常规线上组</span>';

                const evalData = s.effect_evaluation || {};
                // match_rate 为 null（无 AI 预填可比对）时显示「-」，不再回退 100%
                const matchRateText = '(' + (evalData.match_rate != null ? evalData.match_rate + '%' : '-') + ')';
                const feedbackBadge = `<span class="badge" style="background:${evalData.feedback_badge_bg || '#e2e3e5'}; color:${evalData.feedback_badge_color || '#383d41'}; font-size:0.75rem;">${w2Escape(evalData.feedback_label || '待复核')} ${w2Escape(matchRateText)}</span>`;

                html += `
                    <tr>
                        <td><strong>#${w2Escape(s.receipt_id)}</strong></td>
                        <td class="cell-vendor"><span style="color:#2f6b4f; font-weight:600;">${w2Escape(s.masked_vendor)}</span> <small style="color:#999; margin-left:4px;">(已脱敏)</small></td>
                        <td class="cell-doc-form"><code>${w2Escape(s.doc_form)}</code></td>
                        <td class="cell-amount"><code>${w2Escape(s.masked_total)}</code></td>
                        <td>${engineBadge}</td>
                        <td>${statusBadge}</td>
                        <td>${feedbackBadge}</td>
                        <td class="cell-actions">
                            <button class="btn btn-secondary" style="font-size:0.75rem; padding:3px 10px;" onclick="viewGreySampleDetail(${idx})">查看脱敏解析</button>
                        </td>
                    </tr>
                `;
            });
            if (tbody) tbody.innerHTML = html;
        })
        .catch(err => {
            console.error(err);
            if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00;">网络异常，请重试</td></tr>';
        });
}

const DESENSITIZED_IMG_FALLBACK = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(`
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 480" width="100%" height="100%" fill="none">
    <rect width="360" height="480" rx="8" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>
    <g fill="#94a3b8">
        <rect x="30" y="36" width="140" height="14" rx="3" fill="#64748b"/>
        <rect x="30" y="60" width="220" height="8" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="74" width="180" height="8" rx="2" fill="#cbd5e1"/>
        <line x1="30" y1="96" x2="330" y2="96" stroke="#cbd5e1" stroke-dasharray="4 4"/>
        <rect x="30" y="112" width="100" height="10" rx="2" fill="#94a3b8"/>
        <rect x="250" y="112" width="80" height="10" rx="2" fill="#94a3b8"/>
        <rect x="30" y="136" width="120" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="260" y="136" width="70" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="160" width="90" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="250" y="160" width="80" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="30" y="184" width="130" height="10" rx="2" fill="#cbd5e1"/>
        <rect x="270" y="184" width="60" height="10" rx="2" fill="#cbd5e1"/>
        <line x1="30" y1="210" x2="330" y2="210" stroke="#cbd5e1" stroke-dasharray="4 4"/>
        <rect x="30" y="226" width="80" height="12" rx="2" fill="#64748b"/>
        <rect x="240" y="226" width="90" height="12" rx="2" fill="#64748b"/>
    </g>
    <rect x="30" y="260" width="300" height="64" rx="6" fill="#f1f5f9" stroke="#e2e8f0"/>
    <text x="180" y="288" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="12" font-weight="600" fill="#475569" text-anchor="middle">脱敏单据切片原图保护</text>
    <text x="180" y="308" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-size="10" fill="#94a3b8" text-anchor="middle">原始单据图像已脱敏隔离存储</text>
</svg>
`);

function viewGreySampleDetail(idx) {
    const s = _adminGreySamplesCache[idx];
    if (!s) return;
    
    // 1. 填充 Modal
    const modal = document.getElementById('adminGreySampleModal');
    const mTitle = document.getElementById('adminGreyModalTitle');
    const mImg = document.getElementById('adminGreyModalImg');
    const mVendor = document.getElementById('mModalVendor');
    const mTotal = document.getElementById('mModalTotal');
    const mForm = document.getElementById('mModalForm');
    const mStatus = document.getElementById('mModalStatus');
    const mEngine = document.getElementById('mModalEngine');
    const mItemsBody = document.getElementById('adminGreyModalItemsBody');
    const mJson = document.getElementById('adminGreyModalJson');

    // 效果评估组件
    const mBadge = document.getElementById('mModalFeedbackBadge');
    const mMatchRate = document.getElementById('mModalMatchRate');
    const mMatchedFields = document.getElementById('mModalMatchedFields');
    const mModifiedFields = document.getElementById('mModalModifiedFields');
    const mCompareBody = document.getElementById('mModalFieldCompareBody');

    const evalData = s.effect_evaluation || {};

    if (mTitle) mTitle.textContent = `单据 #${s.receipt_id} 脱敏解析详情与图像切片（${s.masked_vendor} · ${s.user_status}）`;
    const mWorkbenchLink = document.getElementById('mModalWorkbenchLink');
    if (mWorkbenchLink) {
        mWorkbenchLink.href = '/evalset/workbench/' + encodeURIComponent(s.receipt_id);
    }
    if (mImg) {
        mImg.onerror = function() {
            this.onerror = null;
            this.src = DESENSITIZED_IMG_FALLBACK;
        };
        mImg.src = s.image_url || DESENSITIZED_IMG_FALLBACK;
    }
    if (mVendor) mVendor.textContent = s.masked_vendor || '-';
    if (mTotal) mTotal.textContent = s.masked_total || '-';
    if (mForm) mForm.textContent = s.doc_form || '-';
    if (mStatus) {
        mStatus.innerHTML = s.is_approved
            ? '<span class="badge" style="background:#d4edda; color:#155724;">老板已入库</span>'
            : (s.is_user_edited
                ? '<span class="badge" style="background:#cce5ff; color:#004085;">店员已编辑</span>'
                : '<span class="badge" style="background:#fff3cd; color:#856404;">待复核</span>');
    }
    if (mEngine) mEngine.textContent = s.engine || '-';

    // 填充效果评估卡
    if (mBadge) {
        mBadge.textContent = evalData.feedback_label || '待复核';
        mBadge.style.background = evalData.feedback_badge_bg || '#e2e3e5';
        mBadge.style.color = evalData.feedback_badge_color || '#383d41';
    }
    if (mMatchRate) mMatchRate.textContent = (evalData.match_rate != null ? evalData.match_rate + '%' : '-');
    if (mMatchedFields) mMatchedFields.textContent = evalData.matched_fields || 0;
    if (mModifiedFields) mModifiedFields.textContent = evalData.modified_fields || 0;

    // 渲染 AI vs 用户最终输入 逐项对照表
    if (mCompareBody) {
        const comparisons = evalData.field_comparisons || [];
        if (comparisons.length === 0) {
            // 无真实 AI 预填时后端不造对照数据，如实显示占位
            mCompareBody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#999;">无 AI 预填数据</td></tr>';
        } else {
            let compHtml = '';
            comparisons.forEach(c => {
                const badge = c.is_match
                    ? '<span class="badge" style="background:#d4edda; color:#155724; font-size:0.7rem;">[完全采纳]</span>'
                    : '<span class="badge" style="background:#fff3cd; color:#856404; font-size:0.7rem;">[人工纠偏]</span>';
                compHtml += `
                    <tr>
                        <td><strong>${escapeHtml(c.field)}</strong></td>
                        <td><code>${escapeHtml(c.ai_value)}</code></td>
                        <td><strong style="color:${c.is_match ? '#2f6b4f' : '#b8860b'}">${escapeHtml(c.user_value)}</strong></td>
                        <td class="col-center">${badge}</td>
                    </tr>
                `;
            });
            mCompareBody.innerHTML = compHtml;
        }
    }

    // 渲染明细行
    if (mItemsBody) {
        if (!s.masked_items || s.masked_items.length === 0) {
            mItemsBody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#999;">无明细行或未产生明细</td></tr>';
        } else {
            let itemHtml = '';
            s.masked_items.forEach(it => {
                itemHtml += `
                    <tr>
                        <td><strong>${escapeHtml(it.item_name || '-')}</strong></td>
                        <td class="col-right">${it.quantity || 1}</td>
                        <td>${escapeHtml(it.unit || '斤')}</td>
                        <td class="col-right"><code>${escapeHtml(it.unit_price || '-')}</code></td>
                        <td class="col-right"><code>${escapeHtml(it.amount || '-')}</code></td>
                    </tr>
                `;
            });
            mItemsBody.innerHTML = itemHtml;
        }
    }

    if (mJson) {
        mJson.textContent = JSON.stringify({
            "receipt_id": s.receipt_id,
            "masked_vendor": s.masked_vendor,
            "receipt_date": s.date,
            "doc_form": s.doc_form,
            "masked_total_amount": s.masked_total,
            "user_status_machine": s.user_status,
            "engine_routing": s.engine,
            "is_user_edited": s.is_user_edited,
            "is_approved_by_owner": s.is_approved,
            "math_gate_verified": s.math_gate_passed ?? null,
            "effect_evaluation": {
                "feedback_result": evalData.feedback_label,
                "is_exact_match": evalData.is_exact_match,
                "field_match_rate": (evalData.match_rate != null ? evalData.match_rate + "%" : null),
                "matched_fields_count": evalData.matched_fields,
                "user_modified_fields_count": evalData.modified_fields
            },
            "desensitized_items": s.masked_items
        }, null, 2);
    }

    if (modal) {
        modal.classList.remove('hide');
    }

    // 2. 同步填充底栏抽屉（备选展示）
    const card = document.getElementById('adminGreySampleDetailCard');
    const title = document.getElementById('adminGreySampleDetailTitle');
    const img = document.getElementById('adminGreyDetailImg');
    const jsonPre = document.getElementById('adminGreyDetailJson');

    if (title) title.textContent = `单据 #${s.receipt_id} 脱敏解析详情（${s.masked_vendor} · ${s.user_status}）`;
    if (img) {
        img.onerror = function() {
            this.onerror = null;
            this.src = DESENSITIZED_IMG_FALLBACK;
        };
        img.src = s.image_url || DESENSITIZED_IMG_FALLBACK;
    }
    if (jsonPre) {
        jsonPre.textContent = mJson ? mJson.textContent : '';
    }
    if (card) {
        card.classList.remove('hide');
    }
}

function openGreySampleModal(receiptId) {
    if (receiptId == null) return;
    const idx = _adminGreySamplesCache.findIndex(s => s && String(s.receipt_id) === String(receiptId));
    if (idx >= 0) {
        viewGreySampleDetail(idx);
    } else if (typeof receiptId === 'number' && _adminGreySamplesCache[receiptId]) {
        viewGreySampleDetail(receiptId);
    }
}

function closeGreySampleModal() {
    const modal = document.getElementById('adminGreySampleModal');
    if (modal) modal.classList.add('hide');
}
if (typeof window !== 'undefined') {
    window.loadAdminGreySamples = loadAdminGreySamples;
    window.viewGreySampleDetail = viewGreySampleDetail;
    window.openGreySampleModal = openGreySampleModal;
    window.closeGreySampleModal = closeGreySampleModal;
}

// 历史灰度发布与放量履历
function loadCanaryHistory() {
    const tbody = document.getElementById('adminCanaryHistoryBody');
    if (!tbody) return;
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#666; padding:16px;">加载历史灰度发布履历中...</td></tr>';
    apiFetch('/api/admin/canary/history')
        .then(r => r.json())
        .then(res => {
            if (!res || res.status !== 'success') {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#c00; padding:16px;">加载灰测履历失败</td></tr>';
                return;
            }
            const mTotal = document.getElementById('mCanaryHistoryTotal');
            const mPromote = document.getElementById('mCanaryHistoryPromote');
            const mRollback = document.getElementById('mCanaryHistoryRollback');
            const mReceipts = document.getElementById('mCanaryHistoryReceipts');
            if (mTotal) mTotal.textContent = res.total_records || 0;
            if (mPromote) mPromote.textContent = res.promote_count || 0;
            if (mRollback) mRollback.textContent = res.rollback_count || 0;
            if (mReceipts) mReceipts.textContent = res.total_grey_receipts || 0;

            const items = res.items || [];
            if (items.length === 0) {
                tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#999; padding:16px;">暂无历史灰测变更记录</td></tr>';
                return;
            }
            const _formatCanaryModelName = (val) => {
                if (!val) return '-';
                const s = String(val).trim();
                if (!s || s === '-') return '-';
                const replaceKind = (k) => {
                    const lower = k.trim().toLowerCase();
                    if (lower.includes('openai')) return 'deepseek/deepseek-v4.1-flash';
                    if (lower.includes('opencode') || lower.includes('codebuddy')) return 'Qwen/Qwen3-VL-32B-Instruct';
                    if (lower.includes('mock')) return 'mock-vlm-rec';
                    if (lower === 'qwen' || lower.includes('qwen')) return 'qwen3.5-omni-flash';
                    return k.trim();
                };
                if (s.includes('->')) {
                    const parts = s.split('->');
                    return replaceKind(parts[0]) + ' -> ' + replaceKind(parts[1]);
                }
                return replaceKind(s);
            };

            let html = '';
            items.forEach(it => {
                const badgeClass = it.badge_class || 'badge-secondary';
                html += '<tr>'
                    + '<td style="font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:0.75rem; color:var(--text-muted); white-space:nowrap;">' + w2Escape(_fmtDateTime(it.ts)) + '</td>'
                    + '<td><span class="badge ' + badgeClass + '" style="font-size:0.72rem; padding:2px 6px;">' + w2Escape(it.action_label || it.action) + '</span></td>'
                    + '<td><code style="font-size:0.75rem;">' + w2Escape(_formatCanaryModelName(it.models)) + '</code></td>'
                    + '<td style="font-size:0.78rem; color:var(--text-main);">' + w2Escape(it.description || '-') + '</td>'
                    + '<td style="font-size:0.78rem; color:var(--text-muted);">' + w2Escape(it.operator || 'admin') + '</td>'
                    + '<td style="text-align:right;"><span class="badge" style="background:#f1f5f9; color:#475569; font-size:0.72rem; padding:2px 6px;">' + w2Escape(it.status || '归档') + '</span></td>'
                    + '</tr>';
            });
            tbody.innerHTML = html;
        })
        .catch(err => {
            console.error('加载灰度历史失败', err);
            tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#c00; padding:16px;">加载历史履历异常</td></tr>';
        });
}
if (typeof window !== 'undefined') {
    window.loadCanaryHistory = loadCanaryHistory;
}


// =====================================================================
// E-P1-2 黄金样本 57 看板（Admin）
// =====================================================================
const GOLDEN_DOC_FORM_LABELS = {
    printed_delivery_note: '印刷送货单',
    ncr_handwritten: '手写街市单',
    thermal: '热敏机打',
    weigh_slip: '磅单',
    correction_note: '更正单',
    credit_note: 'Credit Note',
    monthly_statement: '月结单',
};

let _goldenBoardScope = 'all';

function loadGoldenBoard(scope) {
    const VALID_SCOPES = ['all', 'golden', 'gt_confirmed', 'gt_pending'];
    if (VALID_SCOPES.includes(scope)) _goldenBoardScope = scope;
    const isCurrent = _analyticsSeq('goldenBoard');
    const sel = document.getElementById('goldenScopeSelect');
    if (sel && VALID_SCOPES.includes(scope)) sel.value = scope;
    const body = document.getElementById('goldenBoardBody');
    if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:24px;">加载中...</td></tr>';
    apiFetch('/api/admin/golden-samples?scope=' + _goldenBoardScope)
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!isCurrent()) return;
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:24px;">加载失败：' + w2Escape((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)) + '</td></tr>';
                return;
            }
            if (!body) return;
            const items = ret.items || [];
            if (items.length === 0) {
                body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:24px;">暂无单据，可点击下方按钮导入黄金样本</td></tr>';
                return;
            }
            let html = '';
            items.forEach(it => {
                // 1. 业务状态本地化
                let statusBadge = '<span class="badge badge-secondary" style="font-size:0.72rem; padding:2px 6px;">已上传</span>';
                if (it.status === 'approved') {
                    statusBadge = '<span class="badge badge-success" style="font-size:0.72rem; padding:2px 6px;">已入库</span>';
                } else if (it.status === 'edited') {
                    statusBadge = '<span class="badge badge-warning" style="font-size:0.72rem; padding:2px 6px;">' + ((typeof _termStatus === 'function' && _termStatus('edited', 'label')) || '待老板审批') + '</span>';
                }

                // 2. GT 确权治理列（唯一入口，直达确权台，消除操作列重复按钮）
                let gtHtml = '<span style="color:var(--text-muted); font-size:0.75rem;">-</span>';
                if (it.gt_status === 'confirmed') {
                    gtHtml = '<span class="badge badge-success" style="font-size:0.72rem; padding:2px 6px;" title="' + w2Escape(it.sample_id || '') + '">已确权' + (it.sample_id ? ' · ' + w2Escape(it.sample_id) : '') + '</span>';
                } else if (it.gt_status === 'pending') {
                    gtHtml = '<a href="/evalset" target="_blank" class="gt-pill-link pending" title="待抽检确认（点击直接进入确权台）">待抽检 ↗</a>';
                } else if (it.gt_status === 'promoted') {
                    gtHtml = '<a href="/evalset" target="_blank" class="gt-pill-link promoted" title="已晋升（点击查看确权台详情）">已晋升 ↗</a>';
                } else if (it.gt_status === 'rejected') {
                    gtHtml = '<span class="badge badge-danger" style="font-size:0.72rem; padding:2px 6px;">已驳回</span>';
                }

                // 3. 金额格式化（0 元优雅淡化）
                const isZero = !it.total_amount || Number(it.total_amount) === 0;
                const amountHtml = isZero
                    ? '<span style="color:var(--text-muted); font-size:0.78rem;">HK$ 0.00</span>'
                    : '<span style="font-family:var(--font-mono); font-weight:600;">' + currencySymbol(it.currency) + ' ' + I18N.fmtMoney(it.total_amount) + '</span>';

                // 4. 基准集状态与操作融合成轻量级 Ghost 交互（彻底移除黑色实心大方块）
                let goldenActionHtml = '';
                if (it.is_golden_sample) {
                    goldenActionHtml = '<span class="golden-member-indicator" title="当前样本已纳入黄金基准集">[基准]</span>'
                        + '<button class="btn-golden-ghost btn-remove-golden" onclick="toggleGoldenSample(' + Number(it.id) + ', false)" title="从基准集中移出此样本">移出</button>';
                } else {
                    goldenActionHtml = '<button class="btn-golden-ghost btn-add-golden" onclick="toggleGoldenSample(' + Number(it.id) + ', true)" title="将此样本收录进黄金基准集">+ 纳入基准</button>';
                }
                const detailBtn = '<button class="btn-golden-ghost" onclick="loadReceiptDetail(' + Number(it.id) + ')" title="查看单据完整识别结果与图片切片">详情</button>';

                const actionHtml = '<div class="golden-actions-cell">'
                    + goldenActionHtml
                    + detailBtn
                    + '</div>';

                // 5. 8 列优雅工业级排版
                html += '<tr>'
                    + '<td style="font-family:var(--font-mono); font-size:0.78rem; color:var(--text-muted-2);">#' + Number(it.id) + '</td>'
                    + '<td class="cell-supplier" style="font-weight:500; color:var(--text-main);" title="' + w2Escape(it.supplier_name || '') + '">' + w2Escape(it.supplier_name || '-') + '</td>'
                    + '<td style="color:var(--text-muted-2); font-size:0.78rem; font-family:var(--font-mono); font-variant-numeric:tabular-nums;">' + w2Escape(_fmtDateOnly(it.receipt_date)) + '</td>'
                    + '<td><span style="font-size:0.75rem; color:var(--text-subtle);">' + w2Escape(GOLDEN_DOC_FORM_LABELS[it.doc_form] || it.doc_form || '-') + '</span></td>'
                    + '<td class="col-right">' + amountHtml + '</td>'
                    + '<td>' + statusBadge + '</td>'
                    + '<td>' + gtHtml + '</td>'
                    + '<td class="col-right">' + actionHtml + '</td>'
                    + '</tr>';
            });
            body.innerHTML = html;
        })
        .catch(err => {
            if (!isCurrent()) return;
            console.error('黄金样本看板加载失败', err);
            if (body) body.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:24px;">加载失败，请稍后重试</td></tr>';
        });
}
window.loadGoldenBoard = loadGoldenBoard;
window.toggleGoldenSample = toggleGoldenSample;

// 方案 A 重组：02 区推全深链——切到引擎配置 tab 并激活 02 子分页，展开灰测抽屉，聚焦推全/回滚操作
function gotoGreyPromote() {
    const engineBtn = document.getElementById('adminEngineBtn');
    if (engineBtn) engineBtn.click();
    setTimeout(() => {
        if (typeof switchEngineSubView === 'function') {
            switchEngineSubView('view02', { scroll: false, updateHash: true });
        }
        const drawer = document.getElementById('adminGreyDrawer');
        if (drawer && drawer.classList.contains('collapsed') && typeof toggleEngineDrawer === 'function') {
            toggleEngineDrawer('adminGreyDrawer');
        }
        if (drawer) {
            const promoteBtn = drawer.querySelector('#btnCanaryPromote100') || drawer.querySelector('.canary-promote-btn') || drawer;
            if (typeof promoteBtn.scrollIntoView === 'function') {
                promoteBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            if (promoteBtn !== drawer && typeof promoteBtn.focus === 'function') {
                promoteBtn.focus();
            }
        }
    }, 250);
}

// 方案 A 重组：03 区 GT 确权进度（各 split 的 draft/confirmed/missing 计数）
function loadGTEvalsetStats() {
    const el = document.getElementById('gtEvalsetStats');
    if (!el) return;
    apiFetch('/api/evalset/stats')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success' || !ret.data || !ret.data.splits) {
                el.textContent = '评测集尚未构建（manifest 缺失，运行 build_evalset.py 后显示）';
                return;
            }
            const parts = Object.keys(ret.data.splits).map(s => {
                const b = ret.data.splits[s] || {};
                return s + '：已确权 ' + (b.confirmed || 0) + ' / 草稿 ' + (b.draft || 0) + ' / 待补 ' + (b.missing || 0);
            });
            el.textContent = parts.length ? parts.join('；') : '评测集为空';
        })
        .catch(() => { el.textContent = '确权进度加载失败'; });
}

// =====================================================================
// P1-T5 人工闸入口发现性：待确认记忆队列的可达性
// why：连续 3 次纠偏蒸馏出的记忆先进 pending 队列、人工批准后才注入识别，
// 但主台此前只有一句 toast 文案，既无跳转也无任何带「记忆」语义的入口/角标
// -> 人工闸在主台上实际不可达（gap 分析 M11）。这里只补入口，不改评测台页面：
// 落点用评测台「待确认记忆」区块表格现有的 id（pendingMemoryBody）做锚点。
// =====================================================================

const PENDING_MEMORY_URL = '/evalset#pendingMemoryBody';

// 在主台新标签打开待确认记忆队列（与主台既有 /evalset 入口的 target="_blank" 同口径）。
// 由 toast 行动点触发 = 用户点击，不会被弹窗拦截器挡下。
function openPendingMemoryQueue() {
    window.open(PENDING_MEMORY_URL, '_blank');
}

/**
 * 刷新「待确认记忆」角标，条数取自只读接口 GET /api/memory/pending?status=pending。
 * why 静默：角标是补充入口而非核心数据——无 pending、无权限、网络失败一律隐藏角标，
 * 既不打断主台也不抛未捕获异常。
 * 非 admin 直接跳过请求：该接口 require_admin，评测中心对 owner/staff 一进去就是 403，
 * 与 applyRoleVisibility「避免进入即 403 弹窗」同口径，不给页面加无意义的权限提示。
 */
function loadPendingMemoryBadge() {
    const el = document.getElementById('pendingMemoryBadge');
    if (!el) return;
    const serverRole = AuthState.account && AuthState.account.role;
    const localRole = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    const role = serverRole || localRole;
    if (role && role !== 'admin') { el.classList.add('hide'); return; }
    apiFetch('/api/memory/pending?status=pending')
        .then(res => (res && res.ok) ? res.json().catch(() => null) : null)
        .then(ret => {
            const list = (ret && Array.isArray(ret.data)) ? ret.data : [];
            if (!list.length) { el.classList.add('hide'); return; }
            el.textContent = '待确认记忆 ' + list.length + ' 条';
            el.classList.remove('hide');
        })
        .catch(() => { el.classList.add('hide'); });
}

// 加入/移出黄金基准集（可逆策展标记；移出需二次确认）
async function toggleGoldenSample(receiptId, inSet) {
    if (!inSet) {
        const okGolden = await uiConfirm({
            title: '移出基准集',
            message: '确认把单据 #' + receiptId + ' 移出黄金基准集？\n单据本身和审计记录不受影响，以后可以再加回来。',
            confirmText: '移出',
        });
        if (!okGolden) return;
    }
    apiFetch('/api/admin/golden-samples/' + Number(receiptId) + '/membership', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ in_set: !!inSet })
    })
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                showToast((inSet ? '加入' : '移出') + '基准集失败：' + ((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)), 'error');
                return;
            }
            showToast(inSet ? '已加入黄金基准集' : '已移出黄金基准集', 'success');
            loadGoldenBoard();
        })
        .catch(err => {
            console.error('基准集成员操作失败', err);
            showToast('请求失败，请稍后重试', 'error');
        });
}

// =====================================================================
// E-P1-3 p-value 卡片（A/B 显著性检验）
// 阈值说明：双侧双比例 z 检验，alpha=0.05；p<0.05 显著；N<30 低置信度
// =====================================================================
var PVALUE_ALPHA = 0.05;
var PVALUE_MIN_SAMPLE = 30;

function loadPValueCards(targetExpId) {
    const container = document.getElementById('pvalueCardsContainer');
    const select = document.getElementById('pvalueExperimentSelect');
    if (!container) return;
    apiFetch('/api/admin/experiments')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">实验列表加载失败</div>';
                return;
            }
            const exps = ret.data || [];
            if (exps.length === 0) {
                if (select) select.innerHTML = '<option value="">暂无实验</option>';
                container.innerHTML = '<div style="color:var(--text-muted); font-size:0.82rem;">暂无 A/B 实验。可在灰测开启后创建实验，样本回流后此处展示显著性卡片。</div>';
                return;
            }
            const currentVal = targetExpId || (select ? select.value : null);
            if (select) {
                select.innerHTML = '';
                exps.forEach(e => {
                    const opt = document.createElement('option');
                    opt.value = e.id;
                    opt.textContent = '#' + e.id + ' ' + (e.name || '') + ' (' + (e.status || '') + ')';
                    select.appendChild(opt);
                });
            }
            let chosenId = null;
            if (currentVal && exps.some(e => String(e.id) === String(currentVal))) {
                chosenId = currentVal;
            } else {
                const running = exps.find(e => e.status === 'running') || exps[0];
                chosenId = running ? running.id : exps[0].id;
            }
            if (select) select.value = String(chosenId);
            loadPValueCardsFor(Number(chosenId));
        })
        .catch(err => {
            console.error('实验列表加载失败', err);
            if (container) container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">实验列表加载失败</div>';
        });
}

function onPValueExperimentChange(expId) {
    const select = document.getElementById('pvalueExperimentSelect');
    const targetId = Number(expId || (select ? select.value : null));
    if (!targetId) return;
    if (select && select.value !== String(targetId)) {
        select.value = String(targetId);
    }
    loadPValueCardsFor(targetId);
}
window.onPValueExperimentChange = onPValueExperimentChange;
window.loadPValueCards = loadPValueCards;

function loadPValueCardsFor(expId) {
    const container = document.getElementById('pvalueCardsContainer');
    if (!container || !expId) return;
    apiFetch('/api/admin/experiments/' + Number(expId) + '/pvalue')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!ret || ret.status !== 'success') {
                if (toastHttpError(httpStatus, ret)) return;
                container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">p-value 加载失败</div>';
                return;
            }
            renderPValueCards(ret.cards || [], !!ret.low_confidence);
        })
        .catch(err => {
            console.error('p-value 加载失败', err);
            if (container) container.innerHTML = '<div style="color:#c00; font-size:0.82rem;">p-value 加载失败</div>';
        });
}

function renderPValueCards(cards, lowConfidence) {
    const container = document.getElementById('pvalueCardsContainer');
    if (!container) return;
    const METRIC_LABELS = { accuracy: '准确率', hallucination_rate: '幻觉率', edit_rate: '人工修改率' };
    let html = '';
    cards.forEach(c => {
        const sig = c.significant;
        const sigBadge = (sig == null)
            ? '<span class="badge badge-secondary">无法判定</span>'
            : (sig
                ? '<span class="badge badge-success">显著 (p &lt; ' + PVALUE_ALPHA + ')</span>'
                : '<span class="badge badge-warning">不显著 (p >= ' + PVALUE_ALPHA + ')</span>');
        const pText = (c.p_value == null) ? '-' : Number(c.p_value).toFixed(4);
        const zText = (c.z == null) ? '-' : Number(c.z).toFixed(3);
        const effText = (c.effect_size_pp == null) ? '-' : ((c.effect_size_pp > 0 ? '+' : '') + Number(c.effect_size_pp).toFixed(2) + 'pp');
        html += '<div style="border:1px solid var(--border-color); border-radius:10px; padding:10px 12px; background:var(--bg-main);">'
            + '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">'
            + '<strong style="font-size:0.85rem;">' + w2Escape(METRIC_LABELS[c.metric] || c.metric) + '</strong>' + sigBadge
            + '</div>'
            + '<div style="font-size:0.78rem; color:var(--text-muted); line-height:1.7;">'
            + 'p-value：<strong style="color:var(--text-main);">' + pText + '</strong><br>'
            + 'z 分数：' + zText + '　效应量：' + effText + '<br>'
            + '阈值：alpha=' + PVALUE_ALPHA + '（双侧）' + (lowConfidence ? '｜<span style="color:#b8860b;">低置信度（N&lt;' + PVALUE_MIN_SAMPLE + '）</span>' : '')
            + '</div>'
            + '</div>';
    });
    container.innerHTML = html;
}

// =====================================================================
// 埋点观测台（11-组件Spec §7.1；owner/admin 可见）
// 四区块：全量埋点事件分布 / 挽回与点踩指标 / 灰测现状 / A/B 实验数据
// 复用现有端点：/api/analytics/recovery-summary（新增）、
// /api/admin/grey-test、/api/admin/experiments(+pvalue)，不重复造端点。
// 可视化：HTML 表格 + CSS 进度条（复用 batch-progress 样式），不引图表库。
// =====================================================================

function loadAnalyticsBoard(tenantId) {
    const sel = document.getElementById('analyticsTenantSelect');
    const selectedTenant = tenantId || (sel ? sel.value : 'all') || 'all';
    const activeBtn = document.querySelector('.analytics-segment-btn.active');
    const activeView = activeBtn ? activeBtn.getAttribute('data-view') : 'sec-telemetry';
    if (activeView === 'sec-telemetry' || activeView === 'sec-parsing-logs') {
        if (typeof syncTelemetryTenants === 'function') syncTelemetryTenants();
        loadRecoverySummaryBlocks(selectedTenant);
        if (typeof loadParsingLogs === 'function') loadParsingLogs(1);
    } else if (activeView === 'sec-canary') {
        loadAnalyticsGreyStatus();
        loadAdminGreySamples(selectedTenant, false);
    } else if (activeView === 'sec-experiment') {
        loadAnalyticsExperiments();
        loadPValueCards();
    } else if (activeView === 'sec-eval') {
        loadGoldenBoard();
        loadGTEvalsetStats();
        loadPendingMemoryBadge();
    }
}
window.loadAnalyticsBoard = loadAnalyticsBoard;

window.onAnalyticsTenantChange = function(tId) {
    if (typeof onUnifiedTenantChange === 'function') {
        onUnifiedTenantChange(tId);
    } else {
        loadAnalyticsBoard(tId);
    }
};

// AI 效果观测与评测中心：四大独立功能区分段切换器 (05 解析日志已并入 01 观测中枢)
function switchAnalyticsSubView(targetViewId) {
    // 兼容历史调用：当入参为 sec-parsing-logs 时自动重定向至 sec-telemetry 并切换至链路审计子选项卡
    let shouldSwitchToAuditSubTab = false;
    if (targetViewId === 'sec-parsing-logs') {
        targetViewId = 'sec-telemetry';
        shouldSwitchToAuditSubTab = true;
    }

    const validViews = ['sec-telemetry', 'sec-canary', 'sec-experiment', 'sec-eval'];
    if (!validViews.includes(targetViewId)) {
        targetViewId = 'sec-telemetry';
    }

    // 更新分段按钮状态与无障碍属性
    const buttons = document.querySelectorAll('.analytics-segment-btn');
    buttons.forEach(btn => {
        const isMatch = btn.getAttribute('data-view') === targetViewId;
        btn.classList.toggle('active', isMatch);
        btn.setAttribute('aria-selected', isMatch ? 'true' : 'false');
    });

    // 切换独立功能区子面板显示
    validViews.forEach(viewId => {
        const panel = document.getElementById(viewId);
        if (panel) {
            if (viewId === targetViewId) {
                panel.style.display = '';
                panel.classList.add('active');
            } else {
                panel.style.display = 'none';
                panel.classList.remove('active');
            }
        }
    });

    // 隐藏可能残留的历史 sec-parsing-logs 面板
    const legacyPanel = document.getElementById('sec-parsing-logs');
    if (legacyPanel) legacyPanel.style.display = 'none';

    // 针对 4 大激活功能区触发专属刷新
    if (targetViewId === 'sec-telemetry') {
        if (typeof syncTelemetryTenants === 'function') syncTelemetryTenants();
        const sel = document.getElementById('analyticsTenantSelect') || document.getElementById('parsingLogTenantSelect');
        const tId = sel ? sel.value : 'all';
        if (typeof loadRecoverySummaryBlocks === 'function') loadRecoverySummaryBlocks(tId);
        if (typeof loadParsingLogs === 'function') loadParsingLogs(1);
        if (shouldSwitchToAuditSubTab && typeof switchTelemetrySubTab === 'function') {
            switchTelemetrySubTab('audit');
        }
    } else if (targetViewId === 'sec-canary') {
        if (typeof loadAnalyticsGreyStatus === 'function') loadAnalyticsGreyStatus();
        if (typeof loadAdminGreySamples === 'function') loadAdminGreySamples(null, false);
        if (typeof loadCanaryHistory === 'function') loadCanaryHistory();
    } else if (targetViewId === 'sec-experiment') {
        if (typeof loadAnalyticsExperiments === 'function') loadAnalyticsExperiments();
        if (typeof loadPValueCards === 'function') loadPValueCards();
        if (typeof refreshCurrentExpDetail === 'function') refreshCurrentExpDetail();
    } else if (targetViewId === 'sec-eval') {
        if (typeof loadGoldenBoard === 'function') loadGoldenBoard();
        if (typeof loadGTEvalsetStats === 'function') loadGTEvalsetStats();
        if (typeof loadPendingMemoryBadge === 'function') loadPendingMemoryBadge();
    }
}
window.switchAnalyticsSubView = switchAnalyticsSubView;

// 小工具：0~1 比率 → 百分文本（空值返回 '-'）
function _analyticsPct(v) {
    return (v == null) ? '-' : (Number(v) * 100).toFixed(1) + '%';
}

// CSS 进度条（复用批量聚合进度条样式）
function _analyticsBarHtml(share) {
    const w = Math.max(0, Math.min(100, Math.round((share || 0) * 100)));
    return '<div class="batch-progress-track" style="height:6px; min-width:80px;">'
        + '<div class="batch-progress-bar" style="width:' + w + '%"></div></div>';
}

// 指标小卡片
function _analyticsStatHtml(label, value, sub, id, color, isRawSub, metricKey) {
    const valColor = color ? color : 'var(--text-main)';
    const idAttr = id ? (' id="' + w2Escape(id) + '"') : '';
    const subHtml = isRawSub ? (sub || '') : (sub ? w2Escape(sub) : '');
    const clickAttr = metricKey ? (' onclick="openKpiTrendModal(\'' + w2Escape(metricKey) + '\')" onkeydown="if(event.key===\'Enter\'||event.key===\' \'){event.preventDefault();openKpiTrendModal(\'' + w2Escape(metricKey) + '\');}" role="button" tabindex="0" aria-haspopup="dialog" title="点击查看时序趋势与多维下钻"') : '';
    const cardClass = metricKey ? 'card kpi-trend-card' : 'card';
    return '<div class="' + cardClass + '"' + clickAttr + ' style="flex:1 1 180px; padding:12px; margin-bottom:0; background:var(--bg-card); border:1px solid var(--border-color); border-radius:8px; position:relative;">'
        + '<div style="font-size:0.75rem; color:var(--text-muted); margin-bottom:4px; font-weight:500;">' + w2Escape(label) + '</div>'
        + '<div' + idAttr + ' style="font-size:1.35rem; font-weight:700; color:' + valColor + '; font-variant-numeric:tabular-nums;">' + w2Escape(String(value)) + '</div>'
        + (subHtml ? '<div style="font-size:0.72rem; color:var(--text-muted); margin-top:4px; line-height:1.3;">' + subHtml + '</div>' : '')
        + '</div>';
}

// Tab B 全量埋点事件分布专属筛选状态与时间跨度控制
window._eventsPeriod = { mode: 'all', startDate: null, endDate: null };
window._lastRecoverySummaryData = null;

const _EVENTS_PERIOD_BTN_IDS = {
    'all': 'btnEventsRangeAll',
    '24h': 'btnEventsRange24h',
    '7d': 'btnEventsRange7d',
    '14d': 'btnEventsRange14d',
    '30d': 'btnEventsRange30d',
};

function _updateEventsPeriodButtons() {
    const mode = window._eventsPeriod.mode;
    Object.keys(_EVENTS_PERIOD_BTN_IDS).forEach(m => {
        const btn = document.getElementById(_EVENTS_PERIOD_BTN_IDS[m]);
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

function setEventsPeriod(mode) {
    const p = window._eventsPeriod;
    p.mode = mode;
    p.startDate = null;
    p.endDate = null;
    const sEl = document.getElementById('eventsStartDate');
    const eEl = document.getElementById('eventsEndDate');
    if (sEl) sEl.value = '';
    if (eEl) eEl.value = '';
    _updateEventsPeriodButtons();
    loadRecoverySummaryBlocks();
}
window.setEventsPeriod = setEventsPeriod;

function applyEventsCustomPeriod(silent) {
    const sEl = document.getElementById('eventsStartDate');
    const eEl = document.getElementById('eventsEndDate');
    const sVal = sEl && sEl.value ? sEl.value.trim() : '';
    const eVal = eEl && eEl.value ? eEl.value.trim() : '';
    if (!eVal) {
        if (!silent) showToast('请选择结束日期（开始日期留空默认从最早一条记录开始）', 'warning');
        return;
    }
    if (sVal && sVal > eVal) {
        if (!silent) showToast('开始日期不能晚于结束日期', 'warning');
        return;
    }
    const p = window._eventsPeriod;
    p.mode = 'custom';
    p.startDate = sVal || null;
    p.endDate = eVal;
    _updateEventsPeriodButtons();
    loadRecoverySummaryBlocks();
}
window.applyEventsCustomPeriod = applyEventsCustomPeriod;

function onEventsTenantChange(tenantId) {
    const mainSel = document.getElementById('analyticsTenantSelect');
    const plSel = document.getElementById('parsingLogTenantSelect');
    const evSel = document.getElementById('eventsFilterTenant');
    if (mainSel && mainSel.value !== tenantId) mainSel.value = tenantId;
    if (plSel && plSel.value !== tenantId) plSel.value = tenantId;
    if (evSel && evSel.value !== tenantId) evSel.value = tenantId;
    loadRecoverySummaryBlocks(tenantId || 'all');
}
window.onEventsTenantChange = onEventsTenantChange;

function resetEventsFilters() {
    const tSel = document.getElementById('eventsFilterTenant');
    if (tSel) tSel.value = '';
    const mainSel = document.getElementById('analyticsTenantSelect');
    if (mainSel) mainSel.value = '';
    const catSel = document.getElementById('eventsCategorySelect');
    if (catSel) catSel.value = 'all';
    const sInput = document.getElementById('eventsSearchInput');
    if (sInput) sInput.value = '';

    const p = window._eventsPeriod;
    p.mode = 'all';
    p.startDate = null;
    p.endDate = null;
    const sEl = document.getElementById('eventsStartDate');
    const eEl = document.getElementById('eventsEndDate');
    if (sEl) sEl.value = '';
    if (eEl) eEl.value = '';
    _updateEventsPeriodButtons();

    loadRecoverySummaryBlocks();
}
window.resetEventsFilters = resetEventsFilters;

// 区块 1 + 2：全量事件分布 / 挽回与点踩指标（同一端点）
function loadRecoverySummaryBlocks(tenantId) {
    const distEl = document.getElementById('analyticsEventDistBody');
    const recEl = document.getElementById('analyticsRecoveryBody');
    if (distEl) distEl.innerHTML = '<div style="padding:24px; text-align:center; color:var(--text-muted); font-size:0.85rem;">加载中...</div>';
    if (recEl) recEl.innerHTML = '加载中';

    const tId = tenantId || (document.getElementById('eventsFilterTenant')?.value) || (document.getElementById('analyticsTenantSelect')?.value) || 'all';

    // 双向同步租户输入框
    const evTenant = document.getElementById('eventsFilterTenant');
    const mainSel = document.getElementById('analyticsTenantSelect');
    if (evTenant && tId !== 'all' && evTenant.value !== tId) evTenant.value = tId;
    if (mainSel && tId !== 'all' && mainSel.value !== tId) mainSel.value = tId;

    const period = window._eventsPeriod || { mode: 'all', startDate: null, endDate: null };
    const params = new URLSearchParams({
        tenant_id: tId,
        date_range: 'all',
    });
    if (period.mode === 'custom') {
        if (period.startDate) params.set('start_date', period.startDate + 'T00:00:00');
        if (period.endDate) params.set('end_date', period.endDate + 'T23:59:59.999999');
    } else if (period.mode === '7d' || period.mode === '14d' || period.mode === '30d') {
        const n = parseInt(period.mode, 10);
        params.set('start_date', _parsingLogDaysAgoStr(n - 1) + 'T00:00:00');
        params.set('end_date', _parsingLogTodayStr() + 'T23:59:59.999999');
    } else {
        params.set('date_range', period.mode);
    }

    const url = '/api/analytics/recovery-summary?' + params.toString();
    // F3：stale-response 守卫——快速切换租户时，旧慢响应不得覆写新区块/租户选择器
    const isCurrent = _analyticsSeq('recovery');
    apiFetch(url)
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!isCurrent()) return;
            if (!ret || ret.status !== 'success') {
                const msg = '加载失败：' + ((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus))
                    + (httpStatus === 403 ? '（本区块仅限 admin 权限访问）' : '');
                if (distEl) distEl.innerHTML = '<div style="padding:20px; text-align:center; color:#c00; font-size:0.85rem;">' + w2Escape(msg) + '</div>';
                if (recEl) recEl.innerHTML = '<span style="color:#c00;">' + w2Escape(msg) + '</span>';
                return;
            }
            const data = ret.data || {};
            window._lastRecoverySummaryData = data;

            // 租户筛选为文本输入：已知租户 ID 仅供 datalist 提示，不覆盖用户输入
            const sel = document.getElementById('analyticsTenantSelect');
            const plSel = document.getElementById('parsingLogTenantSelect');
            const datalist = document.getElementById('tenantIdSuggestions');
            if (datalist && Array.isArray(data.available_tenants)) {
                let optHtml = '';
                data.available_tenants.forEach(t => {
                    optHtml += '<option value="' + w2Escape(t) + '"></option>';
                });
                datalist.innerHTML = optHtml;
            }
            if (sel && plSel && plSel.value !== sel.value) {
                plSel.value = sel.value;
            }
            if (sel && evTenant && evTenant.value !== sel.value) {
                evTenant.value = sel.value;
            }
            renderEventDistribution(distEl, data);
            renderRecoveryMetrics(recEl, data);
        })
        .catch(err => {
            if (!isCurrent()) return;
            console.error('埋点观测台加载失败', err);
            if (distEl) distEl.innerHTML = '<div style="padding:20px; text-align:center; color:#c00; font-size:0.85rem;">加载失败，请稍后重试</div>';
            if (recEl) recEl.innerHTML = '<span style="color:#c00;">加载失败，请稍后重试</span>';
        });
}

// 埋点事件码 -> 业务规范说明（对齐产品指标与 AI 治理工程标准定义）
const TELEMETRY_EVENT_ALIASES = {
    // 核心生命周期流（上传 → 解析 → 复核 → 审核 → 入库）
    upload: '收据凭证上传与任务创建',
    receipt_uploaded: '收据凭证上传与任务创建',
    ocr_parse_started: 'AI 结构化解析流水线启动',
    ocr_parsed: 'AI 结构化解析完成并产出字段',
    parse_done: 'AI 结构化解析完成',
    ocr_error: 'AI 解析执行异常或超时',
    receipt_review_submitted: '人工复核确认并保存提交',
    save_edited: '人工复核修改保存',
    edit: '人工复核编辑保存',
    field_edited: '明细行离散编辑（增删行、改食材对应）',
    receipt_approved: '收据审核通过并确认入库',
    approve: '收据审核通过并确认入库',
    receipt_flagged: '标记单据异常并转人工处理',
    inventory_in: '库存流水入库并更新现存量',

    // 异常挽回与体验交互
    image_replaced: '收据原图替换并触发重验',
    retake_clicked: '触发单据重拍（图像质量归因）',
    reparse_clicked: '触发重新解析（模型推理归因）',
    reupload_after_fail: '异常单据重试上传',
    feedback_received: '用户满意度体验反馈（赞/踩）',
    parse_abandoned_for_manual: '解析等待中断并转手工录入',
    manual_entry_start: '新建手工录入凭证',
    manual_entry_started: '新建手工录入凭证',

    // AI 治理护栏与知识增强
    rag_hit: '供应商记忆库命中与上下文注入（RAG）',
    math_guard_checked: '金额与数量计算一致性校验（数学护栏）',
    contract_guard_checked: '字段完整性与 Schema 契约校验（契约护栏）',
    price_anomaly_flagged: '采购单价波动超阈值告警',
    reconciliation_completed: '供应商账期对账完成',
};

// 过滤非业务测试/探测事件（如 qa_*, *probe*, test_* 等），保持大盘纯净业务视角
function _isExcludedTelemetryEvent(type) {
    if (!type) return true;
    const t = String(type).toLowerCase();
    return t.startsWith('qa_') || t.includes('probe') || t.startsWith('test_') || t === 'totally_unknown_evt_zzz';
}

function _eventTypeAlias(code) {
    if (TELEMETRY_EVENT_ALIASES[code]) return TELEMETRY_EVENT_ALIASES[code];
    return '自定义业务事件';
}

// 最近触发时间格式化：全中心对齐 _fmtDateTime 标准 (YYYY-MM-DD HH:mm:ss)
function _fmtLastTs(ts) {
    return _fmtDateTime(ts);
}

// 事件类别分组定义（与 TELEMETRY_EVENT_ALIASES 注释块对齐）
const TELEMETRY_EVENT_CATEGORIES = [
    {
        key: 'lifecycle',
        label: '核心生命周期',
        desc: '上传 → 解析 → 复核 → 审核 → 入库',
        color: '#1e4ed8',
        bg: 'rgba(219,234,254,0.55)',
        events: new Set([
            'upload','receipt_uploaded','ocr_parse_started','ocr_parsed','parse_done',
            'ocr_error','receipt_review_submitted','save_edited','edit','field_edited',
            'receipt_approved','approve','receipt_flagged','inventory_in'
        ])
    },
    {
        key: 'recovery',
        label: '异常挽回与体验交互',
        desc: '重拍 / 重解析 / 手工录入 / 反馈',
        color: '#b45309',
        bg: 'rgba(254,243,199,0.6)',
        events: new Set([
            'image_replaced','retake_clicked','reparse_clicked','reupload_after_fail',
            'feedback_received','parse_abandoned_for_manual','manual_entry_start','manual_entry_started'
        ])
    },
    {
        key: 'governance',
        label: 'AI 治理护栏与知识增强',
        desc: 'RAG 命中 / 契约护栏 / 数学护栏 / 价格告警',
        color: '#065f46',
        bg: 'rgba(209,250,229,0.55)',
        events: new Set([
            'rag_hit','math_guard_checked','contract_guard_checked',
            'price_anomaly_flagged','reconciliation_completed'
        ])
    }
];

function _resolveEventCategory(eventType) {
    for (const cat of TELEMETRY_EVENT_CATEGORIES) {
        if (cat.events.has(eventType)) return cat.key;
    }
    return '__other__';
}

function renderEventDistribution(el, data) {
    if (!el) el = document.getElementById('analyticsEventDistBody');
    if (!el) return;
    if (!data) data = window._lastRecoverySummaryData;
    if (!data) return;

    let dist = (data.event_distribution || []).filter(d => !_isExcludedTelemetryEvent(d.event_type));

    // 客户端类别与搜索过滤
    const catSelect = document.getElementById('eventsCategorySelect');
    const selectedCat = catSelect ? catSelect.value : 'all';
    const searchInput = document.getElementById('eventsSearchInput');
    const searchVal = searchInput ? searchInput.value.trim().toLowerCase() : '';

    if (selectedCat && selectedCat !== 'all') {
        dist = dist.filter(d => _resolveEventCategory(d.event_type) === selectedCat);
    }
    if (searchVal) {
        dist = dist.filter(d => {
            const alias = _eventTypeAlias(d.event_type).toLowerCase();
            return d.event_type.toLowerCase().includes(searchVal) || alias.includes(searchVal);
        });
    }

    const countEl = document.getElementById('analyticsEventDistCount');
    const filteredTotalEvents = dist.reduce((s, d) => s + Number(d.count || 0), 0);

    if (dist.length === 0) {
        if (countEl) countEl.textContent = '共 0 条事件、0 类';
        el.innerHTML = '<div style="padding:32px; text-align:center; color:var(--text-muted); font-size:0.85rem;">暂无符合当前筛选条件的埋点事件记录。</div>';
        return;
    }

    // 按类别分桶
    const buckets = {};
    TELEMETRY_EVENT_CATEGORIES.forEach(c => { buckets[c.key] = []; });
    buckets['__other__'] = [];
    dist.forEach(d => {
        const catKey = _resolveEventCategory(d.event_type);
        if (buckets[catKey]) {
            buckets[catKey].push(d);
        } else {
            buckets['__other__'].push(d);
        }
    });

    // 组装分组顺序：已知三类 + 兜底「其他」
    const orderedCats = [];
    TELEMETRY_EVENT_CATEGORIES.forEach(c => {
        if (buckets[c.key] && buckets[c.key].length > 0) orderedCats.push(c);
    });
    if (buckets['__other__'].length > 0) {
        orderedCats.push({ key: '__other__', label: '其他自定义事件', desc: '', color: '#475569', bg: 'rgba(241,245,249,0.8)' });
    }

    if (countEl) {
        let countText = '共 ' + filteredTotalEvents + ' 条事件、' + dist.length + ' 类';
        if (data.low_confidence) {
            countText += '（样本 < 30，低置信度）';
        }
        countEl.textContent = countText;
    }

    let html = '<div class="table-container" style="overflow-x:auto; margin-bottom:0; border:none;">'
        + '<table class="data-table" id="analyticsEventDistTable" style="width:100%; min-width:980px; table-layout:auto;">'
        + '<thead><tr>'
        + '<th style="white-space:nowrap; width:240px; min-width:220px; text-align:left;">事件类型</th>'
        + '<th style="min-width:240px; text-align:left;">说明</th>'
        + '<th class="col-right" style="white-space:nowrap; width:90px; text-align:right;">计数</th>'
        + '<th style="width:180px; min-width:160px; white-space:nowrap; text-align:left;">占比</th>'
        + '<th style="white-space:nowrap; width:180px; text-align:left;">最近触发时间 (最新在前)</th>'
        + '<th class="col-center" style="white-space:nowrap; width:100px; text-align:center;">操作</th>'
        + '</tr></thead><tbody>';

    orderedCats.forEach(cat => {
        const rows = buckets[cat.key];
        if (!rows || rows.length === 0) return;

        // 统一组内排序：计数倒序，计数相同时按最后触发时间倒序
        rows.sort((a, b) => {
            const cntDiff = Number(b.count || 0) - Number(a.count || 0);
            if (cntDiff !== 0) return cntDiff;
            return String(b.last_ts || '').localeCompare(String(a.last_ts || ''));
        });

        // 分组 header 行（可点击折叠）
        const catCount = rows.reduce((s, d) => s + Number(d.count || 0), 0);
        const safeKey = cat.key.replace(/[^a-z0-9_]/g, '_');
        html += '<tr style="cursor:pointer; user-select:none;" onclick="toggleEventCatGroup(\'' + safeKey + '\', this)">'
            + '<td colspan="6" style="'
            +   'background:' + cat.bg + ';'
            +   'padding:6px 10px;'
            +   'font-size:0.78rem;'
            +   'font-weight:700;'
            +   'color:' + cat.color + ';'
            +   'letter-spacing:0.02em;'
            +   'border-bottom:1px solid rgba(0,0,0,0.06);'
            + '">'
            + '<span class="evt-cat-arrow" style="display:inline-block; margin-right:6px; font-size:0.7rem; transition:transform 0.18s; transform:rotate(0deg);">&#9660;</span>'
            + cat.label
            + (cat.desc ? '<span style="font-weight:400; margin-left:8px; color:' + cat.color + '; opacity:0.75;">' + w2Escape(cat.desc) + '</span>' : '')
            + '<span style="float:right; font-weight:400; font-variant-numeric:tabular-nums; opacity:0.7;">' + catCount + ' 次</span>'
            + '</td>'
            + '</tr>';

        // 事件行（data-cat 供折叠定位，支持点击代码或操作列查看明细）
        rows.forEach(d => {
            const alias = _eventTypeAlias(d.event_type);
            const safeEvt = w2Escape(d.event_type);
            html += '<tr data-cat="' + safeKey + '">'
                + '<td style="white-space:nowrap; text-align:left; padding-left:24px;" title="点击下钻查看该事件类型全量明细日志">'
                + '<a href="javascript:void(0)" onclick="openEventDetailModal(\'' + safeEvt + '\')" style="text-decoration:none; color:var(--primary-color, #2563eb); font-weight:600; cursor:pointer;" title="点击下钻查看该事件类型全量明细日志">'
                + '<code style="white-space:nowrap; font-size:0.8rem; display:inline-block; font-family:monospace; background:rgba(37,99,235,0.06); padding:2px 6px; border-radius:4px; border:1px solid rgba(37,99,235,0.15);">' + safeEvt + '</code>'
                + '</a></td>'
                + '<td style="font-size:0.82rem; color:var(--text-main); text-align:left; line-height:1.4;" title="' + w2Escape(alias) + '">' + w2Escape(alias) + '</td>'
                + '<td class="col-right" style="white-space:nowrap; text-align:right; font-weight:600;">' + Number(d.count) + '</td>'
                + '<td><div style="display:flex; align-items:center; gap:8px;">'
                + _analyticsBarHtml(d.share)
                + '<span style="font-size:0.75rem; color:var(--text-muted); font-variant-numeric:tabular-nums;">' + _analyticsPct(d.share) + '</span>'
                + '</div></td>'
                + '<td style="font-size:0.78rem; font-family:var(--font-mono); font-variant-numeric:tabular-nums; color:var(--text-muted); white-space:nowrap; text-align:left;">' + w2Escape(_fmtDateTime(d.last_ts)) + '</td>'
                + '<td class="col-center" style="white-space:nowrap; text-align:center;">'
                + '<button class="btn btn-secondary btn-sm" onclick="openEventDetailModal(\'' + safeEvt + '\')" style="padding:2px 8px; font-size:0.75rem; cursor:pointer;" title="点击下钻查看该事件类型全量明细日志">[查看明细]</button>'
                + '</td>'
                + '</tr>';
        });
    });

    html += '</tbody></table></div>';
    el.innerHTML = html;
}
window.renderEventDistribution = renderEventDistribution;

function onEventsCategoryChange() {
    renderEventDistribution();
}
window.onEventsCategoryChange = onEventsCategoryChange;

function onEventsSearchInput() {
    renderEventDistribution();
}
window.onEventsSearchInput = onEventsSearchInput;

function toggleEventCatGroup(catKey, headerTr) {
    const table = headerTr.closest('table');
    if (!table) return;
    const rows = table.querySelectorAll('tr[data-cat="' + catKey + '"]');
    const arrow = headerTr.querySelector('.evt-cat-arrow');
    const isCollapsed = rows.length > 0 && rows[0].style.display === 'none';
    rows.forEach(r => { r.style.display = isCollapsed ? '' : 'none'; });
    if (arrow) arrow.style.transform = isCollapsed ? 'rotate(0deg)' : 'rotate(-90deg)';
}

function renderRecoveryMetrics(el, data) {
    if (!el) return;
    const rec = data.recovery || {};
    const fb = data.feedback || {};
    const rs = data.recovery_success || {};
    const lowNote = data.low_confidence ? '（低置信度：全量事件 < 30，仅作参考）' : '';

    // 综合好评率与人工修正率 (SSOT 与 parsing_logs 对齐)
    let posRateVal = fb.positive_rate != null ? _analyticsPct(fb.positive_rate) : '-';
    let editRateVal = fb.edit_rate != null ? _analyticsPct(fb.edit_rate) : '-';
    if (posRateVal === '-' && window.currentParsingLogsSummary && window.currentParsingLogsSummary.positive_rate != null) {
        posRateVal = _analyticsPct(window.currentParsingLogsSummary.positive_rate);
    }
    if (editRateVal === '-' && window.currentParsingLogsSummary && window.currentParsingLogsSummary.edit_rate != null) {
        editRateVal = _analyticsPct(window.currentParsingLogsSummary.edit_rate);
    }

    // 副标签：点踩率与评价明细
    let posSub = '';
    if (fb.positive_rate != null) {
        posSub = '好评 ' + (fb.liked_receipts_count || 0) + ' / 评价 ' + (fb.feedbacked_receipts || 0) + ' · ';
    }
    posSub += '点踩率 <span id="telemetryKpiDislikeRate">' + _analyticsPct(fb.down_rate) + '</span>';

    const editSub = '人工改动单据占比';

    // 挽回归因拆解：重拍（输入质量）与重新解析（模型能力）
    const retakeTxt = (rec.retake_clicked != null ? rec.retake_clicked : '-') + (rec.input_attribution_share != null ? (' (' + _analyticsPct(rec.input_attribution_share) + ')') : '');
    const reparseTxt = (rec.reparse_clicked != null ? rec.reparse_clicked : '-') + (rec.model_attribution_share != null ? (' (' + _analyticsPct(rec.model_attribution_share) + ')') : '');
    const entrySub = (rec.entry_rate_desc ? (w2Escape(rec.entry_rate_desc) + '<br>') : '')
        + '重拍 <span id="telemetryKpiRetakeAttribution">' + w2Escape(retakeTxt) + '</span> ｜ 重解析 <span id="telemetryKpiReparseAttribution">' + w2Escape(reparseTxt) + '</span>';

    const successSub = '成功 ' + (rs.success || 0) + ' / 尝试 ' + (rs.total || 0) + ' (闭环入库转化)';

    // 统一区块 B：精简聚合为 4 大核心 KPI 卡片，与区块 A 保持 1:1 对称信息架构
    let html = '<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap:12px;">'
        + _analyticsStatHtml('综合好评率', posRateVal, posSub, 'parsingKpiPositiveRate', '#0284c7', true, 'positive_rate')
        + _analyticsStatHtml('人工修正率', editRateVal, editSub, 'parsingKpiEditRate', '#b8860b', true, 'edit_rate')
        + _analyticsStatHtml('异常挽回触发率', _analyticsPct(rec.entry_rate), entrySub, 'telemetryKpiRecoveryEntry', '#b45309', true, 'recovery_entry_rate')
        + _analyticsStatHtml('挽回成功率', _analyticsPct(rs.rate), successSub, 'telemetryKpiRecoverySuccess', '#16a34a', true, 'recovery_success_rate')
        + '</div>';

    if (lowNote) {
        html += '<div style="font-size:0.75rem; color:#b8860b; margin-top:8px;">' + w2Escape(lowNote) + '</div>';
    }

    el.innerHTML = html;
}

// -------------------------------------------------------------
// 埋点事件明细审计模态框控制器 (Drill-down Event Logs Controller)
// -------------------------------------------------------------
window.currentEventModalState = {
    eventType: '',
    tenantId: 'all',
    page: 1,
    pageSize: 20,
    search: '',
    totalPages: 1,
    totalItems: 0,
};

function openEventDetailModal(eventType) {
    if (!eventType) return;
    const modal = document.getElementById('analyticsEventModal');
    if (!modal) return;

    const tenantSelect = document.getElementById('analyticsTenantSelect');
    const tenantId = tenantSelect ? tenantSelect.value : 'all';

    currentEventModalState.eventType = eventType;
    currentEventModalState.tenantId = tenantId;
    currentEventModalState.page = 1;
    currentEventModalState.search = '';

    const titleEl = document.getElementById('analyticsEventModalTitle');
    const aliasEl = document.getElementById('analyticsEventModalAliasBadge');
    const codeEl = document.getElementById('analyticsEventModalTypeCode');
    const tenantEl = document.getElementById('analyticsEventModalTenant');
    const searchInput = document.getElementById('analyticsEventSearchInput');

    const alias = _eventTypeAlias(eventType);
    if (titleEl) titleEl.textContent = '事件审计日志: ' + eventType;
    if (aliasEl) aliasEl.textContent = alias;
    if (codeEl) codeEl.textContent = eventType;
    if (tenantEl) tenantEl.textContent = tenantId || 'all';
    if (searchInput) searchInput.value = '';

    modal.classList.remove('hide');
    modal.style.display = 'flex';

    loadAnalyticsEventLogs(1);
}
window.openEventDetailModal = openEventDetailModal;
const openEventLogsModal = openEventDetailModal;
window.openEventLogsModal = openEventDetailModal;

function closeAnalyticsEventModal() {
    const modal = document.getElementById('analyticsEventModal');
    if (modal) {
        modal.classList.add('hide');
        modal.style.display = 'none';
    }
}
window.closeAnalyticsEventModal = closeAnalyticsEventModal;

function loadAnalyticsEventLogs(page) {
    const container = document.getElementById('analyticsEventTableContainer');
    if (container) {
        container.innerHTML = '<div style="text-align:center; padding:30px; color:var(--text-muted);">正在加载事件明细...</div>';
    }

    const state = currentEventModalState;
    state.page = page || 1;

    const params = new URLSearchParams();
    params.set('event_type', state.eventType);
    params.set('tenant_id', state.tenantId || 'all');
    params.set('page', String(state.page));
    params.set('page_size', String(state.pageSize));
    if (state.search) {
        params.set('search', state.search.trim());
    }

    apiFetch('/api/analytics/events/logs?' + params.toString())
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (httpStatus !== 200 || !ret || ret.status !== 'success') {
                const msg = (ret && (ret.detail || ret.msg)) || ('HTTP ' + httpStatus);
                if (container) {
                    container.innerHTML = '<div style="padding:20px; color:#c00; text-align:center;">加载失败: ' + w2Escape(msg) + '</div>';
                }
                return;
            }
            const data = ret.data || {};
            const pagination = data.pagination || {};
            const items = data.items || [];

            state.totalItems = pagination.total_items || 0;
            state.totalPages = pagination.total_pages || 1;

            const totalCountEl = document.getElementById('analyticsEventModalTotalCount');
            if (totalCountEl) totalCountEl.textContent = String(state.totalItems);

            const pageInfo = document.getElementById('analyticsEventPageInfo');
            if (pageInfo) {
                pageInfo.textContent = '第 ' + state.page + ' / ' + state.totalPages + ' 页 (共 ' + state.totalItems + ' 条记录)';
            }
            const curSpan = document.getElementById('analyticsEventCurrentPageSpan');
            if (curSpan) curSpan.textContent = String(state.page);

            const prevBtn = document.getElementById('btnAnalyticsEventPrev');
            const nextBtn = document.getElementById('btnAnalyticsEventNext');
            if (prevBtn) prevBtn.disabled = state.page <= 1;
            if (nextBtn) nextBtn.disabled = state.page >= state.totalPages;

            renderAnalyticsEventTable(items, state.eventType);
        })
        .catch(err => {
            if (container) {
                container.innerHTML = '<div style="padding:20px; color:#c00; text-align:center;">网络异常: ' + w2Escape(err.message || '未知错误') + '</div>';
            }
        });
}
window.loadAnalyticsEventLogs = loadAnalyticsEventLogs;

function searchAnalyticsEventLogs() {
    const input = document.getElementById('analyticsEventSearchInput');
    currentEventModalState.search = input ? input.value.trim() : '';
    loadAnalyticsEventLogs(1);
}
window.searchAnalyticsEventLogs = searchAnalyticsEventLogs;

function resetAnalyticsEventSearch() {
    const input = document.getElementById('analyticsEventSearchInput');
    if (input) input.value = '';
    currentEventModalState.search = '';
    loadAnalyticsEventLogs(1);
}
window.resetAnalyticsEventSearch = resetAnalyticsEventSearch;

function refreshAnalyticsEventLogs() {
    loadAnalyticsEventLogs(currentEventModalState.page || 1);
}
window.refreshAnalyticsEventLogs = refreshAnalyticsEventLogs;

function changeAnalyticsEventPage(delta) {
    const target = currentEventModalState.page + delta;
    if (target < 1 || target > currentEventModalState.totalPages) return;
    loadAnalyticsEventLogs(target);
}
window.changeAnalyticsEventPage = changeAnalyticsEventPage;

function renderAnalyticsEventTable(items, eventType) {
    const container = document.getElementById('analyticsEventTableContainer');
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = '<div style="text-align:center; padding:36px; color:var(--text-muted); font-size:0.85rem;">该事件类型暂无符合条件的审计记录</div>';
        return;
    }

    let html = '<div class="table-container" style="overflow-x:auto;">'
        + '<table class="data-table" id="analyticsRecentEventsTable" style="width:100%; min-width:1160px; table-layout:auto;">'
        + '<thead><tr>'
        + '<th style="white-space:nowrap; width:180px; min-width:170px; text-align:left;">触发时间 (最新在前)</th>'
        + '<th style="white-space:nowrap; width:220px; min-width:200px; text-align:left;">事件类型</th>'
        + '<th style="min-width:220px; text-align:left;">业务说明</th>'
        + '<th class="col-center" style="white-space:nowrap; width:90px; text-align:center;">单据 ID</th>'
        + '<th style="white-space:nowrap; width:130px; text-align:left;">操作账号</th>'
        + '<th style="min-width:320px; text-align:left;">事件属性 (Properties)</th>'
        + '</tr></thead><tbody>';

    items.forEach(ev => {
        let propsFull = '';
        try {
            propsFull = typeof ev.properties === 'object' ? JSON.stringify(ev.properties || {}, null, 2) : String(ev.properties || '{}');
        } catch (e) {
            propsFull = '{}';
        }
        const alias = _eventTypeAlias(ev.event_type || eventType || '');
        const fmtTs = ev.ts ? String(ev.ts).replace('T', ' ') : '';
        const displayTs = _fmtDateTime(ev.ts);

        const ridHtml = ev.receipt_id != null
            ? '<a href="javascript:void(0)" onclick="openParsingLogDetailModal(' + Number(ev.receipt_id) + ')" style="text-decoration:none; color:var(--primary-color, #2563eb); font-weight:600; cursor:pointer;" title="点击查看单据解析生命周期详情">#' + Number(ev.receipt_id) + '</a>'
            : '-';

        html += '<tr>'
            + '<td style="font-size:0.78rem; font-family:var(--font-mono); white-space:nowrap; text-align:left; font-variant-numeric:tabular-nums;">' + w2Escape(displayTs) + '</td>'
            + '<td style="white-space:nowrap; text-align:left;" title="' + w2Escape(ev.event_type || '') + '">'
            + '<code style="white-space:nowrap; font-size:0.8rem; display:inline-block; font-family:monospace; background:rgba(0,0,0,0.03); padding:2px 6px; border-radius:4px;">' + w2Escape(ev.event_type || '') + '</code>'
            + '</td>'
            + '<td style="font-size:0.82rem; color:var(--text-main); text-align:left; line-height:1.4;" title="' + w2Escape(alias) + '">' + w2Escape(alias) + '</td>'
            + '<td class="col-center" style="white-space:nowrap; text-align:center;">' + ridHtml + '</td>'
            + '<td style="font-size:0.78rem; white-space:nowrap; text-align:left; color:var(--text-muted);">' + w2Escape(ev.account_id || '-') + '</td>'
            + '<td style="font-size:0.75rem; text-align:left; color:var(--text-muted); line-height:1.4;">'
            + '<pre style="font-family:monospace; font-size:0.75rem; background:rgba(0,0,0,0.02); padding:4px 8px; border-radius:4px; margin:0; white-space:pre-wrap; word-break:break-all; max-height:120px; overflow-y:auto;">' + w2Escape(propsFull) + '</pre>'
            + '</td>'
            + '</tr>';
    });

    html += '</tbody></table></div>';
    container.innerHTML = html;
}
window.renderAnalyticsEventTable = renderAnalyticsEventTable;

// 全局键盘监听：无障碍模态弹窗 Esc 安全关闭与 Tab/Shift+Tab 焦点封闭 (QA-P1-04, AC-08-05)
document.addEventListener('keydown', function(e) {
    let modal = null;
    if (typeof _activeAccessibleModals !== 'undefined' && _activeAccessibleModals.length > 0) {
        modal = _activeAccessibleModals[_activeAccessibleModals.length - 1].modal;
    }
    if (!modal || modal.classList.contains('hide') || modal.style.display === 'none') {
        const visibleModals = Array.from(document.querySelectorAll('.modal-backdrop:not(.hide)'))
            .filter(el => el.style.display !== 'none');
        if (visibleModals.length > 0) {
            modal = visibleModals[visibleModals.length - 1];
        } else {
            return;
        }
    }

    if (e.key === 'Escape' || e.key === 'Esc') {
        e.preventDefault();
        e.stopPropagation();
        if (modal.id === 'modalApproveConfirmDialog') {
            closeApproveConfirmModal();
        } else if (modal.id === 'modalConflictDiffDialog') {
            closeConflictDiffModal();
        } else if (modal.id === 'modalStockMovementDialog') {
            closeStockMovementModal();
        } else if (modal.id === 'analyticsEventModal') {
            closeAnalyticsEventModal();
        } else if (modal.id === 'kpiTrendModal') {
            closeKpiTrendModal();
        } else if (typeof closeAccessibleModal === 'function') {
            closeAccessibleModal(modal);
        } else {
            modal.classList.add('hide');
        }
        return;
    }

    if (e.key === 'Tab') {
        if (typeof getModalFocusableElements !== 'function') return;
        const focusables = getModalFocusableElements(modal);
        if (focusables.length === 0) {
            e.preventDefault();
            return;
        }

        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement;

        // 如果焦点在模态框外部，强行拉入内部
        if (!modal.contains(active)) {
            e.preventDefault();
            if (e.shiftKey) {
                last.focus();
            } else {
                first.focus();
            }
            return;
        }

        if (e.shiftKey) {
            if (active === first) {
                e.preventDefault();
                last.focus();
            }
        } else {
            if (active === last) {
                e.preventDefault();
                first.focus();
            }
        }
    }
});

