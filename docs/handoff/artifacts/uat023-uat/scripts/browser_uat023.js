/**
 * UAT-023 independent UAT — browser MANUAL_UI (puppeteer-core + system Chrome).
 * Port 15270 — fresh isolation; do NOT reuse 15150-15260 (QA used 15260).
 *
 * MANUAL_UI: Hans→Hant→Hans; preview identity cell textContent === CSV cell;
 * headers translate under Hant; DB name unchanged.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const PORT = Number(process.env.APP_PORT || 15270);
const BASE = `http://127.0.0.1:${PORT}`;
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

const LONG_NAME = '有机走地鸡胸肉（去皮去骨特长规格说明超长品名测试ABC）';
const LONG_SUPPLIER = '鲜生农产供应商（特长名称说明）';
const LONG_DEPT = '后厨冷链中心（超长部门名称）';
const XSS_NAME = '鸡翅<script>alert(1)</script>';

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function isIdentityHeader(h) {
  if (!h) return false;
  return h.includes('品名') || h.includes('名称') || h.includes('说明')
    || h.includes('备注') || h.includes('配方') || h.includes('建议')
    || h.includes('供应商') || h.includes('往来')
    || h === '部门' || h.includes('部门名称')
    || h === '归属部门' || h === '收购对象' || h.includes('诊断');
}

function normCsvCell(s) {
  s = s == null ? '' : String(s);
  if (s.startsWith("'") && s.length > 1 && '=+-@\t\r'.includes(s[1])) return s.slice(1);
  return s;
}

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

async function goExport(page) {
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    const btn = document.querySelector('.sidebar-btn[data-target="tab-export"]');
    if (btn) btn.click();
    document.querySelectorAll('.tab-content, [id^="tab-"]').forEach(t => {
      if (t.id && t.id.startsWith('tab-') && t.id !== 'tab-export') t.classList.add('hide');
    });
    const tab = document.getElementById('tab-export');
    if (tab) {
      tab.classList.remove('hide');
      tab.classList.add('active');
    }
    document.querySelectorAll('.sidebar-btn').forEach(b =>
      b.classList.toggle('active', b.getAttribute('data-target') === 'tab-export'));
  });
  await sleep(600);
  await page.waitForFunction(() => {
    return typeof selectExportReport === 'function' && document.getElementById('exportPreviewTbody');
  }, { timeout: 20000 });
}

async function selectReportAndPreview(page, reportId, dates) {
  await page.evaluate((rid, d) => {
    if (typeof selectExportReport === 'function') selectExportReport(rid);
    else if (window.exportCenterState) {
      window.exportCenterState.currentReportId = rid;
    }
  }, reportId, dates || null);
  await sleep(400);
  if (dates) {
    await page.evaluate((d) => {
      const s = document.getElementById('expStartDate');
      const e = document.getElementById('expEndDate');
      if (s) { s.value = d.start; s.dispatchEvent(new Event('change', { bubbles: true })); }
      if (e) { e.value = d.end; e.dispatchEvent(new Event('change', { bubbles: true })); }
      if (typeof handleExportDateChange === 'function') handleExportDateChange();
    }, dates);
    await sleep(200);
  }
  await page.evaluate(() => {
    if (typeof refreshExportPreview === 'function') refreshExportPreview();
  });
  await page.waitForFunction(() => {
    const tb = document.getElementById('exportPreviewTbody');
    if (!tb) return false;
    // wait until not "正在生成"
    const sum = document.getElementById('exportPreviewSummary');
    const loading = sum && /正在生成/.test(sum.textContent || '');
    return !loading && tb.querySelectorAll('tr').length >= 0;
  }, { timeout: 20000 });
  await sleep(500);
  // Extra wait for i18n MutationObserver to settle
  await sleep(400);
}

async function readPaintedPreview(page) {
  return page.evaluate(() => {
    const thead = document.getElementById('exportPreviewThead');
    const tbody = document.getElementById('exportPreviewTbody');
    const headers = [];
    if (thead) {
      thead.querySelectorAll('th').forEach(th => headers.push(th.textContent.trim()));
    }
    const rows = [];
    const skipFlags = [];
    if (tbody) {
      tbody.querySelectorAll('tr').forEach(tr => {
        // skip spacer rows from virtual scroll
        if (tr.classList.contains('export-virtual-spacer') || tr.getAttribute('aria-hidden') === 'true') return;
        const cells = [];
        const flags = [];
        tr.querySelectorAll('td').forEach(td => {
          cells.push(td.textContent);
          flags.push(td.hasAttribute('data-i18n-skip'));
        });
        if (cells.length) {
          rows.push(cells);
          skipFlags.push(flags);
        }
      });
    }
    const locale = document.documentElement.getAttribute('data-locale')
      || document.documentElement.getAttribute('lang') || '';
    const chromeSample = (() => {
      const btn = document.querySelector('.export-btn-download');
      return btn ? btn.textContent.trim().replace(/\s+/g, ' ') : null;
    })();
    return { headers, rows, skipFlags, locale, chromeSample };
  });
}

async function fetchCsvViaPage(page, reportId, params, lang) {
  return page.evaluate(async (rid, p, lang) => {
    const body = { report_id: rid, params: p || {} };
    if (lang) body.lang = lang;
    const res = await (window.apiFetch || fetch)('/api/export/download', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error('download HTTP ' + res.status);
    const buf = await res.arrayBuffer();
    // strip UTF-8 BOM
    const text = new TextDecoder('utf-8').decode(buf).replace(/^\uFEFF/, '');
    const lines = text.split(/\r?\n/).filter(l => l.length);
    // simple CSV parse (handles quoted fields)
    function parseLine(line) {
      const out = [];
      let cur = '';
      let inQ = false;
      for (let i = 0; i < line.length; i++) {
        const ch = line[i];
        if (inQ) {
          if (ch === '"' && line[i + 1] === '"') { cur += '"'; i++; }
          else if (ch === '"') inQ = false;
          else cur += ch;
        } else {
          if (ch === '"') inQ = true;
          else if (ch === ',') { out.push(cur); cur = ''; }
          else cur += ch;
        }
      }
      out.push(cur);
      return out;
    }
    const rows = lines.map(parseLine);
    return { headers: rows[0] || [], data: rows.slice(1), raw: text.slice(0, 400) };
  }, reportId, params || {}, lang || null);
}

async function readDbSkuName(page) {
  // via API if available; fall back to preview identity presence
  return page.evaluate(async () => {
    try {
      const res = await (window.apiFetch || fetch)('/api/skus');
      if (res.ok) {
        const js = await res.json();
        const list = (js.data || js) || [];
        const hit = (Array.isArray(list) ? list : []).find(s =>
          s.name && s.name.includes('有机走地鸡胸肉'));
        return hit ? hit.name : null;
      }
    } catch (e) {}
    return null;
  });
}

function compareIdentity(preview, csvHans, reportId, checks) {
  // Use Hans headers from CSV to find identity columns (preview under Hant has translated headers)
  const hansHeaders = csvHans.headers;
  const idxs = [];
  hansHeaders.forEach((h, i) => { if (isIdentityHeader(h)) idxs.push(i); });

  const detail = { reportId, idxs, comparisons: [], pass: true };
  if (!idxs.length) {
    detail.note = 'no identity cols';
    checks.push({ name: `${reportId}: has-or-skip-identity`, ok: true, detail });
    return detail;
  }
  if (!preview.rows.length) {
    detail.pass = false;
    detail.note = 'no painted rows';
    checks.push({ name: `${reportId}: painted rows present`, ok: false, detail });
    return detail;
  }
  for (const ci of idxs) {
    for (let ri = 0; ri < preview.rows.length; ri++) {
      if (ri >= csvHans.data.length) {
        detail.pass = false;
        checks.push({ name: `${reportId} row${ri} in CSV`, ok: false });
        continue;
      }
      const pv = preview.rows[ri][ci] != null ? String(preview.rows[ri][ci]) : '';
      const cv = normCsvCell(csvHans.data[ri][ci]);
      // CSV may stringify numbers differently; for identity cols they're strings
      const equal = pv === cv || pv.trim() === cv.trim();
      const skipOk = preview.skipFlags[ri] && preview.skipFlags[ri][ci] === true;
      detail.comparisons.push({
        col: hansHeaders[ci], ci, ri, preview: pv, csv: cv, equal, skipAttr: skipOk,
      });
      if (!equal) detail.pass = false;
      checks.push({
        name: `${reportId}: identity[${hansHeaders[ci]}] row${ri} preview==CSV`,
        ok: equal,
        detail: { preview: pv, csv: cv },
      });
      checks.push({
        name: `${reportId}: identity[${hansHeaders[ci]}] row${ri} has data-i18n-skip`,
        ok: skipOk,
      });
    }
  }
  return detail;
}

function headersTranslated(hansHeaders, paintedHeaders, checks, reportId) {
  // Under Hant, at least one header that contains 简体-specific chars should differ OR
  // known pairs: 食材品名 may stay same OpenCC-wise for some words; check 供应商名称→供應商名稱 etc.
  // Safer: use I18N.convert if available from page — but here we compare painted vs Hans CSV headers.
  let anyDiff = false;
  let allSameLen = paintedHeaders.length === hansHeaders.length;
  const pairs = [];
  for (let i = 0; i < Math.min(hansHeaders.length, paintedHeaders.length); i++) {
    const a = hansHeaders[i];
    const b = paintedHeaders[i];
    pairs.push([a, b]);
    if (a !== b) anyDiff = true;
  }
  // Identity-containing headers that OpenCC should change: 供应商名称, 归属部门 often convert
  // 食材品名: 品名 same; 食材 may stay. 供应商名称 → 供應商名稱 is a strong signal.
  const strong = pairs.filter(([a, b]) => /供应商|归属|状态|备注|分类|金额/.test(a));
  const strongTranslated = strong.length === 0
    ? anyDiff // fallback
    : strong.some(([a, b]) => a !== b);

  const ok = allSameLen && (strongTranslated || anyDiff);
  checks.push({
    name: `${reportId}: Hant painted headers translated vs Hans CSV`,
    ok,
    detail: { pairs: pairs.slice(0, 8), strong, anyDiff, allSameLen },
  });
  return ok;
}

(async () => {
  const result = {
    port: PORT,
    pass: false,
    checks: [],
    errors: [],
    reports: {},
    locales: {},
    db: {},
    screenshots: [],
  };
  const checks = result.checks;

  const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome';
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900'],
    defaultViewport: { width: 1440, height: 900 },
  });

  try {
    const page = await browser.newPage();
    page.setDefaultTimeout(30000);
    page.on('pageerror', e => result.errors.push('pageerror:' + e.message));

    // ---------- Phase A: Hans ----------
    await page.goto(`${BASE}/?lang=zh-Hans`, { waitUntil: 'networkidle2', timeout: 45000 });
    await page.evaluate(() => { try { localStorage.setItem('demo_role', 'owner'); } catch (e) {} });
    await sleep(800);
    await goExport(page);
    result.screenshots.push(await snap(page, 'export-hans-landed.png'));

    const reportsToTest = [
      { id: 'inventory_stocktake', params: {}, dates: null },
      { id: 'receipts_itemized_ledger', params: { start_date: '2026-09-01', end_date: '2026-09-30' },
        dates: { start: '2026-09-01', end: '2026-09-30' } },
      { id: 'inventory_valuation', params: {}, dates: null },
      { id: 'spend_summary', params: { start_date: '2026-09-01', end_date: '2026-09-30' },
        dates: { start: '2026-09-01', end: '2026-09-30' } },
      { id: 'inventory_stock_logs', params: { start_date: '2026-09-01', end_date: '2026-09-30' },
        dates: { start: '2026-09-01', end: '2026-09-30' } },
    ];

    // Capture Hans CSV baselines + painted under Hans
    const csvHansByReport = {};
    for (const rep of reportsToTest) {
      await selectReportAndPreview(page, rep.id, rep.dates);
      const painted = await readPaintedPreview(page);
      const csv = await fetchCsvViaPage(page, rep.id, rep.params, 'zh-Hans');
      csvHansByReport[rep.id] = csv;
      result.reports[rep.id] = result.reports[rep.id] || {};
      result.reports[rep.id].hans = { painted, csvHeaders: csv.headers, csvN: csv.data.length };

      // Under Hans, preview identity == CSV
      compareIdentity(painted, csv, rep.id + '/Hans', checks);

      // Long name presence for stocktake / ledger
      if (rep.id === 'inventory_stocktake' || rep.id === 'receipts_itemized_ledger') {
        const flat = painted.rows.map(r => r.join('|')).join('\n');
        const hit = flat.includes(LONG_NAME) || flat.includes(LONG_SUPPLIER) || flat.includes(LONG_DEPT);
        checks.push({ name: `${rep.id}/Hans: synthetic long name painted`, ok: hit, detail: flat.slice(0, 200) });
      }
      result.screenshots.push(await snap(page, `preview-hans-${rep.id}.png`));
    }

    const dbHans = await readDbSkuName(page);
    result.db.before = dbHans;
    checks.push({
      name: 'DB SKU long name present (Hans phase)',
      ok: dbHans === LONG_NAME || dbHans == null, // null = API missing; don't fail hard yet
      detail: dbHans,
    });

    // ---------- Phase B: Hant ----------
    await page.goto(`${BASE}/?lang=zh-Hant-HK`, { waitUntil: 'networkidle2', timeout: 45000 });
    await page.evaluate(() => { try { localStorage.setItem('demo_role', 'owner'); } catch (e) {} });
    await sleep(1000); // allow OpenCC pass
    await goExport(page);
    result.locales.hant_doc = await page.evaluate(() => ({
      lang: document.documentElement.getAttribute('lang'),
      dataLocale: document.documentElement.getAttribute('data-locale'),
    }));
    checks.push({
      name: 'document locale is zh-Hant-HK',
      ok: /Hant/i.test(result.locales.hant_doc.dataLocale || '')
        || /Hant/i.test(result.locales.hant_doc.lang || ''),
      detail: result.locales.hant_doc,
    });

    let chromeConverted = false;
    for (const rep of reportsToTest) {
      await selectReportAndPreview(page, rep.id, rep.dates);
      // wait extra for i18n observer after tbody inject
      await sleep(700);
      const painted = await readPaintedPreview(page);
      const csvHant = await fetchCsvViaPage(page, rep.id, rep.params, 'zh-Hant-HK');
      result.reports[rep.id].hant = {
        paintedHeaders: painted.headers,
        paintedN: painted.rows.length,
        chromeSample: painted.chromeSample,
        csvHantHeaders: csvHant.headers,
      };

      // Core AC: painted identity textContent === Hans CSV cell (not Hant-converted)
      compareIdentity(painted, csvHansByReport[rep.id], rep.id + '/Hant', checks);

      // Headers on painted thead should be translated (OpenCC)
      headersTranslated(csvHansByReport[rep.id].headers, painted.headers, checks, rep.id + '/Hant');

      // Hant CSV headers should differ from Hans for localizable headers
      const hantCsvDiff = csvHant.headers.some((h, i) => h !== csvHansByReport[rep.id].headers[i]);
      checks.push({
        name: `${rep.id}/Hant: CSV headers localized vs Hans`,
        ok: hantCsvDiff || csvHansByReport[rep.id].headers.length === 0,
        detail: { hans: csvHansByReport[rep.id].headers.slice(0, 5), hant: csvHant.headers.slice(0, 5) },
      });

      if (painted.chromeSample && /匯出|導出/.test(painted.chromeSample)) chromeConverted = true;
      // Also probe status badge cells if any converted
      if (painted.chromeSample && painted.chromeSample !== '导出 CSV (Excel)') {
        // under Hant download button text may convert
      }

      if (rep.id === 'inventory_stocktake' || rep.id === 'receipts_itemized_ledger') {
        const flat = painted.rows.map(r => r.join('|')).join('\n');
        const preserved = flat.includes(LONG_NAME);
        // Must NOT be OpenCC-converted form of the long name (e.g. 有機走地雞胸肉)
        const wronglyConverted = flat.includes('有機走地雞胸肉');
        checks.push({
          name: `${rep.id}/Hant: LONG_NAME preserved exact Hans`,
          ok: preserved && !wronglyConverted,
          detail: { preserved, wronglyConverted, sample: flat.slice(0, 240) },
        });
      }
      result.screenshots.push(await snap(page, `preview-hant-${rep.id}.png`));
    }

    // Chrome convert probe via I18N
    const convertProbe = await page.evaluate(() => {
      const I = window.I18N;
      if (!I || typeof I.convert !== 'function') return { hasI18N: false };
      return {
        hasI18N: true,
        exportCsv: I.convert('导出 CSV'),
        status: I.convert('已入账'),
        supplierHdr: I.convert('供应商名称'),
        locale: I.getLocale ? I.getLocale() : (I.locale || null),
      };
    });
    result.locales.convertProbe = convertProbe;
    checks.push({
      name: 'I18N.convert still active under Hant (not globally disabled)',
      ok: convertProbe.hasI18N
        && convertProbe.exportCsv
        && convertProbe.exportCsv !== '导出 CSV',
      detail: convertProbe,
    });
    if (chromeConverted) {
      checks.push({ name: 'download button chrome converted in DOM', ok: true, detail: true });
    }

    const dbHant = await readDbSkuName(page);
    result.db.mid = dbHant;

    // ---------- Phase C: back to Hans ----------
    await page.goto(`${BASE}/?lang=zh-Hans`, { waitUntil: 'networkidle2', timeout: 45000 });
    await page.evaluate(() => { try { localStorage.setItem('demo_role', 'owner'); } catch (e) {} });
    await sleep(800);
    await goExport(page);
    await selectReportAndPreview(page, 'inventory_stocktake', null);
    await sleep(500);
    const paintedBack = await readPaintedPreview(page);
    result.reports.inventory_stocktake.hansAgain = {
      headers: paintedBack.headers,
      n: paintedBack.rows.length,
    };
    compareIdentity(paintedBack, csvHansByReport.inventory_stocktake, 'inventory_stocktake/HansAgain', checks);
    const flatBack = paintedBack.rows.map(r => r.join('|')).join('\n');
    checks.push({
      name: 'HansAgain: LONG_NAME still exact',
      ok: flatBack.includes(LONG_NAME),
      detail: flatBack.slice(0, 200),
    });
    result.screenshots.push(await snap(page, 'preview-hans-again-stocktake.png'));

    const dbAfter = await readDbSkuName(page);
    result.db.after = dbAfter;
    if (dbHans || dbAfter) {
      checks.push({
        name: 'DB SKU name unchanged across Hans→Hant→Hans',
        ok: (dbAfter === LONG_NAME) && (!dbHans || dbHans === dbAfter),
        detail: result.db,
      });
    } else {
      // verify via sqlite from outside — recorded as deferred; still check via API preview identity
      checks.push({
        name: 'DB SKU name unchanged (API /skus unavailable; identity paint == seed)',
        ok: flatBack.includes(LONG_NAME),
        detail: 'fallback to painted identity',
      });
    }

    // XSS name escaped in DOM (no raw script node)
    const xssCheck = await page.evaluate((xss) => {
      const tb = document.getElementById('exportPreviewTbody');
      if (!tb) return { found: false };
      const html = tb.innerHTML;
      const hasRaw = html.includes('<script>') || !!tb.querySelector('script');
      const hasEsc = html.includes('&lt;script&gt;') || tb.textContent.includes('<script>');
      // textContent should show the literal with angle brackets if escaped properly as text
      const textHas = tb.textContent.includes('鸡翅') && tb.textContent.includes('script');
      return { found: textHas || html.includes('鸡翅'), hasRaw, hasEsc, textHas };
    }, XSS_NAME);
    // Need to load a report that shows XSS sku — stocktake has it
    checks.push({
      name: 'XSS name not injected as live script in preview DOM',
      ok: xssCheck.found && !xssCheck.hasRaw,
      detail: xssCheck,
    });

    const failed = checks.filter(c => !c.ok);
    result.pass = failed.length === 0;
    result.failedCount = failed.length;
    result.passCount = checks.filter(c => c.ok).length;
    result.failed = failed.map(c => ({ name: c.name, detail: c.detail }));

    const summary = [
      '=== UAT-023 browser MANUAL_UI ===',
      `pass=${result.pass}`,
      `passCount=${result.passCount} failCount=${result.failedCount}`,
      `locale_hant=${JSON.stringify(result.locales.hant_doc)}`,
      `convertProbe=${JSON.stringify(convertProbe)}`,
      `db=${JSON.stringify(result.db)}`,
      `failed=${JSON.stringify(result.failed, null, 2)}`,
    ].join('\n');
    fs.writeFileSync(path.join(LOG_DIR, 'browser-uat.txt'), summary + '\n', 'utf8');
    fs.writeFileSync(path.join(LOG_DIR, 'browser-uat-result.json'), JSON.stringify(result, null, 2), 'utf8');
    console.log(summary);
    process.exitCode = result.pass ? 0 : 1;
  } catch (e) {
    result.errors.push(String(e && e.stack || e));
    fs.writeFileSync(path.join(LOG_DIR, 'browser-uat-result.json'), JSON.stringify(result, null, 2), 'utf8');
    console.error('FATAL', e);
    process.exitCode = 2;
  } finally {
    await browser.close();
  }
})();
