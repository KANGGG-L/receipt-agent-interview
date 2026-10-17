'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15155';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);

  // Capture cost_report API
  let lastReport = null;
  page.on('response', async (res) => {
    try {
      const u = res.url();
      if (u.includes('/api/cost_report') && !u.includes('/items')) {
        lastReport = await res.json().catch(() => null);
      }
    } catch (e) {}
  });

  await page.setExtraHTTPHeaders({
    'X-Role': 'owner',
    'X-Tenant-Id': 'uat033dev',
  });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await sleep(500);
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    try { localStorage.setItem('demo_tenant', 'uat033dev'); } catch (e) {}
  });

  // Ensure apiFetch sends tenant — patch if needed
  await page.evaluate(() => {
    // many demos use X-Role from localStorage via common.js; force tenant header wrapper
    if (typeof window.apiFetch === 'function' && !window.__uat033_patched) {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {
          'X-Role': 'owner',
          'X-Tenant-Id': 'uat033dev',
        });
        return orig.call(this, url, opts);
      };
      window.__uat033_patched = true;
    }
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(800);
  await page.evaluate(() => {
    if (typeof window.apiFetch === 'function' && !window.__uat033_patched) {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {
          'X-Role': 'owner',
          'X-Tenant-Id': 'uat033dev',
        });
        return orig.call(this, url, opts);
      };
      window.__uat033_patched = true;
    }
  });

  // Navigate to cost report tab
  const clicked = await page.evaluate(() => {
    const candidates = Array.from(document.querySelectorAll('a,button,[role="tab"],.nav-item,.menu-item,div,span'));
    const hit = candidates.find(el => /部门花销|花销报表|成本报表|部门成本/.test((el.textContent || '').trim()));
    if (hit) { hit.click(); return (hit.textContent || '').trim().slice(0, 40); }
    // try data-tab / hash
    const tab = document.querySelector('[data-tab="cost_report"],#tabCostReport,[href*="cost"]');
    if (tab) { tab.click(); return 'data-tab'; }
    return null;
  });

  await sleep(1000);

  // Set month and load
  await page.evaluate(() => {
    const m = document.getElementById('costReportMonth');
    if (m) m.value = '2026-10';
    if (typeof loadCostReport === 'function') loadCostReport();
  });
  await sleep(1500);

  const summary = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    return el ? el.textContent : null;
  });
  const helpers = await page.evaluate(() => ({
    hasCount: typeof costReportMissingFxCount === 'function',
    hasNotice: typeof costReportMissingFxNotice === 'function',
    sample: (typeof costReportMissingFxCount === 'function')
      ? costReportMissingFxCount({ unconverted: [{ receipt_id: 1 }], unconverted_warning: '有 1 张' })
      : null,
  }));

  await page.screenshot({ path: path.join(UI, 'cost-report-missing-fx.png'), fullPage: true });

  const result = {
    clicked,
    summary,
    helpers,
    lastReport: lastReport && lastReport.data ? {
      month_total: lastReport.data.month_total,
      unconverted_count: lastReport.data.unconverted_count,
      missing_fx_count: lastReport.data.missing_fx_count,
      unconverted_warning: lastReport.data.unconverted_warning,
      unconverted_len: (lastReport.data.unconverted || []).length,
    } : lastReport,
  };
  fs.writeFileSync(path.join(LOG, 'ui-before-fx.json'), JSON.stringify(result, null, 2));

  const okCount = result.lastReport && result.lastReport.unconverted_count === 1;
  const okSummary = summary && /汇率/.test(summary) && /1/.test(summary);
  const okHelpers = helpers.hasCount && helpers.hasNotice && helpers.sample === 1;
  console.log(JSON.stringify({ okCount, okSummary, okHelpers, summary, lastReport: result.lastReport }, null, 2));
  if (!okCount || !okSummary || !okHelpers) {
    await browser.close();
    process.exit(1);
  }
  await browser.close();
  console.log('UI_BEFORE_OK');
}

main().catch((e) => { console.error(e); process.exit(1); });
