# UAT-017 Independent QA Report

> Generated: 2026-10-17 04:27 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-017 acceptance only (recon create modal dialog semantics)
> Card: [UAT-017] 对账创建弹窗缺dialog语义 — P2 PRODUCT_FAULT
> UAT: NOT started (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Open create-recon → dialog + accessible name (role / aria-modal / labelledby / describedby) | **PASS** |
| 2. openAccessibleModal wiring; Tab cycles inside; Esc closes and restores trigger focus | **PASS** |
| 3. Cancel / submit controls present; close via `closeModalById` → accessible close path | **PASS** |
| Negative: do not rewrite unrelated modals (payment / resolve) | **PASS** |
| Specialty pytest (4) | **PASS** |

**UAT-017 QA gate: PASS.** Do not treat this as card close; independent UAT (real browser AXTree paint / Hans·Hant·390 occlusion MANUAL_UI) not started.

## Commands run (actually executed)

1. Static evidence scan (HTML dialog attrs, openReconModal path, Esc/Tab helpers, negative scope)
   - Log: `docs/handoff/artifacts/uat017-qa/logs/static-evidence.txt`
2. `pytest demo/tests/test_uat_017_recon_create_dialog.py -v --tb=short`
   - Venv: `docs/handoff/artifacts/uat003-qa/environment/venv` (linked)
   - Fresh `DB_PATH=.../uat017-qa/environment/qa_pytest.db`
   - Log: `docs/handoff/artifacts/uat017-qa/logs/pytest-uat017.txt`
   - Result: **4 passed**
3. Frontend syntax
   - `node --check demo/static/js/finance_ops.js` / `review_modal.js` / `admin_observation.js`
   - Log: `docs/handoff/artifacts/uat017-qa/logs/node-check.txt` — OK
4. Node accessible-modal harness (open / ARIA / preferred focus / Tab wrap / Esc restore / closeModalById)
   - Script: `docs/handoff/artifacts/uat017-qa/scripts/frontend_uat017_accessible_modal_harness.js`
   - Loads real `review_modal.js` + Esc/Tab listener excerpt + extracted `openReconModal` / `closeModalById`
   - Log: `docs/handoff/artifacts/uat017-qa/logs/frontend-harness.txt`
   - Result: **PASS 22 FAIL 0**
5. Isolated uvicorn smoke (self-built; fresh port/DB)
   - Port **15240** (avoided 15150-15220), DB `qa_isolated_smoke.db`, AUTH_ENABLED=0
   - `GET /` 200; served HTML/JS carry dialog attrs + `openAccessibleModal` path
   - Log: `docs/handoff/artifacts/uat017-qa/logs/uvicorn-15240.log`, `live-served-verify.txt`

## Criterion 1 evidence — dialog + name

Self-checked citations:

- `demo/templates/index.html:4137` — `#reconCreateModal` `role="dialog" aria-modal="true" aria-labelledby="reconCreateTitle" aria-describedby="reconSupplierHint"`
- `demo/templates/index.html:4140` — `h3#reconCreateTitle`「发起对账」
- `demo/static/js/finance_ops.js:626-634` — runtime ensures labelledby/describedby; `openAccessibleModal(modal, '#reconPeriodType', trigger)`
- `demo/static/js/review_modal.js:27-32` — open path sets `role=dialog` / `aria-modal=true`

Runtime:

- Static scan: all four attrs True on opening tag
- Pytest `test_recon_create_modal_has_dialog_semantics_in_html` PASSED
- Harness: after `openReconModal`, attrs + preferred focus on `#reconPeriodType`
- Live :15240 served HTML: `SERVED_HTML_role_dialog/aria_modal/labelledby/describedby=True`

Real painted AXTree name after click is reserved for independent UAT.

## Criterion 2 evidence — openAccessibleModal / Tab / Esc / focus restore

Citations:

- `demo/static/js/finance_ops.js:612-637` — `openReconModal` uses `openAccessibleModal` (no bare `classList.remove('hide')`)
- `demo/static/js/review_modal.js:34-37` — push `{modal, trigger}` onto `_activeAccessibleModals`
- `demo/static/js/review_modal.js:60-69` — `closeAccessibleModal` → `item.trigger.focus`
- `demo/static/js/admin_observation.js:1616-1630` — Esc → `closeAccessibleModal(modal)`
- `demo/static/js/admin_observation.js:1637-1668` — Tab / Shift+Tab cycle via `getModalFocusableElements`

Runtime:

- Pytest `test_open_recon_modal_uses_accessible_modal_path` + `test_accessible_modal_helpers_still_present` PASSED
- Harness: stack records trigger; Tab last→first / Shift+Tab first→last; Esc hides + clears stack + restores trigger; `closeModalById` same restore
- Live served JS: `SERVED_JS_openAccessibleModal=True`, bare remove-hide absent; Esc/Tab present in served `admin_observation.js`

## Criterion 3 evidence — cancel / submit usable (handlers preserved)

Citations:

- `demo/templates/index.html:4165-4166` — `#btnCancelReconCreate` / `#btnSubmitReconCreate` `type="button"`
- Cancel close: `closeModalById('reconCreateModal')` CSP on cancel + X button (`4141`)
- `demo/static/js/finance_ops.js:666` — success path `closeModalById('reconCreateModal')` → `closeAccessibleModal`

Runtime:

- Static + served: cancel/submit ids present; harness closeModalById restores focus
- Hans/Hant & 390 occlusion MANUAL_UI reserved for independent UAT (no CSS layout change in this card)

## Negative — scope not expanded to all modals

Citations / runtime:

- `paymentModal` / `reconResolveModal` opening tags lack `reconCreateTitle` (static + served)
- Pytest `test_uat017_scope_does_not_rewrite_unrelated_modals` PASSED
- Esc/Tab global listener reused, not rewritten beyond existing helpers

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15240 (fresh; not 15150-15220) |
| DB | `docs/handoff/artifacts/uat017-qa/environment/qa_isolated_smoke.db` (+ pytest DB) |
| AUTH_ENABLED | 0 (dialog markup/JS smoke; no auth required for static serve) |
| uvicorn | started then stopped; port free after cleanup |
| Artifacts | `docs/handoff/artifacts/uat017-qa/` (logs, scripts, environment) |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |

## Scheduler handoff

- UAT-017 **QA PASS**.
- Please dispatch **independent UAT** next (real browser: open 发起对账 → AXTree dialog name; Tab trap; Esc focus restore; cancel/submit; Hans/Hant + 390).
- Only after UAT PASS flip `[UAT-017]` checklist to `[DONE] PASS`.
