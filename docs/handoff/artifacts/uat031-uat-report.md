# UAT-031 Independent UAT Report

> Generated: 2026-10-17 15:17 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — SKU merge migrates open batches + receipt/log/batch unify + provenance; after merge available 2kg consume 1.25kg with no estimated_partial; failure-injection rollback; other tenant unchanged; historical consumed traces kept (not stock-total-only / no delete of history)
> Card: [UAT-031] SKU合并没有迁移库存批次，后续FIFO错误暂估 — P1 PRODUCT_FAULT MANUAL_UI
> Prior gates (not reused as PASS): `uat031-dev-report.md` (port 15131), `uat031-qa-report.md` (port 15141)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Merge migrates open batches + receipt/log/batch unify; source provenance kept | **PASS** |
| 2. After merge available 2kg, consume 1.25kg: no estimated_partial (cost 52.5) | **PASS** |
| 3. Failure-injection transaction rollback | **PASS** (specialty pytest monkeypatch) |
| 4. Other tenant batches unchanged | **PASS** |
| Negative: not stock-total-only; historical consumed traces / batch rows kept | **PASS** |

**UAT-031 UAT gate: PASS.** Checklist flipped to `[DONE] PASS` on this UAT (per dispatch). Ledger next = **UAT-032** (now unblocked).

## Isolation (fresh; not QA/DEV)

| Field | Value |
|---|---|
| Port | **15251** (≠15141 QA / ≠15131 DEV) |
| Avoided | **15010 kept free**; 15131 / 15141 never bound this turn |
| DB | `docs/handoff/artifacts/uat031-uat/environment/uat_isolated.db` (copy of `demo/receipt_demo.db`) |
| Tenants | `uat031uat` / `uat031uatb` (UAT-owned; not QA `uat031qa` / DEV) |
| AUTH_ENABLED | 0 |
| Server pid | 257609 (stopped; `environment/cleanup.json`) |
| Venv | `docs/handoff/artifacts/uat025-qa/environment/venv` |

## Commands actually run

1. Isolated uvicorn `127.0.0.1:15251` with stage DB / AUTH_ENABLED=0
2. Specialty + merge regression pytest — **11 passed** (`logs/pytest-specialty.log`)
   - `demo/tests/test_uat_031_sku_merge_batch_migration.py` (6; includes failure-injection rollback + historical traces)
   - `demo/tests/test_merge_skus_no_dead_memory_write.py` (5)
3. API + DB acceptance — `scripts/uat_api_check.py` — tenants `uat031uat` / B `uat031uatb` — **10/10 PASS** (`logs/uat_api_check.json`)
4. Live Puppeteer MANUAL_UI SKU merge — seed UI pair + `scripts/uat_ui_merge.js` — **3/3 PASS** (`logs/uat_ui_merge.json`, screenshots under `ui/`)
5. Post-UI merge consume / DB provenance — `logs/uat_ui_post_verify.json` — **4/4 PASS** (cost 52.5; estimated=0; tenant B still 3.0)
6. Stop uvicorn; port 15251 free; reuseaddr bind OK; **15010** free

Did **not**: git commit / push; reuse QA/DEV ports or tenants as evidence; approved orphan migrate against real stock.

## Criterion 1 — Merge migrates batches + unify + provenance (PASS)

### API path (tenant `uat031uat`)

| Check | Observed |
|---|---|
| Pre-merge primary / secondary | stock 1.0 each; one open batch remaining 1.0 each (@40 / @50) |
| `POST /api/inventory/skus/merge` | HTTP 200; `new_stock=2.0`; `batches_migrated=1`; `logs_migrated=1`; `items_migrated=1` |
| Post-merge primary detail | stock **2.0**; **2** open batches remaining **1.0** each |
| Secondary `active` / stock | `0` / `0.0` |
| Batches left on secondary `sku_id` | **0** (not stock-total-only) |
| Batches / logs / items with `source_sku_id == secondary` | all now `sku_id == primary`; log note carries merge provenance |

### MANUAL_UI path (live Chrome / Puppeteer, same isolate)

| Check | Observed |
|---|---|
| Seed UI SKUs | primary id **4** / secondary id **5** (`logs/ui_seed.json`) |
| Open `#inventory` → `openSkuMergeModalFromInv` | modal visible (`ui/merge_modal_open.png`) |
| Select primary 4 + checkbox secondary 5 → `submitSkuMerge` | POST `/api/inventory/skus/merge` **200** `status=success` |
| Response data | `new_stock=2`, `batches_migrated=1`, `logs_migrated=1`, `items_migrated=1` |
| Screenshots | `ui/inventory_before_merge.png`, `merge_modal_selected.png`, `after_merge.png` |

Evidence: `logs/uat_api_check.json`, `logs/uat_ui_merge.json`, `logs/uat_ui_post_verify.json`.

## Criterion 2 — Consume 1.25kg after merge; no estimated_partial (PASS)

| Path | Consume | Cost | `is_estimated=1` | Open remaining | Batch rows kept |
|---|---|---|---|---|---|
| API (after API merge ids 2/3) | qty 1.25 HTTP 200 | **52.5** (=1×40+0.25×50) | **0** | **0.75** | 2 rows; `initial_qty` [1.0,1.0] |
| Post-UI (after UI merge ids 4/5) | qty 1.25 HTTP 200 | **52.5** | **0** | present | 2 rows; initials [1.0,1.0] |

Pytest: `test_req2_fifo_deduction_after_merge_no_estimated_partial` PASSED.

Evidence: `logs/uat_api_check.json` (`ac2_*`), `logs/uat_ui_post_verify.json`, `logs/pytest-specialty.log`.

## Criterion 3 — Failure-injection transaction rollback (PASS)

Verified via specialty pytest (live HTTP cannot inject mid-transaction crash without product hooks):

- `test_req3_failure_injection_transaction_rollback` monkeypatches `_repoint_vendor_sku_alias` to raise `RuntimeError`
- After failed merge: primary stock still 1.0; secondary stock 1.0 and `active=1`; secondary batch / item / log still on secondary (no half-merge)

Evidence: `logs/pytest-specialty.log` (PASSED).

## Criterion 4 — Other tenant batches unchanged (PASS)

| Check | Observed |
|---|---|
| Tenant B baseline | SKU stock **3.0**; one batch remaining 3.0 @30 |
| Cross-tenant merge (A primary + B SKU) | `status=error` (rejected) |
| Tenant B after A API merge+consume | stock still **3.0** |
| Tenant B after UI merge+consume | stock still **3.0** |

Pytest: `test_req4_other_tenant_batches_untouched_and_cross_tenant_rejected` PASSED.

Evidence: `logs/uat_api_check.json` (`tenant_b_baseline`, `cross_tenant_rejected`, `ac4_tenant_b_unchanged`), `logs/uat_ui_post_verify.json`.

## Negative — not stock-total-only; historical traces kept (PASS)

| Negative | How verified | Result |
|---|---|---|
| Must not only change stock total | Merge reports `batches_migrated`/`logs_migrated`/`items_migrated`; DB zero batches left on secondary; primary has 2 open batches | PASS |
| Must not delete historical consumed traces / rewrite batches | Post-consume both batch rows still exist with original `initial_qty`; pytest req5 keeps pre-merge consumption detail | PASS |
| Orphan migrate default dry-run | `POST /api/inventory/batches/orphan/migrate` `{}` → `dry_run=true`, `PENDING_APPROVAL` | PASS |

Pytest: `test_req5_historical_consumed_traces_preserved`, `test_req6_orphan_batch_dry_run_default` PASSED.

## Cleanup

- Uvicorn pid 257609 on **15251** stopped; reuseaddr bind OK; **15010** / **15131** / **15141** free (`environment/cleanup.json`).
- Isolate DB retained under `uat031-uat/environment/` for handoff (not production).
- No git push.

## Limits / honesty

- Failure-injection (AC3) covered by specialty pytest monkeypatch, not live HTTP crash injection (same boundary as independent QA).
- Did **not** run approved orphan migrate against any real stock.
- UI consume after merge used the same owner API consume endpoint (FIFO cost path); merge itself was exercised via live browser modal submit.
