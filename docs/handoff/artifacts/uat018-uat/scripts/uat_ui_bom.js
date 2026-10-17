'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15235';
const TENANT = process.env.UAT_TENANT || 'uat018uat';
const OUT = path.join(__dirname, '..');
const LOG = path.join(OUT, 'logs');
const UI = path.join(OUT, 'ui');
fs.mkdirSync(LOG, { recursive: true });
fs.mkdirSync(UI, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function setup(browser) {
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.setDefaultTimeout(60000);
  const seen = [];
  await page.setRequestInterception(true);
  page.on('request', (req) => {
    const url = req.url();
    const headers = Object.assign({}, req.headers(), {
      'x-role': 'owner',
      'X-Role': 'owner',
      'x-tenant-id': TENANT,
      'X-Tenant-Id': TENANT,
    });
    if (url.includes('/api/')) {
      seen.push({ url, tenant: headers['x-tenant-id'] || headers['X-Tenant-Id'], role: headers['x-role'] || headers['X-Role'] });
    }
    req.continue({ headers }).catch(() => {});
  });
  page.__seen = seen;

  await page.goto(BASE + '/?cb=' + Date.now(), { waitUntil: 'networkidle0' });
  await page.evaluate((tenant) => {
    localStorage.setItem('demo_role', 'owner');
    localStorage.setItem('demo_tenant_id', tenant);
    localStorage.setItem('demo_tenant', tenant);
  }, TENANT);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(500);
  return page;
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const report = { pass: true, base: BASE, tenant: TENANT, checks: [], network: [] };
  const add = (name, ok, detail) => {
    report.checks.push({ name, pass: !!ok, detail });
    if (!ok) report.pass = false;
    console.log((ok ? 'PASS' : 'FAIL'), name, JSON.stringify(detail).slice(0, 320));
  };

  const page = await setup(browser);
  await page.evaluate(() => {
    const btn = document.querySelector('[data-target="tab-dishes"]');
    if (btn) btn.click();
  });
  await sleep(300);
  await page.evaluate(() => {
    if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-bom');
    if (typeof loadDishesList === 'function') loadDishesList();
  });
  for (let i = 0; i < 24; i++) {
    const n = await page.evaluate(() => (Array.isArray(dishLibraryCache) ? dishLibraryCache.length : -1));
    if (n > 0) break;
    await sleep(250);
  }
  report.network = page.__seen.filter(x => x.url.includes('/api/dishes')).slice(-6);

  const listState = await page.evaluate(() => {
    const grid = document.getElementById('dishCardGrid');
    const table = document.getElementById('dishTableView');
    const text = ((grid && grid.innerText) || '') + '\n' + ((table && table.innerText) || '');
    const html = ((grid && grid.innerHTML) || '') + ((table && table.innerHTML) || '');
    return {
      cacheLen: Array.isArray(dishLibraryCache) ? dishLibraryCache.length : -1,
      estimatedNames: (dishLibraryCache || []).filter(d => d.theory_cost_status === 'estimated').map(d => d.name),
      completeNames: (dishLibraryCache || []).filter(d => d.theory_cost_status === 'complete').map(d => ({
        name: d.name, margin: d.gross_margin_rate, cost: d.theoretical_cost,
      })),
      hasUnknown: /未知\/估算/.test(text) || /未知\/估算/.test(html),
      textSample: text.slice(0, 900),
    };
  });
  await page.screenshot({ path: path.join(UI, 'bom_list.png'), fullPage: true });
  add('ui_list_loaded_with_tenant', listState.cacheLen > 0, { cacheLen: listState.cacheLen, network: report.network, estimatedNames: listState.estimatedNames, completeNames: listState.completeNames });
  add('ui_list_has_estimated_marker', listState.hasUnknown && listState.estimatedNames.length > 0, {
    hasUnknown: listState.hasUnknown, estimatedNames: listState.estimatedNames, textSample: listState.textSample,
  });

  const badge = await page.evaluate(() => {
    const est = (dishLibraryCache || []).find(d => d.theory_cost_status === 'estimated');
    const complete = (dishLibraryCache || []).find(d => d.theory_cost_status === 'complete' && d.theoretical_cost === 10);
    return {
      badgeSample: est && dishTheoryMarginBadgeHtml(est, true),
      costSample: est && dishTheoryCostDisplay(est, true),
      completeBadge: complete && dishTheoryMarginBadgeHtml(complete, true),
      completeCost: complete && dishTheoryCostDisplay(complete, true),
    };
  });
  add('ui_estimated_badge_not_certain_100',
    !!(badge.badgeSample && /未知\/估算/.test(badge.badgeSample) && !/100\.0%/.test(badge.badgeSample)),
    badge);
  add('ui_purchased_badge_keeps_margin',
    !!(badge.completeBadge && /90\.0%/.test(badge.completeBadge) && !/未知\/估算/.test(badge.completeBadge)),
    badge);

  const extreme = await page.evaluate(() =>
    (dishLibraryCache || []).find(d => d.theory_cost_status === 'estimated'
      && Number((d.ingredients || [{}])[0].consumption_qty) === 999999999999));
  if (extreme) {
    await page.evaluate((id) => openDishModal(id), extreme.id);
    await sleep(900);
    await page.evaluate(() => { if (typeof calcDishModalTheoryCost === 'function') calcDishModalTheoryCost(); });
    await sleep(200);
    const modalState = await page.evaluate(() => ({
      cost: (document.getElementById('dishModalTheoryCost') || {}).innerText || '',
      profit: (document.getElementById('dishModalTheoryProfit') || {}).innerText || '',
      margin: (document.getElementById('dishModalTheoryMargin') || {}).innerText || '',
      qty: (() => {
        const modal = document.getElementById('dishEditModal');
        const inputs = modal ? Array.from(modal.querySelectorAll('input')) : [];
        const hit = inputs.find(i => /9{9,}/.test(String(i.value || '')));
        return hit ? hit.value : null;
      })(),
    }));
    await page.screenshot({ path: path.join(UI, 'bom_modal_estimated.png') });
    add('ui_modal_estimated_markers',
      /未知|估算/.test(modalState.cost + modalState.margin)
        && !/^100\.0%$/.test(String(modalState.margin).trim())
        && String(modalState.qty) === '999999999999',
      modalState);

    for (const bad of ['NaN', 'Infinity', '-1', '0']) {
      const r = await page.evaluate((badVal) => {
        const modal = document.getElementById('dishEditModal');
        const inputs = Array.from(modal.querySelectorAll('input'));
        let target = inputs.find(i => /9{9,}/.test(String(i.value || '')))
          || inputs.find(i => i.type === 'number');
        if (!target) return { ok: false, reason: 'no qty input', inputs: inputs.map(i => ({ id: i.id, type: i.type, value: i.value, name: i.name, cls: i.className })).slice(0, 20) };
        const prev = target.value;
        target.value = badVal;
        target.dispatchEvent(new Event('input', { bubbles: true }));
        let toastMsg = '';
        const orig = window.showToast;
        window.showToast = (msg) => { toastMsg = String(msg || ''); if (orig) orig.apply(this, arguments); };
        const errEl = document.getElementById('dishModalError');
        if (errEl) errEl.textContent = '';
        try { saveDishModal(); } catch (e) { toastMsg += String(e); }
        window.showToast = orig;
        const errText = errEl ? (errEl.innerText || '') : '';
        const combined = toastMsg + ' ' + errText;
        target.value = prev;
        return { ok: /NaN|Infinity|大于 0|有限|不可为|必须/.test(combined), toastMsg, errText, badVal };
      }, bad);
      add('ui_client_reject_' + bad, r.ok, r);
      await page.evaluate((id) => openDishModal(id), extreme.id);
      await sleep(350);
    }
  } else {
    add('ui_modal_estimated_markers', false, { reason: 'no extreme' });
  }

  const purch = await page.evaluate(() =>
    (dishLibraryCache || []).find(d => d.theory_cost_status === 'complete' && d.theoretical_cost === 10));
  if (purch) {
    await page.evaluate((id) => openDishModal(id), purch.id);
    await sleep(800);
    await page.evaluate(() => { if (typeof calcDishModalTheoryCost === 'function') calcDishModalTheoryCost(); });
    await sleep(200);
    const m = await page.evaluate(() => ({
      cost: (document.getElementById('dishModalTheoryCost') || {}).innerText || '',
      margin: (document.getElementById('dishModalTheoryMargin') || {}).innerText || '',
      profit: (document.getElementById('dishModalTheoryProfit') || {}).innerText || '',
    }));
    await page.screenshot({ path: path.join(UI, 'bom_modal_purchased.png') });
    add('ui_modal_purchased_keeps_cost',
      /90\.0%/.test(m.margin) && !/未知\/估算/.test(m.margin) && /10/.test(m.cost.replace(/,/g, '')),
      m);
  } else {
    add('ui_modal_purchased_keeps_cost', false, { reason: 'no purchased' });
  }

  fs.writeFileSync(path.join(LOG, 'uat_ui_bom.json'), JSON.stringify(report, null, 2));
  console.log('SUMMARY', JSON.stringify({ pass: report.pass, n: report.checks.length, failed: report.checks.filter(c => !c.pass).length }));
  await browser.close();
  process.exit(report.pass ? 0 : 1);
}

main().catch((e) => { console.error(e); process.exit(2); });
