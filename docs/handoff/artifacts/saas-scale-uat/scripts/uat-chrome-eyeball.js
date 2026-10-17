'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15140';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function md5(fp) {
  return crypto.createHash('md5').update(fs.readFileSync(fp)).digest('hex');
}

async function clickTab(page, target) {
  await page.evaluate((target) => {
    const btn = document.querySelector('.sidebar-btn[data-target="' + target + '"]');
    if (!btn) throw new Error('missing sidebar btn ' + target);
    btn.click();
  }, target);
  await sleep(600);
  const active = await page.evaluate((target) => {
    const tab = document.getElementById(target);
    const btn = document.querySelector('.sidebar-btn[data-target="' + target + '"]');
    return {
      tabActive: !!(tab && tab.classList.contains('active')),
      btnActive: !!(btn && btn.classList.contains('active')),
      display: tab ? getComputedStyle(tab).display : null,
    };
  }, target);
  return active;
}

async function injectScale(page) {
  await page.evaluate(() => {
    const archiveN = 670, todoN = 635;
    const statusesTodo = ['parsed', 'parsed_with_warnings', 'edited', 'error', 'flagged', 'uploaded'];
    const data = [];
    for (let i = 1; i <= archiveN; i++) {
      const status = i <= todoN ? statusesTodo[(i - 1) % statusesTodo.length] : 'approved';
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
        math_warnings: [],
        review_priority_score: 0,
        deleted_at: null,
        use_grey: false,
      });
    }
    window.__saasScaleSynthetic = data;
    allArchiveReceipts = data.slice();
    cachedWorkbenchQueueData = data.slice();
  });
}

async function shotEl(page, sel, fp) {
  const h = await page.$(sel);
  if (!h) return { ok: false, reason: 'missing ' + sel };
  await h.screenshot({ path: fp });
  return { ok: true, path: fp, md5: md5(fp) };
}

async function chromeOf(page, kind) {
  return page.evaluate((kind) => {
    const map = kind === 'archive' ? {
      badge: 'archiveRecordCount', range: 'archivePageRange', label: 'archivePageLabel',
      pager: 'archivePagination', prev: 'archivePagePrev', next: 'archivePageNext',
      body: 'archiveTableBody', dataAttr: 'data-saas-scale',
    } : {
      badge: 'queueFilteredCount', range: 'queuePageRange', label: 'queuePageLabel',
      pager: 'queuePagination', prev: 'queuePagePrev', next: 'queuePageNext',
      body: 'workbenchQueueBody', dataAttr: 'data-saas-scale',
    };
    const box = (id) => {
      const el = document.getElementById(id);
      if (!el) return { id, present: false };
      const r = el.getBoundingClientRect();
      const st = getComputedStyle(el);
      return {
        id, present: true,
        text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 160),
        visible: st.display !== 'none' && st.visibility !== 'hidden' && r.width > 0 && r.height > 0,
        w: Math.round(r.width), h: Math.round(r.height),
        top: Math.round(r.top), left: Math.round(r.left),
        dataSaas: el.getAttribute('data-saas-scale'),
      };
    };
    const body = document.getElementById(map.body);
    const rows = body ? body.querySelectorAll('tr[data-receipt-id]').length : 0;
    return {
      kind,
      badge: box(map.badge),
      range: box(map.range),
      label: box(map.label),
      pager: box(map.pager),
      prev: box(map.prev),
      next: box(map.next),
      rows,
      tabTitle: (document.getElementById('bcActiveTitle') || {}).textContent || null,
    };
  }, kind);
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
  await sleep(500);
  await page.evaluate(() => { try { localStorage.setItem('demo_role', 'owner'); } catch (e) {} });
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(800);
  await injectScale(page);

  // --- ARCHIVE ---
  const archNav = await clickTab(page, 'tab-archive');
  await sleep(400);
  // Tab switch may refetch /api/receipts and overwrite synthetic seed — re-inject then render
  await injectScale(page);
  await page.evaluate(() => {
    archiveCurrentPage = 1;
    if (typeof ArchiveUI !== 'undefined' && ArchiveUI) ArchiveUI.q = '';
    const kw = document.getElementById('fltKeyword');
    if (kw) kw.value = '';
    if (typeof renderArchiveTable === 'function') renderArchiveTable(allArchiveReceipts.slice());
    if (typeof setArchivePage === 'function') setArchivePage(1);
  });
  await sleep(500);
  await page.evaluate(() => {
    const el = document.getElementById('archivePagination');
    if (el) el.scrollIntoView({ block: 'center' });
  });
  await sleep(300);
  const archiveChrome = await chromeOf(page, 'archive');
  await page.screenshot({ path: path.join(UI, 'archive-page1.png'), fullPage: false });
  const archPagerShot = await shotEl(page, '#archivePagination', path.join(UI, 'archive-pagination-chrome.png'));
  // also table header+rows region
  await shotEl(page, '#archiveTable', path.join(UI, 'archive-table-region.png')).catch(() => null);

  // --- QUEUE (scan / 识别复核) ---
  const queueNav = await clickTab(page, 'tab-scan');
  await sleep(400);
  await injectScale(page);
  await page.evaluate(() => {
    currentWorkbenchQueueFilter = 'todo';
    queueCurrentPage = 1;
    if (typeof updateWorkbenchQueueCounters === 'function') updateWorkbenchQueueCounters();
    if (typeof renderWorkbenchQueueTable === 'function') renderWorkbenchQueueTable();
    if (typeof setQueuePage === 'function') setQueuePage(1);
  });
  await sleep(700);
  await page.evaluate(() => {
    const el = document.getElementById('queuePagination') || document.getElementById('workbenchQueueBody');
    if (el) el.scrollIntoView({ block: 'center' });
  });
  await sleep(300);
  const queueChrome = await chromeOf(page, 'queue');
  await page.screenshot({ path: path.join(UI, 'queue-page1.png'), fullPage: false });
  const queuePagerShot = await shotEl(page, '#queuePagination', path.join(UI, 'queue-pagination-chrome.png'));
  await shotEl(page, '#workbenchQueueBody', path.join(UI, 'queue-table-region.png')).catch(() => null);

  const shotMd5 = {
    archivePage1: md5(path.join(UI, 'archive-page1.png')),
    archiveChrome: archPagerShot.ok ? archPagerShot.md5 : null,
    queuePage1: md5(path.join(UI, 'queue-page1.png')),
    queueChrome: queuePagerShot.ok ? queuePagerShot.md5 : null,
  };

  const ux = {
    archiveNavOk: !!(archNav.tabActive || archNav.btnActive),
    queueNavOk: !!(queueNav.tabActive || queueNav.btnActive),
    archiveBadgeVisible: !!(archiveChrome.badge && archiveChrome.badge.visible),
    archiveRangeVisible: !!(archiveChrome.range && archiveChrome.range.visible),
    archivePagerVisible: !!(archiveChrome.pager && archiveChrome.pager.visible),
    archiveRows: archiveChrome.rows,
    archiveBadgeText: archiveChrome.badge && archiveChrome.badge.text,
    archiveRangeText: archiveChrome.range && archiveChrome.range.text,
    archiveLabelText: archiveChrome.label && archiveChrome.label.text,
    queueBadgeVisible: !!(queueChrome.badge && queueChrome.badge.visible),
    queueRangeVisible: !!(queueChrome.range && queueChrome.range.visible),
    queuePagerVisible: !!(queueChrome.pager && queueChrome.pager.visible),
    queueRows: queueChrome.rows,
    queueBadgeText: queueChrome.badge && queueChrome.badge.text,
    queueRangeText: queueChrome.range && queueChrome.range.text,
    queueLabelText: queueChrome.label && queueChrome.label.text,
    pageShotsDiffer: shotMd5.archivePage1 !== shotMd5.queuePage1,
    pagerShotsDiffer: !!(shotMd5.archiveChrome && shotMd5.queueChrome && shotMd5.archiveChrome !== shotMd5.queueChrome),
    pagerDataAttrsDistinct:
      (archiveChrome.pager && archiveChrome.pager.dataSaas) === 'archive-pager' &&
      (queueChrome.pager && queueChrome.pager.dataSaas) === 'queue-pager',
    badgeCopyDistinct:
      /条记录/.test((archiveChrome.badge && archiveChrome.badge.text) || '') &&
      /共\s*\d+\s*条/.test((queueChrome.badge && queueChrome.badge.text) || '') &&
      ((archiveChrome.badge && archiveChrome.badge.text) !== (queueChrome.badge && queueChrome.badge.text)),
  };

  ux.pass =
    ux.archiveNavOk && ux.queueNavOk &&
    ux.archiveBadgeVisible && ux.archiveRangeVisible && ux.archivePagerVisible &&
    ux.queueBadgeVisible && ux.queueRangeVisible && ux.queuePagerVisible &&
    ux.archiveRows === 50 && ux.queueRows === 50 &&
    ux.pageShotsDiffer && ux.pagerShotsDiffer &&
    ux.pagerDataAttrsDistinct && ux.badgeCopyDistinct &&
    /670/.test(ux.archiveBadgeText || '') && /635/.test(ux.queueBadgeText || '') &&
    /1\s*[–-]\s*50/.test(ux.archiveRangeText || '') && /1\s*[–-]\s*50/.test(ux.queueRangeText || '') &&
    /1\s*\/\s*14/.test(ux.archiveLabelText || '') && /1\s*\/\s*13/.test(ux.queueLabelText || '');

  const result = {
    generatedAt: new Date().toISOString(),
    archNav, queueNav,
    archiveChrome, queueChrome,
    shotMd5, ux,
    gate: ux.pass ? 'PASS' : 'FAIL',
  };
  fs.writeFileSync(path.join(LOG, 'chrome-eyeball.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({ gate: result.gate, ux, shotMd5 }, null, 2));
  await browser.close();
  process.exit(ux.pass ? 0 : 2);
}

main().catch((e) => { console.error(e); process.exit(1); });
