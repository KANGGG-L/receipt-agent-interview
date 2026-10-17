# DEP-HIDDEN Development / Gate Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development / DEPENDENCY_GATE first evidenced run
> Card: [DEP-HIDDEN] 真实hidden后台轮询分支1项BLOCKED — P1 DEPENDENCY_GATE / G2
> Constraints: no git push; keep port 15010 reserved; do not fake-fix product; do not claim supportive tests as real-hidden E2E
> Pattern: DEPENDENCY_GATE — if physically cannot achieve real `document.hidden`, leave **BLOCKED** with attempts (honest)

## Overall

| Criterion | Verdict |
|---|---|
| 1. Real visibilitychange and document.hidden recorded | **BLOCKED** — 0/9 attempts achieved `document.hidden===true` or fired `visibilitychange` |
| 2. While hidden, request interval matches original case; resume continue/stop correct | **BLOCKED / unverified** — poll gap measurement requires real hidden; not run |
| 3. If cannot hidden: explicitly mark unverified; do NOT fake-fix product | **PASS (honesty)** — marked unverified/BLOCKED; no `Object.defineProperty` / no product patch |
| Negative: keep 15010; no baseline delete/skip-mask; no supportive-as-model-E2E | **PASS** — 15010 FREE_RESERVED; no product source edits; supportive code presence not claimed as gate PASS |

**DEP-HIDDEN DEV gate: BLOCKED (physically cannot achieve real Page Visibility in this cloud X11/VNC desktop).** Independent QA may re-attempt on another host/desktop; do **not** mark DONE PASS. Do **not** fake-fix `upload.js`. Ledger stays on **DEP-HIDDEN**. **UAT-032** still blocked on **UAT-031**.

## Discovery (prior BLOCKED / original case)

| Source | Finding |
|---|---|
| `docs/handoff/01-acceptance-checklist.md` DEP-HIDDEN | Card requires real `visibilitychange` / `document.hidden`; interval vs original case; else explicit unverified |
| `docs/review-2026-10/S4-frontend-eng.md` **S4-19** | Original defect: fixed **800 ms** poll, **no pause when page hidden**; recommendation: pause on `document.hidden` |
| `demo/static/js/upload.js` `pollReceiptJob` | Product already implements `pauseWhileHidden`: if `document.hidden`, stop scheduling fetches; on `visibilitychange` when visible, `setTimeout(tick, intervalMs)` with `JOB_POLL_INTERVAL_MS = 800` |
| Prior DEP-HIDDEN artifact in clone | None (first run) |
| Prior DEP-MODEL note | G2 predecessor closed; this card is the remaining G2 gate before CLOSE-139 |

### Original-case expected intervals (from S4-19 + current product)

| Phase | Expected request behavior |
|---|---|
| Visible | `/api/job/{id}` polls about every **800 ms** (`JOB_POLL_INTERVAL_MS`) |
| Hidden (`document.hidden === true`) | **Pause** — no further poll requests until visible again |
| Resume (visibilitychange → visible) | Continue after ~800 ms; cancel token / timeout still settle correctly |

Evidence copies: `evidence/s4-19-excerpt.md`, `evidence/upload-js-poll-snippet.txt`.

## Attempts to achieve real hidden (9)

All recorded in `evidence/attempts.json`. None produced `document.hidden === true`.

| # | Method | `document.hidden` | visibilitychange |
|---|---|---|---|
| 1 | Playwright headed Chromium, ignore backgrounding-disable flags, second page `bring_to_front` | false | none |
| 2 | CDP `Emulation.setEmulatedVisibilityState` | N/A — **command not in protocol** on Chrome/156 | none |
| 3 | CDP `Page.setWebLifecycleState` frozen | false | none |
| 4 | CDP `Browser.setWindowBounds` minimized (Playwright Chromium) | false | none |
| 5 | `xdotool windowminimize` on Chrome X window | false | none |
| 6 | `xdotool set_desktop 1` (other virtual desktop) | false | none |
| 7 | System Google Chrome via `connect_over_cdp` + `Target.activateTarget` other target | false | none |
| 8 | System Chrome `Browser.setWindowBounds` minimized — **bounds confirmed `windowState: minimized`** | false | none |
| 9 | System Chrome `Emulation.setEmulatedVisibilityState` | N/A — command absent | none |

Protocol scan (`/json/protocol` on system Chrome): no `Emulation.setEmulatedVisibilityState`; related only `Page.setWebLifecycleState` (does not flip `document.hidden` here).

**Conclusion:** This environment's Chromium/Chrome + X11/VNC stack does not propagate tab/window hide/minimize into the Page Visibility API. Prefer-true-tab-hide and CDP emulate paths were both exhausted. Per card rule: **BLOCKED / unverified**, not a product fake-fix.

## What was intentionally NOT done

- Did **not** stub `document.hidden` / dispatch synthetic `visibilitychange` to force a green poll-interval chart.
- Did **not** patch `demo/static/js/upload.js`.
- Did **not** delete baselines, skip tests to mask, or claim unit/supportive coverage as real-hidden E2E.
- Did **not** git push.
- Did **not** touch port **15010**.

## Environment

- Isolated demo uvicorn: **127.0.0.1:15431** (`DB_PATH` / `RAG_DIR` under `dep-hidden-dev/environment/`); health returned; later stopped.
- **15010**: FREE_RESERVED before and after (`environment/cleanup.json`).
- DISPLAY=:2 (XFCE/VNC); Playwright Chromium 156.0.8078.4; system `/usr/bin/google-chrome`.
- Screens: `screens/01-start-visible.png` … `04-system-chrome-after-min-restore.png`.

## Status for coordinator

| Field | Value |
|---|---|
| Gate result | **BLOCKED** |
| Phase | Dev evidenced BLOCKED (not DEV_DELIVERED PASS) |
| DONE? | **No** — ledger remains **DEP-HIDDEN** |
| QA next? | Optional independent QA re-probe on a host where Page Visibility works; until then gate stays BLOCKED |
| Product change | None |
| Evidence root | `docs/handoff/artifacts/dep-hidden-dev/` + this report |

## Cleanup

- 15431 stopped; 15010 free; chrome debug profiles killed; see `environment/cleanup.json`.
