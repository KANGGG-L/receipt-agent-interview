# UAT-035 Development Report

> Generated: 2026-10-17 06:49 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run)
> Card: [UAT-035] 每日消耗数量及导出筛选仍有三个无可访问名称控件 — P2 PRODUCT_FAULT MANUAL_UI
> Git: no commit / no push (per dispatch)

## Overall (dev gate only)

| Item | Verdict |
|---|---|
| Root cause identified | PASS |
| Fix applied (aria-label on qty spinbutton + two BOM filter comboboxes) | PASS |
| Isolated port 15185 AXTree Hans/Hant x 1440/390 | PASS |
| Keyboard filter/input + daily consume submit | PASS |
| Existing named controls (date label-for) not regress | PASS |
| Tenant B baseline unchanged (curl empty dishes) | PASS |
| Independent QA / UAT | NOT_RUN (per scheduler brief) |

**Dev delivery: ready for independent QA.** Do not treat this report as card PASS.
Checklist: `[TODO] DEV_DELIVERED` only (not DONE).

## Root cause

1. Daily consumption qty inputs (`#dishConsumeQty_*`, `type="number"` → AX `spinbutton`) are rendered in `renderDishConsumeEntryTable` without `aria-label` / `aria-labelledby` / associated `<label>`. They stay nameless after `DOMContentLoaded` `ensureAccessibleNames` (dynamic rows).
2. BOM library filter selects `#dishCategoryFilter` / `#dishStatusFilter` (AX `combobox`) in `index.html` had no explicit accessible name. Runtime `ensureAccessibleNames` either used `ACCESSIBLE_NAME_FALLBACK` (category) or nearby previous-select option text (status → wrong name 「全部分类」). Card negative: do not count placeholder/title as AX name; status name did not describe the field.
3. `#dishConsumeSearchInput` previously relied on placeholder→aria copy; added explicit `aria-label` so search name is not placeholder-only.

Scope (card source files): `demo/static/js/dish_consumption.js`, `demo/templates/index.html`.
Note: data-center `#exportDynamicFilters` selects still lack linked names until `export_center.js` associates labels (out of listed source files; not claimed fixed).

## Fix

### `demo/static/js/dish_consumption.js`

- Qty spinbutton: `aria-label="' + w2Escape(d.name) + ' 当日消耗份数"`.
- Step buttons (±1 / +5 / +10): descriptive `aria-label` including dish name (keyboard/SR clarity; not counted as the three targets).

### `demo/templates/index.html`

- `#dishConsumeSearchInput` → `aria-label="搜索在售餐品"`.
- `#dishCategoryFilter` → `aria-label="餐品分类筛选"`.
- `#dishStatusFilter` → `aria-label="餐品在售状态筛选"` (replaces wrong 「全部分类」).
- Cache bust: `dish_consumption.js?v=20261017_uat035`.

## Repro / self-check (isolated port; 15010 kept free)

1. Temp DB + `AUTH_ENABLED=0` uvicorn on **15185** (avoided 15010 / 15155 / 15165 / 15175 / 15130 / 15140 / 15450).
2. Seed owner dish `测试餐品A` on tenant `uat035dev`.
3. Before fix: AXTree daily view → nameless `spinbutton` (qty); status combobox name wrongly 「全部分类」 when only fallback/nearby logic applied.
4. After fix (Puppeteer, owner Hans 1440 + Hant 390):
   - spinbutton name: `测试餐品A 当日消耗份数` / `測試餐品A 當日消耗份數`
   - comboboxes: `餐品分类筛选` / `餐品在售状态筛选` (Hant translated)
   - Keyboard: type qty `2`; select category `主食` / status `all`
   - Submit: POST `/api/dishes/daily_consumption/batch` 200, recorded qty 1
5. Tenant B `GET /api/dishes` → `data: []` (curl; Puppeteer extra-headers merge can false-positive).
6. Cleanup: deactivate dish; stop uvicorn; port 15185 free.

Evidence: `docs/handoff/artifacts/uat035-dev/logs/axtree_before_summary.json`, `axtree_after.json`, `keyboard_submit.json`, `tenant_b_dishes.json`, `ui/hans_1440_daily.png`, `ui/hant_390_daily.png`.

## Self-check mapping

| AC | Evidence | Verdict |
|---|---|---|
| 1. owner Hans/Hant x 1440/390 AXTree non-ignored target spinbutton + two comboboxes named and field-describing | axtree_after.json | PASS |
| 2. Real keyboard filter/submit | keyboard_submit.json + select in after script | PASS |
| 3. Existing named controls no regress | date `label[for=dishConsumptionDate]` still present; role combobox still named | PASS |
| Negative: not placeholder/title as sole name; no full-site WCAG claim | explicit aria-label on three targets | PASS |
| Tenant B baseline | tenant_b_dishes.json empty | PASS |

## Key diffs (UAT-035 only)

```
demo/static/js/dish_consumption.js
  renderDishConsumeEntryTable: aria-label on qty input + step buttons

demo/templates/index.html
  dishConsumeSearchInput aria-label="搜索在售餐品"
  dishCategoryFilter aria-label="餐品分类筛选"
  dishStatusFilter aria-label="餐品在售状态筛选"
  script ?v=20261017_uat035
```

## Checklist / ledger

- `01-acceptance-checklist.md`: UAT-035 → `[TODO] DEV_DELIVERED` only.
- Ledger not advanced past UAT-035; no DONE; no QA/UAT claimed.
