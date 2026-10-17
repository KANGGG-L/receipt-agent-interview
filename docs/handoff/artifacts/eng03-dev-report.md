# ENG-03 DEV Report

Generated: 2026-10-17 18:58:53 CST. Card: [ENG-03] 手工模式预填徽章旧断言 (P1 ENGINEERING_GATE). Phase: DEV (already-fixed card: re-verify + evidence; no product code changed).
Isolate: port **15438**, fresh DB `eng03-dev/environment/eng03.db`, AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). Creds sourced from openai_rec.env + model.env; key names only in `logs/env-keys-present.txt`, no values.

## Verdict: DEV PASS

| AC | Method | Result |
|---|---|---|
| 1 手工模式 aiPrefillNoticeBadge 隐藏 | live UI `#btnNewManualEntry` -> badge_state | PASS: manual badge "手工录入" visible; AI badge hidden; title has no "AI 预填" |
| 2 真实人工填写保存成功 | live fill Vendor_A / 4x20 / total 80 / cash / manual_entry -> POST /api/save_edited | PASS: HTTP 200 status=success receipt_id=2; AI badge still hidden after save; DB doc_form=manual_entry |
| 3 不能把真正 AI 预填显示回归为隐藏 | **real model, no route mocks / no fake syncReviewSourceBadge**: POST /api/upload (receipt field), job `62142af4ceec47efa57db12969e0aff6` -> done; queue click 继续复核 | PASS: AI badge "AI 预填，待核对" visible; manual badge hidden; supplier 7-ELEVEN; doc_form=printed_delivery_note; 1 item row; receipt_api ai_prefill_present |
| Supporting regression | tests/test_workbench_e2e_playwright.py (non-manual badge path still uses evaluate syncReviewSourceBadge by design; NOT counted toward model AC3) | 1 passed |
| Negative | 15010 free; no skip; no mock as model E2E; no git push | PASS |

## Evidence
- `eng03-dev/evidence/real-probe.json` (checks, verdict, network, DB rows)
- `eng03-dev/screens/00-landing.png`, `01-manual-mode-badges.png`, `02-manual-save-done.png`, `03b-queue-before-open.png`, `03-real-ai-prefill-badge.png`
- `eng03-dev/db/eng03.db` (receipt #1 AI parsed 7-ELEVEN / printed_delivery_note; #2 manual Vendor_A)
- `logs/real-probe.txt`, `logs/pytest.txt`, `logs/pytest.xml`, `logs/uvicorn.log`, `logs/env-keys-present.txt`, `environment/cleanup.json`
- Scripts: `eng03-shared/run_phase.sh`, `eng03-shared/probe_real.py`

## Request / terminal (AC3)
- Upload: `POST /api/upload?treatment=true&async=true&force=false` -> 200 queued, job_id=`62142af4ceec47efa57db12969e0aff6`, receipt_id=1
- Job polls: running -> done (~26s wall for open path; job terminal in upload.polls)
- Open: queue row `#1` button 继续复核 (opened_via=`queue_continue_review_click`)
- UI after open: `#aiPrefillNoticeBadge` text `AI 预填，待核对`; `#inpSupplier`=`7-ELEVEN`; `#inpDocForm`=`printed_delivery_note`

## Notes
- Product code unchanged. Residual PARTIAL was missing real-model AC3 evidence; prior workbench e2e only evaluated `syncReviewSourceBadge({doc_form:'printed_delivery_note'})`.
- UI `set_input_files` auto-upload is flaky on this host; probe falls back to the same real `/api/upload` + job poll + workbench open path (still live model, still live badge). Attempt3 earlier also proved UI auto-upload + badge on the same build.
- Cleanup: uvicorn 15438 stopped; rag_chroma/uploads removed; 15010 FREE_RESERVED; no git push.
- Checklist set to `[TODO] DEV_DELIVERED` only (not DONE). Next gate: independent QA.
