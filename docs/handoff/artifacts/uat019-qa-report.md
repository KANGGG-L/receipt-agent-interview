# UAT019 Independent QA Report

> Generated: 2026-10-17 07:16 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT019 acceptance only (huge sale ordinary → 409 no silent negative; force+reason contract; normal 0.5; illegal date/non-number no write; duplicate request_id; no new cap; reverse 409 intact; Tenant B)
> Card: [UAT019] 极大售出数量无确认直接扣成巨额负库存 — P2 QUALITY_RISK MANUAL_UI
> Prior: `uat019-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)
> Checklist: left `[TODO] DEV_DELIVERED` (NOT flipped to DONE)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. stock 1kg + BOM 0.5kg/portion + sale 999999999999: ordinary click/API must NOT silently create huge negative (409 INSUFFICIENT_STOCK) | **PASS** |
| 2. Existing 409 → explicit force+reason path only as contract | **PASS** |
| 3. Normal 1 portion deducts 0.5 | **PASS** |
| 4. Illegal date / non-number no write | **PASS** |
| 5. Duplicate request_id no double-deduct | **PASS** |
| Negative: no new business sale-qty cap; reverse 409 not loosened; Tenant B unchanged | **PASS** |

**UAT019 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped (still `[TODO] DEV_DELIVERED`).

## Commands actually run

1. Isolated uvicorn on **15255** (fresh; ≠ **15245**; avoided **15010** / **15215** / **15225** / **15235** and earlier UAT ports)
   - `DB_PATH=…/uat019-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid from `logs/uvicorn.pid` → stopped (`environment/cleanup.json`)
   - Port **15010** remained free throughout
2. API acceptance — `scripts/qa_api_check.py` — tenants `uat019qa2` / B `uat019qab2` — **28/28 PASS** (`logs/qa_api_check.json`)
3. Specialty pytest — `demo/tests/test_uat_019_huge_sale_overdraw.py` — **6 passed** (`logs/pytest_uat019.log`)
4. Live Puppeteer dish-consume overdraw — `scripts/qa_ui_overdraw.js` (tenant `uat019qaui2`) — **10/10 PASS** (`logs/qa_ui_overdraw.json`, screenshots under `ui/`)
5. Tenant B via API only (`X-Tenant-Id: uat019qab2`) — stock baseline **7.0** before / after AC1 reject / final

Did **not** run: independent UAT; checklist/ledger flip to DONE; git write.

## Criterion 1 — Ordinary huge sale → 409, no silent negative (PASS)

API (tenant `uat019qa2`, stock 1kg, BOM 0.5kg/portion, qty `999999999999`):

| Check | Observed |
|---|---|
| HTTP | **409** |
| `code` | `INSUFFICIENT_STOCK` |
| `can_override` / `reason_required` | **true** / **true** |
| `shortfalls[0].requested` | **499999999999.5** |
| `shortfalls[0].shortfall` | **499999999998.5** |
| Stock after ordinary submit | **1.0** (not `-499999999998.5`) |

Browser (Hans 1440, dish daily consume):

| Check | Observed |
|---|---|
| Fill qty `999999999999` + click submit | batch **409** `INSUFFICIENT_STOCK` |
| `uiPrompt` title | `库存不够扣` |
| Cancel 「先不记」 | stock stays **1** |

Evidence: `logs/qa_api_check.json` (`ac1_*`), `logs/qa_ui_overdraw.json`, `ui/overdraw_prompt.png`.

## Criterion 2 — 409 → explicit force+reason only (PASS)

| Path | HTTP | Stock |
|---|---|---|
| `allow_negative=true` without reason | **409** (msg contains 「原因」) | **1.0** |
| `allow_negative=true` + `override_reason` (qty 4 → need 2kg) | **200** | **-1.0** |
| Extreme finite WITH force (qty `999999999999`) | **200** | **-499999999998.5** (no new business cap) |

Browser: cancel path no write; confirm with reason → 409 then 200 → stock **-1**.

Evidence: `ac2_*`, `ui_force_*`, `ui/overdraw_prompt_force.png`, `ui/after_force.png`.

## Criterion 3 — Normal 1 portion deducts 0.5 (PASS)

| Surface | Result |
|---|---|
| API qty 1 | HTTP **200**, stock **0.5** |
| Specialty pytest `test_normal_one_portion_deducts_half` | PASS |
| Browser after stocktake reset | HTTP **200**, stock **0.5** |

## Criterion 4 — Illegal date / non-number no write (PASS)

| Input | HTTP | code / note |
|---|---|---|
| `date: "not-a-date"` | 400 | `INVALID_CONSUMPTION_DATE` |
| Future date (+2d) | 400 | `INVALID_CONSUMPTION_DATE` |
| JSON `quantity: NaN` | 400 | rejected; stock untouched **1.0** |

## Criterion 5 — Duplicate request_id no double-deduct (PASS)

Stock 10kg, BOM 0.5, qty 2, same `request_id`:

| Step | Result |
|---|---|
| First | 200 |
| Second | 200, `replayed: true` |
| Final stock | **9.0** (deducted once: 1kg) |

## Negative boundaries (PASS)

| Boundary | Evidence |
|---|---|
| No new sale-qty business cap | Extreme finite still allowed with force+reason (stock → huge negative only on explicit force) |
| Reverse / already-consumed 409 not loosened | `ReversalBlocked` class + `raise ReversalBlocked` still present; comment 「Do not loosen」 intact in `inventory.py` (helpers added below, reverse path untouched) |
| Tenant B unchanged | Seed stock **7.0**; after default-tenant huge reject **7.0**; final dishes `[]`, stock **7.0** |
| Residual extreme finite risk | Remains open (PM upper-limit adjudication) — not closed by this card |

## Specialty pytest

```
demo/tests/test_uat_019_huge_sale_overdraw.py — 6 passed
```

## Cleanup

- uvicorn on **15255** stopped; port freed
- **15010** remained free
- Artifacts under `docs/handoff/artifacts/uat019-qa/` (`environment/`, `logs/`, `scripts/`, `ui/`)
- Checklist / ledger: **not** advanced past `[TODO] DEV_DELIVERED`
- No git commit / push
- No independent UAT started

## Scheduler handoff

- **QA: PASS**
- Please dispatch **independent UAT** next (fresh isolation port ≠ 15255/15245/15215/15225/15235/15010)
- Only after UAT PASS flip `[UAT019]` to `[DONE] PASS`
