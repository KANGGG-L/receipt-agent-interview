// demo/static/js/dish_bom.js
// Task 4 配方库族剩余：餐品管理列表（renderDishTable/openDishModal）、
// BOM 配方库管理、配方打印（printDishBomLibrary）。
// 日常消耗/配方版本/消耗成本分析已在 dish_consumption.js（F3·B6-2）。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// Task 4: 餐品管理、BOM 配方、每日消耗极速录入与 FIFO 批次成本穿透溯源
// ==========================================================================

let dishLibraryCache = [];          // 餐品库全量缓存
let dailyConsumptionCache = [];     // 当日消耗流水缓存
let dishDailyDishesCache = [];      // 极速录入在售餐品列表缓存
let dailyKpiSummaryCache = {};      // 最近一次后端返回的当日流水汇总（KPI 预估叠加的实际值基准）
let dishCostAnalysisData = null;    // 成本大盘分析数据缓存
let dishViewMode = 'card';          // 'card' 或 'table'
let dishCategoryAccordionState = {}; // key: categoryName (string), value: boolean (true = expanded, false = collapsed)
let dishAnalysisDays = 7;           // 成本分析周期天数
let dishAvailableSkus = [];         // 用于配方选择的可用 SKU 列表

/**
 * 前端单位换算器（用于配方弹窗实时理论成本预览与计算）
 */
function convertUnitQtyFrontend(qty, fromUnit, toUnit) {
    if (!fromUnit || !toUnit || fromUnit.trim() === toUnit.trim()) return Number(qty) || 0;
    const u1 = fromUnit.trim().toLowerCase();
    const u2 = toUnit.trim().toLowerCase();
    const q = Number(qty) || 0;

    const weightToKg = {
        'kg': 1.0, '千克': 1.0, '公斤': 1.0,
        'g': 0.001, '克': 0.001,
        '斤': 0.5, '市斤': 0.5,
        '两': 0.05,
        '磅': 0.45359237, 'lb': 0.45359237, 'lbs': 0.45359237,
        'oz': 0.0283495, '盎司': 0.0283495,
        '吨': 1000.0, 'ton': 1000.0, 't': 1000.0
    };
    if (weightToKg[u1] && weightToKg[u2]) {
        return (q * weightToKg[u1]) / weightToKg[u2];
    }

    const volToL = {
        'l': 1.0, '升': 1.0, '公升': 1.0,
        'ml': 0.001, '毫升': 0.001, 'cc': 0.001,
        'cl': 0.01
    };
    if (volToL[u1] && volToL[u2]) {
        return (q * volToL[u1]) / volToL[u2];
    }

    if ((u1 === '打' || u1 === 'dozen') && (u2 === '个' || u2 === '件' || u2 === '只' || u2 === '支' || u2 === '份')) {
        return q * 12;
    }
    if ((u2 === '打' || u2 === 'dozen') && (u1 === '个' || u1 === '件' || u1 === '只' || u1 === '支' || u1 === '份')) {
        return q / 12;
    }

    return q;
}

/**
 * 拉取系统当前可用食材 SKU 列表（供配方下拉选择）
 */
function fetchDishAvailableSkus(callback) {
    apiFetch('/api/inventory?include_inactive=0')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && Array.isArray(ret.data)) {
                dishAvailableSkus = ret.data;
            } else if (typeof inventorySkusMap !== 'undefined' && inventorySkusMap.size > 0) {
                dishAvailableSkus = Array.from(inventorySkusMap.values());
            }
            if (typeof callback === 'function') callback(dishAvailableSkus);
        })
        .catch(err => {
            if (!window.__roleSwitchPending) console.error('Failed to fetch SKUs for dish recipe:', err);
            if (typeof inventorySkusMap !== 'undefined' && inventorySkusMap.size > 0) {
                dishAvailableSkus = Array.from(inventorySkusMap.values());
            }
            if (typeof callback === 'function') callback(dishAvailableSkus);
        });
}

/**
 * 初始化餐品与消耗主 Tab
 */
function initDishTab() {
    initDishConsumptionDate();
    const activeSubtabBtn = document.querySelector('.dish-subtab-btn.active');
    const targetSubtab = activeSubtabBtn ? activeSubtabBtn.getAttribute('data-subtab') : 'subtab-dish-daily';
    switchDishSubtab(targetSubtab);
    // 异步预加载 SKU 列表
    fetchDishAvailableSkus();
}

/**
 * 切换餐品管理子 Tab (每日消耗录入 / BOM 配方库 / 成本大盘)
 */
function switchDishSubtab(subtabId) {
    const subtabBtns = document.querySelectorAll('.dish-subtab-btn');
    const panes = document.querySelectorAll('.dish-subtab-pane');

    subtabBtns.forEach(btn => {
        if (btn.getAttribute('data-subtab') === subtabId || btn.dataset.subtab === subtabId) {
            btn.classList.add('active');
        } else {
            btn.classList.remove('active');
        }
    });

    panes.forEach(pane => {
        if (pane.id === subtabId) {
            pane.classList.add('active');
        } else {
            pane.classList.remove('active');
        }
    });

    if (subtabId === 'subtab-dish-daily') {
        loadDailyConsumption();
    } else if (subtabId === 'subtab-dish-bom') {
        loadDishesList();
    } else if (subtabId === 'subtab-dish-insights') {
        loadDishCostAnalysis();
    }
}

// --------------------------------------------------------------------------
// 1. 餐品 BOM 配方库管理
// --------------------------------------------------------------------------

// 毛利率分档口径（全文件唯一事实源）：>=60 优 / >=40 中 / 其余 差。
// 徽章样式与文字颜色共用同一分档函数，阈值不再散落在各处硬编码。
const MARGIN_GOOD = 60;
const MARGIN_WARN = 40;
const MARGIN_CLASS = { good: 'badge-success', warn: 'badge-warning', bad: 'badge-danger' };
const MARGIN_COLOR = { good: 'var(--success, #16a34a)', warn: 'var(--warning, #eab308)', bad: 'var(--danger, #dc2626)' };

function marginTier(v) {
    return v >= MARGIN_GOOD ? 'good' : (v >= MARGIN_WARN ? 'warn' : 'bad');
}

function marginClass(v) {
    return MARGIN_CLASS[marginTier(v)];
}

function marginTextColor(v) {
    return MARGIN_COLOR[marginTier(v)];
}

/**
 * 加载餐品库全量列表
 */
function loadDishesList() {
    apiFetch('/api/dishes')
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') {
                showToast(ret.msg || '加载餐品列表失败', 'error');
                return;
            }
            dishLibraryCache = ret.data || [];
            refreshDishCategoryOptions();
            filterDishBomLibrary();
        })
        .catch(err => {
            console.error('loadDishesList error:', err);
            showToast('网络异常，无法加载餐品列表', 'error');
        });
}

// 分类单一事实源：由 GET /api/dishes/categories 拉取（distinct 非空 + 引用数），
// 供 datalist / 筛选下拉 / 分类管理弹窗共用同一集合（D-4）。
let dishCategoryList = [];

/**
 * 拉取分类集合并同步刷新 datalist 与筛选下拉（保留「全部分类」置顶）
 */
function refreshDishCategoryOptions() {
    return apiFetch('/api/dishes/categories')
        .then(res => res.json())
        .then(ret => {
            dishCategoryList = (ret.status === 'success' && Array.isArray(ret.data)) ? ret.data : [];
        })
        .catch(() => { dishCategoryList = []; })
        .then(() => {
            renderDishCategoryFilter();
            updateDishCategorySuggestions();
        });
}

/**
 * 重建 #dishCategoryFilter 下拉：全部分类 + 动态 distinct 分类（来自 dishCategoryList，含「默认」）
 */
function renderDishCategoryFilter() {
    const catSelect = document.getElementById('dishCategoryFilter');
    if (!catSelect) return;
    const current = catSelect.value;
    let html = '<option value="">全部分类</option>';
    const addedCats = new Set();
    (dishCategoryList || []).forEach(c => {
        const name = (typeof c === 'string' ? c : (c && c.name ? c.name : '')).trim();
        if (name && !addedCats.has(name)) {
            addedCats.add(name);
            html += '<option value="' + w2Escape(name) + '">' + w2Escape(name) + '</option>';
        }
    });
    if (!addedCats.has('默认')) {
        addedCats.add('默认');
        html += '<option value="默认">默认</option>';
    }
    catSelect.innerHTML = html;
    if (current && addedCats.has(current)) {
        catSelect.value = current;
    }
    filterDishBomLibrary();
}

/**
 * 重建分类建议集合（完全由 dishCategoryList 动态驱动，废除写死静态分类），供自定义 Combobox 下拉使用
 */
let dishCategorySuggestions = [];

function updateDishCategorySuggestions() {
    const cats = new Set();
    (dishCategoryList || []).forEach(c => {
        const name = (typeof c === 'string' ? c : (c && c.name ? c.name : '')).trim();
        if (name) cats.add(name);
    });
    if (!cats.has('默认')) {
        cats.add('默认');
    }
    dishCategorySuggestions = Array.from(cats);
}

/**
 * 打开餐品分类自定义下拉（页内渲染，规避原生 datalist 弹窗在嵌入式浏览器飞出窗口）
 */
function openDishCategoryMenu(inputElem) {
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const wrap = inputElem.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;
    renderDishCategoryMenuItems(inputElem, menu);
    menu.classList.remove('hide');
}

function renderDishCategoryMenuItems(inputElem, menuElem) {
    const cur = (inputElem.value || '').trim().toLowerCase();
    const recommended = [];
    const regular = [];
    dishCategorySuggestions.forEach(cat => {
        const cLower = cat.toLowerCase();
        const isExact = cLower === cur;
        const isSimilar = !!cur && (cLower.includes(cur) || cur.includes(cLower));
        (isExact || isSimilar ? recommended : regular).push({ cat, isExact, isSimilar });
    });
    const html = [...recommended, ...regular].map(it =>
        '<div class="unit-dropdown-item' + ((it.isExact || it.isSimilar) ? ' highlight' : '') + '" data-dish-cat="' + w2Escape(it.cat) + '">'
        + '<span>' + w2Escape(it.cat) + '</span>'
        + (it.isExact ? '<span class="badge-matched">已选择</span>' : (it.isSimilar ? '<span class="badge-matched">推荐匹配</span>' : ''))
        + '</div>'
    ).join('');
    menuElem.innerHTML = html || '<div class="unit-dropdown-item" style="cursor:default; color:var(--text-muted);">无匹配分类，可直接输入新分类</div>';
}

function closeDishCategoryMenuDelay(inputElem) {
    setTimeout(() => {
        const wrap = inputElem.closest('.unit-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}

// 分类下拉选项选择（事件委托，与单位下拉同模式）
document.addEventListener('mousedown', (e) => {
    const item = e.target && e.target.closest
        ? e.target.closest('.unit-dropdown-item[data-dish-cat]') : null;
    if (!item) return;
    const wrap = item.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const input = wrap.querySelector('#dishModalCategory');
    if (input) {
        input.value = item.getAttribute('data-dish-cat') || '';
        input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (menu) menu.classList.add('hide');
});

/**
 * 打开餐品分类管理弹窗（仅 owner，见 applyDishRoleVisibility）
 */
function openDishCategoryManager() {
    if (isStaffRoleNow()) {
        showToast('店员无权管理餐品分类', 'warn');
        return;
    }
    const modal = document.getElementById('dishCategoryManagerModal');
    const body = document.getElementById('dishCategoryManagerBody');
    if (body) body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">加载分类中…</div>';
    if (modal) openModalById('dishCategoryManagerModal');
    apiFetch('/api/dishes/categories')
        .then(res => res.json())
        .then(ret => {
            dishCategoryList = (ret.status === 'success' && Array.isArray(ret.data)) ? ret.data : [];
            renderDishCategoryManager(dishCategoryList);
        })
        .catch(() => {
            renderDishCategoryManager([]);
        });
}

/**
 * 渲染分类管理列表（分类名 + 引用数 + 重命名/替换分类 + 删除按钮）
 */
function renderDishCategoryManager(cats) {
    const body = document.getElementById('dishCategoryManagerBody');
    if (!body) return;
    if (!cats.length) {
        body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">暂无已使用餐品分类</div>';
        return;
    }
    let html = '';
    cats.forEach(c => {
        const isDefault = c.name === '默认';
        const nameBadge = isDefault
            ? '<span class="badge badge-info" style="font-size:0.8rem; background:rgba(37,99,235,0.1); color:#2563eb;">' + w2Escape(c.name) + '</span>'
              + '<span class="badge badge-neutral" style="font-size:0.75rem; margin-left:6px;">[内置默认]</span>'
            : '<span class="badge badge-info" style="font-size:0.8rem; background:rgba(37,99,235,0.1); color:#2563eb;">' + w2Escape(c.name) + '</span>';
        const actionButtons = isDefault
            ? '<button type="button" class="btn btn-secondary" disabled title="系统默认分组无法修改也无法删除" style="padding:4px 10px; font-size:0.8rem; min-height:30px; height:30px; opacity:0.5; cursor:not-allowed;">重命名</button>'
              + '<button type="button" class="btn btn-danger" disabled title="系统默认分组无法修改也无法删除" style="padding:4px 10px; font-size:0.8rem; min-height:30px; height:30px; opacity:0.5; cursor:not-allowed; margin-left:6px;">删除</button>'
            : '<button type="button" class="btn btn-secondary btn-rename-dish-cat" style="padding:4px 10px; font-size:0.8rem; min-height:30px; height:30px;">重命名</button>'
              + '<button type="button" class="btn btn-danger btn-delete-dish-cat" style="padding:4px 10px; font-size:0.8rem; min-height:30px; height:30px;">删除</button>';

        html += '<div class="dish-cat-mgmt-row" data-cat-name="' + w2Escape(c.name) + '" data-cat-count="' + c.count + '"'
            + ' style="display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border:1px solid var(--border-color); border-radius:8px; margin-bottom:8px;">'
            + '  <div style="display:flex; align-items:center; gap:8px;">'
            + '    ' + nameBadge
            + '    <span style="font-size:0.78rem; color:var(--text-muted);">' + c.count + ' 道餐品引用</span>'
            + '  </div>'
            + '  <div style="display:flex; gap:6px;">'
            + '    ' + actionButtons
            + '  </div>'
            + '</div>';
    });
    body.innerHTML = html;
    body.onclick = (e) => {
        const renameBtn = e.target && e.target.closest('.btn-rename-dish-cat');
        if (renameBtn) {
            const row = renameBtn.closest('.dish-cat-mgmt-row');
            if (!row) return;
            const name = row.getAttribute('data-cat-name') || '';
            const count = parseInt(row.getAttribute('data-cat-count') || '0', 10) || 0;
            renameDishCategory(name, count);
            return;
        }
        const delBtn = e.target && e.target.closest('.btn-delete-dish-cat');
        if (delBtn) {
            const row = delBtn.closest('.dish-cat-mgmt-row');
            if (!row) return;
            const name = row.getAttribute('data-cat-name') || '';
            const count = parseInt(row.getAttribute('data-cat-count') || '0', 10) || 0;
            deleteDishCategory(name, count);
            return;
        }
    };
}

/**
 * 重命名 / 替换分类：弹出输入框修改分类名称或合并到目标分类
 */
function renameDishCategory(name, count) {
    const oldName = name || '';
    if (oldName === '默认') {
        showToast('系统默认分组无法修改', 'warn');
        return;
    }
    showCustomPromptModal({
        title: '重命名 / 替换分类',
        message: '当前有 ' + count + ' 道餐品归属于「' + oldName + '」。\n请输入新的分类名称（若输入已有分类，则餐品将自动归并）：',
        defaultValue: oldName,
        placeholder: '如：主食热菜、特色热菜',
        maxLength: 20,
        confirmText: '确认修改',
        cancelText: '取消',
        onConfirm: (newName) => {
            const trimmed = (newName || '').trim();
            if (!trimmed) {
                showToast('分类名称不能为空', 'error');
                return;
            }
            if (trimmed === oldName) {
                showToast('新分类名称与原分类名称相同，未做更改', 'info');
                return;
            }
            const url = '/api/dishes/categories/' + encodeURIComponent(oldName)
                + '?replace_with=' + encodeURIComponent(trimmed);
            apiFetch(url, { method: 'DELETE' })
                .then(res => res.json())
                .then(ret => {
                    if (ret.status !== 'success') {
                        showToast(ret.msg || '重命名分类失败', 'error');
                        return;
                    }
                    showToast(ret.msg || '分类已更新', 'success');
                    openDishCategoryManager();
                    loadDishesList();
                })
                .catch(err => {
                    console.error('renameDishCategory error:', err);
                    showToast('网络异常，无法更新分类', 'error');
                });
        }
    });
}

/**
 * 删除餐品分类：二次确认（被引用时给出影响提示），删除后同步刷新 datalist/筛选
 */
function deleteDishCategory(name, count) {
    const catName = name || '';
    if (catName === '默认') {
        showToast('系统默认分组无法删除', 'warn');
        return;
    }
    const title = '删除分类确认';
    const message = count > 0
        ? ('删除分类「' + catName + '」？\n将影响 ' + count + ' 道餐品，这些餐品的分类将改为「默认」。\n\n（提示：如需重命名或合并为其他指定分类，请使用「重命名」功能）')
        : ('删除分类「' + catName + '」？');
    showCustomConfirmModal({
        title: title,
        message: message,
        confirmText: '确认删除',
        cancelText: '取消',
        onConfirm: () => {
            const delUrl = '/api/dishes/categories/' + encodeURIComponent(catName);
            apiFetch(delUrl, { method: 'DELETE' })
                .then(res => res.json())
                .then(ret => {
                    if (ret.status !== 'success') {
                        showToast(ret.msg || '删除分类失败', 'error');
                        return;
                    }
                    showToast(ret.msg || '分类已删除', 'success');
                    openDishCategoryManager();
                    loadDishesList();
                })
                .catch(err => {
                    console.error('deleteDishCategory error:', err);
                    showToast('网络异常，无法删除分类', 'error');
                });
        }
    });
}

/**
 * 餐品 BOM 品类分组与折叠手风琴模块
 */

/**
 * 依据餐品品类聚合餐品数据
 * 规则：
 * 1. 已定义品类优先级：优先依据 dishCategoryList 自然排序；
 * 2. 动态出现品类：按字符/拼音升序排列；
 * 3. 兜底归并：category 为空/null、未分类或「其他」的餐品统一归入「默认」；
 * 4. 空数据过滤：餐品数为 0 的分组不返回。
 */
function groupDishesByCategory(dishes) {
    if (!Array.isArray(dishes) || dishes.length === 0) {
        return [];
    }

    const groupsMap = new Map();
    dishes.forEach(d => {
        const rawCat = (d.category || '').trim();
        const isDefault = !rawCat || rawCat === '未分类' || rawCat === '其他' || rawCat === '默认';
        const catName = isDefault ? '默认' : rawCat;

        if (!groupsMap.has(catName)) {
            groupsMap.set(catName, {
                category: catName,
                isDefault: catName === '默认',
                dishes: []
            });
        }
        groupsMap.get(catName).dishes.push(d);
    });

    const knownCats = (dishCategoryList || []).map(c => {
        return (typeof c === 'string' ? c : (c && c.name ? c.name : '')).trim();
    }).filter(Boolean);

    const sortedGroups = Array.from(groupsMap.values()).sort((a, b) => {
        const idxA = knownCats.indexOf(a.category);
        const idxB = knownCats.indexOf(b.category);

        if (idxA !== -1 && idxB !== -1) {
            return idxA - idxB;
        }
        if (idxA !== -1 && idxB === -1) {
            return -1;
        }
        if (idxA === -1 && idxB !== -1) {
            return 1;
        }
        return a.category.localeCompare(b.category, 'zh-CN');
    });

    sortedGroups.forEach(g => {
        const count = g.dishes.length;
        let sumPrice = 0;
        let sumCost = 0;

        g.dishes.forEach(d => {
            sumPrice += Number(d.price) || 0;
            sumCost += Number(d.theoretical_cost) || 0;
        });

        g.count = count;
        g.avgPrice = count > 0 ? (sumPrice / count) : 0;
        g.avgTheoreticalCost = count > 0 ? (sumCost / count) : 0;
        g.avgMarginRate = (g.avgPrice > 0)
            ? ((g.avgPrice - g.avgTheoreticalCost) / g.avgPrice) * 100
            : 0;
    });

    return sortedGroups;
}

/**
 * 渲染单个餐品卡片 HTML
 */
function renderDishCardHtml(d) {
    const isActive = d.status === 'active';
    const statusBadge = isActive
        ? '<span class="badge badge-success" style="font-size:0.72rem;">在售</span>'
        : '<span class="badge badge-neutral" style="font-size:0.72rem; color:var(--text-muted);">已停用</span>';

    const categoryTag = d.category
        ? '<span class="badge badge-info" style="font-size:0.72rem; background: rgba(37,99,235,0.1); color:#2563eb; max-width:110px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; vertical-align:middle;" title="' + w2Escape(d.category) + '">' + w2Escape(d.category) + '</span>'
        : '';

    const marginRate = Number(d.gross_margin_rate || 0);
    const marginBadgeClass = marginClass(marginRate);

    let ingredientsHtml = '';
    if (Array.isArray(d.ingredients) && d.ingredients.length > 0) {
        ingredientsHtml = d.ingredients.map(ing => {
            return '<span class="badge badge-neutral" style="font-size:0.75rem; background:var(--bg-subtle, #f1f5f9); color:var(--text-main); margin-right:4px; margin-bottom:4px; display:inline-block;">'
                + w2Escape(ing.sku_name || ('食材#' + ing.sku_id)) + ': '
                + w2Escape(String(ing.consumption_qty) + (ing.unit || ''))
                + ' <span style="color:var(--text-muted); font-size:0.7rem;">(' + I18N.fmtMoney(ing.ingredient_cost) + ')</span>'
                + '</span>';
        }).join('');
    } else {
        ingredientsHtml = '<span style="font-size:0.78rem; color:var(--text-muted); font-style:italic;">未配置食材配方</span>';
    }

    return '<div class="card dish-card" style="display:flex; flex-direction:column; justify-content:space-between; border-top: 3px solid ' + (isActive ? 'var(--primary, #0f766e)' : 'var(--border-color)') + ';">'
        + '<div>'
        + '  <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px; gap:8px;">'
        + '    <div>'
        + '      <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:4px;">'
        + categoryTag
        + statusBadge
        + '      </div>'
        + '      <h4 style="margin:0; font-size:1.05rem; font-weight:600; color:var(--text-main);">' + w2Escape(d.name) + '</h4>'
        + '    </div>'
        + '    <div style="text-align:right;">'
        + '      <div style="font-size:1.1rem; font-weight:700; color:var(--primary, #0f766e);">' + I18N.fmtMoney(d.price) + '</div>'
        + '      <div style="font-size:0.72rem; color:var(--text-muted);">建议售价</div>'
        + '    </div>'
        + '  </div>'
        + (d.description ? ('<div style="font-size:0.82rem; color:var(--text-muted); margin-bottom:12px; line-height:1.4;">' + w2Escape(d.description) + '</div>') : '')
        + '  <div class="dish-metrics-box" style="background:var(--bg-subtle, #f8fafc); border-radius:6px; padding:8px 10px; margin-bottom:12px; display:grid; grid-template-columns:repeat(3, 1fr); gap:6px; text-align:center;">'
        + '    <div>'
        + '      <div style="font-size:0.72rem; color:var(--text-muted);">理论单份成本</div>'
        + '      <div style="font-size:0.88rem; font-weight:600; color:var(--text-main);">' + I18N.fmtMoney(d.theoretical_cost) + '</div>'
        + '    </div>'
        + '    <div>'
        + '      <div style="font-size:0.72rem; color:var(--text-muted);">单份理论毛利</div>'
        + '      <div style="font-size:0.88rem; font-weight:600; color:var(--success, #16a34a);">' + I18N.fmtMoney(d.gross_profit) + '</div>'
        + '    </div>'
        + '    <div>'
        + '      <div style="font-size:0.72rem; color:var(--text-muted);">理论毛利率</div>'
        + '      <div><span class="badge ' + marginBadgeClass + '" style="font-size:0.72rem;">' + marginRate.toFixed(1) + '%</span></div>'
        + '    </div>'
        + '  </div>'
        + '  <div style="margin-bottom:12px;">'
        + '    <div style="font-size:0.78rem; font-weight:600; color:var(--text-secondary); margin-bottom:6px;">BOM 食材清单 (' + (d.ingredients ? d.ingredients.length : 0) + ' 种):</div>'
        + '    <div style="display:flex; flex-wrap:wrap;">' + ingredientsHtml + '</div>'
        + '  </div>'
        + '</div>'
        + '<div style="display:flex; justify-content:flex-end; gap:8px; border-top:1px solid var(--border-color); padding-top:10px; margin-top:8px;">'
        + (isOwnerRoleNow()
            ? ('  <button type="button" class="btn btn-secondary" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px;" onclick="openDishModal(' + d.id + ')">编辑配方</button>'
               + '  <button type="button" class="btn btn-danger" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px;" onclick=\'deleteDish(' + d.id + ', ' + jsStr(d.name) + ', ' + jsStr(d.status) + ')\'>' + (isActive ? '停用' : '删除') + '</button>')
            : ('  <button type="button" class="btn btn-secondary" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px;" onclick="openDishModal(' + d.id + ')">查看配方</button>'))
        + '</div>'
        + '</div>';
}

/**
 * 渲染单个餐品表格行 HTML
 */
function renderDishTableRowHtml(d) {
    const isActive = d.status === 'active';
    const marginRate = Number(d.gross_margin_rate || 0);
    const marginBadgeClass = marginClass(marginRate);

    const ingSummary = (d.ingredients || []).map(ing => (ing.sku_name || ('食材#' + ing.sku_id)) + ' ' + ing.consumption_qty + ing.unit).join('、') || '-';

    return '<tr>'
        + '<td><strong>' + w2Escape(d.name) + '</strong>' + (!isActive ? ' <span class="badge badge-neutral" style="font-size:0.7rem;">已停用</span>' : '') + '</td>'
        + '<td>' + (d.category ? ('<span class="badge badge-info">' + w2Escape(d.category) + '</span>') : '-') + '</td>'
        + '<td class="col-right" style="font-weight:600;">' + I18N.fmtMoney(d.price) + '</td>'
        + '<td style="font-size:0.82rem; color:var(--text-secondary); max-width:240px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + w2Escape(ingSummary) + '">' + w2Escape(ingSummary) + '</td>'
        + '<td class="col-right">' + I18N.fmtMoney(d.theoretical_cost) + '</td>'
        + '<td class="col-right"><span class="badge ' + marginBadgeClass + '">' + marginRate.toFixed(1) + '%</span></td>'
        + '<td class="col-center">'
        + (isOwnerRoleNow()
            ? ('  <button type="button" class="btn btn-secondary" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px; margin-right:4px;" onclick="openDishModal(' + d.id + ')">编辑</button>'
               + '  <button type="button" class="btn btn-danger" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px;" onclick=\'deleteDish(' + d.id + ', ' + jsStr(d.name) + ', ' + jsStr(d.status) + ')\'>' + (isActive ? '停用' : '删除') + '</button>')
            : ('  <button type="button" class="btn btn-secondary" style="padding:4px 12px; font-size:0.85rem; min-height:38px; height:38px;" onclick="openDishModal(' + d.id + ')">查看配方</button>'))
        + '</td>'
        + '</tr>';
}

/**
 * 渲染品类折叠项头部 HTML
 */
function renderDishCategoryHeaderHtml(group, isCollapsed) {
    const badgeClass = group.isDefault ? 'badge-neutral' : 'badge-info';
    return '<div class="dish-category-header" onclick=\'toggleDishCategoryAccordion(' + jsStr(group.category) + ')\'>'
        + '  <div class="dish-category-header-left">'
        + '    <span class="dish-category-chevron">'
        + '      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        + '        <path d="M6 9l6 6 6-6"/>'
        + '      </svg>'
        + '    </span>'
        + '    <span class="dish-category-title">' + w2Escape(group.category) + '</span>'
        + '    <span class="badge ' + badgeClass + ' dish-category-count-badge">' + group.count + ' 道餐品</span>'
        + '  </div>'
        + '  <div class="dish-category-header-right">'
        + '    <span class="dish-category-summary-kpi">均价 ' + I18N.fmtMoney(group.avgPrice) + ' · 平均理论毛利 ' + group.avgMarginRate.toFixed(1) + '%</span>'
        + '    <span class="dish-category-status-indicator">' + (isCollapsed ? '[已折叠]' : '[已展开]') + '</span>'
        + '  </div>'
        + '</div>';
}

/**
 * 渲染餐品卡片网格（按品类手风琴折叠）
 */
function renderDishCards(dishes) {
    const grid = document.getElementById('dishCardGrid');
    if (!grid) return;

    if (!dishes || dishes.length === 0) {
        grid.innerHTML = '<div style="padding: 40px 20px; text-align: center; color: var(--text-muted); background: var(--bg-card); border-radius: 8px; border: 1px dashed var(--border-color);">'
            + '<div style="font-size: 0.95rem; font-weight: 500;">暂无匹配的餐品数据</div>'
            + '<div style="font-size: 0.8rem; margin-top: 4px;">可点击右上角「+ 新建餐品」创建您的第一个餐品 BOM 配方</div>'
            + '</div>';
        return;
    }

    const groups = groupDishesByCategory(dishes);
    let html = '';
    groups.forEach(group => {
        const isCollapsed = dishCategoryAccordionState[group.category] === false;
        let cardsHtml = '';
        group.dishes.forEach(d => {
            cardsHtml += renderDishCardHtml(d);
        });

        html += '<div class="dish-category-group' + (isCollapsed ? ' collapsed' : '') + '" data-category="' + w2Escape(group.category) + '">'
            + renderDishCategoryHeaderHtml(group, isCollapsed)
            + '<div class="dish-category-body">'
            + '  <div class="dish-card-grid-inner">'
            + cardsHtml
            + '  </div>'
            + '</div>'
            + '</div>';
    });

    grid.innerHTML = html;
}

/**
 * 渲染餐品表格视图（按品类手风琴折叠，并同步全量行到隐藏 #dishTableBody 兼容打印）
 */
function renderDishTable(dishes) {
    const tableView = document.getElementById('dishTableView');
    const hiddenTbody = document.getElementById('dishTableBody');

    if (!dishes || dishes.length === 0) {
        if (tableView) {
            tableView.innerHTML = '<div style="padding: 40px 20px; text-align: center; color: var(--text-muted); background: var(--bg-card); border-radius: 8px; border: 1px dashed var(--border-color);">'
                + '<div style="font-size: 0.95rem; font-weight: 500;">暂无匹配的餐品数据</div>'
                + '<div style="font-size: 0.8rem; margin-top: 4px;">可点击右上角「+ 新建餐品」创建您的第一个餐品 BOM 配方</div>'
                + '</div>';
        }
        if (hiddenTbody) {
            hiddenTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:30px; color:var(--text-muted);">暂无匹配餐品数据</td></tr>';
        }
        return;
    }

    const groups = groupDishesByCategory(dishes);
    let viewHtml = '';
    let allRowsHtml = '';

    groups.forEach(group => {
        const isCollapsed = dishCategoryAccordionState[group.category] === false;
        let groupRowsHtml = '';
        group.dishes.forEach(d => {
            const rowHtml = renderDishTableRowHtml(d);
            groupRowsHtml += rowHtml;
            allRowsHtml += rowHtml;
        });

        viewHtml += '<div class="dish-category-group' + (isCollapsed ? ' collapsed' : '') + '" data-category="' + w2Escape(group.category) + '">'
            + renderDishCategoryHeaderHtml(group, isCollapsed)
            + '<div class="dish-category-body">'
            + '  <div class="table-container">'
            + '    <table class="data-table">'
            + '      <thead>'
            + '        <tr>'
            + '          <th>餐品名称</th>'
            + '          <th>分类</th>'
            + '          <th class="col-right">建议售价</th>'
            + '          <th>食材配方组成</th>'
            + '          <th class="col-right">理论单份成本</th>'
            + '          <th class="col-right">理论毛利率</th>'
            + '          <th class="col-center">操作</th>'
            + '        </tr>'
            + '      </thead>'
            + '      <tbody>'
            + groupRowsHtml
            + '      </tbody>'
            + '    </table>'
            + '  </div>'
            + '</div>'
            + '</div>';
    });

    if (tableView) {
        tableView.innerHTML = viewHtml;
    }
    if (hiddenTbody) {
        hiddenTbody.innerHTML = allRowsHtml;
    }
}

/**
 * 切换单个品类折叠项状态（展开/折叠）
 */
function toggleDishCategoryAccordion(category) {
    const isCurrentlyCollapsed = dishCategoryAccordionState[category] === false;
    const nextState = isCurrentlyCollapsed; // 从折叠切换为展开 (true)，从展开切换为折叠 (false)
    dishCategoryAccordionState[category] = nextState;

    updateCategoryAccordionDom(category, nextState);
    updateToggleAllDishCategoriesBtn();
}

/**
 * 更新 DOM 中指定品类的折叠与状态指示器
 */
function updateCategoryAccordionDom(category, isExpanded) {
    const groups = document.querySelectorAll('.dish-category-group');
    groups.forEach(group => {
        if (group.getAttribute('data-category') === category) {
            if (isExpanded) {
                group.classList.remove('collapsed');
            } else {
                group.classList.add('collapsed');
            }
            const indicator = group.querySelector('.dish-category-status-indicator');
            if (indicator) {
                indicator.textContent = isExpanded ? '[已展开]' : '[已折叠]';
            }
        }
    });
}

/**
 * 刷新「全部展开 / 全部折叠」按钮文案与状态
 */
function updateToggleAllDishCategoriesBtn() {
    const btn = document.getElementById('btnToggleAllDishCategories');
    if (!btn) return;

    const catFilter = document.getElementById('dishCategoryFilter');
    const selectedCat = catFilter ? catFilter.value : '';

    if (selectedCat) {
        btn.disabled = true;
        btn.style.opacity = '0.5';
        btn.style.cursor = 'not-allowed';
        btn.innerText = '全部折叠';
        return;
    }
    btn.disabled = false;
    btn.style.opacity = '';
    btn.style.cursor = '';

    const groups = Array.from(document.querySelectorAll('#dishCardGrid .dish-category-group'));
    if (groups.length === 0) {
        btn.innerText = '全部折叠';
        btn.disabled = true;
        return;
    }

    const anyExpanded = groups.some(g => !g.classList.contains('collapsed'));
    btn.innerText = anyExpanded ? '全部折叠' : '全部展开';
}

/**
 * 全局一键展开全部或折叠全部品类手风琴
 */
function toggleAllDishCategoryAccordions() {
    const groups = Array.from(document.querySelectorAll('#dishCardGrid .dish-category-group'));
    if (groups.length === 0) return;

    const anyExpanded = groups.some(g => !g.classList.contains('collapsed'));
    const targetState = !anyExpanded;

    const updatedCats = new Set();
    groups.forEach(g => {
        const cat = g.getAttribute('data-category');
        if (cat) {
            dishCategoryAccordionState[cat] = targetState;
            updatedCats.add(cat);
            updateCategoryAccordionDom(cat, targetState);
        }
    });

    const tableGroups = Array.from(document.querySelectorAll('#dishTableView .dish-category-group'));
    tableGroups.forEach(g => {
        const cat = g.getAttribute('data-category');
        if (cat && !updatedCats.has(cat)) {
            dishCategoryAccordionState[cat] = targetState;
            updateCategoryAccordionDom(cat, targetState);
        }
    });

    updateToggleAllDishCategoriesBtn();
}

/**
 * 按搜索关键词、分类与在售状态筛选餐品 BOM 库
 */
function filterDishBomLibrary() {
    const qInput = document.getElementById('dishSearchInput');
    const catSelect = document.getElementById('dishCategoryFilter');
    const statusSelect = document.getElementById('dishStatusFilter');

    const q = (qInput ? qInput.value : '').trim().toLowerCase();
    const cat = catSelect ? catSelect.value : '';
    const status = statusSelect ? statusSelect.value : 'active';

    let filtered = dishLibraryCache.filter(d => {
        if (status === 'active' && d.status !== 'active') return false;
        if (status === 'inactive' && d.status !== 'inactive') return false;
        if (cat && (d.category || '') !== cat) return false;
        if (q) {
            const nameMatch = (d.name || '').toLowerCase().includes(q);
            const descMatch = (d.description || '').toLowerCase().includes(q);
            const catMatch = (d.category || '').toLowerCase().includes(q);
            const ingMatch = Array.isArray(d.ingredients) && d.ingredients.some(ing => {
                const sName = (ing.sku_name || '').toLowerCase();
                return sName.includes(q);
            });
            if (!nameMatch && !descMatch && !catMatch && !ingMatch) return false;
        }
        return true;
    });

    // 搜索词非空时，智能自动展开命中餐品的品类
    if (q) {
        filtered.forEach(d => {
            const rawCat = (d.category || '').trim();
            const catName = rawCat || '未分类';
            dishCategoryAccordionState[catName] = true;
        });
    }

    // 筛选指定分类时，该分类强制展开
    if (cat) {
        dishCategoryAccordionState[cat] = true;
    }

    renderDishCards(filtered);
    renderDishTable(filtered);
    updateToggleAllDishCategoriesBtn();
}

// 挂载到全局 window 对象供各视图与测试脚本调用
window.groupDishesByCategory = groupDishesByCategory;
window.renderDishCards = renderDishCards;
window.renderDishTable = renderDishTable;
window.filterDishBomLibrary = filterDishBomLibrary;
window.toggleDishCategoryAccordion = toggleDishCategoryAccordion;
window.toggleAllDishCategoryAccordions = toggleAllDishCategoryAccordions;
window.updateToggleAllDishCategoriesBtn = updateToggleAllDishCategoriesBtn;


/**
 * 切换餐品 BOM 库卡片/表格视图
 */
function toggleDishViewMode() {
    const cardGrid = document.getElementById('dishCardGrid');
    const tableView = document.getElementById('dishTableView');
    const toggleBtn = document.getElementById('btnToggleDishView');

    if (dishViewMode === 'card') {
        dishViewMode = 'table';
        if (cardGrid) cardGrid.classList.add('hide');
        if (tableView) tableView.classList.remove('hide');
        if (toggleBtn) toggleBtn.innerText = '切换卡片视图';
    } else {
        dishViewMode = 'card';
        if (cardGrid) cardGrid.classList.remove('hide');
        if (tableView) tableView.classList.add('hide');
        if (toggleBtn) toggleBtn.innerText = '切换列表视图';
    }
}

/**
 * 打印餐品 BOM 标准配方档案（B2：原数据中心 dish_bom_recipes 报表移入餐品管理，
 * 复用配方库列表视图的渲染结果出简单打印页；数据未加载时给温和提示）。
 */
function printDishBomLibrary() {
    const table = document.getElementById('dishTableView')?.querySelector('table');
    const tbody = document.getElementById('dishTableBody');
    if (!table || !tbody || !tbody.children.length) {
        showToast('配方库数据尚未加载完成，请稍候再试', 'warning');
        return;
    }
    // 表格内容取自 renderDishTable 已用 w2Escape 转义的可信 HTML；标题为受控常量
    const thead = table.querySelector('thead')?.innerHTML || '';
    const body = tbody.innerHTML;

    const printWin = window.open('', '_blank', 'width=960,height=720');
    if (!printWin) {
        showToast('弹出打印窗口被拦截，请在浏览器设置中允许弹窗后重试', 'warning');
        return;
    }
    printWin.document.write(`
        <!DOCTYPE html>
        <html>
        <head>
            <title>${uiText('餐品 BOM 标准配方档案')}</title>
            <meta charset="utf-8">
            <link id="printSheet" rel="stylesheet" href="${location.origin}/static/css/print-dish.css">
        </head>
        <body>
            <h2>${uiText('餐品 BOM 标准配方档案')}</h2>
            <div class="meta-bar">${uiText('打印时间')}: ${new Date().toLocaleString(printLocaleTag(), { hour12: false })}</div>
            <table><thead>${window.CSPBind ? window.CSPBind.rewriteHtml(thead) : thead}</thead><tbody>${window.CSPBind ? window.CSPBind.rewriteHtml(body) : body}</tbody></table>
            <div class="print-footer">
                <div>${uiText('经办人签字')}：_______________</div>
                <div>${uiText('日期')}：_______________</div>
            </div>

        </body>
        </html>
    `);
    // 打印窗口是独立文档，不在主页面 DOM 翻译器的观察范围内。
    if (window.I18N) window.I18N.translateDocument(printWin.document);
    printWin.document.close();
    if (window.CSPBind && printWin.document && printWin.document.body) {
        try { window.CSPBind.applyStyles(printWin.document.body); } catch (e) { /* print fragment */ }
    }
    var printSheet = (printWin.document && typeof printWin.document.getElementById === 'function')
        ? printWin.document.getElementById('printSheet') : null;
    var firePrint = function () {
        if (typeof printWin.focus === 'function') printWin.focus();
        if (typeof printWin.print === 'function') printWin.print();
        setTimeout(function () { try { printWin.close(); } catch (err) { /* popup already gone */ } }, 500);
    };
    if (printSheet && !printSheet.sheet && typeof printSheet.addEventListener === 'function') {
        printSheet.addEventListener('load', firePrint);
        printSheet.addEventListener('error', firePrint);
    } else {
        firePrint();
    }
}

/**
 * 打开餐品建档 / 编辑配方 Modal
 */
function openDishModal(dishId) {
    const modal = document.getElementById('dishEditModal');
    const titleEl = document.getElementById('dishEditModalTitle');
    const errEl = document.getElementById('dishModalError');
    if (errEl) { errEl.innerText = ''; errEl.classList.add('hide'); }

    const idInput = document.getElementById('dishModalId');
    const nameInput = document.getElementById('dishModalName');
    const catInput = document.getElementById('dishModalCategory');
    const priceInput = document.getElementById('dishModalPrice');
    const descInput = document.getElementById('dishModalDescription');
    const statusInput = document.getElementById('dishModalStatus');
    const rowsBody = document.getElementById('dishIngredientRowsBody');

    if (rowsBody) rowsBody.innerHTML = '';

    const applyDishModalRoleState = () => {
        const isOwner = isOwnerRoleNow();
        const btnSaveElem = document.getElementById('btnSaveDishModal');
        const modalCard = modal ? modal.querySelector('.modal-card') : null;
        const btnCancelElem = document.getElementById('btnCancelDishModal') || (modal ? modal.querySelector('.modal-body button[data-csp-click*="dishEditModal"]') : null);
        const headerCloseBtn = modal ? modal.querySelector('.modal-header .modal-close-btn') : null;
        if (headerCloseBtn) {
            headerCloseBtn.innerText = 'x';
            headerCloseBtn.removeAttribute('style');
        }
        const btnAddIngElem = modal ? modal.querySelector('button[data-csp-click*="addDishIngredientRow"]') : null;

        let staffNotice = document.getElementById('dishModalStaffNotice');
        if (!isOwner) {
            if (titleEl) titleEl.innerText = '餐品配方详情（店员查阅）';
            if (!staffNotice && modalCard) {
                staffNotice = document.createElement('div');
                staffNotice.id = 'dishModalStaffNotice';
                staffNotice.style.cssText = 'margin-bottom:12px; padding:10px 14px; font-size:0.85rem; border-left:4px solid var(--primary, #0f766e); background:var(--bg-subtle, #f0fdf4); color:var(--text-main); border-radius:4px;';
                staffNotice.innerText = '[提示] 店员仅支持查阅食材用量与规格。如需调整配方或售价，请联系老板。';
                const bodyElem = modalCard.querySelector('.modal-body');
                if (bodyElem) bodyElem.insertBefore(staffNotice, bodyElem.firstChild);
            } else if (staffNotice) {
                staffNotice.classList.remove('hide');
            }

            if (nameInput) nameInput.disabled = true;
            if (catInput) catInput.disabled = true;
            if (priceInput) priceInput.disabled = true;
            if (descInput) descInput.disabled = true;
            if (statusInput) statusInput.disabled = true;

            if (btnAddIngElem) btnAddIngElem.classList.add('hide');
            if (btnSaveElem) btnSaveElem.classList.add('hide');

            if (btnCancelElem) {
                btnCancelElem.innerText = '关闭';
                btnCancelElem.style.cssText = 'min-height:42px; height:42px; min-width:90px; padding:0 24px; font-size:0.95rem;';
            }

            if (rowsBody) {
                rowsBody.querySelectorAll('input, select').forEach(el => { el.disabled = true; });
                rowsBody.querySelectorAll('button').forEach(btn => { btn.classList.add('hide'); });
            }
        } else {
            if (staffNotice) staffNotice.classList.add('hide');

            if (nameInput) nameInput.disabled = false;
            if (catInput) catInput.disabled = false;
            if (priceInput) priceInput.disabled = false;
            if (descInput) descInput.disabled = false;
            if (statusInput) statusInput.disabled = false;

            if (btnAddIngElem) btnAddIngElem.classList.remove('hide');
            if (btnSaveElem) btnSaveElem.classList.remove('hide');

            if (btnCancelElem) {
                btnCancelElem.innerText = '取消';
                btnCancelElem.style.cssText = 'min-height:38px; height:38px; min-width:auto; padding:0 16px; font-size:0.875rem;';
            }

            if (rowsBody) {
                rowsBody.querySelectorAll('input, select').forEach(el => { el.disabled = false; });
                rowsBody.querySelectorAll('button').forEach(btn => { btn.classList.remove('hide'); });
            }
        }
    };

    const prepareModal = () => {
        if (!dishCategoryList || dishCategoryList.length === 0) {
            refreshDishCategoryOptions();
        } else {
            updateDishCategorySuggestions();
        }
        if (dishId) {
            if (titleEl) titleEl.innerText = isOwnerRoleNow() ? '编辑餐品与配方' : '餐品配方详情（店员查阅）';
            const dish = dishLibraryCache.find(d => d.id === Number(dishId));
            if (dish) {
                if (idInput) idInput.value = dish.id;
                if (nameInput) nameInput.value = dish.name || '';
                if (catInput) catInput.value = (dish.category || '').trim() || '默认';
                if (priceInput) priceInput.value = dish.price != null ? dish.price : '';
                if (descInput) descInput.value = dish.description || '';
                if (statusInput) statusInput.value = dish.status || 'active';

                if (Array.isArray(dish.ingredients) && dish.ingredients.length > 0) {
                    dish.ingredients.forEach(ing => {
                        addDishIngredientRow(ing.sku_id, ing.consumption_qty, ing.unit, ing.notes, ing.component_type || 'sku', (ing.component_id != null ? ing.component_id : ing.sku_id), ing.yield_rate);
                    });
                } else {
                    addDishIngredientRow();
                }
                calcDishModalTheoryCost();
                applyDishModalRoleState();
                openModalById('dishEditModal');
            } else {
                apiFetch('/api/dishes/' + dishId)
                    .then(res => res.json())
                    .then(ret => {
                        if (ret.status !== 'success') {
                            showToast(ret.msg || '获取餐品详情失败', 'error');
                            return;
                        }
                        const d = ret.data;
                        if (idInput) idInput.value = d.id;
                        if (nameInput) nameInput.value = d.name || '';
                        if (catInput) catInput.value = (d.category || '').trim() || '默认';
                        if (priceInput) priceInput.value = d.price != null ? d.price : '';
                        if (descInput) descInput.value = d.description || '';
                        if (statusInput) statusInput.value = d.status || 'active';

                        if (Array.isArray(d.ingredients) && d.ingredients.length > 0) {
                            d.ingredients.forEach(ing => {
                                addDishIngredientRow(ing.sku_id, ing.consumption_qty, ing.unit, ing.notes, ing.component_type || 'sku', (ing.component_id != null ? ing.component_id : ing.sku_id), ing.yield_rate);
                            });
                        } else {
                            addDishIngredientRow();
                        }
                        calcDishModalTheoryCost();
                        applyDishModalRoleState();
                        openModalById('dishEditModal');
                    });
            }
        } else {
            if (titleEl) titleEl.innerText = '新建餐品与配方';
            if (idInput) idInput.value = '';
            if (nameInput) nameInput.value = '';
            if (catInput) catInput.value = '默认';
            if (priceInput) priceInput.value = '';
            if (descInput) descInput.value = '';
            if (statusInput) statusInput.value = 'active';

            addDishIngredientRow();
            calcDishModalTheoryCost();
            applyDishModalRoleState();
            openModalById('dishEditModal');
        }
    };

    if (!dishAvailableSkus || dishAvailableSkus.length === 0) {
        fetchDishAvailableSkus(() => prepareModal());
    } else {
        prepareModal();
    }
}

// U-12：全局挂载辅助函数与并发锁
if (typeof window !== 'undefined') {
    window.handleFileSelect = handleFileSelect;
    window.handleSingleUploadSelection = handleSingleUploadSelection;
    window.handleFilesSelect = handleFilesSelect;
    window.showImagePrepIndicator = showImagePrepIndicator;
    window.queueFilesSelect = queueFilesSelect;
    // Task 4: 餐品管理（配方库族，实现在 main.js）
    window.initDishTab = initDishTab;
    window.switchDishSubtab = switchDishSubtab;
    window.loadDishesList = loadDishesList;
    window.filterDishBomLibrary = filterDishBomLibrary;
    window.toggleDishViewMode = toggleDishViewMode;
    window.printDishBomLibrary = printDishBomLibrary;
    window.openDishModal = openDishModal;

}

// ---- 反馈组件显隐判定（全场景穿透：只要处于复核或错误态即展示） ----
function shouldShowFeedback() {
    const scanBtn = document.querySelector('.sidebar-btn[data-target="tab-scan"]');
    const scanActive = !scanBtn || scanBtn.classList.contains('active');
    if (!scanActive) return false;

    const loading = document.getElementById('loadingCard');
    const preConfirm = document.getElementById('preConfirmCard');
    const isBusy = (loading && !loading.classList.contains('hide'))
        || (preConfirm && !preConfirm.classList.contains('hide'));
    if (isBusy) return false;

    const prefill = document.getElementById('prefillFormCard');
    const errorCard = document.getElementById('errorCard');
    const isPrefill = prefill && !prefill.classList.contains('hide');
    const isError = errorCard && !errorCard.classList.contains('hide');

    return !!(isPrefill || isError);
}
window.shouldShowFeedback = shouldShowFeedback;

// ---- 挽回动作栏（重新解析/重拍）显隐谓词（必须有原图单据上下文） ----
function hasRecognizedResult(data) {
    const scanBtn = document.querySelector('.sidebar-btn[data-target="tab-scan"]');
    const scanActive = !scanBtn || scanBtn.classList.contains('active');
    const loading = document.getElementById('loadingCard');
    const preConfirm = document.getElementById('preConfirmCard');
    const idle = (!loading || loading.classList.contains('hide'))
        && (!preConfirm || preConfirm.classList.contains('hide'));
    const prefill = document.getElementById('prefillFormCard');
    const isPrefill = prefill && !prefill.classList.contains('hide');
    // 有单据且不是新建手工单（无原图），且处于表单就绪态
    return !!(scanActive && currentReceiptId && !isManualEntry && idle && isPrefill);
}
window.hasRecognizedResult = hasRecognizedResult;

// ---- 反馈按钮 UI 高亮联动（双向同步头部、底部、错误态按钮） ----
function syncFeedbackButtonsUI(like) {
    const upBtns = document.querySelectorAll('.feedback-btn-up');
    const downBtns = document.querySelectorAll('.feedback-btn-down');
    upBtns.forEach(btn => {
        if (like === 1) btn.classList.add('active');
        else btn.classList.remove('active');
    });
    downBtns.forEach(btn => {
        if (like === -1) btn.classList.add('active');
        else btn.classList.remove('active');
    });
}
window.syncFeedbackButtonsUI = syncFeedbackButtonsUI;

// ---- 点赞/点踩反馈显隐同步 ----
function syncFeedbackVisibility(data) {
    const show = shouldShowFeedback();
    const prefill = document.getElementById('prefillFormCard');
    const errorCard = document.getElementById('errorCard');
    const isPrefill = prefill && !prefill.classList.contains('hide');
    const isError = errorCard && !errorCard.classList.contains('hide');

    const boxInline = document.getElementById('feedbackInline');
    const boxError = document.getElementById('feedbackErrorCard');

    if (boxInline) boxInline.style.display = (show && isPrefill) ? 'inline-flex' : 'none';
    if (boxError) boxError.style.display = (show && isError) ? 'inline-flex' : 'none';

    syncFeedbackButtonsUI(lastFeedbackForCurrent);
}
window.syncFeedbackVisibility = syncFeedbackVisibility;

// ---- 挽回动作栏显隐：与原图单据上下文绑定（解析中/预确认/手工单隐藏，防并发双击）----
function syncRecoveryVisibility(data) {
    const show = hasRecognizedResult(data);
    ['btnReparse', 'btnRetake'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.style.display = show ? '' : 'none';
    });
    const elRot = document.getElementById('btnReparseRotated');
    if (elRot) elRot.style.display = (show && _hasRotatedAfterParse) ? '' : 'none';
}
window.syncRecoveryVisibility = syncRecoveryVisibility;

// 本会话对当前单据的最后一次点赞/点踩表态（挽回埋点 properties.prior_feedback；
// 新识别结果产生时在 applyRecognizedResult 清零）
let lastFeedbackForCurrent = null;

// ---- 挽回动作：重新解析（模型归因）——复用原图重跑，直接走既有重试链路 ----
function onReparseClick() {
    const rid = currentReceiptId;
    if (!rid) return;
    if (_hasRotatedAfterParse && selectedFile) {
        onReparseRotatedClick();
        return;
    }
    track('reparse_clicked', rid, { prior_feedback: lastFeedbackForCurrent });
    retryReceiptRecognition(rid);
}
window.onReparseClick = onReparseClick;

// ---- 挽回动作：按当前旋转视角重新识别 ----
function onReparseRotatedClick() {
    const rid = currentReceiptId;
    if (!rid) {
        showToast('未找到有效单据编号', 'warning');
        return;
    }
    if (!selectedFile) {
        showToast('旋转图片文件尚未准备就绪，请稍候', 'warning');
        return;
    }
    track('reparse_rotated_clicked', rid, { prior_feedback: lastFeedbackForCurrent });
    submitRotatedImage(rid, selectedFile);
}
window.onReparseRotatedClick = onReparseRotatedClick;

async function submitRotatedImage(receiptId, file) {
    if (!file) {
        showToast('未找到旋转图片文件', 'warning');
        return;
    }
    const fd = new FormData();
    fd.append('receipt', file);
    fd.append('force', 'true');
    const gen = ++singleUploadGen;
    if (singlePollToken) singlePollToken.cancelled = true;
    singlePollToken = { cancelled: false };

    document.getElementById('errorCard').classList.add('hide');
    document.getElementById('preConfirmCard').classList.add('hide');
    document.getElementById('prefillFormCard').classList.add('hide');
    document.getElementById('loadingCard').classList.remove('hide');
    startOcrTimer();
    syncFeedbackVisibility();
    syncRecoveryVisibility();

    try {
        const res = await apiFetch(`/api/receipt/${receiptId}/replace-image`, { method: 'POST', body: fd });
        const body = await res.json().catch(() => ({}));
        if (gen !== singleUploadGen) return;
        const ret = body || {};
        if (res.ok && ret.status === 'queued' && ret.job_id) {
            _hasRotatedFile = false;
            _hasRotatedAfterParse = false;
            const btnRot = document.getElementById('btnReparseRotated');
            if (btnRot) btnRot.style.display = 'none';
            const btnErrRot = document.getElementById('btnRetryRotated');
            if (btnErrRot) btnErrRot.classList.add('hide');
            if (ret.image_url) {
                const img = document.getElementById('previewImg');
                if (img) img.src = ret.image_url;
                const ph = getActivePhoto();
                if (ph) {
                    ph.imageUrl = ret.image_url;
                    if (typeof renderSider === 'function') renderSider();
                }
            }
            pollAndApply(ret, receiptId);
            return;
        }
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        showToast((ret && ret.msg) || '按新视角重新识别失败', 'error');
        if (routeRecognitionFailure(ret, receiptId)) return;
        showErrorCard((ret && ret.msg) || '按新视角重新识别失败', receiptId);
    } catch (err) {
        if (gen !== singleUploadGen) return;
        stopOcrTimer();
        document.getElementById('loadingCard').classList.add('hide');
        console.error('提交旋转图片异常', err);
        showToast('提交旋转图片失败，请检查网络后重试', 'error');
        showErrorCard('提交旋转图片失败: ' + err, receiptId);
    }
}
window.submitRotatedImage = submitRotatedImage;

// ---- 挽回动作：重拍（输入归因）——埋点 → 二次确认 → 选新图换图重跑 ----
function onRetakeClick() {
    const rid = currentReceiptId;
    if (!rid) return;
    track('retake_clicked', rid, { prior_feedback: lastFeedbackForCurrent });
    const ok = confirm('将用新图替换原图并重新解析，当前识别结果与已编辑内容会被覆盖。是否继续？');
    if (!ok) return;
    const fi = document.getElementById('retakeFile');
    if (!fi) return;
    fi.value = '';
    fi.onchange = () => {
        const f = fi.files && fi.files[0];
        fi.value = '';
        fi.onchange = null;
        if (f) submitRetakeImage(rid, f).catch(err => console.error('重拍提交异常', err));
    };
    fi.click();
}
window.onRetakeClick = onRetakeClick;

// 重拍提交：先按需转码非 Web 格式（HEIC/TIFF），再 FormData POST replace-image
// （字段名与 /api/upload 一致）；成功后先刷左图（新图）再轮询识别结果；
// 失败仅 toast，保留当前界面不破坏
async function submitRetakeImage(receiptId, file) {
    // 治本：重拍与「选择文件」共用同一转码通道，避免 iPhone HEIC 原样落库成新的 .heic 单据。
    // 转码失败时 ensureWebDisplayableImageFile 降级返回原文件，不阻断；仅在文件选择代次变化时返回 null
    file = await ensureWebDisplayableImageFile(file);
    if (!file) return;

    const fd = new FormData();
    fd.append('receipt', file);
    // 作废在途轮询，防并发双击（后端 parsing 409 兜底）
    const gen = ++singleUploadGen;
    if (singlePollToken) singlePollToken.cancelled = true;
    singlePollToken = { cancelled: false };

    apiFetch(`/api/receipt/${receiptId}/replace-image`, { method: 'POST', body: fd })
    .then(res => res.json().then(body => ({ res, body })).catch(() => ({ res, body: null })))
    .then(({ res, body }) => {
        if (gen !== singleUploadGen) return;
        const ret = body || {};
        if (res.ok && ret.status === 'queued' && ret.job_id) {
            // 切换到解析中界面（与重试链路一致）
            document.getElementById('errorCard').classList.add('hide');
            document.getElementById('preConfirmCard').classList.add('hide');
            document.getElementById('prefillFormCard').classList.add('hide');
            document.getElementById('loadingCard').classList.remove('hide');
            startOcrTimer();
            syncFeedbackVisibility();
            syncRecoveryVisibility();
            // 轮询前先刷新左图为新图
            if (ret.image_url) {
                const img = document.getElementById('previewImg');
                if (img) img.src = ret.image_url;
                const ph = getActivePhoto();
                if (ph) {
                    ph.imageUrl = ret.image_url;
                    if (typeof renderSider === 'function') renderSider();
                }
            }
            pollAndApply(ret, receiptId);
            return;
        }
        const msg = (ret && ret.msg) || '重拍失败，请稍后重试';
        showToast(msg, 'error');
    })
    .catch(err => {
        if (gen !== singleUploadGen) return;
        console.error('重拍请求异常', err);
        showToast('重拍请求失败，请检查网络后重试', 'error');
    });
}
window.submitRetakeImage = submitRetakeImage;

// ---- 点赞/点踩反馈按钮（头部/底部/错误卡片通用，全场景支持）----
function onFeedbackClick(like, source) {
    const rid = (typeof currentReceiptId === 'number' && currentReceiptId > 0)
        ? currentReceiptId
        : (currentReceiptId && Number(currentReceiptId) > 0 ? Number(currentReceiptId) : null);

    const errorCard = document.getElementById('errorCard');
    const isError = errorCard && !errorCard.classList.contains('hide');
    const activeSource = source || (isError ? 'error_card' : 'prefill_footer');

    const itemsCount = (currentReceiptData && currentReceiptData.items && currentReceiptData.items.length)
        || (document.getElementById('itemTableBody') ? document.getElementById('itemTableBody').children.length : 0);

    lastFeedbackForCurrent = like;
    syncFeedbackButtonsUI(like);

    const okMsg = like === 1 ? '感谢您的点赞反馈！' : '已收到您的改进建议，感谢反馈！';

    if (rid) {
        const tenantId = _feedbackTenantId();
        apiFetch(`/api/receipt/${rid}/feedback`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenantId },
            body: JSON.stringify({ like: like, comment: '', tenant_id: tenantId }),
        })
            .then(r => r.json())
            .then(ret => {
                if (ret && ret.status === 'success') {
                    track('receipt_feedback_submitted', rid, {
                        like: like,
                        source: activeSource,
                        is_manual: !!isManualEntry,
                        items_count: itemsCount,
                    });
                    showToast(okMsg, like === 1 ? 'success' : 'info');
                } else {
                    showToast('反馈已记录，我们会持续优化', 'info');
                }
            })
            .catch(() => {
                showToast('反馈已记录，我们会持续优化', 'info');
            });
    } else {
        track('feedback_received', null, {
            like: like,
            scene: isManualEntry ? 'manual_entry' : 'pre_receipt_error',
            source: activeSource,
            role: localStorage.getItem('demo_role') || 'owner',
            tab: 'tab-scan',
        });
        showToast(okMsg, like === 1 ? 'success' : 'info');
    }
}
window.onFeedbackClick = onFeedbackClick;


// =====================================================================
