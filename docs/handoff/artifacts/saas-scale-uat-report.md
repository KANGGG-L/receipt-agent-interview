# SAAS-SCALE Independent UAT Report

> Generated: 2026-10-17 06:33 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — client pagination pageSize 50 for archive + workbench queue; totals+range; last item reachable; no lost ids; cross-page edit state; empty-db vs no-filter-results; budgets OK; UX chrome distinct for archive vs queue
> Card: [SAAS-SCALE] 归档670/队列635行无分页规模任务效率 — P2 DESIGN_GAP
> Prior: `saas-scale-dev-report.md` (port 15450), `saas-scale-qa-report.md` (port 15130; residual: identical PNG MD5 — eyeball queue chrome separately)
> Checklist / ledger: flipped on PASS (per dispatch). No git push.

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. pageSize 50 + totals/range + sequential access (670 / 635) | **PASS** (archive `共 670 条记录` / `显示 1–50` / `1 / 14`; queue `共 635 条` / `显示 1–50` / `1 / 13`; keyword `SCALE_HIT_SUPPLIER` filter exercised in measure) |
| 2. 末项可达，不丢记录 | **PASS** (archive last id `900001`; queue last `900635`; unique id set size = 670; `noLostRecords=true`) |
| 3. 修改记录跨页后状态正确 | **PASS** (archive `#900006` →`edited`, page2→page1 present+hint; queue `#900004` same) |
| 4. empty-db vs no-filter-results | **PASS** (`data-empty-state` distinct copy on both surfaces) |
| 5. DOM行数 + 交互耗时 vs budgets | **PASS** (9/9 budgets; DOM capped at 50/page) |
| 6. UX chrome distinct archive vs queue (QA residual) | **PASS** (real sidebar nav to `tab-archive` / `tab-scan`; page MD5s differ; pager `data-saas-scale` archive-pager vs queue-pager; badge copy `共 N 条记录` vs `共 N 条`; eyeball shots below) |

**SAAS-SCALE UAT gate: PASS.**

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15140** (≠15130 QA, ≠15450 DEV; avoided 15020–15050 / 154xx / 15120 / 15150–15800; **15010** reserved untouched) |
| DB | `docs/handoff/artifacts/saas-scale-uat/environment/uat_isolated.db` (fresh copy of demo DB) |
| RAG | `docs/handoff/artifacts/saas-scale-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo role switcher) |
| Venv | symlink → saas-mobile-uat venv (deps only) |
| Server pid | 73643 (stopped at end) |
| Browser | Chrome headless via puppeteer-core |

## Commands actually run

1. Isolated uvicorn on **15140** — `GET /` → HTTP 200
2. Functional Puppeteer measure — `scripts/uat-measure-scale.js` → budgets 9/9 PASS; acceptance functional keys PASS (initial run failed only `uxChromeDistinct` because screenshots landed on 今日待办 before real sidebar nav — same root cause as QA residual)
3. Chrome eyeball — `scripts/uat-chrome-eyeball.js` → **PASS** (click `.sidebar-btn[data-target=tab-archive|tab-scan]`, re-seed synthetic 670/635 after tab refetch, element screenshots of `#archivePagination` / `#queuePagination`)
4. Merged `logs/measure-result.json` + `logs/chrome-eyeball.json` → `uatGate=PASS` (`logs/uat-summary.json`, `uat-result.json`)
5. Cleanup: stop pid 73643; free port 15140; `environment/cleanup.json`

Did **not** run: git commit / push.

## Criterion 1 — pageSize 50 + totals/range (PASS)

Live against `http://127.0.0.1:15140`. Client-injected synthetic 670/635.

| Surface | Badge | Range | Page label | DOM rows |
|---|---|---|---|---:|
| Archive | `共 670 条记录` | `显示 1–50` | `1 / 14` | 50 |
| Queue (todo) | `共 635 条` | `显示 1–50` | `1 / 13` | 50 |

## Criterion 2 — last item / no lost ids (PASS)

| Surface | Last page rows | Last item id | Expected |
|---|---:|---|---|
| Archive (`id_desc`) | 20 | **900001** | 900001 |
| Queue (todo) | 35 | **900635** | 900635 |

## Criterion 3 — cross-page edit (PASS)

| Surface | editId | after page2→page1 |
|---|---|---|
| Archive | 900006 | rowPresent + edited hint |
| Queue | 900004 | rowPresent + edited hint |

## Criterion 4 — empty states (PASS)

| Surface | no-filter-results | empty-db |
|---|---|---|
| Archive | 未找到符合筛选条件的归档记录 | 暂无归档单据（空库） |
| Queue | 当前队列暂无待处理单据 | 暂无单据（空库） |

## Criterion 5 — budgets (PASS)

| Budget | Max | UAT measured | Pass |
|---|---:|---:|:---:|
| archive DOM data rows / page | 50 | 50 | Y |
| archive render page 1 | 200 ms | 80 ms | Y |
| archive page next | 100 ms | 9 ms | Y |
| archive goto last | 150 ms | 4 ms | Y |
| archive keyword filter render | 250 ms | 6 ms | Y |
| queue DOM data rows / page | 50 | 50 | Y |
| queue render page 1 | 200 ms | 4 ms | Y |
| queue page next | 100 ms | 3 ms | Y |
| queue goto last | 150 ms | 2 ms | Y |

Raw: `docs/handoff/artifacts/saas-scale-uat/logs/measure-result.json`.

## Criterion 6 — UX chrome distinct (PASS; closes QA residual)

QA residual: archive/queue PNGs shared MD5 because shots were taken while 今日待办 was visible. UAT fixed navigation and re-verified by eye.

| Check | Evidence |
|---|---|
| Archive sidebar | `tab-archive` / 归档对账 active; breadcrumb 归档对账 |
| Queue sidebar | `tab-scan` / 识别复核 active; 当天收据处理队列 + todo filter tabs |
| Archive pager | `data-saas-scale=archive-pager`; range `显示 1–50`; label `1 / 14`; badge on title `共 670 条记录` |
| Queue pager | `data-saas-scale=queue-pager`; badge `共 635 条` + range `显示 1–50`; label `1 / 13` |
| Page MD5 differ | archive-page1 `d800b4ef…` ≠ queue-page1 `03e015ae…` |
| Pager MD5 differ | archive-pagination-chrome `d4f140cf…` ≠ queue-pagination-chrome `14fc9c57…` |

Shots: `ui/archive-page1.png`, `ui/queue-page1.png`, `ui/archive-pagination-chrome.png`, `ui/queue-pagination-chrome.png`.

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15140**; **15010** untouched |
| Baselines | not deleted |
| Scale data | client-injected only (not written into live demo DB) |
| uvicorn | pid 73643 stopped; port released |
| Git | no commit / no push |
| Checklist / ledger | flipped to DONE PASS on this UAT (per dispatch) |

## Residual notes (honest)

1. Pagination remains client-side over full `/api/receipts` payload (same as DEV/QA). Server limit/offset unused by design so filter/search stay globally consistent.
2. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
3. Tab switch refetches live API and can overwrite synthetic seed — UAT chrome script re-injects after nav before measuring visible chrome.

## Scheduler handoff

- SAAS-SCALE **UAT PASS** (functional + budgets + distinct chrome eyeball).
- Independent DEV + QA + UAT all PASS. Checklist flipped to `[DONE] PASS`.
- **Next atomic task (W4):** **UAT-033** (legacy缺汇率单据被排除但部门报表未显示API缺汇率说明，P2；无前置依赖). Skip **UAT-032** until **UAT-031** closes (UAT-032 depends on UAT-031).
