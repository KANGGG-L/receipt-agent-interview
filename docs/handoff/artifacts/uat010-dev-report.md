# UAT-010 Development Report

> Generated: 2026-10-17 04:17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-010] 生产会话缺少可见退出登录入口 — P2 QUALITY_RISK
> Vendor aliases in fixtures: n/a (auth accounts only)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Visible logout + session clear | PASS |
| Specialty pytest (4) | PASS |
| Frontend harness (cancel / network / success) | PASS (18) |
| Related B1 logout / cookie CSRF | PASS (2) |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.

## Root cause (printed citations)

1. Backend already had real logout, but UI had no production entry:
   - `demo/app/api_auth.py:57-73` — `POST /api/auth/logout` revokes jti + `clear_session_cookie`.
   - `demo/templates/index.html` (pre-fix) — only `demoRoleSelect` role switch; no logout control.
2. Production identity is HttpOnly Cookie (`receipt_session`), not localStorage Bearer:
   - `demo/static/js/common.js:459-464` — `getAuthToken()` always returns `null`; `setAuthToken` only removes `receipt_auth_token`.
   - Effect: users could log in but could not visibly end the cookie session without closing the browser / clearing cookies manually.
3. Demo role switch is not production logout (negative boundary):
   - `demo/static/js/common.js:1259+` — `initDemoRoleSwitch` only writes `demo_role` + reloads; does not call `/api/auth/logout`.

## Fix

### Template — `demo/templates/index.html`

- `33` — `id="demoRoleSwitch"` on demo role container (so production can hide it).
- `42-45` — `#authLogoutWrap` / `#authLogoutBtn` 「退出登录」, default `display:none`.

### Frontend — `demo/static/js/common.js`

- `722-736` — `clearClientAuthCredentials` / `storageHasAuthCredentials` (clear `receipt_auth_token` from local/session storage; never store password/token on login).
- `739-756` — `syncAuthChrome`: show logout only when `AuthState.enabled && AuthState.account`; hide `demoRoleSwitch` when auth enabled (do not treat role switch as logout).
- `759-769` — `requestAuthLogout`: confirm modal; `onCancel` is explicit no-op (cancel must not fake-logout).
- `772-811` — `performAuthLogout`: `POST /api/auth/logout` with `credentials: 'same-origin'` + `X-Requested-With`;
  - success → clear credentials, `AuthState.account = null`, `showLoginPanel('已退出登录')`;
  - non-2xx / network error → keep account, toast error, no login-panel fake logout.
- `843-885` — `probeAuthAndEnter` / login success paths call `clearClientAuthCredentials` + `syncAuthChrome`.

### API — `demo/app/api_auth.py`

- `57-73` — logout docstring clarifies UAT-010 boundaries: revoke + clear cookie only; no new token; no write-perm expansion; not demo role switch. Always `clear_session_cookie` even if token already invalid.

### CSS — `demo/static/css/index-head.css`

- `.auth-logout-wrap` / `.auth-logout-btn` full-width sidebar control.

### Tests

- `demo/tests/test_uat_010_logout_entry.py` (new):
  1. three cookie roles admin/owner/staff login → logout → protected `/api/receipts` 401 + token revoked
  2. logout idempotent / no token in body / staff role not elevated
  3. static HTML/JS/API citations for visible button + cancel/network safety
  4. logout response has no `token`/`password`
- `demo/tests/frontend_uat010_logout_harness.js` (new): cancel no-op source + network fail keeps session + success clears storage and shows login.

## Commands actually run

```text
node --check demo/static/js/common.js
python3 -m py_compile demo/app/api_auth.py
node demo/tests/frontend_uat010_logout_harness.js
# PASS 18 FAIL 0

.../venv/bin/pytest demo/tests/test_uat_010_logout_entry.py -v
# 4 passed

.../venv/bin/pytest demo/tests/test_b1_platform.py::test_logout_revokes_token_and_clears_cookie \
  demo/tests/test_b1_platform.py::test_cookie_session_and_csrf_protection -q
# 2 passed
```

## Acceptance mapping (dev evidence only)

| AC | Dev evidence | Status |
|---|---|---|
| 1. three cookie roles → visible logout → refresh/protected API 401 | specialty test #1; `#authLogoutBtn` + `syncAuthChrome` | PASS (API+static UI; browser MANUAL_UI reserved for UAT) |
| 2. storage has no credentials | `getAuthToken` null; clear on login/logout; harness success clears key | PASS |
| 3. cancel / network failure must not fake-logout | `onCancel` no-op; harness network keeps account | PASS |
| Negative: demo role switch ≠ production logout | role switch hidden when auth enabled; separate `#authLogoutBtn` | PASS |
| Negative: do not expand write perms | logout only revoke+clear cookie; staff remains staff | PASS |

Browser MANUAL_UI (isolation port + three interactive logins) is **reserved for independent UAT** — not claimed here.

## Files modified

| Path | Change |
|---|---|
| `demo/templates/index.html` | logout button + `demoRoleSwitch` id |
| `demo/static/js/common.js` | logout flow + chrome sync |
| `demo/app/api_auth.py` | logout boundary docstring |
| `demo/static/css/index-head.css` | logout button styles |
| `demo/tests/test_uat_010_logout_entry.py` | specialty pytest |
| `demo/tests/frontend_uat010_logout_harness.js` | cancel/network/success harness |
| `docs/handoff/01-acceptance-checklist.md` | **UAT-007 only** closed to `[DONE] PASS` (not UAT-010) |
| `docs/handoff/02-coordinator-ledger.md` | UAT-007 closed; next W3 = UAT-010 |
| `docs/handoff/artifacts/uat010-dev-report.md` | this report |

No git commit. No git push. No independent QA/UAT dispatched.

## Scheduler handoff

- UAT-007 docs closed (`[DONE] PASS`, evidence uat007-dev/qa/uat reports).
- UAT-010 **dev complete**; please dispatch **independent QA** next (fresh isolation port, do not reuse this report as PASS).
- After QA PASS, dispatch independent UAT (MANUAL_UI: three real cookie roles, visible button, refresh + protected API 401).
- Only then flip `[UAT-010]` checklist to `[DONE] PASS`.
