# UAT-005 Development Re-verify Report

> Generated: 2026-10-17 19:52 Asia/Shanghai (CST)
> Role: Development re-verify only (independent QA / UAT NOT run in this stage)
> Card: [UAT-005] 错误上传格式/损坏图被误归类为识别服务问题 — P2 PRODUCT_FAULT / MANUAL_UI
> Constraints: no git push; keep port 15010 free/reserved; fix only if AC fails
> Note: Ledger already PASS; this packet supplies local DEV evidence only. QA/UAT packets still required for full three-phase local closure.

## Overall (DEV re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse empty.png, malformed.png, wrong-type.txt, PDF, valid JPEG/PNG/WEBP (+ HEIC/TIFF/oversize) fixtures | **PASS** — all under `demo/tests/fixtures/uat005/` exercised by pytest + live API + UI upload |
| 2. Empty/corrupt/unsupported → accurate errors; no approvable receipt/inventory; valid image keeps image on convert_manual (timeout/manual path) | **PASS** — EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED_IMAGE_FORMAT / FILE_TOO_LARGE; zero residue on bad uploads; convert_manual keeps `has_original_image` + `image_url` |
| 3. Assert per actual 9-format contract, not blanket reject PDF | **PASS** — PNG/JPEG/WEBP/TIFF upload OK; HEIC via `/api/convert-image` → JPEG; receipt `/api/upload` PDF → UNSUPPORTED (not recognition); finance voucher still accepts safe PDF (pytest S1-P1-08) |
| Negative: RECOGNITION_UNREADABLE / ENGINE_TIMEOUT / input decode separated; Tenant B ok | **PASS** — bad codes never those; UI ENGINE_TIMEOUT card not file-issue titles; B detail 404; B baseline unchanged |
| Product fix needed? | **No** — HEAD already correct (`api_receipts.py` UploadRejected codes; `upload.js` `isFileIssue` route + file error cards) |

**UAT-005 DEV re-verify: PASS.** Checklist → `[TODO] DEV_DELIVERED` (local DEV packet; ledger previously PASS; QA/UAT local packets still pending).

## Environment

- Python 3.13 / pytest (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15495** (fresh DB `uat005-dev/environment/uat005.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id` (tenants `uat005-a` / `uat005-b` / `uat005-ui`)
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none**

## AC evidence (pytest)

`demo/tests/test_uat_005_upload_error_classification.py` on isolate DB → **10 passed** (`logs/pytest-uat005.txt`)

## AC evidence (live API)

Probe: `uat005-shared/probe_upload_errors_api.py` → `evidence/upload-errors-api-probe.json` (**17/17 PASS**)

| Step | Result |
|---|---|
| empty / malformed / wrong-type / oversize | EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED / FILE_TOO_LARGE + zero residue |
| pdf receipt upload | UNSUPPORTED_IMAGE_FORMAT (not recognition) |
| png/jpg/webp/tif | accepted with receipt_id |
| heic convert | 200 JPEG |
| convert_manual keeps image | doc_form=manual_entry; has_original_image=true |
| codes distinct; B isolation | PASS |

## AC evidence (browser)

Probe: `uat005-shared/probe_upload_errors_ui.py` → `evidence/upload-errors-ui-probe.json` (**10/10 PASS**)

- `showErrorCard` file titles: 文件内容为空 / 图片文件已损坏 / 文件格式不支持 / 文件过大
- ENGINE_TIMEOUT card is not a file-issue title
- Live file uploads: empty / malformed / wrong-type → matching cards or 400 codes
- Screenshots: `ui/error-*.png`, `ui/upload-*.png` (`ui/ui-hashes.txt`)

## Code contract (unchanged HEAD)

- `demo/app/api_receipts.py`: EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED_IMAGE_FORMAT / FILE_TOO_LARGE; magic-unknown → UNSUPPORTED
- `demo/static/js/upload.js`: `isFileIssue` in `routeRecognitionFailure` + `showErrorCard`; ENGINE_TIMEOUT separate; no `escapeHtml(file.name)` (S4-20)

Static contract scan: `logs/static-contract.txt` → ALL_PASS

## Evidence paths

- Report: `docs/handoff/artifacts/uat005-dev-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat005-dev/`
  - `logs/health.json`, `uvicorn.log`, `pytest-uat005.txt`, `probe-api.txt`, `probe-ui.txt`, `static-contract.txt`
  - `evidence/upload-errors-api-probe.json`, `upload-errors-ui-probe.json`, `dev-summary.json`
  - `ui/error-EMPTY_FILE.png` … `upload-wrong-type.png`, `ui-hashes.txt`
  - `db/uat005.db`, `environment/{pre-state.txt,cleanup.json,port.txt}`
- Shared scripts: `docs/handoff/artifacts/uat005-shared/{probe_upload_errors_api.py,probe_upload_errors_ui.py,run_phase.sh}`

## Cleanup / 15010

- Isolate uvicorn 15495 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push

## Not done (out of DEV scope)

- Independent QA packet (`uat005-qa-report.md`)
- Independent UAT packet (`uat005-uat-report.md`)
