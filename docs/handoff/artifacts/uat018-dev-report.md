# UAT018 Development Report

> Generated: 2026-10-17 06:59 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT018] BOM极大用量允许保存且无采购餐品显示零成本100%毛利 — P2 QUALITY_RISK MANUAL_UI
> Git: no commit / no push (per dispatch)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Reject NaN / Infinity / negative / 0 BOM qty writes | PASS |
| Unpurchased cost/margin unknown-estimate markers (no definite 100%) | PASS |
| Purchased normal BOM keeps certain cost | PASS |
| Extreme finite qty: no silent clamp; open-risk recorded | PASS |
| Tenant B baseline unchanged | PASS |
| Specialty pytest (5) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

1. `_validate_recipe_payload` only checked `qty is None or float(qty) <= 0`. In Python, `NaN <= 0` and `Inf <= 0` are both False, so NaN/Infinity were accepted, persisted, and later blew JSON serialization (`Out of range float values are not JSON compliant`) into HTTP 500 on create/list.
2. Theory cost used `last_unit_price or 0.0` with no certainty flag. Unpurchased SKUs (`last_unit_price == 0`) produced `theoretical_cost = 0` and `gross_margin_rate = 100` as if certain.
3. Extreme finite qty (e.g. `999999999999`) had no product-adjudicated upper limit; silently clamping or claiming a B2-6 cap would violate the negative boundary.

## Fix

### `demo/app/api_dishes.py`

- `_validate_recipe_payload`: require `math.isfinite(qty) and qty > 0`; reject with `INVALID_CONSUMPTION_QTY` (no silent clamp of large finite values).
- `_dish_to_dict`: when any ingredient `cost_known is False`, set `theory_cost_status=estimated`, disclose `theory_cost_source`, keep `provisional_*` numbers, and **null** certain `theoretical_cost` / `gross_profit` / `gross_margin_rate` so clients cannot present definite 0 / 100%.
- Cost analysis variance uses provisional theory when certain theory is null.

### `demo/app/services/recipe_cost.py`

- Per SKU component: `cost_known` / `cost_source` (`last_unit_price` | `unpurchased_zero_price` | …).
- Sub-recipe: known only if every expanded leaf has purchase price.
- Read-path: non-finite legacy qty → warning + unknown cost (JSON-safe).

### `demo/static/js/dish_bom.js`

- Helpers `dishTheoryCostDisplay` / `dishTheoryMarginBadgeHtml` / `dishTheoryProfitDisplay`: estimated dishes show **未知/估算** (with source title), never bare `100.0%` / certain `$0`.

### `demo/static/js/dish_consumption.js`

- `saveDishModal`: `Number.isFinite` gate; reject NaN/Infinity/<=0 with explicit message.
- `calcDishModalTheoryCost`: unpurchased (`unitPrice <= 0`) shows 未知/估算, not definite margin %.

### `demo/app/dishes_store.py`

- Document open risk; constants:
  - `BOM_QTY_UPPER_LIMIT_STATUS = "open_risk_not_adjudicated"`
  - `BOM_QTY_UPPER_LIMIT_SOURCE = None` (explicitly **not** B2-6)

### `demo/templates/index.html`

- Cache bust: `dish_bom.js?v=20261017_uat018`, `dish_consumption.js?v=20261017_uat018`.

### Tests

- `demo/tests/test_uat_018_bom_qty_and_unpurchased_cost.py` (5 cases).

## Repro / self-check (isolated port; 15010 kept free)

1. Temp DB + `AUTH_ENABLED=0` uvicorn on **15215** (avoided 15010 / 15185 / 15195 / 15205 / 15155 / 15165 / 15175 / 15130 / 15140 / 15450).
2. Before fix: unpurchased SKU + BOM qty `999999999999` → save 200 with `theoretical_cost=0`, `gross_margin_rate=100`; NaN/Inf → 500.
3. After fix (API self-check, 17 assertions): all PASS — see `docs/handoff/artifacts/uat018-dev/logs/selfcheck_after.json`, `repro_before.json`.
4. Specialty pytest: 5 passed (`logs/pytest_uat018.log`).
5. Tenant B `GET /api/dishes` → empty both before and after.
6. Cleanup: stop uvicorn; port 15215 freed.

## Acceptance mapping (dev evidence only)

| AC | Dev evidence | Status |
|---|---|---|
| 1. Reproduce unpurchased + extreme 999999999999 | repro_before.json + after extreme save still allowed | PASS (repro) |
| 2. Reject NaN / Infinity / negative / 0 | 400 `INVALID_CONSUMPTION_QTY`; pytest | PASS |
| 3. Unpurchased cost/margin unknown-estimate; must not show definite 100% | `theory_cost_status=estimated`, certain fields null, UI 未知/估算 | PASS |
| 4. Purchased normal BOM keeps cost; extreme finite upper-limit risk open | cost 10 / margin 90; `BOM_QTY_UPPER_LIMIT_STATUS=open_risk_not_adjudicated`; qty persisted unclamped | PASS |
| Negative: no B2-6 cap claim; provisional disclosed; no silent clamp; Tenant B | source constants + theory_cost_source; tenant_b empty | PASS |

MANUAL_UI browser card/table/modal click-through is reserved for independent QA/UAT.

## Open risk (not closed)

Extreme **finite** BOM consumption_qty upper limit is **not adjudicated**. Product still allows `999999999999` to persist. Do not claim B2-6 defined a cap. Risk remains open until PM decides.

## Files modified (UAT018 scope)

| Path | Change |
|---|---|
| `demo/app/api_dishes.py` | finite qty reject; theory certainty fields |
| `demo/app/services/recipe_cost.py` | cost_known / cost_source; non-finite read guard |
| `demo/app/dishes_store.py` | open-risk constants + docstring |
| `demo/static/js/dish_bom.js` | estimate/unknown display helpers |
| `demo/static/js/dish_consumption.js` | save reject + modal estimate UI |
| `demo/templates/index.html` | JS cache bust |
| `demo/tests/test_uat_018_bom_qty_and_unpurchased_cost.py` | specialty pytest (5) |
| `docs/handoff/01-acceptance-checklist.md` | UAT018 → `[TODO] DEV_DELIVERED` only |
| `docs/handoff/02-coordinator-ledger.md` | DEV note; next atomic still UAT018 |
| `docs/handoff/artifacts/uat018-dev-report.md` | this report |

No git commit. No git push. No independent QA/UAT dispatched. Ledger not advanced past UAT018.

## Scheduler handoff

- Checklist: **[TODO] DEV_DELIVERED** (not DONE).
- Please dispatch **independent QA** next (fresh isolation port 15xxx, real browser BOM modal + card/table markers + API rejects).
- After QA PASS, dispatch independent UAT.
- Only then flip `[UAT018]` to `[DONE] PASS`.
- **UAT019** stays blocked on UAT018.
