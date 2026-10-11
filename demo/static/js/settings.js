// -*- coding: utf-8 -*-
// 设置页（owner / admin）：单位、单位换算、汇率。数据全部按商户（租户）存储在后端。
// 接口：/api/settings/units、/api/settings/unit_conversions、/api/settings/unit_conversions/check、/api/settings/fx_rates
// 导航：NAV_ITEMS 是侧栏与手机底部导航共用的入口登记点，设置入口在这里登记，不要在别处再写一份。
(function (root) {
    'use strict';

    // ---- 导航登记点（侧栏与手机“更多”共用）----
    root.NAV_ITEMS = root.NAV_ITEMS || [];
    root.registerNavItem = root.registerNavItem || function (item) {
        for (var i = 0; i < root.NAV_ITEMS.length; i++) {
            if (root.NAV_ITEMS[i].target === item.target) { root.NAV_ITEMS[i] = item; return; }
        }
        root.NAV_ITEMS.push(item);
    };
    root.registerNavItem({ target: 'tab-settings', label: '设置', roles: ['owner', 'admin'], group: 'more', order: 90 });

    var KIND_LABEL = { weight: '重量', volume: '容量', count: '数量', other: '其他' };
    var state = { units: [], conversions: [], loaded: false, loading: false, error: '' };
    var fxState = { rates: null, base: 'HKD', loading: false, error: '' };
    var editing = null; // 正在修改的换算 {id, builtin}

    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }
    function role() { try { return localStorage.getItem('demo_role') || 'owner'; } catch (e) { return 'owner'; } }
    function $(id) { return document.getElementById(id); }
    function toast(msg, type) { if (typeof root.showToast === 'function') root.showToast(msg, type || 'info'); }
    function num(v) {
        if (!isFinite(v)) return '';
        var s = Number(v).toPrecision(6);
        if (s.indexOf('e') !== -1) return String(Number(v));
        return String(parseFloat(s));
    }

    async function api(url, opts) {
        var res, body = null;
        try {
            res = await root.apiFetch(url, opts);
        } catch (e) {
            throw new Error('网络好像断了，请检查网络后再试。');
        }
        try { body = await res.json(); } catch (e) { body = null; }
        if (!res.ok) {
            var m = body && (body.msg || body.message || (typeof body.detail === 'string' ? body.detail : (body.detail && body.detail.msg)));
            var err = new Error(m || (res.status === 403 ? '只有老板可以改这里的设置。' : '操作没有成功，请稍后再试。'));
            err.status = res.status;
            err.body = body;
            throw err;
        }
        return body;
    }

    // ---- 单位数据：设置页与 SKU 弹窗共用 ----
    var TenantUnits = {
        names: function () {
            var seen = {}, out = [];
            state.units.forEach(function (u) { if (!seen[u.name]) { seen[u.name] = 1; out.push(u.name); } });
            return out;
        },
        load: async function (force) {
            if (state.loaded && !force) return state.units;
            if (role() === 'staff') return [];
            var body = await api('/api/settings/units');
            var d = (body && body.data) || {};
            state.units = d.units || [];
            state.conversions = d.conversions || [];
            state.loaded = true;
            return state.units;
        },
        hasUnit: function (name) {
            name = String(name || '').trim();
            return state.units.some(function (u) { return u.name === name; });
        }
    };
    root.TenantUnits = TenantUnits;

    function unitOptionsHtml() {
        return TenantUnits.names().map(function (n) { return '<option value="' + esc(n) + '"></option>'; }).join('');
    }

    // ---- 渲染 ----
    function shell() {
        var host = $('tab-settings');
        if (!host) return null;
        if (!host.dataset.ready) {
            host.dataset.ready = '1';
            host.innerHTML = ''
                + '<div class="st-wrap">'
                + '<div class="st-head"><h2>设置</h2><p class="st-sub">这里的设置只对本店生效。改了以后，新入账的单据马上按新设置计算。</p></div>'
                + '<div id="stGate" class="st-note hide"></div>'
                + '<div id="stBody">'
                + '<section class="card st-card" aria-labelledby="stUnitsTitle"><h3 id="stUnitsTitle">单位</h3>'
                + '<p class="st-sub">食材记账用的单位，例如 斤、箱、瓶。系统自带的单位不能删除，自己加的可以删除。</p>'
                + '<div id="stUnitsList" aria-live="polite"></div>'
                + '<form id="stUnitForm" class="st-form" novalidate>'
                + '<div class="st-field"><label for="stUnitName">新单位名称</label><input id="stUnitName" class="form-control" maxlength="12" autocomplete="off" placeholder="例如：打、包、桶"></div>'
                + '<div class="st-field"><label for="stUnitKind">这个单位量的是什么</label><select id="stUnitKind" class="form-control"><option value="count">数量（个、包、箱）</option><option value="weight">重量（斤、克）</option><option value="volume">容量（升、毫升）</option><option value="other">其他</option></select></div>'
                + '<button type="submit" class="btn btn-primary st-btn" id="stUnitAdd">添加单位</button>'
                + '</form><div id="stUnitMsg" class="st-msg" role="alert"></div></section>'
                + '<section class="card st-card" aria-labelledby="stConvTitle"><h3 id="stConvTitle">单位换算</h3>'
                + '<p class="st-sub">告诉系统不同单位之间怎么换，例如「1 箱 = 12 瓶」。进货单写「箱」、库存按「瓶」记的时候，系统会自动换算。</p>'
                + '<div id="stConvList" aria-live="polite"></div>'
                + '<form id="stConvForm" class="st-form st-conv-form" novalidate>'
                + '<div id="stConvFormTitle" class="st-form-title">添加一条换算</div>'
                + '<div class="st-sentence">'
                + '<span class="st-word">1</span>'
                + '<div class="st-field"><label for="stConvFrom">单位</label><input id="stConvFrom" class="form-control" list="stUnitDatalist" autocomplete="off" placeholder="例如 箱"></div>'
                + '<span class="st-word">=</span>'
                + '<div class="st-field"><label for="stConvFactor">数量</label><input id="stConvFactor" class="form-control" inputmode="decimal" autocomplete="off" placeholder="例如 12"></div>'
                + '<div class="st-field"><label for="stConvTo">换成的单位</label><input id="stConvTo" class="form-control" list="stUnitDatalist" autocomplete="off" placeholder="例如 瓶"></div>'
                + '</div>'
                + '<datalist id="stUnitDatalist"></datalist>'
                + '<div class="st-field st-note-field"><label for="stConvNote">备注（可不填）</label><input id="stConvNote" class="form-control" maxlength="60" autocomplete="off" placeholder="例如：每箱 12 瓶装"></div>'
                + '<div id="stConvPreview" class="st-preview" aria-live="polite"></div>'
                + '<div class="st-actions"><button type="submit" class="btn btn-primary st-btn" id="stConvSave">保存换算</button>'
                + '<button type="button" class="btn btn-secondary st-btn hide" id="stConvCancel">取消修改</button></div>'
                + '</form><div id="stConvMsg" class="st-msg" role="alert"></div></section>'
                + '<section class="card st-card" aria-labelledby="stFxTitle"><h3 id="stFxTitle">汇率</h3>'
                + '<p class="st-sub">外币单据换成港币时用。填「1 个外币 = 多少港币」，例如 1 美元 = 7.8 港币。</p>'
                + '<div id="stFxBody" aria-live="polite"></div></section>'
                + '</div></div>';
            bind();
        }
        return host;
    }

    function stateBox(kind, text, retryId) {
        return '<div class="st-state st-state-' + kind + '">' + esc(text)
            + (retryId ? ' <button type="button" class="btn btn-secondary st-btn" id="' + retryId + '">重新加载</button>' : '') + '</div>';
    }

    function renderUnits() {
        var el = $('stUnitsList');
        if (!el) return;
        if (state.loading && !state.loaded) { el.innerHTML = stateBox('loading', '正在加载单位……'); return; }
        if (state.error) { el.innerHTML = stateBox('error', state.error, 'stRetryUnits'); var r = $('stRetryUnits'); if (r) r.onclick = reload; return; }
        if (!state.units.length) { el.innerHTML = stateBox('empty', '还没有任何单位。请在下面添加第一个单位。'); return; }
        var rows = state.units.map(function (u) {
            var del = u.builtin
                ? '<span class="st-tag">系统自带</span>'
                : '<button type="button" class="btn btn-secondary st-btn st-del-unit" data-id="' + esc(u.id) + '" data-name="' + esc(u.name) + '" aria-label="删除单位 ' + esc(u.name) + '">删除</button>';
            return '<tr><td data-i18n-skip>' + esc(u.name) + '</td><td>' + esc(KIND_LABEL[u.kind] || '其他') + '</td><td>' + del + '</td></tr>';
        }).join('');
        el.innerHTML = '<div class="table-container st-table-wrap"><table class="data-table st-table"><thead><tr><th>单位</th><th>量的是</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
        el.querySelectorAll('.st-del-unit').forEach(function (b) { b.onclick = function () { deleteUnit(b.dataset.id, b.dataset.name); }; });
    }

    function renderConversions() {
        var el = $('stConvList');
        if (!el) return;
        var dl = $('stUnitDatalist');
        if (dl) dl.innerHTML = unitOptionsHtml();
        if (state.loading && !state.loaded) { el.innerHTML = stateBox('loading', '正在加载换算……'); return; }
        if (state.error) { el.innerHTML = stateBox('error', state.error, 'stRetryConv'); var r = $('stRetryConv'); if (r) r.onclick = reload; return; }
        if (!state.conversions.length) { el.innerHTML = stateBox('empty', '还没有换算。例如添加「1 箱 = 12 瓶」。'); return; }
        var rows = state.conversions.map(function (c) {
            var f = Number(c.factor);
            var rev = f > 0 ? num(1 / f) : '';
            var tag = c.builtin ? '<span class="st-tag">系统默认，可修改</span>' : '<span class="st-tag st-tag-own">本店设置</span>';
            var delBtn = c.builtin ? '' : '<button type="button" class="btn btn-secondary st-btn st-del-conv" data-id="' + esc(c.id) + '" data-text="1 ' + esc(c.from_unit) + ' = ' + esc(num(f)) + ' ' + esc(c.to_unit) + '">删除</button>';
            return '<tr><td data-i18n-skip><strong>1 ' + esc(c.from_unit) + ' = ' + esc(num(f)) + ' ' + esc(c.to_unit) + '</strong>'
                + '<div class="st-rev">反过来：1 ' + esc(c.to_unit) + ' = ' + esc(rev) + ' ' + esc(c.from_unit) + '</div></td>'
                + '<td>' + tag + (c.note ? '<div class="st-rev" data-i18n-skip>' + esc(c.note) + '</div>' : '') + '</td>'
                + '<td class="st-ops"><button type="button" class="btn btn-secondary st-btn st-edit-conv" data-id="' + esc(c.id) + '">修改</button>' + delBtn + '</td></tr>';
        }).join('');
        el.innerHTML = '<div class="table-container st-table-wrap"><table class="data-table st-table"><thead><tr><th>换算</th><th>来源</th><th>操作</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
        el.querySelectorAll('.st-edit-conv').forEach(function (b) { b.onclick = function () { startEdit(b.dataset.id); }; });
        el.querySelectorAll('.st-del-conv').forEach(function (b) { b.onclick = function () { deleteConv(b.dataset.id, b.dataset.text); }; });
    }

    // ---- 加载 ----
    async function reload() {
        state.loading = true; state.error = '';
        renderUnits(); renderConversions();
        try {
            await TenantUnits.load(true);
        } catch (e) {
            state.error = e.message || '单位设置加载失败，请稍后重试。';
        }
        state.loading = false;
        renderUnits(); renderConversions();
        loadFx();
    }

    // ---- 单位 ----
    async function addUnit(ev) {
        ev.preventDefault();
        var name = $('stUnitName').value.trim();
        var msg = $('stUnitMsg');
        msg.textContent = '';
        if (!name) { msg.textContent = '请先填写单位名称，例如「打」。'; $('stUnitName').focus(); return; }
        if (name.length > 12) { msg.textContent = '单位名称太长了，请控制在 12 个字以内。'; return; }
        var btn = $('stUnitAdd'); btn.disabled = true;
        try {
            await api('/api/settings/units', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: name, kind: $('stUnitKind').value }) });
            $('stUnitName').value = '';
            toast('已添加单位「' + name + '」。', 'success');
            await TenantUnits.load(true);
            renderUnits(); renderConversions();
        } catch (e) {
            msg.textContent = e.status === 409 ? (e.message || '这个单位已经有了，不用再加。') : e.message;
        }
        btn.disabled = false;
    }

    async function deleteUnit(id, name) {
        var ok = await root.uiConfirm({ title: '删除单位', message: '确定删除单位「' + name + '」吗？删除后下拉里不再出现这个单位。', confirmText: '删除', danger: true });
        if (!ok) return;
        try {
            await api('/api/settings/units/' + encodeURIComponent(id), { method: 'DELETE' });
            toast('已删除单位「' + name + '」。', 'success');
            await TenantUnits.load(true);
            renderUnits(); renderConversions();
        } catch (e) {
            await root.uiAlert({ title: '这个单位现在不能删', message: e.message });
        }
    }

    // ---- 换算 ----
    function readConvForm() {
        return {
            from: $('stConvFrom').value.trim(),
            to: $('stConvTo').value.trim(),
            factorRaw: $('stConvFactor').value.trim().replace(/，/g, '.'),
            note: $('stConvNote').value.trim()
        };
    }
    function validateConv(v) {
        if (!v.from) return { field: 'stConvFrom', msg: '请先填写左边的单位，例如「箱」。' };
        if (!v.to) return { field: 'stConvTo', msg: '请先填写右边的单位，例如「瓶」。' };
        if (v.from === v.to) return { field: 'stConvTo', msg: '左右两个单位是同一个，换算没有意义。请改成不同的单位。' };
        if (!v.factorRaw) return { field: 'stConvFactor', msg: '请填写数量，例如 12。' };
        var f = Number(v.factorRaw);
        if (!isFinite(f)) return { field: 'stConvFactor', msg: '数量只能填数字，例如 12 或 0.6048。' };
        if (f <= 0) return { field: 'stConvFactor', msg: '数量要大于 0。' };
        if (f > 1e9) return { field: 'stConvFactor', msg: '数量太大了，请检查有没有多填了零。' };
        return null;
    }

    var previewTimer = null, previewSeq = 0;
    function schedulePreview() {
        clearTimeout(previewTimer);
        previewTimer = setTimeout(updatePreview, 250);
    }
    async function updatePreview() {
        var box = $('stConvPreview');
        if (!box) return;
        var v = readConvForm();
        var f = Number(v.factorRaw);
        var lines = [];
        if (v.from && v.to && v.from !== v.to && v.factorRaw && isFinite(f) && f > 0) {
            lines.push('保存后：1 ' + v.from + ' = ' + num(f) + ' ' + v.to + '，反过来 1 ' + v.to + ' = ' + num(1 / f) + ' ' + v.from + '。');
        }
        if (v.from && v.to && v.from !== v.to) {
            var seq = ++previewSeq;
            try {
                var body = await api('/api/settings/unit_conversions/check?from_unit=' + encodeURIComponent(v.from) + '&to_unit=' + encodeURIComponent(v.to));
                if (seq !== previewSeq) return;
                var d = (body && body.data) || body || {};
                if (d.convertible && d.factor != null) {
                    lines.push('按现在的设置：10 ' + v.from + ' = ' + num(10 * Number(d.factor)) + ' ' + v.to + '。');
                } else {
                    lines.push('现在「' + v.from + '」和「' + v.to + '」之间还没有换算关系。');
                }
            } catch (e) { /* 预览失败不打扰填写 */ }
        }
        box.innerHTML = lines.map(function (l) { return '<div data-i18n-skip>' + esc(l) + '</div>'; }).join('');
    }

    function resetConvForm() {
        editing = null;
        $('stConvFrom').value = ''; $('stConvTo').value = ''; $('stConvFactor').value = ''; $('stConvNote').value = '';
        $('stConvFrom').readOnly = false; $('stConvTo').readOnly = false;
        $('stConvFormTitle').textContent = '添加一条换算';
        $('stConvCancel').classList.add('hide');
        $('stConvPreview').innerHTML = '';
    }

    function startEdit(id) {
        var c = state.conversions.filter(function (x) { return String(x.id) === String(id); })[0];
        if (!c) return;
        editing = { id: c.id, builtin: !!c.builtin };
        $('stConvFrom').value = c.from_unit; $('stConvTo').value = c.to_unit;
        $('stConvFactor').value = num(c.factor); $('stConvNote').value = c.note || '';
        $('stConvFrom').readOnly = true; $('stConvTo').readOnly = true;
        $('stConvFormTitle').textContent = c.builtin ? '修改系统默认换算（保存后以本店设置为准）' : '修改这条换算';
        $('stConvCancel').classList.remove('hide');
        $('stConvMsg').textContent = '';
        $('stConvFactor').focus();
        $('stConvForm').scrollIntoView({ block: 'center' });
        updatePreview();
    }

    async function saveConv(ev) {
        ev.preventDefault();
        var msg = $('stConvMsg'); msg.textContent = '';
        var v = readConvForm();
        var bad = validateConv(v);
        if (bad) { msg.textContent = bad.msg; $(bad.field).focus(); return; }
        var payload = { from_unit: v.from, to_unit: v.to, factor: Number(v.factorRaw), note: v.note };
        var btn = $('stConvSave'); btn.disabled = true;
        try {
            if (editing && !editing.builtin) {
                await api('/api/settings/unit_conversions/' + encodeURIComponent(editing.id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            } else {
                await api('/api/settings/unit_conversions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            }
            toast('已保存：1 ' + v.from + ' = ' + num(payload.factor) + ' ' + v.to + '。', 'success');
            resetConvForm();
            await TenantUnits.load(true);
            renderUnits(); renderConversions();
        } catch (e) {
            msg.textContent = e.message;
        }
        btn.disabled = false;
    }

    async function deleteConv(id, text) {
        var ok = await root.uiConfirm({ title: '删除换算', message: '确定删除「' + text + '」吗？\n删除后，用到这条换算的单据入账时会提示补充换算。', confirmText: '删除', danger: true });
        if (!ok) return;
        try {
            await api('/api/settings/unit_conversions/' + encodeURIComponent(id), { method: 'DELETE' });
            toast('已删除换算。', 'success');
            if (editing && String(editing.id) === String(id)) resetConvForm();
            await TenantUnits.load(true);
            renderUnits(); renderConversions();
        } catch (e) {
            await root.uiAlert({ title: '没有删掉', message: e.message });
        }
    }

    // ---- 汇率 ----
    function normalizeRates(d) {
        var r = (d && d.rates) || [];
        var out = [];
        r.forEach(function (x) {
            if (!x || !x.currency) return;
            var code = String(x.currency).toUpperCase();
            if (code === 'HKD') return;
            out.push({ code: code, rate: Number(x.rate), confirmed: !!x.confirmed, seed: !!x.seed, note: x.note || '' });
        });
        return out;
    }

    async function loadFx() {
        var box = $('stFxBody');
        if (!box) return;
        fxState.loading = true; fxState.error = '';
        box.innerHTML = stateBox('loading', '正在加载汇率……');
        try {
            var body = await api('/api/settings/fx_rates');
            fxState.rates = normalizeRates(body && body.data);
        } catch (e) {
            fxState.error = e.message;
        }
        fxState.loading = false;
        renderFx();
    }

    function renderFx() {
        var box = $('stFxBody');
        if (!box) return;
        if (fxState.error) { box.innerHTML = stateBox('error', fxState.error, 'stRetryFx'); $('stRetryFx').onclick = loadFx; return; }
        var rates = fxState.rates || [];
        var rows = rates.map(function (r, i) {
            var tag = (r.seed && !r.confirmed) ? '<span class="st-tag st-tag-warn">参考值，需老板确认</span>' : '<span class="st-tag st-tag-own">已确认</span>';
            return '<div class="st-fx-row"><label for="stFx' + i + '" data-i18n-skip>1 ' + esc(r.code) + ' =</label>'
                + '<input id="stFx' + i + '" class="form-control st-fx-in" data-code="' + esc(r.code) + '" inputmode="decimal" value="' + esc(num(r.rate)) + '" autocomplete="off"><span class="st-word">港币</span>' + tag + '</div>';
        }).join('');
        box.innerHTML = (rows || stateBox('empty', '还没有设置外币汇率。需要时在下面添加，例如 USD（美元）。'))
            + '<div class="st-fx-row st-fx-add"><label for="stFxNewCode">加一个币种（3 个字母）</label><input id="stFxNewCode" class="form-control st-fx-code" maxlength="3" placeholder="USD" autocomplete="off">'
            + '<label for="stFxNewRate">1 个外币 = 多少港币</label><input id="stFxNewRate" class="form-control st-fx-in" inputmode="decimal" placeholder="7.8" autocomplete="off"></div>'
            + '<div class="st-actions"><button type="button" class="btn btn-primary st-btn" id="stFxSave">保存汇率</button></div><div id="stFxMsg" class="st-msg" role="alert"></div>';
        $('stFxSave').onclick = saveFx;
    }

    async function saveFx() {
        var msg = $('stFxMsg'); msg.textContent = '';
        var list = [];
        var bad = '';
        $('stFxBody').querySelectorAll('.st-fx-in[data-code]').forEach(function (inp) {
            var raw = String(inp.value).trim();
            var n = Number(raw);
            if (!raw || !isFinite(n) || n <= 0) { bad = bad || ('「' + inp.dataset.code + '」的汇率要填大于 0 的数字。'); inp.focus(); }
            var old = (fxState.rates || []).filter(function (x) { return x.code === inp.dataset.code; })[0];
            // 只提交改过的或已确认的行；没动过的参考值保持「待确认」状态
            if (!old || !old.seed || old.confirmed || Math.abs(old.rate - n) > 1e-12) list.push({ currency: inp.dataset.code, rate: n, confirmed: true, note: (old && old.note) || '' });
        });
        if (bad) { msg.textContent = bad; return; }
        var code = $('stFxNewCode').value.trim().toUpperCase();
        var rateRaw = $('stFxNewRate').value.trim().replace(/，/g, '.');
        if (code || rateRaw) {
            if (!/^[A-Z]{3}$/.test(code)) { msg.textContent = '外币代码是 3 个英文字母，例如 USD。'; $('stFxNewCode').focus(); return; }
            if (code === 'HKD') { msg.textContent = '港币是本位币，不用设置汇率。'; return; }
            var n = Number(rateRaw);
            if (!rateRaw || !isFinite(n) || n <= 0) { msg.textContent = '请填写 ' + code + ' 的汇率，例如 7.8。'; $('stFxNewRate').focus(); return; }
            list.push({ currency: code, rate: n, confirmed: true, note: '' });
        }
        if (!list.length) { msg.textContent = '没有改动，不用保存。要确认某个参考汇率，请先把数字改成实际汇率。'; return; }
        var btn = $('stFxSave'); btn.disabled = true;
        try {
            var body = await api('/api/settings/fx_rates', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rates: list }) });
            fxState.rates = normalizeRates(body && body.data);
            toast('汇率已保存。', 'success');
            renderFx();
        } catch (e) {
            msg.textContent = e.message;
            btn.disabled = false;
        }
    }

    function bind() {
        $('stUnitForm').addEventListener('submit', addUnit);
        $('stConvForm').addEventListener('submit', saveConv);
        $('stConvCancel').addEventListener('click', resetConvForm);
        ['stConvFrom', 'stConvTo', 'stConvFactor'].forEach(function (id) { $(id).addEventListener('input', schedulePreview); });
    }

    // ---- 入口 ----
    function open() {
        var host = shell();
        if (!host) return;
        var gate = $('stGate'), body = $('stBody');
        if (role() === 'staff') {
            gate.textContent = '设置只有老板可以修改。需要改单位或换算，请找老板。';
            gate.classList.remove('hide');
            body.classList.add('hide');
            return;
        }
        gate.classList.add('hide');
        body.classList.remove('hide');
        reload();
    }

    // ---- SKU 弹窗：基础单位下拉来自本租户单位列表 ----
    function skuHintUpdate() {
        var inp = $('skuModalUnit'), hint = $('skuModalUnitHint');
        if (!inp || !hint) return;
        var v = inp.value.trim();
        if (v && state.loaded && !TenantUnits.hasUnit(v)) {
            hint.textContent = '「' + v + '」还不在本店单位里。可以先用它，但进货单用别的单位时，请到「设置」里补上「' + v + '」的换算，否则入账会被拦下。';
            hint.classList.add('is-warn');
        } else {
            hint.textContent = '下拉里是本店「设置」里的单位。要用新单位可以直接输入。';
            hint.classList.remove('is-warn');
        }
    }
    root.refreshSkuUnitChoices = async function () {
        var dl = $('skuUnitList'), inp = $('skuModalUnit');
        if (!dl || !inp) return;
        if (!inp.dataset.hintBound) { inp.dataset.hintBound = '1'; inp.addEventListener('input', skuHintUpdate); }
        try {
            await TenantUnits.load(false);
            var names = TenantUnits.names();
            if (names.length) dl.innerHTML = names.map(function (n) { return '<option value="' + esc(n) + '">'; }).join('');
        } catch (e) { /* 取不到时保留原有下拉，不打扰录入 */ }
        skuHintUpdate();
    };

    // 入账被拦（单位没有换算 / 外币没有汇率）时，引导老板到设置页。返回 true 表示已处理。
    root.handleSettingsBlock = async function (ret) {
        if (!ret || (ret.code !== 'UNIT_CONVERSION_MISSING' && ret.code !== 'FX_RATE_MISSING')) return false;
        var staff = role() === 'staff';
        var go = await root.uiConfirm({
            title: ret.code === 'FX_RATE_MISSING' ? '还没有这个外币的汇率' : '单位还没有换算关系',
            message: (ret.msg || '') + (staff ? '\n这个设置只有老板可以改，请找老板处理。' : ''),
            confirmText: staff ? '知道了' : '去设置',
            cancelText: '先不去'
        });
        if (!go || staff) return true;
        var btn = document.querySelector('.sidebar-btn[data-target="tab-settings"]');
        if (btn) btn.click();
        var pair = ret.data && ret.data.pairs && ret.data.pairs[0];
        if (pair && ret.code === 'UNIT_CONVERSION_MISSING') {
            setTimeout(function () {
                var f = $('stConvFrom'), t = $('stConvTo');
                if (f && t) { f.value = pair.from_unit; t.value = pair.to_unit; f.dispatchEvent(new Event('input')); $('stConvFactor').focus(); }
            }, 600);
        }
        return true;
    };

    root.openSettingsTab = open;
    var prevHook = root.onAppTabActivated;
    root.onAppTabActivated = function (target) {
        if (typeof prevHook === 'function') prevHook(target);
        if (target === 'tab-settings') open();
    };

    root.__settingsTest = { validateConv: validateConv, normalizeRates: normalizeRates, num: num };
})(window);
