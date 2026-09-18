// -*- coding: utf-8 -*-
/**
 * 统一报表与数据导出中心前端控制器 (Export & Reporting Center)
 * 支持库存、餐品、供应商、部门四大模块 14 套标准表格的在线预览、参数定制与 CSV 流式下载
 */

const exportCenterState = {
    reports: [],
    currentCategory: 'inventory',
    currentReportId: 'inventory_stocktake',
    previewData: null,
    isLoading: false,
    suppliers: [],
    departments: [],
    categories: ['肉类', '海鲜', '蔬菜', '主食粮油', '调味品', '烘焙', '饮品', '清洁', '其他']
};
window.exportCenterState = exportCenterState;

// 页面加载或切换至导出中心时初始化
function initExportCenter() {
    loadExportMetadata();
    loadExportAuxiliaryData();
}

// 加载报表元数据
function loadExportMetadata() {
    apiFetch('/api/export/reports')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                exportCenterState.reports = ret.data;
                renderExportCategoryTabs();
                renderExportReportList();
                renderExportFilters();
                refreshExportPreview();
            }
        })
        .catch(err => {
            console.error('加载导出中心报表失败:', err);
            showToast('加载报表定义失败，请重试', 'error');
        });
}

// 加载供应商、部门等过滤辅助数据源
function loadExportAuxiliaryData() {
    // 部门
    apiFetch('/api/departments')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                exportCenterState.departments = ret.data;
                updateExportFilterDropdowns();
            }
        }).catch(() => {});

    // 供应商
    apiFetch('/api/suppliers')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                exportCenterState.suppliers = ret.data;
                updateExportFilterDropdowns();
            }
        }).catch(() => {});
}

// 渲染分类 Tab
function renderExportCategoryTabs() {
    const container = document.getElementById('exportCategoryTabs');
    if (!container) return;

    const cats = [
        { id: 'inventory', name: '实时库存与价格', icon: '' },
        { id: 'dishes', name: '餐品与消耗', icon: '' },
        { id: 'suppliers', name: '供应商与归档', icon: '' },
        { id: 'department', name: '部门花销报表', icon: '' },
    ];

    let html = '';
    cats.forEach(c => {
        const count = exportCenterState.reports.filter(r => r.category === c.id).length;
        const active = exportCenterState.currentCategory === c.id ? 'active' : '';
        html += `
            <button type="button" class="export-tab-btn ${active}" onclick="selectExportCategory('${c.id}')">
                ${c.icon ? `<span style="margin-right:4px;">${c.icon}</span>` : ''}
                <span>${c.name}</span>
                <span class="export-count-badge">${count}</span>
            </button>
        `;
    });
    container.innerHTML = html;
}

// 切换报表分类
function selectExportCategory(catId, targetReportId = null) {
    exportCenterState.currentCategory = catId;
    renderExportCategoryTabs();
    renderExportReportList();

    if (targetReportId) {
        selectExportReport(targetReportId);
        return;
    }

    // 如果当前选中的报表不在当前分类下，自动切换到该分类下的第一个报表
    const filtered = catId === 'all' 
        ? exportCenterState.reports 
        : exportCenterState.reports.filter(r => r.category === catId);
    if (filtered.length > 0 && !filtered.some(r => r.id === exportCenterState.currentReportId)) {
        selectExportReport(filtered[0].id);
    }
}

// 渲染左侧报表列表卡片
function renderExportReportList() {
    const container = document.getElementById('exportReportList');
    if (!container) return;

    const filtered = exportCenterState.currentCategory === 'all'
        ? exportCenterState.reports
        : exportCenterState.reports.filter(r => r.category === exportCenterState.currentCategory);

    let html = '';
    filtered.forEach(rep => {
        const isSelected = rep.id === exportCenterState.currentReportId;
        const catBadgeClass = {
            'inventory': 'badge-primary',
            'dishes': 'badge-success',
            'suppliers': 'badge-warning',
            'department': 'badge-neutral'
        }[rep.category] || 'badge-secondary';

        html += `
            <div class="export-report-card ${isSelected ? 'selected' : ''}" onclick="selectExportReport('${rep.id}')">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
                    <span class="badge ${catBadgeClass}" style="font-size:0.72rem; padding:3px 10px;">${rep.category_name}</span>
                    ${isSelected ? '<span class="badge badge-primary" style="font-size:0.72rem; padding:3px 10px;">正在预览</span>' : ''}
                </div>
                <div class="export-report-title">${rep.title}</div>
                <div class="export-report-desc">${rep.description}</div>
            </div>
        `;
    });
    container.innerHTML = html;
}

// 选中某个具体报表
function selectExportReport(reportId) {
    exportCenterState.currentReportId = reportId;
    renderExportReportList();
    renderExportFilters();
    refreshExportPreview();
}

// 快捷场景预置包跳转
function applyExportPreset(presetType) {
    if (presetType === 'monthly_close') {
        selectExportCategory('suppliers', 'receipts_itemized_ledger');
        setExportQuickRange('month');
        showToast('已载入【月末财务审计归档包】推荐配置', 'info');
    } else if (presetType === 'stocktake') {
        selectExportCategory('inventory', 'inventory_stocktake');
        showToast('已载入【冷库/干货实地盘点清册】标准底单', 'info');
    } else if (presetType === 'dish_margin') {
        selectExportCategory('dishes', 'dish_margin_variance');
        setExportQuickRange('month');
        showToast('已载入【餐品理论 vs 真实毛利诊断】报表', 'info');
    }
}

// 日期防呆：起始晚于结束时自动调换并人话提示
function checkAndAutoSwapDateRange() {
    const sInput = document.getElementById('expStartDate');
    const eInput = document.getElementById('expEndDate');
    if (!sInput || !eInput || !sInput.value || !eInput.value) return false;
    if (sInput.value > eInput.value) {
        const temp = sInput.value;
        sInput.value = eInput.value;
        eInput.value = temp;
        showToast('检测到开单起始日期晚于结束日期，系统已自动帮您理顺顺序', 'info');
        return true;
    }
    return false;
}

function handleExportDateChange() {
    checkAndAutoSwapDateRange();
    refreshExportPreview();
}

// 渲染右侧筛选工具栏（根据报表元数据需求动态显示）
function renderExportFilters() {
    const report = exportCenterState.reports.find(r => r.id === exportCenterState.currentReportId);
    if (!report) return;

    const filters = report.filters || [];
    const container = document.getElementById('exportDynamicFilters');
    if (!container) return;

    let html = '';

    // 1. 日期区间
    if (filters.includes('date_range')) {
        html += `
            <div class="export-filter-item">
                <label>业务时间跨度</label>
                <div style="display:flex; gap:4px; align-items:center;">
                    <button type="button" class="export-chip" data-range="" onclick="setExportQuickRange('')">全部</button>
                    <button type="button" class="export-chip" data-range="today" onclick="setExportQuickRange('today')">今天</button>
                    <button type="button" class="export-chip" data-range="week" onclick="setExportQuickRange('week')">近7天</button>
                    <button type="button" class="export-chip active" data-range="month" onclick="setExportQuickRange('month')">本月</button>
                    <button type="button" class="export-chip" data-range="last_month" onclick="setExportQuickRange('last_month')">上月</button>
                </div>
                <div style="display:flex; gap:6px; align-items:center; margin-top:4px;">
                    <input type="date" id="expStartDate" class="form-control export-control-input" onchange="handleExportDateChange()" onblur="handleExportDateChange()">
                    <span style="color:var(--text-muted); font-size:0.85rem; font-weight:500;">至</span>
                    <input type="date" id="expEndDate" class="form-control export-control-input" onchange="handleExportDateChange()" onblur="handleExportDateChange()">
                </div>
            </div>
        `;
    }

    // 2. 食材分类
    if (filters.includes('category')) {
        html += `
            <div class="export-filter-item">
                <label>食材分类</label>
                <select id="expCategory" class="form-control export-control-input" style="width:130px;" onchange="refreshExportPreview()">
                    <option value="">全部分类</option>
                    ${exportCenterState.categories.map(c => `<option value="${c}">${c}</option>`).join('')}
                </select>
            </div>
        `;
    }

    // 3. 库存状态
    if (filters.includes('stock_filter')) {
        html += `
            <div class="export-filter-item">
                <label>库存预警筛选</label>
                <select id="expStockFilter" class="form-control export-control-input" style="width:130px;" onchange="refreshExportPreview()">
                    <option value="all">全部库存状态</option>
                    <option value="low">仅低库存预警</option>
                </select>
            </div>
        `;
    }

    // 4. 出入库流水类型
    if (filters.includes('log_kind')) {
        html += `
            <div class="export-filter-item">
                <label>流水变动类型</label>
                <select id="expLogKind" class="form-control export-control-input" style="width:140px;" onchange="refreshExportPreview()">
                    <option value="">全部类型流水</option>
                    <option value="in">仅采购入库</option>
                    <option value="consume">仅餐品消耗扣减</option>
                    <option value="waste">仅后厨报损</option>
                    <option value="stocktake">仅盘点调整</option>
                </select>
            </div>
        `;
    }

    // 5. 供应商下拉
    if (filters.includes('supplier_id')) {
        html += `
            <div class="export-filter-item">
                <label>指定往来供应商</label>
                <select id="expSupplier" class="form-control export-control-input" style="min-width:170px;" onchange="refreshExportPreview()">
                    <option value="">全部供应商</option>
                    ${exportCenterState.suppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('')}
                </select>
            </div>
        `;
    }

    // 6. 部门下拉
    if (filters.includes('department_id')) {
        html += `
            <div class="export-filter-item">
                <label>归属成本部门</label>
                <select id="expDepartment" class="form-control export-control-input" style="width:140px;" onchange="refreshExportPreview()">
                    <option value="">全部部门</option>
                    ${exportCenterState.departments.map(d => `<option value="${d.id}">${d.name}</option>`).join('')}
                    <option value="0">未分配部门</option>
                </select>
            </div>
        `;
    }

    // 7. 单据入账状态
    if (filters.includes('receipt_status')) {
        html += `
            <div class="export-filter-item">
                <label>单据审批状态</label>
                <select id="expReceiptStatus" class="form-control export-control-input" style="width:140px;" onchange="refreshExportPreview()">
                    <option value="">全部单据状态</option>
                    <option value="approved">仅已入账 (老板审核通过)</option>
                    <option value="parsed">仅待核对 (AI解析完成)</option>
                    <option value="edited">仅已修改 (店员保存)</option>
                    <option value="flagged">仅有问题单据</option>
                </select>
            </div>
        `;
    }

    // 8. 餐品状态
    if (filters.includes('dish_status')) {
        html += `
            <div class="export-filter-item">
                <label>餐品状态</label>
                <select id="expDishStatus" class="form-control export-control-input" style="width:120px;" onchange="refreshExportPreview()">
                    <option value="active" selected>仅在售餐品</option>
                    <option value="all">全部餐品状态</option>
                    <option value="inactive">仅已下架</option>
                </select>
            </div>
        `;
    }

    container.innerHTML = html;

    // 9. 动态渲染当前报表真正支持的附加能力（草稿/脱敏），无适用选项时整行自动隐藏
    const extraContainer = document.getElementById('exportExtraOptions');
    if (extraContainer) {
        let extraHtml = '';
        if (report.supports_drafts) {
            extraHtml += `
                <label style="display:flex; align-items:center; gap:6px; font-size:0.82rem; cursor:pointer; color:var(--text-muted); user-select:none;">
                    <input type="checkbox" id="expIncludeNonApproved" onchange="refreshExportPreview()">
                    <span>包含未审核/草稿单据</span>
                </label>
            `;
        }
        if (report.supports_desensitization) {
            extraHtml += `
                <label style="display:flex; align-items:center; gap:6px; font-size:0.82rem; cursor:pointer; color:var(--text-muted); user-select:none;">
                    <input type="checkbox" id="expDesensitized" onchange="refreshExportPreview()">
                    <span>数据脱敏保护 (隐藏供应商电话与全称)</span>
                </label>
            `;
        }
        if (extraHtml) {
            extraContainer.innerHTML = extraHtml;
            extraContainer.style.display = 'flex';
            extraContainer.classList.remove('hide');
        } else {
            extraContainer.innerHTML = '';
            extraContainer.style.display = 'none';
            extraContainer.classList.add('hide');
        }
    }

    // 默认如果显示了日期区间，设为本月
    if (filters.includes('date_range')) {
        setExportQuickRange('month', false);
    }
}

// 更新下拉框数据（异步补充）
function updateExportFilterDropdowns() {
    const supSel = document.getElementById('expSupplier');
    if (supSel) {
        const val = supSel.value;
        supSel.innerHTML = '<option value="">全部供应商</option>' +
            exportCenterState.suppliers.map(s => `<option value="${s.id}">${s.name}</option>`).join('');
        supSel.value = val;
    }
    const deptSel = document.getElementById('expDepartment');
    if (deptSel) {
        const val = deptSel.value;
        deptSel.innerHTML = '<option value="">全部部门</option>' +
            exportCenterState.departments.map(d => `<option value="${d.id}">${d.name}</option>`).join('') +
            '<option value="0">未分配部门</option>';
        deptSel.value = val;
    }
}

// 设置快捷时间跨度并联动输入框
function setExportQuickRange(range, triggerRefresh = true) {
    const chips = document.querySelectorAll('#exportDynamicFilters .export-chip');
    chips.forEach(c => {
        if (c.getAttribute('data-range') === range) c.classList.add('active');
        else c.classList.remove('active');
    });

    const sInput = document.getElementById('expStartDate');
    const eInput = document.getElementById('expEndDate');
    if (!sInput || !eInput) return;

    const now = new Date();
    const fmt = (d) => d.toISOString().slice(0, 10);

    if (range === 'today') {
        sInput.value = fmt(now);
        eInput.value = fmt(now);
    } else if (range === 'week') {
        const past = new Date(now.getTime() - 6 * 24 * 3600 * 1000);
        sInput.value = fmt(past);
        eInput.value = fmt(now);
    } else if (range === 'month') {
        const y = now.getFullYear(), m = now.getMonth();
        sInput.value = fmt(new Date(y, m, 1));
        eInput.value = fmt(new Date(y, m + 1, 0));
    } else if (range === 'last_month') {
        const y = now.getFullYear(), m = now.getMonth() - 1;
        sInput.value = fmt(new Date(y, m, 1));
        eInput.value = fmt(new Date(y, m + 1, 0));
    } else {
        sInput.value = '';
        eInput.value = '';
    }

    if (triggerRefresh) refreshExportPreview();
}

// 采集当前表单过滤参数（严格基于报表支持的白名单）
function collectExportParams() {
    const report = exportCenterState.reports.find(r => r.id === exportCenterState.currentReportId);
    const p = {};
    if (!report) return p;

    const filters = report.filters || [];

    if (filters.includes('date_range')) {
        const sInput = document.getElementById('expStartDate');
        const eInput = document.getElementById('expEndDate');
        if (sInput && sInput.value) p.start_date = sInput.value;
        if (eInput && eInput.value) p.end_date = eInput.value;
    }

    if (filters.includes('category')) {
        const cat = document.getElementById('expCategory');
        if (cat && cat.value) p.category = cat.value;
    }

    if (filters.includes('stock_filter')) {
        const stFlt = document.getElementById('expStockFilter');
        if (stFlt && stFlt.value) p.stock_filter = stFlt.value;
    }

    if (filters.includes('log_kind')) {
        const logK = document.getElementById('expLogKind');
        if (logK && logK.value) p.log_kind = logK.value;
    }

    if (filters.includes('supplier_id')) {
        const sup = document.getElementById('expSupplier');
        if (sup && sup.value) p.supplier_id = sup.value;
    }

    if (filters.includes('department_id')) {
        const dept = document.getElementById('expDepartment');
        if (dept && dept.value) p.department_id = dept.value;
    }

    if (filters.includes('receipt_status')) {
        const rst = document.getElementById('expReceiptStatus');
        if (rst && rst.value) p.receipt_status = rst.value;
    }

    if (filters.includes('dish_status')) {
        const dishSt = document.getElementById('expDishStatus');
        if (dishSt && dishSt.value) p.dish_status = dishSt.value;
    }

    if (report.supports_drafts) {
        const incNon = document.getElementById('expIncludeNonApproved');
        if (incNon) p.include_non_approved = incNon.checked ? 1 : 0;
    }

    return p;
}

let exportPreviewSeq = 0;

// 刷新实时在线预览
function refreshExportPreview() {
    checkAndAutoSwapDateRange();
    exportCenterState.isLoading = true;
    const currentSeq = ++exportPreviewSeq;

    const reportId = exportCenterState.currentReportId;
    const report = exportCenterState.reports.find(r => r.id === reportId);
    const params = collectExportParams();
    const desens = (report && report.supports_desensitization)
        ? !!document.getElementById('expDesensitized')?.checked
        : false;

    const tableContainer = document.getElementById('exportPreviewTableWrapper');
    if (tableContainer) {
        tableContainer.style.opacity = '0.5';
    }
    const summaryBox = document.getElementById('exportPreviewSummary');
    if (summaryBox) {
        summaryBox.innerHTML = '<span style="color:var(--text-muted);">正在生成实时预览数据…</span>';
    }

    return apiFetch('/api/export/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            report_id: reportId,
            params: params,
            desensitized: desens
        })
    })
    .then(async res => {
        if (currentSeq !== exportPreviewSeq) return null;
        if (!res.ok) {
            let errMsg = '生成报表预览失败 (HTTP ' + res.status + ')';
            try {
                const errJson = await res.json();
                if (errJson && errJson.detail) errMsg = errJson.detail;
            } catch (e) {}
            throw new Error(errMsg);
        }
        return res.json();
    })
    .then(ret => {
        if (!ret) return;
        if (currentSeq !== exportPreviewSeq) return;
        if (ret.status === 'success' && ret.data) {
            exportCenterState.previewData = ret.data;
            renderExportPreviewUI(ret.data);
        } else {
            throw new Error(ret.msg || '预览数据获取失败');
        }
    })
    .catch(err => {
        if (currentSeq !== exportPreviewSeq) return;
        console.error('预览获取失败:', err);
        showToast(err.message, 'error');
        if (summaryBox) {
            summaryBox.innerHTML = '<span style="color:var(--danger); font-size:0.88rem;">[提示] ' + err.message + '</span>';
        }
    })
    .finally(() => {
        if (currentSeq === exportPreviewSeq) {
            exportCenterState.isLoading = false;
            if (tableContainer) tableContainer.style.opacity = '1';
        }
    });
}

// 渲染预览表格及汇总胶囊
function renderExportPreviewUI(data) {
    const titleEl = document.getElementById('exportActiveReportTitle');
    if (titleEl) titleEl.innerText = data.title || '报表实时数据预览';

    const summaryBox = document.getElementById('exportPreviewSummary');
    if (summaryBox) {
        let sumHtml = `<span class="badge badge-primary" style="font-size:0.85rem; padding:4px 8px;">共 ${data.total_rows} 条记录</span>`;
        if (data.summary && Object.keys(data.summary).length > 0) {
            for (const [k, v] of Object.entries(data.summary)) {
                sumHtml += `<span class="export-summary-pill"><strong>${k}:</strong> ${v}</span>`;
            }
        }
        summaryBox.innerHTML = sumHtml;
    }

    const thead = document.getElementById('exportPreviewThead');
    const tbody = document.getElementById('exportPreviewTbody');
    if (!thead || !tbody) return;

    // 统一列对齐判定逻辑
    const getColAlignClass = (headerText) => {
        if (!headerText) return '';
        const h = String(headerText);
        if (h.includes('金额') || h.includes('价') || h.includes('库存') || h.includes('数量') || 
            h.includes('率') || h.includes('总额') || h.includes('差异') || h.includes('幅度') || 
            h.includes('比') || h.includes('成本') || h.includes('毛利') || h.includes('份数') || 
            h.includes('货值') || h.includes('警戒线') || h.includes('实盘') || h.includes('均价')) {
            return 'col-right';
        }
        if (h.includes('日期') || h.includes('类型') || h.includes('单位') || h.includes('状态') || 
            h.includes('签字') || h.includes('分类') || h.includes('模式') || h.includes('级别')) {
            return 'col-center';
        }
        return '';
    };

    // 表头
    let headHtml = '<tr>';
    (data.headers || []).forEach(h => {
        const alignClass = getColAlignClass(h);
        headHtml += `<th class="${alignClass}" style="white-space:nowrap;">${h}</th>`;
    });
    headHtml += '</tr>';
    thead.innerHTML = headHtml;

    // 行
    const rows = data.preview_rows || [];
    if (rows.length === 0) {
        // AC-6: 100% 人话直白展示空状态，杜绝任何技术黑话
        tbody.innerHTML = `<tr><td colspan="${(data.headers && data.headers.length) || 1}" class="col-center" style="padding:48px 16px; text-align:center; color:var(--text-muted); font-size:0.95rem;">在这个时间段内没有找到任何单据记录，请检查日期或切换分类</td></tr>`;
        return;
    }

    let bodyHtml = '';
    rows.forEach(r => {
        bodyHtml += '<tr>';
        r.forEach((cell, idx) => {
            const h = data.headers[idx] || '';
            const alignClass = getColAlignClass(h);
            
            // 特殊样式高亮（统一为胶囊徽标）
            let cellStyle = '';
            let cellContent = (cell === null || cell === undefined) ? '-' : String(cell);
            if (cellContent.includes('异常') || cellContent.includes('低库存') || cellContent.includes('承压') || cellContent.includes('跌价') || cellContent.includes('逾期') || cellContent.includes('预警') || cellContent.includes('异动')) {
                cellContent = `<span class="badge badge-danger">${cellContent}</span>`;
            } else if (cellContent.includes('已入账') || cellContent.includes('正常') || cellContent.includes('稳定') || cellContent.includes('下降') || cellContent.includes('已审批') || cellContent.includes('充足') || cellContent.includes('健康')) {
                cellContent = `<span class="badge badge-success">${cellContent}</span>`;
            }

            bodyHtml += `<td class="${alignClass}" style="white-space:nowrap; ${cellStyle}">${cellContent}</td>`;
        });
        bodyHtml += '</tr>';
    });
    tbody.innerHTML = bodyHtml;
}

// 执行标准 CSV 下载（带 UTF-8 BOM）
function executeExportDownload() {
    checkAndAutoSwapDateRange();

    // AC-6: 数据量为 0 时给予温和人话提示，避免生成空文件
    if (exportCenterState.previewData && exportCenterState.previewData.total_rows === 0) {
        showToast('当前筛选条件下没有数据，请重新选择日期或分类后再试', 'warning');
        return;
    }

    const reportId = exportCenterState.currentReportId;
    const report = exportCenterState.reports.find(r => r.id === reportId);
    const params = collectExportParams();
    const desens = (report && report.supports_desensitization)
        ? !!document.getElementById('expDesensitized')?.checked
        : false;

    showToast('正在生成并打包 CSV 报表…', 'info');

    apiFetch('/api/export/download', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            report_id: reportId,
            params: params,
            desensitized: desens
        })
    })
    .then(async res => {
        if (!res.ok) {
            let errMsg = '导出下载失败 (HTTP ' + res.status + ')';
            try {
                const errJson = await res.json();
                if (errJson && errJson.detail) errMsg = errJson.detail;
            } catch (e) {}
            throw new Error(errMsg);
        }
        
        // 从响应头解析文件名 (RFC 5987 标准)
        let filename = 'export_report.csv';
        const disposition = res.headers.get('content-disposition');
        if (disposition && disposition.includes('filename*=')) {
            const match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
            if (match && match[1]) filename = decodeURIComponent(match[1]);
        } else if (disposition && disposition.includes('filename=')) {
            const match = disposition.match(/filename="?([^";]+)"?/i);
            if (match && match[1]) filename = match[1];
        }

        return res.blob().then(blob => ({ blob, filename }));
    })
    .then(({ blob, filename }) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 2000);
        showToast('报表已成功下载！', 'success');
    })
    .catch(err => {
        console.error('下载导出失败:', err);
        showToast(err.message, 'error');
    });
}

// 打印当前预览报表（AC-9: 严格符合 A4 实地盘点标准，带空白手填列与三级下划线签名栏）
function printExportReport() {
    const title = document.getElementById('exportActiveReportTitle')?.innerText || '报表打印';
    const thead = document.getElementById('exportPreviewThead')?.innerHTML || '';
    const tbody = document.getElementById('exportPreviewTbody')?.innerHTML || '';
    const summary = document.getElementById('exportPreviewSummary')?.innerText || '';

    const printWin = window.open('', '_blank', 'width=960,height=720');
    if (!printWin) {
        showToast('弹出打印窗口被拦截，请在浏览器设置中允许弹窗后重试', 'warning');
        return;
    }

    printWin.document.write(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>${title}</title>
            <meta charset="utf-8">
            <style>
                @page {
                    size: A4 portrait;
                    margin: 12mm 10mm 15mm 10mm;
                }
                * { box-sizing: border-box; }
                body {
                    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif;
                    padding: 10px 15px;
                    color: #000;
                    background: #fff;
                    font-size: 10.5pt;
                }
                .print-header {
                    text-align: center;
                    margin-bottom: 12px;
                    border-bottom: 2px solid #000;
                    padding-bottom: 8px;
                }
                h2 {
                    margin: 0 0 6px 0;
                    font-size: 16pt;
                    letter-spacing: 1px;
                }
                .meta-bar {
                    display: flex;
                    justify-content: space-between;
                    font-size: 9pt;
                    color: #333;
                    margin-top: 4px;
                }
                table {
                    width: 100%;
                    table-layout: auto;
                    border-collapse: collapse;
                    margin-top: 10px;
                    font-size: 8.5pt;
                    page-break-inside: auto;
                }
                tr {
                    page-break-inside: avoid;
                    page-break-after: auto;
                }
                th, td {
                    border: 1px solid #000;
                    padding: 4px 6px;
                    text-align: left;
                    vertical-align: middle;
                    font-size: 8.5pt;
                    white-space: nowrap;
                }
                th {
                    background: #f2f2f2 !important;
                    font-weight: 700;
                    -webkit-print-color-adjust: exact;
                    print-color-adjust: exact;
                }
                .col-right {
                    text-align: right;
                }
                .col-center {
                    text-align: center;
                }
                /* 实地盘点留白手填列保障 */
                td:empty {
                    min-height: 28px;
                    background: #fff;
                }
                .print-footer {
                    margin-top: 30px;
                    display: flex;
                    justify-content: space-between;
                    font-size: 10pt;
                    padding: 0 10px;
                    page-break-inside: avoid;
                }
                @media print {
                    body { padding: 0; }
                    .no-print { display: none; }
                }
            </style>
        </head>
        <body>
            <div class="print-header">
                <h2>${title}</h2>
                <div class="meta-bar">
                    <span>${summary}</span>
                    <span>打印时间: ${new Date().toLocaleString('zh-HK', { hour12: false })}</span>
                </div>
            </div>
            <table>
                <thead>${thead}</thead>
                <tbody>${tbody}</tbody>
            </table>
            <div class="print-footer">
                <div>经办人签字：_______________</div>
                <div>主管审核签字：_______________</div>
                <div>日期：_______________</div>
            </div>
            <script>
                window.onload = function() {
                    window.print();
                    setTimeout(function() { window.close(); }, 500);
                };
            </script>
        </body>
        </html>
    `);
    printWin.document.close();
}

// 跨模块快捷导航至导出中心
function navigateToExportCategory(catId, reportId) {
    const btn = document.querySelector('.sidebar-btn[data-target="tab-export"]');
    if (btn) {
        btn.click();
        setTimeout(() => {
            if (catId) selectExportCategory(catId);
            if (reportId) selectExportReport(reportId);
        }, 50);
    }
}
