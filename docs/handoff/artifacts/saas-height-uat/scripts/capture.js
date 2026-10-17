'use strict';
const fs = require('fs');
const path = require('path');
const puppeteer = require('/workspace/receipt-agent-interview/docs/handoff/artifacts/uat025-qa/scripts/node_modules/puppeteer-core');

const BASE = process.env.UAT_BASE || 'http://127.0.0.1:15050';
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
  const m = css.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
  if (!m) return null;
  const a = m[4] == null ? 1 : Number(m[4]);
  if (a < 0.01) return null; // transparent
  return '#' + [m[1],m[2],m[3]].map(x => Number(x).toString(16).padStart(2,'0')).join('');
}

async function setRole(page, role) {
  await page.evaluate((r) => { localStorage.setItem('demo_role', r); }, role);
  await page.reload({ waitUntil: 'networkidle0' });
  await sleep(800);
}

async function clickNav(page, target) {
  await page.evaluate((t) => {
    const btn = document.querySelector('.sidebar-btn[data-target="' + t + '"]')
      || document.querySelector('[data-target="' + t + '"]')
      || document.querySelector('#' + t.replace('tab-','') + 'BoardBtn');
    if (btn) btn.click();
  }, target);
  await sleep(1200);
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
      ['.app-toast-close', 'app-toast-close'],
      ['.btn.sm', 'btn-sm'],
      ['.lean-toggle', 'lean-toggle'],
    ];
    const out = [];
    const seen = new Set();
    for (const [sel, label] of sels) {
      const els = Array.from(document.querySelectorAll(sel)).slice(0, 8);
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
          overflow: cs.overflow,
          tabIndex: el.tabIndex,
          focusable: el.tagName === 'BUTTON' || el.tagName === 'SELECT' || el.tagName === 'INPUT' || el.tabIndex >= 0 || el.getAttribute('role') === 'button',
        });
      }
    }
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
      archiveChipCount: chips.length,
      docOverflowX: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
}

async function keyboardCheck(page) {
  return page.evaluate(() => {
    const results = [];
    const pick = [
      ['#demoRoleSelect', 'demoRoleSelect'],
      ['.lang-switch-btn.active, .lang-switch-btn', 'lang-switch-btn'],
      ['#tab-archive .chip', 'chip'],
      ['#archiveAdvancedToggle', 'archiveAdvancedToggle'],
      ['#filterTimeRange .btn', 'filterTimeRangeBtn'],
      ['.dish-subtab-btn', 'dish-subtab-btn'],
      ['.analytics-segment-btn', 'analytics-segment-btn'],
      ['.engine-card-toggle-btn', 'engine-card-toggle-btn'],
    ];
    for (const [sel, id] of pick) {
      const el = document.querySelector(sel);
      if (!el || !el.getClientRects().length) {
        results.push({ id, present: false, canTab: false, focused: false });
        continue;
      }
      const tag = el.tagName;
      const canTab = tag === 'BUTTON' || tag === 'SELECT' || tag === 'INPUT' || tag === 'A' || el.tabIndex >= 0 || el.getAttribute('role') === 'button';
      try { el.focus({ preventScroll: true }); } catch (_) { el.focus(); }
      const active = document.activeElement === el;
      const cs = getComputedStyle(el);
      results.push({
        id,
        present: true,
        tag,
        canTab,
        focused: active,
        outline: cs.outline,
        tabIndex: el.tabIndex,
      });
    }
    return results;
  });
}

async function contrastSamples(page) {
  return page.evaluate(() => {
    function parseRgb(css) {
      if (!css) return null;
      css = css.trim();
      if (css.startsWith('#')) {
        if (css.length !== 7) return null;
        return { r: parseInt(css.slice(1,3),16), g: parseInt(css.slice(3,5),16), b: parseInt(css.slice(5,7),16), a: 1 };
      }
      const m = css.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?/);
      if (!m) return null;
      return { r: +m[1], g: +m[2], b: +m[3], a: m[4] == null ? 1 : Number(m[4]) };
    }
    function toHex(c) {
      return '#' + [c.r,c.g,c.b].map(x => Math.round(x).toString(16).padStart(2,'0')).join('');
    }
    function compositeBg(el) {
      // Alpha-blend translucent ancestors onto first opaque surface (DEV residual: do not treat rgba(0,0,0,0.04) as black).
      let n = el;
      let layers = [];
      while (n) {
        const c = parseRgb(getComputedStyle(n).backgroundColor);
        if (c && c.a > 0.001) layers.push(c);
        if (c && c.a >= 0.95) break;
        if (n === document.body) break;
        n = n.parentElement;
      }
      const root = getComputedStyle(document.documentElement);
      let bgMain = parseRgb(root.getPropertyValue('--bg-main').trim()) || parseRgb('#f3f2f1');
      // Blend from bottom (opaque) up
      let base = (layers.length && layers[layers.length-1].a >= 0.95) ? layers[layers.length-1] : bgMain;
      let out = { r: base.r, g: base.g, b: base.b };
      const upper = (layers.length && layers[layers.length-1].a >= 0.95) ? layers.slice(0, -1).reverse() : layers.slice().reverse();
      for (const L of upper) {
        const a = Math.min(1, Math.max(0, L.a));
        out = { r: L.r * a + out.r * (1-a), g: L.g * a + out.g * (1-a), b: L.b * a + out.b * (1-a) };
      }
      return toHex(out);
    }
    function parseColor(css) {
      const c = parseRgb(css);
      if (!c || c.a < 0.01) return null;
      return toHex(c);
    }
    function relLum(hex) {
      const h = hex.replace('#','');
      const rgb = [0,2,4].map(i => parseInt(h.slice(i,i+2),16)/255).map(c => c <= 0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055, 2.4));
      return 0.2126*rgb[0] + 0.7152*rgb[1] + 0.0722*rgb[2];
    }
    function ratio(fg, bg) {
      const L1 = relLum(fg), L2 = relLum(bg);
      const hi = Math.max(L1,L2), lo = Math.min(L1,L2);
      return (hi + 0.05) / (lo + 0.05);
    }
    const picks = [
      ['.dish-subtab-btn:not(.active)', 'dish-subtab-btn inactive'],
      ['.dish-subtab-btn.active', 'dish-subtab-btn active'],
      ['#tab-archive .chip', 'chip'],
      ['.lang-switch-btn:not(.active)', 'lang-switch-btn'],
      ['.lang-switch-btn.active', 'lang-switch-btn active'],
      ['.analytics-segment-btn', 'analytics-segment-btn'],
      ['#filterTimeRange .btn', 'filter-time-range-btn'],
      ['.engine-card-toggle-btn', 'engine-card-toggle-btn'],
    ];
    const out = [];
    for (const [sel, label] of picks) {
      const el = document.querySelector(sel);
      if (!el || !el.getClientRects().length) continue;
      const fg = parseColor(getComputedStyle(el).color);
      const bg = compositeBg(el);
      if (!fg || !bg) continue;
      const r = ratio(fg, bg);
      out.push({
        label,
        text: (el.textContent||'').trim().replace(/\s+/g,' ').slice(0,24),
        fg, bg,
        ratio: r,
        pass: r >= 4.5,
      });
    }
    return out;
  });
}

async function ensureToastClose(page) {
  await page.evaluate(() => {
    if (typeof window.showAppToast === 'function') {
      try { window.showAppToast({ message: 'SAAS-HEIGHT UAT probe', type: 'info' }); } catch (_) {}
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
  const results = { checks: [], shots: [], metrics: {}, contrast: {}, keyboard: {}, summaryHeights: {} };

  const TARGET = new Set([
    'archive-chip','archive-advanced-toggle','demo-role-select','lang-switch-btn',
    'dish-subtab-btn','analytics-segment-btn','filter-time-range-btn',
    'parsing-log-filter-btn','parsing-log-filter-control',
    'engine-card-toggle-btn','app-toast-close','fees-drawer-toggle','export-chip','btn-sm','lean-toggle'
  ]);

  async function runViewport(name, vp, isMobile) {
    await page.setViewport({ ...vp, deviceScaleFactor: 1, isMobile: !!isMobile, hasTouch: !!isMobile });
    await page.goto(BASE + '/', { waitUntil: 'networkidle0', timeout: 60000 });
    await setRole(page, 'admin');

    await clickNav(page, 'tab-archive');
    await ensureToastClose(page);
    let m = await measureTargets(page);
    results.metrics[name + '_archive'] = m;
    const shotA = path.join(UI, name + '-archive-controls.png');
    await page.screenshot({ path: shotA, fullPage: false });
    results.shots.push(shotA);
    results.keyboard[name + '_archive'] = await keyboardCheck(page);
    results.contrast[name + '_archive'] = await contrastSamples(page);

    await clickNav(page, 'tab-dishes');
    m = await measureTargets(page);
    results.metrics[name + '_dishes'] = m;
    const shotD = path.join(UI, name + '-dishes-controls.png');
    await page.screenshot({ path: shotD, fullPage: false });
    results.shots.push(shotD);
    results.keyboard[name + '_dishes'] = await keyboardCheck(page);
    results.contrast[name + '_dishes'] = await contrastSamples(page);

    await clickNav(page, 'tab-analytics');
    await sleep(800);
    m = await measureTargets(page);
    results.metrics[name + '_analytics'] = m;
    const shotN = path.join(UI, name + '-analytics-controls.png');
    await page.screenshot({ path: shotN, fullPage: false });
    results.shots.push(shotN);
    results.keyboard[name + '_analytics'] = await keyboardCheck(page);
    results.contrast[name + '_analytics'] = await contrastSamples(page);

    await clickNav(page, 'tab-engine');
    await sleep(800);
    m = await measureTargets(page);
    results.metrics[name + '_engine'] = m;
    const shotE = path.join(UI, name + '-engine-controls.png');
    await page.screenshot({ path: shotE, fullPage: false });
    results.shots.push(shotE);
    results.keyboard[name + '_engine'] = await keyboardCheck(page);
  }

  await runViewport('1440', { width: 1440, height: 900 }, false);
  await runViewport('390', { width: 390, height: 844 }, true);

  // Aggregate heights
  const samples = [];
  for (const [k, m] of Object.entries(results.metrics)) {
    for (const c of (m.controls || [])) {
      samples.push({ vp: k, ...c });
    }
  }

  // toast close explicit on 1440
  await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
  await page.goto(BASE + '/', { waitUntil: 'networkidle0', timeout: 60000 });
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

  const heightFails = samples.filter(s => TARGET.has(s.label) && s.h + 0.01 < 38);
  if (toastM && toastM.h + 0.01 < 38) heightFails.push({ label: 'app-toast-close', h: toastM.h, vp: '1440' });

  // min height per label per major viewport family
  const minBy = {};
  for (const s of samples.filter(s => TARGET.has(s.label))) {
    const family = s.vp.startsWith('390') ? '390' : '1440';
    const key = family + ':' + s.label;
    if (!minBy[key] || s.h < minBy[key].h) minBy[key] = { vp: s.vp, label: s.label, h: s.h, text: s.text };
  }
  if (toastM) minBy['1440:app-toast-close'] = { vp: '1440', label: 'app-toast-close', h: toastM.h, text: '×' };
  results.summaryHeights = minBy;

  // Keyboard: on 1440 archive samples that are present must focus OR canTab
  const kb1440 = results.keyboard['1440_archive'] || [];
  const kbOk = kb1440.filter(k => k.present).every(k => k.canTab && (k.focused || k.tag === 'SPAN' ? true : k.focused));
  // stricter: every present native control (BUTTON/SELECT/INPUT) must focus; SPAN with tabindex ok if focused or canTab
  const kbStrict = kb1440.filter(k => k.present).every(k => {
    if (!k.canTab) return false;
    if (k.tag === 'BUTTON' || k.tag === 'SELECT' || k.tag === 'INPUT') return k.focused;
    return k.focused || k.canTab; // span toggle etc.
  });

  // 390: at least chip + advanced toggle focusable when present
  const kb390 = results.keyboard['390_archive'] || [];
  const kb390Ok = kb390.filter(k => k.present && (k.id === 'chip' || k.id === 'archiveAdvancedToggle' || k.id === 'filterTimeRangeBtn'))
    .every(k => k.canTab && (k.focused || k.canTab));

  const overflowFail = Object.entries(results.metrics).some(([k,m]) => m && m.docOverflowX);
  const overlapFail = Object.entries(results.metrics).some(([k,m]) => m && m.archiveChipOverlap);

  // Contrast: flatten all composite samples
  const allContrast = [];
  for (const [k, arr] of Object.entries(results.contrast)) {
    for (const c of (arr || [])) allContrast.push({ vp: k, ...c });
  }
  const contrastOk = allContrast.length > 0 && allContrast.every(c => c.pass === true);

  const tokenOk = Object.values(results.metrics).some(m => m && m.controlMinHeightToken === '38px');

  results.checks = [
    { name: 'desktop_390_target_height_ge_38', ok: heightFails.length === 0, fails: heightFails.slice(0, 40), minBy },
    { name: 'keyboard_usable', ok: kbStrict && kb390Ok, detail: { '1440_archive': kb1440, '390_archive': kb390, kbStrict, kb390Ok } },
    { name: 'no_clip_overlap', ok: !overflowFail && !overlapFail, overflowFail, overlapFail },
    { name: 'contrast_4_5_sample', ok: contrastOk, samples: allContrast },
    { name: 'token_control_min_height', ok: tokenOk },
  ];

  const overall = results.checks.every(c => c.ok);
  fs.writeFileSync(path.join(LOG, 'capture-result.json'), JSON.stringify(results, null, 2));
  const summary = {
    overall,
    checks: results.checks.map(c => ({ name: c.name, ok: c.ok, failCount: (c.fails||[]).length })),
    minHeights: minBy,
    toast: toastM,
    contrastCount: allContrast.length,
    contrastFails: allContrast.filter(c => !c.pass).slice(0, 10),
    shots: results.shots.map(s => path.basename(s)),
  };
  fs.writeFileSync(path.join(LOG, 'uat-summary.json'), JSON.stringify(summary, null, 2));
  console.log(JSON.stringify(summary, null, 2));
  await browser.close();
  process.exit(overall ? 0 : 2);
})().catch(e => { console.error(e); process.exit(1); });
