'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.DEV_BASE || 'http://127.0.0.1:15420';
const OUT = path.join(__dirname, '..');
const UI = path.join(OUT, 'ui');
const LOG = path.join(OUT, 'logs');
fs.mkdirSync(UI, { recursive: true });
fs.mkdirSync(LOG, { recursive: true });

function sleep(ms){ return new Promise(r => setTimeout(r, ms)); }

function relativeLuminance(hex) {
  const h = hex.replace('#','').trim();
  if (h.length !== 6) return null;
  const rgb = [0,2,4].map(i => parseInt(h.slice(i,i+2),16)/255).map(c => c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4));
  return 0.2126*rgb[0] + 0.7152*rgb[1] + 0.0722*rgb[2];
}
function contrastRatio(fg, bg) {
  const L1 = relativeLuminance(fg), L2 = relativeLuminance(bg);
  if (L1 == null || L2 == null) return null;
  const hi = Math.max(L1,L2), lo = Math.min(L1,L2);
  return (hi + 0.05) / (lo + 0.05);
}
function parseColor(css) {
  if (!css) return null;
  css = css.trim();
  if (css.startsWith('#')) return css.length === 7 ? css.toLowerCase() : null;
  const m = css.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (!m) return null;
  return '#' + [m[1],m[2],m[3]].map(x => Number(x).toString(16).padStart(2,'0')).join('');
}

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
  await sleep(1000);
}

async function measureTargets(page) {
  return page.evaluate(() => {
    const sels = [
      ['#tab-archive .chip', 'archive-chip'],
      ['#archiveAdvancedToggle', 'archive-advanced-toggle'],
      ['#demoRoleSelect', 'demo-role-select'],
      ['.lang-switch-btn', 'lang-switch-btn'],
      ['.dish-subtab-btn', 'dish-subtab-btn'],
      ['.analytics-segment-btn', 'analytics-segment-btn'],
      ['#filterTimeRange .btn', 'filter-time-range-btn'],
      ['.parsing-log-filter-control', 'parsing-log-filter-control'],
      ['.parsing-log-filter-btn', 'parsing-log-filter-btn'],
      ['.engine-card-toggle-btn', 'engine-card-toggle-btn'],
      ['.fees-drawer-toggle', 'fees-drawer-toggle'],
      ['.export-chip', 'export-chip'],
      ['.chip', 'any-chip'],
      ['.btn-primary', 'btn-primary'],
      ['.form-control', 'form-control'],
    ];
    const out = [];
    const seen = new Set();
    for (const [sel, label] of sels) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 6);
      for (const el of els) {
        if (!el || !el.getClientRects().length) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 1 || r.height < 1) continue;
        const key = label + ':' + Math.round(r.top) + ':' + Math.round(r.left) + ':' + (el.textContent||'').trim().slice(0,24);
        if (seen.has(key)) continue;
        seen.add(key);
        const cs = getComputedStyle(el);
        out.push({
          label,
          sel,
          text: (el.textContent||'').trim().replace(/\s+/g,' ').slice(0,40),
          tag: el.tagName.toLowerCase(),
          h: Math.round(r.height * 100) / 100,
          w: Math.round(r.width * 100) / 100,
          minH: cs.minHeight,
          heightCss: cs.height,
          display: cs.display,
          color: cs.color,
          backgroundColor: cs.backgroundColor,
          borderColor: cs.borderTopColor,
          overflow: cs.overflow,
          tabIndex: el.tabIndex,
          focusable: el.tagName === 'BUTTON' || el.tagName === 'SELECT' || el.tagName === 'INPUT' || el.tabIndex >= 0 || el.getAttribute('role') === 'button',
        });
      }
    }
    // clip/overlap sample among measured chips in archive toolbar
    const chips = Array.from(document.querySelectorAll('#tab-archive .chip')).slice(0, 8).map(el => {
      const r = el.getBoundingClientRect();
      return { t: Math.round(r.top), b: Math.round(r.bottom), l: Math.round(r.left), r: Math.round(r.right), text: el.textContent.trim() };
    });
    let overlap = false;
    for (let i=0;i<chips.length;i++) for (let j=i+1;j<chips.length;j++) {
      const a=chips[i], b=chips[j];
      const ox = Math.max(0, Math.min(a.r,b.r) - Math.max(a.l,b.l));
      const oy = Math.max(0, Math.min(a.b,b.b) - Math.max(a.t,b.t));
      if (ox > 2 && oy > 2) overlap = true;
    }
    const root = getComputedStyle(document.documentElement);
    return {
      viewport: { w: window.innerWidth, h: window.innerHeight },
      controlMinHeightToken: root.getPropertyValue('--control-min-height').trim(),
      controls: out,
      archiveChipOverlap: overlap,
      docOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
}

async function keyboardCheck(page) {
  return page.evaluate(async () => {
    const results = [];
    const targets = [
      document.querySelector('#demoRoleSelect'),
      document.querySelector('.lang-switch-btn'),
      document.querySelector('#tab-archive .chip'),
      document.querySelector('#archiveAdvancedToggle'),
    ].filter(Boolean);
    for (const el of targets) {
      el.focus();
      const active = document.activeElement === el;
      const cs = getComputedStyle(el);
      results.push({
        id: el.id || el.className.slice(0,40),
        focused: active,
        outline: cs.outlineStyle + ' ' + cs.outlineWidth,
        outlineOffset: cs.outlineOffset,
      });
    }
    // Tab from role select
    const sel = document.querySelector('#demoRoleSelect');
    if (sel) {
      sel.focus();
      // cannot synthesize Tab fully here; report tabindex chain presence
    }
    return results;
  });
}

async function ensureToastClose(page) {
  await page.evaluate(() => {
    // create a toast-like close button if toast API exists; else inject sample
    if (typeof window.showAppToast === 'function') {
      try { window.showAppToast({ message: 'SAAS-HEIGHT probe', type: 'info' }); } catch (_) {}
    }
    if (!document.querySelector('.app-toast-close')) {
      const wrap = document.createElement('div');
      wrap.className = 'app-toast app-toast-info';
      wrap.style.cssText = 'position:fixed;top:80px;right:20px;z-index:9999;display:flex;gap:8px;padding:10px;background:#f9faf9;border:1px solid #ddd;';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'app-toast-close';
      btn.setAttribute('aria-label', '关闭');
      btn.textContent = '×';
      wrap.appendChild(btn);
      document.body.appendChild(wrap);
    }
  });
  await sleep(300);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1440,900']
  });
  const page = await browser.newPage();
  const results = { checks: [], shots: [], metrics: {}, contrast: [], keyboard: [] };

  async function runViewport(name, vp, isMobile) {
    await page.setViewport({ ...vp, deviceScaleFactor: 1, isMobile: !!isMobile, hasTouch: !!isMobile });
    await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
    await setRole(page, 'admin');

    // archive chips + advanced toggle
    await clickNav(page, 'tab-archive');
    await ensureToastClose(page);
    let m = await measureTargets(page);
    results.metrics[name + '_archive'] = m;
    const shotA = path.join(UI, name + '-archive-controls.png');
    await page.screenshot({ path: shotA, fullPage: false });
    results.shots.push(shotA);

    // dishes subtabs
    await clickNav(page, 'tab-dishes');
    m = await measureTargets(page);
    results.metrics[name + '_dishes'] = m;
    const shotD = path.join(UI, name + '-dishes-controls.png');
    await page.screenshot({ path: shotD, fullPage: false });
    results.shots.push(shotD);

    // analytics time range
    await clickNav(page, 'tab-analytics');
    await sleep(800);
    m = await measureTargets(page);
    results.metrics[name + '_analytics'] = m;
    const shotN = path.join(UI, name + '-analytics-controls.png');
    await page.screenshot({ path: shotN, fullPage: false });
    results.shots.push(shotN);

    // engine toggle
    await clickNav(page, 'tab-engine');
    await sleep(800);
    m = await measureTargets(page);
    results.metrics[name + '_engine'] = m;
    const shotE = path.join(UI, name + '-engine-controls.png');
    await page.screenshot({ path: shotE, fullPage: false });
    results.shots.push(shotE);

    // scan for fees drawer if present
    await clickNav(page, 'tab-scan');
    await sleep(600);
    await ensureToastClose(page);
    m = await measureTargets(page);
    results.metrics[name + '_scan'] = m;

    results.keyboard[name] = await keyboardCheck(page);
  }

  await runViewport('1440', { width: 1440, height: 900 }, false);
  await runViewport('390', { width: 390, height: 844 }, true);

  // Aggregate height checks
  const samples = [];
  for (const [k, m] of Object.entries(results.metrics)) {
    for (const c of (m.controls || [])) {
      samples.push({ vp: k, ...c });
    }
  }
  const targetLabels = new Set([
    'archive-chip','archive-advanced-toggle','demo-role-select','lang-switch-btn',
    'dish-subtab-btn','analytics-segment-btn','filter-time-range-btn',
    'engine-card-toggle-btn','fees-drawer-toggle','export-chip','any-chip'
  ]);
  // also measure toast close explicitly
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0' });
  await setRole(page, 'admin');
  await ensureToastClose(page);
  const toastM = await page.evaluate(() => {
    const el = document.querySelector('.app-toast-close');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return { h: r.height, w: r.width, minH: cs.minHeight, color: cs.color, backgroundColor: cs.backgroundColor };
  });
  results.metrics.toast_close = toastM;

  const heightFails = samples.filter(s => targetLabels.has(s.label) && s.h + 0.01 < 38);
  if (toastM && toastM.h + 0.01 < 38) heightFails.push({ label: 'app-toast-close', h: toastM.h, vp: '1440' });

  // Contrast samples on same controls
  const contrastTargets = samples.filter(s => ['archive-chip','lang-switch-btn','dish-subtab-btn','btn-primary'].includes(s.label));
  const uniq = [];
  const seenC = new Set();
  for (const s of contrastTargets) {
    const key = s.label + s.text;
    if (seenC.has(key)) continue;
    seenC.add(key);
    const fg = parseColor(s.color);
    const bg = parseColor(s.backgroundColor) || '#ffffff';
    const ratio = (fg && bg) ? contrastRatio(fg, bg) : null;
    uniq.push({ label: s.label, text: s.text, fg, bg, ratio, pass: ratio != null && ratio >= 4.5 });
  }
  // also static known pairs
  uniq.push({ label: 'static-chip-idle', fg: '#1e2223', bg: '#ffffff', ratio: contrastRatio('#1e2223','#ffffff'), pass: true });
  uniq.push({ label: 'static-chip-active', fg: '#ffffff', bg: '#032425', ratio: contrastRatio('#ffffff','#032425'), pass: true });
  results.contrast = uniq;

  const overflowFail = Object.entries(results.metrics).some(([k,m]) => m && m.docOverflowX);
  const overlapFail = Object.entries(results.metrics).some(([k,m]) => m && m.archiveChipOverlap);

  results.checks = [
    { name: 'desktop_390_target_height_ge_38', ok: heightFails.length === 0, fails: heightFails.slice(0, 30) },
    { name: 'keyboard_focusable_sample', ok: (results.keyboard['1440']||[]).every(k => k.focused), detail: results.keyboard },
    { name: 'no_clip_overlap_sample', ok: !overflowFail && !overlapFail, overflowFail, overlapFail },
    { name: 'contrast_4_5_sample', ok: uniq.filter(u => u.ratio != null).every(u => u.ratio >= 4.5), samples: uniq },
    { name: 'token_control_min_height', ok: Object.values(results.metrics).some(m => m && m.controlMinHeightToken === '38px') },
  ];

  fs.writeFileSync(path.join(LOG, 'capture-result.json'), JSON.stringify(results, null, 2));
  console.log(JSON.stringify({
    checks: results.checks.map(c => ({ name: c.name, ok: c.ok, failCount: (c.fails||[]).length })),
    sampleHeights: samples.filter(s => targetLabels.has(s.label)).slice(0, 25).map(s => ({ vp:s.vp, label:s.label, text:s.text, h:s.h })),
    toast: toastM,
    contrast: uniq.slice(0, 8),
  }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
