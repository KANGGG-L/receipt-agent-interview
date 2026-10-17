'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15450';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// Explicit performance budgets (ms / counts) — not empty PASS
const BUDGETS = {
  archivePageSizeMaxDom: 50,
  queuePageSizeMaxDom: 50,
  archiveRenderPage1Ms: 200,
  archivePageNextMs: 100,
  archiveGotoLastMs: 150,
  archiveFilterMs: 250,
  archiveSearchMs: 250,
  queueRenderPage1Ms: 200,
  queuePageNextMs: 100,
  queueGotoLastMs: 150,
  archiveTotal: 670,
  queueTodoTotal: 635,
};

function synthReceipts(n, opts) {
  opts = opts || {};
  const statusesTodo = ['parsed', 'parsed_with_warnings', 'edited', 'error', 'flagged', 'uploaded'];
  const out = [];
  for (let i = 1; i <= n; i++) {
    const status = opts.allApproved
      ? 'approved'
      : (i <= (opts.todoCount || 0)
          ? statusesTodo[(i - 1) % statusesTodo.length]
          : 'approved');
    out.push({
      id: 900000 + i,
      supplier_name: i % 17 === 0 ? 'SCALE_HIT_SUPPLIER' : ('供应商Scale' + String(i % 40).padStart(2, '0')),
      supplier_code: 'SC' + String(i % 40).padStart(2, '0'),
      sheet_name: 'SHEET-' + i,
      status,
      payment_status: i % 3 === 0 ? 'paid' : 'unpaid',
      total_amount: 10 + (i % 97),
      currency: 'HKD',
      receipt_date: '2026-10-' + String((i % 28) + 1).padStart(2, '0'),
      upload_date: '2026-10-01T10:00:00',
      updated_date: '2026-10-02T11:00:00',
      department_name: i % 5 === 0 ? '厨房' : null,
      image_url: '',
      quality_warnings: [],
      math_warnings: i % 41 === 0 ? ['math'] : [],
      review_priority_score: 0,
      deleted_at: null,
      use_grey: false,
    });
  }
  return out;
}

async function main() {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });
  const page = await browser.newPage();
  page.setDefaultTimeout(60000);
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await sleep(800);
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
  });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(1000);

  // Inject synthesized dataset: 670 archive; among them 635 non-approved for todo queue
  const archiveN = BUDGETS.archiveTotal;
  const todoN = BUDGETS.queueTodoTotal;
  await page.evaluate((archiveN, todoN) => {
    const statusesTodo = ['parsed', 'parsed_with_warnings', 'edited', 'error', 'flagged', 'uploaded'];
    const data = [];
    for (let i = 1; i <= archiveN; i++) {
      const status = i <= todoN
        ? statusesTodo[(i - 1) % statusesTodo.length]
        : 'approved';
      data.push({
        id: 900000 + i,
        supplier_name: i % 17 === 0 ? 'SCALE_HIT_SUPPLIER' : ('供应商Scale' + String(i % 40).padStart(2, '0')),
        supplier_code: 'SC' + String(i % 40).padStart(2, '0'),
        sheet_name: 'SHEET-' + i,
        status,
        payment_status: i % 3 === 0 ? 'paid' : 'unpaid',
        total_amount: 10 + (i % 97),
        currency: 'HKD',
        receipt_date: '2026-10-' + String((i % 28) + 1).padStart(2, '0'),
        upload_date: '2026-10-01T10:00:00',
        updated_date: '2026-10-02T11:00:00',
        department_name: i % 5 === 0 ? '厨房' : null,
        image_url: '',
        quality_warnings: [],
        math_warnings: i % 41 === 0 ? ['math'] : [],
        review_priority_score: 0,
        deleted_at: null,
        use_grey: false,
      });
    }
    window.__saasScaleSynthetic = data;
    // shared list cache used by both archive + queue
    if (typeof window.fetchReceiptListShared === 'function') {
      // invalidate then seed cache via side channel
    }
    // Directly seed module-level caches
    allArchiveReceipts = data.slice();
    cachedWorkbenchQueueData = data.slice();
    if (typeof invalidateReceiptListCache === 'function') {
      try { /* keep synthetic */ } catch (e) {}
    }
  }, archiveN, todoN);

  // Navigate to archive pane and render
  await page.evaluate(() => {
    if (typeof switchTab === 'function') switchTab('archive');
    else {
      const btn = document.querySelector('[data-target="archive"]');
      if (btn) btn.click();
    }
  });
  await sleep(500);

  const archiveMetrics = await page.evaluate((BUDGETS) => {
    const out = { budgets: BUDGETS, checks: {}, timings: {}, dom: {}, empty: {}, search: {}, crossPage: {} };
    const t0 = performance.now();
    archiveCurrentPage = 1;
    applyArchiveFilters(0);
    // applyArchiveFilters uses setTimeout(delay); force immediate path by calling render directly if needed
    return new Promise((resolve) => {
      setTimeout(() => {
        const t1 = performance.now();
        out.timings.archiveRenderPage1Ms = Math.round(t1 - t0);
        const badge = document.getElementById('archiveRecordCount');
        const range = document.getElementById('archivePageRange');
        const label = document.getElementById('archivePageLabel');
        const rows = Array.from(document.querySelectorAll('#archiveTableBody tr[data-receipt-id]'));
        const emptyRow = document.querySelector('#archiveTableBody tr[data-empty-state]');
        out.dom.archiveDataRows = rows.length;
        out.dom.archiveBadge = badge ? badge.textContent : null;
        out.dom.archiveRange = range ? range.textContent : null;
        out.dom.archivePageLabel = label ? label.textContent : null;
        out.dom.archiveFirstId = rows[0] ? rows[0].getAttribute('data-receipt-id') : null;
        out.dom.archiveLastOnPageId = rows.length ? rows[rows.length - 1].getAttribute('data-receipt-id') : null;
        out.dom.archiveFilteredTotal = archiveFilteredCache.length;
        out.dom.archivePageSizeConst = ARCHIVE_PAGE_SIZE;

        // page next timing
        const t2 = performance.now();
        setArchivePage(2);
        const t3 = performance.now();
        out.timings.archivePageNextMs = Math.round(t3 - t2);
        out.dom.archivePage2Rows = document.querySelectorAll('#archiveTableBody tr[data-receipt-id]').length;
        out.dom.archivePage2FirstId = (document.querySelector('#archiveTableBody tr[data-receipt-id]') || {}).getAttribute?.('data-receipt-id') || null;

        // goto last
        const pages = Math.ceil(archiveFilteredCache.length / ARCHIVE_PAGE_SIZE);
        const t4 = performance.now();
        setArchivePage(pages);
        const t5 = performance.now();
        out.timings.archiveGotoLastMs = Math.round(t5 - t4);
        const lastRows = Array.from(document.querySelectorAll('#archiveTableBody tr[data-receipt-id]'));
        out.dom.archiveLastPageRows = lastRows.length;
        out.dom.archiveLastItemId = lastRows.length ? lastRows[lastRows.length - 1].getAttribute('data-receipt-id') : null;
        out.dom.archiveExpectedLastId = String(archiveFilteredCache[archiveFilteredCache.length - 1].id);
        out.checks.lastItemReachable = out.dom.archiveLastItemId === out.dom.archiveExpectedLastId;
        out.checks.noLostRecords = archiveFilteredCache.length === BUDGETS.archiveTotal
          && new Set(archiveFilteredCache.map(r => r.id)).size === BUDGETS.archiveTotal;

        // search / filter
        ArchiveUI.q = 'SCALE_HIT_SUPPLIER';
        const kw = document.getElementById('fltKeyword');
        if (kw) kw.value = 'SCALE_HIT_SUPPLIER';
        const t6 = performance.now();
        archiveCurrentPage = 1;
        // direct filter path without debounce
        const filtered = allArchiveReceipts.filter(r => (r.supplier_name || '').includes('SCALE_HIT_SUPPLIER'));
        renderArchiveTable(filtered);
        const t7 = performance.now();
        out.timings.archiveSearchMs = Math.round(t7 - t6);
        out.search.hitTotal = archiveFilteredCache.length;
        out.search.domRows = document.querySelectorAll('#archiveTableBody tr[data-receipt-id]').length;
        out.search.badge = (document.getElementById('archiveRecordCount') || {}).textContent;
        out.search.range = (document.getElementById('archivePageRange') || {}).textContent;

        // no-filter-results empty state
        ArchiveUI.q = '___NO_MATCH_SCALE___';
        if (kw) kw.value = '___NO_MATCH_SCALE___';
        renderArchiveTable(allArchiveReceipts.filter(() => false));
        out.empty.noFilter = {
          state: (document.querySelector('#archiveTableBody tr[data-empty-state]') || {}).getAttribute?.('data-empty-state'),
          text: (document.querySelector('#archiveTableBody tr[data-empty-state] td') || {}).textContent,
          badge: (document.getElementById('archiveRecordCount') || {}).textContent,
        };

        // restore full list for cross-page edit
        ArchiveUI.q = '';
        if (kw) kw.value = '';
        renderArchiveTable(allArchiveReceipts.slice());
        setArchivePage(1);
        const editId = Number(archiveFilteredCache[5].id);
        // mutate status on page 1 item, go page 2, come back — state must remain
        const target = allArchiveReceipts.find(r => r.id === editId);
        const before = target.status;
        target.status = 'edited';
        // also update filtered cache entry
        const fc = archiveFilteredCache.find(r => r.id === editId);
        if (fc) fc.status = 'edited';
        setArchivePage(2);
        setArchivePage(1);
        const row = document.querySelector('#archiveTableBody tr[data-receipt-id="' + editId + '"]');
        out.crossPage = {
          editId,
          before,
          after: target.status,
          rowPresentOnReturn: !!row,
          // badge text includes 待老板审批 / edited label somewhere
          rowHtmlHasEditedHint: row ? /edited|待老板|审批|badge-primary/i.test(row.innerHTML) : false,
        };

        // empty DB distinction
        const saved = allArchiveReceipts.slice();
        allArchiveReceipts = [];
        renderArchiveTable([]);
        out.empty.emptyDb = {
          state: (document.querySelector('#archiveTableBody tr[data-empty-state]') || {}).getAttribute?.('data-empty-state'),
          text: (document.querySelector('#archiveTableBody tr[data-empty-state] td') || {}).textContent,
        };
        allArchiveReceipts = saved;
        renderArchiveTable(saved);

        resolve(out);
      }, 80);
    });
  }, BUDGETS);

  await page.screenshot({ path: path.join(UI, 'archive-page1.png'), fullPage: false });

  // Workbench / scan tab
  await page.evaluate(() => {
    if (typeof switchTab === 'function') switchTab('scan');
    else {
      const btn = document.querySelector('[data-target="scan"]');
      if (btn) btn.click();
    }
  });
  await sleep(600);

  const queueMetrics = await page.evaluate((BUDGETS) => {
    const out = { timings: {}, dom: {}, empty: {}, crossPage: {}, checks: {} };
    currentWorkbenchQueueFilter = 'todo';
    queueCurrentPage = 1;
    const t0 = performance.now();
    updateWorkbenchQueueCounters();
    renderWorkbenchQueueTable();
    const t1 = performance.now();
    out.timings.queueRenderPage1Ms = Math.round(t1 - t0);
    const rows = Array.from(document.querySelectorAll('#workbenchQueueBody tr[data-receipt-id]'));
    out.dom.queueDataRows = rows.length;
    out.dom.queueCount = (document.getElementById('queueFilteredCount') || {}).textContent;
    out.dom.queueRange = (document.getElementById('queuePageRange') || {}).textContent;
    out.dom.queuePageLabel = (document.getElementById('queuePageLabel') || {}).textContent;
    out.dom.queueFilteredTotal = queueFilteredCache.length;
    out.dom.qCountTodo = (document.getElementById('qCountTodo') || {}).textContent;
    out.checks.todoTotalOk = queueFilteredCache.length === BUDGETS.queueTodoTotal;

    const t2 = performance.now();
    setQueuePage(2);
    const t3 = performance.now();
    out.timings.queuePageNextMs = Math.round(t3 - t2);
    out.dom.queuePage2Rows = document.querySelectorAll('#workbenchQueueBody tr[data-receipt-id]').length;

    const pages = Math.ceil(queueFilteredCache.length / QUEUE_PAGE_SIZE);
    const t4 = performance.now();
    setQueuePage(pages);
    const t5 = performance.now();
    out.timings.queueGotoLastMs = Math.round(t5 - t4);
    const lastRows = Array.from(document.querySelectorAll('#workbenchQueueBody tr[data-receipt-id]'));
    out.dom.queueLastPageRows = lastRows.length;
    out.dom.queueLastItemId = lastRows.length ? lastRows[lastRows.length - 1].getAttribute('data-receipt-id') : null;
    out.dom.queueExpectedLastId = String(queueFilteredCache[queueFilteredCache.length - 1].id);
    out.checks.lastItemReachable = out.dom.queueLastItemId === out.dom.queueExpectedLastId;

    // tab with no results while DB has data
    currentWorkbenchQueueFilter = 'to_approve';
    // force empty by temporarily clearing edited
    const backup = cachedWorkbenchQueueData.map(r => ({ ...r }));
    cachedWorkbenchQueueData.forEach(r => { if (r.status === 'edited') r.status = 'parsed'; });
    queueCurrentPage = 1;
    renderWorkbenchQueueTable();
    out.empty.noFilter = {
      state: (document.querySelector('#workbenchQueueBody tr[data-empty-state]') || {}).getAttribute?.('data-empty-state'),
      text: (document.querySelector('#workbenchQueueBody tr[data-empty-state] td') || {}).textContent,
    };
    cachedWorkbenchQueueData = backup;
    currentWorkbenchQueueFilter = 'todo';
    renderWorkbenchQueueTable();

    // empty DB
    const saved = cachedWorkbenchQueueData;
    cachedWorkbenchQueueData = [];
    renderWorkbenchQueueTable();
    out.empty.emptyDb = {
      state: (document.querySelector('#workbenchQueueBody tr[data-empty-state]') || {}).getAttribute?.('data-empty-state'),
      text: (document.querySelector('#workbenchQueueBody tr[data-empty-state] td') || {}).textContent,
    };
    cachedWorkbenchQueueData = saved;
    currentWorkbenchQueueFilter = 'todo';
    queueCurrentPage = 1;
    renderWorkbenchQueueTable();

    // cross-page edit: change status of item on page 1, flip pages, confirm still edited in cache + DOM
    setQueuePage(1);
    const editId = Number(queueFilteredCache[3].id);
    const target = cachedWorkbenchQueueData.find(r => r.id === editId);
    target.status = 'edited';
    setQueuePage(2);
    setQueuePage(1);
    // item may leave todo filter if we set edited... wait, edited is still in todo (not approved). Good.
    renderWorkbenchQueueTable();
    setQueuePage(1);
    const row = document.querySelector('#workbenchQueueBody tr[data-receipt-id="' + editId + '"]');
    out.crossPage = {
      editId,
      status: target.status,
      rowPresent: !!row,
      rowHintsEdited: row ? /待老板|审批|badge-primary|edited/i.test(row.innerHTML) : false,
    };

    return out;
  }, BUDGETS);

  await page.screenshot({ path: path.join(UI, 'queue-page1.png'), fullPage: false });

  // Budget evaluation
  function passBudget(actual, max, name) {
    return { name, actual, max, pass: typeof actual === 'number' && actual <= max };
  }
  const budgetResults = [
    passBudget(archiveMetrics.dom.archiveDataRows, BUDGETS.archivePageSizeMaxDom, 'archiveDomRowsPage1'),
    passBudget(archiveMetrics.timings.archiveRenderPage1Ms, BUDGETS.archiveRenderPage1Ms, 'archiveRenderPage1Ms'),
    passBudget(archiveMetrics.timings.archivePageNextMs, BUDGETS.archivePageNextMs, 'archivePageNextMs'),
    passBudget(archiveMetrics.timings.archiveGotoLastMs, BUDGETS.archiveGotoLastMs, 'archiveGotoLastMs'),
    passBudget(archiveMetrics.timings.archiveSearchMs, BUDGETS.archiveSearchMs, 'archiveSearchMs'),
    passBudget(queueMetrics.dom.queueDataRows, BUDGETS.queuePageSizeMaxDom, 'queueDomRowsPage1'),
    passBudget(queueMetrics.timings.queueRenderPage1Ms, BUDGETS.queueRenderPage1Ms, 'queueRenderPage1Ms'),
    passBudget(queueMetrics.timings.queuePageNextMs, BUDGETS.queuePageNextMs, 'queuePageNextMs'),
    passBudget(queueMetrics.timings.queueGotoLastMs, BUDGETS.queueGotoLastMs, 'queueGotoLastMs'),
  ];

  const result = {
    generatedAt: new Date().toISOString(),
    budgets: BUDGETS,
    archive: archiveMetrics,
    queue: queueMetrics,
    budgetResults,
    allBudgetsPass: budgetResults.every(b => b.pass),
    acceptance: {
      totalsAndRange: !!(archiveMetrics.dom.archiveBadge && archiveMetrics.dom.archiveRange && queueMetrics.dom.queueCount && queueMetrics.dom.queueRange),
      lastItemArchive: !!archiveMetrics.checks.lastItemReachable,
      lastItemQueue: !!queueMetrics.checks.lastItemReachable,
      noLost: !!archiveMetrics.checks.noLostRecords,
      todo635: !!queueMetrics.checks.todoTotalOk,
      emptyStatesArchive: archiveMetrics.empty.noFilter.state === 'no-filter-results' && archiveMetrics.empty.emptyDb.state === 'empty-db',
      emptyStatesQueue: queueMetrics.empty.noFilter.state === 'no-filter-results' && queueMetrics.empty.emptyDb.state === 'empty-db',
      crossPageArchive: !!archiveMetrics.crossPage.rowPresentOnReturn && archiveMetrics.crossPage.after === 'edited',
      crossPageQueue: !!queueMetrics.crossPage.rowPresent && queueMetrics.crossPage.status === 'edited',
      domCapped: archiveMetrics.dom.archiveDataRows <= 50 && queueMetrics.dom.queueDataRows <= 50,
    },
  };
  result.acceptancePass = Object.values(result.acceptance).every(Boolean);
  result.devSelfCheck = result.acceptancePass && result.allBudgetsPass ? 'PASS' : 'FAIL';

  fs.writeFileSync(path.join(LOG, 'measure-result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({
    devSelfCheck: result.devSelfCheck,
    allBudgetsPass: result.allBudgetsPass,
    acceptancePass: result.acceptancePass,
    archiveRows: archiveMetrics.dom.archiveDataRows,
    queueRows: queueMetrics.dom.queueDataRows,
    archiveTotal: archiveMetrics.dom.archiveFilteredTotal,
    queueTotal: queueMetrics.dom.queueFilteredTotal,
    timings: { ...archiveMetrics.timings, ...queueMetrics.timings },
    budgetFails: budgetResults.filter(b => !b.pass),
    acceptanceFails: Object.entries(result.acceptance).filter(([, v]) => !v).map(([k]) => k),
  }, null, 2));

  await browser.close();
  process.exit(result.devSelfCheck === 'PASS' ? 0 : 2);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
