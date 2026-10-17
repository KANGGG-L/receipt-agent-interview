const fs = require('fs');
const path = require('path');
const puppeteer = require(path.join(process.env.NODE_DIR, 'node_modules/puppeteer-core'));

const PORT = process.env.PORT || '15650';
const BASE = `http://127.0.0.1:${PORT}`;
const UI = process.env.UI_DIR;
const results = { pass: 0, fail: 0, checks: [] };

function gate(cond, label, detail) {
  const ok = !!cond;
  results.checks.push({ label, pass: ok, detail });
  if (ok) results.pass++; else results.fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${detail ? ' | ' + detail : ''}`);
}

async function login(page, email, password) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 60000 });
  // Wait for auth probe
  await page.waitForFunction(() => window.AuthState && typeof window.AuthState.enabled !== 'undefined', { timeout: 15000 }).catch(() => {});
  const enabled = await page.evaluate(() => !!(window.AuthState && window.AuthState.enabled));
  if (!enabled) {
    // force show login if needed
  }
  // Fill login form if present
  const hasLogin = await page.$('#authEmail, input[name=email], #loginEmail');
  // Try common selectors from this app
  const filled = await page.evaluate((email, password) => {
    const emailEl = document.querySelector('#authEmail') || document.querySelector('input[type=email]');
    const passEl = document.querySelector('#authPassword') || document.querySelector('input[type=password]');
    const btn = document.querySelector('#authLoginBtn') || document.querySelector('button[type=submit]');
    if (!emailEl || !passEl) return { ok: false, reason: 'no_form' };
    emailEl.value = email;
    passEl.value = password;
    emailEl.dispatchEvent(new Event('input', { bubbles: true }));
    passEl.dispatchEvent(new Event('input', { bubbles: true }));
    if (btn) btn.click();
    return { ok: true };
  }, email, password);
  if (!filled.ok) {
    // API login then set cookie via page
    const resp = await page.evaluate(async (email, password) => {
      const r = await fetch('/api/auth/login', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
        body: JSON.stringify({ email, password }), credentials: 'include'
      });
      return { status: r.status, body: await r.json() };
    }, email, password);
    await page.reload({ waitUntil: 'networkidle2' });
    return resp;
  }
  await page.waitForFunction(() => window.AuthState && window.AuthState.account, { timeout: 15000 }).catch(() => {});
  return filled;
}

async function captureApis(page) {
  const out = {};
  page.on('response', async (res) => {
    try {
      const url = res.url();
      if (!url.includes('/api/')) return;
      const u = new URL(url);
      const p = u.pathname;
      if (['/api/inventory', '/api/dishes'].includes(p) || /\/api\/inventory\/skus\/\d+$/.test(p) || /\/api\/dishes\/\d+$/.test(p) || /\/api\/dishes\/\d+\/versions\/\d+$/.test(p)) {
        const body = await res.json().catch(() => null);
        out[p] = { status: res.status(), body };
      }
    } catch (_) {}
  });
  return out;
}

(async () => {
  const chromePaths = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/home/box/.cache/puppeteer/chrome'
  ].filter(Boolean);
  let executablePath = null;
  for (const p of chromePaths) {
    if (fs.existsSync(p)) { executablePath = p; break; }
  }
  // find chrome under puppeteer cache
  if (!executablePath) {
    const cache = '/home/box/.cache/puppeteer';
    if (fs.existsSync(cache)) {
      const walk = (dir) => {
        for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
          const full = path.join(dir, ent.name);
          if (ent.isDirectory()) {
            const hit = walk(full); if (hit) return hit;
          } else if (ent.name === 'chrome' || ent.name === 'google-chrome') return full;
        }
        return null;
      };
      executablePath = walk(cache);
    }
  }
  if (!executablePath) {
    console.log('NO_CHROME — skip browser MANUAL_UI (API cookie path already verified)');
    results.skipped = true;
    fs.writeFileSync(path.join(UI, '..', 'logs', 'browser-qa-result.json'), JSON.stringify(results, null, 2));
    process.exit(0);
  }
  console.log('CHROME=' + executablePath);
  const browser = await puppeteer.launch({
    executablePath, headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,900']
  });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  const captured = await captureApis(page);

  // Staff login via fetch cookie
  await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
  const staffLogin = await page.evaluate(async () => {
    const r = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      credentials: 'include',
      body: JSON.stringify({ email: 'staff@demo.hk', password: 'staff123' })
    });
    return { status: r.status, body: await r.json() };
  });
  gate(staffLogin.status === 200 && staffLogin.body?.account?.role === 'staff', 'browser_staff_login');
  await page.reload({ waitUntil: 'networkidle2' });

  // Navigate to inventory tab if present
  await page.evaluate(() => {
    const tab = document.querySelector('#tab-inventory, [data-tab=inventory], a[href="#inventory"]');
    if (tab) tab.click();
    if (typeof loadInventory === 'function') loadInventory();
    if (window.InventoryBoard && InventoryBoard.load) InventoryBoard.load();
  });
  await new Promise(r => setTimeout(r, 1500));

  // Direct API from page context (staff cookie)
  const staffApis = await page.evaluate(async () => {
    const get = async (p) => {
      const r = await fetch(p, { credentials: 'include' });
      return { status: r.status, body: await r.json() };
    };
    const inv = await get('/api/inventory');
    const dishes = await get('/api/dishes');
    const sku = inv.body?.data?.[0];
    const dish = dishes.body?.data?.[0];
    const skuDet = sku ? await get(`/api/inventory/skus/${sku.id}`) : null;
    const dishDet = dish ? await get(`/api/dishes/${dish.id}`) : null;
    const ver = dish ? await get(`/api/dishes/${dish.id}/versions/1`) : null;
    const fake = await get('/api/dishes'); // will re-fetch with header below
    return { inv, dishes, skuDet, dishDet, ver, skuId: sku?.id, dishId: dish?.id };
  });

  const skuRow = (staffApis.inv.body?.data || []).find(x => x.name && x.name.includes('QA021')) || staffApis.inv.body?.data?.[0];
  gate(skuRow && skuRow.last_unit_price == null, 'browser_staff_inv_price_null', JSON.stringify(skuRow && { id: skuRow.id, last_unit_price: skuRow.last_unit_price, stock: skuRow.current_stock, unit: skuRow.base_unit }));
  gate(skuRow && skuRow.base_unit, 'browser_staff_inv_unit_visible', skuRow && skuRow.base_unit);
  gate(skuRow && skuRow.current_stock != null, 'browser_staff_inv_qty_visible', skuRow && String(skuRow.current_stock));

  const dishRow = (staffApis.dishes.body?.data || []).find(x => x.name && x.name.includes('QA021')) || staffApis.dishes.body?.data?.[0];
  gate(dishRow && dishRow.price == null && dishRow.gross_profit == null && dishRow.gross_margin_rate == null,
    'browser_staff_dish_prices_null', dishRow && JSON.stringify({ price: dishRow.price, gp: dishRow.gross_profit, gm: dishRow.gross_margin_rate }));

  const sd = staffApis.skuDet?.body?.data;
  gate(sd && sd.prices_visible === false && sd.last_unit_price == null, 'browser_staff_sku_detail_masked');

  const dd = staffApis.dishDet?.body?.data;
  gate(dd && dd.price == null, 'browser_staff_dish_detail_masked');

  const snapPrice = staffApis.ver?.body?.data?.snapshot?.dish?.price;
  gate(snapPrice == null, 'browser_staff_version_price_null', String(snapPrice));

  // Fake header from page
  const fake = await page.evaluate(async (dishId, skuId) => {
    const get = async (p) => {
      const r = await fetch(p, { credentials: 'include', headers: { 'X-Role': 'owner' } });
      return await r.json();
    };
    const inv = await get('/api/inventory');
    const dishes = await get('/api/dishes');
    const ver = await get(`/api/dishes/${dishId}/versions/1`);
    const sku = (inv.data || []).find(x => x.id === skuId);
    const dish = (dishes.data || []).find(x => x.id === dishId);
    return {
      sku_price: sku && sku.last_unit_price,
      dish_price: dish && dish.price,
      ver_price: ver?.data?.snapshot?.dish?.price
    };
  }, staffApis.dishId, staffApis.skuId);
  gate(fake.sku_price == null && fake.dish_price == null && fake.ver_price == null, 'browser_fake_header_blocked', JSON.stringify(fake));

  // UI text: inventory table should not show $15 style for staff price cell if present
  await page.screenshot({ path: path.join(UI, 'staff-inventory.png'), fullPage: true });

  // Switch to owner
  await page.evaluate(async () => {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'include', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
  });
  await page.evaluate(async () => {
    await fetch('/api/auth/login', {
      method: 'POST', credentials: 'include',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
      body: JSON.stringify({ email: 'boss@demo.hk', password: 'boss123' })
    });
  });
  await page.reload({ waitUntil: 'networkidle2' });
  const ownerApis = await page.evaluate(async () => {
    const inv = await (await fetch('/api/inventory', { credentials: 'include' })).json();
    const dishes = await (await fetch('/api/dishes', { credentials: 'include' })).json();
    return { inv, dishes };
  });
  const oDish = (ownerApis.dishes.data || []).find(x => x.name && x.name.includes('QA021'));
  gate(oDish && oDish.price === 48, 'browser_owner_dish_price_visible', oDish && String(oDish.price));
  await page.screenshot({ path: path.join(UI, 'owner-inventory.png'), fullPage: true });

  results.overall = results.fail === 0;
  console.log(`=== BROWSER overall pass=${results.pass} fail=${results.fail} ===`);
  fs.writeFileSync(path.join(UI, '..', 'logs', 'browser-qa-result.json'), JSON.stringify(results, null, 2));
  await browser.close();
  process.exit(results.overall ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
