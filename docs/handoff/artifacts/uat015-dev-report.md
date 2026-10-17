# UAT-015 Development Re-verify Report

> Generated: 2026-10-17 19:41 Asia/Shanghai (CST)
> Role: Development re-verify only (independent QA / UAT NOT run in this stage)
> Card: [UAT-015] 外币应付金额被错误标为港币原值 — P1 PRODUCT_FAULT / MANUAL_UI
> Constraints: no git push; keep port 15010 free/reserved; fix only if AC fails
> Note: Ledger already PASS; this packet supplies local DEV evidence only. QA/UAT packets still required for full three-phase local closure.

## Overall (DEV re-verify)

| Criterion | Verdict |
|---|---|
| 1. Synthetic SGD100, snapshot FX 5 → HKD500; archive original S$100; payable/pay HK$500 | **PASS** — archive currency=SGD total=100; payables currency=HKD total_outstanding=500; row total_hkd=500 has_fx_snapshot=true; UI `HK$500.00` + `原币 S$ 100.00` |
| 2. Partial pay HKD100 → unpaid 400; full pay → 0 | **PASS** — after partial: outstanding 400 / payment_status=partial; after full 400: outstanding 0 / paid 500 / paid |
| 3. After changing live FX, historical still uses snapshot | **PASS** — live FX set to 6.0; payable still total_hkd=500 outstanding=400 fx_snapshot_rate=5.0 |
| 4. Missing-snapshot legacy shows unknown — do not hard-label HKD | **PASS** — fx_missing=true total_hkd=null; pay rejected FX_RATE_MISSING; UI `未知 汇率未知` + `原币 S$ 120.00` (not HK$120) |
| Negative: overpayment + tenant isolation; principal/payment cross-currency correct | **PASS** — pay 550 → PAYMENT_OVER_OUTSTANDING; tenant B no A payables; B cannot pay A receipt (404) |
| Product fix needed? | **No** — HEAD already correct (`api_finance.py` snapshot/total_hkd/fx_missing; `finance.js` / `finance_ops.js` 原币 + 汇率未知) |

**UAT-015 DEV re-verify: PASS.** Checklist remains `[DONE] PASS` (ledger). Local note: DEV packet present; QA/UAT local packets still pending.

## Environment

- Python 3.13 / pytest (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15491** (fresh DB `uat015-dev/environment/uat015.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id`
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (probe-only zero-falsy assert fix in shared probe script)

## AC evidence (pytest)

`demo/tests/test_uat_015_currency_payables.py` on isolate DB → **4 passed** (`logs/pytest-uat015.txt`)

## AC evidence (live API)

Probe: `uat015-shared/probe_currency_payables.py` → `evidence/currency-payables-probe.json` (**18/18 PASS**)

| Step | Result |
|---|---|
| ac1_payables_hkd500 | SGD100 @5 → HKD500; archive SGD100 |
| ac2_partial / full | unpaid 400 then 0 |
| ac3_historical_still_snapshot | live FX 6; snapshot 5 / HKD500 |
| ac4_missing_snapshot_unknown | total_hkd null; FX_RATE_MISSING on pay |
| neg_overpayment / tenant_b | 400 PAYMENT_OVER_OUTSTANDING; B isolated |

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

- Report: `docs/handoff/artifacts/uat015-dev-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat015-dev/`
  - `logs/health.json`, `uvicorn.log`, `pytest-uat015.txt`, `probe-api.txt`, `probe-ui.txt`, `static-contract.txt`
  - `evidence/currency-payables-probe.json`, `payables-ui-probe.json`, `dev-summary.json`
  - `ui/01-payables-pane.png` … `03-payables-full.png`
  - `db/uat015.db`, `environment/{pre-state.txt,cleanup.json,port.txt}`
- Shared scripts: `docs/handoff/artifacts/uat015-shared/{probe_currency_payables.py,probe_payables_ui.py,run_phase.sh}`

## Cleanup / 15010

- Isolate uvicorn 15491 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Not done (out of DEV scope)

- Independent QA packet (`uat015-qa-report.md`)
- Independent UAT packet (`uat015-uat-report.md`)
