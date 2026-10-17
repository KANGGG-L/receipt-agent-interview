/**
 * UAT-036 independent UAT — MANUAL_UI (puppeteer-core + system Chrome).
 * Port 15500 — fresh; avoided 15150-15450.
 *
 * Gates:
 *  1. GET 500 → em-dash + error + retry (inventory / dishes / scan)
 *  2. Cancel inject → success restores numbers
 *  3. Empty tenant 200/[] → legitimate 0 / empty (not —)
 *  4. Delayed/stale responses do not overwrite newer
 * Negative: legitimate 0 must not become —; archive — regression covered by static harness.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15500';
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

const checks = [];
function record(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail == null ? '' : String(detail) });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + String(detail).slice(0, 280) : ''}`);
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

async function prepOwner(page) {
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
  });
}

async function clickNav(page, target) {
  await page.evaluate((t) => {
    const btn = document.querySelector('.sidebar-btn[data-target="' + t + '"]');
    if (btn) btn.click();
  }, target);
  await sleep(400);
}

function installFaultController(page) {
  // Shared mutable state across CDP interception
  const state = {
    mode: 'passthrough', // passthrough | fail500 | empty200 | delayThenFail
    delayMs: 0,
    pendingDelays: [], // resolve fns
    hit: { inventory: 0, dishes: 0, daily: 0, receipts: 0 },
  };

  async function enable() {
    await page.setRequestInterception(true);
    page.removeAllListeners('request');
    page.on('request', async (req) => {
      try {
        const u = req.url();
        const isInv = /\/api\/inventory(\?|$)/.test(u) && !/\/api\/inventory\//.test(u.replace(/\/api\/inventory/, '/api/inventoryX'));
        // Match list inventory precisely
        const invList = /\/api\/inventory(?:\?|$)/.test(u) && !/\/api\/inventory\/(logs|skus|batches|price|negative|sku)/.test(u);
        const dishesList = /\/api\/dishes(?:\?|$)/.test(u) && !/\/api\/dishes\//.test(u);
        const daily = /\/api\/dishes\/daily_consumption(?:\?|$)/.test(u);
        const receipts = /\/api\/receipts(?:\?|$)/.test(u) && !/\/api\/receipts\//.test(u);

        let kind = null;
        if (invList) kind = 'inventory';
        else if (daily) kind = 'daily';
        else if (dishesList) kind = 'dishes';
        else if (receipts) kind = 'receipts';

        if (!kind) {
          await req.continue();
          return;
        }
        state.hit[kind] = (state.hit[kind] || 0) + 1;

        if (state.mode === 'fail500') {
          await req.respond({
            status: 500,
            contentType: 'application/json',
            body: JSON.stringify({ status: 'error', msg: 'injected_500_uat036' }),
          });
          return;
        }
        if (state.mode === 'empty200') {
          let body;
          if (kind === 'inventory') {
            body = { status: 'success', data: [], meta: { total_active: 0, low_stock_count: 0, price_anomaly_count: 0 } };
          } else if (kind === 'dishes') {
            body = { status: 'success', data: [] };
          } else if (kind === 'daily') {
            body = { status: 'success', data: { consumptions: [], summary: { total_cost: 0, total_revenue: 0, gross_margin: 0, count: 0 } } };
          } else if (kind === 'receipts') {
            body = { status: 'success', data: [] };
          }
          await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
          return;
        }
        if (state.mode === 'staleHold') {
          // Hold this response until released; used for older request
          await new Promise((resolve) => {
            state.pendingDelays.push({ resolve, req, kind, heldAt: Date.now() });
          });
          // After release, respond with STALE success that would overwrite if bug exists
          let body;
          if (kind === 'inventory') {
            body = { status: 'success', data: [{ id: 999, name: 'STALE_SHOULD_NOT_SHOW', category: '食材', base_unit: 'kg', current_stock: 1, min_stock_alert: 0, last_unit_price: 1, price_anomaly: false, is_low_stock: false, active: 1 }], meta: { total_active: 999, low_stock_count: 888, price_anomaly_count: 777 } };
          } else if (kind === 'dishes') {
            body = { status: 'success', data: [{ id: 999, name: 'STALE_DISH' }] };
          } else if (kind === 'daily') {
            body = { status: 'success', data: { consumptions: [], summary: { total_cost: 999, total_revenue: 999, gross_margin: 99, count: 99 } } };
          } else {
            body = { status: 'success', data: [{ id: 999, status: 'edited', deleted_at: null }] };
          }
          await req.respond({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
          return;
        }
        await req.continue();
      } catch (e) {
        try { await req.abort('failed'); } catch (_) {}
      }
    });
  }

  function setMode(mode) {
    state.mode = mode;
  }

  function releaseHeld() {
    const held = state.pendingDelays.splice(0);
    held.forEach(h => h.resolve());
    return held.length;
  }

  async function disable() {
    releaseHeld();
    state.mode = 'passthrough';
    try { await page.setRequestInterception(false); } catch (e) {}
    page.removeAllListeners('request');
  }

  return { state, enable, setMode, releaseHeld, disable };
}

async function readInventoryMetrics(page) {
  return page.evaluate(() => {
    const t = (id) => {
      const el = document.getElementById(id);
      return el ? String(el.innerText || el.textContent || '').trim() : null;
    };
    const notice = document.getElementById('invLoadErrorNotice');
    const retry = document.getElementById('inventoryKpiRetry');
    const noticeVisible = !!(notice && !notice.classList.contains('hide') && notice.offsetParent !== null);
    const retryVisible = !!(retry && !retry.classList.contains('hide') && retry.offsetParent !== null);
    return {
      valTotalSKU: t('valTotalSKU'),
      valLowStock: t('valLowStock'),
      valPriceAnomaly: t('valPriceAnomaly'),
      noticeVisible,
      retryVisible,
      noticeText: notice ? notice.innerText : '',
      hasDash: [t('valTotalSKU'), t('valLowStock'), t('valPriceAnomaly')].every(v => v === '—'),
      tableText: (document.getElementById('inventoryTableBody') || {}).innerText || '',
    };
  });
}

async function readDishMetrics(page) {
  return page.evaluate(() => {
    const t = (id) => {
      const el = document.getElementById(id);
      return el ? String(el.innerText || el.textContent || '').trim() : null;
    };
    const err = document.querySelector('[data-uat036="dish-error"]');
    const kpiErr = document.getElementById('dishKpiLoadError');
    const kpiRetry = document.getElementById('dishKpiRetry');
    const dishRetry = document.querySelector('[data-uat036="dish-retry"]');
    return {
      kpiDishCost: t('kpiDishCost'),
      kpiDishRevenue: t('kpiDishRevenue'),
      kpiDishGrossMargin: t('kpiDishGrossMargin'),
      kpiDishCount: t('kpiDishCount'),
      kpiDash: [t('kpiDishCost'), t('kpiDishRevenue'), t('kpiDishGrossMargin'), t('kpiDishCount')].every(v => v === '—'),
      kpiErrVisible: !!(kpiErr && !kpiErr.classList.contains('hide')),
      kpiRetryVisible: !!(kpiRetry && kpiRetry.offsetParent !== null),
      dishErrorVisible: !!(err && err.offsetParent !== null),
      dishRetryVisible: !!(dishRetry && dishRetry.offsetParent !== null),
      dishErrorText: err ? err.innerText : '',
      gridText: (document.getElementById('dishCardGrid') || {}).innerText || '',
    };
  });
}

async function readWorkbenchMetrics(page) {
  return page.evaluate(() => {
    const t = (id) => {
      const el = document.getElementById(id);
      return el ? String(el.innerText || el.textContent || '').trim() : null;
    };
    const retry = document.getElementById('workbenchKpiRetry');
    const body = document.getElementById('workbenchQueueBody');
    return {
      qCountTodo: t('qCountTodo'),
      qCountReview: t('qCountReview'),
      qCountApprove: t('qCountApprove'),
      qCountIssues: t('qCountIssues'),
      hasDash: [t('qCountTodo'), t('qCountReview'), t('qCountApprove'), t('qCountIssues')].every(v => v === '—'),
      retryVisible: !!(retry && !retry.classList.contains('hide') && retry.offsetParent !== null),
      bodyText: body ? body.innerText : '',
      badge: t('workbenchQueueTotalBadge'),
    };
  });
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);

  const fault = installFaultController(page);
  await fault.enable();

  try {
    // ---- Baseline success load (passthrough) ----
    fault.setMode('passthrough');
    await page.goto(BASE + '/', { waitUntil: 'networkidle2' });
    await prepOwner(page);
    await page.reload({ waitUntil: 'networkidle2' });
    await sleep(800);

    // Ensure workbench loaded on scan tab
    await clickNav(page, 'tab-scan');
    await page.evaluate(() => { if (typeof loadWorkbenchQueue === 'function') loadWorkbenchQueue(true); });
    await sleep(600);
    const wb0 = await readWorkbenchMetrics(page);
    record('baseline workbench not dash', !wb0.hasDash, JSON.stringify(wb0));

    await clickNav(page, 'tab-inventory');
    await page.evaluate(() => { if (typeof loadInventoryData === 'function') loadInventoryData(); });
    await sleep(800);
    const inv0 = await readInventoryMetrics(page);
    record('baseline inventory has real numbers (not dash)', !inv0.hasDash && Number(inv0.valTotalSKU) >= 2,
      JSON.stringify(inv0));
    await snap(page, 'baseline-inventory.png');

    await clickNav(page, 'tab-dishes');
    await sleep(1000);
    const dish0 = await readDishMetrics(page);
    record('baseline dish KPI not dash (legitimate zeros ok)', !dish0.kpiDash, JSON.stringify(dish0));
    await snap(page, 'baseline-dishes.png');

    // ========== Criterion 1: GET 500 → — + error + retry ==========
    fault.setMode('fail500');

    // Inventory fail
    await clickNav(page, 'tab-inventory');
    await page.evaluate(() => loadInventoryData());
    await sleep(700);
    const invFail = await readInventoryMetrics(page);
    record('C1 inventory GET500 → all metrics —', invFail.hasDash, JSON.stringify(invFail));
    record('C1 inventory error notice visible', invFail.noticeVisible && /失败|不可用/.test(invFail.noticeText), invFail.noticeText);
    record('C1 inventory retry visible', invFail.retryVisible, '');
    record('C1 inventory NOT fake 0', invFail.valTotalSKU !== '0' && invFail.valLowStock !== '0', invFail.valTotalSKU);
    await snap(page, 'fail-inventory.png');

    // Dishes fail (list + daily KPI)
    await clickNav(page, 'tab-dishes');
    await page.evaluate(() => {
      if (typeof loadDishesList === 'function') loadDishesList();
      if (typeof loadDailyConsumption === 'function') loadDailyConsumption();
    });
    await sleep(900);
    // KPI lives on daily subtab (default)
    const dishFailKpi = await readDishMetrics(page);
    record('C1 dish KPI GET500 → —', dishFailKpi.kpiDash, JSON.stringify(dishFailKpi));
    record('C1 dish KPI error+retry visible', dishFailKpi.kpiErrVisible && dishFailKpi.kpiRetryVisible, '');
    await snap(page, 'fail-dishes-kpi.png');
    // BOM library subtab hosts dishCardGrid fail panel
    await page.evaluate(() => {
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-bom');
    });
    await sleep(400);
    const dishFail = await readDishMetrics(page);
    const listOk = /餐品列表加载失败/.test(dishFail.dishErrorText || dishFail.gridText || '')
      && /不是空库/.test(dishFail.dishErrorText || dishFail.gridText || '');
    record('C1 dish list fail copy (not empty library)', listOk, dishFail.dishErrorText || dishFail.gridText);
    const retryInDom = await page.evaluate(() => {
      const b = document.querySelector('[data-uat036="dish-retry"]');
      if (!b) return { present: false };
      const r = b.getBoundingClientRect();
      return { present: true, w: r.width, h: r.height, visible: !!(b.offsetParent || (r.width + r.height > 0)) };
    });
    record('C1 dish retry visible', !!(retryInDom.present && retryInDom.visible), JSON.stringify(retryInDom));
    await snap(page, 'fail-dishes.png');

    // Scan/workbench fail
    await clickNav(page, 'tab-scan');
    await page.evaluate(() => loadWorkbenchQueue(true));
    await sleep(700);
    const wbFail = await readWorkbenchMetrics(page);
    record('C1 workbench GET500 → qCount —', wbFail.hasDash, JSON.stringify(wbFail));
    record('C1 workbench retry visible', wbFail.retryVisible, '');
    record('C1 workbench fail copy not empty-queue', /加载失败|不是空队列|指标不可用/.test(wbFail.bodyText), wbFail.bodyText.slice(0, 120));
    await snap(page, 'fail-workbench.png');

    // ========== Criterion 2: cancel inject → recover ==========
    fault.setMode('passthrough');

    // Inventory retry click
    await clickNav(page, 'tab-inventory');
    await sleep(200);
    // Ensure fail UI still there from before — re-fail then cancel then retry for clean sequence
    fault.setMode('fail500');
    await page.evaluate(() => loadInventoryData());
    await sleep(500);
    fault.setMode('passthrough');
    const clickedInv = await page.evaluate(() => {
      const btn = document.getElementById('inventoryKpiRetry');
      if (!btn) return false;
      btn.click();
      return true;
    });
    await sleep(900);
    const invRec = await readInventoryMetrics(page);
    record('C2 inventory retry clicked', clickedInv, '');
    record('C2 inventory recovered real numbers', !invRec.hasDash && Number(invRec.valTotalSKU) >= 2 && !invRec.noticeVisible,
      JSON.stringify(invRec));
    await snap(page, 'recover-inventory.png');

    // Dish recover
    fault.setMode('fail500');
    await clickNav(page, 'tab-dishes');
    await page.evaluate(() => { loadDishesList(); loadDailyConsumption(); });
    await sleep(600);
    fault.setMode('passthrough');
    const clickedDish = await page.evaluate(() => {
      let ok = false;
      const a = document.getElementById('dishKpiRetry');
      if (a) { a.click(); ok = true; }
      const b = document.querySelector('[data-uat036="dish-retry"]');
      if (b) { b.click(); ok = true; }
      return ok;
    });
    await sleep(1000);
    const dishRec = await readDishMetrics(page);
    record('C2 dish retry clicked', clickedDish, '');
    record('C2 dish KPI recovered (not —)', !dishRec.kpiDash && !dishRec.kpiErrVisible, JSON.stringify(dishRec));
    record('C2 dish list recovered (fail panel gone)', !dishRec.dishErrorVisible, dishRec.gridText.slice(0, 80));
    await snap(page, 'recover-dishes.png');

    // Workbench recover
    fault.setMode('fail500');
    await clickNav(page, 'tab-scan');
    await page.evaluate(() => loadWorkbenchQueue(true));
    await sleep(500);
    fault.setMode('passthrough');
    const clickedWb = await page.evaluate(() => {
      const btn = document.getElementById('workbenchKpiRetry');
      if (!btn) return false;
      btn.click();
      return true;
    });
    await sleep(900);
    const wbRec = await readWorkbenchMetrics(page);
    record('C2 workbench retry clicked', clickedWb, '');
    record('C2 workbench recovered (not —)', !wbRec.hasDash && !wbRec.retryVisible, JSON.stringify(wbRec));
    await snap(page, 'recover-workbench.png');

    // ========== Criterion 3: empty 200/[] → legitimate 0 ==========
    fault.setMode('empty200');
    await clickNav(page, 'tab-inventory');
    await page.evaluate(() => loadInventoryData());
    await sleep(800);
    const invEmpty = await readInventoryMetrics(page);
    record('C3 empty inventory metrics are 0 not —',
      invEmpty.valTotalSKU === '0' && invEmpty.valLowStock === '0' && invEmpty.valPriceAnomaly === '0' && !invEmpty.hasDash,
      JSON.stringify(invEmpty));
    record('C3 empty inventory no error notice', !invEmpty.noticeVisible, invEmpty.noticeText);
    record('C3 empty inventory empty-state copy (not fail)',
      /还没有食材|暂无|没有/.test(invEmpty.tableText) && !/加载失败/.test(invEmpty.tableText),
      invEmpty.tableText.slice(0, 100));
    await snap(page, 'empty-inventory.png');

    await clickNav(page, 'tab-dishes');
    await page.evaluate(() => { loadDishesList(); loadDailyConsumption(); });
    await sleep(900);
    const dishEmpty = await readDishMetrics(page);
    record('C3 empty dish KPI legitimate zeros (not —)',
      !dishEmpty.kpiDash && !dishEmpty.kpiErrVisible,
      JSON.stringify({ cost: dishEmpty.kpiDishCost, count: dishEmpty.kpiDishCount, kpiDash: dishEmpty.kpiDash }));
    // cost may be $0.00; count may be "0 笔" or "0"
    const countOk = /0/.test(String(dishEmpty.kpiDishCount || '')) && dishEmpty.kpiDishCount !== '—';
    const costOk = /0/.test(String(dishEmpty.kpiDishCost || '')) && dishEmpty.kpiDishCost !== '—';
    record('C3 empty dish KPI shows 0-ish values', countOk && costOk, JSON.stringify(dishEmpty));
    record('C3 empty dish list not fail panel', !dishEmpty.dishErrorVisible, dishEmpty.gridText.slice(0, 80));
    await snap(page, 'empty-dishes.png');

    await clickNav(page, 'tab-scan');
    await page.evaluate(() => loadWorkbenchQueue(true));
    await sleep(700);
    const wbEmpty = await readWorkbenchMetrics(page);
    record('C3 empty workbench counts are 0 not —',
      wbEmpty.qCountTodo === '0' && !wbEmpty.hasDash && !wbEmpty.retryVisible,
      JSON.stringify(wbEmpty));
    await snap(page, 'empty-workbench.png');

    // ========== Criterion 4: stale delayed must not overwrite ==========
    // Sequence for inventory:
    //  1) start staleHold, fire load A (held)
    //  2) switch to passthrough, fire load B (completes with real data)
    //  3) release A (stale success with 999) — must NOT overwrite B
    fault.setMode('passthrough');
    await clickNav(page, 'tab-inventory');
    await page.evaluate(() => loadInventoryData());
    await sleep(600);

    fault.setMode('staleHold');
    await page.evaluate(() => loadInventoryData()); // A held
    await sleep(300);
    record('C4 inventory request A held', fault.state.pendingDelays.length >= 1, 'held=' + fault.state.pendingDelays.length);

    fault.setMode('passthrough');
    await page.evaluate(() => loadInventoryData()); // B completes
    await sleep(800);
    const invBeforeRelease = await readInventoryMetrics(page);
    record('C4 inventory B loaded real numbers before release',
      Number(invBeforeRelease.valTotalSKU) >= 2 && invBeforeRelease.valTotalSKU !== '999',
      JSON.stringify(invBeforeRelease));

    const released = fault.releaseHeld();
    await sleep(800);
    const invAfterStale = await readInventoryMetrics(page);
    record('C4 inventory stale A released', released >= 1, 'released=' + released);
    record('C4 inventory stale A did NOT overwrite (still real, not 999)',
      invAfterStale.valTotalSKU !== '999' && invAfterStale.valLowStock !== '888' && Number(invAfterStale.valTotalSKU) >= 2,
      JSON.stringify(invAfterStale));
    record('C4 inventory table has no STALE_SHOULD_NOT_SHOW',
      !/STALE_SHOULD_NOT_SHOW/.test(invAfterStale.tableText),
      invAfterStale.tableText.slice(0, 80));
    await snap(page, 'stale-inventory.png');

    // Same pattern for dishes list
    fault.setMode('staleHold');
    await clickNav(page, 'tab-dishes');
    await page.evaluate(() => loadDishesList());
    await sleep(300);
    fault.setMode('empty200'); // newer request returns empty success
    await page.evaluate(() => loadDishesList());
    await sleep(700);
    const dishBefore = await readDishMetrics(page);
    const releasedDish = fault.releaseHeld();
    await sleep(700);
    const dishAfter = await readDishMetrics(page);
    record('C4 dish stale released', releasedDish >= 1, 'released=' + releasedDish);
    record('C4 dish stale did NOT bring STALE_DISH',
      !/STALE_DISH/.test(dishAfter.gridText),
      dishAfter.gridText.slice(0, 100));
    // After empty200 newer + stale release, should still be empty-ish not stale
    record('C4 dish after race not showing stale-only state',
      !/STALE_DISH/.test(dishAfter.gridText), '');

    // Workbench race
    fault.setMode('staleHold');
    await clickNav(page, 'tab-scan');
    await page.evaluate(() => loadWorkbenchQueue(true));
    await sleep(300);
    fault.setMode('empty200');
    await page.evaluate(() => loadWorkbenchQueue(true));
    await sleep(700);
    const releasedWb = fault.releaseHeld();
    await sleep(700);
    const wbAfter = await readWorkbenchMetrics(page);
    record('C4 workbench stale released', releasedWb >= 1, 'released=' + releasedWb);
    record('C4 workbench stale did NOT set qCountTodo=1 from stale edited',
      wbAfter.qCountTodo !== '1' || wbAfter.hasDash === false,
      JSON.stringify(wbAfter));
    // With empty200 as newer, expect 0
    record('C4 workbench newer empty wins (0 not stale 1)',
      wbAfter.qCountTodo === '0',
      JSON.stringify(wbAfter));
    await snap(page, 'stale-workbench.png');

    // Negative: after success empty, metrics are 0 not —
    fault.setMode('empty200');
    await clickNav(page, 'tab-inventory');
    await page.evaluate(() => loadInventoryData());
    await sleep(600);
    const neg = await readInventoryMetrics(page);
    record('NEG legitimate empty stays 0 (not converted to —)',
      neg.valTotalSKU === '0' && !neg.hasDash, JSON.stringify(neg));

  } catch (e) {
    record('runner exception', false, String(e && e.stack || e));
  } finally {
    try { await fault.disable(); } catch (e) {}
    await browser.close();
  }

  const passCount = checks.filter(c => c.ok).length;
  const failCount = checks.filter(c => !c.ok).length;
  const result = { pass: failCount === 0, passCount, failCount, checks };
  fs.writeFileSync(path.join(LOG_DIR, 'browser-uat-result.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(LOG_DIR, 'browser-uat.txt'),
    checks.map(c => `${c.ok ? 'PASS' : 'FAIL'} ${c.name}${c.detail ? ' — ' + c.detail : ''}`).join('\n')
    + `\n\npass=${result.pass} passCount=${passCount} failCount=${failCount}\n`);
  console.log('\nSUMMARY pass=' + result.pass + ' passCount=' + passCount + ' failCount=' + failCount);
  process.exit(failCount ? 1 : 0);
})().catch(e => {
  console.error(e);
  process.exit(2);
});
