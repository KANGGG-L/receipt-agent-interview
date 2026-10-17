# UAT-010 Independent QA Report

> Generated: 2026-10-17 04:20 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-010 acceptance only (visible logout / session clear)
> Card: [UAT-010] 生产会话缺少可见退出登录入口 — P2 QUALITY_RISK
> UAT: NOT started (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. three cookie roles → visible logout → refresh / protected API 401 | **PASS** |
| 2. storage has no credentials | **PASS** |
| 3. cancel / network failure must not fake-logout | **PASS** |
| Negative: demo role switch ≠ production logout | **PASS** |
| Negative: logout does not expand write perms | **PASS** |

**UAT-010 QA gate: PASS.** Do not treat this as card close; independent UAT (real browser MANUAL_UI for three interactive logins + painted logout button) not started.

## Commands run (actually executed)

1. Static evidence scan (HTML button, JS handlers, API revoke/clear, role-switch boundary)
   - Log: `docs/handoff/artifacts/uat010-qa/logs/static-evidence.txt`
2. `pytest demo/tests/test_uat_010_logout_entry.py -v --tb=short`
   - Venv: `docs/handoff/artifacts/uat003-qa/environment/venv` (linked)
   - Fresh env secret; specialty test uses its own tempfile DB
   - Log: `docs/handoff/artifacts/uat010-qa/logs/pytest-uat010.txt`
   - Result: **4 passed**
3. Frontend harness + syntax
   - `node --check demo/static/js/common.js` OK
   - `python3 -m py_compile demo/app/api_auth.py` OK
   - `node demo/tests/frontend_uat010_logout_harness.js`
   - Log: `docs/handoff/artifacts/uat010-qa/logs/frontend-harness.txt`
   - Result: **PASS 18 FAIL 0**
4. B1 logout / cookie CSRF regression
   - `pytest demo/tests/test_b1_platform.py::test_logout_revokes_token_and_clears_cookie demo/tests/test_b1_platform.py::test_cookie_session_and_csrf_protection -q`
   - Log: `docs/handoff/artifacts/uat010-qa/logs/pytest-b1-logout.txt`
   - Result: **2 passed**
5. Isolated uvicorn smoke (self-built; fresh port/DB; AUTH_ENABLED=1)
   - Port **15210** (avoided 15150-15200), DB `qa_isolated_smoke.db`, pid 129258
   - `GET /` 200; served HTML has `#authLogoutBtn` / 「退出登录」; served JS has `requestAuthLogout` / cancel no-op
   - Log: `docs/handoff/artifacts/uat010-qa/logs/uvicorn-15210.log`
6. Live API three cookie roles on :15210
   - admin / owner / staff: login cookie → `/api/receipts` 200 → `POST /api/auth/logout` → cookie cleared Max-Age=0 → receipts 401; stale token cookie 401; me account null
   - X-Role alone without cookie → receipts 401 (not production auth)
   - Idempotent logout without session → success, no token in body
   - Log: `docs/handoff/artifacts/uat010-qa/logs/live-api-verify.txt`

## Criterion 1 evidence — visible logout + protected API 401

Self-checked citations:

- `demo/templates/index.html:43-44` — `#authLogoutWrap` / `#authLogoutBtn` 「退出登录」 (default `display:none`)
- `demo/static/js/common.js:739-756` — `syncAuthChrome` shows logout only when `AuthState.enabled && AuthState.account`
- `demo/app/api_auth.py:57-73` — `POST /api/auth/logout` revoke jti + `clear_session_cookie`

Runtime:

- Pytest `test_three_cookie_roles_logout_then_protected_api_401` PASSED
- Live :15210 for admin/owner/staff:
  - `RECEIPTS_BEFORE=200`
  - `LOGOUT_BODY={"status":"success","msg":"已退出登录"}`
  - `LOGOUT_SET_COOKIE=receipt_session=""; Max-Age=0`
  - `RECEIPTS_AFTER_COOKIE_JAR=401`
  - `RECEIPTS_STALE_TOKEN=401`
  - `ME_AFTER.account=null`, `identity_source=missing`
- Served HTML: `HTML_HAS_authLogoutBtn=1`

Browser MANUAL_UI (paint logout button after real login, click confirm, refresh) reserved for independent UAT.

## Criterion 2 evidence — storage has no credentials

Citations:

- `demo/static/js/common.js:459-464` — `getAuthToken()` always returns `null`; `setAuthToken` only removes `receipt_auth_token`
- `demo/static/js/common.js:722-736` — `clearClientAuthCredentials` / `storageHasAuthCredentials`
- Login / logout success paths call `clearClientAuthCredentials` (probe/login/performAuthLogout)

Runtime:

- Static: `JS_getAuthToken_returns_null=True`
- Harness: `success clears storage credential key`; `storageHasAuthCredentials false`
- Pytest specialty #3 asserts getAuthToken null pattern; #4 logout body has no `token`/`password`
- Live logout bodies: no `token` / no `password`

Production identity is HttpOnly `receipt_session` cookie (not localStorage Bearer).

## Criterion 3 evidence — cancel / network fail must not fake-logout

Citations:

- `demo/static/js/common.js:768` — `onCancel: function () { /* 取消：保持会话，不假退出 */ }`
- `demo/static/js/common.js:800-811` — non-2xx / catch keep `AuthState`, toast error, no `showLoginPanel`

Runtime:

- Harness OK: cancel keeps account + storage; network fail keeps account, does not open login, toast「网络异常，未能退出」
- Source assert: cancel no-op present; no fake-logout before success branch

## Negative — demo role switch ≠ production logout

Citations:

- `demo/static/js/common.js:752-756` — hide `#demoRoleSwitch` when `AuthState.enabled`
- `demo/static/js/common.js:1371+` — `initDemoRoleSwitch` does **not** call `/api/auth/logout` or `performAuthLogout`
- Separate control `#authLogoutBtn` vs `#demoRoleSelect`

Runtime:

- `initDemoRoleSwitch_calls_logout_api=False`
- `syncAuthChrome_hides_demoRoleSwitch=True`
- Live: `X-Role: owner` alone → `/api/receipts` **401** when AUTH_ENABLED=1
- Pytest: staff remains staff after logout API use (role not elevated)

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15210 (fresh; not 15150-15200) |
| DB | `docs/handoff/artifacts/uat010-qa/environment/qa_isolated_smoke.db` (+ pytest tempfile / `qa_pytest_b1.db`) |
| Auth | `AUTH_ENABLED=1`, non-default `DEMO_TOKEN_SECRET` |
| Fixture accounts | admin@demo.hk / boss@demo.hk / staff@demo.hk only |
| Stop | `STOPPED_OWNED_TEST_PROCESS`, pid 129258 dead |
| Port free | true (`ss` empty; SO_REUSEADDR bind ok) |
| Browser tabs | 0 opened; `browser_tab_closed=true` |
| 15010 | not present on this host; untouched |
| cleanup.json | `docs/handoff/artifacts/uat010-qa/environment/cleanup.json` |

No orphan uvicorn left. No git commit / push. Checklist card left `[TODO]` (scheduler closes after independent UAT only).

## Residual risks (honest)

1. **No real browser / painted-pixel proof**: logout button visibility after login, confirm modal cancel click, and post-logout full page refresh UX are MANUAL_UI for independent UAT on a **new** 151xx/152xx port.
2. **QA live path used curl cookie jar**, not Chromium storage inspector — storage-clear proven via source + Node harness + getAuthToken null contract.
3. Checklist in `01-acceptance-checklist.md` still `[TODO] NOT_RUN`; do not flip from this QA alone.

## Scheduler signal

- **QA gate: PASS**
- Evidence path: `docs/handoff/artifacts/uat010-qa-report.md`
- Supporting logs/dir: `docs/handoff/artifacts/uat010-qa/`
- **UAT: not started**
- Next: independent UAT on a **new** port / DB / fixture (do not reuse 15210 env)
