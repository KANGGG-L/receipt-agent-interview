// demo/static/js/kpi_trend.js
// 8 大核心 KPI 时序趋势与多维下钻分析控制器（PRD-OBS-KPI-DRILLDOWN-V2）：
// renderKpiTrendTable / 下钻 Modal / 维度切换。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// 注：原 pytest 钉定「renderKpiTrendTable 必须在 main.js」的 3 处判据已同步改写为
// 守真不变量（判据＝函数在 kpi_trend.js 且 main.js 不再保留第二份实现）。
// 8 大核心 KPI 时序趋势与多维下钻分析控制器 (PRD-OBS-KPI-DRILLDOWN-V2-20260930)
// =============================================================
window.currentKpiTrendState = {
    metricKey: 'total_parses',
    days: 7,
    startDate: null,
    endDate: null,
    tenantId: 'all',
    data: null,
};

function kpiTrendTodayStr() {
    const d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
}

function openKpiTrendModal(metricKey) {
    const modal = document.getElementById('kpiTrendModal');
    if (!modal) return;

    const state = window.currentKpiTrendState;
    if (metricKey) {
        state.metricKey = metricKey;
    }
    const tenantSel = document.getElementById('analyticsTenantSelect') || document.getElementById('parsingLogTenantSelect');
    state.tenantId = (tenantSel && tenantSel.value) ? tenantSel.value : 'all';

    modal.classList.remove('hide');
    modal.style.display = 'flex';

    const eEl = document.getElementById('kpiTrendEndDate');
    if (eEl) eEl.value = state.endDate || kpiTrendTodayStr();

    loadKpiTrendData();
}
window.openKpiTrendModal = openKpiTrendModal;

function closeKpiTrendModal() {
    const modal = document.getElementById('kpiTrendModal');
    if (modal) {
        modal.classList.add('hide');
        modal.style.display = 'none';
    }
    const tip = document.getElementById('kpiTrendChartTooltip');
    if (tip) tip.style.display = 'none';
}
window.closeKpiTrendModal = closeKpiTrendModal;

function switchKpiTrendDays(days) {
    const state = window.currentKpiTrendState;
    state.days = parseInt(days, 10) || 7;
    state.startDate = null;
    state.endDate = null;

    const sEl = document.getElementById('kpiTrendStartDate');
    const eEl = document.getElementById('kpiTrendEndDate');
    if (sEl) sEl.value = '';
    if (eEl) eEl.value = kpiTrendTodayStr();

    ['7D', '14D', '30D'].forEach(k => {
        const btn = document.getElementById('btnKpiTrend' + k);
        if (!btn) return;
        if (k === (state.days + 'D')) {
            btn.className = 'btn btn-sm btn-primary';
            btn.style.fontWeight = '600';
        } else {
            btn.className = 'btn btn-sm btn-secondary';
            btn.style.fontWeight = 'normal';
        }
    });

    const span = document.getElementById('kpiTrendModalPeriodSpan');
    if (span) span.textContent = '最近 ' + state.days + ' 天';

    loadKpiTrendData();
}
window.switchKpiTrendDays = switchKpiTrendDays;

function applyKpiTrendCustomDate() {
    const state = window.currentKpiTrendState;
    const sEl = document.getElementById('kpiTrendStartDate');
    const eEl = document.getElementById('kpiTrendEndDate');
    if (!sEl || !eEl) return;
    const sVal = sEl.value ? sEl.value.trim() : '';
    const eVal = eEl.value ? eEl.value.trim() : '';

    if (!eVal) {
        showToast('请选择结束日期（开始日期留空时默认从最早一条记录开始）', 'warning');
        return;
    }
    if (sVal && sVal > eVal) {
        showToast('开始日期不能晚于结束日期', 'warning');
        return;
    }
    let diffDays = null;
    if (sVal) {
        const dStart = new Date(sVal);
        const dEnd = new Date(eVal);
        diffDays = Math.round((dEnd - dStart) / (1000 * 60 * 60 * 24)) + 1;
        if (diffDays > 90) {
            showToast('自定义时间段跨度不能超过 90 天', 'warning');
            return;
        }
    }

    state.startDate = sVal || null;
    state.endDate = eVal;
    if (diffDays != null) state.days = diffDays;

    ['7D', '14D', '30D'].forEach(k => {
        const btn = document.getElementById('btnKpiTrend' + k);
        if (btn) {
            btn.className = 'btn btn-sm btn-secondary';
            btn.style.fontWeight = 'normal';
        }
    });

    const span = document.getElementById('kpiTrendModalPeriodSpan');
    if (span) span.textContent = (sVal ? sVal : '最早记录') + ' 至 ' + eVal;

    loadKpiTrendData();
}
window.applyKpiTrendCustomDate = applyKpiTrendCustomDate;

function loadKpiTrendData() {
    const state = window.currentKpiTrendState;
    const chartBox = document.getElementById('kpiTrendChartContainer');
    const tableBox = document.getElementById('kpiTrendTableContainer');
    if (chartBox) chartBox.innerHTML = '<div style="color:var(--text-muted); font-size:0.85rem; padding:40px 0;">正在加载时序数据与渲染图表...</div>';
    if (tableBox) tableBox.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-muted); font-size:0.82rem;">加载明细数据中...</div>';

    const params = new URLSearchParams({
        metric_key: state.metricKey,
        tenant_id: state.tenantId,
    });
    if (state.endDate) {
        if (state.startDate) params.set('start_date', state.startDate);
        params.set('end_date', state.endDate);
    } else {
        params.set('days', String(state.days));
    }

    apiFetch('/api/analytics/kpi-trends?' + params.toString())
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (httpStatus !== 200 || !ret || ret.status !== 'success' || !ret.data) {
                const msg = (ret && (ret.message || ret.detail || ret.msg)) || ('HTTP ' + httpStatus);
                if (chartBox) chartBox.innerHTML = '<div style="color:#b91c1c; font-size:0.85rem; padding:30px 0;">时序数据加载失败: ' + w2Escape(msg) + '</div>';
                if (tableBox) tableBox.innerHTML = '<div style="color:#b91c1c; text-align:center; padding:20px; font-size:0.82rem;">无法读取时序切片数据</div>';
                return;
            }

            const data = ret.data;
            state.data = data;

            // 1. 模态框头部导引信息 (简化为 ${metric_name}趋势)
            const titleEl = document.getElementById('kpiTrendModalTitle');
            if (titleEl) titleEl.textContent = data.metric_name;

            const sectionEl = document.getElementById('kpiTrendModalSectionBadge');
            if (sectionEl) sectionEl.textContent = '[' + (data.section_name || '观测中心') + ']';

            const tenantEl = document.getElementById('kpiTrendModalTenantBadge');
            if (tenantEl) {
                if (data.tenant_id && data.tenant_id !== 'all') {
                    tenantEl.textContent = '[租户: ' + data.tenant_id + ']';
                    tenantEl.style.display = 'inline-block';
                } else {
                    tenantEl.textContent = '';
                    tenantEl.style.display = 'none';
                }
            }

            const keyEl = document.getElementById('kpiTrendModalMetricKey');
            if (keyEl) keyEl.textContent = data.metric_key;

            const unitEl = document.getElementById('kpiTrendModalUnit');
            if (unitEl) unitEl.textContent = data.metric_unit || '无';

            // 2. 统计卡片摘要区 (智能格式化，整数指标严禁 0.0)
            const summary = data.summary || {};
            const isInt = !!data.is_integer;
            const unitStr = data.metric_unit ? (' ' + data.metric_unit) : '';
            const formatVal = (v) => {
                if (v == null) return '-';
                const num = Number(v);
                if (isInt) return Math.round(num).toLocaleString();
                return num.toLocaleString();
            };

            const avgEl = document.getElementById('kpiTrendSummaryAvg');
            if (avgEl) avgEl.textContent = formatVal(summary.avg_val) + unitStr;

            const curEl = document.getElementById('kpiTrendSummaryCurrent');
            if (curEl) curEl.textContent = formatVal(summary.current_val) + unitStr;

            const extEl = document.getElementById('kpiTrendSummaryExtremes');
            if (extEl) {
                extEl.textContent = '高 ' + formatVal(summary.max_val)
                    + ' / 低 ' + formatVal(summary.min_val)
                    + unitStr;
            }

            const trEl = document.getElementById('kpiTrendSummaryTrend');
            if (trEl) {
                const ch = summary.change_rate_pct != null ? summary.change_rate_pct : 0;
                // lower_is_better 指标（耗时/Token/修正率/挽回触发率）下降是积极信号：
                // [提升]/[下降] 只描述方向，颜色按好与坏标注
                const lowerIsBetter = !!data.lower_is_better;
                const upGood = !lowerIsBetter;
                let badgeText = '[平稳]';
                let color = 'var(--text-muted)';
                if (ch > 0.5) {
                    badgeText = '[提升] +' + ch.toFixed(1) + '%';
                    color = upGood ? '#16a34a' : '#dc2626';
                } else if (ch < -0.5) {
                    badgeText = '[下降] ' + ch.toFixed(1) + '%';
                    color = upGood ? '#dc2626' : '#16a34a';
                } else {
                    badgeText = '[平稳] ' + ch.toFixed(1) + '%';
                }
                trEl.innerHTML = '<span style="color:' + color + ';">' + w2Escape(badgeText) + '</span>';
            }

            // 3. 原生内联 SVG 折线图与面积图渲染
            renderKpiTrendChart(data.series || [], data);

            // 4. 每日切片明细表渲染
            renderKpiTrendTable(data.daily_records || [], data);
        })
        .catch(err => {
            if (chartBox) chartBox.innerHTML = '<div style="color:#b91c1c; font-size:0.85rem; padding:30px 0;">网络异常: ' + w2Escape(err.message || '未知错误') + '</div>';
            if (tableBox) tableBox.innerHTML = '<div style="color:#b91c1c; text-align:center; padding:20px; font-size:0.82rem;">加载异常</div>';
        });
}
window.loadKpiTrendData = loadKpiTrendData;

function renderKpiTrendChart(series, meta) {
    const container = document.getElementById('kpiTrendChartContainer');
    if (!container) return;

    if (!series || series.length === 0) {
        container.innerHTML = '<div style="padding:40px; color:var(--text-muted); font-size:0.85rem;">暂无时序切片数据</div>';
        return;
    }

    const W = 840;
    const H = 240;
    const padL = 65;
    const padR = 45;
    const padT = 30;
    const padB = 40;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;

    const values = series.map(s => Number(s.value) || 0);
    const summary = meta.summary || {};
    const isRate = (meta.metric_unit === '%');

    let minVal = 0;
    let maxVal = Math.max(...values, summary.avg_val || 0);
    if (isRate) {
        maxVal = 100;
    } else {
        if (maxVal <= 0) maxVal = 10;
        maxVal = Math.ceil(maxVal * 1.15);
    }

    function getY(v) {
        if (maxVal === minVal) return padT + plotH / 2;
        const ratio = (v - minVal) / (maxVal - minVal);
        return padT + plotH - (ratio * plotH);
    }

    const n = series.length;
    function getX(idx) {
        if (n <= 1) return padL + plotW / 2;
        return padL + (idx / (n - 1)) * plotW;
    }

    // Y 轴刻度 4 档
    const yTicks = [0, 0.33, 0.66, 1.0].map(ratio => {
        const val = minVal + ratio * (maxVal - minVal);
        const y = padT + plotH - ratio * plotH;
        let lbl = isRate ? (val.toFixed(0) + '%') : (val >= 1000 ? (val / 1000).toFixed(1) + 'k' : val.toFixed(0));
        return { val, y, lbl };
    });

    let svg = '<svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%; height:auto; display:block; overflow:visible;" xmlns="http://www.w3.org/2000/svg">';
    // Defs: 渐变
    svg += '<defs>'
        + '<linearGradient id="kpiTrendGrad" x1="0" y1="0" x2="0" y2="1">'
        + '<stop offset="0%" stop-color="#2563eb" stop-opacity="0.25"/>'
        + '<stop offset="100%" stop-color="#2563eb" stop-opacity="0.01"/>'
        + '</linearGradient>'
        + '</defs>';

    // 背景网格线与 Y 轴刻度
    yTicks.forEach(tick => {
        svg += '<line x1="' + padL + '" y1="' + tick.y + '" x2="' + (padL + plotW) + '" y2="' + tick.y + '" stroke="#e2e8f0" stroke-width="1" stroke-dasharray="3,3"/>';
        svg += '<text x="' + (padL - 10) + '" y="' + (tick.y + 4) + '" fill="#94a3b8" font-size="11" text-anchor="end" font-family="monospace">' + tick.lbl + '</text>';
    });

    // 均值参考虚线（数值不再标注在图上，改为 hover tooltip 展示）
    let avgY = null;
    if (summary.avg_val != null && summary.avg_val >= minVal && summary.avg_val <= maxVal) {
        avgY = getY(summary.avg_val);
        svg += '<line x1="' + padL + '" y1="' + avgY + '" x2="' + (padL + plotW) + '" y2="' + avgY + '" stroke="#94a3b8" stroke-width="1.5" stroke-dasharray="5,4"/>';
    }

    // 折线与面积路径
    const points = series.map((s, idx) => ({ x: getX(idx), y: getY(s.value), item: s, idx: idx }));
    let pathD = '';
    points.forEach((p, i) => {
        pathD += (i === 0 ? 'M ' : ' L ') + p.x.toFixed(1) + ' ' + p.y.toFixed(1);
    });

    // 面积
    if (points.length > 0) {
        const areaD = pathD + ' L ' + points[points.length - 1].x.toFixed(1) + ' ' + (padT + plotH)
            + ' L ' + points[0].x.toFixed(1) + ' ' + (padT + plotH) + ' Z';
        svg += '<path d="' + areaD + '" fill="url(#kpiTrendGrad)"/>';
    }

    // 主线条
    svg += '<path d="' + pathD + '" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>';

    // X 轴日期刻度
    const showEvery = n <= 7 ? 1 : (n <= 14 ? 2 : 4);
    points.forEach((p, idx) => {
        if (idx % showEvery === 0 || idx === n - 1) {
            const shortDate = p.item.date.slice(5); // MM-DD
            svg += '<text x="' + p.x.toFixed(1) + '" y="' + (padT + plotH + 20) + '" fill="#64748b" font-size="11" text-anchor="middle" font-family="monospace">' + shortDate + '</text>';
            svg += '<line x1="' + p.x.toFixed(1) + '" y1="' + (padT + plotH) + '" x2="' + p.x.toFixed(1) + '" y2="' + (padT + plotH + 4) + '" stroke="#cbd5e1" stroke-width="1"/>';
        }
    });

    // 数据节点圆圈
    points.forEach(p => {
        const isAnomaly = !!p.item.is_anomaly;
        const color = isAnomaly ? '#ef4444' : '#2563eb';
        const r = isAnomaly ? 5.5 : 4.5;

        if (isAnomaly) {
            svg += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="9" fill="none" stroke="#fca5a5" stroke-width="2" opacity="0.8"/>';
        }
        svg += '<circle cx="' + p.x.toFixed(1) + '" cy="' + p.y.toFixed(1) + '" r="' + r + '" fill="' + color + '" stroke="#ffffff" stroke-width="2"/>';
    });

    // 悬停参考线与高亮点（随鼠标移动更新）
    svg += '<line id="kpiTrendGuideLine" x1="' + padL + '" y1="' + padT + '" x2="' + padL + '" y2="' + (padT + plotH) + '" stroke="#94a3b8" stroke-width="1" stroke-dasharray="4,3" visibility="hidden"/>';
    svg += '<circle id="kpiTrendHoverDot" r="6.5" fill="none" stroke="#2563eb" stroke-width="2.5" visibility="hidden"/>';

    // 全绘图区悬停热区：线上任意位置都能按最近切片触发 tooltip
    svg += '<rect x="' + padL + '" y="' + padT + '" width="' + plotW + '" height="' + plotH + '" fill="transparent"'
        + ' onmousemove="kpiTrendPlotMove(event)"'
        + ' onmouseleave="hideKpiTrendHover()"/>';

    svg += '</svg>';
    container.innerHTML = svg;

    // 记录布局供 hover 换算坐标使用（avgY 供均值参考线 hover 判定）
    window.currentKpiTrendState._chartLayout = { W: W, points: points, avgY: avgY };
}
window.renderKpiTrendChart = renderKpiTrendChart;

function kpiTrendPlotMove(e) {
    const state = window.currentKpiTrendState;
    const layout = state && state._chartLayout;
    const chartBox = document.getElementById('kpiTrendChartContainer');
    if (!layout || !layout.points || !layout.points.length || !chartBox) return;

    const points = layout.points;
    const rect = chartBox.getBoundingClientRect();
    const scale = rect.width / layout.W;
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;

    // x 超出折线首尾范围（留 12px 余量）时不展示
    if (points.length > 1 && (mx < points[0].x * scale - 12 || mx > points[points.length - 1].x * scale + 12)) {
        hideKpiTrendHover();
        return;
    }

    // 最近邻切片：按 x 距离取整段折线上离鼠标最近的日期点
    let idx;
    if (points.length === 1) {
        idx = 0;
    } else {
        const firstX = points[0].x * scale;
        const step = (points[1].x - points[0].x) * scale;
        idx = Math.round((mx - firstX) / step);
        idx = Math.max(0, Math.min(points.length - 1, idx));
    }
    const p = points[idx];
    if (!p) return;

    // 灵敏度收敛：仅当鼠标贴近折线（距临近数据点 ≤36px，或距所在线段 ≤14px）才展示
    const px = p.x * scale;
    const py = p.y * scale;
    let lineY = py;
    if (points.length > 1) {
        const i2 = (mx > px && idx < points.length - 1) ? idx + 1 : ((mx < px && idx > 0) ? idx - 1 : idx);
        const q = points[i2];
        const qx = q.x * scale;
        const qy = q.y * scale;
        if (qx !== px) {
            const t = (mx - px) / (qx - px);
            lineY = py + t * (qy - py);
        }
    }
    const distPt = Math.hypot(mx - px, my - py);
    const dyLine = Math.abs(my - lineY);
    const avgDist = (layout.avgY != null) ? Math.abs(my - layout.avgY * scale) : Infinity;

    // 灰色均值线优先：贴近虚线（≤10px）且不在数据点标记上（>12px）时，
    // 一律展示均值信息，不被邻近数据点的 tooltip 抢走
    if (avgDist <= 10 && distPt > 12) {
        const guide0 = document.getElementById('kpiTrendGuideLine');
        if (guide0) guide0.setAttribute('visibility', 'hidden');
        const dot0 = document.getElementById('kpiTrendHoverDot');
        if (dot0) dot0.setAttribute('visibility', 'hidden');
        showKpiTrendAvgTooltip(e);
        return;
    }

    const nearData = distPt <= 36 || dyLine <= 14;
    if (!nearData) {
        hideKpiTrendHover();
        return;
    }

    const guide = document.getElementById('kpiTrendGuideLine');
    if (guide) {
        guide.setAttribute('x1', p.x.toFixed(1));
        guide.setAttribute('x2', p.x.toFixed(1));
        guide.setAttribute('visibility', 'visible');
    }
    const dot = document.getElementById('kpiTrendHoverDot');
    if (dot) {
        dot.setAttribute('cx', p.x.toFixed(1));
        dot.setAttribute('cy', p.y.toFixed(1));
        dot.setAttribute('stroke', p.item.is_anomaly ? '#ef4444' : '#2563eb');
        dot.setAttribute('visibility', 'visible');
    }

    showKpiTrendTooltip(e, idx);
}
window.kpiTrendPlotMove = kpiTrendPlotMove;

function hideKpiTrendHover() {
    ['kpiTrendGuideLine', 'kpiTrendHoverDot'].forEach(id => {
        const el = document.getElementById(id);
        if (el) el.setAttribute('visibility', 'hidden');
    });
    hideKpiTrendTooltip();
}
window.hideKpiTrendHover = hideKpiTrendHover;

function showKpiTrendAvgTooltip(e) {
    const tip = document.getElementById('kpiTrendChartTooltip');
    const state = window.currentKpiTrendState;
    const layout = state && state._chartLayout;
    const chartBox = document.getElementById('kpiTrendChartContainer');
    if (!tip || !state || !state.data || !layout || layout.avgY == null || !chartBox) return;
    const summary = state.data.summary || {};
    if (summary.avg_val == null) return;

    const isInt = !!state.data.is_integer;
    const unit = state.data.metric_unit || '';
    const avgDisplay = isInt ? Math.round(Number(summary.avg_val)) : summary.avg_val;
    tip.innerHTML = '<div style="font-weight:700; color:#f8fafc; font-family:monospace;">区间均值</div>'
        + '<div style="font-size:0.85rem; font-weight:700; color:#94a3b8; margin-top:2px;">' + avgDisplay + ' ' + w2Escape(unit) + '</div>';

    const svgEl = chartBox.querySelector('svg');
    const sr = svgEl ? svgEl.getBoundingClientRect() : chartBox.getBoundingClientRect();
    const br = chartBox.getBoundingClientRect();
    const scale = sr.width / layout.W;
    let x = e.clientX - br.left;
    let y = layout.avgY * scale + (sr.top - br.top);
    tip.style.display = 'block';
    const tw = tip.offsetWidth;
    const th = tip.offsetHeight;
    x = Math.max(tw / 2 + 4, Math.min(br.width - tw / 2 - 4, x));
    const below = (y - th - 8) < 0;
    tip.style.left = x + 'px';
    tip.style.top = (below ? y + 14 : y - 8) + 'px';
    tip.style.transform = below ? 'translate(-50%, 0)' : 'translate(-50%, -100%)';
}
window.showKpiTrendAvgTooltip = showKpiTrendAvgTooltip;

function showKpiTrendTooltip(e, idx) {
    const tip = document.getElementById('kpiTrendChartTooltip');
    const state = window.currentKpiTrendState;
    if (!tip || !state || !state.data || !state.data.series) return;
    const pt = state.data.series[idx];
    if (!pt) return;

    const unit = state.data.metric_unit || '';
    const isInt = !!state.data.is_integer;
    let popHtml = '';
    if (pt.prev_diff_pct != null) {
        const diffNum = Number(pt.prev_diff_pct);
        const sign = diffNum > 0 ? '+' : '';
        // lower_is_better 指标：上涨标红、下降标绿
        const upGood = !(state.data && state.data.lower_is_better);
        const clr = diffNum > 0 ? (upGood ? '#4ade80' : '#f87171') : (diffNum < 0 ? (upGood ? '#f87171' : '#4ade80') : '#94a3b8');
        popHtml = '<div style="font-size:0.72rem; color:' + clr + '; margin-top:3px;">环比昨日: ' + sign + diffNum.toFixed(1) + '%</div>';
    }

    const anomalyBadge = pt.is_anomaly ? '<span style="color:#f87171; font-weight:700; margin-left:6px;">[异动预警]</span>' : '';
    const valDisplay = isInt ? Math.round(Number(pt.value)) : pt.value;

    // 元素收敛：tooltip 样本行与切片明细「构成样本」列同口径
    const mk = state.data.metric_key;
    let sampleLine = '';
    if (mk === 'avg_elapsed_ms' || mk === 'avg_tokens') {
        sampleLine = '<div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">样本量: ' + pt.denominator + '</div>';
    } else if (mk !== 'total_parses') {
        sampleLine = '<div style="font-size:0.72rem; color:#94a3b8; margin-top:2px;">样本构成: ' + pt.numerator + ' / ' + pt.denominator + '</div>';
    }

    tip.innerHTML = '<div style="font-weight:700; color:#f8fafc; font-family:monospace;">' + w2Escape(pt.date) + anomalyBadge + '</div>'
        + '<div style="font-size:0.85rem; font-weight:700; color:#60a5fa; margin-top:2px;">' + valDisplay + ' ' + w2Escape(unit) + '</div>'
        + sampleLine
        + popHtml;

    const chartBox = document.getElementById('kpiTrendChartContainer');
    if (chartBox) {
        const rect = chartBox.getBoundingClientRect();
        const layout = state._chartLayout;
        let x, y;
        if (layout && layout.points && layout.points[idx]) {
            // 锚定在数据点上方（viewBox 坐标按实际渲染宽度换算），不随光标飘移
            const scale = rect.width / layout.W;
            x = layout.points[idx].x * scale;
            y = layout.points[idx].y * scale;
        } else {
            x = e.clientX - rect.left;
            y = e.clientY - rect.top;
        }
        tip.style.display = 'block';
        const tw = tip.offsetWidth;
        const th = tip.offsetHeight;
        // 水平钳制在容器内；贴顶时翻转到点下方
        x = Math.max(tw / 2 + 4, Math.min(rect.width - tw / 2 - 4, x));
        const below = (y - th - 8) < 0;
        tip.style.left = x + 'px';
        tip.style.top = (below ? y + 14 : y - 8) + 'px';
        tip.style.transform = below ? 'translate(-50%, 0)' : 'translate(-50%, -100%)';
    }
}
window.showKpiTrendTooltip = showKpiTrendTooltip;

function hideKpiTrendTooltip() {
    const tip = document.getElementById('kpiTrendChartTooltip');
    if (tip) tip.style.display = 'none';
}
window.hideKpiTrendTooltip = hideKpiTrendTooltip;

function renderKpiTrendTable(records, meta) {
    const container = document.getElementById('kpiTrendTableContainer');
    if (!container) return;

    if (!records || records.length === 0) {
        container.innerHTML = '<div style="text-align:center; padding:20px; color:var(--text-muted); font-size:0.82rem;">暂无时序明细记录</div>';
        return;
    }

    let html = '<table class="kpi-trend-table table" style="width:100%; border-collapse:collapse; font-size:0.8rem;">'
        + '<thead><tr style="background:#f8fafc; border-bottom:1px solid #e2e8f0; color:var(--text-muted);">'
        + '<th style="padding:9px 12px; font-weight:600; text-align:left;">日期</th>'
        + '<th style="padding:9px 12px; font-weight:600; text-align:right;">当日数值</th>'
        + '<th style="padding:9px 12px; font-weight:600; text-align:right;">构成样本</th>'
        + '<th style="padding:9px 12px; font-weight:600; text-align:right;">较前日环比</th>'
        + '<th style="padding:9px 12px; font-weight:600; text-align:center;">健康评估</th>'
        + '<th style="padding:9px 12px; font-weight:600; text-align:center;">操作</th>'
        + '</tr></thead><tbody>';

    const isInt = !!meta.is_integer;

    records.forEach(r => {
        let statusBadge = '<span class="badge" style="background:#f1f5f9; color:#475569;">[正常]</span>';
        if (r.health_status === '[异动预警]') {
            statusBadge = '<span class="badge" style="background:#fee2e2; color:#b91c1c; border:1px solid #fecaca;">[异动预警]</span>';
        } else if (r.health_status === '[样本不足]') {
            statusBadge = '<span class="badge" style="background:#fef3c7; color:#b45309; border:1px solid #fde68a;">[样本不足]</span>';
        } else if (r.health_status === '[无数据]') {
            statusBadge = '<span class="badge" style="background:#f1f5f9; color:#94a3b8;">[无数据]</span>';
        }

        // 纯数值展示，单元格不拼接"单"等汉字单位
        let displayVal = r.formatted_value;
        if (!displayVal) {
            displayVal = isInt ? String(Math.round(Number(r.value || 0))) : String(r.value || 0);
        }

        const sampleText = r.formatted_ratio || (r.denominator != null ? String(r.denominator) : '-');
        let momText = '-';
        if (r.prev_diff_pct != null) {
            const prefix = r.prev_diff_pct > 0 ? '+' : '';
            momText = prefix + r.prev_diff_pct + (meta.is_rate ? ' pp' : '%');
        }

        // [查看详情] 按钮联动
        html += '<tr class="kpi-trend-row" style="border-bottom:1px solid #f1f5f9;">'
            + '<td style="padding:8px 12px; text-align:left; font-family:monospace; font-weight:600;">' + w2Escape(r.date) + '</td>'
            + '<td style="padding:8px 12px; text-align:right; font-weight:700; color:#2563eb; font-variant-numeric:tabular-nums;">' + w2Escape(displayVal) + '</td>'
            + '<td style="padding:8px 12px; text-align:right; color:var(--text-muted); font-variant-numeric:tabular-nums;">' + w2Escape(sampleText) + '</td>'
            + '<td style="padding:8px 12px; text-align:right; font-variant-numeric:tabular-nums; color:' + (r.is_anomaly ? '#b91c1c;' : 'var(--text-primary);') + '">' + w2Escape(momText) + '</td>'
            + '<td style="padding:8px 12px; text-align:center;">' + statusBadge + '</td>'
            + '<td style="padding:8px 12px; text-align:center;">'
            + '<button class="btn btn-secondary btn-sm" onclick="drillDownKpiDate(\'' + w2Escape(r.date) + '\')" title="[查看当日单据详情]" style="font-size:0.75rem; padding:3px 10px; cursor:pointer;">查看详情</button>'
            + '</td>'
            + '</tr>';
    });

    html += '</tbody></table>';

    container.innerHTML = html;
}
window.renderKpiTrendTable = renderKpiTrendTable;

function drillDownKpiDate(dateStr) {
    closeKpiTrendModal();

    // 1. 先填充时间筛选（单日 = 自定义区间的起止同为该日），再切页签，
    //    避免 switchTelemetrySubTab 内部按旧筛选发起加载
    setParsingLogCustomRange(dateStr, dateStr);
    const dateRangeSel = document.getElementById('parsingLogDateRangeSelect');
    if (dateRangeSel) dateRangeSel.value = 'custom';
    if (typeof switchTelemetrySubTab === 'function') {
        switchTelemetrySubTab('audit');
    }

    // 2. 加载日志并滚动聚焦
    loadParsingLogs(1);
    const tableEl = document.getElementById('parsingLogsTableBody');
    if (tableEl) {
        tableEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}
window.drillDownKpiDate = drillDownKpiDate;

// 按当前统计周期（含自定义时间段）整段钻取日志明细
function drillDownKpiRange() {
    const state = window.currentKpiTrendState;
    const dr = state && state.data && state.data.date_range;
    if (!dr || !dr.start_date || !dr.end_date) return;

    closeKpiTrendModal();

    // 1. 先填充时间筛选（统计周期区间），再切页签：switchTelemetrySubTab
    //    内部会按当前筛选触发 loadParsingLogs，若先切页签会以旧筛选加载
    setParsingLogCustomRange(dr.start_date, dr.end_date);
    const dateRangeSel = document.getElementById('parsingLogDateRangeSelect');
    if (dateRangeSel) dateRangeSel.value = 'custom';

    // 2. 切入「单据解析与链路审计」Tab（内部按已填充的区间筛选加载）
    if (typeof switchTelemetrySubTab === 'function') {
        switchTelemetrySubTab('audit');
    }

    // 3. 若切页签前已处于 audit 视图（未触发内部加载），显式兜底加载一次
    loadParsingLogs(1);

    const tableEl = document.getElementById('parsingLogsTableBody');
    if (tableEl) {
        tableEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
}
window.drillDownKpiRange = drillDownKpiRange;

// 区块 3：灰测现状（复用 /api/admin/grey-test；非 admin 降级提示）
function loadAnalyticsGreyStatus() {
    const el = document.getElementById('analyticsGreyBody');
    if (!el) return;
    el.innerHTML = '加载中';
    const isCurrent = _analyticsSeq('greyStatus');
    apiFetch('/api/admin/grey-test')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!isCurrent()) return;
            if (!ret || ret.status !== 'success') {
                if (httpStatus === 403 || httpStatus === 401) {
                    el.innerHTML = '<span style="color:var(--text-muted);">灰测配置为 admin 专属，请切换 admin 角色查看。</span>';
                    return;
                }
                el.innerHTML = '<span style="color:#c00;">灰测状态加载失败：' + w2Escape((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)) + '</span>';
                return;
            }
            const cur = (ret.data && ret.data.current) || {};
            const on = !!cur.grey_enabled;
            const recModelDisplay = cur.recognition_model || cur.openai_rec_model || '-';
            const greyRecModelDisplay = cur.grey_recognition_model || cur.grey_openai_rec_model || '-';
            const greyAudModelDisplay = cur.grey_audit_model || cur.grey_openai_aud_model || '-';
            let html = '<div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:10px;">'
                + _analyticsStatHtml('灰测状态', on ? '开启' : '停用',
                    on ? ('概率 ' + (cur.grey_percent || 0) + '% · 分配 ' + (cur.grey_assign_mode || '-')) : '全量走常规引擎')
                + _analyticsStatHtml('常规识别引擎', recModelDisplay)
                + (on ? _analyticsStatHtml('灰测识别引擎', greyRecModelDisplay) : '')
                + (on && cur.grey_audit_enabled ? _analyticsStatHtml('灰测审核引擎', greyAudModelDisplay) : '')
                + '</div>';
            html += '<div style="font-size:0.75rem; color:var(--text-muted);">配置快照来自引擎配置实时读取；调整请前往「引擎与系统配置」页签（admin）。</div>';
            el.innerHTML = html;
        })
        .catch(err => {
            console.error('灰测现状加载失败', err);
            el.innerHTML = '<span style="color:#c00;">灰测状态加载失败，请稍后重试</span>';
        });
}

// 区块 4：A/B 实验数据（复用 /api/admin/experiments + pvalue；非 admin 降级提示）
function loadAnalyticsExperiments() {
    const el = document.getElementById('analyticsExperimentsBody');
    if (!el) return;
    el.innerHTML = '加载中';
    const isCurrent = _analyticsSeq('experiments');
    apiFetch('/api/admin/experiments')
        .then(res => Promise.all([res.status, res.json().catch(() => null)]))
        .then(([httpStatus, ret]) => {
            if (!isCurrent()) return;
            if (!ret || ret.status !== 'success') {
                if (httpStatus === 403 || httpStatus === 401) {
                    el.innerHTML = '<span style="color:var(--text-muted);">A/B 实验为 admin 专属，请切换 admin 角色查看。</span>';
                    return;
                }
                el.innerHTML = '<span style="color:#c00;">实验列表加载失败：' + w2Escape((ret && (ret.msg || ret.detail)) || ('HTTP ' + httpStatus)) + '</span>';
                return;
            }
            const exps = ret.data || [];
            if (exps.length === 0) {
                el.innerHTML = '<span style="color:var(--text-muted);">暂无 A/B 实验。灰测开启后可在实验管理创建，样本回流后此处展示指标与 p 值。</span>';
                return;
            }
            // 填充顶部下拉切换框
            const expSelect = document.getElementById('analyticsExpSelect');
            if (expSelect) {
                let optHtml = '';
                exps.forEach(e => {
                    optHtml += `<option value="${e.id}">#${e.id} ${w2Escape(e.name || '实验')} (${expStatusLabel(e.status)})</option>`;
                });
                expSelect.innerHTML = optHtml;
            }

            let html = '<div class="table-container" style="overflow-x:auto;"><table class="data-table" id="analyticsExperimentsTable" style="width:100%; min-width:980px; table-layout:auto;">'
                + '<thead><tr>'
                + '<th class="col-center" style="width:80px; text-align:center;">编号</th>'
                + '<th style="min-width:180px; text-align:left;">实验名称</th>'
                + '<th class="col-center" style="width:100px; text-align:center;">状态</th>'
                + '<th style="min-width:130px; text-align:left;">主指标</th>'
                + '<th class="col-right" style="width:100px; text-align:right;">目标流量</th>'
                + '<th class="col-right" style="width:100px; text-align:right;">最小样本</th>'
                + '<th style="min-width:140px; text-align:left;">结论</th>'
                + '<th class="col-right" style="width:120px; text-align:right;">操作</th>'
                + '</tr></thead><tbody>';
            exps.forEach(e => {
                html += '<tr>'
                    + '<td class="col-center" style="text-align:center;"><strong>#' + Number(e.id) + '</strong></td>'
                    + '<td style="text-align:left; font-weight:600;">' + w2Escape(e.name || '-') + '</td>'
                    + '<td class="col-center" style="text-align:center;"><span class="badge" style="' + expStatusStyle(e.status) + ' font-size:0.75rem; padding:3px 8px;">' + w2Escape(expStatusLabel(e.status, '-')) + '</span></td>'
                    + '<td style="text-align:left;"><code style="font-family:monospace; font-size:0.8rem;">' + w2Escape(e.success_metric || '-') + '</code></td>'
                    + '<td class="col-right" style="text-align:right; font-variant-numeric:tabular-nums;">' + Number(e.target_percent || 0) + '%</td>'
                    + '<td class="col-right" style="text-align:right; font-variant-numeric:tabular-nums;">' + Number(e.min_sample || 0) + '</td>'
                    + '<td style="text-align:left; line-height:1.4;">' + w2Escape(e.conclusion || '-') + '</td>'
                    + '<td class="col-right" style="text-align:right;"><button class="btn btn-secondary" style="font-size:0.75rem; padding:4px 10px;" onclick="inspectExperimentDetail(' + Number(e.id) + ')">查看深度档案</button></td>'
                    + '</tr>';
            });
            html += '</tbody></table></div>';
            el.innerHTML = html;

            // 确定当前待检查的实验 ID
            const running = exps.find(e => e.status === 'running') || exps[0];
            const pvalSel = document.getElementById('pvalueExperimentSelect');
            let targetId = (pvalSel && pvalSel.value) ? Number(pvalSel.value) : (running ? Number(running.id) : null);
            if (_currentInspectedExpId && exps.some(e => Number(e.id) === Number(_currentInspectedExpId))) {
                targetId = _currentInspectedExpId;
            }
            if (targetId) {
                inspectExperimentDetail(targetId);
            }
        })
        .catch(err => {
            console.error('实验列表加载失败', err);
            el.innerHTML = '<span style="color:#c00;">实验列表加载失败，请稍后重试</span>';
        });
}

let _currentInspectedExpId = null;

function inspectExperimentDetail(expId) {
    if (!expId) return;
    _currentInspectedExpId = Number(expId);

    const sel = document.getElementById('analyticsExpSelect');
    if (sel && sel.value !== String(expId)) sel.value = String(expId);

    const pvalSel = document.getElementById('pvalueExperimentSelect');
    if (pvalSel && pvalSel.value !== String(expId)) pvalSel.value = String(expId);

    const titleEl = document.getElementById('expDetailHeaderName');
    const metaEl = document.getElementById('expMetaDossier');
    const sbsBody = document.getElementById('expSideBySideBody');

    if (titleEl) titleEl.textContent = `实验 #${expId}`;
    if (metaEl) metaEl.innerHTML = '正在拉取实验元数据与全链路指标...';
    if (sbsBody) sbsBody.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#666; padding:16px;">计算 Control vs Treatment 组别深度指标中...</td></tr>';

    apiFetch(`/api/admin/experiments/${expId}`)
        .then(r => r.json())
        .then(res => {
            if (!res || res.status !== 'success' || !res.experiment) {
                if (metaEl) metaEl.innerHTML = '<span style="color:#c00;">获取实验详情失败</span>';
                return;
            }
            const exp = res.experiment;
            const metrics = res.metrics || {};
            const detail = res.detail || {};
            if (titleEl) {
                titleEl.innerHTML = `<strong>#${exp.id} ${w2Escape(exp.name)}</strong> <span class="badge" style="${expStatusStyle(exp.status)} font-size:0.72rem; padding:2px 6px; margin-left:6px;">${w2Escape(expStatusLabel(exp.status))}</span>`;
            }

            const startFormatted = exp.start_ts ? _fmtDateTime(exp.start_ts) : '未记录启动时间';
            const endFormatted = exp.end_ts ? _fmtDateTime(exp.end_ts) : (exp.status === 'running' ? '持续运行中' : '未记录');
            let metaHtml = '<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:12px;">'
                + '<div><b>实验名称：</b><strong style="color:#2563eb;">' + w2Escape(exp.name || '-') + '</strong></div>'
                + '<div><b>科学假设：</b>' + w2Escape(exp.hypothesis || '未设定假设') + '</div>'
                + '<div><b>主成功指标：</b><code>' + w2Escape(exp.success_metric || 'accuracy') + '</code></div>'
                + '<div><b>目标流量比例：</b>' + Number(exp.target_percent || 0) + '% (Control : Treatment = ' + (100 - Number(exp.target_percent || 0)) + ' : ' + Number(exp.target_percent || 0) + ')</div>'
                + '<div><b>样本门槛：</b>最小 ' + (exp.min_sample != null ? Number(exp.min_sample) : 30) + ' 样本</div>'
                + '<div><b>起止周期：</b>' + w2Escape(startFormatted) + ' ~ ' + w2Escape(endFormatted) + '</div>';

            if (exp.conclusion) {
                const CONCLUSION_LABELS = { promote: '推全实验组', rollback: '回滚对照组', inconclusive: '不显著/无差异' };
                metaHtml += '<div style="grid-column: 1 / -1; background:#fff; border:1px solid var(--border-color); border-radius:6px; padding:8px 12px; margin-top:4px;">'
                    + '<b>结题结论：</b><span class="badge badge-success" style="margin-right:8px;">' + w2Escape(CONCLUSION_LABELS[exp.conclusion] || exp.conclusion) + '</span>'
                    + '<span style="color:var(--text-muted);">结题人：' + w2Escape(exp.concluded_by || 'admin') + ' · ' + w2Escape(exp.concluded_at ? _fmtDateTime(exp.concluded_at) : '-') + '</span>'
                    + (exp.conclusion_reason ? '<div style="margin-top:4px; color:var(--text-main);"><b>结题依据：</b>' + w2Escape(exp.conclusion_reason) + '</div>' : '')
                    + '</div>';
            }
            metaHtml += '</div>';
            if (metaEl) metaEl.innerHTML = metaHtml;

            // 组别指标 Side-by-Side 深度对比表
            const ctrl = (detail.groups && detail.groups.control) || metrics.control || {};
            const treat = (detail.groups && detail.groups.treatment) || metrics.treatment || {};
            const assigns = detail.assignments || {};
            const tests = metrics.tests || {};

            const fmtPct = (val) => (val != null ? (Number(val) * 100).toFixed(2) + '%' : '-');
            const fmtDiff = (cVal, tVal, isHigherBetter = true) => {
                if (cVal == null || tVal == null) return '-';
                const diff = (Number(tVal) - Number(cVal)) * 100;
                const sign = diff > 0 ? '+' : '';
                const isGood = isHigherBetter ? diff > 0 : diff < 0;
                const color = diff === 0 ? 'var(--text-muted)' : (isGood ? '#166534' : '#991b1b');
                return `<span style="font-weight:600; color:${color};">${sign}${diff.toFixed(2)} pp</span>`;
            };

            const rowsData = [
                {
                    dim: '归属单据量 (Receipts)',
                    c: (assigns.control != null ? assigns.control : '-') + ' 单',
                    t: (assigns.treatment != null ? assigns.treatment : '-') + ' 单',
                    diff: '-',
                    note: '按流量比例分配'
                },
                {
                    dim: '决策样本量 (Decisions N)',
                    c: (ctrl.n || ctrl.sample_size || 0) + ' 决策',
                    t: (treat.n || treat.sample_size || 0) + ' 决策',
                    diff: '-',
                    note: ((ctrl.n || ctrl.sample_size || 0) >= 30 && (treat.n || treat.sample_size || 0) >= 30)
                        ? '<span class="badge badge-success" style="font-size:0.72rem;">样本充分 (N≥30)</span>'
                        : '<span class="badge badge-warning" style="font-size:0.72rem;">样本量较小 (N<30)</span>'
                },
                {
                    dim: '字段提取准确率 (Accuracy)',
                    c: fmtPct(ctrl.accuracy),
                    t: fmtPct(treat.accuracy),
                    diff: fmtDiff(ctrl.accuracy, treat.accuracy, true),
                    note: tests.accuracy && tests.accuracy.p_value != null
                        ? `p = ${tests.accuracy.p_value} (${tests.accuracy.p_value < 0.05 ? '显著' : '不显著'})`
                        : '待累积检验'
                },
                {
                    dim: '人工修正率 (Edit Rate)',
                    c: fmtPct(ctrl.edit_rate),
                    t: fmtPct(treat.edit_rate),
                    diff: fmtDiff(ctrl.edit_rate, treat.edit_rate, false),
                    note: tests.edit_rate && tests.edit_rate.p_value != null
                        ? `p = ${tests.edit_rate.p_value} (${tests.edit_rate.p_value < 0.05 ? '显著' : '不显著'})`
                        : '待累积检验'
                },
                {
                    dim: '模型幻觉率 (Hallucination Rate)',
                    c: fmtPct(ctrl.hallucination_rate),
                    t: fmtPct(treat.hallucination_rate),
                    diff: fmtDiff(ctrl.hallucination_rate, treat.hallucination_rate, false),
                    note: tests.hallucination_rate && tests.hallucination_rate.p_value != null
                        ? `p = ${tests.hallucination_rate.p_value} (${tests.hallucination_rate.p_value < 0.05 ? '显著' : '不显著'})`
                        : '待累积检验'
                },
                {
                    dim: '审核采纳率 (Audit Adoption)',
                    c: fmtPct(ctrl.audit_adoption_rate),
                    t: fmtPct(treat.audit_adoption_rate),
                    diff: fmtDiff(ctrl.audit_adoption_rate, treat.audit_adoption_rate, true),
                    note: '双模型复核一致性'
                }
            ];

            let sbsHtml = '';
            rowsData.forEach(r => {
                sbsHtml += '<tr>'
                    + '<td style="font-weight:600; color:var(--text-main);">' + r.dim + '</td>'
                    + '<td><code style="font-size:0.82rem;">' + r.c + '</code></td>'
                    + '<td><code style="font-size:0.82rem; color:#2563eb;">' + r.t + '</code></td>'
                    + '<td>' + r.diff + '</td>'
                    + '<td style="font-size:0.78rem; color:var(--text-muted);">' + r.note + '</td>'
                    + '</tr>';
            });
            if (sbsBody) sbsBody.innerHTML = sbsHtml;

            // 守护事件展示
            const gEvents = res.guardrail_events || [];
            const gSec = document.getElementById('expGuardrailSection');
            const gBody = document.getElementById('expGuardrailBody');
            if (gSec && gBody) {
                if (gEvents.length > 0) {
                    gSec.style.display = 'block';
                    let gHtml = '<div style="display:flex; flex-direction:column; gap:8px;">';
                    gEvents.forEach(ev => {
                        gHtml += '<div style="background:#fff; border:1px solid var(--border-color); border-radius:6px; padding:8px 12px;">'
                            + '<span class="badge badge-danger" style="margin-right:8px;">' + w2Escape(ev.action) + '</span>'
                            + '<span style="font-size:0.75rem; color:var(--text-muted); font-family:var(--font-mono); font-variant-numeric:tabular-nums; margin-right:12px;">' + w2Escape(_fmtDateTime(ev.ts)) + '</span>'
                            + '<span>' + w2Escape(ev.reason || '守护策略触发') + '</span>'
                            + '</div>';
                    });
                    gHtml += '</div>';
                    gBody.innerHTML = gHtml;
                } else {
                    gSec.style.display = 'none';
                }
            }

            // 同步刷新 p-value 卡片和实验关联单据样本
            if (typeof loadPValueCards === 'function') loadPValueCards(expId);
            loadExpSamples(expId);
        })
        .catch(err => {
            console.error('加载实验详情异常', err);
            if (metaEl) metaEl.innerHTML = '<span style="color:#c00;">加载实验详情异常</span>';
        });
}

function refreshCurrentExpDetail() {
    if (_currentInspectedExpId) {
        inspectExperimentDetail(_currentInspectedExpId);
    } else {
        const sel = document.getElementById('analyticsExpSelect');
        if (sel && sel.value) inspectExperimentDetail(sel.value);
    }
}

function loadExpSamples(expId) {
    const targetExpId = expId || _currentInspectedExpId;
    if (!targetExpId) return;
    const tbody = document.getElementById('expSampleTableBody');
    const countEl = document.getElementById('expSampleTotalCount');
    const scopeSel = document.getElementById('expSampleScopeSelect');
    const scope = scopeSel ? scopeSel.value : 'all';

    if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#666; padding:16px;">加载实验关联单据样本明细流中...</td></tr>';

    apiFetch(`/api/admin/experiments/${targetExpId}/samples?scope=${encodeURIComponent(scope)}`)
        .then(r => r.json())
        .then(res => {
            if (!res || res.status !== 'success') {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:16px;">拉取实验单据样本失败</td></tr>';
                return;
            }
            if (countEl) countEl.textContent = res.total_samples || 0;
            const samples = res.samples || [];
            if (samples.length === 0) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#999; padding:16px;">暂无该组别的单据样本明细</td></tr>';
                return;
            }
            let html = '';
            samples.forEach(s => {
                const grpBadge = s.grp === 'treatment'
                    ? '<span class="badge" style="background:#ede7f6; color:#512da8; font-size:0.72rem; padding:2px 6px;">实验组 Treatment</span>'
                    : '<span class="badge" style="background:#e0f2fe; color:#0369a1; font-size:0.72rem; padding:2px 6px;">对照组 Control</span>';
                const matchBadge = s.is_match
                    ? '<span class="badge badge-success" style="font-size:0.72rem; padding:2px 6px;">完全采纳</span>'
                    : '<span class="badge badge-warning" style="font-size:0.72rem; padding:2px 6px;">人工修正</span>';

                html += '<tr>'
                    + '<td><strong>#' + Number(s.receipt_id) + '</strong></td>'
                    + '<td>' + grpBadge + '</td>'
                    + '<td><span style="color:#2f6b4f; font-weight:600;">' + w2Escape(s.supplier_name) + '</span></td>'
                    + '<td><code style="font-weight:600;">' + w2Escape(s.total_amount) + '</code></td>'
                    + '<td><code>' + w2Escape(s.model || '-') + '</code></td>'
                    + '<td><b>' + (s.accuracy != null ? (s.accuracy * 100).toFixed(1) + '%' : '-') + '</b></td>'
                    + '<td>' + matchBadge + '</td>'
                    + '<td style="font-family:var(--font-mono); font-variant-numeric:tabular-nums; font-size:0.75rem; text-align:right; color:var(--text-muted);">' + w2Escape(_fmtDateTime(s.assigned_at)) + '</td>'
                    + '</tr>';
            });
            if (tbody) tbody.innerHTML = html;
        })
        .catch(err => {
            console.error('加载实验样本明细流失败', err);
            if (tbody) tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; color:#c00; padding:16px;">加载样本异常</td></tr>';
        });
}

function loadExperimentDetail(expId) {
    if (expId) {
        inspectExperimentDetail(expId);
    } else {
        loadAnalyticsExperiments();
    }
}

if (typeof window !== 'undefined') {
    window.loadExperimentDetail = loadExperimentDetail;
    window.loadAnalyticsExperiments = loadAnalyticsExperiments;
    window.inspectExperimentDetail = inspectExperimentDetail;
    window.refreshCurrentExpDetail = refreshCurrentExpDetail;
    window.loadExpSamples = loadExpSamples;
}


// =====================================================================
