'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');
const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15175';
const TN = 'uat033uat';
const OUT = process.env.UAT_OUT;
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
const sleep = (ms) => new Promise(r => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': TN });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await page.evaluate((tn) => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    try { localStorage.setItem('demo_tenant', tn); } catch (e) {}
  }, TN);
  await page.reload({ waitUntil: 'networkidle0' });
  await page.evaluate((tn) => {
    const orig = window.apiFetch;
    window.apiFetch = function(url, opts) {
      opts = opts || {};
      opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': tn });
      return orig.call(this, url, opts);
    };
  }, TN);
  await page.evaluate(() => {
    const btn = document.querySelector('[data-target="tab-report"]');
    if (btn) btn.click();
  });
  await sleep(600);
  await page.evaluate(() => {
    document.querySelectorAll('.tab-content').forEach(el => {
      if (el.id === 'tab-report') {
        el.classList.add('active');
        el.style.display = 'block';
        el.style.visibility = 'visible';
      }
    });
  });
  await page.evaluate(() => {
    const m = document.getElementById('costReportMonth');
    if (m) m.value = '2026-10';
    if (typeof loadCostReport === 'function') loadCostReport();
  });
  await sleep(1800);
  await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    if (el) el.scrollIntoView({ block: 'center' });
  });
  await sleep(300);
  const summary = await page.evaluate(() => (document.getElementById('costReportSummary') || {}).textContent || '');
  const paint = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    const tab = document.getElementById('tab-report');
    if (!el) return null;
    return {
      textContent: el.textContent,
      innerHTML: el.innerHTML.slice(0, 800),
      offsetHeight: el.offsetHeight,
      offsetWidth: el.offsetWidth,
      tabDisplay: tab ? getComputedStyle(tab).display : null,
      tabOffsetH: tab ? tab.offsetHeight : null,
    };
  });
  await page.screenshot({ path: path.join(UI, 'cost-report-after-fx.png'), fullPage: true });
  const box = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y + (window.scrollY||0), width: Math.max(r.width, 40), height: Math.max(r.height, 20) };
  });
  if (box) {
    try {
      await page.screenshot({
        path: path.join(UI, 'cost-report-summary-after-fx-crop.png'),
        clip: {
          x: Math.max(0, box.x - 4),
          y: Math.max(0, box.y - 4),
          width: Math.min(box.width + 8, 1400),
          height: Math.min(box.height + 8, 400),
        },
      });
    } catch (e) {
      fs.writeFileSync(path.join(LOG, 'crop-after-error.txt'), String(e));
    }
  }
  const cleared = /汇率/.test(summary) === false && /220|HK\$220/.test(summary);
  const okPaint = !!(paint && /220/.test(paint.textContent || '') &&
    ((paint.offsetHeight > 0) || (paint.tabOffsetH > 0)));
  fs.writeFileSync(path.join(LOG, 'ui-after-fx.json'), JSON.stringify({ summary, cleared, okPaint, paint }, null, 2));
  console.log(JSON.stringify({ summary, cleared, okPaint, paint }, null, 2));
  await browser.close();
  if (!cleared || !okPaint) process.exit(1);
  console.log('UI_AFTER_OK');
})().catch(e => { console.error(e); process.exit(1); });
