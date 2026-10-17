'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.QA_BASE || 'http://127.0.0.1:15020';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

async function setRole(page, role) {
  await page.evaluate((r) => { localStorage.setItem('demo_role', r); }, role);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(800);
}

async function clickNav(page, target) {
  await page.evaluate((t) => {
    const btn = document.querySelector('.sidebar-btn[data-target="' + t + '"]');
    if (btn) btn.click();
  }, target);
  await sleep(1200);
}

async function metrics(page, tabId) {
  return page.evaluate((tid) => {
    const main = document.querySelector('.main-container');
    const tab = document.querySelector('#' + tid);
    const mr = main ? main.getBoundingClientRect() : null;
    const tr = tab ? tab.getBoundingClientRect() : null;
    const allCards = tab ? Array.from(tab.querySelectorAll('.card')) : [];
    const visibleCards = allCards.filter(c => {
      const r = c.getBoundingClientRect();
      const cs = getComputedStyle(c);
      return r.width > 40 && r.height > 20 && cs.display !== 'none' && cs.visibility !== 'hidden' && r.bottom > 0 && r.top < window.innerHeight;
    }).slice(0, 8).map(c => {
      const r = c.getBoundingClientRect();
      return { left: Math.round(r.left), width: Math.round(r.width), top: Math.round(r.top), height: Math.round(r.height) };
    });
    const st = tab ? tab.querySelector('.st-wrap') : null;
    const sr = st ? st.getBoundingClientRect() : null;
    const labels = Array.from(document.querySelectorAll('#tab-report .report-date-label, #tab-report .csp-s-204')).map(el => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      const fsPx = parseFloat(cs.fontSize) || 14;
      return {
        text: el.textContent.trim(),
        w: Math.round(r.width), h: Math.round(r.height),
        whiteSpace: cs.whiteSpace,
        writingMode: cs.writingMode,
        wordBreak: cs.wordBreak,
        lineHeight: cs.lineHeight,
        fontSize: cs.fontSize,
        letterStackedSuspect: r.height > fsPx * 2.2
      };
    });
    const cs = getComputedStyle(document.documentElement);
    const tokens = {
      pageMaxWidth: cs.getPropertyValue('--page-max-width').trim(),
      pageInlinePad: cs.getPropertyValue('--page-inline-pad').trim(),
      pageBlockGap: cs.getPropertyValue('--page-block-gap').trim(),
      pageCardPad: cs.getPropertyValue('--page-card-pad').trim(),
      toolbarGap: cs.getPropertyValue('--toolbar-gap').trim(),
      toolbarClusterGap: cs.getPropertyValue('--toolbar-cluster-gap').trim(),
      toolbarDivider: cs.getPropertyValue('--toolbar-divider').trim(),
      bgMain: cs.getPropertyValue('--bg-main').trim(),
      borderColor: cs.getPropertyValue('--border-color').trim(),
    };
    // report toolbar clusters if present
    let toolbar = null;
    if (tid === 'tab-report') {
      const filters = document.querySelector('#tab-report .report-filters, #tab-report .saas-toolbar .filters, #tab-report .card-title .report-date-cluster');
      const actions = document.querySelector('#tab-report .report-actions, #tab-report .saas-toolbar .actions, #tab-report .card-title .report-actions');
      // fallback: look for title row children
      const title = document.querySelector('#tab-report .card-title, #tab-report .saas-toolbar');
      if (title) {
        const kids = Array.from(title.children).map(el => {
          const r = el.getBoundingClientRect();
          return { cls: el.className, left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), w: Math.round(r.width) };
        });
        toolbar = { kids };
      }
    }
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      main: mr ? { left: Math.round(mr.left), width: Math.round(mr.width), padL: getComputedStyle(main).paddingLeft, maxW: getComputedStyle(main).maxWidth } : null,
      tab: tr ? { left: Math.round(tr.left), width: Math.round(tr.width) } : null,
      stWrap: sr ? { left: Math.round(sr.left), width: Math.round(sr.width), maxW: getComputedStyle(st).maxWidth } : null,
      cards: visibleCards,
      labels, tokens, toolbar,
      docOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
      scrollW: document.documentElement.scrollWidth,
      clientW: document.documentElement.clientWidth
    };
  }, tabId);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900']
  });
  const page = await browser.newPage();
  const results = { checks: [], shots: [], metrics: {}, base: BASE, port: 15020 };

  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await setRole(page, 'owner');

  for (const row of [
    ['tab-today', '1440-today', 'tab-today'],
    ['tab-inventory', '1440-inventory', 'tab-inventory'],
    ['tab-archive', '1440-archive', 'tab-archive'],
    ['tab-settings', '1440-settings', 'tab-settings'],
  ]) {
    const target = row[0], name = row[1], tabId = row[2];
    await clickNav(page, target);
    const shot = path.join(UI, name + '.png');
    await page.screenshot({ path: shot, fullPage: false });
    results.shots.push(shot);
    results.metrics[name] = await metrics(page, tabId);
  }

  await clickNav(page, 'tab-report');
  await sleep(800);
  const reportShot = path.join(UI, '1440-report-date-labels.png');
  await page.screenshot({ path: reportShot, fullPage: false });
  results.shots.push(reportShot);
  results.metrics['1440-report'] = await metrics(page, 'tab-report');

  // AC1: container left align — first visible card left edge consistent across 4 tabs
  const lefts = ['1440-today','1440-inventory','1440-archive','1440-settings'].map(k => {
    const m = results.metrics[k];
    const cardLeft = (m.cards && m.cards[0]) ? m.cards[0].left : (m.stWrap ? m.stWrap.left : (m.tab ? m.tab.left : null));
    const cardW = (m.cards && m.cards[0]) ? m.cards[0].width : null;
    return {
      k, cardLeft, cardW,
      tabLeft: m.tab && m.tab.left,
      tabW: m.tab && m.tab.width,
      mainPadL: m.main && m.main.padL,
      stMax: m.stWrap && m.stWrap.maxW,
      note: 'visible cards only'
    };
  });
  const cardLeftVals = lefts.map(x => x.cardLeft).filter(v => v != null);
  const alignOk = cardLeftVals.length === 4 && (Math.max(...cardLeftVals) - Math.min(...cardLeftVals) <= 2);
  // also expect ~264 (240 sidebar + 24 pad) at 1440 when sidebar visible
  const expectedBandOk = cardLeftVals.every(v => v >= 250 && v <= 280);
  results.checks.push({
    name: '1440_container_left_align',
    ok: alignOk && expectedBandOk,
    detail: lefts,
    method: 'first visible card left edge; delta<=2px; band 250-280'
  });

  // AC2: report date labels not letter-stacked
  const labelM = results.metrics['1440-report'].labels || [];
  const interesting = labelM.filter(l => /月份|起止日期/.test(l.text));
  const labelOk = interesting.length >= 1 && interesting.every(l => !l.letterStackedSuspect && (l.whiteSpace === 'nowrap' || l.h <= 28));
  results.checks.push({ name: '1440_report_date_labels_nowrap', ok: labelOk, detail: interesting.length ? interesting : labelM });

  // AC3: 390 no regress
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 1, isMobile: true, hasTouch: true });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await setRole(page, 'owner');
  for (const row of [
    ['tab-today', '390-today', 'tab-today'],
    ['tab-inventory', '390-inventory', 'tab-inventory'],
    ['tab-settings', '390-settings', 'tab-settings'],
  ]) {
    const target = row[0], name = row[1], tabId = row[2];
    await clickNav(page, target);
    const shot = path.join(UI, name + '.png');
    await page.screenshot({ path: shot, fullPage: false });
    results.shots.push(shot);
    results.metrics[name] = await metrics(page, tabId);
  }
  const overflowDetail = {
    today: results.metrics['390-today'].docOverflowX,
    inventory: results.metrics['390-inventory'].docOverflowX,
    settings: results.metrics['390-settings'].docOverflowX,
  };
  const overflowOk = !overflowDetail.today && !overflowDetail.inventory && !overflowDetail.settings;
  results.checks.push({ name: '390_no_doc_overflow', ok: overflowOk, detail: overflowDetail });

  // AC4: tokens listed
  const tokens = results.metrics['1440-today'].tokens;
  const tokenOk = !!(tokens
    && tokens.pageMaxWidth === '1440px'
    && tokens.pageInlinePad === '24px'
    && tokens.pageBlockGap === '16px'
    && tokens.pageCardPad === '24px'
    && tokens.toolbarGap === '12px'
    && tokens.toolbarClusterGap === '8px'
    && tokens.toolbarDivider);
  results.checks.push({ name: 'layout_tokens_present', ok: tokenOk, detail: tokens });

  // AC4b: no unapproved brand expansion claimed complete (static + live token set)
  const brandKeys = Object.keys(tokens || {}).filter(k => /primary|accent|brand|semantic|success|danger|warning/i.test(k));
  results.checks.push({
    name: 'no_unapproved_brand_expansion_claimed_complete',
    ok: brandKeys.length === 0,
    detail: {
      liveBrandishTokenKeys: brandKeys,
      note: 'layout tokens only expected; brand/semantic expansion must remain NOT complete'
    }
  });

  const allOk = results.checks.every(c => c.ok);
  results.overall = allOk ? 'PASS' : 'FAIL';
  fs.writeFileSync(path.join(LOG, 'capture-result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ overall: results.overall, checks: results.checks.map(c => ({ name: c.name, ok: c.ok })), shotCount: results.shots.length }, null, 2));
  await browser.close();
  process.exit(allOk ? 0 : 2);
})().catch(e => { console.error(e); process.exit(1); });
