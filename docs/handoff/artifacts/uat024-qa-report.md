# UAT-024 Independent QA Report

> Generated: 2026-10-17 04:52 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-024 acceptance only (mobile inventory name column + same-card actions)
> Card: [UAT-024] 手机库存品名列过窄且操作与名称分离 — P2 QUALITY_RISK
> Prior: `uat024-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 390/412：Rice / Oil / long ZH readable | **PASS** |
| 1b. 390/412：真实 More 点击 → 同 row `data-sku-id` / menu `activeId` | **PASS** |
| 2. 操作可见时能同时辨识名称（同卡 + 水平重叠） | **PASS** |
| 2b. 短名不得逐字竖排（Rice / Oil） | **PASS** |
| 3. document 无水平溢出（390/412） | **PASS** |
| 4. 1440 操作不退化（min-width:640 表布局 + More 仍在） | **PASS** |
| Specialty pytest (5) | **PASS** |
| Frontend harness (14) | **PASS** |
| Live browser MANUAL_UI | **PASS** (60/0) |

**UAT-024 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat024-qa/logs/static-evidence.txt`
2. `pytest demo/tests/test_uat_024_mobile_inventory_name_actions.py -v` — **5 passed** (`logs/pytest-uat024.txt`)
3. `node --check demo/static/js/inventory_board.js` — OK (`logs/node-check.txt`)
4. `node demo/tests/frontend_uat024_inventory_mobile_harness.js` — **14 PASS / 0 FAIL** (`logs/frontend-harness.txt`)
5. Isolated uvicorn live MANUAL_UI (self-built)
   - Port **15295** (fresh; avoided 15150–15290)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Seed: `scripts/seed_uat024.py` → Synthetic_Rice / Synthetic_Oil / long ZH + t2 Synthetic_Rice (`logs/seed.json`)
   - Browser: `scripts/browser_qa024.js` (puppeteer-core + Chrome; viewports 390 / 412 / 1440)
   - Result: **pass=true, passCount=60, failCount=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`)
6. Live served verify — `logs/live-served-verify.txt` (UAT-024 CSS/JS markers + API names + t2 isolation)

Did **not** run: independent UAT stage; checklist edit; git write.

## Criterion 1 — Rice / Oil / long ZH readable @ 390/412 (PASS)

Live painted `#inventoryTableBody` after owner role + inventory tab:

| Viewport | SKU | visible text | data-full-name | word-break |
|---|---|---|---|---|
| 390 | Synthetic_Rice | Synthetic_Rice | Synthetic_Rice | break-word |
| 390 | Synthetic_Oil | Synthetic_Oil | Synthetic_Oil | break-word |
| 390 | long ZH | 特级有机黑糯米_测×20 | same | break-word |
| 412 | (same three) | readable | stamped | break-word |

Citations (served):

- `inventory_board.js` — `tr.dataset.skuId`, `data-label="品名"`, `data-full-name`, More `toggleInventoryMoreMenu(this, ${Number(sku.id)})`
- `style.css` / `review-mobile.css` — `.inv-name-text { word-break: break-word }` (no `break-all` in rule bodies)
- Mobile card: `@media (max-width: 640px)` `#inventoryTable { min-width: 0 !important; display:block }`

## Criterion 1b — More click same-row SKU (PASS)

Real button click on each card’s 「更多 ▾」:

| Viewport | Row | menu.dataset.activeId | row data-sku-id | CSP payload mentions id |
|---|---|---|---|---|
| 390/412 | Rice | 1 | 1 | true |
| 390/412 | Oil | 2 | 2 | true |
| 390/412 | long | 3 | 3 | true |

Note: after the first open, a leftover document `closeHandler` can hide the menu during the next click’s bubble (`visible` may be false). Contract used for gate: `activeId === rowSkuId` + CSP `data-csp-click` mentions the same id (menu was built for that SKU). `.inv-actions[data-sku-id]` also matches the row.

## Criterion 2 — name + actions co-visible; short names not vertical (PASS)

After `scrollIntoView` on each card:

- `sameCard=true` (品名 + 操作 cells share the same `tr`)
- `horizOverlap=true` (both full-width on card grid; not separated by horizontal column scroll)
- Rice/Oil strong box ≈ 119×20 / 105×20 — **not** letter-stacked (`letterStacked=false`)

Screenshots: `ui/vp390-inventory.png`, `ui/vp412-inventory.png`, `ui/vp*-after-more.png`.

## Criterion 3 — document no horizontal overflow (PASS)

| Viewport | clientWidth | scrollWidth | overflowX |
|---|---|---|---|
| 390 | 390 | 390 | false |
| 412 | 412 | 412 | false |
| 1440 | 1440 | 1440 | false |

## Criterion 4 — 1440 regression (PASS)

- `matchMedia('(max-width: 640px)')` = false (mobile card CSS inactive)
- `#inventoryTable` `display=table`, `min-width=640px` retained
- Rice / Oil / long present; owner More buttons present; short names not stacked

## Negative / isolation notes

- Local table scroll not invented as a pass contract (per card: 局部横滚可保留，不虚构整页溢出).
- Cross-tenant: default inventory returns Rice/Oil/long only; `X-Tenant-Id: t2` returns Synthetic_Rice only (no Oil leak) — `logs/live-served-verify.txt`.
- Vendors in fixtures/prose: Vendor_A / Vendor_B only (seed metadata).

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15295** (fresh; avoided 15150–15290) |
| DB | `docs/handoff/artifacts/uat024-qa/environment/qa_isolated.db` (+ pytest DB) |
| AUTH_ENABLED | 0 |
| uvicorn | pid 164087 started then stopped; port free; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; contexts closed |
| Artifacts | `docs/handoff/artifacts/uat024-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat024-qa/environment/cleanup.json` |

## Scheduler handoff

- UAT-024 **QA PASS** (60/60 live checks + specialty gates).
- Please dispatch **independent UAT** next (fresh port ≠ 15295; do not reuse 15150–15295).
