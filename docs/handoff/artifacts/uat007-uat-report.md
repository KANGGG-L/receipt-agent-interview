# UAT-007 Independent UAT Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation of inventory ledger qty sign direction
> Card: [UAT-007] 消耗台账数量方向显示为正数 — P1 PRODUCT_FAULT
> Prior gates (not reused): `uat007-dev-report.md`, `uat007-qa-report.md` (QA port 15190)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. consume 1 from stock 10 → ledger shows -1, stock 9 | **PASS** |
| 2. inbound +1 → ledger +1, stock 10 | **PASS** |
| 3. 冲销 opposite direction (reversal / adjust) | **PASS** |
| 4. same event id across DB/API; UI binds same row fields | **PASS** |
| Negative: no double stock deduct; raw qty magnitude unchanged | **PASS** |
| Negative: cross-tenant reject / B baseline unchanged | **PASS** |

**UAT-007 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15200** (QA used 15190; forbidden reuse of 15150–15190) |
| Aux port | **15201** (static harness host for Chrome only) |
| DB | `docs/handoff/artifacts/uat007-uat/environment/uat_isolated.db` (fresh seeded) |
| Fixture | Synthetic_Rice_UAT 10kg (default) / TenantB_SKU_UAT 7kg (t2); Vendor_A / Vendor_B |
| Venv | `docs/handoff/artifacts/uat007-uat/environment/venv` (stage-local link) |
| Server pid | 121522 (stopped at end) |
| HTTP pid | 122076 (stopped at end) |

## Commands actually run

1. Seed fresh DB via `app.db.create_sku` + `apply_stock_log` (kind=`in`) — `environment/seed.json`
2. Self-built uvicorn on 127.0.0.1:15200 with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200
3. Live API script `scripts/live_api_verify.py`: consume → inbound → reversal/adjust insert → second consume → cross-tenant — `logs/live-api-verify.txt`, `logs/api-logs-default.json`
4. Static citation print + served JS from :15200 — `logs/static-evidence.txt`
5. Chrome headless `--dump-dom` against harness HTML loading served `inventory_board.js` from :15200, calling real `loadInventoryLedger()` with live API payload — `TITLE_TAG UAT007_PASS`, `logs/chrome-result.json`, `ui/ledger-tbody.html`
6. Node secondary qty-text harness mirroring `invLedgerQtySigned` / `invLedgerQtyText` — `logs/node-qty-signed.txt` `NODE_HARNESS_PASS=true`
7. Full cleanup: stop pids, free ports, `environment/cleanup.json`

Did **not** run: interactive GUI click on full logged-in inventory shell; checklist edit; git write; pytest (reserved for QA stage).

## Criterion 1 — consume 1 / stock 9 / painted `-1`

Self-checked citations (exact line text):

- `demo/app/api_inventory.py:629` — `qty=body.quantity,` (DB still stores positive magnitude)
- `demo/app/api_inventory.py:745` — `def ledger_qty_signed(kind, qty):`
- `demo/app/api_inventory.py:758-759` — outbound kinds return `-abs(q)`
- `demo/app/api_inventory.py:826-827` — API returns both `qty` and `qty_signed`
- `demo/static/js/inventory_board.js:701` — `const qtyText = invLedgerQtyText(r);`
- `demo/static/js/inventory_board.js:717` — qty cell uses `qtyText`

Runtime:

- Live :15200: `CONSUME_STATUS=200 STOCK=9.0`; row `id=3 qty=1.0 qty_signed=-1.0`
- Chrome painted: consume kindLabel `餐品消耗` → qtyText `-1` (not `+1`); `consumeTexts=['-1','-1']`
- Node harness: consume text `-1`

## Criterion 2 — inbound +1 / stock 10

- Live: `STOCK_AFTER_IN1=10.0`; `IN1_ROW id=4 qty=1.0 qty_signed=1.0`
- Chrome: inbound kindLabel `采购入库` → qtyText `+1`; `inTexts=['+1']`

## Criterion 3 — 冲销 opposite direction

- Live: `REVERSAL id=5 qty=-1.0 qty_signed=-1.0`; `ADJUST id=6 qty=1.0 qty_signed=1.0`
- Chrome: reversal qtyText `-1`; adjust qtyText `+1`

## Criterion 4 — same event id

- Live: `ID_ALIGN db_id=3 api_id=3 db_qty=1.0 kind=consume PASS` (`session.get(_StockLogRow)` by API id)
- UI renders from same API row object (`invLedgerQtyText(r)`); no separate id remapping
- Note: ledger table has no visible `id` column; on-screen id equality is via shared API payload fields

## Negative — no double deduct; cross-tenant

- Second consume from stock 10 → 9 once; `CONSUME_COUNT=2` each with raw `qty=1.0` / `qty_signed=-1.0`
- Write path still positive magnitude (`api_inventory.py:629`); `ledger_qty_signed` is read-only derivation
- Cross-tenant: `CROSS_TENANT_CONSUME_STATUS=404`; `CROSS_TENANT_B_STOCK_UNCHANGED=7.0`; B logs do not include tenant A sku

## Residual risks (honest)

1. Harness used minimal `#tab-inventory #inventoryLedgerTable` DOM + served production JS + embedded live API payload (same-origin CORS bypass). Not a full logged-in multi-pane inventory session with real cookie `apiFetch`.
2. UI does not paint a log `id` column; DB/API id alignment proven; UI on-screen id equality only via shared payload fields.
3. Reversal/adjust rows were inserted as stock-log records (same as specialty / QA approach) because there is no dedicated public HTTP create for those kinds in this stage; display direction still verified through live `GET /api/inventory/logs` + painted cells.
4. Checklist card in `01-acceptance-checklist.md` not updated by this UAT (scheduler ownership).

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 121522, http.server 122076) |
| Port 15200 free | true |
| Port 15201 free | true |
| pid alive after stop | false |
| Orphans | none |
| 15010 | not present; untouched |
| cleanup.json | `docs/handoff/artifacts/uat007-uat/environment/cleanup.json` |

No git commit / push. No checklist edit. No secrets copied into working tree beyond stage-local env.

## Scheduler signal

- **UAT gate: PASS**
- Evidence: `docs/handoff/artifacts/uat007-uat-report.md`
- Supporting dir: `docs/handoff/artifacts/uat007-uat/`
- Next: scheduler may mark UAT-007 card closed and proceed W3 remaining items.
