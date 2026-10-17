# UAT-015 Independent UAT Report

> Generated: 2026-10-17 19:43 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform the development or QA)
> Card: [UAT-015] 外币应付金额被错误标为港币原值 — P1 PRODUCT_FAULT / MANUAL_UI
> Prior: `uat015-dev-report.md`, `uat015-qa-report.md`
> Constraints: no git push; keep port 15010 FREE_RESERVED; fresh isolate port ≠15521 ≠15491; fix only if AC fails; do not strip checklist DONE PASS

## Overall (UAT re-verify)

| Criterion | Verdict |
|---|---|
| 1. Synthetic SGD100, snapshot FX 5 → HKD500; archive original S$100; payable/pay HK$500 | **PASS** — archive currency=SGD total=100; payables row total_hkd=500 outstanding=500 has_fx_snapshot=true fx_snapshot_rate=5.0; UI `HK$500.00` + `原币 S$ 100.00` |
| 2. Partial pay HKD100 → unpaid 400; full pay → 0 | **PASS** — after partial: outstanding 400 / payment_status=partial; after full 400: outstanding 0 / paid 500 / paid |
| 3. After changing live FX, historical still uses snapshot | **PASS** — live FX set to 6.0; payable still total_hkd=500 outstanding=400 fx_snapshot_rate=5.0 |
| 4. Missing-snapshot legacy shows unknown — do not hard-label HKD | **PASS** — fx_missing=true total_hkd=null; pay rejected FX_RATE_MISSING; UI `未知 汇率未知` + `原币 S$ 120.00` (not HK$120) |
| Negative: overpayment + tenant isolation; principal/payment cross-currency correct | **PASS** — overpay → PAYMENT_OVER_OUTSTANDING; tenant B no A payables; B cannot pay A receipt (404 RECEIPT_NOT_FOUND) |
| Product fix needed? | **No** — HEAD already correct (`api_finance.py` snapshot/total_hkd/fx_missing; `finance.js` / `finance_ops.js` 原币 + 汇率未知) |

**UAT-015 UAT re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger); local DEV + QA + UAT packets now complete (three-phase local evidence).

## Environment

- Python 3.13 / pytest / Playwright (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15651** (≠ DEV 15491, ≠ QA 15521); fresh DB `uat015-uat/environment/uat015.db` (+ backup `db/uat015.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id`
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (re-verify only)

## AC evidence (pytest)

`demo/tests/test_uat_015_currency_payables.py` on isolate DB → **4 passed** (`logs/pytest-uat015.txt`)

## AC evidence (live API)

Probe: `uat015-shared/probe_currency_payables.py` → `evidence/currency-payables-probe.json` (**18/18 PASS**)

| Step | Result |
|---|---|
| ac1_payables_hkd500 | SGD100 @5 → HKD500; archive SGD100; unpaid_credit_total=500 |
| ac2_partial / full | unpaid 400 then 0 (paid 500) |
| ac3_historical_still_snapshot | live FX 6; snapshot 5 / HKD500 / outstanding 400 |
| ac4_missing_snapshot_unknown | total_hkd null; FX_RATE_MISSING on pay |
| neg_overpayment / tenant_b | PAYMENT_OVER_OUTSTANDING; B isolated (404) |

## AC evidence (browser)

Probe: `uat015-shared/probe_payables_ui.py` → `evidence/payables-ui-probe.json` (**4/4 PASS**)

- Pane shows `#8 Vendor_UI_SGD` → `HK$500.00` / `原币 S$ 100.00`
- Legacy `#9` → `未知 汇率未知` / `原币 S$ 120.00` (not hard-labeled HKD)
- Payment modal: amount `HK$500.00` + original SGD; pay balance HKD
- Screenshots: `ui/01-payables-pane.png`, `ui/02-payment-modal.png`, `ui/03-payables-full.png`

## Code contract (unchanged HEAD)

- `demo/app/api_finance.py`: snapshot → `total_hkd` / `fx_snapshot_rate`; missing → `fx_missing` + `total_hkd=None`
- `demo/static/js/finance.js`: foreign missing FX → `汇率未知`; else `I18N.fmtMoney(total_hkd)` + `原币`
- `demo/static/js/finance_ops.js`: same unknown / 原币 contract on payables table

Static contract scan: `logs/static-contract.txt` → ALL_PASS

## Evidence paths

- Report: `docs/handoff/artifacts/uat015-uat-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat015-uat/`
  - `logs/health.json`, `uvicorn.log`, `pytest-uat015.txt`, `probe-api.txt`, `probe-ui.txt`, `static-contract.txt`
  - `evidence/currency-payables-probe.json`, `payables-ui-probe.json`, `uat-summary.json`
  - `ui/01-payables-pane.png` … `03-payables-full.png`
  - `db/uat015.db`, `environment/{pre-state.txt,cleanup.json,port.txt,uat015.db}`
- Shared scripts: `docs/handoff/artifacts/uat015-shared/{probe_currency_payables.py,probe_payables_ui.py,run_phase.sh}`

## Checklist annotation

- Did **not** strip `[DONE] PASS`
- Updated UAT-015 status note: local DEV + QA + UAT packets complete (`uat015-{dev,qa,uat}-report.md`)

## Cleanup / 15010

- Isolate uvicorn 15651 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Next gate

Per `artifacts/partials-triage.md`: **UAT-001** (ledger-only, no local packet — re-verify + evidence).
