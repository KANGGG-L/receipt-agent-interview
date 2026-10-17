# UAT-033 Independent QA Report

> Generated: 2026-10-17 06:40 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-033 acceptance only (legacy missing-FX notice on department cost report; totals exclude unconverted; restore after FX; cleanup; new-approve still 422)
> Card: [UAT-033] legacy缺汇率单据被排除但部门报表未显示API缺汇率说明 — P2 PRODUCT_FAULT MANUAL_UI
> Prior: `uat033-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Legacy NFX fixture: API excludes 1 + UI missing-FX notice; totals correct (exclude from sum) | **PASS** |
| 2. After adding FX: notice clears; totals restore | **PASS** |
| 3. finally clean fixture | **PASS** |
| 4. New receipt missing FX approve still 422 | **PASS** |
| Negative: legacy missing-FX must not enter totals | **PASS** (`month_total=20` with NFX excluded; `220` after FX) |
| Tenant B baseline unchanged | **PASS** (`month_total=0`, `unconverted_count=0` throughout) |
| Specialty pytest (3) + B2 unconverted regression (1) | **PASS** (4) |

**UAT-033 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static / unit
   - `python -m py_compile demo/app/services/cost_report.py demo/app/api_suppliers.py` — OK
   - `node --check demo/static/js/cost_report.js` — OK
   - `pytest demo/tests/test_uat_033_cost_report_missing_fx_notice.py` + `test_b2_cost_report.py::test_unconverted_currency_is_listed_not_summed` — **4 passed** (`logs/pytest-uat033.txt`)
2. Isolated uvicorn on **15165** (fresh; ≠ **15155**; avoided **15010** / **15130** / **15140** / **15450**)
   - `DB_PATH=…/uat033-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid **80226** → stopped (`environment/cleanup.json`)
3. Live API + Puppeteer UI — scripts under `docs/handoff/artifacts/uat033-qa/scripts/`
   - Tenants `uat033qa` / `uat033qab` (QA-owned; not DEV's `uat033dev`)
   - `API_BEFORE_OK` / `UI_BEFORE_OK` / `API_AFTER_FX_OK` / `UI_AFTER_OK` / `NEW_APPROVE_422_OK` / `CLEANUP_OK`

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — legacy NFX: API exclude + UI notice; totals exclude (PASS)

Live against `http://127.0.0.1:15165`, tenant `uat033qa`.

| Check | Expected | Observed |
|---|---|---|
| `month_total` | 20.0 (HKD companion only) | **20.0** |
| `unconverted_count` / `missing_fx_count` | 1 | **1** / **1** |
| `unconverted[0].currency` | NFX | **NFX** |
| `unconverted_warning` | contains 汇率 + 1 | `有 1 张单据是「NFX」，设置里没有对应汇率，没有算进合计。请老板到「设置」里填好汇率。` |
| UI `#costReportSummary` | notice + HK$20.00 | contains 汇率 / 1 / `HK$20.00` |
| Helpers | `costReportMissingFxCount` / `Notice` present | sample count **1** |

Evidence: `logs/api-before-fx.json`, `logs/ui-before-fx.json`, screenshot `ui/cost-report-missing-fx.png`.

## Criterion 2 — after FX: notice clears; totals restore (PASS)

| Check | Expected | Observed |
|---|---|---|
| Put `NFX=2.0` then `month_total` | 220.0 (20 + 100×2) | **220.0** |
| counts / unconverted / warning | 0 / empty / "" | **0** / empty / **""** |
| UI summary | `HK$220.00`, no 汇率 | **cleared=true** |

Evidence: `logs/api-after-fx.json`, `logs/ui-after-fx.json`, screenshot `ui/cost-report-after-fx.png`.

## Criterion 3 — finally clean fixture (PASS)

| Check | Result |
|---|---|
| Delete tenant `uat033qa` receipts + FX rows | done |
| Post-cleanup `GET /api/cost_report?month=2026-10` | `month_total=0.0`, `unconverted_count=0` |
| uvicorn stopped; port 15165 free; reuseaddr bind OK | yes |

Evidence: `logs/api-422-cleanup.txt`, `logs/cleanup-report.json`, `environment/cleanup.json`.

## Criterion 4 — new receipt missing FX still 422 (PASS)

| Check | Result |
|---|---|
| Save as CNY → rewrite currency `XXX` → approve | **422** `FX_RATE_MISSING` |
| Receipt status | not `approved` |
| Report totals after failed approve | still **220.0** (XXX not entered) |

Evidence: `logs/api-approve-422.json`.

## Negative / Tenant B / isolation

| Field | Value |
|---|---|
| Legacy NFX in totals | **no** (20 then 220 only after FX) |
| New approve bypass | **no** (422) |
| Tenant B `uat033qab` | baseline `month_total=0` before / after / final |
| Port | **15165**; avoided **15155**, **15010**, **15130**, **15140**, **15450**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/uat033-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 |
| Checklist / ledger | **not** flipped (still `[TODO] DEV_DELIVERED`) |
| Git | no commit / no push |
| UAT | not started |

## Residual risks (honest)

1. Screenshots `cost-report-missing-fx.png` and `cost-report-after-fx.png` share the same MD5 — full-page capture likely dominated by chrome identical across states. **DOM/API JSON still PASS** with distinct summaries (`HK$20.00`+汇率 vs `HK$220.00` without). Independent UAT should confirm the notice paint by eye.
2. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
3. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- UAT-033 **QA PASS** (4/4 AC + Tenant B + negative + specialty 4 pytest).
- Please dispatch **independent UAT** next (MANUAL_UI; fresh port ≠ 15165 / ≠ 15155; avoid 15010 / 15130 / 15140 / 15450).
- After UAT PASS: flip `[UAT-033]` to DONE PASS.
