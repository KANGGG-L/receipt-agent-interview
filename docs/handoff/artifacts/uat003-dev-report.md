# UAT-003 Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT-003] 库存流水品名截断到相同前缀
> Vendors in fixtures / prose: Vendor_A / Vendor_B only

## Summary

Inventory ledger name cells under `#tab-inventory` inherited
`#tab-inventory table.data-table td:nth-child(3) { white-space: nowrap; }`
(`demo/static/css/style.css:2902-2909`). With nine ledger columns, the
品名 column was squeezed so shared-prefix names such as `Synthetic_Rice`
and `Synthetic_Oil` visually collapsed to the same prefix. The renderer
also emitted a bare `<td>` with no `title` / `data-*` fallback, so the
full original value was not recoverable from the cell.

Fix (dev only): stamp full escaped business name into visible text plus
`title` and `data-full-name`, and add a higher-specificity CSS override
for `#inventoryLedgerTable` so the name column wraps instead of nowrap
clipping. Primary label remains `r.name` (not `sku_id`). Cross-tenant
API behavior unchanged.

## Before

- `demo/static/js/inventory_board.js` (pre-fix) rendered ledger 品名 as a
  plain cell with escaped name only, e.g. conceptually:
  `<td>${w2Escape(r.name || '')}</td>` inside `loadInventoryLedger`
  (same forEach block that is now `inventory_board.js:679-706`).
- `demo/static/css/style.css:2902-2909` forced `white-space: nowrap` on
  `#tab-inventory` table columns 2-6. Ledger 品名 is column 3
  (`index.html` thead: 日期 / 类型 / **品名** / ...), so both
  `Synthetic_Rice` and `Synthetic_Oil` lost distinguishing suffixes in
  the narrow cell.
- No `title` / `data-full-name` on the ledger name cell; long Chinese
  names and underscore suffixes had no DOM-accessible full-value path
  from the cell itself.
- API `GET /api/inventory/logs` already returned full `name`
  (`demo/app/api_inventory.py:798-804`); defect was display-layer.

## After

### JS (`demo/static/js/inventory_board.js:688-697`)

```688:697:demo/static/js/inventory_board.js
                // UAT-003：品名必须保留完整业务原值（含下划线后缀与长中文），不得被
                // #tab-inventory 表格的 nowrap/窄列裁成相同前缀；主展示仍用 name，
                // 不以 sku_id 替代。title + data-full-name 保证原值可获取。
                const rawName = (r.name == null) ? '' : String(r.name);
                const nameEsc = w2Escape(rawName);
                const nameCell = `<td class="inv-ledger-name-cell" title="${nameEsc}" data-full-name="${nameEsc}"><span class="inv-ledger-name-text">${nameEsc || '-'}</span></td>`;
                tr.innerHTML = `
                    <td>${w2Escape(r.date || '')}</td>
                    <td><span class="badge badge-secondary">${w2Escape(invLedgerKindLabel(r.kind))}</span></td>
                    ${nameCell}
```

- Visible text = full business name (Rice vs Oil remain in the string).
- `title` + `data-full-name` = same escaped original (underscore + long ZH).
- Does not substitute `sku_id` for the primary label.

### CSS (`demo/static/css/style.css:2931-2953`)

```2931:2953:demo/static/css/style.css
/* UAT-003：库存流水台账品名列 —— 覆盖 #tab-inventory td:nth-child(3) 的
   white-space:nowrap，避免 Synthetic_Rice / Synthetic_Oil 被窄列裁成相同前缀。
   选择器双 ID 提高优先级；原值经 title / data-full-name 可获取，主文案仍为业务名。 */
#tab-inventory #inventoryLedgerTable td.inv-ledger-name-cell {
    height: auto;
    min-height: 66px;
    overflow: visible;
    white-space: normal;
    word-break: break-word;
    overflow-wrap: anywhere;
    text-overflow: clip;
    max-width: 16rem;
    text-align: left;
    vertical-align: middle;
}
#tab-inventory #inventoryLedgerTable .inv-ledger-name-text {
    display: inline-block;
    max-width: 100%;
    white-space: normal;
    word-break: break-word;
    overflow-wrap: anywhere;
    line-height: 1.35;
}
```

Dual-ID selector beats `#tab-inventory table.data-table td:nth-child(3)`
specificity so wrap wins without relaxing other inventory columns.

### Tests (`tests/test_inventory_ledger_api.py`)

- `test_uat003_api_keeps_distinct_full_names_with_shared_prefix` — seeds
  `Synthetic_Rice` / `Synthetic_Oil` / long ZH underscore name via
  Vendor_A / Vendor_B; asserts logs API returns full distinct names.
- `test_uat003_ui_ledger_name_cell_exposes_full_original` — asserts JS
  stamps `inv-ledger-name-cell` + `data-full-name` + `title`, and CSS
  override sets `white-space: normal` / `overflow: visible`.

Command (dev self-check, not independent QA):

```bash
docs/handoff/artifacts/uat002-qa/environment/venv/bin/pytest \
  tests/test_inventory_ledger_api.py -v --tb=short
```

Result: **7 passed** (including the two new UAT-003 cases).
Also: `node --check demo/static/js/inventory_board.js` exit 0.

## Files touched

| Path | Change |
|---|---|
| `demo/static/js/inventory_board.js` | Ledger name cell: class + title + data-full-name |
| `demo/static/css/style.css` | `#inventoryLedgerTable` name-column wrap override |
| `tests/test_inventory_ledger_api.py` | Two UAT-003 automated checks |
| `docs/handoff/artifacts/uat003-dev-report.md` | This report |

No git commit / push in this stage (per scheduler instruction). Working tree left dirty.

## Residual risks (for independent QA / UAT)

1. **Runtime visual proof still required**: automated checks cover API
   full names + static source/CSS. Independent MANUAL_UI must still
   render `Synthetic_Rice` vs `Synthetic_Oil` in a real browser on an
   isolated 151xx port and confirm Rice/Oil are distinguishable in the
   painted cell (not only in `data-full-name`), plus hover/title for
   long Chinese names.
2. **max-width: 16rem**: wrap keeps suffixes visible for the card
   fixtures; extremely long names still wrap across lines — full value
   remains in `title` / `data-full-name`. If UAT judges 16rem too
   narrow on 390px, widen or drop max-width without restoring nowrap.
3. **Sibling inventory SKU table**: name column there already uses
   `.inv-name-cell` / wrap; this fix is scoped to
   `#inventoryLedgerTable` only. UAT-024 (mobile name + actions) is a
   separate card and depends on UAT-003.
4. **Cross-tenant**: no API auth / scoped-query changes; QA should still
   reconfirm tenant B baseline zero drift as card negative boundary.
5. **Independent QA/UAT not started** in this stage — card remains
   open until those gates PASS.

## Gate status

- Development: delivered (this report)
- Independent QA: NOT RUN
- Independent UAT: NOT RUN
