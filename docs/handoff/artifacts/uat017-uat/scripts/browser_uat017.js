/**
 * UAT-017 independent UAT — browser MANUAL_UI (puppeteer-core + system Chrome).
 * Port 15250 — fresh isolation; do NOT reuse 15150-15240 (QA used 15240).
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:15250';
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function findDialog(node, pred, out = []) {
  if (!node) return out;
  if (node.role === 'dialog' || node.role === 'alertdialog') out.push(node);
  if (pred && pred(node)) out.push(node);
  (node.children || []).forEach(c => findDialog(c, pred, out));
  return out;
}

function walkFind(node, pred, out = []) {
  if (!node) return out;
  if (pred(node)) out.push(node);
  (node.children || []).forEach(c => walkFind(c, pred, out));
  return out;
}

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

async function goArchiveAndSelectSupplier(page) {
  // Prefer JS navigation so 390 mobile (sidebar may be off-canvas) still works
  await page.evaluate(() => {
    try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
    // Activate archive tab via shared switch if present
    const btn = document.querySelector('.sidebar-btn[data-target="tab-archive"]');
    if (btn) btn.click();
    // Ensure archive visible even if layout hid sidebar
    document.querySelectorAll('.tab-content, [id^="tab-"]').forEach(t => {
      if (t.id && t.id.startsWith('tab-') && t.id !== 'tab-archive') {
        t.classList.add('hide');
      }
    });
    const tab = document.getElementById('tab-archive');
    if (tab) tab.classList.remove('hide');
    document.querySelectorAll('.sidebar-btn').forEach(b => b.classList.toggle('active', b.getAttribute('data-target') === 'tab-archive'));
    // Force payables pane
    if (typeof showArchivePane === 'function') {
      showArchivePane('payables');
    } else {
      try { sessionStorage.setItem('arcPane', 'payables'); } catch (e) {}
      document.querySelectorAll('#tab-archive [data-arc-pane]').forEach(el => {
        el.classList.toggle('arc-pane-hidden', el.dataset.arcPane !== 'payables');
      });
      const payTab = document.getElementById('arcTab-payables');
      if (payTab) {
        document.querySelectorAll('.arc-subtab').forEach(b => b.setAttribute('aria-selected', 'false'));
        payTab.setAttribute('aria-selected', 'true');
      }
    }
  });
  await sleep(500);

  await page.waitForFunction(() => {
    const card = document.getElementById('payablesCard');
    const sel = document.getElementById('finSupplier');
    if (!card || !sel) return false;
    const hidden = card.classList.contains('arc-pane-hidden') || card.classList.contains('hide');
    return !hidden && sel.options && sel.options.length > 1;
  }, { timeout: 15000 });

  // Select supplier via evaluate (select() can fail if control scrolled off)
  const sid = await page.evaluate(() => {
    const sel = document.getElementById('finSupplier');
    if (!sel) return null;
    sel.value = '1';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    if (typeof finState !== 'undefined') finState.supplierId = Number(sel.value) || null;
    return (typeof finState !== 'undefined') ? finState.supplierId : Number(sel.value);
  });
  await sleep(150);
  return sid;
}

async function openCreateRecon(page) {
  // Focus the trigger button then click so activeElement is the trigger
  const btnSel = 'button[data-csp-click*="openReconModal"]';
  await page.waitForSelector(btnSel, { timeout: 10000 });
  // Scroll into view; on 390 ensure payables actions visible
  await page.evaluate((sel) => {
    const b = document.querySelector(sel);
    if (b) b.scrollIntoView({ block: 'center', inline: 'nearest' });
  }, btnSel);
  await sleep(100);
  const triggerInfo = await page.evaluate((sel) => {
    const b = document.querySelector(sel);
    if (b) b.focus();
    return {
      tag: b ? b.tagName : null,
      text: b ? b.textContent.trim() : null,
      isActive: document.activeElement === b,
    };
  }, btnSel);
  // Prefer real click; fall back to JS click if hit-test fails (mobile chrome)
  try {
    await page.click(btnSel, { delay: 20 });
  } catch (e) {
    await page.evaluate((sel) => {
      const b = document.querySelector(sel);
      if (b) { b.focus(); b.click(); }
    }, btnSel);
  }
  await page.waitForFunction(() => {
    const m = document.getElementById('reconCreateModal');
    return m && !m.classList.contains('hide');
  }, { timeout: 8000 });
  await sleep(150);
  return triggerInfo;
}

async function modalAria(page) {
  return page.evaluate(() => {
    const m = document.getElementById('reconCreateModal');
    if (!m) return null;
    const title = document.getElementById('reconCreateTitle');
    const hint = document.getElementById('reconSupplierHint');
    const focus = document.activeElement;
    const style = window.getComputedStyle(m);
    const rect = m.getBoundingClientRect();
    const panel = m.querySelector('.modal-panel, .modal-content, .modal-box, .modal') || m.firstElementChild;
    const panelRect = panel ? panel.getBoundingClientRect() : null;
    return {
      hidden: m.classList.contains('hide'),
      role: m.getAttribute('role'),
      ariaModal: m.getAttribute('aria-modal'),
      labelledby: m.getAttribute('aria-labelledby'),
      describedby: m.getAttribute('aria-describedby'),
      titleText: title ? title.textContent.trim() : null,
      hintText: hint ? hint.textContent.trim() : null,
      activeId: focus ? (focus.id || focus.tagName) : null,
      display: style.display,
      visibility: style.visibility,
      rect: { w: rect.width, h: rect.height, t: rect.top, l: rect.left },
      panelRect: panelRect ? { w: panelRect.width, h: panelRect.height, t: panelRect.top, l: panelRect.left, b: panelRect.bottom, r: panelRect.right } : null,
      cancelVisible: !!(document.getElementById('btnCancelReconCreate')),
      submitVisible: !!(document.getElementById('btnSubmitReconCreate')),
      cancelText: (document.getElementById('btnCancelReconCreate') || {}).textContent,
      submitText: (document.getElementById('btnSubmitReconCreate') || {}).textContent,
      stackLen: (typeof _activeAccessibleModals !== 'undefined' && Array.isArray(_activeAccessibleModals))
        ? _activeAccessibleModals.length : null,
      stackHasTrigger: (typeof _activeAccessibleModals !== 'undefined' && _activeAccessibleModals[0] && !!_activeAccessibleModals[0].trigger),
    };
  });
}

async function run() {
  const result = {
    port: 15250,
    pass: false,
    criteria: {},
    errors: [],
    ax: null,
    screenshots: [],
  };

  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome-stable',
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });

  try {
    const context = await browser.createBrowserContext();
    const page = await context.newPage();
    page.setDefaultTimeout(20000);
    await page.setViewport({ width: 1280, height: 800 });

    await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 45000 });
    await sleep(500);

    // Ensure owner demo role
    await page.evaluate(() => {
      try { localStorage.setItem('demo_role', 'owner'); } catch (e) {}
      if (typeof window.setRole === 'function') window.setRole('owner');
    });
    await page.reload({ waitUntil: 'networkidle2', timeout: 45000 });
    await sleep(400);

    const sid = await goArchiveAndSelectSupplier(page);
    result.supplierId = sid;
    if (!sid) result.errors.push('no supplierId after select');

    // ---- Criterion 1: open → dialog + name (AXTree) ----
    const triggerInfo = await openCreateRecon(page);
    result.triggerInfo = triggerInfo;
    const aria1 = await modalAria(page);
    result.criteria.openAria = aria1;
    await snap(page, 'open-dialog-desktop-hans.png').then(p => result.screenshots.push(p));

    const axSnap = await page.accessibility.snapshot({ interestingOnly: false });
    const dialogs = walkFind(axSnap, n => n.role === 'dialog' || n.role === 'alertdialog');
    const named = dialogs.filter(d => (d.name || '').includes('发起对账') || (d.name || '').includes('對賬') || (d.name || '').includes('对账'));
    result.ax = {
      dialogCount: dialogs.length,
      dialogNames: dialogs.map(d => d.name || null),
      namedMatch: named.length > 0,
      namedNames: named.map(d => d.name),
    };
    const c1 = !!(
      aria1 && aria1.role === 'dialog' && aria1.ariaModal === 'true'
      && aria1.labelledby === 'reconCreateTitle' && aria1.describedby === 'reconSupplierHint'
      && aria1.titleText && aria1.titleText.includes('发起对账')
      && !aria1.hidden
      && (result.ax.namedMatch || result.ax.dialogCount >= 1)
    );
    result.criteria.c1_dialog_name = { pass: c1, detail: { aria: aria1, ax: result.ax } };

    // ---- Criterion 2: Tab trap + Esc focus restore ----
    // Preferred focus should be #reconPeriodType
    const focusAfterOpen = await page.evaluate(() => document.activeElement && document.activeElement.id);
    result.criteria.preferredFocus = focusAfterOpen;

    // Collect focusable ids inside modal, Tab through, ensure we stay inside
    const tabCycle = await page.evaluate(async () => {
      const modal = document.getElementById('reconCreateModal');
      function focusables(root) {
        const sel = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
        return Array.from(root.querySelectorAll(sel)).filter(el => {
          const s = window.getComputedStyle(el);
          if (s.display === 'none' || s.visibility === 'hidden') return false;
          const r = el.getBoundingClientRect();
          return r.width > 0 && r.height > 0;
        });
      }
      const list = focusables(modal);
      const ids = list.map(el => el.id || el.getAttribute('aria-label') || el.tagName);
      return { count: list.length, ids };
    });
    result.criteria.tabFocusables = tabCycle;

    // Simulate Tab key events and record activeElement after each
    const tabTrail = [];
    const nTabs = Math.max(tabCycle.count + 2, 4);
    for (let i = 0; i < nTabs; i++) {
      await page.keyboard.press('Tab');
      await sleep(40);
      const info = await page.evaluate(() => {
        const ae = document.activeElement;
        const modal = document.getElementById('reconCreateModal');
        return {
          id: ae ? (ae.id || ae.getAttribute('aria-label') || ae.tagName) : null,
          inside: modal && ae ? modal.contains(ae) : false,
        };
      });
      tabTrail.push(info);
    }
    const tabAllInside = tabTrail.length > 0 && tabTrail.every(t => t.inside);
    result.criteria.tabTrail = tabTrail;
    result.criteria.c2_tab_trap = { pass: tabAllInside && tabCycle.count >= 2, detail: { tabAllInside, count: tabCycle.count } };

    // Esc closes and restores trigger focus
    // Re-open fresh for Esc (current may have focus elsewhere)
    await page.keyboard.press('Escape');
    await sleep(200);
    const afterEsc = await page.evaluate((btnSel) => {
      const m = document.getElementById('reconCreateModal');
      const btn = document.querySelector(btnSel);
      return {
        hidden: !m || m.classList.contains('hide'),
        activeIsTrigger: document.activeElement === btn,
        activeId: document.activeElement ? (document.activeElement.id || document.activeElement.tagName) : null,
        activeText: document.activeElement ? document.activeElement.textContent.trim().slice(0, 40) : null,
        stackLen: (typeof _activeAccessibleModals !== 'undefined') ? _activeAccessibleModals.length : null,
      };
    }, 'button[data-csp-click*="openReconModal"]');
    result.criteria.afterEsc = afterEsc;
    const c2esc = !!(afterEsc.hidden && (afterEsc.activeIsTrigger || (afterEsc.activeText && afterEsc.activeText.includes('发起对账'))));
    result.criteria.c2_esc_restore = { pass: c2esc, detail: afterEsc };

    // ---- Criterion 3: cancel / submit usable; Hans/Hant & 390 no occlusion ----
    // Re-open and click cancel
    await openCreateRecon(page);
    await sleep(100);
    await page.click('#btnCancelReconCreate');
    await sleep(200);
    const afterCancel = await page.evaluate(() => {
      const m = document.getElementById('reconCreateModal');
      return {
        hidden: !m || m.classList.contains('hide'),
        stackLen: (typeof _activeAccessibleModals !== 'undefined') ? _activeAccessibleModals.length : null,
      };
    });
    result.criteria.afterCancel = afterCancel;

    // Re-open and verify submit button is enabled / clickable (do not assert success if API needs more data —
    // assert control is present, visible, enabled, and handler wired)
    await openCreateRecon(page);
    const submitInfo = await page.evaluate(() => {
      const b = document.getElementById('btnSubmitReconCreate');
      const c = document.getElementById('btnCancelReconCreate');
      const vis = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      return {
        submitVisible: vis(b),
        submitDisabled: b ? b.disabled : null,
        submitText: b ? b.textContent.trim() : null,
        cancelVisible: vis(c),
        cancelText: c ? c.textContent.trim() : null,
        hasSubmitHandler: !!(b && b.getAttribute('data-csp-click') && b.getAttribute('data-csp-click').includes('submitReconCreate')),
        hasCancelHandler: !!(c && c.getAttribute('data-csp-click') && c.getAttribute('data-csp-click').includes('closeModalById')),
      };
    });
    result.criteria.submitCancel = submitInfo;
    await snap(page, 'open-dialog-controls.png').then(p => result.screenshots.push(p));

    // Close via Esc before locale/viewport tests
    await page.keyboard.press('Escape');
    await sleep(150);

    // Hans (default) @ 390
    await page.setViewport({ width: 390, height: 844 });
    await sleep(200);
    // ensure locale Hans
    await page.evaluate(() => {
      if (window.I18N && typeof I18N.setLocale === 'function') I18N.setLocale('zh-Hans');
    });
    await sleep(300);
    await goArchiveAndSelectSupplier(page);
    await openCreateRecon(page);
    const hans390 = await page.evaluate(() => {
      const m = document.getElementById('reconCreateModal');
      const title = document.getElementById('reconCreateTitle');
      const cancel = document.getElementById('btnCancelReconCreate');
      const submit = document.getElementById('btnSubmitReconCreate');
      const panel = m.querySelector('.modal-panel, .modal-content, .modal-box') || m.firstElementChild || m;
      const pr = panel.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const occluded = pr.right > vw + 2 || pr.bottom > vh + 2 || pr.left < -2 || pr.top < -2;
      const vis = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
      };
      return {
        locale: (window.I18N && I18N.getLocale) ? I18N.getLocale() : 'unknown',
        titleText: title ? title.textContent.trim() : null,
        panel: { w: pr.width, h: pr.height, t: pr.top, l: pr.left, r: pr.right, b: pr.bottom },
        viewport: { w: vw, h: vh },
        occluded,
        cancelVisibleInVp: vis(cancel),
        submitVisibleInVp: vis(submit),
        titleVisibleInVp: vis(title),
      };
    });
    result.criteria.hans390 = hans390;
    await snap(page, 'open-dialog-390-hans.png').then(p => result.screenshots.push(p));
    await page.keyboard.press('Escape');
    await sleep(150);

    // Hant @ 390
    await page.evaluate(() => {
      if (window.I18N && typeof I18N.setLocale === 'function') I18N.setLocale('zh-Hant-HK');
      else if (window.I18N && typeof I18N.setLocale === 'function') I18N.setLocale('zh-Hant');
    });
    await sleep(400);
    await goArchiveAndSelectSupplier(page);
    await openCreateRecon(page);
    const hant390 = await page.evaluate(() => {
      const m = document.getElementById('reconCreateModal');
      const title = document.getElementById('reconCreateTitle');
      const cancel = document.getElementById('btnCancelReconCreate');
      const submit = document.getElementById('btnSubmitReconCreate');
      const panel = m.querySelector('.modal-panel, .modal-content, .modal-box') || m.firstElementChild || m;
      const pr = panel.getBoundingClientRect();
      const vw = window.innerWidth, vh = window.innerHeight;
      const occluded = pr.right > vw + 2 || pr.bottom > vh + 2 || pr.left < -2 || pr.top < -2;
      const vis = (el) => {
        if (!el) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0 && r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
      };
      return {
        locale: (window.I18N && I18N.getLocale) ? I18N.getLocale() : 'unknown',
        titleText: title ? title.textContent.trim() : null,
        panel: { w: pr.width, h: pr.height, t: pr.top, l: pr.left, r: pr.right, b: pr.bottom },
        viewport: { w: vw, h: vh },
        occluded,
        cancelVisibleInVp: vis(cancel),
        submitVisibleInVp: vis(submit),
        titleVisibleInVp: vis(title),
      };
    });
    result.criteria.hant390 = hant390;
    await snap(page, 'open-dialog-390-hant.png').then(p => result.screenshots.push(p));
    await page.keyboard.press('Escape');
    await sleep(100);

    const c3 = !!(
      afterCancel.hidden
      && submitInfo.submitVisible && submitInfo.cancelVisible
      && submitInfo.hasSubmitHandler && submitInfo.hasCancelHandler
      && !hans390.occluded && hans390.cancelVisibleInVp && hans390.submitVisibleInVp
      && !hant390.occluded && hant390.cancelVisibleInVp && hant390.submitVisibleInVp
    );
    result.criteria.c3_controls_locale_390 = { pass: c3, detail: { afterCancel, submitInfo, hans390, hant390 } };

    // ---- Negative: unrelated modals not rewritten ----
    const negative = await page.evaluate(() => {
      const pay = document.getElementById('paymentModal');
      const resolve = document.getElementById('reconResolveModal');
      const payOpen = pay ? pay.outerHTML.slice(0, 280) : null;
      const resolveOpen = resolve ? resolve.outerHTML.slice(0, 280) : null;
      return {
        paymentHasReconCreateTitle: !!(pay && pay.innerHTML.includes('reconCreateTitle')),
        resolveHasReconCreateTitle: !!(resolve && resolve.innerHTML.includes('reconCreateTitle')),
        paymentOpeningHasReconCreateTitle: !!(payOpen && payOpen.includes('reconCreateTitle')),
        resolveOpeningHasReconCreateTitle: !!(resolveOpen && resolveOpen.includes('reconCreateTitle')),
        paymentRole: pay ? pay.getAttribute('role') : null,
        resolveRole: resolve ? resolve.getAttribute('role') : null,
      };
    });
    result.criteria.negative = negative;
    const cNeg = !negative.paymentHasReconCreateTitle && !negative.resolveHasReconCreateTitle
      && !negative.paymentOpeningHasReconCreateTitle && !negative.resolveOpeningHasReconCreateTitle;
    result.criteria.c_negative = { pass: cNeg, detail: negative };

    result.pass = !!(
      result.criteria.c1_dialog_name.pass
      && result.criteria.c2_tab_trap.pass
      && result.criteria.c2_esc_restore.pass
      && result.criteria.c3_controls_locale_390.pass
      && result.criteria.c_negative.pass
    );

    await context.close();
  } catch (e) {
    result.errors.push(String(e && e.stack || e));
    result.pass = false;
  } finally {
    await browser.close();
  }

  const outPath = path.join(LOG_DIR, 'browser-uat-result.json');
  fs.writeFileSync(outPath, JSON.stringify(result, null, 2));
  const lines = [];
  lines.push('=== UAT-017 browser MANUAL_UI ===');
  lines.push('pass=' + result.pass);
  lines.push('c1=' + (result.criteria.c1_dialog_name && result.criteria.c1_dialog_name.pass));
  lines.push('c2_tab=' + (result.criteria.c2_tab_trap && result.criteria.c2_tab_trap.pass));
  lines.push('c2_esc=' + (result.criteria.c2_esc_restore && result.criteria.c2_esc_restore.pass));
  lines.push('c3=' + (result.criteria.c3_controls_locale_390 && result.criteria.c3_controls_locale_390.pass));
  lines.push('neg=' + (result.criteria.c_negative && result.criteria.c_negative.pass));
  lines.push('ax=' + JSON.stringify(result.ax));
  lines.push('errors=' + JSON.stringify(result.errors));
  fs.writeFileSync(path.join(LOG_DIR, 'browser-uat.txt'), lines.join('\n') + '\n');
  console.log(lines.join('\n'));
  process.exit(result.pass ? 0 : 1);
}

run().catch(err => {
  console.error(err);
  process.exit(2);
});
