'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15185';
const OUT = path.join(__dirname, '..');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function walk(node, acc, pathRoles) {
  if (!node) return;
  const role = node.role || '';
  const name = (node.name || '').trim();
  const ignored = !!node.ignored;
  const nextPath = pathRoles.concat(role);
  const interesting = ['spinbutton', 'combobox', 'textbox', 'searchbox', 'slider'];
  if (interesting.includes(role)) {
    acc.push({
      role,
      name,
      ignored,
      description: node.description || '',
      value: node.value,
      htmlId: node.id || null,
      path: nextPath.slice(-8).join('/'),
    });
  }
  const kids = node.children || [];
  for (const c of kids) walk(c, acc, nextPath);
}

async function setupPage(browser, width, height, langHeaders) {
  const page = await browser.newPage();
  await page.setViewport({ width, height });
  page.setDefaultTimeout(60000);
  await page.setExtraHTTPHeaders(Object.assign({
    'X-Role': 'owner',
    'X-Tenant-Id': 'uat035dev',
  }, langHeaders || {}));
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    try { localStorage.setItem('demo_tenant', 'uat035dev'); } catch (e) {}
  });
  await page.evaluate(() => {
    if (typeof window.apiFetch === 'function' && !window.__uat035_patched) {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {
          'X-Role': 'owner',
          'X-Tenant-Id': 'uat035dev',
        });
        return orig.call(this, url, opts);
      };
      window.__uat035_patched = true;
    }
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(600);
  await page.evaluate(() => {
    if (typeof window.apiFetch === 'function' && !window.__uat035_patched) {
      const orig = window.apiFetch;
      window.apiFetch = function(url, opts) {
        opts = opts || {};
        opts.headers = Object.assign({}, opts.headers || {}, {
          'X-Role': 'owner',
          'X-Tenant-Id': 'uat035dev',
        });
        return orig.call(this, url, opts);
      };
      window.__uat035_patched = true;
    }
  });
  return page;
}

async function goDishesDaily(page) {
  await page.evaluate(() => {
    const btn = document.querySelector('.sidebar-btn[data-target="tab-dishes"]')
      || document.querySelector('[data-target="tab-dishes"]');
    if (btn) btn.click();
    if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-daily');
    else {
      const st = document.querySelector('[data-subtab="subtab-dish-daily"]');
      if (st) st.click();
    }
    if (typeof loadDailyConsumption === 'function') loadDailyConsumption();
  });
  await sleep(1000);
}

async function goDishesBom(page) {
  await page.evaluate(() => {
    if (typeof switchDishSubtab === 'function') switchDishSubtab('subtab-dish-bom');
    else {
      const st = document.querySelector('[data-subtab="subtab-dish-bom"]');
      if (st) st.click();
    }
  });
  await sleep(800);
}

async function goExportDishCost(page) {
  await page.evaluate(() => {
    const btn = document.querySelector('.sidebar-btn[data-target="tab-export"]')
      || document.querySelector('[data-target="tab-export"]');
    if (btn) btn.click();
    if (typeof initExportCenter === 'function') initExportCenter();
  });
  await sleep(1200);
  await page.evaluate(() => {
    if (typeof selectExportCategory === 'function') {
      selectExportCategory('business', 'dish_cost_margin');
    } else if (typeof selectExportReport === 'function') {
      selectExportReport('dish_cost_margin');
    }
  });
  await sleep(1000);
}

async function goExportConsumption(page) {
  await page.evaluate(() => {
    if (typeof selectExportCategory === 'function') {
      // stocktake category has dish_ingredient_consumption
      selectExportCategory('stocktake', 'dish_ingredient_consumption');
    }
  });
  await sleep(1000);
}

async function collectDomNames(page) {
  return page.evaluate(() => {
    function axNameHint(el) {
      const al = el.getAttribute('aria-label');
      const alb = el.getAttribute('aria-labelledby');
      let labelFor = null;
      if (el.id) {
        const lab = document.querySelector('label[for="' + el.id + '"]');
        if (lab) labelFor = (lab.textContent || '').trim();
      }
      return {
        id: el.id || null,
        tag: el.tagName,
        type: el.type || null,
        className: el.className || '',
        ariaLabel: al,
        ariaLabelledby: alb,
        labelFor,
        placeholder: el.getAttribute('placeholder'),
        title: el.getAttribute('title'),
        nameAttr: el.getAttribute('name'),
      };
    }
    const targets = [];
    document.querySelectorAll(
      '#dishConsumeEntryTableBody input.dish-consume-qty-input, #dishCategoryFilter, #dishStatusFilter, #dishConsumeSearchInput, #exportDynamicFilters select, #exportDynamicFilters input'
    ).forEach(el => targets.push(axNameHint(el)));
    return targets;
  });
}

async function scan(page, label) {
  const snap = await page.accessibility.snapshot({ interestingOnly: false });
  const acc = [];
  walk(snap, acc, []);
  const nameless = acc.filter(n => !n.ignored && !n.name && (n.role === 'spinbutton' || n.role === 'combobox'));
  const named = acc.filter(n => !n.ignored && n.name && (n.role === 'spinbutton' || n.role === 'combobox'));
  const dom = await collectDomNames(page);
  return { label, nameless, namedCount: named.length, namedSample: named.slice(0, 15), allInteresting: acc.filter(n => !n.ignored && (n.role === 'spinbutton' || n.role === 'combobox' || n.role === 'textbox')), dom };
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
  });
  const results = {};
  for (const [key, w, h, lang] of [
    ['hans_1440', 1440, 900, {}],
    ['hant_390', 390, 844, {}],
  ]) {
    const page = await setupPage(browser, w, h, lang);
    if (key.startsWith('hant')) {
      await page.evaluate(() => {
        if (typeof setLocale === 'function') setLocale('zh-Hant-HK');
        else if (window.I18N && typeof I18N.setLocale === 'function') I18N.setLocale('zh-Hant-HK');
        else try { localStorage.setItem('demo_locale', 'zh-Hant-HK'); } catch (e) {}
      });
      await page.reload({ waitUntil: 'networkidle0' });
      await sleep(800);
      await page.evaluate(() => {
        if (typeof window.apiFetch === 'function') {
          const orig = window.apiFetch;
          window.apiFetch = function(url, opts) {
            opts = opts || {};
            opts.headers = Object.assign({}, opts.headers || {}, {
              'X-Role': 'owner',
              'X-Tenant-Id': 'uat035dev',
            });
            return orig.call(this, url, opts);
          };
        }
      });
    }
    await goDishesDaily(page);
    results[key + '_daily'] = await scan(page, key + '_daily');
    await goDishesBom(page);
    results[key + '_bom'] = await scan(page, key + '_bom');
    await goExportDishCost(page);
    results[key + '_export_margin'] = await scan(page, key + '_export_margin');
    await goExportConsumption(page);
    results[key + '_export_consum'] = await scan(page, key + '_export_consum');
    await page.close();
  }
  fs.writeFileSync(path.join(LOG, 'axtree_before.json'), JSON.stringify(results, null, 2));
  // compact summary
  const summary = {};
  for (const [k, v] of Object.entries(results)) {
    summary[k] = {
      nameless: v.nameless,
      namelessCount: v.nameless.length,
      dom: v.dom,
      interestingNamelessOrEmpty: (v.allInteresting || []).filter(x => !x.name),
    };
  }
  fs.writeFileSync(path.join(LOG, 'axtree_before_summary.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
  await browser.close();
}
main().catch(e => { console.error(e); process.exit(1); });
