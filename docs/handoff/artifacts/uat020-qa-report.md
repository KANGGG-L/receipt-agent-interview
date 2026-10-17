# UAT020 Independent QA Report

> Generated: 2026-10-17 05:26 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform the development)
> Scope: Verify UAT020 acceptance only (batch photo drawer must not block rotate/crop)
> Card: [UAT020] 批量照片抽屉遮挡原图旋转工具 — P2 QUALITY_RISK
> Prior: `uat020-dev-report.md`
> UAT: NOT started (per dispatch: No UAT/git)

## Overall gate

| Criterion | Verdict |
|---|---|
| 1. 1440/390/412/1024 + drawer open: `elementFromPoint` hits `#btnRotate` | **PASS** |
| 1b. Real mouse click rotates 90° / bake (dims swap; `_hasRotatedFlag`) | **PASS** |
| 1c. Crop drag + apply (切图) with drawer open | **PASS** |
| 2. Drawer close + reopen operable; close-btn hittable; items selectable where not under elevated viewer | **PASS** |
| Negative: no force-click / fake dispatch; `originalFile` preserved after rotate + reset | **PASS** |
| Specialty static pytest (5) | **PASS** |
| Frontend harness (20) | **PASS** |
| Live browser MANUAL_UI | **PASS** (61/0) |

**UAT020 QA gate: PASS.** Do not treat this as card close; independent UAT not started. Checklist not flipped.

## Commands actually run

1. Static evidence — `docs/handoff/artifacts/uat020-qa/logs/static-evidence.txt`
2. `node --check demo/static/js/upload.js` — OK (`logs/node-check.txt`)
3. `node demo/tests/frontend_uat020_drawer_rotate_harness.js` — **20 PASS / 0 FAIL** (`logs/frontend-harness.txt`)
4. Inline runner of `demo/tests/test_uat_020_drawer_rotate_static.py` — **5/5 PASS** (`logs/pytest-uat020.txt`)
5. Isolated uvicorn live MANUAL_UI (self-built)
   - Port **15550** (fresh; avoided 15150–15500)
   - DB `environment/qa_isolated.db`, RAG `rag_isolated`, `AUTH_ENABLED=0`
   - Fixtures: two synthetic PNGs `fixtures/synth_a.png` / `synth_b.png`
   - Browser: `scripts/browser_qa020.js` (puppeteer-core + Chrome; viewports 1440 / 1024 / 412 / 390)
   - Batch analysis stubbed so photos stay `pending` (rotate/crop editable); real `page.mouse.click` only (no force)
   - Result: **pass=true, passCount=61, failCount=0** (`logs/browser-qa-result.json`, `logs/browser-qa.txt`)
6. Live served verify — `logs/live-served-verify.txt` (CSS z-index 110 / open-drawer `pointer-events:none` + JS clearance markers)

Did **not** run: independent UAT stage; checklist edit; git write.

## Criterion 1 — elementFromPoint + real rotate @ drawer open (PASS)

Two synthetic images seeded via `handleFilesSelect`; drawer auto-opens (`BatchUploader.siderOpen`). Owner default `tab-today` navigated to `tab-scan` first.

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

Note (matches dev note): open-drawer chrome uses `pointer-events:none` with header/items re-enabled; elevated `#imgViewerContainer` (z=110) may occlude mid-list hits on full-bleed mobile — QA probes unoccluded item left-edge / close chrome.

## Negative — no force-click; original preserved (PASS)

- QA harness uses `page.mouse.click` only (recorded per viewport).
- Static harness asserts upload.js does not fake rotate via `dispatchEvent` on `#btnRotate`.
- After rotate: `originalFile` size unchanged (452773); after reset: `fileSize` restored to original, `cropped=false`, `rotation=0`.

## Isolation / cleanup

| Field | Value |
|---|---|
| Port | **15550** (fresh; avoided 15150–15500) |
| DB | `docs/handoff/artifacts/uat020-qa/environment/qa_isolated.db` |
| AUTH_ENABLED | 0 |
| uvicorn | pid 22599 started then stopped; port free; reuseaddr bind OK |
| Browser | headless Chrome via puppeteer-core; contexts closed |
| Artifacts | `docs/handoff/artifacts/uat020-qa/` + this report |
| Checklist / ledger | **not** flipped (QA only) |
| Git | no commit / no push |
| UAT | not started |
| cleanup.json | `docs/handoff/artifacts/uat020-qa/environment/cleanup.json` |

## Scheduler handoff

- UAT020 **QA PASS** (61/61 live checks + specialty gates).
- Please dispatch **independent UAT** next (fresh port ≠ 15550; do not reuse 15150–15550).
