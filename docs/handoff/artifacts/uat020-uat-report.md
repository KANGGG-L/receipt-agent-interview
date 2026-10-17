# UAT020 Independent UAT Report

> Generated: 2026-10-17 05:27 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform development or QA)
> Scope: User/acceptance confirmation — batch photo drawer must not block rotate/crop (`elementFromPoint` + real mouse)
> Card: [UAT020] 批量照片抽屉遮挡原图旋转工具 — P2 QUALITY_RISK
> Prior: `uat020-dev-report.md`, `uat020-qa-report.md` (QA port 15550)
> Checklist / git: not touched (per dispatch)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 1440/390/412/1024 + drawer open: `elementFromPoint` hits `#btnRotate` | **PASS** |
| 1b. Real mouse click rotates 90° / bake (dims swap; `_hasRotatedFlag`) | **PASS** |
| 1c. Crop drag + apply (切图) with drawer open | **PASS** |
| 2. Drawer close + reopen operable; close-btn hittable; items selectable where not under elevated viewer | **PASS** |
| Negative: no force-click / fake dispatch; `originalFile` preserved after rotate + reset | **PASS** |
| Live browser MANUAL_UI | **PASS** (61/0) |

**UAT020 UAT gate: PASS.**

## Isolation (fresh; not QA)

| Field | Value |
|---|---|
| Port | **15600** (QA used 15550; forbidden reuse of 15150–15550) |
| DB | `docs/handoff/artifacts/uat020-uat/environment/uat_isolated.db` (fresh) |
| RAG | `docs/handoff/artifacts/uat020-uat/rag_isolated` |
| Auth | `AUTH_ENABLED=0` (demo X-Role owner) |
| Venv | `environment/venv` → symlink to uat025-qa venv (deps only; fresh DB/port) |
| Fixtures | two synthetic PNGs `fixtures/synth_a.png` / `synth_b.png` (copied from QA fixtures; not shared server) |
| Server pid | 28998 (stopped at end) |
| Browser | Chrome headless via puppeteer-core; viewports 1440 / 1024 / 412 / 390; real `page.mouse.click` only |

## Commands actually run

1. Self-built uvicorn on `127.0.0.1:15600` with fresh `DB_PATH` / `RAG_DIR` / `AUTH_ENABLED=0` — `GET /` HTTP 200; `/api/auth/me` shows `auth_enabled=false`
2. Static + live served citation — `logs/static-evidence.txt`, `logs/live-served-verify.txt` (CSS z-index 110 / open-drawer `pointer-events:none` + JS clearance / `data-uat020` markers)
3. Browser MANUAL_UI `scripts/browser_uat020.js` (puppeteer-core + system Chrome): seed two photos; drawer open; `elementFromPoint` on `#btnRotate`; real rotate bake; crop/slice; drawer close/reopen; reset preserves `originalFile` — `logs/browser-uat.txt`, `logs/browser-uat-result.json`, screenshots under `ui/`
4. Full cleanup: stop pid 28998, free port 15600, `environment/cleanup.json`

Did **not** run: QA harness / pytest specialty (QA-stage); checklist edit; git write.

Browser check tally: **61 PASS / 0 FAIL**.

## Criterion 1 — elementFromPoint + real rotate @ drawer open (PASS)

Two synthetic images seeded via `handleFilesSelect`; drawer auto-opens (`BatchUploader.siderOpen`). Owner default `tab-today` navigated to `tab-scan` first. Batch analysis stubbed so photos stay `pending` (rotate/crop editable).

| Viewport | elementFromPoint → `#btnRotate` | drawer z / tools z | drawer `bottom` clearance | Real click bake |
|---|---|---|---|---|
| 1440 | hitBtn=true, hitDrawer=false | 95 / 110 | 219px | dims 360×480 → 480×360; `hasRotatedFlag=true`; `originalFile` kept |
| 1024 | hitBtn=true | 95 / 110 | 244px | same |
| 412 | hitBtn=true | 95 / 110 | 391px | same |
| 390 | hitBtn=true | 95 / 110 | 320px | same |

Screenshots: `ui/vp{1440,1024,412,390}-drawer-open.png`, `ui/vp*-after-rotate.png`.

## Criterion 1c — crop / 切图 with drawer open (PASS)

Per viewport (drawer open):

1. `elementFromPoint` on `#btnCropToggle` hits crop button
2. Real click → `activeTool=crop`
3. Real mouse drag on `#imgViewerContainer` → non-trivial `cropRect`
4. Real click `#btnApplyCrop` → `cropped=true` / smaller file; **`originalFile` still present**

Screenshots: `ui/vp*-after-crop.png`.

## Criterion 2 — drawer open/closed operable (PASS)

| Check | 1440 | 1024 | 412 | 390 |
|---|---|---|---|---|
| Close then reopen | PASS | PASS | PASS | PASS |
| Close-btn `elementFromPoint` while open | PASS | PASS | PASS | PASS |
| Item select (mouse) where not under elevated viewer | PASS (idx 1) | PASS | PASS (left edge) | PASS (left edge) |
| Rotate hittable with drawer closed | PASS | PASS | PASS | PASS |

Note (matches dev/QA note): open-drawer chrome uses `pointer-events:none` with header/items re-enabled; elevated `#imgViewerContainer` (z=110) may occlude mid-list hits on full-bleed mobile — UAT probes unoccluded item left-edge / close chrome.

## Negative — no force-click; original preserved (PASS)

- UAT harness uses `page.mouse.click` only (recorded per viewport).
- After rotate: `originalFile` size unchanged (452773); after reset: `fileSize` restored to original, `cropped=false`, `rotation=0`.

## Residual notes

1. Browser automation used headless Chromium via puppeteer-core (not a human GUI tab). Flows exercised real served HTML/JS + real mouse geometry (`elementFromPoint` + `page.mouse`).
2. This UAT deliberately used a fresh port/DB distinct from QA 15550 (and outside 15150–15550).
3. Model / LLM creds were not required for this card.

## Cleanup

| Field | Value |
|---|---|
| Stop | `STOPPED_OWNED_TEST_PROCESS` (uvicorn 28998) |
| Port 15600 free | yes (`environment/cleanup.json`) |
| Browser contexts | closed; no leftover chrome for 15600 |
| 15010 | not present; untouched |
| Checklist / ledger | **not** flipped (UAT only; scheduler owns close) |
| Git | no commit / no push |

## Scheduler handoff

- UAT020 **UAT PASS** (61/61 live checks).
- Card may be marked DONE PASS by coordinator (dev + QA + UAT all PASS).
- Artifacts: `docs/handoff/artifacts/uat020-uat/` + this report.
