# UAT019 Development Report

> Generated: 2026-10-17 07:12 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT019] 极大售出数量无确认直接扣成巨额负库存 — P2 QUALITY_RISK MANUAL_UI
> Git: no commit / no push (per dispatch)
> Prerequisite: UAT018 DONE PASS

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Reproduce silent huge negative | PASS (before: stock -499999999998.5 on ordinary 200) |
| Ordinary click → 409, no write | PASS |
| 409 → explicit force+reason only | PASS |
| Normal 1 portion deducts 0.5 | PASS |
| Illegal date / non-number no write | PASS |
| Duplicate request_id no double-deduct | PASS |
| No new business sale-qty cap | PASS (extreme finite still allowed with force) |
| Reverse / already-consumed 409 untouched | PASS (inventory.reverse ReversalBlocked unchanged) |
| Tenant B unchanged | PASS |
| Specialty pytest (6) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

`POST /api/dishes/daily_consumption/batch` expanded BOM and deducted SKU `current_stock` with no insufficient-stock gate. SKU-level consume/waste already had 409 `INSUFFICIENT_STOCK` → `allow_negative` + `override_reason`. Dish batch did not, so ordinary UI submit of sale `999999999999` (stock 1kg, BOM 0.5kg/portion) silently wrote stock `-499999999998.5`.

## Fix

### `demo/app/services/inventory.py`

Shared overdraw helpers (no change to `reverse_receipt_inventory` / ReversalBlocked):

- `fmt_stock_qty`
- `collect_sku_shortfalls(session, needed_by_sku, tenant_id)`
- `can_force_negative_stock(allow_negative, override_reason)`
- `insufficient_stock_response(...)` → 409 body matching SKU consume contract (`can_override`, `reason_required`, `shortfalls`)

### `demo/app/api_dishes.py`

- `DailyConsumptionBatchCreate`: `allow_negative: bool = False`, `override_reason: str = ""`
- Before FIFO write: expand all merged items, aggregate base-unit need per SKU, compare to stock
- Shortfall without force → 409 `INSUFFICIENT_STOCK` (no write, request_id not saved)
- Force = both `allow_negative` and non-empty reason → proceed; reason appended to stock-log note + system audit `stock_overdraw_confirmed`
- No new sale-quantity business cap (upper-limit adjudication remains open)

### `demo/static/js/dish_consumption.js`

- `askDishConsumeOverdrawReason` via `uiPrompt` (same UX as inventory board)
- `submitDailyConsumptionBatch` async: on 409 `INSUFFICIENT_STOCK`, prompt; cancel → no write; confirm → retry with `allow_negative` + `override_reason` (same `request_id`)

### `demo/templates/index.html`

- Cache bust: `dish_consumption.js?v=20261017_uat019`

### Tests

- `demo/tests/test_uat_019_huge_sale_overdraw.py` (6 cases)

## Repro / self-check (isolated port; 15010 kept free)

1. Venv: `docs/handoff/artifacts/uat025-qa/environment/venv`
2. Temp DB + `AUTH_ENABLED=0` uvicorn on **15245** (avoided 15010 / 15215 / 15225 / 15235 and earlier UAT ports)
3. Before fix: sale `999999999999` → HTTP 200, stock `-499999999998.5` — `logs/repro_before.json`
4. After fix: API self-check **22/22 PASS** — `logs/selfcheck_after.json`
5. Specialty pytest: **6 passed** — `logs/pytest_uat019.log`
6. Regression (clean env): `test_b2_dishes` + specialty + SKU overdraw tests **17 passed** — `logs/pytest_regression2.log`
7. Tenant B stock baseline unchanged under default-tenant huge reject
8. Cleanup: uvicorn stopped; port 15245 freed; 15010 remained free

Note: `test_daily_consumption_idempotent_merge::test_daily_consumption_second_submit_merges` still fails on `cons["total_cost"] is None` for **staff** GET (price redaction). Stock merge asserts (95→93) pass; failure is pre-existing staff cost visibility, not UAT019 overdraw gate.

## Acceptance mapping (dev evidence only)

| AC | Dev evidence | Status |
|---|---|---|
| 1. stock1kg BOM0.5 sale999999999999 ordinary click no silent huge negative | repro_before.json + after 409 + stock stays 1.0 | PASS |
| 2. Existing 409 → explicit force+reason only | force without reason 409; with reason 200 → stock -1 | PASS |
| 3. Normal 1 portion deducts 0.5 | selfcheck + pytest | PASS |
| 4. Illegal date / non-number no write | 400 / 422; stock untouched | PASS |
| 5. Duplicate request_id no double-deduct | replayed + stock once | PASS |
| Negative: no new cap; reverse 409 not loosened; residual extreme risk open; Tenant B | no qty clamp; ReversalBlocked untouched; force still allows huge; tenant_b 7.0 | PASS |

MANUAL_UI browser click-through of the prompt card is reserved for independent QA/UAT.

## Open risk (not closed)

Extreme **finite** sale quantity upper limit is **not adjudicated**. Product still allows huge finite qty when force+reason is provided. Do not invent a business cap. Residual risk remains until PM decides.

## Files modified (UAT019 scope)

| Path | Change |
|---|---|
| `demo/app/services/inventory.py` | shared overdraw helpers |
| `demo/app/api_dishes.py` | batch pre-check 409 + force fields + audit/note |
| `demo/static/js/dish_consumption.js` | 409 → prompt → force retry |
| `demo/templates/index.html` | JS cache bust uat019 |
| `demo/tests/test_uat_019_huge_sale_overdraw.py` | specialty pytest (6) |
| `docs/handoff/01-acceptance-checklist.md` | UAT019 → `[TODO] DEV_DELIVERED` only |
| `docs/handoff/02-coordinator-ledger.md` | DEV note; next atomic still UAT019 |
| `docs/handoff/artifacts/uat019-dev-report.md` | this report |
| `docs/handoff/artifacts/uat019-dev/` | logs + environment DBs |

No git commit. No git push. No independent QA/UAT dispatched. Ledger not advanced past UAT019.

## Scheduler handoff

- Checklist: **[TODO] DEV_DELIVERED** (not DONE).
- Please dispatch **independent QA** next (fresh isolation port 15xxx ≠ 15245/15215/15225/15235/15010; real browser dish-consume submit + 409 prompt + force path + API ACs).
- After QA PASS, dispatch independent UAT.
- Only then flip `[UAT019]` to `[DONE] PASS`.
