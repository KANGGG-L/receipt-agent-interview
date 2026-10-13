// -*- coding: utf-8 -*-
// 全站共用的应用内对话框：替代浏览器原生 confirm / alert / prompt。
// 用法（均返回 Promise，调用方用 await）：
//   await uiConfirm({title, message, details:[...], confirmText, cancelText, danger:true})  -> true/false
//   await uiAlert({title, message, okText})                                                 -> undefined
//   await uiPrompt({title, label, value, placeholder, confirmText, validate(v)->错误文案})  -> 字符串 或 null(取消)
// 键盘：Esc 取消、Enter 确认（输入框内）、Tab 在对话框内循环；打开时焦点落在“取消”（危险操作）或首个输入框，
// 关闭后焦点回到触发元素。message 为纯文本（自动转义），换行用 \n。
(function (root) {
    'use strict';
    if (root.uiConfirm) return;

    var STYLE_ID = 'uiDialogStyle';
    var CSS = ''
        + '.ui-dlg-mask{position:fixed;inset:0;background:rgba(15,23,22,.55);z-index:100000;display:flex;align-items:center;justify-content:center;padding:16px}'
        + '.ui-dlg{background:#fff;color:#1e2223;border-radius:16px;max-width:460px;width:100%;max-height:90vh;overflow:auto;box-shadow:0 20px 50px rgba(0,0,0,.25);padding:20px 22px;font-size:15px;line-height:1.6}'
        + '.ui-dlg h3{margin:0 0 8px;font-size:1.1rem;font-weight:700}'
        + '.ui-dlg-msg{white-space:pre-wrap;margin:0 0 6px;color:#1e2223}'
        + '.ui-dlg-list{margin:6px 0 0;padding:10px 12px 10px 28px;background:#f3f2f1;border-radius:10px;font-size:14px}'
        + '.ui-dlg label{display:block;font-weight:600;margin:8px 0 4px}'
        + '.ui-dlg input{width:100%;box-sizing:border-box;min-height:44px;padding:8px 12px;border:1px solid #8a8f8d;border-radius:10px;font-size:16px}'
        + '.ui-dlg-err{color:#b91c1c;font-size:14px;min-height:20px;margin-top:4px}'
        + '.ui-dlg-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:16px;flex-wrap:wrap}'
        + '.ui-dlg-btn{min-height:44px;min-width:96px;padding:8px 18px;border-radius:10px;border:1px solid #8a8f8d;background:#fff;color:#1e2223;font-size:15px;font-weight:600;cursor:pointer}'
        + '.ui-dlg-btn.primary{background:#032425;border-color:#032425;color:#fff}'
        + '.ui-dlg-btn.danger{background:#b91c1c;border-color:#b91c1c;color:#fff}'
        + '.ui-dlg-btn:focus-visible,.ui-dlg input:focus-visible{outline:3px solid #2563eb;outline-offset:2px}'
        + '@media (max-width:640px){.ui-dlg-actions{flex-direction:column-reverse}.ui-dlg-btn{width:100%}}';

    function ensureStyle() {
        // CSP 加固（F4）：样式已外置为 /static/css/ui-dialog.css，由模板以
        // <link id="uiDialogStyle"> 挂载；链接在位时跳过运行时注入——强制 CSP
        // （style-src 'self'）会拦截运行时 <style> 注入并污染 console。
        if (document.getElementById(STYLE_ID)) return;
        var link = document.querySelector && document.querySelector('link[id="' + STYLE_ID + '"]');
        if (link) return;
        var s = document.createElement('style');
        s.id = STYLE_ID;
        s.textContent = CSS;
        document.head.appendChild(s);
    }

    function el(tag, cls, text) {
        var n = document.createElement(tag);
        if (cls) n.className = cls;
        if (text != null) n.textContent = text;
        return n;
    }

    var seq = 0;

    function open(opts, kind) {
        ensureStyle();
        return new Promise(function (resolve) {
            var id = 'uiDlg' + (++seq);
            var prev = document.activeElement;
            var mask = el('div', 'ui-dlg-mask');
            var box = el('div', 'ui-dlg');
            box.setAttribute('role', kind === 'alert' ? 'alertdialog' : 'dialog');
            box.setAttribute('aria-modal', 'true');
            box.setAttribute('aria-labelledby', id + 't');
            var h = el('h3', null, opts.title || (kind === 'alert' ? '提示' : kind === 'prompt' ? '请输入' : '请确认'));
            h.id = id + 't';
            box.appendChild(h);
            if (opts.message) {
                var m = el('p', 'ui-dlg-msg', opts.message);
                m.id = id + 'm';
                box.setAttribute('aria-describedby', m.id);
                box.appendChild(m);
            }
            if (opts.details && opts.details.length) {
                var ul = el('ul', 'ui-dlg-list');
                opts.details.forEach(function (d) { ul.appendChild(el('li', null, String(d))); });
                box.appendChild(ul);
            }
            var input = null, err = null;
            if (kind === 'prompt') {
                var lab = el('label', null, opts.label || '');
                lab.setAttribute('for', id + 'i');
                input = el('input');
                input.id = id + 'i';
                input.type = 'text';
                input.value = opts.value || '';
                if (opts.placeholder) input.placeholder = opts.placeholder;
                err = el('div', 'ui-dlg-err');
                err.setAttribute('role', 'alert');
                box.appendChild(lab);
                box.appendChild(input);
                box.appendChild(err);
            }
            var actions = el('div', 'ui-dlg-actions');
            var cancelBtn = null;
            if (kind !== 'alert') {
                cancelBtn = el('button', 'ui-dlg-btn', opts.cancelText || '取消');
                cancelBtn.type = 'button';
                actions.appendChild(cancelBtn);
            }
            var okBtn = el('button', 'ui-dlg-btn ' + (opts.danger ? 'danger' : 'primary'),
                opts.confirmText || opts.okText || (kind === 'alert' ? '知道了' : '确定'));
            okBtn.type = 'button';
            actions.appendChild(okBtn);
            box.appendChild(actions);
            mask.appendChild(box);

            var done = false;
            function finish(val) {
                if (done) return;
                done = true;
                document.removeEventListener('keydown', onKey, true);
                if (mask.parentNode) mask.parentNode.removeChild(mask);
                if (prev && prev.focus) { try { prev.focus(); } catch (e) {} }
                resolve(val);
            }
            function submit() {
                if (kind === 'prompt') {
                    var v = input.value.trim();
                    var msg = opts.validate ? opts.validate(v) : (opts.required !== false && !v ? '这一项不能空着，请填写后再点确定。' : '');
                    if (msg) { err.textContent = msg; input.focus(); return; }
                    finish(v);
                } else if (kind === 'alert') {
                    finish(undefined);
                } else {
                    finish(true);
                }
            }
            function cancel() { finish(kind === 'prompt' ? null : kind === 'alert' ? undefined : false); }
            function onKey(e) {
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); return; }
                if (e.key === 'Enter' && input && e.target === input) { e.preventDefault(); submit(); return; }
                if (e.key === 'Tab') {
                    var f = box.querySelectorAll('input,button');
                    if (!f.length) return;
                    var first = f[0], last = f[f.length - 1];
                    if (!box.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
                    else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
                    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
                }
            }
            document.addEventListener('keydown', onKey, true);
            okBtn.addEventListener('click', submit);
            if (cancelBtn) cancelBtn.addEventListener('click', cancel);
            mask.addEventListener('mousedown', function (e) { if (e.target === mask && kind !== 'alert') cancel(); });
            document.body.appendChild(mask);
            // 危险操作默认聚焦“取消”，避免连按回车误确认
            (input || (opts.danger && cancelBtn) || okBtn).focus();
            if (input) input.select();
        });
    }

    root.uiConfirm = function (opts) { return open(typeof opts === 'string' ? { message: opts } : (opts || {}), 'confirm'); };
    root.uiAlert = function (opts) { return open(typeof opts === 'string' ? { message: opts } : (opts || {}), 'alert'); };
    root.uiPrompt = function (opts) { return open(opts || {}, 'prompt'); };
})(window);
