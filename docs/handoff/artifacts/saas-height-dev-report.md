# SAAS-HEIGHT Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [SAAS-HEIGHT] 原38px控件合同未全面达标 — P2 DESIGN_GAP
> Git: no commit / no push (per dispatch)

## Summary

Implemented a shared `--control-min-height: 38px` contract and applied it to the known undersized interactive controls called out in S3-15 / SAAS-HEIGHT (archive chips, advanced filter toggle, role select, lang switch, dish subtabs, analytics segment / time-range buttons, parsing-log filters, engine card toggle, toast close, lean/fees toggles, sm buttons). Desktop **1440** and mobile **390** Puppeteer measurements show target control computed heights **>= 38px**. Keyboard focus works on sample native controls while visible; archive chips do not overlap; composite text contrast on the same controls remains **>= 4.5:1**.

**SAAS-HEIGHT DEV self-check: PASS.** Card remains `DEV_DELIVERED` until independent QA/UAT.

## Acceptance mapping (dev self-check)

| AC | Dev evidence |
|---|---|
| 1. 桌面/390 目标控件 computed height >= 38px | Table below; toast close 38x38; token `--control-min-height: 38px`; fails=0 in `logs/capture-result.json` |
| 2. keyboard 可用，无截断/重叠 | `logs/keyboard-contrast-verify.json`: role select / lang / chip / advanced toggle / time-range btn focusable; `archiveChipOverlap=false`; `docOverflowX=false` |
| 3. 原文字 4.5:1 抽样同控件仍满足 | Composite contrast: chip idle 16.05:1; lang active 16.37:1; dish inactive `#646464` on `#f3f2f1` **5.29:1**; dish active white on `#032425` 16.37:1 |

## Before

- Archive status/range `.chip` used `padding: 4px 12px` without min-height (~27px historically).
- `#archiveAdvancedToggle` was a text-sized span (~19px).
- `.demo-role-switch select` / `#filterTimeRange .btn` / `.parsing-log-filter-*` forced to 28–34px via `index-head.css !important`.
- `.engine-card-toggle-btn` height 28px; `.app-toast-close` 20x20; `.dish-subtab-btn` no min-height (~35px).

## After (files touched)

### `demo/static/css/style.css`

- `:root` token `--control-min-height: 38px`.
- SAAS-HEIGHT block: min-height contract for chips / toggles / selects / filters / toast close; overrides index-head `!important` 28/34px rules; preserves existing surface colors (no new brand/semantic hues).

### `demo/static/css/review-mobile.css`

- 390 media query reinforces the same 38px contract; chips `flex-shrink: 0` to avoid horizontal clip.

### `demo/templates/index.html`

- Cache-bust CSS query `20261017_saas_height` for `style.css` / `review-mobile.css` / `f1b.css`.

## Measured heights (min observed per label)

| Control | 1440 h (px) | 390 h (px) | Sample text |
|---|---:|---:|---|
| `analytics-segment-btn` | 42 | 42 | 02 金丝雀发布监控 |
| `archive-advanced-toggle` | 38 | 38 | 高级筛选 ▾ |
| `archive-chip` | 38 | 38 | 全部 |
| `demo-role-select` | 38 | - | ● 系统管理员 ● 餐厅老板 / 店长 ● 餐厅 |
| `dish-subtab-btn` | 38 | 38 | 每日消耗录入 |
| `engine-card-toggle-btn` | 38 | 38 | 收起 |
| `filter-time-range-btn` | 38 | 38 | 全部 |
| `lang-switch-btn` | 38 | - | 简体中文 |
| `parsing-log-filter-btn` | 38 | 38 | 查询/刷新 |
| `parsing-log-filter-control` | 38 | 38 |  |
| `app-toast-close` | 38 | (same CSS) | × |

All listed targets >= 38 on measured viewports (dev self-check).

## Keyboard / overlap / contrast (verify)

See `docs/handoff/artifacts/saas-height-dev/logs/keyboard-contrast-verify.json`.

- 1440: `#demoRoleSelect`, `.lang-switch-btn`, archive `.chip`, `#archiveAdvancedToggle` focused with visible outline; `#filterTimeRange .btn` focused after analytics nav.
- 390: chip + advanced toggle + time-range btn focused; role/lang may lose focus under mobile chrome timing but remain `canTab`/native focusable.
- Contrast samples (composite opaque ancestor bg): all checked pairs >= 4.5:1.

## Visual evidence

| Shot | Path |
|---|---|
| 1440 archive / dishes / analytics / engine | `docs/handoff/artifacts/saas-height-dev/ui/1440-*-controls.png` |
| 390 archive / dishes / analytics / engine | `docs/handoff/artifacts/saas-height-dev/ui/390-*-controls.png` |
| Metrics JSON | `docs/handoff/artifacts/saas-height-dev/logs/capture-result.json` |
| Keyboard+contrast verify | `docs/handoff/artifacts/saas-height-dev/logs/keyboard-contrast-verify.json` |

Isolation: port **15420** (15010 untouched); DB `environment/dev_isolated.db`; cleanup `environment/cleanup.json`.

## Negative / isolation

| Field | Value |
|---|---|
| Port | **15420**; **15010** reserved untouched |
| AUTH_ENABLED | 0 |
| Baselines | not deleted |
| Git | no commit / no push |
| QA / UAT | **not** run |

## Residual notes

1. Fees drawer toggle CSS is contracted; live measure requires an open receipt review session (not forced in this DEV capture).
2. Toast close was injected for hit-area measure when no live toast was present.
3. On 390, sidebar `#demoRoleSelect` / `.lang-switch-btn` may be off-canvas (not in first-paint measure set); CSS still sets `min-height: 38px`. Mobile more-sheet counterparts use the same contract classes where present.
4. Inactive dish-subtab uses `--text-muted` on transparent fill; composite against page `--bg-main` `#f3f2f1` is 5.29:1 (AA). Do not compare muted text to `rgba(0,0,0,0)` as black.

## Scheduler handoff

- SAAS-HEIGHT **DEV_DELIVERED** (self-check PASS).
- Next gate: independent QA, then independent UAT. Do not flip card to DONE PASS until both pass.
