// -*- coding: utf-8 -*-
/**
 * 统一报表与数据导出中心前端控制器 (Export & Reporting Center)
 * 按 3 个使用者组（盘点补货/经营毛利/台账合规）提供 8 张标准表格的在线预览、参数定制与 CSV 流式下载
 */

const exportCenterState = {
    reports: [],
    currentCategory: 'stocktake',
    currentReportId: 'inventory_stocktake',
    previewData: null,
    isLoading: false,
    suppliers: [],
    departments: [],
    dishCategories: [],
    categories: ['肉类', '海鲜', '蔬菜', '主食粮油', '调味品', '烘焙', '饮品', '清洁', '其他']
};
window.exportCenterState = exportCenterState;

// 转义小工具：一切进入 innerHTML / document.write 的动态插值必须过本函数。
// 与 evalset.js 的 esc() 同实现（临时 div 的 textContent → innerHTML，由浏览器
// 自带的 HTML 文本序列化完成转义），不自行拼正则，避免规则漂移。
// 作用范围说明：本文件只用它做「文本上下文」插值（元素之间/属性值均为受控常量），
// 故不额外转义引号；需要行内 JS 字符串上下文时一律改事件委托 + data-*，不在此处裸拼。
function esc(s) {
    const d = document.createElement('div');
    d.textContent = s === null || s === undefined ? '' : String(s);
    return d.innerHTML;
}

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

    // 餐品分类（FILTERS-§2：dish_category 下拉数据源，单一事实源 /api/dishes/categories）
    apiFetch('/api/dishes/categories')
        .then(res => res.json())
        .then(ret => {
            const list = Array.isArray(ret) ? ret : (ret.data || []);
            exportCenterState.dishCategories = list
                .map(c => (typeof c === 'string' ? c : (c.name || c.category || '')))
                .filter(Boolean);
            updateExportFilterDropdowns();
        }).catch(() => {});
}

// 渲染分类 Tab
function renderExportCategoryTabs() {
    const container = document.getElementById('exportCategoryTabs');
    if (!container) return;

    // B3: 组清单从后端下发的报表元数据派生（按 REPORTS_META 顺序去重保序），
    // 组名单一事实源是后端 category_name，前端不抄写第二份清单。
    const cats = [];
    const seenCats = {};
    exportCenterState.reports.forEach(r => {
        if (r.category && !seenCats[r.category]) {
            seenCats[r.category] = true;
            cats.push({ id: r.category, name: r.category_name || r.category });
        }
    });

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

        html += `
            <div class="export-report-card ${isSelected ? 'selected' : ''}" onclick="selectExportReport('${rep.id}')">
                <div class="export-report-title">${esc(rep.title)}</div>
                <div class="export-report-desc">${esc(rep.description)}</div>
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
        selectExportCategory('ledger', 'receipts_itemized_ledger');
        setExportQuickRange('month');
        showToast('已载入月末财务审计归档包推荐配置', 'info');
    } else if (presetType === 'stocktake') {
        selectExportCategory('stocktake', 'inventory_stocktake');
        showToast('已载入冷库/干货实地盘点清册标准底单', 'info');
    } else if (presetType === 'dish_margin') {
        selectExportCategory('business', 'dish_cost_margin');
        setExportQuickRange('month');
        showToast('已载入菜品成本与毛利诊断报表', 'info');
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
                    ${exportCenterState.suppliers.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}
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
                    ${exportCenterState.departments.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}
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

    // 8. 餐品状态（cost_margin 用；默认全部，保持按日逐单视图既有口径）
    if (filters.includes('dish_status')) {
        html += `
            <div class="export-filter-item">
                <label>餐品状态</label>
                <select id="expDishStatus" class="form-control export-control-input" style="width:120px;" onchange="refreshExportPreview()">
                    <option value="all" selected>全部餐品状态</option>
                    <option value="active">仅在售餐品</option>
                    <option value="inactive">仅已下架</option>
                </select>
            </div>
        `;
    }

    // 9. 成本毛利核算粒度（菜品成本与毛利表：按日逐单 / 按期间汇总）
    if (filters.includes('cost_granularity')) {
        html += `
            <div class="export-filter-item">
                <label>核算粒度</label>
                <select id="expCostGranularity" class="form-control export-control-input" style="width:130px;" onchange="refreshExportPreview()">
                    <option value="daily" selected>按日逐单</option>
                    <option value="period">按期间汇总</option>
                </select>
            </div>
        `;
    }

    // 10. 花销透视维度（期间花销汇总表：部门 / 供应商）
    if (filters.includes('spend_pivot')) {
        html += `
            <div class="export-filter-item">
                <label>花销透视维度</label>
                <select id="expSpendPivot" class="form-control export-control-input" style="width:130px;" onchange="handleSpendPivotChange()">
                    <option value="department" selected>按部门</option>
                    <option value="supplier">按供应商</option>
                </select>
            </div>
        `;
    }

    // 11. FILTERS-§5: 花销维度聚焦（随透视维度联动渲染部门/供应商下拉）
    if (filters.includes('pivot_focus')) {
        html += `
            <div class="export-filter-item">
                <label>聚焦核算对象</label>
                <div id="expPivotFocusWrap"></div>
            </div>
        `;
    }

    // 12. FILTERS-§5/§7/§1: 金额下限 / 区间
    if (filters.includes('amount_range')) {
        const isSpend = report.id === 'spend_summary';
        html += `
            <div class="export-filter-item">
                <label>${isSpend ? '本期采购额下限(元)' : '单据金额区间(元)'}</label>
                <div style="display:flex; gap:6px; align-items:center;">
                    <input type="number" step="0.01" min="0" id="expAmountMin" class="form-control export-control-input" style="width:110px;" placeholder="最低" onchange="refreshExportPreview()">
                    ${isSpend ? '' : `<span style="color:var(--text-muted); font-size:0.85rem;">至</span>
                    <input type="number" step="0.01" min="0" id="expAmountMax" class="form-control export-control-input" style="width:110px;" placeholder="最高" onchange="refreshExportPreview()">`}
                </div>
            </div>
        `;
    }

    // 13. FILTERS-§8 共用：关键词搜索（防抖 300ms）
    if (filters.includes('keyword')) {
        html += `
            <div class="export-filter-item">
                <label>关键词搜索</label>
                <input type="text" id="expKeyword" class="form-control export-control-input" style="width:190px;" placeholder="单号 / 供应商 / 品名" oninput="handleExportSearchInput('expKeyword')">
            </div>
        `;
    }

    // 14. FILTERS-§1: 付清状态
    if (filters.includes('paid_status')) {
        html += `
            <div class="export-filter-item">
                <label>付清状态</label>
                <select id="expPaidStatus" class="form-control export-control-input" style="width:150px;" onchange="refreshExportPreview()">
                    <option value="all" selected>全部付清状态</option>
                    <option value="unpaid">仅未付</option>
                    <option value="paid">仅已付</option>
                    <option value="overdue">仅逾期未付</option>
                </select>
            </div>
        `;
    }

    // 15. FILTERS-§1: 币种（精确匹配，如 HKD / USD）
    if (filters.includes('currency')) {
        html += `
            <div class="export-filter-item">
                <label>币种</label>
                <input type="text" id="expCurrency" class="form-control export-control-input" style="width:90px;" placeholder="HKD" onchange="refreshExportPreview()">
            </div>
        `;
    }

    // 16. FILTERS-§2: 毛利率上限
    if (filters.includes('margin_threshold')) {
        html += `
            <div class="export-filter-item">
                <label>毛利率上限(%)</label>
                <input type="number" step="0.1" min="0" max="100" id="expMarginPctMax" class="form-control export-control-input" style="width:110px;" placeholder="如 50" onchange="refreshExportPreview()">
            </div>
        `;
    }

    // 17. FILTERS-§2: 餐品分类
    if (filters.includes('dish_category')) {
        html += `
            <div class="export-filter-item">
                <label>餐品分类</label>
                <select id="expDishCategory" class="form-control export-control-input" style="width:130px;" onchange="refreshExportPreview()">
                    <option value="">全部分类</option>
                    ${exportCenterState.dishCategories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('')}
                </select>
            </div>
        `;
    }

    // 18. FILTERS-§3: 仅看建议补货
    if (filters.includes('only_suggested')) {
        html += `
            <div class="export-filter-item">
                <label>补货建议</label>
                <label style="display:flex; align-items:center; gap:6px; font-size:0.82rem; cursor:pointer; color:var(--text-muted); user-select:none; padding-top:6px;">
                    <input type="checkbox" id="expOnlySuggested" onchange="refreshExportPreview()">
                    <span>仅看建议补货 &gt; 0</span>
                </label>
            </div>
        `;
    }

    // 19. FILTERS-§4: 涨跌方向 / 阈值覆盖 / 均价窗口
    if (filters.includes('direction')) {
        html += `
            <div class="export-filter-item">
                <label>波动方向</label>
                <select id="expDirection" class="form-control export-control-input" style="width:120px;" onchange="refreshExportPreview()">
                    <option value="all" selected>涨跌都看</option>
                    <option value="up">仅涨价</option>
                    <option value="down">仅降价</option>
                </select>
            </div>
        `;
    }
    if (filters.includes('threshold_override')) {
        html += `
            <div class="export-filter-item">
                <label>异动阈值临时覆盖(%)</label>
                <input type="number" step="0.1" min="0" max="100" id="expThresholdPct" class="form-control export-control-input" style="width:110px;" placeholder="留空=租户阈值" onchange="refreshExportPreview()">
            </div>
        `;
    }
    if (filters.includes('window_days')) {
        html += `
            <div class="export-filter-item">
                <label>均价窗口</label>
                <select id="expWindowDays" class="form-control export-control-input" style="width:120px;" onchange="refreshExportPreview()">
                    <option value="7">近 7 天均价</option>
                    <option value="30" selected>近 30 天均价</option>
                    <option value="60">近 60 天均价</option>
                </select>
            </div>
        `;
    }

    // 20. FILTERS-§6: 关联单号 / 供应商关键词（防抖）
    if (filters.includes('receipt_ref')) {
        html += `
            <div class="export-filter-item">
                <label>关联收据号</label>
                <input type="text" id="expReceiptRef" class="form-control export-control-input" style="width:130px;" placeholder="如 #1502" oninput="handleExportSearchInput('expReceiptRef')">
            </div>
        `;
    }
    if (filters.includes('vendor_keyword')) {
        html += `
            <div class="export-filter-item">
                <label>供应商关键词</label>
                <input type="text" id="expVendorKeyword" class="form-control export-control-input" style="width:150px;" placeholder="模糊匹配" oninput="handleExportSearchInput('expVendorKeyword')">
            </div>
        `;
    }

    // 21. FILTERS-§7: 货值下限
    if (filters.includes('value_min')) {
        html += `
            <div class="export-filter-item">
                <label>货值下限(元)</label>
                <input type="number" step="0.01" min="0" id="expValueMin" class="form-control export-control-input" style="width:110px;" placeholder="如 1000" onchange="refreshExportPreview()">
            </div>
        `;
    }

    container.innerHTML = html;
    // FILTERS-§5: 聚焦下拉按当前透视维度渲染
    if (filters.includes('pivot_focus')) renderPivotFocusOptions();

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
            exportCenterState.suppliers.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('');
        supSel.value = val;
    }
    const deptSel = document.getElementById('expDepartment');
    if (deptSel) {
        const val = deptSel.value;
        deptSel.innerHTML = '<option value="">全部部门</option>' +
            exportCenterState.departments.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('') +
            '<option value="0">未分配部门</option>';
        deptSel.value = val;
    }
    // FILTERS-§2: 餐品分类下拉异步补齐
    const dishCatSel = document.getElementById('expDishCategory');
    if (dishCatSel) {
        const val = dishCatSel.value;
        dishCatSel.innerHTML = '<option value="">全部分类</option>' +
            exportCenterState.dishCategories.map(c => `<option value="${esc(c)}">${esc(c)}</option>`).join('');
        dishCatSel.value = val;
    }
    // FILTERS-§5: 聚焦下拉复用同一数据源
    renderPivotFocusOptions();
}

// FILTERS-§5: 透视维度切换 → 重置聚焦选择并重渲下拉
function handleSpendPivotChange() {
    renderPivotFocusOptions();
    refreshExportPreview();
}

function renderPivotFocusOptions() {
    const wrap = document.getElementById('expPivotFocusWrap');
    if (!wrap) return;
    const pivotSel = document.getElementById('expSpendPivot');
    const pivot = pivotSel ? pivotSel.value : 'department';
    const prev = wrap.dataset.prevValue || '';
    if (pivot === 'supplier') {
        wrap.innerHTML = `<select id="expPivotFocus" class="form-control export-control-input" style="min-width:170px;" onchange="refreshExportPreview()">
            <option value="">全部供应商</option>
            ${exportCenterState.suppliers.map(s => `<option value="${s.id}">${esc(s.name)}</option>`).join('')}
        </select>`;
    } else {
        wrap.innerHTML = `<select id="expPivotFocus" class="form-control export-control-input" style="width:150px;" onchange="refreshExportPreview()">
            <option value="">全部部门</option>
            ${exportCenterState.departments.map(d => `<option value="${d.id}">${esc(d.name)}</option>`).join('')}
        </select>`;
    }
    const sel = document.getElementById('expPivotFocus');
    if (sel && prev) sel.value = prev; // 切维度后原值通常不适用，sel.value 静默回退到 ''
    wrap.dataset.prevValue = sel ? sel.value : '';
}

// FILTERS: 文本类筛选防抖（300ms），避免每敲一个字符就打一次后端
let _exportSearchTimer = null;
function handleExportSearchInput(inputId) {
    clearTimeout(_exportSearchTimer);
    _exportSearchTimer = setTimeout(() => refreshExportPreview(), 300);
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

    if (filters.includes('cost_granularity')) {
        const gran = document.getElementById('expCostGranularity');
        if (gran && gran.value) p.cost_granularity = gran.value;
    }

    if (filters.includes('spend_pivot')) {
        const pivot = document.getElementById('expSpendPivot');
        if (pivot && pivot.value) p.spend_pivot = pivot.value;
    }

    // ---- FILTERS-§8: 新增筛选参数（全部空值不入参，保持"空=不过滤"契约） ----
    if (filters.includes('keyword')) {
        const kw = document.getElementById('expKeyword');
        if (kw && kw.value.trim()) p.keyword = kw.value.trim();
    }
    if (filters.includes('paid_status')) {
        const el = document.getElementById('expPaidStatus');
        if (el && el.value && el.value !== 'all') p.paid_status = el.value;
    }
    if (filters.includes('amount_range')) {
        const mn = document.getElementById('expAmountMin');
        const mx = document.getElementById('expAmountMax');
        if (mn && mn.value !== '') p.amount_min = mn.value;
        if (mx && mx.value !== '') p.amount_max = mx.value;
    }
    if (filters.includes('currency')) {
        const el = document.getElementById('expCurrency');
        if (el && el.value.trim()) p.currency = el.value.trim();
    }
    if (filters.includes('margin_threshold')) {
        const el = document.getElementById('expMarginPctMax');
        if (el && el.value !== '') p.margin_pct_max = el.value;
    }
    if (filters.includes('dish_category')) {
        const el = document.getElementById('expDishCategory');
        if (el && el.value) p.dish_category = el.value;
    }
    if (filters.includes('only_suggested')) {
        const el = document.getElementById('expOnlySuggested');
        if (el && el.checked) p.only_suggested = 'true';
    }
    if (filters.includes('direction')) {
        const el = document.getElementById('expDirection');
        if (el && el.value && el.value !== 'all') p.direction = el.value;
    }
    if (filters.includes('threshold_override')) {
        const el = document.getElementById('expThresholdPct');
        if (el && el.value !== '') p.threshold_pct = el.value;
    }
    if (filters.includes('window_days')) {
        const el = document.getElementById('expWindowDays');
        if (el && el.value && el.value !== '30') p.window_days = el.value;
    }
    if (filters.includes('pivot_focus')) {
        const focus = document.getElementById('expPivotFocus');
        if (focus && focus.value) {
            // 按当前透视维度落参数名：按部门 → department_id；按供应商 → supplier_id
            const pivotSel = document.getElementById('expSpendPivot');
            const isSupplier = pivotSel && pivotSel.value === 'supplier';
            if (isSupplier) p.supplier_id = focus.value;
            else p.department_id = focus.value;
        }
    }
    if (filters.includes('receipt_ref')) {
        const el = document.getElementById('expReceiptRef');
        if (el && el.value.trim()) p.receipt_ref = el.value.trim();
    }
    if (filters.includes('vendor_keyword')) {
        const el = document.getElementById('expVendorKeyword');
        if (el && el.value.trim()) p.vendor = el.value.trim();
    }
    if (filters.includes('value_min')) {
        const el = document.getElementById('expValueMin');
        if (el && el.value !== '') p.value_min = el.value;
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
// 列对齐判定：按表头语义词归类右对齐（金额/数量）或居中（日期/状态）。
// 供预览渲染与虚拟滚动窗口复用。
function exportColAlignClass(h) {
    if (!h) return '';
    const s = String(h);
    if (s.includes('金额') || s.includes('价') || s.includes('库存') || s.includes('数量') ||
        s.includes('率') || s.includes('总额') || s.includes('差异') || s.includes('幅度') ||
        s.includes('比') || s.includes('成本') || s.includes('毛利') || s.includes('份数') ||
        s.includes('货值') || s.includes('警戒线') || s.includes('实盘') || s.includes('均价')) {
        return 'col-right';
    }
    if (s.includes('日期') || s.includes('类型') || s.includes('单位') || s.includes('状态') ||
        s.includes('签字') || s.includes('分类') || s.includes('模式') || s.includes('级别') ||
        h.includes('ID') || h.includes('编号') || s.includes('ID') || s.includes('编号') || s.includes('单号') || s.includes('凭证')) {
        return 'col-center';
    }
    return '';
}

// 行 HTML 构建（预览渲染 / 虚拟滚动窗口 / 打印全量 三处共用，保证口径一致）
function buildExportRowsHtml(rows, headers) {
    let html = '';
    rows.forEach(r => {
        html += '<tr>';
        r.forEach((cell, idx) => {
            const h = headers[idx] || '';
            const alignClass = exportColAlignClass(h);
            const isTextDesc = (h.includes('品名') || h.includes('名称') || h.includes('说明') || h.includes('备注') || h.includes('配方') || h.includes('建议'));
            const wrapClass = isTextDesc ? 'export-col-desc' : 'export-col-nowrap';

            // 特殊样式高亮（统一为胶囊徽标）
            // 已知债（A5，本次不修）：徽标判定靠单元格文本子串猜测（'异常'/'正常'…），
            // 后端文案措辞变化会静默失效。正道是后端在 summary/单元格元数据里显式下发
            // 语义标记（如 {text, tone}），待下一轮重构处理。
            // 单元格值来自单据/供应商/食材名等 OCR 派生数据：判定语义词用原文，
            // 写进 innerHTML 的形态用转义后的文本（先转义再包徽标，避免二次解码）
            const cellText = (cell === null || cell === undefined) ? '-' : String(cell);
            let cellContent = esc(cellText);
            if (cellText.includes('异常') || cellText.includes('低库存') || cellText.includes('承压') || cellText.includes('跌价') || cellText.includes('逾期') || cellText.includes('预警') || cellText.includes('异动')) {
                cellContent = `<span class="badge badge-danger">${cellContent}</span>`;
            } else if (cellText.includes('已入账') || cellText.includes('正常') || cellText.includes('稳定') || cellText.includes('下降') || cellText.includes('已审批') || cellText.includes('充足') || cellText.includes('健康')) {
                cellContent = `<span class="badge badge-success">${cellContent}</span>`;
            }

            html += `<td class="${alignClass} ${wrapClass}">${cellContent}</td>`;
        });
        html += '</tr>';
    });
    return html;
}

// ========== 虚拟滚动（全量预览的 DOM 性能保障） ==========
// 策略：行数 ≤ 阈值照常一次性渲染；超过则只渲染可视窗口 ± 过扫描行，
// 用上下两条占位 <tr> 撑起真实总滚动高度。行高带实测修正缓存：
// 默认估算值，行渲染进 DOM 后回读实测高度修正缓存（长品名折行行高更高），避免滚动跳动。
const EXPORT_VIRTUAL_THRESHOLD = 80;   // 超过该行数才虚拟化，小表直接全渲染
const EXPORT_VIRTUAL_OVERSCAN = 8;     // 视口外上下各多渲染的缓冲行数
const EXPORT_VIRTUAL_DEFAULT_ROW_H = 44;

const exportVirtualState = {
    active: false,
    rows: [],
    headers: [],
    tbody: null,
    container: null,
    heights: [],           // 每行高度（0 = 未实测，取默认估算值）
    totalH: 0,
    start: -1,
    end: -1,
    scrollBound: false,
};
// 虚拟滚动状态显式挂 window：与 exportCenterState 同惯例，供控制台调试与 e2e 探针
window.exportVirtualState = exportVirtualState;

function resetExportVirtual() {
    exportVirtualState.active = false;
    exportVirtualState.rows = [];
    exportVirtualState.headers = [];
    exportVirtualState.tbody = null;
    exportVirtualState.heights = [];
    exportVirtualState.totalH = 0;
    exportVirtualState.start = -1;
    exportVirtualState.end = -1;
}

function _virtualPrefixSums() {
    // P[i] = 第 i 行顶边的纵向偏移；P[n] = 总滚动高度
    const n = exportVirtualState.heights.length;
    const P = new Array(n + 1);
    P[0] = 0;
    for (let i = 0; i < n; i++) {
        P[i + 1] = P[i] + (exportVirtualState.heights[i] || EXPORT_VIRTUAL_DEFAULT_ROW_H);
    }
    return P;
}

function _virtualIndexForOffset(P, offset) {
    // 在前缀和上二分：返回最后一个 P[idx] <= offset 的行号（即 offset 落在哪一行）
    let lo = 0, hi = P.length - 2, ans = 0;
    while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (P[mid] <= offset) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
    }
    return ans;
}

function startExportVirtual(tbody, rows, headers) {
    const s = exportVirtualState;
    s.active = true;
    s.rows = rows;
    s.headers = headers;
    s.tbody = tbody;
    s.heights = new Array(rows.length).fill(0);
    s.start = -1;
    s.end = -1;
    s.container = tbody.closest('.table-container') || tbody.parentElement;
    if (s.container && !s.scrollBound) {
        s.scrollBound = true;
        // 时间节流（50ms）而非 rAF：rAF 在页签不可见时会被浏览器暂停，
        // 程序化滚动（如打印前定位）会拿不到重渲染。窗口渲染只有 ~20 行，开销可忽略。
        let lastTick = 0;
        s.container.addEventListener('scroll', () => {
            const now = Date.now();
            if (now - lastTick < 50) {
                // 尾部兜底：最后一次滚动若落在节流窗内，延迟补渲一次，避免停在旧窗口
                clearTimeout(s._trailTimer);
                s._trailTimer = setTimeout(() => {
                    if (exportVirtualState.active) renderExportVirtualWindow();
                }, 60);
                return;
            }
            lastTick = now;
            if (exportVirtualState.active) renderExportVirtualWindow();
        });
    }
    if (s.container) s.container.scrollTop = 0;
    renderExportVirtualWindow();
}

function renderExportVirtualWindow() {
    const s = exportVirtualState;
    if (!s.active || !s.tbody || !s.container) return;

    const n = s.rows.length;
    const viewportH = s.container.clientHeight || 460;
    const scrollTop = s.container.scrollTop;
    const colspan = Math.max(s.headers.length, 1);

    // 实测修正最多回环 2 次：首渲估算 → 量实高 → 位置可能漂移 → 再校正一次
    for (let pass = 0; pass < 2; pass++) {
        const P = _virtualPrefixSums();
        const startRaw = _virtualIndexForOffset(P, scrollTop);
        const endRaw = _virtualIndexForOffset(P, scrollTop + viewportH) + 1;
        const start = Math.max(0, startRaw - EXPORT_VIRTUAL_OVERSCAN);
        const end = Math.min(n, endRaw + EXPORT_VIRTUAL_OVERSCAN);

        if (pass === 0 && start === s.start && end === s.end && s.totalH === P[n]) return;
        s.start = start; s.end = end; s.totalH = P[n];

        const spacerTop = P[start];
        const spacerBottom = P[n] - P[end];
        const windowHtml = buildExportRowsHtml(s.rows.slice(start, end), s.headers);
        s.tbody.innerHTML =
            `<tr class="virtual-spacer"><td colspan="${colspan}" style="height:${spacerTop}px; padding:0; border:none;"></td></tr>` +
            windowHtml +
            `<tr class="virtual-spacer"><td colspan="${colspan}" style="height:${spacerBottom}px; padding:0; border:none;"></td></tr>`;

        // 回读本窗口实高，修正缓存（折行行比估算高/矮都会导致后续窗口定位漂移）
        let dirty = false;
        const trs = s.tbody.querySelectorAll('tr:not(.virtual-spacer)');
        trs.forEach((tr, i) => {
            const idx = start + i;
            const h = Math.round(tr.getBoundingClientRect().height);
            if (h > 0 && (s.heights[idx] || 0) !== h) {
                s.heights[idx] = h;
                dirty = true;
            }
        });
        if (!dirty) break;
    }
}

function renderExportPreviewUI(data) {
    const titleEl = document.getElementById('exportActiveReportTitle');
    if (titleEl) titleEl.innerText = data.title || '报表实时数据预览';

    const summaryBox = document.getElementById('exportPreviewSummary');
    if (summaryBox) {
        let sumHtml = `<span class="badge badge-primary" style="font-size:0.85rem; padding:4px 8px;">共 ${esc(data.total_rows)} 条记录</span>`;
        if (data.summary && Object.keys(data.summary).length > 0) {
            for (const [k, v] of Object.entries(data.summary)) {
                sumHtml += `<span class="export-summary-pill"><strong>${esc(k)}:</strong> ${esc(v)}</span>`;
            }
        }
        summaryBox.innerHTML = sumHtml;
    }

    const thead = document.getElementById('exportPreviewThead');
    const tbody = document.getElementById('exportPreviewTbody');
    if (!thead || !tbody) return;

    // 表头
    let headHtml = '<tr>';
    (data.headers || []).forEach(h => {
        const alignClass = exportColAlignClass(h);
        headHtml += `<th class="${alignClass}" style="white-space:nowrap;">${esc(h)}</th>`;
    });
    headHtml += '</tr>';
    thead.innerHTML = headHtml;

    // 行
    const rows = data.preview_rows || [];
    if (rows.length === 0) {
        // AC-6: 100% 人话直白展示空状态，杜绝任何技术黑话
        tbody.innerHTML = `<tr><td colspan="${(data.headers && data.headers.length) || 1}" class="col-center" style="padding:48px 16px; text-align:center; color:var(--text-muted); font-size:0.95rem;">在这个时间段内没有找到任何单据记录，请检查日期或切换分类</td></tr>`;
        resetExportVirtual();
        return;
    }

    // 全量预览的 DOM 性能保障：行数超阈值时启用虚拟滚动，只渲染可视窗口 + 过扫描缓冲
    if (rows.length > EXPORT_VIRTUAL_THRESHOLD) {
        startExportVirtual(tbody, rows, data.headers || []);
    } else {
        resetExportVirtual();
        tbody.innerHTML = buildExportRowsHtml(rows, data.headers || []);
    }
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
            desensitized: desens,
            lang: (window.I18N && typeof window.I18N.getLocale === 'function') ? window.I18N.getLocale() : undefined
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
    // 取值口径：标题与汇总取自 innerText（纯文本），写进新文档前一律过 esc()，
    // 否则标题里的 `<` / `&` 会被当作标签解析（打印窗口是真实文档，等同又一次 innerHTML）。
    // thead / tbody 取自 innerHTML，是 renderExportPreviewUI 已用 esc() 生成的可信 HTML：
    // 表结构必须保持为 HTML 才能渲染，故不二次转义（转义会让整张表退化成源码文本）。
    const title = document.getElementById('exportActiveReportTitle')?.innerText || '报表打印';
    const thead = document.getElementById('exportPreviewThead')?.innerHTML || '';
    // 虚拟滚动激活时 on-screen tbody 只有可视窗口行，打印必须用全量数据重建，
    // 保证打印件与导出 CSV 同口径（盘点底单少一行都是现场对不上账）。
    const tbody = exportVirtualState.active
        ? buildExportRowsHtml(exportVirtualState.rows, exportVirtualState.headers)
        : document.getElementById('exportPreviewTbody')?.innerHTML || '';
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
            <title>${esc(title)}</title>
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
                <h2>${esc(title)}</h2>
                <div class="meta-bar">
                    <span>${esc(summary)}</span>
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
    // 打印窗口是独立文档，不在主页面 DOM 翻译器的观察范围内。
    if (window.I18N) window.I18N.translateDocument(printWin.document);
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
