# UAT-036 Independent UAT Report

> Generated: 2026-10-17 05:12 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation — KPI fail → em-dash + error + retry; recover; empty zeros; stale race
> Card: [UAT-036] 库存和餐品请求失败仍显示0指标 — P2 PRODUCT_FAULT
> Prior: `uat036-dev-report.md`, `uat036-qa-report.md` (QA port 15450)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. GET 500 → — + error + retry (inventory / dishes / scan) | **PASS** |
| 2. Cancel inject → success restores numbers | **PASS** |
| 3. Empty tenant / 200 `data:[]` → legitimate 0 / empty | **PASS** |
| 4. Delayed/stale responses do not overwrite newer | **PASS** |
| Negative: legitimate 0 not turned into —; archive — not clobbered (static) | **PASS** |
| Live browser MANUAL_UI fault/empty/stale | **PASS** (40/0) |

**UAT-036 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15500** (QA used 15450; forbidden reuse of 15150–15450) |
| DB | `docs/handoff/artifacts/uat036-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat036-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner) |
| Venv | `environment/venv` → symlink to uat025-qa venv (deps only; fresh DB/port) |
| Seed | `scripts/seed_uat036.py` → UAT036_Flour / UAT036_Sugar (+ empty tenant id `empty_uat036`) |
| Server pid | 18386 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; request interception for 500 / empty200 / staleHold |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15500` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Seed + live API — default inventory n=2; `X-Tenant-Id: empty_uat036` n=0 (`logs/seed.json`, `logs/live-served-verify.txt`)
3. Static + live served JS citation — `logs/static-evidence.txt`, `logs/live-served-verify.txt` (LoadSeq / mark*Failed / data-uat036 retry markers)
4. Browser MANUAL_UI `scripts/browser_uat036.js` (puppeteer-core + system Chrome): fault500 / recover / empty200 / staleHold races on inventory, dishes (list+KPI), workbench — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
5. Full cleanup: stop pid 18386, free port 15500, `environment/cleanup.json`

Did **not** run: QA harness / pytest specialty (QA-stage); checklist edit; git write.

Browser check tally: **40 PASS / 0 FAIL**.

## Criterion 1 — GET 500 → — + error + retry (PASS)

Fault inject via CDP `setRequestInterception` → HTTP 500 JSON for `/api/inventory`, `/api/dishes`, `/api/dishes/daily_consumption`, `/api/receipts`.

| Surface | Metrics after 500 | Error UI | Retry |
|---|---|---|---|
| Inventory | `valTotalSKU/LowStock/PriceAnomaly` = `—` (not `0`) | `#invLoadErrorNotice` visible: 库存指标加载失败… | `#inventoryKpiRetry` (`data-uat036=inventory-retry`) |
| Dish KPI | `kpiDishCost/Revenue/GrossMargin/Count` = `—` | `#dishKpiLoadError` + `dish-kpi-retry` | visible |
| Dish BOM list | fail panel (not empty-library copy) | 「餐品列表加载失败 / 当前不是空库」 | `data-uat036=dish-retry` visible on BOM subtab |
| Workbench (scan) | `qCount*` = `—` | body: 工作台队列加载失败…（不是空队列） | `#workbenchKpiRetry` |

Screenshots: `ui/fail-inventory.png`, `ui/fail-dishes-kpi.png`, `ui/fail-dishes.png`, `ui/fail-workbench.png`.

Injected English `injected_500_uat036` not treated as language bug (per card).

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

Screenshots: `ui/stale-inventory.png`, `ui/stale-workbench.png`.

## Negative / archive (PASS)

- Legitimate empty stays `0`, not converted to `—` (NEG check + C3).
- Archive em-dash not clobbered by supplier success: static citation of `supplier_archive.js` `updateTodoBar` guard on `#archiveKpiRetry` / `todoPendingNum === '—'` (`logs/static-evidence.txt` + live served markers).
- Injected English `injected_500_uat036` not treated as language bug (per card).

## Residual notes

1. Browser automation used headless Chromium via puppeteer-core (not a human GUI tab). Flows exercised real served HTML/JS + CDP request interception for fault/empty/stale.
2. This UAT deliberately used a fresh port/DB distinct from QA 15450 (and outside 15150–15450).
3. Model / LLM creds were not required for this card.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 18386) |
| Port 15500 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15500 |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat036-uat/environment/cleanup.json` |
| Checklist / ledger | **not** flipped (UAT report only; scheduler owns card close) |
| Git | no commit / no push |

## Scheduler handoff

- UAT-036 **UAT PASS** (40/40 live checks + criteria).
- Card may be closed DONE PASS by scheduler after reading this report + prior QA PASS.
