# UAT-031 Independent QA Report

> Generated: 2026-10-17 15:15 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-031 acceptance only (SKU merge migrates open batches + receipt/log/batch unify + provenance; FIFO consume 1.25kg no estimated_partial; failure-injection rollback; other tenant unchanged; not stock-total-only; no delete of historical consumed traces)
> Card: [UAT-031] SKU合并没有迁移库存批次，后续FIFO错误暂估 — P1 PRODUCT_FAULT MANUAL_UI
> Prior: `uat031-dev-report.md`
> UAT: NOT started (per dispatch: No UAT / no DONE / no git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. Source SKU open remaining=1 batch merge → main SKU; receipt/log/batch unify; provenance kept | **PASS** |
| 2. After merge available 2kg consume 1.25kg: no estimated_partial | **PASS** |
| 3. Failure-injection transaction rollback | **PASS** |
| 4. Other tenant batches unchanged | **PASS** |
| Negative: not stock-total-only; no delete of historical consumed traces | **PASS** |

**UAT-031 QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped (still `[TODO] DEV_DELIVERED`).

## Commands actually run

1. Isolated uvicorn on **15141** (fresh; ≠ **15131**; **15010** never bound)
   - `DB_PATH=…/uat031-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`, pid **256096** → stopped (`environment/cleanup.json`)
2. Specialty + merge regression pytest — **11 passed** (`logs/pytest-specialty.log`)
   - `demo/tests/test_uat_031_sku_merge_batch_migration.py` (6)
   - `demo/tests/test_merge_skus_no_dead_memory_write.py` (5)
3. API + DB acceptance — `scripts/qa_api_check.py` — tenants `uat031qa` / B `uat031qab` — **10/10 PASS** (`logs/qa_api_check.json`)

Did **not** run: independent UAT; checklist/ledger flip; git write; approved orphan migrate against real stock.

## Criterion 1 — Open batch merge + unify + provenance (PASS)

HTTP (tenant `uat031qa`):

| Check | Observed |
|---|---|
| Pre-merge primary / secondary | stock 1.0 each; one open batch remaining 1.0 each (@40 / @50) |
| `POST /api/inventory/skus/merge` | HTTP 200; `new_stock=2.0`; `batches_migrated=1`; `logs_migrated=1`; `items_migrated=1` |
| Post-merge primary detail | stock **2.0**; **2** open batches remaining **1.0** each (batch_id 2 @40, batch_id 3 @50) |

DB after merge (same isolate DB):

| Check | Observed |
|---|---|
| Secondary `active` / stock | `0` / `0.0` |
| Batches left on secondary `sku_id` | **0** (not stock-total-only) |
| Batches with `source_sku_id == secondary` | **>=1**, all now `sku_id == primary` |
| Stock logs with `source_sku_id == secondary` | **>=1**, `sku_id == primary`, note carries merge provenance (`from_sku_id=` / `sku_merged`) |
| Receipt items with `source_sku_id == secondary` | **>=1**, `sku_id == primary` |

Pytest: `test_req1_open_batch_and_records_migrated_with_source_provenance` PASSED.

Evidence: `logs/qa_api_check.json` (`merge_api`, `ac1_post_merge_batches`, `ac1_db_unify_provenance`).

## Criterion 2 — Consume 1.25kg after merge; no estimated_partial (PASS)

| Check | Observed |
|---|---|
| `POST /api/inventory/{primary}/consume` qty 1.25 | HTTP 200; **cost 52.5** (= 1×40 + 0.25×50) |
| `current_stock` after | **0.75** |
| DB `is_estimated=1` on primary | **0** |
| Open remaining | **0.75** on migrated second batch |
| Both batch rows kept | count **2**; `initial_qty` still `[1.0, 1.0]` |

Pytest: `test_req2_fifo_deduction_after_merge_no_estimated_partial` PASSED (FIFO details two real batches; no estimated rows).

Evidence: `logs/qa_api_check.json` (`ac2_consume_cost`, `ac2_no_estimated_and_neg_keep_batches`).

## Criterion 3 — Failure-injection transaction rollback (PASS)

Verified via specialty pytest (HTTP cannot inject mid-transaction crash without product hooks):

- `test_req3_failure_injection_transaction_rollback` monkeypatches `_repoint_vendor_sku_alias` to raise `RuntimeError`
- After failed merge: primary stock still 1.0; secondary stock 1.0 and `active=1`; secondary batch / item / log still on secondary (no half-merge)

Evidence: `logs/pytest-specialty.log` (PASSED).

## Criterion 4 — Other tenant batches unchanged (PASS)

| Check | Observed |
|---|---|
| Tenant B baseline | SKU stock **3.0**; one batch remaining 3.0 @30 |
| Cross-tenant merge (A primary + B SKU) | `status=error`, `code=SECONDARY_NOT_FOUND` |
| Tenant B after A merge + consume | stock still **3.0**; same batch remaining 3.0 |

Pytest: `test_req4_other_tenant_batches_untouched_and_cross_tenant_rejected` PASSED.

Evidence: `logs/qa_api_check.json` (`tenant_b_baseline`, `cross_tenant_rejected`, `ac4_tenant_b_unchanged`).

## Negative — not stock-total-only; no delete of historical consumed traces (PASS)

| Negative | How verified | Result |
|---|---|---|
| Must not only change stock total | Merge reports `batches_migrated`/`logs_migrated`/`items_migrated`; DB shows zero batches left on secondary; primary has 2 open batches | PASS |
| Must not delete historical consumed traces / rewrite batches | Post-consume both batch rows still exist with original `initial_qty`; pytest req5 keeps pre-merge consumption detail + batch `initial_qty=2.0` after partial consume | PASS |
| Orphan migrate default dry-run | `POST /api/inventory/batches/orphan/migrate` `{}` → `dry_run=true`, `PENDING_APPROVAL` | PASS |

Pytest: `test_req5_historical_consumed_traces_preserved`, `test_req6_orphan_batch_dry_run_default` PASSED.

## Cleanup

- Uvicorn pid 256096 on **15141** stopped; **15010** and **15131** never bound / remain free (`environment/cleanup.json`).
- Isolate DB retained under `uat031-qa/environment/` for handoff (not production).
- No git; no DONE; no UAT; no checklist flip.

## Limits / honesty

- This report is **independent QA only**. MANUAL_UI UAT still required before DONE.
- Failure-injection (AC3) covered by specialty pytest monkeypatch, not live HTTP crash injection.
- Did **not** run approved orphan migrate against any real stock.
