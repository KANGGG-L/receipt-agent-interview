'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15185';
const OUT = path.join(__dirname, '..');
const LOG = path.join(OUT, 'logs');
const UI = path.join(OUT, 'ui');
fs.mkdirSync(LOG, { recursive: true });
fs.mkdirSync(UI, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function walk(node, acc) {
  if (!node) return;
  const role = node.role || '';
  const name = (node.name || '').trim();
  const ignored = !!node.ignored;
  if (!ignored && (role === 'spinbutton' || role === 'combobox')) {
    acc.push({ role, name, value: node.value });
  }
  for (const c of node.children || []) walk(c, acc);
}

async function setup(browser, w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h });
  page.setDefaultTimeout(60000);
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
  await page.goto(BASE + '/?cb=' + Date.now(), { waitUntil: 'networkidle0' });
  await page.evaluate(() => {
    localStorage.setItem('demo_role', 'owner');
    localStorage.setItem('demo_tenant', 'uat035dev');
  });
  await page.evaluate(() => {
    if (typeof window.apiFetch === 'function') {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
        return orig.call(this, url, opts);
      };
    }
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(700);
  await page.evaluate(() => {
    if (typeof window.apiFetch === 'function') {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
        return orig.call(this, url, opts);
      };
    }
  });
  return page;
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const report = { pass: true, cases: [] };

  for (const [label, w, h, hant] of [
    ['hans_1440', 1440, 900, false],
    ['hant_390', 390, 844, true],
  ]) {
    const page = await setup(browser, w, h);
    if (hant) {
      await page.evaluate(() => {
        try { localStorage.setItem('demo_locale', 'zh-Hant-HK'); } catch (e) {}
        if (typeof setLocale === 'function') setLocale('zh-Hant-HK');
        else if (window.I18N && I18N.setLocale) I18N.setLocale('zh-Hant-HK');
      });
      await page.reload({ waitUntil: 'networkidle0' });
      await sleep(700);
      await page.evaluate(() => {
        if (typeof window.apiFetch === 'function') {
          const orig = window.apiFetch;
          window.apiFetch = function(url, opts) {
            opts = opts || {};
            opts.headers = Object.assign({}, opts.headers || {}, { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035dev' });
            return orig.call(this, url, opts);
          };
        }
      });
    }

    // Daily consume
    await page.evaluate(() => {
      const btn = document.querySelector('[data-target="tab-dishes"]');
      if (btn) btn.click();
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
      if (typeof loadDailyConsumption === 'function') loadDailyConsumption();
    });
    await sleep(1200);

    const domDaily = await page.evaluate(() => {
      const qty = document.getElementById('dishConsumeQty_1');
      return {
        qtyAria: qty ? qty.getAttribute('aria-label') : null,
        searchAria: (document.getElementById('dishConsumeSearchInput') || {}).getAttribute?.('aria-label') || null,
        catAria: (document.getElementById('dishCategoryFilter') || {}).getAttribute?.('aria-label') || null,
        stAria: (document.getElementById('dishStatusFilter') || {}).getAttribute?.('aria-label') || null,
        scriptSrc: (document.querySelector('script[src*="dish_consumption"]') || {}).src || null,
      };
    });

    const snapDaily = await page.accessibility.snapshot({ interestingOnly: false });
    const axDaily = [];
    walk(snapDaily, axDaily);
    const spin = axDaily.filter(n => n.role === 'spinbutton');
    const namedSpin = spin.filter(n => n.name && (n.name.includes('消耗') || n.name.includes('份数') || n.name.includes('份數')));

    // Keyboard: focus qty, type value
    await page.focus('#dishConsumeQty_1');
    await page.keyboard.down('Control');
    await page.keyboard.press('a');
    await page.keyboard.up('Control');
    await page.keyboard.type('2');
    await sleep(200);
    const qtyVal = await page.$eval('#dishConsumeQty_1', el => el.value);

    // BOM filters
    await page.evaluate(() => {
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-bom');
    });
    await sleep(800);
    const snapBom = await page.accessibility.snapshot({ interestingOnly: false });
    const axBom = [];
    walk(snapBom, axBom);
    const combos = axBom.filter(n => n.role === 'combobox');
    const cat = combos.find(n => n.name && (n.name.includes('分类') || n.name.includes('分類')));
    const st = combos.find(n => n.name && (n.name.includes('状态') || n.name.includes('狀態') || n.name.includes('在售')));

    // Keyboard filter on category
    await page.focus('#dishCategoryFilter');
    await page.select('#dishCategoryFilter', '主食');
    await sleep(300);
    const catVal = await page.$eval('#dishCategoryFilter', el => el.value);
    await page.focus('#dishStatusFilter');
    await page.select('#dishStatusFilter', 'all');
    const stVal = await page.$eval('#dishStatusFilter', el => el.value);

    // Regression: date still named via label
    await page.evaluate(() => {
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
    });
    await sleep(400);
    const dateNamed = await page.evaluate(() => {
      const el = document.getElementById('dishConsumptionDate');
      const lab = document.querySelector('label[for="dishConsumptionDate"]');
      return !!(lab && lab.textContent.trim() && el);
    });

    await page.screenshot({ path: path.join(UI, label + '_daily.png'), fullPage: false });

    const caseResult = {
      label, domDaily, qtyVal, catVal, stVal, dateNamed,
      spinNames: spin.map(s => s.name),
      namedSpinCount: namedSpin.length,
      catName: cat ? cat.name : null,
      stName: st ? st.name : null,
      comboNames: combos.map(c => c.name),
    };
    const ok =
      namedSpin.length >= 1 &&
      !!cat &&
      !!st &&
      qtyVal === '2' &&
      !!domDaily.qtyAria &&
      !!domDaily.catAria &&
      !!domDaily.stAria &&
      !domDaily.stAria.includes('全部分类') &&
      !domDaily.stAria.includes('全部分類') &&
      dateNamed;
    caseResult.ok = ok;
    if (!ok) report.pass = false;
    report.cases.push(caseResult);
    await page.close();
  }

  // Tenant B baseline: no dishes cross-read
  const pageB = await setup(browser, 1440, 900);
  const bDishes = await pageB.evaluate(async () => {
    const r = await fetch('/api/dishes', { headers: { 'X-Role': 'owner', 'X-Tenant-Id': 'uat035tenantb' } });
    return r.json();
  });
  report.tenantB = { dishes: bDishes, ok: Array.isArray((bDishes.data || bDishes)) ? (bDishes.data || bDishes).length === 0 : (bDishes.data || []).length === 0 };
  if (!report.tenantB.ok) report.pass = false;
  await pageB.close();

  fs.writeFileSync(path.join(LOG, 'axtree_after.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
  process.exit(report.pass ? 0 : 1);
}
main().catch(e => { console.error(e); process.exit(1); });
