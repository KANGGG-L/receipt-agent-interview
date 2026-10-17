/**
 * UAT-010 independent browser smoke (puppeteer-core + system Chrome).
 * Port 15220 — fresh isolation, not QA 15210 / 15150-15210 reuse.
 */
const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer-core');

const BASE = 'http://127.0.0.1:15220';
const OUT_DIR = path.join(__dirname, '..');
const UI_DIR = path.join(OUT_DIR, 'ui');
const LOG_DIR = path.join(OUT_DIR, 'logs');
fs.mkdirSync(UI_DIR, { recursive: true });
fs.mkdirSync(LOG_DIR, { recursive: true });

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

const ROLES = [
  { email: 'admin@demo.hk', password: 'admin123', role: 'admin' },
  { email: 'boss@demo.hk', password: 'boss123', role: 'owner' },
  { email: 'staff@demo.hk', password: 'staff123', role: 'staff' },
];

function visible(el) {
  if (!el) return false;
  const s = window.getComputedStyle(el);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}

async function login(page, email, password) {
  await page.waitForSelector('#authLoginEmail', { timeout: 15000 });
  await page.click('#authLoginEmail', { clickCount: 3 });
  await page.type('#authLoginEmail', email, { delay: 10 });
  await page.click('#authLoginPassword', { clickCount: 3 });
  await page.type('#authLoginPassword', password, { delay: 10 });
  await Promise.all([
    page.waitForFunction(() => !document.getElementById('authLoginOverlay'), { timeout: 15000 }),
    page.click('#authLoginSubmit'),
  ]);
}

async function snap(page, name) {
  const p = path.join(UI_DIR, name);
  await page.screenshot({ path: p, fullPage: false });
  return p;
}

async function runRole(browser, acct) {
  const context = await browser.createBrowserContext();
  const page = await context.newPage();
  const result = {
    role: acct.role,
    email: acct.email,
    pass: false,
    steps: {},
    errors: [],
  };
  try {
    await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 30000 });
    // Seed a fake credential key to prove logout clears it
    await page.evaluate(() => {
      try { localStorage.setItem('receipt_auth_token', 'UAT010_FAKE_SHOULD_CLEAR'); } catch (e) {}
      try { sessionStorage.setItem('receipt_auth_token', 'UAT010_FAKE_SHOULD_CLEAR'); } catch (e) {}
    });

    await login(page, acct.email, acct.password);
    await sleep(400);

    // After login: logout visible, demo switch hidden, storage cleared by login path
    const chrome = await page.evaluate(() => {
      const logoutBtn = document.getElementById('authLogoutBtn');
      const logoutWrap = document.getElementById('authLogoutWrap');
      const roleSwitch = document.getElementById('demoRoleSwitch');
      const vis = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      let ls = null, ss = null;
      try { ls = localStorage.getItem('receipt_auth_token'); } catch (e) {}
      try { ss = sessionStorage.getItem('receipt_auth_token'); } catch (e) {}
      return {
        logoutVisible: vis(logoutBtn) && vis(logoutWrap),
        logoutText: logoutBtn ? logoutBtn.textContent.trim() : null,
        demoSwitchVisible: vis(roleSwitch),
        authEnabled: !!(AuthState && AuthState.enabled),
        accountRole: AuthState && AuthState.account ? AuthState.account.role : null,
        accountEmail: AuthState && AuthState.account ? AuthState.account.email : null,
        storageHasCred: !!(ls || ss),
        getAuthTokenNull: typeof getAuthToken === 'function' ? getAuthToken() === null : null,
        hasAuthLoginOverlay: !!document.getElementById('authLoginOverlay'),
      };
    });
    result.steps.afterLogin = chrome;
    await snap(page, `after-login-${acct.role}.png`);

    if (!chrome.logoutVisible) result.errors.push('logout button not visible after login');
    if (chrome.demoSwitchVisible) result.errors.push('demoRoleSwitch still visible with AUTH_ENABLED');
    if (chrome.storageHasCred) result.errors.push('storage still has receipt_auth_token after login clear');
    if (chrome.accountRole !== acct.role) result.errors.push(`role mismatch ${chrome.accountRole}`);
    if (chrome.hasAuthLoginOverlay) result.errors.push('login overlay still showing after login');

    // Protected API before logout
    const receiptsBefore = await page.evaluate(async () => {
      const r = await fetch('/api/receipts', { credentials: 'same-origin' });
      return r.status;
    });
    result.steps.receiptsBefore = receiptsBefore;
    if (receiptsBefore !== 200) result.errors.push(`receipts before logout ${receiptsBefore}`);

    // Cancel path: open confirm, click 取消, stay logged in
    await page.click('#authLogoutBtn');
    await page.waitForSelector('.custom-modal-overlay', { timeout: 5000 });
    await snap(page, `confirm-modal-${acct.role}.png`);
    // cancel button is secondary in the modal
    await page.evaluate(() => {
      const overlay = document.querySelector('.custom-modal-overlay');
      const btns = overlay ? [...overlay.querySelectorAll('button')] : [];
      const cancel = btns.find(b => b.textContent.trim() === '取消');
      if (!cancel) throw new Error('cancel button missing');
      cancel.click();
    });
    await page.waitForFunction(() => !document.querySelector('.custom-modal-overlay'), { timeout: 5000 });
    const afterCancel = await page.evaluate(() => {
      const vis = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      return {
        stillLoggedIn: !!(AuthState && AuthState.account),
        logoutVisible: vis(document.getElementById('authLogoutBtn')),
        hasLoginOverlay: !!document.getElementById('authLoginOverlay'),
        receiptsOk: null,
      };
    });
    afterCancel.receiptsStatus = await page.evaluate(async () => {
      const r = await fetch('/api/receipts', { credentials: 'same-origin' });
      return r.status;
    });
    result.steps.afterCancel = afterCancel;
    if (!afterCancel.stillLoggedIn) result.errors.push('cancel fake-logged-out AuthState');
    if (afterCancel.hasLoginOverlay) result.errors.push('cancel opened login panel');
    if (afterCancel.receiptsStatus !== 200) result.errors.push(`cancel broke session ${afterCancel.receiptsStatus}`);

    // Network fail path: abort logout request, confirm, stay logged in
    await page.setRequestInterception(true);
    const abortHandler = (req) => {
      if (req.url().includes('/api/auth/logout') && req.method() === 'POST') {
        req.abort('failed');
      } else {
        req.continue();
      }
    };
    page.on('request', abortHandler);
    await page.click('#authLogoutBtn');
    await page.waitForSelector('.custom-modal-overlay', { timeout: 5000 });
    await page.evaluate(() => {
      const overlay = document.querySelector('.custom-modal-overlay');
      const btns = [...overlay.querySelectorAll('button')];
      const conf = btns.find(b => b.textContent.trim() === '退出登录');
      if (!conf) throw new Error('confirm missing');
      conf.click();
    });
    await sleep(800);
    const afterNetFail = await page.evaluate(() => {
      return {
        stillLoggedIn: !!(AuthState && AuthState.account),
        hasLoginOverlay: !!document.getElementById('authLoginOverlay'),
        toastText: (() => {
          const t = document.querySelector('.toast, .toast-msg, [class*="toast"]');
          return t ? t.textContent : null;
        })(),
      };
    });
    afterNetFail.receiptsStatus = await page.evaluate(async () => {
      const r = await fetch('/api/receipts', { credentials: 'same-origin' });
      return r.status;
    });
    result.steps.afterNetworkFail = afterNetFail;
    page.off('request', abortHandler);
    await page.setRequestInterception(false);
    if (!afterNetFail.stillLoggedIn) result.errors.push('network fail fake-logged-out');
    if (afterNetFail.hasLoginOverlay) result.errors.push('network fail opened login panel');
    if (afterNetFail.receiptsStatus !== 200) result.errors.push(`network fail broke cookie ${afterNetFail.receiptsStatus}`);

    // Success logout
    await page.click('#authLogoutBtn');
    await page.waitForSelector('.custom-modal-overlay', { timeout: 5000 });
    await page.evaluate(() => {
      const overlay = document.querySelector('.custom-modal-overlay');
      const btns = [...overlay.querySelectorAll('button')];
      const conf = btns.find(b => b.textContent.trim() === '退出登录');
      conf.click();
    });
    await page.waitForSelector('#authLoginOverlay', { timeout: 10000 });
    await sleep(300);
    const afterLogout = await page.evaluate(() => {
      const vis = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      let ls = null, ss = null;
      try { ls = localStorage.getItem('receipt_auth_token'); } catch (e) {}
      try { ss = sessionStorage.getItem('receipt_auth_token'); } catch (e) {}
      const err = document.getElementById('authLoginError');
      return {
        accountNull: !(AuthState && AuthState.account),
        logoutVisible: vis(document.getElementById('authLogoutBtn')),
        hasLoginOverlay: !!document.getElementById('authLoginOverlay'),
        loginHint: err ? err.textContent : null,
        storageHasCred: !!(ls || ss),
        getAuthTokenNull: typeof getAuthToken === 'function' ? getAuthToken() === null : null,
      };
    });
    afterLogout.receiptsStatus = await page.evaluate(async () => {
      const r = await fetch('/api/receipts', { credentials: 'same-origin' });
      return r.status;
    });
    afterLogout.me = await page.evaluate(async () => {
      const r = await fetch('/api/auth/me', { credentials: 'same-origin' });
      return r.json();
    });
    result.steps.afterLogout = afterLogout;
    await snap(page, `after-logout-${acct.role}.png`);

    if (!afterLogout.accountNull) result.errors.push('account still set after logout');
    if (afterLogout.logoutVisible) result.errors.push('logout button still visible after logout');
    if (!afterLogout.hasLoginOverlay) result.errors.push('login panel not shown after logout');
    if (afterLogout.storageHasCred) result.errors.push('storage still has credentials after logout');
    if (afterLogout.receiptsStatus !== 401) result.errors.push(`receipts after logout ${afterLogout.receiptsStatus}`);
    if (afterLogout.me && afterLogout.me.account) result.errors.push('me.account not null after logout');

    // Refresh → still logged out
    await page.reload({ waitUntil: 'networkidle2', timeout: 30000 });
    await page.waitForSelector('#authLoginOverlay', { timeout: 15000 });
    const afterRefresh = await page.evaluate(async () => {
      const r = await fetch('/api/receipts', { credentials: 'same-origin' });
      const me = await (await fetch('/api/auth/me', { credentials: 'same-origin' })).json();
      return {
        hasLoginOverlay: !!document.getElementById('authLoginOverlay'),
        receiptsStatus: r.status,
        meAccount: me.account,
        logoutVisible: (() => {
          const el = document.getElementById('authLogoutBtn');
          if (!el) return false;
          const s = window.getComputedStyle(el);
          if (s.display === 'none') return false;
          const rect = el.getBoundingClientRect();
          return rect.width > 0 && rect.height > 0;
        })(),
      };
    });
    result.steps.afterRefresh = afterRefresh;
    await snap(page, `after-refresh-${acct.role}.png`);
    if (!afterRefresh.hasLoginOverlay) result.errors.push('no login after refresh');
    if (afterRefresh.receiptsStatus !== 401) result.errors.push(`refresh receipts ${afterRefresh.receiptsStatus}`);
    if (afterRefresh.meAccount) result.errors.push('me still has account after refresh');
    if (afterRefresh.logoutVisible) result.errors.push('logout visible while logged out after refresh');

    result.pass = result.errors.length === 0;
  } catch (e) {
    result.errors.push(String(e && e.stack ? e.stack : e));
    result.pass = false;
    try { await snap(page, `error-${acct.role}.png`); } catch (_) {}
  } finally {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
  }
  return result;
}

(async () => {
  const chromePath = process.env.CHROME_PATH || '/usr/bin/google-chrome-stable';
  const browser = await puppeteer.launch({
    executablePath: chromePath,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--window-size=1280,900'],
    defaultViewport: { width: 1280, height: 900 },
  });
  const report = {
    port: 15220,
    started_at: new Date().toISOString(),
    roles: [],
    negative_demo_switch: null,
    overall: false,
  };
  try {
    for (const acct of ROLES) {
      const r = await runRole(browser, acct);
      report.roles.push(r);
      console.log(`ROLE_${acct.role}=${r.pass ? 'PASS' : 'FAIL'} errors=${JSON.stringify(r.errors)}`);
    }

    // Negative: with AUTH_ENABLED, landing without cookie shows login; demo switch should be hidden
    // even before login once probe sees auth_enabled (syncAuthChrome on AuthState.enabled)
    const page = await browser.newPage();
    await page.goto(BASE + '/', { waitUntil: 'networkidle2', timeout: 30000 });
    await sleep(500);
    const neg = await page.evaluate(() => {
      const roleSwitch = document.getElementById('demoRoleSwitch');
      const logoutBtn = document.getElementById('authLogoutBtn');
      const vis = (el) => {
        if (!el) return false;
        const s = window.getComputedStyle(el);
        if (s.display === 'none') return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      };
      // Does initDemoRoleSwitch source call logout? checked offline; here UI state:
      return {
        authEnabled: !!(AuthState && AuthState.enabled),
        demoSwitchVisible: vis(roleSwitch),
        logoutVisibleLoggedOut: vis(logoutBtn),
        hasLoginOverlay: !!document.getElementById('authLoginOverlay'),
        initDemoRoleSwitchIsFn: typeof initDemoRoleSwitch === 'function',
      };
    });
    // Also static check: role switch change handler must not hit logout API
    const staticJs = fs.readFileSync(
      path.join('/workspace/receipt-agent-interview/demo/static/js/common.js'),
      'utf8'
    );
    const switchIdx = staticJs.indexOf('function initDemoRoleSwitch');
    const switchSlice = switchIdx >= 0 ? staticJs.slice(switchIdx, switchIdx + 2500) : '';
    neg.initDemoRoleSwitch_calls_logout =
      /\/api\/auth\/logout|performAuthLogout|requestAuthLogout/.test(switchSlice);
    neg.demo_switch_not_logout_pass =
      neg.authEnabled === true &&
      neg.demoSwitchVisible === false &&
      neg.logoutVisibleLoggedOut === false &&
      neg.initDemoRoleSwitch_calls_logout === false;
    report.negative_demo_switch = neg;
    console.log('NEGATIVE_DEMO_SWITCH=' + JSON.stringify(neg));
    await page.close();

    report.overall =
      report.roles.every((r) => r.pass) &&
      report.negative_demo_switch.demo_switch_not_logout_pass === true;
  } finally {
    await browser.close();
  }
  report.finished_at = new Date().toISOString();
  const outPath = path.join(LOG_DIR, 'browser-uat-result.json');
  fs.writeFileSync(outPath, JSON.stringify(report, null, 2));
  console.log('BROWSER_OVERALL=' + (report.overall ? 'PASS' : 'FAIL'));
  console.log('WROTE ' + outPath);
  process.exit(report.overall ? 0 : 1);
})().catch((e) => {
  console.error(e);
  process.exit(2);
});
