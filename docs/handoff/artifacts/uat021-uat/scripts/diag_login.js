const fs = require('fs');
const path = require('path');
const puppeteer = require(path.join(__dirname, 'node_modules/puppeteer-core'));
(async () => {
  const browser = await puppeteer.launch({
    executablePath: '/usr/bin/google-chrome', headless: 'new',
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  page.on('console', m => console.log('CONSOLE', m.type(), m.text()));
  page.on('pageerror', e => console.log('PAGEERR', e.message));
  await page.goto('http://127.0.0.1:15700/', { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForSelector('#authLoginEmail', { timeout: 20000 });
  await page.type('#authLoginEmail', 'staff@demo.hk');
  await page.type('#authLoginPassword', 'staff123');
  const before = await page.evaluate(() => ({
    overlay: !!document.getElementById('authLoginOverlay'),
    auth: window.AuthState ? {enabled: AuthState.enabled, account: AuthState.account} : null,
  }));
  console.log('BEFORE', JSON.stringify(before));
  await page.click('#authLoginSubmit');
  await new Promise(r => setTimeout(r, 3000));
  const after = await page.evaluate(async () => {
    let me = null;
    try { me = await (await fetch('/api/auth/me', {credentials:'include'})).json(); } catch(e) { me = String(e); }
    return {
      overlay: !!document.getElementById('authLoginOverlay'),
      err: document.getElementById('authLoginError')?.textContent || null,
      auth: window.AuthState ? {enabled: AuthState.enabled, account: AuthState.account, loginPanelShowing: AuthState.loginPanelShowing} : null,
      me,
    };
  });
  console.log('AFTER', JSON.stringify(after, null, 2));
  await page.screenshot({ path: path.join(__dirname, '..', 'ui', 'diag-login.png') });
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
