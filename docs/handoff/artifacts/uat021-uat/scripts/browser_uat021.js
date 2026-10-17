/**
 * UAT-021 independent MANUAL_UI (puppeteer-core + system Chrome).
 * Fresh port 15700 — do NOT reuse 15150-15650 / QA 15650.
 * Staff vs owner price masking: real login form, tab clicks, painted DOM cells + cookie APIs.
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require(path.join(__dirname, 'node_modules/puppeteer-core'));

const PORT = process.env.PORT || '15700';
const BASE = `http://127.0.0.1:${PORT}`;
const OUT_DIR = path.join(__dirname, '..');
const UI = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

const FIXTURE = JSON.parse(fs.readFileSync(path.join(OUT_DIR, 'environment', 'fixture.json'), 'utf8'));
const results = { pass: 0, fail: 0, checks: [] };

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function gate(cond, label, detail) {
  const ok = !!cond;
  results.checks.push({ label, pass: ok, detail: detail == null ? null : String(detail) });
  if (ok) results.pass++; else results.fail++;
  console.log(`${ok ? 'PASS' : 'FAIL'} ${label}${detail != null ? ' | ' + detail : ''}`);
}

async function findChrome() {
  const candidates = [
    process.env.CHROME_PATH,
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
  ].filter(Boolean);
  for (const p of candidates) {
    if (fs.existsSync(p)) return p;
  }
  const cache = '/home/box/.cache/puppeteer';
  if (fs.existsSync(cache)) {
    const walk = (dir) => {
      for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, ent.name);
        if (ent.isDirectory()) { const hit = walk(full); if (hit) return hit; }
        else if (ent.name === 'chrome' || ent.name === 'google-chrome') return full;
      }
      return null;
    };
    return walk(cache);
  }
  return null;
}

async function loginForm(page, email, password) {
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForSelector('#authLoginEmail', { timeout: 20000 });
  // clear then type
  await page.click('#authLoginEmail', { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type('#authLoginEmail', email, { delay: 8 });
  await page.click('#authLoginPassword', { clickCount: 3 });
  await page.keyboard.press('Backspace');
  await page.type('#authLoginPassword', password, { delay: 8 });
  await Promise.all([
    page.waitForFunction(() => !document.getElementById('authLoginOverlay'), { timeout: 20000 }),
    page.click('#authLoginSubmit'),
  ]);
  // AuthState is a script-scope const — accessible as bare identifier, NOT window.AuthState
  await page.waitForFunction(() => {
    try {
      if (typeof AuthState !== 'undefined' && AuthState.account && AuthState.account.role) return true;
    } catch (e) {}
    return false;
  }, { timeout: 20000 });
  // belt: cookie /me must also reflect role
  await page.waitForFunction(async () => {
    try {
      const me = await (await fetch('/api/auth/me', { credentials: 'include' })).json();
      return !!(me && me.account && me.account.role);
    } catch (e) { return false; }
  }, { timeout: 10000 });
}

async function clickNav(page, target) {
  await page.evaluate((t) => {
    const btn = document.querySelector(`button.sidebar-btn[data-target="${t}"]`);
    if (btn) btn.click();
  }, target);
  await page.waitForFunction((t) => {
    const sec = document.getElementById(t);
    return sec && sec.classList.contains('active');
  }, { timeout: 10000 }, target).catch(() => {});
  await sleep(1200);
}

async function snap(page, name) {
  await page.screenshot({ path: path.join(UI, name), fullPage: false });
}

(async () => {
  const executablePath = await findChrome();
  if (!executablePath) {
    console.error('NO_CHROME');
    process.exit(2);
  }
  console.log('CHROME=' + executablePath);
  console.log('FIXTURE=' + JSON.stringify(FIXTURE));

  const browser = await puppeteer.launch({
    executablePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox', '--window-size=1440,900'],
  });

  // ========== STAFF context ==========
  const staffCtx = await browser.createBrowserContext();
  const staffPage = await staffCtx.newPage();
  await staffPage.setViewport({ width: 1440, height: 900 });

  await loginForm(staffPage, 'staff@demo.hk', 'staff123');
  const staffRole = await staffPage.evaluate(() => ({
    role: (typeof AuthState !== 'undefined' && AuthState.account) ? AuthState.account.role : null,
    email: (typeof AuthState !== 'undefined' && AuthState.account) ? AuthState.account.email : null,
    enabled: (typeof AuthState !== 'undefined') ? !!AuthState.enabled : null,
    isOwner: typeof isOwnerRoleNow === 'function' ? isOwnerRoleNow() : null,
  }));
  gate(staffRole.role === 'staff' && staffRole.enabled && staffRole.isOwner === false,
    'ui_staff_login_role', JSON.stringify(staffRole));
  await snap(staffPage, 'staff-after-login.png');

  // Navigate inventory — real sidebar click
  await clickNav(staffPage, 'tab-inventory');
  // Trigger reload if needed
  await staffPage.evaluate(() => {
    if (window.InventoryBoard && typeof InventoryBoard.load === 'function') InventoryBoard.load();
    if (typeof loadInventory === 'function') loadInventory();
  });
  await sleep(1500);
  await snap(staffPage, 'staff-inventory.png');

  const staffInvUi = await staffPage.evaluate((skuName) => {
    const rows = Array.from(document.querySelectorAll('#inventoryTable tbody tr'));
    const hit = rows.find(r => (r.textContent || '').includes(skuName)) || rows[0];
    if (!hit) return { found: false };
    const priceCell = hit.querySelector('[data-label="最新进货单价"]') || hit.querySelector('td.inv-metric-cell');
    const nameCell = hit.querySelector('[data-label="品名"], [data-label="名称"], td');
    const stockCell = hit.querySelector('[data-label="当前库存"], [data-label="库存"]');
    const unitCell = hit.querySelector('[data-label="单位"], [data-label="基础单位"]');
    const txt = (el) => el ? (el.textContent || '').trim() : null;
    return {
      found: true,
      priceText: txt(priceCell),
      rowText: (hit.textContent || '').replace(/\s+/g, ' ').slice(0, 200),
      hasDollarPrice: /\$\s*\d/.test(hit.textContent || ''),
      nameHint: txt(nameCell),
      stockHint: txt(stockCell),
      unitHint: txt(unitCell),
    };
  }, FIXTURE.sku_name);
  gate(staffInvUi.found, 'ui_staff_inv_row_found', JSON.stringify(staffInvUi));
  gate(staffInvUi.priceText === '—' || staffInvUi.priceText === '-' || staffInvUi.priceText === '—',
    'ui_staff_inv_price_cell_emdash', staffInvUi.priceText);
  gate(!staffInvUi.hasDollarPrice, 'ui_staff_inv_no_dollar_price', staffInvUi.rowText);

  // Staff cookie API from page
  const staffApis = await staffPage.evaluate(async (skuId, dishId) => {
    const get = async (p, headers) => {
      const r = await fetch(p, { credentials: 'include', headers: headers || {} });
      return { status: r.status, body: await r.json() };
    };
    const inv = await get('/api/inventory');
    const dishes = await get('/api/dishes');
    const skuDet = await get(`/api/inventory/skus/${skuId}`);
    const dishDet = await get(`/api/dishes/${dishId}`);
    const ver = await get(`/api/dishes/${dishId}/versions/1`);
    const fakeInv = await get('/api/inventory', { 'X-Role': 'owner' });
    const fakeDish = await get('/api/dishes', { 'X-Role': 'owner' });
    const fakeVer = await get(`/api/dishes/${dishId}/versions/1`, { 'X-Role': 'owner' });
    return { inv, dishes, skuDet, dishDet, ver, fakeInv, fakeDish, fakeVer };
  }, FIXTURE.sku_id, FIXTURE.dish_id);

  const ssku = (staffApis.inv.body?.data || []).find(x => x.id === FIXTURE.sku_id);
  gate(ssku && ssku.last_unit_price == null, 'browser_staff_inv_api_price_null',
    ssku && JSON.stringify({ last_unit_price: ssku.last_unit_price, stock: ssku.current_stock, unit: ssku.base_unit }));
  gate(ssku && ssku.base_unit === '斤' && ssku.current_stock != null, 'browser_staff_inv_qty_unit_visible',
    ssku && `${ssku.current_stock}${ssku.base_unit}`);

  const sdish = (staffApis.dishes.body?.data || []).find(x => x.id === FIXTURE.dish_id);
  gate(sdish && sdish.price == null && sdish.gross_profit == null && sdish.gross_margin_rate == null,
    'browser_staff_dish_api_prices_null',
    sdish && JSON.stringify({ price: sdish.price, gp: sdish.gross_profit, gm: sdish.gross_margin_rate }));

  const sd = staffApis.skuDet.body?.data;
  gate(sd && sd.prices_visible === false && sd.last_unit_price == null, 'browser_staff_sku_detail_masked');
  const dd = staffApis.dishDet.body?.data;
  gate(dd && dd.price == null, 'browser_staff_dish_detail_masked');
  const snapPrice = staffApis.ver.body?.data?.snapshot?.dish?.price;
  gate(snapPrice == null, 'browser_staff_version_price_null', String(snapPrice));

  const fakeSku = (staffApis.fakeInv.body?.data || []).find(x => x.id === FIXTURE.sku_id);
  const fakeDish = (staffApis.fakeDish.body?.data || []).find(x => x.id === FIXTURE.dish_id);
  const fakeVerPrice = staffApis.fakeVer.body?.data?.snapshot?.dish?.price;
  gate(fakeSku && fakeSku.last_unit_price == null && fakeDish && fakeDish.price == null && fakeVerPrice == null,
    'browser_fake_header_blocked',
    JSON.stringify({ sku: fakeSku && fakeSku.last_unit_price, dish: fakeDish && fakeDish.price, ver: fakeVerPrice }));

  // Navigate dishes tab — painted price/margin
  await clickNav(staffPage, 'tab-dishes');
  await staffPage.evaluate(() => {
    if (window.DishBom && typeof DishBom.load === 'function') DishBom.load();
    if (typeof loadDishes === 'function') loadDishes();
    if (typeof refreshDishList === 'function') refreshDishList();
  });
  await sleep(1800);
  await snap(staffPage, 'staff-dishes.png');

  const staffDishUi = await staffPage.evaluate((dishName) => {
    // Cards or table rows
    const all = Array.from(document.querySelectorAll('#tab-dishes *'));
    const card = all.find(el => (el.textContent || '').includes(dishName) && (
      el.classList.contains('dish-card') || el.tagName === 'TR' || el.classList.contains('dish-item')
        || (el.children && el.children.length > 2)
    ));
    // Prefer table row containing name
    const rows = Array.from(document.querySelectorAll('#tab-dishes table tbody tr'));
    const row = rows.find(r => (r.textContent || '').includes(dishName));
    const target = row || card;
    if (!target) {
      return {
        found: false,
        tabTextSample: (document.getElementById('tab-dishes')?.innerText || '').slice(0, 300),
      };
    }
    const text = (target.textContent || '').replace(/\s+/g, ' ');
    // Look for money-like display of 48
    const has48Money = /\$\s*48|48\.00|HK\$\s*48|￥\s*48/.test(text);
    const cells = Array.from(target.querySelectorAll('td, .dish-price, [data-label]'));
    const cellTexts = cells.map(c => (c.textContent || '').trim()).filter(Boolean).slice(0, 12);
    return { found: true, text: text.slice(0, 240), has48Money, cellTexts, isOwner: typeof isOwnerRoleNow === 'function' ? isOwnerRoleNow() : null };
  }, FIXTURE.dish_name);
  gate(staffDishUi.found, 'ui_staff_dish_row_found', JSON.stringify(staffDishUi).slice(0, 300));
  gate(!staffDishUi.has48Money, 'ui_staff_dish_no_painted_48', staffDishUi.text);
  gate(staffDishUi.isOwner === false, 'ui_staff_isOwner_false');

  // ========== OWNER context (fresh browser context) ==========
  const ownerCtx = await browser.createBrowserContext();
  const ownerPage = await ownerCtx.newPage();
  await ownerPage.setViewport({ width: 1440, height: 900 });
  await loginForm(ownerPage, 'boss@demo.hk', 'boss123');
  const ownerRole = await ownerPage.evaluate(() => ({
    role: (typeof AuthState !== 'undefined' && AuthState.account) ? AuthState.account.role : null,
    isOwner: typeof isOwnerRoleNow === 'function' ? isOwnerRoleNow() : null,
  }));
  gate(ownerRole.role === 'owner' && ownerRole.isOwner === true, 'ui_owner_login_role', JSON.stringify(ownerRole));
  await snap(ownerPage, 'owner-after-login.png');

  await clickNav(ownerPage, 'tab-inventory');
  await ownerPage.evaluate(() => {
    if (window.InventoryBoard && typeof InventoryBoard.load === 'function') InventoryBoard.load();
  });
  await sleep(1500);
  await snap(ownerPage, 'owner-inventory.png');

  const ownerInvUi = await ownerPage.evaluate((skuName) => {
    const rows = Array.from(document.querySelectorAll('#inventoryTable tbody tr'));
    const hit = rows.find(r => (r.textContent || '').includes(skuName));
    if (!hit) return { found: false };
    const priceCell = hit.querySelector('[data-label="最新进货单价"]');
    const priceText = priceCell ? (priceCell.textContent || '').trim() : null;
    return {
      found: true,
      priceText,
      hasDollar: /\$\s*\d/.test(priceText || ''),
      rowText: (hit.textContent || '').replace(/\s+/g, ' ').slice(0, 200),
    };
  }, FIXTURE.sku_name);
  gate(ownerInvUi.found, 'ui_owner_inv_row_found', JSON.stringify(ownerInvUi));
  // Owner should see a price glyph if API returned non-null; if still null after seed, accept '未知' but not staff emdash-only as sole owner path — dish AC is stronger
  if (FIXTURE.owner_inv_price != null) {
    gate(ownerInvUi.hasDollar || (ownerInvUi.priceText && ownerInvUi.priceText !== '—'),
      'ui_owner_inv_price_cell_visible', ownerInvUi.priceText);
  } else {
    gate(ownerInvUi.priceText !== null, 'ui_owner_inv_price_cell_present_even_if_unknown', ownerInvUi.priceText);
    console.log('INFO owner inv price seed was null — skipping dollar assert');
  }

  const ownerApis = await ownerPage.evaluate(async (skuId, dishId) => {
    const get = async (p) => {
      const r = await fetch(p, { credentials: 'include' });
      return await r.json();
    };
    return {
      inv: await get('/api/inventory'),
      dishes: await get('/api/dishes'),
      dishDet: await get(`/api/dishes/${dishId}`),
      ver: await get(`/api/dishes/${dishId}/versions/1`),
      skuDet: await get(`/api/inventory/skus/${skuId}`),
    };
  }, FIXTURE.sku_id, FIXTURE.dish_id);

  const oDish = (ownerApis.dishes.data || []).find(x => x.id === FIXTURE.dish_id);
  gate(oDish && oDish.price === 48, 'browser_owner_dish_api_price_48', oDish && String(oDish.price));
  gate(oDish && oDish.gross_profit != null, 'browser_owner_dish_api_margin',
    oDish && `gp=${oDish.gross_profit} gm=${oDish.gross_margin_rate}`);
  gate(ownerApis.dishDet?.data?.price === 48, 'browser_owner_dish_detail_price');
  gate(ownerApis.ver?.data?.snapshot?.dish?.price === 48, 'browser_owner_version_price');
  gate(ownerApis.skuDet?.data?.prices_visible === true, 'browser_owner_sku_prices_visible');

  await clickNav(ownerPage, 'tab-dishes');
  await ownerPage.evaluate(() => {
    if (window.DishBom && typeof DishBom.load === 'function') DishBom.load();
    if (typeof loadDishes === 'function') loadDishes();
  });
  await sleep(1800);
  await snap(ownerPage, 'owner-dishes.png');

  const ownerDishUi = await ownerPage.evaluate((dishName) => {
    const rows = Array.from(document.querySelectorAll('#tab-dishes table tbody tr'));
    const row = rows.find(r => (r.textContent || '').includes(dishName));
    const cards = Array.from(document.querySelectorAll('#tab-dishes .dish-card, #tab-dishes [class*="dish"]'));
    const card = cards.find(c => (c.textContent || '').includes(dishName));
    const target = row || card;
    if (!target) {
      return { found: false, sample: (document.getElementById('tab-dishes')?.innerText || '').slice(0, 300) };
    }
    const text = (target.textContent || '').replace(/\s+/g, ' ');
    const has48 = /48/.test(text);
    return { found: true, text: text.slice(0, 240), has48, isOwner: typeof isOwnerRoleNow === 'function' ? isOwnerRoleNow() : null };
  }, FIXTURE.dish_name);
  gate(ownerDishUi.found, 'ui_owner_dish_row_found', JSON.stringify(ownerDishUi).slice(0, 300));
  gate(ownerDishUi.has48 === true, 'ui_owner_dish_painted_48', ownerDishUi.text);
  gate(ownerDishUi.isOwner === true, 'ui_owner_isOwner_true');

  // Negative: inventory (the price-leak surface) must remain reachable for staff —
  // nav-hide must NOT be the only control. Dishes/archive chrome may be hidden for
  // staff (product UX); API redaction still required and already gated above.
  const staffNav = await staffPage.evaluate(() => {
    const inv = document.querySelector('button.sidebar-btn[data-target="tab-inventory"]');
    const dishes = document.querySelector('button.sidebar-btn[data-target="tab-dishes"]');
    const scan = document.querySelector('button.sidebar-btn[data-target="tab-scan"]');
    const vis = (el) => {
      if (!el) return false;
      const s = getComputedStyle(el);
      return s.display !== 'none' && s.visibility !== 'hidden';
    };
    return {
      invVisible: vis(inv),
      dishesChromeVisible: vis(dishes),
      scanVisible: vis(scan),
      // Programmatic access to dishes section still works even if chrome hidden
      dishesSectionExists: !!document.getElementById('tab-dishes'),
    };
  });
  gate(staffNav.invVisible === true, 'ui_staff_inventory_nav_visible_not_hidden_as_auth',
    JSON.stringify(staffNav));
  gate(staffNav.dishesSectionExists === true, 'ui_staff_dishes_section_exists_api_is_source_of_truth',
    JSON.stringify(staffNav));
  // Document chrome policy honestly (not a fail): dishes may be display:none for staff
  console.log('INFO staff dishes chrome visible=' + staffNav.dishesChromeVisible + ' (product UX; API still redacts)');

  results.overall = results.fail === 0;
  console.log(`=== BROWSER overall pass=${results.pass} fail=${results.fail} ===`);
  fs.writeFileSync(path.join(LOG_DIR, 'browser-uat-result.json'), JSON.stringify(results, null, 2));
  await staffCtx.close();
  await ownerCtx.close();
  await browser.close();
  process.exit(results.overall ? 0 : 1);
})().catch(e => { console.error(e); process.exit(2); });
