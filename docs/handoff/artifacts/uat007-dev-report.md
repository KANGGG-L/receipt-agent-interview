# UAT-007 Development Report

> Generated: 2026-10-17 04:08 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-007] 消耗台账数量方向显示为正数 — P1 PRODUCT_FAULT
> Vendor aliases in fixtures: Vendor_A / Vendor_B

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Fix applied without double-deducting stock | PASS |
| Specialty tests (5) | PASS |
| Related ledger API suite | PASS (12) |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.

## Root cause (printed citations)

1. DB / write path stores outbound magnitude as positive:
   - `demo/app/api_inventory.py:626-639` (`_deduct_stock`) writes `qty=body.quantity` with `kind=consume|waste`.
   - `demo/app/db.py:3493-3494` (`apply_stock_log`) subtracts that positive magnitude from `current_stock`.
   - Dish path same pattern: `demo/app/api_dishes.py:1492-1506` (`kind="consume"`, positive `qty`).
2. Ledger UI blindly prefixed `+` for any positive `qty`:
   - Pre-fix (removed): `const qtyText = (qty > 0 ? '+' : '') + qty;` inside `loadInventoryLedger`.
   - Effect: consume 1kg rendered as `+1` while stock correctly became 9.
3. Already-signed kinds were not the bug:
   - `stocktake` stores signed diff (`api_inventory.py` stocktake path `qty=diff`).
   - Receipt `reversal` stores negative qty (`demo/app/services/inventory.py:376-378`).
   - Dish void writes `kind="adjust"` with positive restore qty (`api_dishes.py:1658-1671`).

Negative boundary respected: **no change** to `_deduct_stock` / `apply_stock_log` stock arithmetic (would risk double-deduct if consume qty were negated in DB).

## Fix

### API — `demo/app/api_inventory.py`

- `740:742` — `_OUTBOUND_LOG_KINDS` / `_SIGNED_LOG_KINDS` constants.
- `745:762` — `ledger_qty_signed(kind, qty)` read-only derivation:
  - `consume` / `waste` → `-abs(qty)`
  - `stocktake` / `reversal` → pass-through (already signed)
  - else (`in`, `adjust`, …) → stored value
- `824:832` — `GET /api/inventory/logs` each row now includes:
  - `qty`: DB original (unchanged)
  - `qty_signed`: display direction value
  - `id`: same primary key as DB row (DB/API/UI alignment)

### UI — `demo/static/js/inventory_board.js`

- `635:652` — `invLedgerQtySigned` / `invLedgerQtyText` prefer `r.qty_signed`, with kind-based fallback for older responses.
- `703:719` — ledger row renders `invLedgerQtyText(r)` instead of blind `+` on raw `qty`.
- `invLedgerKindLabel` map kept exactly aligned with `api_export` `kind_map` (in/consume/waste/stocktake only) so existing label-equality regression stays green.

### Tests — `demo/tests/test_uat_007_ledger_qty_sign.py` (new)

1. `test_ledger_qty_signed_helper_unit` — unit table for kinds.
2. `test_consume_inbound_signed_stock_and_id_alignment` — synthesize 10kg → consume 1 → `qty_signed=-1`, stock 9; inbound 1 → `qty_signed=+1`, stock 10; DB row id == API `id`.
3. `test_reversal_and_adjust_opposite_direction` — `reversal` negative / `adjust` positive.
4. `test_cross_tenant_logs_invisible_and_stock_machine_unchanged` — Tenant B baseline unchanged; cross-tenant consume 404.
5. `test_ui_uses_qty_signed_not_blind_plus` — static assert old `qtyText` pattern gone.

## Commands actually run

```text
node --check demo/static/js/inventory_board.js
python3 -m py_compile demo/app/api_inventory.py
.../uat003-qa/environment/venv/bin/pytest demo/tests/test_uat_007_ledger_qty_sign.py -v
# 5 passed

.../venv/bin/pytest tests/test_inventory_ledger_api.py demo/tests/test_uat_007_ledger_qty_sign.py -q
# 12 passed

.../venv/bin/pytest demo/tests/test_b2_sku_inventory.py::test_owner_can_confirm_overdraw_with_reason_and_it_is_recorded \
  demo/tests/test_b2_sku_inventory.py::test_staff_cannot_consume_and_nan_rejected -q
# 2 passed
```

Note: broader `test_b2_sku_inventory.py` containment / FX cases still fail in this environment; they are outside UAT-007 write scope and predate this change (consume-path cases above remain green).

## Acceptance mapping (dev evidence only)

| AC | Dev evidence | Status |
|---|---|---|
| 1. 10kg then consume 1 → ledger -1, stock 9 | specialty test #2 | PASS (API+DB) |
| 2. inbound 1 → +1, stock 10 | specialty test #2 | PASS (API+DB) |
| 3. reverse opposite direction | specialty test #3 (reversal / adjust) | PASS (API) |
| 4. DB/API/UI same event id | specialty test #2 id match; UI binds same row fields | PASS (DB/API; UI static) |
| Negative: no double-deduct / state machine | write paths untouched; overdraw/consume tests green | PASS |
| Negative: cross-tenant reject/invisible | specialty test #4 | PASS |

Browser MANUAL_UI (isolation port + Orca) is **reserved for independent UAT** — not claimed here.

## Files modified

| Path | Change |
|---|---|
| `demo/app/api_inventory.py` | `ledger_qty_signed` + `qty_signed` on logs |
| `demo/static/js/inventory_board.js` | signed qty display helpers |
| `demo/tests/test_uat_007_ledger_qty_sign.py` | new specialty tests |
| `docs/handoff/01-acceptance-checklist.md` | **UAT-003 only** closed to `[DONE] PASS` (not UAT-007) |
| `docs/handoff/02-coordinator-ledger.md` | UAT-003 closed; next W3 = UAT-007 |
| `docs/handoff/artifacts/uat007-dev-report.md` | this report |

No git commit. No git push. No independent QA/UAT dispatched.

## Scheduler handoff

- UAT-003 docs closed.
- UAT-007 **dev complete**; please dispatch **independent QA** next (fresh isolation port, do not reuse this report as PASS).
- After QA PASS, dispatch independent UAT (MANUAL_UI).
- Only then flip `[UAT-007]` checklist to `[DONE] PASS`.
