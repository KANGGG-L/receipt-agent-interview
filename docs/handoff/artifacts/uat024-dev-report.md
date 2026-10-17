# UAT-024 Development Report

> Generated: 2026-10-17 04:49 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-024] 手机库存品名列过窄且操作与名称分离 — P2 QUALITY_RISK
> Deps: UAT-003 DONE PASS (satisfied)
> Vendors in fixtures / prose: Vendor_A / Vendor_B only

## Summary

On viewports 390/412 the inventory SKU table (`#inventoryTable`) kept
`min-width: 640px` (`style.css:2903-2905`) while `.inv-name-text` used
`word-break: break-all`. The name column was squeezed so Rice / Oil / long
Chinese names became hard to read (short Latin names stacked letter-by-letter),
and the actions column scrolled away from the name. Document-level horizontal
overflow followed the forced table min-width.

Fix (dev only): stamp row `data-sku-id` + per-cell `data-label` / name
`title`+`data-full-name`; switch name wrapping to `break-word`; convert the
inventory table to a card grid under `@media (max-width: 640px)` in
`review-mobile.css` so name and actions share the same card; keep desktop
`min-width: 640px` for 1440. No QA/UAT in this stage.

## Before

- `#tab-inventory table.data-table { min-width: 640px; }` forced a wide table
  on phone; name and actions lived in separate columns that separated under
  horizontal scroll.
- `.inv-name-text` / `#tab-inventory .inv-name-text` used `word-break: break-all`,
  so short names like `Rice` / `Oil` could render vertical letter-by-letter in a
  narrow column.
- Row markup had no `data-label` / `data-sku-id`, so a receipt-style mobile card
  layout could not label cells or prove More hits the same-row SKU from DOM.
- `#inventoryTable` lacked a stable marker class for mobile overrides.

## After

### JS (`demo/static/js/inventory_board.js:487-516`)

- `tr.dataset.skuId = String(Number(sku.id))`
- Name cell: `data-label="品名"`, `title`, `data-full-name` (escaped business name)
- Metric / category cells: matching `data-label` values
- Actions: `td.inv-actions-cell data-label="操作"` + `.inv-actions data-sku-id`
- More button still `toggleInventoryMoreMenu(this, ${Number(sku.id)})` — same
  row SKU id path unchanged

### HTML (`demo/templates/index.html:721`)

- `#inventoryTable` gains class `inventory-sku-table` for mobile targeting

### CSS desktop (`demo/static/css/style.css`)

- `.inv-name-text` and `#tab-inventory .inv-name-text`: `word-break: break-word`
  + `overflow-wrap: anywhere` (self-checked: no `word-break: break-all` in those
  rule bodies)
- `min-width: 640px` retained for wide viewports (1440 path)

### CSS mobile (`demo/static/css/review-mobile.css:183-282`)

Inside `@media (max-width: 640px)` only:

- `#inventoryTable` → `display:block`, `min-width: 0 !important`
- thead/colgroup hidden; tbody `tr` → card `display:grid`
- 品名 + 操作 `grid-column: 1 / -1` (name identifiable while actions visible)
- name wrapping `break-word` (not `break-all`)
- action buttons `min-height: 44px`, nowrap
- table-container `:has(#inventoryTable)` overflow-x visible / max-width 100%
  (document horizontal overflow avoided; local scroll not invented as a contract)

1440 is outside this media query — desktop table layout unchanged.

## Tests run (dev self-check; not independent QA)

| Command | Result |
|---|---|
| `python3` inline runner on `demo/tests/test_uat_024_mobile_inventory_name_actions.py` | 5/5 PASS |
| `node demo/tests/frontend_uat024_inventory_mobile_harness.js` | 14 PASS / 0 FAIL |
| `node --check demo/static/js/inventory_board.js` | exit 0 |

pytest module not installed in this box env; equivalent assert runner used.
MANUAL_UI (390/412 live More click, document scrollWidth, 1440 visual) left for
independent QA/UAT.

## Files touched

- `demo/static/js/inventory_board.js`
- `demo/static/css/style.css`
- `demo/static/css/review-mobile.css`
- `demo/templates/index.html`
- `demo/tests/test_uat_024_mobile_inventory_name_actions.py` (new)
- `demo/tests/frontend_uat024_inventory_mobile_harness.js` (new)
- `docs/handoff/artifacts/uat024-dev-report.md` (this file)

## Explicit non-claims

- Independent QA not run
- Independent UAT not run
- Card must not be marked DONE PASS until QA + UAT gates pass
- No git commit / push
- Cross-tenant / role boundaries unchanged (display/CSS/markup only)

## Next gate

Independent QA on fresh isolate port: viewport 390 and 412 with Rice / Oil /
long ZH fixtures; real More click resolves same-row `data-sku-id`; name visible
with actions; short names not letter-stacked; `document` no horizontal overflow;
1440 regression check.
