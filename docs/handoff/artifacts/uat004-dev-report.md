# UAT-004 Development Re-verify Report

> Generated: 2026-10-17 19:28 Asia/Shanghai (CST)
> Role: Development re-verify only (independent QA / UAT NOT run in this stage)
> Card: [UAT-004] 供应商可选电话和备注留空时保存422 — P1 PRODUCT_FAULT / AUTO_API
> Constraints: no git push; keep port 15010 free/reserved; fix only if AC fails
> Note: Ledger already PASS; this packet supplies local DEV evidence only. QA/UAT packets still required for full three-phase local closure.

## Overall (DEV re-verify)

| Criterion | Verdict |
|---|---|
| 1. name=Vendor_A only, rest UI defaults → save HTTP success; phone/notes empty-string norm | **PASS** — API UI-default payload `contact_phone:""` `notes:""` `settlement_pref:null` → 200; readback `""`/`""`. Live browser: name-only modal save POST 200 with same empty optional fields |
| 2. Fill both fields then read back | **PASS** — PATCH phone=`91234567` notes=`note-uat004` → readback exact |
| 3. HTTP null / omitted / empty-string covered separately | **PASS** — create omitted/null/empty-string → `""`; patch empty-string clears; patch null clears; patch omit leaves phone/notes unchanged |
| 4. Explicit empty settlement still 422 (existing contract) | **PASS** — create+patch `settlement_pref:""` → HTTP 422 (`结算方式不能为空`) |
| Negative: 422 only for settlement empty; credit still OK; Tenant B unchanged | **PASS** — empty phone/notes not 422; credit settlement create OK; B cannot see/patch A (404); no A name leak into B |
| Regression S1-P0-05 (card scope) | **PASS** — `test_departments_are_tenant_scoped` 1 passed on isolate DB |
| Product fix needed? | **No** — HEAD already has `SupplierBody` settlement-only validator + `_optional_text` for phone/notes; re-verify only |

**UAT-004 DEV re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger). Local note: DEV packet present; QA/UAT local packets still pending.

## Environment

- Python 3.13.5 / pytest 9.1.1 (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15461** (fresh DB `uat004-dev/environment/uat004.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id`
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none**

## AC evidence (API)

Probe: `uat004-shared/probe_supplier_optional.py` → `evidence/supplier-optional-probe.json` (10/10 PASS)

| Step | Result |
|---|---|
| ac1_ui_default_create_http | 200 success id |
| ac1_ui_default_readback_empty_norm | phone=`""` notes=`""` |
| ac2_fill_both_readback | phone/notes match filled values |
| ac3_null_omitted_empty_string | 6 variants all PASS |
| ac4_explicit_empty_settlement_422 | create+patch 422 |
| neg_empty_phone_notes_not_422 | 200 |
| neg_credit_settlement_still_ok | credit + empty phone OK |
| neg_tenant_b_isolation | B list/hack/leak PASS |
| ui_payload_contract_static | `supplier_archive.js` saveSupplier sends empty phone/notes + null settlement when blank |

## AC evidence (browser)

Probe: `uat004-shared/probe_supplier_ui.py` → `evidence/supplier-ui-probe.json` (3/3 PASS)

- Pane: archive → `showArchivePane('suppliers')` → open modal → name=`Vendor_A_UI` only
- POST body: `{"name":"Vendor_A_UI","contact_phone":"","notes":"","settlement_pref":null,"payment_terms_days":30}` (30 = form default terms)
- HTTP 200; readback phone/notes empty strings
- Screenshots: `ui/01-supplier-pane.png`, `ui/02-modal-name-only.png`, `ui/03-after-save.png`

## Code contract (unchanged HEAD)

- `demo/app/api_suppliers.py`: `field_validator("settlement_pref")` rejects explicit empty; `_optional_text` normalizes phone/notes null/omit/empty → `""` on create; patch omit does not clear
- `demo/static/js/supplier_archive.js`: `saveSupplier` always sends trimmed phone/notes (empty → `""`) and `settlement_pref: pref || null`

## Evidence paths

- Report: `docs/handoff/artifacts/uat004-dev-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat004-dev/`
  - `logs/health.json`, `uvicorn.log`, `supplier-optional-probe.txt`, `supplier-ui-probe.txt`, `pytest-s1p005.txt` / `.xml`
  - `evidence/supplier-optional-probe.json`, `supplier-ui-probe.json`, `dev-summary.json`
  - `ui/01-supplier-pane.png` … `03-after-save.png`
  - `db/uat004.db`, `environment/cleanup.json`, `pre-state.txt`, `uat004.db`
- Shared scripts: `docs/handoff/artifacts/uat004-shared/probe_supplier_optional.py`, `probe_supplier_ui.py`

## Cleanup / 15010

- Isolate uvicorn 15461 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Not done (out of DEV scope)

- Independent QA packet (`uat004-qa-report.md`)
- Independent UAT packet (`uat004-uat-report.md`)
- Do not treat this DEV packet alone as three-phase local close beyond ledger DONE

## Next gate

Independent QA: new isolate port/DB; re-run API AC1–4 + Tenant B + settlement 422; optional browser name-only save; leave checklist DONE until QA+UAT local packets also exist.
