# UAT-031 Development Report

> Generated: 2026-10-17 15:13 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-031] SKU合并没有迁移库存批次，后续FIFO错误暂估 — P1 PRODUCT_FAULT MANUAL_UI
> Git: no commit / no push (per dispatch)
> Isolate port: **15131** (15010 kept free)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS (merge previously only moved stock totals / logs / items; batches stayed on secondary → FIFO shortfall → `is_estimated=1`) |
| Product fix present / verified | PASS (in-tree since `364b407`; this turn re-verified, no new product edit required) |
| Specialty pytest (6 AC cases) | PASS |
| Related merge regression (5) | PASS |
| Isolated port 15131 HTTP self-check | PASS (merge migrates batches; consume 1.25kg cost 52.5; zero estimated; tenant B unchanged; orphan dry-run) |
| Failure-injection rollback (pytest) | PASS |
| Historical consumed traces kept (pytest) | PASS |
| No unapproved mass stock repair | PASS (orphan migrate defaults dry_run + PENDING_APPROVAL) |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

Before the UAT-031 fix, `db.merge_skus` could raise secondary `current_stock` into the primary and re-point receipt items / stock logs, but **left `inventory_batches.sku_id` on the deactivated secondary**. After merge the primary showed available stock 2kg while FIFO only saw the primary's own open batch (1kg). Consuming 1.25kg therefore fell through `CostingService.deduct_consumption_fifo` into the shortfall branch and created an `is_estimated=1` batch (estimated / 暂估), corrupting cost provenance.

## Fix (already in HEAD; verified this turn)

Landed in commit `364b407` (`feat(uag): 20 项原子任务三阶段闭环…`). This cloud clone had **no local UAT-031 artifacts** and checklist still `NOT_RUN`, so this Dev turn re-ran AC on an isolate port and wrote packets. **No additional product code change in this turn** (working tree product files for this card already match the fix).

### `demo/app/db.py`

- `merge_skus`: within one transaction, migrate `_InventoryBatchRow` / `_StockLogRow` / `_ItemRow` to primary; set `source_sku_id` provenance; append `[sku_merged: from_sku_id=…]` on log notes; sum stock; deactivate secondary; `_repoint_vendor_sku_alias`; any exception → `rollback` + re-raise.
- Tenant guard: cross-tenant secondary → `(None, "SECONDARY_NOT_FOUND")` (also when `tenant_id is None` if tenants differ).
- Same batch/log/item migration in `deduplicate_skus_by_canonical` (shared root cause).
- Schema: `source_sku_id` on receipt_items / inventory_log / inventory_batches (migration v6).
- `find_orphan_inventory_batches` (read-only) + `migrate_orphan_inventory_batches(dry_run=True, approved=False)` → `PENDING_APPROVAL` (no silent mass repair).

### `demo/app/api_inventory.py`

- `POST /api/inventory/skus/merge` → `db.merge_skus`; 500 `MERGE_FAILED` on exception (rolled back).
- `GET /api/inventory/batches/orphan`, `POST /api/inventory/batches/orphan/migrate` (body defaults dry_run / not approved).

### `demo/app/services/inventory.py`

- `reverse_receipt_inventory` looks up batches by `(sku_id == X) | (source_sku_id == X)` for the receipt so post-merge provenance still finds inbound batches on reverse.

### Tests

- `demo/tests/test_uat_031_sku_merge_batch_migration.py` (6 cases: migrate+provenance, FIFO no estimated, rollback, tenant isolation, historical traces, orphan dry-run).

## Diffs this turn

| Path | Change |
|---|---|
| Product (`db.py` / `api_inventory.py` / `inventory.py`) | **none** (fix already in HEAD `364b407`) |
| `docs/handoff/artifacts/uat031-dev/**` | new isolate evidence + scripts |
| `docs/handoff/artifacts/uat031-dev-report.md` | this report |
| `docs/handoff/01-acceptance-checklist.md` | UAT-031 → `[TODO] DEV_DELIVERED` only |

Key existing fix locus (`demo/app/db.py` ~3681–3746): migrate batches first with `source_sku_id`, then logs/items, stock merge, deactivate, alias repoint, commit; except → rollback.

## Repro / self-check (isolated port; 15010 free)

1. Temp DB `artifacts/uat031-dev/environment/uat031.db`, `AUTH_ENABLED=0`, uvicorn **127.0.0.1:15131**.
2. Tenant B: create SKU + approve 3kg @30 → baseline stock 3.0.
3. Tenant A: create primary + secondary; approve 1kg @40 and 1kg @50 → each has one open batch remaining 1.0.
4. `POST /api/inventory/skus/merge` `{primary, [secondary]}` → `new_stock=2.0`, `batches_migrated=1`.
5. Primary detail: 2 open batches remaining 1.0 each; secondary active=0 / stock 0; batches carry `source_sku_id`.
6. Cross-tenant merge (A primary + B SKU) → error (rejected).
7. `POST /api/inventory/{primary}/consume` quantity 1.25 → **cost 52.5** (1×40 + 0.25×50); DB `is_estimated=0` count for primary; open remaining 0.75.
8. `POST /api/inventory/batches/orphan/migrate` `{}` → `dry_run=true`, `PENDING_APPROVAL`.
9. Tenant B stock still 3.0.
10. Stop uvicorn; ports **15131** and **15010** free.

Evidence: `docs/handoff/artifacts/uat031-dev/logs/selfcheck-evidence.json` (`verdict=PASS`), `pytest-specialty.log`, `uvicorn.log`.
Script: `docs/handoff/artifacts/uat031-dev/scripts/selfcheck_uat031.py`.

## Self-check mapping (card AC)

| AC | Result |
|---|---|
| Open remaining=1 batch merged; receipt/log/batch → primary; source provenance | PASS (pytest req1 + isolate merge/db) |
| After merge available 2kg, consume 1.25kg must NOT show estimated_partial | PASS (cost 52.5; estimated_count=0) |
| Failure-injection rolls back transaction | PASS (pytest req3 monkeypatch `_repoint_vendor_sku_alias`) |
| Other tenant batches unchanged | PASS (pytest req4 + isolate B baseline) |
| Must not only change stock total / delete old batches; historical consumed traces kept | PASS (pytest req5; batches kept with initial_qty) |
| Stock repair needs dry-run / backup / idempotent + separate approval — do not invent unapproved mass repair | PASS (default dry-run; no approved apply in this turn) |

## Commands (exit codes)

```
pytest demo/tests/test_uat_031_sku_merge_batch_migration.py -v
  → 6 passed, exit 0

pytest demo/tests/test_uat_031_sku_merge_batch_migration.py \
       demo/tests/test_merge_skus_no_dead_memory_write.py -v
  → 11 passed, exit 0

python docs/handoff/artifacts/uat031-dev/scripts/selfcheck_uat031.py
  → verdict=PASS, exit 0
```

## Cleanup

- Isolate uvicorn on 15131 stopped; **15010** never bound.
- Fixture DB retained under `artifacts/uat031-dev/environment/` for QA handoff (not production).

## Limits / honesty

- This report is **Dev self-check only**. Independent QA and MANUAL_UI UAT still required before DONE.
- Ledger on the original host claimed a prior three-phase close; this clone lacked local packets — this turn supplies cloud-local Dev evidence only.
- Did **not** run approved orphan migrate against any real stock; did **not** invent a mass repair path.
