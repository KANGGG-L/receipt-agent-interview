# DEP-MODEL Development / Gate Report

> Generated: 2026-10-17 Asia/Shanghai (CST)
> Role: Development / DEPENDENCY_GATE first evidenced run
> Card: [DEP-MODEL] 9项正常模型后续BLOCKED及超时运行时失败 — P1 DEPENDENCY_GATE / G2
> Constraints: no git push; keep port 15010 reserved; never print API key values; no mock PASS
> Pattern: DEPENDENCY_GATE single evidenced run; leave independent QA re-verify of the 9

## Overall

| Criterion | Verdict |
|---|---|
| 1. Discover 9 scope_ids + how to run | **PASS with limitation** — original UAG catalog not in cloud clone (local harness `~/harness-tmp/receipt-repair-plan-20261015`); inferred 9 scopes from ENG-02/ENG-03 PARTIAL deferred model-path AC + recognition contract |
| 2. Real model path for all 9; job done; read original/assertions | **PASS** — 9/9 PASS; sample_711 original assertions matched |
| 3. Evidence: request, terminal state, screenshots, manual field/save, DB | **PASS** — paths under `docs/handoff/artifacts/dep-model-dev/` |
| 4. If dependency unrestored stay BLOCKED with attempts | **PASS** — keys present; smoke + live jobs succeeded; no mock substitute |
| Negative: keep 15010 free; no skip-mask; no mock as model E2E | **PASS** |

**DEP-MODEL DEV gate: DEV_DELIVERED (9/9 real-job PASS).** Independent QA re-verify of the 9 required before DONE PASS. Next ledger: **DEP-HIDDEN** only after QA/UAT green (or coordinator accepts DEV_DELIVERED → QA). **UAT-032 still blocked on UAT-031** — do not invent UAT-031.

## Catalog discovery

| Source searched | Result |
|---|---|
| `docs/handoff/01-acceptance-checklist.md` DEP-MODEL AC | Names "9 scope_id" + "原三组合" + "original/assertions"; does not enumerate IDs |
| ENG-02 / ENG-03 PARTIAL artifacts on cloud | **Absent** (local Orca `harness-tmp` paths only) |
| `demo/samples/20161001_711_thermal_receipt.json` | Used as `original/assertions` for DM-09 |
| Credentials | `/home/box/.config/receipt-agent/openai_rec.env` + `model.env` (sourced; values never logged) |

### Inferred 9 scope_ids (operational)

| scope_id | Source AC | What was verified |
|---|---|---|
| DM-01-normal-auto-recognize | ENG-02 AC1 | Real upload → recognition job (no mock route) |
| DM-02-job-terminal-done | ENG-02 AC2 / DEP-MODEL | Job terminal `done` |
| DM-03-prefill-header-fields | ENG-02 AC2 | supplier / date / total from model |
| DM-04-prefill-line-items | ENG-02 AC2 | >=1 line item |
| DM-05-second-image-heic-done | assets HEIC→JPEG | Second real image job `done` |
| DM-06-ai-prefill-badge-contract | ENG-03 AC3 | Job done with non-manual doc_form (UI badge on triples) |
| DM-07-manual-entry-save | ENG-03 AC2 | Manual `save_edited` + DB readback |
| DM-08-ai-then-human-save | ENG-02/03 | AI job then human `save_edited` + DB |
| DM-09-original-assertions-711 | DEP-MODEL AC | Live vs `original/assertions_711.json` |

### 原三组合 (viewport × role)

| Triple | UI status | Evidence |
|---|---|---|
| desk-staff (1440×staff) | PASS | screens/desk-staff-*.png; prefill 7-Eleven; AI badge; manual badge hidden; save |
| mob-staff (390×staff) | PASS | screens/mob-staff-*.png; same |
| desk-owner (1440×owner) | PASS | API job done + owner session screens; prefill fields set from live receipt |

## Per-scope table

| scope_id | Status | Attempts | Evidence |
|---|---|---|---|
| DM-01-normal-auto-recognize | PASS | 1 | `evidence/scopes/DM-01-*.json` |
| DM-02-job-terminal-done | PASS | 1 | `evidence/scopes/DM-02-*.json` |
| DM-03-prefill-header-fields | PASS | 1 | `evidence/scopes/DM-03-*.json` |
| DM-04-prefill-line-items | PASS | 1 | `evidence/scopes/DM-04-*.json` |
| DM-05-second-image-heic-done | PASS | 1 | `evidence/scopes/DM-05-*.json` |
| DM-06-ai-prefill-badge-contract | PASS | 1 | `evidence/scopes/DM-06-*.json` + UI screens |
| DM-07-manual-entry-save | PASS | 1 (after db column fix retry) | `evidence/scopes/DM-07-*.json`; DB Vendor_A |
| DM-08-ai-then-human-save | PASS | 1 (after db column fix; prior job attempt 2 for timeout then done) | `evidence/scopes/DM-08-*.json` |
| DM-09-original-assertions-711 | PASS | 1 | supplier/date/total match `original/assertions_711.json` |

**Counts: PASS 9 / FAIL 0 / BLOCKED 0** (`evidence/summary.json`)

### Timeout note (recorded, not mocked)

DM-08 first recognition attempt hit runtime timeout (~48s, humanized error) then attempt 2 reached `done` (~42s). Smoke earlier succeeded in ~6.8s. Attempts recorded in job JSON.

### original/assertions (DM-09)

- Original: `original/assertions_711.json` ← `demo/samples/20161001_711_thermal_receipt.json`
- Live: supplier 7-ELEVEN / 7-Eleven, date 2016-10-01, total 13.0 — **matched**

## Environment

- Port: **15391** (isolated); **15010 left FREE_RESERVED**
- Python: eng05-uat venv symlink; model via sourced env files (hydrate auto)
- Smoke: `logs/smoke1.txt` exit 0 (redacted)
- DB: `environment/dep_model.db`; snapshot `db/receipts-snapshot.json`
- UI: `evidence/ui-triples.json`; screens under `screens/`

## Commands (authoritative)

```text
set -a; source /home/box/.config/receipt-agent/openai_rec.env
source /home/box/.config/receipt-agent/model.env; set +a
# smoke
AUTH_ENABLED=0 RAG_EMBEDDING_PROVIDER=hash DB_PATH=.../smoke1.db \
  python -m app.main --smoke .../sample_711.jpg
# isolated uvicorn :15391
# scopes runner
python docs/handoff/artifacts/dep-model-dev/scripts/run_dep_model_scopes.py
# UI triples via Playwright against :15391
```

## Cleanup

See `environment/cleanup.json`. Uvicorn 15391 stopped; 15010 free.

## Checklist / Ledger

- Checklist: set DEP-MODEL → `[TODO] DEV_DELIVERED` (not DONE PASS — QA re-verify of 9 required)
- Ledger next: independent QA re-verify DEP-MODEL → then DEP-HIDDEN
- UAT-032 still blocked on UAT-031 — do not invent UAT-031

## QA re-verify needed?

**YES.** Independent QA must re-run the 9 scopes against a fresh isolate (new port/DB), confirm catalog inference or recover original UAG scope_ids if available, and re-check original/assertions + triples without reusing this stage's DB/fixture conclusions.
