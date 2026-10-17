# UAT-016 DEV Report

Generated: 2026-10-17 19:13 CST. Card: [UAT-016] 当前UAT Spec硬删边界与原计划owner回收策略不一致 (P2 SPEC_CONFLICT / SPEC_DECISION). Phase: DEV (SPEC_KEEP document + live owner recycle/restore/purge; no product code changed).
Isolate: port **15469**, fresh DB `uat016-dev/environment/uat016.db` (+ browser re-probe DB `uat016-browser.db`), AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). No model keys required.

## SPEC_DECISION: DEFAULT_ORIGINAL (SPEC_KEEP)

| Field | Value |
|---|---|
| Decision | **DEFAULT_ORIGINAL** |
| Card contract | `docs/handoff/01-acceptance-checklist.md` §[UAT-016] — D0 SPEC_KEEP; source_files=`[]`; keep existing owner recycle/restore/purge; do not invent admin-only trash auth |
| Original canonical | **S1-P1-12** `docs/review-2026-10/S1-backend.md:76` — trash open to **owner** with UI, tenant-filtered; flagged discard owner-only. **api-conventions** `docs/api-conventions.md:88` — `/api/admin/trash*` removed; replaced by `/api/trash/receipts*`. Related: **S1-P1-13** experiment conclude `require_admin` (identity `concluded_by`); **S3-07** in-app discard confirm without false admin-restore promise; **S4-12** shared dialog path used by destructive confirms (`uiConfirm` in finance purge). |
| Derived Spec rejected | Derived UAT Spec that would treat hard-delete as admin-only / replace owner recycle with invented admin trash — **not approved**. Optional branch without approval must not claim new admin-trash or hard-delete-only features done. |
| Owner | D0 SPEC_KEEP wave / handoff checklist owner (`docs/handoff/01-acceptance-checklist.md` §2.1 UAT-016). |
| Product change | None. Owner trash already live via `demo/app/api_receipt_lifecycle.py` + `demo/static/js/finance.js`. |

## Verdict: DEV PASS

| AC | Method | Result |
|---|---|---|
| 1 SPEC_DECISION recorded | DEFAULT_ORIGINAL + citations above / `evidence/spec-keep.json` | PASS |
| 2 Keep owner recycle/restore/purge; no invented admin-only auth | Live API isolate 15469; staff list 403; owner list/restore/purge; cross-tenant 404; old `/api/admin/trash` 404; approved discard 409 `CANNOT_DISCARD_APPROVED` | PASS |
| 3 Optional branch not claimed | No new admin trash UI/API; no product edits | PASS |
| Live browser trash UI | Owner archive `#trashCard` list → restore → purge (`uiConfirm`); staff trash hidden | PASS |
| Regression S1-P1-12/13 | pytest 5 passed (`test_b1_lifecycle_reverse_trash` trash + conclude) | PASS |
| Regression S3-07 | pytest 3 passed (`test_f1a_flows` discard/flag dialogs) | PASS |
| Regression S4-12 (trash destructive + shared confirm) | pytest 2 passed (`test_f1b_archive_finance` trash list/restore/purge + staff hidden); purge uses `uiConfirm` | PASS |
| Negative | source_files empty honored; 15010 free; no git push; Tenant B trash unchanged after A purge | PASS |

## Evidence
- `uat016-dev/evidence/trash-live-probe.json` (verdict PASS; 13 steps)
- `uat016-dev/evidence/trash-browser-probe.json` (verdict PASS; 4 screenshots)
- `uat016-dev/evidence/spec-keep.json` (DEFAULT_ORIGINAL + contract scan)
- `uat016-dev/ui/01-trash-list.png` … `04-staff-no-trash.png`
- `uat016-dev/db/uat016.db` (sqlite backup after first isolate wave)
- `logs/trash-live-probe.txt`, `logs/trash-browser-probe.txt`, `logs/spec-keep.txt`
- `logs/pytest-s1.txt` / `.xml`, `logs/pytest-s3.txt` / `.xml`, `logs/pytest-f1b-trash.txt` / `.xml`
- `logs/uvicorn.log`, `logs/health.json`, `environment/cleanup.json`
- Scripts: `uat016-shared/run_phase.sh`, `probe_trash_live.py`, `probe_trash_browser.py`, `check_spec_keep.py`

## Live owner recycle (API, redacted)
- Staff discard → soft-delete into trash (msg: 放进了「回收站」).
- Owner `GET /api/trash/receipts` tenant-scoped (peer tenant ids absent).
- Staff trash list **403**.
- Cross-tenant restore/purge **404**.
- Owner restore → row leaves trash; owner purge → hard delete from trash.
- Tenant B trash still holds its discarded receipt after A purge.
- `/api/admin/trash` **404** (not re-invented).
- Approved receipt discard **409** `CANNOT_DISCARD_APPROVED` (hard-delete boundary stays: use reverse, not discard/purge of approved ledger).

## Live browser (owner UI)
- `#trashCard` on archive tab; open details → table with restore/purge.
- Restore removes row; purge confirms via in-app `uiConfirm` then removes row.
- Staff: trash card not visible (`owner-only`).

## Notes
- Residual PARTIAL was "D0 SPEC_KEEP 门禁已闭环，保留未运行标注" — missing runtime was owner recycle/restore/purge regression (S1-P1-12/13, S3-07, S4-12). No model needed.
- First browser attempt failed because seed used custom tenant `uat016-dev` while UI uses default tenant; re-probe on default tenant PASS (recorded in same evidence path).
- Cleanup: uvicorn 15469 stopped; f1b 15479 stopped; rag/uploads removed; 15010 FREE_RESERVED; no git push.
- Checklist set to `[TODO] DEV_DELIVERED` only (not DONE). Next gate: independent QA.
