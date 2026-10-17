# UAT-017 Independent UAT Report

> Generated: 2026-10-17 04:30 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of recon create modal dialog semantics
> Card: [UAT-017] 对账创建弹窗缺dialog语义 — P2 PRODUCT_FAULT
> Prior gates (not reused): `uat017-dev-report.md`, `uat017-qa-report.md` (QA port 15240)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Open create-recon → AXTree dialog + accessible name | **PASS** |
| 2. Tab cycles inside modal; Esc closes and restores trigger focus | **PASS** |
| 3. Cancel / submit usable; Hans/Hant & 390 no occlusion | **PASS** |
| Negative: do not rewrite unrelated modals (payment / resolve) | **PASS** |

**UAT-017 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15250** (QA used 15240; forbidden reuse of 15150–15240) |
| DB | `docs/handoff/artifacts/uat017-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat017-uat/environment/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner; dialog MANUAL_UI does not require production cookie) |
| Fixture | seeded supplier id=1 name=Vendor_A via POST /api/suppliers |
| Venv | `docs/handoff/artifacts/uat017-uat/environment/venv` → uat003-qa venv link |
| Server pid | 140450 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; real DOM click/Tab/Esc on served HTML/JS |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15250` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Seed Vendor_A supplier — `logs/seed-supplier.json` (id=1)
3. Browser MANUAL_UI `scripts/browser_uat017.js` (puppeteer-core + system Chrome): archive → 应付与对账 → select supplier → 发起对账 → AXTree / Tab / Esc / cancel / Hans·Hant·390 — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
4. Static citation print + live served HTML markers — `logs/static-evidence.txt`
5. Full cleanup: stop pid, free port, `environment/cleanup.json`

Did **not** run: pytest (QA stage); checklist edit; git write.

## Criterion 1 — open create-recon → dialog + name

Self-checked citations:

- `demo/templates/index.html:4137` — `#reconCreateModal` `role="dialog" aria-modal="true" aria-labelledby="reconCreateTitle" aria-describedby="reconSupplierHint"`
- `demo/templates/index.html:4140` — `h3#reconCreateTitle`「发起对账」
- `demo/static/js/finance_ops.js:632-634` — `openAccessibleModal(modal, '#reconPeriodType', trigger)`
- `demo/static/js/review_modal.js:27-32` — open path sets `role=dialog` / `aria-modal=true`

Runtime (browser, port 15250):

- Painted AXTree: `dialogCount=1`, `dialogNames=["发起对账"]`, `namedMatch=true`
- DOM after open: `role=dialog`, `aria-modal=true`, labelledby/describedby present, title「发起对账」, not hidden
- Preferred focus after open: `#reconPeriodType`
- Screenshot: `ui/open-dialog-desktop-hans.png`

## Criterion 2 — Tab trap + Esc focus restore

Self-checked citations:

- `demo/static/js/finance_ops.js:612-637` — `openReconModal` uses `openAccessibleModal` (no bare `classList.remove('hide')`)
- `demo/static/js/review_modal.js:34-37` — push `{modal, trigger}` onto `_activeAccessibleModals`
- `demo/static/js/review_modal.js:60-69` — `closeAccessibleModal` → `item.trigger.focus`
- `demo/static/js/admin_observation.js:1616-1630` — Esc → `closeAccessibleModal(modal)`
- `demo/static/js/admin_observation.js:1637-1668` — Tab / Shift+Tab cycle via `getModalFocusableElements`

Runtime (browser, port 15250):

- Tab trail: every Tab stay `inside=true` modal; focusable count ≥ 2
- Esc: modal `hidden=true`, `activeIsTrigger=true`, `activeText`「发起对账」, `stackLen=0`
- Screenshot after controls open: `ui/open-dialog-controls.png`

## Criterion 3 — cancel / submit usable; Hans/Hant & 390 no occlusion

Self-checked citations:

- `demo/templates/index.html:4165-4166` — `#btnCancelReconCreate` / `#btnSubmitReconCreate` `type="button"`
- Cancel close: `closeModalById('reconCreateModal')` on cancel + X
- `demo/static/js/finance_ops.js:666` — success path `closeModalById` → accessible close

Runtime (browser, port 15250):

- Cancel click → modal hidden, stack cleared
- Submit/cancel: visible, enabled, CSP handlers present (`submitReconCreate` / `closeModalById`)
- Viewport **390×844** Hans (`zh-Hans`): title「发起对账」, panel not occluded, cancel+submit+title in viewport — `ui/open-dialog-390-hans.png`
- Viewport **390×844** Hant (`zh-Hant-HK`): title「發起對賬」, panel not occluded, cancel+submit+title in viewport — `ui/open-dialog-390-hant.png`

## Negative — scope not expanded to all modals

- `paymentModal` / `reconResolveModal` DOM do **not** contain `reconCreateTitle`
- Only `#reconCreateModal` + `openReconModal` path under test; Esc/Tab global listener reused

## Residual risks (honest)

1. Browser automation used headless Chromium via puppeteer-core (not a human-driven GUI tab). Flows exercised real DOM focus/click/Tab/Esc on production HTML/JS served from :15250, including Chrome accessibility snapshot for dialog name.
2. Submit path was verified for control presence / handler wiring / visibility; a full successful POST create was not required for dialog-semantics AC (supplier selected; cancel path fully exercised).
3. Checklist card in `01-acceptance-checklist.md` not updated by this UAT (scheduler ownership).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 140450) |
| Port 15250 free | (see cleanup.json) |
| Browser contexts | closed; no leftover chrome for 15250 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat017-uat/environment/cleanup.json` |

No git commit / push. No checklist edit.

## Scheduler signal

- **UAT gate: PASS**
- Evidence: `docs/handoff/artifacts/uat017-uat-report.md` + `docs/handoff/artifacts/uat017-uat/`
- Scheduler may flip `[UAT-017]` checklist to `[DONE] PASS` and advance W3.
