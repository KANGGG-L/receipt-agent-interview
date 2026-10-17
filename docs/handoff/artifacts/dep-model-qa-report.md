# DEP-MODEL Independent QA Report

> Generated: 2026-10-17 10:18 Asia/Shanghai (CST)
> Role: Independent QA (did NOT perform development)
> Card: [DEP-MODEL] 9项正常模型后续BLOCKED及超时运行时失败 — P1 DEPENDENCY_GATE / G2
> Prior: `dep-model-dev-report.md` (DEV_DELIVERED; 9/9 real-job PASS on :15391)
> Constraints: no git push; keep port 15010 reserved; never print API key values; no mock PASS
> Pattern: independent re-verify of 9 scopes on fresh isolate; next = independent UAT third re-verify before DONE

## Overall

| Criterion | Verdict |
|---|---|
| 1. Fresh isolate (new port ≠15391, new DB); source openai_rec.env + model.env | **PASS** — port **15401**; DB `environment/dep_model.db`; keys present SET-only in `logs/env-keys-present.txt` |
| 2. Re-run all 9 scope_ids on real model path (no mocks) | **PASS** — 9/9 PASS; smoke exit 0 (~32s, 7-ELEVEN / 2016-10-01 / 13.0) |
| 3. Evidence: request, terminal state, screenshots, save/DB, original/assertions | **PASS** — under `docs/handoff/artifacts/dep-model-qa/` |
| 4. If BLOCKED/FAIL record attempts; no mock PASS | **PASS** — DM-07/08 attempt 1 harness column miss → attempt 2 PASS after `receipt_date` harness fix (not product mock) |
| Negative: 15010 free; no skip-mask; no mock as model E2E | **PASS** — cleanup.json 15010 FREE_RESERVED; 15401 stopped |

**DEP-MODEL QA gate: QA_PASS (9/9 real-job PASS).** Do **not** mark DONE yet. Next: **independent UAT** third re-verify of the 9 on another fresh isolate (three-phase: Dev → QA → UAT), then DEP-HIDDEN. UAT-032 still blocked on UAT-031.

## Catalog note

Same operational catalog as Dev (original UAG scope list absent from cloud clone). Inferred DM-01..DM-09 from ENG-02/ENG-03 PARTIAL + recognition contract. Independent UAT should reconfirm or recover original UAG IDs if available.

## Per-scope table

| scope_id | Status | Attempts | Evidence / notes |
|---|---|---|---|
| DM-01-normal-auto-recognize | PASS | 1 | `evidence/scopes/DM-01-*.json` |
| DM-02-job-terminal-done | PASS | 1 | `evidence/scopes/DM-02-*.json` |
| DM-03-prefill-header-fields | PASS | 1 | supplier/date/total from live job |
| DM-04-prefill-line-items | PASS | 1 | items_n >= 1 |
| DM-05-second-image-heic-done | PASS | 1 | HEIC-derived JPEG job done |
| DM-06-ai-prefill-badge-contract | PASS | 1 | job done + non-manual doc_form; UI AI badge on triples |
| DM-07-manual-entry-save | PASS | 2 | attempt 1 BLOCKED (`OperationalError: no such column: date`); attempt 2 PASS after harness `receipt_date` SELECT fix; DB Vendor_A |
| DM-08-ai-then-human-save | PASS | 2 | same harness fix; AI job done then `save_edited` + DB |
| DM-09-original-assertions-711 | PASS | 1 | live 7-ELEVEN / 2016-10-01 / 13.0 vs `original/assertions_711.json` |

**Counts: PASS 9 / FAIL 0 / BLOCKED 0** (`evidence/summary.json`)

## 原三组合 (viewport × role)

| Triple | UI status | Evidence |
|---|---|---|
| desk-staff (1440×staff) | PASS | screens/desk-staff-*.png; attempts=4 (screenshot/prefill flake then PASS); prefill 7-Eleven; AI badge; manual badge path |
| mob-staff (390×staff) | PASS | screens/mob-staff-*.png; 7-ELEVEN; AI badge; save |
| desk-owner (1440×owner) | PASS | screens/desk-owner-*.png; job done + prefill 7-ELEVEN |

## Environment

- Port: **15401** (isolated); **15010 left FREE_RESERVED**
- Python: eng05-uat venv symlink; model via sourced env (values never logged)
- Smoke: `logs/smoke1.txt` exit 0 (redacted)
- DB: `environment/dep_model.db`; snapshot `db/receipts-snapshot.json`
- UI: `evidence/ui-triples.json`; screens under `screens/`
- Harness note: QA script patched `db_receipt` to use schema column `receipt_date` (Dev reported same fix; product code unchanged)

## Commands (authoritative)

```text
set -a; source /home/box/.config/receipt-agent/openai_rec.env
source /home/box/.config/receipt-agent/model.env; set +a
# smoke
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash DB_PATH=.../smoke1.db \
  python -m app.main --smoke .../sample_711.jpg
# isolated uvicorn APP_PORT=15401 DB_PATH=.../dep_model.db RAG_DIR=.../rag_chroma
# scopes
DEP_MODEL_BASE=http://127.0.0.1:15401 DEP_MODEL_ART=.../dep-model-qa \
  python docs/handoff/artifacts/dep-model-qa/scripts/run_dep_model_scopes.py
# UI triples
python docs/handoff/artifacts/dep-model-qa/scripts/run_ui_triples.py
```

## Cleanup

See `environment/cleanup.json`. Uvicorn 15401 stopped; 15010 free.

## Checklist / Ledger

- Checklist: set DEP-MODEL → `[TODO] QA_PASS` (not DONE — UAT re-verify of 9 required)
- Ledger next: independent UAT re-verify DEP-MODEL → then DEP-HIDDEN
- UAT-032 still blocked on UAT-031 — do not invent UAT-031

## UAT re-verify needed?

**YES.** Independent UAT must re-run the 9 scopes (+ triples / original assertions) against a fresh isolate (new port/DB), without reusing this QA stage DB/fixture conclusions. Prefer three-phase (Dev → QA → UAT) over single-gate DONE after QA alone.
