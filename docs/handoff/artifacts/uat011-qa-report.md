# UAT-011 Independent QA Report

> Generated: 2026-10-17 19:36 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Card: [UAT-011] GT确权按钮同时创建真实业务待审单 — P1 PRODUCT_FAULT / MANUAL_UI
> Prior: `uat011-dev-report.md` (DEV re-verify PASS, no product edit)
> Constraints: no git; keep port 15010 FREE_RESERVED; fresh isolate port ≠15471; fix only if AC fails; do not strip checklist DONE PASS
> UAT: NOT started (per dispatch: No UAT)

## Overall (QA re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse gt-single-click + synthetic U001 | **PASS** — `uat011-shared/gt_single_click.py` + `setup_synth_u001.py` (U001/U002 draft GT, isolate EVALSET_DIR) |
| 2. One click: GT confirm exactly 1; save_edited zero; receipts/items/inventory/payments unchanged | **PASS** — confirm POST=1 HTTP 200; save_edited=0; totals stable `{receipts:2, receipt_items:2, inventory_batches:0, inventory_log:0, payments:0}` |
| 3. Double-click GT no duplicate business writes | **PASS** — U002 delayed-confirm dblclick → confirm=1, save_edited=0; business totals unchanged |
| 4. Leave eval → normal manual save still works | **PASS** — `/` manual entry → save_edited HTTP 200 success receipt_id=3; receipts/items grew on tenant A only |
| Negative: do not only dedupe GT while missing business save_edited; GT vs business DB isolated; Tenant B ok | **PASS** — confirm path never posts save_edited; B tables stable; B list shows only Marker_B (no A leak) |
| Product fix needed? | **No** — HEAD already isolates GT confirm (`eval_workbench.js` lock + button replace; `review_core.js` `isEvalWorkbenchContext` early return; `api_evalset.py` corpus-only write) |

**UAT-011 QA re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger). Local note: DEV+QA packets present; UAT local packet still pending.

## Environment

- Python 3.13.5 / Playwright chromium (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15511** (≠ DEV 15471); fresh DB `uat011-qa/environment/uat011.db` (+ backup `db/uat011.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id`
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (re-verify only)

## AC evidence (browser + DB)

Probe: `uat011-shared/gt_single_click.py` → `evidence/gt-single-click.json` (10/10 PASS)

| Step | Result |
|---|---|
| seed_markers | A+B each 1 receipt via save_edited (baseline non-zero) |
| gt_button_label | `确认本张 GT（进入下一张）` |
| ac_single_click_posts | confirm=1 status=200; save_edited=0 (other POSTs are csp-report only) |
| ac_db_unchanged_after_gt | A/B/ALL receipts+receipt_items+inventory_*+payments identical before/after |
| ac_gt_file_confirmed | `expected/U001.json` gt_status=confirmed, reviewed_by=admin@demo.hk |
| ac_double_click_no_dup_business | U002 dblclick under delayed confirm → confirm=1 save_edited=0; DB totals flat |
| leave_eval_context_cleared | meta/`__EVAL_WORKBENCH__` absent on `/` |
| ac_leave_eval_manual_save | save_edited=1 confirm=0; HTTP 200 success |
| ac_manual_save_wrote_business_db | A receipts 1→2, receipt_items 1→2 after leave-eval save |
| neg_tenant_b_isolation | B unchanged; no Marker_A / Vendor_Manual leak into B list |

Schema mapping for AC wording "items/inventory": `receipt_items`, `inventory_batches`, `inventory_log` (no bare `items`/`inventory` tables).

## Code contract (unchanged HEAD)

- `demo/static/js/eval_workbench.js`: `gtConfirmLock`; `replaceWithoutBusinessSave` strips CSP/`submitSaveEdited`; POST only `/api/evalset/sample/<sid>/confirm`
- `demo/static/js/review_core.js`: `isEvalWorkbenchContext()` → `submitSaveEdited` early return
- `demo/app/api_evalset.py`: confirm writes expected JSON + manifest only; comment documents no `link_or_create` / no receipts/items/inventory/payments writes

Static contract scan: `logs/static-contract.txt` → ALL_PASS

## Evidence paths

- Report: `docs/handoff/artifacts/uat011-qa-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat011-qa/`
  - `logs/health.json`, `uvicorn.log`, `gt-single-click.txt`, `setup-u001.txt`, `sample-u001.json`, `static-contract.txt`
  - `evidence/gt-single-click.json`, `qa-summary.json`, `evalset/{U001,U002}.json`, `evalset/manifest.csv`
  - `ui/01-workbench-u001.png` … `05-after-manual-save.png`
  - `db/uat011.db`, `environment/cleanup.json`, `pre-state.txt`, `uat011.db`, `evalsets/`
- Shared scripts: `docs/handoff/artifacts/uat011-shared/{setup_synth_u001.py,gt_single_click.py,run_phase.sh}`

## Checklist annotation

- Did **not** strip `[DONE] PASS`
- Updated UAT-011 status note: local QA packet `uat011-qa-report.md` present; UAT local packet still pending

## Cleanup / 15010

- Isolate uvicorn 15511 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git

## Not done (out of QA scope)

- Independent UAT packet (`uat011-uat-report.md`)
