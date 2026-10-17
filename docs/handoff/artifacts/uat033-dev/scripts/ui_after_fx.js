'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');
const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15155';
const UI = path.join(__dirname, '..', 'ui');
const LOG = path.join(__dirname, '..', 'logs');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': 'uat033dev' });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await page.evaluate(() => { try { localStorage.setItem('demo_role', 'owner'); } catch (e) {} });
  await page.reload({ waitUntil: 'networkidle0' });
  await page.evaluate(() => {
    const orig = window.apiFetch;
    window.apiFetch = function(url, opts) {
      opts = opts || {};
      opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat033dev' });
      return orig.call(this, url, opts);
    };
  });
  await page.evaluate(() => {
    const m = document.getElementById('costReportMonth');
    if (m) m.value = '2026-10';
    if (typeof loadCostReport === 'function') loadCostReport();
  });
  await sleep(1500);
  const summary = await page.evaluate(() => (document.getElementById('costReportSummary') || {}).textContent || '');
  await page.screenshot({ path: path.join(UI, 'cost-report-after-fx.png'), fullPage: true });
  const cleared = /汇率/.test(summary) === false && /220|HK\$220/.test(summary);
  fs.writeFileSync(path.join(LOG, 'ui-after-fx.json'), JSON.stringify({ summary, cleared }, null, 2));
  console.log(JSON.stringify({ summary, cleared }, null, 2));
  await browser.close();
  if (!cleared) process.exit(1);
  console.log('UI_AFTER_OK');
})().catch(e => { console.error(e); process.exit(1); });
