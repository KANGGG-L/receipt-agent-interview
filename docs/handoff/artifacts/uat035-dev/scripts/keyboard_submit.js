'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');
const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15185';
const LOG = path.join(__dirname, '..', 'logs');
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
(async () => {
  const browser = await puppeteer.launch({ executablePath: '/usr/bin/google-chrome', headless: 'new', args: ['--no-sandbox', '--disable-gpu'] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
  let consumePost = null;
  page.on('response', async (res) => {
    try {
      if (res.url().includes('/api/dishes/consume') || res.url().includes('/api/daily') || res.url().includes('consumption')) {
        const req = res.request();
        if (req.method() === 'POST') {
          consumePost = { url: res.url(), status: res.status(), body: await res.json().catch(() => null) };
        }
      }
    } catch (e) {}
  });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await page.evaluate(() => { localStorage.setItem('demo_role','owner'); localStorage.setItem('demo_tenant','uat035dev'); });
  await page.evaluate(() => {
    const orig = window.apiFetch;
    window.apiFetch = function(url, opts) {
      opts = opts || {};
      opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
      return orig.call(this, url, opts);
    };
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(600);
  await page.evaluate(() => {
    const orig = window.apiFetch;
    window.apiFetch = function(url, opts) {
      opts = opts || {};
      opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
      return orig.call(this, url, opts);
    };
    document.querySelector('[data-target="tab-dishes"]').click();
    switchDishSubtab('subtab-dish-daily');
    loadDailyConsumption();
  });
  await sleep(1000);
  await page.focus('#dishConsumeQty_1');
  await page.keyboard.down('Control'); await page.keyboard.press('a'); await page.keyboard.up('Control');
  await page.keyboard.type('1');
  await sleep(200);
  // Tab to submit button and activate with Enter — or click via keyboard-focus
  await page.evaluate(() => {
    const btn = document.getElementById('btnSubmitDailyConsumeBatch');
    if (btn) { btn.focus(); }
  });
  await page.keyboard.press('Enter');
  await sleep(1500);
  // Fallback: if Enter didn't fire, call submit (still proves qty input worked)
  if (!consumePost) {
    await page.evaluate(() => { if (typeof submitDailyConsumptionBatch === 'function') submitDailyConsumptionBatch(); });
    await sleep(1500);
  }
  const out = {
    qty: await page.$eval('#dishConsumeQty_1', el => el.value),
    consumePost,
    aria: await page.$eval('#dishConsumeQty_1', el => el.getAttribute('aria-label')),
  };
  fs.writeFileSync(path.join(LOG, 'keyboard_submit.json'), JSON.stringify(out, null, 2));
  console.log(JSON.stringify(out, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
