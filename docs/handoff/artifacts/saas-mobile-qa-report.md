# SAAS-MOBILE Independent QA Report

> Generated: 2026-10-17 06:23 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify SAAS-MOBILE acceptance only (390/412: search full; filter expand/clear; business table Y earlier vs baseline; bottom nav not blocking; KPIs not all hidden)
> Card: [SAAS-MOBILE] 移动KPI/筛选堆叠与首个业务表过晚 — P2 DESIGN_GAP
> Prior: `saas-mobile-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 390/412 搜索完整可见；筛选展开/清除有效 | **PASS** (inv search w=316/338 `fullyInView`; archive `#fltKeyword` w=316/338 `fullyInView`; `#invMobileFilterToggleCb` opens panel; **清除** → `resetInventoryFilters` clears search/cat/filt; archive **高级筛选** expands `#archiveFilterGrid`) |
| 2. vs 原 y891 / y1408 业务起点前移 | **PASS** (`#inventoryTable` **468** < 891; `#archiveTable` collapsed **748** < 1408; expanded advanced archive Y=1440 expected) |
| 3. 固定底导航/保存不挡末字段 | **PASS** (`bottom.blocked=false`; `main-wrapper` padding-bottom **72px**; tab bar visible; scrolled-end shots) |
| 4. 不得隐藏全部指标 | **PASS** (inventory `kpiCount=3` `kpiHidden=0`; todoBar `todoCount=3` `todoHidden=0`; compact 3-col grid h=67) |

**SAAS-MOBILE QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Isolated uvicorn on **15120** (avoided **15020–15050**, **154xx**, **15150–15800**; **15010** untouched)
   - `DB_PATH=…/saas-mobile-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`
   - listen pid **66367** → stopped (`environment/cleanup.json`)
2. Live Puppeteer capture — `scripts/capture.js` → **overall PASS** (`logs/capture-run2.log`, `logs/qa-summary.json`, `logs/qa-capture-result.json`)
3. Screenshots under `docs/handoff/artifacts/saas-mobile-qa/ui/`

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — search full + filter expand/clear (PASS)

Live against `http://127.0.0.1:15120`.

| Viewport | Inv `#invSearchInput` | Arc `#fltKeyword` | Inv filters expand | Inv clear | Arc advanced |
|---|---|---|---|---|---|
| 390 | w=**316**, fullyInView | w=**316**, fullyInView | open → panel display≠none; tableY=**576** | search→`""`, cat→`""`, filt→`all` | collapsed: `#archiveFilterGrid` `display:none`; expand → `block`, tableY=1440 |
| 412 | w=**338**, fullyInView | w=**338**, fullyInView | same pattern; tableY=**576** | same clear | same pattern |

Shots: `ui/qa-inv-*-collapsed.png`, `ui/qa-inv-*-filters-open.png`, `ui/qa-arc-*-collapsed.png`, `ui/qa-arc-*-advanced-open.png`.

## Criterion 2 — business table Y earlier vs baseline (PASS)

Baselines from card / DEV: inventory **y891**, archive **y1408** (DEV measure-before had archive **1438**).

| Viewport | Surface | Baseline | QA absY (collapsed) | Delta |
|---|---|---:|---:|---:|
| 390 | `#inventoryTable` | 891 | **468** | −423 |
| 412 | `#inventoryTable` | 891 | **468** | −423 |
| 390 | `#archiveTable` | 1408 | **748** | −660 |
| 412 | `#archiveTable` | 1408 | **748** | −660 |

Matches DEV after-metrics (inv 468 / arc 748). Expanded-advanced archive Y≈1440 is expected residual (filters intentionally revealed).

## Criterion 3 — bottom nav not blocking (PASS)

| Viewport | `blocked` | `padBottom` | `tabVisible` | note |
|---|---|---|---|---|
| 390 | **false** | 72px | true | scrolled inventory end; clearance positive vs tab top |
| 412 | **false** | 72px | true | same |

Shots: `ui/qa-inv-*-scrolled-end.png`.

## Criterion 4 — KPIs not all hidden (PASS)

| Viewport | Inv KPI count / hidden | KPI grid h / cols | TodoBar count / hidden / h |
|---|---|---|---|
| 390 | **3 / 0** | 67 / 3-col compact | **3 / 0** / 67 |
| 412 | **3 / 0** | 67 / 3-col compact | **3 / 0** / 67 |

No metric cards forced `display:none` for fake density.

## Gate matrix (14/14)

All gates in `logs/qa-summary.json` **ok=true** (AC1×3 + AC2×2 + AC3 + AC4, ×2 viewports).

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15120**; avoided **15020–15050**, **154xx**, **15150–15800**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/saas-mobile-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 (demo role switcher) |
| uvicorn | pid 66367 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Artifacts | `docs/handoff/artifacts/saas-mobile-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/saas-mobile-qa/environment/cleanup.json` |

## Residual risks (honest)

1. Archive table Y when **高级筛选** is open (~1440) returns near pre-fix height — expected; default collapsed path is the “business start earlier” gate.
2. Bottom-block check uses scrolled-end + `main-wrapper` padding-bottom contract; wet-finger hit on metric cards left to UAT.
3. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
4. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- SAAS-MOBILE **QA PASS** (14/14 live checks).
- Please dispatch **independent UAT** next (fresh port ≠ 15120 / ≠ 15430; avoid 15020–15050, 154xx, and 15150–15800; keep 15010 free).
- After UAT PASS: flip `[SAAS-MOBILE]` to DONE PASS.
