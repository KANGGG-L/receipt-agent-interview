// demo/static/js/review_core.js
// 复核核心：单位滑动、供应商 Combobox、SKU 内联指定、Prefill 渲染与动态表格编辑、
// 附加费用/手写注记动态行、结算与付款标记、复核旗标与单据状态表、供应商价格提示、
// 币种、行级反馈飞轮、人工复核保存入库（含整单问题扫描与仅看问题行）。
// F5·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 与 common.js 之后。
// -------------------------------------------------------------
// 5. 单位滑动展示、直接输入、相似匹配与新单位自动保存复用逻辑
// -------------------------------------------------------------
function updateGlobalDatalistUnits() {
    const datalist = document.getElementById('dlUnits');
    if (!datalist) return;
    datalist.innerHTML = '';
    availableUnits.forEach(u => {
        const opt = document.createElement('option');
        opt.value = u;
        datalist.appendChild(opt);
    });
}

function openUnitMenu(inputElem) {
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const wrap = inputElem.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    renderUnitMenuItems(inputElem, menu);
    menu.classList.remove('hide');
}

function renderUnitMenuItems(inputElem, menuElem) {
    const currentVal = (inputElem.value || "").trim().toLowerCase();
    
    const recommendedList = [];
    const regularList = [];

    availableUnits.forEach(u => {
        const uLower = u.toLowerCase();
        const isExactMatch = (uLower === currentVal);
        const isSimilarMatch = currentVal && (uLower.includes(currentVal) || currentVal.includes(uLower));

        const itemData = {
            unit: u,
            isExactMatch,
            isSimilarMatch
        };

        if (isExactMatch || isSimilarMatch) {
            recommendedList.push(itemData);
        } else {
            regularList.push(itemData);
        }
    });

    // 核心规则：将 Highlight 推荐匹配单位强制排列在下拉列表的最最顶端！
    const sortedList = [...recommendedList, ...regularList];

    let html = "";
    sortedList.forEach(item => {
        const u = item.unit;
        let highlightClass = "";
        let badgeHTML = "";
        if (item.isExactMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched">已选择</span>`;
        } else if (item.isSimilarMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched"> 推荐匹配</span>`;
        }

        html += `
            <div class="unit-dropdown-item ${highlightClass}" data-unit="${w2Escape(u)}">
                <span data-i18n-skip>${w2Escape(u)}</span>
                ${badgeHTML}
            </div>
        `;
    });

    menuElem.innerHTML = html;
}

// P0-2：单位下拉改事件委托 + dataset 取值。
// 原 onmousedown="selectUnitItem(this,'${u}')" 把单位值裸拼进行内 JS 字符串，
// 单位含引号即可逃逸注入（单位来自 OCR/用户输入，经 registerNewUnitsFromRows 入库池）。
document.addEventListener('mousedown', (e) => {
    const item = e.target && e.target.closest
        ? e.target.closest('.unit-dropdown-item[data-unit]') : null;
    if (item) selectUnitItem(item);
});

function selectUnitItem(itemElem) {
    const unitValue = itemElem.dataset.unit || '';
    const wrap = itemElem.closest('.unit-combobox-wrap');
    if (wrap) {
        const input = wrap.querySelector('.inp-unit');
        if (input) {
            input.value = unitValue;
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
}

function closeUnitMenuDelay(inputElem) {
    setTimeout(() => {
        const wrap = inputElem.closest('.unit-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}

function registerNewUnitsFromRows(rowElements) {
    rowElements.forEach(tr => {
        const unitInput = tr.querySelector('.inp-unit');
        if (unitInput && unitInput.value) {
            const uVal = unitInput.value.trim();
            if (uVal && !availableUnits.includes(uVal)) {
                availableUnits.push(uVal);
            }
        }
    });
    updateGlobalDatalistUnits();
}

// -------------------------------------------------------------
// 5.5 供应商 Combobox 下拉框逻辑 (Highlight 推荐项目强行置顶)
// -------------------------------------------------------------
let availableSuppliers = [];

let isSelectingSupplier = false;

function openSupplierMenu(inputElem) {
    if (isSelectingSupplier) return;
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const wrap = inputElem.closest('.unit-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    renderSupplierMenuItems(inputElem, menu);
    menu.classList.remove('hide');
}

function closeSupplierMenuDelay(inputElem) {
    setTimeout(() => {
        if (isSelectingSupplier) return;
        const wrap = inputElem.closest('.unit-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}

function selectSupplierItem(itemElem) {
    isSelectingSupplier = true;
    const supplierName = itemElem.getAttribute('data-supplier-name') || '';
    const wrap = itemElem.closest('.unit-combobox-wrap');
    if (wrap) {
        const input = wrap.querySelector('input');
        if (input) {
            input.value = supplierName;
        }
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) {
            menu.classList.add('hide');
        }
    }
    // 下拉选中是「程序化赋值」，不会触发 input 的 change 事件 → 显式重取提示
    // （P2-T4：供应商变了，历史价格区间的口径也变了）。
    refreshAllVendorPriceHints();
    if (typeof applySupplierSettlementPref === 'function') applySupplierSettlementPref();
    setTimeout(() => {
        isSelectingSupplier = false;
    }, 300);
}

function renderSupplierMenuItems(inputElem, menuElem) {
    if (!availableSuppliers || availableSuppliers.length === 0) {
        apiFetch('/api/suppliers')
        .then(res => res.json())
        .then(ret => {
            if (ret.status === 'success' && ret.data) {
                availableSuppliers = ret.data;
                renderSupplierMenuItems(inputElem, menuElem);
            }
        })
        .catch(err => console.error(err));
    }

    const rawVal = (inputElem.value || "").trim();
    const currentVal = rawVal.toLowerCase();
    
    const recommendedList = [];
    const regularList = [];

    availableSuppliers.forEach(sup => {
        // U-06：软停用（active=0）的供应商不进联想下拉
        if (sup.active === 0 || sup.active === '0' || sup.active === false) return;
        const supNameLower = (sup.name || "").toLowerCase();
        const supCodeLower = (sup.supplier_code || "").toLowerCase();

        const isExactMatch = currentVal && (supNameLower === currentVal || supCodeLower === currentVal);
        const isSimilarMatch = currentVal && (
            supNameLower.includes(currentVal) || 
            currentVal.includes(supNameLower) ||
            supCodeLower.includes(currentVal)
        );

        const itemData = {
            ...sup,
            isExactMatch,
            isSimilarMatch
        };

        if (isExactMatch || isSimilarMatch) {
            recommendedList.push(itemData);
        } else {
            regularList.push(itemData);
        }
    });

    const sortedList = [...recommendedList, ...regularList];

    let html = "";

    // 若用户输入/预填了系统库以外的新供应商，在顶部展示一键使用新名称选项
    if (rawVal && recommendedList.length === 0) {
        // P0-1：属性与文本插值统一过 w2Escape（原仅替换双引号，文本位裸拼可注入）
        const safeRaw = w2Escape(rawVal);
        html += `
            <div class="unit-dropdown-item highlight" data-supplier-name="${safeRaw}" onmousedown="selectSupplierItem(this)">
                <div>
                    <span>使用新供应商: <strong data-i18n-skip>"${safeRaw}"</strong></span>
                </div>
                <span class="badge-matched" style="color:#38bdf8; border-color:rgba(56,189,248,0.4);"> 确认使用新供应商</span>
            </div>
        `;
    }

    sortedList.forEach(sup => {
        let highlightClass = "";
        let badgeHTML = "";
        if (sup.isExactMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched">已选择</span>`;
        } else if (sup.isSimilarMatch) {
            highlightClass = "highlight";
            badgeHTML = `<span class="badge-matched"> 推荐匹配</span>`;
        }

        // P0-1：供应商名/编号均来自后端（可被 OCR/注册输入污染），全插值点过 w2Escape
        const codeSpan = sup.supplier_code ? `<span style="font-size:0.75rem; color:#94a3b8; font-family:monospace; margin-left:6px;">[${w2Escape(sup.supplier_code)}]</span>` : '';
        const safeName = w2Escape(sup.name || '');

        html += `
            <div class="unit-dropdown-item ${highlightClass}" data-supplier-name="${safeName}" onmousedown="selectSupplierItem(this)">
                <div>
                    <span data-i18n-skip>${safeName}</span>
                    ${codeSpan}
                </div>
                ${badgeHTML}
            </div>
        `;
    });

    if (sortedList.length === 0 && !rawVal) {
        html = `<div style="padding:10px; color:var(--text-muted); font-size:0.85rem; text-align:center;">暂无供应商</div>`;
    }

    menuElem.innerHTML = html;
}

// 智能剥离品名中混写的数量与单位（如 "菜心 20斤" -> {name: "菜心", qty: 20, unit: "斤"}）
function smartSplitItemName(rawText) {
    if (!rawText || typeof rawText !== 'string') return null;
    const text = rawText.trim();
    if (!text) return null;

    // 模式 1：乘法复合包装（如 "大豆油 5L*2樽" / "可乐 330ml*6罐" / "鲜鸡蛋 30只*3盘"）
    // 对齐后端 smart_splitter v1_2_0 MULTI_PACK_PATTERN（支持 5L*2樽回归）
    const multMatch = text.match(/^(.*?)\s*(\d+(?:\.\d+)?)\s*(ml|mL|L|l|g|G|kg|KG|斤|两|磅|lbs|oz|豪升|升|克|千克|只|粒|头|片|包|袋|瓶|听)?\s*[*xX×]\s*(\d+(?:\.\d+)?)\s*([斤公斤磅箱包罐樽扎打板只盘袋条桶瓶盒支听件]|[一-龥a-zA-Z]+)?$/i);
    if (multMatch) {
        const baseName = multMatch[1].trim();
        const specNum = multMatch[2];
        const specUnit = multMatch[3] || '';
        const count = parseFloat(multMatch[4]) || 1;
        const mainUnit = multMatch[5] || specUnit || '箱';
        const cleanName = baseName + (specNum ? ` ${specNum}${specUnit}` : '');
        return {
            cleanName: cleanName.trim(),
            quantity: count,
            unit: mainUnit
        };
    }

    // 模式 2：末尾带数量与常见单位（如 "本地菜心 20斤" / "鲜牛肉 2.5kg" / "干贝 8两"）
    const tailMatch = text.match(/^(.*?)\s+(\d+(?:\.\d+)?)\s*(司马斤|公斤|斤|两|磅|kg|g|L|ml|mL|升|箱|包|件|罐|隻|只|瓶|盒|桶|扎|把|袋|条|个)\s*$/i);
    if (tailMatch && tailMatch[1].trim()) {
        return {
            cleanName: tailMatch[1].trim(),
            quantity: parseFloat(tailMatch[2]) || 1,
            unit: tailMatch[3]
        };
    }

    return null;
}

// 明细行内联快捷新建 SKU
let activeInlineSkuInput = null;

// -------------------------------------------------------------
// 5.6 明细行 SKU 内联指定（D19：复核当场指定 SKU，保存后自动学别称）
// 候选来源优先级：① 该行 OCR 结果 fuzzy_candidates/entity_candidates（仅精确未命中非空）；
//               ② 输入品名/SKU 后 GET /api/inventory?q= 模糊搜索；
//               ③ 恒备「不关联（保持未关联）」。
// 安全纪律：候选文本来自 OCR/DB，一律 w2Escape；交互事件委托 + data-* 传值。
// -------------------------------------------------------------
let isSelectingSku = false;
let itemNameDebounceTimer = null;

function computeSkuSimilarity(query, skuName) {
    if (!query || !skuName) return 0;
    const q = query.trim().toLowerCase();
    const name = skuName.trim().toLowerCase();
    if (!q || !name) return 0;

    // 1. 完全一致
    if (name === q) return 1000;

    // 2. 前缀匹配（如 q="菜", name="菜心"）-> 优先级极高，短名称更精准
    if (name.startsWith(q)) {
        return 800 - name.length;
    }

    // 3. 包含匹配（如 q="菜", name="生菜"、"甜菜心"）
    if (name.includes(q)) {
        return 600 - name.length;
    }

    // 4. 反向包含（如 q="新鲜菜心", name="菜心"）
    if (q.includes(name)) {
        return 500 + name.length;
    }

    // 5. 字符重叠匹配
    let matchChars = 0;
    for (const ch of q) {
        if (name.includes(ch)) matchChars++;
    }
    if (matchChars > 0) {
        return (matchChars / Math.max(q.length, name.length)) * 100;
    }

    return 0;
}

function openSkuMenuForNameInput(inputElem) {
    if (isSelectingSku) return;
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    const tr = inputElem.closest('tr');
    if (!tr) return;
    const wrap = tr.querySelector('.sku-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    const inpSku = wrap.querySelector('.inp-sku') || inputElem;
    const val = (inputElem.value || '').trim();
    inpSku.__skuSearchQuery = val;

    if (!inpSku.__skuCandidates || inpSku.__skuCandidates.length === 0) {
        apiFetch('/api/inventory?q=' + (val ? encodeURIComponent(val) : ''))
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            inpSku.__skuCandidates = (ret.data || []).map(sku => ({
                sku_id: sku.id,
                sku_name: sku.name,
                sku_code: sku.sku_code,
                score: null,
            }));
            renderSkuDropdownHtml(inpSku, menu, inpSku.__skuCandidates);
        })
        .catch(e => console.error(e));
    }

    renderSkuDropdownHtml(inpSku, menu, inpSku.__skuCandidates || []);
    menu.classList.remove('hide');
}
window.openSkuMenuForNameInput = openSkuMenuForNameInput;

function closeSkuMenuDelay(inputElem) {
    setTimeout(() => {
        if (isSelectingSku) return;
        const tr = inputElem.closest('tr');
        if (!tr) return;
        const wrap = tr.querySelector('.sku-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.unit-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 250);
}
window.closeSkuMenuDelay = closeSkuMenuDelay;

function onItemNameInput(inputElem) {
    const tr = inputElem.closest('tr');
    if (!tr) return;
    tr.dataset.manual = '1';
    const val = (inputElem.value || '').trim();
    const wrap = tr.querySelector('.sku-combobox-wrap');
    if (!wrap) return;
    const skuInput = wrap.querySelector('.inp-sku');
    const idInput = wrap.querySelector('.inp-sku-id');
    const menu = wrap.querySelector('.unit-dropdown-menu');

    if (skuInput) {
        skuInput.__skuSearchQuery = val;
        skuInput.value = val;
    }

    if (!val) {
        if (idInput) idInput.value = '';
    }

    if (menu) {
        menu.classList.remove('hide');
        if (skuInput) renderSkuMenuItems(skuInput, menu);
    }

    if (itemNameDebounceTimer) clearTimeout(itemNameDebounceTimer);
    itemNameDebounceTimer = setTimeout(() => {
        apiFetch('/api/inventory?q=' + (val ? encodeURIComponent(val) : ''))
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const searched = (ret.data || []).map(sku => ({
                sku_id: sku.id,
                sku_name: sku.name,
                sku_code: sku.sku_code,
                score: null,
            }));
            if (skuInput) {
                skuInput.__skuCandidates = searched;
                if (menu && !menu.classList.contains('hide')) {
                    renderSkuDropdownScrollOnly(skuInput, menu, skuInput.__skuCandidates);
                }
            }
            if (val) {
                const exact = searched.find(s => s.sku_name === val);
                if (exact) {
                    if (idInput) idInput.value = exact.sku_id;
                    if (skuInput) skuInput.value = exact.sku_name;
                }
            }
        })
        .catch(err => console.error('SKU 联想失败:', err));
    }, 150);
}
window.onItemNameInput = onItemNameInput;

function mergeSkuCandidates(primary, secondary) {
    const seen = {};
    const out = [];
    [].concat(primary || [], secondary || []).forEach(c => {
        if (!c || c.sku_id == null) return;
        const key = String(c.sku_id);
        if (seen[key]) return;
        seen[key] = true;
        out.push(c);
    });
    return out;
}

function toggleSkuMenu(pillElem) {
    const wrap = pillElem.closest('.sku-combobox-wrap') || pillElem.closest('tr');
    if (!wrap) return;
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;
    const isHidden = menu.classList.contains('hide');
    document.querySelectorAll('.unit-dropdown-menu').forEach(m => m.classList.add('hide'));
    if (isHidden) {
        const inpSku = wrap.querySelector('.inp-sku') || pillElem;
        renderSkuMenuItems(inpSku, menu);
        menu.classList.remove('hide');
    }
}
window.toggleSkuMenu = toggleSkuMenu;

let skuSearchDebounceTimer = null;

function onSkuSearchInput(searchInput) {
    const wrap = searchInput.closest('.sku-combobox-wrap');
    if (!wrap) return;
    const inpSku = wrap.querySelector('.inp-sku');
    const val = searchInput.value;
    if (inpSku) {
        inpSku.__skuSearchQuery = val;
    }
    const menu = wrap.querySelector('.unit-dropdown-menu');
    if (!menu) return;

    if (skuSearchDebounceTimer) clearTimeout(skuSearchDebounceTimer);
    skuSearchDebounceTimer = setTimeout(() => {
        if (inpSku) renderSkuMenuItems(inpSku, menu);
    }, 100);
}
window.onSkuSearchInput = onSkuSearchInput;

function onSkuSearchKeydown(event, searchInput) {
    if (event.key === 'Enter') {
        event.preventDefault();
        const wrap = searchInput.closest('.sku-combobox-wrap');
        if (!wrap) return;
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (!menu) return;
        const firstCandidate = menu.querySelector('.unit-dropdown-item[data-sku-id]:not([data-sku-id=""])');
        if (firstCandidate) {
            selectSkuItem(firstCandidate);
        } else {
            const quickAdd = menu.querySelector('.sku-quick-add-item');
            if (quickAdd) quickCreateAndBindSku(quickAdd);
        }
    } else if (event.key === 'Escape') {
        const menu = searchInput.closest('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
}
window.onSkuSearchKeydown = onSkuSearchKeydown;

function selectSkuItem(itemElem) {
    isSelectingSku = true;
    const skuId = String(itemElem.getAttribute('data-sku-id') || '').trim();
    const skuName = itemElem.getAttribute('data-sku-name') || '';
    const wrap = itemElem.closest('.sku-combobox-wrap') || itemElem.closest('tr');
    if (wrap) {
        const nameInput = wrap.querySelector('.inp-name');
        const skuInput = wrap.querySelector('.inp-sku');
        const idInput = wrap.querySelector('.inp-sku-id');
        if (skuName && nameInput) {
            nameInput.value = skuName;
        }
        if (skuInput) skuInput.value = skuId ? skuName : '';
        if (idInput) idInput.value = skuId;
        const menu = wrap.querySelector('.unit-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
    // SKU 是「供应商×SKU 历史价格区间」的另一维 → 换绑后重取该行提示（P2-T4）。
    refreshVendorPriceHint(itemElem.closest('tr'));
    track('field_edited', currentReceiptId,
          { field_name: 'sku_binding', sku_id: skuId, sku_name: skuName });
    setTimeout(() => { isSelectingSku = false; }, 200);
}

// 1-Click 极速一键建档绑定（无需打开繁琐 Modal）
function quickCreateAndBindSku(itemElem) {
    isSelectingSku = true;
    const rawName = itemElem.getAttribute('data-prefill-name') || '';
    const cleanName = rawName.trim();
    if (!cleanName) return;
    const wrap = itemElem.closest('.sku-combobox-wrap') || itemElem.closest('tr');
    const tr = itemElem.closest('tr');
    const rowUnit = tr ? (tr.querySelector('.inp-unit')?.value || '斤') : '斤';

    apiFetch('/api/inventory/skus', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
            name: cleanName,
            category: '食材',
            base_unit: rowUnit,
            min_stock_alert: 5.0
        })
    })
    .then(res => res.json())
    .then(ret => {
        const targetId = ret.id || ret.existing_id;
        if (ret.status === 'success' || targetId) {
            if (wrap) {
                const nameInput = wrap.querySelector('.inp-name');
                const skuInput = wrap.querySelector('.inp-sku');
                const idInput = wrap.querySelector('.inp-sku-id');
                if (nameInput) nameInput.value = cleanName;
                if (skuInput) skuInput.value = cleanName;
                if (idInput) idInput.value = targetId || '';
                const menu = wrap.querySelector('.unit-dropdown-menu');
                if (menu) menu.classList.add('hide');
            }
            showToast(`已一键建档并关联「${cleanName}」`, 'success');
            if (typeof loadInventoryData === 'function') loadInventoryData();
        } else {
            showToast('建档提示: ' + (ret.message || '请重试'), 'warning');
        }
    })
    .catch(err => {
        console.error('一键建档失败:', err);
        showToast('建档网络异常', 'error');
    })
    .finally(() => {
        setTimeout(() => { isSelectingSku = false; }, 200);
    });
}
window.quickCreateAndBindSku = quickCreateAndBindSku;

function buildSkuDropdownItemsHtml(inputElem, candidates) {
    const query = (inputElem.__skuSearchQuery !== undefined ? inputElem.__skuSearchQuery : (inputElem.value || '')).trim();
    let rows = (candidates && candidates.length > 0) ? [...candidates] : [];

    if (rows.length === 0 && typeof inventorySkusMap !== 'undefined' && inventorySkusMap.size > 0) {
        rows = Array.from(inventorySkusMap.values()).map(s => ({
            sku_id: s.id,
            sku_name: s.name,
            sku_code: s.sku_code,
            score: null
        }));
    }

    const tr = inputElem.closest('tr');
    const rowName = query || (tr ? (tr.querySelector('.inp-name')?.value || '') : '');
    const cleanRowName = rowName ? rowName.trim() : '';

    // 按照相似率从高往低排列
    if (cleanRowName && rows.length > 0) {
        rows.sort((a, b) => {
            const scoreA = computeSkuSimilarity(cleanRowName, a.sku_name);
            const scoreB = computeSkuSimilarity(cleanRowName, b.sku_name);
            return scoreB - scoreA;
        });
    }

    let itemsHtml = '';
    if (cleanRowName) {
        const safeName = w2Escape(cleanRowName);
        itemsHtml += `
            <div class="unit-dropdown-item sku-quick-add-item" data-prefill-name="${safeName}" onmousedown="quickCreateAndBindSku(this)">
                <div><span style="font-weight:600; color:var(--primary);">＋ 一键为「${safeName}」建档入库</span></div>
                <span class="badge-matched" style="background:var(--primary); color:#fff; font-size:0.72rem; padding:2px 8px; border-radius:4px;">1秒建档</span>
            </div>
        `;
    }

    if (rows.length > 0) {
        rows.forEach(c => {
            const codeSpan = c.sku_code
                ? `<span style="font-size:0.75rem; color:#94a3b8; font-family:monospace; margin-left:6px;">[${w2Escape(c.sku_code)}]</span>`
                : '';
            itemsHtml += `
                <div class="unit-dropdown-item" data-sku-id="${w2Escape(c.sku_id)}" data-sku-name="${w2Escape(c.sku_name || '')}" onmousedown="selectSkuItem(this)">
                    <div>
                        <span style="font-weight:500;" data-i18n-skip>${w2Escape(c.sku_name || '')}</span>
                        ${codeSpan}
                    </div>
                </div>
            `;
        });
    }

    itemsHtml += `
        <div class="unit-dropdown-item" data-sku-id="" data-sku-name="" onmousedown="selectSkuItem(this)" style="border-top:1px solid #f1f5f9; color:var(--text-muted);">
            <div><span>设为未关联 (临时消费单)</span></div>
        </div>
    `;
    return itemsHtml;
}

function renderSkuDropdownScrollOnly(inputElem, menuElem, candidates) {
    const scrollElem = menuElem.querySelector('.sku-dropdown-scroll') || menuElem;
    if (scrollElem) {
        scrollElem.innerHTML = buildSkuDropdownItemsHtml(inputElem, candidates);
    }
}

function renderSkuMenuItems(inputElem, menuElem) {
    const query = (inputElem.__skuSearchQuery !== undefined ? inputElem.__skuSearchQuery : (inputElem.value || '')).trim();

    if (query !== inputElem.__skuLastFetchedQuery) {
        inputElem.__skuLastFetchedQuery = query;
        apiFetch('/api/inventory?q=' + (query ? encodeURIComponent(query) : ''))
        .then(res => res.json())
        .then(ret => {
            if (ret.status !== 'success') return;
            const searched = (ret.data || []).map(sku => ({
                sku_id: sku.id,
                sku_name: sku.name,
                sku_code: sku.sku_code,
                score: null,
            }));
            inputElem.__skuCandidates = searched;
            if (!menuElem.classList.contains('hide')) {
                renderSkuDropdownScrollOnly(inputElem, menuElem, inputElem.__skuCandidates);
            }
        })
        .catch(err => console.error('SKU 候选搜索失败:', err));
    }

    renderSkuDropdownHtml(inputElem, menuElem, inputElem.__skuCandidates || []);
}

function renderSkuDropdownHtml(inputElem, menuElem, candidates) {
    const itemsHtml = buildSkuDropdownItemsHtml(inputElem, candidates);
    let html = `
        <div class="sku-dropdown-scroll" style="max-height:220px; overflow-y:auto;">
            ${itemsHtml}
        </div>
    `;
    menuElem.innerHTML = html;
}

// -------------------------------------------------------------
// 6. 右侧 Prefill 渲染与动态表格编辑
// -------------------------------------------------------------
function renderEditForm(data) {
    if (!data) data = {};
    if (typeof resetTotalOverride === 'function') resetTotalOverride();
    if (typeof syncReviewSourceBadge === 'function') syncReviewSourceBadge(data);
    if (typeof syncFlaggedLock === 'function') syncFlaggedLock(data);
    syncFeedbackVisibility(data);
    syncRecoveryVisibility(data);
    document.getElementById('inpSupplier').value = data.supplier_name || '';
    // WS2：供应商变化 → 整表重取「供应商历史报价」提示（单据级维度）。
    const _hintSupplierInput = document.getElementById('inpSupplier');
    if (_hintSupplierInput && !_hintSupplierInput.__vendorHintBound) {
        _hintSupplierInput.__vendorHintBound = true;
        _hintSupplierInput.addEventListener('change', () => {
            refreshAllVendorPriceHints();
            if (typeof applySupplierSettlementPref === 'function') applySupplierSettlementPref();
        });
    }
    document.getElementById('inpDate').value = data.date || '';
    document.getElementById('inpSheet').value = data.sheet_name || (data.date ? data.date.slice(0, 7) : '');
    const rawTotal = (data.total_amount != null) ? data.total_amount : ((data.total != null) ? data.total : 0);
    document.getElementById('inpTotal').value = Number(rawTotal).toFixed(2);

    // 单据形态与币种
    const docFormElem = document.getElementById('inpDocForm');
    if (docFormElem) {
        docFormElem.value = data.doc_form || 'printed_delivery_note';
    }
    const curElem = document.getElementById('inpCurrency');
    if (curElem) {
        curElem.value = data.currency || 'HKD';
    }

    // 附加费用：动态行模式——非零契约字段渲染为费用行，全零/缺省显示占位提示
    renderFeeRowsFromData(data);
    updateFeesSummaryBadge();

    // Gap 6：手写注记（拒收/短装/调整）——动态行模式：数组每项一行，
    // 有注记自动展开抽屉，无注记折叠；徽章「N 条注记」联动
    renderNoteRowsFromData(data);

    // M3/D4: 从 prefill 数据初始化结算方式与付款标记
    applySettlementToForm('inp', data);

    // G3/U2：付款证据内存变量更新（用于后端提交透传，DOM 展示已移除）
    const rawEvidence = String(data.payment_evidence || '').trim();
    const origMark = String(data.payment_mark || '').trim();
    const autoEvidence = rawEvidence
        || ((origMark && origMark !== '已付款' && origMark !== '未付款') ? origMark : '');
    setAutoPaymentEvidence(autoEvidence);

    // Wave 2（D44）：单据级部门下拉
    populateDeptSelect(document.getElementById('inpDepartmentId'), data.department_id);

    // 重置字段告警高亮
    ['inpSupplier', 'inpDate', 'inpTotal'].forEach(id => {
        const el = document.getElementById(id);
        if (el) {
            el.style.borderColor = '';
            el.style.backgroundColor = '';
        }
    });

    const alertBanner = document.getElementById('alertBanner');
    // 整单级提示条：判据走 receiptReviewReasons（手写单 / 门禁未通过），
    // 与行级角标严格分层——此处只提示「整单需要人工复核」，不给任何行刷标记。
    const reviewReasons = receiptReviewReasons(data);
    if (reviewReasons.length > 0) {
        alertBanner.innerText = '请重点核对：' + reviewReasons.join('；')
            + (isManualReceiptData(data) ? '。' : '。已自动填入读到的内容，请对照左侧原图核对或补录。');
        alertBanner.classList.remove('hide');
    } else {
        alertBanner.classList.add('hide');
    }

    // 门禁/花码整单警告：保留原有 toast 提示与字段级高亮（仅在有 math_warnings 时触发，
    // 手写单本身不弹 toast，避免每次打开都打扰店员）。
    if (data.math_warnings && data.math_warnings.length > 0) {
        const _gateTexts = (Array.isArray(data.gate_issues) && data.gate_issues.length && typeof TERMS !== 'undefined')
            ? TERMS.formatGateIssues(data.gate_issues) : data.math_warnings.map(humanizeGateMsg);
        const warnText = _gateTexts.join("；");
        showToast((isManualReceiptData(data) ? '核对提示：' : '核对提示：系统读完了这张单，但发现：') + warnText + '，请核对', 'warning', 7000);

        // 针对具体问题精确定位高亮
        const warnStr = data.math_warnings.join(' ');
        if (/明细|商品|项目/.test(warnStr)) {
            // 明细问题高亮表格
        }
        if (/总额|算术|金额|合计/.test(warnStr)) {
            const el = document.getElementById('inpTotal');
            if (el) {
                el.style.borderColor = 'var(--warning)';
                el.style.backgroundColor = 'rgba(217, 119, 6, 0.08)';
            }
        }
        if (/供应商/.test(warnStr)) {
            const el = document.getElementById('inpSupplier');
            if (el) {
                el.style.borderColor = 'var(--warning)';
                el.style.backgroundColor = 'rgba(217, 119, 6, 0.08)';
            }
        }
        if (/日期/.test(warnStr)) {
            const el = document.getElementById('inpDate');
            if (el) {
                el.style.borderColor = 'var(--warning)';
                el.style.backgroundColor = 'rgba(217, 119, 6, 0.08)';
            }
        }
    }

    const tbody = document.getElementById('itemTableBody');
    tbody.innerHTML = '';

    const items = (data.items || []).filter(item =>
        String((item && (item.raw_name || item.name)) || '').trim() !== '');
    if (items.length === 0) {
        tbody.innerHTML = '<tr class="warning-row" style="background: rgba(217, 119, 6, 0.06);"><td colspan="7" style="text-align: center; color: var(--warning); padding: 14px; font-size: 0.9rem;">未检出单据明细项，请对照左侧原图点击「+ 添加一行」补录商品</td></tr>';
    } else {
        items.forEach((item) => {
            appendTableRow(item);
        });
    }

    // P2-T4：供应商是「供应商×SKU 历史价格区间」的维度之一 → 供应商改了，整表提示重取，
    // 否则提示会停留在上一个供应商的结果上（比没有提示更糟）。
    const hintSupplierInput = document.getElementById('inpSupplier');
    if (hintSupplierInput) hintSupplierInput.onchange = refreshAllVendorPriceHints;

    showQualityWarnings(data.quality_warnings);
    // P2-1：图内隐藏文字命中时显示人话横幅（未命中不显示）
    showImageGuardNotice(data.image_guard);
    renderCurrencySymbol();
    // AC-05-01: 自动诊断问题并刷新问题诊断摘要栏
    refreshReceiptIssueSummary(data);

    // 绑定字段变化事件，实现实时动态重算与计数递减（AC-05-03: 3->2->1->0）
    ['inpSupplier', 'inpDate', 'inpTotal'].forEach(id => {
        const inp = document.getElementById(id);
        if (inp && !inp.__issueListenerBound) {
            inp.__issueListenerBound = true;
            inp.addEventListener('input', () => {
                refreshReceiptIssueSummary();
                if (typeof markReviewDirty === 'function') markReviewDirty();
            });
        }
    });

    // 记录复核表单初始快照并重置脏状态 (QA-P0-01)
    try {
        if (typeof collectReviewFormData === 'function') {
            _reviewFormOriginalSnapshot = JSON.stringify(collectReviewFormData());
            _reviewIsDirty = false;
        }
    } catch (e) {}

    const reviewFormContainer = document.getElementById('editCard') || document.getElementById('itemTableBody');
    if (reviewFormContainer && !reviewFormContainer.__reviewDirtyBound) {
        reviewFormContainer.__reviewDirtyBound = true;
        reviewFormContainer.addEventListener('input', () => {
            if (typeof markReviewDirty === 'function') markReviewDirty();
        });
        reviewFormContainer.addEventListener('change', () => {
            if (typeof markReviewDirty === 'function') markReviewDirty();
        });
    }
}
window.renderEditForm = renderEditForm;

function toggleFeesDrawer() {
    const drawer = document.getElementById('feesDrawerContent');
    const icon = document.getElementById('feesToggleIcon');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hide');
    if (isHidden) {
        drawer.classList.remove('hide');
        if (icon) icon.textContent = '▲';
    } else {
        drawer.classList.add('hide');
        if (icon) icon.textContent = '▾';
    }
}
window.toggleFeesDrawer = toggleFeesDrawer;

// Gap 6：手写注记抽屉展开/收起（与附加费用抽屉同交互）
function toggleNotesDrawer() {
    const drawer = document.getElementById('notesDrawerContent');
    const icon = document.getElementById('notesToggleIcon');
    if (!drawer) return;
    const isHidden = drawer.classList.contains('hide');
    if (isHidden) {
        drawer.classList.remove('hide');
        if (icon) icon.textContent = '▲';
    } else {
        drawer.classList.add('hide');
        if (icon) icon.textContent = '▾';
    }
}
window.toggleNotesDrawer = toggleNotesDrawer;

// -------------------------------------------------------------
// 附加费用动态行（用户实测反馈：固定六宫格观感差，改为像明细一样按需添加）
// 契约字段不变：discount_amount/delivery_fee/service_fee/tax_amount/
// deposit_amount/rounding_adjustment——仅换视图，落库链路零改动。
// -------------------------------------------------------------
const FEE_TYPES = [
    { key: 'discount_amount',     sign: '-', label: '整单折扣/折让 (-)' },
    { key: 'delivery_fee',        sign: '+', label: '送货运费 (+)' },
    { key: 'service_fee',         sign: '+', label: '服务费 (加一) (+)' },
    { key: 'tax_amount',          sign: '+', label: '税额/VAT (+)' },
    { key: 'deposit_amount',      sign: '+', label: '胶筐押金 (+)' },
    { key: 'rounding_adjustment', sign: '-', label: '尾数抹零 (-)' }
];

function feeTypeMeta(key) {
    for (let i = 0; i < FEE_TYPES.length; i++) {
        if (FEE_TYPES[i].key === key) return FEE_TYPES[i];
    }
    return null;
}

// 单据数据 → 费用行：仅非零字段成行；类型顺序与 FEE_TYPES 一致
function renderFeeRowsFromData(data) {
    const container = document.getElementById('feesRowsContainer');
    if (!container) return;
    container.innerHTML = '';
    const d = data || {};
    FEE_TYPES.forEach(t => {
        const v = d[t.key];
        if (v != null && Number(v) !== 0 && !isNaN(Number(v))) {
            appendFeeRow(t.key, Number(v));
        }
    });
    updateFeesEmptyState();
    // 有费用时自动展开抽屉（与手写注记抽屉同策略），无费用保持折叠零负担
    const drawer = document.getElementById('feesDrawerContent');
    const icon = document.getElementById('feesToggleIcon');
    if (drawer) {
        if (container.children.length > 0) {
            drawer.classList.remove('hide');
            if (icon) icon.textContent = '▲';
        } else {
            drawer.classList.add('hide');
            if (icon) icon.textContent = '▾';
        }
    }
}

// 追加一行费用 = [原因下拉] + [自定义原因输入（仅自定义时可见）] + [符号提示 + 金额输入] + [删除按钮]
function appendFeeRow(feeKey, amountValue) {
    const container = document.getElementById('feesRowsContainer');
    if (!container) return null;

    const isCustom = feeKey === 'custom' || (feeKey && !FEE_TYPES.find(t => t.key === feeKey));
    const effectiveKey = isCustom ? 'custom' : (feeKey || '');
    const customLabel = isCustom ? (feeKey !== 'custom' ? feeKey : '') : '';

    const row = document.createElement('div');
    row.className = 'fee-row';
    row.dataset.feeKey = effectiveKey;

    const sel = document.createElement('select');
    sel.className = 'fee-type-select form-control form-control-sm';
    sel.setAttribute('aria-label', '费用原因');
    const blankOpt = document.createElement('option');
    blankOpt.value = '';
    blankOpt.textContent = '请选择费用原因';
    sel.appendChild(blankOpt);
    FEE_TYPES.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.key;
        opt.textContent = t.label;
        sel.appendChild(opt);
    });
    // 自定义选项
    const customOpt = document.createElement('option');
    customOpt.value = 'custom';
    customOpt.textContent = '自定义 (+/-)';
    sel.appendChild(customOpt);
    sel.value = effectiveKey;
    sel.addEventListener('change', function () { onFeeTypeChange(sel, row); });

    // 自定义原因文本输入框（仅 custom 时可见）
    const customInput = document.createElement('input');
    customInput.type = 'text';
    customInput.className = 'fee-custom-label form-control form-control-sm';
    customInput.placeholder = '请输入费用原因';
    customInput.maxLength = 40;
    customInput.style.display = effectiveKey === 'custom' ? '' : 'none';
    if (customLabel) customInput.value = customLabel;
    customInput.addEventListener('input', function () {
        row.dataset.feeCustomLabel = customInput.value.trim();
    });
    row.dataset.feeCustomLabel = customLabel;

    const signHint = document.createElement('span');
    signHint.className = 'fee-sign-hint';

    const amountInput = document.createElement('input');
    amountInput.type = 'number';
    amountInput.step = '0.01';
    amountInput.className = 'fee-amount-input form-control form-control-sm';
    amountInput.placeholder = '0.00';
    amountInput.title = '填写该项费用金额；折扣 / 抹零会从总额中扣减';
    // 非自定义类型禁止负数
    if (effectiveKey !== 'custom') {
        amountInput.min = '0';
    }
    if (amountValue != null && !isNaN(Number(amountValue))) {
        amountInput.value = Number(amountValue).toFixed(2);
    }
    amountInput.addEventListener('input', function () {
        // 非自定义时强制非负
        if (sel.value !== 'custom') {
            const v = parseFloat(amountInput.value);
            if (!isNaN(v) && v < 0) amountInput.value = Math.abs(v).toFixed(2);
        }
        row.dataset.feeKey = sel.value || '';
        recalcTotalSum();
    });

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'fee-remove-btn btn btn-secondary btn-sm';
    removeBtn.textContent = '删除';
    removeBtn.title = '删除该费用行（该项按 0 计入单据）';
    removeBtn.addEventListener('click', function () { removeFeeRow(removeBtn); });

    row.appendChild(sel);
    row.appendChild(customInput);
    row.appendChild(signHint);
    row.appendChild(amountInput);
    row.appendChild(removeBtn);
    updateRowSignHint(row);
    container.appendChild(row);
    updateFeesEmptyState();
    return row;
}

// 「+ 添加费用」入口：追加空行并自动聚焦原因下拉
function addFeeRow() {
    const row = appendFeeRow('', null);
    if (row) {
        const sel = row.querySelector('.fee-type-select');
        if (sel) sel.focus();
    }
}
window.addFeeRow = addFeeRow;

// 类型互斥：同一费用原因只能有一行（自定义不参与互斥检查）。
function onFeeTypeChange(sel, row) {
    const val = sel.value || '';
    const customInput = row.querySelector('.fee-custom-label');
    const amountInput = row.querySelector('.fee-amount-input');

    // 自定义：显示文本输入，允许负数；预设：隐藏文本输入，禁止负数
    if (customInput) customInput.style.display = val === 'custom' ? '' : 'none';
    if (amountInput) {
        if (val === 'custom') {
            amountInput.removeAttribute('min');
        } else {
            amountInput.min = '0';
            // 若当前金额为负则强制转正
            const v = parseFloat(amountInput.value);
            if (!isNaN(v) && v < 0) amountInput.value = Math.abs(v).toFixed(2);
        }
    }

    updateRowSignHint(row);
    if (!val) {
        row.dataset.feeKey = '';
        recalcTotalSum();
        return;
    }
    if (val !== 'custom') {
        const dup = findFeeRowByType(val, row);
        if (dup) {
            showToast('该项已添加，已在上方标出', 'warning');
            sel.value = '';
            if (customInput) customInput.style.display = 'none';
            updateRowSignHint(row);
            dup.classList.add('fee-row-flash');
            dup.scrollIntoView({ block: 'center' });
            const dupAmount = dup.querySelector('.fee-amount-input');
            if (dupAmount) dupAmount.focus();
            setTimeout(function () { dup.classList.remove('fee-row-flash'); }, 1600);
            return;
        }
    }
    row.dataset.feeKey = val;
    recalcTotalSum();
}

function findFeeRowByType(typeKey, excludeRow) {
    const container = document.getElementById('feesRowsContainer');
    if (!container) return null;
    const rows = container.querySelectorAll('.fee-row');
    for (let i = 0; i < rows.length; i++) {
        if (rows[i] === excludeRow) continue;
        const sel = rows[i].querySelector('.fee-type-select');
        if (sel && sel.value === typeKey) return rows[i];
    }
    return null;
}

function updateRowSignHint(row) {
    if (!row) return;
    const sel = row.querySelector('.fee-type-select');
    const hint = row.querySelector('.fee-sign-hint');
    if (!sel || !hint) return;
    if (sel.value === 'custom') {
        hint.textContent = '+/-';
    } else {
        const meta = feeTypeMeta(sel.value);
        hint.textContent = meta ? meta.sign : '';
    }
}

// 删除行 = 该项归 0（collectFeeMap 不再产出该字段，等价 0 提交）
function removeFeeRow(btn) {
    const row = btn.closest('.fee-row');
    if (row) row.remove();
    updateFeesEmptyState();
    recalcTotalSum();
}

function updateFeesEmptyState() {
    const container = document.getElementById('feesRowsContainer');
    const emptyTip = document.getElementById('feesRowsEmpty');
    if (!container || !emptyTip) return;
    if (container.children.length > 0) {
        emptyTip.classList.add('hide');
    } else {
        emptyTip.classList.remove('hide');
    }
}

// 费用行 → 六契约字段映射（未出现的类型 = 0；类型互斥由下拉去重保证）
function collectFeeMap() {
    const feeMap = {
        discount_amount: 0.00,
        delivery_fee: 0.00,
        service_fee: 0.00,
        tax_amount: 0.00,
        deposit_amount: 0.00,
        rounding_adjustment: 0.00
    };
    const container = document.getElementById('feesRowsContainer');
    if (!container) return feeMap;
    container.querySelectorAll('.fee-row').forEach(row => {
        const typeSel = row.querySelector('.fee-type-select');
        if (!typeSel || !typeSel.value) return;
        const amt = parseFloat(row.querySelector('.fee-amount-input')?.value);
        if (isNaN(amt)) return;
        feeMap[typeSel.value] = amt;
    });
    return feeMap;
}

function updateFeesSummaryBadge() {
    const fees = collectFeeMap();
    const badge = document.getElementById('feesSummaryBadge');
    if (!badge) return;
    const hasFees = (fees.discount_amount > 0 || fees.delivery_fee > 0 || fees.deposit_amount > 0
        || fees.rounding_adjustment > 0 || fees.service_fee > 0 || fees.tax_amount > 0);
    if (hasFees) {
        const netFee = (fees.delivery_fee + fees.deposit_amount + fees.service_fee + fees.tax_amount
            - fees.discount_amount - fees.rounding_adjustment);
        const sign = netFee >= 0 ? '+' : '';
        badge.textContent = `差额: ${sign}${netFee.toFixed(2)}`;
        badge.classList.remove('hide');
    } else {
        badge.classList.add('hide');
    }
}
window.updateFeesSummaryBadge = updateFeesSummaryBadge;

// -------------------------------------------------------------
// 手写注记动态行（与附加费用动态行同款交互）：
// 契约字段不变：adjustment_notes: list[str]——仅换视图，落库链路零改动。
// -------------------------------------------------------------
// 单据数据 → 注记行：数组每项一行（与费用行「非零字段成行」同策略）
function renderNoteRowsFromData(data) {
    const container = document.getElementById('notesRowsContainer');
    if (!container) return;
    container.innerHTML = '';
    // 标记行容器已由单据/候选数据渲染过（评测工作台据此判断删行后不再回落候选注记）
    container.dataset.seededFromData = '1';
    const notesList = Array.isArray(data && data.adjustment_notes) ? data.adjustment_notes : [];
    notesList.forEach(note => {
        appendNoteRow(String(note == null ? '' : note));
    });
    updateNotesEmptyState();
    updateNotesSummaryBadge();
    // 有注记时自动展开抽屉，无注记保持折叠零负担（与费用抽屉同策略）
    const drawer = document.getElementById('notesDrawerContent');
    const icon = document.getElementById('notesToggleIcon');
    if (drawer) {
        if (container.children.length > 0) {
            drawer.classList.remove('hide');
            if (icon) icon.textContent = '▲';
        } else {
            drawer.classList.add('hide');
            if (icon) icon.textContent = '▾';
        }
    }
}

// 追加一行注记 = [注记文本输入] + [删除按钮]
function appendNoteRow(noteValue) {
    const container = document.getElementById('notesRowsContainer');
    if (!container) return null;

    const row = document.createElement('div');
    row.className = 'fee-row note-row';

    const textInput = document.createElement('input');
    textInput.type = 'text';
    textInput.className = 'note-text-input form-control form-control-sm';
    textInput.placeholder = '如: 拒收 2 包 / 短装 1 箱';
    textInput.title = '对照原图一行一条抄录手写注记';
    textInput.value = String(noteValue == null ? '' : noteValue);
    textInput.addEventListener('input', function () {
        updateNotesSummaryBadge();
    });

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'fee-remove-btn btn btn-secondary btn-sm';
    removeBtn.textContent = '删除';
    removeBtn.title = '删除该注记行';
    removeBtn.addEventListener('click', function () { removeNoteRow(removeBtn); });

    row.appendChild(textInput);
    row.appendChild(removeBtn);
    container.appendChild(row);
    updateNotesEmptyState();
    updateNotesSummaryBadge();
    return row;
}

// 「+ 添加注记」入口：追加空行并自动聚焦文本输入
function addNoteRow() {
    const row = appendNoteRow('');
    if (row) {
        const inp = row.querySelector('.note-text-input');
        if (inp) inp.focus();
    }
}
window.addNoteRow = addNoteRow;

// 删除行 = 移除该项注记（采集时不再产出）
function removeNoteRow(btn) {
    const row = btn.closest('.note-row');
    if (row) row.remove();
    updateNotesEmptyState();
    updateNotesSummaryBadge();
}

function updateNotesEmptyState() {
    const container = document.getElementById('notesRowsContainer');
    const emptyTip = document.getElementById('notesRowsEmpty');
    if (!container || !emptyTip) return;
    if (container.children.length > 0) {
        emptyTip.classList.add('hide');
    } else {
        emptyTip.classList.remove('hide');
    }
}

// 注记行 → list[str]（空行剔除，与旧 textarea 一行一条语义一致）
function collectNoteRows() {
    const notes = [];
    const container = document.getElementById('notesRowsContainer');
    if (!container) return notes;
    container.querySelectorAll('.note-row').forEach(row => {
        const raw = row.querySelector('.note-text-input')?.value || '';
        const s = String(raw).trim();
        if (s !== '') notes.push(s);
    });
    return notes;
}

// 标题徽章「N 条注记」实时联动（新增/删除/输入均触发）
function updateNotesSummaryBadge() {
    const badge = document.getElementById('notesSummaryBadge');
    if (!badge) return;
    const count = collectNoteRows().length;
    if (count > 0) {
        badge.textContent = count + ' 条注记';
        badge.classList.remove('hide');
    } else {
        badge.classList.add('hide');
    }
}
window.updateNotesSummaryBadge = updateNotesSummaryBadge;

function autoFillSheetNameFromDate(dateVal) {
    const sheetInp = document.getElementById('inpSheet');
    if (sheetInp && (!sheetInp.value || sheetInp.value.trim() === '')) {
        if (dateVal && dateVal.length >= 7) {
            sheetInp.value = dateVal.slice(0, 7);
        }
    }
}
window.autoFillSheetNameFromDate = autoFillSheetNameFromDate;

// -------------------------------------------------------------
// 6b. 结算方式与付款标记 (M3/D4)：
// 系统自动检测商户单据付款标记（印章/手写/签名等），用户仅需确认「已付款」或「未付款」
function hasMerchantPaymentMark(data) {
    if (!data) return false;
    if (data.payment_marked === true || data.payment_marked === 'true' || data.payment_marked === 1) return true;
    const mark = String(data.payment_mark || '').trim().toLowerCase();
    if (!mark || mark === '无' || mark === 'none' || mark === '未付款' || mark === 'false' || mark === '0' || mark === 'unpaid') {
        return false;
    }
    return true;
}

function getPaymentMarkDisplayInfo(data) {
    const rawMark = (data && (data.payment_mark !== undefined && data.payment_mark !== null)) ? String(data.payment_mark).trim() : '';
    const isPaidFlag = hasMerchantPaymentMark(data);

    if (isPaidFlag) {
        let desc = '';
        if (rawMark && rawMark !== '已付款' && rawMark !== 'paid' && rawMark !== 'true' && rawMark !== '单据有付款印记') {
            desc = ` (含${rawMark})`;
        }
        return {
            isPaid: true,
            label: '单据有付款印记',
            badgeText: `已检测到单面付款印记${desc}`,
            badgeColor: '#2f6b4f',
            badgeBg: 'rgba(47, 107, 79, 0.1)'
        };
    }

    // 区分明确未付款与未识别/不确定
    const norm = rawMark.toLowerCase();
    const isExplicitUnpaid = norm === '未付款' || norm === 'unpaid' || norm === '0' || norm === 'false';

    if (isExplicitUnpaid) {
        return {
            isPaid: false,
            label: '单据未见付款印章',
            badgeText: '未检测到原单印记（原单无盖章，不代表系统已付）',
            badgeColor: '#b91c1c',
            badgeBg: 'rgba(185, 28, 28, 0.1)'
        };
    }

    // 空/未填/无法确认
    return {
        isPaid: false,
        label: '单据未见付款印章',
        badgeText: '不确定/待人工核验付款标记',
        badgeColor: '#b45309',
        badgeBg: 'rgba(217, 119, 6, 0.1)'
    };
}

// 付款标记点击切换徽章（用户实测反馈：付款标记改为「单据有付款印记/单据未见付款印章」点击切换，
// 绿=有付款印记、红=未见付款印章；禁止要求店员手输付款证据）。state 存于按钮 data-marked，
// 文案随态变化（色盲友好），颜色仅作辅助。
function getPaymentMarkBadgeState() {
    const btn = document.getElementById('inpPaymentMark');
    return !!(btn && btn.dataset && btn.dataset.marked === 'true');
}

function setPaymentMarkBadge(marked) {
    const btn = document.getElementById('inpPaymentMark');
    if (!btn) return;
    const isPaid = (marked === true || marked === 'true');
    btn.dataset.marked = isPaid ? 'true' : 'false';
    btn.title = isPaid ? '原单上有付款印章或「已付」字样（点一下取消）' : '原单上没有付款印章（原单有盖章或写「已付」就点一下打开；这不等于系统已付款）';
    btn.classList.toggle('payment-mark-paid', isPaid);
    btn.classList.toggle('payment-mark-unpaid', !isPaid);
    btn.setAttribute('aria-pressed', isPaid ? 'true' : 'false');
    btn.setAttribute('aria-checked', isPaid ? 'true' : 'false');
}

function togglePaymentMarkBadge() {
    setPaymentMarkBadge(!getPaymentMarkBadgeState());
}
window.setPaymentMarkBadge = setPaymentMarkBadge;
window.getPaymentMarkBadgeState = getPaymentMarkBadgeState;
window.togglePaymentMarkBadge = togglePaymentMarkBadge;

// G3/U2 付款证据自动带出：识别/检测信息里的证据描述由系统写入（无手输入口），
// 内存态透传后端提交（getAutoPaymentEvidence 于采集侧消费）。
// 历史注记：曾有只读展示区 #inpPaymentEvidenceAuto + updatePaymentEvidenceAutoDisplay
// 刷新函数，后随复核区重设计移除 DOM 展示——展示函数已删（目标元素不存在、零调用方），
// 数据通道（set/get 内存态）保留。
let autoPaymentEvidence = '';
function setAutoPaymentEvidence(value) {
    autoPaymentEvidence = String(value == null ? '' : value).trim();
}
function getAutoPaymentEvidence() {
    return autoPaymentEvidence;
}
window.setAutoPaymentEvidence = setAutoPaymentEvidence;
window.getAutoPaymentEvidence = getAutoPaymentEvidence;

// prefix='inp' → Tab1 复核表单；prefix='arc' → 归档弹窗
function applySettlementToForm(prefix, data) {
    const d = data || {};
    const sel = document.getElementById(prefix + 'SettlementType');
    if (sel) {
        sel.value = (d.settlement_type === 'cash' || d.settlement_type === 'credit') ? d.settlement_type : '';
    }
    const markInput = document.getElementById(prefix + 'PaymentMark');
    const badge = document.getElementById(prefix + 'PaymentMarkDetectBadge');
    const info = getPaymentMarkDisplayInfo(d);

    // 复核表单(#inp)付款标记为点击切换徽章（灌 state）；归档弹窗(#arc)仍为下拉，按元素形态分流
    if (markInput && markInput.tagName === 'BUTTON') {
        setPaymentMarkBadge(info.isPaid);
    } else if (markInput) {
        markInput.value = info.isPaid ? '已付款' : '未付款';
    }
    if (badge) {
        badge.textContent = info.badgeText;
        badge.style.color = info.badgeColor;
        badge.style.background = info.badgeBg;
        badge.style.padding = '2px 6px';
        badge.style.borderRadius = '4px';
    }
}
// 明细行「待核验」判定的唯一实现——复核台（appendTableRow）与归档弹窗
// （appendArcTableRow）共用同一份逻辑，避免「同一根因多份实现」导致两处口径漂移。
//
// 触发依据是**确定性信号**，不再依赖模型自报置信度：实测真实读数集中在 0.85~0.98，
// 旧阈值 0.40 永不触发；而抬到 0.50 又会让整表历史兜底值（0.5）全部标黄，
// 0.40~0.49 之间没有任何可用的中间档——阈值这条路解决不了问题。
// 当前行级确定性信号：unit_conversion_warning —— 解析链路上 huama_evaluator 确定性
// 命中街市花码（苏州码子）时写入「包含街市花码，需人工核验」，随行落库并透传到前端。
// 返回空数组表示该行无需额外核验提示。
function itemReviewFlags(item) {
    const flags = [];
    if (!item) return flags;
    const ucw = (item.unit_conversion_warning != null)
        ? String(item.unit_conversion_warning).trim() : '';
    if (ucw) {
        flags.push({
            key: 'unit_conversion_warning',
            label: /花码/.test(ucw) ? '花码待核验' : '单位待核验',
            reason: ucw,
        });
    }
    return flags;
}

// 行级待核验角标 HTML（无标记时返回空串）：样式 class 与 P11 抽出的共用实现保持一致，
// 复核台与归档弹窗看到的是同一个角标，不另造第二套。
// conf 仅作参考信息附在 title 里（不再参与触发判定）：置信度保留但不再决定是否标记。
function itemReviewBadgeHtml(flags, conf) {
    if (!Array.isArray(flags) || flags.length === 0) return '';
    const titleParts = flags.map(f => f.reason).filter(Boolean);
    if (typeof conf === 'number' && isFinite(conf)) {
        titleParts.push('识别置信度 ' + conf.toFixed(2) + '（仅供参考，不作为复核触发依据）');
    }
    return '<span class="item-conf-badge" title="' + w2Escape(titleParts.join('；'))
        + '，请对照原图逐行核验">' + w2Escape(flags[0].label) + '</span>';
}

// 整单级确定性信号的取值与判据——只有这一处定义，复核台提示条、归档详情提示条、
// 归档列表黄底三处共用，避免「街市手写单」这四个字被抄成多份实现。
// HANDWRITTEN_DOC_FORM 与后端 DocForm.ncr_handwritten 同源。
const HANDWRITTEN_DOC_FORM = 'ncr_handwritten';

// 状态显示名取自 i18n 键值目录（status.*）；i18n 未加载（如 node 沙箱）时回落原文。
function i18nLabel(key, fallback) {
    const i18n = (typeof window !== 'undefined') ? window.I18N : null;
    return (i18n && typeof i18n.t === 'function') ? i18n.t(key, null, fallback) : fallback;
}

// 单据状态唯一事实源：标签/徽章样式/悬停说明/聚合分组/筛选项文案集中在此，
// 归档徽章（renderStatusBadge）、归档聚合过滤（STATUS_GROUP_MAP）、
// 待办条计数（updateTodoBar）、筛选下拉（populateReceiptStatusFilter）四处共用。
// 新增一个状态只需在此登记一次，不再出现「改了三处漏一处」的漂移。
// review=true 表示「尚未经过人工核对」，供黄底/复核提示判定使用（区别于聚合分组的 pending）。
function _termStatus(code, field) {
    const t = (typeof TERMS !== 'undefined' && TERMS.status) ? TERMS.status[code] : null;
    return t ? t[field] : '';
}
const RECEIPT_STATUSES = {
    uploaded:             { label: i18nLabel('status.uploaded', _termStatus('uploaded', 'label')),     cls: 'badge-warning', title: _termStatus('uploaded', 'title'), group: 'pending', filter: _termStatus('uploaded', 'filter'), review: true },
    parsing:              { label: i18nLabel('status.parsing', _termStatus('parsing', 'label')),       cls: 'badge-warning', title: _termStatus('parsing', 'title'), group: 'pending', filter: _termStatus('parsing', 'filter'), review: true },
    parsed:               { label: i18nLabel('status.parsed', _termStatus('parsed', 'label')),         cls: 'badge-warning', title: _termStatus('parsed', 'title'), group: 'pending', filter: _termStatus('parsed', 'filter'), review: true },
    parsed_with_warnings: { label: i18nLabel('status.parsed_with_warnings', _termStatus('parsed_with_warnings', 'label')), cls: 'badge-warning', title: _termStatus('parsed_with_warnings', 'title'), group: 'pending', filter: _termStatus('parsed_with_warnings', 'filter'), review: true },
    edited:               { label: i18nLabel('status.edited', _termStatus('edited', 'label')),         cls: 'badge-info',    title: _termStatus('edited', 'title'), group: 'pending', filter: _termStatus('edited', 'filter') },
    approved:             { label: i18nLabel('status.approved', _termStatus('approved', 'label')),     cls: 'badge-success', title: _termStatus('approved', 'title'), group: 'posted', filter: _termStatus('approved', 'filter') },
    flagged:              { label: i18nLabel('status.flagged', _termStatus('flagged', 'label')),       cls: 'badge-danger',  title: _termStatus('flagged', 'title'), group: 'problem', filter: _termStatus('flagged', 'filter') },
    reversed:             { label: i18nLabel('status.reversed', _termStatus('reversed', 'label')),     cls: 'badge-secondary', title: _termStatus('reversed', 'title'), group: 'reversed', filter: _termStatus('reversed', 'filter') },
    error:                { label: i18nLabel('status.error', _termStatus('error', 'label')),           cls: 'badge-danger',  title: _termStatus('error', 'title'), group: 'failed', filter: _termStatus('error', 'filter') },
};

// 「还没经过人工核对」的状态白名单（由 RECEIPT_STATUSES.review 派生，顺序与定义一致）
const RECEIPT_PENDING_REVIEW_STATES = Object.keys(RECEIPT_STATUSES)
    .filter(k => RECEIPT_STATUSES[k].review);

// 状态聚合映射（UI 口语，D-SUP-10 / 4.3）——由 RECEIPT_STATUSES.group 派生，避免手工维护第二份清单
const STATUS_GROUP_MAP = Object.keys(RECEIPT_STATUSES).reduce((acc, key) => {
    const g = RECEIPT_STATUSES[key].group;
    (acc[g] || (acc[g] = [])).push(key);
    return acc;
}, {});

// 手工录入单：doc_form=manual_entry，或正处于「新建手工单」态
function isManualReceiptData(data) {
    if (data && String(data.doc_form || '') === 'manual_entry') return true;
    return (typeof isManualEntry !== 'undefined') && isManualEntry && !(data && data.id);
}

// 复核卡标题只留一个来源徽章：手工录入 或 AI 预填
function syncReviewSourceBadge(data) {
    const manual = isManualReceiptData(data);
    const mBadge = document.getElementById('manualEntryFormBadge');
    const aBadge = document.getElementById('aiPrefillNoticeBadge');
    if (mBadge) mBadge.classList.toggle('hide', !manual);
    if (aBadge) aBadge.classList.toggle('hide', manual);
}

function isHandwrittenReceipt(data) {
    return String((data && data.doc_form) || '') === HANDWRITTEN_DOC_FORM;
}

function isReceiptPendingHumanReview(status) {
    return RECEIPT_PENDING_REVIEW_STATES.indexOf(String(status || '')) !== -1;
}

// 整单级「需人工复核」判定的唯一实现——识别复核台（renderEditForm 的 alertBanner）
// 与归档详情（applyArchiveDetail 的 archiveMathWarnBanner）共用。
// 只收确定性信号，且严格与行级分层：整单信号只出现在整单级提示条上，
// 绝不贴到每一行（否则就是「全表标黄」的同类问题）。
// 当前整单级信号：① 街市手写单（doc_form=ncr_handwritten）；
//               ② 门禁未通过 / 花码整单校准警告（math_warnings 非空）。
function receiptReviewReasons(data) {
    const reasons = [];
    if (!data) return reasons;
    if (isHandwrittenReceipt(data)) {
        reasons.push('街市手写单（NCR）：手写字迹与花码易误读，建议逐行对照原图');
    }
    const hasStructured = Array.isArray(data.gate_issues) && data.gate_issues.length > 0 && typeof TERMS !== 'undefined';
    const mw = hasStructured ? TERMS.formatGateIssues(data.gate_issues)
        : (Array.isArray(data.math_warnings) ? data.math_warnings : []);
    mw.forEach(m => {
        const t = hasStructured ? m : humanizeGateMsg(m);
        if (t && reasons.indexOf(t) === -1) reasons.push(t);
    });
    return reasons;
}

// 读取整单级复核信号（图像质量预检 / 交叉审核分歧）——供明细行黄底提示使用。
// 返回值：对象 = 读取成功；null = 读取失败。
//
// why：原实现把这两次读取整段包在 `try { ... } catch (e) {}` 里，一旦抛错就静默
// 让「质量预检 / 重点复核」从该单据的**每一行**上消失，且不留任何痕迹——本仓高发的
// 第三类缺陷（异常被静默吞掉 → 功能静默失效）。现在：异常必须可观测（console.warn）
// 且 fail-visible（调用方把该行标成「待核验」），绝不允许继续静默消失。
function readReceiptLevelReviewSignals() {
    try {
        const cur = (typeof currentReceiptData !== 'undefined') ? currentReceiptData : null;
        if (!cur) return { hasQualityWarning: false, highReviewPriority: false };
        const qws = Array.isArray(cur.quality_warnings) ? cur.quality_warnings : null;
        const rps = (typeof cur.review_priority_score === 'number') ? cur.review_priority_score : null;
        return {
            hasQualityWarning: !!(qws && qws.length > 0),
            highReviewPriority: (rps != null && rps > 0.6),
        };
    } catch (e) {
        console.warn('[review-flag] 读取整单级复核信号失败，按「待核验」fail-visible 处理：', e);
        return null;
    }
}

// -------------------------------------------------------------
// 复核侧「供应商×SKU 历史价格区间」提示（收敛计划 P2-T4 / 概念 C5）
// -------------------------------------------------------------
// 定位：Side-by-Side 复核界面的人眼提示 —— **只做人类可见提示，绝不写入生成侧 prompt**
// （若注入生成侧，模型会照抄上一单的价格形成锚定；生成侧只放别名/单位/版式等确定性事实）。
// 数据源：GET /api/vendor_price_hint（只读），后端复用 db.price_history（inventory_log
// kind=in 自带 vendor 字段）按供应商过滤 —— 与库存页/成本层同一口径，不另建一份价格历史。
// 边界：① 无该供应商的历史一律隐藏（不渲染空壳）；
//      ② 区间与「当前单价是否超出」全部由后端算 —— 前端不得自己算，否则会与库存页阈值
//         口径分叉（本仓高发的「同一口径多份实现」）；
//      ③ 供应商 / SKU / 单价任一变化都重取（这三者都是区间判定的输入），并用序号丢弃
//         过期响应，避免慢响应回来把新结果盖掉。

// 纯渲染：hint 为 null / 无历史 / 数字非法 → 返回空串（调用方据此隐藏，绝不显示空壳）。
function vendorPriceHintHtml(hint) {
    if (!hint || !hint.has_history) return '';
    const low = Number(hint.low);
    const high = Number(hint.high);
    const count = Number(hint.count || 0);
    if (!isFinite(low) || !isFinite(high) || count <= 0) return '';
    const sym = currencySymbol(currentCurrencyCode());
    const range = (low === high)
        ? sym + low.toFixed(2)
        : sym + low.toFixed(2) + '–' + high.toFixed(2);
    const text = '供应商历史区间 ' + range + '（' + count + ' 次）';
    const why = '该供应商在本店此 SKU 的历史进货单价区间（来自库存流水），仅供参考';
    if (hint.out_of_range) {
        // direction 缺省/异常时不猜方向，只说「超出」（后端 out_of_range=true 必带方向）
        const beyond = (hint.direction === 'down') ? '低于区间'
            : (hint.direction === 'up' ? '高于区间' : '超出区间');
        return '<div class="vendor-price-hint vendor-price-hint-out" title="' + w2Escape(why + '；本次' + beyond) + '"'
            + ' style="font-size:0.7rem; line-height:1.25; color:#b45309; background:#fef3c7;'
            + ' border-radius:4px; padding:1px 5px; max-width:100%; word-break:break-word;">'
            + w2Escape(text + ' · 本次' + beyond) + '</div>';
    }
    return '<div class="vendor-price-hint" title="' + w2Escape(why) + '"'
        + ' style="font-size:0.7rem; line-height:1.25; color:#64748b; max-width:100%; word-break:break-word;">'
        + w2Escape(text) + '</div>';
}

// 提示的「供应商」维度来源：实时读表单字段（而非渲染时快照），
// 这样用户改了供应商，提示会跟着变而不是停留在旧供应商的结果上。
function currentSupplierForHint() {
    try {
        const el = document.getElementById('inpSupplier');
        return el ? String(el.value || '').trim() : '';
    } catch (e) {
        // 读不到就按「无供应商」处理（提示会隐藏）—— 但必须留痕，不静默吞掉异常
        console.warn('[vendor-price-hint] 读取供应商输入框失败，按「无供应商」处理：', e);
        return '';
    }
}

// 拉取并渲染某一行的历史价格区间提示；非明细行（无提示容器）直接忽略。
// 返回 Promise（渲染完成）——调用方多为 fire-and-forget，测试可据此等待收敛。
function refreshVendorPriceHint(tr) {
    if (!tr || typeof tr.querySelector !== 'function') return Promise.resolve();
    const box = tr.querySelector('.vendor-price-hint-box');
    if (!box) return Promise.resolve();
    const idInput = tr.querySelector('.inp-sku-id');
    const skuId = idInput ? String(idInput.value || '').trim() : '';
    const vendor = currentSupplierForHint();
    // 「供应商×SKU」两维缺一即无从取值 → 清空（既不留空壳，也不留上一次的结果）
    if (!skuId || !vendor) { box.innerHTML = ''; return Promise.resolve(); }

    const priceInput = tr.querySelector('.inp-price');
    const price = priceInput ? Number(priceInput.value) : NaN;
    const qs = '/api/vendor_price_hint?sku_id=' + encodeURIComponent(skuId)
        + '&vendor=' + encodeURIComponent(vendor)
        + ((isFinite(price) && price > 0) ? '&unit_price=' + encodeURIComponent(price) : '');

    const seq = String((Number(box.dataset.hintSeq) || 0) + 1);
    box.dataset.hintSeq = seq;
    return apiFetch(qs)
        .then(res => res.json())
        .then(ret => {
            if (box.dataset.hintSeq !== seq) return;   // 过期响应（价格/SKU/供应商已变）
            box.innerHTML = (ret && ret.status === 'success') ? vendorPriceHintHtml(ret) : '';
        })
        .catch(err => {
            // 提示属辅助信息：失败隐藏，但不静默 —— 留痕，避免本仓「异常被吞掉 → 功能静默失效」
            console.warn('[vendor-price-hint] 读取供应商历史价格区间失败，已隐藏该提示：', err);
            if (box.dataset.hintSeq === seq) box.innerHTML = '';
        });
}

// 整表重取（供应商变化时调用；动态行也一并覆盖）。
function refreshAllVendorPriceHints() {
    const tbody = document.getElementById('itemTableBody');
    if (!tbody || typeof tbody.querySelectorAll !== 'function') return;
    tbody.querySelectorAll('tr').forEach(tr => refreshVendorPriceHint(tr));
}

// 空明细占位行判据（renderEditForm 在 items=[] 时写入的 tr.warning-row，行内无任何
// 输入控件）。用户补录第一行明细前必须先移除它：占位行与真行共存时，采集侧对无 input
// 的行取 .inp-name.value 会抛 TypeError —— 异常冒泡出 submitSaveEdited，表现为
// 「点确认上传单据后不发任何请求、界面无任何提示」。
const ITEM_TABLE_PLACEHOLDER_SELECTOR = 'tr.warning-row';
function clearItemTablePlaceholder(tbody) {
    if (!tbody || typeof tbody.querySelectorAll !== 'function') return;
    tbody.querySelectorAll(ITEM_TABLE_PLACEHOLDER_SELECTOR).forEach(ph => {
        if (ph && typeof ph.remove === 'function') ph.remove();
    });
}

// WS5-3：行级红色异动角标点击下钻 —— 复用 WS2 的供应商历史报价提示通道
// （refreshVendorPriceHint + 行内 vendor-price-hint-box），不新造第二套下钻 Modal。
// 角标与提示同源基准（「较上一笔进价」）；无 SKU / 无供应商时由提示函数自身
// 走「清空 + 不发请求」分支，不会抛错。
function drillIntoRowVendorPriceHint(el) {
    const tr = el && el.closest ? el.closest('tr') : null;
    if (!tr) return;
    const box = tr.querySelector('.vendor-price-hint-box');
    if (!box) {
        showToast('该行暂不支持历史报价下钻', 'info');
        return;
    }
    box.classList.remove('hide');
    refreshVendorPriceHint(tr);
    if (box.scrollIntoView) box.scrollIntoView({block: 'nearest'});
}

function appendTableRow(item = {}) {
    const tbody = document.getElementById('itemTableBody');

    const rawName = item.name || item.raw_name || '';
    const qty = Number(item.quantity || 1.0);
    const unit = item.raw_unit || item.unit || 'kg';
    const price = Number(item.unit_price || 0.00);
    const amount = Number(item.amount != null ? item.amount : (qty * price));
    const skuId = item.sku_id || '';
    const isAnomaly = item.price_anomaly || false;
    const actualQtyVal = (item.actual_qty != null && item.actual_qty !== '') ? String(item.actual_qty) : '';

    const rowWarnings = [];
    // 行级待核验判据（确定性信号，见 itemReviewFlags）：命中即角标 + 黄底。
    // itemConfRaw 为原始置信度读数（保存时随行回传，仅作参考，不参与触发判定）。
    const itemConfRaw = (typeof item.confidence === 'number' && isFinite(item.confidence)) ? item.confidence : null;
    let itemFlags = itemReviewFlags(item);
    const _receiptSignals = readReceiptLevelReviewSignals();
    if (_receiptSignals === null) {
        // fail-visible：整单级信号读取异常时不能静默让角标消失 —— 明确标成「待核验」
        // （异常本身已由 readReceiptLevelReviewSignals 输出 console 警告留痕）。
        itemFlags = itemFlags.concat([{
            key: 'receipt_signal_unreadable',
            label: '待核验',
            reason: '整单复核信号读取失败（已输出 console 警告），请对照原图核对该行',
        }]);
    }
    if (itemFlags.length) {
        rowWarnings.push(itemFlags[0].label);
    }
    // 置信度不再触发标记，但保留为参考信息：挂在品名输入的 title 上（不占版面、不新增列），
    // 与 tr.dataset.confidence（保存回传）两处同源。
    const itemConfRefTitle = (itemConfRaw !== null)
        ? '识别置信度 ' + itemConfRaw.toFixed(2) + '（仅供参考，不作为复核触发依据）' : '';
    if (item.matched === false && !item.sku_id) {
        rowWarnings.push('SKU未匹配');
    }

    // 整单级信号 → 行内黄底提示（读取失败时已按「待核验」处理，此处不再重复标记）
    if (_receiptSignals !== null) {
        if (_receiptSignals.hasQualityWarning) {
            rowWarnings.push('质量预检');
        }
        if (_receiptSignals.highReviewPriority) {
            rowWarnings.push('重点复核');
        }
    }

    const rowWarnClass = rowWarnings.length ? ' row-warning' : '';
    const tr = document.createElement('tr');
    tr.className = rowWarnClass;

    const skuName = item.sku_name || '';
    const skuCandidates = mergeSkuCandidates(
        (item.fuzzy_candidates || []).map(c => ({
            sku_id: c.sku_id,
            sku_name: c.sku_name || c.matched_text || '',
            sku_code: c.sku_code,
            score: c.score,
        })),
        (item.entity_candidates || []).map(c => ({
            sku_id: c.sku_id,
            sku_name: c.sku_name || '',
            sku_code: c.sku_code,
            score: c.score,
        }))
    );

    if (unit && !availableUnits.includes(unit)) {
        availableUnits.push(unit);
        updateGlobalDatalistUnits();
    }

    let finalRawName = rawName;
    let finalQty = qty;
    let finalUnit = unit;
    let finalPrice = price;
    const splitRes = smartSplitItemName(rawName);
    if (splitRes && (qty === 1 || !item.quantity)) {
        finalRawName = splitRes.cleanName;
        finalQty = splitRes.quantity;
        finalUnit = splitRes.unit || unit;
        if (amount > 0 && finalQty > 0) {
            finalPrice = parseFloat((amount / finalQty).toFixed(2));
        }
    }

    const isVoidMain = !!(item.is_void);
    if (isVoidMain) {
        tr.dataset.isVoid = '1';
        tr.style.opacity = '0.5';
    }

    // WS6：删除 `|| 10` 兜底 —— 0 或缺失绝不显示成假数字 10.0%。
    // 缺失 → '-'（不展示任何假数字）；0 → '0.0%'；符号方向由 ↓/↑ 角标显式表达。
    const rawDiffPct = item.price_diff_percent;
    const diffPctNum = (rawDiffPct === null || rawDiffPct === undefined || rawDiffPct === '')
        ? null : Number(rawDiffPct);
    const diffPctText = (diffPctNum !== null && Number.isFinite(diffPctNum))
        ? Math.abs(diffPctNum).toFixed(1) + '%' : '-';

    // WS5-3：角标改为可点击（cursor:pointer + title 说明），点击下钻到该行供应商历史报价。
    const anomalyHtml = isAnomaly ? `
        <span class="badge ${item.price_anomaly_direction === 'down' ? 'badge-info' : 'badge-danger'} anomaly-pill" style="cursor:pointer;" title="较上一笔进价偏离超过 ${currentThresholdPctLabel()}%（点击查看该行供应商的历史报价区间）" onclick="drillIntoRowVendorPriceHint(this)">
            ${item.price_anomaly_direction === 'down' ? '↓ 偏低' : '↑ 偏高'}${diffPctText}
        </span>
    ` : '';

    tr.innerHTML = `
        <td data-label="品名">
            <div class="item-name-sku-stack sku-combobox-wrap" style="position: relative;">
                <input type="text" class="inp-name form-control" value="${w2Escape(finalRawName)}" placeholder="品名(如走地鸡)" autocomplete="off"
                       onfocus="openSkuMenuForNameInput(this)" onclick="openSkuMenuForNameInput(this)" oninput="onItemNameInput(this)" onblur="closeSkuMenuDelay(this)"
                       ${itemConfRefTitle ? 'title="' + w2Escape(itemConfRefTitle) + '"' : ''}
                       ${isVoidMain ? 'style="text-decoration:line-through; color:#94a3b8;"' : ''}>
                <input type="hidden" class="inp-sku" value="${w2Escape(skuName)}">
                <input type="hidden" class="inp-sku-id" value="${skuId || ''}">
                <div class="unit-dropdown-menu sku-dropdown-floating hide"></div>
            </div>
            <div class="sku-candidate-box hide"></div>
        </td>
        <td data-label="数量">
            <div style="display:flex; flex-direction:column; gap:4px;">
                <input type="number" step="0.01" class="inp-qty form-control text-right" value="${finalQty}" oninput="recalcRow(this)" title="数量" placeholder="数量" ${isVoidMain ? 'disabled style="text-decoration:line-through;"' : ''}>
                <input type="number" step="0.01" class="inp-actual-qty form-control text-right" value="${w2Escape(actualQtyVal)}" placeholder="实收" title="实际数量（手写改量，留空则按数量计）" style="font-size:0.78rem; padding:4px 6px;" oninput="recalcRow(this)" ${isVoidMain ? 'disabled style="text-decoration:line-through;"' : ''}>
            </div>
        </td>
        <td data-label="单位">
            <div class="unit-combobox-wrap">
                <input type="text" class="inp-unit form-control text-center" value="${w2Escape(finalUnit)}" placeholder="单位" onfocus="this.select(); openUnitMenu(this)" onclick="openUnitMenu(this)" oninput="renderUnitMenuItems(this, this.nextElementSibling)" onblur="closeUnitMenuDelay(this)" ${isVoidMain ? 'style="text-decoration:line-through; color:#94a3b8;"' : ''}>
                <div class="unit-dropdown-menu hide"></div>
            </div>
        </td>
        <td data-label="单价">
            <div class="price-input-wrap">
                <input type="number" step="0.01" class="inp-price form-control text-right" aria-label="单价" value="${finalPrice}" oninput="recalcRow(this)" ${isVoidMain ? 'disabled' : ''}>
                ${anomalyHtml}
                <!-- P2-T4：供应商×SKU 历史价格区间提示（只做人类可见提示，不进生成侧 prompt）。
                     挂在单价单元格内 —— 不新增列、不改列序。 -->
                <div class="vendor-price-hint-box"></div>
            </div>
        </td>
        <td data-label="金额">
            <input type="number" step="0.01" class="inp-amount form-control text-right" aria-label="金额" value="${amount.toFixed(2)}" oninput="recalcTotalSum()" ${isVoidMain ? 'disabled' : ''}>
        </td>
        <td data-label="部门">
            <select class="inp-dept form-control" title="本行归属部门" style="width:100%; padding:6px; font-size:0.82rem;" ${isVoidMain ? 'disabled' : ''}>
                ${deptSelectOptionsHtml(item.cost_center_id)}
            </select>
        </td>
        <td style="text-align:center;" data-label="操作">
            <div class="row-actions">
                ${itemReviewBadgeHtml(itemFlags, itemConfRaw)}
                ${isVoidMain ? '<span class="badge badge-secondary" title="票面划线行：不计入总额与入库" style="margin-right:4px;">作废</span>' : ''}
                <button type="button" class="btn-action-delete" onclick="removeRow(this)" title="删除此明细行">删除</button>
            </div>
        </td>
    `;
    // 真行落表前先移除空明细占位行（否则占位行残留 → 采集侧取 .inp-name 抛错）
    clearItemTablePlaceholder(tbody);
    tbody.appendChild(tr);

    tr.dataset.confidence = (itemConfRaw !== null) ? String(itemConfRaw) : '';
    tr.dataset.unitConversionWarning = item.unit_conversion_warning
        ? String(item.unit_conversion_warning) : '';

    const skuInput = tr.querySelector('.inp-sku');
    if (skuInput) skuInput.__skuCandidates = skuCandidates;
    if (typeof renderSkuCandidates === 'function') renderSkuCandidates(tr, item);

    tr.addEventListener('input', () => { tr.dataset.manual = '1'; });
    tr.addEventListener('change', () => { tr.dataset.manual = '1'; });

    // P2-T4：单价是本行提示的判定输入（是否超出历史区间）→ 失焦时重取。
    // 区间仍由后端算，前端只负责「价格变了就再问一次」。
    const priceInput = tr.querySelector('.inp-price');
    if (priceInput) priceInput.addEventListener('change', () => refreshVendorPriceHint(tr));
    refreshVendorPriceHint(tr);
}

function addEmptyRow(opts) {
    appendTableRow({ raw_name: '', quantity: 1.0, raw_unit: 'kg', unit_price: 0.0, amount: 0.0 });
    const container = document.querySelector('#prefillFormCard .table-container');
    if (container) {
        container.scrollTop = container.scrollHeight;
    }
    if (!(opts && opts.silent)) {
        track('field_edited', currentReceiptId, { field_name: 'row_add' });
    }
}

function removeRow(btn) {
    const tr = btn.closest('tr');
    if (tr) {
        const idx = [...(tr.parentElement ? tr.parentElement.children : [])].indexOf(tr);
        track('field_edited', currentReceiptId, { field_name: 'row_remove', row_index: idx });
        tr.remove();
    }
    recalcTotalSum();
}




// 产品决策（见 docs 决策记录）：手动作废入口已移除——作废态仅来自票面划线的 AI 提取，只读展示；
// 用户剔除行的唯一操作是「删除」。is_void 行的置灰/划线/禁用样式在 appendTableRow 渲染时完成。

function recalcRow(inputElem) {
    const tr = inputElem.closest('tr');
    const qty = parseFloat(tr.querySelector('.inp-qty').value) || 0;
    const price = parseFloat(tr.querySelector('.inp-price').value) || 0;
    // 与算术门禁同口径：实收>0 时按实收计金额（math_engine effective_qty）
    const actualInput = tr.querySelector('.inp-actual-qty');
    const actual = actualInput ? parseFloat(actualInput.value) : NaN;
    const effQty = !isNaN(actual) && actual > 0 ? actual : qty;

    const amtInput = tr.querySelector('.inp-amount');
    amtInput.value = (effQty * price).toFixed(2);

    recalcTotalSum();
}

function recalcTotalSum() {
    const tbody = document.getElementById('itemTableBody');
    const rows = tbody.querySelectorAll('tr');
    let itemsSum = 0.0;
    rows.forEach(tr => {
        if (tr.dataset && tr.dataset.isVoid === '1') return;
        const amt = parseFloat(tr.querySelector('.inp-amount')?.value) || 0;
        itemsSum += amt;
    });

    // 附加费用：从动态费用行收集（与后端算术门禁 expected_total 同口径：
    // +delivery+deposit+serviceFee+tax-discount-rounding）
    const fees = collectFeeMap();

    const netTotal = Math.max(0, itemsSum - fees.discount_amount - fees.rounding_adjustment
        + fees.delivery_fee + fees.deposit_amount + fees.service_fee + fees.tax_amount);
    // S3-19：店员手改过总额就不再被明细合计覆盖，只提示差额
    if (typeof isTotalOverridden === 'function' && isTotalOverridden()) {
        if (typeof updateTotalOverrideHint === 'function') updateTotalOverrideHint(netTotal);
        if (typeof refreshReceiptIssueSummary === 'function') refreshReceiptIssueSummary();
    } else {
        document.getElementById('inpTotal').value = netTotal.toFixed(2);
    }
    updateFeesSummaryBadge();
    renderCurrencySymbol();
}

// 展开/收起 Prefill 表头更多元数据
function togglePrefillMoreMeta() {
    const box = document.getElementById('prefillMoreMetaBox');
    const text = document.getElementById('moreMetaToggleText');
    const icon = document.getElementById('moreMetaToggleIcon');
    if (!box) return;
    const isHidden = box.classList.contains('hide');
    if (isHidden) {
        box.classList.remove('hide');
        if (text) text.textContent = '收起辅助字段';
        if (icon) icon.textContent = '▲';
    } else {
        box.classList.add('hide');
        if (text) text.textContent = '更多字段 (币种/付款/归档)';
        if (icon) icon.textContent = '▼';
    }
}
window.togglePrefillMoreMeta = togglePrefillMoreMeta;

// F-P1-3 币种符号渲染：按当前选择币种刷新金额前缀符号 (U-13)
const CURRENCY_SYMBOLS = {
    HKD: 'HK$', CNY: '¥', USD: '$', EUR: '€', JPY: 'JP¥', GBP: '£', MOP: 'MOP$', SGD: 'S$'
};
function currentCurrencyCode() {
    const sel = document.getElementById('inpCurrency') || document.getElementById('arcCurrency');
    return (sel && sel.value ? sel.value : 'HKD').toUpperCase();
}
function currencySymbol(code) {
    return CURRENCY_SYMBOLS[(code || 'HKD').toUpperCase()] || (code || 'HKD') + ' ';
}
function formatCurrency(amount, currency = 'HKD') {
    const num = Number(amount);
    const validNum = (amount == null || isNaN(num) || !isFinite(num)) ? 0.00 : num;
    const cur = (currency || 'HKD').toString().trim().toUpperCase();
    const sym = CURRENCY_SYMBOLS[cur] || (cur ? cur + ' ' : 'HK$ ');
    const prefix = sym.endsWith(' ') || sym.endsWith('$') ? sym + ' ' : sym + ' ';
    return prefix + validNum.toFixed(2);
}
window.formatCurrency = formatCurrency;
window.currencySymbol = currencySymbol;
window.currentCurrencyCode = currentCurrencyCode;

function renderCurrencySymbol() {
    const inpCur = (document.getElementById('inpCurrency')?.value || 'HKD').toUpperCase();
    const arcCur = (document.getElementById('arcCurrency')?.value || 'HKD').toUpperCase();

    const inpTotal = document.getElementById('inpTotal');
    if (inpTotal) {
        const label = inpTotal.closest('.form-group')?.querySelector('label');
        if (label) {
            label.textContent = '整单总金额（' + currencySymbol(inpCur) + '）';
        }
    }
    const arcTotal = document.getElementById('arcTotal');
    if (arcTotal) {
        const label = arcTotal.closest('.form-group')?.querySelector('label');
        if (label) {
            label.textContent = '整单总金额（' + currencySymbol(arcCur) + '）';
        }
    }
}
window.renderCurrencySymbol = renderCurrencySymbol;

// -------------------------------------------------------------
// FR-8 反馈飞轮：点赞/点踩 + textarea 乐观 UI 与 qualityWarnings 联动
// -------------------------------------------------------------
function _feedbackTenantId() {
    try { return localStorage.getItem('demo_tenant_id') || 'default'; } catch (e) { return 'default'; }
}

// U-08：反馈收进弹层后，行内以小徽标呈现已反馈状态
function _syncFeedbackBadge(cell) {
    if (!cell) return;
    const td = cell.closest('td') || _rowFeedbackHomeTd;
    const badge = td ? td.querySelector('.feedback-badge') : null;
    if (!badge) return;
    const like = cell.dataset.like;
    if (like === '1') {
        badge.textContent = '已点赞';
        badge.style.display = '';
        badge.style.background = '#e8f5e9';
        badge.style.color = '#2e7d32';
    } else if (like === '-1') {
        badge.textContent = '已点踩';
        badge.style.display = '';
        badge.style.background = '#fdecea';
        badge.style.color = '#c62828';
    } else {
        badge.textContent = '';
        badge.style.display = 'none';
    }
}

// U-08：反馈弹层——点击行内「反馈」按钮，把该行 feedback-cell 移入 modal；关闭时移回
let _rowFeedbackHomeTd = null;

function openRowFeedbackModal(btn) {
    const td = btn.closest('td');
    const cell = td ? td.querySelector('.feedback-cell') : null;
    const body = document.getElementById('rowFeedbackModalBody');
    const modal = document.getElementById('rowFeedbackModal');
    if (!cell || !body || !modal) return;
    _rowFeedbackHomeTd = td;
    body.innerHTML = '';
    body.appendChild(cell);
    cell.style.display = '';
    cell.style.minWidth = '260px';
    modal.classList.remove('hide');
}

function closeRowFeedbackModal() {
    const body = document.getElementById('rowFeedbackModalBody');
    const cell = body ? body.querySelector('.feedback-cell') : null;
    if (cell && _rowFeedbackHomeTd) {
        _rowFeedbackHomeTd.appendChild(cell);
        cell.style.display = 'none';
    }
    _rowFeedbackHomeTd = null;
    closeModalById('rowFeedbackModal');
}

function _applyOptimisticLike(btn, likeVal) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    const likeBtn = cell.querySelector('.btn-like');
    const dislikeBtn = cell.querySelector('.btn-dislike');
    const statusEl = cell.querySelector('.feedback-status');
    // 记录回滚前状态
    cell.dataset.prevLike = cell.dataset.like || '';
    cell.dataset.like = String(likeVal);
    if (likeVal === 1) {
        if (likeBtn) likeBtn.classList.add('active');
        if (dislikeBtn) dislikeBtn.classList.remove('active');
        if (statusEl) statusEl.textContent = '已点赞';
    } else if (likeVal === -1) {
        if (dislikeBtn) dislikeBtn.classList.add('active');
        if (likeBtn) likeBtn.classList.remove('active');
        if (statusEl) statusEl.textContent = '已点踩';
    }
    _syncFeedbackBadge(cell);
}

function _revertOptimisticLike(cell) {
    if (!cell) return;
    const prev = cell.dataset.prevLike;
    const likeBtn = cell.querySelector('.btn-like');
    const dislikeBtn = cell.querySelector('.btn-dislike');
    const statusEl = cell.querySelector('.feedback-status');
    if (likeBtn) likeBtn.classList.remove('active');
    if (dislikeBtn) dislikeBtn.classList.remove('active');
    if (prev === '1' && likeBtn) likeBtn.classList.add('active');
    if (prev === '-1' && dislikeBtn) dislikeBtn.classList.add('active');
    cell.dataset.like = prev || '';
    if (statusEl) statusEl.textContent = prev ? (prev === '1' ? '已点赞' : '已点踩') : '';
    _syncFeedbackBadge(cell);
}

function _qualityWarningsLink(comment, likeVal) {
    if (likeVal !== -1) return;
    const text = String(comment || '');
    const triggers = ['模糊', '过暗', '遮挡', '算术', '金额', '单价', '数量', '错', '漏', '重'];
    const hit = triggers.some(k => text.includes(k));
    if (!hit && !text.trim()) return;
    const banner = document.getElementById('qualityWarningsBanner');
    if (!banner) return;
    const existing = [];
    try {
        banner.querySelectorAll('div').forEach(d => {
            const t = d.textContent || '';
            if (t && t.indexOf('•') === 0) existing.push(t.slice(2).trim());
        });
    } catch (e) {}
    const newWarning = text.trim() ? ('用户反馈：' + text.trim().slice(0, 80)) : '用户反馈：点踩';
    if (existing.indexOf(newWarning) !== -1) return;
    const merged = existing.concat([newWarning]);
    showQualityWarnings(merged);
}

function _postFeedback(receiptId, likeVal, comment, itemIndex) {
    const tenantId = _feedbackTenantId();
    return apiFetch('/api/receipt/' + Number(receiptId) + '/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-Id': tenantId },
        body: JSON.stringify({ like: likeVal, comment: comment || '', item_index: itemIndex, tenant_id: tenantId })
    }).then(r => Promise.all([r.status, r.json().catch(() => null)])).then(([s, j]) => ({ status: s, body: j }));
}

function handleArcFeedback(btn, likeVal) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    _applyOptimisticLike(btn, likeVal);
    const textarea = cell.querySelector('.feedback-comment');
    if (textarea && likeVal === -1) textarea.focus();
}

function submitArcFeedback(btn) {
    const cell = btn.closest('.feedback-cell');
    if (!cell) return;
    const rowIdx = cell.dataset.rowIndex != null ? Number(cell.dataset.rowIndex) : null;
    const textarea = cell.querySelector('.feedback-comment');
    const comment = textarea ? String(textarea.value || '').trim() : '';
    const likeVal = cell.dataset.like ? Number(cell.dataset.like) : null;
    const receiptId = currentArchiveReceiptId || (currentArchiveDetailData && currentArchiveDetailData.receipt_id);
    if (!receiptId) {
        showToast('归档单据 ID 缺失，无法提交反馈', 'warning');
        return;
    }
    if (likeVal == null && !comment) {
        showToast('请选择点赞或点踩，或填写反馈原因', 'warning');
        return;
    }
    const statusEl = cell.querySelector('.feedback-status');
    const prevStatus = statusEl ? statusEl.textContent : '';
    if (statusEl) statusEl.textContent = '提交中...';
    btn.disabled = true;
    _postFeedback(receiptId, likeVal, comment, rowIdx).then(({ status, body }) => {
        btn.disabled = false;
        if (status >= 200 && status < 300 && body && body.status === 'success') {
            if (statusEl) statusEl.textContent = likeVal === 1 ? '已点赞' : (likeVal === -1 ? '已点踩' : '已提交');
            showToast('归档反馈已提交', 'success');
            _qualityWarningsLink(comment, likeVal);
            if (body.distilled) {
                // P1-T5 发现性：此前只报「已记入队列」却不给入口，用户收到提示后无处可去
                // （gap 分析 M11）。行动点走 showToast 既有 action 机制：文案仍是文本节点，
                // 新标签打开队列页，不打断当前复核。
                showToast('已记入待确认记忆队列（连续3次纠偏触发，管理员确认后生效）', 'info', {
                    duration: TOAST_DURATION.long,
                    action: { label: '查看待确认记忆', onClick: openPendingMemoryQueue },
                });
            }
        } else {
            if (statusEl) statusEl.textContent = prevStatus || '提交失败';
            _revertOptimisticLike(cell);
            const msg = (body && body.msg) ? body.msg : ('HTTP ' + status);
            showToast('归档反馈提交失败：' + msg, 'error');
        }
    }).catch(err => {
        btn.disabled = false;
        if (statusEl) statusEl.textContent = prevStatus || '提交失败';
        _revertOptimisticLike(cell);
        showToast('归档反馈提交异常' + toastFailDetail(err), 'error');
    });
}

const FEEDBACK_COMMENT_MAXLEN_FRONTEND = 2000;   // 与后端 FEEDBACK_COMMENT_MAXLEN 对齐

function onFeedbackCommentInput(textarea) {
    // 即时提示：字数统计 + 接近上限告警；点踩态下联动 qualityWarnings 预览
    const cell = textarea.closest('.feedback-cell');
    if (!cell) return;
    const len = String(textarea.value || '').length;
    const charEl = cell.querySelector('.feedback-charcount');
    if (charEl) {
        const remain = FEEDBACK_COMMENT_MAXLEN_FRONTEND - len;
        charEl.textContent = `${len}/${FEEDBACK_COMMENT_MAXLEN_FRONTEND}`;
        if (remain <= 100) {
            charEl.style.color = '#c62828';
            charEl.textContent = `仅剩 ${remain} 字`;
        } else {
            charEl.style.color = '';
        }
    }
    const likeVal = cell.dataset.like ? Number(cell.dataset.like) : null;
    if (likeVal === -1 && len > 6) {
        _qualityWarningsLink(textarea.value, likeVal);
    }
}

// -------------------------------------------------------------
// 7. 提交人工复核结果保存入库
// -------------------------------------------------------------

// 读取 Tab1 复核表单当前内容，返回 prefill 风格数据对象
function collectReviewFormData() {
    const supplierName = document.getElementById('inpSupplier').value.trim();
    const dateStr = document.getElementById('inpDate').value.trim();
    const sheetName = document.getElementById('inpSheet').value.trim();
    const totalAmt = parseFloat(document.getElementById('inpTotal').value) || 0.00;

    const tbody = document.getElementById('itemTableBody');
    const rows = tbody.querySelectorAll('tr');
    registerNewUnitsFromRows(rows);

    const items = [];
    rows.forEach(tr => {
        // 采集侧兜底：不含 .inp-name 的行（空明细占位行 tr.warning-row）整行跳过，
        // 绝不进提交、也不抛错。旧实现对占位行无条件取 .inp-name.value → TypeError
        // 冒泡出 submitSaveEdited（不发请求、无提示）。
        const nameInput = tr.querySelector('.inp-name');
        if (!nameInput) return;
        // 行内取值一律走软取（缺控件按空串），避免个别畸形行中断整次保存
        const rowVal = (sel) => {
            const el = tr.querySelector(sel);
            return (el && el.value != null) ? String(el.value) : '';
        };
        const deptInput = tr.querySelector('.inp-dept');
        const actualQtyRaw = rowVal('.inp-actual-qty').trim();
        const item = {
            name: rowVal('.inp-name').trim(),
            quantity: parseFloat(rowVal('.inp-qty')) || 1.0,
            unit: rowVal('.inp-unit').trim(),
            unit_price: parseFloat(rowVal('.inp-price')) || 0.00,
            amount: parseFloat(rowVal('.inp-amount')) || 0.00,
            // Gap 6：手写实收数量（actual_qty），留空则按 quantity 计
            actual_qty: actualQtyRaw !== '' && !isNaN(parseFloat(actualQtyRaw)) ? parseFloat(actualQtyRaw) : null,
            // Wave 2（D44）：明细行成本中心——行内下拉实际显示值（未打部门 → null）
            cost_center_id: (deptInput && deptInput.value) ? Number(deptInput.value) : null,
            // D-P1-4：划线作废状态随行保存（后端算术门禁剔除 is_void 行）
            is_void: !!(tr.dataset && tr.dataset.isVoid === '1')
        };
        // P11/T5：item 级置信度（数值型才带）与单位换算警告（非空才带）随行透传。
        // 缺失时不携带键，后端兜底 0.5 / 空串语义不变（旧单据无这两列不会上浮）；
        // 渲染期由 appendTableRow 写入 dataset（见 tr.dataset.confidence / unitConversionWarning）。
        const rowConfRaw = tr.dataset ? tr.dataset.confidence : '';
        if (rowConfRaw !== '' && rowConfRaw != null && isFinite(Number(rowConfRaw))) {
            item.confidence = Number(rowConfRaw);
        }
        const rowUcwRaw = tr.dataset ? tr.dataset.unitConversionWarning : '';
        if (rowUcwRaw) item.unit_conversion_warning = String(rowUcwRaw);
        // D19：行内显式指定 SKU → 携带 sku_id；未指定（保持未关联）→ 缺省不传
        const skuIdInput = tr.querySelector('.inp-sku-id');
        const skuIdVal = skuIdInput && String(skuIdInput.value || '').trim();
        if (skuIdVal) item.sku_id = Number(skuIdVal);
        items.push(item);
    });

    const settlementSel = document.getElementById('inpSettlementType');
    const src = currentReceiptData || {};
    // Wave 2（D44）：单据级部门（一键填充目标）；未打部门 → null 可空保存
    const deptSel = document.getElementById('inpDepartmentId');
    const departmentId = (deptSel && deptSel.value) ? Number(deptSel.value) : null;
    // F-P1-3 多币种
    const curSel = document.getElementById('inpCurrency');
    const docFormSel = document.getElementById('inpDocForm');
    // 附加费用：动态费用行 → 六契约字段（删除行/未出现的类型 = 0，落库契约不变）
    const feeFields = collectFeeMap();

    const data = {
        supplier_name: supplierName || '通用供应商',
        date: dateStr,
        sheet_name: sheetName,
        total_amount: totalAmt,
        items: items,
        settlement_type: settlementSel ? (settlementSel.value || null) : null,
        department_id: departmentId,
        doc_form: (docFormSel && docFormSel.value ? docFormSel.value : (src.doc_form || 'printed_delivery_note')),
        currency: (curSel && curSel.value ? curSel.value : (src.currency || 'HKD')),
        discount_amount: feeFields.discount_amount,
        delivery_fee: feeFields.delivery_fee,
        deposit_amount: feeFields.deposit_amount,
        rounding_adjustment: feeFields.rounding_adjustment,
        // Gap 9：服务费(加一)/税额——店员可修正，随 save_edited 提交
        service_fee: feeFields.service_fee,
        tax_amount: feeFields.tax_amount,
        // Gap 6：手写注记——动态注记行 → list[str]（空行剔除，一行一条）
        adjustment_notes: collectNoteRows(),
        // G3/U2：付款证据由系统自动带出（无手输入口），采集读自动证据状态
        payment_evidence: getAutoPaymentEvidence(),
    };
    // U-05/用户反馈：付款标记为点击切换徽章，保存以徽章 state 为准；表单缺失时回落 AI 原值
    const inpMarkBadge = document.getElementById('inpPaymentMark');
    if (inpMarkBadge && inpMarkBadge.tagName === 'BUTTON') {
        data.payment_mark = getPaymentMarkBadgeState() ? '已付款' : '未付款';
    } else if (src.payment_mark != null) {
        data.payment_mark = src.payment_mark;
    }
    if (src.layout_type != null) data.layout_type = src.layout_type;
    // D17: 乐观锁版本号（加载/保存成功后记录）
    if (src.version != null) data.version = src.version;
    const boundId = (src && (src.id || src.receipt_id)) || currentReceiptId;
    if (boundId) {
        data.id = Number(boundId);
        data.receipt_id = Number(boundId);
    }
    return data;
}

// 由 prefill 风格数据构造 save_edited payload（单张复核/归档弹窗/批量全部保存共用）
function buildSavePayloadFromData(data, receiptId) {
    const d = data || {};
    const payload = {
        receipt_id: receiptId,
        source: d.source || 'manual',
        supplier_name: d.supplier_name || '通用供应商',
        date: d.date || '',
        sheet_name: d.sheet_name || '',
        total_amount: parseFloat(d.total_amount) || 0.00,
        items: (d.items || []).map(it => {
            const itemPayload = {
                name: it.name != null ? it.name : (it.raw_name || ''),
                quantity: parseFloat(it.quantity) || 1.0,
                unit: it.unit != null ? it.unit : (it.raw_unit || ''),
                unit_price: parseFloat(it.unit_price) || 0.00,
                amount: parseFloat(it.amount) || 0.00,
                // Gap 6：手写实收数量 actual_qty 可见
                actual_qty: (it.actual_qty != null && String(it.actual_qty).trim() !== '' && !isNaN(Number(it.actual_qty))) ? Number(it.actual_qty) : null,
                // Wave 2（契约⑦）：明细行成本中心——行内实际显示值；未打部门 → null
                cost_center_id: (it.cost_center_id != null && String(it.cost_center_id).trim() !== ''
                    && Number(it.cost_center_id) > 0) ? Number(it.cost_center_id) : null,
                // D-P1-4：划线作废透传（后端算术门禁剔除 is_void 行）
                is_void: !!(it.is_void)
            };
            // P11/T5：item 级置信度（数值型才带）与单位换算警告（非空才带）随行透传。
            // 自动保存（source=auto）此前不带这两列，落库被 /api/save_edited 的兜底值
            // （confidence 0.5 / unit_conversion_warning 空串）覆盖，低置信与花码提示丢失。
            // 条件跟 evidence/sku_id 一致：缺失不携带键 → 后端兜底语义与修复前完全一致。
            if (it.confidence != null && String(it.confidence).trim() !== ''
                && isFinite(Number(it.confidence))) {
                itemPayload.confidence = Number(it.confidence);
            }
            if (it.unit_conversion_warning != null
                && String(it.unit_conversion_warning).trim() !== '') {
                itemPayload.unit_conversion_warning = String(it.unit_conversion_warning);
            }
            // D19：行内显式指定的 sku_id 透传（未指定不携带，后端走安全精确匹配）
            if (it.sku_id != null && String(it.sku_id).trim() !== ''
                && Number(it.sku_id) > 0) {
                itemPayload.sku_id = Number(it.sku_id);
            }
            return itemPayload;
        }),
        // M3/D4: 结算方式必填——提交前已由 requireSettlementMarked 校验；
        // 此处仅保留 cash/credit，未知兜底 null（后端对非手工单 400 SETTLEMENT_REQUIRED）
        settlement_type: (d.settlement_type === 'cash' || d.settlement_type === 'credit') ? d.settlement_type : null,
        doc_form: d.doc_form || 'printed_delivery_note',
        currency: String(d.currency || 'HKD').toUpperCase(),
        discount_amount: parseFloat(d.discount_amount) || 0.00,
        delivery_fee: parseFloat(d.delivery_fee) || 0.00,
        deposit_amount: parseFloat(d.deposit_amount) || 0.00,
        rounding_adjustment: parseFloat(d.rounding_adjustment) || 0.00,
        // Gap 9 / Gap 6 / G3：新字段随 save_edited payload 透传（缺省取默认值，后端不 422）
        service_fee: parseFloat(d.service_fee) || 0.00,
        tax_amount: parseFloat(d.tax_amount) || 0.00,
        adjustment_notes: Array.isArray(d.adjustment_notes)
            ? d.adjustment_notes.map(s => String(s || '').trim()).filter(s => s !== '')
            : [],
        payment_evidence: String(d.payment_evidence || '').trim(),
    };
    // Wave 2（契约⑦）：单据级部门归属——未打部门可空保存（null 不覆盖旧值语义）
    if (d.department_id != null && String(d.department_id).trim() !== ''
        && Number(d.department_id) > 0) {
        payload.department_id = Number(d.department_id);
    } else {
        payload.department_id = null;
    }
    if (d.payment_mark != null) payload.payment_mark = d.payment_mark;
    if (d.layout_type != null) payload.layout_type = d.layout_type;
    // D17/W6 Q1：更新已有单据必须携带 version（缺失 → 后端 400 VERSION_REQUIRED）
    // 新建手工单（receiptId 为空）不要求 version
    if (receiptId != null && receiptId !== '') {
        if (d.version != null) payload.version = d.version;
    }
    return payload;
}

// 从保存成功响应中提取新版本号（容错：后端可能暂未返回）
function extractVersionFromResponse(ret) {
    if (!ret) return null;
    if (ret.version != null) return ret.version;
    if (ret.data && ret.data.version != null) return ret.data.version;
    return null;
}

// 结算方式必填化（PRD M3 / D4）：结算方式未标记（未知）→ 阻断保存并明确提示；
// 返回 true 表示已标记（可继续提交），false 表示未标记（应中断，不发请求）
function requireSettlementMarked(settlementType) {
    if (settlementType === 'cash' || settlementType === 'credit') return true;
    showToast('请选择结算方式（如现结或挂账）', 'error');
    return false;
}

// P3/R5：版本冲突重载保护——冲突时不再"提示后静默重载丢编辑"：
// 先把本地未保存编辑留副本（会话内 window.lastVersionConflictCopy + console），
// 再由用户显式确认是否重载最新内容。取消则保留当前画面编辑。
let lastVersionConflictCopy = null;
async function handleVersionConflictReload(receiptId, localData, reloadFn) {
    lastVersionConflictCopy = {
        receipt_id: receiptId,
        data: localData,
        at: new Date().toISOString(),
    };
    try {
        console.log('[VERSION_CONFLICT] 本地编辑副本已保留',
                    lastVersionConflictCopy);
    } catch (e) { /* console 不可用不影响主流程 */ }
    const ok = (typeof uiConfirm === 'function')
        ? await uiConfirm({
            title: '单据已被修改',
            message: '这张单刚刚被别人改过，不能直接覆盖。当前没保存的内容先留在这一页。重新加载会换成最新内容。',
            confirmText: '重新加载',
            cancelText: '留在本页',
        })
        : false;
    if (ok && typeof reloadFn === 'function') {
        reloadFn();
    }
}

// D34: 放弃上传/放弃解析——软删除未审核单据（移入回收站，可在管理后台恢复）并从侧栏/前端移除
async function discardCurrentReceipt() {
    const photo = typeof getActivePhoto === 'function' ? getActivePhoto() : null;
    const targetReceiptId = (photo && photo.receiptId) || currentReceiptId;
    const displayName = photo ? (photo.fileName || (photo.file ? photo.file.name : null) || `单据 #${targetReceiptId || ''}`) : (targetReceiptId ? `单据 #${targetReceiptId}` : '当前单据');

    if (!(await confirmDiscard(displayName))) {
        return;
    }

    const btnDiscard = document.getElementById('btnDiscardReceipt');
    if (btnDiscard) btnDiscard.disabled = true;

    try {
        if (targetReceiptId) {
            const res = await apiFetch(`/api/receipt/${targetReceiptId}/discard`, { method: 'POST' });
            const ret = await res.json().catch(() => null);
            if (!res.ok && res.status !== 404) {
                const errorMsg = '没能放弃这张单：' + TERMS.humanizeCode(ret && ret.code, ret && ret.msg);
                showToast(errorMsg, 'error');
                if (btnDiscard) btnDiscard.disabled = false;
                return;
            }
        }

        // 放弃成功，开始从前端列表移除
        const idx = photo ? BatchUploader.photos.indexOf(photo) : -1;
        if (idx >= 0) {
            if (photo.pollToken) photo.pollToken.cancelled = true;
            try { URL.revokeObjectURL(photo.objectUrl); } catch(e) {}
            BatchUploader.photos.splice(idx, 1);

            if (BatchUploader.photos.length === 0) {
                BatchUploader.activeIndex = 0;
                document.getElementById('splitViewArea').classList.add('hide');
                const btn = document.getElementById('btnSiderToggle');
                if (btn) btn.classList.add('hide');
                const drawer = document.getElementById('photoSider');
                if (drawer) drawer.classList.remove('open');
                BatchUploader.siderOpen = false;
                document.getElementById('prefillFormCard').classList.add('hide');
                document.getElementById('preConfirmCard').classList.add('hide');
                document.getElementById('loadingCard').classList.add('hide');
                document.getElementById('errorCard').classList.add('hide');
                currentReceiptId = null;
                currentReceiptData = null;
                clearManifest();
                resetManualEntryMode();   // D12：放弃手工单草稿 → 恢复左栏/标题
                syncFeedbackVisibility();
                syncRecoveryVisibility();
            } else {
                BatchUploader.activeIndex = Math.min(idx, BatchUploader.photos.length - 1);
                pruneUnselectableFromSelection();
                renderSider();
                updateSelectionUI();
                setActivePhoto(BatchUploader.activeIndex);
                persistManifest();
            }
        } else {
            // 单张照片场景
            document.getElementById('splitViewArea').classList.add('hide');
            document.getElementById('prefillFormCard').classList.add('hide');
            document.getElementById('preConfirmCard').classList.add('hide');
            document.getElementById('loadingCard').classList.add('hide');
            document.getElementById('errorCard').classList.add('hide');
            currentReceiptId = null;
            currentReceiptData = null;
            clearManifest();
            resetManualEntryMode();   // D12：放弃手工单草稿 → 恢复左栏/标题
            syncFeedbackVisibility();
            syncRecoveryVisibility();
        }

        announceDiscarded(targetReceiptId);
    } catch (err) {
        console.error('放弃单据异常:', err);
        showToast('网络不通，没能放弃这张单，请检查网络后再试', 'error');
    } finally {
        if (btnDiscard) btnDiscard.disabled = false;
    }
}
window.discardCurrentReceipt = discardCurrentReceipt;

// QA-P0-01: 复核表单未保存修改追踪（防脏数据审批入账）
let _reviewFormOriginalSnapshot = null;
let _reviewIsDirty = false;

function markReviewDirty() {
    _reviewIsDirty = true;
    const saveBadge = document.getElementById('reviewSaveStatusBadge');
    if (saveBadge && saveBadge.textContent !== '未保存') {
        saveBadge.textContent = '未保存';
        saveBadge.className = 'badge badge-secondary';
    }
}
window.markReviewDirty = markReviewDirty;

// AC-06-03: 依据单据权威状态重置保存状态徽标（清退「版本冲突 / 保存中 / 保存失败」
// 这类瞬时状态）。重载服务端最新版本成功、或重新打开一张干净单据时调用。
function syncReviewSaveBadgeFromStatus(status) {
    const saveBadge = document.getElementById('reviewSaveStatusBadge');
    if (!saveBadge) return;
    if (status === 'edited') {
        saveBadge.textContent = '已提交，待老板审批';
        saveBadge.className = 'badge badge-info';
    } else if (status === 'approved') {
        saveBadge.textContent = '已入账 (锁定)';
        saveBadge.className = 'badge badge-success';
    } else {
        saveBadge.textContent = '未保存';
        saveBadge.className = 'badge badge-secondary';
    }
}
window.syncReviewSaveBadgeFromStatus = syncReviewSaveBadgeFromStatus;

function isReviewFormDirty() {
    if (_reviewIsDirty) return true;
    if (_reviewFormOriginalSnapshot === null) return false;
    try {
        const cur = JSON.stringify(collectReviewFormData());
        return cur !== _reviewFormOriginalSnapshot;
    } catch (e) {
        return _reviewIsDirty;
    }
}
window.isReviewFormDirty = isReviewFormDirty;

// Q32: 单张保存互斥旗标——保存中禁止再次触发（防双击重复提交）
let isSavingReview = false;
let currentReceiptIssues = [];
let currentIssueFocusIndex = -1;
let currentWorkbenchQueueFilter = 'todo';
let cachedWorkbenchQueueData = [];

// AC-05-01: 统一问题模型与诊断算法（阿叔阿姨直白人话，零代码黑话）
function calculateReceiptIssues(data) {
    const issues = [];
    if (!data) return issues;

    // 1. 必填基本信息诊断
    const supplier = String(data.supplier_name || '').trim();
    if (!supplier || supplier === '通用供应商') {
        issues.push({
            id: 'err_supplier',
            field: 'inpSupplier',
            type: 'blocker',
            title: '缺少供应商名称',
            guide: '请填写供应商名称，以便正确记账并比对历史价格。'
        });
    }

    const dateStr = String(data.date || '').trim();
    if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
        issues.push({
            id: 'err_date',
            field: 'inpDate',
            type: 'blocker',
            title: '缺少开单日期',
            guide: '请选择或填写单据上的开单日期。'
        });
    }

    const totalAmt = Number(data.total_amount || 0);
    if (isNaN(totalAmt) || totalAmt <= 0) {
        issues.push({
            id: 'err_total',
            field: 'inpTotal',
            type: 'blocker',
            title: '总金额必须大于 0',
            guide: '请填写整单总金额，不能为 0 或负数。'
        });
    }

    // 2. 明细检查与行内算术守恒
    const items = (data.items || []).filter(it => !it.is_void);
    if (items.length === 0) {
        issues.push({
            id: 'err_empty_items',
            field: 'itemTableBody',
            type: 'blocker',
            title: '缺少食材明细行',
            guide: '请至少保留或添加一行有效的食材消费明细。'
        });
    }

    let itemsSum = 0;
    items.forEach((it, idx) => {
        const rowNum = idx + 1;
        const name = String(it.name || it.raw_name || '').trim();
        if (!name) {
            issues.push({
                id: `err_item_name_${idx}`,
                field: `item_row_${idx}_name`,
                type: 'blocker',
                rowIndex: idx,
                title: `第 ${rowNum} 行缺少食材品名`,
                guide: `第 ${rowNum} 行尚未填写品名，请填写或点击右侧操作删除该行。`
            });
        }

        const qty = Number(it.quantity);
        const price = Number(it.unit_price);
        const amount = Number(it.amount);

        if (isNaN(qty) || qty <= 0 || isNaN(price) || price < 0) {
            issues.push({
                id: `err_item_num_${idx}`,
                field: `item_row_${idx}_qty`,
                type: 'blocker',
                rowIndex: idx,
                title: `第 ${rowNum} 行单价或数量不是有效数字`,
                guide: `第 ${rowNum} 行「${name || '明细'}」单价或数量不正确，请重新输入。`
            });
        } else {
            // 行内算术一致性检查
            const calcAmt = Math.round(qty * price * 100) / 100;
            const diff = Math.abs(calcAmt - amount);
            if (diff > 0.05) {
                issues.push({
                    id: `err_item_math_${idx}`,
                    field: `item_row_${idx}_amount`,
                    type: 'warning',
                    rowIndex: idx,
                    title: `第 ${rowNum} 行算术有出入`,
                    guide: `第 ${rowNum} 行「${name || '明细'}」单价乘数量 ($${price} x ${qty} = $${calcAmt.toFixed(2)}) 与所填金额 ($${amount.toFixed(2)}) 相差 $${diff.toFixed(2)} 元。请核对单价或所填金额。`
                });
            }
        }

        if (it.unit_conversion_warning) {
            issues.push({
                id: `warn_item_unit_${idx}`,
                field: `item_row_${idx}_unit`,
                type: 'warning',
                rowIndex: idx,
                title: `第 ${rowNum} 行单位无标准折算`,
                guide: `第 ${rowNum} 行单位「${it.unit}」无标准折算关系，将保留原单位记录。`
            });
        }

        if (!isNaN(amount)) itemsSum += amount;
    });

    // 附加费用计入总额比对
    if (data.delivery_fee) itemsSum += Number(data.delivery_fee) || 0;
    if (data.service_fee) itemsSum += Number(data.service_fee) || 0;
    if (data.tax_amount) itemsSum += Number(data.tax_amount) || 0;
    if (data.deposit_amount) itemsSum += Number(data.deposit_amount) || 0;
    if (data.discount_amount) itemsSum -= Number(data.discount_amount) || 0;
    if (data.rounding_adjustment) itemsSum -= Number(data.rounding_adjustment) || 0;

    itemsSum = Math.round(itemsSum * 100) / 100;

    // 3. 整单算术守恒与差额指引 (AC-05-01, AC-05-04)
    if (totalAmt > 0 && items.length > 0) {
        const totalDiff = Math.abs(itemsSum - totalAmt);
        if (totalDiff > 0.05) {
            issues.push({
                id: 'err_grand_total_math',
                field: 'inpTotal',
                type: 'blocker',
                title: '整单总金额与明细加总不符',
                guide: `明细各项金额加总 ($${itemsSum.toFixed(2)}) 与整单总金额 ($${totalAmt.toFixed(2)}) 不符，相差 $${totalDiff.toFixed(2)} 元。请对照原图核对各行金额，或修改总金额。`
            });
        }
    }

    return issues;
}

// 刷新问题诊断摘要栏状态（AC-05-01 至 AC-05-03）
function refreshReceiptIssueSummary(data) {
    if (!data) {
        try { data = collectReviewFormData(); } catch (e) { data = currentReceiptData || {}; }
    }
    currentReceiptIssues = calculateReceiptIssues(data);
    currentIssueFocusIndex = -1;

    const bar = document.getElementById('issueSummaryBar');
    const bBadge = document.getElementById('issueBlockerCountBadge');
    const wBadge = document.getElementById('issueWarningCountBadge');
    const guideText = document.getElementById('issueSummaryGuideText');
    const valBadge = document.getElementById('aiValidationStatusBadge');

    const blockers = currentReceiptIssues.filter(i => i.type === 'blocker');
    const warnings = currentReceiptIssues.filter(i => i.type === 'warning');

    if (bBadge) bBadge.textContent = blockers.length > 0 ? `必须修改: ${blockers.length} 处` : '无必改问题';
    if (wBadge) wBadge.textContent = `核对提醒: ${warnings.length} 处`;

    if (currentReceiptIssues.length === 0) {
        if (bar) bar.classList.add('hide');
        if (valBadge) {
            valBadge.className = 'badge badge-success';
            valBadge.textContent = '金额与明细校验通过';
        }
    } else {
        if (bar) bar.classList.remove('hide');
        if (blockers.length > 0) {
            if (valBadge) {
                valBadge.className = 'badge badge-danger';
                valBadge.textContent = `必须先改 ${blockers.length} 处`;
            }
            if (guideText) guideText.textContent = blockers[0].guide;
        } else {
            if (valBadge) {
                valBadge.className = 'badge badge-warning';
                valBadge.textContent = `发现 ${warnings.length} 处需核对`;
            }
            if (guideText) guideText.textContent = warnings[0].guide;
        }
    }
}
window.refreshReceiptIssueSummary = refreshReceiptIssueSummary;

// AC-05-02: 逐项定位跳到下一处问题
function focusNextReceiptIssue() {
    if (!currentReceiptIssues || currentReceiptIssues.length === 0) {
        showToast('当前单据未发现算术或必填问题', 'success');
        return;
    }
    currentIssueFocusIndex = (currentIssueFocusIndex + 1) % currentReceiptIssues.length;
    const issue = currentReceiptIssues[currentIssueFocusIndex];

    const guideText = document.getElementById('issueSummaryGuideText');
    if (guideText) guideText.textContent = `[${currentIssueFocusIndex + 1}/${currentReceiptIssues.length}] ${issue.guide}`;

    let targetEl = null;
    if (issue.rowIndex != null) {
        const tbody = document.getElementById('itemTableBody');
        const row = tbody && tbody.querySelectorAll('tr')[issue.rowIndex];
        if (row) {
            targetEl = row.querySelector('.inp-amount') || row.querySelector('.inp-name') || row;
        }
    } else if (issue.field) {
        targetEl = document.getElementById(issue.field);
    }

    if (targetEl) {
        targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
        if (typeof targetEl.focus === 'function') targetEl.focus();
        targetEl.classList.remove('issue-focus-pulse');
        void targetEl.offsetWidth; // 触发 reflow 重置动画
        targetEl.classList.add('issue-focus-pulse');
        setTimeout(() => targetEl.classList.remove('issue-focus-pulse'), 1800);
    }
}
window.focusNextReceiptIssue = focusNextReceiptIssue;

// AC-05-06: 仅看问题行 / 显示全表切换
let isIssueOnlyFilterActive = false;
function toggleIssueOnlyRows() {
    const tbody = document.getElementById('itemTableBody');
    const btn = document.getElementById('btnToggleIssueFilter');
    if (!tbody) return;

    isIssueOnlyFilterActive = !isIssueOnlyFilterActive;
    const rows = tbody.querySelectorAll('tr');

    if (!isIssueOnlyFilterActive) {
        rows.forEach(r => r.classList.remove('row-hide-issue-filter'));
        if (btn) btn.textContent = '仅看问题行';
        showToast('已显示全部明细行', 'info');
        return;
    }

    const problemRowIndices = new Set(
        currentReceiptIssues.filter(i => i.rowIndex != null).map(i => i.rowIndex)
    );

    if (problemRowIndices.size === 0) {
        showToast('当前明细行无算术或字段错误', 'info');
        isIssueOnlyFilterActive = false;
        if (btn) btn.textContent = '仅看问题行';
        return;
    }

    rows.forEach((r, idx) => {
        if (problemRowIndices.has(idx)) {
            r.classList.remove('row-hide-issue-filter');
        } else {
            r.classList.add('row-hide-issue-filter');
        }
    });

    if (btn) btn.textContent = '显示全表';
    showToast(`仅展示存在问题的 ${problemRowIndices.size} 行明细`, 'info');
}
window.toggleIssueOnlyRows = toggleIssueOnlyRows;

// AC-06-01 至 AC-06-05: 保存复核表单（支持保存并下一张，多错内联聚合与乐观锁）
function submitSaveEdited(openNext = false) {
    if (isSavingReview) return; // AC-06-05: 严格互斥，防止连击重复提交

    // AC-08-05: 记录本次保存的触发元素，供 409 冲突弹窗关闭后归还焦点。
    // 保存开始后保存按钮会被 disabled（焦点落到 body），若不在此刻抓取，关闭
    // 冲突弹窗时 document.activeElement 已丢失，无法把焦点还给触发表达元素。
    const _saveTriggerEl = (document.activeElement && document.activeElement !== document.body)
        ? document.activeElement : (document.getElementById('btnSaveReview') || null);

    // 清除前序校验报错与残留提示 (AC-08-01 顶部提示不叠加)
    clearAllToasts(true);
    document.querySelectorAll('.is-invalid').forEach(el => el.classList.remove('is-invalid'));
    document.querySelectorAll('.field-error-msg').forEach(el => el.remove());

    let data;
    try {
        data = collectReviewFormData();
    } catch (err) {
        console.warn('[WARN] 复核表单内容读取异常，已中止本次保存:', err);
        showToast('保存未完成：请检查明细表格各输入框是否填写完整', 'error');
        return;
    }

    // AC-06-01: 多错内联聚合校验，一次性列全所有错误并首错聚焦
    const fieldErrors = [];
    function recordFieldError(elementId, msg) {
        const el = document.getElementById(elementId);
        if (el) {
            el.classList.add('is-invalid');
            const errDiv = document.createElement('div');
            errDiv.className = 'field-error-msg';
            errDiv.textContent = msg;
            if (el.parentNode) el.parentNode.appendChild(errDiv);
            fieldErrors.push({ el, msg });
        }
    }

    if (isManualEntry) {
        const manualErr = validateManualEntry(data);
        if (manualErr) {
            showToast(manualErr, 'error');
            return;
        }
    } else {
        const supplier = String((data && data.supplier_name) || '').trim();
        if (!supplier || supplier === '通用供应商') {
            recordFieldError('inpSupplier', '请填写供应商名称（不能留空或使用通用占位符）');
        }

        const dateStr = String((data && data.date) || '').trim();
        if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
            recordFieldError('inpDate', '请选择开单日期（格式 YYYY-MM-DD）');
        } else {
            const m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
            const y = Number(m[1]), mo = Number(m[2]), d = Number(m[3]);
            const dt = new Date(y, mo - 1, d);
            if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== d) {
                recordFieldError('inpDate', '开单日期不正确，请选择真实存在的日历日期');
            }
        }

        const totalAmt = Number((data && data.total_amount) || 0);
        if (isNaN(totalAmt) || totalAmt <= 0) {
            recordFieldError('inpTotal', '整单总金额必须大于 0');
        }

        if (!data.items || data.items.length === 0) {
            showToast('请至少输入一行消费明细', 'error');
            return;
        }

        const tbody = document.getElementById('itemTableBody');
        const rows = tbody ? tbody.querySelectorAll('tr') : [];
        for (let i = 0; i < data.items.length; i++) {
            const it = data.items[i];
            const name = String((it && (it.name || it.raw_name)) || '').trim();
            const rowEl = rows[i];
            if (!name && rowEl) {
                const nameInp = rowEl.querySelector('.inp-name');
                if (nameInp) {
                    nameInp.classList.add('is-invalid');
                    fieldErrors.push({ el: nameInp, msg: `第 ${i + 1} 行缺少品名` });
                }
            }
            const qty = Number(it && it.quantity);
            const price = Number(it && it.unit_price);
            if ((isNaN(qty) || qty <= 0 || isNaN(price) || price < 0) && rowEl) {
                const qtyInp = rowEl.querySelector('.inp-qty') || rowEl.querySelector('.inp-price');
                if (qtyInp) {
                    qtyInp.classList.add('is-invalid');
                    fieldErrors.push({ el: qtyInp, msg: `第 ${i + 1} 行数量或单价不正确` });
                }
            }
        }
    }

    if (fieldErrors.length > 0) {
        fieldErrors[0].el.focus();
        showToast(`发现 ${fieldErrors.length} 处必填项未通过，请检查标红字段`, 'error');
        return; // 零网络请求，本地阻断
    }

    // S3-05：界面写着「必须修改」的问题就真的拦住，并跳到出错的地方（手工单也一样）
    const _saveBlockers = calculateReceiptIssues(data).filter(i => i.type === 'blocker');
    if (_saveBlockers.length > 0) {
        currentReceiptIssues = calculateReceiptIssues(data);
        currentIssueFocusIndex = -1;
        showToast(`还有 ${_saveBlockers.length} 处必须先改的地方：${_saveBlockers[0].guide} 改好后再点「保存并提交审核」`, 'error', 7000);
        focusNextReceiptIssue();
        return;
    }

    if (!requireSettlementMarked(data.settlement_type)) {
        return;
    }

    const payload = buildSavePayloadFromData(data, currentReceiptId);
    payload.source = 'manual';

    isSavingReview = true;
    const saveBtn = document.getElementById('btnSaveReview');
    const saveNextBtn = document.getElementById('btnSaveAndNext');
    const saveStatusBadge = document.getElementById('reviewSaveStatusBadge');
    if (saveBtn) saveBtn.disabled = true;
    if (saveNextBtn) saveNextBtn.disabled = true;
    if (saveStatusBadge) {
        saveStatusBadge.textContent = '保存中...';
        saveStatusBadge.className = 'badge badge-warning';
    }

    apiFetch('/api/save_edited', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        // AC-06-03: 409 版本冲突对比弹窗
        if (httpStatus === 409 && ret && ret.code === 'VERSION_CONFLICT') {
            if (saveStatusBadge) {
                saveStatusBadge.textContent = '版本冲突';
                saveStatusBadge.className = 'badge badge-danger';
            }
            showConflictDiffModal(currentReceiptId, data, ret.server_data || null, _saveTriggerEl);
            return;
        }

        // 409 已入账保护
        if (httpStatus === 409 && ret && ret.code === 'CANNOT_MODIFY_APPROVED') {
            showToast('该单据已正式审核入账，不能直接修改；如需修正请联系管理员进行冲销', 'warning', 6000);
            if (saveStatusBadge) {
                saveStatusBadge.textContent = '已入账 (锁定)';
                saveStatusBadge.className = 'badge badge-success';
            }
            return;
        }

        if (httpStatus === 400 && ret && ret.code === 'VERSION_REQUIRED') {
            showToast('缺少版本号，正在重新拉取最新记录...', 'warning');
            if (currentReceiptId) refreshReviewFormFromServer(currentReceiptId);
            return;
        }

        if (toastHttpError(httpStatus, ret)) return;
        if (!ret || ret.status !== 'success') {
            showToast('保存失败：' + ((ret && ret.msg) || '请稍后重试'), 'error');
            if (saveStatusBadge) {
                saveStatusBadge.textContent = '保存失败';
                saveStatusBadge.className = 'badge badge-danger';
            }
            return;
        }

        // AC-01-01: 保存成功状态变为 edited，不产生库存增加
        resetQualityFailCount();
        const savedReceiptId = Number(ret.receipt_id || ret.id || currentReceiptId);
        if (savedReceiptId) {
            currentReceiptId = savedReceiptId;
        }
        const newVersion = extractVersionFromResponse(ret);
        if (currentReceiptData) {
            if (savedReceiptId) {
                currentReceiptData.id = savedReceiptId;
                currentReceiptData.receipt_id = savedReceiptId;
            }
            if (newVersion != null) currentReceiptData.version = newVersion;
        } else if (savedReceiptId) {
            currentReceiptData = { id: savedReceiptId, receipt_id: savedReceiptId, version: newVersion };
        }
        if (data) {
            if (savedReceiptId) {
                data.id = savedReceiptId;
                data.receipt_id = savedReceiptId;
            }
            if (newVersion != null) data.version = newVersion;
        }

        try {
            _reviewFormOriginalSnapshot = JSON.stringify(collectReviewFormData());
            _reviewIsDirty = false;
        } catch(e) {}

        if (saveStatusBadge) {
            saveStatusBadge.textContent = '已提交，待老板审批';
            saveStatusBadge.className = 'badge badge-info';
        }

        // 清除本地草稿缓存
        try { localStorage.removeItem(`receipt_draft_${currentReceiptId}`); } catch(e){}

        // 立即同步内存中的队列条目状态与表单属性
        if (Array.isArray(cachedWorkbenchQueueData)) {
            const cachedItem = cachedWorkbenchQueueData.find(r => String(r.id) === String(currentReceiptId));
            if (cachedItem) {
                cachedItem.status = 'edited';
                if (newVersion != null) cachedItem.version = newVersion;
                if (data.supplier_name) cachedItem.supplier_name = data.supplier_name;
                if (data.total_amount != null) cachedItem.total_amount = data.total_amount;
                if (data.date) cachedItem.receipt_date = data.date;
            }
        }
        updateWorkbenchQueueCounters();
        renderWorkbenchQueueTable();

        // AC-06-04: 若本次保存源自「保存后再切换」守卫，保存成功后完成切换
        if (_pendingWorkbenchSwitchTarget != null) {
            const _pendingTarget = _pendingWorkbenchSwitchTarget;
            _pendingWorkbenchSwitchTarget = null;
            if (Number(_pendingTarget) !== Number(savedReceiptId)) {
                openReceiptInWorkbench(_pendingTarget, true, true);
            }
        }

        // 刷新队列数据
        loadWorkbenchQueue(true);
        loadInventoryData();
        loadSuppliersData();

        const wasManual = isManualEntry;
        if (isManualEntry) {
            isManualEntry = false;
            currentReceiptId = Number(ret.receipt_id || ret.id || savedReceiptId);
            currentReceiptData = data;
            currentReceiptData.id = currentReceiptId;
            currentReceiptData.receipt_id = currentReceiptId;
        }

        // 保存成功后单据状态已是「待老板审批」，本地状态同步（审核入账按钮据此放行）
        if (currentReceiptData) currentReceiptData.status = 'edited';

        // AC-03-03: 保存并下一张
        if (openNext) {
            navigateNextOperableQueueItem(savedReceiptId, wasManual);
        } else {
            clearAllToasts(true);
            if (wasManual) {
                showToast('已保存手工录入单据 #' + ret.receipt_id + '，已提交审核', 'success');
            } else {
                showToast('已保存复核修改并提交审核（未入账）', 'success');
            }
        }
    })
    .catch(err => {
        console.error('保存单据异常:', err);
        // AC-06-02: 失败后输入与 dirty 完整保留，不自动刷新丢失
        showToast('保存请求因网络波动失败，您的修改已在本地保留，请重试保存', 'error', 6000);
        if (saveStatusBadge) {
            saveStatusBadge.textContent = '未保存 (网络错误)';
            saveStatusBadge.className = 'badge badge-danger';
        }
    })
    .finally(() => {
        isSavingReview = false;
        if (saveBtn) saveBtn.disabled = false;
        if (saveNextBtn) saveNextBtn.disabled = false;
    });
}
window.submitSaveEdited = submitSaveEdited;

// 冲突或重载场景：从服务端拉取最新详情并重渲染 Tab1 复核表单
function refreshReviewFormFromServer(receiptId, _retryCount) {
    if (!receiptId) return;
    resetManualEntryMode();   // D12：真实单据重载 → 退出新建手工单态
    apiFetch(`/api/receipt/${receiptId}`)
    .then(res => res.json())
    .then(ret => {
        if (ret.status !== 'success') {
            showToast('重新加载单据详情失败：' + (ret.msg || '请稍后重试') + '（可再次点「重新加载」重试）', 'error');
            return;
        }
        currentReceiptId = ret.receipt_id || receiptId;
        currentReceiptData = ret.data || {};
        renderEditForm(currentReceiptData);
        // AC-06-03: 重载成功后清退「版本冲突 / 保存失败」等瞬时徽标，回到权威状态
        syncReviewSaveBadgeFromStatus(currentReceiptData.status);

        const photo = getActivePhoto();
        if (photo && photo.receiptId === receiptId) {
            photo.data = currentReceiptData;
            if (photo.status === 'error') photo.status = 'parsed';
            renderSider();
        }
        showToast('已从服务器重新加载单据最新内容', 'info');
    })
    .catch(err => {
        console.error('重新加载单据详情请求失败:', err);
        // AC-06-03: 网络失败给出可见、可重试的恢复入口（而不是仅一闪而过的提示）
        showToast('重新加载失败，请检查网络后点「重新加载」重试', 'error', 8000);
    });
}

