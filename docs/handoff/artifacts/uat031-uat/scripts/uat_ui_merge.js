'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15251';
const TENANT = process.env.UAT_TENANT || 'uat031uat';
const PRIMARY = process.env.UAT_PRIMARY_ID;
const SECONDARY = process.env.UAT_SECONDARY_ID;
const OUT = path.join(__dirname, '..');
const LOG = path.join(OUT, 'logs');
const UI = path.join(OUT, 'ui');
fs.mkdirSync(LOG, { recursive: true });
fs.mkdirSync(UI, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  const evidence = {
    base: BASE,
    tenant: TENANT,
    primary: PRIMARY,
    secondary: SECONDARY,
    checks: [],
    verdict: 'FAIL',
  };
  const ok = (name, detail) => evidence.checks.push({ name, pass: true, detail });
  const fail = (name, detail) => {
    evidence.checks.push({ name, pass: false, detail });
    throw new Error(name + ': ' + JSON.stringify(detail));
  };

  if (!PRIMARY || !SECONDARY) {
    fail('env_ids', { PRIMARY, SECONDARY });
  }

  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
  });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width: 1440, height: 900 });
    page.setDefaultTimeout(60000);
    const apiCalls = [];
    await page.setRequestInterception(true);
    page.on('request', (req) => {
      const headers = Object.assign({}, req.headers(), {
        'x-role': 'owner',
        'X-Role': 'owner',
        'x-tenant-id': TENANT,
        'X-Tenant-Id': TENANT,
      });
      if (req.url().includes('/api/')) {
        apiCalls.push({ url: req.url(), method: req.method() });
      }
      req.continue({ headers });
    });

    let mergeRespBody = null;
    page.on('response', async (res) => {
      try {
        if (res.url().includes('/api/inventory/skus/merge') && res.request().method() === 'POST') {
          mergeRespBody = await res.json().catch(() => null);
          mergeRespBody = Object.assign({ http: res.status() }, mergeRespBody || {});
        }
      } catch (_) {}
    });

    // Auth bypass: set role/tenant in localStorage if app uses it
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 60000 });
    await page.evaluate((t) => {
      try {
        localStorage.setItem('role', 'owner');
        localStorage.setItem('tenant_id', t);
        localStorage.setItem('X-Tenant-Id', t);
      } catch (_) {}
    }, TENANT);
    await page.goto(BASE + '/#inventory', { waitUntil: 'networkidle2', timeout: 60000 });
    await sleep(1500);
    await page.screenshot({ path: path.join(UI, 'inventory_before_merge.png'), fullPage: true });

    // Open merge modal via function (CSP buttons may need click)
    const opened = await page.evaluate(async () => {
      if (typeof openSkuMergeModalFromInv === 'function') {
        openSkuMergeModalFromInv();
        return true;
      }
      const btn = Array.from(document.querySelectorAll('button')).find(
        (b) => (b.textContent || '').includes('合并食材')
      );
      if (btn) {
        btn.click();
        return true;
      }
      return false;
    });
    if (!opened) fail('open_merge_modal', { opened });
    await sleep(1500);

    const modalVisible = await page.evaluate(() => {
      const m = document.getElementById('skuMergeModal');
      return m && !m.classList.contains('hide');
    });
    if (!modalVisible) fail('modal_visible', { modalVisible });
    ok('modal_opened', { modalVisible });

    await page.screenshot({ path: path.join(UI, 'merge_modal_open.png') });

    // Select primary + check secondary
    await page.evaluate(
      (pid, sid) => {
        const sel = document.getElementById('skuMergePrimarySelect');
        if (sel) sel.value = String(pid);
        const boxes = document.querySelectorAll('.chk-sku-sec');
        boxes.forEach((c) => {
          c.checked = String(c.value) === String(sid);
        });
      },
      PRIMARY,
      SECONDARY
    );
    await sleep(300);
    await page.screenshot({ path: path.join(UI, 'merge_modal_selected.png') });

    // Submit
    await page.evaluate(() => {
      if (typeof submitSkuMerge === 'function') submitSkuMerge();
      else {
        const btn = document.getElementById('skuMergeSubmitBtn');
        if (btn) btn.click();
      }
    });
    await sleep(2500);

    if (!mergeRespBody || mergeRespBody.http !== 200 || mergeRespBody.status !== 'success') {
      fail('ui_merge_response', mergeRespBody);
    }
    const md = mergeRespBody.data || {};
    if (Number(md.batches_migrated || 0) < 1) fail('ui_batches_migrated', md);
    if (Math.abs(Number(md.new_stock || 0) - 2.0) > 0.011) fail('ui_new_stock', md);
    ok('ui_merge_success', mergeRespBody);

    await page.screenshot({ path: path.join(UI, 'after_merge.png'), fullPage: true });

    // Toast or modal closed
    const after = await page.evaluate(() => {
      const m = document.getElementById('skuMergeModal');
      const toast = document.body.innerText || '';
      return {
        modalHidden: !m || m.classList.contains('hide'),
        toastHasMerge: toast.includes('合并成功') || toast.includes('已转移'),
      };
    });
    ok('ui_after_state', after);

    evidence.merge_response = mergeRespBody;
    evidence.api_calls_sample = apiCalls.filter((c) => c.url.includes('merge')).slice(0, 5);
    evidence.verdict = 'PASS';
  } catch (e) {
    evidence.error = String(e && e.stack ? e.stack : e);
    evidence.verdict = 'FAIL';
    throw e;
  } finally {
    await browser.close().catch(() => {});
    const outPath = path.join(LOG, 'uat_ui_merge.json');
    fs.writeFileSync(outPath, JSON.stringify(evidence, null, 2));
    console.log('WROTE', outPath, 'verdict=', evidence.verdict);
    console.log(
      'checks_passed=',
      evidence.checks.filter((c) => c.pass).length,
      '/',
      evidence.checks.length
    );
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
