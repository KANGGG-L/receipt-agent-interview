# SAAS-MOBILE Independent UAT Report

> Generated: 2026-10-17 06:24 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — 390/412: search full; filter expand/clear; business table Y earlier vs baseline y891/y1408; bottom nav not blocking; KPIs not all hidden (+ wet-finger hit sample)
> Card: [SAAS-MOBILE] 移动KPI/筛选堆叠与首个业务表过晚 — P2 DESIGN_GAP
> Prior: `saas-mobile-dev-report.md`, `saas-mobile-qa-report.md` (QA port 15120)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 390/412 搜索完整可见；筛选展开/清除有效 | **PASS** (inv search w=316/338 `fullyInView`; archive `#fltKeyword` w=316/338 `fullyInView`; `#invMobileFilterToggleCb` opens panel; **清除** → `resetInventoryFilters` clears search/cat/filt; archive **高级筛选** expands `#archiveFilterGrid`) |
| 2. vs 原 y891 / y1408 业务起点前移 | **PASS** (`#inventoryTable` **468** < 891; `#archiveTable` collapsed **748** < 1408; expanded advanced archive Y=1440 expected) |
| 3. 固定底导航/保存不挡末字段 | **PASS** (`bottom.blocked=false`; `main-wrapper` padding-bottom **72px**; tab bar visible; scrolled-end shots) |
| 4. 不得隐藏全部指标 | **PASS** (inventory `kpiCount=3` `kpiHidden=0`; todoBar `todoCount=3` `todoHidden=0`; compact 3-col grid h=67; wet-finger sample ≥44×36: 117×67 / 124×67) |

**SAAS-MOBILE UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15130** (avoided **15120** / **15430** / **15020–15050** / **154xx** / **15150–15800**; **15010** reserved untouched) |
| DB | `docs/handoff/artifacts/saas-mobile-uat/environment/uat_isolated.db` (fresh copy of demo DB; not QA DB) |
| RAG | `docs/handoff/artifacts/saas-mobile-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo role switcher) |
| Venv | `environment/venv` → symlink to saas-consistency-uat venv (deps only; fresh DB/port) |
| Server pid | 68437 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; MANUAL_UI measurements + screenshots |

## Commands actually run

1. Isolated uvicorn on **15130**
   - `DB_PATH=…/saas-mobile-uat/environment/uat_isolated.db`
   - `AUTH_ENABLED=0`, host `127.0.0.1`
   - module `demo.app.main:app`
   - pid 68437 → stopped (`environment/cleanup.json`)
   - `GET /` → HTTP 200
2. Live Puppeteer MANUAL_UI — `scripts/capture.js` → **overall PASS** (`logs/capture.txt`, `logs/uat-capture-result.json`, `logs/uat-summary.json`, `uat-result.json`)
3. Screenshots under `docs/handoff/artifacts/saas-mobile-uat/ui/` (10 shots)
4. Full cleanup: stop pid 68437, free port 15130, `environment/cleanup.json`

Did **not** run: checklist/ledger flip; git write; development changes.

## Criterion 1 — search full + filter expand/clear (PASS)

Live against `http://127.0.0.1:15130`.

| Viewport | Inv `#invSearchInput` | Arc `#fltKeyword` | Inv filters expand | Inv clear | Arc advanced |
|---|---|---|---|---|---|
| 390 | w=**316**, fullyInView | w=**316**, fullyInView | open → panel `flex`; tableY=**576** | search→`""`, cat→`""`, filt→`all` | collapsed: `#archiveFilterGrid` `display:none`; expand → `block`, tableY=1440 |
| 412 | w=**338**, fullyInView | w=**338**, fullyInView | same pattern; tableY=**576** | same clear | same pattern |

Shots: `ui/uat-inv-*-collapsed.png`, `ui/uat-inv-*-filters-open.png`, `ui/uat-arc-*-collapsed.png`, `ui/uat-arc-*-advanced-open.png`.

## Criterion 2 — business table Y earlier vs baseline (PASS)

Baselines from card / DEV/QA: inventory **y891**, archive **y1408**.

| Viewport | Surface | Baseline | UAT absY (collapsed) | Delta |
|---|---|---:|---:|---:|
| 390 | `#inventoryTable` | 891 | **468** | −423 |
| 412 | `#inventoryTable` | 891 | **468** | −423 |
| 390 | `#archiveTable` | 1408 | **748** | −660 |
| 412 | `#archiveTable` | 1408 | **748** | −660 |

Matches DEV/QA after-metrics (inv 468 / arc 748). Expanded-advanced archive Y=1440 is expected residual (filters intentionally revealed).

## Criterion 3 — bottom nav not blocking (PASS)

| Viewport | `blocked` | `padBottom` | `tabVisible` | clearance |
|---|---|---|---|---:|
| 390 | **false** | 72px | true | 678 |
| 412 | **false** | 72px | true | 678 |

Shots: `ui/uat-inv-*-scrolled-end.png`.

## Criterion 4 — KPIs not all hidden (PASS)

| Viewport | Inv KPI count / hidden | KPI card size (sample) | TodoBar count / hidden / h |
|---|---|---|---|
| 390 | **3 / 0** | 117×67 (wet-finger ok) | **3 / 0** / 67 |
| 412 | **3 / 0** | 124×67 (wet-finger ok) | **3 / 0** / 67 |

No metric cards forced `display:none` for fake density. Wet-finger sample (DEV residual): all three inventory KPI cards ≥44×36.

## Gate matrix (16/16)

All gates in `logs/uat-summary.json` **ok=true** (AC1×3 + AC2×2 + AC3 + AC4 + AC4b hit-target, ×2 viewports).

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15130**; avoided **15120** / **15430** / **15020–15050** / **154xx** / **15150–15800**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/saas-mobile-uat/environment/uat_isolated.db` (fresh vs QA) |
| AUTH_ENABLED | 0 (demo role switcher path) |
| uvicorn | pid 68437 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Artifacts | `docs/handoff/artifacts/saas-mobile-uat/` + this report |
| Checklist / ledger | **not** flipped (UAT report only) |
| Git | no commit / no push |
| cleanup.json | `docs/handoff/artifacts/saas-mobile-uat/environment/cleanup.json` |

## Residual notes (honest)

1. Archive table Y when **高级筛选** is open (1440) returns near pre-fix height — expected; default collapsed path is the “business start earlier” gate.
2. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
3. Wet-finger hit targets measured on inventory KPI cards only (todoBar cards share same compact grid CSS).
4. Checklist still awaiting scheduler flip to `[DONE] PASS` after this UAT.

## Scheduler handoff

- SAAS-MOBILE **UAT PASS** (16/16 live MANUAL_UI checks on 390/412).
- Independent DEV + QA + UAT all PASS. Scheduler may flip `[SAAS-MOBILE]` to DONE PASS.
