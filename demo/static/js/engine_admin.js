// demo/static/js/engine_admin.js
// admin 引擎配置 / 灰测管理界面：分段视图 / 单腿探测 / 刊例载入 / 实时测算 /
// 脏状态与原子保存 / 生效态概览条 / 观测台 stale-response 守卫。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// admin：引擎配置 / 灰测管理界面
// -------------------------------------------------------------
// 清理历史残留已保存的 opencode / codebuddy 模型配置与白名单缓存
try {
    ['custom_models_opencode', 'custom_models_codebuddy',
     'verified_models_opencode', 'verified_models_codebuddy'].forEach(k => {
        localStorage.removeItem(k);
    });
} catch (e) {}

function toggleEngineDrawer(drawerId) {
    const drawer = document.getElementById(drawerId);
    if (!drawer) return;
    const isCollapsed = drawer.classList.contains('collapsed');
    if (isCollapsed) {
        drawer.classList.remove('collapsed');
    } else {
        drawer.classList.add('collapsed');
    }
    const header = drawer.querySelector('.engine-drawer-header');
    if (header) {
        header.setAttribute('aria-expanded', isCollapsed ? 'true' : 'false');
    }
}
window.toggleEngineDrawer = toggleEngineDrawer;

function toggleEngineCard(target) {
    const cardMap = {
        'rec': { cardId: 'adminRecognitionCard', bodyId: 'adminOpenaiRecognitionBox', leg: 'rec' },
        'aud': { cardId: 'adminAuditCard', bodyId: 'adminOpenaiAuditBox', leg: 'aud' },
        'parse': { cardId: 'adminParseDrawer', bodyId: 'adminParseOpenaiBox', leg: 'parse' },
        'adminRecognitionCard': { cardId: 'adminRecognitionCard', bodyId: 'adminOpenaiRecognitionBox', leg: 'rec' },
        'adminAuditCard': { cardId: 'adminAuditCard', bodyId: 'adminOpenaiAuditBox', leg: 'aud' },
        'adminParseDrawer': { cardId: 'adminParseDrawer', bodyId: 'adminParseOpenaiBox', leg: 'parse' }
    };
    const item = cardMap[target] || { cardId: target, bodyId: null, leg: target };
    const card = document.getElementById(item.cardId);
    if (!card) return;

    const body = item.bodyId ? document.getElementById(item.bodyId) : card.querySelector('#adminOpenaiRecognitionBox, #adminOpenaiAuditBox, #adminParseOpenaiBox');
    const isCurrentlyCollapsed = card.classList.contains('collapsed') || (body && body.style.display === 'none');
    const nextCollapsed = !isCurrentlyCollapsed;

    card.classList.toggle('collapsed', nextCollapsed);
    if (body) {
        body.style.display = nextCollapsed ? 'none' : 'block';
    }

    const leg = item.leg;
    const btns = card.querySelectorAll(`.engine-card-toggle-btn, #btnToggleCard_${leg}, #engineCardToggleBtn_${leg}`);
    btns.forEach(b => {
        b.textContent = nextCollapsed ? '展开' : '收起';
        b.setAttribute('aria-expanded', nextCollapsed ? 'false' : 'true');
    });

    const header = card.querySelector('.engine-card-header');
    if (header) {
        header.setAttribute('aria-expanded', nextCollapsed ? 'false' : 'true');
    }
}
window.toggleEngineCard = toggleEngineCard;

// ============================================================
// 引擎配置与治理重构 (Spec v2.0): 分段视图 / 单腿探测 / 刊例载入 / 实时测算 / 脏状态与原子保存
// ============================================================

function formatCleanPrice(val) {
    if (val === null || val === undefined || isNaN(val) || val === '') return '';
    const n = parseFloat(val);
    if (isNaN(n)) return '';
    const rounded = parseFloat(n.toFixed(4));
    return rounded.toString();
}
window.formatCleanPrice = formatCleanPrice;

function initEngineSubViewOnTabEnter() {
    const subFromUrl = (typeof getEngineSubViewFromUrl === 'function') ? getEngineSubViewFromUrl() : '01';
    if (typeof switchEngineSubView === 'function') {
        switchEngineSubView(subFromUrl, { scroll: false, updateHash: false });
    }
}
window.initEngineSubViewOnTabEnter = initEngineSubViewOnTabEnter;

function getEngineSubViewFromUrl() {
    try {
        const hash = (window.location.hash || '').toLowerCase();
        const search = (window.location.search || '').toLowerCase();
        if (hash.includes('view02') || hash.includes('subview_02') || hash.includes('traffic') || hash.includes('experiment') || hash.includes('grey') || search.includes('sub=02') || search.includes('view=view02') || search.includes('view=02')) {
            return '02';
        }
        if (hash.includes('view03') || hash.includes('subview_03') || hash.includes('governance') || hash.includes('flywheel') || hash.includes('rules') || search.includes('sub=03') || search.includes('view=view03') || search.includes('view=03')) {
            return '03';
        }
    } catch (e) {
        // ignore
    }
    return '01';
}
window.getEngineSubViewFromUrl = getEngineSubViewFromUrl;

function updateEngineErrorBadges() {
    ['01', '02', '03'].forEach(num => {
        const subview = document.getElementById('engineSubView_' + num);
        const badge = document.getElementById('engineTabErr_' + num);
        if (subview && badge) {
            const errs = subview.querySelectorAll('.input-error-highlight');
            if (errs.length > 0) {
                badge.textContent = String(errs.length);
                badge.classList.remove('hide');
            } else {
                badge.textContent = '';
                badge.classList.add('hide');
            }
        }
    });
}
window.updateEngineErrorBadges = updateEngineErrorBadges;

function switchEngineSubView(viewId, options = {}) {
    const opts = (typeof options === 'boolean') ? { updateHash: options } : (options || {});
    const shouldUpdateHash = opts.updateHash !== false;
    const shouldScroll = opts.scroll !== false;

    let sub = '01';
    const raw = String(viewId || '').toLowerCase().trim();
    if (raw === 'view02' || raw === '02' || raw === '2' || raw === 'traffic' || raw === 'experiment' || raw === 'experiments' || raw === 'grey') {
        sub = '02';
    } else if (raw === 'view03' || raw === '03' || raw === '3' || raw === 'governance' || raw === 'flywheel' || raw === 'rules') {
        sub = '03';
    } else {
        sub = '01';
    }

    // 1. 更新分段导航按钮高亮与 WAI-ARIA 状态
    ['01', '02', '03'].forEach(num => {
        const btn = document.getElementById('engineSubNav_' + num);
        if (btn) {
            const isTarget = (num === sub);
            btn.classList.toggle('active', isTarget);
            btn.setAttribute('aria-selected', isTarget ? 'true' : 'false');
        }
    });

    // 2. 严格互斥隐藏与展现子视图容器 (彻底废止伪切换)
    ['01', '02', '03'].forEach(num => {
        const target = document.getElementById('engineSubView_' + num);
        if (target) {
            if (num === sub) {
                target.classList.remove('d-none');
                target.style.display = 'block';
            } else {
                target.classList.add('d-none');
                target.style.display = 'none';
            }
        }
    });

    // 3. 视口重置平滑定位至子导航条顶部 (避免长内容滚屏断层)
    if (shouldScroll) {
        const navBar = document.getElementById('engineSubNav');
        if (navBar && typeof navBar.scrollIntoView === 'function') {
            const rect = navBar.getBoundingClientRect();
            if (rect.top < 0 || rect.top > 300) {
                navBar.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }
        }
    }

    // 4. 更新 URL Hash (免刷新)
    if (shouldUpdateHash && window.history && typeof window.history.replaceState === 'function') {
        try {
            const currentHash = window.location.hash || '';
            const newHash = '#view' + sub;
            if (currentHash !== newHash) {
                window.history.replaceState(null, '', newHash);
            }
        } catch (e) {
            // ignore
        }
    }
}
window.switchEngineSubView = switchEngineSubView;


function getVendorKeyPlaceholder(baseUrl) {
    if (!baseUrl) return 'sk-...';
    const u = String(baseUrl).toLowerCase();
    if (u.includes('gemini') || u.includes('generativelanguage.googleapis.com')) return 'AIzaSy...';
    if (u.includes('anthropic') || u.includes('claude')) return 'sk-ant-...';
    return 'sk-...';
}
window.getVendorKeyPlaceholder = getVendorKeyPlaceholder;

const LEG_INPUT_MAP = {
    'rec': {
        base_url: 'adminOpenaiRecBaseUrl',
        api_key: 'adminOpenaiRecApiKey',
        model: 'adminOpenaiRecModel',
        timeout: 'adminRecTimeoutSeconds',
        label: '常规识别引擎'
    },
    'aud': {
        base_url: 'adminOpenaiAudBaseUrl',
        api_key: 'adminOpenaiAudApiKey',
        model: 'adminOpenaiAudModel',
        timeout: 'adminAudTimeoutSeconds',
        label: '常规审核引擎'
    },
    'parse': {
        base_url: 'adminParseOpenaiBaseUrl',
        api_key: 'adminParseOpenaiApiKey',
        model: 'adminParseOpenaiModel',
        timeout: 'adminParseTimeoutSeconds',
        label: '常规解析引擎'
    },
    'grey_rec': {
        base_url: 'adminGreyOpenaiRecBaseUrl',
        api_key: 'adminGreyOpenaiRecApiKey',
        model: 'adminGreyOpenaiRecModel',
        timeout: 'adminGreyRecTimeoutSeconds',
        label: '灰测识别引擎'
    },
    'grey_aud': {
        base_url: 'adminGreyOpenaiAudBaseUrl',
        api_key: 'adminGreyOpenaiAudApiKey',
        model: 'adminGreyOpenaiAudModel',
        timeout: 'adminGreyAudTimeoutSeconds',
        label: '灰测审核引擎'
    },
    'grey_parse': {
        base_url: 'adminGreyParseOpenaiBaseUrl',
        api_key: 'adminGreyParseOpenaiApiKey',
        model: 'adminGreyParseOpenaiModel',
        timeout: 'adminGreyParseTimeoutSeconds',
        label: '灰测解析引擎'
    }
};

async function probeSingleEngineLeg(leg) {
    const info = LEG_INPUT_MAP[leg];
    if (!info) return;

    const btn = document.getElementById('btnProbe_' + leg);
    const statusTag = document.getElementById('probeStatus_' + leg);
    const origText = btn ? btn.textContent : '测试此引擎';

    const baseEl = document.getElementById(info.base_url);
    const keyEl = document.getElementById(info.api_key);
    const modelEl = document.getElementById(info.model);
    const timeoutEl = document.getElementById(info.timeout);

    const baseUrl = (baseEl ? baseEl.value : '').trim();
    const apiKey = (keyEl ? keyEl.value : '').trim();
    const model = (modelEl ? modelEl.value : '').trim();
    let timeout = parseFloat(timeoutEl ? timeoutEl.value : '');
    if (isNaN(timeout) || timeout <= 0) {
        timeout = 8.0;
    }

    if (!baseUrl) {
        showToast('请先输入 ' + info.label + ' 的 Base URL', 'warning');
        if (statusTag) {
            statusTag.className = 'probe-status-tag fail';
            statusTag.textContent = '缺少 Base URL';
        }
        if (baseEl) baseEl.focus();
        return;
    }
    if (!model) {
        showToast('请先输入 ' + info.label + ' 的模型名称', 'warning');
        if (statusTag) {
            statusTag.className = 'probe-status-tag fail';
            statusTag.textContent = '缺少模型名';
        }
        if (modelEl) modelEl.focus();
        return;
    }

    if (btn) {
        btn.disabled = true;
        btn.textContent = '测试中...';
    }
    if (statusTag) {
        statusTag.className = 'probe-status-tag';
        statusTag.textContent = '连接中...';
    }

    try {
        const res = await apiFetch('/api/admin/probe-single-engine', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                leg_type: leg,
                leg: leg,
                base_url: baseUrl,
                api_key: apiKey,
                model_name: model,
                model: model,
                timeout_seconds: timeout
            })
        });
        const ret = await res.json();
        if (ret && ret.status === 'success' && ret.data && ret.data.ok) {
            const ms = ret.data.latency_ms || 0;
            if (statusTag) {
                statusTag.className = 'probe-status-tag ok';
                statusTag.textContent = `连接正常 (${ms}ms)`;
            }
            showToast(`${info.label} 连接正常 (${ms}ms)`, 'success');
        } else {
            const err = (ret && ret.data && ret.data.error) || (ret && (ret.msg || ret.detail)) || '探测失败';
            if (statusTag) {
                statusTag.className = 'probe-status-tag fail';
                statusTag.textContent = `失败: ${err.slice(0, 30)}`;
            }
            showToast(`${info.label} 探测未通过: ${err}`, 'error');
        }
    } catch (e) {
        if (statusTag) {
            statusTag.className = 'probe-status-tag fail';
            statusTag.textContent = `网络异常`;
        }
        showToast(`${info.label} 探测请求失败: ${e.message || e}`, 'error');
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = origText;
        }
    }
}
window.probeSingleEngineLeg = probeSingleEngineLeg;

const LEG_PRICE_INPUTS = {
    'rec': {
        inId: 'adminPriceInputRec',
        cachedId: 'adminPriceCachedInputRec',
        outId: 'adminPriceOutputRec',
        isAud: false,
        label: '常规识别'
    },
    'aud': {
        inId: 'adminPriceInputAud',
        cachedId: 'adminPriceCachedInputAud',
        outId: 'adminPriceOutputAud',
        isAud: true,
        label: '常规审核'
    },
    'parse': {
        inId: 'adminPriceInputParse',
        cachedId: 'adminPriceCachedInputParse',
        outId: 'adminPriceOutputParse',
        isAud: false,
        label: '常规解析'
    },
    'grey_rec': {
        inId: 'adminGreyPriceInputRec',
        cachedId: 'adminGreyPriceCachedInputRec',
        outId: 'adminGreyPriceOutputRec',
        isAud: false,
        label: '灰测识别'
    },
    'grey_aud': {
        inId: 'adminGreyPriceInputAud',
        cachedId: 'adminGreyPriceCachedInputAud',
        outId: 'adminGreyPriceOutputAud',
        isAud: true,
        label: '灰测审核'
    },
    'grey_parse': {
        inId: 'adminGreyPriceInputParse',
        cachedId: 'adminGreyPriceCachedInputParse',
        outId: 'adminGreyPriceOutputParse',
        isAud: false,
        label: '灰测解析'
    }
};

const BENCHMARK_TOKENS = {
    standard: {
        inTokens: 2400,
        cachedTokens: 1600,
        outTokens: 800,
        label: '标准',
        formula: '公式: 2.4k非缓存输入 + 1.6k缓存输入 + 800输出'
    },
    complex: {
        inTokens: 4500,
        cachedTokens: 3000,
        outTokens: 1500,
        label: '全面',
        formula: '公式: 4.5k非缓存输入 + 3.0k缓存输入 + 1.5k输出'
    },
    audit_only: {
        inTokens: 1000,
        cachedTokens: 500,
        outTokens: 300,
        label: '纯文本',
        formula: '公式: 1.0k非缓存输入 + 0.5k缓存输入 + 300输出'
    }
};

function updateEngineCostCalculator(leg) {
    const meta = LEG_PRICE_INPUTS[leg];
    if (!meta) return;

    const inEl = document.getElementById(meta.inId);
    const cachedEl = document.getElementById(meta.cachedId);
    const outEl = document.getElementById(meta.outId);

    const priceIn = (inEl && inEl.value.trim() !== '') ? parseFloat(inEl.value) : 0;
    const priceCached = (cachedEl && cachedEl.value.trim() !== '') ? parseFloat(cachedEl.value) : (priceIn * 0.5);
    const priceOut = (outEl && outEl.value.trim() !== '') ? parseFloat(outEl.value) : 0;

    let selectedBench = 'standard';
    const radios = document.getElementsByName('bench_' + leg);
    for (let i = 0; i < radios.length; i++) {
        if (radios[i].checked) {
            selectedBench = radios[i].value;
            break;
        }
    }
    const bench = BENCHMARK_TOKENS[selectedBench] || BENCHMARK_TOKENS.standard;

    // 单据成本换算公式: (inTokens * priceIn + cachedTokens * priceCached + outTokens * priceOut) / 1,000,000
    const cost = (bench.inTokens * priceIn + bench.cachedTokens * priceCached + bench.outTokens * priceOut) / 1000000.0;

    const costSpan = document.getElementById('calcCost_' + leg);
    const breakdownSpan = document.getElementById('calcBreakdown_' + leg);

    if (costSpan) {
        costSpan.textContent = `约 ¥${parseFloat(cost.toFixed(4))} / 张发票`;
    }
    if (breakdownSpan) {
        breakdownSpan.textContent = bench.formula;
    }
}
window.updateEngineCostCalculator = updateEngineCostCalculator;

function updateAllCostCalculators() {
    ['rec', 'aud', 'parse', 'grey_rec', 'grey_aud', 'grey_parse'].forEach(leg => {
        updateEngineCostCalculator(leg);
    });
}
window.updateAllCostCalculators = updateAllCostCalculators;

let _engineFormOriginalSnapshot = null;
let _engineIsDirty = false;

function captureEngineFormSnapshot() {
    const container = document.getElementById('tab-engine');
    if (!container) return;
    const snapshot = {};
    const inputs = container.querySelectorAll('input, select, textarea');
    inputs.forEach(el => {
        if (!el.id) return;
        if (el.type === 'checkbox') {
            snapshot[el.id] = el.checked;
        } else if (el.type === 'radio') {
            if (el.checked) snapshot[el.name] = el.value;
        } else {
            snapshot[el.id] = el.value;
        }
    });
    _engineFormOriginalSnapshot = JSON.stringify(snapshot);
    _engineIsDirty = false;
    updateEngineStickyFooterStatus(0);
    if (typeof renderEngineOverview === 'function') {
        renderEngineOverview();
    }
}
window.captureEngineFormSnapshot = captureEngineFormSnapshot;

function checkEngineDirty() {
    if (!_engineFormOriginalSnapshot) return false;
    const container = document.getElementById('tab-engine');
    if (!container) return false;
    const current = {};
    let diffCount = 0;
    const inputs = container.querySelectorAll('input, select, textarea');
    inputs.forEach(el => {
        if (!el.id) return;
        if (el.type === 'checkbox') {
            current[el.id] = el.checked;
        } else if (el.type === 'radio') {
            if (el.checked) current[el.name] = el.value;
        } else {
            current[el.id] = el.value;
        }
    });

    const orig = JSON.parse(_engineFormOriginalSnapshot);
    for (const k in current) {
        if (current[k] !== orig[k]) {
            diffCount++;
        }
    }
    for (const k in orig) {
        if (!(k in current)) {
            diffCount++;
        }
    }

    _engineIsDirty = (diffCount > 0);
    updateEngineStickyFooterStatus(diffCount);
    if (typeof renderEngineOverview === 'function') {
        renderEngineOverview();
    }
    return _engineIsDirty;
}
window.checkEngineDirty = checkEngineDirty;

function updateEngineStickyFooterStatus(diffCount = 0) {
    const icon = document.getElementById('engineStickyStatusIcon');
    const text = document.getElementById('engineStickyStatusText');
    const discardBtn = document.getElementById('btnDiscardEngineChanges');

    if (_engineIsDirty) {
        if (icon) {
            icon.className = 'sticky-status-badge status-dirty';
            icon.innerHTML = '<span class="status-dot"></span>未保存';
        }
        if (text) {
            text.textContent = `存在未保存的修改 (${diffCount} 项变更)`;
        }
        if (discardBtn) {
            discardBtn.disabled = false;
            discardBtn.style.opacity = '1';
        }
    } else {
        if (icon) {
            icon.className = 'sticky-status-badge status-synced';
            icon.innerHTML = '<span class="status-dot"></span>已同步';
        }
        if (text) {
            text.textContent = '配置与服务器已同步';
        }
        if (discardBtn) {
            discardBtn.disabled = true;
            discardBtn.style.opacity = '0.6';
        }
    }
}

function discardEngineDraftChanges() {
    if (!_engineIsDirty) {
        showToast('当前没有未保存的修改', 'info');
        return;
    }
    showCustomConfirmModal({
        title: '放弃未保存的修改',
        message: '确认放弃所有未保存的修改并重新从服务器加载配置吗？\n\n放弃后，当前表单中尚未保存的修改将全部丢失。',
        confirmText: '确认放弃',
        cancelText: '取消',
        onConfirm: () => {
            _engineIsDirty = false;
            loadAdminEngineConfig();
            loadAdminSettings();
            showToast('已放弃本地修改，重新加载服务器配置', 'info');
        }
    });
}
window.discardEngineDraftChanges = discardEngineDraftChanges;

function validateAllAdminConfigs() {
    document.querySelectorAll('.input-error-highlight').forEach(el => el.classList.remove('input-error-highlight'));
    ['01', '02', '03'].forEach(num => {
        const badge = document.getElementById('engineTabErr_' + num);
        if (badge) {
            badge.textContent = '';
            badge.classList.add('hide');
        }
    });

    const errors = [];


    const recBaseEl = document.getElementById('adminOpenaiRecBaseUrl');
    if (recBaseEl && recBaseEl.value.trim()) {
        const u = recBaseEl.value.trim();
        if (!/^https?:\/\//i.test(u)) {
            errors.push({ view: '01', elementId: 'adminOpenaiRecBaseUrl', message: '常规识别 Base URL 必须以 http:// 或 https:// 开头' });
        }
    }

    const recPriceEl = document.getElementById('adminPriceInputRec');
    if (recPriceEl && recPriceEl.value.trim()) {
        const p = parseFloat(recPriceEl.value.trim());
        if (isNaN(p) || p < 0) {
            errors.push({ view: '01', elementId: 'adminPriceInputRec', message: '常规识别 Input 价格不能为负数' });
        }
    }

    const audEnabled = document.getElementById('adminAuditEnabled') && document.getElementById('adminAuditEnabled').value === 'true';
    if (audEnabled) {
        const audBaseEl = document.getElementById('adminOpenaiAudBaseUrl');
        if (audBaseEl && audBaseEl.value.trim() && !/^https?:\/\//i.test(audBaseEl.value.trim())) {
            errors.push({ view: '01', elementId: 'adminOpenaiAudBaseUrl', message: '常规审核 Base URL 必须以 http:// 或 https:// 开头' });
        }
    }

    const parseEnabled = document.getElementById('adminParseEnabled') && document.getElementById('adminParseEnabled').value === 'true';
    if (parseEnabled) {
        const parseBaseEl = document.getElementById('adminParseOpenaiBaseUrl');
        if (parseBaseEl && parseBaseEl.value.trim() && !/^https?:\/\//i.test(parseBaseEl.value.trim())) {
            errors.push({ view: '01', elementId: 'adminParseOpenaiBaseUrl', message: '常规解析 Base URL 必须以 http:// 或 https:// 开头' });
        }
    }

    // View 02
    const greyEnabled = document.getElementById('adminGreyEnabled') && document.getElementById('adminGreyEnabled').value === 'true';
    if (greyEnabled) {
        const greyPercentEl = document.getElementById('adminGreyPercent');
        if (greyPercentEl && greyPercentEl.value.trim()) {
            const p = parseInt(greyPercentEl.value.trim(), 10);
            if (isNaN(p) || p < 0 || p > 100) {
                errors.push({ view: '02', elementId: 'adminGreyPercent', message: '灰测流量分流比例必须在 0% 到 100% 之间' });
            }
        }
        const greyRecBase = document.getElementById('adminGreyOpenaiRecBaseUrl');
        if (greyRecBase && greyRecBase.value.trim() && !/^https?:\/\//i.test(greyRecBase.value.trim())) {
            errors.push({ view: '02', elementId: 'adminGreyOpenaiRecBaseUrl', message: '灰测识别 Base URL 必须以 http:// 或 https:// 开头' });
        }
    }

    // View 03
    if (typeof ADMIN_SETTINGS_FIELDS !== 'undefined') {
        ADMIN_SETTINGS_FIELDS.forEach(([id, key, type]) => {
            const el = document.getElementById(id);
            if (!el || !el.value.trim()) return;
            const n = Number(el.value.trim());
            if (!isFinite(n) || n < 0) {
                errors.push({ view: '03', elementId: id, message: `系统参数「${key}」必须为大于等于 0 的有效数值` });
            }
        });
    }

    if (errors.length > 0) {
        const counts = { '01': 0, '02': 0, '03': 0 };
        errors.forEach(e => {
            counts[e.view] = (counts[e.view] || 0) + 1;
            const el = document.getElementById(e.elementId);
            if (el) el.classList.add('input-error-highlight');
        });

        ['01', '02', '03'].forEach(num => {
            const badge = document.getElementById('engineTabErr_' + num);
            if (badge) {
                if (counts[num] > 0) {
                    badge.textContent = String(counts[num]);
                    badge.classList.remove('hide');
                } else {
                    badge.textContent = '';
                    badge.classList.add('hide');
                }
            }
        });

        const firstErr = errors[0];
        switchEngineSubView(firstErr.view, { scroll: false, updateHash: true });

        const firstEl = document.getElementById(firstErr.elementId);
        if (firstEl) {
            firstEl.focus();
            if (typeof firstEl.scrollIntoView === 'function') {
                firstEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
        }
        showToast(firstErr.message, 'error');
        return false;
    }

    return true;
}
window.validateAllAdminConfigs = validateAllAdminConfigs;

async function saveUnifiedAdminConfigAction() {
    const saveBtn = document.getElementById('btnSaveUnifiedConfig') || document.getElementById('adminSaveEngineBtn');
    const origText = saveBtn ? saveBtn.textContent : '保存全部配置';

    if (!validateAllAdminConfigs()) {
        return;
    }

    const engineConfig = collectEngineConfigBody();
    if (!engineConfig) return;

    const settings = _collectAdminSettingsPayload();
    if (settings === null) return;

    const payload = {
        version: (_engineConfigVersion != null ? _engineConfigVersion : undefined),
        engine_config: engineConfig,
        settings: settings
    };

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = '正在保存全部配置...';
    }

    try {
        const res = await apiFetch('/api/admin/unified-config', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const status = res.status;
        const ret = await res.json();

        if (status === 200 && ret && ret.status === 'success') {
            showToast('系统与引擎全部配置已统一原子保存', 'success');
            renderAdminEngineConfigSource('manual');
            if (ret.data && ret.data.version != null) {
                _engineConfigVersion = ret.data.version;
            }
            const eng = (ret && ret.data && ret.data.engine) || {};
            const keyMap = {
                adminOpenaiRecApiKey: eng.openai_rec_api_key,
                adminOpenaiAudApiKey: eng.openai_aud_api_key,
                adminParseOpenaiApiKey: eng.openai_parse_api_key,
                adminGreyOpenaiRecApiKey: eng.grey_openai_rec_api_key,
                adminGreyOpenaiAudApiKey: eng.grey_openai_aud_api_key,
                adminGreyParseOpenaiApiKey: eng.grey_openai_parse_api_key
            };
            Object.entries(keyMap).forEach(([id, val]) => {
                const el = document.getElementById(id);
                if (el) {
                    if (val !== undefined && val !== null) {
                        el.value = val;
                    }
                }
            });
            setEngineStatusMessage('配置已统一保存，生效版本 v' + (_engineConfigVersion != null ? _engineConfigVersion : '-'), 'success');
            loadEngineConfigRouting();
            loadEngineConfigHistory();

            captureEngineFormSnapshot();

            document.querySelectorAll('.input-error-highlight').forEach(el => el.classList.remove('input-error-highlight'));
            ['01', '02', '03'].forEach(num => {
                const badge = document.getElementById('engineTabErr_' + num);
                if (badge) badge.classList.add('hide');
            });
        } else if (status === 409 || (ret && ret.code === 'VERSION_CONFLICT')) {
            showEngineVersionConflictModal(ret);
        } else {
            const errMsg = (ret && (ret.msg || ret.detail)) || '统一配置保存失败';
            showToast(humanizeForbidden(errMsg) || errMsg, 'error');
        }
    } catch (err) {
        showToast('保存异常: ' + (err.message || err), 'error');
    } finally {
        if (saveBtn) {
            saveBtn.disabled = false;
            saveBtn.textContent = origText;
        }
    }
}
window.saveUnifiedAdminConfigAction = saveUnifiedAdminConfigAction;

let _adminEngineEventsBound = false;

function bindAdminEngineEventsOnce() {
    if (_adminEngineEventsBound) return;
    _adminEngineEventsBound = true;

    // 监听 #tab-engine 全局输入以追踪草稿脏状态与实时消除校验错误
    const engineTab = document.getElementById('tab-engine');
    if (engineTab && !engineTab._dirtyBound) {
        engineTab._dirtyBound = true;
        const handleEngineInput = (e) => {
            checkEngineDirty();
            if (e && e.target && e.target.classList && e.target.classList.contains('input-error-highlight')) {
                e.target.classList.remove('input-error-highlight');
                if (typeof updateEngineErrorBadges === 'function') {
                    updateEngineErrorBadges();
                }
            }
        };
        engineTab.addEventListener('input', handleEngineInput);
        engineTab.addEventListener('change', handleEngineInput);
    }

    // 审核开关控制置灰
    const auditEnabledEl = document.getElementById('adminAuditEnabled');
    if (auditEnabledEl) auditEnabledEl.addEventListener('change', updateAuditDisabledState);
    const greyAuditEnabledEl = document.getElementById('adminGreyAuditEnabled');
    if (greyAuditEnabledEl) greyAuditEnabledEl.addEventListener('change', updateGreyAuditDisabledState);

    // Base URL 变化时动态联动更新对应 API Key 占位符
    [
        ['adminOpenaiRecBaseUrl', 'adminOpenaiRecApiKey'],
        ['adminOpenaiAudBaseUrl', 'adminOpenaiAudApiKey'],
        ['adminParseOpenaiBaseUrl', 'adminParseOpenaiApiKey'],
        ['adminGreyOpenaiRecBaseUrl', 'adminGreyOpenaiRecApiKey'],
        ['adminGreyOpenaiAudBaseUrl', 'adminGreyOpenaiAudApiKey'],
        ['adminGreyParseOpenaiBaseUrl', 'adminGreyParseOpenaiApiKey']
    ].forEach(([baseId, keyId]) => {
        const bEl = document.getElementById(baseId);
        const kEl = document.getElementById(keyId);
        if (bEl && kEl) {
            const updatePh = () => {
                kEl.placeholder = getVendorKeyPlaceholder(bEl.value);
            };
            bEl.addEventListener('input', updatePh);
            bEl.addEventListener('change', updatePh);
        }
    });

    // 灰测停用 -> 整块置灰
    const greyEnabledEl = document.getElementById('adminGreyEnabled');
    if (greyEnabledEl) greyEnabledEl.addEventListener('change', updateGreyDisabledState);

    // 解析 LLM 开关 -> 置灰
    const parseEnabledEl = document.getElementById('adminParseEnabled');
    if (parseEnabledEl) parseEnabledEl.addEventListener('change', updateParseDisabledState);

    // 灰测解析 LLM 开关 -> 置灰
    const greyParseEnabledEl = document.getElementById('adminGreyParseEnabled');
    if (greyParseEnabledEl) greyParseEnabledEl.addEventListener('change', updateGreyParseDisabledState);

    // 抽屉无障碍键盘支持 (role="button", tabindex="0", keydown for Enter/Space)
    document.querySelectorAll('.engine-drawer').forEach((drawer) => {
        const header = drawer.querySelector('.engine-drawer-header');
        if (header) {
            header.setAttribute('role', 'button');
            header.setAttribute('tabindex', '0');
            const isCollapsed = drawer.classList.contains('collapsed');
            header.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
            header.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' || e.key === ' ' || e.key === 'Spacebar') {
                    e.preventDefault();
                    toggleEngineDrawer(drawer.id);
                }
            });
        }
    });
}

// ---------------------------------- 高级模型推理参数折叠与徽章 ----------------------------------
function getAdvInputPrefix(legId) {
    const map = {
        'rec': 'adminRec',
        'aud': 'adminAud',
        'parse': 'adminParse',
        'grey_rec': 'adminGreyRec',
        'grey_aud': 'adminGreyAud',
        'grey_parse': 'adminGreyParse'
    };
    return map[legId] || ('admin' + legId);
}

function toggleAdvParams(legId) {
    const drawer = document.getElementById('adminAdvParams_' + legId);
    const body = document.getElementById('adminAdvBody_' + legId);
    const toggleText = document.getElementById('adminAdvToggleText_' + legId);
    if (!drawer || !body) return;
    const isCollapsed = drawer.classList.contains('collapsed');
    if (isCollapsed) {
        drawer.classList.remove('collapsed');
        body.style.display = 'block';
        if (toggleText) toggleText.textContent = '收起';
    } else {
        drawer.classList.add('collapsed');
        body.style.display = 'none';
        if (toggleText) toggleText.textContent = '展开';
    }
}
window.toggleAdvParams = toggleAdvParams;

function getEngineLegMeta(legId) {
    if (typeof USER_ENGINE_LEG_MAP !== 'undefined' && USER_ENGINE_LEG_MAP[legId]) {
        return USER_ENGINE_LEG_MAP[legId];
    }
    const map = {
        'rec': { modelId: 'adminOpenaiRecModel', baseUrlId: 'adminOpenaiRecBaseUrl', reasoningEffortId: 'adminOpenaiRecReasoningEffort' },
        'aud': { modelId: 'adminOpenaiAudModel', baseUrlId: 'adminOpenaiAudBaseUrl', reasoningEffortId: 'adminOpenaiAudReasoningEffort' },
        'parse': { modelId: 'adminParseOpenaiModel', baseUrlId: 'adminParseOpenaiBaseUrl', reasoningEffortId: 'adminParseOpenaiReasoningEffort' },
        'grey_rec': { modelId: 'adminGreyOpenaiRecModel', baseUrlId: 'adminGreyOpenaiRecBaseUrl', reasoningEffortId: 'adminGreyOpenaiRecReasoningEffort' },
        'grey_aud': { modelId: 'adminGreyOpenaiAudModel', baseUrlId: 'adminGreyOpenaiAudBaseUrl', reasoningEffortId: 'adminGreyOpenaiAudReasoningEffort' },
        'grey_parse': { modelId: 'adminGreyParseOpenaiModel', baseUrlId: 'adminGreyParseOpenaiBaseUrl', reasoningEffortId: 'adminGreyParseOpenaiReasoningEffort' }
    };
    return map[legId] || null;
}

function updateAdvBadge(legId) {
    const pfx = getAdvInputPrefix(legId);
    const badge = document.getElementById('adminAdvBadge_' + legId);
    if (!badge) return;

    // 1. 思考强度
    const effortLabelMap = { '': '默认', 'low': 'Low', 'medium': 'Medium', 'high': 'High', 'xhigh': 'X-High', 'max': 'Max' };
    let effortLabel = '默认';
    const legMeta = getEngineLegMeta(legId);
    if (legMeta && legMeta.reasoningEffortId) {
        const effortEl = document.getElementById(legMeta.reasoningEffortId);
        if (effortEl) {
            const effortVal = effortEl.value.trim();
            effortLabel = effortLabelMap[effortVal] !== undefined ? effortLabelMap[effortVal] : (effortVal || '默认');
        }
    }

    // 2. 最大 Token 数
    const tokensEl = document.getElementById(pfx + 'MaxTokens');
    const defaultTokens = legId.includes('parse') ? '2048' : '4096';
    const tokens = (tokensEl && tokensEl.value.trim()) ? tokensEl.value.trim() : defaultTokens;

    // 3. 采样温度
    const tempEl = document.getElementById(pfx + 'Temperature');
    let temp = (tempEl && tempEl.value.trim() !== '') ? parseFloat(tempEl.value) : 0.0;
    if (isNaN(temp)) temp = 0.0;
    const tempStr = temp.toFixed(temp % 1 === 0 ? 1 : 2);

    // 4. 视觉切片粒度
    const detailMap = { high: '高精度', auto: '自适应', low: '粗粒度' };
    const detailEl = document.getElementById(pfx + 'ImageDetail');
    const defaultDetail = (legId === 'rec' || legId === 'grey_rec') ? 'high' : 'auto';
    const detailVal = (detailEl && detailEl.value) ? detailEl.value : defaultDetail;
    const detailLabel = detailMap[detailVal] || detailVal;

    // 统一按照「思考强度 -> 最大 Token 数 -> 视觉切片粒度 -> 采样温度」顺序与冒号前缀格式展示
    badge.textContent = `思考强度:${effortLabel} · 最大Token:${tokens} · 视觉切片:${detailLabel} · 采样温度:${tempStr}`;
}
window.updateAdvBadge = updateAdvBadge;

function updateModelTraitHints(legId) {
    const meta = getEngineLegMeta(legId);
    const modelEl = meta ? document.getElementById(meta.modelId) : null;
    const baseUrlEl = meta ? document.getElementById(meta.baseUrlId) : null;

    const m = (modelEl && modelEl.value) ? modelEl.value.trim().toLowerCase() : '';
    const u = (baseUrlEl && baseUrlEl.value) ? baseUrlEl.value.trim().toLowerCase() : '';

    const isDeepSeek = u.includes('api.deepseek.com') || (m.includes('deepseek') && !u.includes('aliyun') && !u.includes('dashscope') && !u.includes('volces') && !u.includes('siliconflow') && !u.includes('openrouter'));
    const isOReasoning = m.startsWith('o1') || m.startsWith('o3') || m.includes('-o1-') || m.includes('-o3-') || m.includes('/o1') || m.includes('/o3');

    const boxEl = document.getElementById('adminAdvModelHint_' + legId);
    const pfx = getAdvInputPrefix(legId);
    const tokensHint = document.getElementById(pfx + 'TokensHint');
    const reasoningHint = document.getElementById(pfx + 'ReasoningHint');
    const tempHint = document.getElementById(pfx + 'TempHint');

    if (isDeepSeek) {
        if (boxEl) {
            boxEl.style.display = 'block';
            boxEl.textContent = '检测到 DeepSeek 模型：系统将自动钳制 Max Tokens <= 8192 并静默剥离 reasoning_effort。';
        }
        if (tokensHint) {
            tokensHint.style.display = 'block';
            tokensHint.textContent = 'DeepSeek 官方 API 最大支持 8192 Tokens（超过将自动钳制为 8192）';
        }
        if (reasoningHint) {
            reasoningHint.style.display = 'block';
            reasoningHint.textContent = 'DeepSeek 官方 API 无需传思考强度参数；直连官方网关时建议保持「默认」';
        }
        if (tempHint) {
            tempHint.style.display = 'none';
            tempHint.textContent = '';
        }
    } else if (isOReasoning) {
        if (boxEl) {
            boxEl.style.display = 'block';
            boxEl.textContent = '检测到 OpenAI o1/o3 推理模型：系统将自动桥接 max_completion_tokens 并剥离采样温度。';
        }
        if (tokensHint) {
            tokensHint.style.display = 'block';
            tokensHint.textContent = '系统将自动为您桥接 max_completion_tokens 规范';
        }
        if (reasoningHint) {
            reasoningHint.style.display = 'none';
            reasoningHint.textContent = '';
        }
        if (tempHint) {
            tempHint.style.display = 'block';
            tempHint.textContent = 'OpenAI o1/o3 推理模型由服务商统一管理采样温度，系统将自动适配';
        }
    } else {
        if (boxEl) {
            boxEl.style.display = 'none';
            boxEl.textContent = '';
        }
        if (tokensHint) {
            tokensHint.style.display = 'none';
            tokensHint.textContent = '';
        }
        if (reasoningHint) {
            reasoningHint.style.display = 'none';
            reasoningHint.textContent = '';
        }
        if (tempHint) {
            tempHint.style.display = 'none';
            tempHint.textContent = '';
        }
    }
}
window.updateModelTraitHints = updateModelTraitHints;

function checkTempWarn(legId) {
    const pfx = getAdvInputPrefix(legId);
    const tempEl = document.getElementById(pfx + 'Temperature');
    const warnEl = document.getElementById(pfx + 'TempWarn');
    if (!tempEl || !warnEl) return;
    const val = parseFloat(tempEl.value);
    if (!isNaN(val) && val > 0.2) {
        warnEl.classList.remove('hide');
    } else {
        warnEl.classList.add('hide');
    }
}
window.checkTempWarn = checkTempWarn;

function bindAdvParamListeners() {
    const legs = ['rec', 'aud', 'parse', 'grey_rec', 'grey_aud', 'grey_parse'];
    legs.forEach(legId => {
        const pfx = getAdvInputPrefix(legId);
        const meta = getEngineLegMeta(legId);
        const inputs = [
            document.getElementById(pfx + 'MaxTokens'),
            document.getElementById(pfx + 'ImageDetail'),
            document.getElementById(pfx + 'Temperature'),
            meta && meta.reasoningEffortId ? document.getElementById(meta.reasoningEffortId) : null
        ];
        inputs.forEach(el => {
            if (!el || el.dataset.advBound) return;
            el.dataset.advBound = '1';
            const handler = () => {
                updateAdvBadge(legId);
                checkTempWarn(legId);
            };
            el.addEventListener('input', handler);
            el.addEventListener('change', handler);
        });

        if (meta) {
            const modelInputs = [
                document.getElementById(meta.modelId),
                document.getElementById(meta.baseUrlId)
            ];
            modelInputs.forEach(el => {
                if (!el || el.dataset.modelTraitBound) return;
                el.dataset.modelTraitBound = '1';
                const handler = () => {
                    updateModelTraitHints(legId);
                };
                el.addEventListener('input', handler);
                el.addEventListener('change', handler);
            });
        }
    });
}

// ============================================================
// 生效态概览条（顶部常驻）：version + 三腿生效模型 + 来源 + 最近变更
// ============================================================
// 引擎配置版本（来自 routing DTO），保存时随 body 回传用于乐观锁
let _engineConfigVersion = null;
let _engineOverviewState = { routing: null, latest: null, message: null };
window._engineOverviewState = _engineOverviewState;

let _engineOverviewExpanded = (() => {
    try { return localStorage.getItem('engine_overview_expanded') === '1'; } catch (e) { return false; }
})();
let _adminSettingsState = null;
window._adminSettingsState = _adminSettingsState;

function _formatEngineTime(v) {
    if (!v) return '-';
    try {
        const d = new Date(v);
        if (isNaN(d.getTime())) return String(v);
        const p = (n) => String(n).padStart(2, '0');
        return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
    } catch (e) {
        return String(v);
    }
}

const ENGINE_HISTORY_ACTION_LABELS = {
    save: '保存',
    unified_save: '统一保存',
    promote: '推全',
    rollback: '回滚',
    reset: '恢复默认'
};

// 切换概览全景拓扑展开/折叠状态
function toggleEngineTopologyInspector() {
    _engineOverviewExpanded = !_engineOverviewExpanded;
    try {
        localStorage.setItem('engine_overview_expanded', _engineOverviewExpanded ? '1' : '0');
    } catch (e) {}
    const btn = document.getElementById('btnToggleEngineTopology');
    if (btn) {
        btn.textContent = _engineOverviewExpanded ? '收起拓扑' : '展开拓扑';
    }
    const inspector = document.getElementById('engineOverviewInspector');
    if (inspector) {
        inspector.style.display = _engineOverviewExpanded ? 'flex' : 'none';
    } else {
        renderEngineOverview();
    }
}
window.toggleEngineTopologyInspector = toggleEngineTopologyInspector;

// 拓扑节点穿透下钻跳转
function jumpToEngineSection(viewId, targetElementId) {
    if (typeof switchEngineSubView === 'function') {
        switchEngineSubView(viewId, { scroll: false, updateHash: true });
    }
    setTimeout(() => {
        if (!targetElementId) return;
        const target = document.getElementById(targetElementId);
        if (!target) return;

        // 若目标处于折叠状态，则主动展开
        if (target.classList.contains('collapsed')) {
            if (typeof toggleEngineCard === 'function' && (targetElementId === 'adminRecognitionCard' || targetElementId === 'adminAuditCard' || targetElementId === 'adminParseDrawer')) {
                toggleEngineCard(targetElementId);
            } else if (typeof toggleEngineDrawer === 'function') {
                toggleEngineDrawer(targetElementId);
            } else {
                target.classList.remove('collapsed');
            }
        }

        if (typeof target.scrollIntoView === 'function') {
            target.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
        target.classList.add('flash-highlight');
        setTimeout(() => {
            target.classList.remove('flash-highlight');
        }, 1600);
    }, 150);
}
window.jumpToEngineSection = jumpToEngineSection;

// 概览条与全景拓扑渲染：状态消息 + 路由决策层 + 串行执行流水线（写入 #adminEngineStatus）
function renderEngineOverview() {
    const el = document.getElementById('adminEngineStatus');
    if (!el) return;

    const isExpanded = (typeof window !== 'undefined' && typeof window._engineOverviewExpanded === 'boolean')
        ? window._engineOverviewExpanded
        : _engineOverviewExpanded;

    // 同步切换按钮文字与状态
    const toggleBtn = document.getElementById('btnToggleEngineTopology');
    if (toggleBtn) {
        toggleBtn.textContent = isExpanded ? '[收起全景拓扑]' : '[展开全景拓扑]';
    }

    const parts = [];
    const overviewState = (typeof window !== 'undefined' && window._engineOverviewState) || _engineOverviewState || {};
    const msg = overviewState.message;
    if (msg) {
        parts.push(`<div class="engine-status-msg ${msg.kind || 'info'}">${escapeHtml(msg.text)}</div>`);
    }

    const r = overviewState.routing;
    if (!r) {
        parts.push('<div class="engine-overview-grid"><span>正在加载生效态与运行拓扑...</span></div>');
        el.innerHTML = parts.join('');
        return;
    }

    const version = r.version || _engineConfigVersion || 1;
    const configSource = String(r.config_source || (r.module_d && r.module_d.config_source) || 'auto');
    const sourceLabel = configSource === 'manual' ? '管理台手工配置' : (configSource === 'preset' ? '模版预装配置' : '.env 默认装配');

    const modA = r.module_a || {};
    const regularLegs = modA.legs || {};
    const recLeg = regularLegs.rec || {};
    const audLeg = regularLegs.aud || {};
    const parseLeg = regularLegs.parse || {};
    const auditEnabled = !!modA.audit_enabled;
    const parseLlmEnabled = !!modA.parse_llm_enabled;

    const modC = r.module_c || {};
    const greyLegs = modC.legs || {};
    const greyRecLeg = greyLegs.grey_rec || {};
    const greyAudLeg = greyLegs.grey_aud || {};
    const greyParseLeg = greyLegs.grey_parse || {};
    const greyEnabled = !!modC.grey_enabled;
    const greyPercent = Number(modC.grey_percent || 0);
    const greyAssignMode = String(modC.grey_assign_mode || '');
    const greyModeLabel = greyAssignMode === 'supplier_hash' ? '供应商哈希' : (greyAssignMode === 'tenant_whitelist' ? '租户白名单' : '单据随机');

    const experiments = (typeof window !== 'undefined' && Array.isArray(window._adminExperimentsList))
        ? window._adminExperimentsList
        : (Array.isArray(_adminExperimentsList) ? _adminExperimentsList : []);
    const runningExp = experiments.find(e => String(e.status) === 'running');

    const settings = (typeof window !== 'undefined' && window._adminSettingsState) || _adminSettingsState || {};
    const blurVal = (settings.blur_laplacian_threshold !== undefined && settings.blur_laplacian_threshold !== null) ? Number(settings.blur_laplacian_threshold) : 30.0;
    const ragBudget = (settings.memory_budget_facts_tokens !== undefined && settings.memory_budget_facts_tokens !== null) ? Number(settings.memory_budget_facts_tokens) : 800;
    const memEnabled = settings.memory_enabled !== undefined ? !!settings.memory_enabled : true;
    const vendorPrecheck = !!settings.vendor_precheck_enabled;
    const evalLowConf = (settings.eval_candidate_low_confidence !== undefined && settings.eval_candidate_low_confidence !== null) ? Number(settings.eval_candidate_low_confidence) : 0.6;
    const feedbackDistill = (settings.feedback_distill_threshold !== undefined && settings.feedback_distill_threshold !== null) ? Number(settings.feedback_distill_threshold) : 3;

    // 1. 流量路由仲裁层判定 (P0 -> P1 -> P2)
    let p0BadgeClass = 'badge-standby';
    let p0BadgeText = '[就绪备用: 无运行实验]';
    let p0Desc = '当前无运行实验 (独占接管就绪)';
    let p0Active = false;
    if (runningExp) {
        p0Active = true;
        p0BadgeClass = 'badge-takeover';
        p0BadgeText = `[独占接管: 实验 #${runningExp.id}]`;
        const expTarget = runningExp.target_percent || 50;
        p0Desc = `实验 #${runningExp.id}「${escapeHtml(runningExp.name || '未命名')}」独占接管，挂起灰测 (对照 ${100 - expTarget}% / 实验 ${expTarget}%)`;
    }

    let p1BadgeClass = 'badge-muted';
    let p1BadgeText = '[未启用]';
    let p1Desc = '灰度发布未启用 (0% 流量)';
    let p1Active = false;
    if (p0Active && greyEnabled) {
        p1BadgeClass = 'badge-standby';
        p1BadgeText = '[就绪备用: 实验独占挂起]';
        p1Desc = `灰度已配置 (${greyPercent}%) 但被 A/B 实验独占暂停分流`;
    } else if (!p0Active && greyEnabled && greyPercent > 0) {
        p1Active = true;
        p1BadgeClass = 'badge-active';
        p1BadgeText = `[已启用: 灰度分流 ${greyPercent}%]`;
        p1Desc = `${greyPercent}% 候选灰度 (${greyModeLabel}) -&gt; 灰测模型: 识别: [${escapeHtml(greyRecLeg.model || '未配')}] | 审核: [${escapeHtml(greyAudLeg.model || '未配')}]`;
    } else if (!p0Active && greyEnabled) {
        p1BadgeClass = 'badge-standby';
        p1BadgeText = '[就绪备用: 灰度放量 0%]';
        p1Desc = '灰度已开启但放量比例为 0% (就绪备用)';
    }

    let p2BadgeClass = 'badge-active';
    let p2BadgeText = '[已启用: 生产基线 100%]';
    let p2Desc = `100% 生产基线流量全量承载 -&gt; 识别: [${escapeHtml(recLeg.model || '未配')}] | 审核: [${escapeHtml(audLeg.model || '未配')}]`;
    if (p0Active) {
        p2BadgeClass = 'badge-standby';
        p2BadgeText = '[就绪备用: 受实验控制]';
        p2Desc = '流量分配完全受 A/B 实验分组规则托管';
    } else if (p1Active) {
        p2BadgeClass = 'badge-active';
        p2BadgeText = `[已启用: 基线兜底 ${100 - greyPercent}%]`;
        p2Desc = `${100 - greyPercent}% 基线兜底流量 -&gt; 生产模型: 识别: [${escapeHtml(recLeg.model || '未配')}] | 审核: [${escapeHtml(audLeg.model || '未配')}]`;
    }

    let routingSummary = '生产基线 100%';
    let routingSummaryBadge = 'badge-active';
    if (p0Active) {
        routingSummary = `实验接管 (${runningExp.target_percent || 50}%)`;
        routingSummaryBadge = 'badge-takeover';
    } else if (p1Active) {
        routingSummary = `灰度分流 ${greyPercent}% (${greyModeLabel})`;
        routingSummaryBadge = 'badge-active';
    }

    // 2. 串行流水线阶段判定 (S0 ~ S5)
    const s0Active = blurVal > 0;
    const s0BadgeClass = s0Active ? 'badge-active' : 'badge-muted';
    const s0BadgeText = s0Active ? '[已启用]' : '[未启用]';
    const s0Summary = s0Active ? `模糊度: ${blurVal}` : '质检已关闭';
    const s0Short = s0Active ? `[阈值 ${blurVal}]` : '[未启用]';

    const s1Active = memEnabled;
    const s1BadgeClass = s1Active ? 'badge-active' : 'badge-muted';
    const s1BadgeText = s1Active ? '[已启用]' : '[未启用]';
    const s1Summary = s1Active ? `预算: ${ragBudget} 词元${vendorPrecheck ? ' (预判开)' : ''}` : '记忆已停用';
    const s1Short = s1Active ? `[${ragBudget} 词元]` : '[未启用]';

    let s2ModelName = recLeg.model || '未配置';
    if (p0Active) {
        s2ModelName = 'A/B 实验动态';
    } else if (p1Active) {
        s2ModelName = `${recLeg.model || '基线'} / ${greyRecLeg.model || '灰测'}`;
    }
    const s2BadgeClass = 'badge-active';
    const s2BadgeText = '[已启用]';
    const s2Summary = `抽取: ${s2ModelName}`;
    const s2Short = `[${escapeHtml(recLeg.model || '视觉识别')}]`;

    const s3Active = parseLlmEnabled;
    const s3BadgeClass = s3Active ? 'badge-bypass' : 'badge-muted';
    const s3BadgeText = s3Active ? '[按需自愈]' : '[未启用]';
    const s3Summary = s3Active ? `按需自愈 (${escapeHtml(parseLeg.model || '未配置')})` : '规则硬解析 (直通)';
    const s3Short = s3Active ? '[按需自愈]' : '[未启用]';

    const s4Active = auditEnabled;
    const s4BadgeClass = s4Active ? 'badge-active' : 'badge-muted';
    const s4BadgeText = s4Active ? '[已启用]' : '[未启用]';
    const s4Summary = s4Active ? `双盲复核 (${escapeHtml(audLeg.model || '未配置')})` : '复核已关闭';
    const s4Short = s4Active ? '[已启用]' : '[未启用]';

    const s5Active = (evalLowConf > 0);
    const s5BadgeClass = s5Active ? 'badge-active' : 'badge-muted';
    const s5BadgeText = s5Active ? '[已启用]' : '[未启用]';
    const s5Summary = s5Active ? `低置信 &lt;${evalLowConf} / 蒸馏 ${feedbackDistill}` : '治理回流已关闭';
    const s5Short = s5Active ? '[已启用]' : '[未启用]';

    // 3. 构建 Compact Flow Bar
    const compactHtml = `
        <div id="engineOverviewCompact" class="engine-topo-compact">
            <div class="engine-topo-compact-left">
                <span class="engine-node-badge ${routingSummaryBadge}">[路由决策: ${escapeHtml(routingSummary)}]</span>
                <div class="engine-topo-chain">
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view03', 'setBlurLaplacianThreshold')" title="点击跳转: S0 质检熔断">
                        <strong>S0</strong> <span class="engine-node-badge ${s0BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s0Short}</span>
                    </span>
                    <span class="engine-topo-arrow">-&gt;</span>
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view03', 'setMemoryBudgetFactsTokens')" title="点击跳转: S1 动态记忆注入">
                        <strong>S1</strong> <span class="engine-node-badge ${s1BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s1Short}</span>
                    </span>
                    <span class="engine-topo-arrow">-&gt;</span>
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view01', 'adminRecognitionCard')" title="点击跳转: S2 核心视觉识别">
                        <strong>S2</strong> <span class="engine-node-badge ${s2BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s2Short}</span>
                    </span>
                    <span class="engine-topo-arrow">-&gt;</span>
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view01', 'adminParseDrawer')" title="点击跳转: S3 语义自愈">
                        <strong>S3</strong> <span class="engine-node-badge ${s3BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s3Short}</span>
                    </span>
                    <span class="engine-topo-arrow">-&gt;</span>
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view01', 'adminAuditCard')" title="点击跳转: S4 交叉审核">
                        <strong>S4</strong> <span class="engine-node-badge ${s4BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s4Short}</span>
                    </span>
                    <span class="engine-topo-arrow">-&gt;</span>
                    <span class="engine-topo-compact-chip" onclick="jumpToEngineSection('view03', 'setEvalCandidateLowConfidence')" title="点击跳转: S5 飞轮治理">
                        <strong>S5</strong> <span class="engine-node-badge ${s5BadgeClass}" style="font-size:0.65rem; padding:1px 4px;">${s5Short}</span>
                    </span>
                </div>
            </div>
            <div class="engine-topo-compact-right">
                <span class="engine-overview-source">${escapeHtml(sourceLabel)}</span>
            </div>
        </div>
    `;
    parts.push(compactHtml);

    // 4. 构建 Diagnostic Inspector
    let latestHistoryHtml = '';
    if (overviewState.latest) {
        const lat = overviewState.latest;
        const actionLabel = ENGINE_HISTORY_ACTION_LABELS[lat.action] || lat.action || '变更';
        const timeStr = _formatEngineTime(lat.created_at || lat.ts || '');
        const op = escapeHtml(lat.operator || 'admin');
        latestHistoryHtml = `<div class="engine-overview-history" style="font-size:0.75rem;">最近变更: <strong>[${escapeHtml(actionLabel)}]</strong> 于 ${timeStr} 由 ${op} 执行 (快照 v${escapeHtml(String(lat.version || ''))})</div>`;
    }

    const inspectorHtml = `
        <div id="engineOverviewInspector" class="engine-topo-inspector" style="display:${isExpanded ? 'flex' : 'none'};">
            <div class="engine-topo-grid">
                <!-- 流量路由决策层 -->
                <div class="engine-topo-plane engine-topo-plane-routing">
                    <div class="engine-topo-plane-header">
                        <div class="engine-topo-plane-title">
                            <span>[流量路由决策层]</span>
                            <span style="font-weight:400; font-size:0.72rem; color:#64748b;">仲裁顺序: P0 -&gt; P1 -&gt; P2 (独占/灰度/兜底)</span>
                        </div>
                    </div>
                    <div class="engine-routing-list">
                        <div class="engine-routing-item" onclick="jumpToEngineSection('view02', 'adminExperimentDrawer')" title="点击下钻 View 02: A/B 实验配置">
                            <div class="engine-routing-item-main">
                                <strong style="color:#1e293b;">P0 A/B 实验</strong>
                                <span class="engine-node-badge ${p0BadgeClass}">${p0BadgeText}</span>
                                <span style="color:#475569;">${p0Desc}</span>
                            </div>
                        </div>
                        <div class="engine-routing-item" onclick="jumpToEngineSection('view02', 'adminGreyDrawer')" title="点击下钻 View 02: 金丝雀灰度配置">
                            <div class="engine-routing-item-main">
                                <strong style="color:#1e293b;">P1 灰度发布</strong>
                                <span class="engine-node-badge ${p1BadgeClass}">${p1BadgeText}</span>
                                <span style="color:#475569;">${p1Desc}</span>
                            </div>
                        </div>
                        <div class="engine-routing-item" onclick="jumpToEngineSection('view01', 'adminRecognitionCard')" title="点击下钻 View 01: 核心生产基线配置">
                            <div class="engine-routing-item-main">
                                <strong style="color:#1e293b;">P2 生产基线</strong>
                                <span class="engine-node-badge ${p2BadgeClass}">${p2BadgeText}</span>
                                <span style="color:#475569;">${p2Desc}</span>
                            </div>
                        </div>
                    </div>
                </div>

                <!-- 串行执行流水线 -->
                <div class="engine-topo-plane engine-topo-plane-exec">
                    <div class="engine-topo-plane-header">
                        <div class="engine-topo-plane-title">
                            <span>[串行执行流水线]</span>
                            <span style="font-weight:400; font-size:0.72rem; color:#64748b;">单据流转时序: S0 -&gt; S1 -&gt; S2 -&gt; S3 -&gt; S4 -&gt; S5</span>
                        </div>
                    </div>
                    <div class="engine-pipeline-chain">
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view03', 'setBlurLaplacianThreshold')" title="点击跳转 View 03 配置质检阈值">
                            <div class="engine-topo-node-title">
                                <span>S0 质检熔断</span>
                                <span class="engine-node-badge ${s0BadgeClass}">${s0BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s0Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">0 词元 快速拦截</span></div>
                        </div>
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view03', 'setMemoryBudgetFactsTokens')" title="点击跳转 View 03 配置记忆预算">
                            <div class="engine-topo-node-title">
                                <span>S1 动态记忆注入</span>
                                <span class="engine-node-badge ${s1BadgeClass}">${s1BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s1Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">供应商记忆注入</span></div>
                        </div>
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view01', 'adminRecognitionCard')" title="点击跳转 View 01 配置识别引擎">
                            <div class="engine-topo-node-title">
                                <span>S2 核心视觉识别</span>
                                <span class="engine-node-badge ${s2BadgeClass}">${s2BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s2Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">核心视觉多模抽取</span></div>
                        </div>
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view01', 'adminParseDrawer')" title="点击跳转 View 01 配置语义自愈">
                            <div class="engine-topo-node-title">
                                <span>S3 语义自愈</span>
                                <span class="engine-node-badge ${s3BadgeClass}">${s3BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s3Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">格式损坏按需触发</span></div>
                        </div>
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view01', 'adminAuditCard')" title="点击跳转 View 01 配置交叉审核">
                            <div class="engine-topo-node-title">
                                <span>S4 交叉审核</span>
                                <span class="engine-node-badge ${s4BadgeClass}">${s4BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s4Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">独立双盲复核比对</span></div>
                        </div>
                        <div class="engine-topo-node" onclick="jumpToEngineSection('view03', 'setEvalCandidateLowConfidence')" title="点击跳转 View 03 配置治理规则">
                            <div class="engine-topo-node-title">
                                <span>S5 飞轮治理</span>
                                <span class="engine-node-badge ${s5BadgeClass}">${s5BadgeText}</span>
                            </div>
                            <div class="engine-topo-node-desc">${escapeHtml(s5Summary)}<br/><span style="color:#94a3b8; font-size:0.65rem;">评测集与记忆候选</span></div>
                        </div>
                    </div>
                </div>
            </div>
            ${latestHistoryHtml}
        </div>
    `;
    parts.push(inspectorHtml);

    el.innerHTML = parts.join('');
}
window.renderEngineOverview = renderEngineOverview;

// routing DTO：version + 三腿生效模型 + config_source
function loadEngineConfigRouting() {
    return apiFetch('/api/admin/engine-config/routing')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) return;
            _engineConfigVersion = body.data.version;
            _engineOverviewState.routing = body.data;
            renderEngineOverview();
        })
        .catch(() => {});
}

// history 最新一条：动作 / 时间 / 操作人
function loadEngineConfigHistory() {
    return apiFetch('/api/admin/engine-config/history?page=1&page_size=1')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) return;
            const items = body.data.items || [];
            _engineOverviewState.latest = items[0] || null;
            renderEngineOverview();
        })
        .catch(() => {});
}

// 保存 / 测试结果写入概览条
function setEngineStatusMessage(text, kind) {
    _engineOverviewState.message = text ? { text: text, kind: kind || 'info' } : null;
    renderEngineOverview();
}

let _userEnginePresetsCache = {};
window._userEnginePresetsCache = _userEnginePresetsCache;
let _currentSavePresetLeg = null;

const USER_ENGINE_LEG_MAP = {
    'rec': {
        selectId: 'adminRecPresetSelect',
        deleteBtnId: 'adminRecDeletePresetBtn',
        baseUrlId: 'adminOpenaiRecBaseUrl',
        apiKeyId: 'adminOpenaiRecApiKey',
        modelId: 'adminOpenaiRecModel',
        timeoutId: 'adminRecTimeoutSeconds',
        retryId: 'adminRecMaxRetryRounds',
        priceInputId: 'adminPriceInputRec',
        priceCachedId: 'adminPriceCachedInputRec',
        priceOutputId: 'adminPriceOutputRec',
        reasoningEffortId: 'adminOpenaiRecReasoningEffort',
        title: '识别引擎'
    },
    'aud': {
        selectId: 'adminAudPresetSelect',
        deleteBtnId: 'adminAudDeletePresetBtn',
        baseUrlId: 'adminOpenaiAudBaseUrl',
        apiKeyId: 'adminOpenaiAudApiKey',
        modelId: 'adminOpenaiAudModel',
        timeoutId: 'adminAudTimeoutSeconds',
        retryId: 'adminAudMaxRetryRounds',
        priceInputId: 'adminPriceInputAud',
        priceCachedId: 'adminPriceCachedInputAud',
        priceOutputId: 'adminPriceOutputAud',
        reasoningEffortId: 'adminOpenaiAudReasoningEffort',
        title: '审核引擎'
    },
    'parse': {
        selectId: 'adminParsePresetSelect',
        deleteBtnId: 'adminParseDeletePresetBtn',
        baseUrlId: 'adminParseOpenaiBaseUrl',
        apiKeyId: 'adminParseOpenaiApiKey',
        modelId: 'adminParseOpenaiModel',
        timeoutId: 'adminParseTimeoutSeconds',
        retryId: 'adminParseMaxRetryRounds',
        priceInputId: 'adminPriceInputParse',
        priceCachedId: 'adminPriceCachedInputParse',
        priceOutputId: 'adminPriceOutputParse',
        reasoningEffortId: 'adminParseOpenaiReasoningEffort',
        title: '语义解析自愈引擎'
    },
    'grey_rec': {
        selectId: 'adminGreyRecPresetSelect',
        deleteBtnId: 'adminGreyRecDeletePresetBtn',
        baseUrlId: 'adminGreyOpenaiRecBaseUrl',
        apiKeyId: 'adminGreyOpenaiRecApiKey',
        modelId: 'adminGreyOpenaiRecModel',
        timeoutId: 'adminGreyRecTimeoutSeconds',
        retryId: 'adminGreyRecMaxRetryRounds',
        priceInputId: 'adminGreyPriceInputRec',
        priceCachedId: 'adminGreyPriceCachedInputRec',
        priceOutputId: 'adminGreyPriceOutputRec',
        reasoningEffortId: 'adminGreyOpenaiRecReasoningEffort',
        title: '灰测识别引擎'
    },
    'grey_aud': {
        selectId: 'adminGreyAudPresetSelect',
        deleteBtnId: 'adminGreyAudDeletePresetBtn',
        baseUrlId: 'adminGreyOpenaiAudBaseUrl',
        apiKeyId: 'adminGreyOpenaiAudApiKey',
        modelId: 'adminGreyOpenaiAudModel',
        timeoutId: 'adminGreyAudTimeoutSeconds',
        retryId: 'adminGreyAudMaxRetryRounds',
        priceInputId: 'adminGreyPriceInputAud',
        priceCachedId: 'adminGreyPriceCachedInputAud',
        priceOutputId: 'adminGreyPriceOutputAud',
        reasoningEffortId: 'adminGreyOpenaiAudReasoningEffort',
        title: '灰测审核引擎'
    },
    'grey_parse': {
        selectId: 'adminGreyParsePresetSelect',
        deleteBtnId: 'adminGreyParseDeletePresetBtn',
        baseUrlId: 'adminGreyParseOpenaiBaseUrl',
        apiKeyId: 'adminGreyParseOpenaiApiKey',
        modelId: 'adminGreyParseOpenaiModel',
        timeoutId: 'adminGreyParseTimeoutSeconds',
        retryId: 'adminGreyParseMaxRetryRounds',
        priceInputId: 'adminGreyPriceInputParse',
        priceCachedId: 'adminGreyPriceCachedInputParse',
        priceOutputId: 'adminGreyPriceOutputParse',
        reasoningEffortId: 'adminGreyParseOpenaiReasoningEffort',
        title: '灰测解析 LLM'
    }
};

async function loadUserEnginePresets() {
    try {
        const res = await apiFetch('/api/admin/engine-presets');
        const body = await res.json();
        if (body && body.status === 'success' && body.data) {
            _userEnginePresetsCache = body.data || {};
        } else {
            _userEnginePresetsCache = {};
        }
    } catch (e) {
        _userEnginePresetsCache = {};
    }
    window._userEnginePresetsCache = _userEnginePresetsCache;

    const presetEntries = Object.entries(_userEnginePresetsCache);

    Object.keys(USER_ENGINE_LEG_MAP).forEach(leg => {
        const meta = USER_ENGINE_LEG_MAP[leg];
        const sel = document.getElementById(meta.selectId);
        if (!sel) return;
        const currentVal = sel.value;

        if (sel.tagName === 'INPUT') {
            const wrap = sel.closest('.preset-combobox-wrap');
            if (wrap) {
                const menu = wrap.querySelector('.preset-dropdown-menu');
                if (menu) renderEnginePresetMenuItems(leg, sel, menu);
            }
            const listId = sel.getAttribute('list') || (meta.selectId + 'List');
            const datalist = document.getElementById(listId);
            if (datalist) {
                datalist.innerHTML = '';
                presetEntries.forEach(([key, p]) => {
                    const opt = document.createElement('option');
                    opt.value = (p && p.label) ? p.label : key;
                    opt.setAttribute('data-key', key);
                    datalist.appendChild(opt);
                });
            }
            let matchedKey = null;
            if (currentVal) {
                if (_userEnginePresetsCache[currentVal]) matchedKey = currentVal;
                else {
                    const match = presetEntries.find(([k, p]) => (p && p.label === currentVal) || k === currentVal);
                    if (match) matchedKey = match[0];
                }
            }
            const delBtn = document.getElementById(meta.deleteBtnId);
            if (delBtn) {
                delBtn.style.display = matchedKey ? 'inline-block' : 'none';
            }
        } else {
            sel.innerHTML = '<option value="">— 手动填写 / 未选择 —</option>';
            presetEntries.forEach(([key, p]) => {
                const opt = document.createElement('option');
                opt.value = key;
                opt.textContent = (p && p.label) ? p.label : key;
                sel.appendChild(opt);
            });

            if (currentVal && _userEnginePresetsCache[currentVal]) {
                sel.value = currentVal;
            } else {
                sel.value = '';
            }

            const delBtn = document.getElementById(meta.deleteBtnId);
            if (delBtn) {
                delBtn.style.display = sel.value ? 'inline-block' : 'none';
            }
        }
    });
}
window.loadUserEnginePresets = loadUserEnginePresets;

function toggleEnginePresetMenu(leg) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    const input = document.getElementById(meta.selectId);
    if (!input) return;
    const wrap = input.closest('.preset-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.preset-dropdown-menu');
    if (!menu) return;
    if (menu.classList.contains('hide')) {
        openEnginePresetMenu(leg);
        input.focus();
    } else {
        menu.classList.add('hide');
    }
}
window.toggleEnginePresetMenu = toggleEnginePresetMenu;

function openEnginePresetMenu(leg) {
    document.querySelectorAll('.preset-dropdown-menu').forEach(m => m.classList.add('hide'));
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    const input = document.getElementById(meta.selectId);
    if (!input) return;
    const wrap = input.closest('.preset-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.preset-dropdown-menu');
    if (!menu) return;

    if (!window._userEnginePresetsCache || Object.keys(window._userEnginePresetsCache).length === 0) {
        if (typeof loadUserEnginePresets === 'function') {
            loadUserEnginePresets().then(() => {
                renderEnginePresetMenuItems(leg, input, menu);
                menu.classList.remove('hide');
            });
            return;
        }
    }

    renderEnginePresetMenuItems(leg, input, menu);
    menu.classList.remove('hide');
}
window.openEnginePresetMenu = openEnginePresetMenu;

function renderEnginePresetMenuItems(leg, inputElem, menuElem) {
    const cur = (inputElem.value || '').trim().toLowerCase();
    const presetEntries = Object.entries(_userEnginePresetsCache || {});

    let html = '';
    presetEntries.forEach(([key, p]) => {
        const label = (p && p.label) ? p.label : key;
        const model = (p && (p.model || p.rec_model || p.aud_model || p.parse_model)) || '';
        const isMatch = !cur || key.toLowerCase().includes(cur) || label.toLowerCase().includes(cur) || model.toLowerCase().includes(cur);
        if (isMatch) {
            const isExact = (cur === key.toLowerCase() || cur === label.toLowerCase());
            const safeKey = key.replace(/'/g, "\\'");
            html += `<div class="preset-dropdown-item ${isExact ? 'active' : ''}" style="padding:6px 10px; font-size:0.78rem; color:#1e293b; cursor:pointer; display:flex; justify-content:space-between; align-items:center; gap:8px; white-space:nowrap; border-bottom:1px solid #f1f5f9;" onmousedown="selectEnginePresetItem('${leg}', '${safeKey}')">
                <div style="display:flex; flex-direction:column; gap:2px; text-align:left;">
                    <span style="font-weight:600; color:#1e293b;">${escapeHtml(label)}</span>
                    ${model ? `<span style="font-size:0.68rem; color:#64748b;">${escapeHtml(model)}</span>` : ''}
                </div>
                ${isExact ? '<span class="preset-badge" style="font-size:0.68rem; color:#1d4ed8; background:#eff6ff; border:1px solid #bfdbfe; padding:1px 5px; border-radius:4px;">已选择</span>' : ''}
            </div>`;
        }
    });

    if (cur && !presetEntries.some(([k, p]) => k.toLowerCase() === cur || ((p && p.label) && p.label.toLowerCase() === cur))) {
        html += `<div class="preset-dropdown-item" style="padding:6px 10px; font-size:0.78rem; border-top:1px dashed #e2e8f0; color:#0284c7; cursor:pointer; display:flex; justify-content:space-between; align-items:center;" onmousedown="closeEnginePresetMenuDelay('${leg}')">
            <span>使用新 Provider: <b>${escapeHtml(inputElem.value.trim())}</b></span>
            <span style="font-size:0.68rem; color:#0369a1; background:#f0f9ff; border:1px solid #bae6fd; padding:1px 5px; border-radius:4px;">点击保存入库</span>
        </div>`;
    }

    if (!html) {
        html = '<div class="preset-dropdown-item" style="padding:8px 10px; font-size:0.75rem; cursor:default; color:#94a3b8; text-align:center;">无匹配模版，可直接输入新名称并保存</div>';
    }

    menuElem.innerHTML = html;
}
window.renderEnginePresetMenuItems = renderEnginePresetMenuItems;

function onInputEnginePresetSearch(leg, text) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    const input = document.getElementById(meta.selectId);
    if (!input) return;
    const wrap = input.closest('.preset-combobox-wrap');
    if (!wrap) return;
    const menu = wrap.querySelector('.preset-dropdown-menu');
    if (menu) {
        renderEnginePresetMenuItems(leg, input, menu);
        menu.classList.remove('hide');
    }
    onInputUserEnginePreset(leg, text);
}
window.onInputEnginePresetSearch = onInputEnginePresetSearch;

function closeEnginePresetMenuDelay(leg) {
    setTimeout(() => {
        const meta = USER_ENGINE_LEG_MAP[leg];
        if (!meta) return;
        const input = document.getElementById(meta.selectId);
        if (!input) return;
        const wrap = input.closest('.preset-combobox-wrap');
        if (wrap) {
            const menu = wrap.querySelector('.preset-dropdown-menu');
            if (menu) menu.classList.add('hide');
        }
    }, 200);
}
window.closeEnginePresetMenuDelay = closeEnginePresetMenuDelay;

function selectEnginePresetItem(leg, key) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    const input = document.getElementById(meta.selectId);
    const preset = _userEnginePresetsCache ? _userEnginePresetsCache[key] : null;
    if (input) {
        input.value = (preset && preset.label) ? preset.label : key;
    }
    onSelectUserEnginePreset(leg, key);
    const wrap = input ? input.closest('.preset-combobox-wrap') : null;
    if (wrap) {
        const menu = wrap.querySelector('.preset-dropdown-menu');
        if (menu) menu.classList.add('hide');
    }
}
window.selectEnginePresetItem = selectEnginePresetItem;

function onInputUserEnginePreset(leg, text) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    const val = (text || '').trim();
    const delBtn = document.getElementById(meta.deleteBtnId);

    if (!val) {
        if (delBtn) delBtn.style.display = 'none';
        return;
    }

    let matchedKey = null;
    if (_userEnginePresetsCache[val]) {
        matchedKey = val;
    } else {
        const entry = Object.entries(_userEnginePresetsCache).find(([k, p]) => {
            return k.toLowerCase() === val.toLowerCase() || (p && p.label && p.label.toLowerCase() === val.toLowerCase());
        });
        if (entry) {
            matchedKey = entry[0];
        }
    }

    if (matchedKey) {
        onSelectUserEnginePreset(leg, matchedKey);
        if (delBtn) delBtn.style.display = 'inline-block';
    } else {
        if (delBtn) delBtn.style.display = 'none';
    }
}
window.onInputUserEnginePreset = onInputUserEnginePreset;

function onSelectUserEnginePreset(leg, presetKey) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;

    const delBtn = document.getElementById(meta.deleteBtnId);
    if (!presetKey) {
        if (delBtn) delBtn.style.display = 'none';
        return;
    }

    const preset = _userEnginePresetsCache[presetKey];
    if (!preset) return;

    if (delBtn) delBtn.style.display = 'inline-block';

    const sel = document.getElementById(meta.selectId);
    if (sel) {
        if (sel.tagName === 'INPUT') {
            sel.value = (preset.label || presetKey);
        } else {
            sel.value = presetKey;
        }
    }

    const setVal = (id, val) => {
        const el = document.getElementById(id);
        if (el && val !== undefined && val !== null) {
            el.value = val;
        }
    };

    setVal(meta.baseUrlId, preset.base_url || '');

    const keyEl = document.getElementById(meta.apiKeyId);
    if (keyEl) {
        keyEl.value = preset.api_key || '';
        keyEl.setAttribute('data-preset-key', presetKey);
    }

    setVal(meta.modelId, preset.model || preset.rec_model || preset.aud_model || preset.parse_model || '');

    if (preset.timeout_seconds !== undefined && preset.timeout_seconds !== null) {
        setVal(meta.timeoutId, preset.timeout_seconds);
    }
    if (preset.max_retry_rounds !== undefined && preset.max_retry_rounds !== null) {
        setVal(meta.retryId, preset.max_retry_rounds);
    }
    if (preset.price_input !== undefined && preset.price_input !== null) {
        setVal(meta.priceInputId, preset.price_input);
    }
    if (preset.price_cached !== undefined && preset.price_cached !== null) {
        setVal(meta.priceCachedId, preset.price_cached);
    }
    if (preset.price_output !== undefined && preset.price_output !== null) {
        setVal(meta.priceOutputId, preset.price_output);
    }
    if (preset.reasoning_effort !== undefined && preset.reasoning_effort !== null && meta.reasoningEffortId) {
        setVal(meta.reasoningEffortId, preset.reasoning_effort);
    }

    if (typeof updateEngineCostCalculator === 'function') {
        updateEngineCostCalculator(leg);
    }

    if (typeof updateModelTraitHints === 'function') {
        updateModelTraitHints(leg);
    }
    if (typeof updateAdvBadge === 'function') {
        updateAdvBadge(leg);
    }

    if (typeof checkEngineDirty === 'function') {
        checkEngineDirty();
    }

    showToast('[INFO] 已回填模版「' + (preset.label || presetKey) + '」参数', 'info');
}
window.onSelectUserEnginePreset = onSelectUserEnginePreset;

function saveCurrentConfigAsPreset(leg) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;
    _currentSavePresetLeg = leg;

    const sel = document.getElementById(meta.selectId);
    const existingVal = (sel && sel.value ? sel.value.trim() : '');

    if (existingVal && existingVal !== '— 手动填写 / 未选择 —') {
        confirmSaveCurrentPresetWithLabel(leg, existingVal);
        return;
    }

    const modal = document.getElementById('savePresetModal');
    const input = document.getElementById('savePresetLabelInput');
    const title = document.getElementById('savePresetModalTitle');

    if (title) {
        title.textContent = '保存配置模版 (' + meta.title + ')';
    }
    if (input) {
        input.value = '';
    }
    if (modal) {
        modal.classList.remove('hide');
        if (input) setTimeout(() => input.focus(), 50);
    } else {
        uiPrompt({ title: '保存配置模版', label: '模版名称', confirmText: '保存' }).then(label => {
            if (label !== null) confirmSaveCurrentPresetWithLabel(leg, label);
        });
    }
}
window.saveCurrentConfigAsPreset = saveCurrentConfigAsPreset;
window.openSavePresetModal = saveCurrentConfigAsPreset;

function closeSavePresetModal() {
    const modal = document.getElementById('savePresetModal');
    if (modal) {
        modal.classList.add('hide');
    }
    _currentSavePresetLeg = null;
}
window.closeSavePresetModal = closeSavePresetModal;

async function confirmSaveCurrentPreset() {
    const leg = _currentSavePresetLeg;
    if (!leg) return;
    const input = document.getElementById('savePresetLabelInput');
    const label = (input ? input.value : '').trim();
    await confirmSaveCurrentPresetWithLabel(leg, label);
}
window.confirmSaveCurrentPreset = confirmSaveCurrentPreset;

async function confirmSaveCurrentPresetWithLabel(leg, label) {
    if (!label) {
        showToast('[WARN] 请输入模版名称', 'warning');
        return;
    }
    if (label.length > 60) {
        showToast('[WARN] 模版名称不能超过 60 个字符', 'warning');
        return;
    }

    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;

    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };

    const baseUrl = getVal(meta.baseUrlId);
    if (!baseUrl) {
        showToast('[WARN] Base URL 不能为空', 'warning');
        return;
    }

    const apiKey = getVal(meta.apiKeyId);
    const model = getVal(meta.modelId);
    const timeoutRaw = getVal(meta.timeoutId);
    const retryRaw = getVal(meta.retryId);
    const priceInRaw = getVal(meta.priceInputId);
    const priceCachedRaw = getVal(meta.priceCachedId);
    const priceOutRaw = getVal(meta.priceOutputId);
    const reasoningEffort = meta.reasoningEffortId ? getVal(meta.reasoningEffortId) : '';

    const payload = {
        label: label,
        base_url: baseUrl,
        api_key: apiKey,
        model: model,
        rec_model: model,
        aud_model: model,
        parse_model: model,
        reasoning_effort: reasoningEffort
    };

    if (timeoutRaw !== '') {
        const t = parseInt(timeoutRaw, 10);
        if (!isNaN(t)) payload.timeout_seconds = t;
    }
    if (retryRaw !== '') {
        const r = parseInt(retryRaw, 10);
        if (!isNaN(r)) payload.max_retry_rounds = r;
    }
    if (priceInRaw !== '') {
        const pi = parseFloat(priceInRaw);
        if (!isNaN(pi)) payload.price_input = pi;
    }
    if (priceCachedRaw !== '') {
        const pc = parseFloat(priceCachedRaw);
        if (!isNaN(pc)) payload.price_cached = pc;
    }
    if (priceOutRaw !== '') {
        const po = parseFloat(priceOutRaw);
        if (!isNaN(po)) payload.price_output = po;
    }

    const btn = document.getElementById('confirmSavePresetBtn');
    if (btn) btn.disabled = true;

    try {
        const res = await apiFetch('/api/admin/engine-presets', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const ret = await res.json();
        if (res.ok && ret && ret.status === 'success') {
            closeSavePresetModal();
            showToast('[OK] 模版「' + label + '」已成功保存至数据库', 'success');
            await loadUserEnginePresets();
            const sel = document.getElementById(meta.selectId);
            if (sel && ret.key) {
                if (sel.tagName === 'INPUT') {
                    sel.value = label;
                } else {
                    sel.value = ret.key;
                }
            }
            const delBtn = document.getElementById(meta.deleteBtnId);
            if (delBtn) {
                delBtn.style.display = 'inline-block';
            }
        } else {
            const errMsg = (ret && (ret.msg || ret.detail)) || '保存模版失败';
            showToast('[FAIL] 保存模版失败: ' + errMsg, 'error');
        }
    } catch (err) {
        showToast('[FAIL] 保存模版网络异常: ' + (err.message || err), 'error');
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function deleteSelectedPreset(leg) {
    const meta = USER_ENGINE_LEG_MAP[leg];
    if (!meta) return;

    const sel = document.getElementById(meta.selectId);
    if (!sel || !sel.value) return;

    let key = (sel.value || '').trim();
    let preset = _userEnginePresetsCache[key];
    if (!preset) {
        const match = Object.entries(_userEnginePresetsCache).find(([k, p]) => (p && p.label === key) || k === key);
        if (match) {
            key = match[0];
            preset = match[1];
        }
    }
    const label = (preset && preset.label) ? preset.label : (sel.options && sel.options[sel.selectedIndex] ? sel.options[sel.selectedIndex].text : key);

    const okPreset = await uiConfirm({
        title: '删除模版',
        message: '确定删除模版「' + label + '」吗？删除后不能恢复。',
        confirmText: '删除',
        danger: true,
    });
    if (!okPreset) return;

    try {
        const res = await apiFetch('/api/admin/engine-presets/' + encodeURIComponent(key), {
            method: 'DELETE'
        });
        const ret = await res.json();
        if (res.ok && ret && ret.status === 'success') {
            showToast('[OK] 模版「' + label + '」已成功从数据库删除', 'success');
            await loadUserEnginePresets();
            Object.keys(USER_ENGINE_LEG_MAP).forEach(l => {
                const s = document.getElementById(USER_ENGINE_LEG_MAP[l].selectId);
                if (s && (s.value === key || s.value === label)) {
                    s.value = '';
                    const d = document.getElementById(USER_ENGINE_LEG_MAP[l].deleteBtnId);
                    if (d) d.style.display = 'none';
                }
            });
        } else {
            const errMsg = (ret && (ret.msg || ret.detail)) || '删除模版失败';
            showToast('[FAIL] 删除模版失败: ' + errMsg, 'error');
        }
    } catch (err) {
        showToast('[FAIL] 删除模版网络异常: ' + (err.message || err), 'error');
    }
}
window.deleteSelectedPreset = deleteSelectedPreset;
window.deleteUserEnginePresetAction = deleteSelectedPreset;

// 按模块渲染：A 引擎与凭据 / B 成本与计费 / C 发布与流量管理
function loadAdminEngineConfig() {
    // 仅 admin 可查看
    const role = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    if (role !== 'admin') return;
    bindAdminEngineEventsOnce();
    loadAdminExperimentsList();
    loadUserEnginePresets();
    loadAdminSettings();

    _engineOverviewState.message = null;
    loadEngineConfigRouting();
    loadEngineConfigHistory();

    apiFetch('/api/admin/engine-config')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) {
                showToast('读取引擎配置失败', 'error');
                return;
            }
            const cfg = body.data;
            renderAdminEngineConfigSource(cfg.config_source);
            renderEngineModuleA(cfg);
            renderEngineModuleB(cfg);
            renderEngineModuleC(cfg);

            bindAdvParamListeners();
            ['rec', 'aud', 'parse', 'grey_rec', 'grey_aud', 'grey_parse'].forEach(legId => {
                updateAdvBadge(legId);
                checkTempWarn(legId);
                updateModelTraitHints(legId);
            });

            // 高级模型推理参数回显与防呆
            const setElVal = (id, val, fallback) => {
                const el = document.getElementById(id);
                if (el) el.value = (val !== null && val !== undefined && val !== '') ? val : fallback;
            };

            // Rec
            setElVal('adminRecMaxTokens', cfg.openai_rec_max_tokens, 4096);
            setElVal('adminRecImageDetail', cfg.openai_rec_image_detail, 'high');
            setElVal('adminRecTemperature', cfg.openai_rec_temperature !== undefined && cfg.openai_rec_temperature !== null ? cfg.openai_rec_temperature : 0.0, 0.0);

            // Aud
            setElVal('adminAudMaxTokens', cfg.openai_aud_max_tokens, 4096);
            setElVal('adminAudImageDetail', 'auto', 'auto');
            setElVal('adminAudTemperature', cfg.openai_aud_temperature !== undefined && cfg.openai_aud_temperature !== null ? cfg.openai_aud_temperature : 0.0, 0.0);

            // Parse
            setElVal('adminParseMaxTokens', cfg.openai_parse_max_tokens, 2048);
            setElVal('adminParseImageDetail', 'auto', 'auto');
            setElVal('adminParseTemperature', cfg.openai_parse_temperature !== undefined && cfg.openai_parse_temperature !== null ? cfg.openai_parse_temperature : 0.0, 0.0);

            // Grey Rec
            setElVal('adminGreyRecMaxTokens', cfg.grey_openai_rec_max_tokens, 4096);
            setElVal('adminGreyRecImageDetail', cfg.grey_openai_rec_image_detail, 'high');
            setElVal('adminGreyRecTemperature', cfg.grey_openai_rec_temperature !== undefined && cfg.grey_openai_rec_temperature !== null ? cfg.grey_openai_rec_temperature : 0.0, 0.0);

            // Grey Aud
            setElVal('adminGreyAudMaxTokens', cfg.grey_openai_aud_max_tokens, 4096);
            setElVal('adminGreyAudImageDetail', 'auto', 'auto');
            setElVal('adminGreyAudTemperature', cfg.grey_openai_aud_temperature !== undefined && cfg.grey_openai_aud_temperature !== null ? cfg.grey_openai_aud_temperature : 0.0, 0.0);

            // Grey Parse
            setElVal('adminGreyParseMaxTokens', cfg.grey_openai_parse_max_tokens, 2048);
            setElVal('adminGreyParseImageDetail', 'auto', 'auto');
            setElVal('adminGreyParseTemperature', cfg.grey_openai_parse_temperature !== undefined && cfg.grey_openai_parse_temperature !== null ? cfg.grey_openai_parse_temperature : 0.0, 0.0);

            // 绑定事件并刷新 Badge 与温度警告
            bindAdvParamListeners();
            ['rec', 'aud', 'parse', 'grey_rec', 'grey_aud', 'grey_parse'].forEach(legId => {
                updateAdvBadge(legId);
                checkTempWarn(legId);
                updateModelTraitHints(legId);
            });

            updateAuditDisabledState();
            updateGreyDisabledState();
            updateParseDisabledState();
            updateGreyParseDisabledState();
            updateGreyAuditDisabledState();

            updateAllCostCalculators();
            setTimeout(() => {
                if (typeof captureEngineFormSnapshot === 'function') {
                    captureEngineFormSnapshot();
                }
            }, 300);
        })
        .catch(() => showToast('读取引擎配置失败', 'error'));
}

// 模块 A — 引擎与凭据：识别 / 审核 / 解析三腿
function renderEngineModuleA(cfg) {
    // 常规识别
    const recBase = document.getElementById('adminOpenaiRecBaseUrl');
    const recKey = document.getElementById('adminOpenaiRecApiKey');
    const recMod = document.getElementById('adminOpenaiRecModel');
    if (recBase) recBase.value = cfg.openai_rec_base_url || '';
    if (recKey) {
        recKey.value = cfg.openai_rec_api_key || '';
        recKey.placeholder = getVendorKeyPlaceholder(cfg.openai_rec_base_url);
    }
    if (recMod) recMod.value = cfg.openai_rec_model || cfg.recognition_model || '';
    const recTimeout = document.getElementById('adminRecTimeoutSeconds');
    if (recTimeout) recTimeout.value = (cfg.rec_timeout_seconds != null ? cfg.rec_timeout_seconds : '');
    const recRetry = document.getElementById('adminRecMaxRetryRounds');
    if (recRetry) {
        const v = cfg.openai_rec_max_retry_rounds != null ? cfg.openai_rec_max_retry_rounds : (cfg.rec_max_retry_rounds != null ? cfg.rec_max_retry_rounds : (cfg.max_retry_rounds != null ? cfg.max_retry_rounds : ''));
        recRetry.value = v != null ? v : '';
    }
    const recReasoning = document.getElementById('adminOpenaiRecReasoningEffort');
    if (recReasoning) recReasoning.value = cfg.openai_rec_reasoning_effort || '';

    // 常规审核
    const audEnabled = document.getElementById('adminAuditEnabled');
    if (audEnabled) audEnabled.value = cfg.audit_enabled ? 'true' : 'false';
    const audBase = document.getElementById('adminOpenaiAudBaseUrl');
    const audKey = document.getElementById('adminOpenaiAudApiKey');
    const audMod = document.getElementById('adminOpenaiAudModel');
    if (audBase) audBase.value = cfg.openai_aud_base_url || '';
    if (audKey) {
        audKey.value = cfg.openai_aud_api_key || '';
        audKey.placeholder = getVendorKeyPlaceholder(cfg.openai_aud_base_url);
    }
    if (audMod) audMod.value = cfg.openai_aud_model || cfg.audit_model || '';
    const audTimeout = document.getElementById('adminAudTimeoutSeconds');
    if (audTimeout) audTimeout.value = (cfg.aud_timeout_seconds != null ? cfg.aud_timeout_seconds : '');
    const audRetry = document.getElementById('adminAudMaxRetryRounds');
    if (audRetry) {
        const v = cfg.openai_aud_max_retry_rounds != null ? cfg.openai_aud_max_retry_rounds : (cfg.aud_max_retry_rounds != null ? cfg.aud_max_retry_rounds : '');
        audRetry.value = v != null ? v : '';
    }
    const audReasoning = document.getElementById('adminOpenaiAudReasoningEffort');
    if (audReasoning) audReasoning.value = cfg.openai_aud_reasoning_effort || '';

    // 常规解析 LLM
    const parseEnabled = document.getElementById('adminParseEnabled');
    if (parseEnabled) parseEnabled.value = cfg.parse_llm_enabled ? 'true' : 'false';
    const parseBase = document.getElementById('adminParseOpenaiBaseUrl');
    const parseKey = document.getElementById('adminParseOpenaiApiKey');
    const parseMod = document.getElementById('adminParseOpenaiModel');
    if (parseBase) parseBase.value = cfg.openai_parse_base_url || '';
    if (parseKey) {
        parseKey.value = cfg.openai_parse_api_key || '';
        parseKey.placeholder = getVendorKeyPlaceholder(cfg.openai_parse_base_url);
    }
    if (parseMod) parseMod.value = cfg.openai_parse_model || cfg.parse_llm_model || '';
    const parseTimeout = document.getElementById('adminParseTimeoutSeconds');
    if (parseTimeout) parseTimeout.value = (cfg.parse_timeout_seconds != null ? cfg.parse_timeout_seconds : '');
    const parseRetry = document.getElementById('adminParseMaxRetryRounds');
    if (parseRetry) {
        const v = cfg.openai_parse_max_retry_rounds != null ? cfg.openai_parse_max_retry_rounds : (cfg.parse_max_retry_rounds != null ? cfg.parse_max_retry_rounds : '');
        parseRetry.value = v != null ? v : '';
    }
    const parseReasoning = document.getElementById('adminParseOpenaiReasoningEffort');
    if (parseReasoning) parseReasoning.value = cfg.openai_parse_reasoning_effort || '';

    // 高级参数回显（max_tokens / image_detail / temperature）
    const setElVal = (id, val, fallback) => {
        const el = document.getElementById(id);
        if (el) el.value = (val !== null && val !== undefined && val !== '') ? val : fallback;
    };
    setElVal('adminRecMaxTokens', cfg.openai_rec_max_tokens, 4096);
    setElVal('adminRecImageDetail', cfg.openai_rec_image_detail, 'high');
    setElVal('adminRecTemperature', cfg.openai_rec_temperature !== undefined && cfg.openai_rec_temperature !== null ? cfg.openai_rec_temperature : 0.0, 0.0);
    setElVal('adminAudMaxTokens', cfg.openai_aud_max_tokens, 4096);
    setElVal('adminAudImageDetail', 'auto', 'auto');
    setElVal('adminAudTemperature', cfg.openai_aud_temperature !== undefined && cfg.openai_aud_temperature !== null ? cfg.openai_aud_temperature : 0.0, 0.0);
    setElVal('adminParseMaxTokens', cfg.openai_parse_max_tokens, 2048);
    setElVal('adminParseImageDetail', 'auto', 'auto');
    setElVal('adminParseTemperature', cfg.openai_parse_temperature !== undefined && cfg.openai_parse_temperature !== null ? cfg.openai_parse_temperature : 0.0, 0.0);

    // 自动展开/折叠抽屉（对应功能启用时）
    const setCoreEngineCardCollapse = (leg, isCollapsed) => {
        const cardMap = {
            'rec': { cardId: 'adminRecognitionCard', bodyId: 'adminOpenaiRecognitionBox' },
            'aud': { cardId: 'adminAuditCard', bodyId: 'adminOpenaiAuditBox' },
            'parse': { cardId: 'adminParseDrawer', bodyId: 'adminParseOpenaiBox' }
        };
        const item = cardMap[leg];
        if (!item) return;
        const card = document.getElementById(item.cardId);
        if (!card) return;
        card.classList.toggle('collapsed', isCollapsed);
        const body = document.getElementById(item.bodyId);
        if (body) {
            body.style.display = isCollapsed ? 'none' : 'block';
        }
        const btns = card.querySelectorAll(`.engine-card-toggle-btn, #btnToggleCard_${leg}, #engineCardToggleBtn_${leg}`);
        btns.forEach(b => {
            b.textContent = isCollapsed ? '展开' : '收起';
            b.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
        });
        const h = card.querySelector('.engine-card-header');
        if (h) h.setAttribute('aria-expanded', isCollapsed ? 'false' : 'true');
    };

    setCoreEngineCardCollapse('rec', false);
    setCoreEngineCardCollapse('aud', !cfg.audit_enabled);
    setCoreEngineCardCollapse('parse', !cfg.parse_llm_enabled);
    const parseDrawer = document.getElementById('adminParseDrawer');
    if (parseDrawer) {
        parseDrawer.classList.toggle('collapsed', !cfg.parse_llm_enabled);
    }
}

// 模块 B — 成本与计费：18 个单价（元/1M Token）
function renderEngineModuleB(cfg) {
    const setPrice = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.value = formatCleanPrice(val);
    };
    setPrice('adminPriceInputRec', cfg.price_input_rec);
    setPrice('adminPriceCachedInputRec', cfg.price_cached_input_rec);
    setPrice('adminPriceOutputRec', cfg.price_output_rec);
    setPrice('adminPriceInputAud', cfg.price_input_aud);
    setPrice('adminPriceCachedInputAud', cfg.price_cached_input_aud);
    setPrice('adminPriceOutputAud', cfg.price_output_aud);
    setPrice('adminPriceInputParse', cfg.price_input_parse);
    setPrice('adminPriceCachedInputParse', cfg.price_cached_input_parse);
    setPrice('adminPriceOutputParse', cfg.price_output_parse);
    setPrice('adminGreyPriceInputRec', cfg.price_input_grey_rec);
    setPrice('adminGreyPriceCachedInputRec', cfg.price_cached_input_grey_rec);
    setPrice('adminGreyPriceOutputRec', cfg.price_output_grey_rec);
    setPrice('adminGreyPriceInputAud', cfg.price_input_grey_aud);
    setPrice('adminGreyPriceCachedInputAud', cfg.price_cached_input_grey_aud);
    setPrice('adminGreyPriceOutputAud', cfg.price_output_grey_aud);
    setPrice('adminGreyPriceInputParse', cfg.price_input_grey_parse);
    setPrice('adminGreyPriceCachedInputParse', cfg.price_cached_input_grey_parse);
    setPrice('adminGreyPriceOutputParse', cfg.price_output_grey_parse);
    updateAllCostCalculators();
}

// 模块 C — 发布与流量管理：金丝雀灰测三腿（A/B 实验由既有实验端点渲染）
function renderEngineModuleC(cfg) {
    const greyEnabled = document.getElementById('adminGreyEnabled');
    if (greyEnabled) greyEnabled.value = cfg.grey_enabled ? 'true' : 'false';
    const greyPercent = document.getElementById('adminGreyPercent');
    if (greyPercent) greyPercent.value = cfg.grey_percent || 0;
    const greyAssign = document.getElementById('adminGreyAssignMode');
    if (greyAssign) greyAssign.value = cfg.grey_assign_mode || 'receipt';

    if (typeof bindTrxSliderEvents === 'function') bindTrxSliderEvents();
    if (typeof syncGreySplitSlider === 'function') syncGreySplitSlider(cfg.grey_percent || 0);
    if (typeof onGreyAssignModeChange === 'function') onGreyAssignModeChange(cfg.grey_assign_mode || 'receipt');
    if (typeof updateTrxGlobalHeader === 'function') updateTrxGlobalHeader();

    // 灰测识别
    const gRecBase = document.getElementById('adminGreyOpenaiRecBaseUrl');
    const gRecKey = document.getElementById('adminGreyOpenaiRecApiKey');
    const gRecMod = document.getElementById('adminGreyOpenaiRecModel');
    if (gRecBase) gRecBase.value = cfg.grey_openai_rec_base_url || '';
    if (gRecKey) {
        gRecKey.value = cfg.grey_openai_rec_api_key || '';
        gRecKey.placeholder = getVendorKeyPlaceholder(cfg.grey_openai_rec_base_url);
    }
    if (gRecMod) gRecMod.value = cfg.grey_openai_rec_model || cfg.grey_recognition_model || '';
    const gRecTimeout = document.getElementById('adminGreyRecTimeoutSeconds');
    if (gRecTimeout) gRecTimeout.value = (cfg.grey_rec_timeout_seconds != null ? cfg.grey_rec_timeout_seconds : '');
    const gRecRetry = document.getElementById('adminGreyRecMaxRetryRounds');
    if (gRecRetry) {
        const v = cfg.grey_openai_rec_max_retry_rounds != null ? cfg.grey_openai_rec_max_retry_rounds : (cfg.grey_rec_max_retry_rounds != null ? cfg.grey_rec_max_retry_rounds : '');
        gRecRetry.value = v != null ? v : '';
    }
    const gRecReasoning = document.getElementById('adminGreyOpenaiRecReasoningEffort');
    if (gRecReasoning) gRecReasoning.value = cfg.grey_openai_rec_reasoning_effort || '';

    // 灰测审核
    const gAudEnabled = document.getElementById('adminGreyAuditEnabled');
    if (gAudEnabled) gAudEnabled.value = cfg.grey_audit_enabled ? 'true' : 'false';
    const gAudBase = document.getElementById('adminGreyOpenaiAudBaseUrl');
    const gAudKey = document.getElementById('adminGreyOpenaiAudApiKey');
    const gAudMod = document.getElementById('adminGreyOpenaiAudModel');
    if (gAudBase) gAudBase.value = cfg.grey_openai_aud_base_url || '';
    if (gAudKey) {
        gAudKey.value = cfg.grey_openai_aud_api_key || '';
        gAudKey.placeholder = getVendorKeyPlaceholder(cfg.grey_openai_aud_base_url);
    }
    if (gAudMod) gAudMod.value = cfg.grey_openai_aud_model || cfg.grey_audit_model || '';
    const gAudTimeout = document.getElementById('adminGreyAudTimeoutSeconds');
    if (gAudTimeout) gAudTimeout.value = (cfg.grey_aud_timeout_seconds != null ? cfg.grey_aud_timeout_seconds : '');
    const gAudRetry = document.getElementById('adminGreyAudMaxRetryRounds');
    if (gAudRetry) {
        const v = cfg.grey_openai_aud_max_retry_rounds != null ? cfg.grey_openai_aud_max_retry_rounds : (cfg.grey_aud_max_retry_rounds != null ? cfg.grey_aud_max_retry_rounds : '');
        gAudRetry.value = v != null ? v : '';
    }
    const gAudReasoning = document.getElementById('adminGreyOpenaiAudReasoningEffort');
    if (gAudReasoning) gAudReasoning.value = cfg.grey_openai_aud_reasoning_effort || '';

    // 灰测解析 LLM
    const gParseEnabled = document.getElementById('adminGreyParseEnabled');
    if (gParseEnabled) gParseEnabled.value = cfg.grey_parse_llm_enabled ? 'true' : 'false';
    const gParseBase = document.getElementById('adminGreyParseOpenaiBaseUrl');
    const gParseKey = document.getElementById('adminGreyParseOpenaiApiKey');
    const gParseMod = document.getElementById('adminGreyParseOpenaiModel');
    if (gParseBase) gParseBase.value = cfg.grey_openai_parse_base_url || '';
    if (gParseKey) {
        gParseKey.value = cfg.grey_openai_parse_api_key || '';
        gParseKey.placeholder = getVendorKeyPlaceholder(cfg.grey_openai_parse_base_url);
    }
    if (gParseMod) gParseMod.value = cfg.grey_openai_parse_model || cfg.grey_parse_llm_model || '';
    const gParseTimeout = document.getElementById('adminGreyParseTimeoutSeconds');
    if (gParseTimeout) gParseTimeout.value = (cfg.grey_parse_timeout_seconds != null ? cfg.grey_parse_timeout_seconds : '');
    const gParseRetry = document.getElementById('adminGreyParseMaxRetryRounds');
    if (gParseRetry) {
        const v = cfg.grey_openai_parse_max_retry_rounds != null ? cfg.grey_openai_parse_max_retry_rounds : (cfg.grey_parse_max_retry_rounds != null ? cfg.grey_parse_max_retry_rounds : '');
        gParseRetry.value = v != null ? v : '';
    }
    const gParseReasoning = document.getElementById('adminGreyParseOpenaiReasoningEffort');
    if (gParseReasoning) gParseReasoning.value = cfg.grey_openai_parse_reasoning_effort || '';

    // 灰测高级参数
    const setElVal = (id, val, fallback) => {
        const el = document.getElementById(id);
        if (el) el.value = (val !== null && val !== undefined && val !== '') ? val : fallback;
    };
    setElVal('adminGreyRecMaxTokens', cfg.grey_openai_rec_max_tokens, 4096);
    setElVal('adminGreyRecImageDetail', cfg.grey_openai_rec_image_detail, 'high');
    setElVal('adminGreyRecTemperature', cfg.grey_openai_rec_temperature !== undefined && cfg.grey_openai_rec_temperature !== null ? cfg.grey_openai_rec_temperature : 0.0, 0.0);
    setElVal('adminGreyAudMaxTokens', cfg.grey_openai_aud_max_tokens, 4096);
    setElVal('adminGreyAudImageDetail', 'auto', 'auto');
    setElVal('adminGreyAudTemperature', cfg.grey_openai_aud_temperature !== undefined && cfg.grey_openai_aud_temperature !== null ? cfg.grey_openai_aud_temperature : 0.0, 0.0);
    setElVal('adminGreyParseMaxTokens', cfg.grey_openai_parse_max_tokens, 2048);
    setElVal('adminGreyParseImageDetail', 'auto', 'auto');
    setElVal('adminGreyParseTemperature', cfg.grey_openai_parse_temperature !== undefined && cfg.grey_openai_parse_temperature !== null ? cfg.grey_openai_parse_temperature : 0.0, 0.0);

    // 金丝雀抽屉自动展开
    const greyDrawer = document.getElementById('adminGreyDrawer');
    if (greyDrawer) {
        const greyExpanded = !!cfg.grey_enabled;
        greyDrawer.classList.toggle('collapsed', !greyExpanded);
        const h = greyDrawer.querySelector('.engine-drawer-header');
        if (h) h.setAttribute('aria-expanded', greyExpanded ? 'true' : 'false');
    }
}

// 采集表单字段并构建保存 / 测试 body（不包含供应商自由文本；携带乐观锁 version）
function collectEngineConfigBody() {
    const getVal = (id) => {
        const el = document.getElementById(id);
        return el ? el.value.trim() : '';
    };
    const getTimeout = (id) => {
        const el = document.getElementById(id);
        if (!el || !el.value.trim()) return null;
        const v = parseInt(el.value.trim(), 10);
        return isNaN(v) ? null : v;
    };
    const getRetry = (id, fallback) => {
        const el = document.getElementById(id);
        if (!el || !el.value.trim()) return (fallback !== undefined ? fallback : null);
        const v = parseInt(el.value.trim(), 10);
        return isNaN(v) ? (fallback !== undefined ? fallback : null) : Math.max(0, Math.min(10, v));
    };
    const getInt = (id, fallback) => {
        const el = document.getElementById(id);
        if (!el || !el.value.trim()) return fallback;
        const v = parseInt(el.value.trim(), 10);
        return isNaN(v) ? fallback : v;
    };
    const getFloat = (id, fallback) => {
        const el = document.getElementById(id);
        if (!el || !el.value.trim()) return fallback;
        const v = parseFloat(el.value.trim());
        return isNaN(v) ? fallback : v;
    };
    const getPrice = (id, label) => {
        const el = document.getElementById(id);
        if (!el || !el.value.trim()) return null;
        const v = parseFloat(el.value.trim());
        if (isNaN(v)) return null;
        if (v < 0) {
            el.classList.add('is-invalid');
            throw new Error((label || 'Token 单价') + ' 不能为负数');
        }
        el.classList.remove('is-invalid');
        return v;
    };

    let priceFields;
    try {
        priceFields = {
            price_input_rec: getPrice('adminPriceInputRec', '常规识别 Input 价格'),
            price_cached_input_rec: getPrice('adminPriceCachedInputRec', '常规识别 Cached Input 价格'),
            price_output_rec: getPrice('adminPriceOutputRec', '常规识别 Output 价格'),
            price_input_aud: getPrice('adminPriceInputAud', '常规审核 Input 价格'),
            price_cached_input_aud: getPrice('adminPriceCachedInputAud', '常规审核 Cached Input 价格'),
            price_output_aud: getPrice('adminPriceOutputAud', '常规审核 Output 价格'),
            price_input_parse: getPrice('adminPriceInputParse', '常规解析 Input 价格'),
            price_cached_input_parse: getPrice('adminPriceCachedInputParse', '常规解析 Cached Input 价格'),
            price_output_parse: getPrice('adminPriceOutputParse', '常规解析 Output 价格'),
            price_input_grey_rec: getPrice('adminGreyPriceInputRec', '灰测识别 Input 价格'),
            price_cached_input_grey_rec: getPrice('adminGreyPriceCachedInputRec', '灰测识别 Cached Input 价格'),
            price_output_grey_rec: getPrice('adminGreyPriceOutputRec', '灰测识别 Output 价格'),
            price_input_grey_aud: getPrice('adminGreyPriceInputAud', '灰测审核 Input 价格'),
            price_cached_input_grey_aud: getPrice('adminGreyPriceCachedInputAud', '灰测审核 Cached Input 价格'),
            price_output_grey_aud: getPrice('adminGreyPriceOutputAud', '灰测审核 Output 价格'),
            price_input_grey_parse: getPrice('adminGreyPriceInputParse', '灰测解析 Input 价格'),
            price_cached_input_grey_parse: getPrice('adminGreyPriceCachedInputParse', '灰测解析 Cached Input 价格'),
            price_output_grey_parse: getPrice('adminGreyPriceOutputParse', '灰测解析 Output 价格'),
        };
    } catch (err) {
        showToast(err.message || 'Token 单价不能为负数', 'error');
        return null;
    }

    const recModel = getVal('adminOpenaiRecModel');
    const audModel = getVal('adminOpenaiAudModel');
    const parseModel = getVal('adminParseOpenaiModel');
    const greyRecModel = getVal('adminGreyOpenaiRecModel');
    const greyAudModel = getVal('adminGreyOpenaiAudModel');
    const greyParseModel = getVal('adminGreyParseOpenaiModel');

    // 敏感掩码传回后端保留，用户输入新值传新值，清空则传空串 "" 触发显式清空
    const getKeyVal = (id) => {
        const el = document.getElementById(id);
        if (!el) return undefined;
        return el.value.trim();
    };

    return {
        ...priceFields,
        // 乐观锁：来自 routing DTO；缺失时 undefined（JSON 序列化省略，后端按旧行为处理）
        version: (_engineConfigVersion != null ? _engineConfigVersion : undefined),

        recognition_model: recModel,
        openai_rec_base_url: getVal('adminOpenaiRecBaseUrl'),
        openai_rec_api_key: getKeyVal('adminOpenaiRecApiKey'),
        openai_rec_model: recModel,

        audit_enabled: document.getElementById('adminAuditEnabled') ? document.getElementById('adminAuditEnabled').value === 'true' : false,
        audit_model: audModel,
        openai_aud_base_url: getVal('adminOpenaiAudBaseUrl'),
        openai_aud_api_key: getKeyVal('adminOpenaiAudApiKey'),
        openai_aud_model: audModel,

        parse_llm_enabled: document.getElementById('adminParseEnabled') ? document.getElementById('adminParseEnabled').value === 'true' : false,
        parse_llm_model: parseModel,
        openai_parse_base_url: getVal('adminParseOpenaiBaseUrl'),
        openai_parse_api_key: getKeyVal('adminParseOpenaiApiKey'),
        openai_parse_model: parseModel,

        grey_enabled: document.getElementById('adminGreyEnabled') ? document.getElementById('adminGreyEnabled').value === 'true' : false,
        grey_percent: parseInt(document.getElementById('adminGreyPercent') ? document.getElementById('adminGreyPercent').value : 0, 10) || 0,
        grey_assign_mode: getVal('adminGreyAssignMode') || 'receipt',

        grey_recognition_model: greyRecModel,
        grey_openai_rec_base_url: getVal('adminGreyOpenaiRecBaseUrl'),
        grey_openai_rec_api_key: getKeyVal('adminGreyOpenaiRecApiKey'),
        grey_openai_rec_model: greyRecModel,

        grey_audit_enabled: document.getElementById('adminGreyAuditEnabled') ? document.getElementById('adminGreyAuditEnabled').value === 'true' : false,
        grey_audit_model: greyAudModel,
        grey_openai_aud_base_url: getVal('adminGreyOpenaiAudBaseUrl'),
        grey_openai_aud_api_key: getKeyVal('adminGreyOpenaiAudApiKey'),
        grey_openai_aud_model: greyAudModel,

        grey_parse_llm_enabled: document.getElementById('adminGreyParseEnabled') ? document.getElementById('adminGreyParseEnabled').value === 'true' : false,
        grey_parse_llm_model: greyParseModel,
        grey_openai_parse_base_url: getVal('adminGreyParseOpenaiBaseUrl'),
        grey_openai_parse_api_key: getKeyVal('adminGreyParseOpenaiApiKey'),
        grey_openai_parse_model: greyParseModel,

        // 分引擎腿超时、重试上限与思考强度
        rec_timeout_seconds: getTimeout('adminRecTimeoutSeconds'),
        openai_rec_max_retry_rounds: getRetry('adminRecMaxRetryRounds', 3),
        rec_max_retry_rounds: getRetry('adminRecMaxRetryRounds', 3),
        openai_rec_reasoning_effort: getVal('adminOpenaiRecReasoningEffort'),

        aud_timeout_seconds: getTimeout('adminAudTimeoutSeconds'),
        openai_aud_max_retry_rounds: getRetry('adminAudMaxRetryRounds', 1),
        aud_max_retry_rounds: getRetry('adminAudMaxRetryRounds', 1),
        openai_aud_reasoning_effort: getVal('adminOpenaiAudReasoningEffort'),

        parse_timeout_seconds: getTimeout('adminParseTimeoutSeconds'),
        openai_parse_max_retry_rounds: getRetry('adminParseMaxRetryRounds', 1),
        parse_max_retry_rounds: getRetry('adminParseMaxRetryRounds', 1),
        openai_parse_reasoning_effort: getVal('adminParseOpenaiReasoningEffort'),

        grey_rec_timeout_seconds: getTimeout('adminGreyRecTimeoutSeconds'),
        grey_openai_rec_max_retry_rounds: getRetry('adminGreyRecMaxRetryRounds', null),
        grey_rec_max_retry_rounds: getRetry('adminGreyRecMaxRetryRounds', null),
        grey_openai_rec_reasoning_effort: getVal('adminGreyOpenaiRecReasoningEffort'),

        grey_aud_timeout_seconds: getTimeout('adminGreyAudTimeoutSeconds'),
        grey_openai_aud_max_retry_rounds: getRetry('adminGreyAudMaxRetryRounds', null),
        grey_aud_max_retry_rounds: getRetry('adminGreyAudMaxRetryRounds', null),
        grey_openai_aud_reasoning_effort: getVal('adminGreyOpenaiAudReasoningEffort'),

        grey_parse_timeout_seconds: getTimeout('adminGreyParseTimeoutSeconds'),
        grey_openai_parse_max_retry_rounds: getRetry('adminGreyParseMaxRetryRounds', null),
        grey_parse_max_retry_rounds: getRetry('adminGreyParseMaxRetryRounds', null),
        grey_openai_parse_reasoning_effort: getVal('adminGreyParseOpenaiReasoningEffort'),

        // 高级参数 (Rec)
        openai_rec_max_tokens: getInt('adminRecMaxTokens', 4096),
        openai_rec_image_detail: getVal('adminRecImageDetail') || 'high',
        openai_rec_temperature: getFloat('adminRecTemperature', 0.0),
        // 高级参数 (Aud)
        openai_aud_max_tokens: getInt('adminAudMaxTokens', 4096),
        openai_aud_temperature: getFloat('adminAudTemperature', 0.0),
        // 高级参数 (Parse)
        openai_parse_max_tokens: getInt('adminParseMaxTokens', 4096),
        openai_parse_temperature: getFloat('adminParseTemperature', 0.0),
        // 高级参数 (Grey Rec)
        grey_openai_rec_max_tokens: getInt('adminGreyRecMaxTokens', 4096),
        grey_openai_rec_image_detail: getVal('adminGreyRecImageDetail') || 'high',
        grey_openai_rec_temperature: getFloat('adminGreyRecTemperature', 0.0),
        // 高级参数 (Grey Aud)
        grey_openai_aud_max_tokens: getInt('adminGreyAudMaxTokens', 4096),
        grey_openai_aud_temperature: getFloat('adminGreyAudTemperature', 0.0),
        // 高级参数 (Grey Parse)
        grey_openai_parse_max_tokens: getInt('adminGreyParseMaxTokens', 4096),
        grey_openai_parse_temperature: getFloat('adminGreyParseTemperature', 0.0),
    };
}

// 保存配置：携带乐观锁 version；409 提示「配置已被他人修改」+ 字段级 diff + 重新加载
function saveAdminEngineConfig() {
    const saveBtn = document.getElementById('adminSaveEngineBtn') || document.getElementById('btnSaveAdminEngine');
    const origText = saveBtn ? saveBtn.textContent : '保存配置';

    const body = collectEngineConfigBody();
    if (!body) return;

    if (saveBtn) {
        saveBtn.disabled = true;
        saveBtn.textContent = '正在保存配置...';
    }
    return apiFetch('/api/admin/engine-config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(res => res.json().then(data => ({ status: res.status, data: data })))
        .then(({ status, data: ret }) => {
            if (ret && ret.status === 'success') {
                showToast('配置已成功保存', 'success');
                // T1：管理台保存后来源置 manual（后端强制），重启不再被 .env 覆盖
                renderAdminEngineConfigSource('manual');
                if (ret.version != null) _engineConfigVersion = ret.version;
                const eng = (ret && ret.data) || {};
                const keyMap = {
                    adminOpenaiRecApiKey: eng.openai_rec_api_key,
                    adminOpenaiAudApiKey: eng.openai_aud_api_key,
                    adminParseOpenaiApiKey: eng.openai_parse_api_key,
                    adminGreyOpenaiRecApiKey: eng.grey_openai_rec_api_key,
                    adminGreyOpenaiAudApiKey: eng.grey_openai_aud_api_key,
                    adminGreyParseOpenaiApiKey: eng.grey_openai_parse_api_key
                };
                Object.entries(keyMap).forEach(([id, val]) => {
                    const el = document.getElementById(id);
                    if (el) {
                        if (val !== undefined && val !== null) {
                            el.value = val;
                        }
                    }
                });
                setEngineStatusMessage('配置已保存，生效版本 v' + (_engineConfigVersion != null ? _engineConfigVersion : '-'), 'success');
                loadEngineConfigRouting();
                loadEngineConfigHistory();
            } else if (status === 409 || (ret && ret.code === 'VERSION_CONFLICT')) {
                showEngineVersionConflictModal(ret);
            } else {
                const errMsg = (ret && (ret.msg || ret.detail)) || '保存失败';
                showToast(humanizeForbidden(errMsg) || errMsg, 'error');
            }
        })
        .catch(err => {
            showToast('保存异常: ' + (err.message || err), 'error');
        })
        .finally(() => {
            if (saveBtn) {
                saveBtn.disabled = false;
                saveBtn.textContent = origText;
            }
        });
}

// 409 并发冲突：字段级 diff + 「重新加载」
function showEngineVersionConflictModal(ret) {
    const data = ret || {};
    const diff = Array.isArray(data.diff) ? data.diff : [];
    const curV = (data.current_version != null ? data.current_version : (data.version != null ? data.version : '-'));
    let msg = '配置已被他人修改（服务器当前版本 v' + curV + '）。您的改动未保存。';
    if (diff.length) {
        msg += '\n\n字段差异（本机提交 → 服务器现值）：';
        diff.slice(0, 12).forEach(d => {
            const key = d && d.key ? d.key : '';
            const oldV = d && d.old !== undefined && d.old !== null ? d.old : '(空)';
            const newV = d && d.new !== undefined && d.new !== null ? d.new : '(空)';
            msg += '\n· ' + key + '：' + String(newV) + ' → ' + String(oldV);
        });
    }
    showToast('配置已被他人修改', 'error');
    setEngineStatusMessage('配置已被他人修改，请重新加载后再试', 'error');
    showCustomConfirmModal({
        title: '配置已被他人修改',
        message: msg,
        confirmText: '重新加载',
        cancelText: '知道了',
        onConfirm: () => { loadAdminEngineConfig(); },
        onCancel: () => {}
    });
}

// 独立「测试连通性」按钮：不再与保存隐式串行等待
function testAdminEngineConnectivity() {
    const btn = document.getElementById('adminTestEngineConnBtn');
    const origText = btn ? btn.textContent : '测试连通性';
    const body = collectEngineConfigBody();
    if (!body) return;

    if (btn) {
        btn.disabled = true;
        btn.textContent = '正在测试连通性...';
    }
    setEngineStatusMessage('正在测试连通性，最长需 45s...', 'info');
    apiFetch('/api/admin/test-engine-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    })
        .then(res => res.json().then(data => ({ status: res.status, data: data })))
        .then(({ status, data: ret }) => {
            if (status === 200 && ret && ret.status === 'success') {
                showToast('连通性测试通过', 'success');
                setEngineStatusMessage('连通性测试通过', 'success');
            } else {
                let errMsg = (ret && (ret.msg || ret.detail)) || '连接测试未通过';
                errMsg = humanizeForbidden(errMsg, status) || errMsg;
                showToast(errMsg, 'error');
                setEngineStatusMessage('连通性测试未通过：' + errMsg, 'error');
            }
        })
        .catch(err => {
            showToast('测试请求异常: ' + (err.message || err), 'error');
            setEngineStatusMessage('连通性测试请求异常', 'error');
        })
        .finally(() => {
            if (btn) {
                btn.disabled = false;
                btn.textContent = origText;
            }
        });
}

function renderAdminEngineConfigSource(source) {
    const isManual = String(source || 'auto') === 'manual';
    const resetBtn = document.getElementById('adminResetEngineConfigBtn');
    if (resetBtn) resetBtn.disabled = !isManual;
}

// 复位为 .env 默认装配：POST /api/admin/engine-config/reset（T1）
function resetAdminEngineConfig() {
    showCustomConfirmModal({
        title: '复位为 .env 默认装配',
        message: '确认复位吗？\n\n复位将：\n① 把配置来源改回 .env 自动装配\n② 立即按 .env 重新装配识别腿与审核腿\n③ 当前管理台手工保存的引擎参数将被 .env 值覆盖',
        confirmText: '确认复位',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/admin/engine-config/reset', { method: 'POST' })
                .then(r => r.json())
                .then(ret => {
                    if (!ret || ret.status !== 'success') {
                        showToast((ret && (ret.msg || ret.detail)) || '复位失败', 'error');
                        return;
                    }
                    showToast('已复位为 .env 默认装配', 'success');
                    loadAdminEngineConfig();
                })
                .catch(err => { showToast('复位请求异常', 'error'); console.error(err); });
        },
        onCancel: () => { showToast('已取消复位', 'info'); }
    });
}

function updateAuditDisabledState() {
    const enabled = document.getElementById('adminAuditEnabled') ? document.getElementById('adminAuditEnabled').value === 'true' : false;
    const box = document.getElementById('adminOpenaiAuditBox');
    if (box) {
        box.querySelectorAll('input, select').forEach(el => { el.disabled = !enabled; });
        box.style.opacity = enabled ? '1' : '0.45';
        box.style.pointerEvents = enabled ? 'auto' : 'none';
    }
    if (enabled) {
        const card = document.getElementById('adminAuditCard');
        if (card && card.classList.contains('collapsed')) {
            toggleEngineCard('aud');
        }
    }
}

function updateGreyAuditDisabledState() {
    const enabled = document.getElementById('adminGreyAuditEnabled') ? document.getElementById('adminGreyAuditEnabled').value === 'true' : false;
    const box = document.getElementById('adminGreyOpenaiAudBox');
    if (box) {
        box.querySelectorAll('input, select').forEach(el => {
            if (el.id !== 'adminGreyAuditEnabled') {
                el.disabled = !enabled;
            }
        });
        box.style.opacity = enabled ? '1' : '0.55';
    }
    const badge = document.getElementById('trxLegAudBadge');
    if (badge) {
        badge.textContent = enabled ? '[开]' : '[关]';
        badge.style.color = enabled ? '#16a34a' : '#94a3b8';
    }
}
window.updateGreyAuditDisabledState = updateGreyAuditDisabledState;

function updateGreyDisabledState() {
    const enabled = document.getElementById('adminGreyEnabled') ? document.getElementById('adminGreyEnabled').value === 'true' : false;
    const body = document.getElementById('adminGreyBody');
    if (body) {
        body.querySelectorAll('input, select, button').forEach((el) => {
            if (el.classList.contains('trx-leg-tab')) return;
            el.disabled = !enabled;
        });
        body.style.opacity = enabled ? '1' : '0.65';
        body.style.pointerEvents = enabled ? 'auto' : 'none';
        const switcher = body.querySelector('.trx-leg-switcher');
        if (switcher) switcher.style.pointerEvents = 'auto';
        if (enabled) {
            updateGreyAuditDisabledState();
            updateGreyParseDisabledState();
        }
    }
    if (typeof updateTrxGlobalHeader === 'function') {
        updateTrxGlobalHeader();
    }
}
window.updateGreyDisabledState = updateGreyDisabledState;

function updateParseDisabledState() {
    const enabled = document.getElementById('adminParseEnabled') ? document.getElementById('adminParseEnabled').value === 'true' : false;
    const box = document.getElementById('adminParseOpenaiBox');
    if (box) {
        box.querySelectorAll('input, select').forEach(el => { el.disabled = !enabled; });
        box.style.opacity = enabled ? '1' : '0.45';
        box.style.pointerEvents = enabled ? 'auto' : 'none';
    }
    if (enabled) {
        const card = document.getElementById('adminParseDrawer');
        if (card && card.classList.contains('collapsed')) {
            toggleEngineCard('parse');
        }
    }
}

function updateGreyParseDisabledState() {
    const enabled = document.getElementById('adminGreyParseEnabled') ? document.getElementById('adminGreyParseEnabled').value === 'true' : false;
    const box = document.getElementById('adminGreyParseBox') || document.getElementById('adminGreyParseOpenaiBox');
    if (box) {
        box.querySelectorAll('input, select').forEach(el => {
            if (el.id !== 'adminGreyParseEnabled') {
                el.disabled = !enabled;
            }
        });
        box.style.opacity = enabled ? '1' : '0.55';
    }
    const badge = document.getElementById('trxLegParseBadge');
    if (badge) {
        badge.textContent = enabled ? '[开]' : '[关]';
        badge.style.color = enabled ? '#16a34a' : '#94a3b8';
    }
}
window.updateGreyParseDisabledState = updateGreyParseDisabledState;

function switchGreyLegTab(leg) {
    const container = document.getElementById('adminGreyLegsContainer');
    const recBox = document.getElementById('adminGreyOpenaiRecBox');
    const audBox = document.getElementById('adminGreyOpenaiAudBox');
    const parseBox = document.getElementById('adminGreyParseBox') || document.getElementById('adminGreyParseOpenaiBox');
    
    const tabAll = document.getElementById('tabBtn_grey_all');
    const tabRec = document.getElementById('tabBtn_grey_rec');
    const tabAud = document.getElementById('tabBtn_grey_aud');
    const tabParse = document.getElementById('tabBtn_grey_parse');

    [tabAll, tabRec, tabAud, tabParse].forEach(t => { if (t) t.classList.remove('active'); });

    if (leg === 'grey_rec') {
        if (tabRec) tabRec.classList.add('active');
        if (container) {
            container.style.display = 'block';
        }
        if (recBox) recBox.style.display = 'block';
        if (audBox) audBox.style.display = 'none';
        if (parseBox) parseBox.style.display = 'none';
    } else if (leg === 'grey_aud') {
        if (tabAud) tabAud.classList.add('active');
        if (container) {
            container.style.display = 'block';
        }
        if (recBox) recBox.style.display = 'none';
        if (audBox) audBox.style.display = 'block';
        if (parseBox) parseBox.style.display = 'none';
    } else if (leg === 'grey_parse') {
        if (tabParse) tabParse.classList.add('active');
        if (container) {
            container.style.display = 'block';
        }
        if (recBox) recBox.style.display = 'none';
        if (audBox) audBox.style.display = 'none';
        if (parseBox) parseBox.style.display = 'block';
    } else {
        // 'all' / 默认全景矩阵
        if (tabAll) tabAll.classList.add('active');
        if (container) {
            container.style.display = 'grid';
            container.style.gridTemplateColumns = 'repeat(3, minmax(0, 1fr))';
            container.style.gap = '12px';
        }
        if (recBox) recBox.style.display = 'block';
        if (audBox) audBox.style.display = 'block';
        if (parseBox) parseBox.style.display = 'block';
    }
}
window.switchGreyLegTab = switchGreyLegTab;

// ---------------------------------- 推全 / 回滚 / 对比 ----------------------------------


// 灰测↔常规配置对比：GET /api/admin/engine-config/diff
function fetchAndRenderDiff(onRendered) {
    const panel = document.getElementById('adminGreyDiffPanel');
    if (panel) {
        panel.classList.remove('hide');
        panel.dataset.loading = '1';
    }
    apiFetch('/api/admin/engine-config/diff')
        .then(r => r.json())
        .then(body => {
            if (!body || body.status !== 'success') {
                showToast((body && body.msg) || '读取对比失败', 'warning');
                if (panel) panel.classList.add('hide');
                if (onRendered) onRendered(false);
                return;
            }
            const { diffs, totalPairs, diffCount } = body.data || {};
            renderGreyDiffPanel({ diffs, totalPairs, diffCount });
            if (panel) panel.dataset.loading = '0';
            if (onRendered) onRendered(true);
        })
        .catch(err => {
            console.error('diff 请求异常', err);
            showToast('对比请求异常', 'error');
            if (panel) panel.classList.add('hide');
            if (onRendered) onRendered(false);
        });
}

// 渲染双列对照表：差异行高亮，敏感字段打码展示
function renderGreyDiffPanel({ diffs, totalPairs, diffCount }) {
    const panel = document.getElementById('adminGreyDiffPanel');
    if (!panel) return;
    const wrap = panel.querySelector('.diff-wrap');
    if (!wrap) return;

    const header = document.getElementById('adminGreyDiffHeader');
    if (header) {
        header.innerHTML = '配置对比 · 常规 <span style="color:#1565c0;">vs</span> 灰测 '
            + (diffCount > 0
                ? `<span style="color:#b8860b;">发现 <strong>${diffCount}</strong> 项差异（共 ${totalPairs} 项）</span>`
                : '<span style="color:#2f6b4f;">两组配置完全一致</span>');
    }

    let html = '<table class="diff-table"><thead><tr><th>配置项</th><th>常规组</th><th>灰测组</th><th>状态</th></tr></thead><tbody>';
    if (Array.isArray(diffs) && diffs.length > 0) {
        diffs.forEach(d => {
            const isDiff = !d.identical;
            const rowCls = isDiff ? 'diff-row' : '';
            const sensitive = d.sensitive;
            const reg = sensitive ? (d.regular || '-') : `<code>${escapeHtml(String(d.regular || '-'))}</code>`;
            const grey = sensitive ? (d.grey || '-') : `<code>${escapeHtml(String(d.grey || '-'))}</code>`;
            const status = isDiff ? '<span class="badge" style="color:#b8860b;">差异</span>' : '<span class="badge" style="color:#2f6b4f;">一致</span>';
            html += `<tr class="${rowCls}"><td>${escapeHtml(d.label)}</td><td>${reg}</td><td>${grey}</td><td>${status}</td></tr>`;
        });
    } else {
        html += '<tr><td colspan="4" style="text-align:center; color:var(--text-muted);">无差异项</td></tr>';
    }
    html += '</tbody></table>';
    wrap.innerHTML = html;
}

// 一键推全：PUT /api/admin/engine-config/promote
function promoteGreyConfig() {
    showCustomConfirmModal({
        title: '一键推全：灰测组 → 常规组',
        message: '确认将当前灰测组配置覆盖为常规组吗？\n\n推全将：\n① 记录当前常规组配置作为回滚快照\n② 将灰测组引擎/模型/OpenAI 参数/解析 LLM 全部覆盖到常规组\n③ 灰测组随后清空为默认态并停用灰测\n\n推全后如需回滚，可在同面板点击「一键回滚」。',
        confirmText: '确认推全',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/admin/engine-config/promote', { method: 'PUT', headers: { 'Content-Type': 'application/json' } })
                .then(r => r.json())
                .then(body => {
                    if (!body || body.status !== 'success') {
                        showToast((body && body.msg) || '推全失败', 'error');
                        return;
                    }
                    showToast('推全成功：灰测组已覆盖为常规组，快照已保存', 'success');
                    const panel = document.getElementById('adminGreyDiffPanel');
                    if (panel) panel.classList.add('hide');
                    loadAdminEngineConfig();
                })
                .catch(err => { showToast('推全请求异常', 'error'); console.error(err); });
        },
        onCancel: () => { showToast('已取消推全', 'info'); }
    });
}

// 回滚前先渲染一次 Diff（推全确认步骤）：默认直接走 fetch+render
function preparePromoteWithDiff() {
    fetchAndRenderDiff((ok) => {
        if (!ok) {
            // 若灰测未启用 → 直接提示
            showToast('灰测未启用，无法推全', 'warning');
            return;
        }
        const panel = document.getElementById('adminGreyDiffPanel');
        const confirmBtn = document.getElementById('adminGreyPromoteConfirm');
        if (confirmBtn) {
            confirmBtn.classList.remove('hide');
            confirmBtn.onclick = () => {
                promoteGreyConfig();
            };
        }
    });
}

// 一键回滚：PUT /api/admin/engine-config/rollback
function rollbackEngineConfig() {
    showCustomConfirmModal({
        title: '一键回滚：恢复上次推全前的常规组',
        message: '确认回滚到上一次推全前的配置吗？\n\n回滚将：\n① 用快照中的常规组配置覆盖当前常规组\n② 清空回滚快照（一次快照，回滚后不再可回）',
        confirmText: '确认回滚',
        cancelText: '取消',
        onConfirm: () => {
            apiFetch('/api/admin/engine-config/rollback', { method: 'PUT', headers: { 'Content-Type': 'application/json' } })
                .then(r => r.json())
                .then(body => {
                    if (!body || body.status !== 'success') {
                        showToast((body && body.msg) || '回滚失败', 'error');
                        return;
                    }
                    showToast('回滚成功', 'success');
                    const panel = document.getElementById('adminGreyDiffPanel');
                    if (panel) panel.classList.add('hide');
                    loadAdminEngineConfig();
                })
                .catch(err => { showToast('回滚请求异常', 'error'); console.error(err); });
        },
        onCancel: () => { showToast('已取消回滚', 'info'); }
    });
}

/* =============================================================
   SaaS 级流量调度中枢与预设模型定义 (View 02 Traffic & Experiments)
   ============================================================= */
const EXP_PRESET_MODELS = {
    agnes: {
        label: 'Agnes AI',
        base_url: 'https://apihub.agnes-ai.com/v1',
        api_key: '',
        rec_model: 'agnes-2.0-flash',
        aud_model: 'agnes-2.0-flash',
    },
    bailian: {
        label: '阿里云百炼 · DashScope',
        base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
        api_key: '',
        rec_model: 'qwen3.5-omni-flash',
        aud_model: 'qwen3-vl-plus',
    },
    siliconflow: {
        label: 'SiliconFlow · 硅基流动',
        base_url: 'https://api.siliconflow.cn/v1',
        api_key: '',
        rec_model: 'Qwen/Qwen3-VL-32B-Instruct',
        aud_model: 'zai-org/GLM-4.5V',
    },
};

function applyExpPreset(side, presetKey) {
    let baseUrlEl, apiKeyEl, modelEl;
    if (side === 'ctrl') {
        baseUrlEl = document.getElementById('newExpCtrlBaseUrl');
        apiKeyEl = document.getElementById('newExpCtrlApiKey');
        modelEl = document.getElementById('newExpControlModel');
    } else {
        baseUrlEl = document.getElementById('newExpTreatBaseUrl');
        apiKeyEl = document.getElementById('newExpTreatApiKey');
        modelEl = document.getElementById('newExpTreatmentModel');
    }

    if (presetKey === 'current_prod') {
        const prodBase = document.getElementById('adminOpenaiRecBaseUrl');
        const prodModel = document.getElementById('adminOpenaiRecModel');
        const prodKey = document.getElementById('adminOpenaiRecApiKey');
        if (baseUrlEl) baseUrlEl.value = (prodBase && prodBase.value.trim()) ? prodBase.value.trim() : 'https://dashscope.aliyuncs.com/compatible-mode/v1';
        if (modelEl) modelEl.value = (prodModel && prodModel.value.trim()) ? prodModel.value.trim() : 'qwen3.5-omni-flash';
        if (apiKeyEl && prodKey && prodKey.value.trim()) apiKeyEl.value = prodKey.value.trim();
        return;
    }

    const preset = EXP_PRESET_MODELS[presetKey];
    if (!preset) return;

    if (baseUrlEl) baseUrlEl.value = preset.base_url || '';
    if (modelEl) modelEl.value = preset.rec_model || '';
    if (apiKeyEl && preset.api_key) apiKeyEl.value = preset.api_key;
}
window.applyExpPreset = applyExpPreset;

function syncGreySplitSlider(val) {
    const numInput = document.getElementById('adminGreyPercent');
    const slider = document.getElementById('adminGreySlider');
    const ratioProd = document.getElementById('trxRatioProd');
    const ratioCanary = document.getElementById('trxRatioCanary');
    let percent = parseInt(val, 10);
    if (isNaN(percent)) percent = 0;
    if (percent < 0) percent = 0;
    if (percent > 100) percent = 100;

    if (numInput && parseInt(numInput.value, 10) !== percent) {
        numInput.value = percent;
    }
    if (slider && parseInt(slider.value, 10) !== percent) {
        slider.value = percent;
    }
    const canaryPct = percent;
    const prodPct = 100 - canaryPct;
    if (ratioProd) {
        ratioProd.style.width = prodPct + '%';
        ratioProd.textContent = '基线生产 ' + prodPct + '%';
    }
    if (ratioCanary) {
        ratioCanary.style.width = canaryPct + '%';
        ratioCanary.textContent = '金丝雀 ' + canaryPct + '%';
    }
    updateTrxGlobalHeader();
}
window.syncGreySplitSlider = syncGreySplitSlider;

function bindTrxSliderEvents() {
    const numInput = document.getElementById('adminGreyPercent');
    const slider = document.getElementById('adminGreySlider');
    if (slider && !slider._trxBound) {
        slider._trxBound = true;
        slider.addEventListener('input', function() {
            syncGreySplitSlider(this.value);
        });
        slider.addEventListener('change', function() {
            syncGreySplitSlider(this.value);
        });
    }
    if (numInput && !numInput._trxBound) {
        numInput._trxBound = true;
        numInput.addEventListener('input', function() {
            syncGreySplitSlider(this.value);
        });
        numInput.addEventListener('change', function() {
            syncGreySplitSlider(this.value);
        });
    }
}
window.bindTrxSliderEvents = bindTrxSliderEvents;

function onGreyAssignModeChange(mode) {
    const expEl = document.getElementById('trxAssignModeExplanation');
    if (!expEl) return;
    if (mode === 'vendor') {
        expEl.textContent = '[INFO] 供应商哈希：相同供应商一律路由至同一模型，避免同一商户在对账期出现不同模型的识别偏差，推荐生产放量与商户一致性体验保障。';
    } else {
        expEl.textContent = '[INFO] 单据随机：按单据 ID 哈希分流，流量均匀离散，适合全局快速探索与大样本随机分布。';
    }
}
window.onGreyAssignModeChange = onGreyAssignModeChange;

function updateTrxGlobalHeader() {
    const baseRecModInput = document.getElementById('adminOpenaiRecModel');
    const baseBadge = document.getElementById('trxStatusBaselineModel');
    if (baseBadge) {
        const m = (baseRecModInput && baseRecModInput.value.trim()) ? baseRecModInput.value.trim() : 'gpt-4o-mini';
        baseBadge.textContent = m;
    }

    const greyEnabled = document.getElementById('adminGreyEnabled') && document.getElementById('adminGreyEnabled').value === 'true';
    const greyPercentEl = document.getElementById('adminGreyPercent');
    const pct = greyPercentEl ? (parseInt(greyPercentEl.value, 10) || 0) : 0;
    const canaryBadge = document.getElementById('trxStatusCanaryBadge');
    const switchLabel = document.getElementById('trxGreySwitchLabel');
    if (canaryBadge) {
        if (greyEnabled) {
            canaryBadge.textContent = `灰度放量中（${pct}%）`;
            canaryBadge.className = 'trx-badge trx-badge-warning';
        } else {
            canaryBadge.textContent = '灰度未开启';
            canaryBadge.className = 'trx-badge trx-badge-secondary';
        }
    }
    if (switchLabel) {
        switchLabel.textContent = greyEnabled ? '已开启' : '已关闭';
        switchLabel.className = greyEnabled ? 'trx-tag trx-tag-warning' : 'trx-tag trx-tag-secondary';
    }

    const expBadge = document.getElementById('trxStatusExpBadge');
    if (expBadge) {
        const runningCount = (_adminExperimentsList || []).filter(e => e.status === 'running').length;
        if (runningCount > 0) {
            expBadge.textContent = `进行中的对比实验：${runningCount} 个`;
            expBadge.className = 'trx-badge trx-badge-info';
        } else {
            expBadge.textContent = '没有进行中的对比实验';
            expBadge.className = 'trx-badge trx-badge-secondary';
        }
    }

    const topoText = document.getElementById('trxTopoSplitText');
    if (topoText) {
        if (greyEnabled) {
            topoText.textContent = `${100 - pct}% 生产基线 / ${pct}% 候选灰度`;
        } else {
            topoText.textContent = '100% 生产基线 / 0% 候选灰度';
        }
    }
}
window.updateTrxGlobalHeader = updateTrxGlobalHeader;

function refreshTrxCockpitHeader() {
    updateTrxGlobalHeader();
    if (typeof loadAdminExperimentsList === 'function') {
        loadAdminExperimentsList();
    }
}
window.refreshTrxCockpitHeader = refreshTrxCockpitHeader;

async function updateAdminExpDetailMetrics(expId, targetN) {
    if (!expId) return;
    try {
        const res = await apiFetch('/api/admin/experiments/' + expId);
        const data = await res.json();
        if (data && data.status === 'success' && data.metrics) {
            const m = data.metrics;
            const cN = (m.control && m.control.n) || 0;
            const tN = (m.treatment && m.treatment.n) || 0;
            const curN = Math.min(cN, tN);
            const reqN = (targetN != null) ? Number(targetN) : 30;
            const progTextEl = document.getElementById('adminExpProgressText');
            const progBarEl = document.getElementById('adminExpProgressBar');
            if (reqN <= 0) {
                if (progTextEl) progTextEl.textContent = '[无门槛要求 (100%)]';
                if (progBarEl) {
                    progBarEl.style.width = '100%';
                    progBarEl.style.background = '#16a34a';
                }
            } else {
                const pct = Math.min(100, Math.round((curN / reqN) * 1000) / 10);
                if (progTextEl) {
                    if (curN >= reqN) {
                        progTextEl.textContent = `[已达统计门槛 (${pct.toFixed(1)}%)]`;
                    } else {
                        progTextEl.textContent = `[样本积累中 ${curN} / ${reqN} (${pct.toFixed(1)}%)]`;
                    }
                }
                if (progBarEl) {
                    progBarEl.style.width = pct + '%';
                    progBarEl.style.background = (curN >= reqN) ? '#16a34a' : '#2563eb';
                }
            }

            const metric = (data.experiment && data.experiment.success_metric) || 'accuracy';
            const test = m.tests && m.tests[metric];
            const sumEl = document.getElementById('adminExpSignificanceSummary');
            const badgeEl = document.getElementById('adminExpPValueBadge');
            if (test && test.p_value != null) {
                const pval = Number(test.p_value);
                const pStr = pval < 0.001 ? 'p < 0.001' : `p = ${pval.toFixed(4)}`;
                if (badgeEl) {
                    badgeEl.textContent = `p-value: ${pStr}`;
                    badgeEl.className = pval < 0.05 ? 'badge badge-success' : 'badge badge-secondary';
                }
                if (sumEl) {
                    sumEl.textContent = pval < 0.05
                        ? '统计显著性达成 (p < 0.05): 两组差异具有统计学显著意义'
                        : '未达显著性水平 (p >= 0.05): 建议继续积累样本观测';
                }
            } else {
                if (badgeEl) {
                    badgeEl.textContent = 'p-value: -';
                    badgeEl.className = 'badge badge-secondary';
                }
                if (sumEl) {
                    sumEl.textContent = '统计显著性状态: 样本收集推进中，满足最小样本量后触发统计推断';
                }
            }
        }
    } catch (e) {
        // 静默
    }
}

/* =============================================================
   A/B 科学实验编排抽屉交互控制 (A/B Experimentation Drawer)
   ============================================================= */
// A/B 实验状态唯一事实源：label=展示名，cls=徽章类（管理抽屉），style=内联配色（大盘/详情）。
// draft/running/stopped/concluded 与后端 db.py Experiment.status 取值同源；
// 文案跟随操作按钮（index.html「暂停实验」）与说明文案（「实验处于运行中」）口径。
const EXP_STATUS = {
    draft:     { label: '草稿',   cls: 'badge-secondary', style: 'background:#f1f5f9; color:#475569;' },
    running:   { label: '运行中', cls: 'badge-success',   style: 'background:#dbeafe; color:#1e40af;' },
    stopped:   { label: '已暂停', cls: 'badge-warning',   style: 'background:#fee2e2; color:#991b1b;' },
    concluded: { label: '已结题', cls: 'badge-info',      style: 'background:#dcfce7; color:#166534;' },
};

// 取实验状态展示名；未知状态回落显示原始值（与后端不一致时可肉眼发现），
// 原始值也为空时用 fallback（各调用点保留自己的占位符）。
function expStatusLabel(s, fallback) {
    const spec = EXP_STATUS[String(s || '')];
    if (spec) return spec.label;
    return String(s || fallback || '未知');
}

// 取实验状态内联配色；未知状态返回空串（调用点 style 属性保持原样）
function expStatusStyle(s) {
    const spec = EXP_STATUS[String(s || '')];
    return spec ? spec.style : '';
}

let _adminExperimentsList = [];

async function loadAdminExperimentsList() {
    const sel = document.getElementById('adminExpSelect');
    try {
        const res = await apiFetch('/api/admin/experiments');
        const data = await res.json();
        if (data && data.status === 'success' && Array.isArray(data.data)) {
            _adminExperimentsList = data.data;
            if (typeof window !== 'undefined') window._adminExperimentsList = _adminExperimentsList;
            if (typeof renderEngineOverview === 'function') {
                renderEngineOverview();
            }
            if (sel) {
                sel.innerHTML = '';
                if (_adminExperimentsList.length === 0) {
                    sel.innerHTML = '<option value="">暂无实验 (请点击新建)</option>';
                    onAdminExpSelectChange('');
                    return;
                }
                _adminExperimentsList.forEach(exp => {
                    const opt = document.createElement('option');
                    opt.value = exp.id;
                    const statusLabel = expStatusLabel(exp.status);
                    opt.innerHTML = `#${exp.id}: ${w2Escape(exp.name || '未命名')} (${statusLabel})`;
                    sel.appendChild(opt);
                });
                let activeId = _adminExperimentsList[0].id;
                const prevSelected = sel.getAttribute('data-selected-id');
                if (prevSelected) {
                    const found = _adminExperimentsList.find(e => String(e.id) === String(prevSelected));
                    if (found) activeId = found.id;
                }
                sel.value = String(activeId);
                onAdminExpSelectChange(activeId);
            }
        } else if (sel) {
            sel.innerHTML = '<option value="">加载实验列表失败</option>';
        }
    } catch (err) {
        console.error('loadAdminExperimentsList error:', err);
        if (sel) {
            sel.innerHTML = '<option value="">加载实验列表异常</option>';
        }
    }
}

function onAdminExpSelectChange(expId) {
    const sel = document.getElementById('adminExpSelect');
    if (sel && expId) sel.setAttribute('data-selected-id', String(expId));

    const hypEl = document.getElementById('adminExpHypothesis');
    const metricEl = document.getElementById('adminExpMetric');
    const trafficEl = document.getElementById('adminExpTraffic');
    const minSampleEl = document.getElementById('adminExpMinSample');
    const badgeEl = document.getElementById('adminExpStatusBadge');
    const ctrlEl = document.getElementById('adminExpCtrlModel');
    const treatEl = document.getElementById('adminExpTreatModel');
    const startBtn = document.getElementById('adminExpStartBtn');
    const stopBtn = document.getElementById('adminExpStopBtn');
    const progTextEl = document.getElementById('adminExpProgressText');
    const progBarEl = document.getElementById('adminExpProgressBar');
    const sumEl = document.getElementById('adminExpSignificanceSummary');
    const pValueBadge = document.getElementById('adminExpPValueBadge');

    const exp = _adminExperimentsList.find(e => String(e.id) === String(expId));
    if (!exp) {
        if (hypEl) hypEl.textContent = '-';
        if (metricEl) metricEl.textContent = '-';
        if (trafficEl) trafficEl.textContent = '-';
        if (minSampleEl) minSampleEl.textContent = '-';
        if (badgeEl) {
            badgeEl.className = 'badge badge-secondary';
            badgeEl.textContent = '-';
        }
        if (ctrlEl) ctrlEl.textContent = '生产主力引擎';
        if (treatEl) treatEl.textContent = '待测对比模型';
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = true;
        if (progTextEl) progTextEl.textContent = '[样本积累中 0 / 30 (0.0%)]';
        if (progBarEl) progBarEl.style.width = '0%';
        if (sumEl) sumEl.textContent = '统计显著性状态: 样本收集推进中，满足最小样本量后触发统计推断';
        if (pValueBadge) { pValueBadge.textContent = 'p-value: -'; pValueBadge.className = 'badge badge-secondary'; }
        updateTrxGlobalHeader();
        return;
    }

    if (hypEl) hypEl.textContent = exp.hypothesis || '未设定实验假设';

    const METRIC_LABELS = {
        accuracy: 'accuracy (准确率)',
        hallucination_rate: 'hallucination_rate (幻觉率)',
        edit_rate: 'edit_rate (人工修改率)'
    };
    if (metricEl) metricEl.textContent = METRIC_LABELS[exp.success_metric] || exp.success_metric || 'accuracy (准确率)';

    const treatPct = (exp.target_percent != null) ? Number(exp.target_percent) : 50;
    const ctrlPct = 100 - treatPct;
    if (trafficEl) trafficEl.textContent = `${ctrlPct}% Control : ${treatPct}% Treatment`;

    // S2：min_sample=0（不做样本量门槛）是合法值，不能因 0 是 falsy 而显示成 30
    if (minSampleEl) minSampleEl.textContent = (exp.min_sample != null) ? `${exp.min_sample} 样本/组` : '30 样本/组';

    // 状态徽标与操作控制
    const status = exp.status || 'draft';
    if (badgeEl) {
        const spec = EXP_STATUS[status] || EXP_STATUS.draft;
        badgeEl.className = 'badge ' + spec.cls;
        badgeEl.textContent = spec.label;
    }

    // 模型信息
    const baseRecModInput = document.getElementById('adminOpenaiRecModel');
    let snapshot = exp.grey_snapshot;
    if (typeof snapshot === 'string') {
        try { snapshot = JSON.parse(snapshot); } catch (e) { snapshot = {}; }
    }
    const baselineModel = (snapshot && snapshot.control_model)
        ? snapshot.control_model
        : ((baseRecModInput && baseRecModInput.value.trim()) ? baseRecModInput.value.trim() : '生产主力引擎');
    if (ctrlEl) ctrlEl.textContent = baselineModel;

    const treatmentModel = (snapshot && snapshot.treatment_model) ? snapshot.treatment_model : '待测对比模型';
    if (treatEl) treatEl.textContent = treatmentModel;

    // 样本进度与显著性初始状态
    const minN = (exp.min_sample != null) ? Number(exp.min_sample) : 30;
    if (minN <= 0) {
        if (progTextEl) progTextEl.textContent = '[无门槛要求 (100%)]';
        if (progBarEl) { progBarEl.style.width = '100%'; progBarEl.style.background = '#16a34a'; }
    } else {
        if (progTextEl) progTextEl.textContent = `[样本积累中 0 / ${minN} (0.0%)]`;
        if (progBarEl) { progBarEl.style.width = '0%'; progBarEl.style.background = '#2563eb'; }
    }
    if (sumEl) sumEl.textContent = '统计显著性状态: 样本收集推进中，满足最小样本量后触发统计推断';
    if (pValueBadge) { pValueBadge.textContent = 'p-value: -'; pValueBadge.className = 'badge badge-secondary'; }

    // 异步刷新即时样本指标
    updateAdminExpDetailMetrics(expId, exp.min_sample);
    updateTrxGlobalHeader();

    // 按钮禁用控制
    if (startBtn) {
        startBtn.disabled = (status === 'running' || status === 'concluded');
    }
    if (stopBtn) {
        stopBtn.disabled = (status !== 'running');
    }
}

async function startCurrentExperiment() {
    const sel = document.getElementById('adminExpSelect');
    const expId = sel ? sel.value : null;
    if (!expId) {
        showToast('请先选择要启动的实验', 'warning');
        return;
    }
    try {
        const res = await apiFetch('/api/admin/experiments/' + expId + '/start', { method: 'POST' });
        const data = await res.json();
        if (data && data.status === 'success') {
            showToast('实验 #' + expId + ' 已成功启动！', 'success');
            await loadAdminExperimentsList();
        } else {
            showToast('启动实验失败：' + ((data && (data.msg || data.detail)) || '未知错误'), 'error');
        }
    } catch (err) {
        console.error('startCurrentExperiment error:', err);
        showToast('启动实验网络异常', 'error');
    }
}

async function stopCurrentExperiment() {
    const sel = document.getElementById('adminExpSelect');
    const expId = sel ? sel.value : null;
    if (!expId) {
        showToast('请先选择要暂停的实验', 'warning');
        return;
    }
    try {
        const res = await apiFetch('/api/admin/experiments/' + expId + '/stop', { method: 'POST' });
        const data = await res.json();
        if (data && data.status === 'success') {
            showToast('实验 #' + expId + ' 已暂停！', 'info');
            await loadAdminExperimentsList();
        } else {
            showToast('暂停实验失败：' + ((data && (data.msg || data.detail)) || '未知错误'), 'error');
        }
    } catch (err) {
        console.error('stopCurrentExperiment error:', err);
        showToast('暂停实验网络异常', 'error');
    }
}

function openCreateExperimentModal() {
    const modal = document.getElementById('createExperimentModal');
    if (modal) {
        modal.classList.remove('hide');
        modal.style.display = 'flex';
        // 绑定点击遮罩关闭事件（仅首次绑定）
        if (!modal._hasBackdropListener) {
            modal._hasBackdropListener = true;
            modal.addEventListener('click', function(e) {
                if (e.target === modal) closeCreateExperimentModal();
            });
        }
        // 预设默认选择与初始填充
        const ctrlPresetEl = document.getElementById('newExpCtrlPreset');
        if (ctrlPresetEl && !ctrlPresetEl.value) {
            ctrlPresetEl.value = 'current_prod';
        }
        const treatPresetEl = document.getElementById('newExpTreatPreset');
        if (treatPresetEl && !treatPresetEl.value) {
            treatPresetEl.value = 'siliconflow';
        }

        const ctrlModelEl = document.getElementById('newExpControlModel');
        if (ctrlModelEl && !ctrlModelEl.value) {
            applyExpPreset('ctrl', 'current_prod');
        }
        const treatModelEl = document.getElementById('newExpTreatmentModel');
        if (treatModelEl && !treatModelEl.value) {
            applyExpPreset('treat', 'siliconflow');
        }

        setTimeout(() => {
            const nameEl = document.getElementById('newExpName');
            if (nameEl) nameEl.focus();
        }, 50);
    }
}

function closeCreateExperimentModal() {
    const modal = document.getElementById('createExperimentModal');
    if (modal) {
        modal.classList.add('hide');
        modal.style.display = 'none';
    }
}

async function submitCreateExperiment() {
    const nameEl = document.getElementById('newExpName');
    const hypEl = document.getElementById('newExpHypothesis');
    const ctrlEl = document.getElementById('newExpControlModel');
    const treatEl = document.getElementById('newExpTreatmentModel');
    const ctrlBaseUrlEl = document.getElementById('newExpCtrlBaseUrl');
    const treatBaseUrlEl = document.getElementById('newExpTreatBaseUrl');
    const ctrlApiKeyEl = document.getElementById('newExpCtrlApiKey');
    const treatApiKeyEl = document.getElementById('newExpTreatApiKey');
    const metricEl = document.getElementById('newExpSuccessMetric');
    const pctEl = document.getElementById('newExpTargetPercent');
    const minSampleEl = document.getElementById('newExpMinSample');

    const name = nameEl ? nameEl.value.trim() : '';
    if (!name) {
        showToast('请输入实验名称', 'warning');
        return;
    }

    const controlModel = ctrlEl ? ctrlEl.value.trim() : '';
    const treatmentModel = treatEl ? treatEl.value.trim() : '';
    if (!treatmentModel) {
        showToast('请输入实验组候选模型标识', 'warning');
        return;
    }

    // P2：target_percent=0（全部走对照组）是合法值，不能因 0 是 falsy 而被 `|| 50`
    // 静默改成 50。仅当输入为空/非数字时才回落默认 50。
    const pctParsed = pctEl ? parseInt(pctEl.value, 10) : NaN;
    const targetPercent = Number.isNaN(pctParsed) ? 50 : pctParsed;

    // S2：min_sample=0（不做样本量门槛）同属合法值，不能因 0 是 falsy 而被
    // `|| 30` 静默改成 30。仅当输入为空/非数字时才回落默认 30。
    const sampleParsed = minSampleEl ? parseInt(minSampleEl.value, 10) : NaN;
    const minSample = Number.isNaN(sampleParsed) ? 30 : sampleParsed;

    const payload = {
        name: name,
        hypothesis: hypEl ? hypEl.value.trim() : '',
        success_metric: metricEl ? metricEl.value : 'accuracy',
        target_percent: targetPercent,
        min_sample: minSample,
        control_model: controlModel,
        treatment_model: treatmentModel,
        control_base_url: ctrlBaseUrlEl ? ctrlBaseUrlEl.value.trim() : '',
        treatment_base_url: treatBaseUrlEl ? treatBaseUrlEl.value.trim() : '',
        control_api_key: ctrlApiKeyEl ? ctrlApiKeyEl.value.trim() : '',
        treatment_api_key: treatApiKeyEl ? treatApiKeyEl.value.trim() : '',
        production_data_outbound_acknowledged: true
    };

    try {
        const res = await apiFetch('/api/admin/experiments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (data && data.status === 'success') {
            showToast('A/B 实验创建成功！', 'success');
            closeCreateExperimentModal();
            if (nameEl) nameEl.value = '';
            if (hypEl) hypEl.value = '';
            if (ctrlEl) ctrlEl.value = '';
            if (treatEl) treatEl.value = '';
            if (ctrlBaseUrlEl) ctrlBaseUrlEl.value = '';
            if (treatBaseUrlEl) treatBaseUrlEl.value = '';
            if (ctrlApiKeyEl) ctrlApiKeyEl.value = '';
            if (treatApiKeyEl) treatApiKeyEl.value = '';
            if (data.id) {
                const sel = document.getElementById('adminExpSelect');
                if (sel) sel.setAttribute('data-selected-id', String(data.id));
            }
            await loadAdminExperimentsList();
        } else {
            showToast('创建实验失败：' + ((data && (data.msg || data.detail)) || '未知错误'), 'error');
        }
    } catch (err) {
        console.error('submitCreateExperiment error:', err);
        showToast('创建实验网络异常', 'error');
    }
}

function gotoExperimentObservatory() {
    const btn = document.getElementById('analyticsBoardBtn');
    if (btn) btn.click();
    setTimeout(() => {
        if (typeof switchAnalyticsSubView === 'function') {
            switchAnalyticsSubView('sec-experiment');
        }
    }, 100);
}

window.loadAdminExperimentsList = loadAdminExperimentsList;
window.onAdminExpSelectChange = onAdminExpSelectChange;
window.startCurrentExperiment = startCurrentExperiment;
window.stopCurrentExperiment = stopCurrentExperiment;
window.openCreateExperimentModal = openCreateExperimentModal;
window.closeCreateExperimentModal = closeCreateExperimentModal;
window.submitCreateExperiment = submitCreateExperiment;
window.gotoExperimentObservatory = gotoExperimentObservatory;

/* =============================================================
   T10 Gap E3：系统配置（阈值规则配置化 + 预处理纠偏开关）
   GET /api/admin/settings 读取；PUT /api/admin/settings 保存；
   改动即时生效（后端实时读 app_settings），无需重启。
   ============================================================= */
// [控件 id, settings 键, 类型]
const ADMIN_SETTINGS_FIELDS = [
    ['setBlurLaplacianThreshold', 'blur_laplacian_threshold', 'float'],
    ['setEvalCandidateLowConfidence', 'eval_candidate_low_confidence', 'float'],
    ['setEvalCandidateMaxPending', 'eval_candidate_max_pending', 'int'],
    ['setAuditDiscrepancySevereMinCount', 'audit_discrepancy_severe_min_count', 'int'],
    ['setFeedbackDistillThreshold', 'feedback_distill_threshold', 'int'],
    ['setPriceAnomalyThresholdPct', 'price_anomaly_threshold_pct', 'float'],
    ['setMemoryBudgetFactsTokens', 'memory_budget_facts_tokens', 'int'],
    ['setMemoryBudgetPerItemTokens', 'memory_budget_per_item_tokens', 'int'],
    ['setMemoryBudgetMaxItems', 'memory_budget_max_items', 'int'],
    ['setMemoryDecayHitBonus', 'memory_decay_hit_bonus', 'float'],
    ['setMemoryDecayOverridePenalty', 'memory_decay_override_penalty', 'float'],
    ['setMemoryArchiveThreshold', 'memory_archive_threshold', 'float'],
    // P28-AU2：解析超时清扫阈值（settings 键 parsing_timeout_seconds，缺省 300）。
    // 走本表既有机制（int 类型）—— load 回显 / collect 下发 / reset 恢复缺省三处由同一张表驱动，
    // 不再各写一份；控件在 index.html#setParsingTimeoutSeconds（模块 D）。
    ['setParsingTimeoutSeconds', 'parsing_timeout_seconds', 'int']
];
let _adminSettingsDefaults = null;

function loadAdminSettings() {
    const role = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    if (role !== 'admin') return;
    apiFetch('/api/admin/settings')
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success' || !body.data) return;
            _adminSettingsDefaults = body.defaults || {};
            const data = body.data;
            _adminSettingsState = data;
            if (typeof window !== 'undefined') window._adminSettingsState = _adminSettingsState;
            if (typeof renderEngineOverview === 'function') {
                renderEngineOverview();
            }
            ADMIN_SETTINGS_FIELDS.forEach(([id, key]) => {
                const el = document.getElementById(id);
                if (el && data[key] !== undefined && data[key] !== null) el.value = String(data[key]);
            });
            const pre = document.getElementById('setPreprocessEnabled');
            if (pre) pre.checked = !!data.preprocess_enabled;
            // Y3-P2b：首轮供应商预判开关（settings 键 vendor_precheck_enabled，缺省 false）。
            // 布尔项不进 ADMIN_SETTINGS_FIELDS（那张表按 Number() 解析数值），在 load / collect /
            // reset 三处各自显式处理，缺任一处即「显示了但不生效」或「保存了又被重置」。
            const vp = document.getElementById('setVendorPrecheckEnabled');
            if (vp) vp.checked = !!data.vendor_precheck_enabled;
            // P23-AQ1：LLM 抽取开关（settings 键 memory_extract_enabled，缺省 false），同款机制。
            const me = document.getElementById('setMemoryExtractEnabled');
            if (me) me.checked = !!data.memory_extract_enabled;
            setTimeout(() => {
                if (typeof captureEngineFormSnapshot === 'function') {
                    captureEngineFormSnapshot();
                }
            }, 300);
        })
        .catch(() => { /* 读取失败静默，表单保持空态 */ });
}

function _collectAdminSettingsPayload() {
    const payload = {};
    for (const [id, key, type] of ADMIN_SETTINGS_FIELDS) {
        const el = document.getElementById(id);
        if (!el) continue;
        const raw = String(el.value || '').trim();
        if (raw === '') continue;   // 留空不覆盖（后端缺省回退）
        const num = Number(raw);
        if (!isFinite(num)) {
            showToast('「' + key + '」不是合法数字，已忽略该项', 'error');
            return null;
        }
        payload[key] = (type === 'int') ? Math.round(num) : num;
    }
    const pre = document.getElementById('setPreprocessEnabled');
    if (pre) payload.preprocess_enabled = !!pre.checked;
    // Y3-P2b：元素不存在时**不下发**该键（不把一个读不到的开关默认写成 false —— 那会在页面与
    // 后端版本不一致时静默关掉预判；与 preprocess_enabled 同款的 if 保护）。
    const vp = document.getElementById('setVendorPrecheckEnabled');
    if (vp) payload.vendor_precheck_enabled = !!vp.checked;
    // P23-AQ1：同样地，元素不存在时不下发该键（否则页面与后端版本不一致时会静默关掉抽取）。
    const me = document.getElementById('setMemoryExtractEnabled');
    if (me) payload.memory_extract_enabled = !!me.checked;
    return payload;
}

function saveAdminSettings() {
    const payload = _collectAdminSettingsPayload();
    if (!payload) return;
    if (!Object.keys(payload).length) { showToast('没有需要保存的配置项', 'info'); return; }
    apiFetch('/api/admin/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(res => res.json().then(body => ({ ok: res.ok, body })))
        .then(({ ok, body }) => {
            if (!ok || !body || body.status !== 'success') {
                showToast('保存系统配置失败：' + ((body && body.msg) || '未知错误'), 'error');
                return;
            }
            showToast('系统配置已保存，改动即时生效', 'success');
            loadAdminSettings();
        })
        .catch(() => { showToast('保存系统配置失败（网络错误）', 'error'); });
}

function resetAdminSettingsToDefaults() {
    if (!_adminSettingsDefaults) { showToast('缺省值尚未加载，请先刷新页面', 'info'); return; }
    const payload = {};
    ADMIN_SETTINGS_FIELDS.forEach(([id, key]) => {
        if (_adminSettingsDefaults[key] !== undefined) payload[key] = _adminSettingsDefaults[key];
    });
    payload.preprocess_enabled = !!_adminSettingsDefaults.preprocess_enabled;
    // Y3-P2b：恢复缺省值同样要把预判开关带回缺省（false），否则「恢复缺省值」会留下一个被显式
    // 打开着的开关，与按钮文案不符。
    payload.vendor_precheck_enabled = !!_adminSettingsDefaults.vendor_precheck_enabled;
    // P23-AQ1：同理，抽取开关也要带回缺省（false）。
    payload.memory_extract_enabled = !!_adminSettingsDefaults.memory_extract_enabled;
    apiFetch('/api/admin/settings', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
        .then(res => res.json())
        .then(body => {
            if (!body || body.status !== 'success') {
                showToast('恢复缺省值失败：' + ((body && body.msg) || '未知错误'), 'error');
                return;
            }
            showToast('已恢复全部缺省值', 'success');
            loadAdminSettings();
        })
        .catch(() => { showToast('恢复缺省值失败（网络错误）', 'error'); });
}

/* =============================================================
   记忆度量看板（收敛计划 P2-T5 / 概念 C11）
   GET /api/admin/memory-metrics（admin 只读端点）→ 四项指标 + 记忆总开关状态。
   口径由后端统一给出（与 demo/scripts/memory_metrics.py 同源），前端**不自算任何比率**
   —— 否则就会出现本仓高发的「同一口径多份实现」。分母为 0 时显示「无数据」，
   不用 0% 冒充（0% 表示「有数据且结果全是坏」）。
   ============================================================= */
// 看板样式落点：**内联 style 写在生成函数里**，与同一功能区的兄弟实现「供应商×SKU 历史价格
// 区间提示」(vendorPriceHintHtml) 保持同一套机制 —— 本项目纪律要求同一功能区一套机制，且避免
// 在 style.css 里留下一批无兄弟的孤立规则。项目曾发生「class 生成了但全仓无样式定义」的缺陷
// （前端版的「只写列·零消费方」），故样式与 class 名在此同处声明，配套的
// demo/tests/test_memory_metrics_panel.js 会断言「渲染产物里每个 mm-* class 都必须带 style」。
const MM_STYLE_NO_DATA = 'font-size:0.85rem; color:var(--text-muted,#9ca3af); font-style:italic;';
const MM_STYLE_RATE = 'font-size:0.95rem; font-weight:600; color:var(--text-main,#1f2937);';
const MM_STYLE_ROW = 'display:flex; align-items:center; flex-wrap:wrap; gap:10px; padding:6px 0;'
    + ' border-bottom:1px solid var(--border-color,#eef2f7);';
const MM_STYLE_LABEL = 'min-width:170px; font-size:0.85rem; color:var(--text-main,#1f2937);';
const MM_STYLE_DENOM = 'font-size:0.78rem; color:var(--text-muted,#6b7280);';
const MM_STYLE_NOTE = 'flex-basis:100%; font-size:0.75rem; line-height:1.35;'
    + ' color:var(--text-muted,#6b7280);';
const MM_STYLE_DELTA = 'margin-top:8px; padding:6px 8px; border-radius:4px; font-size:0.78rem;'
    + ' line-height:1.4; background:#f1f5f9; color:#475569;';
const MM_STYLE_ERROR = 'margin-top:8px; padding:6px 8px; border-radius:4px; font-size:0.8rem;'
    + ' background:#fee2e2; color:#b91c1c;';
// 开关三态的配色；三态必须肉眼可区分（开=绿 / 关=灰 / 未知=琥珀），否则「读取失败」会被读成「已关闭」
const MM_SWITCH_STYLE_BASE = 'display:inline-block; margin-bottom:12px; padding:3px 10px;'
    + ' border-radius:4px; font-size:0.8rem;';
const MM_SWITCH_STATE_STYLES = {
    on: 'background:#dcfce7; color:#166534;',
    off: 'background:#e5e7eb; color:#374151;',
    unknown: 'background:#fef3c7; color:#92400e;',
};

// 单个比率：ratePct 与分母必须成对给出；分母为 0 / 缺失 → 无数据。
// noDataText 允许调用方给出「为什么无数据」的显式文案（例如「当前库中没有可用记忆」）；
// 缺省仍是「无数据」。异常态必须可见且可区分：既不许渲染成空白，也不许用 0% 冒充。
function memoryMetricRateHtml(ratePct, denominator, noDataText) {
    const d = Number(denominator);
    if (!isFinite(d) || d <= 0) {
        return '<span class="mm-no-data" style="' + MM_STYLE_NO_DATA + '">'
            + escapeHtml(noDataText || '无数据') + '</span>';
    }
    const r = Number(ratePct);
    return '<span class="mm-rate" style="' + MM_STYLE_RATE + '">'
        + (isFinite(r) ? r.toFixed(2) : '0.00') + '%</span>';
}

// 一行指标：分子/分母一并展示，便于人眼复核（分母为 0 时上面已显示无数据）
function memoryMetricRowHtml(key, label, ratePct, numerator, denominator, denomLabel, noDataText) {
    const n = isFinite(Number(numerator)) ? Number(numerator) : 0;
    const d = isFinite(Number(denominator)) ? Number(denominator) : 0;
    return '<div class="mm-row" style="' + MM_STYLE_ROW + '" data-metric="' + key + '">'
        + '<span class="mm-label" style="' + MM_STYLE_LABEL + '">' + escapeHtml(label) + '</span>'
        + memoryMetricRateHtml(ratePct, d, noDataText)
        + '<span class="mm-denom" style="' + MM_STYLE_DENOM + '">' + n + ' / ' + d
        + (denomLabel ? '，' + escapeHtml(denomLabel) : '') + '</span>'
        + '</div>';
}

// 四组指标（含两组修改率）→ HTML。纯函数：入参就是后端 payload.metrics，便于离线断言。
function memoryMetricsRowsHtml(metrics) {
    const m = metrics || {};
    const hit = m.memory_hit || {};
    // 有效命中率（P2-T6 验收口径，字段 memory_hit_effective）：分母＝「供应商可确定」且
    // 「该供应商有可用记忆」的单据数。它与旧口径回答的是**不同问题**，故两个比率并列展示。
    const eff = m.memory_hit_effective || {};
    const hasEff = !!m.memory_hit_effective;
    const sku = m.sku_match || {};
    const dec = m.decision_log || {};
    const ab = m.edit_rate_ab || {};
    const hm = ab.has_memory || {};
    const nm = ab.no_memory || {};
    let html = memoryMetricRowHtml('memory_hit', '记忆命中率（旧口径·分母=全部单据）', hit.rate_pct,
        hit.with_memory_context, hit.total_receipts, 'rag_context_json 非空 / 全部单据');
    // 分母为 0 时显式说明原因（「当前库中没有可用记忆」），而不是 0%。
    // 只有字段真的存在、且后端判 no_data（或分母为 0）时才敢断言「库里没有可用记忆」——
    // 字段整体缺失是「取不到该口径」，与「库里确实没有记忆」语义相反，那种情况只说「无数据」。
    // 记忆总开关关闭是**第三种**状态（Q3-M3）：后端已把整个口径判为「未测量」
    // （disabled_by_switch=true / 分母归 0 / rate=null），此时说「库里没有可用记忆」是错的理由
    // —— 库里未必没有记忆，只是没测。必须把关闭态讲出来。
    const effDisabled = hasEff && eff.disabled_by_switch === true;
    const effNoDataText = effDisabled
        ? '记忆已关闭（未测量）'
        : ((hasEff && (eff.no_data === true || !(Number(eff.eligible_denominator) > 0)))
            ? '无数据（当前库中没有可用记忆）' : '');
    html += memoryMetricRowHtml('memory_hit_effective', '记忆有效命中率（分母=有条件命中的单据）',
        eff.effective_rate_pct, eff.injected, eff.eligible_denominator,
        'rag_context_json 非空 / 供应商可确定且有可用记忆的单据', effNoDataText);
    html += memoryMetricRowHtml('sku_match', '食材匹配率', sku.rate_pct,
        sku.matched, sku.items, 'matched=1 / 明细行');
    html += memoryMetricRowHtml('edit_rate_has_memory', '修改率（有记忆组）', hm.edited_rate_pct,
        hm.edited, hm.receipts, 'status=edited / 该组单据，分组键=供应商是否有记忆行');
    html += memoryMetricRowHtml('edit_rate_no_memory', '修改率（无记忆组）', nm.edited_rate_pct,
        nm.edited, nm.receipts, 'status=edited / 该组单据，分组键=供应商是否有记忆行');
    html += memoryMetricRowHtml('not_adopted', '字段级未采纳率', dec.not_adopted_rate_pct,
        dec.not_adopted, dec.decisions, 'adopted=0 / 决策总数');
    // 两个命中率并列，必须写明「不可混读」：分母定义不同，差异来自口径而非行为变化。
    // 文案优先取后端（唯一口径实现 memory_metrics.py）自述的 incomparable_reason，
    // 缺省回落到前端固定句 —— 保证 payload 缺该字段时也不会把两个数字读成可比。
    if (hasEff) {
        const effReason = eff.incomparable_reason
            || '与「记忆命中率（旧口径）」的分母不同，两者数值不可混读、不可相互比较。';
        const funnel = effDisabled
            ? '（记忆总开关 memory_enabled 已关闭：本口径未测量，不输出 0%）'
            : '（全部单据 ' + (Number(eff.receipts_total) || 0)
              + ' / 供应商可确定 ' + (Number(eff.vendor_determinable) || 0)
              + ' / 有条件命中 ' + (Number(eff.eligible_denominator) || 0)
              + ' / 因无可用记忆被排除 ' + (Number(eff.excluded_no_usable_memory) || 0) + '）';
        html += '<div class="mm-note" style="' + MM_STYLE_NOTE + '" data-metric="memory_hit_effective_note">'
            + escapeHtml(effReason + funnel) + '</div>';
    }
    // 分组键是代理口径（供应商是否有记忆行），不等于「本次真的注入了」——按本仓纪律必须
    // 在数字旁写明，否则会被读成因果结论（真注入口径见 CLI 脚本 memory_ab_baseline.py）。
    if (hit.note) {
        html += '<div class="mm-note" style="' + MM_STYLE_NOTE + '" data-metric="memory_hit_note">'
            + escapeHtml(hit.note) + '</div>';
    }
    // 两组之差：任一组无样本时后端不给 delta_pp → 显示无数据（不猜）
    if (ab.delta_pp === undefined || ab.delta_pp === null) {
        html += '<div class="mm-delta" style="' + MM_STYLE_DELTA + '" data-metric="edit_rate_delta">'
            + '两组修改率之差：无数据（任一组无样本）</div>';
    } else {
        const d = Number(ab.delta_pp);
        html += '<div class="mm-delta" style="' + MM_STYLE_DELTA + '" data-metric="edit_rate_delta">两组修改率之差：'
            + (d > 0 ? '+' : '') + (isFinite(d) ? d.toFixed(2) : '0.00') + 'pp'
            + '（负值=有记忆组修改率更低，即目标方向；观察性对比非因果，真注入口径见 demo/scripts/memory_ab_baseline.py）</div>';
    }
    return html;
}

// 记忆总开关状态：true/false/null 三态各自显式，不用「未开启」把读取失败混进来。
// 三态各带一条不同的背景/文字配色（见 MM_SWITCH_STATE_STYLES）：仅靠文案差一个字不足以
// 让人一眼分辨，且本项目已要求「所有胶囊/徽标一套机制 + 状态必须有色差」。
function memoryMetricsSwitchHtml(memoryEnabled) {
    let text, cls, stateStyle;
    if (memoryEnabled === true) {
        text = '已开启'; cls = 'on'; stateStyle = MM_SWITCH_STATE_STYLES.on;
    } else if (memoryEnabled === false) {
        text = '已关闭（检索入口直接返回空串，不查记忆表与向量）';
        cls = 'off'; stateStyle = MM_SWITCH_STATE_STYLES.off;
    } else {
        text = '状态未知（读取失败）'; cls = 'unknown'; stateStyle = MM_SWITCH_STATE_STYLES.unknown;
    }
    return '<div class="mm-switch" style="' + MM_SWITCH_STYLE_BASE + stateStyle
        + '" data-switch="' + cls + '">记忆注入总开关 memory_enabled：'
        + text + '</div>';
}

function renderMemoryMetrics(payload) {
    const box = document.getElementById('memoryMetricsPanel');
    if (!box) return;
    const body = payload || {};
    let html = memoryMetricsSwitchHtml(body.memory_enabled);
    if (body.status !== 'success' || !body.metrics) {
        // 失败时一个比率都不显示：显示 0% 会冒充「有数据且全是坏结果」，与「取不到数」语义相反
        html += '<div class="mm-error" style="' + MM_STYLE_ERROR + '" data-metric="error">度量读取失败：'
            + escapeHtml(body.msg || '未知错误') + '</div>';
        box.innerHTML = html;
        return;
    }
    box.innerHTML = html + memoryMetricsRowsHtml(body.metrics);
}

function loadMemoryMetrics() {
    const role = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    if (role !== 'admin') return;
    apiFetch('/api/admin/memory-metrics')
        .then(res => res.json())
        .then(body => renderMemoryMetrics(body))
        .catch(() => renderMemoryMetrics({ status: 'error', msg: '网络错误', memory_enabled: null }));
}

/* =============================================================
   已生效记忆治理区（收敛计划 P8 / AC2 —— 闭环残债 R1 的「界面半边」）
   GET  /api/admin/vendor-memory?status=active  （只读治理视图）
   DELETE /api/memory/{memory_id}               （唯一删除路径）

   为什么要有这一块：`db.list_vendor_memory` 此前只有测试与内部脚本调用，界面上没有任何
   地方能看到「系统记住了哪些供应商、这条是谁写的、被命中过几次、衰减到什么程度」。缺了
   这层可见面，记忆治理只存在于代码里。两条纪律：
   ① 信任度 / 衰减分 / 命中次数等数值**一律原样来自后端**，前端不做任何重算与折算
      （0 与「取不到值」必须区分：null/缺字段显示「未知」，不冒充 0）；列表口径也显式传
      status=active（端点缺省是全状态，治理区只讲「已生效」）。
   ② 删除只调已有的 `DELETE /api/memory/{memory_id}`，**不新增第二条删除路径**；
      删除前二次确认，删除后重载列表。端点失败静默降级为可见错误态 + console.warn 留痕，
      不产生未捕获异常。
   ============================================================= */
// 样式落点与记忆度量看板同机制：内联 style 写在生成函数旁（同一功能区一套机制，避免
// 在 style.css 留下一批无兄弟的孤立规则）。配套 harness 会断言渲染产物里每个 vm-* class
// 都带非空 style（防「class 生成了但全仓无样式」这一前端版的「只写列·零消费方」）。
const VM_STYLE_HEAD = 'display:flex; align-items:center; gap:10px; padding:6px 0;'
    + ' border-bottom:1px solid var(--border-color,#e5e7eb); font-size:0.78rem;'
    + ' color:var(--text-muted,#6b7280);';
const VM_STYLE_ROW = 'display:flex; align-items:center; gap:10px; padding:7px 0;'
    + ' border-bottom:1px solid var(--border-color,#eef2f7); font-size:0.82rem; flex-wrap:wrap;';
const VM_STYLE_CELL = 'flex:1 1 90px; min-width:70px; word-break:break-word;'
    + ' color:var(--text-main,#1f2937);';
const VM_STYLE_HCELL = 'flex:1 1 90px; min-width:70px; font-weight:600;';
const VM_STYLE_ACTION = 'flex:0 0 64px; text-align:right;';
const VM_STYLE_DEL = 'padding:2px 10px; font-size:0.78rem; border-radius:4px; cursor:pointer;'
    + ' border:1px solid #fca5a5; background:#fef2f2; color:#b91c1c;';
const VM_STYLE_EMPTY = 'padding:14px 0; font-size:0.85rem; color:var(--text-muted,#9ca3af);'
    + ' font-style:italic;';
const VM_STYLE_ERROR = 'margin-top:8px; padding:6px 8px; border-radius:4px; font-size:0.8rem;'
    + ' background:#fee2e2; color:#b91c1c;';
const VM_STYLE_COUNT = 'margin-top:8px; font-size:0.78rem; color:var(--text-muted,#6b7280);';

// 状态中文标签：原始 status 值同时保留在 data-status 上（供人机复核，不让中文标签掩盖原值）
const VM_STATUS_LABELS = { active: '生效中', archived: '已归档', superseded: '已被取代' };
function vendorMemoryStatusLabel(status) {
    const s = (status === null || status === undefined) ? '' : String(status);
    return VM_STATUS_LABELS[s] || (s || '未知');
}

// 数值原样展示：null / 缺失 / 空串 → 「未知」而不是 0（取不到值不能冒充 0，与本仓
// payment_marked「读不出来就别折成合法值」同一条纪律）。非数值原样回显，不抛异常。
function vendorMemoryNumText(v) {
    if (v === null || v === undefined || v === '') return '未知';
    const n = Number(v);
    return isFinite(n) ? (Number.isInteger(n) ? String(n) : parseFloat(n.toFixed(4)).toString()) : String(v);
}

// written_by 为空 → 「未知」（谁写的这条记忆必须有一个可见的落点，空白无信息）
function vendorMemoryWhoText(v) {
    const s = (v === null || v === undefined) ? '' : String(v).trim();
    return s || '未知';
}

function vendorMemoryCellHtml(field, value) {
    return '<span class="vm-cell" style="' + VM_STYLE_CELL + '" data-field="' + field + '">'
        + escapeHtml(value === null || value === undefined ? '' : String(value)) + '</span>';
}

// 一行记忆：字段顺序与只读治理端点的治理元数据对应；删除按钮带 memory_id，供 deleteVendorMemory 调用
function vendorMemoryRowHtml(row) {
    const r = row || {};
    const mid = (r.memory_id === null || r.memory_id === undefined) ? '' : String(r.memory_id);
    const vendorName = (r.vendor === null || r.vendor === undefined) ? '' : String(r.vendor);
    return '<div class="vm-row" style="' + VM_STYLE_ROW + '" data-memory-id="' + escapeHtml(mid) + '">'
        + vendorMemoryCellHtml('vendor', vendorName)
        + vendorMemoryCellHtml('fact_kind', r.fact_kind)
        + vendorMemoryCellHtml('fact_value', r.fact_value)
        + vendorMemoryCellHtml('trust_level', vendorMemoryNumText(r.trust_level))
        + vendorMemoryCellHtml('decay_score', vendorMemoryNumText(r.decay_score))
        + vendorMemoryCellHtml('hit_count', vendorMemoryNumText(r.hit_count))
        + vendorMemoryCellHtml('written_by', vendorMemoryWhoText(r.written_by))
        + '<span class="vm-cell" style="' + VM_STYLE_CELL + '" data-field="status" data-status="'
        + escapeHtml(r.status === null || r.status === undefined ? '' : String(r.status)) + '">'
        + escapeHtml(vendorMemoryStatusLabel(r.status)) + '</span>'
        + '<span class="vm-action" style="' + VM_STYLE_ACTION + '">'
        + '<button type="button" class="vm-del" style="' + VM_STYLE_DEL + '"'
        + ' data-memory-id="' + escapeHtml(mid) + '"'
        + ' data-vendor="' + escapeHtml(vendorName) + '"'
        + " onclick=\"deleteVendorMemory(this.getAttribute('data-memory-id'), this.getAttribute('data-vendor'))\""
        + '>删除</button></span>'
        + '</div>';
}

function vendorMemoryGovernanceHeadHtml() {
    const cols = ['供应商', '类型', '值', '信任度', '衰减分', '命中次数', '谁写的', '状态'];
    let html = '<div class="vm-head" style="' + VM_STYLE_HEAD + '">';
    cols.forEach(label => {
        html += '<span class="vm-hcell" style="' + VM_STYLE_HCELL + '">' + escapeHtml(label) + '</span>';
    });
    html += '<span class="vm-hcell" style="' + VM_STYLE_HCELL + VM_STYLE_ACTION + '">操作</span></div>';
    return html;
}

// payload 为只读治理端点的响应体。失败态只显式给错误文案（不渲染半截列表），空列表显空态。
function renderVendorMemoryGovernance(payload) {
    const box = document.getElementById('vendorMemoryGovernancePanel');
    if (!box) return;
    const body = payload || {};
    if (body.status !== 'success') {
        box.innerHTML = '<div class="vm-error" style="' + VM_STYLE_ERROR + '" data-field="error">'
            + '已生效记忆读取失败：' + escapeHtml(body.msg || '未知错误') + '</div>';
        return;
    }
    const rows = Array.isArray(body.data) ? body.data : [];
    if (!rows.length) {
        box.innerHTML = vendorMemoryGovernanceHeadHtml()
            + '<div class="vm-empty" style="' + VM_STYLE_EMPTY + '" data-field="empty">暂无已生效记忆</div>';
        return;
    }
    const total = isFinite(Number(body.total)) ? Number(body.total) : rows.length;
    box.innerHTML = vendorMemoryGovernanceHeadHtml()
        + rows.map(vendorMemoryRowHtml).join('')
        + '<div class="vm-count" style="' + VM_STYLE_COUNT + '" data-field="count">共 '
        + total + ' 条已生效记忆</div>';
}

function loadVendorMemoryGovernance() {
    const role = (() => { try { return localStorage.getItem('demo_role'); } catch (e) { return null; } })();
    if (role !== 'admin') return Promise.resolve();
    return apiFetch('/api/admin/vendor-memory?status=active')
        .then(res => res.json())
        .then(body => { renderVendorMemoryGovernance(body); return body; })
        .catch(err => {
            // 辅助治理面：失败降级为可见错误态，但必须留痕，不静默吞掉异常
            console.warn('[vendor-memory-governance] 读取已生效记忆失败：', err);
            renderVendorMemoryGovernance({ status: 'error', msg: '网络错误' });
        });
}

// 删除单条已生效记忆：只调唯一删除端点 DELETE /api/memory/{memory_id}。
// 删除前二次确认；成功后重载列表（该条随之消失）。取消 / 失败都不改列表。
async function deleteVendorMemory(memoryId, vendor) {
    const mid = (memoryId === null || memoryId === undefined) ? '' : String(memoryId).trim();
    if (!mid) return Promise.resolve(false);
    const name = (vendor === null || vendor === undefined || String(vendor).trim() === '')
        ? '该条' : ('「' + String(vendor).trim() + '」的该条');
    const okMem = await uiConfirm({
        title: '删除已生效记忆',
        message: '确定删除' + name + '已生效记忆？\n删除后系统会忘掉这条供应商习惯，不能恢复，需要重新学习。',
        confirmText: '删除',
        danger: true,
    });
    if (!okMem) return false;
    return apiFetch('/api/memory/' + encodeURIComponent(mid), { method: 'DELETE' })
        .then(res => res.json())
        .then(body => {
            if (body && body.status === 'success') {
                showToast('已删除该条记忆', 'success');
                return loadVendorMemoryGovernance();
            }
            showToast('删除失败：' + ((body && body.msg) || '未知错误'), 'error');
            return false;
        })
        .catch(err => {
            console.warn('[vendor-memory-governance] 删除记忆失败：', err);
            showToast('删除失败（网络错误）', 'error');
            return false;
        });
}

// 辅助：转义 HTML
function escapeHtml(s) {
    if (s === null || s === undefined) return '';
    return String(s)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}


/* =============================================================
   阶段 1：AI 可见性与信任
   （PM 反馈：audit_reason 差异横幅与「AI 建议 vs 用户确认」对照面板已移除，
     AI 解析结果直接展示在步骤 2/2 的表单字段与明细表中）
   1.3  灰测组视觉标记（greyBadgeHtml）

   ============================================================= */

// 1.3 灰测组视觉标记：返回 <span class="badge-grey"> 或空串
function greyBadgeHtml(useGrey) {
    if (!useGrey) return '';
    return '<span class="badge-grey" title="灰测组处理（cross_audit 双模型）">灰测组</span>';
}
