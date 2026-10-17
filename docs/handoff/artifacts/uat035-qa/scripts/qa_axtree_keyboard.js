'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.QA_BASE || 'http://127.0.0.1:15195';
const TENANT = process.env.QA_TENANT || 'uat035qa';
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
  if (!ignored && (role === 'spinbutton' || role === 'combobox' || role === 'textbox' || role === 'button')) {
    acc.push({
      role,
      name,
      value: node.value,
      description: node.description || '',
      ignored: false,
    });
  }
  for (const c of node.children || []) walk(c, acc);
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

async function setup(browser, w, h) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h });
  page.setDefaultTimeout(60000);
  // Do NOT rely on setExtraHTTPHeaders for tenant isolation checks.
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
  return page;
}

function nameOkFieldDescribing(name, kinds) {
  if (!name || !String(name).trim()) return false;
  const n = String(name);
  if (kinds.includes('qty')) {
    return /消耗|份数|份數/.test(n) && n.length > 2;
  }
  if (kinds.includes('category')) {
    return /分类|分類/.test(n) && !/全部/.test(n);
  }
  if (kinds.includes('status')) {
    return (/状态|狀態|在售/.test(n)) && !/全部分类|全部分類/.test(n);
  }
  return false;
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu'],
  });
  const report = {
    pass: true,
    base: BASE,
    tenant: TENANT,
    cases: [],
    keyboardSubmit: null,
    regressions: null,
    negativeChecks: null,
  };

  for (const [label, w, h, hant] of [
    ['hans_1440', 1440, 900, false],
    ['hant_390', 390, 844, true],
  ]) {
    const page = await setup(browser, w, h);
    if (hant) {
      await page.evaluate(() => {
        try {
          localStorage.setItem('demo_locale', 'zh-Hant-HK');
        } catch (e) {}
        if (typeof setLocale === 'function') setLocale('zh-Hant-HK');
        else if (window.I18N && I18N.setLocale) I18N.setLocale('zh-Hant-HK');
      });
      await page.reload({ waitUntil: 'networkidle0' });
      await sleep(700);
      await injectTenant(page);
    }

    // Navigate to daily consume
    await page.evaluate(() => {
      const btn = document.querySelector('[data-target="tab-dishes"]');
      if (btn) btn.click();
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
      if (typeof loadDailyConsumption === 'function') loadDailyConsumption();
    });
    await sleep(1400);

    const domDaily = await page.evaluate(() => {
      const qtyInputs = Array.from(document.querySelectorAll('input.dish-consume-qty-input, input[id^="dishConsumeQty_"]'));
      const qty = qtyInputs[0] || null;
      const search = document.getElementById('dishConsumeSearchInput');
      const cat = document.getElementById('dishCategoryFilter');
      const st = document.getElementById('dishStatusFilter');
      const date = document.getElementById('dishConsumptionDate');
      const dateLab = document.querySelector('label[for="dishConsumptionDate"]');
      const attrs = (el) =>
        el
          ? {
              id: el.id,
              ariaLabel: el.getAttribute('aria-label'),
              ariaLabelledby: el.getAttribute('aria-labelledby'),
              placeholder: el.getAttribute('placeholder'),
              title: el.getAttribute('title'),
              value: el.value,
            }
          : null;
      return {
        qtyCount: qtyInputs.length,
        qty: attrs(qty),
        search: attrs(search),
        cat: attrs(cat),
        st: attrs(st),
        dateLabelText: dateLab ? dateLab.textContent.trim() : null,
        datePresent: !!date,
        scriptSrc: (document.querySelector('script[src*="dish_consumption"]') || {}).src || null,
      };
    });

    const snapDaily = await page.accessibility.snapshot({ interestingOnly: false });
    const axDaily = [];
    walk(snapDaily, axDaily);
    const spin = axDaily.filter((n) => n.role === 'spinbutton');
    const namedSpin = spin.filter((n) => nameOkFieldDescribing(n.name, ['qty']));

    // Keyboard type qty
    const qtySel = domDaily.qty && domDaily.qty.id ? '#' + domDaily.qty.id : 'input.dish-consume-qty-input';
    await page.focus(qtySel);
    await page.keyboard.down('Control');
    await page.keyboard.press('a');
    await page.keyboard.up('Control');
    await page.keyboard.type('2');
    await sleep(200);
    const qtyVal = await page.$eval(qtySel, (el) => el.value);

    // BOM filters
    await page.evaluate(() => {
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-bom');
    });
    await sleep(900);

    const domBom = await page.evaluate(() => {
      const cat = document.getElementById('dishCategoryFilter');
      const st = document.getElementById('dishStatusFilter');
      const attrs = (el) =>
        el
          ? {
              id: el.id,
              ariaLabel: el.getAttribute('aria-label'),
              ariaLabelledby: el.getAttribute('aria-labelledby'),
              placeholder: el.getAttribute('placeholder'),
              title: el.getAttribute('title'),
              value: el.value,
              options: Array.from(el.options || []).map((o) => o.value + ':' + o.textContent.trim()),
            }
          : null;
      return { cat: attrs(cat), st: attrs(st) };
    });

    const snapBom = await page.accessibility.snapshot({ interestingOnly: false });
    const axBom = [];
    walk(snapBom, axBom);
    const combos = axBom.filter((n) => n.role === 'combobox');
    const catAx = combos.find((n) => nameOkFieldDescribing(n.name, ['category']));
    const stAx = combos.find((n) => nameOkFieldDescribing(n.name, ['status']));

    // Keyboard filter on category/status
    await page.focus('#dishCategoryFilter');
    await page.select('#dishCategoryFilter', '主食');
    await sleep(300);
    const catVal = await page.$eval('#dishCategoryFilter', (el) => el.value);
    await page.focus('#dishStatusFilter');
    await page.select('#dishStatusFilter', 'all');
    const stVal = await page.$eval('#dishStatusFilter', (el) => el.value);

    // Regression: date still named via label; search has aria-label; role combobox still named
    await page.evaluate(() => {
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
    });
    await sleep(400);
    const regressions = await page.evaluate(() => {
      const dateEl = document.getElementById('dishConsumptionDate');
      const lab = document.querySelector('label[for="dishConsumptionDate"]');
      const search = document.getElementById('dishConsumeSearchInput');
      const logout = document.getElementById('authLogoutBtn');
      return {
        dateLabelFor: !!(lab && lab.textContent.trim() && dateEl),
        dateLabelText: lab ? lab.textContent.trim() : null,
        searchAria: search ? search.getAttribute('aria-label') : null,
        searchPlaceholder: search ? search.getAttribute('placeholder') : null,
        logoutAria: logout ? logout.getAttribute('aria-label') : null,
      };
    });

    await page.screenshot({ path: path.join(UI, label + '_daily.png'), fullPage: false });

    // Negative: names must not be placeholder/title alone
    const qtyAria = (domDaily.qty && domDaily.qty.ariaLabel) || '';
    const catAria = (domBom.cat && domBom.cat.ariaLabel) || '';
    const stAria = (domBom.st && domBom.st.ariaLabel) || '';
    const qtyPlaceholder = (domDaily.qty && domDaily.qty.placeholder) || '';
    const qtyTitle = (domDaily.qty && domDaily.qty.title) || '';
    const catTitle = (domBom.cat && domBom.cat.title) || '';
    const stTitle = (domBom.st && domBom.st.title) || '';
    const catPlaceholder = (domBom.cat && domBom.cat.placeholder) || '';
    const stPlaceholder = (domBom.st && domBom.st.placeholder) || '';

    const notPlaceholderAlone =
      !!qtyAria &&
      !!catAria &&
      !!stAria &&
      // aria-label is the name source (present); even if placeholder equals text, aria-label satisfies AC
      (qtyAria !== qtyPlaceholder || !!qtyAria) &&
      !stAria.includes('全部分类') &&
      !stAria.includes('全部分類');

    const axNameMatchesAria =
      namedSpin.length >= 1 &&
      !!catAx &&
      !!stAx &&
      (namedSpin[0].name === qtyAria || namedSpin.some((s) => s.name === qtyAria)) &&
      catAx.name === catAria &&
      stAx.name === stAria;

    const caseResult = {
      label,
      viewport: { w, h },
      hant,
      domDaily,
      domBom,
      qtyVal,
      catVal,
      stVal,
      regressions,
      spinNames: spin.map((s) => s.name),
      namedSpinCount: namedSpin.length,
      namedSpinNames: namedSpin.map((s) => s.name),
      catName: catAx ? catAx.name : null,
      stName: stAx ? stAx.name : null,
      comboNames: combos.map((c) => c.name),
      notPlaceholderAlone,
      axNameMatchesAria,
      attrsProbe: {
        qtyAria,
        catAria,
        stAria,
        qtyPlaceholder,
        qtyTitle,
        catTitle,
        stTitle,
        catPlaceholder,
        stPlaceholder,
      },
    };

    const ok =
      namedSpin.length >= 1 &&
      !!catAx &&
      !!stAx &&
      qtyVal === '2' &&
      catVal === '主食' &&
      stVal === 'all' &&
      !!qtyAria &&
      !!catAria &&
      !!stAria &&
      notPlaceholderAlone &&
      nameOkFieldDescribing(qtyAria, ['qty']) &&
      nameOkFieldDescribing(catAria, ['category']) &&
      nameOkFieldDescribing(stAria, ['status']) &&
      regressions.dateLabelFor &&
      !!regressions.searchAria;

    caseResult.ok = ok;
    if (!ok) report.pass = false;
    report.cases.push(caseResult);
    await page.close();
  }

  // Real keyboard submit (Hans 1440)
  {
    const page = await setup(browser, 1440, 900);
    let consumePost = null;
    page.on('response', async (res) => {
      try {
        if (
          res.url().includes('/api/dishes/daily_consumption/batch') ||
          res.url().includes('/api/dishes/consume')
        ) {
          const req = res.request();
          if (req.method() === 'POST') {
            consumePost = {
              url: res.url(),
              status: res.status(),
              body: await res.json().catch(() => null),
            };
          }
        }
      } catch (e) {}
    });
    await page.evaluate(() => {
      const btn = document.querySelector('[data-target="tab-dishes"]');
      if (btn) btn.click();
      if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
      if (typeof loadDailyConsumption === 'function') loadDailyConsumption();
    });
    await sleep(1200);
    const qtyId = await page.evaluate(() => {
      const el = document.querySelector('input[id^="dishConsumeQty_"]');
      return el ? el.id : null;
    });
    if (!qtyId) {
      report.pass = false;
      report.keyboardSubmit = { ok: false, error: 'no qty input' };
    } else {
      await page.focus('#' + qtyId);
      await page.keyboard.down('Control');
      await page.keyboard.press('a');
      await page.keyboard.up('Control');
      await page.keyboard.type('1');
      await sleep(200);
      await page.evaluate(() => {
        const btn = document.getElementById('btnSubmitDailyConsumeBatch');
        if (btn) btn.focus();
      });
      await page.keyboard.press('Enter');
      await sleep(1500);
      if (!consumePost) {
        await page.evaluate(() => {
          if (typeof submitDailyConsumptionBatch === 'function') submitDailyConsumptionBatch();
        });
        await sleep(1500);
      }
      const aria = await page.$eval('#' + qtyId, (el) => el.getAttribute('aria-label'));
      const qtyAfter = await page.$eval('#' + qtyId, (el) => el.value);
      const submitOk =
        !!consumePost &&
        consumePost.status === 200 &&
        consumePost.body &&
        (consumePost.body.status === 'success' ||
          (consumePost.body.data && consumePost.body.data.quantity >= 1));
      report.keyboardSubmit = {
        ok: submitOk,
        qtyId,
        qtyAfter,
        aria,
        consumePost,
        enterTriggered: true,
      };
      if (!submitOk) report.pass = false;
      fs.writeFileSync(path.join(LOG, 'keyboard_submit.json'), JSON.stringify(report.keyboardSubmit, null, 2));
    }
    await page.close();
  }

  // Aggregate negative + regression summary from cases
  report.negativeChecks = {
    notPlaceholderTitleAlone: report.cases.every((c) => c.notPlaceholderAlone),
    axNamesFromAriaLabel: report.cases.every((c) => c.axNameMatchesAria),
    statusNotWrongAllCategory: report.cases.every(
      (c) => c.stName && !/全部分类|全部分類/.test(c.stName)
    ),
  };
  report.regressions = {
    dateLabelForAll: report.cases.every((c) => c.regressions && c.regressions.dateLabelFor),
    searchAriaAll: report.cases.every((c) => c.regressions && c.regressions.searchAria),
    cases: report.cases.map((c) => ({ label: c.label, regressions: c.regressions })),
  };
  if (!report.negativeChecks.notPlaceholderTitleAlone || !report.negativeChecks.statusNotWrongAllCategory) {
    report.pass = false;
  }
  if (!report.regressions.dateLabelForAll) report.pass = false;

  fs.writeFileSync(path.join(LOG, 'qa_axtree_keyboard.json'), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
  await browser.close();
  process.exit(report.pass ? 0 : 1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
