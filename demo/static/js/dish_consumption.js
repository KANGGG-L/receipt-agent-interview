// -*- coding: utf-8 -*-
// 餐品日常消耗 / 配方版本 / 消耗成本分析（F3 B6-2 自 main.js 抽出，函数体逐字节未改）。
// 依赖 main.js 的 apiFetch / w2Escape / showToast / showCustomConfirmModal / isOwnerRoleNow /
//   openModalById / closeModalById / marginTextColor / marginClass / todayLocalStr / fmtLocalDateStr /
//   convertUnitQtyFrontend / loadDishesList / loadInventoryData / fetchDishAvailableSkus /
//   dishLibraryCache / dishAvailableSkus / dailyConsumptionCache / dailyKpiSummaryCache /
//   dishAnalysisDays / dishDailyDishesCache。
// 加载顺序：main.js -> dish_consumption.js（index.html 中位于 supplier_archive.js 之后）。

/**
 * 构建配方组件下拉（食材 SKU + 子配方餐品两个分组）。
 * option value 编码为 "sku:<id>" / "dish:<id>"，与后端 component_type/component_id 对应。
 */
function dishComponentOptionsHtml(selectedValue) {
    let skuOpts = '';
    (dishAvailableSkus || []).forEach(s => {
        const val = 'sku:' + s.id;
        const isSel = (selectedValue === val);
        skuOpts += '<option value="' + val + '"'
            + (isSel ? ' selected' : '')
            + ' data-unit="' + w2Escape(s.base_unit || '') + '"'
            + ' data-price="' + (s.last_unit_price || 0) + '"'
            + ' data-type="sku"'
            + ' data-stock="' + (s.current_stock || 0) + '"'
            + ' data-category="' + w2Escape(s.category || '') + '">'
            + w2Escape(s.name) + ' (' + (s.category || '通用') + ' ｜ 库存: ' + (s.current_stock || 0) + (s.base_unit || '') + ' ｜ ' + I18N.fmtMoney(s.last_unit_price) + '/' + (s.base_unit || '') + ')'
            + '</option>';
    });

    const selfId = Number((document.getElementById('dishModalId') || {}).value || 0) || null;
    let dishOpts = '';
    (dishLibraryCache || []).forEach(d => {
        if (selfId != null && Number(d.id) === selfId) return;   // 自引用禁止
        const val = 'dish:' + d.id;
        const isSel = (selectedValue === val);
        dishOpts += '<option value="' + val + '"'
            + (isSel ? ' selected' : '')
            + ' data-unit="份"'
            + ' data-price="' + (d.theoretical_cost || 0) + '"'
            + ' data-type="dish"'
            + ' data-category="子配方">'
            + w2Escape(d.name) + ' (子配方 ｜ 单份理论成本 ' + I18N.fmtMoney(d.theoretical_cost) + ')'
            + '</option>';
    });

    return '<option value="">-- 请选择食材 / 子配方 --</option>'
        + '<optgroup label="食材">' + skuOpts + '</optgroup>'
        + '<optgroup label="子配方 (餐品)">' + dishOpts + '</optgroup>';
}

/**
 * 动态向配方表格添加一行组件（食材 SKU 或子配方餐品）。
 * 兼容旧签名 addDishIngredientRow(skuId, qty, unit, notes)：
 * 传 componentType='dish' + componentId 即子配方行。
 */
function addDishIngredientRow(skuId, qty, unit, notes, componentType, componentId, yieldRate) {
    const tbody = document.getElementById('dishIngredientRowsBody');
    if (!tbody) return;

    const tr = document.createElement('tr');
    tr.className = 'dish-ing-row';

    const ctype = (componentType || 'sku');
    const cid = (componentId != null ? componentId : skuId);
    const selectedValue = (cid != null && cid !== '') ? (ctype + ':' + cid) : '';
    const optionsHtml = dishComponentOptionsHtml(selectedValue);

    const selectedOpt = (function () {
        const tmp = document.createElement('div');
        tmp.innerHTML = optionsHtml;
        const sel = tmp.querySelector('option[value="' + selectedValue + '"]');
        return sel;
    })();
    const initUnit = unit != null
        ? unit
        : (selectedOpt ? (selectedOpt.getAttribute('data-unit') || '') : '');
    const initQty = qty != null ? qty : '';
    const initYield = (yieldRate != null && yieldRate !== '') ? yieldRate : 1;
    const initRefPrice = selectedOpt
        ? (I18N.fmtMoney(parseFloat(selectedOpt.getAttribute('data-price')) || 0)
           + '/' + (selectedOpt.getAttribute('data-unit') || ''))
        : '-';
    const typeLabel = ctype === 'dish' ? '子配方' : (selectedValue ? '食材' : '-');
    const isOwner = isOwnerRoleNow();

    tr.innerHTML = '<td>'
        + '  <select class="form-control ing-sku-select" onchange="onDishIngredientSkuChange(this)"' + (!isOwner ? ' disabled' : '') + '>'
        + optionsHtml
        + '  </select>'
        + '</td>'
        + '<td class="col-center">'
        + '  <span class="ing-type-label badge badge-secondary" style="font-size:0.72rem;">' + w2Escape(typeLabel) + '</span>'
        + '</td>'
        + '<td>'
        + '  <input type="number" step="0.0001" min="0.0001" class="form-control ing-qty-input col-right" placeholder="0.00" value="' + initQty + '" oninput="calcDishModalTheoryCost()"' + (!isOwner ? ' disabled' : '') + '>'
        + '</td>'
        + '<td>'
        + '  <input type="text" class="form-control ing-unit-input col-center" placeholder="单位" value="' + w2Escape(initUnit) + '" oninput="calcDishModalTheoryCost()"' + (!isOwner ? ' disabled' : '') + '>'
        + '</td>'
        + '<td>'
        + '  <input type="number" step="0.0001" min="0.0001" max="1" class="form-control ing-yield-input col-right" placeholder="1.0" value="' + initYield + '" title="出成率 0-1；有效领料量 = 配方用量 / 出成率" oninput="calcDishModalTheoryCost()"' + (!isOwner ? ' disabled' : '') + '>'
        + '</td>'
        + '<td class="col-right">'
        + '  <span class="ing-ref-price" style="font-size:0.85rem; color:var(--text-muted);">' + initRefPrice + '</span>'
        + '</td>'
        + '<td class="col-right">'
        + '  <span class="ing-item-cost" style="font-weight:600; color:var(--primary, #0f766e);">$0.00</span>'
        + '</td>'
        + '<td class="col-center">'
        + (isOwner ? '  <button type="button" class="btn btn-danger" style="padding:2px 8px; font-size:0.75rem;" onclick="removeDishIngredientRow(this)">删除</button>' : '')
        + '</td>';

    tbody.appendChild(tr);
    calcDishModalTheoryCost();
}

/**
 * 删除一行配方组件
 */
function removeDishIngredientRow(btn) {
    const tr = btn.closest('tr');
    if (tr && tr.parentNode) {
        tr.parentNode.removeChild(tr);
        calcDishModalTheoryCost();
    }
}

/**
 * 监听配方行组件切换（食材 / 子配方）
 */
function onDishIngredientSkuChange(selectEl) {
    const tr = selectEl.closest('tr');
    if (!tr) return;

    const opt = selectEl.selectedOptions[0];
    const unitInput = tr.querySelector('.ing-unit-input');
    const refPriceSpan = tr.querySelector('.ing-ref-price');
    const typeLabel = tr.querySelector('.ing-type-label');

    if (opt && opt.value) {
        const baseUnit = opt.getAttribute('data-unit') || '';
        const lastPrice = parseFloat(opt.getAttribute('data-price')) || 0;
        const isDish = opt.getAttribute('data-type') === 'dish';
        if (unitInput) unitInput.value = baseUnit;
        if (refPriceSpan) {
            refPriceSpan.innerText = I18N.fmtMoney(lastPrice) + '/' + (baseUnit || '');
        }
        if (typeLabel) typeLabel.innerText = isDish ? '子配方' : '食材';
    } else {
        if (refPriceSpan) refPriceSpan.innerText = '-';
        if (typeLabel) typeLabel.innerText = '-';
    }

    calcDishModalTheoryCost();
}

/**
 * 计算建档/编辑弹窗内的理论成本与毛利率（与后端 recipe_cost 同口径：
 * 有效领料量 = 配方用量 / 出成率；子配方按单份理论成本 × 份数折算）
 */
function calcDishModalTheoryCost() {
    const rows = document.querySelectorAll('#dishIngredientRowsBody tr.dish-ing-row');
    let totalTheoreticalCost = 0.0;
    let anyUnpurchased = false;

    rows.forEach(tr => {
        const sel = tr.querySelector('.ing-sku-select');
        const qtyInp = tr.querySelector('.ing-qty-input');
        const unitInp = tr.querySelector('.ing-unit-input');
        const yieldInp = tr.querySelector('.ing-yield-input');
        const costSpan = tr.querySelector('.ing-item-cost');
        const refPriceSpan = tr.querySelector('.ing-ref-price');
        const typeLabel = tr.querySelector('.ing-type-label');

        if (!sel || !qtyInp) return;
        const opt = sel.selectedOptions[0];
        const rawQty = parseFloat(qtyInp.value) || 0;
        const ingUnit = (unitInp ? unitInp.value : '').trim();
        let yr = parseFloat(yieldInp ? yieldInp.value : 1);
        if (!(yr > 0) || yr > 1) yr = 1;

        if (opt && opt.value && rawQty > 0) {
            const baseUnit = opt.getAttribute('data-unit') || '';
            const unitPrice = parseFloat(opt.getAttribute('data-price')) || 0;
            const isDish = opt.getAttribute('data-type') === 'dish';
            if (typeLabel) typeLabel.innerText = isDish ? '子配方' : '食材';

            if (refPriceSpan) {
                refPriceSpan.innerText = I18N.fmtMoney(unitPrice) + '/' + (baseUnit || '');
            }

            // 子配方以「份」计量：成本 = 单份理论成本 × 份数 / 出成率
            const effectiveQty = isDish ? (rawQty / yr) : null;
            const convertedQty = isDish
                ? effectiveQty
                : convertUnitQtyFrontend(rawQty / yr, ingUnit || baseUnit, baseUnit);
            const priceKnown = Number.isFinite(unitPrice) && unitPrice > 0;
            if (!priceKnown) {
                anyUnpurchased = true;
            }
            const rowCost = Math.round(convertedQty * (priceKnown ? unitPrice : 0) * 100) / 100;
            totalTheoreticalCost += rowCost;

            if (costSpan) {
                costSpan.innerText = priceKnown ? I18N.fmtMoney(rowCost) : '未采购/估算';
                costSpan.style.color = priceKnown ? 'var(--primary, #0f766e)' : 'var(--warning, #b45309)';
            }
        } else {
            if (costSpan) costSpan.innerText = '$0.00';
        }
    });

    totalTheoreticalCost = Math.round(totalTheoreticalCost * 100) / 100;
    const priceInput = document.getElementById('dishModalPrice');
    const dishPrice = parseFloat(priceInput ? priceInput.value : 0) || 0;

    const grossProfit = Math.round((dishPrice - totalTheoreticalCost) * 100) / 100;
    const grossMarginRate = dishPrice > 0 ? Math.round((grossProfit / dishPrice) * 1000) / 10 : 0.0;

    const costEl = document.getElementById('dishModalTheoryCost');
    const profitEl = document.getElementById('dishModalTheoryProfit');
    const marginEl = document.getElementById('dishModalTheoryMargin');

    // UAT018: unpurchased SKU must not show definite 0 cost / 100% margin.
    if (anyUnpurchased) {
        if (costEl) costEl.innerText = '未知/估算 ' + I18N.fmtMoney(totalTheoreticalCost);
        if (profitEl) {
            profitEl.innerText = '估算 ' + I18N.fmtMoney(grossProfit);
            profitEl.style.color = 'var(--warning, #b45309)';
        }
        if (marginEl) {
            marginEl.innerText = '未知/估算';
            marginEl.className = 'badge badge-warning';
            marginEl.title = '含未采购食材，毛利不可作确定值';
        }
    } else {
        if (costEl) costEl.innerText = I18N.fmtMoney(totalTheoreticalCost);
        if (profitEl) {
            profitEl.innerText = I18N.fmtMoney(grossProfit);
            profitEl.style.color = grossProfit >= 0 ? 'var(--success, #16a34a)' : 'var(--danger, #dc2626)';
        }
        if (marginEl) {
            marginEl.innerText = grossMarginRate.toFixed(1) + '%';
            marginEl.className = 'badge ' + marginClass(grossMarginRate);
            marginEl.title = '';
        }
    }
}

/**
 * 提交保存餐品及其 BOM 配方
 */
function saveDishModal() {
    const idInput = document.getElementById('dishModalId');
    const nameInput = document.getElementById('dishModalName');
    const catInput = document.getElementById('dishModalCategory');
    const priceInput = document.getElementById('dishModalPrice');
    const descInput = document.getElementById('dishModalDescription');
    const statusInput = document.getElementById('dishModalStatus');
    const errEl = document.getElementById('dishModalError');
    const saveBtn = document.getElementById('btnSaveDishModal');

    if (errEl) { errEl.innerText = ''; errEl.classList.add('hide'); }

    const dishId = (idInput ? idInput.value : '').trim();
    const name = (nameInput ? nameInput.value : '').trim();
    const category = (catInput ? catInput.value : '').trim().substring(0, 20);
    const priceVal = priceInput ? priceInput.value : '';
    const description = (descInput ? descInput.value : '').trim();
    const status = statusInput ? statusInput.value : 'active';

    if (!name) {
        if (errEl) { errEl.innerText = '餐品名称不能为空'; errEl.classList.remove('hide'); }
        if (nameInput) nameInput.focus();
        return;
    }

    if (priceVal === '' || isNaN(parseFloat(priceVal)) || parseFloat(priceVal) < 0) {
        if (errEl) { errEl.innerText = '请输入有效的餐品销售单价（不能为负数）'; errEl.classList.remove('hide'); }
        if (priceInput) priceInput.focus();
        return;
    }
    const price = parseFloat(priceVal);

    // 收集配方行（食材 SKU 与子配方两类组件）
    const rows = document.querySelectorAll('#dishIngredientRowsBody tr.dish-ing-row');
    const ingredients = [];
    const seenComps = new Set();

    for (let i = 0; i < rows.length; i++) {
        const tr = rows[i];
        const sel = tr.querySelector('.ing-sku-select');
        const qtyInp = tr.querySelector('.ing-qty-input');
        const unitInp = tr.querySelector('.ing-unit-input');
        const yieldInp = tr.querySelector('.ing-yield-input');

        const compVal = sel ? sel.value : '';
        const qtyRaw = qtyInp ? qtyInp.value : '';
        const qtyNum = parseFloat(qtyRaw);
        // UAT018: NaN / Infinity / non-finite must not be written (no silent clamp).
        const qtyFinite = Number.isFinite(qtyNum);
        const qtyVal = qtyFinite ? qtyNum : NaN;
        const qtyFilled = qtyRaw.trim() !== '' && qtyFinite && qtyNum > 0;
        const unitVal = (unitInp ? unitInp.value : '').trim();
        let yieldVal = parseFloat(yieldInp ? yieldInp.value : 1);
        if (isNaN(yieldVal)) yieldVal = 1;

        // D-7：全空行（未选组件且 用量无效/空）直接跳过；部分填写仍按下方人话文案拦截
        if (!compVal && !qtyFilled) continue;

        if (!compVal) {
            if (errEl) { errEl.innerText = '第 ' + (i + 1) + ' 行配方还没选食材或子配方'; errEl.classList.remove('hide'); }
            return;
        }
        // value 编码 "sku:<id>" / "dish:<id>"
        const parts = compVal.split(':');
        const ctype = parts[0] === 'dish' ? 'dish' : 'sku';
        const cid = parseInt(parts[1], 10);
        if (!cid || cid <= 0) {
            if (errEl) { errEl.innerText = '第 ' + (i + 1) + ' 行组件无效，请重新选择'; errEl.classList.remove('hide'); }
            return;
        }
        const compKey = ctype + ':' + cid;
        if (seenComps.has(compKey)) {
            if (errEl) { errEl.innerText = '配方里有重复的食材或子配方，请合并成一行'; errEl.classList.remove('hide'); }
            return;
        }
        seenComps.add(compKey);

        if (!Number.isFinite(qtyVal) || qtyVal <= 0) {
            if (errEl) { errEl.innerText = '第 ' + (i + 1) + ' 行单份用量必须是大于 0 的有限数值（不可为 NaN、Infinity、负数或 0）'; errEl.classList.remove('hide'); }
            return;
        }

        if (yieldVal <= 0 || yieldVal > 1) {
            if (errEl) { errEl.innerText = '第 ' + (i + 1) + ' 行出成率必须满足 0 < 出成率 <= 1'; errEl.classList.remove('hide'); }
            return;
        }

        if (!unitVal) {
            if (errEl) { errEl.innerText = '第 ' + (i + 1) + ' 行单位不能为空'; errEl.classList.remove('hide'); }
            return;
        }

        ingredients.push({
            sku_id: (ctype === 'sku' ? cid : null),
            component_type: ctype,
            component_id: cid,
            consumption_qty: qtyVal,
            unit: unitVal,
            yield_rate: yieldVal,
            notes: ''
        });
    }

    const payload = {
        name: name,
        category: category,
        price: price,
        description: description,
        status: status,
        ingredients: ingredients
    };

    // WS2 乐观锁：编辑时带上当前版本号，服务端不一致返回 409 VERSION_CONFLICT
    if (dishId) {
        const cached = (dishLibraryCache || []).find(d => Number(d.id) === Number(dishId));
        if (cached && cached.version != null) payload.expected_version = cached.version;
    }

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.innerText = '保存中...';
    }

    const isEdit = !!dishId;
    const url = isEdit ? ('/api/dishes/' + dishId) : '/api/dishes';
    const method = isEdit ? 'PUT' : 'POST';

    apiFetch(url, {
        method: method,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => res.json())
    .then(ret => {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.innerText = '保存餐品配方'; }
        if (ret.status !== 'success') {
            // D-8：内联错误优先取 ret.detail 再 ret.msg（403/校验 detail 为服务端人话文案）
            const errText = (typeof ret.detail === 'string' && ret.detail)
                ? ret.detail
                : (ret.msg || '保存失败');
            if (errEl) { errEl.innerText = errText; errEl.classList.remove('hide'); }
            showToast(errText, 'error');
            return;
        }

        showToast(isEdit ? '餐品与配方已成功更新' : '新建餐品成功', 'success');
        closeModalById('dishEditModal');
        loadDishesList();
        // 若停留在每日消耗录入 Tab，同步刷新录入列表
        const activeSubtab = document.querySelector('.dish-subtab-pane.active');
        if (activeSubtab && activeSubtab.id === 'subtab-dish-daily') {
            loadDailyConsumption();
        }
    })
    .catch(err => {
        if (saveBtn) { saveBtn.disabled = false; saveBtn.innerText = '保存餐品配方'; }
        console.error('saveDishModal error:', err);
        if (errEl) { errEl.innerText = '网络请求异常，请稍后重试'; errEl.classList.remove('hide'); }
        showToast('网络请求异常', 'error');
    });
}

/**
 * WS2：打开 BOM 版本历史抽屉（列表 + 回滚）
 */
function openDishVersionHistory() {
    const idInput = document.getElementById('dishModalId');
    const dishId = (idInput ? idInput.value : '').trim();
    const body = document.getElementById('dishVersionListBody');
    const cur = document.getElementById('dishVersionCurrent');
    const titleEl = document.getElementById('dishVersionModalTitle');
    if (!dishId) {
        showToast('请先保存餐品后再查看版本历史', 'warning');
        return;
    }
    const cached = (dishLibraryCache || []).find(d => Number(d.id) === Number(dishId));
    if (titleEl) titleEl.innerText = 'BOM 版本历史 - ' + ((cached && cached.name) || ('#' + dishId));
    if (cur) cur.innerText = '加载中...';
    if (body) body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">加载中...</div>';
    openModalById('dishVersionModal');

    apiFetch('/api/dishes/' + dishId + '/versions')
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') {
                if (body) body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">' + w2Escape(ret.msg || '加载版本历史失败') + '</div>';
                return;
            }
            const data = ret.data || {};
            if (cur) cur.innerText = '当前版本 v' + (data.current_version || 1) + '（共 ' + ((data.versions || []).length) + ' 个历史版本）';
            renderDishVersionList(data.current_version, data.versions || []);
        })
        .catch(err => {
            console.error('openDishVersionHistory error:', err);
            if (body) body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">网络异常，无法加载版本历史</div>';
        });
}

/**
 * 渲染版本历史列表：版本号 + 时间 + 操作人 + 摘要 + 回滚按钮（owner）
 */
function renderDishVersionList(currentVersion, versions) {
    const body = document.getElementById('dishVersionListBody');
    if (!body) return;
    if (!versions.length) {
        body.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">暂无版本快照</div>';
        return;
    }
    const isOwner = isOwnerRoleNow();
    let html = '';
    versions.forEach(v => {
        const isCurrent = Number(v.version) === Number(currentVersion);
        html += '<div class="dish-version-row" data-version="' + v.version + '"'
            + ' style="display:flex; justify-content:space-between; align-items:center; padding:10px 12px; border:1px solid var(--border-color); border-radius:8px; margin-bottom:8px;">'
            + '  <div style="display:flex; flex-direction:column; gap:4px;">'
            + '    <div style="display:flex; align-items:center; gap:8px;">'
            + '      <span class="badge ' + (isCurrent ? 'badge-success' : 'badge-info') + '" style="font-size:0.8rem;">v' + v.version + (isCurrent ? ' 当前' : '') + '</span>'
            + '      <span style="font-size:0.8rem; color:var(--text-muted);">' + w2Escape(v.changed_at || '') + ' ｜ ' + w2Escape(v.changed_by || 'unknown') + '</span>'
            + '    </div>'
            + '    <span style="font-size:0.82rem;">' + w2Escape(v.change_summary || '（无摘要）') + '</span>'
            + '  </div>'
            + (isOwner ? '  <button type="button" class="btn btn-secondary btn-restore-dish-version" style="padding:4px 10px; font-size:0.8rem; min-height:30px; height:30px;" data-version="' + v.version + '">回滚到此版本</button>' : '')
            + '</div>';
    });
    body.innerHTML = html;
    body.onclick = (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('.btn-restore-dish-version') : null;
        if (!btn) return;
        rollbackDishVersion(parseInt(btn.getAttribute('data-version'), 10));
    };
}

/**
 * WS2：回滚到指定版本（恢复为新版本，不改写历史）
 */
function rollbackDishVersion(version) {
    const idInput = document.getElementById('dishModalId');
    const dishId = (idInput ? idInput.value : '').trim();
    if (!dishId || !version) return;
    showCustomConfirmModal({
        title: '回滚配方版本确认',
        message: '确定将餐品「' + ((document.getElementById('dishModalName') || {}).value || '')
            + '」回滚到 v' + version + '？\n回滚会覆盖当前配方并生成一个新版本，历史版本不会丢失。',
        confirmText: '确认回滚',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/dishes/' + dishId + '/versions/' + version + '/restore', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            })
                .then(res => res.json())
                .then(ret => {
                    if (ret.status !== 'success') {
                        showToast(ret.msg || '回滚失败', 'error');
                        return;
                    }
                    showToast(ret.msg || '已回滚并生成新版本', 'success');
                    closeModalById('dishVersionModal');
                    loadDishesList();
                    if (ret.data) {
                        // 回滚后同步刷新编辑弹窗内容（版本与配方已变）
                        closeModalById('dishEditModal');
                        openDishModal(ret.data.id);
                    }
                })
                .catch(err => {
                    console.error('rollbackDishVersion error:', err);
                    showToast('网络异常，无法回滚版本', 'error');
                });
        }
    });
}

/**
 * 停用或删除餐品：active 走 hard=0 停用；inactive 走 hard=1 彻底删除（不可撤销）
 */
function deleteDish(dishId, dishName, dishStatus) {
    const name = dishName || ('#' + dishId);
    if (dishStatus === 'inactive') {
        showCustomConfirmModal({
            title: '彻底删除餐品确认',
            message: '彻底删除餐品「' + name + '」？\n此操作不可撤销，配方将一并移除；历史消耗与成本核算数据完整保留。',
            confirmText: '彻底删除',
            cancelText: '取消',
            onConfirm: () => {
                apiFetch('/api/dishes/' + dishId + '?hard=1', { method: 'DELETE' })
                    .then(res => res.json())
                    .then(ret => {
                        if (ret.status !== 'success') {
                            showToast(ret.msg || '操作失败', 'error');
                            return;
                        }
                        showToast(ret.msg || '餐品已彻底删除', 'success');
                        loadDishesList();
                        loadDailyConsumption();
                    })
                    .catch(err => {
                        console.error('deleteDish error:', err);
                        showToast('网络异常，无法彻底删除餐品', 'error');
                    });
            }
        });
        return;
    }
    showCustomConfirmModal({
        title: '停用/删除餐品确认',
        message: '确定要停用或删除餐品「' + name + '」吗？\n停用后该餐品将不会在每日消耗录入界面出现，历史消耗与成本核算数据将完整保留。',
        confirmText: '确认操作',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/dishes/' + dishId + '?hard=0', { method: 'DELETE' })
                .then(res => res.json())
                .then(ret => {
                    if (ret.status !== 'success') {
                        showToast(ret.msg || '操作失败', 'error');
                        return;
                    }
                    showToast(ret.msg || '餐品已停用', 'success');
                    loadDishesList();
                    loadDailyConsumption();
                })
                .catch(err => {
                    console.error('deleteDish error:', err);
                    showToast('网络异常，无法停用餐品', 'error');
                });
        }
    });
}

// --------------------------------------------------------------------------
// 2. 每日消耗极速录入与扣减
// --------------------------------------------------------------------------

/**
 * 初始化日期输入框为今天
 */
function initDishConsumptionDate() {
    const dateInput = document.getElementById('dishConsumptionDate');
    if (dateInput && !dateInput.value) {
        const today = todayLocalStr();
        dateInput.value = today;
    }
}

/**
 * 快捷设置核算日期为「今天」
 */
function setDishConsumptionToday() {
    const today = todayLocalStr();
    const dateInput = document.getElementById('dishConsumptionDate');
    if (dateInput) dateInput.value = today;
    loadDailyConsumption(today);
}

/**
 * 快捷设置核算日期为「昨天」
 */
function setDishConsumptionYesterday() {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    const yest = fmtLocalDateStr(d);
    const dateInput = document.getElementById('dishConsumptionDate');
    if (dateInput) dateInput.value = yest;
    loadDailyConsumption(yest);
}

/**
 * 日期改变触发重载
 */
function onDishConsumptionDateChange(val) {
    loadDailyConsumption(val);
}

let dailyConsumptionReqSeq = 0;

function markDishKpiFailed() {
    // UAT-036：餐品 KPI GET 失败 → — + 错误 + 可点重试（不得伪装 $0.00 / 0 笔）。
    const costEl = document.getElementById('kpiDishCost');
    const revEl = document.getElementById('kpiDishRevenue');
    const marginEl = document.getElementById('kpiDishGrossMargin');
    const countEl = document.getElementById('kpiDishCount');
    if (costEl) costEl.innerText = '—';
    if (revEl) revEl.innerText = '—';
    if (marginEl) { marginEl.innerText = '—'; marginEl.style.color = ''; }
    if (countEl) countEl.innerText = '—';
    let notice = document.getElementById('dishKpiLoadError');
    if (!notice) {
        const kpiGrid = document.querySelector('.metrics-grid.dish-kpi-grid');
        notice = document.createElement('div');
        notice.id = 'dishKpiLoadError';
        notice.setAttribute('role', 'alert');
        notice.setAttribute('data-uat036', 'dish-kpi-error');
        notice.style.cssText = 'margin:8px 0 12px 0; padding:8px 12px; border-radius:6px;'
            + ' background:#fee2e2; color:#991b1b; font-size:0.85rem;';
        notice.innerHTML = '餐品指标加载失败，当前数值不可用。'
            + ' <button type="button" id="dishKpiRetry" class="btn btn-secondary"'
            + ' data-uat036="dish-kpi-retry" style="margin-left:8px; min-height:38px;">重试</button>';
        if (kpiGrid && kpiGrid.parentNode) {
            kpiGrid.parentNode.insertBefore(notice, kpiGrid.nextSibling);
        }
        const btn = document.getElementById('dishKpiRetry');
        if (btn) btn.addEventListener('click', () => loadDailyConsumption());
    } else {
        notice.classList.remove('hide');
    }
}

function clearDishKpiFailed() {
    const notice = document.getElementById('dishKpiLoadError');
    if (notice) notice.classList.add('hide');
}

/**
 * 加载指定日期的餐品消耗记录与当日在售餐品录入列表
 */
function loadDailyConsumption(targetDate) {
    const dateInput = document.getElementById('dishConsumptionDate');
    const curDate = (targetDate || (dateInput ? dateInput.value : '') || todayLocalStr()).trim();
    if (dateInput && dateInput.value !== curDate) dateInput.value = curDate;

    const reqSeq = ++dailyConsumptionReqSeq;

    // 并行获取当日已提交流水与当前在售餐品列表
    // 返回 Promise：UAT-032 提交成功后必须 await 再开 FIFO 弹窗，避免读到第一次提交的缓存。
    return Promise.all([
        apiFetch('/api/dishes/daily_consumption?date=' + encodeURIComponent(curDate)).then(r => {
            if (!r || !r.ok) throw new Error('daily_consumption_http_' + ((r && r.status) || 'net'));
            return r.json();
        }),
        apiFetch('/api/dishes?status=active').then(r => {
            if (!r || !r.ok) throw new Error('dishes_active_http_' + ((r && r.status) || 'net'));
            return r.json();
        })
    ])
    .then(([dailyRes, dishesRes]) => {
        if (reqSeq !== dailyConsumptionReqSeq) return; // 过期响应：已有更新的加载请求，丢弃
        if (dailyRes.status === 'success') {
            clearDishKpiFailed();
            const data = dailyRes.data || {};
            dailyConsumptionCache = data.consumptions || [];
            // 合法空态：summary 全 0 / 空流水 → 显示 0，不是 —
            renderDailyKPI(data.summary || {});
            renderDailyHistoryTable(dailyConsumptionCache);
        } else {
            markDishKpiFailed();
            showToast(dailyRes.msg || '加载当日消耗流水失败', 'error');
        }

        if (dishesRes.status === 'success') {
            dishDailyDishesCache = dishesRes.data || [];
            renderDishConsumeEntryTable(dishDailyDishesCache);
        }
    })
    .catch(err => {
        if (reqSeq !== dailyConsumptionReqSeq) return; // 过期响应：已有更新的加载请求，丢弃
        console.error('loadDailyConsumption error:', err);
        markDishKpiFailed();
        showToast('网络请求异常，无法加载每日消耗数据', 'error');
    });
}

/**
 * 渲染当日 KPI 统计面板
 */
function renderDailyKPI(summary) {
    dailyKpiSummaryCache = summary || {};
    const costEl = document.getElementById('kpiDishCost');
    const revEl = document.getElementById('kpiDishRevenue');
    const marginEl = document.getElementById('kpiDishGrossMargin');
    const countEl = document.getElementById('kpiDishCount');

    if (costEl) costEl.innerText = I18N.fmtMoney(summary.total_cost || 0);
    if (revEl) revEl.innerText = I18N.fmtMoney(summary.total_revenue || 0);
    if (marginEl) {
        const gm = Number(summary.gross_margin_rate || 0);
        marginEl.innerText = gm.toFixed(1) + '%';
        marginEl.style.color = marginTextColor(gm);
    }
    if (countEl) {
        const validCount = Math.max(0, (summary.records_count || 0) - (summary.void_count || 0));
        countEl.innerText = validCount + ' 笔';
    }

    // 零成本/数据完整性风险警示横幅
    let bannerEl = document.getElementById('dishDataIntegrityBanner');
    if (!bannerEl) {
        const kpiGrid = document.querySelector('.metrics-grid.dish-kpi-grid');
        if (kpiGrid && kpiGrid.parentNode) {
            bannerEl = document.createElement('div');
            bannerEl.id = 'dishDataIntegrityBanner';
            kpiGrid.parentNode.insertBefore(bannerEl, kpiGrid.nextSibling);
        }
    }
    if (bannerEl) {
        if (summary.has_zero_cost_batch || summary.data_integrity_status === 'zero_cost_alert') {
            bannerEl.className = 'alert alert-warning';
            bannerEl.style.cssText = 'margin: 12px 0 20px 0; padding: 12px 16px; border-left: 4px solid var(--warning, #eab308); background: rgba(234, 179, 8, 0.12); border-radius: 6px; font-size: 0.88rem; color: var(--text-main); line-height: 1.5;';
            bannerEl.innerHTML = '<strong>提示：</strong>' + w2Escape(summary.data_integrity_msg || '今日核算中包含未录入进货价的食材（暂估成本 $0）。当前大盘毛利率可能偏高，请提醒老板尽快补录进货单据以还原真实利润。');
            bannerEl.classList.remove('hide');
        } else {
            bannerEl.classList.add('hide');
            bannerEl.innerHTML = '';
        }
    }

    refreshDishKpiEstimates();
}

/**
 * 浮点安全解析函数：支持半份/小数份（如 0.5），防 NaN 与负数截断
 */
function parseSafeQuantity(val) {
    if (val === null || val === undefined || val === '') return 0;
    const num = parseFloat(val);
    return isNaN(num) ? 0 : Math.max(0, Math.round(num * 100) / 100);
}

/**
 * 预估 KPI 叠加：已提交流水实际值 + 当前已输入未提交份数的理论估算（Σ 份数 × 理论成本/售价）
 */
function refreshDishKpiEstimates() {
    const summary = dailyKpiSummaryCache || {};
    let estCost = 0;
    let estRevenue = 0;
    let pendingQty = 0;

    const rows = document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row');
    rows.forEach(tr => {
        const dishId = tr.getAttribute('data-dish-id');
        const input = document.getElementById('dishConsumeQty_' + dishId);
        const qty = input ? parseSafeQuantity(input.value) : 0;
        if (qty > 0) {
            pendingQty = Math.round((pendingQty + qty) * 100) / 100;
            estCost += qty * (parseFloat(tr.getAttribute('data-dish-cost')) || 0);
            estRevenue += qty * (parseFloat(tr.getAttribute('data-dish-price')) || 0);
        }
    });

    const costEl = document.getElementById('kpiDishCost');
    const revEl = document.getElementById('kpiDishRevenue');
    const marginEl = document.getElementById('kpiDishGrossMargin');
    const badgeEl = document.getElementById('kpiDishCostBadge');

    const totalCost = Number(summary.total_cost || 0) + estCost;
    const totalRevenue = Number(summary.total_revenue || 0) + estRevenue;

    if (costEl) costEl.innerText = I18N.fmtMoney(totalCost);
    if (revEl) revEl.innerText = I18N.fmtMoney(totalRevenue);
    if (marginEl) {
        const gm = totalRevenue > 0 ? (totalRevenue - totalCost) / totalRevenue * 100 : 0;
        marginEl.innerText = gm.toFixed(1) + '%';
        marginEl.style.color = marginTextColor(gm);
    }
    if (badgeEl) {
        if (pendingQty > 0) {
            badgeEl.innerText = '预估叠加 ' + pendingQty + ' 份未提交';
            badgeEl.className = 'badge badge-info';
        } else if ((summary.records_count || 0) - (summary.void_count || 0) > 0) {
            badgeEl.innerText = '实际 FIFO';
            badgeEl.className = 'badge badge-success';
        } else {
            badgeEl.innerText = '基准理论';
            badgeEl.className = 'badge badge-warning';
        }
    }
}

/**
 * 渲染今日在售餐品极速录入表格
 */
function renderDishConsumeEntryTable(dishes) {
    const tbody = document.getElementById('dishConsumeEntryTableBody');
    const countBadge = document.getElementById('dishConsumeActiveCount');
    if (!tbody) return;

    if (countBadge) countBadge.innerText = dishes.length + ' 种在售餐品';

    if (dishes.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-muted);">'
            + '暂无在售餐品，请先前往「餐品 BOM 配方库」创建并上架餐品'
            + '</td></tr>';
        updateDishConsumeSummary();
        return;
    }

    let html = '';
    dishes.forEach(d => {
        const ingSummary = (d.ingredients || []).map(ing => (ing.sku_name || ('食材#' + ing.sku_id)) + ' ' + ing.consumption_qty + ing.unit).join('、') || '未配置配方';

        html += '<tr class="dish-consume-row" data-dish-id="' + d.id + '" data-dish-name="' + w2Escape(d.name) + '" data-dish-price="' + (d.price || 0) + '" data-dish-cost="' + (d.theoretical_cost || 0) + '">'
            + '<td>'
            + '  <div style="font-weight:600; color:var(--text-main); font-size:0.95rem;">' + w2Escape(d.name) + '</div>'
            + (d.description ? ('<div style="font-size:0.75rem; color:var(--text-muted);">' + w2Escape(d.description) + '</div>') : '')
            + '</td>'
            + '<td>' + (d.category ? ('<span class="badge badge-info">' + w2Escape(d.category) + '</span>') : '-') + '</td>'
            + '<td class="col-right" style="font-weight:600; color:var(--text-main);">' + I18N.fmtMoney(d.price) + '</td>'
            + '<td style="font-size:0.8rem; color:var(--text-secondary); max-width:260px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="' + w2Escape(ingSummary) + '">'
            + w2Escape(ingSummary)
            + '</td>'
            + '<td style="text-align:center;">'
            + '  <div class="dish-qty-control" style="display:inline-flex; align-items:center; gap:4px;">'
            + '    <button type="button" class="btn btn-secondary dish-step-btn" style="width:38px; min-width:38px; height:38px; min-height:38px; padding:0; font-size:18px; font-weight:700; display:inline-flex; align-items:center; justify-content:center;" onclick="adjustDishConsumeQty(' + d.id + ', -1)" aria-label="' + w2Escape(d.name) + ' 份数减 1">-</button>'
            + '    <input type="number" min="0" step="0.5" id="dishConsumeQty_' + d.id + '" class="form-control dish-consume-qty-input" style="width:80px; text-align:center; font-weight:700; height:38px; min-height:38px; padding:2px 4px; font-size:1rem;" value="0" aria-label="' + w2Escape(d.name) + ' 当日消耗份数" onkeydown="if(event.key===\x27-\x27||event.key===\x27+\x27||event.key===\x27e\x27||event.key===\x27E\x27){event.preventDefault();}" oninput="onDishConsumeQtyInput(' + d.id + ', this.value)">'
            + '    <button type="button" class="btn btn-secondary dish-step-btn" style="width:38px; min-width:38px; height:38px; min-height:38px; padding:0; font-size:18px; font-weight:700; display:inline-flex; align-items:center; justify-content:center;" onclick="adjustDishConsumeQty(' + d.id + ', 1)" aria-label="' + w2Escape(d.name) + ' 份数加 1">+</button>'
            + '    <button type="button" class="btn btn-secondary dish-step-btn" style="font-size:0.8rem; font-weight:600; padding:0 8px; min-height:38px; height:38px;" onclick="adjustDishConsumeQty(' + d.id + ', 5)" aria-label="' + w2Escape(d.name) + ' 份数加 5">+5</button>'
            + '    <button type="button" class="btn btn-secondary dish-step-btn" style="font-size:0.8rem; font-weight:600; padding:0 8px; min-height:38px; height:38px;" onclick="adjustDishConsumeQty(' + d.id + ', 10)" aria-label="' + w2Escape(d.name) + ' 份数加 10">+10</button>'
            + '  </div>'
            + '</td>'
            + '<td class="col-right" style="font-weight:700; color:var(--primary, #0f766e); font-size:0.95rem;" id="dishSubtotal_' + d.id + '">'
            + '$0.00'
            + '</td>'
            + '</tr>';
    });

    // FIX-A01 关联体验：重渲染前保留用户已录入但未提交的份数（loadDailyConsumption
    // 在提交成功后重拉本表；保留语义与本函数「只重绘不改值」的直觉一致，避免
    // 用户在异步刷新窗口内输入的份数被静默清零——此前二次提交被误判为空输入）。
    const _prevQty = {};
    tbody.querySelectorAll('tr.dish-consume-row').forEach(tr => {
        const _id = tr.getAttribute('data-dish-id');
        const _inp = tr.querySelector('.dish-consume-qty-input');
        if (_id && _inp) _prevQty[_id] = _inp.value;
    });

    tbody.innerHTML = html;
    Object.keys(_prevQty).forEach(_id => {
        const _inp = document.getElementById('dishConsumeQty_' + _id);
        if (_inp && _inp.value === '0' && _prevQty[_id] !== '0') {
            _inp.value = _prevQty[_id];
            onDishConsumeQtyInput(parseInt(_id, 10), _prevQty[_id]);
        }
    });
    updateDishConsumeSummary();
}

/**
 * 过滤在售餐品极速录入表格
 */
function filterDishConsumeEntryTable(query) {
    const q = (query || '').trim().toLowerCase();
    const rows = document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row');
    rows.forEach(tr => {
        const name = (tr.getAttribute('data-dish-name') || '').toLowerCase();
        if (!q || name.includes(q)) {
            tr.style.display = '';
        } else {
            tr.style.display = 'none';
        }
    });
}

/**
 * 步进调整餐品售出份数
 */
function adjustDishConsumeQty(dishId, delta) {
    const input = document.getElementById('dishConsumeQty_' + dishId);
    if (!input) return;
    let current = parseSafeQuantity(input.value);
    current = Math.max(0, Math.round((current + delta) * 100) / 100);
    input.value = current;
    onDishConsumeQtyInput(dishId, current);
}

/**
 * 份数输入框监听
 */
function onDishConsumeQtyInput(dishId, val) {
    const row = document.querySelector('#dishConsumeEntryTableBody tr[data-dish-id="' + dishId + '"]');
    if (!row) return;

    const price = parseFloat(row.getAttribute('data-dish-price')) || 0;
    const qty = parseSafeQuantity(val);
    const subtotal = Math.round(qty * price * 100) / 100;

    const subtotalEl = document.getElementById('dishSubtotal_' + dishId);
    if (subtotalEl) subtotalEl.innerText = I18N.fmtMoney(subtotal);

    updateDishConsumeSummary();
}

/**
 * 更新极速录入底部汇总栏
 */
function updateDishConsumeSummary() {
    const rows = document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row');
    let totalItems = 0;
    let totalQty = 0;

    rows.forEach(tr => {
        const dishId = tr.getAttribute('data-dish-id');
        const input = document.getElementById('dishConsumeQty_' + dishId);
        const qty = input ? parseSafeQuantity(input.value) : 0;
        if (qty > 0) {
            totalItems++;
            totalQty = Math.round((totalQty + qty) * 100) / 100;
        }
    });

    const countText = document.getElementById('dishSubmitCountText');
    const qtyText = document.getElementById('dishSubmitTotalQty');

    if (countText) countText.innerText = totalItems;
    if (qtyText) qtyText.innerText = totalQty;

    refreshDishKpiEstimates();
}

/**
 * 清空所有已输入份数
 */
function resetDishConsumeInputs() {
    showCustomConfirmModal({
        title: '清空确认',
        message: '确定要清空刚才输入的所有数字吗？',
        confirmText: '确认清空',
        cancelText: '取消',
        onConfirm: () => {
            const inputs = document.querySelectorAll('.dish-consume-qty-input');
            inputs.forEach(inp => { inp.value = 0; });
            const subtotals = document.querySelectorAll('[id^="dishSubtotal_"]');
            subtotals.forEach(st => { st.innerText = (window.I18N && I18N.fmtMoney) ? I18N.fmtMoney(0) : '0.00'; });
            updateDishConsumeSummary();
        }
    });
}

// 同一次提交（含失败重试和双击）共用一个 request_id；成功后下一次点击才换新的。
let _dailyConsumeRequestId = null;

function _newDailyConsumeRequestId() {
    try {
        if (typeof crypto !== 'undefined' && crypto && typeof crypto.randomUUID === 'function') {
            return crypto.randomUUID();
        }
    } catch (e) { /* 沙箱没有 crypto 时用下面的随机串 */ }
    return 'dc-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
}

/**
 * UAT019：餐品消耗超库存时与库存页同合同——先问原因，再带 allow_negative 重试；
 * 取消则不写。返回 null=取消；其余为原因字符串。
 */
async function askDishConsumeOverdrawReason(message) {
    if (typeof uiPrompt !== 'function') {
        showToast(message || '库存不够扣，请核对数量或联系老板确认。', 'error');
        return null;
    }
    return uiPrompt({
        title: '库存不够扣',
        message: message,
        label: '要继续记录的话，请写明原因（会记在库存流水里）',
        placeholder: '例如：上周的进货单还没录入',
        confirmText: '我确认，继续记录',
        cancelText: '先不记',
        validate: v => (v ? '' : '请写明为什么要扣成负数，不然不能继续。'),
    });
}

/**
 * 批量提交每日餐品消耗并触发 FIFO 扣减。
 * UAT019：普通点击遇 409 INSUFFICIENT_STOCK 不得静默负库存；仅显式 force+原因路径放行。
 */
async function submitDailyConsumptionBatch() {
    const btnSubmit = document.getElementById('btnSubmitDailyConsumeBatch');
    if (btnSubmit && btnSubmit.disabled) return;

    const dateInput = document.getElementById('dishConsumptionDate');
    const dateStr = (dateInput ? dateInput.value : '').trim() || todayLocalStr();

    const rows = document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row');
    const items = [];

    rows.forEach(tr => {
        const dishId = parseInt(tr.getAttribute('data-dish-id'));
        const input = document.getElementById('dishConsumeQty_' + dishId);
        const qty = input ? parseSafeQuantity(input.value) : 0;
        if (dishId && qty > 0) {
            items.push({
                dish_id: dishId,
                quantity: qty,
                notes: ''
            });
        }
    });

    if (items.length === 0) {
        showToast('请至少输入一种餐品的售出份数（份数大于 0）', 'warning');
        return;
    }

    if (!_dailyConsumeRequestId) {
        _dailyConsumeRequestId = _newDailyConsumeRequestId();
    }
    const requestId = _dailyConsumeRequestId;

    if (btnSubmit) {
        btnSubmit.disabled = true;
        btnSubmit.innerText = '扣减核算中...';
    }

    const send = (extra) => {
        const payload = Object.assign({
            date: dateStr,
            notes: '前台批量录入',
            items: items,
            request_id: requestId
        }, extra || {});
        return apiFetch('/api/dishes/daily_consumption/batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        }).then(res => res.json().then(data => ({ status: res.status, body: data })));
    };

    const finishUi = () => {
        if (btnSubmit) {
            btnSubmit.disabled = false;
            btnSubmit.innerText = '一键扣减库存并核算真实成本';
        }
    };

    const onSuccess = async (ret) => {
        // 成功后才换新的 request_id。失败重试继续用刚才那个，避免双击或网络重发扣两次。
        _dailyConsumeRequestId = null;

        // WS5：同 (日期, 餐品) 二次提交后端会合并累加，merged=true 时明确提示，
        // 避免店员误以为新建了重复记录（份数为合并后的累计值）。
        const respData = ret.body.data || {};
        let successMsg;
        if (respData.replayed) {
            successMsg = ret.body.msg || '这一单刚才已经记过了，没有重复扣库存';
        } else {
            const baseMsg = ret.body.msg || '消耗扣减成功！已完成 FIFO 批次精确计价';
            successMsg = respData.merged
                ? (baseMsg + '（已合并至当日同餐品记录，累计 ' + respData.quantity + ' 份）')
                : baseMsg;
        }
        showToast(successMsg, 'success');

        // 仅在成功后清空输入框
        const inputs = document.querySelectorAll('.dish-consume-qty-input');
        inputs.forEach(inp => { inp.value = 0; });
        const subtotals = document.querySelectorAll('[id^="dishSubtotal_"]');
        subtotals.forEach(st => { st.innerText = '$0.00'; });
        updateDishConsumeSummary();

        // UAT-032：优先用 batch 响应里的累计 consumptions 快照刷新本地缓存，
        // 再 await 列表刷新，最后才开 FIFO 弹窗——避免弹窗仍显示第一次份数/成本。
        const snapList = Array.isArray(respData.consumptions) ? respData.consumptions : [];
        if (snapList.length > 0) {
            const byId = new Map((dailyConsumptionCache || []).map(c => [Number(c.id), c]));
            snapList.forEach(c => { byId.set(Number(c.id), c); });
            dailyConsumptionCache = Array.from(byId.values());
        }

        // 1. 刷新当日流水与 KPI（必须完成后再开弹窗）
        try {
            await loadDailyConsumption(dateStr);
        } catch (e) {
            console.error('post-submit loadDailyConsumption failed:', e);
        }

        // 2. 即时刷新全局「实时库存与价格」Tab 数据（库存已扣减）
        loadInventoryData();

        // 3. 自动展示第一条记录的 FIFO 批次穿透溯源报告（累计份数/两次明细）
        if (Array.isArray(respData.consumption_ids) && respData.consumption_ids.length > 0) {
            const cid = respData.consumption_ids[0];
            const fromSnap = snapList.find(c => Number(c.id) === Number(cid));
            if (fromSnap) {
                renderCostTraceModalContent(fromSnap);
            } else {
                showCostTraceModal(cid);
            }
        }
    };

    try {
        let ret = await send({});
        // UAT019：409 INSUFFICIENT_STOCK → 显式原因后 force；取消则不写、保留输入
        if (ret.status === 409 && ret.body && ret.body.code === 'INSUFFICIENT_STOCK') {
            const again = await askDishConsumeOverdrawReason(ret.body.msg || '库存不够扣。');
            if (again === null) {
                finishUi();
                showToast('已取消，未扣减库存', 'info');
                return;
            }
            ret = await send({ allow_negative: true, override_reason: again });
        }

        finishUi();

        if (ret.status !== 200 || !ret.body || ret.body.status !== 'success') {
            const err = (ret.body && ret.body.msg) || '消耗扣减失败，请检查输入后重试';
            showToast(err, 'error');
            // 保留已录入内容，绝不清空输入框
            return;
        }
        await onSuccess(ret);
    } catch (err) {
        finishUi();
        console.error('submitDailyConsumptionBatch error:', err);
        showToast('网络请求异常，消耗扣减失败，已保留填写内容', 'error');
    }
}

/**
 * 渲染当日消耗历史流水表格
 */
function renderDailyHistoryTable(consumptions) {
    const tbody = document.getElementById('dishDailyHistoryTableBody');
    if (!tbody) return;

    if (consumptions.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding:30px; color:var(--text-muted);">'
            + '当日暂无已提交的消耗流水记录'
            + '</td></tr>';
        return;
    }

    let html = '';
    consumptions.forEach(r => {
        const isVoid = r.is_void === 1;
        const statusBadge = isVoid
            ? '<span class="badge badge-danger" style="font-size:0.75rem;">已冲销作废</span>'
            : '<span class="badge badge-success" style="font-size:0.75rem;">正常</span>';

        const gm = Number(r.gross_margin_rate || 0);
        const gmBadgeClass = marginClass(gm);

        html += '<tr style="' + (isVoid ? 'opacity:0.55; text-decoration:line-through;' : '') + '">'
            + '<td style="font-family:monospace; font-weight:600;">#' + r.id + '</td>'
            + '<td>'
            + '  <strong>' + w2Escape(r.dish_name) + '</strong>'
            + (r.dish_category ? (' <span class="badge badge-info" style="font-size:0.7rem;">' + w2Escape(r.dish_category) + '</span>') : '')
            + '</td>'
            + '<td class="col-right" style="font-weight:600;">' + r.quantity + ' 份</td>'
            + '<td class="col-right" style="font-weight:600; color:var(--primary, #0f766e);">'
            + '  ' + I18N.fmtMoney(r.total_cost)
            + '  <div style="font-size:0.72rem; color:var(--text-muted); font-weight:400;">单份 ' + I18N.fmtMoney(r.unit_cost) + '</div>'
            + '</td>'
            + '<td class="col-right">' + I18N.fmtMoney(r.revenue) + '</td>'
            + '<td class="col-right"><span class="badge ' + gmBadgeClass + '">' + gm.toFixed(1) + '%</span></td>'
            + '<td class="col-center">' + statusBadge + '</td>'
            + '<td class="col-center">'
            + '  <button type="button" class="btn btn-secondary" style="padding:4px 10px; font-size:0.85rem; min-height:38px; height:38px; margin-right:4px;" onclick="showCostTraceModal(' + r.id + ')">批次溯源</button>'
            + (!isVoid && isOwnerRoleNow() ? ('<button type="button" class="btn btn-danger" style="padding:4px 10px; font-size:0.85rem; min-height:38px; height:38px;" onclick="voidDailyConsumption(' + r.id + ')">冲销作废</button>') : '')
            + '</td>'
            + '</tr>';
    });

    tbody.innerHTML = html;
}

// --------------------------------------------------------------------------
// 3. FIFO 批次穿透溯源与冲销
// --------------------------------------------------------------------------

/**
 * 打开 FIFO 批次成本穿透溯源报告弹窗
 */
function showCostTraceModal(consumptionId) {
    // UAT-032：始终按当日接口取最新累计记录再渲染，避免同日二次合并后
    // 仍用第一次提交残留在 dailyConsumptionCache 里的份数/成本/明细。
    const dateInput = document.getElementById('dishConsumptionDate');
    const dStr = (dateInput ? dateInput.value : '') || todayLocalStr();
    apiFetch('/api/dishes/daily_consumption?date=' + encodeURIComponent(dStr))
        .then(res => {
            if (!res || !res.ok) throw new Error('daily_consumption_http_' + ((res && res.status) || 'net'));
            return res.json();
        })
        .then(ret => {
            if (ret.status === 'success') {
                const list = (ret.data || {}).consumptions || [];
                dailyConsumptionCache = list;
                const found = list.find(c => Number(c.id) === Number(consumptionId));
                if (found) renderCostTraceModalContent(found);
                else showToast('未找到对应流水记录 #' + consumptionId, 'warning');
            } else {
                showToast((ret && ret.msg) || '加载批次溯源失败', 'error');
            }
        })
        .catch(err => {
            console.error('showCostTraceModal fetch error:', err);
            const traceRecord = (dailyConsumptionCache || []).find(
                c => Number(c.id) === Number(consumptionId));
            if (traceRecord) renderCostTraceModalContent(traceRecord);
            else showToast('网络异常，无法加载批次溯源', 'error');
        });
}

/**
 * 渲染穿透溯源报告内容并打开 Modal
 */
function renderCostTraceModalContent(c) {
    const nameEl = document.getElementById('traceDishName');
    const qtyEl = document.getElementById('traceDishQty');
    const totalEl = document.getElementById('traceTotalCost');
    const diffEl = document.getElementById('traceUnitCostDiff');
    const treeContainer = document.getElementById('costTraceDetailsContainer');

    if (nameEl) nameEl.innerText = c.dish_name || ('餐品#' + c.dish_id);
    if (qtyEl) qtyEl.innerText = (c.date || '-') + ' / 售出 ' + c.quantity + ' 份';
    if (totalEl) totalEl.innerText = I18N.fmtMoney(c.total_cost);

    const unitCost = Number(c.unit_cost || 0);
    if (diffEl) {
        diffEl.innerHTML = '<span style="font-weight:600;">' + I18N.fmtMoney(unitCost) + '/份</span>'
            + ' <span style="font-size:0.75rem; color:var(--text-muted);">(基于入库批次 FIFO 实际加权计价)</span>';
    }

    if (!treeContainer) return;

    const details = c.details || [];
    if (details.length === 0) {
        treeContainer.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">暂无批次扣减明细（未配置配方食材或用量为 0）</div>';
        openModalById('costTraceModal');
        return;
    }

    // 按食材 SKU 分组展示扣减批次
    const skuMap = new Map();
    details.forEach(dt => {
        if (!skuMap.has(dt.sku_id)) {
            skuMap.set(dt.sku_id, {
                sku_id: dt.sku_id,
                sku_name: dt.sku_name,
                unit: dt.unit,
                total_qty: 0,
                total_cost: 0,
                batches: []
            });
        }
        const group = skuMap.get(dt.sku_id);
        group.total_qty = Math.round((group.total_qty + dt.qty_consumed) * 10000) / 10000;
        if (dt.total_cost == null || dt.unit_cost == null) {
            group.cost_missing = true;
        } else {
            group.total_cost = Math.round((group.total_cost + Number(dt.total_cost)) * 100) / 100;
        }
        group.batches.push(dt);
    });

    let html = '';
    skuMap.forEach(group => {
        let batchesHtml = '';
        group.batches.forEach(b => {
            const isEst = (b.is_estimated === 1) || (!b.batch_id || b.batch_id <= 0);
            const isZeroCost = (b.is_zero_cost === true) || (Number(b.unit_cost || 0) <= 0);
            if (isEst) {
                const badgeLabel = isZeroCost ? '[零成本暂估]' : '[超卖暂估批次]';
                const warningMsg = (b.unit_cost == null || isZeroCost)
                    ? '无进货。这味食材还没有进货单价，这里不显示 0 元，也不按 0 元计算毛利。'
                    : '无可用历史入库批次，已生成暂估对冲批次，待新入库后自动核销差异。';
                batchesHtml += '<div class="cost-trace-batch-item" style="padding:10px 12px; margin:4px 0 4px 16px; background:rgba(239,68,68,0.08); border-left:4px solid var(--danger, #dc2626); border-radius:4px; font-size:0.85rem;">'
                    + '<div style="display:flex; justify-content:space-between; align-items:center;">'
                    + '  <div>'
                    + '    <span class="badge badge-danger" style="font-size:0.75rem; margin-right:6px; padding:2px 6px;">' + badgeLabel + '</span>'
                    + '    <span>扣减 <strong>' + b.qty_consumed + ' ' + w2Escape(b.unit) + '</strong> @ ' + ((b.unit_cost == null || isZeroCost) ? '无进货' : ('暂估单价 ' + I18N.fmtMoney(b.unit_cost) + '/' + w2Escape(b.unit))) + '</span>'
                    + '  </div>'
                    + '  <div style="font-weight:700; color:var(--danger, #dc2626);">' + ((b.total_cost == null || b.unit_cost == null) ? '无进货' : I18N.fmtMoney(b.total_cost)) + '</div>'
                    + '</div>'
                    + '<div style="font-size:0.76rem; color:' + (isZeroCost ? 'var(--danger, #dc2626)' : 'var(--text-muted)') + '; margin-top:4px; line-height:1.4;">' + warningMsg + '</div>'
                    + '</div>';
            } else {
                batchesHtml += '<div class="cost-trace-batch-item" style="padding:10px 12px; margin:4px 0 4px 16px; background:var(--bg-subtle, #f8fafc); border-left:4px solid var(--primary, #0f766e); border-radius:4px; font-size:0.85rem;">'
                    + '<div style="display:flex; justify-content:space-between; align-items:center;">'
                    + '  <div>'
                    + '    <span class="badge badge-info" style="font-size:0.75rem; margin-right:6px; padding:2px 6px;">批次 #' + b.batch_id + '</span>'
                    + '    <span>入库日期: <strong>' + (b.batch_date || '未知') + '</strong> ｜ 扣减 <strong>' + b.qty_consumed + ' ' + w2Escape(b.unit) + '</strong> @ ' + I18N.fmtMoney(b.unit_cost) + '/' + w2Escape(b.unit) + '</span>'
                    + '  </div>'
                    + '  <div style="font-weight:700; color:var(--primary, #0f766e);">' + I18N.fmtMoney(b.total_cost) + '</div>'
                    + '</div>'
                    + '</div>';
            }
        });

        html += '<div class="cost-trace-sku-group" style="margin-bottom:14px; border:1px solid var(--border-color); border-radius:8px; padding:10px 14px; background:var(--bg-card);">'
            + '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; border-bottom:1px solid var(--border-color); padding-bottom:6px;">'
            + '  <div style="font-weight:600; font-size:0.92rem; color:var(--text-main);">'
            + '    ' + w2Escape(group.sku_name)
            + '    <span style="font-size:0.8rem; color:var(--text-muted); font-weight:400; margin-left:8px;">合计耗用: <strong>' + group.total_qty + ' ' + w2Escape(group.unit) + '</strong></span>'
            + '  </div>'
            + '  <div style="font-weight:700; color:var(--text-main); font-size:0.95rem;">'
            + '    食材总计: ' + (group.cost_missing ? '无进货' : I18N.fmtMoney(group.total_cost))
            + '  </div>'
            + '</div>'
            + '<div class="cost-trace-batches-tree">'
            + batchesHtml
            + '</div>'
            + '</div>';
    });

    treeContainer.innerHTML = html;
    openModalById('costTraceModal');
}

/**
 * 冲销/作废指定消耗流水记录并回滚批次与库存
 */
function voidDailyConsumption(consumptionId) {
    showCustomConfirmModal({
        title: '冲销作废消耗流水',
        message: '确定要冲销作废流水记录 #' + consumptionId + ' 吗？\n冲销后，这笔消耗扣掉的库存会原样加回去，对应食材的库存马上恢复。',
        confirmText: '确认冲销',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/dishes/daily_consumption/' + consumptionId + '/void', { method: 'POST' })
                .then(res => res.json())
                .then(ret => {
                    if (ret.status !== 'success') {
                        showToast(ret.msg || '冲销失败', 'error');
                        return;
                    }
                    showToast(ret.msg || '已成功冲销作废该消耗流水并恢复库存', 'success');

                    // 1. 刷新当日流水
                    loadDailyConsumption();

                    // 2. 刷新实时库存 Tab
                    loadInventoryData();
                })
                .catch(err => {
                    console.error('voidDailyConsumption error:', err);
                    showToast('网络异常，冲销失败', 'error');
                });
        }
    });
}

// --------------------------------------------------------------------------
// 4. 成本波动与毛利大盘分析
// --------------------------------------------------------------------------

/**
 * 切换成本大盘分析周期（7天 / 30天）
 */
function setCostAnalysisDays(days) {
    dishAnalysisDays = parseInt(days) || 7;
    const chips = document.querySelectorAll('.dish-period-selector .chip');
    chips.forEach(c => {
        if (parseInt(c.getAttribute('data-days')) === dishAnalysisDays) {
            c.classList.add('active');
        } else {
            c.classList.remove('active');
        }
    });
    loadDishCostAnalysis(dishAnalysisDays);
}

/**
 * 加载成本波动与毛利大盘数据
 */
function loadDishCostAnalysis(days) {
    const periodDays = days || dishAnalysisDays || 7;
    apiFetch('/api/dishes/cost_analysis?days=' + periodDays)
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') {
                showToast(ret.msg || '加载成本趋势分析失败', 'error');
                return;
            }
            dishCostAnalysisData = ret.data || {};
            renderDishCostAnalysisDashboard(dishCostAnalysisData);
        })
        .catch(err => {
            console.error('loadDishCostAnalysis error:', err);
            showToast('网络异常，无法加载成本分析大盘', 'error');
        });
}

/**
 * 渲染成本大盘 KPI、对比网格、排行榜与预警面板
 */
function renderDishCostAnalysisDashboard(data) {
    const summary = data.summary || {};
    const dishes = data.dishes || [];

    // 1. 顶部 KPI
    const avgMarginEl = document.getElementById('insightAvgMargin');
    const totalCostEl = document.getElementById('insightTotalCost');
    const costUpEl = document.getElementById('insightCostUpCount');
    const alertsEl = document.getElementById('insightIngredientAlerts');

    const overallMargin = Number(summary.overall_gross_margin_rate || 0);
    if (avgMarginEl) {
        avgMarginEl.innerText = overallMargin.toFixed(1) + '%';
        avgMarginEl.style.color = marginTextColor(overallMargin);
    }
    if (totalCostEl) totalCostEl.innerText = I18N.fmtMoney(summary.total_cost || 0);

    const costUpDishes = dishes.filter(d => (d.avg_unit_cost > d.theoretical_cost) && d.total_sold_quantity > 0);
    if (costUpEl) costUpEl.innerText = costUpDishes.length;

    // 食材进价异常预警数
    let anomalyCount = 0;
    dishes.forEach(d => {
        if (d.cost_variance > 0 && d.total_sold_quantity > 0) anomalyCount++;
    });
    if (alertsEl) alertsEl.innerText = anomalyCount;

    // 2. 各餐品真实加权成本 vs 理论基准成本对比卡片网格
    renderDishCostComparisonGrid(dishes);

    // 3. 毛利率排行榜
    renderDishMarginRanking(dishes);

    // 4. 食材涨价穿透预警面板
    renderDishPriceAnomalyImpact(dishes, costUpDishes);
}

/**
 * 渲染真实成本 vs 理论基准成本对比网格
 */
function renderDishCostComparisonGrid(dishes) {
    const grid = document.getElementById('dishCostComparisonGrid');
    if (!grid) return;

    if (dishes.length === 0) {
        grid.innerHTML = '<div style="grid-column: 1 / -1; padding: 30px; text-align: center; color: var(--text-muted); background: var(--bg-card); border-radius: 8px; border: 1px dashed var(--border-color);">'
            + '分析周期内暂无餐品售出消耗记录'
            + '</div>';
        return;
    }

    let html = '';
    dishes.forEach(d => {
        const hasSales = d.total_sold_quantity > 0;
        const variance = Number(d.cost_variance || 0);
        let varianceTag = '';

        if (!hasSales) {
            varianceTag = '<span class="badge badge-neutral" style="font-size:0.72rem;">周期内无售出</span>';
        } else if (variance > 0.05) {
            varianceTag = '<span class="badge badge-danger" style="font-size:0.72rem;">真实成本 +' + I18N.fmtMoney(variance) + ' (上涨)</span>';
        } else if (variance < -0.05) {
            varianceTag = '<span class="badge badge-success" style="font-size:0.72rem;">真实成本 -' + I18N.fmtMoney(Math.abs(variance)) + ' (下降)</span>';
        } else {
            varianceTag = '<span class="badge badge-neutral" style="font-size:0.72rem;">与理论持平</span>';
        }

        const marginMissing = d.avg_gross_margin_rate == null || d.avg_unit_cost == null;
        const marginRate = (!marginMissing && hasSales) ? Number(d.avg_gross_margin_rate) : 0;
        const marginColorVal = marginTextColor(marginRate);

        html += '<div class="card dish-cost-compare-card" style="border-left:4px solid ' + (variance > 0.05 ? 'var(--danger, #dc2626)' : 'var(--primary, #0f766e)') + ';">'
            + '<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:8px;">'
            + '  <div>'
            + '    <h4 style="margin:0 0 4px 0; font-size:1rem; font-weight:600; color:var(--text-main);">' + w2Escape(d.name) + '</h4>'
            + '    <div style="font-size:0.75rem; color:var(--text-muted);">' + (d.category ? ('<span class="badge badge-info" style="font-size:0.68rem;">' + w2Escape(d.category) + '</span> ') : '') + '售价: ' + I18N.fmtMoney(d.price) + '</div>'
            + '  </div>'
            + '  <div>' + varianceTag + '</div>'
            + '</div>'
            + '<div style="display:grid; grid-template-columns:repeat(2, 1fr); gap:8px; margin-bottom:10px; background:var(--bg-subtle, #f8fafc); border-radius:6px; padding:8px 10px;">'
            + '  <div>'
            + '    <div style="font-size:0.72rem; color:var(--text-muted);">真实加权单份成本</div>'
            + '    <div style="font-size:0.95rem; font-weight:700; color:' + (variance > 0.05 ? 'var(--danger, #dc2626)' : 'var(--primary, #0f766e)') + ';">' + (d.avg_unit_cost == null ? '无进货' : I18N.fmtMoney(d.avg_unit_cost)) + '</div>'
            + '  </div>'
            + '  <div>'
            + '    <div style="font-size:0.72rem; color:var(--text-muted);">理论基准单份成本</div>'
            + '    <div style="font-size:0.95rem; font-weight:600; color:var(--text-main);">' + (d.theoretical_cost == null ? '无进货' : I18N.fmtMoney(d.theoretical_cost)) + '</div>'
            + '  </div>'
            + '</div>'
            + '<div style="display:flex; justify-content:space-between; align-items:center; font-size:0.78rem; color:var(--text-secondary); margin-bottom:4px;">'
            + '  <span>累计售出: <strong>' + d.total_sold_quantity + ' 份</strong> (营收 ' + I18N.fmtMoney(d.total_revenue) + ')</span>'
            + '  <span>实际毛利率: <strong style="color:' + marginColorVal + ';">' + (marginMissing ? '无进货' : (marginRate.toFixed(1) + '%')) + '</strong></span>'
            + '</div>'
            + '<div style="height:6px; background:var(--border-color); border-radius:3px; overflow:hidden;">'
            + '  <div style="width:' + Math.min(100, Math.max(0, marginRate)) + '%; height:100%; background:' + marginColorVal + '; border-radius:3px;"></div>'
            + '</div>'
            + '</div>';
    });

    grid.innerHTML = html;
}

/**
 * 渲染毛利率排行榜
 */
function renderDishMarginRanking(dishes) {
    const container = document.getElementById('dishMarginRanking');
    if (!container) return;

    const validDishes = dishes.filter(d => d.total_sold_quantity > 0);
    if (validDishes.length === 0) {
        container.innerHTML = '<div style="padding:20px; text-align:center; color:var(--text-muted);">周期内暂无售出数据</div>';
        return;
    }

    const sorted = [...validDishes].sort((a, b) => b.avg_gross_margin_rate - a.avg_gross_margin_rate);

    let html = '<div class="ranking-list">';
    sorted.forEach((d, idx) => {
        const gm = Number(d.avg_gross_margin_rate || 0);
        const gmColor = marginTextColor(gm);
        const rankMedal = '#' + (idx + 1);

        html += '<div style="display:flex; justify-content:space-between; align-items:center; padding:8px 0; border-bottom:1px solid var(--border-color);">'
            + '<div style="display:flex; align-items:center; gap:8px;">'
            + '  <span style="font-size:0.9rem; font-weight:700; width:24px; text-align:center;">' + rankMedal + '</span>'
            + '  <div>'
            + '    <div style="font-weight:600; font-size:0.88rem; color:var(--text-main);">' + w2Escape(d.name) + '</div>'
            + '    <div style="font-size:0.72rem; color:var(--text-muted);">售出 ' + d.total_sold_quantity + ' 份 ｜ 营业额 ' + I18N.fmtMoney(d.total_revenue) + '</div>'
            + '  </div>'
            + '</div>'
            + '<div style="text-align:right;">'
            + '  <div style="font-weight:700; font-size:0.92rem; color:' + gmColor + ';">' + gm.toFixed(1) + '%</div>'
            + '  <div style="font-size:0.72rem; color:var(--text-muted);">毛利 ' + I18N.fmtMoney(d.total_gross_profit) + '</div>'
            + '</div>'
            + '</div>';
    });
    html += '</div>';

    container.innerHTML = html;
}

/**
 * 渲染食材涨价穿透预警面板
 */
function renderDishPriceAnomalyImpact(dishes, costUpDishes) {
    const container = document.getElementById('dishPriceAnomalyImpact');
    if (!container) return;

    if (costUpDishes.length === 0) {
        container.innerHTML = '<div style="padding:20px; text-align:center; color:var(--success, #16a34a); background:rgba(22,163,74,0.06); border-radius:8px;">'
            + '<div style="font-size:0.88rem; font-weight:600;">大盘成本稳健，无进价上涨挤压毛利异常</div>'
            + '<div style="font-size:0.76rem; color:var(--text-muted); margin-top:2px;">所有餐品的 FIFO 实际消耗成本均未超出理论基准成本</div>'
            + '</div>';
        return;
    }

    let html = '';
    costUpDishes.forEach(d => {
        const variance = Number(d.cost_variance || 0);
        html += '<div style="padding:10px 12px; margin-bottom:8px; border-left:4px solid var(--danger, #dc2626); background:rgba(239,68,68,0.05); border-radius:4px;">'
            + '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">'
            + '  <div style="font-weight:600; font-size:0.88rem; color:var(--text-main);">' + w2Escape(d.name) + '</div>'
            + '  <span class="badge badge-danger" style="font-size:0.7rem;">单份成本上涨 ' + I18N.fmtMoney(variance) + '</span>'
            + '</div>'
            + '<div style="font-size:0.78rem; color:var(--text-secondary); line-height:1.4;">'
            + '由于进货食材单价上涨，实际 FIFO 单份成本达到 <strong>' + (d.avg_unit_cost == null ? '无进货' : I18N.fmtMoney(d.avg_unit_cost)) + '</strong> (理论基准 ' + (d.theoretical_cost == null ? '无进货' : I18N.fmtMoney(d.theoretical_cost)) + ')，导致实际毛利率下滑至 <strong style="color:var(--danger, #dc2626);">' + (d.avg_gross_margin_rate == null ? '无进货' : (Number(d.avg_gross_margin_rate).toFixed(1) + '%')) + '</strong>。'
            + '</div>'
            + '</div>';
    });

    container.innerHTML = html;
}

// U-12（dish 部分）：全局挂载（原位于 main.js 导出块，随域迁出）
if (typeof window !== 'undefined') {
    window.addDishIngredientRow = addDishIngredientRow;
    window.removeDishIngredientRow = removeDishIngredientRow;
    window.onDishIngredientSkuChange = onDishIngredientSkuChange;
    window.calcDishModalTheoryCost = calcDishModalTheoryCost;
    window.saveDishModal = saveDishModal;
    window.deleteDish = deleteDish;
    window.initDishConsumptionDate = initDishConsumptionDate;
    window.setDishConsumptionToday = setDishConsumptionToday;
    window.setDishConsumptionYesterday = setDishConsumptionYesterday;
    window.onDishConsumptionDateChange = onDishConsumptionDateChange;
    window.loadDailyConsumption = loadDailyConsumption;
    window.filterDishConsumeEntryTable = filterDishConsumeEntryTable;
    window.adjustDishConsumeQty = adjustDishConsumeQty;
    window.onDishConsumeQtyInput = onDishConsumeQtyInput;
    window.updateDishConsumeSummary = updateDishConsumeSummary;
    window.resetDishConsumeInputs = resetDishConsumeInputs;
    window.submitDailyConsumptionBatch = submitDailyConsumptionBatch;
    window.showCostTraceModal = showCostTraceModal;
    window.voidDailyConsumption = voidDailyConsumption;
    window.setCostAnalysisDays = setCostAnalysisDays;
    window.loadDishCostAnalysis = loadDishCostAnalysis;
}
