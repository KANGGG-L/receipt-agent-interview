# UAT-036 Independent QA Report

> Generated: 2026-10-17 05:10 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-036 acceptance only (KPI fail → em-dash + error + retry; recover; empty zeros; stale race)
> Card: [UAT-036] 库存和餐品请求失败仍显示0指标 — P2 PRODUCT_FAULT
> Prior: `uat036-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. GET 500 → — + error + retry (inventory / dishes / scan) | **PASS** |
| 2. Cancel inject → success restores numbers | **PASS** |
| 3. Empty tenant / 200 `data:[]` → legitimate 0 / empty | **PASS** |
| 4. Delayed/stale responses do not overwrite newer | **PASS** |
| Negative: do not turn legitimate 0 into —; archive — not clobbered (static) | **PASS** |
| Specialty static pytest (3) | **PASS** |
| Frontend harness (25) | **PASS** |
| Live browser MANUAL_UI | **PASS** (40/0) |

**UAT-036 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat036-qa/logs/static-evidence.txt`
2. `node --check` on inventory_board / dish_bom / dish_consumption / workbench_queue / supplier_archive / archive_list — OK (`logs/node-check.txt`)
3. `node demo/tests/frontend_uat036_kpi_fail_harness.js` — **25 PASS / 0 FAIL** (`logs/frontend-harness.txt`)
4. Inline runner of `demo/tests/test_uat_036_kpi_fail_static.py` — **3/3 PASS** (`logs/pytest-uat036.txt`)
5. Isolated uvicorn live MANUAL_UI (self-built)
   - Port **15450** (fresh; avoided 15150–15400)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Seed: `scripts/seed_uat036.py` → UAT036_Flour / UAT036_Sugar (+ empty tenant id `empty_uat036`) (`logs/seed.json`)
   - Browser: `scripts/browser_qa036.js` (puppeteer-core + Chrome; request interception for 500 / empty200 / staleHold)
   - Result: **pass=true, passCount=40, failCount=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`)
6. Live served verify — `logs/live-served-verify.txt` (JS markers + default/empty tenant API)

Did **not** run: independent UAT stage; checklist edit; git write.

## Criterion 1 — GET 500 → — + error + retry (PASS)

Fault inject via CDP `setRequestInterception` → HTTP 500 JSON for `/api/inventory`, `/api/dishes`, `/api/dishes/daily_consumption`, `/api/receipts`.

| Surface | Metrics after 500 | Error UI | Retry |
|---|---|---|---|
| Inventory | `valTotalSKU/LowStock/PriceAnomaly` = `—` (not `0`) | `#invLoadErrorNotice` visible: 库存指标加载失败… | `#inventoryKpiRetry` (`data-uat036=inventory-retry`) |
| Dish KPI | `kpiDishCost/Revenue/GrossMargin/Count` = `—` | `#dishKpiLoadError` + `dish-kpi-retry` | visible |
| Dish BOM list | fail panel (not empty-library copy) | 「餐品列表加载失败 / 当前不是空库」 | `data-uat036=dish-retry` visible on BOM subtab |
| Workbench (scan) | `qCount*` = `—` | body: 工作台队列加载失败…（不是空队列） | `#workbenchKpiRetry` |

Screenshots: `ui/fail-inventory.png`, `ui/fail-dishes-kpi.png`, `ui/fail-dishes.png`, `ui/fail-workbench.png`.

## Criterion 2 — Cancel inject → recover (PASS)

After fail UI shown: switch intercept to passthrough, click visible 重试.

| Surface | After retry |
|---|---|
| Inventory | `valTotalSKU=2`, `valLowStock=1`, notice hidden |
| Dish KPI | `HK$0.00` / `0 笔` (legitimate empty dishes), error hidden |
| Dish list | fail panel gone → 「暂无匹配的餐品数据」 empty copy |
| Workbench | `qCountTodo=0`, retry hidden, empty-queue copy |

Screenshots: `ui/recover-*.png`.

## Criterion 3 — Empty 200/`[]` → legitimate 0 (PASS)

Intercept fulfill `200` + empty payloads (and curl `X-Tenant-Id: empty_uat036` confirms real empty tenant API).

| Surface | Result |
|---|---|
| Inventory | metrics `0/0/0` (not `—`); empty copy 「库存还没有食材」; no error notice |
| Dish KPI | `HK$0.00` / `0.0%` / `0 笔` (not `—`) |
| Dish list | empty library copy, not fail panel |
| Workbench | `qCount*=0` (not `—`) |

Screenshots: `ui/empty-*.png`. Live API: default n=2; empty_uat036 n=0 (`logs/live-served-verify.txt`).

## Criterion 4 — Stale delayed must not overwrite (PASS)

Race: hold request A (`staleHold` with fake meta 999/888/777 or `STALE_*` rows) → fire newer B (passthrough or empty200) → release A.

| Surface | After releasing stale A |
|---|---|
| Inventory | still `total=2` (not 999); table has no `STALE_SHOULD_NOT_SHOW` |
| Dish list | no `STALE_DISH` |
| Workbench | `qCountTodo=0` (newer empty wins; not stale edited count 1) |

Screenshot: `ui/stale-inventory.png`, `ui/stale-workbench.png`.

## Negative / archive (PASS)

- Legitimate empty stays `0`, not converted to `—` (NEG check + C3).
- Archive em-dash not clobbered by supplier success: static harness + pytest assert `updateTodoBar` guards `#archiveKpiRetry` / `todoPendingNum === '—'`.
- Injected English `injected_500_uat036` not treated as language bug (per card).

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15450** (fresh; avoided 15150–15400) |
| DB | `docs/handoff/artifacts/uat036-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 |
| uvicorn | pid 16251 started then stopped; port free; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; contexts closed |
| Artifacts | `docs/handoff/artifacts/uat036-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat036-qa/environment/cleanup.json` |

## Scheduler handoff

- UAT-036 **QA PASS** (40/40 live checks + specialty gates).
- Please dispatch **independent UAT** next (fresh port ≠ 15450; do not reuse 15150–15450).
