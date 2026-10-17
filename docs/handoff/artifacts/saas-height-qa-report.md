# SAAS-HEIGHT Independent QA Report

> Generated: 2026-10-17 06:12 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify SAAS-HEIGHT acceptance only (desktop/390 computed height ≥38px; keyboard usable; no clip/overlap; contrast 4.5:1 samples)
> Card: [SAAS-HEIGHT] 原38px控件合同未全面达标 — P2 DESIGN_GAP
> Prior: `saas-height-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 桌面/390 目标控件 computed height >= 38px | **PASS** (live Puppeteer; heightFails=0; toast close 38×38; token `--control-min-height: 38px`) |
| 2. keyboard 可用，无截断/重叠 | **PASS** (1440 archive: role select / lang / chip / advanced toggle focus+outline; 390: chip + advanced toggle focus; `archiveChipOverlap=false`; `docOverflowX=false` all measured tabs) |
| 3. 原文字 4.5:1 抽样同控件仍满足 | **PASS** (16 composite samples; fails=0; inactive dish-subtab `#646464` on blended `#e9e8e7` **4.84:1**; active / chip / lang ≥16:1) |

**SAAS-HEIGHT QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Isolated uvicorn on **15040** (avoided **15020–15030**, **15420**, **15150–15800**; **15010** untouched)
   - `DB_PATH=…/saas-height-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`
   - listen pid **59306** → stopped (`environment/cleanup.json`)
2. Live Puppeteer capture — `scripts/capture.js` → **overall PASS** (`logs/capture-run2.log`, `logs/qa-summary.json`, `logs/capture-result.json`)
3. Screenshots under `docs/handoff/artifacts/saas-height-qa/ui/`

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — desktop/390 height ≥38px (PASS)

Live against `http://127.0.0.1:15040`. Min observed computed height per label (fails=0):

| Control | 1440 h (px) | 390 h (px) |
|---|---:|---:|
| `archive-chip` | 38 | 38 |
| `archive-advanced-toggle` | 38 | 38 |
| `demo-role-select` | 38 | (off-canvas first paint; CSS min-height 38px) |
| `lang-switch-btn` | 38 | (off-canvas first paint; CSS min-height 38px) |
| `dish-subtab-btn` | 38 | 38 |
| `analytics-segment-btn` | 42 | 42 |
| `filter-time-range-btn` | 38 | 38 |
| `parsing-log-filter-control` | 38 | 38 |
| `parsing-log-filter-btn` | 38 | 38 |
| `engine-card-toggle-btn` | 38 | 38 |
| `app-toast-close` | 38×38 | 38×38 |

Token live: `--control-min-height: 38px` on all measured viewports.

Shots: `ui/1440-*-controls.png`, `ui/390-*-controls.png`.

## Criterion 2 — keyboard / no clip / no overlap (PASS)

### Keyboard (archive samples)

| Control | 1440 | 390 |
|---|---|---|
| `#demoRoleSelect` | focus + outline `rgb(37,99,235) solid 2px` | off-canvas / not in first-paint measure |
| `.lang-switch-btn` | focus + outline | off-canvas |
| `#tab-archive .chip` | focus + outline | focus + outline |
| `#archiveAdvancedToggle` | focus + outline (`tabIndex=0`) | focus + outline |

Present native controls on 1440 archive all `canTab` and focused. 390 chip + advanced toggle focused.

### Clip / overlap

| Viewport family | `archiveChipOverlap` | `docOverflowX` |
|---|---|---|
| 1440 archive/dishes/analytics/engine | **false** | **false** |
| 390 archive/dishes/analytics/engine | **false** | **false** |

## Criterion 3 — contrast ≥4.5:1 samples (PASS)

Composite opaque (alpha-blended translucent ancestors; do **not** treat `rgba(0,0,0,0.04)` as solid black). Representative samples:

| Sample | fg | bg (composite) | ratio | pass |
|---|---|---|---:|---|
| chip idle 「全部」 | `#1e2223` | `#ffffff` | 16.05 | yes |
| lang active 「简体中文」 | `#ffffff` | `#032425` | 16.37 | yes |
| dish inactive 「餐品 BOM 配方库」 | `#646464` | `#e9e8e7` | **4.84** | yes |
| dish active 「每日消耗录入」 | `#ffffff` | `#032425` | 16.37 | yes |
| analytics-segment-btn | `#0f172a` | `#ffffff` | 17.85 | yes |
| filter-time-range-btn active | `#ffffff` | `#032425` | 16.37 | yes |

16/16 measured composite samples ≥ 4.5:1 (`contrastFails=[]`).

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15040**; avoided **15020–15030**, **15420**, **15150–15800**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/saas-height-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 (demo role switcher) |
| uvicorn | pid 59306 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Artifacts | `docs/handoff/artifacts/saas-height-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/saas-height-qa/environment/cleanup.json` |

## Residual risks (honest)

1. On 390, sidebar `#demoRoleSelect` / `.lang-switch-btn` may be off-canvas in first-paint measure set (same residual as DEV); CSS still sets `min-height: 38px`. Height gate scored on visible targets + CSS contract for off-canvas ones.
2. Fees drawer toggle not forced open in this QA capture (requires live receipt review session); CSS contract present per DEV.
3. Toast close was injected for hit-area measure when no live toast was present (matches DEV method).
4. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
5. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.

## Scheduler handoff

- SAAS-HEIGHT **QA PASS** (5/5 live checks).
- Please dispatch **independent UAT** next (fresh port ≠ 15040 / ≠ 15420; avoid 15020–15030 and 15150–15800; keep 15010 free).
- After UAT PASS: flip `[SAAS-HEIGHT]` to DONE PASS.
