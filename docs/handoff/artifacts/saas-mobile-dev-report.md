# SAAS-MOBILE Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [SAAS-MOBILE] 移动KPI/筛选堆叠与首个业务表过晚 — P2 DESIGN_GAP
> Git: no commit / no push (per dispatch)
> Deps: UAT-024 + UAT-036 satisfied (already PASS)

## Summary

On 390/412 viewports, KPI metric cards no longer stack full-width (three inventory / todo cards share one compact row; dish KPI uses 2x2). Inventory search is full-width and fully visible; secondary filters sit behind a mobile **筛选** toggle with **清除** wired to `resetInventoryFilters`. Archive keyword search is full-width; chip rows stay horizontal wrap; `#archiveFilterGrid` collapses into the existing **高级筛选** expand on mobile so the first business table moves up. All KPI cards remain visible (none hidden for fake density). Fixed bottom tab bar clearance preserved (`main-wrapper` padding-bottom); last toolbar/table content is not blocked after scroll.

**SAAS-MOBILE DEV self-check: PASS.** Card remains `DEV_DELIVERED` until independent QA/UAT.

## Acceptance mapping (dev self-check)

| AC | Dev evidence |
|---|---|
| 1. 390/412 搜索完整可见；筛选展开/清除有效 | Inv search w=316 (390) / 338 (412), `fullyInView=true`; toggle opens `#invMobileFiltersPanel`; clear resets search/cat/filt; archive advanced expands `#archiveFilterGrid` + `#archiveAdvanced`; 重置条件 works |
| 2. vs 原 y891 / y1408 业务起点前移 | Inventory `#inventoryTable` **891 → 468** (collapsed) / **576** (filters open). Archive `#archiveTable` **1438 → 748** (advanced collapsed; prior card cited y1408). Expanded advanced returns filters (table Y rises as expected) |
| 3. 固定底导航/保存不挡末字段 | `bottom.blocked=false` after scroll; `main-wrapper` padding-bottom 72px + safe-area; review save bar budget unchanged |
| 4. 不得隐藏全部指标 | `kpiCount=3`, `kpiHidden=0`; todoBar also 3 visible compact cards |

## Before / after Y positions (same fixture, port 15430)

| Viewport | Surface | Before absY | After absY | Delta |
|---|---|---:|---:|---:|
| 390 | `#inventoryTable` (filters collapsed) | **891** | **468** | −423 |
| 390 | `#inventoryTable` (filters expanded) | 891 | 576 | −315 |
| 390 | `#archiveTable` (advanced collapsed) | **1438** (~card y1408) | **748** | −690 |
| 412 | `#inventoryTable` (collapsed) | **891** | **468** | −423 |
| 412 | `#archiveTable` (collapsed) | **1438** | **748** | −690 |

Supporting metrics (390): KPI grid height 399→67; inv search width 240→316; archive `#fltKeyword` width 48→316; todoBar height 399→67.

Raw JSON: `docs/handoff/artifacts/saas-mobile-dev/logs/measure-before.json`, `measure-after.json`.

## After (files touched)

### `demo/static/css/review-mobile.css`

- SAAS-MOBILE media block (`max-width: 640px`): compact 3-col metrics for inventory + todoBar; 2x2 dish KPI; full-width inv search; mobile filter bar + collapsed secondary panel via `:has(checkbox:checked)`; archive keyword full-width with row-wrap chips; `#archiveFilterGrid` hidden until `#archiveAdvanced` open; bottom padding reinforced.
- Desktop (`min-width: 641px`): hide `.inv-mobile-filter-bar`; `.inv-filters-secondary { display: contents }` so selects stay in toolbar flex.

### `demo/templates/index.html`

- Inventory toolbar: `inv-search-wrap`, mobile filter checkbox/label **筛选**, **清除** → `resetInventoryFilters`, wrap category/status/inactive in `#invMobileFiltersPanel`.
- Cache-bust CSS query `20261017_saas_mobile`.

## Visual evidence

| Shot | Path |
|---|---|
| Before inv 390 | `docs/handoff/artifacts/saas-mobile-dev/ui/before-inv-390.png` |
| After inv 390 | `docs/handoff/artifacts/saas-mobile-dev/ui/after-inv-390.png` |
| After arc 390 | `docs/handoff/artifacts/saas-mobile-dev/ui/after-arc-390.png` |
| After inv 412 | `docs/handoff/artifacts/saas-mobile-dev/ui/after-inv-412.png` |
| Desktop 1440 inv (filters still visible) | `docs/handoff/artifacts/saas-mobile-dev/ui/after-desk-1440-inv.png` |

## Negative / isolation

| Field | Value |
|---|---|
| Port | **15430**; **15010** reserved untouched |
| AUTH_ENABLED | 0 |
| DB | `docs/handoff/artifacts/saas-mobile-dev/environment/dev_isolated.db` |
| Baselines | not deleted |
| Git | no commit / no push |
| QA / UAT | **not** run |

## Residual notes

1. Archive table Y when **高级筛选** is open (~1440) is expected — secondary dropdown grid is intentionally revealed with advanced; default collapsed path is the “business start earlier” path.
2. KPI label text on 3-col mobile is smaller (0.72rem); values remain readable; independent UAT should confirm wet-finger hit targets on metric cards.
3. Desktop inventory search remains `max-width: 240px` via legacy `csp-s-77` (unchanged desktop contract).

## Scheduler handoff

- SAAS-MOBILE **DEV_DELIVERED** (self-check PASS).
- Next: independent QA, then independent UAT. Do not mark card `[DONE] PASS` until both pass.
