#!/usr/bin/env node
/**
 * UAT-023：业务身份单元格 data-i18n-skip 后，OpenCC 不得改品名；表头 / UI chrome 仍转换。
 * 用法：node demo/tests/frontend_uat023_export_preview_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const I18N_DIR = path.join(__dirname, '..', 'static', 'js', 'i18n');

function loadI18n(extra) {
    const sandbox = Object.assign({ console, setTimeout, clearTimeout, Map, URLSearchParams, URL, Intl }, extra || {});
    sandbox.window = sandbox;
    sandbox.globalThis = sandbox;
    sandbox.__I18N_NO_AUTOBOOT = true;
    sandbox.document = {
        documentElement: { lang: 'zh-Hant-HK', getAttribute() { return null; }, setAttribute() {} },
        title: '',
        body: null,
        createElement(tag) {
            const el = {
                nodeType: 1,
                nodeName: String(tag).toUpperCase(),
                _tc: '',
                _ih: '',
                childNodes: [],
                getAttribute() { return null; },
                setAttribute() {},
            };
            Object.defineProperty(el, 'textContent', {
                get() { return this._tc; },
                set(v) {
                    this._tc = String(v);
                    this._ih = String(v)
                        .replace(/&/g, '&amp;')
                        .replace(/</g, '&lt;')
                        .replace(/>/g, '&gt;');
                },
            });
            Object.defineProperty(el, 'innerHTML', {
                get() { return this._ih; },
                set(v) { this._ih = String(v); },
            });
            return el;
        },
    };
    vm.createContext(sandbox);
    for (const f of ['zh-hant-data.js', 'zh-hant-hk-glossary.js', 'locales/zh-Hans.js', 'locales/zh-Hant-HK.js', 'i18n.js']) {
        vm.runInContext(fs.readFileSync(path.join(I18N_DIR, f), 'utf8'), sandbox, { filename: f });
    }
    return sandbox;
}

const results = [];
let failed = 0;
function eq(name, actual, expected) {
    const ok = actual === expected;
    results.push({ name, ok, actual, expected });
    if (!ok) {
        failed++;
        console.error('FAIL', name, { actual, expected });
    } else {
        console.log('PASS', name);
    }
}

function mkText(data) { return { nodeType: 3, nodeName: '#text', data, parentNode: null }; }
function mkEl(tag, attrs, children) {
    const el = {
        nodeType: 1,
        nodeName: tag.toUpperCase(),
        parentNode: null,
        _attrs: Object.assign({}, attrs || {}),
        childNodes: [],
        getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; },
        setAttribute(n, v) { this._attrs[n] = String(v); },
        get textContent() {
            return this.childNodes.map(c => (c.nodeType === 3 ? c.data : c.textContent)).join('');
        },
    };
    (children || []).forEach(c => { c.parentNode = el; el.childNodes.push(c); });
    return el;
}

const sb = loadI18n();
const I18N = sb.I18N;
I18N._setLocaleForTest('zh-Hant-HK');

const longName = '有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）';
const nameTd = mkEl('td', { 'data-i18n-skip': '', class: 'export-col-desc' }, [mkText(longName)]);
const statusTd = mkEl('td', { class: 'export-col-nowrap' }, [mkText('已入账')]);
const th = mkEl('th', {}, [mkText('食材品名')]);
const chrome = mkEl('button', {}, [mkText('导出 CSV')]);
const rootEl = mkEl('div', {}, [th, nameTd, statusTd, chrome]);
I18N._translateSubtree(rootEl);

eq('business name preserved (Hans)', nameTd.childNodes[0].data, longName);
eq('header converted via OpenCC', th.childNodes[0].data, I18N.convert('食材品名'));
eq('UI chrome converted', chrome.childNodes[0].data, I18N.convert('导出 CSV'));
eq('status cell converted (not skipped)', statusTd.childNodes[0].data, I18N.convert('已入账'));

function esc(s) {
    const d = sb.document.createElement('div');
    d.textContent = s === null || s === undefined ? '' : String(s);
    return d.innerHTML;
}
eq('html escaped in names', esc('鸡翅<script>alert(1)</script>'), '鸡翅&lt;script&gt;alert(1)&lt;/script&gt;');

const th2 = mkEl('th', {}, [mkText('供应商名称')]);
I18N._translateSubtree(th2);
eq('supplier header converted', th2.childNodes[0].data, I18N.convert('供应商名称'));

const exportSrc = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'export_center.js'), 'utf8');
eq('export_center defines isExportBusinessIdentityHeader', exportSrc.includes('function isExportBusinessIdentityHeader'), true);
eq('export_center emits data-i18n-skip attr', exportSrc.includes("preserveBiz ? ' data-i18n-skip'"), true);

// Extract identity predicate + row builder (UAT-023 fix2: 归属部门 must skip OpenCC)
const idStart = exportSrc.indexOf('function isExportBusinessIdentityHeader');
const idEnd = exportSrc.indexOf('// ========== 虚拟滚动');
const extract = exportSrc.slice(idStart, idEnd);
const box = { console };
box.document = sb.document;
box.esc = function (s) {
    const d = sb.document.createElement('div');
    d.textContent = s === null || s === undefined ? '' : String(s);
    return d.innerHTML;
};
box.exportColAlignClass = function () { return ''; };
vm.createContext(box);
vm.runInContext(extract + '\nthis.__build = buildExportRowsHtml; this.__isId = isExportBusinessIdentityHeader;', box);

eq('identity header 归属部门', box.__isId('归属部门'), true);
eq('identity header 部门名称', box.__isId('部门名称'), true);
eq('identity header 部门 exact', box.__isId('部门'), true);
eq('identity header 销售对象', box.__isId('销售对象'), true);
eq('non-identity 分类', box.__isId('分类'), false);
eq('non-identity 单据状态', box.__isId('单据状态'), false);

const LONG_DEPT = '后厨冷链中心（超长部门名称）';
const deptHeaders = ['收据ID', '归属部门', '单据状态'];
const deptRows = [['#1', LONG_DEPT, '已入账']];
const deptHtml = box.__build(deptRows, deptHeaders);
const skipCount = (deptHtml.match(/data-i18n-skip/g) || []).length;
eq('build marks only 归属部门 cell with data-i18n-skip', skipCount, 1);
eq('build HTML contains Hans dept name', deptHtml.includes(LONG_DEPT), true);
eq('build HTML does not pre-convert dept to Hant', deptHtml.includes('後廚冷鏈中心') === false, true);

// Painted-DOM equality: skip cell stays Hans under Hant (simulates CSV Hans cell)
const deptTd = mkEl('td', { 'data-i18n-skip': '', class: 'export-col-nowrap' }, [mkText(LONG_DEPT)]);
const deptTh = mkEl('th', {}, [mkText('归属部门')]);
const deptRoot = mkEl('div', {}, [deptTh, deptTd]);
I18N._setLocaleForTest('zh-Hant-HK');
I18N._translateSubtree(deptRoot);
eq('Hant: 归属部门 cell == CSV Hans', deptTd.childNodes[0].data, LONG_DEPT);
eq('Hant: 归属部门 header still converts', deptTh.childNodes[0].data, I18N.convert('归属部门'));
eq('Hant: converted header differs from Hans label', deptTh.childNodes[0].data !== '归属部门', true);

// Negative control: without skip, same dept name would OpenCC-convert
const bareDeptTd = mkEl('td', { class: 'export-col-nowrap' }, [mkText(LONG_DEPT)]);
I18N._translateSubtree(bareDeptTd);
eq('Hant without skip: dept name converts (control)', bareDeptTd.childNodes[0].data !== LONG_DEPT, true);
eq('Hant without skip: matches OpenCC of dept', bareDeptTd.childNodes[0].data, I18N.convert(LONG_DEPT));

if (failed) {
    console.error(JSON.stringify({ failed, results }, null, 2));
    process.exit(1);
}
console.log(JSON.stringify({ ok: true, passed: results.length }, null, 2));
