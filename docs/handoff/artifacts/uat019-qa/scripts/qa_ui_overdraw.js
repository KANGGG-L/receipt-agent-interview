'use strict';
/**
 * UAT019 independent QA — browser dish-consume overdraw prompt (port 15255).
 * Verifies: ordinary huge submit → 409 + uiPrompt; cancel → no write; confirm force → negative as contract.
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.QA_BASE || 'http://127.0.0.1:15255';
const TENANT = process.env.QA_TENANT || 'uat019qaui';
const OUT = path.join(__dirname, '..');
const LOG = path.join(OUT, 'logs');
const UI = path.join(OUT, 'ui');
fs.mkdirSync(LOG, { recursive: true });
fs.mkdirSync(UI, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const checks = [];
function add(name, passed, detail) {
  checks.push({ name, pass: !!passed, detail });
  console.log((passed ? 'PASS' : 'FAIL'), name, JSON.stringify(detail).slice(0, 240));
}

async function api(method, p, body) {
  const res = await fetch(BASE + p, {
    method,
    headers: {
      'Content-Type': 'application/json',
      'X-Role': 'owner',
      'X-Email': 'qa-ui@uat019.hk',
      'X-Tenant-Id': TENANT,
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let j;
  try { j = JSON.parse(text); } catch (e) { j = { _raw: text }; }
  return { status: res.status, body: j };
}

async function seedViaApi() {
  // Create SKU + dish for UI tenant; stock seeded by calling stocktake after create
  const skuRes = await api('POST', '/api/inventory/skus', {
    name: 'QA019UI料', category: '测试', base_unit: 'kg',
  });
  const skuId = skuRes.body.id;
  if (!skuId) throw new Error('sku create failed ' + JSON.stringify(skuRes));
  // stocktake to 1.0 (creates inbound batch when from 0)
  const st = await api('POST', `/api/inventory/${skuId}/stocktake`, {
    actual_qty: 1.0, note: 'QA019 UI seed',
  });
  const dishRes = await api('POST', '/api/dishes', {
    name: 'QA019UI菜',
    price: 30,
    category: '测试',
    ingredients: [{ sku_id: skuId, consumption_qty: 0.5, unit: 'kg' }],
  });
  const dishId = (dishRes.body.data || {}).id;
  const stockGet = await api('GET', `/api/inventory/skus/${skuId}`);
  const stock = (stockGet.body.data || stockGet.body).current_stock
    ?? stockGet.body.current_stock;
  return { skuId, dishId, stock, stocktake: st.status, dishStatus: dishRes.status };
}

async function injectTenant(page) {
  await page.evaluate((tenant) => {
    if (typeof window.apiFetch === 'function') {
      const orig = window.apiFetch;
      window.apiFetch = function (url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {
          'X-Role': 'owner',
          'X-Tenant-Id': tenant,
        });
        return orig.call(this, url, opts);
      };
    }
  }, TENANT);
}

async function openConsumeTab(page) {
  await page.click('button.sidebar-btn[data-target="tab-dishes"]');
  await sleep(600);
  // daily subtab should be default active
  const daily = await page.$('#subtab-dish-daily');
  if (!daily) throw new Error('daily subtab missing');
  await page.evaluate(() => {
    if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
  });
  await sleep(800);
}

(async () => {
  const seed = await seedViaApi();
  add('ui_seed', !!seed.skuId && !!seed.dishId && Number(seed.stock) === 1, seed);

  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  page.setDefaultTimeout(90000);
  await page.setExtraHTTPHeaders({ 'X-Role': 'owner', 'X-Tenant-Id': TENANT });
  await page.goto(BASE + '/?cb=' + Date.now(), { waitUntil: 'networkidle0' });
  await page.evaluate((tenant) => {
    localStorage.setItem('demo_role', 'owner');
    localStorage.setItem('demo_tenant', tenant);
  }, TENANT);
  await injectTenant(page);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(700);
  await injectTenant(page);

  // Static JS contract presence
  const jsCheck = await page.evaluate(async () => {
    const r = await fetch('/static/js/dish_consumption.js?v=20261017_uat019');
    const t = await r.text();
    return {
      status: r.status,
      hasAsk: t.includes('askDishConsumeOverdrawReason'),
      hasForce: t.includes("allow_negative: true") || t.includes('allow_negative:true'),
      has409: t.includes('INSUFFICIENT_STOCK'),
    };
  });
  add('ui_js_contract', jsCheck.status === 200 && jsCheck.hasAsk && jsCheck.hasForce && jsCheck.has409, jsCheck);

  await openConsumeTab(page);
  await page.screenshot({ path: path.join(UI, 'consume_tab.png'), fullPage: true });

  // Find qty input for our dish
  const found = await page.evaluate((dishId) => {
    const rows = Array.from(document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row'));
    const row = rows.find(tr => String(tr.getAttribute('data-dish-id')) === String(dishId));
    if (!row) {
      return { ok: false, n: rows.length, ids: rows.map(r => r.getAttribute('data-dish-id')) };
    }
    const input = row.querySelector('input.dish-consume-qty-input, input[id^="dishConsumeQty_"]');
    if (!input) return { ok: false, reason: 'no input' };
    input.value = '999999999999';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    return { ok: true, id: input.id, value: input.value };
  }, seed.dishId);
  add('ui_fill_huge_qty', found.ok, found);
  if (!found.ok) {
    await page.screenshot({ path: path.join(UI, 'fill_fail.png'), fullPage: true });
    throw new Error('could not fill qty');
  }

  // Capture batch responses
  const batchResps = [];
  page.on('response', async (res) => {
    try {
      if (res.url().includes('/api/dishes/daily_consumption/batch')) {
        const status = res.status();
        let body = null;
        try { body = await res.json(); } catch (e) { body = { _err: String(e) }; }
        batchResps.push({ status, body, url: res.url() });
      }
    } catch (e) { /* ignore */ }
  });

  // Click submit → expect 409 + prompt
  await page.click('#btnSubmitDailyConsumeBatch');
  await sleep(1500);
  await page.screenshot({ path: path.join(UI, 'overdraw_prompt.png'), fullPage: true });

  const promptVisible = await page.evaluate(() => {
    const dlg = document.querySelector('.ui-dlg');
    if (!dlg) return { visible: false };
    const title = (dlg.querySelector('h3') || {}).textContent || '';
    const msg = (dlg.querySelector('.ui-dlg-msg') || {}).textContent || '';
    const cancel = Array.from(dlg.querySelectorAll('button')).find(b => /先不记|取消/.test(b.textContent || ''));
    const confirm = Array.from(dlg.querySelectorAll('button')).find(b => /确认|继续/.test(b.textContent || ''));
    return {
      visible: true,
      title,
      msg: msg.slice(0, 200),
      hasCancel: !!cancel,
      hasConfirm: !!confirm,
    };
  });
  add('ui_prompt_on_409', promptVisible.visible && /库存不够/.test(promptVisible.title || ''), promptVisible);
  add('ui_first_batch_409', batchResps.some(r => r.status === 409 && r.body && r.body.code === 'INSUFFICIENT_STOCK'), {
    n: batchResps.length,
    statuses: batchResps.map(r => ({ status: r.status, code: (r.body || {}).code })),
  });

  // Cancel → no write
  await page.evaluate(() => {
    const dlg = document.querySelector('.ui-dlg');
    const cancel = Array.from(dlg.querySelectorAll('button')).find(b => /先不记|取消/.test(b.textContent || ''));
    if (cancel) cancel.click();
  });
  await sleep(800);

  const stockAfterCancel = await api('GET', `/api/inventory/skus/${seed.skuId}`);
  const stockC = (stockAfterCancel.body.data || stockAfterCancel.body).current_stock
    ?? stockAfterCancel.body.current_stock;
  add('ui_cancel_no_write', Number(stockC) === 1, { stock: stockC });

  // Re-fill and submit, then confirm with reason (force path)
  await page.evaluate((dishId) => {
    const rows = Array.from(document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row'));
    const row = rows.find(tr => String(tr.getAttribute('data-dish-id')) === String(dishId));
    const input = row.querySelector('input.dish-consume-qty-input, input[id^="dishConsumeQty_"]');
    input.value = '4';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }, seed.dishId);
  batchResps.length = 0;
  await page.click('#btnSubmitDailyConsumeBatch');
  await sleep(1500);
  await page.screenshot({ path: path.join(UI, 'overdraw_prompt_force.png'), fullPage: true });

  // Type reason and confirm
  await page.evaluate(() => {
    const dlg = document.querySelector('.ui-dlg');
    const input = dlg && dlg.querySelector('input');
    if (input) {
      input.value = '进货单未录入-QA019';
      input.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const confirm = Array.from((dlg || document).querySelectorAll('button')).find(b => /我确认|继续记录|确认/.test(b.textContent || ''));
    if (confirm) confirm.click();
  });
  await sleep(2000);
  await page.screenshot({ path: path.join(UI, 'after_force.png'), fullPage: true });

  const forceOk = batchResps.some(r => r.status === 200 && r.body && r.body.status === 'success');
  const force409first = batchResps.some(r => r.status === 409);
  add('ui_force_path_409_then_200', force409first && forceOk, {
    statuses: batchResps.map(r => ({ status: r.status, code: (r.body || {}).code, statusField: (r.body || {}).status })),
  });

  const stockAfterForce = await api('GET', `/api/inventory/skus/${seed.skuId}`);
  const stockF = (stockAfterForce.body.data || stockAfterForce.body).current_stock
    ?? stockAfterForce.body.current_stock;
  // 4 * 0.5 = 2 needed from 1 → -1
  add('ui_force_negative_stock', Math.abs(Number(stockF) - (-1)) < 1e-6, { stock: stockF });

  // Normal path: close any post-success modal, reset stock, reload entry, sell 1 → 0.5
  await page.evaluate(() => {
    document.querySelectorAll('.ui-dlg-mask, .modal.show, .modal[style*="display: block"], [id*="Trace"] .btn-close, .modal .btn-close').forEach(el => {
      try {
        if (el.classList && el.classList.contains('ui-dlg-mask')) el.remove();
        else if (el.click) el.click();
      } catch (e) {}
    });
    // force-hide common modals
    document.querySelectorAll('.modal').forEach(m => { m.classList.remove('show'); m.style.display = 'none'; });
    document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
  });
  await sleep(300);
  const reset = await api('POST', `/api/inventory/${seed.skuId}/stocktake`, { actual_qty: 1.0, note: 'QA019 reset' });
  const stockResetGet = await api('GET', `/api/inventory/skus/${seed.skuId}`);
  const stockReset = (stockResetGet.body.data || stockResetGet.body).current_stock ?? stockResetGet.body.current_stock;
  add('ui_stock_reset', Math.abs(Number(stockReset) - 1) < 1e-6, { stock: stockReset, resetStatus: reset.status });

  // Reload consume list so UI matches stock
  await page.evaluate(() => {
    if (typeof loadDailyConsumption === 'function') {
      const d = (document.getElementById('dishConsumptionDate') || {}).value;
      loadDailyConsumption(d || undefined);
    }
  });
  await sleep(1000);

  await page.evaluate((dishId) => {
    const rows = Array.from(document.querySelectorAll('#dishConsumeEntryTableBody tr.dish-consume-row'));
    const row = rows.find(tr => String(tr.getAttribute('data-dish-id')) === String(dishId));
    const input = row && row.querySelector('input.dish-consume-qty-input, input[id^="dishConsumeQty_"]');
    if (!input) return false;
    input.value = '1';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    const btn = document.getElementById('btnSubmitDailyConsumeBatch');
    if (btn) { btn.disabled = false; }
    return true;
  }, seed.dishId);
  batchResps.length = 0;
  await page.click('#btnSubmitDailyConsumeBatch');
  await sleep(2500);
  // If unexpected prompt, cancel
  await page.evaluate(() => {
    const dlg = document.querySelector('.ui-dlg');
    if (dlg) {
      const cancel = Array.from(dlg.querySelectorAll('button')).find(b => /先不记|取消/.test(b.textContent || ''));
      if (cancel) cancel.click();
    }
  });
  await sleep(500);
  const stockN = await api('GET', `/api/inventory/skus/${seed.skuId}`);
  const sn = (stockN.body.data || stockN.body).current_stock ?? stockN.body.current_stock;
  add('ui_normal_one_portion', batchResps.some(r => r.status === 200) && Math.abs(Number(sn) - 0.5) < 1e-6, {
    stock: sn,
    statuses: batchResps.map(r => ({ status: r.status, code: (r.body || {}).code, msg: (r.body || {}).msg })),
  });

  await browser.close();

  const passed = checks.filter(c => c.pass).length;
  const summary = { passed, total: checks.length, failed: checks.filter(c => !c.pass), checks };
  fs.writeFileSync(path.join(LOG, 'qa_ui_overdraw.json'), JSON.stringify(summary, null, 2));
  console.log(`\nSUMMARY ${passed}/${checks.length}`);
  process.exit(passed === checks.length ? 0 : 1);
})().catch((e) => {
  console.error('FATAL', e);
  fs.writeFileSync(path.join(LOG, 'qa_ui_overdraw_fatal.json'), JSON.stringify({ error: String(e), stack: e.stack, checks }, null, 2));
  process.exit(2);
});
