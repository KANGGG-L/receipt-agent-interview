#!/usr/bin/env node
/**
 * UAT-027：库存/餐品金额展示 HK$ + 千分位；null → —；外币不强标 HKD。
 * 用法：node demo/tests/frontend_uat027_money_format_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const ROOT = path.resolve(__dirname, '../..');
const I18N_DIR = path.join(ROOT, 'demo/static/js/i18n');
let pass = 0;
let fail = 0;
const results = [];

function check(name, cond, detail) {
  results.push({ name, ok: !!cond, detail: cond ? undefined : detail });
  if (cond) { pass += 1; console.log('[PASS]', name); }
  else { fail += 1; console.log('[FAIL]', name, detail || ''); }
}
function eq(name, actual, expected) {
  check(name, actual === expected, { actual, expected });
}

function loadI18n() {
  const sandbox = { console, setTimeout, clearTimeout, Map, URLSearchParams, URL, Intl };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.__I18N_NO_AUTOBOOT = true;
  vm.createContext(sandbox);
  for (const f of ['zh-hant-data.js', 'zh-hant-hk-glossary.js', 'locales/zh-Hans.js', 'locales/zh-Hant-HK.js', 'i18n.js']) {
    vm.runInContext(fs.readFileSync(path.join(I18N_DIR, f), 'utf8'), sandbox, { filename: f });
  }
  return sandbox;
}

const sb = loadI18n();
const I18N = sb.I18N;

// ---- AC: synthetic money ----
for (const loc of ['zh-Hans', 'zh-Hant-HK']) {
  I18N._setLocaleForTest(loc);
  eq('fmtMoney 1234.5 ' + loc, I18N.fmtMoney(1234.5), 'HK$1,234.50');
  eq('fmtMoney 0 ' + loc, I18N.fmtMoney(0), 'HK$0.00');
  eq('fmtMoney null ' + loc, I18N.fmtMoney(null), '—');
  eq('fmtMoney undefined ' + loc, I18N.fmtMoney(undefined), '—');
  eq('fmtMoney empty ' + loc, I18N.fmtMoney(''), '—');
  // foreign currency must not be forced to HKD
  const sgd = I18N.fmtMoney(1234.5, 'SGD');
  check('fmtMoney SGD not HKD-prefixed ' + loc, !String(sgd).startsWith('HK$'), sgd);
  check('fmtMoney SGD has grouping ' + loc, /1[,.]234/.test(String(sgd)), sgd);
}

// ---- Static: inventory_board uses I18N / formatHkd, no bare $ price ----
const inv = fs.readFileSync(path.join(ROOT, 'demo/static/js/inventory_board.js'), 'utf8');
check('formatHkd delegates to I18N.fmtMoney', /function formatHkd[\s\S]*I18N\.fmtMoney\(price,\s*'HKD'\)/.test(inv));
check('list last_unit_price uses I18N.fmtMoney', /I18N\.fmtMoney\(sku\.last_unit_price\)/.test(inv));
check('no bare `$`+toFixed list price', !/`\$\$\{Number\(sku\.last_unit_price\)\.toFixed\(2\)\}`/.test(inv) && !/`\$\$\{Number\(sku\.last_unit_price\)/.test(inv));
check('no formatHkd return `$`+toFixed', !/return '\$' \+ Number\(price\)\.toFixed\(2\)/.test(inv));
check('AI cards use I18N.fmtMoney', /I18N\.fmtMoney\(it\.earliest_price\)/.test(inv) && /I18N\.fmtMoney\(it\.latest_price\)/.test(inv) && /I18N\.fmtMoney\(it\.impact_amount\)/.test(inv));
check('AI summary uses I18N.fmtMoney', /I18N\.fmtMoney\(data\.total_impact_amount\)/.test(inv));
check('chart/history use formatHkd', /formatHkd\(v\)/.test(inv) && /formatHkd\(avgLine\)/.test(inv) && /formatHkd\(price\)/.test(inv));
check('ledger amount uses I18N.fmtMoney', /I18N\.fmtMoney\(amt\)/.test(inv));

const dish = fs.readFileSync(path.join(ROOT, 'demo/static/js/dish_bom.js'), 'utf8');
check('dish_bom price/cost/profit use I18N.fmtMoney', /I18N\.fmtMoney\(d\.price\)/.test(dish) && /I18N\.fmtMoney\(d\.theoretical_cost\)/.test(dish) && /I18N\.fmtMoney\(d\.gross_profit\)/.test(dish));
check('dish_bom null path shows em-dash', /hasPrice \? I18N\.fmtMoney\(d\.price\) : '—'/.test(dish));

const fin = fs.readFileSync(path.join(ROOT, 'demo/static/js/finance.js'), 'utf8');
check('finance HKD path uses I18N.fmtMoney', /I18N\.fmtMoney\(r\.total_amount\)/.test(fin) || /I18N\.fmtMoney\(r\.total_hkd\)/.test(fin));
check('finance foreign keeps formatCurrency / original currency', /formatCurrency\(r\.total_amount,\s*r\.currency\)/.test(fin) && /currency !== 'HKD'/.test(fin));
check('finance does not force HKD label on foreign original', !/原币.*I18N\.fmtMoney\(r\.total_amount\)/.test(fin));

const i18nSrc = fs.readFileSync(path.join(I18N_DIR, 'i18n.js'), 'utf8');
check('i18n fmtMoney defaults currency HKD', /currency:\s*currency\s*\|\|\s*'HKD'/.test(i18nSrc));
check('i18n fmtMoney null/non-finite → em-dash', /!isFinite\(n\)\)\s*return '—'/.test(i18nSrc));

console.log('');
console.log(pass + ' PASS / ' + fail + ' FAIL');
fs.mkdirSync(path.join(ROOT, 'docs/handoff/artifacts/uat027-dev/logs'), { recursive: true });
fs.writeFileSync(
  path.join(ROOT, 'docs/handoff/artifacts/uat027-dev/logs/harness-uat027.json'),
  JSON.stringify({ summary: { pass, fail, total: pass + fail }, results }, null, 2)
);
process.exit(fail ? 1 : 0);
