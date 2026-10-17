# UAT-027 Development Report

> Generated: 2026-10-17 05:39 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-027] 库存价格金额仍使用美元符号且缺千分位 — P2 PRODUCT_FAULT
> Deps: UAT-021 DONE PASS (satisfied)
> Git: no commit / no push (per dispatch)

## Summary

Inventory price surfaces still rendered bare `$` + `toFixed(2)` (no thousands
separator). `I18N.fmtMoney` already produced `HK$1,234.50` via Intl; this DEV
pass routes inventory list / detail / ledger / AI cards / price-history chart
metrics through `I18N.fmtMoney` or `formatHkd` (wrapper). Dish BOM and finance
HKD paths already used `I18N.fmtMoney`; foreign originals keep
`formatCurrency(amount, currency)` and are not forced to HKD. FX math untouched.
No independent QA/UAT — card stays `DEV_DELIVERED`, not PASS.

## Acceptance mapping (dev self-check)

| AC | Dev evidence |
|---|---|
| 1. synthetic 1234.5 → `HK$1,234.50`; 0 → `HK$0.00`; null → `—` | `I18N.fmtMoney` + harness Hans/Hant |
| 2. inventory list / chart metrics / history / AI cards Hans+Hant | inventory_board.js uses fmtMoney/formatHkd; harness static + locale assertions |
| 3. foreign currency not forced HKD | finance keeps `formatCurrency(..., r.currency)`; harness SGD not HK$-prefixed |
| Negative: do not change FX math; mask commercial values in prose | no FX/service edits; fixture uses synthetic 1234.5 only |

## Before

- `inventory_board.js` list/detail: `` `$${Number(...).toFixed(2)}` ``
- `formatHkd`: `return '$' + Number(price).toFixed(2)` (used by price-history metrics/overview)
- Chart axis / avg / threshold / dot titles: bare `$`
- AI lean summary/cards: `HK$ ${n.toFixed(2)}` / `` `$${price}` `` (no grouping)
- Ledger amount cell: `amt.toFixed(2)` or `-` (no HK$ / grouping / em-dash)

## After (files touched)

### `demo/static/js/inventory_board.js` (primary)

- List + detail unit price → `I18N.fmtMoney`
- Ledger amount → `I18N.fmtMoney(amt)` or `—`
- AI summary + cards → `I18N.fmtMoney`
- Chart labels / titles → `formatHkd`
- History table unit price → `formatHkd`
- `formatHkd`: delegate to `I18N.fmtMoney(price, 'HKD')`; null/''/non-finite → `—`; offline fallback `HK$` + grouping

### `demo/static/js/dish_bom.js`

- No code change (already `I18N.fmtMoney` + `—` for missing price/cost/profit)

### `demo/static/js/finance.js`

- No code change (HKD via `I18N.fmtMoney`; foreign original via `formatCurrency`; `currency !== 'HKD'` branch retained; no FX edits)

### `demo/static/js/i18n/i18n.js`

- No code change (`fmtMoney` already Intl HKD → `HK$1,234.50`, null → `—`)

## Commands run

```text
node --check demo/static/js/inventory_board.js
node --check demo/static/js/dish_bom.js
node --check demo/static/js/finance.js
node --check demo/static/js/i18n/i18n.js
# JS_OK

node demo/tests/frontend_uat027_money_format_harness.js
# 29 PASS / 0 FAIL

node demo/tests/frontend_i18n_converter_harness.js
# existing i18n harness still green (includes fmtMoney 1234.5)
```

Logs: `docs/handoff/artifacts/uat027-dev/logs/harness-uat027.txt`,
`docs/handoff/artifacts/uat027-dev/logs/harness-uat027.json`

## Negative / non-goals

- Did **not** alter FX rates, snapshot conversion, or payable outstanding math.
- Did **not** force HKD symbol on foreign `formatCurrency` originals.
- Did **not** run independent QA or UAT; did **not** flip card to DONE PASS.
- Did **not** git commit or push.

## Next gate (for coordinator)

Independent QA on fresh port/DB: owner fixture with synthetic last_unit_price
1234.5; assert painted inventory list / price-history metrics+chart / history
table / AI cards show `HK$1,234.50` (or equiv) under zh-Hans and zh-Hant-HK;
0 → `HK$0.00`; null/staff-masked → `—`; SGD/foreign receipt original not
HKD-forced. Then independent UAT (MANUAL_UI). Only after both PASS: flip
`[UAT-027]` to DONE PASS.
