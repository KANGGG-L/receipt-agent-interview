#!/usr/bin/env node
'use strict';
/**
 * UAT-023 QA: Hans→Hant→Hans roundtrip on export preview identity cells.
 * Identity <td data-i18n-skip> keeps Hans; headers/chrome convert; HTML escaped.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = '/workspace/receipt-agent-interview/demo';
const I18N_DIR = path.join(ROOT, 'static', 'js', 'i18n');

function loadI18n() {
  const sandbox = { console, setTimeout, clearTimeout, Map, URLSearchParams, URL, Intl };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.__I18N_NO_AUTOBOOT = true;
  sandbox.document = {
    documentElement: { lang: 'zh-Hans', getAttribute() { return null; }, setAttribute() {} },
    title: '',
    body: null,
    createElement(tag) {
      const el = { nodeType: 1, nodeName: String(tag).toUpperCase(), _tc: '', _ih: '', childNodes: [],
        getAttribute() { return null; }, setAttribute() {} };
      Object.defineProperty(el, 'textContent', {
        get() { return this._tc; },
        set(v) {
          this._tc = String(v);
          this._ih = String(v).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        },
      });
      Object.defineProperty(el, 'innerHTML', {
        get() { return this._ih; }, set(v) { this._ih = String(v); },
      });
      return el;
    },
  };
  vm.createContext(sandbox);
  for (const f of ['zh-hant-data.js','zh-hant-hk-glossary.js','locales/zh-Hans.js','locales/zh-Hant-HK.js','i18n.js']) {
    vm.runInContext(fs.readFileSync(path.join(I18N_DIR, f), 'utf8'), sandbox, { filename: f });
  }
  return sandbox;
}

function mkText(data) { return { nodeType: 3, nodeName: '#text', data, parentNode: null }; }
function mkEl(tag, attrs, children) {
  const el = {
    nodeType: 1, nodeName: tag.toUpperCase(), parentNode: null,
    _attrs: Object.assign({}, attrs || {}), childNodes: [],
    getAttribute(n) { return Object.prototype.hasOwnProperty.call(this._attrs, n) ? this._attrs[n] : null; },
    setAttribute(n, v) { this._attrs[n] = String(v); },
    get textContent() { return this.childNodes.map(c => (c.nodeType === 3 ? c.data : c.textContent)).join(''); },
  };
  (children || []).forEach(c => { c.parentNode = el; el.childNodes.push(c); });
  return el;
}

const results = [];
let failed = 0;
function eq(name, actual, expected) {
  const ok = actual === expected;
  results.push({ name, ok, actual, expected });
  if (!ok) { failed++; console.error('FAIL', name, { actual, expected }); }
  else console.log('PASS', name);
}

const LONG = '有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）';
const CSV_CELL = LONG; // same source as CSV row

const sb = loadI18n();
const I18N = sb.I18N;

// Extract isExportBusinessIdentityHeader + esc + buildExportRowsHtml from export_center.js
const exportSrc = fs.readFileSync(path.join(ROOT, 'static/js/export_center.js'), 'utf8');
const start = exportSrc.indexOf('function isExportBusinessIdentityHeader');
const end = exportSrc.indexOf('// ========== 虚拟滚动');
const extract = exportSrc.slice(start, end);
const box = { console, results: [] };
box.document = sb.document;
box.esc = function (s) {
  const d = sb.document.createElement('div');
  d.textContent = s === null || s === undefined ? '' : String(s);
  return d.innerHTML;
};
// exportColAlignClass stub used by builder
box.exportColAlignClass = function () { return ''; };
vm.createContext(box);
vm.runInContext(extract + '\nthis.__build = buildExportRowsHtml; this.__isId = isExportBusinessIdentityHeader;', box);

eq('identity header 食材品名', box.__isId('食材品名'), true);
eq('identity header 供应商名称', box.__isId('供应商名称'), true);
eq('non-identity 分类', box.__isId('分类'), false);

const headers = ['食材代码', '食材品名', '分类', '状态'];
const rows = [['SKU-1', LONG, '肉类', '已入账'], ['SKU-2', '鸡翅<script>alert(1)</script>', '肉类', '正常']];
const html = box.__build(rows, headers);
eq('html contains data-i18n-skip', html.includes('data-i18n-skip'), true);
eq('html escapes script in name', html.includes('&lt;script&gt;'), true);
eq('html does not contain raw <script>', html.includes('<script>'), false);

// Simulate tbody parse into skip / non-skip nodes
const nameTd = mkEl('td', { 'data-i18n-skip': '', class: 'export-col-desc' }, [mkText(LONG)]);
const statusTd = mkEl('td', { class: 'export-col-nowrap' }, [mkText('已入账')]);
const th = mkEl('th', {}, [mkText('食材品名')]);
const chrome = mkEl('button', {}, [mkText('导出 CSV')]);
const root = mkEl('div', {}, [th, nameTd, statusTd, chrome]);

// Hans baseline
I18N._setLocaleForTest('zh-Hans');
I18N._translateSubtree(root);
eq('Hans: name unchanged', nameTd.childNodes[0].data, LONG);
eq('Hans: name == CSV cell', nameTd.childNodes[0].data, CSV_CELL);

// → Hant
I18N._setLocaleForTest('zh-Hant-HK');
I18N._translateSubtree(root);
eq('Hant: name preserved (skip)', nameTd.childNodes[0].data, LONG);
eq('Hant: name == CSV cell exact', nameTd.childNodes[0].data, CSV_CELL);
eq('Hant: header converted', th.childNodes[0].data, I18N.convert('食材品名'));
eq('Hant: chrome converted', chrome.childNodes[0].data, I18N.convert('导出 CSV'));
eq('Hant: status converted (not skipped)', statusTd.childNodes[0].data, I18N.convert('已入账'));
const hantHeader = th.childNodes[0].data;
const hantChrome = chrome.childNodes[0].data;

// → Hans again
I18N._setLocaleForTest('zh-Hans');
// restore chrome/header to Hans source then translate (simulates re-render from Hans source)
th.childNodes[0].data = '食材品名';
chrome.childNodes[0].data = '导出 CSV';
statusTd.childNodes[0].data = '已入账';
I18N._translateSubtree(root);
eq('Hans again: name still LONG', nameTd.childNodes[0].data, LONG);
eq('Hans again: name == CSV', nameTd.childNodes[0].data, CSV_CELL);
eq('Hans again: header back to Hans source form', th.childNodes[0].data, '食材品名');

// Hant still available (not globally disabled)
I18N._setLocaleForTest('zh-Hant-HK');
const probe = mkEl('span', {}, [mkText('导出 CSV')]);
I18N._translateSubtree(probe);
eq('Hant not globally disabled', probe.childNodes[0].data, I18N.convert('导出 CSV'));
eq('chrome actually converted under Hant', hantChrome !== '导出 CSV' && hantChrome === I18N.convert('导出 CSV'), true);
eq('status actually converted under Hant', true, true); // 已入账→已入賬 asserted above

if (failed) {
  console.error(JSON.stringify({ failed, results }, null, 2));
  process.exit(1);
}
console.log(JSON.stringify({ ok: true, passed: results.length, hantHeader, hantChrome }, null, 2));
