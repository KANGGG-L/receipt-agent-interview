# UAT-021 Development Report

> Generated: 2026-10-17 05:35 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-021] 店员库存列表与餐品列表泄露价格及毛利 — P1 PRODUCT_FAULT
> Deps: none
> Git: no commit / no push (per dispatch)

## Summary

Staff callers on inventory/dishes list + detail must not receive
`last_unit_price` / `price` / `gross_profit` / `gross_margin_rate` (and synonym
cost fields); qty/unit remain visible; owner same fixture still sees prices;
forged `X-Role: owner` with a real staff cookie must not elevate.

**Baseline on HEAD:** API redaction for `/api/inventory`, `/api/inventory/skus/{id}`,
`/api/dishes`, `/api/dishes/{id}`, daily consumption cost fields, and inventory
logs `amount` was already present (commit `364b407`), with UI owner gates in
`inventory_board.js` / `dish_bom.js` and specialty tests in
`demo/tests/test_uat_021_price_margin_masking.py`.

**This DEV pass** verified AC against that baseline, closed one synonym leak
(dish version snapshot still returned `snapshot.dish.price` to staff), and
added list-column defense-in-depth. No QA/UAT in this stage — card stays
`DEV_DELIVERED`, not PASS.

## Acceptance mapping (dev self-check)

| AC | Dev evidence |
|---|---|
| 1. staff list JSON: price fields null/omitted | Existing list handlers + pytest AC1 |
| 2. staff detail still null | SKU detail + dish detail already null; **new** version snapshot redaction |
| 3. owner same fixture sees prices | Existing owner tests + new owner version test |
| 4. fake owner header cannot bypass | Cookie staff + `X-Role: owner` tests (list/detail/version) |
| Negative: qty/unit visible; no nav-hide-as-auth; keep detail redaction | stock/unit assertions; identity from `request.state.account` / token only |

## Before (gap found this pass)

- `GET /api/dishes/{id}/versions/{version}` allowed `require_role("staff")` and
  returned raw `db.get_dish_recipe_version` payload including
  `snapshot.dish.price` — synonym leak vs list/detail redaction.
- Inventory table price cell used truthy `sku.last_unit_price` only (API null
  already masked staff; no extra `isOwnerRoleNow` gate).

## After (files touched)

### `demo/app/api_dishes.py`

- Added `_redact_dish_version_payload` (deep copy; nulls price/cost/margin
  synonyms on `snapshot.dish`, ingredients, expanded rows).
- `get_dish_version`: resolve `can_see_prices` from authenticated account;
  redact when staff.

### `demo/static/js/inventory_board.js`

- List column「最新进货单价」: require `isOwnerRoleNow()` **and** finite
  `last_unit_price`; else `—`. Comment notes API remains source of truth.

### `demo/static/js/dish_bom.js`

- No code change this pass (already gates price/cost/margin on
  `isOwnerRoleNow()`; relies on API nulls).

### `demo/app/api_inventory.py`

- No code change this pass (list/detail/logs already null sensitive fields for
  staff via `can_see_prices`).

### `demo/tests/test_uat_021_price_margin_masking.py`

- Extended with version snapshot staff mask, owner visibility, and fake-header
  bypass cases (10 tests total).

## Commands run

```text
node --check demo/static/js/inventory_board.js
node --check demo/static/js/dish_bom.js
# JS_OK

cd demo && <venv>/bin/python -m pytest tests/test_uat_021_price_margin_masking.py -q --tb=line
# 10 passed
```

Log: `docs/handoff/artifacts/uat021-dev/logs/pytest-uat021.txt`

## Negative / non-goals

- Did **not** hide tabs/nav as a substitute for API auth.
- Did **not** weaken already-correct detail redaction on list/SKU/dish GET.
- Did **not** run independent QA or UAT; did **not** flip card to DONE PASS.
- Did **not** git commit or push.

## Next gate (for coordinator)

Independent QA on fresh port/DB: real staff cookie vs owner fixture; full JSON
on inventory + dishes list/detail + version snapshot; forged owner header;
qty/unit retained; tenant B baseline. Then independent UAT (MANUAL_UI).
Only after both PASS: flip `[UAT-021]` to DONE PASS and **unblock UAT-027**.
