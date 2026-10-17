# UAT-033 Development Report

> Generated: 2026-10-17 06:38 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-033] legacy缺汇率单据被排除但部门报表未显示API缺汇率说明 — P2 PRODUCT_FAULT MANUAL_UI
> Git: no commit / no push (per dispatch)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Fix applied (API count fields + UI notice) | PASS |
| Specialty pytest (3) + B2 unconverted regression (1) | PASS (4) |
| Isolated port 15155 API + Puppeteer UI self-check | PASS |
| Tenant B baseline unchanged | PASS |
| Fixture cleanup (finally) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

1. Backend already excluded missing-FX receipts from totals and returned Form-B2 fields
   `unconverted` + `unconverted_warning` (`demo/app/api_suppliers.py` cost_report,
   `demo/app/services/cost_report.py` `build_allocations` / `unconverted_warning`).
2. UI only checked nonexistent numeric fields:
   `Number(d.unconverted_count || d.missing_fx_count || 0)` — always 0 — so the
   prepared Chinese notice never appended (`demo/static/js/cost_report.js`).
3. Empty-state early return (`month_total===0` and no depts) exited with
   「暂无进货记录」**before** the FX notice path, so a sole legacy NFX receipt
   produced zero totals with **no** explanation.

Negative boundary respected: totals still exclude unconverted receipts; new-receipt
approve path unchanged (`FxRateMissing` / 422); no claim that approve can bypass FX.

## Fix

### `demo/app/services/cost_report.py`

- Added `unconverted_fields(unconverted)` returning:
  `unconverted`, `unconverted_count`, `missing_fx_count` (alias), `unconverted_warning`.

### `demo/app/api_suppliers.py`

- `GET /api/cost_report` spreads `**cr.unconverted_fields(...)` instead of only the
  two legacy keys (same list/warning semantics; counts now explicit for UI).

### `demo/static/js/cost_report.js`

- `costReportMissingFxCount` / `costReportMissingFxNotice` prefer `unconverted[]`
  length and API `unconverted_warning`.
- Empty early-return requires `missFx === 0` so excluded FX receipts still surface
  the notice with correct zero (or partial) totals.

### Tests

- `demo/tests/test_uat_033_cost_report_missing_fx_notice.py` (new):
  1. legacy NFX exclude + counts/warning + HKD companion totals; restore after FX;
     new XXX approve 422; Tenant B unchanged
  2. only-NFX empty month still exposes count/warning
  3. static UI contract for helpers / early-return

## Repro steps (isolated port; 15010 kept free)

1. Temp DB + `AUTH_ENABLED=0` uvicorn on **15155** (avoided 15010 / 15130 / 15140 / 15450).
2. Tenant `uat033dev`: approve CNY receipt → delete `receipt_fx_snapshots` + clear
   tenant CNY rate → rewrite currency to `NFX` (legacy NFX fixture). Add HKD 20 companion.
3. `GET /api/cost_report?month=2026-10` → `month_total=20`, `unconverted_count=1`,
   warning mentions NFX / 汇率.
4. Owner UI (Puppeteer, headers `X-Role: owner` / `X-Tenant-Id: uat033dev`) →
   `#costReportSummary` contains the warning and `HK$20.00`.
5. Put FX `NFX=2.0` → totals `220`, warning cleared; UI summary has `HK$220.00` and no 汇率.
6. New receipt currency forced to `XXX` → approve **422** `FX_RATE_MISSING`.
7. Tenant `uat033tenantb` report stays `month_total=0` throughout.
8. Finally: delete tenant receipts + FX rows; stop uvicorn; port free.

## Self-check mapping (4 positive criteria)

| AC | Evidence | Status |
|---|---|---|
| 1. legacy NFX: API excludes 1 + UI 1-receipt missing-FX notice; totals correct | `logs/api-before-fx.json`, `logs/ui-before-fx.json`, screenshot `ui/cost-report-missing-fx.png` | PASS |
| 2. After FX: notice clears; totals restore | `logs/api-after-fx.json`, `logs/ui-after-fx.json`, `ui/cost-report-after-fx.png` | PASS |
| 3. finally clean fixture | `FINAL_CLEANUP_OK` (receipts for tenant emptied); server stopped | PASS |
| 4. New receipt missing FX still 422 on approve | `logs/api-after-fx-selfcheck.txt` `NEW_APPROVE_422_OK` | PASS |
| Negative: legacy not in totals; no approve bypass | month_total 20 then 220; XXX→422 | PASS |
| Tenant B unchanged | baseline/after assertions | PASS |

## Commands actually run

```text
node --check demo/static/js/cost_report.js
python3 -m py_compile demo/app/services/cost_report.py demo/app/api_suppliers.py

.../venv/bin/pytest demo/tests/test_uat_033_cost_report_missing_fx_notice.py \
  demo/tests/test_b2_cost_report.py::test_unconverted_currency_is_listed_not_summed -v
# 4 passed

# Isolated 15155 + scripts under docs/handoff/artifacts/uat033-dev/scripts/
# UI_BEFORE_OK / UI_AFTER_OK / NEW_APPROVE_422_OK / CLEANED_SERVER
```

## Files modified

| Path | Change |
|---|---|
| `demo/app/services/cost_report.py` | `unconverted_fields` |
| `demo/app/api_suppliers.py` | cost_report response uses `unconverted_fields` |
| `demo/static/js/cost_report.js` | missing-FX helpers + empty-return guard |
| `demo/tests/test_uat_033_cost_report_missing_fx_notice.py` | specialty tests (new) |
| `docs/handoff/01-acceptance-checklist.md` | `[UAT-033]` → `[TODO] DEV_DELIVERED` only |
| `docs/handoff/02-coordinator-ledger.md` | DEV note; **next gate still UAT-033 QA** (not advanced past) |
| `docs/handoff/artifacts/uat033-dev-report.md` | this report |
| `docs/handoff/artifacts/uat033-dev/` | logs / ui / scripts / temp db |

No git commit. No git push. No independent QA/UAT dispatched.

## Scheduler handoff

- Dispatch **independent QA** next (fresh isolation port ≠ 15155; do not reuse this report as PASS).
- After QA PASS, dispatch independent UAT (MANUAL_UI).
- Only then flip `[UAT-033]` to `[DONE] PASS`.
- Do not advance ledger next-card past UAT-033 until that card closes.
