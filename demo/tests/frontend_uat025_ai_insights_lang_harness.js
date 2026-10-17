#!/usr/bin/env node
/**
 * UAT-025：静态检查 loadAiLeanInsights 携带当前语言（无浏览器）。
 * 用法：node demo/tests/frontend_uat025_ai_insights_lang_harness.js
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ROOT = path.resolve(__dirname, '../..');
const INV = path.join(ROOT, 'demo/static/js/inventory_board.js');
const API = path.join(ROOT, 'demo/app/api_admin.py');

let pass = 0;
let fail = 0;
function check(name, cond) {
  if (cond) {
    pass += 1;
    console.log('[PASS]', name);
  } else {
    fail += 1;
    console.log('[FAIL]', name);
  }
}

const js = fs.readFileSync(INV, 'utf8');
const py = fs.readFileSync(API, 'utf8');

check('loadAiLeanInsights exists', /function\s+loadAiLeanInsights\s*\(/.test(js));
check('reads I18N.getLocale', /I18N\.getLocale/.test(js));
check('builds ?lang= URL', /\/api\/ai-insights\?lang=/.test(js));
check('encodeURIComponent(lang)', /encodeURIComponent\(\s*lang\s*\)/.test(js));
check('uses insightsUrl variable', /apiFetch\(\s*insightsUrl\s*\)/.test(js));
check('UAT-025 comment in JS', /UAT-025/.test(js));

// Bare no-lang owner fetch must not remain as sole path
const bareRe = /apiFetch\(\s*['"]\/api\/ai-insights['"]\s*\)/;
const bareLines = js.split('\n').filter((l) => bareRe.test(l) && !l.trim().startsWith('//'));
check('no bare apiFetch(/api/ai-insights) statement', bareLines.length === 0);

check('api_admin ai_insights has lang param', /def ai_insights\(request: Request, lang: str = ""\)/.test(py));
check('sets review_locale via normalize_locale', /data\["review_locale"\]\s*=\s*normalize_locale\(lang\)\s*or\s*""/.test(py));
check('UAT-025 docstring on endpoint', /UAT-025/.test(py));
check('does not call run_review in ai_insights block', (() => {
  const i = py.indexOf('def ai_insights');
  const chunk = py.slice(i, i + 900);
  return !/run_review\s*\(/.test(chunk);
})());

console.log('');
console.log(pass + ' PASS / ' + fail + ' FAIL');
process.exit(fail ? 1 : 0);
