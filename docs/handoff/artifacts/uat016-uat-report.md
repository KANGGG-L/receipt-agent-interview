# UAT-016 UAT Report

Generated: 2026-10-17 19:16 CST. Card: [UAT-016] 当前UAT Spec硬删边界与原计划owner回收策略不一致 (P2 SPEC_CONFLICT / SPEC_DECISION). Phase: UAT (independent isolate; no product code changed).
Isolate: port **15509** (≠ DEV 15469, ≠ QA 15489), F1B regression port **15519** (≠ QA 15499), fresh DB `uat016-uat/environment/uat016.db` (+ backup `db/uat016.db`), AUTH_ENABLED=0, RAG hash. 15010 untouched (FREE_RESERVED). No model keys required.

## SPEC_DECISION: DEFAULT_ORIGINAL (SPEC_KEEP) — reconfirmed

| Field | Value |
|---|---|
| Decision | **DEFAULT_ORIGINAL** |
| Card contract | `docs/handoff/01-acceptance-checklist.md` §[UAT-016] — D0 SPEC_KEEP; source_files=`[]`; keep existing owner recycle/restore/purge; do not invent admin-only trash auth |
| Original canonical | **S1-P1-12** `docs/review-2026-10/S1-backend.md:76` — trash open to **owner** with UI, tenant-filtered; flagged discard owner-only. **api-conventions** `docs/api-conventions.md:88` — `/api/admin/trash*` removed; replaced by `/api/trash/receipts*`. Related: **S1-P1-13** experiment conclude `require_admin`; **S3-07** in-app discard confirm; **S4-12** shared dialog path (`uiConfirm` in finance purge). |
| Derived Spec rejected | Derived UAT Spec that would treat hard-delete as admin-only / invent admin trash auth — **not approved**. Optional branch without approval must not claim new admin-trash features done. |
| Owner | D0 SPEC_KEEP wave / handoff checklist owner (`docs/handoff/01-acceptance-checklist.md` §2.1 UAT-016). |
| Product change this phase | None. |

## Verdict: UAT PASS

| AC | Method | Result |
|---|---|---|
| 1 SPEC_DECISION recorded | DEFAULT_ORIGINAL + citations above / `evidence/spec-keep.json` (matches DEV/QA) | PASS |
| 2 Keep owner recycle/restore/purge; no invented admin-only auth | Live API isolate 15509; staff list 403; owner list/restore/purge; cross-tenant 404; old `/api/admin/trash` 404; approved discard 409 `CANNOT_DISCARD_APPROVED` | PASS |
| 3 Optional branch not claimed | No new admin trash UI/API; contract scan `admin_trash_hits=[]`; `admin_trash_router_absent=true` | PASS |
| Live browser trash UI | Owner archive `#trashCard` list → restore → purge; staff trash hidden | PASS |
| Tenant B unchanged | After A purge, B trash still holds discarded receipt id 2 | PASS |
| Regression S1-P1-12/13 | pytest 5 passed (`test_b1_lifecycle_reverse_trash` trash + conclude) | PASS |
| Regression S3-07 | pytest 3 passed (`test_f1a_flows` discard/flag dialogs) | PASS |
| Regression S4-12 (trash + shared confirm) | pytest 2 passed (`test_f1b_archive_finance` trash list/restore/purge + staff hidden) | PASS |
| Negative | source_files empty honored; 15010 free; no git push; distinct ports from DEV/QA | PASS |

## Evidence
- `uat016-uat/evidence/trash-live-probe.json` (verdict PASS; 13 steps)
- `uat016-uat/evidence/trash-browser-probe.json` (verdict PASS; 4 screenshots)
- `uat016-uat/evidence/spec-keep.json` (DEFAULT_ORIGINAL + contract scan)
- `uat016-uat/ui/01-trash-list.png` … `04-staff-no-trash.png`
- `uat016-uat/db/uat016.db` (sqlite backup after isolate wave)
- `logs/trash-live-probe.txt`, `logs/trash-browser-probe.txt`, `logs/spec-keep.txt`
- `logs/pytest-s1.txt` / `.xml`, `logs/pytest-s3.txt` / `.xml`, `logs/pytest-f1b-trash.txt` / `.xml`
- `logs/uvicorn.log`, `logs/health.json`, `environment/cleanup.json`
- Scripts: `uat016-shared/run_uat_phase.sh`, `probe_trash_live.py`, `probe_trash_browser.py`, `check_spec_keep.py`
- Prior: `uat016-dev-report.md`, `uat016-qa-report.md`

## Live owner recycle (API, redacted)
- Staff discard → soft-delete into trash (msg: 放进了「回收站」).
- Owner `GET /api/trash/receipts` tenant-scoped (peer tenant ids absent; ids=[1]).
- Staff trash list **403**.
- Cross-tenant restore/purge **404**.
- Owner restore → row leaves trash; owner purge → hard delete from trash.
- Tenant B trash still holds its discarded receipt id **2** after A purge.
- `/api/admin/trash` **404** (not re-invented).
- Approved receipt discard **409** `CANNOT_DISCARD_APPROVED`.

## Live browser (owner UI)
- `#trashCard` on archive tab; open details → table with restore/purge.
- Restore removes row; purge confirms via in-app `uiConfirm` then removes row.
- Staff: trash card not visible (`owner-only`).

## Notes
- Independent UAT isolate (port 15509 ≠ DEV 15469 ≠ QA 15489; F1B 15519 ≠ QA 15499); product code unchanged this phase.
- `spec-keep.json` notes `demo/app/api_receipts.py` dirty vs HEAD from other in-progress cards; lifecycle/finance trash paths for this card were clean; this UAT made **zero** product file edits.
- Cleanup: uvicorn 15509 stopped; f1b 15519 stopped; rag/uploads removed; 15010 FREE_RESERVED; no git push.
- Checklist set to `[DONE] PASS` (clears prior PARTIAL). Next card per `partials-triage.md`: **ENG-01**.
