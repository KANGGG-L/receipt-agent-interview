# UAT-036 Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-036] 库存和餐品请求失败仍显示0指标 — P2 PRODUCT_FAULT
> Deps: UAT-002 DONE PASS (satisfied)
> Vendors in fixtures / prose: none (KPI fail UX only)

## Summary

On GET failure (HTTP 500 / network / non-success JSON), inventory KPI cards,
dish library / daily dish KPIs, and scan workbench counters previously kept
HTML-default **0** (or empty-queue copy), which operators read as "legitimately
empty". Archive todo KPIs already used em-dash via `markArchiveKpiFailed`
(`archive_list.js`); supplier success callbacks could still clobber that dash
with fake zeros.

Fix (dev only): on fail show **— + visible error + clickable retry**; on
success (including empty tenant `200` + `data: []`) restore legitimate **0** /
empty states; each loader uses a generation token so delayed success/error
cannot overwrite a newer request. No independent QA/UAT in this stage.

## Before

- `inventory_board.js` `degradeInventoryLoad`: toast + stale rows; metric IDs
  `valTotalSKU` / `valLowStock` / `valPriceAnomaly` stayed at HTML `0` on first
  fail; no `res.ok` gate; no request generation.
- `dish_bom.js` `loadDishesList`: toast only; grid could look like empty library.
- `dish_consumption.js` `loadDailyConsumption`: toast only; KPI stayed
  `$0.00` / `0.0%` / `0 笔` (had `dailyConsumptionReqSeq` already).
- `workbench_queue.js` `loadWorkbenchQueue`: catch logged only; `qCount*` stayed
  `0`; empty-queue table copy on later render.
- `supplier_archive.js` `updateTodoBar`: always wrote counts from
  `allArchiveReceipts || []`, wiping archive `—` after supplier load success.

## After

### `demo/static/js/inventory_board.js`

- `inventoryLoadSeq` + `mySeq` guards on then/catch
- `res.ok` required before `json()`
- `markInventoryMetricsFailed` / `clearInventoryMetricsFailed`: metrics → `—`,
  alert + `#inventoryKpiRetry` (`data-uat036=inventory-retry`)
- `degradeInventoryLoad` calls `markInventoryMetricsFailed` (keeps stale-row UX)
- success path clears fail UI then writes meta counts (including legitimate 0)

### `demo/static/js/dish_bom.js`

- `dishesLoadSeq` + `res.ok` + fail panel distinct from empty copy
  (“餐品列表加载失败 / 当前不是空库”) + retry `onclick=loadDishesList()`

### `demo/static/js/dish_consumption.js` (AC dish KPI render point)

- Card source list named `dish_bom.js`; daily KPI DOM is updated here.
- `markDishKpiFailed` / `clearDishKpiFailed`: KPI → `—` + retry
- both parallel GETs check `res.ok`; existing `dailyConsumptionReqSeq` kept
- success with empty summary still `renderDailyKPI({})` → legitimate zeros

### `demo/static/js/workbench_queue.js`

- `workbenchQueueLoadSeq` + `workbenchQueueKpiFailed`
- fail: `qCount*` → `—`, error row, `#workbenchKpiRetry`
- `updateWorkbenchQueueCounters` / `renderWorkbenchQueueTable` no-op while failed

### `demo/static/js/supplier_archive.js`

- `updateTodoBar` short-circuits when `#archiveKpiRetry` visible and
  `todoPendingNum === '—'`
- `loadSupplierAdmin` checks `res.ok` + catch warn

### `demo/static/js/archive_list.js` (sibling scan KPI)

- `archiveLoadSeq` on `loadReceiptsHistory` (existing `markArchiveKpiFailed`
  unchanged: already `—` + 重试)

## Tests run (dev self-check; not independent QA)

| Command | Result |
|---|---|
| `node --check` on inventory_board / dish_bom / dish_consumption / workbench_queue / supplier_archive / archive_list | exit 0 |
| `node demo/tests/frontend_uat036_kpi_fail_harness.js` | **25 PASS / 0 FAIL** |
| inline runner `demo/tests/test_uat_036_kpi_fail_static.py` | **3/3 PASS** (pytest module not installed on box) |

Did **not** run: live fault-inject MANUAL_UI, independent QA/UAT, git commit/push.

## Acceptance mapping (dev claim only)

| Criterion | Dev disposition |
|---|---|
| GET 500 → — + error + retry (inventory / dishes / scan) | Implemented in loaders above |
| Cancel inject → success restores numbers | clear*Failed + success writers |
| Empty tenant 200/`[]` → legitimate 0 / empty | success paths unchanged for empty data |
| Delayed request must not overwrite newer | `*LoadSeq` / `dailyConsumptionReqSeq` / `archiveLoadSeq` |
| Negative: do not turn all legitimate 0 into — | fail-only; success still assigns 0 |
| Archive already — must not regress | supplier `updateTodoBar` guard |

## Next gate

Independent QA on isolated port: reuse fault + real-empty scripts; assert DOM
em-dash + retry click recovers; empty tenant zeros; race with delayed responses.
Do not flip card to DONE PASS until QA + UAT pass.
