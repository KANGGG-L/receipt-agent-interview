'use strict';
/**
 * SAAS-MOBILE Independent UAT capture (MANUAL_UI) (390/412).
 * Criteria:
 *  1. search fully visible; filter expand/clear works
 *  2. business table Y earlier vs baseline y891 / y1408
 *  3. fixed bottom nav does not block last content after scroll
 *  4. KPIs not all hidden
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15130';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const BASELINE = {
  invTableY: 891,
  archiveTableY: 1408, // card cites ~1408; measure-before had 1438
};

function fail(msg) { return { ok: false, msg }; }
function pass(msg) { return { ok: true, msg }; }

async function setRole(page, role) {
  await page.evaluate((r) => localStorage.setItem('demo_role', r), role);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(900);
}

async function goTab(page, target) {
  // App uses data-target="tab-inventory" etc. Mobile bar uses data-tab.
  const tid = target.startsWith('tab-') ? target : ('tab-' + target);
  await page.evaluate((id) => {
    const mob = document.querySelector('#mobileTabBar .mobile-tab[data-tab="' + id + '"], .mobile-tab[data-tab="' + id + '"]');
    if (mob && getComputedStyle(mob).display !== 'none') { mob.click(); return; }
    const btn = document.querySelector('.sidebar-btn[data-target="' + id + '"], .sidebar-nav .sidebar-btn[data-target="' + id + '"]');
    if (btn) { btn.click(); return; }
    // fallback: show tab content directly (should not be needed)
    document.querySelectorAll('.tab-content').forEach((el) => { el.style.display = 'none'; });
    const tab = document.getElementById(id);
    if (tab) tab.style.display = 'block';
  }, tid);
  await sleep(1600);
  // Wait until tab content is display:block
  await page.waitForFunction((id) => {
    const tab = document.getElementById(id);
    return tab && getComputedStyle(tab).display !== 'none';
  }, { timeout: 10000 }, tid).catch(() => {});
  await sleep(500);
}

async function scrollTop(page) {
  await page.evaluate(() => {
    const mw = document.querySelector('.main-wrapper');
    if (mw) mw.scrollTop = 0;
    window.scrollTo(0, 0);
  });
  await sleep(350);
}

function absY(el) {
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
}

async function measureInventoryCollapsed(page, vp) {
  return page.evaluate((viewport) => {
    const absY = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
    };
    const search = document.querySelector('#invSearchInput');
    const sr = search ? search.getBoundingClientRect() : null;
    const fullyInView = !!(sr && sr.left >= -1 && sr.right <= window.innerWidth + 1 && sr.width > 0 && sr.height > 0);
    const table = document.querySelector('#inventoryTable');
    const kpiCards = Array.from(document.querySelectorAll('#tab-inventory .metrics-grid .metric-card'));
    const kpiHidden = kpiCards.filter((c) => {
      const cs = getComputedStyle(c);
      return cs.display === 'none' || cs.visibility === 'hidden' || !c.getClientRects().length;
    }).length;
    const g = document.querySelector('#tab-inventory .metrics-grid');
    const cb = document.querySelector('#invMobileFilterToggleCb');
    const panel = document.querySelector('#invMobileFiltersPanel');
    const panelDisplay = panel ? getComputedStyle(panel).display : null;
    const filtersCollapsed = !(cb && cb.checked) && (panelDisplay === 'none' || panelDisplay === null);
    return {
      viewport,
      tableY: absY(table),
      search: sr ? { w: Math.round(sr.width), h: Math.round(sr.height), left: Math.round(sr.left), right: Math.round(sr.right), fullyInView } : null,
      kpiH: g ? Math.round(g.getBoundingClientRect().height) : null,
      kpiCols: g ? getComputedStyle(g).gridTemplateColumns : null,
      kpiCount: kpiCards.length,
      kpiHidden,
      kpiHitTargets: kpiCards.map((c) => {
        const r = c.getBoundingClientRect();
        return { w: Math.round(r.width), h: Math.round(r.height), ok: r.width >= 44 && r.height >= 36 };
      }),
      filtersCollapsed,
      filterCbChecked: !!(cb && cb.checked),
      panelDisplay,
      mainWrapperPadBottom: (() => {
        const mw = document.querySelector('.main-wrapper');
        return mw ? getComputedStyle(mw).paddingBottom : null;
      })(),
    };
  }, vp);
}

async function expandInvFilters(page) {
  // Click label to check the checkbox (CSS :has opens panel)
  await page.evaluate(() => {
    const cb = document.querySelector('#invMobileFilterToggleCb');
    if (cb && !cb.checked) {
      const label = document.querySelector('label[for="invMobileFilterToggleCb"], #invMobileFilterToggle');
      if (label) label.click();
      else cb.click();
    }
  });
  await sleep(400);
  return page.evaluate(() => {
    const cb = document.querySelector('#invMobileFilterToggleCb');
    const panel = document.querySelector('#invMobileFiltersPanel');
    const table = document.querySelector('#inventoryTable');
    const absY = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
    };
    return {
      filtersOpen: !!(cb && cb.checked) && panel && getComputedStyle(panel).display !== 'none',
      filterCbChecked: !!(cb && cb.checked),
      panelDisplay: panel ? getComputedStyle(panel).display : null,
      tableY: absY(table),
    };
  });
}

async function clearInvFilters(page) {
  // Set dirty values then clear
  await page.evaluate(() => {
    const s = document.querySelector('#invSearchInput');
    if (s) {
      s.value = 'uat-dirty-search';
      s.dispatchEvent(new Event('input', { bubbles: true }));
    }
    const cat = document.querySelector('#invCategorySelect');
    if (cat && cat.options.length > 1) {
      cat.selectedIndex = Math.min(1, cat.options.length - 1);
      cat.dispatchEvent(new Event('change', { bubbles: true }));
    }
    const filt = document.querySelector('#invFilterSelect');
    if (filt && filt.options.length > 1) {
      filt.selectedIndex = Math.min(1, filt.options.length - 1);
      filt.dispatchEvent(new Event('change', { bubbles: true }));
    }
  });
  await sleep(200);
  const before = await page.evaluate(() => ({
    search: document.querySelector('#invSearchInput')?.value,
    cat: document.querySelector('#invCategorySelect')?.value,
    filt: document.querySelector('#invFilterSelect')?.value,
  }));
  // Click clear button
  await page.evaluate(() => {
    const btn = document.querySelector('#invMobileFilterClear');
    if (btn) btn.click();
    else if (typeof window.resetInventoryFilters === 'function') window.resetInventoryFilters();
  });
  await sleep(400);
  const after = await page.evaluate(() => ({
    search: document.querySelector('#invSearchInput')?.value,
    cat: document.querySelector('#invCategorySelect')?.value,
    filt: document.querySelector('#invFilterSelect')?.value,
  }));
  return { before, after, cleared: after.search === '' || after.search === null || after.search === undefined };
}

async function measureArchiveCollapsed(page, vp) {
  return page.evaluate((viewport) => {
    const absY = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
    };
    const table = document.querySelector('#archiveTable');
    const flt = document.querySelector('#fltKeyword');
    const fr = flt ? flt.getBoundingClientRect() : null;
    const fullyInView = !!(fr && fr.left >= -1 && fr.right <= window.innerWidth + 1 && fr.width > 0);
    const todo = document.querySelector('#todoBar');
    const todoCards = todo ? Array.from(todo.querySelectorAll('.metric-card')) : [];
    const todoHidden = todoCards.filter((c) => {
      const cs = getComputedStyle(c);
      return cs.display === 'none' || cs.visibility === 'hidden' || !c.getClientRects().length;
    }).length;
    const grid = document.querySelector('#archiveFilterGrid');
    const adv = document.querySelector('#archiveAdvanced');
    return {
      viewport,
      archiveTableY: absY(table),
      todoH: todo ? Math.round(todo.getBoundingClientRect().height) : null,
      todoCount: todoCards.length,
      todoHidden,
      fltKeyword: fr ? { w: Math.round(fr.width), fullyInView } : null,
      filterGridDisplay: grid ? getComputedStyle(grid).display : null,
      advancedHidden: !!(adv && adv.classList.contains('hide')),
    };
  }, vp);
}

async function expandArchiveAdvanced(page) {
  await page.evaluate(() => {
    const t = document.querySelector('#archiveAdvancedToggle');
    if (t) t.click();
    else if (typeof window.toggleArchiveAdvanced === 'function') window.toggleArchiveAdvanced();
  });
  await sleep(450);
  return page.evaluate(() => {
    const absY = (el) => {
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return Math.round(r.top + (window.scrollY || document.documentElement.scrollTop || 0));
    };
    const adv = document.querySelector('#archiveAdvanced');
    const grid = document.querySelector('#archiveFilterGrid');
    const table = document.querySelector('#archiveTable');
    return {
      advancedHidden: !!(adv && adv.classList.contains('hide')),
      filterGridDisplay: grid ? getComputedStyle(grid).display : null,
      archiveTableY: absY(table),
    };
  });
}

async function measureBottomClearance(page) {
  // Scroll inventory table last row into view and check vs tab bar
  return page.evaluate(() => {
    const mw = document.querySelector('.main-wrapper');
    const tab = document.querySelector('#mobileTabBar, .mobile-tabbar');
    const lastRow = document.querySelector('#inventoryTable tbody tr:last-child');
    const lastAction = lastRow
      ? (lastRow.querySelector('.inv-actions, td:last-child') || lastRow)
      : document.querySelector('#inventoryTable');
    if (lastAction) lastAction.scrollIntoView({ block: 'end' });
    // also scroll main-wrapper to bottom
    if (mw) mw.scrollTop = mw.scrollHeight;
    const tabR = tab ? tab.getBoundingClientRect() : null;
    const lastR = lastAction ? lastAction.getBoundingClientRect() : null;
    const tabTop = tabR ? Math.round(tabR.top) : null;
    const lastBottom = lastR ? Math.round(lastR.bottom) : null;
    // blocked if last content bottom is below tab top while still "on screen" overlapping tab
    let blocked = false;
    if (tabR && lastR) {
      const overlap = lastR.bottom > tabR.top + 2 && lastR.top < tabR.bottom;
      // After scrolling to end, last content should sit above tab or be scrollable past with padding.
      // Use clearance: padding-bottom on main-wrapper should allow last content above tab when scrolled fully.
      const pad = mw ? parseFloat(getComputedStyle(mw).paddingBottom) || 0 : 0;
      // Re-measure after ensuring scroll end: content bottom relative to scrollport
      const mwR = mw ? mw.getBoundingClientRect() : null;
      // If last row bottom is within tab bar area (overlap) AND cannot scroll further → blocked
      const canScrollMore = mw ? (mw.scrollTop + mw.clientHeight < mw.scrollHeight - 2) : false;
      blocked = overlap && !canScrollMore && lastR.bottom > tabR.top + 4;
      // Also consider: if pad is insufficient (< 56) that's a risk
      if (pad < 56) blocked = true;
    }
    return {
      lastBottom,
      tabTop,
      blocked,
      clearance: (tabTop != null && lastBottom != null) ? (tabTop - lastBottom) : null,
      padBottom: mw ? getComputedStyle(mw).paddingBottom : null,
      tabVisible: tab ? getComputedStyle(tab).display !== 'none' : false,
    };
  });
}

async function runVp(browser, vp) {
  const page = await browser.newPage();
  await page.setViewport({ width: vp.w, height: vp.h, isMobile: true, hasTouch: true, deviceScaleFactor: 2 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0', timeout: 60000 });
  await setRole(page, 'owner');
  await goTab(page, 'inventory');
  await scrollTop(page);

  const inv = await measureInventoryCollapsed(page, vp);
  await page.screenshot({ path: path.join(UI, `uat-inv-${vp.w}-collapsed.png`), fullPage: false });

  const invExp = await expandInvFilters(page);
  await page.screenshot({ path: path.join(UI, `uat-inv-${vp.w}-filters-open.png`), fullPage: false });

  const clearResult = await clearInvFilters(page);

  // Collapse filters again for baseline Y check path (default)
  await page.evaluate(() => {
    const cb = document.querySelector('#invMobileFilterToggleCb');
    if (cb && cb.checked) {
      const label = document.querySelector('label[for="invMobileFilterToggleCb"], #invMobileFilterToggle');
      if (label) label.click();
      else cb.checked = false;
    }
  });
  await sleep(300);
  await scrollTop(page);
  const invCollapsedAgain = await measureInventoryCollapsed(page, vp);

  // Bottom clearance
  const bottom = await measureBottomClearance(page);
  await page.screenshot({ path: path.join(UI, `uat-inv-${vp.w}-scrolled-end.png`), fullPage: false });

  // Archive
  await goTab(page, 'archive');
  await scrollTop(page);
  const arc = await measureArchiveCollapsed(page, vp);
  await page.screenshot({ path: path.join(UI, `uat-arc-${vp.w}-collapsed.png`), fullPage: false });
  const arcExp = await expandArchiveAdvanced(page);
  await page.screenshot({ path: path.join(UI, `uat-arc-${vp.w}-advanced-open.png`), fullPage: false });

  // Archive reset button present
  const resetPresent = await page.evaluate(() => {
    const btns = Array.from(document.querySelectorAll('button'));
    return btns.some((x) => (x.textContent || '').includes('重置'));
  });

  await page.close();
  return {
    inv: invCollapsedAgain.tableY ? invCollapsedAgain : inv,
    invFirst: inv,
    invExp,
    clearResult,
    bottom,
    arc,
    arcExp,
    resetPresent,
  };
}

function evaluateGates(results) {
  const gates = [];
  for (const w of ['390', '412']) {
    const r = results[w];
    const inv = r.inv;
    const searchOk = inv.search && inv.search.fullyInView && inv.search.w >= Math.min(300, Number(w) - 80);
    const expandOk = !!(r.invExp && r.invExp.filtersOpen);
    const clearOk = !!(r.clearResult && r.clearResult.cleared);
    gates.push({
      id: `AC1-search-full-${w}`,
      ... (searchOk ? pass(`search w=${inv.search.w} fullyInView`) : fail(`search=${JSON.stringify(inv.search)}`)),
    });
    gates.push({
      id: `AC1-filter-expand-${w}`,
      ...(expandOk ? pass(`filtersOpen tableY=${r.invExp.tableY}`) : fail(JSON.stringify(r.invExp))),
    });
    gates.push({
      id: `AC1-filter-clear-${w}`,
      ...(clearOk ? pass(`cleared after=${JSON.stringify(r.clearResult.after)}`) : fail(JSON.stringify(r.clearResult))),
    });

    const invEarlier = inv.tableY != null && inv.tableY < BASELINE.invTableY - 50; // meaningful move
    gates.push({
      id: `AC2-inv-table-earlier-${w}`,
      ...(invEarlier ? pass(`tableY ${inv.tableY} < baseline ${BASELINE.invTableY}`) : fail(`tableY=${inv.tableY} baseline=${BASELINE.invTableY}`)),
    });
    const arcEarlier = r.arc.archiveTableY != null && r.arc.archiveTableY < BASELINE.archiveTableY - 50;
    gates.push({
      id: `AC2-arc-table-earlier-${w}`,
      ...(arcEarlier ? pass(`archiveTableY ${r.arc.archiveTableY} < baseline ${BASELINE.archiveTableY}`) : fail(`archiveTableY=${r.arc.archiveTableY}`)),
    });

    gates.push({
      id: `AC3-bottom-nav-${w}`,
      ...(!r.bottom.blocked ? pass(`blocked=false pad=${r.bottom.padBottom} clearance=${r.bottom.clearance}`) : fail(JSON.stringify(r.bottom))),
    });

    const kpiOk = inv.kpiCount > 0 && inv.kpiHidden === 0;
    gates.push({
      id: `AC4-kpi-visible-${w}`,
      ...(kpiOk ? pass(`kpiCount=${inv.kpiCount} kpiHidden=${inv.kpiHidden}`) : fail(`kpiCount=${inv.kpiCount} kpiHidden=${inv.kpiHidden}`)),
    });
    const hits = inv.kpiHitTargets || [];
    const hitOk = hits.length > 0 && hits.every((h) => h.ok);
    gates.push({
      id: `AC4b-kpi-hit-target-${w}`,
      ...(hitOk ? pass(`hits=${JSON.stringify(hits)}`) : fail(`hits=${JSON.stringify(hits)}`)),
    });
  }
  const overall = gates.every((g) => g.ok);
  return { overall, gates };
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  const results = {};
  for (const vp of [{ w: 390, h: 844 }, { w: 412, h: 915 }]) {
    results[String(vp.w)] = await runVp(browser, vp);
  }
  await browser.close();

  const evaluation = evaluateGates(results);
  const payload = {
    base: BASE,
    baseline: BASELINE,
    results,
    evaluation,
    timestamp: new Date().toISOString(),
  };
  fs.writeFileSync(path.join(LOG, 'uat-capture-result.json'), JSON.stringify(payload, null, 2));
  fs.writeFileSync(path.join(LOG, 'uat-summary.json'), JSON.stringify({
    overall: evaluation.overall ? 'PASS' : 'FAIL',
    gates: evaluation.gates,
    summary: Object.fromEntries(Object.entries(results).map(([k, v]) => [k, {
      invTableY: v.inv.tableY,
      invSearchW: v.inv.search?.w,
      invSearchFullyInView: v.inv.search?.fullyInView,
      kpiCount: v.inv.kpiCount,
      kpiHidden: v.inv.kpiHidden,
      kpiHitTargets: v.inv.kpiHitTargets,
      filtersOpen: v.invExp.filtersOpen,
      cleared: v.clearResult.cleared,
      archiveTableY: v.arc.archiveTableY,
      bottomBlocked: v.bottom.blocked,
      padBottom: v.bottom.padBottom,
    }])),
  }, null, 2));
  console.log(JSON.stringify({ overall: evaluation.overall ? 'PASS' : 'FAIL', gates: evaluation.gates, summary: JSON.parse(fs.readFileSync(path.join(LOG, 'uat-summary.json'), 'utf8')).summary }, null, 2));
  process.exit(evaluation.overall ? 0 : 2);
})().catch((e) => { console.error(e); process.exit(1); });
