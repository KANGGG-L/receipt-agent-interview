# UAT020 Development Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development only (independent QA / UAT NOT run in this stage)
> Card: [UAT020] 批量照片抽屉遮挡原图旋转工具 — P2 QUALITY_RISK
> Deps: none
> Vendors in fixtures / prose: none

## Summary

With two-or-more batch photos, `#photoSider.photo-sider-drawer` (`position: fixed; z-index: 95`) overlayed the left original-image column. At 1440 the left pane is only ~280px wide while the drawer is 260px, so `#btnRotate` / crop controls sat under the drawer hit box: `elementFromPoint` returned drawer chrome, not the rotate button. Real clicks never reached `rotateImg` / crop handlers.

Fix (dev only): elevate `#imgViewerContainer` + `.img-toolbar` above the drawer (`z-index: 110`); make open-drawer chrome `pointer-events: none` while header/items re-enable; JS marks the tools layer and clips drawer `bottom` above the toolbar band when open. Rotate bake still keeps `originalFile`. No force-click. No independent QA/UAT in this stage.

## Before

- `.photo-sider-drawer` fixed overlay at `z-index: 95` covered left tools
- `.img-toolbar` had no stacking elevation; open drawer used default `pointer-events: auto` on the full flex box (including empty list area)
- Mobile `left: 0` full-bleed drawer ( <=640 ) made the same occlusion worse at 390/412
- No clearance sync when drawer opened/resized

## After

### `demo/static/css/style.css`

- `.img-toolbar`: `position: relative; z-index: 110; background: var(--bg-card)`
- `#imgViewerContainer` / `.img-tools-hit-layer`: `z-index: 110` (above drawer 95)
- `.photo-sider-drawer.open`: `pointer-events: none`; `.sider-header` / `.photo-sider-item` (+ descendants) `pointer-events: auto`
- Closed drawer `:not(.open)` still `pointer-events: none` + `visibility: hidden`

### `demo/static/css/review-mobile.css`

- `@media (max-width: 1024px)` reinforces tool elevation + open-drawer pass-through for 390/412/1024

### `demo/static/js/upload.js`

- `ensureImgToolsHitLayer()` — class `img-tools-hit-layer` + `data-uat020` markers
- `syncPhotoSiderClearanceForImgTools()` — when drawer `.open`, set `bottom` so drawer ends above toolbar (gap 10px); clear inline styles when closed
- `refreshUat020ImgToolsVsDrawer()` from `updateToolbarButtonStates`, resize, and class MutationObserver on `#photoSider` (class only; no style feedback loop)
- Rotate bake: if `originalFile` missing, snapshot current `photo.file` before replacing file / croppedObjectUrl (reset path unchanged)

## Tests run (dev self-check; not independent QA)

| Command | Result |
|---|---|
| `node --check demo/static/js/upload.js` | exit 0 |
| `node demo/tests/frontend_uat020_drawer_rotate_harness.js` | 20 PASS / 0 FAIL |
| `python3 demo/tests/test_uat_020_drawer_rotate_static.py` | 5/5 PASS |

MANUAL_UI (two synthetic images; 1440/390/412/1024; drawer open; `elementFromPoint` on `#btnRotate` + real click -> 90deg; crop/slice; drawer open+closed operable; no force-click) left for independent QA/UAT.

## Files touched

- `demo/static/js/upload.js`
- `demo/static/css/style.css`
- `demo/static/css/review-mobile.css`
- `demo/tests/test_uat_020_drawer_rotate_static.py` (new)
- `demo/tests/frontend_uat020_drawer_rotate_harness.js` (new)
- `docs/handoff/artifacts/uat020-dev-report.md` (this file)
- `docs/handoff/01-acceptance-checklist.md` (UAT-036 DONE PASS; UAT020 DEV_DELIVERED; UAT-027 still BLOCKED)
- `docs/handoff/02-coordinator-ledger.md` (UAT-036 closed; UAT020 DEV; next=UAT020 QA; UAT-027 BLOCKED on UAT-021)

## Explicit non-claims

- Independent QA not run
- Independent UAT not run
- Card must not be marked DONE PASS until QA + UAT gates pass
- No git commit / push
- Cross-tenant / role boundaries unchanged

## Suggested independent QA focus

1. Seed two synthetic images; open batch drawer
2. Viewports 1440, 1024, 412, 390
3. With drawer open: `document.elementFromPoint(cx, cy)` at `#btnRotate` center === `#btnRotate` (or child text node of it)
4. Real `click()` (not `dispatchEvent` / force) rotates preview 90deg / bake path
5. Crop drag on `#imgViewerContainer` works with drawer open
6. Drawer close/open still works (header close / toggle); selecting items still possible where not under elevated viewer
7. Reset after rotate still restores original (originalFile / objectUrl intact)
