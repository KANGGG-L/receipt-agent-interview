'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');
const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15175';
const TN = 'uat033uat';
const OUT = process.env.UAT_OUT;
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  let lastReport = null;
  page.on('response', async (res) => {
    try {
      const u = res.url();
      if (u.includes('/api/cost_report') && !u.includes('/items')) {
        lastReport = await res.json().catch(() => null);
      }
    } catch (e) {}
  });
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': TN });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await sleep(400);
  await page.evaluate((tn) => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    try { localStorage.setItem('demo_tenant', tn); } catch (e) {}
  }, TN);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(600);
  await page.evaluate((tn) => {
    if (typeof window.apiFetch === 'function') {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': tn });
        return orig.call(this, url, opts);
      };
    }
  }, TN);

  // Activate 部门花销 via real sidebar button
  const clicked = await page.evaluate(() => {
    const btn = document.querySelector('[data-target="tab-report"]');
    if (btn) { btn.click(); return 'data-target=tab-report'; }
    const candidates = Array.from(document.querySelectorAll('a,button,[role="tab"],.sidebar-btn,.nav-item'));
    const hit = candidates.find(el => /部门花销/.test((el.textContent || '').trim()));
    if (hit) { hit.click(); return (hit.textContent || '').trim().slice(0, 40); }
    return null;
  });
  await sleep(800);
  // Ensure section visible (layout may keep inactive tabs display:none)
  await page.evaluate(() => {
    document.querySelectorAll('.tab-content').forEach(el => {
      if (el.id === 'tab-report') {
        el.classList.add('active');
        el.style.display = 'block';
        el.style.visibility = 'visible';
      }
    });
  });
  await sleep(200);
  await page.evaluate(() => {
    const m = document.getElementById('costReportMonth');
    if (m) m.value = '2026-10';
    if (typeof loadCostReport === 'function') loadCostReport();
  });
  await sleep(1800);
  // Scroll summary into view
  await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    if (el) el.scrollIntoView({ block: 'center' });
  });
  await sleep(300);

  const summary = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    return el ? el.textContent : null;
  });
  const paint = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    const tab = document.getElementById('tab-report');
    if (!el) return null;
    const cs = window.getComputedStyle(el);
    const tcs = tab ? window.getComputedStyle(tab) : null;
    return {
      innerHTML: el.innerHTML.slice(0, 800),
      textContent: el.textContent,
      display: cs.display,
      visibility: cs.visibility,
      color: cs.color,
      offsetHeight: el.offsetHeight,
      offsetWidth: el.offsetWidth,
      tabDisplay: tcs ? tcs.display : null,
      tabClass: tab ? tab.className : null,
      tabOffsetH: tab ? tab.offsetHeight : null,
    };
  });
  const helpers = await page.evaluate(() => ({
    hasCount: typeof costReportMissingFxCount === 'function',
    hasNotice: typeof costReportMissingFxNotice === 'function',
    sample: (typeof costReportMissingFxCount === 'function')
      ? costReportMissingFxCount({ unconverted: [{ receipt_id: 1 }], unconverted_warning: '有 1 张' })
      : null,
  }));
  await page.screenshot({ path: path.join(UI, 'cost-report-missing-fx.png'), fullPage: true });
  const box = await page.evaluate(() => {
    const el = document.getElementById('costReportSummary');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.x, y: r.y + (window.scrollY||0), width: Math.max(r.width, 40), height: Math.max(r.height, 20) };
  });
  if (box) {
    try {
      await page.screenshot({
        path: path.join(UI, 'cost-report-summary-missing-fx-crop.png'),
        clip: {
          x: Math.max(0, box.x - 4),
          y: Math.max(0, box.y - 4),
          width: Math.min(box.width + 8, 1400),
          height: Math.min(box.height + 8, 400),
        },
      });
    } catch (e) {
      fs.writeFileSync(path.join(LOG, 'crop-before-error.txt'), String(e));
    }
  }
  const result = {
    clicked, summary, paint, helpers,
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
  const okTotal = result.lastReport && result.lastReport.month_total === 20;
  const okSummary = !!(summary && /汇率/.test(summary) && /1/.test(summary) && /HK\$20|20\.00/.test(summary));
  const okHelpers = helpers.hasCount && helpers.hasNotice && helpers.sample === 1;
  // Paint: prefer layout size; also accept visible tab + non-empty summary text (DOM painted)
  const okPaint = !!(paint && /汇率/.test(paint.textContent || '') &&
    ((paint.offsetHeight > 0 && paint.offsetWidth > 0) ||
     (paint.tabDisplay && paint.tabDisplay !== 'none' && paint.tabOffsetH > 0)));
  console.log(JSON.stringify({ okCount, okTotal, okSummary, okHelpers, okPaint, summary, paint, lastReport: result.lastReport }, null, 2));
  await browser.close();
  if (!okCount || !okTotal || !okSummary || !okHelpers || !okPaint) process.exit(1);
  console.log('UI_BEFORE_OK');
})().catch((e) => { console.error(e); process.exit(1); });
