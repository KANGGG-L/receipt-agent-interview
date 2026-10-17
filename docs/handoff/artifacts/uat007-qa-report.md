# UAT-007 Independent QA Report

> Generated: 2026-10-17 04:10 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT-007 acceptance only (ledger qty sign / consume display)
> Card: [UAT-007] 消耗台账数量方向显示为正数 — P1 PRODUCT_FAULT
> UAT: NOT started (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. consume 1 from stock 10 -> ledger qty_signed -1, stock 9 | **PASS** |
| 2. inbound +1 -> qty_signed +1, stock 10 | **PASS** |
| 3. 冲销 / adjust opposite sign | **PASS** |
| 4. same event id across DB/API (UI binds same row fields) | **PASS** |
| 5. display uses qty_signed; raw qty/id unchanged; no double stock deduct | **PASS** |
| Negative: cross-tenant reject / B baseline unchanged | **PASS** |

**UAT-007 QA gate: PASS.** Do not treat this as card close; independent UAT (real browser MANUAL_UI) not started.

## Commands run (actually executed)

1. Static evidence scan (write-path still positive magnitude; UI helpers; old blind-plus gone)
   - Log: `docs/handoff/artifacts/uat007-qa/logs/static-evidence.txt`
2. `pytest demo/tests/test_uat_007_ledger_qty_sign.py tests/test_inventory_ledger_api.py -v --tb=short`
   - Venv: `docs/handoff/artifacts/uat003-qa/environment/venv` (linked for deps; fresh DB/port for this stage)
   - Fresh `DB_PATH=.../uat007-qa/environment/qa_pytest.db`
   - Log: `docs/handoff/artifacts/uat007-qa/logs/pytest-uat007.txt`
   - Result: **12 passed** (specialty 5 + ledger API 7)
3. Consume regression (no double-deduct / state machine)
   - `pytest demo/tests/test_b2_sku_inventory.py::test_owner_can_confirm_overdraw_with_reason_and_it_is_recorded demo/tests/test_b2_sku_inventory.py::test_staff_cannot_consume_and_nan_rejected -q`
   - Log: `docs/handoff/artifacts/uat007-qa/logs/pytest-consume-regression.txt`
   - Result: **2 passed**
4. `node --check demo/static/js/inventory_board.js` + `python3 -m py_compile demo/app/api_inventory.py` — OK
5. Isolated uvicorn smoke (self-built; fresh port/DB)
   - Port **15190** (avoided 15150-15180), DB `qa_isolated_smoke.db`, pid 119552
   - `GET /` 200; served JS contains `invLedgerQtySigned` / `invLedgerQtyText` / `qty_signed`; old blind-plus absent
   - Log: `docs/handoff/artifacts/uat007-qa/logs/uvicorn-15190.log`
6. Live API seed + verify on :15190
   - Synthetic_Rice_QA: in 10 -> consume 1 -> stock 9 / qty_signed -1 / qty 1; inbound 1 -> stock 10 / qty_signed +1; reversal -1 / adjust +1; second consume stock 9 once; cross-tenant 404 / B stock 7 unchanged
   - Logs: `docs/handoff/artifacts/uat007-qa/logs/live-api-verify.txt`, `api-logs-default.json`
7. Node qty-text harness (mirrors inventory_board.js helpers)
   - Log: `docs/handoff/artifacts/uat007-qa/logs/node-qty-signed.txt`
   - Exit 0; `NODE_HARNESS_PASS=true`

## Criterion 1 evidence — consume 1 / stock 9 / qty_signed -1

Self-checked citations:

- `demo/app/api_inventory.py:629` — `qty=body.quantity,` (DB still stores positive magnitude)
- `demo/app/api_inventory.py:745` — `def ledger_qty_signed(kind, qty):`
- `demo/app/api_inventory.py:758-759` — outbound kinds return `-abs(q)`
- `demo/app/api_inventory.py:826-827` — API returns both `qty` and `qty_signed`

Runtime:

- Pytest `test_consume_inbound_signed_stock_and_id_alignment` PASSED
- Live :15190: `CONSUME_STATUS=200 STOCK=9.0`; row `id=2 qty=1.0 qty_signed=-1.0`

## Criterion 2 evidence — inbound +1 / stock 10

- Live: `STOCK_AFTER_IN1=10.0`; `IN1_ROW ... qty=1.0 qty_signed=1.0`
- Pytest specialty case covers same path

## Criterion 3 evidence — 冲销 opposite direction

- Live: `REVERSAL qty=-1.0 qty_signed=-1.0`; `ADJUST qty=1.0 qty_signed=1.0`
- Pytest `test_reversal_and_adjust_opposite_direction` PASSED (reversal -5 / adjust +1 / in +5)

## Criterion 4 evidence — same event id

- Live: `ID_ALIGN db_id=2 api_id=2 db_qty=1.0 PASS` (session.get `_StockLogRow` by API id)
- UI renders from same API row object (`inventory_board.js:701` `invLedgerQtyText(r)`); no separate id remapping
- Real painted-table id column is out of MANUAL_UI scope for this QA stage

## Criterion 5 evidence — display uses qty_signed; raw unchanged; no double deduct

Citations:

- `demo/static/js/inventory_board.js:635` — `function invLedgerQtySigned(r) {`
- `demo/static/js/inventory_board.js:645` — `function invLedgerQtyText(r) {`
- `demo/static/js/inventory_board.js:649` — sign prefix applied to **signed** value only
- `demo/static/js/inventory_board.js:701` — `const qtyText = invLedgerQtyText(r);`
- `demo/static/js/inventory_board.js:717` — qty cell uses `qtyText`
- Old buggy pattern `const qtyText = (qty > 0 ? '+' : '') + qty;` **absent** (`OLD_PATTERN_ABSENT=true`)

Runtime:

- Node harness: consume -> `-1`, in -> `+1`, waste -> `-2.5`, fallback without `qty_signed` still `-1`
- Live second consume from stock 10 -> 9 (single deduct); consume API rows keep `qty=1.0` raw
- Write path still `qty=body.quantity` positive (`api_inventory.py:629`); `ledger_qty_signed` is read-only derivation
- B2 overdraw / staff-cannot-consume regression: 2 passed

Cross-tenant:

- Live `CROSS_TENANT_CONSUME_STATUS=404`; `CROSS_TENANT_B_STOCK_UNCHANGED=7.0`
- Pytest `test_cross_tenant_logs_invisible_and_stock_machine_unchanged` PASSED

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | 15190 (fresh; not 15150-15180) |
| DB | `docs/handoff/artifacts/uat007-qa/environment/qa_isolated_smoke.db` (+ separate `qa_pytest.db`) |
| Fixture | Synthetic_Rice_QA / TenantB_SKU; Vendor_A / Vendor_B only |
| Stop | `STOPPED_OWNED_TEST_PROCESS`, pid 119552 dead |
| Port free | true (`ss` LISTEN empty; SO_REUSEADDR bind ok) |
| Browser tabs | 0 opened; `browser_tab_closed=true` |
| 15010 | not present on this host; untouched |
| cleanup.json | `docs/handoff/artifacts/uat007-qa/environment/cleanup.json` |

No orphan uvicorn left. No git commit / push. Checklist card left `[TODO]` (scheduler closes after independent UAT only).

## Residual risks (honest)

1. **No real browser / Orca painted-pixel proof**: display direction verified via API `qty_signed` + JS source + Node text harness. Independent MANUAL_UI must still open `#inventoryLedgerTable` on a **new** 151xx port and confirm consume shows `-1` (not `+1`) in the painted cell.
2. **UI does not show log `id` column**: DB/API id alignment proven; UI on-screen id equality is only via shared API payload fields, not a visible id column.
3. **Broader `test_b2_sku_inventory.py`**: only consume-path cases exercised here (per dev note that containment/FX failures predate UAT-007). Out of write scope.
4. Checklist in `01-acceptance-checklist.md` still `[TODO] NOT_RUN`; do not flip from this QA alone.

## Scheduler signal

- **QA gate: PASS**
- Evidence path: `docs/handoff/artifacts/uat007-qa-report.md`
- Supporting logs/dir: `docs/handoff/artifacts/uat007-qa/`
- **UAT: not started**
- Next: independent UAT on a **new** port / DB / fixture (do not reuse 15190 env)
