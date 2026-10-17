/**
 * UAT-025 independent QA — MANUAL_UI (puppeteer-core + system Chrome).
 * Port 15350 — fresh; avoided 15150-15300.
 *
 * Gate: real Hant/Hans UI triggers GET /api/ai-insights?lang=...;
 * response data.review_locale must match API normalize mapping.
 * UI OpenCC alone is NOT a pass criterion.
 */
'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = process.env.QA_BASE || 'http://127.0.0.1:15350';
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

const checks = [];
function record(name, ok, detail) {
  checks.push({ name, ok: !!ok, detail: detail == null ? '' : detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + String(detail).slice(0, 240) : ''}`);
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

function attachInsightsCapture(page) {
  const hits = [];
  page.on('request', (req) => {
    const u = req.url();
    if (u.includes('/api/ai-insights')) {
      hits.push({
        kind: 'request',
        url: u,
        method: req.method(),
        headers: req.headers(),
      });
    }
  });
  page.on('response', async (res) => {
    const u = res.url();
    if (!u.includes('/api/ai-insights')) return;
    let body = null;
    try { body = await res.json(); } catch (e) { body = { parse_error: String(e) }; }
    hits.push({
      kind: 'response',
      url: u,
      status: res.status(),
      body,
    });
  });
  return hits;
}

function parseLangFromUrl(url) {
  try {
    const u = new URL(url);
    return u.searchParams.get('lang');
  } catch (e) {
    const m = String(url).match(/[?&]lang=([^&]*)/);
    return m ? decodeURIComponent(m[1]) : null;
  }
}

async function prepOwner(page) {
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
  });
}

async function goInventoryAndWaitInsights(page, hits, label) {
  const before = hits.length;
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
    else if (typeof loadAiLeanInsights === 'function') loadAiLeanInsights();
  });
  // Wait for at least one new ai-insights response
  const deadline = Date.now() + 15000;
  while (Date.now() < deadline) {
    const resp = hits.filter((h, i) => i >= before && h.kind === 'response');
    if (resp.length) return resp[resp.length - 1];
    await sleep(100);
  }
  return null;
}

async function runLocaleCase(browser, locale, expectReviewLocale, tag) {
  const page = await browser.newPage();
  const hits = attachInsightsCapture(page);
  await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 1 });
  await prepOwner(page);
  await page.goto(`${BASE}/?lang=${encodeURIComponent(locale)}`, {
    waitUntil: 'networkidle2',
    timeout: 45000,
  });
  // Ensure role sticks after navigation scripts
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
  });

  const localeState = await page.evaluate(() => ({
    dataLocale: document.documentElement.getAttribute('data-locale'),
    htmlLang: document.documentElement.getAttribute('lang'),
    i18n: (window.I18N && I18N.getLocale) ? I18N.getLocale() : null,
    hasConvert: !!(window.I18N && typeof I18N.convert === 'function'),
  }));
  record(`${tag}: document/I18N locale`,
    localeState.i18n === locale || /Hant|Hans/i.test(String(localeState.dataLocale || '')),
    JSON.stringify(localeState));

  const resp = await goInventoryAndWaitInsights(page, hits, tag);
  await snap(page, `${tag}-inventory.png`);

  const reqs = hits.filter(h => h.kind === 'request');
  const lastReq = reqs.length ? reqs[reqs.length - 1] : null;
  const langQ = lastReq ? parseLangFromUrl(lastReq.url) : null;
  const reviewLocale = resp && resp.body && resp.body.data
    ? resp.body.data.review_locale
    : undefined;

  record(`${tag}: GET /api/ai-insights captured`, !!lastReq && !!resp,
    lastReq ? lastReq.url : 'no request');
  record(`${tag}: query lang=${locale}`, langQ === locale,
    `got lang=${langQ}`);
  record(`${tag}: response review_locale=${expectReviewLocale}`,
    reviewLocale === expectReviewLocale,
    `got review_locale=${JSON.stringify(reviewLocale)} status=${resp && resp.status}`);

  // Negative: OpenCC convert presence alone must NOT be treated as language pass
  const convertProbe = await page.evaluate(() => {
    const I = window.I18N;
    if (!I || typeof I.convert !== 'function') return { hasConvert: false };
    return {
      hasConvert: true,
      sample: I.convert('价格异常'),
      locale: I.getLocale ? I.getLocale() : null,
    };
  });
  record(`${tag}: OpenCC alone not used as pass (convert may exist; gate is request+review_locale)`,
    langQ === locale && reviewLocale === expectReviewLocale,
    JSON.stringify(convertProbe));

  // Also force re-call via setLocale path if available
  if (typeof page.evaluate === 'function') {
    const before = hits.length;
    await page.evaluate((loc) => {
      if (window.I18N && typeof I18N.setLocale === 'function') {
        I18N.setLocale(loc, { persist: false });
      }
      if (typeof loadAiLeanInsights === 'function') loadAiLeanInsights();
    }, locale);
    await sleep(800);
    const afterReqs = hits.filter((h, i) => i >= before && h.kind === 'request');
    const afterResp = hits.filter((h, i) => i >= before && h.kind === 'response');
    if (afterReqs.length && afterResp.length) {
      const rlang = parseLangFromUrl(afterReqs[afterReqs.length - 1].url);
      const rloc = afterResp[afterResp.length - 1].body
        && afterResp[afterResp.length - 1].body.data
        && afterResp[afterResp.length - 1].body.data.review_locale;
      record(`${tag}: setLocale+reload still sends lang`, rlang === locale, `lang=${rlang}`);
      record(`${tag}: setLocale+reload review_locale`, rloc === expectReviewLocale, `review_locale=${rloc}`);
    }
  }

  await page.close();
  return { locale, hits, langQ, reviewLocale, localeState };
}

(async () => {
  const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome-stable';
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });

  try {
    await runLocaleCase(browser, 'zh-Hant-HK', 'zh-Hant-HK', 'hant');
    await runLocaleCase(browser, 'zh-Hans', 'zh-Hans', 'hans');

    // Direct API aliases (supportive, not UI OpenCC)
    // Done via page.evaluate fetch with owner headers to keep same origin session
    const page = await browser.newPage();
    await page.goto(BASE + '/', { waitUntil: 'domcontentloaded', timeout: 30000 });
    const alias = await page.evaluate(async () => {
      async function hit(lang) {
        const url = lang == null
          ? '/api/ai-insights'
          : '/api/ai-insights?lang=' + encodeURIComponent(lang);
        const res = await fetch(url, { headers: { 'X-Role': 'owner' } });
        const j = await res.json();
        return { status: res.status, review_locale: j.data && j.data.review_locale, url };
      }
      return {
        zhHK: await hit('zh-HK'),
        zhHant: await hit('zh-Hant'),
        zhHans: await hit('zh-Hans'),
        empty: await hit(null),
      };
    });
    record('API alias zh-HK -> zh-Hant-HK', alias.zhHK.review_locale === 'zh-Hant-HK', JSON.stringify(alias.zhHK));
    record('API alias zh-Hant* -> zh-Hant-HK', alias.zhHant.review_locale === 'zh-Hant-HK', JSON.stringify(alias.zhHant));
    record('API Hans zh-Hans -> zh-Hans', alias.zhHans.review_locale === 'zh-Hans', JSON.stringify(alias.zhHans));
    record('API empty lang -> empty review_locale', alias.empty.review_locale === '', JSON.stringify(alias.empty));
    await page.close();
  } finally {
    await browser.close();
  }

  const passCount = checks.filter(c => c.ok).length;
  const failCount = checks.filter(c => !c.ok).length;
  const result = {
    pass: failCount === 0,
    passCount,
    failCount,
    checks,
    base: BASE,
    port: 15350,
  };
  fs.writeFileSync(path.join(LOG_DIR, 'browser-qa-result.json'), JSON.stringify(result, null, 2));
  fs.writeFileSync(path.join(LOG_DIR, 'browser-qa.txt'),
    checks.map(c => `${c.ok ? 'PASS' : 'FAIL'} ${c.name}${c.detail ? ' — ' + c.detail : ''}`).join('\n')
    + `\n\npass=${result.pass} passCount=${passCount} failCount=${failCount}\n`);
  console.log(`\npass=${result.pass} passCount=${passCount} failCount=${failCount}`);
  process.exit(failCount ? 1 : 0);
})().catch((err) => {
  console.error(err);
  process.exit(2);
});
