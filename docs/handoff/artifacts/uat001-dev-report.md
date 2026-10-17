# UAT-001 Development Re-verify Report

> Generated: 2026-10-17 19:47 Asia/Shanghai (CST)
> Role: Development re-verify only (independent QA / UAT NOT run in this stage)
> Card: [UAT-001] 已有原图单据在复核工作台误判为手工无原图 — P1 PRODUCT_FAULT / MANUAL_UI
> Constraints: no git push; keep port 15010 free/reserved; fix only if AC fails
> Note: Ledger already PASS; this packet supplies local DEV evidence only. QA/UAT packets still required for full three-phase local closure.

## Overall (DEV re-verify)

| Criterion | Verdict |
|---|---|
| 1. Isolate preset: edited WITH original image + manual WITHOUT; owner continue-from-queue review AND archive reopen+refresh once each | **PASS** — seeded `Vendor_A_Edited_WithImage` (#7) + `Vendor_A_Manual_NoImage` (#8); workbench `openReceiptInWorkbench`; archive `loadReceiptDetail` + refresh |
| 2. With-image: img naturalWidth>0 and request 200; only no-image shows 手工无原图 | **PASS** — workbench naturalWidth=640, placeholder hidden; image GET 200 (1949 B PNG); manual shows `无原图（手工录入）` visible; archive same split |
| 3. Expired/tampered URL recover or clear load-failure — must NOT pretend URL failure is no-image; storage/tenant signed auth unchanged | **PASS** — tampered/missing sig → HTTP 403 `IMAGE_SIGNATURE_INVALID`; detail refresh re-issues sig → 200; browser error handler recovered fresh signed URL (not `无原图（手工录入）`) |
| Negative: Tenant B unchanged; cross-tenant denied | **PASS** — B detail of A → 404; tampered tenant_id on image → 403; B baseline rows identical before/after |
| Product fix needed? | **No** — HEAD already correct (`api_receipts.py` data.image_url + has_original_image; `review_core.js` / `archive_list.js` `__uat001Bound` recover/fail labels) |

**UAT-001 DEV re-verify: PASS.** Checklist → `[TODO] DEV_DELIVERED` (local DEV packet; ledger previously PASS; QA/UAT local packets still pending).

## Environment

- Python 3.13 / pytest (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15493** (fresh DB `uat001-dev/environment/uat001.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id` (tenants `uat001-a` / `uat001-b`)
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (probe-only assertion nuance for display:none + leftover naturalWidth)

## AC evidence (pytest)

`demo/tests/test_uat_001_edited_receipt_has_original_image.py` on isolate DB → **5 passed** (`logs/pytest-uat001.txt`)

## AC evidence (live API)

Probe: `uat001-shared/probe_original_image_api.py` → `evidence/original-image-api-probe.json` (**10/10 PASS**)

| Step | Result |
|---|---|
| edited_detail_signed_url | top + data.image_url match; has_original_image=true |
| edited_image_http_200 | 200 / PNG magic / 1949 bytes |
| manual_detail_empty_image | image_url="" / has_original_image=false |
| tampered_sig_403 / missing_sig_403 | IMAGE_SIGNATURE_INVALID |
| refresh_recovers_signed_url | fresh sig → 200 |
| cross_tenant_detail_404 / image_403 | denied |
| tenant_b_unchanged | baseline identical |

## AC evidence (browser)

Probe: `uat001-shared/probe_original_image_ui.py` → `evidence/original-image-ui-probe.json` (**8/8 PASS**)

- Workbench edited: naturalWidth=640, placeholder hidden, image request 200
- Workbench manual: placeholder visible `无原图（手工录入）`, img display=none
- Archive edited + reopen/refresh: naturalWidth=640, placeholder hidden
- Archive manual: `无原图（手工录入）` visible
- Tampered sig: recovered signed URL (not pretend no-image)
- Screenshots MD5 all distinct (`ui/ui-hashes.txt`)

## Code contract (unchanged HEAD)

- `demo/app/api_receipts.py`: detail returns top-level + `data.image_url` via `public_image_url`; `has_original_image` from `image_path`
- `demo/static/js/review_core.js`: `__uat001Bound` error → refresh or `原图加载失败` (not manual no-image)
- `demo/static/js/archive_list.js`: same recover/fail contract on `#archivePreviewImg`

Static contract scan: `logs/static-contract.txt` → ALL_PASS

## Evidence paths

- Report: `docs/handoff/artifacts/uat001-dev-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat001-dev/`
  - `logs/health.json`, `uvicorn.log`, `pytest-uat001.txt`, `probe-api.txt`, `probe-ui.txt`, `static-contract.txt`
  - `evidence/original-image-api-probe.json`, `original-image-ui-probe.json`, `seed-ids.json`, `dev-summary.json`
  - `ui/01_workbench_edited_with_image.png` … `06_workbench_tampered_handled.png`, `ui-hashes.txt`
  - `db/uat001.db`, `environment/{pre-state.txt,cleanup.json,port.txt}`
- Shared scripts: `docs/handoff/artifacts/uat001-shared/{probe_original_image_api.py,probe_original_image_ui.py,run_phase.sh}`

## Cleanup / 15010

- Isolate uvicorn 15493 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Not done (out of DEV scope)

- Independent QA packet (`uat001-qa-report.md`)
- Independent UAT packet (`uat001-uat-report.md`)
