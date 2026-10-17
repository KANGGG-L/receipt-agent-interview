# UAT-032 Development Report

> Generated: 2026-10-17 15:23 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-032] 同日餐品二次提交后FIFO弹窗仍显示第一次份数与成本 — P2 PRODUCT_FAULT MANUAL_UI
> Git: no commit / no push (per dispatch)
> Prerequisite: UAT-031 DONE PASS
> Isolate port: **15132** (15010 kept free)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS (post-submit FIFO modal read stale `dailyConsumptionCache` from first submit; `loadDailyConsumption` not awaited) |
| Product fix | PASS (backend cumulative snapshot + frontend await/refresh + always-fetch modal) |
| Specialty pytest (2) + merge regression (3) | PASS (5 passed) |
| Isolated HTTP self-check (31) | PASS |
| Playwright UI (3 then 2 → modal 售出 5 份 + table 5 + two FIFO detail lines) | PASS |
| Replay second request_id still qty 5 | PASS |
| Different day no leak | PASS |
| Tenant B unchanged | PASS |
| Port 15010 free | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

Backend same-day merge (WS5) was already correct: second submit with a new `request_id` accumulates quantity/cost and appends FIFO detail rows on the same consumption id. The UI bug was timing/cache:

1. `submitDailyConsumptionBatch` → `onSuccess` fired `loadDailyConsumption(dateStr)` **without awaiting**.
2. Immediately called `showCostTraceModal(consumption_ids[0])`.
3. `showCostTraceModal` preferred `dailyConsumptionCache`, which still held the **first** submit (qty 3, one detail set, first cost).
4. The history table later refreshed to 5, but the auto-opened FIFO modal already showed the first submit.

Negative boundary preserved: accumulate/idempotency untouched; table-alone is not used as substitute for dialog/cost evidence.

## Fix

### `demo/app/api_dishes.py`

- Added `_serialize_consumption_row(session, r, can_see_prices)` — same shape as GET `consumptions[]` (qty, costs, **all** FIFO details ordered by id).
- After batch commit, response `data.consumptions` carries full **post-merge** snapshots for affected ids (saved into request-id replay body too).
- Merge/idempotency write path unchanged.

### `demo/static/js/dish_consumption.js`

- `loadDailyConsumption` returns its Promise.
- `onSuccess` is async: seed cache from response `consumptions` snapshot → `await loadDailyConsumption` → open modal from snapshot (`renderCostTraceModalContent(fromSnap)`) or fresh fetch.
- `showCostTraceModal` always re-fetches the selected date list before render (cache fallback only on network error).

### `demo/templates/index.html`

- Cache bust: `dish_consumption.js?v=20261017_uat032`

### Tests

- `demo/tests/test_uat_032_same_day_fifo_modal_merge.py` (2 cases: 3+2 snapshot/replay/day isolation; third merge still accumulates).

## Diffs this turn (UAT-032 locus)

| Path | Change |
|---|---|
| `demo/app/api_dishes.py` | `_serialize_consumption_row` + batch `data.consumptions` snapshots |
| `demo/static/js/dish_consumption.js` | await refresh; snapshot-first modal; always-fetch `showCostTraceModal` |
| `demo/templates/index.html` | `?v=20261017_uat032` |
| `demo/tests/test_uat_032_same_day_fifo_modal_merge.py` | new specialty tests |
| `docs/handoff/artifacts/uat032-dev/**` | isolate evidence + scripts |
| `docs/handoff/artifacts/uat032-dev-report.md` | this report |
| `docs/handoff/01-acceptance-checklist.md` | UAT-032 → `[TODO] DEV_DELIVERED` only |

Note: working tree also carries prior-card edits in the same files; UAT-032-specific hunks are the serializer/snapshot, await/`fromSnap`, always-fetch modal, and cache-bust tag above.

## Repro / self-check (isolated port; 15010 free)

1. Temp DB `artifacts/uat032-dev/environment/uat032.db`, `AUTH_ENABLED=0`, uvicorn **127.0.0.1:15132**.
2. Specialty: `pytest tests/test_uat_032_same_day_fifo_modal_merge.py tests/test_daily_consumption_idempotent_merge.py` → **5 passed** (`logs/pytest-specialty.log`).
3. HTTP self-check `scripts/selfcheck_uat032.py` → **31/31 PASS** (`logs/selfcheck-evidence.json`): response merged qty 5, consumptions snapshot qty 5 / cost 25 / 2 details, GET table matches, replay still 5, day B isolated, tenant B stock unchanged, static JS served with fix markers.
4. Playwright UI `scripts/ui_fifo_modal.py` → modal `#traceDishQty` = `2026-10-17 / 售出 5 份`, `#traceTotalCost` = `HK$25.00`, table `5 份`, details two FIFO deduct lines (1.5kg + 1kg); screenshot `ui/modal-after-second.png` (`logs/ui-evidence.json`).
5. Stop uvicorn; **15132** and **15010** free.

Evidence: `docs/handoff/artifacts/uat032-dev/logs/`, `docs/handoff/artifacts/uat032-dev/ui/modal-after-second.png`.
Scripts: `docs/handoff/artifacts/uat032-dev/scripts/{run_isolate.sh,selfcheck_uat032.py,ui_fifo_modal.py}`.

## Cleanup

- Isolate uvicorn stopped; ports 15132 and 15010 verified free.
- No git commit / no push.
