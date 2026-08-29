// -*- coding: utf-8 -*-
// GT 人工抽检台前端（T4 Gap A2）。
// 数据源：/api/evalset/*（仅 admin）。角色与主台一致：localStorage('demo_role') → X-Role 头。
// 快捷键：J 下一张 / K 上一张 / A 确认当前并跳下一张。

(function () {
    'use strict';

    var samples = [];          // 当前过滤条件下的样本列表
    var pos = -1;              // 当前样本下标
    var current = null;        // 当前样本详情（含 gt）
    var stats = null;
    var formDirty = false;     // 当前表单是否有已编辑未保存的内容

    function role() {
        try { return localStorage.getItem('demo_role') || 'admin'; } catch (e) { return 'admin'; }
    }

    function headers(extra) {
        var h = { 'X-Role': role() };
        if (extra) { for (var k in extra) { h[k] = extra[k]; } }
        return h;
    }

    function toast(msg, isErr) {
        var el = document.getElementById('toast');
        el.textContent = msg;
        el.className = 'toast' + (isErr ? ' err' : '');
        el.style.display = 'block';
        clearTimeout(el._timer);
        el._timer = setTimeout(function () { el.style.display = 'none'; }, 3200);
    }

    function api(url, options) {
        options = options || {};
        options.headers = headers(options.body ? { 'Content-Type': 'application/json' } : null);
        return fetch(url, options).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                if (!resp.ok) {
                    var msg = (data && data.detail) || (data && data.msg) ||
                              ('请求失败（HTTP ' + resp.status + '）');
                    if (resp.status === 403) {
                        msg = '权限不足：GT 抽检台仅限 admin 角色。请回主界面右上角切换角色为 admin 后刷新本页。';
                    }
                    var err = new Error(msg);
                    err.status = resp.status;
                    throw err;
                }
                return data;
            });
        });
    }

    // ---------------- 统计与进度条 ----------------
    function loadStats() {
        return api('/api/evalset/stats').then(function (data) {
            stats = data.data.splits;
            renderProgress();
        });
    }

    function renderProgress() {
        var split = document.getElementById('splitSelect').value;
        var s = (stats && stats[split]) || { confirmed: 0, draft: 0, missing: 0, total: 0 };
        var pct = s.total ? Math.round(s.confirmed / s.total * 100) : 0;
        document.getElementById('progressFill').style.width = pct + '%';
        document.getElementById('progressText').textContent =
            split + ' 集进度：已确认 ' + s.confirmed + ' / ' + s.total +
            '（' + pct + '%）｜待抽检 ' + s.draft + '｜缺 GT ' + s.missing;
    }

    // ---------------- 列表加载 ----------------
    function loadSamples() {
        var split = document.getElementById('splitSelect').value;
        var status = document.getElementById('statusSelect').value;
        var qs = 'split=' + encodeURIComponent(split) +
                 (status ? '&gt_status=' + encodeURIComponent(status) : '');
        return api('/api/evalset/samples?' + qs).then(function (data) {
            samples = data.data.samples || [];
            document.getElementById('posText').textContent = samples.length ?
                ('本批 ' + samples.length + ' 张') : '本批没有样本';
            if (!samples.length) {
                showEmpty('当前过滤条件下没有样本。若刚生成完候选，请点「刷新」。');
                return;
            }
            hideEmpty();
            gotoSample(0);
        });
    }

    function reloadAll() {
        loadStats().then(loadSamples).catch(showErr);
        loadCandidates().catch(function () {});
    }

    function onFilterChange() {
        pos = -1;
        current = null;
        renderProgress();
        loadSamples().catch(showErr);
    }

    // ---------------- 样本详情渲染 ----------------
    function gotoSample(idx) {
        if (!samples.length) { return; }
        if (idx < 0) { idx = 0; }
        if (idx >= samples.length) {
            toast('已是本批最后一张。' + (document.getElementById('statusSelect').value === 'draft' ?
                '若全部抽检完，可切到「全部状态」或刷新。' : ''));
            idx = samples.length - 1;
        }
        pos = idx;
        var sid = samples[pos].sample_id;
        document.getElementById('posText').textContent =
            '第 ' + (pos + 1) + ' / ' + samples.length + ' 张（' + sid + '）';
        var box = document.getElementById('imgBox');
        box.innerHTML = '<div class="loading-note">加载图片中…</div>';
        api('/api/evalset/sample/' + encodeURIComponent(sid)).then(function (data) {
            current = data.data;
            renderImage();
            renderForm();
        }).catch(showErr);
    }

    function renderImage() {
        var box = document.getElementById('imgBox');
        if (current.image_missing) {
            box.innerHTML = '<div class="img-missing">' + current.image_missing + '</div>';
            document.getElementById('imgMeta').textContent = '';
            return;
        }
        box.innerHTML = '';
        var img = document.createElement('img');
        img.alt = '样本预览 ' + current.sample_id;
        img.src = 'data:' + (current.image_mime || 'image/jpeg') + ';base64,' + current.image_base64;
        box.appendChild(img);
        document.getElementById('imgMeta').textContent =
            current.sample_id + '｜' + (current.doc_form || '') + '｜候选来源：' +
            (current.gt_source_model || '无');
    }

    function renderForm() {
        formDirty = false;
        var gt = current.gt || {};
        document.getElementById('fSupplier').value = gt.supplier_name || '';
        document.getElementById('fDate').value = gt.date || '';
        var total = gt.total_amount;
        document.getElementById('fTotal').value =
            (total === null || total === undefined || total === '') ? '' : total;
        document.getElementById('fDocForm').value = gt.doc_form || 'printed_delivery_note';

        var body = document.getElementById('itemsBody');
        body.innerHTML = '';
        var items = (gt.items && gt.items.length) ? gt.items : [];
        if (!items.length) {
            addItemRow();
        } else {
            items.forEach(function (it) { addItemRow(it); });
        }

        var badge = document.getElementById('gtStatusBadge');
        var st = current.gt_status || 'missing';
        badge.textContent = st === 'confirmed' ? '已确认' :
            (st === 'draft' ? '待抽检（draft）' : '缺 GT');
        badge.className = st === 'confirmed' ? 'status-ok' :
            (st === 'draft' ? 'status-draft' : 'status-missing');
        document.getElementById('confirmBtn').textContent =
            st === 'confirmed' ? '更新确认内容（A）' : '确认本张（A）并跳下一张';
    }

    function addItemRow(it) {
        it = it || {};
        // 纵深防御：候选 GT 可能仍带 quantity 键（旧数据/服务端兜底前），渲染层统一读 qty
        if (it.qty === undefined || it.qty === null) { it.qty = it.quantity; }
        var tr = document.createElement('tr');
        ['name', 'qty', 'unit', 'unit_price', 'amount'].forEach(function (key) {
            var td = document.createElement('td');
            var input = document.createElement('input');
            input.type = (key === 'name' || key === 'unit') ? 'text' : 'number';
            if (key !== 'name' && key !== 'unit') { input.step = '0.01'; }
            input.value = it[key] === null || it[key] === undefined ? '' : it[key];
            input.dataset.field = key;
            td.appendChild(input);
            tr.appendChild(td);
        });
        var tdDel = document.createElement('td');
        var del = document.createElement('button');
        del.className = 'row-del';
        del.textContent = '删';
        del.title = '删除本明细行';
        del.onclick = function () { tr.remove(); };
        tdDel.appendChild(del);
        tr.appendChild(tdDel);
        document.getElementById('itemsBody').appendChild(tr);
    }

    function collectGt() {
        var items = [];
        var rows = document.querySelectorAll('#itemsBody tr');
        rows.forEach(function (tr) {
            var it = {};
            tr.querySelectorAll('input').forEach(function (input) {
                var v = input.value.trim();
                if (input.dataset.field === 'name' || input.dataset.field === 'unit') {
                    it[input.dataset.field] = v;
                } else {
                    it[input.dataset.field] = v === '' ? null : parseFloat(v);
                }
            });
            // 跳过整行为空的行
            if (!(it.name === '' && it.qty === null && it.amount === null)) {
                items.push(it);
            }
        });
        return {
            supplier_name: document.getElementById('fSupplier').value.trim(),
            date: document.getElementById('fDate').value.trim(),
            total_amount: document.getElementById('fTotal').value === '' ?
                null : parseFloat(document.getElementById('fTotal').value),
            doc_form: document.getElementById('fDocForm').value,
            items: items
        };
    }

    // ---------------- 确认 ----------------
    function isBlankTotal(v) {
        return v === null || v === undefined || (typeof v === 'string' && v.trim() === '');
    }

    // GT v2 字段（对齐 ReceiptData 契约）：队列页表单只渲染核心字段，
    // 付款标记/币种/费用/注记等从 AI 候选原样透传（完整编辑走工作台
    // /evalset/workbench/<sid>，那里复用店员复核界面含付款标记控件）。
    var GT_V2_KEYS = ['payment_marked', 'payment_evidence', 'currency',
        'discount_amount', 'deposit_amount', 'delivery_fee', 'service_fee',
        'tax_amount', 'rounding_adjustment', 'adjustment_notes'];
    var GT_V2_FEE_KEYS = ['discount_amount', 'deposit_amount', 'delivery_fee',
        'service_fee', 'tax_amount', 'rounding_adjustment'];

    function mergeGtV2(gt) {
        var src = (current && current.gt) || {};
        GT_V2_KEYS.forEach(function (k) {
            if (gt[k] === undefined && src[k] !== undefined) { gt[k] = src[k]; }
        });
        // 类型兜底（旧候选缺 v2 字段时给中性默认，满足 confirm v2 校验）
        if (typeof gt.payment_marked !== 'boolean') {
            gt.payment_marked = src.payment_marked === true || src.payment_mark === '已付款';
        }
        if (typeof gt.payment_evidence !== 'string') { gt.payment_evidence = ''; }
        if (typeof gt.currency !== 'string' || !gt.currency) { gt.currency = 'HKD'; }
        GT_V2_FEE_KEYS.forEach(function (k) { gt[k] = Number(gt[k]) || 0; });
        if (!Array.isArray(gt.adjustment_notes)) { gt.adjustment_notes = []; }
        return gt;
    }

    function confirmCurrent(advance) {
        if (!current) { toast('当前没有可确认的样本', true); return; }
        var gt = mergeGtV2(collectGt());
        if (!gt.supplier_name) { toast('供应商名称为空，请先核对图面填写', true); return; }
        if (!gt.date) { toast('开单日期为空：图面确实无日期时可填 1970-01-01 并在明细备注', true); return; }
        var blankTotal = isBlankTotal(gt.total_amount);
        if (blankTotal && !window.confirm('总额为空。若图面确无总额（如月结单）可留空确认；' +
                '否则请回到表单核实图面金额。确定留空吗？')) {
            return;
        }
        var payload = { gt: gt };
        if (blankTotal) { payload.confirm_blank_total = true; }
        api('/api/evalset/sample/' + encodeURIComponent(current.sample_id) + '/confirm', {
            method: 'POST',
            body: JSON.stringify(payload)
        }).then(function () {
            toast('已确认 ' + current.sample_id);
            // 同步本地列表状态，避免重复确认
            samples.forEach(function (s) {
                if (s.sample_id === current.sample_id) { s.gt_status = 'confirmed'; }
            });
            if (advance && document.getElementById('statusSelect').value === 'draft') {
                // draft 视图：当前张已确认，原地刷新列表后继续看下一张
                loadSamples().catch(showErr);
            } else if (advance) {
                gotoSample(pos + 1);
            } else {
                loadStats().then(renderFormFallback).catch(function () {});
            }
        }).catch(showErr);
    }

    function renderFormFallback() {
        // 确认后刷新进度（不移动指针）
        if (current) {
            current.gt_status = 'confirmed';
            renderForm();
        }
    }

    // 批量原样确认：对本批 draft 样本逐张取回服务端 AI 候选原样提交。
    // 风险显式化：不做逐张人工核对（一键操作），因此弹窗必须列出样本 ID 清单与总数，
    // 并明确警告「未经人工逐张核对，将直接参与出分」；若当前表单有未保存编辑，先让用户选择
    // 「放弃编辑」或「取消批量」，避免静默丢失人工校正。
    function batchConfirmUntouched() {
        if (document.getElementById('statusSelect').value !== 'draft') {
            toast('批量确认只对「待抽检（draft）」视图可用', true);
            return;
        }
        var draftCount = samples.length;
        if (!draftCount) { toast('本批没有待抽检样本', true); return; }
        if (formDirty &&
                !window.confirm('当前样本的表单有已编辑但未保存的内容，批量确认不会包含这些编辑' +
                    '（将切换样本导致编辑丢失）。选择「确定」放弃编辑并继续批量，或「取消」先保存。')) {
            return;
        }
        var ids = samples.map(function (s) { return s.sample_id; });
        if (!window.confirm('即将原样确认本批 ' + ids.length + ' 张 draft 样本：\n' + ids.join('、') +
                '\n\n警告：这些 GT 未经人工逐张核对，将直接参与出分。' +
                '仅适用于您已核对图面且候选无需改动的情况。确定继续？')) {
            return;
        }
        var done = 0, failed = 0;
        function next(i) {
            if (i >= ids.length) {
                toast('批量确认完成：成功 ' + done + ' 张' + (failed ? ('，失败 ' + failed + ' 张') : ''));
                reloadAll();
                return;
            }
            api('/api/evalset/sample/' + encodeURIComponent(ids[i]))
                .then(function (detail) {
                    var gt = detail.data.gt;
                    if (!gt) { throw new Error('无候选 GT'); }
                    // 纵深防御：候选可能带 quantity 键，提交前统一为 qty
                    var items = (gt.items || []).map(function (it) {
                        var n = {};
                        for (var k in it) { n[k] = it[k]; }
                        if (n.qty === undefined || n.qty === null) { n.qty = n.quantity; }
                        delete n.quantity;
                        return n;
                    });
                    var body = {
                        supplier_name: gt.supplier_name,
                        date: gt.date,
                        total_amount: gt.total_amount,
                        doc_form: gt.doc_form || 'printed_delivery_note',
                        items: items
                    };
                    // v2 字段（付款标记/币种/费用/注记）从候选原样透传
                    GT_V2_KEYS.forEach(function (k) {
                        if (gt[k] !== undefined) { body[k] = gt[k]; }
                    });
                    body = mergeGtV2(body);
                    var payload = { gt: body };
                    // 候选总额为空时显式确认留空（与服务端空总额守卫对齐）
                    if (isBlankTotal(body.total_amount)) { payload.confirm_blank_total = true; }
                    return api('/api/evalset/sample/' + encodeURIComponent(ids[i]) + '/confirm', {
                        method: 'POST', body: JSON.stringify(payload)
                    });
                })
                .then(function () { done++; })
                .catch(function () { failed++; })
                .then(function () { next(i + 1); });
        }
        next(0);
    }

    // ---------------- 回流候选（T6 Gap A5）----------------
    // 简化列表实现：线上失败样本（低置信/门禁拒绝/店员修改/审核分歧）在此人工取舍。
    // 晋升仅把「原图 + AI 候选 GT」带进评测集（draft），仍需在上方抽检台逐张核对确认。
    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s === null || s === undefined ? '' : String(s);
        return d.innerHTML;
    }

    function loadCandidates() {
        return api('/api/evalset/candidates?status=pending').then(function (data) {
            var list = (data.data && data.data.candidates) || [];
            var body = document.getElementById('reflowBody');
            var empty = document.getElementById('reflowEmpty');
            body.innerHTML = '';
            if (!list.length) {
                empty.textContent = '暂无待处理的回流候选。线上识别失败/低置信的单据会自动出现在这里。';
                empty.className = 'loading-note';
                return;
            }
            empty.className = 'loading-note hide';
            list.forEach(function (c) {
                var tr = document.createElement('tr');
                var conf = (c.confidence === null || c.confidence === undefined) ?
                    '-' : c.confidence;
                tr.innerHTML =
                    '<td>#' + c.id + '</td>' +
                    '<td>单据 #' + esc(c.receipt_id) + '</td>' +
                    '<td>' + esc(c.reason_label || c.reason) + '</td>' +
                    '<td>' + esc(conf) + '</td>' +
                    '<td>' + esc(c.note || '') + '</td>' +
                    '<td><div class="reflow-actions">' +
                    '<button class="btn-success">晋升到 val</button>' +
                    '<button class="btn-secondary">晋升到 test</button>' +
                    '<button class="btn-danger">不收录</button>' +
                    '</div></td>';
                var btns = tr.querySelectorAll('button');
                btns[0].onclick = function () { promoteCandidate(c.id, 'val'); };
                btns[1].onclick = function () { promoteCandidate(c.id, 'test'); };
                btns[2].onclick = function () { rejectCandidate(c.id); };
                body.appendChild(tr);
            });
        });
    }

    function promoteCandidate(id, split) {
        if (!window.confirm('把候选 #' + id + ' 晋升到 ' + split + ' 集？\n' +
                '将复制原图与 AI 候选 GT（未经人工核对，状态为 draft），' +
                '晋升后请在上方抽检台逐张核对确认。')) {
            return;
        }
        api('/api/evalset/candidates/' + id + '/promote?split=' + encodeURIComponent(split), {
            method: 'POST'
        }).then(function (data) {
            toast('已晋升为评测样本 ' + (data.data && data.data.sample_id || '') +
                '（' + split + ' 集，GT 为 AI 候选 draft，请记得抽检确认）');
            loadCandidates().catch(function () {});
            loadStats().catch(function () {});
        }).catch(showErr);
    }

    function rejectCandidate(id) {
        if (!window.confirm('不收录候选 #' + id + '？该样本将不进入评测集。')) {
            return;
        }
        api('/api/evalset/candidates/' + id + '/reject', { method: 'POST' })
            .then(function () {
                toast('已驳回候选 #' + id);
                loadCandidates().catch(function () {});
            })
            .catch(showErr);
    }

    // ---------------- 空态 / 错误 ----------------
    // 显式翻页入口（按钮用）：pos 是 IIFE 内部变量，inline onclick 拿不到，
    // 必须通过这里的包装函数转发。
    function nextSample() { gotoSample(pos + 1); }
    function prevSample() { gotoSample(pos - 1); }

    // 跳转工作台：在店员日常使用的同一套 Side-by-Side 复核界面中完成校正
    // （合并反馈②：eval 工作台复用用户界面，控制变量；本队列页保留导航/进度/过滤/批量）。
    function openWorkbench() {
        if (!current || !current.sample_id) { toast('当前没有样本，无法进入工作台', true); return; }
        window.location.href = '/evalset/workbench/' + encodeURIComponent(current.sample_id);
    }

    function showEmpty(msg) {
        var note = document.getElementById('emptyNote');
        note.textContent = msg;
        note.className = 'loading-note';
        document.getElementById('formArea').style.display = 'none';
        document.getElementById('imgBox').innerHTML = '<div class="loading-note">无样本</div>';
    }

    function hideEmpty() {
        document.getElementById('emptyNote').className = 'loading-note hide';
        document.getElementById('formArea').style.display = '';
    }

    function showErr(e) {
        toast(e && e.message ? e.message : '未知错误', true);
    }

    // ---------------- 快捷键 ----------------
    document.addEventListener('keydown', function (e) {
        var tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') { return; }
        if (e.metaKey || e.ctrlKey || e.altKey) { return; }
        if (e.key === 'j' || e.key === 'J') { gotoSample(pos + 1); }
        else if (e.key === 'k' || e.key === 'K') { gotoSample(pos - 1); }
        else if (e.key === 'a' || e.key === 'A') { confirmCurrent(true); }
    });

    // ---------------- 启动 ----------------
    document.getElementById('roleNote').textContent =
        '当前角色：' + role() + (role() === 'admin' ? '' : '（非 admin，接口将返回 403，请回主界面切换角色）');
    // 表单脏标记：任何输入即视为「已编辑未保存」，批量确认前需用户显式取舍
    document.getElementById('formArea').addEventListener('input', function () {
        formDirty = true;
    });
    reloadAll();

    // 暴露给 inline onclick
    window.onFilterChange = onFilterChange;
    window.reloadAll = reloadAll;
    window.gotoSample = gotoSample;
    window.nextSample = nextSample;
    window.prevSample = prevSample;
    window.confirmCurrent = confirmCurrent;
    window.addItemRow = addItemRow;
    window.openWorkbench = openWorkbench;
    window.batchConfirmUntouched = batchConfirmUntouched;
    window.promoteCandidate = promoteCandidate;
    window.rejectCandidate = rejectCandidate;
})();
