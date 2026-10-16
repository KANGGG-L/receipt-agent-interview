// demo/static/js/inventory_board.js
// 库存域：库存管理层级与变动闭环（Category 07）、库存看板与供应商统计
// （InventoryUI）、库存流水台账、SKU 合并、极简 AI 洞察（价格异常雷达）、
// M5 价格走势 Modal、SKU 概览、owner 价格预警阈值控件。
// F4·S4-11 从 main.js 按域抽离（两段），逐字节一致；加载顺序在 main.js 之后。
// =============================================================
// Category 07: 库存管理层级与变动闭环（AC-07-02, AC-07-06）
// =============================================================

function toggleInvManageMenu() {
    const menu = document.getElementById('invManageMenu');
    if (menu) menu.classList.toggle('hide');
}
window.toggleInvManageMenu = toggleInvManageMenu;

document.addEventListener('click', (e) => {
    const menu = document.getElementById('invManageMenu');
    const btn = document.getElementById('btnManageInvMenu');
    if (menu && !menu.classList.contains('hide')) {
        if (btn && !btn.contains(e.target) && !menu.contains(e.target)) {
            menu.classList.add('hide');
        }
    }
});

let currentMovementSku = null;
function openStockMovementModal(skuId, skuName, currentStock, baseUnit) {
    currentMovementSku = { id: skuId, name: skuName, stock: currentStock, unit: baseUnit };
    const modal = document.getElementById('modalStockMovementDialog');
    const nameEl = document.getElementById('stockMovementSkuName');
    const stockEl = document.getElementById('stockMovementCurrentStock');
    const unitEl = document.getElementById('stockMovementUnit');
    const idEl = document.getElementById('stockMovementSkuId');
    const qtyEl = document.getElementById('stockMovementQty');
    const noteEl = document.getElementById('stockMovementNote');

    if (nameEl) nameEl.textContent = skuName;
    if (stockEl) stockEl.textContent = `(当前库存: ${currentStock} ${baseUnit})`;
    if (unitEl) unitEl.textContent = baseUnit;
    if (idEl) idEl.value = skuId;
    if (qtyEl) qtyEl.value = '';
    if (noteEl) noteEl.value = '';

    updateStockMovementLabels();
    if (modal) openAccessibleModal(modal, '#stockMovementQty');
}
window.openStockMovementModal = openStockMovementModal;

function closeStockMovementModal() {
    const modal = document.getElementById('modalStockMovementDialog');
    if (modal) closeAccessibleModal(modal);
    currentMovementSku = null;
}
window.closeStockMovementModal = closeStockMovementModal;

function updateStockMovementLabels() {
    const kind = document.getElementById('stockMovementKind').value;
    const qtyLabel = document.getElementById('stockMovementQtyLabel');
    if (qtyLabel) {
        if (kind === 'stocktake') qtyLabel.textContent = '盘点实际库存';
        else if (kind === 'waste') qtyLabel.textContent = '报损消耗数量';
        else qtyLabel.textContent = '消耗出库数量';
    }
}
window.updateStockMovementLabels = updateStockMovementLabels;

// 消耗/报损超过账面库存时：先问老板，明确确认并写明原因后才带 allow_negative 提交，库存不会悄悄变成负数。
// 返回 null 表示老板选择不记录；'' 表示库存够、不需要确认；其余是老板写的原因。
async function askStockOverdrawReason(message) {
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

// 发出消耗/报损请求。返回 null = 老板取消；否则 { httpStatus, ret }。
async function postStockOutflow(url, body, stock, unit, verb) {
    const send = payload => apiFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
    }).then(res => Promise.all([res.status, res.json().catch(() => null)]));
    const qty = Number(body.quantity);
    const have = Number(stock || 0);
    let reason = '';
    if (qty > have + 1e-9) {
        const diff = Math.round((qty - have) * 1000) / 1000;
        reason = await askStockOverdrawReason(
            `账面库存只有 ${have} ${unit || ''}，这次要${verb} ${qty} ${unit || ''}，差 ${diff} ${unit || ''}。\n多半是进货单还没录入。如果实物确实用了这么多，可以继续记录，库存会显示成负数，请尽快补录进货单。`);
        if (reason === null) return null;
    }
    let [httpStatus, ret] = await send(reason ? Object.assign({}, body, { allow_negative: true, override_reason: reason }) : body);
    if (httpStatus === 409 && ret && ret.code === 'INSUFFICIENT_STOCK' && !reason) {
        const again = await askStockOverdrawReason(ret.msg || '库存不够扣。');
        if (again === null) return null;
        [httpStatus, ret] = await send(Object.assign({}, body, { allow_negative: true, override_reason: again }));
    }
    return { httpStatus, ret };
}

async function submitStockMovement() {
    if (!currentMovementSku) return;
    const skuId = currentMovementSku.id;
    const kind = document.getElementById('stockMovementKind').value;
    const qty = parseFloat(document.getElementById('stockMovementQty').value);
    const note = (document.getElementById('stockMovementNote').value || '').trim();

    if (isNaN(qty) || qty < 0) {
        showToast('请输入有效的数量', 'error');
        return;
    }

    let url = `/api/inventory/${skuId}/${kind}`;
    let body = {};
    if (kind === 'stocktake') {
        body = { actual_qty: qty, note: note };
    } else {
        body = { quantity: qty, notes: note };
    }

    const movementSend = (kind === 'stocktake')
        ? apiFetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        }).then(res => Promise.all([res.status, res.json().catch(() => null)]))
        : postStockOutflow(url, body, currentMovementSku.stock, currentMovementSku.unit, kind === 'waste' ? '报损' : '扣减')
            .then(o => (o ? [o.httpStatus, o.ret] : null));
    movementSend
    .then(pair => {
        if (!pair) return;
        const [httpStatus, ret] = pair;
        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('记录变动失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            return;
        }

        showToast(`已成功记录库存变动（${ret.msg || '操作成功'}）`, 'success');
        closeStockMovementModal();
        loadInventoryData();
        if (typeof loadInventoryLedger === 'function') loadInventoryLedger();
    })
    .catch(err => {
        console.error('库存变动提交失败:', err);
        showToast('提交库存变动失败，请检查网络设置', 'error');
    });
}
window.submitStockMovement = submitStockMovement;

// =============================================================
// Category 08: 全局快捷键与无障碍键盘链路（AC-08-03 至 AC-08-05）
// =============================================================

function initGlobalKeyboardShortcuts() {
    document.addEventListener('keydown', (e) => {
        // Cmd+S / Ctrl+S 保存拦截
        if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
            const prefillCard = document.getElementById('prefillFormCard');
            if (prefillCard && !prefillCard.classList.contains('hide')) {
                e.preventDefault();
                submitSaveEdited(false);
                return;
            }
        }

        // Esc 安全关闭弹窗 (AC-08-04, AC-08-05)
        if (e.key === 'Escape') {
            const approveModal = document.getElementById('modalApproveConfirmDialog');
            if (approveModal && !approveModal.classList.contains('hide')) {
                closeApproveConfirmModal();
                return;
            }
            const conflictModal = document.getElementById('modalConflictDiffDialog');
            if (conflictModal && !conflictModal.classList.contains('hide')) {
                closeConflictDiffModal(false);
                return;
            }
            const stockModal = document.getElementById('modalStockMovementDialog');
            if (stockModal && !stockModal.classList.contains('hide')) {
                closeStockMovementModal();
                return;
            }
        }

        // AC-08-06: role="button" 的非语义元素（banner/toggle/metric-card）键盘激活
        if ((e.key === 'Enter' || e.key === ' ') && e.target
                && e.target.getAttribute && e.target.getAttribute('role') === 'button'
                && e.target.tagName !== 'BUTTON' && e.target.tagName !== 'A') {
            e.preventDefault();
            e.target.click();
            return;
        }
        // 防止在输入框内按 Cmd+Enter 误触发审批 (AC-08-04)
        if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
            if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) {
                e.preventDefault();
                return;
            }
        }
    });
}

// -------------------------------------------------------------
// 8. 库存看板与供应商统计获取
// -------------------------------------------------------------
const InventoryUI = {
    q: '',
    category: '',
    stock: 'all',      // all | low
    price: 'all',      // all | anomaly
    includeInactive: false,
    debounceTimer: null,
};
if (typeof window !== 'undefined') {
    window.InventoryUI = InventoryUI;
}

let inventorySkusMap = new Map();

function onInventorySearchInput(val) {
    const inputQ = document.getElementById('invSearchInput');
    if (inputQ) inputQ.value = val || '';
    InventoryUI.q = (val || '').trim();
    if (InventoryUI.debounceTimer) clearTimeout(InventoryUI.debounceTimer);
    InventoryUI.debounceTimer = setTimeout(() => {
        loadInventoryData();
    }, 200);
}

function onInventoryCategoryChange(val) {
    const selCat = document.getElementById('invCategorySelect');
    if (selCat) selCat.value = val || '';
    InventoryUI.category = val || '';
    loadInventoryData();
}

function onInventoryFilterSelectChange(val) {
    const selFlt = document.getElementById('invFilterSelect');
    if (selFlt) selFlt.value = val || 'all';
    if (val === 'low') {
        InventoryUI.stock = 'low';
        InventoryUI.price = 'all';
    } else if (val === 'anomaly') {
        InventoryUI.stock = 'all';
        InventoryUI.price = 'anomaly';
    } else {
        InventoryUI.stock = 'all';
        InventoryUI.price = 'all';
    }
    loadInventoryData();
}

function onInventoryIncludeInactiveChange(checked) {
    const chkInc = document.getElementById('invIncludeInactive');
    if (chkInc) chkInc.checked = !!checked;
    InventoryUI.includeInactive = !!checked;
    loadInventoryData();
}

function setInventoryStockFilter(val) {
    InventoryUI.stock = val;
    InventoryUI.price = 'all';
    const select = document.getElementById('invFilterSelect');
    if (select) select.value = val === 'low' ? 'low' : 'all';
    loadInventoryData();
}

function setInventoryPriceFilter(val) {
    InventoryUI.price = val;
    InventoryUI.stock = 'all';
    const select = document.getElementById('invFilterSelect');
    if (select) select.value = val === 'anomaly' ? 'anomaly' : 'all';
    loadInventoryData();
}

function resetInventoryFilters() {
    InventoryUI.q = '';
    InventoryUI.category = '';
    InventoryUI.stock = 'all';
    InventoryUI.price = 'all';
    InventoryUI.includeInactive = false;

    const inputQ = document.getElementById('invSearchInput');
    if (inputQ) inputQ.value = '';
    const selCat = document.getElementById('invCategorySelect');
    if (selCat) selCat.value = '';
    const selFlt = document.getElementById('invFilterSelect');
    if (selFlt) selFlt.value = 'all';
    const chkIn = document.getElementById('invIncludeInactive');
    if (chkIn) chkIn.checked = false;

    loadInventoryData();
}

function loadInventoryData() {
    const inputQ = document.getElementById('invSearchInput');
    const selCat = document.getElementById('invCategorySelect');
    const selFlt = document.getElementById('invFilterSelect');
    const chkInc = document.getElementById('invIncludeInactive');

    if (inputQ) InventoryUI.q = inputQ.value.trim();
    if (selCat) InventoryUI.category = selCat.value;
    if (selFlt) {
        const val = selFlt.value;
        if (val === 'low') {
            InventoryUI.stock = 'low';
            InventoryUI.price = 'all';
        } else if (val === 'anomaly') {
            InventoryUI.stock = 'all';
            InventoryUI.price = 'anomaly';
        } else {
            InventoryUI.stock = 'all';
            InventoryUI.price = 'all';
        }
    }
    if (chkInc) InventoryUI.includeInactive = !!chkInc.checked;

    const params = new URLSearchParams();
    if (InventoryUI.q) params.set('q', InventoryUI.q);
    if (InventoryUI.category) params.set('category', InventoryUI.category);
    if (InventoryUI.stock !== 'all') params.set('stock', InventoryUI.stock);
    if (InventoryUI.price !== 'all') params.set('price', InventoryUI.price);
    if (InventoryUI.includeInactive) params.set('include_inactive', '1');

    apiFetch('/api/inventory?' + params.toString())
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') {
            // WS5-1：接口"成功响应但业务失败"同样不得静默 —— 走同一降级路径。
            console.warn('[inventory] 库存接口返回非成功状态，保留上一次数据：', ret);
            degradeInventoryLoad();
            return;
        }

        const skus = ret.data || [];
        const meta = ret.meta || {};

        // WS3：用后端下发的本租户生效阈值回填可变阈值（百分数 → 小数）。
        // 缺省保持 0.10（接口未返回 meta 时不报错、不写成 undefined）。
        if (meta.price_anomaly_threshold_pct != null
            && Number.isFinite(Number(meta.price_anomaly_threshold_pct))) {
            PRICE_ANOMALY_THRESHOLD = Number(meta.price_anomaly_threshold_pct) / 100;
        }
        // 成功加载即清除降级标记（旧数据已由新数据替换）
        clearInventoryStale();
        // WS3：加载库存数据时回填 owner 门店阈值控件当前值（owner-only；staff 不请求）
        if (isOwnerRoleNow()) loadPriceThresholdControl();
        // WS5-2：处置状态来自后端持久化映射（不再用会话级内存 Set），刷新页面后仍有效
        priceAnomalyAckMap = meta.price_anomaly_ack_map || {};

        inventorySkusMap.clear();
        skus.forEach(s => inventorySkusMap.set(s.id, s));

        const totalEl = document.getElementById('valTotalSKU');
        if (totalEl) totalEl.innerText = meta.total_active != null ? meta.total_active : skus.length;

        const lowEl = document.getElementById('valLowStock');
        if (lowEl) lowEl.innerText = meta.low_stock_count != null ? meta.low_stock_count : skus.filter(s => s.is_low_stock).length;

        const priceEl = document.getElementById('valPriceAnomaly');
        if (priceEl) priceEl.innerText = meta.price_anomaly_count != null ? meta.price_anomaly_count : skus.filter(s => s.price_anomaly).length;

        // WS5-4：老板看板「价格异动雷达」置顶区块（先于 AI 洞察渲染，供横幅可见性判定）
        renderPriceAnomalyRadar(skus);

        // 同步加载极简 AI 发现胶囊
        loadAiLeanInsights();

        // WS4-5：库存数据刷新后同步刷新流水台账（含空态；不只在首次加载时取一次）
        if (typeof loadInventoryLedger === 'function') loadInventoryLedger();

        const tbody = document.getElementById('inventoryTableBody');
        if (!tbody) return;
        tbody.innerHTML = '';

        if (skus.length === 0) {
            // AC-07-04：三种空/错状态必须可区分——
            //   ① 接口失败：degradeInventoryLoad 保留旧数据 + 非最新标记（已有）
            //   ② 真·空库：无任何筛选生效且结果为空 → 「库存还没有食材」+ 仅「新增食材」
            //   ③ 筛选无结果：有筛选生效 → 「没有符合条件的食材」+ 清除筛选/新增食材
            const hasActiveFilter = !!(InventoryUI.q || InventoryUI.category
                || InventoryUI.stock !== 'all' || InventoryUI.price !== 'all'
                || InventoryUI.includeInactive);
            const tr = document.createElement('tr');
            if (hasActiveFilter) {
                tr.innerHTML = `
                <td colspan="7" class="col-center" style="padding: 32px 16px; color: var(--text-muted, #6b7280);">
                    <div style="font-size: 1rem; margin-bottom: 8px;">没有符合条件的食材</div>
                    <div style="display: flex; gap: 8px; justify-content: center; margin-top: 8px;">
                        <button class="btn btn-secondary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="resetInventoryFilters()">清除筛选</button>
                        <button class="btn btn-primary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="openAddSkuModal()">新增食材</button>
                    </div>
                </td>
            `;
            } else {
                tr.innerHTML = `
                <td colspan="7" class="col-center" style="padding: 32px 16px; color: var(--text-muted, #6b7280);">
                    <div style="font-size: 1rem; margin-bottom: 8px;">库存还没有食材</div>
                    <div style="font-size: 0.75rem; margin-bottom: 12px;">上传第一张进货单据后，食材会按明细自动建档</div>
                    <div style="display: flex; gap: 8px; justify-content: center; margin-top: 8px;">
                        <button class="btn btn-primary" style="padding: 4px 12px; font-size: 0.82rem;" onclick="openAddSkuModal()">新增食材</button>
                    </div>
                </td>
            `;
            }
            tbody.appendChild(tr);
            return;
        }

        skus.forEach(sku => {
            if (sku.base_unit && !availableUnits.includes(sku.base_unit)) {
                availableUnits.push(sku.base_unit);
            }

            const tr = document.createElement('tr');
            const skuStock = Number(sku.current_stock || 0);
            const alertVal = Number(sku.min_stock_alert || 0);
            const priceAnomaly = !!sku.price_anomaly;
            const vsAvgPct = (sku.vs_avg_pct != null && Number.isFinite(Number(sku.vs_avg_pct)))
                ? Number(sku.vs_avg_pct) : null;
            const priceText = sku.last_unit_price
                ? `$${Number(sku.last_unit_price).toFixed(2)}`
                : '—';

            const isActive = sku.active !== 0;
            if (!isActive) tr.classList.add('inv-row-inactive');
            if (priceAnomaly) tr.classList.add('inv-row-price-hot');
            if (sku.is_low_stock) tr.classList.add('inv-row-low-stock');

            // 统一胶囊机制：所有库存表告警胶囊都用 .badge + 颜色修饰符，
            // 数值类胶囊（涨价 / 低库存）统一堆叠在数值「上方」并与数值右边缘对齐。
            let priceAnomalyBadge = '';
            if (priceAnomaly) {
                const pctLabel = vsAvgPct != null
                    ? `涨价 +${vsAvgPct.toFixed(1)}%`
                    : '涨价异动';
                // WS3：阈值与基准均由后端同源动态给出 —— 库存角标基准是「较 30 日均价」
                // （与收据行级「较上一笔进价」是两套不同基准，此处显式标注，避免混读）。
                priceAnomalyBadge = `<span class="badge badge-danger" title="较 30 日均价涨幅超过 ${currentThresholdPctLabel()}%">${w2Escape(pctLabel)}</span>`;
            }
            const priceBtnCls = priceAnomaly ? 'btn btn-price-anomaly' : 'btn btn-secondary';

            // F5-L1：店员隐藏 owner-only 行内操作（价格走势 / 盘点 / 编辑 / 更多）。
            // 四个入口的后端接口均为 require_role("owner")，店员点击必 403，故渲染期直接不输出。
            // S3-12: a row keeps two visible actions. Stock movement stays in the more menu.
            const skuActionsHtml = isOwnerRoleNow() ? `
                        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.9rem; min-height:40px;"
                            onclick='viewInventoryDetail(${Number(sku.id)})'>详情</button>
                        <button class="${priceAnomaly ? 'btn btn-price-anomaly' : 'btn btn-secondary'}" style="padding:4px 10px; font-size:0.9rem; min-height:40px;"
                            aria-haspopup="menu" onclick='toggleInventoryMoreMenu(this, ${Number(sku.id)})'>更多 ▾</button>` : `
                        <button class="btn btn-secondary" style="padding:4px 10px; font-size:0.9rem; min-height:40px;"
                            onclick='viewInventoryDetail(${Number(sku.id)})'>详情</button>`;

            // 已停用 / 分类 等同属标签胶囊，统一走 .badge.badge-secondary（去掉内联自定义样式与间距，间距交由 .inv-name-cell 的 gap 控制）
            const activeBadge = !isActive
                ? `<span class="badge badge-secondary">已停用</span>`
                : '';
            // WS5-2：价格异动处置状态（后端持久化，刷新后仍保留）——渲染「已忽略 / 已采纳」，
            // 并给 owner 一个撤销入口（撤销属经营决策，与处置同权限）。
            const ackAction = sku.ack_action || '';
            const ackBadge = ackAction === 'adopt'
                ? `<span class="badge badge-info" title="已采纳该价格异动并纳入跟进（处置状态已持久化）">已采纳</span>`
                : (ackAction === 'ignore'
                    ? `<span class="badge badge-secondary" title="已忽略该价格异动（处置状态已持久化）">已忽略</span>`
                    : '');
            const ackRevokeHtml = (ackAction && isOwnerRoleNow())
                ? `<button class="btn btn-secondary" style="padding:2px 6px; font-size:0.72rem;"
                        onclick="revokePriceAnomalyAck(${Number(sku.id)})" title="撤销该价格异动处置（回到未处置）">撤销处置</button>`
                : '';
            // kg 列：仅重量单位显示折算（系数与后端 unit_factors 同源）；无换算依据时
            // 显式标「未知」（AC-07-03：未知=未知，不折成 0、不留空白破折号）。
            let kgCell = '<span class="badge badge-secondary" title="该单位无可用的 kg 换算依据，不作猜测">未知</span>';
            if (sku.standard_kg != null && Number.isFinite(Number(sku.standard_kg))) {
                const kgVal = Number(sku.standard_kg);
                kgCell = `${kgVal.toFixed(3)} kg`;
                // hover 提示按实际单位显示换算率
                const unitHint = '按「设置」里的单位换算折算成公斤';
                const weightHint = unitHint ? ` title="${unitHint}"` : '';
                kgCell = `<span${weightHint}>${w2Escape(kgCell)}</span>`;
            }
            tr.innerHTML = `
                <td class="inv-name-cell"><div class="inv-name-wrap"><span class="inv-name-text"><strong>${w2Escape(sku.name)}</strong></span>${activeBadge}${ackBadge}${ackRevokeHtml}</div></td>
                <td><span class="badge badge-secondary">${w2Escape(sku.category || '其他')}</span></td>
                <td class="col-right inv-metric-cell">
                    <div class="inv-metric-stack">
                        ${sku.is_low_stock ? `<span class="badge badge-danger">低库存</span>` : ''}
                        <span class="inv-metric-value ${sku.is_low_stock ? 'is-low' : (skuStock < 1 ? 'is-zero' : 'is-ok')}"><strong>${skuStock}</strong> ${w2Escape(sku.base_unit)}</span>
                    </div>
                </td>
                <td class="col-right">${kgCell}</td>
                <td class="col-right">
                    <span class="${sku.is_low_stock ? 'inv-alert-danger' : ''}">${alertVal} ${w2Escape(sku.base_unit)}</span>
                </td>
                <td class="col-right inv-metric-cell">
                    <div class="inv-metric-stack">
                        ${priceAnomalyBadge}
                        <span class="inv-price-value${priceAnomaly ? ' inv-price-hot' : ''}">${w2Escape(priceText)}</span>
                    </div>
                </td>
                <td class="col-center">
                    <div class="inv-actions">
                        ${skuActionsHtml}
                    </div>
                </td>
            `;
            tbody.appendChild(tr);
        });
    })
    .catch(err => {
        // WS5-1：接口失败绝不静默留在旧 UI —— 保留已有可用行（不清空到白屏）、
        // 给出可见提示与 console.warn 留痕，并标注「数据可能不是最新」。
        // 只用 warn：契约测试断言 console.error 调用次数为 0。
        console.warn('[inventory] 库存列表加载失败，保留上一次数据：', err);
        degradeInventoryLoad();
    });
}

// WS5-1：库存加载降级 —— 保留旧行并标注「可能不是最新」；再次成功加载时清除标记。
function degradeInventoryLoad() {
    showToast('库存加载失败，当前显示的数据可能不是最新', 'warning');
    const tbody = document.getElementById('inventoryTableBody');
    // 非库存页（无表格）时只留 toast，零 DOM 副作用。
    if (!tbody) return;
    if (typeof tbody.querySelectorAll === 'function') {
        tbody.querySelectorAll('tr').forEach(tr => {
            if (tr && tr.classList) tr.classList.add('inv-row-stale');
        });
    }
    if (document.getElementById('invStaleNotice')) return;   // 幂等：不重复插入
    const notice = document.createElement('div');
    notice.id = 'invStaleNotice';
    notice.className = 'inv-stale-notice';
    notice.style.cssText = 'margin:8px 0; padding:8px 12px; border-radius:6px;'
        + ' background:#fef3c7; color:#92400e; font-size:0.85rem;';
    notice.textContent = '库存数据加载失败，当前显示的是上一次成功加载的结果，可能不是最新。';
    const host = (tbody.closest && tbody.closest('.table-container'))
        || tbody.parentElement || null;
    if (host && host.insertBefore) host.insertBefore(notice, host.firstChild);
}

function clearInventoryStale() {
    const notice = document.getElementById('invStaleNotice');
    if (notice && notice.remove) notice.remove();
    const tbody = document.getElementById('inventoryTableBody');
    if (tbody && typeof tbody.querySelectorAll === 'function') {
        tbody.querySelectorAll('tr').forEach(tr => {
            if (tr && tr.classList) tr.classList.remove('inv-row-stale');
        });
    }
}

// AC-07-02：行常驻「详情」入口（只读，不扩权）——展示该 SKU 的库存/单位/阈值/单价事实。
function viewInventoryDetail(skuId) {
    const sku = (typeof inventorySkusMap !== 'undefined' && inventorySkusMap)
        ? inventorySkusMap.get(Number(skuId)) : null;
    if (!sku) {
        showToast('未找到该食材详情，请刷新库存后重试', 'warning');
        return;
    }
    const overlay = document.createElement('div');
    overlay.className = 'custom-modal-overlay';
    overlay.setAttribute('role', 'dialog');
    overlay.setAttribute('aria-modal', 'true');
    overlay.setAttribute('aria-label', '食材详情');
    overlay.style.cssText = 'position:fixed; top:0; left:0; width:100vw; height:100vh;'
        + ' background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:center; justify-content:center;';
    const stock = Number(sku.current_stock || 0);
    const unit = sku.base_unit || '未知单位';
    const kg = (sku.standard_kg != null && Number.isFinite(Number(sku.standard_kg)))
        ? Number(sku.standard_kg).toFixed(3) + ' kg' : '未知（无换算依据）';
    const lastPrice = (isOwnerRoleNow() && sku.last_unit_price != null && Number.isFinite(Number(sku.last_unit_price)))
        ? `$${Number(sku.last_unit_price).toFixed(2)}`
        : (isOwnerRoleNow() ? '未知' : '—');
    const rows = [
        ['品名', sku.name || ''],
        ['分类', sku.category || '其他'],
        ['当前库存', `${stock} ${unit}`],
        ['参考 kg', kg],
        ['警戒线', `${Number(sku.min_stock_alert || 0)} ${unit}`],
        ['最新进货单价', lastPrice],
        ['低库存', sku.is_low_stock ? '是' : '否'],
        ['价格异动', isOwnerRoleNow() ? (sku.price_anomaly ? '是' : '否') : '—'],
    ];
    const rowsHtml = rows.map(([k, v]) =>
        `<tr><th style="text-align:left; padding:6px 10px; color:var(--text-muted); font-weight:600;">${w2Escape(k)}</th>`
        + `<td style="padding:6px 10px;">${w2Escape(String(v))}</td></tr>`).join('');
    overlay.innerHTML = `<div class="card" style="width:440px; max-width:92vw; background:var(--card-bg,#fff);`
        + ` border-radius:10px; padding:20px; box-shadow:0 8px 24px rgba(0,0,0,0.25);">`
        + `<h4 style="margin:0 0 12px 0; font-size:1.05rem;">食材详情</h4>`
        + `<table style="width:100%; border-collapse:collapse; font-size:0.9rem;">${rowsHtml}</table>`
        + `<div style="display:flex; justify-content:flex-end; margin-top:16px;">`
        + `<button type="button" class="btn btn-secondary" id="btnCloseInvDetail" style="min-height:40px; padding:8px 16px;">关闭</button>`
        + `</div></div>`;
    document.body.appendChild(overlay);
    let onKey = null;
    const close = () => {
        if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
        if (onKey) window.removeEventListener('keydown', onKey);
        onKey = null;
    };
    overlay.querySelector('#btnCloseInvDetail').onclick = close;
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
    onKey = (e) => { if (e.key === 'Escape') { e.preventDefault(); close(); } };
    window.addEventListener('keydown', onKey);
    const btn = overlay.querySelector('#btnCloseInvDetail');
    if (btn) btn.focus();
}
window.viewInventoryDetail = viewInventoryDetail;

// -------------------------------------------------------------
// WS4-5：库存流水台账（只读；数据取自 GET /api/inventory/logs）
// -------------------------------------------------------------
function invLedgerKindLabel(kind) {
    // 与后端 api_export.py 既有 kind_map 同源口径（in/consume/waste/stocktake）。
    // 用词对齐：同一业务动作在系统里只能有一个中文名 —— 一律以 api_export.py 的
    // kind_map 为准，台账侧不得另起一套译名。
    const map = {
        'in': '采购入库',
        'consume': '餐品消耗',
        'waste': '后厨报损',
        'stocktake': '盘点调整',
    };
    return map[kind] || kind || '-';
}

function _invLedgerEmptyRow(msg) {
    return `<tr><td colspan="9" class="col-center" style="padding:24px 16px; color:var(--text-muted, #6b7280);">${w2Escape(msg)}</td></tr>`;
}

function loadInventoryLedger() {
    const tbody = document.getElementById('inventoryLedgerBody');
    if (!tbody) return;   // 台账卡片不在当前 DOM（非库存页 / 测试沙箱）：零动作

    const skuSel = document.getElementById('invLedgerSkuSelect');
    const kindSel = document.getElementById('invLedgerKindSelect');
    const startEl = document.getElementById('invLedgerStartDate');
    const endEl = document.getElementById('invLedgerEndDate');

    // 品名/SKU 下拉：从已加载的库存数据回填（数据刷新后选项同步，不只在首次加载时取一次）
    if (skuSel && typeof inventorySkusMap !== 'undefined' && inventorySkusMap && inventorySkusMap.size > 0) {
        const cur = skuSel.value;
        const opts = ['<option value="">全部食材</option>'];
        inventorySkusMap.forEach(s => {
            opts.push(`<option value="${Number(s.id)}">${w2Escape(s.name || ('#' + s.id))}</option>`);
        });
        const sig = String(inventorySkusMap.size);
        if (skuSel.dataset.optsSig !== sig) {
            skuSel.innerHTML = opts.join('');
            skuSel.dataset.optsSig = sig;
            if (cur && skuSel.querySelector(`option[value="${cur}"]`)) skuSel.value = cur;
        }
    }

    const params = new URLSearchParams();
    if (skuSel && skuSel.value) params.set('sku_id', skuSel.value);
    if (kindSel && kindSel.value) params.set('kind', kindSel.value);
    if (startEl && startEl.value) params.set('start_date', startEl.value);
    if (endEl && endEl.value) params.set('end_date', endEl.value);

    apiFetch('/api/inventory/logs?' + params.toString())
        .then(res => res.json())
        .then(ret => {
            if (!ret || ret.status !== 'success') {
                tbody.innerHTML = _invLedgerEmptyRow('加载库存流水失败');
                return;
            }
            const rows = ret.data || [];
            if (rows.length === 0) {
                tbody.innerHTML = _invLedgerEmptyRow('暂无库存流水记录');
                return;
            }
            tbody.innerHTML = '';
            rows.forEach(r => {
                const tr = document.createElement('tr');
                const qty = Number(r.qty || 0);
                const qtyText = (qty > 0 ? '+' : '') + qty;
                const amt = (r.amount != null && Number.isFinite(Number(r.amount))) ? Number(r.amount) : null;
                const rid = (r.receipt_id != null && Number(r.receipt_id) > 0) ? Number(r.receipt_id) : null;
                const receiptCell = rid
                    ? `<button type="button" class="btn btn-secondary" style="padding:2px 8px; font-size:0.75rem;" onclick="openReceiptFromPriceHistory(${rid})">#${rid}</button>`
                    : '-';
                tr.innerHTML = `
                    <td>${w2Escape(r.date || '')}</td>
                    <td><span class="badge badge-secondary">${w2Escape(invLedgerKindLabel(r.kind))}</span></td>
                    <td>${w2Escape(r.name || '')}</td>
                    <td class="col-right">${w2Escape(qtyText)}</td>
                    <td>${w2Escape(r.unit || '')}</td>
                    <td class="col-right">${amt != null ? amt.toFixed(2) : '-'}</td>
                    <td>${w2Escape(r.vendor || '')}</td>
                    <td class="col-center">${receiptCell}</td>
                    <td>${w2Escape(r.note || '')}</td>
                `;
                tbody.appendChild(tr);
            });
        })
        .catch(e => {
            tbody.innerHTML = _invLedgerEmptyRow('加载库存流水失败，请稍后重试');
            console.warn('[inventory-ledger] 台账加载失败', e);
        });
}

async function deduplicateSkus() {
    // WS1 防御性门禁：越权直达（staff 直接调用）时拒绝，不发出写请求。
    if (!isOwnerRoleNow()) {
        showToast('权限不足：仅老板（owner）可执行一键清理重复食材', 'error');
        return;
    }
    const okDedupe = await uiConfirm({
        title: '一键清理重复食材',
        message: '系统会把名字相同、只是后面多了流水号的重复食材合并成一个，库存流水一起并过去。\n合并过程会留下记录，确定要做吗？',
        confirmText: '开始清理',
        danger: true,
    });
    if (!okDedupe) return;
    apiFetch('/api/admin/maintenance/deduplicate', {
        method: 'POST',
        headers: _authHeaders({'Content-Type': 'application/json'})
    }).then(r => r.json()).then(ret => {
        if (ret.status === 'success') {
            const cnt = ret.merged_skus || 0;
            const groups = ret.merged_groups || 0;
            showToast(cnt > 0 ? `已清理 ${cnt} 个重复食材（${groups} 组）` : '没有重复食材需要清理', 'success');
            loadInventoryData();
        } else {
            showToast(ret.msg || ret.detail || '清理失败', 'error');
        }
    }).catch(e => showToast('清理请求失败: ' + e, 'error'));
}

function _resolveSkuObject(skuTarget) {
    if (!skuTarget) return null;
    if (typeof skuTarget === 'object') return skuTarget;
    if (typeof skuTarget === 'number') {
        return inventorySkusMap.get(skuTarget) || null;
    }
    if (typeof skuTarget === 'string') {
        const num = Number(skuTarget);
        if (!isNaN(num) && inventorySkusMap.has(num)) {
            return inventorySkusMap.get(num);
        }
        try { return JSON.parse(skuTarget); } catch(e) { return null; }
    }
    return null;
}

function toggleInventoryMoreMenu(btn, skuTarget) {
    // WS1 防御性门禁：越权直达（staff 直接调用）时拒绝，不打开 owner 级菜单。
    if (!isOwnerRoleNow()) {
        showToast('权限不足：仅老板（owner）可操作该菜单', 'error');
        return;
    }
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息', 'error');
        return;
    }
    const skuId = sku.id;
    const skuName = sku.name;
    const skuStock = Number(sku.current_stock || 0);
    const baseUnit = sku.base_unit;
    const isActive = sku.active !== 0;

    let menu = document.getElementById('invMoreMenu');
    if (!menu) {
        menu = document.createElement('div');
        menu.id = 'invMoreMenu';
        menu.className = 'unit-dropdown-menu';
        document.body.appendChild(menu);
    }
    if (!menu.classList.contains('hide') && menu.dataset.activeId == skuId) {
        menu.classList.add('hide');
        return;
    }
    const rect = btn.getBoundingClientRect();
    menu.style.position = 'fixed';
    menu.style.top = (rect.bottom + 4) + 'px';
    menu.style.left = Math.max(10, rect.left - 40) + 'px';
    menu.style.width = '160px';
    menu.style.minWidth = '160px';
    menu.style.zIndex = '99999';
    menu.dataset.activeId = skuId;

    const toggleText = isActive ? '停用' : '启用';

    // 产品合并：计用量/报损耗 → 统一出库（内部分类型），停用/删除保留在更多作为快捷，编辑内已含停用开关
    menu.innerHTML = `
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" onclick='closeInvMoreMenu(); viewPriceHistory(${Number(skuId)})'>价格走势</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" onclick='closeInvMoreMenu(); openStockMovementModal(${Number(skuId)}, ${jsStr(skuName)}, ${skuStock}, ${jsStr(baseUnit)})'>变动录入</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" onclick='closeInvMoreMenu(); openStocktakeModal(${Number(skuId)}, ${jsStr(skuName)}, ${skuStock}, ${jsStr(baseUnit)})'>盘点</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" onclick='closeInvMoreMenu(); openEditSkuModal(${Number(skuId)})'>编辑</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" onclick='closeInvMoreMenu(); openOutboundModal(${Number(skuId)}, ${jsStr(skuName)}, ${skuStock}, ${jsStr(baseUnit)})'>出库</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" style="color:${isActive ? '#b91c1c' : '#047857'};" onclick='closeInvMoreMenu(); toggleSkuActive(${Number(skuId)})'>${toggleText}</div>
        <div class="unit-dropdown-item" role="menuitem" tabindex="0" style="color:#b91c1c; border-top:1px dashed var(--border-color);" onclick='closeInvMoreMenu(); deleteSkuDirect(${Number(skuId)}, ${jsStr(skuName)})'>删除/清理</div>
    `;
    menu.classList.remove('hide');
    setTimeout(() => {
        const closeHandler = (e) => {
            if (!menu.contains(e.target) && e.target !== btn) {
                menu.classList.add('hide');
                document.removeEventListener('click', closeHandler);
            }
        };
        document.addEventListener('click', closeHandler);
    }, 10);
}

function openAddSkuModal() {
    const titleEl = document.getElementById('skuModalTitle');
    if (titleEl) titleEl.innerText = '新增食材';
    document.getElementById('skuModalId').value = '';
    document.getElementById('skuModalName').value = '';
    document.getElementById('skuModalCategory').value = '肉类';

    const unitInput = document.getElementById('skuModalUnit');
    if (unitInput) {
        unitInput.value = 'kg';
        unitInput.disabled = false;
    }
    const noticeEl = document.getElementById('skuModalUnitNotice');
    if (noticeEl) noticeEl.style.display = 'none';

    document.getElementById('skuModalAlert').value = '5.0';

    const activeGrp = document.getElementById('skuModalActiveGroup');
    if (activeGrp) activeGrp.style.display = 'none';

    const codeGrp = document.getElementById('skuModalAdvancedCode');
    if (codeGrp) codeGrp.style.display = 'none';

    openModalById('skuModal');
    if (typeof window.refreshSkuUnitChoices === 'function') window.refreshSkuUnitChoices();
}

function openEditSkuModal(skuTarget) {
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息，请刷新后再试', 'error');
        return;
    }
    const titleEl = document.getElementById('skuModalTitle');
    if (titleEl) titleEl.innerText = '编辑食材档案';
    document.getElementById('skuModalId').value = sku.id;
    document.getElementById('skuModalName').value = sku.name || '';
    document.getElementById('skuModalCategory').value = sku.category || '其他';

    const unitInput = document.getElementById('skuModalUnit');
    const noticeEl = document.getElementById('skuModalUnitNotice');
    if (unitInput) {
        unitInput.value = sku.base_unit || 'kg';
        const stock = Math.abs(Number(sku.current_stock || 0));
        if (stock > 1e-9) {
            unitInput.disabled = true;
            if (noticeEl) {
                noticeEl.innerText = `账面库存不为 0，禁止修改单位。请先通过盘点或记用量/报损耗将库存调整为 0。`;
                noticeEl.style.display = 'block';
            }
        } else {
            unitInput.disabled = false;
            if (noticeEl) noticeEl.style.display = 'none';
        }
    }

    document.getElementById('skuModalAlert').value = sku.min_stock_alert != null ? sku.min_stock_alert : 5.0;

    const activeGrp = document.getElementById('skuModalActiveGroup');
    if (activeGrp) {
        activeGrp.style.display = 'block';
        document.getElementById('skuModalActive').value = sku.active !== 0 ? '1' : '0';
    }

    const codeGrp = document.getElementById('skuModalAdvancedCode');
    if (codeGrp) {
        codeGrp.style.display = 'block';
        document.getElementById('skuModalCodeDisplay').innerText = String(sku.sku_code || '').replace(/^SKU-/i, '');
    }

    openModalById('skuModal');
    if (typeof window.refreshSkuUnitChoices === 'function') window.refreshSkuUnitChoices();
}

function submitSkuModal() {
    const skuId = document.getElementById('skuModalId').value;
    const name = document.getElementById('skuModalName').value.trim();
    const category = document.getElementById('skuModalCategory').value.trim() || '其他';
    const base_unit = document.getElementById('skuModalUnit').value.trim() || 'kg';
    const alertRaw = document.getElementById('skuModalAlert').value.trim();

    if (!name) {
        showToast('请填写真实品名', 'warning');
        return;
    }
    if (name.length > 64) {
        showToast('品名不能超过 64 个字符', 'warning');
        return;
    }

    const min_stock_alert = parseFloat(alertRaw);
    if (isNaN(min_stock_alert) || min_stock_alert < 0) {
        showToast('补货警戒线必须为大于或等于 0 的数值', 'warning');
        return;
    }

    const btn = document.getElementById('skuModalSubmitBtn');
    if (btn) btn.disabled = true;

    if (!skuId) {
        // Create
        apiFetch('/api/inventory/skus', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                name,
                category,
                base_unit,
                min_stock_alert
            })
        })
        .then(res => res.json())
        .then(ret => {
            if (btn) btn.disabled = false;
            if (ret.status === 'success') {
                closeModalById('skuModal');
                loadInventoryData();
                showToast(`食材「${name}」创建成功`, 'success');

                // 若由明细行内联发起，自动绑定回该行
                if (activeInlineSkuInput) {
                    const wrap = activeInlineSkuInput.closest('.sku-combobox-wrap');
                    if (wrap) {
                        const skuInput = wrap.querySelector('.inp-sku');
                        const idInput = wrap.querySelector('.inp-sku-id');
                        const badge = wrap.querySelector('.sku-badge');
                        if (skuInput) skuInput.value = name;
                        if (idInput) idInput.value = ret.id;
                        if (badge) {
                            badge.textContent = '已匹配SKU';
                            badge.className = 'badge badge-success sku-badge';
                        }
                    }
                    activeInlineSkuInput = null;
                }
            } else {
                if (ret.code === 'SKU_NAME_CONFLICT') {
                    showToast('已存在同名的启用食材，请修改品名', 'warning');
                } else {
                    showToast('创建失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
                }
            }
        })
        .catch(err => {
            if (btn) btn.disabled = false;
            console.error('创建食材请求失败:', err);
            showToast('创建失败，请检查网络后重试', 'error');
        });
    } else {
        // Edit
        const bodyData = {
            name,
            category,
            min_stock_alert,
            active: parseInt(document.getElementById('skuModalActive').value || '1')
        };
        const unitInput = document.getElementById('skuModalUnit');
        if (unitInput && !unitInput.disabled) {
            bodyData.base_unit = base_unit;
        }

        apiFetch('/api/inventory/skus/' + skuId, {
            method: 'PATCH',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(bodyData)
        })
        .then(res => res.json())
        .then(ret => {
            if (btn) btn.disabled = false;
            if (ret.status === 'success') {
                closeModalById('skuModal');
                loadInventoryData();
            } else {
                if (ret.code === 'UNIT_CHANGE_BLOCKED') {
                    showToast('账面库存不为 0，请先盘点调整为 0 再改单位', 'warning');
                } else if (ret.code === 'SKU_NAME_CONFLICT') {
                    showToast('已存在同名的启用食材，请修改品名', 'warning');
                } else {
                    showToast('更新失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
                }
            }
        })
        .catch(err => {
            if (btn) btn.disabled = false;
            console.error('更新食材请求失败:', err);
            showToast('更新失败，请检查网络后重试', 'error');
        });
    }
}

// -------------------------------------------------------------
// SKU 合并管理 (SKU Merge Modal)
// -------------------------------------------------------------
function openSkuMergeModalFromInv() {
    apiFetch('/api/inventory?include_inactive=0')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success' || !ret.data || ret.data.length < 2) {
            showToast('现有启用食材少于 2 种，无需合并', 'info');
            return;
        }
        const skus = ret.data;
        const sel = document.getElementById('skuMergePrimarySelect');
        const listDiv = document.getElementById('skuMergeSecondaryList');
        if (!sel || !listDiv) return;

        sel.innerHTML = '';
        skus.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.id;
            opt.textContent = `${s.name} (${s.category || '未分类'} · 当前库存 ${s.current_stock}${s.base_unit || ''})`;
            sel.appendChild(opt);
        });

        function updateSecondaryCheckboxes() {
            const primaryId = parseInt(sel.value);
            let html = '';
            skus.forEach(s => {
                if (s.id !== primaryId) {
                    html += `
                        <label style="display:flex; align-items:center; gap:8px; margin:4px 0; cursor:pointer;">
                            <input type="checkbox" class="chk-sku-sec" value="${s.id}">
                            <span>${w2Escape(s.name)} <small style="color:var(--text-muted);">[库存:${Number(s.current_stock) || 0}${w2Escape(s.base_unit)}]</small></span>
                        </label>
                    `;
                }
            });
            listDiv.innerHTML = html;
        }

        sel.onchange = updateSecondaryCheckboxes;
        updateSecondaryCheckboxes();
        openModalById('skuMergeModal');
    })
    .catch(err => {
        console.error('加载合并食材列表异常:', err);
        showToast('加载食材列表失败', 'error');
    });
}

function submitSkuMerge() {
    // WS1 防御性门禁：越权直达（staff 直接调用）时拒绝，不发出合并写请求。
    if (!isOwnerRoleNow()) {
        showToast('权限不足：仅老板（owner）可执行食材合并', 'error');
        return;
    }
    const primaryId = parseInt(document.getElementById('skuMergePrimarySelect').value);
    const secCheckboxes = document.querySelectorAll('.chk-sku-sec:checked');
    const secIds = Array.from(secCheckboxes).map(c => parseInt(c.value));

    if (!primaryId || secIds.length === 0) {
        showToast('请至少选择一个被合并的副食材', 'warning');
        return;
    }

    const btn = document.getElementById('skuMergeSubmitBtn');
    if (btn) btn.disabled = true;

    // WS6：已废弃的「合并期记忆同步」死勾选已删除 —— 后端合并端点早已无该空开关形参
    // （供应商别名改指在 db.merge_skus 内无条件执行，不受任何开关控制），
    // 前端不再收集这个半死不活的字段。
    apiFetch('/api/inventory/skus/merge', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            primary_sku_id: primaryId,
            secondary_sku_ids: secIds
        })
    })
    .then(res => res.json())
    .then(ret => {
        if (btn) btn.disabled = false;
        if (ret.status === 'success') {
            closeModalById('skuMergeModal');
            loadInventoryData();
            showToast('食材合并成功，历史流水与别名映射已转移', 'success');
        } else {
            showToast('合并失败：' + (ret.msg || ret.message || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        if (btn) btn.disabled = false;
        console.error('合并食材异常:', err);
        showToast('合并请求失败，请检查网络', 'error');
    });
}

async function deleteSkuDirect(skuId, skuName) {
    const okSku = await uiConfirm({
        title: '删除食材',
        message: '确定要删除食材「' + skuName + '」吗？\n如果从来没有进货记录，会直接删除；如果已经有进货记录，只会停用，数据不会丢。',
        confirmText: '删除',
        danger: true,
    });
    if (!okSku) return;

    apiFetch('/api/inventory/skus/' + skuId, {
        method: 'DELETE'
    })
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            loadInventoryData();
            showToast(ret.msg || '食材已处理', 'success');
        } else {
            showToast('删除失败：' + (ret.msg || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        console.error('删除食材异常:', err);
        showToast('删除请求失败', 'error');
    });
}

// -------------------------------------------------------------
// 极简 AI 发现逻辑 (Lean AI Insights Banner & Cards)
// -------------------------------------------------------------
let aiLeanInsightsData = null;
// WS5-2：价格异动处置状态改为**后端持久化**（price_anomaly_ack 表），
// 键 = sku_id 字符串，值 = {action: 'ignore'|'adopt', threshold_pct, snapshot_vs_pct, ...}。
// 由 loadInventoryData 从 /api/inventory 的 meta 回填（不再有会话级内存 Set，刷新即失效）。
let priceAnomalyAckMap = {};
// WS5-4：价格异动雷达命中的异动 SKU 数（供横幅可见性判定：有异动即展示横幅）
let priceAnomalyRadarCount = 0;

// WS5-4：横幅可见性统一判定 —— AI 洞察有内容或价格异动雷达有异动时展示。
function setAiLeanBannerVisible(visible) {
    const banner = document.getElementById('aiLeanBanner');
    if (!banner) return;
    if (isStaffRoleNow()) { banner.classList.add('hide'); return; }
    if (visible) banner.classList.remove('hide');
    else banner.classList.add('hide');
}

// WS5-4：老板看板「价格异动雷达」置顶区块 —— 按涨幅（vs_avg_pct）降序列出异动 SKU
// （Top N），每条可下钻价格走势 Modal；无异动时给出明确空态文案。
const PRICE_ANOMALY_RADAR_TOP_N = 5;

function _radarPctValue(s) {
    const v = (s && s.vs_avg_pct != null) ? Number(s.vs_avg_pct) : NaN;
    return Number.isFinite(v) ? v : null;
}

function renderPriceAnomalyRadar(skus) {
    const list = document.getElementById('priceAnomalyRadarList');
    if (!list) return;   // 容器不在当前 DOM（非库存页 / 测试沙箱）：零动作
    const sub = document.getElementById('priceAnomalyRadarSub');
    if (!isOwnerRoleNow()) {
        list.innerHTML = '<div style="padding:6px 2px; color:var(--text-muted, #6b7280); font-size:0.85rem;">'
            + '仅老板可见价格异动雷达。</div>';
        if (sub) sub.textContent = '';
        return;
    }
    const anomalies = (skus || []).filter(s => s && s.price_anomaly).slice().sort((a, b) => {
        const av = _radarPctValue(a);
        const bv = _radarPctValue(b);
        // 无涨幅读数的项排在最后（不给假数字撑排序）
        return (bv === null ? -Infinity : bv) - (av === null ? -Infinity : av);
    });
    priceAnomalyRadarCount = anomalies.length;

    if (anomalies.length === 0) {
        list.innerHTML = '<div style="padding:6px 2px; color:var(--text-muted, #6b7280); font-size:0.85rem;">'
            + '当前没有价格异动食材（全部在预警阈值内）。</div>';
        if (sub) sub.textContent = '';
        return;
    }

    const top = anomalies.slice(0, PRICE_ANOMALY_RADAR_TOP_N);
    if (sub) {
        sub.textContent = `共 ${anomalies.length} 项，按涨幅降序`
            + (anomalies.length > top.length ? `（仅显示前 ${top.length} 项）` : '');
    }
    list.innerHTML = top.map(s => {
        const pct = _radarPctValue(s);
        const pctText = (pct === null) ? '-' : ('+' + pct.toFixed(1) + '%');
        const ack = priceAnomalyAckMap[String(s.id)] || null;
        const ackText = ack && ack.action === 'adopt' ? '已采纳'
            : (ack && ack.action === 'ignore' ? '已忽略' : '');
        const ackBadge = ackText
            ? `<span class="badge ${ack.action === 'adopt' ? 'badge-info' : 'badge-secondary'}"
                   style="font-size:0.68rem; padding:1px 6px;">${ackText}</span>`
            : '';
        return `<button type="button" class="price-anomaly-radar-item"
                        style="display:flex; align-items:center; gap:8px; width:100%; text-align:left;
                               background:transparent; border:0; border-top:1px dashed rgba(148,163,184,0.4);
                               padding:5px 2px; cursor:pointer; font-size:0.85rem;
                               opacity:${ackText === '已忽略' ? '0.55' : '1'};"
                        onclick="viewPriceHistory(${Number(s.id)})"
                        title="点击查看该食材价格走势（较 30 日均价涨幅 ${w2Escape(pctText)}）">
                    <span style="flex:1; color:var(--text-primary, #1f2937);">${w2Escape(s.name)}</span>
                    <span style="color:#dc2626; font-weight:600;">${pctText}</span>${ackBadge}
                </button>`;
    }).join('');
}

function loadAiLeanInsights() {
    const banner = document.getElementById('aiLeanBanner');
    if (!banner) return;
    // 店员不发 AI 洞察（owner 域接口），横幅保持隐藏
    if (isStaffRoleNow()) { banner.classList.add('hide'); return; }

    apiFetch('/api/ai-insights')
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success' || !ret.data) {
            // WS5-4：AI 洞察缺失不再无条件隐藏 —— 价格异动雷达有异动时横幅仍需展示
            setAiLeanBannerVisible(priceAnomalyRadarCount > 0);
            return;
        }
        const data = ret.data;
        aiLeanInsightsData = data;
        const rawItems = data.top_price_risers || [];
        // WS5-2：处置状态来自后端持久化映射；已忽略项不再提醒（状态跨刷新保留）。
        const activeItems = rawItems.filter(
            it => (priceAnomalyAckMap[String(it.sku_id)] || {}).action !== 'ignore');

        if (activeItems.length === 0) {
            setAiLeanBannerVisible(priceAnomalyRadarCount > 0);
            return;
        }

        const sumText = document.getElementById('aiLeanSummaryText');
        if (sumText) {
            sumText.innerText = `检测到 ${activeItems.length} 项食材价格异常上涨，预估影响成本 HK$ ${data.total_impact_amount.toFixed(2)}`;
        }

        renderAiLeanCards(activeItems);
        setAiLeanBannerVisible(true);
    })
    .catch(err => {
        if (!window.__roleSwitchPending) console.error('加载极简 AI 发现异常:', err);
        // WS5-4：失败时也按雷达是否有异动决定可见性（不把有异动的看板一并藏掉）
        setAiLeanBannerVisible(priceAnomalyRadarCount > 0);
    });
}

function toggleAiLeanDetails() {
    const cards = document.getElementById('aiLeanCardsContainer');
    const btn = document.getElementById('btnToggleAiLean');
    if (!cards || !btn) return;
    const isHidden = cards.classList.contains('hide');
    if (isHidden) {
        cards.classList.remove('hide');
        btn.innerText = '收起';
    } else {
        cards.classList.add('hide');
        btn.innerText = '展开查看';
    }
}

function renderAiLeanCards(items) {
    const container = document.getElementById('aiLeanCardsContainer');
    if (!container) return;
    container.innerHTML = '';

    items.forEach(it => {
        const card = document.createElement('div');
        card.className = 'ai-lean-card-item';
        card.id = `aiLeanCard_${it.sku_id}`;

        // WS5-2：处置状态（后端持久化）——已处置的项不再展示对应动作按钮，
        // 采纳/忽略都走后端，成功后 toast 并刷新列表（状态跨刷新保留）。
        const ack = priceAnomalyAckMap[String(it.sku_id)] || null;
        const ackAction = ack ? ack.action : '';
        const ackBadge = ackAction === 'adopt'
            ? '<span class="badge badge-info" style="font-size:0.7rem; padding:1px 6px;">已采纳</span>'
            : (ackAction === 'ignore'
                ? '<span class="badge badge-secondary" style="font-size:0.7rem; padding:1px 6px;">已忽略</span>'
                : '');
        const adoptBtn = ackAction === 'adopt' ? '' :
            `<button type="button" class="btn-lean-act" onclick="ackPriceAnomaly(${Number(it.sku_id)}, 'adopt')" title="采纳该价格异动并发起跟进（owner）">采纳</button>`;
        const ignoreBtn = ackAction === 'ignore' ? '' :
            `<button type="button" class="btn-lean-act" style="color:var(--text-muted);" onclick="ackPriceAnomaly(${Number(it.sku_id)}, 'ignore')" title="忽略该价格异动（处置状态持久化）">忽略</button>`;

        card.innerHTML = `
            <div class="ai-lean-card-left">
                <div class="ai-lean-card-title">
                    <span style="color:#dc2626;">${w2Escape(it.name)}</span>
                    <span style="font-size:0.75rem; color:var(--text-muted);">· ${w2Escape(it.vendor)}</span>
                    <span class="badge badge-danger" style="font-size:0.7rem; padding:1px 6px;">涨幅 +${it.change_pct}%</span>
                    ${ackBadge}
                </div>
                <div class="ai-lean-card-desc">
                    单价 $${it.earliest_price} -&gt; <strong style="color:#dc2626;">$${it.latest_price}</strong>/${w2Escape(it.unit)} · 累计多支出 <strong style="color:var(--primary);">HK$ ${it.impact_amount}</strong>
                </div>
            </div>
            <div class="ai-lean-card-actions">
                ${it.sku_id ? `<button type="button" class="btn-lean-act" onclick="viewPriceHistory(${Number(it.sku_id)})">查看走势</button>` : ''}
                <button type="button" class="btn-lean-act" onclick="copyAiLeanEvidence(${Number(it.sku_id)})" title="复制异动记录明细">复制记录</button>
                ${adoptBtn}
                ${ignoreBtn}
            </div>
        `;
        container.appendChild(card);
    });
}

function copyAiLeanEvidence(skuId) {
    if (!aiLeanInsightsData || !aiLeanInsightsData.top_price_risers) return;
    const item = aiLeanInsightsData.top_price_risers.find(x => x.sku_id === skuId);
    if (!item || !item.evidence_text) return;

    navigator.clipboard.writeText(item.evidence_text).then(() => {
        showToast('已复制价格异动明细至剪贴板', 'success');
    }).catch(() => {
        showToast(item.evidence_text, 'info', TOAST_DURATION.long);
    });
}

// WS5-2：价格异动处置（采纳 / 忽略）—— 走后端持久化 `POST /api/inventory/{id}/price_anomaly_ack`，
// 成功 toast 并刷新列表（处置状态跨刷新保留；adopt 埋点 price_anomaly_adopted 由后端发出）。
function ackPriceAnomaly(skuId, action) {
    const id = Number(skuId);
    if (!Number.isFinite(id) || id <= 0) return;
    if (action !== 'ignore' && action !== 'adopt') return;
    apiFetch(`/api/inventory/${id}/price_anomaly_ack`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action: action})
    })
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            showToast(action === 'adopt' ? '已采纳该价格异动并纳入跟进' : '已忽略该价格异动', 'success');
            loadInventoryData();
        } else {
            showToast('处置失败：' + (ret.msg || ret.message || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        // 只用 warn：不把可恢复的写失败升级成 error 日志（契约测试断言 error 为 0）
        console.warn('[price-anomaly-ack] 处置请求失败：', err);
        showToast('处置失败，请检查网络后重试', 'error');
    });
}

// WS5-2：撤销处置（回到未处置态）—— owner 专用，成功后刷新列表。
function revokePriceAnomalyAck(skuId) {
    const id = Number(skuId);
    if (!Number.isFinite(id) || id <= 0) return;
    apiFetch(`/api/inventory/${id}/price_anomaly_ack`, {method: 'DELETE'})
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            showToast('已撤销该价格异动处置', 'success');
            loadInventoryData();
        } else {
            showToast('撤销失败：' + (ret.msg || ret.message || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        console.warn('[price-anomaly-ack] 撤销请求失败：', err);
        showToast('撤销失败，请检查网络后重试', 'error');
    });
}

async function toggleSkuActive(skuTarget) {
    const sku = _resolveSkuObject(skuTarget);
    if (!sku) {
        showToast('无法读取食材档案信息', 'error');
        return;
    }
    const isActive = sku.active !== 0;
    const targetActive = isActive ? 0 : 1;

    if (isActive) {
        const okOff = await uiConfirm({
            title: '停用食材',
            message: '确定要停用食材「' + sku.name + '」吗？\n停用后进货不再自动对到这个食材，列表里也默认不显示；以后可以再启用。',
            confirmText: '停用',
            danger: true,
        });
        if (!okOff) return;
    }

    apiFetch('/api/inventory/skus/' + sku.id, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ active: targetActive })
    })
    .then(res => res.json())
    .then(ret => {
        if (ret.status === 'success') {
            loadInventoryData();
        } else {
            showToast('操作失败：' + (ret.message || ret.msg || '请稍后重试'), 'error');
        }
    })
    .catch(err => {
        console.error('食材启停用请求失败:', err);
        showToast('操作失败，请检查网络后重试', 'error');
    });
}

function closeInvMoreMenu() {
    const menu = document.getElementById('invMoreMenu');
    if (menu) menu.classList.add('hide');
}

// M5 价格走势：专业 Modal（曲线 + 30 日均价/异动指标 + 明细表）
// 替代原生 alert；异动阈值**不再硬编码**（由后端 app_settings 下发，见下方
// PRICE_ANOMALY_THRESHOLD 与 currentThresholdPctLabel），口径与 /api/inventory 同源。
// 纯函数 computePriceHistorySummary / buildPriceChartSvg 可测
// -------------------------------------------------------------
// WS3：阈值改为**可变**并由后端回填（SSOT 在后端 app_settings：admin 全局默认 + owner
// 门店覆盖）。默认 0.10 仅作接口未返回 meta/summary 时的缺省；/api/inventory 成功回调用
// meta.price_anomaly_threshold_pct、renderPriceHistoryModal 用 summary.threshold_pct 回填。
// 用 let 而非 var：getter 仍是同一词法绑定，纯函数 computePriceHistorySummary /
// buildPriceChartSvg 可经 opts.threshold 注入，保持"纯函数可测"性质。
let PRICE_ANOMALY_THRESHOLD = 0.10;

// 阈值百分数标签（整数不补小数位；用于 tooltip / 图例动态文案）
function currentThresholdPctLabel() {
    const pct = Number(PRICE_ANOMALY_THRESHOLD) * 100;
    if (!Number.isFinite(pct)) return '10';
    return Number.isInteger(pct) ? String(pct) : pct.toFixed(1);
}

/**
 * 从价格点序列计算看板摘要（与后端 summary 同语义，前端可独立重算/回退）。
 * points: [{date, unit_price, ...}] 已按日期升序
 */
function computePriceHistorySummary(points, opts) {
    const threshold = (opts && opts.threshold != null) ? opts.threshold : PRICE_ANOMALY_THRESHOLD;
    const windowDays = (opts && opts.windowDays != null) ? opts.windowDays : 30;
    const now = (opts && opts.now) ? opts.now : new Date();
    const list = Array.isArray(points) ? points : [];
    const prices = list
        .map(p => Number(p.unit_price))
        .filter(n => Number.isFinite(n) && n > 0);

    if (prices.length === 0) {
        return {
            count: 0,
            latest_price: null,
            avg_30d: null,
            min_price: null,
            max_price: null,
            vs_avg_pct: null,
            is_anomaly: false,
            threshold_pct: Math.round(threshold * 1000) / 10,
        };
    }

    const latest = prices[prices.length - 1];
    const minP = Math.min.apply(null, prices);
    const maxP = Math.max.apply(null, prices);

    const cutoff = new Date(now.getTime() - windowDays * 86400000);
    const cutoffStr = [
        cutoff.getFullYear(),
        String(cutoff.getMonth() + 1).padStart(2, '0'),
        String(cutoff.getDate()).padStart(2, '0'),
    ].join('-');

    const windowPrices = [];
    list.forEach(p => {
        const d = String(p.date || '');
        if (d.length >= 10 && d[4] === '-' && d.slice(0, 10) >= cutoffStr) {
            const n = Number(p.unit_price);
            if (Number.isFinite(n) && n > 0) windowPrices.push(n);
        }
    });
    const avgBase = windowPrices.length ? windowPrices : prices;
    const avg30 = avgBase.reduce((a, b) => a + b, 0) / avgBase.length;

    let vsAvgPct = null;
    let isAnomaly = false;
    if (avg30 > 0) {
        vsAvgPct = Math.round(((latest - avg30) / avg30) * 1000) / 10;
        isAnomaly = vsAvgPct > threshold * 100;
    }

    return {
        count: prices.length,
        latest_price: Math.round(latest * 100) / 100,
        avg_30d: Math.round(avg30 * 100) / 100,
        min_price: Math.round(minP * 100) / 100,
        max_price: Math.round(maxP * 100) / 100,
        vs_avg_pct: vsAvgPct,
        is_anomaly: isAnomaly,
        threshold_pct: Math.round(threshold * 1000) / 10,
    };
}

/**
 * 生成纯 SVG 价格曲线（零依赖）。avgLine 为 30 日均价水平线。
 * 返回 SVG 字符串（调用方负责插入 DOM；数值均已 sanitize）。
 */
function buildPriceChartSvg(points, opts) {
    const width = (opts && opts.width) || 640;
    const height = (opts && opts.height) || 200;
    const padL = 48;
    const padR = 16;
    const padT = 16;
    const padB = 32;
    const avgLine = (opts && opts.avgLine != null) ? Number(opts.avgLine) : null;
    const threshold = (opts && opts.threshold != null) ? opts.threshold : PRICE_ANOMALY_THRESHOLD;

    const list = (Array.isArray(points) ? points : [])
        .map(p => ({
            date: String(p.date || ''),
            price: Number(p.unit_price),
        }))
        .filter(p => Number.isFinite(p.price) && p.price > 0);

    if (list.length === 0) return '';

    let minY = Math.min.apply(null, list.map(p => p.price));
    let maxY = Math.max.apply(null, list.map(p => p.price));
    if (avgLine != null && Number.isFinite(avgLine)) {
        minY = Math.min(minY, avgLine);
        maxY = Math.max(maxY, avgLine);
    }
    // WS3：阈值参考线（上界 = 均价 x (1+阈值)），须一并纳入 y 轴范围，否则可能画出画布外
    const thresholdUpper = (avgLine != null && Number.isFinite(avgLine) && avgLine > 0
        && Number.isFinite(threshold) && threshold > 0)
        ? avgLine * (1 + threshold) : null;
    if (thresholdUpper != null) {
        minY = Math.min(minY, thresholdUpper);
        maxY = Math.max(maxY, thresholdUpper);
    }
    if (maxY - minY < 1e-9) {
        minY = Math.max(0, minY * 0.9);
        maxY = maxY * 1.1 || 1;
    }
    const yPad = (maxY - minY) * 0.12;
    minY = Math.max(0, minY - yPad);
    maxY = maxY + yPad;

    const plotW = width - padL - padR;
    const plotH = height - padT - padB;
    const n = list.length;

    function xAt(i) {
        if (n === 1) return padL + plotW / 2;
        return padL + (i / (n - 1)) * plotW;
    }
    function yAt(price) {
        return padT + (1 - (price - minY) / (maxY - minY)) * plotH;
    }

    const coords = list.map((p, i) => ({ x: xAt(i), y: yAt(p.price), ...p }));
    const linePts = coords.map(c => `${c.x.toFixed(1)},${c.y.toFixed(1)}`).join(' ');
    const areaPts = [
        `${coords[0].x.toFixed(1)},${(padT + plotH).toFixed(1)}`,
        linePts,
        `${coords[n - 1].x.toFixed(1)},${(padT + plotH).toFixed(1)}`,
    ].join(' ');

    // Y 轴刻度（3 档）
    const yTicks = [minY, (minY + maxY) / 2, maxY];
    let yGrid = '';
    yTicks.forEach(v => {
        const y = yAt(v);
        yGrid += `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" class="ph-grid"/>`;
        yGrid += `<text x="${padL - 8}" y="${(y + 4).toFixed(1)}" class="ph-axis-label" text-anchor="end">$${v.toFixed(1)}</text>`;
    });

    // X 轴标签：首 / 中 / 末
    const xIdx = n === 1 ? [0] : (n === 2 ? [0, 1] : [0, Math.floor((n - 1) / 2), n - 1]);
    let xLabels = '';
    xIdx.forEach(i => {
        const c = coords[i];
        const label = formatMonthDay(list[i].date);
        xLabels += `<text x="${c.x.toFixed(1)}" y="${(height - 10).toFixed(1)}" class="ph-axis-label" text-anchor="middle">${w2Escape(label)}</text>`;
    });

    let avgSvg = '';
    if (avgLine != null && Number.isFinite(avgLine) && avgLine > 0) {
        const y = yAt(avgLine);
        avgSvg = `
            <line x1="${padL}" y1="${y.toFixed(1)}" x2="${width - padR}" y2="${y.toFixed(1)}" class="ph-avg-line"/>
            <text x="${width - padR}" y="${(y - 6).toFixed(1)}" class="ph-avg-label" text-anchor="end">均 $${avgLine.toFixed(2)}</text>
        `;
    }

    // WS3：阈值参考线（独立样式类 ph-threshold-line，与均价线区分；图例中亦有一项）
    let thresholdSvg = '';
    if (thresholdUpper != null) {
        const ty = yAt(thresholdUpper);
        thresholdSvg = `
            <line x1="${padL}" y1="${ty.toFixed(1)}" x2="${width - padR}" y2="${ty.toFixed(1)}" class="ph-threshold-line"/>
            <text x="${width - padR}" y="${(ty - 6).toFixed(1)}" class="ph-threshold-label" text-anchor="end">阈值 $${thresholdUpper.toFixed(2)}</text>
        `;
    }

    let dots = '';
    coords.forEach((c, i) => {
        let anomaly = false;
        if (i > 0 && list[i - 1].price > 0) {
            const ratio = (c.price - list[i - 1].price) / list[i - 1].price;
            anomaly = ratio > threshold;
        }
        const cls = anomaly ? 'ph-dot-point ph-dot-up' : 'ph-dot-point';
        const title = `${list[i].date}: $${c.price.toFixed(2)}`;
        dots += `<circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="${i === n - 1 ? 5 : 3.5}" class="${cls}"><title>${w2Escape(title)}</title></circle>`;
    });

    return `
<svg class="ph-svg" viewBox="0 0 ${width} ${height}" width="100%" height="${height}" preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="phAreaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#032425" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#032425" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  ${yGrid}
  <polygon points="${areaPts}" fill="url(#phAreaGrad)" class="ph-area"/>
  <polyline points="${linePts}" class="ph-line" fill="none"/>
  ${avgSvg}
  ${thresholdSvg}
  ${dots}
  ${xLabels}
</svg>`.trim();
}

function formatHkd(price) {
    if (price == null || !Number.isFinite(Number(price))) return '—';
    return '$' + Number(price).toFixed(2);
}

// 完整日期规范化：任意来源（YYYY-MM-DD / YYYY-M-D / 含时间 / / 分隔）统一为 YYYY-MM-DD（供 hover 弹出）
function formatDateStandard(dateStr) {
    if (!dateStr) return '—';
    const s = String(dateStr).trim();
    const m = s.match(/(\d{4})[-/](\d{1,2})[-/](\d{1,2})/);
    if (!m) return s;
    return `${m[1]}-${String(m[2]).padStart(2, '0')}-${String(m[3]).padStart(2, '0')}`;
}

// 进价明细日期：默认只显示「月-日」，完整日期通过 hover (title) 弹出
function formatMonthDay(dateStr) {
    if (!dateStr) return '—';
    const s = String(dateStr).trim();
    const m = s.match(/(\d{4})-(\d{2})-(\d{2})/);
    if (!m) return s;
    return `${m[2]}-${m[3]}`;
}

// 全局日期单元格标准：默认短「MM-DD」，hover 弹出完整日期（统一入口，所有日期展示走这里）
function renderDateCell(dateStr) {
    if (dateStr == null || String(dateStr).trim() === '') return '—';
    const full = w2Escape(formatDateStandard(dateStr));
    const short = w2Escape(formatMonthDay(dateStr));
    return `<span class="ph-date-cell" data-full-date="${full}" onmouseenter="showDateTooltip(this)" onmouseleave="hideDateTooltip()">${short}</span>`;
}

// 自定义 tooltip：hover 日期单元格时立即弹出完整日期（渲染到 body，规避表格滚动容器裁剪）
let _dateTooltipEl = null;
function showDateTooltip(cell) {
    const text = cell && cell.dataset ? cell.dataset.fullDate : '';
    if (!text) return;
    if (!_dateTooltipEl) {
        _dateTooltipEl = document.createElement('div');
        _dateTooltipEl.className = 'cell-tooltip';
        _dateTooltipEl.setAttribute('role', 'tooltip');
        document.body.appendChild(_dateTooltipEl);
    }
    _dateTooltipEl.textContent = text;
    _dateTooltipEl.style.display = 'block';
    const rect = cell.getBoundingClientRect();
    const tw = _dateTooltipEl.offsetWidth;
    const th = _dateTooltipEl.offsetHeight;
    let left = rect.left + rect.width / 2 - tw / 2;
    let top = rect.top - th - 6;
    if (top < 8) top = rect.bottom + 6;  // 顶部空间不足则翻到下方
    left = Math.max(8, Math.min(left, window.innerWidth - tw - 8));
    _dateTooltipEl.style.left = left + 'px';
    _dateTooltipEl.style.top = top + 'px';
}
function hideDateTooltip() {
    if (_dateTooltipEl) _dateTooltipEl.style.display = 'none';
}

function formatPctSigned(pct) {
    if (pct == null || !Number.isFinite(Number(pct))) return '—';
    const n = Number(pct);
    const sign = n > 0 ? '+' : '';
    return sign + n.toFixed(1) + '%';
}

function setPriceHistoryViewState(state) {
    // state: 'loading' | 'empty' | 'content'
    const loading = document.getElementById('priceHistoryLoading');
    const empty = document.getElementById('priceHistoryEmpty');
    const content = document.getElementById('priceHistoryContent');
    if (loading) loading.classList.toggle('hide', state !== 'loading');
    if (empty) empty.classList.toggle('hide', state !== 'empty');
    if (content) content.classList.toggle('hide', state !== 'content');
}

function renderPriceHistoryModal(ret) {
    const skuName = ret.sku_name || '商品';
    const skuCode = ret.sku_code || '';
    const baseUnit = ret.base_unit || '';
    const points = Array.isArray(ret.data) ? ret.data : [];
    const summary = ret.summary && typeof ret.summary === 'object'
        ? ret.summary
        : computePriceHistorySummary(points);

    // WS3：阈值与后端**同源** —— summary.threshold_pct（本租户生效值）驱动曲线阈值线、
    // 图例文案与明细行 ph-row-hot 判定；接口未给时回退当前可变阈值。图表与表格不再各算一套。
    const backendThresholdPct = (summary.threshold_pct != null
        && Number.isFinite(Number(summary.threshold_pct)))
        ? Number(summary.threshold_pct) : PRICE_ANOMALY_THRESHOLD * 100;
    const threshold = backendThresholdPct / 100;
    PRICE_ANOMALY_THRESHOLD = threshold;   // 回填全局可变阈值，供其它展示（库存角标 tooltip）同源
    const thresholdLabel = Number.isInteger(backendThresholdPct)
        ? String(backendThresholdPct) : backendThresholdPct.toFixed(1);
    const legendEl = document.getElementById('phLegendThresholdText');
    if (legendEl) legendEl.textContent = `涨幅 >${thresholdLabel}%`;

    const titleEl = document.getElementById('priceHistoryTitle');
    const subEl = document.getElementById('priceHistorySubtitle');
    if (titleEl) titleEl.textContent = `${skuName} · 价格走势`;
    if (subEl) {
        const bits = [];
        if (baseUnit) bits.push(`单位 ${baseUnit}`);
        bits.push('进货单价历史');
        subEl.textContent = bits.join(' · ');
    }

    if (points.length === 0) {
        setPriceHistoryViewState('empty');
        return;
    }
    setPriceHistoryViewState('content');

    // 指标卡
    const metricsEl = document.getElementById('priceHistoryMetrics');
    if (metricsEl) {
        // 近 30 天没有进货时均价没有意义：显示「—」而不是 $0.00，避免看起来像价格是 0
        const noRecent = !(Number(summary.avg_30d) > 0);
        const vs = noRecent ? null : summary.vs_avg_pct;
        const anomaly = !noRecent && !!summary.is_anomaly;
        const vsClass = anomaly ? 'ph-metric-danger' : (vs != null && vs < 0 ? 'ph-metric-ok' : '');
        metricsEl.innerHTML = `
            <div class="ph-metric">
                <div class="ph-metric-label">最新进价</div>
                <div class="ph-metric-value">${w2Escape(formatHkd(summary.latest_price))}</div>
            </div>
            <div class="ph-metric">
                <div class="ph-metric-label">30 日均价</div>
                <div class="ph-metric-value">${noRecent ? '—' : w2Escape(formatHkd(summary.avg_30d))}</div>
                ${noRecent ? '<div class="ph-metric-sub" style="font-size:0.78rem; color:var(--text-muted);">近 30 天无进货</div>' : ''}
            </div>
            <div class="ph-metric ${vsClass}">
                <div class="ph-metric-label">较均价${anomaly ? ' · 异动' : ''}</div>
                <div class="ph-metric-value">${noRecent ? '—' : w2Escape(formatPctSigned(vs))}</div>
            </div>
            <div class="ph-metric">
                <div class="ph-metric-label">区间低 / 高</div>
                <div class="ph-metric-value ph-metric-range">${w2Escape(formatHkd(summary.min_price))} <span class="ph-range-sep">–</span> ${w2Escape(formatHkd(summary.max_price))}</div>
            </div>
        `;
    }

    // 曲线
    const chartEl = document.getElementById('priceHistoryChart');
    if (chartEl) {
        const hasReceiptPoints = points.some(p => p.receipt_id || p.source === '进货单据' || p.source === '收据入库' || p.source === '已审核进货');
        const noticeHtml = !hasReceiptPoints ? `
            <div style="background-color:#eff6ff; border:1px solid #bfdbfe; color:#1e40af; padding:8px 12px; border-radius:6px; font-size:0.85rem; margin-bottom:10px;">
                <strong>提示：</strong>当前为系统初始基准单价，暂无已审核入库的实际进货记录。审批进货单后将自动形成动态价格走势。
            </div>
        ` : '';
        chartEl.innerHTML = noticeHtml + buildPriceChartSvg(points, {
            avgLine: summary.avg_30d,
            threshold: threshold,
        });
    }

    // 明细表（新在上）；先升序算「较上笔」；供应商 + 单据钻取（P0）
    const tbody = document.getElementById('priceHistoryTableBody');
    const countEl = document.getElementById('priceHistoryCount');
    if (countEl) countEl.textContent = `${points.length} 笔`;
    if (tbody) {
        tbody.innerHTML = '';
        const deltaByIdx = {};
        for (let i = 0; i < points.length; i++) {
            const price = Number(points[i].unit_price);
            const prev = i > 0 ? Number(points[i - 1].unit_price) : NaN;
            if (i === 0 || !Number.isFinite(price) || !Number.isFinite(prev) || prev <= 0) {
                deltaByIdx[i] = null;
            } else {
                deltaByIdx[i] = Math.round(((price - prev) / prev) * 1000) / 10;
            }
        }
        for (let revI = 0; revI < points.length; revI++) {
            const origIdx = points.length - 1 - revI;
            const item = points[origIdx];
            const price = Number(item.unit_price);
            const qty = item.qty != null && Number.isFinite(Number(item.qty)) ? Number(item.qty) : null;
            const delta = deltaByIdx[origIdx];
            let deltaCell = '<span class="ph-delta-na">—</span>';
            if (delta != null) {
                const up = delta > 0;
                const big = delta > backendThresholdPct;
                const cls = big ? 'ph-delta-up ph-delta-hot' : (up ? 'ph-delta-up' : (delta < 0 ? 'ph-delta-down' : 'ph-delta-flat'));
                deltaCell = `<span class="${cls}">${w2Escape(formatPctSigned(delta))}</span>`;
            }
            const supName = (item.supplier_name && String(item.supplier_name).trim())
                ? String(item.supplier_name).trim()
                : '—';
            const rid = item.receipt_id != null ? Number(item.receipt_id) : null;
            let receiptCell = '<span class="ph-delta-na">—</span>';
            if (rid && Number.isFinite(rid) && rid > 0) {
                receiptCell = `<button type="button" class="btn btn-secondary ph-receipt-btn" `
                    + `style="padding:2px 8px; font-size:0.75rem;" `
                    + `onclick="openReceiptFromPriceHistory(${rid})">查看</button>`;
            }
            const tr = document.createElement('tr');
            if (delta != null && delta > backendThresholdPct) tr.classList.add('ph-row-hot');
            if (rid && Number.isFinite(rid) && rid > 0) {
                tr.classList.add('ph-row-clickable');
                tr.title = '双击打开原单';
                tr.ondblclick = () => openReceiptFromPriceHistory(rid);
            }
            tr.innerHTML = `
                <td>${renderDateCell(item.date)}</td>
                <td class="ph-supplier-cell" title="${w2Escape(supName)}">${w2Escape(supName)}</td>
                <td class="col-right"><strong>${w2Escape(Number.isFinite(price) ? price.toFixed(2) : '—')}</strong></td>
                <td class="col-right">${qty != null ? w2Escape(String(qty)) : '—'}</td>
                <td><span class="badge badge-secondary">${w2Escape(({receipt: '进货单据', manual: '手工录入', initial: '初始单价', baseline: '初始单价'})[item.source] || item.source || '—')}</span></td>
                <td class="col-right">${deltaCell}</td>
                <td class="col-center">${receiptCell}</td>
            `;
            tbody.appendChild(tr);
        }
    }
}

// -------------------------------------------------------------
// WS4-6：价格走势 Modal 内的「SKU 概览」（数据取自 GET /api/inventory/skus/{id}）
// -------------------------------------------------------------
function renderSkuOverview(data) {
    const el = document.getElementById('priceHistorySkuOverview');
    if (!el) return;
    if (!data) {
        // 取详情失败：仅本块降级为提示，价格曲线照常渲染；不抛出、不用 console.error。
        el.innerHTML = '<div class="ph-overview-title">食材概况</div>'
            + '<div class="ph-overview-fail">详情加载失败</div>';
        return;
    }
    const unit = data.base_unit || '';
    const stock = (data.current_stock != null) ? data.current_stock : '-';
    // 折算 kg：None（计件/体积）显示 -，绝不显示 0。
    const kg = (data.standard_kg != null && Number.isFinite(Number(data.standard_kg)))
        ? (Number(data.standard_kg).toFixed(3) + ' kg') : '-';
    const avgCost = (data.avg_cost != null && Number.isFinite(Number(data.avg_cost)) && Number(data.avg_cost) > 0)
        ? formatHkd(data.avg_cost) : '-';
    const lastPrice = (data.last_unit_price != null && Number.isFinite(Number(data.last_unit_price)) && Number(data.last_unit_price) > 0)
        ? formatHkd(data.last_unit_price) : '-';
    const alertLine = (data.min_stock_alert != null && Number.isFinite(Number(data.min_stock_alert)) && Number(data.min_stock_alert) > 0)
        ? (Number(data.min_stock_alert) + (unit ? ' ' + unit : '')) : '-';
    // WS4-6：SKU 概览补「在库批次」（口径与后端 avg_cost 同源：未关闭且剩余 > 0，FIFO 序）。
    const batches = Array.isArray(data.batches) ? data.batches : [];
    el.innerHTML = `
        <div class="ph-overview-title">食材概况</div>
        <div class="ph-overview-grid">
            <div class="ph-overview-item"><span class="ph-overview-label">当前结存</span><span class="ph-overview-value">${w2Escape(String(stock))}${unit ? ' ' + w2Escape(unit) : ''}</span></div>
            <div class="ph-overview-item"><span class="ph-overview-label">单位</span><span class="ph-overview-value">${w2Escape(unit || '-')}</span></div>
            <div class="ph-overview-item"><span class="ph-overview-label">折算 kg</span><span class="ph-overview-value">${w2Escape(kg)}</span></div>
            <div class="ph-overview-item"><span class="ph-overview-label">加权成本</span><span class="ph-overview-value">${w2Escape(avgCost)}</span></div>
            <div class="ph-overview-item"><span class="ph-overview-label">最近单价</span><span class="ph-overview-value">${w2Escape(lastPrice)}</span></div>
            <div class="ph-overview-item"><span class="ph-overview-label">警戒线</span><span class="ph-overview-value">${w2Escape(alertLine)}</span></div>
        </div>
        <div class="ph-overview-batches-title">在库批次 ${batches.length} 个（入库日期 / 单价 / 剩余）</div>
        ${renderSkuOverviewBatches(batches, unit)}`;
}

// WS4-6：SKU 概览的批次区块。批次可能很多，列表放在固定高度可滚动区，
// 避免把价格走势 Modal 撑爆；无批次时给出明确空态（不留白）。
function renderSkuOverviewBatches(batches, unit) {
    if (!Array.isArray(batches) || batches.length === 0) {
        return '<div class="ph-overview-batches-empty">暂无在库批次</div>';
    }
    const rows = batches.map(b => {
        const date = (b && b.inbound_date) ? String(b.inbound_date) : '-';
        const cost = (b && b.unit_cost != null && Number.isFinite(Number(b.unit_cost)) && Number(b.unit_cost) > 0)
            ? formatHkd(b.unit_cost) : '-';
        const qty = (b && b.remaining_qty != null && Number.isFinite(Number(b.remaining_qty)))
            ? String(b.remaining_qty) : '-';
        const bu = (b && b.unit) ? String(b.unit) : (unit || '');
        return '<li class="ph-overview-batch">'
            + `<span class="ph-overview-batch-date">${w2Escape(date)}</span>`
            + `<span class="ph-overview-batch-cost">${w2Escape(cost)}</span>`
            + `<span class="ph-overview-batch-qty">${w2Escape(qty)} ${w2Escape(bu)}</span>`
            + '</li>';
    }).join('');
    return `<ul class="ph-overview-batches">${rows}</ul>`;
}

function loadSkuOverview(skuId) {
    const el = document.getElementById('priceHistorySkuOverview');
    if (el) el.innerHTML = '<div class="ph-overview-title">食材概况</div><div class="ph-overview-fail">加载中…</div>';
    apiFetch(`/api/inventory/skus/${skuId}`)
        .then(res => res.json().then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'success' || !body.data) {
                renderSkuOverview(null);
                return;
            }
            renderSkuOverview(body.data);
        })
        .catch(err => {
            // 详情失败不得让 Modal 崩掉：价格曲线已独立渲染，这里只降级本块并留痕。
            renderSkuOverview(null);
            console.warn('[price-history-sku-overview] 详情加载失败', err);
        });
}

/** P0：从价格走势钻取原单 —— 关闭走势 Modal，打开归档详情 */
function openReceiptFromPriceHistory(receiptId) {
    const rid = Number(receiptId);
    if (!Number.isFinite(rid) || rid <= 0) {
        showToast('无效的单据 ID', 'error');
        return;
    }
    closeModalById('priceHistoryModal');
    if (typeof loadReceiptDetail === 'function') {
        loadReceiptDetail(rid);
    } else {
        showToast('无法打开单据详情', 'error');
    }
}

function viewPriceHistory(skuId) {
    const id = Number(skuId);
    if (!Number.isFinite(id) || id <= 0) {
        showToast('找不到这个食材，请刷新页面再试。', 'error');
        return;
    }
    const modal = document.getElementById('priceHistoryModal');
    if (!modal) {
        showToast('价格走势组件未就绪', 'error');
        return;
    }
    const titleEl = document.getElementById('priceHistoryTitle');
    if (titleEl) titleEl.textContent = '价格走势';
    const subEl = document.getElementById('priceHistorySubtitle');
    if (subEl) subEl.textContent = '加载中…';
    setPriceHistoryViewState('loading');
    modal.classList.remove('hide');

    apiFetch(`/api/price_history/${id}`)
        .then(res => res.json().then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'success') {
                setPriceHistoryViewState('empty');
                const empty = document.getElementById('priceHistoryEmpty');
                if (empty) empty.textContent = (body && body.msg) || '加载价格走势失败';
                showToast((body && body.msg) || '加载价格走势失败', 'error');
                return;
            }
            renderPriceHistoryModal(body);
        })
        .catch(err => {
            setPriceHistoryViewState('empty');
            const empty = document.getElementById('priceHistoryEmpty');
            if (empty) empty.textContent = '加载价格走势失败';
            console.error('加载价格走势失败', err);
            showToast('加载价格走势失败' + toastFailDetail(err), 'error');
        });

    // WS4-6：并行取一次 SKU 详情填充「SKU 概览」；其失败/异常独立于价格曲线。
    loadSkuOverview(id);
}

// -------------------------------------------------------------
// WS3：owner 门店级价格预警阈值控件（owner-only）
// 读取/保存 GET / PUT /api/inventory/price_threshold（本租户覆盖值）
// -------------------------------------------------------------
function loadPriceThresholdControl() {
    const input = document.getElementById('invPriceThresholdInput');
    if (!input) return;   // 控件不在当前 DOM（非库存页 / 测试沙箱）：零动作
    apiFetch('/api/inventory/price_threshold')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) return;
            const v = Number(body.data.threshold_pct);
            if (Number.isFinite(v)) input.value = String(v);
            const hint = document.getElementById('invPriceThresholdHint');
            if (hint && body.data.global_default_pct != null) {
                hint.textContent = '全局默认 ' + body.data.global_default_pct + '%';
            }
        })
        .catch(e => console.warn('[price-threshold] 读取失败', e));
}

function savePriceThreshold() {
    const input = document.getElementById('invPriceThresholdInput');
    if (!input) return;
    const v = Number(input.value);
    if (!Number.isFinite(v) || !(v > 0) || v > 100) {
        showToast('阈值必须为正数且不超过 100', 'error');
        return;
    }
    apiFetch('/api/inventory/price_threshold', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ threshold_pct: v })
    })
        .then(res => res.json().then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'success') {
                showToast('保存阈值失败：' + ((body && body.msg) || '未知错误'), 'error');
                return;
            }
            showToast('价格预警阈值已保存为 ' + body.data.threshold_pct + '%', 'success');
            loadInventoryData();   // 即时刷新列表，使异动角标按新阈值重算
        })
        .catch(e => {
            console.warn('[price-threshold] 保存失败', e);
            showToast('保存阈值失败（网络错误）', 'error');
        });
}

