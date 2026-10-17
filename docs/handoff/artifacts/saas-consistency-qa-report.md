# SAAS-CONSISTENCY Independent QA Report

> Generated: 2026-10-17 06:00 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify SAAS-CONSISTENCY acceptance only (1440 container align / report date labels / 390 no regress / tokens listed / no unapproved brand expansion claimed complete)
> Card: [SAAS-CONSISTENCY] 页面容器/工具条/日期标签视觉体系差异 — P2 DESIGN_OBSERVATION
> Prior: `saas-consistency-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 1440 今日/库存/归档/设置同类容器对齐规则一致 | **PASS** (live Puppeteer; first visible card left=264px all four; tabW=1148; `st-wrap` maxW=100%) |
| 2. 报表日期 label 不逐字两行 | **PASS** (`月份：` / `起止日期：` h=20, `white-space:nowrap`, `letterStackedSuspect=false`) |
| 3. 390 不退化 | **PASS** (`docOverflowX=false` for today/inventory/settings) |
| 4. 列出采用 token 及视觉证据；未获批准语义/品牌扩展不标完成 | **PASS** (7 layout tokens live; brandish keys empty; SAAS CSS block has no new primary/accent/brand decls; dev report explicitly does **not** mark brand expansion complete) |

**SAAS-CONSISTENCY QA gate: PASS.** Do not treat as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Isolated uvicorn on **15020** (avoided **15150–15800** and **15410**; **15010** untouched)
   - `DB_PATH=…/saas-consistency-qa/environment/qa_isolated.db` (copy of `demo/receipt_demo.db`)
   - `AUTH_ENABLED=0`, host `127.0.0.1`
   - pid 52287 → stopped (`environment/cleanup.json`)
2. Live Puppeteer capture — `scripts/capture.js` → **overall PASS** (`logs/capture.txt`, `logs/capture-result.json`)
3. Static evidence — `logs/static-evidence.txt` (tokens present; `st-wrap` 100%; `report-date-label` / `page-shell` / cache-bust in HTML; no brandish decls in SAAS block)
4. Screenshots under `docs/handoff/artifacts/saas-consistency-qa/ui/`

Did **not** run: independent UAT; checklist/ledger flip; git write.

## Criterion 1 — 1440 container alignment (PASS)

Live against `http://127.0.0.1:15020` @ 1440×900, role `owner`:

| Tab | cardLeft | tabLeft | tabW | st-wrap maxW |
|---|---|---|---|---|
| today | **264** | 264 | 1148 | 100% |
| inventory | **264** | 264 | 1148 | (n/a) |
| archive | **264** | 264 | 1148 | (n/a) |
| settings | **264** | 264 | 1148 | 100% |

Method: first **visible** `.card` left edge; delta across four tabs = 0 (≤2px gate); band 250–280 matches sidebar 240 + `--page-inline-pad` 24. Shots: `ui/1440-today.png`, `ui/1440-inventory.png`, `ui/1440-archive.png`, `ui/1440-settings.png`.

## Criterion 2 — report date labels not letter-stacked (PASS)

| Label | h | whiteSpace | wordBreak | letterStackedSuspect |
|---|---|---|---|---|
| 月份： | 20 | nowrap | keep-all | **false** |
| 起止日期： | 20 | nowrap | keep-all | **false** |

Shot: `ui/1440-report-date-labels.png`.

## Criterion 3 — 390 no regress (PASS)

| View | docOverflowX |
|---|---|
| today | **false** |
| inventory | **false** |
| settings | **false** |

Shots: `ui/390-today.png`, `ui/390-inventory.png`, `ui/390-settings.png`.

## Criterion 4 — tokens + brand expansion not falsely complete (PASS)

### Layout tokens (live computed)

| Token | Live value |
|---|---|
| `--page-max-width` | `1440px` |
| `--page-inline-pad` | `24px` |
| `--page-block-gap` | `16px` |
| `--page-card-pad` | `24px` |
| `--toolbar-gap` | `12px` |
| `--toolbar-cluster-gap` | `8px` |
| `--toolbar-divider` | `rgba(30, 34, 35, 0.08)` (= `--border-color`) |

Reused surfaces observed live: `--bg-main` `#f3f2f1`, `--border-color` `rgba(30, 34, 35, 0.08)`.

### Brand / semantic expansion

| Check | Result |
|---|---|
| Live brandish token keys in capture set | **[]** (none) |
| New `--*primary* / --*accent* / --*brand* / --*semantic*` decls in SAAS-CONSISTENCY CSS block | **NONE** (`logs/static-evidence.txt`) |
| Dev report marks unapproved palette expansion complete? | **No** — explicitly “**Not marked complete**” / “no new brand/semantic hues” |

QA therefore treats AC4 as PASS for “tokens listed + evidence” and confirms brand expansion remains **not** claimed complete.

## Negative / isolation / cleanup

| Field | Value |
|---|---|
| Port | **15020**; avoided **15150–15800** and **15410**; **15010** reserved untouched |
| DB | `docs/handoff/artifacts/saas-consistency-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 (demo role switcher) |
| uvicorn | pid 52287 started then stopped; process dead; reuseaddr bind OK |
| Baselines | not deleted |
| Artifacts | `docs/handoff/artifacts/saas-consistency-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/saas-consistency-qa/environment/cleanup.json` |

## Residual risks (honest)

1. Alignment measured on first visible card left edge; today grid uses narrower cards (width 372) but shares the same left edge as full-width inventory/archive/settings cards — intentional shell alignment, not equal card widths.
2. AUTH_ENABLED=0 demo path only; production AUTH=1 cookie path not re-exercised.
3. Checklist still `[TODO] DEV_DELIVERED`; do not flip from this QA alone.
4. Inventory empty-state content is fixture data (out of scope per dev report), not scored as a layout fail.

## Scheduler handoff

- SAAS-CONSISTENCY **QA PASS** (5/5 live checks + static brand-expansion non-claim confirmed).
- Please dispatch **independent UAT** next (fresh port ≠ 15020 / ≠ 15410; avoid 15150–15800; keep 15010 free unless ENG-04 E2E needs it).
- After UAT PASS: flip `[SAAS-CONSISTENCY]` to DONE PASS.
