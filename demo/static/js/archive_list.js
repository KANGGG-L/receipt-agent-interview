// demo/static/js/archive_list.js
// 归档列表与 KPI：供应商数据源、单据列表缓存、归档筛选/表格、审批入账/标记/
// 重试/转手工入口、归档详情 Side-by-Side 与行级反馈（FR-8）。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
function loadSuppliersData() {
    apiFetch('/api/suppliers')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') return;
        const suppliers = ret.data || [];
        // 供应商 combobox 联想数据源
        availableSuppliers = suppliers;
        // 上传卡片「供应商（可选）」下拉同源填充（同一份列表，不额外发请求）
        populateVendorHintOptions(suppliers);
        // 兼容旧 datalist（可能不存在于当前页面，做 null 防护）
        const datalist = document.getElementById('dlSuppliers');
        if (datalist) {
            datalist.innerHTML = '';
            suppliers.forEach(s => {
                const opt = document.createElement('option');
                opt.value = s.name;
                datalist.appendChild(opt);
            });
        }
    })
    .catch(err => { if (!window.__roleSwitchPending) console.error("加载供应商列表失败:", err); });
}

let allArchiveReceipts = [];

// SAAS-SCALE: client-side pagination for large archive lists
const ARCHIVE_PAGE_SIZE = 50;
let archiveCurrentPage = 1;
let archiveFilteredCache = [];

function getArchivePageSize() {
    return ARCHIVE_PAGE_SIZE;
}

function setArchivePage(page, opts) {
    const total = archiveFilteredCache.length;
    const pages = Math.max(1, Math.ceil(total / ARCHIVE_PAGE_SIZE) || 1);
    let p = Number(page) || 1;
    if (p < 1) p = 1;
    if (p > pages) p = pages;
    archiveCurrentPage = p;
    renderArchiveTable(archiveFilteredCache, { keepPage: true, ...(opts || {}) });
}
window.setArchivePage = setArchivePage;

function updateArchivePaginationUI(total, from, to, page, pages) {
    const rangeEl = document.getElementById('archivePageRange');
    const pager = document.getElementById('archivePagination');
    const prev = document.getElementById('archivePagePrev');
    const next = document.getElementById('archivePageNext');
    const pageLabel = document.getElementById('archivePageLabel');
    if (rangeEl) {
        if (total === 0) rangeEl.textContent = '显示 0–0';
        else rangeEl.textContent = '显示 ' + from + '–' + to;
    }
    if (pageLabel) pageLabel.textContent = page + ' / ' + pages;
    if (prev) prev.disabled = page <= 1 || total === 0;
    if (next) next.disabled = page >= pages || total === 0;
    if (pager) pager.classList.toggle('hide', false);
}

function markArchiveKpiFailed() {
    ['todoPendingNum', 'todoFailedNum', 'todoOverdueNum'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.textContent = '—';
    });
    let retry = document.getElementById('archiveKpiRetry');
    if (!retry) {
        const bar = document.getElementById('todoBar');
        retry = document.createElement('button');
        retry.id = 'archiveKpiRetry';
        retry.type = 'button';
        retry.className = 'btn btn-secondary';
        retry.textContent = '重试';
        retry.style.minHeight = '38px';
        retry.addEventListener('click', () => loadReceiptsHistory());
        if (bar && bar.parentNode) bar.parentNode.insertBefore(retry, bar.nextSibling);
    }
    retry.classList.remove('hide');
}

let receiptsListCache = null;
let receiptsListInflight = null;

function fetchReceiptListShared(force) {
    if (force) receiptsListCache = null;
    if (!force && receiptsListCache) return Promise.resolve(receiptsListCache);
    if (!force && receiptsListInflight) return receiptsListInflight;
    receiptsListInflight = apiFetch('/api/receipts')
        .then(res => {
            if (!res || !res.ok) throw new Error('receipts');
            return res.json();
        })
        .then(ret => {
            if (!ret || ret.status !== 'success' || !Array.isArray(ret.data)) throw new Error('receipts');
            receiptsListCache = ret;
            return ret;
        })
        .finally(() => { receiptsListInflight = null; });
    return receiptsListInflight;
}

function invalidateReceiptListCache() {
    receiptsListCache = null;
}
window.__invalidateReceiptList = invalidateReceiptListCache;
window.fetchReceiptListShared = fetchReceiptListShared;

let archiveLoadSeq = 0;

function loadReceiptsHistory() {
    const mySeq = ++archiveLoadSeq;
    fetchReceiptListShared(false)
        .then(ret => {
            if (mySeq !== archiveLoadSeq) return;
            const retry = document.getElementById('archiveKpiRetry');
            if (retry) retry.classList.add('hide');
            allArchiveReceipts = ret.data || [];
            archiveCurrentPage = 1;
            populateFilterSuppliers();
            applyArchiveFilters();
            updateTodoBar();
        })
        .catch(() => {
            if (mySeq !== archiveLoadSeq) return;
            markArchiveKpiFailed();
        });
}

function populateFilterSuppliers() {
    const sel = document.getElementById('fltSupplier');
    if (!sel) return;
    const currentVal = sel.value;
    sel.innerHTML = `<option value="">全部供应商</option>`;
    
    const set = new Map();
    if (typeof availableSuppliers !== 'undefined' && availableSuppliers) {
        // U-06：过滤软停用（active=0）供应商，脏名不进归档筛选下拉
        availableSuppliers.forEach(sup => {
            if (sup.active === 0 || sup.active === '0' || sup.active === false) return;
            set.set(sup.name, sup.supplier_code);
        });
    }
    allArchiveReceipts.forEach(r => {
        if (r.supplier_name && !set.has(r.supplier_name)) {
            set.set(r.supplier_name, r.supplier_code);
        }
    });

    set.forEach((code, name) => {
        const opt = document.createElement('option');
        opt.value = name;
        opt.textContent = code ? `${name} [${code}]` : name;
        if (name === currentVal) opt.selected = true;
        sel.appendChild(opt);
    });
}

function getTodayDateStr() {
    const d = new Date();
    const yyyy = d.getFullYear();
    const mm = String(d.getMonth() + 1).padStart(2, '0');
    const dd = String(d.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
}

let archiveFilterTimer = null;

function applyArchiveFilters(delay = 60) {
    if (!allArchiveReceipts) return;

    if (archiveFilterTimer) clearTimeout(archiveFilterTimer);

    archiveFilterTimer = setTimeout(() => {
        const supplierFilter = (document.getElementById('fltSupplier')?.value || '').trim().toLowerCase();
        const statusFilter = (document.getElementById('fltStatus')?.value || '').trim();
        // Wave 1：轻量 paid/unpaid 筛选（03 章六）
        const payStateFilter = (document.getElementById('fltPayState')?.value || '').trim();

        const todayStr = getTodayDateStr();

        let receiptDateFrom = document.getElementById('fltReceiptDateFrom')?.value || '';
        let receiptDateTo = document.getElementById('fltReceiptDateTo')?.value || '';
        if (receiptDateFrom && !receiptDateTo) {
            receiptDateTo = todayStr;
        }
        
        let uploadDateFrom = document.getElementById('fltUploadDateFrom')?.value || '';
        let uploadDateTo = document.getElementById('fltUploadDateTo')?.value || '';
        if (uploadDateFrom && !uploadDateTo) {
            uploadDateTo = todayStr;
        }
        
        let updateDateFrom = document.getElementById('fltUpdateDateFrom')?.value || '';
        let updateDateTo = document.getElementById('fltUpdateDateTo')?.value || '';
        if (updateDateFrom && !updateDateTo) {
            updateDateTo = todayStr;
        }
        
        const minAmtVal = document.getElementById('fltAmountMin')?.value;
        const maxAmtVal = document.getElementById('fltAmountMax')?.value;
        const amountMin = minAmtVal !== '' ? parseFloat(minAmtVal) : NaN;
        const amountMax = maxAmtVal !== '' ? parseFloat(maxAmtVal) : NaN;
        
        const sortMode = document.getElementById('fltSort')?.value || 'id_desc';

        let filtered = allArchiveReceipts.filter(r => {
            // 1. 供应商 Filter
            if (supplierFilter) {
                const supName = (r.supplier_name || '').toLowerCase();
                const supCode = (r.supplier_code || '').toLowerCase();
                if (supName !== supplierFilter && supCode !== supplierFilter && !supName.includes(supplierFilter)) {
                    return false;
                }
            }

            // 2. 状态 Filter
            if (statusFilter && (r.status || 'uploaded') !== statusFilter) {
                return false;
            }

            // 2a. 状态聚合筛选（老板口语，D-SUP-10 / 4.3）
            if (ArchiveUI.statusGroup) {
                const grp = STATUS_GROUP_MAP[ArchiveUI.statusGroup] || [];
                if (!grp.includes(r.status || 'uploaded')) return false;
            }

            // 2a-2. 关键字搜索：供应商名 / 单号 sheet_name / 单据 #id（M5）
            // 去掉用户按 placeholder「单据 #id」输入的前导 # 再比，#12 与 12 等价
            if (ArchiveUI.q) {
                const q = ArchiveUI.q.replace(/^#+/, '').toLowerCase();
                if (q) {
                    const hit =
                        (r.supplier_name || '').toLowerCase().includes(q) ||
                        (r.sheet_name || '').toLowerCase().includes(q) ||
                        String(r.id) === q ||
                        String(r.id).includes(q);
                    if (!hit) return false;
                }
            }

            // 2b. Wave 1 付款状态 Filter：unpaid=未付(含逾期)，paid=已付(含现结)
            if (payStateFilter === 'unpaid') {
                if (r.payment_status !== 'unpaid' && r.payment_status !== 'overdue' && r.payment_status !== 'partial') return false;
            } else if (payStateFilter === 'paid') {
                if (r.payment_status !== 'paid' && r.payment_status !== 'paid_at_delivery') return false;
            }

            // 3. 开单日期区间 Filter
            if (r.receipt_date) {
                const rDate = r.receipt_date.trim();
                if (receiptDateFrom && rDate < receiptDateFrom) return false;
                if (receiptDateTo && rDate > receiptDateTo) return false;
            } else if (receiptDateFrom || receiptDateTo) {
                return false;
            }

            // 4. 上传日期区间 Filter
            if (r.upload_date) {
                const uDate = r.upload_date.split(' ')[0].split('T')[0];
                if (uploadDateFrom && uDate < uploadDateFrom) return false;
                if (uploadDateTo && uDate > uploadDateTo) return false;
            } else if (uploadDateFrom || uploadDateTo) {
                return false;
            }

            // 5. 编辑日期区间 Filter
            if (r.updated_date) {
                const upDate = r.updated_date.split(' ')[0].split('T')[0];
                if (updateDateFrom && upDate < updateDateFrom) return false;
                if (updateDateTo && upDate > updateDateTo) return false;
            } else if (updateDateFrom || updateDateTo) {
                return false;
            }

            // 6. 金额区间 Filter
            const total = r.total_amount || 0.0;
            if (!isNaN(amountMin) && total < amountMin) return false;
            if (!isNaN(amountMax) && total > amountMax) return false;

            return true;
        });

        // 7. 排序 (Sort)
        filtered.sort((a, b) => {
            if (sortMode === 'amount_desc') {
                return (b.total_amount || 0) - (a.total_amount || 0);
            } else if (sortMode === 'amount_asc') {
                return (a.total_amount || 0) - (b.total_amount || 0);
            } else if (sortMode === 'receipt_date_desc') {
                return (b.receipt_date || '').localeCompare(a.receipt_date || '');
            } else if (sortMode === 'receipt_date_asc') {
                return (a.receipt_date || '').localeCompare(b.receipt_date || '');
            } else {
                return (b.id || 0) - (a.id || 0);
            }
        });

        archiveCurrentPage = 1;
        renderArchiveTable(filtered);
    }, delay);
}

function resetArchiveFilters() {
    ['fltSupplier', 'fltStatus', 'fltPayState', 'fltReceiptDateFrom', 'fltReceiptDateTo', 'fltUploadDateFrom', 'fltUploadDateTo', 'fltUpdateDateFrom', 'fltUpdateDateTo', 'fltAmountMin', 'fltAmountMax', 'fltKeyword'].forEach(id => {
        const elem = document.getElementById(id);
        if (elem) elem.value = '';
    });
    const sortElem = document.getElementById('fltSort');
    if (sortElem) sortElem.value = 'id_desc';
    // 重置口语化聚合状态（09 方案）
    ArchiveUI.q = '';
    setArchiveStatusGroup('');
    setArchiveQuickRange('');
    if (typeof toggleArchiveAdvanced === 'function') {
        const adv = document.getElementById('archiveAdvanced');
        if (adv && !adv.classList.contains('hide')) toggleArchiveAdvanced();
    }
    applyArchiveFilters();
}

// 归档状态徽章——文案/样式/悬停说明全部取自 RECEIPT_STATUSES（单一事实源），
// 未知状态回落显示原始值。
function renderStatusBadge(st) {
    const spec = RECEIPT_STATUSES[String(st || '')];
    if (!spec) {
        return '<span class="badge badge-secondary">' + w2Escape(st) + '</span>';
    }
    return '<span class="badge ' + spec.cls + '" title="' + w2Escape(spec.title) + '">'
        + w2Escape(spec.label) + '</span>';
}
window.renderStatusBadge = renderStatusBadge;

// 按 RECEIPT_STATUSES 定义顺序生成归档「单据状态」筛选下拉（保留「全部状态」占位项），
// 避免 index.html 与 JS 各维护一份状态清单。
function populateReceiptStatusFilter() {
    const sel = document.getElementById('fltStatus');
    if (!sel) return;
    const current = sel.value;
    sel.innerHTML = '<option value="">全部状态</option>'
        + Object.keys(RECEIPT_STATUSES).map(k =>
            '<option value="' + w2Escape(k) + '">' + w2Escape(RECEIPT_STATUSES[k].filter) + '</option>'
        ).join('');
    sel.value = current;
}

// 渲染归档表格逻辑（锁高防抖动与焦点恢复优化）
function renderArchiveTable(receipts, opts) {
    opts = opts || {};
    const list = Array.isArray(receipts) ? receipts : [];
    if (!opts.keepPage) {
        archiveFilteredCache = list;
    } else {
        // keepPage: re-slice current cache (page nav); still accept explicit list
        if (Array.isArray(receipts)) archiveFilteredCache = list;
    }
    const total = archiveFilteredCache.length;
    const pages = Math.max(1, Math.ceil(total / ARCHIVE_PAGE_SIZE) || 1);
    if (archiveCurrentPage > pages) archiveCurrentPage = pages;
    if (archiveCurrentPage < 1) archiveCurrentPage = 1;
    const page = archiveCurrentPage;
    const startIdx = total === 0 ? 0 : (page - 1) * ARCHIVE_PAGE_SIZE;
    const endIdx = Math.min(startIdx + ARCHIVE_PAGE_SIZE, total);
    const pageRows = archiveFilteredCache.slice(startIdx, endIdx);
    const from = total === 0 ? 0 : startIdx + 1;
    const to = endIdx;

    const recordBadge = document.getElementById('archiveRecordCount');
    if (recordBadge) {
        recordBadge.textContent = '共 ' + total + ' 条记录';
    }
    updateArchivePaginationUI(total, from, to, page, pages);

    const tbody = document.getElementById('archiveTableBody');
    if (!tbody) return;

    // 关键修复 1：重绘前锁定表格容器当前实际高度，防止 DOM 瞬间塌陷 (Collapsing) 导致视口跳晃
    const tableContainer = tbody.closest('.table-container');
    if (tableContainer) {
        const currentH = tableContainer.offsetHeight;
        if (currentH > 0) {
            tableContainer.style.minHeight = currentH + 'px';
        }
    }

    tbody.innerHTML = '';

    // SAAS-SCALE empty states: empty DB vs no-filter-results vs has-data page
    const dbEmpty = !Array.isArray(allArchiveReceipts) || allArchiveReceipts.length === 0;
    if (total === 0) {
        const msg = dbEmpty
            ? '暂无归档单据（空库）'
            : '未找到符合筛选条件的归档记录';
        const kind = dbEmpty ? 'empty-db' : 'no-filter-results';
        tbody.innerHTML = '<tr data-empty-state="' + kind + '"><td colspan="10" style="text-align:center; color:var(--text-muted); padding:36px;">' + msg + '</td></tr>';
    } else {
        let html = '';
        pageRows.forEach(function(r) {
            const st = r.status || 'uploaded';
            const statusBadgeHTML = renderStatusBadge(st);

            const supCode = r.supplier_code
                ? '<span class="badge badge-secondary" style="font-size:0.72rem; font-family:monospace; margin-left:4px; opacity:0.85;">' + w2Escape(r.supplier_code) + '</span>'
                : '';

            const rid = Number(r.id);
            let actionBtns = '<button class="btn btn-primary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="loadReceiptDetail(' + rid + ')">详情</button>' +
                ' <a href="' + w2Escape(r.image_url || '#') + '" target="_blank" class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;">原图</a>';
            if (st === 'error') {
                actionBtns += ' <button class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="retryReceiptFromArchive(' + rid + ')">重试</button>' +
                    ' <button class="btn btn-secondary" style="padding:2px 6px; font-size:0.75rem; line-height:1.4;" onclick="convertReceiptFromArchive(' + rid + ')">转手工</button>';
            }

            const upDate = renderDateCell(r.upload_date);
            const editDate = renderDateCell(r.updated_date);
            const recDate = renderDateCell(r.receipt_date);

            // P1-2：黄底 row-warning 绑定 quality_warnings / review_priority>0.6
            // P14/P15：parsed_with_warnings（门禁带警告）同样标黄，并在状态徽章上标出门禁问题
            const _hasQw = Array.isArray(r.quality_warnings) && r.quality_warnings.length > 0;
            const _rps = typeof r.review_priority_score === 'number' ? r.review_priority_score : 0;
            const _hasGateWarn = st === 'parsed_with_warnings';
            // 确定性复核信号（整单级）：街市手写单。黄底只给「尚未人工核对」的状态
            // （RECEIPT_PENDING_REVIEW_STATES）——已修改/已入账的单据不再刷黄，
            // 避免把「已完成的工作」重复标成待办；而供应商名旁的「手写单」来源徽标
            // 在任何状态都显示（它描述的是单据来源事实，不是待办状态）。
            // 判据 isHandwrittenReceipt / isReceiptPendingHumanReview 与详情页提示条同源。
            const _isHandwritten = isHandwrittenReceipt(r);
            const _isHandwrittenPending = _isHandwritten && isReceiptPendingHumanReview(st);
            const _rowWarn = (_hasQw || _hasGateWarn || _isHandwrittenPending || _rps > 0.6) ? ' class="row-warning"' : '';
            const _mathWarnLines = (Array.isArray(r.math_warnings) && r.math_warnings.length)
                ? r.math_warnings.map(humanizeGateMsg).join('；') : '';
            const _reviewReasonLines = _mathWarnLines
                || (_isHandwrittenPending ? '街市手写单（NCR），需逐行对照原图核对' : '');
            const statusBadgeCell = ((_hasGateWarn || _isHandwrittenPending) && _reviewReasonLines)
                ? statusBadgeHTML + '<div class="gate-warn-text" style="font-size:0.68rem; color:var(--warning); margin-top:2px; max-width:200px; white-space:normal; line-height:1.3;">' + w2Escape(_reviewReasonLines) + '</div>'
                : statusBadgeHTML;
            const handwrittenBadge = _isHandwritten
                ? '<span class="badge badge-secondary" style="font-size:0.72rem; margin-left:4px;" title="街市手写单（NCR）：手写字迹与花码易误读，建议逐行对照原图">手写单</span>'
                : '';
            html += '<tr data-receipt-id="' + rid + '"' + _rowWarn + '>' +
                '<td style="vertical-align:middle;">#' + rid + '</td>' +
                '<td style="vertical-align:middle;"><strong>' + w2Escape(r.supplier_name || '-') + '</strong>' + supCode + handwrittenBadge + greyBadgeHtml(r.use_grey) + '</td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.82rem; color:var(--text-muted);">' + upDate + '</span></td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.85rem; color:var(--text-main);">' + editDate + '</span></td>' +
                '<td style="vertical-align:middle;"><span style="font-size:0.82rem; color:var(--text-muted);">' + recDate + '</span></td>' +
                '<td style="vertical-align:middle; text-align:center;"><strong style="font-size:1.02rem; font-weight:700;">' + formatCurrency(r.total_amount, r.currency) + '</strong></td>' +
                // Wave 2（D44）：归档列表展示单据级部门（未打 → 未分配）
                '<td class="col-center" style="vertical-align:middle;">' + (r.department_name
                    ? '<span class="badge badge-secondary" style="font-size:0.75rem;">' + w2Escape(r.department_name) + '</span>'
                    : '<span style="color:var(--text-muted); font-size:0.75rem;">未分配</span>') + '</td>' +
                '<td class="col-center" style="vertical-align:middle;">' + statusBadgeCell + '</td>' +
                '<td class="col-center" style="vertical-align:middle;">' + payStatusLiteBadgeHtml(r) + '</td>' +
                '<td class="col-center" style="vertical-align:middle; white-space:nowrap; padding-right:4px;">' + actionBtns + '</td>' +
            '</tr>';
        });
        tbody.innerHTML = html;
    }

    // 关键修复 2：待 Chrome 浮层动画和焦点计算平滑完成后，在下一帧解锁恢复默认 minHeight
    requestAnimationFrame(() => {
        setTimeout(() => {
            if (tableContainer) {
                tableContainer.style.minHeight = '480px';
            }
        }, 200);
    });
}

// 彻底解决 Chrome 日历弹窗关闭后自动 scrollIntoView() 引发页面跳晃的问题
(function setupDateScrollLock() {
    let lockedScrollTop = null;

    function captureScroll() {
        const wrapper = document.querySelector('.main-wrapper');
        lockedScrollTop = wrapper ? wrapper.scrollTop : window.scrollY;
    }

    function restoreScroll() {
        if (lockedScrollTop === null) return;
        const wrapper = document.querySelector('.main-wrapper');
        if (wrapper) {
            wrapper.scrollTop = lockedScrollTop;
        }
        window.scrollTo(0, lockedScrollTop);
    }

    document.addEventListener('DOMContentLoaded', () => {
        const dateInputs = document.querySelectorAll('input[type="date"], .archive-date-grid button');
        dateInputs.forEach(el => {
            el.addEventListener('focus', captureScroll, { passive: true });
            el.addEventListener('mousedown', captureScroll, { passive: true });
            el.addEventListener('click', captureScroll, { passive: true });
            el.addEventListener('change', () => {
                restoreScroll();
                setTimeout(restoreScroll, 50);
                setTimeout(restoreScroll, 150);
                setTimeout(restoreScroll, 250);
            });
            el.addEventListener('blur', () => {
                setTimeout(restoreScroll, 50);
                setTimeout(restoreScroll, 150);
            });
        });
    });
})();


// Q33/W6 Q1：approve 必须携带 version（缺失 → 400 VERSION_REQUIRED）；
// 处理 409 VERSION_CONFLICT（提示 + 重载详情）；返回 Promise<boolean> 供弹窗决定是否关闭。
function approveReceipt(receiptId, knownVersion) {
    return askConfirm({
        title: '审核入账',
        message: `单据 #${receiptId} 审核入账后会计入支出，库存也会增加。确定入账吗？`,
        confirmText: '审核入账',
        cancelText: '先不入账'
    }).then(ok => ok ? _postApprove(receiptId, knownVersion) : false);
}

function _postApprove(receiptId, knownVersion) {
    // D17/W6 Q1：始终提交 JSON body 含 version（详情加载时记录于 currentArchiveDetailData）
    const body = { version: knownVersion };
    return apiFetch(`/api/receipt/${receiptId}/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            showToast('单据已被他人修改，已重新加载', 'warning');
            loadReceiptDetail(receiptId);   // 重载详情（含最新 version），弹窗保持打开
            return false;
        }
        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，已重新加载最新内容', 'warning');
            loadReceiptDetail(receiptId);
            return false;
        }
        if (handleApproveError(httpStatus, ret, { receiptId: receiptId })) return false;
        // E-P1-4：403 人话统一（审批为 owner 专属，staff 触发时给出角色指引）
        if (toastHttpError(httpStatus, ret)) return false;
        if (!ret || ret.status !== 'success') {
            showToast('审核入账没有成功：' + TERMS.humanizeCode(ret && ret.code, ret && ret.msg), 'error');
            return false;
        }
        // P1-13: approve 成功后更新本地 version（响应契约带 version）
        if (currentArchiveDetailData) {
            const nv = extractVersionFromResponse(ret);
            if (nv != null) currentArchiveDetailData.version = nv;
        }
        showToast(`已审核入账：单据 #${receiptId}`, 'success');
        loadReceiptsHistory();
        loadFinancePanel();     // Wave 1：approve 落库钩子可能写预期付款日快照 → 提醒联动
        return true;
    })
    .catch(err => {
        console.error('审核请求异常:', err);
        showToast('审核请求失败，请检查网络后重试', 'error');
        return false;
    });
}

function flagReceipt(receiptId) {
    // 应用内弹窗，可填原因；成功后刷新应付（flagged 单据移出应付口径）
    return flagReceiptWithReason(receiptId, () => { loadFinancePanel(); });
}

function modalApproveReceipt() {
    if (!currentArchiveReceiptId) return;
    // D16: 有未保存修改时审批不执行——保存是保存，审批是审批
    if (hasUnsavedArcChanges) {
        showToast('有未保存修改，请先保存单据再审批', 'warning');
        return;
    }
    // Q33: 携带详情加载时记录的 version；成功后才关闭弹窗
    const knownVersion = currentArchiveDetailData ? currentArchiveDetailData.version : null;
    approveReceipt(currentArchiveReceiptId, knownVersion).then(ok => {
        if (ok) closeArchiveModal(true);
    });
}

function modalFlagReceipt() {
    if (!currentArchiveReceiptId) return;
    const rid = currentArchiveReceiptId;
    flagReceipt(rid).then(ok => { if (ok) closeArchiveModal(true); });
}

// ---- Q35：归档列表 error 行的「重试 / 转手工」入口 ----
// rid 在 renderArchiveTable 已经过 Number() 强制为数字；此处再做防御性校验。
function retryReceiptFromArchive(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    askConfirm({ title: '重新识别', message: `让系统重新读一遍单据 #${rid}？`, confirmText: '重新识别', cancelText: '先不用' }).then(ok => { if (ok) _postRetryFromArchive(rid); });
}

function _postRetryFromArchive(rid) {
    showToast(`单据 #${rid} 重试已提交…`, 'info');
    apiFetch(`/api/receipt/${rid}/retry`, { method: 'POST' })
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' || ret.status === 'queued') {
                showToast(`单据 #${rid} 已重新进入识别流程`, 'success');
                loadReceiptsHistory();   // 刷新归档列表（状态变为 parsing/uploaded）
            } else {
                showToast('重试失败：' + (ret.msg || '请稍后重试'), 'error');
            }
        })
        .catch(err => {
            console.error('重试请求失败:', err);
            showToast('重试请求失败，请检查网络后重试', 'error');
        });
}

function convertReceiptFromArchive(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) return;
    askConfirm({
        title: '转手工录入',
        message: `把单据 #${rid} 改成手工录入？原图会保留，已经读出来的内容也会保留，你可以自己改。`,
        confirmText: '转手工录入',
        cancelText: '先不转'
    }).then(ok => { if (ok) _postConvertFromArchive(rid); });
}

function _postConvertFromArchive(rid) {
    apiFetch(`/api/receipt/${rid}/convert_manual`, { method: 'POST' })
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (ret && ret.status === 'success') {
                showToast(`单据 #${rid} 已转为手工录入，点进详情就能修改，原图已保留`, 'success');
                loadReceiptsHistory();   // 刷新归档列表（状态变为 edited）
            } else {
                showToast('没能转成手工录入：' + TERMS.humanizeCode(ret && ret.code, ret && ret.msg) + '。原图已保留，可以再点一次「转手工」', 'error', 7000);
            }
        })
        .catch(err => {
            console.error('转手工请求失败:', err);
            showToast('网络不通，没能转成手工录入，请检查网络后再试', 'error');
        });
}

let currentArchiveReceiptId = null;
// 归档详情完整 data（含 version/doc_form/settlement_type/payment_mark，字段可能暂时缺失）
let currentArchiveDetailData = null;
let arcZoom = 1.0;
let arcRotation = 0;
let arcPanX = 0;
let arcPanY = 0;
let arcActiveTool = null;
let hasUnsavedArcChanges = false;

function markArcDirty() {
    hasUnsavedArcChanges = true;
}

function isRagDataOnlyEnabled() {
    const cb = document.getElementById('arcDataOnlyToggle');
    return !!(cb && cb.checked);
}
function toggleRagDataOnly() {
    if (currentArchiveReceiptId) loadReceiptDetail(currentArchiveReceiptId);
}
function loadReceiptDetail(receiptId) {
    const qs = isRagDataOnlyEnabled() ? '?data_only=true' : '';
    if (typeof closeRowFeedbackModal === 'function') {
        closeRowFeedbackModal();
    }
    const receiptReq = apiFetch(`/api/receipt/${receiptId}${qs}`).then(res => res.json());
    const feedbackReq = (typeof apiFetch === 'function')
        ? apiFetch(`/api/receipt/${receiptId}/feedback`)
            .then(res => res.json().catch(() => null))
            .catch(err => {
                console.warn('[WARN] 加载单据反馈失败:', err);
                return null;
            })
        : Promise.resolve(null);

    Promise.all([receiptReq, feedbackReq])
    .then(([ret, fbRet]) => {
        if (!ret || ret.status !== 'success') {
            showToast('无法加载单据详情：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return;
        }

        currentArchiveReceiptId = ret.receipt_id;
        currentArchiveDetailData = ret.data;   // D17: 记下 version 等字段，保存时随提交做乐观锁
        hasUnsavedArcChanges = false;
        const data = ret.data;

        document.getElementById('archiveModalTitle').innerText = ` 历史收据明细校对 - 单据 #${ret.receipt_id}`;
        // 空 image_url（image_path 为空的历史单据）不得直接赋值：写空串会让浏览器
        // 回落到向当前页面发请求，且「查看大图」会指向空 URL。URL 为空时清空属性并隐藏入口。
        const archiveImageUrl = ret.image_url || (ret.data && ret.data.image_url) || '';
        const archivePreviewImg = document.getElementById('archivePreviewImg');
        const archiveOpenRawBtn = document.getElementById('btnArchiveOpenRaw');
        const archiveNoImage = document.getElementById('archiveNoImage');
        if (archiveImageUrl) {
            archivePreviewImg.src = archiveImageUrl;
            archivePreviewImg.style.display = '';
            if (archiveNoImage) {
                archiveNoImage.classList.add('hide');
                archiveNoImage.textContent = '无原图（手工录入）';
            }
            archiveOpenRawBtn.href = archiveImageUrl;
            archiveOpenRawBtn.classList.remove('hide');
        } else {
            archivePreviewImg.removeAttribute('src');
            archivePreviewImg.style.display = 'none';
            if (archiveNoImage) {
                const hasImg = !!(ret.has_original_image || (ret.data && ret.data.has_original_image) || (data && data.doc_form && data.doc_form !== 'manual_entry'));
                archiveNoImage.textContent = hasImg ? '原图加载失败' : '无原图（手工录入）';
                archiveNoImage.classList.remove('hide');
            }
            archiveOpenRawBtn.removeAttribute('href');
            archiveOpenRawBtn.classList.add('hide');
        }
        if (archivePreviewImg && !archivePreviewImg.__uat001Bound) {
            archivePreviewImg.__uat001Bound = true;
            archivePreviewImg.addEventListener('error', function () {
                const curId = currentArchiveReceiptId;
                if (!curId) return;
                if (!archivePreviewImg.__refreshing) {
                    archivePreviewImg.__refreshing = true;
                    if (typeof apiFetch === 'function') {
                        apiFetch(`/api/receipt/${curId}`)
                            .then(res => res.json())
                            .then(freshRet => {
                                archivePreviewImg.__refreshing = false;
                                const freshUrl = (freshRet && (freshRet.image_url || (freshRet.data && freshRet.data.image_url))) || '';
                                if (freshUrl && freshUrl !== archivePreviewImg.src) {
                                    archivePreviewImg.src = freshUrl;
                                    archivePreviewImg.style.display = '';
                                    if (archiveNoImage) archiveNoImage.classList.add('hide');
                                    if (archiveOpenRawBtn) archiveOpenRawBtn.href = freshUrl;
                                    return;
                                }
                                archivePreviewImg.style.display = 'none';
                                if (archiveNoImage) {
                                    archiveNoImage.textContent = '原图加载失败';
                                    archiveNoImage.classList.remove('hide');
                                }
                                if (typeof showToast === 'function') {
                                    showToast('原图加载失败：链接失效或网络异常，请重新打开刷新', 'error');
                                }
                            })
                            .catch(() => {
                                archivePreviewImg.__refreshing = false;
                                archivePreviewImg.style.display = 'none';
                                if (archiveNoImage) {
                                    archiveNoImage.textContent = '原图加载失败';
                                    archiveNoImage.classList.remove('hide');
                                }
                                if (typeof showToast === 'function') {
                                    showToast('原图加载失败：网络异常，请稍后重试', 'error');
                                }
                            });
                    }
                } else {
                    archivePreviewImg.style.display = 'none';
                    if (archiveNoImage) {
                        archiveNoImage.textContent = '原图加载失败';
                        archiveNoImage.classList.remove('hide');
                    }
                }
            });
        }
        resetArchiveImgTransform();

        renderArchiveForm(data);

        // UAT-034: 回填明细行反馈（点赞/点踩与原因文本）
        const feedbacks = (fbRet && fbRet.status === 'success' && Array.isArray(fbRet.feedbacks))
            ? fbRet.feedbacks : [];
        if (typeof rehydrateArchiveFeedbacks === 'function') {
            rehydrateArchiveFeedbacks(feedbacks);
        }

        // 绑定输入监听器，检测未保存修改（结算方式下拉变更同样算未保存修改）
        document.querySelectorAll('#arcSupplier, #arcDate, #arcSheet, #arcTotal').forEach(inp => {
            inp.oninput = markArcDirty;
        });
        const arcSettlementSel = document.getElementById('arcSettlementType');
        if (arcSettlementSel) arcSettlementSel.onchange = markArcDirty;
        // Wave 2（D44）：归档单据级部门下拉变更同样算未保存修改
        const arcDeptSel = document.getElementById('arcDepartmentId');
        if (arcDeptSel) arcDeptSel.onchange = markArcDirty;

        applyModalRoleVisibility();
        if (typeof syncArchiveActionButtons === 'function') syncArchiveActionButtons(data);
        const archiveModalEl = document.getElementById('archiveDetailModal');
        if (typeof openAccessibleModal === 'function') openAccessibleModal(archiveModalEl);
        else if (archiveModalEl) archiveModalEl.classList.remove('hide');
    })
    .catch(err => {
        console.error('加载单据详情请求失败:', err);
        showToast('加载单据详情失败，请检查网络后重试', 'error');
    });
}

async function closeArchiveModal(force = false) {
    if (!force && hasUnsavedArcChanges) {
        const confirmExit = (typeof uiConfirm === 'function')
            ? await uiConfirm({
                title: '放弃修改',
                message: '这张单有还没保存的修改。确定放弃并退出吗？',
                confirmText: '放弃这张单',
                cancelText: '先留着',
            })
            : false;
        if (!confirmExit) return;
    }
    if (typeof closeRowFeedbackModal === 'function') {
        closeRowFeedbackModal();
    }
    const archiveModalEl = document.getElementById('archiveDetailModal');
    if (archiveModalEl && typeof closeAccessibleModal === 'function') closeAccessibleModal(archiveModalEl);
    else if (archiveModalEl) archiveModalEl.classList.add('hide');
    currentArchiveReceiptId = null;
    currentArchiveDetailData = null;
    hasUnsavedArcChanges = false;
}

// P3/R5：归档履历徽章按真实 action_type 映射，消除"upload/其余一律校对编辑"
// 的二分失真（approve/flag/retry 等曾被误标为"校对编辑"）。未知类型回退显示
// 原始值，保证徽章语义不失真。
const ACTION_BADGES = {
    'upload':           { label: '初始上传',   cls: 'badge-secondary' },
    'auto_save':        { label: 'AI自动入库', cls: 'badge-warning' },
    'save_edited':      { label: '店员人工修改', cls: 'badge-info' },
    'edit':             { label: '店员人工修改', cls: 'badge-info' },
    'approve':          { label: '审批通过',   cls: 'badge-success' },
    'approve_receipt':  { label: '审批通过',   cls: 'badge-success' },
    'flag':             { label: '标记异常',   cls: 'badge-warning' },
    'flag_receipt':     { label: '标记异常',   cls: 'badge-warning' },
    'retry':            { label: '重试识别',   cls: 'badge-secondary' },
    'convert_manual':   { label: '转手工录入', cls: 'badge-secondary' },
    'discard_receipt':  { label: '放弃单据',   cls: 'badge-secondary' },
    'ocr_parsed':       { label: '识别完成',   cls: 'badge-success' },
    'ocr_error':        { label: '识别失败',   cls: 'badge-danger' },
    'ocr_attempt':      { label: '识别尝试',   cls: 'badge-secondary' },
    'async_parse':      { label: '异步解析',   cls: 'badge-secondary' },
    'startup_recovery': { label: '启动恢复',   cls: 'badge-secondary' },
    'recon_resolve':    { label: '对账处理',   cls: 'badge-secondary' },
    'recon_confirm':    { label: '对账确认',   cls: 'badge-success' },
    'supplier_merge':   { label: '供应商合并', cls: 'badge-secondary' },
    'state_change':     { label: '状态变更',   cls: 'badge-secondary' },
    'feedback':         { label: '用户反馈',   cls: 'badge-secondary' },
    'feedback_distilled': { label: '先验提炼', cls: 'badge-secondary' },
    'timeout':          { label: '识别超时',   cls: 'badge-danger' },
    // AI 决策履历专有：decision_type=extract 时真实结果在 props.status，无法直映射类型
    'extract_ok':       { label: '识别完成',   cls: 'badge-success' },
    'gate_reject':      { label: '门禁拦截',   cls: 'badge-danger' },
    'extract_fail':     { label: '识别失败',   cls: 'badge-secondary' },
};

// 唯一徽章入口：归档履历（原 auditActionBadge）与 AI 决策履历（原 aiDecisionBadge）共用。
// 新增 action_type / 决策结果只需在 ACTION_BADGES 登记一次，两个调用点自动一致。
// props 仅 AI 决策履历提供（承载 status/reason）；fallback 供两处保留各自的兜底文案。
function actionBadge(type, props, fallback) {
    const t = String(type == null ? '' : type);
    const p = (props && typeof props === 'object') ? props : {};
    // 交叉审核：需看该轮是否被跳过（audit_disabled / audit_error 或显式 skipped）
    if (t === 'audit') {
        const skipped = !!(p.skipped || /^audit_(disabled|error)/.test(String(p.reason || '')));
        if (skipped) {
            return `<span class="badge badge-secondary">${w2Escape('审核跳过')}</span>`;
        }
        return `<span class="badge" style="background:#cce5ff; color:#004085;">${w2Escape('交叉审核')}</span>`;
    }
    const spec = ACTION_BADGES[t] || ACTION_BADGES[String(p.status || '')];
    if (spec) {
        return `<span class="badge ${spec.cls}">${w2Escape(spec.label)}</span>`;
    }
    return `<span class="badge badge-secondary">${w2Escape(String(t || fallback || '未知'))}</span>`;
}

function renderArchiveForm(data) {
    if (typeof syncArchiveActionButtons === 'function') syncArchiveActionButtons(data);
    document.getElementById('arcSupplier').value = data.supplier_name || '';
    document.getElementById('arcDate').value = data.date || '';
    document.getElementById('arcSheet').value = data.sheet_name || '';
    document.getElementById('arcTotal').value = data.total_amount ? data.total_amount.toFixed(2) : '0.00';

    // M3/D4: 初始化结算方式与付款标记（字段可能缺失，容错）
    applySettlementToForm('arc', data);

    // F-P1-3 多币种
    const arcCur = document.getElementById('arcCurrency');
    if (arcCur) arcCur.value = (data.currency || 'HKD').toUpperCase();
    const inpCur = document.getElementById('inpCurrency');
    if (inpCur && data.currency) inpCur.value = (data.currency || 'HKD').toUpperCase();
    renderCurrencySymbol();

    // P14/P15 + 确定性复核：整单级提示条覆盖「门禁未通过」与「街市手写单」两类
    // 需人工复核的确定性信号（判据与复核台共用 receiptReviewReasons，口径不分岔）。
    const arcWarnBanner = document.getElementById('archiveMathWarnBanner');
    if (arcWarnBanner) {
        const _reasons = receiptReviewReasons(data);
        if (_reasons.length > 0) {
            arcWarnBanner.innerText = '[需人工复核] ' + _reasons.join('；')
                + '。请对照左侧原图逐项核对后再保存或入账。';
            arcWarnBanner.classList.remove('hide');
        } else {
            arcWarnBanner.innerText = '';
            arcWarnBanner.classList.add('hide');
        }
    }

    // E-P1-1 灰测 Tag：标题旁徽标
    const titleEl = document.getElementById('archiveModalTitle');
    if (titleEl) {
        // 移除旧徽标
        const old = titleEl.querySelector('.badge-grey');
        if (old) old.remove();
        if (data.use_grey) {
            const badge = document.createElement('span');
            badge.className = 'badge-grey';
            badge.style.marginLeft = '8px';
            badge.textContent = '灰测组';
            badge.title = '灰测组处理';
            titleEl.appendChild(badge);
        }
    }

    // D-P1-3 RAG data_only 调试卡可见性（截断显示，完整见接口 data_only=true）
    const ragCard = document.getElementById('arcRagContextCard');
    const ragPre = document.getElementById('arcRagContextPre');
    if (ragCard && ragPre) {
        if (isRagDataOnlyEnabled() && (data.rag_context != null)) {
            const ctxRaw = String(data.rag_context || '').trim();
            let display = ctxRaw ? ctxRaw : '（空）无 RAG 上下文（冷启动或未检索）';
            // 截断：前端调试卡最多显示 800 字符，避免超长撑满弹窗
            if (display.length > 800) {
                display = display.slice(0, 800) + '\n...（已截断，完整 rag_context_json 见 GET /api/receipt/{id}?data_only=true 审计日志）';
            }
            ragPre.textContent = display;
            ragCard.classList.remove('hide');
        } else {
            ragCard.classList.add('hide');
        }
    }

    // Wave 2（D44）：归档单据级部门下拉（只列 active；历史停用部门选择仍保留显示）
    populateDeptSelect(document.getElementById('arcDepartmentId'), data.department_id);

    const tbody = document.getElementById('arcTableBody');
    tbody.innerHTML = '';

    const items = data.items || [];
    items.forEach(item => appendArcTableRow(item));

    // 渲染操作与编辑改动履历 Logs
    const logContainer = document.getElementById('arcAuditLogsContainer');
    if (logContainer) {
        const logs = data.audit_logs || [];
        if (logs.length === 0) {
            logContainer.innerHTML = `<div style="color:var(--text-muted);">暂无编辑履历记录</div>`;
        } else {
            let html = "";
            logs.forEach(l => {
                // P3/R5：按真实 action_type 映射徽章（消除二分失真）
                const actionType = l.action_type || l.action;
                const typeBadge = actionBadge(actionType);
                const operator = l.operator || l.who || 'unknown';
                const ts = l.timestamp || l.ts || '';
                let details = l.details;
                if (!details) {
                    if (l.field && l.field !== 'receipt' && l.field !== 'status') {
                        details = `${l.field}: ${JSON.stringify(l.old)} -> ${JSON.stringify(l.new)}`;
                    } else if (l.field === 'receipt') {
                        details = `新建单据 #${l.new || ''}`;
                    } else if (l.field === 'status') {
                        details = `状态: ${l.old || ''} -> ${l.new || ''}`;
                    } else {
                        details = '';
                    }
                }
                html += `
                    <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                        <div>
                            <strong> ${w2Escape(operator)}</strong> ${typeBadge}
                            <span style="margin-left:8px; color:var(--text-main);">${w2Escape(details)}</span>
                        </div>
                        <div style="color:var(--text-muted); font-size:0.75rem; white-space:nowrap; margin-left:12px;"> ${renderDateCell(ts)}</div>
                    </div>
                `;
            });
            logContainer.innerHTML = html;
        }
    }

    // U-2：渲染 AI 决策履历（extract 各轮 + audit 交叉审核）
    renderArcAiDecisions(data.ai_decisions);
}

// U-2：AI 决策履历渲染（arcAiDecisionsContainer）
// 安全纪律（AC-D3 阻断级）：所有动态文本一律 w2Escape 后再插 innerHTML
function renderArcAiDecisions(decisions) {
    const container = document.getElementById('arcAiDecisionsContainer');
    if (!container) return;
    const list = Array.isArray(decisions) ? decisions : [];
    if (list.length === 0) {
        container.innerHTML = `<div style="color:var(--text-muted);">暂无 AI 决策记录</div>`;
        return;
    }
    let html = '';
    list.forEach(d => {
        let p = {};
        try {
            p = JSON.parse(d.ai_value || '{}');
            // 兼容历史 audit 行的双重编码（log_ai_decision 曾收 json.dumps 字符串）
            if (typeof p === 'string') p = JSON.parse(p);
        } catch (e) { p = {}; }
        if (!p || typeof p !== 'object') p = {};
        const badge = actionBadge(d.decision_type, p, 'AI 决策');
        let summary;
        if (d.decision_type === 'audit') {
            summary = [w2Escape(String(d.engine || '')), w2Escape(String(p.reason || ''))]
                .filter(Boolean).join(' · ');
        } else if (d.decision_type === 'auto_save') {
            summary = 'AI自动识别落库';
        } else {
            const attemptNo = Number(p.attempt || 0);
            const segs = [
                `第${w2Escape(String(attemptNo))}轮`,
                w2Escape(String(p.engine || d.engine || '')),
                w2Escape(String(p.status || '')),
            ];
            if (p.gate_err) segs.push(w2Escape(String(p.gate_err)));
            summary = segs.filter(Boolean).join(' · ');
        }
        html += `
            <div style="padding:4px 0; border-bottom:1px solid rgba(255,255,255,0.05); display:flex; justify-content:space-between; align-items:center;">
                <div>
                    ${badge}
                    <span style="margin-left:8px; color:var(--text-main);">${summary}</span>
                </div>
                <div style="color:var(--text-muted); font-size:0.75rem; white-space:nowrap; margin-left:12px;">${w2Escape(d.ts || '')}</div>
            </div>
        `;
    });
    container.innerHTML = html;
}

function appendArcTableRow(item = {}) {
    const tbody = document.getElementById('arcTableBody');
    const tr = document.createElement('tr');

    const rawName = item.name || item.raw_name || '';
    const qty = Number(item.quantity || 1.0);
    const unit = item.raw_unit || item.unit || 'kg';
    const price = Number(item.unit_price || 0.00);
    const amount = Number(item.amount || (qty * price));
    const skuId = item.sku_id || '';
    const splitRes = (typeof smartSplitItemName === 'function') ? smartSplitItemName(rawName) : null;

    if (unit && !availableUnits.includes(unit)) {
        availableUnits.push(unit);
        updateGlobalDatalistUnits();
    }

    const arcRowIdx = tbody.children.length;
    // D-P1-4 划线作废：is_void 行置灰 + 删除线 + 作废徽标 + 联动保存
    const isVoid = !!(item.is_void);
    const actualQtyArc = (item.actual_qty != null && item.actual_qty !== '') ? String(item.actual_qty) : '';
    if (isVoid) tr.style.opacity = '0.55';
    // 归档是只读校对视角，同样要让确定性待核验行可见——复用复核台同一套判定与角标
    // （itemReviewFlags / itemReviewBadgeHtml 与 appendTableRow 完全同源，不另造样式或文案）。
    // itemConfRaw 原样写入 dataset，便于归档侧也保留每行置信度读数（仅作参考）。
    const itemConfRaw = (typeof item.confidence === 'number' && isFinite(item.confidence)) ? item.confidence : null;
    const itemFlags = itemReviewFlags(item);
    // P0-2：归档弹窗明细行与 Tab1 同口径——品名/单位属性插值过 w2Escape
    tr.innerHTML = `
        <td>
            <input type="text" class="inp-name" value="${w2Escape(rawName)}" placeholder="品名" oninput="markArcDirty()" style="${isVoid ? 'text-decoration:line-through; color:#6c757d;' : ''}">
            ${itemReviewBadgeHtml(itemFlags, itemConfRaw)}
            ${skuId ? `<span class="badge badge-success">已关联</span>` : ''}
            ${isVoid ? `<span class="badge badge-secondary" title="划线作废，不计入总额">作废</span>` : ''}
        </td>
        <td><div style="display:flex; flex-direction:column; gap:4px;"><input type="number" step="0.01" class="inp-qty" value="${qty}" oninput="markArcDirty(); recalcArcRow(this)" title="数量" placeholder="数量" ${isVoid ? 'disabled style="text-decoration:line-through;"' : ''}>${splitRes && splitRes.quantity > 1 && qty !== splitRes.quantity ? `<span class="packaging-hint badge badge-light text-muted" style="font-size:0.72rem;" title="品名含包装规格：${splitRes.quantity}${splitRes.unit || ''}（未自动覆盖）">包装: ${splitRes.quantity}${splitRes.unit || ''}</span>` : ''}<input type="number" step="0.01" class="inp-actual-qty" value="${w2Escape(actualQtyArc)}" placeholder="实收" title="实际数量（手写改量，留空则按数量计）" style="padding:4px; font-size:0.78rem;" oninput="markArcDirty(); recalcArcRow(this)" ${isVoid ? 'disabled style="text-decoration:line-through;"' : ''}></div></td>
        <td>
            <div class="unit-combobox-wrap">
                <input type="text" class="inp-unit form-control" value="${w2Escape(unit)}" placeholder="单位" style="padding:4px; font-size:0.8rem; ${isVoid ? 'text-decoration:line-through; color:#6c757d;' : ''}" onfocus="this.select(); openUnitMenu(this)" onclick="openUnitMenu(this)" oninput="markArcDirty(); renderUnitMenuItems(this, this.nextElementSibling)" onblur="closeUnitMenuDelay(this)" ${isVoid ? 'disabled' : ''}>
                <div class="unit-dropdown-menu hide"></div>
            </div>
        </td>
        <td><input type="number" step="0.01" class="inp-price" value="${price}" oninput="markArcDirty(); recalcArcRow(this)" ${isVoid ? 'disabled' : ''}></td>
        <td><input type="number" step="0.01" class="inp-amount" value="${amount}" oninput="markArcDirty(); recalcArcTotalSum()" ${isVoid ? 'disabled' : ''}></td>
        <td>
            <select class="inp-dept form-control" title="本行归属部门" style="width:100%; padding:4px; font-size:0.8rem;">
                ${deptSelectOptionsHtml(item.cost_center_id)}
            </select>
        </td>
        <td style="text-align:center; white-space:nowrap;">
            <button class="btn btn-danger" style="padding:2px 6px; font-size:0.75rem;" onclick="removeArcRow(this)">删除</button>
        </td>
        <td style="text-align:center; white-space:nowrap;">
            <button type="button" class="btn btn-secondary" style="padding:3px 8px; font-size:0.78rem;" onclick="openRowFeedbackModal(this)">反馈</button>
            <span class="feedback-badge badge" style="font-size:0.68rem; display:none;"></span>
            <div class="feedback-cell" data-row-index="${arcRowIdx}" style="display:none;">
                <div class="feedback-actions">
                    <button type="button" class="btn-like" data-like="1" onclick="handleArcFeedback(this, 1)">点赞</button>
                    <button type="button" class="btn-dislike" data-like="-1" onclick="handleArcFeedback(this, -1)">点踩</button>
                </div>
                <textarea class="feedback-comment" placeholder="反馈原因（选填）" rows="2" maxlength="2000" oninput="onFeedbackCommentInput(this)"></textarea>
                <span class="feedback-charcount" style="font-size:0.70rem; color:var(--text-muted); align-self:flex-end;"></span>
                <button type="button" class="btn btn-secondary feedback-submit" onclick="submitArcFeedback(this)">提交反馈</button>
                <span class="feedback-status" style="font-size:0.72rem; color:var(--text-muted);"></span>
            </div>
        </td>
    `;
    tr.dataset.isVoid = isVoid ? '1' : '0';
    // P11：与复核台同口径挂行 dataset（归档保存不采集该列，仅作只读读数与核验依据）
    tr.dataset.confidence = (itemConfRaw !== null) ? String(itemConfRaw) : '';
    tbody.appendChild(tr);

    // Wave 2（D44）：归档行内任何手动改动（含部门下拉）→ data-manual + 未保存脏标记；
    // "应用到全部明细"跳过已手动改过的行（04 章三）
    tr.addEventListener('input', () => { tr.dataset.manual = '1'; markArcDirty(); });
    tr.addEventListener('change', () => { tr.dataset.manual = '1'; markArcDirty(); });
}

function addArcEmptyRow() {
    appendArcTableRow({ raw_name: '', quantity: 1.0, raw_unit: 'kg', unit_price: 0.0, amount: 0.0 });
    const container = document.querySelector('#archiveDetailModal .table-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
}

function removeArcRow(btn) {
    btn.closest('tr').remove();
    markArcDirty();
    recalcArcTotalSum();
}

function recalcArcRow(inputElem) {
    const tr = inputElem.closest('tr');
    const qty = parseFloat(tr.querySelector('.inp-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.inp-price').value) || 0;
    // 与算术门禁同口径：实收>0 时按实收计金额
    const actualInput = tr.querySelector('.inp-actual-qty');
    const actual = actualInput ? parseFloat(actualInput.value) : NaN;
    const effQty = !isNaN(actual) && actual > 0 ? actual : qty;

    const amtInput = tr.querySelector('.inp-amount');
    amtInput.value = (effQty * price).toFixed(2);

    recalcArcTotalSum();
}

function recalcArcTotalSum() {
    const tbody = document.getElementById('arcTableBody');
    const rows = tbody.querySelectorAll('tr');
    let sum = 0.0;
    rows.forEach(tr => {
        // D-P1-4：划线作废行不计入总额（与后端算术门禁口径一致）
        if (tr.dataset && tr.dataset.isVoid === '1') return;
        const amt = parseFloat(tr.querySelector('.inp-amount').value) || 0;
        sum += amt;
    });
    document.getElementById('arcTotal').value = sum.toFixed(2);
}

// Q32: 归档弹窗保存互斥旗标——保存中禁止再次触发（防双击重复提交）
let isSavingArchive = false;

async function submitSaveArchiveEdited() {
    if (isSavingArchive) return;   // Q32: 保存进行中，忽略重复点击

    const supplierName = document.getElementById('arcSupplier').value.trim();
    const dateStr = document.getElementById('arcDate').value.trim();
    const sheetName = document.getElementById('arcSheet').value.trim();
    const totalAmt = parseFloat(document.getElementById('arcTotal').value) || 0.00;

    if (!supplierName || supplierName === '通用供应商') {
        showToast('请填写供应商名称', 'error');
        return;
    }
    if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        showToast('请选择开单日期', 'error');
        return;
    }
    const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
    const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
    const dt = new Date(y, mo - 1, d);
    if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
        showToast('开单日期不正确，请选择真实存在的日历日期', 'error');
        return;
    }
    if (isNaN(totalAmt) || totalAmt <= 0) {
        showToast('单据总金额必须大于 0', 'error');
        return;
    }

    const tbody = document.getElementById('arcTableBody');
    const rows = tbody.querySelectorAll('tr');
    registerNewUnitsFromRows(rows);

    const items = [];
    let rowErr = null;
    let rowIndex = 0;
    rows.forEach(tr => {
        rowIndex++;
        const nameVal = tr.querySelector('.inp-name').value.trim();
        const qtyVal = parseFloat(tr.querySelector('.inp-qty').value);
        const priceVal = parseFloat(tr.querySelector('.inp-price').value);
        if (!nameVal && rows.length === 1) {
            rowErr = `第 ${rowIndex} 行的品名不能为空，请填写或删除该行`;
            return;
        }
        if (nameVal) {
            if (isNaN(qtyVal) || qtyVal <= 0 || isNaN(priceVal) || priceVal < 0) {
                if (!rowErr) rowErr = `第 ${rowIndex} 行的单价或数量不是有效数字，请重新输入`;
            }
            const deptInput = tr.querySelector('.inp-dept');
            const actualInput = tr.querySelector('.inp-actual-qty');
            const actualRaw = actualInput ? actualInput.value.trim() : '';
            items.push({
                name: nameVal,
                quantity: isNaN(qtyVal) ? 1.0 : qtyVal,
                unit: tr.querySelector('.inp-unit').value.trim(),
                unit_price: isNaN(priceVal) ? 0.00 : priceVal,
                amount: parseFloat(tr.querySelector('.inp-amount').value) || 0.00,
                actual_qty: actualRaw !== '' && !isNaN(parseFloat(actualRaw)) ? parseFloat(actualRaw) : null,
                // Wave 2（D44）：明细行成本中心——行内下拉实际显示值（未打部门 → null）
                cost_center_id: (deptInput && deptInput.value) ? Number(deptInput.value) : null,
                // D-P1-4：划线作废状态随行保存
                is_void: !!(tr.dataset && tr.dataset.isVoid === '1')
            });
        }
    });

    if (rowErr) {
        showToast(rowErr, 'error');
        return;
    }

    if (items.length === 0) {
        showToast('请至少输入一行消费明细', 'warning');
        return;
    }

    // 变动提交二次确认（避免误触保存）
    if (hasUnsavedArcChanges) {
        const confirmSave = (typeof uiConfirm === 'function')
            ? await uiConfirm({
                title: '保存修改',
                message: '这张单有还没保存的修改。确定写入并留下修改记录吗？',
                confirmText: '保存',
                cancelText: '先留着',
            })
            : false;
        if (!confirmSave) return;
    }

    const arcSettlementSel = document.getElementById('arcSettlementType');
    const settlementType = arcSettlementSel ? (arcSettlementSel.value || null) : null;
    // M3/D4 结算方式必填化：归档保存同样要求已标记结算方式，未标记 → 阻断
    if (!requireSettlementMarked(settlementType)) {
        return;
    }

    const src = currentArchiveDetailData || {};
    // Wave 2（D44）：归档单据级部门（一键填充目标）；未打部门 → null 可空保存
    const arcDeptSel = document.getElementById('arcDepartmentId');
    const departmentId = (arcDeptSel && arcDeptSel.value) ? Number(arcDeptSel.value) : null;
    // F-P1-3 多币种
    const arcCurSel = document.getElementById('arcCurrency');

    const data = {
        supplier_name: supplierName || '通用供应商',
        date: dateStr,
        sheet_name: sheetName,
        total_amount: totalAmt,
        items: items,
        settlement_type: settlementType,
        department_id: departmentId,
        currency: (arcCurSel && arcCurSel.value ? arcCurSel.value : (src.currency || 'HKD'))
    };
    // U-05：付款标记改为枚举下拉，保存以用户选择为准；弹窗缺失时回落 AI 原值
    const arcMarkSel = document.getElementById('arcPaymentMark');
    if (arcMarkSel && arcMarkSel.value) data.payment_mark = arcMarkSel.value;
    else if (src.payment_mark != null) data.payment_mark = src.payment_mark;
    if (src.doc_form != null) data.doc_form = src.doc_form;
    if (src.layout_type != null) data.layout_type = src.layout_type;
    // D17: 乐观锁版本号（加载详情时记录）
    if (src.version != null) data.version = src.version;

    const payload = buildSavePayloadFromData(data, currentArchiveReceiptId);
    payload.source = 'manual';

    isSavingArchive = true;
    const arcSaveBtn = document.getElementById('btnSaveArchive');
    if (arcSaveBtn) arcSaveBtn.disabled = true;

    apiFetch('/api/save_edited', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        // D17: 版本冲突——单据已被他人修改；留副本 + 显式确认后重载（P3/R5）
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            handleVersionConflictReload(currentArchiveReceiptId, data,
                () => loadReceiptDetail(currentArchiveReceiptId));
            return;
        }
        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，已重新加载最新内容', 'warning');
            if (currentArchiveReceiptId) loadReceiptDetail(currentArchiveReceiptId);
            return;
        }
        // E-P1-4：403 人话统一（同步表单提交路径，与 fetch 包装双保险，去重由 toast key 保证）
        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('保存失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return;
        }
        closeArchiveModal(true); // 强制关闭弹窗并重置状态

        loadReceiptsHistory();
        loadInventoryData();
        loadSuppliersData();
        loadFinancePanel();     // Wave 1：save_edited 快照钩子可能写预期付款日 → 提醒联动
    })
    .catch(err => {
        console.error('保存归档单据请求失败:', err);
        showToast('保存请求失败，请检查网络后重试', 'error');
    })
    .finally(() => {
        // Q32: 无论成功/失败/冲突，恢复保存按钮可再次触发
        isSavingArchive = false;
        const arcSaveBtn2 = document.getElementById('btnSaveArchive');
        if (arcSaveBtn2) arcSaveBtn2.disabled = false;
    });
}

function toggleArchiveZoomMode() {
    if (arcActiveTool === 'zoom') {
        arcActiveTool = null;
        arcZoom = 1.0;
    } else {
        arcActiveTool = 'zoom';
        arcZoom = 2.0;
    }
    applyArchiveImgTransform();
}

function rotateArchiveImg() {
    // 归档弹窗为只读查看，影响较小，保持纯 CSS 旋转即可，不强制烤入文件
    // 原因：归档图已落库且为服务端 JPEG，无需再上传；CSS 足够用于查看校对
    arcRotation = (arcRotation + 90) % 360;
    applyArchiveImgTransform();
}

function resetArchiveImgTransform() {
    arcZoom = 1.0;
    arcRotation = 0;
    arcPanX = 0;
    arcPanY = 0;
    arcActiveTool = null;
    applyArchiveImgTransform();
}

function applyArchiveImgTransform() {
    const img = document.getElementById('archivePreviewImg');
    if (img) {
        img.style.transform = `translate(${arcPanX}px, ${arcPanY}px) scale(${arcZoom}) rotate(${arcRotation}deg)`;
    }
}


// Q35：归档 error 行入口（window 导出随域迁入；原在 main.js 导出簇）
window.retryReceiptFromArchive = retryReceiptFromArchive;
window.convertReceiptFromArchive = convertReceiptFromArchive;
// ---- F4 S4-11 随域并入 archive_list.js：F-P1-5 成本分摊（归档弹窗） ----
// =============================================================
// F-P1-5 成本分摊（归档弹窗）：整单金额按部门比例分摊到明细行部门
// =====================================================================
let costShareRows = [];

function openCostShareModal() {
    const totalAmt = parseFloat((document.getElementById('arcTotal') || {}).value) || 0;
    if (totalAmt <= 0) {
        showToast('本单总额为 0，无可分摊成本', 'warning');
        return;
    }
    costShareRows = [];
    // 默认两行：当前单据级部门 + 未分配
    const deptSel = document.getElementById('arcDepartmentId');
    const curDept = deptSel && deptSel.value ? Number(deptSel.value) : '';
    addCostShareRow(curDept, 70);
    addCostShareRow('', 30);
    renderCostShareRows();
    recalcCostShareSummary();
    const modal = document.getElementById('costShareModal');
    if (modal) modal.classList.remove('hide');
}

function addCostShareRow(deptId, percent) {
    costShareRows.push({ dept_id: deptId != null ? String(deptId) : '', percent: (percent != null ? Number(percent) : 0) });
}

function removeCostShareRow(idx) {
    costShareRows.splice(Number(idx), 1);
    renderCostShareRows();
    recalcCostShareSummary();
}

function equalizeCostShare() {
    const n = costShareRows.length;
    if (n === 0) return;
    const base = Math.floor(100 / n);
    let remainder = 100 - base * n;
    costShareRows.forEach(r => {
        r.percent = base + (remainder > 0 ? 1 : 0);
        if (remainder > 0) remainder--;
    });
    renderCostShareRows();
    recalcCostShareSummary();
}

function onCostShareInput(idx, field, value) {
    const row = costShareRows[Number(idx)];
    if (!row) return;
    if (field === 'percent') row.percent = parseFloat(value) || 0;
    else row.dept_id = String(value || '');
    recalcCostShareSummary();
}

function renderCostShareRows() {
    const body = document.getElementById('costShareBody');
    if (!body) return;
    let html = '';
    costShareRows.forEach((r, i) => {
        html += '<div style="display:flex; gap:8px; align-items:center; margin-bottom:8px;">'
            + '<select class="form-control" style="flex:1;" onchange="onCostShareInput(' + i + ', \'dept\', this.value)">'
            + deptSelectOptionsHtml(r.dept_id)
            + '</select>'
            + '<input type="number" class="form-control" style="width:90px;" min="0" max="100" step="0.1" value="' + r.percent + '" oninput="onCostShareInput(' + i + ', \'percent\', this.value)">'
            + '<span style="font-size:0.8rem; color:var(--text-muted);">%</span>'
            + '<button class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem;" onclick="removeCostShareRow(' + i + ')">删除</button>'
            + '</div>';
    });
    body.innerHTML = html || '<div style="color:var(--text-muted); font-size:0.82rem;">暂无分摊行，请点击「增加部门」</div>';
}

function recalcCostShareSummary() {
    const totalAmt = parseFloat((document.getElementById('arcTotal') || {}).value) || 0;
    const sum = costShareRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
    const el = document.getElementById('costShareSummary');
    if (!el) return;
    const parts = costShareRows.map(r => {
        const amt = totalAmt * (Number(r.percent) || 0) / 100;
        const sel = document.querySelector('#costShareBody select');
        void sel;
        return (r.dept_id ? ('部门#' + r.dept_id) : '未分配') + ' ' + r.percent + '% = ' + currencySymbol(currentCurrencyCode()) + amt.toFixed(2);
    });
    el.textContent = '合计 ' + sum.toFixed(1) + '%' + (Math.abs(sum - 100) < 0.05 ? '' : '（警告：比例合计应为 100%）') + (parts.length ? ' ｜ ' + parts.join('，') : '');
}

function applyCostShare() {
    const sum = costShareRows.reduce((acc, r) => acc + (Number(r.percent) || 0), 0);
    if (costShareRows.length === 0) { showToast('请先添加分摊行', 'warning'); return; }
    if (Math.abs(sum - 100) > 0.05) { showToast('分摊比例合计须为 100%，当前 ' + sum.toFixed(1) + '%', 'error'); return; }

    const tbody = document.getElementById('arcTableBody');
    const rows = Array.from(tbody.querySelectorAll('tr')).filter(tr =>
        !(tr.dataset && tr.dataset.isVoid === '1'));
    if (rows.length === 0) { showToast('无有效明细行可分摊', 'warning'); return; }

    // 按比例把行分配到各部门（大百分比优先占满），保证每行都有归属且不重复
    const assignments = [];
    costShareRows.forEach(r => {
        const count = Math.round(rows.length * (Number(r.percent) || 0) / 100);
        for (let k = 0; k < count; k++) assignments.push(r.dept_id);
    });
    while (assignments.length < rows.length) assignments.push(costShareRows[0] ? costShareRows[0].dept_id : '');
    rows.forEach((tr, i) => {
        const sel = tr.querySelector('.inp-dept');
        if (sel) sel.value = assignments[i] || '';
    });
    markArcDirty();
    closeModalById('costShareModal');
    showToast('已按比例将 ' + rows.length + ' 行明细分摊至各部门，保存后生效', 'success', TOAST_DURATION.long);
}


(function bindArchivePaginationControls() {
    function bind() {
        const prev = document.getElementById('archivePagePrev');
        const next = document.getElementById('archivePageNext');
        if (prev && !prev.dataset.saasScaleBound) {
            prev.dataset.saasScaleBound = '1';
            prev.addEventListener('click', () => setArchivePage(archiveCurrentPage - 1));
        }
        if (next && !next.dataset.saasScaleBound) {
            next.dataset.saasScaleBound = '1';
            next.addEventListener('click', () => setArchivePage(archiveCurrentPage + 1));
        }
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', bind);
    else bind();
})();
