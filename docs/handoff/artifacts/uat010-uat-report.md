# UAT-010 Independent UAT Report

> Generated: 2026-10-17 04:23 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of visible production logout
> Card: [UAT-010] 生产会话缺少可见退出登录入口 — P2 QUALITY_RISK
> Prior gates (not reused): `uat010-dev-report.md`, `uat010-qa-report.md` (QA port 15210)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. three cookie roles → visible logout → refresh / protected API 401 | **PASS** |
| 2. storage has no credentials | **PASS** |
| 3. cancel / network failure must not fake-logout | **PASS** |
| Negative: demo role switch ≠ production logout | **PASS** |
| Negative: logout does not expand write perms (staff stays staff; X-Role alone 401) | **PASS** |

**UAT-010 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15220** (QA used 15210; forbidden reuse of 15150–15210) |
| DB | `docs/handoff/artifacts/uat010-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat010-uat/environment/rag_isolated` |
| Auth | `AUTH_ENABLED=1`, `DEMO_TOKEN_SECRET=uat010-uat-secret-not-default-32chars` |
| Fixture accounts | admin@demo.hk / boss@demo.hk / staff@demo.hk only |
| Venv | `docs/handoff/artifacts/uat010-uat/environment/venv` → uat003-qa venv link |
| Server pid | 131305 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; **fresh BrowserContext per role** |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15220` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=1` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=true`
2. Browser MANUAL_UI `scripts/browser_uat010.js` (puppeteer-core + system Chrome): for each of admin/owner/staff — real login form → painted `#authLogoutBtn` → cancel confirm → network-abort logout → success logout → refresh — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
3. Supporting live curl API three roles on :15220 — `logs/live-api-verify.txt`
4. Static citation print + served HTML/JS markers — `logs/static-evidence.txt`
5. Full cleanup: stop pid, free port, `environment/cleanup.json`

Did **not** run: pytest (QA stage); checklist edit; git write.

## Criterion 1 — three roles, visible logout, refresh + 401

Self-checked citations:

- `demo/templates/index.html:43-44` — `#authLogoutWrap` / `#authLogoutBtn` 「退出登录」
- `demo/static/js/common.js:739-756` — `syncAuthChrome` shows logout only when `AuthState.enabled && AuthState.account`
- `demo/app/api_auth.py:57-73` — `POST /api/auth/logout` revoke + `clear_session_cookie`

Runtime (browser, port 15220):

| Role | logoutVisible after login | receipts before | after logout | after refresh |
|---|---|---|---|---|
| admin | true (text「退出登录」) | 200 | receipts **401**, login overlay on | receipts **401**, login overlay on |
| owner | true | 200 | **401** | **401** |
| staff | true | 200 | **401** | **401** |

Screenshots: `ui/after-login-{role}.png`, `ui/confirm-modal-{role}.png`, `ui/after-logout-{role}.png`, `ui/after-refresh-{role}.png`.

Supporting curl: each role `RECEIPTS_BEFORE=200` → logout `Max-Age=0` → jar 401 + stale token 401 + `ME_AFTER.account=null`.

## Criterion 2 — storage has no credentials

Citations:

- `demo/static/js/common.js:459-464` — `getAuthToken()` always `null`
- `demo/static/js/common.js:722-736` — `clearClientAuthCredentials` / `storageHasAuthCredentials`
- Login and logout success paths call `clearClientAuthCredentials`

Runtime:

- Browser seeded `localStorage`/`sessionStorage` key `receipt_auth_token=UAT010_FAKE_SHOULD_CLEAR` before login; after login `storageHasCred=false`; after logout `storageHasCred=false`; `getAuthToken()===null`
- Production identity remains HttpOnly `receipt_session` cookie (not Bearer in storage)
- Curl logout bodies: no `token` / no `password` keys

## Criterion 3 — cancel / network fail must not fake-logout

Citations:

- `demo/static/js/common.js:768` — `onCancel` no-op
- `demo/static/js/common.js:800-811` — non-2xx / catch keep `AuthState`, toast error, no `showLoginPanel`

Runtime (browser, all three roles):

- Cancel「取消」: `stillLoggedIn=true`, no login overlay, `/api/receipts` **200**
- Network abort of `POST /api/auth/logout`: `stillLoggedIn=true`, no login overlay, `/api/receipts` **200**
- Only success confirm path opens `#authLoginOverlay` and yields receipts **401**

## Negative — demo role switch ≠ production logout

- After login with `AUTH_ENABLED=1`: `#demoRoleSwitch` **not visible**; separate `#authLogoutBtn` **visible**
- Logged-out landing: demo switch hidden; logout hidden; login overlay shown
- Static: `initDemoRoleSwitch` body does **not** call `/api/auth/logout` / `performAuthLogout` / `requestAuthLogout`
- Curl: `X-Role: owner` alone without cookie → `/api/receipts` **401**
- Staff login → logout API use does not elevate role (account role remained staff through session; logout clears account)

## Residual risks (honest)

1. Browser automation used headless Chromium via puppeteer-core (not a human-driven GUI tab). Flows exercised real DOM click/type on production HTML/JS served from :15220, including confirm modal cancel/confirm and request interception for network fail.
2. Toast copy on network fail was not strictly asserted (selector for toast text is best-effort); session retention + no login panel + receipts 200 were asserted.
3. Checklist card in `01-acceptance-checklist.md` not updated by this UAT (scheduler ownership).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 131305) |
| Port 15220 free | true |
| pid alive after stop | false |
| Browser contexts | closed; no leftover chrome for 15220 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat010-uat/environment/cleanup.json` |

No git commit / push. No checklist edit.

## Scheduler signal

- **UAT gate: PASS**
- Evidence: `docs/handoff/artifacts/uat010-uat-report.md`
- Supporting dir: `docs/handoff/artifacts/uat010-uat/`
- Next: scheduler may mark UAT-010 card closed and proceed remaining W3 items.
