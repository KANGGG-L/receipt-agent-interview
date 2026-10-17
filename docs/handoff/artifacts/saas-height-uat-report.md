# SAAS-HEIGHT Independent UAT Report

> Generated: 2026-10-17 06:13 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: MANUAL_UI acceptance — desktop/390 computed height ≥38px; keyboard usable; no clip/overlap; contrast 4.5:1 samples
> Card: [SAAS-HEIGHT] 原38px控件合同未全面达标 — P2 DESIGN_GAP
> Prior: `saas-height-dev-report.md`, `saas-height-qa-report.md` (QA port 15040)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 桌面/390 目标控件 computed height >= 38px | **PASS** (live Puppeteer; heightFails=0; toast close 38×38; token `--control-min-height: 38px`) |
| 2. keyboard 可用，无截断/重叠 | **PASS** (1440 archive: role select / lang / chip / advanced toggle focus+outline; 390: chip + advanced toggle focus; `archiveChipOverlap=false`; `docOverflowX=false` all measured tabs) |
| 3. 原文字 4.5:1 抽样同控件仍满足 | **PASS** (16 composite samples; fails=0; inactive dish-subtab `#646464` on blended `#e9e8e7` **4.84:1**; active / chip / lang ≥16:1) |

**SAAS-HEIGHT UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15050** (avoided **15040** / **15420** / **15020–15030** / **15150–15800**; **15010** reserved untouched) |
| DB | `docs/handoff/artifacts/saas-height-uat/environment/uat_isolated.db` (fresh copy of demo DB; not QA DB) |
| RAG | `docs/handoff/artifacts/saas-height-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo role switcher) |
| Venv | `environment/venv` → symlink to saas-consistency-uat venv (deps only; fresh DB/port) |
| Server pid | 60957 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; MANUAL_UI measurements + screenshots |

## Commands actually run

1. Isolated uvicorn on **15050**
   - `DB_PATH=…/saas-height-uat/environment/uat_isolated.db`
   - `AUTH_ENABLED=0`, host `127.0.0.1`
   - module `demo.app.main:app`
   - pid 60957 → stopped (`environment/cleanup.json`)
   - `GET /` → HTTP 200
2. Live Puppeteer MANUAL_UI — `scripts/capture.js` → **overall PASS** (`logs/capture.txt`, `logs/capture-result.json`, `logs/uat-summary.json`, `uat-result.json`)
3. Screenshots under `docs/handoff/artifacts/saas-height-uat/ui/` (8 shots)
4. Full cleanup: stop pid 60957, free port 15050, `environment/cleanup.json`

Did **not** run: checklist/ledger flip; git write; development changes.

## Criterion 1 — desktop/390 height ≥38px (PASS)

Live against `http://127.0.0.1:15050`. Min observed computed height per label (fails=0):

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
| Port | **15050**; avoided **15040** / **15420** / **15020–15030** / **15150–15800**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/saas-height-uat/environment/uat_isolated.db` (fresh vs QA) |
| AUTH_ENABLED | 0 (demo role switcher path) |
| uvicorn | pid 60957 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Artifacts | `docs/handoff/artifacts/saas-height-uat/` + this report |
| Checklist / ledger | **not** flipped (UAT report only) |
| Git | no commit / no push |
| cleanup.json | `docs/handoff/artifacts/saas-height-uat/environment/cleanup.json` |

## Residual notes (honest)

1. On 390, sidebar `#demoRoleSelect` / `.lang-switch-btn` may be off-canvas in first-paint measure set (same residual as DEV/QA); CSS still sets `min-height: 38px`. Height gate scored on visible targets + CSS contract for off-canvas ones.
2. Fees drawer toggle not forced open in this UAT capture (requires live receipt review session); CSS contract present per DEV.
3. Toast close was injected for hit-area measure when no live toast was present (matches DEV/QA method).
4. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.

## Scheduler handoff

- SAAS-HEIGHT **UAT PASS** (5/5 live checks).
- Independent DEV + QA + UAT all PASS. Scheduler may flip `[SAAS-HEIGHT]` to DONE PASS.
- This UAT agent did **not** flip checklist or touch git.
