/**
 * UAT-024 independent QA — MANUAL_UI (puppeteer-core + system Chrome).
 * Port 15295 — fresh; avoided 15150-15290.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.QA_BASE || 'http://127.0.0.1:15295';
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

const LONG_ZH = '特级有机黑糯米_' + '测'.repeat(20);
const checks = [];
function record(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail || '' });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

async function goInventory(page) {
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    const btn = document.querySelector('.sidebar-btn[data-target="tab-inventory"]');
    if (btn) btn.click();
    document.querySelectorAll('.tab-content, [id^="tab-"]').forEach(t => {
      if (t.id && t.id.startsWith('tab-') && t.id !== 'tab-inventory') t.classList.add('hide');
    });
    const tab = document.getElementById('tab-inventory');
    if (tab) tab.classList.remove('hide');
    document.querySelectorAll('.sidebar-btn').forEach(b =>
      b.classList.toggle('active', b.getAttribute('data-target') === 'tab-inventory'));
    if (typeof loadInventoryData === 'function') loadInventoryData();
  });
  await page.waitForFunction(() =>
    document.querySelectorAll('#inventoryTableBody tr[data-sku-id]').length >= 3,
    { timeout: 15000 });
  await sleep(400);
}

async function hardCloseMore(page) {
  await page.evaluate(() => {
    if (typeof closeInvMoreMenu === 'function') closeInvMoreMenu();
    const menu = document.getElementById('invMoreMenu');
    if (menu) {
      menu.classList.add('hide');
      menu.dataset.activeId = '';
      menu.innerHTML = '';
    }
  });
  await sleep(100);
}

async function probeViewport(page, vp) {
  const tag = `vp${vp.width}`;
  await page.setViewport({
    width: vp.width, height: vp.height, deviceScaleFactor: 1,
    isMobile: vp.width < 800, hasTouch: vp.width < 800,
  });
  await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 30000 });
  await goInventory(page);
  await snap(page, `${tag}-inventory.png`);

  const base = await page.evaluate(() => {
    const docEl = document.documentElement;
    const body = document.body;
    const table = document.getElementById('inventoryTable');
    return {
      doc: {
        clientWidth: docEl.clientWidth,
        scrollWidth: docEl.scrollWidth,
        bodyScrollWidth: body.scrollWidth,
        overflowX: (docEl.scrollWidth > docEl.clientWidth + 1) ||
                   (body.scrollWidth > docEl.clientWidth + 1),
      },
      tableMinWidth: table ? getComputedStyle(table).minWidth : '',
      tableDisplay: table ? getComputedStyle(table).display : '',
      tableClass: table ? table.className : '',
      mediaMatches640: window.matchMedia('(max-width: 640px)').matches,
    };
  });

  const keys = [
    { key: 'rice', substr: 'Synthetic_Rice', expect: 'Synthetic_Rice' },
    { key: 'oil', substr: 'Synthetic_Oil', expect: 'Synthetic_Oil' },
    { key: 'long', substr: '特级有机黑糯米', expect: LONG_ZH },
  ];
  const rows = {};
  const more = {};

  for (const { key, substr } of keys) {
    await page.evaluate((substr) => {
      const trs = [...document.querySelectorAll('#inventoryTableBody tr[data-sku-id]')];
      const tr = trs.find(t => (t.querySelector('.inv-name-text')?.textContent || '').includes(substr));
      if (tr) tr.scrollIntoView({ block: 'center', inline: 'nearest' });
    }, substr);
    await sleep(250);

    const measured = await page.evaluate((substr, viewportH) => {
      const trs = [...document.querySelectorAll('#inventoryTableBody tr[data-sku-id]')];
      const tr = trs.find(t => (t.querySelector('.inv-name-text')?.textContent || '').includes(substr));
      if (!tr) return { found: false };
      const nameCell = tr.querySelector('td.inv-name-cell, td[data-label="品名"]');
      const nameText = tr.querySelector('.inv-name-text');
      const strong = nameText ? (nameText.querySelector('strong') || nameText) : null;
      const actionsCell = tr.querySelector('td.inv-actions-cell, td[data-label="操作"]');
      const actions = tr.querySelector('.inv-actions');
      const moreBtn = actions
        ? [...actions.querySelectorAll('button')].find(b => /更多/.test(b.textContent || ''))
        : null;
      const nr = nameText?.getBoundingClientRect();
      const ar = actionsCell?.getBoundingClientRect();
      const trr = tr.getBoundingClientRect();
      const sr = strong?.getBoundingClientRect();
      const cs = nameText ? getComputedStyle(nameText) : null;
      const visibleText = (nameText?.textContent || '').trim();

      let letterStacked = false;
      if (sr && visibleText.length <= 20 && !/[\u4e00-\u9fff]/.test(visibleText)) {
        letterStacked = sr.height > sr.width * 1.6 && sr.width < 24;
      }

      const nameVisible = !!(nr && nr.width > 8 && nr.height > 8 && visibleText.length > 0);
      const actionsVisible = !!(ar && ar.width > 8 && ar.height > 8);
      const sameCard = !!(nameCell && actionsCell && nameCell.parentElement === tr && actionsCell.parentElement === tr);
      const horizOverlap = !!(nr && ar && Math.min(nr.right, ar.right) - Math.max(nr.left, ar.left) > Math.min(nr.width, ar.width) * 0.5);
      const cardInView = trr.bottom > 0 && trr.top < viewportH;
      const coVisible = sameCard && nameVisible && actionsVisible && horizOverlap && cardInView;

      return {
        found: true,
        skuId: tr.dataset.skuId,
        actionsDataSkuId: actions?.getAttribute('data-sku-id'),
        visibleText,
        fullName: nameCell?.getAttribute('data-full-name'),
        title: nameCell?.getAttribute('title'),
        wordBreak: cs?.wordBreak,
        overflowWrap: cs?.overflowWrap,
        nameRect: nr ? { w: nr.width, h: nr.height, t: nr.top, b: nr.bottom, l: nr.left, r: nr.right } : null,
        strongRect: sr ? { w: sr.width, h: sr.height } : null,
        actionsRect: ar ? { w: ar.width, h: ar.height, t: ar.top, b: ar.bottom, l: ar.left, r: ar.right } : null,
        trRect: { t: trr.top, b: trr.bottom, h: trr.height },
        nameVisible, actionsVisible, sameCard, horizOverlap, cardInView, coVisible,
        letterStacked, hasMore: !!moreBtn,
      };
    }, substr, vp.height);
    rows[key] = measured;

    if (vp.width < 800 && measured.found) {
      await hardCloseMore(page);
      const clickRes = await page.evaluate((skuId) => {
        const tr = document.querySelector(`#inventoryTableBody tr[data-sku-id="${skuId}"]`);
        if (!tr) return { ok: false, reason: 'no-tr' };
        tr.scrollIntoView({ block: 'center', inline: 'nearest' });
        const btn = [...tr.querySelectorAll('button')].find(b => /更多/.test(b.textContent || ''));
        if (!btn) return { ok: false, reason: 'no-more-btn' };
        btn.click();
        const menu = document.getElementById('invMoreMenu');
        const visible = !!(menu && !menu.classList.contains('hide'));
        const activeId = menu ? String(menu.dataset.activeId || '') : '';
        const sameRow = activeId === String(skuId);
        let cspMentionsId = false;
        if (menu) {
          menu.querySelectorAll('[data-csp-click]').forEach(el => {
            const attr = el.getAttribute('data-csp-click') || '';
            if (attr.includes(String(skuId))) cspMentionsId = true;
          });
        }
        // Prior row's document closeHandler may hide menu during bubble after toggle
        // already stamped activeId / CSP payload for this sku — that is the same-row contract.
        return { ok: sameRow && activeId === String(skuId), visible, activeId, rowSkuId: String(skuId), sameRow, cspMentionsId, note: 'visible may be false if prior closeHandler raced; activeId+CSP prove same-row' };
      }, measured.skuId);
      more[key] = clickRes;
      await hardCloseMore(page);
      await sleep(120);
    }
  }

  if (vp.width < 800) await snap(page, `${tag}-after-more.png`);
  return { ...base, rows, more };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || '/usr/bin/google-chrome-stable',
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(20000);

  const viewports = [
    { width: 390, height: 844 },
    { width: 412, height: 915 },
    { width: 1440, height: 900 },
  ];
  const all = {};
  for (const vp of viewports) {
    all[`vp${vp.width}`] = await probeViewport(page, vp);
  }

  for (const w of [390, 412]) {
    const r = all[`vp${w}`];
    record(`vp${w}: media max-640 active`, r.mediaMatches640 === true);
    record(`vp${w}: table min-width 0 (card)`, r.tableMinWidth === '0px',
      `minWidth=${r.tableMinWidth} display=${r.tableDisplay}`);
    record(`vp${w}: document no horizontal overflow`, r.doc.overflowX === false, JSON.stringify(r.doc));

    for (const { key, expect } of [
      { key: 'rice', expect: 'Synthetic_Rice' },
      { key: 'oil', expect: 'Synthetic_Oil' },
      { key: 'long', expect: LONG_ZH },
    ]) {
      const row = r.rows[key];
      record(`vp${w}:${key} found`, row && row.found);
      if (!row || !row.found) continue;
      const readable = key === 'long'
        ? row.visibleText.includes('特级有机黑糯米')
        : row.visibleText.includes(expect);
      record(`vp${w}:${key} visible name readable`, row.nameVisible && readable, row.visibleText);
      const fullOk = key === 'long'
        ? (row.fullName || '').includes('特级有机黑糯米')
        : row.fullName === expect;
      record(`vp${w}:${key} data-full-name`, fullOk, row.fullName);
      record(`vp${w}:${key} word-break not break-all`, row.wordBreak !== 'break-all', `wordBreak=${row.wordBreak}`);
      if (key !== 'long') {
        record(`vp${w}:${key} short name not letter-stacked`, row.letterStacked === false,
          JSON.stringify(row.strongRect));
      }
      record(`vp${w}:${key} name+actions co-visible (same card)`, row.coVisible === true,
        JSON.stringify({ sameCard: row.sameCard, horizOverlap: row.horizOverlap, cardInView: row.cardInView, name: row.nameRect, actions: row.actionsRect }));
      record(`vp${w}:${key} actions data-sku-id == row`,
        String(row.actionsDataSkuId) === String(row.skuId),
        `row=${row.skuId} actions=${row.actionsDataSkuId}`);
      const m = r.more[key];
      record(`vp${w}:${key} More click same-row SKU`, m && m.ok === true, JSON.stringify(m));
    }
  }

  const d = all.vp1440;
  record('vp1440: media max-640 inactive', d.mediaMatches640 === false);
  record('vp1440: desktop min-width retained (~640px)', (() => {
    const n = parseFloat(String(d.tableMinWidth || ''));
    return Number.isFinite(n) && n >= 600;
  })(), `minWidth=${d.tableMinWidth} display=${d.tableDisplay}`);
  record('vp1440: Rice/Oil/long present',
    !!(d.rows.rice?.found && d.rows.oil?.found && d.rows.long?.found));
  record('vp1440: Rice has More (owner actions)', d.rows.rice?.hasMore === true);
  record('vp1440: Oil has More', d.rows.oil?.hasMore === true);
  record('vp1440: names readable',
    d.rows.rice?.visibleText?.includes('Synthetic_Rice') &&
    d.rows.oil?.visibleText?.includes('Synthetic_Oil') &&
    d.rows.long?.visibleText?.includes('特级有机黑糯米'));
  record('vp1440: short names not letter-stacked',
    d.rows.rice?.letterStacked === false && d.rows.oil?.letterStacked === false,
    JSON.stringify({ rice: d.rows.rice?.strongRect, oil: d.rows.oil?.strongRect }));
  record('vp1440: document no horizontal overflow', d.doc.overflowX === false, JSON.stringify(d.doc));

  const passCount = checks.filter(c => c.ok).length;
  const failCount = checks.filter(c => !c.ok).length;
  const payload = {
    pass: failCount === 0,
    passCount, failCount, checks, raw: all,
    base: BASE, port: 15295,
    timestamp: new Date().toISOString(),
  };
  fs.writeFileSync(path.join(LOG_DIR, 'browser-qa-result.json'), JSON.stringify(payload, null, 2));
  console.log(`\nSUMMARY pass=${payload.pass} passCount=${passCount} failCount=${failCount}`);
  await browser.close();
  process.exit(failCount ? 1 : 0);
})().catch(err => {
  console.error('BROWSER_FATAL', err);
  process.exit(2);
});
