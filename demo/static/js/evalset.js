// -*- coding: utf-8 -*-
// GT 人工抽检队列页前端。
// 设计原则（用户反馈③收敛单一编辑表面）：本页只做导航/进度/过滤/批量/回流候选，
// 不含任何可编辑控件；单张 GT 校正的唯一编辑表面是工作台
// /evalset/workbench/<sid>（复用店员复核界面，含付款标记等全部字段，控制变量）。
// 数据源：/api/evalset/*（仅 admin）。角色与主台一致：localStorage('demo_role') → X-Role 头。
// 快捷键：J 下一张 / K 上一张（确认动作只在工作台发生）。

(function () {
    'use strict';

    function ask(opts) {
        return window.uiConfirm(opts);
    }

    var samples = [];          // 当前过滤条件下的样本列表
    var pos = -1;              // 当前样本下标
    var current = null;        // 当前样本详情（含 gt，仅用于缩略图渲染）
    var stats = null;

    var DOC_FORM_LABELS = {
        printed_delivery_note: '印刷送货单',
        ncr_handwritten: '手写单',
        thermal: '热敏小票',
        weigh_slip: '磅单',
        correction_note: '更正单',
        monthly_statement: '月结单'
    };

    function role() {
        try { return localStorage.getItem('demo_role') || 'admin'; } catch (e) { return 'admin'; }
    }

    function headers(extra) {
        var h = { 'X-Role': role(), 'X-Requested-With': 'fetch' };
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
        if (!options.credentials) options.credentials = 'same-origin';
        options.headers = headers(options.body ? { 'Content-Type': 'application/json' } : null);
        return fetch(url, options).then(function (resp) {
            return resp.json().catch(function () { return {}; }).then(function (data) {
                if (!resp.ok) {
                    var msg = (data && data.detail) || (data && data.msg) ||
                              ('请求失败（HTTP ' + resp.status + '）');
                    if (resp.status === 403) {
                        msg = '这个页面只有系统管理员能用。请回到主界面，在左侧栏最上方的「当前角色」里选「系统管理员」，再回来刷新。';
                    } else if (resp.status === 404) {
                        msg = '还没有评测集（评测集清单文件不存在）。请让技术人员先运行 build_evalset.py 生成评测集，再点「刷新」。';
                    }
                    var err = new Error(msg);
                    err.status = resp.status;
                    throw err;
                }
                return data;
            });
        });
    }

    function esc(s) {
        var d = document.createElement('div');
        d.textContent = s === null || s === undefined ? '' : String(s);
        return d.innerHTML;
    }

    function docFormLabel(code) {
        return DOC_FORM_LABELS[code] || code || '-';
    }

    function statusLabel(st) {
        return st === 'confirmed' ? '已确认' :
            (st === 'draft' ? '待抽检' : (st === 'missing' ? '缺 GT' : (st || '-')));
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

    // ---------------- 样本列表 ----------------
    function loadSamples() {
        var split = document.getElementById('splitSelect').value;
        var status = document.getElementById('statusSelect').value;
        var qs = 'split=' + encodeURIComponent(split) +
                 (status ? '&gt_status=' + encodeURIComponent(status) : '');
        return api('/api/evalset/samples?' + qs).then(function (data) {
            samples = data.data.samples || [];
            document.getElementById('posText').textContent = samples.length ?
                ('本批 ' + samples.length + ' 张') : '本批没有样本';
            renderSampleList();
            if (!samples.length) {
                showEmpty('当前过滤条件下没有样本。若刚生成完候选，请点「刷新」。');
                return;
            }
            hideEmpty();
            gotoSample(0);
        });
    }

    function renderSampleList() {
        var wrap = document.getElementById('sampleList');
        wrap.innerHTML = '';
        if (!samples.length) {
            wrap.innerHTML = '<div class="loading-note">无样本</div>';
            return;
        }
        samples.forEach(function (s, i) {
            var row = document.createElement('div');
            row.className = 'sample-row' + (i === pos ? ' selected' : '');
            row.innerHTML =
                '<span class="sample-id">' + esc(s.sample_id) + '</span>' +
                '<span class="sample-form">' + esc(docFormLabel(s.doc_form)) + '</span>' +
                '<span class="sample-status st-' + esc(s.gt_status || 'missing') + '">' +
                esc(statusLabel(s.gt_status)) + '</span>';
            row.title = '点击选中该样本';
            var btn = document.createElement('button');
            btn.className = 'btn-secondary row-workbench';
            btn.textContent = '在工作台核对';
            btn.title = '打开与店员复核界面同源的工作台（唯一编辑表面）';
            btn.onclick = function (e) {
                e.stopPropagation();
                openWorkbenchFor(s.sample_id);
            };
            row.appendChild(btn);
            row.onclick = function () { if (i !== pos) { gotoSample(i); } };
            wrap.appendChild(row);
        });
    }

    // 评测集缺失或接口失败：每个面板都给出空状态/错误状态，不能一直停在「加载中…」
    function panelState(bodyId, emptyId, cols, msg) {
        var body = document.getElementById(bodyId);
        var empty = document.getElementById(emptyId);
        if (body) { body.innerHTML = ''; }
        if (empty) { empty.textContent = msg; empty.className = 'loading-note'; }
    }

    function showEvalUnavailable(e) {
        var msg = (e && e.message) || '评测集暂时读不出来，请稍后点「刷新」重试。';
        document.getElementById('progressText').textContent = '评测集不可用';
        document.getElementById('progressFill').style.width = '0%';
        document.getElementById('posText').textContent = '';
        document.getElementById('imgBox').innerHTML = '<div class="loading-note">没有可预览的单据</div>';
        document.getElementById('sampleList').innerHTML = '<div class="loading-note">' + esc(msg) + '</div>';
        document.getElementById('infoArea').style.display = 'none';
        var note = document.getElementById('emptyNote');
        note.textContent = msg;
        note.className = 'loading-note';
        var batch = document.getElementById('batchBtn');
        if (batch) { batch.disabled = true; }
    }

    function reloadAll() {
        var batch = document.getElementById('batchBtn');
        if (batch) { batch.disabled = false; }
        loadStats().then(loadSamples).catch(showEvalUnavailable);
        loadCandidates().catch(function (e) {
            panelState('reflowBody', 'reflowEmpty', 6, (e && e.status === 404) ? '评测集还没建好，暂时没有回流候选。' : ((e && e.message) || '回流候选加载失败，请点「刷新」重试。'));
        });
        loadPendingMemories().catch(function (e) {
            panelState('pendingMemoryBody', 'pendingMemoryEmpty', 7, (e && e.message) || '待确认记忆加载失败，请点「刷新」重试。');
        });
    }

    function onFilterChange() {
        pos = -1;
        current = null;
        renderProgress();
        loadSamples().catch(showErr);
    }

    // ---------------- 当前样本（缩略图 + 只读信息卡） ----------------
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
        renderSampleList();
        renderInfoCard(samples[pos]);
        var box = document.getElementById('imgBox');
        box.innerHTML = '<div class="loading-note">加载图片中…</div>';
        api('/api/evalset/sample/' + encodeURIComponent(sid)).then(function (data) {
            current = data.data;
            renderImage();
            renderInfoCard(current);
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

    // 只读样本信息卡：样本号/形态/候选来源模型/gt_status（来自 samples 接口，
    // 无任何可编辑控件；编辑请进工作台）。
    function renderInfoCard(src) {
        src = src || {};
        function set(id, v) {
            document.getElementById(id).textContent =
                (v === undefined || v === null || v === '') ? '-' : v;
        }
        set('infoSampleId', src.sample_id);
        set('infoDocForm', docFormLabel(src.doc_form));
        set('infoSourceModel', src.gt_source_model || '无');
        set('infoGtStatus', statusLabel(src.gt_status));
        var badge = document.getElementById('gtStatusBadge');
        var st = src.gt_status || 'missing';
        badge.textContent = st === 'confirmed' ? '已确认' :
            (st === 'draft' ? '待抽检（draft）' : '缺 GT');
        badge.className = st === 'confirmed' ? 'status-ok' :
            (st === 'draft' ? 'status-draft' : 'status-missing');
    }

    // ---------------- 批量原样确认 ----------------
    function isBlankTotal(v) {
        return v === null || v === undefined || (typeof v === 'string' && v.trim() === '');
    }

    // GT v2 字段（对齐 ReceiptData 契约）：批量原样确认从服务端 AI 候选原样透传
    // （付款标记/币种/费用/注记等），完整人工编辑只在工作台 /evalset/workbench/<sid>。
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

    // 批量原样确认：对本批 draft 样本逐张取回服务端 AI 候选原样提交（不经过本页任何表单）。
    // 风险显式化：不做逐张人工核对（一键操作），因此弹窗必须列出样本 ID 清单与总数，
    // 并明确警告「未经人工逐张核对，将直接参与出分」；人工校正请逐张进工作台。
    function batchConfirmUntouched() {
        if (document.getElementById('statusSelect').value !== 'draft') {
            toast('批量确认只对「待抽检（draft）」视图可用', true);
            return;
        }
        var draftCount = samples.length;
        if (!draftCount) { toast('本批没有待抽检样本', true); return; }
        var ids = samples.map(function (s) { return s.sample_id; });
        var idList = ids.join('、');
        ask({
            title: '批量确认待抽检样本',
            message: '即将原样确认本批 ' + ids.length + ' 张待抽检样本：' + idList + '。\n这些标准答案没有人工逐张核对过，确认后会直接参与评分。需要人工校正（含付款标记）的，请逐张进工作台核对。',
            details: ids.length > 12 ? ids.slice(0, 12).concat(['……一共 ' + ids.length + ' 张']) : ids,
            confirmText: '确定继续',
            danger: true
        }).then(function (okBatch) { if (okBatch) runBatch(ids); });
    }

    function runBatch(ids) {
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
    // 晋升仅把「原图 + AI 候选 GT」带进评测集（draft），仍需进工作台逐张核对确认。
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
        ask({
            title: '加入评测集',
            message: '把候选 #' + id + ' 加入「' + split + '」评测集？\n会复制原图和 AI 预填的标准答案（还没人工核对），加入后请到工作台逐张核对确认。',
            confirmText: '加入'
        }).then(function (ok) { if (ok) doPromote(id, split); });
    }

    function doPromote(id, split) {
        api('/api/evalset/candidates/' + id + '/promote?split=' + encodeURIComponent(split), {
            method: 'POST'
        }).then(function (data) {
            toast('已晋升为评测样本 ' + (data.data && data.data.sample_id || '') +
                '（' + split + ' 集，GT 为 AI 候选 draft，请进工作台抽检确认）');
            loadCandidates().catch(function () {});
            loadStats().catch(function () {});
        }).catch(showErr);
    }

    function rejectCandidate(id) {
        ask({ title: '不收录这个候选', message: '不收录候选 #' + id + '？这张样本不会进入评测集。', confirmText: '不收录', danger: true })
            .then(function (ok) { if (ok) doReject(id); });
    }

    function doReject(id) {
        api('/api/evalset/candidates/' + id + '/reject', { method: 'POST' })
            .then(function () {
                toast('已驳回候选 #' + id);
                loadCandidates().catch(function () {});
            })
            .catch(showErr);
    }

    // ---------------- 待确认记忆（T8 Gap D1 人工闸）----------------
    // 连续纠偏蒸馏产物先进队列，人工批准后才注入识别；未批准不参与检索。

    // W-10 字段级 diff：把候选的结构化载荷逐字段渲染出来（kind / key / 既有值 / 候选值），
    // 而不是只摊一整段 content 让审核者对整体点头/摇头。旧值侧（existing_fact_value）由
    // 只读端点复用 db.list_vendor_memory 投射（同 vendor+fact_key 的当前 active 值）；
    // 取不到时显示「新增」，不渲染成空串（"没有旧值"不等于"旧值为空"）。
    // 遗留的纯文本候选（无任何结构化字段）退回一行中性说明，approve/reject 流程不变。
    function pendingFactDiffHtml(m) {
        m = m || {};
        function has(v) {
            return !(v === null || v === undefined || String(v).trim() === '');
        }
        if (!has(m.fact_kind) && !has(m.fact_key) && !has(m.fact_value)) {
            return '<span class="kbd-hint">纯文本候选（无结构化字段）</span>';
        }
        var existing = m.existing_fact_value;
        var html = '<div class="fact-diff">' +
            '<div class="fd-line"><span class="fd-label">kind</span>' +
            '<span class="fd-kind">' + esc(m.fact_kind || '-') + '</span></div>' +
            '<div class="fd-line"><span class="fd-label">key</span>' +
            '<span class="fd-key">' + esc(m.fact_key || '-') + '</span></div>';
        if (has(existing)) {
            html += '<div class="fd-line"><span class="fd-label">既有值</span>' +
                '<span class="fd-old">' + esc(existing) + '</span></div>';
        } else {
            html += '<div class="fd-line"><span class="fd-label">既有值</span>' +
                '<span class="fd-new">（新增，无既有值）</span></div>';
        }
        html += '<div class="fd-line"><span class="fd-label">候选值</span>' +
            '<span class="fd-new">' + esc(m.fact_value || '-') + '</span></div></div>';
        return html;
    }

    function loadPendingMemories() {
        return api('/api/memory/pending?status=pending').then(function (data) {
            var list = (data && data.data) || [];
            var body = document.getElementById('pendingMemoryBody');
            var empty = document.getElementById('pendingMemoryEmpty');
            if (!body) { return; }
            body.innerHTML = '';
            if (!list.length) {
                empty.textContent = '暂无待确认记忆。同供应商连续 3 次点踩纠偏会自动出现在这里，等待人工批准。';
                empty.className = 'loading-note';
                return;
            }
            empty.className = 'loading-note hide';
            list.forEach(function (m) {
                var refs = '[]';
                try { refs = JSON.parse(m.source_receipt_ids_json || '[]'); } catch (e) { refs = []; }
                var tr = document.createElement('tr');
                tr.innerHTML =
                    '<td>#' + m.id + '</td>' +
                    '<td>' + esc(m.vendor || '') + '</td>' +
                    '<td style="max-width:420px; white-space:pre-wrap;">' + esc(m.content || '') + '</td>' +
                    '<td class="fact-diff-td">' + pendingFactDiffHtml(m) + '</td>' +
                    '<td>' + esc(refs.map(function (r) { return '#' + r; }).join(' ') || '-') + '</td>' +
                    '<td>' + esc(m.status) + '</td>' +
                    '<td><div class="reflow-actions">' +
                    '<button class="btn-success">批准并生效</button>' +
                    '<button class="btn-danger">拒绝</button>' +
                    '</div></td>';
                var btns = tr.querySelectorAll('button');
                btns[0].onclick = function () { reviewPendingMemory(m.id, 'approve'); };
                btns[1].onclick = function () { reviewPendingMemory(m.id, 'reject'); };
                body.appendChild(tr);
            });
        });
    }

    function reviewPendingMemory(id, action) {
        var verb = action === 'approve' ? '批准记忆 #' + id + ' 并注入后续识别？' :
            '拒绝记忆 #' + id + '？拒绝后不会写入任何记忆库。';
        ask({ title: action === 'approve' ? '批准这条记忆' : '拒绝这条记忆', message: verb, confirmText: action === 'approve' ? '批准' : '拒绝', danger: action !== 'approve' })
            .then(function (ok) { if (ok) doReviewMemory(id, action); });
    }

    function doReviewMemory(id, action) {
        api('/api/memory/pending/' + id + '/' + action, { method: 'POST' })
            .then(function () {
                toast(action === 'approve' ? '已批准记忆 #' + id + '（已写入供应商记忆）' :
                    '已拒绝记忆 #' + id);
                loadPendingMemories().catch(function () {});
            })
            .catch(showErr);
    }

    // ---------------- 空态 / 错误 ----------------
    // 显式翻页入口（按钮用）：pos 是 IIFE 内部变量，inline onclick 拿不到，
    // 必须通过这里的包装函数转发。
    function nextSample() { gotoSample(pos + 1); }
    function prevSample() { gotoSample(pos - 1); }

    // 跳转工作台：在店员日常使用的同一套 Side-by-Side 复核界面中完成全部校正
    // （唯一编辑表面，控制变量；本队列页只保留导航/进度/过滤/批量/回流候选）。
    function openWorkbenchFor(sid) {
        if (!sid) { toast('没有样本 ID，无法进入工作台', true); return; }
        window.location.href = '/evalset/workbench/' + encodeURIComponent(sid);
    }

    function openWorkbench() {
        if (!current || !current.sample_id) { toast('当前没有样本，无法进入工作台', true); return; }
        openWorkbenchFor(current.sample_id);
    }

    function showEmpty(msg) {
        var note = document.getElementById('emptyNote');
        note.textContent = msg;
        note.className = 'loading-note';
        document.getElementById('infoArea').style.display = 'none';
        document.getElementById('imgBox').innerHTML = '<div class="loading-note">无样本</div>';
        document.getElementById('gtStatusBadge').textContent = '';
    }

    function hideEmpty() {
        document.getElementById('emptyNote').className = 'loading-note hide';
        document.getElementById('infoArea').style.display = '';
    }

    function showErr(e) {
        toast(e && e.message ? e.message : '未知错误', true);
    }

    // ---------------- 快捷键 ----------------
    // 只有 J/K 导航；确认动作统一在工作台（队列页无表单，无 A 键确认）。
    document.addEventListener('keydown', function (e) {
        var tag = (e.target.tagName || '').toLowerCase();
        if (tag === 'input' || tag === 'textarea' || tag === 'select') { return; }
        if (e.metaKey || e.ctrlKey || e.altKey) { return; }
        if (e.key === 'j' || e.key === 'J') { gotoSample(pos + 1); }
        else if (e.key === 'k' || e.key === 'K') { gotoSample(pos - 1); }
    });

    // ---------------- 启动 ----------------
    document.getElementById('roleNote').textContent = '当前角色：' + ({ admin: '系统管理员', owner: '餐厅老板', staff: '餐厅店员' }[role()] || role());
    if (role() !== 'admin') {
        document.body.innerHTML = '<div style="max-width:560px; margin:80px auto; padding:24px; background:#fff; border-radius:16px; font-family:inherit; line-height:1.8;" role="alert">'
            + '<h1 style="font-size:1.3rem; margin:0 0 12px;">无权限</h1>'
            + '<p style="margin:0 0 16px;">这个页面只有系统管理员能用。你现在的角色没有权限，工作台不会打开。如果需要使用，请回到主界面，在左侧栏最上方的「当前角色」下拉里选「系统管理员」，再回来刷新本页。</p>'
            + '<a href="/" style="display:inline-block; min-height:44px; line-height:44px; padding:0 22px; background:#032425; color:#fff; border-radius:10px; text-decoration:none; font-weight:600;">回到主界面</a>'
            + '</div>';
        return;
    }
    reloadAll();

    // 暴露给 inline onclick
    window.onFilterChange = onFilterChange;
    window.reloadAll = reloadAll;
    window.gotoSample = gotoSample;
    window.nextSample = nextSample;
    window.prevSample = prevSample;
    window.openWorkbench = openWorkbench;
    window.batchConfirmUntouched = batchConfirmUntouched;
    window.promoteCandidate = promoteCandidate;
    window.rejectCandidate = rejectCandidate;
    // W-10 字段级 diff：暴露给 node vm 脚手架直接断言渲染产物（本页无其它导出面）。
    window.pendingFactDiffHtml = pendingFactDiffHtml;
    window.loadPendingMemories = loadPendingMemories;
})();
