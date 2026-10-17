# UAT-005 Independent UAT Report

> Generated: 2026-10-17 19:55 Asia/Shanghai (CST)
> Role: Independent UAT (did NOT perform the development or QA)
> Card: [UAT-005] 错误上传格式/损坏图被误归类为识别服务问题 — P2 PRODUCT_FAULT / MANUAL_UI
> Prior: `uat005-dev-report.md`, `uat005-qa-report.md`
> Constraints: no git push; keep port 15010 FREE_RESERVED; fresh isolate port ≠15505 ≠15495; re-verify all AC; on PASS mark checklist DONE

## Overall (UAT re-verify)

| Criterion | Verdict |
|---|---|
| 1. Reuse empty.png, malformed.png, wrong-type.txt, PDF, valid JPEG/PNG/WEBP (+ HEIC/TIFF/oversize) fixtures | **PASS** — fixtures under `demo/tests/fixtures/uat005/` exercised by pytest + live API + UI upload |
| 2. Empty/corrupt/unsupported → accurate errors; no approvable receipt/inventory; valid image keeps image on convert_manual | **PASS** — EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED_IMAGE_FORMAT / FILE_TOO_LARGE; zero residue on bad uploads; convert_manual keeps `has_original_image` + `image_url` |
| 3. Assert per actual 9-format contract, not blanket reject PDF | **PASS** — PNG/JPEG/WEBP/TIFF upload OK; HEIC via `/api/convert-image` → JPEG; receipt `/api/upload` PDF → UNSUPPORTED_IMAGE_FORMAT (not recognition) |
| Negative: RECOGNITION_UNREADABLE / ENGINE_TIMEOUT / input decode separated; Tenant B ok | **PASS** — bad codes never those; UI ENGINE_TIMEOUT card title/badge are not file-issue titles; B detail 404; B receipts/batches baseline unchanged (0→0) |
| Product fix needed? | **No** — HEAD already correct |

**UAT-005 UAT re-verify: PASS.** Checklist → `[DONE] PASS` (2026-10-17). Local DEV + QA + UAT packets now complete (three-phase local evidence).

## Environment

- Python 3.13 / pytest / Playwright (venv: `docs/handoff/artifacts/eng05-uat/environment/venv`)
- Isolate port: **15515** (≠ DEV 15495, ≠ QA 15505); fresh DB `uat005-uat/environment/uat005.db` (+ backup `db/uat005.db`)
- Auth: `AUTH_ENABLED=0`; RAG hash; headers `X-Role` / `X-Tenant-Id` (tenants `uat005-a` / `uat005-b`)
- 15010: never listening; left FREE_RESERVED
- Product edits this stage: **none** (re-verify only)

## AC evidence (pytest)

`demo/tests/test_uat_005_upload_error_classification.py` on isolate DB → **10 passed** (`logs/pytest-uat005.txt`)

## AC evidence (live API)

Probe: `uat005-shared/probe_upload_errors_api.py` → `evidence/upload-errors-api-probe.json` (**17/17 PASS**)

| Step | Result |
|---|---|
| empty / malformed / wrong-type / oversize | EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED / FILE_TOO_LARGE + zero residue on empty/malformed |
| pdf receipt upload | UNSUPPORTED_IMAGE_FORMAT (not recognition) |
| png/jpg/webp/tif | accepted with receipt_id |
| heic convert | 200 JPEG |
| convert_manual keeps image | doc_form=manual_entry; has_original_image=true |
| codes distinct; bad not misclassified as recognition | PASS (EMPTY/INVALID/UNSUPPORTED/TOO_LARGE only) |
| B isolation | detail 404; B receipts/batches 0→0 |

## AC evidence (browser)

Probe: `uat005-shared/probe_upload_errors_ui.py` → `evidence/upload-errors-ui-probe.json` (**10/10 PASS**)

- `showErrorCard` file titles: 文件内容为空 / 图片文件已损坏 / 文件格式不支持 / 文件过大
- ENGINE_TIMEOUT card: title「自动识别现在比较忙」, badge「不是照片的问题」 (not a file-issue title)
- Live file uploads: empty / malformed / wrong-type → matching cards + HTTP 400 codes
- Screenshots: 8 distinct (`ui/ui-hashes.txt`)

## Code contract (unchanged HEAD)

- `demo/app/api_receipts.py`: EMPTY_FILE / INVALID_IMAGE / UNSUPPORTED_IMAGE_FORMAT / FILE_TOO_LARGE; magic-unknown → UNSUPPORTED
- `demo/static/js/upload.js`: `isFileIssue` in `routeRecognitionFailure` + `showErrorCard`; ENGINE_TIMEOUT separate; no `escapeHtml(file.name)` (S4-20)

Static contract scan: `logs/static-contract.txt` → ALL_PASS

## Evidence paths

- Report: `docs/handoff/artifacts/uat005-uat-report.md` (this file)
- Folder: `docs/handoff/artifacts/uat005-uat/`
  - `logs/health.json`, `uvicorn.log`, `pytest-uat005.txt`, `probe-api.txt`, `probe-ui.txt`, `static-contract.txt`
  - `evidence/upload-errors-api-probe.json`, `upload-errors-ui-probe.json`, `uat-summary.json` (and run_phase `dev-summary.json` twin)
  - `ui/error-*.png`, `ui/upload-*.png`, `ui/ui-hashes.txt`
  - `db/uat005.db`, `environment/{pre-state.txt,cleanup.json,port.txt}`
- Shared scripts: `docs/handoff/artifacts/uat005-shared/{probe_upload_errors_api.py,probe_upload_errors_ui.py,run_phase.sh}`

## Checklist annotation

- Promoted UAT-005 to `[DONE] PASS` (2026-10-17 Asia/Shanghai) — local DEV + QA + UAT packets complete
- Ledger next per `artifacts/partials-triage.md`: **UAT-008**

## Cleanup / 15010

- Isolate uvicorn 15515 stopped
- 15010: FREE_RESERVED before and after (`environment/cleanup.json`)
- No git push
