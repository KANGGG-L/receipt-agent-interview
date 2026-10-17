'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15430';
const TAG = process.env.MEASURE_TAG || 'before';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function setRole(page, role) {
  await page.evaluate((r) => localStorage.setItem('demo_role', r), role);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(900);
}

async function goTab(page, target) {
  // Prefer mobile tab bar if visible, else sidebar
  await page.evaluate((t) => {
    const mob = document.querySelector('.mobile-tab[data-target="' + t + '"], #mobileTabBar [data-target="' + t + '"]');
    if (mob && getComputedStyle(mob).display !== 'none') { mob.click(); return; }
    const btn = document.querySelector('.sidebar-btn[data-target="' + t + '"], [data-target="' + t + '"]');
    if (btn) btn.click();
    // also try onclick switchTab if present
    if (typeof window.switchTab === 'function') {
      try { window.switchTab(t); } catch (_) {}
    }
  }, target);
  await sleep(1200);
}

async function measure(page, vp) {
  return page.evaluate((viewport) => {
    const absY = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
    };
    const rect = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: Math.round(r.left), y: Math.round(r.top),
        w: Math.round(r.width), h: Math.round(r.height),
        absY: absY(el),
        visibleW: Math.round(Math.min(r.right, window.innerWidth) - Math.max(r.left, 0)),
      };
    };
    const search = document.querySelector('#invSearchInput');
    const fltKw = document.querySelector('#fltKeyword');
    const invTable = document.querySelector('#inventoryTable') || document.querySelector('#tab-inventory .table-container');
    const archiveHist = document.querySelector('#archiveHistoryBody') || document.querySelector('#historyTable') || document.querySelector('[data-arc-pane="docs"] .table-container');
    // Prefer first business table rows
    const invBiz = document.querySelector('#tab-inventory #inventoryTable') || document.querySelector('#tab-inventory .table-container');
    const arcDocsCard = document.querySelector('#tab-archive [data-arc-pane="docs"].card, #tab-archive .card.csp-s-5');
    const arcHistTable = document.querySelector('#archiveListBody') || document.querySelector('#historyTableBody') || document.querySelector('#tab-archive #historyTable') || document.querySelector('#tab-archive table.data-table');
    // Find history records table more carefully
    const allTables = Array.from(document.querySelectorAll('#tab-archive table'));
    const histCandidates = allTables.map((t) => ({
      id: t.id || '',
      parent: t.closest('.card')?.id || '',
      absY: absY(t),
      rows: t.querySelectorAll('tbody tr').length,
    }));

    const kpiCards = Array.from(document.querySelectorAll('#tab-inventory .metrics-grid .metric-card, #todoBar .metric-card'));
    const kpiVisible = kpiCards.filter((c) => {
      const cs = getComputedStyle(c);
      return cs.display !== 'none' && cs.visibility !== 'hidden' && c.getClientRects().length;
    }).length;

    const tabBar = document.querySelector('#mobileTabBar, .mobile-tabbar');
    const saveBtn = document.querySelector('#btnSaveReview');

    // Last field in inventory card / review form
    const lastInvRow = document.querySelector('#inventoryTable tbody tr:last-child');
    const lastInvAction = lastInvRow ? lastInvRow.querySelector('.inv-actions, td:last-child') : null;

    const mw = document.querySelector('.main-wrapper');
    const scrollTop = mw ? mw.scrollTop : (window.scrollY || 0);

    return {
      viewport,
      innerWidth: window.innerWidth,
      innerHeight: window.innerHeight,
      scrollTop,
      docOverflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
      kpiVisibleCount: kpiVisible,
      kpiTotal: kpiCards.length,
      invSearch: rect(search),
      fltKeyword: rect(fltKw),
      invBizStart: rect(invBiz),
      invTableAbsY: absY(invBiz),
      arcDocsCard: rect(arcDocsCard),
      arcHistCandidates: histCandidates,
      archiveAdvancedHidden: !!(document.querySelector('#archiveAdvanced') && document.querySelector('#archiveAdvanced').classList.contains('hide')),
      archiveAdvancedToggle: rect(document.querySelector('#archiveAdvancedToggle')),
      mobileTabBar: rect(tabBar),
      saveBtn: rect(saveBtn),
      lastInvAction: rect(lastInvAction),
      mainWrapperPadBottom: mw ? getComputedStyle(mw).paddingBottom : null,
      prefillPadBottom: (() => {
        const el = document.querySelector('#prefillFormCard');
        return el ? getComputedStyle(el).paddingBottom : null;
      })(),
      metricsGrid: (() => {
        const g = document.querySelector('#tab-inventory .metrics-grid');
        if (!g) return null;
        const cs = getComputedStyle(g);
        return { columns: cs.gridTemplateColumns, gap: cs.gap, absY: absY(g), h: Math.round(g.getBoundingClientRect().height) };
      })(),
      invToolbar: rect(document.querySelector('.inv-toolbar')),
      invFiltersExpanded: (() => {
        const p = document.querySelector('#invMobileFiltersPanel, .inv-mobile-filters');
        if (!p) return null;
        return !p.classList.contains('hide') && getComputedStyle(p).display !== 'none';
      })(),
    };
  }, vp);
}

async function runVp(browser, vp, tag) {
  const page = await browser.newPage();
  await page.setViewport({ width: vp.w, height: vp.h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0', timeout: 60000 });
  await setRole(page, 'owner');
  await goTab(page, 'inventory');
  await sleep(1500);
  // Scroll main-wrapper to top
  await page.evaluate(() => {
    const mw = document.querySelector('.main-wrapper');
    if (mw) mw.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await sleep(400);
  const inv = await measure(page, vp);
  await page.screenshot({ path: path.join(UI, `${tag}-inv-${vp.w}.png`), fullPage: false });
  // full page via main-wrapper height capture alternative: scroll snap of biz area
  await page.evaluate(() => {
    const t = document.querySelector('#inventoryTable');
    if (t) t.scrollIntoView({ block: 'start' });
  });
  await sleep(300);
  await page.screenshot({ path: path.join(UI, `${tag}-inv-${vp.w}-at-table.png`), fullPage: false });

  await page.evaluate(() => {
    const mw = document.querySelector('.main-wrapper');
    if (mw) mw.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await goTab(page, 'archive');
  await sleep(1800);
  await page.evaluate(() => {
    const mw = document.querySelector('.main-wrapper');
    if (mw) mw.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await sleep(400);
  const arc = await measure(page, vp);
  await page.screenshot({ path: path.join(UI, `${tag}-arc-${vp.w}.png`), fullPage: false });

  // Probe filter expand / clear on archive
  let filterProbe = {};
  try {
    await page.click('#archiveAdvancedToggle');
    await sleep(400);
    filterProbe.advancedAfterToggleHide = await page.evaluate(() => document.querySelector('#archiveAdvanced')?.classList.contains('hide'));
    await page.click('#archiveAdvancedToggle');
    await sleep(200);
    // reset filters button
    const resetVisible = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const b = btns.find((x) => (x.textContent || '').includes('重置'));
      return b ? true : false;
    });
    filterProbe.resetButtonPresent = resetVisible;
    if (resetVisible) {
      await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button'));
        const b = btns.find((x) => (x.textContent || '').includes('重置'));
        if (b) b.click();
      });
      await sleep(300);
      filterProbe.resetClicked = true;
    }
  } catch (e) {
    filterProbe.error = String(e);
  }

  // Inventory search visibility / filter expand if panel exists
  await goTab(page, 'inventory');
  await sleep(800);
  let invFilterProbe = {};
  const hasToggle = await page.$('#invMobileFilterToggle');
  if (hasToggle) {
    await page.click('#invMobileFilterToggle');
    await sleep(300);
    invFilterProbe.expanded = await page.evaluate(() => {
      const p = document.querySelector('#invMobileFiltersPanel');
      return p && !p.classList.contains('hide');
    });
    const clearBtn = await page.$('#invMobileFilterClear');
    if (clearBtn) {
      await page.click('#invMobileFilterClear');
      await sleep(200);
      invFilterProbe.clearClicked = true;
      invFilterProbe.afterClear = await page.evaluate(() => ({
        search: document.querySelector('#invSearchInput')?.value,
        cat: document.querySelector('#invCategorySelect')?.value,
        filt: document.querySelector('#invFilterSelect')?.value,
      }));
    }
  } else {
    invFilterProbe.noMobileToggle = true;
  }

  await page.close();
  return { inventory: inv, archive: arc, filterProbe, invFilterProbe };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const results = {};
  for (const vp of [{ w: 390, h: 844 }, { w: 412, h: 915 }]) {
    results[String(vp.w)] = await runVp(browser, vp, TAG);
  }
  await browser.close();
  const outPath = path.join(LOG, `measure-${TAG}.json`);
  fs.writeFileSync(outPath, JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ tag: TAG, outPath, summary: Object.fromEntries(Object.entries(results).map(([k, v]) => [k, {
    invTableAbsY: v.inventory.invTableAbsY,
    invSearchW: v.inventory.invSearch?.w,
    invSearchVisibleW: v.inventory.invSearch?.visibleW,
    kpiVisible: v.inventory.kpiVisibleCount,
    metricsH: v.inventory.metricsGrid?.h,
    metricsCols: v.inventory.metricsGrid?.columns,
    arcHist: v.archive.arcHistCandidates,
    filterProbe: v.filterProbe,
    invFilterProbe: v.invFilterProbe,
  }])) }, null, 2));
})().catch((e) => { console.error(e); process.exit(1); });
