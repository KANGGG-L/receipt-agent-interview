// demo/static/js/cost_report.js
// 部门花销报表（Wave 2 D44 契约⑤⑥）：月 x 部门汇总 + 下钻明细。
// F4·S4-11 从 main.js 按域抽离，逐字节一致；加载顺序在 main.js 之后。
// ---------------------------------------------------------------------
// Wave 2（D44）：部门花销报表（契约⑤ 月×部门 汇总 + 契约⑥ 下钻）
// 口径：明细行 amount 按 cost_center_id 归属；月份一律以 receipt_date 为准。
// ---------------------------------------------------------------------
let lastCostReportQuery = '';
let lastCostReportData = null;

// UAT-033：缺汇率说明 —— API 已返回 unconverted / unconverted_warning / *_count；
// 合计不含缺汇率单；空表也不要吞掉提示。
function costReportMissingFxCount(d) {
    if (!d) return 0;
    if (Array.isArray(d.unconverted)) return d.unconverted.length;
    return Number(d.unconverted_count || d.missing_fx_count || 0) || 0;
}

function costReportMissingFxNotice(d, missFx) {
    const n = (missFx == null) ? costReportMissingFxCount(d) : Number(missFx) || 0;
    if (n <= 0) return '';
    const w = (d && d.unconverted_warning) ? String(d.unconverted_warning).trim() : '';
    if (w) return ' ' + w;
    return ' 有 ' + n + ' 张外币单据还没有汇率，没算进来，请到「设置」里补上汇率。';
}

function currentMonthStr() {
    const now = new Date();
    return now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0');
}

function onCostReportMonthChange() {
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
}

function setCostReportCurrentMonth() {
    const monthInput = document.getElementById('costReportMonth');
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    if (monthInput) monthInput.value = currentMonthStr();
    if (startInput) startInput.value = '';
    if (endInput) endInput.value = '';
    loadCostReport();
}

// U-07：月份未选时，默认跳到最近有已入库（approved）单据的月份；全库为空才回落当前月
function loadCostReportAutoMonth() {
    const monthInput = document.getElementById('costReportMonth');
    if (monthInput && monthInput.value.trim()) { loadCostReport(); return; }
    apiFetch('/api/receipts')
    .then(res => res.json())
    .then(ret => {
        let target = null;
        if (ret && ret.status === 'success' && Array.isArray(ret.data)) {
            const months = ret.data
                .filter(r => r && r.status === 'approved' && r.receipt_date)
                .map(r => String(r.receipt_date).slice(0, 7))
                .sort();
            if (months.length) target = months[months.length - 1];
        }
        const cur = currentMonthStr();
        const finalMonth = target || cur;
        if (monthInput) monthInput.value = finalMonth;
        loadCostReport();
        // 仅在发生自动切换（最近有记录月 ≠ 当前月）时提示
        if (target && target !== cur) {
            showToast('该月暂无进货记录，已为你切换到最近有记录的月份：' + target, 'info');
        }
    })
    .catch(() => loadCostReport());
}

function loadCostReport() {
    const monthInput = document.getElementById('costReportMonth');
    const startInput = document.getElementById('costReportStartDate');
    const endInput = document.getElementById('costReportEndDate');
    const incCheck = document.getElementById('costReportIncNonApproved');

    const month = monthInput ? monthInput.value.trim() : '';
    const startDate = startInput ? startInput.value.trim() : '';
    const endDate = endInput ? endInput.value.trim() : '';
    const incNonApp = incCheck && incCheck.checked ? 1 : 0;

    if (startDate || endDate) {
        if (!startDate || !endDate) {
            showToast('开始日期与结束日期必须成对选择', 'warning');
            return;
        }
        if (startDate > endDate) {
            showToast('开始日期不能大于结束日期', 'warning');
            return;
        }
    }

    const params = new URLSearchParams();
    if (incNonApp) params.append('include_non_approved', '1');
    if (startDate && endDate) {
        params.append('start_date', startDate);
        params.append('end_date', endDate);
        if (month) params.append('month', month);
    } else {
        let m = month || currentMonthStr();
        if (monthInput) monthInput.value = m;
        params.append('month', m);
    }

    lastCostReportQuery = params.toString();
    const summary = document.getElementById('costReportSummary');

    apiFetch('/api/cost_report?' + lastCostReportQuery)
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (!ret || ret.status !== 'success') {
            if (summary) summary.textContent = '查询失败：' + ((ret && ret.msg) || ('HTTP ' + httpStatus));
            return;
        }
        const d = ret.data || {};
        lastCostReportData = d;
        const body = document.getElementById('costReportBody');
        if (!body) return;
        body.innerHTML = '';

        const periodLabel = (d.period && d.period.label) ? d.period.label : (d.month || '');
        const depts = d.departments || [];
        const unalloc = d.unallocated || {};
        const unallocCount = Number(unalloc.count) || 0;
        const missFx = costReportMissingFxCount(d);

        // 仅真正「无记录」才早退；若有缺汇率被排除的单据，继续渲染 0 合计并显示说明
        if (depts.length === 0 && Number(d.month_total) === 0 && unallocCount === 0 && missFx === 0) {
            body.innerHTML = '<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:28px;">这段时间还没有进货记录</td></tr>';
            if (summary) {
                summary.textContent = '时段 ' + periodLabel + '：暂无进货记录。';
            }
            return;
        }

        if (depts.length > 0) {
            depts.forEach(dep => {
                const active = Number(dep.active) === 1;
                const tr = document.createElement('tr');
                tr.style.cursor = 'pointer';
                tr.title = '点击查看构成该部门成本的单据与明细';
                tr.addEventListener('click', () => openCostDrilldown(dep.id, dep.name, periodLabel));

                const prevVal = Number(dep.prev_total) || 0;
                const prevText = I18N.fmtMoney(prevVal);
                let deltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
                if (dep.delta > 0) {
                    const tag = prevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
                    deltaHtml = `<span style="color:#ef4444; font-weight:600;">+${I18N.fmtMoney(dep.delta)}</span>${tag}`;
                } else if (dep.delta < 0) {
                    deltaHtml = `<span style="color:#047857; font-weight:600;">-${I18N.fmtMoney(Math.abs(dep.delta))}</span>`;
                }

                tr.innerHTML = `
                    <td><span data-i18n-skip>${w2Escape(dep.name)}</span>${active ? '' : ' <span style="color:var(--text-muted); font-size:0.72rem;">已停用</span>'}</td>
                    <td class="col-right" style="font-weight:600;">${I18N.fmtMoney(dep.total)}</td>
                    <td class="col-right" style="color:var(--text-muted);">${prevText}</td>
                    <td class="col-right">${deltaHtml}</td>`;
                body.appendChild(tr);
            });
        }

        // 未分配桶
        const unallocSubtext = unallocCount > 0
            ? `未分配`
            : '未分配';

        const unallocTr = document.createElement('tr');
        unallocTr.style.cursor = 'pointer';
        unallocTr.title = '未打部门的明细行成本';
        unallocTr.addEventListener('click', () => openCostDrilldown(null, '未分配', periodLabel));

        const unallocPrevVal = Number(unalloc.prev_total) || 0;
        const unallocPrevText = I18N.fmtMoney(unallocPrevVal);
        let unallocDeltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
        if (unalloc.delta > 0) {
            const tag = unallocPrevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
            unallocDeltaHtml = `<span style="color:#ef4444; font-weight:600;">+${I18N.fmtMoney(unalloc.delta)}</span>${tag}`;
        } else if (unalloc.delta < 0) {
            unallocDeltaHtml = `<span style="color:#047857; font-weight:600;">-${I18N.fmtMoney(Math.abs(unalloc.delta))}</span>`;
        }

        unallocTr.innerHTML = `
            <td style="color:var(--warning);">${w2Escape(unallocSubtext)}</td>
            <td class="col-right" style="font-weight:600; color:var(--warning);">${I18N.fmtMoney(unalloc.total)}</td>
            <td class="col-right" style="color:var(--text-muted);">${unallocPrevText}</td>
            <td class="col-right">${unallocDeltaHtml}</td>`;
        body.appendChild(unallocTr);

        // 合计
        const totalTr = document.createElement('tr');
        totalTr.style.background = 'var(--bg-hover)';
        const totalPrevVal = Number(d.prev_total) || 0;
        const totalPrevText = I18N.fmtMoney(totalPrevVal);
        let totalDeltaHtml = '<span style="color:var(--text-muted);">$0.00</span>';
        if (d.delta > 0) {
            const tag = totalPrevVal === 0 ? ' <span style="font-size:0.72rem; color:var(--primary); font-weight:normal;">新增</span>' : '';
            totalDeltaHtml = `<span style="color:#ef4444; font-weight:600;">+${I18N.fmtMoney(d.delta)}</span>${tag}`;
        } else if (d.delta < 0) {
            totalDeltaHtml = `<span style="color:#047857; font-weight:600;">-${I18N.fmtMoney(Math.abs(d.delta))}</span>`;
        }

        totalTr.innerHTML = `
            <td style="font-weight:700;">合计</td>
            <td class="col-right" style="font-weight:700; color:var(--primary);">${I18N.fmtMoney(d.month_total)}</td>
            <td class="col-right" style="font-weight:700; color:var(--text-muted);">${totalPrevText}</td>
            <td class="col-right" style="font-weight:700;">${totalDeltaHtml}</td>`;
        body.appendChild(totalTr);

        if (summary) {
            const incAll = !!(document.getElementById('costReportIncNonApproved') || {}).checked;
            let txt = '时段 ' + periodLabel + '：' + (incAll ? '进货总额（含还没入账的单据）' : '已入账单据总额') + I18N.fmtMoney(d.month_total) + '，含服务费、税、折扣。';
            txt += costReportMissingFxNotice(d, missFx);
            summary.textContent = txt;
        }
    })
    .catch(err => {
        console.error('加载报表失败', err);
        if (summary) summary.textContent = '加载报表失败，请稍后重试';
    });
}

function openCostDrilldown(deptId, deptName, displayPeriod) {
    const periodStr = displayPeriod || (lastCostReportData && lastCostReportData.period ? lastCostReportData.period.label : currentMonthStr());
    const title = document.getElementById('costDrilldownTitle');
    if (title) title.textContent = (deptName || '未分配') + ' · ' + periodStr + ' · 花销明细';

    const notice = document.getElementById('costDrilldownNotice');
    if (notice) {
        if (deptId == null) {
            notice.style.display = 'block';
            notice.textContent = '提示：点击单据号可打开原单据详情。在原单里选择部门并保存后，点击下方「刷新报表」即可更新。';
        } else {
            notice.style.display = 'none';
        }
    }

    let url = '/api/cost_report/items?' + lastCostReportQuery;
    if (deptId != null) {
        url += '&department_id=' + encodeURIComponent(deptId);
    } else {
        // 未分配下钻：显式传 0，让后端按未归属过滤（部门分摊空的精准回退）
        url += '&department_id=0';
    }
    const body = document.getElementById('costDrilldownBody');
    if (body) body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">加载中…</td></tr>';

    apiFetch(url)
    .then(res => Promise.all([res.status, res.json().catch(() => null)]))
    .then(([httpStatus, ret]) => {
        if (body) body.innerHTML = '';
        if (!ret || ret.status !== 'success') {
            if (body) {
                body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#f87171; padding:24px;">'
                    + w2Escape((ret && ret.msg) || ('HTTP ' + httpStatus)) + '</td></tr>';
            }
            showModalAndScroll();
            return;
        }
        const rows = ret.data || [];
        if (rows.length === 0) {
            if (body) body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:24px;">这段时间该部门还没有进货记录</td></tr>';
            showModalAndScroll();
            return;
        }

        // 按供应商分组 (Option A)
        const groups = {};
        rows.forEach(r => {
            const supp = (r.supplier_name || '').trim() || '未填写供应商';
            if (!groups[supp]) groups[supp] = [];
            groups[supp].push(r);
        });

        Object.keys(groups).forEach(suppName => {
            const groupItems = groups[suppName];
            const subtotal = groupItems.reduce((sum, item) => sum + Number(item.amount || 0), 0);

            const headerTr = document.createElement('tr');
            headerTr.style.background = 'var(--bg-hover, #f8fafc)';
            headerTr.style.fontWeight = '600';
            headerTr.innerHTML = `
                <td colspan="5" style="color:var(--primary); padding:8px 12px;">
                    供应商：<span data-i18n-skip>${w2Escape(suppName)}</span> · 小计 ${I18N.fmtMoney(subtotal)} · ${groupItems.length} 条
                </td>`;
            body.appendChild(headerTr);

            groupItems.forEach(r => {
                const tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${renderDateCell(r.receipt_date)}</td>
                    <td>
                        <button class="btn-link" style="background:none; border:none; color:var(--primary); cursor:pointer; text-decoration:underline; padding:0; font-family:inherit; font-size:inherit;" onclick="openReceiptFromDrilldown(${Number(r.receipt_id)})">
                            #${Number(r.receipt_id)}
                        </button>
                    </td>
                    <td data-i18n-skip>${w2Escape(r.raw_name || '-')}</td>
                    <td>${Number(r.quantity)}</td>
                    <td class="col-right">${I18N.fmtMoney(r.amount)}</td>`;
                body.appendChild(tr);
            });
        });
        showModalAndScroll();
    })
    .catch(err => {
        console.error('加载花销明细失败', err);
        if (body) {
            body.innerHTML = '<tr><td colspan="5" style="text-align:center; color:#f87171; padding:24px;">加载明细失败，请稍后重试</td></tr>';
        }
        showModalAndScroll();
    });

    function showModalAndScroll() {
        const modal = document.getElementById('costDrilldownModal');
        if (modal) modal.classList.remove('hide');
    }
}

function openReceiptFromDrilldown(receiptId) {
    closeModalById('costDrilldownModal');
    loadReceiptDetail(receiptId);
}
