# UAT-017 Development Report

> Generated: 2026-10-17 04:30 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-017] 对账创建弹窗缺dialog语义 — P2 PRODUCT_FAULT
> Vendor aliases in fixtures: n/a (a11y / modal only)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Dialog semantics on `#reconCreateModal` | PASS (static markup + open path) |
| Focus trap / Esc restore wiring | PASS (reuses existing accessible-modal stack) |
| Specialty pytest (4) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.

## Root cause (printed citations)

1. Create-recon markup had no dialog role / name:
   - Pre-fix `demo/templates/index.html` `#reconCreateModal` was a bare `modal-backdrop hide` without `role="dialog"`, `aria-modal`, or `aria-labelledby`.
   - Sibling `importStatementModal` already had `role="dialog" aria-modal="true"` (`index.html:4179` after edit; import block follows create block), exposing the create modal as the outlier.
2. Open path bypassed the accessible-modal registry:
   - Pre-fix `openReconModal` only did `document.getElementById('reconCreateModal').classList.remove('hide')`, so `_activeAccessibleModals` never recorded a trigger.
   - Existing helpers already provide the contract:
     - `demo/static/js/review_modal.js:27-57` — `openAccessibleModal` sets `role=dialog` / `aria-modal=true`, pushes `{modal, trigger}`, focuses preferred control.
     - `demo/static/js/review_modal.js:60-70` — `closeAccessibleModal` hides and restores `item.trigger.focus`.
     - `demo/static/js/admin_observation.js:1600-1664` — global Esc → `closeAccessibleModal(modal)`; Tab cycles via `getModalFocusableElements`.
   - `closeModalById` / `openModalById` in `finance_ops.js:22-38` already delegate to those helpers; create-recon alone skipped them on open.

## Fix

### Template — `demo/templates/index.html`

- `4136-4170` — `#reconCreateModal`:
  - `4137` — `role="dialog" aria-modal="true" aria-labelledby="reconCreateTitle" aria-describedby="reconSupplierHint"`.
  - `4140` — title `h3#reconCreateTitle`「发起对账」(accessible name).
  - `4141` — close control `type="button"` + `aria-label="关闭"`.
  - `4147-4161` — labels `for=` on period / start / end fields.
  - `4165-4166` — `#btnCancelReconCreate` / `#btnSubmitReconCreate` with `type="button"`.

### Frontend — `demo/static/js/finance_ops.js`

- `611-639` — `openReconModal`:
  - Fills hint / resets fields as before.
  - Ensures `aria-labelledby` / `aria-describedby` if missing.
  - Calls `openAccessibleModal(modal, '#reconPeriodType', trigger)` so Esc/Tab stack and focus restore apply.
  - Fallback: `openModalById('reconCreateModal')` if helper absent.
- `646-666` — `submitReconCreate` still closes via `closeModalById('reconCreateModal')` (unchanged success path → `closeAccessibleModal`).

### Unchanged (negative boundary)

- Did **not** rewrite `paymentModal`, `reconResolveModal`, or other backdrops.
- Did **not** alter Esc/Tab global listener beyond reusing it.
- Submit / cancel CSP click handlers and POST `/api/reconciliation` payload unchanged.

### Tests

- `demo/tests/test_uat_017_recon_create_dialog.py` (new):
  1. HTML has dialog / aria-modal / labelledby / describedby / title / cancel / submit / label `for=`
  2. `openReconModal` uses `openAccessibleModal`, no bare `classList.remove('hide')`
  3. `openAccessibleModal` / `closeAccessibleModal` / trigger focus restore still present
  4. Scope boundary: payment / resolve modal tags do not gain `reconCreateTitle`

## Commands actually run

```text
node --check demo/static/js/finance_ops.js
# exit 0

.../venv/bin/pytest demo/tests/test_uat_017_recon_create_dialog.py -v
# 4 passed in 0.97s
```

Browser MANUAL_UI (isolation port + AXTree dialog name + Tab trap + Esc focus restore + Hans/Hant + 390) is **reserved for independent QA/UAT** — not claimed here.

## Acceptance mapping (dev evidence only)

| AC | Dev evidence | Status |
|---|---|---|
| 1. Open create-recon → AXTree has dialog + name | Static `role=dialog` + `aria-labelledby=reconCreateTitle`; open via `openAccessibleModal` | PASS (static+wiring; AXTree runtime for QA/UAT) |
| 2. Tab cycles inside; Esc closes and returns trigger focus | Registered on `_activeAccessibleModals`; Esc/Tab in `admin_observation.js:1600+`; restore `review_modal.js:66-67` | PASS (wiring); MANUAL_UI for QA/UAT |
| 3. Submit / cancel usable; Hans/Hant & 390 no occlusion | Cancel/submit ids + existing handlers; no CSS layout change | PASS (handlers preserved); layout MANUAL_UI for QA/UAT |
| Negative: keep existing Esc/focus; do not rewrite all modals | Only `#reconCreateModal` markup + `openReconModal`; specialty test #4 | PASS |

## Files modified

| Path | Change |
|---|---|
| `demo/templates/index.html` | `#reconCreateModal` dialog ARIA + labelled controls |
| `demo/static/js/finance_ops.js` | `openReconModal` → `openAccessibleModal` |
| `demo/tests/test_uat_017_recon_create_dialog.py` | specialty static pytest (4) |
| `docs/handoff/01-acceptance-checklist.md` | **UAT-010** closed to `[DONE] PASS` (not UAT-017) |
| `docs/handoff/02-coordinator-ledger.md` | UAT-010 closed; next W3 = UAT-017; UAT-017 DEV note |
| `docs/handoff/artifacts/uat017-dev-report.md` | this report |

No git commit. No git push. No independent QA/UAT dispatched.

## Scheduler handoff

- UAT-010 docs closed (`[DONE] PASS`, evidence uat010-dev/qa/uat reports).
- Ledger next W3 atomic = **UAT-017**.
- UAT-017 **dev complete**; please dispatch **independent QA** next (fresh isolation port 15xxx, real browser AXTree / Tab / Esc / submit-cancel / locale+390).
- After QA PASS, dispatch independent UAT.
- Only then flip `[UAT-017]` checklist to `[DONE] PASS`.
