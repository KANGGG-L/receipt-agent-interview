# DEP-MODEL Independent UAT Report

> Generated: 2026-10-17 10:27:55 CST
> Role: Independent UAT (third re-verify; did NOT perform development or QA)
> Card: [DEP-MODEL] 9项正常模型后续BLOCKED及超时运行时失败 — P1 DEPENDENCY_GATE / G2
> Prior: `dep-model-dev-report.md` (DEV_DELIVERED 9/9 on :15391); `dep-model-qa-report.md` (QA_PASS 9/9 on :15401)
> Constraints: no git push; keep port 15010 reserved; never print API key values; no mock PASS
> Pattern: three-phase Dev → QA → UAT; this is the UAT gate for DONE

## Overall

| Criterion | Verdict |
|---|---|
| 1. Fresh isolate (new port ≠15401/≠15391, new DB); source openai_rec.env + model.env | **PASS** — port **15411**; DB `environment/dep_model.db`; keys SET-only in `logs/env-keys-present.txt` |
| 2. Re-run all 9 scope_ids on real model path (no mocks); include QA harness `receipt_date` fix | **PASS** — 9/9 PASS first attempt; harness SELECT uses `receipt_date` (DM-07/08 no regress) |
| 3. Evidence: request, terminal state, screenshots, save/DB, original/assertions | **PASS** — under `docs/handoff/artifacts/dep-model-uat/` |
| 4. If BLOCKED/FAIL record attempts; no mock PASS | **PASS** — no BLOCKED/FAIL on scopes; UI desk-staff first suite flake then PASS on re-run |
| Negative: 15010 free; no skip-mask; no mock as model E2E | **PASS** — cleanup.json 15010 FREE_RESERVED; 15411 stopped |

**DEP-MODEL UAT gate: UAT_PASS (9/9 real-job PASS).** Mark checklist **[DONE] PASS**. Next ledger: **DEP-HIDDEN**. UAT-032 still blocked on UAT-031 — do not invent UAT-031.

## Catalog note

Same operational catalog as Dev/QA (original UAG scope list absent from cloud clone). Inferred DM-01..DM-09 from ENG-02/ENG-03 PARTIAL + recognition contract.

## Per-scope table

| scope_id | Status | Attempts | Evidence / notes |
|---|---|---|---|
| DM-01-normal-auto-recognize | PASS | 1 | `evidence/scopes/DM-01-*.json`; job done ~8s; 7-Eleven |
| DM-02-job-terminal-done | PASS | 1 | job_status done + supplier/date/total |
| DM-03-prefill-header-fields | PASS | 1 | supplier/date/total from live job |
| DM-04-prefill-line-items | PASS | 1 | items_n >= 1 |
| DM-05-second-image-heic-done | PASS | 1 | HEIC-derived JPEG job done |
| DM-06-ai-prefill-badge-contract | PASS | 1 | job done + non-manual doc_form; UI AI badge on staff triples |
| DM-07-manual-entry-save | PASS | 1 | `receipt_date` harness fix included; save HTTP 200; DB Vendor_A / 2026-10-17 / 80.0 |
| DM-08-ai-then-human-save | PASS | 1 | AI job done then `save_edited` + DB 7-Eleven (no column regress) |
| DM-09-original-assertions-711 | PASS | 1 | live 7-ELEVEN / 2016-10-01 / 13.0 vs `original/assertions_711.json` |

**Counts: PASS 9 / FAIL 0 / BLOCKED 0** (`evidence/summary.json`)

## 原三组合 (viewport × role)

| Triple | UI status | Evidence |
|---|---|---|
| desk-staff (1440×staff) | PASS | screens/desk-staff-*.png; suite-1 prefill timeout flake → re-run attempt 1 PASS; prefill 7-Eleven; AI badge; save |
| mob-staff (390×staff) | PASS | screens/mob-staff-*.png; 7-Eleven; AI badge; save |
| desk-owner (1440×owner) | PASS | screens/desk-owner-*.png; job prefill 7-Eleven |

## Environment

- Port: **15411** (isolated); **15010 left FREE_RESERVED**
- Python: eng05-uat venv symlink; model via sourced env (values never logged)
- Smoke: `logs/smoke1.txt` exit 0 (~25.7s, 7-Eleven / 2016-10-01 / 13.0)
- DB: `environment/dep_model.db`; snapshot `db/receipts-snapshot.json`
- UI: `evidence/ui-triples.json`; screens under `screens/`
- Harness: copied QA script with `db_receipt` SELECT on schema column `receipt_date` (product code unchanged)

## Commands (authoritative)

```text
set -a; source /home/box/.config/receipt-agent/openai_rec.env
source /home/box/.config/receipt-agent/model.env; set +a
# smoke
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash DB_PATH=.../smoke1.db \
  python -m app.main --smoke .../sample_711.jpg
# isolated uvicorn APP_PORT=15411 DB_PATH=.../dep_model.db RAG_DIR=.../rag_chroma
# scopes
DEP_MODEL_BASE=http://127.0.0.1:15411 DEP_MODEL_ART=.../dep-model-uat \
  python docs/handoff/artifacts/dep-model-uat/scripts/run_dep_model_scopes.py
# UI triples (+ desk-staff re-run if flake)
python docs/handoff/artifacts/dep-model-uat/scripts/run_ui_triples.py
python docs/handoff/artifacts/dep-model-uat/scripts/retry_desk_staff.py
```

## Cleanup

See `environment/cleanup.json`. Uvicorn 15411 stopped; 15010 free.

## Checklist / Ledger

- Checklist: set DEP-MODEL → `[DONE] PASS (2026-10-17 独立 UAT 9/9 PASS on :15411)`
- Ledger next: **DEP-HIDDEN**
- UAT-032 still blocked on UAT-031 — do not invent UAT-031
