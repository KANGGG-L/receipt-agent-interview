# UAT-004 Independent QA Report

> Generated: 2026-10-17 19:29 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Card: [UAT-004] 供应商可选电话和备注留空时保存422 — P1 PRODUCT_FAULT / AUTO_API
> Prior: `uat004-dev-report.md` (DEV re-verify PASS, no product edit)
> Constraints: no git; keep port 15010 FREE_RESERVED; fresh isolate port ≠15461; fix only if AC fails; do not strip checklist DONE PASS
> UAT: NOT started (per dispatch: No UAT)

## Overall (QA re-verify)

| Criterion | Verdict |
|---|---|
| 1. name=Vendor_A only, rest UI defaults → save HTTP success; phone/notes empty-string norm | **PASS** — API UI-default payload `contact_phone:""` `notes:""` `settlement_pref:null` → 200; readback `""`/`""`. Live browser: name-only modal save POST 200 with same empty optional fields |
| 2. Fill both fields then read back | **PASS** — PATCH phone=`91234567` notes=`note-uat004` → readback exact |
| 3. HTTP null / omitted / empty-string covered separately | **PASS** — create omitted/null/empty-string → `""`; patch empty-string clears; patch null clears; patch omit leaves phone/notes unchanged (6/6) |
| 4. Explicit empty settlement still 422 (existing contract) | **PASS** — create+patch `settlement_pref:""` → HTTP 422 (`VALIDATION_ERROR` on field `settlement_pref`; source raises `结算方式不能为空`) |
| Negative: 422 only for settlement empty; credit still OK; Tenant B unchanged | **PASS** — empty phone/notes not 422; credit settlement create OK; B cannot see/patch A (404); no A name leak into B |
| Regression S1-P0-05 (card scope) | **PASS** — `test_departments_are_tenant_scoped` 1 passed on isolate DB |

**UAT-004 QA re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger). Local note: DEV+QA packets present; UAT local packet still pending.

## Environment

- Python 3.13.5 / pytest 9.1.1 (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15471** (≠ DEV 15461); fresh DB `uat004-qa/environment/uat004.db` (+ backup `db/uat004.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id`
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (re-verify only)

## AC evidence (API)

Probe: `uat004-shared/probe_supplier_optional.py` → `evidence/supplier-optional-probe.json` (10/10 PASS)

| Step | Result |
|---|---|
| ac1_ui_default_create_http | 200 success id=1 |
| ac1_ui_default_readback_empty_norm | phone=`""` notes=`""` |
| ac2_fill_both_readback | phone/notes match filled values |
| ac3_null_omitted_empty_string | 6 variants all PASS |
| ac4_explicit_empty_settlement_422 | create+patch 422 on `settlement_pref` |
| neg_empty_phone_notes_not_422 | 200 |
| neg_credit_settlement_still_ok | credit + empty phone OK |
| neg_tenant_b_isolation | B list/hack/leak PASS (hack 404) |
| ui_payload_contract_static | `supplier_archive.js` saveSupplier sends empty phone/notes + null settlement when blank |

## AC evidence (browser)

Probe: `uat004-shared/probe_supplier_ui.py` → `evidence/supplier-ui-probe.json` (3/3 PASS)

- Pane: archive → `showArchivePane('suppliers')` → open modal → name=`Vendor_A_UI` only
- POST body: `{"name":"Vendor_A_UI","contact_phone":"","notes":"","settlement_pref":null,"payment_terms_days":30}` (30 = form default terms)
- HTTP 200; readback phone/notes empty strings
- Screenshots: `ui/01-supplier-pane.png`, `ui/02-modal-name-only.png`, `ui/03-after-save.png`

## Code contract (unchanged HEAD)

- `demo/app/api_suppliers.py`: `field_validator("settlement_pref")` rejects explicit empty (`ValueError("结算方式不能为空")`); `_optional_text` normalizes phone/notes null/omit/empty → `""` on create; patch omit does not clear
- `demo/static/js/supplier_archive.js`: `saveSupplier` always sends trimmed phone/notes (empty → `""`) and `settlement_pref: pref || null`

## Evidence paths

- Report: `docs/handoff/artifacts/uat004-qa-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat004-qa/`
  - `logs/health.json`, `uvicorn.log`, `supplier-optional-probe.txt`, `supplier-ui-probe.txt`, `pytest-s1p005.txt` / `.xml`
  - `evidence/supplier-optional-probe.json`, `supplier-ui-probe.json`, `qa-summary.json`
  - `ui/01-supplier-pane.png` … `03-after-save.png`
  - `db/uat004.db`, `environment/cleanup.json`, `pre-state.txt`, `port.txt`, `uat004.db`
- Shared scripts: `docs/handoff/artifacts/uat004-shared/probe_supplier_optional.py`, `probe_supplier_ui.py`

## Checklist annotation

- Did **not** strip `[DONE] PASS`
- Updated UAT-004 status note: local QA packet `uat004-qa-report.md` present; UAT local packet still pending

## Cleanup / 15010

- Isolate uvicorn 15471 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git

## Not done (out of QA scope)

- Independent UAT packet (`uat004-uat-report.md`)
- Do not treat DEV+QA alone as three-phase local close beyond ledger DONE + packet annotations

## Next gate

Independent UAT: new isolate port/DB (≠15461, ≠15471); re-run API AC1–4 + Tenant B + settlement 422; optional browser name-only save; then annotate UAT packet on checklist without stripping DONE PASS.
