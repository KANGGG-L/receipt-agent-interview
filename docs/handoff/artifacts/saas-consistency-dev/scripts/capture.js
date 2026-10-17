'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15410';
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
    const cards = tab ? Array.from(tab.querySelectorAll(':scope > .card, :scope > .st-wrap .card, :scope > .st-wrap .today-grid .card')).slice(0, 6).map(c => {
      const r = c.getBoundingClientRect();
      return { left: Math.round(r.left), width: Math.round(r.width), top: Math.round(r.top) };
    }) : [];
    const st = tab ? tab.querySelector('.st-wrap') : null;
    const sr = st ? st.getBoundingClientRect() : null;
    const labels = Array.from(document.querySelectorAll('#tab-report .report-date-label, #tab-report .csp-s-204')).map(el => {
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        text: el.textContent.trim(),
        w: Math.round(r.width), h: Math.round(r.height),
        whiteSpace: cs.whiteSpace,
        writingMode: cs.writingMode,
        wordBreak: cs.wordBreak,
        lineHeight: cs.lineHeight,
        fontSize: cs.fontSize,
        letterStackedSuspect: r.height > parseFloat(cs.fontSize) * 2.2
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
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      main: mr ? { left: Math.round(mr.left), width: Math.round(mr.width), padL: getComputedStyle(main).paddingLeft, maxW: getComputedStyle(main).maxWidth } : null,
      tab: tr ? { left: Math.round(tr.left), width: Math.round(tr.width) } : null,
      stWrap: sr ? { left: Math.round(sr.left), width: Math.round(sr.width), maxW: getComputedStyle(st).maxWidth } : null,
      cards, labels, tokens,
      docOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1
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
  const results = { checks: [], shots: [], metrics: {} };

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
  await sleep(500);
  const reportShot = path.join(UI, '1440-report-date-labels.png');
  await page.screenshot({ path: reportShot, fullPage: false });
  results.shots.push(reportShot);
  results.metrics['1440-report'] = await metrics(page, 'tab-report');

  const lefts = ['1440-today','1440-inventory','1440-archive','1440-settings'].map(k => {
    const m = results.metrics[k];
    const cardLeft = (m.cards && m.cards[0]) ? m.cards[0].left : (m.stWrap ? m.stWrap.left : (m.tab ? m.tab.left : null));
    return { k, cardLeft, tabLeft: m.tab && m.tab.left, mainLeft: m.main && m.main.left, stMax: m.stWrap && m.stWrap.maxW };
  });
  const cardLeftVals = lefts.map(x => x.cardLeft).filter(v => v != null);
  const alignOk = cardLeftVals.length >= 3 && (Math.max(...cardLeftVals) - Math.min(...cardLeftVals) <= 2);
  results.checks.push({ name: '1440_container_left_align', ok: alignOk, detail: lefts });

  const labelM = results.metrics['1440-report'].labels || [];
  const labelOk = labelM.length > 0 && labelM.every(l => !l.letterStackedSuspect && l.whiteSpace === 'nowrap');
  results.checks.push({ name: '1440_report_date_labels_nowrap', ok: labelOk, detail: labelM });

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
  const overflowOk = !results.metrics['390-today'].docOverflowX && !results.metrics['390-inventory'].docOverflowX;
  results.checks.push({ name: '390_no_doc_overflow', ok: overflowOk, detail: {
    today: results.metrics['390-today'].docOverflowX,
    inventory: results.metrics['390-inventory'].docOverflowX,
    settings: results.metrics['390-settings'].docOverflowX,
  }});

  const tokens = results.metrics['1440-today'].tokens;
  results.checks.push({ name: 'layout_tokens_present', ok: !!(tokens && tokens.pageMaxWidth === '1440px'), detail: tokens });

  fs.writeFileSync(path.join(LOG, 'capture-result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({ checks: results.checks, shotCount: results.shots.length }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
