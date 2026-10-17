# SAAS-CONSISTENCY Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [SAAS-CONSISTENCY] 页面容器/工具条/日期标签视觉体系差异 — P2 DESIGN_OBSERVATION
> Git: no commit / no push (per dispatch)

## Summary

Unified page-shell alignment for 今日 / 库存 / 归档 / 设置 at 1440 by removing the 980px `.st-wrap` cap and binding layout to shared CSS tokens. Fixed report date labels (`月份：` / `起止日期：`) so they stay single-line (`white-space: nowrap`, `word-break: keep-all`) and reflowed the report card-title toolbar so filter and action clusters no longer overlap. 390 viewports keep full-width shells without document overflow. **No new brand/semantic color tokens were added**; unapproved palette expansion is explicitly **not** marked complete.

**SAAS-CONSISTENCY DEV self-check: PASS.** Card remains `DEV_DELIVERED` until independent QA/UAT.

## Acceptance mapping (dev self-check)

| AC | Dev evidence |
|---|---|
| 1. 1440 今日/库存/归档/设置同类容器对齐规则一致 | Visible card left edge all **264px** inside `.main-container` (sidebar 240 + pad 24); tab width **1148**; `st-wrap` max-width **100%** (was 980px) |
| 2. 报表日期 label 不逐字两行 | Labels `月份：` / `起止日期：` height 20px, `white-space:nowrap`, `letterStackedSuspect=false`; shot `ui/1440-report-date-labels.png` |
| 3. 390 不退化 | `390_no_doc_overflow` true for today/inventory/settings; shots `ui/390-*.png` |
| 4. 列出采用 token 及视觉证据；未获批准语义/品牌扩展不标完成 | Token table below; evidence under `docs/handoff/artifacts/saas-consistency-dev/`; **no new brand/semantic hues** |

## Tokens adopted (layout only)

| Token | Value | Used for |
|---|---|---|
| `--page-max-width` | `1440px` | `.main-container` max width |
| `--page-inline-pad` | `24px` | `.main-container` horizontal padding |
| `--page-block-gap` | `16px` | card / today-grid / head spacing |
| `--page-card-pad` | `24px` | `.card` padding |
| `--toolbar-gap` | `12px` | `.inv-toolbar` / `.saas-toolbar` / report title gaps |
| `--toolbar-cluster-gap` | `8px` | toolbar filter/action clusters |
| `--toolbar-divider` | `var(--border-color)` | toolbar bottom/top borders |

Reused existing surface tokens (not expanded): `--bg-main`, `--bg-card`, `--border-color`, `--radius-card`, `--shadow`, `--text-muted`.

**Not marked complete:** any new brand primary/accent/semantic color system beyond existing `:root` hues.

## Before

- `#tab-today` / `#tab-settings` used `.st-wrap { max-width: 980px; margin: 0 auto }` (`f1b.css`), so at 1440 content was narrower/centered differently than inventory/archive cards that fill `.main-container` (max 1440).
- Report date labels in the card-title flex cluster could wrap character-by-character when the title row squeezed; filter chip and action cluster could overlap horizontally.
- Layout lengths were mostly hardcoded (`1440px`, `24px`, `12px`, `8px`) instead of shared page/toolbar tokens.

## After (files touched)

### `demo/static/css/style.css`

- Added layout tokens under `:root`.
- `.main-container` / `.card` / `.inv-toolbar` (+ `.saas-toolbar` alias) consume tokens.
- SAAS-CONSISTENCY block: four target tabs full-width; `#tab-today|#tab-settings > .st-wrap` override; report label nowrap/keep-all; report title toolbar full-width row so filters and actions do not overlap.

### `demo/static/css/f1b.css` (adjacent; required)

- `.st-wrap` source changed from `max-width: 980px` to `max-width: 100%` so load-order readers are not confused by a conflicting cap.

### `demo/static/css/review-mobile.css`

- 390 reinforcements: full-width shells, single-line date labels, card spacing.

### `demo/templates/index.html`

- Cache-bust CSS query to `20261017_saas_consistency`.
- `page-shell` on today/inventory/archive/settings.
- `report-date-label` + `report-date-cluster` on report month/range controls.

## Visual evidence

| Shot | Path |
|---|---|
| 1440 today | `docs/handoff/artifacts/saas-consistency-dev/ui/1440-today.png` |
| 1440 inventory | `docs/handoff/artifacts/saas-consistency-dev/ui/1440-inventory.png` |
| 1440 archive | `docs/handoff/artifacts/saas-consistency-dev/ui/1440-archive.png` |
| 1440 settings | `docs/handoff/artifacts/saas-consistency-dev/ui/1440-settings.png` |
| 1440 report date labels | `docs/handoff/artifacts/saas-consistency-dev/ui/1440-report-date-labels.png` |
| 390 today / inventory / settings | `docs/handoff/artifacts/saas-consistency-dev/ui/390-*.png` |
| Metrics JSON | `docs/handoff/artifacts/saas-consistency-dev/logs/capture-result.json` |
| Report toolbar remeasure | `docs/handoff/artifacts/saas-consistency-dev/logs/report-toolbar-remeasure.json` |

Isolation: port **15410** (15010 untouched); DB `environment/dev_isolated.db`; cleanup `environment/cleanup.json`.

## Negative / out of scope

- Did not invent brand or semantic color expansions.
- Did not flip checklist to PASS; no QA/UAT.
- Did not git commit or push.
- Inventory table empty-state content is fixture data, not a layout defect for this card.

## Next

Dispatch independent QA (fresh port ≠ 15410; avoid 15150–15410 if prior wave reserved), then independent UAT.
