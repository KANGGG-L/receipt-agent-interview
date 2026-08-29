// -*- coding: utf-8 -*-
// 评测 GT 核对工作台适配器（合并反馈①②）。
//
// 设计约束：本文件是「适配器」而不是「第二套界面」——复用 index.html 的
// Side-by-Side 复核 DOM 与 main.js 的既有渲染/组装函数，与店员日常识别一张
// 单据后的复核界面完全同源：
//   - 数据来源：本地上传→AI 识别  =>  评测集样本→AI 候选 GT 预填
//     （渲染直接调用 main.js 的 renderEditForm / applySettlementToForm，不重写渲染）
//   - 提交动作：save_edited       =>  /api/evalset/sample/<sid>/confirm
//     （字段组装复用 main.js 的 collectReviewFormData，与 save_edited 同一来源）
// 仅隐藏与单次核对无关的区块（上传区/本批照片抽屉/放弃上传/行反馈），其余不动。
//
// 页面路由：GET /evalset/workbench/<sample_id>（demo/app/main.py 注入
// window.__EVAL_WORKBENCH__.sampleId 与本脚本）。

(function () {
    'use strict';

    var flag = window.__EVAL_WORKBENCH__;
    if (!flag || !flag.sampleId) { return; }
    var sampleId = String(flag.sampleId);
    var split = 'test';          // 样本所属 split（详情接口回填，用于确认后取下一张 draft）
    var candidateGt = null;      // 服务端 AI 候选 GT（v2 全集），保存 UI 未暴露字段的候选值

    function role() {
        try { return localStorage.getItem('demo_role') || 'admin'; } catch (e) { return 'admin'; }
    }

    function headers(extra) {
        var h = { 'X-Role': role() };
        if (extra) { for (var k in extra) { h[k] = extra[k]; } }
        return h;
    }

    function toastMsg(msg, isErr) {
        if (typeof showToast === 'function') {
            showToast(msg, isErr ? 'error' : 'success');
        } else {
            console.log('[eval_workbench] ' + msg);
        }
    }

    // ---------------- 隐藏与单次核对无关的区块 ----------------
    function hideById(id) {
        var el = document.getElementById(id);
        if (el) { el.classList.add('hide'); }
    }

    function adaptDomForEval() {
        // 上传区与批量照片抽屉：与单次核对无关
        hideById('uploadArea');
        hideById('photoSider');
        hideById('btnSiderToggle');
        hideById('preConfirmCard');
        hideById('loadingCard');
        hideById('errorCard');
        // 「放弃上传」会物理删除线上单据，评测场景不存在该单据
        hideById('btnDiscardReceipt');
        // 行点赞/点踩关联线上 receipt_id，评测场景不适用
        hideById('feedbackInline');
        // 显示与真实识别完成后一致的 Side-by-Side 复核区
        var splitView = document.getElementById('splitViewArea');
        if (splitView) { splitView.classList.remove('hide'); }
        var formCard = document.getElementById('prefillFormCard');
        if (formCard) { formCard.classList.remove('hide'); }
        // 表单标题：说明当前是评测核对（数据来源是评测集样本 + AI 候选 GT）
        var title = document.getElementById('manualEntryFormTitle');
        if (title) {
            title.textContent = '评测 GT 核对（' + sampleId + '）：对照左侧原图校正 AI 候选，与店员复核界面同源';
        }
    }

    // ---------------- 候选 GT → 复核表单数据（与识别结果 ret.data 同构） ----------------
    function gtToFormData(gt) {
        gt = gt || {};
        return {
            supplier_name: gt.supplier_name || '',
            date: gt.date || '',
            total_amount: gt.total_amount,
            doc_form: gt.doc_form || 'printed_delivery_note',
            currency: gt.currency || 'HKD',
            discount_amount: gt.discount_amount || 0,
            delivery_fee: gt.delivery_fee || 0,
            deposit_amount: gt.deposit_amount || 0,
            rounding_adjustment: gt.rounding_adjustment || 0,
            // Gap 9 / Gap 6：UI 已暴露可修正字段，预填进复核界面（与店员界面同源）
            service_fee: gt.service_fee || 0,
            tax_amount: gt.tax_amount || 0,
            adjustment_notes: Array.isArray(gt.adjustment_notes) ? gt.adjustment_notes : [],
            payment_evidence: gt.payment_evidence || '',
            // 付款标记走店员界面既有通道：payment_marked 驱动已付款/未付款切换徽章
            // （applySettlementToForm 灌 state），payment_evidence 自动带出到只读证据展示区
            payment_marked: gt.payment_marked === true,
            payment_mark: gt.payment_marked === true ? (gt.payment_evidence || '已付款') : '',
            // 明细行键名对齐 appendTableRow 读取口径（quantity）
            items: (gt.items || []).map(function (it) {
                return {
                    name: it.name || '',
                    quantity: (it.qty != null) ? it.qty : it.quantity,
                    unit: it.unit || '',
                    unit_price: it.unit_price,
                    amount: it.amount
                };
            })
        };
    }

    // ---------------- 加载样本并灌入复核界面 ----------------
    function loadSample() {
        fetch('/api/evalset/sample/' + encodeURIComponent(sampleId), { headers: headers() })
            .then(function (resp) {
                return resp.json().then(function (data) {
                    if (!resp.ok) {
                        var msg = (data && (data.detail || data.msg)) ||
                            ('样本加载失败（HTTP ' + resp.status + '）');
                        if (resp.status === 403) {
                            msg = '权限不足：评测核对工作台仅限 admin 角色。请回主界面切换角色后刷新。';
                        }
                        throw new Error(msg);
                    }
                    return data;
                });
            })
            .then(function (data) {
                var d = data.data || {};
                split = d.split || 'test';
                candidateGt = d.gt || {};
                // 左侧原图：样本缩略图（与真实复核同一 img 容器）
                var img = document.getElementById('previewImg');
                if (img && d.image_base64) {
                    img.src = 'data:' + (d.image_mime || 'image/jpeg') + ';base64,' + d.image_base64;
                }
                if (d.image_missing) { toastMsg(d.image_missing, true); }
                // 右侧表单：直接调用 main.js 既有渲染函数（与识别完成 applyRecognizedResult 同一入口）
                if (typeof renderEditForm === 'function') {
                    renderEditForm(gtToFormData(candidateGt));
                    hideById('feedbackInline');   // renderEditForm 内部可能重新显示，再压一次
                } else {
                    toastMsg('main.js 复核渲染函数不可用，请刷新页面', true);
                }
            })
            .catch(function (e) {
                toastMsg('评测样本加载失败：' + (e && e.message ? e.message : '未知错误'), true);
            });
    }

    // ---------------- 提交：复用 save_edited 同一组装函数 → confirm ----------------
    function isBlankTotal(v) {
        return v === null || v === undefined || (typeof v === 'string' && String(v).trim() === '');
    }

    function buildGtFromForm() {
        // 与 submitSaveEdited 完全同一字段组装来源（含校验前置的原始值）
        var data = collectReviewFormData();
        var cand = candidateGt || {};

        // 校验口径与店员提交一致（人话报错）；差异点：总额允许留空（月结单），
        // 改走 confirm_blank_total 显式确认；结算方式不在 GT v2 契约内，不做必填拦截。
        var supplier = String(data.supplier_name || '').trim();
        if (!supplier || supplier === '通用供应商') {
            toastMsg('请填写供应商名称', true); return null;
        }
        var dateStr = String(data.date || '').trim();
        if (!dateStr || !/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
            toastMsg('请选择开单日期', true); return null;
        }
        var m = dateStr.match(/^(\d{4})-(\d{2})-(\d{2})$/);
        var y = Number(m[1]), mo = Number(m[2]), dd = Number(m[3]);
        var dt = new Date(y, mo - 1, dd);
        if (dt.getFullYear() !== y || dt.getMonth() !== mo - 1 || dt.getDate() !== dd) {
            toastMsg('开单日期不正确，请选择真实存在的日历日期', true); return null;
        }
        var blankTotal = isBlankTotal(document.getElementById('inpTotal').value);
        if (!blankTotal) {
            var totalAmt = Number(data.total_amount || 0);
            if (isNaN(totalAmt) || totalAmt <= 0) {
                toastMsg('单据总金额必须大于 0（图面确无总额可清空后按月结单留空确认）', true);
                return null;
            }
        }
        var items = (data.items || []).filter(function (it) {
            return String((it && it.name) || '').trim() !== '';
        });
        if (!items.length) {
            toastMsg('请至少输入一行消费明细', true); return null;
        }
        for (var i = 0; i < items.length; i++) {
            var it = items[i];
            var qty = Number(it.quantity);
            var price = Number(it.unit_price);
            if (isNaN(qty) || qty <= 0 || isNaN(price) || price < 0) {
                toastMsg('第 ' + (i + 1) + ' 行的单价或数量不是有效数字，请重新输入', true);
                return null;
            }
        }

        // GT v2 全集：UI 可编辑字段一律取表单实际值（店员/抽检员修正优先生效）。
        // 附加费用走动态费用行（collectReviewFormData 已映射回六契约字段，
        // 删除费用行 = 该项归 0，不再回落候选值）。
        var gt = {
            supplier_name: supplier,
            date: dateStr,
            total_amount: blankTotal ? null : Number(data.total_amount),
            doc_form: data.doc_form || 'printed_delivery_note',
            // 用户反馈：付款标记改点击切换徽章——GT payment_marked 从徽章 state 取
            //（不再读已移除的下拉枚举值）；payment_evidence 取自动带出值（无手输入口），
            // 表单从未渲染时仍回落候选值
            payment_marked: getPaymentMarkBadgeState(),
            payment_evidence: String(data.payment_evidence != null && data.payment_evidence !== ''
                ? data.payment_evidence : (cand.payment_evidence || '')),
            currency: data.currency || 'HKD',
            discount_amount: Number(data.discount_amount) || 0,
            deposit_amount: Number(data.deposit_amount) || 0,
            delivery_fee: Number(data.delivery_fee) || 0,
            service_fee: Number(data.service_fee) || 0,
            tax_amount: Number(data.tax_amount) || 0,
            rounding_adjustment: Number(data.rounding_adjustment) || 0,
            // 手写注记：动态注记行 → 数组。候选注记已由 renderEditForm 灌入行中，
            // 表单实际值优先；仅当表单从未被候选数据渲染过时才回落候选值——
            // 抽检员删光注记行后不得复活候选注记（与费用区「删除行 = 归 0」同款语义）。
            adjustment_notes: (function () {
                var formNotes = Array.isArray(data.adjustment_notes) ? data.adjustment_notes : [];
                if (formNotes.length) return formNotes;
                var notesContainer = document.getElementById('notesRowsContainer');
                var seededFromData = !!(notesContainer
                    && notesContainer.dataset.seededFromData === '1');
                if (seededFromData) return [];
                return Array.isArray(cand.adjustment_notes) ? cand.adjustment_notes : [];
            })(),
            items: items.map(function (it) {
                return {
                    name: String(it.name || '').trim(),
                    qty: Number(it.quantity) || 0,
                    unit: String(it.unit || '').trim(),
                    unit_price: Number(it.unit_price) || 0,
                    amount: Number(it.amount) || 0
                };
            })
        };
        return { gt: gt, blankTotal: blankTotal };
    }

    function goNextDraft() {
        // A 确认跳下一张的流式体验：取同 split 的下一张 draft 直达其工作台
        var qs = 'split=' + encodeURIComponent(split) + '&gt_status=draft';
        fetch('/api/evalset/samples?' + qs, { headers: headers() })
            .then(function (r) { return r.json(); })
            .then(function (data) {
                var list = (data.data && data.data.samples) || [];
                var next = null;
                for (var i = 0; i < list.length; i++) {
                    if (list[i].sample_id !== sampleId) { next = list[i].sample_id; break; }
                }
                if (next) {
                    window.location.href = '/evalset/workbench/' + encodeURIComponent(next) + '?auto_next=1';
                } else {
                    toastMsg('本 split 已无待抽检样本，返回队列', false);
                    setTimeout(function () { window.location.href = '/evalset'; }, 800);
                }
            })
            .catch(function () { window.location.href = '/evalset'; });
    }

    function submitEvalConfirm() {
        var built = buildGtFromForm();
        if (!built) { return; }
        var payload = { gt: built.gt };
        if (built.blankTotal) {
            if (!window.confirm('总额为空。若图面确无总额（如月结单）可留空确认；' +
                    '否则请核实图面金额。确定留空吗？')) {
                return;
            }
            payload.confirm_blank_total = true;
        }
        var btn = document.getElementById('btnSaveReview');
        if (btn) { btn.disabled = true; }
        fetch('/api/evalset/sample/' + encodeURIComponent(sampleId) + '/confirm', {
            method: 'POST',
            headers: headers({ 'Content-Type': 'application/json' }),
            body: JSON.stringify(payload)
        })
            .then(function (resp) {
                return resp.json().catch(function () { return {}; }).then(function (data) {
                    if (!resp.ok) {
                        var msg = (data && (data.detail || data.msg)) ||
                            ('确认失败（HTTP ' + resp.status + '）');
                        throw new Error(msg);
                    }
                    return data;
                });
            })
            .then(function () {
                toastMsg('已确认 ' + sampleId + '（GT v2，' + built.gt.items.length + ' 行明细）', false);
                goNextDraft();
            })
            .catch(function (e) {
                toastMsg('确认失败：' + (e && e.message ? e.message : '未知错误'), true);
                if (btn) { btn.disabled = false; }
            });
    }

    // ---------------- 启动（main.js DOMContentLoaded 初始化之后执行） ----------------
    function enter() {
        adaptDomForEval();
        // 提交按钮改道：复用同一按钮（店员点「确认上传单据」的同一位），仅换请求终点
        var btn = document.getElementById('btnSaveReview');
        if (btn) {
            btn.textContent = ' 确认本张 GT（进入下一张）';
            btn.onclick = submitEvalConfirm;   // 覆盖 inline onclick 的 save_edited 路径
        }
        loadSample();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', enter);
    } else {
        enter();
    }
})();
